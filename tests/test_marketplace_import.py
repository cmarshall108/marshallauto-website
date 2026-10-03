import json
import unittest
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from app import create_app, db
from app.models import MarketplaceSync, User, Vehicle, VehicleImage, utcnow
from config import TestingConfig
from app.craigslist_import import craigslist_url, parse_craigslist

from app.marketplace_import import (
    MarketplaceError, apply_photos, attach_import, listing_url, parse_listing,
    photo_token, photo_url, preview_token, read_preview, stage_photos, sync_due,
)


URL = 'https://www.facebook.com/marketplace/item/12345/'
CRAIGSLIST_URL = 'https://www.craigslist.org/view/d/sanford-2018-buick-regal-sportback/nE3yePJrnEKxic4P388Xg7'


def craigslist_html(price='7999.00', description='Rebuilt title. Professionally repaired.', image_names=('front', 'rear')):
    images = [f'https://images.craigslist.org/{name}_600x450.jpg' for name in image_names]
    product = {'@type': 'Product', 'name': '2018 Buick Regal Sportback',
               'offers': {'price': price, 'priceCurrency': 'USD'}, 'image': images}
    thumbs = ''.join(f'<a href="{url}">Photo</a>' for url in images)
    return f'''<link rel="canonical" href="{CRAIGSLIST_URL}">
        <script id="ld_posting_data" type="application/ld+json">{json.dumps(product)}</script>
        <section id="postingbody"><div class="print-information">QR Code Link</div>{description}</section>
        <div class="attrgroup"><div class="attr important"><span class="valu year">2018</span>
            <span class="valu makemodel">buick regal sportback ii</span></div>
            <div class="attr auto_miles"><span class="labl">odometer:</span><span class="valu">67,000</span></div>
            <div class="attr auto_title_status"><span class="labl">title status:</span><span class="valu">rebuilt</span></div>
            <div class="attr auto_fuel_type"><span class="labl">fuel:</span><span class="valu">gas</span></div>
            <div class="attr auto_transmission"><span class="labl">transmission:</span><span class="valu">automatic</span></div>
        </div><div id="thumbs">{thumbs}</div><p class="postinginfo">post id: 7974467394</p>'''


class CraigslistParsingTests(unittest.TestCase):
    def test_details_description_and_all_photos(self):
        result = parse_craigslist(craigslist_html(description='Rebuilt title.<br>New seats.'), CRAIGSLIST_URL)
        self.assertEqual(result['fields']['price'], '7999.00')
        self.assertEqual(result['fields']['mileage'], 67000)
        self.assertEqual(result['fields']['title_status'], 'rebuilt')
        self.assertEqual(result['fields']['make'], 'Buick')
        self.assertEqual(result['fields']['model'], 'Regal Sportback II')
        self.assertEqual(result['fields']['description'], 'Rebuilt title.\nNew seats.')
        self.assertEqual([image['id'] for image in result['photos']], ['front', 'rear'])
        self.assertEqual(result['source_id'], '7974467394')
        self.assertNotIn('status', result['fields'])

    def test_legacy_url_matches_posting_id(self):
        legacy = 'https://raleigh.craigslist.org/cto/d/sanford-buick/7974467394.html'
        self.assertEqual(parse_craigslist(craigslist_html(), legacy)['url'], CRAIGSLIST_URL)
        with self.assertRaises(MarketplaceError):
            parse_craigslist(craigslist_html(), legacy.replace('7974467394', '7974467395'))

    def test_url_boundaries(self):
        self.assertEqual(listing_url(CRAIGSLIST_URL + '?tracking=1'), CRAIGSLIST_URL)
        for url in ('https://www.craigslist.org/account', 'https://www.craigslist.org/search/cta',
                    CRAIGSLIST_URL.replace('www.craigslist.org', 'craigslist.org.evil.test'),
                    CRAIGSLIST_URL.replace('www.craigslist.org', 'user@www.craigslist.org'),
                    CRAIGSLIST_URL.replace('https:', 'http:'),
                    CRAIGSLIST_URL.replace('www.craigslist.org', 'localhost'),
                    CRAIGSLIST_URL.replace('www.craigslist.org', 'www.craigslist.org:8000')):
            with self.assertRaises(MarketplaceError):
                craigslist_url(url)

    def test_unavailable_wrong_identity_and_incomplete_photos_rejected(self):
        for html in ('<h1>This posting has expired.</h1>',
                     craigslist_html().replace('nE3yePJrnEKxic4P388Xg7', 'nE3yePJrnEKxic4P388Xg8'),
                     craigslist_html().replace('id="postingbody"', 'id="missing"'),
                     craigslist_html().replace('</div><p class="postinginfo">', '<a href="https://images.craigslist.org/extra_600x450.jpg">Extra</a></div><p class="postinginfo">'),
                     craigslist_html().replace('"USD"', '"CAD"')):
            with self.assertRaises(MarketplaceError):
                parse_craigslist(html, CRAIGSLIST_URL)

    def test_missing_mileage_is_not_invented(self):
        result = parse_craigslist(craigslist_html().replace('67,000', 'unknown'), CRAIGSLIST_URL)
        self.assertNotIn('mileage', result['fields'])

    def test_unsafe_photos_rejected(self):
        for host in ('127.0.0.1', 'images.craigslist.org.evil.test', 'scontent.xx.fbcdn.net'):
            with self.assertRaises(MarketplaceError):
                parse_craigslist(craigslist_html().replace('images.craigslist.org', host), CRAIGSLIST_URL)

    def test_download_redirect_stays_on_valid_craigslist_listings(self):
        from unittest.mock import MagicMock
        from app.marketplace_import import download
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 302
        for destination in ('https://127.0.0.1/internal', 'https://www.craigslist.org/account', URL):
            response.headers = {'Location': destination}
            with patch('app.marketplace_import.requests.get', return_value=response) as request:
                with self.assertRaises(MarketplaceError):
                    download(CRAIGSLIST_URL)
                self.assertEqual(request.call_count, 1)


def listing_html(**changes):
    listing = {
        'id': '12345', 'marketplace_listing_title': '2018 Toyota Camry SE',
        'vehicle_model_display_name': 'Camry', 'vehicle_trim_display_name': 'SE',
        'listing_price': {'amount': '12500', 'currency': 'USD'},
        'vehicle_odometer_data': {'value': 80000, 'unit': 'MILES'},
        'seller_description': {'text': 'Clean car.'},
        'is_sold': False, 'is_pending': False,
        'listing_photos': [
            {'id': 'photo1', 'image': {'uri': 'https://scontent.xx.fbcdn.net/car1.jpg'}},
            {'id': 'photo2', 'image': {'uri': 'https://scontent.xx.fbcdn.net/car2.jpg'}},
        ],
    }
    listing.update(changes)
    return '<script type="application/json">' + json.dumps({'data': {'listing': listing}}) + '</script>'


class MarketplaceParsingTests(unittest.TestCase):
    def test_requested_listing_fields_and_photo_order(self):
        result = parse_listing(listing_html(), URL)
        self.assertEqual(result['fields']['price'], '12500.00')
        self.assertEqual(result['fields']['model'], 'Camry')
        self.assertEqual(result['fields']['mileage'], 80000)
        self.assertEqual([photo['id'] for photo in result['photos']], ['photo1', 'photo2'])

    def test_login_and_other_listings_rejected(self):
        for html in ('<html>Log in</html>', listing_html(id='67890')):
            with self.assertRaises(MarketplaceError):
                parse_listing(html, URL)

    def test_missing_photos_and_foreign_currency_rejected(self):
        for changes in ({'listing_photos': []}, {'listing_price': {'amount': '100', 'currency': 'CAD'}}):
            with self.assertRaises(MarketplaceError):
                parse_listing(listing_html(**changes), URL)

    def test_urls_are_restricted(self):
        for url in ('http://www.facebook.com/marketplace/item/12345/',
                    'https://facebook.com.evil.test/marketplace/item/12345/',
                    'https://localhost/marketplace/item/12345/',
                    'https://www.facebook.com/share/abc/',
                    'https://user@www.facebook.com/marketplace/item/12345/'):
            with self.assertRaises(MarketplaceError):
                listing_url(url)
        with self.assertRaises(MarketplaceError):
            photo_url('https://127.0.0.1/photo.jpg')
        self.assertEqual(listing_url(URL + '?tracking=1'), URL)

    def test_unknown_mileage_is_not_zero(self):
        result = parse_listing(listing_html(vehicle_odometer_data=None), URL)
        self.assertNotIn('mileage', result['fields'])

    def test_optional_details_and_multiword_makes(self):
        result = parse_listing(listing_html(
            marketplace_listing_title='2018 Land Rover Discovery',
            vehicle_model_display_name='Discovery', vehicle_title_status='Rebuilt title',
            vehicle_features=['Leather seats', {'name': 'Sunroof'}], vehicle_mpg_city=20,
        ), URL)
        self.assertEqual(result['fields']['make'], 'Land Rover')
        self.assertEqual(result['fields']['title_status'], 'rebuilt')
        self.assertEqual(result['fields']['features'], 'Leather seats, Sunroof')
        self.assertEqual(result['fields']['mpg_city'], 20)

    def test_incomplete_photo_collection_is_rejected(self):
        for changes in ({'listing_photos_count': 5}, {'listing_photos': {'page_info': {'has_next_page': True}}}):
            with self.assertRaises(MarketplaceError):
                parse_listing(listing_html(**changes), URL)


class MarketplaceSyncTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['PHOTO_HIGHLIGHTS_ENABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(year=2018, make='Toyota', model='Camry', price=12500,
                               mileage=80000, description='Staff correction')
        db.session.add(self.vehicle)
        db.session.flush()
        self.snapshot = parse_listing(listing_html(), URL)
        self.photos = [{'id': photo['id'], 'filename': photo['id'] + '.jpg',
                        'width': 600, 'height': 400} for photo in self.snapshot['photos']]
        self.source = attach_import(self.vehicle, self.snapshot, self.photos)
        self.source.next_check_at = utcnow() - timedelta(minutes=1)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def test_signed_preview_is_bound_to_user(self):
        token = preview_token(self.snapshot, 1)
        self.assertEqual(read_preview(token, 1), self.snapshot)
        for value, user in ((token + 'bad', 1), (token, 2)):
            with self.assertRaises(MarketplaceError):
                read_preview(value, user)
        with patch('time.time', return_value=0):
            expired = preview_token(self.snapshot, 1)
        with self.assertRaises(MarketplaceError):
            read_preview(expired, 1)

    def test_refresh_changes_price_preserves_local_description_and_photos(self):
        changed = parse_listing(listing_html(listing_price={'amount': '11900', 'currency': 'USD'}), URL)
        with patch('app.marketplace_import.fetch_listing', return_value=changed), \
                patch('app.marketplace_import.download') as download:
            self.assertEqual(sync_due(), {'checked': 1, 'updated': 1, 'failed': 0})
            download.assert_not_called()
        self.assertEqual(self.vehicle.price, 11900)
        self.assertEqual(self.vehicle.description, 'Staff correction')
        self.assertEqual(len(self.vehicle.images), 2)
        self.assertGreater(self.source.next_check_at, utcnow())
        self.assertEqual(sync_due()['checked'], 0)

    def test_failed_refresh_retains_vehicle_and_schedules_retry(self):
        with patch('app.marketplace_import.fetch_listing', side_effect=MarketplaceError('Login required')):
            self.assertEqual(sync_due()['failed'], 1)
        self.assertEqual(self.vehicle.price, 12500)
        self.assertEqual(len(self.vehicle.images), 2)
        self.assertEqual(self.source.last_error, 'Login required')
        self.assertGreater(self.source.next_check_at, utcnow())

    def test_repeated_listing_updates_do_not_repeat_ai_analysis(self):
        from app.highlight_jobs import claim_next_job, enqueue_vehicle_highlight_jobs, process_job
        from app.models import PhotoHighlightJob

        self.app.config.update(PHOTO_HIGHLIGHTS_ENABLED=True, PHOTO_HIGHLIGHTS_AUTO_ENQUEUE=True)
        result = {'scene': 'exterior_side', 'highlights': [], 'analysis_version': 3, 'engine': 'grok'}
        with patch('app.photo_highlights.analyze_vehicle_image', return_value=result) as analyze:
            self.assertEqual(enqueue_vehicle_highlight_jobs(self.vehicle.id), 2)
            for image_index in range(2):
                self.assertTrue(process_job(claim_next_job()))
            analyzed_at = {image.id: image.highlight_analyzed_at for image in self.vehicle.images}
            for price in ('11900', '11500', '11000'):
                changed = parse_listing(listing_html(listing_price={'amount': price, 'currency': 'USD'}), URL)
                self.source.next_check_at = utcnow() - timedelta(minutes=1)
                db.session.commit()
                with patch('app.marketplace_import.fetch_listing', return_value=changed), \
                        patch('app.marketplace_import.download') as download:
                    self.assertEqual(sync_due(), {'checked': 1, 'updated': 1, 'failed': 0})
                    download.assert_not_called()
                self.assertEqual(PhotoHighlightJob.query.count(), 2)
                self.assertIsNone(claim_next_job())
                self.assertEqual(analyze.call_count, 2)
            self.assertEqual(self.vehicle.price, 11000)
            self.assertEqual({image.id: image.highlight_analyzed_at for image in self.vehicle.images}, analyzed_at)

    def test_photo_replacement_keeps_staff_uploads(self):
        self.vehicle.images.append(VehicleImage(filename='manual.jpg', is_primary=False, order_index=2))
        removed = apply_photos(self.vehicle, self.photos[1:], self.photos)
        db.session.commit()
        self.assertEqual(removed, [{'filename': 'photo1.jpg'}])
        self.assertEqual({image.filename for image in self.vehicle.images}, {'photo2.jpg', 'manual.jpg'})
        self.assertEqual(sum(image.is_primary for image in self.vehicle.images), 1)

    def test_disabled_job_skipped_and_vehicle_delete_cascades(self):
        self.source.enabled = False
        db.session.commit()
        self.assertEqual(sync_due()['checked'], 0)
        db.session.delete(self.vehicle)
        db.session.commit()
        self.assertEqual(MarketplaceSync.query.count(), 0)

    def client(self):
        user = User(username='import-admin')
        user.set_password('test-password-only')
        db.session.add(user)
        db.session.commit()
        client = self.app.test_client()
        response = client.post('/admin/login', data={
            'username': user.username, 'password': 'test-password-only',
        })
        self.assertEqual(response.status_code, 302)
        return client, user

    def test_admin_preview_and_save_create_linked_vehicle(self):
        client, user = self.client()
        with patch('app.admin.fetch_listing', return_value=self.snapshot):
            response = client.post('/admin/vehicles/marketplace/preview', data={'url': URL, 'permission': '1'})
        self.assertEqual(response.status_code, 200)
        data = dict(self.snapshot['fields'], marketplace_url=URL,
                    marketplace_token=response.json['token'], marketplace_sync_enabled='y',
                    condition='used', title_status='clean')
        with patch('app.admin.stage_photos', return_value=(self.photos, [])):
            response = client.post('/admin/vehicles/new', data=data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MarketplaceSync.query.count(), 2)
        imported = Vehicle.query.order_by(Vehicle.id.desc()).first()
        self.assertEqual(imported.marketplace_sync.source_url, URL)
        self.assertEqual(len(imported.images), 2)
        self.assertTrue(imported.marketplace_sync.enabled)

    def test_preview_requires_login_and_permission(self):
        self.assertEqual(self.app.test_client().post('/admin/vehicles/marketplace/preview').status_code, 302)
        client, user = self.client()
        self.assertEqual(client.post('/admin/vehicles/marketplace/preview', data={'url': URL}).status_code, 400)

    def test_photo_preview_is_local_authenticated_and_user_bound(self):
        self.assertEqual(self.app.test_client().get('/admin/vehicles/marketplace/photo').status_code, 302)
        client, user = self.client()
        image = BytesIO()
        Image.new('RGB', (600, 400), 'red').save(image, format='JPEG')
        token = photo_token(self.snapshot['photos'][0]['url'], user.id)
        with patch('app.marketplace_import.download', return_value=image.getvalue()):
            response = client.get('/admin/vehicles/marketplace/photo', query_string={'token': token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        self.assertEqual(Image.open(BytesIO(response.data)).width, 336)
        wrong_user = photo_token(self.snapshot['photos'][0]['url'], user.id + 1)
        self.assertEqual(client.get('/admin/vehicles/marketplace/photo', query_string={'token': wrong_user}).status_code, 422)
        with self.assertRaises(MarketplaceError):
            read_preview(token, user.id)

    def test_new_and_edit_forms_render_without_nested_forms(self):
        client, user = self.client()
        for path in ('/admin/vehicles/new', f'/admin/vehicles/{self.vehicle.id}/edit'):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'marketplace_sync_enabled', response.data)

    def test_real_photo_download_is_saved_and_partial_failure_cleaned(self):
        from pathlib import Path
        from app.marketplace_import import discard_photos

        image = BytesIO()
        Image.new('RGB', (80, 60), 'red').save(image, format='JPEG')
        with TemporaryDirectory() as folder:
            self.app.config['UPLOAD_FOLDER'] = folder
            with patch('app.marketplace_import.download', return_value=image.getvalue()):
                photos, created = stage_photos(self.snapshot)
            self.assertEqual(len(photos), 2)
            self.assertTrue(all((Path(folder) / 'vehicles' / photo['filename']).exists() for photo in photos))
            discard_photos(created)
            with patch('app.marketplace_import.download', side_effect=[image.getvalue(), MarketplaceError('Unavailable')]):
                with self.assertRaises(MarketplaceError):
                    stage_photos(self.snapshot)
            self.assertEqual(list((Path(folder) / 'vehicles').iterdir()), [])

    def test_failed_initial_photos_do_not_create_vehicle(self):
        client, user = self.client()
        data = dict(self.snapshot['fields'], marketplace_url=URL,
                    marketplace_token=preview_token(self.snapshot, user.id),
                    condition='used', title_status='clean')
        with patch('app.admin.stage_photos', side_effect=MarketplaceError('Photo unavailable')):
            response = client.post('/admin/vehicles/new', data=data)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Photo unavailable', response.data)
        self.assertEqual(Vehicle.query.count(), 1)

    def test_sync_can_be_paused_and_resumed_on_edit(self):
        client, user = self.client()
        data = dict(self.snapshot['fields'], condition='used', title_status='clean')
        self.assertEqual(client.post(f'/admin/vehicles/{self.vehicle.id}/edit', data=data).status_code, 302)
        self.assertFalse(self.source.enabled)
        data['marketplace_sync_enabled'] = 'y'
        self.assertEqual(client.post(f'/admin/vehicles/{self.vehicle.id}/edit', data=data).status_code, 302)
        self.assertTrue(self.source.enabled)

    def test_craigslist_admin_import_creates_enabled_refresh_record(self):
        client, user = self.client()
        with patch('app.marketplace_import.download', return_value=craigslist_html().encode()):
            preview = client.post('/admin/vehicles/marketplace/preview', data={'url': CRAIGSLIST_URL, 'permission': '1'})
        self.assertEqual(preview.status_code, 200)
        data = dict(preview.json['snapshot']['fields'], marketplace_url=CRAIGSLIST_URL,
                    marketplace_token=preview.json['token'], marketplace_sync_enabled='y',
                    condition='used', status='available')
        with patch('app.admin.stage_photos', return_value=(self.photos, [])):
            self.assertEqual(client.post('/admin/vehicles/new', data=data).status_code, 302)
        imported = Vehicle.query.order_by(Vehicle.id.desc()).first()
        self.assertEqual(imported.price, 7999)
        self.assertEqual(imported.title_status, 'rebuilt')
        self.assertEqual(imported.marketplace_sync.source_url, CRAIGSLIST_URL)
        self.assertTrue(imported.marketplace_sync.enabled)
        self.assertIn(b'Source Craigslist listing', client.get(f'/admin/vehicles/{imported.id}/edit').data)

    def test_craigslist_refresh_changes_price_description_and_gallery(self):
        self.source.source_url = CRAIGSLIST_URL
        self.source.snapshot = parse_craigslist(craigslist_html(), CRAIGSLIST_URL)
        self.source.photo_files = [dict(photo, id=identity) for photo, identity in zip(self.photos, ('front', 'rear'))]
        db.session.commit()
        image = BytesIO()
        Image.new('RGB', (80, 60), 'blue').save(image, format='JPEG')
        updated = craigslist_html(price='7499', description='Updated seller description.', image_names=('rear', 'interior'))
        with TemporaryDirectory() as folder:
            self.app.config['UPLOAD_FOLDER'] = folder
            with patch('app.marketplace_import.download', side_effect=[updated.encode(), image.getvalue()]):
                self.assertEqual(sync_due()['updated'], 1)
            self.assertEqual(self.vehicle.price, 7499)
            self.assertEqual(self.vehicle.description, 'Updated seller description.')
            self.assertEqual(len(self.vehicle.images), 2)
            self.assertEqual([photo['id'] for photo in self.source.photo_files], ['rear', 'interior'])
            self.assertIsNone(self.source.last_error)

    def test_craigslist_removed_listing_retains_existing_inventory(self):
        self.source.source_url = CRAIGSLIST_URL
        self.source.snapshot = parse_craigslist(craigslist_html(), CRAIGSLIST_URL)
        db.session.commit()
        with patch('app.marketplace_import.download', return_value=b'<h1>This posting has expired.</h1>'):
            self.assertEqual(sync_due()['failed'], 1)
        self.assertEqual(self.vehicle.price, 12500)
        self.assertEqual(len(self.vehicle.images), 2)
        self.assertEqual(self.vehicle.status, 'available')
        self.assertTrue(self.source.last_error)
        self.assertGreater(self.source.next_check_at, utcnow())


if __name__ == '__main__':
    unittest.main()