import unittest
from unittest import mock

from app.spam_filter import check_lead_spam, check_lead_spam_for_request

ALLOWED = {'marshallautosanford.com'}


def check(name='', email='', phone='', message=''):
    return check_lead_spam(name=name, email=email, phone=phone, message=message, allowed_hosts=ALLOWED)


class RealSpamSamplesTests(unittest.TestCase):
    """Samples taken from actual spam submitted through the public contact form."""

    def test_promo_code_link_blast(self):
        result = check(
            name='LarryInfak',
            email='karlimelitschka@gmx.de',
            phone='82924483198',
            message=('Get your game on with a $25,000 promo code '
                     'https://telegra.ph/Win-the-1000000-jackpot-today-Message-ID-208975-08-30'),
        )
        self.assertTrue(result.is_spam, result.reasons)

    def test_all_caps_promo_variant(self):
        result = check(
            name='LarryInfak',
            email='mbalzano@hotmail.com',
            phone='83968382816',
            message=('A $25,000 PROMO CODE FOR THE PRODIGY '
                     'https://telegra.ph/Win-the-1000000-jackpot-today-Message-ID-22545-08-30'),
        )
        self.assertTrue(result.is_spam, result.reasons)

    def test_foreign_language_price_probe(self):
        result = check(
            name='Robertvot',
            email='hsilojhonaisy@gmail.com',
            phone='82965974791',
            message='Hai, saya ingin tahu harga Anda.',
        )
        self.assertTrue(result.is_spam, result.reasons)

    def test_cyrillic_price_probe(self):
        result = check(
            name='Robertvot',
            email='hsilojhonaisy@gmail.com',
            phone='85922793144',
            message='Прывітанне, я хацеў даведацца Ваш прайс.',
        )
        self.assertTrue(result.is_spam, result.reasons)

    def test_welsh_price_probe(self):
        result = check(
            name='Robertvot',
            email='hsilojhonaisy@gmail.com',
            phone='88648333744',
            message='Hi, roeddwn i eisiau gwybod eich pris.',
        )
        self.assertTrue(result.is_spam, result.reasons)

    def test_seo_pitch(self):
        result = check(
            name='Digital Growth',
            email='sales@seo-agency.xyz',
            phone='',
            message='We offer SEO services and quality backlinks. Click here: www.rank-boost.top/offer',
        )
        self.assertTrue(result.is_spam, result.reasons)


    def test_disposable_email_domain_is_flagged(self):
        result = check(
            name='Ann Blake',
            email='ablake@mailinator.com',
            phone='9195550111',
            message='Please send me pricing information on the truck.',
        )
        self.assertTrue(result.is_suspicious, result.reasons)


class LegitimateLeadTests(unittest.TestCase):
    def _assert_clean(self, result):
        self.assertFalse(result.is_spam, result.reasons)
        self.assertFalse(result.is_suspicious, result.reasons)

    def test_typical_inquiry(self):
        self._assert_clean(check(
            name='Jennifer Hayes',
            email='jhayes82@gmail.com',
            phone='(919) 555-0182',
            message="Hi, is the 2015 F-150 still available? I'd like to schedule a test drive this weekend.",
        ))

    def test_short_message_no_phone(self):
        self._assert_clean(check(
            name='Mike Alvarez',
            email='malvarez@yahoo.com',
            phone='',
            message='Is this still available?',
        ))

    def test_first_name_only_with_country_code(self):
        self._assert_clean(check(
            name='Dave',
            email='dave.r@outlook.com',
            phone='+1 919-555-0110',
            message='What is the out the door price on the Silverado? Thanks.',
        ))

    def test_all_caps_short_note(self):
        self._assert_clean(check(
            name='Robert Chandler',
            email='rchandler@aol.com',
            phone='9195550143',
            message='CALL ME ABOUT THE JEEP',
        ))

    def test_no_message_at_all(self):
        self._assert_clean(check(
            name='Sara Nguyen',
            email='sara.nguyen@gmail.com',
            phone='919-555-0166',
            message='',
        ))

    def test_link_to_our_own_listing_is_allowed(self):
        self._assert_clean(check(
            name='Tom Reilly',
            email='treilly@gmail.com',
            phone='9195550188',
            message='I saw https://marshallautosanford.com/inventory/2016-honda-accord and want more info.',
        ))

    def test_spanish_speaking_customer_is_not_blocked(self):
        result = check(
            name='Maria Gonzalez',
            email='mgonzalez@gmail.com',
            phone='919-555-0134',
            message='Hola, quisiera saber el precio del Toyota Camry y si aceptan financiamiento.',
        )
        self.assertFalse(result.is_spam, result.reasons)

    def test_customer_email_in_message_is_not_a_link(self):
        self._assert_clean(check(
            name='Bill Turner',
            email='bill.t@comcast.net',
            phone='(919) 555-0177',
            message='My email is bill.t@comcast.net. Please send me the CarFax for stock MA2001.',
        ))


class AkismetIntegrationTests(unittest.TestCase):
    config = {
        'SITE_URL': 'https://marshallautosanford.com',
        'AKISMET_API_KEY': 'test-key',
    }
    legit = dict(name='Jennifer Hayes', email='jhayes82@gmail.com', phone='(919) 555-0182',
                 message='Hi, is the 2015 F-150 still available?', user_ip='203.0.113.10')

    def test_akismet_spam_verdict_overrides_clean_score(self):
        with mock.patch('app.spam_filter.akismet_verdict', return_value=True):
            result = check_lead_spam_for_request(self.config, **self.legit)
        self.assertTrue(result.is_spam, result.reasons)

    def test_akismet_failure_leaves_local_score_alone(self):
        with mock.patch('app.spam_filter.akismet_verdict', return_value=None):
            result = check_lead_spam_for_request(self.config, **self.legit)
        self.assertFalse(result.is_spam, result.reasons)

    def test_akismet_ham_can_rescue_a_flagged_lead(self):
        config = dict(self.config, AKISMET_TRUST_HAM=True)
        spam_like = dict(name='Robertvot', email='x@gmail.com', phone='88648333744',
                         message='Hi, roeddwn i eisiau gwybod eich pris.', user_ip='203.0.113.10')
        with mock.patch('app.spam_filter.akismet_verdict', return_value=False):
            result = check_lead_spam_for_request(config, **spam_like)
        self.assertFalse(result.is_spam, result.reasons)


if __name__ == '__main__':
    unittest.main()
