"""
dataset_builder.py — Leakage-free UFC fight training dataset.

Constructs one row per fight pair. Each row contains:
  - Pre-fight features for fighter A (computed from fights BEFORE this one only)
  - Pre-fight features for fighter B (same)
  - Difference features (A minus B)
  - Target: 1 if fighter A won, 0 if lost (draws and NC excluded)

Data leakage prevention:
  - Fight history is sorted chronologically BEFORE feature computation.
  - For fight at index i, only fights [0..i-1] are used.
  - Career bio stats (UFCStats cumulative) are used with shrinkage as priors,
    but rolling stats from history override them as sample grows.
  - Test set is always the most recent fights (time-based split, not random).

Usage:
    python dataset_builder.py
    python dataset_builder.py --output custom/path.csv

Output:
    mma/data/processed/training_data.csv
    mma/data/processed/training_data_meta.json
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from features import (
    FEATURE_COLUMNS,
    compute_fighter_features,
    compute_matchup_features,
    parse_date,
    result_value,
)
from elo import build_elo_system, get_pre_fight_elos
from utils import DATA_PROC, DATA_RAW, load_json

OUTPUT_CSV  = DATA_PROC / "training_data.csv"
OUTPUT_META = DATA_PROC / "training_data_meta.json"
MODELS_DIR  = DATA_PROC.parent / "models"


def _fighter_by_url(fighters: list[dict]) -> dict[str, dict]:
    """Index fighters by their profile URL."""
    return {f.get("url", ""): f for f in fighters if f.get("url")}


def _fighter_by_id(fighters: list[dict]) -> dict[str, dict]:
    return {f.get("fighter_id", ""): f for f in fighters if f.get("fighter_id")}


def _fights_chronological(fighter: dict) -> list[dict]:
    """
    Return fight history sorted oldest-first.
    The raw data stores fights most-recent-first, so we reverse it.
    Fights with unparseable dates are appended at the start (oldest assumed).
    """
    history = list(fighter.get("fight_history", []))
    dated = []
    undated = []
    for f in history:
        d = parse_date(f.get("event_date"))
        if d is not None:
            dated.append((d, f))
        else:
            undated.append(f)
    dated.sort(key=lambda x: x[0])
    return [f for _, f in dated] + undated  # undated at front (assume oldest)


def _infer_weight_class(fight: dict, fighter: dict) -> str | None:
    """Try to determine weight class from fight event name or fighter weight."""
    event = fight.get("event", "")
    wc_keywords = [
        "Strawweight", "Flyweight", "Bantamweight", "Featherweight",
        "Lightweight", "Welterweight", "Middleweight", "Light Heavyweight",
        "Heavyweight", "Women's",
    ]
    for kw in wc_keywords:
        if kw.lower() in event.lower():
            return kw
    # Fallback: infer from fighter weight
    w = float(fighter.get("weight_lbs") or 0)
    if w <= 115: return "Strawweight"
    if w <= 125: return "Flyweight"
    if w <= 135: return "Bantamweight"
    if w <= 145: return "Featherweight"
    if w <= 155: return "Lightweight"
    if w <= 170: return "Welterweight"
    if w <= 185: return "Middleweight"
    if w <= 205: return "Light Heavyweight"
    return "Heavyweight"


def build_fighter_timelines(
    fighters: list[dict],
    elo_sys,
) -> dict[str, list[dict]]:
    """
    For each fighter, compute pre-fight features for every fight they've had.

    Returns: {fight_url: [record_for_fighter_a, record_for_fighter_b]}
    Keyed by fight_url so we can match opponents later.
    """
    by_url: dict[str, dict] = _fighter_by_url(fighters)
    fights_by_fighturl: dict[str, list[dict]] = defaultdict(list)

    for fighter in fighters:
        fid = fighter.get("fighter_id", "")
        fights_chrono = _fights_chronological(fighter)

        for i, fight in enumerate(fights_chrono):
            # Skip non-decisive results
            rv = result_value(fight)
            if rv is None:
                continue

            fight_url  = fight.get("fight_url") or fight.get("fight_id") or ""
            event_date = parse_date(fight.get("event_date"))
            division   = _infer_weight_class(fight, fighter)

            # Pre-fight Elo
            elo_a, _ = get_pre_fight_elos(elo_sys, fid, fight.get("opponent_url", ""), fight_url)

            # Compute features using only fights BEFORE index i
            prior_fights = fights_chrono[:i]
            features = compute_fighter_features(
                fighter=fighter,
                prior_fights=prior_fights,
                fight_date=event_date,
                division=division,
                elo=elo_a,
            )

            fights_by_fighturl[fight_url].append({
                "fighter_id":   fid,
                "fighter_url":  fighter.get("url", ""),
                "opponent_url": fight.get("opponent_url", ""),
                "fight_url":    fight_url,
                "event_date":   fight.get("event_date", ""),
                "event_date_parsed": event_date,
                "division":     division,
                "result":       int(rv),
                "method":       fight.get("method", "Decision"),
                "features":     features,
            })

    return fights_by_fighturl


def build_matchup_rows(
    fights_by_fighturl: dict[str, list[dict]],
) -> list[dict]:
    """
    Match fighter records by fight_url and produce one row per fight pair.
    Skips fights where we can't find both fighters.
    """
    rows = []
    for fight_url, records in fights_by_fighturl.items():
        # We may have 1 or 2 records per fight (depending on whether both fighters are in our dataset)
        if len(records) < 2:
            continue

        # If somehow more than 2 (shouldn't happen), take first two
        a, b = records[0], records[1]

        # Ensure they are indeed opponents (sanity check)
        if a.get("result") == b.get("result"):
            # Both same result — likely an error in the data
            continue

        # Make a=winner, b=loser (for consistent target encoding)
        if a["result"] == 0:
            a, b = b, a

        event_date = a.get("event_date_parsed")

        feat_a = a["features"]
        feat_b = b["features"]
        matchup = compute_matchup_features(feat_a, feat_b)

        row = {
            "fight_url":   fight_url,
            "event_date":  a.get("event_date", ""),
            "division":    a.get("division", ""),
            "method":      a.get("method", "Decision"),
            "winner_id":   a["fighter_id"],
            "loser_id":    b["fighter_id"],
            # Matchup (diff) features
            **matchup,
            # Raw individual features (prefixed) — useful for analysis
            **{f"a_{k}": v for k, v in feat_a.items()},
            **{f"b_{k}": v for k, v in feat_b.items()},
            # Target
            "target": 1,  # Always 1 (winner=A by construction above)
        }
        rows.append(row)

        # Also add the flipped row (loser perspective) — doubles dataset size
        # and prevents model from learning fighter ordering bias
        flipped = {
            "fight_url":   fight_url,
            "event_date":  a.get("event_date", ""),
            "division":    a.get("division", ""),
            "method":      a.get("method", "Decision"),
            "winner_id":   b["fighter_id"],
            "loser_id":    a["fighter_id"],
            **compute_matchup_features(feat_b, feat_a),
            **{f"a_{k}": v for k, v in feat_b.items()},
            **{f"b_{k}": v for k, v in feat_a.items()},
            "target": 0,  # loser perspective
        }
        rows.append(flipped)

    # Sort chronologically
    rows.sort(key=lambda r: r.get("event_date", ""))
    return rows


def save_dataset(rows: list[dict], output: Path = OUTPUT_CSV) -> None:
    if not rows:
        print("[WARN] No rows to save.")
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    all_keys = list(rows[0].keys())
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} rows → {output}")


def save_meta(rows: list[dict], meta_path: Path = OUTPUT_META) -> None:
    if not rows:
        return
    dates = [r.get("event_date", "") for r in rows if r.get("event_date")]
    dates.sort()
    meta = {
        "total_rows":    len(rows),
        "unique_fights": len(rows) // 2,  # each fight produces 2 rows (A+B perspectives)
        "earliest":      dates[0] if dates else None,
        "latest":        dates[-1] if dates else None,
        "feature_columns": FEATURE_COLUMNS,
        "divisions":     sorted(set(r.get("division", "") for r in rows if r.get("division"))),
    }
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"Meta → {meta_path}")


def build_dataset(
    fighters_path: Path | None = None,
    output_csv: Path = OUTPUT_CSV,
    output_meta: Path = OUTPUT_META,
) -> list[dict]:
    """
    Full pipeline: load raw fighters → build timeline features → match pairs → save CSV.

    Returns the list of row dicts (also saved to disk).
    """
    path = fighters_path or (DATA_PROC / "fighters_raw.json")
    if not path.exists():
        raise FileNotFoundError(
            f"fighters_raw.json not found at {path}. "
            "Run preprocess.py first."
        )

    print(f"Loading fighters from {path} ...")
    fighters = load_json(path)
    print(f"  {len(fighters)} fighters loaded.")

    print("Building Elo ratings from fight history ...")
    elo_sys = build_elo_system(fighters)

    print("Computing pre-fight features for every fight (leakage-free) ...")
    fights_by_url = build_fighter_timelines(fighters, elo_sys)
    print(f"  {len(fights_by_url)} unique fight URLs processed.")

    print("Matching fight pairs and computing diff features ...")
    rows = build_matchup_rows(fights_by_url)
    print(f"  {len(rows)} training rows ({len(rows)//2} unique fights).")

    if rows:
        save_dataset(rows, output_csv)
        save_meta(rows, output_meta)

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build UFC training dataset (leakage-free).")
    parser.add_argument("--fighters", type=Path, default=None,
                        help="Path to fighters_raw.json (default: data/processed/fighters_raw.json)")
    parser.add_argument("--output", type=Path, default=OUTPUT_CSV,
                        help="Output CSV path")
    parser.add_argument("--meta", type=Path, default=OUTPUT_META,
                        help="Output meta JSON path")
    args = parser.parse_args()

    rows = build_dataset(args.fighters, args.output, args.meta)
    print(f"\nDone. {len(rows)} rows written.")
    if rows:
        cols = [k for k in rows[0].keys() if k in FEATURE_COLUMNS or k.startswith("diff_")]
        print(f"Feature columns: {len(FEATURE_COLUMNS)}")
        print(f"Total columns in output: {len(rows[0])}")


if __name__ == "__main__":
    main()
