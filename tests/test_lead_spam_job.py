import unittest

from config import TestingConfig
from app import create_app, db
from app.models import Lead
from app.spam_filter import MANUAL_CLEAR_MARKER, rescan_leads

SPAM_MESSAGE = ('Get your game on with a $25,000 promo code '
                'https://telegra.ph/Win-the-1000000-jackpot-today-Message-ID-208975-08-30')


class RescanLeadsTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestingConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.spam = Lead(name='LarryInfak', email='karlimelitschka@gmx.de',
                         phone='82924483198', message=SPAM_MESSAGE)
        self.legit = Lead(name='Jennifer Hayes', email='jhayes82@gmail.com', phone='(919) 555-0182',
                          message='Hi, is the 2015 F-150 still available? I want a test drive.')
        db.session.add_all([self.spam, self.legit])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_flags_existing_spam_and_keeps_legit(self):
        stats = rescan_leads(self.app.config)
        self.assertEqual(stats['flagged'], 1)
        self.assertTrue(db.session.get(Lead, self.spam.id).is_spam)
        self.assertFalse(db.session.get(Lead, self.legit.id).is_spam)

    def test_delete_removes_only_spam(self):
        stats = rescan_leads(self.app.config, delete=True)
        self.assertEqual(stats['deleted'], 1)
        self.assertIsNone(db.session.get(Lead, self.spam.id))
        self.assertIsNotNone(db.session.get(Lead, self.legit.id))

    def test_dry_run_changes_nothing(self):
        stats = rescan_leads(self.app.config, dry_run=True)
        self.assertEqual(stats['spam'], 1)
        self.assertEqual(stats['flagged'], 0)
        self.assertFalse(db.session.get(Lead, self.spam.id).is_spam)

    def test_manually_cleared_lead_is_skipped(self):
        self.spam.spam_reasons = MANUAL_CLEAR_MARKER
        db.session.commit()
        stats = rescan_leads(self.app.config, delete=True)
        self.assertEqual(stats['deleted'], 0)
        self.assertIsNotNone(db.session.get(Lead, self.spam.id))


if __name__ == '__main__':
    unittest.main()
