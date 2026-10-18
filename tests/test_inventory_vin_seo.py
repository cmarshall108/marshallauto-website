import json
import unittest
from datetime import datetime
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle
from config import TestingConfig


class InventoryVINSEOTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config['SITE_URL'] = 'https://dealer.example'
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        self.available = self.add_vehicle('available', '1HGCM82633A004352', 'available-accord')
        self.sold = self.add_vehicle('sold', '1HGCM82633A004353', 'sold-accord')
        self.pending = self.add_vehicle('pending', '1HGCM82633A004354', 'pending-accord')
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def add_vehicle(self, status, vin, slug):
        vehicle = Vehicle(
            year=2018, make='Honda', model='Accord', body_style='Sedan',
            price=15000, mileage=65000, status=status, vin=vin, slug=slug,
            description='Honda Accord with automatic transmission.',
            created_at=datetime(2026, 9, 1, 10), updated_at=datetime(2026, 9, 20, 14, 30),
        )
        db.session.add(vehicle)
        return vehicle

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('noindex', response.headers.get('X-Robots-Tag', ''))
        return BeautifulSoup(response.data, 'html.parser')

    def schema(self, html, schema_type):
        schemas = [json.loads(script.string) for script in html.select('script[type="application/ld+json"]')]
        return next(schema for schema in schemas if schema.get('@type') == schema_type)

    def test_all_vehicle_statuses_are_indexable_with_vins(self):
        for vehicle in (self.available, self.sold, self.pending):
            with self.subTest(status=vehicle.status):
                html = self.page(f'/inventory/{vehicle.slug}')
                self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'index, follow')
                self.assertIn(vehicle.vin, html.select_one('meta[name="description"]')['content'])
                self.assertIn(vehicle.vin, html.get_text())
                self.assertEqual(html.select_one('link[rel="canonical"]')['href'], f'https://dealer.example/inventory/{vehicle.slug}')
                self.assertEqual(self.schema(html, 'Car')['vehicleIdentificationNumber'], vehicle.vin)

    def test_detail_page_renders_extended_vehicle_specifications(self):
        self.available.mpg_city = 25
        self.available.mpg_highway = 30
        self.available.mpg_combined = 27
        self.available.doors = 4
        self.available.passenger_capacity = 5
        self.available.gvwr = '4,189 lbs'
        self.available.features = 'Blind Spot Monitor, Backup Camera'
        self.available.exterior_features = (
            '17-inch alloy wheels\nHeated side mirrors\nPhoto-observed (verify): Alloy Wheels'
        )
        self.available.interior_features = 'Heated front seats'
        self.available.entertainment_features = 'Apple CarPlay'
        self.available.mechanical_features = 'Six-speed automatic transmission'
        self.available.safety_features = 'Blind spot collision warning'
        self.available.additional_specifications = (
            'Engine displacement: 2.0 L / 122 cu in\nHorsepower: 147 hp'
        )
        self.available.vin_decode_data = json.dumps({
            'NHTSA': {
                'VIN': self.available.vin,
                'EngineHP': '147',
                'EngineManufacturer': 'HMC',
                'PlantCountry': 'SOUTH KOREA',
                'AirBagLocCurtain': '1st and 2nd Rows',
                'TrailerType': 'Not Applicable',
                'ErrorCode': '0',
                'ErrorText': '0 - VIN decoded clean.',
            },
            'EPA': {'id': '12345', 'year': '2018', 'city08': '25'},
        })
        db.session.commit()

        html = self.page('/inventory/available-accord')
        self.assertNotIn('Photo-observed', html.get_text())
        self.assertIn('Alloy Wheels', html.select_one('#exterior').get_text(' ', strip=True))
        spec_rows = {
            dt.get_text(' ', strip=True): dt.find_next_sibling('dd').get_text(' ', strip=True)
            for dt in html.select('.specs-table dt')
        }
        expected = {
            'Combined MPG': '27',
            'Doors': '4',
            'Passenger Capacity': '5',
            'Gross Vehicle Weight Rating': '4,189 lbs',
        }
        for label, value in expected.items():
            with self.subTest(value=value):
                self.assertEqual(spec_rows[label], value)
        additional = html.select_one('.vehicle-additional-specifications')
        additional_specs = {
            row.select_one('dt').get_text(' ', strip=True):
            row.select_one('dd').get_text(' ', strip=True)
            for row in additional.select('.vehicle-decoded-spec')
        }
        self.assertEqual(additional_specs['Engine displacement'], '2.0 L / 122 cu in')
        self.assertEqual(additional_specs['Horsepower'], '147 hp')
        vin_data = html.select_one('.vehicle-vin-decoded-data')
        self.assertIn('Full VIN decode data', vin_data.get_text(' ', strip=True))
        self.assertIn('Engine Manufacturer', vin_data.get_text(' ', strip=True))
        self.assertIn('HMC', vin_data.get_text(' ', strip=True))
        self.assertIn('SOUTH KOREA', vin_data.get_text(' ', strip=True))
        self.assertIn('EPA Fuel Economy', vin_data.get_text(' ', strip=True))
        self.assertIn('12345', vin_data.get_text(' ', strip=True))
        self.assertNotIn('Not Applicable', vin_data.get_text(' ', strip=True))
        self.assertEqual(len(vin_data.select('.vehicle-vin-data-row')), 8)
        self.assertEqual(
            [button.get_text(' ', strip=True) for button in html.select('#vehicleTabs .nav-link')],
            [
                'Exterior', 'Interior', 'Entertainment', 'Mechanical', 'Safety',
                'Tech Specs', 'Details',
            ],
        )
        self.assertEqual(
            [button.get_text(' ', strip=True) for button in html.select('#vehicleTabs .nav-link.active')],
            ['Details'],
        )
        self.assertEqual(
            [panel.get('id') for panel in html.select('#vehicleTabsContent .tab-pane.active')],
            ['details'],
        )
        for panel_id, expected_text in (
            ('#exterior', '17-inch alloy wheels'),
            ('#interior', 'Heated front seats'),
            ('#entertainment', 'Apple CarPlay'),
            ('#entertainment', 'Android Auto'),
            ('#mechanical', 'Six-speed automatic transmission'),
            ('#safety', 'Blind spot collision warning'),
            ('#details', 'VIN'),
        ):
            with self.subTest(panel=panel_id):
                self.assertIn(expected_text, html.select_one(panel_id).get_text(' ', strip=True))
        self.assertEqual(
            [feature.get_text(' ', strip=True) for feature in html.select('.vehicle-key-features .feature-tag')],
            ['Blind Spot Monitor', 'Backup Camera', 'Android Auto'],
        )
        self.assertEqual(
            len(html.select('.vehicle-key-features .feature-tag i.bi[aria-hidden="true"]')),
            3,
        )
        self.assertIn('Service History', html.select_one('#details').get_text())
        self.assertIn('CarFax History Report', html.select_one('#details').get_text())
        basic_info = {
            item.select_one('.vehicle-basic-info-label').get_text(' ', strip=True):
            item.select_one('.vehicle-basic-info-value').get_text(' ', strip=True)
            for item in html.select('.vehicle-basic-info-item')
        }
        self.assertEqual(basic_info['Exterior'], self.available.exterior_color or 'Not provided')
        self.assertEqual(basic_info['Mileage'], '65,000 mi')
        summary = self.client.get(
            '/api/vehicles-summary?slugs=available-accord',
        ).get_json()['vehicles'][0]
        self.assertEqual(summary['mpg_combined'], 27)
        self.assertEqual(summary['doors'], 4)
        self.assertEqual(summary['passenger_capacity'], 5)
        self.assertEqual(
            summary['features'],
            'Blind Spot Monitor, Backup Camera, Android Auto',
        )
        self.assertEqual(
            summary['entertainment_features'],
            'Apple CarPlay\nAndroid Auto',
        )
        self.assertEqual(summary['exterior_features'], self.available.exterior_features)
        self.assertEqual(summary['safety_features'], self.available.safety_features)
        self.assertEqual(summary['additional_specifications'], self.available.additional_specifications)

    def test_real_vins_have_indexable_vin_record_pages(self):
        for vehicle in (self.available, self.sold, self.pending):
            with self.subTest(status=vehicle.status):
                html = self.page(f'/vin/{vehicle.vin}')
                self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'index, follow')
                self.assertEqual(
                    html.select_one('link[rel="canonical"]')['href'],
                    f'https://dealer.example/vin/{vehicle.vin}',
                )
                self.assertIn(vehicle.vin, html.title.string)
                self.assertIn(vehicle.title, html.select_one('h1').get_text())
                self.assertIsNotNone(
                    html.select_one(f'a[href="/inventory/{vehicle.slug}"]')
                )
                self.assertEqual(
                    self.schema(html, 'Car')['vehicleIdentificationNumber'],
                    vehicle.vin,
                )
                item_page = self.schema(html, 'ItemPage')
                self.assertEqual(item_page['url'], f'https://dealer.example/vin/{vehicle.vin}')

    def test_vin_record_normalizes_case_and_rejects_unknown_or_invalid_vins(self):
        response = self.client.get(f'/vin/{self.available.vin.lower()}')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.headers['Location'], f'/vin/{self.available.vin}')

        for path in ('/vin/not-a-vin', '/vin/1HGCM82633A999999'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertIn('noindex', response.headers['X-Robots-Tag'])

    def test_vin_pages_are_not_linked_from_primary_shopping_pages(self):
        for path in ('/', '/inventory'):
            html = self.page(path)
            self.assertIsNone(html.select_one('a[href^="/vin/"]'))

    def test_unlisted_or_malformed_vehicle_vins_do_not_create_search_pages(self):
        coming_soon = self.add_vehicle(
            'available', '1HGCM82633A004355', 'coming-soon-accord',
        )
        coming_soon.is_coming_soon = True
        malformed = self.add_vehicle('sold', 'NOT-A-VALID-VIN', 'malformed-vin')
        db.session.commit()

        self.assertEqual(self.client.get(f'/vin/{coming_soon.vin}').status_code, 404)
        _, entries = self.sitemap_entries()
        self.assertNotIn(f'https://dealer.example/vin/{coming_soon.vin}', entries)
        self.assertNotIn(f'https://dealer.example/vin/{malformed.vin}', entries)

    def test_sold_metadata_overrides_stale_for_sale_copy(self):
        self.sold.seo_title = 'Honda Accord for Sale'
        self.sold.seo_description = 'Buy this car today.'
        db.session.commit()
        html = self.page('/inventory/sold-accord')
        self.assertIn('Sold', html.title.string)
        self.assertIn(self.sold.vin, html.title.string)
        self.assertNotIn('Buy this car today', html.select_one('meta[name="description"]')['content'])
        self.assertIn('Last listed price:', html.get_text())
        self.assertIsNotNone(html.select_one('main a[href="/inventory"]'))
        self.assertEqual(self.schema(html, 'Car')['offers']['availability'], 'https://schema.org/SoldOut')

    def test_real_dates_and_linked_vehicle_schema(self):
        html = self.page('/inventory/available-accord')
        webpage = self.schema(html, 'WebPage')
        car = self.schema(html, 'Car')
        self.assertEqual(webpage['datePublished'], '2026-09-01T10:00:00Z')
        self.assertEqual(webpage['dateModified'], '2026-09-20T14:30:00Z')
        self.assertEqual(webpage['mainEntity']['@id'], car['@id'])
        self.assertEqual(car['offers']['availability'], 'https://schema.org/InStock')
        self.assertNotIn('priceValidUntil', car['offers'])

    def sitemap_entries(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        namespace = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        root = ElementTree.fromstring(response.data)
        entries = {
            entry.find('sm:loc', namespace).text: entry.find('sm:lastmod', namespace)
            for entry in root.findall('sm:url', namespace)
        }
        return response, entries

    def test_sitemap_contains_every_vehicle_and_honest_dates(self):
        response, entries = self.sitemap_entries()
        for vehicle in (self.available, self.sold, self.pending):
            self.assertEqual(entries[f'https://dealer.example/inventory/{vehicle.slug}'].text, '2026-09-20T14:30:00Z')
            self.assertEqual(entries[f'https://dealer.example/vin/{vehicle.vin}'].text, '2026-09-20T14:30:00Z')
        self.assertIn('https://dealer.example/inventory/history', entries)
        self.assertIsNone(entries['https://dealer.example/about'])
        self.assertEqual(response.data, self.client.get('/sitemap.xml').data)

    def test_sold_update_changes_inventory_sitemap_freshness(self):
        self.sold.updated_at = datetime(2026, 10, 1, 12)
        db.session.commit()
        _, entries = self.sitemap_entries()
        self.assertEqual(entries['https://dealer.example/inventory'].text, '2026-10-01T12:00:00Z')

    def test_archive_has_crawlable_vin_links_and_current_inventory_link(self):
        html = self.page('/inventory/history')
        self.assertIn(self.sold.vin, html.get_text())
        self.assertIn(self.pending.vin, html.get_text())
        self.assertIsNotNone(html.select_one('a[href="/inventory/sold-accord"]'))
        self.assertIsNotNone(html.select_one('a[href="/inventory"]'))

    def test_archive_pagination_has_distinct_canonical(self):
        for number in range(24):
            self.add_vehicle('sold', f'ARCHIVE{number:010d}', f'archive-{number}')
        db.session.commit()
        first = self.page('/inventory/history')
        self.assertIsNotNone(first.select_one('a[href="/inventory/history?page=2"]'))
        second = self.page('/inventory/history?page=2&utm_source=google')
        self.assertEqual(second.select_one('link[rel="canonical"]')['href'], 'https://dealer.example/inventory/history?page=2')

    def test_local_inventory_links_archive_but_only_lists_available_stock(self):
        html = self.page('/inventory/used-cars-for-sale-in-sanford-nc')
        self.assertIn('Sanford', html.select_one('meta[name="description"]')['content'])
        self.assertEqual(html.select_one('link[rel="canonical"]')['href'], 'https://dealer.example/inventory/used-cars-for-sale-in-sanford-nc')
        self.assertIsNotNone(html.select_one('a[href="/inventory/history"]'))
        self.assertIsNotNone(html.select_one('a[href="/inventory/available-accord"]'))
        self.assertIsNone(html.select_one('a[href="/inventory/sold-accord"]'))

    def test_local_inventory_pagination_preserves_page_canonical(self):
        for number in range(12):
            self.add_vehicle('available', f'CURRENT{number:010d}', f'current-{number}')
        db.session.commit()
        html = self.page('/inventory/used-cars-for-sale-in-sanford-nc?page=2')
        self.assertEqual(html.select_one('link[rel="canonical"]')['href'], 'https://dealer.example/inventory/used-cars-for-sale-in-sanford-nc?page=2')

    def test_feed_remains_available_only(self):
        response = self.client.get('/feeds/vehicles.xml')
        self.assertEqual(response.status_code, 200)
        root = ElementTree.fromstring(response.data)
        links = [item.find('link').text for item in root.findall('./channel/item')]
        self.assertTrue(any('/inventory/available-accord' in link for link in links))
        self.assertFalse(any('/inventory/sold-accord' in link for link in links))
        self.assertFalse(any('/inventory/pending-accord' in link for link in links))

    def test_robots_advertises_configured_sitemap(self):
        response = self.client.get('/robots.txt')
        self.assertIn('Sitemap: https://dealer.example/sitemap.xml', response.get_data(as_text=True))
        self.assertIn('Allow: /vin/', response.get_data(as_text=True))

    def test_missing_vehicle_is_not_indexable(self):
        response = self.client.get('/inventory/missing')
        self.assertEqual(response.status_code, 404)
        self.assertIn('noindex', response.headers['X-Robots-Tag'])