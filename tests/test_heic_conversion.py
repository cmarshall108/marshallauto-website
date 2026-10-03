import os
import unittest
from io import BytesIO
from tempfile import TemporaryDirectory

import pillow_heif
from PIL import Image
from werkzeug.datastructures import FileStorage

from app import create_app, db
from app.models import Vehicle, VehicleImage
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
