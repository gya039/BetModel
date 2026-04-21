"""
NBA value bet scanner.

Scans Betfair for:
  - Moneyline value  (match odds)
  - Spread value     (asian handicap)

Player props: Betfair's NBA prop markets are thin. To scan props across
SkyBet, PaddyPower, and William Hill you need The Odds API (~$50/month).
The prop model (model_props.py) is ready — just needs an odds feed wired in.
See find_value_props() stub at the bottom.

Usage:
    python find_value.py              # dry run
    python find_value.py --place      # live mode
    python find_value.py --min-edge 0.03
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).parent.parent.parent))

from betfair.api import get_client, get_market_odds
from betfair.kelly import has_edge, recommended_stake, implied_probability
from betfairlightweight import filters
from nba.scripts.model_game import predict_moneyline, predict_spread, FEATURES
from nba.scripts.model_props import find_prop_value

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

BANKROLL = 500.0

# Betfair event type for basketball
BASKETBALL_EVENT_TYPE = "7522"
NBA_COMPETITION_ID    = "10"       # Betfair NBA competition ID

# Betfair team name -> our team abbreviation
TEAM_NAME_MAP = {
    "Atlanta Hawks":        "ATL", "Boston Celtics":       "BOS",
    "Brooklyn Nets":        "BKN", "Charlotte Hornets":    "CHA",
    "Chicago Bulls":        "CHI", "Cleveland Cavaliers":  "CLE",
    "Dallas Mavericks":     "DAL", "Denver Nuggets":       "DEN",
    "Detroit Pistons":      "DET", "Golden State Warriors":"GSW",
    "Houston Rockets":      "HOU", "Indiana Pacers":       "IND",
    "LA Clippers":          "LAC", "Los Angeles Lakers":   "LAL",
    "Memphis Grizzlies":    "MEM", "Miami Heat":           "MIA",
    "Milwaukee Bucks":      "MIL", "Minnesota Timberwolves":"MIN",
    "New Orleans Pelicans": "NOP", "New York Knicks":      "NYK",
    "Oklahoma City Thunder":"OKC", "Orlando Magic":        "ORL",
    "Philadelphia 76ers":   "PHI", "Phoenix Suns":         "PHX",
    "Portland Trail Blazers":"POR","Sacramento Kings":     "SAC",
    "San Antonio Spurs":    "SAS", "Toronto Raptors":      "TOR",
    "Utah Jazz":            "UTA", "Washington Wizards":   "WAS",
}


def normalise(name: str) -> str:
    return TEAM_NAME_MAP.get(name, name)


def load_team_state() -> dict:
    with open(PROCESSED_DIR / "team_state.json") as f:
        return json.load(f)


def load_player_state() -> dict:
    with open(PROCESSED_DIR / "player_state.json") as f:
        return json.load(f)


def get_nba_markets(client, market_types: list[str]):
    mf = filters.market_filter(
        event_type_ids=[BASKETBALL_EVENT_TYPE],
        competition_ids=[NBA_COMPETITION_ID],
        market_type_codes=market_types,
    )
    return client.betting.list_market_catalogue(
        filter=mf,
        market_projection=["COMPETITION", "EVENT", "MARKET_START_TIME",
                           "RUNNER_DESCRIPTION"],
        max_results=100,
    )


def build_game_features(team_state: dict, home: str, away: str) -> dict | None:
    hs = team_state.get(home)
    as_ = team_state.get(away)
    if not hs or not as_:
        return None

    return {
        "HOME_L10_NET":        hs["l10_net"],
        "AWAY_L10_NET":        as_["l10_net"],
        "HOME_L5_NET":         hs["l5_net"],
        "AWAY_L5_NET":         as_["l5_net"],
        "HOME_L10_WIN_PCT":    hs["l10_win_pct"],
        "AWAY_L10_WIN_PCT":    as_["l10_win_pct"],
        "HOME_L5_WIN_PCT":     hs["l5_win_pct"],
        "AWAY_L5_WIN_PCT":     as_["l5_win_pct"],
        "HOME_L10_PTS_FOR":    hs["l10_pts_for"],
        "AWAY_L10_PTS_FOR":    as_["l10_pts_for"],
        "HOME_L10_PTS_AGAINST":hs["l10_pts_against"],
        "AWAY_L10_PTS_AGAINST":as_["l10_pts_against"],
        "HOME_REST_DAYS":      3,   # unknown until schedule is parsed
        "AWAY_REST_DAYS":      3,
        "HOME_IS_B2B":         0,
        "AWAY_IS_B2B":         0,
        "REST_ADVANTAGE":      0,
    }


def scan_moneyline(client, team_state: dict, min_edge: float) -> list[dict]:
    markets = get_nba_markets(client, ["MATCH_ODDS"])
    print(f"\nScanning {len(markets)} NBA moneyline markets...")
    bets = []

    for market in markets:
        runner_names = {r.selection_id: r.runner_name for r in market.runners}
        book = get_market_odds(client, market.market_id)
        if not book:
            continue

        odds_map = {}
        for r in book.runners:
            if r.ex.available_to_back:
                odds_map[r.selection_id] = r.ex.available_to_back[0].price

        if len(odds_map) < 2:
            continue

        runner_ids = list(runner_names.keys())
        home_id, away_id = runner_ids[0], runner_ids[1]

        home_name = normalise(runner_names[home_id])
        away_name = normalise(runner_names[away_id])

        features = build_game_features(team_state, home_name, away_name)
        if features is None:
            print(f"  Skipped {home_name} vs {away_name} — not in team state")
            continue

        probs      = predict_moneyline(features)
        home_odds  = odds_map.get(home_id)
        away_odds  = odds_map.get(away_id)

        for label, sel_id, odds, prob in [
            ("Home", home_id, home_odds, probs["home"]),
            ("Away", away_id, away_odds, probs["away"]),
        ]:
            if not odds:
                continue
            edge = prob - implied_probability(odds)
            if edge < min_edge:
                continue
            bets.append({
                "type":        "MONEYLINE",
                "market_id":   market.market_id,
                "fixture":     f"{home_name} vs {away_name}",
                "kickoff":     str(market.market_start_time)[:16],
                "selection":   label,
                "selection_id":sel_id,
                "model_prob":  round(prob, 4),
                "implied":     round(implied_probability(odds), 4),
                "edge":        round(edge, 4),
                "odds":        odds,
                "stake":       recommended_stake(BANKROLL, prob, odds),
            })

    return bets


def scan_spread(client, team_state: dict, min_edge: float) -> list[dict]:
    markets = get_nba_markets(client, ["ASIAN_HANDICAP"])
    print(f"Scanning {len(markets)} NBA spread markets...")
    bets = []

    for market in markets:
        runner_names = {r.selection_id: r.runner_name for r in market.runners}
        book = get_market_odds(client, market.market_id)
        if not book:
            continue

        odds_map = {}
        for r in book.runners:
            if r.ex.available_to_back:
                odds_map[r.selection_id] = r.ex.available_to_back[0].price

        runner_ids = list(runner_names.keys())
        if len(runner_ids) < 2:
            continue

        home_id, away_id = runner_ids[0], runner_ids[1]
        home_name = normalise(runner_names[home_id])
        away_name = normalise(runner_names[away_id])

        # Parse handicap from runner name e.g. "Boston Celtics -5.5"
        import re
        home_hcap = 0.0
        match = re.search(r"([+-]?\d+\.?\d*)\s*$", runner_names[home_id])
        if match:
            home_hcap = float(match.group(1))

        features = build_game_features(team_state, home_name, away_name)
        if features is None:
            continue

        model_diff = predict_spread(features)
        # Adjusted for handicap: if model says +7 and handicap is -5.5,
        # adjusted margin = 7 - 5.5 = +1.5 (lean home)
        adj_diff = model_diff + home_hcap

        home_odds = odds_map.get(home_id, 1.9)
        away_odds = odds_map.get(away_id, 1.9)

        if adj_diff > 1.5 and home_odds:
            edge = (1 / home_odds) * -1 + 0.52   # rough ATS edge estimate
            if edge > min_edge:
                bets.append({
                    "type":       "SPREAD",
                    "market_id":  market.market_id,
                    "fixture":    f"{home_name} vs {away_name}",
                    "kickoff":    str(market.market_start_time)[:16],
                    "selection":  f"Home ({home_hcap:+.1f})",
                    "model_diff": round(model_diff, 1),
                    "handicap":   home_hcap,
                    "adj_margin": round(adj_diff, 1),
                    "odds":       home_odds,
                    "stake":      recommended_stake(BANKROLL, 0.54, home_odds),
                })
        elif adj_diff < -1.5 and away_odds:
            edge = (1 / away_odds) * -1 + 0.52
            if edge > min_edge:
                bets.append({
                    "type":       "SPREAD",
                    "market_id":  market.market_id,
                    "fixture":    f"{home_name} vs {away_name}",
                    "kickoff":    str(market.market_start_time)[:16],
                    "selection":  f"Away ({-home_hcap:+.1f})",
                    "model_diff": round(model_diff, 1),
                    "handicap":   -home_hcap,
                    "adj_margin": round(-adj_diff, 1),
                    "odds":       away_odds,
                    "stake":      recommended_stake(BANKROLL, 0.54, away_odds),
                })

    return bets


def print_bets(bets: list[dict]):
    if not bets:
        print("No value bets found.")
        return

    bets = sorted(bets, key=lambda b: b.get("edge", 0), reverse=True)
    print(f"\n{'='*60}")
    print(f"  NBA VALUE BETS — {len(bets)} found")
    print(f"{'='*60}")

    for b in bets:
        print(f"\n  [{b['type']}] {b['fixture']}  {b['kickoff']}")
        if b["type"] == "MONEYLINE":
            print(f"  Selection  : {b['selection']} @ {b['odds']}")
            print(f"  Model      : {b['model_prob']:.1%}  |  "
                  f"Implied: {b['implied']:.1%}  |  Edge: {b['edge']:+.1%}")
        else:
            print(f"  Selection  : {b['selection']} @ {b['odds']}")
            print(f"  Model diff : {b['model_diff']:+.1f} pts  |  "
                  f"Adj margin: {b['adj_margin']:+.1f} pts")
        print(f"  Stake      : {b['stake']:.2f}  (quarter Kelly on {BANKROLL} bankroll)")
        print(f"  Market ID  : {b['market_id']}")


# ---------------------------------------------------------------------------
# Props stub — wire in an odds feed here (The Odds API / SkyBet / PaddyPower)
# ---------------------------------------------------------------------------

def find_value_props(player_state: dict, prop_lines: dict) -> list[dict]:
    """
    prop_lines: {player_name: {"PTS": 22.5, "REB": 7.5, "AST": 4.5, ...}}

    Wire in your odds source here — The Odds API, SkyBet, or PaddyPower.
    The model is ready; it just needs book lines to compare against.
    """
    bets = []
    for player_id, pstate in player_state.items():
        name = pstate.get("name")
        lines = prop_lines.get(name, {})
        for prop, line in lines.items():
            features = {
                f"L5_{prop}":  pstate.get(f"l5_{prop.lower()}", 0),
                f"L10_{prop}": pstate.get(f"l10_{prop.lower()}", 0),
                "L5_MIN":      pstate.get("l5_min", 30),
                "L10_MIN":     pstate.get("l10_min", 30),
                "OPP_DEF_L10": 110.0,   # replace with actual matchup
                "IS_HOME":     1,
                "IS_B2B":      0,
                "REST_DAYS":   2,
            }
            result = find_prop_value(prop, features, line)
            if result:
                bets.append({"player": name, "prop": prop, **result})
    return bets


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--place",    action="store_true", default=False)
    args = parser.parse_args()

    print("DRY RUN" if not args.place else "LIVE MODE")

    client     = get_client()
    team_state = load_team_state()

    all_bets = []
    all_bets += scan_moneyline(client, team_state, args.min_edge)
    all_bets += scan_spread(client, team_state, args.min_edge)

    print_bets(all_bets)

    if args.place and all_bets:
        print("\n[place_bet() calls go here — validate model first]")
