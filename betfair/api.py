"""
Betfair Exchange API wrapper using betfairlightweight.
Handles login, market lookup, and bet placement.
"""

import os
import betfairlightweight
from betfairlightweight import filters
from dotenv import load_dotenv

load_dotenv()


def get_client():
    client = betfairlightweight.APIClient(
        username=os.getenv("BETFAIR_USERNAME"),
        password=os.getenv("BETFAIR_PASSWORD"),
        app_key=os.getenv("BETFAIR_APP_KEY"),
        certs=os.getenv("BETFAIR_CERT_PATH", "./betfair/certs"),
    )
    client.login()
    return client


def get_football_markets(client, competition_ids: list[str], market_types: list[str]):
    """
    Fetch available markets for given competitions.
    competition_ids: Betfair competition IDs (e.g. La Liga = '117', Bundesliga = '59')
    market_types: e.g. ['MATCH_ODDS', 'ASIAN_HANDICAP', 'OVER_UNDER_25']
    """
    market_filter = filters.market_filter(
        event_type_ids=["1"],  # 1 = Soccer
        competition_ids=competition_ids,
        market_type_codes=market_types,
    )
    markets = client.betting.list_market_catalogue(
        filter=market_filter,
        market_projection=["COMPETITION", "EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"],
        max_results=200,
    )
    return markets


def get_market_odds(client, market_id: str):
    """Get best available back/lay prices for a market."""
    book = client.betting.list_market_book(
        market_ids=[market_id],
        price_projection=filters.price_projection(
            price_data=["EX_BEST_OFFERS"]
        ),
    )
    return book[0] if book else None


def place_bet(client, market_id: str, selection_id: int, side: str, price: float, size: float):
    """
    Place a single bet on the exchange.
    side: 'BACK' or 'LAY'
    """
    instruction = filters.place_instruction(
        order_type="LIMIT",
        selection_id=selection_id,
        side=side,
        limit_order=filters.limit_order(
            size=size,
            price=price,
            persistence_type="LAPSE",
        ),
    )
    result = client.betting.place_orders(
        market_id=market_id,
        instructions=[instruction],
    )
    return result
