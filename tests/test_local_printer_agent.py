import errno
import unittest
from unittest.mock import patch

from scripts import local_printer_agent


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


if __name__ == '__main__':
    unittest.main()
