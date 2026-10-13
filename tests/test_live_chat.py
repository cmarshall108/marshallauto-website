import importlib
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from bs4 import BeautifulSoup
from flask import g
from sqlalchemy.exc import SQLAlchemyError

from app import create_app, db
from app.models import ChatConversation, ChatMessage, ChatPresence, Lead, TestDrive, User, Vehicle, utcnow
from config import TestingConfig


class LiveChatTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.app.config.update(SEND_LEAD_EMAIL=False, AKISMET_API_KEY='')
        # The shared outer app context must not carry Flask-Login's cached user
        # between independent test-client requests.
        @self.app.before_request
        def clear_cached_user():
            g.pop('_login_user', None)

        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.customer = self.app.test_client()
        self.staff = self.app.test_client()
        user = User(username='chat-agent')
        user.set_password('chat-test-password')
        db.session.add(user)
        db.session.commit()
        self.user_id = user.id
        self.presence_id = str(uuid4())
        admin_limit = patch('app.admin.rate_limit_exceeded', return_value=False)
        admin_limit.start()
        self.addCleanup(admin_limit.stop)
        self.login(self.staff)
        self.contact_limit = patch('app.routes.rate_limit_exceeded', return_value=False)
        self.contact_limit.start()

    def tearDown(self):
        self.contact_limit.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def login(self, client):
        response = client.post('/admin/login', data={
            'username': 'chat-agent', 'password': 'chat-test-password',
        })
        self.assertEqual(response.status_code, 302)

    def online(self):
        response = self.staff.post('/admin/api/chat/presence', json={
            'id': self.presence_id, 'available': True,
        })
        self.assertEqual(response.status_code, 200)

    def start(self):
        self.online()
        response = self.customer.post('/contact-submit', data={
            'name': 'Jennifer Hayes', 'email': 'jhayes82@gmail.com',
            'phone': '919-555-0182', 'message': 'Hi, is the Toyota Camry available?',
            'chat_mode': 'live', 'utm_source': 'newsletter',
        })
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()['conversation']['id']

    def send(self, client, url, body='Can I visit today?', nonce=None):
        return client.post(url, json={
            'body': body, 'client_nonce': nonce or str(uuid4()),
        })

    def test_presence_expiry_away_and_logout(self):
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])
        self.online()
        self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
        record = db.session.get(ChatPresence, self.presence_id)
        record.last_seen_at = utcnow() - timedelta(seconds=91)
        db.session.commit()
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])
        self.online()
        self.staff.post('/admin/api/chat/presence', json={'id': self.presence_id, 'available': False})
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])
        self.online()
        self.staff.get('/admin/logout')
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])

    def test_presence_does_not_survive_deactivated_user_or_password_change(self):
        self.online()
        user = db.session.get(User, self.user_id)
        user.is_active_user = False
        db.session.commit()
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])
        user.is_active_user = True
        user.set_password('different-password')
        db.session.commit()
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])

    def test_page_close_grace_preserves_navigation_then_expires(self):
        now = utcnow()
        with patch('app.chat.utcnow', return_value=now):
            self.online()
            response = self.staff.post('/admin/api/chat/presence', data={
                'id': self.presence_id, 'available': 'false', 'closing': 'true',
            })
            self.assertEqual(response.status_code, 200)
            self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
        with patch('app.chat.utcnow', return_value=now + timedelta(seconds=9)):
            self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
        with patch('app.chat.utcnow', return_value=now + timedelta(seconds=10)):
            self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])
        next_page = str(uuid4())
        with patch('app.chat.utcnow', return_value=now + timedelta(seconds=11)):
            self.staff.post('/admin/api/chat/presence', json={'id': next_page, 'available': True})
            self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
            # A delayed close beacon must not extend the old lease or close the new page.
            self.staff.post('/admin/api/chat/presence', json={
                'id': self.presence_id, 'available': False, 'closing': True,
            })
            self.assertEqual(db.session.get(ChatPresence, self.presence_id).last_seen_at,
                             now - timedelta(seconds=80))
            self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
            self.staff.get('/admin/logout')
            self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])

    def test_closing_last_tab_does_not_disconnect_other_tabs(self):
        now = utcnow()
        second_id = str(uuid4())
        with patch('app.chat.utcnow', return_value=now):
            self.online()
            self.staff.post('/admin/api/chat/presence', json={'id': second_id, 'available': True})
            self.staff.post('/admin/api/chat/presence', json={
                'id': self.presence_id, 'available': False, 'closing': True,
            })
        with patch('app.chat.utcnow', return_value=now + timedelta(seconds=10)):
            self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
            self.staff.post('/admin/api/chat/presence', json={
                'id': second_id, 'available': False, 'closing': True,
            })
        with patch('app.chat.utcnow', return_value=now + timedelta(seconds=20)):
            self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])

    def test_closing_presence_validates_flag_and_does_not_create_presence(self):
        for closing in (None, 1, 'yes', [], {}):
            with self.subTest(closing=closing):
                response = self.staff.post('/admin/api/chat/presence', json={
                    'id': self.presence_id, 'available': False, 'closing': closing,
                })
                self.assertEqual(response.status_code, 400)
        response = self.staff.post('/admin/api/chat/presence', json={
            'id': self.presence_id, 'available': False, 'closing': True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ChatPresence.query.count(), 0)

    def test_live_start_links_lead_and_resumes_without_duplicate(self):
        identifier = self.start()
        self.assertEqual(Lead.query.count(), 1)
        self.assertEqual(ChatConversation.query.count(), 1)
        self.assertEqual(ChatMessage.query.count(), 1)
        lead = Lead.query.first()
        self.assertEqual(lead.source, 'live_chat')
        self.assertEqual(lead.utm_source, 'newsletter')
        resumed = self.customer.get('/api/chat/status').get_json()
        self.assertEqual(resumed['conversation']['id'], identifier)
        retry = self.customer.post('/contact-submit', data={
            'name': 'Jennifer Hayes', 'email': 'jhayes82@gmail.com',
            'message': 'Hi again', 'chat_mode': 'live',
        })
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(ChatMessage.query.count(), 1)
        self.assertEqual(Lead.query.count(), 1)

    def test_customer_and_agent_exchange_incremental_messages_and_unread(self):
        identifier = self.start()
        first = self.customer.get('/api/chat/messages').get_json()['messages'][0]
        self.assertEqual(first['sender'], 'customer')
        response = self.send(self.staff, f'/admin/api/chat/{identifier}/messages', 'Yes, it is available.')
        self.assertEqual(response.status_code, 201)
        messages = self.customer.get(f'/api/chat/messages?after={first["id"]}').get_json()['messages']
        self.assertEqual([m['body'] for m in messages], ['Yes, it is available.'])
        self.assertEqual(messages[0]['sender'], 'agent')
        inbox = self.staff.get('/admin/api/chat/conversations').get_json()['conversations']
        self.assertTrue(inbox[0]['unread'])
        self.staff.post(f'/admin/api/chat/{identifier}/read', json={'through': first['id']})
        self.assertFalse(self.staff.get('/admin/api/chat/conversations').get_json()['conversations'][0]['unread'])
        response = self.send(self.customer, '/api/chat/messages')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.staff.get('/admin/api/chat/conversations').get_json()['conversations'][0]['unread'])
        self.staff.post(f'/admin/api/chat/{identifier}/read', json={'through': first['id']})
        self.assertTrue(self.staff.get('/admin/api/chat/conversations').get_json()['conversations'][0]['unread'])

    def test_messages_idempotent_retries_and_conflict(self):
        identifier = self.start()
        nonce = str(uuid4())
        first = self.send(self.customer, '/api/chat/messages', nonce=nonce)
        second = self.send(self.customer, '/api/chat/messages', nonce=nonce)
        self.assertEqual(first.get_json()['message']['id'], second.get_json()['message']['id'])
        self.assertEqual(ChatMessage.query.count(), 2)
        conflict = self.send(self.customer, '/api/chat/messages', body='Different question', nonce=nonce)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(self.send(self.staff, f'/admin/api/chat/{identifier}/messages', nonce=nonce).status_code, 409)

    def test_offline_regular_message_and_live_race_fallback(self):
        data = {'name': 'Jennifer Hayes', 'email': 'jhayes82@gmail.com',
                'message': 'Is the Camry available?', 'source': 'live_chat_widget'}
        response = self.customer.post('/contact-submit', data=dict(data, chat_mode='live'))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Lead.query.count(), 0)
        response = self.customer.post('/contact-submit', data=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Lead.query.first().source, 'live_chat_widget')
        self.assertEqual(ChatConversation.query.count(), 0)

    def test_existing_conversation_saves_customer_messages_when_agents_away(self):
        identifier = self.start()
        self.staff.post('/admin/api/chat/presence', json={'id': self.presence_id, 'available': False})
        self.assertEqual(self.send(self.customer, '/api/chat/messages').status_code, 201)
        data = self.customer.get('/api/chat/messages').get_json()
        self.assertFalse(data['online'])
        self.assertEqual(len(data['messages']), 2)
        self.assertEqual(data['conversation']['id'], identifier)

    def test_customer_isolation_and_staff_authentication(self):
        identifier = self.start()
        stranger = self.app.test_client()
        self.assertIsNone(stranger.get('/api/chat/status').get_json()['conversation'])
        self.assertEqual(stranger.get('/api/chat/messages').status_code, 404)
        self.assertEqual(self.send(stranger, '/api/chat/messages').status_code, 404)
        self.assertEqual(stranger.post('/api/chat/end').status_code, 404)
        for url in (
            '/admin/live-chat', '/admin/api/chat/conversations',
            f'/admin/api/chat/{identifier}/messages',
        ):
            self.assertIn(stranger.get(url).status_code, (302, 401))
        self.assertIn(stranger.post('/admin/api/chat/presence', json={}).status_code, (302, 401))
        self.assertIn(self.send(stranger, f'/admin/api/chat/{identifier}/messages').status_code, (302, 401))

    def test_end_chat_preserves_transcript_and_allows_new_chat(self):
        identifier = self.start()
        self.assertEqual(self.staff.post(f'/admin/api/chat/{identifier}/end').status_code, 200)
        self.assertTrue(self.customer.get('/api/chat/messages').get_json()['conversation']['closed'])
        self.assertEqual(self.send(self.customer, '/api/chat/messages').status_code, 409)
        self.assertEqual(self.send(self.staff, f'/admin/api/chat/{identifier}/messages').status_code, 409)
        self.assertEqual(len(self.staff.get('/admin/api/chat/conversations?closed=true').get_json()['conversations']), 1)
        new_id = self.start()
        self.assertNotEqual(new_id, identifier)
        self.assertEqual(ChatConversation.query.count(), 2)
        self.assertEqual(self.customer.post('/api/chat/end').status_code, 200)

    def test_input_validation_rate_limit_and_payload_limit(self):
        self.start()
        for data in (None, [], {}, {'body': '', 'client_nonce': str(uuid4())},
                     {'body': 'x' * 5001, 'client_nonce': str(uuid4())},
                     {'body': 7, 'client_nonce': 'nope'}):
            response = self.customer.post('/api/chat/messages', json=data)
            self.assertEqual(response.status_code, 400)
        with patch('app.chat.rate_limit_exceeded', return_value=True):
            self.assertEqual(self.send(self.customer, '/api/chat/messages').status_code, 429)
        response = self.customer.post('/api/chat/messages', data='x' * 17000,
                                      content_type='application/json')
        self.assertEqual(response.status_code, 413)
        for value in (None, 1, 'maybe'):
            self.assertEqual(self.staff.post('/admin/api/chat/presence', json={
                'id': self.presence_id, 'available': value,
            }).status_code, 400)

    def test_start_validation_and_spam_quarantine(self):
        self.online()
        for data in (
            {'name': 'Jennifer', 'email': 'not-an-email', 'message': 'hello'},
            {'name': 'Jennifer', 'email': 'jhayes82@gmail.com', 'message': '  '},
        ):
            self.assertEqual(self.customer.post('/contact-submit', data={
                **data, 'chat_mode': 'live',
            }).status_code, 400)
        with patch('app.routes.check_lead_spam_for_request') as check:
            check.return_value.is_spam = True
            check.return_value.score = 10
            check.return_value.reason_text = 'Test spam'
            response = self.customer.post('/contact-submit', data={
                'name': 'Jennifer', 'email': 'jhayes82@gmail.com',
                'message': 'Test question', 'chat_mode': 'live',
            })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ChatConversation.query.count(), 0)
        self.assertTrue(Lead.query.first().is_spam)

    def test_database_failure_is_explicit_and_rolls_back_start(self):
        self.online()
        with patch.object(db.session, 'commit', side_effect=SQLAlchemyError('Test failure')):
            response = self.customer.post('/contact-submit', data={
                'name': 'Jennifer', 'email': 'jhayes82@gmail.com',
                'message': 'Is the Camry available?', 'chat_mode': 'live',
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(Lead.query.count(), 0)
        self.assertEqual(ChatConversation.query.count(), 0)
        self.assertEqual(ChatMessage.query.count(), 0)

    def test_csrf_required_on_customer_and_staff_mutations(self):
        identifier = self.start()
        self.app.config['WTF_CSRF_ENABLED'] = True
        headers = {'Accept': 'application/json'}
        for client, url, data in (
            (self.customer, '/api/chat/messages', {'body': 'hello', 'client_nonce': str(uuid4())}),
            (self.customer, '/api/chat/end', {}),
            (self.staff, '/admin/api/chat/presence', {'id': self.presence_id, 'available': True}),
            (self.staff, f'/admin/api/chat/{identifier}/messages', {'body': 'hello', 'client_nonce': str(uuid4())}),
            (self.staff, f'/admin/api/chat/{identifier}/read', {'through': 0}),
            (self.staff, f'/admin/api/chat/{identifier}/end', {}),
            (self.staff, f'/admin/api/chat/{identifier}/block', {'blocked': True}),
        ):
            with self.subTest(url=url):
                response = client.post(url, json=data, headers=headers)
                self.assertEqual(response.status_code, 400)
                self.assertFalse(response.get_json()['success'])
        html = BeautifulSoup(self.customer.get('/').data, 'html.parser')
        csrf = html.select_one('#chat-widget-form [name="csrf_token"]')['value']
        response = self.customer.post('/api/chat/messages', json={
            'body': 'Thanks!', 'client_nonce': str(uuid4()),
        }, headers={**headers, 'X-CSRFToken': csrf})
        self.assertEqual(response.status_code, 201)

    def test_transcript_pagination_and_cache_headers(self):
        identifier = self.start()
        db.session.add_all([
            ChatMessage(conversation_id=identifier, sender='customer',
                        body=f'Question {i}', client_nonce=str(uuid4()))
            for i in range(205)
        ])
        db.session.commit()
        response = self.customer.get('/api/chat/messages')
        first = response.get_json()
        self.assertEqual(len(first['messages']), 200)
        self.assertTrue(first['has_more'])
        rest = self.customer.get(f'/api/chat/messages?after={first["messages"][-1]["id"]}').get_json()
        self.assertEqual(len(rest['messages']), 6)
        self.assertFalse(rest['has_more'])
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertIn('noindex', response.headers['X-Robots-Tag'])

    def test_presence_is_scoped_to_authenticated_agent(self):
        self.online()
        user = User(username='second-agent')
        user.set_password('second-password')
        db.session.add(user)
        db.session.commit()
        other = self.app.test_client()
        other.post('/admin/login', data={'username': 'second-agent', 'password': 'second-password'})
        response = other.post('/admin/api/chat/presence', json={
            'id': self.presence_id, 'available': False,
        })
        self.assertEqual(response.status_code, 403)

    def test_another_available_inbox_keeps_team_online(self):
        self.online()
        second_id = str(uuid4())
        self.staff.post('/admin/api/chat/presence', json={'id': second_id, 'available': True})
        self.staff.post('/admin/api/chat/presence', json={'id': self.presence_id, 'available': False})
        self.assertTrue(self.customer.get('/api/chat/status').get_json()['online'])
        self.staff.post('/admin/api/chat/presence', json={'id': second_id, 'available': False})
        self.assertFalse(self.customer.get('/api/chat/status').get_json()['online'])

    def test_invalid_cursors_and_message_limit(self):
        identifier = self.start()
        for after in ('abc', '-1', '9999999999999999999999999999'):
            self.assertEqual(self.customer.get(f'/api/chat/messages?after={after}').status_code, 400)
        for page in ('abc', '0', '10001'):
            self.assertEqual(self.staff.get(f'/admin/api/chat/conversations?page={page}').status_code, 400)
        for through in ('1', -1, 9999, True):
            response = self.staff.post(f'/admin/api/chat/{identifier}/read', json={'through': through})
            self.assertEqual(response.status_code, 400)
        db.session.add_all([
            ChatMessage(conversation_id=identifier, sender='customer',
                        body='A question', client_nonce=str(uuid4()))
            for _ in range(999)
        ])
        db.session.commit()
        self.assertEqual(self.send(self.customer, '/api/chat/messages').status_code, 409)
        self.assertEqual(ChatMessage.query.count(), 1000)

    def test_inbox_rendering_and_linked_lead_filter(self):
        identifier = self.start()
        conversation = db.session.get(ChatConversation, identifier)
        db.session.add(Lead(name='Another customer', email='another@gmail.com',
                            message='Unrelated lead', source='contact'))
        db.session.commit()
        response = self.staff.get('/admin/live-chat')
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.data, 'html.parser')
        self.assertIsNone(html.select_one('#chat-availability'))
        self.assertIsNotNone(html.select_one('#admin-chat-presence-status'))
        self.assertIsNotNone(html.select_one('#chat-inbox-reply'))
        self.assertIsNotNone(html.select_one('#chat-inbox-block'))
        self.assertIsNotNone(html.select_one('#admin-chat-sounds'))
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        lead_page = self.staff.get(f'/admin/leads?lead_id={conversation.lead_id}').get_data(as_text=True)
        self.assertIn('Jennifer Hayes', lead_page)
        self.assertNotIn('Another customer', lead_page)

    def test_monitor_and_toast_on_all_signed_in_page_layouts_only(self):
        vehicle = Vehicle(year=2020, make='Toyota', model='Camry', price=20000,
                          mileage=40000, slug='chat-monitor-car')
        db.session.add(vehicle)
        db.session.flush()
        drive = TestDrive(vehicle_id=vehicle.id, customer_name='Test Customer')
        db.session.add(drive)
        db.session.commit()
        for path in ('/admin/dashboard', '/admin/vehicles', '/admin/leads',
                     '/admin/live-chat', '/', f'/admin/test-drives/{drive.id}/print'):
            with self.subTest(path=path):
                response = self.staff.get(path)
                self.assertEqual(response.status_code, 200)
                html = BeautifulSoup(response.data, 'html.parser')
                monitor = html.select_one('#admin-chat-monitor')
                self.assertIsNotNone(monitor)
                self.assertTrue(monitor['data-csrf'])
                self.assertEqual(monitor['data-user-id'], str(self.user_id))
                self.assertEqual(len(html.select('#admin-chat-toast')), 1)
                self.assertEqual(len(html.select('script[src*="/admin-chat-notifications.js"]')), 1)
        user = db.session.get(User, self.user_id)
        user.totp_enabled = True
        db.session.commit()
        with patch('app.admin.requires_two_factor', return_value=True):
            response = self.staff.get('/admin/settings/two-factor')
        self.assertEqual(response.status_code, 200)
        html = BeautifulSoup(response.data, 'html.parser')
        self.assertIsNotNone(html.select_one('#admin-chat-monitor'))
        self.assertIsNotNone(html.select_one('#admin-chat-toast'))
        for path in ('/', '/admin/login'):
            html = BeautifulSoup(self.customer.get(path).data, 'html.parser')
            self.assertIsNone(html.select_one('#admin-chat-monitor'))
            self.assertIsNone(html.select_one('#admin-chat-toast'))

    def test_staff_database_error_response_and_message_rollback(self):
        identifier = self.start()
        with patch.object(db.session, 'commit', side_effect=SQLAlchemyError('Test failure')):
            response = self.send(self.staff, f'/admin/api/chat/{identifier}/messages')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.get_json()['success'])
        self.assertEqual(ChatMessage.query.count(), 1)

    def test_chat_migration_handles_new_and_already_bootstrapped_tables(self):
        migration = importlib.import_module('migrations.versions.a9b0c1d2e3f4_add_live_chat')
        moderation = importlib.import_module('migrations.versions.b1c2d3e4f5a6_add_chat_moderation')
        with db.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.downgrade()
                migration.upgrade()
                migration.upgrade()
                moderation.upgrade()
                moderation.upgrade()
        self.assertEqual(ChatConversation.query.count(), 0)
        self.assertEqual(ChatPresence.query.count(), 0)
        self.assertEqual(ChatMessage.query.count(), 0)

    def test_reply_spam_blocked_and_suspicious_delivered_with_staff_only_flags(self):
        identifier = self.start()
        before = db.session.get(ChatConversation, identifier).last_message_id
        response = self.send(self.customer, '/api/chat/messages', 'Casino jackpot https://spam.example.com')
        self.assertEqual(response.status_code, 400)
        self.assertIn('blocked', response.get_json()['message'])
        self.assertEqual(ChatMessage.query.count(), 1)
        self.assertEqual(db.session.get(ChatConversation, identifier).last_message_id, before)
        response = self.send(self.customer, '/api/chat/messages', 'https://example.com')
        self.assertEqual(response.status_code, 201)
        self.assertNotIn('spam_reasons', response.get_json()['message'])
        staff = self.staff.get(f'/admin/api/chat/{identifier}/messages').get_json()
        self.assertTrue(staff['messages'][-1]['suspicious'])
        self.assertEqual(staff['messages'][-1]['spam_score'], 3)
        self.assertIn('external link', staff['messages'][-1]['spam_reasons'])
        self.assertTrue(staff['conversation']['suspicious'])
        visitor = self.customer.get('/api/chat/messages').get_json()
        self.assertNotIn('suspicious', visitor['messages'][-1])
        # Staff can discuss blocked content without being subject to buyer spam checks.
        self.assertEqual(self.send(
            self.staff, f'/admin/api/chat/{identifier}/messages',
            'Casino jackpot https://spam.example.com',
        ).status_code, 201)
        self.assertEqual(self.send(self.customer, '/api/chat/messages', 'What time can I test drive?').status_code, 201)

    def test_suspicious_initial_question_is_delivered_for_staff_review(self):
        self.online()
        response = self.customer.post('/contact-submit', data={
            'name': 'Jennifer Hayes', 'email': 'jhayes82@gmail.com',
            'message': 'https://example.com', 'chat_mode': 'live',
        })
        self.assertEqual(response.status_code, 201)
        identifier = response.get_json()['conversation']['id']
        data = self.staff.get(f'/admin/api/chat/{identifier}/messages').get_json()
        self.assertTrue(data['conversation']['suspicious'])
        self.assertTrue(data['messages'][0]['suspicious'])
        self.assertEqual(data['messages'][0]['spam_score'], 3)
        self.assertFalse(Lead.query.first().is_spam)

    def test_repeat_flood_rejected_but_nonce_retry_is_idempotent(self):
        identifier = self.start()
        nonce = str(uuid4())
        first = self.send(self.customer, '/api/chat/messages', 'Hello there', nonce=nonce)
        retry = self.send(self.customer, '/api/chat/messages', 'Hello there', nonce=nonce)
        self.assertEqual(first.get_json()['message']['id'], retry.get_json()['message']['id'])
        self.assertEqual(self.send(self.customer, '/api/chat/messages', 'Hello there').status_code, 429)
        previous = ChatMessage.query.order_by(ChatMessage.id.desc()).first()
        previous.created_at = utcnow() - timedelta(seconds=61)
        db.session.commit()
        self.assertEqual(self.send(self.customer, '/api/chat/messages', 'Hello there').status_code, 201)

    def test_staff_block_ends_chat_quarantines_lead_and_can_be_reversed(self):
        identifier = self.start()
        url = f'/admin/api/chat/{identifier}/block'
        self.assertEqual(self.customer.post(url, json={'blocked': True}).status_code, 302)
        for value in (None, 'true', 1):
            self.assertEqual(self.staff.post(url, json={'blocked': value}).status_code, 400)
        self.assertEqual(self.staff.post(url, json={'blocked': True}).status_code, 200)
        conversation = db.session.get(ChatConversation, identifier)
        self.assertIsNotNone(conversation.closed_at)
        self.assertIsNotNone(conversation.blocked_at)
        self.assertTrue(conversation.lead.is_spam)
        self.assertEqual(self.send(self.customer, '/api/chat/messages').status_code, 403)
        data = {'name': 'Jennifer Hayes', 'email': 'jhayes82@gmail.com',
                'message': 'Can I visit?', 'chat_mode': 'live'}
        self.assertEqual(self.customer.post('/contact-submit', data=data).status_code, 403)
        self.assertEqual(ChatConversation.query.count(), 1)
        self.assertTrue(self.customer.get('/api/chat/status').get_json()['conversation']['blocked'])
        self.assertEqual(self.staff.post(url, json={'blocked': False}).status_code, 200)
        self.assertFalse(conversation.lead.is_spam)
        self.assertEqual(conversation.lead.spam_reasons, 'manually cleared by admin')
        self.assertIsNotNone(conversation.closed_at)
        self.assertEqual(self.customer.post('/contact-submit', data=data).status_code, 201)

    def test_notification_cursor_tracks_only_customer_messages_and_unread_count(self):
        self.assertEqual(self.customer.get('/admin/api/chat/notifications').status_code, 302)
        empty = self.staff.get('/admin/api/chat/notifications').get_json()
        self.assertEqual(empty['latest_customer_message_id'], 0)
        identifier = self.start()
        first = self.staff.get('/admin/api/chat/notifications').get_json()
        self.assertEqual(first['unread'], 1)
        self.send(self.staff, f'/admin/api/chat/{identifier}/messages', 'Yes, available.')
        own = self.staff.get('/admin/api/chat/notifications').get_json()
        self.assertEqual(own['latest_customer_message_id'], first['latest_customer_message_id'])
        self.send(self.customer, '/api/chat/messages', 'Thank you')
        incoming = self.staff.get('/admin/api/chat/notifications').get_json()
        self.assertGreater(incoming['latest_customer_message_id'], first['latest_customer_message_id'])
        self.staff.post(f'/admin/api/chat/{identifier}/read',
                        json={'through': incoming['latest_customer_message_id']})
        read = self.staff.get('/admin/api/chat/notifications')
        self.assertEqual(read.get_json()['unread'], 0)
        self.assertEqual(read.headers['Cache-Control'], 'private, no-store')

    def test_chat_moderation_migration_preserves_existing_messages(self):
        identifier = self.start()
        migration = importlib.import_module('migrations.versions.b1c2d3e4f5a6_add_chat_moderation')
        db.session.remove()
        with db.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
                migration.upgrade()
        conversation = db.session.get(ChatConversation, identifier)
        self.assertFalse(conversation.is_suspicious)
        self.assertIsNone(conversation.blocked_at)
        message = ChatMessage.query.first()
        self.assertEqual(message.body, 'Hi, is the Toyota Camry available?')
        self.assertEqual(message.spam_score, 0)
        self.assertFalse(message.is_suspicious)


if __name__ == '__main__':
    unittest.main()
