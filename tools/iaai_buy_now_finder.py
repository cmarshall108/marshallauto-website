#!/usr/bin/env python3
"""Collect IAAI Buy Now listings into a local, sortable-by-ACV HTML report."""

from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse


IAAI_LISTING_URL = "https://www.iaai.com/VehicleListing"
TARGET_STATES = ("North Carolina", "South Carolina", "Virginia")
MAX_BUY_NOW_PRICE = 1999
MAX_ODOMETER = 79999
MAX_RESULT_PAGES = 500
PROFILE_DIR = Path.home() / ".iaai-buy-now-finder" / "browser-profile"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "iaai_buy_now_results.html"


@dataclass(frozen=True)
class Listing:
    title: str
    url: str
    buy_now_price: int | None
    acv: int | None
    odometer: int | None
    stock_number: str
    location: str
    seller_name: str


def _money_value(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1).replace(",", "").split(".", 1)[0])


def parse_listing(raw: dict[str, str]) -> Listing | None:
    """Parse the visible text collected from one IAAI result card."""
    text = "\n".join((raw.get("text", ""), raw.get("titles", "")))
    price = _money_value(
        r"\bBuy\s*Now(?:\s*Price)?\s*:?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
        text,
    )
    odometer_match = re.search(
        r"\bOdometer\s*:?\s*([\d,]+)\s*(?:mi|miles)?\b", text, re.IGNORECASE,
    )
    acv = _money_value(r"\bACV\s*:?\s*\$?\s*([\d,]+(?:\.\d{1,2})?)", text)
    detail_url = urljoin(IAAI_LISTING_URL, raw.get("url", ""))
    parsed_url = urlparse(detail_url)
    if parsed_url.scheme != "https" or parsed_url.netloc != "www.iaai.com":
        return None
    if not parsed_url.path.startswith("/VehicleDetail/"):
        return None

    title = raw.get("title", "").strip()
    if not title:
        title = next(
            (line.strip() for line in raw.get("text", "").splitlines() if line.strip()),
            "Vehicle listing",
        )
    stock_match = re.search(r"\bStock\s*#\s*:?\s*([\w-]+)", text, re.IGNORECASE)
    location = next(
        (
            state
            for state in TARGET_STATES
            if re.search(rf"\b{re.escape(state)}\b", text, re.IGNORECASE)
        ),
        "",
    )
    seller_match = re.search(r"\bSeller\s*:?\s*([^\n]+)", text, re.IGNORECASE)

    return Listing(
        title=title,
        url=detail_url,
        buy_now_price=price,
        acv=acv,
        odometer=(
            int(odometer_match.group(1).replace(",", ""))
            if odometer_match
            else None
        ),
        stock_number=stock_match.group(1) if stock_match else "",
        location=location,
        seller_name=seller_match.group(1).strip() if seller_match else "",
    )


def _safe_link(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc == "www.iaai.com":
        return url
    return ""


def render_report(
    listings: list[Listing],
    output_path: Path,
    skipped_count: int = 0,
) -> str:
    """Write a self-contained HTML report and return its absolute path."""
    ordered = sorted(
        listings,
        key=lambda item: (item.acv is not None, item.acv if item.acv is not None else -1),
        reverse=True,
    )
    generated = datetime.now().astimezone().strftime("%B %-d, %Y at %-I:%M %p %Z")
    rows: list[str] = []
    for item in ordered:
        title = html.escape(item.title)
        link = _safe_link(item.url)
        vehicle_cell = (
            f'<a href="{html.escape(link, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{title}</a>'
            if link
            else title
        )
        if item.stock_number:
            vehicle_cell += (
                f'<small>Stock #: {html.escape(item.stock_number)}</small>'
            )
        if item.seller_name:
            seller = f"Insurance · {html.escape(item.seller_name)}"
        else:
            seller = "Insurance"
        acv = f"${item.acv:,.0f}" if item.acv is not None else "Not shown"
        buy_now = (
            f"${item.buy_now_price:,.0f}"
            if item.buy_now_price is not None
            else "Not shown"
        )
        rows.append(
            "<tr>"
            f"<td class=\"money\">{acv}</td>"
            f"<td class=\"money\">{buy_now}</td>"
            f"<td>{vehicle_cell}</td>"
            f"<td>{item.odometer:,} mi</td>"
            f"<td>{html.escape(item.location) if item.location else '—'}</td>"
            f"<td>{seller}</td>"
            "</tr>"
        )

    warning = (
        f'<p class="notice">{skipped_count} result(s) were not included because '
        "their visible Buy Now price or odometer could not be verified.</p>"
        if skipped_count
        else ""
    )
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>IAAI Buy Now Finder</title>
  <style>
    :root {{ color-scheme: light; font: 15px/1.5 system-ui, sans-serif; color: #24272b; background: #f4f5f7; }}
    body {{ margin: 0; padding: 32px; }}
    main {{ max-width: 1250px; margin: auto; }}
    header, .table-wrap {{ background: white; border: 1px solid #e1e4e8; border-radius: 12px; }}
    header {{ padding: 24px 28px; margin-bottom: 18px; }}
    h1 {{ margin: 0 0 6px; font-size: 1.6rem; }}
    .muted {{ color: #616975; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 14px; }}
    .notice {{ color: #7a4c00; background: #fff6df; padding: 10px 14px; border-radius: 8px; }}
    .table-wrap {{ overflow-x: auto; }}
    table {{ width: 100%; border-collapse: collapse; min-width: 850px; }}
    th, td {{ padding: 13px 15px; border-bottom: 1px solid #e9ebee; text-align: left; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #f8f9fa; font-size: .82rem; text-transform: uppercase; letter-spacing: .04em; }}
    tr:last-child td {{ border-bottom: 0; }}
    td.money {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
    td:first-child {{ font-weight: 700; color: #255b42; }}
    a {{ color: #174f86; font-weight: 650; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    small {{ display: block; color: #69717b; margin-top: 3px; }}
    .empty {{ padding: 24px; text-align: center; }}
    @media (max-width: 650px) {{ body {{ padding: 14px; }} header {{ padding: 18px; }} }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>IAAI Buy Now opportunities</h1>
      <div class="muted">Generated {html.escape(generated)} · {len(ordered)} verified listing(s)</div>
      <div class="summary">
        <span><strong>Buy Now:</strong> under $2,000</span>
        <span><strong>Seller Type:</strong> Insurance</span>
        <span><strong>Location:</strong> North Carolina, South Carolina, Virginia</span>
        <span><strong>Odometer:</strong> under 80,000 miles</span>
        <span><strong>Order:</strong> highest ACV first</span>
      </div>
      {warning}
    </header>
    <section class="table-wrap">
      <table>
        <thead><tr><th>ACV</th><th>Buy Now</th><th>Vehicle</th><th>Odometer</th><th>State</th><th>Seller</th></tr></thead>
        <tbody>
          {''.join(rows) if rows else '<tr><td colspan="6" class="empty">No matching listings were found.</td></tr>'}
        </tbody>
      </table>
    </section>
    <p class="muted">Listing details and prices can change on IAAI. Confirm availability, fees, title status, and condition on the original listing before bidding.</p>
  </main>
</body>
</html>
"""
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return str(output_path)


def _normalize_option(text: str) -> str:
    return re.sub(r"\s+\(\d[\d,]*\)\s*$", "", text.strip()).casefold()


def _wait_for_results(page: object) -> None:
    page.wait_for_timeout(900)


def _expand_facet(page: object, facet_id: str) -> object:
    panel = page.locator(f"#{facet_id}")
    if not panel.count():
        raise RuntimeError(f"IAAI filter panel #{facet_id} was not found.")
    if not panel.is_visible():
        header = page.locator(f'a[href="#{facet_id}"]')
        if not header.count():
            raise RuntimeError(f"Could not open the IAAI {facet_id} filter.")
        header.first.click()
    return panel


def _set_facet_options(
    page: object,
    facet_id: str,
    desired_options: tuple[str, ...],
) -> None:
    wanted = {value.casefold() for value in desired_options}
    panel = _expand_facet(page, facet_id)

    for _ in range(100):
        inputs = panel.locator('input[type="checkbox"]')
        selected: list[tuple[object, str]] = []
        visible_options: set[str] = set()
        for index in range(inputs.count()):
            checkbox = inputs.nth(index)
            option_id = checkbox.get_attribute("id")
            label = (
                panel.locator(f'label[for="{option_id}"]')
                if option_id
                else checkbox.locator("xpath=..")
            )
            label_text = label.inner_text().strip() if label.count() else ""
            normalized = _normalize_option(label_text)
            visible_options.add(normalized)
            if checkbox.is_checked():
                selected.append((checkbox, normalized))

        unwanted = next(
            ((checkbox, name) for checkbox, name in selected if name not in wanted),
            None,
        )
        if unwanted:
            option_id = unwanted[0].get_attribute("id")
            if not option_id:
                raise RuntimeError(
                    f"Cannot identify a selected option in #{facet_id} to clear it."
                )
            panel.locator(f'label[for="{option_id}"]').click()
            _wait_for_results(page)
            panel = page.locator(f"#{facet_id}")
            continue

        missing = wanted - {name for _, name in selected}
        if not missing:
            return
        if not missing.issubset(visible_options):
            raise RuntimeError(
                f"IAAI did not show filter option(s) {sorted(missing)} in #{facet_id}."
            )

        option = next(iter(missing))
        clicked = False
        inputs = panel.locator('input[type="checkbox"]')
        for index in range(inputs.count()):
            checkbox = inputs.nth(index)
            option_id = checkbox.get_attribute("id")
            label = (
                panel.locator(f'label[for="{option_id}"]')
                if option_id
                else checkbox.locator("xpath=..")
            )
            if label.count() and _normalize_option(label.inner_text()) == option:
                label.click()
                clicked = True
                break
        if not clicked:
            raise RuntimeError(f"Could not select IAAI filter option {option!r}.")
        _wait_for_results(page)
        panel = page.locator(f"#{facet_id}")

    raise RuntimeError(f"Could not settle IAAI filter selections in #{facet_id}.")


def _apply_range(page: object, facet_id: str, maximum: int, button_name: str) -> None:
    panel = _expand_facet(page, facet_id)
    lower = panel.locator(f"#{facet_id}From")
    upper = panel.locator(f"#{facet_id}To")
    if not lower.count() or not upper.count():
        raise RuntimeError(f"IAAI range fields for #{facet_id} were not found.")
    lower.fill("0")
    upper.fill(str(maximum))
    upper.press("Tab")
    page.get_by_role("button", name=f"Apply {button_name}", exact=True).click()
    _wait_for_results(page)


def _ensure_authenticated(page: object) -> None:
    prompted = False
    while True:
        auth_state = page.evaluate(
            """() => {
              const text = (document.body?.innerText || "").toLowerCase();
              return location.hostname.toLowerCase().startsWith("login.")
                || text.includes("please log in as a buyer");
            }"""
        )
        if not auth_state:
            return
        if not prompted:
            print("IAAI buyer sign-in is required to read seller and ACV details.")
            prompted = True
        input(
            "Sign in (and complete MFA) in the browser. The finder will wait here; "
            "press Enter after sign-in: "
        )


def _wait_for_search_page(page: object) -> None:
    while True:
        page.wait_for_function(
            """() => {
              const host = location.hostname.toLowerCase();
              return host === "login.iaai.com"
                || host === "www.iaai.com"
                || host === "iaai.com";
            }""",
            timeout=90000,
        )
        state = page.evaluate(
            """() => {
              const text = (document.body?.innerText || "").toLowerCase();
              const host = location.hostname.toLowerCase();
              return {
                needsLogin: host === "login.iaai.com"
                  || text.includes("please log in as a buyer"),
                hasSearch: (host === "www.iaai.com" || host === "iaai.com")
                  && !!document.querySelector("#AuctionTypeFilter")
                  && !!document.querySelector('a[href*="/VehicleDetail/"]'),
                url: location.href
              };
            }"""
        )
        if state["hasSearch"] and not state["needsLogin"]:
            return
        if state["needsLogin"]:
            print("IAAI sign-in is required. Complete sign-in and MFA in Chrome.")
            input(
                "The finder is waiting while Chrome stays open. Complete sign-in "
                "and MFA there; press Enter when finished: "
            )
        else:
            print(f"Waiting for IAAI Vehicles → Search to finish loading ({state['url']}).")
            input(
                "Chrome will remain open. If needed, sign in and open Vehicles → "
                "Search, then press Enter to check again: "
            )
            page.wait_for_timeout(1000)


def _configure_result_list(page: object) -> None:
    customize_button = page.get_by_role("button", name="Customize List", exact=True)
    for retry in range(2):
        for _ in range(45):
            if customize_button.count():
                break
            page.wait_for_timeout(1000)
        if customize_button.count():
            break
        if retry:
            raise RuntimeError(
                "IAAI did not expose the Customize List control after waiting "
                "for sign-in and page loading. Verify that Vehicles → Search is "
                "fully loaded in Chrome, then run the finder again."
            )
        _ensure_authenticated(page)
        input(
            "Chrome remains open. Sign in if needed and open Vehicles → Search. "
            "Press Enter when ready; the finder will wait for the page controls: "
        )
        _wait_for_search_page(page)
        customize_button = page.get_by_role(
            "button", name="Customize List", exact=True,
        )
    page.get_by_role("button", name="Customize List", exact=True).evaluate(
        "(button) => button.click()"
    )
    dialog = page.get_by_role("dialog")
    dialog.wait_for(state="visible", timeout=15000)
    for checkbox_id in (
        "customizedSeller",
        "customizedSellerType",
        "customizedOdometer",
        "customizedAvc",
    ):
        checkbox = dialog.locator(f"#{checkbox_id}")
        if not checkbox.count():
            raise RuntimeError(
                f"The IAAI result-list option #{checkbox_id} was not found."
            )
        if not checkbox.is_checked():
            dialog.locator(f'label[for="{checkbox_id}"]').click()

    primary_sort = dialog.get_by_role("combobox", name="Primary sort")
    primary_sort.select_option(label="ACV (Highest First)")
    dialog.get_by_role("button", name="Update List", exact=True).evaluate(
        "(button) => button.click()"
    )
    dialog.wait_for(state="hidden", timeout=15000)
    page.wait_for_selector("#ProviderTypeFilter", state="attached", timeout=15000)
    _wait_for_results(page)


def _collect_page_cards(page: object) -> list[dict[str, str]]:
    return page.evaluate(
        """() => {
          const rows = [...document.querySelectorAll(".js-intro-result-table")];
          const visited = new Set();
          const cards = [];
          for (const row of rows) {
            const link = row.querySelector('a[href*="/VehicleDetail/"]');
            if (!link) continue;
            const parsedLink = new URL(link.href, location.href);
            const identity = parsedLink.origin + parsedLink.pathname;
            if (visited.has(identity)) continue;
            visited.add(identity);
            const title = row.querySelector("h1,h2,h3,h4,h5");
            cards.push({
              url: identity,
              title: title ? title.innerText.trim() : link.innerText.trim(),
              text: row.innerText || "",
              titles: [...row.querySelectorAll("[title]")].map(item => item.title).join("\\n")
            });
          }
          return cards;
        }"""
    )


def _read_detail_listing(page: object, listing: Listing) -> Listing:
    page.goto(listing.url, wait_until="domcontentloaded", timeout=45000)
    _ensure_authenticated(page)
    page.wait_for_selector("h1", timeout=30000)
    page.wait_for_timeout(600)
    raw = page.evaluate(
        """() => ({
          url: location.href,
          title: document.querySelector("h1")?.innerText || "",
          text: document.body.innerText || "",
          titles: [...document.querySelectorAll("[title]")]
            .map(item => item.title).join("\\n")
        })"""
    )
    detail = parse_listing(raw)
    if detail is None:
        return listing
    return replace(
        listing,
        buy_now_price=detail.buy_now_price,
        acv=listing.acv if listing.acv is not None else detail.acv,
        odometer=listing.odometer if listing.odometer is not None else detail.odometer,
        stock_number=listing.stock_number or detail.stock_number,
        location=listing.location or detail.location,
        seller_name=listing.seller_name or detail.seller_name,
    )


def _collect_all_listings(page: object) -> tuple[list[Listing], int]:
    listings: dict[str, Listing] = {}
    skipped = 0
    detail_page = None
    try:
        for page_number in range(MAX_RESULT_PAGES):
            raw_cards = _collect_page_cards(page)
            if not raw_cards and page_number == 0:
                raise RuntimeError(
                    "No IAAI vehicle result cards were found after filtering. "
                    "Confirm you are signed in and on Vehicles → Search."
                )
            for raw in raw_cards:
                listing = parse_listing(raw)
                if listing is None:
                    skipped += 1
                    continue
                if listing.buy_now_price is None or listing.odometer is None:
                    if detail_page is None:
                        detail_page = page.context.new_page()
                        detail_page.set_default_timeout(15000)
                    listing = _read_detail_listing(detail_page, listing)
                if listing.buy_now_price is None or listing.odometer is None:
                    skipped += 1
                    continue
                if (
                    0 <= listing.buy_now_price <= MAX_BUY_NOW_PRICE
                    and listing.odometer <= MAX_ODOMETER
                ):
                    listings[listing.url] = listing

            next_button = page.get_by_role("button", name="Next", exact=True)
            if not next_button.count() or next_button.is_disabled():
                return list(listings.values()), skipped

            first_url = raw_cards[0]["url"] if raw_cards else ""
            next_button.click()
            page.wait_for_function(
                """oldUrl => {
                  const first = document.querySelector('a[href*="/VehicleDetail/"]');
                  if (!first) return false;
                  const url = new URL(first.href, location.href);
                  return url.origin + url.pathname !== oldUrl;
                }""",
                arg=first_url,
                timeout=30000,
            )
            _wait_for_results(page)

        raise RuntimeError(
            f"Stopped after {MAX_RESULT_PAGES} pages to avoid an unbounded collection."
        )
    finally:
        if detail_page is not None and not detail_page.is_closed():
            detail_page.close()


def _find_browser_executable() -> str | None:
    for command in ("google-chrome", "google-chrome-stable", "chrome"):
        executable = shutil.which(command)
        if executable:
            return executable
    if sys.platform == "darwin":
        chrome_app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if Path(chrome_app).is_file():
            return chrome_app
    for command in ("chromium", "chromium-browser"):
        executable = shutil.which(command)
        if executable:
            return executable
    if sys.platform == "darwin":
        chromium_app = "/Applications/Chromium.app/Contents/MacOS/Chromium"
        if Path(chromium_app).is_file():
            return chromium_app
    return None


def _run(output_path: Path) -> str:
    try:
        from playwright.sync_api import (
            Error as PlaywrightError,
            TimeoutError as PlaywrightTimeoutError,
            sync_playwright,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: "
            "python3 -m pip install -r tools/requirements-iaai.txt"
        ) from exc

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    executable = _find_browser_executable()
    context = None
    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": False}
            if executable:
                launch_options["executable_path"] = executable
            context = playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                **launch_options,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.set_default_timeout(15000)
                page.goto(IAAI_LISTING_URL, wait_until="domcontentloaded", timeout=60000)
                _wait_for_search_page(page)

                _set_facet_options(page, "AuctionTypeFilter", ("Buy Now",))
                _configure_result_list(page)
                _set_facet_options(page, "ProviderTypeFilter", ("Insurance",))
                _set_facet_options(page, "StateDescFilter", TARGET_STATES)
                _apply_range(page, "ODOValueFilter", MAX_ODOMETER, "Odometer")
                _apply_range(page, "BuyNowRangeFilter", MAX_BUY_NOW_PRICE, "Buy Now Price")
                listings, skipped = _collect_all_listings(page)

                result_path = render_report(listings, output_path, skipped)
                report_page = context.new_page()
                report_page.goto(Path(result_path).as_uri())
                print(
                    f"Found {len(listings)} verified listing(s); "
                    f"skipped {skipped} unverifiable result(s)."
                )
                print(f"Local report: {result_path}")
                input("The report is open in Chrome. Press Enter to close the browser: ")
                return result_path
            finally:
                primary_error = sys.exc_info()[0] is not None
                if context is not None:
                    try:
                        context.close()
                    except PlaywrightError as exc:
                        if not primary_error:
                            raise
                        print(f"Browser cleanup warning: {exc}", file=sys.stderr)
    except (PlaywrightTimeoutError, PlaywrightError) as exc:
        raise RuntimeError(
            "IAAI page interaction did not complete. The site may need a sign-in, "
            "MFA/CAPTCHA step, or may have changed its controls. Resolve any prompt "
            "in the browser and run this tool again."
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"HTML report destination (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()
    try:
        _run(args.output)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"IAAI finder: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
