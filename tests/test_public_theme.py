import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle
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
        self.assertEqual(manifest['theme_color'], '#25282c')
        self.assertEqual(manifest['background_color'], '#faf8f4')

    def test_theme_contains_motion_and_focus_accessibility_rules(self):
        css = (Path(self.app.static_folder) / 'css' / 'public.css').read_text()
        self.assertIn('@media (prefers-reduced-motion: reduce)', css)
        self.assertIn(':focus-visible', css)
        html = self.page('/inventory/theme-car')
        self.assertIsNotNone(html.select_one('.js-favorite-btn[data-slug="theme-car"]'))
        self.assertIsNotNone(html.select_one('button[data-bs-target="#contactModal"]'))
        self.assertIsNotNone(html.select_one('.mobile-cta-bar a[href^="tel:"]'))


if __name__ == '__main__':
    unittest.main()
