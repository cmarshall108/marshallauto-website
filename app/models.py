import re
from datetime import datetime, timezone

from flask import current_app
from flask_login import UserMixin
from sqlalchemy import case
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import check_password_hash, generate_password_hash

from app import db


def utcnow():
    """Timezone-aware UTC timestamp helper (stored naive UTC for SQLite compat)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_active_user = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        """Flask-Login uses this to block deactivated accounts."""
        return bool(self.is_active_user)

    def __repr__(self):
        return f'<User {self.username}>'


class Vehicle(db.Model):
    __tablename__ = 'vehicles'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # Identification
    year = db.Column(db.Integer, nullable=False, index=True)
    make = db.Column(db.String(64), nullable=False, index=True)
    model = db.Column(db.String(64), nullable=False, index=True)
    trim = db.Column(db.String(128), nullable=True)
    vin = db.Column(db.String(17), unique=True, nullable=True, index=True)
    stock_number = db.Column(db.String(32), unique=True, nullable=True, index=True)

    # Pricing & status
    price = db.Column(db.Numeric(10, 2), nullable=False)
    sale_price = db.Column(db.Numeric(10, 2), nullable=True)
    mileage = db.Column(db.Integer, nullable=False)
    condition = db.Column(db.String(20), default='used', nullable=False)  # used, certified, rebuilt
    title_status = db.Column(db.String(20), default='clean', nullable=False, index=True)  # clean, rebuilt, salvage
    status = db.Column(db.String(20), default='available', nullable=False, index=True)  # available, sold, pending

    # Details
    body_style = db.Column(db.String(64), nullable=True, index=True)
    exterior_color = db.Column(db.String(64), nullable=True)
    interior_color = db.Column(db.String(64), nullable=True)
    engine = db.Column(db.String(128), nullable=True)
    transmission = db.Column(db.String(128), nullable=True)
    drivetrain = db.Column(db.String(32), nullable=True)
    fuel_type = db.Column(db.String(32), nullable=True)
    mpg_city = db.Column(db.Integer, nullable=True)
    mpg_highway = db.Column(db.Integer, nullable=True)

    # Content
    description = db.Column(db.Text, nullable=True)
    features = db.Column(db.Text, nullable=True)  # comma-separated

    # SEO
    seo_title = db.Column(db.String(160), nullable=True)
    seo_description = db.Column(db.String(320), nullable=True)
    meta_keywords = db.Column(db.String(255), nullable=True)
    slug = db.Column(db.String(256), unique=True, nullable=True, index=True)

    # Relationships — selectin avoids N+1 on list pages when preloaded
    images = db.relationship(
        'VehicleImage',
        backref='vehicle',
        lazy='selectin',
        cascade='all, delete-orphan',
        order_by='VehicleImage.order_index.asc()',
    )
    service_records = db.relationship(
        'ServiceRecord',
        backref='vehicle',
        lazy='selectin',
        cascade='all, delete-orphan',
        order_by='ServiceRecord.service_date.desc()',
    )
    carfax_reports = db.relationship(
        'CarfaxReport',
        backref='vehicle',
        lazy='selectin',
        cascade='all, delete-orphan',
        order_by='CarfaxReport.created_at.desc()',
    )

    @hybrid_property
    def display_price(self):
        return self.sale_price if self.sale_price is not None else self.price

    @display_price.expression
    def display_price(cls):
        return case((cls.sale_price.isnot(None), cls.sale_price), else_=cls.price)

    @property
    def title(self):
        parts = [str(self.year), self.make, self.model]
        if self.trim:
            parts.append(self.trim)
        return ' '.join(parts)

    @property
    def feature_list(self):
        if not self.features:
            return []
        return [f.strip() for f in self.features.split(',') if f.strip()]

    @property
    def is_available(self):
        return self.status == 'available'

    @property
    def is_low_mileage(self):
        """Retail attention flag for low-odometer inventory cards."""
        from app.utils import is_low_mileage
        return is_low_mileage(self.mileage)

    def generate_slug(self):
        base = re.sub(
            r'[^\w]+',
            '-',
            f"{self.year}-{self.make}-{self.model}-{self.trim or ''}-{self.stock_number or self.id}",
        ).strip('-').lower()
        base = re.sub(r'-+', '-', base)
        return base

    def ensure_slug(self, force=False):
        if force or not self.slug:
            candidate = self.generate_slug()
            existing = Vehicle.query.filter(Vehicle.slug == candidate, Vehicle.id != self.id).first()
            if existing:
                candidate = f"{candidate}-{self.id or 'new'}"
            self.slug = candidate

    def primary_image(self):
        """Return primary image without extra queries when relationship is loaded."""
        images = list(self.images) if self.images is not None else []
        if not images:
            return None
        for img in images:
            if img.is_primary:
                return img
        return min(images, key=lambda i: (i.order_index is None, i.order_index or 0, i.id or 0))

    def primary_image_url(self, absolute=False):
        img = self.primary_image()
        if not img:
            path = '/static/images/vehicle-placeholder.jpg'
            if absolute:
                return f"{current_app.config['SITE_URL']}{path}"
            return path
        return img.absolute_url if absolute else img.url

    def ordered_images(self):
        return sorted(
            list(self.images or []),
            key=lambda i: (i.order_index is None, i.order_index or 0, i.id or 0),
        )

    def __repr__(self):
        return f'<Vehicle {self.title}>'


class VehicleImage(db.Model):
    __tablename__ = 'vehicle_images'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False, index=True)
    filename = db.Column(db.String(256), nullable=False)
    is_primary = db.Column(db.Boolean, default=False, nullable=False)
    order_index = db.Column(db.Integer, default=0, nullable=False)
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def url(self):
        return f'/static/uploads/vehicles/{self.filename}'

    @property
    def absolute_url(self):
        return f"{current_app.config['SITE_URL']}/static/uploads/vehicles/{self.filename}"


class ServiceRecord(db.Model):
    __tablename__ = 'service_records'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False, index=True)
    service_date = db.Column(db.Date, nullable=False)
    mileage_at_service = db.Column(db.Integer, nullable=True)
    service_type = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)
    provider = db.Column(db.String(128), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    def __repr__(self):
        return f'<ServiceRecord {self.service_type} on {self.service_date}>'


class CarfaxReport(db.Model):
    __tablename__ = 'carfax_reports'

    id = db.Column(db.Integer, primary_key=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=False, index=True)
    report_date = db.Column(db.Date, nullable=True)
    accidents_reported = db.Column(db.Integer, default=0, nullable=True)
    owners_reported = db.Column(db.Integer, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    filename = db.Column(db.String(256), nullable=True)
    report_url = db.Column(db.String(512), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def file_url(self):
        """Public download route (not raw static path)."""
        if self.filename:
            return f'/carfax/{self.id}/download'
        return None

    @property
    def absolute_file_url(self):
        if self.filename:
            return f"{current_app.config['SITE_URL']}/carfax/{self.id}/download"
        return None

    def __repr__(self):
        return f'<CarfaxReport for vehicle {self.vehicle_id}>'


class SiteSetting(db.Model):
    __tablename__ = 'site_settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)

    _cache = {}
    _cache_loaded = False

    @classmethod
    def invalidate_cache(cls):
        cls._cache = {}
        cls._cache_loaded = False

    @classmethod
    def load_all(cls):
        """Load all settings into process cache (one query)."""
        cls._cache = {s.key: s.value for s in cls.query.all()}
        cls._cache_loaded = True
        return cls._cache

    @classmethod
    def get(cls, key, default=None):
        if not cls._cache_loaded:
            try:
                cls.load_all()
            except Exception:
                return default
        return cls._cache.get(key, default)

    @classmethod
    def set(cls, key, value):
        setting = cls.query.filter_by(key=key).first()
        if not setting:
            setting = cls(key=key)
            db.session.add(setting)
        setting.value = value
        cls._cache[key] = value


class Review(db.Model):
    __tablename__ = 'reviews'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    author_name = db.Column(db.String(128), nullable=False)
    rating = db.Column(db.Integer, nullable=False, default=5)
    title = db.Column(db.String(160), nullable=True)
    content = db.Column(db.Text, nullable=False)
    source = db.Column(db.String(64), nullable=True)
    is_approved = db.Column(db.Boolean, default=False, nullable=False, index=True)
    is_featured = db.Column(db.Boolean, default=False, nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=True)

    vehicle = db.relationship('Vehicle', backref=db.backref('reviews', lazy='selectin'))

    @property
    def structured_data(self):
        if self.vehicle_id and self.vehicle:
            item_reviewed = {
                "@type": "Car",
                "name": self.vehicle.title,
                "brand": {"@type": "Brand", "name": self.vehicle.make},
                "model": self.vehicle.model,
                "vehicleModelDate": str(self.vehicle.year),
                "url": f"{current_app.config['SITE_URL']}/inventory/{self.vehicle.slug}",
            }
            if self.vehicle.vin:
                item_reviewed['vehicleIdentificationNumber'] = self.vehicle.vin
        else:
            item_reviewed = {
                "@type": "AutoDealer",
                "name": current_app.config['BUSINESS_NAME'],
                "image": f"{current_app.config['SITE_URL']}/static/images/logo-icon.png",
                "url": current_app.config['SITE_URL'],
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": current_app.config['BUSINESS_ADDRESS'],
                    "addressLocality": current_app.config['BUSINESS_CITY'],
                    "addressRegion": current_app.config['BUSINESS_STATE'],
                    "postalCode": current_app.config['BUSINESS_ZIP'],
                    "addressCountry": "US"
                }
            }
        return {
            "@type": "Review",
            "author": {"@type": "Person", "name": self.author_name},
            "reviewRating": {
                "@type": "Rating",
                "ratingValue": self.rating,
                "bestRating": 5,
                "worstRating": 1
            },
            "reviewBody": self.content,
            "name": self.title or f"Review by {self.author_name}",
            "itemReviewed": item_reviewed,
        }


class Lead(db.Model):
    __tablename__ = 'leads'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    name = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(128), nullable=False)
    phone = db.Column(db.String(32), nullable=True)
    message = db.Column(db.Text, nullable=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey('vehicles.id'), nullable=True)
    source = db.Column(db.String(64), default='contact', nullable=False)
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    # Attribution (optional marketing params captured at submit)
    utm_source = db.Column(db.String(128), nullable=True)
    utm_medium = db.Column(db.String(128), nullable=True)
    utm_campaign = db.Column(db.String(128), nullable=True)
    utm_term = db.Column(db.String(128), nullable=True)
    utm_content = db.Column(db.String(128), nullable=True)
    gclid = db.Column(db.String(255), nullable=True)
    fbclid = db.Column(db.String(255), nullable=True)
    landing_path = db.Column(db.String(512), nullable=True)
    referrer = db.Column(db.String(512), nullable=True)

    vehicle = db.relationship('Vehicle', backref=db.backref('leads', lazy='dynamic'))
