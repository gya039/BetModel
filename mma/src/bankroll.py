"""
bankroll.py — Full-deployment staking plan for UFC betting.

Bankroll per card:
  - Fight Night / UFC on ESPN / UFC on ABC → EUR 500
  - Numbered card (UFC 300, UFC 329, etc.)  → EUR 1,000

Full bankroll is always deployed. Budget split per card:
  - Singles (moneyline):         55%  of bankroll
  - Method of victory:           25%  of bankroll
  - Accumulators (2-4 leg):      20%  of bankroll

Within singles, stakes are proportional to edge × confidence weight,
scaled to hit the 55% target exactly. No fixed percentage cap per bet —
the card size drives how much goes on each pick.

Division stake multipliers from division_filter.py are applied before
the final allocation is committed (poor divisions get reduced; blocked
divisions are skipped entirely).

Stop-loss: no bets after 3 consecutive losing events.
"""
from __future__ import annotations

import csv
import itertools
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from utils import DATA_PROC, save_json

BETTING_DIR   = DATA_PROC / "betting"
STAKING_JSON  = BETTING_DIR / "staking_plan.json"
STAKING_CSV   = BETTING_DIR / "staking_plan.csv"
BET_HISTORY_CSV = BETTING_DIR / "bet_history.csv"

# Bankroll per card type
BANKROLL_FIGHT_NIGHT = 500.0
BANKROLL_NUMBERED    = 1_000.0

# Allocation of the total bankroll across bet types
ALLOC_SINGLES  = 0.55
ALLOC_MOV      = 0.25
ALLOC_ACCUM    = 0.20

# Minimum edge to consider any bet
EDGE_MIN = 4.0

# Confidence weights used for proportional singles staking
CONFIDENCE_WEIGHTS = {
    "High":       3.0,
    "Medium":     2.0,
    "Low-Medium": 1.2,
    "Low":        0.8,
}

# Accumulator configuration
MAX_LEGS_PER_ACCUM   = 4
MIN_LEGS_PER_ACCUM   = 2
ACCUM_LEGS_PREFERRED = [2, 3]   # 2-leg and 3-leg parlays
MIN_CONFIDENCE_ACCUM = {"High", "Medium"}  # only use these in parlays

# Stop-loss: no bets after this many consecutive losing events
STOP_LOSS_EVENTS = 3

# Min/max stake per individual bet (EUR) — guardrails only
MIN_STAKE_EUR = 5.0
MAX_STAKE_EUR_FIGHT_NIGHT = 150.0
MAX_STAKE_EUR_NUMBERED    = 250.0


# ── Card type detection ───────────────────────────────────────────────────────

def is_numbered_card(event_name: str) -> bool:
    """
    Returns True for numbered UFC events: UFC 300, UFC 329, etc.
    Fight Nights, UFC on ESPN, UFC on ABC, etc. return False.
    """
    if not event_name:
        return False
    # "UFC 329", "UFC 300: Jones vs Miocic", "ufc299" all match
    return bool(re.search(r"\bufc\s*\d{2,3}\b", event_name, re.IGNORECASE))


def card_bankroll(event_name: str) -> float:
    return BANKROLL_NUMBERED if is_numbered_card(event_name) else BANKROLL_FIGHT_NIGHT


# ── Live bankroll ─────────────────────────────────────────────────────────────

def current_bankroll() -> float:
    """Live bankroll from ledger DB; CSV fallback; default if neither exists."""
    try:
        from ledger import current_bankroll_from_db
        return current_bankroll_from_db()
    except Exception:
        pass
    if not BET_HISTORY_CSV.exists():
        return BANKROLL_FIGHT_NIGHT
    with BET_HISTORY_CSV.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    settled = [r for r in rows if r.get("result") in {"Win", "Loss", "Push"} and r.get("bankroll_after")]
    if not settled:
        return BANKROLL_FIGHT_NIGHT
    try:
        return float(settled[-1]["bankroll_after"])
    except (TypeError, ValueError):
        return BANKROLL_FIGHT_NIGHT


def _has_consecutive_losing_events(n: int = STOP_LOSS_EVENTS) -> bool:
    try:
        from ledger import get_db
        with get_db() as conn:
            rows = conn.execute(
                "SELECT event_pnl FROM bankroll_snapshots "
                "WHERE snapshot_type = 'post_event' ORDER BY snapshot_at DESC LIMIT ?",
                (n,),
            ).fetchall()
        if len(rows) < n:
            return False
        return all(float(r[0]) < 0 for r in rows)
    except Exception:
        return False


# ── Division multiplier ───────────────────────────────────────────────────────

def _division_multiplier(division: str | None) -> float:
    try:
        from division_filter import get_filter
        return get_filter().stake_multiplier(division)
    except Exception:
        return 1.0


# ── Singles pool ──────────────────────────────────────────────────────────────

def _qualify_singles(analyses: list[dict]) -> list[dict]:
    """Return all qualifying moneyline rows, one per fight (best edge wins)."""
    best_per_fight: dict[str, dict] = {}

    for fight in analyses:
        # Skip if no-bet structural filter flagged this fight
        nobet = fight.get("nobet_filter")
        if nobet and nobet.get("action") == "PASS":
            continue

        division = fight.get("weight_class")
        div_mult = _division_multiplier(division)
        if div_mult == 0.0:
            continue  # division blocked

        for row in fight.get("markets", []):
            if row.get("market") != "Moneyline":
                continue
            if not row.get("decimal_odds") or row.get("decimal_odds") == "":
                continue
            edge_pct = row.get("edge")
            if edge_pct is None or float(edge_pct) < EDGE_MIN:
                continue
            if row.get("label") in {"Pass", "Avoid", "No Bet"}:
                continue

            fid = row.get("fight_id", "")
            row = dict(row, _division=division, _div_mult=div_mult)
            existing = best_per_fight.get(fid)
            if existing is None or float(row["edge"]) > float(existing["edge"]):
                best_per_fight[fid] = row

    return list(best_per_fight.values())


def _stake_singles(
    picks: list[dict],
    budget: float,
    max_per_bet: float,
) -> list[dict]:
    """
    Allocate `budget` across picks proportionally by edge × confidence weight.
    Applies division multiplier, then scales to exactly hit the budget.
    """
    if not picks or budget <= 0:
        return []

    # Raw weight = edge × confidence × division multiplier
    weights = []
    for p in picks:
        edge_w = float(p.get("edge") or 4.0)
        conf_w = CONFIDENCE_WEIGHTS.get(p.get("confidence", "Low"), 0.8)
        div_m  = float(p.get("_div_mult", 1.0))
        weights.append(edge_w * conf_w * div_m)

    total_w = sum(weights)
    staked: list[dict] = []
    actual_total = 0.0

    for p, w in zip(picks, weights):
        raw = (w / total_w) * budget
        stake = max(MIN_STAKE_EUR, min(max_per_bet, round(raw, 2)))
        actual_total += stake

    # Scale proportionally so total = budget
    scale = budget / actual_total if actual_total > 0 else 1.0

    for p, w in zip(picks, weights):
        raw   = (w / total_w) * budget * scale
        stake = max(MIN_STAKE_EUR, min(max_per_bet, round(raw, 2)))
        dec   = float(p["decimal_odds"])
        staked.append({
            **{k: v for k, v in p.items() if not k.startswith("_")},
            "stake": {
                "eur":         stake,
                "pct":         f"{stake / budget * 100:.1f}%",
                "pctValue":    round(stake / budget * 100, 1),
                "label":       "Proportional",
                "reportLabel": f"EUR {stake:.2f}",
            },
            "potential_return": round(stake * dec, 2),
            "profit_if_win":    round(stake * (dec - 1), 2),
            "decision":         "BET",
            "bet_type":         "single",
        })

    return staked


def candidate_singles(analyses: list[dict], bankroll: float, event_name: str = "") -> list[dict]:
    if _has_consecutive_losing_events(STOP_LOSS_EVENTS):
        print(f"[BANKROLL] Stop-loss: {STOP_LOSS_EVENTS} consecutive losing events. No bets.")
        return []

    is_numbered = is_numbered_card(event_name)
    max_per_bet = MAX_STAKE_EUR_NUMBERED if is_numbered else MAX_STAKE_EUR_FIGHT_NIGHT
    budget = round(bankroll * ALLOC_SINGLES, 2)

    picks = _qualify_singles(analyses)
    if not picks:
        return []

    return _stake_singles(picks, budget, max_per_bet)


# ── Method of victory bets ────────────────────────────────────────────────────

def _qualify_mov(analyses: list[dict]) -> list[dict]:
    """
    Pull method-of-victory rows from analyses (generated by betting_model.py).
    Falls back gracefully if the market isn't in the payload.
    """
    picks = []
    seen: set[str] = set()

    for fight in analyses:
        nobet = fight.get("nobet_filter")
        if nobet and nobet.get("action") == "PASS":
            continue

        division = fight.get("weight_class")
        div_mult = _division_multiplier(division)
        if div_mult == 0.0:
            continue

        for row in fight.get("markets", []):
            if row.get("market") not in {"Method - KO/TKO", "Method - Submission", "Method - Decision"}:
                continue
            if not row.get("decimal_odds") or row.get("decimal_odds") == "":
                continue
            edge_pct = row.get("edge")
            if edge_pct is None or float(edge_pct) < EDGE_MIN:
                continue
            if row.get("label") in {"Pass", "Avoid", "No Bet"}:
                continue

            key = f"{row.get('fight_id')}_{row.get('market')}_{row.get('selection')}"
            if key in seen:
                continue
            seen.add(key)
            picks.append(dict(row, _division=division, _div_mult=div_mult))

    picks.sort(key=lambda r: float(r.get("edge") or 0), reverse=True)
    return picks


def candidate_mov(analyses: list[dict], bankroll: float) -> list[dict]:
    if _has_consecutive_losing_events(STOP_LOSS_EVENTS):
        return []

    budget = round(bankroll * ALLOC_MOV, 2)
    picks  = _qualify_mov(analyses)
    if not picks:
        return []

    # Equal split across MoV picks, capped per bet
    n          = len(picks)
    per_bet    = max(MIN_STAKE_EUR, min(round(budget / n, 2), budget * 0.40))
    staked     = []

    for p in picks:
        div_m  = float(p.get("_div_mult", 1.0))
        stake  = round(per_bet * div_m, 2)
        stake  = max(MIN_STAKE_EUR, stake)
        dec    = float(p.get("decimal_odds", 2.0))
        staked.append({
            **{k: v for k, v in p.items() if not k.startswith("_")},
            "stake": {
                "eur":         stake,
                "pct":         f"{stake / bankroll * 100:.1f}%",
                "pctValue":    round(stake / bankroll * 100, 1),
                "label":       "MoV flat",
                "reportLabel": f"EUR {stake:.2f}",
            },
            "potential_return": round(stake * dec, 2),
            "profit_if_win":    round(stake * (dec - 1), 2),
            "decision":         "BET",
            "bet_type":         "mov",
        })

    return staked


# ── Accumulators ──────────────────────────────────────────────────────────────

def _leg_combinations(pool: list[dict], n_legs: int) -> list[list[dict]]:
    """All unique fight combinations (one pick per fight) of exactly n_legs."""
    # Group pool by fight_id, take best pick per fight
    by_fight: dict[str, dict] = {}
    for p in pool:
        fid = p.get("fight_id", "")
        if fid not in by_fight or float(p["edge"]) > float(by_fight[fid]["edge"]):
            by_fight[fid] = p
    legs = list(by_fight.values())
    return [list(combo) for combo in itertools.combinations(legs, n_legs)]


def build_accumulators(analyses: list[dict], bankroll: float) -> list[dict]:
    """
    Build 2-leg and 3-leg accumulators from High/Medium-confidence singles.

    Strategy:
      - Pool: all qualifying moneyline picks with High or Medium confidence
      - Prefer lower-odds legs (closer to even money) → more likely to land
      - Build up to 4 two-leg and 3 three-leg accumulators
      - Allocate budget evenly across all accumulators built
    """
    if _has_consecutive_losing_events(STOP_LOSS_EVENTS):
        return []

    budget = round(bankroll * ALLOC_ACCUM, 2)
    pool   = [
        p for p in _qualify_singles(analyses)
        if p.get("confidence") in MIN_CONFIDENCE_ACCUM
    ]

    if len(pool) < MIN_LEGS_PER_ACCUM:
        return []

    accumulators: list[dict] = []

    # Build 2-leg parlays (up to 4)
    combos_2 = _leg_combinations(pool, 2)
    # Sort by combined implied probability (more likely to hit = lower combined odds)
    combos_2.sort(key=lambda legs: math.prod(1 / float(l["decimal_odds"]) for l in legs), reverse=True)
    for legs in combos_2[:4]:
        combined_odds = round(math.prod(float(l["decimal_odds"]) for l in legs), 2)
        accumulators.append({"legs": legs, "n_legs": 2, "combined_odds": combined_odds})

    # Build 3-leg parlays (up to 3, only if pool has ≥5 picks)
    if len(pool) >= 5:
        combos_3 = _leg_combinations(pool, 3)
        combos_3.sort(key=lambda legs: math.prod(1 / float(l["decimal_odds"]) for l in legs), reverse=True)
        for legs in combos_3[:3]:
            combined_odds = round(math.prod(float(l["decimal_odds"]) for l in legs), 2)
            accumulators.append({"legs": legs, "n_legs": 3, "combined_odds": combined_odds})

    if not accumulators:
        return []

    stake_per = max(MIN_STAKE_EUR, round(budget / len(accumulators), 2))
    result    = []

    for acc in accumulators:
        legs    = acc["legs"]
        c_odds  = acc["combined_odds"]
        stake   = stake_per
        selections = " + ".join(l["selection"] for l in legs)
        fights     = " / ".join(l["fight"] for l in legs)
        result.append({
            "bet_type":         "accumulator",
            "n_legs":           acc["n_legs"],
            "fight":            fights,
            "market":           f"{acc['n_legs']}-leg accumulator",
            "selection":        selections,
            "legs":             [
                {
                    "fight":          l["fight"],
                    "selection":      l["selection"],
                    "decimal_odds":   l["decimal_odds"],
                    "edge":           l["edge"],
                    "confidence":     l["confidence"],
                    "model_probability": l.get("model_probability"),
                }
                for l in legs
            ],
            "decimal_odds":     c_odds,
            "combined_odds":    c_odds,
            "stake": {
                "eur":         stake,
                "pct":         f"{stake / bankroll * 100:.1f}%",
                "pctValue":    round(stake / bankroll * 100, 1),
                "label":       f"{acc['n_legs']}-leg parlay",
                "reportLabel": f"EUR {stake:.2f}",
            },
            "potential_return": round(stake * c_odds, 2),
            "profit_if_win":    round(stake * (c_odds - 1), 2),
            "decision":         "BET",
            "sportsbook":       legs[0].get("sportsbook", ""),
        })

    return result


# ── Full staking plan ─────────────────────────────────────────────────────────

def ensure_history() -> None:
    if BET_HISTORY_CSV.exists():
        return
    BET_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with BET_HISTORY_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "date", "bet_type", "fight", "market", "selection",
            "sportsbook", "odds", "stake_eur", "result", "pnl",
            "bankroll_before", "bankroll_after", "notes",
        ])
        writer.writeheader()


def build_staking_plan(analyses: list[dict], event_name: str = "") -> dict:
    bankroll   = card_bankroll(event_name)
    numbered   = is_numbered_card(event_name)
    card_label = "Numbered card (UFC NNN)" if numbered else "Fight Night"

    stop_loss  = _has_consecutive_losing_events(STOP_LOSS_EVENTS)
    singles    = candidate_singles(analyses, bankroll, event_name) if not stop_loss else []
    mov_bets   = candidate_mov(analyses, bankroll) if not stop_loss else []
    accums     = build_accumulators(analyses, bankroll) if not stop_loss else []

    total_singles  = round(sum(p["stake"]["eur"] for p in singles), 2)
    total_mov      = round(sum(p["stake"]["eur"] for p in mov_bets), 2)
    total_accums   = round(sum(a["stake"]["eur"] for a in accums), 2)
    total_exposure = round(total_singles + total_mov + total_accums, 2)

    ensure_history()
    return {
        "generated_at":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_name":          event_name,
        "card_type":           card_label,
        "bankroll":            bankroll,
        "is_numbered_card":    numbered,
        "stop_loss_active":    stop_loss,
        "budget_singles":      round(bankroll * ALLOC_SINGLES, 2),
        "budget_mov":          round(bankroll * ALLOC_MOV, 2),
        "budget_accumulators": round(bankroll * ALLOC_ACCUM, 2),
        "staking_rules": [
            {"rule": "Fight Night bankroll",    "value": f"EUR {BANKROLL_FIGHT_NIGHT:.0f}"},
            {"rule": "Numbered card bankroll",  "value": f"EUR {BANKROLL_NUMBERED:.0f}"},
            {"rule": "Singles allocation",      "value": f"{ALLOC_SINGLES*100:.0f}% of bankroll"},
            {"rule": "Method of victory",       "value": f"{ALLOC_MOV*100:.0f}% of bankroll"},
            {"rule": "Accumulators",            "value": f"{ALLOC_ACCUM*100:.0f}% of bankroll"},
            {"rule": "Minimum edge",            "value": f"{EDGE_MIN}%"},
            {"rule": "Stop-loss",               "value": f"No bets after {STOP_LOSS_EVENTS} consecutive losing events"},
        ],
        "singles":             singles,
        "mov_bets":            mov_bets,
        "accumulators":        accums,
        "total_single_stake":      total_singles,
        "total_mov_stake":         total_mov,
        "total_accumulator_stake": total_accums,
        "total_card_exposure":     total_exposure,
        "total_card_exposure_pct": round(total_exposure / bankroll * 100, 1) if bankroll > 0 else 0.0,
    }


def save_staking_plan(plan: dict) -> None:
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    save_json(plan, STAKING_JSON)

    all_bets = (
        plan.get("singles", []) +
        plan.get("mov_bets", []) +
        plan.get("accumulators", [])
    )
    fields = [
        "bet_type", "fight", "market", "selection", "sportsbook",
        "decimal_odds", "model_probability", "edge", "confidence",
        "stake_eur", "potential_return", "profit_if_win",
    ]
    with STAKING_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for bet in all_bets:
            writer.writerow({
                "bet_type":          bet.get("bet_type", "single"),
                "fight":             bet.get("fight", ""),
                "market":            bet.get("market", ""),
                "selection":         bet.get("selection", ""),
                "sportsbook":        bet.get("sportsbook", ""),
                "decimal_odds":      bet.get("decimal_odds", ""),
                "model_probability": bet.get("model_probability", ""),
                "edge":              bet.get("edge", ""),
                "confidence":        bet.get("confidence", ""),
                "stake_eur":         bet.get("stake", {}).get("eur", ""),
                "potential_return":  bet.get("potential_return", ""),
                "profit_if_win":     bet.get("profit_if_win", ""),
            })


def load_history() -> list[dict]:
    ensure_history()
    with BET_HISTORY_CSV.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))
