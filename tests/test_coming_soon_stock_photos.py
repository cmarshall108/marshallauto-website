import base64
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app import create_app
from app.coming_soon_stock_photos import (
    generate_stock_photo,
    stock_photo_filename,
)
from config import TestingConfig


class ComingSoonStockPhotoTests(unittest.TestCase):
    def test_filename_changes_when_color_trim_or_year_changes(self):
        silver = stock_photo_filename(
            2014, 'Chevrolet', 'Cruze', 'Sedan',
            trim='LT', exterior_color='Silver Ice', interior_color='Jet Black',
        )
        self.assertEqual(
            stock_photo_filename(
                2014, 'Chevrolet', 'Cruze', 'Sedan',
                trim='LT', exterior_color='silver ice', interior_color='Jet Black',
            ),
            silver,
        )
        self.assertTrue(silver.startswith('images/coming-soon-stock/2014-chevrolet-cruze-sedan-'))
        self.assertTrue(silver.endswith('.webp'))
        self.assertNotEqual(
            stock_photo_filename(
                2015, 'Chevrolet', 'Cruze', 'Sedan',
                trim='LT', exterior_color='Silver Ice',
            ),
            silver,
        )
        self.assertNotEqual(
            stock_photo_filename(
                2014, 'Chevrolet', 'Cruze', 'Sedan',
                trim='LT', exterior_color='Red',
            ),
            silver,
        )
        self.assertNotEqual(
            stock_photo_filename(2014, 'Chevrolet', 'Cruze', 'Hatchback', trim='LT'),
            stock_photo_filename(2014, 'Chevrolet', 'Cruze', 'Sedan', trim='LT'),
        )

    def test_generated_photo_is_cached_as_optimized_webp(self):
        app = create_app(TestingConfig)
        with tempfile.TemporaryDirectory() as static_folder:
            image = Image.new('RGB', (32, 24), color='silver')
            image_buffer = BytesIO()
            image.save(image_buffer, format='PNG')
            encoded = base64.b64encode(image_buffer.getvalue()).decode('ascii')
            api_response = json.dumps({
                'data': [{'b64_json': encoded}],
            }).encode('utf-8')

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self, _limit):
                    return api_response

            with patch(
                'app.coming_soon_stock_photos.urllib.request.urlopen',
                return_value=Response(),
            ) as urlopen:
                filename = generate_stock_photo(
                    2014, 'Chevrolet', 'Cruze', 'test-api-key', static_folder,
                    'Sedan', trim='LT', exterior_color='Silver Ice',
                    interior_color='Jet Black', doors=4, drivetrain='FWD',
                    fuel_type='Gasoline', engine='1.4L Turbo',
                    transmission='6-speed automatic',
                )

            request = urlopen.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(request.full_url, 'https://api.x.ai/v1/images/generations')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-api-key')
            self.assertEqual(body['model'], 'grok-imagine-image-2.0')
            self.assertEqual(body['response_format'], 'b64_json')
            self.assertIn('2014 Chevrolet Cruze with a Sedan body style', body['prompt'])
            self.assertIn('Trim: LT', body['prompt'])
            self.assertIn('Exterior color: Silver Ice', body['prompt'])
            self.assertIn('Interior color: Jet Black', body['prompt'])
            self.assertIn('Doors: 4', body['prompt'])
            self.assertIn('Drivetrain: FWD', body['prompt'])
            self.assertIn('Fuel type: Gasoline', body['prompt'])
            self.assertIn('Engine: 1.4L Turbo', body['prompt'])
            self.assertIn('Transmission: 6-speed automatic', body['prompt'])
            self.assertNotIn('neutral factory-style paint color', body['prompt'])

            image_path = Path(static_folder) / filename
            self.assertTrue(image_path.is_file())
            self.assertEqual(os.stat(image_path).st_mode & 0o777, 0o644)
            with Image.open(image_path) as generated:
                self.assertEqual(generated.format, 'WEBP')
                self.assertEqual(generated.size, (1200, 800))

    def test_command_reports_missing_grok_credential(self):
        app = create_app(TestingConfig)
        app.config['XAI_API_KEY'] = ''

        result = app.test_cli_runner().invoke(
            args=['generate-coming-soon-stock-photos'],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('Set XAI_API_KEY', result.output)


if __name__ == '__main__':
    unittest.main()
