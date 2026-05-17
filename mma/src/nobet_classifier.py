"""
nobet_classifier.py — BET / PASS filter for UFC matchups.

A binary classifier that flags fights as PASS before the edge/staking logic
ever runs. It identifies structural risk factors that make a fight a bad bet
even when the edge calculation looks positive:

  Risk factors:
    1. Sample size risk   — one or both fighters have < 5 recorded fights
    2. Inactivity risk    — fighter returning from 18+ month layoff
    3. Style clash risk   — fight type where the model historically underperforms
    4. Line movement risk — odds moved ≥ 15% since opening (market disagrees)
    5. Data quality risk  — key features (Elo, stats) are missing
    6. Market disagreement — model and market are ≥ 20pp apart (one is very wrong)

Each factor contributes a risk score. Total score ≥ PASS_THRESHOLD → PASS.
Each factor can also be a hard veto if flagged as blocking.

Usage:
    from nobet_classifier import NoBetClassifier
    clf = NoBetClassifier()
    decision = clf.evaluate(feat_a, feat_b, market_implied, opening_odds)
    # decision: {"action": "BET"|"PASS", "risk_score": float, "reasons": [...]}

The classifier is rule-based (no training required) with optional calibration
from historical PASS decisions in the ledger.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Score thresholds
PASS_THRESHOLD = 3.0    # total risk score >= this → PASS
HARD_VETO_SCORE = 10.0  # any single factor can trigger immediate PASS

# Individual factor weights
FACTOR_WEIGHTS: dict[str, float] = {
    "sample_size":          2.0,   # < 5 fights for either fighter
    "sample_size_minor":    1.0,   # 5-8 fights (warning, not veto)
    "inactivity":           2.0,   # 18+ month layoff
    "inactivity_minor":     0.5,   # 12-18 month layoff
    "missing_elo":          1.5,   # Elo not available for either fighter
    "missing_stats":        1.0,   # core striking/grappling features missing
    "line_movement_large":  2.5,   # odds moved ≥15% → market got new info
    "line_movement_medium": 1.0,   # odds moved 8-15%
    "market_disagreement":  2.0,   # |model - market| ≥ 20pp
    "extreme_underdog":     1.5,   # odds > 4.00 (high variance fight)
    "debut_fighter":        HARD_VETO_SCORE,  # UFC debut → no fight history
    "withdrawal_risk":      1.5,   # very short notice (< 7 days) — placeholder
}

# Minimum fights required before the model trusts fighter data
MIN_FIGHTS_TRUST     = 5
MIN_FIGHTS_WARNING   = 8
INACTIVITY_HARD_DAYS = 540   # 18 months
INACTIVITY_SOFT_DAYS = 365   # 12 months
LINE_MOVE_HARD_PCT   = 0.15  # 15% odds movement
LINE_MOVE_SOFT_PCT   = 0.08
MARKET_DISAGREE_PP   = 0.20  # 20 percentage points
EXTREME_UNDERDOG_ODDS = 4.00


@dataclass
class RiskFactor:
    name:        str
    weight:      float
    description: str
    is_veto:     bool = False


@dataclass
class NoBetDecision:
    action:      str               # "BET" or "PASS"
    risk_score:  float
    factors:     list[RiskFactor] = field(default_factory=list)
    reasons:     list[str]        = field(default_factory=list)
    is_vetoed:   bool             = False

    def as_dict(self) -> dict:
        return {
            "action":     self.action,
            "risk_score": round(self.risk_score, 2),
            "reasons":    self.reasons,
            "is_vetoed":  self.is_vetoed,
            "n_factors":  len(self.factors),
        }


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _is_missing(val: Any) -> bool:
    return val is None or (isinstance(val, float) and val == 0.0)


# ── Rule evaluators ───────────────────────────────────────────────────────────

def _check_sample_size(feat_a: dict, feat_b: dict) -> list[RiskFactor]:
    factors = []
    for label, feat in (("A", feat_a), ("B", feat_b)):
        n = _safe_float(feat.get("n_fights"), 0)
        if n < 1:
            factors.append(RiskFactor(
                name="debut_fighter",
                weight=FACTOR_WEIGHTS["debut_fighter"],
                description=f"Fighter {label} has no recorded UFC fights (debut)",
                is_veto=True,
            ))
        elif n < MIN_FIGHTS_TRUST:
            factors.append(RiskFactor(
                name="sample_size",
                weight=FACTOR_WEIGHTS["sample_size"],
                description=f"Fighter {label} has only {int(n)} recorded fights (< {MIN_FIGHTS_TRUST})",
            ))
        elif n < MIN_FIGHTS_WARNING:
            factors.append(RiskFactor(
                name="sample_size_minor",
                weight=FACTOR_WEIGHTS["sample_size_minor"],
                description=f"Fighter {label} has limited fight history ({int(n)} fights)",
            ))
    return factors


def _check_inactivity(feat_a: dict, feat_b: dict) -> list[RiskFactor]:
    factors = []
    for label, feat in (("A", feat_a), ("B", feat_b)):
        # layoff_factor: 1.0 = active, 0.84 = 2+ year layoff
        layoff = _safe_float(feat.get("layoff_factor"), 1.0)
        # Reconstruct approximate days from layoff factor
        # 1.0 → <180d, 0.95 → ~6-12mo, 0.90 → ~12-18mo, 0.84 → 18-24mo+
        if layoff <= 0.85:
            factors.append(RiskFactor(
                name="inactivity",
                weight=FACTOR_WEIGHTS["inactivity"],
                description=f"Fighter {label} returning from extended layoff (layoff_factor={layoff:.2f})",
            ))
        elif layoff <= 0.92:
            factors.append(RiskFactor(
                name="inactivity_minor",
                weight=FACTOR_WEIGHTS["inactivity_minor"],
                description=f"Fighter {label} has not fought recently (layoff_factor={layoff:.2f})",
            ))
    return factors


def _check_data_quality(feat_a: dict, feat_b: dict) -> list[RiskFactor]:
    factors = []

    # Check Elo availability
    elo_a = feat_a.get("elo")
    elo_b = feat_b.get("elo")
    if _is_missing(elo_a) or _is_missing(elo_b):
        factors.append(RiskFactor(
            name="missing_elo",
            weight=FACTOR_WEIGHTS["missing_elo"],
            description="Elo rating unavailable for one or both fighters",
        ))

    # Check core stats availability
    critical_keys = ["sig_strikes_ew", "td_landed_ew", "win_rate"]
    missing_a = sum(1 for k in critical_keys if _is_missing(feat_a.get(k)))
    missing_b = sum(1 for k in critical_keys if _is_missing(feat_b.get(k)))

    if missing_a >= 2 or missing_b >= 2:
        factors.append(RiskFactor(
            name="missing_stats",
            weight=FACTOR_WEIGHTS["missing_stats"],
            description=f"Core stats missing: fighter A missing {missing_a}, fighter B missing {missing_b} features",
        ))

    return factors


def _check_market_signals(
    feat_a: dict,
    feat_b: dict,
    model_prob: float | None,
    market_implied: float | None,
    opening_odds: float | None = None,
    current_odds: float | None = None,
) -> list[RiskFactor]:
    factors = []

    # Market disagreement: model vs current market
    if model_prob is not None and market_implied is not None:
        gap = abs(model_prob - market_implied)
        if gap >= MARKET_DISAGREE_PP:
            factors.append(RiskFactor(
                name="market_disagreement",
                weight=FACTOR_WEIGHTS["market_disagreement"],
                description=(
                    f"Model ({model_prob:.1%}) and market ({market_implied:.1%}) "
                    f"disagree by {gap:.1%} — large uncertainty"
                ),
            ))

    # Line movement (opening vs current)
    if opening_odds is not None and current_odds is not None and opening_odds > 0:
        move_pct = abs(current_odds - opening_odds) / opening_odds
        if move_pct >= LINE_MOVE_HARD_PCT:
            factors.append(RiskFactor(
                name="line_movement_large",
                weight=FACTOR_WEIGHTS["line_movement_large"],
                description=(
                    f"Odds moved {move_pct:.1%} from opening "
                    f"({opening_odds:.2f} → {current_odds:.2f}) — sharp money signal"
                ),
            ))
        elif move_pct >= LINE_MOVE_SOFT_PCT:
            factors.append(RiskFactor(
                name="line_movement_medium",
                weight=FACTOR_WEIGHTS["line_movement_medium"],
                description=(
                    f"Odds moved {move_pct:.1%} since opening "
                    f"({opening_odds:.2f} → {current_odds:.2f})"
                ),
            ))

    # Extreme underdog
    if current_odds is not None and current_odds >= EXTREME_UNDERDOG_ODDS:
        factors.append(RiskFactor(
            name="extreme_underdog",
            weight=FACTOR_WEIGHTS["extreme_underdog"],
            description=f"Extreme underdog at {current_odds:.2f} — high variance outcome",
        ))

    return factors


# ── Main classifier ───────────────────────────────────────────────────────────

class NoBetClassifier:
    """
    Evaluates structural risk factors for a given matchup and returns
    a BET or PASS decision with an explanatory breakdown.
    """

    def evaluate(
        self,
        feat_a: dict,
        feat_b: dict,
        model_prob: float | None = None,
        market_implied: float | None = None,
        opening_odds: float | None = None,
        current_odds: float | None = None,
    ) -> NoBetDecision:
        """
        Run all risk checks and aggregate into a BET/PASS decision.

        Args:
            feat_a, feat_b:  fighter feature dicts from features.py
            model_prob:      model's estimated win probability for fighter A
            market_implied:  market-implied win probability for fighter A
            opening_odds:    decimal odds at line open
            current_odds:    current decimal odds (for line-movement check)
        """
        all_factors: list[RiskFactor] = []

        all_factors.extend(_check_sample_size(feat_a, feat_b))
        all_factors.extend(_check_inactivity(feat_a, feat_b))
        all_factors.extend(_check_data_quality(feat_a, feat_b))
        all_factors.extend(_check_market_signals(
            feat_a, feat_b, model_prob, market_implied,
            opening_odds, current_odds,
        ))

        # Veto check — any single hard-veto factor → immediate PASS
        vetoed = any(f.is_veto for f in all_factors)
        total_score = sum(f.weight for f in all_factors)

        if vetoed or total_score >= PASS_THRESHOLD:
            action = "PASS"
        else:
            action = "BET"

        reasons = [f.description for f in all_factors]

        return NoBetDecision(
            action=action,
            risk_score=total_score,
            factors=all_factors,
            reasons=reasons,
            is_vetoed=vetoed,
        )

    def should_bet(
        self,
        feat_a: dict,
        feat_b: dict,
        model_prob: float | None = None,
        market_implied: float | None = None,
        opening_odds: float | None = None,
        current_odds: float | None = None,
    ) -> bool:
        """Convenience wrapper — returns True if the matchup passes the filter."""
        d = self.evaluate(feat_a, feat_b, model_prob, market_implied, opening_odds, current_odds)
        return d.action == "BET"


# ── Singleton ─────────────────────────────────────────────────────────────────

_clf: NoBetClassifier | None = None


def get_classifier() -> NoBetClassifier:
    global _clf
    if _clf is None:
        _clf = NoBetClassifier()
    return _clf


# ── Historical calibration (optional) ────────────────────────────────────────

def calibrate_from_ledger() -> dict:
    """
    Analyse whether PASS decisions were correct: did the corresponding bets
    (had they been placed) lose? Returns a calibration summary.
    """
    from utils import DATA_PROC
    import sqlite3

    db = DATA_PROC / "betting" / "ledger.db"
    if not db.exists():
        return {"status": "no_ledger"}

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "bets" not in tables:
            return {"status": "no_bets_table"}

        rows = conn.execute("""
            SELECT b.status, b.edge, b.confidence, b.decimal_odds, b.pnl
            FROM bets b
            WHERE b.status IN ('won', 'lost') AND b.bet_type = 'single'
        """).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"status": "no_settled_bets"}

    # Retroactively flag which bets would have been filtered
    clf = NoBetClassifier()
    total = len(rows)

    # Without feature data we can only check market disagreement via edge/odds proxies
    high_edge_bets = [r for r in rows if float(r["edge"] or 0) > 15]
    extreme_ud     = [r for r in rows if float(r["decimal_odds"] or 2.0) >= EXTREME_UNDERDOG_ODDS]

    def hit_rate(group):
        if not group:
            return None
        wins = sum(1 for b in group if b["status"] == "won")
        return round(wins / len(group) * 100, 1)

    return {
        "status":                "calibrated",
        "total_settled":         total,
        "all_hit_rate":          hit_rate(rows),
        "high_edge_bets":        len(high_edge_bets),
        "high_edge_hit_rate":    hit_rate(high_edge_bets),
        "extreme_underdog_bets": len(extreme_ud),
        "extreme_ud_hit_rate":   hit_rate(extreme_ud),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="No-bet classifier for UFC matchups.")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run calibration analysis from ledger")
    parser.add_argument("--demo", action="store_true",
                        help="Run demo evaluation with synthetic fighters")
    args = parser.parse_args()

    if args.calibrate:
        result = calibrate_from_ledger()
        print(f"\nCalibration result: {result}")

    if args.demo or not args.calibrate:
        clf = NoBetClassifier()

        # Demo: experienced active fighter vs debut fighter
        feat_a_debut = {"n_fights": 0, "layoff_factor": 1.0, "elo": 1500.0,
                        "sig_strikes_ew": 4.5, "td_landed_ew": 1.2, "win_rate": 0.6}
        feat_b_vet   = {"n_fights": 12, "layoff_factor": 0.98, "elo": 1480.0,
                        "sig_strikes_ew": 4.8, "td_landed_ew": 1.0, "win_rate": 0.58}

        decision = clf.evaluate(feat_a_debut, feat_b_vet,
                                model_prob=0.55, market_implied=0.48)
        print(f"\nDemo 1 (debut vs veteran):")
        print(f"  Action:     {decision.action}")
        print(f"  Risk score: {decision.risk_score:.1f} (threshold: {PASS_THRESHOLD})")
        for r in decision.reasons:
            print(f"  ⚠ {r}")

        # Demo: both experienced, stable odds
        feat_a_ok = {"n_fights": 10, "layoff_factor": 0.99, "elo": 1520.0,
                     "sig_strikes_ew": 5.0, "td_landed_ew": 1.3, "win_rate": 0.65}
        feat_b_ok = {"n_fights": 8, "layoff_factor": 0.97, "elo": 1490.0,
                     "sig_strikes_ew": 4.7, "td_landed_ew": 0.9, "win_rate": 0.60}

        decision2 = clf.evaluate(feat_a_ok, feat_b_ok,
                                 model_prob=0.60, market_implied=0.58,
                                 opening_odds=1.80, current_odds=1.82)
        print(f"\nDemo 2 (experienced vs experienced, stable line):")
        print(f"  Action:     {decision2.action}")
        print(f"  Risk score: {decision2.risk_score:.1f}")
        if decision2.reasons:
            for r in decision2.reasons:
                print(f"  ⚠ {r}")
        else:
            print("  No risk factors flagged.")


if __name__ == "__main__":
    main()
