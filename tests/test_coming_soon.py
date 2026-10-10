import unittest
from datetime import datetime

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle
from config import TestingConfig


class ComingSoonTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['SITE_URL'] = 'https://dealer.example'
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.teaser = self.add_vehicle(
            '2020-Toyota-RAV4-coming-soon',
            is_coming_soon=True,
            price=24800,
        )
        self.available = self.add_vehicle(
            '2018-Honda-Accord-available',
            is_coming_soon=False,
            price=15900,
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def add_vehicle(self, slug, *, is_coming_soon, price):
        vehicle = Vehicle(
            year=2020 if is_coming_soon else 2018,
            make='Toyota' if is_coming_soon else 'Honda',
            model='RAV4' if is_coming_soon else 'Accord',
            price=price,
            mileage=60000,
            status='available',
            is_coming_soon=is_coming_soon,
            slug=slug,
            description='Fresh arrival being prepared for sale.',
            created_at=datetime(2026, 9, 1, 10),
            updated_at=datetime(2026, 9, 20, 14, 30),
        )
        db.session.add(vehicle)
        return vehicle

    def test_coming_soon_page_shows_teaser_without_price(self):
        response = self.client.get('/coming-soon')
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.data, 'html.parser')
        text = page.get_text(' ', strip=True)

        self.assertIn(self.teaser.title, text)
        self.assertIn('Coming Soon', text)
        self.assertNotIn('24,800', text)
        self.assertNotIn('60,000 miles', text)
        self.assertIsNotNone(
            page.select_one(f'a[href="/contact?vehicle_id={self.teaser.id}"]')
        )
        self.assertNotIn(self.available.title, text)

    def test_teaser_is_not_in_sale_inventory_homepage_or_feed(self):
        inventory = BeautifulSoup(self.client.get('/inventory').data, 'html.parser')
        home = BeautifulSoup(self.client.get('/').data, 'html.parser')
        feed = self.client.get('/feeds/vehicles.xml').get_data(as_text=True)

        self.assertIsNone(inventory.select_one(f'a[href="/inventory/{self.teaser.slug}"]'))
        self.assertIsNotNone(inventory.select_one(f'a[href="/inventory/{self.available.slug}"]'))
        self.assertIn(self.teaser.title, home.get_text())
        self.assertNotIn(self.teaser.slug, feed)
        self.assertIn(self.available.slug, feed)
        self.assertEqual(self.client.get(f'/inventory/{self.teaser.slug}').status_code, 404)

    def test_contact_link_prefills_the_vehicle_interest(self):
        response = self.client.get(f'/contact?vehicle_id={self.teaser.id}')
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.data, 'html.parser')

        self.assertEqual(
            page.select_one('input[name="vehicle_id"]')['value'],
            str(self.teaser.id),
        )
        self.assertIn(self.teaser.title, page.select_one('textarea[name="message"]').text)

    def test_sitemap_advertises_page_not_unlisted_teaser_detail(self):
        response = self.client.get('/sitemap.xml').get_data(as_text=True)
        self.assertIn('https://dealer.example/coming-soon', response)
        self.assertNotIn(f'https://dealer.example/inventory/{self.teaser.slug}', response)
        self.assertIn(f'https://dealer.example/inventory/{self.available.slug}', response)


if __name__ == '__main__':
    unittest.main()
