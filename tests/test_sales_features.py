import unittest
from datetime import timedelta
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Lead, User, Vehicle, VehicleImage, utcnow
from config import TestingConfig


class SalesFeatureTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        user = User(username='sales-test')
        user.set_password('test-password')
        self.vehicle = Vehicle(
            year=2020, make='Honda', model='Civic', slug='sales-civic',
            price=15000, mileage=40000, body_style='Sedan',
            description='Good car', engine='2.0L', transmission='Automatic',
            status='available', created_at=utcnow() - timedelta(days=61),
        )
        db.session.add_all([user, self.vehicle])
        db.session.flush()
        db.session.add(VehicleImage(vehicle_id=self.vehicle.id, filename='civic.jpg'))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def login(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            self.client.post('/admin/login', data={
                'username': 'sales-test', 'password': 'test-password',
            })

    def test_find_fit_uses_existing_inventory_filters_and_sale_price(self):
        db.session.add_all([
            Vehicle(year=2021, make='Toyota', model='RAV4', slug='fit-suv',
                    price=25000, sale_price=19000, mileage=45000, body_style='SUV'),
            Vehicle(year=2021, make='Ford', model='Escape', slug='fit-expensive',
                    price=30000, mileage=45000, body_style='SUV'),
        ])
        db.session.commit()
        response = self.client.get('/inventory?body_style=SUV&max_price=20000&max_mileage=50000')
        html = BeautifulSoup(response.data, 'html.parser')
        form = html.select_one('form:has(#fit-body)')
        self.assertEqual(form['action'], '/inventory')
        self.assertEqual(form.select_one('#fit-budget')['value'], '20000')
        self.assertEqual(form.select_one('#fit-body option[selected]')['value'], 'SUV')
        self.assertIn('RAV4', response.text)
        self.assertNotIn('Escape', response.text)
        self.assertNotIn('Civic', response.text)

    def test_share_link_is_canonical_and_available_without_javascript(self):
        self.app.config['SITE_URL'] = 'https://example.com/'
        response = self.client.get('/inventory/sales-civic?utm_source=private')
        html = BeautifulSoup(response.data, 'html.parser')
        link = html.select_one('#vehicle-share-url')
        self.assertTrue(link.has_attr('readonly'))
        self.assertEqual(link['value'], 'https://example.com/inventory/sales-civic')
        self.assertNotIn('utm_source', link['value'])
        self.assertIn('Select and copy', html.select_one('#vehicle-share-status').text)
        self.assertIsNotNone(html.select_one('script[src*="vehicle-share.js"]'))

    def test_priorities_are_private_and_exclude_spam_and_nonpublic_inventory(self):
        db.session.add_all([
            Lead(name='Old unread', email='old@example.com', created_at=utcnow() - timedelta(days=4)),
            Lead(name='New unread', email='new@example.com'),
            Lead(name='Read lead', email='read@example.com', is_read=True),
            Lead(name='Spam lead', email='spam@example.com', is_spam=True),
            Vehicle(year=2020, make='Secret', model='Sold', slug='sold-hidden',
                    price=1, mileage=1, status='sold', created_at=utcnow() - timedelta(days=90)),
            Vehicle(year=2020, make='Secret', model='Soon', slug='soon-hidden',
                    price=1, mileage=1, status='available', is_coming_soon=True,
                    created_at=utcnow() - timedelta(days=90)),
        ])
        db.session.commit()
        self.assertEqual(self.client.get('/admin/sales-priorities').status_code, 302)
        self.login()
        response = self.client.get('/admin/sales-priorities')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertLess(response.text.index('Old unread'), response.text.index('New unread'))
        self.assertNotIn('Read lead', response.text)
        self.assertNotIn('Spam lead', response.text)
        self.assertNotIn('Secret Sold', response.text)
        self.assertNotIn('Secret Soon', response.text)
        html = BeautifulSoup(response.data, 'html.parser')
        self.assertIn('Honda Civic', response.text)
        self.assertNotIn('Honda Civic', html.select_one('#listing-quality').text)
        self.assertIn('60 days', response.text)

    def test_quality_checklist_paginates_and_updates_after_edit(self):
        for index in range(26):
            db.session.add(Vehicle(
                year=2020, make='Review', model=f'Car {index:02}', slug=f'review-{index}',
                price=10000, mileage=50000, description=' \t\n',
                created_at=utcnow() - timedelta(days=30) + timedelta(seconds=index),
            ))
        db.session.commit()
        self.login()
        first = BeautifulSoup(self.client.get('/admin/sales-priorities').data, 'html.parser')
        self.assertEqual(len(first.select('#listing-quality tbody tr')), 25)
        self.assertIn('No photos, Description, Body style, Engine, Transmission', first.text)
        second = BeautifulSoup(self.client.get('/admin/sales-priorities?page=2').data, 'html.parser')
        self.assertEqual(len(second.select('#listing-quality tbody tr')), 1)
        self.assertIn('Car 25', second.select_one('#listing-quality').text)
        self.assertEqual(self.client.get('/admin/sales-priorities?page=3').status_code, 404)
        self.vehicle.description = None
        db.session.commit()
        updated = self.client.get('/admin/sales-priorities').text
        self.assertIn('Honda Civic', BeautifulSoup(updated, 'html.parser').select_one('#listing-quality').text)


if __name__ == '__main__':
    unittest.main()
