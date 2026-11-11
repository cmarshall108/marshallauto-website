"""Fill the official FTC Buyers Guide from an inventory vehicle.

The blank multi-page template is opened read-only and never modified. Staff pick
the AS IS or IMPLIED WARRANTIES front, edit any boxes, and a flattened copy is
generated with the matching front page plus the dealer back page.
"""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from app.mvr_forms import download_name as _mvr_download_name
from app.mvr_forms import fit_font_size, pdf_text

_FONT = 'Helvetica-Bold'
_FIELD_PADDING = 3.5
_PAGE_WIDTH = 612.0
_PAGE_HEIGHT = 792.0

FORM_CODE = 'Buyers-Guide'
GUIDE_AS_IS = 'as_is'
GUIDE_IMPLIED = 'implied'
_GUIDE_TYPES = {GUIDE_AS_IS, GUIDE_IMPLIED}

# Short field names as authored on the blank form (per page).
MAKE = 'VehicleMake[0]'
MODEL = 'Model[0]'
YEAR = 'Year[0]'
VIN = 'VIN[0]'
WARRANTY = 'Warranty[0]'
DEALER_WARRANTY = 'DealerWarranty[0]'
LABOR = 'Labor[0]'
PARTS = 'Parts[0]'
SYSTEMS = tuple(f'SystemsCovered{index}[0]' for index in range(1, 6))
DURATIONS = tuple(f'Duration{index}[0]' for index in range(1, 6))
NON_DEALER = 'NonDealerWarranty[0]'
SERVICE_CONTRACT = 'ServiceContract[0]'
DEALER_NAME = 'DealerName[0]'
DEALER_ADDRESS = 'DealerAddress[0]'
DEALER_PHONE = 'DealerPhone[0]'
DEALER_EMAIL = 'DealerEmail[0]'
COMPLAINT_CONTACT = 'ComplaintContact[0]'

_BLANK_IMAGES = {
    GUIDE_AS_IS: 'images/buyers-guide-as-is-blank.png',
    GUIDE_IMPLIED: 'images/buyers-guide-implied-blank.png',
    'back': 'images/buyers-guide-back-blank.png',
}

_FRONT_PAGE = {
    GUIDE_AS_IS: 0,
    GUIDE_IMPLIED: 1,
}
_BACK_PAGE = 2


def download_name(vehicle):
    return _mvr_download_name(FORM_CODE, vehicle)


def normalize_guide_type(value):
    token = (value or GUIDE_AS_IS).strip().lower().replace('-', '_')
    if token in {'implied', 'implied_warranties', 'implied_only'}:
        return GUIDE_IMPLIED
    return GUIDE_AS_IS


def _dealer_address(config):
    locality = ' '.join(
        part for part in (
            pdf_text(config.get('BUSINESS_STATE')),
            pdf_text(config.get('BUSINESS_ZIP')),
        ) if part
    )
    if locality and len(locality.split()[0]) == 2:
        pieces = locality.split()
        pieces[0] = pieces[0].upper()
        locality = ' '.join(pieces)
    city_line = ', '.join(
        part for part in (pdf_text(config.get('BUSINESS_CITY')), locality) if part
    )
    return ', '.join(
        part for part in (pdf_text(config.get('BUSINESS_ADDRESS')), city_line) if part
    )


def _vehicle_defaults(vehicle):
    vin = pdf_text(getattr(vehicle, 'vin', '')).upper().replace(' ', '')
    model = ' '.join(
        part for part in (
            pdf_text(getattr(vehicle, 'model', '')),
            pdf_text(getattr(vehicle, 'trim', '')),
        ) if part
    )
    year = getattr(vehicle, 'year', None)
    return {
        'make': pdf_text(getattr(vehicle, 'make', '')),
        'model': model,
        'year': str(int(year)) if year else '',
        'vin': vin,
    }


def default_overlay_values(vehicle, config, guide_type=GUIDE_AS_IS):
    """Prefill from inventory and business settings; warranty defaults to AS IS."""
    identity = _vehicle_defaults(vehicle)
    name = pdf_text(config.get('BUSINESS_NAME'))
    phone = pdf_text(config.get('BUSINESS_PHONE'))
    complaint = ', '.join(part for part in (name, phone) if part)
    values = {
        'guide_type': normalize_guide_type(guide_type),
        'make': identity['make'],
        'model': identity['model'],
        'year': identity['year'],
        'vin': identity['vin'],
        'warranty_primary': True,
        'warranty_dealer': False,
        'dealer_full': False,
        'dealer_limited': False,
        'labor': '',
        'parts': '',
        'systems1': '',
        'duration1': '',
        'systems2': '',
        'duration2': '',
        'systems3': '',
        'duration3': '',
        'systems4': '',
        'duration4': '',
        'systems5': '',
        'duration5': '',
        'manufacturer_warranty': False,
        'manufacturer_used_warranty': False,
        'other_used_warranty': False,
        'service_contract': False,
        'dealer_name': name,
        'dealer_address': _dealer_address(config),
        'dealer_phone': phone,
        'dealer_email': pdf_text(config.get('BUSINESS_EMAIL')),
        'complaint_contact': complaint,
    }
    return values


def _text_field(key, label, name, max_length, sheet, index=0):
    return {
        'key': key,
        'kind': 'text',
        'label': label,
        'pdf': name,
        'group': '',
        'max_length': max_length,
        'index': index,
        'sheet': sheet,
    }


def _check_field(key, label, name, group, sheet, index=0):
    return {
        'key': key,
        'kind': 'checkbox',
        'label': label,
        'pdf': name,
        'group': group,
        'max_length': 0,
        'index': index,
        'sheet': sheet,
    }


def _overlay_specs(guide_type):
    guide_type = normalize_guide_type(guide_type)
    primary_label = (
        'Implied warranties only'
        if guide_type == GUIDE_IMPLIED
        else 'AS IS — no dealer warranty'
    )
    # Implied front has four systems rows; AS IS has five.
    system_count = 4 if guide_type == GUIDE_IMPLIED else 5
    specs = [
        _text_field('make', 'Vehicle make', MAKE, 40, 'front'),
        _text_field('model', 'Model', MODEL, 40, 'front'),
        _text_field('year', 'Year', YEAR, 4, 'front'),
        _text_field('vin', 'VIN', VIN, 17, 'front'),
        _check_field('warranty_primary', primary_label, WARRANTY, 'warranty', 'front', 0),
        _check_field('warranty_dealer', 'Dealer warranty', WARRANTY, 'warranty', 'front', 1),
        _check_field('dealer_full', 'Full warranty', DEALER_WARRANTY, 'dealer_warranty', 'front', 0),
        _check_field(
            'dealer_limited', 'Limited warranty', DEALER_WARRANTY, 'dealer_warranty', 'front', 1,
        ),
        _text_field('labor', 'Labor percent', LABOR, 3, 'front'),
        _text_field('parts', 'Parts percent', PARTS, 3, 'front'),
    ]
    for index in range(1, system_count + 1):
        specs.append(
            _text_field(
                f'systems{index}',
                f'Systems covered {index}',
                SYSTEMS[index - 1],
                80,
                'front',
            ),
        )
        specs.append(
            _text_field(
                f'duration{index}',
                f'Duration {index}',
                DURATIONS[index - 1],
                80,
                'front',
            ),
        )
    specs.extend([
        _check_field(
            'manufacturer_warranty',
            "Manufacturer's warranty still applies",
            NON_DEALER,
            'manufacturer_warranty',
            'front',
            0,
        ),
        _check_field(
            'manufacturer_used_warranty',
            "Manufacturer's used vehicle warranty applies",
            NON_DEALER,
            'manufacturer_used_warranty',
            'front',
            1,
        ),
        _check_field(
            'other_used_warranty',
            'Other used vehicle warranty applies',
            NON_DEALER,
            'other_used_warranty',
            'front',
            2,
        ),
        _check_field(
            'service_contract',
            'Service contract available',
            SERVICE_CONTRACT,
            'service_contract',
            'front',
            0,
        ),
        _text_field('dealer_name', 'Dealer name', DEALER_NAME, 80, 'back'),
        _text_field('dealer_address', 'Dealer address', DEALER_ADDRESS, 100, 'back'),
        _text_field('dealer_phone', 'Dealer telephone', DEALER_PHONE, 30, 'back'),
        _text_field('dealer_email', 'Dealer email', DEALER_EMAIL, 60, 'back'),
        _text_field(
            'complaint_contact',
            'Complaints after sale contact',
            COMPLAINT_CONTACT,
            100,
            'back',
        ),
    ])
    return specs


def _percent_box(rect):
    x0, y0, x1, y1 = rect
    return {
        'left': round(x0 / _PAGE_WIDTH * 100, 3),
        'top': round((_PAGE_HEIGHT - y1) / _PAGE_HEIGHT * 100, 3),
        'width': round((x1 - x0) / _PAGE_WIDTH * 100, 3),
        'height': round((y1 - y0) / _PAGE_HEIGHT * 100, 3),
    }


def _page_widgets(template_path, page_index):
    reader = PdfReader(str(template_path))
    if page_index < 0 or page_index >= len(reader.pages):
        raise ValueError(f'Buyers Guide page {page_index} is missing.')
    widgets = []
    for annot in reader.pages[page_index].get('/Annots') or []:
        obj = annot.get_object()
        parent = obj.get('/Parent')
        parent_obj = parent.get_object() if parent is not None else None
        name = obj.get('/T') or (parent_obj.get('/T') if parent_obj is not None else None)
        field_type = obj.get('/FT') or (parent_obj.get('/FT') if parent_obj is not None else None)
        if not name:
            continue
        widgets.append({
            'name': str(name),
            'type': str(field_type or ''),
            'rect': [float(value) for value in obj.get('/Rect')],
        })
    return widgets


def _widget(widgets, name, index=0):
    matches = [widget for widget in widgets if widget['name'] == name]
    matches.sort(key=lambda widget: widget['rect'][1], reverse=True)
    if index >= len(matches):
        raise KeyError(f'{name}[{index}]')
    return matches[index]


def _positioned_fields(guide_type, template_path):
    guide_type = normalize_guide_type(guide_type)
    front_widgets = _page_widgets(template_path, _FRONT_PAGE[guide_type])
    back_widgets = _page_widgets(template_path, _BACK_PAGE)
    positioned = []
    for spec in _overlay_specs(guide_type):
        widgets = front_widgets if spec['sheet'] == 'front' else back_widgets
        widget = _widget(widgets, spec['pdf'], spec['index'])
        field = dict(spec)
        field.update(_percent_box(widget['rect']))
        field['rect_pts'] = widget['rect']
        positioned.append(field)
    return positioned


def blank_form_images(guide_type):
    guide_type = normalize_guide_type(guide_type)
    return {
        'front': _BLANK_IMAGES[guide_type],
        'back': _BLANK_IMAGES['back'],
    }


def overlay_fields(guide_type, template_path, values):
    fields = []
    for field in _positioned_fields(guide_type, template_path):
        item = {
            'key': field['key'],
            'kind': field['kind'],
            'label': field['label'],
            'group': field['group'],
            'max_length': field['max_length'],
            'sheet': field['sheet'],
            'left': field['left'],
            'top': field['top'],
            'width': field['width'],
            'height': field['height'],
        }
        if field['kind'] == 'text':
            item['value'] = values.get(field['key'], '') or ''
            item['checked'] = False
        else:
            item['value'] = ''
            item['checked'] = bool(values.get(field['key']))
        fields.append(item)
    return fields


def parse_overlay_submission(submitted):
    guide_type = normalize_guide_type(submitted.get('guide_type'))
    errors = []
    values = {'guide_type': guide_type}
    grouped = {}
    for spec in _overlay_specs(guide_type):
        if spec['kind'] == 'checkbox':
            grouped.setdefault(spec['group'], []).append(spec['key'])
            continue
        value = pdf_text(submitted.get(spec['key']))
        if len(value) > spec['max_length']:
            errors.append(f'{spec["label"]} must be {spec["max_length"]} characters or fewer.')
            value = value[:spec['max_length']]
        if spec['key'] in {'labor', 'parts'} and value:
            if not value.isdigit() or int(value) > 100:
                errors.append(f'{spec["label"]} must be a whole percent from 0 to 100.')
            else:
                value = str(int(value))
        values[spec['key']] = value
    for group, keys in grouped.items():
        selected = pdf_text(submitted.get(group))
        if selected and selected not in keys:
            errors.append('Choose only one answer for each exclusive option group.')
            selected = ''
        for key in keys:
            values[key] = selected == key
    if values.get('warranty_dealer') and not (
        values.get('dealer_full') or values.get('dealer_limited')
    ):
        errors.append('Choose full or limited when dealer warranty is selected.')
    if (values.get('dealer_full') or values.get('dealer_limited')) and not values.get(
        'warranty_dealer'
    ):
        values['warranty_dealer'] = True
        values['warranty_primary'] = False
    if values.get('dealer_limited'):
        for key, label in (('labor', 'Labor percent'), ('parts', 'Parts percent')):
            if not values.get(key):
                errors.append(f'{label} is required for a limited warranty.')
    return values, errors


def _draw_fitted(pdf, text, rect):
    x0, y0, x1, y1 = rect
    height = y1 - y0
    available = max((x1 - x0) - _FIELD_PADDING, 4)
    max_font = 11 if height >= 16 else 10
    min_font = 8.5 if height >= 16 else 7.5
    font_size = fit_font_size(text, (x1 - x0), max_font, min_font)
    natural = pdfmetrics.stringWidth(text, _FONT, font_size)
    scale = 1 if natural <= available else available / natural
    baseline = y0 + (height - font_size) / 2 + (2.35 if height < 16 else 1.55)
    baseline = min(max(baseline, y0 + 1.1), y1 - 1.4)
    pdf.saveState()
    pdf.translate(x0 + 1.6, baseline)
    pdf.scale(scale, 1)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont(_FONT, font_size)
    pdf.drawString(0, 0, text)
    pdf.restoreState()


def _draw_page_overlay(draws, checks=()):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(_PAGE_WIDTH, _PAGE_HEIGHT))
    drew = False
    for rect, text in draws:
        text = pdf_text(text)
        if not text:
            continue
        _draw_fitted(pdf, text, rect)
        drew = True
    for rect in checks:
        x0, y0, x1, y1 = rect
        box = min(x1 - x0, y1 - y0)
        font_size = box * 0.82
        mark = 'n'
        mark_width = pdfmetrics.stringWidth(mark, 'ZapfDingbats', font_size)
        pdf.setFillColorRGB(0, 0, 0)
        pdf.setFont('ZapfDingbats', font_size)
        pdf.drawString((x0 + x1 - mark_width) / 2, y0 + box * 0.12, mark)
        drew = True
    if not drew:
        return None
    pdf.save()
    buffer.seek(0)
    return buffer


def _flatten_page(page):
    if '/Annots' in page:
        del page['/Annots']


def build_overlay_pdf(template_path, values):
    """Return a two-page flattened Buyers Guide (selected front + back)."""
    guide_type = normalize_guide_type(values.get('guide_type'))
    path = Path(template_path)
    if not path.is_file():
        raise FileNotFoundError(f'Buyers Guide template is missing: {path.name}')
    with path.open('rb') as template_file:
        template_bytes = template_file.read()
    reader = PdfReader(BytesIO(template_bytes))
    if len(reader.pages) < 3:
        raise ValueError(f'{path.name} must include the AS IS, implied, and back pages.')

    front_draws = []
    front_checks = []
    back_draws = []
    back_checks = []
    for field in _positioned_fields(guide_type, path):
        target_draws = front_draws if field['sheet'] == 'front' else back_draws
        target_checks = front_checks if field['sheet'] == 'front' else back_checks
        if field['kind'] == 'text':
            text = values.get(field['key']) or ''
            if text:
                target_draws.append((field['rect_pts'], text))
        elif values.get(field['key']):
            target_checks.append(field['rect_pts'])

    writer = PdfWriter()
    front_index = _FRONT_PAGE[guide_type]
    for page_index, draws, checks in (
        (front_index, front_draws, front_checks),
        (_BACK_PAGE, back_draws, back_checks),
    ):
        writer.add_page(reader.pages[page_index])
        page = writer.pages[-1]
        overlay = _draw_page_overlay(draws, checks)
        if overlay is not None:
            page.merge_page(PdfReader(overlay).pages[0])
        _flatten_page(page)

    if '/AcroForm' in writer._root_object:
        del writer._root_object['/AcroForm']
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
