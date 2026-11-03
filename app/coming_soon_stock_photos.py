import base64
import binascii
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


def stock_photo_filename(year, make, model, body_style=None):
    """Return a shared asset path for a make/model/body-style reference image."""
    if not str(make or '').strip() or not str(model or '').strip():
        raise ValueError('A make and model are required to name a stock photo.')
    body = str(body_style or '').strip() or 'vehicle'
    name = f'{str(make).strip()}-{str(model).strip()}-{body}'
    return f'images/coming-soon-stock/{slugify(name)}.webp'


def existing_stock_photo_filename(vehicle):
    filename = stock_photo_filename(
        vehicle.year, vehicle.make, vehicle.model, vehicle.body_style,
    )
    if (Path(current_app.static_folder) / filename).is_file():
        return filename
    return None


def ensure_stock_photo_for_vehicle(vehicle):
    """Generate a shared reference image only when a teaser has no real photo."""
    if (
        vehicle.status != 'available'
        or not vehicle.is_coming_soon
        or vehicle.primary_image()
    ):
        return None
    filename = stock_photo_filename(
        vehicle.year, vehicle.make, vehicle.model, vehicle.body_style,
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
        vehicle.body_style,
    )


def _image_prompt(year, make, model, body_style=None):
    body = f' with a {body_style} body style' if body_style else ''
    return (
        f'Create one photorealistic automotive catalog reference image representing a '
        f'{year} {make} {model}{body}. Show a single complete vehicle in a front three-quarter '
        f'view on a clean neutral studio background, soft even lighting, realistic '
        f'proportions, no people, no text, no watermark, no visible logos or badges, '
        f'and no readable license plate. Use a neutral factory-style paint color. '
        f'This is an illustrative reference image, not a depiction of a specific car.'
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


def generate_stock_photo(year, make, model, api_key, static_folder, body_style=None):
    prompt = _image_prompt(year, make, model, body_style)
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
    filename = stock_photo_filename(year, make, model, body_style)
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
    @click.option('--force', is_flag=True, help='Regenerate existing model/body-style images.')
    @with_appcontext
    def generate_coming_soon_stock_photos(force):
        """Generate representative Grok images for Coming Soon vehicles."""
        api_key = current_app.config.get('XAI_API_KEY', '').strip()
        if not api_key:
            raise click.ClickException('Set XAI_API_KEY to generate Coming Soon stock photos.')

        from app.models import Vehicle

        models = {}
        vehicles = (
            Vehicle.query.with_entities(
                Vehicle.year, Vehicle.make, Vehicle.model, Vehicle.body_style,
            )
            .filter_by(status='available', is_coming_soon=True)
            .filter(~Vehicle.images.any())
            .order_by(Vehicle.year, Vehicle.make, Vehicle.model, Vehicle.body_style)
            .distinct()
            .all()
        )
        for year, make, model, body_style in vehicles:
            filename = stock_photo_filename(year, make, model, body_style)
            models.setdefault(filename, (year, make, model, body_style))

        generated = 0
        skipped = 0
        for filename, (year, make, model, body_style) in sorted(models.items()):
            destination = Path(current_app.static_folder) / filename
            if destination.is_file() and not force:
                skipped += 1
                continue
            try:
                generate_stock_photo(
                    year, make, model, api_key, current_app.static_folder, body_style,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise click.ClickException(
                    f'Could not generate a stock photo for {year} {make} {model}: {exc}'
                ) from exc
            click.echo(f'Generated reference photo for {year} {make} {model}.')
            generated += 1

        click.echo(
            f'Generated {generated} image(s); skipped {skipped} existing image(s).'
        )
