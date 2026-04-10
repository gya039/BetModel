"""
Horse racing lay strategy on Betfair Exchange.
Core idea: lay short-priced favourites that are overbet by the public.
Statistically, favourites at odds < 2.0 win less often than implied.
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent.parent))

from betfair.api import get_client, get_market_odds
from betfair.kelly import implied_probability
import betfairlightweight
from betfairlightweight import filters

BANKROLL = 500.0

# Only lay horses within this odds range
LAY_MIN_ODDS = 1.5
LAY_MAX_ODDS = 2.5

# Max liability per bet as % of bankroll
MAX_LIABILITY_PCT = 0.03  # 3% of bankroll


def get_racing_markets(client, country: str = "IE"):
    """Fetch today's horse racing markets for given country."""
    market_filter = filters.market_filter(
        event_type_ids=["7"],  # 7 = Horse Racing
        market_countries=[country],
        market_type_codes=["WIN"],
    )
    markets = client.betting.list_market_catalogue(
        filter=market_filter,
        market_projection=["EVENT", "RUNNER_DESCRIPTION", "MARKET_START_TIME"],
        sort="FIRST_TO_START",
        max_results=50,
    )
    return markets


def find_lay_opportunities(client, markets: list) -> list:
    """
    Identify favourites in the lay odds range.
    Returns list of potential lay bets with liability calculated.
    """
    opportunities = []

    for market in markets:
        market_id = market.market_id
        book = get_market_odds(client, market_id)
        if not book:
            continue

        runners = sorted(
            book.runners,
            key=lambda r: r.ex.available_to_back[0].price if r.ex.available_to_back else 999,
        )

        if not runners:
            continue

        favourite = runners[0]
        lay_prices = favourite.ex.available_to_lay

        if not lay_prices:
            continue

        best_lay = lay_prices[0].price

        if LAY_MIN_ODDS <= best_lay <= LAY_MAX_ODDS:
            # Liability = stake * (lay_odds - 1)
            # Work backwards from max liability to get stake
            max_liability = BANKROLL * MAX_LIABILITY_PCT
            lay_stake = round(max_liability / (best_lay - 1), 2)

            opportunities.append({
                "market_id": market_id,
                "event": market.event.name,
                "selection_id": favourite.selection_id,
                "lay_odds": best_lay,
                "implied_win_pct": round(implied_probability(best_lay) * 100, 1),
                "lay_stake": lay_stake,
                "max_liability": round(lay_stake * (best_lay - 1), 2),
                "profit_if_loses": lay_stake,
            })

    return opportunities


def run():
    client = get_client()
    print("Fetching Irish horse racing markets...")
    markets = get_racing_markets(client, country="IE")
    print(f"Found {len(markets)} markets")

    opportunities = find_lay_opportunities(client, markets)

    if not opportunities:
        print("No lay opportunities found in target odds range.")
        return

    print(f"\n{'='*60}")
    print(f"LAY OPPORTUNITIES ({len(opportunities)} found)")
    print(f"{'='*60}")
    for opp in opportunities:
        print(f"\nEvent:      {opp['event']}")
        print(f"Lay Odds:   {opp['lay_odds']} (implies {opp['implied_win_pct']}% win chance)")
        print(f"Lay Stake:  €{opp['lay_stake']}")
        print(f"Liability:  €{opp['max_liability']}")
        print(f"Profit if loses: €{opp['profit_if_loses']}")


if __name__ == "__main__":
    run()
