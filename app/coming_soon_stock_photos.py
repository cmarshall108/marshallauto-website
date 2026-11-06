import base64
import binascii
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

import click
from flask import current_app
from flask.cli import with_appcontext
from PIL import Image, ImageOps

from app.utils import slugify

_IMAGE_GENERATION_URL = 'https://api.x.ai/v1/images/generations'
_IMAGE_MODEL = 'grok-imagine-image-2.0'
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_APPEARANCE_FIELDS = (
    'trim', 'body_style', 'exterior_color', 'interior_color', 'doors',
    'drivetrain', 'fuel_type', 'engine', 'transmission', 'exterior_features',
    'features',
)
_PROMPT_LABELS = (
    ('trim', 'Trim'),
    ('body_style', 'Body style'),
    ('exterior_color', 'Exterior color'),
    ('interior_color', 'Interior color'),
    ('doors', 'Doors'),
    ('drivetrain', 'Drivetrain'),
    ('fuel_type', 'Fuel type'),
    ('engine', 'Engine'),
    ('transmission', 'Transmission'),
    ('exterior_features', 'Exterior equipment'),
    ('features', 'Equipment'),
)


def _clean_detail(value):
    return ' '.join(str(value or '').replace('\n', ', ').split())


def appearance_details(vehicle):
    """Visible configuration used to depict one Coming Soon vehicle."""
    return {field: getattr(vehicle, field, None) for field in _APPEARANCE_FIELDS}


def _filled_details(body_style=None, **details):
    filled = {}
    if body_style and not _clean_detail(details.get('body_style')):
        details['body_style'] = body_style
    for field in _APPEARANCE_FIELDS:
        token = _clean_detail(details.get(field))
        if token:
            filled[field] = token
    if filled.get('exterior_features'):
        filled.pop('features', None)
    return filled


def stock_photo_filename(year, make, model, body_style=None, **details):
    """Return an asset path unique to this vehicle's listed configuration."""
    if not str(make or '').strip() or not str(model or '').strip():
        raise ValueError('A make and model are required to name a stock photo.')
    filled = _filled_details(body_style, **details)
    signature = '|'.join([
        _clean_detail(year),
        _clean_detail(make).casefold(),
        _clean_detail(model).casefold(),
        *(
            f'{field}:{filled[field].casefold()}'
            for field in _APPEARANCE_FIELDS
            if field in filled
        ),
    ])
    digest = hashlib.sha256(signature.encode('utf-8')).hexdigest()[:12]
    label = slugify(
        f'{year}-{make}-{model}-{filled.get("body_style") or "vehicle"}',
    )
    return f'images/coming-soon-stock/{label}-{digest}.webp'


def existing_stock_photo_filename(vehicle):
    filename = stock_photo_filename(
        vehicle.year, vehicle.make, vehicle.model,
        **appearance_details(vehicle),
    )
    if (Path(current_app.static_folder) / filename).is_file():
        return filename
    return None


def ensure_stock_photo_for_vehicle(vehicle):
    """Generate a configuration image only when a teaser has no real photo."""
    if (
        vehicle.status != 'available'
        or not vehicle.is_coming_soon
        or vehicle.primary_image()
    ):
        return None
    filename = stock_photo_filename(
        vehicle.year, vehicle.make, vehicle.model, **appearance_details(vehicle),
    )
    if (Path(current_app.static_folder) / filename).is_file():
        return filename
    api_key = current_app.config.get('XAI_API_KEY', '').strip()
    if not api_key:
        raise RuntimeError('Set XAI_API_KEY to generate Coming Soon stock photos.')
    return generate_stock_photo(
        vehicle.year,
        vehicle.make,
        vehicle.model,
        api_key,
        current_app.static_folder,
        **appearance_details(vehicle),
    )


def _image_prompt(year, make, model, body_style=None, **details):
    filled = _filled_details(body_style, **details)
    body = filled.get('body_style')
    body_phrase = f' with a {body} body style' if body else ''
    listed = [f'Year: {year}', f'Make: {make}', f'Model: {model}']
    for field, label in _PROMPT_LABELS:
        token = filled.get(field)
        if not token:
            continue
        if field in {'exterior_features', 'features'} and len(token) > 400:
            token = token[:400].rsplit(' ', 1)[0]
        listed.append(f'{label}: {token}')
    return (
        f'Create one photorealistic automotive catalog reference image of a '
        f'{year} {make} {model}{body_phrase}. Match every listed detail exactly, '
        f'including trim, exterior color, interior color, doors, drivetrain, fuel '
        f'type, engine, transmission, and visible equipment. Do not substitute a '
        f'generic or neutral paint color when an exterior color is listed. Show one '
        f'complete vehicle in a front three-quarter view on a clean neutral studio '
        f'background, soft even lighting, and realistic proportions. Show the listed '
        f'interior color through the glass. No people, no text, no watermark, include '
        f'visible logos and badges though, and no readable license plate. This is an '
        f'illustrative reference of the listed configuration, not a photo of one '
        f'specific car.\n' + '\n'.join(listed)
    )


def _response_image_bytes(image):
    encoded = image.get('b64_json')
    if encoded:
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError('Grok returned invalid base64 image data.') from exc

    image_url = image.get('url')
    parsed_url = urlsplit(str(image_url or ''))
    if parsed_url.scheme != 'https' or not parsed_url.hostname or parsed_url.username or parsed_url.password:
        raise ValueError('Grok returned an invalid image URL.')
    try:
        with urllib.request.urlopen(image_url, timeout=45) as response:
            image_bytes = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Could not download the generated image (HTTP {exc.code}).') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError('Could not download the generated image.') from exc
    if len(image_bytes) > _MAX_RESPONSE_BYTES:
        raise ValueError('The generated image exceeds the size limit.')
    return image_bytes


def generate_stock_photo(year, make, model, api_key, static_folder, body_style=None, **details):
    prompt = _image_prompt(year, make, model, body_style, **details)
    request_data = json.dumps({
        'model': _IMAGE_MODEL,
        'prompt': prompt,
        'response_format': 'b64_json',
    }).encode('utf-8')
    request = urllib.request.Request(
        _IMAGE_GENERATION_URL,
        data=request_data,
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_data = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode('utf-8', 'replace').strip()
        raise RuntimeError(
            f'Grok image generation failed (HTTP {exc.code})'
            + (f': {detail}' if detail else '.')
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError('Grok image generation could not connect.') from exc

    if len(response_data) > _MAX_RESPONSE_BYTES:
        raise ValueError('Grok returned an image response that exceeds the size limit.')
    try:
        payload = json.loads(response_data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Grok returned an invalid image response.') from exc
    images = payload.get('data') if isinstance(payload, dict) else None
    if not images or not isinstance(images[0], dict):
        raise ValueError('Grok did not return a generated image.')

    image_bytes = _response_image_bytes(images[0])
    filename = stock_photo_filename(year, make, model, body_style, **details)
    destination = Path(static_folder) / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with Image.open(BytesIO(image_bytes)) as source:
            output = ImageOps.fit(
                source.convert('RGB'),
                (1200, 800),
                method=Image.Resampling.LANCZOS,
            )
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                suffix='.webp',
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
            output.save(temporary_path, format='WEBP', quality=84, method=6)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
    return filename


def register_commands(app):
    @app.cli.command('generate-coming-soon-stock-photos')
    @click.option('--force', is_flag=True, help='Regenerate existing configuration images.')
    @with_appcontext
    def generate_coming_soon_stock_photos(force):
        """Generate Grok images matching each Coming Soon vehicle's details."""
        api_key = current_app.config.get('XAI_API_KEY', '').strip()
        if not api_key:
            raise click.ClickException('Set XAI_API_KEY to generate Coming Soon stock photos.')

        from app.models import Vehicle

        configurations = {}
        vehicles = (
            Vehicle.query
            .filter_by(status='available', is_coming_soon=True)
            .filter(~Vehicle.images.any())
            .order_by(Vehicle.year, Vehicle.make, Vehicle.model, Vehicle.id)
            .all()
        )
        for vehicle in vehicles:
            details = appearance_details(vehicle)
            filename = stock_photo_filename(
                vehicle.year, vehicle.make, vehicle.model, **details,
            )
            configurations.setdefault(
                filename, (vehicle.year, vehicle.make, vehicle.model, details),
            )

        generated = 0
        skipped = 0
        for filename, (year, make, model, details) in sorted(configurations.items()):
            destination = Path(current_app.static_folder) / filename
            label = ' '.join(
                str(part) for part in (
                    year, make, model, details.get('trim'), details.get('exterior_color'),
                ) if _clean_detail(part)
            )
            if destination.is_file() and not force:
                skipped += 1
                continue
            try:
                generate_stock_photo(
                    year, make, model, api_key, current_app.static_folder, **details,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise click.ClickException(
                    f'Could not generate a stock photo for {label}: {exc}'
                ) from exc
            click.echo(f'Generated reference photo for {label}.')
            generated += 1

        click.echo(
            f'Generated {generated} image(s); skipped {skipped} existing image(s).'
        )
