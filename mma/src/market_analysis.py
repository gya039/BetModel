"""
market_analysis.py — Model vs market residual analysis.

Answers:
  - Where does the model consistently disagree with the betting market?
  - Are those disagreements historically profitable?
  - Which divisions / fight types have the biggest gaps?
  - Does beating the opening line (CLV) correlate with actual profitability?

Data source: the ledger DB (calibration_predictions + bets tables).

Usage:
    python market_analysis.py
    python market_analysis.py --min-bets 20 --output analysis.json
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from utils import DATA_PROC

DB_PATH = DATA_PROC / "betting" / "ledger.db"


@contextmanager
def get_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Ledger DB not found: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Data loading ──────────────────────────────────────────────────────────────

def load_settled_bets() -> list[dict]:
    """
    Load all settled single bets with their model probabilities, market odds, and outcomes.
    Joins bets + calibration_predictions where available.
    """
    with get_db() as conn:
        # Check if calibration table exists
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        if "calibration_predictions" in tables:
            rows = conn.execute("""
                SELECT
                    b.bet_id,
                    b.fight,
                    b.market,
                    b.selection,
                    b.odds            AS american_odds,
                    b.decimal_odds,
                    b.stake_eur,
                    b.edge            AS recorded_edge,
                    b.confidence,
                    b.status,
                    b.pnl,
                    b.bet_type,
                    e.event_date,
                    e.event_name,
                    cp.model_probability,
                    cp.market_probability,
                    cp.closing_market_probability,
                    cp.clv,
                    cp.outcome
                FROM bets b
                JOIN events e ON b.event_id = e.event_id
                LEFT JOIN calibration_predictions cp ON b.bet_id = cp.bet_id
                WHERE b.status IN ('won', 'lost', 'push')
                  AND b.bet_type = 'single'
                ORDER BY e.event_date
            """).fetchall()
        else:
            rows = conn.execute("""
                SELECT
                    b.bet_id,
                    b.fight,
                    b.market,
                    b.selection,
                    b.odds            AS american_odds,
                    b.decimal_odds,
                    b.stake_eur,
                    b.edge            AS recorded_edge,
                    b.confidence,
                    b.status,
                    b.pnl,
                    b.bet_type,
                    e.event_date,
                    e.event_name,
                    NULL AS model_probability,
                    NULL AS market_probability,
                    NULL AS closing_market_probability,
                    NULL AS clv,
                    NULL AS outcome
                FROM bets b
                JOIN events e ON b.event_id = e.event_id
                WHERE b.status IN ('won', 'lost', 'push')
                  AND b.bet_type = 'single'
                ORDER BY e.event_date
            """).fetchall()

    return [dict(r) for r in rows]


# ── Residual analysis ─────────────────────────────────────────────────────────

def _safe(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def residual_analysis(bets: list[dict]) -> dict:
    """
    Compute model-vs-market residuals for all settled bets.
    Residual = model_probability - market_implied_probability.
    Positive = model sees more value than market.
    """
    with_probs = [
        b for b in bets
        if b.get("model_probability") is not None
        and b.get("market_probability") is not None
    ]

    if not with_probs:
        return {"error": "No calibration probability data available yet. Run some events first."}

    residuals = [
        _safe(b["model_probability"]) - _safe(b["market_probability"])
        for b in with_probs
    ]
    avg_res = sum(residuals) / len(residuals)
    positive = sum(1 for r in residuals if r > 0)

    # Segment by residual size
    buckets = [
        ("<-10%",  [b for b, r in zip(with_probs, residuals) if r < -0.10]),
        ("-10 to -5%", [b for b, r in zip(with_probs, residuals) if -0.10 <= r < -0.05]),
        ("-5 to 0%",  [b for b, r in zip(with_probs, residuals) if -0.05 <= r < 0]),
        ("0 to +5%",  [b for b, r in zip(with_probs, residuals) if 0 <= r < 0.05]),
        ("+5 to +10%",[b for b, r in zip(with_probs, residuals) if 0.05 <= r < 0.10]),
        (">+10%",     [b for b, r in zip(with_probs, residuals) if r >= 0.10]),
    ]

    bucket_stats = []
    for label, group in buckets:
        settled = [b for b in group if b.get("status") in ("won", "lost")]
        wins = sum(1 for b in settled if b["status"] == "won")
        total_pnl = sum(_safe(b.get("pnl")) for b in settled)
        bucket_stats.append({
            "residual_bucket": label,
            "count":           len(group),
            "settled":         len(settled),
            "wins":            wins,
            "hit_rate":        round(wins / len(settled) * 100, 1) if settled else None,
            "total_pnl":       round(total_pnl, 2),
        })

    return {
        "total_with_probs": len(with_probs),
        "avg_residual":     round(avg_res, 4),
        "pct_model_higher": round(positive / len(with_probs) * 100, 1),
        "buckets":          bucket_stats,
    }


def profitable_disagreements(bets: list[dict], min_residual: float = 0.05) -> dict:
    """
    Identify patterns where model consistently disagrees with market AND is profitable.
    """
    with_probs = [
        b for b in bets
        if b.get("model_probability") is not None
        and b.get("market_probability") is not None
    ]

    big_disagreements = [
        b for b in with_probs
        if abs(_safe(b["model_probability"]) - _safe(b["market_probability"])) >= min_residual
    ]
    settled = [b for b in big_disagreements if b.get("status") in ("won", "lost")]
    wins = sum(1 for b in settled if b["status"] == "won")
    total_pnl = sum(_safe(b.get("pnl")) for b in settled)
    total_staked = sum(_safe(b.get("stake_eur")) for b in settled)

    # Model higher than market AND won
    model_higher = [
        b for b in settled
        if _safe(b["model_probability"]) > _safe(b["market_probability"])
    ]
    model_higher_wins = sum(1 for b in model_higher if b["status"] == "won")
    model_higher_pnl  = sum(_safe(b.get("pnl")) for b in model_higher)

    # Model lower than market AND won
    model_lower = [
        b for b in settled
        if _safe(b["model_probability"]) < _safe(b["market_probability"])
    ]
    model_lower_wins = sum(1 for b in model_lower if b["status"] == "won")
    model_lower_pnl  = sum(_safe(b.get("pnl")) for b in model_lower)

    return {
        "min_residual_threshold": min_residual,
        "total_big_disagreements": len(big_disagreements),
        "settled": len(settled),
        "wins": wins,
        "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
        "total_pnl": round(total_pnl, 2),
        "roi": round(total_pnl / total_staked * 100, 1) if total_staked else None,
        "model_higher_than_market": {
            "count": len(model_higher),
            "wins": model_higher_wins,
            "hit_rate": round(model_higher_wins / len(model_higher) * 100, 1) if model_higher else None,
            "pnl": round(model_higher_pnl, 2),
            "interpretation": (
                "Model consistently rates fighter higher than market. "
                "If profitable, consider slightly increasing stake on these picks."
                if model_higher_pnl > 0
                else "Model is wrong when it sees more value than market. Review calibration."
            ),
        },
        "model_lower_than_market": {
            "count": len(model_lower),
            "wins": model_lower_wins,
            "hit_rate": round(model_lower_wins / len(model_lower) * 100, 1) if model_lower else None,
            "pnl": round(model_lower_pnl, 2),
            "interpretation": (
                "Model rates fighter lower than market. If bets still placed here "
                "(via other mechanisms), they're underperforming expectation."
            ),
        },
    }


def clv_vs_profitability(bets: list[dict]) -> dict:
    """
    Correlate CLV (closing line value) with realized profit.
    High CLV should predict better long-run results.
    """
    with_clv = [b for b in bets if b.get("clv") is not None]
    if not with_clv:
        return {"error": "No CLV data. Needs closing odds recorded at settlement."}

    settled = [b for b in with_clv if b.get("status") in ("won", "lost")]
    pos_clv = [b for b in settled if _safe(b["clv"]) > 0]
    neg_clv = [b for b in settled if _safe(b["clv"]) <= 0]

    def summary(group):
        if not group:
            return {"count": 0, "pnl": 0, "hit_rate": None}
        wins = sum(1 for b in group if b["status"] == "won")
        return {
            "count": len(group),
            "wins": wins,
            "hit_rate": round(wins / len(group) * 100, 1),
            "total_pnl": round(sum(_safe(b.get("pnl")) for b in group), 2),
        }

    return {
        "total_with_clv": len(with_clv),
        "positive_clv":   summary(pos_clv),
        "negative_clv":   summary(neg_clv),
        "interpretation": (
            "Positive CLV bets outperform negative CLV bets significantly."
            if pos_clv and neg_clv
            and sum(_safe(b.get("pnl")) for b in pos_clv) > sum(_safe(b.get("pnl")) for b in neg_clv)
            else "Insufficient data to draw conclusions yet."
        ),
    }


def division_performance(bets: list[dict]) -> list[dict]:
    """P&L and hit rate broken down by weight class."""
    div_map: dict[str, list] = {}
    for b in bets:
        fight = b.get("fight", "")
        # Division inference is limited here — use event context when available
        div = "Unknown"
        div_map.setdefault(div, []).append(b)

    result = []
    for div, group in div_map.items():
        settled = [b for b in group if b.get("status") in ("won", "lost")]
        wins = sum(1 for b in settled if b["status"] == "won")
        total_pnl = sum(_safe(b.get("pnl")) for b in settled)
        result.append({
            "division": div,
            "bets": len(group),
            "settled": len(settled),
            "wins": wins,
            "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
            "total_pnl": round(total_pnl, 2),
        })
    result.sort(key=lambda r: r.get("total_pnl", 0), reverse=True)
    return result


def underdog_vs_favorite(bets: list[dict]) -> dict:
    """Compare performance on underdogs (odds > 2.0) vs favorites (odds < 2.0)."""
    def classify(b):
        dec = _safe(b.get("decimal_odds"), 2.0)
        return "underdog" if dec > 2.0 else "favorite"

    groups = {"underdog": [], "favorite": []}
    for b in bets:
        groups[classify(b)].append(b)

    result = {}
    for label, group in groups.items():
        settled = [b for b in group if b.get("status") in ("won", "lost")]
        wins = sum(1 for b in settled if b["status"] == "won")
        staked = sum(_safe(b.get("stake_eur")) for b in settled)
        pnl = sum(_safe(b.get("pnl")) for b in settled)
        result[label] = {
            "count": len(group),
            "settled": len(settled),
            "wins": wins,
            "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
            "total_pnl": round(pnl, 2),
            "roi": round(pnl / staked * 100, 1) if staked else None,
        }

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def run(min_bets: int = 5, output_json: Path | None = None) -> dict:
    try:
        bets = load_settled_bets()
    except FileNotFoundError as e:
        print(f"[ERROR] {e}")
        return {}

    if len(bets) < min_bets:
        print(f"Only {len(bets)} settled bets found. Need at least {min_bets}.")
        return {"error": f"Insufficient data: {len(bets)} bets < {min_bets} minimum"}

    print(f"\nAnalysing {len(bets)} settled bets ...\n")

    analysis = {
        "total_bets":              len(bets),
        "residual_analysis":       residual_analysis(bets),
        "profitable_disagreements": profitable_disagreements(bets, min_residual=0.05),
        "clv_vs_profitability":    clv_vs_profitability(bets),
        "underdog_vs_favorite":    underdog_vs_favorite(bets),
        "division_performance":    division_performance(bets),
    }

    # Print summary
    res = analysis["residual_analysis"]
    if "error" not in res:
        print(f"Model vs market:")
        print(f"  Average residual : {res['avg_residual']:.4f}")
        print(f"  Model higher %   : {res['pct_model_higher']}%")

    pd = analysis["profitable_disagreements"]
    if "error" not in pd:
        print(f"\nBig disagreements (>5% residual): {pd['total_big_disagreements']}")
        print(f"  Hit rate: {pd.get('hit_rate')}%  |  ROI: {pd.get('roi')}%")

    uvf = analysis["underdog_vs_favorite"]
    for label, info in uvf.items():
        print(f"\n{label.title()} ({info['count']} bets):")
        print(f"  Hit rate: {info.get('hit_rate')}%  |  ROI: {info.get('roi')}%")

    if output_json:
        output_json.write_text(json.dumps(analysis, indent=2, default=str))
        print(f"\nSaved: {output_json}")

    return analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="UFC model vs market residual analysis.")
    parser.add_argument("--min-bets", type=int, default=5,
                        help="Minimum settled bets required (default: 5)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Save JSON output to this path")
    args = parser.parse_args()
    run(min_bets=args.min_bets, output_json=args.output)


if __name__ == "__main__":
    main()
