import io
import unittest
from unittest import mock

from app import create_app, db
from app.license_analysis import _extract_json_object, normalize_license_analysis
from app.models import User
from config import TestingConfig


class LicenseAnalysisTests(unittest.TestCase):
    def test_normalizes_details_and_authenticity(self):
        result = normalize_license_analysis({
            'details': {
                'customer_name': '  Jane   Driver ',
                'license_number': ' D1234567 ',
                'license_state': 'NC',
                'date_of_birth': '1990-02-03',
                'expiration_date': '2029-02-03',
                'address': '123 Main St, Sanford, NC 27330',
            },
            'authenticity': {
                'status': 'appears_authentic',
                'confidence': 0.91,
                'reasons': ['Layout and typography are visually consistent.'],
            },
        })

        self.assertEqual(result['details']['customer_name'], 'Jane Driver')
        self.assertEqual(result['details']['license_number'], 'D1234567')
        self.assertEqual(result['authenticity']['status'], 'appears_authentic')

    def test_low_confidence_verdict_becomes_uncertain(self):
        result = normalize_license_analysis({
            'details': {},
            'authenticity': {'status': 'suspicious', 'confidence': 0.4, 'reasons': []},
        })
        self.assertEqual(result['authenticity']['status'], 'uncertain')

    def test_extracts_json_from_markdown_response(self):
        self.assertEqual(_extract_json_object('```json\n{"details": {}}\n```'), {'details': {}})


class LicenseAnalysisRouteTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        user = User(username='license-test-admin')
        user.set_password('test-password')
        db.session.add(user)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _login(self):
        return self.client.post('/admin/login', data={
            'username': 'license-test-admin',
            'password': 'test-password',
            'submit': 'Log In',
        })

    def test_analysis_requires_login(self):
        response = self.client.post('/admin/test-drives/analyze-license')
        self.assertEqual(response.status_code, 302)

    def test_analysis_returns_extracted_details(self):
        self._login()
        expected = {
            'details': {
                'customer_name': 'Jane Driver',
                'license_number': 'D1234567',
                'license_state': 'NC',
                'date_of_birth': '1990-02-03',
                'expiration_date': '2029-02-03',
                'address': '123 Main St',
            },
            'authenticity': {
                'status': 'appears_authentic',
                'confidence': 0.91,
                'reasons': [],
            },
        }
        with mock.patch('app.admin.analyze_license_image', return_value=expected) as analyzer:
            response = self.client.post(
                '/admin/test-drives/analyze-license',
                data={'license_image': (io.BytesIO(b'image bytes'), 'license.jpg')},
                content_type='multipart/form-data',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['details']['customer_name'], 'Jane Driver')
        analyzer.assert_called_once_with(b'image bytes')


if __name__ == '__main__':
    unittest.main()