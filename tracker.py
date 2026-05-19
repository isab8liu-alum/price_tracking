"""
Unified price tracker.
Supports two item types:
  - "url"    : scrape a specific product page for price/availability
  - "search" : run a Google Shopping search by keyword and return top hits
"""

import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright, Page


# ── email ──────────────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT") or "587")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    email_from = os.environ.get("EMAIL_FROM", smtp_user)
    email_to = os.environ.get("EMAIL_TO", "")

    if not (smtp_host and smtp_user and smtp_pass and email_to):
        print(f"[email] SMTP not configured, skipping: {subject}", file=sys.stderr)
        return

    msg = EmailMessage()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    print(f"[email] Sent: {subject}")


# ── state ──────────────────────────────────────────────────────────────────────

STATE_FILE = Path("tracker_state.json")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


# ── helpers ────────────────────────────────────────────────────────────────────

def parse_price(text: str) -> Optional[float]:
    m = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def open_page(p, url: str) -> Page:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 2200})
    page.goto(url, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_load_state("load", timeout=30_000)
    page.wait_for_timeout(3000)
    return page, browser


# ── URL-based tracking ─────────────────────────────────────────────────────────

def _date_variants(iso_date: str) -> list[str]:
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    return [
        dt.strftime("%a %d %b %Y"),
        dt.strftime("%a, %d %b %Y"),
        dt.strftime("%d %b %Y"),
        dt.strftime("%b %d, %Y"),
        dt.strftime("%a %b %d %Y"),
    ]


def _extract_trip_block(page_text: str, target_start: str, target_end: str) -> str:
    normalized = re.sub(r"\s+", " ", page_text)
    start_hits = [normalized.find(v) for v in _date_variants(target_start) if normalized.find(v) != -1]
    end_hits = [normalized.find(v) for v in _date_variants(target_end) if normalized.find(v) != -1]

    if start_hits:
        s = min(start_hits)
        e = min([i for i in end_hits if i > s], default=s + 1800)
        return normalized[s:max(e + 300, s + 1200)]

    marker = normalized.lower().find("dates and prices")
    if marker != -1:
        return normalized[marker:marker + 2500]
    return normalized[:5000]


def track_url(item: dict, state: dict, p) -> dict:
    url = item["url"]
    target_start = item.get("target_start")
    target_end = item.get("target_end")
    alert_on = item.get("alert_on", [])

    page, browser = open_page(p, url)

    # Try to expand "Dates and prices" sections
    if target_start and target_end:
        for label in ["Dates and prices", "View dates and prices", "Dates & prices", "Dates"]:
            try:
                page.get_by_text(label, exact=False).first.click(timeout=1500)
                break
            except Exception:
                pass
        page.wait_for_timeout(2000)

    page_text = page.locator("body").inner_text(timeout=30_000)
    browser.close()

    if target_start and target_end:
        block = _extract_trip_block(page_text, target_start, target_end)
    else:
        block = re.sub(r"\s+", " ", page_text)[:5000]

    current_price = parse_price(block)
    current_spots = None
    m = re.search(r"(?:Only\s+)?(\d+)\s+spots?\s+left", block, re.IGNORECASE)
    if m:
        current_spots = int(m.group(1))

    prev_price = state.get("price")
    prev_spots = state.get("spots")

    alerts = []
    if "price_drop" in alert_on and prev_price and current_price and current_price < prev_price:
        alerts.append(f"Price dropped: ${prev_price:.0f} → ${current_price:.0f}")
    if "low_spots" in alert_on and current_spots is not None and current_spots <= 3:
        alerts.append(f"Only {current_spots} spots left!")

    result = {
        "price": current_price,
        "spots": current_spots,
        "alerts": alerts,
        "url": url,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    state.update(result)

    if alerts:
        _send_email(
            f"Alert: {item['name']}",
            "\n".join(alerts) + f"\n\nURL: {url}\nPrice: ${current_price}\nSpots: {current_spots}",
        )

    return result


# ── keyword search tracking ────────────────────────────────────────────────────

def _google_shopping_url(keywords: str, sites: list[str]) -> str:
    query = keywords
    if sites:
        site_filter = " OR ".join(f"site:{s}" for s in sites)
        query = f"{keywords} ({site_filter})"
    return f"https://www.google.com/search?tbm=shop&q={quote_plus(query)}"


def _parse_shopping_results(page_text: str, max_price: Optional[float]) -> list[dict]:
    """
    Extract product name + price pairs from Google Shopping page text.
    Returns up to 10 results below max_price (if set).
    """
    results = []
    # Google Shopping renders results as product lines with prices nearby.
    # We look for patterns like "ProductName ... $XX.XX"
    lines = [l.strip() for l in re.split(r"[\n\r]+", page_text) if l.strip()]

    i = 0
    while i < len(lines) and len(results) < 10:
        price = parse_price(lines[i])
        if price is not None:
            # Title is usually the line before the price
            title = lines[i - 1] if i > 0 else ""
            if max_price is None or price <= max_price:
                results.append({"title": title, "price": price})
        i += 1

    return results


def track_search(item: dict, state: dict, p) -> dict:
    keywords = item["keywords"]
    sites = item.get("sites", [])
    max_price = item.get("max_price")
    alert_on = item.get("alert_on", [])

    search_url = _google_shopping_url(keywords, sites)
    page, browser = open_page(p, search_url)
    page_text = page.locator("body").inner_text(timeout=30_000)
    browser.close()

    current_results = _parse_shopping_results(page_text, max_price)
    prev_results = state.get("results", [])
    prev_min_price = min((r["price"] for r in prev_results), default=None) if prev_results else None
    curr_min_price = min((r["price"] for r in current_results), default=None) if current_results else None

    alerts = []
    if "price_drop" in alert_on and prev_min_price and curr_min_price and curr_min_price < prev_min_price:
        alerts.append(f"Price dropped: ${prev_min_price:.2f} → ${curr_min_price:.2f}")
    if "new_result" in alert_on and current_results and not prev_results:
        alerts.append(f"New results found for: {keywords}")

    result = {
        "results": current_results,
        "result_count": len(current_results),
        "min_price": curr_min_price,
        "alerts": alerts,
        "search_url": search_url,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    state.update(result)

    if alerts or (not prev_results and current_results):
        top = "\n".join(f"  ${r['price']:.2f}  {r['title']}" for r in current_results[:5])
        _send_email(
            f"Search results: {item['name']}",
            "\n".join(alerts) + f"\n\nTop results:\n{top}\n\nSearch: {search_url}",
        )

    return result


# ── main ───────────────────────────────────────────────────────────────────────

def main(items_file: str = "items.json") -> int:
    items_path = Path(items_file)
    if not items_path.exists():
        print(f"Items file not found: {items_file}", file=sys.stderr)
        return 1

    items = json.loads(items_path.read_text(encoding="utf-8"))
    state = load_state()
    all_results = {}
    errors = []

    with sync_playwright() as p:
        for item in items:
            if not item.get("enabled", True):
                continue

            item_id = item["id"]
            item_state = state.setdefault(item_id, {})

            print(f"\n[tracker] Checking: {item['name']}")
            try:
                if item["type"] == "url":
                    result = track_url(item, item_state, p)
                elif item["type"] == "search":
                    result = track_search(item, item_state, p)
                else:
                    print(f"  Unknown type: {item['type']}", file=sys.stderr)
                    continue

                all_results[item_id] = result
                alerts = result.get("alerts", [])
                if alerts:
                    print(f"  ALERTS: {'; '.join(alerts)}")
                else:
                    print(f"  OK — price: {result.get('price') or result.get('min_price')}")

            except Exception as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)
                errors.append({"id": item_id, "error": str(exc)})

    save_state(state)

    print(json.dumps({"results": all_results, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(main(args[0] if args else "items.json"))
