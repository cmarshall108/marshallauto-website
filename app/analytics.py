"""First-party website analytics: collection helpers and admin aggregations."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import current_app, request
from sqlalchemy import case, func

from app import db
from app.models import AnalyticsEvent, PageView, Vehicle, utcnow
from app.utils import client_ip, rate_limit_exceeded

# ---- limits / sanitization -------------------------------------------------

_MAX_PATH = 512
_MAX_TITLE = 255
_MAX_REF = 512
_MAX_UA = 512
_MAX_META = 1500
_MAX_EVENTS_PER_REQUEST = 20
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.I,
)

PAGE_TYPE_LABELS = {
    'home': 'Home',
    'inventory': 'Inventory',
    'vehicle_detail': 'Vehicle Detail',
    'contact': 'Contact',
    'about': 'About',
    'financing': 'Financing',
    'sell_your_car': 'Sell Your Car',
    'service_area': 'Service Area',
    'other': 'Other',
}

EVENT_LABELS = {
    'page_view': 'Page View',
    'view_item': 'Vehicle View',
    'view_search_results': 'Inventory Search Results',
    'search': 'Search',
    'inventory_filter': 'Inventory Filter',
    'click_to_call': 'Click to Call',
    'click_to_sms': 'Click to Text',
    'click_to_email': 'Click to Email',
    'generate_lead': 'Lead Submitted',
    'file_download': 'File Download',
    'gallery_engagement': 'Gallery Engagement',
    'payment_calculated': 'Payment Calculator',
    'select_content': 'Content Click',
    'outbound_click': 'Outbound Click',
    'scroll_depth': 'Scroll Depth',
    'engaged_time': 'Engaged Time',
}

EVENT_CATEGORIES = {
    'view_item': 'interest',
    'view_search_results': 'interest',
    'search': 'interest',
    'inventory_filter': 'interest',
    'click_to_call': 'conversion',
    'click_to_sms': 'conversion',
    'click_to_email': 'conversion',
    'generate_lead': 'conversion',
    'file_download': 'engagement',
    'gallery_engagement': 'engagement',
    'payment_calculated': 'interest',
    'select_content': 'engagement',
    'outbound_click': 'engagement',
    'scroll_depth': 'engagement',
    'engaged_time': 'engagement',
}


def _clip(value, max_len):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:max_len]


def _valid_id(value):
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if _UUID_RE.match(value):
        return value.lower()
    # Allow compact hex ids from older clients
    if re.match(r'^[0-9a-f]{16,64}$', value, re.I):
        return value.lower()[:64]
    return None


def _safe_int(value, default=None, min_v=None, max_v=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if min_v is not None and n < min_v:
        n = min_v
    if max_v is not None and n > max_v:
        n = max_v
    return n


def _safe_float(value, default=None, min_v=None, max_v=None):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if min_v is not None and n < min_v:
        n = min_v
    if max_v is not None and n > max_v:
        n = max_v
    return n


def hash_ip(ip=None):
    """One-way IP hash for unique-visitor estimates without storing raw IPs."""
    raw = (ip or client_ip() or 'unknown').strip()
    secret = current_app.config.get('SECRET_KEY') or 'marshall-auto'
    return hashlib.sha256(f'{secret}|{raw}'.encode('utf-8')).hexdigest()[:40]


def parse_user_agent(ua_string):
    """Lightweight UA parse (no external dependency)."""
    ua = (ua_string or '').strip()
    low = ua.lower()

    device = 'desktop'
    if re.search(r'bot|crawl|spider|slurp|facebookexternalhit|preview', low):
        device = 'bot'
    elif re.search(r'ipad|tablet|kindle|silk|playbook', low):
        device = 'tablet'
    elif re.search(r'mobile|iphone|ipod|android.*mobile|windows phone|opera mini', low):
        device = 'mobile'
    elif 'android' in low:
        device = 'tablet'

    browser = 'Other'
    if 'edg/' in low or 'edge/' in low:
        browser = 'Edge'
    elif 'opr/' in low or 'opera' in low:
        browser = 'Opera'
    elif 'chrome/' in low and 'chromium' not in low and 'edg' not in low:
        browser = 'Chrome'
    elif 'safari/' in low and 'chrome' not in low and 'chromium' not in low:
        browser = 'Safari'
    elif 'firefox/' in low:
        browser = 'Firefox'
    elif 'msie' in low or 'trident/' in low:
        browser = 'IE'

    os_name = 'Other'
    if 'windows' in low:
        os_name = 'Windows'
    elif 'android' in low:
        os_name = 'Android'
    elif 'iphone' in low or 'ipad' in low or 'ios' in low:
        os_name = 'iOS'
    elif 'mac os' in low or 'macintosh' in low:
        os_name = 'macOS'
    elif 'cros' in low:
        os_name = 'ChromeOS'
    elif 'linux' in low:
        os_name = 'Linux'

    return {
        'device_type': device,
        'browser': browser,
        'os': os_name,
        'user_agent': _clip(ua, _MAX_UA),
    }


def _referrer_host(referrer):
    if not referrer:
        return None
    try:
        host = urlparse(referrer).netloc.lower()
        if host.startswith('www.'):
            host = host[4:]
        return _clip(host, 255)
    except Exception:
        return None


def _normalize_path(path):
    path = _clip(path, _MAX_PATH) or '/'
    if not path.startswith('/'):
        path = '/' + path
    # Never track admin or static/health endpoints from public beacon
    blocked_prefixes = ('/admin', '/static/', '/healthz', '/api/analytics')
    if any(path.startswith(p) for p in blocked_prefixes):
        return None
    return path


def _meta_json(data):
    if not data:
        return None
    if isinstance(data, str):
        return _clip(data, _MAX_META)
    try:
        return _clip(json.dumps(data, separators=(',', ':'), default=str), _MAX_META)
    except (TypeError, ValueError):
        return None


def collection_rate_limited():
    limit = current_app.config.get('ANALYTICS_RATE_LIMIT', 120)
    window = current_app.config.get('ANALYTICS_RATE_WINDOW', 60)
    return rate_limit_exceeded(f'analytics:{client_ip()}', limit, window)


def record_page_view(payload):
    """Create a page view from a client payload. Returns (page_view, error)."""
    visitor_id = _valid_id(payload.get('visitor_id'))
    session_id = _valid_id(payload.get('session_id'))
    if not visitor_id or not session_id:
        return None, 'invalid_identity'

    path = _normalize_path(payload.get('path') or payload.get('page_path'))
    if not path:
        return None, 'invalid_path'

    ua_info = parse_user_agent(payload.get('user_agent') or request.headers.get('User-Agent', ''))
    referrer = _clip(payload.get('referrer'), _MAX_REF)
    # Prefer client document.referrer; fall back to header
    if not referrer:
        referrer = _clip(request.referrer, _MAX_REF)

    vehicle_id = _safe_int(payload.get('vehicle_id'), default=None, min_v=1)
    if vehicle_id and not db.session.get(Vehicle, vehicle_id):
        vehicle_id = None

    page_view = PageView(
        visitor_id=visitor_id,
        session_id=session_id,
        path=path,
        page_type=_clip(payload.get('page_type'), 64) or 'other',
        page_title=_clip(payload.get('page_title') or payload.get('title'), _MAX_TITLE),
        vehicle_id=vehicle_id,
        referrer=referrer,
        referrer_host=_referrer_host(referrer),
        landing_path=_normalize_path(payload.get('landing_path') or path) or path,
        query_string=_clip(payload.get('query_string') or payload.get('query'), 512),
        utm_source=_clip(payload.get('utm_source'), 128),
        utm_medium=_clip(payload.get('utm_medium'), 128),
        utm_campaign=_clip(payload.get('utm_campaign'), 128),
        utm_term=_clip(payload.get('utm_term'), 128),
        utm_content=_clip(payload.get('utm_content'), 128),
        gclid=_clip(payload.get('gclid'), 255),
        fbclid=_clip(payload.get('fbclid'), 255),
        device_type=ua_info['device_type'],
        browser=ua_info['browser'],
        os=ua_info['os'],
        language=_clip(payload.get('language'), 32),
        screen_width=_safe_int(payload.get('screen_width'), min_v=0, max_v=10000),
        screen_height=_safe_int(payload.get('screen_height'), min_v=0, max_v=10000),
        timezone=_clip(payload.get('timezone'), 64),
        ip_hash=hash_ip(),
        user_agent=ua_info['user_agent'],
        duration_seconds=0,
        scroll_depth_pct=0,
        is_bounce=True,
        is_engaged=False,
        is_exit=False,
        heartbeat_count=0,
    )
    db.session.add(page_view)
    db.session.flush()
    return page_view, None


def update_page_view(payload):
    """Heartbeat / exit update for an existing page view."""
    page_view_id = _safe_int(payload.get('page_view_id') or payload.get('id'), min_v=1)
    visitor_id = _valid_id(payload.get('visitor_id'))
    session_id = _valid_id(payload.get('session_id'))
    if not page_view_id or not visitor_id or not session_id:
        return None, 'invalid_identity'

    page_view = db.session.get(PageView, page_view_id)
    if not page_view or page_view.visitor_id != visitor_id or page_view.session_id != session_id:
        return None, 'not_found'

    duration = _safe_int(payload.get('duration_seconds'), default=page_view.duration_seconds, min_v=0, max_v=86400)
    scroll = _safe_int(payload.get('scroll_depth_pct'), default=page_view.scroll_depth_pct, min_v=0, max_v=100)

    # Only move metrics forward (monotonic)
    if duration is not None and duration > (page_view.duration_seconds or 0):
        page_view.duration_seconds = duration
    if scroll is not None and scroll > (page_view.scroll_depth_pct or 0):
        page_view.scroll_depth_pct = scroll

    engaged = bool(payload.get('is_engaged')) or (page_view.duration_seconds or 0) >= 15 or (page_view.scroll_depth_pct or 0) >= 50
    page_view.is_engaged = bool(page_view.is_engaged or engaged)

    # Bounce = left quickly with little engagement
    if page_view.is_engaged or (page_view.duration_seconds or 0) >= 10 or (page_view.heartbeat_count or 0) >= 1:
        page_view.is_bounce = False

    if payload.get('is_exit'):
        page_view.is_exit = True

    if payload.get('heartbeat'):
        page_view.heartbeat_count = (page_view.heartbeat_count or 0) + 1

    page_view.updated_at = utcnow()
    return page_view, None


def record_event(payload, page_view=None):
    """Record a discrete analytics event."""
    visitor_id = _valid_id(payload.get('visitor_id'))
    session_id = _valid_id(payload.get('session_id'))
    event_name = _clip(payload.get('event_name') or payload.get('name') or payload.get('event'), 64)
    if not visitor_id or not session_id or not event_name:
        return None, 'invalid_event'

    # Normalize common aliases
    event_name = event_name.lower().replace(' ', '_')
    if event_name == 'click' and payload.get('outbound'):
        event_name = 'outbound_click'

    vehicle_id = _safe_int(payload.get('vehicle_id'), default=None, min_v=1)
    if vehicle_id and not db.session.get(Vehicle, vehicle_id):
        vehicle_id = None

    path = _normalize_path(payload.get('path') or payload.get('page_path'))
    page_view_id = None
    if page_view is not None:
        page_view_id = page_view.id
    else:
        page_view_id = _safe_int(payload.get('page_view_id'), min_v=1)

    category = _clip(payload.get('event_category') or payload.get('category'), 64)
    if not category:
        category = EVENT_CATEGORIES.get(event_name, 'other')

    meta = payload.get('meta') or payload.get('params') or {}
    # Pull common fields out of nested params if present
    if isinstance(meta, dict):
        if not vehicle_id:
            vehicle_id = _safe_int(meta.get('vehicle_id'), min_v=1)
        label = payload.get('label') or meta.get('label') or meta.get('link_text') or meta.get('search_term') or meta.get('cta_type')
    else:
        label = payload.get('label')

    event = AnalyticsEvent(
        visitor_id=visitor_id,
        session_id=session_id,
        page_view_id=page_view_id,
        event_name=event_name,
        event_category=category,
        label=_clip(label, 255),
        value=_safe_float(payload.get('value'), min_v=0, max_v=1_000_000),
        path=path,
        page_type=_clip(payload.get('page_type'), 64),
        vehicle_id=vehicle_id,
        meta_json=_meta_json(meta if meta else None),
    )
    db.session.add(event)
    return event, None


def process_collect_payload(data):
    """
    Handle a collect POST body.

    Supported shapes:
      { "type": "pageview", ...fields }
      { "type": "update", page_view_id, duration_seconds, ... }
      { "type": "event", event_name, ... }
      { "type": "batch", items: [ ... ] }
    """
    if not isinstance(data, dict):
        return {'ok': False, 'error': 'invalid_json'}, 400

    if collection_rate_limited():
        return {'ok': False, 'error': 'rate_limited'}, 429

    kind = (data.get('type') or data.get('action') or 'pageview').lower().strip()
    result = {'ok': True}

    try:
        if kind == 'batch':
            items = data.get('items') or []
            if not isinstance(items, list):
                return {'ok': False, 'error': 'invalid_batch'}, 400
            items = items[:_MAX_EVENTS_PER_REQUEST]
            accepted = 0
            page_view_id = None
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_type = (item.get('type') or 'event').lower()
                # Inherit identity from envelope
                for key in ('visitor_id', 'session_id', 'page_view_id'):
                    item.setdefault(key, data.get(key))
                if item_type in ('pageview', 'page_view'):
                    pv, err = record_page_view(item)
                    if pv:
                        accepted += 1
                        page_view_id = pv.id
                elif item_type == 'update':
                    pv, err = update_page_view(item)
                    if pv:
                        accepted += 1
                        page_view_id = pv.id
                else:
                    ev, err = record_event(item)
                    if ev:
                        accepted += 1
            db.session.commit()
            result['accepted'] = accepted
            if page_view_id:
                result['page_view_id'] = page_view_id
            return result, 200

        if kind in ('pageview', 'page_view'):
            page_view, err = record_page_view(data)
            if err:
                db.session.rollback()
                return {'ok': False, 'error': err}, 400
            # Optional nested events with the page view
            events = data.get('events') or []
            if isinstance(events, list):
                for item in events[:_MAX_EVENTS_PER_REQUEST]:
                    if isinstance(item, dict):
                        item.setdefault('visitor_id', data.get('visitor_id'))
                        item.setdefault('session_id', data.get('session_id'))
                        record_event(item, page_view=page_view)
            db.session.commit()
            result['page_view_id'] = page_view.id
            return result, 200

        if kind == 'update':
            page_view, err = update_page_view(data)
            if err:
                db.session.rollback()
                return {'ok': False, 'error': err}, 400
            db.session.commit()
            result['page_view_id'] = page_view.id
            return result, 200

        if kind == 'event':
            event, err = record_event(data)
            if err:
                db.session.rollback()
                return {'ok': False, 'error': err}, 400
            db.session.commit()
            result['event_id'] = event.id
            return result, 200

        return {'ok': False, 'error': 'unknown_type'}, 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Analytics collect failed')
        return {'ok': False, 'error': 'server_error'}, 500


# ---- admin aggregations ----------------------------------------------------

def _parse_date_range(args):
    """Return (start_dt, end_dt, days, range_key) in naive UTC."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end = now
    range_key = (args.get('range') or '30d').strip().lower()

    custom_start = args.get('start')
    custom_end = args.get('end')
    if range_key == 'custom' and custom_start:
        try:
            start = datetime.strptime(custom_start, '%Y-%m-%d')
            if custom_end:
                end = datetime.strptime(custom_end, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
            else:
                end = now
            days = max(1, (end.date() - start.date()).days + 1)
            return start, end, days, 'custom'
        except ValueError:
            range_key = '30d'

    mapping = {
        '24h': 1,
        '7d': 7,
        '14d': 14,
        '30d': 30,
        '90d': 90,
        '180d': 180,
        '365d': 365,
    }
    days = mapping.get(range_key, 30)
    if range_key == '24h':
        start = now - timedelta(hours=24)
    else:
        start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end, days, range_key if range_key in mapping else '30d'


def _pv_filter(query, start, end, page_type=None, device=None):
    query = query.filter(PageView.created_at >= start, PageView.created_at <= end)
    query = query.filter(PageView.device_type != 'bot')
    if page_type:
        query = query.filter(PageView.page_type == page_type)
    if device:
        query = query.filter(PageView.device_type == device)
    return query


def _ev_filter(query, start, end):
    return query.filter(
        AnalyticsEvent.created_at >= start,
        AnalyticsEvent.created_at <= end,
    )


def _pct(part, whole):
    if not whole:
        return 0.0
    return round((part / whole) * 100.0, 1)


def _avg(total, count):
    if not count:
        return 0.0
    return round(total / count, 1)


def build_analytics_dashboard(args):
    """Build the full admin analytics context dict."""
    start, end, days, range_key = _parse_date_range(args)
    page_type = (args.get('page_type') or '').strip() or None
    device = (args.get('device') or '').strip() or None

    # Previous period for comparison
    period_len = end - start
    prev_end = start - timedelta(seconds=1)
    prev_start = prev_end - period_len

    def base_q():
        return _pv_filter(PageView.query, start, end, page_type=page_type, device=device)

    def prev_q():
        return _pv_filter(PageView.query, prev_start, prev_end, page_type=page_type, device=device)

    total_views = base_q().count()
    prev_views = prev_q().count()

    unique_visitors = base_q().with_entities(func.count(func.distinct(PageView.visitor_id))).scalar() or 0
    prev_visitors = prev_q().with_entities(func.count(func.distinct(PageView.visitor_id))).scalar() or 0

    unique_sessions = base_q().with_entities(func.count(func.distinct(PageView.session_id))).scalar() or 0
    prev_sessions = prev_q().with_entities(func.count(func.distinct(PageView.session_id))).scalar() or 0

    engaged_views = base_q().filter(PageView.is_engaged.is_(True)).count()
    bounce_views = base_q().filter(PageView.is_bounce.is_(True)).count()

    duration_row = base_q().with_entities(
        func.coalesce(func.avg(PageView.duration_seconds), 0),
        func.coalesce(func.avg(PageView.scroll_depth_pct), 0),
        func.coalesce(func.sum(PageView.duration_seconds), 0),
    ).first()
    avg_duration = round(float(duration_row[0] or 0), 1)
    avg_scroll = round(float(duration_row[1] or 0), 1)
    total_time = int(duration_row[2] or 0)

    prev_duration = prev_q().with_entities(func.coalesce(func.avg(PageView.duration_seconds), 0)).scalar() or 0

    # Events
    event_q = _ev_filter(AnalyticsEvent.query, start, end)
    if page_type:
        event_q = event_q.filter(AnalyticsEvent.page_type == page_type)
    total_events = event_q.count()
    lead_events = event_q.filter(AnalyticsEvent.event_name == 'generate_lead').count()
    call_events = event_q.filter(AnalyticsEvent.event_name == 'click_to_call').count()
    sms_events = event_q.filter(AnalyticsEvent.event_name == 'click_to_sms').count()
    email_events = event_q.filter(AnalyticsEvent.event_name == 'click_to_email').count()
    filter_events = event_q.filter(AnalyticsEvent.event_name == 'inventory_filter').count()
    gallery_events = event_q.filter(AnalyticsEvent.event_name == 'gallery_engagement').count()
    payment_events = event_q.filter(AnalyticsEvent.event_name == 'payment_calculated').count()
    download_events = event_q.filter(AnalyticsEvent.event_name == 'file_download').count()
    vehicle_view_events = event_q.filter(AnalyticsEvent.event_name == 'view_item').count()

    def delta(current, previous):
        if previous == 0:
            return None if current == 0 else 100.0
        return round(((current - previous) / previous) * 100.0, 1)

    kpis = {
        'page_views': total_views,
        'page_views_delta': delta(total_views, prev_views),
        'unique_visitors': unique_visitors,
        'unique_visitors_delta': delta(unique_visitors, prev_visitors),
        'sessions': unique_sessions,
        'sessions_delta': delta(unique_sessions, prev_sessions),
        'avg_duration': avg_duration,
        'avg_duration_delta': delta(avg_duration, float(prev_duration or 0)),
        'avg_scroll': avg_scroll,
        'bounce_rate': _pct(bounce_views, total_views),
        'engagement_rate': _pct(engaged_views, total_views),
        'pages_per_session': _avg(total_views, unique_sessions),
        'total_time_seconds': total_time,
        'total_events': total_events,
        'leads': lead_events,
        'calls': call_events,
        'sms': sms_events,
        'emails': email_events,
        'vehicle_views': vehicle_view_events,
        'filters': filter_events,
        'gallery': gallery_events,
        'payments': payment_events,
        'downloads': download_events,
    }

    # ---- time series (daily) ----
    day_expr = func.date(PageView.created_at)
    daily_rows = (
        _pv_filter(db.session.query(
            day_expr.label('day'),
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
            func.count(func.distinct(PageView.session_id)).label('sessions'),
            func.coalesce(func.avg(PageView.duration_seconds), 0).label('avg_duration'),
        ), start, end, page_type=page_type, device=device)
        .group_by(day_expr)
        .order_by(day_expr)
        .all()
    )
    daily_map = {
        str(r.day): {
            'views': r.views,
            'visitors': r.visitors,
            'sessions': r.sessions,
            'avg_duration': round(float(r.avg_duration or 0), 1),
        }
        for r in daily_rows
    }
    # Fill gaps
    daily_labels = []
    daily_views = []
    daily_visitors = []
    daily_sessions = []
    daily_duration = []
    cursor = start.date() if hasattr(start, 'date') else start
    end_date = end.date() if hasattr(end, 'date') else end
    if range_key == '24h':
        # Hourly for last 24h
        hour_expr = func.strftime('%Y-%m-%d %H:00', PageView.created_at)
        # SQLite strftime; PostgreSQL needs different — use Python bucketing fallback
        hourly_rows = (
            _pv_filter(db.session.query(
                PageView.created_at,
                PageView.visitor_id,
                PageView.session_id,
            ), start, end, page_type=page_type, device=device)
            .all()
        )
        buckets = defaultdict(lambda: {'views': 0, 'visitors': set(), 'sessions': set()})
        for row in hourly_rows:
            key = row.created_at.replace(minute=0, second=0, microsecond=0)
            buckets[key]['views'] += 1
            buckets[key]['visitors'].add(row.visitor_id)
            buckets[key]['sessions'].add(row.session_id)
        h = start.replace(minute=0, second=0, microsecond=0)
        while h <= end:
            label = h.strftime('%m/%d %H:00')
            daily_labels.append(label)
            b = buckets.get(h, {'views': 0, 'visitors': set(), 'sessions': set()})
            daily_views.append(b['views'] if isinstance(b['views'], int) else 0)
            daily_visitors.append(len(b['visitors']))
            daily_sessions.append(len(b['sessions']))
            daily_duration.append(0)
            h += timedelta(hours=1)
    else:
        d = cursor
        while d <= end_date:
            key = str(d)
            daily_labels.append(d.strftime('%b %d'))
            cell = daily_map.get(key, {'views': 0, 'visitors': 0, 'sessions': 0, 'avg_duration': 0})
            daily_views.append(cell['views'])
            daily_visitors.append(cell['visitors'])
            daily_sessions.append(cell['sessions'])
            daily_duration.append(cell['avg_duration'])
            d += timedelta(days=1)

    # ---- hour of day / day of week (Python-side for DB portability) ----
    ts_rows = (
        _pv_filter(db.session.query(
            PageView.created_at,
            PageView.duration_seconds,
            PageView.is_engaged,
        ), start, end, page_type=page_type, device=device)
        .all()
    )
    hour_counts = [0] * 24
    hour_engaged = [0] * 24
    dow_counts = [0] * 7  # Mon=0
    for row in ts_rows:
        if not row.created_at:
            continue
        hour_counts[row.created_at.hour] += 1
        if row.is_engaged:
            hour_engaged[row.created_at.hour] += 1
        dow_counts[row.created_at.weekday()] += 1

    # ---- top pages ----
    top_pages = (
        _pv_filter(db.session.query(
            PageView.path,
            PageView.page_type,
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
            func.coalesce(func.avg(PageView.duration_seconds), 0).label('avg_duration'),
            func.coalesce(func.avg(PageView.scroll_depth_pct), 0).label('avg_scroll'),
            func.sum(case((PageView.is_bounce.is_(True), 1), else_=0)).label('bounces'),
        ), start, end, page_type=page_type, device=device)
        .group_by(PageView.path, PageView.page_type)
        .order_by(func.count(PageView.id).desc())
        .limit(20)
        .all()
    )
    top_pages_data = [{
        'path': r.path,
        'page_type': r.page_type or 'other',
        'page_type_label': PAGE_TYPE_LABELS.get(r.page_type or 'other', r.page_type or 'Other'),
        'views': r.views,
        'visitors': r.visitors,
        'avg_duration': round(float(r.avg_duration or 0), 1),
        'avg_scroll': round(float(r.avg_scroll or 0), 1),
        'bounce_rate': _pct(int(r.bounces or 0), r.views),
    } for r in top_pages]

    # ---- page type breakdown ----
    page_type_rows = (
        _pv_filter(db.session.query(
            PageView.page_type,
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
            func.coalesce(func.avg(PageView.duration_seconds), 0).label('avg_duration'),
        ), start, end, page_type=None, device=device)
        .group_by(PageView.page_type)
        .order_by(func.count(PageView.id).desc())
        .all()
    )
    page_types_data = [{
        'key': r.page_type or 'other',
        'label': PAGE_TYPE_LABELS.get(r.page_type or 'other', r.page_type or 'Other'),
        'views': r.views,
        'visitors': r.visitors,
        'avg_duration': round(float(r.avg_duration or 0), 1),
        'share': _pct(r.views, total_views) if not page_type else _pct(r.views, sum(x.views for x in page_type_rows) or 1),
    } for r in page_type_rows]

    # ---- devices / browsers / OS ----
    def breakdown(column):
        rows = (
            _pv_filter(db.session.query(
                column,
                func.count(PageView.id).label('views'),
                func.count(func.distinct(PageView.visitor_id)).label('visitors'),
            ), start, end, page_type=page_type, device=None if column is PageView.device_type else device)
            .group_by(column)
            .order_by(func.count(PageView.id).desc())
            .all()
        )
        return [{
            'label': (r[0] or 'Unknown').title() if column is PageView.device_type else (r[0] or 'Unknown'),
            'views': r.views,
            'visitors': r.visitors,
            'share': _pct(r.views, total_views if total_views else sum(x.views for x in rows) or 1),
        } for r in rows if (r[0] or '') != 'bot']

    devices_data = breakdown(PageView.device_type)
    browsers_data = breakdown(PageView.browser)
    os_data = breakdown(PageView.os)

    # ---- traffic sources ----
    referrer_rows = (
        _pv_filter(db.session.query(
            PageView.referrer_host,
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
        ), start, end, page_type=page_type, device=device)
        .group_by(PageView.referrer_host)
        .order_by(func.count(PageView.id).desc())
        .limit(15)
        .all()
    )
    referrers_data = []
    direct_views = 0
    for r in referrer_rows:
        if not r.referrer_host:
            direct_views += r.views
            continue
        referrers_data.append({
            'host': r.referrer_host,
            'views': r.views,
            'visitors': r.visitors,
            'share': _pct(r.views, total_views),
        })
    if direct_views:
        referrers_data.insert(0, {
            'host': '(direct / none)',
            'views': direct_views,
            'visitors': 0,
            'share': _pct(direct_views, total_views),
        })

    utm_source_rows = (
        _pv_filter(db.session.query(
            PageView.utm_source,
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
        ), start, end, page_type=page_type, device=device)
        .filter(PageView.utm_source.isnot(None), PageView.utm_source != '')
        .group_by(PageView.utm_source)
        .order_by(func.count(PageView.id).desc())
        .limit(15)
        .all()
    )
    utm_sources_data = [{
        'source': r.utm_source,
        'views': r.views,
        'visitors': r.visitors,
    } for r in utm_source_rows]

    utm_campaign_rows = (
        _pv_filter(db.session.query(
            PageView.utm_campaign,
            PageView.utm_source,
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
        ), start, end, page_type=page_type, device=device)
        .filter(PageView.utm_campaign.isnot(None), PageView.utm_campaign != '')
        .group_by(PageView.utm_campaign, PageView.utm_source)
        .order_by(func.count(PageView.id).desc())
        .limit(15)
        .all()
    )
    utm_campaigns_data = [{
        'campaign': r.utm_campaign,
        'source': r.utm_source or '—',
        'views': r.views,
        'visitors': r.visitors,
    } for r in utm_campaign_rows]

    # Paid click ids
    gclid_views = base_q().filter(PageView.gclid.isnot(None), PageView.gclid != '').count()
    fbclid_views = base_q().filter(PageView.fbclid.isnot(None), PageView.fbclid != '').count()

    # ---- top vehicles ----
    vehicle_rows = (
        _pv_filter(db.session.query(
            PageView.vehicle_id,
            func.count(PageView.id).label('views'),
            func.count(func.distinct(PageView.visitor_id)).label('visitors'),
            func.coalesce(func.avg(PageView.duration_seconds), 0).label('avg_duration'),
            func.coalesce(func.avg(PageView.scroll_depth_pct), 0).label('avg_scroll'),
        ), start, end, page_type=page_type, device=device)
        .filter(PageView.vehicle_id.isnot(None))
        .group_by(PageView.vehicle_id)
        .order_by(func.count(PageView.id).desc())
        .limit(15)
        .all()
    )
    vehicle_ids = [r.vehicle_id for r in vehicle_rows]
    vehicles_by_id = {}
    if vehicle_ids:
        for v in Vehicle.query.filter(Vehicle.id.in_(vehicle_ids)).all():
            vehicles_by_id[v.id] = v

    # Interest events per vehicle
    vehicle_interest = defaultdict(lambda: {'calls': 0, 'leads': 0, 'gallery': 0, 'payments': 0})
    if vehicle_ids:
        interest_rows = (
            _ev_filter(db.session.query(
                AnalyticsEvent.vehicle_id,
                AnalyticsEvent.event_name,
                func.count(AnalyticsEvent.id),
            ), start, end)
            .filter(AnalyticsEvent.vehicle_id.in_(vehicle_ids))
            .group_by(AnalyticsEvent.vehicle_id, AnalyticsEvent.event_name)
            .all()
        )
        for vid, ename, cnt in interest_rows:
            if ename == 'click_to_call':
                vehicle_interest[vid]['calls'] += cnt
            elif ename == 'generate_lead':
                vehicle_interest[vid]['leads'] += cnt
            elif ename == 'gallery_engagement':
                vehicle_interest[vid]['gallery'] += cnt
            elif ename == 'payment_calculated':
                vehicle_interest[vid]['payments'] += cnt

    top_vehicles_data = []
    for r in vehicle_rows:
        v = vehicles_by_id.get(r.vehicle_id)
        interest = vehicle_interest.get(r.vehicle_id, {})
        top_vehicles_data.append({
            'id': r.vehicle_id,
            'title': v.title if v else f'Vehicle #{r.vehicle_id}',
            'slug': v.slug if v else None,
            'status': v.status if v else None,
            'price': float(v.display_price) if v and v.display_price is not None else None,
            'views': r.views,
            'visitors': r.visitors,
            'avg_duration': round(float(r.avg_duration or 0), 1),
            'avg_scroll': round(float(r.avg_scroll or 0), 1),
            'calls': interest.get('calls', 0),
            'leads': interest.get('leads', 0),
            'gallery': interest.get('gallery', 0),
            'payments': interest.get('payments', 0),
        })

    # ---- event breakdown ----
    event_rows = (
        _ev_filter(db.session.query(
            AnalyticsEvent.event_name,
            AnalyticsEvent.event_category,
            func.count(AnalyticsEvent.id).label('count'),
        ), start, end)
        .group_by(AnalyticsEvent.event_name, AnalyticsEvent.event_category)
        .order_by(func.count(AnalyticsEvent.id).desc())
        .all()
    )
    events_data = [{
        'name': r.event_name,
        'label': EVENT_LABELS.get(r.event_name, r.event_name.replace('_', ' ').title()),
        'category': r.event_category or 'other',
        'count': r.count,
    } for r in event_rows]

    # ---- landing pages ----
    landing_rows = (
        _pv_filter(db.session.query(
            PageView.landing_path,
            func.count(func.distinct(PageView.session_id)).label('sessions'),
            func.count(PageView.id).label('views'),
        ), start, end, page_type=page_type, device=device)
        .filter(PageView.landing_path.isnot(None))
        .group_by(PageView.landing_path)
        .order_by(func.count(func.distinct(PageView.session_id)).desc())
        .limit(12)
        .all()
    )
    landings_data = [{
        'path': r.landing_path,
        'sessions': r.sessions,
        'views': r.views,
    } for r in landing_rows]

    # ---- recent activity ----
    recent_views = (
        _pv_filter(PageView.query, start, end, page_type=page_type, device=device)
        .order_by(PageView.created_at.desc())
        .limit(25)
        .all()
    )
    recent_view_vehicle_ids = [pv.vehicle_id for pv in recent_views if pv.vehicle_id]
    recent_vehicles = {}
    if recent_view_vehicle_ids:
        for v in Vehicle.query.filter(Vehicle.id.in_(recent_view_vehicle_ids)).all():
            recent_vehicles[v.id] = v

    recent_views_data = [{
        'id': pv.id,
        'created_at': pv.created_at,
        'path': pv.path,
        'page_type': pv.page_type,
        'page_type_label': PAGE_TYPE_LABELS.get(pv.page_type or 'other', pv.page_type or 'Other'),
        'device_type': pv.device_type,
        'browser': pv.browser,
        'os': pv.os,
        'duration_seconds': pv.duration_seconds or 0,
        'scroll_depth_pct': pv.scroll_depth_pct or 0,
        'is_engaged': pv.is_engaged,
        'is_bounce': pv.is_bounce,
        'referrer_host': pv.referrer_host or 'direct',
        'utm_source': pv.utm_source,
        'vehicle_title': recent_vehicles[pv.vehicle_id].title if pv.vehicle_id in recent_vehicles else None,
        'visitor_short': (pv.visitor_id or '')[:8],
    } for pv in recent_views]

    recent_events = (
        _ev_filter(AnalyticsEvent.query, start, end)
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(25)
        .all()
    )
    recent_events_data = [{
        'created_at': ev.created_at,
        'name': ev.event_name,
        'label': EVENT_LABELS.get(ev.event_name, ev.event_name.replace('_', ' ').title()),
        'category': ev.event_category or 'other',
        'path': ev.path,
        'event_label': ev.label,
        'vehicle_id': ev.vehicle_id,
        'value': ev.value,
    } for ev in recent_events]

    # ---- languages / screens (interest signals) ----
    lang_rows = (
        _pv_filter(db.session.query(
            PageView.language,
            func.count(PageView.id).label('views'),
        ), start, end, page_type=page_type, device=device)
        .filter(PageView.language.isnot(None), PageView.language != '')
        .group_by(PageView.language)
        .order_by(func.count(PageView.id).desc())
        .limit(8)
        .all()
    )
    languages_data = [{'label': r.language, 'views': r.views} for r in lang_rows]

    # Engagement duration buckets
    duration_buckets = [
        ('0–10s', 0, 10),
        ('10–30s', 10, 30),
        ('30–60s', 30, 60),
        ('1–3m', 60, 180),
        ('3–10m', 180, 600),
        ('10m+', 600, 10**9),
    ]
    # Single query then bucket in Python
    dur_values = [
        row[0] or 0
        for row in _pv_filter(
            db.session.query(PageView.duration_seconds),
            start, end, page_type=page_type, device=device,
        ).all()
    ]
    duration_dist = []
    for label, lo, hi in duration_buckets:
        count = sum(1 for d in dur_values if lo <= d < hi)
        duration_dist.append({'label': label, 'count': count, 'share': _pct(count, len(dur_values))})

    scroll_buckets = [
        ('0–25%', 0, 25),
        ('25–50%', 25, 50),
        ('50–75%', 50, 75),
        ('75–100%', 75, 101),
    ]
    scroll_values = [
        row[0] or 0
        for row in _pv_filter(
            db.session.query(PageView.scroll_depth_pct),
            start, end, page_type=page_type, device=device,
        ).all()
    ]
    scroll_dist = []
    for label, lo, hi in scroll_buckets:
        count = sum(1 for s in scroll_values if lo <= s < hi)
        scroll_dist.append({'label': label, 'count': count, 'share': _pct(count, len(scroll_values))})

    return {
        'range_key': range_key,
        'start': start,
        'end': end,
        'days': days,
        'page_type_filter': page_type or '',
        'device_filter': device or '',
        'kpis': kpis,
        'charts': {
            'daily_labels': daily_labels,
            'daily_views': daily_views,
            'daily_visitors': daily_visitors,
            'daily_sessions': daily_sessions,
            'daily_duration': daily_duration,
            'hour_labels': [f'{h:02d}:00' for h in range(24)],
            'hour_counts': hour_counts,
            'hour_engaged': hour_engaged,
            'dow_labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
            'dow_counts': dow_counts,
            'duration_dist_labels': [d['label'] for d in duration_dist],
            'duration_dist_counts': [d['count'] for d in duration_dist],
            'scroll_dist_labels': [d['label'] for d in scroll_dist],
            'scroll_dist_counts': [d['count'] for d in scroll_dist],
            'page_type_labels': [p['label'] for p in page_types_data],
            'page_type_views': [p['views'] for p in page_types_data],
            'device_labels': [d['label'] for d in devices_data],
            'device_views': [d['views'] for d in devices_data],
            'browser_labels': [b['label'] for b in browsers_data[:8]],
            'browser_views': [b['views'] for b in browsers_data[:8]],
            'event_labels': [e['label'] for e in events_data[:12]],
            'event_counts': [e['count'] for e in events_data[:12]],
        },
        'top_pages': top_pages_data,
        'page_types': page_types_data,
        'devices': devices_data,
        'browsers': browsers_data,
        'os_list': os_data,
        'referrers': referrers_data,
        'utm_sources': utm_sources_data,
        'utm_campaigns': utm_campaigns_data,
        'gclid_views': gclid_views,
        'fbclid_views': fbclid_views,
        'top_vehicles': top_vehicles_data,
        'events': events_data,
        'landings': landings_data,
        'recent_views': recent_views_data,
        'recent_events': recent_events_data,
        'languages': languages_data,
        'duration_dist': duration_dist,
        'scroll_dist': scroll_dist,
        'page_type_choices': PAGE_TYPE_LABELS,
        'has_data': total_views > 0 or total_events > 0,
    }


def format_duration(seconds):
    """Human-readable duration for templates."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        return '0s'
    if seconds < 60:
        return f'{seconds}s'
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}m {sec}s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h {minutes}m'
