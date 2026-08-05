# Marshall Auto LLC Website

A complete, SEO-optimized used car dealership website built with Python and Flask. Includes a public-facing inventory system, vehicle detail pages, financing and contact forms, and a secure admin panel for managing vehicles, service records, and CarFax reports.

## Features

- **Public Website**
  - Responsive, modern design with Bootstrap 5
  - Home page with featured inventory and search
  - Inventory listing with filters (make, body style, price, mileage, search)
  - Vehicle detail pages with image gallery, specs, features, service history, and CarFax
  - Financing, sell-your-car, about, and contact pages
  - SEO: meta tags, Open Graph, JSON-LD (AutoDealer, Car, FAQ, HowTo, ItemList, Breadcrumb), sitemap.xml, robots.txt
  - Local SEO landings for cities, makes, body styles, and rebuilt-title inventory
  - Site analytics: GTM (preferred), GA4 fallback, Facebook Pixel, custom event layer

- **Admin Panel** (`/admin`)
  - Secure login with Flask-Login
  - Add/edit/delete vehicles with image uploads
  - Manage service records per vehicle
  - Upload and link CarFax PDF reports
  - View and manage customer leads (with UTM / click-ID attribution)
  - Edit site settings, SEO defaults, and analytics IDs

## Quick Start

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2. Create a `.env` file from the example:

```bash
cp .env.example .env
```

Edit `.env` and set at least `SECRET_KEY` and `ADMIN_PASSWORD`.

3. Run the application:

```bash
python run.py
```

source .venv/bin/activate

The website will be available at `http://127.0.0.1:8080` and the admin panel at `http://127.0.0.1:8080/admin`.

> **Note for macOS users:** Port 5000 is used by macOS AirPlay Receiver, so the development server defaults to port 8080.

The default admin credentials are set by `ADMIN_USERNAME` and `ADMIN_PASSWORD` in your `.env` file (defaults to `admin`/`admin`).

4. (Optional) Seed sample data:

```bash
flask seed
```

## Production Deployment

For production, set strong credentials and use a production WSGI server such as Gunicorn:

```bash
gunicorn -w 4 -b 0.0.0.0:8000 "run:create_app()"
```

Set `DATABASE_URL` to a production PostgreSQL database for better performance and reliability.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask secret key (change in production) |
| `DATABASE_URL` | Database connection string (defaults to SQLite) |
| `ADMIN_USERNAME` | Admin login username |
| `ADMIN_PASSWORD` | Admin login password |
| `SITE_URL` | Public site URL used for sitemaps and structured data |
| `BUSINESS_NAME` | Business name |
| `BUSINESS_PHONE` | Business phone number |
| `BUSINESS_EMAIL` | Business email |
| `BUSINESS_ADDRESS` | Business street address |
| `GOOGLE_TAG_ID` | Google Tag Manager container ID (optional; preferred over bare GA4) |
| `GOOGLE_ANALYTICS_ID` | GA4 measurement ID used only when GTM is not set (optional) |
| `FACEBOOK_PIXEL_ID` | Facebook Pixel ID (optional) |

Analytics IDs can also be set in **Admin → Settings** (`google_tag_id`, `google_analytics_id`, `facebook_pixel_id`), which override env defaults.

## Analytics Events

Client-side tracking lives in `app/static/js/main.js` (`MarshallAnalytics`). Events are pushed to `dataLayer` (GTM), `gtag` (GA4 fallback), and Facebook Pixel where relevant:

| Event | When |
|-------|------|
| `page_context` | Every page load (page type + business) |
| `view_item` / Pixel `ViewContent` | Vehicle detail pages |
| `view_search_results` / `search` | Inventory with filters or search |
| `inventory_filter` | Filter form submit |
| `generate_lead` / Pixel `Lead` + `Contact` | Contact form success (AJAX or full POST) |
| `click_to_call` / `click_to_sms` / `click_to_email` | `tel:`, `sms:`, `mailto:` clicks |
| `file_download` | CarFax / PDF links |
| `gallery_engagement` | Vehicle photo thumbs / swipe |
| `payment_calculated` | Financing calculator results |

UTM parameters (`utm_*`, `gclid`, `fbclid`) are stored in `sessionStorage` and attached to lead form submissions. Leads persist attribution fields in the database for reporting.

## SEO Checklist

- [ ] Set `SITE_URL` to your real domain
- [ ] Add Google Tag Manager and/or GA4 + Facebook Pixel IDs (env or Admin → Settings)
- [ ] Update business address and hours in `config.py`
- [ ] Replace placeholder images in `app/static/images/`
- [ ] Submit `sitemap.xml` to Google Search Console
- [ ] Create a Google Business Profile
- [ ] Add real social media links in the footer
- [ ] Confirm local landing pages for your service cities resolve and are in the sitemap
