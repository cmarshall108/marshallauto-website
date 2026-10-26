import base64
import io
import os
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from app import create_app, db
from app.license_analysis import _extract_json_object, normalize_license_analysis
from app.models import TestDrive, User, Vehicle
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


class TestDriveLicenseScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            UPLOAD_FOLDER=os.path.join(self.tmp.name, 'public'),
            PRIVATE_UPLOAD_FOLDER=os.path.join(self.tmp.name, 'private'),
        )
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.vehicle = Vehicle(
            year=2021, make='Toyota', model='Corolla', price=16000,
            mileage=30000, status='available', slug='license-scan-corolla',
        )
        user = User(username='td-scan-admin')
        user.set_password('test-password')
        db.session.add_all([self.vehicle, user])
        db.session.commit()
        self.client = self.app.test_client()
        with mock.patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': 'td-scan-admin', 'password': 'test-password',
            })
        self.assertEqual(response.status_code, 302)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()
        self.tmp.cleanup()

    def scan_data_url(self):
        stream = BytesIO()
        Image.new('RGB', (80, 50), 'white').save(stream, 'JPEG')
        return 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode()

    def test_checkout_prefers_printer_scan_over_photo_upload(self):
        page = self.client.get('/admin/test-drives/new')
        self.assertIn("Scan Driver's License", page.text)
        self.assertIn('Scanning is preferred over a photo', page.text)
        self.assertIn('id="td-license-scan"', page.text)
        response = self.client.post('/admin/test-drives/new', data={
            'vehicle_id': self.vehicle.id,
            'customer_name': 'Jane Driver',
            'start_mileage': '30000',
            'license_scan_data': self.scan_data_url(),
            'license_image': (BytesIO(b'not-used'), 'phone-photo.jpg'),
        })
        self.assertEqual(response.status_code, 302)
        test_drive = TestDrive.query.one()
        self.assertTrue(test_drive.license_image_filename)
        path = os.path.join(
            self.app.config['PRIVATE_UPLOAD_FOLDER'], 'licenses', test_drive.license_image_filename,
        )
        self.assertTrue(os.path.isfile(path))


if __name__ == '__main__':
    unittest.main()