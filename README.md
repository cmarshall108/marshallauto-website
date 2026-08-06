# Marshall Auto LLC Website

A complete, SEO-optimized used car dealership website built with Python and Flask. Includes a public-facing inventory system, vehicle detail pages, financing and contact forms, and a secure admin panel for managing vehicles, service records, and CarFax reports.

## Features

- **Public Website**
  - Responsive, modern design with Bootstrap 5
  - Home page with featured inventory and search
  - Inventory listing with filters (make, body style, price, mileage, search)
  - Vehicle detail pages with image gallery, specs, features, service history, and CarFax
  - Carvana-style photo highlight bubbles (features + condition notes) powered by a local OpenCV worker
  - Financing, sell-your-car, about, and contact pages
  - SEO: meta tags, Open Graph, JSON-LD (AutoDealer, Car, FAQ, HowTo, ItemList, Breadcrumb), sitemap.xml, robots.txt
  - Local SEO landings for cities, makes, body styles, and rebuilt-title inventory
  - Site analytics: GTM (preferred), GA4 fallback, Facebook Pixel, custom event layer

- **Admin Panel** (`/admin`)
  - Secure login with Flask-Login
  - Add/edit/delete vehicles with image uploads
  - VIN decode (NHTSA vPIC + EPA) to prefill year/make/model/trim/specs, MPG, and default safety features when adding a vehicle
  - Cascading typeahead suggestions for make/model/trim, colors, and features
  - “LOW MILES!” badge on inventory cards under 75,000 miles
  - Manage service records per vehicle
  - Upload and link CarFax PDF reports
  - View and manage customer leads (with UTM / click-ID attribution)
  - Edit site settings, SEO defaults, and analytics IDs
  - Facebook Page auto-post for new/updated vehicles + Marketplace paste draft (see below)

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
| `FACEBOOK_PAGE_ID` | Facebook Page ID for Graph API vehicle posts (optional; also in Admin → Settings) |
| `FACEBOOK_PAGE_ACCESS_TOKEN` | Long-lived **Page** access token (optional env fallback; preferred in Admin → Settings) |
| `FACEBOOK_AUTO_POST_VEHICLES` | Env fallback to enable Page posting (`true`/`false`) |
| `PHOTO_HIGHLIGHTS_ENABLED` | Enable photo highlight system (`true`/`false`, default true) |
| `PHOTO_HIGHLIGHTS_AUTO_ENQUEUE` | Auto-queue analysis on image upload (`true`/`false`, default true) |
| `PHOTO_HIGHLIGHTS_MAX` | Max bubbles per photo (default `5`) |
| `PHOTO_HIGHLIGHTS_ENGINE` | `grok` (default), `auto`, or `opencv` fallback-only |
| `PHOTO_HIGHLIGHTS_GROK_MODEL` | xAI vision model (default `grok-4.5`) |
| `XAI_API_KEY` | xAI API key for Grok vision highlights (required for Grok engine) |
| `PHOTO_HIGHLIGHTS_GROK_REQUIRED` | If `true`, fail the job instead of OpenCV fallback when Grok errors |
| `HIGHLIGHT_WORKER_POLL` | Worker idle poll seconds (default `2`) |
| `HIGHLIGHT_WORKER_LEASE` | Job lease seconds (default `300`) |

Analytics and Facebook posting credentials can also be set in **Admin → Settings** (`google_tag_id`, `google_analytics_id`, `facebook_pixel_id`, Page ID, Page access token), which override env defaults when present.

## Facebook Page posts & Marketplace drafts

**Important:** Meta does **not** provide a public API for ordinary third-party apps to automatically create **Facebook Marketplace** vehicle listings. Marketplace listing creation is limited to Meta partnership programs. This site does **not** scrape or browser-automate Marketplace (fragile and against Meta’s terms).

What *is* supported:

1. **Facebook Page posts** via Graph API when you add/edit an available vehicle (photo + caption + link to your inventory page).
2. **Marketplace-ready draft** on the vehicle edit screen — copy title/price/description and paste into [Marketplace → Create vehicle](https://www.facebook.com/marketplace/create/vehicle). Upload photos there manually.

### Setup

1. Create a Meta developer app and add the **Facebook Login** / Pages product as needed.
2. Generate a **Page access token** for your business Page with at least:
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `pages_show_list`
3. Prefer a **long-lived Page token**.
4. In **Admin → Settings → Facebook / Meta account details**, enter:
   - Facebook App ID (optional, for Open Graph)
   - Facebook Page ID
   - Facebook Page Access Token
   - Enable posting / auto-post toggles
5. Optionally set the same values in `.env` (`FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_ACCESS_TOKEN`) as a fallback. Admin Settings takes priority for the token when saved there. Never commit tokens to git.
6. On **Add/Edit Vehicle**, use **Post to Facebook Page on save**, or **Post to Facebook Page now**, and **Copy Marketplace draft** when you want a Marketplace listing.

Post status (`facebook_post_id`, last error/time) is stored on each vehicle
Analytics IDs can also be set in **Admin → Settings** (`google_tag_id`, `google_analytics_id`, `facebook_pixel_id`), which override env defaults.


## Photo Highlights (Carvana-style bubbles)

Listing photos can show clickable hotspot bubbles for features (CarPlay, leather seats, new tires, sunroof, etc.) and condition notes (scratches, dings). Analysis runs in a **background worker** — uploads never wait on the model.

**Primary engine:** [Grok vision](https://docs.x.ai/) via the xAI API (`XAI_API_KEY`).  
**Fallback:** local OpenCV if the key is missing or the API call fails (unless `PHOTO_HIGHLIGHTS_GROK_REQUIRED=true`).

Coordinates are stored as percent of the **full source image**. The gallery JS maps them through `object-fit: cover` so bubbles sit on the painted car, not the cropped stage edges.

### How it works

1. Admin uploads vehicle photos (or saves a vehicle with feature text).
2. The web app **only enqueues** a DB-backed `PhotoHighlightJob`.
3. A **separate worker process** claims jobs, calls Grok (or OpenCV), and writes `VehicleImageHighlight` rows.
4. The public vehicle gallery renders bubbles + a detail card when analysis is `ready`.

### Grok / xAI setup

1. Create an API key at [console.x.ai](https://console.x.ai/).
2. Put it in `.env` on the app host **and** the worker host (never commit the key):

```bash
XAI_API_KEY=xai-...
PHOTO_HIGHLIGHTS_ENGINE=grok
PHOTO_HIGHLIGHTS_GROK_MODEL=grok-4.5
PHOTO_HIGHLIGHTS_MAX=5
```

3. Restart the web app and `python -m app.highlight_worker`.
4. In Admin → vehicle edit, use **Re-analyze all photos** so existing images pick up Grok results.

### Run the worker

In a second terminal (or as a Procfile `worker` process):

```bash
python -m app.highlight_worker
# one-shot:
python -m app.highlight_worker --once
# flask CLI:
flask highlight-worker
flask highlight-enqueue-all
```

On platforms that use the included `Procfile`, start both `web` and `worker`.

### Admin controls

On **Edit Vehicle**:

- Per-image highlight status (`pending` / `processing` / `ready` / `failed`)
- **Analyze** one image or **Re-analyze all**
- Toggle visibility or delete individual bubbles

Optional queue snapshot: `GET /admin/api/highlight-queue` (logged-in admin).

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
