import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from flask import current_app
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import ChatPresence, FinancingApplication, User, utcnow
from app.spam_filter import check_lead_spam_for_app

_SSN_PATTERN = re.compile(r'^\d{3}-?\d{2}-?\d{4}$')
_EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_PHONE_PATTERN = re.compile(r'^[\d\s()+.\-]+$')
_US_STATE_PATTERN = re.compile(r'^[A-Za-z]{2}$')
_US_ZIP_PATTERN = re.compile(r'^\d{5}(?:-?\d{4})?$')
_STORED_FILENAME_PATTERN = re.compile(r'^[0-9a-f]{32}\.enc$')
_MONEY_FIELDS = {
    'housing_payment', 'employment_gross', 'employment_net',
    'other_income_gross', 'total_gross_income', 'total_net_income',
    'alimony_amount',
}
_TEXT_LIMITS = {
    'name': 128,
    'ssn': 11,
    'dob': 10,
    'residence_address': 160,
    'residence_street': 100,
    'residence_city': 64,
    'residence_state': 2,
    'residence_zip': 10,
    'residence_years': 3,
    'residence_months': 2,
    'mailing_address': 160,
    'previous_address': 160,
    'previous_street': 100,
    'previous_city': 64,
    'previous_state': 2,
    'previous_zip': 10,
    'previous_residence_years': 3,
    'previous_residence_months': 2,
    'home_phone': 24,
    'cell_phone': 24,
    'dependents': 2,
    'email': 120,
    'housing_status': 16,
    'landlord': 128,
    'housing_payment': 12,
    'employment_status': 16,
    'employer_name_address': 128,
    'occupation': 64,
    'employment_date': 10,
    'work_phone': 24,
    'work_extension': 8,
    'employment_gross': 12,
    'employment_net': 12,
    'previous_employer': 128,
    'previous_employment_date': 10,
    'other_income_gross': 12,
    'other_income_source': 128,
    'total_gross_income': 12,
    'total_net_income': 12,
    'alimony_obligation': 3,
    'alimony_amount': 12,
    'auto_1_year_model': 64,
    'auto_1_financed_by': 64,
    'auto_1_responsibility': 16,
    'auto_2_year_model': 64,
    'auto_2_financed_by': 64,
    'auto_2_responsibility': 16,
    'license_number': 32,
    'license_state': 2,
    'other_qualifying_id': 64,
    'signature': 128,
}
_PERSON_PREFIXES = ('applicant', 'coapplicant')
_EMPLOYMENT_STATUSES = {'employed', 'self-employed', 'unemployed', 'retired'}
_HOUSING_STATUSES = {'buy', 'rent', 'own', 'other'}
_RESPONSIBILITIES = {'applicant', 'coapplicant', 'none'}


class FinancingEncryptionUnavailable(RuntimeError):
    pass


def _cipher():
    key = current_app.config.get('FINANCING_ENCRYPTION_KEY')
    if not key:
        raise FinancingEncryptionUnavailable('Financing encryption key is not configured.')
    try:
        return Fernet(key.encode('ascii'))
    except (AttributeError, UnicodeEncodeError, ValueError) as exc:
        raise FinancingEncryptionUnavailable(
            'Financing encryption key is invalid.',
        ) from exc


def financing_encryption_ready():
    try:
        _cipher()
    except FinancingEncryptionUnavailable:
        return False
    return True


def online_application_available():
    from flask import current_app, request

    return financing_encryption_ready() and (
        request.is_secure or current_app.testing
    )


def is_valid_ssn(value):
    if not _SSN_PATTERN.fullmatch(value or ''):
        return False
    digits = value.replace('-', '')
    area, group, serial = digits[:3], digits[3:5], digits[5:]
    return (
        area not in {'000', '666'}
        and not area.startswith('9')
        and group != '00'
        and serial != '0000'
    )


def _clean(raw, limit):
    value = ' '.join((raw or '').split())
    return value[:limit]


def _parse_date(value, label, errors, required=False):
    if not value:
        if required:
            errors[label] = 'This field is required.'
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        errors[label] = 'Enter a valid date.'
        return None
    if parsed > date.today():
        errors[label] = 'The date cannot be in the future.'
        return None
    return parsed


def _parse_integer(value, label, errors, required=False, minimum=0, maximum=120):
    if not value:
        if required:
            errors[label] = 'This field is required.'
        return None
    if not value.isdigit():
        errors[label] = 'Enter a whole number.'
        return None
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        errors[label] = f'Enter a value between {minimum} and {maximum}.'
        return None
    return parsed


def _parse_money(value, label, errors, required=False):
    if not value:
        if required:
            errors[label] = 'This field is required.'
        return None
    try:
        amount = Decimal(value.replace('$', '').replace(',', '').strip())
    except (InvalidOperation, AttributeError):
        errors[label] = 'Enter a valid dollar amount.'
        return None
    if not amount.is_finite() or amount < 0 or amount > Decimal(10000000):
        errors[label] = 'Enter an amount between $0 and $10,000,000.'
        return None
    if amount.as_tuple().exponent < -2:
        errors[label] = 'Use no more than two decimal places.'
        return None
    return amount.quantize(Decimal('0.01'))


def _normalize_zip(value):
    digits = re.sub(r'\D', '', value or '')
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f'{digits[:5]}-{digits[5:]}'
    return (value or '').strip()


def _compose_us_address(street, city, state, zip_code):
    street = ' '.join((street or '').split())
    city = ' '.join((city or '').split())
    state = (state or '').strip().upper()
    zip_code = _normalize_zip(zip_code)
    locality = ' '.join(part for part in (city and f'{city},', state, zip_code) if part).replace(' ,', ',')
    if street and locality:
        return f'{street}, {locality}'
    return street or locality


def _validate_us_address_parts(
    values,
    prefix,
    errors,
    *,
    street_field,
    city_field,
    state_field,
    zip_field,
    combined_field,
    required,
    label,
):
    """Require street/city/state/ZIP when needed and build the combined address line."""
    street = values.get(street_field) or ''
    city = values.get(city_field) or ''
    state = (values.get(state_field) or '').strip().upper()
    zip_code = values.get(zip_field) or ''
    values[state_field] = state
    values[zip_field] = _normalize_zip(zip_code) if zip_code else ''

    any_part = any((street, city, state, values[zip_field]))
    if not required and not any_part and not values.get(combined_field):
        values[combined_field] = ''
        return

    if required or any_part:
        if not street:
            errors[f'{prefix}_{street_field}'] = f'Enter the {label} street address.'
        elif len(street) < 3 or not any(character.isdigit() for character in street):
            # House numbers are standard on US credit apps; still allow rare rural exceptions
            # when the applicant also provided city + ZIP.
            if not (city and values[zip_field]):
                errors[f'{prefix}_{street_field}'] = (
                    f'Enter a full {label} street address including the house number.'
                )
        if not city:
            errors[f'{prefix}_{city_field}'] = f'Enter the {label} city.'
        elif len(city) < 2 or not re.search(r'[A-Za-z]', city):
            errors[f'{prefix}_{city_field}'] = f'Enter a valid {label} city name.'
        if not state:
            errors[f'{prefix}_{state_field}'] = f'Enter the {label} state (2 letters).'
        elif not _US_STATE_PATTERN.fullmatch(state):
            errors[f'{prefix}_{state_field}'] = 'Use the 2-letter state code (for example NC).'
        if not values[zip_field]:
            errors[f'{prefix}_{zip_field}'] = f'Enter the {label} ZIP code.'
        elif not _US_ZIP_PATTERN.fullmatch(values[zip_field]):
            errors[f'{prefix}_{zip_field}'] = 'Enter a 5-digit ZIP code (or ZIP+4).'

    if street and city and state and values[zip_field] and f'{prefix}_{street_field}' not in errors \
            and f'{prefix}_{city_field}' not in errors \
            and f'{prefix}_{state_field}' not in errors \
            and f'{prefix}_{zip_field}' not in errors:
        values[combined_field] = _compose_us_address(street, city, state, values[zip_field])
    elif values.get(combined_field) and not any_part:
        # Legacy single-line submissions: keep the value but require city + ZIP cues.
        combined = values[combined_field]
        has_zip = bool(re.search(r'\b\d{5}(?:-?\d{4})?\b', combined))
        # City-ish token before a state code, e.g. "Sanford, NC 27332"
        has_city = bool(re.search(r'[A-Za-z].*,\s*[A-Za-z]{2}\b', combined))
        if required or combined:
            if not has_city:
                errors[f'{prefix}_{city_field}'] = (
                    f'Include the city in the {label} address (street, city, state, ZIP).'
                )
            if not has_zip:
                errors[f'{prefix}_{zip_field}'] = (
                    f'Include the ZIP code in the {label} address.'
                )


def lookup_us_zip(zip_code):
    """Return city/state suggestions for a US ZIP via Zippopotam.us (no API key)."""
    digits = re.sub(r'\D', '', zip_code or '')
    if len(digits) < 5:
        return None
    zip5 = digits[:5]
    url = f'https://api.zippopotam.us/us/{zip5}'
    try:
        request = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'MarshallAuto/1.0'})
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError):
        return None
    places = payload.get('places') or []
    if not places:
        return None
    primary = places[0]
    cities = []
    seen = set()
    for place in places:
        name = (place.get('place name') or '').strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            cities.append(name)
    return {
        'zip': zip5,
        'state': (payload.get('state abbreviation') or '').strip().upper(),
        'state_name': (payload.get('state') or '').strip(),
        'city': (primary.get('place name') or '').strip(),
        'cities': cities,
    }


def _collect_person(form, prefix, errors, require_previous_address):
    values = {}
    for field, limit in _TEXT_LIMITS.items():
        if field in {'signature'}:
            continue
        key = f'{prefix}_{field}'
        raw_value = form.get(key) or ''
        if len(' '.join(raw_value.split())) > limit:
            errors[key] = f'Keep this entry to {limit} characters or fewer.'
        values[field] = _clean(raw_value, limit)

    signature_key = f'{prefix}_signature'
    raw_signature = form.get(signature_key) or values['name']
    if len(' '.join(raw_signature.split())) > _TEXT_LIMITS['signature']:
        errors[signature_key] = 'Keep this entry to 128 characters or fewer.'
    values['signature'] = _clean(raw_signature, _TEXT_LIMITS['signature'])
    values['dob'] = _parse_date(
        values['dob'], f'{prefix}_dob', errors, required=True,
    )
    values['employment_date'] = _parse_date(
        values['employment_date'], f'{prefix}_employment_date', errors,
    )
    values['previous_employment_date'] = _parse_date(
        values['previous_employment_date'],
        f'{prefix}_previous_employment_date', errors,
    )

    _validate_us_address_parts(
        values, prefix, errors,
        street_field='residence_street',
        city_field='residence_city',
        state_field='residence_state',
        zip_field='residence_zip',
        combined_field='residence_address',
        required=True,
        label='current residence',
    )

    required_fields = (
        'name', 'ssn', 'residence_years',
        'residence_months', 'dependents', 'email', 'housing_status',
        'employment_status', 'total_gross_income', 'total_net_income',
        'alimony_obligation',
    )
    for field in required_fields:
        if not values[field]:
            errors[f'{prefix}_{field}'] = 'This field is required.'
    if not values.get('residence_address') and f'{prefix}_residence_street' not in errors:
        errors[f'{prefix}_residence_street'] = 'Enter your current residence address.'

    if not values['home_phone'] and not values['cell_phone']:
        errors[f'{prefix}_cell_phone'] = 'Enter at least one home or cell phone number.'
    for field in ('home_phone', 'cell_phone', 'work_phone'):
        phone = values[field]
        if phone and (
            not _PHONE_PATTERN.fullmatch(phone)
            or not 7 <= sum(character.isdigit() for character in phone) <= 15
        ):
            errors[f'{prefix}_{field}'] = 'Enter a valid phone number.'
    if values['email'] and (
        len(values['email']) > 120 or not _EMAIL_PATTERN.fullmatch(values['email'])
    ):
        errors[f'{prefix}_email'] = 'Enter a valid email address.'

    if not is_valid_ssn(values['ssn']):
        errors[f'{prefix}_ssn'] = 'Enter a valid 9-digit Social Security number.'
    else:
        values['ssn'] = values['ssn'].replace('-', '')
        values['ssn'] = (
            f'{values["ssn"][:3]}-{values["ssn"][3:5]}-{values["ssn"][5:]}'
        )

    for field in ('residence_years', 'residence_months', 'dependents'):
        values[field] = _parse_integer(
            values[field], f'{prefix}_{field}', errors, required=True,
            maximum=120 if field == 'residence_years' else (
                11 if field == 'residence_months' else 20
            ),
        )
    for field in ('previous_residence_years', 'previous_residence_months'):
        values[field] = _parse_integer(
            values[field], f'{prefix}_{field}', errors,
            maximum=120 if field.endswith('years') else 11,
        )

    if values['housing_status'] not in _HOUSING_STATUSES:
        errors[f'{prefix}_housing_status'] = 'Choose a housing status.'
    if values['employment_status'] not in _EMPLOYMENT_STATUSES:
        errors[f'{prefix}_employment_status'] = 'Choose an employment status.'
    if values['alimony_obligation'] not in {'yes', 'no'}:
        errors[f'{prefix}_alimony_obligation'] = 'Choose Yes or No.'
    for car_number in (1, 2):
        key = f'auto_{car_number}_responsibility'
        if values[key] and values[key] not in _RESPONSIBILITIES:
            errors[f'{prefix}_{key}'] = 'Choose who is responsible for this auto loan.'

    if require_previous_address:
        current_months = (
            (values['residence_years'] or 0) * 12
            + (values['residence_months'] or 0)
        )
        previous_months = (
            (values['previous_residence_years'] or 0) * 12
            + (values['previous_residence_months'] or 0)
        )
        need_previous = current_months < 24
        _validate_us_address_parts(
            values, prefix, errors,
            street_field='previous_street',
            city_field='previous_city',
            state_field='previous_state',
            zip_field='previous_zip',
            combined_field='previous_address',
            required=need_previous,
            label='previous residence',
        )
        if need_previous and not values.get('previous_address') and f'{prefix}_previous_street' not in errors:
            errors[f'{prefix}_previous_street'] = (
                'Enter your prior address to complete two years of residence history.'
            )
        if current_months + previous_months < 24:
            errors[f'{prefix}_previous_residence_years'] = (
                'Provide enough prior residence history to cover two years.'
            )
    else:
        # Still compose previous address when the optional parts are provided.
        _validate_us_address_parts(
            values, prefix, errors,
            street_field='previous_street',
            city_field='previous_city',
            state_field='previous_state',
            zip_field='previous_zip',
            combined_field='previous_address',
            required=False,
            label='previous residence',
        )

    active_employment = values['employment_status'] in {'employed', 'self-employed'}
    if active_employment:
        for field in ('employer_name_address', 'occupation', 'employment_date'):
            if not values[field]:
                errors[f'{prefix}_{field}'] = 'This field is required.'
    else:
        label = values['employment_status'].replace('-', ' ').title()
        values['employer_name_address'] = values['employer_name_address'] or label
        values['occupation'] = values['occupation'] or label

    if values['employment_date']:
        current_months = (
            (date.today().year - values['employment_date'].year) * 12
            + date.today().month - values['employment_date'].month
        )
        if current_months < 24 and (
            not values['previous_employer'] or not values['previous_employment_date']
        ):
            errors[f'{prefix}_previous_employer'] = (
                'Add your prior employer and start date for two years of employment history.'
            )
        if current_months < 24 and values['previous_employment_date']:
            previous_months = (
                (date.today().year - values['previous_employment_date'].year) * 12
                + date.today().month - values['previous_employment_date'].month
            )
            if previous_months < 24:
                errors[f'{prefix}_previous_employment_date'] = (
                    'Provide a prior employment start date that covers two years of history.'
                )
    if bool(values['previous_employer']) != bool(values['previous_employment_date']):
        errors[f'{prefix}_previous_employer'] = (
            'Enter both the prior employer and its employment date.'
        )

    for field in _MONEY_FIELDS:
        values[field] = _parse_money(
            values[field], f'{prefix}_{field}', errors,
            required=field in {'total_gross_income', 'total_net_income'}
            or (active_employment and field in {'employment_gross', 'employment_net'}),
        )
    if not active_employment:
        values['employment_gross'] = values['employment_gross'] or Decimal('0.00')
        values['employment_net'] = values['employment_net'] or Decimal('0.00')
    if values['other_income_gross'] is None:
        values['other_income_gross'] = Decimal('0.00')
    if values['other_income_gross'] > 0 and not values['other_income_source']:
        errors[f'{prefix}_other_income_source'] = (
            'Describe any other income you want considered.'
        )
    if values['alimony_obligation'] == 'yes' and values['alimony_amount'] is None:
        errors[f'{prefix}_alimony_amount'] = 'Enter the monthly amount.'
    if values['alimony_obligation'] == 'no':
        values['alimony_amount'] = Decimal('0.00')
    if values['housing_payment'] is None:
        values['housing_payment'] = Decimal('0.00')
    if values['total_gross_income'] is None:
        values['total_gross_income'] = Decimal('0.00')
    if values['total_net_income'] is None:
        values['total_net_income'] = Decimal('0.00')

    if prefix == 'applicant':
        for field in ('license_number', 'license_state'):
            if not values[field]:
                errors[f'{prefix}_{field}'] = 'This field is required.'
        if values['license_state'] and not re.fullmatch(r'[A-Za-z]{2}', values['license_state']):
            errors[f'{prefix}_license_state'] = 'Enter a two-letter state abbreviation.'

    if values['signature'].casefold() != values['name'].casefold():
        errors[f'{prefix}_signature'] = (
            'Type your full legal name exactly as entered above to sign.'
        )

    return values


def validate_application(form, vehicle):
    errors = {}
    raw_requested_amount = form.get('requested_amount') or ''
    if len(raw_requested_amount.strip()) > _TEXT_LIMITS['housing_payment']:
        errors['requested_amount'] = 'Enter an amount with no more than 12 characters.'
    requested_amount = _parse_money(
        _clean(raw_requested_amount, _TEXT_LIMITS['housing_payment']),
        'requested_amount', errors, required=True,
    )
    if requested_amount is not None and requested_amount <= 0:
        errors['requested_amount'] = 'Enter an amount greater than $0.'

    joint_credit = form.get('joint_credit') == 'yes'
    applicant = _collect_person(form, 'applicant', errors, True)
    coapplicant = (
        _collect_person(form, 'coapplicant', errors, True) if joint_credit else None
    )
    if form.get('certify_accuracy') != 'yes':
        errors['certify_accuracy'] = 'Confirm that the information is complete and accurate.'
    if form.get('authorize_credit_report') != 'yes':
        errors['authorize_credit_report'] = (
            'Authorize the lender and seller to verify the application and obtain a credit report.'
        )
    if form.get('electronic_signature_consent') != 'yes':
        errors['electronic_signature_consent'] = (
            'Agree to use your typed name as an electronic signature to submit this application.'
        )

    submitted_at = utcnow()
    application = {
        'vehicle': {
            'year': vehicle.year,
            'make': vehicle.make,
            'model': vehicle.model,
            'trim': vehicle.trim or '',
            'vin': vehicle.vin or '',
            'stock_number': vehicle.stock_number or '',
            'price': vehicle.display_price,
        },
        'requested_amount': requested_amount,
        'applicant': applicant,
        'coapplicant': coapplicant,
        'joint_credit': joint_credit,
        'submitted_at': submitted_at,
    }

    values = {}
    for prefix in _PERSON_PREFIXES:
        for field, limit in _TEXT_LIMITS.items():
            if field == 'signature':
                continue
            key = f'{prefix}_{field}'
            raw = _clean(form.get(key), limit)
            if field not in {'ssn', 'license_number', 'other_qualifying_id'}:
                values[key] = raw
        values[f'{prefix}_signature'] = _clean(
            form.get(f'{prefix}_signature'), 128,
        )
    values['requested_amount'] = _clean(form.get('requested_amount'), 12)
    values['joint_credit'] = joint_credit

    if errors:
        return None, errors, values
    return application, {}, values


def financing_spam_detected(application):
    """Apply local lead-spam scoring without sending applicant data externally."""
    for person in (application['applicant'], application['coapplicant']):
        if not person:
            continue
        phone = person['cell_phone'] or person['home_phone']
        result = check_lead_spam_for_app(
            current_app.config,
            name=person['name'],
            email=person['email'],
            phone=phone,
        )
        if result.is_spam:
            return True
    return False


def _pdf_safe(value):
    if value is None:
        return ''
    return str(value).encode('cp1252', 'replace').decode('cp1252')


def _date_text(value):
    return value.strftime('%m/%d/%Y') if value else ''


def _money_text(value, include_symbol=True):
    if value is None:
        return ''
    amount = f'{value:,.2f}'
    return f'${amount}' if include_symbol else amount


def _draw_text(pdf, x_px, baseline_px, text, width_px, max_font=7.2):
    text = _pdf_safe(text).replace('\n', ' ')
    if not text:
        return
    font_name = 'Helvetica-Bold'
    font_size = max_font
    available_width = width_px * 0.6
    while (
        font_size > 4.4
        and pdfmetrics.stringWidth(text, font_name, font_size) > available_width
    ):
        font_size -= 0.2
    if pdfmetrics.stringWidth(text, font_name, font_size) > available_width:
        while text and pdfmetrics.stringWidth(
            f'{text}...', font_name, font_size,
        ) > available_width:
            text = text[:-1]
        text = f'{text}...' if text else ''
    if text:
        pdf.setFont(font_name, font_size)
        pdf.drawString(x_px * 0.6, 792 - baseline_px * 0.6, text)


def _draw_check(pdf, x_px, baseline_px):
    pdf.setFont('Helvetica-Bold', 8)
    pdf.drawString(x_px * 0.6, 792 - baseline_px * 0.6, 'X')


def _person_tenure(person, prefix=''):
    years = person.get('residence_years')
    months = person.get('residence_months')
    if years is None or months is None:
        return ''
    return f'{years} yr {months} mo'


def _draw_income(
    pdf, x_px, baseline_px, gross, net, width_px,
    include_symbol=True, max_font=6.4,
):
    _draw_text(
        pdf, x_px, baseline_px,
        (
            f'{_money_text(gross, include_symbol)} / '
            f'{_money_text(net, include_symbol)}'
        ),
        width_px, max_font=max_font,
    )


def _draw_person_on_original(pdf, person, coapplicant=False):
    # Coordinates are 120 dpi image pixels. Single-line labels sit at the top
    # of each cell, so those values use a baseline just above the bottom rule.
    # Payment and income labels are two lines and fill the cell; those amounts
    # go in the blank space to the right of "/Mo" or "GROSS:" / "NET:".
    if not coapplicant:
        rows = {
            'name': 248, 'residence': 279, 'mailing': 310, 'previous': 342,
            'housing': 387, 'employer': 417, 'work': 457, 'other_income': 493,
            'support': 542, 'auto1': 577, 'auto2': 606, 'signature': 1116,
        }
        name_x, ssn_x, dob_x = 52, 652, 822
        address_width = 580
        home_x, cell_x = 652, 822
        email_x, landlord_x, payment_x = 52, 526, 844
        employer_x, occupation_x, employment_date_x = 52, 652, 822
        work_x, extension_x, earnings_x = 52, 168, 334
        previous_employer_x, previous_date_x = 458, 822
        other_income_x, other_source_x = 102, 264
        total_income_x, total_income_width = 830, 140
        other_value_baseline = 490
        total_value_baseline = 490
        employer_income_baseline = 450
        employer_income_width = 116
        payment_baseline = 375
        auto_x, auto_financer_x = 52, 430
        housing_checks = {
            'buy': (330, 361), 'rent': (435, 361),
            'own': (330, 382), 'other': (435, 382),
        }
        support_checks = (810, 512)
        support_yes = (892, 512)
    else:
        rows = {
            'name': 638, 'residence': 668, 'mailing': 0, 'previous': 0,
            'housing': 708, 'employer': 752, 'work': 790, 'previous_work': 827,
            'other_income': 865, 'support': 916, 'auto1': 0, 'auto2': 0,
            'signature': 1155,
        }
        name_x, ssn_x, dob_x = 52, 652, 822
        address_width = 750
        home_x, cell_x = 52, 220
        email_x, landlord_x, payment_x = 52, 0, 846
        employer_x, occupation_x, employment_date_x = 330, 52, 332
        work_x, extension_x, earnings_x = 656, 788, 134
        previous_employer_x, previous_date_x = 332, 806
        other_income_x, other_source_x = 102, 264
        total_income_x, total_income_width = 844, 126
        other_value_baseline = 861
        total_value_baseline = 861
        employer_income_baseline = 824
        employer_income_width = 186
        payment_baseline = 701
        housing_checks = {
            'buy': (651, 687), 'rent': (734, 687),
            'own': (651, 708), 'other': (734, 708),
        }
        support_checks = (817, 884)
        support_yes = (900, 884)

    _draw_text(pdf, name_x, rows['name'], person['name'], 585)
    _draw_text(pdf, ssn_x, rows['name'], person['ssn'], 155)
    _draw_text(pdf, dob_x, rows['name'], _date_text(person['dob']), 150)
    _draw_text(
        pdf, 52, rows['residence'], person['residence_address'], address_width,
    )
    _draw_text(pdf, 822, rows['residence'], _person_tenure(person), 118)

    if not coapplicant:
        _draw_text(pdf, 52, rows['mailing'], person['mailing_address'], 585)
        _draw_text(pdf, home_x, rows['mailing'], person['home_phone'], 155)
        _draw_text(pdf, cell_x, rows['mailing'], person['cell_phone'], 150)
        _draw_text(pdf, 52, rows['previous'], person['previous_address'], 585)
        _draw_text(
            pdf, 652, rows['previous'],
            _person_tenure({
                'residence_years': person['previous_residence_years'],
                'residence_months': person['previous_residence_months'],
            }),
            120,
        )
        _draw_text(pdf, 822, rows['previous'], person['dependents'], 145)
        _draw_text(pdf, email_x, rows['housing'], person['email'], 265)
    else:
        _draw_text(pdf, home_x, rows['housing'], person['home_phone'], 155)
        _draw_text(pdf, cell_x, rows['housing'], person['cell_phone'], 130)
        _draw_text(pdf, 368, rows['housing'], person['dependents'], 90)
        _draw_text(pdf, email_x, rows['employer'], person['email'], 265)
        _draw_text(
            pdf, payment_x, payment_baseline,
            _money_text(person['housing_payment']), 125,
        )

    _draw_check(pdf, *housing_checks[person['housing_status']])
    if not coapplicant:
        _draw_text(pdf, landlord_x, rows['housing'], person['landlord'], 275)
        _draw_text(
            pdf, payment_x, payment_baseline,
            _money_text(person['housing_payment']), 125,
        )
        _draw_text(
            pdf, employer_x, rows['employer'], person['employer_name_address'], 585,
        )
        _draw_text(pdf, occupation_x, rows['employer'], person['occupation'], 155)
        _draw_text(
            pdf, employment_date_x, rows['employer'],
            _date_text(person['employment_date']), 145,
        )
        _draw_text(pdf, work_x, rows['work'], person['work_phone'], 100)
        _draw_text(pdf, extension_x, rows['work'], person['work_extension'], 70)
        _draw_income(
            pdf, earnings_x, employer_income_baseline, person['employment_gross'],
            person['employment_net'], employer_income_width,
        )
        _draw_text(
            pdf, previous_employer_x, rows['work'],
            person['previous_employer'], 345,
        )
        _draw_text(
            pdf, previous_date_x, rows['work'],
            _date_text(person['previous_employment_date']), 145,
        )
        _draw_text(
            pdf, other_income_x, other_value_baseline,
            _money_text(person['other_income_gross']), 150,
        )
        _draw_text(
            pdf, other_source_x, rows['other_income'],
            person['other_income_source'], 460,
        )
    else:
        _draw_text(
            pdf, employer_x, rows['employer'], person['employer_name_address'], 630,
        )
        _draw_text(pdf, occupation_x, rows['work'], person['occupation'], 260)
        _draw_text(
            pdf, employment_date_x, rows['work'],
            _date_text(person['employment_date']), 300,
        )
        _draw_text(pdf, work_x, rows['work'], person['work_phone'], 120)
        _draw_text(pdf, extension_x, rows['work'], person['work_extension'], 70)
        _draw_income(
            pdf, earnings_x, employer_income_baseline, person['employment_gross'],
            person['employment_net'], employer_income_width, max_font=7.2,
        )
        _draw_text(
            pdf, previous_employer_x, rows['previous_work'],
            person['previous_employer'], 455,
        )
        _draw_text(
            pdf, previous_date_x, rows['previous_work'],
            _date_text(person['previous_employment_date']), 160,
        )
        _draw_text(
            pdf, other_income_x, other_value_baseline,
            _money_text(person['other_income_gross']), 150,
        )
        _draw_text(
            pdf, other_source_x, rows['other_income'],
            person['other_income_source'], 460,
        )

    _draw_income(
        pdf, total_income_x, total_value_baseline,
        person['total_gross_income'], person['total_net_income'],
        total_income_width, include_symbol=False, max_font=7.2,
    )

    if person['alimony_obligation'] == 'no':
        _draw_check(pdf, *support_checks)
    else:
        _draw_check(pdf, *support_yes)
        _draw_text(
            pdf, 616, rows['support'],
            _money_text(person['alimony_amount'], include_symbol=False), 250,
        )

    if not coapplicant:
        for number, row in ((1, rows['auto1']), (2, rows['auto2'])):
            _draw_text(
                pdf, auto_x, row, person[f'auto_{number}_year_model'], 360,
            )
            _draw_text(
                pdf, auto_financer_x, row,
                person[f'auto_{number}_financed_by'], 320,
            )
            responsibility = person[f'auto_{number}_responsibility']
            if responsibility in {'applicant', 'coapplicant'}:
                _draw_check(
                    pdf,
                    774 if responsibility == 'applicant' else 868,
                    565 if number == 1 else 595,
                )

    if not coapplicant:
        _draw_text(
            pdf, 52, 1116,
            f"{person['license_number']}  {person['license_state'].upper()}",
            240,
        )
        _draw_text(pdf, 52, 1155, person['other_qualifying_id'], 240)
    _draw_text(pdf, 438, rows['signature'], person['signature'], 360)
    _draw_text(
        pdf, 820, rows['signature'],
        date.today().strftime('%m/%d/%Y'), 145,
    )


def _build_supplemental_page(application):
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=(612, 792))
    pdf.setTitle('Financing Application - Supplemental Residence History')
    pdf.setFont('Helvetica-Bold', 15)
    pdf.drawString(48, 746, 'Lendmark Financial Services - Supplemental Information')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(
        48, 727,
        'This page supplements the attached signed Lendmark credit application.',
    )
    vehicle = application['vehicle']
    vehicle_text = (
        f"{vehicle['year']} {vehicle['make']} {vehicle['model']} "
        f"{vehicle['trim']}".strip()
    )
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(
        48, 697,
        _pdf_safe(f"Applicant: {application['applicant']['name']}"),
    )
    if application['coapplicant']:
        pdf.drawString(
            48, 679,
            _pdf_safe(f"Co-applicant: {application['coapplicant']['name']}"),
        )
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(48, 646, 'Vehicle of interest')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(60, 628, _pdf_safe(f'Vehicle: {vehicle_text}'))
    pdf.drawString(
        60, 612,
        _pdf_safe(f"VIN: {vehicle['vin'] or 'Not available'}"),
    )
    if vehicle['stock_number']:
        pdf.drawString(60, 596, _pdf_safe(f"Stock #: {vehicle['stock_number']}"))
    pdf.drawString(
        60, 580,
        _pdf_safe(f"Advertised price: {_money_text(vehicle['price'])}"),
    )
    pdf.drawString(
        60, 564,
        _pdf_safe(f"Amount requested: {_money_text(application['requested_amount'])}"),
    )
    pdf.drawString(
        60, 548,
        f"Credit type: {'Joint' if application['joint_credit'] else 'Individual'}",
    )
    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(48, 514, 'Online submission authorizations')
    pdf.setFont('Helvetica', 9)
    pdf.drawString(60, 496, 'Applicant certified the information as accurate: Yes')
    pdf.drawString(60, 480, 'Credit-report authorization: Yes')
    pdf.drawString(60, 464, 'Electronic signature consent: Yes')

    pdf.setFont('Helvetica-Bold', 10)
    pdf.drawString(48, 426, 'Monthly income summary (gross / net)')
    y = 409
    pdf.setFont('Helvetica', 9)
    for label, person in (
        ('Applicant', application['applicant']),
        ('Co-applicant', application['coapplicant']),
    ):
        if not person:
            continue
        pdf.drawString(
            60, y,
            _pdf_safe(
                f"{label}: {_money_text(person['total_gross_income'])} / "
                f"{_money_text(person['total_net_income'])}",
            ),
        )
        y -= 15
    y -= 7
    for label, person in (
        ('Applicant', application['applicant']),
        ('Co-applicant', application['coapplicant']),
    ):
        if not person or not person['previous_address']:
            continue
        pdf.setFont('Helvetica-Bold', 11)
        pdf.drawString(48, y, _pdf_safe(f'{label}: {person["name"]}'))
        y -= 22
        pdf.setFont('Helvetica', 9)
        lines = simpleSplit(
            _pdf_safe(f'Current residence: {person["residence_address"]}'),
            'Helvetica', 9, 510,
        )
        for line in lines:
            pdf.drawString(60, y, line)
            y -= 13
        pdf.drawString(
            60, y,
            _pdf_safe(f'Current residence duration: {_person_tenure(person)}'),
        )
        y -= 13
        lines = simpleSplit(
            _pdf_safe(f'Previous residence: {person["previous_address"]}'),
            'Helvetica', 9, 510,
        )
        for line in lines:
            pdf.drawString(60, y, line)
            y -= 13
        previous_tenure = _person_tenure({
            'residence_years': person['previous_residence_years'],
            'residence_months': person['previous_residence_months'],
        })
        pdf.drawString(
            60, y, _pdf_safe(f'Previous residence duration: {previous_tenure}'),
        )
        y -= 26
    pdf.save()
    output.seek(0)
    return output


def build_lender_pdf(application):
    from flask import current_app

    source_path = Path(current_app.config['LENDMARK_APPLICATION_PDF'])
    with source_path.open('rb') as source:
        original = PdfReader(source)
        if len(original.pages) != 1:
            raise ValueError('The lender application PDF must contain exactly one page.')

        overlay_buffer = BytesIO()
        overlay = canvas.Canvas(overlay_buffer, pagesize=(612, 792))
        overlay.setTitle('Lendmark Financial Services Application')
        _draw_text(overlay, 120, 216, 'Vehicle purchase', 245)
        _draw_text(
            overlay, 502, 216, _money_text(application['requested_amount']), 110,
        )
        _draw_text(overlay, 682, 216, 'Marshall Auto website', 112)
        if application['joint_credit']:
            _draw_check(overlay, 775, 99)
        else:
            _draw_check(overlay, 775, 124)
        _draw_person_on_original(overlay, application['applicant'])
        if application['coapplicant']:
            _draw_person_on_original(
                overlay, application['coapplicant'], coapplicant=True,
            )
        overlay.save()
        overlay_buffer.seek(0)

        writer = PdfWriter()
        writer.append(original)
        writer.pages[0].merge_page(PdfReader(overlay_buffer).pages[0])
        supplemental = _build_supplemental_page(application)
        writer.add_page(PdfReader(supplemental).pages[0])
        writer.add_metadata({
            '/Title': 'Completed Lendmark Financial Services Credit Application',
            '/Author': 'Marshall Auto',
        })
        output = BytesIO()
        writer.write(output)
        return output.getvalue()


def _application_storage_directory():
    from flask import current_app

    directory = Path(current_app.config['PRIVATE_UPLOAD_FOLDER']) / 'financing_applications'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def store_encrypted_pdf(pdf_bytes):
    cipher = _cipher()
    filename = f'{uuid.uuid4().hex}.enc'
    path = _application_storage_directory() / filename
    file_descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(file_descriptor, 'wb') as stored_file:
            stored_file.write(cipher.encrypt(pdf_bytes))
            stored_file.flush()
            os.fsync(stored_file.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            current_app.logger.exception(
                'Could not remove an incomplete encrypted financing PDF.',
            )
        raise
    return filename


def load_encrypted_pdf(filename):
    if not _STORED_FILENAME_PATTERN.fullmatch(filename or ''):
        raise ValueError('Invalid financing application filename.')
    path = _application_storage_directory() / filename
    with path.open('rb') as stored_file:
        encrypted = stored_file.read()
    return _cipher().decrypt(encrypted)


def remove_encrypted_pdf(filename):
    if not _STORED_FILENAME_PATTERN.fullmatch(filename or ''):
        raise ValueError('Invalid financing application filename.')
    path = _application_storage_directory() / filename
    path.unlink()


def save_application(application):
    pdf_bytes = build_lender_pdf(application)
    filename = store_encrypted_pdf(pdf_bytes)
    record = FinancingApplication(
        vehicle_id=application['vehicle'].get('id'),
        encrypted_pdf_filename=filename,
        joint_credit=application['joint_credit'],
        submitted_at=application['submitted_at'],
    )
    db.session.add(record)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        try:
            remove_encrypted_pdf(filename)
        except FileNotFoundError:
            pass
        except OSError:
            current_app.logger.exception(
                'Could not remove an orphaned encrypted financing PDF.',
            )
        raise
    return record


def delete_application_record(record):
    try:
        remove_encrypted_pdf(record.encrypted_pdf_filename)
    except FileNotFoundError:
        from flask import current_app

        current_app.logger.warning(
            'Encrypted financing application file %s was missing during deletion.',
            record.id,
        )
    db.session.delete(record)
    db.session.commit()


def admin_is_online():
    from app.chat import PRESENCE_SECONDS

    return db.session.query(ChatPresence.id).join(
        User, User.id == ChatPresence.user_id,
    ).filter(
        User.role == User.ROLE_ADMIN,
        User.is_active_user.is_(True),
        User.auth_version == ChatPresence.auth_version,
        ChatPresence.last_seen_at > utcnow() - timedelta(seconds=PRESENCE_SECONDS),
    ).first() is not None


def _textbelt_phone(phone):
    raw = (phone or '').strip()
    digits = ''.join(character for character in raw if character.isdigit())
    if raw.startswith('+') and 7 <= len(digits) <= 15:
        return f'+{digits}'
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return None


def _send_textbelt_message(phone, message, api_key):
    payload = urlencode({
        'phone': phone,
        'message': message,
        'key': api_key,
    }).encode('ascii')
    request = urllib.request.Request(
        'https://textbelt.com/text',
        data=payload,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode('utf-8'))
    except (OSError, TimeoutError, urllib.error.URLError, ValueError) as exc:
        current_app.logger.error(
            'Textbelt financing notification failed (%s).', type(exc).__name__,
        )
        return False
    if not isinstance(result, dict) or result.get('success') is not True:
        current_app.logger.error('Textbelt did not accept a financing notification.')
        return False
    return True


def notify_salespeople_by_textbelt(vehicle):
    api_key = current_app.config.get('TEXTBELT_API_KEY')
    if not api_key:
        current_app.logger.error(
            'Financing application was saved, but TEXTBELT_API_KEY is not configured.',
        )
        return

    salespeople = User.query.filter_by(
        role=User.ROLE_SALESMAN, is_active_user=True,
    ).filter(User.phone.isnot(None)).all()
    title = f"{vehicle.year} {vehicle.make} {vehicle.model}"
    if vehicle.trim:
        title += f' {vehicle.trim}'
    message = (
        f'New financing application for {title}. '
        'Sign in to the Marshall Auto admin panel to review it.'
    )
    phones = set()
    missing_phone_count = 0
    for salesperson in salespeople:
        phone = _textbelt_phone(salesperson.phone)
        if phone:
            phones.add(phone)
        else:
            missing_phone_count += 1
    if missing_phone_count:
        current_app.logger.warning(
            '%d active salesperson account(s) have no usable SMS phone number.',
            missing_phone_count,
        )
    if not phones:
        current_app.logger.warning(
            'No active salespeople have a usable phone number for financing alerts.',
        )
        return

    failed = sum(
        not _send_textbelt_message(phone, message, api_key)
        for phone in sorted(phones)
    )
    if failed:
        current_app.logger.error(
            'Textbelt financing alerts failed for %d of %d salesperson number(s).',
            failed, len(phones),
        )
    else:
        current_app.logger.info(
            'Textbelt financing alert sent to %d salesperson number(s).',
            len(phones),
        )
