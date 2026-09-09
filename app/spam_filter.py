"""Spam scoring for customer leads.

Signals come from mature third-party libraries where one exists, with pure
Python fallbacks so the app still works if an optional dependency is missing:

* ``phonenumbers``  (Google libphonenumber) — phone validity / region
* ``email_validator`` — address syntax and optional MX deliverability
* ``disposable_email_domains`` — throwaway mailbox blocklist
* ``py3langid`` / ``langid`` — language identification
* ``tldextract`` — public-suffix aware link/domain extraction
* Akismet (optional, network) — industry spam service, used as an override

Nothing is a hard block on its own: signals accumulate into a score with two
thresholds so a real customer is never silently dropped.

* score >= ``spam_threshold``  -> quarantined as spam (no email notification)
* score >= ``suspicious_threshold`` -> delivered normally, flagged for review
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

SPAM_THRESHOLD = 5
SUSPICIOUS_THRESHOLD = 3

# Written to Lead.spam_reasons when an admin restores a lead; rescans skip these.
MANUAL_CLEAR_MARKER = 'manually cleared by admin'

DEFAULT_PHONE_REGIONS = ('US', 'CA')
DEFAULT_LANGUAGES = ('en', 'es')
# Restricting the candidate set sharply improves langid accuracy on short text.
_LANGID_CANDIDATES = (
    'en', 'es', 'fr', 'de', 'pt', 'it', 'nl', 'pl', 'ru', 'uk', 'tr', 'vi',
    'id', 'ms', 'cy', 'ro', 'cs', 'sv', 'da', 'no', 'fi', 'hu', 'zh', 'ja',
)

try:
    import phonenumbers
except ImportError:
    phonenumbers = None

try:
    from disposable_email_domains import blocklist as _DISPOSABLE_DOMAINS
except ImportError:
    _DISPOSABLE_DOMAINS = frozenset()

try:
    from email_validator import EmailNotValidError, validate_email
except ImportError:
    validate_email = None

    class EmailNotValidError(Exception):
        pass

try:
    import tldextract
    # Bundled public-suffix snapshot, no disk cache: never touch the network or
    # sqlite at request time.
    _tld = tldextract.TLDExtract(suffix_list_urls=(), fallback_to_snapshot=True, cache_dir=None)
except ImportError:
    _tld = None

try:
    import py3langid as _langid
except ImportError:
    try:
        import langid as _langid
    except ImportError:
        _langid = None

if _langid is not None:
    try:
        _langid.set_languages(list(_LANGID_CANDIDATES))
    except Exception:
        logger.debug('langid language restriction unavailable', exc_info=True)

_URL_RE = re.compile(r'(?:https?://|www\.)[^\s<>"\']+', re.IGNORECASE)
# Bare hostnames (telegra.ph, rank-boost.top); validated against the public suffix list.
_BARE_DOMAIN_RE = re.compile(r'\b[a-z0-9][a-z0-9-]{1,63}(?:\.[a-z0-9-]{2,63})+\b', re.IGNORECASE)
_MARKUP_RE = re.compile(r'\[url[=\]]|\[/url\]|<a\s+href|</a>|\[link[=\]]', re.IGNORECASE)
# Customers often type their own address in the body; that is not a link.
_EMAIL_IN_TEXT_RE = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')

# Phrases that essentially never appear in a genuine car-buying inquiry.
_SPAM_PHRASES = (
    'promo code', 'jackpot', 'casino', 'betting', 'lottery', 'crypto',
    'bitcoin', 'forex', 'binary option', 'viagra', 'cialis', 'porn',
    'sex dating', 'escort', 'backlink', 'guest post', 'link building',
    'search engine optimization', 'seo services', 'seo audit',
    'increase your traffic', 'rank higher', 'web design services',
    'make money', 'earn money', 'work from home', 'investment opportunity',
    'click here', 'unsubscribe', 'telegra.ph', 'message-id-',
)

# Non-Latin scripts commonly used by the bot traffic this site receives.
_NON_LATIN_RE = re.compile(r'[\u0400-\u04FF\u0370-\u03FF\u0590-\u05FF\u0600-\u06FF\u4E00-\u9FFF\u3040-\u30FF]')

# Fallback English hint list, used only when no langid library is installed.
_ENGLISH_HINTS = frozenset("""
hi hello hey good morning afternoon evening thanks thank you your my me we our
is are was the a an this that these it its and or but not no yes do does did
can could would will want interested interest looking look need still have has
about more info information price cost payment finance financing trade in
available availability appointment test drive miles mileage vehicle car truck
suv van sedan jeep ford chevy chevrolet toyota honda nissan dodge ram gmc
call text email phone tomorrow today weekend visit stop by see when what how
much please send let know question questions offer sell selling buy buying
""".split())

_WORD_RE = re.compile(r"[A-Za-z']{2,}")

# Below this length language identification is unreliable, so it is skipped.
_MIN_LANGID_CHARS = 25


@dataclass
class SpamCheckResult:
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    spam_threshold: int = SPAM_THRESHOLD
    suspicious_threshold: int = SUSPICIOUS_THRESHOLD
    forced_spam: bool = False

    @property
    def is_spam(self) -> bool:
        return self.forced_spam or self.score >= self.spam_threshold

    @property
    def is_suspicious(self) -> bool:
        return self.is_spam or self.score >= self.suspicious_threshold

    @property
    def reason_text(self) -> str:
        return '; '.join(self.reasons)

    def add(self, points: int, reason: str) -> None:
        self.score += points
        self.reasons.append(f'{reason} (+{points})')

    def force_spam(self, reason: str) -> None:
        self.forced_spam = True
        self.reasons.append(reason)


def _digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


def _registered_domain(host: str) -> str:
    """Public-suffix aware domain, or '' when the host has no real TLD."""
    host = (host or '').strip().strip('.').lower()
    if not host:
        return ''
    if _tld is not None:
        parsed = _tld(host)
        # tldextract >= 5.3 renamed registered_domain
        domain = getattr(parsed, 'top_domain_under_public_suffix', None) or parsed.registered_domain
        return domain.lower()
    return host[4:] if host.startswith('www.') else host


def _external_links(message: str, allowed_domains: set[str]) -> list[str]:
    found = []
    text = _EMAIL_IN_TEXT_RE.sub(' ', message)
    for match in _URL_RE.findall(text):
        host = urlsplit(match if '//' in match else f'http://{match}').hostname or ''
        found.append(_registered_domain(host))
    for match in _BARE_DOMAIN_RE.findall(text):
        found.append(_registered_domain(match))
    return sorted({d for d in found if d and d not in allowed_domains})


def _fallback_phone_is_implausible(phone: str) -> bool:
    digits = _digits(phone)
    if not digits:
        return False
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) != 10:
        return True
    # NANP: area code and exchange code both start 2-9.
    return digits[0] in '01' or digits[3] in '01'


def _phone_signal(phone: str, regions: tuple[str, ...]) -> tuple[int, str] | None:
    """Validate the phone with libphonenumber when it is installed."""
    if not phone:
        return None
    regions = tuple(regions or DEFAULT_PHONE_REGIONS)
    if phonenumbers is None:
        if _fallback_phone_is_implausible(phone):
            return 3, 'phone number is not a valid US/Canada number'
        return None

    parsed = None
    for region in regions:
        try:
            candidate = phonenumbers.parse(phone, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(candidate):
            parsed = candidate
            break
    if parsed is None:
        return 3, 'phone number is not a valid phone number'

    number_region = phonenumbers.region_code_for_number(parsed)
    if number_region and number_region not in regions:
        return 2, f'phone number is registered outside the service area ({number_region})'
    return None


def _language_signal(message: str, allowed_languages: tuple[str, ...]) -> tuple[int, str] | None:
    if _NON_LATIN_RE.search(message):
        return 3, 'message uses a non-Latin script'
    if len(message) < _MIN_LANGID_CHARS:
        return None

    if _langid is not None:
        try:
            lang, _confidence = _langid.classify(message)
        except Exception:
            logger.debug('langid classify failed', exc_info=True)
            return None
        if lang not in allowed_languages:
            return 3, f'message language detected as "{lang}"'
        return None

    words = [w.lower() for w in _WORD_RE.findall(message)]
    if len(words) >= 4 and not any(word in _ENGLISH_HINTS for word in words):
        return 2, 'message does not appear to be English'
    return None


def _email_signals(email: str, check_mx: bool) -> list[tuple[int, str]]:
    if not email or '@' not in email:
        return []

    signals: list[tuple[int, str]] = []
    domain = email.rsplit('@', 1)[1].lower()
    if validate_email is not None:
        try:
            info = validate_email(email, check_deliverability=check_mx)
            domain = (info.domain or domain).lower()
        except EmailNotValidError as exc:
            return [(3 if check_mx else 2, f'email address rejected: {exc}')]
        except Exception:
            # DNS timeouts and similar must never block a lead.
            logger.debug('email deliverability check failed', exc_info=True)

    if domain in _DISPOSABLE_DOMAINS:
        signals.append((3, f'disposable email domain ({domain})'))
    return signals


def check_lead_spam(
    name: str = '',
    email: str = '',
    phone: str = '',
    message: str = '',
    allowed_hosts: set[str] | None = None,
    spam_threshold: int = SPAM_THRESHOLD,
    suspicious_threshold: int = SUSPICIOUS_THRESHOLD,
    phone_regions: tuple[str, ...] = DEFAULT_PHONE_REGIONS,
    allowed_languages: tuple[str, ...] = DEFAULT_LANGUAGES,
    check_email_mx: bool = False,
) -> SpamCheckResult:
    """Score a submitted lead. Higher score means more likely spam."""
    name = (name or '').strip()
    email = (email or '').strip()
    phone = (phone or '').strip()
    message = (message or '').strip()
    allowed_domains = {d for d in (_registered_domain(h) for h in (allowed_hosts or set())) if d}

    result = SpamCheckResult(
        spam_threshold=spam_threshold,
        suspicious_threshold=suspicious_threshold,
    )

    if message:
        domains = _external_links(message, allowed_domains)
        if domains:
            result.add(3, f"message contains external link(s): {', '.join(domains[:3])}")
        if _MARKUP_RE.search(message):
            result.add(3, 'message contains link markup')

        lowered = message.lower()
        hits = [phrase for phrase in _SPAM_PHRASES if phrase in lowered]
        if hits:
            result.add(3, f"spam phrase(s): {', '.join(hits[:3])}")

        language = _language_signal(message, tuple(allowed_languages))
        if language:
            result.add(*language)

        letters = [c for c in message if c.isalpha()]
        if len(letters) >= 15 and all(c.isupper() for c in letters):
            result.add(1, 'message is all caps')

    phone_signal = _phone_signal(phone, phone_regions)
    if phone_signal:
        result.add(*phone_signal)

    for points, reason in _email_signals(email, check_email_mx):
        result.add(points, reason)

    if name:
        if not re.search(r'\s', name):
            result.add(1, 'name is a single word')
        if re.search(r'[a-z][A-Z]', name):
            result.add(1, 'name has run-together capitalization')

    local_part = email.split('@', 1)[0].lower()
    if local_part and name and local_part == name.lower() and len(name) > 12:
        result.add(1, 'email matches an unusually long single-word name')

    return result


def _tuple_config(app_config, key, default):
    value = app_config.get(key)
    if not value:
        return default
    if isinstance(value, str):
        value = value.split(',')
    return tuple(str(v).strip() for v in value if str(v).strip())


def check_lead_spam_for_app(app_config, name='', email='', phone='', message=''):
    """``check_lead_spam`` using Flask config for thresholds and the site host."""
    allowed_hosts = set()
    site_url = app_config.get('SITE_URL') or ''
    host = (urlsplit(site_url).hostname or '').lower()
    if host:
        allowed_hosts.add(host)
    for extra in app_config.get('LEAD_SPAM_ALLOWED_HOSTS', ()) or ():
        allowed_hosts.add(str(extra).lower())

    return check_lead_spam(
        name=name,
        email=email,
        phone=phone,
        message=message,
        allowed_hosts=allowed_hosts,
        spam_threshold=app_config.get('LEAD_SPAM_THRESHOLD', SPAM_THRESHOLD),
        suspicious_threshold=app_config.get('LEAD_SPAM_SUSPICIOUS_THRESHOLD', SUSPICIOUS_THRESHOLD),
        phone_regions=_tuple_config(app_config, 'LEAD_SPAM_PHONE_REGIONS', DEFAULT_PHONE_REGIONS),
        allowed_languages=_tuple_config(app_config, 'LEAD_SPAM_LANGUAGES', DEFAULT_LANGUAGES),
        check_email_mx=bool(app_config.get('LEAD_SPAM_CHECK_EMAIL_MX', False)),
    )


def akismet_verdict(app_config, *, name='', email='', message='', user_ip='',
                    user_agent='', referrer='', permalink=''):
    """Ask Akismet whether a submission is spam.

    Returns True (spam), False (ham) or None when Akismet is not configured or
    unreachable — a network failure never blocks a lead.
    """
    api_key = app_config.get('AKISMET_API_KEY')
    site_url = app_config.get('AKISMET_SITE_URL') or app_config.get('SITE_URL')
    if not api_key or not site_url or not user_ip:
        return None
    try:
        import requests
    except ImportError:
        return None

    payload = {
        'blog': site_url,
        'user_ip': user_ip,
        'user_agent': user_agent or '',
        'referrer': referrer or '',
        'permalink': permalink or '',
        'comment_type': 'contact-form',
        'comment_author': name or '',
        'comment_author_email': email or '',
        'comment_content': message or '',
        'blog_lang': 'en_us',
        'is_test': '1' if app_config.get('AKISMET_IS_TEST') else '0',
    }
    try:
        response = requests.post(
            f'https://{api_key}.rest.akismet.com/1.1/comment-check',
            data=payload,
            headers={'User-Agent': 'marshallauto-lead-spam-filter/1.0'},
            timeout=float(app_config.get('AKISMET_TIMEOUT', 3)),
        )
    except Exception:
        logger.warning('Akismet request failed', exc_info=True)
        return None

    body = (response.text or '').strip().lower()
    if response.status_code != 200 or body not in ('true', 'false'):
        logger.warning('Akismet returned an unexpected response: %s %s', response.status_code, body[:200])
        return None
    return body == 'true'


def check_lead_spam_for_request(app_config, *, name='', email='', phone='', message='',
                                user_ip='', user_agent='', referrer='', permalink=''):
    """Local scoring plus the Akismet verdict when request details are known."""
    result = check_lead_spam_for_app(
        app_config, name=name, email=email, phone=phone, message=message
    )
    verdict = akismet_verdict(
        app_config,
        name=name,
        email=email,
        message=message,
        user_ip=user_ip,
        user_agent=user_agent,
        referrer=referrer,
        permalink=permalink,
    )
    if verdict is True:
        result.force_spam('Akismet classified this submission as spam')
    elif verdict is False and result.is_spam and app_config.get('AKISMET_TRUST_HAM', False):
        result.forced_spam = False
        result.score = min(result.score, result.spam_threshold - 1)
        result.reasons.append('Akismet classified this submission as ham')
    return result


def rescan_leads(app_config, *, delete=False, rescan_all=False, dry_run=False, batch_size=500):
    """Re-score stored leads with the current rules; flag (or delete) the spam.

    Leads an admin manually restored from the spam folder are always skipped so
    the job can never undo a human decision.
    """
    from app import db
    from app.models import Lead

    query = Lead.query.order_by(Lead.id.asc())
    if not rescan_all:
        query = query.filter(Lead.is_spam.is_(False))
    query = query.filter(
        (Lead.spam_reasons.is_(None)) | (Lead.spam_reasons != MANUAL_CLEAR_MARKER)
    )

    stats = {'scanned': 0, 'spam': 0, 'flagged': 0, 'deleted': 0, 'cleared': 0}
    # Materialize ids first: the loop commits in batches, which would invalidate a streaming cursor.
    lead_ids = [row[0] for row in query.with_entities(Lead.id).all()]

    for start in range(0, len(lead_ids), batch_size):
        chunk = lead_ids[start:start + batch_size]
        for lead in Lead.query.filter(Lead.id.in_(chunk)).all():
            result = check_lead_spam_for_app(
                app_config,
                name=lead.name or '',
                email=lead.email or '',
                phone=lead.phone or '',
                message=lead.message or '',
            )
            stats['scanned'] += 1
            if result.is_spam:
                stats['spam'] += 1
            if dry_run:
                continue

            if result.is_spam and delete:
                db.session.delete(lead)
                stats['deleted'] += 1
                continue
            if result.is_spam and not lead.is_spam:
                stats['flagged'] += 1
            elif lead.is_spam and not result.is_spam:
                stats['cleared'] += 1
            lead.is_spam = result.is_spam
            lead.spam_score = result.score
            lead.spam_reasons = result.reason_text[:512] or None
        if dry_run:
            db.session.rollback()
        else:
            db.session.commit()

    return stats
