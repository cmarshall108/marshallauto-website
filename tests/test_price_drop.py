import unittest
from decimal import Decimal

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle
from config import TestingConfig


class PriceDropTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def add_vehicle(self, **overrides):
        values = dict(
            year=2019,
            make='Honda',
            model='Civic',
            price=Decimal('15000'),
            mileage=42000,
            status='available',
            slug='2019-honda-civic',
        )
        values.update(overrides)
        vehicle = Vehicle(**values)
        db.session.add(vehicle)
        db.session.commit()
        return vehicle

    def test_price_reduction_keeps_highest_original_until_price_recovers(self):
        vehicle = self.add_vehicle()

        vehicle.price = Decimal('12000')
        vehicle.note_price_change(Decimal('15000'))
        db.session.commit()

        info = vehicle.price_drop_info
        self.assertEqual(info['original'], Decimal('15000'))
        self.assertEqual(info['price'], Decimal('12000'))
        self.assertEqual(info['savings'], Decimal('3000'))
        self.assertEqual(info['percent'], 20)
        self.assertIsNotNone(vehicle.price_dropped_at)

        vehicle.price = Decimal('11000')
        vehicle.note_price_change(Decimal('12000'))
        self.assertEqual(vehicle.previous_price, Decimal('15000'))

        vehicle.price = Decimal('15000')
        vehicle.note_price_change(Decimal('11000'))
        self.assertIsNone(vehicle.previous_price)
        self.assertIsNone(vehicle.price_dropped_at)
        self.assertIsNone(vehicle.price_drop_info)

    def test_sale_price_below_list_shows_without_stored_previous_price(self):
        vehicle = self.add_vehicle(sale_price=Decimal('12500'))

        info = vehicle.price_drop_info
        self.assertEqual(info['original'], Decimal('15000'))
        self.assertEqual(info['savings'], Decimal('2500'))

    def test_sold_listing_does_not_advertise_a_price_drop(self):
        vehicle = self.add_vehicle(
            status='sold',
            previous_price=Decimal('15000'),
            price=Decimal('12000'),
        )

        self.assertIsNone(vehicle.price_drop_info)

    def test_public_pages_strike_original_and_feature_new_price(self):
        vehicle = self.add_vehicle(previous_price=Decimal('15000'), price=Decimal('12000'))

        inventory = self.client.get('/inventory')
        self.assertEqual(inventory.status_code, 200)
        card = BeautifulSoup(inventory.data, 'html.parser').select_one('.price-drop-deal')
        self.assertIn('Price Drop', card.get_text(' ', strip=True))
        self.assertIn('Save $3,000', card.get_text(' ', strip=True))
        struck = BeautifulSoup(inventory.data, 'html.parser').select_one('.price-drop-was s')
        self.assertEqual(struck.get_text(strip=True), '$15,000')
        self.assertIn('$12,000', BeautifulSoup(inventory.data, 'html.parser').select_one('.price-drop-now').get_text())

        detail = self.client.get(f'/inventory/{vehicle.slug}')
        self.assertEqual(detail.status_code, 200)
        page = BeautifulSoup(detail.data, 'html.parser')
        self.assertIsNotNone(page.select_one('.price-drop-deal-lg'))
        self.assertEqual(page.select_one('.price-drop-was s').get_text(strip=True), '$15,000')
        self.assertEqual(page.select_one('.vehicle-price .price-drop-now').get_text(strip=True), '$12,000')
        self.assertIsNone(page.select_one('.vehicle-price[itemprop="offers"]'))
