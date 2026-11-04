import hashlib
import unittest
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from bs4 import BeautifulSoup
from pypdf import PdfReader

from app import create_app, db
from app.models import User, Vehicle, VehicleSaleReceipt
from app.mvr_forms import (
    DAMAGE_25_NO,
    DAMAGE_25_YES,
    FLOOD_NO,
    FLOOD_YES,
    ODOMETER_DISCREPANCY,
    ODOMETER_EXCESS,
    RECONSTRUCTED_NO,
    RECONSTRUCTED_YES,
    SALVAGE_NO,
    SALVAGE_YES,
    THEFT_NO,
    THEFT_YES,
    build_mvr_180,
    build_mvr_181,
    choose_series_model,
    damage_answers,
    fitted_text,
    format_mileage,
    parse_postal_address,
    text_fits,
    within_five_model_years,
)
from config import TestingConfig


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _content(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    pieces = [reader.pages[0].extract_text() or '']
    contents = reader.pages[0].get_contents()
    if contents is not None:
        pieces.append(contents.get_data().decode('latin1', 'replace'))
    return '\n'.join(pieces)


def _xobject(field_name):
    token = ''.join(
        character if character.isalnum() else '_' for character in field_name
    ).strip('_')
    while '__' in token:
        token = token.replace('__', '_')
    return f'/Fm_{token}'


class MvrFormUnitTests(unittest.TestCase):
    def test_five_model_year_window_matches_the_form_definition(self):
        on_date = datetime(2026, 11, 4).date()
        self.assertTrue(within_five_model_years(2022, on_date))
        self.assertTrue(within_five_model_years(2026, on_date))
        self.assertFalse(within_five_model_years(2021, on_date))
        self.assertFalse(within_five_model_years(None, on_date))

    def test_mileage_has_no_tenths(self):
        self.assertEqual(format_mileage(82000), '82,000')
        self.assertEqual(format_mileage(0), '0')
        self.assertEqual(format_mileage(None), '')
        self.assertNotIn('.', format_mileage(82000))

    def test_address_parser_does_not_guess(self):
        self.assertEqual(
            parse_postal_address('100 Main St, Sanford, NC 27330'),
            ('100 Main St', 'Sanford', 'NC', '27330'),
        )
        self.assertEqual(
            parse_postal_address('100 Main St\nSanford, NC 27330-1234'),
            ('100 Main St', 'Sanford', 'NC', '27330-1234'),
        )
        self.assertEqual(
            parse_postal_address('123 Main Street, Sanford, NC'),
            ('123 Main Street', 'Sanford', 'NC', ''),
        )
        self.assertEqual(
            parse_postal_address('100 Main St, Sanford NC 27330'),
            ('100 Main St', 'Sanford', 'NC', '27330'),
        )
        street, city, state, zip_code = parse_postal_address('PO Box 5')
        self.assertEqual((street, city, state, zip_code), ('PO Box 5', '', '', ''))

    def test_damage_answers_follow_title_brand_not_guesses(self):
        on_date = datetime(2026, 11, 4).date()
        recent_rebuilt = damage_answers('rebuilt', 2024, on_date)
        self.assertTrue(recent_rebuilt['damage_25'])
        self.assertTrue(recent_rebuilt['salvage'])
        self.assertFalse(recent_rebuilt['reconstructed'])
        self.assertIsNone(recent_rebuilt['flood'])
        self.assertIsNone(recent_rebuilt['theft'])

        old_salvage = damage_answers('salvage', 2010, on_date)
        self.assertIsNone(old_salvage['damage_25'])
        self.assertTrue(old_salvage['salvage'])
        self.assertTrue(old_salvage['reconstructed'])

        clean = damage_answers('clean', 2024, on_date)
        self.assertIsNone(clean['damage_25'])
        self.assertFalse(clean['salvage'])
        self.assertFalse(clean['reconstructed'])
        self.assertIsNone(clean['flood'])

        overridden = damage_answers('salvage', 2024, on_date, reconstructed='no')
        self.assertFalse(overridden['reconstructed'])
        self.assertTrue(overridden['salvage'])
        forced = damage_answers('clean', 2024, on_date, reconstructed='yes')
        self.assertTrue(forced['reconstructed'])
        self.assertFalse(forced['salvage'])

    def test_series_model_and_body_style_stay_readable(self):
        self.assertEqual(choose_series_model('Camry', 'SE', 64.5), 'CAMRY SE')
        long_name = choose_series_model('Grand Cherokee', 'Laredo Sport', 64.5)
        self.assertEqual(long_name, 'GRAND CHEROKEE')
        self.assertTrue(text_fits(long_name, 64.5, 8.5))
        body = fitted_text(
            'Sport Utility Vehicle', 55.7, extra_options=('SUV',),
        )
        self.assertEqual(body, 'SPORT UTILITY')
        self.assertTrue(text_fits(body, 55.7, 8.5))
        self.assertNotIn('VEHIC', body.replace('VEHICLE', ''))


class MvrFormPdfTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(username='mvr-admin')
        self.user.set_password('mvr-test-password')
        self.vehicle = Vehicle(
            slug='mvr-camry', year=2024, make='Toyota', model='Camry', trim='SE',
            vin='4t1bf1fk5ju000001', body_style='Sedan', stock_number='A 100',
            price=18900, mileage=82000, title_status='rebuilt', status='sold',
            sold_at=datetime(2026, 11, 4, 15, 30),
        )
        db.session.add_all([self.user, self.vehicle])
        db.session.commit()
        self.receipt = VehicleSaleReceipt(
            vehicle=self.vehicle,
            buyer_name='  Jane   Q. Buyer ',
            buyer_address='100 Main St, Apt 2, Sanford, NC 27330',
            vehicle_price=Decimal('18000.00'),
            amount_received=Decimal('18000.00'),
        )
        db.session.add(self.receipt)
        db.session.commit()
        self.client = self.app.test_client()
        self.template_hashes = {
            self.app.config['MVR_180_PDF']: _sha256(self.app.config['MVR_180_PDF']),
            self.app.config['MVR_181_PDF']: _sha256(self.app.config['MVR_181_PDF']),
        }

    def tearDown(self):
        for path, digest in self.template_hashes.items():
            self.assertEqual(_sha256(path), digest, path)
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def login(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': 'mvr-admin',
                'password': 'mvr-test-password',
            })
        self.assertEqual(response.status_code, 302)

    def test_routes_require_login_and_list_buttons(self):
        odometer = self.client.get(f'/admin/vehicles/{self.vehicle.id}/mvr-180')
        damage = self.client.get(f'/admin/vehicles/{self.vehicle.id}/mvr-181')
        self.assertEqual(odometer.status_code, 302)
        self.assertIn('/admin/login', odometer.headers['Location'])
        self.assertEqual(damage.status_code, 302)

        self.login()
        edit = BeautifulSoup(self.client.get(f'/admin/vehicles/{self.vehicle.id}/edit').data, 'html.parser')
        listing = BeautifulSoup(self.client.get('/admin/vehicles').data, 'html.parser')
        sale = BeautifulSoup(self.client.get(f'/admin/vehicles/{self.vehicle.id}/sale').data, 'html.parser')
        for page in (edit, listing, sale):
            self.assertIsNotNone(page.select_one(f'a[href="/admin/vehicles/{self.vehicle.id}/mvr-180"]'))
            self.assertIsNotNone(page.select_one(f'a[href="/admin/vehicles/{self.vehicle.id}/mvr-181"]'))

    def test_odometer_form_defaults_to_saved_mileage_and_prints_the_entered_reading(self):
        self.login()
        page = self.client.get(f'/admin/vehicles/{self.vehicle.id}/mvr-180')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.mimetype, 'text/html')
        html = BeautifulSoup(page.data, 'html.parser')
        field = html.select_one('input[name="odometer"]')
        self.assertIsNotNone(field)
        self.assertEqual(field.get('value'), '82000')

        invalid = self.client.post(f'/admin/vehicles/{self.vehicle.id}/mvr-180', data={
            'odometer': '82.5',
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(invalid.mimetype, 'text/html')
        self.assertIn(b'whole number', invalid.data.lower())

        response = self.client.post(f'/admin/vehicles/{self.vehicle.id}/mvr-180', data={
            'odometer': '82550',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('MVR-180-A-100.pdf', response.headers.get('Content-Disposition', ''))
        content = _content(response.data)
        self.assertIn('82,550', content)
        self.assertNotIn('82,000', content)
        for value in (
            '2024', 'TOYOTA', 'SEDAN', 'CAMRY SE', '4T1BF1FK5JU000001',
            'Marshall Auto, LLC', '360 Wilson Rd', 'Sanford', 'NC',
            '27332', '11/04/2026', 'Jane Q. Buyer', '100 Main St, Apt 2', '27330',
        ):
            self.assertIn(value, content)
        self.assertNotIn(_xobject(ODOMETER_EXCESS), content)
        self.assertNotIn(_xobject(ODOMETER_DISCREPANCY), content)
        reader = PdfReader(BytesIO(response.data))
        self.assertIsNone(reader.pages[0].get('/Annots'))
        self.assertEqual(len(reader.pages), 1)

    def test_rebuilt_recent_vehicle_checks_only_known_damage_answers(self):
        pdf_bytes = build_mvr_181(self.vehicle, self.app.config)
        content = _content(pdf_bytes)
        self.assertIn('4T1BF1FK5JU000001', content)
        self.assertIn('360 Wilson Rd, Sanford, NC 27332', content)
        self.assertIn('11/04/2026', content)
        for checked in (DAMAGE_25_YES, SALVAGE_YES, RECONSTRUCTED_NO):
            self.assertIn(_xobject(checked), content)
        for blank in (
            DAMAGE_25_NO, SALVAGE_NO, FLOOD_YES, FLOOD_NO, THEFT_YES, THEFT_NO,
            RECONSTRUCTED_YES,
        ):
            self.assertNotIn(_xobject(blank), content)
        self.assertNotIn('Front bumper', content)

    def test_clean_and_older_titles_do_not_invent_damage(self):
        on_date = datetime(2026, 11, 4).date()
        clean = Vehicle(
            slug='clean-civic', year=2024, make='Honda', model='Civic',
            price=9000, mileage=12000, title_status='clean', status='available',
        )
        old = Vehicle(
            slug='old-truck', year=2010, make='Ford', model='F-150',
            price=4000, mileage=150000, title_status='salvage', status='available',
        )
        clean_content = _content(build_mvr_181(clean, self.app.config, on_date))
        self.assertIn(_xobject(SALVAGE_NO), clean_content)
        self.assertIn(_xobject(RECONSTRUCTED_NO), clean_content)
        self.assertNotIn(_xobject(DAMAGE_25_YES), clean_content)
        self.assertNotIn(_xobject(DAMAGE_25_NO), clean_content)
        self.assertNotIn(_xobject(FLOOD_NO), clean_content)
        self.assertNotIn('11/04/2026', clean_content)

        old_content = _content(build_mvr_181(old, self.app.config, on_date))
        self.assertIn(_xobject(SALVAGE_YES), old_content)
        self.assertIn(_xobject(RECONSTRUCTED_YES), old_content)
        self.assertNotIn(_xobject(RECONSTRUCTED_NO), old_content)
        self.assertNotIn(_xobject(DAMAGE_25_YES), old_content)
        self.assertNotIn(_xobject(DAMAGE_25_NO), old_content)

        old.reconstructed = 'no'
        overridden = _content(build_mvr_181(old, self.app.config, on_date))
        self.assertIn(_xobject(RECONSTRUCTED_NO), overridden)
        self.assertNotIn(_xobject(RECONSTRUCTED_YES), overridden)

    def test_missing_template_redirects_without_writing_a_file(self):
        self.login()
        with patch.dict(self.app.config, {'MVR_180_PDF': '/tmp/missing-mvr-180.pdf'}):
            response = self.client.post(f'/admin/vehicles/{self.vehicle.id}/mvr-180', data={
                'odometer': '82000',
            })
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'/admin/vehicles/{self.vehicle.id}/edit', response.headers['Location'])
