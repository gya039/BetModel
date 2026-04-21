"""
bankroll.py - bankroll, staking, accumulator, and bet-history helpers.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from utils import DATA_PROC, save_json

BASE_BANKROLL = 500.0
MAX_SINGLE_EXPOSURE_PCT = 0.50
BETTING_DIR = DATA_PROC / "betting"
STAKING_JSON = BETTING_DIR / "staking_plan.json"
STAKING_CSV = BETTING_DIR / "staking_plan.csv"
BET_HISTORY_CSV = BETTING_DIR / "bet_history.csv"


def current_bankroll() -> float:
    """Use settled bet history if present, otherwise start at EUR 500."""
    if not BET_HISTORY_CSV.exists():
        return BASE_BANKROLL
    with BET_HISTORY_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    settled = [row for row in rows if row.get("result") in {"Win", "Loss", "Push"} and row.get("bankroll_after")]
    if not settled:
        return BASE_BANKROLL
    try:
        return float(settled[-1]["bankroll_after"])
    except (TypeError, ValueError):
        return BASE_BANKROLL


def stake_tier(edge_pct: float | None, confidence: str, bankroll: float) -> dict:
    """MMA staking profile: 3-10% of bankroll, capped by confidence."""
    if edge_pct is None or edge_pct < 3:
        pct = 0.0
        label = "Pass"
    elif edge_pct < 6:
        pct = 0.03
        label = "Small"
    elif edge_pct < 10:
        pct = 0.05
        label = "Standard"
    elif edge_pct < 15:
        pct = 0.07
        label = "Strong"
    else:
        pct = 0.10
        label = "Max"

    if confidence == "Low" and pct > 0.03:
        pct = 0.03
        label = "Capped"
    if confidence == "Low-Medium" and pct > 0.05:
        pct = 0.05
        label = "Capped"

    stake = round(bankroll * pct, 2)
    return {
        "pct": f"{pct * 100:.1f}%".replace(".0%", "%"),
        "pctValue": round(pct * 100, 1),
        "eur": stake,
        "label": label,
        "reportLabel": f"{pct * 100:.1f}%".replace(".0%", "%") + f" (EUR {stake:.2f})",
    }


def is_acca_market(row: dict) -> bool:
    market = str(row.get("market", ""))
    return market == "Moneyline" or " by KO/TKO" in market or " by Submission" in market or " by Decision" in market


def candidate_singles(analyses: list[dict], bankroll: float) -> list[dict]:
    picks = []
    used_fights = set()
    exposure_cap = round(bankroll * MAX_SINGLE_EXPOSURE_PCT, 2)
    current_exposure = 0.0
    all_rows = [row for fight in analyses for row in fight.get("markets", [])]
    priced = [
        row for row in all_rows
        if row.get("decimal_odds") not in ("", None)
        and row.get("edge") is not None
        and row.get("edge") >= 3
        and row.get("label") not in {"Pass", "Avoid"}
    ]
    priced.sort(key=lambda row: (row.get("edge") or 0, row.get("model_probability") or 0), reverse=True)
    for row in priced:
        fight_id = row.get("fight_id")
        if fight_id in used_fights:
            continue
        stake = stake_tier(row.get("edge"), row.get("confidence", "Low"), bankroll)
        if stake["eur"] <= 0:
            continue
        if current_exposure + stake["eur"] > exposure_cap:
            continue
        used_fights.add(fight_id)
        current_exposure = round(current_exposure + stake["eur"], 2)
        pick = {
            **row,
            "stake": stake,
            "potential_return": round(stake["eur"] * float(row["decimal_odds"]), 2),
            "profit_if_win": round(stake["eur"] * (float(row["decimal_odds"]) - 1), 2),
            "decision": "BET",
        }
        picks.append(pick)
    return picks


def best_acca_leg_for_fight(fight: dict) -> dict | None:
    rows = [
        row for row in fight.get("markets", [])
        if is_acca_market(row)
        and row.get("decimal_odds") not in ("", None)
        and row.get("edge") is not None
        and row.get("edge") >= 3
        and row.get("model_probability") is not None
    ]
    if not rows:
        return None
    rows.sort(key=lambda row: (row.get("edge") or 0, row.get("model_probability") or 0), reverse=True)
    row = rows[0]
    return {
        "fight_id": row.get("fight_id"),
        "fight": row.get("fight"),
        "label": f"{row.get('selection')} ({row.get('market')})",
        "market": row.get("market"),
        "selection": row.get("selection"),
        "sportsbook": row.get("sportsbook"),
        "odds": round(float(row.get("decimal_odds")), 2),
        "edge": round(float(row.get("edge")), 1),
        "model_probability": row.get("model_probability"),
    }


def build_accumulators(analyses: list[dict], bankroll: float) -> list[dict]:
    legs = []
    for fight in analyses:
        leg = best_acca_leg_for_fight(fight)
        if leg:
            legs.append(leg)
    legs.sort(key=lambda leg: (leg["edge"], leg["model_probability"]), reverse=True)

    def combined_odds(selection: list[dict]) -> float:
        result = 1.0
        for leg in selection:
            result *= leg["odds"]
        return round(result, 2)

    def combined_probability(selection: list[dict]) -> float:
        result = 1.0
        for leg in selection:
            result *= float(leg["model_probability"])
        return round(result, 4)

    accas = []
    for type_name, count, stake_pct, min_edge in [
        ("Double", 2, 0.03, 3),
        ("Treble", 3, 0.02, 4),
        ("4-Fold", 4, 0.01, 5),
    ]:
        eligible = [leg for leg in legs if leg["edge"] >= min_edge]
        if len(eligible) < count:
            continue
        selection = eligible[:count]
        odds = combined_odds(selection)
        stake = round(bankroll * stake_pct, 2)
        model_prob = combined_probability(selection)
        market_prob = round(1 / odds, 4) if odds else None
        combined_edge = round((model_prob - market_prob) * 100, 1) if market_prob else None
        accas.append(
            {
                "type": type_name,
                "legs": selection,
                "combined_odds": odds,
                "model_probability": model_prob,
                "market_probability": market_prob,
                "combined_edge": combined_edge,
                "stake": stake,
                "stake_pct": round(stake_pct * 100, 1),
                "potential_return": round(stake * odds, 2),
                "profit_if_win": round(stake * (odds - 1), 2),
            }
        )
    return accas


def ensure_history() -> None:
    if BET_HISTORY_CSV.exists():
        return
    BET_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with BET_HISTORY_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "date",
                "bet_type",
                "fight",
                "market",
                "selection",
                "sportsbook",
                "odds",
                "stake_eur",
                "result",
                "pnl",
                "bankroll_before",
                "bankroll_after",
                "notes",
            ],
        )
        writer.writeheader()


def build_staking_plan(analyses: list[dict]) -> dict:
    bankroll = current_bankroll()
    singles = candidate_singles(analyses, bankroll)
    accumulators = build_accumulators(analyses, bankroll)
    ensure_history()
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bankroll": bankroll,
        "base_bankroll": BASE_BANKROLL,
        "staking_rules": [
            {"edge": "3-6%", "stake": "3%"},
            {"edge": "6-10%", "stake": "5%"},
            {"edge": "10-15%", "stake": "7%"},
            {"edge": "15%+", "stake": "10%"},
            {"edge": "Low confidence cap", "stake": "3%"},
            {"edge": "Card singles cap", "stake": "50% max"},
        ],
        "singles": singles,
        "accumulators": accumulators,
        "total_single_stake": round(sum(p["stake"]["eur"] for p in singles), 2),
        "total_accumulator_stake": round(sum(a["stake"] for a in accumulators), 2),
    }
    return payload


def save_staking_plan(plan: dict) -> None:
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    save_json(plan, STAKING_JSON)
    fields = [
        "fight",
        "market",
        "selection",
        "sportsbook",
        "decimal_odds",
        "model_probability",
        "edge",
        "confidence",
        "stake_pct",
        "stake_eur",
        "potential_return",
        "profit_if_win",
    ]
    with STAKING_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for pick in plan["singles"]:
            writer.writerow(
                {
                    "fight": pick.get("fight"),
                    "market": pick.get("market"),
                    "selection": pick.get("selection"),
                    "sportsbook": pick.get("sportsbook"),
                    "decimal_odds": pick.get("decimal_odds"),
                    "model_probability": pick.get("model_probability"),
                    "edge": pick.get("edge"),
                    "confidence": pick.get("confidence"),
                    "stake_pct": pick.get("stake", {}).get("pct"),
                    "stake_eur": pick.get("stake", {}).get("eur"),
                    "potential_return": pick.get("potential_return"),
                    "profit_if_win": pick.get("profit_if_win"),
                }
            )


def load_history() -> list[dict]:
    ensure_history()
    with BET_HISTORY_CSV.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
