"""
model_trainer.py — Train, calibrate, and evaluate UFC prediction models.

Pipeline:
  1. Load training_data.csv (built by dataset_builder.py)
  2. Time-based train/test split (no random leakage)
  3. Train: LogisticRegression, RandomForest, XGBoost (if available)
  4. Calibrate each with isotonic regression (CalibratedClassifierCV)
  5. Evaluate: Brier score, log loss, calibration error, ROC-AUC
  6. Save best model to mma/models/best_model.pkl
  7. Save metadata: feature list, training window, performance

Usage:
    python model_trainer.py
    python model_trainer.py --test-from 2024-01-01 --min-fights 3
    python model_trainer.py --model lr   (train only logistic regression)
    python model_trainer.py --tune       (grid-search regularization hyperparameters)

The saved model is loaded automatically by betting_model.py for inference.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pickle
import warnings
from datetime import date
from pathlib import Path

from features import FEATURE_COLUMNS
from utils import DATA_PROC

warnings.filterwarnings("ignore")

MODELS_DIR   = DATA_PROC.parent / "models"
TRAINING_CSV = DATA_PROC / "training_data.csv"
MODEL_PATH   = MODELS_DIR / "best_model.pkl"
META_PATH    = MODELS_DIR / "model_meta.json"

# Time-based split: everything before this date is training, after is test
DEFAULT_TEST_FROM = "2024-01-01"

# Minimum prior fights before a row is trustworthy for training
MIN_PRIOR_FIGHTS = 3


# ── Data loading ──────────────────────────────────────────────────────────────

def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(
            f"Training data not found: {path}\n"
            "Run:  python dataset_builder.py"
        )
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _f(val: str | None, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def extract_features(rows: list[dict], feature_cols: list[str]) -> tuple[list[list[float]], list[int]]:
    """Extract X (feature matrix) and y (targets) from training rows."""
    X, y = [], []
    for row in rows:
        try:
            x = [_f(row.get(col)) for col in feature_cols]
            target = int(float(row.get("target", 0.5)))
            X.append(x)
            y.append(target)
        except Exception:
            continue
    return X, y


def train_test_split_temporal(
    rows: list[dict],
    test_from: str = DEFAULT_TEST_FROM,
    min_prior_fights: int = MIN_PRIOR_FIGHTS,
) -> tuple[list[dict], list[dict]]:
    """
    Split by date — training is everything before test_from,
    test is everything from test_from onwards.
    Rows with too few prior fights are excluded from training (but not test).
    """
    train, test = [], []
    for row in rows:
        event_date = row.get("event_date", "")
        n_fights = _f(row.get("a_n_fights", 0))
        is_test = event_date >= test_from

        if is_test:
            test.append(row)
        elif n_fights >= min_prior_fights:
            train.append(row)

    return train, test


# ── Model definitions ─────────────────────────────────────────────────────────

def _make_lr():
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    return Pipeline([
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(C=0.5, max_iter=1000, solver="lbfgs")),
    ])


def _make_rf():
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=10,
        random_state=42, n_jobs=-1,
    )


def _make_xgb():
    try:
        import xgboost as xgb
        return xgb.XGBClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            min_child_weight=5, use_label_encoder=False,
            eval_metric="logloss", random_state=42,
        )
    except ImportError:
        return None


def _make_models() -> dict:
    models = {"lr": _make_lr(), "rf": _make_rf()}
    xgb = _make_xgb()
    if xgb is not None:
        models["xgb"] = xgb
    return models


# ── Calibration ───────────────────────────────────────────────────────────────

def calibrate(base_model, X_cal: list[list[float]], y_cal: list[int], method: str = "isotonic"):
    """
    Wrap a trained model in isotonic or sigmoid (Platt) calibration.
    Calibration corrects for over/under-confidence in raw ML probabilities.
    """
    from sklearn.calibration import CalibratedClassifierCV
    cal = CalibratedClassifierCV(base_model, method=method, cv="prefit")
    import numpy as np
    cal.fit(np.array(X_cal), np.array(y_cal))
    return cal


# ── Evaluation metrics ────────────────────────────────────────────────────────

def _safe_log(p: float, eps: float = 1e-15) -> float:
    return math.log(max(eps, min(1 - eps, p)))


def evaluate(model, X_test: list[list[float]], y_test: list[int]) -> dict:
    """
    Compute Brier score, log loss, calibration error, and hit rate.
    """
    import numpy as np
    probs = model.predict_proba(np.array(X_test))[:, 1].tolist()

    brier = sum((p - y) ** 2 for p, y in zip(probs, y_test)) / len(probs)
    logloss = -sum(
        _safe_log(p) if y == 1 else _safe_log(1 - p)
        for p, y in zip(probs, y_test)
    ) / len(probs)

    # Calibration error across 5-point buckets
    bucket_width = 0.10
    buckets: dict[float, list] = {}
    for p, y in zip(probs, y_test):
        mid = round(math.floor(p / bucket_width) * bucket_width + bucket_width / 2, 2)
        buckets.setdefault(mid, []).append((p, y))

    cal_errors = []
    for mid, pairs in buckets.items():
        obs = sum(y for _, y in pairs) / len(pairs)
        cal_errors.append(abs(mid - obs) * len(pairs))
    mce = sum(cal_errors) / len(probs) if probs else 0.0

    # ROC-AUC (simple trapezoid)
    try:
        from sklearn.metrics import roc_auc_score
        auc = roc_auc_score(y_test, probs)
    except Exception:
        auc = None

    hit_rate = sum(
        1 for p, y in zip(probs, y_test) if (p >= 0.5) == (y == 1)
    ) / len(probs)

    return {
        "n_test":         len(probs),
        "brier_score":    round(brier, 4),
        "log_loss":       round(logloss, 4),
        "cal_error":      round(mce, 4),
        "roc_auc":        round(auc, 4) if auc else None,
        "hit_rate":       round(hit_rate * 100, 1),
        "prob_mean":      round(sum(probs) / len(probs), 4),
        "prob_min":       round(min(probs), 4),
        "prob_max":       round(max(probs), 4),
    }


# ── Training pipeline ─────────────────────────────────────────────────────────

def train_all(
    training_csv: Path = TRAINING_CSV,
    test_from: str = DEFAULT_TEST_FROM,
    min_prior_fights: int = MIN_PRIOR_FIGHTS,
    which: str | None = None,     # None = all, "lr"/"rf"/"xgb" = specific
    cal_method: str = "isotonic",
) -> dict:
    """
    Full training pipeline. Returns dict with trained models and evaluation metrics.
    """
    print(f"Loading training data: {training_csv}")
    rows = load_csv(training_csv)
    print(f"  {len(rows)} total rows loaded.")

    train_rows, test_rows = train_test_split_temporal(rows, test_from, min_prior_fights)
    print(f"  Train: {len(train_rows)} rows (before {test_from})")
    print(f"  Test:  {len(test_rows)} rows (from {test_from})")

    if len(train_rows) < 50:
        raise ValueError(
            f"Only {len(train_rows)} training rows. Need at least 50. "
            "Run more event settlements to build history, or adjust --test-from."
        )

    import numpy as np
    X_train, y_train = extract_features(train_rows, FEATURE_COLUMNS)
    X_test,  y_test  = extract_features(test_rows, FEATURE_COLUMNS)

    # Reserve 20% of train for calibration (further time-ordered split)
    cal_split = int(len(X_train) * 0.8)
    X_fit,  y_fit  = X_train[:cal_split], y_train[:cal_split]
    X_cal,  y_cal  = X_train[cal_split:], y_train[cal_split:]

    models_def = _make_models_tuned()
    if which:
        models_def = {k: v for k, v in models_def.items() if k == which}

    results: dict[str, dict] = {}

    for name, model in models_def.items():
        if model is None:
            continue
        print(f"\nTraining {name.upper()} ...")
        model.fit(np.array(X_fit), np.array(y_fit))

        print(f"  Calibrating ({cal_method}) ...")
        cal_model = calibrate(model, X_cal, y_cal, method=cal_method)

        if X_test:
            print(f"  Evaluating on test set ...")
            metrics = evaluate(cal_model, X_test, y_test)
            print(f"  Brier: {metrics['brier_score']}  |  LogLoss: {metrics['log_loss']}  |  "
                  f"CalErr: {metrics['cal_error']}  |  AUC: {metrics.get('roc_auc')}")
        else:
            metrics = {}

        results[name] = {"model": cal_model, "metrics": metrics}

    return {
        "results":      results,
        "feature_cols": FEATURE_COLUMNS,
        "train_rows":   len(train_rows),
        "test_rows":    len(test_rows),
        "test_from":    test_from,
        "cal_method":   cal_method,
    }


# ── Regularization grid search ────────────────────────────────────────────────

# LR C values to try (smaller = stronger L2 regularization)
LR_C_GRID       = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
# RF hyperparameter grid
RF_DEPTH_GRID   = [4, 5, 6, 8]
RF_LEAF_GRID    = [5, 10, 15, 20]
# XGBoost grid
XGB_DEPTH_GRID  = [3, 4, 5]
XGB_LR_GRID     = [0.03, 0.05, 0.10]


def tune_regularization(
    X_fit:  list[list[float]],
    y_fit:  list[int],
    X_cal:  list[list[float]],
    y_cal:  list[int],
    X_val:  list[list[float]],
    y_val:  list[int],
    model_name: str = "lr",
    cal_method:  str = "isotonic",
    verbose: bool = True,
) -> dict:
    """
    Grid-search regularization hyperparameters for a single model type.
    Uses time-ordered splits (X_fit→train, X_cal→calibration, X_val→evaluation).
    Returns the best hyperparameter dict by Brier score.
    """
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline

    if verbose:
        print(f"\nTuning {model_name.upper()} regularization ...")

    best_brier = float("inf")
    best_params: dict = {}
    all_results: list[dict] = []

    def _fit_and_eval(model) -> float:
        model.fit(np.array(X_fit), np.array(y_fit))
        cal_model = calibrate(model, X_cal, y_cal, method=cal_method)
        metrics   = evaluate(cal_model, X_val, y_val)
        return metrics.get("brier_score", 1.0), cal_model, metrics

    if model_name == "lr":
        for c in LR_C_GRID:
            model = Pipeline([
                ("scaler", StandardScaler()),
                ("lr", LogisticRegression(C=c, max_iter=1000, solver="lbfgs")),
            ])
            try:
                brier, cal, metrics = _fit_and_eval(model)
                row = {"C": c, "brier": brier, "log_loss": metrics.get("log_loss"),
                       "cal_error": metrics.get("cal_error")}
                all_results.append(row)
                if verbose:
                    print(f"  LR  C={c:5.2f}  Brier={brier:.4f}  LL={metrics.get('log_loss'):.4f}")
                if brier < best_brier:
                    best_brier = brier
                    best_params = {"C": c}
            except Exception as exc:
                if verbose:
                    print(f"  LR  C={c}  ERROR: {exc}")

    elif model_name == "rf":
        from sklearn.ensemble import RandomForestClassifier
        for depth in RF_DEPTH_GRID:
            for leaf in RF_LEAF_GRID:
                model = RandomForestClassifier(
                    n_estimators=200, max_depth=depth,
                    min_samples_leaf=leaf, random_state=42, n_jobs=-1,
                )
                try:
                    brier, cal, metrics = _fit_and_eval(model)
                    row = {"max_depth": depth, "min_samples_leaf": leaf, "brier": brier,
                           "log_loss": metrics.get("log_loss")}
                    all_results.append(row)
                    if verbose:
                        print(f"  RF  depth={depth}  leaf={leaf:2d}  Brier={brier:.4f}")
                    if brier < best_brier:
                        best_brier = brier
                        best_params = {"max_depth": depth, "min_samples_leaf": leaf}
                except Exception as exc:
                    if verbose:
                        print(f"  RF  depth={depth}  leaf={leaf}  ERROR: {exc}")

    elif model_name == "xgb":
        try:
            import xgboost as xgb
        except ImportError:
            print("  XGBoost not installed — skipping tuning.")
            return {"best_params": {}, "best_brier": None, "all_results": []}

        for depth in XGB_DEPTH_GRID:
            for lr_xgb in XGB_LR_GRID:
                model = xgb.XGBClassifier(
                    n_estimators=200, max_depth=depth,
                    learning_rate=lr_xgb, subsample=0.8, colsample_bytree=0.8,
                    min_child_weight=5, use_label_encoder=False,
                    eval_metric="logloss", random_state=42,
                )
                try:
                    brier, cal, metrics = _fit_and_eval(model)
                    row = {"max_depth": depth, "learning_rate": lr_xgb, "brier": brier}
                    all_results.append(row)
                    if verbose:
                        print(f"  XGB  depth={depth}  lr={lr_xgb}  Brier={brier:.4f}")
                    if brier < best_brier:
                        best_brier = brier
                        best_params = {"max_depth": depth, "learning_rate": lr_xgb}
                except Exception as exc:
                    if verbose:
                        print(f"  XGB  depth={depth}  lr={lr_xgb}  ERROR: {exc}")

    if verbose and best_params:
        print(f"\n  Best {model_name.upper()}: {best_params}  Brier={best_brier:.4f}")

    return {
        "model_name":  model_name,
        "best_params": best_params,
        "best_brier":  round(best_brier, 4) if best_brier < float("inf") else None,
        "all_results": all_results,
    }


def tune_all_models(
    rows: list[dict],
    test_from: str = DEFAULT_TEST_FROM,
    min_prior_fights: int = MIN_PRIOR_FIGHTS,
    cal_method: str = "isotonic",
) -> dict:
    """
    Run regularization grid search for all model types.
    Splits: first 60% → fit, next 20% → calibration, last 20% → validation.
    Returns best params per model and saves to models/best_hyperparams.json.
    """
    import numpy as np

    train_rows, _ = train_test_split_temporal(rows, test_from, min_prior_fights)
    if len(train_rows) < 60:
        print("[Tune] Insufficient training rows for hyperparameter search.")
        return {}

    X_all, y_all = extract_features(train_rows, FEATURE_COLUMNS)
    n = len(X_all)
    fit_end = int(n * 0.60)
    cal_end = int(n * 0.80)

    X_fit, y_fit = X_all[:fit_end],  y_all[:fit_end]
    X_cal, y_cal = X_all[fit_end:cal_end], y_all[fit_end:cal_end]
    X_val, y_val = X_all[cal_end:],  y_all[cal_end:]

    if len(set(y_fit)) < 2 or len(set(y_cal)) < 2 or len(set(y_val)) < 2:
        print("[Tune] Single class in one of the splits — skipping tuning.")
        return {}

    all_best: dict = {}
    for model_name in ["lr", "rf", "xgb"]:
        result = tune_regularization(
            X_fit, y_fit, X_cal, y_cal, X_val, y_val,
            model_name=model_name, cal_method=cal_method,
        )
        all_best[model_name] = result

    out_path = MODELS_DIR / "best_hyperparams.json"
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_best, indent=2, default=str))
    print(f"\nHyperparameter search complete. Saved: {out_path}")

    return all_best


def load_best_hyperparams() -> dict[str, dict]:
    """Load tuned hyperparameters if available, else return empty dict."""
    hp_path = MODELS_DIR / "best_hyperparams.json"
    if not hp_path.exists():
        return {}
    try:
        data = json.loads(hp_path.read_text())
        return {k: v.get("best_params", {}) for k, v in data.items()}
    except Exception:
        return {}


def _make_lr_tuned(C: float | None = None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    c = C if C is not None else 0.5
    return Pipeline([
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(C=c, max_iter=1000, solver="lbfgs")),
    ])


def _make_rf_tuned(max_depth: int | None = None, min_samples_leaf: int | None = None):
    from sklearn.ensemble import RandomForestClassifier
    return RandomForestClassifier(
        n_estimators=200,
        max_depth=max_depth if max_depth is not None else 6,
        min_samples_leaf=min_samples_leaf if min_samples_leaf is not None else 10,
        random_state=42, n_jobs=-1,
    )


def _make_xgb_tuned(max_depth: int | None = None, learning_rate: float | None = None):
    try:
        import xgboost as xgb
        return xgb.XGBClassifier(
            n_estimators=200,
            max_depth=max_depth if max_depth is not None else 4,
            learning_rate=learning_rate if learning_rate is not None else 0.05,
            subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
            use_label_encoder=False, eval_metric="logloss", random_state=42,
        )
    except ImportError:
        return None


def _make_models_tuned() -> dict:
    """Build models using tuned hyperparameters if available."""
    hp = load_best_hyperparams()
    models = {
        "lr": _make_lr_tuned(**hp.get("lr", {})),
        "rf": _make_rf_tuned(**hp.get("rf", {})),
    }
    xgb = _make_xgb_tuned(**hp.get("xgb", {}))
    if xgb is not None:
        models["xgb"] = xgb
    return models


def select_best_model(results: dict) -> tuple[str, object]:
    """
    Pick the model with the lowest Brier score on the test set.
    Falls back to LR if metrics are unavailable.
    """
    best_name = None
    best_brier = float("inf")
    for name, info in results.items():
        b = info.get("metrics", {}).get("brier_score", float("inf"))
        if b < best_brier:
            best_brier = b
            best_name = name
    if best_name is None:
        best_name = list(results.keys())[0]
    return best_name, results[best_name]["model"]


def save_model(model, name: str, metrics: dict, feature_cols: list[str],
               train_rows: int, test_rows: int, test_from: str) -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    with MODEL_PATH.open("wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols}, f)

    meta = {
        "model_name":    name,
        "feature_cols":  feature_cols,
        "test_from":     test_from,
        "train_rows":    train_rows,
        "test_rows":     test_rows,
        "metrics":       metrics,
    }
    META_PATH.write_text(json.dumps(meta, indent=2))

    print(f"\nSaved: {MODEL_PATH}")
    print(f"Meta:  {META_PATH}")


def load_model() -> tuple[object, list[str]] | tuple[None, None]:
    """Load saved model and feature column list. Returns (None, None) if not found."""
    if not MODEL_PATH.exists():
        return None, None
    try:
        with MODEL_PATH.open("rb") as f:
            payload = pickle.load(f)
        return payload["model"], payload["feature_cols"]
    except Exception as exc:
        print(f"[WARN] Could not load model: {exc}")
        return None, None


def predict_proba(model, feature_cols: list[str], feat_a: dict, feat_b: dict) -> float | None:
    """
    Predict P(fighter_a wins) using the trained model.
    feat_a and feat_b are output dicts from features.compute_fighter_features().
    """
    try:
        from features import compute_matchup_features
        import numpy as np
        matchup = compute_matchup_features(feat_a, feat_b)
        x = [matchup.get(col, 0.0) for col in feature_cols]
        prob = model.predict_proba(np.array([x]))[0][1]
        return float(prob)
    except Exception as exc:
        print(f"[WARN] Model inference failed: {exc}")
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Train UFC betting model.")
    parser.add_argument("--training-csv", type=Path, default=TRAINING_CSV)
    parser.add_argument("--test-from", default=DEFAULT_TEST_FROM,
                        help="ISO date: test split starts here (default: 2024-01-01)")
    parser.add_argument("--min-fights", type=int, default=MIN_PRIOR_FIGHTS,
                        help="Min prior fights before a row is used for training (default: 3)")
    parser.add_argument("--model", choices=["lr", "rf", "xgb"], default=None,
                        help="Train only this model (default: all)")
    parser.add_argument("--calibration", choices=["isotonic", "sigmoid"], default="isotonic",
                        help="Calibration method (default: isotonic)")
    parser.add_argument("--tune", action="store_true",
                        help="Grid-search regularization hyperparameters before training")
    args = parser.parse_args()

    if args.tune:
        print("Running regularization grid search ...")
        rows = load_csv(args.training_csv)
        tune_all_models(rows, test_from=args.test_from, min_prior_fights=args.min_fights,
                        cal_method=args.calibration)
        print("\nRe-training with tuned hyperparameters ...")

    pipeline = train_all(
        training_csv=args.training_csv,
        test_from=args.test_from,
        min_prior_fights=args.min_fights,
        which=args.model,
        cal_method=args.calibration,
    )

    best_name, best_model = select_best_model(pipeline["results"])
    best_metrics = pipeline["results"][best_name].get("metrics", {})

    print(f"\nBest model: {best_name.upper()} (Brier: {best_metrics.get('brier_score', 'N/A')})")
    save_model(
        model=best_model,
        name=best_name,
        metrics=best_metrics,
        feature_cols=pipeline["feature_cols"],
        train_rows=pipeline["train_rows"],
        test_rows=pipeline["test_rows"],
        test_from=pipeline["test_from"],
    )

    print("\n" + "=" * 60)
    print(f"  MODEL: {best_name.upper()}")
    for k, v in best_metrics.items():
        print(f"  {k:20s}: {v}")
    print("=" * 60)
    print("\nNext steps:")
    print("  python feature_importance.py    # see which features matter")
    print("  python bet_optimizer.py         # find optimal thresholds")
    print("  python market_analysis.py       # compare model vs market")


if __name__ == "__main__":
    main()
