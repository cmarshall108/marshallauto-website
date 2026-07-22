import os
from functools import wraps

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template,
    request, send_from_directory, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    CarfaxReportForm, LoginForm, ReviewForm, ServiceRecordForm,
    SiteSettingForm, VehicleForm,
)
from app.models import (
    CarfaxReport, Lead, Review, ServiceRecord, SiteSetting, User,
    Vehicle, VehicleImage,
)
from app.utils import (
    client_ip, is_safe_redirect, rate_limit_exceeded,
    save_uploaded_image, save_uploaded_pdf,
)

admin_bp = Blueprint('admin', __name__, template_folder='templates/admin')


def admin_required(f):
    """Decorator requiring an authenticated active admin user."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_active:
            return redirect(url_for('admin.login', next=request.path))
        return f(*args, **kwargs)
    return decorated_function


def _vehicle_choices():
    return [
        (v.id, v.title)
        for v in Vehicle.query.order_by(Vehicle.year.desc(), Vehicle.make).all()
    ]


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('admin.dashboard'))

    form = LoginForm()
    if form.validate_on_submit():
        if rate_limit_exceeded(
            f"login:{client_ip()}",
            current_app.config.get('LOGIN_RATE_LIMIT', 10),
            current_app.config.get('LOGIN_RATE_WINDOW', 300),
        ):
            flash('Too many login attempts. Please try again later.', 'danger')
            return render_template('admin/login.html', form=form), 429

        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user and user.is_active and user.check_password(form.password.data):
            login_user(user, remember=bool(form.remember.data))
            next_page = request.args.get('next')
            if next_page and is_safe_redirect(next_page):
                return redirect(next_page)
            return redirect(url_for('admin.dashboard'))
        flash('Invalid username or password.', 'danger')
    return render_template('admin/login.html', form=form)


@admin_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
@admin_bp.route('/dashboard')
@login_required
def dashboard():
    stats = {
        'total_vehicles': Vehicle.query.count(),
        'available_vehicles': Vehicle.query.filter_by(status='available').count(),
        'sold_vehicles': Vehicle.query.filter_by(status='sold').count(),
        'pending_leads': Lead.query.filter_by(is_read=False).count(),
        'total_leads': Lead.query.count(),
        'service_records': ServiceRecord.query.count(),
        'carfax_reports': CarfaxReport.query.count(),
    }
    recent_leads = Lead.query.order_by(Lead.created_at.desc()).limit(10).all()
    avg_rating = (
        Review.query.filter_by(is_approved=True)
        .with_entities(func.avg(Review.rating))
        .scalar()
    )
    review_count = Review.query.filter_by(is_approved=True).count()
    stats['avg_rating'] = round(avg_rating, 2) if avg_rating else None
    stats['review_count'] = review_count
    return render_template('admin/dashboard.html', stats=stats, recent_leads=recent_leads)


# ------------------------------ REVIEWS ------------------------------

@admin_bp.route('/reviews')
@login_required
def reviews():
    page = request.args.get('page', 1, type=int)
    pagination = Review.query.order_by(Review.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False)
    return render_template('admin/reviews.html', pagination=pagination)


@admin_bp.route('/reviews/new', methods=['GET', 'POST'])
@login_required
def review_new():
    form = ReviewForm()
    if form.validate_on_submit():
        review = Review(
            author_name=form.author_name.data.strip(),
            rating=form.rating.data,
            title=(form.title.data or '').strip() or None,
            content=form.body.data.strip(),
            source=(form.source.data or '').strip() or None,
            is_approved=bool(form.is_approved.data),
            is_featured=bool(form.is_featured.data),
        )
        db.session.add(review)
        db.session.commit()
        flash('Review added.', 'success')
        return redirect(url_for('admin.reviews'))
    return render_template('admin/review_form.html', form=form, title='Add Review')


@admin_bp.route('/reviews/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def review_edit(id):
    review = db.session.get(Review, id) or abort(404)
    form = ReviewForm(obj=review)
    if request.method == 'GET':
        form.body.data = review.content
        form.is_featured.data = review.is_featured
    if form.validate_on_submit():
        review.author_name = form.author_name.data.strip()
        review.rating = form.rating.data
        review.title = (form.title.data or '').strip() or None
        review.content = form.body.data.strip()
        review.source = (form.source.data or '').strip() or None
        review.is_approved = bool(form.is_approved.data)
        review.is_featured = bool(form.is_featured.data)
        db.session.commit()
        flash('Review updated.', 'success')
        return redirect(url_for('admin.reviews'))
    return render_template('admin/review_form.html', form=form, review=review, title='Edit Review')


@admin_bp.route('/reviews/<int:id>/toggle-approval', methods=['POST'])
@login_required
def review_toggle_approval(id):
    review = db.session.get(Review, id) or abort(404)
    review.is_approved = not review.is_approved
    db.session.commit()
    flash('Review approval toggled.', 'success')
    return redirect(url_for('admin.reviews'))


@admin_bp.route('/reviews/<int:id>/delete', methods=['POST'])
@login_required
def review_delete(id):
    review = db.session.get(Review, id) or abort(404)
    db.session.delete(review)
    db.session.commit()
    flash('Review deleted.', 'success')
    return redirect(url_for('admin.reviews'))


# ------------------------------ VEHICLES ------------------------------

@admin_bp.route('/vehicles')
@login_required
def vehicles():
    page = request.args.get('page', 1, type=int)
    status = request.args.get('status', '')
    query = Vehicle.query.options(selectinload(Vehicle.images))
    if status:
        query = query.filter_by(status=status)
    pagination = query.order_by(Vehicle.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('admin/vehicles.html', pagination=pagination, status=status)


@admin_bp.route('/vehicles/new', methods=['GET', 'POST'])
@login_required
def vehicle_new():
    form = VehicleForm()
    if form.validate_on_submit():
        vehicle = _vehicle_from_form(form)
        db.session.add(vehicle)
        db.session.flush()
        vehicle.ensure_slug()
        db.session.commit()
        _handle_vehicle_images(vehicle, request.files.getlist('images'))
        flash('Vehicle added successfully.', 'success')
        return redirect(url_for('admin.vehicles'))
    return render_template('admin/vehicle_form.html', form=form, vehicle=None, title='Add Vehicle')


@admin_bp.route('/vehicles/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def vehicle_edit(id):
    vehicle = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(id=id)
        .first_or_404()
    )
    form = VehicleForm(obj=vehicle)
    if form.validate_on_submit():
        _apply_vehicle_form(vehicle, form)
        vehicle.ensure_slug()
        _handle_vehicle_images(vehicle, request.files.getlist('images'))
        db.session.commit()
        flash('Vehicle updated successfully.', 'success')
        return redirect(url_for('admin.vehicles'))
    return render_template('admin/vehicle_form.html', form=form, vehicle=vehicle, title='Edit Vehicle')


@admin_bp.route('/vehicles/<int:id>/delete', methods=['POST'])
@login_required
def vehicle_delete(id):
    vehicle = (
        Vehicle.query
        .options(
            selectinload(Vehicle.images),
            selectinload(Vehicle.carfax_reports),
        )
        .filter_by(id=id)
        .first_or_404()
    )
    for img in list(vehicle.images):
        _delete_vehicle_image_file(img)
    for report in list(vehicle.carfax_reports):
        if report.filename:
            _delete_carfax_file(report.filename)
    db.session.delete(vehicle)
    db.session.commit()
    flash('Vehicle deleted.', 'success')
    return redirect(url_for('admin.vehicles'))


@admin_bp.route('/vehicles/<int:id>/images/reorder', methods=['POST'])
@login_required
def vehicle_images_reorder(id):
    vehicle = (
        Vehicle.query
        .options(selectinload(Vehicle.images))
        .filter_by(id=id)
        .first_or_404()
    )
    ordered_ids = request.form.getlist('image_order[]', type=int)
    images = {img.id: img for img in vehicle.images}
    for idx, img_id in enumerate(ordered_ids):
        if img_id in images:
            images[img_id].order_index = idx
            images[img_id].is_primary = (idx == 0)
    db.session.commit()
    flash('Image order updated.', 'success')
    return redirect(url_for('admin.vehicle_edit', id=id))


@admin_bp.route('/vehicles/images/<int:id>/delete', methods=['POST'])
@login_required
def vehicle_image_delete(id):
    image = db.session.get(VehicleImage, id) or abort(404)
    vehicle_id = image.vehicle_id
    _delete_vehicle_image_file(image)
    db.session.delete(image)
    db.session.commit()
    flash('Image deleted.', 'success')
    return redirect(url_for('admin.vehicle_edit', id=vehicle_id))


# ------------------------------ SERVICE RECORDS ------------------------------

@admin_bp.route('/service-records')
@login_required
def service_records():
    page = request.args.get('page', 1, type=int)
    pagination = ServiceRecord.query.order_by(ServiceRecord.service_date.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('admin/service_records.html', pagination=pagination)


@admin_bp.route('/service-records/new', methods=['GET', 'POST'])
@login_required
def service_record_new():
    form = ServiceRecordForm()
    form.vehicle_id.choices = _vehicle_choices()
    if form.validate_on_submit():
        record = ServiceRecord(
            vehicle_id=form.vehicle_id.data,
            service_date=form.service_date.data,
            mileage_at_service=form.mileage_at_service.data,
            service_type=form.service_type.data.strip(),
            provider=(form.provider.data or '').strip() or None,
            description=form.description.data,
        )
        db.session.add(record)
        db.session.commit()
        flash('Service record added.', 'success')
        return redirect(url_for('admin.service_records'))
    return render_template('admin/service_record_form.html', form=form, title='Add Service Record')


@admin_bp.route('/service-records/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def service_record_edit(id):
    record = db.session.get(ServiceRecord, id) or abort(404)
    form = ServiceRecordForm(obj=record)
    form.vehicle_id.choices = _vehicle_choices()
    if form.validate_on_submit():
        form.populate_obj(record)
        record.service_type = (record.service_type or '').strip()
        record.provider = (record.provider or '').strip() or None
        db.session.commit()
        flash('Service record updated.', 'success')
        return redirect(url_for('admin.service_records'))
    return render_template('admin/service_record_form.html', form=form, title='Edit Service Record')


@admin_bp.route('/service-records/<int:id>/delete', methods=['POST'])
@login_required
def service_record_delete(id):
    record = db.session.get(ServiceRecord, id) or abort(404)
    db.session.delete(record)
    db.session.commit()
    flash('Service record deleted.', 'success')
    return redirect(url_for('admin.service_records'))


# ------------------------------ CARFAX REPORTS ------------------------------

@admin_bp.route('/carfax-reports')
@login_required
def carfax_reports():
    page = request.args.get('page', 1, type=int)
    pagination = CarfaxReport.query.order_by(CarfaxReport.created_at.desc()).paginate(
        page=page, per_page=20, error_out=False)
    return render_template('admin/carfax_reports.html', pagination=pagination)


@admin_bp.route('/carfax-reports/new', methods=['GET', 'POST'])
@login_required
def carfax_report_new():
    form = CarfaxReportForm()
    form.vehicle_id.choices = _vehicle_choices()
    if form.validate_on_submit():
        report = CarfaxReport(
            vehicle_id=form.vehicle_id.data,
            report_date=form.report_date.data,
            accidents_reported=form.accidents_reported.data,
            owners_reported=form.owners_reported.data,
            summary=form.summary.data,
            report_url=(form.report_url.data or '').strip() or None,
        )
        if form.report_file.data:
            report.filename = save_uploaded_pdf(form.report_file.data)
        db.session.add(report)
        db.session.commit()
        flash('CarFax report added.', 'success')
        return redirect(url_for('admin.carfax_reports'))
    return render_template('admin/carfax_report_form.html', form=form, title='Add CarFax Report')


@admin_bp.route('/carfax-reports/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def carfax_report_edit(id):
    report = db.session.get(CarfaxReport, id) or abort(404)
    form = CarfaxReportForm(obj=report)
    form.vehicle_id.choices = _vehicle_choices()
    if form.validate_on_submit():
        form.populate_obj(report)
        report.report_url = (report.report_url or '').strip() or None
        if form.report_file.data and getattr(form.report_file.data, 'filename', None):
            if report.filename:
                _delete_carfax_file(report.filename)
            report.filename = save_uploaded_pdf(form.report_file.data)
        db.session.commit()
        flash('CarFax report updated.', 'success')
        return redirect(url_for('admin.carfax_reports'))
    return render_template('admin/carfax_report_form.html', form=form, report=report, title='Edit CarFax Report')


@admin_bp.route('/carfax-reports/<int:id>/delete', methods=['POST'])
@login_required
def carfax_report_delete(id):
    report = db.session.get(CarfaxReport, id) or abort(404)
    if report.filename:
        _delete_carfax_file(report.filename)
    db.session.delete(report)
    db.session.commit()
    flash('CarFax report deleted.', 'success')
    return redirect(url_for('admin.carfax_reports'))


@admin_bp.route('/carfax-reports/<int:id>/download')
@login_required
def carfax_report_download(id):
    """Authenticated admin download for CarFax PDFs."""
    report = db.session.get(CarfaxReport, id) or abort(404)
    if not report.filename:
        abort(404)
    filename = os.path.basename(report.filename)
    directory = os.path.join(current_app.config['UPLOAD_FOLDER'], 'carfax')
    return send_from_directory(directory, filename, as_attachment=True, mimetype='application/pdf')


# ------------------------------ LEADS ------------------------------

@admin_bp.route('/leads')
@login_required
def leads():
    page = request.args.get('page', 1, type=int)
    pagination = (
        Lead.query
        .options(selectinload(Lead.vehicle))
        .order_by(Lead.created_at.desc())
        .paginate(page=page, per_page=25, error_out=False)
    )
    return render_template('admin/leads.html', pagination=pagination)


@admin_bp.route('/leads/<int:id>/mark-read', methods=['POST'])
@login_required
def lead_mark_read(id):
    lead = db.session.get(Lead, id) or abort(404)
    lead.is_read = True
    db.session.commit()
    flash('Lead marked as read.', 'success')
    return redirect(url_for('admin.leads'))


@admin_bp.route('/leads/<int:id>/delete', methods=['POST'])
@login_required
def lead_delete(id):
    lead = db.session.get(Lead, id) or abort(404)
    db.session.delete(lead)
    db.session.commit()
    flash('Lead deleted.', 'success')
    return redirect(url_for('admin.leads'))


# ------------------------------ SETTINGS ------------------------------

@admin_bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    form = SiteSettingForm()
    if form.validate_on_submit():
        SiteSetting.set('site_title', form.site_title.data)
        SiteSetting.set('site_tagline', form.site_tagline.data)
        SiteSetting.set('meta_description', form.meta_description.data)
        SiteSetting.set('meta_keywords', form.meta_keywords.data)
        SiteSetting.set('about_text', form.about_text.data)
        SiteSetting.set('home_hero_title', form.home_hero_title.data)
        SiteSetting.set('home_hero_subtitle', form.home_hero_subtitle.data)
        SiteSetting.set('google_search_console', form.google_search_console.data)
        SiteSetting.set('google_analytics_id', form.google_analytics_id.data)
        SiteSetting.set('facebook_app_id', form.facebook_app_id.data)
        SiteSetting.set('twitter_handle', form.twitter_handle.data)
        SiteSetting.set('instagram_url', form.instagram_url.data)
        SiteSetting.set('facebook_url', form.facebook_url.data)
        SiteSetting.set('youtube_url', form.youtube_url.data)
        SiteSetting.set('business_latitude', form.business_latitude.data)
        SiteSetting.set('business_longitude', form.business_longitude.data)
        db.session.commit()
        SiteSetting.invalidate_cache()
        SiteSetting.load_all()
        flash('Settings saved.', 'success')
        return redirect(url_for('admin.settings'))

    # Only populate from DB on GET so validation errors keep user input
    if request.method == 'GET':
        form.site_title.data = SiteSetting.get('site_title')
        form.site_tagline.data = SiteSetting.get('site_tagline')
        form.meta_description.data = SiteSetting.get('meta_description')
        form.meta_keywords.data = SiteSetting.get('meta_keywords')
        form.about_text.data = SiteSetting.get('about_text')
        form.home_hero_title.data = SiteSetting.get('home_hero_title')
        form.home_hero_subtitle.data = SiteSetting.get('home_hero_subtitle')
        form.google_search_console.data = SiteSetting.get('google_search_console')
        form.google_analytics_id.data = SiteSetting.get('google_analytics_id')
        form.facebook_app_id.data = SiteSetting.get('facebook_app_id')
        form.twitter_handle.data = SiteSetting.get('twitter_handle')
        form.instagram_url.data = SiteSetting.get('instagram_url')
        form.facebook_url.data = SiteSetting.get('facebook_url')
        form.youtube_url.data = SiteSetting.get('youtube_url')
        form.business_latitude.data = SiteSetting.get('business_latitude')
        form.business_longitude.data = SiteSetting.get('business_longitude')
    return render_template('admin/settings.html', form=form)


# ------------------------------ HELPERS ------------------------------

def _vehicle_from_form(form):
    return Vehicle(
        year=form.year.data,
        make=form.make.data.strip(),
        model=form.model.data.strip(),
        trim=form.trim.data.strip() if form.trim.data else None,
        vin=form.vin.data.strip().upper() if form.vin.data else None,
        stock_number=form.stock_number.data.strip().upper() if form.stock_number.data else None,
        price=form.price.data,
        sale_price=form.sale_price.data,
        mileage=form.mileage.data,
        condition=form.condition.data,
        title_status=form.title_status.data,
        status=form.status.data,
        body_style=form.body_style.data.strip() if form.body_style.data else None,
        exterior_color=form.exterior_color.data.strip() if form.exterior_color.data else None,
        interior_color=form.interior_color.data.strip() if form.interior_color.data else None,
        engine=form.engine.data.strip() if form.engine.data else None,
        transmission=form.transmission.data.strip() if form.transmission.data else None,
        drivetrain=form.drivetrain.data or None,
        fuel_type=form.fuel_type.data.strip() if form.fuel_type.data else None,
        mpg_city=form.mpg_city.data,
        mpg_highway=form.mpg_highway.data,
        description=form.description.data,
        features=form.features.data,
        seo_title=form.seo_title.data,
        seo_description=form.seo_description.data,
        meta_keywords=form.meta_keywords.data,
    )


def _apply_vehicle_form(vehicle, form):
    vehicle.year = form.year.data
    vehicle.make = form.make.data.strip()
    vehicle.model = form.model.data.strip()
    vehicle.trim = form.trim.data.strip() if form.trim.data else None
    vehicle.vin = form.vin.data.strip().upper() if form.vin.data else None
    vehicle.stock_number = form.stock_number.data.strip().upper() if form.stock_number.data else None
    vehicle.price = form.price.data
    vehicle.sale_price = form.sale_price.data
    vehicle.mileage = form.mileage.data
    vehicle.condition = form.condition.data
    vehicle.title_status = form.title_status.data
    vehicle.status = form.status.data
    vehicle.body_style = form.body_style.data.strip() if form.body_style.data else None
    vehicle.exterior_color = form.exterior_color.data.strip() if form.exterior_color.data else None
    vehicle.interior_color = form.interior_color.data.strip() if form.interior_color.data else None
    vehicle.engine = form.engine.data.strip() if form.engine.data else None
    vehicle.transmission = form.transmission.data.strip() if form.transmission.data else None
    vehicle.drivetrain = form.drivetrain.data or None
    vehicle.fuel_type = form.fuel_type.data.strip() if form.fuel_type.data else None
    vehicle.mpg_city = form.mpg_city.data
    vehicle.mpg_highway = form.mpg_highway.data
    vehicle.description = form.description.data
    vehicle.features = form.features.data
    vehicle.seo_title = form.seo_title.data
    vehicle.seo_description = form.seo_description.data
    vehicle.meta_keywords = form.meta_keywords.data


def _handle_vehicle_images(vehicle, files):
    existing = list(vehicle.images or [])
    order_offset = len(existing)
    for idx, file in enumerate(files):
        if not file or not getattr(file, 'filename', None):
            continue
        filename, width, height = save_uploaded_image(file, subfolder='vehicles')
        if not filename:
            continue
        is_primary = (len(existing) == 0 and idx == 0) or (
            order_offset == 0 and idx == 0 and not any(i.is_primary for i in existing)
        )
        img = VehicleImage(
            vehicle_id=vehicle.id,
            filename=filename,
            is_primary=is_primary,
            order_index=order_offset + idx,
            width=width,
            height=height,
        )
        db.session.add(img)
        existing.append(img)
    db.session.commit()


def _delete_vehicle_image_file(image):
    try:
        base = os.path.join(current_app.config['UPLOAD_FOLDER'], 'vehicles')
        path = os.path.join(base, image.filename)
        if os.path.exists(path):
            os.remove(path)
        # Remove responsive variants if present
        if '.' in image.filename:
            name, ext = image.filename.rsplit('.', 1)
            for label in current_app.config.get('IMAGE_WIDTHS', {}):
                if label == 'detail':
                    continue
                variant = os.path.join(base, f'{name}_{label}.{ext}')
                if os.path.exists(variant):
                    os.remove(variant)
    except OSError:
        pass


def _delete_carfax_file(filename):
    try:
        path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'carfax', os.path.basename(filename))
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
