"""Monthly new-car outreach for formatted lead contacts.

Leads stay in the inbox; this module keeps a separate, deduplicated contact
list with normalized phone/email values, monthly send logic, and durable
opt-out state for texts and emails.
"""
from __future__ import annotations

import smtplib
from datetime import timedelta
from email.message import EmailMessage

from flask import current_app, url_for
from sqlalchemy import or_

from app import db
from app.models import Lead, OutreachContact, Vehicle, utcnow
from app.utils import format_email_address, format_phone_number, normalize_phone_e164


def _new_unsubscribe_token():
    import secrets
    return secrets.token_urlsafe(24)


def upsert_outreach_contact_from_lead(lead, *, commit=False):
    """Add or refresh an outreach contact from a non-spam lead.

    Existing opt-outs are preserved: a later lead never silently re-subscribes.
    """
    if lead is None or getattr(lead, 'is_spam', False):
        return None

    email = format_email_address(lead.email) or None
    phone_display = format_phone_number(lead.phone) or None
    phone_e164 = normalize_phone_e164(lead.phone or phone_display)
    if phone_e164 and not phone_display:
        phone_display = format_phone_number(phone_e164) or None
    if not email and not phone_e164 and not phone_display:
        return None

    contact = None
    if phone_e164:
        contact = OutreachContact.query.filter_by(phone_e164=phone_e164).first()
    if contact is None and email:
        contact = OutreachContact.query.filter_by(email=email).first()

    name = (lead.name or '').strip()[:128] or 'Customer'
    source = (lead.source or 'contact')[:64]
    now = utcnow()

    if contact is None:
        contact = OutreachContact(
            name=name,
            email=email,
            phone=phone_display,
            phone_e164=phone_e164,
            last_lead_id=lead.id,
            source=source,
            sms_opt_in=bool(phone_e164),
            email_opt_in=bool(email),
            unsubscribe_token=_new_unsubscribe_token(),
        )
        db.session.add(contact)
    else:
        contact.name = name or contact.name
        contact.source = source or contact.source
        contact.last_lead_id = lead.id
        contact.updated_at = now
        # Refresh contact channels without reviving a prior opt-out.
        if email and (contact.email_opt_in or not contact.email):
            contact.email = email
        if phone_e164 and (contact.sms_opt_in or not contact.phone_e164):
            contact.phone_e164 = phone_e164
            contact.phone = phone_display or format_phone_number(phone_e164) or contact.phone
        elif phone_display and not contact.phone:
            contact.phone = phone_display

    # Keep the lead row itself formatted for the inbox.
    if email and lead.email != email:
        lead.email = email
    if phone_display and lead.phone != phone_display:
        lead.phone = phone_display[:32]

    if commit:
        db.session.commit()
    else:
        db.session.flush()
    return contact


def sync_outreach_contacts_from_leads():
    """Backfill outreach contacts from existing non-spam leads (newest first)."""
    query = (
        Lead.query
        .filter(Lead.is_spam.is_(False))
        .order_by(Lead.created_at.desc(), Lead.id.desc())
    )
    created = 0
    updated = 0
    for lead in query.all():
        email = format_email_address(lead.email) or None
        phone_e164 = normalize_phone_e164(lead.phone)
        existing = None
        if phone_e164:
            existing = OutreachContact.query.filter_by(phone_e164=phone_e164).first()
        if existing is None and email:
            existing = OutreachContact.query.filter_by(email=email).first()
        contact = upsert_outreach_contact_from_lead(lead, commit=False)
        if contact is None:
            continue
        if existing is None:
            created += 1
        else:
            updated += 1
    db.session.commit()
    return {'created': created, 'updated': updated, 'total': OutreachContact.query.count()}


def opt_out_sms(contact, *, reason='customer_request'):
    contact.sms_opt_in = False
    contact.sms_opted_out_at = utcnow()
    contact.opt_out_reason = (reason or 'customer_request')[:128]
    contact.updated_at = utcnow()
    return contact


def opt_out_email(contact, *, reason='customer_request'):
    contact.email_opt_in = False
    contact.email_opted_out_at = utcnow()
    contact.opt_out_reason = (reason or 'customer_request')[:128]
    contact.updated_at = utcnow()
    return contact


def opt_out_all(contact, *, reason='customer_request'):
    opt_out_sms(contact, reason=reason)
    opt_out_email(contact, reason=reason)
    return contact


def find_contact_by_phone(phone):
    e164 = normalize_phone_e164(phone)
    if not e164:
        return None
    return OutreachContact.query.filter_by(phone_e164=e164).first()


def find_contact_by_token(token):
    token = (token or '').strip()
    if not token:
        return None
    return OutreachContact.query.filter_by(unsubscribe_token=token).first()


def _inventory_highlights(limit=5):
    return (
        Vehicle.query
        .filter_by(status='available', is_coming_soon=False)
        .order_by(Vehicle.created_at.desc(), Vehicle.id.desc())
        .limit(limit)
        .all()
    )


def _due_contacts(min_days):
    cutoff = utcnow() - timedelta(days=max(int(min_days), 1))
    return (
        OutreachContact.query
        .filter(
            or_(
                OutreachContact.sms_opt_in.is_(True),
                OutreachContact.email_opt_in.is_(True),
            ),
            or_(
                OutreachContact.last_outreach_at.is_(None),
                OutreachContact.last_outreach_at <= cutoff,
            ),
        )
        .order_by(OutreachContact.id.asc())
        .all()
    )


def _absolute_url(endpoint, **values):
    """Build absolute URLs from SITE_URL so workers are proxy-host independent."""
    path = url_for(endpoint, **values)
    return f"{current_app.config['SITE_URL'].rstrip('/')}{path}"


def _unsubscribe_url(contact):
    return _absolute_url('main.outreach_unsubscribe', token=contact.unsubscribe_token)


def _inventory_url():
    return _absolute_url('main.inventory')


def _sms_body(contact, vehicles):
    business = current_app.config.get('BUSINESS_NAME', 'Marshall Auto')
    first = contact.first_name
    if vehicles:
        top = vehicles[0]
        price = f" OTD ${top.display_price:,.0f}" if top.display_price is not None else ''
        teaser = f"New on the lot: {top.title}{price}."
        if len(vehicles) > 1:
            teaser += f" Plus {len(vehicles) - 1} more."
    else:
        teaser = 'We have fresh inventory on the lot.'
    stop_url = _unsubscribe_url(contact)
    return (
        f"Hi {first}, {business} here. {teaser} "
        f"Browse: {_inventory_url()} "
        f"About 1 text/month. Stop anytime: {stop_url}"
    )


def _email_body(contact, vehicles):
    business = current_app.config.get('BUSINESS_NAME', 'Marshall Auto')
    phone = current_app.config.get('BUSINESS_PHONE', '')
    lines = [
        f"Hi {contact.first_name},",
        '',
        f"Just a friendly monthly note from {business}: we wanted to share a few "
        f"vehicles currently available in Sanford, NC.",
        '',
    ]
    if vehicles:
        lines.append('Highlights:')
        for vehicle in vehicles:
            price = f"${vehicle.display_price:,.0f} OTD" if vehicle.display_price is not None else 'Call for price'
            link = _absolute_url('main.vehicle_detail', slug=vehicle.slug)
            lines.append(f"  • {vehicle.title} — {price}")
            lines.append(f"    {link}")
        lines.append('')
    lines.extend([
        f"Full inventory: {_inventory_url()}",
        '',
        f"Questions? Call us at {phone} or reply to this email.",
        '',
        f"You are receiving this because you contacted {business}.",
        f"Stop texts and emails anytime: {_unsubscribe_url(contact)}",
        '',
        f"— {business}",
    ])
    return '\n'.join(lines)


def _send_email(contact, vehicles):
    if not contact.email:
        return False
    if current_app.config.get('OUTREACH_DRY_RUN'):
        current_app.logger.info('Outreach email dry-run to %s', contact.email)
        return True

    server = current_app.config.get('MAIL_SERVER')
    sender = current_app.config.get('MAIL_DEFAULT_SENDER') or current_app.config.get('BUSINESS_EMAIL')
    if not current_app.config.get('SEND_LEAD_EMAIL') or not server or not sender:
        current_app.logger.info('Outreach email skipped (mail not configured): %s', contact.email)
        return False

    business = current_app.config.get('BUSINESS_NAME', 'Marshall Auto')
    message = EmailMessage()
    message['Subject'] = f"New cars at {business}"
    message['From'] = sender
    message['To'] = contact.email
    message['List-Unsubscribe'] = f'<{_unsubscribe_url(contact)}>'
    message.set_content(_email_body(contact, vehicles))
    try:
        port = current_app.config.get('MAIL_PORT', 587)
        use_tls = current_app.config.get('MAIL_USE_TLS', True)
        use_ssl = current_app.config.get('MAIL_USE_SSL', False)
        username = current_app.config.get('MAIL_USERNAME') or None
        password = current_app.config.get('MAIL_PASSWORD') or None
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(server, port, timeout=10) as smtp:
            if not use_ssl and use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
    except Exception as exc:
        current_app.logger.error('Outreach email failed for %s: %s', contact.email, exc)
        return False
    return True


def _send_sms(contact, vehicles):
    from app.financing import _send_textbelt_message

    api_key = current_app.config.get('TEXTBELT_API_KEY')
    phone = contact.phone_e164
    if not phone:
        return False
    if current_app.config.get('OUTREACH_DRY_RUN'):
        current_app.logger.info('Outreach SMS dry-run to %s', phone)
        return True
    if not api_key:
        current_app.logger.warning('Outreach SMS skipped: TEXTBELT_API_KEY is not configured.')
        return False
    body = _sms_body(contact, vehicles)
    return _send_textbelt_message(phone, body, api_key)


def process_monthly_outreach(*, force=False):
    """Send due monthly new-car notices. Safe to run daily; enforces min spacing."""
    if not current_app.config.get('OUTREACH_ENABLED', True) and not force:
        current_app.logger.info('Monthly outreach disabled (OUTREACH_ENABLED=false).')
        return {'sms': 0, 'email': 0, 'contacts': 0, 'skipped': 'disabled'}

    vehicles = _inventory_highlights(limit=int(current_app.config.get('OUTREACH_VEHICLE_LIMIT', 5)))
    if not vehicles and not force:
        current_app.logger.info('Monthly outreach skipped: no available inventory.')
        return {'sms': 0, 'email': 0, 'contacts': 0, 'skipped': 'no_inventory'}

    min_days = 0 if force else int(current_app.config.get('OUTREACH_MIN_DAYS', 28))
    send_sms = current_app.config.get('OUTREACH_SEND_SMS', True)
    send_email = current_app.config.get('OUTREACH_SEND_EMAIL', True)
    contacts = _due_contacts(min_days)
    sms_sent = 0
    email_sent = 0
    touched = 0
    now = utcnow()

    for contact in contacts:
        did_sms = False
        did_email = False
        if send_sms and contact.sms_opt_in and contact.phone_e164:
            if _send_sms(contact, vehicles):
                contact.last_sms_at = now
                did_sms = True
                sms_sent += 1
        if send_email and contact.email_opt_in and contact.email:
            if _send_email(contact, vehicles):
                contact.last_email_at = now
                did_email = True
                email_sent += 1
        if did_sms or did_email:
            contact.last_outreach_at = now
            contact.updated_at = now
            touched += 1

    db.session.commit()
    return {'sms': sms_sent, 'email': email_sent, 'contacts': touched, 'skipped': None}


def register_commands(app):
    @app.cli.command('sync-outreach-contacts')
    def sync_outreach_contacts_cmd():
        """Normalize lead phones/emails into the outreach_contacts table."""
        stats = sync_outreach_contacts_from_leads()
        print(
            f"Outreach contacts synced. created={stats['created']} "
            f"updated={stats['updated']} total={stats['total']}"
        )

    @app.cli.command('send-monthly-outreach')
    def send_monthly_outreach_cmd():
        """Send due monthly new-car texts/emails (honors OUTREACH_MIN_DAYS)."""
        stats = process_monthly_outreach()
        print(
            f"Monthly outreach finished. contacts={stats['contacts']} "
            f"sms={stats['sms']} email={stats['email']} skipped={stats['skipped']}"
        )
