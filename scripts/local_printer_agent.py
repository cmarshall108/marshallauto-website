#!/usr/bin/env python3
"""List and scan printers on the computer viewing the sale page.

The public site cannot see a Mac's printers. Run this beside the browser:

    python3 scripts/local_printer_agent.py

It listens only on 127.0.0.1 and returns the scanned PDF to the page.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.local_scanners import ScannerError, list_scanners, scan_document

HOST = '127.0.0.1'
PORT = 8765


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, content_type='application/json'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b'')

    def do_GET(self):
        if self.path.split('?', 1)[0] != '/printers':
            self._send(404, {'error': 'Not found'})
            return
        try:
            self._send(200, {'printers': list_scanners()})
        except ScannerError as exc:
            self._send(503, {'error': str(exc)})

    def do_POST(self):
        if self.path.split('?', 1)[0] != '/scan':
            self._send(404, {'error': 'Not found'})
            return
        length = int(self.headers.get('Content-Length') or 0)
        if length > 4096:
            self._send(413, {'error': 'Request is too large.'})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b'{}')
            printer_id = str(payload.get('printer_id') or '')
            page = scan_document(printer_id)
        except (ScannerError, ValueError, json.JSONDecodeError) as exc:
            self._send(422, {'error': str(exc)})
            return
        content_type = 'application/pdf' if page.startswith(b'%PDF-') else 'image/jpeg'
        self._send(200, page, content_type)

    def log_message(self, fmt, *args):
        sys.stderr.write('%s - %s\n' % (self.address_string(), fmt % args))


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f'Local printer agent listening on http://{HOST}:{PORT}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
