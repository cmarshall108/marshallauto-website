import os
import re
import secrets
import smtplib
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import lru_cache

from flask import current_app, request
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func

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
    """Save and resize an uploaded image, returning (filename, width, height) or (None, None, None)."""
    if not file_obj or not getattr(file_obj, 'filename', None):
        return None, None, None

    if not allowed_file(file_obj.filename, current_app.config['ALLOWED_IMAGE_EXTENSIONS']):
        return None, None, None

    if not _validate_image_magic(file_obj):
        current_app.logger.warning('Rejected non-image upload: %s', file_obj.filename)
        return None, None, None

    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    # Normalize extension for JPEG
    if ext == 'jpeg':
        ext = 'jpg'

    width = width or current_app.config.get('IMAGE_WIDTHS', {}).get('detail', 1200)
    quality = quality or current_app.config.get('IMAGE_QUALITY', 85)

    filename = f"{utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}.{ext}"
    upload_path = os.path.join(current_app.config['UPLOAD_FOLDER'], subfolder)
    os.makedirs(upload_path, exist_ok=True)
    full_path = os.path.join(upload_path, filename)

    try:
        img = Image.open(file_obj)
        img = ImageOps.exif_transpose(img)
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

        save_kwargs = {'optimize': True}
        if ext in ('jpg', 'jpeg', 'webp'):
            save_kwargs['quality'] = quality
        if ext == 'webp':
            save_kwargs['method'] = 6

        img.save(full_path, **save_kwargs)

        # Generate card/thumbnail variants for responsive listings
        _save_image_variants(img, upload_path, filename)

        return filename, img.width, img.height
    except Exception as e:
        current_app.logger.error('Image save failed: %s', e)
        return None, None, None


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
    if not target:
        return False
    from urllib.parse import urlparse
    ref = urlparse(request.host_url)
    test = urlparse(target)
    # Relative path
    if not test.netloc and test.path.startswith('/'):
        return not test.path.startswith('//')
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
        username = current_app.config.get('MAIL_USERNAME') or None
        password = current_app.config.get('MAIL_PASSWORD') or None
        with smtplib.SMTP(server, port, timeout=10) as smtp:
            if use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)
    except Exception as e:
        current_app.logger.error('Lead email failed: %s', e)


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
    data = {
        "@context": "https://schema.org",
        "@type": "AutoDealer",
        "name": current_app.config['BUSINESS_NAME'],
        "url": current_app.config['SITE_URL'],
        "logo": f"{current_app.config['SITE_URL']}/static/images/logo-icon.png",
        "image": f"{current_app.config['SITE_URL']}/static/images/og-default.jpg",
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
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": current_app.config['BUSINESS_NAME'],
        "url": current_app.config['SITE_URL'],
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": f"{current_app.config['SITE_URL']}/inventory?q={{search_term_string}}"
            },
            "query-input": "required name=search_term_string"
        },
        "publisher": {
            "@type": "AutoDealer",
            "name": current_app.config['BUSINESS_NAME'],
            "logo": {
                "@type": "ImageObject",
                "url": f"{current_app.config['SITE_URL']}/static/images/logo-icon.png"
            }
        }
    }


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

    next_year = utcnow().year + 1
    offer = {
        "@type": "Offer",
        "priceCurrency": "USD",
        "price": str(vehicle.display_price),
        "priceValidUntil": f"{next_year}-12-31",
        "itemCondition": item_condition,
        "availability": "https://schema.org/InStock" if vehicle.status == 'available' else "https://schema.org/OutOfStock",
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
        "name": vehicle.title,
        "image": images[:8],
        "description": vehicle.seo_description or vehicle.description or f"{vehicle.title} for sale at {current_app.config['BUSINESS_NAME']}",
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
