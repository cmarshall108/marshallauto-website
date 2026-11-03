import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import local_scanners


class LocalScannerTests(unittest.TestCase):
    def test_lists_network_scanner_without_requiring_cups_printer(self):
        def dns_sd_output(args, timeout=2):
            if args[0] == '-B':
                service_type = args[1]
                return (
                    f'11:06:09.969 Add 2 5 local. {service_type}. Network Scanner\n'
                )
            service_type = args[2]
            return (
                f'Lookup Network Scanner.{service_type}.local.\n'
                'Network Scanner can be reached at scanner.local.:443 (interface 5)\n'
            )

        with patch.object(
            local_scanners, '_dns_sd_output', side_effect=dns_sd_output,
        ), patch.object(local_scanners, '_supports_escl', return_value=True):
            scanners = local_scanners.list_scanners()

        self.assertEqual(scanners, [{
            'id': '_uscans._tcp|Network Scanner',
            'label': 'Network Scanner',
            'state': 'ready',
        }])

    def test_resolves_plain_escl_service_using_its_advertised_port(self):
        with patch.object(
            local_scanners,
            '_dns_sd_output',
            return_value='Scanner can be reached at scanner.local.:8080 (interface 5)\n',
        ):
            scanner = local_scanners._resolve_scanner('_uscan._tcp', 'Network Scanner')

        self.assertEqual(scanner, {
            'host': 'scanner.local',
            'port': 8080,
            'scheme': 'http',
        })

    def test_scan_uses_the_advertised_network_endpoint(self):
        scanner = {
            'id': '_uscan._tcp|Network Scanner',
            'label': 'Network Scanner',
            'host': 'scanner.local',
            'port': 80,
            'scheme': 'http',
        }
        created = SimpleNamespace(
            returncode=0,
            stdout=b'HTTP/1.1 201 Created\r\nLocation: /eSCL/ScanJobs/1\r\n\r\n',
        )
        with patch.object(
            local_scanners, '_discover_scanners', return_value=[scanner],
        ), patch.object(local_scanners, '_curl', return_value=created) as curl, patch.object(
            local_scanners, '_download_scan', return_value=b'\xff\xd8\xffscan',
        ) as download, patch.object(local_scanners, '_close_scan') as close:
            result = local_scanners.scan_document(scanner['id'])

        self.assertEqual(result, b'\xff\xd8\xffscan')
        self.assertIn('http://scanner.local:80/eSCL/ScanJobs', curl.call_args.args[0])
        download.assert_called_once_with('http://scanner.local:80/eSCL/ScanJobs/1')
        close.assert_called_once_with('http://scanner.local:80/eSCL/ScanJobs/1')


if __name__ == '__main__':
    unittest.main()
