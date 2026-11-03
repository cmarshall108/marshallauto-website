import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from bs4 import BeautifulSoup
from flask.globals import _cv_app

from app import create_app, db
from app.models import User, Vehicle, VehicleSaleReceipt
from config import TestingConfig


class SaleReceiptTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(username='receipt-admin')
        self.user.set_password('receipt-test-password')
        self.vehicle = Vehicle(
            slug='receipt-camry', year=2018, make='Toyota', model='Camry',
            trim='SE', vin='4T1BF1FK5JU000001', price=19900, sale_price=18900,
            sold_price=18000, mileage=82000, status='sold',
        )
        db.session.add_all([self.user, self.vehicle])
        db.session.commit()
        self.client = self.app.test_client()
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': self.user.username,
                'password': 'receipt-test-password',
            })
        self.assertEqual(response.status_code, 302)
        self.path = f'/admin/vehicles/{self.vehicle.id}/sale/receipt'

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def test_receipt_requires_admin_login(self):
        _cv_app.get().g.pop('_login_user', None)
        client = self.app.test_client()
        response = client.get(self.path)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.headers['Location'])
        printed = client.get(f'{self.path}/print')
        self.assertEqual(printed.status_code, 302)
        self.assertIn('/admin/login', printed.headers['Location'])

    def test_saves_and_prints_manual_itemized_receipt(self):
        editor = self.client.get(self.path)
        self.assertEqual(editor.status_code, 200)
        self.assertIn(b'Highway Use Tax and fees are entered manually', editor.data)
        self.assertIn(b'value="18000.00"', editor.data)

        response = self.client.post(self.path, data={
            'buyer_name': 'Jordan Customer',
            'buyer_address': '123 Main Street, Sanford, NC',
            'vehicle_price': '17000.00',
            'highway_use_tax': '600.00',
            'document_fee': '150.00',
            'title_registration_fee': '200.00',
            'other_charges_data': json.dumps([
                {'description': 'Inspection', 'amount': '75.00'},
            ]),
            'credits': '500.00',
            'amount_received': '18000.00',
            'payment_method': 'cashiers_check',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/sale/receipt/print', response.headers['Location'])

        receipt = VehicleSaleReceipt.query.one()
        self.assertEqual(receipt.vehicle_id, self.vehicle.id)
        self.assertEqual(receipt.total_due, Decimal('17525.00'))
        self.assertEqual(receipt.charges[0].description, 'Inspection')
        self.assertEqual(receipt.amount_received, Decimal('18000.00'))

        printed = self.client.get(response.headers['Location'])
        self.assertEqual(printed.status_code, 200)
        soup = BeautifulSoup(printed.data, 'html.parser')
        text = ' '.join(soup.get_text(' ', strip=True).split())
        self.assertIn('Vehicle Sale Receipt', text)
        self.assertIn('Receipt #:', text)
        self.assertIn('Jordan Customer', text)
        self.assertIn('Highway Use Tax', text)
        self.assertIn('Inspection', text)
        self.assertIn('$17,525.00', text)
        self.assertIn('$18,000.00', text)
        self.assertIn('$475.00', text)
        self.assertIn("Cashier's Check", text)
        self.assertIsNotNone(soup.select_one('button[onclick="window.print()"]'))

        saved = self.client.get(self.path)
        self.assertIn(b'Jordan Customer', saved.data)
        self.assertIn(b'Inspection', saved.data)

        updated = self.client.post(self.path, data={
            'buyer_name': 'Jordan C.',
            'buyer_address': '',
            'vehicle_price': '16900.00',
            'highway_use_tax': '600.00',
            'document_fee': '150.00',
            'title_registration_fee': '200.00',
            'other_charges_data': json.dumps([
                {'description': 'Dealer preparation', 'amount': '90.00'},
            ]),
            'credits': '500.00',
            'amount_received': '17440.00',
            'payment_method': 'cash',
        })
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(VehicleSaleReceipt.query.count(), 1)
        self.assertEqual(receipt.total_due, Decimal('17440.00'))
        self.assertEqual([charge.description for charge in receipt.charges], ['Dealer preparation'])

    def test_rejects_credits_that_exceed_total(self):
        response = self.client.post(self.path, data={
            'vehicle_price': '100.00',
            'highway_use_tax': '0',
            'document_fee': '0',
            'title_registration_fee': '0',
            'other_charges_data': '[]',
            'credits': '101.00',
            'amount_received': '0',
            'payment_method': '',
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'Credits and deposits cannot exceed the itemized charges.', response.data)
        self.assertEqual(VehicleSaleReceipt.query.count(), 0)

    def test_limits_extra_charge_count(self):
        response = self.client.post(self.path, data={
            'vehicle_price': '100.00',
            'highway_use_tax': '0',
            'document_fee': '0',
            'title_registration_fee': '0',
            'other_charges_data': json.dumps([
                {'description': f'Charge {index}', 'amount': '1'}
                for index in range(21)
            ]),
            'credits': '0',
            'amount_received': '100',
            'payment_method': '',
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'Enter no more than 20 additional charges.', response.data)
        self.assertEqual(VehicleSaleReceipt.query.count(), 0)


if __name__ == '__main__':
    unittest.main()
