"""
MLB moneyline model — logistic regression on home win probability.

Features: rolling team form (L5/L10 win%, run differential, runs for/against)
          + starting pitcher ERA, WHIP, K/9 + derived differentials.

Uses a date-based split (first 40% of season = train, remaining 60% = test)
to mimic a mid-season model deployment.

Usage:
    python mlb/scripts/model.py         # train + save
    python mlb/scripts/model.py --eval  # show evaluation stats
"""

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import (accuracy_score, log_loss, brier_score_loss,
                              roc_auc_score)

sys.path.append(str(Path(__file__).parent.parent.parent))
from mlb.scripts.feature_utils import FEATURES

PROC_DIR  = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def load() -> pd.DataFrame:
    df = pd.read_csv(PROC_DIR / "games_processed.csv", parse_dates=["game_date"])
    df = df.dropna(subset=FEATURES + ["home_win"])
    return df.sort_values("game_date").reset_index(drop=True)


def date_split(df: pd.DataFrame, train_frac: float = 0.40):
    """Split by date (chronological) — not random — to avoid look-ahead."""
    cutoff_idx = int(len(df) * train_frac)
    cutoff_dt  = df.iloc[cutoff_idx]["game_date"]
    train = df[df["game_date"] <  cutoff_dt]
    test  = df[df["game_date"] >= cutoff_dt]
    return train, test, cutoff_dt


def train(train_df: pd.DataFrame):
    cutoff = max(20, int(len(train_df) * 0.80))
    fit_df = train_df.iloc[:cutoff]
    calib_df = train_df.iloc[cutoff:]
    X = fit_df[FEATURES].values
    y = fit_df["home_win"].values
    scaler = StandardScaler()
    model  = LogisticRegression(C=1.0, max_iter=500, random_state=42)
    model.fit(scaler.fit_transform(X), y)
    if len(calib_df) >= 20 and calib_df["home_win"].nunique() == 2:
        calibrator = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
        calibrator.fit(scaler.transform(calib_df[FEATURES].values), calib_df["home_win"].values)
        return calibrator, scaler
    return model, scaler


def evaluate(model, scaler, test_df: pd.DataFrame):
    X     = test_df[FEATURES].values
    y     = test_df["home_win"].values
    probs = model.predict_proba(scaler.transform(X))[:, 1]
    preds = (probs > 0.5).astype(int)

    acc    = accuracy_score(y, preds)
    ll     = log_loss(y, probs)
    brier  = brier_score_loss(y, probs)
    auc    = roc_auc_score(y, probs)
    home_w = y.mean()

    print(f"\n  Accuracy  : {acc:.1%}  (baseline={home_w:.1%})")
    print(f"  AUC-ROC   : {auc:.4f}")
    print(f"  Log loss  : {ll:.4f}")
    print(f"  Brier     : {brier:.4f}")

    # Feature importance (coefficient magnitude after scaling)
    coef_model = model
    if not hasattr(coef_model, "coef_") and hasattr(model, "calibrated_classifiers_"):
        coef_model = model.calibrated_classifiers_[0].estimator
    if hasattr(coef_model, "coef_"):
        coefs = pd.Series(
            np.abs(coef_model.coef_[0]),
            index=FEATURES,
        ).sort_values(ascending=False)

        print(f"\n  Top features (by |coef|):")
        for feat, v in coefs.head(10).items():
            direction = "HOME+" if coef_model.coef_[0][FEATURES.index(feat)] > 0 else "AWAY+"
            print(f"    {feat:<28} {v:.4f}  [{direction}]")

    # Probability calibration: predicted bucket vs actual win rate
    print(f"\n  --- Probability calibration (home win) ---")
    print(f"  {'Bucket':<12} {'Games':>6} {'Pred ~':>8} {'Actual':>8} {'Diff':>7}")
    buckets = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.0)]
    for lo, hi in buckets:
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n < 5:
            continue
        actual = float(y[mask].mean())
        mid = (lo + hi) / 2.0
        diff = actual - mid
        flag = "  OVER-conf" if diff < -0.04 else ("  UNDER-conf" if diff > 0.04 else "")
        print(f"  {lo:.0%}–{hi:.0%}      {n:>6}   {mid:.1%}   {actual:.1%}   {diff:+.1%}{flag}")
    # Away probability calibration (mirror: away_prob = 1 - home_prob)
    away_probs = 1.0 - probs
    away_y = 1 - y
    print(f"\n  --- Probability calibration (away win) ---")
    print(f"  {'Bucket':<12} {'Games':>6} {'Pred ~':>8} {'Actual':>8} {'Diff':>7}")
    for lo, hi in buckets:
        mask = (away_probs >= lo) & (away_probs < hi)
        n = int(mask.sum())
        if n < 5:
            continue
        actual = float(away_y[mask].mean())
        mid = (lo + hi) / 2.0
        diff = actual - mid
        flag = "  OVER-conf" if diff < -0.04 else ("  UNDER-conf" if diff > 0.04 else "")
        print(f"  {lo:.0%}–{hi:.0%}      {n:>6}   {mid:.1%}   {actual:.1%}   {diff:+.1%}{flag}")

    return probs


def predict_moneyline(features: dict) -> dict:
    """Predict home/away win probabilities for a live game (single row dict)."""
    with open(MODEL_DIR / "moneyline_model.pkl", "rb") as f:
        saved = pickle.load(f)
    X    = pd.DataFrame([features])[saved["features"]].values
    prob = float(saved["model"].predict_proba(saved["scaler"].transform(X))[0][1])
    return {"home": round(prob, 4), "away": round(1 - prob, 4)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval",       action="store_true")
    parser.add_argument("--train-frac", type=float, default=0.40)
    args = parser.parse_args()

    print("\n=== MLB 2025 Moneyline Model ===\n")
    df = load()
    print(f"  {len(df)} games loaded")

    train_df, test_df, cutoff = date_split(df, args.train_frac)
    print(f"  Train: {len(train_df)} games  (before {cutoff.date()})")
    print(f"  Test : {len(test_df)} games  (from  {cutoff.date()})")

    model, scaler = train(train_df)
    print(f"\n  Model trained.")

    if args.eval or True:   # always evaluate on first run
        print("\n  --- Test-set evaluation ---")
        evaluate(model, scaler, test_df)

    with open(MODEL_DIR / "moneyline_model.pkl", "wb") as f:
        pickle.dump({
            "model":    model,
            "scaler":   scaler,
            "features": FEATURES,
            "cutoff":   str(cutoff.date()),
        }, f)
    print(f"\n  Saved -> models/moneyline_model.pkl")
    print("\nDone.")
