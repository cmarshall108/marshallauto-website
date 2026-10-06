import json
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle
from config import TestingConfig


class SeoTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['SITE_URL'] = 'http://localhost'
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        for number in range(13):
            db.session.add(Vehicle(
                slug=f'test-car-{number}', year=2020, make='Toyota', model='Camry',
                price=12000, mileage=50000, body_style='Sedan', condition='used',
                status='available', title_status='clean',
            ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, 'html.parser')

    def test_inventory_pagination_canonical_keeps_page_not_tracking(self):
        html = self.page('/inventory?page=2&utm_source=test')
        self.assertEqual(html.select_one('link[rel="canonical"]')['href'],
                         'http://localhost/inventory?page=2')
        self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'index, follow')
        html = self.page('/inventory?page=1')
        self.assertEqual(html.select_one('link[rel="canonical"]')['href'], 'http://localhost/inventory')

    def test_out_of_range_pages_return_404(self):
        for query in ('page=3', 'page=9999', 'page=0', 'page=-1'):
            response = self.client.get(f'/inventory?{query}')
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.headers['X-Robots-Tag'], 'noindex, follow')

    def test_filters_and_sorts_are_noindex_on_every_page(self):
        for query in ('make=Toyota', 'make=Toyota&page=2', 'min_price=0', 'sort=price_asc'):
            html = self.page(f'/inventory?{query}')
            self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'noindex, follow')
            self.assertEqual(html.select_one('link[rel="canonical"]')['href'], 'http://localhost/inventory')

    def test_dedicated_landing_pagination_remains_indexable(self):
        path = '/inventory/used-cars-for-sale-in-sanford-nc'
        html = self.page(f'{path}?page=2')
        self.assertEqual(html.select_one('link[rel="canonical"]')['href'], f'http://localhost{path}?page=2')
        self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'index, follow')

    def test_empty_inventory_is_noindex(self):
        Vehicle.query.update({'status': 'sold'})
        db.session.commit()
        html = self.page('/inventory')
        self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'noindex, follow')

    def sitemap_pages(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        namespace = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        return {
            entry.find('sm:loc', namespace).text: entry.find('sm:lastmod', namespace)
            for entry in ET.fromstring(response.data).findall('sm:url', namespace)
        }

    def test_external_seo_urls_use_site_origin_behind_proxy(self):
        site_url = 'https://marshallautosanford.com'
        self.app.config['SITE_URL'] = site_url
        self.app.config['SERVER_NAME'] = '127.0.0.1:8080'
        proxy_headers = {
            'X-Forwarded-Host': '127.0.0.1:8080',
            'X-Forwarded-Proto': 'http',
        }

        sitemap = self.client.get('/sitemap.xml', headers=proxy_headers)
        namespace = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        locations = [
            entry.find('sm:loc', namespace).text
            for entry in ET.fromstring(sitemap.data).findall('sm:url', namespace)
        ]
        self.assertTrue(locations)
        self.assertTrue(all(url.startswith(f'{site_url}/') for url in locations))
        self.assertFalse(any('127.0.0.1' in url for url in locations))

        page = self.client.get('/inventory', headers=proxy_headers)
        html = BeautifulSoup(page.data, 'html.parser')
        self.assertEqual(
            html.select_one('link[rel="canonical"]')['href'],
            f'{site_url}/inventory',
        )
        robots = self.client.get('/robots.txt', headers=proxy_headers)
        self.assertIn(f'Sitemap: {site_url}/sitemap.xml', robots.get_data(as_text=True))

    def test_sitemap_dates_are_only_real_content_timestamps(self):
        vehicle = Vehicle.query.filter_by(slug='test-car-0').one()
        vehicle.updated_at = datetime(2026, 9, 1)
        db.session.commit()
        pages = self.sitemap_pages()
        self.assertEqual(pages['http://localhost/inventory/test-car-0'].text, '2026-09-01T00:00:00Z')
        latest_update = max(vehicle.updated_at for vehicle in Vehicle.query.all())
        self.assertEqual(pages['http://localhost/inventory'].text, latest_update.isoformat() + 'Z')
        for path in ('/', '/about', '/contact', '/service-area', '/blog'):
            self.assertIsNone(pages[f'http://localhost{path}'])

    def test_sitemap_lists_stocked_landings_and_retains_sold_vehicles(self):
        pages = self.sitemap_pages()
        self.assertFalse(any('rebuilt-title-cars' in url for url in pages))
        self.assertTrue(any('used-toyota-for-sale' in url for url in pages))
        self.assertTrue(any('used-sedans-for-sale' in url for url in pages))
        vehicle = Vehicle.query.filter_by(slug='test-car-0').one()
        vehicle.title_status = 'rebuilt'
        db.session.commit()
        self.assertTrue(any('rebuilt-title-cars' in url for url in self.sitemap_pages()))
        vehicle.status = 'sold'
        db.session.commit()
        pages = self.sitemap_pages()
        self.assertIn('http://localhost/inventory/test-car-0', pages)
        self.assertFalse(any('rebuilt-title-cars' in url for url in pages))
        Vehicle.query.delete()
        db.session.commit()
        self.assertFalse(any('/inventory' in url for url in self.sitemap_pages()))

    def test_sitemap_landings_are_indexable_and_self_canonical(self):
        for url in self.sitemap_pages():
            if '-for-sale-in-' not in url:
                continue
            html = self.page(url)
            self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'index, follow')
            self.assertEqual(html.select_one('link[rel="canonical"]')['href'], url)
            self.assertIsNotNone(html.select_one('.vehicle-card'))

    def test_stocked_landings_have_crawlable_internal_links(self):
        make_path = '/inventory/used-toyota-for-sale-in-sanford-nc'
        style_path = '/inventory/used-sedans-for-sale-in-sanford-nc'
        for path in ('/inventory', '/service-area'):
            html = self.page(path)
            for target in (make_path, style_path):
                self.assertIsNotNone(html.select_one(f'a[href="http://localhost{target}"]'))
            self.assertIsNone(html.select_one('a[href*="rebuilt-title-cars-for-sale-in-"]'))

    def test_vehicle_offer_has_no_invented_expiration(self):
        html = self.page('/inventory/test-car-0')
        schemas = [json.loads(script.string) for script in html.select('script[type="application/ld+json"]')]
        vehicle = next(schema for schema in schemas if schema.get('@type') == 'Car')
        self.assertNotIn('priceValidUntil', vehicle['offers'])
        self.assertEqual(vehicle['offers']['availability'], 'https://schema.org/InStock')