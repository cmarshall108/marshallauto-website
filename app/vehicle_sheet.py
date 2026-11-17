"""Printable vehicle details sheet shared by the public listing and admin."""

from __future__ import annotations

import base64
from io import BytesIO

import qrcode
from flask import current_app, url_for
from qrcode.image.svg import SvgPathImage

from app.utils import dealership_now
from app.vin_decode import (
    group_vin_decode_data,
    present_nhtsa_safety_ratings,
    safety_ratings_from_vehicle,
)


def _clean_print_lines(raw) -> list[str]:
    items = []
    for item in (raw or '').splitlines():
        text = item.strip().replace('Photo-observed (verify): ', '')
        if text:
            items.append(text)
    return items


def feature_groups_for_print(vehicle) -> list[dict]:
    groups = []
    if vehicle.feature_list:
        groups.append({'label': 'Key Features', 'items': list(vehicle.feature_list)})
    raw_groups = (
        ('Exterior', vehicle.exterior_features),
        ('Interior', vehicle.interior_features),
        ('Entertainment', '\n'.join(vehicle.entertainment_feature_list or [])),
        ('Mechanical', vehicle.mechanical_features),
        ('Safety', vehicle.safety_features),
    )
    for label, raw in raw_groups:
        items = _clean_print_lines(raw)
        if items:
            groups.append({'label': label, 'items': items})
    return groups


def extra_specs_for_print(raw) -> list[dict]:
    rows = []
    for line in (raw or '').splitlines():
        text = line.strip()
        if not text:
            continue
        if ':' in text:
            label, value = text.split(':', 1)
            rows.append({'label': label.strip(), 'value': value.strip()})
        else:
            rows.append({'label': '', 'value': text})
    return rows


def vehicle_details_print_context(vehicle, *, back_url: str) -> dict:
    site = (current_app.config.get('SITE_URL') or '').rstrip('/')
    coming_soon = bool(vehicle.is_coming_soon and vehicle.status == 'available')
    if vehicle.slug:
        qr_target = site + url_for('main.vehicle_qr_scan', slug=vehicle.slug)
        if coming_soon:
            display_url = site + url_for('main.coming_soon')
        else:
            display_url = site + url_for('main.vehicle_detail', slug=vehicle.slug)
    else:
        qr_target = ''
        display_url = site

    qr_code = ''
    if qr_target:
        image = qrcode.make(qr_target, image_factory=SvgPathImage)
        output = BytesIO()
        image.save(output)
        qr_code = base64.b64encode(output.getvalue()).decode('ascii')

    return {
        'vehicle': vehicle,
        'back_url': back_url,
        'qr_code': qr_code,
        'display_url': display_url,
        'feature_groups': feature_groups_for_print(vehicle),
        'extra_specs': extra_specs_for_print(vehicle.additional_specifications),
        'vin_decode_groups': group_vin_decode_data(vehicle.vin_decode_data),
        'safety_ratings': present_nhtsa_safety_ratings(safety_ratings_from_vehicle(vehicle)),
        'price_drop': vehicle.price_drop_info,
        'savings': vehicle.savings_info(),
        'coming_soon_unpublished': coming_soon,
        'dealer_name': current_app.config.get('BUSINESS_NAME') or 'Marshall Auto',
        'dealer_address': (
            f"{current_app.config.get('BUSINESS_ADDRESS', '')}, "
            f"{current_app.config.get('BUSINESS_CITY', '')}, "
            f"{current_app.config.get('BUSINESS_STATE', '')} "
            f"{current_app.config.get('BUSINESS_ZIP', '')}"
        ).strip(' ,'),
        'dealer_phone': current_app.config.get('BUSINESS_PHONE') or '',
        'dealer_email': current_app.config.get('BUSINESS_EMAIL') or '',
        'printed_at': dealership_now(),
    }
