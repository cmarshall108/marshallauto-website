import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))


def _normalize_database_url(url):
    """Normalize legacy postgres:// URLs used by some hosts (e.g. Heroku)."""
    if url and url.startswith('postgres://'):
        return url.replace('postgres://', 'postgresql://', 1)
    return url


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = _normalize_database_url(
        os.environ.get('DATABASE_URL') or 'sqlite:///marshall_auto.db'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
    }

    # Session / cookie hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    WTF_CSRF_TIME_LIMIT = 3600 * 8  # 8 hours
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    # Uploads
    UPLOAD_FOLDER = os.path.join(basedir, 'app', 'static', 'uploads')
    # Total request body size (all images in one form submit). Default 256MB for bulk vehicle photos.
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 256 * 1024 * 1024))
    ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    ALLOWED_PDF_EXTENSIONS = {'pdf'}
    IMAGE_WIDTHS = {
        'thumbnail': 300,
        'card': 600,
        'detail': 1200,
    }
    IMAGE_QUALITY = 85

    # Carvana-style photo highlights (worker only; never blocks web requests)
    # Primary engine: Grok vision via xAI (XAI_API_KEY). Fallback: OpenCV.
    PHOTO_HIGHLIGHTS_ENABLED = os.environ.get('PHOTO_HIGHLIGHTS_ENABLED', 'true').lower() in (
        '1', 'true', 'yes', 'on',
    )
    PHOTO_HIGHLIGHTS_AUTO_ENQUEUE = os.environ.get('PHOTO_HIGHLIGHTS_AUTO_ENQUEUE', 'true').lower() in (
        '1', 'true', 'yes', 'on',
    )
    PHOTO_HIGHLIGHTS_MAX = int(os.environ.get('PHOTO_HIGHLIGHTS_MAX', '5'))
    PHOTO_HIGHLIGHTS_ENGINE = (os.environ.get('PHOTO_HIGHLIGHTS_ENGINE') or 'grok').strip().lower()
    PHOTO_HIGHLIGHTS_GROK_MODEL = (os.environ.get('PHOTO_HIGHLIGHTS_GROK_MODEL') or 'grok-4.5').strip()
    XAI_API_KEY = (os.environ.get('XAI_API_KEY') or os.environ.get('GROK_API_KEY') or '').strip()
    # Legacy optional YOLO path (unused when engine=grok)
    PHOTO_HIGHLIGHTS_USE_YOLO = os.environ.get('PHOTO_HIGHLIGHTS_USE_YOLO', 'false').lower() in (
        '1', 'true', 'yes', 'on',
    )

    # Admin auth
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME') or 'admin'
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD') or 'admin'

    # Simple in-process rate limits (per worker)
    LOGIN_RATE_LIMIT = 10          # attempts
    LOGIN_RATE_WINDOW = 300        # seconds
    CONTACT_RATE_LIMIT = 8
    CONTACT_RATE_WINDOW = 600
    # First-party analytics beacon (pageviews + events)
    ANALYTICS_RATE_LIMIT = int(os.environ.get('ANALYTICS_RATE_LIMIT', 120))
    ANALYTICS_RATE_WINDOW = int(os.environ.get('ANALYTICS_RATE_WINDOW', 60))

    # Business info
    SITE_URL = os.environ.get('SITE_URL') or 'https://marshallautosanford.com'
    BUSINESS_NAME = os.environ.get('BUSINESS_NAME') or 'Marshall Auto, LLC'
    BUSINESS_PHONE = os.environ.get('BUSINESS_PHONE') or '(919)-215-0702'
    BUSINESS_EMAIL = os.environ.get('BUSINESS_EMAIL') or 'sales@marshallautosanford.com'
    BUSINESS_ADDRESS = os.environ.get('BUSINESS_ADDRESS') or '360 Wilson Rd'
    BUSINESS_CITY = os.environ.get('BUSINESS_CITY') or 'Sanford'
    BUSINESS_STATE = os.environ.get('BUSINESS_STATE') or 'NC'
    BUSINESS_ZIP = os.environ.get('BUSINESS_ZIP') or '27332'
    # Geocoded for 360 Wilson Rd, Sanford, NC 27332
    BUSINESS_LATITUDE = os.environ.get('BUSINESS_LATITUDE') or '35.4438494'
    BUSINESS_LONGITUDE = os.environ.get('BUSINESS_LONGITUDE') or '-79.1374067'
    BUSINESS_HOURS = {
        'Monday': '9:00 AM - 5:00 PM',
        'Tuesday': '9:00 AM - 5:00 PM',
        'Wednesday': '9:00 AM - 5:00 PM',
        'Thursday': '9:00 AM - 5:00 PM',
        'Friday': '9:00 AM - 5:00 PM',
        'Saturday': 'Closed',
        'Sunday': 'Closed'
    }

    # Local SEO service areas (city, state)
    SERVICE_AREAS = [
        ('Sanford', 'NC'),
        ('Chapel Hill', 'NC'),
        ('Durham', 'NC'),
        ('Raleigh', 'NC'),
        ('Cary', 'NC'),
        ('Apex', 'NC'),
        ('Fayetteville', 'NC'),
        ('Greensboro', 'NC'),
        ('Asheboro', 'NC'),
        ('Pinehurst', 'NC'),
        ('Southern Pines', 'NC'),
        ('Lillington', 'NC'),
        ('Siler City', 'NC'),
        ('Pittsboro', 'NC'),
    ]

    # Integrations
    GOOGLE_TAG_ID = os.environ.get('GOOGLE_TAG_ID') or ''
    GOOGLE_ANALYTICS_ID = os.environ.get('GOOGLE_ANALYTICS_ID') or ''
    FACEBOOK_PIXEL_ID = os.environ.get('FACEBOOK_PIXEL_ID') or ''
    FACEBOOK_APP_ID = os.environ.get('FACEBOOK_APP_ID') or ''
    # Facebook Page publishing (Graph API). Token stays in env only — never commit real tokens.
    # Note: Meta does not allow ordinary apps to auto-create Marketplace listings.
    FACEBOOK_PAGE_ID = os.environ.get('FACEBOOK_PAGE_ID') or ''
    FACEBOOK_PAGE_ACCESS_TOKEN = os.environ.get('FACEBOOK_PAGE_ACCESS_TOKEN') or ''
    FACEBOOK_AUTO_POST_VEHICLES = os.environ.get('FACEBOOK_AUTO_POST_VEHICLES', 'false').lower() in (
        '1', 'true', 'yes', 'on',
    )
    TWITTER_HANDLE = os.environ.get('TWITTER_HANDLE') or ''
    INSTAGRAM_URL = os.environ.get('INSTAGRAM_URL') or ''
    FACEBOOK_URL = os.environ.get('FACEBOOK_URL') or ''
    YOUTUBE_URL = os.environ.get('YOUTUBE_URL') or ''

    # Feature flags
    SEND_LEAD_EMAIL = os.environ.get('SEND_LEAD_EMAIL', 'false').lower() in ('1', 'true', 'yes')
    MAIL_SERVER = os.environ.get('MAIL_SERVER', '')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', '587'))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'false').lower() in ('1', 'true', 'yes')
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER') or os.environ.get('BUSINESS_EMAIL') or ''


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = 'https'

    @classmethod
    def init_app(cls, app):
        secret = app.config.get('SECRET_KEY') or ''
        admin_password = app.config.get('ADMIN_PASSWORD') or ''
        weak_secrets = {
            '',
            'dev-secret-key-change-in-production',
            'change-me-to-a-random-32-char-string',
        }
        weak_passwords = {'', 'admin', 'change-me-strong-password', 'password'}
        if secret in weak_secrets or len(secret) < 16:
            raise RuntimeError(
                'Refusing to start: set a strong SECRET_KEY environment variable in production.'
            )
        if admin_password in weak_passwords:
            raise RuntimeError(
                'Refusing to start: set a strong ADMIN_PASSWORD environment variable in production.'
            )


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SECRET_KEY = 'test-secret-key'
    ADMIN_USERNAME = 'admin'
    ADMIN_PASSWORD = 'test-admin-password'
    SERVER_NAME = 'localhost'


config_by_name = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig,
}


def get_config(name=None):
    env = (name or os.environ.get('FLASK_ENV') or os.environ.get('APP_ENV') or 'development').lower()
    if env in ('prod', 'production'):
        return ProductionConfig
    if env in ('test', 'testing'):
        return TestingConfig
    return DevelopmentConfig
