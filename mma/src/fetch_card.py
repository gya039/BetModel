"""
fetch_card.py — discover the target UFC event from ufcstats.com and scrape
                every bout on the card.

Run:  python src/fetch_card.py
Out:  data/raw/card.json
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup
from utils import fetch_html, save_json, DATA_RAW, get_logger

log = get_logger("fetch_card")

UPCOMING_URL    = "http://ufcstats.com/statistics/events/upcoming?page=all"
# Keywords that identify the target event (lower-case, both must appear)
TARGET_KEYWORDS = ["sterling", "zalal"]


# ── event discovery ───────────────────────────────────────────────────────────

def _find_target_event(soup: BeautifulSoup) -> dict:
    """
    Walk the upcoming-events table and return the first row whose event name
    contains all TARGET_KEYWORDS.  Falls back to the very first listed event
    so the pipeline never hard-stops when the card isn't posted yet.
    """
    # ufcstats uses two possible row selectors; try both
    rows = soup.select("tr.b-statistics__table-events_row")
    if not rows:
        rows = soup.select("table.b-statistics__table-events tbody tr")

    first_fallback = None
    for row in rows:
        link = row.find("a")
        if not link or not link.get("href"):
            continue
        name = link.get_text(strip=True)
        href = link["href"].strip()
        if not first_fallback:
            first_fallback = {"name": name, "url": href}
        if all(kw in name.lower() for kw in TARGET_KEYWORDS):
            log.info("Matched target event: %s", name)
            return {"name": name, "url": href}

    if first_fallback:
        log.warning("Target keywords %s not found — using first upcoming: %s",
                    TARGET_KEYWORDS, first_fallback["name"])
        return first_fallback

    raise RuntimeError("No upcoming events found on ufcstats.com.")


# ── event page parsing ────────────────────────────────────────────────────────

def _parse_event_meta(soup: BeautifulSoup) -> dict:
    meta: dict = {}

    title_el = (soup.select_one("span.b-content__title-highlight") or
                soup.select_one("h2.b-content__title span"))
    meta["event_name"] = title_el.get_text(strip=True) if title_el else "Unknown Event"

    for item in soup.select("li.b-list__box-list-item"):
        label_el = item.find("i")
        if not label_el:
            continue
        label = label_el.get_text(strip=True).lower().rstrip(":")
        # strip the label text from the full item text to get the value
        value = item.get_text(" ", strip=True)
        value = value.replace(label_el.get_text(strip=True), "").strip()
        if "date" in label:
            meta["event_date"] = value
        elif "location" in label:
            meta["event_location"] = value
        elif "attendance" in label:
            meta["attendance"] = value

    return meta


def _parse_bouts(soup: BeautifulSoup) -> list[dict]:
    """
    Extract every bout row from the event details page.
    Each row has two fighter name/link pairs in the first <td>.
    """
    bouts = []
    rows = soup.select("tr.b-fight-details__table-row.js-fight-details-click")

    for i, row in enumerate(rows):
        # Event page uses class "b-link b-link_style_black" for fighter links
        fighter_links = row.select('a[href*="fighter-details"]')
        if len(fighter_links) < 2:
            continue

        fighter_a = {
            "name": fighter_links[0].get_text(strip=True),
            "url":  fighter_links[0].get("href", "").strip(),
        }
        fighter_b = {
            "name": fighter_links[1].get_text(strip=True),
            "url":  fighter_links[1].get("href", "").strip(),
        }

        # Weight class lives in the 7th <td> (index 6) on the event page
        tds = row.find_all("td")
        weight_class = ""
        if len(tds) > 6:
            weight_class = tds[6].get_text(strip=True)

        fight_url = row.get("data-link", "").strip()

        bouts.append({
            "bout_number":  i + 1,
            "fighter_a":    fighter_a,
            "fighter_b":    fighter_b,
            "weight_class": weight_class,
            "fight_url":    fight_url,
        })

    return bouts


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> dict:
    # Fetch upcoming events list (cached for speed; delete to refresh)
    cache_upcoming = DATA_RAW / "cache" / "upcoming.html"
    html = fetch_html(UPCOMING_URL, cache_path=cache_upcoming)
    soup = BeautifulSoup(html, "html.parser")

    event_ref = _find_target_event(soup)
    event_url = event_ref["url"]

    # Always re-fetch the event page so late card changes are captured
    event_id    = event_url.rstrip("/").split("/")[-1]
    cache_event = DATA_RAW / "cache" / f"event_{event_id}.html"
    # Remove stale cache so we get live card
    if cache_event.exists():
        cache_event.unlink()

    html_event = fetch_html(event_url, cache_path=cache_event)
    soup_event = BeautifulSoup(html_event, "html.parser")

    meta  = _parse_event_meta(soup_event)
    bouts = _parse_bouts(soup_event)

    card = {
        **meta,
        "event_url":   event_url,
        "total_bouts": len(bouts),
        "bouts":       bouts,
    }

    out = DATA_RAW / "card.json"
    save_json(card, out)
    log.info("Saved %d bouts → %s", len(bouts), out)

    for b in bouts:
        log.info("  #%02d  %-25s vs  %s  [%s]",
                 b["bout_number"],
                 b["fighter_a"]["name"],
                 b["fighter_b"]["name"],
                 b["weight_class"])

    return card


if __name__ == "__main__":
    main()
