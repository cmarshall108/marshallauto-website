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
import logging
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
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.vehicle_catalog import COMMON_FEATURES, EXTERIOR_COLORS, INTERIOR_COLORS

logger = logging.getLogger(__name__)


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
NHTSA_SAFETY_BASE_URL = 'https://api.nhtsa.gov/SafetyRatings'
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
_SAFETY_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_SAFETY_CACHE_TTL_SECONDS = 3600
_SAFETY_CACHE_MAX = 256

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
    ('NHTSA 5-Star Safety Ratings', 'safety_ratings'),
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

_VIN_DECODE_OMIT_FIELDS = {
    'ErrorCode', 'ErrorText',
    'OverallStars', 'FrontStars', 'SideStars', 'RolloverStars',
    'FrontDriverStars', 'FrontPassengerStars', 'SideDriverStars',
    'SidePassengerStars', 'SidePoleStars',
    'CrashTests', 'SafetyTech', 'Summary', 'RolloverPercent', 'NoTip',
    'NhtsaVehicleUrl', 'NhtsaRatingsUrl', 'source',
    'FrontCrashPicture', 'SideCrashPicture', 'SidePolePicture',
    'FrontCrashVideo', 'SideCrashVideo', 'SidePoleVideo',
}

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
    if field.startswith('Safety '):
        bare = field[7:]
        labels = {
            'OverallRating': 'Overall Rating',
            'OverallFrontCrashRating': 'Frontal Crash Rating',
            'OverallSideCrashRating': 'Side Crash Rating',
            'RolloverRating': 'Rollover Rating',
            'VehicleDescription': 'Rated Configuration',
            'VehicleId': 'NHTSA Vehicle ID',
            'NHTSAElectronicStabilityControl': 'Electronic Stability Control',
            'NHTSAForwardCollisionWarning': 'Forward Collision Warning',
            'NHTSALaneDepartureWarning': 'Lane Departure Warning',
            'OverallStars': 'Overall Stars',
            'FrontStars': 'Frontal Stars',
            'SideStars': 'Side Stars',
            'RolloverStars': 'Rollover Stars',
        }
        if bare in labels:
            return labels[bare]
        return _humanize_vin_field(bare)
    if field in _VIN_DECODE_LABELS:
        return _VIN_DECODE_LABELS[field]
    words = re.findall(r'[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+', field)
    return ' '.join(
        word.upper() if word.casefold() == 'id'
        else word if word.isupper()
        else word[0].upper() + word[1:]
        for word in words
    )


_SAFETY_RATING_FIELDS = {
    'OverallRating', 'OverallFrontCrashRating', 'OverallSideCrashRating',
    'RolloverRating', 'FrontCrashDriversideRating', 'FrontCrashPassengersideRating',
    'SideCrashDriversideRating', 'SideCrashPassengersideRating', 'SidePoleCrashRating',
    'NHTSAElectronicStabilityControl', 'NHTSAForwardCollisionWarning',
    'NHTSALaneDepartureWarning', 'VehicleDescription', 'VehicleId',
    'ModelYear', 'Make', 'Model', 'dynamicTipResult', 'RolloverPossibility',
    'ComplaintsCount', 'RecallsCount', 'InvestigationCount',
}


def _vin_decode_group(field: str) -> str:
    if field.startswith('Safety '):
        return 'safety_ratings'
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
            if source == 'EPA':
                prefix = 'EPA '
            elif source == 'SafetyRatings':
                prefix = 'Safety '
            else:
                prefix = ''
            fields.extend(
                (f'{prefix}{field}', value)
                for field, value in source_data.items()
            )
        else:
            fields.append((source, source_data))

    for field, raw_value in fields:
        bare_field = str(field)
        if bare_field.startswith('EPA '):
            bare_field = bare_field[4:]
        elif bare_field.startswith('Safety '):
            bare_field = bare_field[7:]
        if field in _VIN_DECODE_OMIT_FIELDS or bare_field in _VIN_DECODE_OMIT_FIELDS or raw_value is None:
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


def _safety_cache_get(key: str) -> tuple[bool, dict[str, Any] | None]:
    entry = _SAFETY_CACHE.get(key)
    if not entry:
        return False, None
    ts, payload = entry
    if time.time() - ts > _SAFETY_CACHE_TTL_SECONDS:
        _SAFETY_CACHE.pop(key, None)
        return False, None
    return True, payload


def _safety_cache_set(key: str, payload: dict[str, Any] | None) -> None:
    if len(_SAFETY_CACHE) >= _SAFETY_CACHE_MAX:
        oldest = min(_SAFETY_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _SAFETY_CACHE.pop(oldest, None)
    _SAFETY_CACHE[key] = (time.time(), payload)


def parse_nhtsa_star_rating(value: Any) -> int | None:
    """Return 1-5 when NHTSA provides a star rating, otherwise None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in {
        'not rated', 'n/a', 'na', 'not available', 'unknown',
    }:
        return None
    try:
        stars = int(float(text))
    except (TypeError, ValueError):
        return None
    return stars if 1 <= stars <= 5 else None


def _safety_json(url: str, timeout: float = 8.0) -> dict[str, Any]:
    body = _http_get_bytes(url, timeout=timeout, accept='application/json')
    try:
        payload = json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError('NHTSA Safety Ratings returned invalid JSON') from exc
    if not isinstance(payload, dict):
        raise RuntimeError('NHTSA Safety Ratings returned unexpected payload')
    return payload


def _normalize_safety_token(value: str | None) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').casefold()).strip()


def _drivetrain_tokens(drivetrain: str | None) -> set[str]:
    text = _normalize_safety_token(drivetrain)
    tokens: set[str] = set()
    if not text:
        return tokens
    if 'awd' in text or 'all wheel' in text:
        tokens.add('awd')
    if '4wd' in text or '4x4' in text or 'four wheel' in text:
        tokens.add('4wd')
    if 'fwd' in text or 'front wheel' in text:
        tokens.add('fwd')
    if 'rwd' in text or 'rear wheel' in text:
        tokens.add('rwd')
    return tokens


def _body_tokens(body_style: str | None) -> set[str]:
    text = _normalize_safety_token(body_style)
    tokens: set[str] = set()
    if not text:
        return tokens
    mapping = {
        'suv': {'suv', 'sport utility'},
        'sedan': {'sedan', '4 dr', '4dr'},
        'coupe': {'coupe', '2 dr', '2dr'},
        'hatchback': {'hatchback', 'hatch'},
        'wagon': {'wagon'},
        'truck': {'truck', 'pickup', 'crew cab', 'extended cab', 'regular cab'},
        'van': {'van', 'minivan'},
        'convertible': {'convertible'},
    }
    for kind, needles in mapping.items():
        if any(needle in text for needle in needles) or kind in text:
            tokens.add(kind)
    return tokens


def _score_safety_model_name(candidate: str, model: str, fuel_type: str | None) -> int:
    cand = _normalize_safety_token(candidate)
    target = _normalize_safety_token(model)
    if not cand or not target:
        return -1
    score = 0
    if cand == target:
        score = 100
    elif cand.startswith(target) or target.startswith(cand):
        score = 80
    elif target in cand or cand in target:
        score = 60
    else:
        target_parts = set(target.split())
        cand_parts = set(cand.split())
        overlap = target_parts & cand_parts
        if not overlap:
            return -1
        score = 20 + (10 * len(overlap))
    fuel = _normalize_safety_token(fuel_type)
    hybrid_need = 'hybrid' in fuel or 'plug' in fuel
    hybrid_have = 'hybrid' in cand
    if hybrid_need and hybrid_have:
        score += 15
    elif hybrid_need and not hybrid_have:
        score -= 10
    elif hybrid_have and not hybrid_need:
        score -= 5
    return score


def _score_safety_variant(
    description: str,
    *,
    drivetrain: str | None,
    body_style: str | None,
) -> int:
    desc = _normalize_safety_token(description)
    score = 0
    drive_tokens = _drivetrain_tokens(drivetrain)
    if 'awd' in drive_tokens and 'awd' in desc:
        score += 20
    elif '4wd' in drive_tokens and ('4wd' in desc or '4x4' in desc):
        score += 20
    elif 'fwd' in drive_tokens and 'fwd' in desc:
        score += 20
    elif 'rwd' in drive_tokens and 'rwd' in desc:
        score += 20
    elif drive_tokens:
        # Prefer exact drivetrain matches; mild penalty when mismatched.
        if any(token in desc for token in ('awd', '4wd', 'fwd', 'rwd', '4x4')):
            score -= 5

    body_tokens = _body_tokens(body_style)
    for token in body_tokens:
        if token == 'suv' and 'suv' in desc:
            score += 12
        elif token == 'sedan' and ('sedan' in desc or '4 dr' in desc or '4dr' in desc):
            score += 12
        elif token == 'coupe' and ('coupe' in desc or '2 dr' in desc or '2dr' in desc):
            score += 12
        elif token == 'truck' and any(t in desc for t in ('truck', 'pickup', 'cab')):
            score += 12
        elif token == 'van' and 'van' in desc:
            score += 12
        elif token == 'hatchback' and 'hatch' in desc:
            score += 12
        elif token == 'wagon' and 'wagon' in desc:
            score += 12
        elif token == 'convertible' and 'convertible' in desc:
            score += 12
    return score


def _safety_models_for_make(year: int, make: str, timeout: float = 8.0) -> list[str]:
    url = (
        f'{NHTSA_SAFETY_BASE_URL}/modelyear/{int(year)}/make/'
        f'{urllib.parse.quote(make, safe="")}'
    )
    payload = _safety_json(url, timeout=timeout)
    models = []
    for row in payload.get('Results') or []:
        if not isinstance(row, dict):
            continue
        model = str(row.get('Model') or '').strip()
        if model:
            models.append(model)
    return models


def _safety_variants_for_model(
    year: int,
    make: str,
    model: str,
    timeout: float = 8.0,
) -> list[dict[str, Any]]:
    url = (
        f'{NHTSA_SAFETY_BASE_URL}/modelyear/{int(year)}/make/'
        f'{urllib.parse.quote(make, safe="")}/model/'
        f'{urllib.parse.quote(model, safe="")}'
    )
    payload = _safety_json(url, timeout=timeout)
    variants = []
    for row in payload.get('Results') or []:
        if not isinstance(row, dict):
            continue
        vehicle_id = row.get('VehicleId')
        try:
            vehicle_id = int(vehicle_id)
        except (TypeError, ValueError):
            continue
        if vehicle_id <= 0:
            continue
        variants.append({
            'VehicleId': vehicle_id,
            'VehicleDescription': str(row.get('VehicleDescription') or '').strip(),
            'Model': str(row.get('Model') or model).strip(),
        })
    return variants


def _safety_vehicle_detail(vehicle_id: int, timeout: float = 8.0) -> dict[str, Any] | None:
    url = f'{NHTSA_SAFETY_BASE_URL}/VehicleId/{int(vehicle_id)}'
    payload = _safety_json(url, timeout=timeout)
    results = payload.get('Results') or []
    if not results or not isinstance(results[0], dict):
        return None
    return dict(results[0])


def _safety_media_url(value: Any) -> str | None:
    url = str(value or '').strip()
    if not url:
        return None
    # NHTSA sometimes returns legacy WMV crash videos; keep for outbound links only.
    if url.startswith('//'):
        url = f'https:{url}'
    if url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    if not url.startswith('https://'):
        return None
    return url


def _safety_int(value: Any) -> int | None:
    try:
        if value is None or value == '':
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _safety_float(value: Any) -> float | None:
    try:
        if value is None or value == '':
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_safety_result(detail: dict[str, Any], *, matched_model: str) -> dict[str, Any]:
    """Keep a stable, display-friendly subset of the NHTSA Safety Ratings payload."""
    overall = str(detail.get('OverallRating') or '').strip()
    front = str(detail.get('OverallFrontCrashRating') or '').strip()
    side = str(detail.get('OverallSideCrashRating') or '').strip()
    rollover = str(detail.get('RolloverRating') or '').strip()
    front_driver = str(detail.get('FrontCrashDriversideRating') or '').strip()
    front_passenger = str(detail.get('FrontCrashPassengersideRating') or '').strip()
    side_driver = str(detail.get('SideCrashDriversideRating') or '').strip()
    side_passenger = str(detail.get('SideCrashPassengersideRating') or '').strip()
    side_pole = str(detail.get('SidePoleCrashRating') or '').strip()
    barrier_front = str(detail.get('combinedSideBarrierAndPoleRating-Front') or '').strip()
    barrier_rear = str(detail.get('combinedSideBarrierAndPoleRating-Rear') or '').strip()
    barrier_overall = str(detail.get('sideBarrierRating-Overall') or '').strip()
    result = {
        'VehicleId': detail.get('VehicleId'),
        'ModelYear': detail.get('ModelYear'),
        'Make': detail.get('Make'),
        'Model': detail.get('Model') or matched_model,
        'VehicleDescription': str(detail.get('VehicleDescription') or '').strip(),
        'OverallRating': overall,
        'OverallFrontCrashRating': front,
        'OverallSideCrashRating': side,
        'RolloverRating': rollover,
        'FrontCrashDriversideRating': front_driver,
        'FrontCrashPassengersideRating': front_passenger,
        'SideCrashDriversideRating': side_driver,
        'SideCrashPassengersideRating': side_passenger,
        'SidePoleCrashRating': side_pole,
        'SideBarrierRatingOverall': barrier_overall,
        'CombinedSideBarrierAndPoleRatingFront': barrier_front,
        'CombinedSideBarrierAndPoleRatingRear': barrier_rear,
        'RolloverPossibility': _safety_float(detail.get('RolloverPossibility')),
        'dynamicTipResult': str(detail.get('dynamicTipResult') or '').strip(),
        'NHTSAElectronicStabilityControl': str(
            detail.get('NHTSAElectronicStabilityControl') or ''
        ).strip(),
        'NHTSAForwardCollisionWarning': str(
            detail.get('NHTSAForwardCollisionWarning') or ''
        ).strip(),
        'NHTSALaneDepartureWarning': str(
            detail.get('NHTSALaneDepartureWarning') or ''
        ).strip(),
        'ComplaintsCount': _safety_int(detail.get('ComplaintsCount')),
        'RecallsCount': _safety_int(detail.get('RecallsCount')),
        'InvestigationCount': _safety_int(detail.get('InvestigationCount')),
        'FrontCrashPicture': _safety_media_url(detail.get('FrontCrashPicture')),
        'SideCrashPicture': _safety_media_url(detail.get('SideCrashPicture')),
        'SidePolePicture': _safety_media_url(detail.get('SidePolePicture')),
        'FrontCrashVideo': _safety_media_url(detail.get('FrontCrashVideo')),
        'SideCrashVideo': _safety_media_url(detail.get('SideCrashVideo')),
        'SidePoleVideo': _safety_media_url(detail.get('SidePoleVideo')),
        'OverallStars': parse_nhtsa_star_rating(overall),
        'FrontStars': parse_nhtsa_star_rating(front),
        'SideStars': parse_nhtsa_star_rating(side),
        'RolloverStars': parse_nhtsa_star_rating(rollover),
        'FrontDriverStars': parse_nhtsa_star_rating(front_driver),
        'FrontPassengerStars': parse_nhtsa_star_rating(front_passenger),
        'SideDriverStars': parse_nhtsa_star_rating(side_driver),
        'SidePassengerStars': parse_nhtsa_star_rating(side_passenger),
        'SidePoleStars': parse_nhtsa_star_rating(side_pole),
        'source': 'nhtsa-safety-ratings',
    }
    return result


def present_nhtsa_safety_ratings(ratings: dict[str, Any] | None) -> dict[str, Any] | None:
    """Enrich stored ratings for templates: crash media, meters, and summary copy."""
    if not isinstance(ratings, dict) or not ratings:
        return None
    data = dict(ratings)

    def stars_for(*keys: str) -> int | None:
        for key in keys:
            value = data.get(key)
            if isinstance(value, int) and 1 <= value <= 5:
                return value
            parsed = parse_nhtsa_star_rating(value)
            if parsed:
                return parsed
        return None

    data['OverallStars'] = stars_for('OverallStars', 'OverallRating')
    data['FrontStars'] = stars_for('FrontStars', 'OverallFrontCrashRating')
    data['SideStars'] = stars_for('SideStars', 'OverallSideCrashRating')
    data['RolloverStars'] = stars_for('RolloverStars', 'RolloverRating')
    data['FrontDriverStars'] = stars_for('FrontDriverStars', 'FrontCrashDriversideRating')
    data['FrontPassengerStars'] = stars_for(
        'FrontPassengerStars', 'FrontCrashPassengersideRating',
    )
    data['SideDriverStars'] = stars_for('SideDriverStars', 'SideCrashDriversideRating')
    data['SidePassengerStars'] = stars_for(
        'SidePassengerStars', 'SideCrashPassengersideRating',
    )
    data['SidePoleStars'] = stars_for('SidePoleStars', 'SidePoleCrashRating')

    possibility = _safety_float(data.get('RolloverPossibility'))
    data['RolloverPossibility'] = possibility
    data['RolloverPercent'] = (
        round(possibility * 100, 1) if possibility is not None else None
    )

    tip = str(data.get('dynamicTipResult') or '').strip()
    data['dynamicTipResult'] = tip
    data['NoTip'] = tip.casefold() in {'no tip', 'no-tip', 'did not tip'}

    crash_tests = []
    for key, label, overall_key, seats, picture_key, video_key in (
        (
            'frontal',
            'Frontal crash',
            'FrontStars',
            (
                ('Driver', 'FrontDriverStars', 'FrontCrashDriversideRating'),
                ('Passenger', 'FrontPassengerStars', 'FrontCrashPassengersideRating'),
            ),
            'FrontCrashPicture',
            'FrontCrashVideo',
        ),
        (
            'side',
            'Side crash',
            'SideStars',
            (
                ('Driver', 'SideDriverStars', 'SideCrashDriversideRating'),
                ('Passenger', 'SidePassengerStars', 'SideCrashPassengersideRating'),
            ),
            'SideCrashPicture',
            'SideCrashVideo',
        ),
        (
            'pole',
            'Side pole crash',
            'SidePoleStars',
            (),
            'SidePolePicture',
            'SidePoleVideo',
        ),
        (
            'rollover',
            'Rollover',
            'RolloverStars',
            (),
            None,
            None,
        ),
    ):
        stars = data.get(overall_key)
        raw_map = {
            'frontal': data.get('OverallFrontCrashRating'),
            'side': data.get('OverallSideCrashRating'),
            'pole': data.get('SidePoleCrashRating'),
            'rollover': data.get('RolloverRating'),
        }
        picture = _safety_media_url(data.get(picture_key)) if picture_key else None
        video = _safety_media_url(data.get(video_key)) if video_key else None
        seat_rows = []
        for seat_label, seat_stars_key, seat_raw_key in seats:
            seat_stars = data.get(seat_stars_key)
            seat_raw = str(data.get(seat_raw_key) or '').strip()
            if seat_stars or seat_raw:
                seat_rows.append({
                    'label': seat_label,
                    'stars': seat_stars,
                    'raw': seat_raw or None,
                })
        if not any((stars, raw_map.get(key), picture, seat_rows, key == 'rollover' and possibility is not None)):
            continue
        crash_tests.append({
            'key': key,
            'label': label,
            'stars': stars,
            'raw': raw_map.get(key) or None,
            'picture': picture,
            'video': video,
            'seats': seat_rows,
            'fill_percent': (stars or 0) * 20,
        })
    data['CrashTests'] = crash_tests

    techs = []
    for label, key, icon in (
        ('Electronic stability control', 'NHTSAElectronicStabilityControl', 'bi-shield-check'),
        ('Forward collision warning', 'NHTSAForwardCollisionWarning', 'bi-exclamation-triangle'),
        ('Lane departure warning', 'NHTSALaneDepartureWarning', 'bi-signpost-split'),
    ):
        value = str(data.get(key) or '').strip()
        if value:
            techs.append({'label': label, 'value': value, 'icon': icon})
    data['SafetyTech'] = techs

    year = data.get('ModelYear') or ''
    make = str(data.get('Make') or '').title()
    model = str(data.get('Model') or '').title()
    description = data.get('VehicleDescription') or f'{year} {make} {model}'.strip()
    overall_stars = data.get('OverallStars')
    if overall_stars:
        summary = (
            f'The {description} earned an overall NHTSA 5-Star Safety Rating of '
            f'{overall_stars} out of 5 in the New Car Assessment Program (NCAP).'
        )
    elif data.get('OverallRating'):
        summary = (
            f'NHTSA lists the {description} overall crash rating as '
            f'{data.get("OverallRating")}.'
        )
    else:
        summary = (
            f'NHTSA safety testing information is available for the {description} '
            f'configuration.'
        )
    parts = []
    if data.get('FrontStars'):
        parts.append(f'frontal crash {data["FrontStars"]}/5')
    if data.get('SideStars'):
        parts.append(f'side crash {data["SideStars"]}/5')
    if data.get('RolloverStars'):
        parts.append(f'rollover {data["RolloverStars"]}/5')
    if parts:
        summary += ' Category results: ' + ', '.join(parts) + '.'
    if data.get('RolloverPercent') is not None:
        summary += (
            f' NHTSA estimates a {data["RolloverPercent"]}% chance of rollover '
            f'in a single-vehicle crash'
        )
        if data.get('NoTip'):
            summary += ' and recorded no tip in the dynamic test'
        summary += '.'
    if techs:
        tech_bits = [f'{item["label"].lower()} {item["value"].lower()}' for item in techs]
        summary += ' Crash-avoidance equipment on the tested configuration: ' + '; '.join(tech_bits) + '.'
    summary += (
        ' Ratings apply to the NHTSA-tested configuration and may differ from this '
        'exact vehicle.'
    )
    data['Summary'] = summary

    vehicle_id = data.get('VehicleId')
    if vehicle_id:
        data['NhtsaVehicleUrl'] = (
            f'https://www.nhtsa.gov/vehicle/{urllib.parse.quote(str(vehicle_id), safe="")}'
        )
    else:
        data['NhtsaVehicleUrl'] = 'https://www.nhtsa.gov/ratings'
    data['NhtsaRatingsUrl'] = 'https://www.nhtsa.gov/ratings'
    return data


def lookup_nhtsa_safety_ratings(
    *,
    year: int | None,
    make: str | None,
    model: str | None,
    drivetrain: str | None = None,
    body_style: str | None = None,
    fuel_type: str | None = None,
    timeout: float = 8.0,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Fetch NHTSA NCAP 5-star ratings for a year/make/model configuration."""
    try:
        year_int = int(year) if year is not None else 0
    except (TypeError, ValueError):
        year_int = 0
    make_value = (make or '').strip()
    model_value = (model or '').strip()
    if year_int < 1990 or not make_value or not model_value:
        return None

    cache_key = '|'.join([
        str(year_int),
        make_value.casefold(),
        model_value.casefold(),
        (drivetrain or '').casefold(),
        (body_style or '').casefold(),
        (fuel_type or '').casefold(),
    ])
    if use_cache:
        hit, cached = _safety_cache_get(cache_key)
        if hit:
            return dict(cached) if cached else None

    try:
        variants = _safety_variants_for_model(
            year_int, make_value, model_value, timeout=timeout,
        )
        matched_model = model_value
        if not variants:
            model_candidates = _safety_models_for_make(year_int, make_value, timeout=timeout)
            ranked = sorted(
                (
                    (_score_safety_model_name(candidate, model_value, fuel_type), candidate)
                    for candidate in model_candidates
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            for score, candidate in ranked:
                if score < 20:
                    break
                variants = _safety_variants_for_model(
                    year_int, make_value, candidate, timeout=timeout,
                )
                if variants:
                    matched_model = candidate
                    break
        if not variants:
            if use_cache:
                _safety_cache_set(cache_key, None)
            return None

        ranked_variants = sorted(
            variants,
            key=lambda row: (
                _score_safety_variant(
                    row.get('VehicleDescription') or '',
                    drivetrain=drivetrain,
                    body_style=body_style,
                ),
                -(row.get('VehicleId') or 0),
            ),
            reverse=True,
        )
        detail = None
        chosen = None
        for row in ranked_variants:
            detail = _safety_vehicle_detail(int(row['VehicleId']), timeout=timeout)
            if detail:
                chosen = row
                break
        if not detail or not chosen:
            if use_cache:
                _safety_cache_set(cache_key, None)
            return None
        result = _normalize_safety_result(detail, matched_model=matched_model)
        if use_cache:
            _safety_cache_set(cache_key, result)
        return result
    except Exception:
        logger.debug(
            'NHTSA safety ratings lookup failed for %s %s %s',
            year_int, make_value, model_value, exc_info=True,
        )
        return None


def apply_nhtsa_safety_ratings_to_vehicle(vehicle, ratings: dict[str, Any] | None) -> bool:
    """Copy normalized SafetyRatings onto Vehicle columns. Returns True if changed."""
    if vehicle is None:
        return False
    changed = False

    def set_field(name: str, value: Any) -> None:
        nonlocal changed
        if getattr(vehicle, name, None) != value:
            setattr(vehicle, name, value)
            changed = True

    if not ratings:
        return False

    set_field('nhtsa_vehicle_id', ratings.get('VehicleId'))
    set_field('nhtsa_overall_rating', str(ratings.get('OverallRating') or '').strip() or None)
    set_field(
        'nhtsa_front_rating',
        str(ratings.get('OverallFrontCrashRating') or '').strip() or None,
    )
    set_field(
        'nhtsa_side_rating',
        str(ratings.get('OverallSideCrashRating') or '').strip() or None,
    )
    set_field(
        'nhtsa_rollover_rating',
        str(ratings.get('RolloverRating') or '').strip() or None,
    )
    set_field(
        'nhtsa_safety_description',
        str(ratings.get('VehicleDescription') or '').strip() or None,
    )
    set_field('nhtsa_safety_data', json.dumps(ratings, separators=(',', ':')))
    from app.models import utcnow as _utcnow
    set_field('nhtsa_safety_checked_at', _utcnow())
    return changed


def safety_ratings_from_vehicle(vehicle) -> dict[str, Any] | None:
    """Best available SafetyRatings dict for templates (column JSON or vin_decode_data)."""
    if vehicle is None:
        return None
    raw = getattr(vehicle, 'nhtsa_safety_data', None)
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return present_nhtsa_safety_ratings(data)
        except (TypeError, json.JSONDecodeError):
            pass
    try:
        decoded = json.loads(getattr(vehicle, 'vin_decode_data', None) or '')
    except (TypeError, json.JSONDecodeError):
        decoded = None
    if isinstance(decoded, dict):
        ratings = decoded.get('SafetyRatings')
        if isinstance(ratings, dict) and ratings:
            return present_nhtsa_safety_ratings(ratings)
    overall = getattr(vehicle, 'nhtsa_overall_rating', None)
    if overall:
        return present_nhtsa_safety_ratings({
            'OverallRating': overall,
            'OverallFrontCrashRating': getattr(vehicle, 'nhtsa_front_rating', None),
            'OverallSideCrashRating': getattr(vehicle, 'nhtsa_side_rating', None),
            'RolloverRating': getattr(vehicle, 'nhtsa_rollover_rating', None),
            'VehicleDescription': getattr(vehicle, 'nhtsa_safety_description', None),
            'VehicleId': getattr(vehicle, 'nhtsa_vehicle_id', None),
            'ModelYear': getattr(vehicle, 'year', None),
            'Make': getattr(vehicle, 'make', None),
            'Model': getattr(vehicle, 'model', None),
        })
    return None


def nhtsa_safety_seo_blurb(vehicle) -> str | None:
    """Short crawlable safety sentence for meta descriptions and JSON-LD."""
    ratings = safety_ratings_from_vehicle(vehicle)
    if not ratings:
        return None
    stars = ratings.get('OverallStars')
    description = ratings.get('VehicleDescription') or getattr(vehicle, 'title', 'this vehicle')
    if stars:
        return (
            f'NHTSA overall safety rating {stars}/5 for the {description} '
            f'crash-test configuration.'
        )
    if ratings.get('OverallRating'):
        return (
            f'NHTSA overall crash rating: {ratings["OverallRating"]} '
            f'({description}).'
        )
    return None


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

    candidates = ranked_models[:6]
    with ThreadPoolExecutor(max_workers=min(3, len(candidates))) as executor:
        futures = [
            executor.submit(
                _epa_menu_items, 'options',
                {'year': str(year), 'make': make, 'model': epa_model},
                timeout=timeout,
            )
            for _, epa_model in candidates
        ]
        for (model_score, epa_model), future in zip(candidates, futures):
            try:
                options = future.result()
            except RuntimeError as exc:
                logger.warning('EPA options lookup failed for %s: %s', epa_model, exc)
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

    # NHTSA NCAP 5-star safety ratings (best-effort; never fails the whole decode)
    safety = None
    try:
        safety = lookup_nhtsa_safety_ratings(
            year=vehicle.get('year'),
            make=vehicle.get('make'),
            model=vehicle.get('model'),
            drivetrain=vehicle.get('drivetrain'),
            body_style=vehicle.get('body_style'),
            fuel_type=vehicle.get('fuel_type'),
            use_cache=use_cache,
        )
    except Exception:
        safety = None
    if safety:
        vehicle['vin_decode_data']['SafetyRatings'] = safety
        vehicle['nhtsa_overall_rating'] = safety.get('OverallRating') or None
        vehicle['nhtsa_front_rating'] = safety.get('OverallFrontCrashRating') or None
        vehicle['nhtsa_side_rating'] = safety.get('OverallSideCrashRating') or None
        vehicle['nhtsa_rollover_rating'] = safety.get('RolloverRating') or None
        vehicle['nhtsa_vehicle_id'] = safety.get('VehicleId')
        vehicle['nhtsa_safety_description'] = safety.get('VehicleDescription') or None
        sources.append('nhtsa-safety')
    else:
        warnings.append('NHTSA 5-star safety ratings were not found for this configuration.')

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
