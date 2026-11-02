import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import create_app, db
from app.models import CarfaxReport, User, Vehicle
from config import TestingConfig


class CarfaxReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            UPLOAD_FOLDER=os.path.join(self.tmp.name, 'uploads'),
            PHOTO_HIGHLIGHTS_ENABLED=False,
        )
        self.app.static_folder = self.tmp.name
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(
            year=2020, make='Honda', model='Civic', price=15000, mileage=40000,
        )
        self.report = CarfaxReport(vehicle=self.vehicle, filename='history.pdf')
        user = User(username='carfax-admin')
        user.set_password('test-password-only')
        db.session.add_all([self.vehicle, self.report, user])
        db.session.commit()
        self.vehicle_id = self.vehicle.id
        self.report_id = self.report.id
        os.makedirs(os.path.join(self.app.config['UPLOAD_FOLDER'], 'carfax'))
        self.pdf = b'%PDF-1.4\nCARFAX test report\n%%EOF'
        with open(os.path.join(
            self.app.config['UPLOAD_FOLDER'], 'carfax', self.report.filename,
        ), 'wb') as pdf:
            pdf.write(self.pdf)
        self.client = self.app.test_client()
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': user.username, 'password': 'test-password-only',
            })
        self.assertEqual(response.status_code, 302)
        self.download_path = f'/carfax/{self.report.id}/download'
        self.ctx.pop()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.tmp.cleanup()

    def test_admin_list_and_edit_with_pdf_render_download_link(self):
        for path in (
            '/admin/carfax-reports',
            f'/admin/carfax-reports/{self.report_id}/edit',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'href="{self.download_path}"', response.text)

    def test_admin_list_and_edit_without_pdf_render(self):
        with self.app.app_context():
            db.session.get(CarfaxReport, self.report_id).filename = None
            db.session.commit()
        for path in (
            '/admin/carfax-reports',
            f'/admin/carfax-reports/{self.report_id}/edit',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(f'href="{self.download_path}"', response.text)

    def test_admin_can_download_pdf_for_available_and_sold_vehicles(self):
        for status in ('available', 'sold'):
            with self.subTest(status=status):
                with self.app.app_context():
                    db.session.get(Vehicle, self.vehicle_id).status = status
                    db.session.commit()
                response = self.client.get(self.download_path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'application/pdf')
                self.assertEqual(response.data, self.pdf)
                response.close()

    def test_public_download_preserves_vehicle_visibility(self):
        public_client = self.app.test_client()
        response = public_client.get(self.download_path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.pdf)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, nofollow, noarchive')
        response.close()
        direct_path = f'/static/uploads/carfax/{self.report.filename}'
        response = public_client.get(direct_path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.pdf)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        response.close()
        with self.app.app_context():
            db.session.get(Vehicle, self.vehicle_id).status = 'sold'
            db.session.commit()
        self.assertEqual(public_client.get(self.download_path).status_code, 404)
        self.assertEqual(public_client.get(direct_path).status_code, 404)
        self.assertEqual(self.client.get(direct_path).status_code, 200)
        with self.app.app_context():
            vehicle = db.session.get(Vehicle, self.vehicle_id)
            vehicle.status = 'available'
            vehicle.is_coming_soon = True
            db.session.commit()
        self.assertEqual(public_client.get(self.download_path).status_code, 404)
        self.assertEqual(public_client.get(direct_path).status_code, 404)

    def test_direct_static_download_requires_a_database_listing_record(self):
        public_client = self.app.test_client()
        response = public_client.get('/static/uploads/carfax/untracked.pdf')
        self.assertEqual(response.status_code, 404)

    def test_non_carfax_static_assets_remain_public(self):
        public_asset = os.path.join(self.app.static_folder, 'public-asset.txt')
        with open(public_asset, 'w', encoding='utf-8') as asset:
            asset.write('public site asset')
        try:
            response = self.app.test_client().get('/static/public-asset.txt')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'public site asset')
        finally:
            os.remove(public_asset)

    def test_admin_pages_still_require_login(self):
        public_client = self.app.test_client()
        for path in (
            '/admin/carfax-reports',
            f'/admin/carfax-reports/{self.report_id}/edit',
        ):
            with self.subTest(path=path):
                response = public_client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/admin/login?next=', response.location)
