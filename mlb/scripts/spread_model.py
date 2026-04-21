"""
MLB Spread / Run-Line Model

Trains a ridge regression on home margin (home_score - away_score) and uses
the residual distribution to estimate cover probability for any spread line.

For any spread S:
  P(home covers S)  = P(margin > S  | features)
  P(away covers S)  = P(margin < S  | features)  =  1 - P(margin > S | features)

The model can price ±1.5, ±2.5, ±3.5, ±4.5, ±5.5, or any other line a book offers.
ML inference is never used here — cover probability comes entirely from this model.

Usage:
    python mlb/scripts/spread_model.py               # train + save + diagnostics
    python mlb/scripts/spread_model.py --csv path    # use alternate processed CSV
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

sys.path.append(str(Path(__file__).parent.parent.parent))
from mlb.scripts.feature_utils import FEATURES

PROC_DIR = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_PATH = MODEL_DIR / "spread_model.pkl"

COMMON_SPREADS = [-5.5, -4.5, -3.5, -2.5, -1.5, -0.5, 0.5, 1.5, 2.5, 3.5, 4.5, 5.5]

# Validation thresholds to be considered "validated"
MIN_TEST_GAMES = 200
MAX_ECE_AT_MINUS_1_5 = 0.07    # calibration error at -1.5 line must be below 7%
MIN_POSITIVE_ROI_SPREADS = 1   # at least 1 spread line must show positive simulated ROI


class SpreadModel:
    """
    Margin regression model for MLB spread cover probability estimation.

    fit() trains on processed game data.
    cover_prob() estimates P(home_score - away_score > spread_point).
    best_cover_ev() finds the highest-EV spread option from a list of {line, odds}.
    """

    def __init__(self):
        self.model: Ridge | None = None
        self.scaler: StandardScaler | None = None
        self.residual_std: float | None = None
        self.features: list[str] | None = None
        self.trained_through: str | None = None
        self.train_n: int | None = None

    def fit(self, df: pd.DataFrame, features: list[str]) -> "SpreadModel":
        self.features = features
        X = df[features].fillna(0.0).values.astype(float)
        y = df["point_diff"].values.astype(float)
        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        self.model = Ridge(alpha=1.0)
        self.model.fit(X_scaled, y)
        preds = self.model.predict(X_scaled)
        self.residual_std = float(np.std(y - preds, ddof=1))
        self.trained_through = str(df["game_date"].max())[:10]
        self.train_n = len(df)
        return self

    def predict_margin(self, feat_vec: list) -> float:
        """Predict expected home margin (home_score - away_score)."""
        X = np.array([feat_vec], dtype=float)
        return float(self.model.predict(self.scaler.transform(X))[0])

    def cover_prob(self, feat_vec: list, spread_point: float) -> float:
        """P(home_score - away_score > spread_point) — home covers spread_point."""
        pred = self.predict_margin(feat_vec)
        return float(1.0 - norm.cdf(spread_point, loc=pred, scale=self.residual_std))

    def best_cover_ev(self, feat_vec: list, spread_options: list[dict]) -> dict | None:
        """
        Given [{line, odds}, ...], find the highest-EV home-cover spread bet.
        Returns {line, odds, cover_prob, edge} or None if no option has positive EV.
        """
        best: dict | None = None
        for opt in spread_options:
            line = float(opt["line"])
            odds = float(opt["odds"])
            if odds <= 1.0:
                continue
            prob = self.cover_prob(feat_vec, line)
            implied = 1.0 / odds
            ev = round(prob - implied, 4)
            if best is None or ev > best["edge"]:
                best = {
                    "line": line,
                    "odds": round(odds, 3),
                    "cover_prob": round(prob, 4),
                    "edge": ev,
                }
        return best if best and best["edge"] > 0 else None

    def is_validated(self, diag: dict) -> tuple[bool, list[str]]:
        """
        Check whether this model meets validation thresholds.
        Returns (passed: bool, reasons: list[str]).
        """
        reasons = []
        ok = True
        if diag.get("n_test", 0) < MIN_TEST_GAMES:
            reasons.append(f"Insufficient test games: {diag.get('n_test')} < {MIN_TEST_GAMES}")
            ok = False
        ece_rows = {r["spread"]: r["ece"] for r in diag.get("ece_by_spread", [])}
        if -1.5 in ece_rows and ece_rows[-1.5] > MAX_ECE_AT_MINUS_1_5:
            reasons.append(f"ECE at -1.5 too high: {ece_rows[-1.5]:.4f} > {MAX_ECE_AT_MINUS_1_5}")
            ok = False
        positive_roi_count = sum(
            1 for r in diag.get("roi_by_spread", []) if r.get("roi", -999) > 0
        )
        if positive_roi_count < MIN_POSITIVE_ROI_SPREADS:
            reasons.append(f"No positive-ROI spread lines (need {MIN_POSITIVE_ROI_SPREADS})")
            ok = False
        if ok:
            reasons.append("All validation thresholds passed.")
        return ok, reasons

    def save(self, path: Path) -> None:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "scaler": self.scaler,
                "residual_std": self.residual_std,
                "features": self.features,
                "trained_through": self.trained_through,
                "train_n": self.train_n,
            }, f)

    @classmethod
    def load(cls, path: Path) -> "SpreadModel":
        obj = cls()
        with open(path, "rb") as f:
            saved = pickle.load(f)
        obj.model = saved["model"]
        obj.scaler = saved["scaler"]
        obj.residual_std = saved["residual_std"]
        obj.features = saved["features"]
        obj.trained_through = saved.get("trained_through")
        obj.train_n = saved.get("train_n")
        return obj


def _chronological_split(df: pd.DataFrame, test_fraction: float = 0.20) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = df.sort_values("game_date").reset_index(drop=True)
    split = int(len(df) * (1.0 - test_fraction))
    return df.iloc[:split].copy(), df.iloc[split:].copy()


def run_diagnostics(model: SpreadModel, test_df: pd.DataFrame) -> dict:
    """Full diagnostic suite: residual stats, calibration, ECE, ROI simulation."""
    features = model.features
    X_test = test_df[features].fillna(0.0).values.astype(float)
    y_test = test_df["point_diff"].values.astype(float)
    pred_margins = model.model.predict(model.scaler.transform(X_test))

    # Per-game per-spread results
    records = []
    for pred, actual in zip(pred_margins, y_test):
        for spread in COMMON_SPREADS:
            cover_p = float(1.0 - norm.cdf(spread, loc=pred, scale=model.residual_std))
            records.append({
                "spread": spread,
                "cover_prob": cover_p,
                "covered": int(actual > spread),
                "pred_margin": float(pred),
                "actual_margin": float(actual),
            })
    df = pd.DataFrame(records)

    # ECE per spread line
    ece_rows = []
    for spread in COMMON_SPREADS:
        sub = df[df["spread"] == spread]
        if len(sub) < 10:
            continue
        ece = float(np.mean(np.abs(sub["cover_prob"] - sub["covered"])))
        ece_rows.append({
            "spread": spread,
            "n": len(sub),
            "cover_rate": round(float(sub["covered"].mean()), 4),
            "mean_pred_prob": round(float(sub["cover_prob"].mean()), 4),
            "ece": round(ece, 4),
        })

    # Calibration table: bucket predicted prob, compare to actual cover rate
    calib_rows = []
    bins = [0.0, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0]
    for spread in COMMON_SPREADS:
        sub = df[df["spread"] == spread].copy()
        if len(sub) < 20:
            continue
        sub["bucket"] = pd.cut(sub["cover_prob"], bins=bins, include_lowest=True)
        for bucket, g in sub.groupby("bucket", observed=True):
            if len(g) < 5:
                continue
            calib_rows.append({
                "spread": spread,
                "prob_bucket": str(bucket),
                "n": len(g),
                "mean_pred_prob": round(float(g["cover_prob"].mean()), 4),
                "actual_cover_rate": round(float(g["covered"].mean()), 4),
            })

    # ROI simulation: bet when edge >= 3% at assumed fair odds (−110 = 1.909 decimal)
    ASSUMED_ODDS = 1.909
    roi_rows = []
    for spread in COMMON_SPREADS:
        sub = df[df["spread"] == spread].copy()
        if len(sub) < 10:
            continue
        implied = 1.0 / ASSUMED_ODDS
        sub["edge"] = sub["cover_prob"] - implied
        bets = sub[sub["edge"] >= 0.03]
        if len(bets) < 5:
            continue
        wins = int(bets["covered"].sum())
        n_bets = len(bets)
        roi = (wins * (ASSUMED_ODDS - 1.0) - (n_bets - wins)) / n_bets
        roi_rows.append({
            "spread": spread,
            "n_bets": n_bets,
            "win_rate": round(wins / n_bets, 4),
            "roi": round(float(roi), 4),
        })

    # Favorite / underdog split at ±1.5
    fav_sub = df[df["spread"] == -1.5]
    dog_sub = df[df["spread"] == 1.5]

    resid = y_test - pred_margins
    return {
        "n_test": len(test_df),
        "residual_std": round(float(model.residual_std), 3),
        "mae": round(float(np.mean(np.abs(resid))), 3),
        "rmse": round(float(np.sqrt(np.mean(resid ** 2))), 3),
        "fav_cover_rate_at_minus_1_5": round(float(fav_sub["covered"].mean()), 4) if len(fav_sub) > 0 else None,
        "dog_cover_rate_at_plus_1_5": round(float(dog_sub["covered"].mean()), 4) if len(dog_sub) > 0 else None,
        "ece_by_spread": ece_rows,
        "roi_by_spread": roi_rows,
        "calibration": calib_rows,
    }


def train_and_save(
    processed_csv: Path = PROC_DIR / "games_processed.csv",
    model_path: Path = MODEL_PATH,
    features: list[str] | None = None,
) -> dict:
    """Load processed data, train spread model, save, run and print diagnostics."""
    if not processed_csv.exists():
        print(f"[!] Processed CSV not found: {processed_csv}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(processed_csv)
    df = df.dropna(subset=[
        "HOME_L10_WIN_PCT", "AWAY_L10_WIN_PCT",
        "HOME_L10_RD", "AWAY_L10_RD",
        "HOME_L20_WIN_PCT", "AWAY_L20_WIN_PCT",
        "HOME_L20_RD", "AWAY_L20_RD",
    ])

    if "point_diff" not in df.columns:
        if "home_score" in df.columns and "away_score" in df.columns:
            df["point_diff"] = df["home_score"].astype(float) - df["away_score"].astype(float)
        else:
            print("[!] Cannot find point_diff or home_score/away_score in processed CSV.", file=sys.stderr)
            sys.exit(1)

    if features is None:
        features = FEATURES

    # Use only features that exist in the CSV; fill missing ones with 0
    for f in features:
        if f not in df.columns:
            df[f] = 0.0
    features = [f for f in features if f in df.columns]

    print(f"  Dataset : {len(df)} games  ({df['game_date'].min()} → {df['game_date'].max()})")
    print(f"  Features: {len(features)}")

    train_df, test_df = _chronological_split(df, test_fraction=0.20)
    print(f"  Train   : {len(train_df)}  |  Test: {len(test_df)}")

    sm = SpreadModel()
    sm.fit(train_df, features)
    sm.save(model_path)
    print(f"  Saved   → {model_path}")
    print(f"  σ residual = {sm.residual_std:.3f} runs  |  trained through {sm.trained_through}")

    print("\n  === DIAGNOSTICS ===\n")
    diag = run_diagnostics(sm, test_df)
    print(f"  Test N : {diag['n_test']}   MAE: {diag['mae']}   RMSE: {diag['rmse']}")
    print(f"  Fav (-1.5) actual cover rate : {diag['fav_cover_rate_at_minus_1_5']}")
    print(f"  Dog (+1.5) actual cover rate : {diag['dog_cover_rate_at_plus_1_5']}")

    if diag["ece_by_spread"]:
        print(f"\n  Calibration error by spread line:")
        print(f"  {'Line':>6}  {'N':>5}  {'Cover%':>7}  {'PredProb%':>10}  {'ECE':>7}")
        for row in diag["ece_by_spread"]:
            print(
                f"  {row['spread']:>6.1f}  {row['n']:>5}  "
                f"{row['cover_rate']*100:>6.1f}%  {row['mean_pred_prob']*100:>9.1f}%  "
                f"{row['ece']:>7.4f}"
            )

    if diag["roi_by_spread"]:
        print(f"\n  ROI simulation (edge ≥ 3%, odds 1.909 = -110):")
        print(f"  {'Line':>6}  {'Bets':>5}  {'Win%':>7}  {'ROI':>7}")
        for row in diag["roi_by_spread"]:
            print(
                f"  {row['spread']:>6.1f}  {row['n_bets']:>5}  "
                f"{row['win_rate']*100:>6.1f}%  {row['roi']*100:>+6.1f}%"
            )

    validated, reasons = sm.is_validated(diag)
    print(f"\n  === VALIDATION {'PASSED ✓' if validated else 'FAILED ✗'} ===")
    for r in reasons:
        print(f"  {r}")
    if validated:
        print("\n  To enable spread betting: set USE_SPREAD_MODEL = True in predict_today.py")

    diag_path = model_path.parent / "spread_model_diagnostics.json"
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2)
    print(f"\n  Diagnostics → {diag_path}")

    return diag


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train and validate the MLB spread model.")
    parser.add_argument(
        "--csv",
        default=str(PROC_DIR / "games_processed.csv"),
        help="Path to processed games CSV (default: mlb/data/processed/games_processed.csv)",
    )
    args = parser.parse_args()

    print("\n=== MLB Spread Model Training ===\n")
    train_and_save(processed_csv=Path(args.csv))
    print("\nDone.")
