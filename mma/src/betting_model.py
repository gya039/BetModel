"""
betting_model.py - explainable UFC betting engine for Octagon IQ.

Probability model (priority order):
  1. EnsemblePredictor (ensemble.py) — dynamic weights across ML, Elo,
     heuristic, and market components. Weights update automatically after
     each event via Brier-score inverse weighting.

  2. Heuristic fallback — when no trained model exists.
     Raw score differential via sigmoid, hard-capped 25-75%,
     blended 60/40 with market implied probability.

Both paths:
  - Apply hard probability caps [25%, 75%]
  - Apply 60/40 market shrinkage
  - Generate moneyline markets only (props/totals disabled)
  - NoBetClassifier screens structural risk before edge is computed
  - Every prediction passing edge filter is logged to the model audit log

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
    EDGE_MIN_PCT,
    american_to_decimal,
    american_to_implied,
    classify_value,
    confidence_from_margin,
    edge,
    implied_to_american,
)

# ── Lazy-loaded singletons ────────────────────────────────────────────────────
_ENSEMBLE: object | None = None
_NOBET_CLF: object | None = None


def _get_ensemble():
    global _ENSEMBLE
    if _ENSEMBLE is None:
        try:
            from ensemble import get_predictor
            _ENSEMBLE = get_predictor()
        except Exception as exc:
            print(f"[Model] Ensemble load failed ({exc}) — using heuristic fallback.")
    return _ENSEMBLE


def _get_nobet_clf():
    global _NOBET_CLF
    if _NOBET_CLF is None:
        try:
            from nobet_classifier import get_classifier
            _NOBET_CLF = get_classifier()
        except Exception:
            pass
    return _NOBET_CLF

BETTING_DIR = DATA_PROC / "betting"
EDGES_JSON = BETTING_DIR / "betting_edges.json"
EDGES_CSV = BETTING_DIR / "betting_edges.csv"

# Probability caps — never allow extreme predictions regardless of score gap
PROB_MIN = 0.25
PROB_MAX = 0.75


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


def _build_ml_features(fighter_a: dict, fighter_b: dict) -> tuple[dict | None, dict | None]:
    """Build ML feature dicts for both fighters. Returns (feat_a, feat_b) or (None, None)."""
    try:
        from features import compute_fighter_features
        from elo import build_elo_system
        from datetime import date as _date

        mini_fighters = [fighter_a, fighter_b]
        elo_sys = build_elo_system(mini_fighters)
        elo_a = elo_sys.rating(fighter_a.get("fighter_id", ""))
        elo_b = elo_sys.rating(fighter_b.get("fighter_id", ""))

        today     = _date.today()
        history_a = list(reversed(fighter_a.get("fight_history", [])))
        history_b = list(reversed(fighter_b.get("fight_history", [])))

        feat_a = compute_fighter_features(
            fighter_a, history_a, today, division=None, elo=elo_a
        )
        feat_b = compute_fighter_features(
            fighter_b, history_b, today, division=None, elo=elo_b
        )
        return feat_a, feat_b
    except Exception as exc:
        print(f"[Model] Feature extraction failed ({exc})")
        return None, None


def side_probabilities(
    fighter_a: dict,
    fighter_b: dict,
    market_implied_a: float | None = None,
    division: str | None = None,
) -> tuple[float, float, float, dict | None, dict | None]:
    """
    Compute P(fighter_a wins) using the adaptive ensemble.

    Ensemble path:
      - Combines ML, Elo, heuristic, and market components
      - Weights are loaded from disk and updated after each event
      - Returns component breakdown and applied weights

    Heuristic fallback:
      - Score differential → sigmoid → market shrinkage
      - Hard caps: [PROB_MIN, PROB_MAX] throughout

    Returns (prob_a, prob_b, score_diff, feat_a, feat_b)
    feat_a / feat_b are the feature dicts used (for no-bet screening), or None.
    """
    score_a = stat_rating(fighter_a) + matchup_adjustment(fighter_a, fighter_b)
    score_b = stat_rating(fighter_b) + matchup_adjustment(fighter_b, fighter_a)
    diff = score_a - score_b

    # ── Try ensemble predictor ────────────────────────────────────────────────
    feat_a, feat_b = _build_ml_features(fighter_a, fighter_b)
    ensemble = _get_ensemble()

    if ensemble is not None and feat_a is not None:
        try:
            result = ensemble.predict(
                feat_a=feat_a,
                feat_b=feat_b,
                fighter_a=fighter_a,
                fighter_b=fighter_b,
                market_implied_a=market_implied_a,
                division=division,
            )
            final_prob = clamp(result["prob"], PROB_MIN, PROB_MAX)
            return round(final_prob, 4), round(1 - final_prob, 4), round(diff, 2), feat_a, feat_b
        except Exception as exc:
            print(f"[Model] Ensemble inference failed ({exc}) — using heuristic fallback.")

    # ── Heuristic fallback ────────────────────────────────────────────────────
    raw_prob = 1 / (1 + math.exp(-diff / 12))
    raw_prob = clamp(raw_prob, PROB_MIN, PROB_MAX)
    heuristic_prob = raw_prob * 0.7 + 0.5 * 0.3
    model_prob = clamp(heuristic_prob, PROB_MIN, PROB_MAX)

    if market_implied_a is not None:
        final_prob = model_prob * 0.6 + market_implied_a * 0.4
    else:
        final_prob = model_prob

    final_prob = clamp(final_prob, PROB_MIN, PROB_MAX)
    return round(final_prob, 4), round(1 - final_prob, 4), round(diff, 2), feat_a, feat_b


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


# ── Method of victory helpers ─────────────────────────────────────────────────

# Odds API market keys for method-of-victory markets (checked in order)
_MOV_MARKET_KEYS = [
    "method_of_victory",
    "fight_result",
    "winning_method",
    "mma_method_of_victory",
]

_MOV_METHOD_LABELS = {
    "ko":  "Method - KO/TKO",
    "sub": "Method - Submission",
    "dec": "Method - Decision",
}

# Min finish-rate thresholds before we consider a MoV bet on that method
_MIN_KO_RATE  = 0.35   # fighter KOs >= 35% of wins
_MIN_SUB_RATE = 0.30   # fighter subs >= 30% of wins
_MIN_DEC_RATE = 0.50   # fighter decisions >= 50% of wins (point fighter)


def _fighter_mov_probs(fighter: dict) -> dict[str, float]:
    """
    Estimate P(fighter wins by KO/TKO), P(sub), P(dec) from career stats.
    These are conditional on the fighter winning — we multiply by win_prob later.
    """
    def pct_stat(key: float) -> float:
        try:
            return float(fighter.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    ko_pct  = pct_stat("wins_by_ko_tko_pct")
    sub_pct = pct_stat("wins_by_sub_pct")
    dec_pct = pct_stat("wins_by_dec_pct")

    # Normalise so they sum to 1 (ignore draws/DQ)
    total = ko_pct + sub_pct + dec_pct
    if total <= 0:
        return {"ko": 0.33, "sub": 0.33, "dec": 0.34}
    return {
        "ko":  ko_pct  / total,
        "sub": sub_pct / total,
        "dec": dec_pct / total,
    }


def _best_mov_price(prices: list[dict], fighter_name: str, method_key: str) -> dict | None:
    """Find the best-priced MoV outcome for a fighter+method across all bookmakers."""
    candidates = []
    for p in prices:
        if p.get("market_key") not in _MOV_MARKET_KEYS:
            continue
        sel = p.get("selection", "")
        # Selection strings vary: "Jones by KO", "Jones KO/TKO", "Jones - KO"
        name_match = names_match(fighter_name, sel.split(" by ")[0].split(" - ")[0].strip())
        method_match = method_key.lower() in sel.lower().replace("/", " ").replace("-", " ")
        if name_match and method_match:
            candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: float(p.get("odds") or -9999))


def method_of_victory_rows(
    fight_id: str,
    fight_name: str,
    fighter_a: dict,
    fighter_b: dict,
    prob_a: float,
    prob_b: float,
    prices: list[dict],
) -> list[dict]:
    """
    Generate method-of-victory market rows for both fighters.
    Only produces rows when:
      1. The fighter has a strong tendency toward that finish type
      2. Odds are available for that method
      3. The model edge is >= EDGE_MIN
    """
    rows = []

    for fighter, win_prob in ((fighter_a, prob_a), (fighter_b, prob_b)):
        mov_probs = _fighter_mov_probs(fighter)
        name = fighter.get("name", "")

        for method_key, market_label in _MOV_METHOD_LABELS.items():
            method_prob_given_win = mov_probs.get(method_key, 0.0)

            # Skip if fighter doesn't have a strong tendency for this method
            threshold = {"ko": _MIN_KO_RATE, "sub": _MIN_SUB_RATE, "dec": _MIN_DEC_RATE}[method_key]
            if method_prob_given_win < threshold:
                continue

            # P(fighter wins by this method) = P(win) × P(method | win)
            model_prob = round(win_prob * method_prob_given_win, 4)
            model_prob = clamp(model_prob, 0.05, 0.75)

            # Find odds
            search_term = {"ko": "KO", "sub": "submission", "dec": "decision"}[method_key]
            price = _best_mov_price(prices, name, search_term)
            if not price:
                continue

            implied    = american_to_implied(price.get("odds"))
            dec_odds   = american_to_decimal(price.get("odds"))
            edge_pct   = edge(model_prob, implied)

            if edge_pct is None or edge_pct < EDGE_MIN:
                continue

            label = classify_value(
                edge_pct=edge_pct,
                confidence="Medium",
                has_price=True,
                decimal_odds=dec_odds,
            )

            rows.append({
                "fight_id":           fight_id,
                "fight":              fight_name,
                "market":             market_label,
                "selection":          name,
                "sportsbook":         price.get("sportsbook", ""),
                "odds":               price.get("odds", ""),
                "decimal_odds":       dec_odds,
                "implied_probability": implied,
                "model_probability":  model_prob,
                "fair_odds":          implied_to_american(model_prob),
                "edge":               edge_pct,
                "confidence":         "Medium",
                "label":              label,
                "rationale": (
                    f"{name} has {method_prob_given_win:.0%} of wins by "
                    f"{market_label.split(' - ')[1]}. "
                    f"Model P(win by method)={model_prob:.1%}."
                ),
            })

    return rows


def moneyline_row(
    fight_id: str,
    fight_name: str,
    fighter: dict,
    model_probability: float,
    confidence: str,
    explanation: str,
    price: dict | None,
    score_diff: float,
    total_fights: int,
    market_implied: float | None,
) -> dict:
    """Build a single moneyline market row with full calibration metadata."""
    implied = american_to_implied(price.get("odds")) if price else None
    decimal_odds = american_to_decimal(price.get("odds")) if price else None
    edge_pct = edge(model_probability, implied)

    # Refine confidence with market disagreement and odds context
    confidence = confidence_from_margin(
        margin=score_diff,
        sample_size=total_fights,
        market_edge_pct=edge_pct,
        decimal_odds=decimal_odds,
    )

    label = classify_value(
        edge_pct=edge_pct,
        confidence=confidence,
        has_price=bool(price),
        decimal_odds=decimal_odds,
    )

    return {
        "fight_id": fight_id,
        "fight": fight_name,
        "market": "Moneyline",
        "selection": fighter["name"],
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
    division  = matchup.get("weight_class")

    # Extract market odds FIRST — needed for probability shrinkage
    odds_event = find_odds_event(odds_payload, fighter_a, fighter_b)
    prices = extract_prices(odds_event)

    price_a = best_price_for(prices, "h2h", fighter_a["name"])
    price_b = best_price_for(prices, "h2h", fighter_b["name"])
    market_implied_a = american_to_implied(price_a.get("odds")) if price_a else None
    decimal_odds_a   = american_to_decimal(price_a.get("odds")) if price_a else None

    # Probabilities via adaptive ensemble (or heuristic fallback)
    prob_a, prob_b, diff, feat_a, feat_b = side_probabilities(
        fighter_a, fighter_b, market_implied_a, division=division
    )

    # ── No-bet structural risk filter ────────────────────────────────────────
    nobet_decision = None
    nobet_clf = _get_nobet_clf()
    if nobet_clf is not None and feat_a is not None and feat_b is not None:
        try:
            nobet_decision = nobet_clf.evaluate(
                feat_a=feat_a,
                feat_b=feat_b,
                model_prob=prob_a,
                market_implied=market_implied_a,
                current_odds=decimal_odds_a,
            )
        except Exception:
            pass

    favorite = fighter_a if prob_a >= prob_b else fighter_b
    underdog = fighter_b if prob_a >= prob_b else fighter_a
    favorite_prob = max(prob_a, prob_b)
    underdog_prob = min(prob_a, prob_b)
    favorite_diff = diff if favorite is fighter_a else -diff

    total_fights = int(
        num(fighter_a.get("total_fights", 0)) + num(fighter_b.get("total_fights", 0))
    )

    fight_id = event_fingerprint(fighter_a, fighter_b)
    fight_name = f"{fighter_a['name']} vs {fighter_b['name']}"
    favorite_method = best_method(favorite, underdog)
    fight_script = explain_fight_script(favorite, underdog, favorite_method)

    explanation = (
        "Side probability from matchup score after market shrinkage (60% model / 40% market). "
        "Hard capped at 25-75%."
    )

    row_a = moneyline_row(
        fight_id=fight_id,
        fight_name=fight_name,
        fighter=fighter_a,
        model_probability=prob_a,
        confidence="Low",  # will be refined inside moneyline_row
        explanation=explanation,
        price=price_a,
        score_diff=diff,
        total_fights=total_fights,
        market_implied=market_implied_a,
    )
    row_b = moneyline_row(
        fight_id=fight_id,
        fight_name=fight_name,
        fighter=fighter_b,
        model_probability=prob_b,
        confidence="Low",
        explanation=explanation,
        price=price_b,
        score_diff=-diff,
        total_fights=total_fights,
        market_implied=(1 - market_implied_a) if market_implied_a is not None else None,
    )

    mov_rows = method_of_victory_rows(
        fight_id, fight_name, fighter_a, fighter_b, prob_a, prob_b, prices
    )
    rows = [row_a, row_b] + mov_rows
    priced_rows = [r for r in rows if r.get("market") == "Moneyline" and r.get("odds") != ""]
    best_priced = sorted(
        priced_rows,
        key=lambda r: r.get("edge") if r.get("edge") is not None else -999,
        reverse=True,
    )

    # Confidence from best priced row (or fallback)
    confidence = best_priced[0]["confidence"] if best_priced else row_a["confidence"]
    best_side = fighter_a["name"] if prob_a >= prob_b else fighter_b["name"]

    # Override best_bet to None if no-bet classifier flagged this fight
    nobet_flagged = nobet_decision is not None and nobet_decision.action == "PASS"
    best_bet_row = (
        best_priced[0]
        if (best_priced
            and best_priced[0]["label"] not in {"Pass", "Avoid", "No Bet"}
            and not nobet_flagged)
        else None
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
        "confidence": confidence,
        "playable_label": (
            "No Bet (risk filter)"
            if nobet_flagged
            else (best_priced[0]["label"] if best_priced else "No Bet")
        ),
        "best_priced_edge": best_priced[0]["edge"] if best_priced else None,
        "best_bet": best_bet_row,
        "nobet_filter": nobet_decision.as_dict() if nobet_decision else None,
        "betting_breakdown": {
            "edge": f"{best_side} is the model side at {favorite_prob:.1%} (ensemble probability).",
            "fight_script": fight_script,
            "underdog_path": underdog_path(underdog, favorite),
            "favorite_danger": "; ".join(vulnerabilities(favorite)[:2]),
            "rationale": (
                "Model probability from adaptive ensemble (ML + Elo + heuristic + market). "
                "Hard capped at 25-75%. Confidence is multi-factor. "
                + (f"No-bet filter: {nobet_decision.action} (score={nobet_decision.risk_score:.1f})."
                   if nobet_decision else "")
            ),
        },
        "markets": rows,
        "odds_available": bool(prices),
        # Props and totals disabled — model only bets moneylines until calibration is proven
        "props_disabled": True,
        "totals_disabled": True,
    }
    return summary


def generate_card_betting(odds_path: Path | None = None) -> list[dict]:
    matchups = load_json(DATA_PROC / "matchup_summary.json")
    odds_payload = load_odds(odds_path)
    analyses = [analyze_matchup(matchup, odds_payload) for matchup in matchups]
    return analyses


def _event_meta() -> dict:
    """Return slug + card metadata from card.json for archive labelling."""
    import re
    try:
        card = load_json(DATA_RAW / "card.json")
        name     = card.get("event_name", "")
        date_str = card.get("event_date", "")
        url      = card.get("event_url", "")
        from datetime import datetime as _dt
        try:
            date_slug = _dt.strptime(date_str, "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            date_slug = date_str.replace(" ", "-").replace(",", "")
        short = name.split(":")[-1].strip() if ":" in name else name
        short = re.sub(r"[^\w\s-]", "", short).strip()
        short = re.sub(r"\s+", "-", short).lower()[:40]
        return {
            "slug":        f"{date_slug}_{short}",
            "event_name":  name,
            "event_date":  date_str,
            "event_url":   url,
        }
    except Exception:
        from datetime import datetime as _dt
        return {"slug": _dt.utcnow().strftime("%Y-%m-%dT%H%M%SZ"),
                "event_name": "", "event_date": "", "event_url": ""}


def save_outputs(analyses: list[dict]) -> None:
    from ledger import (
        blocks_new_generation,
        register_event,
        place_bets,
        pipeline_lock,
    )

    meta = _event_meta()

    with pipeline_lock("generate_card", ttl_seconds=600):

        blocked, blocker = blocks_new_generation(meta["event_url"])
        if blocked:
            name = blocker.get("event_name", "unknown") if blocker else "unknown"
            eid  = blocker.get("event_id", "")           if blocker else ""
            raise RuntimeError(
                f"\n\n  ⛔  UNRESOLVED EVENT BLOCKING GENERATION\n"
                f"  Event  : {name}  (status: {blocker.get('status','?')})\n"
                f"  Settle : http://localhost:5002/settle/{eid}\n\n"
                f"  Settle or archive the previous card first.\n"
            )

        BETTING_DIR.mkdir(parents=True, exist_ok=True)
        save_json(analyses, EDGES_JSON)

        # CSV — moneyline markets only
        rows = [row for fight in analyses for row in fight["markets"]]
        fieldnames = [
            "fight", "market", "selection", "sportsbook", "odds", "decimal_odds",
            "implied_probability", "model_probability", "edge", "confidence",
            "label", "rationale",
        ]
        with EDGES_CSV.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: row.get(field, "") for field in fieldnames})

        slug = meta["slug"]
        plan = build_staking_plan(analyses, event_name=meta["event_name"])
        plan["event_date"] = meta["event_date"]
        plan["event_url"]  = meta["event_url"]
        save_staking_plan(plan)

        archive_edges = BETTING_DIR / f"betting_edges_{slug}.json"
        archive_plan  = BETTING_DIR / f"staking_plan_{slug}.json"
        save_json(analyses, archive_edges)
        save_json(plan, archive_plan)
        print(f"Archived: {archive_edges.name}")
        print(f"Archived: {archive_plan.name}")

        try:
            card_raw = None
            try:
                card_raw = load_json(DATA_RAW / "card.json")
            except Exception:
                pass

            event_id = register_event(
                event_name=meta["event_name"],
                event_date=meta["event_date"],
                event_url=meta["event_url"],
                card_json=card_raw,
                edges_json=analyses,
            )

            from ledger import _bet_id as _mk_bid
            for pick in plan.get("singles", []):
                pick["bet_id"] = _mk_bid(
                    event_id,
                    pick.get("fight_id"),
                    pick.get("market", ""),
                    pick.get("selection", ""),
                )
            save_staking_plan(plan)

            all_singles = plan.get("singles", []) + plan.get("mov_bets", [])
            placed = place_bets(
                event_id=event_id,
                singles=all_singles,
                accumulators=plan.get("accumulators", []),
                bankroll=plan.get("bankroll", 500.0),
            )
            print(f"Ledger: registered event '{meta['event_name']}' ({event_id})")
            print(f"Ledger: placed {len(placed)} bet(s) as pending")

            # Log predictions to calibration module for tracking
            try:
                import calibration as _cal
                _cal.ensure_tables()
                for pick in plan.get("singles", []):
                    _cal.log_prediction(
                        bet_id=pick.get("bet_id", ""),
                        event_id=event_id,
                        fight=pick.get("fight", ""),
                        market=pick.get("market", "Moneyline"),
                        selection=pick.get("selection", ""),
                        model_probability=float(pick.get("model_probability") or 0),
                        market_probability=float(pick.get("implied_probability") or 0) or None,
                        edge_pct=float(pick.get("edge") or 0) or None,
                        confidence=pick.get("confidence"),
                    )
            except Exception as exc:
                print(f"[WARN] Calibration log failed (non-fatal): {exc}")

            # Audit log via model registry
            try:
                from model_registry import get_registry
                _reg = get_registry()
                for analysis in analyses:
                    fid  = analysis.get("fight_id", "")
                    fa   = analysis.get("fighter_a", {}).get("name", "?")
                    fb   = analysis.get("fighter_b", {}).get("name", "?")
                    mp   = analysis.get("model_side_probability")
                    div  = analysis.get("weight_class")
                    conf = analysis.get("confidence", "")
                    best = analysis.get("best_bet")
                    edge_val = analysis.get("best_priced_edge")
                    mkt  = analysis.get("markets", [{}])[0].get("implied_probability")
                    if best and edge_val and mp:
                        _reg.log_prediction(
                            fight_id=fid,
                            fighter_a=fa,
                            fighter_b=fb,
                            model_prob=mp,
                            edge=edge_val,
                            confidence=conf,
                            market_prob=mkt,
                            division=div,
                        )
            except Exception as exc:
                print(f"[WARN] Registry audit log failed (non-fatal): {exc}")

        except RuntimeError:
            raise
        except Exception as exc:
            print(f"[WARN] Ledger write failed (non-fatal): {exc}")


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
