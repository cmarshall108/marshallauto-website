"""NHTSA vPIC + EPA fuel-economy helpers for admin vehicle form prefills.

Uses free public APIs (no keys):
- NHTSA DecodeVinValues for year/make/model/specs/features
- EPA fueleconomy.gov for city/highway MPG

Paint colors are not encoded in a VIN; exterior/interior color keys are
returned empty so the admin form can still merge them if a future source
provides values.
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
import xml.etree.ElementTree as ET
from typing import Any

from app.vehicle_catalog import COMMON_FEATURES, EXTERIOR_COLORS, INTERIOR_COLORS


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
EPA_BASE_URL = 'https://www.fueleconomy.gov/ws/rest/vehicle'

# VIN characters exclude I, O, Q.
_VIN_RE = re.compile(r'^[A-HJ-NPR-Z0-9]{17}$')

# Short-lived process cache to avoid hammering upstream APIs during form edits.
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS = 600
_CACHE_MAX = 64
_EPA_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_EPA_CACHE_TTL_SECONDS = 3600
_EPA_CACHE_MAX = 128

# NHTSA equipment field -> catalog-friendly feature label.
# Only applied when the decoded value indicates the equipment is present.
_EQUIPMENT_FEATURE_MAP: dict[str, str] = {
    # Prefer customer-facing ADAS / convenience items over universal basics (ABS/ESC).
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


def _normalize_color(raw: str | None, catalog: list[str]) -> str:
    """Map a free-text color onto catalog casing when possible."""
    text = (raw or '').strip()
    if not text:
        return ''
    # Drop useless placeholders
    if text.lower() in _ABSENT_VALUES or text.lower() in {'other', 'unknown', 'n/a'}:
        return ''
    lookup = {c.lower(): c for c in catalog}
    if text.lower() in lookup:
        return lookup[text.lower()]
    # Prefer a catalog color contained in the string (e.g. "Pearl White Metallic")
    for color in catalog:
        if color.lower() in text.lower():
            return color
    # Title-case short free text
    if text.isupper() and len(text) > 2:
        return text.title()[:64]
    return text[:64]


def map_colors(result: dict[str, Any]) -> dict[str, str]:
    """Extract exterior/interior colors when present (usually empty on NHTSA)."""
    exterior = _normalize_color(
        _nhtsa_get(
            result,
            'ExteriorColor',
            'Colour',
            'Color',
            'PaintColor',
            'PrimaryColor',
        ),
        EXTERIOR_COLORS,
    )
    interior = _normalize_color(
        _nhtsa_get(
            result,
            'InteriorColor',
            'InteriorColour',
            'CabinColor',
        ),
        INTERIOR_COLORS,
    )
    return {
        'exterior_color': exterior,
        'interior_color': interior,
    }


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

    # Headlamp light source often returns "LED" rather than Standard/Optional.
    lamp = _nhtsa_get(result, 'LowerBeamHeadlampLightSource', 'HeadlampLightSource').lower()
    if 'led' in lamp:
        add('LED Headlights')
    elif 'hid' in lamp or 'xenon' in lamp:
        add('LED Headlights')

    # Alloy wheels: NHTSA wheel size is common; 17"+ is a reasonable retail cue.
    wheel = _nhtsa_get(result, 'WheelSizeFront', 'WheelSizeRear')
    try:
        wheel_n = float(wheel) if wheel else 0
    except ValueError:
        wheel_n = 0
    if wheel_n >= 17:
        add('Alloy Wheels')

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

    fuel = map_fuel_type(_nhtsa_get(result, 'FuelTypePrimary'))
    if fuel == 'Electric':
        add('Electric')

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


def _http_get_bytes(url: str, timeout: float = 8.0, accept: str = '*/*') -> bytes:
    """GET bytes with certifi SSL + curl fallback (macOS-friendly)."""
    req = urllib.request.Request(
        url,
        headers={
            'Accept': accept,
            'User-Agent': 'MarshallAutoWebsite/1.0 (admin-vin-decode)',
        },
        method='GET',
    )
    ctx = _ssl_context()
    open_kwargs: dict[str, Any] = {'timeout': timeout}
    if ctx is not None:
        open_kwargs['context'] = ctx
    try:
        with urllib.request.urlopen(req, **open_kwargs) as resp:
            return resp.read()
    except (urllib.error.URLError, TimeoutError, ssl.SSLError) as exc:
        # curl uses the OS trust store — helpful on some macOS Python builds.
        try:
            completed = subprocess.run(
                [
                    'curl', '-fsSL',
                    '--max-time', str(max(1, int(timeout))),
                    '-H', f'Accept: {accept}',
                    '-H', 'User-Agent: MarshallAutoWebsite/1.0 (admin-vin-decode)',
                    url,
                ],
                check=False,
                capture_output=True,
            )
        except FileNotFoundError as curl_exc:
            raise RuntimeError(f'Network error: {exc}') from curl_exc
        if completed.returncode != 0:
            err = (completed.stderr or completed.stdout or b'curl failed')[:200]
            raise RuntimeError(f'Network error: {err!r}') from exc
        return completed.stdout


def _epa_cache_get(key: str) -> tuple[bool, dict[str, Any] | None]:
    entry = _EPA_CACHE.get(key)
    if not entry:
        return False, None
    ts, payload = entry
    if time.time() - ts > _EPA_CACHE_TTL_SECONDS:
        _EPA_CACHE.pop(key, None)
        return False, None
    return True, payload


def _epa_cache_set(key: str, payload: dict[str, Any] | None) -> None:
    if len(_EPA_CACHE) >= _EPA_CACHE_MAX:
        oldest = min(_EPA_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _EPA_CACHE.pop(oldest, None)
    _EPA_CACHE[key] = (time.time(), payload)


def _epa_menu_items(path: str, params: dict[str, str], timeout: float = 8.0) -> list[tuple[str, str]]:
    query = urllib.parse.urlencode(params)
    url = f'{EPA_BASE_URL}/menu/{path}?{query}'
    body = _http_get_bytes(url, timeout=timeout, accept='application/xml, text/xml, */*')
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise RuntimeError('EPA returned invalid XML') from exc
    items: list[tuple[str, str]] = []
    for mi in root.findall('.//menuItem'):
        text = (mi.findtext('text') or '').strip()
        value = (mi.findtext('value') or '').strip()
        if text or value:
            items.append((text, value))
    return items


def _epa_vehicle(vehicle_id: str, timeout: float = 8.0) -> dict[str, str]:
    url = f'{EPA_BASE_URL}/{urllib.parse.quote(str(vehicle_id), safe="")}'
    body = _http_get_bytes(url, timeout=timeout, accept='application/xml, text/xml, */*')
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise RuntimeError('EPA vehicle XML invalid') from exc

    def text(tag: str) -> str:
        return (root.findtext(tag) or '').strip()

    return {
        'id': vehicle_id,
        'city08': text('city08'),
        'highway08': text('highway08'),
        'comb08': text('comb08'),
        'trany': text('trany'),
        'drive': text('drive'),
        'displ': text('displ'),
        'cylinders': text('cylinders'),
        'fuelType1': text('fuelType1'),
        'model': text('model'),
        'make': text('make'),
        'year': text('year'),
        'atvType': text('atvType'),
    }


def _score_epa_model_name(candidate: str, model: str, trim: str, fuel_type: str) -> int:
    cand = (candidate or '').strip().lower()
    model_l = (model or '').strip().lower()
    trim_l = (trim or '').strip().lower()
    fuel_l = (fuel_type or '').strip().lower()
    if not cand or not model_l:
        return -1
    score = 0
    if cand == model_l:
        score += 100
    elif cand.startswith(model_l + ' ') or cand.startswith(model_l + '-'):
        score += 80
    elif model_l in cand:
        score += 50
    else:
        # Handle F-150 vs F150 style
        compact_c = re.sub(r'[^a-z0-9]', '', cand)
        compact_m = re.sub(r'[^a-z0-9]', '', model_l)
        if compact_m and compact_m in compact_c:
            score += 45
        else:
            return -1

    if trim_l:
        # EPA often groups trims: "Camry LE/SE"
        trim_token = trim_l.split()[0]
        if trim_token and trim_token in cand:
            score += 25
        elif trim_l in cand:
            score += 30

    is_hybrid_cand = 'hybrid' in cand or 'phev' in cand or 'plug' in cand
    is_hybrid_fuel = 'hybrid' in fuel_l or 'plug' in fuel_l or 'electric' in fuel_l
    if is_hybrid_fuel and is_hybrid_cand:
        score += 35
    elif is_hybrid_fuel and not is_hybrid_cand:
        score -= 20
    elif not is_hybrid_fuel and is_hybrid_cand:
        score -= 40

    return score


def _parse_displacement_liters(engine: str | None, nhtsa_disp: str | None = None) -> float | None:
    for raw in (nhtsa_disp, engine):
        if not raw:
            continue
        m = re.search(r'(\d+(?:\.\d+)?)\s*L\b', str(raw), re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
        try:
            return float(str(raw).strip())
        except ValueError:
            continue
    return None


def _score_epa_option(
    option_text: str,
    *,
    engine: str,
    transmission: str,
    drivetrain: str,
    cylinders: str | None,
    displacement: float | None,
) -> int:
    text = (option_text or '').lower()
    score = 0

    # Displacement match: "2.5 L"
    if displacement is not None:
        if re.search(rf'{re.escape(str(displacement).rstrip("0").rstrip("."))}\s*l\b', text) or \
           re.search(rf'{displacement:.1f}\s*l\b', text):
            score += 40
        else:
            # soft penalty when option has a different explicit liters value
            m = re.search(r'(\d+(?:\.\d+)?)\s*l\b', text)
            if m:
                try:
                    other = float(m.group(1))
                    if abs(other - displacement) >= 0.4:
                        score -= 25
                except ValueError:
                    pass

    # Cylinder match: "4 cyl"
    cyl_n = None
    if cylinders:
        try:
            cyl_n = int(float(cylinders))
        except ValueError:
            cyl_n = None
    if cyl_n is None and engine:
        m = re.search(r'\b(?:I|V|H|L)?(\d)\b', engine.upper())
        if m:
            try:
                cyl_n = int(m.group(1))
            except ValueError:
                cyl_n = None
    if cyl_n is not None:
        if re.search(rf'\b{cyl_n}\s*cyl\b', text):
            score += 25

    # Transmission cues
    tr = (transmission or '').lower()
    if 'cvt' in tr and 'av' in text:
        score += 15
    if 'manual' in tr and 'man' in text:
        score += 20
    if 'auto' in tr and 'auto' in text:
        score += 10
    m = re.search(r'(\d+)\s*-?\s*speed', tr)
    if m and m.group(1) in text:
        score += 10

    # Drivetrain
    dt = (drivetrain or '').upper()
    if dt == 'AWD' and 'awd' in text:
        score += 20
    elif dt == '4WD' and ('4wd' in text or '4x4' in text or 'four' in text):
        score += 20
    elif dt == 'FWD' and ('fwd' in text or 'front' in text):
        score += 10
    elif dt == 'RWD' and ('rwd' in text or 'rear' in text):
        score += 10
    elif dt in {'AWD', '4WD'} and ('fwd' in text or '2wd' in text) and 'awd' not in text and '4wd' not in text:
        score -= 15

    return score


def lookup_epa_mpg(
    *,
    year: int | None,
    make: str | None,
    model: str | None,
    trim: str | None = None,
    engine: str | None = None,
    transmission: str | None = None,
    drivetrain: str | None = None,
    fuel_type: str | None = None,
    nhtsa_displacement: str | None = None,
    nhtsa_cylinders: str | None = None,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """
    Best-effort EPA MPG lookup by year/make/model (+ trim/engine hints).

    Returns dict with mpg_city, mpg_highway, mpg_combined, epa_model, epa_option
    or None when no confident match is found.
    """
    if not year or not make or not model:
        return None

    cache_key = '|'.join([
        str(year),
        (make or '').lower(),
        (model or '').lower(),
        (trim or '').lower(),
        (engine or '').lower(),
        (transmission or '').lower(),
        (drivetrain or '').lower(),
        (fuel_type or '').lower(),
    ])
    hit, cached = _epa_cache_get(cache_key)
    if hit:
        return dict(cached) if cached else None

    try:
        models = _epa_menu_items(
            'model',
            {'year': str(year), 'make': make},
            timeout=timeout,
        )
    except RuntimeError:
        _epa_cache_set(cache_key, None)
        return None

    if not models:
        _epa_cache_set(cache_key, None)
        return None

    ranked_models: list[tuple[int, str]] = []
    for text, _value in models:
        score = _score_epa_model_name(text, model, trim or '', fuel_type or '')
        if score >= 45:
            ranked_models.append((score, text))
    ranked_models.sort(key=lambda x: x[0], reverse=True)
    if not ranked_models:
        _epa_cache_set(cache_key, None)
        return None

    displacement = _parse_displacement_liters(engine, nhtsa_displacement)
    best: tuple[int, dict[str, str], str, str] | None = None

    for model_score, epa_model in ranked_models[:6]:
        try:
            options = _epa_menu_items(
                'options',
                {'year': str(year), 'make': make, 'model': epa_model},
                timeout=timeout,
            )
        except RuntimeError:
            continue
        if not options:
            continue
        for opt_text, opt_id in options:
            if not opt_id:
                continue
            opt_score = _score_epa_option(
                opt_text,
                engine=engine or '',
                transmission=transmission or '',
                drivetrain=drivetrain or '',
                cylinders=nhtsa_cylinders,
                displacement=displacement,
            )
            total = model_score + opt_score
            # Prefer fewer options ambiguity: single option gets a small boost
            if len(options) == 1:
                total += 5
            if best is None or total > best[0]:
                best = (total, {'id': opt_id, 'text': opt_text}, epa_model, opt_text)

    if not best or best[0] < 50:
        _epa_cache_set(cache_key, None)
        return None

    try:
        vehicle = _epa_vehicle(best[1]['id'], timeout=timeout)
    except RuntimeError:
        _epa_cache_set(cache_key, None)
        return None

    def _to_int(raw: str | None) -> int | None:
        if raw is None or raw == '':
            return None
        try:
            return int(round(float(raw)))
        except ValueError:
            return None

    city = _to_int(vehicle.get('city08'))
    hwy = _to_int(vehicle.get('highway08'))
    comb = _to_int(vehicle.get('comb08'))
    if city is None and hwy is None:
        _epa_cache_set(cache_key, None)
        return None

    payload = {
        'mpg_city': city,
        'mpg_highway': hwy,
        'mpg_combined': comb,
        'epa_model': best[2],
        'epa_option': best[3],
        'epa_vehicle_id': vehicle.get('id'),
        'score': best[0],
    }
    _epa_cache_set(cache_key, payload)
    return dict(payload)


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

    colors = map_colors(result)

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
        'exterior_color': colors.get('exterior_color') or '',
        'interior_color': colors.get('interior_color') or '',
        'mpg_city': None,
        'mpg_highway': None,
        'mpg_combined': None,
        'doors': _nhtsa_get(result, 'Doors') or None,
        'plant_city': _nhtsa_get(result, 'PlantCity') or None,
        'plant_country': _nhtsa_get(result, 'PlantCountry') or None,
        'vehicle_type': _nhtsa_get(result, 'VehicleType') or None,
        'gvwr': _nhtsa_get(result, 'GVWR') or None,
        # retained for EPA matching
        '_displacement_l': _nhtsa_get(result, 'DisplacementL') or None,
        '_cylinders': _nhtsa_get(result, 'EngineCylinders') or None,
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
    sources = ['nhtsa']
    if codes and codes != ['0']:
        if err_text:
            warnings.append(err_text)
        else:
            warnings.append('Decoded with NHTSA warnings: ' + ', '.join(codes))

    if not vehicle.get('make') and not vehicle.get('model'):
        public_vehicle = {k: v for k, v in vehicle.items() if not str(k).startswith('_')}
        payload = {
            'ok': False,
            'vin': vin,
            'error': err_text or 'No vehicle data returned for this VIN.',
            'vehicle': public_vehicle,
            'features': features,
            'warnings': warnings,
            'source': 'nhtsa',
        }
        _cache_set(vin, payload)
        return dict(payload)

    # EPA fuel economy (best-effort; never fails the whole decode)
    epa = None
    try:
        epa = lookup_epa_mpg(
            year=vehicle.get('year'),
            make=vehicle.get('make'),
            model=vehicle.get('model'),
            trim=vehicle.get('trim'),
            engine=vehicle.get('engine'),
            transmission=vehicle.get('transmission'),
            drivetrain=vehicle.get('drivetrain'),
            fuel_type=vehicle.get('fuel_type'),
            nhtsa_displacement=vehicle.get('_displacement_l'),
            nhtsa_cylinders=vehicle.get('_cylinders'),
        )
    except Exception:
        epa = None

    if epa:
        vehicle['mpg_city'] = epa.get('mpg_city')
        vehicle['mpg_highway'] = epa.get('mpg_highway')
        vehicle['mpg_combined'] = epa.get('mpg_combined')
        vehicle['epa_model'] = epa.get('epa_model')
        vehicle['epa_option'] = epa.get('epa_option')
        sources.append('epa')
    else:
        warnings.append('Fuel economy not found in EPA database for this configuration.')

    # Paint color is not part of the VIN — tell the admin clearly when empty.
    if not vehicle.get('exterior_color') and not vehicle.get('interior_color'):
        warnings.append(
            'Exterior/interior color are not encoded in the VIN — enter them manually.'
        )

    public_vehicle = {k: v for k, v in vehicle.items() if not str(k).startswith('_')}
    payload = {
        'ok': True,
        'vin': vin,
        'error': None,
        'vehicle': public_vehicle,
        'features': features,
        'warnings': warnings,
        'source': '+'.join(sources),
    }
    _cache_set(vin, payload)
    return dict(payload)
