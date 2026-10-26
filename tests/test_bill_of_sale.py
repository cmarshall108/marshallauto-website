import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import User, Vehicle
from config import TestingConfig


class BillOfSaleTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='bos-admin')
        user.set_password('bos-test-password')
        db.session.add_all([
            user,
            Vehicle(
                slug='bos-camry', year=2018, make='Toyota', model='Camry',
                trim='SE', vin='4T1BF1FK5JU000001', body_style='Sedan',
                price=12900, sale_price=11900, sold_price=10500,
                mileage=82000, status='sold',
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
                'username': 'bos-admin',
                'password': 'bos-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def test_bill_of_sale_requires_admin_login(self):
        response = self.client.get('/admin/vehicles/1/bill-of-sale')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.headers['Location'])

    def test_print_page_reproduces_blank_form_and_prefills_vehicle(self):
        self.login()
        response = self.client.get('/admin/vehicles/1/bill-of-sale')
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.data, 'html.parser')
        text = ' '.join(html.get_text(' ', strip=True).split())

        self.assertIn('NORTH CAROLINA MOTOR VEHICLE BILL OF SALE', text)
        self.assertIn('(Sold "As-Is" without Warranty)', text)
        self.assertIn('STATE OF: North Carolina', text)
        self.assertIn('COUNTY OF: Lee', text)
        self.assertIn('KNOW ALL PERSONS BY THESE PRESENTS:', text)
        self.assertIn('WARRANTY OF OWNERSHIP.', text)
        self.assertIn('DISCLAIMER OF ALL OTHER WARRANTIES.', text)
        self.assertIn('ADDITIONAL TERMS OF SALE.', text)
        self.assertIn('his/her successors and assigns', text)
        self.assertIn('Marshall Auto, LLC', text)
        self.assertIn('360 Wilson Rd, Sanford, NC 27332', text)
        self.assertIn('$10,500.00', text)
        self.assertIn('Make: Toyota', text)
        self.assertIn('Model: Camry', text)
        self.assertIn('Body Type: Sedan', text)
        self.assertIn('Year: 2018', text)
        self.assertIn('VIN: 4T1BF1FK5JU000001', text)
        self.assertIn('Printed Name', text)
        self.assertIn('Date', text)
        self.assertEqual(text.count('Signature'), 2)
        self.assertIsNotNone(html.select_one('button[onclick="window.print()"]'))
        logo = html.select_one('img.bos-logo')
        self.assertIsNotNone(logo)
        self.assertIn('images/dealership-logo.jpg', logo.get('src', ''))
        self.assertEqual(logo.get('alt'), 'Marshall Auto')

        edit = self.client.get('/admin/vehicles/1/edit')
        edit_html = BeautifulSoup(edit.data, 'html.parser')
        button = edit_html.select_one('a[href="/admin/vehicles/1/bill-of-sale"]')
        self.assertIsNotNone(button)
        self.assertIn('Print Bill of Sale', button.get_text())

        listing = self.client.get('/admin/vehicles')
        listing_html = BeautifulSoup(listing.data, 'html.parser')
        list_button = listing_html.select_one('a[href="/admin/vehicles/1/bill-of-sale"]')
        self.assertIsNotNone(list_button)
        self.assertIn('Bill of Sale', list_button.get_text())
