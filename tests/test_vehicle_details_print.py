import base64
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import CarfaxReport, ServiceRecord, User, Vehicle
from config import TestingConfig


class VehicleDetailsPrintTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['SITE_URL'] = 'https://marshallautosanford.com'
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='print-agent')
        user.set_password('print-test-password')
        available = Vehicle(
            slug='print-test-car',
            year=2020,
            make='Honda',
            model='Civic',
            trim='EX',
            vin='1HGBH41JXMN109186',
            stock_number='MA-100',
            price=Decimal('15000.00'),
            sale_price=Decimal('13950.00'),
            mileage=32100,
            condition='used',
            title_status='clean',
            status='available',
            exterior_color='Blue',
            interior_color='Black',
            engine='1.5L I4',
            transmission='CVT',
            drivetrain='FWD',
            fuel_type='Gasoline',
            mpg_city=32,
            mpg_highway=42,
            doors=4,
            description='One owner, clean history.',
            features='Bluetooth, Backup Camera',
            exterior_features='Alloy Wheels\nSunroof',
            additional_specifications='Curb Weight: 2900 lbs',
            acquisition_cost=Decimal('9000.00'),
            sale_notes='Private buyer note',
            sold_price=Decimal('14000.00'),
        )
        coming_soon = Vehicle(
            slug='print-coming-soon',
            year=2021,
            make='Toyota',
            model='Camry',
            price=Decimal('16500.00'),
            mileage=18000,
            status='available',
            is_coming_soon=True,
            acquisition_cost=Decimal('11000.00'),
        )
        db.session.add_all([user, available, coming_soon])
        db.session.flush()
        db.session.add(ServiceRecord(
            vehicle_id=available.id,
            service_date=date(2025, 6, 1),
            mileage_at_service=30000,
            service_type='Oil change',
            provider='Marshall Auto',
        ))
        db.session.add(CarfaxReport(
            vehicle_id=available.id,
            report_date=date(2025, 5, 1),
            accidents_reported=0,
            owners_reported=1,
            summary='No accidents reported.',
        ))
        db.session.commit()
        self.client = self.app.test_client()
        self.available_id = available.id
        self.coming_soon_id = coming_soon.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': 'print-agent',
                'password': 'print-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def test_public_sheet_includes_price_and_hides_internal_fields(self):
        response = self.client.get('/inventory/print-test-car/print')
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.data, 'html.parser')
        text = html.get_text(' ', strip=True)

        self.assertIn('2020 Honda Civic EX', html.select_one('h1').get_text())
        self.assertIn('$13,950', text)
        self.assertIn('$15,000', text)
        self.assertIn('32,100 mi', text)
        self.assertIn('1HGBH41JXMN109186', text)
        self.assertIn('MA-100', text)
        self.assertIn('Blue', text)
        self.assertIn('1.5L I4', text)
        self.assertIn('One owner, clean history.', text)
        self.assertIn('Bluetooth', text)
        self.assertIn('Alloy Wheels', text)
        self.assertIn('Curb Weight', text)
        self.assertIn('2900 lbs', text)
        self.assertIn('Oil change', text)
        self.assertIn('No accidents reported.', text)
        self.assertIn('out-the-door price', text)
        self.assertNotIn('9000', text)
        self.assertNotIn('Private buyer note', text)
        self.assertNotIn('$14,000', text)
        self.assertIsNotNone(html.select_one('button[onclick="window.print()"]'))

        image = html.select_one('img.qr')
        self.assertTrue(image['src'].startswith('data:image/svg+xml;base64,'))
        svg_data = base64.b64decode(image['src'].split(',', 1)[1])
        self.assertEqual(ET.fromstring(svg_data).tag.rsplit('}', 1)[-1], 'svg')

        listing = BeautifulSoup(self.client.get('/inventory/print-test-car').data, 'html.parser')
        self.assertIsNotNone(listing.select_one('a[href="/inventory/print-test-car/print"]'))

    def test_coming_soon_public_sheet_is_hidden_but_staff_sheet_shows_price(self):
        public = self.client.get('/inventory/print-coming-soon/print')
        self.assertEqual(public.status_code, 404)

        admin = self.client.get(f'/admin/vehicles/{self.coming_soon_id}/details-print')
        self.assertEqual(admin.status_code, 302)
        self.assertIn('/admin/login', admin.headers['Location'])

        self.login()
        response = self.client.get(f'/admin/vehicles/{self.coming_soon_id}/details-print')
        self.assertEqual(response.status_code, 200)
        text = BeautifulSoup(response.data, 'html.parser').get_text(' ', strip=True)
        self.assertIn('$16,500', text)
        self.assertIn('not shown on the public website yet', text)
        self.assertNotIn('11000', text)

    def test_admin_surfaces_link_to_details_sheet(self):
        self.login()
        inventory = BeautifulSoup(self.client.get('/admin/vehicles').data, 'html.parser')
        self.assertIsNotNone(inventory.select_one(f'a[href="/admin/vehicles/{self.available_id}/details-print"]'))
        edit = BeautifulSoup(self.client.get(f'/admin/vehicles/{self.available_id}/edit').data, 'html.parser')
        self.assertIsNotNone(edit.select_one(f'a[href="/admin/vehicles/{self.available_id}/details-print"]'))
