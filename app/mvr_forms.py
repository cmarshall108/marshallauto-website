"""Fill official NC DMV MVR-180 and MVR-181 forms from an inventory vehicle.

The blank templates are opened read-only and never modified. A copy is filled,
appearances are drawn at a font size that fits each box, and the copy is
flattened so the values stay readable when printed.
"""

import re
from datetime import date, datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

_FONT = 'Helvetica'
_MAX_FONT = 11
_MIN_FONT = 8.5
_MIN_SCALE = 0.72
# Keep a small inset so filled text does not touch the printed box edge.
_FIELD_PADDING = 3.5

_STATE_ZIP = re.compile(r'^([A-Za-z]{2})(?:\s+(\d{5}(?:-\d{4})?))?$')
_CITY_STATE_ZIP = re.compile(
    r'^(.+?)\s+([A-Za-z]{2})(?:\s+(\d{5}(?:-\d{4})?))?$',
)
_FILENAME_TOKEN = re.compile(r'[^A-Za-z0-9_-]+')

# MVR-180 field names, exactly as authored on the blank form.
ODOMETER_YEAR = 'YEAR'
ODOMETER_MAKE = 'MAKE'
ODOMETER_BODY = 'BODY STYLE'
ODOMETER_SERIES = 'SERIES MODEL'
ODOMETER_VIN = 'VEHICLE IDENTIFICATION NUMBER'
ODOMETER_SELLER_SENTENCE = 'sellers printed name'
ODOMETER_READING = 'odometer now reads miles no tenths'
ODOMETER_SELLER_NAME = 'SELLERS PRINTED NAME'
ODOMETER_SELLER_STREET = 'SELLERS street ADDRESS to include apartment or suite number'
ODOMETER_SELLER_CITY = 'sellers CITY'
ODOMETER_SELLER_STATE = 'sellers STATE'
ODOMETER_SELLER_ZIP = 'sellers ZIP CODE'
ODOMETER_SELLER_DATE = 'DATE OF CERTIFICATION by seller'
ODOMETER_BUYER_NAME = 'BUYERS PRINTED NAME'
ODOMETER_BUYER_STREET = 'BUYERS street ADDRESS to include apartment or suite number'
ODOMETER_BUYER_CITY = 'buyers CITY'
ODOMETER_BUYER_STATE = 'buyers STATE'
ODOMETER_BUYER_ZIP = 'buyers ZIP CODE'
ODOMETER_BUYER_DATE = 'DATE OF CERTIFICATION by buyer'
ODOMETER_EXCESS = (
    'check box - I hereby certify that the odometer reading reflects the '
    'amount of mileage in excess of its'
)
ODOMETER_DISCREPANCY = (
    'checkbox - I hereby certify that the odometer reading is not the actual '
    'mileage WARNING ODOMETER'
)

# MVR-181 field names.
DAMAGE_YEAR = 'Year'
DAMAGE_MAKE = 'Make'
DAMAGE_BODY = 'Body Style'
DAMAGE_VIN = 'Vehicle Identification Number'
DAMAGE_DATE = 'DATE'
DAMAGE_SELLER_ADDRESS = (
    'SELLERS ADDRESS to include street apartment or suite number city state zip'
)
DAMAGE_PARTS = 'If yes list parts that were damaged'
DAMAGE_PARTS_EXTRA = 'additional space for listing parts that were damaged if yes'
DAMAGE_SALVAGE_STATE = 'If yes in which state was it titled'
DAMAGE_25_YES = (
    'Check box yes damaged by collision or other occurrence to the extent that '
    'damages exceed 25 percent of its value at the time of the collision or '
    'other occurrence'
)
DAMAGE_25_NO = (
    'Check Box no was not damaged by collision or other occurrence to the '
    'extent that damages exceed 25 percent of its value at the time of the '
    'collision or other occurrence'
)
SALVAGE_YES = 'Check Box Yes was a salvage motor vehicle'
SALVAGE_NO = 'Check Box No was not a salvage motor vehicle'
FLOOD_YES = 'Check Box Yes is a flood vehicle'
FLOOD_NO = 'Check Box No is not a flood vehicle'
THEFT_YES = 'Check Box Yes is a recovered theft vehicle'
THEFT_NO = 'Check Box no vehicle is not a recovered theft vehicl'
RECONSTRUCTED_YES = 'Check Box Yes has been reconstructed'
RECONSTRUCTED_NO = 'Check Box No has not been reconstructed'

_SIGNATURE_FIELDS = {
    'SELLERS SIGNATURE CERTIFYING ODOMETER READING',
    'BUYERS SIGNATURE ACKNOWLEDGING ODOMETER READING AS CERTIFIED',
    'SELLERS SIGNATURE',
    'BUYERS ACKNOWLEDGEMENT AND SIGNATURE',
}
_KNOWN_TITLES = {'clean', 'rebuilt', 'salvage'}


def pdf_text(value):
    """Single-line text that Helvetica can draw."""
    text = ' '.join(str(value or '').replace('\x00', ' ').split())
    return text.encode('cp1252', 'replace').decode('cp1252')


def _available_width(field_width):
    return max(float(field_width) - _FIELD_PADDING, 4)


def text_fits(text, field_width, font_size, min_scale=_MIN_SCALE):
    """True when text can be drawn at ``font_size`` without clipping or over-squashing."""
    available = _available_width(field_width)
    width = pdfmetrics.stringWidth(text, _FONT, font_size)
    if width <= available:
        return True
    return width > 0 and available / width >= min_scale


def fit_font_size(text, field_width, max_size=_MAX_FONT, min_size=_MIN_FONT):
    """Largest Helvetica size that fits, allowing slight horizontal condensing."""
    size = float(max_size)
    while size > min_size and not text_fits(text, field_width, size):
        size = round(size - 0.25, 2)
    return size


def fitted_text(text, field_width, extra_options=(), max_size=_MAX_FONT, min_size=_MIN_FONT):
    """Choose the most complete label that stays readable in the box.

    Trailing words are dropped before a shorter alias is used. Characters are
    never cut off mid-word.
    """
    text = pdf_text(text).upper()
    if not text:
        return ''
    options = [text]
    words = text.split()
    while len(words) > 1:
        words = words[:-1]
        options.append(' '.join(words))
    for option in extra_options:
        option = pdf_text(option).upper()
        if option and option not in options:
            options.append(option)
    for option in options:
        if text_fits(option, field_width, min_size):
            return option
    return min(options, key=lambda item: pdfmetrics.stringWidth(item, _FONT, min_size))


def within_five_model_years(model_year, on_date):
    """NC counts the model year through the disclosure calendar year.

    Questions 1 and 4 apply only when that span is five model years or fewer.
    """
    try:
        year = int(model_year)
    except (TypeError, ValueError):
        return False
    span = on_date.year - year + 1
    return 1 <= span <= 5


def parse_postal_address(raw):
    """Split a receipt address into street, city, state, and ZIP.

    Unrecognized text stays in the street line so a city or state is never guessed.
    """
    text = pdf_text((raw or '').replace('\n', ', '))
    if not text:
        return '', '', '', ''
    parts = [part.strip(' ,') for part in text.split(',') if part.strip(' ,')]
    if len(parts) >= 2 and _STATE_ZIP.fullmatch(parts[-1]):
        state, zip_code = _STATE_ZIP.fullmatch(parts[-1]).groups()
        city = parts[-2]
        street = ', '.join(parts[:-2])
        return street, city, state.upper(), zip_code or ''
    if len(parts) >= 2 and _CITY_STATE_ZIP.fullmatch(parts[-1]):
        city, state, zip_code = _CITY_STATE_ZIP.fullmatch(parts[-1]).groups()
        street = ', '.join(parts[:-1])
        return street, city.strip(), state.upper(), zip_code or ''
    if len(parts) == 1 and _CITY_STATE_ZIP.fullmatch(parts[0]):
        city, state, zip_code = _CITY_STATE_ZIP.fullmatch(parts[0]).groups()
        if city.strip():
            return '', city.strip(), state.upper(), zip_code or ''
    return text, '', '', ''


def format_mileage(miles):
    """Whole miles with thousands separators and no tenths."""
    if miles is None:
        return ''
    try:
        reading = int(miles)
    except (TypeError, ValueError):
        return ''
    if reading < 0:
        return ''
    return f'{reading:,}'


def format_form_date(value):
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return ''
    return value.strftime('%m/%d/%Y')


def choose_series_model(model, trim, field_width):
    """Prefer model plus trim, then the model, when it stays readable."""
    model = pdf_text(model).upper()
    trim = pdf_text(trim).upper()
    return fitted_text(
        ' '.join(part for part in (model, trim) if part),
        field_width,
        extra_options=(model, trim),
        min_size=8.5,
    )


_BODY_ALIASES = {
    'SPORT UTILITY VEHICLE': ('SUV',),
    'SPORT UTILITY': ('SUV',),
    'SPORT-UTILITY VEHICLE': ('SUV',),
    'PICKUP TRUCK': ('PICKUP',),
    'PASSENGER VAN': ('VAN',),
    'CARGO VAN': ('VAN',),
}


def normalize_reconstructed(value):
    """Return auto, yes, or no. Anything else is not a specific selection."""
    token = (value or 'auto').strip().lower()
    if token in {'yes', 'true', '1', 'y'}:
        return 'yes'
    if token in {'no', 'false', '0', 'n'}:
        return 'no'
    return 'auto'


def damage_answers(title_status, model_year, on_date, reconstructed='auto'):
    """Map stored title status to MVR-181 answers.

    Flood and recovered-theft are left unanswered because inventory does not
    record them. A salvage title is marked reconstructed unless staff
    specifically select No. Any other title is not reconstructed unless they
    specifically select Yes. Damage over 25% is answered only for salvage or
    rebuilt vehicles inside the five-model-year window. A clean title does not
    prove the absence of lesser collision damage.
    """
    title = (title_status or '').strip().lower()
    selection = normalize_reconstructed(reconstructed)
    answers = {
        'damage_25': None,
        'salvage': None,
        'flood': None,
        'theft': None,
        'reconstructed': None,
    }
    if title in _KNOWN_TITLES:
        answers['salvage'] = title in {'salvage', 'rebuilt'}
        if title in {'salvage', 'rebuilt'} and within_five_model_years(model_year, on_date):
            answers['damage_25'] = True
    if selection == 'yes':
        answers['reconstructed'] = True
    elif selection == 'no':
        answers['reconstructed'] = False
    elif title == 'salvage':
        answers['reconstructed'] = True
    elif title in _KNOWN_TITLES:
        answers['reconstructed'] = False
    return answers


def download_name(form_code, vehicle):
    token = _FILENAME_TOKEN.sub('-', str(getattr(vehicle, 'stock_number', None) or vehicle.id))
    token = token.strip('-') or str(vehicle.id)
    return f'{form_code}-{token}.pdf'


def build_mvr_180(vehicle, config, on_date=None, mileage=None):
    return _fill_template(
        config['MVR_180_PDF'],
        _odometer_values(vehicle, config, mileage=mileage),
        {},
    )


def build_mvr_181(vehicle, config, on_date=None):
    today = on_date or date.today()
    return _fill_template(
        config['MVR_181_PDF'],
        _damage_text_values(vehicle, config, today),
        _damage_checks(vehicle, today),
    )


def _seller(config):
    state = pdf_text(config.get('BUSINESS_STATE'))
    if len(state) == 2:
        state = state.upper()
    return {
        'name': pdf_text(config.get('BUSINESS_NAME')),
        'street': pdf_text(config.get('BUSINESS_ADDRESS')),
        'city': pdf_text(config.get('BUSINESS_CITY')),
        'state': state,
        'zip': pdf_text(config.get('BUSINESS_ZIP')),
    }


def _certification_date(vehicle):
    sold_at = getattr(vehicle, 'sold_at', None)
    if getattr(vehicle, 'status', None) == 'sold' and isinstance(sold_at, datetime):
        return sold_at.date()
    if getattr(vehicle, 'status', None) == 'sold' and isinstance(sold_at, date):
        return sold_at
    return None


def _reference_date(vehicle, today):
    return _certification_date(vehicle) or today


def _vehicle_identity(vehicle):
    vin = pdf_text(getattr(vehicle, 'vin', '')).upper().replace(' ', '')
    return {
        'year': str(int(vehicle.year)) if getattr(vehicle, 'year', None) else '',
        'make': pdf_text(getattr(vehicle, 'make', '')).upper(),
        'body': pdf_text(getattr(vehicle, 'body_style', '')).upper(),
        'model': pdf_text(getattr(vehicle, 'model', '')),
        'trim': pdf_text(getattr(vehicle, 'trim', '')),
        'vin': vin,
    }


def _buyer(vehicle):
    receipt = getattr(vehicle, 'sale_receipt', None)
    if receipt is None:
        return None
    name = pdf_text(getattr(receipt, 'buyer_name', ''))
    if not name:
        return None
    street, city, state, zip_code = parse_postal_address(
        getattr(receipt, 'buyer_address', ''),
    )
    return {
        'name': name,
        'street': street,
        'city': city,
        'state': state,
        'zip': zip_code,
    }


def _odometer_values(vehicle, config, mileage=None):
    seller = _seller(config)
    identity = _vehicle_identity(vehicle)
    buyer = _buyer(vehicle)
    certified = _certification_date(vehicle)
    values = {
        ODOMETER_YEAR: identity['year'],
        ODOMETER_MAKE: identity['make'],
        ODOMETER_BODY: identity['body'],
        ODOMETER_VIN: identity['vin'],
        ODOMETER_SELLER_SENTENCE: seller['name'],
        ODOMETER_SELLER_NAME: seller['name'],
        ODOMETER_READING: format_mileage(
            getattr(vehicle, 'mileage', None) if mileage is None else mileage,
        ),
        ODOMETER_SELLER_STREET: seller['street'],
        ODOMETER_SELLER_CITY: seller['city'],
        ODOMETER_SELLER_STATE: seller['state'],
        ODOMETER_SELLER_ZIP: seller['zip'],
        ODOMETER_SELLER_DATE: format_form_date(certified),
    }
    widths = _field_widths(config['MVR_180_PDF'])
    values[ODOMETER_MAKE] = fitted_text(
        identity['make'], widths.get(ODOMETER_MAKE, 60),
    )
    values[ODOMETER_BODY] = fitted_text(
        identity['body'], widths.get(ODOMETER_BODY, 56),
        extra_options=_BODY_ALIASES.get(identity['body'].upper(), ()),
    )
    values[ODOMETER_SERIES] = choose_series_model(
        identity['model'], identity['trim'], widths.get(ODOMETER_SERIES, 64),
    )
    if buyer and certified:
        values.update({
            ODOMETER_BUYER_NAME: buyer['name'],
            ODOMETER_BUYER_STREET: buyer['street'],
            ODOMETER_BUYER_CITY: buyer['city'],
            ODOMETER_BUYER_STATE: buyer['state'],
            ODOMETER_BUYER_ZIP: buyer['zip'],
            ODOMETER_BUYER_DATE: format_form_date(certified),
        })
    elif buyer:
        values.update({
            ODOMETER_BUYER_NAME: buyer['name'],
            ODOMETER_BUYER_STREET: buyer['street'],
            ODOMETER_BUYER_CITY: buyer['city'],
            ODOMETER_BUYER_STATE: buyer['state'],
            ODOMETER_BUYER_ZIP: buyer['zip'],
        })
    return {key: value for key, value in values.items() if value}


def _one_line_address(seller):
    locality = ' '.join(part for part in (seller['state'], seller['zip']) if part)
    city_line = ', '.join(part for part in (seller['city'], locality) if part)
    return ', '.join(part for part in (seller['street'], city_line) if part)


def _damage_text_values(vehicle, config, today):
    seller = _seller(config)
    identity = _vehicle_identity(vehicle)
    widths = _field_widths(config['MVR_181_PDF'])
    values = {
        DAMAGE_YEAR: identity['year'],
        DAMAGE_MAKE: fitted_text(identity['make'], widths.get(DAMAGE_MAKE, 72)),
        DAMAGE_BODY: fitted_text(
            identity['body'], widths.get(DAMAGE_BODY, 94),
            extra_options=_BODY_ALIASES.get(identity['body'].upper(), ()),
        ),
        DAMAGE_VIN: identity['vin'],
        DAMAGE_DATE: format_form_date(_certification_date(vehicle)),
        DAMAGE_SELLER_ADDRESS: _one_line_address(seller),
    }
    return {key: value for key, value in values.items() if value}


def _damage_checks(vehicle, today):
    answers = damage_answers(
        getattr(vehicle, 'title_status', None),
        getattr(vehicle, 'year', None),
        _reference_date(vehicle, today),
        getattr(vehicle, 'reconstructed', 'auto'),
    )
    pairs = (
        ('damage_25', DAMAGE_25_YES, DAMAGE_25_NO),
        ('salvage', SALVAGE_YES, SALVAGE_NO),
        ('flood', FLOOD_YES, FLOOD_NO),
        ('theft', THEFT_YES, THEFT_NO),
        ('reconstructed', RECONSTRUCTED_YES, RECONSTRUCTED_NO),
    )
    selected = {}
    for key, yes_name, no_name in pairs:
        if answers[key] is True:
            selected[yes_name] = yes_name
        elif answers[key] is False:
            selected[no_name] = no_name
    return selected


def _field_widths(template_path):
    reader = PdfReader(str(template_path))
    widths = {}
    for annot in reader.pages[0].get('/Annots') or []:
        obj = annot.get_object()
        name = obj.get('/T')
        if not name:
            continue
        rect = obj.get('/Rect')
        widths[str(name)] = float(rect[2]) - float(rect[0])
    return widths


def _on_state(page, field_name):
    for annot in page.get('/Annots') or []:
        obj = annot.get_object()
        name = obj.get('/T')
        parent = obj.get('/Parent')
        if not name and parent is not None:
            name = parent.get_object().get('/T')
        if str(name or '') != field_name:
            continue
        states = obj.get('/_States_')
        if not states and parent is not None:
            states = parent.get_object().get('/_States_')
        for state in states or []:
            token = str(state)
            if token not in {'/Off', 'Off'}:
                return token if token.startswith('/') else f'/{token}'
        appearance = obj.get('/AP')
        if appearance is not None:
            normal = appearance.get_object().get('/N')
            if normal is not None:
                for key in normal.get_object().keys():
                    if str(key) != '/Off':
                        return str(key)
    return '/Yes'


def _widget_rects(page):
    rects = {}
    for annot in page.get('/Annots') or []:
        obj = annot.get_object()
        name = obj.get('/T')
        if not name or str(name) in _SIGNATURE_FIELDS:
            continue
        rect = [float(value) for value in obj.get('/Rect')]
        rects[str(name)] = rect
    return rects


def _draw_fitted(pdf, text, rect):
    x0, y0, x1, y1 = rect
    height = y1 - y0
    available = max((x1 - x0) - _FIELD_PADDING, 4)
    max_font = 11 if height >= 16 else 10
    min_font = 8.5 if height >= 16 else 8
    font_size = fit_font_size(text, (x1 - x0), max_font, min_font)
    natural = pdfmetrics.stringWidth(text, _FONT, font_size)
    scale = 1 if natural <= available else available / natural
    baseline = y0 + (height - font_size) / 2 + 1.55
    baseline = min(max(baseline, y0 + 1.1), y1 - 1.4)
    pdf.saveState()
    pdf.translate(x0 + 1.6, baseline)
    pdf.scale(scale, 1)
    pdf.setFillColorRGB(0, 0, 0)
    pdf.setFont(_FONT, font_size)
    pdf.drawString(0, 0, text)
    pdf.restoreState()


def _text_overlay(rects, text_values):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    drew = False
    for name, value in text_values.items():
        if name in _SIGNATURE_FIELDS or name in {DAMAGE_PARTS, DAMAGE_PARTS_EXTRA}:
            continue
        text = pdf_text(value)
        rect = rects.get(name)
        if not text or not rect:
            continue
        _draw_fitted(pdf, text, rect)
        drew = True
    if not drew:
        return None
    pdf.save()
    buffer.seek(0)
    return buffer


def _fill_template(template_path, text_values, checks):
    path = Path(template_path)
    if not path.is_file():
        raise FileNotFoundError(f'MVR form template is missing: {path.name}')
    with path.open('rb') as template_file:
        template_bytes = template_file.read()
    reader = PdfReader(BytesIO(template_bytes))
    if len(reader.pages) != 1:
        raise ValueError(f'{path.name} must be a one-page form.')
    writer = PdfWriter()
    writer.append(reader)
    page = writer.pages[0]
    rects = _widget_rects(page)
    resolved_checks = {name: _on_state(page, name) for name in checks}
    if resolved_checks:
        writer.update_page_form_field_values(
            page, resolved_checks, auto_regenerate=False, flatten=True,
        )
    overlay = _text_overlay(rects, text_values)
    if overlay is not None:
        page.merge_page(PdfReader(overlay).pages[0])
    # Painted values are part of the page. Drop the widgets so a viewer cannot
    # hide them, cover them, or draw an empty field on top.
    if '/Annots' in page:
        del page['/Annots']
    if '/AcroForm' in writer._root_object:
        del writer._root_object['/AcroForm']
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
