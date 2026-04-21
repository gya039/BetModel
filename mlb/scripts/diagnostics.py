"""
Walk-forward validation and calibration diagnostics for the MLB moneyline model.

This does not change daily staking rules. It measures whether the edge estimates
are trustworthy under several practical filters.

Usage:
    python mlb/scripts/diagnostics.py
    python mlb/scripts/diagnostics.py --bankroll 500
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.preprocessing import StandardScaler

sys.path.append(str(Path(__file__).parent.parent.parent))
from betfair.kelly import implied_probability
from mlb.scripts.feature_utils import FEATURES

PROC_DIR = Path(__file__).parent.parent / "data" / "processed"
OUT_DIR = Path(__file__).parent.parent / "predictions" / "diagnostics"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FAV_ODDS = 1.870
DOG_ODDS = 2.050


def stake_tier(edge: float, bankroll: float) -> float:
    if edge < 0.01:
        pct = 0.0
    elif edge < 0.03:
        pct = 0.005
    elif edge < 0.06:
        pct = 0.01
    elif edge < 0.10:
        pct = 0.02
    elif edge < 0.15:
        pct = 0.03
    elif edge < 0.20:
        pct = 0.04
    else:
        pct = 0.05
    return round(bankroll * pct, 2)


def load_data() -> pd.DataFrame:
    df = pd.read_csv(PROC_DIR / "games_processed.csv", parse_dates=["game_date"])
    missing = [c for c in FEATURES if c not in df.columns]
    if missing:
        raise SystemExit(
            "Processed data is missing new feature columns. Run "
            "python mlb/scripts/preprocess.py first. Missing: " + ", ".join(missing)
        )
    return df.dropna(subset=FEATURES + ["home_win"]).sort_values("game_date").reset_index(drop=True)


def monthly_folds(df: pd.DataFrame, min_train_frac: float = 0.40):
    min_train = int(len(df) * min_train_frac)
    test_months = (
        df.iloc[min_train:]["game_date"].dt.to_period("M").drop_duplicates().tolist()
    )
    for month in test_months:
        test_mask = df["game_date"].dt.to_period("M") == month
        test = df[test_mask]
        train = df[df["game_date"] < test["game_date"].min()]
        if len(train) < min_train or len(test) < 20:
            continue
        yield str(month), train, test


def fit_predict(train: pd.DataFrame, test: pd.DataFrame):
    calib_idx = max(20, int(len(train) * 0.80))
    fit_df = train.iloc[:calib_idx]
    calib_df = train.iloc[calib_idx:]
    scaler = StandardScaler()
    model = LogisticRegression(C=1.0, max_iter=500, random_state=42)
    model.fit(scaler.fit_transform(fit_df[FEATURES].values), fit_df["home_win"].values)
    if len(calib_df) >= 20 and calib_df["home_win"].nunique() == 2:
        model = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
        model.fit(scaler.transform(calib_df[FEATURES].values), calib_df["home_win"].values)
    probs = model.predict_proba(scaler.transform(test[FEATURES].values))[:, 1]
    return model, probs


def simulate_bets(test: pd.DataFrame, probs: np.ndarray, bankroll: float, min_edge: float, top_n: int | None):
    rows = []
    for row, home_prob in zip(test.to_dict("records"), probs):
        away_prob = 1.0 - home_prob
        candidates = []
        if home_prob > 0.505:
            candidates.append(("home", home_prob, FAV_ODDS, bool(row["home_win"])))
        if away_prob > 0.505:
            candidates.append(("away", away_prob, DOG_ODDS, not bool(row["home_win"])))
        for side, model_prob, odds, won in candidates:
            edge = model_prob - implied_probability(odds)
            if edge < min_edge:
                continue
            stake = stake_tier(edge, bankroll)
            if stake <= 0:
                continue
            pnl = round(stake * (odds - 1.0) if won else -stake, 2)
            rows.append({
                "date": row["game_date"],
                "side": side,
                "odds": odds,
                "model_prob": model_prob,
                "edge": edge,
                "stake": stake,
                "won": won,
                "pnl": pnl,
            })
    if top_n is not None:
        by_day = {}
        for row in rows:
            day = str(pd.Timestamp(row["date"]).date())
            by_day.setdefault(day, []).append(row)
        rows = [
            row
            for day_rows in by_day.values()
            for row in sorted(day_rows, key=lambda r: r["edge"], reverse=True)[:top_n]
        ]
    rows = sorted(rows, key=lambda r: (r["date"], -r["edge"]))
    return rows


def summarize_bets(rows: list[dict]) -> dict:
    if not rows:
        return {"bets": 0, "staked": 0.0, "pnl": 0.0, "roi": 0.0, "win_rate": 0.0, "drawdown": 0.0}
    df = pd.DataFrame(rows)
    staked = float(df["stake"].sum())
    pnl = float(df["pnl"].sum())
    cum = df.sort_values("date")["pnl"].cumsum()
    drawdown = float((cum - cum.cummax()).min())
    return {
        "bets": int(len(df)),
        "staked": round(staked, 2),
        "pnl": round(pnl, 2),
        "roi": round(pnl / staked * 100, 2) if staked else 0.0,
        "win_rate": round(float(df["won"].mean() * 100), 2),
        "drawdown": round(drawdown, 2),
    }


def calibration_table(y_true: np.ndarray, probs: np.ndarray) -> tuple[list[dict], float]:
    bins = np.arange(0.40, 0.76, 0.05)
    rows = []
    ece = 0.0
    for lo in bins:
        hi = lo + 0.05
        mask = (probs >= lo) & (probs < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        avg_pred = float(probs[mask].mean())
        actual = float(y_true[mask].mean())
        ece += (n / len(probs)) * abs(avg_pred - actual)
        rows.append({
            "bucket": f"{lo:.0%}-{hi:.0%}",
            "games": n,
            "avg_pred": round(avg_pred, 4),
            "actual": round(actual, 4),
            "diff": round(actual - avg_pred, 4),
        })
    return rows, round(float(ece), 4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bankroll", type=float, default=500.0)
    args = parser.parse_args()

    df = load_data()
    fold_rows = []
    all_probs = []
    all_true = []
    all_tests = []
    feature_importance = []

    for label, train, test in monthly_folds(df):
        model, probs = fit_predict(train, test)
        y = test["home_win"].values
        fold_rows.append({
            "fold": label,
            "train_games": len(train),
            "test_games": len(test),
            "log_loss": round(float(log_loss(y, probs)), 4),
            "brier": round(float(brier_score_loss(y, probs)), 4),
            "accuracy": round(float(accuracy_score(y, probs > 0.5)), 4),
        })
        all_probs.extend(probs.tolist())
        all_true.extend(y.tolist())
        all_tests.append(test.assign(_prob=probs))
        coef_model = model
        if not hasattr(coef_model, "coef_") and hasattr(model, "calibrated_classifiers_"):
            coef_model = model.calibrated_classifiers_[0].estimator.estimator
        if hasattr(coef_model, "coef_"):
            feature_importance.append(pd.Series(np.abs(coef_model.coef_[0]), index=FEATURES))

    if not fold_rows:
        raise SystemExit("Not enough data for monthly walk-forward folds.")

    test_all = pd.concat(all_tests, ignore_index=True)
    probs_all = np.array(all_probs)
    y_all = np.array(all_true)

    threshold_results = []
    for threshold in (0.03, 0.04, 0.05, 0.06):
        for top_n in (3, 5, None):
            rows = simulate_bets(test_all, probs_all, args.bankroll, threshold, top_n)
            summary = summarize_bets(rows)
            summary.update({
                "min_edge": threshold,
                "selection": "all" if top_n is None else f"top_{top_n}",
                "clv": "not_available_without_historical_closing_odds",
            })
            threshold_results.append(summary)

    edge_buckets = []
    all_bets = simulate_bets(test_all, probs_all, args.bankroll, 0.01, None)
    if all_bets:
        bets_df = pd.DataFrame(all_bets)
        bets_df["bucket"] = pd.cut(
            bets_df["edge"],
            bins=[0.01, 0.03, 0.06, 0.10, 0.15, 0.20, 1.0],
            labels=["1-3%", "3-6%", "6-10%", "10-15%", "15-20%", "20%+"],
            include_lowest=True,
        )
        for bucket, group in bets_df.groupby("bucket", observed=True):
            s = summarize_bets(group.to_dict("records"))
            s["edge_bucket"] = str(bucket)
            edge_buckets.append(s)

    odds_ranges = []
    if all_bets:
        bets_df["odds_range"] = pd.cut(
            bets_df["odds"],
            bins=[1.0, 1.6, 1.9, 2.2, 5.0],
            labels=["<=1.60", "1.61-1.90", "1.91-2.20", "2.21+"],
        )
        for bucket, group in bets_df.groupby("odds_range", observed=True):
            s = summarize_bets(group.to_dict("records"))
            s["odds_range"] = str(bucket)
            odds_ranges.append(s)

    fav_dog = []
    if all_bets:
        bets_df["fav_dog"] = np.where(bets_df["odds"] < 2.0, "favorite", "underdog")
        for label, group in bets_df.groupby("fav_dog"):
            s = summarize_bets(group.to_dict("records"))
            s["segment"] = label
            fav_dog.append(s)

    cal_rows, ece = calibration_table(y_all, probs_all)
    if feature_importance:
        feature_summary = (
            pd.concat(feature_importance, axis=1).mean(axis=1).sort_values(ascending=False).head(20)
        )
    else:
        feature_summary = pd.Series(dtype=float)

    payload = {
        "games": int(len(df)),
        "folds": fold_rows,
        "overall": {
            "log_loss": round(float(log_loss(y_all, probs_all)), 4),
            "brier": round(float(brier_score_loss(y_all, probs_all)), 4),
            "accuracy": round(float(accuracy_score(y_all, probs_all > 0.5)), 4),
            "ece": ece,
        },
        "calibration": cal_rows,
        "threshold_comparison": threshold_results,
        "edge_buckets": edge_buckets,
        "odds_ranges": odds_ranges,
        "favorite_vs_underdog": fav_dog,
        "feature_importance": [
            {"feature": k, "mean_abs_coef": round(float(v), 5)}
            for k, v in feature_summary.items()
        ],
    }

    (OUT_DIR / "walk_forward_diagnostics.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# MLB Walk-Forward Diagnostics",
        "",
        f"Games: {payload['games']}",
        f"Overall log loss: {payload['overall']['log_loss']}",
        f"Overall Brier: {payload['overall']['brier']}",
        f"Overall accuracy: {payload['overall']['accuracy']:.1%}",
        f"Expected calibration error: {payload['overall']['ece']}",
        "",
        "## Folds",
        "| Fold | Train | Test | Log Loss | Brier | Accuracy |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for r in fold_rows:
        lines.append(f"| {r['fold']} | {r['train_games']} | {r['test_games']} | {r['log_loss']} | {r['brier']} | {r['accuracy']:.1%} |")
    lines.extend(["", "## Calibration", "| Bucket | Games | Avg Pred | Actual | Diff |", "|---|---:|---:|---:|---:|"])
    for r in cal_rows:
        lines.append(f"| {r['bucket']} | {r['games']} | {r['avg_pred']:.1%} | {r['actual']:.1%} | {r['diff']:+.1%} |")
    lines.extend(["", "## Threshold Comparison", "| Min Edge | Selection | Bets | ROI | P&L | Drawdown | Win % |", "|---:|---|---:|---:|---:|---:|---:|"])
    for r in threshold_results:
        lines.append(f"| {r['min_edge']:.0%} | {r['selection']} | {r['bets']} | {r['roi']:+.2f}% | EUR {r['pnl']:+.2f} | EUR {r['drawdown']:+.2f} | {r['win_rate']:.1f}% |")
    lines.extend(["", "## Top Feature Contributions", "| Feature | Mean |", "|---|---:|"])
    for r in payload["feature_importance"]:
        lines.append(f"| {r['feature']} | {r['mean_abs_coef']} |")

    (OUT_DIR / "walk_forward_diagnostics.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_DIR / 'walk_forward_diagnostics.json'}")
    print(f"Wrote {OUT_DIR / 'walk_forward_diagnostics.md'}")


if __name__ == "__main__":
    main()
