"""Google Business Profile review helpers.

Reviews are stored in the existing Review model (source='Google') so they can be
moderated in admin. Optional Places API sync fills them when GOOGLE_PLACES_API_KEY
and a Place ID are configured.

Scraping Google Search/Maps is unreliable and blocked; use the official Places
Details API or paste approved reviews in Admin → Reviews with Source = Google.
"""

from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

from app import db
from app.models import Review, SiteSetting

logger = logging.getLogger(__name__)

GOOGLE_SOURCE = 'Google'
DEFAULT_BUSINESS_URL = 'https://share.google/jRec671UblSEwqZTa'


def google_business_url() -> str:
    return (
        (SiteSetting.get('google_business_url') or '').strip()
        or (current_app.config.get('GOOGLE_BUSINESS_URL') or '').strip()
        or DEFAULT_BUSINESS_URL
    )


def google_place_id() -> str:
    return (
        (SiteSetting.get('google_place_id') or '').strip()
        or (current_app.config.get('GOOGLE_PLACE_ID') or '').strip()
    )


def places_api_key() -> str:
    return (current_app.config.get('GOOGLE_PLACES_API_KEY') or '').strip()


def stored_google_rating():
    raw = (SiteSetting.get('google_rating') or '').strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if 1.0 <= value <= 5.0:
        return round(value, 1)
    return None


def stored_google_review_count():
    raw = (SiteSetting.get('google_review_count') or '').strip()
    if not raw:
        return None
    try:
        count = int(raw)
    except ValueError:
        return None
    return count if count >= 0 else None


def approved_dealership_reviews(limit=6, prefer_google=True):
    """Approved lot-level reviews for public display."""
    query = (
        Review.query
        .filter_by(is_approved=True)
        .filter(Review.vehicle_id.is_(None))
    )
    if prefer_google:
        google_rows = (
            query.filter_by(source=GOOGLE_SOURCE)
            .order_by(Review.is_featured.desc(), Review.created_at.desc())
            .limit(limit)
            .all()
        )
        if google_rows:
            return google_rows
    return (
        query.order_by(Review.is_featured.desc(), Review.created_at.desc())
        .limit(limit)
        .all()
    )


def google_reviews_summary(limit=6):
    """Public summary for templates: rating, count, profile URL, display reviews."""
    reviews = approved_dealership_reviews(limit=limit, prefer_google=True)
    rating = stored_google_rating()
    count = stored_google_review_count()
    google_only = [r for r in reviews if (r.source or '') == GOOGLE_SOURCE]
    if google_only and rating is None:
        rating = round(sum(r.rating for r in google_only) / len(google_only), 1)
    return {
        'url': google_business_url(),
        'rating': rating,
        'count': count,
        'reviews': reviews,
        'place_id': google_place_id(),
        'can_sync': bool(places_api_key() and google_place_id()),
        'has_google_reviews': bool(google_only),
    }


def ensure_google_profile_defaults():
    """Persist the public GBP share URL when none is configured."""
    changed = False
    if not (SiteSetting.get('google_business_url') or '').strip():
        SiteSetting.set('google_business_url', DEFAULT_BUSINESS_URL)
        changed = True
    if changed:
        db.session.commit()
    return changed


def _review_fingerprint(author_name: str, content: str) -> str:
    payload = f'{author_name.strip().lower()}|{content.strip().lower()}'
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]


def upsert_google_review(
    *,
    author_name: str,
    rating: int,
    content: str,
    title: str | None = None,
    featured: bool = False,
):
    """Insert or refresh an approved Google review without duplicating the same text."""
    author = (author_name or 'Google reviewer').strip()[:128] or 'Google reviewer'
    body = ' '.join((content or '').split())
    if not body:
        return None, False
    body = body[:5000]
    rating = max(1, min(5, int(rating or 5)))
    title = (title or '').strip()[:160] or None

    existing = (
        Review.query
        .filter_by(source=GOOGLE_SOURCE, author_name=author, vehicle_id=None)
        .all()
    )
    fingerprint = _review_fingerprint(author, body)
    for row in existing:
        if _review_fingerprint(row.author_name, row.content) == fingerprint:
            changed = False
            if row.rating != rating:
                row.rating = rating
                changed = True
            if title and row.title != title:
                row.title = title
                changed = True
            if not row.is_approved:
                row.is_approved = True
                changed = True
            if featured and not row.is_featured:
                row.is_featured = True
                changed = True
            return row, changed

    review = Review(
        author_name=author,
        rating=rating,
        title=title,
        content=body,
        source=GOOGLE_SOURCE,
        is_approved=True,
        is_featured=bool(featured),
        vehicle_id=None,
    )
    db.session.add(review)
    return review, True


def fetch_place_details():
    """Call Google Places Details for rating + up to 5 reviews."""
    key = places_api_key()
    place_id = google_place_id()
    if not key or not place_id:
        raise RuntimeError('Set GOOGLE_PLACES_API_KEY and google_place_id to sync reviews.')

    query = urllib.parse.urlencode({
        'place_id': place_id,
        'fields': 'name,rating,user_ratings_total,reviews,url',
        'reviews_sort': 'newest',
        'key': key,
    })
    url = f'https://maps.googleapis.com/maps/api/place/details/json?{query}'
    request = urllib.request.Request(
        url,
        headers={'Accept': 'application/json', 'User-Agent': 'MarshallAuto/1.0'},
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f'Google Places request failed: {exc}') from exc

    if payload.get('status') != 'OK':
        raise RuntimeError(payload.get('error_message') or payload.get('status') or 'Places API error')
    return payload.get('result') or {}


def sync_google_reviews_from_places():
    """Pull Place Details reviews into the Review table and store summary stats."""
    result = fetch_place_details()
    rating = result.get('rating')
    total = result.get('user_ratings_total')
    maps_url = (result.get('url') or '').strip()
    changed = 0

    if rating is not None:
        SiteSetting.set('google_rating', str(round(float(rating), 1)))
    if total is not None:
        SiteSetting.set('google_review_count', str(int(total)))
    if maps_url:
        SiteSetting.set('google_business_url', maps_url)

    for index, item in enumerate(result.get('reviews') or []):
        text = (item.get('text') or '').strip()
        if not text:
            continue
        _, did_change = upsert_google_review(
            author_name=item.get('author_name') or 'Google reviewer',
            rating=item.get('rating') or 5,
            content=text,
            title=None,
            featured=index < 3,
        )
        if did_change:
            changed += 1

    db.session.commit()
    return {
        'imported_or_updated': changed,
        'rating': stored_google_rating(),
        'count': stored_google_review_count(),
        'name': result.get('name'),
        'url': maps_url or google_business_url(),
    }
