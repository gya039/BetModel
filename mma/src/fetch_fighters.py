"""
fetch_fighters.py — scrape each fighter's ufcstats.com profile page:
                    record, physical attributes, career stats, fight history.

Run:  python src/fetch_fighters.py
Out:  data/raw/fighters/<fighter_id>.json  (one file per fighter)
"""
import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup
from utils import (
    fetch_html, save_json, load_json,
    DATA_RAW, get_logger,
    normalise_method, normalise_result,
    pct_to_float, safe_float, safe_int,
)

log = get_logger("fetch_fighters")


# ── record ────────────────────────────────────────────────────────────────────

def _parse_record(soup: BeautifulSoup) -> dict:
    """Extract W-L-D (and optional NC) from the title record span."""
    el = (soup.select_one("span.b-content__title-record") or
          soup.select_one(".b-content__title-record"))
    if not el:
        return {"wins": 0, "losses": 0, "draws": 0, "nc": 0}

    text = el.get_text(strip=True)  # e.g. "Record: 22-4-0 (1 NC)"
    m    = re.search(r"(\d+)[–\-](\d+)[–\-](\d+)", text)
    nc_m = re.search(r"\((\d+)\s*NC\)", text, re.I)
    return {
        "wins":   int(m.group(1)) if m else 0,
        "losses": int(m.group(2)) if m else 0,
        "draws":  int(m.group(3)) if m else 0,
        "nc":     int(nc_m.group(1)) if nc_m else 0,
    }


# ── bio & career stats ────────────────────────────────────────────────────────

# Mapping from the label text (lower-cased) → dict key
_LABEL_KEYS = {
    "height":   "height",
    "weight":   "weight",
    "reach":    "reach",
    "stance":   "stance",
    "dob":      "dob",
    "slpm":     "slpm",
    "str. acc": "str_acc",
    "str. def": "str_def",
    "sapm":     "sapm",
    "td avg":   "td_avg",
    "td acc":   "td_acc",
    "td def":   "td_def",
    "sub. avg": "sub_avg",
}

def _parse_bio_and_stats(soup: BeautifulSoup) -> dict:
    """
    Parse both physical attributes (first ul.b-list__box-list) and career
    stats (second ul) into one flat dict.
    """
    bio: dict = {}
    lists = soup.select("ul.b-list__box-list")

    for ul in lists[:2]:
        for item in ul.select("li.b-list__box-list-item"):
            label_el = item.find("i")
            if not label_el:
                continue
            label = label_el.get_text(strip=True).lower().rstrip(":")
            # Value = full item text minus the label
            full_text = item.get_text(" ", strip=True)
            value     = full_text.replace(label_el.get_text(strip=True), "").strip()
            if not value or value in ("--", "N/A", ""):
                continue
            for k, dict_key in _LABEL_KEYS.items():
                if k in label:
                    bio[dict_key] = value
                    break

    return bio


# ── fight history ─────────────────────────────────────────────────────────────

def _td_texts(td) -> list[str]:
    """Return all non-empty text segments from a <td> (via <p> tags or direct)."""
    ps = td.find_all("p")
    if ps:
        return [p.get_text(strip=True) for p in ps if p.get_text(strip=True)]
    return [td.get_text(strip=True)]


def _parse_fight_history(soup: BeautifulSoup, fighter_url: str) -> list[dict]:
    """
    Parse the fight history table on a fighter profile page.

    Actual ufcstats column layout (10 cols):
      0  W/L     — result text via b-flag link (win/loss/draw/nc)
      1  Fighter — p[0]=this fighter, p[1]=opponent (both b-link links)
      2  KD      — p[0]=this fighter, p[1]=opponent
      3  Str     — sig strikes: p[0]=this, p[1]=opponent (raw counts)
      4  TD      — takedowns: p[0]=this, p[1]=opponent
      5  Sub     — sub attempts: p[0]=this, p[1]=opponent
      6  Event   — p[0]=event name (link), p[1]=date
      7  Method  — p[0]=method string
      8  Round
      9  Time
    Fight URL is in row's data-link attribute.
    """
    fights: list[dict] = []

    # The history table uses class js-fight-table on the fighter profile page
    history_table = (soup.select_one("table.js-fight-table") or
                     soup.select_one("table.b-fight-details__table"))
    if not history_table:
        return fights

    # Only completed fights have js-fight-details-click class
    rows = history_table.select("tr.js-fight-details-click")

    for row in rows:
        fight_url = row.get("data-link", "").strip()
        tds = row.find_all("td")
        if len(tds) < 8:
            continue

        def p_text(td_idx: int, p_idx: int = 0) -> str:
            if td_idx >= len(tds):
                return ""
            ps = tds[td_idx].find_all("p")
            if p_idx < len(ps):
                return ps[p_idx].get_text(strip=True)
            return tds[td_idx].get_text(strip=True)

        # ── Result (td[0]) ─────────────────────────────────────────────────────
        result_raw = p_text(0)
        result     = normalise_result(result_raw)

        # ── Fighters (td[1]) ───────────────────────────────────────────────────
        fighter_links = tds[1].select("a")
        opponent_name = fighter_links[1].get_text(strip=True) if len(fighter_links) > 1 else ""
        opponent_url  = fighter_links[1].get("href", "").strip() if len(fighter_links) > 1 else ""

        # ── Per-fight stats from profile table (raw counts, not fractions) ─────
        kd_val  = safe_int(p_text(2, 0))     # td[2] p[0] = KD for this fighter
        sig_str = safe_int(p_text(3, 0))     # td[3] p[0] = sig strikes landed
        td_val  = safe_int(p_text(4, 0))     # td[4] p[0] = TDs landed
        sub_att = safe_int(p_text(5, 0))     # td[5] p[0] = submission attempts

        # ── Event (td[6]) ──────────────────────────────────────────────────────
        event_a    = tds[6].find("a", href=re.compile(r"event-details")) if len(tds) > 6 else None
        event_name = event_a.get_text(strip=True) if event_a else p_text(6, 0)
        event_url  = event_a.get("href", "").strip() if event_a else ""
        event_date = p_text(6, 1)

        # ── Method / Round / Time (td[7], [8], [9]) ───────────────────────────
        method_raw = p_text(7, 0)
        round_raw  = p_text(8, 0)
        time_raw   = p_text(9, 0)

        fights.append({
            "fight_url":            fight_url,
            "opponent":             opponent_name,
            "opponent_url":         opponent_url,
            "result":               result,
            "result_raw":           result_raw,
            "event":                event_name,
            "event_date":           event_date,
            "event_url":            event_url,
            "method_raw":           method_raw,
            "method":               normalise_method(method_raw),
            "round":                safe_int(round_raw, 0),
            "time":                 time_raw,
            # Stats from the fighter profile table (raw counts per fight)
            "kd":                   kd_val,
            "sig_strikes_landed":   sig_str,
            "td_landed":            td_val,
            "sub_attempts":         sub_att,
        })

    return fights


# ── full fighter scrape ───────────────────────────────────────────────────────

def scrape_fighter(fighter_url: str, fighter_name: str) -> dict:
    fighter_id = fighter_url.rstrip("/").split("/")[-1]
    cache_path = DATA_RAW / "fighters" / f"_html_{fighter_id}.html"
    html = fetch_html(fighter_url, cache_path=cache_path)
    soup = BeautifulSoup(html, "html.parser")

    record  = _parse_record(soup)
    bio     = _parse_bio_and_stats(soup)
    history = _parse_fight_history(soup, fighter_url)

    # Normalise nickname if present in the page title area
    nickname = ""
    nick_el = soup.select_one("p.b-content__Nickname")
    if nick_el:
        nickname = nick_el.get_text(strip=True).strip('"')

    return {
        "name":         fighter_name,
        "nickname":     nickname,
        "fighter_id":   fighter_id,
        "url":          fighter_url,
        "record":       record,
        "bio":          bio,
        "fight_history": history,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> list[dict]:
    card_path = DATA_RAW / "card.json"
    if not card_path.exists():
        raise FileNotFoundError("card.json not found — run fetch_card.py first.")

    card     = load_json(card_path)
    seen_urls: set[str] = set()
    fighters: list[dict] = []

    for bout in card.get("bouts", []):
        for side in ("fighter_a", "fighter_b"):
            f   = bout[side]
            url = f.get("url", "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            log.info("Scraping: %s  (%s)", f["name"], url)
            try:
                data       = scrape_fighter(url, f["name"])
                fighter_id = data["fighter_id"]
                out        = DATA_RAW / "fighters" / f"{fighter_id}.json"
                save_json(data, out)
                log.info("  → %d fights  saved to %s", len(data["fight_history"]), out.name)
                fighters.append(data)
            except Exception as exc:
                log.error("  ✗  %s — %s", f["name"], exc)

    log.info("Done. Scraped %d fighters.", len(fighters))
    return fighters


if __name__ == "__main__":
    main()
