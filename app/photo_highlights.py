"""
Photo highlight analysis for vehicle listing images (Carvana-style hotspots).

Primary engine: Grok vision via xAI API (XAI_API_KEY) — accurate placement,
conservative condition notes, listing-feature awareness.

Fallback: OpenCV classical CV when no API key / network / parse failure.

Coordinates are always percent of the **full source image** (0–100).
Gallery JS maps them through object-fit cover/contain to the painted box.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import mimetypes
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

ANALYSIS_VERSION = 3

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
        'icon': 'signpost',
        'description': 'Lane keep assist is listed on this vehicle.',
        'scenes': ('interior_dash',),
    },
    'adaptive cruise': {
        'label': 'Adaptive Cruise',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speedometer',
        'description': 'Adaptive cruise control is listed on this vehicle.',
        'scenes': ('interior_dash',),
    },
    'navigation': {
        'label': 'Navigation',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'geo-alt',
        'description': 'Built-in navigation is listed on this vehicle.',
        'scenes': ('interior_dash', 'interior_cabin'),
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
        'description': 'Tow package equipment is listed on this vehicle.',
        'scenes': ('exterior_rear',),
    },
    'trailer tow': {
        'label': 'Trailer Tow',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'truck',
        'description': 'Trailer tow capability is listed on this vehicle.',
        'scenes': ('exterior_rear',),
    },
    'running boards': {
        'label': 'Running Boards',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'distribute-horizontal',
        'description': 'Running boards / side steps are listed on this vehicle.',
        'scenes': ('exterior_side',),
    },
    'alloy wheels': {
        'label': 'Alloy Wheels',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'circle',
        'description': 'Alloy wheels are listed on this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear', 'wheel_closeup'),
    },
    'new tires': {
        'label': 'New Tires',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'check-circle',
        'description': 'Newer / replaced tires are noted for this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'exterior_rear', 'wheel_closeup'),
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
        'scenes': ('exterior_side', 'exterior_front', 'interior_cabin'),
    },
    'premium audio': {
        'label': 'Premium Audio',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'speaker',
        'description': 'Premium audio is listed on this vehicle.',
        'scenes': ('interior_cabin', 'interior_dash'),
    },
    'third row': {
        'label': 'Third Row Seating',
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
        'description': 'Keyless entry is listed on this vehicle.',
        'scenes': ('exterior_side', 'exterior_front', 'interior_dash'),
    },
    'push button': {
        'label': 'Push-Button Start',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'power',
        'description': 'Push-button start is listed on this vehicle.',
        'scenes': ('interior_dash',),
    },
}

IMPERFECTION_META = {
    'scratch': {
        'label': 'Possible Scratch',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Possible scratch or thin mark on the body panel. Confirm in person.',
    },
    'scuff': {
        'label': 'Possible Scuff',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Possible scuff mark on the finish. Confirm in person.',
    },
    'ding': {
        'label': 'Possible Ding',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-circle',
        'description': 'Possible ding or small body imperfection. Confirm in person.',
    },
    'chip': {
        'label': 'Possible Chip',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-circle',
        'description': 'Possible paint chip. Confirm in person.',
    },
    'wear': {
        'label': 'Wear Spot',
        'category': 'imperfection',
        'severity': 'info',
        'icon': 'info-circle',
        'description': 'Area of higher wear or texture variation — common on high-touch surfaces.',
    },
    'tire_good': {
        'label': 'Tire Condition',
        'category': 'feature',
        'severity': 'positive',
        'icon': 'check-circle',
        'description': 'Tire tread pattern looks present from this angle. Confirm remaining life in person.',
    },
    'tire_worn': {
        'label': 'Check Tire Wear',
        'category': 'imperfection',
        'severity': 'caution',
        'icon': 'exclamation-triangle',
        'description': 'Tire tread may be worn from this angle. Confirm remaining life in person.',
    },
    'alloy_wheel': {
        'label': 'Wheel Detail',
        'category': 'detail',
        'severity': 'info',
        'icon': 'circle',
        'description': 'Wheel / rim detail highlighted for closer inspection.',
    },
    'infotainment': {
        'label': 'Infotainment Screen',
        'category': 'detail',
        'severity': 'info',
        'icon': 'display',
        'description': 'Center stack / infotainment display area highlighted.',
    },
    'leather_surface': {
        'label': 'Seat Surface',
        'category': 'detail',
        'severity': 'info',
        'icon': 'stars',
        'description': 'Seating surface highlighted for closer inspection.',
    },
}

ICON_ALLOWLIST = {
    'phone', 'stars', 'thermometer-half', 'wind', 'brightness-high', 'camera-video',
    'eye', 'signpost', 'speedometer', 'geo-alt', 'bluetooth', 'key', 'truck',
    'distribute-horizontal', 'circle', 'check-circle', 'shuffle', 'speaker',
    'people', 'door-open', 'power', 'exclamation-triangle', 'exclamation-circle',
    'info-circle', 'display', 'bullseye',
}

SCENE_VALUES = {
    'exterior_front', 'exterior_side', 'exterior_rear', 'wheel_closeup',
    'interior_dash', 'interior_cabin', 'other',
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
        self.x_pct = round(min(97.0, max(3.0, float(self.x_pct))), 2)
        self.y_pct = round(min(97.0, max(3.0, float(self.y_pct))), 2)
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
            'OpenCV is required for photo highlights fallback. Install with: '
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
    for key in sorted(FEATURE_CATALOG.keys(), key=len, reverse=True):
        hit = key in blob or any(key == t or key in t for t in tokens)
        if not hit:
            continue
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
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    top = gray[: max(1, h // 3), :]
    bottom = gray[int(h * 0.65):, :]
    top_mean = float(np.mean(top))
    bottom_mean = float(np.mean(bottom))
    overall = float(np.mean(gray))

    upper_hsv = hsv[: max(1, h // 3), :]
    sky_mask = (
        (upper_hsv[:, :, 0] >= 90) & (upper_hsv[:, :, 0] <= 130) &
        (upper_hsv[:, :, 1] >= 40) & (upper_hsv[:, :, 2] >= 120)
    )
    sky_ratio = float(np.mean(sky_mask)) if sky_mask.size else 0.0

    seat_mask = (
        ((hsv[:, :, 0] <= 25) | (hsv[:, :, 0] >= 160)) &
        (hsv[:, :, 1] >= 20) & (hsv[:, :, 1] <= 180) &
        (hsv[:, :, 2] >= 25) & (hsv[:, :, 2] <= 160)
    )
    seat_ratio = float(np.mean(seat_mask))

    center = gray[int(h * 0.25):int(h * 0.75), int(w * 0.25):int(w * 0.75)]
    bright_center = float(np.mean(center > 180)) if center.size else 0.0

    circles = _detect_wheels(cv2, np, bgr)
    wheel_count = len(circles)

    if wheel_count >= 1 and seat_ratio < 0.22 and (sky_ratio > 0.03 or overall > 85):
        if wheel_count == 1:
            x, y, r = circles[0]
            if r > min(h, w) * 0.16 and y > h * 0.35:
                return 'wheel_closeup'
        if sky_ratio > 0.06 and top_mean >= bottom_mean - 5:
            edges = cv2.Canny(gray, 60, 140)
            left = float(np.mean(edges[:, : w // 2]))
            right = float(np.mean(edges[:, w // 2:]))
            symmetry = 1.0 - min(1.0, abs(left - right) / (max(left, right, 1e-3)))
            if symmetry > 0.75 and wheel_count >= 2:
                return 'exterior_front' if bright_center < 0.15 else 'exterior_rear'
            return 'exterior_side'
        return 'exterior_side'

    if seat_ratio > 0.20 and sky_ratio < 0.04:
        if bright_center > 0.10:
            return 'interior_dash'
        return 'interior_cabin'

    if sky_ratio > 0.05:
        return 'exterior_side' if wheel_count else 'exterior_front'

    if bright_center > 0.12 and overall < 115:
        return 'interior_dash'

    return 'other'


def _detect_wheels(cv2, np, bgr) -> List[Tuple[int, int, int]]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (9, 9), 1.5)
    min_r = max(14, int(min(h, w) * 0.055))
    max_r = max(min_r + 4, int(min(h, w) * 0.26))
    circles = cv2.HoughCircles(
        blur,
        cv2.HOUGH_GRADIENT,
        dp=1.25,
        minDist=int(min(h, w) * 0.22),
        param1=130,
        param2=42,
        minRadius=min_r,
        maxRadius=max_r,
    )
    found = []
    if circles is not None:
        for c in np.round(circles[0]).astype(int):
            x, y, r = int(c[0]), int(c[1]), int(c[2])
            if y < h * 0.32 or y > h * 0.95:
                continue
            roi = gray[max(0, y - r): min(h, y + r), max(0, x - r): min(w, x + r)]
            if roi.size and float(np.mean(roi)) > 175:
                continue
            if roi.size and float(np.std(roi)) < 12:
                continue
            found.append((x, y, r))
    found.sort(key=lambda t: t[2], reverse=True)
    return found[:3]


def _wheel_highlights(cv2, np, bgr, circles) -> List[HighlightCandidate]:
    out: List[HighlightCandidate] = []
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    for idx, (x, y, r) in enumerate(circles[:2]):
        yy, xx = np.ogrid[:h, :w]
        dist = np.sqrt((xx - x) ** 2 + (yy - y) ** 2)
        ring = (dist >= r * 0.62) & (dist <= r * 0.95)
        if not np.any(ring):
            continue
        ring_vals = gray[ring]
        contrast = float(np.std(ring_vals))
        mean_v = float(np.mean(ring_vals))
        if contrast >= 34 and mean_v < 135:
            meta = IMPERFECTION_META['tire_good']
            conf = min(0.86, 0.58 + contrast / 140.0)
        elif contrast < 12 and mean_v > 40:
            meta = IMPERFECTION_META['tire_worn']
            conf = 0.6
        else:
            continue
        if conf < 0.58:
            continue
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
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    y0, y1 = int(h * 0.18), int(h * 0.70)
    x0, x1 = int(w * 0.28), int(w * 0.72)
    roi = gray[y0:y1, x0:x1]
    if roi.size == 0:
        return None
    blur = cv2.GaussianBlur(roi, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    candidates = []
    for mat in (th, cv2.bitwise_not(th)):
        contours, _ = cv2.findContours(mat, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            roi_area = float(roi.shape[0] * roi.shape[1] or 1)
            if area < roi_area * 0.035 or area > roi_area * 0.40:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = bw / float(bh or 1)
            if aspect < 1.2 or aspect > 2.6:
                continue
            fill = area / float(bw * bh or 1)
            if fill < 0.55:
                continue
            patch = roi[y:y + bh, x:x + bw]
            if patch.size and float(np.mean(patch)) < 90:
                continue
            cx = x0 + x + bw / 2.0
            cy = y0 + y + bh / 2.0
            conf = min(0.9, 0.5 + fill * 0.35 + (0.1 if 1.4 <= aspect <= 2.2 else 0.0))
            candidates.append((cx, cy, conf, area))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[2], t[3]), reverse=True)
    cx, cy, conf, _ = candidates[0]
    if conf < 0.58:
        return None
    return (cx / w) * 100.0, (cy / h) * 100.0, conf


def _detect_seat_regions(cv2, np, bgr) -> List[Tuple[float, float, float]]:
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    masks = [
        cv2.inRange(hsv, (0, 0, 15), (180, 80, 85)),
        cv2.inRange(hsv, (5, 45, 40), (25, 200, 170)),
        cv2.inRange(hsv, (0, 0, 70), (180, 35, 160)),
    ]
    mask = masks[0]
    for m in masks[1:]:
        mask = cv2.bitwise_or(mask, m)
    mask[: int(h * 0.22), :] = 0
    mask[int(h * 0.90):, :] = 0
    mask = cv2.medianBlur(mask, 9)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    min_area = h * w * 0.05
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bh < h * 0.14 or bw < w * 0.12:
            continue
        cx = x + bw / 2.0
        cy = y + bh * 0.45
        conf = min(0.8, 0.45 + area / float(h * w))
        regions.append(((cx / w) * 100.0, (cy / h) * 100.0, conf))
    regions.sort(key=lambda t: t[2], reverse=True)
    return regions[:2]


def _detect_imperfections(cv2, np, bgr, scene: str) -> List[HighlightCandidate]:
    if scene.startswith('interior'):
        return _detect_interior_wear(cv2, np, bgr)

    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    l, _a, _b = cv2.split(lab)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 70, 160)

    horizontal = cv2.morphologyEx(
        edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1)),
    )
    vertical = cv2.morphologyEx(
        edges, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 21)),
    )
    thin = cv2.bitwise_or(horizontal, vertical)

    blur = cv2.GaussianBlur(l, (31, 31), 0)
    highpass = cv2.absdiff(l, blur)
    thr = max(18, int(float(np.mean(highpass)) + 2.4 * float(np.std(highpass))))
    _, spots = cv2.threshold(highpass, thr, 255, cv2.THRESH_BINARY)
    spots = cv2.morphologyEx(spots, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=2)

    thin[: int(h * 0.18), :] = 0
    thin[int(h * 0.88):, :] = 0
    thin[:, : int(w * 0.08)] = 0
    thin[:, int(w * 0.92):] = 0
    spots[: int(h * 0.18), :] = 0
    spots[int(h * 0.88):, :] = 0

    for (cx, cy, r) in _detect_wheels(cv2, np, bgr):
        cv2.circle(thin, (cx, cy), int(r * 1.25), 0, -1)
        cv2.circle(spots, (cx, cy), int(r * 1.25), 0, -1)

    out: List[HighlightCandidate] = []

    def _collect(mask, kind: str, min_area: int, max_items: int):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        scored = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > h * w * 0.02:
                continue
            x, y, bw, bh = cv2.boundingRect(cnt)
            aspect = max(bw, bh) / float(min(bw, bh) or 1)
            cx = x + bw / 2.0
            cy = y + bh / 2.0
            if cy < h * 0.15 or cy > h * 0.86:
                continue
            score = area * (1.2 if aspect > 4.0 else 1.0)
            scored.append((score, cx, cy, aspect, area))
        scored.sort(key=lambda t: t[0], reverse=True)
        for score, cx, cy, aspect, area in scored[:max_items]:
            meta = IMPERFECTION_META[kind]
            if kind == 'scratch' and aspect < 3.2:
                meta = IMPERFECTION_META['scuff']
            if kind == 'ding' and area < 55:
                meta = IMPERFECTION_META['chip']
            conf = min(0.72, 0.48 + math.log1p(area) / 14.0)
            if conf < 0.55:
                continue
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

    _collect(thin, 'scratch', min_area=max(40, int(h * w * 0.00025)), max_items=1)
    _collect(spots, 'ding', min_area=max(28, int(h * w * 0.00018)), max_items=1)
    return out


def _detect_interior_wear(cv2, np, bgr) -> List[HighlightCandidate]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (21, 21), 0)
    hp = cv2.absdiff(gray, blur)
    thr = max(22, int(float(np.mean(hp)) + 2.5 * float(np.std(hp))))
    _, mask = cv2.threshold(hp, thr, 255, cv2.THRESH_BINARY)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=2)
    mask[: int(h * 0.18), :] = 0
    mask[int(h * 0.92):, :] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    scored = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < h * w * 0.0012 or area > h * w * 0.08:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        scored.append((area, x + bw / 2.0, y + bh / 2.0))
    scored.sort(reverse=True)
    meta = IMPERFECTION_META['wear']
    for area, cx, cy in scored[:1]:
        conf = min(0.68, 0.48 + area / float(h * w) * 6)
        if conf < 0.55:
            continue
        out.append(HighlightCandidate(
            x_pct=(cx / w) * 100.0,
            y_pct=(cy / h) * 100.0,
            label=meta['label'],
            category=meta['category'],
            description=meta['description'],
            icon=meta['icon'],
            severity=meta['severity'],
            confidence=conf,
            order_index=45,
        ))
    return out


def _feature_placements(scene: str, matched_features, screen_pt, seat_pts, wheels, w, h) -> List[HighlightCandidate]:
    """Place listing features only when a visual anchor exists for that scene."""
    out: List[HighlightCandidate] = []
    used_points: List[Tuple[float, float]] = []

    def accept(pt: Tuple[float, float]) -> bool:
        if any(math.hypot(pt[0] - u[0], pt[1] - u[1]) < 12 for u in used_points):
            return False
        used_points.append(pt)
        return True

    seat_pref = [(p[0], p[1]) for p in seat_pts]
    screen_pref = [(screen_pt[0], screen_pt[1])] if screen_pt else []
    wheel_pref = [((x / w) * 100.0, (y / h) * 100.0) for x, y, r in wheels]

    for idx, (key, meta) in enumerate(matched_features):
        scenes = meta.get('scenes') or ()
        if scenes and scene not in scenes:
            continue

        preferred: List[Tuple[float, float]] = []
        label_l = meta['label'].lower()
        if any(s in label_l for s in (
            'carplay', 'android', 'navigation', 'bluetooth', 'infotainment',
            'camera', 'cruise', 'lane', 'push-button',
        )):
            preferred.extend(screen_pref)
        elif 'seat' in label_l or 'leather' in label_l or 'audio' in label_l or 'third' in label_l:
            preferred.extend(seat_pref)
        elif 'tire' in label_l or 'wheel' in label_l or 'awd' in label_l or '4wd' in label_l or 'running' in label_l:
            preferred.extend(wheel_pref)
        elif 'tow' in label_l or 'liftgate' in label_l:
            preferred.append((50.0, 60.0))
        elif 'sunroof' in label_l or 'moonroof' in label_l or 'panoramic' in label_l:
            preferred.append((50.0, 18.0 if scene.startswith('exterior') else 22.0))
        else:
            if scene.startswith('exterior') and wheel_pref:
                preferred.extend(wheel_pref[:1])
            elif screen_pref:
                preferred.extend(screen_pref)

        # Require a real anchor — no random jitter
        if not preferred:
            continue

        placed = False
        for p in preferred:
            if accept(p):
                out.append(HighlightCandidate(
                    x_pct=p[0],
                    y_pct=p[1],
                    label=meta['label'],
                    category=meta['category'],
                    description=meta['description'],
                    icon=meta.get('icon', 'stars'),
                    severity=meta.get('severity', 'positive'),
                    confidence=0.72 if scene in scenes else 0.58,
                    order_index=idx,
                    meta={'feature_key': key},
                ))
                placed = True
                break
        if not placed:
            continue
    return out


def _nms_highlights(
    items: List[HighlightCandidate],
    min_dist_pct: float = 12.0,
    max_items: int = 5,
    min_confidence: float = 0.55,
) -> List[HighlightCandidate]:
    severity_rank = {'positive': 3, 'info': 2, 'caution': 2, 'issue': 1}
    category_rank = {'feature': 3, 'detail': 1, 'imperfection': 2}

    def sort_key(h: HighlightCandidate):
        return (
            category_rank.get(h.category, 0),
            severity_rank.get(h.severity, 0),
            h.confidence,
        )

    filtered = [h.clamped() for h in items if h.confidence >= min_confidence]
    ordered = sorted(filtered, key=sort_key, reverse=True)
    kept: List[HighlightCandidate] = []
    for h in ordered:
        if any(math.hypot(h.x_pct - k.x_pct, h.y_pct - k.y_pct) < min_dist_pct for k in kept):
            continue
        if any(k.label == h.label for k in kept):
            continue
        kept.append(h)
        if len(kept) >= max_items:
            break
    kept.sort(key=lambda h: (0 if h.category == 'feature' else 1, h.y_pct, h.x_pct))
    for i, h in enumerate(kept):
        h.order_index = i
    return kept


# --- Grok / xAI vision ---------------------------------------------------------

def _engine_preference() -> str:
    return (os.environ.get('PHOTO_HIGHLIGHTS_ENGINE') or 'grok').strip().lower()


def _xai_api_key() -> str:
    return (
        os.environ.get('XAI_API_KEY')
        or os.environ.get('GROK_API_KEY')
        or ''
    ).strip()


def _grok_model() -> str:
    return (
        os.environ.get('PHOTO_HIGHLIGHTS_GROK_MODEL')
        or os.environ.get('XAI_MODEL')
        or 'grok-4.5'
    ).strip()


def _image_to_data_url(image_path: str, max_side: int = 1280) -> str:
    """Encode image as JPEG data URL, downscaling large photos for API cost/latency."""
    mime, _ = mimetypes.guess_type(image_path)
    try:
        from PIL import Image
        import io
        with Image.open(image_path) as im:
            im = im.convert('RGB')
            w, h = im.size
            if max(w, h) > max_side:
                scale = max_side / float(max(w, h))
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format='JPEG', quality=85, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode('ascii')
            return f'data:image/jpeg;base64,{b64}'
    except Exception:
        with open(image_path, 'rb') as f:
            raw = f.read()
        # Cap raw payload ~4MB
        if len(raw) > 4_000_000:
            raise ValueError('Image too large and Pillow resize failed')
        b64 = base64.b64encode(raw).decode('ascii')
        mt = mime if mime in ('image/jpeg', 'image/png') else 'image/jpeg'
        return f'data:{mt};base64,{b64}'


def _extract_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    # Strip markdown fences
    if cleaned.startswith('```'):
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
        cleaned = re.sub(r'\s*```$', '', cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        pass
    # Find outermost { ... }
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            return data if isinstance(data, dict) else None
        except Exception:
            return None
    return None


def _normalize_icon(icon: Optional[str], category: str, severity: str) -> str:
    raw = (icon or '').strip().lower().replace('_', '-')
    if raw in ICON_ALLOWLIST:
        return raw
    if category == 'imperfection' or severity in ('caution', 'issue'):
        return 'exclamation-triangle'
    if category == 'feature' or severity == 'positive':
        return 'stars'
    return 'info-circle'


def _normalize_grok_highlights(payload: dict, max_highlights: int) -> dict:
    scene = str(payload.get('scene') or 'other').strip().lower()
    if scene not in SCENE_VALUES:
        # fuzzy map
        if 'dash' in scene:
            scene = 'interior_dash'
        elif 'cabin' in scene or 'interior' in scene:
            scene = 'interior_cabin'
        elif 'wheel' in scene:
            scene = 'wheel_closeup'
        elif 'front' in scene:
            scene = 'exterior_front'
        elif 'rear' in scene:
            scene = 'exterior_rear'
        elif 'side' in scene or 'exterior' in scene:
            scene = 'exterior_side'
        else:
            scene = 'other'

    raw_items = payload.get('highlights') or payload.get('hotspots') or []
    if not isinstance(raw_items, list):
        raw_items = []

    candidates: List[HighlightCandidate] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        try:
            x = float(item.get('x_pct', item.get('x', 50)))
            y = float(item.get('y_pct', item.get('y', 50)))
        except (TypeError, ValueError):
            continue
        # Accept 0-1 normalized coords
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 and (x <= 1.0 and y <= 1.0):
            # Heuristic: if both look like fractions and max<=1, scale
            if x <= 1.0 and y <= 1.0 and (item.get('x_pct') is None and item.get('y_pct') is None):
                x, y = x * 100.0, y * 100.0
            elif max(x, y) <= 1.5:
                x, y = x * 100.0, y * 100.0

        label = str(item.get('label') or item.get('title') or 'Detail').strip()[:120]
        if not label:
            continue
        category = str(item.get('category') or 'detail').strip().lower()
        if category not in ('feature', 'imperfection', 'detail'):
            if category in ('condition', 'damage', 'defect'):
                category = 'imperfection'
            elif category in ('option', 'equipment'):
                category = 'feature'
            else:
                category = 'detail'
        severity = str(item.get('severity') or 'info').strip().lower()
        if severity not in ('positive', 'info', 'caution', 'issue'):
            severity = 'caution' if category == 'imperfection' else ('positive' if category == 'feature' else 'info')
        try:
            conf = float(item.get('confidence', 0.7))
        except (TypeError, ValueError):
            conf = 0.7
        # Grok should already be conservative; still floor weak guesses
        if conf < 0.55:
            continue
        desc = str(item.get('description') or item.get('text') or '').strip()
        if not desc:
            if category == 'imperfection':
                desc = f'{label} — confirm in person.'
            elif category == 'feature':
                desc = f'{label} is visible or listed for this vehicle.'
            else:
                desc = f'{label} highlighted for closer inspection.'
        icon = _normalize_icon(item.get('icon'), category, severity)
        candidates.append(HighlightCandidate(
            x_pct=x,
            y_pct=y,
            label=label,
            category=category,
            description=desc[:400],
            icon=icon,
            severity=severity,
            confidence=conf,
            order_index=idx,
            meta={'engine': 'grok'},
        ))

    final = _nms_highlights(
        candidates,
        min_dist_pct=12.0,
        max_items=max(1, min(8, int(max_highlights or 5))),
        min_confidence=float(os.environ.get('PHOTO_HIGHLIGHTS_MIN_CONF', '0.55')),
    )
    return {
        'scene': scene,
        'highlights': [h.to_dict() for h in final],
        'analysis_version': ANALYSIS_VERSION,
        'engine': 'grok',
    }


def _build_grok_prompt(
    features_text: Optional[str],
    vehicle_context: Optional[dict],
    max_highlights: int,
) -> Tuple[str, str]:
    ctx = vehicle_context or {}
    vehicle_bits = []
    for key in ('year', 'make', 'model', 'body_style', 'exterior_color', 'interior_color', 'drivetrain', 'transmission'):
        val = ctx.get(key)
        if val not in (None, ''):
            vehicle_bits.append(f'{key}={val}')
    vehicle_line = ', '.join(vehicle_bits) if vehicle_bits else 'unknown vehicle'
    features_line = (features_text or '').strip() or '(none provided)'
    matched = match_feature_catalog(features_text)
    matched_labels = ', '.join(m['label'] for _, m in matched[:12]) or '(none matched)'

    system = (
        'You are an expert used-car photo inspector for a dealership website. '
        'You place a small number of accurate Carvana-style hotspot markers on vehicle photos. '
        'Be conservative: fewer high-quality markers beat many weak ones. '
        'Never invent damage. Never place markers on sky, driveway, buildings, people, or background. '
        'Coordinates are percentages of the FULL image width/height (0-100), origin top-left. '
        'Respond with JSON only — no markdown, no commentary.'
    )

    user = f"""Analyze this dealership vehicle photo and return JSON only.

Vehicle: {vehicle_line}
Listed features text: {features_line}
Matched listing features (may place only if visually appropriate for THIS photo): {matched_labels}

Rules:
1. scene must be one of: exterior_front, exterior_side, exterior_rear, wheel_closeup, interior_dash, interior_cabin, other
2. Return at most {max(1, min(8, int(max_highlights or 5)))} highlights (prefer 2-4).
3. Each highlight needs: label, category (feature|imperfection|detail), severity (positive|info|caution|issue), x_pct, y_pct, confidence (0-1), description, icon (bootstrap-icons name like stars, phone, circle, display, exclamation-triangle, check-circle, info-circle).
4. x_pct/y_pct must sit ON the actual object (wheel center, screen center, seat bolster, scratch location on body panel). Not empty space.
5. Features (CarPlay, leather, sunroof, etc.): only if that feature is visible OR clearly relevant to this camera angle. Do not put interior tech bubbles on exterior photos.
6. Imperfections: only clear, visible scratches/scuffs/dings/chips/wear. If unsure, omit. Max 2 imperfections.
7. Prefer listing features that are actually visible over generic labels.
8. confidence >= 0.6 for anything you include.

JSON schema:
{{
  "scene": "exterior_side",
  "highlights": [
    {{
      "label": "Alloy Wheels",
      "category": "feature",
      "severity": "positive",
      "x_pct": 22.5,
      "y_pct": 68.0,
      "confidence": 0.86,
      "description": "Short shopper-facing note.",
      "icon": "circle"
    }}
  ]
}}
"""
    return system, user


def analyze_with_grok(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 5,
) -> dict:
    api_key = _xai_api_key()
    if not api_key:
        raise RuntimeError('XAI_API_KEY is not set')

    data_url = _image_to_data_url(image_path)
    system, user = _build_grok_prompt(features_text, vehicle_context, max_highlights)
    model = _grok_model()
    timeout = float(os.environ.get('PHOTO_HIGHLIGHTS_GROK_TIMEOUT', '90'))

    body = {
        'model': model,
        'temperature': 0.1,
        'max_tokens': 1200,
        'messages': [
            {'role': 'system', 'content': system},
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': data_url,
                            'detail': os.environ.get('PHOTO_HIGHLIGHTS_GROK_DETAIL', 'high'),
                        },
                    },
                    {'type': 'text', 'text': user},
                ],
            },
        ],
    }

    req = urllib.request.Request(
        'https://api.x.ai/v1/chat/completions',
        data=json.dumps(body).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'User-Agent': 'marshallauto-highlights/3',
        },
        method='POST',
    )
    # Prefer certifi CA bundle (macOS python.org builds often lack system certs)
    ssl_context = None
    try:
        import ssl
        import certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ssl_context = None
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
    except urllib.error.HTTPError as exc:
        err_body = ''
        try:
            err_body = exc.read().decode('utf-8', errors='replace')[:500]
        except Exception:
            pass
        raise RuntimeError(f'xAI HTTP {exc.code}: {err_body or exc.reason}') from exc
    except Exception as exc:
        raise RuntimeError(f'xAI request failed: {exc}') from exc

    try:
        payload = json.loads(raw)
    except Exception as exc:
        raise RuntimeError('Invalid JSON from xAI') from exc

    content = ''
    try:
        content = payload['choices'][0]['message']['content'] or ''
    except Exception as exc:
        raise RuntimeError(f'Unexpected xAI response shape: {str(payload)[:300]}') from exc

    if isinstance(content, list):
        # Some SDKs return content parts
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get('type') in ('text', 'output_text'):
                parts.append(part.get('text') or '')
            elif isinstance(part, str):
                parts.append(part)
        content = '\n'.join(parts)

    parsed = _extract_json_object(str(content))
    if not parsed:
        raise RuntimeError(f'Could not parse Grok JSON: {str(content)[:400]}')

    return _normalize_grok_highlights(parsed, max_highlights)


def analyze_with_opencv(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 5,
) -> dict:
    """Conservative OpenCV fallback when Grok is unavailable."""
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
    if scene.startswith('exterior') or scene == 'wheel_closeup':
        candidates.extend(_wheel_highlights(cv2, np, bgr, wheels))
    candidates.extend(_detect_imperfections(cv2, np, bgr, scene))
    candidates.extend(_feature_placements(scene, matched, screen_pt, seat_pts, wheels, w, h))

    if screen_pt and scene.startswith('interior') and float(screen_pt[2]) >= 0.6:
        if not any('carplay' in (c.label or '').lower() or 'android' in (c.label or '').lower()
                   or 'navigation' in (c.label or '').lower() for c in candidates):
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

    max_items = max(1, min(8, int(max_highlights or 5)))
    final = _nms_highlights(
        candidates,
        min_dist_pct=12.0,
        max_items=max_items,
        min_confidence=float(os.environ.get('PHOTO_HIGHLIGHTS_MIN_CONF', '0.55')),
    )
    return {
        'scene': scene,
        'highlights': [h.to_dict() for h in final],
        'analysis_version': ANALYSIS_VERSION,
        'engine': 'opencv',
    }


def analyze_vehicle_image(
    image_path: str,
    features_text: Optional[str] = None,
    vehicle_context: Optional[dict] = None,
    max_highlights: int = 5,
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
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError(f'Image not found: {image_path}')

    engine_pref = _engine_preference()
    max_highlights = int(max_highlights or os.environ.get('PHOTO_HIGHLIGHTS_MAX', '5') or 5)
    max_highlights = max(1, min(8, max_highlights))

    want_grok = engine_pref in ('grok', 'xai', 'auto', '')
    force_opencv = engine_pref in ('opencv', 'local', 'cv')

    if want_grok and not force_opencv and _xai_api_key():
        try:
            return analyze_with_grok(
                image_path,
                features_text=features_text,
                vehicle_context=vehicle_context,
                max_highlights=max_highlights,
            )
        except Exception as exc:
            logger.warning('Grok highlight analysis failed, falling back to OpenCV: %s', exc)
            if engine_pref in ('grok', 'xai') and os.environ.get(
                'PHOTO_HIGHLIGHTS_GROK_REQUIRED', ''
            ).lower() in ('1', 'true', 'yes', 'on'):
                raise

    return analyze_with_opencv(
        image_path,
        features_text=features_text,
        vehicle_context=vehicle_context,
        max_highlights=max_highlights,
    )
