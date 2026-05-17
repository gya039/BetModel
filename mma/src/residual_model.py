"""
residual_model.py — Market residual regression for UFC predictions.

Instead of predicting fight outcomes directly, predicts:
    residual = model_probability - market_implied_probability

A large positive residual means the model thinks fighter A is underpriced;
a large negative residual means the market is significantly more bullish.

Key design choices:
  - Trained only on fights where closing market odds were recorded
  - Ridge regression (L2) to keep coefficients stable with limited data
  - Output is NOT used to override probabilities — it's a secondary signal
    that adjusts edge confidence and filters borderline bets
  - Positive residual + positive CLV historically = sharper edge

Usage from code:
    from residual_model import ResidualPredictor
    rp = ResidualPredictor()
    rp.train()                              # fit on calibration_predictions
    signal = rp.predict(feat_a, feat_b)    # float in roughly [-0.3, +0.3]
    trust = rp.trust_level(signal)          # "Strong" | "Moderate" | "Weak" | "Fade"

CLI:
    python residual_model.py               # train and print summary
    python residual_model.py --save        # persist model to models/residual_model.pkl
    python residual_model.py --eval        # run leave-one-out evaluation
"""
from __future__ import annotations

import json
import math
import pickle
from pathlib import Path
from typing import Any

from utils import DATA_PROC

MODEL_PATH   = DATA_PROC.parent / "models" / "residual_model.pkl"
META_PATH    = DATA_PROC.parent / "models" / "residual_model_meta.json"
DB_PATH      = DATA_PROC / "betting" / "ledger.db"

# Residual thresholds for trust classification
STRONG_THRESHOLD   = 0.08   # model thinks fighter is ≥8pp underpriced
MODERATE_THRESHOLD = 0.04
FADE_THRESHOLD     = -0.08  # model is ≥8pp below market → potential fade

# Ridge regularization strength (alpha = 1/C in sklearn terms)
RIDGE_ALPHA = 1.0


# ── Data loading ──────────────────────────────────────────────────────────────

def _load_residual_data() -> list[dict]:
    """
    Load fights where both model_probability and market_probability were recorded.
    Returns list of {feat_a, feat_b, model_prob, market_prob, outcome, residual}.
    """
    import sqlite3
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        if "calibration_predictions" not in tables:
            return []

        rows = conn.execute("""
            SELECT cp.bet_id, cp.model_probability, cp.market_probability,
                   cp.outcome, b.fight, b.selection, e.event_date
            FROM calibration_predictions cp
            JOIN bets b ON cp.bet_id = b.bet_id
            JOIN events e ON b.event_id = e.event_id
            WHERE cp.model_probability IS NOT NULL
              AND cp.market_probability IS NOT NULL
              AND cp.outcome IS NOT NULL
            ORDER BY e.event_date ASC
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _load_feature_rows() -> list[dict]:
    """Load training CSV rows for joining features to residual targets."""
    from model_trainer import TRAINING_CSV, load_csv
    if not TRAINING_CSV.exists():
        return []
    return load_csv(TRAINING_CSV)


# ── Feature extraction for residual model ────────────────────────────────────

def _residual_features(feat_a: dict, feat_b: dict) -> list[float]:
    """
    Compact feature vector for residual prediction.
    Focuses on market-disagreement-relevant features rather than outcome prediction.
    """
    def g(d, k, default=0.0):
        try:
            v = d.get(k)
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    # Elo gap (the main market-efficiency signal)
    elo_gap      = g(feat_a, "elo") - g(feat_b, "elo")

    # Experience asymmetry — public may over-/under-react to experience gaps
    fights_gap   = g(feat_a, "n_fights") - g(feat_b, "n_fights")

    # Form gap — recent momentum vs overall record
    form_gap     = g(feat_a, "form_score") - g(feat_b, "form_score")

    # Finish rate gap — markets may undervalue finishers
    finish_gap   = g(feat_a, "finish_rate") - g(feat_b, "finish_rate")

    # Layoff asymmetry — markets often misprice returning fighters
    layoff_diff  = g(feat_a, "layoff_factor") - g(feat_b, "layoff_factor")

    # Age gap — markets sometimes mis-weight age trajectory
    age_diff     = g(feat_a, "age") - g(feat_b, "age")

    # Striking differential
    slpm_gap     = g(feat_a, "sig_strikes_ew") - g(feat_b, "sig_strikes_ew")

    # Takedown differential
    td_gap       = g(feat_a, "td_landed_ew") - g(feat_b, "td_landed_ew")

    # Win rate gap
    wr_gap       = g(feat_a, "win_rate") - g(feat_b, "win_rate")

    # KO finish rate asymmetry — public loves knockout artists
    ko_gap       = g(feat_a, "ko_rate") - g(feat_b, "ko_rate")

    # Submission rate — grappling markets can be inefficient
    sub_gap      = g(feat_a, "sub_rate") - g(feat_b, "sub_rate")

    # Absolute Elo (top-level match vs bottom)
    avg_elo      = (g(feat_a, "elo") + g(feat_b, "elo")) / 2

    # Squared Elo gap (non-linear effect)
    elo_gap_sq   = elo_gap ** 2 / 10000.0

    return [
        elo_gap, elo_gap_sq, avg_elo / 1000.0,
        fights_gap, form_gap, finish_gap,
        layoff_diff, age_diff,
        slpm_gap, td_gap, wr_gap, ko_gap, sub_gap,
    ]

RESIDUAL_FEATURE_NAMES = [
    "elo_gap", "elo_gap_sq", "avg_elo_norm",
    "fights_gap", "form_gap", "finish_gap",
    "layoff_diff", "age_diff",
    "slpm_gap", "td_gap", "wr_gap", "ko_gap", "sub_gap",
]


def _build_training_matrix(
    residual_rows: list[dict],
    feature_rows: list[dict],
) -> tuple[list[list[float]], list[float]]:
    """
    Match calibration_predictions entries to feature rows by fight+date proximity,
    then build X (features) and y (residuals) arrays.

    Falls back to using model_prob - market_prob as the target directly when
    no feature rows are available to join (e.g., pre-training bootstrap).
    """
    if not residual_rows:
        return [], []

    # If no feature rows, use a single intercept feature
    if not feature_rows:
        X, y = [], []
        for r in residual_rows:
            mp  = float(r["model_probability"])
            mkt = float(r["market_probability"])
            residual = mp - mkt
            X.append([1.0])  # intercept only
            y.append(residual)
        return X, y

    # Index feature rows by fight signature
    feat_index: dict[str, dict] = {}
    for row in feature_rows:
        key = (row.get("fighter_a", ""), row.get("fighter_b", ""), row.get("event_date", ""))
        feat_index[key] = row

    X, y = [], []
    for r in residual_rows:
        mp  = float(r["model_probability"])
        mkt = float(r["market_probability"])
        residual = mp - mkt

        # Try to find matching feature row (approximate match by fight text)
        fight_text = r.get("fight", "")
        matched_row = None
        for key, frow in feat_index.items():
            fa, fb = key[0], key[1]
            if fa.lower() in fight_text.lower() or fb.lower() in fight_text.lower():
                matched_row = frow
                break

        if matched_row:
            feat_a = {k: matched_row.get(f"a_{k}") for k in [
                "elo", "n_fights", "form_score", "finish_rate",
                "layoff_factor", "age", "sig_strikes_ew", "td_landed_ew",
                "win_rate", "ko_rate", "sub_rate"
            ]}
            feat_b = {k: matched_row.get(f"b_{k}") for k in feat_a.keys()}
            feats = _residual_features(feat_a, feat_b)
        else:
            # No feature join: use intercept + elo gap if available
            feats = [1.0] + [0.0] * (len(RESIDUAL_FEATURE_NAMES) - 1)

        X.append(feats)
        y.append(residual)

    return X, y


# ── Model training ────────────────────────────────────────────────────────────

def train(save: bool = False) -> dict:
    """
    Train ridge regression on (model_prob - market_prob) targets.
    Returns training summary dict.
    """
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import mean_squared_error, mean_absolute_error

    residual_rows = _load_residual_data()
    feature_rows  = _load_feature_rows()

    if len(residual_rows) < 10:
        print(f"[ResidualModel] Only {len(residual_rows)} calibration rows. "
              f"Need ≥10 to train. Skipping.")
        return {"status": "insufficient_data", "n": len(residual_rows)}

    X, y = _build_training_matrix(residual_rows, feature_rows)

    if not X:
        return {"status": "no_features"}

    arr_X = np.array(X, dtype=float)
    arr_y = np.array(y, dtype=float)

    # Time-ordered split: last 20% for evaluation
    split = max(1, int(len(arr_X) * 0.80))
    X_train, X_test = arr_X[:split], arr_X[split:]
    y_train, y_test = arr_y[:split], arr_y[split:]

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("ridge",  Ridge(alpha=RIDGE_ALPHA)),
    ])
    model.fit(X_train, y_train)

    # Evaluation
    y_pred_train = model.predict(X_train)
    y_pred_test  = model.predict(X_test) if len(X_test) > 0 else []

    train_mse = float(mean_squared_error(y_train, y_pred_train))
    train_mae = float(mean_absolute_error(y_train, y_pred_train))

    test_mse  = float(mean_squared_error(y_test, y_pred_test)) if len(y_pred_test) > 0 else None
    test_mae  = float(mean_absolute_error(y_test, y_pred_test)) if len(y_pred_test) > 0 else None

    # Baseline: predict mean residual always
    mean_residual = float(np.mean(y_train))
    baseline_mse  = float(np.mean((y_test - mean_residual) ** 2)) if len(y_test) > 0 else None

    # Coefficient summary
    ridge = model.named_steps["ridge"]
    n_feat = arr_X.shape[1]
    coef_names = RESIDUAL_FEATURE_NAMES[:n_feat] if n_feat <= len(RESIDUAL_FEATURE_NAMES) else [
        f"f{i}" for i in range(n_feat)
    ]
    coefficients = {name: round(float(c), 6) for name, c in zip(coef_names, ridge.coef_)}

    summary = {
        "status":         "trained",
        "n_train":        len(X_train),
        "n_test":         len(X_test),
        "mean_residual":  round(mean_residual, 4),
        "train_mse":      round(train_mse, 6),
        "train_mae":      round(train_mae, 4),
        "test_mse":       round(test_mse, 6) if test_mse is not None else None,
        "test_mae":       round(test_mae, 4) if test_mae is not None else None,
        "baseline_mse":   round(baseline_mse, 6) if baseline_mse is not None else None,
        "vs_baseline":    (
            round((baseline_mse - test_mse) / baseline_mse * 100, 1)
            if baseline_mse and test_mse else None
        ),
        "coefficients":   coefficients,
        "ridge_alpha":    RIDGE_ALPHA,
    }

    if save:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
        META_PATH.write_text(json.dumps(summary, indent=2, default=str))
        print(f"[ResidualModel] Saved to {MODEL_PATH}")

    _print_summary(summary)
    return summary


def _print_summary(s: dict) -> None:
    print(f"\n{'='*56}")
    print(f"  RESIDUAL MODEL SUMMARY")
    print(f"{'='*56}")
    print(f"  Status:          {s['status']}")
    if s["status"] != "trained":
        return
    print(f"  Training rows:   {s['n_train']}  |  Test rows: {s['n_test']}")
    print(f"  Mean residual:   {s['mean_residual']:+.4f} (systematic model bias)")
    print(f"  Train MAE:       {s['train_mae']:.4f}")
    print(f"  Test MAE:        {s['test_mae'] or '—'}")
    if s.get("vs_baseline") is not None:
        sign = "+" if s["vs_baseline"] > 0 else ""
        print(f"  vs. Baseline:    {sign}{s['vs_baseline']}% MSE improvement")
    print(f"\n  Top coefficients (ridge α={s['ridge_alpha']}):")
    coefs = sorted(s.get("coefficients", {}).items(), key=lambda x: abs(x[1]), reverse=True)
    for name, val in coefs[:8]:
        bar = "█" * min(20, int(abs(val) * 200))
        sign = "+" if val >= 0 else "-"
        print(f"    {name:<22}  {sign}{abs(val):.4f}  {bar}")
    print(f"{'='*56}")


# ── Predictor class ───────────────────────────────────────────────────────────

class ResidualPredictor:
    """
    Predicts market residual (model_prob - market_prob) for a matchup.

    A positive value means the trained model thinks fighter A is undervalued
    relative to the current market. A negative value is the opposite.

    This is a SECONDARY signal — it informs confidence, not the core edge.
    """

    def __init__(self) -> None:
        self._model = None
        self._loaded = False
        self._mean_residual = 0.0

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if MODEL_PATH.exists():
            try:
                with open(MODEL_PATH, "rb") as f:
                    self._model = pickle.load(f)
                if META_PATH.exists():
                    meta = json.loads(META_PATH.read_text())
                    self._mean_residual = float(meta.get("mean_residual", 0.0))
            except Exception:
                self._model = None

    def train(self, save: bool = True) -> dict:
        """Re-train from current calibration data."""
        result = train(save=save)
        self._loaded = False  # force reload next predict()
        return result

    def predict(self, feat_a: dict, feat_b: dict) -> float | None:
        """
        Return predicted residual (model_prob - market_implied_prob).
        Returns None if no model is available.
        """
        self._ensure_loaded()
        if self._model is None:
            return None
        try:
            import numpy as np
            feats = _residual_features(feat_a, feat_b)
            arr   = np.array([feats], dtype=float)
            return float(self._model.predict(arr)[0])
        except Exception:
            return None

    def trust_level(self, residual: float | None) -> str:
        """
        Classify the residual signal into an actionable trust level.

          "Strong"   → model sees ≥8pp of hidden value vs market
          "Moderate" → model sees 4-8pp of value
          "Neutral"  → small disagreement, no strong signal
          "Fade"     → model is substantially below market → potential fade signal
        """
        if residual is None:
            return "Neutral"
        if residual >= STRONG_THRESHOLD:
            return "Strong"
        if residual >= MODERATE_THRESHOLD:
            return "Moderate"
        if residual <= FADE_THRESHOLD:
            return "Fade"
        return "Neutral"

    def adjust_confidence(
        self,
        base_confidence: str,
        feat_a: dict,
        feat_b: dict,
    ) -> tuple[str, float | None]:
        """
        Optionally upgrade or downgrade base_confidence based on residual signal.

        Returns (adjusted_confidence, residual).
        Rules:
          - Strong residual + High confidence → keep High (confirm)
          - Strong residual + Medium confidence → upgrade to High
          - Fade signal + any confidence → downgrade one level
          - Neutral → no change
        """
        residual = self.predict(feat_a, feat_b)
        level    = self.trust_level(residual)

        tiers = ["Low", "Low-Medium", "Medium", "High"]
        idx   = tiers.index(base_confidence) if base_confidence in tiers else 2

        if level == "Strong":
            idx = min(idx + 1, len(tiers) - 1)
        elif level == "Fade":
            idx = max(idx - 1, 0)

        return tiers[idx], residual


# ── Singleton ─────────────────────────────────────────────────────────────────

_predictor: ResidualPredictor | None = None


def get_predictor() -> ResidualPredictor:
    global _predictor
    if _predictor is None:
        _predictor = ResidualPredictor()
    return _predictor


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Train market residual regression model.")
    parser.add_argument("--save", action="store_true", help="Save model to disk")
    parser.add_argument("--eval", action="store_true", help="Run LOO evaluation summary")
    args = parser.parse_args()

    if args.eval:
        rows = _load_residual_data()
        if len(rows) < 5:
            print(f"Not enough data for evaluation ({len(rows)} rows).")
            return
        residuals = [float(r["model_probability"]) - float(r["market_probability"]) for r in rows]
        mean_r = sum(residuals) / len(residuals)
        mae    = sum(abs(r) for r in residuals) / len(residuals)
        std    = math.sqrt(sum((r - mean_r)**2 for r in residuals) / len(residuals))
        print(f"\n  Residual distribution across {len(rows)} predictions:")
        print(f"    Mean:   {mean_r:+.4f}")
        print(f"    MAE:    {mae:.4f}")
        print(f"    Std:    {std:.4f}")
        print(f"    Min:    {min(residuals):+.4f}")
        print(f"    Max:    {max(residuals):+.4f}")
        print()

    train(save=args.save)


if __name__ == "__main__":
    main()
