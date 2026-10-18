"""NHTSA vPIC + EPA fuel-economy helpers for admin vehicle form prefills.

Uses free public APIs (no keys):
- NHTSA DecodeVinValues for year/make/model/specs/features
- EPA fueleconomy.gov for city/highway MPG

Paint colors are not encoded in a VIN; exterior/interior color keys are
returned empty so the admin form can still merge them if a future source
provides values.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
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
    'https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValuesExtended/{vin}?format=json'
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
    'LaneCenteringAssistance': 'Lane Centering Assist',
    'LaneDepartureWarning': 'Lane Departure Warning',
    'LaneKeepSystem': 'Lane Keep Assist',
    'ParkAssist': 'Parking Sensors',
    'PedestrianAutomaticEmergencyBraking': 'Automatic Emergency Braking',
    'RearAutomaticEmergencyBraking': 'Automatic Emergency Braking',
    'RearCrossTrafficAlert': 'Rear Cross-Traffic Alert',
    'RearVisibilitySystem': 'Backup Camera',
    'AdaptiveDrivingBeam': 'Adaptive Headlights',
}

_SAFETY_EQUIPMENT_MAP: dict[str, str] = {
    'ABS': 'Anti-lock Braking System',
    'ESC': 'Electronic Stability Control',
    'TractionControl': 'Traction Control',
    'DynamicBrakeSupport': 'Dynamic Brake Support',
    'BlindSpotMon': 'Blind Spot Monitor',
    'BlindSpotIntervention': 'Blind Spot Intervention',
    'CIB': 'Automatic Emergency Braking',
    'ForwardCollisionWarning': 'Forward Collision Warning',
    'LaneCenteringAssistance': 'Lane Centering Assist',
    'LaneDepartureWarning': 'Lane Departure Warning',
    'LaneKeepSystem': 'Lane Keep Assist',
    'ParkAssist': 'Parking Assist',
    'PedestrianAutomaticEmergencyBraking': 'Pedestrian Detection',
    'RearAutomaticEmergencyBraking': 'Rear Automatic Emergency Braking',
    'RearCrossTrafficAlert': 'Rear Cross-Traffic Alert',
    'RearVisibilitySystem': 'Backup Camera',
    'AdaptiveCruiseControl': 'Adaptive Cruise Control',
    'AutoReverseSystem': 'Automatic Reverse Braking',
}

_AIRBAG_LOCATION_MAP: dict[str, str] = {
    'AirBagLocFront': 'Front Airbags',
    'AirBagLocSide': 'Side-Impact Airbags',
    'AirBagLocCurtain': 'Side Curtain Airbags',
    'AirBagLocKnee': 'Knee Airbags',
    'AirBagLocSeatCushion': 'Seat Cushion Airbags',
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


def _nhtsa_value(field: dict[str, Any] | None, *keys: str) -> str:
    """Return a decoded field only when it contains usable information."""
    value = _nhtsa_get(field, *keys)
    return '' if value.casefold() in _ABSENT_VALUES else value


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
    fuel = map_fuel_type(_nhtsa_value(result, 'FuelTypePrimary'))
    if fuel == 'Electric':
        # Prefer battery / motor info when present
        motor = _nhtsa_value(result, 'ElectrificationLevel', 'EngineModel')
        return motor or 'Electric'

    displacement = _nhtsa_value(result, 'DisplacementL')
    cylinders = _nhtsa_value(result, 'EngineCylinders')
    config = _nhtsa_value(result, 'EngineConfiguration')
    model = _nhtsa_value(result, 'EngineModel')

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
        add('HID Headlights')

    # Wheel diameter is decoded; wheel material is not, so do not claim alloy.
    wheel = _nhtsa_get(result, 'WheelSizeFront', 'WheelSizeRear')
    try:
        wheel_n = float(wheel) if wheel else 0
    except ValueError:
        wheel_n = 0
    if wheel_n >= 17:
        add(f'{wheel_n:g}-Inch Wheels')

    if _is_present(_nhtsa_get(result, 'DaytimeRunningLight')):
        add('Daytime Running Lights')

    entertainment = _nhtsa_get(result, 'EntertainmentSystem')
    if entertainment and entertainment.casefold() not in _ABSENT_VALUES:
        add(entertainment)

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


def _append_unique(lines: list[str], value: str) -> None:
    if value and value.casefold() not in {line.casefold() for line in lines}:
        lines.append(value)


_VIN_DECODE_GROUPS = (
    ('Vehicle Identity', 'identity'),
    ('Powertrain', 'powertrain'),
    ('Dimensions & Chassis', 'chassis'),
    ('Safety & Equipment', 'safety'),
    ('Occupants & Restraints', 'occupants'),
    ('Manufacturer & Assembly', 'manufacturing'),
    ('EPA Fuel Economy', 'epa'),
    ('Other Decoded Data', 'other'),
)

_VIN_DECODE_LABELS = {
    'ABS': 'Anti-lock Braking System',
    'AirBagLocCurtain': 'Curtain Airbag Location',
    'AirBagLocFront': 'Front Airbag Location',
    'AirBagLocKnee': 'Knee Airbag Location',
    'AirBagLocSeatCushion': 'Seat Cushion Airbag Location',
    'AirBagLocSide': 'Side Airbag Location',
    'CIB': 'Crash Imminent Braking',
    'CurbWeightLB': 'Curb Weight (lb)',
    'DisplacementL': 'Engine Displacement (L)',
    'ESC': 'Electronic Stability Control',
    'EngineHP': 'Engine Horsepower',
    'EngineKW': 'Engine Power (kW)',
    'GVWR': 'Gross Vehicle Weight Rating',
    'MakeID': 'Make ID',
    'ManufacturerId': 'Manufacturer ID',
    'ModelID': 'Model ID',
    'ModelYear': 'Model Year',
    'NCSABodyType': 'NCSA Body Type',
    'NCSAMake': 'NCSA Make',
    'NCSAModel': 'NCSA Model',
    'SeatBeltsAll': 'Seat Belts - All Positions',
    'TPMS': 'Tire Pressure Monitoring System',
    'VIN': 'VIN',
    'VINDescriptor': 'Vehicle Descriptor',
    'VehicleDescriptor': 'Vehicle Descriptor',
    'WheelBaseLong': 'Wheelbase (Long)',
    'WheelBaseShort': 'Wheelbase (Short)',
    'atvType': 'ATV Type',
    'city08': 'City MPG',
    'comb08': 'Combined MPG',
    'cylinders': 'Engine Cylinders',
    'displ': 'Engine Displacement (L)',
    'drive': 'Drivetrain',
    'fuelType1': 'Fuel Type',
    'highway08': 'Highway MPG',
    'id': 'ID',
    'trany': 'Transmission',
}

_VIN_DECODE_OMIT_VALUES = {
    '',
    'n/a',
    'na',
    'not applicable',
    'not available',
    'unknown',
}

_VIN_DECODE_OMIT_FIELDS = {'ErrorCode', 'ErrorText'}

_GROK_ENUM_ESTIMATES = {
    'drivetrain': {
        'fwd': 'Front-wheel drive',
        'front-wheel drive': 'Front-wheel drive',
        'rwd': 'Rear-wheel drive',
        'rear-wheel drive': 'Rear-wheel drive',
        'awd': 'All-wheel drive',
        'all-wheel drive': 'All-wheel drive',
        '4wd': 'Four-wheel drive',
        'four-wheel drive': 'Four-wheel drive',
        '4x4': 'Four-wheel drive',
    },
    'transmission': {
        'automatic': 'Automatic',
        'manual': 'Manual',
        'cvt': 'Continuously variable (CVT)',
        'continuously variable (cvt)': 'Continuously variable (CVT)',
        'dual-clutch automatic': 'Dual-clutch automatic',
    },
    'body_style': {
        'sedan': 'Sedan',
        'suv': 'SUV',
        'hatchback': 'Hatchback',
        'coupe': 'Coupe',
        'wagon': 'Wagon',
        'pickup': 'Pickup',
        'pickup truck': 'Pickup',
        'van': 'Van',
        'minivan': 'Minivan',
        'convertible': 'Convertible',
    },
    'fuel_type': {
        'gasoline': 'Gasoline',
        'diesel': 'Diesel',
        'electric': 'Electric',
        'hybrid': 'Hybrid',
        'plug-in hybrid': 'Plug-in hybrid',
        'flex fuel': 'Flex fuel',
        'cng': 'CNG',
        'lpg': 'LPG',
    },
}

_GROK_ENUM_FIELDS = {
    'drivetrain': ('DriveType', 'drivetrain'),
    'transmission': ('TransmissionStyle', 'transmission'),
    'body_style': ('BodyClass', 'body_style'),
    'fuel_type': ('FuelTypePrimary', 'fuel_type'),
}

_GROK_NUMERIC_FIELDS = {
    'engine_horsepower_hp': ('EngineHP', None, 30, 2000, 'hp', 0),
    'engine_torque_lb_ft': ('EngineTorque', None, 25, 2000, 'lb-ft', 0),
    'curb_weight_lb': ('CurbWeightLB', None, 500, 30000, 'lb', 0),
    'wheelbase_in': ('WheelBaseShort', 'WheelBaseLong', 50, 300, 'in', 1),
    'overall_length_in': ('OverallLength', None, 80, 400, 'in', 1),
    'overall_width_in': ('OverallWidth', None, 40, 120, 'in', 1),
    'overall_height_in': ('OverallHeight', None, 40, 160, 'in', 1),
    'passenger_capacity': ('Seats', 'NumberOfSeats', 1, 20, 'passengers', 0),
    'city_mpg': (None, None, 1, 200, 'MPG', 0),
    'highway_mpg': (None, None, 1, 200, 'MPG', 0),
    'combined_mpg': (None, None, 1, 200, 'MPG', 0),
}

_GROK_ESTIMATE_LABELS = {
    'engine_horsepower_hp': 'Engine horsepower',
    'engine_torque_lb_ft': 'Engine torque',
    'curb_weight_lb': 'Curb weight',
    'wheelbase_in': 'Wheelbase',
    'overall_length_in': 'Overall length',
    'overall_width_in': 'Overall width',
    'overall_height_in': 'Overall height',
    'passenger_capacity': 'Passenger capacity',
    'city_mpg': 'City MPG',
    'highway_mpg': 'Highway MPG',
    'combined_mpg': 'Combined MPG',
    'drivetrain': 'Drivetrain',
    'transmission': 'Transmission',
    'body_style': 'Body style',
    'fuel_type': 'Fuel type',
}

_GROK_PHOTO_FEATURE_CATEGORIES = {
    'alloy wheels': ('exterior', {'exterior_front', 'exterior_side', 'exterior_rear', 'wheel_closeup'}),
    'new tires': ('exterior', {'exterior_front', 'exterior_side', 'exterior_rear', 'wheel_closeup'}),
    'sunroof': ('exterior', {'exterior_front', 'exterior_side', 'exterior_rear', 'interior_cabin'}),
    'moonroof': ('exterior', {'exterior_front', 'exterior_side', 'interior_cabin'}),
    'panoramic roof': ('exterior', {'exterior_front', 'exterior_side', 'interior_cabin'}),
    'running boards': ('exterior', {'exterior_side'}),
    'trailer tow': ('exterior', {'exterior_rear'}),
    'tow package': ('exterior', {'exterior_rear'}),
    'power liftgate': ('exterior', {'exterior_rear'}),
    'leather seats': ('interior', {'interior_cabin'}),
    'leather interior': ('interior', {'interior_cabin'}),
    'heated seats': ('interior', {'interior_cabin'}),
    'ventilated seats': ('interior', {'interior_cabin'}),
    'third row seating': ('interior', {'interior_cabin'}),
    'seat surface': ('interior', {'interior_cabin'}),
    'apple carplay': ('entertainment', {'interior_dash', 'interior_cabin'}),
    'android auto': ('entertainment', {'interior_dash', 'interior_cabin'}),
    'navigation': ('entertainment', {'interior_dash', 'interior_cabin'}),
    'bluetooth': ('entertainment', {'interior_dash', 'interior_cabin'}),
    'premium audio': ('entertainment', {'interior_dash', 'interior_cabin'}),
    'infotainment screen': ('entertainment', {'interior_dash', 'interior_cabin'}),
}

_GROK_PHOTO_DETAIL_CATEGORIES = {
    'wheel detail': ('exterior', {'exterior_front', 'exterior_side', 'exterior_rear', 'wheel_closeup'}),
    'infotainment screen': ('entertainment', {'interior_dash', 'interior_cabin'}),
    'seat surface': ('interior', {'interior_cabin'}),
}

_GROK_PHOTO_CATEGORIES = ('exterior', 'interior', 'entertainment')
_GROK_PHOTO_ANNOTATION_VERSION = 1
_GROK_PHOTO_MIN_CONFIDENCE = 0.85


def grok_photo_annotation_signature(images: list[Any]) -> str:
    states = sorted(
        (
            image.id,
            image.highlight_status,
            image.highlight_version,
            image.highlight_analyzed_at.isoformat()
            if image.highlight_analyzed_at else '',
        )
        for image in images
    )
    return hashlib.sha256(
        json.dumps(states, separators=(',', ':')).encode('utf-8'),
    ).hexdigest()


def _humanize_vin_field(field: str) -> str:
    if field.startswith('EPA '):
        if field[4:] == 'id':
            return 'EPA Vehicle ID'
        return f'EPA {_humanize_vin_field(field[4:])}'
    if field in _VIN_DECODE_LABELS:
        return _VIN_DECODE_LABELS[field]
    words = re.findall(r'[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+', field)
    return ' '.join(
        word.upper() if word.casefold() == 'id'
        else word if word.isupper()
        else word[0].upper() + word[1:]
        for word in words
    )


def _vin_decode_group(field: str) -> str:
    if field.startswith('EPA '):
        return 'epa'
    name = field.casefold()
    if field in {
        'VIN', 'ModelYear', 'Make', 'MakeID', 'Model', 'ModelID', 'Trim',
        'Trim2', 'Series', 'Series2', 'BodyClass', 'VehicleType',
        'VehicleDescriptor', 'NCSABodyType', 'NCSAMake', 'NCSAModel',
    }:
        return 'identity'
    if name.startswith((
        'engine', 'transmission', 'fuel', 'electrification', 'battery',
        'evdrive', 'charger', 'charge', 'plug',
    )) or name in {'drivetype', 'otherengineinfo', 'turbo'}:
        return 'powertrain'
    if any(token in name for token in (
        'wheel', 'axle', 'gvwr', 'curbweight', 'length', 'width', 'height',
        'track', 'tire', 'brake', 'steering', 'suspension', 'spring',
        'wheeldrive',
    )):
        return 'chassis'
    if any(token in name for token in (
        'airbag', 'abs', 'esc', 'traction', 'blindspot', 'collision', 'lane',
        'brake', 'crash', 'parkassist', 'reverse', 'visibility', 'tpms',
        'daytimerunning', 'adaptivecruise', 'adaptivebraking', 'keylessignition',
        'child', 'seatbelt',
    )):
        return 'safety'
    if any(token in name for token in (
        'seat', 'row', 'door', 'occupant', 'passenger', 'restraint',
    )):
        return 'occupants'
    if name.startswith(('plant', 'manufacturer')) or field in {
        'MakeID', 'ManufacturerId',
    }:
        return 'manufacturing'
    return 'other'


def group_vin_decode_data(raw_data: str | dict[str, Any] | None) -> list[dict[str, Any]]:
    """Group manufacturer/EPA data and separately label unverified Grok estimates."""
    if isinstance(raw_data, str):
        try:
            decoded = json.loads(raw_data)
        except json.JSONDecodeError:
            return []
    else:
        decoded = raw_data
    if not isinstance(decoded, dict):
        return []

    grouped: dict[str, list[dict[str, str]]] = {
        key: [] for _, key in _VIN_DECODE_GROUPS
    }
    grok_data = decoded.get('Grok')
    fields = []
    for source, source_data in decoded.items():
        if source == 'Grok':
            continue
        if isinstance(source_data, dict):
            prefix = 'EPA ' if source == 'EPA' else ''
            fields.extend(
                (f'{prefix}{field}', value)
                for field, value in source_data.items()
            )
        else:
            fields.append((source, source_data))

    for field, raw_value in fields:
        if field in _VIN_DECODE_OMIT_FIELDS or raw_value is None:
            continue
        value = str(raw_value).strip()
        if value.casefold() in _VIN_DECODE_OMIT_VALUES:
            continue
        group = _vin_decode_group(str(field))
        grouped[group].append({
            'label': _humanize_vin_field(str(field)),
            'value': value,
        })

    groups = [
        {'label': label, 'fields': grouped[key]}
        for label, key in _VIN_DECODE_GROUPS
        if grouped[key]
    ]
    nhtsa_data = decoded.get('NHTSA')
    epa_data = decoded.get('EPA')
    estimates = grok_data.get('estimates') if isinstance(grok_data, dict) else None
    grok_fields = []
    if isinstance(nhtsa_data, dict) and isinstance(estimates, dict):
        eligible_fields = set(_missing_grok_estimate_fields(
            nhtsa_data,
            epa_data if isinstance(epa_data, dict) else {},
        ))
        grok_fields = [
            {
                'label': _GROK_ESTIMATE_LABELS[field],
                'value': str(value).strip(),
            }
            for field, value in estimates.items()
            if field in eligible_fields
            and field in _GROK_ESTIMATE_LABELS
            and value is not None
            and str(value).strip()
        ]
    if grok_fields:
        groups.append({
            'label': 'Grok Estimates (not manufacturer-verified)',
            'fields': grok_fields,
        })
    observations = (
        grok_data.get('photo_annotations')
        if isinstance(grok_data, dict) else None
    )
    if isinstance(observations, dict):
        photo_fields = []
        for category in _GROK_PHOTO_CATEGORIES:
            for item in observations.get(category, []):
                if not isinstance(item, dict):
                    continue
                label = str(item.get('label') or '').strip()
                scene = str(item.get('scene') or '').strip().replace('_', ' ')
                if not label or not scene:
                    continue
                try:
                    confidence = float(item.get('confidence'))
                except (TypeError, ValueError):
                    continue
                feature_info = (
                    _GROK_PHOTO_FEATURE_CATEGORIES.get(label.casefold())
                    or _GROK_PHOTO_DETAIL_CATEGORIES.get(label.casefold())
                )
                if (
                    not feature_info
                    or feature_info[0] != category
                    or scene.replace(' ', '_') not in feature_info[1]
                    or not math.isfinite(confidence)
                    or not _GROK_PHOTO_MIN_CONFIDENCE <= confidence <= 1
                ):
                    continue
                photo_fields.append({
                    'label': f'{category.title()}: {label}',
                    'value': f'Visible in {scene} photo ({confidence:.0%} confidence)',
                })
        if photo_fields:
            groups.append({
                'label': 'Grok Visual Details (verify)',
                'fields': photo_fields,
            })
    return groups


def classify_grok_photo_annotations(
    images: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Keep high-confidence feature annotations relevant to their photo scene."""
    classified: dict[str, dict[str, dict[str, Any]]] = {
        category: {} for category in _GROK_PHOTO_CATEGORIES
    }
    for image in images:
        scene = str(image.get('scene') or '').strip().casefold()
        for highlight in image.get('highlights') or []:
            if not isinstance(highlight, dict):
                continue
            label = str(highlight.get('label') or '').strip()
            if highlight.get('category') == 'feature':
                category_info = _GROK_PHOTO_FEATURE_CATEGORIES.get(label.casefold())
            elif highlight.get('category') == 'detail':
                category_info = _GROK_PHOTO_DETAIL_CATEGORIES.get(label.casefold())
            else:
                category_info = None
            if not category_info:
                continue
            category, allowed_scenes = category_info
            if scene not in allowed_scenes:
                continue
            try:
                confidence = float(highlight.get('confidence'))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(confidence) or not (
                _GROK_PHOTO_MIN_CONFIDENCE <= confidence <= 1
            ):
                continue
            prior = classified[category].get(label.casefold())
            if prior and prior['confidence'] >= confidence:
                continue
            classified[category][label.casefold()] = {
                'label': label,
                'scene': scene,
                'confidence': round(confidence, 3),
            }
    return {
        category: list(classified[category].values())
        for category in _GROK_PHOTO_CATEGORIES
    }


def collect_grok_photo_annotations(vehicle_id: int) -> dict[str, Any] | None:
    """Reuse completed Grok photo results; return None while any image is pending."""
    from app.models import PhotoHighlightJob, VehicleImage, VehicleImageHighlight

    images = VehicleImage.query.filter_by(vehicle_id=vehicle_id).all()
    if any(image.highlight_status in {'pending', 'processing'} for image in images):
        return None
    image_ids = [image.id for image in images]
    if image_ids and PhotoHighlightJob.query.filter(
        PhotoHighlightJob.vehicle_image_id.in_(image_ids),
        PhotoHighlightJob.status.in_(('queued', 'running')),
    ).first():
        return None

    ready_image_ids = [
        image.id for image in images if image.highlight_status == 'ready'
    ]
    if not ready_image_ids:
        return {
            'version': _GROK_PHOTO_ANNOTATION_VERSION,
            'image_signature': grok_photo_annotation_signature(images),
            **{category: [] for category in _GROK_PHOTO_CATEGORIES},
        }

    jobs = PhotoHighlightJob.query.filter(
        PhotoHighlightJob.vehicle_image_id.in_(ready_image_ids),
        PhotoHighlightJob.status == 'completed',
    ).order_by(
        PhotoHighlightJob.finished_at.desc(),
        PhotoHighlightJob.id.desc(),
    ).all()
    latest_jobs = {}
    for job in jobs:
        latest_jobs.setdefault(job.vehicle_image_id, job)
    grok_image_ids = [
        image_id
        for image_id, job in latest_jobs.items()
        if 'engine=grok' in (job.result_summary or '').casefold()
    ]
    if not grok_image_ids:
        return {
            'version': _GROK_PHOTO_ANNOTATION_VERSION,
            'image_signature': grok_photo_annotation_signature(images),
            **{category: [] for category in _GROK_PHOTO_CATEGORIES},
        }

    image_scenes = {
        image.id: image.highlight_scene
        for image in images
        if image.id in grok_image_ids
    }
    highlights = VehicleImageHighlight.query.filter(
        VehicleImageHighlight.vehicle_image_id.in_(grok_image_ids),
        VehicleImageHighlight.source == 'auto',
        VehicleImageHighlight.is_visible.is_(True),
        VehicleImageHighlight.category.in_(('feature', 'detail')),
        VehicleImageHighlight.confidence >= _GROK_PHOTO_MIN_CONFIDENCE,
    ).all()
    by_image: dict[int, list[dict[str, Any]]] = {}
    for highlight in highlights:
        by_image.setdefault(highlight.vehicle_image_id, []).append({
            'label': highlight.label,
            'category': highlight.category,
            'confidence': highlight.confidence,
        })
    observations = classify_grok_photo_annotations([
        {'scene': image_scenes.get(image_id), 'highlights': items}
        for image_id, items in by_image.items()
    ])
    return {
        'version': _GROK_PHOTO_ANNOTATION_VERSION,
        'image_signature': grok_photo_annotation_signature(images),
        **observations,
    }


def merge_grok_photo_annotations(
    vin_decode_data: dict[str, Any],
    annotations: dict[str, Any],
) -> dict[str, list[str]]:
    """Store visual observations separately and return labeled detail-tab lines."""
    grok_data = vin_decode_data.setdefault('Grok', {})
    if not isinstance(grok_data, dict):
        grok_data = {}
        vin_decode_data['Grok'] = grok_data
    grok_data['photo_annotations'] = annotations

    details = {
        'exterior_features': [],
        'interior_features': [],
        'entertainment_features': [],
    }
    field_map = {
        'exterior': 'exterior_features',
        'interior': 'interior_features',
        'entertainment': 'entertainment_features',
    }
    for category, field in field_map.items():
        for item in annotations.get(category, []):
            if isinstance(item, dict) and item.get('label'):
                details[field].append(
                    f"Verify on vehicle: {str(item['label']).strip()}"
                )
    return details


def _xai_api_key() -> str:
    return (
        os.environ.get('XAI_API_KEY')
        or os.environ.get('GROK_API_KEY')
        or ''
    ).strip()


def is_vin_grok_enrichment_enabled() -> bool:
    return bool(_xai_api_key())


def _missing_grok_estimate_fields(
    nhtsa: dict[str, Any],
    epa: dict[str, Any],
) -> list[str]:
    missing = []
    for key, definition in _GROK_NUMERIC_FIELDS.items():
        nhtsa_field, alternate_field = definition[:2]
        if key in {'city_mpg', 'highway_mpg', 'combined_mpg'}:
            epa_field = {
                'city_mpg': 'city08',
                'highway_mpg': 'highway08',
                'combined_mpg': 'comb08',
            }[key]
            present = _nhtsa_value(epa, epa_field)
        else:
            present = _nhtsa_value(
                nhtsa,
                *(field for field in (nhtsa_field, alternate_field) if field),
            )
        if not present:
            missing.append(key)

    for key, (nhtsa_field, _) in _GROK_ENUM_FIELDS.items():
        if not _nhtsa_value(nhtsa, nhtsa_field):
            missing.append(key)
    return missing


def _validate_grok_estimate(field: str, value: Any) -> str | None:
    if field in _GROK_ENUM_FIELDS:
        if not isinstance(value, str):
            return None
        allowed_values = _GROK_ENUM_ESTIMATES.get(field)
        return allowed_values.get(value.strip().casefold()) if allowed_values else None

    definition = _GROK_NUMERIC_FIELDS.get(field)
    if not definition or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    _, _, minimum, maximum, unit, decimals = definition
    if not math.isfinite(number) or not minimum <= number <= maximum:
        return None
    if decimals == 0 and not number.is_integer():
        return None
    formatted = f'{number:.{decimals}f}' if decimals else f'{int(number)}'
    return f'{formatted} {unit}'


def enrich_vin_decode_data_with_grok(
    vin_decode_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Return low-token, validated estimates only for missing VIN specs."""
    api_key = _xai_api_key()
    nhtsa = vin_decode_data.get('NHTSA')
    if not api_key or not isinstance(nhtsa, dict):
        return None

    epa = vin_decode_data.get('EPA')
    if not isinstance(epa, dict):
        epa = {}
    missing = _missing_grok_estimate_fields(nhtsa, epa)
    year = _nhtsa_value(nhtsa, 'ModelYear')
    make = _nhtsa_value(nhtsa, 'Make')
    model = _nhtsa_value(nhtsa, 'Model')
    if not missing or not year or not make or not model:
        return {'estimates': {}}

    context = {
        'year': year,
        'make': make,
        'model': model,
        'trim': _nhtsa_value(nhtsa, 'Trim', 'Series'),
        'engine_displacement_l': _nhtsa_value(nhtsa, 'DisplacementL'),
        'engine_cylinders': _nhtsa_value(nhtsa, 'EngineCylinders'),
        'fuel_type': _nhtsa_value(nhtsa, 'FuelTypePrimary'),
        'missing_fields': missing,
    }
    prompt = (
        'Return a JSON object mapping requested field names to values. '
        'Estimate only factory specifications you know confidently for this exact '
        'year/make/model/trim; omit uncertain values. Never guess optional equipment, '
        'colors, condition, or safety features. These are supplemental estimates; '
        'manufacturer and EPA data take precedence.\n'
        + json.dumps(context, separators=(',', ':'))
    )
    model_name = (
        os.environ.get('VIN_GROK_MODEL')
        or 'grok-4.20-0309-non-reasoning'
    ).strip()
    request_body = {
        'model': model_name,
        'temperature': 0,
        'max_tokens': 128,
        'response_format': {'type': 'json_object'},
        'messages': [
            {
                'role': 'system',
                'content': 'Conservative vehicle-spec estimator. Never invent missing facts.',
            },
            {'role': 'user', 'content': prompt},
        ],
    }
    request = urllib.request.Request(
        'https://api.x.ai/v1/chat/completions',
        data=json.dumps(request_body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'marshallauto-vin-enrichment/1',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            response_data = json.loads(response.read().decode('utf-8'))
        content = response_data['choices'][0]['message']['content']
        estimates = json.loads(content)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Grok VIN enrichment HTTP {exc.code}') from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f'Grok VIN enrichment request failed: {exc}') from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError('Grok VIN enrichment returned an invalid response') from exc

    if not isinstance(estimates, dict):
        raise RuntimeError('Grok VIN enrichment did not return a JSON object')
    validated = {}
    for field in missing:
        value = _validate_grok_estimate(field, estimates.get(field))
        if value:
            validated[field] = value
    return {'estimates': validated}


def derive_vehicle_details(result: dict[str, Any]) -> dict[str, str]:
    """Format only VIN-decoded equipment and specifications for editable form fields."""
    details = {
        'exterior_features': [],
        'interior_features': [],
        'entertainment_features': [],
        'mechanical_features': [],
        'safety_features': [],
        'additional_specifications': [],
    }

    front_wheel = _nhtsa_value(result, 'WheelSizeFront')
    rear_wheel = _nhtsa_value(result, 'WheelSizeRear')
    if front_wheel:
        _append_unique(details['exterior_features'], f'{front_wheel}-inch front wheels')
    if rear_wheel:
        _append_unique(details['exterior_features'], f'{rear_wheel}-inch rear wheels')
    headlamp = _nhtsa_value(result, 'LowerBeamHeadlampLightSource', 'HeadlampLightSource')
    if headlamp:
        _append_unique(details['exterior_features'], f'{headlamp} headlights')
    if _is_present(_nhtsa_get(result, 'DaytimeRunningLight')):
        _append_unique(details['exterior_features'], 'Daytime running lights')

    seats = _nhtsa_value(result, 'Seats', 'NumberOfSeats', 'PassengerCapacity')
    seat_rows = _nhtsa_value(result, 'SeatRows')
    if seats:
        _append_unique(details['interior_features'], f'{seats}-passenger seating')
    if seat_rows:
        _append_unique(details['interior_features'], f'{seat_rows} seating rows')

    entertainment = _nhtsa_value(result, 'EntertainmentSystem')
    if entertainment:
        _append_unique(details['entertainment_features'], entertainment)

    drivetrain = _nhtsa_value(result, 'DriveType')
    if drivetrain:
        _append_unique(details['mechanical_features'], drivetrain)
    transmission_speeds = _nhtsa_value(result, 'TransmissionSpeeds')
    transmission_style = _nhtsa_value(result, 'TransmissionStyle')
    if transmission_speeds and transmission_style:
        _append_unique(
            details['mechanical_features'],
            f'{transmission_speeds}-speed {transmission_style.lower()} transmission',
        )
    displacement = _nhtsa_value(result, 'DisplacementL')
    cylinders = _nhtsa_value(result, 'EngineCylinders')
    if displacement and cylinders:
        _append_unique(
            details['mechanical_features'],
            f'{displacement}L {cylinders}-cylinder engine',
        )

    for field, label in _SAFETY_EQUIPMENT_MAP.items():
        if _is_present(_nhtsa_get(result, field)):
            _append_unique(details['safety_features'], label)
    tpms = _nhtsa_value(result, 'TPMS')
    if tpms:
        _append_unique(details['safety_features'], 'Tire Pressure Monitoring System')
    for field, label in _AIRBAG_LOCATION_MAP.items():
        location = _nhtsa_value(result, field)
        if location:
            _append_unique(details['safety_features'], label)

    engine_model = _nhtsa_value(result, 'EngineModel')
    engine_hp = _nhtsa_value(result, 'EngineHP')
    engine_kw = _nhtsa_value(result, 'EngineKW')
    engine_configuration = _nhtsa_value(result, 'EngineConfiguration')
    valve_train = _nhtsa_value(result, 'ValveTrainDesign')
    fuel_injection = _nhtsa_value(result, 'FuelInjectionType')
    fuel_type = _nhtsa_value(result, 'FuelTypePrimary')
    wheelbase = _nhtsa_value(result, 'WheelBaseShort', 'WheelBaseLong')
    gvwr = _nhtsa_value(result, 'GVWR')
    curb_weight = _nhtsa_value(result, 'CurbWeightLB')
    axles = _nhtsa_value(result, 'Axles')
    for label, value in (
        ('Engine model', engine_model),
        ('Engine type', _nhtsa_value(result, 'EngineType')),
        ('Engine configuration', engine_configuration),
        ('Engine displacement', ' / '.join(
            part for part in (
                f'{displacement} L' if displacement else '',
                f'{_nhtsa_value(result, "DisplacementCI")} cu in'
                if _nhtsa_value(result, 'DisplacementCI') else '',
            ) if part
        )),
        ('Engine cylinders', cylinders),
        ('Engine output', ' / '.join(
            part for part in (
                f'{engine_hp} hp' if engine_hp else '',
                f'{engine_kw} kW' if engine_kw else '',
            ) if part
        )),
        ('Engine torque', _nhtsa_value(result, 'EngineTorque')),
        ('Valve train', valve_train),
        ('Fuel system', fuel_injection),
        ('Fuel type', fuel_type),
        ('Wheelbase', f'{wheelbase} in' if wheelbase else ''),
        ('Overall length', f'{_nhtsa_value(result, "OverallLength")} in'
         if _nhtsa_value(result, 'OverallLength') else ''),
        ('Overall width', f'{_nhtsa_value(result, "OverallWidth")} in'
         if _nhtsa_value(result, 'OverallWidth') else ''),
        ('Overall height', f'{_nhtsa_value(result, "OverallHeight")} in'
         if _nhtsa_value(result, 'OverallHeight') else ''),
        ('Number of axles', axles),
        ('Gross vehicle weight rating', gvwr),
        ('Curb weight', f'{curb_weight} lb' if curb_weight else ''),
    ):
        if value:
            _append_unique(details['additional_specifications'], f'{label}: {value}')

    return {name: '\n'.join(lines) for name, lines in details.items()}


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
        'epa_details': vehicle,
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

    trim = _nhtsa_value(result, 'Trim', 'Series', 'Trim2', 'Series2')
    make = _title_case_make(_nhtsa_value(result, 'Make'))
    model = _nhtsa_value(result, 'Model')
    # Light title-case for multi-word models when NHTSA returns ALL CAPS
    if model and model.isupper() and len(model) > 3:
        model = model.title()

    colors = map_colors(result)

    return {
        'year': year,
        'make': make,
        'model': model,
        'trim': trim,
        'body_style': map_body_style(_nhtsa_value(result, 'BodyClass')),
        'drivetrain': map_drivetrain(_nhtsa_value(result, 'DriveType')),
        'fuel_type': map_fuel_type(_nhtsa_value(result, 'FuelTypePrimary')),
        'engine': build_engine_description(result),
        'transmission': map_transmission(
            _nhtsa_value(result, 'TransmissionStyle'),
            _nhtsa_value(result, 'TransmissionSpeeds'),
        ),
        'exterior_color': colors.get('exterior_color') or '',
        'interior_color': colors.get('interior_color') or '',
        'mpg_city': None,
        'mpg_highway': None,
        'mpg_combined': None,
        'doors': _nhtsa_value(result, 'Doors') or None,
        'passenger_capacity': _nhtsa_value(
            result, 'Seats', 'NumberOfSeats', 'PassengerCapacity',
        ) or None,
        'plant_city': _nhtsa_get(result, 'PlantCity') or None,
        'plant_country': _nhtsa_get(result, 'PlantCountry') or None,
        'vehicle_type': _nhtsa_get(result, 'VehicleType') or None,
        'gvwr': _nhtsa_value(result, 'GVWR') or None,
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
    """Call NHTSA DecodeVinValuesExtended and return the full first result row."""
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
    vehicle['vin_decode_data'] = {'NHTSA': result}
    features = derive_features(result)
    vehicle.update(derive_vehicle_details(result))
    existing_features = {feature.casefold() for feature in features}
    for feature in vehicle['safety_features'].splitlines():
        if feature.casefold() not in existing_features:
            features.append(feature)
            existing_features.add(feature.casefold())
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
        epa_details = dict(epa.get('epa_details') or {})
        epa_details.update({
            'Matched model': epa.get('epa_model') or '',
            'Matched configuration': epa.get('epa_option') or '',
        })
        vehicle['vin_decode_data']['EPA'] = epa_details
        sources.append('epa')
    else:
        warnings.append('Fuel economy not found in EPA database for this configuration.')

    if is_vin_grok_enrichment_enabled():
        try:
            grok_data = enrich_vin_decode_data_with_grok(vehicle['vin_decode_data'])
            if grok_data is not None:
                vehicle['vin_decode_data']['Grok'] = grok_data
                sources.append('grok-supplemental')
        except RuntimeError:
            vehicle['vin_decode_data']['Grok'] = {'estimates': {}}
            warnings.append(
                'Grok estimates are temporarily unavailable; NHTSA and EPA data are unchanged.'
            )

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
