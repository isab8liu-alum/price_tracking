import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

URL = os.environ["TRIP_URL"]
TARGET_START = os.environ["TARGET_START"]  # YYYY-MM-DD
TARGET_END = os.environ["TARGET_END"]      # YYYY-MM-DD

EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_FROM = os.environ.get("EMAIL_FROM", os.environ["SMTP_USER"])

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT") or "587")
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

STATE_FILE = Path("trip_state.json")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def parse_price(text: str) -> Optional[int]:
    m = re.search(r"(?:USD\s*)?\$([\d,]+)", text, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1).replace(",", ""))


def parse_spots(text: str) -> Optional[int]:
    m = re.search(r"(?:Only\s+)?(\d+)\s+spots?\s+left", text, re.IGNORECASE)
    if not m:
        return None
    return int(m.group(1))


def date_variants(iso_date: str) -> list[str]:
    dt = datetime.strptime(iso_date, "%Y-%m-%d")
    variants = [
        dt.strftime("%a %d %b %Y"),
        dt.strftime("%a, %d %b %Y"),
        dt.strftime("%d %b %Y"),
        dt.strftime("%b %d, %Y"),
        dt.strftime("%a %b %d %Y"),
    ]
    # de-duplicate while preserving order
    seen = set()
    out = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def extract_relevant_block(page_text: str) -> str:
    normalized = re.sub(r"\s+", " ", page_text)

    start_hits = []
    for v in date_variants(TARGET_START):
        idx = normalized.find(v)
        if idx != -1:
            start_hits.append(idx)

    end_hits = []
    for v in date_variants(TARGET_END):
        idx = normalized.find(v)
        if idx != -1:
            end_hits.append(idx)

    if start_hits:
        start_idx = min(start_hits)
        end_idx = min([i for i in end_hits if i > start_idx], default=start_idx + 1800)
        return normalized[start_idx:max(end_idx + 300, start_idx + 1200)]

    # fallback: if we cannot find the exact dates, scan the first part of the prices section
    marker = normalized.lower().find("dates and prices")
    if marker != -1:
        return normalized[marker: marker + 2500]

    return normalized[:5000]


def main() -> int:
    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})
        page.goto(URL, wait_until="networkidle", timeout=120_000)

        # Try a few common ways to expose the dates/prices section.
        for label in ["Dates and prices", "View dates and prices", "Dates & prices", "Dates"]:
            try:
                loc = page.get_by_text(label, exact=False).first
                loc.click(timeout=1500)
                break
            except Exception:
                pass

        page.wait_for_timeout(4000)
        page_text = page.locator("body").inner_text(timeout=30_000)
        browser.close()

    block = extract_relevant_block(page_text)
    current_price = parse_price(block)
    current_spots = parse_spots(block)

    if current_price is None:
        print("Could not parse a price from the page text.", file=sys.stderr)
        print(block[:1200], file=sys.stderr)
        return 1

    previous_price = state.get("price")
    previous_spots = state.get("spots")

    price_drop = previous_price is not None and current_price < previous_price
    low_spots = current_spots is not None and current_spots <= 3

    if price_drop or low_spots:
        subject = "Intrepid Morocco alert"
        body = (
            f"Trip alert for Essential Morocco\n\n"
            f"Dates: {TARGET_START} to {TARGET_END}\n"
            f"Current price: {current_price}\n"
            f"Current spots: {current_spots}\n"
            f"Previous price: {previous_price}\n"
            f"Previous spots: {previous_spots}\n"
            f"URL: {URL}\n"
        )
        send_email(subject, body)

    state["price"] = current_price
    state["spots"] = current_spots
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    save_state(state)

    print(json.dumps(
        {
            "price": current_price,
            "spots": current_spots,
            "price_drop": price_drop,
            "low_spots": low_spots,
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())