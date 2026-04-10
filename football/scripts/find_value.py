"""
Compare model probabilities against live Betfair odds to find +EV bets.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from betfair.api import get_client, get_football_markets, get_market_odds
from betfair.kelly import has_edge, recommended_stake, implied_probability
from football.scripts.model import predict

# Betfair competition IDs
COMPETITIONS = {
    "laliga": "117",
    "bundesliga": "59",
}

MARKET_TYPES = ["MATCH_ODDS"]

BANKROLL = 500.0  # update this to your actual bankroll


def scan_value_bets(league: str):
    client = get_client()
    comp_id = COMPETITIONS[league]

    markets = get_football_markets(client, [comp_id], MARKET_TYPES)
    print(f"\nScanning {len(markets)} {league} markets...")

    value_bets = []

    for market in markets:
        market_id = market.market_id
        book = get_market_odds(client, market_id)
        if not book:
            continue

        # Get best back prices for each runner (Home, Draw, Away)
        runners = book.runners
        if len(runners) < 3:
            continue

        odds = {}
        for runner in runners:
            name = runner.selection_id
            best_back = runner.ex.available_to_back
            if best_back:
                odds[name] = best_back[0].price

        if len(odds) < 3:
            continue

        runner_ids = list(odds.keys())
        home_odds, draw_odds, away_odds = odds[runner_ids[0]], odds[runner_ids[1]], odds[runner_ids[2]]

        # TODO: build features from team form data and call predict()
        # For now this is the structure — wire in your feature pipeline here
        # model_probs = predict(league, features)

        for label, sel_odds, sel_id in [
            ("Home", home_odds, runner_ids[0]),
            ("Draw", draw_odds, runner_ids[1]),
            ("Away", away_odds, runner_ids[2]),
        ]:
            impl = implied_probability(sel_odds)
            # Replace with model_probs[label.lower()] once model is wired in
            # model_prob = model_probs[label.lower()]
            # if has_edge(model_prob, sel_odds):
            #     stake = recommended_stake(BANKROLL, model_prob, sel_odds)
            #     value_bets.append({...})
            pass

    return value_bets


if __name__ == "__main__":
    for league in ["laliga", "bundesliga"]:
        bets = scan_value_bets(league)
        if bets:
            for b in bets:
                print(b)
        else:
            print(f"No value bets found for {league}")
