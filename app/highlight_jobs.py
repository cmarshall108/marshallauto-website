"""
DB-backed photo highlight job queue + separate-process worker.

Jobs are claimed with a simple lease so multiple workers stay safe.
The Flask web process only enqueues; analysis runs in `python -m app.highlight_worker`.
"""
from __future__ import annotations

import logging
import os
import random
import socket
import traceback
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import and_, or_
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled'})
ACTIVE_STATUSES = frozenset({'queued', 'running'})


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _worker_name() -> str:
    return f'{socket.gethostname()}:{os.getpid()}'


def enqueue_image_highlight_job(image_id: int, force: bool = False, priority: int = 100):
    """Queue analysis for a single VehicleImage. Returns job or None."""
    from app import db
    from app.models import PhotoHighlightJob, VehicleImage

    image = db.session.get(VehicleImage, image_id)
    if not image:
        return None

    existing = (
        PhotoHighlightJob.query
        .filter(
            PhotoHighlightJob.vehicle_image_id == image_id,
            PhotoHighlightJob.status.in_(tuple(ACTIVE_STATUSES)),
        )
        .order_by(PhotoHighlightJob.id.desc())
        .first()
    )
    if existing and not force:
        return existing

    if force and existing:
        existing.status = 'cancelled'
        existing.finished_at = utcnow()
        existing.last_error = 'Superseded by forced requeue'
        db.session.add(existing)

    # Mark image pending so admin UI can show status immediately
    if image.highlight_status not in ('processing',) or force:
        image.highlight_status = 'pending'
        image.highlight_error = None
        db.session.add(image)

    job = PhotoHighlightJob(
        vehicle_image_id=image.id,
        vehicle_id=image.vehicle_id,
        status='queued',
        priority=int(priority or 100),
        attempts=0,
        max_attempts=3,
        scheduled_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.session.add(job)
    db.session.commit()
    return job


def enqueue_vehicle_highlight_jobs(vehicle_id: int, force: bool = False, only_missing: bool = True) -> int:
    """Enqueue jobs for all images on a vehicle. Returns number of jobs created/kept."""
    from app import db
    from app.models import Vehicle, VehicleImage

    vehicle = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(id=vehicle_id)
        .first()
    )
    if not vehicle:
        return 0

    count = 0
    for image in vehicle.ordered_images():
        if only_missing and not force:
            if image.highlight_status == 'ready' and (image.highlights or []):
                continue
            if image.highlight_status == 'processing':
                # Ensure a job exists
                pass
        job = enqueue_image_highlight_job(image.id, force=force)
        if job:
            count += 1
    return count


def enqueue_images(image_ids: Sequence[int], force: bool = False) -> int:
    n = 0
    for image_id in image_ids:
        if enqueue_image_highlight_job(int(image_id), force=force):
            n += 1
    return n


def claim_next_job(lease_seconds: int = 300):
    """
    Atomically claim the next queued (or expired running) job.
    Returns PhotoHighlightJob or None.
    """
    from app import db
    from app.models import PhotoHighlightJob

    now = utcnow()
    lease_until = now + timedelta(seconds=max(30, int(lease_seconds)))
    worker = _worker_name()

    # Prefer highest priority (lower number), then oldest scheduled
    candidates = (
        PhotoHighlightJob.query
        .filter(
            or_(
                PhotoHighlightJob.status == 'queued',
                and_(
                    PhotoHighlightJob.status == 'running',
                    PhotoHighlightJob.lease_expires_at.isnot(None),
                    PhotoHighlightJob.lease_expires_at < now,
                ),
            )
        )
        .order_by(PhotoHighlightJob.priority.asc(), PhotoHighlightJob.scheduled_at.asc(), PhotoHighlightJob.id.asc())
        .limit(8)
        .all()
    )
    if not candidates:
        return None

    # Randomize slightly among the first few to reduce multi-worker collisions
    random.shuffle(candidates)
    for job in candidates:
        # Optimistic claim
        previous_status = job.status
        job.status = 'running'
        job.locked_by = worker
        job.locked_at = now
        job.lease_expires_at = lease_until
        job.started_at = job.started_at or now
        job.updated_at = now
        job.attempts = int(job.attempts or 0) + (1 if previous_status != 'running' else 0)
        try:
            db.session.add(job)
            db.session.commit()
            return job
        except Exception:
            db.session.rollback()
            continue
    return None


def heartbeat_job(job_id: int, lease_seconds: int = 300) -> bool:
    from app import db
    from app.models import PhotoHighlightJob

    job = db.session.get(PhotoHighlightJob, job_id)
    if not job or job.status != 'running':
        return False
    if job.locked_by != _worker_name():
        return False
    job.lease_expires_at = utcnow() + timedelta(seconds=max(30, int(lease_seconds)))
    job.updated_at = utcnow()
    db.session.add(job)
    db.session.commit()
    return True


def _image_path_for(image) -> str:
    from flask import current_app
    return os.path.join(current_app.config['UPLOAD_FOLDER'], 'vehicles', image.filename)


def process_job(job) -> bool:
    """Run analysis for a claimed job. Returns True on success."""
    from app import db
    from app.models import Vehicle, VehicleImage, VehicleImageHighlight
    from app.photo_highlights import ANALYSIS_VERSION, analyze_vehicle_image

    image = db.session.get(VehicleImage, job.vehicle_image_id)
    if not image:
        job.status = 'failed'
        job.last_error = 'Vehicle image missing'
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        db.session.add(job)
        db.session.commit()
        return False

    image.highlight_status = 'processing'
    image.highlight_error = None
    db.session.add(image)
    db.session.commit()

    vehicle = db.session.get(Vehicle, image.vehicle_id)
    features_text = (vehicle.features if vehicle else None) or ''
    context = {}
    if vehicle:
        context = {
            'drivetrain': vehicle.drivetrain,
            'transmission': vehicle.transmission,
            'body_style': vehicle.body_style,
            'exterior_color': vehicle.exterior_color,
            'interior_color': vehicle.interior_color,
            'year': vehicle.year,
            'make': vehicle.make,
            'model': vehicle.model,
        }

    path = _image_path_for(image)
    try:
        result = analyze_vehicle_image(
            path,
            features_text=features_text,
            vehicle_context=context,
            max_highlights=int(os.environ.get('PHOTO_HIGHLIGHTS_MAX', '8')),
        )
        # Replace auto-generated highlights; keep manual ones
        existing = list(image.highlights or [])
        for h in existing:
            if (h.source or 'auto') == 'auto':
                db.session.delete(h)

        for idx, item in enumerate(result.get('highlights') or []):
            row = VehicleImageHighlight(
                vehicle_image_id=image.id,
                x_pct=item.get('x_pct', 50),
                y_pct=item.get('y_pct', 50),
                label=(item.get('label') or 'Detail')[:120],
                category=(item.get('category') or 'detail')[:32],
                description=item.get('description'),
                icon=(item.get('icon') or 'info-circle')[:64],
                severity=(item.get('severity') or 'info')[:32],
                confidence=item.get('confidence'),
                source='auto',
                order_index=int(item.get('order_index', idx) or idx),
                is_visible=True,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.session.add(row)

        image.highlight_status = 'ready'
        image.highlight_error = None
        image.highlight_scene = (result.get('scene') or '')[:64] or None
        image.highlight_analyzed_at = utcnow()
        image.highlight_version = int(result.get('analysis_version') or ANALYSIS_VERSION)
        db.session.add(image)

        job.status = 'completed'
        job.last_error = None
        job.result_summary = (
            f"{len(result.get('highlights') or [])} highlights; "
            f"scene={result.get('scene')}; engine={result.get('engine')}"
        )[:500]
        job.finished_at = utcnow()
        job.lease_expires_at = None
        job.updated_at = utcnow()
        db.session.add(job)
        db.session.commit()
        logger.info(
            'Highlight job %s completed for image %s (%s)',
            job.id, image.id, job.result_summary,
        )
        return True
    except Exception as exc:
        db.session.rollback()
        # Re-load after rollback
        job = db.session.get(type(job), job.id)
        image = db.session.get(VehicleImage, job.vehicle_image_id) if job else None
        err = f'{exc.__class__.__name__}: {exc}'
        tb = traceback.format_exc(limit=8)
        logger.exception('Highlight job failed for image %s', getattr(image, 'id', None))

        attempts = int(getattr(job, 'attempts', 1) or 1)
        max_attempts = int(getattr(job, 'max_attempts', 3) or 3)
        if job:
            job.last_error = (err + '\n' + tb)[:2000]
            job.updated_at = utcnow()
            if attempts >= max_attempts:
                job.status = 'failed'
                job.finished_at = utcnow()
                job.lease_expires_at = None
                if image:
                    image.highlight_status = 'failed'
                    image.highlight_error = err[:500]
                    db.session.add(image)
            else:
                # Exponential backoff requeue
                delay = min(900, 15 * (2 ** max(0, attempts - 1)))
                job.status = 'queued'
                job.locked_by = None
                job.locked_at = None
                job.lease_expires_at = None
                job.scheduled_at = utcnow() + timedelta(seconds=delay)
                if image:
                    image.highlight_status = 'pending'
                    image.highlight_error = f'Retrying ({attempts}/{max_attempts}): {err}'[:500]
                    db.session.add(image)
            db.session.add(job)
            db.session.commit()
        return False


def run_worker_once(lease_seconds: int = 300) -> bool:
    """Claim and process a single job. Returns True if a job was processed."""
    job = claim_next_job(lease_seconds=lease_seconds)
    if not job:
        return False
    # Skip if scheduled in the future (backoff)
    if job.scheduled_at and job.scheduled_at > utcnow() and job.attempts > 0 and job.status == 'running':
        # Shouldn't normally claim future jobs; release
        job.status = 'queued'
        job.locked_by = None
        job.locked_at = None
        job.lease_expires_at = None
        from app import db
        db.session.add(job)
        db.session.commit()
        return False
    process_job(job)
    return True


def queue_stats() -> dict:
    from app.models import PhotoHighlightJob, VehicleImage
    from sqlalchemy import func
    from app import db

    def count_jobs(status):
        return db.session.query(func.count(PhotoHighlightJob.id)).filter_by(status=status).scalar() or 0

    def count_images(status):
        return db.session.query(func.count(VehicleImage.id)).filter_by(highlight_status=status).scalar() or 0

    return {
        'jobs_queued': count_jobs('queued'),
        'jobs_running': count_jobs('running'),
        'jobs_failed': count_jobs('failed'),
        'jobs_completed': count_jobs('completed'),
        'images_pending': count_images('pending'),
        'images_processing': count_images('processing'),
        'images_ready': count_images('ready'),
        'images_failed': count_images('failed'),
    }
