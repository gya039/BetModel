"""
value_engine.py - odds conversion and value classification for Octagon IQ.

Philosophy: No Bet is the default state. A bet only fires when edge is genuine,
confidence is multi-factor validated, and underdog risk is controlled.
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


def confidence_from_margin(
    margin: float,
    sample_size: int = 0,
    market_edge_pct: float | None = None,
    decimal_odds: float | None = None,
) -> str:
    """
    Multi-factor confidence rating.

    Factors:
      - Score margin between fighters (0-3 pts)
      - Combined sample size (0-2 pts)
      - Market agreement: model vs market within 12% (0-1 pt)
      - Underdog penalty: decimal odds > 2.50 or > 3.00 (-1 or -2 pts)

    Thresholds:
      >= 5 → High
      >= 3 → Medium
      >= 2 → Low-Medium
      < 2  → Low
    """
    # Margin factor
    if abs(margin) >= 18:
        margin_score = 3
    elif abs(margin) >= 10:
        margin_score = 2
    elif abs(margin) >= 5:
        margin_score = 1
    else:
        margin_score = 0

    # Sample depth factor
    if sample_size >= 15:
        sample_score = 2
    elif sample_size >= 8:
        sample_score = 1
    else:
        sample_score = 0

    # Market agreement bonus: model and market within 12%
    market_score = 0
    if market_edge_pct is not None and abs(market_edge_pct) < 12:
        market_score = 1

    # Underdog variance penalty
    underdog_penalty = 0
    if decimal_odds is not None:
        if decimal_odds > 3.00:
            underdog_penalty = 2
        elif decimal_odds > 2.50:
            underdog_penalty = 1

    total = margin_score + sample_score + market_score - underdog_penalty

    if total >= 5:
        return "High"
    if total >= 3:
        return "Medium"
    if total >= 2:
        return "Low-Medium"
    return "Low"


# Minimum edge required for any bet to be considered.
EDGE_MIN_PCT = 4.0

# Maximum decimal odds allowed without High confidence.
UNDERDOG_ODDS_THRESHOLD = 3.00

# Maximum model-vs-market disagreement before confidence is automatically downgraded.
MAX_MARKET_DISAGREEMENT_PCT = 12.0


def classify_value(
    edge_pct: float | None,
    confidence: str = "Low",
    has_price: bool = True,
    decimal_odds: float | None = None,
) -> str:
    """
    Classify a priced moneyline market. No Bet is the default state.

    A bet only triggers when:
      - edge >= 4%
      - confidence is not Low (unless edge >= 6% and odds are short)
      - underdog odds <= 3.00 OR confidence is High
    """
    if not has_price or edge_pct is None:
        return "No Bet"
    if edge_pct < -4:
        return "Avoid"
    if edge_pct < EDGE_MIN_PCT:
        return "Pass"

    # Hard underdog safeguard: odds > 3.00 requires High confidence
    if decimal_odds is not None and decimal_odds > UNDERDOG_ODDS_THRESHOLD:
        if confidence != "High":
            return "Pass"

    # Low confidence: only allow Small Value with a tight edge
    if confidence == "Low" and edge_pct < 8:
        return "Pass"

    if edge_pct < 6:
        return "Small Value"
    if edge_pct < 8:
        return "Lean"
    if confidence in {"High", "Medium"}:
        return "Best Bet"
    return "Lean"


def prop_label(edge_pct: float | None, confidence: str = "Low", has_price: bool = True) -> str:
    """Props are disabled — always return No Bet."""
    return "No Bet"
