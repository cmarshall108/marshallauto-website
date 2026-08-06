import os
import tempfile
import unittest
from unittest import mock

from app.photo_highlights import (
    FEATURE_CATALOG,
    _nms_highlights,
    HighlightCandidate,
    match_feature_catalog,
)


class FeatureCatalogTests(unittest.TestCase):
    def test_match_carplay_and_leather(self):
        matched = match_feature_catalog('Apple CarPlay, Android Auto, Leather Seats, Heated Seats')
        labels = {m[1]['label'] for m in matched}
        self.assertIn('Apple CarPlay', labels)
        self.assertIn('Android Auto', labels)
        self.assertTrue(any('Leather' in label for label in labels))
        self.assertIn('Heated Seats', labels)

    def test_catalog_has_imperfection_friendly_keys(self):
        # Sanity: catalog is non-empty and entries have required display fields
        self.assertGreater(len(FEATURE_CATALOG), 10)
        sample = next(iter(FEATURE_CATALOG.values()))
        for key in ('label', 'category', 'severity', 'icon', 'description', 'scenes'):
            self.assertIn(key, sample)


class NmsTests(unittest.TestCase):
    def test_nms_keeps_spread_out_points(self):
        cands = [
            HighlightCandidate(10, 10, 'A', 'feature', 'a', 'info-circle', 'positive', 0.9, 0),
            HighlightCandidate(12, 12, 'B', 'feature', 'b', 'info-circle', 'positive', 0.8, 1),
            HighlightCandidate(80, 80, 'C', 'feature', 'c', 'info-circle', 'positive', 0.7, 2),
        ]
        kept = _nms_highlights(cands, max_items=3, min_dist_pct=8.0)
        labels = {c.label for c in kept}
        self.assertIn('A', labels)
        self.assertIn('C', labels)
        self.assertNotIn('B', labels)

    def test_nms_respects_max_items(self):
        cands = [
            HighlightCandidate(10 + i * 20, 20, f'H{i}', 'detail', '', 'info-circle', 'info', 0.7, i)
            for i in range(6)
        ]
        kept = _nms_highlights(cands, max_items=3, min_dist_pct=5.0)
        self.assertLessEqual(len(kept), 3)
        self.assertEqual(len(kept), 3)


class GrokNormalizeTests(unittest.TestCase):
    def test_normalize_grok_payload(self):
        from app.photo_highlights import _normalize_grok_highlights

        payload = {
            'scene': 'exterior_side',
            'highlights': [
                {
                    'label': 'Alloy Wheels',
                    'category': 'feature',
                    'severity': 'positive',
                    'x_pct': 22.5,
                    'y_pct': 68.0,
                    'confidence': 0.86,
                    'description': 'Wheel detail',
                    'icon': 'circle',
                },
                {
                    'label': 'Too weak',
                    'category': 'imperfection',
                    'severity': 'caution',
                    'x_pct': 50,
                    'y_pct': 50,
                    'confidence': 0.2,
                    'description': 'should drop',
                },
            ],
        }
        result = _normalize_grok_highlights(payload, max_highlights=5)
        self.assertEqual(result['scene'], 'exterior_side')
        self.assertEqual(result['engine'], 'grok')
        self.assertEqual(result['analysis_version'], 3)
        labels = {h['label'] for h in result['highlights']}
        self.assertIn('Alloy Wheels', labels)
        self.assertNotIn('Too weak', labels)


class AnalyzeSmokeTests(unittest.TestCase):
    def test_analyze_synthetic_image_when_opencv_available(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
        except Exception:
            self.skipTest('opencv/numpy not installed')

        from app.photo_highlights import analyze_vehicle_image

        # Create a simple synthetic "car-ish" image
        img = np.zeros((480, 640, 3), dtype=np.uint8)
        img[:] = (40, 40, 40)
        # body
        cv2.rectangle(img, (80, 180), (560, 360), (90, 90, 200), -1)
        # wheels
        cv2.circle(img, (160, 360), 40, (20, 20, 20), -1)
        cv2.circle(img, (480, 360), 40, (20, 20, 20), -1)
        # bright "screen-like" rectangle for interior-ish cues
        cv2.rectangle(img, (250, 120), (390, 200), (230, 230, 230), -1)

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'car.jpg')
            cv2.imwrite(path, img)
            # Force OpenCV path so unit tests do not call xAI
            with mock.patch.dict(os.environ, {
                'PHOTO_HIGHLIGHTS_ENGINE': 'opencv',
                'XAI_API_KEY': '',
            }, clear=False):
                result = analyze_vehicle_image(
                    path,
                    features_text='Apple CarPlay, Leather Seats, New Tires',
                    vehicle_context={'drivetrain': 'AWD'},
                    max_highlights=5,
                )

        self.assertIn('scene', result)
        self.assertIn('highlights', result)
        self.assertIsInstance(result['highlights'], list)
        self.assertEqual(result['analysis_version'], 3)
        self.assertEqual(result.get('engine'), 'opencv')
        for h in result['highlights']:
            self.assertIn('x_pct', h)
            self.assertIn('y_pct', h)
            self.assertIn('label', h)
            self.assertGreaterEqual(h['x_pct'], 0)
            self.assertLessEqual(h['x_pct'], 100)

    def test_analyze_prefers_grok_when_mocked(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
        except Exception:
            self.skipTest('opencv/numpy not installed')

        from app.photo_highlights import analyze_vehicle_image

        img = np.zeros((120, 160, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'tiny.jpg')
            cv2.imwrite(path, img)
            fake = {
                'scene': 'exterior_side',
                'highlights': [{
                    'x_pct': 40.0,
                    'y_pct': 60.0,
                    'label': 'Grok Spot',
                    'category': 'feature',
                    'description': 'from grok',
                    'icon': 'stars',
                    'severity': 'positive',
                    'confidence': 0.9,
                    'source': 'auto',
                    'order_index': 0,
                }],
                'analysis_version': 3,
                'engine': 'grok',
            }
            with mock.patch.dict(os.environ, {
                'PHOTO_HIGHLIGHTS_ENGINE': 'grok',
                'XAI_API_KEY': 'test-key-not-real',
            }, clear=False):
                with mock.patch(
                    'app.photo_highlights.analyze_with_grok',
                    return_value=fake,
                ) as grok_mock:
                    result = analyze_vehicle_image(path, max_highlights=5)

        grok_mock.assert_called_once()
        self.assertEqual(result['engine'], 'grok')
        self.assertEqual(result['highlights'][0]['label'], 'Grok Spot')


class EnqueueHelperTests(unittest.TestCase):
    def test_queue_stats_shape_with_mocks(self):
        from app import highlight_jobs

        fake_app = mock.MagicMock()
        # Minimal stand-in so imports inside queue_stats work if called under app context is not required
        with mock.patch.object(highlight_jobs, 'queue_stats', wraps=None) as _:
            # Direct unit: ACTIVE_STATUSES constants exist
            self.assertIn('queued', highlight_jobs.ACTIVE_STATUSES)
            self.assertIn('running', highlight_jobs.ACTIVE_STATUSES)


if __name__ == '__main__':
    unittest.main()
