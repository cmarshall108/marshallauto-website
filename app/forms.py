from flask_wtf import FlaskForm
from wtforms import (
    BooleanField, DateField, DecimalField, FileField, HiddenField,
    IntegerField, PasswordField, SelectField, StringField, SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional, ValidationError


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(max=64)])
    password = PasswordField('Password', validators=[DataRequired(), Length(max=128)])
    remember = BooleanField('Remember me')
    submit = SubmitField('Log In')


class VehicleForm(FlaskForm):
    year = IntegerField('Year *', validators=[DataRequired(), NumberRange(1900, 2100)])
    make = StringField('Make *', validators=[DataRequired(), Length(max=64)])
    model = StringField('Model *', validators=[DataRequired(), Length(max=64)])
    trim = StringField('Trim', validators=[Optional(), Length(max=128)])
    vin = StringField('VIN', validators=[Optional(), Length(max=17)])
    stock_number = StringField('Stock Number', validators=[Optional(), Length(max=32)])

    price = DecimalField('Price *', validators=[DataRequired(), NumberRange(min=0)])
    sale_price = DecimalField('Sale Price', validators=[Optional(), NumberRange(min=0)])
    mileage = IntegerField('Mileage *', validators=[DataRequired(), NumberRange(min=0)])
    condition = SelectField('Condition', choices=[
        ('used', 'Used'),
        ('certified', 'Certified Pre-Owned'),
        ('rebuilt', 'Rebuilt Title'),
    ])
    title_status = SelectField('Title Status', choices=[
        ('clean', 'Clean Title'),
        ('rebuilt', 'Rebuilt Title'),
        ('salvage', 'Salvage Title'),
    ])
    status = SelectField('Status', choices=[
        ('available', 'Available'),
        ('pending', 'Pending'),
        ('sold', 'Sold'),
    ])

    body_style = StringField('Body Style', validators=[Optional(), Length(max=64)])
    exterior_color = StringField('Exterior Color', validators=[Optional(), Length(max=64)])
    interior_color = StringField('Interior Color', validators=[Optional(), Length(max=64)])
    engine = StringField('Engine', validators=[Optional(), Length(max=128)])
    transmission = StringField('Transmission', validators=[Optional(), Length(max=128)])
    drivetrain = SelectField('Drivetrain', choices=[
        ('', '-- Select --'),
        ('FWD', 'FWD'),
        ('RWD', 'RWD'),
        ('AWD', 'AWD'),
        ('4WD', '4WD'),
    ], validators=[Optional()])
    fuel_type = StringField('Fuel Type', validators=[Optional(), Length(max=32)])
    mpg_city = IntegerField('MPG City', validators=[Optional(), NumberRange(min=0)])
    mpg_highway = IntegerField('MPG Highway', validators=[Optional(), NumberRange(min=0)])

    description = TextAreaField('Description')
    features = TextAreaField('Features (comma-separated)')

    seo_title = StringField('SEO Title', validators=[Optional(), Length(max=160)])
    seo_description = TextAreaField('SEO Description', validators=[Optional(), Length(max=320)])
    meta_keywords = StringField('Meta Keywords', validators=[Optional(), Length(max=255)])

    images = FileField('Vehicle Images', render_kw={'multiple': True})

    # Facebook Page post on save (Marketplace auto-list is not available via public API)
    post_to_facebook = BooleanField(
        'Post to Facebook Page on save',
        default=False,
        description=(
            'Creates a Facebook Page post with photos/caption and a link to this listing. '
            'Does not create a Marketplace listing (Meta does not allow that via public API).'
        ),
    )

    submit = SubmitField('Save Vehicle')


class ServiceRecordForm(FlaskForm):
    vehicle_id = SelectField('Vehicle *', coerce=int, validators=[DataRequired()])
    service_date = DateField('Service Date *', validators=[DataRequired()])
    mileage_at_service = IntegerField('Mileage at Service', validators=[Optional(), NumberRange(min=0)])
    service_type = StringField('Service Type *', validators=[DataRequired(), Length(max=128)])
    provider = StringField('Provider', validators=[Optional(), Length(max=128)])
    description = TextAreaField('Description')
    submit = SubmitField('Save Service Record')


class CarfaxReportForm(FlaskForm):
    vehicle_id = SelectField('Vehicle *', coerce=int, validators=[DataRequired()])
    report_date = DateField('Report Date', validators=[Optional()])
    accidents_reported = IntegerField('Accidents Reported', validators=[Optional(), NumberRange(min=0)])
    owners_reported = IntegerField('Owners Reported', validators=[Optional(), NumberRange(min=0)])
    summary = TextAreaField('Summary')
    report_file = FileField('CarFax PDF')
    report_url = StringField('External Report URL', validators=[Optional(), Length(max=512)])
    submit = SubmitField('Save CarFax Report')


class SiteSettingForm(FlaskForm):
    site_title = StringField('Site Title', validators=[DataRequired(), Length(max=160)])
    site_tagline = StringField('Site Tagline', validators=[Optional(), Length(max=255)])
    meta_description = TextAreaField('Default Meta Description', validators=[Optional(), Length(max=500)])
    meta_keywords = TextAreaField('Meta Keywords', validators=[Optional(), Length(max=500)])
    about_text = TextAreaField('About Page Text')
    home_hero_title = StringField('Home Hero Title', validators=[Optional(), Length(max=255)])
    home_hero_subtitle = StringField('Home Hero Subtitle', validators=[Optional(), Length(max=500)])
    google_search_console = StringField(
        'Google Search Console Verification Meta Tag',
        validators=[Optional(), Length(max=512)],
        description='Paste the full meta tag or just the content token.',
    )
    google_analytics_id = StringField('Google Analytics 4 Measurement ID', validators=[Optional(), Length(max=32)])
    google_tag_id = StringField(
        'Google Tag Manager Container ID',
        validators=[Optional(), Length(max=32)],
        description='e.g. GTM-XXXXXXX. When set, GTM loads instead of the direct GA4 snippet.',
    )
    facebook_pixel_id = StringField(
        'Facebook / Meta Pixel ID',
        validators=[Optional(), Length(max=64)],
        description='Numeric Pixel ID for Meta ads conversion tracking.',
    )
    facebook_app_id = StringField(
        'Facebook App ID',
        validators=[Optional(), Length(max=64)],
        description='Meta app ID (Open Graph fb:app_id and developer console reference).',
    )
    facebook_page_id = StringField(
        'Facebook Page ID',
        validators=[Optional(), Length(max=64)],
        description='Numeric Page ID used for Graph API posts (Page → About, or Graph API Explorer).',
    )
    facebook_page_access_token = PasswordField(
        'Facebook Page Access Token',
        validators=[Optional(), Length(max=512)],
        description=(
            'Long-lived Page access token with pages_manage_posts, pages_read_engagement, '
            'and pages_show_list. Leave blank when saving to keep the current token.'
        ),
    )
    clear_facebook_page_access_token = BooleanField(
        'Clear saved Page access token',
        default=False,
        description='Remove the token stored in Admin Settings (env token still applies if set).',
    )
    facebook_page_posting_enabled = BooleanField(
        'Enable Facebook Page vehicle posts',
        default=False,
        description=(
            'When enabled, available vehicles can be posted to your Facebook Page. '
            'Marketplace listings still require manual paste (no public Marketplace create API).'
        ),
    )
    facebook_auto_post_on_create = BooleanField(
        'Auto-post new available vehicles to Facebook Page',
        default=False,
    )
    facebook_auto_post_on_edit = BooleanField(
        'Auto-post again when an available vehicle is edited',
        default=False,
        description='Creates a new Page post with updated details (does not edit the old post).',
    )
    twitter_handle = StringField('Twitter / X Handle', validators=[Optional(), Length(max=64)])
    instagram_url = StringField('Instagram URL', validators=[Optional(), Length(max=512)])
    facebook_url = StringField('Facebook URL', validators=[Optional(), Length(max=512)])
    youtube_url = StringField('YouTube URL', validators=[Optional(), Length(max=512)])
    business_latitude = StringField('Business Latitude', validators=[Optional(), Length(max=32)])
    business_longitude = StringField('Business Longitude', validators=[Optional(), Length(max=32)])
    submit = SubmitField('Save Settings')


class ContactForm(FlaskForm):
    name = StringField('Full Name *', validators=[DataRequired(), Length(max=128)])
    email = StringField('Email *', validators=[DataRequired(), Email(), Length(max=128)])
    phone = StringField('Phone', validators=[Optional(), Length(max=32)])
    message = TextAreaField('Message', validators=[Optional(), Length(max=5000)])
    vehicle_id = HiddenField(validators=[Optional()])
    # Honeypot — leave empty; bots often fill hidden fields
    honeypot = StringField('Website', validators=[Optional(), Length(max=100)])
    submit = SubmitField('Send Message')

    def validate_vehicle_id(self, field):
        if field.data in (None, ''):
            field.data = None
            return
        try:
            field.data = int(field.data)
        except (TypeError, ValueError):
            raise ValidationError('Invalid vehicle reference.')


class ReviewForm(FlaskForm):
    author_name = StringField('Author Name *', validators=[DataRequired(), Length(max=128)])
    rating = SelectField('Rating *', coerce=int, choices=[
        (5, '5 Stars - Excellent'),
        (4, '4 Stars - Very Good'),
        (3, '3 Stars - Average'),
        (2, '2 Stars - Poor'),
        (1, '1 Star - Terrible'),
    ], validators=[DataRequired()])
    title = StringField('Review Title', validators=[Optional(), Length(max=160)])
    # Form field is "body"; model column is "content"
    body = TextAreaField('Review Body *', validators=[DataRequired(), Length(max=5000)])
    source = StringField('Source', validators=[Optional(), Length(max=64)])
    is_approved = BooleanField('Approved', default=True)
    is_featured = BooleanField('Featured on homepage')
    submit = SubmitField('Save Review')
