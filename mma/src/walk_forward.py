"""
walk_forward.py — Walk-forward (expanding-window) cross-validation for UFC model.

Replaces the single train/test split with rolling yearly folds:

    Fold 1:  Train → everything up to 2021-12-31   Test → 2022
    Fold 2:  Train → everything up to 2022-12-31   Test → 2023
    Fold 3:  Train → everything up to 2023-12-31   Test → 2024
    Fold 4:  Train → everything up to 2024-12-31   Test → 2025

Each fold trains a fresh model and evaluates it on unseen future data.
Aggregated metrics give a far more honest picture than any single split.

Design invariants:
  - No random shuffling at any stage (would leak future into past)
  - Training window always starts from the beginning (expanding, not rolling)
  - Minimum training rows required per fold before evaluation
  - All metrics computed on the held-out test year only

Usage:
    python walk_forward.py
    python walk_forward.py --start 2020 --end 2025 --min-train 100
    python walk_forward.py --save
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import NamedTuple

from features import FEATURE_COLUMNS
from model_trainer import (
    TRAINING_CSV,
    _make_models,
    _make_lr,
    calibrate,
    evaluate,
    extract_features,
    load_csv,
)
from utils import DATA_PROC

OUTPUT_PATH = DATA_PROC.parent / "models" / "walk_forward_results.json"

MIN_TRAIN_ROWS  = 80   # folds with fewer rows are skipped
MIN_TEST_ROWS   = 10   # folds with too-thin test sets are flagged, not skipped
CAL_HOLD_PCT    = 0.20  # fraction of training reserved for calibration fitting


class Fold(NamedTuple):
    train_until: str   # "YYYY-12-31" — last date included in training
    test_from:   str   # "YYYY-01-01" — first date in test
    test_until:  str   # "YYYY-12-31" — last date in test


def generate_folds(start_year: int, end_year: int) -> list[Fold]:
    """
    Build expanding-window folds from start_year through end_year.
    Training always starts from the beginning of the dataset.
    Each fold tests one calendar year.
    """
    folds = []
    for test_year in range(start_year + 1, end_year + 1):
        folds.append(Fold(
            train_until = f"{test_year - 1}-12-31",
            test_from   = f"{test_year}-01-01",
            test_until  = f"{test_year}-12-31",
        ))
    return folds


def split_fold(rows: list[dict], fold: Fold, min_prior_fights: int = 3):
    """Filter rows into train / test for a single fold."""
    train, test = [], []
    for row in rows:
        date = row.get("event_date", "")
        n    = float(row.get("a_n_fights", 0) or 0)
        if date <= fold.train_until and n >= min_prior_fights:
            train.append(row)
        elif fold.test_from <= date <= fold.test_until:
            test.append(row)
    return train, test


def _mean(vals: list[float | None]) -> float | None:
    valid = [v for v in vals if v is not None]
    return round(sum(valid) / len(valid), 4) if valid else None


def run_fold(
    rows: list[dict],
    fold: Fold,
    model_name: str = "lr",
    cal_method: str = "isotonic",
    min_prior_fights: int = 3,
) -> dict:
    """Train and evaluate a single walk-forward fold. Returns metrics dict."""
    import numpy as np

    train_rows, test_rows = split_fold(rows, fold, min_prior_fights)
    result = {
        "fold":        f"{fold.test_from[:4]}",
        "train_until": fold.train_until,
        "test_from":   fold.test_from,
        "test_until":  fold.test_until,
        "n_train":     len(train_rows),
        "n_test":      len(test_rows),
        "skipped":     False,
        "skip_reason": None,
    }

    if len(train_rows) < MIN_TRAIN_ROWS:
        result["skipped"] = True
        result["skip_reason"] = f"Only {len(train_rows)} training rows (minimum {MIN_TRAIN_ROWS})"
        return result

    if len(test_rows) < MIN_TEST_ROWS:
        result["skipped"] = True
        result["skip_reason"] = f"Only {len(test_rows)} test rows (minimum {MIN_TEST_ROWS})"
        return result

    X_train, y_train = extract_features(train_rows, FEATURE_COLUMNS)
    X_test,  y_test  = extract_features(test_rows,  FEATURE_COLUMNS)

    # Reserve last CAL_HOLD_PCT of training for calibration
    cal_split = int(len(X_train) * (1 - CAL_HOLD_PCT))
    X_fit,  y_fit  = X_train[:cal_split], y_train[:cal_split]
    X_cal,  y_cal  = X_train[cal_split:], y_train[cal_split:]

    if len(set(y_fit)) < 2 or len(set(y_cal)) < 2:
        result["skipped"] = True
        result["skip_reason"] = "Single class in training or calibration split"
        return result

    models = _make_models() if model_name == "all" else {model_name: _make_lr() if model_name == "lr" else _make_models().get(model_name)}
    models = {k: v for k, v in models.items() if v is not None}

    fold_model_metrics = {}
    for name, model in models.items():
        try:
            model.fit(np.array(X_fit), np.array(y_fit))
            cal_model = calibrate(model, X_cal, y_cal, method=cal_method)
            metrics = evaluate(cal_model, X_test, y_test)
            fold_model_metrics[name] = metrics
        except Exception as exc:
            fold_model_metrics[name] = {"error": str(exc)}

    result["models"] = fold_model_metrics

    # Use the primary model's metrics as the fold summary
    primary = fold_model_metrics.get(model_name, fold_model_metrics.get("lr", {}))
    result.update({
        "brier_score":   primary.get("brier_score"),
        "log_loss":      primary.get("log_loss"),
        "cal_error":     primary.get("cal_error"),
        "roc_auc":       primary.get("roc_auc"),
        "hit_rate":      primary.get("hit_rate"),
    })
    return result


def aggregate_folds(fold_results: list[dict]) -> dict:
    """Aggregate metrics across all non-skipped folds."""
    active = [f for f in fold_results if not f.get("skipped")]
    if not active:
        return {"error": "All folds were skipped — insufficient data."}

    def agg(key: str) -> dict:
        vals = [f.get(key) for f in active if f.get(key) is not None]
        if not vals:
            return {"mean": None, "std": None, "min": None, "max": None}
        mn = sum(vals) / len(vals)
        std = math.sqrt(sum((v - mn) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
        return {
            "mean": round(mn, 4),
            "std":  round(std, 4),
            "min":  round(min(vals), 4),
            "max":  round(max(vals), 4),
        }

    return {
        "n_folds":     len(active),
        "n_skipped":   len(fold_results) - len(active),
        "brier_score": agg("brier_score"),
        "log_loss":    agg("log_loss"),
        "cal_error":   agg("cal_error"),
        "roc_auc":     agg("roc_auc"),
        "hit_rate":    agg("hit_rate"),
        "interpretation": _interpret(agg("brier_score")),
    }


def _interpret(brier_agg: dict) -> str:
    mn = brier_agg.get("mean")
    std = brier_agg.get("std")
    if mn is None:
        return "Insufficient data."
    if mn < 0.22:
        quality = "Good — meaningfully better than random (baseline 0.25)."
    elif mn < 0.245:
        quality = "Marginal — slightly better than random. More data needed."
    else:
        quality = "Poor — model performs close to random. Review features."
    stability = (
        f" Stable across folds (std={std:.4f})."
        if std is not None and std < 0.01
        else f" Unstable across folds (std={std:.4f}) — check for era effects."
        if std is not None
        else ""
    )
    return quality + stability


def run(
    start_year: int = 2019,
    end_year: int   = 2025,
    model_name: str = "lr",
    cal_method: str = "isotonic",
    save: bool      = False,
) -> dict:
    if not TRAINING_CSV.exists():
        print(f"[ERROR] Training data not found: {TRAINING_CSV}")
        print("        Run: python dataset_builder.py")
        return {}

    print(f"Loading training data ...")
    rows = load_csv(TRAINING_CSV)
    print(f"  {len(rows)} rows total.\n")

    folds = generate_folds(start_year, end_year)
    fold_results = []

    for fold in folds:
        print(f"Fold {fold.test_from[:4]}  "
              f"[train until {fold.train_until}  |  test {fold.test_from} → {fold.test_until}]")
        r = run_fold(rows, fold, model_name=model_name, cal_method=cal_method)

        if r.get("skipped"):
            print(f"  SKIPPED: {r['skip_reason']}")
        else:
            b  = r.get("brier_score", "—")
            ll = r.get("log_loss", "—")
            ce = r.get("cal_error", "—")
            hr = r.get("hit_rate", "—")
            print(f"  train={r['n_train']}  test={r['n_test']}  "
                  f"Brier={b}  LogLoss={ll}  CalErr={ce}  Hit={hr}%")

        fold_results.append(r)

    agg = aggregate_folds(fold_results)

    print(f"\n{'='*64}")
    print(f"  WALK-FORWARD SUMMARY  ({agg.get('n_folds', 0)} folds)")
    print(f"{'='*64}")
    for metric in ("brier_score", "log_loss", "cal_error", "roc_auc", "hit_rate"):
        m = agg.get(metric, {})
        if isinstance(m, dict) and m.get("mean") is not None:
            print(f"  {metric:<20} mean={m['mean']}  std={m['std']}  "
                  f"[{m['min']} – {m['max']}]")
    print(f"\n  {agg.get('interpretation', '')}")
    print(f"{'='*64}")

    output = {
        "config": {
            "start_year": start_year,
            "end_year":   end_year,
            "model":      model_name,
            "cal_method": cal_method,
        },
        "folds":     fold_results,
        "aggregate": agg,
    }

    if save:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
        print(f"\nSaved: {OUTPUT_PATH}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation for UFC model.")
    parser.add_argument("--start", type=int, default=2019,
                        help="First year used for training (default: 2019)")
    parser.add_argument("--end", type=int, default=2025,
                        help="Last test year (default: 2025)")
    parser.add_argument("--model", default="lr", choices=["lr", "rf", "xgb", "all"],
                        help="Model type to use in each fold (default: lr)")
    parser.add_argument("--calibration", default="isotonic",
                        choices=["isotonic", "sigmoid"])
    parser.add_argument("--save", action="store_true",
                        help="Save results JSON to models/walk_forward_results.json")
    args = parser.parse_args()
    run(start_year=args.start, end_year=args.end,
        model_name=args.model, cal_method=args.calibration, save=args.save)


if __name__ == "__main__":
    main()
