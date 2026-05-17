"""
calibration.py — Model calibration, CLV tracking, and performance metrics.

Stores prediction records in the ledger SQLite DB and computes:
  - Brier score (mean squared error — lower is better)
  - Log loss (cross-entropy — lower is better)
  - Closing line value (CLV — positive means beating the market)
  - Calibration bucket performance (observed win rate vs predicted)
  - Edge distribution by tier
  - ROI summary

A well-calibrated model:
  - Brier score ~0.21 (random baseline for 50/50 is 0.25)
  - Log loss ~0.68 (random baseline)
  - CLV > 0 consistently
  - Predictions at 60% win ~60% of the time
"""
from __future__ import annotations

import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from utils import DATA_PROC

BETTING_DIR = DATA_PROC / "betting"
DB_PATH = BETTING_DIR / "ledger.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_tables() -> None:
    """Create calibration table if it doesn't exist (idempotent)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS calibration_predictions (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                bet_id                      TEXT    NOT NULL,
                event_id                    TEXT    NOT NULL,
                fight                       TEXT    NOT NULL,
                market                      TEXT    NOT NULL,
                selection                   TEXT    NOT NULL,
                model_probability           REAL    NOT NULL,
                market_probability          REAL,
                opening_market_probability  REAL,
                closing_market_probability  REAL,
                edge_pct                    REAL,
                confidence                  TEXT,
                outcome                     INTEGER,
                outcome_recorded_at         TEXT,
                clv                         REAL,
                created_at                  TEXT    NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_cal_bet_id
                ON calibration_predictions(bet_id);
            CREATE INDEX IF NOT EXISTS idx_cal_event_id
                ON calibration_predictions(event_id);
            CREATE INDEX IF NOT EXISTS idx_cal_outcome
                ON calibration_predictions(outcome);
        """)


def log_prediction(
    bet_id: str,
    event_id: str,
    fight: str,
    market: str,
    selection: str,
    model_probability: float,
    market_probability: float | None,
    edge_pct: float | None,
    confidence: str | None,
) -> None:
    """
    Store a prediction before the fight fires, for later calibration evaluation.
    INSERT OR IGNORE — safe to call multiple times (idempotent on bet_id).
    """
    ensure_tables()
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO calibration_predictions
                (bet_id, event_id, fight, market, selection, model_probability,
                 market_probability, opening_market_probability,
                 edge_pct, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bet_id, event_id, fight, market, selection,
                model_probability, market_probability, market_probability,
                edge_pct, confidence,
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )


def record_outcome(
    bet_id: str,
    outcome: int,
    closing_market_prob: float | None = None,
) -> None:
    """
    Record fight outcome (1=win, 0=loss) and optionally closing market probability.
    Called automatically by the settlement pipeline.
    CLV = model_probability - closing_market_probability (positive = beating the close).
    """
    ensure_tables()
    with get_db() as conn:
        row = conn.execute(
            "SELECT model_probability FROM calibration_predictions WHERE bet_id = ?",
            (bet_id,),
        ).fetchone()

        clv = None
        if closing_market_prob is not None and row:
            clv = round(float(row["model_probability"]) - closing_market_prob, 4)

        conn.execute(
            """
            UPDATE calibration_predictions
            SET outcome = ?,
                closing_market_probability = ?,
                clv = ?,
                outcome_recorded_at = ?
            WHERE bet_id = ?
            """,
            (
                outcome,
                closing_market_prob,
                clv,
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                bet_id,
            ),
        )


def _all_predictions() -> list[dict]:
    """Return every row from calibration_predictions."""
    ensure_tables()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM calibration_predictions ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def _settled(predictions: list[dict]) -> list[dict]:
    return [p for p in predictions if p.get("outcome") is not None]


# ── Calibration metrics ───────────────────────────────────────────────────────

def brier_score(predictions: list[dict]) -> float | None:
    """
    Mean squared error between predicted probability and binary outcome.
    Range 0-1. Lower is better. Random baseline for 50/50 events: 0.25.
    A model at 0.21 is meaningfully better than random.
    """
    settled = _settled(predictions)
    if not settled:
        return None
    total = sum(
        (p["model_probability"] - p["outcome"]) ** 2 for p in settled
    )
    return round(total / len(settled), 4)


def log_loss(predictions: list[dict]) -> float | None:
    """
    Cross-entropy loss. Lower is better. Random baseline ~0.693.
    Penalises confident wrong predictions heavily.
    """
    settled = _settled(predictions)
    if not settled:
        return None
    eps = 1e-15
    total = 0.0
    for p in settled:
        prob = max(eps, min(1.0 - eps, float(p["model_probability"])))
        total += (
            -math.log(prob) if p["outcome"] == 1
            else -math.log(1.0 - prob)
        )
    return round(total / len(settled), 4)


def calibration_buckets(
    predictions: list[dict],
    bucket_width: float = 0.05,
) -> list[dict]:
    """
    Group predictions into probability buckets and compare predicted vs observed win rate.

    A perfectly calibrated model: observed_rate ≈ bucket_midpoint.
    Overconfident model: observed_rate < bucket_midpoint consistently.
    Underconfident model: observed_rate > bucket_midpoint consistently.
    """
    settled = _settled(predictions)
    buckets: dict[float, list[int]] = {}
    for p in settled:
        lo = math.floor(float(p["model_probability"]) / bucket_width) * bucket_width
        midpoint = round(lo + bucket_width / 2, 3)
        buckets.setdefault(midpoint, []).append(int(p["outcome"]))

    result = []
    for midpoint in sorted(buckets):
        outcomes = buckets[midpoint]
        n = len(outcomes)
        observed = sum(outcomes) / n
        result.append({
            "bucket_midpoint": midpoint,
            "predicted_rate": midpoint,
            "observed_rate": round(observed, 4),
            "count": n,
            "calibration_error": round(abs(midpoint - observed), 4),
        })
    return result


def mean_calibration_error(predictions: list[dict]) -> float | None:
    """Weighted mean calibration error across all buckets."""
    buckets = calibration_buckets(predictions)
    if not buckets:
        return None
    total_n = sum(b["count"] for b in buckets)
    weighted = sum(b["calibration_error"] * b["count"] for b in buckets)
    return round(weighted / total_n, 4) if total_n else None


def clv_report(predictions: list[dict]) -> dict:
    """
    Closing Line Value analysis.

    Positive average CLV means the model consistently beats the closing market,
    which is a strong signal of genuine edge (market-validated).
    """
    with_clv = [p for p in predictions if p.get("clv") is not None]
    if not with_clv:
        return {"count": 0, "avg_clv": None, "clv_positive_pct": None, "interpretation": "No CLV data yet"}

    avg = sum(float(p["clv"]) for p in with_clv) / len(with_clv)
    positive = sum(1 for p in with_clv if float(p["clv"]) > 0)
    pct = round(positive / len(with_clv) * 100, 1)

    interpretation = (
        "Strong edge signal — consistently beating the closing line."
        if pct >= 55 and avg > 0.01
        else "Neutral — not clearly beating the market yet."
        if pct >= 45
        else "Warning — model is behind the closing line. Review calibration."
    )
    return {
        "count": len(with_clv),
        "avg_clv": round(avg, 4),
        "clv_positive_pct": pct,
        "interpretation": interpretation,
    }


def edge_distribution(predictions: list[dict]) -> list[dict]:
    """Hit rate and count breakdown by edge tier."""
    tiers = [
        (4.0,  6.0,  "4–6%"),
        (6.0,  10.0, "6–10%"),
        (10.0, 15.0, "10–15%"),
        (15.0, 100.0, "15%+"),
    ]
    result = []
    for lo, hi, label in tiers:
        tier = [
            p for p in predictions
            if p.get("edge_pct") is not None
            and lo <= float(p["edge_pct"]) < hi
        ]
        settled = _settled(tier)
        wins = sum(int(p["outcome"]) for p in settled)
        result.append({
            "label": label,
            "count": len(tier),
            "settled": len(settled),
            "wins": wins,
            "losses": len(settled) - wins,
            "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
        })
    return result


def confidence_tier_performance(predictions: list[dict]) -> list[dict]:
    """Win rate broken down by confidence tier."""
    tiers = ["High", "Medium", "Low-Medium", "Low"]
    result = []
    for tier in tiers:
        tier_preds = [p for p in predictions if p.get("confidence") == tier]
        settled = _settled(tier_preds)
        wins = sum(int(p["outcome"]) for p in settled)
        result.append({
            "confidence": tier,
            "count": len(tier_preds),
            "settled": len(settled),
            "wins": wins,
            "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
        })
    return result


def roi_summary(predictions: list[dict]) -> dict:
    """Basic win/loss count and hit rate from calibration records."""
    settled = _settled(predictions)
    wins = sum(int(p["outcome"]) for p in settled)
    losses = len(settled) - wins
    return {
        "total_predictions": len(predictions),
        "settled": len(settled),
        "wins": wins,
        "losses": losses,
        "hit_rate": round(wins / len(settled) * 100, 1) if settled else None,
        "pending": len(predictions) - len(settled),
    }


# ── Dashboard aggregate ───────────────────────────────────────────────────────

def model_health_data() -> dict:
    """
    Aggregate all calibration metrics for the /model-health dashboard.
    Pulls directly from the calibration_predictions table.
    """
    all_preds = _all_predictions()
    settled = _settled(all_preds)

    return {
        "total_predictions": len(all_preds),
        "settled_predictions": len(settled),
        "pending_predictions": len(all_preds) - len(settled),
        "brier_score": brier_score(settled),
        "brier_baseline": 0.25,  # random coin-flip baseline
        "log_loss": log_loss(settled),
        "log_loss_baseline": 0.693,
        "mean_calibration_error": mean_calibration_error(settled),
        "calibration_buckets": calibration_buckets(settled),
        "clv": clv_report(settled),
        "edge_distribution": edge_distribution(all_preds),
        "confidence_tier_performance": confidence_tier_performance(settled),
        "roi_summary": roi_summary(all_preds),
        "model_notes": [
            "Probabilities capped at 25-75% (no extreme predictions).",
            "Market shrinkage applied: 60% model + 40% market implied.",
            "Staking: 0.5% flat (1% max for High confidence + edge >= 8%).",
            "Accumulators disabled until 500+ bets tracked with positive CLV.",
            "Moneyline only — props and totals disabled.",
        ],
    }
