"""IndexNow notifications for faster Bing/Yandex (and partner) recrawl.

Google does not consume IndexNow today; submitting still helps multi-engine
discovery and is harmless when disabled or when the key is unset.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Iterable
from urllib.parse import urlsplit

from flask import current_app, has_app_context, url_for

logger = logging.getLogger(__name__)

INDEXNOW_ENDPOINT = 'https://api.indexnow.org/indexnow'


def indexnow_enabled() -> bool:
    if not has_app_context():
        return False
    if not current_app.config.get('INDEXNOW_ENABLED', True):
        return False
    key = (current_app.config.get('INDEXNOW_KEY') or '').strip()
    return bool(key)


def indexnow_key() -> str:
    return (current_app.config.get('INDEXNOW_KEY') or '').strip()


def indexnow_key_location() -> str:
    site = current_app.config['SITE_URL'].rstrip('/')
    return f"{site}/indexnow/{indexnow_key()}.txt"


def _normalize_url(url: str) -> str | None:
    if not url:
        return None
    site = current_app.config['SITE_URL'].rstrip('/')
    site_host = urlsplit(site).netloc.lower()
    parsed = urlsplit(url.strip())
    if not parsed.scheme:
        path = url if url.startswith('/') else f'/{url}'
        return f"{site}{path}"
    if parsed.netloc.lower() != site_host:
        # Rewrite accidental internal/proxy hosts onto the public origin.
        return f"{site}{parsed.path or '/'}"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"


def _public_url(endpoint: str, **values) -> str:
    """Build a public absolute URL using SITE_URL (not the proxy/server name)."""
    path = url_for(endpoint, **values)
    parsed = urlsplit(path)
    site = current_app.config['SITE_URL'].rstrip('/')
    if parsed.scheme:
        return f"{site}{parsed.path or '/'}"
    return f"{site}{path if path.startswith('/') else '/' + path}"


def collect_vehicle_urls(vehicle) -> list[str]:
    """Public URLs that change when a vehicle is created, updated, or removed."""
    urls: list[str] = []
    if getattr(vehicle, 'slug', None):
        urls.append(_public_url('main.vehicle_detail', slug=vehicle.slug))
    try:
        from app.vin_decode import is_valid_vin, normalize_vin
        vin = normalize_vin(getattr(vehicle, 'vin', None))
        if is_valid_vin(vin):
            urls.append(_public_url('main.vehicle_vin_record', vin=vin))
    except Exception:
        logger.debug('VIN URL skipped for IndexNow', exc_info=True)
    urls.append(_public_url('main.inventory'))
    urls.append(_public_url('main.index'))
    if getattr(vehicle, 'is_coming_soon', False) and getattr(vehicle, 'status', None) == 'available':
        urls.append(_public_url('main.coming_soon'))
    return urls


def collect_blog_urls(post) -> list[str]:
    urls = [_public_url('main.blog_list')]
    if getattr(post, 'slug', None) and getattr(post, 'is_published', False):
        urls.append(_public_url('main.blog_post_detail', slug=post.slug))
    return urls


def submit_urls(urls: Iterable[str], *, reason: str = '') -> dict:
    """POST changed URLs to IndexNow. Never raises; safe to call from request handlers."""
    if not indexnow_enabled():
        return {'ok': False, 'skipped': True, 'reason': 'disabled'}

    key = indexnow_key()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = _normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    if not normalized:
        return {'ok': False, 'skipped': True, 'reason': 'empty'}

    site = current_app.config['SITE_URL'].rstrip('/')
    host = urlsplit(site).netloc
    payload = {
        'host': host,
        'key': key,
        'keyLocation': indexnow_key_location(),
        'urlList': normalized[:10000],
    }
    body = json.dumps(payload).encode('utf-8')
    timeout = float(current_app.config.get('INDEXNOW_TIMEOUT', 3) or 3)
    req = urllib.request.Request(
        INDEXNOW_ENDPOINT,
        data=body,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, 'status', None) or resp.getcode()
            logger.info(
                'IndexNow submitted %s URL(s)%s status=%s',
                len(normalized),
                f' ({reason})' if reason else '',
                status,
            )
            return {'ok': 200 <= int(status) < 300, 'status': int(status), 'count': len(normalized)}
    except urllib.error.HTTPError as exc:
        logger.warning('IndexNow HTTP error %s: %s', exc.code, exc.reason)
        return {'ok': False, 'status': int(exc.code), 'error': str(exc.reason)}
    except Exception as exc:
        logger.warning('IndexNow request failed: %s', exc)
        return {'ok': False, 'error': str(exc)}


def notify_vehicle(vehicle, *, reason: str = 'vehicle') -> dict:
    try:
        return submit_urls(collect_vehicle_urls(vehicle), reason=reason)
    except Exception as exc:
        logger.warning('IndexNow vehicle notify failed: %s', exc)
        return {'ok': False, 'error': str(exc)}


def notify_blog(post, *, reason: str = 'blog', extra_urls: Iterable[str] | None = None) -> dict:
    try:
        urls = collect_blog_urls(post)
        if extra_urls:
            urls.extend(extra_urls)
        return submit_urls(urls, reason=reason)
    except Exception as exc:
        logger.warning('IndexNow blog notify failed: %s', exc)
        return {'ok': False, 'error': str(exc)}
