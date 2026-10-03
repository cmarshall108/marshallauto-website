"""Read public Craigslist vehicle listings without account credentials."""
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.marketplace_import import MarketplaceError


def craigslist_url(value):
    try:
        parts = urlsplit((value or '').strip())
        valid = (parts.scheme == 'https'
                 and re.fullmatch(r'(?:[a-z0-9-]+\.)?craigslist\.org', parts.hostname or '')
                 and not parts.username and not parts.password and parts.port in (None, 443))
    except ValueError as exc:
        raise MarketplaceError('Invalid Craigslist listing URL.') from exc
    if not valid:
        raise MarketplaceError('Use an HTTPS Craigslist vehicle listing URL.')
    if not (re.fullmatch(r'/view/d/[a-zA-Z0-9_-]+/[a-zA-Z0-9]{10,40}/?', parts.path)
            or re.fullmatch(r'/(?:[a-z]{3}/)?(?:cto|ctd|cta)/d/[a-zA-Z0-9_-]+/[0-9]{6,20}\.html', parts.path)):
        raise MarketplaceError('Use a Craigslist vehicle listing, not an account, search, or management link.')
    return f'https://{parts.hostname}{parts.path.rstrip("/")}'


def _image_key(url):
    from app.marketplace_import import photo_url

    photo_url(url)
    parts = urlsplit(url)
    if parts.hostname != 'images.craigslist.org':
        raise MarketplaceError('The Craigslist listing contains an unsupported photo host.')
    match = re.fullmatch(r'/([A-Za-z0-9_]+)_(\d+x\d+[a-z]?)\.(?:jpg|jpeg|png|webp)', parts.path)
    if not match:
        raise MarketplaceError('The Craigslist listing contains an unsupported photo format.')
    return match.group(1)


def parse_craigslist(html, url):
    requested = craigslist_url(url)
    soup = BeautifulSoup(html, 'html.parser')
    canonical_tag = soup.select_one('link[rel="canonical"]')
    if not canonical_tag or not isinstance(canonical_tag.get('href'), str):
        raise MarketplaceError('Craigslist listing identity is unavailable. Saved data was retained.')
    canonical = craigslist_url(canonical_tag['href'])
    requested_path = urlsplit(requested).path
    legacy_id = re.search(r'/([0-9]+)\.html$', requested_path)
    posting_info = ' '.join(element.get_text(' ', strip=True) for element in soup.select('.postinginfo'))
    post_id = re.search(r'post id:\s*([0-9]+)', posting_info, re.IGNORECASE)
    if legacy_id:
        if not post_id or legacy_id.group(1) != post_id.group(1):
            raise MarketplaceError('Craigslist returned a different listing. Saved data was retained.')
    elif requested_path.rstrip('/').rsplit('/', 1)[1] != urlsplit(canonical).path.rsplit('/', 1)[1]:
        raise MarketplaceError('Craigslist returned a different listing. Saved data was retained.')
    script = soup.select_one('script#ld_posting_data[type="application/ld+json"]')
    body = soup.select_one('#postingbody')
    try:
        product = json.loads(script.get_text()) if script else None
    except (ValueError, RecursionError) as exc:
        raise MarketplaceError('Craigslist listing data could not be read.') from exc
    if not isinstance(product, dict) or product.get('@type') not in ('Product', 'Car', 'Vehicle') or body is None:
        raise MarketplaceError('Craigslist listing is unavailable, expired, or blocked. Saved data was retained.')
    title = product.get('name')
    offers = product.get('offers')
    if not isinstance(title, str) or not isinstance(offers, dict) or offers.get('priceCurrency') != 'USD':
        raise MarketplaceError('A vehicle title and USD listing price are required.')
    try:
        amount = Decimal(str(offers.get('price')))
        if not amount.is_finite() or not 0 <= amount <= Decimal('99999999.99'):
            raise InvalidOperation
        fields = {'price': str(amount.quantize(Decimal('0.01')))}
    except InvalidOperation as exc:
        raise MarketplaceError('The Craigslist price is invalid.') from exc
    attributes = {}
    for attribute in soup.select('.attrgroup .attr'):
        label = attribute.select_one('.labl')
        value = attribute.select_one('.valu')
        if label and value:
            attributes[label.get_text(' ', strip=True).rstrip(':').lower()] = value.get_text(' ', strip=True)
    for attribute in soup.select('.attrgroup > span'):
        label, separator, value = attribute.get_text(' ', strip=True).partition(':')
        if separator:
            attributes.setdefault(label.strip().lower(), value.strip())
    year = soup.select_one('.attrgroup .year')
    make_model = soup.select_one('.attrgroup .makemodel')
    identity = title.strip()
    if year and make_model:
        identity = year.get_text(strip=True) + ' ' + make_model.get_text(' ', strip=True)
    match = re.fullmatch(r'(19\d{2}|20\d{2}|2100)\s+(Land Rover|Alfa Romeo|Aston Martin|AM General|\S+)\s+(.+)', identity, re.IGNORECASE)
    if match:
        fields['year'] = int(match.group(1))
        fields['make'] = match.group(2).title()[:64]
        model = match.group(3).title()
        fields['model'] = re.sub(r'\bI{2,3}\b', lambda token: token.group().upper(), model, flags=re.IGNORECASE)[:64]
    mileage = attributes.get('odometer', '').replace(',', '').strip()
    if re.fullmatch(r'[0-9]{1,8}', mileage) and int(mileage) <= 10000000:
        fields['mileage'] = int(mileage)
    title_status = attributes.get('title status', '').lower()
    if title_status in ('clean', 'rebuilt', 'salvage'):
        fields['title_status'] = title_status
    mapping = {'fuel': ('fuel_type', 32), 'transmission': ('transmission', 128),
               'type': ('body_style', 64), 'paint color': ('exterior_color', 64),
               'cylinders': ('engine', 128)}
    for source, (field, limit) in mapping.items():
        if attributes.get(source):
            fields[field] = attributes[source][:limit]
    drive = attributes.get('drive', '').upper()
    if drive in ('FWD', 'RWD', 'AWD', '4WD'):
        fields['drivetrain'] = drive
    vin = attributes.get('vin', '').upper()
    if re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        fields['vin'] = vin
    for extra in body.select('.print-information, script, style'):
        extra.decompose()
    description = body.get_text('\n', strip=True)
    if not description:
        raise MarketplaceError('The listing description is missing. Saved data was retained.')
    fields['description'] = description
    extra_fields = {key: value for key, value in attributes.items()
                    if key not in {*mapping, 'odometer', 'title status', 'drive', 'vin'}}
    if extra_fields:
        fields['features'] = ', '.join(f'{key.title()}: {value}' for key, value in extra_fields.items())
    image_urls = product.get('image')
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    if not isinstance(image_urls, list) or not 1 <= len(image_urls) <= 40:
        raise MarketplaceError('The listing must expose between 1 and 40 photos.')
    originals = {}
    for anchor in soup.select('#thumbs a[href]'):
        href = anchor.get('href')
        if isinstance(href, str):
            originals[_image_key(href)] = href
    photos = []
    seen = set()
    for image_url in image_urls:
        if not isinstance(image_url, str):
            raise MarketplaceError('The Craigslist photo collection is incomplete.')
        identity = _image_key(image_url)
        if identity not in seen:
            seen.add(identity)
            photos.append({'id': identity, 'url': originals.get(identity, image_url)})
    if originals and set(originals) != seen:
        raise MarketplaceError('The Craigslist photo collection is incomplete; saved photos were retained.')
    return {'url': canonical, 'title': title, 'fields': fields, 'photos': photos,
            'provider': 'craigslist', 'source_id': post_id.group(1) if post_id else canonical.rsplit('/', 1)[1],
            'attributes': attributes}