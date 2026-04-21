"""
fetch_fight_history.py — scrape individual fight detail pages from ufcstats.com
                         to obtain per-fight totals (strikes, TDs, KD, etc.)
                         for every historical fight of every card fighter.

Run:  python src/fetch_fight_history.py
Out:  data/raw/fights/<fight_id>.json  (one file per unique fight)
"""
import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup
from utils import (
    fetch_html, save_json, load_json,
    DATA_RAW, get_logger,
    parse_fraction, safe_int,
)

log = get_logger("fetch_fight_history")


# ── fight detail parsing ──────────────────────────────────────────────────────

def _td_first_text(td) -> str:
    ps = td.find_all("p")
    if ps:
        return ps[0].get_text(strip=True)
    return td.get_text(strip=True)


def _parse_totals_table(soup: BeautifulSoup) -> list[dict]:
    """
    Parse the 'Totals' table from a fight detail page.

    Expected columns:
      Fighter | KD | Sig. Str. | Sig. Str. % | Total Str. | Td | Td % |
      Sub. Att | Rev. | Ctrl

    Returns one dict per fighter (2 rows expected).
    """
    stats: list[dict] = []

    # The Totals section header contains "Totals"
    # Find the table that follows it, or just take the first b-fight-details__table
    tables = soup.select("table.b-fight-details__table")
    if not tables:
        return stats

    totals_table = tables[0]
    rows = totals_table.select("tbody tr.b-fight-details__table-row")
    if not rows:
        rows = totals_table.select("tbody tr")

    for row in rows:
        tds = row.find_all("td")
        if len(tds) < 9:
            continue

        # td[0] = fighter name (link)
        name_link = tds[0].find("a")
        if name_link:
            name        = name_link.get_text(strip=True)
            fighter_url = name_link.get("href", "").strip()
        else:
            name        = _td_first_text(tds[0])
            fighter_url = ""

        if not name:
            continue

        kd_text      = _td_first_text(tds[1])
        sig_str_text = _td_first_text(tds[2])   # "45 of 90"
        sig_str_pct  = _td_first_text(tds[3])   # "50%"
        tot_str_text = _td_first_text(tds[4])   # "78 of 120"
        td_text      = _td_first_text(tds[5])   # "2 of 5"
        td_pct_text  = _td_first_text(tds[6])   # "40%"
        sub_att_text = _td_first_text(tds[7])
        rev_text     = _td_first_text(tds[8])
        ctrl_text    = _td_first_text(tds[9]) if len(tds) > 9 else ""

        sig_landed, sig_att = parse_fraction(sig_str_text)
        tot_landed, tot_att = parse_fraction(tot_str_text)
        td_landed,  td_att  = parse_fraction(td_text)

        stats.append({
            "fighter_name":          name,
            "fighter_url":           fighter_url,
            "kd":                    safe_int(kd_text),
            "sig_strikes_landed":    sig_landed,
            "sig_strikes_att":       sig_att,
            "sig_strikes_pct_raw":   sig_str_pct,
            "total_strikes_landed":  tot_landed,
            "total_strikes_att":     tot_att,
            "td_landed":             td_landed,
            "td_att":                td_att,
            "td_pct_raw":            td_pct_text,
            "sub_attempts":          safe_int(sub_att_text),
            "reversals":             safe_int(rev_text),
            "ctrl_time":             ctrl_text,
        })

    return stats


def _parse_fight_meta(soup: BeautifulSoup) -> dict:
    """Extract method, round, time from the fight detail header section."""
    meta: dict = {}
    for item in soup.select("i.b-fight-details__label"):
        label = item.get_text(strip=True).lower().rstrip(":")
        value_el = item.find_next_sibling() or item.parent
        if value_el:
            value = value_el.get_text(strip=True).replace(item.get_text(strip=True), "").strip()
            if "method" in label:
                meta["method_raw"] = value
            elif "round" in label:
                meta["round"] = safe_int(value)
            elif "time" in label:
                meta["time"] = value
    return meta


def scrape_fight(fight_url: str) -> dict:
    fight_id   = fight_url.rstrip("/").split("/")[-1]
    cache_path = DATA_RAW / "fights" / f"_html_{fight_id}.html"
    html       = fetch_html(fight_url, cache_path=cache_path)
    soup       = BeautifulSoup(html, "html.parser")

    fighter_stats = _parse_totals_table(soup)
    fight_meta    = _parse_fight_meta(soup)

    return {
        "fight_id":      fight_id,
        "fight_url":     fight_url,
        "fighter_stats": fighter_stats,
        **fight_meta,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    fighter_dir = DATA_RAW / "fighters"
    if not fighter_dir.exists():
        raise FileNotFoundError("fighters/ dir not found — run fetch_fighters.py first.")

    fight_urls_seen: set[str] = set()
    scraped = 0
    skipped = 0

    # Collect all unique fight URLs from every fighter's history
    all_fight_urls: list[str] = []
    for fp in sorted(fighter_dir.glob("*.json")):
        if "_html_" in fp.name:
            continue
        try:
            fighter = load_json(fp)
        except Exception as exc:
            log.warning("Could not load %s: %s", fp.name, exc)
            continue
        for fight in fighter.get("fight_history", []):
            url = fight.get("fight_url", "").strip()
            if url and url not in fight_urls_seen:
                fight_urls_seen.add(url)
                all_fight_urls.append(url)

    log.info("Found %d unique fight URLs to process.", len(all_fight_urls))

    for url in all_fight_urls:
        fight_id = url.rstrip("/").split("/")[-1]
        out_path = DATA_RAW / "fights" / f"{fight_id}.json"

        if out_path.exists():
            skipped += 1
            continue

        log.info("Scraping fight: %s", url)
        try:
            data = scrape_fight(url)
            save_json(data, out_path)
            scraped += 1
        except Exception as exc:
            log.error("  ✗  %s — %s", url, exc)

    log.info("Done. New: %d  |  Already cached: %d", scraped, skipped)
    return scraped


if __name__ == "__main__":
    main()
