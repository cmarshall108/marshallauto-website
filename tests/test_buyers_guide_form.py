import hashlib
import re
import unittest
from io import BytesIO

from bs4 import BeautifulSoup
from pypdf import PdfReader

from app import create_app, db
from app.buyers_guide_form import (
    FORM_CODE,
    GUIDE_AS_IS,
    GUIDE_IMPLIED,
    build_overlay_pdf,
    default_overlay_values,
    normalize_guide_type,
    parse_overlay_submission,
)
from app.models import User, Vehicle
from config import TestingConfig


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _content(pdf_bytes):
    reader = PdfReader(BytesIO(pdf_bytes))
    pieces = []
    for page in reader.pages:
        pieces.append(page.extract_text() or '')
        contents = page.get_contents()
        if contents is not None:
            pieces.append(contents.get_data().decode('latin1', 'replace'))
    return '\n'.join(pieces)


def _mark_near(pdf_bytes, page_index, rect):
    page = PdfReader(BytesIO(pdf_bytes)).pages[page_index]
    contents = page.get_contents().get_data().decode('latin1', 'replace')
    return any(
        rect[0] - 0.5 <= float(x_pos) <= rect[2] + 0.5
        and rect[1] - 0.5 <= float(y_pos) <= rect[3] + 0.5
        for x_pos, y_pos in re.findall(
            r'1 0 0 1 ([0-9.]+) ([0-9.]+) Tm\s+\(n\) Tj', contents,
        )
    )


def _as_is_warranty_rect(template_path, index=0):
    reader = PdfReader(template_path)
    matches = []
    for annot in reader.pages[0].get('/Annots') or []:
        obj = annot.get_object()
        parent = obj.get('/Parent')
        name = obj.get('/T')
        if not name and parent is not None:
            name = parent.get_object().get('/T')
        if str(name or '') != 'Warranty[0]':
            continue
        matches.append([float(value) for value in obj.get('/Rect')])
    matches.sort(key=lambda rect: rect[1], reverse=True)
    return matches[index]


class BuyersGuideUnitTests(unittest.TestCase):
    def test_guide_type_defaults_to_as_is(self):
        self.assertEqual(normalize_guide_type(None), GUIDE_AS_IS)
        self.assertEqual(normalize_guide_type('implied'), GUIDE_IMPLIED)
        self.assertEqual(normalize_guide_type('IMPLIED-WARRANTIES'), GUIDE_IMPLIED)

    def test_parse_requires_limited_percents(self):
        values, errors = parse_overlay_submission({
            'guide_type': 'as_is',
            'warranty': 'warranty_dealer',
            'dealer_warranty': 'dealer_limited',
            'make': 'Toyota',
            'model': 'Camry',
            'year': '2024',
            'vin': '4T1BF1FK5JU000001',
            'dealer_name': 'Marshall Auto, LLC',
        })
        self.assertTrue(values['warranty_dealer'])
        self.assertTrue(values['dealer_limited'])
        self.assertTrue(any('Labor percent' in error for error in errors))
        self.assertTrue(any('Parts percent' in error for error in errors))


class BuyersGuidePdfTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.user = User(username='bg-admin')
        self.user.set_password('bg-test-password')
        self.vehicle = Vehicle(
            slug='bg-camry', year=2024, make='Toyota', model='Camry', trim='SE',
            vin='4t1bf1fk5ju000001', body_style='Sedan', stock_number='BG 100',
            price=18900, mileage=82000, title_status='clean', status='available',
        )
        db.session.add_all([self.user, self.vehicle])
        db.session.commit()
        self.client = self.app.test_client()
        self.template_path = self.app.config['BUYERS_GUIDE_PDF']
        self.template_hash = _sha256(self.template_path)

    def tearDown(self):
        self.assertEqual(_sha256(self.template_path), self.template_hash)
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self):
        return self.client.post('/admin/login', data={
            'username': 'bg-admin',
            'password': 'bg-test-password',
        }, follow_redirects=True)

    def test_defaults_prefill_vehicle_and_dealer(self):
        values = default_overlay_values(self.vehicle, self.app.config)
        self.assertEqual(values['guide_type'], GUIDE_AS_IS)
        self.assertEqual(values['make'], 'Toyota')
        self.assertEqual(values['model'], 'Camry SE')
        self.assertEqual(values['year'], '2024')
        self.assertEqual(values['vin'], '4T1BF1FK5JU000001')
        self.assertTrue(values['warranty_primary'])
        self.assertFalse(values['warranty_dealer'])
        self.assertIn('Marshall Auto', values['dealer_name'])
        self.assertIn('360 Wilson', values['dealer_address'])
        self.assertIn('Sanford', values['dealer_address'])
        self.assertTrue(values['dealer_phone'])
        self.assertTrue(values['dealer_email'])
        self.assertIn(values['dealer_name'], values['complaint_contact'])

    def test_generated_pdf_is_two_pages_and_leaves_blank_unchanged(self):
        values = default_overlay_values(self.vehicle, self.app.config)
        pdf_bytes = build_overlay_pdf(self.template_path, values)
        reader = PdfReader(BytesIO(pdf_bytes))
        self.assertEqual(len(reader.pages), 2)
        content = _content(pdf_bytes)
        self.assertIn('TOYOTA', content.upper())
        self.assertIn('4T1BF1FK5JU000001', content.upper())
        self.assertIn('MARSHALL AUTO', content.upper())
        self.assertTrue(
            _mark_near(pdf_bytes, 0, _as_is_warranty_rect(self.template_path, 0)),
        )
        # No live form widgets remain on the filled copy.
        for page in reader.pages:
            self.assertFalse(page.get('/Annots'))

    def test_admin_editor_prefills_and_generates_download(self):
        self._login()
        page = self.client.get(f'/admin/vehicles/{self.vehicle.id}/buyers-guide')
        self.assertEqual(page.status_code, 200)
        soup = BeautifulSoup(page.data, 'html.parser')
        self.assertEqual(soup.select_one('input[name="make"]')['value'], 'Toyota')
        self.assertEqual(
            soup.select_one('input[name="vin"]')['value'], '4T1BF1FK5JU000001',
        )
        self.assertTrue(soup.select_one('input[name="warranty"][value="warranty_primary"]'))
        dealer_name = soup.select_one('input[name="dealer_name"]')['value']
        self.assertIn('Marshall Auto', dealer_name)

        preview = self.client.post(f'/admin/vehicles/{self.vehicle.id}/buyers-guide', data={
            'guide_type': 'as_is',
            'make': 'Toyota',
            'model': 'Camry SE',
            'year': '2024',
            'vin': '4T1BF1FK5JU000001',
            'warranty': 'warranty_primary',
            'dealer_name': dealer_name,
            'dealer_address': soup.select_one('input[name="dealer_address"]')['value'],
            'dealer_phone': soup.select_one('input[name="dealer_phone"]')['value'],
            'dealer_email': soup.select_one('input[name="dealer_email"]')['value'],
            'complaint_contact': soup.select_one('input[name="complaint_contact"]')['value'],
        })
        self.assertEqual(preview.status_code, 200)
        frame = BeautifulSoup(preview.data, 'html.parser').select_one('#mvr-output iframe')
        self.assertIsNotNone(frame)
        pdf_response = self.client.get(frame['src'])
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response.mimetype, 'application/pdf')
        self.assertEqual(len(PdfReader(BytesIO(pdf_response.data)).pages), 2)
        self.assertIn('/admin/mvr-preview/', frame['src'])
