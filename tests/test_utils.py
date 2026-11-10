from datetime import datetime, timezone
from decimal import Decimal
import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from app.utils import (
    _ffmpeg_path, dealership_local_to_utc, format_currency, format_mileage_badge,
    format_phone_number, is_low_mileage, is_safe_redirect, notify_new_lead,
    to_dealership_time,
)


class MileageBadgeTests(unittest.TestCase):
    def test_formats_low_mileage_for_vehicle_badges(self):
        self.assertEqual(format_mileage_badge(27000), '27K Miles')
        self.assertEqual(format_mileage_badge(27300), '27K Miles')
        self.assertEqual(format_mileage_badge(850), '850 mi')

    def test_invalid_mileage_uses_existing_fallback(self):
        self.assertEqual(format_mileage_badge(None), '0 mi')

    def test_formats_tracker_currency_to_cents(self):
        self.assertEqual(format_currency(Decimal('1234.5')), '$1,234.50')

    def test_older_vehicles_get_extended_low_mileage_threshold(self):
        current_year = datetime.now().year
        self.assertTrue(is_low_mileage(99999, year=current_year - 9))
        self.assertFalse(is_low_mileage(100000, year=current_year - 9))
        self.assertFalse(is_low_mileage(99999, year=current_year - 8))
        self.assertTrue(is_low_mileage(74999, year=current_year))
        self.assertFalse(is_low_mileage(75000, year=current_year))


class DealershipTimezoneTests(unittest.TestCase):
    def test_utc_timestamps_convert_with_eastern_standard_and_daylight_time(self):
        winter = to_dealership_time(datetime(2026, 1, 1, 4, 30))
        summer = to_dealership_time(datetime(2026, 7, 1, 4, 30))

        self.assertEqual(winter.isoformat(), '2025-12-31T23:30:00-05:00')
        self.assertEqual(winter.tzname(), 'EST')
        self.assertEqual(summer.isoformat(), '2026-07-01T00:30:00-04:00')
        self.assertEqual(summer.tzname(), 'EDT')

    def test_local_midnight_converts_to_correct_utc_boundary_across_dst(self):
        winter = dealership_local_to_utc(datetime(2026, 1, 1))
        summer = dealership_local_to_utc(datetime(2026, 7, 1))

        self.assertEqual(winter, datetime(2026, 1, 1, 5))
        self.assertEqual(summer, datetime(2026, 7, 1, 4))

    def test_aware_utc_timestamps_are_also_converted(self):
        value = datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc)
        self.assertEqual(to_dealership_time(value).strftime('%I:%M %p %Z'), '12:30 AM EDT')


class PhoneNumberFormattingTests(unittest.TestCase):
    def test_formats_valid_us_and_international_numbers(self):
        self.assertEqual(format_phone_number('9195550182'), '(919) 555-0182')
        self.assertEqual(format_phone_number('+442071838750'), '+44 20 7183 8750')

    def test_preserves_invalid_and_empty_values(self):
        self.assertEqual(format_phone_number('123'), '123')
        self.assertEqual(format_phone_number('  '), '')


class FfmpegResolutionTests(unittest.TestCase):
    def test_prefers_system_executable(self):
        with mock.patch('app.utils.shutil.which', return_value='/usr/bin/ffmpeg'), \
                mock.patch('imageio_ffmpeg.get_ffmpeg_exe') as bundled:
            self.assertEqual(_ffmpeg_path(), '/usr/bin/ffmpeg')
        bundled.assert_not_called()

    def test_uses_bundled_executable_without_system_installation(self):
        with mock.patch('app.utils.shutil.which', return_value=None), \
                mock.patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='/wheel/ffmpeg'):
            self.assertEqual(_ffmpeg_path(), '/wheel/ffmpeg')

    def test_missing_bundle_is_logged(self):
        with mock.patch('app.utils.shutil.which', return_value=None), \
                mock.patch('imageio_ffmpeg.get_ffmpeg_exe', side_effect=RuntimeError('unsupported host')), \
                self.assertLogs('app.utils', level='ERROR') as logs:
            self.assertIsNone(_ffmpeg_path())
        self.assertIn('unsupported host', logs.output[0])


class SafeRedirectTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['TESTING'] = True

    def test_rejects_backslash_redirects(self):
        with self.app.test_request_context('/demo', base_url='https://example.com'):
            self.assertFalse(is_safe_redirect(r'\evil.com'))

    def test_rejects_encoded_backslash_redirects(self):
        with self.app.test_request_context('/demo', base_url='https://example.com'):
            self.assertFalse(is_safe_redirect('/%5Cevil.com'))


class LeadNotificationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SEND_LEAD_EMAIL=True,
            MAIL_SERVER='smtp.example.com',
            MAIL_PORT=465,
            MAIL_USE_TLS=False,
            MAIL_USE_SSL=True,
            MAIL_USERNAME='user',
            MAIL_PASSWORD='pass',
            MAIL_DEFAULT_SENDER='dealer@example.com',
            BUSINESS_EMAIL='sales@example.com',
        )

    def test_uses_ssl_smtp_when_enabled(self):
        lead = SimpleNamespace(name='Jane', email='jane@example.com', phone='123', source='contact', vehicle_id=None, message='Hello')

        with self.app.app_context():
            with mock.patch('app.utils.smtplib.SMTP_SSL') as smtp_ssl, mock.patch('app.utils.smtplib.SMTP') as smtp:
                smtp_ssl.return_value.__enter__.return_value = smtp_ssl.return_value
                smtp.return_value.__enter__.return_value = smtp.return_value
                notify_new_lead(lead)

        smtp_ssl.assert_called_once_with('smtp.example.com', 465, timeout=10)
        smtp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
