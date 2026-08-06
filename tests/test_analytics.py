import json
import unittest
import uuid

from config import TestingConfig
from app import create_app, db
from app.models import AnalyticsEvent, PageView, User


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        admin = User(username='admin')
        admin.set_password('test-password')
        db.session.add(admin)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _ids(self):
        return str(uuid.uuid4()), str(uuid.uuid4())

    def _login(self):
        return self.client.post(
            '/admin/login',
            data={
                'username': 'admin',
                'password': 'test-password',
                'remember': False,
                'submit': 'Log In',
            },
            follow_redirects=False,
        )

    def test_collect_pageview_and_update(self):
        visitor_id, session_id = self._ids()
        res = self.client.post(
            '/api/analytics/collect',
            data=json.dumps({
                'type': 'pageview',
                'visitor_id': visitor_id,
                'session_id': session_id,
                'path': '/inventory',
                'page_type': 'inventory',
                'page_title': 'Inventory',
                'utm_source': 'google',
                'utm_campaign': 'spring',
                'language': 'en-US',
                'screen_width': 1440,
                'screen_height': 900,
                'user_agent': (
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        body = res.get_json()
        self.assertTrue(body.get('ok'))
        self.assertIn('page_view_id', body)

        pv = db.session.get(PageView, body['page_view_id'])
        self.assertIsNotNone(pv)
        self.assertEqual(pv.path, '/inventory')
        self.assertEqual(pv.page_type, 'inventory')
        self.assertEqual(pv.utm_source, 'google')
        self.assertEqual(pv.device_type, 'desktop')
        self.assertEqual(pv.browser, 'Chrome')

        res2 = self.client.post(
            '/api/analytics/collect',
            data=json.dumps({
                'type': 'update',
                'visitor_id': visitor_id,
                'session_id': session_id,
                'page_view_id': pv.id,
                'duration_seconds': 42,
                'scroll_depth_pct': 75,
                'is_engaged': True,
                'heartbeat': True,
            }),
            content_type='application/json',
        )
        self.assertEqual(res2.status_code, 200)
        db.session.refresh(pv)
        self.assertEqual(pv.duration_seconds, 42)
        self.assertEqual(pv.scroll_depth_pct, 75)
        self.assertTrue(pv.is_engaged)
        self.assertFalse(pv.is_bounce)
        self.assertEqual(pv.heartbeat_count, 1)

    def test_collect_event(self):
        visitor_id, session_id = self._ids()
        res = self.client.post(
            '/api/analytics/collect',
            data=json.dumps({
                'type': 'event',
                'visitor_id': visitor_id,
                'session_id': session_id,
                'event_name': 'click_to_call',
                'path': '/',
                'page_type': 'home',
                'label': 'Call Now',
                'meta': {'cta_type': 'tel'},
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(AnalyticsEvent.query.count(), 1)
        ev = AnalyticsEvent.query.first()
        self.assertEqual(ev.event_name, 'click_to_call')
        self.assertEqual(ev.event_category, 'conversion')

    def test_rejects_admin_path(self):
        visitor_id, session_id = self._ids()
        res = self.client.post(
            '/api/analytics/collect',
            data=json.dumps({
                'type': 'pageview',
                'visitor_id': visitor_id,
                'session_id': session_id,
                'path': '/admin/dashboard',
            }),
            content_type='application/json',
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(PageView.query.count(), 0)

    def test_admin_analytics_requires_login(self):
        res = self.client.get('/admin/analytics')
        self.assertIn(res.status_code, (302, 401))

    def test_admin_analytics_page(self):
        visitor_id, session_id = self._ids()
        self.client.post(
            '/api/analytics/collect',
            data=json.dumps({
                'type': 'pageview',
                'visitor_id': visitor_id,
                'session_id': session_id,
                'path': '/',
                'page_type': 'home',
                'user_agent': (
                    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                    'AppleWebKit/605.1.15'
                ),
            }),
            content_type='application/json',
        )

        login_res = self._login()
        self.assertIn(login_res.status_code, (302, 200))

        res = self.client.get('/admin/analytics')
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn('Website Analytics', html)
        self.assertIn('Page Views', html)
        self.assertIn('Vehicle interest', html)


if __name__ == '__main__':
    unittest.main()
