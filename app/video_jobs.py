"""Persistent video staging and leased background compression."""
import os
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import and_, or_

from app import db
from app.models import Vehicle, VideoUploadJob, utcnow
from app.utils import (
    ALLOWED_VIDEO_EXTENSIONS, _ffmpeg_path, allowed_file, compress_video_file,
    delete_local_video_file,
)


class VideoUploadError(ValueError):
    pass


def _staging_folder():
    return os.path.join(current_app.config['PRIVATE_UPLOAD_FOLDER'], 'videos')


def _remove_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        current_app.logger.exception('Unable to clean up video file %s', path)


def cancel_video_jobs(vehicle):
    for job in VideoUploadJob.query.filter_by(vehicle_id=vehicle.id).filter(
        VideoUploadJob.status.in_(('queued', 'running'))
    ):
        job.status = 'cancelled'
        job.finished_at = utcnow()
    # The worker cleans staged files after commit, avoiding deletion on rollback.


def discard_staged_video(job):
    _remove_file(os.path.join(_staging_folder(), job.input_filename))


def stage_video_upload(vehicle, file_obj):
    if vehicle.status == 'sold':
        raise VideoUploadError('Videos cannot be uploaded for sold vehicles.')
    if not file_obj or not file_obj.filename or not allowed_file(
        file_obj.filename, ALLOWED_VIDEO_EXTENSIONS
    ):
        raise VideoUploadError('Unsupported video. Use MP4, MOV, M4V, or WebM.')
    if not _ffmpeg_path():
        raise VideoUploadError('Video processing is unavailable: ffmpeg must be installed on the server.')
    folder = _staging_folder()
    os.makedirs(folder, exist_ok=True)
    filename = f'{secrets.token_hex(16)}.{file_obj.filename.rsplit(".", 1)[1].lower()}'
    path = os.path.join(folder, filename)
    try:
        file_obj.save(path)
        cancel_video_jobs(vehicle)
        job = VideoUploadJob(
            vehicle_id=vehicle.id, input_filename=filename, previous_url=vehicle.video_url,
        )
        db.session.add(job)
        db.session.flush()
        return job
    except Exception:
        _remove_file(path)
        raise


def run_video_worker_once():
    """Claim atomically; recover interrupted jobs without publishing stale results."""
    now = utcnow()
    eligible = or_(
        VideoUploadJob.status == 'queued',
        and_(VideoUploadJob.status == 'running', VideoUploadJob.lease_expires_at < now),
    )
    for terminal in VideoUploadJob.query.filter(
        VideoUploadJob.status.in_(('completed', 'failed', 'cancelled'))
    ).all():
        path = os.path.join(_staging_folder(), terminal.input_filename)
        if os.path.exists(path):
            _remove_file(path)
    candidate = VideoUploadJob.query.filter(eligible).order_by(VideoUploadJob.id).first()
    if not candidate:
        return False
    job_id = candidate.id
    previous_token = candidate.claim_token
    token = secrets.token_hex(16)
    claimed = VideoUploadJob.query.filter_by(id=job_id).filter(eligible).update({
        'status': 'running', 'claim_token': token,
        'lease_expires_at': now + timedelta(seconds=900),
        'attempts': VideoUploadJob.attempts + 1,
    }, synchronize_session=False)
    db.session.commit()
    if not claimed:
        return False
    db.session.expire_all()
    job = db.session.get(VideoUploadJob, job_id)
    input_path = os.path.join(_staging_folder(), job.input_filename)
    output_name = f'video_{job_id}_{token}.mp4'
    output_folder = os.path.join(current_app.config['UPLOAD_FOLDER'], 'vehicles', 'videos')
    os.makedirs(output_folder, exist_ok=True)
    if previous_token:
        _remove_file(os.path.join(output_folder, f'video_{job_id}_{previous_token}.mp4'))
    output_path = os.path.join(output_folder, output_name)
    published = False
    try:
        vehicle = db.session.get(Vehicle, job.vehicle_id) if job.vehicle_id else None
        if not vehicle or vehicle.status == 'sold' or vehicle.video_url != job.previous_url:
            status, error = 'cancelled', None
        elif job.attempts > 2:
            status, error = 'failed', 'Video processing was interrupted repeatedly. Please upload again.'
        elif not compress_video_file(input_path, output_path):
            status, error = 'failed', 'Video compression failed. The existing video was kept; upload again or use a video URL.'
        else:
            # End the read transaction and reload after compression: the vehicle
            # may have been sold, deleted, or given another video in the meantime.
            db.session.rollback()
            job = db.session.get(VideoUploadJob, job_id)
            reserved = VideoUploadJob.query.filter_by(
                id=job_id, status='running', claim_token=token,
            ).update({'status': 'completed'}, synchronize_session=False)
            if not reserved:
                db.session.rollback()
                return True
            replaced = Vehicle.query.filter_by(id=job.vehicle_id).filter(
                Vehicle.status != 'sold', Vehicle.video_url == job.previous_url,
            ).update({
                'video_url': f'/static/uploads/vehicles/videos/{output_name}',
            }, synchronize_session=False)
            status, error = ('completed', None) if replaced else ('cancelled', None)
        VideoUploadJob.query.filter_by(id=job_id, claim_token=token).filter(
            VideoUploadJob.status.in_(('running', 'completed'))
        ).update({
            'status': status, 'last_error': error, 'finished_at': utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        published = status == 'completed'
        if published:
            delete_local_video_file(job.previous_url)
        if error:
            current_app.logger.error('Video job %s: %s', job_id, error)
        return True
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Video job %s failed unexpectedly', job_id)
        VideoUploadJob.query.filter_by(id=job_id, status='running', claim_token=token).update({
            'status': 'failed', 'last_error': 'Server error during video processing. Please upload again.',
            'finished_at': utcnow(),
        }, synchronize_session=False)
        db.session.commit()
        raise
    finally:
        if not published:
            _remove_file(output_path)
        db.session.expire_all()
        current_job = db.session.get(VideoUploadJob, job_id)
        if current_job and current_job.status in ('completed', 'failed', 'cancelled'):
            _remove_file(input_path)
