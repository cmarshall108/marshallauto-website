import json
import time

import click
from flask import current_app

from app import db
from app.models import Vehicle
from app.vin_decode import (
    collect_grok_photo_annotations, decode_vin,
    enrich_vin_decode_data_with_grok, is_vin_grok_enrichment_enabled,
    grok_photo_annotation_signature, merge_grok_photo_annotations, normalize_vin,
)


_VIN_FIELDS = (
    'year',
    'make',
    'model',
    'trim',
    'body_style',
    'drivetrain',
    'fuel_type',
    'engine',
    'transmission',
    'exterior_color',
    'interior_color',
    'mpg_city',
    'mpg_highway',
    'mpg_combined',
    'doors',
    'passenger_capacity',
    'gvwr',
)

_FEATURE_FIELDS = (
    'exterior_features',
    'interior_features',
    'entertainment_features',
    'mechanical_features',
    'safety_features',
    'additional_specifications',
)


def _merge_lines(existing: str | None, incoming: str | None) -> str | None:
    lines = [line.strip() for line in (existing or '').splitlines() if line.strip()]
    seen = {line.casefold() for line in lines}
    for line in (incoming or '').splitlines():
        value = line.strip()
        if value and value.casefold() not in seen:
            lines.append(value)
            seen.add(value.casefold())
    return '\n'.join(lines) or None


def _replace_photo_observation_lines(
    existing: str | None,
    incoming: list[str],
) -> str | None:
    retained = [
        line.strip()
        for line in (existing or '').splitlines()
        if line.strip()
        and not line.strip().casefold().startswith('photo-observed (verify):')
    ]
    return _merge_lines('\n'.join(retained), '\n'.join(incoming))


def _merge_features(existing: str | None, incoming: list[str]) -> str | None:
    features = [feature.strip() for feature in (existing or '').split(',') if feature.strip()]
    seen = {feature.casefold() for feature in features}
    for feature in incoming:
        value = str(feature).strip()
        if value and value.casefold() not in seen:
            features.append(value)
            seen.add(value.casefold())
    return ', '.join(features) or None


def _apply_vin_decode(vehicle: Vehicle, result: dict) -> bool:
    if not result.get('ok') or normalize_vin(vehicle.vin) != result.get('vin'):
        return False

    decoded = result.get('vehicle') or {}
    raw_data = decoded.get('vin_decode_data')
    nhtsa_data = raw_data.get('NHTSA') if isinstance(raw_data, dict) else None
    if not isinstance(nhtsa_data, dict) or normalize_vin(
        nhtsa_data.get('VIN'),
    ) != normalize_vin(vehicle.vin):
        return False

    for field in _VIN_FIELDS:
        value = decoded.get(field)
        if getattr(vehicle, field) in (None, '') and value not in (None, ''):
            setattr(vehicle, field, value)

    vehicle.features = _merge_features(vehicle.features, result.get('features') or [])
    for field in _FEATURE_FIELDS:
        setattr(vehicle, field, _merge_lines(
            getattr(vehicle, field),
            decoded.get(field),
        ))

    vehicle.vin_decode_data = json.dumps(raw_data, separators=(',', ':'))
    return True


def _stored_vin_decode_data(vehicle: Vehicle) -> dict | None:
    try:
        raw_data = json.loads(vehicle.vin_decode_data or '')
    except (json.JSONDecodeError, TypeError):
        return None
    nhtsa_data = raw_data.get('NHTSA') if isinstance(raw_data, dict) else None
    if not isinstance(nhtsa_data, dict) or normalize_vin(
        nhtsa_data.get('VIN'),
    ) != normalize_vin(vehicle.vin):
        return None
    return raw_data


def register_commands(app):
    @app.cli.command('backfill-vin-data')
    @click.option(
        '--force',
        is_flag=True,
        help='Re-decode vehicles that already have saved VIN data.',
    )
    @click.option('--limit', type=click.IntRange(min=1), help='Process at most this many vehicles.')
    @click.option(
        '--delay',
        type=click.FloatRange(min=0),
        default=0.25,
        show_default=True,
        help='Seconds to wait between VIN decoder requests.',
    )
    @click.option(
        '--retries',
        type=click.IntRange(min=1, max=5),
        default=3,
        show_default=True,
        help='Attempts per vehicle when the decoder fails or returns an unusable result.',
    )
    def backfill_vin_data(force, limit, delay, retries):
        """Fill missing VIN-derived information for existing inventory."""
        query = Vehicle.query.filter(Vehicle.vin.isnot(None), Vehicle.vin != '')
        query = query.order_by(Vehicle.id)
        inventory = query.all()
        grok_enabled = is_vin_grok_enrichment_enabled()
        vehicles = []
        for vehicle in inventory:
            raw_data = _stored_vin_decode_data(vehicle)
            grok_data = raw_data.get('Grok') if raw_data else None
            annotations = (
                grok_data.get('photo_annotations')
                if isinstance(grok_data, dict) else None
            )
            missing_enrichment = (
                raw_data is None
                or (
                    grok_enabled
                    and (
                        not isinstance(grok_data, dict)
                        or 'estimates' not in grok_data
                    )
                )
                or not isinstance(annotations, dict)
                or annotations.get('version') != 1
                or annotations.get('image_signature')
                != grok_photo_annotation_signature(vehicle.images)
            )
            if force or missing_enrichment:
                vehicles.append(vehicle)
        if limit:
            vehicles = vehicles[:limit]

        click.echo(
            f'VIN backfill: {len(vehicles)} vehicle(s) queued.'
            + (' Grok estimates enabled.' if is_vin_grok_enrichment_enabled() else '')
        )
        updated = 0
        failed = 0
        for index, vehicle in enumerate(vehicles):
            for attempt in range(1, retries + 1):
                try:
                    raw_data = None if force else _stored_vin_decode_data(vehicle)
                    if raw_data is None:
                        result = decode_vin(vehicle.vin, use_cache=False)
                        if not _apply_vin_decode(vehicle, result):
                            reason = result.get('error') or (
                                'decoder response did not include full data for this VIN'
                            )
                            raise RuntimeError(reason)
                        raw_data = _stored_vin_decode_data(vehicle)
                        if raw_data is None:
                            raise RuntimeError(
                                'saved decoder response did not match this vehicle VIN'
                            )

                    grok_data = raw_data.get('Grok')
                    if not isinstance(grok_data, dict):
                        grok_data = {}
                    raw_data['Grok'] = grok_data
                    if grok_enabled and 'estimates' not in grok_data:
                        try:
                            estimates = enrich_vin_decode_data_with_grok(raw_data)
                        except RuntimeError as exc:
                            current_app.logger.warning(
                                'Grok VIN estimates unavailable for vehicle %s: %s',
                                vehicle.id,
                                exc,
                            )
                            grok_data['estimates'] = {}
                        else:
                            if estimates is not None:
                                grok_data.update(estimates)

                    annotations = (
                        grok_data.get('photo_annotations')
                        if isinstance(grok_data.get('photo_annotations'), dict)
                        and grok_data['photo_annotations'].get('version') == 1
                        else collect_grok_photo_annotations(vehicle.id)
                    )
                    if annotations is not None:
                        detail_lines = merge_grok_photo_annotations(raw_data, annotations)
                        grok_data = raw_data['Grok']
                        for field, lines in detail_lines.items():
                            setattr(
                                vehicle,
                                field,
                                _replace_photo_observation_lines(
                                    getattr(vehicle, field),
                                    lines,
                                ),
                            )
                    elif grok_data:
                        raw_data['Grok'] = grok_data

                    vehicle.vin_decode_data = json.dumps(
                        raw_data,
                        separators=(',', ':'),
                    )
                    db.session.commit()
                    updated += 1
                    estimates_added = len(grok_data.get('estimates') or {})
                    observations_added = sum(
                        len(annotations.get(category, []))
                        for category in ('exterior', 'interior', 'entertainment')
                    ) if annotations else 0
                    click.echo(
                        f'  Vehicle {vehicle.id}: updated; '
                        f'{estimates_added} Grok estimate(s), '
                        f'{observations_added} photo observation(s).'
                    )
                    break
                except Exception as exc:
                    db.session.rollback()
                    current_app.logger.exception(
                        'VIN data backfill attempt %s/%s failed for vehicle %s',
                        attempt,
                        retries,
                        vehicle.id,
                    )
                    reason = str(exc)

                if attempt == retries:
                    failed += 1
                    click.echo(
                        f'  Vehicle {vehicle.id}: failed after {retries} attempts — {reason}',
                        err=True,
                    )
                else:
                    click.echo(
                        f'  Vehicle {vehicle.id}: attempt {attempt}/{retries} failed — '
                        f'{reason}; retrying.',
                        err=True,
                    )
                    if delay:
                        time.sleep(delay)
            if delay and index + 1 < len(vehicles):
                time.sleep(delay)

        click.echo(f'VIN backfill complete: {updated} updated, {failed} failed.')
        if failed:
            raise click.ClickException(
                f'{failed} vehicle(s) could not be updated; review errors and rerun the command.'
            )
