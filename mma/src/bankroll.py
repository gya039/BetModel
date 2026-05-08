"""
bankroll.py - staking, accumulator, and bet-history export helpers.

Bankroll state is owned by ledger.py.  This module is responsible for:
  - staking tier calculations (Decimal throughout)
  - accumulator construction
  - staking plan assembly
  - CSV / JSON export (write-only, never read for runtime state)
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ledger import current_bankroll, migrate_from_csv, money
from utils import DATA_PROC, save_json

MAX_SINGLE_EXPOSURE_PCT = Decimal("0.50")
BETTING_DIR  = DATA_PROC / "betting"
STAKING_JSON = BETTING_DIR / "staking_plan.json"
STAKING_CSV  = BETTING_DIR / "staking_plan.csv"
BET_HISTORY_CSV = BETTING_DIR / "bet_history.csv"


# ── Staking tiers (Decimal throughout) ────────────────────────────────────────

def stake_tier(edge_pct: float | None, confidence: str, bankroll: Decimal) -> dict:
    """
    MMA staking profile. bankroll must be a Decimal.
    Returns eur as float only at the JSON/display boundary.
    """
    if edge_pct is None or edge_pct < 3:
        pct   = Decimal("0")
        label = "Pass"
    elif edge_pct < 6:
        pct   = Decimal("0.03")
        label = "Small"
    elif edge_pct < 10:
        pct   = Decimal("0.05")
        label = "Standard"
    elif edge_pct < 15:
        pct   = Decimal("0.07")
        label = "Strong"
    else:
        pct   = Decimal("0.10")
        label = "Max"

    if confidence == "Low" and pct > Decimal("0.03"):
        pct   = Decimal("0.03")
        label = "Capped"
    if confidence == "Low-Medium" and pct > Decimal("0.05"):
        pct   = Decimal("0.05")
        label = "Capped"

    stake    = money(bankroll * pct)
    pct_disp = f"{pct * 100:.1f}%".replace(".0%", "%")
    return {
        "pct":         pct_disp,
        "pctValue":    float(pct * 100),
        "eur":         float(stake),          # float only at display boundary
        "label":       label,
        "reportLabel": f"{pct_disp} (EUR {stake:.2f})",
    }


# ── Accumulator helpers ────────────────────────────────────────────────────────

def is_acca_market(row: dict) -> bool:
    market = str(row.get("market", ""))
    return (
        market == "Moneyline"
        or " by KO/TKO" in market
        or " by Submission" in market
        or " by Decision" in market
    )


def candidate_singles(analyses: list[dict], bankroll: Decimal) -> list[dict]:
    picks: list[dict] = []
    used_fights: set  = set()
    exposure_cap      = money(bankroll * MAX_SINGLE_EXPOSURE_PCT)
    current_exposure  = Decimal("0.00")

    all_rows = [row for fight in analyses for row in fight.get("markets", [])]
    priced = [
        row for row in all_rows
        if row.get("decimal_odds") not in ("", None)
        and row.get("edge") is not None
        and row.get("edge") >= 3
        and row.get("label") not in {"Pass", "Avoid"}
    ]
    priced.sort(
        key=lambda r: (r.get("edge") or 0, r.get("model_probability") or 0),
        reverse=True,
    )

    for row in priced:
        fight_id = row.get("fight_id")
        if fight_id in used_fights:
            continue
        tier = stake_tier(row.get("edge"), row.get("confidence", "Low"), bankroll)
        if tier["eur"] <= 0:
            continue
        stake_d = money(Decimal(str(tier["eur"])))
        if current_exposure + stake_d > exposure_cap:
            continue
        used_fights.add(fight_id)
        current_exposure = money(current_exposure + stake_d)
        picks.append({
            **row,
            "stake":            tier,
            "potential_return": float(money(stake_d * Decimal(str(row["decimal_odds"])))),
            "profit_if_win":    float(money(stake_d * (Decimal(str(row["decimal_odds"])) - Decimal("1")))),
            "decision":         "BET",
        })
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
    rows.sort(
        key=lambda r: (r.get("edge") or 0, r.get("model_probability") or 0),
        reverse=True,
    )
    row = rows[0]
    return {
        "fight_id":         row.get("fight_id"),
        "fight":            row.get("fight"),
        "label":            f"{row.get('selection')} ({row.get('market')})",
        "market":           row.get("market"),
        "selection":        row.get("selection"),
        "sportsbook":       row.get("sportsbook"),
        "odds":             round(float(row.get("decimal_odds")), 2),
        "edge":             round(float(row.get("edge")), 1),
        "model_probability": row.get("model_probability"),
    }


def build_accumulators(analyses: list[dict], bankroll: Decimal) -> list[dict]:
    legs: list[dict] = []
    for fight in analyses:
        leg = best_acca_leg_for_fight(fight)
        if leg:
            legs.append(leg)
    legs.sort(key=lambda l: (l["edge"], l["model_probability"]), reverse=True)

    def combined_odds(sel: list[dict]) -> Decimal:
        result = Decimal("1")
        for leg in sel:
            result *= Decimal(str(leg["odds"]))
        return money(result)

    def combined_prob(sel: list[dict]) -> Decimal:
        result = Decimal("1")
        for leg in sel:
            result *= Decimal(str(leg["model_probability"]))
        return result.quantize(Decimal("0.0001"))

    accas: list[dict] = []
    for type_name, count, stake_pct_str, min_edge in [
        ("Double", 2, "0.03", 3),
        ("Treble", 3, "0.02", 4),
        ("4-Fold", 4, "0.01", 5),
    ]:
        eligible = [l for l in legs if l["edge"] >= min_edge]
        if len(eligible) < count:
            continue
        sel         = eligible[:count]
        odds        = combined_odds(sel)
        stake_pct_d = Decimal(stake_pct_str)
        stake       = money(bankroll * stake_pct_d)
        model_prob  = combined_prob(sel)
        market_prob = (Decimal("1") / odds).quantize(Decimal("0.0001")) if odds else None
        comb_edge   = (
            float(money((model_prob - market_prob) * 100)) if market_prob else None
        )
        accas.append({
            "type":              type_name,
            "legs":              sel,
            "combined_odds":     float(odds),
            "model_probability": float(model_prob),
            "market_probability": float(market_prob) if market_prob else None,
            "combined_edge":     comb_edge,
            "stake":             float(stake),
            "stake_pct":         float(stake_pct_d * 100),
            "potential_return":  float(money(stake * odds)),
            "profit_if_win":     float(money(stake * (odds - Decimal("1")))),
        })
    return accas


# ── Staking plan ───────────────────────────────────────────────────────────────

def build_staking_plan(analyses: list[dict]) -> dict:
    """
    Build the staking plan. Bankroll is always derived from the ledger — never
    from CSV, never from a module-level variable.
    """
    bankroll     = current_bankroll()
    singles      = candidate_singles(analyses, bankroll)
    accumulators = build_accumulators(analyses, bankroll)
    _ensure_history_file()

    return {
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bankroll":      float(bankroll),
        "base_bankroll": float(money("500.00")),
        "staking_rules": [
            {"edge": "3-6%",             "stake": "3%"},
            {"edge": "6-10%",            "stake": "5%"},
            {"edge": "10-15%",           "stake": "7%"},
            {"edge": "15%+",             "stake": "10%"},
            {"edge": "Low confidence",   "stake": "cap 3%"},
            {"edge": "Card singles cap", "stake": "50% max"},
        ],
        "singles":                singles,
        "accumulators":           accumulators,
        "total_single_stake":     float(money(sum(Decimal(str(p["stake"]["eur"])) for p in singles))),
        "total_accumulator_stake": float(money(sum(Decimal(str(a["stake"])) for a in accumulators))),
    }


# ── Export helpers (write-only, never read for runtime state) ──────────────────

def save_staking_plan(plan: dict) -> None:
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    save_json(plan, STAKING_JSON)

    fields = [
        "fight", "market", "selection", "sportsbook", "decimal_odds",
        "model_probability", "edge", "confidence",
        "stake_pct", "stake_eur", "potential_return", "profit_if_win",
    ]
    with STAKING_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for pick in plan["singles"]:
            writer.writerow({
                "fight":             pick.get("fight"),
                "market":            pick.get("market"),
                "selection":         pick.get("selection"),
                "sportsbook":        pick.get("sportsbook"),
                "decimal_odds":      pick.get("decimal_odds"),
                "model_probability": pick.get("model_probability"),
                "edge":              pick.get("edge"),
                "confidence":        pick.get("confidence"),
                "stake_pct":         pick.get("stake", {}).get("pct"),
                "stake_eur":         pick.get("stake", {}).get("eur"),
                "potential_return":  pick.get("potential_return"),
                "profit_if_win":     pick.get("profit_if_win"),
            })


def load_history() -> list[dict]:
    """Read bet history CSV for display/export only. Not used for bankroll state."""
    _ensure_history_file()
    with BET_HISTORY_CSV.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def run_csv_migration() -> dict:
    """
    Migrate existing bet_history.csv into the ledger.
    Safe to call multiple times — idempotent by design.
    """
    return migrate_from_csv(BET_HISTORY_CSV)


def _ensure_history_file() -> None:
    """Create the CSV export file with headers if it does not exist."""
    if BET_HISTORY_CSV.exists():
        return
    BET_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with BET_HISTORY_CSV.open("w", encoding="utf-8", newline="") as fh:
        csv.DictWriter(fh, fieldnames=[
            "date", "bet_type", "fight", "market", "selection", "sportsbook",
            "odds", "stake_eur", "result", "pnl", "bankroll_before", "bankroll_after", "notes",
        ]).writeheader()
