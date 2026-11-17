import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from bs4 import BeautifulSoup
from flask.globals import _cv_app

from app import create_app, db
from app.models import User, Vehicle, VehicleSaleReceipt
from app.pricing import split_otd_sale_price
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
            sold_price=18000, salesperson='Alex Seller', mileage=82000, status='sold',
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
        self.assertIn(b'bill-of-sale price', editor.data)
        self.assertIn(b'value="18000.00"', editor.data)
        self.assertIn(b'$520.98', editor.data)
        self.assertIn(b'$17,366.02', editor.data)
        self.assertIn(b'$113.00', editor.data)
        self.assertIn(b'value="Alex Seller"', editor.data)

        response = self.client.post(self.path, data={
            'buyer_name': 'Jordan Customer',
            'buyer_address': '123 Main Street, Sanford, NC',
            'salesman': 'Alex Seller',
            'otd_price': '18000.00',
            'document_fee': '150.00',
            'warranty_fee': '250.00',
            'tags_fee': '200.00',
            'other_charges_data': json.dumps([
                {'description': 'Inspection', 'amount': '75.00'},
            ]),
            'down_payment': '1000.00',
            'trade_allow': '500.00',
            'payment_method': 'cashiers_check',
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/sale/receipt/print', response.headers['Location'])

        receipt = VehicleSaleReceipt.query.one()
        self.assertEqual(receipt.vehicle_id, self.vehicle.id)
        self.assertEqual(receipt.salesman, 'Alex Seller')
        self.assertEqual(receipt.vehicle_price, Decimal('17366.02'))
        self.assertEqual(receipt.sales_tax, Decimal('520.98'))
        self.assertEqual(receipt.title_registration_fee, Decimal('113.00'))
        self.assertEqual(receipt.cash_due, Decimal('17175.00'))
        self.assertEqual(receipt.charges[0].description, 'Inspection')

        printed = self.client.get(response.headers['Location'])
        self.assertEqual(printed.status_code, 200)
        soup = BeautifulSoup(printed.data, 'html.parser')
        text = ' '.join(soup.get_text(' ', strip=True).split())
        self.assertIn('Vehicle Sale Receipt', text)
        self.assertIn('Receipt #:', text)
        self.assertIn('Jordan Customer', text)
        self.assertIn('Salesman: Alex Seller', text)
        self.assertIn('Bill of Sale Price', text)
        self.assertIn('Sales Tax', text)
        self.assertIn('OTD sale price:', text)
        self.assertIn('$18,000.00', text)
        self.assertIn('$520.98', text)
        self.assertIn('$17,366.02', text)
        self.assertIn('Title & Plate Fees', text)
        self.assertIn('$113.00', text)
        self.assertIn('Doc Fee', text)
        self.assertIn('Warranty Fee', text)
        self.assertIn('Extra Tags Fee', text)
        self.assertIn('Down Payment', text)
        self.assertIn('Trade-Allow', text)
        self.assertIn('Cash Due', text)
        self.assertIn('Inspection', text)
        self.assertIn('$17,175.00', text)
        self.assertIn('sold as-is', text)
        self.assertIn('No liability or collision insurance is included.', text)
        self.assertIn('Title is guaranteed to the purchaser by the seller.', text)
        self.assertIn("Cashier's Check", text)
        self.assertIsNotNone(soup.select_one('button[onclick="window.print()"]'))

        saved = self.client.get(self.path)
        self.assertIn(b'Jordan Customer', saved.data)
        self.assertIn(b'Inspection', saved.data)

        updated = self.client.post(self.path, data={
            'buyer_name': 'Jordan C.',
            'buyer_address': '',
            'salesman': 'Pat Sales',
            'otd_price': '16900.00',
            'document_fee': '150.00',
            'warranty_fee': '0',
            'tags_fee': '200.00',
            'other_charges_data': json.dumps([
                {'description': 'Dealer preparation', 'amount': '90.00'},
            ]),
            'down_payment': '500.00',
            'trade_allow': '0',
            'payment_method': 'cash',
        })
        self.assertEqual(updated.status_code, 302)
        self.assertEqual(VehicleSaleReceipt.query.count(), 1)
        self.assertEqual(receipt.vehicle_price, Decimal('16298.06'))
        self.assertEqual(receipt.sales_tax, Decimal('488.94'))
        self.assertEqual(receipt.title_registration_fee, Decimal('113.00'))
        self.assertEqual(receipt.cash_due, Decimal('16840.00'))
        self.assertEqual(receipt.salesman, 'Pat Sales')
        self.assertEqual([charge.description for charge in receipt.charges], ['Dealer preparation'])

    def test_rejects_credits_that_exceed_total(self):
        response = self.client.post(self.path, data={
            'otd_price': '100.00',
            'document_fee': '0',
            'warranty_fee': '0',
            'tags_fee': '0',
            'other_charges_data': '[]',
            'down_payment': '80.00',
            'trade_allow': '21.00',
            'payment_method': '',
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'Down payment and trade allowance cannot exceed the itemized charges.', response.data)
        self.assertEqual(VehicleSaleReceipt.query.count(), 0)

    def test_limits_extra_charge_count(self):
        response = self.client.post(self.path, data={
            'otd_price': '100.00',
            'document_fee': '0',
            'warranty_fee': '0',
            'tags_fee': '0',
            'other_charges_data': json.dumps([
                {'description': f'Charge {index}', 'amount': '1'}
                for index in range(21)
            ]),
            'down_payment': '0',
            'trade_allow': '0',
            'payment_method': '',
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'Enter no more than 20 additional charges.', response.data)
        self.assertEqual(VehicleSaleReceipt.query.count(), 0)


class OtdSaleSplitTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        self.ctx.pop()

    def test_sale_price_is_separate_from_tax_and_fees(self):
        split = split_otd_sale_price('18000.00')
        self.assertEqual(split['fees'], Decimal('113.00'))
        self.assertEqual(split['sales_tax'], Decimal('520.98'))
        self.assertEqual(split['bill_of_sale_price'], Decimal('17366.02'))
        self.assertEqual(split['agreed_vehicle_price'], split['bill_of_sale_price'])
        self.assertEqual(
            split['bill_of_sale_price'] + split['sales_tax'] + split['fees'],
            split['otd_price'],
        )

        below_fees = split_otd_sale_price('100.50')
        self.assertEqual(below_fees['bill_of_sale_price'], Decimal('100.50'))
        self.assertEqual(below_fees['sales_tax'], Decimal('0.00'))
        self.assertEqual(below_fees['fees'], Decimal('0.00'))


if __name__ == '__main__':
    unittest.main()
