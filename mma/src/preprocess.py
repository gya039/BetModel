"""
preprocess.py — load all raw scraped data, clean/normalise, and produce
                data/processed/fighters_raw.json  +  fighters_raw.csv

Run:  python src/preprocess.py
"""
import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from utils import (
    load_json, save_json,
    DATA_RAW, DATA_PROC, get_logger,
    normalise_method, normalise_result,
    pct_to_float, safe_float, safe_int,
    parse_fraction,
)

log = get_logger("preprocess")


# ── bio / physical cleaning ───────────────────────────────────────────────────

def _height_to_inches(raw: str) -> float | None:
    """5' 11"  →  71.0"""
    m = re.match(r"(\d+)'\s*(\d+)", raw or "")
    return float(int(m.group(1)) * 12 + int(m.group(2))) if m else None


def _reach_to_inches(raw: str) -> float | None:
    m = re.match(r"(\d+(?:\.\d+)?)", (raw or "").strip())
    return float(m.group(1)) if m else None


def _weight_to_lbs(raw: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", (raw or "").strip())
    return float(m.group(1)) if m else None


def _approx_age(dob_raw: str) -> float | None:
    """Approximate age from DOB string like 'Jan / 11 / 1990'."""
    m = re.search(r"(\d{4})", dob_raw or "")
    return float(2026 - int(m.group(1))) if m else None


def _clean_bio(bio: dict) -> dict:
    return {
        "height_raw":  bio.get("height", ""),
        "height_in":   _height_to_inches(bio.get("height", "")),
        "weight_raw":  bio.get("weight", ""),
        "weight_lbs":  _weight_to_lbs(bio.get("weight", "")),
        "reach_raw":   bio.get("reach", ""),
        "reach_in":    _reach_to_inches(bio.get("reach", "")),
        "stance":      bio.get("stance", "").title(),
        "dob":         bio.get("dob", ""),
        "age":         _approx_age(bio.get("dob", "")),
        # Career stats (already strings from bio dict)
        "slpm":    safe_float(bio.get("slpm")),
        "str_acc": pct_to_float(bio.get("str_acc")),
        "sapm":    safe_float(bio.get("sapm")),
        "str_def": pct_to_float(bio.get("str_def")),
        "td_avg":  safe_float(bio.get("td_avg")),
        "td_acc":  pct_to_float(bio.get("td_acc")),
        "td_def":  pct_to_float(bio.get("td_def")),
        "sub_avg": safe_float(bio.get("sub_avg")),
    }


# ── promotion detection ───────────────────────────────────────────────────────

_PROMO_MAP = {
    "UFC": "UFC", "BELLATOR": "Bellator", "ONE FC": "ONE FC", "ONE ": "ONE FC",
    "PFL": "PFL", "INVICTA": "Invicta", "RIZIN": "Rizin",
    "CAGE WARRIORS": "Cage Warriors", "GLORY": "Glory", "STRIKEFORCE": "Strikeforce",
    "WEC": "WEC", "PRIDE": "PRIDE", "AFFLICTION": "Affliction",
}

def _detect_promotion(event_name: str) -> str:
    up = event_name.upper().strip()
    for key, label in _PROMO_MAP.items():
        if up.startswith(key) or f" {key}" in up:
            return label
    if up:
        return up.split(":")[0].split("–")[0].split("-")[0].strip().title()
    return "Unknown"

# ── fight stats lookup ────────────────────────────────────────────────────────

def _build_fight_stats_lookup() -> dict[str, dict]:
    """
    Build: fight_url → {fighter_name_lower: per_fight_stats_dict}
    Used to enrich each fight-history entry with actual strike/TD counts.
    """
    lookup: dict[str, dict] = {}
    fight_dir = DATA_RAW / "fights"
    if not fight_dir.exists():
        return lookup

    for fp in fight_dir.glob("*.json"):
        if "_html_" in fp.name:
            continue
        try:
            data = load_json(fp)
        except Exception:
            continue
        url = data.get("fight_url", "").strip()
        if not url:
            continue
        lookup[url] = {}
        for s in data.get("fighter_stats", []):
            name = s.get("fighter_name", "").lower().strip()
            if name:
                lookup[url][name] = s

    return lookup


def _lookup_per_fight_stats(fight_url: str, fighter_name: str,
                             stats_lookup: dict) -> dict:
    """Try to find per-fight stats for this fighter in the lookup dict."""
    url_data = stats_lookup.get(fight_url, {})
    if not url_data:
        return {}

    name_lower = fighter_name.lower().strip()

    # Exact match first
    if name_lower in url_data:
        return url_data[name_lower]

    # Fuzzy: check if any stored key is a substring of the fighter name or vice versa
    name_parts = set(name_lower.split())
    for stored_name, stats in url_data.items():
        stored_parts = set(stored_name.split())
        # Overlap of >= 1 non-trivial word
        overlap = name_parts & stored_parts - {"de", "da", "el", "the"}
        if overlap:
            return stats

    return {}


# ── fight history normalisation ───────────────────────────────────────────────

def _clean_fight(fight: dict, fighter_name: str, stats_lookup: dict) -> dict:
    """Normalise a single fight history entry; optionally enrich from fight detail pages."""
    method = normalise_method(fight.get("method_raw", fight.get("method", "")))
    result = normalise_result(fight.get("result_raw", fight.get("result", "")))

    # Base stats come directly from the fighter profile table
    sig_landed = fight.get("sig_strikes_landed")
    td_landed  = fight.get("td_landed")
    kd         = fight.get("kd")
    sub_att    = fight.get("sub_attempts")

    # Optionally enrich with fight detail pages (adds total strikes, accuracy, ctrl)
    per_fight = _lookup_per_fight_stats(
        fight.get("fight_url", ""), fighter_name, stats_lookup
    )
    total_landed = per_fight.get("total_strikes_landed")
    sig_att      = per_fight.get("sig_strikes_att")
    td_att       = per_fight.get("td_att")
    ctrl_time    = per_fight.get("ctrl_time", "")

    return {
        "fight_url":              fight.get("fight_url", ""),
        "opponent":               fight.get("opponent", ""),
        "opponent_url":           fight.get("opponent_url", ""),
        "result":                 result,
        "event":                  fight.get("event", ""),
        "event_date":             fight.get("event_date", ""),
        "promotion":              _detect_promotion(fight.get("event", "")),
        "method_raw":             fight.get("method_raw", ""),
        "method":                 method,
        "round":                  safe_int(fight.get("round", 0)),
        "time":                   fight.get("time", ""),
        "kd":                     kd,
        "sig_strikes_landed":     sig_landed,
        "sig_strikes_att":        sig_att,
        "total_strikes_landed":   total_landed,
        "td_landed":              td_landed,
        "td_att":                 td_att,
        "sub_attempts":           sub_att,
        "ctrl_time":              ctrl_time,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> list[dict]:
    fighter_dir = DATA_RAW / "fighters"
    if not fighter_dir.exists():
        raise FileNotFoundError("fighters/ not found — run fetch_fighters.py first.")

    stats_lookup = _build_fight_stats_lookup()
    log.info("Loaded %d fight detail entries.", len(stats_lookup))

    processed: list[dict] = []

    for fp in sorted(fighter_dir.glob("*.json")):
        if "_html_" in fp.name:
            continue
        try:
            raw = load_json(fp)
        except Exception as exc:
            log.warning("Skip %s: %s", fp.name, exc)
            continue

        name        = raw.get("name", "Unknown")
        fighter_id  = raw.get("fighter_id", fp.stem)
        record      = raw.get("record", {})
        bio_clean   = _clean_bio(raw.get("bio", {}))
        history_raw = raw.get("fight_history", [])

        history_clean = [
            _clean_fight(f, name, stats_lookup) for f in history_raw
        ]

        processed.append({
            "fighter_id":    fighter_id,
            "name":          name,
            "nickname":      raw.get("nickname", ""),
            "url":           raw.get("url", ""),
            "wins":          record.get("wins", 0),
            "losses":        record.get("losses", 0),
            "draws":         record.get("draws", 0),
            "nc":            record.get("nc", 0),
            **bio_clean,
            "fight_history": history_clean,
        })

        log.info("Processed %-25s  %d-%d-%d  (%d fights in history)",
                 name,
                 record.get("wins", 0),
                 record.get("losses", 0),
                 record.get("draws", 0),
                 len(history_clean))

    # Save JSON (full, with fight history)
    out_json = DATA_PROC / "fighters_raw.json"
    save_json(processed, out_json)

    # Save CSV (flat, no fight_history column)
    flat_rows = [{k: v for k, v in r.items() if k != "fight_history"}
                 for r in processed]
    df = pd.DataFrame(flat_rows)
    out_csv = DATA_PROC / "fighters_raw.csv"
    df.to_csv(out_csv, index=False)

    log.info("Saved %d fighters → %s and %s", len(processed), out_json.name, out_csv.name)
    return processed


if __name__ == "__main__":
    main()
