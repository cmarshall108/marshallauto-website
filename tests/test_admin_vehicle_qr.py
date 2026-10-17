import base64
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import User, Vehicle
from config import TestingConfig


class AdminVehicleQrTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['SITE_URL'] = 'https://marshallautosanford.com'
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='qr-agent')
        user.set_password('qr-test-password')
        db.session.add_all([
            user,
            Vehicle(
                slug='qr-test-car', year=2022, make='Toyota', model='Camry',
                price=18900, sale_price=17900, mileage=42000,
                status='available',
            ),
            Vehicle(
                slug='qr-coming-soon', year=2021, make='Honda', model='Accord',
                price=16500, mileage=51000, status='available',
                is_coming_soon=True,
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
                'username': 'qr-agent',
                'password': 'qr-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def test_qr_print_requires_admin_login(self):
        response = self.client.get('/admin/vehicles/1/qr-print')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.headers['Location'])

    def test_print_sheet_contains_current_public_url_price_and_valid_qr_svg(self):
        self.login()
        response = self.client.get('/admin/vehicles/1/qr-print')
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.data, 'html.parser')

        self.assertIn('2022 Toyota Camry', html.select_one('h1').get_text())
        self.assertEqual(html.select_one('.qr-price').get_text(strip=True), '$17,900')
        self.assertIn('Website price', html.get_text())
        self.assertIn('https://marshallautosanford.com/inventory/qr-test-car',
                      html.select_one('.qr-url').get_text())
        self.assertIsNotNone(html.select_one('button[onclick="window.print()"]'))

        image = html.select_one('.qr-code-image')
        self.assertTrue(image['src'].startswith('data:image/svg+xml;base64,'))
        svg_data = base64.b64decode(image['src'].split(',', 1)[1])
        self.assertEqual(ET.fromstring(svg_data).tag.rsplit('}', 1)[-1], 'svg')

    def test_coming_soon_qr_links_to_its_public_card_without_exposing_price(self):
        self.login()
        response = self.client.get('/admin/vehicles/2/qr-print')
        html = BeautifulSoup(response.data, 'html.parser')
        self.assertIsNone(html.select_one('.qr-price'))
        self.assertIn('Price and full details will be posted when this vehicle is ready.',
                      html.get_text())
        self.assertIn(
            'https://marshallautosanford.com/coming-soon#vehicle-qr-coming-soon',
            html.select_one('.qr-url').get_text(),
        )

        public = self.client.get('/coming-soon')
        public_html = BeautifulSoup(public.data, 'html.parser')
        vehicle_card = public_html.select_one('#vehicle-qr-coming-soon')
        self.assertIsNotNone(vehicle_card)
        self.assertNotIn('$16,500', vehicle_card.get_text())

    def test_vehicle_admin_surfaces_link_to_qr_print(self):
        self.login()
        inventory = BeautifulSoup(self.client.get('/admin/vehicles').data, 'html.parser')
        self.assertIsNotNone(inventory.select_one('a[href="/admin/vehicles/1/qr-print"]'))
        edit = BeautifulSoup(self.client.get('/admin/vehicles/1/edit').data, 'html.parser')
        self.assertIsNotNone(edit.select_one('a[href="/admin/vehicles/1/qr-print"]'))


if __name__ == '__main__':
    unittest.main()
