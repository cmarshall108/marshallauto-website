import io
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage

from app import create_app, db
from app.models import RebuildCostEntry, RebuildCostReceipt, User, Vehicle
from app.utils import save_uploaded_receipt_image
from config import TestingConfig


class RebuildCostTrackerTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['LOGIN_DISABLED'] = False
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(
            year=2020, make='Honda', model='Civic', price=15000,
            sale_price=14000, mileage=40000, status='available',
            slug='rebuild-test-civic',
        )
        self.user = User(username='rebuild-admin')
        self.user.set_password('rebuild-test-password')
        db.session.add_all([self.vehicle, self.user])
        db.session.commit()
        self.client = self.app.test_client()
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': self.user.username,
                'password': 'rebuild-test-password',
            })
        self.assertEqual(response.status_code, 302)
        self.path = f'/admin/vehicles/{self.vehicle.id}/rebuild'

    def tearDown(self):
        for receipt in RebuildCostReceipt.query.all():
            path = os.path.join(
                self.app.config['PRIVATE_UPLOAD_FOLDER'],
                'rebuild_receipts',
                receipt.filename,
            )
            if os.path.exists(path):
                os.remove(path)
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def test_tracker_adds_acquisition_and_itemized_costs_and_calculates_margin(self):
        response = self.client.post(self.path, data={
            'action': 'save_acquisition',
            'acquisition_cost': '9000.00',
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(self.path, data={
            'action': 'save_entry',
            'category': 'parts',
            'description': 'Headlights',
            'vendor': 'Parts Store',
            'purchase_date': '2026-10-15',
            'quantity': '2',
            'unit_cost': '50.00',
            'notes': 'Pair',
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.post(self.path, data={
            'action': 'save_entry',
            'category': 'service',
            'description': 'Wheel alignment',
            'quantity': '1',
            'unit_cost': '500.00',
        })
        self.assertEqual(response.status_code, 302)

        page = self.client.get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('$9,600.00', page.text)
        self.assertIn('$4,400.00', page.text)
        self.assertIn('31.43%', page.text)
        self.assertIn('45.83%', page.text)
        self.assertIn('Current Asking Price', page.text)
        db.session.refresh(self.vehicle)
        self.assertEqual(self.vehicle.acquisition_cost, Decimal('9000.00'))
        self.assertEqual(RebuildCostEntry.query.count(), 2)

    def test_tracker_uses_actual_sold_price_when_recorded(self):
        self.vehicle.status = 'sold'
        self.vehicle.sold_price = Decimal('13000.00')
        self.vehicle.acquisition_cost = Decimal('10000.00')
        db.session.commit()

        page = self.client.get(self.path)
        self.assertIn('Actual Sale Price', page.text)
        self.assertIn('Actual Profit', page.text)
        self.assertIn('$3,000.00', page.text)

    def test_cost_items_can_be_edited_and_deleted(self):
        entry = RebuildCostEntry(
            vehicle_id=self.vehicle.id,
            category='parts',
            description='Old bumper',
            quantity=1,
            unit_cost=Decimal('250.00'),
        )
        db.session.add(entry)
        db.session.commit()

        edit_url = f'{self.path}/edit/{entry.id}'
        page = self.client.get(edit_url)
        self.assertEqual(page.status_code, 302)
        response = self.client.post(f'{self.path}?edit={entry.id}', data={
            'action': 'save_entry',
            'entry_id': str(entry.id),
            'category': 'parts',
            'description': 'Replacement bumper',
            'quantity': '1',
            'unit_cost': '325.00',
        })
        self.assertEqual(response.status_code, 302)
        db.session.refresh(entry)
        self.assertEqual(entry.description, 'Replacement bumper')
        self.assertEqual(entry.unit_cost, Decimal('325.00'))

        response = self.client.post(
            f'{self.path}/{entry.id}/delete',
            data={'csrf_token': 'unused'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RebuildCostEntry.query.count(), 0)

    def test_rejects_negative_costs_and_unknown_actions(self):
        response = self.client.post(self.path, data={
            'action': 'save_acquisition',
            'acquisition_cost': '-1',
        })
        self.assertEqual(response.status_code, 422)

        response = self.client.post(self.path, data={
            'action': 'save_entry',
            'category': 'parts',
            'description': 'Invalid cost',
            'quantity': '1',
            'unit_cost': '-1',
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(RebuildCostEntry.query.count(), 0)

        response = self.client.post(self.path, data={'action': 'unknown'})
        self.assertEqual(response.status_code, 400)

    @staticmethod
    def receipt_photo(color='white', filename='receipt.jpg'):
        photo = io.BytesIO()
        Image.new('RGB', (40, 30), color).save(photo, format='JPEG')
        photo.seek(0)
        return photo, filename

    def test_receipt_photos_upload_display_privately_and_delete_with_cost_item(self):
        response = self.client.post(self.path, data={
            'action': 'save_entry',
            'category': 'parts',
            'description': 'Battery',
            'quantity': '1',
            'unit_cost': '120.00',
            'receipt_images': [
                self.receipt_photo('white', 'front.jpg'),
                self.receipt_photo('gray', 'back.jpg'),
            ],
        })
        self.assertEqual(response.status_code, 302)
        entry = RebuildCostEntry.query.one()
        receipts = RebuildCostReceipt.query.filter_by(cost_entry_id=entry.id).all()
        self.assertEqual(len(receipts), 2)

        for receipt in receipts:
            path = os.path.join(
                self.app.config['PRIVATE_UPLOAD_FOLDER'],
                'rebuild_receipts',
                receipt.filename,
            )
            self.assertTrue(os.path.isfile(path))
            self.assertNotIn('/static/', path)
            image_response = self.client.get(
                f'{self.path}/{entry.id}/receipts/{receipt.id}',
            )
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(image_response.mimetype, 'image/jpeg')
            self.assertEqual(image_response.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(Image.open(io.BytesIO(image_response.data)).format, 'JPEG')

        edit_page = self.client.get(f'{self.path}/edit/{entry.id}')
        self.assertEqual(edit_page.status_code, 302)
        edit_page = self.client.get(f'{self.path}?edit={entry.id}')
        self.assertEqual(edit_page.status_code, 200)
        self.assertEqual(edit_page.text.count('aria-label="View receipt photo'), 2)

        anonymous_app = create_app(TestingConfig)
        anonymous_app.config['LOGIN_DISABLED'] = False
        anonymous = anonymous_app.test_client()
        private_image = anonymous.get(
            f'{self.path}/{entry.id}/receipts/{receipts[0].id}',
        )
        self.assertEqual(private_image.status_code, 302)

        paths = [
            os.path.join(
                self.app.config['PRIVATE_UPLOAD_FOLDER'],
                'rebuild_receipts',
                receipt.filename,
            )
            for receipt in receipts
        ]
        first_receipt = receipts[0]
        response = self.client.post(
            f'{self.path}/{entry.id}/receipts/{first_receipt.id}/delete',
            data={'csrf_token': 'unused'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RebuildCostReceipt.query.count(), 1)
        self.assertFalse(os.path.exists(paths[0]))

        response = self.client.post(
            f'{self.path}/{entry.id}/delete',
            data={'csrf_token': 'unused'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RebuildCostReceipt.query.count(), 0)
        self.assertFalse(any(os.path.exists(path) for path in paths))

    def test_invalid_receipt_image_does_not_save_cost_item(self):
        invalid = (io.BytesIO(b'not an image'), 'receipt.jpg')
        response = self.client.post(self.path, data={
            'action': 'save_entry',
            'category': 'parts',
            'description': 'Invalid receipt',
            'quantity': '1',
            'unit_cost': '10.00',
            'receipt_images': [invalid],
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn('Could not save a receipt photo', response.text)
        self.assertEqual(RebuildCostEntry.query.count(), 0)
        self.assertEqual(RebuildCostReceipt.query.count(), 0)

    def test_receipt_photo_limit_includes_existing_photos(self):
        entry = RebuildCostEntry(
            vehicle_id=self.vehicle.id,
            category='parts',
            description='Tire set',
            quantity=1,
            unit_cost=Decimal('400.00'),
        )
        db.session.add(entry)
        db.session.flush()
        db.session.add_all(
            RebuildCostReceipt(cost_entry_id=entry.id, filename=f'missing-{index}.jpg')
            for index in range(10)
        )
        db.session.commit()

        response = self.client.post(self.path, data={
            'action': 'save_entry',
            'entry_id': str(entry.id),
            'category': 'parts',
            'description': 'Tire set',
            'quantity': '1',
            'unit_cost': '400.00',
            'receipt_images': [self.receipt_photo()],
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn('Upload no more than 10 receipt photos per cost item.', response.text)
        self.assertEqual(RebuildCostReceipt.query.count(), 10)
        db.session.refresh(entry)
        self.assertEqual(entry.description, 'Tire set')

    def test_receipt_photo_delete_is_scoped_to_vehicle_entry(self):
        entry = RebuildCostEntry(
            vehicle_id=self.vehicle.id,
            category='parts',
            description='Tire',
            quantity=1,
            unit_cost=Decimal('100.00'),
        )
        db.session.add(entry)
        db.session.flush()
        db.session.add(RebuildCostReceipt(cost_entry_id=entry.id, filename='missing.jpg'))
        db.session.commit()
        receipt = RebuildCostReceipt.query.one()

        response = self.client.post(
            f'/admin/vehicles/999/rebuild/{entry.id}/receipts/{receipt.id}/delete',
            data={'csrf_token': 'unused'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(RebuildCostReceipt.query.count(), 1)

    def test_vehicle_deletion_removes_private_receipt_photos(self):
        entry = RebuildCostEntry(
            vehicle_id=self.vehicle.id,
            category='parts',
            description='Mirror',
            quantity=1,
            unit_cost=Decimal('75.00'),
        )
        db.session.add(entry)
        db.session.flush()
        photo, original_name = self.receipt_photo('blue')
        filename = save_uploaded_receipt_image(
            FileStorage(stream=photo, filename=original_name),
        )
        self.assertIsNotNone(filename)
        db.session.add(
            RebuildCostReceipt(cost_entry_id=entry.id, filename=filename),
        )
        db.session.commit()
        path = os.path.join(
            self.app.config['PRIVATE_UPLOAD_FOLDER'],
            'rebuild_receipts',
            filename,
        )
        self.assertTrue(os.path.isfile(path))

        response = self.client.post(
            f'/admin/vehicles/{self.vehicle.id}/delete',
            data={'csrf_token': 'unused'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(RebuildCostReceipt.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
