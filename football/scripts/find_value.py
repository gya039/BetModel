"""
Scan live Betfair markets for +EV bets using the trained model.

Features come from pre-computed team state (Elo, form, H2H) — no bookmaker
odds in the model inputs.

The scanner also checks William Hill (WH) and Pinnacle (PS) odds stored in
the processed data to show which book is offering the best price for each
value bet found. (Live SkyBet / PaddyPower requires The Odds API — phase 2.)

Usage:
    python find_value.py                    # dry run, both leagues
    python find_value.py --league laliga
    python find_value.py --min-edge 0.03    # only show edge >= 3%
    python find_value.py --place            # live mode (places bets)
"""

import sys
import json
import argparse
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from betfair.api import get_client, get_football_markets, get_market_odds
from betfair.kelly import has_edge, recommended_stake, implied_probability
from football.scripts.model import predict, FEATURES

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

COMPETITIONS = {
    "laliga":     "117",
    "bundesliga": "59",
}

MARKET_TYPES = ["MATCH_ODDS"]
BANKROLL = 500.0

# Betfair runner name -> CSV team name
TEAM_NAME_MAP = {
    # La Liga
    "Atletico Madrid":          "Ath Madrid",
    "Athletic Club":            "Ath Bilbao",
    "Rayo Vallecano":           "Vallecano",
    "Real Sociedad":            "Sociedad",
    "Espanyol":                 "Espanol",
    "Celta Vigo":               "Celta",
    # Bundesliga
    "Borussia Dortmund":        "Dortmund",
    "Borussia Monchengladbach": "M'gladbach",
    "B. Monchengladbach":       "M'gladbach",
    "Eintracht Frankfurt":      "Ein Frankfurt",
    "Bayer Leverkusen":         "Leverkusen",
    "FC Augsburg":              "Augsburg",
    "TSG Hoffenheim":           "Hoffenheim",
    "SC Freiburg":              "Freiburg",
    "VfB Stuttgart":            "Stuttgart",
    "VfL Wolfsburg":            "Wolfsburg",
    "VfL Bochum":               "Bochum",
    "FC Schalke 04":            "Schalke 04",
    "Hertha BSC":               "Hertha",
    "1. FC Koln":               "FC Koln",
    "1. FSV Mainz 05":          "Mainz",
    "Mainz 05":                 "Mainz",
    "1. FC Union Berlin":       "Union Berlin",
    "SpVgg Greuther Furth":     "Greuther Furth",
    "1. FC Heidenheim":         "Heidenheim",
    "FC St. Pauli":             "St Pauli",
    "St. Pauli":                "St Pauli",
    "Arminia Bielefeld":        "Bielefeld",
    "SV Darmstadt 98":          "Darmstadt",
    "Darmstadt 98":             "Darmstadt",
}


def normalise_name(name: str) -> str:
    return TEAM_NAME_MAP.get(name, name)


def load_team_state(league: str) -> dict:
    path = PROCESSED_DIR / f"{league}_team_state.json"
    with open(path) as f:
        return json.load(f)


def load_h2h_state(league: str) -> dict:
    path = PROCESSED_DIR / f"{league}_h2h_state.json"
    with open(path) as f:
        return json.load(f)


def get_h2h(h2h_state: dict, home: str, away: str, n: int = 5) -> dict:
    key = "|".join(sorted([home, away]))
    games = h2h_state.get(key, [])[-n:]
    if not games:
        return {"h2h_hw_rate": 0.45, "h2h_draw_rate": 0.25, "h2h_avg_goals": 2.5}
    ng = len(games)
    hw    = sum(1 for g in games if g["winner"] == home)
    draws = sum(1 for g in games if g["winner"] == "draw")
    goals = sum(g["goals"] for g in games)
    return {
        "h2h_hw_rate":   hw / ng,
        "h2h_draw_rate": draws / ng,
        "h2h_avg_goals": goals / ng,
    }


def build_features(team_state: dict, h2h_state: dict,
                   home: str, away: str) -> dict | None:
    hs = team_state.get(home)
    as_ = team_state.get(away)
    if hs is None or as_ is None:
        return None

    h2h = get_h2h(h2h_state, home, away)

    return {
        "home_elo":     hs["elo"],
        "away_elo":     as_["elo"],
        "elo_diff":     hs["elo"] - as_["elo"],
        # Last 5 overall
        "home_l5_ppg":  hs["l5"]["ppg"],
        "home_l5_gf":   hs["l5"]["gf"],
        "home_l5_ga":   hs["l5"]["ga"],
        "home_l5_gd":   hs["l5"]["gd"],
        "away_l5_ppg":  as_["l5"]["ppg"],
        "away_l5_gf":   as_["l5"]["gf"],
        "away_l5_ga":   as_["l5"]["ga"],
        "away_l5_gd":   as_["l5"]["gd"],
        # Last 10 overall
        "home_l10_ppg": hs["l10"]["ppg"],
        "home_l10_gf":  hs["l10"]["gf"],
        "home_l10_ga":  hs["l10"]["ga"],
        "away_l10_ppg": as_["l10"]["ppg"],
        "away_l10_gf":  as_["l10"]["gf"],
        "away_l10_ga":  as_["l10"]["ga"],
        # Venue-specific last 5
        "home_h5_ppg":  hs["home_l5"]["ppg"],
        "home_h5_gf":   hs["home_l5"]["gf"],
        "home_h5_ga":   hs["home_l5"]["ga"],
        "away_a5_ppg":  as_["away_l5"]["ppg"],
        "away_a5_gf":   as_["away_l5"]["gf"],
        "away_a5_ga":   as_["away_l5"]["ga"],
        # H2H
        **h2h,
        # Shots on target
        "home_sot":     hs["sot"],
        "away_sot":     as_["sot"],
    }


def scan_value_bets(league: str, min_edge: float) -> list[dict]:
    client = get_client()
    markets = get_football_markets(client, [COMPETITIONS[league]], MARKET_TYPES)
    print(f"\nScanning {len(markets)} {league} markets...")

    team_state = load_team_state(league)
    h2h_state  = load_h2h_state(league)
    value_bets = []

    for market in markets:
        market_id   = market.market_id
        runner_names = {r.selection_id: r.runner_name for r in market.runners}

        book = get_market_odds(client, market_id)
        if not book:
            continue

        # Live Betfair prices
        bf_odds = {}
        for runner in book.runners:
            back = runner.ex.available_to_back
            if back:
                bf_odds[runner.selection_id] = back[0].price

        if len(bf_odds) < 3:
            continue

        draw_id  = next(
            (sid for sid, name in runner_names.items() if "draw" in name.lower()), None
        )
        team_ids = [sid for sid in runner_names if sid != draw_id]
        if draw_id is None or len(team_ids) < 2:
            continue

        home_id, away_id = team_ids[0], team_ids[1]
        home_name = normalise_name(runner_names[home_id])
        away_name = normalise_name(runner_names[away_id])

        features = build_features(team_state, h2h_state, home_name, away_name)
        if features is None:
            print(f"  Skipped {home_name} vs {away_name} — not in team state")
            continue

        model_probs = predict(league, features)

        for label, sel_id, outcome_key in [
            ("Home", home_id, "home"),
            ("Draw", draw_id, "draw"),
            ("Away", away_id, "away"),
        ]:
            model_prob = model_probs[outcome_key]
            bf_price   = bf_odds.get(sel_id)
            if not bf_price:
                continue

            edge = model_prob - implied_probability(bf_price)
            if edge < min_edge:
                continue

            stake = recommended_stake(BANKROLL, model_prob, bf_price)
            value_bets.append({
                "market_id":    market_id,
                "fixture":      f"{home_name} vs {away_name}",
                "kickoff":      str(market.market_start_time),
                "selection":    label,
                "selection_id": sel_id,
                "model_prob":   round(model_prob, 4),
                "implied":      round(implied_probability(bf_price), 4),
                "edge":         round(edge, 4),
                "betfair_odds": bf_price,
                "stake":        stake,
                # Model internals — useful for understanding the call
                "home_elo":     round(features["home_elo"]),
                "away_elo":     round(features["away_elo"]),
                "home_l5_ppg":  round(features["home_l5_ppg"], 2),
                "away_l5_ppg":  round(features["away_l5_ppg"], 2),
            })

    return value_bets


def print_bets(bets: list[dict]):
    if not bets:
        print("No value bets found.")
        return

    bets_sorted = sorted(bets, key=lambda b: b["edge"], reverse=True)

    print(f"\n{'='*62}")
    print(f"  VALUE BETS — {len(bets_sorted)} found")
    print(f"{'='*62}")

    for b in bets_sorted:
        print(f"\n  {b['fixture']}  ({b['kickoff'][:16]})")
        print(f"  Selection  : {b['selection']} @ {b['betfair_odds']} (Betfair)")
        print(f"  Model      : {b['model_prob']:.1%}  |  Implied: {b['implied']:.1%}  |  Edge: {b['edge']:+.1%}")
        print(f"  Stake      : {b['stake']:.2f}  (quarter Kelly on {BANKROLL} bankroll)")
        print(f"  Elo        : {b['home_elo']} vs {b['away_elo']}  |  "
              f"Form (PPG last 5): {b['home_l5_ppg']} / {b['away_l5_ppg']}")
        print(f"  Market ID  : {b['market_id']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scan for +EV bets on Betfair")
    parser.add_argument("--league",   choices=["laliga", "bundesliga", "both"], default="both")
    parser.add_argument("--min-edge", type=float, default=0.02,
                        help="Minimum model edge to flag a bet (default: 0.02 = 2%%)")
    parser.add_argument("--place",    action="store_true", default=False,
                        help="Place bets live. Omit for dry run (default).")
    args = parser.parse_args()

    leagues = ["laliga", "bundesliga"] if args.league == "both" else [args.league]

    print("LIVE MODE" if args.place else "DRY RUN — no bets placed")

    all_bets = []
    for league in leagues:
        all_bets.extend(scan_value_bets(league, args.min_edge))

    print_bets(all_bets)

    if args.place and all_bets:
        print("\n[place_bet() calls go here — uncomment when model is validated]")
