import json
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright

URL = "https://www.cnn.com/markets/fear-and-greed"
STATE_FILE = Path("fear_greed_state.json")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def parse_index_value(page_text: str) -> Optional[int]:
    """
    Try to extract the Fear & Greed index value from the page text.
    The index is typically shown prominently with the numerical value.
    """
    # Look for patterns like "Fear & Greed Index Now: 45" or just a number near "Fear" or "Greed"
    patterns = [
        r"(?:index|now|current)[\s:]*(\d{1,3})",
        r"fear\s*(?:and|&)\s*greed\s*(?:index)?[\s:]*(\d{1,3})",
        r"(\d{1,3})\s*(?:extreme fear|fear|neutral|greed|extreme greed)",
    ]

    normalized = re.sub(r"\s+", " ", page_text.lower())

    for pattern in patterns:
        matches = re.findall(pattern, normalized, re.IGNORECASE)
        for match in matches:
            val = int(match)
            # Fear & Greed index is 0-100
            if 0 <= val <= 100:
                return val

    return None


def get_sentiment(index_value: int) -> str:
    """Return the sentiment category based on index value."""
    if index_value <= 25:
        return "Extreme Fear"
    elif index_value <= 45:
        return "Fear"
    elif index_value <= 55:
        return "Neutral"
    elif index_value <= 75:
        return "Greed"
    else:
        return "Extreme Greed"


def main() -> int:
    state = load_state()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 2200})

        # Navigate to the page
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_timeout(3000)

        # Get page text
        page_text = page.locator("body").inner_text(timeout=30_000)

        # Also try to get any data attributes or specific elements
        # CNN might have the value in a specific element
        try:
            # Try to find elements with the index value
            index_element = page.locator("text=/\\d{1,3}/").first
            if index_element:
                element_text = index_element.inner_text(timeout=5000)
                page_text = element_text + " " + page_text
        except Exception:
            pass

        browser.close()

    current_index = parse_index_value(page_text)

    if current_index is None:
        print("Could not parse Fear & Greed index from the page.", file=sys.stderr)
        print("Page text preview:", file=sys.stderr)
        print(page_text[:1000], file=sys.stderr)
        return 1

    previous_index = state.get("index")
    previous_sentiment = state.get("sentiment")

    current_sentiment = get_sentiment(current_index)
    extreme_fear = current_index <= 25
    sentiment_changed = previous_sentiment and previous_sentiment != current_sentiment

    # Update state
    state["index"] = current_index
    state["sentiment"] = current_sentiment
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    state["extreme_fear"] = extreme_fear
    save_state(state)

    # Output JSON for the workflow to consume
    result = {
        "index": current_index,
        "sentiment": current_sentiment,
        "extreme_fear": extreme_fear,
        "previous_index": previous_index,
        "sentiment_changed": sentiment_changed,
        "url": URL,
    }

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
