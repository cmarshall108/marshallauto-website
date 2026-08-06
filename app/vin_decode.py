"""NHTSA vPIC VIN decode helpers for admin vehicle form prefills.

Uses the free public DecodeVinValues API (no API key). Results are mapped to
form fields and a default feature list derived from equipment flags.
"""

from __future__ import annotations

import json
import re
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.vehicle_catalog import COMMON_FEATURES


def _ssl_context() -> ssl.SSLContext | None:
    """Prefer certifi CA bundle (fixes macOS Python SSL verify failures)."""
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None

NHTSA_DECODE_URL = (
    'https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json'
)

# VIN characters exclude I, O, Q.
_VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')

# Short-lived process cache to avoid hammering NHTSA during form edits.
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 600
_CACHE_MAX = 64

# NHTSA equipment field -> catalog-friendly feature label.
# Only applied when the decoded value indicates the equipment is present.
_EQUIPMENT_FEATURE_MAP: dict[str, str] = {
    'ABS': 'ABS',
    'AdaptiveCruiseControl': 'Adaptive Cruise Control',
    'BlindSpotMon': 'Blind Spot Monitor',
    'BlindSpotIntervention': 'Blind Spot Monitor',
    'CIB': 'Automatic Emergency Braking',
    'ForwardCollisionWarning': 'Forward Collision Warning',
    'KeylessIgnition': 'Push Button Start',
    'LaneCenteringAssistance': 'Lane Keep Assist',
    'LaneDepartureWarning': 'Lane Keep Assist',
    'LaneKeepSystem': 'Lane Keep Assist',
    'ParkAssist': 'Parking Sensors',
    'PedestrianAutomaticEmergencyBraking': 'Automatic Emergency Braking',
    'RearAutomaticEmergencyBraking': 'Automatic Emergency Braking',
    'RearCrossTrafficAlert': 'Blind Spot Monitor',
    'RearVisibilitySystem': 'Backup Camera',
    'TPMS': 'TPMS',
    'TractionControl': 'Traction Control',
    'ESC': 'Electronic Stability Control',
    'DaytimeRunningLight': 'LED Headlights',
    'AdaptiveDrivingBeam': 'LED Headlights',
}

# Values that mean the equipment is on the vehicle (standard or optional).
_PRESENT_VALUES = {
    'standard',
    'optional',
    'yes',
    'y',
    'true',
    '1',
    'equipped',
    'available',
}

_ABSENT_VALUES = {
    '',
    'not available',
    'not applicable',
    'n/a',
    'na',
    'no',
    'n',
    'false',
    '0',
    'none',
    'unknown',
}


def normalize_vin(raw: str | None) -> str:
    """Uppercase, strip spaces/hyphens, keep alphanumerics only."""
    if not raw:
        return ''
    cleaned = re.sub(r'[^A-Za-z0-9]', '', str(raw)).upper()
    return cleaned


def is_valid_vin(vin: str | None) -> bool:
    """Basic VIN shape check (length + allowed charset)."""
    return bool(vin and _VIN_RE.match(vin))


def _cache_get(vin: str) -> dict[str, Any] | None:
    entry = _CACHE.get(vin)
    if not entry:
        return None
    ts, payload = entry
    if time.time() - ts > _CACHE_TTL_SECONDS:
        _CACHE.pop(vin, None)
        return None
    return payload


def _cache_set(vin: str, payload: dict[str, Any]) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # Drop oldest
        oldest = min(_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _CACHE.pop(oldest, None)
    _CACHE[vin] = (time.time(), payload)


def _nhtsa_get(field: dict[str, Any] | None, *keys: str) -> str:
    if not field:
        return ''
    for key in keys:
        val = field.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            return text
    return ''


def _is_present(value: str | None) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text or text in _ABSENT_VALUES:
        return False
    if text in _PRESENT_VALUES:
        return True
    # Some fields return descriptive text rather than Standard/Optional.
    if any(token in text for token in ('standard', 'optional', 'yes', 'equipped')):
        return True
    # Numeric / descriptive non-empty values (e.g. cylinder counts) are not
    # treated as equipment presence here.
    return False


def _title_case_make(make: str) -> str:
    if not make:
        return ''
    # Keep common brand casing.
    special = {
        'bmw': 'BMW',
        'gmc': 'GMC',
        'mini': 'Mini',
        'ram': 'Ram',
        'mercedes-benz': 'Mercedes-Benz',
        'mercedes benz': 'Mercedes-Benz',
        'land rover': 'Land Rover',
        'alfa romeo': 'Alfa Romeo',
        'rolls-royce': 'Rolls-Royce',
        'aston martin': 'Aston Martin',
    }
    key = make.strip().lower()
    if key in special:
        return special[key]
    return make.strip().title()


def map_drivetrain(raw: str | None) -> str:
    """Normalize NHTSA DriveType into FWD/RWD/AWD/4WD or empty."""
    text = (raw or '').strip().lower()
    if not text:
        return ''
    if 'all' in text and 'wheel' in text:
        return 'AWD'
    if text in {'awd', 'all-wheel drive', 'all wheel drive'}:
        return 'AWD'
    if '4' in text and 'wheel' in text:
        return '4WD'
    if text in {'4wd', '4x4', 'four-wheel drive', 'four wheel drive'}:
        return '4WD'
    if 'front' in text:
        return 'FWD'
    if text in {'fwd', 'front-wheel drive', 'front wheel drive', '2wd/fwd'}:
        return 'FWD'
    if 'rear' in text:
        return 'RWD'
    if text in {'rwd', 'rear-wheel drive', 'rear wheel drive', '2wd/rwd'}:
        return 'RWD'
    # Generic 2WD without front/rear — leave blank rather than guess wrong.
    return ''


def map_body_style(raw: str | None) -> str:
    """Simplify NHTSA BodyClass into common inventory labels."""
    text = (raw or '').strip()
    if not text:
        return ''
    lower = text.lower()
    mapping = [
        (('sport utility', 'suv', 'multipurpose'), 'SUV'),
        (('pickup',), 'Truck'),
        (('cargo van', 'passenger van', 'van'), 'Van'),
        (('minivan',), 'Minivan'),
        (('coupe',), 'Coupe'),
        (('convertible', 'cabriolet'), 'Convertible'),
        (('hatchback',), 'Hatchback'),
        (('wagon',), 'Wagon'),
        (('sedan', 'saloon'), 'Sedan'),
        (('motorcycle',), 'Motorcycle'),
        (('trailer',), 'Trailer'),
        (('bus',), 'Bus'),
    ]
    for keys, label in mapping:
        if any(k in lower for k in keys):
            return label
    # Fall back to original short class
    return text if len(text) <= 64 else text[:64]


def map_fuel_type(raw: str | None) -> str:
    text = (raw or '').strip()
    if not text:
        return ''
    lower = text.lower()
    if 'electric' in lower and 'hybrid' not in lower and 'plug' not in lower:
        return 'Electric'
    if 'plug-in' in lower or 'plugin' in lower or 'phev' in lower:
        return 'Plug-in Hybrid'
    if 'hybrid' in lower:
        return 'Hybrid'
    if 'diesel' in lower:
        return 'Diesel'
    if 'flex' in lower or 'e85' in lower:
        return 'Flex Fuel'
    if 'gasoline' in lower or 'petrol' in lower or lower == 'gas':
        return 'Gasoline'
    if 'hydrogen' in lower or 'fuel cell' in lower:
        return 'Hydrogen'
    return text.title() if len(text) <= 32 else text[:32]


def build_engine_description(result: dict[str, Any]) -> str:
    """Compose a short engine string like '2.5L I4' or 'Electric'."""
    fuel = map_fuel_type(_nhtsa_get(result, 'FuelTypePrimary'))
    if fuel == 'Electric':
        # Prefer battery / motor info when present
        motor = _nhtsa_get(result, 'ElectrificationLevel', 'EngineModel')
        return motor or 'Electric'

    displacement = _nhtsa_get(result, 'DisplacementL')
    cylinders = _nhtsa_get(result, 'EngineCylinders')
    config = _nhtsa_get(result, 'EngineConfiguration')
    model = _nhtsa_get(result, 'EngineModel')

    parts: list[str] = []
    if displacement:
        try:
            disp = float(displacement)
            # NHTSA often returns high-precision liters (e.g. 2.99883); round nicely.
            rounded = round(disp, 1)
            if abs(rounded - round(rounded)) < 1e-9:
                disp_s = f'{rounded:.1f}L'  # 3.0L
            else:
                disp_s = f'{rounded:g}L'  # 2.5L
        except ValueError:
            disp_s = displacement if displacement.upper().endswith('L') else f'{displacement}L'
        parts.append(disp_s)

    cyl_label = ''
    if cylinders:
        try:
            n = int(float(cylinders))
        except ValueError:
            n = None
        cfg = (config or '').lower()
        if n:
            if 'v' in cfg and 'inline' not in cfg and 'in-line' not in cfg:
                cyl_label = f'V{n}'
            elif 'flat' in cfg or 'boxer' in cfg or 'horiz' in cfg:
                cyl_label = f'H{n}'
            elif 'inline' in cfg or 'in-line' in cfg or 'straight' in cfg:
                cyl_label = f'I{n}'
            else:
                # Guess: 8+ usually V; 3-6 default inline unless known V from model
                cyl_label = f'V{n}' if n >= 8 else f'I{n}'
    if cyl_label:
        parts.append(cyl_label)
    elif config:
        parts.append(config)

    # Only fall back to engine model code when we have nothing else useful.
    if not parts and model:
        return model[:128]

    return ' '.join(parts)[:128]


def map_transmission(style: str | None, speeds: str | None = None) -> str:
    text = (style or '').strip()
    speed_n = ''
    if speeds:
        try:
            speed_n = str(int(float(str(speeds).strip())))
        except ValueError:
            m = re.search(r'(\d+)', str(speeds))
            speed_n = m.group(1) if m else ''

    if not text and speed_n:
        return f'{speed_n}-Speed'
    if not text:
        return ''

    lower = text.lower()
    if 'cvt' in lower:
        return 'CVT'
    if 'dual' in lower or 'dct' in lower or 'dsg' in lower:
        return 'Dual-Clutch'
    # Bare numeric style is just gear count
    if re.fullmatch(r'\d+(\.0+)?', text) and not speed_n:
        return f'{int(float(text))}-Speed'

    m = re.search(r'(\d+)\s*[- ]?\s*speed', lower)
    gears = m.group(1) if m else speed_n

    if 'manual' in lower:
        return f'{gears}-Speed Manual' if gears else 'Manual'
    if 'auto' in lower:
        return f'{gears}-Speed Automatic' if gears else 'Automatic'
    if gears and text.lower() in {'automatic', 'manual', 'cvt'}:
        kind = text.title() if text.lower() != 'cvt' else 'CVT'
        return f'{gears}-Speed {kind}' if kind != 'CVT' else 'CVT'
    return text[:128]


def derive_features(result: dict[str, Any]) -> list[str]:
    """Build a default feature list from NHTSA equipment + body/fuel cues."""
    features: list[str] = []
    seen: set[str] = set()

    def add(label: str | None) -> None:
        text = (label or '').strip()
        if not text:
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        features.append(text)

    for field, label in _EQUIPMENT_FEATURE_MAP.items():
        if _is_present(_nhtsa_get(result, field)):
            add(label)

    # Seating cues from NHTSA counts only (no guessy package adds).
    seats = _nhtsa_get(result, 'Seats')
    seat_rows = _nhtsa_get(result, 'SeatRows')
    try:
        seat_n = int(float(seats)) if seats else 0
    except ValueError:
        seat_n = 0
    try:
        row_n = int(float(seat_rows)) if seat_rows else 0
    except ValueError:
        row_n = 0
    if seat_n >= 7 or row_n >= 3 or 'third' in seat_rows.lower():
        add('Third Row Seating')

    electrification = _nhtsa_get(result, 'ElectrificationLevel').lower()
    if 'plug-in' in electrification or 'phev' in electrification:
        add('Plug-in Hybrid')
    elif 'hev' in electrification or 'hybrid' in electrification:
        add('Hybrid System')

    # Prefer catalog casing when we have an exact case-insensitive match.
    catalog_lookup = {f.lower(): f for f in COMMON_FEATURES}
    normalized: list[str] = []
    seen_out: set[str] = set()
    for feat in features:
        canon = catalog_lookup.get(feat.lower(), feat)
        key = canon.lower()
        if key in seen_out:
            continue
        seen_out.add(key)
        normalized.append(canon)
    return normalized


def map_vehicle_fields(result: dict[str, Any]) -> dict[str, Any]:
    """Map a NHTSA DecodeVinValues result row to form-friendly fields."""
    year_raw = _nhtsa_get(result, 'ModelYear')
    year = None
    if year_raw:
        try:
            year = int(float(year_raw))
        except ValueError:
            year = None

    trim = _nhtsa_get(result, 'Trim', 'Series', 'Trim2', 'Series2')
    make = _title_case_make(_nhtsa_get(result, 'Make'))
    model = _nhtsa_get(result, 'Model')
    # Light title-case for multi-word models when NHTSA returns ALL CAPS
    if model and model.isupper() and len(model) > 3:
        model = model.title()

    return {
        'year': year,
        'make': make,
        'model': model,
        'trim': trim,
        'body_style': map_body_style(_nhtsa_get(result, 'BodyClass')),
        'drivetrain': map_drivetrain(_nhtsa_get(result, 'DriveType')),
        'fuel_type': map_fuel_type(_nhtsa_get(result, 'FuelTypePrimary')),
        'engine': build_engine_description(result),
        'transmission': map_transmission(
            _nhtsa_get(result, 'TransmissionStyle'),
            _nhtsa_get(result, 'TransmissionSpeeds'),
        ),
        'doors': _nhtsa_get(result, 'Doors') or None,
        'plant_city': _nhtsa_get(result, 'PlantCity') or None,
        'plant_country': _nhtsa_get(result, 'PlantCountry') or None,
        'vehicle_type': _nhtsa_get(result, 'VehicleType') or None,
        'gvwr': _nhtsa_get(result, 'GVWR') or None,
    }


def _error_codes(result: dict[str, Any]) -> list[str]:
    raw = _nhtsa_get(result, 'ErrorCode', 'Error Codes')
    if not raw:
        return []
    return [c.strip() for c in re.split(r'[,;]', raw) if c.strip()]


def _error_text(result: dict[str, Any]) -> str:
    return _nhtsa_get(result, 'ErrorText', 'AdditionalErrorText')


def _parse_nhtsa_body(body: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError('NHTSA returned invalid JSON') from exc

    results = payload.get('Results') or []
    if not results:
        raise RuntimeError('NHTSA returned no results')
    return results[0]


def _fetch_nhtsa_curl(url: str, timeout: float = 8.0) -> dict[str, Any]:
    """Fallback fetch via system curl (uses OS trust store)."""
    try:
        completed = subprocess.run(
            [
                'curl', '-fsSL',
                '--max-time', str(max(1, int(timeout))),
                '-H', 'Accept: application/json',
                '-H', 'User-Agent: MarshallAutoWebsite/1.0 (admin-vin-decode)',
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError('NHTSA network error: curl not available') from exc
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or 'curl failed').strip()
        raise RuntimeError(f'NHTSA network error: {err[:200]}')
    return _parse_nhtsa_body(completed.stdout)


def fetch_nhtsa_decode(vin: str, timeout: float = 8.0) -> dict[str, Any]:
    """Call NHTSA DecodeVinValues and return the first result row."""
    url = NHTSA_DECODE_URL.format(vin=urllib.parse.quote(vin, safe=''))
    req = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'MarshallAutoWebsite/1.0 (admin-vin-decode)',
        },
        method='GET',
    )
    ctx = _ssl_context()
    try:
        open_kwargs = {'timeout': timeout}
        if ctx is not None:
            open_kwargs['context'] = ctx
        with urllib.request.urlopen(req, **open_kwargs) as resp:
            body = resp.read().decode('utf-8', errors='replace')
        return _parse_nhtsa_body(body)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'NHTSA HTTP {exc.code}') from exc
    except TimeoutError as exc:
        raise RuntimeError('NHTSA request timed out') from exc
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, 'reason', exc))
        if 'CERTIFICATE' in reason.upper() or 'SSL' in reason.upper():
            try:
                return _fetch_nhtsa_curl(url, timeout=timeout)
            except RuntimeError:
                pass
        raise RuntimeError(f'NHTSA network error: {exc.reason}') from exc
    except ssl.SSLError as exc:
        try:
            return _fetch_nhtsa_curl(url, timeout=timeout)
        except RuntimeError:
            raise RuntimeError(f'NHTSA network error: {exc}') from exc


def decode_vin(raw_vin: str | None, *, use_cache: bool = True) -> dict[str, Any]:
    """
    Decode a VIN into form prefills.

    Returns:
        {
          ok: bool,
          vin: str,
          error: str|None,
          vehicle: {year, make, model, ...},
          features: [str, ...],
          warnings: [str, ...],
          source: 'nhtsa',
        }
    """
    vin = normalize_vin(raw_vin)
    if not vin:
        return {
            'ok': False,
            'vin': '',
            'error': 'Enter a VIN to decode.',
            'vehicle': {},
            'features': [],
            'warnings': [],
            'source': 'nhtsa',
        }
    if len(vin) != 17:
        return {
            'ok': False,
            'vin': vin,
            'error': 'VIN must be exactly 17 characters.',
            'vehicle': {},
            'features': [],
            'warnings': [],
            'source': 'nhtsa',
        }
    if not is_valid_vin(vin):
        return {
            'ok': False,
            'vin': vin,
            'error': 'VIN contains invalid characters (I, O, Q are not allowed).',
            'vehicle': {},
            'features': [],
            'warnings': [],
            'source': 'nhtsa',
        }

    if use_cache:
        cached = _cache_get(vin)
        if cached is not None:
            return dict(cached)

    try:
        result = fetch_nhtsa_decode(vin)
    except RuntimeError as exc:
        return {
            'ok': False,
            'vin': vin,
            'error': str(exc),
            'vehicle': {},
            'features': [],
            'warnings': [],
            'source': 'nhtsa',
        }

    codes = _error_codes(result)
    err_text = _error_text(result)
    # NHTSA uses 0 for success; other codes can still include partial data.
    hard_fail = codes and all(c != '0' for c in codes) and not _nhtsa_get(result, 'Make')
    if hard_fail:
        payload = {
            'ok': False,
            'vin': vin,
            'error': err_text or 'VIN could not be decoded.',
            'vehicle': {},
            'features': [],
            'warnings': codes,
            'source': 'nhtsa',
        }
        _cache_set(vin, payload)
        return dict(payload)

    vehicle = map_vehicle_fields(result)
    features = derive_features(result)
    warnings: list[str] = []
    if codes and codes != ['0']:
        if err_text:
            warnings.append(err_text)
        else:
            warnings.append('Decoded with NHTSA warnings: ' + ', '.join(codes))

    if not vehicle.get('make') and not vehicle.get('model'):
        payload = {
            'ok': False,
            'vin': vin,
            'error': err_text or 'No vehicle data returned for this VIN.',
            'vehicle': vehicle,
            'features': features,
            'warnings': warnings,
            'source': 'nhtsa',
        }
        _cache_set(vin, payload)
        return dict(payload)

    payload = {
        'ok': True,
        'vin': vin,
        'error': None,
        'vehicle': vehicle,
        'features': features,
        'warnings': warnings,
        'source': 'nhtsa',
    }
    _cache_set(vin, payload)
    return dict(payload)
