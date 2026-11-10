"""Public shopper requests: test drives, 72-hour holds, and inventory alerts.

Requests create a lead for the existing inbox. They do not check a vehicle out,
collect a deposit, or reserve a vehicle until staff confirms the request.
"""
from datetime import datetime, timedelta

from flask import current_app, url_for
from sqlalchemy import or_

from app import db
from app.models import InventoryAlert, Lead, TestDrive, Vehicle, VehicleHoldRequest, utcnow
from app.utils import dealership_now, format_phone_number, to_dealership_time

HOLD_HOURS = 72
APPOINTMENT_HOURS = (9, 10, 11, 12, 13, 14, 15, 16, 17)
class ShopperRequestError(ValueError):
    """A public request that cannot be accepted as submitted."""


def appointment_slots(days=7):
    """Weekday appointment choices during posted business hours."""
    start = dealership_now().replace(minute=0, second=0, microsecond=0)
    slots = []
    cursor = start + timedelta(hours=1)
    while len(slots) < days * len(APPOINTMENT_HOURS) and cursor < start + timedelta(days=21):
        if cursor.weekday() < 5 and cursor.hour in APPOINTMENT_HOURS and cursor > start:
            slots.append(cursor)
        cursor += timedelta(hours=1)
    return slots


def _lead(name, email, phone, message, vehicle, source):
    phone = format_phone_number(phone)
    lead = Lead(
        name=name.strip(),
        email=email.strip(),
        phone=(phone or '').strip() or None,
        message=message,
        vehicle_id=vehicle.id if vehicle is not None else None,
        source=source,
    )
    db.session.add(lead)
    db.session.flush()
    return lead


def create_test_drive_request(vehicle, *, name, email, phone, appointment_at, notes=''):
    if not vehicle.is_available:
        raise ShopperRequestError('This vehicle is not currently available for a test drive.')
    if appointment_at not in appointment_slots():
        raise ShopperRequestError('Choose an available weekday appointment time.')
    phone = format_phone_number(phone)
    message = (
        f"Test-drive request for {vehicle.title} on "
        f"{appointment_at.strftime('%A, %B %-d at %-I:%M %p')}.\n"
        f"{(notes or '').strip()}"
    ).strip()
    lead = _lead(name, email, phone, message, vehicle, 'test_drive_request')
    drive = TestDrive(
        vehicle_id=vehicle.id,
        customer_name=name.strip(),
        customer_phone=(phone or '').strip() or None,
        customer_email=email.strip(),
        notes=message,
        started_at=appointment_at,
        scheduled_at=appointment_at,
        status='requested',
    )
    db.session.add(drive)
    db.session.commit()
    _notify(lead)
    return lead, drive


def create_hold_request(vehicle, *, name, email, phone, deposit_amount, acknowledged):
    if not vehicle.is_available:
        raise ShopperRequestError('This vehicle is not available to hold.')
    if not acknowledged:
        raise ShopperRequestError('The 72-hour hold terms must be acknowledged before this request can be submitted.')
    phone = format_phone_number(phone)
    amount = deposit_amount
    if amount is None or amount <= 0:
        raise ShopperRequestError('Enter the deposit amount you are prepared to pay at the dealership.')
    expires = to_dealership_time(utcnow() + timedelta(hours=HOLD_HOURS))
    message = (
        f"In-person 72-hour hold for {vehicle.title}. "
        f"Deposit: ${amount:,.2f}, payable only at the dealership in cash or by cashier's check "
        f"with the original receipt from the issuing bank branch. "
        f"Proposed expiration if paid and signed now: "
        f"{expires.strftime('%B %-d, %Y at %-I:%M %p %Z')}."
    )
    lead = _lead(name, email, phone, message, vehicle, 'hold_request')
    hold = VehicleHoldRequest(
        vehicle_id=vehicle.id,
        lead_id=lead.id,
        customer_name=name.strip(),
        customer_email=email.strip(),
        customer_phone=(phone or '').strip() or None,
        deposit_amount=amount,
        status='requested',
        terms_acknowledged_at=utcnow(),
    )
    db.session.add(hold)
    db.session.commit()
    _notify(lead)
    return lead, hold


def create_inventory_alert(
    vehicle,
    *,
    name,
    email,
    phone,
    alert_type,
    max_price=None,
    min_price=None,
    max_mileage=None,
    make=None,
    body_style=None,
    title_status=None,
    search_query=None,
):
    if alert_type not in ('vehicle', 'make', 'body_style', 'search'):
        raise ShopperRequestError('Choose a valid alert.')
    phone = format_phone_number(phone)
    if alert_type == 'vehicle' and not vehicle:
        raise ShopperRequestError('Choose a vehicle for this alert.')
    for label, amount in (('minimum price', min_price), ('maximum price', max_price)):
        if amount is not None and amount <= 0:
            raise ShopperRequestError(f'Enter a {label} above zero, or leave it blank.')
    if max_mileage is not None and max_mileage <= 0:
        raise ShopperRequestError('Enter a maximum mileage above zero, or leave it blank.')
    if (
        min_price is not None
        and max_price is not None
        and min_price > max_price
    ):
        raise ShopperRequestError('Minimum price cannot be higher than maximum price.')

    make_value = (make or (vehicle.make if vehicle and alert_type == 'make' else None) or '').strip() or None
    body_value = (
        body_style or (vehicle.body_style if vehicle and alert_type == 'body_style' else None) or ''
    ).strip() or None
    title_value = (title_status or '').strip().lower() or None
    if title_value and title_value not in ('clean', 'rebuilt', 'salvage'):
        raise ShopperRequestError('Choose a valid title status filter.')
    query_value = ' '.join((search_query or '').split())[:128] or None

    if alert_type == 'make' and not make_value:
        raise ShopperRequestError('Choose a make for this alert.')
    if alert_type == 'body_style' and not body_value:
        raise ShopperRequestError('Choose a body style for this alert.')
    if alert_type == 'search' and not any(
        (make_value, body_value, min_price, max_price, max_mileage, title_value, query_value)
    ):
        raise ShopperRequestError('Add at least one search filter before creating an alert.')

    if alert_type == 'vehicle':
        target = vehicle.title
    elif alert_type == 'make':
        target = make_value
    elif alert_type == 'body_style':
        target = body_value
    else:
        bits = []
        if query_value:
            bits.append(f'"{query_value}"')
        if make_value:
            bits.append(make_value)
        if body_value:
            bits.append(body_value)
        if title_value:
            bits.append(f'{title_value} title')
        if min_price is not None:
            bits.append(f'from ${min_price:,.0f}')
        if max_price is not None:
            bits.append(f'up to ${max_price:,.0f}')
        if max_mileage is not None:
            bits.append(f'under {int(max_mileage):,} mi')
        target = ', '.join(bits) or 'saved search'

    message = f"Inventory alert ({alert_type}) for {target}."
    lead = _lead(name, email, phone, message, vehicle if alert_type == 'vehicle' else None, 'inventory_alert')
    alert = InventoryAlert(
        lead_id=lead.id,
        vehicle_id=vehicle.id if alert_type == 'vehicle' and vehicle else None,
        customer_name=name.strip(),
        customer_email=email.strip(),
        customer_phone=(phone or '').strip() or None,
        alert_type=alert_type,
        make=make_value if alert_type in ('make', 'search') else None,
        body_style=body_value if alert_type in ('body_style', 'search') else None,
        min_price=min_price if alert_type == 'search' else None,
        max_price=max_price,
        max_mileage=max_mileage if alert_type == 'search' else None,
        title_status=title_value if alert_type == 'search' else None,
        search_query=query_value if alert_type == 'search' else None,
        last_seen_price=vehicle.display_price if alert_type == 'vehicle' and vehicle else None,
        status='active',
    )
    db.session.add(alert)
    db.session.commit()
    _notify(lead)
    return lead, alert


def confirm_hold(hold):
    if hold.status != 'requested':
        raise ShopperRequestError('Only a requested hold can be confirmed.')
    now = utcnow()
    hold.status = 'active'
    hold.confirmed_at = now
    hold.expires_at = now + timedelta(hours=HOLD_HOURS)
    db.session.commit()


def similar_vehicles(vehicle, limit=4):
    """Rank available alternatives by body style, price, and mileage proximity.

    Coming Soon teasers stay on /coming-soon only — their inventory detail URLs 404.
    """
    price = float(vehicle.display_price or 0)
    mileage = vehicle.mileage or 0
    candidates = (
        Vehicle.query
        .filter(
            Vehicle.id != vehicle.id,
            Vehicle.status == 'available',
            Vehicle.is_coming_soon.is_(False),
        )
        .all()
    )

    def score(candidate):
        same_make = candidate.make == vehicle.make
        same_body = bool(vehicle.body_style) and candidate.body_style == vehicle.body_style
        if not same_make and not same_body:
            return None
        price_gap = abs(float(candidate.display_price or 0) - price)
        mileage_gap = abs((candidate.mileage or 0) - mileage)
        return (
            0 if same_body else 1,
            0 if same_make else 1,
            price_gap,
            mileage_gap,
        )

    ranked = [(score(item), item) for item in candidates]
    ranked = [item for item in ranked if item[0] is not None]
    ranked.sort(key=lambda item: item[0])
    return [item for _, item in ranked[:limit]]


def process_inventory_alerts():
    """Email active alerts when a watched vehicle drops in price or a match appears."""
    sent = 0
    alerts = InventoryAlert.query.filter_by(status='active').all()
    for alert in alerts:
        match = _alert_match(alert)
        if not match:
            continue
        if _send_alert(alert, match):
            alert.last_notified_at = utcnow()
            if alert.alert_type == 'vehicle':
                alert.last_seen_price = match.display_price
            sent += 1
    db.session.commit()
    return sent


def _alert_match(alert):
    if alert.alert_type == 'vehicle':
        vehicle = alert.vehicle
        if not vehicle or not vehicle.is_available or vehicle.display_price is None:
            return None
        if alert.last_seen_price is None or vehicle.display_price < alert.last_seen_price:
            return vehicle
        return None

    query = Vehicle.query.filter_by(status='available', is_coming_soon=False)
    if alert.alert_type == 'make':
        if not alert.make:
            return None
        query = query.filter(Vehicle.make.ilike(alert.make))
    elif alert.alert_type == 'body_style':
        if not alert.body_style:
            return None
        query = query.filter(Vehicle.body_style.ilike(alert.body_style))
    elif alert.alert_type == 'search':
        if alert.make:
            query = query.filter(Vehicle.make.ilike(alert.make))
        if alert.body_style:
            query = query.filter(Vehicle.body_style.ilike(alert.body_style))
        if alert.title_status:
            query = query.filter(Vehicle.title_status == alert.title_status)
        if alert.search_query:
            term = f"%{alert.search_query}%"
            query = query.filter(
                or_(
                    Vehicle.make.ilike(term),
                    Vehicle.model.ilike(term),
                    Vehicle.trim.ilike(term),
                    Vehicle.slug.ilike(term),
                )
            )
        if alert.min_price is not None:
            query = query.filter(Vehicle.display_price >= alert.min_price)
        if alert.max_mileage is not None:
            query = query.filter(Vehicle.mileage <= alert.max_mileage)
    else:
        return None
    if alert.max_price is not None:
        query = query.filter(Vehicle.display_price <= alert.max_price)
    if alert.last_notified_at:
        query = query.filter(Vehicle.created_at > alert.last_notified_at)
    return query.order_by(Vehicle.created_at.desc()).first()


def _send_alert(alert, vehicle):
    from email.message import EmailMessage
    import smtplib

    server = current_app.config.get('MAIL_SERVER')
    sender = current_app.config.get('MAIL_DEFAULT_SENDER') or current_app.config.get('BUSINESS_EMAIL')
    if not current_app.config.get('SEND_LEAD_EMAIL') or not server or not sender:
        current_app.logger.info('Inventory alert queued without email: %s', alert.customer_email)
        return True
    link = url_for('main.vehicle_detail', slug=vehicle.slug, _external=True)
    message = EmailMessage()
    message['Subject'] = f"Marshall Auto alert: {vehicle.title}"
    message['From'] = sender
    message['To'] = alert.customer_email
    message.set_content(
        f"Hello {alert.customer_name},\n\n"
        f"{vehicle.title} matches your Marshall Auto alert. "
        f"The current advertised price is ${vehicle.display_price:,.2f}.\n\n"
        f"{link}\n\n"
        "This is an availability or price notice, not a financing offer. "
        "Reply to this email or call the dealership to ask about the vehicle.\n"
    )
    try:
        with smtplib.SMTP(server, current_app.config.get('MAIL_PORT', 587), timeout=10) as smtp:
            if current_app.config.get('MAIL_USE_TLS', True):
                smtp.starttls()
            username = current_app.config.get('MAIL_USERNAME')
            password = current_app.config.get('MAIL_PASSWORD')
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    except Exception as exc:
        current_app.logger.error('Inventory alert email failed: %s', exc)
        return False
    return True


def _notify(lead):
    from app.utils import notify_new_lead, send_lead_confirmation
    notify_new_lead(lead)
    send_lead_confirmation(lead)


def vehicle_interest_message(vehicle, *, business_name=None):
    """Short prefilled message for chat, SMS, and contact forms."""
    if not vehicle:
        return 'Hello, I have a question about a vehicle on your website.'
    stock = vehicle.stock_number or vehicle.id
    price = vehicle.display_price
    price_bit = f', OTD ${price:,.0f}' if price is not None else ''
    status = ''
    if getattr(vehicle, 'status', None) == 'available' and not getattr(vehicle, 'is_coming_soon', False):
        status = ' Is it still available?'
    return (
        f'Hello, I am interested in the {vehicle.title} (Stock #{stock}{price_bit}).'
        f'{status}'
    ).strip()
