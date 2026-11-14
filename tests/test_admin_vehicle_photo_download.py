import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import User, Vehicle, VehicleImage
from config import TestingConfig


class AdminVehiclePhotoDownloadTests(unittest.TestCase):
    def setUp(self):
        self.uploads = tempfile.TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config['UPLOAD_FOLDER'] = self.uploads.name
        self.photos = Path(self.uploads.name) / 'vehicles'
        self.photos.mkdir()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='photo-agent')
        user.set_password('photo-test-password')
        vehicle = Vehicle(
            slug='photo-test-car', year=2022, make='Toyota', model='Camry',
            price=18900, mileage=42000, status='available',
        )
        db.session.add_all([user, vehicle])
        db.session.commit()
        self.vehicle_id = vehicle.id
        self.url = f'/admin/vehicles/{self.vehicle_id}/images/download'
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.uploads.cleanup()

    def login(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': 'photo-agent', 'password': 'photo-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def add_image(self, filename, order, content=None, vehicle_id=None):
        db.session.add(VehicleImage(
            vehicle_id=vehicle_id or self.vehicle_id, filename=filename, order_index=order,
        ))
        if content is not None:
            (self.photos / filename).write_bytes(content)
        db.session.commit()

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login', response.location)

    def test_downloads_only_this_vehicles_jpegs_in_display_order(self):
        self.add_image('second.JPEG', 2, b'second')
        self.add_image('first.jpg', 0, b'first')
        self.add_image('other.png', 1, b'png')
        other = Vehicle(
            slug='other-car', year=2020, make='Honda', model='Civic',
            price=12000, mileage=50000, status='available',
        )
        db.session.add(other)
        db.session.commit()
        self.add_image('other.jpg', 0, b'other vehicle', other.id)
        self.login()

        page = BeautifulSoup(
            self.client.get(f'/admin/vehicles/{self.vehicle_id}/edit').data, 'html.parser',
        )
        self.assertIsNotNone(page.select_one(f'a[href="{self.url}"]'))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/zip')
        self.assertIn(f'vehicle-{self.vehicle_id}-jpeg-photos.zip', response.headers['Content-Disposition'])
        with zipfile.ZipFile(BytesIO(response.data)) as archive:
            self.assertEqual(archive.namelist(), ['001_first.jpg', '002_second.JPEG'])
            self.assertEqual(archive.read('001_first.jpg'), b'first')
            self.assertEqual(archive.read('002_second.JPEG'), b'second')

    def test_no_jpegs_reports_why_download_is_unavailable(self):
        self.add_image('photo.png', 0, b'png')
        self.login()
        response = self.client.get(self.url, follow_redirects=True)
        self.assertIn(b'no JPEG photos to download', response.data)

    def test_missing_photo_reports_error_instead_of_partial_download(self):
        self.add_image('first.jpg', 0, b'first')
        self.add_image('missing.jpg', 1)
        self.login()
        response = self.client.get(self.url, follow_redirects=True)
        self.assertIn(b'Could not download all JPEG photos', response.data)

    def test_unknown_vehicle_returns_not_found(self):
        self.login()
        self.assertEqual(self.client.get('/admin/vehicles/999999/images/download').status_code, 404)


if __name__ == '__main__':
    unittest.main()
