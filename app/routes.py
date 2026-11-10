import os
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from flask import (
    Blueprint, current_app, flash, jsonify, make_response, redirect,
    render_template, request, send_from_directory, url_for as flask_url_for, abort
)
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    ContactForm, InventoryAlertForm, InventorySearchAlertForm, TestDriveRequestForm,
)
from app.models import (
    CarfaxReport, FinancingApplication, Lead, Review, SiteSetting, Vehicle,
)
from app.models import BlogPost
from app.financing import (
    FinancingEncryptionUnavailable, admin_is_online, notify_salespeople_by_textbelt,
    financing_spam_detected, online_application_available, save_application,
    validate_application,
)
from app.buyer_guides import published_buyer_guides
from app.platform import cached_public_response
from app.spam_filter import check_lead_spam_for_request
from app.vin_decode import group_vin_decode_data, is_valid_vin, normalize_vin
from app.utils import (
    aggregate_rating_data, business_hours, client_ip, dealership_now, format_mileage, format_mileage_badge, format_price,
    format_phone_number, notify_new_lead, parse_optional_int, rate_limit_exceeded, sanitize_gsc_tag,
    slugify,
    send_lead_confirmation, structured_data_breadcrumb, structured_data_faq,
    structured_data_how_to, structured_data_item_list, structured_data_local_business,
    structured_data_vehicle, structured_data_website,
)

main = Blueprint('main', __name__)


def _blog_post_description(post):
    """Build a useful search/share description when the editor left it blank."""
    for source, truncate in (
        (post.seo_description, False),
        (post.excerpt, False),
        (post.content, True),
    ):
        text = BeautifulSoup(source or '', 'html.parser').get_text(' ', strip=True)
        text = ' '.join(text.split())
        if text:
            if truncate and len(text) > 160:
                text = text[:157].rsplit(' ', 1)[0].rstrip('.,;:') + '...'
            return text
    return ''


def url_for(endpoint, **values):
    """Use the configured site origin for absolute URLs, not the proxy host."""
    external = values.pop('_external', False)
    url = flask_url_for(endpoint, **values)
    if external:
        return f"{current_app.config['SITE_URL'].rstrip('/')}{url}"
    return url


def _canonical_url():
    """Build a canonical URL without query tracking params."""
    args = dict(request.view_args or {})
    page = request.args.get('page', 1, type=int)
    if page and page > 1 and request.endpoint in {
        'main.inventory', 'main.vehicle_archive', 'main.blog_list',
        'main.inventory_by_city', 'main.inventory_by_make_or_body_city',
        'main.inventory_rebuilt_by_city',
    }:
        args['page'] = page
    return current_app.config['SITE_URL'].rstrip('/') + url_for(request.endpoint, **args)


def _service_area_map():
    """Return dict of slug -> (city, state) for configured service areas."""
    areas = current_app.config.get('SERVICE_AREAS', [])
    return {
        f"{city.lower().replace(' ', '-')}-{state.lower()}": (city, state)
        for city, state in areas
    }


def _available_makes():
    return [
        m[0] for m in db.session.query(Vehicle.make)
        .filter_by(status='available', is_coming_soon=False)
        .distinct()
        .order_by(Vehicle.make)
        .all()
    ]


def _available_body_styles():
    return [
        b[0] for b in db.session.query(Vehicle.body_style)
        .filter_by(status='available', is_coming_soon=False)
        .distinct()
        .order_by(Vehicle.body_style)
        .all()
        if b[0]
    ]


def _vehicle_list_query():
    """Base available-vehicle query with images eagerly loaded."""
    return (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(status='available', is_coming_soon=False)
    )


def _coming_soon_query():
    """Public teaser vehicles; these are not yet part of sale inventory."""
    return (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(status='available', is_coming_soon=True)
        .order_by(Vehicle.created_at.desc())
    )


def _available_model_landings():
    """Return unique, inventory-backed make/model landing-page facts."""
    rows = (
        db.session.query(
            Vehicle.make,
            Vehicle.model,
            db.func.count(Vehicle.id).label('vehicle_count'),
            db.func.min(Vehicle.year).label('min_year'),
            db.func.max(Vehicle.year).label('max_year'),
            db.func.min(Vehicle.display_price).label('min_price'),
            db.func.max(Vehicle.display_price).label('max_price'),
            db.func.min(Vehicle.mileage).label('min_mileage'),
            db.func.max(Vehicle.mileage).label('max_mileage'),
            db.func.max(Vehicle.updated_at).label('lastmod'),
        )
        .filter_by(status='available', is_coming_soon=False)
        .group_by(Vehicle.make, Vehicle.model)
        .order_by(Vehicle.make, Vehicle.model)
        .all()
    )
    landings = []
    seen_slugs = set()
    duplicate_slugs = set()
    for row in rows:
        slug = slugify(f'{row.make}-{row.model}')
        if slug in seen_slugs:
            duplicate_slugs.add(slug)
        seen_slugs.add(slug)
        landings.append({
            'slug': slug,
            'make': row.make,
            'model': row.model,
            'count': row.vehicle_count,
            'min_year': row.min_year,
            'max_year': row.max_year,
            'min_price': row.min_price,
            'max_price': row.max_price,
            'min_mileage': row.min_mileage,
            'max_mileage': row.max_mileage,
            'lastmod': row.lastmod,
        })
    return [landing for landing in landings if landing['slug'] not in duplicate_slugs]


def _build_inventory_query(make=None, model=None, body_style=None, title_status=None,
                           min_price=None, max_price=None, max_mileage=None, search=None,
                           sort='newest'):
    query = _vehicle_list_query()
    if make:
        query = query.filter(Vehicle.make == make)
    if model:
        query = query.filter(Vehicle.model == model)
    if body_style:
        query = query.filter(Vehicle.body_style == body_style)
    if title_status:
        query = query.filter(Vehicle.title_status == title_status)
    # hybrid_property display_price is SQL-safe
    if min_price is not None:
        query = query.filter(Vehicle.display_price >= min_price)
    if max_price is not None:
        query = query.filter(Vehicle.display_price <= max_price)
    if max_mileage is not None:
        query = query.filter(Vehicle.mileage <= max_mileage)
    if search:
        like = f'%{search.strip()}%'
        query = query.filter(or_(
            Vehicle.make.ilike(like),
            Vehicle.model.ilike(like),
            Vehicle.trim.ilike(like),
            Vehicle.year.cast(db.String).ilike(like),
            Vehicle.stock_number.ilike(like),
            Vehicle.exterior_color.ilike(like),
        ))

    sort_map = {
        'newest': Vehicle.created_at.desc(),
        'price_asc': Vehicle.display_price.asc(),
        'price_desc': Vehicle.display_price.desc(),
        'mileage_asc': Vehicle.mileage.asc(),
        'mileage_desc': Vehicle.mileage.desc(),
        'year_desc': Vehicle.year.desc(),
        'year_asc': Vehicle.year.asc(),
    }
    order = sort_map.get(sort or 'newest', Vehicle.created_at.desc())
    return query.order_by(order)


def _pagination_url(page_num, endpoint=None, **extra):
    endpoint = endpoint or request.endpoint
    view_args = dict(request.view_args or {})
    args = {k: v for k, v in request.args.items() if k != 'page' and v not in (None, '')}
    args.update(extra)
    args['page'] = page_num
    # Prefer path args from the current landing route
    return url_for(endpoint, **view_args, **args)


def _render_inventory(page, make=None, model=None, body_style=None, title_status=None,
                      min_price=None, max_price=None, max_mileage=None, search=None,
                      sort='newest', landing_h1=None, landing_intro=None,
                      landing_meta_title=None, landing_meta_description=None,
                      landing_breadcrumb=None, landing_canonical=None,
                      landing_faqs=None):
    """Shared inventory rendering helper."""
    if landing_canonical:
        landing_canonical = current_app.config['SITE_URL'].rstrip('/') + urlsplit(landing_canonical).path
    query = _build_inventory_query(
        make, model, body_style, title_status,
        min_price, max_price, max_mileage, search, sort=sort,
    )
    if page < 1:
        abort(404)
    pagination = query.paginate(page=page, per_page=12, error_out=False)
    if page > 1 and page > pagination.pages:
        abort(404)

    makes = _available_makes()
    body_styles = _available_body_styles()
    has_filters = any(value is not None and value != '' for value in (
        make, model, body_style, title_status, min_price, max_price, max_mileage, search,
    ))

    link_city = (request.view_args or {}).get('city') or current_app.config['BUSINESS_CITY'].lower().replace(' ', '-')
    link_state = (request.view_args or {}).get('state') or current_app.config['BUSINESS_STATE'].lower()
    area = _service_area_map().get(f'{link_city}-{link_state}')
    inventory_links = _inventory_landing_links(
        *area, makes, body_styles,
        _vehicle_list_query().filter_by(title_status='rebuilt').first() is not None,
    ) if area else []
    model_inventory_links = [
        {
            'label': f"Used {landing['make']} {landing['model']}",
            'url': url_for('main.inventory_by_model', slug=landing['slug']),
        }
        for landing in _available_model_landings()
    ] if request.endpoint == 'main.inventory' and not has_filters else []

    links = {}
    if pagination.has_prev:
        links['prev'] = _pagination_url(pagination.prev_num)
    if pagination.has_next:
        links['next'] = _pagination_url(pagination.next_num)

    city = current_app.config['BUSINESS_CITY']
    state = current_app.config['BUSINESS_STATE']
    biz = current_app.config['BUSINESS_NAME']

    if landing_meta_title:
        meta_title = landing_meta_title
    elif make and title_status == 'rebuilt':
        meta_title = f"Rebuilt Title {make} for Sale in {city}, {state} | {biz}"
    elif make:
        meta_title = f"Used {make} for Sale in {city}, {state} | {biz}"
    elif title_status == 'rebuilt':
        meta_title = f"Rebuilt Title Cars for Sale in {city}, {state} | {biz}"
    elif body_style:
        meta_title = f"Used {body_style}s for Sale in {city}, {state} | {biz}"
    else:
        meta_title = f"Used Cars for Sale in {city}, {state} | {biz}"

    if landing_meta_description:
        meta_description = landing_meta_description
    else:
        meta_description = (
            f"Browse {pagination.total}+ quality used cars, trucks, and SUVs for sale at {biz} "
            f"in {city}. Financing is available through Lendmark Financial Services. Visit us to apply for a vehicle you are interested in. Eligibility and terms vary."
        )

    if landing_breadcrumb:
        breadcrumbs = landing_breadcrumb
    else:
        breadcrumbs = structured_data_breadcrumb([
            ("Home", current_app.config['SITE_URL']),
            ("Used Cars for Sale", url_for('main.inventory', _external=True))
        ])

    arbitrary_filters = has_filters and request.endpoint == 'main.inventory'
    alternate_sort = sort != 'newest'
    canonical_override = landing_canonical or current_app.config['SITE_URL'].rstrip('/') + url_for('main.inventory')
    if page > 1 and not arbitrary_filters and not alternate_sort:
        canonical_override = f'{canonical_override}?page={page}'

    robots_content = (
        'noindex, follow' if arbitrary_filters or alternate_sort or not pagination.total
        else 'index, follow'
    )

    list_name = landing_h1 or f"Used Vehicles at {biz}"
    list_url = canonical_override
    structured_item_list = structured_data_item_list(
        pagination.items, name=list_name, list_url=list_url,
    ) if pagination.items else None

    structured_faq = structured_data_faq(landing_faqs) if landing_faqs else None

    analytics_inventory = {
        'search': search or '',
        'make': make or '',
        'body_style': body_style or '',
        'title_status': title_status or '',
        'min_price': min_price,
        'max_price': max_price,
        'max_mileage': max_mileage,
        'sort': sort or 'newest',
        'total': pagination.total,
        'page': page,
        'has_filters': bool(has_filters),
    }

    search_alert_form = InventorySearchAlertForm(
        make=make or '',
        body_style=body_style or '',
        title_status=title_status or '',
        min_price=min_price,
        max_price=max_price,
        max_mileage=max_mileage,
        search_query=search or '',
    )
    return render_template(
        'inventory.html',
        pagination=pagination,
        makes=makes,
        body_styles=body_styles,
        title_status=title_status or '',
        current_sort=sort or 'newest',
        page_links=links,
        page_url=_pagination_url,
        format_price=format_price,
        format_mileage=format_mileage,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_item_list=structured_item_list,
        structured_faq=structured_faq,
        breadcrumbs=breadcrumbs,
        meta_title=meta_title,
        meta_description=meta_description,
        landing_h1=landing_h1,
        landing_intro=landing_intro,
        landing_canonical=landing_canonical,
        canonical_override=canonical_override,
        robots_content=robots_content,
        page_type='inventory',
        analytics_inventory=analytics_inventory,
        landing_faqs=landing_faqs or [],
        inventory_links=inventory_links,
        model_inventory_links=model_inventory_links,
        landing_area=area if landing_h1 else None,
        buyer_guides=published_buyer_guides(),
        search_alert_form=search_alert_form,
        has_filters=bool(has_filters),
    )


class _EmptySiteSettings:
    """Shim used when SiteSetting/DB is unavailable (e.g. mid-500)."""

    @staticmethod
    def get(key, default=''):
        return default


def inject_globals():
    """Template globals for all pages, including app-level error handlers.

    Registered on the Flask app (not only the blueprint) so 404/500 templates
    that extend base.html still receive site_setting and business_* vars.
    Falls back to config-only values if the DB is unavailable (e.g. during 500).
    """
    def _setting(key, default=''):
        try:
            return SiteSetting.get(key, default)
        except Exception:
            return default

    try:
        gsc_raw = _setting('google_search_console', '') or ''
        gsc = sanitize_gsc_tag(gsc_raw)
        analytics_id = (
            _setting('google_analytics_id', '')
            or current_app.config.get('GOOGLE_ANALYTICS_ID', '')
        )
        google_tag_id = (
            _setting('google_tag_id', '')
            or current_app.config.get('GOOGLE_TAG_ID', '')
        )
        facebook_pixel_id = (
            _setting('facebook_pixel_id', '')
            or current_app.config.get('FACEBOOK_PIXEL_ID', '')
        )
        facebook_app_id = _setting('facebook_app_id', '') or current_app.config.get('FACEBOOK_APP_ID', '')
        twitter_handle = _setting('twitter_handle', '') or current_app.config.get('TWITTER_HANDLE', '')

        social = []
        for key, conf, network in [
            ('facebook_url', 'FACEBOOK_URL', 'facebook'),
            ('instagram_url', 'INSTAGRAM_URL', 'instagram'),
            ('youtube_url', 'YOUTUBE_URL', 'youtube'),
            ('google_business_url', 'GOOGLE_BUSINESS_URL', 'google'),
        ]:
            val = _setting(key, '') or current_app.config.get(conf, '')
            if val:
                social.append((network, val))

        try:
            canonical = _canonical_url() if getattr(request, 'endpoint', None) else current_app.config['SITE_URL']
        except Exception:
            canonical = current_app.config.get('SITE_URL', '')

        db_ok = True
        try:
            SiteSetting.get('site_title', current_app.config.get('BUSINESS_NAME', ''))
        except Exception:
            db_ok = False

        return {
            'business_name': current_app.config.get('BUSINESS_NAME', 'Marshall Auto, LLC'),
            'business_phone': current_app.config.get('BUSINESS_PHONE', ''),
            'business_email': current_app.config.get('BUSINESS_EMAIL', ''),
            'business_address': current_app.config.get('BUSINESS_ADDRESS', ''),
            'business_city': current_app.config.get('BUSINESS_CITY', ''),
            'business_state': current_app.config.get('BUSINESS_STATE', ''),
            'business_zip': current_app.config.get('BUSINESS_ZIP', ''),
            'business_hours': business_hours(),
            'business_latitude': _setting('business_latitude') or current_app.config.get('BUSINESS_LATITUDE', ''),
            'business_longitude': _setting('business_longitude') or current_app.config.get('BUSINESS_LONGITUDE', ''),
            'site_url': current_app.config.get('SITE_URL', ''),
            'site_title': _setting('site_title', current_app.config.get('BUSINESS_NAME', 'Marshall Auto, LLC')),
            'site_tagline': _setting('site_tagline', ''),
            'site_setting': SiteSetting if db_ok else _EmptySiteSettings,
            'google_tag_id': google_tag_id,
            'google_analytics_id': analytics_id,
            'google_search_console_tag': gsc,
            'facebook_pixel_id': facebook_pixel_id,
            'facebook_app_id': facebook_app_id,
            'twitter_handle': twitter_handle,
            'social_links': social,
            'now': dealership_now(),
            'canonical_url': canonical,
            'page_type': 'other',
            'analytics_vehicle': None,
            'analytics_inventory': None,
        }
    except Exception:
        # Last-resort minimal context so error templates can still render
        name = current_app.config.get('BUSINESS_NAME', 'Marshall Auto, LLC')
        return {
            'business_name': name,
            'business_phone': current_app.config.get('BUSINESS_PHONE', ''),
            'business_email': current_app.config.get('BUSINESS_EMAIL', ''),
            'business_address': current_app.config.get('BUSINESS_ADDRESS', ''),
            'business_city': current_app.config.get('BUSINESS_CITY', ''),
            'business_state': current_app.config.get('BUSINESS_STATE', ''),
            'business_zip': current_app.config.get('BUSINESS_ZIP', ''),
            'business_hours': business_hours(),
            'business_latitude': current_app.config.get('BUSINESS_LATITUDE', ''),
            'business_longitude': current_app.config.get('BUSINESS_LONGITUDE', ''),
            'site_url': current_app.config.get('SITE_URL', ''),
            'site_title': name,
            'site_tagline': '',
            'site_setting': _EmptySiteSettings,
            'google_tag_id': current_app.config.get('GOOGLE_TAG_ID', ''),
            'google_analytics_id': current_app.config.get('GOOGLE_ANALYTICS_ID', ''),
            'google_search_console_tag': '',
            'facebook_pixel_id': current_app.config.get('FACEBOOK_PIXEL_ID', ''),
            'facebook_app_id': current_app.config.get('FACEBOOK_APP_ID', ''),
            'twitter_handle': current_app.config.get('TWITTER_HANDLE', ''),
            'social_links': [],
            'now': dealership_now(),
            'canonical_url': current_app.config.get('SITE_URL', ''),
            'page_type': 'other',
            'analytics_vehicle': None,
            'analytics_inventory': None,
        }


@main.route('/')
@cached_public_response(ttl=60)
def index():
    featured = (
        _vehicle_list_query()
        .order_by(Vehicle.created_at.desc())
        .limit(6)
        .all()
    )
    makes = _available_makes()
    models = [
        m[0] for m in db.session.query(Vehicle.model)
        .filter_by(status='available', is_coming_soon=False)
        .distinct()
        .order_by(Vehicle.model)
        .all()
    ]

    structured_local = structured_data_local_business()
    structured_website = structured_data_website()
    breadcrumbs = structured_data_breadcrumb([
        (SiteSetting.get('site_title', current_app.config['BUSINESS_NAME']), current_app.config['SITE_URL'])
    ])
    home_faqs = [
        ("Do you offer financing for used cars?",
         f"Yes. {current_app.config['BUSINESS_NAME']} accepts secure online applications for vehicles in its inventory through Lendmark Financial Services. The lender determines eligibility, approval, and loan terms; approval is not guaranteed."),
        ("Does every vehicle come with a CarFax report?",
         "CarFax history reports are available on qualifying vehicles. Contact us for details on a specific car."),
        ("Can I trade in my current vehicle?",
         "Yes, we accept trade-ins and will give you a fair market value toward your next vehicle purchase."),
        ("What are rebuilt title cars?",
         "Rebuilt title cars were previously damaged and repaired, then passed state inspection. They are fully road-legal and cost 20-40% less than clean title vehicles."),
        ("Where is Marshall Auto LLC located?",
         f"We are located at {current_app.config['BUSINESS_ADDRESS']} in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}. We serve customers throughout central North Carolina."),
        ("Do you sell cars under $15,000?",
         "Yes, we have affordable used cars and rebuilt title vehicles at huge discounts. Browse our inventory or call us to discuss your budget.")
    ]
    structured_faq = structured_data_faq(home_faqs)

    from app.google_reviews import google_reviews_summary

    google_reviews = google_reviews_summary(limit=6)
    approved_reviews = google_reviews['reviews']
    aggregate = aggregate_rating_data()

    structured_item_list = structured_data_item_list(
        featured,
        name=f"Featured Vehicles at {current_app.config['BUSINESS_NAME']}",
        list_url=current_app.config['SITE_URL'],
    ) if featured else None

    areas = current_app.config.get('SERVICE_AREAS', [])[:9]

    return render_template(
        'index.html',
        featured=featured,
        makes=makes,
        models=models,
        approved_reviews=approved_reviews,
        aggregate=aggregate,
        google_reviews=google_reviews,
        structured_local=structured_local,
        structured_website=structured_website,
        structured_faq=structured_faq,
        structured_item_list=structured_item_list,
        breadcrumbs=breadcrumbs,
        meta_title=SiteSetting.get('site_title'),
        meta_description=SiteSetting.get('meta_description'),
        page_type='home',
        areas=areas,
        home_faqs=home_faqs,
        coming_soon=_coming_soon_query().limit(3).all(),
    )


@main.route('/inventory/models/<slug>')
def inventory_by_model(slug):
    """Inventory-backed search landing for a make/model currently in stock."""
    landing = next(
        (item for item in _available_model_landings() if item['slug'] == slug),
        None,
    )
    if not landing:
        abort(404)

    make = landing['make']
    model = landing['model']
    city = current_app.config['BUSINESS_CITY']
    state = current_app.config['BUSINESS_STATE']
    vehicle_label = f'{make} {model}'
    h1 = f"Used {vehicle_label} for Sale"
    if landing['count'] == 1:
        inventory_summary = (
            f"Our current {vehicle_label} listing is a {landing['min_year']} model "
            f"priced at {format_price(landing['min_price'])} with "
            f"{format_mileage(landing['min_mileage'])}."
        )
    else:
        year_range = (
            str(landing['min_year']) if landing['min_year'] == landing['max_year']
            else f"{landing['min_year']}–{landing['max_year']}"
        )
        price_range = (
            format_price(landing['min_price']) if landing['min_price'] == landing['max_price']
            else f"{format_price(landing['min_price'])}–{format_price(landing['max_price'])}"
        )
        mileage_range = (
            format_mileage(landing['min_mileage'])
            if landing['min_mileage'] == landing['max_mileage']
            else f"{format_mileage(landing['min_mileage'])}–{format_mileage(landing['max_mileage'])}"
        )
        inventory_summary = (
            f"Our current {vehicle_label} inventory includes {landing['count']} vehicles "
            f"from {year_range}, priced {price_range}, with mileage from {mileage_range}."
        )
    intro = (
        f"{inventory_summary} Browse current listings at {current_app.config['BUSINESS_NAME']} "
        f"in {city}, {state}. Inventory changes as vehicles are sold and added; contact us "
        f"to confirm availability or arrange a test drive."
    )
    meta_title = f"Used {vehicle_label} for Sale in {city}, {state} | {current_app.config['BUSINESS_NAME']}"
    meta_description = (
        f"Browse {landing['count']} used {vehicle_label} "
        f"{'for sale' if landing['count'] == 1 else 'listings'} at "
        f"{current_app.config['BUSINESS_NAME']} in {city}, {state}. "
        f"See current prices, mileage, photos, and vehicle details."
    )
    canonical = url_for('main.inventory_by_model', slug=slug, _external=True)
    breadcrumb = structured_data_breadcrumb([
        ('Home', current_app.config['SITE_URL']),
        ('Inventory', url_for('main.inventory', _external=True)),
        (f'Used {vehicle_label}', canonical),
    ])
    return _render_inventory(
        page=max(request.args.get('page', 1, type=int) or 1, 1),
        make=make,
        model=model,
        sort=request.args.get('sort', 'newest'),
        landing_h1=h1,
        landing_intro=intro,
        landing_meta_title=meta_title,
        landing_meta_description=meta_description,
        landing_breadcrumb=breadcrumb,
        landing_canonical=canonical,
    )


@main.route('/coming-soon')
def coming_soon():
    vehicles = _coming_soon_query().all()
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Coming Soon", url_for('main.coming_soon', _external=True)),
    ])
    return render_template(
        'coming_soon.html',
        vehicles=vehicles,
        breadcrumbs=breadcrumbs,
        meta_title=f"Coming Soon Vehicles | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"Get an early look at vehicles coming soon to {current_app.config['BUSINESS_NAME']} "
            f"in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}. "
            "Contact us to be among the first to know when they are ready."
        ),
        page_type='coming_soon',
    )


@main.route('/inventory')
@cached_public_response(ttl=45)
def inventory():
    page = request.args.get('page', 1, type=int)
    make = request.args.get('make', '').strip()
    model = request.args.get('model', '').strip()
    body_style = request.args.get('body_style', '').strip()
    min_price = request.args.get('min_price', type=int)
    max_price = request.args.get('max_price', type=int)
    max_mileage = request.args.get('max_mileage', type=int)
    search = request.args.get('q', '').strip()
    title_status = request.args.get('title_status', '').strip()
    sort = request.args.get('sort', 'newest').strip() or 'newest'

    return _render_inventory(
        page=page, make=make or None, model=model or None, body_style=body_style or None,
        title_status=title_status or None, min_price=min_price, max_price=max_price,
        max_mileage=max_mileage, search=search or None, sort=sort,
    )


def _city_landing_faqs(city_name, state_name):
    biz = current_app.config['BUSINESS_NAME']
    phone = current_app.config['BUSINESS_PHONE']
    home_city = current_app.config['BUSINESS_CITY']
    return [
        (f"Where can I buy used cars near {city_name}, {state_name}?",
         f"{biz} in {home_city} serves {city_name} shoppers with inspected used cars, trucks, and SUVs. "
         f"Call {phone} to schedule a visit, test drive, or delivery options."),
        (f"Does {biz} offer financing for buyers in {city_name}?",
         f"Yes. We offer financing through Lendmark Financial Services. "
         f"Visit our dealership to complete an application for a vehicle you are interested in. Eligibility, approval, and terms are determined by the lender and may depend on the applicant and vehicle. Call {phone} to arrange your visit."),
        (f"Can I get a rebuilt title car delivered near {city_name}?",
         f"Many customers from {city_name} purchase rebuilt title vehicles at a discount. Contact us to "
         f"confirm availability, inspection details, and pickup or delivery arrangements."),
    ]


@main.route('/inventory/used-cars-for-sale-in-<city>-<state>')
def inventory_by_city(city, state):
    """Local SEO landing page: used cars in a specific city."""
    slug = f"{city.lower()}-{state.lower()}"
    area = _service_area_map().get(slug)
    if not area:
        return redirect(url_for('main.inventory'))

    page = request.args.get('page', 1, type=int)
    sort = request.args.get('sort', 'newest')
    city_name, state_name = area
    biz = current_app.config['BUSINESS_NAME']
    home_city = current_app.config['BUSINESS_CITY']
    canonical = url_for('main.inventory_by_city', city=city, state=state, _external=True)
    h1 = f"Used Cars, Trucks & SUVs for Sale in {city_name}, {state_name}"
    intro = (
        f"Looking for used cars for sale in {city_name}, {state_name}? {biz} in nearby {home_city} "
        f"helps {city_name} drivers find reliable pre-owned vehicles with transparent pricing, trade-ins, "
        f"CarFax reports, and rebuilt title inventory. Financing is available through Lendmark Financial Services; visit us to apply for a vehicle you are interested in. Browse online or visit "
        f"our lot — we regularly work with customers throughout the {city_name} area."
    )
    meta_title = f"Used Cars for Sale in {city_name}, {state_name} | {biz}"
    meta_description = (
        f"Shop used cars, trucks, and SUVs for sale near {city_name}, {state_name}. "
        f"Financing is available through Lendmark Financial Services. Visit us to apply for a vehicle you are interested in. Eligibility, approval, and terms vary."
    )
    breadcrumb = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Used Cars for Sale", url_for('main.inventory', _external=True)),
        (f"Used Cars in {city_name}, {state_name}", canonical)
    ])
    return _render_inventory(
        page=page, sort=sort,
        landing_h1=h1, landing_intro=intro,
        landing_meta_title=meta_title, landing_meta_description=meta_description,
        landing_breadcrumb=breadcrumb, landing_canonical=canonical,
        landing_faqs=_city_landing_faqs(city_name, state_name),
    )


BODY_STYLE_SLUG_MAP = {
    'suvs': 'SUV', 'suv': 'SUV',
    'trucks': 'Truck', 'truck': 'Truck',
    'sedans': 'Sedan', 'sedan': 'Sedan',
    'coupes': 'Coupe', 'coupe': 'Coupe',
    'vans': 'Van', 'van': 'Van',
    'hatchbacks': 'Hatchback', 'hatchback': 'Hatchback',
    'wagons': 'Wagon', 'wagon': 'Wagon',
    'convertibles': 'Convertible', 'convertible': 'Convertible',
}

BODY_STYLE_PLURAL_SLUG = {
    'SUV': 'suvs', 'Truck': 'trucks', 'Sedan': 'sedans', 'Coupe': 'coupes',
    'Van': 'vans', 'Hatchback': 'hatchbacks', 'Wagon': 'wagons', 'Convertible': 'convertibles',
}


def _inventory_landing_links(city, state, makes, body_styles, has_rebuilt):
    city_slug = city.lower().replace(' ', '-')
    state_slug = state.lower()
    links = []
    primary_cities = {
        current_app.config['BUSINESS_CITY'].lower(),
        'raleigh', 'durham', 'chapel hill', 'cary', 'fayetteville',
    }
    categories = []
    if city.lower() in primary_cities:
        categories.extend((make, make.lower().replace(' ', '-')) for make in makes[:8]
                          if make.lower().replace(' ', '-') not in BODY_STYLE_SLUG_MAP
                          and make.lower() != 'cars')
    categories.extend((style, BODY_STYLE_PLURAL_SLUG[style]) for style in body_styles
                      if style in BODY_STYLE_PLURAL_SLUG)
    for label, slug in categories:
        links.append({
            'label': f'Used {label}',
            'url': url_for('main.inventory_by_make_or_body_city', slug=slug,
                           city=city_slug, state=state_slug, _external=True),
        })
    if has_rebuilt:
        links.append({
            'label': 'Rebuilt Title Vehicles',
            'url': url_for('main.inventory_rebuilt_by_city', city=city_slug,
                           state=state_slug, _external=True),
        })
    return links


@main.route('/inventory/used-<slug>-for-sale-in-<city>-<state>')
def inventory_by_make_or_body_city(slug, city, state):
    """Local SEO landing: used [make] OR used [body style] in a city.

    Body-style slugs (suvs, trucks, ...) take precedence over make names so the
    shared URL pattern does not collide.
    """
    if slug.lower() == 'cars':
        return inventory_by_city(city, state)
    area_slug = f"{city.lower()}-{state.lower()}"
    area = _service_area_map().get(area_slug)
    page = request.args.get('page', 1, type=int)
    sort = request.args.get('sort', 'newest')

    # --- Body style branch ---
    body_style = BODY_STYLE_SLUG_MAP.get(slug.lower())
    if body_style:
        if not area:
            return redirect(url_for('main.inventory', body_style=body_style))
        available = _available_body_styles()
        if body_style not in available:
            match = next((b for b in available if b and b.lower() == body_style.lower()), None)
            if not match:
                return redirect(url_for('main.inventory_by_city', city=city, state=state))
            body_style = match
        city_name, state_name = area
        biz = current_app.config['BUSINESS_NAME']
        plural_slug = BODY_STYLE_PLURAL_SLUG.get(body_style, slug.lower())
        display_plural = 'SUVs' if body_style == 'SUV' else (
            body_style if body_style.endswith('s') else body_style + 's'
        )
        canonical = url_for(
            'main.inventory_by_make_or_body_city',
            slug=plural_slug, city=city, state=state, _external=True,
        )
        h1 = f"Used {display_plural} for Sale in {city_name}, {state_name}"
        intro = (
            f"Browse used {display_plural.lower()} for sale near {city_name}, {state_name}. "
            f"{biz} offers inspected pre-owned {display_plural.lower()}, trade-ins, CarFax reports, and rebuilt title deals when available. "
            f"Financing is available through Lendmark Financial Services. Visit us to apply for a vehicle you are interested in."
        )
        meta_title = f"Used {display_plural} for Sale in {city_name}, {state_name} | {biz}"
        meta_description = (
            f"Shop used {display_plural.lower()} for sale near {city_name}, {state_name}. "
            f"{biz} offers financing through Lendmark Financial Services. Visit us to apply for a vehicle you are interested in."
        )
        breadcrumb = structured_data_breadcrumb([
            ("Home", current_app.config['SITE_URL']),
            ("Used Cars for Sale", url_for('main.inventory', _external=True)),
            (f"Used {display_plural} in {city_name}", canonical),
        ])
        faqs = [
            (f"What used {display_plural.lower()} do you have near {city_name}?",
             f"Our inventory changes often. Filter by body style online or call {current_app.config['BUSINESS_PHONE']} "
             f"for the latest {display_plural.lower()} available to {city_name} shoppers."),
        ]
        return _render_inventory(
            page=page, body_style=body_style, sort=sort,
            landing_h1=h1, landing_intro=intro,
            landing_meta_title=meta_title, landing_meta_description=meta_description,
            landing_breadcrumb=breadcrumb, landing_canonical=canonical,
            landing_faqs=faqs,
        )

    # --- Make branch ---
    make_name = slug.replace('-', ' ').title()
    if not area:
        return redirect(url_for('main.inventory', make=make_name))

    city_name, state_name = area
    biz = current_app.config['BUSINESS_NAME']
    canonical = url_for(
        'main.inventory_by_make_or_body_city',
        slug=slug.lower().replace(' ', '-'),
        city=city, state=state, _external=True,
    )
    h1 = f"Used {make_name} for Sale in {city_name}, {state_name}"
    intro = (
        f"Shop used {make_name} vehicles for sale near {city_name}, {state_name}. "
        f"{biz} stocks inspected {make_name} cars, trucks, and SUVs with competitive pricing, including rebuilt title {make_name} models at a discount when available. "
        f"Financing is available through Lendmark Financial Services. Visit us to apply for a vehicle you are interested in."
    )
    meta_title = f"Used {make_name} for Sale in {city_name}, {state_name} | {biz}"
    meta_description = (
        f"Shop used {make_name} cars, trucks, and SUVs for sale near {city_name}, {state_name}. "
        f"Huge discounts on rebuilt title {make_name} vehicles. Ask lenders about eligibility for a specific vehicle; approval and terms vary."
    )
    breadcrumb = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Used Cars for Sale", url_for('main.inventory', _external=True)),
        (f"Used {make_name} in {city_name}, {state_name}", canonical)
    ])
    faqs = [
        (f"Do you have used {make_name} inventory for {city_name} buyers?",
         f"Yes. Browse our current {make_name} listings online or call {current_app.config['BUSINESS_PHONE']} "
         f"and we will help {city_name} shoppers find the right match."),
        (f"Can I finance a used {make_name} from {biz}?",
         "Yes. Apply securely online for an available vehicle from our inventory. Lendmark Financial Services determines vehicle eligibility, approval, and loan terms; approval is not guaranteed."),
    ]
    return _render_inventory(
        page=page, make=make_name, sort=sort,
        landing_h1=h1, landing_intro=intro,
        landing_meta_title=meta_title, landing_meta_description=meta_description,
        landing_breadcrumb=breadcrumb, landing_canonical=canonical,
        landing_faqs=faqs,
    )


@main.route('/inventory/rebuilt-title-cars-for-sale-in-<city>-<state>')
def inventory_rebuilt_by_city(city, state):
    """Local SEO landing page: rebuilt title cars in a specific city."""
    slug = f"{city.lower()}-{state.lower()}"
    area = _service_area_map().get(slug)
    if not area:
        return redirect(url_for('main.inventory', title_status='rebuilt'))

    page = request.args.get('page', 1, type=int)
    sort = request.args.get('sort', 'newest')
    city_name, state_name = area
    biz = current_app.config['BUSINESS_NAME']
    canonical = url_for('main.inventory_rebuilt_by_city', city=city, state=state, _external=True)
    h1 = f"Rebuilt Title Cars for Sale in {city_name}, {state_name}"
    intro = (
        f"Save big on rebuilt title cars, trucks, and SUVs near {city_name}, {state_name}. "
        f"Every rebuilt title vehicle at {biz} is inspected and road-ready after proper repairs and "
        f"state requirements. Ideal for budget-conscious buyers in the {city_name} area who want more car for less."
    )
    meta_title = f"Rebuilt Title Cars for Sale in {city_name}, {state_name} | {biz}"
    meta_description = (
        f"Huge discounts on rebuilt title cars for sale near {city_name}, {state_name}. "
        f"Financing is available through Lendmark Financial Services. Visit us to apply for a specific vehicle; eligibility, approval, and terms vary."
    )
    breadcrumb = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Used Cars for Sale", url_for('main.inventory', _external=True)),
        (f"Rebuilt Title Cars in {city_name}, {state_name}", canonical)
    ])
    faqs = [
        (f"Are rebuilt title cars legal to drive in {city_name}, {state_name}?",
         "Yes. Rebuilt title vehicles that have passed required inspections are fully road-legal. "
         "We explain title history and help you review CarFax details before you buy."),
        (f"Why buy a rebuilt title car near {city_name}?",
         "Rebuilt title vehicles typically cost 20–40% less than comparable clean-title cars, "
         "making them a strong value option when properly inspected."),
    ]
    return _render_inventory(
        page=page, title_status='rebuilt', sort=sort,
        landing_h1=h1, landing_intro=intro,
        landing_meta_title=meta_title, landing_meta_description=meta_description,
        landing_breadcrumb=breadcrumb, landing_canonical=canonical,
        landing_faqs=faqs,
    )


@main.route('/service-area')
def service_area():
    """Service area hub page for local SEO."""
    areas = current_app.config.get('SERVICE_AREAS', [])
    makes = _available_makes()
    body_styles = _available_body_styles()
    has_rebuilt = _vehicle_list_query().filter_by(title_status='rebuilt').first() is not None
    area_inventory_links = {
        city: _inventory_landing_links(city, state, makes, body_styles, has_rebuilt)
        for city, state in areas
    }
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Service Area", url_for('main.service_area', _external=True))
    ])
    faqs = [
        (f"Does {current_app.config['BUSINESS_NAME']} deliver vehicles?",
         f"Yes, we regularly serve customers throughout central North Carolina and can arrange delivery or meeting options for many areas. Call {current_app.config['BUSINESS_PHONE']} for details."),
        ("Do you offer financing for out-of-town buyers?",
         "Yes. Visit Marshall Auto to complete a Lendmark Financial Services application for a vehicle you are interested in. The lender determines eligibility, approval, and terms; approval is not guaranteed."),
        ("Can I buy a rebuilt title car in another city?",
         "Absolutely. Browse our rebuilt title inventory online and contact us to arrange a test drive or delivery near you.")
    ]
    return render_template(
        'service_area.html',
        areas=areas,
        buyer_guides=published_buyer_guides(),
        area_inventory_links=area_inventory_links,
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_faq=structured_data_faq(faqs),
        meta_title=f"Used Car Dealer Service Area | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"{current_app.config['BUSINESS_NAME']} serves Sanford, Chapel Hill, Durham, Raleigh, Cary, Apex, "
            f"Fayetteville, and surrounding NC communities with quality used cars and financing."
        ),
        page_type='service_area',
    )


@main.route('/vin/<vin>')
def vehicle_vin_record(vin):
    normalized_vin = normalize_vin(vin)
    if not is_valid_vin(normalized_vin):
        abort(404)
    if vin != normalized_vin:
        return redirect(url_for('main.vehicle_vin_record', vin=normalized_vin), code=301)

    vehicle = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(vin=normalized_vin)
        .first_or_404()
    )
    if vehicle.is_coming_soon and vehicle.status == 'available':
        abort(404)

    related = (
        _vehicle_list_query()
        .filter(
            Vehicle.id != vehicle.id,
            or_(Vehicle.make == vehicle.make, Vehicle.body_style == vehicle.body_style),
        )
        .order_by(Vehicle.created_at.desc())
        .limit(4)
        .all()
    )
    structured_vehicle = structured_data_vehicle(vehicle)
    canonical = _canonical_url()
    listing_status = (
        'currently available' if vehicle.status == 'available'
        else 'sold' if vehicle.status == 'sold'
        else 'no longer available'
    )
    meta_title = f"VIN {normalized_vin} Lookup: {vehicle.title} | {current_app.config['BUSINESS_NAME']}"
    meta_description = (
        f"Look up VIN {normalized_vin}, a {vehicle.title} recorded by "
        f"{current_app.config['BUSINESS_NAME']} as {listing_status}. "
        f"Review vehicle specifications, listing status, and similar {vehicle.make} vehicles."
    )

    return render_template(
        'vin_record.html',
        vehicle=vehicle,
        related=related,
        meta_title=meta_title,
        meta_description=meta_description,
        robots_content='index, follow',
        og_image_absolute=vehicle.primary_image_url(absolute=True),
        structured_vehicle=structured_vehicle,
        breadcrumbs=structured_data_breadcrumb([
            ('Home', current_app.config['SITE_URL']),
            (f'VIN {normalized_vin}', canonical),
        ]),
        structured_page={
            '@context': 'https://schema.org',
            '@type': 'ItemPage',
            '@id': canonical + '#webpage',
            'url': canonical,
            'name': meta_title,
            'datePublished': vehicle.created_at.isoformat() + 'Z',
            'dateModified': (vehicle.updated_at or vehicle.created_at).isoformat() + 'Z',
            'mainEntity': {'@id': structured_vehicle['@id']},
        },
        page_type='vin_record',
    )


def _shopper_submission(form, vehicle, creator):
    if form.honeypot.data:
        return True
    if rate_limit_exceeded(f"shopper:{client_ip()}", 8, 600):
        flash('Too many requests. Please call the dealership or try again shortly.', 'warning')
        return False
    spam = check_lead_spam_for_request(
        current_app.config,
        name=form.name.data,
        email=form.email.data,
        phone=form.phone.data,
        message=request.form.get('notes', ''),
    )
    if spam.is_spam:
        flash('We could not accept that request. Please call the dealership.', 'warning')
        return False
    try:
        creator()
    except Exception as exc:
        from app.shopper import ShopperRequestError
        db.session.rollback()
        if isinstance(exc, ShopperRequestError):
            flash(str(exc), 'warning')
            return False
        current_app.logger.exception('Shopper request failed')
        flash('We could not save that request. Please call the dealership.', 'danger')
        return False
    flash('Request received. It is not confirmed until our team contacts you.', 'success')
    return True


@main.route('/inventory/<slug>/test-drive', methods=['POST'])
def request_test_drive(slug):
    vehicle = Vehicle.query.filter_by(slug=slug, status='available').first_or_404()
    form = TestDriveRequestForm()
    from app.shopper import appointment_slots, create_test_drive_request
    slots = {slot.strftime('%Y-%m-%dT%H:%M'): slot for slot in appointment_slots()}
    form.appointment_at.choices = list(slots)
    if form.validate_on_submit():
        _shopper_submission(
            form, vehicle,
            lambda: create_test_drive_request(
                vehicle, name=form.name.data, email=form.email.data, phone=form.phone.data,
                appointment_at=slots[form.appointment_at.data], notes=form.notes.data or '',
            ),
        )
    else:
        flash('Check the test-drive form and try again.', 'warning')
    return redirect(url_for('main.vehicle_detail', slug=slug) + '#shopper-tools')


@main.route('/inventory/<slug>/hold', methods=['POST'])
def request_hold(slug):
    Vehicle.query.filter_by(slug=slug, status='available').first_or_404()
    flash(
        'A hold cannot be requested online. Come to the dealership in person and pay the deposit '
        'in cash or by cashier\'s check with the original receipt from the issuing bank branch.',
        'warning',
    )
    return redirect(url_for('main.vehicle_detail', slug=slug) + '#shopper-tools')


@main.route('/inventory/<slug>/alert', methods=['POST'])
def request_alert(slug):
    vehicle = Vehicle.query.filter_by(slug=slug).first_or_404()
    form = InventoryAlertForm()
    form.alert_type.choices = [
        ('vehicle', f'Price drop on this {vehicle.title}'),
        ('make', f'New {vehicle.make} arrivals') if vehicle.make else None,
        ('body_style', f'New {vehicle.body_style} arrivals') if vehicle.body_style else None,
    ]
    form.alert_type.choices = [choice for choice in form.alert_type.choices if choice]
    if form.validate_on_submit():
        from app.shopper import create_inventory_alert
        _shopper_submission(
            form, vehicle,
            lambda: create_inventory_alert(
                vehicle, name=form.name.data, email=form.email.data, phone=form.phone.data,
                alert_type=form.alert_type.data, max_price=form.max_price.data,
            ),
        )
    else:
        flash('Check the alert form and try again.', 'warning')
    return redirect(url_for('main.vehicle_detail', slug=slug) + '#shopper-tools')


@main.route('/inventory/alerts/search', methods=['POST'])
def request_search_alert():
    """Save a filter-based inventory alert from the inventory page."""
    form = InventorySearchAlertForm()
    next_url = request.form.get('next') or url_for('main.inventory')
    if form.validate_on_submit():
        from app.shopper import ShopperRequestError, create_inventory_alert
        try:
            create_inventory_alert(
                None,
                name=form.name.data,
                email=form.email.data,
                phone=form.phone.data,
                alert_type='search',
                make=form.make.data,
                body_style=form.body_style.data,
                title_status=form.title_status.data,
                min_price=form.min_price.data,
                max_price=form.max_price.data,
                max_mileage=form.max_mileage.data,
                search_query=form.search_query.data,
            )
            flash(
                'Got it — we will email you when a matching vehicle is listed. '
                'This is an availability notice, not a financing offer.',
                'success',
            )
        except ShopperRequestError as exc:
            flash(str(exc), 'warning')
    else:
        flash('Check the notify-me form and try again.', 'warning')
    # Stay on a safe internal path
    if not next_url.startswith('/') or next_url.startswith('//'):
        next_url = url_for('main.inventory')
    return redirect(next_url + '#inventory-search-alert')


@main.route('/inventory/<slug>')
def vehicle_detail(slug):
    vehicle = (
        Vehicle.query
        .options(
            selectinload(Vehicle.images),
            selectinload(Vehicle.service_records),
            selectinload(Vehicle.carfax_reports),
            selectinload(Vehicle.reviews),
        )
        .filter_by(slug=slug)
        .first_or_404()
    )
    if vehicle.is_coming_soon and vehicle.status == 'available':
        abort(404)
    robots = 'index, follow'
    if vehicle.status != 'available':
        flash('This vehicle is no longer available.', 'info')

    from app.pricing import payment_examples
    from app.shopper import appointment_slots, similar_vehicles, vehicle_interest_message

    related = similar_vehicles(vehicle)
    payments = payment_examples(vehicle.display_price)
    slots = appointment_slots()
    interest_message = vehicle_interest_message(vehicle)
    from app.google_reviews import google_reviews_summary

    google_reviews = google_reviews_summary(limit=3)
    dealership_reviews = google_reviews['reviews']
    review_aggregate = aggregate_rating_data()

    form = ContactForm(vehicle_id=vehicle.id)
    if not form.message.data:
        form.message.data = interest_message
    test_drive_form = TestDriveRequestForm()
    test_drive_form.appointment_at.choices = [
        (slot.strftime('%Y-%m-%dT%H:%M'), slot.strftime('%A, %B %-d at %-I:%M %p'))
        for slot in slots
    ]
    alert_form = InventoryAlertForm()
    alert_form.alert_type.choices = [('vehicle', f'Price drop on this {vehicle.title}')]
    if vehicle.make:
        alert_form.alert_type.choices.append(('make', f'New {vehicle.make} arrivals'))
    if vehicle.body_style:
        alert_form.alert_type.choices.append(('body_style', f'New {vehicle.body_style} arrivals'))
    structured_vehicle = structured_data_vehicle(vehicle)

    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Inventory", url_for('main.inventory', _external=True)),
        (vehicle.title, url_for('main.vehicle_detail', slug=vehicle.slug, _external=True))
    ])

    location_phrase = f" in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}"
    listing_status = 'for Sale' if vehicle.status == 'available' else ('Sold' if vehicle.status == 'sold' else 'Unavailable')
    vin_phrase = f" | VIN {vehicle.vin}" if vehicle.vin else ''
    meta_title = (
        vehicle.seo_title if vehicle.seo_title and vehicle.status == 'available'
        else f"{vehicle.title} {listing_status}{location_phrase} | {current_app.config['BUSINESS_NAME']}"
    ) + vin_phrase

    title_phrase = (
        f"{vehicle.title_status.replace('_', ' ').title()} title" if vehicle.title_status
        else "clean title"
    )
    meta_description = vehicle.seo_description or (
        f"Shop this {vehicle.title} with {vehicle.mileage:,} miles at {current_app.config['BUSINESS_NAME']}. "
        f"{vehicle.condition.title()} condition, {title_phrase}. Financing is available through Lendmark Financial Services; visit us to apply for this vehicle."
    )
    if vehicle.status != 'available':
        meta_description = (
            f"This {vehicle.title} is {listing_status.lower()} at {current_app.config['BUSINESS_NAME']} "
            f"in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}. "
            "View its listing details and browse similar used cars, trucks, and SUVs currently for sale."
        )
    if vehicle.vin:
        meta_description = f"VIN {vehicle.vin}. {meta_description}"
    meta_keywords = vehicle.meta_keywords or ', '.join(filter(None, [
        str(vehicle.year), vehicle.make, vehicle.model, vehicle.trim,
        vehicle.body_style, vehicle.exterior_color, current_app.config['BUSINESS_CITY'],
        current_app.config['BUSINESS_STATE'], 'used car for sale', title_phrase
    ]))

    og_image = vehicle.primary_image_url(absolute=True)

    analytics_vehicle = {
        'id': vehicle.id,
        'title': vehicle.title,
        'year': vehicle.year,
        'make': vehicle.make,
        'model': vehicle.model,
        'trim': vehicle.trim or '',
        'price': float(vehicle.display_price) if vehicle.display_price is not None else 0,
        'stock_number': vehicle.stock_number or '',
        'body_style': vehicle.body_style or '',
        'title_status': vehicle.title_status or '',
        'mileage': vehicle.mileage,
        'slug': vehicle.slug,
    }

    vin_decode_groups = group_vin_decode_data(vehicle.vin_decode_data)
    return render_template(
        'vehicle_detail.html',
        vehicle=vehicle,
        vin_decode_groups=vin_decode_groups,
        vin_decode_field_count=sum(len(group['fields']) for group in vin_decode_groups),
        buyer_guides=published_buyer_guides(),
        related=related,
        form=form,
        payment_examples=payments,
        test_drive_form=test_drive_form,
        alert_form=alert_form,
        interest_message=interest_message,
        dealership_reviews=dealership_reviews,
        review_aggregate=review_aggregate,
        google_reviews=google_reviews,
        structured_vehicle=structured_vehicle,
        structured_local=structured_data_local_business(),
        breadcrumbs=breadcrumbs,
        meta_title=meta_title,
        meta_description=meta_description,
        meta_keywords=meta_keywords,
        og_image_absolute=og_image,
        robots_content=robots,
        page_links={},
        format_price=format_price,
        format_mileage=format_mileage,
        page_type='vehicle_detail',
        analytics_vehicle=analytics_vehicle,
        structured_page={
            '@context': 'https://schema.org',
            '@type': 'WebPage',
            '@id': _canonical_url() + '#webpage',
            'url': _canonical_url(),
            'name': meta_title,
            'datePublished': vehicle.created_at.isoformat() + 'Z',
            'dateModified': (vehicle.updated_at or vehicle.created_at).isoformat() + 'Z',
            'mainEntity': {'@id': structured_vehicle['url'] + '#vehicle'},
        },
    )


@main.route('/inventory/history')
def vehicle_archive():
    page = max(request.args.get('page', 1, type=int) or 1, 1)
    pagination = (
        Vehicle.query.filter(Vehicle.status != 'available')
        .order_by(Vehicle.updated_at.desc(), Vehicle.id.desc())
        .paginate(page=page, per_page=24, error_out=False)
    )
    return render_template(
        'vehicle_archive.html',
        pagination=pagination,
        meta_title=f"Sold Vehicle & VIN Archive in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']} | {current_app.config['BUSINESS_NAME']}",
        meta_description=f"Browse previous vehicle listings and VIN details from our {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']} dealership. Find similar used cars, trucks, and SUVs in our current inventory.",
        page_type='vehicle_archive',
    )


@main.route('/api/vehicles-summary')
def api_vehicles_summary():
    """JSON vehicle summaries for client-side favorites/compare/recently-viewed (localStorage)."""
    raw_slugs = (request.args.get('slugs') or '').split(',')
    slugs = [s.strip() for s in raw_slugs if s.strip()][:24]
    if not slugs:
        return jsonify({'vehicles': []})
    vehicles = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter(Vehicle.slug.in_(slugs), Vehicle.is_coming_soon.is_(False))
        .all()
    )
    by_slug = {v.slug: v for v in vehicles}
    ordered = [by_slug[s] for s in slugs if s in by_slug]
    return jsonify({'vehicles': [
        {
            'slug': v.slug,
            'title': v.title,
            'image_alt': v.image_alt(context='related'),
            'url': url_for('main.vehicle_detail', slug=v.slug),
            'image': v.primary_image_url(),
            'price': format_price(v.display_price),
            'price_raw': float(v.display_price) if v.display_price is not None else None,
            'mileage': format_mileage(v.mileage),
            'is_low_mileage': v.is_low_mileage,
            'low_mileage_badge': format_mileage_badge(v.mileage) if v.is_low_mileage else '',
            'year': v.year,
            'make': v.make,
            'model': v.model,
            'trim': v.trim or '',
            'body_style': v.body_style or '',
            'fuel_type': v.fuel_type or '',
            'drivetrain': v.drivetrain or '',
            'transmission': v.transmission or '',
            'engine': v.engine or '',
            'mpg_city': v.mpg_city,
            'mpg_highway': v.mpg_highway,
            'mpg_combined': v.mpg_combined,
            'doors': v.doors,
            'passenger_capacity': v.passenger_capacity,
            'gvwr': v.gvwr or '',
            'features': ', '.join(v.feature_list),
            'exterior_features': v.exterior_features or '',
            'interior_features': v.interior_features or '',
            'entertainment_features': '\n'.join(v.entertainment_feature_list),
            'mechanical_features': v.mechanical_features or '',
            'safety_features': v.safety_features or '',
            'additional_specifications': v.additional_specifications or '',
            'exterior_color': v.exterior_color or '',
            'condition': v.condition,
            'title_status': v.title_status,
            'status': v.status,
        }
        for v in ordered
    ]})


@main.route('/saved-vehicles')
def saved_vehicles():
    return render_template(
        'saved_vehicles.html',
        meta_title=f"Saved Vehicles | {current_app.config['BUSINESS_NAME']}",
        meta_description='Vehicles you have saved for later.',
        robots_content='noindex, follow',
        page_type='saved_vehicles',
    )


@main.route('/compare')
def compare_vehicles():
    return render_template(
        'compare.html',
        meta_title=f"Compare Vehicles | {current_app.config['BUSINESS_NAME']}",
        meta_description='Compare specs and pricing side-by-side for vehicles you are considering.',
        robots_content='noindex, follow',
        page_type='compare',
    )


def _attribution_from_request():
    """Capture marketing attribution params (no PII)."""
    def clip(key, maxlen=128):
        val = (request.form.get(key) or request.args.get(key) or '').strip()
        return val[:maxlen] if val else None

    referrer = (request.headers.get('Referer') or '')[:512] or None
    landing = (request.form.get('landing_path') or request.path or '')[:512] or None
    return {
        'utm_source': clip('utm_source'),
        'utm_medium': clip('utm_medium'),
        'utm_campaign': clip('utm_campaign'),
        'utm_term': clip('utm_term'),
        'utm_content': clip('utm_content'),
        'gclid': clip('gclid', 255),
        'fbclid': clip('fbclid', 255),
        'landing_path': landing,
        'referrer': referrer,
    }


def _build_lead_from_form(form, source='contact'):
    vehicle_id = parse_optional_int(form.vehicle_id.data)
    if vehicle_id is not None:
        exists = db.session.get(Vehicle, vehicle_id)
        if not exists:
            vehicle_id = None
    attr = _attribution_from_request()
    name = (form.name.data or '').strip()[:128]
    email = (form.email.data or '').strip()[:128]
    phone = format_phone_number(form.phone.data)[:32] or None
    message = (form.message.data or '').strip() or None

    spam = check_lead_spam_for_request(
        current_app.config,
        name=name,
        email=email,
        phone=phone or '',
        message=message or '',
        user_ip=client_ip(),
        user_agent=request.headers.get('User-Agent', ''),
        referrer=request.headers.get('Referer', ''),
        permalink=request.url,
    )

    lead = Lead(
        name=name,
        email=email,
        phone=phone,
        message=message,
        vehicle_id=vehicle_id,
        source=(source or 'contact')[:64],
        is_spam=spam.is_spam,
        spam_score=spam.score,
        spam_reasons=spam.reason_text[:512] or None,
        utm_source=attr['utm_source'],
        utm_medium=attr['utm_medium'],
        utm_campaign=attr['utm_campaign'],
        utm_term=attr['utm_term'],
        utm_content=attr['utm_content'],
        gclid=attr['gclid'],
        fbclid=attr['fbclid'],
        landing_path=attr['landing_path'],
        referrer=attr['referrer'],
    )
    return lead


def _notify_lead(lead):
    if lead.is_spam:
        current_app.logger.info(
            'Lead %s quarantined as spam (score=%s): %s',
            lead.id, lead.spam_score, lead.spam_reasons,
        )
    else:
        notify_new_lead(lead)
        send_lead_confirmation(lead)


def _create_lead_from_form(form, source='contact'):
    lead = _build_lead_from_form(form, source)
    db.session.add(lead)
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception('Lead save failed (source=%s)', source)
        return None
    _notify_lead(lead)
    return lead


@main.route('/contact', methods=['GET', 'POST'])
def contact():
    form = ContactForm()
    if request.method == 'GET':
        vehicle_id = request.args.get('vehicle_id', type=int)
        if vehicle_id:
            vehicle = Vehicle.query.filter_by(
                id=vehicle_id, status='available', is_coming_soon=True,
            ).first()
            if vehicle:
                form.vehicle_id.data = str(vehicle.id)
                form.message.data = (
                    f"I'm interested in the upcoming {vehicle.title}. "
                    "Please let me know when it's available."
                )
    if form.validate_on_submit():
        if rate_limit_exceeded(
            f"contact:{client_ip()}",
            current_app.config.get('CONTACT_RATE_LIMIT', 8),
            current_app.config.get('CONTACT_RATE_WINDOW', 600),
        ):
            flash('Too many messages submitted. Please try again later or call us.', 'warning')
            return redirect(url_for('main.contact'))
        if form.honeypot.data:
            # Bot trap — pretend success
            flash('Thank you! We have received your message and will contact you soon.', 'success')
            return redirect(url_for('main.contact'))
        if _create_lead_from_form(form, source='contact') is None:
            flash('Sorry, we could not save your message. Please call us instead.', 'danger')
            return redirect(url_for('main.contact'))
        flash('Thank you! We have received your message and will contact you soon.', 'success')
        return redirect(url_for('main.contact'))

    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Contact Us", url_for('main.contact', _external=True))
    ])
    return render_template(
        'contact.html',
        form=form,
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        meta_title=f"Contact {current_app.config['BUSINESS_NAME']} | Used Car Dealer in {current_app.config['BUSINESS_CITY']}",
        meta_description=(
            f"Contact {current_app.config['BUSINESS_NAME']} today. Call {current_app.config['BUSINESS_PHONE']} "
            f"or visit us in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']} "
            f"to schedule a test drive or appraisal."
        ),
        page_type='contact',
    )


@main.route('/contact-submit', methods=['POST'])
def contact_submit_ajax():
    form = ContactForm()
    if rate_limit_exceeded(
        f"contact:{client_ip()}",
        current_app.config.get('CONTACT_RATE_LIMIT', 8),
        current_app.config.get('CONTACT_RATE_WINDOW', 600),
    ):
        return jsonify({'success': False, 'errors': {'_form': ['Too many requests. Please try again later.']}}), 429

    if form.validate_on_submit():
        if form.honeypot.data:
            return jsonify({'success': True, 'message': 'Thank you! We will be in touch soon.'})
        if request.form.get('chat_mode') == 'live':
            from app.chat import start_conversation
            return start_conversation(form)
        source = (request.form.get('source') or 'ajax')[:64]
        lead = _create_lead_from_form(form, source=source)
        if lead is None:
            return jsonify({
                'success': False,
                'errors': {'_form': ['Sorry, we could not save your message. Please call us instead.']},
            }), 500
        return jsonify({
            'success': True,
            'message': 'Thank you! We will be in touch soon.',
            'lead_id': lead.id,
        })
    return jsonify({'success': False, 'errors': form.errors}), 400


@main.route('/api/analytics/collect', methods=['POST'])
def analytics_collect():
    """First-party pageview / event beacon used by the public site JS."""
    from app.analytics import process_collect_payload

    # This endpoint is CSRF-exempt and public; MAX_CONTENT_LENGTH is sized for
    # bulk photo uploads, so cap the beacon body separately.
    max_beacon = current_app.config.get('ANALYTICS_MAX_BODY_BYTES', 64 * 1024)
    if (request.content_length or 0) > max_beacon:
        return jsonify({'ok': False, 'error': 'payload_too_large'}), 413

    data = request.get_json(silent=True)
    if data is None and request.form:
        # sendBeacon may post as text/plain JSON body; also accept form field
        raw = request.form.get('payload') or request.form.get('data')
        if raw:
            import json as _json
            try:
                data = _json.loads(raw)
            except (TypeError, ValueError):
                data = None
    if data is None and request.data:
        import json as _json
        try:
            data = _json.loads(request.data.decode('utf-8', errors='ignore') or '{}')
        except (TypeError, ValueError):
            data = None

    body, status = process_collect_payload(data or {})
    return jsonify(body), status


@main.route('/about')
def about():
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("About Us", url_for('main.about', _external=True))
    ])
    return render_template(
        'about.html',
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        meta_title=f"About {current_app.config['BUSINESS_NAME']} | Used Car Dealer in {current_app.config['BUSINESS_CITY']}",
        meta_description=(
            f"Learn about {current_app.config['BUSINESS_NAME']}, a trusted used car dealership in "
            f"{current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']} offering quality "
            f"pre-owned vehicles, financing through Lendmark Financial Services, and transparent pricing."
        ),
        page_type='about',
    )


@main.route('/financing')
def financing():
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Auto Financing", url_for('main.financing', _external=True))
    ])
    faqs = [
        (f"Can I get financing at {current_app.config['BUSINESS_NAME']} with bad credit?",
         "Marshall Auto accepts online applications for vehicles in our inventory and works with Lendmark Financial Services. The lender reviews each application under its underwriting criteria; approval is not guaranteed, and we cannot promise a particular APR, down payment, term, or payment."),
        ("What do I need to bring to apply for financing?",
         "Select an available vehicle and complete the secure application on this page. You will need your Social Security number, contact and residence history, employment and income details, and driver's-license information. Review the lender's credit-report authorization before submitting."),
        ("Can I start a financing application before choosing a vehicle?",
         "Choose an available vehicle from our inventory as part of the application. Lendmark Financial Services reviews the completed application; an application is not a final approval or commitment to lend.")
    ]
    steps = [
        {"name": "Choose a Vehicle", "text": "Select an available vehicle from our inventory and enter the requested borrower, residence, employment, and income details in the secure online form."},
        {"name": "Review and Submit Securely", "text": "Review the lender's application and credit-report authorization before submitting. Your completed application is encrypted for authorized dealership staff to download and send to the lender."},
        {"name": "Review the Vehicle and Written Terms", "text": "Confirm that the lender will consider the specific vehicle and review the APR, amount financed, finance charge, payment schedule, fees, and total of payments in the written disclosures."},
        {"name": "Proceed Only If the Terms Work for You", "text": "Approval and final terms are subject to lender underwriting and the signed agreement. Arrange required insurance and review all purchase and credit documents before signing."}
    ]
    vehicles = _vehicle_list_query().order_by(
        Vehicle.year.desc(), Vehicle.make, Vehicle.model,
    ).all()
    return render_template(
        'financing.html',
        financing_vehicles=vehicles,
        selected_vehicle_id=request.args.get('vehicle_id', type=int),
        form_values={},
        form_errors={},
        application_available=online_application_available(),
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_faq=structured_data_faq(faqs),
        structured_how_to=structured_data_how_to(
            f"How to Finance a Used Car at {current_app.config['BUSINESS_NAME']}",
            steps,
            description=(
                f"An overview of applying online for financing through Lendmark Financial Services at "
                f"{current_app.config['BUSINESS_NAME']} in {current_app.config['BUSINESS_CITY']}, "
                f"{current_app.config['BUSINESS_STATE']}, for an available vehicle."
            ),
        ),
        meta_title=f"Used Car Financing in {current_app.config['BUSINESS_CITY']} | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"Yes, {current_app.config['BUSINESS_NAME']} in {current_app.config['BUSINESS_CITY']} offers financing through Lendmark Financial Services. "
            f"Apply securely online for an available vehicle. Approval and terms are determined by the lender."
        ),
        page_type='financing',
    )


@main.route('/api/zip/<zip_code>')
def zip_lookup(zip_code):
    """Suggest US city/state for a ZIP so financing applicants complete the full address."""
    from app.financing import lookup_us_zip
    if rate_limit_exceeded(f"zip-lookup:{client_ip()}", limit=30, window_seconds=60):
        return jsonify({'error': 'Too many lookups. Try again shortly.'}), 429
    result = lookup_us_zip(zip_code)
    if not result:
        return jsonify({'error': 'ZIP not found'}), 404
    response = jsonify(result)
    response.headers['Cache-Control'] = 'private, max-age=86400'
    response.headers['X-Robots-Tag'] = 'noindex'
    return response


@main.post('/financing/apply')
def financing_apply():
    if not online_application_available():
        abort(503)
    if rate_limit_exceeded(
        f'financing-application:{client_ip()}',
        current_app.config['FINANCING_RATE_LIMIT'],
        current_app.config['FINANCING_RATE_WINDOW'],
    ):
        return _render_financing_application(
            status=429,
            form_errors={'form': 'Too many attempts. Please wait before trying again.'},
        )

    vehicle_id = request.form.get('vehicle_id', type=int)
    vehicle = None
    if vehicle_id:
        vehicle = Vehicle.query.filter_by(
            id=vehicle_id, status='available', is_coming_soon=False,
        ).first()
    if not vehicle:
        return _render_financing_application(
            status=400,
            form_errors={'vehicle_id': 'Choose a vehicle that is currently available.'},
        )

    if request.form.get('website', '').strip():
        current_app.logger.warning(
            'Financing submission rejected by anti-bot honeypot.',
        )
        return _render_financing_application(
            status=400,
            form_errors={
                'form': 'We could not verify this submission. Please review the form or call us for help.',
            },
        )

    existing_application = FinancingApplication.query.filter_by(
        vehicle_id=vehicle.id,
    ).first()
    if existing_application:
        return _render_financing_application(
            status=409,
            form_errors={
                'form': 'An application has already been submitted for this vehicle. '
                'Please contact us if you need help.',
            },
        )

    application, errors, safe_values = validate_application(request.form, vehicle)
    if errors:
        return _render_financing_application(
            status=400, form_errors=errors, form_values=safe_values,
            selected_vehicle_id=vehicle.id,
        )

    if financing_spam_detected(application):
        current_app.logger.warning(
            'Financing submission rejected by local spam screening.',
        )
        return _render_financing_application(
            status=400,
            form_errors={
                'form': 'We could not verify this submission. Please review your contact details or call us for help.',
            },
            selected_vehicle_id=vehicle.id,
        )

    application['vehicle']['id'] = vehicle.id
    try:
        record = save_application(application)
    except IntegrityError:
        existing_application = FinancingApplication.query.filter_by(
            vehicle_id=vehicle.id,
        ).first()
        if not existing_application:
            raise
        return _render_financing_application(
            status=409,
            form_errors={
                'form': 'An application has already been submitted for this vehicle. '
                'Please contact us if you need help.',
            },
        )
    except FinancingEncryptionUnavailable:
        current_app.logger.error('Financing application encryption is unavailable.')
        abort(503)

    try:
        administrator_online = admin_is_online()
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            'Could not determine whether an administrator is online for financing application %s.',
            record.id,
        )
        administrator_online = False
    if not administrator_online:
        try:
            notify_salespeople_by_textbelt(vehicle)
        except Exception:
            current_app.logger.exception(
                'Financing application %s was saved, but salesperson notification dispatch failed.',
                record.id,
            )
    current_app.logger.info(
        'Encrypted financing application %s saved for vehicle %s.',
        record.id, vehicle.id,
    )
    return redirect(url_for('main.financing_received'))


@main.get('/financing/received')
def financing_received():
    if not online_application_available():
        abort(503)
    return render_template(
        'financing_received.html',
        meta_title=f"Application Received | {current_app.config['BUSINESS_NAME']}",
        meta_description='Your secure vehicle financing application has been received.',
        page_type='financing',
    )


def _render_financing_application(
    status=200, form_errors=None, form_values=None, selected_vehicle_id=None,
):
    return render_template(
        'financing.html',
        financing_vehicles=_vehicle_list_query().order_by(
            Vehicle.year.desc(), Vehicle.make, Vehicle.model,
        ).all(),
        selected_vehicle_id=selected_vehicle_id or request.form.get('vehicle_id', type=int),
        form_values=form_values or {},
        form_errors=form_errors or {},
        application_available=online_application_available(),
        breadcrumbs=None,
        structured_local=None,
        structured_website=None,
        structured_faq=None,
        structured_how_to=None,
        meta_title=f"Vehicle Financing | {current_app.config['BUSINESS_NAME']}",
        meta_description='Apply securely online for a vehicle in the Marshall Auto inventory.',
        page_type='financing',
    ), status


@main.route('/sell-your-car')
def sell_your_car():
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Sell Your Car", url_for('main.sell_your_car', _external=True))
    ])
    faqs = [
        ("How do I get an offer for my car?",
         "Fill out our online appraisal form or visit our dealership. We will evaluate your vehicle and provide a fair cash offer."),
        ("Do you buy cars that are not running?",
         "Yes, we purchase vehicles in any condition, including non-running cars, trucks, and SUVs."),
        ("What paperwork do I need to sell my car?",
         "You will need the vehicle title, a valid ID, and any lien payoff information if applicable. We handle the rest.")
    ]
    return render_template(
        'sell_your_car.html',
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_faq=structured_data_faq(faqs),
        meta_title=f"Sell Your Car in {current_app.config['BUSINESS_CITY']} | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"Sell your car to {current_app.config['BUSINESS_NAME']} in {current_app.config['BUSINESS_CITY']}, "
            f"{current_app.config['BUSINESS_STATE']}. Get a fair cash offer, fast payment, and hassle-free paperwork. Trade-ins welcome."
        ),
        page_type='sell_your_car',
    )


@main.route('/blog')
def blog_list():
    page = request.args.get('page', 1, type=int)
    pagination = (
        BlogPost.query
        .filter_by(is_published=True)
        .order_by(BlogPost.published_at.desc().nullslast(), BlogPost.created_at.desc())
        .paginate(page=page, per_page=9, error_out=False)
    )
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Blog", url_for('main.blog_list', _external=True))
    ])
    return render_template(
        'blog.html',
        pagination=pagination,
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        meta_title=f"Car Buying Guides & News | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"Car buying guides, financing tips, and news from {current_app.config['BUSINESS_NAME']} in "
            f"{current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}. Learn how to "
            f"shop for a used car and get the most for your trade-in."
        ),
        page_type='blog',
    )


@main.route('/blog/<slug>')
def blog_post_detail(slug):
    post = BlogPost.query.filter_by(slug=slug, is_published=True).first_or_404()
    post_description = _blog_post_description(post)
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Blog", url_for('main.blog_list', _external=True)),
        (post.title, url_for('main.blog_post_detail', slug=post.slug, _external=True))
    ])
    site = current_app.config['SITE_URL'].rstrip('/')
    page_url = f"{site}/blog/{post.slug}"
    publisher_logo = f"{site}/static/images/logo-icon.png"
    author_name = post.author_name or current_app.config['BUSINESS_NAME']
    plain_body = BeautifulSoup(post.content or '', 'html.parser').get_text(' ', strip=True)
    word_count = len(plain_body.split()) if plain_body else None
    cover = (post.cover_image_url or '').strip()
    if cover and cover.startswith('/'):
        cover = f"{site}{cover}"
    elif cover and not cover.startswith(('http://', 'https://')):
        cover = f"{site}/{cover.lstrip('/')}"
    structured_article = {
        "@context": "https://schema.org",
        "@type": "Article",
        "@id": f"{page_url}#article",
        "headline": post.title,
        "description": post_description,
        "inLanguage": "en-US",
        "isAccessibleForFree": True,
        "author": {
            "@type": "Person" if post.author_name else "Organization",
            "name": author_name,
        },
        "publisher": {
            "@type": "Organization",
            "@id": f"{site}/#dealership",
            "name": current_app.config['BUSINESS_NAME'],
            "logo": {
                "@type": "ImageObject",
                "url": publisher_logo,
                "width": 512,
                "height": 512,
            },
        },
        "datePublished": (post.published_at or post.created_at).isoformat() + 'Z',
        "dateModified": (post.updated_at or post.created_at).isoformat() + 'Z',
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": page_url,
            "url": page_url,
        },
        "isPartOf": {
            "@type": "WebSite",
            "@id": f"{site}/#website",
            "name": current_app.config['BUSINESS_NAME'],
            "url": site,
        },
        "articleSection": "Car Buying Guides",
        "about": [
            {"@type": "Thing", "name": "Used cars"},
            {"@type": "Thing", "name": f"Car buying in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}"},
        ],
    }
    if cover:
        structured_article['image'] = [cover]
    if word_count:
        structured_article['wordCount'] = word_count
    structured_article = {k: v for k, v in structured_article.items() if v not in (None, '', [])}

    return render_template(
        'blog_post.html',
        post=post,
        inventory_picks=_vehicle_list_query().order_by(Vehicle.created_at.desc()).limit(3).all(),
        related_posts=(
            BlogPost.query.filter(BlogPost.is_published.is_(True), BlogPost.id != post.id)
            .order_by(BlogPost.published_at.desc(), BlogPost.id.desc()).limit(3).all()
        ),
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_article=structured_article,
        meta_title=post.seo_title or f"{post.title} | {current_app.config['BUSINESS_NAME']}",
        meta_description=post_description,
        og_image=post.cover_image_url,
        page_type='blog',
    )


@main.route('/carfax/<int:report_id>/download')
def carfax_download(report_id):
    """
    Serve CarFax PDFs only for available vehicles (or authenticated admins).
    Avoids world-readable static PDF URLs.
    """
    from flask_login import current_user
    report = db.session.get(CarfaxReport, report_id)
    if not report or not report.filename:
        abort(404)
    vehicle = db.session.get(Vehicle, report.vehicle_id)
    if not vehicle:
        abort(404)
    if (
        (vehicle.status != 'available' or vehicle.is_coming_soon)
        and not getattr(current_user, 'is_authenticated', False)
    ):
        abort(404)
    # Basic path traversal guard
    filename = os.path.basename(report.filename)
    directory = os.path.join(current_app.config['UPLOAD_FOLDER'], 'carfax')
    response = send_from_directory(
        directory, filename, as_attachment=False, mimetype='application/pdf',
    )
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@main.route('/feeds/vehicles.xml')
def vehicle_feed():
    """Vehicle inventory feed (RSS 2.0 + Google 'g:' namespace fields)."""
    vehicles = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(status='available', is_coming_soon=False)
        .order_by(Vehicle.created_at.desc())
        .all()
    )
    response = make_response(render_template('vehicle_feed.xml', vehicles=vehicles))
    response.headers['Content-Type'] = 'application/rss+xml'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response


@main.route('/sitemap.xml')
def sitemap():
    vehicles = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .order_by(Vehicle.updated_at.desc())
        .all()
    )
    latest_vehicle_mod = None
    if vehicles:
        latest_vehicle_mod = max(
            (v.updated_at for v in vehicles if v.updated_at),
            default=None,
        )
    inventory_lastmod = latest_vehicle_mod.isoformat() + 'Z' if latest_vehicle_mod else None
    coming_soon_vehicles = [
        vehicle for vehicle in vehicles
        if vehicle.status == 'available' and vehicle.is_coming_soon
    ]
    available_vehicles = [
        vehicle for vehicle in vehicles
        if vehicle.status == 'available' and not vehicle.is_coming_soon
    ]
    archived_vehicles = [
        vehicle for vehicle in vehicles
        if vehicle.status != 'available' and not vehicle.is_coming_soon
    ]
    pages = [
        {'loc': url_for('main.index', _external=True), 'priority': '1.00', 'changefreq': 'daily'},
        {'loc': url_for('main.about', _external=True), 'priority': '0.70', 'changefreq': 'monthly'},
        {'loc': url_for('main.financing', _external=True), 'priority': '0.80', 'changefreq': 'monthly'},
        {'loc': url_for('main.sell_your_car', _external=True), 'priority': '0.80', 'changefreq': 'monthly'},
        {'loc': url_for('main.contact', _external=True), 'priority': '0.80', 'changefreq': 'monthly'},
        {'loc': url_for('main.service_area', _external=True), 'priority': '0.80', 'changefreq': 'weekly'},
        {'loc': url_for('main.blog_list', _external=True), 'priority': '0.60', 'changefreq': 'weekly'},
    ]
    if available_vehicles:
        pages.append({'loc': url_for('main.inventory', _external=True), 'priority': '0.90', 'changefreq': 'daily', 'lastmod': inventory_lastmod})
    if coming_soon_vehicles:
        pages.append({'loc': url_for('main.coming_soon', _external=True), 'priority': '0.70', 'changefreq': 'daily', 'lastmod': inventory_lastmod})
    if archived_vehicles:
        pages.append({'loc': url_for('main.vehicle_archive', _external=True), 'priority': '0.50', 'changefreq': 'weekly', 'lastmod': inventory_lastmod})

    for post in BlogPost.query.filter_by(is_published=True).all():
        pages.append({
            'loc': url_for('main.blog_post_detail', slug=post.slug, _external=True),
            'priority': '0.55',
            'changefreq': 'monthly',
            'lastmod': (post.updated_at or post.created_at).isoformat() + 'Z',
        })

    for landing in _available_model_landings():
        pages.append({
            'loc': url_for('main.inventory_by_model', slug=landing['slug'], _external=True),
            'priority': '0.70',
            'changefreq': 'daily',
            'lastmod': landing['lastmod'].isoformat() + 'Z' if landing['lastmod'] else None,
        })

    top_makes = _available_makes()[:8]
    body_styles = _available_body_styles()
    has_rebuilt = any(vehicle.title_status == 'rebuilt' for vehicle in available_vehicles)

    for city, state in current_app.config.get('SERVICE_AREAS', []) if available_vehicles else []:
        city_slug = city.lower().replace(' ', '-')
        state_slug = state.lower()
        pages.append({
            'loc': url_for('main.inventory_by_city', city=city_slug, state=state_slug, _external=True),
            'priority': '0.75',
            'changefreq': 'daily',
            'lastmod': inventory_lastmod,
        })
        for link in _inventory_landing_links(city, state, top_makes, body_styles, has_rebuilt):
            pages.append({
                'loc': link['url'],
                'priority': '0.65',
                'changefreq': 'weekly',
                'lastmod': inventory_lastmod,
            })

    for vehicle in vehicles:
        if vehicle.is_coming_soon and vehicle.status == 'available':
            continue
        images = vehicle.ordered_images()[:5]
        pages.append({
            'loc': url_for('main.vehicle_detail', slug=vehicle.slug, _external=True),
            'priority': '0.70',
            'changefreq': 'weekly',
            'lastmod': (vehicle.updated_at or vehicle.created_at).isoformat() + 'Z',
            'images': [{'loc': img.absolute_url, 'caption': vehicle.title} for img in images]
        })
        normalized_vin = normalize_vin(vehicle.vin)
        if is_valid_vin(normalized_vin):
            pages.append({
                'loc': url_for('main.vehicle_vin_record', vin=normalized_vin, _external=True),
                'priority': '0.60',
                'changefreq': 'monthly',
                'lastmod': (vehicle.updated_at or vehicle.created_at).isoformat() + 'Z',
            })
    for page in pages:
        parsed = urlsplit(page['loc'])
        page['loc'] = current_app.config['SITE_URL'].rstrip('/') + parsed.path
    response = make_response(render_template('sitemap.xml', pages=pages))
    response.headers['Content-Type'] = 'application/xml'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response


@main.route('/robots.txt')
def robots():
    sitemap_url = current_app.config['SITE_URL'].rstrip('/') + url_for('main.sitemap')
    content = f"""User-agent: *
Disallow: /admin/
Disallow: /admin/*
Disallow: /static/uploads/carfax/
Disallow: /carfax/
Disallow: /healthz
Disallow: /api/
Disallow: /saved-vehicles
Disallow: /compare
Allow: /
Allow: /inventory/
Allow: /vin/
Allow: /inventory/used-cars-for-sale-in-*
Allow: /inventory/used-*-for-sale-in-*
Allow: /inventory/rebuilt-title-cars-for-sale-in-*
Allow: /service-area
Allow: /about
Allow: /financing
Allow: /sell-your-car
Allow: /contact
Allow: /blog
Allow: /sitemap.xml
Allow: /*.txt
Allow: /indexnow/

Sitemap: {sitemap_url}
"""
    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


def _indexnow_key_response(key: str):
    """Serve the IndexNow ownership key body, or 404 when it does not match."""
    expected = (current_app.config.get('INDEXNOW_KEY') or '').strip()
    if not expected or key != expected:
        abort(404)
    response = make_response(expected)
    response.headers['Content-Type'] = 'text/plain; charset=utf-8'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    response.headers['X-Robots-Tag'] = 'noindex'
    return response


@main.route('/<key>.txt')
def indexnow_key_file_root(key):
    """Root IndexNow key file (covers all site URLs per IndexNow ownership rules)."""
    return _indexnow_key_response(key)


@main.route('/indexnow/<key>.txt')
def indexnow_key_file(key):
    """Legacy key path kept for older docs/bookmarks; submissions use the root file."""
    return _indexnow_key_response(key)


def page_not_found(e):
    response = make_response(render_template(
        'errors/404.html',
        meta_title='Page Not Found | Marshall Auto LLC',
        meta_description='The page you are looking for could not be found.',
        error_page=True,
    ), 404)
    response.headers['X-Robots-Tag'] = 'noindex, follow'
    return response


def internal_server_error(e):
    response = make_response(render_template(
        'errors/500.html',
        meta_title='Server Error | Marshall Auto LLC',
        meta_description='Something went wrong. Please try again later.',
        error_page=True,
    ), 500)
    response.headers['X-Robots-Tag'] = 'noindex, follow'
    return response
