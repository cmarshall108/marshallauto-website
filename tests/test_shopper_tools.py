import unittest
from datetime import timedelta
from decimal import Decimal

from app import create_app, db
from app.models import InventoryAlert, TestDrive, Vehicle, VehicleHoldRequest, utcnow
from app.platform import rate_limit_exceeded
from app.pricing import highway_use_tax, monthly_payment
from app.shopper import confirm_hold, create_hold_request, create_inventory_alert, process_inventory_alerts
from config import TestingConfig


class ShopperToolTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(
            slug='shopper-civic', year=2019, make='Honda', model='Civic',
            body_style='Sedan', price=10000, mileage=50000, status='available',
        )
        self.other = Vehicle(
            slug='shopper-accord', year=2018, make='Honda', model='Accord',
            body_style='Sedan', price=12000, mileage=60000, status='available',
        )
        db.session.add_all([self.vehicle, self.other])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_north_carolina_tax_helper_is_three_percent(self):
        self.assertEqual(highway_use_tax(10000), Decimal('300.00'))
        self.assertEqual(highway_use_tax(100000), Decimal('3000.00'))

    def test_payment_example_is_an_estimate(self):
        self.assertEqual(monthly_payment(10000, 0, 50), Decimal('200.00'))

    def test_vehicle_page_shows_estimate_and_request_tools(self):
        response = self.client.get('/inventory/shopper-civic')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn('The advertised price is your out-the-door price.', text)
        self.assertIn('does not add dealer, documentation, or other extra fees', text)
        self.assertNotIn('Estimated out the door', text)
        self.assertNotIn('not an out-the-door price', text)
        self.assertIn('Request a test drive', text)
        self.assertIn('72-hour hold — in person only', text)
        self.assertIn("cashier", text)
        self.assertNotIn('Request a 72-hour hold', text)
        self.assertIn('not credit offers', text)
        self.assertIn('Before you decide', text)

    def test_hold_request_does_not_reserve_until_staff_confirms(self):
        lead, hold = create_hold_request(
            self.vehicle, name='Pat Buyer', email='pat@example.com', phone='9195550101',
            deposit_amount=Decimal('500.00'), acknowledged=True,
        )
        self.assertEqual(hold.status, 'requested')
        self.assertIsNone(hold.expires_at)
        self.assertEqual(lead.source, 'hold_request')
        confirm_hold(hold)
        self.assertEqual(hold.status, 'active')
        self.assertLess(hold.expires_at - utcnow(), timedelta(hours=72, seconds=5))

    def test_test_drive_request_is_not_checked_out(self):
        from app.shopper import appointment_slots, create_test_drive_request
        slot = appointment_slots()[0]
        create_test_drive_request(
            self.vehicle, name='Pat Buyer', email='pat@example.com', phone='9195550101',
            appointment_at=slot,
        )
        drive = TestDrive.query.one()
        self.assertEqual(drive.status, 'requested')
        self.assertNotEqual(drive.status, 'out')

    def test_price_drop_alert_matches_once(self):
        create_inventory_alert(
            self.vehicle, name='Pat Buyer', email='pat@example.com', phone='9195550101',
            alert_type='vehicle',
        )
        alert = InventoryAlert.query.one()
        alert.last_seen_price = Decimal('10000.00')
        self.vehicle.sale_price = Decimal('9000.00')
        db.session.commit()
        self.assertEqual(process_inventory_alerts(), 1)
        self.assertEqual(process_inventory_alerts(), 0)

    def test_rate_limit_works_without_redis(self):
        self.assertFalse(rate_limit_exceeded('shopper-test', 1, 60))
        self.assertTrue(rate_limit_exceeded('shopper-test', 1, 60))

    def test_public_hold_post_does_not_create_request(self):
        response = self.client.post('/inventory/shopper-civic/hold', data={
            'name': 'Pat Buyer',
            'email': 'pat@example.com',
            'phone': '9195550101',
            'deposit_amount': '500.00',
            'acknowledge_terms': 'y',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VehicleHoldRequest.query.count(), 0)
        self.assertIn('in person', response.get_data(as_text=True))
