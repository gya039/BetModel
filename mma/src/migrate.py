"""
migrate.py — one-time migration from archived JSON snapshots to the ledger.

Run once from the repo root:
    python mma/src/migrate.py

What it does:
  1. Scans data/processed/betting/ for staking_plan_*.json files
  2. Registers each archived event in the ledger (idempotent)
  3. Writes any bets in those plans as pending (INSERT OR IGNORE, so re-running is safe)
  4. If the event has already been played (event_date < today), marks it as 'settling'
     so it shows up on /history ready for manual result entry

It does NOT invent results — you must still go to /settle/<event_id> and enter
the actual outcomes for each historical card.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make sure src/ is on the path when run directly
sys.path.insert(0, str(Path(__file__).parent))

from utils import DATA_PROC, load_json, get_logger
import ledger

log = get_logger("migrate")

BETTING_DIR = DATA_PROC / "betting"
_TODAY = datetime.now(timezone.utc).date()


def _parse_event_date(date_str: str):
    """Try a few formats: 'May 02, 2026', '2026-05-02', etc."""
    for fmt in ("%B %d, %Y", "%Y-%m-%d", "%B %d %Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt).date()
        except ValueError:
            continue
    return None


def migrate() -> None:
    plans = sorted(BETTING_DIR.glob("staking_plan_*.json"))
    if not plans:
        log.info("No archived staking plans found — nothing to migrate.")
        return

    log.info("Found %d archived staking plan(s).", len(plans))

    for plan_path in plans:
        try:
            plan = load_json(plan_path)
        except Exception as exc:
            log.warning("Could not read %s: %s", plan_path.name, exc)
            continue

        event_name = plan.get("event_name", "")
        event_date = plan.get("event_date", "")
        event_url  = plan.get("event_url", "")

        if not event_url:
            log.warning("Skipping %s — no event_url in plan.", plan_path.name)
            continue

        # Try to load the matching edges snapshot
        slug = plan_path.stem.replace("staking_plan_", "")
        edges_path = BETTING_DIR / f"betting_edges_{slug}.json"
        edges = None
        if edges_path.exists():
            try:
                edges = load_json(edges_path)
            except Exception:
                pass

        # Register event (idempotent)
        event_id = ledger.register_event(
            event_name=event_name,
            event_date=event_date,
            event_url=event_url,
            card_json=None,
            edges_json=edges,
        )
        log.info("Registered: %s (%s)", event_name, event_id)

        # Place bets (INSERT OR IGNORE — safe to re-run)
        bankroll = float(plan.get("bankroll") or 500.0)
        singles      = plan.get("singles", [])
        accumulators = plan.get("accumulators", [])

        placed = ledger.place_bets(
            event_id=event_id,
            singles=singles,
            accumulators=accumulators,
            bankroll=bankroll,
        )
        log.info("  → %d bet(s) placed (or already existed)", len(placed))

        # If the event date is in the past, advance status to 'settling'
        # so it appears on /history flagged as needing manual settlement
        parsed = _parse_event_date(event_date)
        if parsed and parsed < _TODAY:
            event = ledger.get_event(event_id)
            if event and event["status"] == "active":
                import sqlite3
                from ledger import _conn, _utcnow
                with _conn() as con:
                    con.execute(
                        "UPDATE events SET status='settling' WHERE event_id=? AND status='active'",
                        (event_id,),
                    )
                log.info("  → Marked as 'settling' (event date %s is in the past)", parsed)

    log.info("Migration complete. Visit /history to enter results for past cards.")


if __name__ == "__main__":
    migrate()
