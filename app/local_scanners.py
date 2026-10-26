"""Discover and scan printers on the computer running the admin browser.

A browser cannot see macOS printers. This helper is called by a small local
agent on that computer, which then posts the page back to the open sale.
"""
import socket
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

ESCL_NS = {
    'scan': 'http://schemas.hp.com/imaging/escl/2011/05/03',
    'pwg': 'http://www.pwg.org/schemas/2010/12/sm',
}
SCAN_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.6</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:Height>3300</pwg:Height>
      <pwg:Width>2550</pwg:Width>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
</scan:ScanSettings>
"""


class ScannerError(Exception):
    """The selected printer could not produce a scan."""


def _run(command, timeout):
    try:
        return subprocess.run(
            command, check=False, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScannerError(f'Could not query local printers: {exc}') from exc


def _printer_rows():
    result = _run(['lpstat', '-v'], timeout=8)
    if result.returncode:
        detail = (result.stderr or result.stdout or 'lpstat failed').strip()
        raise ScannerError(detail)
    rows = []
    for line in result.stdout.splitlines():
        if not line.startswith('device for '):
            continue
        name, _, uri = line.removeprefix('device for ').partition(': ')
        rows.append((name.strip(), uri.strip()))
    return rows


def _printer_description(name):
    result = _run(['lpstat', '-l', '-p', name], timeout=8)
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith('Description:'):
            return stripped.removeprefix('Description:').strip()
    return name.replace('_', ' ')


def _scanner_host(uri):
    parsed = urlparse(unquote(uri))
    service_name = parsed.netloc.split('._')[0].strip()
    if not service_name:
        return None
    try:
        lookup = subprocess.run(
            ['dns-sd', '-L', service_name, '_uscan._tcp', 'local.'],
            check=False, capture_output=True, text=True, timeout=5,
        )
        text = lookup.stdout
    except subprocess.TimeoutExpired as exc:
        text = exc.stdout or ''
        if isinstance(text, bytes):
            text = text.decode('utf-8', 'replace')
    except OSError:
        return None
    if 'reached at ' in text and '.local.:' in text:
        return text.split('reached at ', 1)[1].split('.local.:', 1)[0] + '.local'
    return None


def _curl(args, timeout, body=None):
    command = ['curl', '-sk', '--max-time', str(timeout), *args]
    try:
        return subprocess.run(
            command, input=body, check=False, capture_output=True, timeout=timeout + 2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScannerError(f'Could not reach the selected printer: {exc}') from exc


def _supports_escl(host):
    result = _curl([f'https://{host}:443/eSCL/ScannerCapabilities'], timeout=4)
    return result.returncode == 0 and b'ScannerCapabilities' in result.stdout


def list_scanners():
    """Return installed printers that can scan, with the idle one first."""
    scanners = []
    for name, uri in _printer_rows():
        host = _scanner_host(uri)
        if not host or not _supports_escl(host):
            continue
        scanners.append({
            'id': name,
            'label': _printer_description(name),
            'state': 'ready',
        })
    scanners.sort(key=lambda item: (item['label'] != 'EPSON XP-4250 Series', item['label']))
    return scanners


def _job_url(host, location):
    if not location:
        raise ScannerError('The printer accepted the scan but did not return a job.')
    if location.startswith('http://') or location.startswith('https://'):
        return location
    return f'https://{host}:443{location if location.startswith("/") else "/" + location}'


def _download_scan(job_url):
    for suffix in ('/NextDocument', '/NextDocument?timeout=30'):
        result = _curl(['-D', '-', f'{job_url}{suffix}'], timeout=90)
        body = result.stdout
        for marker in (b'\xff\xd8\xff', b'%PDF-'):
            start = body.find(marker)
            if start >= 0:
                return body[start:]
    raise ScannerError('The printer did not return a scanned page. Place the document on the glass and try again.')


def scan_document(printer_id):
    """Scan the platen of one installed printer and return PDF bytes."""
    printers = {name: uri for name, uri in _printer_rows()}
    if printer_id not in printers:
        raise ScannerError('That printer is not installed on this computer.')
    host = _scanner_host(printers[printer_id])
    if not host:
        raise ScannerError('That printer does not have a network scanner address.')
    created = _curl([
        '-D', '-',
        '-H', 'Content-Type: text/xml',
        '--data-binary', '@-',
        f'https://{host}:443/eSCL/ScanJobs',
    ], timeout=20, body=SCAN_SETTINGS.encode())
    header_blob, _, _ = created.stdout.partition(b'\r\n\r\n')
    header = header_blob.decode('latin1', 'replace')
    status_line = header.splitlines()[0] if header.splitlines() else ''
    if created.returncode or ' 201 ' not in status_line:
        raise ScannerError('The printer refused the scan. Confirm it is on and a page is on the glass.')
    location = ''
    for line in header.splitlines():
        if line.lower().startswith('location:'):
            location = line.split(':', 1)[1].strip()
    return _download_scan(_job_url(host, location))
