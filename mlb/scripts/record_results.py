"""
Record actual results for a previous day's MLB predictions.

Reads the JSON predictions file for the given date, fetches the completed
game scores from the MLB Stats API, and appends settled bets to
mlb/predictions/results_log.csv with running P&L.

Usage:
    python mlb/scripts/record_results.py               # settles yesterday
    python mlb/scripts/record_results.py --date 2026-04-14
    python mlb/scripts/record_results.py --summary     # P&L / win rate / ROI report
"""

import csv
import json
import sys
import argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import requests
from datetime import date, datetime, timedelta
from pathlib import Path

PREDICTIONS_DIR       = Path(__file__).parent.parent / "predictions"
RESULTS_LOG           = PREDICTIONS_DIR / "results_log.csv"
RESULTS_LOG_UPDATED   = PREDICTIONS_DIR / "results_log_updated.csv"
ACCAS_LOG             = PREDICTIONS_DIR / "accumulators_log.csv"
ODDS_ARCHIVE_DIR      = PREDICTIONS_DIR / "odds_archive"
MLB                   = "https://statsapi.mlb.com/api/v1"
STARTING_BANKROLL     = 500.0

LOG_HEADERS = [
    "date", "game_pk", "home_team", "away_team",
    "pick_side", "pick_team", "pick_odds", "stake_eur",
    "decision", "result", "pnl", "bankroll_before", "bankroll_after",
    "edge", "edge_bucket", "closing_odds", "clv_pct",
]

ACCA_LOG_HEADERS = [
    "date", "type", "legs", "combined_odds", "stake", "result", "pnl",
]

EDGE_BUCKET_ORDER = ["1-3%", "3-6%", "6-10%", "10-15%", "15-20%", "20%+"]


def get_edge_bucket(edge) -> str:
    try:
        e = float(edge) if edge not in (None, "") else 0.0
    except (ValueError, TypeError):
        return ""
    if e < 0.01: return "PASS"
    if e < 0.03: return "1-3%"
    if e < 0.06: return "3-6%"
    if e < 0.10: return "6-10%"
    if e < 0.15: return "10-15%"
    if e < 0.20: return "15-20%"
    return "20%+"


def load_closing_odds(target_date: str) -> dict:
    """Load archived pregame odds for CLV calculation.
    Prefers afternoon snapshot (check_movement), falls back to morning (predict_today)."""
    for filename in (
        f"{target_date}_pregame_odds.json",
        f"{target_date}_morning_odds.json",
    ):
        path = ODDS_ARCHIVE_DIR / filename
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    return {}


def compute_clv(pick_odds, closing_odds, pick_side: str) -> str:
    """Return CLV% as a formatted string, or '' if data is missing.

    CLV% = (closing_implied - bet_implied) / bet_implied * 100
    Positive = market moved toward your pick (you beat the closing line).
    """
    try:
        if not pick_odds or not closing_odds:
            return ""
        bet_implied     = 1.0 / float(pick_odds)
        closing_implied = 1.0 / float(closing_odds)
        return f"{(closing_implied - bet_implied) / bet_implied * 100:.2f}"
    except (ZeroDivisionError, TypeError, ValueError):
        return ""


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def predictions_json_path(target_date: str, variant: str = "original") -> Path:
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    month_folder = f"{dt.strftime('%B')} Predictions"
    day_folder   = f"{dt.strftime('%B')} {ordinal(dt.day)}"
    file_stub    = f"{dt.strftime('%B')} {ordinal(dt.day)} {dt.year} Predictions"
    suffix = " (Updated).json" if variant == "updated" else ".json"
    return PREDICTIONS_DIR / month_folder / day_folder / f"{file_stub}{suffix}"


def get(url, params=None):
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def fetch_scores(target_date: str) -> dict:
    """Returns {game_pk: {home_score, away_score, home_win}} for all final games."""
    data = get(
        f"{MLB}/schedule",
        params={
            "sportId":   1,
            "startDate": target_date,
            "endDate":   target_date,
            "gameType":  "R",
            "hydrate":   "linescore,team",
        },
    )
    scores = {}
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("statusCode") != "F":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            a_score = away.get("score")
            h_score = home.get("score")
            if a_score is None or h_score is None:
                ls = g.get("linescore", {}).get("teams", {})
                a_score = ls.get("away", {}).get("runs")
                h_score = ls.get("home", {}).get("runs")
            if a_score is None or h_score is None:
                continue
            scores[g["gamePk"]] = {
                "home_score": int(h_score),
                "away_score": int(a_score),
                "home_win":   int(h_score) > int(a_score),
            }
    return scores


def read_log(log_path: Path = None) -> list[dict]:
    path = log_path or RESULTS_LOG
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def current_bankroll(log_rows: list[dict]) -> float:
    settled = [r for r in log_rows if r.get("result") not in ("", "Pending")]
    if settled:
        return float(settled[-1]["bankroll_after"])
    return STARTING_BANKROLL


def already_settled(log_rows: list[dict], target_date: str) -> set[str]:
    return {r["game_pk"] for r in log_rows if r["date"] == target_date and r.get("result") not in ("", "Pending")}


def append_rows(new_rows: list[dict], log_path: Path = None):
    path = log_path or RESULTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)


def write_log(rows: list[dict], log_path: Path = None):
    path = log_path or RESULTS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def remove_pending_for_games(log_rows: list[dict], target_date: str, game_pks: set[str]) -> tuple[list[dict], int]:
    kept = []
    removed = 0
    for row in log_rows:
        if (
            row.get("date") == target_date
            and row.get("game_pk") in game_pks
            and row.get("decision") == "BET"
            and row.get("result") == "Pending"
        ):
            removed += 1
            continue
        kept.append(row)
    return kept, removed


# ─── ACCUMULATOR SETTLEMENT ───────────────────────────────────────────────────

def settle_accumulators(accumulators: list[dict], scores: dict, target_date: str):
    """Settle accumulators against final scores. Results go to ACCAS_LOG only."""
    if not accumulators:
        return

    # Check if already settled for this date
    already_settled_dates = set()
    if ACCAS_LOG.exists():
        with open(ACCAS_LOG, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["date"] == target_date:
                    already_settled_dates.add(row["type"] + row["legs"])

    new_rows = []
    for acca in accumulators:
        legs_str = json.dumps([
            {"label": l["label"], "odds": l["odds"], "line": l.get("line", "ml"), "pickSide": l.get("pickSide", "")}
            for l in acca["legs"]
        ])
        key = acca["type"] + json.dumps([{"label": l["label"], "odds": l["odds"]} for l in acca["legs"]])
        if key in already_settled_dates:
            continue

        # Settle each leg individually, storing per-leg result
        settled_legs = []
        any_pending = False
        for leg in acca["legs"]:
            game_pk_int = int(leg["gamePk"]) if leg.get("gamePk", "").isdigit() else None
            score = scores.get(game_pk_int) if game_pk_int else None
            if score is None:
                any_pending = True
                settled_legs.append({**leg, "legResult": "Pending"})
                continue
            pick_side = leg.get("pickSide")
            line_str = leg.get("line", "ml")
            if line_str and line_str != "ml":
                try:
                    line_val = float(line_str)  # e.g. -1.5 or -2.5
                    if pick_side == "home":
                        margin = score["home_score"] - score["away_score"]
                    else:
                        margin = score["away_score"] - score["home_score"]
                    leg_won = margin > -line_val  # -(-2.5)=2.5 → need margin>2.5 i.e. >=3
                except (ValueError, TypeError):
                    leg_won = (pick_side == "home") == score["home_win"]
            else:
                leg_won = (pick_side == "home") == score["home_win"]
            settled_legs.append({**leg, "legResult": "Win" if leg_won else "Loss"})

        all_won = not any_pending and all(l["legResult"] == "Win" for l in settled_legs)

        # Build legs JSON with per-leg results stored
        legs_str = json.dumps([
            {
                "label":     l["label"],
                "odds":      l["odds"],
                "line":      l.get("line", "ml"),
                "pickSide":  l.get("pickSide", ""),
                "legResult": l.get("legResult", ""),
            }
            for l in settled_legs
        ])

        if any_pending:
            result, pnl = "Pending", ""
        elif all_won:
            result = "Win"
            pnl = f"{round(acca['stake'] * (acca['combined_odds'] - 1), 2):.2f}"
        else:
            result = "Loss"
            pnl = f"{-acca['stake']:.2f}"

        icon = "OK" if result == "Win" else ("??" if result == "Pending" else "XX")
        leg_labels = " + ".join(
            f"{l['label']} ({'✓' if l.get('legResult') == 'Win' else '✗' if l.get('legResult') == 'Loss' else '?'})"
            for l in settled_legs
        )
        print(
            f"  {icon} ACCA {acca['type']}: {leg_labels}  "
            f"@ {acca['combined_odds']:.2f}  ->  {result}"
            + (f"  P&L: EUR {pnl}" if result != "Pending" else "")
        )

        new_rows.append({
            "date":          target_date,
            "type":          acca["type"],
            "legs":          legs_str,
            "combined_odds": f"{acca['combined_odds']:.2f}",
            "stake":         f"{acca['stake']:.2f}",
            "result":        result,
            "pnl":           pnl,
        })

    if new_rows:
        write_header = not ACCAS_LOG.exists()
        with open(ACCAS_LOG, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ACCA_LOG_HEADERS)
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)


# ─── SUMMARY ──────────────────────────────────────────────────────────────────

def print_summary(log_path: Path = None, label: str = "MLB BETTING P&L SUMMARY"):
    path = log_path or RESULTS_LOG
    if not path.exists():
        print(f"No results log found at {path.name}.")
        return

    rows = read_log(path)
    bet_rows = [r for r in rows if r["decision"] == "BET" and r["result"] not in ("", "N/A", "Pending")]
    pending  = [r for r in rows if r["decision"] == "BET" and r["result"] == "Pending"]

    if not bet_rows:
        print("No settled bets yet.")
        if pending:
            print(f"{len(pending)} bet(s) still Pending.")
        return

    total_bets   = len(bet_rows)
    won          = sum(1 for r in bet_rows if r["result"] == "Win")
    lost         = sum(1 for r in bet_rows if r["result"] == "Loss")
    win_rate     = won / total_bets * 100
    total_staked = sum(float(r["stake_eur"]) for r in bet_rows)
    total_pnl    = sum(float(r["pnl"]) for r in bet_rows if r["pnl"])
    roi          = total_pnl / total_staked * 100 if total_staked > 0 else 0.0
    start_br     = STARTING_BANKROLL
    current_br   = float(bet_rows[-1]["bankroll_after"])
    br_change    = current_br - start_br
    br_change_pct = br_change / start_br * 100

    # Current streak
    streak_result = bet_rows[-1]["result"]
    streak = 0
    for r in reversed(bet_rows):
        if r["result"] == streak_result:
            streak += 1
        else:
            break
    streak_label = f"{streak}W" if streak_result == "Win" else f"{streak}L"

    # Best / worst single day P&L
    from collections import defaultdict
    daily_pnl: dict = defaultdict(float)
    daily_bets: dict = defaultdict(int)
    for r in bet_rows:
        daily_pnl[r["date"]]  += float(r["pnl"]) if r["pnl"] else 0.0
        daily_bets[r["date"]] += 1
    best_day  = max(daily_pnl, key=daily_pnl.get)
    worst_day = min(daily_pnl, key=daily_pnl.get)

    # Monthly breakdown
    from collections import OrderedDict
    monthly: dict = OrderedDict()
    for r in bet_rows:
        month = r["date"][:7]  # YYYY-MM
        if month not in monthly:
            monthly[month] = {"bets": 0, "won": 0, "staked": 0.0, "pnl": 0.0}
        monthly[month]["bets"]   += 1
        monthly[month]["won"]    += 1 if r["result"] == "Win" else 0
        monthly[month]["staked"] += float(r["stake_eur"])
        monthly[month]["pnl"]    += float(r["pnl"]) if r["pnl"] else 0.0

    sep = "=" * 54
    print(f"\n{sep}")
    print(f"  {label}")
    print(f"{sep}")
    print(f"  Starting bankroll : EUR {start_br:,.2f}")
    print(f"  Current bankroll  : EUR {current_br:,.2f}  ({br_change:+.2f} / {br_change_pct:+.1f}%)")
    print(f"  Current streak    : {streak_label}")
    print(f"{sep}")
    print(f"  Total bets settled: {total_bets}")
    print(f"  Won / Lost        : {won} / {lost}  ({win_rate:.1f}% win rate)")
    print(f"  Pending           : {len(pending)}")
    print(f"  Total staked      : EUR {total_staked:,.2f}")
    print(f"  Total P&L         : EUR {total_pnl:+,.2f}")
    print(f"  ROI on staked     : {roi:+.2f}%")
    print(f"  Best day          : {best_day}  EUR {daily_pnl[best_day]:+.2f}")
    print(f"  Worst day         : {worst_day}  EUR {daily_pnl[worst_day]:+.2f}")

    if monthly:
        print(f"\n  {'─'*50}")
        print(f"  {'Month':<10} {'Bets':>5} {'Won':>5} {'Win%':>6} {'Staked':>10} {'P&L':>10} {'ROI':>7}")
        print(f"  {'─'*50}")
        for month, m in monthly.items():
            m_wr  = m["won"] / m["bets"] * 100 if m["bets"] else 0
            m_roi = m["pnl"] / m["staked"] * 100 if m["staked"] else 0
            print(
                f"  {month:<10} {m['bets']:>5} {m['won']:>5} {m_wr:>5.1f}% "
                f"EUR {m['staked']:>7,.2f} EUR {m['pnl']:>+7,.2f} {m_roi:>+6.1f}%"
            )

    # Edge bucket breakdown
    edge_groups: dict = {}
    for r in bet_rows:
        bkt = r.get("edge_bucket") or get_edge_bucket(r.get("edge", 0))
        if bkt and bkt != "PASS":
            edge_groups.setdefault(bkt, []).append(r)
    if edge_groups:
        print(f"\n  {'─'*50}")
        print(f"  {'Edge tier':<10} {'Bets':>5} {'Won':>5} {'Win%':>6} {'Staked':>10} {'P&L':>10} {'ROI':>7}")
        print(f"  {'─'*50}")
        for bkt in EDGE_BUCKET_ORDER:
            rows = edge_groups.get(bkt, [])
            if not rows:
                continue
            n      = len(rows)
            won_n  = sum(1 for r in rows if r["result"] == "Win")
            staked = sum(float(r["stake_eur"]) for r in rows)
            pnl    = sum(float(r["pnl"]) for r in rows if r.get("pnl"))
            roi    = pnl / staked * 100 if staked > 0 else 0.0
            wr     = won_n / n * 100
            print(
                f"  {bkt:<10} {n:>5} {won_n:>5} {wr:>5.1f}% "
                f"EUR {staked:>7,.2f} EUR {pnl:>+7,.2f} {roi:>+6.1f}%"
            )

    # CLV summary
    clv_rows = [r for r in bet_rows if r.get("clv_pct") not in (None, "")]
    if clv_rows:
        avg_clv  = sum(float(r["clv_pct"]) for r in clv_rows) / len(clv_rows)
        pos_clv  = sum(1 for r in clv_rows if float(r["clv_pct"]) > 0)
        print(f"\n  {'─'*50}")
        print(f"  Closing Line Value  ({len(clv_rows)} bets with archived odds)")
        print(f"  Avg CLV       : {avg_clv:+.2f}%  {'(positive = beating the market)' if avg_clv > 0 else '(negative = buying late steam)'}")
        print(f"  Positive CLV  : {pos_clv}/{len(clv_rows)} bets  ({pos_clv/len(clv_rows)*100:.0f}%)")

    print(f"{sep}\n")

    # Accumulator summary
    if ACCAS_LOG.exists():
        with open(ACCAS_LOG, newline="", encoding="utf-8") as f:
            acca_rows = [r for r in csv.DictReader(f) if r["result"] not in ("", "Pending")]
        if acca_rows:
            a_total  = len(acca_rows)
            a_won    = sum(1 for r in acca_rows if r["result"] == "Win")
            a_staked = sum(float(r["stake"]) for r in acca_rows)
            a_pnl    = sum(float(r["pnl"]) for r in acca_rows if r["pnl"])
            a_roi    = a_pnl / a_staked * 100 if a_staked > 0 else 0.0
            print(f"  🎰 FUN ACCUMULATORS  (excluded from main P&L)")
            print(f"  {'─'*50}")
            print(f"  Total accas  : {a_total}  |  Won: {a_won}  |  Lost: {a_total - a_won}")
            print(f"  Total staked : EUR {a_staked:.2f}")
            print(f"  Total P&L    : EUR {a_pnl:+.2f}  (ROI: {a_roi:+.1f}%)")
            print()


def print_compare():
    """Side-by-side P&L comparison: original morning picks vs updated afternoon picks."""
    print_summary(RESULTS_LOG,         label="ORIGINAL PICKS  (morning predictions)")
    print_summary(RESULTS_LOG_UPDATED, label="UPDATED PICKS   (post movement check)")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=str(date.today() - timedelta(days=1)),
                        help="Date to settle (YYYY-MM-DD, default: yesterday)")
    parser.add_argument("--summary", action="store_true",
                        help="Print running P&L / win rate / ROI report and exit")
    parser.add_argument("--variant", choices=["original", "updated"], default="original",
                        help="Which predictions to settle: original (default) or updated (post movement check)")
    parser.add_argument("--compare", action="store_true",
                        help="Show P&L comparison: original picks vs updated picks side by side")
    args = parser.parse_args()

    if args.compare:
        print_compare()
        sys.exit(0)

    active_log = RESULTS_LOG_UPDATED if args.variant == "updated" else RESULTS_LOG

    if args.summary:
        label = "UPDATED PICKS (post movement check)" if args.variant == "updated" else "MLB BETTING P&L SUMMARY"
        print_summary(active_log, label=label)
        sys.exit(0)

    target_date = args.date

    # Load predictions JSON
    json_path = predictions_json_path(target_date, variant=args.variant)
    if not json_path.exists():
        print(f"No predictions JSON found at {json_path}")
        print("Make sure predict_today.py was run for that date.")
        sys.exit(1)

    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)

    predictions = payload.get("predictions", [])
    if not predictions:
        print("No predictions in JSON file.")
        sys.exit(0)

    print(f"\n=== Settling results for {target_date} ===\n")

    # Fetch actual scores
    print("Fetching MLB scores from API ...", end=" ", flush=True)
    scores = fetch_scores(target_date)
    print(f"{len(scores)} final games found")

    # Load closing odds for CLV calculation
    closing_odds_map = load_closing_odds(target_date)
    if closing_odds_map:
        print(f"Closing odds archive loaded ({len(closing_odds_map)} games)")
    else:
        print("No closing odds archive found — CLV will be blank (run /check-movement to capture odds)")

    # Load existing log + determine starting bankroll
    log_rows = read_log(active_log)
    bankroll = current_bankroll(log_rows)
    already_done = already_settled(log_rows, target_date)
    print(f"Current bankroll: EUR {bankroll:.2f}\n")

    new_rows = []
    bets_placed = 0
    bets_won    = 0
    total_pnl   = 0.0

    for row in predictions:
        game_pk   = str(row.get("gamePk", ""))
        home_team = row.get("homeAbbr", "")
        away_team = row.get("awayAbbr", "")
        pick_side = row.get("pickSide", "none")
        stake_eur = float(row.get("stake", {}).get("eur", 0.0))
        pick_odds = row.get("pickOdds")
        has_odds  = row.get("hasOdds", False)

        use_rl    = row.get("useRl", False)
        rl_odds   = row.get("rlPickOdds")

        # When RL is the primary bet, use RL odds for P&L (fall back to ML odds)
        active_odds = rl_odds if use_rl and rl_odds else pick_odds

        # Determine decision (mirrors decision_for_row logic)
        edge = row.get("edge", 0.0)
        if pick_side == "none" or not has_odds or active_odds is None or edge < 0.03 or stake_eur <= 0:
            decision = "SKIP"
        else:
            decision = "BET"

        pick_team = ""
        if pick_side == "home":
            pick_team = home_team + (" -1.5" if use_rl else "")
        elif pick_side == "away":
            pick_team = away_team + (" -1.5" if use_rl else "")

        if game_pk in already_done:
            print(f"  {away_team} @ {home_team}  — already settled, skipping")
            continue

        row_edge     = row.get("edge", 0.0) or 0.0
        row_edge_bkt = get_edge_bucket(row_edge)

        if decision != "BET":
            # Record SKIP rows so they appear in the log for completeness
            new_rows.append({
                "date":           target_date,
                "game_pk":        game_pk,
                "home_team":      home_team,
                "away_team":      away_team,
                "pick_side":      pick_side,
                "pick_team":      pick_team,
                "pick_odds":      pick_odds or "",
                "stake_eur":      f"{stake_eur:.2f}",
                "decision":       "SKIP",
                "result":         "N/A",
                "pnl":            "0.00",
                "bankroll_before": f"{bankroll:.2f}",
                "bankroll_after":  f"{bankroll:.2f}",
                "edge":           f"{row_edge:.4f}",
                "edge_bucket":    row_edge_bkt,
                "closing_odds":   "",
                "clv_pct":        "",
            })
            continue

        # BET — look up actual result
        game_pk_int = int(game_pk) if game_pk.isdigit() else None
        score = scores.get(game_pk_int) if game_pk_int else None

        if score is None:
            # Game not yet final (postponed / still in progress)
            print(f"  {away_team} @ {home_team}  [{pick_team} @ {active_odds:.2f}]  — not yet final, marking Pending")
            new_rows.append({
                "date":           target_date,
                "game_pk":        game_pk,
                "home_team":      home_team,
                "away_team":      away_team,
                "pick_side":      pick_side,
                "pick_team":      pick_team,
                "pick_odds":      f"{active_odds:.3f}",
                "stake_eur":      f"{stake_eur:.2f}",
                "decision":       "BET",
                "result":         "Pending",
                "pnl":            "",
                "bankroll_before": f"{bankroll:.2f}",
                "bankroll_after":  f"{bankroll:.2f}",
            })
            continue

        # Determine win/loss
        h_score = score["home_score"]
        a_score = score["away_score"]
        if use_rl:
            # Run line -1.5: favoured team must win by 2+
            if pick_side == "home":
                pick_won = h_score - a_score >= 2
            else:
                pick_won = a_score - h_score >= 2
        else:
            home_won = score["home_win"]
            pick_won = (pick_side == "home") == home_won

        result = "Win" if pick_won else "Loss"
        pnl    = round(stake_eur * (active_odds - 1), 2) if pick_won else round(-stake_eur, 2)
        bankroll_before = bankroll
        bankroll += pnl
        total_pnl += pnl
        bets_placed += 1
        if pick_won:
            bets_won += 1

        # CLV: look up closing odds from archive
        _cl_key_fwd = f"{away_team}_{home_team}"
        _cl_key_rev = f"{home_team}_{away_team}"
        _cl_entry   = closing_odds_map.get(_cl_key_fwd) or closing_odds_map.get(_cl_key_rev)
        _cl_odds    = None
        if _cl_entry:
            _cl_odds = _cl_entry.get("home_ml") if pick_side == "home" else _cl_entry.get("away_ml")
        closing_odds_str = f"{_cl_odds:.3f}" if _cl_odds else ""
        clv_str = compute_clv(active_odds, _cl_odds, pick_side) if _cl_odds else ""

        result_icon = "✅" if pick_won else "❌"
        clv_display = f"  CLV: {clv_str}%" if clv_str else ""
        print(
            f"  {result_icon} {away_team} @ {home_team}  "
            f"[{pick_team} @ {active_odds:.2f}]  "
            f"Score: {a_score}-{h_score}  "
            f"→ {result}  P&L: EUR {pnl:+.2f}  |  Bankroll: EUR {bankroll:.2f}"
            f"{clv_display}"
        )

        new_rows.append({
            "date":            target_date,
            "game_pk":         game_pk,
            "home_team":       home_team,
            "away_team":       away_team,
            "pick_side":       pick_side,
            "pick_team":       pick_team,
            "pick_odds":       f"{active_odds:.3f}",
            "stake_eur":       f"{stake_eur:.2f}",
            "decision":        "BET",
            "result":          result,
            "pnl":             f"{pnl:.2f}",
            "bankroll_before": f"{bankroll_before:.2f}",
            "bankroll_after":  f"{bankroll:.2f}",
            "edge":            f"{row_edge:.4f}",
            "edge_bucket":     row_edge_bkt,
            "closing_odds":    closing_odds_str,
            "clv_pct":         clv_str,
        })

    if new_rows:
        append_rows(new_rows, active_log)
        print(f"\nAppended {len(new_rows)} rows to {active_log.name}")

    if bets_placed:
        win_rate = bets_won / bets_placed * 100
        print(f"\n--- Day summary ---")
        print(f"  Bets placed : {bets_placed}")
        print(f"  Won         : {bets_won}  ({win_rate:.0f}%)")
        print(f"  Day P&L     : EUR {total_pnl:+.2f}")
        print(f"  New bankroll: EUR {bankroll:.2f}")
    else:
        print("\nNo bets were placed for this date.")

    # Settle accumulators separately (not in bankroll)
    accumulators = payload.get("accumulators", [])
    if accumulators:
        print(f"\n--- Accumulator results ---")
        settle_accumulators(accumulators, scores, target_date)

    print("\nDone.")

    # Auto-update prediction xlsx colours and rebuild results_log.xlsx
    try:
        import subprocess, sys as _sys
        subprocess.run(
            [_sys.executable, str(Path(__file__).parent / "build_tracker_xlsx.py"),
             "--date", target_date],
            check=True,
        )
    except Exception as exc:
        print(f"// build_tracker_xlsx: {exc} (non-fatal)", file=sys.stderr)
