import unittest

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import Vehicle
from config import TestingConfig


class EngagementRailsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()
        db.session.add(Vehicle(
            slug='engage-camry', year=2021, make='Toyota', model='Camry',
            price=15000, mileage=40000, status='available', body_style='Sedan',
        ))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def html(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return BeautifulSoup(response.data, 'html.parser')

    def test_home_and_inventory_include_recently_viewed_mount(self):
        for path in ('/', '/inventory'):
            page = self.html(path)
            rail = page.select_one('#recently-viewed-rail')
            self.assertIsNotNone(rail)
            self.assertIn('d-none', rail.get('class', []))
            self.assertIsNotNone(page.select_one('#recently-viewed-grid'))

    def test_vehicle_detail_excludes_current_slug_and_has_visit_ctas(self):
        page = self.html('/inventory/engage-camry')
        rail = page.select_one('#recently-viewed-rail')
        self.assertEqual(rail.get('data-exclude-slug'), 'engage-camry')
        prompt = page.select_one('#vehicle-visit-prompt')
        self.assertIsNotNone(prompt)
        self.assertIsNotNone(page.select_one('#visit-prompt-chat'))
        self.assertTrue(page.select_one('a[href^="tel:"]'))
        actions_text = prompt.get_text(' ', strip=True)
        self.assertIn('Call', actions_text)
        self.assertIn('Text', actions_text)

    def test_engagement_nudge_markup_and_script_are_present(self):
        page = self.html('/')
        self.assertIsNotNone(page.select_one('#engagement-nudge'))
        self.assertIsNotNone(page.select_one('#engagement-nudge-chat'))
        scripts = ' '.join(s.get('src') or '' for s in page.select('script[src]'))
        self.assertIn('engagement-nudge.js', scripts)


if __name__ == '__main__':
    unittest.main()
