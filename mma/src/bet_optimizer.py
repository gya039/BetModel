"""
bet_optimizer.py — Find optimal betting thresholds via historical simulation.

Grid-searches edge threshold and confidence threshold combinations to find
which parameters maximise risk-adjusted ROI over historical bets.

Also identifies:
  - Whether underdogs or favorites perform better at each threshold
  - Which divisions should be avoided
  - Optimal stake size given model accuracy

Usage:
    python bet_optimizer.py
    python bet_optimizer.py --edge-range 3 12 --stake 0.5
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from utils import DATA_PROC

DB_PATH = DATA_PROC / "betting" / "ledger.db"


def _safe(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


# ── Data loading ──────────────────────────────────────────────────────────────

def load_bets() -> list[dict]:
    import sqlite3
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Ledger DB not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        has_cal = "calibration_predictions" in tables

        if has_cal:
            rows = conn.execute("""
                SELECT b.bet_id, b.fight, b.market, b.selection,
                       b.decimal_odds, b.stake_eur, b.edge, b.confidence,
                       b.status, b.pnl, e.event_date,
                       cp.model_probability, cp.market_probability
                FROM bets b
                JOIN events e ON b.event_id = e.event_id
                LEFT JOIN calibration_predictions cp ON b.bet_id = cp.bet_id
                WHERE b.status IN ('won', 'lost', 'push')
                  AND b.bet_type = 'single'
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT b.bet_id, b.fight, b.market, b.selection,
                       b.decimal_odds, b.stake_eur, b.edge, b.confidence,
                       b.status, b.pnl, e.event_date,
                       NULL AS model_probability, NULL AS market_probability
                FROM bets b
                JOIN events e ON b.event_id = e.event_id
                WHERE b.status IN ('won', 'lost', 'push')
                  AND b.bet_type = 'single'
            """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate(
    bets: list[dict],
    edge_min: float,
    confidence_allow: set[str] | None,
    stake_pct: float = 0.005,
    starting_bankroll: float = 500.0,
) -> dict:
    """
    Simulate flat-staking bankroll over historical bets with given filters.
    Returns ROI, hit rate, drawdown, and Sharpe-like ratio.
    """
    filtered = [
        b for b in bets
        if _safe(b.get("edge")) >= edge_min
        and (confidence_allow is None or b.get("confidence") in confidence_allow)
        and b.get("status") in ("won", "lost")
    ]
    if not filtered:
        return {"edge_min": edge_min, "n_bets": 0, "roi": None}

    bankroll = starting_bankroll
    peak = bankroll
    max_dd = 0.0
    wins = losses = 0
    returns = []

    for b in sorted(filtered, key=lambda x: x.get("event_date", "")):
        stake = bankroll * stake_pct
        status = b["status"]
        dec_odds = _safe(b.get("decimal_odds"), 2.0)
        if status == "won":
            pnl = stake * (dec_odds - 1)
            wins += 1
        else:
            pnl = -stake
            losses += 1
        bankroll += pnl
        returns.append(pnl / starting_bankroll)
        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak
        if dd > max_dd:
            max_dd = dd

    total = wins + losses
    staked = total * starting_bankroll * stake_pct
    pnl_total = bankroll - starting_bankroll

    mean_r = sum(returns) / len(returns) if returns else 0
    import math
    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns)) if len(returns) > 1 else 0
    sharpe = round(mean_r / std_r * math.sqrt(len(returns)), 2) if std_r > 0 else 0

    return {
        "edge_min":     edge_min,
        "confidence":   sorted(confidence_allow) if confidence_allow else "all",
        "n_bets":       total,
        "wins":         wins,
        "losses":       losses,
        "hit_rate":     round(wins / total * 100, 1) if total else None,
        "total_staked": round(staked, 2),
        "total_pnl":    round(pnl_total, 2),
        "roi":          round(pnl_total / staked * 100, 2) if staked else None,
        "max_drawdown": round(max_dd * 100, 1),
        "sharpe":       sharpe,
        "final_bankroll": round(bankroll, 2),
    }


# ── Grid search ───────────────────────────────────────────────────────────────

def grid_search(
    bets: list[dict],
    edge_range: tuple[float, float] = (3.0, 15.0),
    edge_step: float = 1.0,
    stake_pct: float = 0.005,
) -> list[dict]:
    """
    Sweep edge thresholds (with all confidence tiers) to find optimal cutoff.
    """
    results = []
    edge_min = edge_range[0]
    while edge_min <= edge_range[1]:
        r = simulate(bets, edge_min=edge_min, confidence_allow=None, stake_pct=stake_pct)
        results.append(r)
        edge_min = round(edge_min + edge_step, 1)

    # Also test confidence-filtered variants
    for conf in [{"High"}, {"High", "Medium"}, {"High", "Medium", "Low-Medium"}]:
        r = simulate(bets, edge_min=4.0, confidence_allow=conf, stake_pct=stake_pct)
        r["note"] = f"Edge≥4% + confidence={sorted(conf)}"
        results.append(r)

    return results


def find_optimal(results: list[dict]) -> dict | None:
    """Return the parameter set with the best ROI that has at least 5 bets."""
    candidates = [r for r in results if r.get("n_bets", 0) >= 5 and r.get("roi") is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r["roi"])


# ── Division analysis ─────────────────────────────────────────────────────────

def division_analysis(bets: list[dict]) -> list[dict]:
    """
    Break down performance by inferred weight class.
    NOTE: division inference from fight name is imprecise without a dedicated division field.
    This will improve once division data is stored in the ledger.
    """
    _DIV_KEYWORDS = [
        "Heavyweight", "Light Heavyweight", "Middleweight", "Welterweight",
        "Lightweight", "Featherweight", "Bantamweight", "Flyweight",
        "Strawweight", "Women",
    ]

    def infer_div(fight_name: str) -> str:
        f = fight_name or ""
        for kw in _DIV_KEYWORDS:
            if kw.lower() in f.lower():
                return kw
        return "Unknown"

    by_div: dict[str, list] = {}
    for b in bets:
        div = infer_div(b.get("fight", ""))
        by_div.setdefault(div, []).append(b)

    result = []
    for div, group in by_div.items():
        settled = [b for b in group if b.get("status") in ("won", "lost")]
        wins = sum(1 for b in settled if b["status"] == "won")
        staked = sum(_safe(b.get("stake_eur")) for b in settled)
        pnl = sum(_safe(b.get("pnl")) for b in settled)
        result.append({
            "division": div,
            "bets": len(group),
            "settled": len(settled),
            "wins": wins,
            "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
            "pnl": round(pnl, 2),
            "roi": round(pnl / staked * 100, 1) if staked else None,
            "recommendation": (
                "AVOID — negative ROI" if pnl < -5 and len(settled) >= 5
                else "WATCH — limited data" if len(settled) < 5
                else "OK"
            ),
        })

    result.sort(key=lambda r: (r.get("roi") or -999), reverse=True)
    return result


def underdog_vs_fav_at_thresholds(
    bets: list[dict],
    thresholds: list[float] = [2.0, 2.5, 3.0, 3.5],
) -> list[dict]:
    """Underdog vs favorite performance at different odds thresholds."""
    result = []
    for threshold in thresholds:
        underdogs = [b for b in bets if _safe(b.get("decimal_odds"), 2.0) >= threshold]
        favorites = [b for b in bets if _safe(b.get("decimal_odds"), 2.0) < threshold]

        def stats(group):
            settled = [b for b in group if b.get("status") in ("won", "lost")]
            wins = sum(1 for b in settled if b["status"] == "won")
            staked = sum(_safe(b.get("stake_eur")) for b in settled)
            pnl = sum(_safe(b.get("pnl")) for b in settled)
            return {
                "n": len(settled),
                "wins": wins,
                "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
                "roi": round(pnl / staked * 100, 1) if staked else None,
            }

        result.append({
            "threshold": threshold,
            "label": f"odds >= {threshold}x = underdog",
            "underdogs": stats(underdogs),
            "favorites": stats(favorites),
        })

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    edge_range: tuple[float, float] = (3.0, 15.0),
    stake_pct: float = 0.005,
    output_json: Path | None = None,
) -> dict:
    try:
        bets = load_bets()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return {}

    settled = [b for b in bets if b.get("status") in ("won", "lost")]
    print(f"Loaded {len(bets)} bets ({len(settled)} settled).\n")

    if len(settled) < 10:
        print("Insufficient settled bets for meaningful optimization.")
        print("Need at least 10 settled bets. Keep tracking and re-run.")
        return {"error": "insufficient_data", "settled": len(settled)}

    print("Running grid search over edge thresholds ...")
    grid = grid_search(bets, edge_range=edge_range, stake_pct=stake_pct)
    best = find_optimal(grid)

    print(f"\n{'='*60}")
    print(f"  EDGE THRESHOLD SWEEP  (flat {stake_pct*100:.1f}% staking)")
    print(f"{'='*60}")
    print(f"  {'Edge':>6}  {'Bets':>5}  {'Hit%':>6}  {'ROI%':>7}  {'MaxDD%':>7}  {'Sharpe':>7}")
    print(f"  {'-'*6}  {'-'*5}  {'-'*6}  {'-'*7}  {'-'*7}  {'-'*7}")
    for r in [x for x in grid if isinstance(x.get("edge_min"), float)]:
        marker = " ◄ OPTIMAL" if r is best else ""
        print(f"  {r['edge_min']:>6.1f}  {r['n_bets']:>5}  "
              f"{str(r.get('hit_rate') or '—'):>6}  "
              f"{str(r.get('roi') or '—'):>7}  "
              f"{r.get('max_drawdown', '—'):>7}  "
              f"{r.get('sharpe', '—'):>7}{marker}")

    if best:
        print(f"\nRecommended threshold: edge >= {best['edge_min']}%")
        print(f"  ROI: {best['roi']}%  |  Hit rate: {best['hit_rate']}%  |  "
              f"Bets: {best['n_bets']}  |  Sharpe: {best['sharpe']}")

    print(f"\nDivision performance:")
    divs = division_analysis(bets)
    for d in divs:
        print(f"  {d['division']:<20} {d['settled']:>3} bets  {str(d.get('roi') or '—'):>7}% ROI  {d['recommendation']}")

    print(f"\nUnderdog vs Favorite:")
    uvf = underdog_vs_fav_at_thresholds(bets)
    for row in uvf:
        u = row["underdogs"]
        fav = row["favorites"]
        print(f"  Threshold {row['threshold']}x: "
              f"UDs {u['n']} bets {u.get('hit_rate')}% hit {u.get('roi')}% ROI | "
              f"Favs {fav['n']} bets {fav.get('hit_rate')}% hit {fav.get('roi')}% ROI")

    result = {
        "grid_search":       grid,
        "optimal":           best,
        "division_analysis": divs,
        "underdog_vs_fav":   uvf,
    }

    if output_json:
        output_json.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nSaved: {output_json}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="UFC betting threshold optimizer.")
    parser.add_argument("--edge-range", nargs=2, type=float, default=[3.0, 15.0],
                        metavar=("MIN", "MAX"),
                        help="Edge range to sweep (default: 3 15)")
    parser.add_argument("--stake", type=float, default=0.5,
                        help="Flat stake %% of bankroll (default: 0.5)")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    run(
        edge_range=(args.edge_range[0], args.edge_range[1]),
        stake_pct=args.stake / 100,
        output_json=args.output,
    )


if __name__ == "__main__":
    main()
