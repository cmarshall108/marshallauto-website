"""Public vehicle listing imports and durable source refreshes."""
import json
import re
import time
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import requests
from flask import current_app
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.datastructures import FileStorage


class MarketplaceError(ValueError):
    pass


def listing_url(value):
    parts = urlsplit((value or '').strip())
    if parts.hostname == 'craigslist.org' or (parts.hostname or '').endswith('.craigslist.org'):
        from app.craigslist_import import craigslist_url
        return craigslist_url(value)
    if (parts.scheme != 'https' or parts.hostname not in
            {'facebook.com', 'www.facebook.com', 'm.facebook.com', 'web.facebook.com'}
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise MarketplaceError('Enter a full https://www.facebook.com/marketplace/item/ listing URL.')
    match = re.fullmatch(r'/marketplace/item/([0-9]{1,30})/?', parts.path)
    if not match:
        raise MarketplaceError('Use the Marketplace item URL, not a share or search link.')
    return f'https://www.facebook.com/marketplace/item/{match.group(1)}/'


def photo_url(value):
    parts = urlsplit(value)
    host = parts.hostname or ''
    if (parts.scheme != 'https' or not (host == 'images.craigslist.org' or host.endswith(('.fbcdn.net', '.fbsbx.com')))
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise MarketplaceError('The listing contains an unsupported photo URL.')
    return value


def download(url, *, photo=False, deadline=None):
    url = photo_url(url) if photo else listing_url(url)
    limit = 15 * 1024 * 1024 if photo else 8 * 1024 * 1024
    deadline = deadline or time.monotonic() + 25
    if time.monotonic() >= deadline:
        raise MarketplaceError('Marketplace download timed out. Please retry.')
    craigslist = (urlsplit(url).hostname or '').endswith('.craigslist.org') or urlsplit(url).hostname == 'craigslist.org'
    try:
        for redirect_count in range(4):
            with requests.get(url, timeout=(5, 10), stream=True, allow_redirects=False,
                              headers={'User-Agent': 'MarshallAuto-ListingImport/1.0',
                                       'Accept-Language': 'en-US,en;q=0.9'}) as response:
                if craigslist and not photo and response.status_code in (301, 302, 303, 307, 308):
                    from app.craigslist_import import craigslist_url
                    if redirect_count == 3 or not response.headers.get('Location') or time.monotonic() >= deadline:
                        raise MarketplaceError('Craigslist listing redirects could not be resolved.')
                    url = craigslist_url(urljoin(url, response.headers['Location']))
                    continue
                if response.status_code != 200:
                    raise MarketplaceError('The listing or photo is unavailable, expired, or blocked. Saved data was retained.')
                content_type = response.headers.get('Content-Type', '').lower()
                if photo and not content_type.startswith('image/'):
                    raise MarketplaceError('The listing returned an invalid photo.')
                chunks = bytearray()
                for chunk in response.iter_content(65536):
                    chunks.extend(chunk)
                    if len(chunks) > limit or time.monotonic() > deadline:
                        raise MarketplaceError('Listing download exceeded its size or time limit.')
                return bytes(chunks)
    except requests.RequestException as exc:
        raise MarketplaceError('Could not reach the listing website. Saved vehicle data has not been changed.') from exc


class _Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('type') in ('application/json', 'application/ld+json'):
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            self.scripts.append(''.join(self.current))
            self.current = None


def _objects(value):
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            yield item
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)


def _text(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get('text') or value.get('name'))
    return ''


def parse_listing(html, url):
    canonical = listing_url(url)
    listing_id = canonical.rstrip('/').rsplit('/', 1)[1]
    parser = _Scripts()
    parser.feed(html)
    candidates = []
    for script in parser.scripts:
        try:
            data = json.loads(script)
        except (ValueError, RecursionError):
            continue
        for item in _objects(data):
            if str(item.get('id')) == listing_id and 'marketplace_listing_title' in item:
                candidates.append(item)
    if not candidates:
        raise MarketplaceError('Full listing data is unavailable. Facebook may require login, or the listing was removed. Enter the vehicle manually.')
    item = max(candidates, key=lambda value: len(value))
    fields = {}
    mapping = {
        'vehicle_make_display_name': ('make', 64),
        'vehicle_model_display_name': ('model', 64),
        'vehicle_trim_display_name': ('trim', 128),
        'vehicle_exterior_color': ('exterior_color', 64),
        'vehicle_interior_color': ('interior_color', 64),
        'vehicle_transmission': ('transmission', 128),
        'vehicle_fuel_type': ('fuel_type', 32),
        'vehicle_body_style': ('body_style', 64),
        'vehicle_engine': ('engine', 128),
        'seller_description': ('description', 20000),
    }
    for source, (target, limit) in mapping.items():
        value = _text(item.get(source))
        if value:
            fields[target] = value[:limit]
    title = _text(item.get('marketplace_listing_title'))
    title_match = re.fullmatch(
        r'(19\d{2}|20\d{2}|2100)\s+(Land Rover|Alfa Romeo|Aston Martin|AM General|\S+)\s+(.+)',
        title, re.IGNORECASE,
    )
    if title_match:
        fields['year'] = int(title_match.group(1))
        fields.setdefault('make', title_match.group(2)[:64])
        fields.setdefault('model', title_match.group(3)[:64])
    year = item.get('vehicle_year')
    if str(year).isdigit() and 1900 <= int(year) <= 2100:
        fields['year'] = int(year)
    price = item.get('listing_price') or {}
    if not isinstance(price, dict) or price.get('currency') != 'USD':
        raise MarketplaceError('A USD listing price is required; currency conversion is not supported.')
    try:
        amount = Decimal(str(price.get('amount')))
        if not amount.is_finite() or not 0 <= amount <= Decimal('99999999.99'):
            raise InvalidOperation
        fields['price'] = str(amount.quantize(Decimal('0.01')))
    except InvalidOperation as exc:
        raise MarketplaceError('The listing price is missing or invalid.') from exc
    odometer = item.get('vehicle_odometer_data') or {}
    if isinstance(odometer, dict):
        value = str(odometer.get('value', '')).replace(',', '')
        unit = str(odometer.get('unit', '')).lower()
        if value.isdigit() and int(value) <= 10000000:
            if unit in ('miles', 'mile', 'mi'):
                fields['mileage'] = int(value)
            elif unit in ('kilometers', 'kilometres', 'km'):
                fields['mileage'] = round(int(value) / 1.609344)
    vin = _text(item.get('vehicle_identification_number')).upper()
    if re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        fields['vin'] = vin
    drivetrain = _text(item.get('vehicle_drivetrain')).upper()
    if drivetrain in ('FWD', 'RWD', 'AWD', '4WD'):
        fields['drivetrain'] = drivetrain
    title_status = _text(item.get('vehicle_title_status')).lower().removesuffix(' title')
    if title_status in ('clean', 'rebuilt', 'salvage'):
        fields['title_status'] = title_status
    for field in ('mpg_city', 'mpg_highway'):
        value = item.get('vehicle_' + field)
        if str(value).isdigit() and 0 <= int(value) <= 300:
            fields[field] = int(value)
    features = item.get('vehicle_features')
    if isinstance(features, list):
        names = [_text(feature) for feature in features]
        if any(names):
            fields['features'] = ', '.join(dict.fromkeys(name for name in names if name))[:10000]
    if item.get('is_sold') is True:
        fields['status'] = 'sold'
    elif item.get('is_pending') is True:
        fields['status'] = 'pending'
    elif item.get('is_sold') is False and item.get('is_pending') is False:
        fields['status'] = 'available'
    photos = []
    seen = set()
    photo_data = item.get('listing_photos')
    if not isinstance(photo_data, (list, dict)) or not photo_data:
        raise MarketplaceError('The full listing photo collection is unavailable; import was not started.')
    if any(value.get('has_next_page') is True for value in _objects(photo_data)):
        raise MarketplaceError('Facebook exposed only part of the photo collection; import was not started.')
    for image in _objects(photo_data):
        source = image.get('image')
        if not isinstance(source, dict) or not source.get('uri'):
            continue
        uri = photo_url(source['uri'])
        identity = str(image.get('id') or urlsplit(uri).path)
        if identity not in seen:
            seen.add(identity)
            photos.append({'id': identity, 'url': uri})
    if not photos or len(photos) > 40:
        raise MarketplaceError('The listing must expose between 1 and 40 photos.')
    expected_count = item.get('listing_photos_count')
    if isinstance(photo_data, dict):
        expected_count = photo_data.get('total_count', photo_data.get('count', expected_count))
    if str(expected_count).isdigit() and int(expected_count) > len(photos):
        raise MarketplaceError('Some listing photos are unavailable; import was not started.')
    photos.reverse()
    return {'url': canonical, 'fields': fields, 'photos': photos, 'title': title}


def fetch_listing(url):
    canonical = listing_url(url)
    if 'craigslist.org' in (urlsplit(canonical).hostname or ''):
        from app.craigslist_import import parse_craigslist
        return parse_craigslist(download(canonical).decode('utf-8', errors='replace'), canonical)
    return parse_listing(download(url).decode('utf-8', errors='replace'), url)


def preview_token(snapshot, user_id):
    return URLSafeTimedSerializer(current_app.secret_key, salt='marketplace-import').dumps(
        {'snapshot': snapshot, 'user_id': str(user_id)})


def read_preview(token, user_id):
    try:
        value = URLSafeTimedSerializer(current_app.secret_key, salt='marketplace-import').loads(
            token, max_age=7200)
        if value['user_id'] != str(user_id):
            raise BadData('Wrong user')
        return value['snapshot']
    except (BadData, KeyError, TypeError) as exc:
        raise MarketplaceError('This import preview expired or is invalid. Import the listing again.') from exc


def photo_token(url, user_id):
    return URLSafeTimedSerializer(current_app.secret_key, salt='marketplace-photo').dumps(
        {'url': photo_url(url), 'user_id': str(user_id)})


def photo_thumbnail(token, user_id):
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        value = URLSafeTimedSerializer(current_app.secret_key, salt='marketplace-photo').loads(
            token, max_age=7200)
        if value['user_id'] != str(user_id):
            raise BadData('Wrong user')
        raw = download(value['url'], photo=True)
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 20000000:
                raise MarketplaceError('Listing photo is too large to preview.')
            image = ImageOps.exif_transpose(image)
            image.thumbnail((336, 252))
            output = BytesIO()
            image.convert('RGB').save(output, format='JPEG', quality=80)
            return output.getvalue()
    except (BadData, KeyError, TypeError, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise MarketplaceError('This photo preview is invalid or unavailable.') from exc


def discard_photos(photos):
    from app.admin import _delete_vehicle_image_file
    from app.models import VehicleImage

    for photo in photos:
        _delete_vehicle_image_file(VehicleImage(filename=photo['filename']))


def stage_photos(snapshot, existing=()):
    from app.utils import save_uploaded_image

    previous = {photo['id']: photo for photo in existing}
    staged = []
    created = []
    deadline = time.monotonic() + 90
    try:
        for photo in snapshot['photos']:
            source_path = urlsplit(photo['url']).path
            if (photo['id'] in previous
                    and previous[photo['id']].get('source_path', source_path) == source_path):
                staged.append(previous[photo['id']])
                continue
            raw = download(photo['url'], photo=True, deadline=deadline)
            upload = FileStorage(stream=BytesIO(raw), filename='marketplace.jpg')
            filename, width, height = save_uploaded_image(upload)
            if not filename:
                raise MarketplaceError('A listing photo could not be saved. No imported photos were changed.')
            stored = {'id': photo['id'], 'source_path': source_path,
                      'filename': filename, 'width': width, 'height': height}
            staged.append(stored)
            created.append(stored)
        return staged, created
    except Exception:
        discard_photos(created)
        raise


def apply_photos(vehicle, photos, previous=()):
    from app import db
    from app.models import VehicleImage

    old_names = {photo['filename'] for photo in previous}
    new_names = {photo['filename'] for photo in photos}
    images = {image.filename: image for image in vehicle.images}
    removed = [image for image in vehicle.images
               if image.filename in old_names and image.filename not in new_names]
    for image in removed:
        vehicle.images.remove(image)
        db.session.delete(image)
    manual = [image for image in vehicle.images
              if image.filename not in old_names and image.filename not in new_names]
    for order, photo in enumerate(photos):
        image = images.get(photo['filename'])
        if image is None:
            image = VehicleImage(filename=photo['filename'], width=photo['width'],
                                 height=photo['height'], is_primary=False)
            vehicle.images.append(image)
        image.order_index = order
    for order, image in enumerate(manual, start=len(photos)):
        image.order_index = order
    if vehicle.images and not any(image.is_primary for image in vehicle.images):
        min(vehicle.images, key=lambda image: image.order_index).is_primary = True
    return [{'filename': image.filename} for image in removed]


def attach_import(vehicle, snapshot, photos, enabled=True):
    from app import db
    from app.models import MarketplaceSync, utcnow

    now = utcnow()
    apply_photos(vehicle, photos)
    source = MarketplaceSync(vehicle=vehicle, source_url=snapshot['url'],
                             snapshot=snapshot, photo_files=photos, enabled=enabled,
                             last_checked_at=now, last_success_at=now,
                             next_check_at=now + timedelta(hours=6))
    db.session.add(source)
    return source


def refresh_source(source):
    from app import db
    from app.models import MarketplaceSync, utcnow
    from app.utils import delete_local_video_file

    vehicle_id = source.vehicle_id
    snapshot = fetch_listing(source.source_url)
    current_names = {image.filename for image in source.vehicle.images}
    previous = [photo for photo in source.photo_files if photo['filename'] in current_names]
    photos, created = stage_photos(snapshot, previous)
    old_video = None
    try:
        db.session.expire_all()
        source = db.session.get(MarketplaceSync, vehicle_id)
        if source is None or not source.enabled:
            discard_photos(created)
            return False
        vehicle = source.vehicle
        old_fields = source.snapshot['fields']
        for field, value in snapshot['fields'].items():
            if value != old_fields.get(field):
                setattr(vehicle, field, Decimal(value) if field == 'price' else value)
                if field == 'status':
                    vehicle.sold_at = (vehicle.sold_at or utcnow()) if value == 'sold' else None
                    if value == 'sold':
                        old_video = vehicle.video_url
                        vehicle.video_url = None
        removed = apply_photos(vehicle, photos, source.photo_files)
        source.snapshot = snapshot
        source.photo_files = photos
        source.last_checked_at = utcnow()
        source.last_success_at = source.last_checked_at
        source.next_check_at = source.last_checked_at + timedelta(hours=6)
        source.last_error = None
        db.session.commit()
    except Exception:
        db.session.rollback()
        discard_photos(created)
        raise
    discard_photos(removed)
    if old_video:
        delete_local_video_file(old_video)
    if (current_app.config.get('PHOTO_HIGHLIGHTS_ENABLED', True)
            and current_app.config.get('PHOTO_HIGHLIGHTS_AUTO_ENQUEUE', True)):
        from app.highlight_jobs import enqueue_vehicle_highlight_jobs
        try:
            enqueue_vehicle_highlight_jobs(vehicle_id)
        except Exception:
            db.session.rollback()
            current_app.logger.warning('Marketplace photo highlight enqueue failed for %s', vehicle_id)
    return True


def sync_due(limit=10):
    from app import db
    from app.models import MarketplaceSync, utcnow

    now = utcnow()
    candidates = db.session.query(MarketplaceSync.vehicle_id).filter(
        MarketplaceSync.enabled.is_(True), MarketplaceSync.next_check_at <= now,
    ).order_by(MarketplaceSync.next_check_at).limit(limit).all()
    counts = {'checked': 0, 'updated': 0, 'failed': 0}
    for (vehicle_id,) in candidates:
        claim_time = utcnow()
        claimed = MarketplaceSync.query.filter(
            MarketplaceSync.vehicle_id == vehicle_id,
            MarketplaceSync.enabled.is_(True), MarketplaceSync.next_check_at <= claim_time,
        ).update({'next_check_at': claim_time + timedelta(minutes=15)}, synchronize_session=False)
        db.session.commit()
        if not claimed:
            continue
        counts['checked'] += 1
        try:
            if refresh_source(db.session.get(MarketplaceSync, vehicle_id)):
                counts['updated'] += 1
        except Exception as exc:
            db.session.rollback()
            source = db.session.get(MarketplaceSync, vehicle_id)
            if source:
                source.last_checked_at = utcnow()
                source.next_check_at = source.last_checked_at + timedelta(hours=6)
                source.last_error = (str(exc)[:500] if isinstance(exc, MarketplaceError)
                                     else 'Refresh failed. Saved data was retained; check the server log.')
                db.session.commit()
            current_app.logger.warning('Marketplace refresh failed for vehicle %s (%s)',
                                       vehicle_id, type(exc).__name__)
            counts['failed'] += 1
    return counts