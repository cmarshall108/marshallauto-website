#!/usr/bin/env python3
"""List and scan printers on the computer viewing the admin page.

The public site cannot see a Mac's printers, and a browser cannot browse
Bonjour. Install this helper once so it starts at login:

    python3 scripts/install_local_printer_agent.py

It listens only on 127.0.0.1 and returns scanned pages to the browser.
"""
import json
import errno
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.local_scanners import ScannerError, list_scanners, scan_document

HOST = '127.0.0.1'
PORT = 8765
PORT_FALLBACK_COUNT = 10
MAX_REQUEST_BYTES = 4096
TOKEN_FILE = Path.home() / '.marshallauto' / 'local-printer-agent.token'


def _configured_origins():
    configured = os.environ.get('MARSHALL_AUTO_SCANNER_ALLOWED_ORIGINS')
    if configured:
        origins = [origin.strip() for origin in configured.split(',') if origin.strip()]
    else:
        origins = [os.environ.get('SITE_URL') or 'https://marshallautosanford.com']

    normalized = set()
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ('http', 'https')
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ('', '/')
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                'MARSHALL_AUTO_SCANNER_ALLOWED_ORIGINS must contain exact HTTP(S) origins.'
            )
        normalized.add(f'{parsed.scheme.lower()}://{parsed.netloc.lower()}')
    return frozenset(normalized)


ALLOWED_ORIGINS = _configured_origins()


def _load_or_create_token():
    TOKEN_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(TOKEN_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        token = TOKEN_FILE.read_text(encoding='ascii').strip()
    else:
        token = secrets.token_urlsafe(32)
        with os.fdopen(descriptor, 'w', encoding='ascii') as token_file:
            token_file.write(token)
    if len(token) < 40:
        raise RuntimeError(f'The local scanner token in {TOKEN_FILE} is invalid.')
    TOKEN_FILE.chmod(0o600)
    return token


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, content_type='application/json', headers=None):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        origin = self.headers.get('Origin')
        if origin in self.server.allowed_origins:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _allowed_origin(self):
        return self.headers.get('Origin') in self.server.allowed_origins

    def _authorized(self):
        scheme, separator, token = self.headers.get('Authorization', '').partition(' ')
        return (
            scheme == 'Bearer'
            and bool(separator)
            and secrets.compare_digest(token, self.server.auth_token)
        )

    def do_OPTIONS(self):
        requested_method = self.headers.get('Access-Control-Request-Method', '')
        requested_headers = {
            header.strip().lower()
            for header in self.headers.get('Access-Control-Request-Headers', '').split(',')
            if header.strip()
        }
        if (
            not self._allowed_origin()
            or self.path.split('?', 1)[0] not in ('/printers', '/scan')
            or requested_method not in ('GET', 'POST')
            or not requested_headers.issubset({'authorization', 'content-type'})
        ):
            self._send(403, {'error': 'Origin or preflight request is not allowed.'})
            return
        self._send(204, b'', headers={
            'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Authorization, Content-Type',
        })

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if not self._allowed_origin():
            self._send(403, {'error': 'Origin is not allowed.'})
            return
        if path == '/token':
            self._send(200, {'token': self.server.auth_token})
            return
        if path != '/printers':
            self._send(404, {'error': 'Not found'})
            return
        if not self._authorized():
            self._send(401, {'error': 'Authentication required.'})
            return
        try:
            self._send(200, {'printers': list_scanners()})
        except ScannerError as exc:
            self._send(503, {'error': str(exc)})

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/scan':
            self._send(404, {'error': 'Not found'})
            return
        if not self._allowed_origin():
            self._send(403, {'error': 'Origin is not allowed.'})
            return
        if not self._authorized():
            self._send(401, {'error': 'Authentication required.'})
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            self._send(400, {'error': 'Invalid request length.'})
            return
        if length < 0:
            self._send(400, {'error': 'Invalid request length.'})
            return
        if length > MAX_REQUEST_BYTES:
            self._send(413, {'error': 'Request is too large.'})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('A JSON object is required.')
            printer_id = str(payload.get('printer_id') or '')
            purpose = str(payload.get('purpose') or 'document')
            if purpose not in ('document', 'id'):
                raise ValueError('That scan type is not supported.')
            page = scan_document(printer_id, purpose=purpose)
        except (ScannerError, ValueError, json.JSONDecodeError) as exc:
            self._send(422, {'error': str(exc)})
            return
        content_type = 'application/pdf' if page.startswith(b'%PDF-') else 'image/jpeg'
        self._send(200, page, content_type)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))


def create_server():
    auth_token = _load_or_create_token()
    server = None
    for port in range(PORT, PORT + PORT_FALLBACK_COUNT + 1):
        try:
            server = ThreadingHTTPServer((HOST, port), Handler)
            break
        except OSError as exc:
            if exc.errno != errno.EADDRINUSE:
                raise
    if server is None:
        raise OSError(
            f'No available local scanner-agent port in range {PORT}-{PORT + PORT_FALLBACK_COUNT}.',
        )
    server.auth_token = auth_token
    server.allowed_origins = ALLOWED_ORIGINS
    return server


def main():
    server = create_server()
    print(
        f'Local printer agent listening on http://{HOST}:{server.server_address[1]}',
        flush=True,
    )
    server.serve_forever()


if __name__ == '__main__':
    main()
