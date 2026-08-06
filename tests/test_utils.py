import unittest
from types import SimpleNamespace
from unittest import mock

from flask import Flask

from app.utils import is_safe_redirect, notify_new_lead


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
