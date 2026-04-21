"""
check_movement.py - Saturday odds movement check for Octagon IQ.

Compares the stored odds snapshot against fresh fight-day odds, regenerates
betting edges, and writes a movement report.

Run:
    python src/check_movement.py
    python src/check_movement.py --no-fetch
"""
from __future__ import annotations

import argparse
import csv
import shutil
from datetime import datetime, timezone
from pathlib import Path

from betting_model import generate_card_betting, save_outputs
from fetch_odds import DEFAULT_MARKETS, _load_env_file, fetch_odds, save_odds
from utils import DATA_PROC, DATA_RAW, get_logger, save_json

log = get_logger("check_movement")

MOVEMENT_DIR = DATA_PROC / "betting"
MOVEMENT_JSON = MOVEMENT_DIR / "movement_report.json"
MOVEMENT_CSV = MOVEMENT_DIR / "movement_report.csv"


def latest_odds_path() -> Path:
    return DATA_RAW / "odds" / "latest.json"


def snapshot_baseline() -> Path | None:
    """Copy latest odds before refreshing so movement has a frozen baseline."""
    latest = latest_odds_path()
    if not latest.exists() or latest.stat().st_size == 0:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    baseline = DATA_RAW / "odds" / f"movement_baseline_{stamp}.json"
    baseline.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(latest, baseline)
    return baseline


def priced_rows(analyses: list[dict]) -> dict[tuple, dict]:
    rows = {}
    for fight in analyses:
        for row in fight.get("markets", []):
            if row.get("odds") == "":
                continue
            key = (
                row.get("fight_id"),
                row.get("market"),
                row.get("selection"),
                row.get("sportsbook"),
            )
            rows[key] = row
    return rows


def pct_change(old: float | None, new: float | None) -> float | None:
    if old in (None, 0) or new is None:
        return None
    return round(((new - old) / old) * 100, 1)


def movement_label(old_decimal: float | None, new_decimal: float | None) -> str:
    if old_decimal is None or new_decimal is None:
        return "New Market"
    diff = round(new_decimal - old_decimal, 2)
    if abs(diff) < 0.03:
        return "Stable"
    if diff < 0:
        return "Shortened"
    return "Drifted"


def movement_note(row: dict, old_row: dict | None, label: str) -> str:
    selection = row.get("selection", "")
    edge = row.get("edge")
    if old_row is None:
        return f"{selection} is newly available in the current feed."
    old_dec = old_row.get("decimal_odds")
    new_dec = row.get("decimal_odds")
    if label == "Shortened":
        if edge is not None and edge > 5:
            return f"{selection} shortened from {old_dec:.2f} to {new_dec:.2f}; value remains, but the number is less generous."
        return f"{selection} shortened from {old_dec:.2f} to {new_dec:.2f}; market has moved toward this outcome."
    if label == "Drifted":
        if edge is not None and edge > 5:
            return f"{selection} drifted from {old_dec:.2f} to {new_dec:.2f}; price is more attractive if the model read is trusted."
        return f"{selection} drifted from {old_dec:.2f} to {new_dec:.2f}; market is easing away from this outcome."
    return f"{selection} is broadly stable around {new_dec:.2f}."


def build_report(baseline_path: Path | None, current_path: Path) -> dict:
    current = generate_card_betting(current_path)
    baseline = generate_card_betting(baseline_path) if baseline_path and baseline_path.exists() else []
    baseline_rows = priced_rows(baseline)

    movement_rows = []
    for fight in current:
        for row in fight.get("markets", []):
            if row.get("odds") == "":
                continue
            key = (
                row.get("fight_id"),
                row.get("market"),
                row.get("selection"),
                row.get("sportsbook"),
            )
            old = baseline_rows.get(key)
            old_decimal = old.get("decimal_odds") if old else None
            new_decimal = row.get("decimal_odds")
            label = movement_label(old_decimal, new_decimal)
            decimal_move = round(new_decimal - old_decimal, 2) if old_decimal and new_decimal else None
            implied_move = (
                round((row.get("implied_probability") - old.get("implied_probability")) * 100, 1)
                if old and row.get("implied_probability") is not None and old.get("implied_probability") is not None
                else None
            )
            movement_rows.append(
                {
                    "fight_id": row.get("fight_id"),
                    "fight": row.get("fight"),
                    "market": row.get("market"),
                    "selection": row.get("selection"),
                    "sportsbook": row.get("sportsbook"),
                    "old_decimal_odds": old_decimal if old_decimal is not None else "",
                    "new_decimal_odds": new_decimal,
                    "old_american_odds": old.get("odds") if old else "",
                    "new_american_odds": row.get("odds"),
                    "decimal_move": decimal_move if decimal_move is not None else "",
                    "decimal_move_pct": pct_change(old_decimal, new_decimal) if old_decimal else "",
                    "implied_probability": row.get("implied_probability"),
                    "implied_move_pct_points": implied_move if implied_move is not None else "",
                    "model_probability": row.get("model_probability"),
                    "edge": row.get("edge"),
                    "confidence": row.get("confidence"),
                    "value_label": row.get("label"),
                    "movement_label": label,
                    "note": movement_note(row, old, label),
                }
            )

    noteworthy = [
        row for row in movement_rows
        if row["movement_label"] in {"Shortened", "Drifted"}
        or row.get("value_label") in {"Best Bet", "Lean", "Small Value"}
        or (isinstance(row.get("edge"), (int, float)) and abs(row["edge"]) >= 5)
    ]
    noteworthy.sort(
        key=lambda row: (
            abs(row["decimal_move"]) if isinstance(row.get("decimal_move"), (int, float)) else 0,
            abs(row["edge"]) if isinstance(row.get("edge"), (int, float)) else 0,
        ),
        reverse=True,
    )

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "generated_at": fetched_at,
        "baseline_file": str(baseline_path) if baseline_path else "",
        "current_file": str(current_path),
        "markets_compared": len(movement_rows),
        "noteworthy_count": len(noteworthy),
        "rows": movement_rows,
        "noteworthy": noteworthy[:30],
    }


def save_report(report: dict) -> None:
    MOVEMENT_DIR.mkdir(parents=True, exist_ok=True)
    save_json(report, MOVEMENT_JSON)
    fieldnames = [
        "fight",
        "market",
        "selection",
        "sportsbook",
        "old_decimal_odds",
        "new_decimal_odds",
        "old_american_odds",
        "new_american_odds",
        "decimal_move",
        "decimal_move_pct",
        "implied_probability",
        "implied_move_pct_points",
        "model_probability",
        "edge",
        "confidence",
        "value_label",
        "movement_label",
        "note",
    ]
    with MOVEMENT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def run_check(fetch: bool = True, regions: str = "us", markets: str = DEFAULT_MARKETS) -> dict:
    _load_env_file()
    baseline = snapshot_baseline()
    if fetch:
        import os

        api_key = os.getenv("ODDS_API_KEY")
        if not api_key:
            raise RuntimeError("ODDS_API_KEY is not set. Add it to E:\\BettingModel\\.env or mma\\.env.")
        payload = fetch_odds(api_key, regions=regions, markets=markets, odds_format="american")
        current = save_odds(payload, markets=markets, regions=regions)
    else:
        current = latest_odds_path()
        if not current.exists():
            raise RuntimeError("No latest odds snapshot found. Run python src/fetch_odds.py first.")

    analyses = generate_card_betting(current)
    save_outputs(analyses)
    report = build_report(baseline, current)
    save_report(report)
    return report


def print_summary(report: dict) -> None:
    print("\n" + "=" * 72)
    print("  OCTAGON IQ ODDS MOVEMENT CHECK")
    print("=" * 72)
    print(f"  Markets compared: {report['markets_compared']}")
    print(f"  Noteworthy moves: {report['noteworthy_count']}")
    print(f"  Report: {MOVEMENT_JSON}")
    print(f"  CSV:    {MOVEMENT_CSV}\n")

    if not report["noteworthy"]:
        print("  No notable price movement or value shifts found.\n")
        return

    print(f"  {'FIGHT':<34} {'MARKET':<18} {'OLD':>6} {'NOW':>6} {'MOVE':>8}  LABEL")
    print("-" * 72)
    for row in report["noteworthy"][:12]:
        fight = row["fight"][:33]
        market = row["market"][:17]
        old = f"{row['old_decimal_odds']:.2f}" if isinstance(row["old_decimal_odds"], (int, float)) else "--"
        now = f"{row['new_decimal_odds']:.2f}" if isinstance(row["new_decimal_odds"], (int, float)) else "--"
        move = f"{row['decimal_move']:+.2f}" if isinstance(row["decimal_move"], (int, float)) else "--"
        print(f"  {fight:<34} {market:<18} {old:>6} {now:>6} {move:>8}  {row['movement_label']} / {row['value_label']}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check UFC odds movement for the current Octagon IQ card.")
    parser.add_argument("--no-fetch", action="store_true", help="Do not fetch fresh odds; compare current latest snapshot only.")
    parser.add_argument("--regions", default="us")
    parser.add_argument("--markets", default=DEFAULT_MARKETS)
    args = parser.parse_args()
    report = run_check(fetch=not args.no_fetch, regions=args.regions, markets=args.markets)
    print_summary(report)


if __name__ == "__main__":
    main()
