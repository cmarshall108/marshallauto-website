"""Extract driver's-license details and screen visible authenticity cues with Grok."""
from __future__ import annotations

import base64
import io
import json
import ssl
import urllib.error
import urllib.request

import certifi
from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError


MAX_LICENSE_IMAGE_BYTES = 15 * 1024 * 1024
AUTHENTICITY_STATUSES = {'appears_authentic', 'suspicious', 'uncertain'}


def _clean_text(value, max_length):
    if value is None:
        return None
    cleaned = ' '.join(str(value).split()).strip()
    return cleaned[:max_length] or None


def _extract_json_object(content):
    text = str(content or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:].lstrip()
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        start = text.find('{')
        end = text.rfind('}')
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except (TypeError, ValueError):
            return None


def normalize_license_analysis(payload):
    if not isinstance(payload, dict):
        raise ValueError('Grok returned an invalid result')

    authenticity = payload.get('authenticity') or {}
    status = str(authenticity.get('status') or 'uncertain').strip().lower()
    if status not in AUTHENTICITY_STATUSES:
        status = 'uncertain'
    try:
        confidence = min(1.0, max(0.0, float(authenticity.get('confidence') or 0)))
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < 0.65:
        status = 'uncertain'

    details = payload.get('details') or {}
    if not isinstance(details, dict):
        details = {}
    reasons = authenticity.get('reasons') or []
    if not isinstance(reasons, list):
        reasons = []

    return {
        'details': {
            'customer_name': _clean_text(details.get('customer_name'), 128),
            'license_number': _clean_text(details.get('license_number'), 64),
            'license_state': _clean_text(details.get('license_state'), 32),
            'date_of_birth': _clean_text(details.get('date_of_birth'), 10),
            'expiration_date': _clean_text(details.get('expiration_date'), 10),
            'address': _clean_text(details.get('address'), 255),
        },
        'authenticity': {
            'status': status,
            'confidence': round(confidence, 2),
            'reasons': [reason for reason in (_clean_text(item, 180) for item in reasons[:5]) if reason],
        },
    }


def _image_data_url(raw_bytes):
    if not raw_bytes or len(raw_bytes) > MAX_LICENSE_IMAGE_BYTES:
        raise ValueError('License image is empty or too large')
    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.verify()
        image = Image.open(io.BytesIO(raw_bytes))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError('Please provide a valid license image') from exc

    image = ImageOps.exif_transpose(image).convert('RGB')
    image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=88, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode('ascii')
    return f'data:image/jpeg;base64,{encoded}'


def analyze_license_image(raw_bytes):
    api_key = current_app.config.get('XAI_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('License analysis is not configured')

    body = {
        'model': current_app.config.get('LICENSE_GROK_MODEL', 'grok-4.5'),
        'temperature': 0,
        'max_tokens': 1000,
        'messages': [
            {
                'role': 'system',
                'content': (
                    "You extract fields from US driver's-license images and screen only visible "
                    'signs of alteration. Return JSON only. Never infer unreadable values. This is ' 
                    'a visual screening aid, not definitive document authentication.'
                ),
            },
            {
                'role': 'user',
                'content': [
                    {'type': 'image_url', 'image_url': {'url': _image_data_url(raw_bytes), 'detail': 'high'}},
                    {
                        'type': 'text',
                        'text': (
                            'Extract customer_name, license_number, license_state (2-letter code when visible), '
                            'date_of_birth, expiration_date (dates as YYYY-MM-DD), and address. Assess visible '
                            'authenticity as appears_authentic, suspicious, or uncertain. Use suspicious only for '
                            'specific visible concerns such as inconsistent fonts, altered fields, impossible layout, '
                            'or obvious digital manipulation. Use uncertain for blur, glare, cropping, unsupported '
                            'jurisdiction, or insufficient evidence. Return exactly: '
                            '{"details":{"customer_name":null,"license_number":null,"license_state":null,'
                            '"date_of_birth":null,"expiration_date":null,"address":null},'
                            '"authenticity":{"status":"uncertain","confidence":0.0,"reasons":[]}}'
                        ),
                    },
                ],
            },
        ],
    }
    request = urllib.request.Request(
        'https://api.x.ai/v1/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'marshallauto-license-analysis/1',
        },
        method='POST',
    )
    timeout = float(current_app.config.get('LICENSE_GROK_TIMEOUT', 45))
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            response_payload = json.loads(response.read().decode('utf-8', errors='replace'))
        content = response_payload['choices'][0]['message']['content']
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'xAI returned HTTP {exc.code}') from exc
    except Exception as exc:
        raise RuntimeError(f'License analysis failed: {exc}') from exc

    parsed = _extract_json_object(content)
    if parsed is None:
        raise RuntimeError('Grok returned an unreadable license analysis')
    return normalize_license_analysis(parsed)