import os
from datetime import datetime, timezone

from flask import (
    Blueprint, current_app, flash, jsonify, make_response, redirect,
    render_template, request, send_from_directory, url_for, abort
)
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app import db
from app.forms import ContactForm
from app.models import CarfaxReport, Lead, Review, SiteSetting, Vehicle, VehicleImage
from app.utils import (
    aggregate_rating_data, client_ip, format_mileage, format_price,
    notify_new_lead, parse_optional_int, rate_limit_exceeded, sanitize_gsc_tag,
    structured_data_breadcrumb, structured_data_faq, structured_data_how_to,
    structured_data_local_business, structured_data_vehicle, structured_data_website,
)

main = Blueprint('main', __name__)


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _canonical_url():
    """Build a canonical URL without query tracking params."""
    args = request.view_args or {}
    return url_for(request.endpoint, **args, _external=True)


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
        .filter_by(status='available')
        .distinct()
        .order_by(Vehicle.make)
        .all()
    ]


def _available_body_styles():
    return [
        b[0] for b in db.session.query(Vehicle.body_style)
        .filter_by(status='available')
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
        .filter_by(status='available')
    )


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
                      landing_breadcrumb=None, landing_canonical=None):
    """Shared inventory rendering helper."""
    query = _build_inventory_query(
        make, model, body_style, title_status,
        min_price, max_price, max_mileage, search, sort=sort,
    )
    pagination = query.paginate(page=page, per_page=12, error_out=False)

    makes = _available_makes()
    body_styles = _available_body_styles()

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
            f"in {city}. Financing, CarFax reports, trade-ins, and huge discounts on rebuilt title vehicles available."
        )

    if landing_breadcrumb:
        breadcrumbs = landing_breadcrumb
    else:
        breadcrumbs = structured_data_breadcrumb([
            ("Home", current_app.config['SITE_URL']),
            ("Used Cars for Sale", url_for('main.inventory', _external=True))
        ])

    # Filtered inventory without a dedicated landing canonicalizes to /inventory
    has_filters = any([make, model, body_style, title_status, min_price, max_price, max_mileage, search])
    canonical_override = landing_canonical
    if not landing_canonical and has_filters and request.endpoint == 'main.inventory':
        canonical_override = url_for('main.inventory', _external=True)

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
        breadcrumbs=breadcrumbs,
        meta_title=meta_title,
        meta_description=meta_description,
        landing_h1=landing_h1,
        landing_intro=landing_intro,
        landing_canonical=landing_canonical,
        canonical_override=canonical_override,
    )


@main.context_processor
def inject_globals():
    gsc_raw = SiteSetting.get('google_search_console', '') or ''
    gsc = sanitize_gsc_tag(gsc_raw)
    analytics_id = SiteSetting.get('google_analytics_id', '') or current_app.config['GOOGLE_ANALYTICS_ID']
    facebook_app_id = SiteSetting.get('facebook_app_id', '') or current_app.config['FACEBOOK_APP_ID']
    twitter_handle = SiteSetting.get('twitter_handle', '') or current_app.config['TWITTER_HANDLE']

    social = []
    for key, conf in [
        ('facebook_url', 'FACEBOOK_URL'),
        ('instagram_url', 'INSTAGRAM_URL'),
        ('youtube_url', 'YOUTUBE_URL'),
    ]:
        val = SiteSetting.get(key, '') or current_app.config.get(conf, '')
        if val:
            social.append((key.replace('_url', ''), val))

    return {
        'business_name': current_app.config['BUSINESS_NAME'],
        'business_phone': current_app.config['BUSINESS_PHONE'],
        'business_email': current_app.config['BUSINESS_EMAIL'],
        'business_address': current_app.config['BUSINESS_ADDRESS'],
        'business_city': current_app.config['BUSINESS_CITY'],
        'business_state': current_app.config['BUSINESS_STATE'],
        'business_zip': current_app.config['BUSINESS_ZIP'],
        'business_hours': current_app.config['BUSINESS_HOURS'],
        'business_latitude': SiteSetting.get('business_latitude') or current_app.config['BUSINESS_LATITUDE'],
        'business_longitude': SiteSetting.get('business_longitude') or current_app.config['BUSINESS_LONGITUDE'],
        'site_url': current_app.config['SITE_URL'],
        'site_title': SiteSetting.get('site_title', current_app.config['BUSINESS_NAME']),
        'site_tagline': SiteSetting.get('site_tagline', ''),
        'site_setting': SiteSetting,
        'google_tag_id': current_app.config['GOOGLE_TAG_ID'],
        'google_analytics_id': analytics_id,
        'google_search_console_tag': gsc,
        'facebook_pixel_id': current_app.config['FACEBOOK_PIXEL_ID'],
        'facebook_app_id': facebook_app_id,
        'twitter_handle': twitter_handle,
        'social_links': social,
        'now': _utcnow(),
        'canonical_url': _canonical_url() if request.endpoint else current_app.config['SITE_URL'],
    }


@main.route('/')
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
        .filter_by(status='available')
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
         f"Yes, {current_app.config['BUSINESS_NAME']} works with multiple lenders to offer competitive financing options for all credit situations."),
        ("Does every vehicle come with a CarFax report?",
         "We provide clean title guarantees and CarFax history reports are available on qualifying vehicles."),
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

    approved_reviews = (
        Review.query
        .filter_by(is_approved=True)
        .filter(Review.vehicle_id.is_(None))
        .order_by(Review.is_featured.desc(), Review.created_at.desc())
        .limit(6)
        .all()
    )
    aggregate = aggregate_rating_data()

    return render_template(
        'index.html',
        featured=featured,
        makes=makes,
        models=models,
        approved_reviews=approved_reviews,
        aggregate=aggregate,
        structured_local=structured_local,
        structured_website=structured_website,
        structured_faq=structured_faq,
        breadcrumbs=breadcrumbs,
        meta_title=SiteSetting.get('site_title'),
        meta_description=SiteSetting.get('meta_description'),
    )


@main.route('/inventory')
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
    canonical = url_for('main.inventory_by_city', city=city, state=state, _external=True)
    h1 = f"Used Cars, Trucks & SUVs for Sale in {city_name}, {state_name}"
    intro = (
        f"Browse quality used vehicles for sale in {city_name}, {state_name}. "
        f"{current_app.config['BUSINESS_NAME']} serves the {city_name} area with financing, "
        f"trade-ins, CarFax reports, and huge discounts on rebuilt title cars."
    )
    meta_title = f"Used Cars for Sale in {city_name}, {state_name} | {current_app.config['BUSINESS_NAME']}"
    meta_description = (
        f"Shop used cars, trucks, and SUVs for sale in {city_name}, {state_name}. "
        f"Financing available. Visit {current_app.config['BUSINESS_NAME']} in {current_app.config['BUSINESS_CITY']} or browse online."
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
    )


@main.route('/inventory/used-<make>-for-sale-in-<city>-<state>')
def inventory_by_make_city(make, city, state):
    """Local SEO landing page: used [make] in a specific city."""
    slug = f"{city.lower()}-{state.lower()}"
    area = _service_area_map().get(slug)
    make_name = make.replace('-', ' ').title()
    if not area:
        return redirect(url_for('main.inventory', make=make_name))

    page = request.args.get('page', 1, type=int)
    sort = request.args.get('sort', 'newest')
    city_name, state_name = area
    canonical = url_for(
        'main.inventory_by_make_city',
        make=make.lower().replace(' ', '-'),
        city=city, state=state, _external=True,
    )
    h1 = f"Used {make_name} for Sale in {city_name}, {state_name}"
    intro = (
        f"Find a used {make_name} for sale in {city_name}, {state_name}. "
        f"{current_app.config['BUSINESS_NAME']} offers inspected {make_name} vehicles with financing and trade-in options."
    )
    meta_title = f"Used {make_name} for Sale in {city_name}, {state_name} | {current_app.config['BUSINESS_NAME']}"
    meta_description = (
        f"Shop used {make_name} cars, trucks, and SUVs for sale in {city_name}, {state_name}. "
        f"Huge discounts on rebuilt title {make_name} vehicles. Apply for financing online."
    )
    breadcrumb = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Used Cars for Sale", url_for('main.inventory', _external=True)),
        (f"Used {make_name} in {city_name}, {state_name}", canonical)
    ])
    return _render_inventory(
        page=page, make=make_name, sort=sort,
        landing_h1=h1, landing_intro=intro,
        landing_meta_title=meta_title, landing_meta_description=meta_description,
        landing_breadcrumb=breadcrumb, landing_canonical=canonical,
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
    canonical = url_for('main.inventory_rebuilt_by_city', city=city, state=state, _external=True)
    h1 = f"Rebuilt Title Cars for Sale in {city_name}, {state_name}"
    intro = (
        f"Save big on rebuilt title cars, trucks, and SUVs for sale in {city_name}, {state_name}. "
        f"Every rebuilt title vehicle at {current_app.config['BUSINESS_NAME']} is fully inspected and ready for the road."
    )
    meta_title = f"Rebuilt Title Cars for Sale in {city_name}, {state_name} | {current_app.config['BUSINESS_NAME']}"
    meta_description = (
        f"Huge discounts on rebuilt title cars for sale in {city_name}, {state_name}. "
        f"Browse inspected rebuilt title vehicles with financing available at {current_app.config['BUSINESS_NAME']}."
    )
    breadcrumb = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Used Cars for Sale", url_for('main.inventory', _external=True)),
        (f"Rebuilt Title Cars in {city_name}, {state_name}", canonical)
    ])
    return _render_inventory(
        page=page, title_status='rebuilt', sort=sort,
        landing_h1=h1, landing_intro=intro,
        landing_meta_title=meta_title, landing_meta_description=meta_description,
        landing_breadcrumb=breadcrumb, landing_canonical=canonical,
    )


@main.route('/service-area')
def service_area():
    """Service area hub page for local SEO."""
    areas = current_app.config.get('SERVICE_AREAS', [])
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Service Area", url_for('main.service_area', _external=True))
    ])
    faqs = [
        (f"Does {current_app.config['BUSINESS_NAME']} deliver vehicles?",
         f"Yes, we regularly serve customers throughout central North Carolina and can arrange delivery or meeting options for many areas. Call {current_app.config['BUSINESS_PHONE']} for details."),
        ("Do you offer financing for out-of-town buyers?",
         "Yes, our lenders work with buyers across North Carolina. You can apply online and complete most of the process remotely."),
        ("Can I buy a rebuilt title car in another city?",
         "Absolutely. Browse our rebuilt title inventory online and contact us to arrange a test drive or delivery near you.")
    ]
    return render_template(
        'service_area.html',
        areas=areas,
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_faq=structured_data_faq(faqs),
        meta_title=f"Used Car Dealer Service Area | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"{current_app.config['BUSINESS_NAME']} serves Sanford, Chapel Hill, Durham, Raleigh, Cary, Apex, "
            f"Fayetteville, and surrounding NC communities with quality used cars and financing."
        ),
    )


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
    robots = 'index, follow'
    if vehicle.status != 'available':
        flash('This vehicle is no longer available.', 'info')
        robots = 'noindex, follow'

    related = (
        _vehicle_list_query()
        .filter(
            Vehicle.id != vehicle.id,
            or_(Vehicle.make == vehicle.make, Vehicle.body_style == vehicle.body_style)
        )
        .order_by(Vehicle.created_at.desc())
        .limit(4)
        .all()
    )

    form = ContactForm(vehicle_id=vehicle.id)
    structured_vehicle = structured_data_vehicle(vehicle)

    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Inventory", url_for('main.inventory', _external=True)),
        (vehicle.title, url_for('main.vehicle_detail', slug=vehicle.slug, _external=True))
    ])

    location_phrase = f" in {current_app.config['BUSINESS_CITY']}, {current_app.config['BUSINESS_STATE']}"
    meta_title = vehicle.seo_title or f"{vehicle.title} for Sale{location_phrase} | {current_app.config['BUSINESS_NAME']}"

    title_phrase = (
        f"{vehicle.title_status.replace('_', ' ').title()} title" if vehicle.title_status
        else "clean title"
    )
    meta_description = vehicle.seo_description or (
        f"Shop this {vehicle.title} with {vehicle.mileage:,} miles at {current_app.config['BUSINESS_NAME']}. "
        f"{vehicle.condition.title()} condition, {title_phrase}, financing available."
    )
    meta_keywords = vehicle.meta_keywords or ', '.join(filter(None, [
        str(vehicle.year), vehicle.make, vehicle.model, vehicle.trim,
        vehicle.body_style, vehicle.exterior_color, current_app.config['BUSINESS_CITY'],
        current_app.config['BUSINESS_STATE'], 'used car for sale', title_phrase
    ]))

    og_image = vehicle.primary_image_url(absolute=True)

    return render_template(
        'vehicle_detail.html',
        vehicle=vehicle,
        related=related,
        form=form,
        structured_vehicle=structured_vehicle,
        breadcrumbs=breadcrumbs,
        meta_title=meta_title,
        meta_description=meta_description,
        meta_keywords=meta_keywords,
        og_image_absolute=og_image,
        robots_content=robots,
        page_links={},
        format_price=format_price,
        format_mileage=format_mileage,
    )


def _create_lead_from_form(form, source='contact'):
    vehicle_id = parse_optional_int(form.vehicle_id.data)
    if vehicle_id is not None:
        exists = db.session.get(Vehicle, vehicle_id)
        if not exists:
            vehicle_id = None
    lead = Lead(
        name=(form.name.data or '').strip()[:128],
        email=(form.email.data or '').strip()[:128],
        phone=(form.phone.data or '').strip()[:32] or None,
        message=(form.message.data or '').strip() or None,
        vehicle_id=vehicle_id,
        source=(source or 'contact')[:64],
    )
    db.session.add(lead)
    db.session.commit()
    notify_new_lead(lead)
    return lead


@main.route('/contact', methods=['GET', 'POST'])
def contact():
    form = ContactForm()
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
        _create_lead_from_form(form, source='contact')
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
        source = (request.form.get('source') or 'ajax')[:64]
        _create_lead_from_form(form, source=source)
        return jsonify({'success': True, 'message': 'Thank you! We will be in touch soon.'})
    return jsonify({'success': False, 'errors': form.errors}), 400


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
            f"pre-owned vehicles, financing, and transparent pricing."
        ),
    )


@main.route('/financing')
def financing():
    breadcrumbs = structured_data_breadcrumb([
        ("Home", current_app.config['SITE_URL']),
        ("Auto Financing", url_for('main.financing', _external=True))
    ])
    faqs = [
        (f"Can I get financing at {current_app.config['BUSINESS_NAME']} with bad credit?",
         "Yes. We work with a network of lenders that offer programs for good credit, bad credit, no credit, and first-time buyers."),
        ("What do I need to bring to apply for financing?",
         "You will need a valid driver's license, proof of income, proof of residence, and insurance information."),
        ("Can I get pre-approved before choosing a vehicle?",
         "Absolutely. You can contact us to start the pre-approval process, then shop our inventory with confidence.")
    ]
    steps = [
        {"name": "Apply Online or In-Person", "text": "Fill out our secure credit application with your basic information and employment details."},
        {"name": "Get Approved", "text": "Our finance team submits your application to multiple lenders to find the best rate and terms."},
        {"name": "Choose Your Vehicle", "text": "Browse our inventory and select the car, truck, or SUV that fits your budget and lifestyle."},
        {"name": "Drive Away", "text": "Sign the paperwork, arrange insurance, and take delivery of your next vehicle."}
    ]
    return render_template(
        'financing.html',
        breadcrumbs=breadcrumbs,
        structured_local=structured_data_local_business(),
        structured_website=structured_data_website(),
        structured_faq=structured_data_faq(faqs),
        structured_how_to=structured_data_how_to(
            f"How to Finance a Used Car at {current_app.config['BUSINESS_NAME']}",
            steps,
            description=(
                f"A step-by-step guide to getting approved for used car financing at "
                f"{current_app.config['BUSINESS_NAME']} in {current_app.config['BUSINESS_CITY']}, "
                f"{current_app.config['BUSINESS_STATE']}."
            ),
            total_time="P1D"
        ),
        meta_title=f"Used Car Financing in {current_app.config['BUSINESS_CITY']} | {current_app.config['BUSINESS_NAME']}",
        meta_description=(
            f"Get approved for used car financing at {current_app.config['BUSINESS_NAME']} in "
            f"{current_app.config['BUSINESS_CITY']}. Bad credit, no credit, and first-time buyer programs available. Apply today."
        ),
    )


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
    if vehicle.status != 'available' and not getattr(current_user, 'is_authenticated', False):
        abort(404)
    # Basic path traversal guard
    filename = os.path.basename(report.filename)
    directory = os.path.join(current_app.config['UPLOAD_FOLDER'], 'carfax')
    return send_from_directory(directory, filename, as_attachment=False, mimetype='application/pdf')




@main.route('/sitemap.xml')
def sitemap():
    vehicles = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(status='available')
        .order_by(Vehicle.updated_at.desc())
        .all()
    )
    today = _utcnow().strftime('%Y-%m-%d')
    pages = [
        {'loc': url_for('main.index', _external=True), 'priority': '1.00', 'lastmod': today},
        {'loc': url_for('main.inventory', _external=True), 'priority': '0.90', 'lastmod': today},
        {'loc': url_for('main.about', _external=True), 'priority': '0.80', 'lastmod': today},
        {'loc': url_for('main.financing', _external=True), 'priority': '0.80', 'lastmod': today},
        {'loc': url_for('main.sell_your_car', _external=True), 'priority': '0.80', 'lastmod': today},
        {'loc': url_for('main.contact', _external=True), 'priority': '0.80', 'lastmod': today},
        {'loc': url_for('main.service_area', _external=True), 'priority': '0.80', 'lastmod': today},
    ]

    top_makes = _available_makes()[:10]
    for city, state in current_app.config.get('SERVICE_AREAS', []):
        city_slug = city.lower().replace(' ', '-')
        state_slug = state.lower()
        pages.append({
            'loc': url_for('main.inventory_by_city', city=city_slug, state=state_slug, _external=True),
            'priority': '0.75',
            'lastmod': today
        })
        pages.append({
            'loc': url_for('main.inventory_rebuilt_by_city', city=city_slug, state=state_slug, _external=True),
            'priority': '0.70',
            'lastmod': today
        })
        for make in top_makes:
            pages.append({
                'loc': url_for(
                    'main.inventory_by_make_city',
                    make=make.lower().replace(' ', '-'),
                    city=city_slug, state=state_slug, _external=True,
                ),
                'priority': '0.65',
                'lastmod': today
            })

    for vehicle in vehicles:
        images = vehicle.ordered_images()[:5]
        pages.append({
            'loc': url_for('main.vehicle_detail', slug=vehicle.slug, _external=True),
            'priority': '0.70',
            'lastmod': vehicle.updated_at.strftime('%Y-%m-%d') if vehicle.updated_at else None,
            'images': [{'loc': img.absolute_url, 'caption': vehicle.title} for img in images]
        })
    response = make_response(render_template('sitemap.xml', pages=pages))
    response.headers['Content-Type'] = 'application/xml'
    response.headers['Cache-Control'] = 'public, max-age=3600'
    return response


@main.route('/robots.txt')
def robots():
    sitemap_url = url_for('main.sitemap', _external=True)
    content = f"""User-agent: *
Disallow: /admin/
Disallow: /admin/*
Disallow: /static/uploads/carfax/
Disallow: /carfax/
Disallow: /healthz
Allow: /
Allow: /inventory/
Allow: /inventory/used-cars-for-sale-in-*
Allow: /inventory/used-*-for-sale-in-*
Allow: /inventory/rebuilt-title-cars-for-sale-in-*
Allow: /service-area
Allow: /about
Allow: /financing
Allow: /sell-your-car
Allow: /contact
Allow: /sitemap.xml

Sitemap: {sitemap_url}
"""
    response = make_response(content)
    response.headers['Content-Type'] = 'text/plain'
    response.headers['Cache-Control'] = 'public, max-age=86400'
    return response


def page_not_found(e):
    response = make_response(render_template(
        'errors/404.html',
        meta_title='Page Not Found | Marshall Auto LLC',
        meta_description='The page you are looking for could not be found.',
    ), 404)
    response.headers['X-Robots-Tag'] = 'noindex, follow'
    return response


def internal_server_error(e):
    response = make_response(render_template(
        'errors/500.html',
        meta_title='Server Error | Marshall Auto LLC',
        meta_description='Something went wrong. Please try again later.',
    ), 500)
    response.headers['X-Robots-Tag'] = 'noindex, follow'
    return response
