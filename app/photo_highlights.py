"""
Lightweight local photo highlight analysis for vehicle listing images.

Carvana-style hotspots: clickable bubbles for features (CarPlay, leather,
new tires) and imperfections (scratches, dings). Uses OpenCV classical CV
plus vehicle feature text — no heavyweight ML runtime required.

Optional: if ultralytics YOLO is installed and PHOTO_HIGHLIGHTS_USE_YOLO=1,
a nano detector can refine object boxes. Default path stays OpenCV-only.
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = 1

# Normalized feature keywords → display metadata
FEATURE_CATALOG = {
    'apple carplay': {
        'label': 'Apple CarPlay',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Apple CarPlay smartphone integration is listed on this vehicle.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'android auto': {
        'label': 'Android Auto',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Android Auto smartphone integration is listed on this vehicle.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'carplay': {
        'label': 'Apple CarPlay',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'phone',
        'description': 'Apple CarPlay is available in this cabin.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'leather seats': {
        'label': 'Leather Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'stars',
        'description': 'Leather seating surfaces are listed for this vehicle.',
        'scenes': ('interior_cabin',),
    },
    'leather': {
        'label': 'Leather Interior',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'stars',
        'description': 'Leather interior trim is listed on this vehicle.',
        'scenes': ('interior_cabin',),
    },
    'heated seats': {
        'label': 'Heated Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'thermometer-half',
        'description': 'Heated seats are listed on this vehicle.',
        'scenes': ('interior_cabin',),
    },
    'ventilated seats': {
        'label': 'Ventilated Seats',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'wind',
        'description': 'Ventilated seats are listed on this vehicle.',
        'scenes': ('interior_cabin',),
    },
    'sunroof': {
        'label': 'Sunroof',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A sunroof / moonroof is listed on this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear', 'interior_cabin'),
    },
    'moonroof': {
        'label': 'Moonroof',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A moonroof is listed on this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_cabin'),
    },
    'backup camera': {
        'label': 'Backup Camera',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'camera-video',
        'description': 'Rear backup camera is listed on this vehicle.',
        'scenes': ('interior_dash', 'exterior_rear'),
    },
    'rearview camera': {
        'label': 'Backup Camera',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'camera-video',
        'description': 'Rearview camera support is listed on this vehicle.',
        'scenes': ('interior_dash', 'exterior_rear'),
    },
    'blind spot': {
        'label': 'Blind Spot Monitor',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'eye',
        'description': 'Blind-spot monitoring is listed on this vehicle.',
        'scenes': ('exterior_side', 'interior_dash'),
    },
    'lane keep': {
        'label': 'Lane Keep Assist',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'signpost-split',
        'description': 'Lane keep assist is listed on this vehicle.',
        'scenes': ('interior_dash',),
    },
    'adaptive cruise': {
        'label': 'Adaptive Cruise',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer2',
        'description': 'Adaptive cruise control is listed on this vehicle.',
        'scenes': ('interior_dash',),
    },
    'navigation': {
        'label': 'Navigation',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'geo-alt',
        'description': 'Built-in navigation is listed on this vehicle.',
        'scenes': ('interior_dash',),
    },
    'bluetooth': {
        'label': 'Bluetooth',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'bluetooth',
        'description': 'Bluetooth connectivity is listed on this vehicle.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
    'remote start': {
        'label': 'Remote Start',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'Remote start is listed on this vehicle.',
        'scenes': ('exterior_front', 'exterior_side', 'interior_dash'),
    },
    'tow package': {
        'label': 'Tow Package',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Trailer tow package equipment is listed on this vehicle.',
        'scenes': ('exterior_rear',),
    },
    'trailer tow': {
        'label': 'Tow Package',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Trailer tow package equipment is listed on this vehicle.',
        'scenes': ('exterior_rear',),
    },
    'running boards': {
        'label': 'Running Boards',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'distribute-vertical',
        'description': 'Running boards / side steps are listed on this vehicle.',
        'scenes': ('exterior_side',),
    },
    'alloy wheels': {
        'label': 'Alloy Wheels',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Alloy wheels are listed on this vehicle.',
        'scenes': ('exterior_side', 'wheel_closeup', 'exterior_front', 'exterior_rear'),
    },
    'awd': {
        'label': 'All-Wheel Drive',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'All-wheel drive is listed on this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    '4wd': {
        'label': 'Four-Wheel Drive',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'shuffle',
        'description': 'Four-wheel drive is listed on this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear'),
    },
    'panoramic': {
        'label': 'Panoramic Roof',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'brightness-high',
        'description': 'A panoramic roof is listed on this vehicle.',
        'scenes': ('exterior_side', 'interior_cabin'),
    },
    'premium audio': {
        'label': 'Premium Audio',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'music-note-beamed',
        'description': 'Premium audio is listed on this vehicle.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'third row': {
        'label': 'Third-Row Seating',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'people',
        'description': 'Third-row seating is listed on this vehicle.',
        'scenes': ('interior_cabin',),
    },
    'power liftgate': {
        'label': 'Power Liftgate',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'door-open',
        'description': 'Power liftgate is listed on this vehicle.',
        'scenes': ('exterior_rear',),
    },
    'keyless': {
        'label': 'Keyless Entry',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'key',
        'description': 'Keyless entry / push-button start is listed on this vehicle.',
        'scenes': ('exterior_side', 'interior_dash'),
    },
    'push button': {
        'label': 'Push-Button Start',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'power',
        'description': 'Push-button start is listed on this vehicle.',
        'scenes': ('interior_dash', 'interior_cabin'),
    },
}

IMPERFECTION_META = {
    'scratch': {
        'label': 'Scratch',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Possible surface scratch detected in this area. Inspect in person for severity.',
    },
    'scuff': {
        'label': 'Scuff Mark',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Possible scuff or rub mark detected. Often cosmetic and polishable.',
    },
    'ding': {
        'label': 'Ding / Dent',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-circle',
        'description': 'Possible ding or small dent detected. Review photos and inspect on-site.',
    },
    'chip': {
        'label': 'Paint Chip',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Possible paint chip or stone nick detected in this panel area.',
    },
    'wear': {
        'label': 'Wear Spot',
        'category': 'imperfection',
        'severity': 'info',
        'icon': 'info-circle',
        'description': 'Area of higher wear or texture variation — common on high-touch surfaces.',
    },
    'tire_good': {
        'label': 'Tires Look Solid',
        'category': 'detail',
        'severity': 'positive',
        'icon': 'check-circle',
        'description': 'Wheel/tire area appears even with healthy-looking tread from this angle.',
    },
    'tire_worn': {
        'label': 'Check Tire Wear',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Tire tread may be worn from this angle. Confirm remaining life in person.',
    },
    'alloy_wheel': {
        'label': 'Alloy Wheel',
        'category': 'detail',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Alloy / styled wheel visible in this photo.',
    },
    'infotainment': {
        'label': 'Infotainment Screen',
        'category': 'detail',
        'severity': 'info',
        'icon': 'display',
        'description': 'Center stack / infotainment display area highlighted.',
    },
    'leather_surface': {
        'label': 'Upholstery Detail',
        'category': 'detail',
        'severity': 'info',
        'icon': 'stars',
        'description': 'Seat / upholstery surface highlighted for closer inspection.',
    },
}


@dataclass
class HighlightCandidate:
    x_pct: float
    y_pct: float
    label: str
    category: str = 'detail'  # feature | imperfection | detail
    description: str = ''
    icon: str = 'info-circle'
    severity: str = 'info'  # positive | info | caution | issue
    confidence: float = 0.5
    source: str = 'auto'
    order_index: int = 0
    meta: dict = field(default_factory=dict)

    def clamped(self) -> 'HighlightCandidate':
        self.x_pct = round(min(92.0, max(8.0, float(self.x_pct))), 2)
        self.y_pct = round(min(92.0, max(8.0, float(self.y_pct))), 2)
        self.confidence = round(min(1.0, max(0.0, float(self.confidence))), 3)
        return self

    def to_dict(self) -> dict:
        self.clamped()
        data = asdict(self)
        data.pop('meta', None)
        return data


def _import_cv2():
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        return cv2, np
    except ImportError as exc:
        raise RuntimeError(
            'OpenCV is required for photo highlights. Install with: '
            'pip install opencv-python-headless numpy'
        ) from exc


def parse_feature_tokens(features_text: Optional[str]) -> List[str]:
    if not features_text:
        return []
    raw = re.split(r'[,;\n|/]+', features_text)
    return [t.strip().lower() for t in raw if t and t.strip()]


def match_feature_catalog(features_text: Optional[str]) -> List[Tuple[str, dict]]:
    tokens = parse_feature_tokens(features_text)
    blob = ' | '.join(tokens)
    matched = []
    seen_labels = set()
    # Longer keys first for specificity
    for key in sorted(FEATURE_CATALOG.keys(), key=len, reverse=True):
        if key in blob or any(key in t for t in tokens):
            meta = FEATURE_CATALOG[key]
            if meta['label'] in seen_labels:
                continue
            seen_labels.add(meta['label'])
            matched.append((key, meta))
    return matched


def _resize_for_analysis(cv2, np, bgr, max_side: int = 960):
    h, w = bgr.shape[:2]
    scale = 1.0
    if max(h, w) > max_side:
        scale = max_side / float(max(h, w))
        bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return bgr, scale


def classify_scene(cv2, np, bgr) -> str:
    """Heuristic scene type for placing feature bubbles sensibly."""
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    # Interior often darker overall with warm/brown seat tones and fewer sky pixels
    top = gray[: max(1, h // 3), :]
    bottom = gray[int(h * 0.65):, :]
    top_mean = float(np.mean(top))
    bottom_mean = float(np.mean(bottom))
    overall = float(np.mean(gray))

    # Sky-ish blue in upper third
    upper_hsv = hsv[: max(1, h // 3), :]
    sky_mask = (
        (upper_hsv[:, :, 0] >= 90) & (upper_hsv[:, :, 0] <= 130) &
        (upper_hsv[:, :, 1] >= 40) & (upper_hsv[:, :, 2] >= 120)
    )
    sky_ratio = float(np.mean(sky_mask)) if sky_mask.size else 0.0

    # Brown/black seat-like pixels
    seat_mask = (
        ((hsv[:, :, 0] <= 25) | (hsv[:, :, 0] >= 160)) &
        (hsv[:, :, 1] >= 20) & (hsv[:, :, 1] <= 180) &
        (hsv[:, :, 2] >= 25) & (hsv[:, :, 2] <= 160)
    )
    seat_ratio = float(np.mean(seat_mask))

    # Bright rectangular screen-like regions (center)
    center = gray[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)]
    bright_center = float(np.mean(center > 180)) if center.size else 0.0

    # Wheel-ish dark circles in lower half
    circles = _detect_wheels(cv2, np, bgr)
    wheel_count = len(circles)

    if wheel_count >= 1 and seat_ratio < 0.18 and (sky_ratio > 0.04 or overall > 90):
        # Close-up wheel if one large circle dominates
        if wheel_count == 1:
            x, y, r = circles[0]
            if r > min(h, w) * 0.14 and y > h * 0.35:
                return 'wheel_closeup'
        if sky_ratio > 0.08 and top_mean > bottom_mean:
            # Front/rear often more grille/lights symmetry
            edges = cv2.Canny(gray, 60, 140)
            left = float(np.mean(edges[:, : w // 2]))
            right = float(np.mean(edges[:, w // 2:]))
            symmetry = 1.0 - min(1.0, abs(left - right) / (max(left, right, 1e-3)))
            if symmetry > 0.72 and wheel_count >= 2:
                return 'exterior_front' if bright_center < 0.12 else 'exterior_rear'
            return 'exterior_side'
        return 'exterior_side'

    if seat_ratio > 0.16 and sky_ratio < 0.05:
        if bright_center > 0.08:
            return 'interior_dash'
        return 'interior_cabin'

    if sky_ratio > 0.06:
        return 'exterior_side' if wheel_count else 'exterior_front'

    if bright_center > 0.1 and overall < 120:
        return 'interior_dash'

    return 'other'


def _detect_wheels(cv2, np, bgr) -> List[Tuple[int, int, int]]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (9, 9), 1.5)
    min_r = max(12, int(min(h, w) * 0.05))
    max_r = max(min_r + 4, int(min(h, w) * 0.28))
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=int(min(h, w) * 0.18),
        param1=120,
        param2=36,
        minRadius=min_r,
        maxRadius=max_r,
    )
    found = []
    if circles is not None:
        for c in np.round(circles[0]).astype(int):
            x, y, r = int(c[0]), int(c[1]), int(c[2])
            # Prefer lower half of frame for road wheels
            if y < h * 0.28:
                continue
            # Reject very bright "circles" (headlights)
            roi = gray[max(0, y - r): min(h, y + r), max(0, x - r): min(w, x + r)]
            if roi.size and float(np.mean(roi)) > 190:
                continue
            found.append((x, y, r))
    found.sort(key=lambda t: t[2], reverse=True)
    return found[:4]


def _wheel_highlights(cv2, np, bgr, circles) -> List[HighlightCandidate]:
    out: List[HighlightCandidate] = []
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    for idx, (x, y, r) in enumerate(circles[:2]):
        # Sample annular region approximating tread
        yy, xx = np.ogrid[:h, :w]
        dist = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
        ring = (dist >= r * 0.62) & (dist <= r * 0.95)
        if not np.any(ring):
            continue
        ring_vals = gray[ring]
        contrast = float(np.std(ring_vals))
        mean_v = float(np.mean(ring_vals))
        # Higher contrast on tread grooves often = more remaining pattern
        if contrast >= 28 and mean_v < 140:
            meta = IMPERFECTION_META['tire_good']
            conf = min(0.86, 0.55 + contrast / 120.0)
        elif contrast < 16:
            meta = IMPERFECTION_META['tire_worn']
            conf = 0.52
        else:
            meta = IMPERFECTION_META['alloy_wheel']
            conf = 0.6
        out.append(HighlightCandidate(
            x_pct=(x / w) * 100.0,
            y_pct=(y / h) * 100.0,
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=conf,
            order_index=10 + idx,
            meta={'kind': 'wheel', 'radius_px': r},
        ))
    return out


def _detect_screen_region(cv2, np, bgr) -> Optional[Tuple[float, float, float]]:
    """Return (x_pct, y_pct, confidence) for a likely infotainment screen."""
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Focus on center stack band
    y0, y1 = int(h * 0.18), int(h * 0.72)
    x0, x1 = int(w * 0.22), int(w * 0.78)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    blur = cv2.GaussianBlur(roi, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Screens are often bright-ish rectangles; also try inverse
    candidates = []
    for mat in (th, cv2.bitwise_not(th)):
        contours, _ = cv2.findContours(mat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < (roi.shape[0] * roi.shape[1] * 0.02):
                continue
            if area > (roi.shape[0] * roi.shape[1] * 0.45):
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = bw / float(bh or 1)
            if aspect < 1.15 or aspect > 2.8:
                continue
            fill = area / float(bw * bh or 1)
            if fill < 0.45:
                continue
            cx = x0 + x + bw / 2.0
            cy = y0 + y + bh / 2.0
            conf = min(0.9, 0.45 + fill * 0.4 + (0.1 if 1.4 <= aspect <= 2.2 else 0.0))
            candidates.append((cx, cy, conf, area))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[2], t[3]), reverse=True)
    cx, cy, conf, _ = candidates[0]
    return (cx / w) * 100.0, (cy / h) * 100.0, conf


def _detect_seat_regions(cv2, np, bgr) -> List[Tuple[float, float, float]]:
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    # Common interior upholstery colors
    masks = []
    # Blacks / dark grays
    masks.append(cv2.inRange(hsv, (0, 0, 15), (180, 80, 90)))
    # Browns / tans
    masks.append(cv2.inRange(hsv, (5, 40, 40), (25, 200, 180)))
    # Grays
    masks.append(cv2.inRange(hsv, (0, 0, 70), (180, 40, 170)))
    mask = masks[0]
    for m in masks[1:]:
        mask = cv2.bitwise_or(mask, m)
    # Prefer lower-middle cabin
    mask[: int(h * 0.2), :] = 0
    mask[int(h * 0.92):, :] = 0
    mask = cv2.medianBlur(mask, 9)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    min_area = h * w * 0.035
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bh < h * 0.12 or bw < w * 0.1:
            continue
        cx = x + bw / 2.0
        cy = y + bh * 0.45
        conf = min(0.8, 0.4 + area / float(h * w))
        regions.append(((cx / w) * 100.0, (cy / h) * 100.0, conf))
    regions.sort(key=lambda t: t[2], reverse=True)
    return regions[:3]


def _detect_imperfections(cv2, np, bgr, scene: str) -> List[HighlightCandidate]:
    """Find scratch/scuff/ding-like local anomalies on body-colored panels."""
    if scene.startswith('interior'):
        return _detect_interior_wear(cv2, np, bgr)

    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    # Suppress sky / ground extremes
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 50, 130)

    # Long thin edge structures ≈ scratches
    horizontal = cv2.morphologyEx(
        edges,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (17, 1)),
    )
    vertical = cv2.morphologyEx(
        edges,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, 17)),
    )
    thin = cv2.bitwise_or(horizontal, vertical)

    # Local contrast blobs ≈ dings / chips
    blur = cv2.GaussianBlur(l, (21, 21), 0)
    highpass = cv2.absdiff(l, blur)
    _, spots = cv2.threshold(highpass, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    spots = cv2.morphologyEx(spots, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # Ignore lower 12% (ground) and upper 10% (sky/roof glare often)
    thin[: int(h * 0.1), :] = 0
    thin[int(h * 0.9):, :] = 0
    spots[: int(h * 0.1), :] = 0
    spots[int(h * 0.9):, :] = 0

    # Ignore wheel circles
    for (cx, cy, r) in _detect_wheels(cv2, np, bgr):
        cv2.circle(thin, (cx, cy), int(r * 1.15), 0, -1)
        cv2.circle(spots, (cx, cy), int(r * 1.15), 0, -1)

    out: List[HighlightCandidate] = []

    def _collect(mask, kind: str, min_area: int, max_items: int):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        scored = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = max(bw, bh) / float(min(bw, bh) or 1)
            cx = x + bw / 2.0
            cy = y + bh / 2.0
            # Prefer mid-body panels
            if cy < h * 0.12 or cy > h * 0.88:
                continue
            score = area * (1.15 if aspect > 3.5 else 1.0)
            scored.append((score, cx, cy, aspect, area))
        scored.sort(key=lambda t: t[0], reverse=True)
        for score, cx, cy, aspect, area in scored[:max_items]:
            meta = IMPERFECTION_META[kind]
            if kind == 'scratch' and aspect < 2.8:
                meta = IMPERFECTION_META['scuff']
            if kind == 'ding' and area < 40:
                meta = IMPERFECTION_META['chip']
            conf = min(0.78, 0.42 + math.log1p(area) / 12.0)
            out.append(HighlightCandidate(
                x_pct=(cx / w) * 100.0,
                y_pct=(cy / h) * 100.0,
                label=meta['label'],
                category=meta['category'],
                description=meta['description'],
                icon=meta['icon'],
                severity=meta['severity'],
                confidence=conf,
                order_index=40,
                meta={'kind': kind, 'area': float(area)},
            ))

    _collect(thin, 'scratch', min_area=max(18, int(h * w * 0.00008)), max_items=3)
    _collect(spots, 'ding', min_area=max(12, int(h * w * 0.00005)), max_items=3)
    return out


def _detect_interior_wear(cv2, np, bgr) -> List[HighlightCandidate]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (15, 15), 0)
    hp = cv2.absdiff(gray, blur)
    _, mask = cv2.threshold(hp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask[: int(h * 0.15), :] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    scored = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < h * w * 0.0002:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        scored.append((area, x + bw / 2.0, y + bh / 2.0))
    scored.sort(reverse=True)
    meta = IMPERFECTION_META['wear']
    for area, cx, cy in scored[:2]:
        out.append(HighlightCandidate(
            x_pct=(cx / w) * 100.0,
            y_pct=(cy / h) * 100.0,
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=min(0.7, 0.4 + area / float(h * w) * 8),
            order_index=45,
        ))
    return out


def _feature_placements(scene: str, matched_features, screen_pt, seat_pts, wheels, w, h) -> List[HighlightCandidate]:
    out: List[HighlightCandidate] = []
    used_points: List[Tuple[float, float]] = []

    def take_point(preferred: Sequence[Tuple[float, float]], fallback: Tuple[float, float]) -> Tuple[float, float]:
        for p in preferred:
            if all(math.hypot(p[0] - u[0], p[1] - u[1]) > 10 for u in used_points):
                used_points.append(p)
                return p
        # Jitter fallback so bubbles don't stack
        fx, fy = fallback
        for _ in range(8):
            jx = min(90, max(10, fx + (len(used_points) * 7) % 21 - 10))
            jy = min(90, max(10, fy + (len(used_points) * 5) % 17 - 8))
            if all(math.hypot(jx - u[0], jy - u[1]) > 8 for u in used_points):
                used_points.append((jx, jy))
                return jx, jy
        used_points.append(fallback)
        return fallback

    seat_pref = [(p[0], p[1]) for p in seat_pts]
    screen_pref = [(screen_pt[0], screen_pt[1])] if screen_pt else []
    wheel_pref = [((x / w) * 100.0, (y / h) * 100.0) for x, y, r in wheels]

    scene_defaults = {
        'interior_dash': (50.0, 42.0),
        'interior_cabin': (48.0, 55.0),
        'exterior_side': (55.0, 48.0),
        'exterior_front': (50.0, 45.0),
        'exterior_rear': (50.0, 48.0),
        'wheel_closeup': (50.0, 55.0),
        'other': (50.0, 50.0),
    }

    for idx, (key, meta) in enumerate(matched_features):
        scenes = meta.get('scenes') or ()
        if scenes and scene not in scenes and scene != 'other':
            # Still allow high-value tech features on dash-ish interiors
            if not (scene.startswith('interior') and key in (
                'apple carplay', 'android auto', 'carplay', 'navigation', 'bluetooth', 'backup camera'
            )):
                continue

        preferred = []
        label_l = meta['label'].lower()
        if any(s in label_l for s in ('carplay', 'android', 'navigation', 'bluetooth', 'infotainment', 'camera', 'cruise', 'lane')):
            preferred.extend(screen_pref)
            preferred.append((50.0, 40.0))
        elif 'seat' in label_l or 'leather' in label_l or 'audio' in label_l or 'third' in label_l:
            preferred.extend(seat_pref)
            preferred.append((45.0, 58.0))
        elif 'tire' in label_l or 'wheel' in label_l or 'awd' in label_l or '4wd' in label_l or 'running' in label_l:
            preferred.extend(wheel_pref)
            preferred.append((25.0, 70.0))
        elif 'tow' in label_l or 'liftgate' in label_l:
            preferred.append((50.0, 60.0))
        elif 'sunroof' in label_l or 'moonroof' in label_l or 'panoramic' in label_l:
            preferred.append((50.0, 18.0 if scene.startswith('exterior') else 22.0))
        else:
            preferred.append(scene_defaults.get(scene, (50.0, 50.0)))

        pt = take_point(preferred, scene_defaults.get(scene, (50.0, 50.0)))
        out.append(HighlightCandidate(
            x_pct=pt[0],
            y_pct=pt[1],
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta.get('icon', 'stars'),
            severity=meta.get('severity', 'positive'),
            confidence=0.72 if scene in (meta.get('scenes') or ()) else 0.55,
            order_index=idx,
            meta={'feature_key': key},
        ))
    return out


def _nms_highlights(items: List[HighlightCandidate], min_dist_pct: float = 9.0, max_items: int = 8) -> List[HighlightCandidate]:
    # Prefer features, then higher confidence
    severity_rank = {'positive': 3, 'info': 2, 'caution': 2, 'issue': 1}
    category_rank = {'feature': 3, 'detail': 2, 'imperfection': 2}

    def sort_key(h: HighlightCandidate):
        return (
            category_rank.get(h.category, 0),
            severity_rank.get(h.severity, 0),
            h.confidence,
        )

    ordered = sorted((h.clamped() for h in items), key=sort_key, reverse=True)
    kept: List[HighlightCandidate] = []
    for h in ordered:
        if any(math.hypot(h.x_pct - k.x_pct, h.y_pct - k.y_pct) < min_dist_pct for k in kept):
            # If both imperfections nearly same point, keep higher conf only
            continue
        kept.append(h)
        if len(kept) >= max_items:
            break
    # Stable display order: top-to-bottom-ish then features first
    kept.sort(key=lambda h: (0 if h.category == 'feature' else 1, h.y_pct, h.x_pct))
    for i, h in enumerate(kept):
        h.order_index = i
    return kept


def _optional_yolo_boxes(image_path: str) -> List[Tuple[str, float, float, float]]:
    """Optional YOLO nano detections → (label, x_pct, y_pct, conf)."""
    if os.environ.get('PHOTO_HIGHLIGHTS_USE_YOLO', '').lower() not in ('1', 'true', 'yes', 'on'):
        return []
    try:
        from ultralytics import YOLO  # type: ignore
    except Exception:
        logger.info('YOLO requested but ultralytics is not installed; skipping.')
        return []

    model_name = os.environ.get('PHOTO_HIGHLIGHTS_YOLO_MODEL', 'yolov8n.pt')
    try:
        model = YOLO(model_name)
        results = model.predict(source=image_path, verbose=False, conf=0.35)
    except Exception as exc:
        logger.warning('YOLO inference failed: %s', exc)
        return []

    # COCO-ish labels we care about
    interesting = {
        'car', 'truck', 'bus', 'motorcycle', 'bicycle', 'person',
        'tv', 'laptop', 'cell phone', 'keyboard', 'remote', 'clock',
    }
    out = []
    for result in results or []:
        names = result.names or {}
        boxes = getattr(result, 'boxes', None)
        if boxes is None:
            continue
        wh = result.orig_shape  # h, w
        ih, iw = float(wh[0]), float(wh[1])
        for box in boxes:
            try:
                cls_id = int(box.cls.item())
                conf = float(box.conf.item())
                label = str(names.get(cls_id, '')).lower()
                if label not in interesting:
                    continue
                xyxy = box.xyxy.tolist()[0]
                cx = ((xyxy[0] + xyxy[2]) / 2.0) / iw * 100.0
                cy = ((xyxy[1] + xyxy[3]) / 2.0) / ih * 100.0
                out.append((label, cx, cy, conf))
            except Exception:
                continue
    return out[:5]


def analyze_vehicle_image(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 8,
) -> dict:
    """
    Analyze a vehicle photo and return highlight candidates.

    Returns:
        {
          'scene': str,
          'highlights': [HighlightCandidate.to_dict(), ...],
          'analysis_version': int,
          'engine': str,
        }
    """
    cv2, np = _import_cv2()
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f'Image not found: {image_path}')

    bgr = cv2.imread(image_path)
    if bgr is None:
        raise ValueError(f'Could not decode image: {image_path}')

    bgr, _scale = _resize_for_analysis(cv2, np, bgr)
    h, w = bgr.shape[:2]
    scene = classify_scene(cv2, np, bgr)
    wheels = _detect_wheels(cv2, np, bgr)
    screen_pt = _detect_screen_region(cv2, np, bgr)
    seat_pts = _detect_seat_regions(cv2, np, bgr) if scene.startswith('interior') else []
    matched = match_feature_catalog(features_text)

    # Merge drivetrain from vehicle context into feature matching
    ctx = vehicle_context or {}
    extra_bits = []
    for key in ('drivetrain', 'transmission', 'body_style'):
        val = ctx.get(key)
        if val:
            extra_bits.append(str(val))
    if extra_bits:
        matched_extra = match_feature_catalog(','.join(extra_bits))
        seen = {m['label'] for _, m in matched}
        for item in matched_extra:
            if item[1]['label'] not in seen:
                matched.append(item)
                seen.add(item[1]['label'])

    candidates: List[HighlightCandidate] = []
    candidates.extend(_wheel_highlights(cv2, np, bgr, wheels))
    candidates.extend(_detect_imperfections(cv2, np, bgr, scene))
    candidates.extend(_feature_placements(scene, matched, screen_pt, seat_pts, wheels, w, h))

    if screen_pt and scene.startswith('interior'):
        # Always mark the screen as a detail if present and not already covered
        meta = IMPERFECTION_META['infotainment']
        candidates.append(HighlightCandidate(
            x_pct=screen_pt[0],
            y_pct=screen_pt[1],
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=float(screen_pt[2]),
            order_index=5,
        ))

    for sx, sy, conf in seat_pts[:1]:
        if any(m[1]['label'].lower().find('leather') >= 0 for m in matched):
            continue
        meta = IMPERFECTION_META['leather_surface']
        candidates.append(HighlightCandidate(
            x_pct=sx,
            y_pct=sy,
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=conf * 0.85,
            order_index=15,
        ))

    # Optional YOLO refinement
    engine = 'opencv'
    for label, x, y, conf in _optional_yolo_boxes(image_path):
        engine = 'opencv+yolo'
        pretty = label.replace('_', ' ').title()
        candidates.append(HighlightCandidate(
            x_pct=x,
            y_pct=y,
            label=pretty,
            category='detail',
            description=f'Detected {pretty.lower()} region in this photo.',
            icon='bullseye',
            severity='info',
            confidence=conf,
            order_index=30,
        ))

    final = _nms_highlights(candidates, max_items=max_highlights)
    return {
        'scene': scene,
        'highlights': [h.to_dict() for h in final],
        'analysis_version': ANALYSIS_VERSION,
        'engine': engine,
    }
