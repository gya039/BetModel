"""
feature_diagnostics.py — Feature correlation, redundancy, and signal analysis.

Runs three complementary diagnostics:
  1. Correlation matrix — detect redundant feature pairs (Pearson |r| > threshold)
  2. Variance Inflation Factor (VIF) — detect multicollinearity
  3. Mutual Information — rank features by actual predictive signal

Then offers an auto-pruning step that removes:
  - One feature from each highly-correlated pair (keep the one with higher MI)
  - Features with VIF above threshold (severe multicollinearity)
  - Features with MI below a minimum signal threshold

Usage:
    python feature_diagnostics.py
    python feature_diagnostics.py --corr-thresh 0.80 --vif-thresh 8 --mi-thresh 0.002
    python feature_diagnostics.py --prune --save
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from features import FEATURE_COLUMNS
from model_trainer import TRAINING_CSV, extract_features, load_csv
from utils import DATA_PROC

OUTPUT_PATH = DATA_PROC.parent / "models" / "feature_diagnostics.json"
PRUNED_COLS_PATH = DATA_PROC.parent / "models" / "pruned_feature_columns.json"

# Default thresholds
CORR_THRESH = 0.85    # flag pairs with |r| >= this
VIF_THRESH  = 10.0    # flag features with VIF >= this
MI_THRESH   = 0.001   # flag features with MI <= this (very low signal)


# ── Linear algebra helpers (pure Python / numpy) ──────────────────────────────

def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [list(row) for row in zip(*matrix)]


def _col_mean(X: list[list[float]], j: int) -> float:
    return sum(row[j] for row in X) / len(X)


def _col_std(X: list[list[float]], j: int, mean: float) -> float:
    n = len(X)
    return math.sqrt(sum((row[j] - mean) ** 2 for row in X) / (n - 1)) if n > 1 else 0.0


def correlation_matrix(X: list[list[float]], names: list[str]) -> list[dict]:
    """
    Compute pairwise Pearson correlations.
    Returns list of (feat_a, feat_b, r) for all pairs with |r| >= CORR_THRESH.
    """
    import numpy as np
    arr = np.array(X)
    n_cols = arr.shape[1]

    corr = np.corrcoef(arr, rowvar=False)

    flagged = []
    for i in range(n_cols):
        for j in range(i + 1, n_cols):
            r = float(corr[i, j])
            flagged.append({
                "feature_a": names[i],
                "feature_b": names[j],
                "r":         round(r, 4),
                "abs_r":     round(abs(r), 4),
                "flagged":   abs(r) >= CORR_THRESH,
            })

    flagged.sort(key=lambda x: x["abs_r"], reverse=True)
    return flagged


def vif_scores(X: list[list[float]], names: list[str]) -> list[dict]:
    """
    Compute Variance Inflation Factor for each feature.
    VIF_j = 1 / (1 - R²_j) where R²_j is from regressing feature j on all others.

    VIF = 1         → no multicollinearity
    VIF 1–5         → moderate (acceptable)
    VIF 5–10        → high (investigate)
    VIF > 10        → severe (remove or combine)
    """
    import numpy as np

    arr = np.array(X, dtype=float)
    n, p = arr.shape
    if n < p + 2:
        return [{"feature": name, "vif": None, "flagged": False} for name in names]

    results = []
    for j, name in enumerate(names):
        y_j = arr[:, j]
        X_rest = np.delete(arr, j, axis=1)
        # Add intercept
        X_i = np.column_stack([np.ones(n), X_rest])
        try:
            # OLS: R² = 1 - SS_res/SS_tot
            beta, _, _, _ = np.linalg.lstsq(X_i, y_j, rcond=None)
            y_hat = X_i @ beta
            ss_res = float(np.sum((y_j - y_hat) ** 2))
            ss_tot = float(np.sum((y_j - y_j.mean()) ** 2))
            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
            r2 = max(0.0, min(r2, 0.9999))
            vif = 1 / (1 - r2)
        except Exception:
            vif = None

        results.append({
            "feature": name,
            "vif":     round(vif, 2) if vif is not None else None,
            "flagged": vif is not None and vif >= VIF_THRESH,
        })

    results.sort(key=lambda r: (r["vif"] or 0), reverse=True)
    return results


def mutual_information(X: list[list[float]], y: list[int], names: list[str]) -> list[dict]:
    """
    Rank features by mutual information with the target (sklearn).
    Falls back to a variance-only proxy if sklearn is unavailable.
    """
    try:
        import numpy as np
        from sklearn.feature_selection import mutual_info_classif
        mi = mutual_info_classif(
            np.array(X), np.array(y), discrete_features=False, random_state=42
        )
        results = [
            {
                "feature": name,
                "mi":      round(float(mi[i]), 6),
                "flagged": float(mi[i]) <= MI_THRESH,
            }
            for i, name in enumerate(names)
        ]
    except ImportError:
        # Variance proxy: low variance → low signal
        results = []
        for j, name in enumerate(names):
            vals = [row[j] for row in X]
            mn = sum(vals) / len(vals)
            var = sum((v - mn) ** 2 for v in vals) / len(vals)
            results.append({
                "feature": name,
                "mi":      round(var, 6),  # variance as proxy
                "flagged": var < 1e-6,
                "note":    "variance proxy (sklearn not available)",
            })

    results.sort(key=lambda r: r["mi"], reverse=True)
    return results


def flag_unstable_features(
    fold_importances: list[dict[str, float]],
    names: list[str],
    cv_threshold: float = 1.5,
) -> list[dict]:
    """
    Given permutation importances across walk-forward folds, flag features
    where the coefficient of variation (std/mean) exceeds cv_threshold.
    These features are predictive in some periods but not others.
    """
    if not fold_importances:
        return []

    results = []
    for name in names:
        vals = [imp.get(name, 0.0) for imp in fold_importances]
        mn  = sum(vals) / len(vals)
        std = math.sqrt(sum((v - mn) ** 2 for v in vals) / len(vals)) if len(vals) > 1 else 0.0
        cv  = std / mn if mn > 1e-9 else float("inf")

        results.append({
            "feature":    name,
            "mean_imp":   round(mn, 6),
            "std_imp":    round(std, 6),
            "cv":         round(cv, 3),
            "unstable":   cv >= cv_threshold,
        })

    results.sort(key=lambda r: r["cv"], reverse=True)
    return results


# ── Auto-pruning ──────────────────────────────────────────────────────────────

def prune(
    names:       list[str],
    corr_pairs:  list[dict],
    vif_list:    list[dict],
    mi_list:     list[dict],
    corr_thresh: float = CORR_THRESH,
    vif_thresh:  float = VIF_THRESH,
    mi_thresh:   float = MI_THRESH,
) -> tuple[list[str], dict]:
    """
    Remove redundant / low-signal features.

    Strategy:
      1. Build MI lookup (higher MI = keep).
      2. For each correlated pair, remove the one with lower MI.
      3. Remove any feature with VIF >= vif_thresh (after corr pruning).
      4. Remove any remaining feature with MI <= mi_thresh.

    Returns (kept_features, pruning_log).
    """
    mi_lookup = {r["feature"]: r.get("mi", 0.0) for r in mi_list}
    to_remove: set[str] = set()
    log: list[str] = []

    # Step 1 — correlated pairs
    for pair in corr_pairs:
        if abs(pair["r"]) < corr_thresh:
            break  # sorted by abs_r descending
        a, b = pair["feature_a"], pair["feature_b"]
        if a in to_remove or b in to_remove:
            continue
        drop = b if mi_lookup.get(a, 0) >= mi_lookup.get(b, 0) else a
        to_remove.add(drop)
        log.append(f"CORR |r|={pair['abs_r']}: removed '{drop}' (lower MI than partner)")

    # Step 2 — VIF
    for item in vif_list:
        if item.get("vif") is None or item["vif"] < vif_thresh:
            continue
        name = item["feature"]
        if name not in to_remove:
            to_remove.add(name)
            log.append(f"VIF={item['vif']}: removed '{name}'")

    # Step 3 — low MI (only if not already removed)
    for item in mi_list:
        if item.get("mi", 1.0) > mi_thresh:
            continue
        name = item["feature"]
        if name not in to_remove:
            to_remove.add(name)
            log.append(f"MI={item.get('mi', 0):.6f}: removed '{name}' (low signal)")

    kept = [n for n in names if n not in to_remove]
    return kept, {"removed": sorted(to_remove), "kept": kept, "log": log}


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    corr_thresh: float = CORR_THRESH,
    vif_thresh:  float = VIF_THRESH,
    mi_thresh:   float = MI_THRESH,
    do_prune:    bool  = False,
    save:        bool  = False,
) -> dict:
    if not TRAINING_CSV.exists():
        print(f"[ERROR] {TRAINING_CSV} not found. Run: python dataset_builder.py")
        return {}

    print("Loading training data ...")
    rows = load_csv(TRAINING_CSV)
    X, y = extract_features(rows, FEATURE_COLUMNS)
    print(f"  {len(X)} rows, {len(FEATURE_COLUMNS)} features.\n")

    if len(X) < 20:
        print("Insufficient training data for diagnostics.")
        return {}

    print("Computing correlation matrix ...")
    corr = correlation_matrix(X, FEATURE_COLUMNS)
    flagged_corr = [p for p in corr if p["flagged"]]

    print(f"Computing VIF scores ...")
    vif  = vif_scores(X, FEATURE_COLUMNS)
    flagged_vif = [r for r in vif if r["flagged"]]

    print(f"Computing mutual information ...")
    mi   = mutual_information(X, y, FEATURE_COLUMNS)
    flagged_mi = [r for r in mi if r["flagged"]]

    # ── Print summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  FEATURE DIAGNOSTICS SUMMARY")
    print(f"{'='*60}")

    print(f"\n  Highly correlated pairs (|r| ≥ {corr_thresh}):")
    if flagged_corr:
        for p in flagged_corr[:10]:
            print(f"    {p['feature_a']:<26} ↔ {p['feature_b']:<26}  r={p['r']:+.3f}")
    else:
        print("    None found.")

    print(f"\n  High VIF features (VIF ≥ {vif_thresh}):")
    if flagged_vif:
        for r in flagged_vif:
            print(f"    {r['feature']:<30}  VIF={r['vif']}")
    else:
        print("    None found.")

    print(f"\n  Low mutual information features (MI ≤ {mi_thresh}):")
    if flagged_mi:
        for r in flagged_mi:
            print(f"    {r['feature']:<30}  MI={r.get('mi', 0):.6f}")
    else:
        print("    None found.")

    print(f"\n  Top 10 features by mutual information:")
    for r in mi[:10]:
        bar = "█" * int(r["mi"] * 3000)
        print(f"    {r['feature']:<28}  MI={r['mi']:.6f}  {bar}")

    pruning_result = {}
    kept_cols = FEATURE_COLUMNS

    if do_prune:
        kept_cols, pruning_result = prune(
            FEATURE_COLUMNS, corr, vif, mi,
            corr_thresh=corr_thresh, vif_thresh=vif_thresh, mi_thresh=mi_thresh,
        )
        print(f"\n  AUTO-PRUNING:")
        for line in pruning_result.get("log", []):
            print(f"    {line}")
        print(f"\n  Kept {len(kept_cols)}/{len(FEATURE_COLUMNS)} features.")

        if save:
            PRUNED_COLS_PATH.parent.mkdir(parents=True, exist_ok=True)
            PRUNED_COLS_PATH.write_text(json.dumps(kept_cols, indent=2))
            print(f"  Saved pruned columns: {PRUNED_COLS_PATH}")

    output = {
        "n_features":          len(FEATURE_COLUMNS),
        "n_training_rows":     len(X),
        "corr_thresh":         corr_thresh,
        "vif_thresh":          vif_thresh,
        "mi_thresh":           mi_thresh,
        "flagged_corr_pairs":  flagged_corr,
        "vif_scores":          vif,
        "mutual_information":  mi,
        "pruning":             pruning_result,
        "kept_columns":        kept_cols,
    }

    if save:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
        print(f"\nDiagnostics saved: {OUTPUT_PATH}")

    return output


def load_pruned_columns() -> list[str]:
    """Return pruned column list if it exists, else full FEATURE_COLUMNS."""
    if PRUNED_COLS_PATH.exists():
        try:
            cols = json.loads(PRUNED_COLS_PATH.read_text())
            print(f"[FeatureDiag] Using {len(cols)} pruned columns (from {PRUNED_COLS_PATH.name})")
            return cols
        except Exception:
            pass
    return FEATURE_COLUMNS


def main() -> None:
    parser = argparse.ArgumentParser(description="UFC model feature diagnostics.")
    parser.add_argument("--corr-thresh", type=float, default=CORR_THRESH)
    parser.add_argument("--vif-thresh",  type=float, default=VIF_THRESH)
    parser.add_argument("--mi-thresh",   type=float, default=MI_THRESH)
    parser.add_argument("--prune",  action="store_true", help="Run auto-pruning")
    parser.add_argument("--save",   action="store_true", help="Save results to JSON")
    args = parser.parse_args()
    run(
        corr_thresh=args.corr_thresh,
        vif_thresh=args.vif_thresh,
        mi_thresh=args.mi_thresh,
        do_prune=args.prune,
        save=args.save,
    )


if __name__ == "__main__":
    main()
