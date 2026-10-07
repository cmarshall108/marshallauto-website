import os
import json
import logging
import re
import secrets
import shutil
import smtplib
import subprocess
import sys
import tempfile
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import lru_cache

from flask import current_app, request
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func

try:
    from pillow_heif import register_heif_opener
    register_heif_opener(decode_threads=1)
except ImportError:  # pragma: no cover - HEIC uploads will be rejected as unreadable
    pass

HEIC_EXTENSIONS = {'heic', 'heif'}

# In-process rate limit buckets: key -> deque of timestamps
_rate_buckets = defaultdict(deque)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def allowed_file(filename, allowed_extensions):
    return bool(filename) and '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in allowed_extensions


def client_ip():
    forwarded = request.headers.get('X-Forwarded-For', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = request.headers.get('X-Real-IP', '').strip()
    if real_ip:
        return real_ip
    return request.remote_addr or 'unknown'


def rate_limit_exceeded(bucket_key, limit, window_seconds):
    """Simple sliding-window rate limiter (per process/worker)."""
    now = time.time()
    bucket = _rate_buckets[bucket_key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _validate_image_magic(file_obj):
    """Ensure uploaded bytes are a real image via Pillow."""
    pos = file_obj.tell()
    try:
        file_obj.seek(0)
        img = Image.open(file_obj)
        img.verify()
        file_obj.seek(0)
        return True
    except (UnidentifiedImageError, OSError, ValueError):
        try:
            file_obj.seek(pos)
        except Exception:
            pass
        return False


def _validate_pdf_magic(file_obj):
    pos = file_obj.tell()
    try:
        file_obj.seek(0)
        header = file_obj.read(5)
        file_obj.seek(0)
        return header == b'%PDF-'
    except Exception:
        try:
            file_obj.seek(pos)
        except Exception:
            pass
        return False


def save_uploaded_image(file_obj, subfolder='vehicles', width=None, quality=None):
    """Save a normalized image; isolate native HEIC decoding from the caller."""
    if file_obj and getattr(file_obj, 'filename', None) and \
            allowed_file(file_obj.filename, HEIC_EXTENSIONS):
        return _save_heic_isolated(file_obj, subfolder, width, quality)
    return _save_uploaded_image_in_process(file_obj, subfolder, width, quality)


def _save_heic_isolated(file_obj, subfolder, width, quality):
    """Keep native HEIC decoding and its allocations out of the web process."""
    folder = os.path.join(current_app.config['PRIVATE_UPLOAD_FOLDER'], 'image_conversion')
    os.makedirs(folder, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder) as staging:
        source_path = os.path.join(staging, 'input.heic')
        file_obj.save(source_path)
        settings = {
            'UPLOAD_FOLDER': staging,
            'ALLOWED_IMAGE_EXTENSIONS': list(current_app.config['ALLOWED_IMAGE_EXTENSIONS']),
            'IMAGE_WIDTHS': current_app.config.get('IMAGE_WIDTHS', {}),
            'IMAGE_QUALITY': quality or current_app.config.get('IMAGE_QUALITY', 85),
            'HEIC_CONVERSION_MEMORY_MB': current_app.config.get('HEIC_CONVERSION_MEMORY_MB', 512),
        }
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'app.image_upload_worker', source_path,
                 json.dumps(settings), str(width or settings['IMAGE_WIDTHS'].get('detail', 1200))],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                capture_output=True, text=True,
                timeout=current_app.config.get('HEIC_CONVERSION_TIMEOUT', 45),
            )
            if result.returncode != 0:
                current_app.logger.error(
                    'HEIC conversion failed for %s (exit %s): %s',
                    file_obj.filename, result.returncode, result.stderr[-2000:],
                )
                return None, None, None
            metadata = json.loads(result.stdout)
            filename = metadata['filename']
            output_folder = os.path.join(staging, 'converted')
            destination = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
            os.makedirs(destination, exist_ok=True)
            for name in os.listdir(output_folder):
                os.replace(os.path.join(output_folder, name), os.path.join(destination, name))
            return filename, metadata['width'], metadata['height']
        except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
            current_app.logger.error('HEIC conversion failed for %s: %s', file_obj.filename, exc)
            return None, None, None


def _save_uploaded_image_in_process(file_obj, subfolder='vehicles', width=None, quality=None):
    """Save and resize an uploaded image, returning (filename, width, height) or (None, None, None)."""
    if not file_obj or not getattr(file_obj, 'filename', None):
        return None, None, None

    if not allowed_file(file_obj.filename, current_app.config['ALLOWED_IMAGE_EXTENSIONS']):
        return None, None, None

    if not _validate_image_magic(file_obj):
        current_app.logger.warning('Rejected non-image upload: %s', file_obj.filename)
        return None, None, None

    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    # Browsers can't display HEIC, so store it (and plain .jpeg) as .jpg
    if ext == 'jpeg' or ext in HEIC_EXTENSIONS:
        ext = 'jpg'

    width = width or current_app.config.get('IMAGE_WIDTHS', {}).get('detail', 1200)
    quality = quality or current_app.config.get('IMAGE_QUALITY', 85)

    filename = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}.{ext}"
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(upload_path, exist_ok=True)
    full_path = os.path.join(upload_path, filename)

    try:
        with Image.open(file_obj) as source:
            img = _normalize_image(source, width)
            try:
                save_kwargs = {'optimize': True}
                if ext in ('jpg', 'jpeg', 'webp'):
                    save_kwargs['quality'] = quality
                if ext == 'webp':
                    save_kwargs['method'] = 6

                img.save(full_path, **save_kwargs)
                _save_image_variants(img, upload_path, filename)
                return filename, img.width, img.height
            finally:
                if img is not source:
                    img.close()
    except Exception as e:
        current_app.logger.error('Image save failed: %s', e)
        return None, None, None


def _normalize_image(img, width):
    """Apply EXIF rotation, flatten to RGB, and cap the width."""
    # Shrink before rotation/color conversion to avoid full-resolution copies.
    rotated = img.getexif().get(274) in (5, 6, 7, 8)
    oriented_width = img.height if rotated else img.width
    if oriented_width > width:
        ratio = width / float(oriented_width)
        target = (max(1, int(img.width * ratio)), max(1, int(img.height * ratio)))
        img.thumbnail(target, Image.Resampling.LANCZOS)
    ImageOps.exif_transpose(img, in_place=True)
    if img.mode in ('RGBA', 'P', 'LA'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        alpha = img.split()[-1] if img.mode in ('RGBA', 'LA') else None
        background.paste(img, mask=alpha)
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')

    if img.width > width:
        ratio = width / float(img.width)
        new_height = max(1, int(img.height * ratio))
        img = img.resize((width, new_height), Image.Resampling.LANCZOS)
    return img


def convert_heic_vehicle_images(dry_run=False):
    """Re-encode stored HEIC/HEIF vehicle photos as JPEG and repoint their DB rows."""
    from app import db
    from app.admin import _delete_vehicle_image_file
    from app.highlight_jobs import enqueue_image_highlight_job
    from app.models import VehicleImage

    stats = {'found': 0, 'converted': 0, 'failed': 0}
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'vehicles')
    width = current_app.config.get('IMAGE_WIDTHS', {}).get('detail', 1200)
    quality = current_app.config.get('IMAGE_QUALITY', 85)
    highlights_on = current_app.config.get('PHOTO_HIGHLIGHTS_ENABLED', True)

    candidates = VehicleImage.query.filter(func.lower(VehicleImage.filename).like('%.hei_')).all()
    for image in candidates:
        old_name = os.path.basename(image.filename or '')
        if '.' not in old_name or old_name.rsplit('.', 1)[1].lower() not in HEIC_EXTENSIONS:
            continue
        stats['found'] += 1
        if dry_run:
            continue

        new_name = f"{old_name.rsplit('.', 1)[0]}.jpg"
        if os.path.exists(os.path.join(upload_path, new_name)):
            new_name = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}.jpg"
        try:
            with Image.open(os.path.join(upload_path, old_name)) as src:
                img = _normalize_image(src, width)
                try:
                    img.save(os.path.join(upload_path, new_name), format='JPEG', quality=quality, optimize=True)
                    _save_image_variants(img, upload_path, new_name)
                    image_width, image_height = img.size
                finally:
                    if img is not src:
                        img.close()
        except Exception as exc:
            current_app.logger.error('HEIC conversion failed for image %s (%s): %s', image.id, old_name, exc)
            stats['failed'] += 1
            continue

        image.filename = new_name
        image.width, image.height = image_width, image_height
        requeue = highlights_on and image.highlight_status in ('pending', 'failed')
        if requeue:
            image.highlight_status = 'pending'
            image.highlight_error = None
        db.session.commit()
        _delete_vehicle_image_file(VehicleImage(filename=old_name))
        stats['converted'] += 1

        if requeue:
            try:
                enqueue_image_highlight_job(image.id, force=True)
            except Exception as exc:
                current_app.logger.warning('Failed to enqueue highlight job for image %s: %s', image.id, exc)
    return stats


def _save_image_variants(img, upload_path, filename):
    """Write smaller variants used by listing cards."""
    name, ext = filename.rsplit('.', 1)
    widths = current_app.config.get('IMAGE_WIDTHS', {})
    quality = current_app.config.get('IMAGE_QUALITY', 85)
    for label, target_w in widths.items():
        if label == 'detail':
            continue
        if img.width <= target_w:
            continue
        ratio = target_w / float(img.width)
        new_h = max(1, int(img.height * ratio))
        variant = img.resize((target_w, new_h), Image.Resampling.LANCZOS)
        variant_name = f'{name}_{label}.{ext}'
        kwargs = {'optimize': True}
        if ext in ('jpg', 'jpeg', 'webp'):
            kwargs['quality'] = quality
        try:
            variant.save(os.path.join(upload_path, variant_name), **kwargs)
        except Exception as e:
            current_app.logger.warning('Variant save failed (%s): %s', label, e)
        finally:
            variant.close()


def save_uploaded_pdf(file_obj):
    if not file_obj or not getattr(file_obj, 'filename', None):
        return None
    if not allowed_file(file_obj.filename, current_app.config['ALLOWED_PDF_EXTENSIONS']):
        return None
    if not _validate_pdf_magic(file_obj):
        current_app.logger.warning('Rejected non-PDF upload: %s', file_obj.filename)
        return None

    filename = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}.pdf"
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'carfax')
    os.makedirs(upload_path, exist_ok=True)
    full_path = os.path.join(upload_path, filename)
    file_obj.save(full_path)
    return filename


def _save_license_image_bytes(raw_bytes):
    """Shared save path for license images, whatever the source (file or camera capture)."""
    import io

    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img.verify()
        img = Image.open(io.BytesIO(raw_bytes))  # re-open after verify() invalidates the handle
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    img = ImageOps.exif_transpose(img)
    if img.mode != 'RGB':
        img = img.convert('RGB')

    # Cap resolution — this is a document photo, not a gallery image; no need for full camera res.
    max_width = 1600
    if img.width > max_width:
        ratio = max_width / float(img.width)
        img = img.resize((max_width, max(1, int(img.height * ratio))), Image.Resampling.LANCZOS)

    filename = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(8)}.jpg"
    upload_path = os.path.join(current_app.config['PRIVATE_UPLOAD_FOLDER'], 'licenses')
    os.makedirs(upload_path, exist_ok=True)
    try:
        img.save(os.path.join(upload_path, filename), format='JPEG', quality=85, optimize=True)
        return filename
    except Exception as e:
        current_app.logger.error('License image save failed: %s', e)
        return None


def save_uploaded_license_image(file_obj):
    """Save a driver's license photo/scan uploaded as a regular file field."""
    if not file_obj or not getattr(file_obj, 'filename', None):
        return None
    if not allowed_file(file_obj.filename, current_app.config['ALLOWED_IMAGE_EXTENSIONS']):
        return None
    if not _validate_image_magic(file_obj):
        current_app.logger.warning('Rejected non-image license upload: %s', file_obj.filename)
        return None
    file_obj.seek(0)
    return _save_license_image_bytes(file_obj.read())


def save_license_image_data_url(data_url):
    """Save a driver's license photo captured in-browser (getUserMedia -> canvas -> base64 data URL)."""
    import base64

    if not data_url or not isinstance(data_url, str) or not data_url.startswith('data:image/'):
        return None
    try:
        header, encoded = data_url.split(',', 1)
        raw_bytes = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        return None
    # Reasonable cap so a rogue/huge payload can't be posted as "base64 text"
    if len(raw_bytes) > 15 * 1024 * 1024:
        return None
    return _save_license_image_bytes(raw_bytes)


def delete_license_image(filename):
    if not filename:
        return
    try:
        path = os.path.join(current_app.config['PRIVATE_UPLOAD_FOLDER'], 'licenses', os.path.basename(filename))
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def slugify(text):
    text = str(text).lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    text = re.sub(r'^-+|-+$', '', text)
    return text


def format_price(value):
    try:
        return f"${int(float(value)):,}"
    except (TypeError, ValueError):
        return '$0'


def format_mileage(value):
    try:
        return f"{int(value):,} mi"
    except (TypeError, ValueError):
        return '0 mi'


# Used-car retail cue: under this odometer reading shows a "LOW MILES!" badge.
LOW_MILEAGE_THRESHOLD = 75000


def is_low_mileage(value, threshold: int = LOW_MILEAGE_THRESHOLD) -> bool:
    """True when mileage is a non-negative int strictly below the threshold."""
    try:
        miles = int(value)
    except (TypeError, ValueError):
        return False
    return 0 <= miles < int(threshold)


def parse_optional_int(value):
    """Coerce form/query values to int or None safely."""
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def is_safe_redirect(target):
    """Allow only relative same-host redirects (prevent open redirect)."""
    if not target or not isinstance(target, str):
        return False

    from urllib.parse import unquote, urlparse

    candidate = target.strip()
    if not candidate:
        return False
    if any(ch in candidate for ch in '\x00\r\n'):
        return False

    decoded = unquote(candidate)
    if '\\' in candidate or '\\' in decoded:
        return False
    if decoded.startswith('//') or decoded.startswith('\\\\'):
        return False

    ref = urlparse(request.host_url)
    test = urlparse(candidate)

    if not test.netloc and not test.scheme:
        return test.path.startswith('/') and not test.path.startswith('//')

    return test.scheme in ('http', 'https') and ref.netloc == test.netloc


def sanitize_gsc_tag(raw):
    """
    Allow only a Google site-verification meta tag (or bare content token).
    Prevents stored XSS via admin settings.
    """
    if not raw:
        return ''
    raw = raw.strip()
    # Bare verification token
    if re.fullmatch(r'[A-Za-z0-9_-]{10,100}', raw):
        return f'<meta name="google-site-verification" content="{raw}">'
    match = re.fullmatch(
        r'<meta\s+name=["\']google-site-verification["\']\s+content=["\']([A-Za-z0-9_-]{10,100})["\']\s*/?>',
        raw,
        flags=re.IGNORECASE,
    )
    if match:
        token = match.group(1)
        return f'<meta name="google-site-verification" content="{token}">'
    return ''


def notify_new_lead(lead):
    """Optionally email staff about a new lead when SMTP is configured."""
    if not current_app.config.get('SEND_LEAD_EMAIL'):
        return
    server = current_app.config.get('MAIL_SERVER')
    recipient = current_app.config.get('BUSINESS_EMAIL')
    sender = current_app.config.get('MAIL_DEFAULT_SENDER') or recipient
    if not server or not recipient or not sender:
        return
    try:
        msg = EmailMessage()
        msg['Subject'] = f"New lead from {lead.name}"
        msg['From'] = sender
        msg['To'] = recipient
        body = (
            f"Name: {lead.name}\n"
            f"Email: {lead.email}\n"
            f"Phone: {lead.phone or 'N/A'}\n"
            f"Source: {lead.source}\n"
            f"Vehicle ID: {lead.vehicle_id or 'N/A'}\n\n"
            f"{lead.message or ''}\n"
        )
        msg.set_content(body)
        port = current_app.config.get('MAIL_PORT', 587)
        use_tls = current_app.config.get('MAIL_USE_TLS', True)
        use_ssl = current_app.config.get('MAIL_USE_SSL', False)
        username = current_app.config.get('MAIL_USERNAME') or None
        password = current_app.config.get('MAIL_PASSWORD') or None
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(server, port, timeout=10) as smtp:
            if not use_ssl and use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
    except Exception as e:
        current_app.logger.error('Lead email failed: %s', e)


_YOUTUBE_RE = re.compile(
    r'(?:youtube\.com/(?:watch\?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{6,20})'
)
_VIMEO_RE = re.compile(r'vimeo\.com/(?:video/)?(\d+)')


def parse_video_url(raw_url):
    """Classify a vehicle walkaround video URL for safe embedding.

    Returns {'kind': 'youtube'|'vimeo'|'file', 'embed_url': str, 'video_id': str|None} or None.
    """
    if not raw_url:
        return None
    url = raw_url.strip()
    if not url:
        return None
    match = _YOUTUBE_RE.search(url)
    if match:
        return {
            'kind': 'youtube',
            'embed_url': f'https://www.youtube-nocookie.com/embed/{match.group(1)}',
            'video_id': match.group(1),
        }
    match = _VIMEO_RE.search(url)
    if match:
        return {
            'kind': 'vimeo',
            'embed_url': f'https://player.vimeo.com/video/{match.group(1)}',
            'video_id': match.group(1),
        }
    # Locally-stored (self-hosted, compressed) video — relative /static/... path, no scheme.
    if url.startswith('/') and url.lower().endswith(('.mp4', '.webm', '.ogg', '.mov', '.m4v')):
        return {'kind': 'file', 'embed_url': url, 'video_id': None}
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme in ('http', 'https') and parsed.path.lower().endswith(('.mp4', '.webm', '.ogg', '.mov', '.m4v')):
        return {'kind': 'file', 'embed_url': url, 'video_id': None}
    return None


ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'webm', 'mov', 'm4v'}
# Heavy compression target: this VPS has very limited disk space. Favor small files
# over quality — 854px-wide H.264, low bitrate, mono audio. Fine for a short walkaround clip.
VIDEO_MAX_WIDTH = 854
VIDEO_CRF = 32
VIDEO_AUDIO_BITRATE = '64k'


def _ffmpeg_path():
    system_ffmpeg = shutil.which('ffmpeg')
    if system_ffmpeg:
        return system_ffmpeg
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except (ImportError, RuntimeError) as exc:
        logging.getLogger(__name__).error('No system or bundled FFmpeg available: %s', exc)
        return None


def save_uploaded_video(file_obj, subfolder='vehicles/videos'):
    """Save an uploaded walkaround video, heavily compressed via ffmpeg.

    Returns the stored filename, or None if the upload was rejected/failed
    (missing/invalid file, or ffmpeg unavailable/failed — caller should flash a
    message rather than silently keeping an uncompressed file on a low-disk VPS).
    """
    if not file_obj or not getattr(file_obj, 'filename', None):
        return None
    if not allowed_file(file_obj.filename, ALLOWED_VIDEO_EXTENSIONS):
        return None

    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        current_app.logger.error(
            'ffmpeg not found on PATH — refusing to store an uncompressed video (disk space is limited).'
        )
        return None

    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(upload_path, exist_ok=True)

    token = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"
    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    raw_path = os.path.join(upload_path, f'{token}_raw.{ext}')
    final_name = f'{token}.mp4'
    final_path = os.path.join(upload_path, final_name)

    file_obj.save(raw_path)
    try:
        if compress_video_file(raw_path, final_path):
            return final_name
        return None
    finally:
        if os.path.exists(raw_path):
            try:
                os.remove(raw_path)
            except OSError as exc:
                current_app.logger.warning('Failed to delete raw video %s: %s', raw_path, exc)


def compress_video_file(raw_path, final_path):
    """Compress a staged video; never publish incomplete output."""
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        current_app.logger.error('Video compression unavailable: ffmpeg not found.')
        return False
    succeeded = False
    try:
        result = subprocess.run(
            [
                ffmpeg, '-y', '-threads', '1', '-i', raw_path,
                '-vf', f"scale='min({VIDEO_MAX_WIDTH},iw)':-2",
                '-c:v', 'libx264', '-threads', '1', '-preset', 'veryfast', '-crf', str(VIDEO_CRF),
                '-c:a', 'aac', '-b:a', VIDEO_AUDIO_BITRATE, '-ac', '1',
                '-movflags', '+faststart',
                final_path,
            ],
            capture_output=True,
            timeout=600,
        )
        if result.returncode != 0 or not os.path.exists(final_path):
            current_app.logger.error(
                'Video compression failed: %s', result.stderr.decode('utf-8', 'ignore')[-800:]
            )
            return False
        succeeded = True
        return True
    except (subprocess.SubprocessError, OSError) as exc:
        current_app.logger.error('Video compression failed: %s', exc)
        return False
    finally:
        if not succeeded and os.path.exists(final_path):
            try:
                os.remove(final_path)
            except OSError as exc:
                current_app.logger.warning('Failed to delete incomplete video %s: %s', final_path, exc)


def delete_local_video_file(video_url):
    """Remove a self-hosted compressed video from disk when a vehicle is deleted/sold."""
    if not video_url or not video_url.startswith('/static/uploads/vehicles/videos/'):
        return  # external link (YouTube/Vimeo/etc.) — nothing stored on this VPS
    try:
        filename = os.path.basename(video_url)
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'vehicles', 'videos', filename)
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        current_app.logger.warning('Failed to delete video file %s: %s', video_url, exc)


def send_lead_confirmation(lead):
    """Optionally email the customer a confirmation their message was received."""
    if not current_app.config.get('SEND_LEAD_EMAIL'):
        return
    server = current_app.config.get('MAIL_SERVER')
    sender = current_app.config.get('MAIL_DEFAULT_SENDER') or current_app.config.get('BUSINESS_EMAIL')
    if not server or not sender or not lead.email:
        return
    try:
        msg = EmailMessage()
        business_name = current_app.config.get('BUSINESS_NAME', 'our dealership')
        business_phone = current_app.config.get('BUSINESS_PHONE', '')
        msg['Subject'] = f"We received your message — {business_name}"
        msg['From'] = sender
        msg['To'] = lead.email
        body = (
            f"Hi {lead.name.split(' ')[0] if lead.name else 'there'},\n\n"
            f"Thanks for reaching out to {business_name}! We've received your message and "
            f"a team member will follow up with you shortly.\n\n"
            f"If you need immediate assistance, call us at {business_phone}.\n\n"
            f"— {business_name}\n"
        )
        msg.set_content(body)
        port = current_app.config.get('MAIL_PORT', 587)
        use_tls = current_app.config.get('MAIL_USE_TLS', True)
        use_ssl = current_app.config.get('MAIL_USE_SSL', False)
        username = current_app.config.get('MAIL_USERNAME') or None
        password = current_app.config.get('MAIL_PASSWORD') or None
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(server, port, timeout=10) as smtp:
            if not use_ssl and use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
    except Exception as e:
        current_app.logger.error('Lead confirmation email failed: %s', e)


def _business_social_links():
    links = []
    for key in ['FACEBOOK_URL', 'INSTAGRAM_URL', 'YOUTUBE_URL']:
        value = current_app.config.get(key, '')
        if value:
            links.append(value)
    # Prefer DB overrides when present
    from app.models import SiteSetting
    for key, conf in [('facebook_url', 'FACEBOOK_URL'), ('instagram_url', 'INSTAGRAM_URL'), ('youtube_url', 'YOUTUBE_URL')]:
        val = SiteSetting.get(key) or current_app.config.get(conf, '')
        if val and val not in links:
            links.append(val)
    return links


def _parse_hours_range(hours_str):
    """Parse '9:00 AM - 5:00 PM' into 24h opens/closes; Closed -> None."""
    if not hours_str or hours_str.strip().lower() == 'closed':
        return None
    parts = re.split(r'\s*-\s*', hours_str.strip())
    if len(parts) != 2:
        return None

    def to_24(t):
        t = t.strip().upper().replace('.', '')
        for fmt in ('%I:%M %p', '%I %p', '%H:%M'):
            try:
                return datetime.strptime(t, fmt).strftime('%H:%M')
            except ValueError:
                continue
        return None

    opens = to_24(parts[0])
    closes = to_24(parts[1])
    if opens and closes:
        return opens, closes
    return None


def _opening_hours_spec():
    hours = current_app.config.get('BUSINESS_HOURS') or {}
    specs = []
    for day, value in hours.items():
        parsed = _parse_hours_range(value)
        if not parsed:
            continue
        opens, closes = parsed
        specs.append({
            "@type": "OpeningHoursSpecification",
            "dayOfWeek": day,
            "opens": opens,
            "closes": closes,
        })
    return specs


def _service_areas_schema():
    areas = current_app.config.get('SERVICE_AREAS', [])
    if not areas:
        return {
            "@type": "City",
            "name": current_app.config['BUSINESS_CITY'],
            "containedInPlace": {"@type": "State", "name": current_app.config['BUSINESS_STATE']}
        }
    return [
        {
            "@type": "City",
            "name": city,
            "containedInPlace": {"@type": "State", "name": state}
        }
        for city, state in areas
    ]


def structured_data_local_business():
    social = _business_social_links()
    site = current_app.config['SITE_URL'].rstrip('/')
    logo_url = f"{site}/static/images/logo-icon.png"
    data = {
        "@context": "https://schema.org",
        "@type": "AutoDealer",
        "@id": f"{site}/#dealership",
        "name": current_app.config['BUSINESS_NAME'],
        "url": site,
        "logo": {
            "@type": "ImageObject",
            "url": logo_url,
            "width": 512,
            "height": 512,
        },
        "image": [
            f"{site}/static/images/og-default.jpg",
            logo_url,
        ],
        "telephone": current_app.config['BUSINESS_PHONE'],
        "email": current_app.config['BUSINESS_EMAIL'],
        "address": {
            "@type": "PostalAddress",
            "streetAddress": current_app.config['BUSINESS_ADDRESS'],
            "addressLocality": current_app.config['BUSINESS_CITY'],
            "addressRegion": current_app.config['BUSINESS_STATE'],
            "postalCode": current_app.config['BUSINESS_ZIP'],
            "addressCountry": "US"
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": current_app.config['BUSINESS_LATITUDE'],
            "longitude": current_app.config['BUSINESS_LONGITUDE']
        },
        "openingHoursSpecification": _opening_hours_spec(),
        "priceRange": "$-$$$",
        "currenciesAccepted": "USD",
        "paymentAccepted": "Cash, Credit Card, Financing, Check",
        "areaServed": _service_areas_schema(),
        "knowsAbout": [
            "Used Cars",
            "Rebuilt Title Vehicles",
            "Auto Financing",
            "CarFax Reports",
            "Vehicle Trade-Ins"
        ],
        "hasOfferCatalog": {
            "@type": "OfferCatalog",
            "name": "Used Vehicles",
            "itemListElement": [
                {"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Used Cars"}},
                {"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Used Trucks"}},
                {"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Used SUVs"}},
                {"@type": "Offer", "itemOffered": {"@type": "Product", "name": "Rebuilt Title Vehicles"}}
            ]
        },
        "sameAs": social
    }
    aggregate = aggregate_rating_data()
    if aggregate:
        data['aggregateRating'] = aggregate
    return data


def aggregate_rating_data():
    """Return aggregate rating schema using SQL aggregation."""
    from app.models import Review
    row = (
        Review.query.filter_by(is_approved=True)
        .with_entities(func.avg(Review.rating), func.count(Review.id))
        .first()
    )
    if not row or not row[1]:
        return None
    avg, count = row
    return {
        "@type": "AggregateRating",
        "ratingValue": round(float(avg), 1),
        "bestRating": 5,
        "worstRating": 1,
        "ratingCount": int(count),
        "reviewCount": int(count)
    }


def structured_data_website():
    site = current_app.config['SITE_URL'].rstrip('/')
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": f"{site}/#website",
        "name": current_app.config['BUSINESS_NAME'],
        "url": site,
        "inLanguage": "en-US",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{site}/inventory?q={{search_term_string}}"
            },
            "query-input": "required name=search_term_string"
        },
        "publisher": {
            "@type": "AutoDealer",
            "@id": f"{site}/#dealership",
            "name": current_app.config['BUSINESS_NAME'],
            "logo": {
                "@type": "ImageObject",
                "url": f"{site}/static/images/logo-icon.png",
                "width": 512,
                "height": 512,
            }
        }
    }


def structured_data_item_list(vehicles, name, list_url=None):
    """JSON-LD ItemList for inventory / featured vehicle grids."""
    site = current_app.config['SITE_URL'].rstrip('/')
    elements = []
    for idx, vehicle in enumerate(vehicles or [], start=1):
        img = vehicle.primary_image()
        image_url = img.absolute_url if img else f"{site}/static/images/vehicle-placeholder.jpg"
        elements.append({
            "@type": "ListItem",
            "position": idx,
            "url": f"{site}/inventory/{vehicle.slug}",
            "item": {
                "@type": "Car",
                "name": vehicle.title,
                "url": f"{site}/inventory/{vehicle.slug}",
                "image": image_url,
                "brand": {"@type": "Brand", "name": vehicle.make},
                "model": vehicle.model,
                "vehicleModelDate": str(vehicle.year),
                "mileageFromOdometer": {
                    "@type": "QuantitativeValue",
                    "value": vehicle.mileage,
                    "unitCode": "SMI",
                },
                "offers": {
                    "@type": "Offer",
                    "priceCurrency": "USD",
                    "price": str(vehicle.display_price),
                    "availability": "https://schema.org/InStock",
                    "url": f"{site}/inventory/{vehicle.slug}",
                },
            },
        })
    data = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": name,
        "numberOfItems": len(elements),
        "itemListElement": elements,
    }
    if list_url:
        data['url'] = list_url
    return data


def structured_data_breadcrumb(items):
    """items: list of tuples (name, url) ending with current page."""
    item_list = []
    for idx, (name, url) in enumerate(items):
        item_list.append({
            "@type": "ListItem",
            "position": idx + 1,
            "name": name,
            "item": url if url else current_app.config['SITE_URL']
        })
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": item_list
    }


def structured_data_faq(questions):
    """questions: list of tuples (question, answer)."""
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": a
                }
            }
            for q, a in questions
        ]
    }


def structured_data_how_to(name, steps, description=None, total_time=None):
    data = {
        "@context": "https://schema.org",
        "@type": "HowTo",
        "name": name,
        "step": [
            {
                "@type": "HowToStep",
                "position": idx + 1,
                "name": step.get('name', f"Step {idx + 1}"),
                "text": step['text']
            }
            for idx, step in enumerate(steps)
        ]
    }
    if description:
        data['description'] = description
    if total_time:
        data['totalTime'] = total_time
    return data


def structured_data_vehicle(vehicle):
    img = vehicle.primary_image()
    image_url = img.absolute_url if img else f"{current_app.config['SITE_URL']}/static/images/vehicle-placeholder.jpg"
    images = [image_url]
    for image in vehicle.ordered_images():
        if image.absolute_url not in images:
            images.append(image.absolute_url)

    condition_map = {
        'used': 'https://schema.org/UsedCondition',
        'certified': 'https://schema.org/CertifiedPreOwnedCondition',
        'rebuilt': 'https://schema.org/DamagedCondition',
    }
    title_status = (vehicle.title_status or 'clean').lower()
    if title_status in ('rebuilt', 'salvage'):
        item_condition = 'https://schema.org/DamagedCondition'
    else:
        item_condition = condition_map.get(vehicle.condition, 'https://schema.org/UsedCondition')

    offer = {
        "@type": "Offer",
        "priceCurrency": "USD",
        "price": str(vehicle.display_price),
        "itemCondition": item_condition,
        "availability": (
            "https://schema.org/InStock" if vehicle.status == 'available'
            else "https://schema.org/SoldOut" if vehicle.status == 'sold'
            else "https://schema.org/OutOfStock"
        ),
        "url": f"{current_app.config['SITE_URL']}/inventory/{vehicle.slug}",
        "seller": {
            "@type": "AutoDealer",
            "name": current_app.config['BUSINESS_NAME']
        },
        "businessFunction": "http://purl.org/goodrelations/v1#Sell"
    }

    data = {
        "@context": "https://schema.org",
        "@type": "Car",
        "@id": f"{current_app.config['SITE_URL'].rstrip('/')}/inventory/{vehicle.slug}#vehicle",
        "name": vehicle.title,
        "image": images[:8],
        "description": vehicle.description or vehicle.seo_description or f"{vehicle.title} {'for sale' if vehicle.status == 'available' else 'archived listing'} at {current_app.config['BUSINESS_NAME']}",
        "sku": vehicle.stock_number or str(vehicle.id),
        "brand": {
            "@type": "Brand",
            "name": vehicle.make
        },
        "manufacturer": {
            "@type": "Organization",
            "name": vehicle.make
        },
        "model": vehicle.model,
        "vehicleModelDate": str(vehicle.year),
        "mileageFromOdometer": {
            "@type": "QuantitativeValue",
            "value": vehicle.mileage,
            "unitCode": "SMI"
        },
        "offers": offer,
        "color": vehicle.exterior_color or '',
        "vehicleInteriorColor": vehicle.interior_color or '',
        "fuelType": vehicle.fuel_type or '',
        "vehicleTransmission": vehicle.transmission or '',
        "driveWheelConfiguration": vehicle.drivetrain or '',
        "bodyType": vehicle.body_style or '',
        "url": f"{current_app.config['SITE_URL']}/inventory/{vehicle.slug}",
        "datePosted": vehicle.created_at.strftime('%Y-%m-%d') if vehicle.created_at else None,
        "areaServed": {
            "@type": "City",
            "name": current_app.config['BUSINESS_CITY'],
            "containedInPlace": {
                "@type": "State",
                "name": current_app.config['BUSINESS_STATE']
            }
        }
    }
    if vehicle.vin:
        data['vehicleIdentificationNumber'] = vehicle.vin
        data['mpn'] = vehicle.vin
    if vehicle.engine:
        data['vehicleEngine'] = {
            "@type": "EngineSpecification",
            "name": vehicle.engine
        }

    video_info = parse_video_url(vehicle.video_url) if vehicle.video_url else None
    if video_info:
        video_data = {
            "@type": "VideoObject",
            "name": f"{vehicle.title} Walkaround Video",
            "description": f"Video walkaround of this {vehicle.title} at {current_app.config['BUSINESS_NAME']}.",
            "thumbnailUrl": images[:1],
            "uploadDate": (
                vehicle.created_at.strftime('%Y-%m-%dT%H:%M:%S')
                if vehicle.created_at else utcnow().strftime('%Y-%m-%dT%H:%M:%S')
            ),
        }
        if video_info['kind'] == 'file':
            url = video_info['embed_url']
            if url.startswith('/'):
                url = f"{current_app.config['SITE_URL'].rstrip('/')}{url}"
            video_data['contentUrl'] = url
        else:
            video_data['embedUrl'] = video_info['embed_url']
        data['video'] = video_data

    approved_reviews = [r for r in (vehicle.reviews or []) if r.is_approved]
    if approved_reviews:
        avg = sum(r.rating for r in approved_reviews) / len(approved_reviews)
        data['aggregateRating'] = {
            "@type": "AggregateRating",
            "ratingValue": round(avg, 1),
            "bestRating": 5,
            "worstRating": 1,
            "ratingCount": len(approved_reviews)
        }
        data['review'] = [r.structured_data for r in approved_reviews[:5]]

    # Drop empty/None values for cleaner JSON-LD
    return {k: v for k, v in data.items() if v not in (None, '', [])}
