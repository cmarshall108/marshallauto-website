import unittest
from datetime import timedelta
from unittest.mock import patch

from app import create_app, db
from app.models import Lead, OutreachContact, Vehicle, utcnow
from app.outreach import (
    find_contact_by_phone,
    opt_out_sms,
    process_monthly_outreach,
    sync_outreach_contacts_from_leads,
    upsert_outreach_contact_from_lead,
)
from app.utils import format_email_address, normalize_phone_e164
from config import TestingConfig


class OutreachFormattingTests(unittest.TestCase):
    def test_formats_email_and_phone(self):
        self.assertEqual(format_email_address('  Pat.Buyer@Example.COM '), 'Pat.Buyer@example.com')
        self.assertEqual(normalize_phone_e164('9195550182'), '+19195550182')
        self.assertEqual(normalize_phone_e164('(919) 555-0182'), '+19195550182')
        self.assertIsNone(normalize_phone_e164('not-a-phone'))


class OutreachContactTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _lead(self, **kwargs):
        lead = Lead(
            name=kwargs.get('name', 'Pat Buyer'),
            email=kwargs.get('email', 'pat@example.com'),
            phone=kwargs.get('phone', '9195550182'),
            source=kwargs.get('source', 'contact'),
            is_spam=kwargs.get('is_spam', False),
        )
        db.session.add(lead)
        db.session.commit()
        return lead

    def test_upsert_formats_and_dedupes_contacts(self):
        first = self._lead(email='Pat@Example.COM', phone='9195550182')
        contact = upsert_outreach_contact_from_lead(first, commit=True)
        self.assertIsNotNone(contact)
        self.assertEqual(contact.email, 'Pat@example.com')
        self.assertEqual(contact.phone, '(919) 555-0182')
        self.assertEqual(contact.phone_e164, '+19195550182')
        self.assertTrue(contact.sms_opt_in)
        self.assertTrue(contact.email_opt_in)
        self.assertTrue(contact.unsubscribe_token)

        second = self._lead(name='Patricia Buyer', email='patricia@example.com', phone='(919) 555-0182')
        again = upsert_outreach_contact_from_lead(second, commit=True)
        self.assertEqual(again.id, contact.id)
        self.assertEqual(OutreachContact.query.count(), 1)
        self.assertEqual(again.name, 'Patricia Buyer')
        self.assertEqual(again.email, 'patricia@example.com')

    def test_spam_leads_are_ignored(self):
        lead = self._lead(is_spam=True)
        self.assertIsNone(upsert_outreach_contact_from_lead(lead, commit=True))
        self.assertEqual(OutreachContact.query.count(), 0)

    def test_opt_out_is_preserved_on_later_lead(self):
        lead = self._lead()
        contact = upsert_outreach_contact_from_lead(lead, commit=True)
        opt_out_sms(contact, reason='test')
        db.session.commit()

        later = self._lead(name='Pat Again', email='pat@example.com', phone='9195550182')
        contact = upsert_outreach_contact_from_lead(later, commit=True)
        self.assertFalse(contact.sms_opt_in)
        self.assertIsNotNone(contact.sms_opted_out_at)

    def test_sync_backfill_from_existing_leads(self):
        self._lead(email='one@example.com', phone='9195550101')
        self._lead(email='two@example.com', phone='9195550102')
        stats = sync_outreach_contacts_from_leads()
        self.assertEqual(stats['total'], 2)
        self.assertEqual(OutreachContact.query.count(), 2)

    def test_monthly_outreach_dry_run_marks_contacts(self):
        vehicle = Vehicle(
            slug='outreach-civic', year=2019, make='Honda', model='Civic',
            price=10000, mileage=40000, status='available',
        )
        db.session.add(vehicle)
        lead = self._lead()
        contact = upsert_outreach_contact_from_lead(lead, commit=True)
        self.app.config['OUTREACH_DRY_RUN'] = True
        self.app.config['OUTREACH_ENABLED'] = True
        stats = process_monthly_outreach()
        self.assertEqual(stats['contacts'], 1)
        self.assertEqual(stats['sms'], 1)
        self.assertEqual(stats['email'], 1)
        db.session.refresh(contact)
        self.assertIsNotNone(contact.last_outreach_at)
        self.assertIsNotNone(contact.last_sms_at)
        self.assertIsNotNone(contact.last_email_at)

        # Spacing prevents a second send immediately.
        stats_again = process_monthly_outreach()
        self.assertEqual(stats_again['contacts'], 0)

    def test_monthly_outreach_skips_recent_contacts(self):
        vehicle = Vehicle(
            slug='outreach-accord', year=2018, make='Honda', model='Accord',
            price=12000, mileage=50000, status='available',
        )
        db.session.add(vehicle)
        lead = self._lead(email='recent@example.com', phone='9195550199')
        contact = upsert_outreach_contact_from_lead(lead, commit=True)
        contact.last_outreach_at = utcnow() - timedelta(days=3)
        db.session.commit()
        stats = process_monthly_outreach()
        self.assertEqual(stats['contacts'], 0)

    def test_unsubscribe_page_stops_texts_and_emails(self):
        lead = self._lead()
        contact = upsert_outreach_contact_from_lead(lead, commit=True)
        response = self.client.post(
            f'/alerts/unsubscribe/{contact.unsubscribe_token}',
            data={'action': 'all'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(contact)
        self.assertFalse(contact.sms_opt_in)
        self.assertFalse(contact.email_opt_in)

    def test_stop_texts_by_phone(self):
        lead = self._lead(phone='9195550177')
        contact = upsert_outreach_contact_from_lead(lead, commit=True)
        response = self.client.post(
            '/alerts/stop-texts',
            data={'phone': '9195550177'},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'all set', response.data.lower())
        db.session.refresh(contact)
        self.assertFalse(contact.sms_opt_in)
        self.assertTrue(contact.email_opt_in)

    def test_contact_form_creates_outreach_contact(self):
        with patch('app.routes.notify_new_lead'), patch('app.routes.send_lead_confirmation'):
            response = self.client.post('/contact', data={
                'name': 'Form Buyer',
                'email': 'Form.Buyer@Example.com',
                'phone': '9195550166',
                'message': 'Looking for a truck',
                'csrf_token': 'not-checked-in-testing',
            }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        contact = OutreachContact.query.filter_by(phone_e164='+19195550166').first()
        self.assertIsNotNone(contact)
        self.assertEqual(contact.email, 'Form.Buyer@example.com')
        self.assertEqual(contact.phone, '(919) 555-0166')

    def test_find_contact_by_phone(self):
        lead = self._lead(phone='9195550155')
        contact = upsert_outreach_contact_from_lead(lead, commit=True)
        self.assertEqual(find_contact_by_phone('(919) 555-0155').id, contact.id)


if __name__ == '__main__':
    unittest.main()
