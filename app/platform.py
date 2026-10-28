"""Optional production services that stay disabled for local SQLite tests.

Redis is used for shared rate limiting and short-lived response caching when
REDIS_URL is configured. Sentry is initialized only when SENTRY_DSN is set.
Image delivery can use a CDN base URL without moving the stored files.
"""
import time
from collections import defaultdict, deque

from flask import current_app, request

_memory_buckets = defaultdict(deque)
_memory_cache = {}


def init_error_tracking(app):
    dsn = app.config.get('SENTRY_DSN')
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        app.logger.warning('SENTRY_DSN is set but sentry-sdk is not installed.')
        return False
    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=float(app.config.get('SENTRY_TRACES_SAMPLE_RATE', 0)),
        environment=app.config.get('SENTRY_ENVIRONMENT', 'production'),
        send_default_pii=False,
    )
    return True


def _redis():
    url = current_app.config.get('REDIS_URL')
    if not url:
        return None
    try:
        import redis
    except ImportError:
        current_app.logger.warning('REDIS_URL is set but redis is not installed.')
        return None
    return redis.Redis.from_url(url, socket_timeout=0.25)


def rate_limit_exceeded(bucket_key, limit, window_seconds):
    """Shared rate limit when Redis is configured; otherwise per-process memory."""
    client = _redis()
    if client is not None:
        try:
            key = f'marshallauto:rate:{bucket_key}'
            current = client.incr(key)
            if current == 1:
                client.expire(key, int(window_seconds))
            return current > limit
        except Exception as exc:
            current_app.logger.warning('Redis rate limit unavailable: %s', exc)
    now = time.time()
    bucket = _memory_buckets[bucket_key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def cache_get(key):
    client = _redis()
    if client is not None:
        try:
            return client.get(f'marshallauto:cache:{key}')
        except Exception:
            return None
    item = _memory_cache.get(key)
    if not item or item[0] < time.time():
        return None
    return item[1]


def cache_set(key, value, ttl):
    client = _redis()
    if client is not None:
        try:
            client.setex(f'marshallauto:cache:{key}', int(ttl), value)
            return
        except Exception:
            pass
    _memory_cache[key] = (time.time() + ttl, value)


def cached_public_response(ttl=60):
    """Cache anonymous GET HTML for a short time when caching is enabled."""
    def decorator(view):
        from functools import wraps

        @wraps(view)
        def wrapped(*args, **kwargs):
            if request.method != 'GET' or not current_app.config.get('PUBLIC_CACHE_ENABLED'):
                return view(*args, **kwargs)
            key = f'{request.path}?{request.query_string.decode()}'
            cached = cache_get(key)
            if cached:
                return current_app.response_class(cached, mimetype='text/html')
            response = current_app.make_response(view(*args, **kwargs))
            if (
                response.status_code == 200
                and not response.headers.get('Set-Cookie')
                and 'text/html' in response.mimetype
            ):
                cache_set(key, response.get_data(), ttl)
            return response
        return wrapped
    return decorator


def cdn_url(path):
    base = (current_app.config.get('IMAGE_CDN_BASE_URL') or '').rstrip('/')
    if not base or not path:
        return path
    return f'{base}{path if path.startswith("/") else "/" + path}'
