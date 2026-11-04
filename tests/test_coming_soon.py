import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.coming_soon_stock_photos import (
    ensure_stock_photo_for_vehicle,
    existing_stock_photo_filename,
    stock_photo_filename,
)
from app.models import Vehicle, VehicleImage
from config import TestingConfig


class ComingSoonTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['SITE_URL'] = 'https://dealer.example'
        self.static_dir = tempfile.TemporaryDirectory()
        self.app.static_folder = self.static_dir.name
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.teaser = self.add_vehicle(
            '2020-Toyota-RAV4-coming-soon',
            is_coming_soon=True,
            price=24800,
        )
        self.available = self.add_vehicle(
            '2018-Honda-Accord-available',
            is_coming_soon=False,
            price=15900,
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.static_dir.cleanup()

    def add_vehicle(self, slug, *, is_coming_soon, price):
        vehicle = Vehicle(
            year=2020 if is_coming_soon else 2018,
            make='Toyota' if is_coming_soon else 'Honda',
            model='RAV4' if is_coming_soon else 'Accord',
            price=price,
            mileage=60000,
            status='available',
            is_coming_soon=is_coming_soon,
            body_style='SUV' if is_coming_soon else 'Sedan',
            slug=slug,
            description='Fresh arrival being prepared for sale.',
            created_at=datetime(2026, 9, 1, 10),
            updated_at=datetime(2026, 9, 20, 14, 30),
        )
        db.session.add(vehicle)
        return vehicle

    def test_coming_soon_page_shows_teaser_without_price(self):
        response = self.client.get('/coming-soon')
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.data, 'html.parser')
        text = page.get_text(' ', strip=True)

        self.assertIn(self.teaser.title, text)
        self.assertIn('Coming Soon', text)
        self.assertNotIn('24,800', text)
        self.assertNotIn('60,000 miles', text)
        self.assertIsNotNone(
            page.select_one(f'a[href="/contact?vehicle_id={self.teaser.id}"]')
        )
        self.assertNotIn(self.available.title, text)

    def test_missing_photos_use_grok_reference_asset_when_available(self):
        self.teaser.vin = '2T3P1RFV0LC123456'
        db.session.commit()
        filename = stock_photo_filename(
            self.teaser.year, self.teaser.make, self.teaser.model,
            self.teaser.body_style,
        )
        image_path = Path(self.app.static_folder) / filename
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b'generated image placeholder')

        for path in ('/coming-soon', '/'):
            with self.subTest(path=path):
                page = BeautifulSoup(self.client.get(path).data, 'html.parser')
                card = page.select_one('.coming-soon-card')
                self.assertNotIn(self.teaser.vin, str(card))
                self.assertIn('/static/' + filename, card.img['src'])
                self.assertIn('AI-generated reference image only', card.get_text())
                self.assertIn('not the actual vehicle', card.img['alt'])

    def test_missing_generated_photo_shows_placeholder_and_disclaimer(self):
        page = BeautifulSoup(self.client.get('/coming-soon').data, 'html.parser')
        card = page.select_one('.coming-soon-card')

        self.assertIn('vehicle-placeholder.jpg', card.img['src'])
        self.assertIn('AI-generated reference image not available yet', card.get_text())

    def test_actual_photos_take_precedence_over_generated_reference(self):
        filename = stock_photo_filename(
            self.teaser.year, self.teaser.make, self.teaser.model,
            self.teaser.body_style,
        )
        image_path = Path(self.app.static_folder) / filename
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b'generated image placeholder')
        db.session.add(VehicleImage(
            vehicle_id=self.teaser.id, filename='actual-rav4.jpg', is_primary=True,
        ))
        db.session.commit()
        for path in ('/coming-soon', '/'):
            with self.subTest(path=path):
                page = BeautifulSoup(self.client.get(path).data, 'html.parser')
                card = page.select_one('.coming-soon-card')
                self.assertIsNone(card.select_one('.stock-photo-note'))
                self.assertEqual(card.img['src'], '/static/uploads/vehicles/actual-rav4.jpg')

    def test_sale_inventory_does_not_use_coming_soon_reference_photos(self):
        page = BeautifulSoup(self.client.get('/inventory').data, 'html.parser')
        self.assertIsNone(page.select_one('.stock-photo-note'))

    def test_grok_command_generates_one_image_per_listed_configuration(self):
        duplicate = self.add_vehicle(
            '2020-Toyota-RAV4-coming-soon-duplicate',
            is_coming_soon=True,
            price=23000,
        )
        duplicate.exterior_color = 'Blue'
        self.teaser.exterior_color = 'Blue'
        different = self.add_vehicle(
            '2020-Toyota-RAV4-coming-soon-red',
            is_coming_soon=True,
            price=24000,
        )
        different.exterior_color = 'Red'
        different.trim = 'XLE'
        image_vehicle = self.add_vehicle(
            '2019-Acura-MDX-coming-soon',
            is_coming_soon=True,
            price=27000,
        )
        db.session.add(VehicleImage(
            vehicle=image_vehicle, filename='actual-mdx.jpg', is_primary=True,
        ))
        db.session.commit()
        self.app.config['XAI_API_KEY'] = 'test-api-key'

        with patch(
            'app.coming_soon_stock_photos.generate_stock_photo',
            return_value='images/coming-soon-stock/2020-toyota-rav4.webp',
        ) as generate:
            result = self.app.test_cli_runner().invoke(
                args=['generate-coming-soon-stock-photos'],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(generate.call_count, 2)
        generated_colors = {
            call.kwargs['exterior_color'] for call in generate.call_args_list
        }
        self.assertEqual(generated_colors, {'Blue', 'Red'})
        red_call = next(
            call for call in generate.call_args_list
            if call.kwargs['exterior_color'] == 'Red'
        )
        self.assertEqual(red_call.args[:5], (
            2020, 'Toyota', 'RAV4', 'test-api-key', self.app.static_folder,
        ))
        self.assertEqual(red_call.kwargs['trim'], 'XLE')
        self.assertEqual(red_call.kwargs['body_style'], 'SUV')

    def test_grok_command_reuses_saved_image_for_the_same_configuration(self):
        twin = self.add_vehicle(
            '2020-Toyota-RAV4-coming-soon-twin',
            is_coming_soon=True,
            price=23000,
        )
        self.teaser.exterior_color = 'Blueprint'
        self.teaser.trim = 'XLE'
        twin.exterior_color = 'Blueprint'
        twin.trim = 'XLE'
        filename = stock_photo_filename(
            self.teaser.year, self.teaser.make, self.teaser.model,
            **{
                'body_style': self.teaser.body_style,
                'exterior_color': 'Blueprint',
                'trim': 'XLE',
            },
        )
        image_path = Path(self.app.static_folder) / filename
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b'generated image placeholder')
        self.app.config['XAI_API_KEY'] = 'test-api-key'

        with patch('app.coming_soon_stock_photos.generate_stock_photo') as generate:
            result = self.app.test_cli_runner().invoke(
                args=['generate-coming-soon-stock-photos'],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        generate.assert_not_called()
        self.assertEqual(existing_stock_photo_filename(self.teaser), filename)
        self.assertEqual(existing_stock_photo_filename(twin), filename)

    def test_vehicle_save_regenerates_when_color_or_trim_changes(self):
        self.app.config['XAI_API_KEY'] = 'test-api-key'
        filename = stock_photo_filename(
            self.teaser.year, self.teaser.make, self.teaser.model,
            self.teaser.body_style,
        )
        image_path = Path(self.app.static_folder) / filename

        def save_generated_image(*args, **kwargs):
            generated = stock_photo_filename(*args[:3], **kwargs)
            path = Path(self.app.static_folder) / generated
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'generated image placeholder')
            return generated

        with patch(
            'app.coming_soon_stock_photos.generate_stock_photo',
            side_effect=save_generated_image,
        ) as generate:
            self.assertEqual(ensure_stock_photo_for_vehicle(self.teaser), filename)
            self.assertEqual(ensure_stock_photo_for_vehicle(self.teaser), filename)
            self.teaser.exterior_color = 'Silver'
            self.teaser.trim = 'Limited'
            updated = ensure_stock_photo_for_vehicle(self.teaser)

        self.assertEqual(generate.call_count, 2)
        self.assertNotEqual(updated, filename)
        self.assertEqual(existing_stock_photo_filename(self.teaser), updated)

    def test_teaser_is_not_in_sale_inventory_homepage_or_feed(self):
        inventory = BeautifulSoup(self.client.get('/inventory').data, 'html.parser')
        home = BeautifulSoup(self.client.get('/').data, 'html.parser')
        feed = self.client.get('/feeds/vehicles.xml').get_data(as_text=True)

        self.assertIsNone(inventory.select_one(f'a[href="/inventory/{self.teaser.slug}"]'))
        self.assertIsNotNone(inventory.select_one(f'a[href="/inventory/{self.available.slug}"]'))
        self.assertIn(self.teaser.title, home.get_text())
        self.assertNotIn(self.teaser.slug, feed)
        self.assertIn(self.available.slug, feed)
        self.assertEqual(self.client.get(f'/inventory/{self.teaser.slug}').status_code, 404)

    def test_contact_link_prefills_the_vehicle_interest(self):
        response = self.client.get(f'/contact?vehicle_id={self.teaser.id}')
        self.assertEqual(response.status_code, 200)
        page = BeautifulSoup(response.data, 'html.parser')

        self.assertEqual(
            page.select_one('input[name="vehicle_id"]')['value'],
            str(self.teaser.id),
        )
        self.assertIn(self.teaser.title, page.select_one('textarea[name="message"]').text)

    def test_sitemap_advertises_page_not_unlisted_teaser_detail(self):
        response = self.client.get('/sitemap.xml').get_data(as_text=True)
        self.assertIn('https://dealer.example/coming-soon', response)
        self.assertNotIn(f'https://dealer.example/inventory/{self.teaser.slug}', response)
        self.assertIn(f'https://dealer.example/inventory/{self.available.slug}', response)


if __name__ == '__main__':
    unittest.main()
