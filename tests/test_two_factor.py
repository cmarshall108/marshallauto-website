import hashlib
import importlib
import os
import re
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch

import pyotp
from alembic.migration import MigrationContext
from alembic.operations import Operations
from flask_login.utils import encode_cookie
from sqlalchemy import create_engine, inspect, text

from app import create_app, db
from app.models import AdminRecoveryCode, User
from app.two_factor import (
    consume_code, decrypt_secret, encrypt_secret, provisioning_uri, replace_recovery_codes,
    totp_counter,
)
from config import Config, TestingConfig


class RequiredTwoFactorConfig(TestingConfig):
    ADMIN_TWO_FACTOR_REQUIRED = True


class TwoFactorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        config = type('LocalTwoFactorConfig', (RequiredTwoFactorConfig,), {
            'UPLOAD_FOLDER': os.path.join(self.tmp.name, 'public'),
            'PRIVATE_UPLOAD_FOLDER': os.path.join(self.tmp.name, 'private'),
        })
        self.app = create_app(config)
        self.client = self.app.test_client()
        self.rate_limit = patch('app.admin.rate_limit_exceeded', return_value=False)
        self.rate_limit.start()
        with self.app.app_context():
            db.create_all()
            user = User(username='two-factor-admin')
            user.set_password('test-password-only')
            db.session.add(user)
            db.session.commit()
            self.user_id = user.id

    def tearDown(self):
        self.rate_limit.stop()
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.tmp.cleanup()

    def password_login(self, client=None, next_page=None):
        client = client or self.client
        return client.post('/admin/login', query_string={'next': next_page} if next_page else {}, data={
            'username': 'two-factor-admin', 'password': 'test-password-only', 'remember': 'y',
        })

    def secret(self):
        with self.app.app_context():
            return decrypt_secret(db.session.get(User, self.user_id).totp_secret_encrypted)

    def enroll(self, client=None):
        client = client or self.client
        self.assertEqual(self.password_login(client).location, '/admin/login/setup')
        page = client.get('/admin/login/setup')
        self.assertEqual(page.status_code, 200)
        secret = self.secret()
        response = client.post('/admin/login/setup', data={'code': pyotp.TOTP(secret).now()})
        self.assertEqual(response.status_code, 200)
        codes = re.findall(r'<code>([A-F0-9-]{24})</code>', response.text)
        self.assertEqual(len(codes), 10)
        return secret, codes, response

    def factor_login(self, code, client=None):
        client = client or self.client
        self.assertEqual(self.password_login(client).location, '/admin/login/verify')
        return client.post('/admin/login/verify', data={'code': code})

    def test_mandatory_enrollment_blocks_every_admin_surface_until_confirmed(self):
        self.assertTrue(Config.ADMIN_TWO_FACTOR_REQUIRED)
        response = self.password_login(next_page='/admin/vehicles?status=sold')
        self.assertEqual(response.location, '/admin/login/setup')
        for path in ('/admin/', '/admin/vehicles', '/admin/settings', '/admin/settings/two-factor'):
            self.assertEqual(self.client.get(path).status_code, 302)
        self.assertEqual(self.client.post('/admin/vehicles/1/delete').status_code, 302)
        page = self.client.get('/admin/login/setup')
        self.assertEqual(page.status_code, 200)
        secret = self.secret()
        qr = self.client.get('/admin/login/setup/qr')
        self.assertEqual(qr.status_code, 200)
        self.assertEqual(qr.mimetype, 'image/svg+xml')
        self.assertEqual(qr.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(qr.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(self.client.get('/admin/login/setup').status_code, 200)
        self.assertEqual(self.secret(), secret)
        with self.client.session_transaction() as cookie:
            self.assertNotIn('_user_id', cookie)
            self.assertNotIn(secret, str(dict(cookie)))
        wrong = self.client.post('/admin/login/setup', data={'code': 'not-valid'})
        self.assertIn('Setup is not complete', wrong.text)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        response = self.client.post('/admin/login/setup', data={'code': pyotp.TOTP(secret).now()})
        self.assertIn('I Saved My Codes', response.text)
        self.assertIn('/admin/vehicles?status=sold', response.text)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(self.client.get('/admin/login/setup/qr').status_code, 403)
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            self.assertTrue(user.totp_enabled)
            self.assertNotEqual(user.totp_secret_encrypted, secret)
            self.assertNotIn(secret, user.totp_secret_encrypted)
            self.assertEqual(AdminRecoveryCode.query.count(), 10)
            self.assertIsNotNone(user.last_login_at)

    def test_totp_login_replay_protection_clock_window_and_next_redirect(self):
        secret, _, _ = self.enroll()
        enrolled_counter = int(time.time()) // 30
        self.client.get('/admin/logout')
        self.assertEqual(self.password_login(next_page='/admin/vehicles').location, '/admin/login/verify')
        response = self.client.post('/admin/login/verify', data={'code': pyotp.TOTP(secret).at(enrolled_counter * 30)})
        self.assertEqual(response.status_code, 200)
        self.assertIn('already-used code', response.text)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        with patch('app.two_factor.time.time', return_value=(enrolled_counter + 1) * 30):
            response = self.client.post('/admin/login/verify', data={
                'code': pyotp.TOTP(secret).at((enrolled_counter + 1) * 30),
            })
        self.assertEqual(response.location, '/admin/vehicles')
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            for counter in (enrolled_counter, enrolled_counter + 1, enrolled_counter + 3):
                with patch('app.two_factor.time.time', return_value=(enrolled_counter + 1) * 30):
                    self.assertFalse(consume_code(user, pyotp.TOTP(secret).at(counter * 30)))
                    db.session.rollback()
            with patch('app.two_factor.time.time', return_value=(enrolled_counter + 1) * 30):
                self.assertTrue(consume_code(user, pyotp.TOTP(secret).at((enrolled_counter + 2) * 30)))
                db.session.commit()

    def test_recovery_code_requires_password_and_is_consumed_once(self):
        _, codes, response = self.enroll()
        self.assertNotIn('remember_token=', '\n'.join(response.headers.getlist('Set-Cookie')))
        self.client.get('/admin/logout')
        self.assertEqual(self.client.post('/admin/login/verify', data={'code': codes[0]}).status_code, 302)
        bad = self.client.post('/admin/login', data={
            'username': 'two-factor-admin', 'password': 'bad-password',
        })
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(self.client.get('/admin/login/verify').status_code, 302)
        self.assertEqual(self.factor_login(codes[0]).location, '/admin/dashboard')
        with self.app.app_context():
            digest = hashlib.sha256(codes[0].replace('-', '').lower().encode()).hexdigest()
            self.assertIsNone(AdminRecoveryCode.query.filter_by(code_hash=digest).first())
            self.assertEqual(AdminRecoveryCode.query.count(), 9)
        self.client.get('/admin/logout')
        self.assertEqual(self.factor_login(codes[0]).status_code, 200)
        self.assertEqual(self.client.get('/admin/').status_code, 302)

    def test_expired_pending_challenge_and_changed_password_fail_closed(self):
        self.password_login()
        with self.client.session_transaction() as cookie:
            cookie['admin_2fa_pending'] = dict(
                cookie['admin_2fa_pending'], issued_at=time.time() - 301,
            )
        self.assertEqual(self.client.get('/admin/login/setup').location, '/admin/login')
        self.assertEqual(self.client.get('/admin/login/setup/qr').status_code, 403)
        self.password_login()
        with self.app.app_context():
            db.session.get(User, self.user_id).set_password('new-password-only')
            db.session.commit()
        self.assertEqual(self.client.get('/admin/login/setup').location, '/admin/login')

    def test_persisted_lockout_is_not_reset_by_correct_password(self):
        _, codes, _ = self.enroll()
        self.client.get('/admin/logout')
        self.password_login()
        for _ in range(5):
            self.client.post('/admin/login/verify', data={'code': 'bad-code'})
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            self.assertEqual(user.failed_login_attempts, 5)
            self.assertTrue(user.is_locked)
        self.assertEqual(self.password_login().status_code, 429)
        self.assertEqual(self.client.post('/admin/login/verify', data={'code': codes[0]}).location, '/admin/login')

    def test_old_sessions_remember_cookies_and_expired_verified_sessions_are_rejected(self):
        self.enroll()
        legacy = self.app.test_client()
        with legacy.session_transaction() as cookie:
            cookie['_user_id'] = str(self.user_id)
            cookie['_fresh'] = True
        with self.app.app_context():
            legacy.set_cookie('remember_token', encode_cookie(str(self.user_id)))
        self.assertEqual(legacy.get('/admin/').status_code, 302)
        with self.client.session_transaction() as cookie:
            cookie['admin_2fa_at'] = time.time() - 12 * 3600 - 1
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.assertEqual(self.client.get('/admin/settings/two-factor').status_code, 302)

    def test_legacy_remember_cookie_is_cleared_at_password_step(self):
        with self.app.app_context():
            self.client.set_cookie('remember_token', encode_cookie(str(self.user_id)))
        response = self.password_login()
        self.assertEqual(response.location, '/admin/login/setup')
        self.assertIn('remember_token=;', '\n'.join(response.headers.getlist('Set-Cookie')))
        self.assertIsNone(self.client.get_cookie('remember_token'))

    def test_recovery_regeneration_verifies_password_factor_and_revokes_other_sessions(self):
        _, codes, _ = self.enroll()
        other = self.app.test_client()
        self.assertEqual(self.factor_login(codes[0], other).status_code, 302)
        response = self.client.post('/admin/settings/two-factor', data={
            'password': 'bad-password', 'code': codes[1],
        })
        self.assertIn('Incorrect password', response.text)
        with self.app.app_context():
            self.assertEqual(AdminRecoveryCode.query.count(), 9)
        response = self.client.post('/admin/settings/two-factor', data={
            'password': 'test-password-only', 'code': codes[1],
        })
        self.assertEqual(response.status_code, 200)
        new_codes = re.findall(r'<code>([A-F0-9-]{24})</code>', response.text)
        self.assertEqual(len(new_codes), 10)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(other.get('/admin/').status_code, 302)
        self.client.get('/admin/logout')
        self.assertEqual(self.factor_login(codes[2]).status_code, 200)
        self.assertEqual(self.client.post('/admin/login/verify', data={'code': new_codes[0]}).status_code, 302)
        self.assertNotIn(new_codes[1], self.client.get('/admin/settings/two-factor').text)

    def test_password_change_invalidates_other_sessions_but_keeps_current_verified(self):
        _, codes, _ = self.enroll()
        other = self.app.test_client()
        self.factor_login(codes[0], other)
        response = self.client.post('/admin/settings/password', data={
            'current_password': 'test-password-only', 'new_password': 'updated-password-only',
            'confirm_password': 'updated-password-only',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertEqual(other.get('/admin/').status_code, 302)

    def test_csrf_and_active_account_requirements(self):
        self.password_login()
        self.client.get('/admin/login/setup')
        self.app.config['WTF_CSRF_ENABLED'] = True
        response = self.client.post('/admin/login/setup', data={
            'code': pyotp.TOTP(self.secret()).now(),
        }, headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertFalse(db.session.get(User, self.user_id).totp_enabled)
        self.app.config['WTF_CSRF_ENABLED'] = False
        self.client.post('/admin/login/setup', data={'code': pyotp.TOTP(self.secret()).now()})
        self.client.get('/admin/logout')
        self.password_login()
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/admin/login/verify', data={'code': '123456'}, headers={
            'Accept': 'application/json',
        }).status_code, 400)
        self.app.config['WTF_CSRF_ENABLED'] = False
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            codes = replace_recovery_codes(user)
            db.session.commit()
        self.factor_login(codes[0])
        self.app.config['WTF_CSRF_ENABLED'] = True
        self.assertEqual(self.client.post('/admin/settings/two-factor', headers={
            'Accept': 'application/json',
        }).status_code, 400)
        with self.app.app_context():
            db.session.get(User, self.user_id).is_active_user = False
            db.session.commit()
        self.assertEqual(self.client.get('/admin/').status_code, 302)

    def test_secret_corruption_cannot_bypass_2fa_even_with_recovery_code(self):
        _, codes, _ = self.enroll()
        self.client.get('/admin/logout')
        with self.app.app_context():
            db.session.get(User, self.user_id).totp_secret_encrypted = 'invalid-ciphertext'
            db.session.commit()
        self.password_login()
        response = self.client.post('/admin/login/verify', data={'code': codes[0]})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get('/admin/').status_code, 302)

    def test_signing_key_rotation_blocks_existing_secrets_until_owner_reset(self):
        self.enroll()
        self.app.config['SECRET_KEY'] = 'rotated-test-secret-key'
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.password_login()
        self.assertEqual(self.client.post('/admin/login/verify', data={'code': '123456'}).status_code, 503)

    def test_concurrent_requests_cannot_consume_same_totp_or_recovery_code(self):
        config = type('ConcurrentConfig', (RequiredTwoFactorConfig,), {
            'UPLOAD_FOLDER': os.path.join(self.tmp.name, 'concurrent-public'),
            'PRIVATE_UPLOAD_FOLDER': os.path.join(self.tmp.name, 'concurrent-private'),
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///' + os.path.join(self.tmp.name, 'concurrent.db'),
        })
        app = create_app(config)
        secret = pyotp.random_base32()
        with app.app_context():
            db.create_all()
            user = User(username='concurrent-admin', totp_enabled=True)
            user.set_password('test-password-only')
            user.totp_secret_encrypted = encrypt_secret(secret)
            db.session.add(user)
            db.session.flush()
            codes = replace_recovery_codes(user)
            db.session.commit()
            user_id = user.id
        for code in (pyotp.TOTP(secret).now(), codes[0]):
            with self.subTest(kind='totp' if len(code) == 6 else 'recovery'):
                barrier = Barrier(2)

                def consume():
                    with app.app_context():
                        user = db.session.get(User, user_id)
                        barrier.wait(timeout=5)
                        result = consume_code(user, code)
                        db.session.commit()
                        return result

                with ThreadPoolExecutor(max_workers=2) as workers:
                    results = list(workers.map(lambda _: consume(), range(2)))
                self.assertEqual(sorted(results), [False, True])
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    def test_cli_reset_is_confirmed_and_requires_reenrollment(self):
        self.enroll()
        runner = self.app.test_cli_runner()
        cancelled = runner.invoke(args=['reset-admin-2fa', 'two-factor-admin'], input='n\n')
        self.assertNotEqual(cancelled.exit_code, 0)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        result = runner.invoke(args=['reset-admin-2fa', 'two-factor-admin'], input='y\n')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.client.get('/admin/').status_code, 302)
        self.assertEqual(self.password_login().location, '/admin/login/setup')
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            self.assertFalse(user.totp_enabled)
            self.assertIsNone(user.totp_secret_encrypted)
            self.assertEqual(AdminRecoveryCode.query.count(), 0)

    def test_unsafe_next_redirect_is_discarded_and_login_form_keeps_safe_next(self):
        self.enroll()
        self.client.get('/admin/logout')
        response = self.password_login(next_page='https://untrusted.example/')
        self.assertEqual(response.location, '/admin/login/verify')
        with self.client.session_transaction() as cookie:
            self.assertIsNone(cookie['admin_2fa_pending']['next'])
        page = self.client.get('/admin/login?next=/admin/vehicles')
        self.assertIn('action="/admin/login?next=/admin/vehicles"', page.text)

    def test_second_admin_requires_own_enrollment(self):
        self.enroll()
        with self.app.app_context():
            user = User(username='second-admin')
            user.set_password('second-password')
            db.session.add(user)
            db.session.commit()
        second = self.app.test_client()
        response = second.post('/admin/login', data={
            'username': 'second-admin', 'password': 'second-password',
        })
        self.assertEqual(response.location, '/admin/login/setup')
        self.assertEqual(second.get('/admin/').status_code, 302)

    def test_standard_totp_vector_and_authenticator_uri(self):
        secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        with patch('app.two_factor.time.time', return_value=59):
            self.assertEqual(totp_counter(secret, '287082'), 1)
            self.assertIsNone(totp_counter(secret, 'not-a-code'))
        self.password_login()
        self.client.get('/admin/login/setup')
        with self.app.app_context():
            user = db.session.get(User, self.user_id)
            parsed = pyotp.parse_uri(provisioning_uri(user))
            self.assertEqual(parsed.secret, decrypt_secret(user.totp_secret_encrypted))
            self.assertEqual(parsed.name, user.username)
            self.assertEqual(parsed.issuer, 'Marshall Auto Admin')
            self.assertEqual(parsed.interval, 30)
            self.assertEqual(parsed.digits, 6)


class TwoFactorMigrationTests(unittest.TestCase):
    def test_upgrade_is_idempotent_and_existing_user_survives_downgrade(self):
        migration = importlib.import_module(
            'migrations.versions.e6f7a8b9c0d1_add_admin_two_factor_auth',
        )
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE user (id INTEGER PRIMARY KEY, username VARCHAR(64))'))
            connection.execute(text("INSERT INTO user VALUES (1, 'existing-admin')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                self.assertEqual(connection.execute(text('SELECT totp_enabled FROM user')).scalar(), 0)
                self.assertIn('admin_recovery_codes', inspect(connection).get_table_names())
                migration.downgrade()
                self.assertNotIn('admin_recovery_codes', inspect(connection).get_table_names())
                self.assertEqual(
                    connection.execute(text('SELECT username FROM user')).scalar(), 'existing-admin',
                )
        engine.dispose()
