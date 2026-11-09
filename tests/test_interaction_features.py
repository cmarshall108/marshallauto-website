import unittest
from decimal import Decimal

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import InventoryAlert, Review, Vehicle
from app.shopper import create_inventory_alert, process_inventory_alerts, vehicle_interest_message
from config import TestingConfig


class InteractionFeatureTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.vehicle = Vehicle(
            slug='interact-camry', year=2019, make='Toyota', model='Camry',
            body_style='Sedan', price=12000, mileage=40000, status='available',
            stock_number='T100', title_status='clean',
        )
        db.session.add(self.vehicle)
        db.session.add(Review(
            author_name='Alex Buyer', rating=5,
            content='Fair price and honest about the title history.',
            is_approved=True, is_featured=True,
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_vehicle_page_has_prefilled_ctas_and_reviews(self):
        html = BeautifulSoup(self.client.get('/inventory/interact-camry').data, 'html.parser')
        chat = html.select_one('.js-open-chat')
        self.assertIsNotNone(chat)
        self.assertIn('Toyota Camry', chat['data-chat-message'])
        self.assertIn('Stock #T100', chat['data-chat-message'])
        self.assertIsNotNone(html.select_one('a[href*="sms:"][href*="Toyota"]'))
        self.assertIsNotNone(html.select_one('.vehicle-trust-reviews'))
        self.assertIn('Alex Buyer', html.get_text())
        message = html.select_one('#vd-message')
        self.assertIsNotNone(message)
        self.assertIn('interested', message.text.lower())

    def test_interest_message_helper(self):
        msg = vehicle_interest_message(self.vehicle)
        self.assertIn('2019 Toyota Camry', msg)
        self.assertIn('T100', msg)
        self.assertIn('12000', msg.replace(',', ''))

    def test_inventory_search_alert_form_and_create(self):
        page = BeautifulSoup(self.client.get('/inventory?make=Toyota&max_price=15000').data, 'html.parser')
        panel = page.select_one('details#inventory-search-alert')
        self.assertIsNotNone(panel)
        self.assertFalse(panel.has_attr('open'))
        self.assertEqual(panel.select_one(':scope > summary').get_text(strip=True), 'Notify me about new matches')
        form = page.select_one('#inventory-search-alert form')
        self.assertIsNotNone(form)
        self.assertIsNotNone(form.select_one('#search-alert-name'))
        self.assertEqual(form.find_parent('details'), panel)
        self.assertEqual(form.select_one('input[name="make"]')['value'], 'Toyota')
        response = self.client.post('/inventory/alerts/search', data={
            'name': 'Pat Shopper',
            'email': 'pat@example.com',
            'phone': '',
            'make': 'Toyota',
            'max_price': '15000',
            'title_status': '',
            'honeypot': '',
            'next': '/inventory?make=Toyota',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'email you', response.data.lower())
        alert = InventoryAlert.query.one()
        self.assertEqual(alert.alert_type, 'search')
        self.assertEqual(alert.make, 'Toyota')
        self.assertEqual(alert.max_price, Decimal('15000.00'))

    def test_search_alert_matches_new_vehicle(self):
        create_inventory_alert(
            None, name='Pat', email='pat@example.com', phone=None,
            alert_type='search', make='Honda', max_price=Decimal('20000'),
        )
        # Existing Camry should not match Honda search.
        self.assertEqual(process_inventory_alerts(), 0)
        db.session.add(Vehicle(
            slug='new-honda', year=2018, make='Honda', model='Civic',
            body_style='Sedan', price=11000, mileage=30000, status='available',
        ))
        db.session.commit()
        self.assertEqual(process_inventory_alerts(), 1)


if __name__ == '__main__':
    unittest.main()
