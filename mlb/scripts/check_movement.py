"""
Check line movement for a given date's picks.

Compares odds stored in the predictions JSON (at time of prediction)
against current live odds from The Odds API, and checks the MLB
transactions API for recent IL placements (injury flags).

At the end it saves an *_updated.json with recalculated edges and stakes
using current odds — this is used by record_results.py --variant updated
to track performance of the afternoon-checked picks separately.

Usage:
    python mlb/scripts/check_movement.py
    python mlb/scripts/check_movement.py --date 2026-04-15
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests

# Load .env from repo root
_env_path = Path(__file__).parent.parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

MLB_API = "https://statsapi.mlb.com/api/v1"
SEASON = 2026

ODDS_TEAM_MAP = {
    "Arizona Diamondbacks": "AZ",
    "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",
    "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",
    "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",
    "Detroit Tigers": "DET",
    "Houston Astros": "HOU",
    "Kansas City Royals": "KC",
    "Los Angeles Angels": "LAA",
    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",
    "New York Mets": "NYM",
    "New York Yankees": "NYY",
    "Oakland Athletics": "ATH",
    "Athletics": "ATH",
    "Philadelphia Phillies": "PHI",
    "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SD",
    "San Francisco Giants": "SF",
    "Seattle Mariners": "SEA",
    "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TB",
    "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSH",
}

PREFERRED_BOOKMAKERS = {"paddypower", "skybet", "boylesports"}
UK_BOOKMAKERS = {
    "sport888", "betfair_ex_uk", "betfair_sb_uk", "betvictor", "betway",
    "boylesports", "casumo", "coral", "grosvenor", "ladbrokes_uk", "leovegas",
    "livescorebet", "matchbook", "paddypower", "skybet", "smarkets",
    "unibet_uk", "virginbet", "williamhill",
}

# Abbreviation -> full team name (reverse of ODDS_TEAM_MAP)
ABBR_TO_FULL = {v: k for k, v in ODDS_TEAM_MAP.items() if k != "Athletics"}


def no_vig_probs(home_odds: float | None, away_odds: float | None) -> tuple[float | None, float | None]:
    if not home_odds or not away_odds or home_odds <= 1 or away_odds <= 1:
        return None, None
    h_raw = 1.0 / home_odds
    a_raw = 1.0 / away_odds
    total = h_raw + a_raw
    if total <= 0:
        return None, None
    return round(h_raw / total, 4), round(a_raw / total, 4)


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def find_predictions_json(target_date: str) -> Path | None:
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    month_folder = f"{dt.strftime('%B')} Predictions"
    day_folder = f"{dt.strftime('%B')} {ordinal(dt.day)}"
    file_stub = f"{dt.strftime('%B')} {ordinal(dt.day)} {dt.year} Predictions"
    path = (
        Path(__file__).parent.parent
        / "predictions"
        / month_folder
        / day_folder
        / f"{file_stub}.json"
    )
    return path if path.exists() else None


def fetch_current_odds(target_date: str) -> dict:
    """Fetch current odds from The Odds API. Returns dict keyed by (home_abbr, away_abbr)."""
    api_key = os.environ.get("ODDS_API_KEY", "")
    if not api_key:
        print("  [!] ODDS_API_KEY not set — cannot fetch current odds", file=sys.stderr)
        return {}

    try:
        r = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds",
            params={
                "apiKey": api_key,
                "regions": "uk,us",
                "markets": "h2h,spreads",
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            },
            timeout=20,
        )
        remaining = r.headers.get("x-requests-remaining", "?")
        print(f"  [API] Odds API status: {r.status_code}  remaining requests: {remaining}", file=sys.stderr)
        if r.status_code != 200:
            print(f"  [!] Odds API error: {r.text[:200]}", file=sys.stderr)
            return {}
    except Exception as e:
        print(f"  [!] Odds API fetch failed: {e}", file=sys.stderr)
        return {}

    et_zone = timezone(timedelta(hours=-4))
    target = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=et_zone)

    result = {}
    for g in r.json():
        utc = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
        et = utc.astimezone(et_zone)
        if et.date() != target.date():
            continue

        away_full = g["away_team"]
        home_full = g["home_team"]
        away_abbr = ODDS_TEAM_MAP.get(away_full)
        home_abbr = ODDS_TEAM_MAP.get(home_full)
        if not away_abbr or not home_abbr:
            continue

        def best_price(books, market_key, team_name):
            # Try preferred UK books first
            for pool in [
                [b for b in books if b.get("key") in PREFERRED_BOOKMAKERS],
                [b for b in books if b.get("key") in UK_BOOKMAKERS],
                books,
            ]:
                best = None
                for bm in pool:
                    for mkt in bm.get("markets", []):
                        if mkt.get("key") != market_key:
                            continue
                        for outcome in mkt.get("outcomes", []):
                            if outcome.get("name") == team_name:
                                if best is None or outcome["price"] > best:
                                    best = outcome["price"]
                if best is not None:
                    return best
            return None

        books = g.get("bookmakers", [])
        home_ml = best_price(books, "h2h", home_full)
        away_ml = best_price(books, "h2h", away_full)
        home_no_vig, away_no_vig = no_vig_probs(home_ml, away_ml)
        result[(home_abbr, away_abbr)] = {
            "home_ml": home_ml,
            "away_ml": away_ml,
            "home_rl": best_price(books, "spreads", home_full),  # shown only; not selected without RL model
            "away_rl": best_price(books, "spreads", away_full),
            "home_no_vig": home_no_vig,
            "away_no_vig": away_no_vig,
            "book_count": len(books),
        }

    return result


def fetch_recent_transactions(target_date: str) -> list[dict]:
    """
    Fetch IL placements from the MLB transactions API for the 7 days up to target_date.
    Returns list of dicts with: player, team, date, description.
    """
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    start = (dt - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        r = requests.get(
            f"{MLB_API}/transactions",
            params={
                "sportId": 1,
                "startDate": start,
                "endDate": target_date,
                "season": SEASON,
            },
            timeout=20,
        )
        if r.status_code != 200:
            return []
        data = r.json()
    except Exception:
        return []

    transactions = []
    for t in data.get("transactions", []):
        desc = t.get("description", "").lower()
        # Only IL-related moves
        if not any(kw in desc for kw in ("injured list", "il ", "disabled list", "dl ")):
            continue
        person = t.get("person", {})
        team = t.get("toTeam") or t.get("fromTeam") or {}
        transactions.append({
            "player": person.get("fullName", "Unknown"),
            "team_abbr": team.get("abbreviation", "?"),
            "date": t.get("date", ""),
            "description": t.get("description", ""),
        })

    return transactions


def fetch_confirmed_lineup(game_pk: int) -> dict:
    """
    Fetch the confirmed lineup for a game from the MLB live feed.
    Returns dict with home/away batting orders and confirmed SP names.
    Empty battingOrder list means lineup not yet posted.
    """
    try:
        r = requests.get(
            f"{MLB_API}.1/game/{game_pk}/feed/live",
            timeout=15,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}

    game_data = data.get("gameData", {})
    live_data = data.get("liveData", {})
    boxscore  = live_data.get("boxscore", {}).get("teams", {})
    home_box  = boxscore.get("home", {})
    away_box  = boxscore.get("away", {})

    def parse_order(box: dict) -> list[dict]:
        order = []
        for pid in box.get("battingOrder", []):
            p = box.get("players", {}).get(f"ID{pid}", {})
            order.append({
                "name":     p.get("person", {}).get("fullName", "?"),
                "position": p.get("position", {}).get("abbreviation", "?"),
                "order":    int(p.get("battingOrder", 0)) // 100,
            })
        return order

    def confirmed_sp(box: dict) -> str | None:
        """Find the player listed as starting pitcher (gameStatus.isCurrentPitcher on first pitcher)."""
        for pid, pdata in box.get("players", {}).items():
            if pdata.get("position", {}).get("abbreviation") == "P":
                stats = pdata.get("stats", {}).get("pitching", {})
                if stats.get("gamesStarted", 0) >= 1 or pdata.get("gameStatus", {}).get("isCurrentPitcher"):
                    return pdata.get("person", {}).get("fullName")
        return None

    return {
        "status":        game_data.get("status", {}).get("detailedState", "Unknown"),
        "home_order":    parse_order(home_box),
        "away_order":    parse_order(away_box),
        "home_conf_sp":  confirmed_sp(home_box),
        "away_conf_sp":  confirmed_sp(away_box),
    }


def fetch_game_statuses_for_date(target_date: str) -> dict[int, str]:
    """
    Fetch abstractGameState for all games on target_date via the MLB schedule API.
    Returns dict: gamePk -> "NOT_STARTED" | "LIVE" | "FINAL"

    Uses a single API call so we can gate every downstream step on game state before
    touching any odds data.
    """
    try:
        r = requests.get(
            f"{MLB_API}/schedule",
            params={
                "sportId": 1,
                "startDate": target_date,
                "endDate": target_date,
                "gameType": "R",
            },
            timeout=20,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}

    statuses = {}
    for day in data.get("dates", []):
        for g in day.get("games", []):
            pk = g["gamePk"]
            abstract = g.get("status", {}).get("abstractGameState", "Preview")
            statuses[pk] = _classify_game_state(abstract)
    return statuses


def _classify_game_state(abstract_state: str) -> str:
    """Map MLB abstractGameState ('Preview' | 'Live' | 'Final') to internal constant."""
    if abstract_state == "Live":
        return "LIVE"
    if abstract_state == "Final":
        return "FINAL"
    return "NOT_STARTED"


def movement_arrow(old: float, new: float, pick_side_is_this_team: bool) -> str:
    """
    Returns a formatted movement string.
    If odds go UP (longer), that means books see this team as less likely — unfavourable.
    If odds go DOWN (shorter), money is coming in on this team — favourable signal.
    We show the direction from the bettor's perspective on the picked side.
    """
    diff = new - old
    if abs(diff) < 0.02:
        return "  →  (stable)"

    if pick_side_is_this_team:
        # We backed this team
        if diff < 0:
            return f"  ↓ {diff:+.2f}  (money in — LINE SHORTENING, sharp support)"
        else:
            return f"  ↑ {diff:+.2f}  (line drifting — books less confident)"
    else:
        # We backed the opponent; movement on this (opposing) team
        if diff < 0:
            return f"  ↓ {diff:+.2f}  (money against your pick — WARNING)"
        else:
            return f"  ↑ {diff:+.2f}  (opposition drifting — positive for your pick)"


def stake_tier(edge: float, bankroll: float) -> dict:
    if edge < 0.01:
        return {"pct": "0%", "pctValue": 0, "eur": 0.0, "label": "pass", "reportLabel": "PASS"}
    elif edge < 0.03:
        pct = 0.005
        label = "micro"
    elif edge < 0.06:
        pct = 0.01
        label = "low"
    elif edge < 0.10:
        pct = 0.02
        label = "low-mid"
    elif edge < 0.15:
        pct = 0.03
        label = "mid"
    elif edge < 0.20:
        pct = 0.04
        label = "mid-high"
    else:
        pct = 0.05
        label = "high"
    eur = round(bankroll * pct, 2)
    pct_display = f"{pct * 100:.1f}".rstrip("0").rstrip(".")
    return {
        "pct": f"{pct_display}%",
        "pctValue": round(pct * 100, 1),
        "eur": eur,
        "label": label,
        "reportLabel": f"{pct_display}% (EUR {eur:.2f})",
    }


def current_updated_bankroll(predictions_dir: Path) -> float:
    """Read the last bankroll_after from results_log_updated.csv, or fall back to 500."""
    log = predictions_dir / "results_log_updated.csv"
    if not log.exists():
        return 500.0
    import csv
    with open(log, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    settled = [r for r in rows if r.get("result") not in ("", "N/A", "Pending")]
    if settled:
        return float(settled[-1]["bankroll_after"])
    return 500.0


def generate_updated_predictions(
    predictions: list[dict],
    current_odds_dict: dict,
    bankroll: float,
    game_state_map: dict[int, str] | None = None,
) -> list[dict]:
    """
    Re-run edge calculations using current pregame odds.

    Live and final games are NEVER recalculated — their original morning evaluation
    is preserved unchanged.  Live odds are state-dependent (driven by score, inning,
    pitching changes) and must never be treated as market signals.
    """
    updated = []
    for p in predictions:
        home = p["homeAbbr"]
        away = p["awayAbbr"]
        pick_side = p.get("pickSide", "none")
        model_prob = p.get("modelProb", 0.0)
        use_rl = False

        updated_p = dict(p)  # copy all original fields
        updated_p["useRl"] = False
        updated_p["rlPickOdds"] = None

        # --- Gate: skip recalculation for live / final games ---
        if game_state_map is not None:
            game_pk = p.get("gamePk")
            state = game_state_map.get(game_pk, "NOT_STARTED") if game_pk else "NOT_STARTED"
            if state in ("LIVE", "FINAL"):
                updated_p["_gameState"] = state
                updated.append(updated_p)
                continue

        key = (home, away)
        cur = current_odds_dict.get(key)

        # --- No model direction (pick_side == "none") ---
        # Re-evaluate both sides using stored homeProb/awayProb against current odds.
        # A game the morning model couldn't pick may now show value if the market has
        # moved or if morning odds were missing.
        if pick_side == "none" or model_prob == 0.0:
            if cur is None:
                updated_p["edge"] = 0.0
                updated_p["stake"] = stake_tier(0.0, bankroll)
                updated.append(updated_p)
                continue

            home_prob = p.get("homeProb", 0.0)
            away_prob = p.get("awayProb", 0.0)
            home_ml   = cur.get("home_ml")
            away_ml   = cur.get("away_ml")

            home_market = cur.get("home_no_vig") or (1.0 / home_ml if home_ml and home_ml > 1 else None)
            away_market = cur.get("away_no_vig") or (1.0 / away_ml if away_ml and away_ml > 1 else None)
            home_edge = round(home_prob - home_market, 4) if home_market is not None else -1.0
            away_edge = round(away_prob - away_market, 4) if away_market is not None else -1.0

            best_side  = "home" if home_edge >= away_edge else "away"
            best_edge  = home_edge if best_side == "home" else away_edge
            best_odds  = home_ml  if best_side == "home" else away_ml
            best_prob  = home_prob if best_side == "home" else away_prob

            if best_edge >= 0.01:
                updated_p["pickSide"]     = best_side
                updated_p["modelProb"]    = best_prob
                updated_p["edge"]         = best_edge
                updated_p["pickOdds"]     = best_odds
                updated_p["marketImplied"]= round((cur.get("home_no_vig") if best_side == "home" else cur.get("away_no_vig")) or (1.0 / best_odds), 4) if best_odds else None
                updated_p["stake"]        = stake_tier(best_edge, bankroll)
                updated_p["hasOdds"]      = True
                updated_p["_newFromSkip"] = True   # flag: was a morning skip
            else:
                updated_p["edge"]  = 0.0
                updated_p["stake"] = stake_tier(0.0, bankroll)

            updated.append(updated_p)
            continue

        # --- Model had a direction (pick_side != "none") ---
        if cur is None:
            # Game not in current feed (may have started) — keep original
            updated.append(updated_p)
            continue

        cur_odds = cur["home_ml"] if pick_side == "home" else cur["away_ml"]

        if cur_odds is None:
            updated.append(updated_p)
            continue

        implied_prob = (cur.get("home_no_vig") if pick_side == "home" else cur.get("away_no_vig")) or (1.0 / cur_odds)
        edge = round(model_prob - implied_prob, 4)
        s = stake_tier(edge, bankroll)

        # Flag if this was a morning skip that now qualifies
        was_morning_skip = p.get("stake", {}).get("eur", 0.0) == 0.0 or p.get("edge", 0.0) < 0.01
        if was_morning_skip and edge >= 0.01:
            updated_p["_newFromSkip"] = True

        updated_p["pickOdds"] = cur_odds
        updated_p["edge"]     = edge
        updated_p["stake"]    = s
        updated_p["hasOdds"]  = True
        updated_p["marketImplied"] = round(implied_prob, 4)

        updated.append(updated_p)

    return updated


def save_updated_predictions(original_path: Path, predictions: list, bankroll: float, target_date: str):
    """Save the updated predictions JSON, markdown summary, and Excel tracker."""
    stem = original_path.stem  # e.g. "April 15th 2026 Predictions"
    out_json = original_path.parent / f"{stem} (Updated).json"
    out_md   = original_path.parent / f"{stem} (Updated).md"
    out_xlsx = original_path.parent / f"{stem} (Updated).xlsx"

    payload = {
        "date": target_date,
        "bankroll": bankroll,
        "variant": "updated",
        "predictions": predictions,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # Simple markdown
    bets   = [p for p in predictions if p.get("stake", {}).get("eur", 0) > 0 and p.get("edge", 0) >= 0.01]
    skips  = [p for p in predictions if p.get("stake", {}).get("eur", 0) <= 0 or p.get("edge", 0) < 0.01]
    staked = sum(p["stake"]["eur"] for p in bets)

    lines = [
        f"# {stem} (Updated — post movement check)",
        f"",
        f"Bankroll: EUR {bankroll:.2f}  |  {len(bets)} BETs / {len(skips)} SKIPs  |  EUR {staked:.2f} staked",
        f"",
        f"| Game | Pick | Odds | Edge | Stake | Decision |",
        f"|------|------|------|------|-------|----------|",
    ]
    for p in predictions:
        home = p["homeAbbr"]
        away = p["awayAbbr"]
        pick_side = p.get("pickSide", "none")
        use_rl = p.get("useRl", False)
        if pick_side == "none":
            pick_label = "SKIP"
        else:
            team = home if pick_side == "home" else away
            pick_label = f"{team} -1.5" if use_rl else f"{team} ML"
        odds  = p.get("pickOdds") or ""
        edge  = p.get("edge", 0.0)
        stake = p.get("stake", {})
        dec   = "BET" if stake.get("eur", 0) > 0 and edge >= 0.01 else "SKIP"
        lines.append(
            f"| {away} @ {home} | {pick_label} | "
            f"{'%.2f' % odds if odds else '—'} | "
            f"{edge*100:.1f}% | "
            f"EUR {stake.get('eur', 0):.2f} | {dec} |"
        )

    out_md.write_text("\n".join(lines), encoding="utf-8")

    # Generate Excel — reuse predict_today's write_excel_report, then rename.
    # write_excel_report writes to the original "...Predictions.xlsx" path, so we
    # back that up first, let the function run, rename the result to (Updated).xlsx,
    # then restore the original so both files coexist.
    try:
        import shutil
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from mlb.scripts.predict_today import write_excel_report
        orig_xlsx = original_path.parent / f"{stem}.xlsx"
        backup    = original_path.parent / f"{stem}.xlsx.bak"
        # Back up original if it exists
        if orig_xlsx.exists():
            shutil.copy2(orig_xlsx, backup)
        try:
            tmp_xlsx = write_excel_report(predictions, target_date, bankroll=bankroll)
            if tmp_xlsx.exists():
                shutil.copy2(tmp_xlsx, out_xlsx)
                tmp_xlsx.unlink()
        finally:
            # Always restore the original
            if backup.exists():
                backup.rename(orig_xlsx)
    except Exception as e:
        print(f"  [!] xlsx generation skipped: {e}", file=sys.stderr)

    return out_json, out_md, out_xlsx


def rerun_tbd_predictions(
    predictions: list[dict],
    tbd_sp_updates: dict,
    target_date: str,
) -> list[dict]:
    """
    For games that had TBD starters at morning prediction time but now have confirmed SPs
    (or where the confirmed SP differs from the model's probable), rebuild features with
    the actual pitcher stats and re-run the logistic regression model.

    Also handles BET games where the confirmed SP changed mid-morning.

    Returns an updated predictions list. Games without SP updates are returned unchanged.
    """
    if not tbd_sp_updates:
        return predictions

    try:
        import pickle
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from mlb.scripts.predict_today import (
            fetch_pitcher_stats,
            fetch_completed,
            fetch_upcoming,
            build_team_state,
            build_features,
            predict as predict_prob,
            stake_tier,
            MODEL_DIR,
            FEATURES as PREDICT_FEATURES,
        )

        with open(MODEL_DIR / "moneyline_model.pkl", "rb") as f:
            saved = pickle.load(f)
        ml_model    = saved["model"]
        ml_scaler   = saved["scaler"]
        pkl_features = saved.get("features", PREDICT_FEATURES)

        print("  Fetching pitcher stats for SP re-run ...", end=" ", flush=True)
        pitchers = fetch_pitcher_stats()
        print(f"{len(pitchers)} pitchers")
        name_to_id = {d["name"]: pid for pid, d in pitchers.items() if d.get("name")}

        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        completed  = fetch_completed(yesterday)
        team_state = build_team_state(completed)

        upcoming   = fetch_upcoming(target_date)
        pk_to_game = {g["game_pk"]: g for g in upcoming}

    except Exception as exc:
        print(f"  [!] SP re-run setup failed: {exc}", file=sys.stderr)
        return predictions

    updated = []
    for p in predictions:
        game_pk = p.get("gamePk")
        sp_upd  = tbd_sp_updates.get(game_pk)
        if not sp_upd:
            updated.append(p)
            continue

        game_info = pk_to_game.get(game_pk)
        if not game_info:
            updated.append(p)
            continue

        home_sp_name = sp_upd.get("home_sp_name") or game_info.get("home_sp_name") or p.get("homeSpName", "TBD")
        away_sp_name = sp_upd.get("away_sp_name") or game_info.get("away_sp_name") or p.get("awaySpName", "TBD")
        home_sp_id   = name_to_id.get(home_sp_name) if home_sp_name != "TBD" else None
        away_sp_id   = name_to_id.get(away_sp_name) if away_sp_name != "TBD" else None

        game_dict = {
            "home_team_id": game_info["home_team_id"],
            "away_team_id": game_info["away_team_id"],
            "home_team":    p["homeAbbr"],
            "away_team":    p["awayAbbr"],
            "home_sp_id":   home_sp_id or game_info.get("home_sp_id"),
            "away_sp_id":   away_sp_id or game_info.get("away_sp_id"),
        }

        try:
            feat_vec, _, _ = build_features(game_dict, team_state, pitchers, pkl_features)
            home_prob = predict_prob(ml_model, ml_scaler, feat_vec)
        except Exception as exc:
            print(f"  [!] Re-run failed for {p['awayAbbr']} @ {p['homeAbbr']}: {exc}", file=sys.stderr)
            updated.append(p)
            continue

        away_prob  = 1.0 - home_prob
        pick_side  = p.get("pickSide", "none")

        # If original pick was none (TBD skip), determine new pick direction
        if pick_side == "none":
            if home_prob > 0.505:
                pick_side = "home"
            elif away_prob > 0.505:
                pick_side = "away"

        model_prob = home_prob if pick_side == "home" else (away_prob if pick_side == "away" else max(home_prob, away_prob))

        h_sp = pitchers.get(home_sp_id, {}) if home_sp_id else {}
        a_sp = pitchers.get(away_sp_id, {}) if away_sp_id else {}

        updated_p = dict(p)
        updated_p.update({
            "homeProb":    round(home_prob, 4),
            "awayProb":    round(away_prob, 4),
            "modelProb":   round(model_prob, 4),
            "pickSide":    pick_side,
            "homeSpName":  home_sp_name,
            "awaySpName":  away_sp_name,
            "homeSpEra":   h_sp.get("era"),
            "awaySpEra":   a_sp.get("era"),
            "homeSpWhip":  h_sp.get("whip"),
            "awaySpWhip":  a_sp.get("whip"),
            "homeSpIp":    h_sp.get("ip", 0.0),
            "awaySpIp":    a_sp.get("ip", 0.0),
            "_rerunnedSP": True,
        })
        print(
            f"  SP re-run: {p['awayAbbr']} @ {p['homeAbbr']}  "
            f"H:{home_sp_name}  A:{away_sp_name}  "
            f"→ home_prob {home_prob:.3f}"
        )
        updated.append(updated_p)

    return updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    target_date = args.date

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    print(f"\n{'='*60}")
    print(f"  LINE MOVEMENT CHECK — {dt.strftime('%A %d %B %Y')}")
    print(f"{'='*60}\n")

    # --- Load predictions JSON ---
    json_path = find_predictions_json(target_date)
    if not json_path:
        print(f"  [!] No predictions JSON found for {target_date}")
        print(f"      Run /generate-mlb-predictions first.\n")
        sys.exit(1)

    with open(json_path) as f:
        predictions = json.load(f)

    all_games = predictions.get("predictions", [])
    picks = [p for p in all_games if p.get("pickSide") not in ("none", None)]
    bets = [p for p in all_games if p.get("stake", {}).get("eur", 0) > 0 and p.get("edge", 0) >= 0.01]

    print(f"  Loaded {len(all_games)} games ({len(picks)} directional picks, {len(bets)} BETs) from:")
    print(f"  {json_path.name}\n")

    # --- Detect game states FIRST — before any odds comparison ---
    # This must happen before touching odds so live games are never evaluated
    # with in-play odds that have nothing to do with pregame market signals.
    print("  Detecting game states (pregame / live / final)...")
    game_state_map = fetch_game_statuses_for_date(target_date)
    pregame_count = sum(1 for p in all_games if game_state_map.get(p.get("gamePk"), "NOT_STARTED") == "NOT_STARTED")
    live_count    = sum(1 for p in all_games if game_state_map.get(p.get("gamePk"), "NOT_STARTED") == "LIVE")
    final_count   = sum(1 for p in all_games if game_state_map.get(p.get("gamePk"), "NOT_STARTED") == "FINAL")
    print(f"  States: {pregame_count} pregame  |  {live_count} live  |  {final_count} final\n")

    # --- Fetch current odds ---
    print("  Fetching current odds from The Odds API...")
    current_odds = fetch_current_odds(target_date)
    print(f"  Got current odds for {len(current_odds)} games.\n")

    # --- Fetch injury transactions ---
    print("  Checking MLB transactions for recent IL placements...")
    transactions = fetch_recent_transactions(target_date)

    # Build a lookup: abbr -> list of IL transactions
    il_by_team: dict[str, list[str]] = {}
    for t in transactions:
        abbr = t["team_abbr"]
        il_by_team.setdefault(abbr, []).append(
            f"{t['player']} ({t['date'][:10]}): {t['description']}"
        )
    print(f"  Found {len(transactions)} IL transactions across {len(il_by_team)} teams.\n")

    # --- Print movement table (grouped by game state) ---
    warnings = []

    _sections = [
        ("🟢 PREGAME OPPORTUNITIES", "NOT_STARTED"),
        ("🔴 LIVE GAMES  (pregame picks frozen — live odds are score/inning-driven, not market signals)", "LIVE"),
        ("⚪ FINAL / IGNORED", "FINAL"),
    ]

    for section_label, section_state in _sections:
        section_picks = [
            p for p in all_games
            if game_state_map.get(p.get("gamePk"), "NOT_STARTED") == section_state
        ]
        # Skip empty sections except NOT_STARTED (always show pregame table)
        if not section_picks and section_state != "NOT_STARTED":
            continue

        print(f"{'─'*60}")
        print(f"  {section_label}")
        print(f"{'─'*60}")
        if not section_picks:
            print("  (none)\n")
            continue
        print(f"  {'GAME':<16}  {'PICK':<18}  {'AT PRED':>8}  {'NOW':>8}  MOVEMENT")
        print(f"{'─'*60}")

        for p in section_picks:
            home = p["homeAbbr"]
            away = p["awayAbbr"]
            pick_side = p.get("pickSide", "none")
            use_rl = p.get("useRl", False)
            decision = "BET" if p.get("stake", {}).get("eur", 0) > 0 and p.get("edge", 0) >= 0.01 else "SKIP"
            game_label = f"{away} @ {home}"
            if pick_side == "none":
                pick_label = "NO PICK"
            else:
                pick_label = (home if pick_side == "home" else away) + (" -1.5" if use_rl else " ML")
            stored_odds = p.get("pickOdds")
            stored_str = f"{stored_odds:.2f}" if stored_odds else "  —  "

            if section_state == "LIVE":
                flag = " [BET→frozen]" if decision == "BET" else " [skip]"
                print(
                    f"  {game_label:<16}  {pick_label:<18}  {stored_str:>8}  {'—':>8}"
                    f"  🔴 game in progress — live odds excluded, morning pick preserved  {flag}"
                )
                continue

            if section_state == "FINAL":
                print(
                    f"  {game_label:<16}  {pick_label:<18}  {stored_str:>8}  {'—':>8}"
                    f"  ⚪ FINAL — ignored  [final]"
                )
                continue

            # NOT_STARTED: normal pregame odds comparison
            key = (home, away)
            cur = current_odds.get(key) or current_odds.get((away, home))
            flag = " [BET]" if decision == "BET" else " [skip]"
            if pick_side == "none":
                movement_str = "  afternoon scan will evaluate both sides"
                now_str = "  -  "
            elif stored_odds is None:
                movement_str = "  — (no stored odds)"
                now_str = "  —  "
            elif cur is None:
                movement_str = "  — (not in current feed)"
                now_str = "  —  "
            else:
                if use_rl:
                    cur_odds = cur["home_rl"] if pick_side == "home" else cur["away_rl"]
                else:
                    cur_odds = cur["home_ml"] if pick_side == "home" else cur["away_ml"]
                if cur_odds is None:
                    movement_str = "  — (no current odds for this market)"
                    now_str = "  —  "
                else:
                    now_str = f"{cur_odds:.2f}"
                    movement_str = movement_arrow(stored_odds, cur_odds, pick_side_is_this_team=True)
                    diff = cur_odds - stored_odds
                    if diff < -0.10 and decision == "BET":
                        warnings.append(
                            f"  WARNING: {game_label} — {pick_label} shortened {diff:+.2f} "
                            f"({stored_odds:.2f} -> {cur_odds:.2f}). Sharp money may be opposing."
                        )
            print(f"  {game_label:<16}  {pick_label:<18}  {stored_str:>8}  {now_str:>8}  {movement_str}  {flag}")

        print(f"{'─'*60}\n")

    # --- Warnings ---
    if warnings:
        print("  MOVEMENT WARNINGS:")
        for w in warnings:
            print(w)
        print()

    # --- Injury flags ---
    relevant_teams = set()
    for p in all_games:
        relevant_teams.add(p["homeAbbr"])
        relevant_teams.add(p["awayAbbr"])

    relevant_il = {t: il_by_team[t] for t in relevant_teams if t in il_by_team}
    if relevant_il:
        print(f"  INJURY FLAGS (IL placements last 7 days, teams in today's card):")
        for team, entries in sorted(relevant_il.items()):
            for entry in entries:
                print(f"    [{team}] {entry}")
        print()
    else:
        print("  No IL transactions found for teams in today's card.\n")

    # --- Confirmed lineups + SP check for BET games, TBD games, and near-threshold skips ---
    print(f"  CONFIRMED LINEUPS (BET games + TBD/near-threshold skips) — fetching from MLB Stats API...")
    sp_warnings   = []
    tbd_sp_updates: dict = {}   # {game_pk: {"home_sp_name": ..., "away_sp_name": ...}}

    pregame_games = [
        p for p in all_games
        if game_state_map.get(p.get("gamePk"), "NOT_STARTED") == "NOT_STARTED"
    ]
    pregame_bets = [
        p for p in bets
        if game_state_map.get(p.get("gamePk"), "NOT_STARTED") == "NOT_STARTED"
    ]

    # Include every morning SKIP where either SP was TBD, including pickSide == "none".
    tbd_skips = [
        p for p in pregame_games
        if p not in bets
        and (p.get("homeSpName", "TBD") == "TBD" or p.get("awaySpName", "TBD") == "TBD")
    ]

    # Also inspect skips close enough to threshold that lineups/SP confirmation matter.
    near_threshold_skips = [
        p for p in pregame_games
        if p not in bets
        and p not in tbd_skips
        and p.get("pickSide") not in ("none", None)
        and -0.03 <= p.get("edge", 0.0) < 0.01
    ]

    lineup_games = []
    for p in pregame_bets + tbd_skips + near_threshold_skips:
        if p not in lineup_games:
            lineup_games.append(p)

    for p in lineup_games:
        home      = p["homeAbbr"]
        away      = p["awayAbbr"]
        pick_side = p.get("pickSide", "none")
        game_pk   = p.get("gamePk")
        model_home_sp = p.get("homeSpName", "TBD")
        model_away_sp = p.get("awaySpName", "TBD")
        home_era  = p.get("homeSpEra", "?")
        away_era  = p.get("awaySpEra", "?")
        pick_team = home if pick_side == "home" else away if pick_side == "away" else "none"

        is_tbd_skip = p in tbd_skips
        is_near_threshold_skip = p in near_threshold_skips
        if is_tbd_skip:
            section_tag = "TBD SKIP - SP CHECK"
        elif is_near_threshold_skip:
            section_tag = "NEAR-THRESHOLD SKIP - LINEUP/SP CHECK"
        else:
            section_tag = f"picking: {pick_team}"
        print(f"\n  {'─'*56}")
        print(f"  {away} @ {home}  [{section_tag}]")

        lineup = fetch_confirmed_lineup(game_pk) if game_pk else {}
        game_status = lineup.get("status", "Unknown")

        home_order = lineup.get("home_order", [])
        away_order = lineup.get("away_order", [])
        conf_home_sp = lineup.get("home_conf_sp")
        conf_away_sp = lineup.get("away_conf_sp")

        # SP check — flag if confirmed SP differs from model's probable
        home_sp_label = model_home_sp
        away_sp_label = model_away_sp
        _needs_rerun  = False
        _new_home_sp  = None
        _new_away_sp  = None

        if conf_home_sp and conf_home_sp.lower() != model_home_sp.lower():
            home_sp_label = f"{conf_home_sp}  !! CHANGED from {model_home_sp} !!"
            sp_warnings.append(f"  SP CHANGE: {away} @ {home} — Home SP is now {conf_home_sp} (model used {model_home_sp})")
            _new_home_sp = conf_home_sp
            _needs_rerun = True
        if conf_away_sp and conf_away_sp.lower() != model_away_sp.lower():
            away_sp_label = f"{conf_away_sp}  !! CHANGED from {model_away_sp} !!"
            sp_warnings.append(f"  SP CHANGE: {away} @ {home} — Away SP is now {conf_away_sp} (model used {model_away_sp})")
            _new_away_sp = conf_away_sp
            _needs_rerun = True

        if _needs_rerun and game_pk:
            tbd_sp_updates[game_pk] = {
                "home_sp_name": _new_home_sp or model_home_sp,
                "away_sp_name": _new_away_sp or model_away_sp,
            }
        elif is_tbd_skip and game_pk:
            # Even when the live feed has not posted a confirmed SP yet, re-check the
            # hydrated schedule in the model re-run path. Probables often appear there
            # before full lineups are posted.
            tbd_sp_updates[game_pk] = {
                "home_sp_name": conf_home_sp if model_home_sp == "TBD" else model_home_sp,
                "away_sp_name": conf_away_sp if model_away_sp == "TBD" else model_away_sp,
            }

        h_marker = "<<<" if pick_side == "home" else "   "
        a_marker = "<<<" if pick_side == "away" else "   "
        print(f"    Home SP: {home_sp_label:<40} ERA {home_era}  {h_marker}")
        print(f"    Away SP: {away_sp_label:<40} ERA {away_era}  {a_marker}")

        if not home_order and not away_order:
            print(f"    Lineups: Not yet posted  (game status: {game_status})")
        else:
            # Print both lineups side by side
            max_len = max(len(home_order), len(away_order))
            print(f"    {'':>3}  {'HOME: ' + home:<28}  {'':>3}  {'AWAY: ' + away}")
            for i in range(max_len):
                if i < len(home_order):
                    h = home_order[i]
                    h_str = f"{h['order']:>1}. {h['name']:<22} {h['position']:<3}"
                else:
                    h_str = " " * 29
                if i < len(away_order):
                    a = away_order[i]
                    a_str = f"{a['order']:>1}. {a['name']:<22} {a['position']:<3}"
                else:
                    a_str = ""
                # Highlight the picked side's lineup
                h_flag = " <" if pick_side == "home" else "  "
                a_flag = " <" if pick_side == "away" else "  "
                print(f"    {h_str}{h_flag}  {a_str}{a_flag}")

    print(f"\n  {'─'*56}")

    if sp_warnings:
        print(f"\n  SP CHANGE WARNINGS:")
        for w in sp_warnings:
            print(w)
        print()

    # --- Archive current odds for CLV calculation ---
    predictions_dir  = Path(__file__).parent.parent / "predictions"
    _odds_archive_dir = predictions_dir / "odds_archive"
    _odds_archive_dir.mkdir(parents=True, exist_ok=True)
    _archive_path = _odds_archive_dir / f"{target_date}_pregame_odds.json"
    import json as _json
    _archive_data = {
        f"{home}_{away}": odds_data
        for (home, away), odds_data in current_odds.items()
    }
    _archive_path.write_text(_json.dumps(_archive_data, indent=2), encoding="utf-8")
    print(f"\n  Pregame odds archived → {_archive_path.name}  ({len(current_odds)} games)")

    # --- Re-run model for TBD / changed-SP games ---
    base_predictions = predictions.get("predictions", [])
    if tbd_sp_updates:
        print(f"\n{'─'*60}")
        print(f"  Re-running model for {len(tbd_sp_updates)} game(s) with updated starting pitchers...")
        base_predictions = rerun_tbd_predictions(base_predictions, tbd_sp_updates, target_date)

    # --- Generate updated predictions with current odds ---
    print(f"\n{'─'*60}")
    print("  Generating updated predictions with current odds...")
    updated_bankroll = current_updated_bankroll(predictions_dir)
    updated_preds = generate_updated_predictions(
        base_predictions, current_odds, updated_bankroll, game_state_map
    )
    out_json, out_md, out_xlsx = save_updated_predictions(json_path, updated_preds, updated_bankroll, target_date)

    updated_bets  = [p for p in updated_preds if p.get("stake", {}).get("eur", 0) > 0 and p.get("edge", 0) >= 0.01]
    updated_skips = [p for p in updated_preds if p.get("stake", {}).get("eur", 0) <= 0 or p.get("edge", 0) < 0.01]
    updated_staked = sum(p["stake"]["eur"] for p in updated_bets)

    # Show what changed vs original
    orig_bets = [p for p in predictions.get("predictions", []) if p.get("stake", {}).get("eur", 0) > 0 and p.get("edge", 0) >= 0.01]
    orig_pks  = {p["gamePk"] for p in orig_bets}
    new_pks   = {p["gamePk"] for p in updated_bets}

    dropped = orig_pks - new_pks
    added   = new_pks - orig_pks

    orig_by_pk = {p["gamePk"]: p for p in predictions.get("predictions", [])}
    upd_by_pk  = {p["gamePk"]: p for p in updated_preds}
    shared_pks = set(orig_by_pk) & set(upd_by_pk)
    odds_moved = [
        pk for pk in shared_pks
        if orig_by_pk[pk].get("pickOdds") != upd_by_pk[pk].get("pickOdds")
    ]
    edge_moved = [
        pk for pk in shared_pks
        if abs(orig_by_pk[pk].get("edge", 0.0) - upd_by_pk[pk].get("edge", 0.0)) >= 0.001
    ]
    sp_reruns = [p for p in updated_preds if p.get("_rerunnedSP")]
    lineup_checked_pks = {p.get("gamePk") for p in lineup_games if p.get("gamePk")}

    # --- Afternoon re-evaluations: skips that now qualify ---
    new_from_skips = [p for p in updated_preds if p.get("_newFromSkip") and p.get("gamePk") in added]
    if new_from_skips:
        print(f"\n{'─'*60}")
        print(f"  ⚡ AFTERNOON RE-EVALUATIONS — morning SKIPs that now qualify")
        print(f"{'─'*60}")
        for p in new_from_skips:
            home      = p["homeAbbr"]
            away      = p["awayAbbr"]
            pick_side = p.get("pickSide", "none")
            pick_team = home if pick_side == "home" else away
            odds      = p.get("pickOdds")
            edge      = p.get("edge", 0.0)
            stake     = p.get("stake", {})
            home_sp   = p.get("homeSpName", "TBD")
            away_sp   = p.get("awaySpName", "TBD")
            home_era  = p.get("homeSpEra", "?")
            away_era  = p.get("awaySpEra", "?")
            home_ip   = p.get("homeSpIp", 0.0) or 0.0
            away_ip   = p.get("awaySpIp", 0.0) or 0.0

            ip_note = lambda sp, ip: f" ({ip:.1f} IP)" if ip and ip < 30 else ""

            print(f"\n  {away} @ {home}")
            print(f"    Pick  : {pick_team} ML")
            print(f"    Odds  : {odds:.2f}" if odds else "    Odds  : —")
            print(f"    Edge  : {edge*100:.1f}%")
            print(f"    Stake : EUR {stake.get('eur', 0):.2f}  ({stake.get('pct','0%')})")
            print(f"    Home SP: {home_sp}  ERA {home_era}{ip_note(home_sp, home_ip)}")
            print(f"    Away SP: {away_sp}  ERA {away_era}{ip_note(away_sp, away_ip)}")
            if pick_side == "none":
                print(f"    Note  : Morning model had no clear direction — current odds created this edge")
            else:
                print(f"    Note  : Morning edge was below threshold — current odds now qualify")
        print(f"\n{'─'*60}")

    # Note any live/final games whose picks were frozen (not recalculated)
    frozen_live = [
        p for p in updated_preds
        if p.get("_gameState") == "LIVE" and p.get("stake", {}).get("eur", 0) > 0
    ]
    if frozen_live:
        print(f"\n  FROZEN PICKS (live games — morning evaluation preserved):")
        for p in frozen_live:
            home, away = p["homeAbbr"], p["awayAbbr"]
            stored = p.get("pickOdds")
            odds_str = f" @ {stored:.2f}" if stored else ""
            print(f"    🔴 {away} @ {home}  — game in progress, pick unchanged{odds_str}")

    print(f"\n  AFTERNOON CHECK SUMMARY:")
    print(f"    Odds moved:        {len(odds_moved)} game(s)")
    print(f"    Edge moved:        {len(edge_moved)} game(s)")
    print(f"    Lineups checked:   {len(lineup_checked_pks)} game(s)")
    print(f"    SP model re-runs:  {len(sp_reruns)} game(s)")
    print(f"    New bets:          {len(added)}")
    print(f"    Dropped bets:      {len(dropped)}")

    if dropped or added:
        print(f"\n  PICKS CHANGED vs morning predictions (pregame games only):")
        for p in predictions.get("predictions", []):
            if p["gamePk"] in dropped:
                home, away = p["homeAbbr"], p["awayAbbr"]
                state = game_state_map.get(p["gamePk"], "NOT_STARTED")
                if state == "NOT_STARTED":
                    print(f"    DROPPED: {away} @ {home}  (edge fell below threshold at current odds)")
        for p in updated_preds:
            if p["gamePk"] in added:
                home, away = p["homeAbbr"], p["awayAbbr"]
                print(f"    NEW BET: {away} @ {home}  (edge now qualifies at current odds)")
    else:
        print(f"    Decision changes:  none - same {len(updated_bets)} BETs as morning predictions")

    print(f"\n  Updated predictions: {len(updated_bets)} BETs / {len(updated_skips)} SKIPs | EUR {updated_staked:.2f} staked")
    print(f"  Saved: {out_md.name}")
    if out_xlsx.exists():
        print(f"  Saved: {out_xlsx.name}")

    print(f"\n{'='*60}")
    print("  Done. Review SP names above against today's confirmed lineups.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
