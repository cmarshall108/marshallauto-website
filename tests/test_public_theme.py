import json
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle, VehicleImage, VehicleImageHighlight
from config import TestingConfig


class PublicThemeTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        db.session.add(Vehicle(
            slug='theme-car', year=2022, make='Toyota', model='Camry',
            price=18900, mileage=42000, status='available',
        ))
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, 'html.parser')

    def test_public_theme_is_loaded_on_shared_pages_but_not_admin(self):
        for path in ('/', '/inventory', '/inventory/theme-car', '/coming-soon',
                     '/contact', '/financing', '/sell-your-car', '/about',
                     '/service-area', '/saved-vehicles', '/compare', '/blog'):
            with self.subTest(path=path):
                html = self.page(path)
                self.assertIn('public-site', html.body.get('class', []))
                styles = [link['href'].split('?')[0]
                          for link in html.select('link[rel="stylesheet"]')]
                self.assertLess(styles.index('/static/css/main.css'),
                                styles.index('/static/css/public.css'))
        admin = self.page('/admin/login')
        self.assertNotIn('public-site', admin.body.get('class', []))
        self.assertFalse(admin.select('link[href*="public.css"]'))
        with self.client.get('/static/css/public.css') as response:
            self.assertEqual(response.status_code, 200)

    def test_public_scripts_are_deferred_and_homepage_hero_is_preloaded(self):
        html = self.page('/')
        for script in html.select(
            'script[src*="bootstrap.bundle"], script[src*="/static/js/"]',
        ):
            self.assertIn('defer', script.attrs, script.get('src'))
        hero_preload = html.select_one(
            'link[rel="preload"][as="image"][href*="gallardo_cover.jpg"]',
        )
        self.assertIsNotNone(hero_preload)
        self.assertEqual(hero_preload.get('fetchpriority'), 'high')

    def test_current_navigation_and_inventory_section_are_distinguished(self):
        for path, label in (('/', 'Home'), ('/inventory', 'Inventory'),
                            ('/contact', 'Contact'), ('/financing', 'Financing')):
            with self.subTest(path=path):
                nav = self.page(path).select_one('nav[aria-label="Main navigation"]')
                current = nav.select('[aria-current="page"]')
                self.assertEqual(len(current), 1)
                self.assertEqual(current[0].get_text(strip=True), label)
                self.assertIn('active', current[0]['class'])
        nav = self.page('/inventory/theme-car').select_one(
            'nav[aria-label="Main navigation"]',
        )
        inventory = nav.select_one('a[href="/inventory"]')
        self.assertIn('active', inventory['class'])
        self.assertNotIn('aria-current', inventory.attrs)

    def test_header_call_button_uses_high_contrast_accent_styles(self):
        html = self.page('/')
        call_button = html.select_one('.navbar a.btn-primary[href^="tel:"]')
        self.assertIsNotNone(call_button)
        self.assertIn('Call Now', call_button.get_text(' ', strip=True))

        with self.client.get('/static/css/public.css') as response:
            css = response.get_data(as_text=True)
        self.assertIn('.public-site .navbar .btn-primary {', css)
        self.assertIn('background-color: var(--accent);', css)
        self.assertIn('color: #142238 !important;', css)
        self.assertIn('.public-site .navbar .btn-primary:hover,', css)
        self.assertIn('background-color: #ffd27e;', css)

    def test_financing_page_explains_in_person_lendmark_application(self):
        html = self.page('/financing')
        page_text = html.get_text(' ', strip=True)
        self.assertIn('Lendmark Financial Services', page_text)
        self.assertIn('complete an application for a vehicle you are interested in', page_text)
        self.assertIn('not guaranteed', page_text)
        self.assertIn('final signed credit agreement', page_text)
        self.assertIn('Do not send account numbers', page_text)
        self.assertNotIn('Westlake Financial', page_text)
        self.assertNotIn('PenFed', page_text)
        self.assertIsNone(html.select_one('a[href="https://www.penfed.org/auto"]'))

        apr = html.select_one('#calc-rate')
        self.assertIsNotNone(apr)
        self.assertEqual(apr.get('required'), '')
        self.assertNotIn('value', apr.attrs)
        self.assertIn('Illustration only', page_text)
        self.assertIn('Total Loan Payments (excl. down payment)', page_text)

    def test_home_search_keeps_get_fields_and_has_accessible_labels(self):
        html = self.page('/')
        form = html.select_one('.search-box form')
        self.assertEqual(form['action'], '/inventory')
        self.assertEqual(form['method'].lower(), 'get')
        for name in ('make', 'model', 'max_price'):
            field = form.select_one(f'[name="{name}"]')
            self.assertIsNotNone(field)
            self.assertIsNotNone(form.select_one(f'label[for="{field["id"]}"]'))
        budget = form.select_one('[name="max_price"]')
        self.assertEqual(budget['min'], '0')
        self.assertEqual(budget['inputmode'], 'numeric')

    def test_shopping_pages_make_call_actions_easy_to_find(self):
        home = self.page('/')
        self.assertTrue(any('Call Our Cell' in link.get_text(' ', strip=True)
                            for link in home.select('a[href^="tel:"]')))
        home_badge = home.select_one(
            '.vehicle-card .card-img-wrapper .badge-low-miles',
        )
        self.assertEqual(
            home_badge.select_one('.badge-low-miles-label').get_text(strip=True),
            'Low Mileage',
        )
        self.assertEqual(
            home_badge.select_one('.badge-low-miles-value').get_text(strip=True),
            '42K Miles',
        )

        inventory = self.page('/inventory')
        self.assertTrue(any('Call' in link.get_text(' ', strip=True)
                            for link in inventory.select('a[href^="tel:"]')))
        vehicle_cards = inventory.select('.vehicle-card')
        self.assertTrue(vehicle_cards)
        for card in vehicle_cards:
            self.assertIn('Lendmark Financial Services', card.get_text(' ', strip=True))
            self.assertIsNone(card.select_one('a[href="https://www.penfed.org/auto"]'))
        low_mileage_banner = inventory.select_one(
            '.vehicle-card .card-img-wrapper .badge-low-miles',
        )
        self.assertIsNotNone(low_mileage_banner)
        self.assertEqual(
            low_mileage_banner.select_one('.badge-low-miles-value').get_text(strip=True),
            '42K Miles',
        )
        self.assertIn('42,000 mi', low_mileage_banner['title'])
        summary = self.client.get(
            '/api/vehicles-summary?slugs=theme-car',
        ).get_json()['vehicles'][0]
        self.assertTrue(summary['is_low_mileage'])
        self.assertEqual(summary['low_mileage_badge'], '42K Miles')

    def test_vehicle_detail_offers_in_person_lendmark_financing(self):
        detail = self.page('/inventory/theme-car')
        sidebar = detail.select_one('.sticky-lg-top')
        sidebar_text = sidebar.get_text(' ', strip=True)
        self.assertIn('Lendmark Financial Services', sidebar_text)
        self.assertIn('Visit us to complete', sidebar_text)
        self.assertIn('eligibility, approval, and terms', sidebar_text)
        self.assertIsNone(sidebar.select_one('a[href="https://www.penfed.org/auto"]'))

    def test_home_and_inventory_vehicle_cards_offer_lendmark_financing(self):
        for path in ('/', '/inventory'):
            with self.subTest(path=path):
                html = self.page(path)
                cards = html.select('.inventory-grid .vehicle-card')
                self.assertTrue(cards)
                for card in cards:
                    self.assertIn('Lendmark Financial Services', card.get_text(' ', strip=True))
                    self.assertIsNone(card.select_one('a[href="https://www.penfed.org/auto"]'))

    def test_related_inventory_cards_show_actual_low_mileage(self):
        db.session.add(Vehicle(
            slug='related-low-mileage-car', year=2021, make='Toyota',
            model='Corolla', price=20500, mileage=27500, status='available',
        ))
        db.session.commit()

        detail = self.page('/inventory/theme-car')
        related_badges = detail.select(
            '.inventory-grid .card-img-wrapper .badge-low-miles',
        )
        self.assertEqual(len(related_badges), 1)
        self.assertEqual(
            related_badges[0].select_one('.badge-low-miles-value').get_text(strip=True),
            '27K Miles',
        )
        self.assertEqual(
            related_badges[0].select_one('.badge-low-miles-label').get_text(strip=True),
            'Low Mileage',
        )
        self.assertIn(
            'Lendmark Financial Services',
            detail.select_one('.inventory-grid .vehicle-card').get_text(' ', strip=True),
        )

    def test_older_vehicle_under_100k_is_badged_on_inventory(self):
        older_vehicle = Vehicle(
            slug='older-low-mileage-car',
            year=datetime.now().year - 9,
            make='Toyota',
            model='Corolla',
            price=10500,
            mileage=99999,
            status='available',
        )
        db.session.add(older_vehicle)
        db.session.commit()

        inventory = self.page('/inventory')
        vehicle_link = inventory.select_one(
            'a[href="/inventory/older-low-mileage-car"]',
        )
        card = vehicle_link.find_parent(class_='vehicle-card')
        badge = card.select_one('.card-img-wrapper .badge-low-miles')
        self.assertEqual(
            badge.select_one('.badge-low-miles-value').get_text(strip=True),
            '99K Miles',
        )
        summary = self.client.get(
            '/api/vehicles-summary?slugs=older-low-mileage-car',
        ).get_json()['vehicles'][0]
        self.assertTrue(summary['is_low_mileage'])
        self.assertEqual(summary['low_mileage_badge'], '99K Miles')

    def test_browser_theme_colors_match_manifest_and_tile_config(self):
        html = self.page('/')
        with self.client.get('/static/manifest.json') as response:
            manifest = json.loads(response.data)
        with self.client.get('/static/browserconfig.xml') as response:
            tiles = ET.fromstring(response.data)
        self.assertEqual(html.select_one('meta[name="theme-color"]')['content'],
                         manifest['theme_color'])
        self.assertEqual(html.select_one('meta[name="msapplication-TileColor"]')['content'],
                         tiles.findtext('.//TileColor'))
        self.assertEqual(manifest['theme_color'], '#142238')
        self.assertEqual(manifest['background_color'], '#f4f6f8')

    def test_storefront_hero_and_mobile_actions_emphasize_shopping(self):
        html = self.page('/')
        self.assertIsNotNone(html.select_one('.hero-actions a[href="/inventory"]'))
        self.assertIsNotNone(html.select_one('.hero-actions a[href^="tel:"]'))
        self.assertIsNotNone(html.select_one('.search-box form'))
        self.assertIsNotNone(html.select_one('.cookie-consent-banner'))
        banner = html.select_one('a.financing-banner')
        self.assertIsNotNone(banner)
        self.assertEqual(banner['href'], '/financing')
        self.assertIn('We Now Offer Financing', banner.get_text(' ', strip=True))

        css = (Path(self.app.static_folder) / 'css' / 'public.css').read_text()
        self.assertIn('.public-site .financing-banner {', css)
        self.assertIn('.hero-actions .btn-primary', css)
        self.assertIn('mobile-cta-bar) .btn-primary', css)
        self.assertIn('prefers-reduced-motion: reduce', css)

    def test_mobile_layout_keeps_fixed_actions_and_navigation_accessible(self):
        public_css = (Path(self.app.static_folder) / 'css' / 'public.css').read_text()
        shared_css = (Path(self.app.static_folder) / 'css' / 'main.css').read_text()

        self.assertIn('.mobile-cta-bar .btn {', public_css)
        self.assertIn('white-space: nowrap;', public_css)
        self.assertIn('min-height: 44px;', public_css)
        self.assertIn('.navbar-collapse.show', public_css)
        self.assertIn('overflow-y: auto;', public_css)
        self.assertIn('max-height: calc(100dvh - 13.5rem);', public_css)
        self.assertIn('.search-box form .row > .col-12:nth-child(-n+2)', public_css)
        self.assertIn('body.has-mobile-cta.has-compare-bar .cookie-consent-banner', shared_css)
        self.assertIn('bottom: calc(6rem + env(safe-area-inset-bottom));', shared_css)
        self.assertIn('bottom: calc(12rem + env(safe-area-inset-bottom));', shared_css)

    def test_photo_highlights_include_magnified_feature_viewer(self):
        vehicle = Vehicle.query.filter_by(slug='theme-car').one()
        image = VehicleImage(
            vehicle_id=vehicle.id,
            filename='theme-car.jpg',
            is_primary=True,
            order_index=2,
            highlight_status='ready',
        )
        db.session.add(image)
        db.session.flush()
        db.session.add_all([
            VehicleImageHighlight(
                vehicle_image_id=image.id,
                x_pct=28,
                y_pct=64,
                label='Alloy Wheels',
                category='feature',
                description='Clean multi-spoke wheels.',
                icon='circle',
                severity='positive',
                confidence=0.91,
                source='auto',
                order_index=0,
            ),
            VehicleImageHighlight(
                vehicle_image_id=image.id,
                x_pct=72,
                y_pct=40,
                label='Door Ding',
                category='imperfection',
                description='Small mark on the driver door.',
                icon='exclamation-circle',
                severity='caution',
                confidence=0.74,
                source='manual',
                order_index=1,
            ),
        ])
        db.session.commit()

        detail = self.page('/inventory/theme-car')
        zoom = detail.select_one('#gallery-hotspot-zoom-image')
        self.assertIsNotNone(zoom)
        self.assertIsNotNone(detail.select_one('#gallery-hotspot-facts'))
        self.assertIsNotNone(detail.select_one('#gallery-hotspot-zoom'))
        self.assertIsNotNone(detail.select_one('#gallery-hotspot-zoom-lightbox'))
        self.assertIsNotNone(detail.select_one('#gallery-hotspot-prev'))
        self.assertIsNotNone(detail.select_one('#gallery-hotspot-next'))
        self.assertIsNotNone(detail.select_one('#gallery-lightbox-hotspots'))
        thumb = detail.select_one('.thumbnail-btn[data-highlights]')
        payload = json.loads(thumb['data-highlights'])
        self.assertEqual([item['label'] for item in payload], ['Alloy Wheels', 'Door Ding'])
        self.assertEqual(payload[0]['photo_index'], 3)
        self.assertEqual(payload[1]['source'], 'manual')

        css = (Path(self.app.static_folder) / 'css' / 'main.css').read_text()
        js = (Path(self.app.static_folder) / 'js' / 'main.js').read_text()
        self.assertIn('.gallery-hotspot-zoom-frame', css)
        self.assertIn('backdrop-filter: blur(10px);', css)
        self.assertIn('rgba(247, 248, 251, 0.84)', css)
        card = detail.select_one('#gallery-hotspot-card')
        stage = detail.select_one('#gallery-stage')
        self.assertIsNotNone(card)
        self.assertIsNotNone(stage)
        self.assertEqual(card.parent.get('id'), 'gallery-stage')
        self.assertIn('positionZoom', js)
        self.assertIn('openZoomLightbox', js)
        self.assertIn('stepHotspot', js)
        self.assertIn('gallery-lightbox-hotspots', js)

    def test_model_review_video_is_last_gallery_item_only_when_verified(self):
        matched = Vehicle.query.filter_by(slug='theme-car').one()
        matched.year = 2016
        matched.make = 'Kia'
        matched.model = 'Forte'
        db.session.add(VehicleImage(
            vehicle_id=matched.id,
            filename='theme-car.jpg',
            is_primary=True,
            order_index=0,
        ))
        unmatched = Vehicle(
            slug='unreviewed-car', year=1999, make='Geo', model='Metro',
            price=1900, mileage=180000, status='available',
        )
        db.session.add(unmatched)
        db.session.flush()
        db.session.add(VehicleImage(
            vehicle_id=unmatched.id,
            filename='unreviewed-car.jpg',
            is_primary=True,
            order_index=0,
        ))
        db.session.commit()

        detail = self.page('/inventory/theme-car')
        thumbs = detail.select('#gallery-thumbs .thumbnail-btn')
        self.assertGreaterEqual(len(thumbs), 2)
        review = thumbs[-1]
        self.assertEqual(review.get('data-kind'), 'review')
        self.assertNotEqual(thumbs[0].get('data-kind'), 'review')
        self.assertIn('youtube-nocookie.com/embed/IoYebZDP07U', review['data-embed'])
        self.assertIn('Not a review of this specific vehicle.', detail.get_text(' ', strip=True))
        self.assertIsNotNone(detail.select_one('#gallery-review[hidden]'))
        self.assertIsNotNone(detail.select_one('#gallery-review-note[hidden]'))

        missing = self.page('/inventory/unreviewed-car')
        self.assertIsNone(missing.select_one('[data-kind="review"]'))
        self.assertIsNone(missing.select_one('#gallery-review'))

    def test_fullscreen_gallery_blocks_page_swipe_while_preserving_pinch_zoom(self):
        css = (Path(self.app.static_folder) / 'css' / 'main.css').read_text()
        self.assertIn('.gallery-lightbox-stage {', css)
        self.assertIn('touch-action: pinch-zoom;', css)
        self.assertIn('body.gallery-lightbox-open {', css)
        self.assertIn('overflow: hidden;', css)
        self.assertIn('overscroll-behavior: none;', css)

    def test_theme_contains_motion_and_focus_accessibility_rules(self):
        css = (Path(self.app.static_folder) / 'css' / 'public.css').read_text()
        self.assertIn('@media (prefers-reduced-motion: reduce)', css)
        self.assertIn(':focus-visible', css)
        self.assertIn('@keyframes low-mileage-pop', css)
        self.assertIn('@keyframes low-mileage-inline-pop', css)
        self.assertIn('@keyframes low-mileage-shine', css)
        self.assertIn('@keyframes site-motion-enter', css)
        self.assertIn('translate3d(590%, 0, 0)', css)
        self.assertNotIn('@keyframes low-mileage-colors', css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        html = self.page('/inventory/theme-car')
        self.assertIsNotNone(html.select_one('.js-favorite-btn[data-slug="theme-car"]'))
        self.assertIsNotNone(html.select_one('button[data-bs-target="#contactModal"]'))
        self.assertIsNotNone(html.select_one('.mobile-cta-bar a[href^="tel:"]'))
        vehicle_text = html.select_one('.mobile-cta-bar a[href^="sms:"]')
        self.assertIn('Text About This Car', vehicle_text.get_text(' ', strip=True))
        self.assertIn('Toyota%20Camry', vehicle_text['href'])


if __name__ == '__main__':
    unittest.main()
