import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import User
from config import TestingConfig


class AdminThemeTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        user = User(username='theme-agent')
        user.set_password('theme-test-password')
        db.session.add(user)
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

    def assert_theme(self, html):
        self.assertIn('admin-theme', html.body['class'])
        self.assertNotIn('public-site', html.body['class'])
        styles = [link['href'].split('?')[0] for link in html.select('link[rel="stylesheet"]')]
        self.assertLess(styles.index('/static/css/main.css'), styles.index('/static/css/admin.css'))
        self.assertNotIn('/static/css/public.css', styles)
        self.assertEqual(html.select_one('meta[name="theme-color"]')['content'], '#25282c')

    def test_login_uses_theme_and_preserves_authentication_fields(self):
        self.app.config['WTF_CSRF_ENABLED'] = True
        html = self.page('/admin/login')
        self.assert_theme(html)
        for name in ('username', 'password', 'csrf_token'):
            self.assertIsNotNone(html.select_one(f'[name="{name}"]'))
        self.assertIn('login-page', html.body['class'])

    def test_staff_pages_share_theme_and_keep_chat_and_navigation_controls(self):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            response = self.client.post('/admin/login', data={
                'username': 'theme-agent', 'password': 'theme-test-password',
            })
        self.assertEqual(response.status_code, 302)
        for path in (
            '/admin/dashboard', '/admin/vehicles', '/admin/vehicles/new',
            '/admin/leads', '/admin/settings', '/admin/analytics', '/admin/live-chat',
            '/admin/service-records', '/admin/carfax-reports', '/admin/test-drives',
            '/admin/reviews', '/admin/blog',
        ):
            with self.subTest(path=path):
                html = self.page(path)
                self.assert_theme(html)
                self.assertIn('admin-wrapper', html.body['class'])
                self.assertIsNotNone(html.select_one('.admin-mobile-nav #adminMobileNav'))
                self.assertIsNotNone(html.select_one('#admin-chat-sounds'))
                self.assertIsNotNone(html.select_one('a[href="/admin/live-chat"]'))

    def test_theme_is_scoped_and_matches_public_palette_and_accessibility(self):
        folder = Path(self.app.static_folder) / 'css'
        admin = (folder / 'admin.css').read_text()
        public = (folder / 'public.css').read_text()
        for token in ('--dark: #25282c', '--light: #faf8f4', '--accent: #d6bc90',
                      '--surface: #fffefa', '--primary: #353b42'):
            self.assertIn(token, admin)
            self.assertIn(token, public)
        for rule in (':focus-visible', 'prefers-reduced-motion: reduce', 'min-height: 44px',
                     '.admin-theme .btn-secondary:hover', '.admin-theme .table-primary'):
            self.assertIn(rule, admin)
        html = self.page('/')
        self.assertNotIn('admin-theme', html.body['class'])
        self.assertIsNone(html.select_one('link[href*="/admin.css"]'))
        with self.client.get('/static/css/admin.css') as response:
            self.assertEqual(response.status_code, 200)


if __name__ == '__main__':
    unittest.main()
