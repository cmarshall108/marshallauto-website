import importlib
import os
import unittest
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from PIL import Image
from sqlalchemy import create_engine, inspect, text

from app import create_app, db
from app.models import User, Vehicle, VehicleSaleImage
from config import TestingConfig


def id_photo():
    stream = BytesIO()
    Image.new('RGB', (64, 40), 'white').save(stream, 'JPEG')
    stream.seek(0)
    return stream


class VehicleSaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            UPLOAD_FOLDER=os.path.join(self.tmp.name, 'public'),
            PRIVATE_UPLOAD_FOLDER=os.path.join(self.tmp.name, 'private'),
            PHOTO_HIGHLIGHTS_ENABLED=False,
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(
            year=2020, make='Honda', model='Civic', price=15000,
            sale_price=14500, mileage=40000, status='sold', slug='sale-test-civic',
        )
        user = User(username='sale-admin')
        user.set_password('test-password-only')
        db.session.add_all([self.vehicle, user])
        db.session.commit()
        self.client = self.app.test_client()
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': user.username, 'password': 'test-password-only',
            })
        self.assertEqual(response.status_code, 302)
        self.path = f'/admin/vehicles/{self.vehicle.id}/sale'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def save(self, **overrides):
        data = {
            'sold_price': '13750.25',
            'payment_method': 'cashiers_check',
            'sale_notes': 'Private buyer payment notes <script>alert(1)</script>',
        }
        data.update(overrides)
        return self.client.post(self.path, data=data)

    def photos(self):
        directory = os.path.join(self.app.config['PRIVATE_UPLOAD_FOLDER'], 'licenses')
        return os.listdir(directory) if os.path.isdir(directory) else []

    def test_save_render_edit_append_photos_and_keep_public_price(self):
        response = self.save(buyer_id_images=[
            (id_photo(), 'front.jpg'), (id_photo(), 'back.jpg'),
        ])
        self.assertEqual(response.status_code, 302)
        db.session.refresh(self.vehicle)
        self.assertEqual(self.vehicle.sold_price, Decimal('13750.25'))
        self.assertEqual(self.vehicle.payment_method, 'cashiers_check')
        self.assertEqual(self.vehicle.display_price, Decimal('14500'))
        self.assertEqual(len(self.vehicle.sale_images), 2)
        self.assertEqual(len(self.vehicle.images), 0)
        self.assertEqual(len(self.photos()), 2)
        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('13750.25', page.text)
        self.assertIn('&lt;script&gt;', page.text)
        self.assertNotIn('<script>alert(1)</script>', page.text)
        self.assertEqual(page.headers['Cache-Control'], 'private, no-store')
        self.assertIn(self.path, self.client.get('/admin/vehicles?status=sold').text)
        self.assertIn(self.path, self.client.get(f'/admin/vehicles/{self.vehicle.id}/edit').text)
        self.assertEqual(self.save(
            sale_notes='Updated notes', buyer_id_images=[(id_photo(), 'extra.jpg')],
        ).status_code, 302)
        db.session.refresh(self.vehicle)
        self.assertEqual(self.vehicle.sale_notes, 'Updated notes')
        self.assertEqual(len(self.vehicle.sale_images), 3)

    def test_blank_and_zero_amounts(self):
        for value, expected in (('', None), ('0', Decimal('0'))):
            with self.subTest(value=value):
                self.assertEqual(self.save(
                    sold_price=value, payment_method='', sale_notes='',
                ).status_code, 302)
                db.session.refresh(self.vehicle)
                self.assertEqual(self.vehicle.sold_price, expected)
                self.assertIsNone(self.vehicle.payment_method)
                self.assertIsNone(self.vehicle.sale_notes)

    def test_invalid_fields_do_not_save(self):
        for data in (
            {'sold_price': '-1'}, {'sold_price': '100000000'},
            {'sold_price': 'NaN'}, {'sold_price': 'Infinity'},
            {'payment_method': 'unrecognized'}, {'sale_notes': 'x' * 20001},
        ):
            with self.subTest(data=str(data)[:80]):
                response = self.save(**data)
                self.assertEqual(response.status_code, 200)
                db.session.refresh(self.vehicle)
                self.assertIsNone(self.vehicle.sold_price)
                self.assertIsNone(self.vehicle.sale_notes)

    def test_bad_photo_rolls_back_entire_submission_and_cleans_saved_files(self):
        response = self.save(buyer_id_images=[
            (id_photo(), 'valid.jpg'), (BytesIO(b'not an image'), 'invalid.jpg'),
        ])
        self.assertEqual(response.status_code, 422)
        self.assertIn('Could not save a buyer ID photo', response.text)
        db.session.refresh(self.vehicle)
        self.assertIsNone(self.vehicle.sold_price)
        self.assertEqual(VehicleSaleImage.query.count(), 0)
        self.assertEqual(self.photos(), [])

    def test_upload_limits_and_unsupported_file(self):
        uploads = [
            [(id_photo(), f'{index}.jpg') for index in range(11)],
            [(BytesIO(b'x' * (15 * 1024 * 1024 + 1)), 'large.jpg')],
            [(BytesIO(b'%PDF-1.4'), 'id.pdf')],
        ]
        for files in uploads:
            with self.subTest(filename=files[0][1]):
                self.assertEqual(self.save(buyer_id_images=files).status_code, 422)
                self.assertEqual(self.photos(), [])

    def test_database_failure_cleans_files(self):
        with patch.object(db.session, 'commit', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                self.save(buyer_id_images=[(id_photo(), 'front.jpg')])
        self.assertEqual(self.photos(), [])
        self.assertEqual(VehicleSaleImage.query.count(), 0)

    def test_private_photos_auth_ownership_deletion_and_csrf(self):
        self.save(buyer_id_images=[(id_photo(), 'front.jpg'), (id_photo(), 'back.jpg')])
        image = VehicleSaleImage.query.first()
        image_id = image.id
        url = f'{self.path}/images/{image_id}'
        public = self.app.test_client()
        with self.app.app_context():
            for path in (self.path, url):
                self.assertEqual(public.get(path).status_code, 302)
            self.assertEqual(public.post(f'{url}/delete').status_code, 302)
            self.assertEqual(public.post(self.path, data={'sale_notes': 'unauthorized'}).status_code, 302)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/jpeg')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        response.close()
        self.assertEqual(self.client.get(
            f'/admin/vehicles/{self.vehicle.id + 1}/sale/images/{image_id}',
        ).status_code, 404)
        self.assertEqual(public.get(f'/static/uploads/vehicles/{image.filename}').status_code, 404)
        self.app.config['WTF_CSRF_ENABLED'] = True
        headers = {'Accept': 'application/json'}
        self.assertEqual(self.client.post(f'{url}/delete', headers=headers).status_code, 400)
        self.assertEqual(self.client.post(self.path, headers=headers).status_code, 400)
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.assertEqual(self.client.post(f'{url}/delete').status_code, 302)
        self.assertEqual(len(self.photos()), 1)
        self.assertIsNone(db.session.get(VehicleSaleImage, image_id))
        self.assertEqual(self.client.post(
            f'/admin/vehicles/{self.vehicle.id}/delete',
        ).status_code, 302)
        self.assertEqual(self.photos(), [])
        self.assertEqual(VehicleSaleImage.query.count(), 0)

    def test_inactive_user_cannot_view_sale_or_photos(self):
        self.save(buyer_id_images=[(id_photo(), 'front.jpg')])
        image = VehicleSaleImage.query.first()
        user = User.query.filter_by(username='sale-admin').one()
        user.is_active_user = False
        db.session.commit()
        for path in (self.path, f'{self.path}/images/{image.id}'):
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_sold_save_redirects_including_deferred_and_relisting_retains_private_records(self):
        self.vehicle.status = 'available'
        db.session.commit()
        self.assertEqual(self.client.get(self.path).status_code, 302)
        data = {
            'year': '2020', 'make': 'Honda', 'model': 'Civic',
            'price': '15000', 'sale_price': '14500', 'mileage': '40000',
            'condition': 'used', 'title_status': 'clean', 'status': 'sold',
        }
        edit_url = f'/admin/vehicles/{self.vehicle.id}/edit'
        self.assertEqual(self.client.post(edit_url, data=data).location, self.path)
        db.session.refresh(self.vehicle)
        self.assertIsNotNone(self.vehicle.sold_at)
        response = self.client.post(edit_url, data=data, headers={
            'X-Vehicle-Media-Upload': 'deferred',
        })
        self.assertEqual(response.json['redirect_url'], self.path)
        response = self.client.post(f'/admin/vehicles/{self.vehicle.id}/media/finish')
        self.assertEqual(response.json['redirect_url'], self.path)
        self.save(buyer_id_images=[(id_photo(), 'front.jpg')])
        data['status'] = 'available'
        self.assertEqual(self.client.post(edit_url, data=data).location, '/admin/vehicles')
        db.session.refresh(self.vehicle)
        self.assertIsNone(self.vehicle.sold_at)
        self.assertEqual(self.vehicle.sold_price, Decimal('13750.25'))
        self.assertEqual(len(self.vehicle.sale_images), 1)
        page = self.app.test_client().get('/inventory/sale-test-civic')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('Private buyer payment notes', page.text)
        self.assertNotIn('13750.25', page.text)
        self.assertNotIn('/sale/images/', page.text)

    def test_new_sold_vehicle_opens_sale_details(self):
        response = self.client.post('/admin/vehicles/new', data={
            'year': '2021', 'make': 'Toyota', 'model': 'Camry',
            'price': '18000', 'mileage': '50000', 'condition': 'used',
            'title_status': 'clean', 'status': 'sold',
        })
        self.assertEqual(response.status_code, 302)
        vehicle = Vehicle.query.filter_by(make='Toyota').one()
        self.assertEqual(response.location, f'/admin/vehicles/{vehicle.id}/sale')
        self.assertIsNotNone(vehicle.sold_at)


class SaleMigrationTests(unittest.TestCase):
    def test_upgrade_idempotence_and_downgrade_preserve_vehicle(self):
        migration = importlib.import_module(
            'migrations.versions.d5e6f7a8b9c0_add_vehicle_sale_details',
        )
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE vehicles (id INTEGER PRIMARY KEY, price NUMERIC(10, 2))'))
            connection.execute(text('INSERT INTO vehicles VALUES (1, 15000)'))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                self.assertIn('vehicle_sale_images', inspect(connection).get_table_names())
                columns = {column['name'] for column in inspect(connection).get_columns('vehicles')}
                self.assertTrue({'sold_price', 'payment_method', 'sale_notes'} <= columns)
                migration.downgrade()
                self.assertNotIn('vehicle_sale_images', inspect(connection).get_table_names())
                self.assertEqual(connection.execute(text('SELECT price FROM vehicles')).scalar(), 15000)
        engine.dispose()
