import json
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from app import create_app, db
from app.buyer_guides import BUYER_GUIDES
from app.models import BlogPost, Review, Vehicle, VehicleImage
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

    def test_homepage_title_targets_local_used_car_search_without_repeating_brand(self):
        html = self.page('/')
        title = html.title.get_text(strip=True)
        self.assertEqual(
            title,
            'Used Cars for Sale in Sanford, NC | Marshall Auto, LLC',
        )
        self.assertEqual(html.select_one('meta[property="og:title"]')['content'], title)
        self.assertEqual(html.select_one('meta[name="twitter:title"]')['content'], title)

    def test_homepage_preloads_hero_image_for_faster_paint(self):
        html = self.page('/')
        preload = html.select_one(
            'link[rel="preload"][as="image"][fetchpriority="high"]',
        )
        self.assertIsNotNone(preload)
        self.assertEqual(
            preload['href'],
            '/static/images/gallardo_cover.jpg',
        )

    def test_dealership_schema_omits_self_serving_rating_markup(self):
        db.session.add(Review(
            author_name='Test Customer',
            rating=5,
            content='A test review.',
            is_approved=True,
        ))
        db.session.commit()

        html = self.page('/')
        schemas = [
            json.loads(script.string)
            for script in html.select('script[type="application/ld+json"]')
        ]
        dealership = next(schema for schema in schemas if schema.get('@type') == 'AutoDealer')
        self.assertNotIn('aggregateRating', dealership)
        self.assertIn('A test review.', html.get_text())
        self.assertIn('Rated 5.0 out of 5 stars based on 1 reviews.', html.get_text())

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

    def publish_guides(self):
        result = self.app.test_cli_runner().invoke(args=['publish-buyer-guides'])
        self.assertEqual(result.exit_code, 0, result.output)
        return result

    def test_guides_publish_once_and_preserve_admin_changes(self):
        self.assertIn('Published 3', self.publish_guides().output)
        guide = BlogPost.query.filter_by(slug=BUYER_GUIDES[0]['slug']).one()
        published_at = guide.published_at
        guide.content = '<p>Edited by admin</p>'
        guide.is_published = False
        db.session.commit()
        updated_at = guide.updated_at
        self.assertIn('Published 0', self.publish_guides().output)
        db.session.refresh(guide)
        self.assertEqual(BlogPost.query.count(), 3)
        self.assertEqual(guide.content, '<p>Edited by admin</p>')
        self.assertFalse(guide.is_published)
        self.assertEqual(guide.published_at, published_at)
        self.assertEqual(guide.updated_at, updated_at)
        self.assertEqual(self.client.get(f'/blog/{guide.slug}').status_code, 404)
        self.assertNotIn(f'http://localhost/blog/{guide.slug}', self.sitemap_pages())
        for path in ('/inventory', '/service-area', '/inventory/test-car-0'):
            html = self.page(path)
            self.assertIsNone(html.select_one(f'a[href="/blog/{guide.slug}"]'))

    def test_guides_are_indexable_linked_and_have_real_schema_dates(self):
        self.publish_guides()
        sitemap = self.sitemap_pages()
        for guide in BlogPost.query.all():
            path = f'/blog/{guide.slug}'
            html = self.page(path)
            self.assertEqual(html.select_one('link[rel="canonical"]')['href'],
                             f'http://localhost{path}')
            self.assertEqual(html.select_one('meta[name="robots"]')['content'], 'index, follow')
            schema = next(json.loads(script.string)
                          for script in html.select('script[type="application/ld+json"]')
                          if json.loads(script.string).get('@type') == 'Article')
            self.assertEqual(schema['datePublished'], guide.published_at.isoformat() + 'Z')
            self.assertEqual(schema['dateModified'], guide.updated_at.isoformat() + 'Z')
            self.assertEqual(sitemap[f'http://localhost{path}'].text,
                             guide.updated_at.isoformat() + 'Z')
            self.assertTrue(html.select('.blog-post-content a[href^="https://"]'))
            for anchor in html.select('.blog-post-content a[href^="/"]'):
                self.assertEqual(self.client.get(anchor['href']).status_code, 200, anchor['href'])
            for source in ('/blog', '/inventory', '/service-area', '/inventory/test-car-0'):
                self.assertIsNotNone(self.page(source).select_one(f'a[href="{path}"]'))
            related = html.select_one('aside[aria-label="Related buying guides"]')
            self.assertIsNotNone(related)
            self.assertIsNone(related.select_one(f'a[href="{path}"]'))

    def test_guide_initialization_preserves_existing_draft_and_later_deletion(self):
        draft = BlogPost(slug=BUYER_GUIDES[0]['slug'], title='Existing draft',
                         content='<p>Original draft</p>', is_published=False)
        db.session.add(draft)
        db.session.commit()
        self.assertIn('Published 2', self.publish_guides().output)
        db.session.refresh(draft)
        self.assertEqual(draft.title, 'Existing draft')
        self.assertFalse(draft.is_published)
        self.assertIsNone(draft.published_at)
        db.session.delete(draft)
        db.session.commit()
        self.assertIn('Published 0', self.publish_guides().output)
        self.assertEqual(BlogPost.query.count(), 2)

    def test_article_dates_cover_space_and_draft_related_exclusion(self):
        self.publish_guides()
        guide = BlogPost.query.first()
        guide.cover_image_url = '/static/images/og-default.jpg'
        guide.published_at = datetime(2026, 1, 1)
        guide.updated_at = guide.published_at + timedelta(days=2)
        draft = BlogPost.query.filter(BlogPost.id != guide.id).first()
        draft.is_published = False
        db.session.commit()
        html = self.page(f'/blog/{guide.slug}')
        self.assertEqual(len(html.select('time')), 2)
        image = html.select_one('.ratio-16x9 img')
        self.assertEqual((image['width'], image['height']), ('1200', '675'))
        self.assertIn('object-fit-contain', image['class'])
        related = html.select_one('aside[aria-label="Related buying guides"]')
        self.assertIsNone(related.select_one(f'a[href="/blog/{draft.slug}"]'))

    def test_blog_meta_description_falls_back_to_plain_text_content(self):
        post = BlogPost(
            title='Local used-car buying tips',
            slug='local-used-car-buying-tips',
            content=(
                '<p>Compare vehicle history reports and inspection notes.</p>'
                '<p>Ask questions before you buy.</p>'
            ),
            is_published=True,
        )
        db.session.add(post)
        db.session.commit()

        html = self.page(f'/blog/{post.slug}')
        description = html.select_one('meta[name="description"]')['content']
        self.assertEqual(
            description,
            'Compare vehicle history reports and inspection notes. Ask questions before you buy.',
        )
        article = next(
            json.loads(script.string)
            for script in html.select('script[type="application/ld+json"]')
            if json.loads(script.string).get('@type') == 'Article'
        )
        self.assertEqual(article['description'], description)

    def test_blog_meta_description_prefers_editor_seo_description(self):
        post = BlogPost(
            title='Local used-car buying tips',
            slug='local-used-car-buying-tips-custom',
            excerpt='General excerpt.',
            seo_description='Editor-written search description.',
            content='<p>Article content.</p>',
            is_published=True,
        )
        db.session.add(post)
        db.session.commit()

        html = self.page(f'/blog/{post.slug}')
        self.assertEqual(
            html.select_one('meta[name="description"]')['content'],
            'Editor-written search description.',
        )

    def test_local_visit_details_use_configuration_and_actual_location(self):
        self.app.config.update(
            BUSINESS_ADDRESS='123 Test Street', BUSINESS_CITY='Sanford',
            BUSINESS_HOURS={'Monday': '11:00 AM - 5:00 PM'},
        )
        for prefix in ('used-cars', 'used-toyota', 'used-sedans'):
            html = self.page(f'/inventory/{prefix}-for-sale-in-raleigh-nc')
            text = html.get_text(' ', strip=True)
            self.assertIn('123 Test Street', text)
            self.assertIn('not in Raleigh', text)
            self.assertIn('11:00 AM - 5:00 PM', text)
            directions = html.select_one('a[href*="maps/dir/"]')
            self.assertIn('origin=Raleigh', directions['href'])
            self.assertIn('destination=123', directions['href'])
            self.assertIsNotNone(html.select_one('.vehicle-card'))
        self.assertNotIn('not in Sanford', self.page(
            '/inventory/used-cars-for-sale-in-sanford-nc').get_text())
        self.assertIsNone(self.page('/inventory').select_one('a[href*="origin="]'))

    def test_vehicle_hero_preload_and_slides_share_responsive_candidates(self):
        vehicle = Vehicle.query.filter_by(slug='test-car-0').one()
        db.session.add_all([
            VehicleImage(vehicle_id=vehicle.id, filename='hero.jpg', width=1200,
                         height=900, is_primary=True),
            VehicleImage(vehicle_id=vehicle.id, filename='small.jpg', width=250, height=200),
        ])
        db.session.commit()
        html = self.page('/inventory/test-car-0')
        hero = html.select_one('#main-gallery-image')
        preload = html.select_one('link[rel="preload"][as="image"][fetchpriority="high"]')
        self.assertEqual(hero['srcset'], preload['imagesrcset'])
        self.assertEqual(hero['sizes'], preload['imagesizes'])
        self.assertEqual(hero['src'], preload['href'])
        self.assertIn('hero_thumbnail.jpg 300w', hero['srcset'])
        self.assertIn('hero_card.jpg 600w', hero['srcset'])
        self.assertIn('hero.jpg 1200w', hero['srcset'])
        self.assertNotIn('hero_detail', hero['srcset'])
        thumbs = html.select('.thumbnail-btn')
        self.assertEqual(thumbs[0]['data-srcset'], hero['srcset'])
        self.assertEqual(thumbs[1]['data-srcset'], '')

    def test_vehicle_without_images_keeps_hero_fallback(self):
        html = self.page('/inventory/test-car-0')
        hero = html.select_one('#main-gallery-image')
        preload = html.select_one('link[rel="preload"][as="image"][fetchpriority="high"]')
        self.assertEqual(hero['src'], preload['href'])
        self.assertNotIn('srcset', hero.attrs)
        self.assertNotIn('imagesrcset', preload.attrs)