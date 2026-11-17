import errno
import http.client
import json
import plistlib
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import install_local_printer_agent, local_printer_agent


class LocalPrinterAgentTests(unittest.TestCase):
    def test_uses_next_port_when_default_is_already_in_use(self):
        server = SimpleNamespace()
        with patch.object(
            local_printer_agent,
            'ThreadingHTTPServer',
            side_effect=[OSError(errno.EADDRINUSE, 'Address already in use'), server],
        ) as bind, patch.object(local_printer_agent, '_load_or_create_token', return_value='x' * 43):
            result = local_printer_agent.create_server()

        self.assertIs(result, server)
        self.assertEqual(bind.call_count, 2)
        self.assertEqual(bind.call_args_list[0].args[0], ('127.0.0.1', 8765))
        self.assertEqual(bind.call_args_list[1].args[0], ('127.0.0.1', 8766))
        self.assertEqual(result.auth_token, 'x' * 43)

    def test_persists_a_private_random_token(self):
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / 'agent.token'
            with patch.object(local_printer_agent, 'TOKEN_FILE', token_path):
                first = local_printer_agent._load_or_create_token()
                second = local_printer_agent._load_or_create_token()

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 40)
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)

    def test_scanner_routes_require_allowed_origin_and_bearer_token(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), local_printer_agent.Handler)
        server.auth_token = 'test-token-with-sufficient-length'
        server.allowed_origins = frozenset({'https://marshallautosanford.com'})
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def request(method, path, headers=None, body=None):
                connection = http.client.HTTPConnection(*server.server_address)
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                result = response.status, response.getheaders(), response.read()
                connection.close()
                return result

            status, headers, body = request('GET', '/token', {'Origin': 'https://attacker.example'})
            self.assertEqual(status, 403)
            self.assertNotIn('Access-Control-Allow-Origin', dict(headers))
            self.assertNotIn(server.auth_token.encode(), body)

            status, _, _ = request(
                'GET', '/printers', {'Origin': 'https://marshallautosanford.com'},
            )
            self.assertEqual(status, 401)

            status, headers, _ = request(
                'OPTIONS',
                '/scan',
                {
                    'Origin': 'https://marshallautosanford.com',
                    'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'authorization,content-type',
                },
            )
            self.assertEqual(status, 204)
            self.assertEqual(
                dict(headers)['Access-Control-Allow-Headers'],
                'Authorization, Content-Type',
            )

            status, headers, _ = request(
                'OPTIONS',
                '/scan',
                {
                    'Origin': 'https://attacker.example',
                    'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'authorization,content-type',
                },
            )
            self.assertEqual(status, 403)
            self.assertNotIn('Access-Control-Allow-Origin', dict(headers))

            status, headers, body = request(
                'GET', '/token', {'Origin': 'https://marshallautosanford.com'},
            )
            self.assertEqual(status, 200)
            self.assertEqual(
                dict(headers)['Access-Control-Allow-Origin'],
                'https://marshallautosanford.com',
            )
            self.assertEqual(json.loads(body)['token'], server.auth_token)

            with patch.object(local_printer_agent, 'list_scanners', return_value=[]):
                status, _, body = request(
                    'GET',
                    '/printers',
                    {
                        'Origin': 'https://marshallautosanford.com',
                        'Authorization': f'Bearer {server.auth_token}',
                    },
                )
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {'printers': []})

            with patch.object(local_printer_agent, 'scan_document') as scan:
                status, _, _ = request(
                    'POST',
                    '/scan',
                    {
                        'Origin': 'https://marshallautosanford.com',
                        'Authorization': 'Bearer wrong-token',
                        'Content-Type': 'application/json',
                    },
                    body='{"printer_id":"scanner"}',
                )
            self.assertEqual(status, 401)
            scan.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_login_agent_starts_the_loopback_helper(self):
        definition = install_local_printer_agent.launch_agent_definition(
            '/usr/bin/python3',
            '/Users/shop/marshallauto-website/scripts/local_printer_agent.py',
            '/Users/shop/marshallauto-website',
            '/Users/shop/Library/Logs/marshallauto-local-printer-agent.log',
        )
        encoded = plistlib.dumps(definition).decode()

        self.assertEqual(definition['Label'], 'com.marshallauto.local-printer-agent')
        self.assertTrue(definition['RunAtLoad'])
        self.assertTrue(definition['KeepAlive'])
        self.assertEqual(definition['ProgramArguments'], [
            '/usr/bin/python3',
            '/Users/shop/marshallauto-website/scripts/local_printer_agent.py',
        ])
        self.assertNotIn('0.0.0.0', encoded)
        self.assertNotIn('python3 scripts/local_printer_agent.py', encoded)


if __name__ == '__main__':
    unittest.main()
