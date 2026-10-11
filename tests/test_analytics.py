import json
import unittest
import uuid
from datetime import datetime, timedelta

from config import TestingConfig
from app import create_app, db
from app.analytics import build_acquisition_report
from app.models import AnalyticsEvent, PageView, User, utcnow


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
        self.assertIn('Source-to-inquiry performance', html)
        self.assertIn('Converting sessions', html)
        self.assertIn('(direct) / (none)', html)

    def _page(self, visitor_id, session_id, created_at, **fields):
        values = {
            'visitor_id': visitor_id, 'session_id': session_id,
            'created_at': created_at, 'path': '/', 'page_type': 'home',
            'device_type': 'desktop',
        }
        values.update(fields)
        page = PageView(**values)
        db.session.add(page)
        db.session.flush()
        return page

    def _event(self, page, name, created_at, **fields):
        values = {
            'visitor_id': page.visitor_id, 'session_id': page.session_id,
            'event_name': name, 'created_at': created_at,
            'path': page.path, 'page_type': page.page_type,
        }
        values.update(fields)
        db.session.add(AnalyticsEvent(**values))
        db.session.flush()

    def test_acquisition_later_page_keeps_landing_credit_and_deduplicates_sessions(self):
        now = datetime(2026, 1, 10, 12)
        visitor, session = self._ids()
        self._page(visitor, session, now, utm_source='google', utm_medium='cpc',
                   utm_campaign='winter')
        contact = self._page(visitor, session, now + timedelta(minutes=1),
                             page_type='contact', path='/contact',
                             utm_source='facebook', utm_medium='social',
                             utm_campaign='later-tag')
        for name in ('click_to_call', 'click_to_call', 'click_to_sms',
                     'click_to_email', 'generate_lead'):
            self._event(contact, name, now + timedelta(minutes=2))
        self._page(*self._ids(), now, utm_source='google', utm_medium='cpc',
                   utm_campaign='winter')
        report = build_acquisition_report(now, now + timedelta(days=1))
        self.assertEqual(report['rows'], [{
            'source': 'google', 'medium': 'cpc', 'campaign': 'winter',
            'sessions': 2, 'converting_sessions': 1, 'conversion_rate': 50.0,
            'calls': 2, 'texts': 1, 'emails': 1, 'leads': 1,
        }])
        self.assertEqual(report['totals']['conversion_rate'], 50.0)

    def test_acquisition_filters_do_not_replace_original_landing(self):
        now = datetime(2026, 1, 10, 12)
        visitor, session = self._ids()
        landing = self._page(visitor, session, now - timedelta(days=1),
                             utm_source='newsletter', utm_medium='email',
                             device_type='mobile')
        contact = self._page(visitor, session, now, page_type='contact',
                             device_type='mobile', path='/contact')
        self._event(contact, 'generate_lead', now)
        self._event(landing, 'click_to_call', now)
        self._event(contact, 'click_to_sms', now - timedelta(seconds=1))
        self._event(contact, 'click_to_email', now + timedelta(days=2))
        desktop = self._page(*self._ids(), now, page_type='contact')
        self._event(desktop, 'generate_lead', now)
        report = build_acquisition_report(
            now, now + timedelta(days=1), page_type='contact', device='mobile',
        )
        self.assertEqual(len(report['rows']), 1)
        row = report['rows'][0]
        self.assertEqual((row['source'], row['medium']), ('newsletter', 'email'))
        self.assertEqual(row['sessions'], 1)
        self.assertEqual(row['leads'], 1)
        self.assertEqual((row['calls'], row['texts'], row['emails']), (0, 0, 0))
        self.assertEqual(row['conversion_rate'], 100.0)

    def test_acquisition_matches_both_identity_fields_and_excludes_bots(self):
        now = datetime(2026, 1, 10, 12)
        visitor, session = self._ids()
        page = self._page(visitor, session, now, utm_source='real')
        other_visitor, other_session = self._ids()
        self._event(page, 'generate_lead', now, visitor_id=other_visitor)
        self._event(page, 'generate_lead', now, session_id=other_session)
        bot = self._page(*self._ids(), now, device_type='bot', utm_source='bot')
        self._event(bot, 'click_to_call', now)
        self._event(page, 'gallery_engagement', now)
        # A colliding session ID on a different visitor remains a separate session.
        other = self._page(other_visitor, session, now, utm_source='other')
        self._event(other, 'click_to_call', now)
        report = build_acquisition_report(now, now + timedelta(days=1))
        rows = {row['source']: row for row in report['rows']}
        self.assertEqual(set(rows), {'real', 'other'})
        self.assertEqual(rows['real']['converting_sessions'], 0)
        self.assertEqual(rows['real']['leads'], 0)
        self.assertEqual(rows['real']['calls'], 0)
        self.assertEqual(rows['other']['calls'], 1)
        self.assertEqual(rows['other']['leads'], 1)
        self.assertEqual(report['totals']['sessions'], 2)

    def test_acquisition_source_fallbacks_and_medium_campaign_separation(self):
        now = datetime(2026, 1, 10, 12)
        fields = [
            {'gclid': 'google-click'},
            {'fbclid': 'facebook-click'},
            {'referrer_host': 'example.org'},
            {'referrer_host': 'localhost'},
            {},
            {'utm_source': 'google', 'utm_medium': 'email', 'utm_campaign': 'sale',
             'gclid': 'google-click'},
            {'utm_source': 'google', 'utm_medium': 'social', 'utm_campaign': 'sale'},
            {'utm_source': 'google', 'utm_medium': 'email', 'utm_campaign': 'other'},
        ]
        for values in fields:
            self._page(*self._ids(), now, **values)
        report = build_acquisition_report(now, now + timedelta(days=1))
        rows = {(row['source'], row['medium'], row['campaign']): row
                for row in report['rows']}
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[('(direct)', '(none)', '(not set)')]['sessions'], 2)
        for key in (
            ('google', 'cpc', '(not set)'),
            ('facebook', '(not set)', '(not set)'),
            ('example.org', 'referral', '(not set)'),
            ('google', 'email', 'sale'),
            ('google', 'social', 'sale'),
            ('google', 'email', 'other'),
        ):
            self.assertEqual(rows[key]['sessions'], 1)
        self.assertEqual(report['totals']['sessions'], 8)
        self.assertEqual(report['totals']['conversion_rate'], 0.0)

    def test_acquisition_stable_landing_order_and_event_time_bounds(self):
        now = datetime(2026, 1, 10, 12)
        visitor, session = self._ids()
        first = self._page(visitor, session, now, utm_source='first')
        self._page(visitor, session, now, utm_source='second')
        self._event(first, 'click_to_call', now - timedelta(seconds=1))
        self._event(first, 'click_to_sms', now + timedelta(days=1))
        self._event(first, 'generate_lead', now + timedelta(days=1, seconds=1))
        report = build_acquisition_report(now - timedelta(days=1), now + timedelta(days=1))
        self.assertEqual(report['rows'][0]['source'], 'first')
        self.assertEqual(report['totals']['calls'], 0)
        self.assertEqual(report['totals']['texts'], 1)
        self.assertEqual(report['totals']['leads'], 0)

    def test_acquisition_empty_report(self):
        now = datetime(2026, 1, 10, 12)
        report = build_acquisition_report(now, now + timedelta(days=1))
        self.assertEqual(report['rows'], [])
        self.assertEqual(report['totals'], {
            'sessions': 0, 'converting_sessions': 0, 'conversion_rate': 0.0,
            'calls': 0, 'texts': 0, 'emails': 0, 'leads': 0,
        })

    def test_admin_renders_filtered_acquisition_with_totals_and_escaped_tags(self):
        now = utcnow() - timedelta(minutes=5)
        visitor, session = self._ids()
        self._page(visitor, session, now, utm_source='newsletter',
                   utm_medium='email', utm_campaign='<script>sale</script>',
                   device_type='mobile')
        contact = self._page(visitor, session, now + timedelta(minutes=1),
                             page_type='contact', path='/contact', device_type='mobile')
        self._event(contact, 'generate_lead', now + timedelta(minutes=2))
        db.session.commit()
        self._login()
        response = self.client.get('/admin/analytics?page_type=contact&device=mobile')
        self.assertEqual(response.status_code, 200)
        report_html = response.get_data(as_text=True).split(
            'Source-to-inquiry performance', 1,
        )[1].split('Top pages', 1)[0]
        self.assertIn('newsletter / email', report_html)
        self.assertIn('&lt;script&gt;sale&lt;/script&gt;', report_html)
        self.assertNotIn('<script>sale</script>', report_html)
        self.assertEqual(report_html.count('100.0%'), 2)
        self.assertIn('Total', report_html)
        self.assertIn('Inquiries are limited to the selected page type.', report_html)


if __name__ == '__main__':
    unittest.main()
