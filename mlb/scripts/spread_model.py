"""
MLB Spread / Run-Line Model

Trains a ridge regression on home margin (home_score - away_score) and uses
the residual distribution to estimate cover probability for any spread line.

For sportsbook spread line S:
  P(home covers S)  = P(home margin > -S | features)
  P(away covers S)  = P(home margin <  S | features)

The model can price Â±1.5, Â±2.5, Â±3.5, Â±4.5, Â±5.5, or any other line a book offers.
ML inference is never used here â€” cover probability comes entirely from this model.

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
ECE_BINS = [0.0, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0]

# Validation thresholds to be considered "validated"
MIN_TEST_GAMES = 200
# Calibration: ECE must be below 7% on both the most-common lines (-1.5 and -2.5)
MAX_ECE_PRIMARY_LINES = 0.07
PRIMARY_ECE_CHECK_LINES = [-1.5, -2.5]
# ROI: allow one strong simulated ROI line. Keep ECE and sanity gates unchanged
# so changes in RL output are attributable to this filter only.
MIN_POSITIVE_ROI_SPREADS = 1
# Sanity: actual home -1.5 cover rate should be in a plausible MLB range.
# This is not favourite-only because historical bookmaker favourite lines are
# not stored in the processed data.
COVER_RATE_MIN = 0.30
COVER_RATE_MAX = 0.45


class SpreadModel:
    """
    Margin regression model for MLB spread cover probability estimation.

    fit() trains on processed game data.
    cover_prob() estimates P(home margin > -spread_point).
    best_cover_ev() finds the highest-EV spread option from a list of {line, odds}.
    """

    def __init__(self):
        self.model: Ridge | None = None
        self.scaler: StandardScaler | None = None
        self.residual_std: float | None = None
        self.features: list[str] | None = None
        self.trained_through: str | None = None
        self.train_n: int | None = None
        self.validation: dict | None = None
        self.validation_passed: bool | None = None
        self.validation_reasons: list[str] = []

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
        """P(home_score - away_score > spread_point) â€” home covers spread_point."""
        pred = self.predict_margin(feat_vec)
        return float(1.0 - norm.cdf(-spread_point, loc=pred, scale=self.residual_std))

    def _validation_check_summary(self) -> dict:
        validation = self.validation or {}
        ece_rows = {
            row.get("spread"): row.get("ece")
            for row in validation.get("ece_by_spread", [])
        }
        ece_pass = all(
            line in ece_rows and ece_rows[line] <= MAX_ECE_PRIMARY_LINES
            for line in PRIMARY_ECE_CHECK_LINES
        )
        positive_roi_count = sum(
            1 for row in validation.get("roi_by_spread", [])
            if row.get("roi", -999) > 0
        )
        roi_pass = positive_roi_count >= MIN_POSITIVE_ROI_SPREADS
        fav_cr = validation.get("fav_cover_rate_at_minus_1_5")
        sanity_pass = fav_cr is not None and COVER_RATE_MIN <= fav_cr <= COVER_RATE_MAX
        return {
            "ece_pass": ece_pass,
            "sanity_pass": sanity_pass,
            "roi_pass": roi_pass,
            "positive_roi_lines": positive_roi_count,
            "ece": ece_rows,
            "fav_cover_rate_at_minus_1_5": fav_cr,
        }

    def best_cover_ev(
        self,
        feat_vec: list,
        spread_options: list[dict],
        *,
        debug_label: str = "",
        debug: bool = False,
        return_diagnostics: bool = False,
    ) -> dict | None:
        """
        Given [{line, odds}, ...], find the highest-EV home-cover spread bet.
        P(home covers sportsbook line S) = P(home margin > -S).
        Returns {line, odds, cover_prob, edge} or None if no option has positive EV.
        """
        best, positive, valid_options = self._best_cover_ev_for_options(feat_vec, spread_options, away=False)
        if debug:
            self._log_ev_debug(debug_label or "HOME_RL", best, positive, valid_options)
        if not best:
            return None
        best = {
            **best,
            "positive_line_count": positive,
            "option_count": valid_options,
            "rejection_reason": self._ev_rejection_reason(best, positive),
        }
        if return_diagnostics:
            return best
        return best if best["edge"] > 0 else None

    def _best_cover_ev_for_options(
        self,
        feat_vec: list,
        spread_options: list[dict],
        *,
        away: bool,
    ) -> tuple[dict | None, int, int]:
        best: dict | None = None
        positive = 0
        valid_options = 0
        for opt in spread_options:
            line = float(opt["line"])
            odds = float(opt["odds"])
            if odds <= 1.0:
                continue
            valid_options += 1
            pred = self.predict_margin(feat_vec)
            prob = float(norm.cdf(line, loc=pred, scale=self.residual_std)) if away else self.cover_prob(feat_vec, line)
            implied = 1.0 / odds
            ev = round(prob - implied, 4)
            if ev > 0:
                positive += 1
            if best is None or ev > best["edge"]:
                best = {"line": line, "odds": round(odds, 3), "cover_prob": round(prob, 4), "edge": ev}
        return best, positive, valid_options

    def _ev_rejection_reason(self, best: dict | None, positive_count: int) -> str:
        if not best:
            return "no_valid_spread_options"
        if positive_count <= 0 or best.get("edge", 0) <= 0:
            return "no_positive_ev"
        if best.get("edge", 0) < 0.03:
            return "below_3pct_edge"
        checks = self._validation_check_summary()
        if not checks["ece_pass"]:
            return "failed_ece_gate"
        if not checks["roi_pass"]:
            return "failed_roi_gate"
        if not checks["sanity_pass"]:
            return "failed_sanity_gate"
        return "eligible"

    def best_away_cover_ev(
        self,
        feat_vec: list,
        spread_options: list[dict],
        *,
        debug_label: str = "",
        debug: bool = False,
        return_diagnostics: bool = False,
    ) -> dict | None:
        """
        Given [{line, odds}, ...] for the AWAY team, find the highest-EV away-cover bet.
        Away team covers sportsbook line S (e.g. +1.5) when home margin < S.
        All home/away symmetry is handled here â€” callers never invert probabilities themselves.
        Returns {line, odds, cover_prob, edge} or None if no option has positive EV.
        """
        best, positive, valid_options = self._best_cover_ev_for_options(feat_vec, spread_options, away=True)
        if debug:
            self._log_ev_debug(debug_label or "AWAY_RL", best, positive, valid_options)
        if not best:
            return None
        best = {
            **best,
            "positive_line_count": positive,
            "option_count": valid_options,
            "rejection_reason": self._ev_rejection_reason(best, positive),
        }
        if return_diagnostics:
            return best
        return best if best["edge"] > 0 else None

    def _log_ev_debug(self, label: str, best: dict | None, positive_count: int, option_count: int) -> None:
        checks = self._validation_check_summary()
        ece = checks["ece"]
        status = "accepted_positive_ev" if best and best.get("edge", 0) > 0 else "rejected_no_positive_ev"
        if self.validation_passed is False:
            status = self._ev_rejection_reason(best, positive_count)
        best_text = (
            f"line={best['line']:+g} odds={best['odds']:.3f} "
            f"cover_prob={best['cover_prob']:.4f} ev={best['edge']:.4f}"
            if best else "none"
        )
        print(
            "// Spread EV "
            f"{label}: best={best_text}; positive_lines={positive_count}/{option_count}; "
            f"ece(-1.5)={ece.get(-1.5, 'n/a')}; ece(-2.5)={ece.get(-2.5, 'n/a')}; "
            f"ece_pass={checks['ece_pass']}; sanity_pass={checks['sanity_pass']}; "
            f"roi_pass={checks['roi_pass']}; positive_roi_lines={checks['positive_roi_lines']}; "
            f"validation_passed={self.validation_passed}; "
            f"reason={status}; validation_reasons={self.validation_reasons}",
            file=sys.stderr,
        )

    def is_validated(self, diag: dict) -> tuple[bool, list[str]]:
        """
        Check whether this model meets all validation thresholds.
        Returns (passed: bool, reasons: list[str]).
        Checks: sample size, calibration on primary lines, positive ROI count,
        and home -1.5 cover-rate sanity.
        """
        reasons = []
        ok = True

        if diag.get("n_test", 0) < MIN_TEST_GAMES:
            reasons.append(f"Insufficient test games: {diag.get('n_test')} < {MIN_TEST_GAMES}")
            ok = False

        ece_rows = {r["spread"]: r["ece"] for r in diag.get("ece_by_spread", [])}
        for line in PRIMARY_ECE_CHECK_LINES:
            if line in ece_rows:
                if ece_rows[line] > MAX_ECE_PRIMARY_LINES:
                    reasons.append(
                        f"ECE at {line:+g} too high: {ece_rows[line]:.4f} > {MAX_ECE_PRIMARY_LINES}"
                    )
                    ok = False
            else:
                reasons.append(f"ECE data missing for {line:+g} line (too few samples?)")
                ok = False

        positive_roi_count = sum(1 for r in diag.get("roi_by_spread", []) if r.get("roi", -999) > 0)
        if positive_roi_count < MIN_POSITIVE_ROI_SPREADS:
            reasons.append(
                f"Only {positive_roi_count} positive-ROI spread lines (need {MIN_POSITIVE_ROI_SPREADS})"
            )
            ok = False

        fav_cr = diag.get("fav_cover_rate_at_minus_1_5")
        if fav_cr is not None and not (COVER_RATE_MIN <= fav_cr <= COVER_RATE_MAX):
            reasons.append(
                f"Home -1.5 cover rate is {fav_cr:.3f} - outside [{COVER_RATE_MIN}, {COVER_RATE_MAX}] "
                "(possible spread sign or calibration issue)"
            )
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
                "validation": self.validation,
                "validation_passed": self.validation_passed,
                "validation_reasons": self.validation_reasons,
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
        obj.validation = saved.get("validation")
        obj.validation_passed = saved.get("validation_passed")
        obj.validation_reasons = saved.get("validation_reasons", [])
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

    # Per-game per-spread results. COMMON_SPREADS are sportsbook home-team
    # lines: home -1.5 covers when margin > +1.5, home +1.5 when margin > -1.5.
    records = []
    for pred, actual in zip(pred_margins, y_test):
        for spread in COMMON_SPREADS:
            cover_p = float(1.0 - norm.cdf(-spread, loc=pred, scale=model.residual_std))
            records.append({
                "spread": spread,
                "cover_prob": cover_p,
                "covered": int(actual > -spread),
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
        sub = sub.copy()
        sub["bucket"] = pd.cut(sub["cover_prob"], bins=ECE_BINS, include_lowest=True)
        ece = 0.0
        for _, bucket_df in sub.groupby("bucket", observed=True):
            if len(bucket_df) < 5:
                continue
            weight = len(bucket_df) / len(sub)
            ece += weight * abs(float(bucket_df["cover_prob"].mean()) - float(bucket_df["covered"].mean()))
        ece_rows.append({
            "spread": spread,
            "n": len(sub),
            "cover_rate": round(float(sub["covered"].mean()), 4),
            "mean_pred_prob": round(float(sub["cover_prob"].mean()), 4),
            "ece": round(ece, 4),
            "mae_prob": round(float(np.mean(np.abs(sub["cover_prob"] - sub["covered"]))), 4),
        })

    # Calibration table: bucket predicted prob, compare to actual cover rate
    calib_rows = []
    for spread in COMMON_SPREADS:
        sub = df[df["spread"] == spread].copy()
        if len(sub) < 20:
            continue
        sub["bucket"] = pd.cut(sub["cover_prob"], bins=ECE_BINS, include_lowest=True)
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

    # ROI simulation: bet when edge >= 3% at assumed fair odds (âˆ’110 = 1.909 decimal)
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

    # Home sportsbook line cover rates at +/-1.5.
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
        "home_minus_1_5_cover_rate": round(float(fav_sub["covered"].mean()), 4) if len(fav_sub) > 0 else None,
        "home_plus_1_5_cover_rate": round(float(dog_sub["covered"].mean()), 4) if len(dog_sub) > 0 else None,
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

    print(f"  Dataset : {len(df)} games  ({df['game_date'].min()} â†’ {df['game_date'].max()})")
    print(f"  Features: {len(features)}")

    train_df, test_df = _chronological_split(df, test_fraction=0.20)
    print(f"  Train   : {len(train_df)}  |  Test: {len(test_df)}")

    sm = SpreadModel()
    sm.fit(train_df, features)
    print(f"  Ïƒ residual = {sm.residual_std:.3f} runs  |  trained through {sm.trained_through}")

    print("\n  === DIAGNOSTICS ===\n")
    diag = run_diagnostics(sm, test_df)
    print(f"  Test N : {diag['n_test']}   MAE: {diag['mae']}   RMSE: {diag['rmse']}")
    print(f"  Home -1.5 actual cover rate : {diag['home_minus_1_5_cover_rate']}")
    print(f"  Home +1.5 actual cover rate : {diag['home_plus_1_5_cover_rate']}")

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
        print(f"\n  ROI simulation (edge â‰¥ 3%, odds 1.909 = -110):")
        print(f"  {'Line':>6}  {'Bets':>5}  {'Win%':>7}  {'ROI':>7}")
        for row in diag["roi_by_spread"]:
            print(
                f"  {row['spread']:>6.1f}  {row['n_bets']:>5}  "
                f"{row['win_rate']*100:>6.1f}%  {row['roi']*100:>+6.1f}%"
            )

    validated, reasons = sm.is_validated(diag)
    sm.validation = diag
    sm.validation_passed = validated
    sm.validation_reasons = reasons
    print(f"\n  === VALIDATION {'PASSED âœ“' if validated else 'FAILED âœ—'} ===")
    for r in reasons:
        print(f"  {r}")
    if validated:
        print("\n  To enable spread betting: set USE_SPREAD_MODEL = True in predict_today.py")

    sm.save(model_path)
    print(f"\n  Saved   -> {model_path}")

    diag_path = model_path.parent / "spread_model_diagnostics.json"
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(diag, f, indent=2)
    print(f"\n  Diagnostics â†’ {diag_path}")

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
