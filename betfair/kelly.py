"""
Kelly Criterion calculator for stake sizing.
Always use fractional Kelly to reduce variance.
"""


def kelly_stake(prob: float, decimal_odds: float, fraction: float = 0.25) -> float:
    """
    Calculate recommended stake as a fraction of bankroll.

    prob: your model's estimated probability of winning (0-1)
    decimal_odds: betfair decimal odds (e.g. 2.5)
    fraction: Kelly fraction — 0.25 = quarter Kelly (recommended)

    Returns: fraction of bankroll to stake (0 if no edge)
    """
    b = decimal_odds - 1  # profit per unit staked
    q = 1 - prob
    kelly = (b * prob - q) / b
    return max(0.0, round(kelly * fraction, 4))


def recommended_stake(bankroll: float, prob: float, decimal_odds: float, fraction: float = 0.25) -> float:
    """Returns actual stake amount in currency."""
    k = kelly_stake(prob, decimal_odds, fraction)
    return round(bankroll * k, 2)


def implied_probability(decimal_odds: float) -> float:
    """Convert decimal odds to implied probability."""
    return round(1 / decimal_odds, 4)


def has_edge(model_prob: float, decimal_odds: float) -> bool:
    """Returns True if model prob exceeds bookmaker implied prob."""
    return model_prob > implied_probability(decimal_odds)


if __name__ == "__main__":
    # Example: model says 60% win chance, odds are 2.0 (implies 50%)
    prob = 0.60
    odds = 2.0
    bankroll = 500.0

    print(f"Edge: {has_edge(prob, odds)}")
    print(f"Kelly fraction: {kelly_stake(prob, odds)}")
    print(f"Stake on €{bankroll} bankroll: €{recommended_stake(bankroll, prob, odds)}")
