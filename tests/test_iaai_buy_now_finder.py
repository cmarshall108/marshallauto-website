import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.iaai_buy_now_finder import (
    Listing,
    _ensure_authenticated,
    _wait_for_search_page,
    parse_listing,
    render_report,
)


class IAAIBuyNowFinderTests(unittest.TestCase):
    def test_waits_for_user_sign_in_until_buyer_access_is_ready(self):
        page = Mock()
        page.evaluate.side_effect = [
            {
                "needsLogin": True,
                "hasSearch": False,
                "url": "https://login.iaai.com/Identity/Account/Login",
            },
            {
                "needsLogin": False,
                "hasSearch": True,
                "url": "https://www.iaai.com/Search",
            },
        ]

        with patch("builtins.input") as prompt:
            _wait_for_search_page(page)

        prompt.assert_called_once()
        self.assertEqual(page.wait_for_function.call_count, 2)

    def test_accepts_loaded_search_page_without_customize_button(self):
        page = Mock()
        page.evaluate.return_value = {
            "needsLogin": False,
            "hasSearch": True,
            "url": "https://www.iaai.com/Search",
        }

        with patch("builtins.input") as prompt:
            _wait_for_search_page(page)

        prompt.assert_not_called()

    def test_detail_page_sign_in_waits_until_buyer_prompt_disappears(self):
        page = Mock()
        page.evaluate.side_effect = [True, False]

        with patch("builtins.input") as prompt:
            _ensure_authenticated(page)

        prompt.assert_called_once()
        self.assertEqual(page.evaluate.call_count, 2)

    def test_parses_verified_listing_values_and_detail_url(self):
        listing = parse_listing(
            {
                "url": "/VehicleDetail/12345678~US",
                "title": "2018 Example Sedan",
                "text": "Stock #: 98765432\nBranch: Concord (North Carolina)",
                "titles": (
                    "Buy Now Price: $1,850\nOdometer: 42,123 mi\n"
                    "Seller: Example Insurance\nACV: $7,900"
                ),
            }
        )

        self.assertEqual(
            listing,
            Listing(
                title="2018 Example Sedan",
                url="https://www.iaai.com/VehicleDetail/12345678~US",
                buy_now_price=1850,
                acv=7900,
                odometer=42123,
                stock_number="98765432",
                location="North Carolina",
                seller_name="Example Insurance",
            ),
        )

    def test_marks_missing_price_and_rejects_non_iaai_links(self):
        unavailable_price = parse_listing(
            {
                "url": "/VehicleDetail/123~US",
                "title": "Unknown price",
                "text": "Odometer: 20,000 mi\nACV: $5,000",
                "titles": "",
            }
        )
        unsafe_url = parse_listing(
            {
                "url": "https://example.com/VehicleDetail/123",
                "title": "Unsafe listing",
                "text": "Buy Now: $1,000\nOdometer: 20,000 mi",
                "titles": "",
            }
        )

        self.assertIsNone(unavailable_price.buy_now_price)
        self.assertIsNone(unsafe_url)

    def test_report_sorts_acv_descending_and_escapes_vehicle_text(self):
        listings = [
            Listing("Lower ACV", "https://www.iaai.com/VehicleDetail/1", 1000, 2000, 1000, "", "", ""),
            Listing("<script>Vehicle</script>", "https://www.iaai.com/VehicleDetail/2", 1500, 9000, 2000, "2", "Virginia", ""),
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = Path(temporary_directory) / "report.html"
            result_path = render_report(listings, output_path)
            report = output_path.read_text(encoding="utf-8")

        self.assertEqual(result_path, str(output_path.resolve()))
        self.assertLess(report.index("&lt;script&gt;Vehicle"), report.index("Lower ACV"))
        self.assertNotIn("<script>Vehicle</script>", report)
        self.assertIn("$9,000", report)
        self.assertIn("highest ACV first", report)


if __name__ == "__main__":
    unittest.main()
