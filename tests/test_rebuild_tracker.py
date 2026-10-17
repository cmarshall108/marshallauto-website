import unittest
from decimal import Decimal
from unittest.mock import patch

from app import create_app, db
from app.models import RebuildCostEntry, User, Vehicle
from config import TestingConfig


class RebuildCostTrackerTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
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

if __name__ == '__main__':
    unittest.main()
