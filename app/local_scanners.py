"""Discover and scan printers on the computer running the admin browser.

A browser cannot see macOS printers. This helper is called by a small local
agent on that computer, which then posts the page back to the open sale.
"""
import re
import subprocess
from urllib.parse import urljoin

ESCL_NS = {
    'scan': 'http://schemas.hp.com/imaging/escl/2011/05/03',
    'pwg': 'http://www.pwg.org/schemas/2010/12/sm',
}
SCANNER_SERVICES = ('_uscans._tcp', '_uscan._tcp')
# Letter glass. Units are 1/300 inch and do not change with scan resolution.
PLATEN_REGION = (2550, 3300, 0, 0)


def _scan_settings(resolution, width, height, x_offset=0, y_offset=0):
    resolution = int(resolution)
    if resolution not in (300, 600):
        raise ScannerError('That scan resolution is not supported.')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.6</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:Height>{int(height)}</pwg:Height>
      <pwg:Width>{int(width)}</pwg:Width>
      <pwg:XOffset>{int(x_offset)}</pwg:XOffset>
      <pwg:YOffset>{int(y_offset)}</pwg:YOffset>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
  <scan:XResolution>{resolution}</scan:XResolution>
  <scan:YResolution>{resolution}</scan:YResolution>
</scan:ScanSettings>
"""


class ScannerError(Exception):
    """The selected printer could not produce a scan."""


def _dns_sd_output(args, timeout=2):
    try:
        result = subprocess.run(
            ['dns-sd', *args], check=False, capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or 'dns-sd failed').strip()
            raise ScannerError(f'Could not discover local network scanners: {detail}')
        return result.stdout
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ''
        return output.decode('utf-8', 'replace') if isinstance(output, bytes) else output
    except OSError as exc:
        raise ScannerError(f'Could not discover local network scanners: {exc}') from exc


def _browse_scanners(service_type):
    output = _dns_sd_output(['-B', service_type, 'local.'])
    services = set()
    for line in output.splitlines():
        parts = line.split()
        if (
            len(parts) >= 7
            and parts[1] == 'Add'
            and parts[4] == 'local.'
            and parts[5].rstrip('.') == service_type
        ):
            services.add(' '.join(parts[6:]))
    return services


def _resolve_scanner(service_type, name):
    output = _dns_sd_output(['-L', name, service_type, 'local.'])
    match = re.search(r'can be reached at (.+):(\d+)(?:\s|$)', output)
    if not match:
        return None
    host = match.group(1).strip().rstrip('.')
    port = int(match.group(2))
    if not host or not 1 <= port <= 65535:
        return None
    return {
        'host': host,
        'port': port,
        'scheme': 'https' if service_type == '_uscans._tcp' or port == 443 else 'http',
    }


def _scanner_url(scanner, path):
    host = scanner['host']
    if ':' in host and not host.startswith('['):
        host = f'[{host}]'
    return f"{scanner['scheme']}://{host}:{scanner['port']}{path}"


def _curl(args, timeout, body=None):
    command = ['curl', '-sk', '--max-time', str(timeout), *args]
    try:
        return subprocess.run(
            command, input=body, check=False, capture_output=True, timeout=timeout + 2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScannerError(f'Could not reach the selected printer: {exc}') from exc


def _supports_escl(scanner):
    result = _curl([_scanner_url(scanner, '/eSCL/ScannerCapabilities')], timeout=4)
    return result.returncode == 0 and b'ScannerCapabilities' in result.stdout


def _discover_scanners():
    scanners_by_host = {}
    for service_type in SCANNER_SERVICES:
        for name in _browse_scanners(service_type):
            address = _resolve_scanner(service_type, name)
            if not address:
                continue
            scanner = {
                **address,
                'id': f'{service_type}|{name}',
                'label': name,
            }
            if not _supports_escl(scanner):
                continue
            host_key = scanner['host'].casefold()
            existing = scanners_by_host.get(host_key)
            if not existing or (scanner['scheme'] == 'https' and existing['scheme'] != 'https'):
                scanners_by_host[host_key] = scanner
    return sorted(scanners_by_host.values(), key=lambda item: item['label'].casefold())


def list_scanners():
    """Return network scanners advertising eSCL, whether or not CUPS has them."""
    return [
        {'id': scanner['id'], 'label': scanner['label'], 'state': 'ready'}
        for scanner in _discover_scanners()
    ]


def _job_url(scanner, location):
    if not location:
        raise ScannerError('The printer accepted the scan but did not return a job.')
    if location.startswith('http://') or location.startswith('https://'):
        return location
    return urljoin(_scanner_url(scanner, '/'), location)


def _download_scan(job_url):
    for suffix in ('/NextDocument', '/NextDocument?timeout=30'):
        result = _curl(['-D', '-', f'{job_url}{suffix}'], timeout=90)
        body = result.stdout
        for marker in (b'\xff\xd8\xff', b'%PDF-'):
            start = body.find(marker)
            if start >= 0:
                return body[start:]
    raise ScannerError('The printer did not return a scanned page. Place the document on the glass and try again.')


def _close_scan(job_url):
    """Tell the printer this page is finished so its display leaves Scanning."""
    _curl(['-X', 'DELETE', job_url], timeout=8)


def scan_document(printer_id, *, purpose='document'):
    """Scan the platen of one network scanner and return image or PDF bytes.

    Customer IDs use the scanner's 600 dpi photo setting across the glass so
    the portrait and small text stay readable. Sale documents stay at 300 dpi.
    """
    scanner = next(
        (item for item in _discover_scanners() if item['id'] == printer_id),
        None,
    )
    if not scanner:
        raise ScannerError('That scanner is no longer available on the local network.')
    created = _curl([
        '-D', '-',
        '-H', 'Content-Type: text/xml',
        '--data-binary', '@-',
        _scanner_url(scanner, '/eSCL/ScanJobs'),
    ], timeout=20, body=_scan_settings(
        600 if purpose == 'id' else 300,
        *PLATEN_REGION,
    ).encode())
    header_blob, _, _ = created.stdout.partition(b'\r\n\r\n')
    header = header_blob.decode('latin1', 'replace')
    status_line = header.splitlines()[0] if header.splitlines() else ''
    if created.returncode or ' 201 ' not in status_line:
        raise ScannerError('The printer refused the scan. Confirm it is on and a page is on the glass.')
    location = ''
    for line in header.splitlines():
        if line.lower().startswith('location:'):
            location = line.split(':', 1)[1].strip()
    job_url = _job_url(scanner, location)
    try:
        return _download_scan(job_url)
    finally:
        _close_scan(job_url)
