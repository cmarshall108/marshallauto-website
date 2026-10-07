import os
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pillow_heif
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import create_app, db
from app.models import User, Vehicle, VehicleImage
from app.utils import convert_heic_vehicle_images, save_uploaded_image
from config import TestingConfig


def heic_bytes(size=(1600, 900)):
    output = BytesIO()
    pillow_heif.from_pillow(Image.new('RGB', size, (200, 30, 30))).save(output, quality=80)
    return output.getvalue()


class HeicConversionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(UPLOAD_FOLDER=self.tmp.name, PHOTO_HIGHLIGHTS_ENABLED=False)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.folder = os.path.join(self.tmp.name, 'vehicles')
        os.makedirs(self.folder)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def test_upload_of_heic_is_stored_as_jpeg(self):
        upload = FileStorage(stream=BytesIO(heic_bytes()), filename='IMG_0001.HEIC')
        filename, width, height = save_uploaded_image(upload)

        self.assertTrue(filename.endswith('.jpg'))
        self.assertEqual((width, height), (1200, 675))
        with Image.open(os.path.join(self.folder, filename)) as img:
            self.assertEqual(img.format, 'JPEG')
        self.assertTrue(os.path.exists(os.path.join(self.folder, filename.replace('.jpg', '_card.jpg'))))

    def test_existing_listing_accepts_replacement_photos(self):
        from app.admin import _handle_vehicle_images

        vehicle = Vehicle(year=2012, make='Dodge', model='Challenger', price=8999, mileage=63400)
        db.session.add(vehicle)
        db.session.commit()
        self.assertEqual(list(vehicle.images), [])
        with self.app.test_request_context():
            _handle_vehicle_images(vehicle, [
                FileStorage(stream=BytesIO(b'failed original upload'), filename='old.heic'),
                FileStorage(stream=BytesIO(heic_bytes()), filename='replacement.HEIC'),
            ])
        db.session.refresh(vehicle)

        self.assertEqual(len(vehicle.images), 1)
        self.assertTrue(vehicle.primary_image().is_primary)
        self.assertTrue(vehicle.primary_image_url().endswith('.jpg'))

    def test_edit_existing_listing_saves_and_displays_new_photos(self):
        vehicle = Vehicle(year=2012, make='Dodge', model='Challenger', price=8999, mileage=63400)
        user = User(username='photo-admin')
        user.set_password('test-password-only')
        db.session.add_all([vehicle, user])
        db.session.flush()
        vehicle.ensure_slug()
        db.session.commit()
        client = self.app.test_client()
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = client.post('/admin/login', data={
                'username': user.username, 'password': 'test-password-only',
            })
        self.assertEqual(response.status_code, 302)
        jpeg = BytesIO()
        Image.new('RGB', (800, 600), 'blue').save(jpeg, format='JPEG')
        jpeg.seek(0)
        response = client.post(f'/admin/vehicles/{vehicle.id}/edit', data={
            'year': '2012', 'make': 'Dodge', 'model': 'Challenger',
            'price': '8999', 'mileage': '63400', 'condition': 'used',
            'title_status': 'rebuilt', 'status': 'available', 'drivetrain': '',
            'images': [(BytesIO(heic_bytes()), 'new.HEIC'), (jpeg, 'new.jpg')],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/admin/vehicles')
        db.session.refresh(vehicle)
        self.assertEqual(len(vehicle.images), 2)
        self.assertTrue(vehicle.primary_image().is_primary)
        response = client.get(f'/inventory/{vehicle.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-gallery-count="2"', response.data)
        for photo in vehicle.images:
            self.assertIn(photo.url.encode(), response.data)
            self.assertTrue(os.path.isfile(os.path.join(self.folder, photo.filename)))

    def test_delete_all_vehicle_photos_removes_files_and_rows(self):
        vehicle = Vehicle(year=2012, make='Dodge', model='Challenger', price=8999, mileage=63400)
        other_vehicle = Vehicle(year=2015, make='Honda', model='Civic', price=12000, mileage=50000)
        user = User(username='photo-delete-admin')
        user.set_password('test-password-only')
        db.session.add_all([vehicle, other_vehicle, user])
        db.session.flush()
        images = [
            VehicleImage(vehicle_id=vehicle.id, filename='first.jpg', is_primary=True),
            VehicleImage(vehicle_id=vehicle.id, filename='second.jpg'),
            VehicleImage(vehicle_id=other_vehicle.id, filename='other.jpg'),
        ]
        db.session.add_all(images)
        db.session.commit()

        for filename in ('first.jpg', 'first_thumbnail.jpg', 'second.jpg', 'other.jpg'):
            with open(os.path.join(self.folder, filename), 'wb') as photo_file:
                photo_file.write(b'photo')

        client = self.app.test_client()
        response = client.post('/admin/login', data={
            'username': user.username, 'password': 'test-password-only',
        })
        self.assertEqual(response.status_code, 302)

        response = client.post(f'/admin/vehicles/{vehicle.id}/images/delete-all')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], f'/admin/vehicles/{vehicle.id}/edit')
        self.assertEqual(VehicleImage.query.filter_by(vehicle_id=vehicle.id).count(), 0)
        self.assertEqual(VehicleImage.query.filter_by(vehicle_id=other_vehicle.id).count(), 1)
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'first.jpg')))
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'first_thumbnail.jpg')))
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'second.jpg')))
        self.assertTrue(os.path.isfile(os.path.join(self.folder, 'other.jpg')))

    def test_failed_upload_is_reported_instead_of_silently_skipped(self):
        from flask import get_flashed_messages
        from app.admin import _handle_vehicle_images

        vehicle = Vehicle(year=2012, make='Dodge', model='Challenger', price=8999, mileage=63400)
        db.session.add(vehicle)
        db.session.commit()
        with self.app.test_request_context():
            _handle_vehicle_images(vehicle, [
                FileStorage(stream=BytesIO(b'not an image'), filename='broken.heic'),
            ])
            messages = get_flashed_messages(with_categories=True)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0][0], 'danger')
        self.assertIn('broken.heic', messages[0][1])
        self.assertIn('not added to the listing', messages[0][1])
        db.session.refresh(vehicle)
        self.assertEqual(len(vehicle.images), 0)

    def test_existing_heic_rows_are_converted(self):
        vehicle = Vehicle(year=2020, make='Honda', model='Civic', price=15000, mileage=40000)
        db.session.add(vehicle)
        db.session.flush()
        with open(os.path.join(self.folder, 'old.heic'), 'wb') as fh:
            fh.write(heic_bytes())
        image = VehicleImage(vehicle_id=vehicle.id, filename='old.heic', highlight_status='failed')
        keep = VehicleImage(vehicle_id=vehicle.id, filename='keep.jpg')
        db.session.add_all([image, keep])
        db.session.commit()

        self.assertEqual(convert_heic_vehicle_images(dry_run=True), {'found': 1, 'converted': 0, 'failed': 0})
        self.assertEqual(convert_heic_vehicle_images(), {'found': 1, 'converted': 1, 'failed': 0})

        db.session.refresh(image)
        self.assertEqual(image.filename, 'old.jpg')
        self.assertEqual((image.width, image.height), (1200, 675))
        self.assertFalse(os.path.exists(os.path.join(self.folder, 'old.heic')))
        with Image.open(os.path.join(self.folder, 'old.jpg')) as img:
            self.assertEqual(img.format, 'JPEG')
        self.assertEqual(db.session.get(VehicleImage, keep.id).filename, 'keep.jpg')
        self.assertEqual(convert_heic_vehicle_images()['found'], 0)


if __name__ == '__main__':
    unittest.main()
