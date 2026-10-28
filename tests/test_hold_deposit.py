import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import User, Vehicle
from config import TestingConfig


class HoldDepositTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='hold-admin')
        user.set_password('hold-test-password')
        db.session.add_all([
            user,
            Vehicle(
                slug='hold-camry', year=2018, make='Toyota', model='Camry',
                trim='SE', vin='4T1BF1FK5JU000001', stock_number='H100',
                body_style='Sedan', title_status='rebuilt',
                price=12900, sale_price=11900, mileage=82000, status='available',
            ),
        ])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': 'hold-admin',
                'password': 'hold-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def test_hold_deposit_requires_admin_login(self):
        response = self.client.get('/admin/vehicles/1/hold-deposit')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.headers['Location'])

    def test_print_page_prefills_vehicle_and_states_72_hour_terms(self):
        self.login()
        response = self.client.get('/admin/vehicles/1/hold-deposit')
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.data, 'html.parser')
        text = ' '.join(html.get_text(' ', strip=True).split())

        self.assertIn('Vehicle Hold Deposit Agreement', text)
        self.assertIn('not a bill of sale and not a contract to buy', text)
        self.assertIn('Marshall Auto, LLC', text)
        self.assertIn('360 Wilson Rd, Sanford, NC 27332', text)
        self.assertIn('Lee County, North Carolina', text)
        self.assertIn('(919)-215-0702', text)
        self.assertIn('Year 2018', text)
        self.assertIn('Make Toyota', text)
        self.assertIn('Model Camry', text)
        self.assertIn('VIN 4T1BF1FK5JU000001', text)
        self.assertIn('Stock # H100', text)
        self.assertIn('82,000 mi', text)
        self.assertIn('Rebuilt', text)
        self.assertIn('$11,900.00', text)
        self.assertIn('not a locked sale price', text)
        self.assertIn('exactly 72 hours', text)
        self.assertIn('72 hours is the maximum hold', text)
        self.assertIn('physically present', text)
        self.assertIn('cashier’s check', text)
        self.assertIn('original receipt from the issuing bank branch', text)
        self.assertIn('No other form of payment is accepted', text)
        self.assertNotIn('paid by cash card', text.lower())
        self.assertIn('no authority to extend it', text)
        self.assertIn('does not buy the vehicle', text)
        self.assertIn('as is', text)
        self.assertIn('Dealer earns and may keep the entire deposit', text)
        self.assertIn('return the full deposit', text)
        self.assertIn('five (5) business days', text)
        self.assertIn('North Carolina law governs', text)
        self.assertIn('Lee County, North Carolina', text)
        self.assertIn('at least 18 years old', text)
        self.assertEqual(text.count('Signature'), 1)
        self.assertIn('Authorized signature', text)
        self.assertIsNotNone(html.select_one('button[onclick="window.print()"]'))
        logo = html.select_one('img.hold-logo')
        self.assertIsNotNone(logo)
        self.assertIn('images/dealership-logo.jpg', logo.get('src', ''))

        edit = self.client.get('/admin/vehicles/1/edit')
        edit_html = BeautifulSoup(edit.data, 'html.parser')
        button = edit_html.select_one('a[href="/admin/vehicles/1/hold-deposit"]')
        self.assertIsNotNone(button)
        self.assertIn('Print Hold Deposit', button.get_text())

        listing = self.client.get('/admin/vehicles')
        listing_html = BeautifulSoup(listing.data, 'html.parser')
        list_button = listing_html.select_one('a[href="/admin/vehicles/1/hold-deposit"]')
        self.assertIsNotNone(list_button)
        self.assertIn('Hold Deposit', list_button.get_text())
