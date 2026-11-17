import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from app import create_app, db
from app.models import BlogPost, User
from app.utils import sanitize_blog_html
from config import TestingConfig


class BlogSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        admin = User(username='blog-admin', role=User.ROLE_ADMIN)
        admin.set_password('blog-admin-password')
        salesman = User(username='blog-sales', role=User.ROLE_SALESMAN)
        salesman.set_password('blog-sales-password')
        db.session.add_all([admin, salesman])
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.ctx.pop()

    def login(self, username, password, bypass_salesman_2fa=False):
        with patch('app.admin.rate_limit_exceeded', return_value=False):
            if bypass_salesman_2fa:
                with patch('app.admin.requires_two_factor', return_value=False), \
                        patch('app.two_factor.requires_two_factor', return_value=False):
                    return self.client.post('/admin/login', data={
                        'username': username, 'password': password,
                    })
            return self.client.post('/admin/login', data={
                'username': username, 'password': password,
            })

    def test_blog_content_is_sanitized_on_save_and_when_rendering_legacy_posts(self):
        self.login('blog-admin', 'blog-admin-password')
        response = self.client.post('/admin/blog/new', data={
            'title': 'Sanitized article',
            'content': (
                '<h2>Safe heading</h2><script>window.injected = true</script>'
                '<img src=x onerror="window.injected = true">'
                '<a href="javascript:alert(1)">unsafe link</a>'
            ),
            'is_published': 'y',
        })
        self.assertEqual(response.status_code, 302)
        saved = BlogPost.query.filter_by(title='Sanitized article').one()
        self.assertNotIn('<script', saved.content.lower())
        self.assertNotIn('onerror', saved.content.lower())
        self.assertNotIn('javascript:', saved.content.lower())
        self.assertIn('<h2>Safe heading</h2>', saved.content)

        legacy = BlogPost(
            title='Legacy article',
            slug='legacy-unsafe-article',
            content=(
                '<p>Safe text</p><script>window.legacyInjected = true</script>'
                '<img src=x onerror="alert(1)">'
            ),
            is_published=True,
        )
        db.session.add(legacy)
        db.session.commit()

        response = self.client.get('/blog/legacy-unsafe-article')
        self.assertEqual(response.status_code, 200)
        article = BeautifulSoup(response.data, 'html.parser').select_one('.blog-post-content')
        self.assertEqual(article.select('script, img'), [])
        self.assertIn('<p>Safe text</p>', str(article))

    def test_sanitizer_preserves_safe_markup_and_removes_unsafe_markup(self):
        sanitized = sanitize_blog_html(
            '<p><strong>Safe</strong> <a href="https://example.com">link</a></p>'
            '<svg onload="alert(1)"><script>alert(2)</script></svg>'
        )

        self.assertIn('<strong>Safe</strong>', sanitized)
        self.assertIn('href="https://example.com"', sanitized)
        self.assertNotIn('<script', sanitized.lower())
        self.assertNotIn('<svg', sanitized.lower())
        self.assertNotIn('onload', sanitized.lower())

    def test_salesman_cannot_view_or_change_blog_posts(self):
        post = BlogPost(
            title='Admin article', slug='admin-article', content='<p>Content</p>',
        )
        db.session.add(post)
        db.session.commit()

        self.login(
            'blog-sales', 'blog-sales-password', bypass_salesman_2fa=True,
        )
        paths = (
            ('GET', '/admin/blog'),
            ('GET', '/admin/blog/new'),
            ('GET', f'/admin/blog/{post.id}/edit'),
            ('POST', f'/admin/blog/{post.id}/delete'),
        )
        with patch('app.two_factor.requires_two_factor', return_value=False):
            for method, path in paths:
                with self.subTest(method=method, path=path):
                    response = self.client.open(path, method=method)
                    self.assertEqual(response.status_code, 302)
                    self.assertIn('/admin/dashboard', response.headers['Location'])

        db.session.refresh(post)
        self.assertIsNotNone(post.id)
        self.assertTrue(post.content)


if __name__ == '__main__':
    unittest.main()
