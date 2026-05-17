"""
division_filter.py — Historical stake multipliers by UFC weight class.

Analyses ledger history to compute per-division ROI, then translates that
into a stake multiplier:

  Excellent ROI (≥ +10%)  → multiplier 1.2  (bet slightly more)
  Good ROI     (+5 – +10%) → multiplier 1.0  (standard stake)
  Neutral      (-5 – +5%)  → multiplier 1.0  (standard stake)
  Poor ROI     (-5 – -15%) → multiplier 0.7  (reduce stake)
  Bad ROI      (< -15%)    → multiplier 0.5  (heavily reduce)
  Blocked      (< -25% with ≥ 10 bets) → multiplier 0.0 (skip division)

Multipliers are combined with the base stake from bankroll.py.
Division is inferred from fighter weight class stored in the ledger.

Usage:
    from division_filter import DivisionFilter, get_filter
    df = get_filter()
    mult = df.stake_multiplier("Lightweight")   # e.g. 1.0, 0.7, 0.0

    # Or get full analysis:
    df.refresh()            # recompute from ledger
    report = df.report()    # dict of division → {roi, n_bets, multiplier}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils import DATA_PROC

DB_PATH    = DATA_PROC / "betting" / "ledger.db"
CACHE_PATH = DATA_PROC.parent / "models" / "division_multipliers.json"

# Minimum settled bets before applying a penalty multiplier
MIN_BETS_FOR_PENALTY  = 8
MIN_BETS_FOR_BLOCK    = 10

# ROI thresholds → multiplier
TIER_EXCELLENT  = (+10.0, 1.20)
TIER_GOOD       = (+5.0,  1.00)
TIER_NEUTRAL    = (-5.0,  1.00)
TIER_POOR       = (-15.0, 0.70)
TIER_BAD        = (-25.0, 0.50)
TIER_BLOCKED    = (float("-inf"), 0.0)

# Default multiplier when no data is available for a division
DEFAULT_MULTIPLIER = 1.0

# Weight class name normalisation — handles common variations
_WEIGHT_CLASS_ALIASES: dict[str, str] = {
    "hw":              "Heavyweight",
    "lhw":             "Light Heavyweight",
    "mw":              "Middleweight",
    "ww":              "Welterweight",
    "lw":              "Lightweight",
    "fw":              "Featherweight",
    "bw":              "Bantamweight",
    "flw":             "Flyweight",
    "sw":              "Strawweight",
    "women straw":     "Women's Strawweight",
    "women fly":       "Women's Flyweight",
    "women bantam":    "Women's Bantamweight",
    "women feather":   "Women's Featherweight",
}

ALL_DIVISIONS = [
    "Heavyweight", "Light Heavyweight", "Middleweight", "Welterweight",
    "Lightweight", "Featherweight", "Bantamweight", "Flyweight", "Strawweight",
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight",
]


def _normalise_division(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    low = raw.lower().strip()
    for alias, canonical in _WEIGHT_CLASS_ALIASES.items():
        if alias in low:
            return canonical
    for div in ALL_DIVISIONS:
        if div.lower() in low or low in div.lower():
            return div
    return raw.strip().title()


def _safe(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_division_bets() -> dict[str, list[dict]]:
    """Load settled bets from ledger grouped by division."""
    import sqlite3
    if not DB_PATH.exists():
        return {}

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        has_division_col = False
        if "bets" in tables:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(bets)").fetchall()}
            has_division_col = "division" in cols

        if has_division_col:
            rows = conn.execute("""
                SELECT b.bet_id, b.division, b.decimal_odds,
                       b.stake_eur, b.pnl, b.status
                FROM bets b
                WHERE b.status IN ('won', 'lost') AND b.bet_type = 'single'
            """).fetchall()
        else:
            # Infer division from fight name
            rows = conn.execute("""
                SELECT b.bet_id, b.fight AS division, b.decimal_odds,
                       b.stake_eur, b.pnl, b.status
                FROM bets b
                WHERE b.status IN ('won', 'lost') AND b.bet_type = 'single'
            """).fetchall()

        grouped: dict[str, list[dict]] = {}
        for r in rows:
            div = _normalise_division(r["division"])
            grouped.setdefault(div, []).append(dict(r))
        return grouped

    except Exception:
        return {}
    finally:
        conn.close()


# ── ROI calculation ───────────────────────────────────────────────────────────

def _compute_roi(bets: list[dict]) -> dict:
    """Compute ROI and hit rate for a list of settled bets."""
    if not bets:
        return {"n": 0, "wins": 0, "roi": None, "hit_rate": None, "total_pnl": 0.0}

    wins   = sum(1 for b in bets if b.get("status") == "won")
    staked = sum(_safe(b.get("stake_eur")) for b in bets)
    pnl    = sum(_safe(b.get("pnl")) for b in bets)

    return {
        "n":         len(bets),
        "wins":      wins,
        "roi":       round(pnl / staked * 100, 1) if staked > 0 else None,
        "hit_rate":  round(wins / len(bets) * 100, 1) if bets else None,
        "total_pnl": round(pnl, 2),
    }


def _roi_to_multiplier(roi: float | None, n_bets: int) -> float:
    """Convert historical ROI to a stake multiplier."""
    if roi is None or n_bets < MIN_BETS_FOR_PENALTY:
        return DEFAULT_MULTIPLIER  # not enough data → use standard

    if roi >= TIER_EXCELLENT[0]:
        return TIER_EXCELLENT[1]
    if roi >= TIER_GOOD[0]:
        return TIER_GOOD[1]
    if roi >= TIER_NEUTRAL[0]:
        return TIER_NEUTRAL[1]

    # Only apply penalty multipliers if we have enough data
    if n_bets >= MIN_BETS_FOR_BLOCK and roi < TIER_BAD[0]:
        return TIER_BLOCKED[1]  # 0.0 — block entirely
    if roi >= TIER_POOR[0]:
        return TIER_POOR[1]
    return TIER_BAD[1]


# ── DivisionFilter class ──────────────────────────────────────────────────────

class DivisionFilter:
    """
    Provides historical stake multipliers per UFC weight class.

    Multipliers are loaded from cache on init and refreshed on demand.
    When the ledger has insufficient data for a division, DEFAULT_MULTIPLIER (1.0)
    is returned so no bets are blocked prematurely.
    """

    def __init__(self) -> None:
        self._multipliers: dict[str, float] = {}
        self._stats: dict[str, dict] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if CACHE_PATH.exists():
            try:
                data = json.loads(CACHE_PATH.read_text())
                self._multipliers = data.get("multipliers", {})
                self._stats       = data.get("stats", {})
            except Exception:
                pass

    def refresh(self) -> dict[str, dict]:
        """
        Recompute multipliers from ledger and cache results.
        Returns the full division stats dict.
        """
        grouped = _load_division_bets()
        multipliers: dict[str, float] = {}
        stats:       dict[str, dict]  = {}

        for div, bets in grouped.items():
            roi_data = _compute_roi(bets)
            mult = _roi_to_multiplier(roi_data["roi"], roi_data["n"])
            multipliers[div] = mult
            stats[div] = {**roi_data, "multiplier": mult}

        self._multipliers = multipliers
        self._stats       = stats

        cache = {"multipliers": multipliers, "stats": stats}
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str))

        return stats

    def stake_multiplier(self, division: str | None) -> float:
        """
        Return stake multiplier for a division.
        Returns 1.0 (no adjustment) if division is unknown or has insufficient data.
        """
        if not division:
            return DEFAULT_MULTIPLIER
        norm = _normalise_division(division)
        return self._multipliers.get(norm, DEFAULT_MULTIPLIER)

    def is_blocked(self, division: str | None) -> bool:
        """Return True if the division is currently blocked (multiplier = 0.0)."""
        return self.stake_multiplier(division) == 0.0

    def report(self) -> dict[str, dict]:
        """Return full stats for all tracked divisions."""
        return dict(self._stats)

    def print_report(self) -> None:
        if not self._stats:
            print("[DivisionFilter] No data. Run refresh() first.")
            return

        print(f"\n{'='*70}")
        print(f"  DIVISION FILTER — STAKE MULTIPLIERS")
        print(f"{'='*70}")
        print(f"  {'Division':<26}  {'Bets':>4}  {'Hit%':>6}  {'ROI%':>7}  {'Mult':>5}  Status")
        print(f"  {'-'*26}  {'-'*4}  {'-'*6}  {'-'*7}  {'-'*5}  {'-'*10}")

        for div in ALL_DIVISIONS:
            s = self._stats.get(div)
            if s is None:
                print(f"  {div:<26}  {'—':>4}  {'—':>6}  {'—':>7}  {DEFAULT_MULTIPLIER:>5.1f}  No data")
                continue

            mult   = s.get("multiplier", DEFAULT_MULTIPLIER)
            n      = s.get("n", 0)
            hr     = s.get("hit_rate")
            roi    = s.get("roi")
            status = "BLOCKED" if mult == 0.0 else ("REDUCED" if mult < 1.0 else ("BOOSTED" if mult > 1.0 else "OK"))

            print(f"  {div:<26}  {n:>4}  "
                  f"{str(hr) + '%' if hr is not None else '—':>6}  "
                  f"{str(roi) + '%' if roi is not None else '—':>7}  "
                  f"{mult:>5.1f}  {status}")

        # Also print any non-standard divisions
        extra = set(self._stats.keys()) - set(ALL_DIVISIONS)
        for div in sorted(extra):
            s = self._stats[div]
            mult = s.get("multiplier", DEFAULT_MULTIPLIER)
            n = s.get("n", 0)
            hr = s.get("hit_rate")
            roi = s.get("roi")
            status = "BLOCKED" if mult == 0.0 else ("REDUCED" if mult < 1.0 else "OK")
            print(f"  {div:<26}  {n:>4}  "
                  f"{str(hr) + '%' if hr is not None else '—':>6}  "
                  f"{str(roi) + '%' if roi is not None else '—':>7}  "
                  f"{mult:>5.1f}  {status}")

        print(f"{'='*70}")
        print(f"  Blocked divisions: none placed until ≥{MIN_BETS_FOR_BLOCK} bets")
        print(f"  Penalty applied after ≥{MIN_BETS_FOR_PENALTY} settled bets")


# ── Singleton ─────────────────────────────────────────────────────────────────

_filter: DivisionFilter | None = None


def get_filter() -> DivisionFilter:
    global _filter
    if _filter is None:
        _filter = DivisionFilter()
    return _filter


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Division stake multiplier analysis.")
    parser.add_argument("--refresh", action="store_true",
                        help="Recompute multipliers from ledger")
    parser.add_argument("--division", type=str, default=None,
                        help="Show multiplier for a specific division")
    args = parser.parse_args()

    df = get_filter()

    if args.refresh:
        print("Refreshing from ledger ...")
        df.refresh()

    if args.division:
        mult = df.stake_multiplier(args.division)
        norm = _normalise_division(args.division)
        print(f"\n  Division '{norm}': multiplier = {mult:.2f}"
              f"{'  [BLOCKED]' if mult == 0.0 else ''}")
    else:
        df.print_report()


if __name__ == "__main__":
    main()
