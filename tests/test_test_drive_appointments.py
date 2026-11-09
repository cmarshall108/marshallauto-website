import unittest
from datetime import datetime, timedelta
from importlib import import_module
from html.parser import HTMLParser
from unittest.mock import patch

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from app import create_app, db
from app.models import TestDrive as Drive, TestDriveReminderDismissal as ReminderDismissal, User, Vehicle
from config import TestingConfig


class AppointmentTests(unittest.TestCase):
    def setUp(self):
        config = type('AppointmentConfig', (TestingConfig,), {
            'WTF_CSRF_ENABLED': False,
            'TEXTBELT_API_KEY': 'test-key',
            'ADMIN_TWO_FACTOR_REQUIRED': False,
        })
        self.app = create_app(config)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.admin = User(username='appointment-admin', role=User.ROLE_ADMIN, profile_complete=True)
        self.admin.set_password('appointment-password')
        self.other_admin = User(username='other-admin', role=User.ROLE_ADMIN, profile_complete=True)
        self.other_admin.set_password('appointment-password')
        self.vehicle = Vehicle(
            slug='appointment-car', year=2020, make='Toyota', model='Camry',
            price=15000, mileage=30000, status='available',
        )
        db.session.add_all([self.admin, self.other_admin, self.vehicle])
        db.session.flush()
        self.when = datetime(2030, 6, 10, 10, 0)
        self.drive = Drive(
            vehicle_id=self.vehicle.id, customer_name='Pat Buyer', customer_phone='9195550101',
            status='requested', started_at=self.when, scheduled_at=self.when,
        )
        db.session.add(self.drive)
        db.session.commit()
        self.client = self.app.test_client()
        self.login()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self, username='appointment-admin'):
        self.client.post('/admin/login', data={'username': username, 'password': 'appointment-password'})

    def confirm(self, sent=True):
        with patch('app.admin.dealership_now', return_value=self.when - timedelta(days=1)), \
                patch('app.admin._send_textbelt_message', return_value=sent) as send:
            response = self.client.post(f'/admin/test-drives/{self.drive.id}/confirm', follow_redirects=True)
        return response, send

    def test_confirm_sends_once_and_adds_calendar_event(self):
        response, send = self.confirm()
        self.assertIn(b'confirmation text sent', response.data)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], '+19195550101')
        self.assertIn('June 10, 2030 at 10:00 AM', send.call_args.args[1])
        self.assertEqual(self.drive.status, 'confirmed')
        self.assertEqual(self.drive.confirmation_sms_status, 'sent')
        _, second_send = self.confirm()
        second_send.assert_not_called()
        calendar = self.client.get('/admin/test-drives/calendar?year=2030&month=6')
        self.assertIn(b'Pat Buyer', calendar.data)
        self.assertIn(b'10:00 AM', calendar.data)

    def test_failed_text_is_visible_and_retryable(self):
        response, _ = self.confirm(sent=False)
        self.assertIn(b'text could not be sent', response.data)
        self.assertEqual(self.drive.status, 'confirmed')
        self.assertEqual(self.drive.confirmation_sms_status, 'failed')
        response, send = self.confirm()
        send.assert_called_once()
        self.assertEqual(self.drive.confirmation_sms_status, 'sent')

    def test_missing_key_or_invalid_phone_does_not_confirm(self):
        self.app.config['TEXTBELT_API_KEY'] = None
        response, send = self.confirm()
        send.assert_not_called()
        self.assertIn(b'TEXTBELT_API_KEY', response.data)
        self.assertEqual(self.drive.status, 'requested')
        self.app.config['TEXTBELT_API_KEY'] = 'test-key'
        self.drive.customer_phone = 'bad phone'
        db.session.commit()
        _, send = self.confirm()
        send.assert_not_called()
        self.assertEqual(self.drive.status, 'requested')

    def test_reminders_are_persistent_per_staff_and_have_two_phases(self):
        self.confirm()
        with patch('app.admin.dealership_now', return_value=self.when - timedelta(minutes=31)):
            self.assertEqual(self.client.get('/admin/api/test-drives/reminders').json['reminders'], [])
        with patch('app.admin.dealership_now', return_value=self.when - timedelta(minutes=30)):
            reminders = self.client.get('/admin/api/test-drives/reminders').json['reminders']
            self.assertEqual([item['phase'] for item in reminders], ['upcoming'])
            self.assertEqual(self.client.post(
                f'/admin/test-drives/{self.drive.id}/reminders/upcoming/dismiss',
            ).status_code, 200)
            self.assertEqual(self.client.get('/admin/api/test-drives/reminders').json['reminders'], [])
            self.client.get('/admin/logout')
            self.login('other-admin')
            self.assertEqual(len(self.client.get('/admin/api/test-drives/reminders').json['reminders']), 1)
        with patch('app.admin.dealership_now', return_value=self.when + timedelta(days=1)):
            reminders = self.client.get('/admin/api/test-drives/reminders').json['reminders']
            self.assertEqual([item['phase'] for item in reminders], ['upcoming', 'due'])
        self.assertEqual(ReminderDismissal.query.count(), 1)

    def test_cancelled_appointment_is_not_in_calendar_or_reminders(self):
        self.confirm()
        self.drive.status = 'cancelled'
        db.session.commit()
        with patch('app.admin.dealership_now', return_value=self.when):
            self.assertEqual(self.client.get('/admin/api/test-drives/reminders').json['reminders'], [])
        self.assertNotIn(b'Pat Buyer', self.client.get('/admin/test-drives/calendar?year=2030&month=6').data)

    def test_confirm_requires_admin_and_rejects_past_appointments(self):
        self.client.get('/admin/logout')
        self.assertEqual(self.client.post(f'/admin/test-drives/{self.drive.id}/confirm').status_code, 302)
        self.login()
        with patch('app.admin.dealership_now', return_value=self.when), \
                patch('app.admin._send_textbelt_message') as send:
            self.client.post(f'/admin/test-drives/{self.drive.id}/confirm')
        send.assert_not_called()
        self.assertEqual(self.drive.status, 'requested')

    def test_edit_requested_without_mileage_preserves_status(self):
        class MileageFieldParser(HTMLParser):
            required = False

            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == 'input' and attributes.get('name') == 'start_mileage':
                    self.required = 'required' in attributes

        parser = MileageFieldParser()
        parser.feed(self.client.get(f'/admin/test-drives/{self.drive.id}/edit').get_data(as_text=True))
        self.assertFalse(parser.required)
        response = self.client.post(f'/admin/test-drives/{self.drive.id}/edit', data={
            'customer_name': 'Pat Buyer', 'customer_phone': '9195550101',
            'status': 'requested',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.drive.status, 'requested')

    def test_staff_cannot_confirm_requests(self):
        self.admin.role = User.ROLE_SALESMAN
        db.session.commit()
        with patch('app.two_factor.session_verified', return_value=True), \
                patch('app.admin._send_textbelt_message') as send:
            response = self.client.post(f'/admin/test-drives/{self.drive.id}/confirm')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.drive.status, 'requested')
        send.assert_not_called()

    def test_confirmed_appointment_remains_on_calendar_after_checkout(self):
        self.confirm()
        response = self.client.post(f'/admin/test-drives/{self.drive.id}/edit', data={
            'customer_name': 'Pat Buyer', 'customer_phone': '9195550101',
            'status': 'out', 'start_mileage': '30000',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.drive.status, 'out')
        self.assertEqual(self.drive.scheduled_at, self.when)
        self.assertIn(b'Pat Buyer', self.client.get('/admin/test-drives/calendar?year=2030&month=6').data)


class AppointmentMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_existing_request_and_handles_bootstrapped_table(self):
        engine = create_engine('sqlite://')
        migration = import_module('migrations.versions.n8b9c0d1e2f3_test_drive_confirmation')
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE user (id INTEGER PRIMARY KEY)'))
            connection.execute(text(
                'CREATE TABLE test_drives (id INTEGER PRIMARY KEY, status VARCHAR(20), started_at DATETIME)'
            ))
            connection.execute(text(
                "INSERT INTO test_drives VALUES (1, 'requested', '2030-06-10 10:00:00')"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            row = connection.execute(text(
                'SELECT scheduled_at, confirmed_at FROM test_drives WHERE id = 1'
            )).one()
            self.assertEqual(row.scheduled_at, '2030-06-10 10:00:00')
            self.assertIsNone(row.confirmed_at)
            self.assertTrue(db.inspect(connection).has_table('test_drive_reminder_dismissals'))
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
