"""Static vehicle field catalog for admin typeahead suggestions.

Values are merged at runtime with distinct values already stored in inventory
so previously entered free-text options keep appearing.
"""

from __future__ import annotations

from typing import Iterable

# Popular US-market makes (used inventory focus).
MAKES = [
    'Acura', 'Alfa Romeo', 'Aston Martin', 'Audi', 'Bentley', 'BMW', 'Buick',
    'Cadillac', 'Chevrolet', 'Chrysler', 'Dodge', 'Ferrari', 'Fiat', 'Ford',
    'Genesis', 'GMC', 'Honda', 'Hyundai', 'Infiniti', 'Jaguar', 'Jeep', 'Kia',
    'Lamborghini', 'Land Rover', 'Lexus', 'Lincoln', 'Maserati', 'Mazda',
    'McLaren', 'Mercedes-Benz', 'Mini', 'Mitsubishi', 'Nissan', 'Polestar',
    'Porsche', 'Ram', 'Rivian', 'Rolls-Royce', 'Subaru', 'Tesla', 'Toyota',
    'Volkswagen', 'Volvo', 'Vinfast',
]

# Make -> common models. Keys must match MAKES casing where possible.
MAKE_MODELS = {
    'Acura': ['ILX', 'Integra', 'MDX', 'NSX', 'RDX', 'RLX', 'TLX', 'TSX'],
    'Audi': [
        'A3', 'A4', 'A5', 'A6', 'A7', 'A8', 'e-tron', 'Q3', 'Q5', 'Q7', 'Q8',
        'RS5', 'S4', 'S5', 'TT',
    ],
    'BMW': [
        '2 Series', '3 Series', '4 Series', '5 Series', '7 Series', 'X1', 'X2',
        'X3', 'X4', 'X5', 'X6', 'X7', 'Z4', 'i3', 'i4', 'iX',
    ],
    'Buick': ['Enclave', 'Encore', 'Encore GX', 'Envision', 'LaCrosse', 'Regal'],
    'Cadillac': [
        'CT4', 'CT5', 'CT6', 'Escalade', 'Escalade ESV', 'XT4', 'XT5', 'XT6',
    ],
    'Chevrolet': [
        'Blazer', 'Bolt EUV', 'Bolt EV', 'Camaro', 'Colorado', 'Corvette',
        'Cruze', 'Equinox', 'Express', 'Impala', 'Malibu', 'Silverado 1500',
        'Silverado 2500HD', 'Sonic', 'Spark', 'Suburban', 'Tahoe', 'Trailblazer',
        'Traverse', 'Trax',
    ],
    'Chrysler': ['300', 'Pacifica', 'Voyager'],
    'Dodge': [
        'Challenger', 'Charger', 'Durango', 'Grand Caravan', 'Hornet', 'Journey',
    ],
    'Ford': [
        'Bronco', 'Bronco Sport', 'Edge', 'Escape', 'Expedition', 'Explorer',
        'F-150', 'F-250', 'F-350', 'Fiesta', 'Flex', 'Focus', 'Fusion',
        'Maverick', 'Mustang', 'Mustang Mach-E', 'Ranger', 'Transit',
    ],
    'GMC': [
        'Acadia', 'Canyon', 'Sierra 1500', 'Sierra 2500HD', 'Terrain', 'Yukon',
        'Yukon XL',
    ],
    'Genesis': ['G70', 'G80', 'G90', 'GV60', 'GV70', 'GV80'],
    'Honda': [
        'Accord', 'Civic', 'CR-V', 'CR-Z', 'Element', 'Fit', 'HR-V', 'Insight',
        'Odyssey', 'Passport', 'Pilot', 'Ridgeline',
    ],
    'Hyundai': [
        'Accent', 'Elantra', 'Ioniq', 'Ioniq 5', 'Ioniq 6', 'Kona', 'Palisade',
        'Santa Cruz', 'Santa Fe', 'Sonata', 'Tucson', 'Venue', 'Veloster',
    ],
    'Infiniti': ['Q50', 'Q60', 'QX50', 'QX55', 'QX60', 'QX80'],
    'Jeep': [
        'Cherokee', 'Compass', 'Gladiator', 'Grand Cherokee', 'Grand Cherokee L',
        'Patriot', 'Renegade', 'Wrangler', 'Wrangler Unlimited',
    ],
    'Kia': [
        'Carnival', 'EV6', 'Forte', 'K5', 'Niro', 'Optima', 'Rio', 'Seltos',
        'Sorento', 'Soul', 'Sportage', 'Stinger', 'Telluride',
    ],
    'Land Rover': [
        'Defender', 'Discovery', 'Discovery Sport', 'Range Rover',
        'Range Rover Evoque', 'Range Rover Sport', 'Range Rover Velar',
    ],
    'Lexus': [
        'ES', 'GS', 'GX', 'IS', 'LC', 'LS', 'LX', 'NX', 'RC', 'RX', 'UX',
    ],
    'Lincoln': ['Aviator', 'Corsair', 'Nautilus', 'Navigator'],
    'Mazda': [
        'CX-3', 'CX-30', 'CX-5', 'CX-50', 'CX-9', 'CX-90', 'Mazda3', 'Mazda6',
        'MX-5 Miata',
    ],
    'Mercedes-Benz': [
        'A-Class', 'C-Class', 'CLA', 'CLS', 'E-Class', 'G-Class', 'GLA', 'GLB',
        'GLC', 'GLE', 'GLS', 'S-Class', 'SL',
    ],
    'Mini': ['Clubman', 'Convertible', 'Countryman', 'Hardtop 2 Door', 'Hardtop 4 Door'],
    'Mitsubishi': ['Eclipse Cross', 'Mirage', 'Outlander', 'Outlander Sport'],
    'Nissan': [
        'Altima', 'Armada', 'Frontier', 'Kicks', 'Leaf', 'Maxima', 'Murano',
        'Pathfinder', 'Rogue', 'Rogue Sport', 'Sentra', 'Titan', 'Versa',
        'Z',
    ],
    'Porsche': ['718 Boxster', '718 Cayman', '911', 'Cayenne', 'Macan', 'Panamera', 'Taycan'],
    'Ram': ['1500', '2500', '3500', 'ProMaster'],
    'Subaru': [
        'Ascent', 'BRZ', 'Crosstrek', 'Forester', 'Impreza', 'Legacy', 'Outback',
        'WRX',
    ],
    'Tesla': ['Model 3', 'Model S', 'Model X', 'Model Y', 'Cybertruck'],
    'Toyota': [
        '4Runner', '86', 'Avalon', 'C-HR', 'Camry', 'Corolla', 'Corolla Cross',
        'Crown', 'GR86', 'Highlander', 'Land Cruiser', 'Prius', 'RAV4',
        'Sequoia', 'Sienna', 'Supra', 'Tacoma', 'Tundra', 'Venza', 'Yaris',
    ],
    'Volkswagen': [
        'Arteon', 'Atlas', 'Atlas Cross Sport', 'Golf', 'Golf GTI', 'ID.4',
        'Jetta', 'Passat', 'Taos', 'Tiguan',
    ],
    'Volvo': ['C40', 'S60', 'S90', 'V60', 'V90', 'XC40', 'XC60', 'XC90'],
    'Vinfast': ['Fadil', 'Lux A2.0', 'Lux SA2.0', 'President', 'VF8', 'VF9'],
}

# Optional make+model -> common trims (lowercase keys for lookup).
MODEL_TRIMS = {
    ('honda', 'accord'): ['LX', 'Sport', 'EX', 'EX-L', 'Touring', 'Hybrid', 'Sport Hybrid'],
    ('honda', 'civic'): ['LX', 'Sport', 'EX', 'EX-L', 'Touring', 'Si', 'Type R'],
    ('honda', 'cr-v'): ['LX', 'EX', 'EX-L', 'Touring', 'Hybrid Sport', 'Hybrid EX-L', 'Hybrid Touring'],
    ('honda', 'pilot'): ['LX', 'EX', 'EX-L', 'TrailSport', 'Touring', 'Elite', 'Black Edition'],
    ('toyota', 'camry'): ['LE', 'SE', 'XLE', 'XSE', 'TRD', 'Hybrid LE', 'Hybrid SE', 'Hybrid XLE'],
    ('toyota', 'corolla'): ['L', 'LE', 'SE', 'XLE', 'XSE', 'Hybrid LE', 'Hybrid SE'],
    ('toyota', 'rav4'): ['LE', 'XLE', 'XLE Premium', 'Adventure', 'TRD Off-Road', 'Limited', 'Prime SE', 'Prime XSE'],
    ('toyota', 'tacoma'): ['SR', 'SR5', 'TRD Sport', 'TRD Off-Road', 'Limited', 'TRD Pro'],
    ('toyota', 'tundra'): ['SR', 'SR5', 'Limited', 'Platinum', '1794 Edition', 'TRD Pro', 'Capstone'],
    ('toyota', 'highlander'): ['L', 'LE', 'XLE', 'XSE', 'Limited', 'Platinum', 'Hybrid LE', 'Hybrid XLE'],
    ('toyota', '4runner'): ['SR5', 'TRD Off-Road', 'TRD Off-Road Premium', 'Limited', 'TRD Pro', 'Nightshade'],
    ('nissan', 'altima'): ['S', '2.5 S', 'SV', '2.5 SV', 'SR', '2.5 SR', 'SL', 'Platinum'],
    ('nissan', 'rogue'): ['S', 'SV', 'SL', 'Platinum', 'Rock Creek'],
    ('nissan', 'sentra'): ['S', 'SV', 'SR'],
    ('ford', 'f-150'): [
        'XL', 'XLT', 'Lariat', 'King Ranch', 'Platinum', 'Limited', 'Tremor', 'Raptor',
    ],
    ('ford', 'escape'): ['S', 'SE', 'SEL', 'Titanium', 'ST-Line', 'Plug-In Hybrid'],
    ('ford', 'explorer'): ['Base', 'XLT', 'Limited', 'ST', 'Platinum', 'Timberline'],
    ('ford', 'mustang'): ['EcoBoost', 'EcoBoost Premium', 'GT', 'GT Premium', 'Mach 1', 'Shelby GT350', 'Shelby GT500'],
    ('chevrolet', 'silverado 1500'): [
        'WT', 'Custom', 'LT', 'RST', 'LTZ', 'Premier', 'High Country', 'ZR2', 'Trail Boss',
    ],
    ('chevrolet', 'equinox'): ['L', 'LS', 'LT', 'RS', 'Premier'],
    ('chevrolet', 'malibu'): ['L', 'LS', 'RS', 'LT', 'Premier'],
    ('gmc', 'sierra 1500'): ['Pro', 'SLE', 'Elevation', 'SLT', 'AT4', 'Denali', 'Denali Ultimate'],
    ('jeep', 'wrangler'): ['Sport', 'Sport S', 'Willys', 'Sahara', 'Rubicon', 'High Altitude', '4xe'],
    ('jeep', 'grand cherokee'): ['Laredo', 'Altitude', 'Limited', 'Trailhawk', 'Overland', 'Summit', '4xe'],
    ('subaru', 'outback'): ['Base', 'Premium', 'Onyx Edition', 'Limited', 'Touring', 'Wilderness'],
    ('subaru', 'forester'): ['Base', 'Premium', 'Sport', 'Limited', 'Touring', 'Wilderness'],
    ('subaru', 'crosstrek'): ['Base', 'Premium', 'Sport', 'Limited', 'Wilderness'],
    ('hyundai', 'tucson'): ['SE', 'SEL', 'N Line', 'Limited', 'Hybrid Blue', 'Hybrid SEL', 'Hybrid Limited'],
    ('hyundai', 'elantra'): ['SE', 'SEL', 'N Line', 'Limited', 'Hybrid Blue', 'Hybrid Limited', 'N'],
    ('hyundai', 'santa fe'): ['SE', 'SEL', 'XRT', 'Limited', 'Calligraphy', 'Hybrid SEL', 'Hybrid Limited'],
    ('kia', 'sportage'): ['LX', 'EX', 'SX', 'X-Line', 'X-Pro', 'Hybrid LX', 'Hybrid EX', 'Hybrid SX'],
    ('kia', 'sorento'): ['LX', 'S', 'EX', 'SX', 'X-Line', 'X-Pro', 'Hybrid S', 'Hybrid EX', 'Hybrid SX'],
    ('kia', 'telluride'): ['LX', 'S', 'EX', 'SX', 'SX Prestige', 'X-Line', 'X-Pro'],
    ('mazda', 'cx-5'): ['2.5 S', '2.5 S Select', '2.5 S Preferred', '2.5 S Carbon Edition', '2.5 Turbo', '2.5 Turbo Signature'],
    ('mazda', 'cx-50'): ['2.5 S', '2.5 S Select', '2.5 S Preferred', '2.5 Turbo', '2.5 Turbo Premium'],
    ('bmw', '3 series'): ['330i', '330i xDrive', 'M340i', 'M340i xDrive', '330e', 'M3'],
    ('bmw', 'x3'): ['sDrive30i', 'xDrive30i', 'M40i', 'X3 M'],
    ('bmw', 'x5'): ['sDrive40i', 'xDrive40i', 'xDrive45e', 'M50i', 'X5 M'],
    ('mercedes-benz', 'c-class'): ['C 300', 'C 300 4MATIC', 'AMG C 43', 'AMG C 63'],
    ('mercedes-benz', 'e-class'): ['E 350', 'E 350 4MATIC', 'E 450', 'AMG E 53', 'AMG E 63 S'],
    ('lexus', 'rx'): ['350', '350 F Sport', '350h', '450h', '500h F Sport Performance'],
    ('lexus', 'es'): ['250', '300h', '350', '350 F Sport'],
    ('tesla', 'model 3'): ['RWD', 'Long Range', 'Performance'],
    ('tesla', 'model y'): ['RWD', 'Long Range', 'Performance'],
    ('volkswagen', 'jetta'): ['S', 'Sport', 'SE', 'SEL', 'GLI'],
    ('volkswagen', 'tiguan'): ['S', 'SE', 'SE R-Line Black', 'SEL', 'SEL R-Line'],
    ('ram', '1500'): ['Tradesman', 'Big Horn', 'Laramie', 'Rebel', 'Limited', 'Longhorn', 'TRX'],
    ('vinfast', 'vf8'): ['Eco', 'Plus', 'Pro', 'Max'],
}

BODY_STYLES = [
    'Sedan', 'SUV', 'Truck', 'Coupe', 'Hatchback', 'Wagon', 'Van', 'Minivan',
    'Convertible', 'Crossover', 'Chassis Cab',
]

TRANSMISSIONS = [
    'Automatic', 'Manual', 'CVT', 'Dual-Clutch',
    '6-Speed Automatic', '8-Speed Automatic', '9-Speed Automatic',
    '10-Speed Automatic', '6-Speed Manual', '7-Speed Dual-Clutch',
]

DRIVETRAINS = ['FWD', 'RWD', 'AWD', '4WD']

FUEL_TYPES = [
    'Gasoline', 'Diesel', 'Hybrid', 'Plug-in Hybrid', 'Electric', 'Flex Fuel',
    'Hydrogen',
]

ENGINES = [
    '1.5L I3', '1.5L I4 Turbo', '1.6L I4', '1.8L I4', '2.0L I4', '2.0L I4 Turbo',
    '2.4L I4', '2.5L I4', '2.5L I4 Turbo', '2.7L V6 Turbo', '3.0L I6 Turbo',
    '3.5L V6', '3.6L V6', '5.0L V8', '5.3L V8', '5.7L V8', '6.2L V8',
    'Electric Motor', 'Hybrid',
]

EXTERIOR_COLORS = [
    'Black', 'White', 'Silver', 'Gray', 'Charcoal', 'Gun Metallic', 'Blue',
    'Dark Blue', 'Red', 'Burgundy', 'Green', 'Brown', 'Beige', 'Gold', 'Orange',
    'Yellow', 'Pearl White', 'Super Black', 'Magnetic Gray', 'Oxford White',
    'Shadow Gray', 'Crystal Black', 'Alpine White', 'Glacier White',
]

INTERIOR_COLORS = [
    'Black', 'Charcoal', 'Gray', 'Beige', 'Tan', 'Brown', 'Ivory', 'Red',
    'White', 'Two-Tone',
]

COMMON_FEATURES = [
    'Backup Camera', 'Blind Spot Monitor', 'Bluetooth', 'Apple CarPlay',
    'Android Auto', 'Navigation', 'Leather Seats', 'Heated Seats',
    'Cooled Seats', 'Heated Steering Wheel', 'Sunroof', 'Panoramic Roof',
    'Power Liftgate', 'Remote Start', 'Keyless Entry', 'Push Button Start',
    'Cruise Control', 'Adaptive Cruise Control', 'Lane Keep Assist',
    'Forward Collision Warning', 'Automatic Emergency Braking',
    'Parking Sensors', '360 Camera', 'Alloy Wheels', 'Tow Package',
    'Third Row Seating', 'Power Seats', 'Memory Seats', 'Premium Audio',
    'Satellite Radio', 'USB Ports', 'Wireless Charging', 'LED Headlights',
    'Fog Lights', 'Roof Rails', 'Running Boards', 'Bed Liner',
]


def _norm(value: str | None) -> str:
    return (value or '').strip().lower()


def _unique_preserve(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _filter_prefix(values: Iterable[str], query: str = '', limit: int = 25) -> list[str]:
    items = _unique_preserve(values)
    q = _norm(query)
    if not q:
        return items[:limit]

    starts = [v for v in items if v.lower().startswith(q)]
    contains = [v for v in items if q in v.lower() and v not in starts]
    return (starts + contains)[:limit]


def models_for_make(make: str | None) -> list[str]:
    if not make:
        return []
    key = make.strip()
    if key in MAKE_MODELS:
        return list(MAKE_MODELS[key])
    # Case-insensitive fallback
    for catalog_make, models in MAKE_MODELS.items():
        if catalog_make.lower() == key.lower():
            return list(models)
    return []


def trims_for_make_model(make: str | None, model: str | None) -> list[str]:
    if not make or not model:
        return []
    key = (_norm(make), _norm(model))
    return list(MODEL_TRIMS.get(key, []))


def build_vehicle_catalog(db_values: dict[str, list[str]] | None = None) -> dict:
    """Merge static catalog with distinct DB values for admin suggestions."""
    db_values = db_values or {}

    makes = _unique_preserve(list(MAKES) + db_values.get('make', []))
    makes.sort(key=str.lower)

    # make -> models map including DB pairs
    make_models: dict[str, list[str]] = {
        m: list(models) for m, models in MAKE_MODELS.items()
    }
    # Normalize DB make casing onto catalog keys when possible
    catalog_make_lookup = {m.lower(): m for m in make_models}
    for make in makes:
        catalog_key = catalog_make_lookup.get(make.lower(), make)
        make_models.setdefault(catalog_key, [])

    for make, model in db_values.get('make_model_pairs', []):
        if not make or not model:
            continue
        catalog_key = catalog_make_lookup.get(str(make).strip().lower(), str(make).strip())
        make_models.setdefault(catalog_key, [])
        existing = {m.lower() for m in make_models[catalog_key]}
        model_text = str(model).strip()
        if model_text and model_text.lower() not in existing:
            make_models[catalog_key].append(model_text)

    for key in list(make_models.keys()):
        make_models[key] = sorted(_unique_preserve(make_models[key]), key=str.lower)

    # model trims: serialize keys as "make|model"
    model_trims: dict[str, list[str]] = {
        f'{make}|{model}': list(trims)
        for (make, model), trims in MODEL_TRIMS.items()
    }
    for make, model, trim in db_values.get('make_model_trim_triples', []):
        if not make or not model or not trim:
            continue
        key = f'{_norm(make)}|{_norm(model)}'
        model_trims.setdefault(key, [])
        existing = {t.lower() for t in model_trims[key]}
        trim_text = str(trim).strip()
        if trim_text and trim_text.lower() not in existing:
            model_trims[key].append(trim_text)
    for key in list(model_trims.keys()):
        model_trims[key] = sorted(_unique_preserve(model_trims[key]), key=str.lower)

    def merge_list(static: list[str], db_key: str) -> list[str]:
        return sorted(
            _unique_preserve(list(static) + db_values.get(db_key, [])),
            key=str.lower,
        )

    return {
        'makes': makes,
        'make_models': make_models,
        'model_trims': model_trims,
        'body_styles': merge_list(BODY_STYLES, 'body_style'),
        'transmissions': merge_list(TRANSMISSIONS, 'transmission'),
        'drivetrains': merge_list(DRIVETRAINS, 'drivetrain'),
        'fuel_types': merge_list(FUEL_TYPES, 'fuel_type'),
        'engines': merge_list(ENGINES, 'engine'),
        'exterior_colors': merge_list(EXTERIOR_COLORS, 'exterior_color'),
        'interior_colors': merge_list(INTERIOR_COLORS, 'interior_color'),
        'features': merge_list(COMMON_FEATURES, 'features'),
    }


def suggest_field(
    catalog: dict,
    field: str,
    query: str = '',
    make: str = '',
    model: str = '',
    limit: int = 25,
) -> list[str]:
    """Return filtered suggestions for a single field."""
    field = (field or '').strip().lower()
    limit = max(1, min(int(limit or 25), 50))

    if field == 'make':
        return _filter_prefix(catalog.get('makes', []), query, limit)

    if field == 'model':
        values = []
        if make:
            # Prefer exact catalog key casing
            make_models = catalog.get('make_models', {})
            values = make_models.get(make) or []
            if not values:
                for mk, models in make_models.items():
                    if mk.lower() == make.lower():
                        values = models
                        break
        if not values:
            # Flatten all models if make unknown
            for models in catalog.get('make_models', {}).values():
                values.extend(models)
            values.extend(catalog.get('makes', []))  # no-op safety
        return _filter_prefix(values, query, limit)

    if field == 'trim':
        values = []
        if make and model:
            key = f'{_norm(make)}|{_norm(model)}'
            values = list(catalog.get('model_trims', {}).get(key, []))
        if not values:
            # Fall back to all known trims for the make, then global
            if make:
                prefix = f'{_norm(make)}|'
                for k, trims in catalog.get('model_trims', {}).items():
                    if k.startswith(prefix):
                        values.extend(trims)
            if not values:
                for trims in catalog.get('model_trims', {}).values():
                    values.extend(trims)
        return _filter_prefix(values, query, limit)

    mapping = {
        'body_style': 'body_styles',
        'transmission': 'transmissions',
        'drivetrain': 'drivetrains',
        'fuel_type': 'fuel_types',
        'engine': 'engines',
        'exterior_color': 'exterior_colors',
        'interior_color': 'interior_colors',
        'features': 'features',
    }
    catalog_key = mapping.get(field)
    if not catalog_key:
        return []
    return _filter_prefix(catalog.get(catalog_key, []), query, limit)
