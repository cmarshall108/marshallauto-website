import json
import unittest
from unittest import mock

from bs4 import BeautifulSoup

from app import create_app, db
from app.google_reviews import (
    DEFAULT_BUSINESS_URL,
    google_reviews_summary,
    sync_google_reviews_from_places,
    upsert_google_review,
)
from app.models import Review, SiteSetting, Vehicle
from config import TestingConfig


class GoogleReviewsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        SiteSetting.set('google_business_url', DEFAULT_BUSINESS_URL)
        SiteSetting.set('google_rating', '4.9')
        SiteSetting.set('google_review_count', '12')
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_homepage_shows_google_reviews_section(self):
        upsert_google_review(
            author_name='Jordan G',
            rating=5,
            content='Great experience buying a used car in Sanford. Honest pricing.',
            featured=True,
        )
        db.session.commit()
        html = BeautifulSoup(self.client.get('/').data, 'html.parser')
        section = html.select_one('#customer-reviews')
        self.assertIsNotNone(section)
        text = section.get_text(' ', strip=True)
        self.assertIn('Jordan G', text)
        self.assertIn('4.9', text)
        self.assertIsNotNone(section.select_one(f'a[href="{DEFAULT_BUSINESS_URL}"]'))
        self.assertIsNotNone(section.select_one('.review-source-google'))

    def test_vehicle_page_links_to_google_reviews(self):
        vehicle = Vehicle(
            slug='google-review-car', year=2017, make='Honda', model='Civic',
            body_style='Sedan', price=9000, mileage=80000, status='available',
        )
        db.session.add(vehicle)
        upsert_google_review(
            author_name='Sam R',
            rating=5,
            content='Smooth process and clear communication on title history.',
            featured=True,
        )
        db.session.commit()
        html = BeautifulSoup(self.client.get('/inventory/google-review-car').data, 'html.parser')
        trust = html.select_one('.vehicle-trust-reviews')
        self.assertIsNotNone(trust)
        self.assertIn('Sam R', trust.get_text())
        self.assertIsNotNone(trust.select_one(f'a[href="{DEFAULT_BUSINESS_URL}"]'))

    def test_footer_includes_google_profile_link(self):
        html = BeautifulSoup(self.client.get('/').data, 'html.parser')
        link = html.select_one(f'footer a[href="{DEFAULT_BUSINESS_URL}"]')
        self.assertIsNotNone(link)
        self.assertEqual(link.get('aria-label', '').lower(), 'google')

    def test_upsert_is_idempotent(self):
        first, created = upsert_google_review(
            author_name='Casey', rating=5, content='Would buy again from Marshall Auto.',
        )
        db.session.commit()
        self.assertTrue(created)
        second, changed = upsert_google_review(
            author_name='Casey', rating=5, content='Would buy again from Marshall Auto.',
        )
        db.session.commit()
        self.assertFalse(changed)
        self.assertEqual(first.id, second.id)
        self.assertEqual(Review.query.filter_by(source='Google').count(), 1)

    def test_sync_from_places_mocked(self):
        SiteSetting.set('google_place_id', 'ChIJ-test-place')
        db.session.commit()
        self.app.config['GOOGLE_PLACES_API_KEY'] = 'test-key'
        payload = {
            'status': 'OK',
            'result': {
                'name': 'Marshall Auto, LLC',
                'rating': 4.8,
                'user_ratings_total': 27,
                'url': 'https://maps.google.com/?cid=123',
                'reviews': [
                    {
                        'author_name': 'Taylor',
                        'rating': 5,
                        'text': 'Friendly staff and fair out-the-door pricing.',
                    }
                ],
            },
        }
        fake_body = json.dumps(payload).encode('utf-8')

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return fake_body

        with mock.patch('urllib.request.urlopen', return_value=FakeResponse()):
            result = sync_google_reviews_from_places()
        self.assertEqual(result['imported_or_updated'], 1)
        self.assertEqual(result['rating'], 4.8)
        self.assertEqual(result['count'], 27)
        review = Review.query.filter_by(source='Google').one()
        self.assertIn('out-the-door', review.content)
        summary = google_reviews_summary()
        self.assertEqual(summary['rating'], 4.8)
        self.assertEqual(summary['count'], 27)
        self.assertTrue(summary['has_google_reviews'])


if __name__ == '__main__':
    unittest.main()
