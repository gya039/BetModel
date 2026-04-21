"""
betting_model.py - explainable UFC betting engine for Octagon IQ.

Run:
    python src/betting_model.py
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from difflib import SequenceMatcher
from pathlib import Path

from betting_analysis import (
    best_method,
    explain_fight_script,
    method_strength,
    num,
    pct,
    underdog_path,
    vulnerabilities,
)
from utils import DATA_PROC, DATA_RAW, load_json, save_json
from bankroll import build_staking_plan, save_staking_plan
from value_engine import (
    american_to_decimal,
    american_to_implied,
    classify_value,
    confidence_from_margin,
    edge,
    implied_to_american,
    prop_label,
)

BETTING_DIR = DATA_PROC / "betting"
EDGES_JSON = BETTING_DIR / "betting_edges.json"
EDGES_CSV = BETTING_DIR / "betting_edges.csv"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def pct_stat(fighter: dict, key: str, default: float = 0.5) -> float:
    value = pct(fighter.get(key), default)
    return clamp(value, 0, 1)


def stat_rating(fighter: dict) -> float:
    """A compact, explainable 0-100 fighter rating from processed analytics."""
    striking_net = clamp((num(fighter.get("slpm")) - num(fighter.get("sapm")) + 4) / 8, 0, 1)
    striking_quality = (pct_stat(fighter, "str_acc") + pct_stat(fighter, "str_def")) / 2
    grappling = clamp(
        (num(fighter.get("avg_takedowns_landed")) / 5) * 0.65
        + (num(fighter.get("avg_sub_attempts")) / 2) * 0.35,
        0,
        1,
    )
    recent_volume = clamp(num(fighter.get("avg_sig_strikes_last3")) / 90, 0, 1)
    win_rate = pct_stat(fighter, "win_rate")
    finish = pct_stat(fighter, "finish_rate", 0.35)
    streak = clamp((num(fighter.get("current_streak")) + 3) / 6, 0, 1)
    sample = clamp(num(fighter.get("total_fights")) / 12, 0.25, 1)
    score = (
        win_rate * 24
        + striking_net * 17
        + striking_quality * 15
        + grappling * 14
        + recent_volume * 9
        + finish * 8
        + streak * 7
        + sample * 6
    )
    return round(score, 2)


def matchup_adjustment(fighter: dict, opponent: dict) -> float:
    adj = 0.0
    if num(fighter.get("avg_takedowns_landed")) >= 2 and pct_stat(opponent, "losses_by_sub_pct", 0) >= 0.25:
        adj += 3.0
    if num(fighter.get("avg_takedowns_landed")) >= 2 and num(opponent.get("avg_takedowns_landed")) < 0.8:
        adj += 1.5
    if num(fighter.get("slpm")) >= 4 and num(opponent.get("sapm")) >= 3.5:
        adj += 2.0
    if pct_stat(fighter, "wins_by_ko_tko_pct", 0) >= 0.30 and pct_stat(opponent, "losses_by_ko_tko_pct", 0) >= 0.30:
        adj += 2.0
    if pct_stat(fighter, "wins_by_dec_pct", 0) >= 0.45 and pct_stat(opponent, "losses_by_dec_pct", 0) >= 0.45:
        adj += 1.5
    reach_edge = num(fighter.get("reach_in")) - num(opponent.get("reach_in"))
    if abs(reach_edge) >= 3:
        adj += 0.8 if reach_edge > 0 else -0.8
    age_edge = num(opponent.get("age")) - num(fighter.get("age"))
    if age_edge >= 5:
        adj += 0.7
    return adj


def side_probabilities(fighter_a: dict, fighter_b: dict) -> tuple[float, float, float]:
    score_a = stat_rating(fighter_a) + matchup_adjustment(fighter_a, fighter_b)
    score_b = stat_rating(fighter_b) + matchup_adjustment(fighter_b, fighter_a)
    diff = score_a - score_b
    prob_a = 1 / (1 + math.exp(-diff / 12))
    prob_a = clamp(prob_a, 0.18, 0.82)
    return round(prob_a, 4), round(1 - prob_a, 4), round(diff, 2)


def method_probabilities(fighter: dict, opponent: dict, side_probability: float) -> dict:
    raw = {
        "KO/TKO": method_strength(fighter, opponent, "KO/TKO"),
        "Submission": method_strength(fighter, opponent, "Submission"),
        "Decision": method_strength(fighter, opponent, "Decision"),
    }
    total = sum(raw.values()) or 1
    return {method: round(side_probability * (value / total), 4) for method, value in raw.items()}


def event_fingerprint(fighter_a: dict, fighter_b: dict) -> str:
    ids = sorted([fighter_a.get("fighter_id", ""), fighter_b.get("fighter_id", "")])
    return "_".join(ids)


def names_match(name_a: str, name_b: str) -> bool:
    return SequenceMatcher(None, name_a.lower(), name_b.lower()).ratio() >= 0.72


def load_odds(path: Path | None = None) -> dict | None:
    odds_path = path or (DATA_RAW / "odds" / "latest.json")
    if odds_path.exists():
        return load_json(odds_path)
    return None


def find_odds_event(odds_payload: dict | None, fighter_a: dict, fighter_b: dict) -> dict | None:
    if not odds_payload:
        return None
    names = {fighter_a.get("name", ""), fighter_b.get("name", "")}
    for event in odds_payload.get("events", odds_payload if isinstance(odds_payload, list) else []):
        teams = {event.get("home_team", ""), event.get("away_team", "")}
        if all(any(names_match(name, team) for team in teams) for name in names):
            return event
    return None


def extract_prices(odds_event: dict | None) -> list[dict]:
    prices: list[dict] = []
    if not odds_event:
        return prices
    for bookmaker in odds_event.get("bookmakers", []):
        sportsbook = bookmaker.get("title") or bookmaker.get("key")
        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            for outcome in market.get("outcomes", []):
                prices.append(
                    {
                        "sportsbook": sportsbook,
                        "market_key": market_key,
                        "selection": outcome.get("name"),
                        "odds": outcome.get("price"),
                        "point": outcome.get("point"),
                    }
                )
    return prices


def best_price_for(prices: list[dict], market_key: str, selection: str, point=None) -> dict | None:
    matches = []
    for price in prices:
        if price.get("market_key") != market_key:
            continue
        if point is not None and price.get("point") != point:
            continue
        if price.get("selection") and names_match(price["selection"], selection):
            matches.append(price)
    if not matches:
        return None
    return max(matches, key=lambda item: int(item.get("odds") or -10000))


def market_row(
    fight_id: str,
    fight_name: str,
    market: str,
    selection: str,
    model_probability: float,
    explanation: str,
    confidence: str,
    price: dict | None = None,
    is_prop: bool = False,
) -> dict:
    implied = american_to_implied(price.get("odds")) if price else None
    decimal_odds = american_to_decimal(price.get("odds")) if price else None
    edge_pct = edge(model_probability, implied)
    label = prop_label(edge_pct, confidence, bool(price)) if is_prop else classify_value(edge_pct, confidence, bool(price))
    return {
        "fight_id": fight_id,
        "fight": fight_name,
        "market": market,
        "selection": selection,
        "sportsbook": price.get("sportsbook") if price else "",
        "odds": price.get("odds") if price else "",
        "decimal_odds": decimal_odds if decimal_odds is not None else "",
        "implied_probability": implied,
        "model_probability": round(model_probability, 4),
        "fair_odds": implied_to_american(model_probability),
        "edge": edge_pct,
        "confidence": confidence,
        "label": label,
        "rationale": explanation,
    }


def analyze_matchup(matchup: dict, odds_payload: dict | None = None) -> dict:
    fighter_a = matchup["fighter_a"]
    fighter_b = matchup["fighter_b"]
    prob_a, prob_b, diff = side_probabilities(fighter_a, fighter_b)
    favorite, underdog = (fighter_a, fighter_b) if prob_a >= prob_b else (fighter_b, fighter_a)
    favorite_prob = max(prob_a, prob_b)
    underdog_prob = min(prob_a, prob_b)
    favorite_diff = diff if favorite is fighter_a else -diff
    confidence = confidence_from_margin(favorite_diff, int(num(favorite.get("total_fights")) + num(underdog.get("total_fights"))))
    fight_id = event_fingerprint(fighter_a, fighter_b)
    fight_name = f"{fighter_a['name']} vs {fighter_b['name']}"

    a_methods = method_probabilities(fighter_a, fighter_b, prob_a)
    b_methods = method_probabilities(fighter_b, fighter_a, prob_b)
    finish_probability = round(a_methods["KO/TKO"] + a_methods["Submission"] + b_methods["KO/TKO"] + b_methods["Submission"], 4)
    decision_probability = round(1 - finish_probability, 4)
    favorite_method = best_method(favorite, underdog)
    fight_script = explain_fight_script(favorite, underdog, favorite_method)

    odds_event = find_odds_event(odds_payload, fighter_a, fighter_b)
    prices = extract_prices(odds_event)
    rows = [
        market_row(
            fight_id,
            fight_name,
            "Moneyline",
            fighter_a["name"],
            prob_a,
            f"Side probability from matchup score, form, method profile, and physical/context edges.",
            confidence,
            best_price_for(prices, "h2h", fighter_a["name"]),
        ),
        market_row(
            fight_id,
            fight_name,
            "Moneyline",
            fighter_b["name"],
            prob_b,
            f"Side probability from matchup score, form, method profile, and physical/context edges.",
            confidence,
            best_price_for(prices, "h2h", fighter_b["name"]),
        ),
    ]

    method_prices = {}
    for price in prices:
        key = str(price.get("market_key", "")).lower()
        if "method" in key or "victory" in key:
            method_prices.setdefault(price.get("selection"), price)

    for fighter, methods in [(fighter_a, a_methods), (fighter_b, b_methods)]:
        for method, probability in methods.items():
            rows.append(
                market_row(
                    fight_id,
                    fight_name,
                    f"{fighter['name']} by {method}",
                    f"{fighter['name']} by {method}",
                    probability,
                    f"Blends {fighter['name']}'s win method profile with {('the opponent')}'s loss method profile.",
                    confidence,
                    method_prices.get(f"{fighter['name']} by {method}"),
                    is_prop=True,
                )
            )

    rows.extend(
        [
            market_row(
                fight_id,
                fight_name,
                "Fight Goes Distance",
                "Yes",
                decision_probability,
                "Derived from both fighters' decision rates, finish rates, and historical loss methods.",
                confidence,
                None,
                is_prop=True,
            ),
            market_row(
                fight_id,
                fight_name,
                "Fight Goes Distance",
                "No",
                finish_probability,
                "Derived from combined KO/sub paths and opponent finishing vulnerability.",
                confidence,
                None,
                is_prop=True,
            ),
        ]
    )

    for price in prices:
        if price.get("market_key") == "totals" and price.get("selection") in {"Over", "Under"}:
            model_prob = decision_probability if price["selection"] == "Over" else finish_probability
            rows.append(
                market_row(
                    fight_id,
                    fight_name,
                    f"Total Rounds {price.get('point')}",
                    price["selection"],
                    model_prob,
                    "Round total proxy from model finish/decision outlook.",
                    confidence,
                    price,
                    is_prop=True,
                )
            )

    priced_rows = [row for row in rows if row.get("odds") != ""]
    best_priced = sorted(priced_rows, key=lambda row: row.get("edge") if row.get("edge") is not None else -999, reverse=True)
    best_side = fighter_a["name"] if prob_a >= prob_b else fighter_b["name"]
    best_prop = max(
        [row for row in rows if row["market"] != "Moneyline"],
        key=lambda row: row["model_probability"],
    )

    summary = {
        "fight_id": fight_id,
        "bout_number": matchup.get("bout_number"),
        "weight_class": matchup.get("weight_class"),
        "fighter_a": fighter_a,
        "fighter_b": fighter_b,
        "fight": fight_name,
        "model_side": best_side,
        "model_side_probability": round(favorite_prob, 4),
        "underdog_probability": round(underdog_prob, 4),
        "likely_methods": [favorite_method, best_prop["market"]],
        "finish_probability": finish_probability,
        "decision_probability": decision_probability,
        "confidence": confidence,
        "playable_label": best_priced[0]["label"] if best_priced else "Pass",
        "best_priced_edge": best_priced[0]["edge"] if best_priced else None,
        "best_bet": best_priced[0] if best_priced and best_priced[0]["label"] not in {"Pass", "Avoid"} else None,
        "best_prop": best_prop,
        "betting_breakdown": {
            "edge": f"{best_side} is the model side at {favorite_prob:.1%}.",
            "fight_script": fight_script,
            "finish_outlook": (
                f"Finish is more live ({finish_probability:.1%}) than decision."
                if finish_probability >= decision_probability
                else f"Decision is more live ({decision_probability:.1%}) than a finish."
            ),
            "underdog_path": underdog_path(underdog, favorite),
            "favorite_danger": "; ".join(vulnerabilities(favorite)[:2]),
            "rationale": (
                f"The lean is statistical, not a guarantee: {favorite['name']} grades better across the current "
                f"matchup score, while {underdog['name']} still owns live paths if they can force their preferred phase."
            ),
        },
        "markets": rows,
        "odds_available": bool(prices),
    }
    return summary


def generate_card_betting(odds_path: Path | None = None) -> list[dict]:
    matchups = load_json(DATA_PROC / "matchup_summary.json")
    odds_payload = load_odds(odds_path)
    analyses = [analyze_matchup(matchup, odds_payload) for matchup in matchups]
    return analyses


def save_outputs(analyses: list[dict]) -> None:
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    save_json(analyses, EDGES_JSON)
    rows = [row for fight in analyses for row in fight["markets"]]
    fieldnames = [
        "fight",
        "market",
        "selection",
        "sportsbook",
        "odds",
        "decimal_odds",
        "implied_probability",
        "model_probability",
        "edge",
        "confidence",
        "label",
        "rationale",
    ]
    with EDGES_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    save_staking_plan(build_staking_plan(analyses))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Octagon IQ betting edges.")
    parser.add_argument("--odds-file", type=Path, default=None)
    args = parser.parse_args()
    analyses = generate_card_betting(args.odds_file)
    save_outputs(analyses)
    print(f"Wrote {EDGES_JSON}")
    print(f"Wrote {EDGES_CSV}")


if __name__ == "__main__":
    main()
