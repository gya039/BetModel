"""
feature_importance.py — Which features actually predict UFC fight outcomes?

Methods:
  1. Permutation importance (model-agnostic, reliable)
  2. SHAP values (if shap package installed — shows direction + magnitude)
  3. Coefficient inspection (logistic regression only)
  4. Temporal stability: importance computed over rolling windows

Usage:
    python feature_importance.py
    python feature_importance.py --top 15 --save-json
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

from features import FEATURE_COLUMNS
from model_trainer import load_model, load_csv, extract_features, train_test_split_temporal
from utils import DATA_PROC

OUTPUT_DIR = DATA_PROC.parent / "models"
TRAINING_CSV = DATA_PROC / "training_data.csv"


# ── Permutation importance ────────────────────────────────────────────────────

def permutation_importance(
    model,
    X: list[list[float]],
    y: list[int],
    feature_names: list[str],
    n_repeats: int = 10,
    seed: int = 42,
) -> list[dict]:
    """
    Measure importance of each feature by shuffling it and observing the
    drop in accuracy. Larger drop = more important feature.

    Model-agnostic: works with any sklearn-compatible model.
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    X_arr = np.array(X)
    y_arr = np.array(y)

    # Baseline score (Brier score on unshuffled data)
    base_probs = model.predict_proba(X_arr)[:, 1]
    baseline = float(np.mean((base_probs - y_arr) ** 2))

    results = []
    for col_idx, name in enumerate(feature_names):
        drops = []
        for _ in range(n_repeats):
            X_perm = X_arr.copy()
            rng.shuffle(X_perm[:, col_idx])
            probs = model.predict_proba(X_perm)[:, 1]
            score = float(np.mean((probs - y_arr) ** 2))
            drops.append(score - baseline)

        mean_drop = float(np.mean(drops))
        std_drop  = float(np.std(drops))
        results.append({
            "feature":    name,
            "importance": round(mean_drop, 6),
            "std":        round(std_drop, 6),
            "rank":       0,  # filled after sorting
        })

    results.sort(key=lambda r: r["importance"], reverse=True)
    for i, r in enumerate(results):
        r["rank"] = i + 1
    return results


# ── LR coefficient inspection ─────────────────────────────────────────────────

def lr_coefficients(model, feature_names: list[str]) -> list[dict] | None:
    """
    Extract and rank logistic regression coefficients.
    Only works for pipeline ending in LogisticRegression.
    """
    try:
        # Handle CalibratedClassifierCV wrapper
        inner = model
        if hasattr(model, "calibrated_classifiers_"):
            inner = model.calibrated_classifiers_[0].estimator
        if hasattr(inner, "named_steps"):
            lr = inner.named_steps.get("lr")
        elif hasattr(inner, "coef_"):
            lr = inner
        else:
            return None
        if not hasattr(lr, "coef_"):
            return None

        coefs = lr.coef_[0].tolist()
        results = [
            {
                "feature":     name,
                "coefficient": round(coef, 4),
                "abs_coef":    round(abs(coef), 4),
                "direction":   "favors_a" if coef > 0 else "favors_b",
            }
            for name, coef in zip(feature_names, coefs)
        ]
        results.sort(key=lambda r: r["abs_coef"], reverse=True)
        return results
    except Exception:
        return None


# ── SHAP values ───────────────────────────────────────────────────────────────

def shap_importance(model, X: list[list[float]], feature_names: list[str]) -> list[dict] | None:
    """
    SHAP feature importance. Requires the shap package.
    Falls back gracefully if not installed.
    """
    try:
        import shap
        import numpy as np

        X_arr = np.array(X)
        inner = model
        if hasattr(model, "calibrated_classifiers_"):
            inner = model.calibrated_classifiers_[0].estimator

        if hasattr(inner, "named_steps"):
            explainer = shap.LinearExplainer(inner.named_steps["lr"], X_arr)
        else:
            explainer = shap.TreeExplainer(inner)

        shap_values = explainer.shap_values(X_arr)
        if isinstance(shap_values, list):
            shap_values = shap_values[1]

        mean_abs = [float(abs(shap_values[:, i]).mean()) for i in range(len(feature_names))]
        results = [
            {"feature": name, "mean_abs_shap": round(v, 6)}
            for name, v in zip(feature_names, mean_abs)
        ]
        results.sort(key=lambda r: r["mean_abs_shap"], reverse=True)
        return results
    except ImportError:
        print("[INFO] shap not installed. Run: pip install shap")
        return None
    except Exception as exc:
        print(f"[WARN] SHAP failed: {exc}")
        return None


# ── Temporal stability ────────────────────────────────────────────────────────

def temporal_stability(
    rows: list[dict],
    feature_names: list[str],
    window_size: int = 200,
    step: int = 100,
) -> list[dict]:
    """
    Compute permutation importance over rolling time windows.
    Shows whether features remain predictive over time or decay.
    Each window = 'window_size' training rows.
    """
    from model_trainer import _make_lr
    import numpy as np

    windows = []
    n = len(rows)
    for start in range(0, max(1, n - window_size), step):
        window = rows[start: start + window_size]
        if len(window) < 50:
            continue

        X, y = extract_features(window, feature_names)
        X_arr = np.array(X)
        y_arr = np.array(y)

        try:
            model = _make_lr()
            model.fit(X_arr, y_arr)
            imp = permutation_importance(model, X, y, feature_names, n_repeats=3)
            date_range = (
                window[0].get("event_date", ""),
                window[-1].get("event_date", ""),
            )
            windows.append({
                "date_from":   date_range[0],
                "date_to":     date_range[1],
                "n_fights":    len(window),
                "top_features": [r["feature"] for r in imp[:5]],
                "importances": {r["feature"]: r["importance"] for r in imp},
            })
        except Exception as exc:
            print(f"[WARN] Window {start}:{start+window_size} failed: {exc}")
            continue

    return windows


# ── Main ──────────────────────────────────────────────────────────────────────

def run(top_n: int = 15, save_json: bool = False) -> dict:
    model, feature_cols = load_model()
    if model is None:
        print("No trained model found. Run:  python model_trainer.py")
        return {}

    print(f"Loading training data ...")
    rows = load_csv(TRAINING_CSV)
    _, test_rows = train_test_split_temporal(rows)
    # Use the full dataset for importance (more data = more stable estimates)
    all_rows = rows
    X_all, y_all = extract_features(all_rows, feature_cols)

    print(f"Computing permutation importance ({len(X_all)} samples) ...")
    perm = permutation_importance(model, X_all, y_all, feature_cols, n_repeats=10)

    print(f"\n{'='*55}")
    print(f"  PERMUTATION IMPORTANCE  (top {top_n})")
    print(f"{'='*55}")
    print(f"  {'Feature':<28} {'Drop in Brier':>13}")
    print(f"  {'-'*28} {'-'*13}")
    for r in perm[:top_n]:
        bar = "█" * int(r["importance"] * 500)
        print(f"  {r['feature']:<28} {r['importance']:>10.5f}  {bar}")

    lr_coefs = lr_coefficients(model, feature_cols)
    if lr_coefs:
        print(f"\n{'='*55}")
        print(f"  LR COEFFICIENTS (top {top_n})")
        print(f"{'='*55}")
        print(f"  {'Feature':<28} {'Coef':>8}  Direction")
        print(f"  {'-'*28} {'-'*8}  ---------")
        for r in lr_coefs[:top_n]:
            sign = "▲" if r["coefficient"] > 0 else "▼"
            print(f"  {r['feature']:<28} {r['coefficient']:>8.4f}  {sign} {r['direction']}")

    shap_imp = shap_importance(model, X_all, feature_cols)
    if shap_imp:
        print(f"\n  SHAP (top {top_n}):")
        for r in shap_imp[:top_n]:
            print(f"  {r['feature']:<28} {r['mean_abs_shap']:.5f}")

    print(f"\nComputing temporal stability ...")
    stability = temporal_stability(all_rows, feature_cols, window_size=200, step=100)
    if stability:
        print(f"\n  TEMPORAL STABILITY:")
        for w in stability[-3:]:  # show most recent windows
            print(f"  {w['date_from']} → {w['date_to']}: top={w['top_features'][:3]}")

    output = {
        "permutation_importance": perm,
        "lr_coefficients":        lr_coefs,
        "shap_importance":        shap_imp,
        "temporal_stability":     stability,
    }

    if save_json:
        out_path = OUTPUT_DIR / "feature_importance.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Convert to JSON-serializable (remove nested non-serializable objects)
        out_path.write_text(json.dumps({
            k: v for k, v in output.items() if v is not None
        }, indent=2, default=str))
        print(f"\nSaved: {out_path}")

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="UFC model feature importance analysis.")
    parser.add_argument("--top", type=int, default=15, help="Show top N features (default: 15)")
    parser.add_argument("--save-json", action="store_true", help="Save results to JSON")
    args = parser.parse_args()
    run(top_n=args.top, save_json=args.save_json)


if __name__ == "__main__":
    main()
