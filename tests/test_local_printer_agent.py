import errno
import plistlib
import unittest
from unittest.mock import patch

from scripts import install_local_printer_agent, local_printer_agent


class LocalPrinterAgentTests(unittest.TestCase):
    def test_uses_next_port_when_default_is_already_in_use(self):
        server = object()
        with patch.object(
            local_printer_agent,
            'ThreadingHTTPServer',
            side_effect=[OSError(errno.EADDRINUSE, 'Address already in use'), server],
        ) as bind:
            result = local_printer_agent.create_server()

        self.assertIs(result, server)
        self.assertEqual(bind.call_count, 2)
        self.assertEqual(bind.call_args_list[0].args[0], ('127.0.0.1', 8765))
        self.assertEqual(bind.call_args_list[1].args[0], ('127.0.0.1', 8766))

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
