"""Facebook Page publishing helpers for vehicle listings.

Important limitation
--------------------
Meta does **not** provide a public API for ordinary third-party apps to create
Facebook Marketplace vehicle listings. Marketplace listing creation is limited
to Meta partnership programs / approved platforms.

This module therefore:
1. Posts (or updates messaging for) vehicles on a Facebook **Page** via Graph API
   (photos + caption + link to the website listing).
2. Builds a Marketplace-ready title/description/price draft for manual paste
   into Facebook Marketplace create-listing UI.

Required Graph permissions on the Page access token typically include:
  pages_manage_posts, pages_read_engagement, pages_show_list
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from flask import current_app

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = 'v21.0'
GRAPH_BASE = f'https://graph.facebook.com/{GRAPH_API_VERSION}'


@dataclass
class PublishResult:
    ok: bool
    post_id: str | None = None
    error: str | None = None
    skipped: bool = False
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'ok': self.ok,
            'post_id': self.post_id,
            'error': self.error,
            'skipped': self.skipped,
            'detail': self.detail,
        }


def _ssl_context():
    try:
        import certifi  # type: ignore
        import ssl
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on', 'enabled')


def _setting(key: str, default: str = '') -> str:
    """Read SiteSetting with config/env fallback. Safe if DB unavailable."""
    try:
        from app.models import SiteSetting
        val = SiteSetting.get(key)
        if val is not None and str(val).strip() != '':
            return str(val).strip()
    except Exception:
        pass
    cfg_key = key.upper()
    env_val = current_app.config.get(cfg_key) if current_app else None
    if env_val is not None and str(env_val).strip() != '':
        return str(env_val).strip()
    return default


def facebook_publishing_enabled() -> bool:
    """Master switch: settings flag OR env FACEBOOK_AUTO_POST_VEHICLES."""
    flag = _setting('facebook_page_posting_enabled', '')
    if flag != '':
        return _truthy(flag)
    return _truthy(current_app.config.get('FACEBOOK_AUTO_POST_VEHICLES', False))


def facebook_auto_post_on_create() -> bool:
    flag = _setting('facebook_auto_post_on_create', '')
    if flag != '':
        return _truthy(flag)
    return facebook_publishing_enabled()


def facebook_auto_post_on_edit() -> bool:
    flag = _setting('facebook_auto_post_on_edit', '')
    if flag != '':
        return _truthy(flag)
    return False


def get_page_id() -> str:
    return (
        _setting('facebook_page_id', '')
        or str(current_app.config.get('FACEBOOK_PAGE_ID') or '').strip()
    )


def get_page_access_token() -> str:
    """Page token from Admin Settings, falling back to env/config."""
    return (
        _setting('facebook_page_access_token', '')
        or str(current_app.config.get('FACEBOOK_PAGE_ACCESS_TOKEN') or '').strip()
    )


def token_source() -> str:
    """Where the active Page token comes from: settings, env, or empty."""
    try:
        from app.models import SiteSetting
        stored = SiteSetting.get('facebook_page_access_token')
        if stored is not None and str(stored).strip() != '':
            return 'settings'
    except Exception:
        pass
    env_token = str(current_app.config.get('FACEBOOK_PAGE_ACCESS_TOKEN') or '').strip()
    if env_token:
        return 'env'
    return ''


def is_configured() -> bool:
    return bool(get_page_id() and get_page_access_token())


def configuration_status() -> dict[str, Any]:
    page_id = get_page_id()
    token = get_page_access_token()
    source = token_source() if token else ''
    return {
        'enabled': facebook_publishing_enabled(),
        'auto_post_on_create': facebook_auto_post_on_create(),
        'auto_post_on_edit': facebook_auto_post_on_edit(),
        'page_id_set': bool(page_id),
        'page_id': page_id,
        'token_set': bool(token),
        'token_source': source,
        'configured': bool(page_id and token),
        'marketplace_api_available': False,
        'note': (
            'Meta does not allow ordinary apps to auto-create Marketplace listings. '
            'This integration posts to your Facebook Page and builds a Marketplace draft for manual paste.'
        ),
    }


def _format_price(value: Any) -> str:
    if value is None:
        return ''
    try:
        amount = Decimal(str(value))
    except Exception:
        return str(value)
    quantized = amount.quantize(Decimal('1')) if amount == amount.to_integral_value() else amount.quantize(Decimal('0.01'))
    if quantized == quantized.to_integral_value():
        return f"${int(quantized):,}"
    return f"${quantized:,.2f}"


def _format_mileage(value: Any) -> str:
    try:
        return f"{int(value):,} miles"
    except Exception:
        return f"{value} miles" if value is not None else ''


def vehicle_public_url(vehicle) -> str:
    site = (current_app.config.get('SITE_URL') or '').rstrip('/')
    slug = getattr(vehicle, 'slug', None) or getattr(vehicle, 'id', '')
    return f"{site}/inventory/{slug}"


def build_listing_message(vehicle, *, include_link: bool = True, max_len: int = 2000) -> str:
    """Caption used for Facebook Page photo/feed posts."""
    business = current_app.config.get('BUSINESS_NAME') or 'Marshall Auto'
    city = current_app.config.get('BUSINESS_CITY') or ''
    state = current_app.config.get('BUSINESS_STATE') or ''
    phone = current_app.config.get('BUSINESS_PHONE') or ''
    location = ', '.join(p for p in (city, state) if p)

    title = getattr(vehicle, 'title', None) or 'Vehicle'
    price = _format_price(getattr(vehicle, 'display_price', None) or getattr(vehicle, 'price', None))
    miles = _format_mileage(getattr(vehicle, 'mileage', None))
    color = getattr(vehicle, 'exterior_color', None) or ''
    transmission = getattr(vehicle, 'transmission', None) or ''
    drivetrain = getattr(vehicle, 'drivetrain', None) or ''
    fuel = getattr(vehicle, 'fuel_type', None) or ''
    body = getattr(vehicle, 'body_style', None) or ''
    condition = (getattr(vehicle, 'condition', None) or 'used').replace('_', ' ').title()
    title_status = (getattr(vehicle, 'title_status', None) or 'clean').replace('_', ' ').title()
    stock = getattr(vehicle, 'stock_number', None) or ''
    description = (getattr(vehicle, 'description', None) or '').strip()
    features = []
    if hasattr(vehicle, 'feature_list'):
        features = list(vehicle.feature_list or [])[:12]
    elif getattr(vehicle, 'features', None):
        features = [f.strip() for f in str(vehicle.features).split(',') if f.strip()][:12]

    lines = [
        f"🚗 {title} — {price}" if price else f"🚗 {title}",
        f"📍 {location}" if location else '',
        '',
        f"• Mileage: {miles}" if miles else '',
        f"• Condition: {condition}" if condition else '',
        f"• Title: {title_status}" if title_status else '',
        f"• Exterior: {color}" if color else '',
        f"• Body: {body}" if body else '',
        f"• Transmission: {transmission}" if transmission else '',
        f"• Drivetrain: {drivetrain}" if drivetrain else '',
        f"• Fuel: {fuel}" if fuel else '',
        f"• Stock #: {stock}" if stock else '',
    ]
    if features:
        lines.append('')
        lines.append('Highlights: ' + ', '.join(features))
    if description:
        lines.append('')
        # Keep description short for feed readability
        desc = description if len(description) <= 500 else description[:497].rstrip() + '…'
        lines.append(desc)
    lines.append('')
    lines.append(f"Call/text {phone}" if phone else '')
    lines.append(f"Available now at {business}" + (f" in {location}" if location else ''))
    if include_link:
        lines.append(vehicle_public_url(vehicle))
    lines.append('')
    lines.append('#UsedCars #CarForSale' + (f' #{city.replace(" ", "")}' if city else ''))

    message = '\n'.join(line for line in lines if line is not None)
    # Collapse excessive blank lines
    while '\n\n\n' in message:
        message = message.replace('\n\n\n', '\n\n')
    message = message.strip()
    if len(message) > max_len:
        message = message[: max_len - 1].rstrip() + '…'
    return message


def build_marketplace_draft(vehicle) -> dict[str, Any]:
    """Structured draft for manual Facebook Marketplace vehicle listing paste."""
    business = current_app.config.get('BUSINESS_NAME') or 'Marshall Auto'
    city = current_app.config.get('BUSINESS_CITY') or ''
    state = current_app.config.get('BUSINESS_STATE') or ''
    zip_code = current_app.config.get('BUSINESS_ZIP') or ''
    phone = current_app.config.get('BUSINESS_PHONE') or ''
    address = current_app.config.get('BUSINESS_ADDRESS') or ''

    price_raw = getattr(vehicle, 'display_price', None) or getattr(vehicle, 'price', None)
    try:
        price_number = int(Decimal(str(price_raw))) if price_raw is not None else None
    except Exception:
        price_number = None

    title = getattr(vehicle, 'title', None) or 'Vehicle for sale'
    # Marketplace titles are short
    marketplace_title = title if len(title) <= 100 else title[:97] + '…'

    message = build_listing_message(vehicle, include_link=True, max_len=5000)
    # Marketplace description often works better without hashtags clutter
    description_lines = [
        message.split('#UsedCars')[0].strip(),
        '',
        f'View full details and more photos: {vehicle_public_url(vehicle)}',
        f'Contact {business}' + (f' at {phone}' if phone else '') + '.',
    ]
    description = '\n'.join(description_lines).strip()

    image_urls = []
    try:
        for img in (vehicle.ordered_images() if hasattr(vehicle, 'ordered_images') else list(getattr(vehicle, 'images', None) or []))[:10]:
            url = getattr(img, 'absolute_url', None)
            if url:
                image_urls.append(url)
    except Exception:
        pass
    if not image_urls:
        primary = None
        try:
            primary = vehicle.primary_image_url(absolute=True)
        except Exception:
            primary = None
        if primary:
            image_urls = [primary]

    condition = (getattr(vehicle, 'condition', None) or 'used').lower()
    marketplace_condition = {
        'used': 'Used - Good',
        'certified': 'Used - Like New',
        'rebuilt': 'Used - Fair',
    }.get(condition, 'Used - Good')

    year = getattr(vehicle, 'year', None)
    make = getattr(vehicle, 'make', None) or ''
    model = getattr(vehicle, 'model', None) or ''
    mileage = getattr(vehicle, 'mileage', None)

    copy_block = '\n'.join([
        f'Title: {marketplace_title}',
        f'Price: {_format_price(price_raw)}' if price_raw is not None else 'Price:',
        f'Year: {year}' if year else 'Year:',
        f'Make: {make}',
        f'Model: {model}',
        f'Mileage: {mileage}' if mileage is not None else 'Mileage:',
        f'Condition: {marketplace_condition}',
        f'Location: {city}, {state} {zip_code}'.strip(),
        '',
        'Description:',
        description,
    ])

    return {
        'title': marketplace_title,
        'price': price_number,
        'price_display': _format_price(price_raw),
        'year': year,
        'make': make,
        'model': model,
        'trim': getattr(vehicle, 'trim', None) or '',
        'mileage': mileage,
        'body_style': getattr(vehicle, 'body_style', None) or '',
        'exterior_color': getattr(vehicle, 'exterior_color', None) or '',
        'transmission': getattr(vehicle, 'transmission', None) or '',
        'fuel_type': getattr(vehicle, 'fuel_type', None) or '',
        'drivetrain': getattr(vehicle, 'drivetrain', None) or '',
        'vin': getattr(vehicle, 'vin', None) or '',
        'condition': marketplace_condition,
        'description': description,
        'location': {
            'address': address,
            'city': city,
            'state': state,
            'zip': zip_code,
        },
        'phone': phone,
        'vehicle_url': vehicle_public_url(vehicle),
        'image_urls': image_urls,
        'copy_block': copy_block,
        'marketplace_create_url': 'https://www.facebook.com/marketplace/create/vehicle',
        'api_note': (
            'Facebook Marketplace vehicle listings cannot be created automatically '
            'via the public Graph API. Copy this draft into Marketplace manually.'
        ),
    }


def _http_json(
    method: str,
    url: str,
    *,
    data: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """JSON Graph call with certifi SSL + curl fallback."""
    body = None
    headers = {'Accept': 'application/json', 'User-Agent': 'MarshallAutoWebsite/1.0'}
    if data is not None:
        body = urllib.parse.urlencode(data).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded'

    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    ctx = _ssl_context()
    open_kwargs: dict[str, Any] = {'timeout': timeout}
    if ctx is not None:
        open_kwargs['context'] = ctx

    try:
        with urllib.request.urlopen(req, **open_kwargs) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf-8', errors='replace')[:800]
        try:
            payload = json.loads(err_body)
            msg = payload.get('error', {}).get('message') or err_body
        except Exception:
            msg = err_body or str(exc)
        raise RuntimeError(f'Facebook API HTTP {exc.code}: {msg}') from exc
    except Exception as exc:
        # curl fallback (macOS trust store)
        try:
            cmd = ['curl', '-fsSL', '-X', method.upper(), '-H', 'Accept: application/json']
            if data is not None:
                cmd.extend(['-H', 'Content-Type: application/x-www-form-urlencoded', '--data', urllib.parse.urlencode(data)])
            cmd.extend(['--max-time', str(int(timeout)), url])
            completed = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        except FileNotFoundError as curl_exc:
            raise RuntimeError(f'Facebook network error: {exc}') from curl_exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or b'curl failed').decode('utf-8', errors='replace')[:400]
            raise RuntimeError(f'Facebook network error: {err}') from exc
        raw = completed.stdout

    if not raw:
        return {}
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Invalid Facebook API JSON: {raw[:200]!r}') from exc


def _local_image_path(vehicle) -> str | None:
    """Absolute filesystem path to primary vehicle image, if present."""
    img = None
    try:
        img = vehicle.primary_image()
    except Exception:
        img = None
    if not img or not getattr(img, 'filename', None):
        return None
    upload_folder = current_app.config.get('UPLOAD_FOLDER')
    if not upload_folder:
        return None
    path = os.path.join(upload_folder, 'vehicles', img.filename)
    if os.path.isfile(path):
        return path
    return None


def _post_photo_multipart(
    page_id: str,
    token: str,
    image_path: str,
    message: str,
    *,
    link: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Upload a local image as a published Page photo with caption."""
    url = f'{GRAPH_BASE}/{page_id}/photos'
    boundary = '----MarshallAutoBoundary7MA4YWxkTrZu0gW'
    filename = Path(image_path).name
    mime = mimetypes.guess_type(filename)[0] or 'image/jpeg'
    with open(image_path, 'rb') as fh:
        file_bytes = fh.read()

    fields = {
        'access_token': token,
        'message': message,
        'published': 'true',
    }
    if link:
        # Caption already includes link; keep field optional
        fields['link'] = link

    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            (
                f'--{boundary}\r\n'
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                f'{value}\r\n'
            ).encode('utf-8')
        )
    parts.append(
        (
            f'--{boundary}\r\n'
            f'Content-Disposition: form-data; name="source"; filename="{filename}"\r\n'
            f'Content-Type: {mime}\r\n\r\n'
        ).encode('utf-8')
        + file_bytes
        + b'\r\n'
    )
    parts.append(f'--{boundary}--\r\n'.encode('utf-8'))
    body = b''.join(parts)

    req = urllib.request.Request(
        url,
        data=body,
        method='POST',
        headers={
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Accept': 'application/json',
            'User-Agent': 'MarshallAutoWebsite/1.0',
        },
    )
    ctx = _ssl_context()
    open_kwargs: dict[str, Any] = {'timeout': timeout}
    if ctx is not None:
        open_kwargs['context'] = ctx

    try:
        with urllib.request.urlopen(req, **open_kwargs) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf-8', errors='replace')[:800]
        try:
            payload = json.loads(err_body)
            msg = payload.get('error', {}).get('message') or err_body
        except Exception:
            msg = err_body or str(exc)
        raise RuntimeError(f'Facebook photo upload HTTP {exc.code}: {msg}') from exc
    except Exception as exc:
        # curl multipart fallback
        try:
            cmd = [
                'curl', '-fsSL', '-X', 'POST',
                '-F', f'access_token={token}',
                '-F', f'message={message}',
                '-F', 'published=true',
                '-F', f'source=@{image_path};type={mime}',
                '--max-time', str(int(timeout)),
                url,
            ]
            completed = subprocess.run(cmd, capture_output=True, timeout=timeout + 5)
        except FileNotFoundError as curl_exc:
            raise RuntimeError(f'Facebook photo upload network error: {exc}') from curl_exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or b'curl failed').decode('utf-8', errors='replace')[:400]
            raise RuntimeError(f'Facebook photo upload network error: {err}') from exc
        raw = completed.stdout

    try:
        return json.loads(raw.decode('utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Invalid Facebook photo JSON: {raw[:200]!r}') from exc


def _post_feed_link(page_id: str, token: str, message: str, link: str) -> dict[str, Any]:
    url = f'{GRAPH_BASE}/{page_id}/feed'
    return _http_json(
        'POST',
        url,
        data={
            'access_token': token,
            'message': message,
            'link': link,
        },
    )


def post_vehicle_to_page(vehicle, *, force: bool = False) -> PublishResult:
    """Publish a vehicle listing to the configured Facebook Page.

    Uses photo upload when a local primary image exists; otherwise posts a
    feed item with link + message. Does not create Marketplace listings.

    force=True bypasses the master enable switch (manual admin "Post now" /
    per-vehicle checkbox) but still requires Page ID + token.
    """
    page_id = get_page_id()
    token = get_page_access_token()
    if not page_id or not token:
        return PublishResult(
            ok=False,
            skipped=True,
            error='Facebook Page ID or Page access token is not configured.',
        )

    if not force and not facebook_publishing_enabled():
        return PublishResult(ok=False, skipped=True, error='Facebook Page posting is disabled.')

    status = (getattr(vehicle, 'status', None) or '').lower()
    if status and status != 'available' and not force:
        return PublishResult(
            ok=False,
            skipped=True,
            error=f'Vehicle status is "{status}" (only available vehicles auto-post).',
        )

    message = build_listing_message(vehicle, include_link=True)
    link = vehicle_public_url(vehicle)
    image_path = _local_image_path(vehicle)

    try:
        if image_path:
            payload = _post_photo_multipart(page_id, token, image_path, message, link=link)
            post_id = (
                payload.get('post_id')
                or payload.get('id')
                or payload.get('postId')
            )
            detail = 'Posted photo to Facebook Page.'
        else:
            payload = _post_feed_link(page_id, token, message, link)
            post_id = payload.get('id')
            detail = 'Posted link to Facebook Page (no local image found).'

        if not post_id:
            return PublishResult(
                ok=False,
                error=f'Facebook API returned no post id: {payload!r}'[:400],
            )
        return PublishResult(ok=True, post_id=str(post_id), detail=detail)
    except Exception as exc:
        logger.exception('Facebook Page publish failed for vehicle %s', getattr(vehicle, 'id', '?'))
        return PublishResult(ok=False, error=str(exc)[:500])


def apply_publish_result(vehicle, result: PublishResult) -> None:
    """Write publish outcome onto the vehicle model (caller commits)."""
    from app.models import utcnow

    if result.skipped and not result.ok:
        # Don't clobber a previous successful post id on skip
        if result.error:
            vehicle.facebook_last_error = result.error[:500]
            vehicle.facebook_last_status = 'skipped'
        return

    if result.ok:
        vehicle.facebook_post_id = result.post_id
        vehicle.facebook_posted_at = utcnow()
        vehicle.facebook_last_error = None
        vehicle.facebook_last_status = 'posted'
    else:
        vehicle.facebook_last_error = (result.error or 'Unknown error')[:500]
        vehicle.facebook_last_status = 'error'


def maybe_auto_post_vehicle(vehicle, *, is_new: bool) -> PublishResult | None:
    """Hook after vehicle save. Returns result if an attempt was made, else None."""
    if not facebook_publishing_enabled():
        return None
    if is_new and not facebook_auto_post_on_create():
        return None
    if (not is_new) and not facebook_auto_post_on_edit():
        return None
    if (getattr(vehicle, 'status', None) or '').lower() != 'available':
        return None
    # Avoid duplicate auto-posts on every edit when already posted, unless edit auto-post is on
    # (edit auto-post intentionally creates a fresh Page post with updated details)
    if (not is_new) and vehicle.facebook_post_id and not facebook_auto_post_on_edit():
        return None

    result = post_vehicle_to_page(vehicle, force=False)
    apply_publish_result(vehicle, result)
    return result
