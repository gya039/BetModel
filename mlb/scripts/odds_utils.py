"""
Odds parsing and sanity filters shared by morning and movement workflows.

Rules:
- Moneyline prices are filtered before storage and edge calculation.
- Spread prices must match both exact team name and exact point.
- Standard run-line display is favorite -1.5 / underdog +1.5.
"""

from __future__ import annotations

from statistics import median


PREFERRED_BOOKMAKERS = {
    "paddypower",
    "skybet",
    "boylesports",
}

UK_BOOKMAKERS = {
    "sport888",
    "betfair_ex_uk",
    "betfair_sb_uk",
    "betvictor",
    "betway",
    "boylesports",
    "casumo",
    "coral",
    "grosvenor",
    "ladbrokes_uk",
    "leovegas",
    "livescorebet",
    "matchbook",
    "paddypower",
    "skybet",
    "smarkets",
    "unibet_uk",
    "virginbet",
    "williamhill",
}


def valid_decimal_price(price, *, market: str = "h2h") -> bool:
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False
    if market == "h2h":
        return 1.05 <= price <= 12.0
    return 1.05 <= price <= 25.0


def filter_outlier_prices(prices: list[tuple[float, str]], *, market: str = "h2h") -> list[tuple[float, str]]:
    clean = [(float(p), b) for p, b in prices if valid_decimal_price(p, market=market)]
    if len(clean) < 3:
        return clean
    med = median([p for p, _ in clean])
    max_abs = 0.45 if market == "h2h" else 0.75
    max_rel = 0.35 if market == "h2h" else 0.45
    return [
        (p, b)
        for p, b in clean
        if abs(p - med) <= max(max_abs, med * max_rel)
    ]


def _book_rank(book_key: str | None) -> int:
    if book_key in PREFERRED_BOOKMAKERS:
        return 0
    if book_key in UK_BOOKMAKERS:
        return 1
    return 2


def best_price_from_candidates(candidates: list[tuple[float, str]], *, market: str = "h2h") -> tuple[float | None, str | None]:
    clean = filter_outlier_prices(candidates, market=market)
    if not clean:
        return None, None
    clean.sort(key=lambda item: (_book_rank(item[1]), -item[0]))
    return round(float(clean[0][0]), 3), clean[0][1]


def collect_outcome_prices(books: list[dict], market_key: str, team_name: str, point: float | None = None) -> list[tuple[float, str]]:
    out = []
    for bm in books:
        book_key = bm.get("key")
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue
            for outcome in market.get("outcomes", []):
                if outcome.get("name") != team_name:
                    continue
                if point is not None:
                    outcome_point = outcome.get("point")
                    if outcome_point is None or float(outcome_point) != float(point):
                        continue
                price = outcome.get("price")
                if price is not None:
                    out.append((price, book_key))
    return out


def best_moneyline(books: list[dict], team_name: str) -> tuple[float | None, str | None]:
    return best_price_from_candidates(
        collect_outcome_prices(books, "h2h", team_name),
        market="h2h",
    )


def best_spread(books: list[dict], team_name: str, point: float) -> tuple[float | None, str | None]:
    return best_price_from_candidates(
        collect_outcome_prices(books, "spreads", team_name, point),
        market="spreads",
    )


def collect_spread_options(primary_books: list[dict], alt_books: list[dict], team_name: str) -> list[dict]:
    best_by_line: dict[float, list[tuple[float, str]]] = {}
    for books, market_key in ((primary_books, "spreads"), (alt_books, "alternate_spreads")):
        for bm in books:
            book_key = bm.get("key")
            for market in bm.get("markets", []):
                if market.get("key") != market_key:
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != team_name:
                        continue
                    point = outcome.get("point")
                    price = outcome.get("price")
                    if point is None or price is None:
                        continue
                    best_by_line.setdefault(float(point), []).append((price, book_key))

    options = []
    for point, candidates in best_by_line.items():
        price, _ = best_price_from_candidates(candidates, market="spreads")
        if price is not None:
            options.append({"line": point, "odds": round(float(price), 3)})
    return sorted(options, key=lambda x: x["line"])


def standard_run_line_points(home_ml: float | None, away_ml: float | None) -> tuple[float, float]:
    """Return (home_point, away_point) for standard favorite/underdog RL."""
    if home_ml and away_ml and home_ml <= away_ml:
        return -1.5, 1.5
    if home_ml and away_ml:
        return 1.5, -1.5
    return -1.5, 1.5


def no_vig_probs(home_odds: float | None, away_odds: float | None) -> tuple[float | None, float | None]:
    if not home_odds or not away_odds or home_odds <= 1 or away_odds <= 1:
        return None, None
    h_raw = 1.0 / home_odds
    a_raw = 1.0 / away_odds
    total = h_raw + a_raw
    if total <= 0:
        return None, None
    return round(h_raw / total, 4), round(a_raw / total, 4)
