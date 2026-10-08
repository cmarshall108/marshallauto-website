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
  - Craigslist and public Facebook Marketplace import with local photos and scheduled source refreshes
  - VIN decode (NHTSA vPIC + EPA) to prefill year/make/model/trim/specs, MPG, and default safety features when adding a vehicle
  - Cascading typeahead suggestions for make/model/trim, colors, and features
  - “LOW MILES!” badge on inventory cards under 75,000 miles
  - Manage service records per vehicle
  - Upload and link CarFax PDF reports
  - View and manage customer leads (with UTM / click-ID attribution)
  - Edit site settings, SEO defaults, and analytics IDs
  - Facebook Page auto-post for new/updated vehicles + Marketplace paste draft (see below)

CarFax PDFs linked from the admin list and edit pages use `/carfax/<report_id>/download`.
Authenticated admins can view reports for available or sold vehicles; public visitors
can only download reports for available vehicles.

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

### Bulk vehicle photos and videos

With JavaScript enabled, Save Vehicle saves the details first, then uploads each photo
in order in a separate request and finally uploads the video. The form shows progress
and stops on errors; already-saved media is kept. If a request is interrupted, use the
link to the saved vehicle and check its media before retrying to avoid duplicates.
Without JavaScript, the ordinary multipart form still works, subject to the total
request size limit.

HEIC/HEIF photos are decoded one at a time in a short-lived subprocess, with one
decoder thread, a 45-second timeout, and a 512 MiB virtual-memory ceiling on Linux.
Full-size photo buffers are resized before EXIF/color transformations and released
after saving. Decoder failures are logged and return an explicit upload error
instead of crashing the web process. If a photo exceeds the conversion budget,
export it as JPEG and retry. This mitigates HEIC decoder/resource failures; a 520
still requires origin logs to identify its actual cause. Upload errors show the
filename, HTTP status, and a Cloudflare Ray ID when present.

Video compression runs as a durable database job in the existing
`python -m app.highlight_worker` process, not inside the web request. The Ubuntu launcher
starts this worker by default (`START_HIGHLIGHT_WORKER=1`); other deployments must run
it separately, even if photo highlights are disabled. Install ffmpeg and run
`flask db upgrade` before deployment. Queued/running/failed video status appears on the
vehicle edit page; refresh to check it. The previous video stays available until the
replacement succeeds. Removing/replacing a video, selling, or deleting the vehicle
prevents older jobs from publishing.

The Ubuntu one-time installer and automatic deploy script both check/install
system `ffmpeg`. The Python requirements also include `imageio-ffmpeg`, which bundles
an executable on supported platforms. Web and worker processes prefer system FFmpeg
and fall back to the bundled executable when it is absent; no manual SSH installation
is needed on supported deployments. This is still native FFmpeg, not a pure-Python codec.

If apt installation fails, automatic deployment logs a warning and installs the Python
requirements instead. It verifies the resolved compressor with `-version` before
restarting; if neither option works, deployment rolls back and logs the failure.
See `/var/log/marshallauto-deploy.log` for the selected executable and any errors.
For an unsupported platform, or to repair an existing system installation manually:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends ffmpeg
ffmpeg -version
sudo systemctl restart marshallauto
```

Then reopen the saved vehicle and upload the video again. A 422 reporting missing
ffmpeg means that video was rejected before staging; existing vehicle details/media
are unaffected. On non-Ubuntu hosts, install ffmpeg with the host's package manager
and ensure both web and worker processes can find it on their PATH.

The per-media-request limit defaults to 90 MiB, below the 100 MB ceiling on common
Cloudflare plans. The form reserves 64 KiB for multipart overhead. Larger individual
videos need compression before upload or a YouTube/Vimeo URL; splitting photo requests
does not bypass a proxy's single-file limit. Keep any reverse-proxy body limit at least
as large as this setting, and only increase it if the hosting/proxy plan supports it.

A Cloudflare 520 alone does not identify the cause. Check the origin log
(`/var/log/marshallauto.log` on the Ubuntu deployment) and system journal at the failure
time for worker exits, out-of-memory kills, disk exhaustion, and connection resets.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `SECRET_KEY` | Flask secret key (change in production) |
| `DATABASE_URL` | Database connection string (defaults to SQLite) |
| `ADMIN_USERNAME` | Admin login username |
| `ADMIN_PASSWORD` | Admin login password |
| `MAX_CONTENT_LENGTH` | Total multipart request limit in bytes (default 256 MiB) |
| `MEDIA_UPLOAD_MAX_CONTENT_LENGTH` | Individual media request limit in bytes (default 90 MiB; capped by `MAX_CONTENT_LENGTH`) |
| `HEIC_CONVERSION_TIMEOUT` | HEIC subprocess timeout in seconds (default 45) |
| `HEIC_CONVERSION_MEMORY_MB` | Linux HEIC subprocess virtual-memory ceiling in MiB (default 512; tune for available RAM) |
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
| `LICENSE_GROK_MODEL` | xAI vision model for license extraction and visual screening (default `grok-4.5`) |
| `LICENSE_GROK_TIMEOUT` | License-analysis request timeout in seconds (default `45`) |
| `PHOTO_HIGHLIGHTS_GROK_REQUIRED` | If `true`, fail the job instead of OpenCV fallback when Grok errors |
| `HIGHLIGHT_WORKER_POLL` | Worker idle poll seconds (default `2`) |
| `HIGHLIGHT_WORKER_LEASE` | Job lease seconds (default `300`) |

Analytics and Facebook posting credentials can also be set in **Admin → Settings** (`google_tag_id`, `google_analytics_id`, `facebook_pixel_id`, Page ID, Page access token), which override env defaults when present.

## Facebook Page posts & Marketplace drafts

**Important:** Meta does **not** provide a public API for ordinary third-party apps to automatically create **Facebook Marketplace** vehicle listings. Marketplace listing creation is limited to Meta partnership programs. Outbound Page posting and Marketplace drafts are separate from the optional public-listing importer below.

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


## Import From Craigslist or Facebook Marketplace

On **Add Vehicle**, paste a Craigslist vehicle URL or a full `https://www.facebook.com/marketplace/item/123456789/` URL, confirm permission, and select **Import listing**. Review the populated details and photo previews, fill missing fields, and save. The initial import downloads the listing photos into the existing local image storage, including responsive variants. No account passwords, cookies, paid providers, or Page tokens are used.

**Craigslist:** Both current `https://www.craigslist.org/view/d/.../<id>` links and regional vehicle URLs such as `https://raleigh.craigslist.org/cto/d/.../7974467394.html` are supported. Only HTTPS Craigslist listing redirects are followed; account, management, search, and external redirects are rejected. The importer reads the listing's JSON-LD, description, vehicle attributes, and ordered gallery. It imports exposed year/make/model, USD price, mileage, title status, transmission, fuel, body style, paint color, drive, VIN, and cylinder information. Other attributes are preserved in the source snapshot and features. Missing information is never inferred from photos or advertising claims. Descriptions retain their line breaks and title-history disclosures. Photos must come from `images.craigslist.org`, and mismatched gallery data aborts the import without removing saved photos.

The supplied [2018 Buick Regal Sportback listing](https://www.craigslist.org/view/d/sanford-2018-buick-regal-sportback/nE3yePJrnEKxic4P388Xg7) was live-tested on October 3, 2026: $7,999, 67,000 miles, rebuilt title, and all 16 photos downloaded successfully. A subsequent real-source refresh reused unchanged photos and rescheduled the task. Listing contents and availability may change after this test.

This is **best-effort public-page parsing**, not an official listing API. Only details actually exposed by the source can be imported. Login walls, removed listings, changed page formats, missing photo collections, non-USD prices, and more than 40 photos are rejected. Facebook share links are not followed. Do not assume missing information (especially mileage, trim, or title history) is known; review all vehicle fields before publishing. Full details and photos cannot be guaranteed for every listing. The tested Facebook listing required login and could not be imported.

Import only content you have rights to reuse and for which automated access is authorized. Meta's [Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms) require permission; ownership of the photos alone does not grant that permission. This importer does not bypass Facebook access controls.

Craigslist's [Terms of Use](https://www.craigslist.org/about/terms.of.use) also restrict automated collection without a separate license. Public accessibility is not itself permission. The importer does not bypass logins, challenges, or blocks, and does not retrieve seller contact details or account-management links.

**Refresh behavior:** Saving an imported vehicle creates a persistent task, enabled by default and due every six hours. The worker applies source fields that changed since its last successful snapshot, reconciles imported photos, and retains staff-uploaded photos. Missing source fields do not clear local values. A changed source value takes precedence over a staff edit to that same field; unchanged source values leave staff edits intact. Photo IDs identify unchanged images, so renewed CDN signatures do not cause duplicate downloads. A Facebook sold/pending state updates local status; unavailable listings never imply sold or trigger deletion. Failures retain saved data and retry after six hours. Pause/resume syncing on **Edit Vehicle**, where the last successful import, next due time, and errors are shown. Refreshes do not automatically repost to the Facebook Page.

Apply the schema migration and arrange for the worker to run:

```bash
venv/bin/python -m flask --app run:app db upgrade
venv/bin/python -m flask --app run:app listing-sync
```

The command checks up to 10 due vehicles per run (`--limit` accepts 1-100), with database claims preventing duplicate processing by concurrent workers. `marketplace-sync` remains a compatible alias. Both providers use the existing `MarketplaceSync` table, so Craigslist support adds no extra schema migration. For Ubuntu, the existing `deploy/install.sh` installer installs and enables `marshallauto-marketplace-sync.timer`, which now handles both Craigslist and Facebook records. On an existing deployment, rerun that installer as the server administrator after updating and migrating. The timer invokes the command every 15 minutes; each vehicle retains its six-hour schedule. Inspect failures with `journalctl -u marshallauto-marketplace-sync.service`. This macOS workspace does not install or start systemd services.

For another host, schedule the same command every 15 minutes using cron or the host's scheduler, with the project working directory, virtual environment, and production environment. Merely starting the web server does **not** execute refresh tasks. Initial photo import can take up to about two minutes; configure WSGI/reverse-proxy request timeouts of at least 180 seconds for this admin operation.

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

### Inventory and VIN indexing

Every retained vehicle listing, including sold and pending vehicles, has an
indexable detail page and appears in `/sitemap.xml`. VINs are server-rendered in
the listing text, title, description, and Car structured data when a VIN is on
file. `/inventory/history` provides paginated links to previous listings and
their VINs. Current inventory and `/feeds/vehicles.xml` remain available-only.
Sold pages clearly identify the vehicle as sold and link buyers to current stock.
Keep sold records rather than deleting them to preserve their URLs and VIN content.

Freshness is based on actual listing creation/update timestamps, not daily date
rewrites. Vehicle pages expose `datePublished` and `dateModified` in WebPage
structured data and show the listing update date. The sitemap uses precise UTC
update timestamps and does not invent modification dates for static pages.
Listing status/price/content changes update the vehicle timestamp through the
existing database model. Sitemap responses may be cached for up to one hour.

### Search engine rollout

1. Deploy the changes and confirm `SITE_URL` is the canonical HTTPS public domain.
2. Verify the domain in Google Search Console and Bing Webmaster Tools. The admin
  settings support Google's verification tag; DNS verification also works for
  either service.
3. Submit `https://marshallautosanford.com/sitemap.xml` in both services, replacing
  the domain if your configured public domain differs.
4. Use Google's URL Inspection on the Sanford inventory page, a current vehicle,
  and a sold vehicle with a VIN. Request indexing for these representative URLs.
5. Validate rendered vehicle structured data and review indexing/crawl reports.
  Check search impressions and clicks for VIN queries and Sanford-area searches.
6. Keep the Google Business Profile address, phone, hours, inventory website link,
  photos, and genuine reviews accurate. Add useful, original Sanford-area buying
  guides and detailed vehicle descriptions rather than duplicating city pages.

These changes make pages discoverable and eligible for indexing; they cannot
force Google or Bing to index every VIN or guarantee rankings, rich results, or
traffic. VINs missing from records and listings that have been deleted cannot be
exposed by these changes. Archived VIN pages are dealership listing records, not
vehicle-history reports. Do not mark sold cars as available to attract searches.

- [ ] Set `SITE_URL` to your real domain
- [ ] Add Google Tag Manager and/or GA4 + Facebook Pixel IDs (env or Admin → Settings)
- [ ] Update business address and hours in `config.py`
- [ ] Replace placeholder images in `app/static/images/`
- [ ] Submit `sitemap.xml` to Google Search Console
- [ ] Create a Google Business Profile
- [ ] Add real social media links in the footer
- [ ] Confirm local landing pages for your service cities resolve and are in the sitemap
