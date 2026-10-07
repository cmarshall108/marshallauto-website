import os
import importlib
import shutil
import subprocess
import unittest
from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app import create_app, db
from app.models import User, Vehicle, VideoUploadJob, utcnow
from app.video_jobs import cancel_video_jobs, run_video_worker_once
from app.utils import compress_video_file
from config import TestingConfig


def photo():
    stream = BytesIO()
    Image.new('RGB', (32, 24), 'blue').save(stream, 'JPEG')
    stream.seek(0)
    return stream


class MediaUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.app = create_app(TestingConfig)
        self.app.config.update(
            UPLOAD_FOLDER=os.path.join(self.tmp.name, 'public'),
            PRIVATE_UPLOAD_FOLDER=os.path.join(self.tmp.name, 'private'),
            PHOTO_HIGHLIGHTS_ENABLED=False,
        )
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.rate_limit = patch('app.admin.rate_limit_exceeded', return_value=False)
        self.rate_limit.start()
        db.create_all()
        self.vehicle = Vehicle(year=2020, make='Honda', model='Civic', price=15000, mileage=40000)
        user = User(username='media-admin')
        user.set_password('test-password-only')
        db.session.add_all([self.vehicle, user])
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post('/admin/login', data={
            'username': user.username, 'password': 'test-password-only',
        })
        self.headers = {'Accept': 'application/json'}

    def tearDown(self):
        self.rate_limit.stop()
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()
        self.tmp.cleanup()

    def upload(self, data):
        return self.client.post(
            f'/admin/vehicles/{self.vehicle.id}/media', data=data, headers=self.headers,
        )

    def queue_video(self):
        with patch('app.video_jobs._ffmpeg_path', return_value='/usr/bin/ffmpeg'):
            response = self.upload({'video_file': (BytesIO(b'video content'), 'walkaround.mov')})
        self.assertEqual(response.status_code, 202)
        return db.session.get(VideoUploadJob, response.json['video_job_id'])

    def complete_compression(self, raw_path, output_path):
        self.assertTrue(os.path.isfile(raw_path))
        with open(output_path, 'wb') as output:
            output.write(b'compressed video')
        return True

    def test_twenty_photos_and_video_use_small_requests_without_inline_compression(self):
        # Each request fits; the same media in one multipart body would exceed this.
        self.app.config['MAX_CONTENT_LENGTH'] = 4096
        batch = self.client.post(f'/admin/vehicles/{self.vehicle.id}/edit', data={
            'images': [(photo(), f'{index}.jpg') for index in range(20)],
            'video_file': (BytesIO(b'video content'), 'walkaround.mov'),
        }, headers=self.headers)
        self.assertEqual(batch.status_code, 413)
        with patch('app.video_jobs.compress_video_file') as compressor:
            for index in range(20):
                response = self.upload({'images': (photo(), f'{index}.jpg')})
                self.assertEqual(response.status_code, 200)
            job = self.queue_video()
            compressor.assert_not_called()
        db.session.refresh(self.vehicle)
        self.assertEqual(len(self.vehicle.images), 20)
        self.assertEqual([image.order_index for image in self.vehicle.images], list(range(20)))
        self.assertEqual(sum(image.is_primary for image in self.vehicle.images), 1)
        self.assertEqual(job.status, 'queued')
        self.assertIsNone(self.vehicle.video_url)
        with patch('app.video_jobs.compress_video_file', side_effect=self.complete_compression):
            self.assertTrue(run_video_worker_once())
        db.session.refresh(self.vehicle)
        self.assertTrue(self.vehicle.video_url.endswith('.mp4'))
        self.assertEqual(db.session.get(VideoUploadJob, job.id).status, 'completed')
        self.assertFalse(os.path.exists(os.path.join(
            self.app.config['PRIVATE_UPLOAD_FOLDER'], 'videos', job.input_filename,
        )))

    def test_media_limit_returns_json_413(self):
        self.app.config['MEDIA_UPLOAD_MAX_CONTENT_LENGTH'] = 1024
        response = self.upload({'images': (BytesIO(b'x' * 2048), 'large.jpg')})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(response.json['success'])

    def test_staged_file_is_removed_when_queue_commit_fails(self):
        with patch('app.video_jobs._ffmpeg_path', return_value='/usr/bin/ffmpeg'), \
                patch.object(db.session, 'commit', side_effect=RuntimeError('database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'database unavailable'):
                self.upload({'video_file': (BytesIO(b'video'), 'video.mp4')})
        self.assertEqual(os.listdir(os.path.join(
            self.app.config['PRIVATE_UPLOAD_FOLDER'], 'videos',
        )), [])
        self.assertEqual(VideoUploadJob.query.count(), 0)

    def test_rejects_batch_and_bad_photo(self):
        response = self.upload({'images': [(photo(), 'one.jpg'), (photo(), 'two.jpg')]})
        self.assertEqual(response.status_code, 400)
        response = self.upload({'images': (BytesIO(b'not a photo'), 'broken.jpg')})
        self.assertEqual(response.status_code, 422)
        self.assertIn('broken.jpg', response.json['message'])

    def test_video_requires_ffmpeg(self):
        with patch('app.video_jobs._ffmpeg_path', return_value=None):
            response = self.upload({'video_file': (BytesIO(b'video'), 'video.mp4')})
        self.assertEqual(response.status_code, 422)
        self.assertIn('ffmpeg', response.json['message'])
        self.assertEqual(VideoUploadJob.query.count(), 0)

    def test_failed_compression_keeps_previous_video_and_reports_error(self):
        self.vehicle.video_url = 'https://example.com/previous.mp4'
        db.session.commit()
        job = self.queue_video()
        with patch('app.video_jobs.compress_video_file', return_value=False):
            run_video_worker_once()
        db.session.refresh(self.vehicle)
        self.assertEqual(self.vehicle.video_url, 'https://example.com/previous.mp4')
        self.assertEqual(job.status, 'failed')
        self.assertIn('compression failed', job.last_error)
        page = self.client.get(f'/admin/vehicles/{self.vehicle.id}/edit')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Video compression failed', page.data)

    def test_success_replaces_and_removes_old_local_video(self):
        folder = os.path.join(self.app.config['UPLOAD_FOLDER'], 'vehicles', 'videos')
        os.makedirs(folder)
        old = os.path.join(folder, 'old.mp4')
        with open(old, 'wb') as output:
            output.write(b'old')
        self.vehicle.video_url = '/static/uploads/vehicles/videos/old.mp4'
        db.session.commit()
        self.queue_video()
        self.assertTrue(os.path.isfile(old))
        with patch('app.video_jobs.compress_video_file', side_effect=self.complete_compression):
            run_video_worker_once()
        self.assertFalse(os.path.exists(old))

    def test_superseded_video_does_not_publish(self):
        first = self.queue_video()
        second = self.queue_video()
        self.assertEqual(first.status, 'cancelled')
        with patch('app.video_jobs.compress_video_file', side_effect=self.complete_compression) as compressor:
            run_video_worker_once()
        self.assertEqual(compressor.call_count, 1)
        self.assertEqual(second.status, 'completed')

    def test_cancel_during_compression_discards_output(self):
        self.queue_video()

        def compress_and_cancel(raw_path, output_path):
            self.complete_compression(raw_path, output_path)
            cancel_video_jobs(self.vehicle)
            db.session.commit()
            return True

        with patch('app.video_jobs.compress_video_file', side_effect=compress_and_cancel):
            run_video_worker_once()
        db.session.refresh(self.vehicle)
        self.assertIsNone(self.vehicle.video_url)
        self.assertEqual(os.listdir(os.path.join(
            self.app.config['UPLOAD_FOLDER'], 'vehicles', 'videos',
        )), [])

    def test_sold_or_deleted_vehicle_never_publishes(self):
        job = self.queue_video()
        self.vehicle.status = 'sold'
        db.session.commit()
        with patch('app.video_jobs.compress_video_file') as compressor:
            run_video_worker_once()
        compressor.assert_not_called()
        self.assertEqual(job.status, 'cancelled')
        self.vehicle.status = 'available'
        db.session.commit()
        deleted_job = self.queue_video()
        self.client.post(f'/admin/vehicles/{self.vehicle.id}/delete')
        with patch('app.video_jobs.compress_video_file') as compressor:
            run_video_worker_once()
        compressor.assert_not_called()
        self.assertEqual(deleted_job.status, 'cancelled')

    def test_interrupted_job_is_reclaimed_with_bounded_attempts(self):
        job = self.queue_video()
        job.status = 'running'
        job.attempts = 2
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        with patch('app.video_jobs.compress_video_file') as compressor:
            run_video_worker_once()
        compressor.assert_not_called()
        self.assertEqual(job.status, 'failed')

    def test_interrupted_job_recovers_and_cleans_old_partial_output(self):
        job = self.queue_video()
        job.status = 'running'
        job.attempts = 1
        job.claim_token = 'oldtoken'
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
        folder = os.path.join(self.app.config['UPLOAD_FOLDER'], 'vehicles', 'videos')
        os.makedirs(folder)
        partial = os.path.join(folder, f'video_{job.id}_oldtoken.mp4')
        with open(partial, 'wb') as output:
            output.write(b'partial')
        with patch('app.video_jobs.compress_video_file', side_effect=self.complete_compression):
            run_video_worker_once()
        self.assertFalse(os.path.exists(partial))
        self.assertEqual(job.status, 'completed')
        self.assertEqual(job.attempts, 2)

    def test_worker_processes_video_even_when_photo_highlights_disabled(self):
        from app.highlight_worker import run_loop
        self.queue_video()
        with patch('app.highlight_worker.create_worker_app', return_value=self.app), \
                patch('app.highlight_worker._shutdown', False), \
                patch('app.video_jobs.compress_video_file', side_effect=self.complete_compression), \
                patch('app.highlight_jobs.run_worker_once') as photos:
            run_loop(once=True)
        photos.assert_not_called()
        self.assertEqual(VideoUploadJob.query.first().status, 'completed')

    def test_no_javascript_video_upload_is_also_queued(self):
        with patch('app.video_jobs._ffmpeg_path', return_value='/usr/bin/ffmpeg'), \
                patch('app.video_jobs.compress_video_file') as compressor:
            response = self.client.post(f'/admin/vehicles/{self.vehicle.id}/edit', data={
                'year': '2020', 'make': 'Honda', 'model': 'Civic',
                'price': '15000', 'mileage': '40000', 'condition': 'used',
                'title_status': 'clean', 'status': 'available', 'drivetrain': '',
                'video_file': (BytesIO(b'video content'), 'video.mov'),
            })
        self.assertEqual(response.status_code, 302)
        compressor.assert_not_called()
        self.assertEqual(VideoUploadJob.query.first().status, 'queued')

    def test_failed_compressor_removes_partial_output(self):
        output = os.path.join(self.tmp.name, 'failed.mp4')
        with open(output, 'wb') as video:
            video.write(b'partial')
        with patch('app.utils._ffmpeg_path', return_value='/usr/bin/ffmpeg'), \
                patch('app.utils.subprocess.run') as run:
            run.return_value.returncode = 1
            run.return_value.stderr = b'invalid video'
            self.assertFalse(compress_video_file('/unused/input.mov', output))
        self.assertFalse(os.path.exists(output))
        self.assertEqual(run.call_args.kwargs['timeout'], 600)
        self.assertIn('1', run.call_args.args[0])

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is not installed')
    def test_real_ffmpeg_compression(self):
        raw = os.path.join(self.tmp.name, 'raw.mp4')
        output = os.path.join(self.tmp.name, 'compressed.mp4')
        subprocess.run([
            shutil.which('ffmpeg'), '-y', '-f', 'lavfi', '-i',
            'color=c=blue:s=64x48:r=5', '-t', '0.4', '-c:v', 'libx264', raw,
        ], check=True, capture_output=True, timeout=30)
        self.assertTrue(compress_video_file(raw, output))
        self.assertGreater(os.path.getsize(output), 0)

    def test_deferred_create_saves_details_and_posts_only_after_uploads(self):
        with patch('app.admin._maybe_publish_vehicle_to_facebook') as publish:
            response = self.client.post('/admin/vehicles/new', data={
                'year': '2021', 'make': 'Toyota', 'model': 'Camry',
                'price': '16000', 'mileage': '30000', 'condition': 'used',
                'title_status': 'clean', 'status': 'available', 'drivetrain': '',
            }, headers={**self.headers, 'X-Vehicle-Media-Upload': 'deferred'})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json['success'])
            publish.assert_not_called()
            finish = self.client.post(response.json['finish_url'], data={
                'is_new': '1', 'post_to_facebook': '1',
            }, headers=self.headers)
            self.assertEqual(finish.status_code, 200)
            self.assertEqual(publish.call_args.kwargs, {'is_new': True, 'force': True})

    def test_deferred_validation_and_authentication(self):
        response = self.client.post('/admin/vehicles/new', data={'year': 'bad'}, headers={
            **self.headers, 'X-Vehicle-Media-Upload': 'deferred',
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn('year', response.json['errors'])
        with self.app.app_context():
            self.assertEqual(self.app.test_client().post(
                f'/admin/vehicles/{self.vehicle.id}/media',
            ).status_code, 302)
        self.app.config['WTF_CSRF_ENABLED'] = True
        response = self.upload({'images': (photo(), 'photo.jpg')})
        self.assertEqual(response.status_code, 400)
        self.assertIn('security token', response.json['message'])


class VideoMigrationTests(unittest.TestCase):
    def test_upgrade_is_idempotent_for_bootstrapped_database(self):
        migration = importlib.import_module('migrations.versions.c3d4e5f6a7b8_add_video_upload_jobs')
        engine = create_engine('sqlite:///:memory:')
        with engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE vehicles (id INTEGER PRIMARY KEY)')
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            columns = {column['name'] for column in inspect(connection).get_columns('video_upload_jobs')}
            self.assertIn('claim_token', columns)
            self.assertIn('input_filename', columns)
            self.assertIn('lease_expires_at', columns)
        engine.dispose()


if __name__ == '__main__':
    unittest.main()
