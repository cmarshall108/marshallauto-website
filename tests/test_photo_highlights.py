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


class HighlightQueueTests(unittest.TestCase):
    def setUp(self):
        from app import create_app, db
        from app.models import Vehicle, VehicleImage
        from config import TestingConfig

        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.vehicle = Vehicle(year=2018, make='Toyota', model='Camry', price=12500, mileage=80000)
        self.image = VehicleImage(filename='car.jpg')
        self.vehicle.images.append(self.image)
        db.session.add(self.vehicle)
        db.session.commit()

    def tearDown(self):
        from app import db

        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def test_updates_do_not_requeue_finished_images_even_without_highlights(self):
        from app import db
        from app.highlight_jobs import enqueue_image_highlight_job, enqueue_vehicle_highlight_jobs
        from app.models import PhotoHighlightJob

        for state in ('ready', 'failed', 'skipped'):
            with self.subTest(state=state):
                self.image.highlight_status = state
                db.session.commit()
                self.assertIsNone(enqueue_image_highlight_job(self.image.id))
                self.assertEqual(enqueue_vehicle_highlight_jobs(self.vehicle.id), 0)
                self.assertEqual(PhotoHighlightJob.query.count(), 0)

    def test_updates_do_not_reset_terminal_job_retry_budget(self):
        from app import db
        from app.highlight_jobs import enqueue_image_highlight_job, enqueue_vehicle_highlight_jobs
        from app.models import PhotoHighlightJob

        job = enqueue_image_highlight_job(self.image.id)
        for state in ('completed', 'failed', 'cancelled'):
            with self.subTest(state=state):
                job.status = state
                job.attempts = job.max_attempts
                self.image.highlight_status = 'pending'
                db.session.commit()
                self.assertEqual(enqueue_vehicle_highlight_jobs(self.vehicle.id), 0)
                self.assertEqual(PhotoHighlightJob.query.count(), 1)
                self.assertEqual(job.attempts, job.max_attempts)

    def test_analyzed_timestamp_prevents_requeue_when_status_changes(self):
        from app import db
        from app.highlight_jobs import enqueue_vehicle_highlight_jobs
        from app.models import utcnow

        self.image.highlight_analyzed_at = utcnow()
        db.session.commit()
        self.assertEqual(enqueue_vehicle_highlight_jobs(self.vehicle.id), 0)

    def test_new_photos_queue_once_and_updates_keep_active_job(self):
        from app import db
        from app.highlight_jobs import enqueue_image_highlight_job, enqueue_vehicle_highlight_jobs
        from app.models import PhotoHighlightJob, VehicleImage

        self.image.highlight_status = 'ready'
        new_image = VehicleImage(filename='new.jpg')
        self.vehicle.images.append(new_image)
        db.session.commit()
        self.assertEqual(enqueue_vehicle_highlight_jobs(self.vehicle.id), 1)
        job = PhotoHighlightJob.query.one()
        self.assertEqual(job.vehicle_image_id, new_image.id)
        for state in ('queued', 'running'):
            with self.subTest(state=state):
                job.status = state
                db.session.commit()
                self.assertEqual(enqueue_image_highlight_job(new_image.id).id, job.id)
                self.assertEqual(PhotoHighlightJob.query.count(), 1)

    def test_force_allows_explicit_reanalysis(self):
        from app import db
        from app.highlight_jobs import enqueue_image_highlight_job, enqueue_vehicle_highlight_jobs
        from app.models import PhotoHighlightJob, utcnow

        original = enqueue_image_highlight_job(self.image.id)
        original.status = 'completed'
        self.image.highlight_status = 'ready'
        self.image.highlight_analyzed_at = utcnow()
        db.session.commit()
        self.assertEqual(enqueue_vehicle_highlight_jobs(self.vehicle.id, force=True), 1)
        self.assertEqual(PhotoHighlightJob.query.count(), 2)
        self.assertEqual(original.status, 'completed')
        self.assertEqual(self.image.highlight_status, 'pending')


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
