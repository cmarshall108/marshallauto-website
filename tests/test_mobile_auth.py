import os
import tempfile
import unittest
from unittest.mock import patch

import pyotp

from app import create_app, db
from app.models import Lead, User, Vehicle
from app.spam_filter import MANUAL_CLEAR_MARKER
from app.two_factor import encrypt_secret
from config import TestingConfig


class MobileAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config = type('MobileConfig', (TestingConfig,), {
            'UPLOAD_FOLDER': os.path.join(self.tmp.name, 'public'),
            'PRIVATE_UPLOAD_FOLDER': os.path.join(self.tmp.name, 'private'),
            'WTF_CSRF_ENABLED': True,
            'ADMIN_TWO_FACTOR_REQUIRED': True,
        })
        self.app = create_app(config)
        self.client = self.app.test_client()
        self.rate_limit = patch('app.admin.rate_limit_exceeded', return_value=False)
        self.rate_limit.start()
        with self.app.app_context():
            db.create_all()
            self.secret = pyotp.random_base32()
            user = User(username='mobile', totp_enabled=True,
                        totp_secret_encrypted=encrypt_secret(self.secret))
            user.set_password('mobile-test-password')
            db.session.add(user)
            db.session.commit()

    def tearDown(self):
        self.rate_limit.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.tmp.cleanup()

    def post(self, url, data):
        csrf = self.client.get('/admin/mobile/session').json['csrf_token']
        return self.client.post(url, json=data, headers={'X-CSRFToken': csrf})

    def test_password_factor_dashboard_and_logout(self):
        self.assertEqual(self.client.get('/admin/dashboard/data').status_code, 401)
        self.assertEqual(self.client.post(
            '/admin/mobile/login', json={}, headers={'Accept': 'application/json'},
        ).status_code, 400)
        response = self.post('/admin/mobile/login', {
            'username': 'mobile', 'password': 'wrong',
        })
        self.assertEqual(response.status_code, 401)
        response = self.post('/admin/mobile/login', {
            'username': 'mobile', 'password': 'mobile-test-password',
        })
        self.assertEqual(response.json['state'], 'verify')
        self.assertEqual(self.client.get('/admin/dashboard/data').status_code, 401)
        self.assertEqual(self.post('/admin/mobile/verify', {'code': 'bad'}).status_code, 401)
        code = pyotp.TOTP(self.secret).now()
        self.assertEqual(self.post('/admin/mobile/verify', {'code': code}).json['state'], 'authenticated')
        self.assertEqual(self.client.get('/admin/dashboard/data').status_code, 200)
        self.assertEqual(self.post('/admin/mobile/logout', {}).json['state'], 'signed_out')
        self.assertEqual(self.client.get('/admin/dashboard/data').status_code, 401)
        self.post('/admin/mobile/login', {'username': 'mobile', 'password': 'mobile-test-password'})
        self.assertEqual(self.post('/admin/mobile/verify', {'code': code}).status_code, 401)

    def test_enrollment_and_onboarding_are_not_bypassed(self):
        with self.app.app_context():
            user = User.query.filter_by(username='mobile').one()
            user.totp_enabled = False
            db.session.commit()
        response = self.post('/admin/mobile/login', {
            'username': 'mobile', 'password': 'mobile-test-password',
        })
        self.assertEqual(response.json['state'], 'enroll')
        self.assertEqual(self.client.get('/admin/dashboard/data').status_code, 401)
        with self.app.app_context():
            user = User.query.filter_by(username='mobile').one()
            user.totp_enabled = True
            user.profile_complete = False
            db.session.commit()
        self.post('/admin/mobile/login', {'username': 'mobile', 'password': 'mobile-test-password'})
        response = self.post('/admin/mobile/verify', {'code': pyotp.TOTP(self.secret).now()})
        self.assertEqual(response.json['state'], 'onboarding')
        self.assertTrue(self.client.get('/admin/mobile/session').json['needs_onboarding'])
        self.assertEqual(self.client.get('/admin/dashboard/data').status_code, 403)

    def test_https_csrf_header_and_referrer(self):
        response = self.client.get('/admin/mobile/session', base_url='https://localhost')
        token = response.json['csrf_token']
        response = self.client.post(
            '/admin/mobile/login', base_url='https://localhost',
            json={'username': 'mobile', 'password': 'mobile-test-password'},
            headers={
                'Accept': 'application/json',
                'X-CSRFToken': token,
                'Referer': 'https://localhost/',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['state'], 'verify')

    def test_inventory_and_leads_require_verified_session_and_support_actions(self):
        with self.app.app_context():
            db.session.add_all([
                Vehicle(year=2022, make='Honda', model='Civic', price=12000,
                        mileage=30000, status='available', slug='mobile-civic'),
                Vehicle(year=2021, make='Ford', model='Focus', price=9000,
                        mileage=40000, status='sold', slug='mobile-focus'),
                Lead(name='Buyer', email='buyer@example.com', message='Interested'),
                Lead(name='Spam', email='spam@example.com', is_spam=True, spam_score=9),
            ])
            db.session.commit()
        for path in ('/admin/mobile/vehicles', '/admin/mobile/leads'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.post('/admin/mobile/login', {'username': 'mobile', 'password': 'mobile-test-password'})
        self.assertEqual(self.client.get('/admin/mobile/leads').status_code, 401)
        self.post('/admin/mobile/verify', {'code': pyotp.TOTP(self.secret).now()})
        vehicles = self.client.get('/admin/mobile/vehicles?status=sold').json
        self.assertEqual(len(vehicles['items']), 1)
        self.assertEqual(vehicles['items'][0]['title'], '2021 Ford Focus')
        inbox = self.client.get('/admin/mobile/leads').json
        self.assertEqual((inbox['inbox_count'], inbox['spam_count']), (1, 1))
        lead = inbox['items'][0]
        self.assertEqual(lead['message'], 'Interested')
        response = self.client.post(
            f"/admin/mobile/leads/{lead['id']}/read", json={},
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.post(f"/admin/mobile/leads/{lead['id']}/read", {}).json['state'], 'updated')
        self.assertTrue(self.client.get('/admin/mobile/leads').json['items'][0]['is_read'])
        self.post(f"/admin/mobile/leads/{lead['id']}/spam", {})
        self.assertEqual(self.client.get('/admin/mobile/leads').json['inbox_count'], 0)
        self.post(f"/admin/mobile/leads/{lead['id']}/spam", {})
        with self.app.app_context():
            restored = db.session.get(Lead, lead['id'])
            self.assertEqual(restored.spam_reasons, MANUAL_CLEAR_MARKER)
            self.assertEqual(restored.spam_score, 0)
        self.post(f"/admin/mobile/leads/{lead['id']}/delete", {})
        self.assertEqual(self.client.get('/admin/mobile/leads').json['items'], [])
        self.post('/admin/mobile/logout', {})
        self.assertEqual(self.client.get('/admin/mobile/vehicles').status_code, 401)

    def test_mobile_lists_paginate_and_enforce_onboarding(self):
        with self.app.app_context():
            db.session.add_all([
                Vehicle(year=2020, make='Test', model=str(n), price=1000, mileage=100,
                        status='available', slug=f'mobile-vehicle-{n}')
                for n in range(21)
            ])
            db.session.add_all([
                Lead(name=f'Buyer {n}', email=f'buyer{n}@example.com')
                for n in range(26)
            ])
            db.session.commit()
        self.post('/admin/mobile/login', {'username': 'mobile', 'password': 'mobile-test-password'})
        self.post('/admin/mobile/verify', {'code': pyotp.TOTP(self.secret).now()})
        for path, first_count in (('/admin/mobile/vehicles', 20), ('/admin/mobile/leads', 25)):
            first = self.client.get(path).json
            second = self.client.get(path + '?page=2').json
            self.assertEqual(len(first['items']), first_count)
            self.assertTrue(first['has_next'])
            self.assertEqual(len(second['items']), 1)
            self.assertFalse(second['has_next'])
        with self.app.app_context():
            user = User.query.filter_by(username='mobile').one()
            user.profile_complete = False
            db.session.commit()
        self.assertEqual(self.client.get('/admin/mobile/vehicles').status_code, 403)
        self.assertEqual(self.client.get('/admin/mobile/leads').status_code, 403)
        self.assertEqual(self.post('/admin/mobile/leads/1/read', {}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
