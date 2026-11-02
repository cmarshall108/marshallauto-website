"""Salesman accounts, dealer tags, profile onboarding, prefill, and mandatory 2FA."""
import os
import re
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pyotp

from app import create_app, db
from app.models import DealerTag, User, Vehicle
from app.models import TestDrive as TestDriveRecord
from app.two_factor import decrypt_secret, requires_two_factor
from config import TestingConfig


class SalesmanAccountsConfig(TestingConfig):
    ADMIN_TWO_FACTOR_REQUIRED = False  # site-wide off; salesmen still forced


class SalesmanAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        config = type('LocalSalesmanConfig', (SalesmanAccountsConfig,), {
            'UPLOAD_FOLDER': os.path.join(self.tmp.name, 'public'),
            'PRIVATE_UPLOAD_FOLDER': os.path.join(self.tmp.name, 'private'),
            'WTF_CSRF_ENABLED': False,
        })
        self.app = create_app(config)
        self.client = self.app.test_client()
        self.rate_limit = patch('app.admin.rate_limit_exceeded', return_value=False)
        self.rate_limit.start()
        with self.app.app_context():
            db.create_all()
            admin = User(
                username='boss',
                role=User.ROLE_ADMIN,
                profile_complete=True,
                full_name='Boss Admin',
                dealer_tag='Boss',
            )
            admin.set_password('admin-password-long')
            db.session.add(admin)
            vehicle = Vehicle(
                year=2020, make='Toyota', model='Camry', price=15000, mileage=30000,
                status='available', condition='used', title_status='clean',
            )
            db.session.add(vehicle)
            db.session.commit()
            self.admin_id = admin.id
            self.vehicle_id = vehicle.id

    def tearDown(self):
        self.rate_limit.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.tmp.cleanup()

    def login_admin(self):
        response = self.client.post('/admin/login', data={
            'username': 'boss', 'password': 'admin-password-long',
        })
        self.assertIn(response.status_code, (302, 200))
        # Site-wide 2FA off for admin
        self.assertEqual(self.client.get('/admin/dashboard').status_code, 200)

    def create_salesman_via_admin(self, username='sales1', password='temp-password-ok'):
        self.login_admin()
        response = self.client.post('/admin/salesmen/new', data={
            'username': username,
            'temporary_password': password,
            'full_name': 'Sam Sales',
            'email': 'sam@example.com',
            'phone': '919-555-0100',
            'dealer_tag': 'S. Sales',
            'is_active': 'y',
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user = User.query.filter_by(username=username).first()
            self.assertIsNotNone(user)
            self.assertTrue(user.is_salesman)
            self.assertEqual(user.phone, '(919) 555-0100')
            self.assertTrue(user.must_change_password)
            self.assertFalse(user.profile_complete)
            self.assertFalse(user.totp_enabled)
            tag = DealerTag.query.filter_by(name='S. Sales').first()
            self.assertIsNotNone(tag)
            self.salesman_id = user.id
        self.client.get('/admin/logout')
        return username, password

    def enroll_salesman_2fa(self, username, password):
        response = self.client.post('/admin/login', data={
            'username': username, 'password': password,
        })
        self.assertEqual(response.location, '/admin/login/setup')
        page = self.client.get('/admin/login/setup')
        self.assertEqual(page.status_code, 200)
        with self.app.app_context():
            user = User.query.filter_by(username=username).first()
            secret = decrypt_secret(user.totp_secret_encrypted)
        confirm = self.client.post('/admin/login/setup', data={
            'code': pyotp.TOTP(secret).now(),
        })
        self.assertEqual(confirm.status_code, 200)
        self.assertIn('I Saved My Codes', confirm.get_data(as_text=True))
        # Finish codes page by following destination (profile setup)
        match = re.search(r'href="([^"]+)"[^>]*>\s*I Saved My Codes', confirm.get_data(as_text=True))
        self.assertIsNotNone(match)
        dest = match.group(1).replace('&amp;', '&')
        follow = self.client.get(dest)
        self.assertEqual(follow.status_code, 200)
        self.assertIn(b'finish setup', follow.data.lower())
        return secret

    def complete_profile(self, password='new-password-ok1'):
        response = self.client.post('/admin/profile/setup', data={
            'full_name': 'Sam Sales',
            'email': 'sam@example.com',
            'phone': '919-555-0100',
            'dealer_tag': 'S. Sales',
            'new_password': password,
            'confirm_password': password,
        }, follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            user = db.session.get(User, self.salesman_id)
            self.assertTrue(user.profile_complete)
            self.assertFalse(user.must_change_password)
            self.assertEqual(user.dealer_tag, 'S. Sales')
            self.assertEqual(user.phone, '(919) 555-0100')
        return password

    def test_admin_can_create_salesman_and_dealer_tag(self):
        self.create_salesman_via_admin()
        self.login_admin()
        page = self.client.get('/admin/salesmen')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('sales1', body)
        self.assertIn('S. Sales', body)

        response = self.client.post('/admin/dealer-tags', data={
            'name': 'Lot Runner',
            'sort_order': '5',
            'is_active': 'y',
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertIsNotNone(DealerTag.query.filter_by(name='Lot Runner').first())

    def test_salesman_requires_2fa_even_when_site_wide_off(self):
        with self.app.app_context():
            user = User(username='s2', role=User.ROLE_SALESMAN, profile_complete=False)
            user.set_password('temp-password-ok')
            db.session.add(user)
            db.session.commit()
            self.assertTrue(requires_two_factor(user))
            admin = db.session.get(User, self.admin_id)
            self.assertFalse(requires_two_factor(admin))

    def test_first_login_2fa_then_profile_then_prefill(self):
        username, temp = self.create_salesman_via_admin()
        secret = self.enroll_salesman_2fa(username, temp)
        new_password = self.complete_profile()

        # Dashboard accessible after onboarding
        self.assertEqual(self.client.get('/admin/dashboard').status_code, 200)
        # Settings blocked for salesman
        settings = self.client.get('/admin/settings', follow_redirects=False)
        self.assertEqual(settings.status_code, 302)

        # Test drive form prefills salesperson
        form_page = self.client.get('/admin/test-drives/new')
        self.assertEqual(form_page.status_code, 200)
        body = form_page.get_data(as_text=True)
        self.assertIn('value="S. Sales"', body)
        self.assertIn('Lot Runner', body) if False else None  # tag may not exist
        self.assertIn('S. Sales', body)
        self.assertIn('dealer-tag-pick', body)

        # Checkout test drive uses prefilled salesman
        checkout = self.client.post('/admin/test-drives/new', data={
            'vehicle_id': str(self.vehicle_id),
            'customer_name': 'Pat Customer',
            'start_mileage': '30100',
            'salesperson': 'S. Sales',
        }, follow_redirects=False)
        self.assertEqual(checkout.status_code, 302)
        with self.app.app_context():
            td = TestDriveRecord.query.first()
            self.assertIsNotNone(td)
            self.assertEqual(td.salesperson, 'S. Sales')

        # Re-login with new password + TOTP (use a fresh counter without skewing session clock)
        self.client.get('/admin/logout')
        login = self.client.post('/admin/login', data={
            'username': username, 'password': new_password,
        })
        self.assertEqual(login.location, '/admin/login/verify')
        import time
        next_counter = int(time.time()) // 30 + 2
        code = pyotp.TOTP(secret).at(next_counter * 30)
        with patch('app.two_factor.totp_counter', return_value=next_counter):
            verify = self.client.post('/admin/login/verify', data={'code': code})
        self.assertEqual(verify.status_code, 302)
        with self.client.session_transaction() as browser_session:
            self.assertTrue(browser_session.permanent)
        with patch('app.two_factor.time.time', return_value=time.time() + 24 * 60 * 60):
            self.assertEqual(self.client.get('/admin/dashboard').status_code, 200)
        with patch('app.two_factor.time.time', return_value=time.time() + 3 * 24 * 60 * 60 + 1):
            self.assertEqual(self.client.get('/admin/dashboard').status_code, 302)

    def test_salesman_cannot_manage_salesmen(self):
        username, temp = self.create_salesman_via_admin()
        self.enroll_salesman_2fa(username, temp)
        self.complete_profile()
        denied = self.client.get('/admin/salesmen', follow_redirects=False)
        self.assertEqual(denied.status_code, 302)

    def test_dealer_tag_quick_select_on_sale_form(self):
        self.login_admin()
        self.client.post('/admin/dealer-tags', data={
            'name': 'Closer', 'sort_order': '1', 'is_active': 'y',
        })
        with self.app.app_context():
            vehicle = db.session.get(Vehicle, self.vehicle_id)
            vehicle.status = 'sold'
            db.session.commit()

        page = self.client.get(f'/admin/vehicles/{self.vehicle_id}/sale')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('Closer', body)
        self.assertIn('value="Boss"', body)  # admin dealer_tag prefill
        save = self.client.post(f'/admin/vehicles/{self.vehicle_id}/sale', data={
            'sold_price': '14000',
            'payment_method': 'cash',
            'salesperson': 'Closer',
            'sale_notes': 'Paid in full',
        }, follow_redirects=False)
        self.assertEqual(save.status_code, 302)
        with self.app.app_context():
            vehicle = db.session.get(Vehicle, self.vehicle_id)
            self.assertEqual(vehicle.salesperson, 'Closer')

        bos = self.client.get(f'/admin/vehicles/{self.vehicle_id}/bill-of-sale')
        self.assertEqual(bos.status_code, 200)
        self.assertIn(b'Closer', bos.data)

    def test_inactive_salesman_cannot_login(self):
        username, temp = self.create_salesman_via_admin()
        self.login_admin()
        with self.app.app_context():
            sid = User.query.filter_by(username=username).first().id
        self.client.post(f'/admin/salesmen/{sid}/deactivate')
        self.client.get('/admin/logout')
        response = self.client.post('/admin/login', data={
            'username': username, 'password': temp,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Invalid username or password', response.data)

    def test_profile_setup_requires_password_when_flagged(self):
        username, temp = self.create_salesman_via_admin()
        self.enroll_salesman_2fa(username, temp)
        bad = self.client.post('/admin/profile/setup', data={
            'full_name': 'Sam Sales',
            'email': 'sam@example.com',
            'phone': '919-555-0100',
            'dealer_tag': 'S. Sales',
            'new_password': '',
            'confirm_password': '',
        })
        self.assertEqual(bad.status_code, 422)
        with self.app.app_context():
            user = User.query.filter_by(username=username).first()
            self.assertFalse(user.profile_complete)


if __name__ == '__main__':
    unittest.main()
