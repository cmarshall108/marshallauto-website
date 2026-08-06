import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

from config import get_config
from app.utils import format_mileage, format_price, is_low_mileage

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

# Backwards-compatible password helpers (Werkzeug, not bcrypt)
password_hasher = type('PasswordHasher', (), {
    'generate_password_hash': staticmethod(generate_password_hash),
    'check_password_hash': staticmethod(check_password_hash),
})()
# Keep old name so existing imports of `bcrypt` continue to work
bcrypt = password_hasher


def create_app(config_class=None):
    if config_class is None:
        config_class = get_config()

    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config.from_object(config_class)

    if hasattr(config_class, 'init_app'):
        config_class.init_app(app)

    # Trust X-Forwarded-* headers behind reverse proxies (Heroku, nginx, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Ensure upload directories exist
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'vehicles'), exist_ok=True)
    os.makedirs(os.path.join(app.config['UPLOAD_FOLDER'], 'carfax'), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'admin.login'
    login_manager.login_message = 'Please log in to access the admin panel.'
    login_manager.login_message_category = 'warning'
    login_manager.session_protection = 'strong'

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    from app.admin import admin_bp
    from app.routes import inject_globals, main

    app.register_blueprint(main)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # App-level context processor so error handlers (404/500) also get
    # site_setting / business_* vars used by base.html.
    app.context_processor(inject_globals)

    # Template filters and globals
    app.jinja_env.filters['format_price'] = format_price
    app.jinja_env.filters['format_mileage'] = format_mileage
    app.jinja_env.filters['is_low_mileage'] = is_low_mileage
    app.jinja_env.globals['is_low_mileage'] = is_low_mileage

    # Live "now" callable so footer year stays current without restart
    app.jinja_env.globals['now'] = lambda: datetime.now(timezone.utc)

    # Security headers
    @app.after_request
    def set_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
        # Cache static assets aggressively; HTML stays short-lived
        if request_is_static(response):
            response.headers.setdefault('Cache-Control', 'public, max-age=604800, immutable')
        return response

    def request_is_static(response):
        from flask import request
        return request.path.startswith('/static/') and response.status_code == 200

    # Error handlers
    from app.routes import internal_server_error, page_not_found
    from werkzeug.exceptions import RequestEntityTooLarge

    app.register_error_handler(404, page_not_found)
    app.register_error_handler(500, internal_server_error)

    @app.errorhandler(RequestEntityTooLarge)
    def request_entity_too_large(e):
        from flask import flash, redirect, request, url_for
        limit_mb = int(app.config.get('MAX_CONTENT_LENGTH', 0) / (1024 * 1024))
        message = (
            f'Upload too large. Maximum total size is {limit_mb}MB per request. '
            'Try fewer photos at a time, or compress the images first.'
        )
        # Prefer a flash + redirect back to the admin form when possible
        if request.path.startswith('/admin'):
            flash(message, 'danger')
            referrer = request.referrer
            if referrer:
                return redirect(referrer)
            return redirect(url_for('admin.dashboard'))
        return message, 413

    # Health check (no DB dependency for basic liveness)
    @app.get('/healthz')
    def healthz():
        return {'status': 'ok'}, 200

    # Bootstrap schema + defaults (safe for SQLite/dev; production should use migrations)
    with app.app_context():
        if not app.config.get('TESTING'):
            db.create_all()
            _ensure_schema_columns(app)
            _ensure_admin_exists(app)
            _ensure_default_settings()

    return app


def _ensure_schema_columns(app):
    """
    Lightweight additive schema fixes for columns added after initial create_all
    without a full migration history (SQLite-friendly).
    """
    from sqlalchemy import inspect, text
    try:
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        if 'vehicle_images' in tables:
            cols = {c['name'] for c in inspector.get_columns('vehicle_images')}
            with db.engine.begin() as conn:
                if 'width' not in cols:
                    conn.execute(text('ALTER TABLE vehicle_images ADD COLUMN width INTEGER'))
                if 'height' not in cols:
                    conn.execute(text('ALTER TABLE vehicle_images ADD COLUMN height INTEGER'))
        if 'vehicles' in tables:
            cols = {c['name'] for c in inspector.get_columns('vehicles')}
            with db.engine.begin() as conn:
                if 'title_status' not in cols:
                    conn.execute(text("ALTER TABLE vehicles ADD COLUMN title_status VARCHAR(20) DEFAULT 'clean' NOT NULL"))
                if 'meta_keywords' not in cols:
                    conn.execute(text('ALTER TABLE vehicles ADD COLUMN meta_keywords VARCHAR(255)'))
                if 'facebook_post_id' not in cols:
                    conn.execute(text('ALTER TABLE vehicles ADD COLUMN facebook_post_id VARCHAR(64)'))
                if 'facebook_posted_at' not in cols:
                    conn.execute(text('ALTER TABLE vehicles ADD COLUMN facebook_posted_at DATETIME'))
                if 'facebook_last_error' not in cols:
                    conn.execute(text('ALTER TABLE vehicles ADD COLUMN facebook_last_error VARCHAR(500)'))
                if 'facebook_last_status' not in cols:
                    conn.execute(text('ALTER TABLE vehicles ADD COLUMN facebook_last_status VARCHAR(32)'))
        if 'reviews' in tables:
            cols = {c['name'] for c in inspector.get_columns('reviews')}
            with db.engine.begin() as conn:
                if 'source' not in cols:
                    conn.execute(text('ALTER TABLE reviews ADD COLUMN source VARCHAR(64)'))
                if 'is_featured' not in cols:
                    conn.execute(text('ALTER TABLE reviews ADD COLUMN is_featured BOOLEAN DEFAULT 0 NOT NULL'))
        if 'leads' in tables:
            cols = {c['name'] for c in inspector.get_columns('leads')}
            with db.engine.begin() as conn:
                for col, ddl in [
                    ('utm_source', 'ALTER TABLE leads ADD COLUMN utm_source VARCHAR(128)'),
                    ('utm_medium', 'ALTER TABLE leads ADD COLUMN utm_medium VARCHAR(128)'),
                    ('utm_campaign', 'ALTER TABLE leads ADD COLUMN utm_campaign VARCHAR(128)'),
                    ('utm_term', 'ALTER TABLE leads ADD COLUMN utm_term VARCHAR(128)'),
                    ('utm_content', 'ALTER TABLE leads ADD COLUMN utm_content VARCHAR(128)'),
                    ('gclid', 'ALTER TABLE leads ADD COLUMN gclid VARCHAR(255)'),
                    ('fbclid', 'ALTER TABLE leads ADD COLUMN fbclid VARCHAR(255)'),
                    ('landing_path', 'ALTER TABLE leads ADD COLUMN landing_path VARCHAR(512)'),
                    ('referrer', 'ALTER TABLE leads ADD COLUMN referrer VARCHAR(512)'),
                ]:
                    if col not in cols:
                        conn.execute(text(ddl))
    except Exception as e:
        app.logger.warning('Schema ensure skipped: %s', e)


def _ensure_admin_exists(app):
    from app.models import User
    username = app.config['ADMIN_USERNAME']
    if User.query.filter_by(username=username).first():
        return
    user = User(username=username)
    user.set_password(app.config['ADMIN_PASSWORD'])
    db.session.add(user)
    db.session.commit()


def _ensure_default_settings():
    from app.models import SiteSetting
    defaults = {
        'site_title': 'Marshall Auto, LLC',
        'site_tagline': 'Quality Used Cars You Can Trust',
        'meta_description': 'Marshall Auto, LLC is your trusted used car dealer in Sanford, NC offering quality pre-owned vehicles, financing, CarFax reports, and full service history.',
        'meta_keywords': 'used cars Sanford NC, used car dealer Sanford, pre-owned vehicles, car dealership, auto sales, financing, CarFax, Marshall Auto LLC',
        'facebook_page_id': '',
        'facebook_page_access_token': '',
        'facebook_page_posting_enabled': 'false',
        'facebook_auto_post_on_create': 'false',
        'facebook_auto_post_on_edit': 'false',
        'about_text': 'Marshall Auto, LLC is a family-owned used car dealership in Sanford, NC committed to transparent pricing, quality vehicles, and exceptional customer service. Every vehicle on our lot is inspected, and CarFax history reports are available on qualifying vehicles.',
        'home_hero_title': 'Find Your Next Ride at Marshall Auto, LLC',
        'home_hero_subtitle': 'Browse our hand-picked inventory of quality used cars, trucks, and SUVs in Sanford, NC. Financing available.',
        'google_search_console': '',
        'google_analytics_id': '',
        'google_tag_id': '',
        'facebook_pixel_id': '',
        'facebook_app_id': '',
        'twitter_handle': '',
        'instagram_url': '',
        'facebook_url': '',
        'youtube_url': '',
        'business_latitude': '35.4799',
        'business_longitude': '-79.1803',
    }
    changed = False
    for key, value in defaults.items():
        if not SiteSetting.query.filter_by(key=key).first():
            db.session.add(SiteSetting(key=key, value=value))
            changed = True
    if changed:
        db.session.commit()
    SiteSetting.invalidate_cache()
    SiteSetting.load_all()
