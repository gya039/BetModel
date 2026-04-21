"""
value_engine.py - odds conversion and value classification for Octagon IQ.
"""
from __future__ import annotations


def american_to_implied(odds) -> float | None:
    """Convert American odds to implied probability as a 0-1 float."""
    try:
        price = int(float(odds))
    except (TypeError, ValueError):
        return None
    if price == 0:
        return None
    if price > 0:
        return round(100 / (price + 100), 4)
    return round(abs(price) / (abs(price) + 100), 4)


def american_to_decimal(odds) -> float | None:
    """Convert American odds to decimal odds."""
    try:
        price = int(float(odds))
    except (TypeError, ValueError):
        return None
    if price == 0:
        return None
    if price > 0:
        return round((price / 100) + 1, 2)
    return round((100 / abs(price)) + 1, 2)


def implied_to_american(probability: float) -> int | None:
    """Convert a 0-1 fair probability to American odds."""
    if probability is None or probability <= 0 or probability >= 1:
        return None
    if probability >= 0.5:
        return int(round(-(probability / (1 - probability)) * 100))
    return int(round(((1 - probability) / probability) * 100))


def edge(model_probability: float | None, implied_probability: float | None) -> float | None:
    """Return model minus implied probability as percentage points."""
    if model_probability is None or implied_probability is None:
        return None
    return round((model_probability - implied_probability) * 100, 1)


def confidence_from_margin(margin: float, sample_size: int = 0) -> str:
    """Convert model separation and sample depth into a bettor-friendly label."""
    depth_penalty = 0
    if sample_size < 6:
        depth_penalty = 1
    if abs(margin) >= 18 and depth_penalty == 0:
        return "High"
    if abs(margin) >= 10:
        return "Medium"
    if abs(margin) >= 5:
        return "Low-Medium"
    return "Low"


def classify_value(edge_pct: float | None, confidence: str = "Low", has_price: bool = True) -> str:
    """Classify a priced market without forcing a bet."""
    if not has_price or edge_pct is None:
        return "Pass"
    if edge_pct < -4:
        return "Avoid"
    if edge_pct < 2:
        return "Pass"
    if edge_pct < 5:
        return "Small Value"
    if edge_pct < 8:
        return "Lean"
    if confidence in {"High", "Medium"}:
        return "Best Bet"
    return "Lean"


def prop_label(edge_pct: float | None, confidence: str = "Low", has_price: bool = True) -> str:
    """Slightly softer labels for props and derivative markets."""
    label = classify_value(edge_pct, confidence, has_price)
    if label == "Best Bet":
        return "Best Prop"
    return label
