"""
ensemble.py — Adaptive weighted ensemble for UFC fight probability prediction.

Replaces the static 70/30 ML/heuristic blend with a dynamic system that:
  - Weights each component by its recent Brier score performance
  - Updates weights after each event's settlement
  - Supports division-specific weight overrides
  - Degrades gracefully if any component fails

Components:
  - "ml"        → trained sklearn model (LR / RF / XGBoost via model_trainer)
  - "elo"       → Elo-rating-based probability
  - "heuristic" → original score-differential sigmoid
  - "market"    → market implied probability (if available)

Weights are stored in models/ensemble_weights.json and updated automatically.
If that file doesn't exist, equal weights are used.

Usage from code:
    from ensemble import EnsemblePredictor
    pred = EnsemblePredictor()
    prob_a = pred.predict(feat_a, feat_b, market_implied_a, division)

Update weights after settlement:
    pred.update_weights_from_results(results: list[dict])
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from utils import DATA_PROC

WEIGHTS_PATH = DATA_PROC.parent / "models" / "ensemble_weights.json"

# Component names
COMPONENTS = ["ml", "elo", "heuristic", "market"]

# Default weights when no history is available
DEFAULT_WEIGHTS: dict[str, float] = {
    "ml":        0.50,
    "elo":       0.20,
    "heuristic": 0.15,
    "market":    0.15,
}

# Weight update learning rate: 0.1 = 10% update per event
LEARNING_RATE = 0.10

# Smoothing: never allow any component to drop below MIN_WEIGHT
MIN_WEIGHT = 0.05

# Elo → probability conversion: logistic function on Elo gap
ELO_SCALE = 400.0   # standard Elo scale


def elo_to_prob(elo_a: float, elo_b: float) -> float:
    """Convert Elo ratings to win probability for fighter A."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / ELO_SCALE))


def _clamp(v: float, lo: float = 0.25, hi: float = 0.75) -> float:
    return max(lo, min(hi, v))


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    """Ensure weights sum to 1.0, all >= MIN_WEIGHT."""
    floored = {k: max(MIN_WEIGHT, v) for k, v in weights.items()}
    total = sum(floored.values())
    return {k: round(v / total, 4) for k, v in floored.items()}


# ── Weight persistence ────────────────────────────────────────────────────────

def load_weights(division: str | None = None) -> dict[str, float]:
    """
    Load ensemble weights from disk.
    If division-specific weights exist, they override the global weights.
    Falls back to DEFAULT_WEIGHTS if file not found.
    """
    if not WEIGHTS_PATH.exists():
        return dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(WEIGHTS_PATH.read_text())
        global_w = data.get("global", DEFAULT_WEIGHTS)
        if division:
            div_w = data.get("by_division", {}).get(division)
            if div_w:
                return _normalize(div_w)
        return _normalize(global_w)
    except Exception:
        return dict(DEFAULT_WEIGHTS)


def save_weights(
    weights: dict[str, float],
    division: str | None = None,
    metrics: dict | None = None,
) -> None:
    """Save updated weights to disk, preserving existing division-specific entries."""
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if WEIGHTS_PATH.exists():
        try:
            data = json.loads(WEIGHTS_PATH.read_text())
        except Exception:
            pass

    if division:
        data.setdefault("by_division", {})[division] = weights
    else:
        data["global"] = weights

    if metrics:
        data.setdefault("update_history", []).append(metrics)
        data["update_history"] = data["update_history"][-20:]  # keep last 20

    WEIGHTS_PATH.write_text(json.dumps(data, indent=2, default=str))


def update_weights(
    current_weights: dict[str, float],
    component_briers: dict[str, float | None],
) -> dict[str, float]:
    """
    Update component weights based on recent Brier scores.

    Lower Brier → better performance → higher weight.
    Uses exponential scoring: weight_new = weight_old * (1 - lr) + score * lr
    where score = 1 / brier (inverse — lower Brier is better).
    """
    scores: dict[str, float] = {}
    for name in COMPONENTS:
        b = component_briers.get(name)
        if b is not None and b > 0:
            scores[name] = 1.0 / b  # inverse Brier
        else:
            # No data → keep current weight (neutral update)
            scores[name] = 1.0 / 0.25  # random baseline as fallback

    # Normalize scores to [0, 1] range
    total_score = sum(scores.values())
    norm_scores = {k: v / total_score for k, v in scores.items()}

    # Blend old weights with new performance scores
    new_weights = {
        k: (1 - LEARNING_RATE) * current_weights.get(k, DEFAULT_WEIGHTS.get(k, MIN_WEIGHT))
           + LEARNING_RATE * norm_scores.get(k, MIN_WEIGHT)
        for k in COMPONENTS
    }
    return _normalize(new_weights)


# ── Ensemble predictor ────────────────────────────────────────────────────────

class EnsemblePredictor:
    """
    Combines multiple model components into a single calibrated probability.

    Call predict() for each fight. Call update_weights_from_results() after
    each event settles to keep the ensemble self-correcting.
    """

    def __init__(self) -> None:
        self._ml_model = None
        self._ml_cols  = None
        self._loaded   = False

    def _ensure_ml(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            from model_trainer import load_model
            self._ml_model, self._ml_cols = load_model()
        except Exception:
            pass

    def _ml_prob(self, feat_a: dict, feat_b: dict) -> float | None:
        self._ensure_ml()
        if self._ml_model is None:
            return None
        try:
            from model_trainer import predict_proba
            return predict_proba(self._ml_model, self._ml_cols, feat_a, feat_b)
        except Exception:
            return None

    def _elo_prob(self, feat_a: dict, feat_b: dict) -> float | None:
        elo_a = feat_a.get("elo")
        elo_b = feat_b.get("elo")
        if elo_a is None or elo_b is None:
            return None
        return elo_to_prob(float(elo_a), float(elo_b))

    def _heuristic_prob(self, fighter_a: dict, fighter_b: dict) -> float | None:
        """Score-differential sigmoid (original heuristic)."""
        try:
            from betting_model import stat_rating, matchup_adjustment
            import math
            sa = stat_rating(fighter_a) + matchup_adjustment(fighter_a, fighter_b)
            sb = stat_rating(fighter_b) + matchup_adjustment(fighter_b, fighter_a)
            raw = 1 / (1 + math.exp(-(sa - sb) / 12))
            return _clamp(raw)
        except Exception:
            return None

    def predict(
        self,
        feat_a:          dict,
        feat_b:          dict,
        fighter_a:       dict | None = None,
        fighter_b:       dict | None = None,
        market_implied_a: float | None = None,
        division:        str | None = None,
    ) -> dict:
        """
        Compute ensemble probability for fighter A winning.

        Returns dict with:
          - prob: final blended probability
          - components: individual predictions per model
          - weights: weights applied
          - missing: components that returned None
        """
        weights = load_weights(division)

        components: dict[str, float | None] = {
            "ml":        self._ml_prob(feat_a, feat_b),
            "elo":       self._elo_prob(feat_a, feat_b),
            "heuristic": self._heuristic_prob(fighter_a, fighter_b) if fighter_a else None,
            "market":    _clamp(market_implied_a) if market_implied_a else None,
        }

        missing = [k for k, v in components.items() if v is None]
        available = {k: v for k, v in components.items() if v is not None}

        if not available:
            return {"prob": 0.5, "components": {}, "weights": weights, "missing": missing}

        # Re-normalize weights to only available components
        avail_weights = {k: weights.get(k, MIN_WEIGHT) for k in available}
        total_w = sum(avail_weights.values())
        norm_w  = {k: v / total_w for k, v in avail_weights.items()}

        prob = sum(norm_w[k] * available[k] for k in available)
        prob = _clamp(prob)

        return {
            "prob":       round(prob, 4),
            "components": {k: round(v, 4) for k, v in available.items()},
            "weights":    {k: round(v, 4) for k, v in norm_w.items()},
            "missing":    missing,
        }

    def update_weights_from_results(
        self,
        results: list[dict],
        division: str | None = None,
    ) -> dict[str, float]:
        """
        Compute recent Brier scores per component from settled predictions.

        results: list of dicts with keys:
          - component_probs: {component_name: probability}
          - outcome: 1 or 0
        """
        component_briers: dict[str, list[float]] = {k: [] for k in COMPONENTS}

        for r in results:
            outcome = r.get("outcome")
            if outcome is None:
                continue
            probs = r.get("component_probs", {})
            for name, p in probs.items():
                if p is not None:
                    component_briers[name].append((float(p) - float(outcome)) ** 2)

        brier_per_comp: dict[str, float | None] = {
            k: sum(v) / len(v) if v else None
            for k, v in component_briers.items()
        }

        current = load_weights(division)
        new_weights = update_weights(current, brier_per_comp)
        save_weights(
            new_weights,
            division=division,
            metrics={"brier_per_component": brier_per_comp, "n_results": len(results)},
        )
        return new_weights


# ── Singleton convenience ─────────────────────────────────────────────────────
_predictor: EnsemblePredictor | None = None


def get_predictor() -> EnsemblePredictor:
    global _predictor
    if _predictor is None:
        _predictor = EnsemblePredictor()
    return _predictor
