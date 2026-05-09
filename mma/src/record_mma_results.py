"""
record_mma_results.py - Auto-settle MMA bets by scraping UFC fight results.

Finds the most recent unsettled staking plan archive, fetches fight results
from UFCStats, settles each bet (Win/Loss/Push), and appends to bet_history.csv.

Usage:
    python src/record_mma_results.py               # settle latest unsettled event
    python src/record_mma_results.py --event 2026-04-25  # match by date prefix
    python src/record_mma_results.py --summary     # show running P&L
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bs4 import BeautifulSoup
from utils import DATA_PROC, get_logger, fetch_html, load_json, save_json

log = get_logger("record_results")

BETTING_DIR     = DATA_PROC / "betting"
BET_HISTORY_CSV = BETTING_DIR / "bet_history.csv"
BASE_BANKROLL   = 500.0

BET_HISTORY_FIELDS = [
    "date", "event", "fight", "market", "selection",
    "sportsbook", "odds", "stake_eur",
    "result", "pnl", "bankroll_before", "bankroll_after",
    "winner", "method", "round", "time", "notes",
]


# ── bankroll / history ────────────────────────────────────────────────────────

def load_history() -> list[dict]:
    if not BET_HISTORY_CSV.exists():
        return []
    with BET_HISTORY_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def current_bankroll() -> float:
    rows = load_history()
    settled = [r for r in rows if r.get("result") in {"Win", "Loss", "Push"} and r.get("bankroll_after")]
    return float(settled[-1]["bankroll_after"]) if settled else BASE_BANKROLL


def append_history(rows: list[dict]) -> None:
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not BET_HISTORY_CSV.exists() or BET_HISTORY_CSV.stat().st_size == 0
    with BET_HISTORY_CSV.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=BET_HISTORY_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in BET_HISTORY_FIELDS})


# ── archive discovery ─────────────────────────────────────────────────────────

def find_staking_archives() -> list[Path]:
    """All event-stamped staking plan archives, newest first."""
    return sorted(BETTING_DIR.glob("staking_plan_2*.json"), reverse=True)


def already_settled_events() -> set[str]:
    return {r["event"] for r in load_history() if r.get("event")}


def find_unsettled_archive(event_prefix: str | None = None) -> Path | None:
    settled = already_settled_events()
    for path in find_staking_archives():
        if event_prefix and event_prefix not in path.stem:
            continue
        try:
            plan = load_json(path)
        except Exception:
            continue
        if plan.get("event_name", path.stem) not in settled:
            return path
    return None


# ── UFC results scraping ──────────────────────────────────────────────────────

def _normalize_method(raw: str) -> str:
    m = raw.upper().strip()
    if not m:
        return ""
    if "KO" in m or "TKO" in m:
        return "KO/TKO"
    if "SUB" in m:
        return "Submission"
    if "DEC" in m:
        return "Decision"
    if "DRAW" in m:
        return "Draw"
    if "NC" in m or "NO CONTEST" in m:
        return "No Contest"
    return raw.strip()


def fetch_event_results(event_url: str) -> list[dict]:
    """
    Scrape a completed UFCStats event page and return one result dict per bout.

    On UFCStats, completed-fight rows list the WINNER first (fighter_a).
    Returns: [{fighter_a, fighter_b, winner, loser, method, round, time}, ...]
    """
    log.info("Fetching results from %s", event_url)
    # Always bypass cache so we get live post-event data
    html = fetch_html(event_url, cache_path=None, throttle=1.0)
    soup = BeautifulSoup(html, "html.parser")

    results = []
    rows = soup.select("tr.b-fight-details__table-row.js-fight-details-click")

    for row in rows:
        fighter_links = row.select('a[href*="fighter-details"]')
        if len(fighter_links) < 2:
            continue

        name_a = fighter_links[0].get_text(strip=True)
        name_b = fighter_links[1].get_text(strip=True)
        tds    = row.find_all("td")

        # Event page column layout (0-indexed):
        # 0-fighters  1-KD  2-STR  3-TD  4-SUB  5-PASS  6-WEIGHT  7-METHOD  8-ROUND  9-TIME
        method = _normalize_method(tds[7].get_text(strip=True) if len(tds) > 7 else "")
        round_ = tds[8].get_text(strip=True) if len(tds) > 8 else ""
        time_  = tds[9].get_text(strip=True) if len(tds) > 9 else ""

        # UFCStats convention: first fighter = winner for completed bouts.
        # If method is empty the bout is still upcoming — skip it.
        if not method:
            continue

        results.append({
            "fighter_a": name_a,
            "fighter_b": name_b,
            "winner":    name_a,
            "loser":     name_b,
            "method":    method,
            "round":     round_,
            "time":      time_,
        })
        log.info("  %s def. %s | %s R%s %s", name_a, name_b, method, round_, time_)

    return results


# ── bet settlement ────────────────────────────────────────────────────────────

def _name_in(name: str, text: str) -> bool:
    """True if fighter last name (or full name) appears in text."""
    name_l = name.lower().strip()
    text_l = text.lower().strip()
    if name_l in text_l:
        return True
    last = name_l.split()[-1]
    return len(last) > 3 and last in text_l


def _fight_minutes(round_: str, time_: str) -> float | None:
    """Convert round + elapsed time to total minutes into the fight."""
    try:
        r = int(round_)
        parts = time_.split(":")
        elapsed = int(parts[0]) + int(parts[1]) / 60
        return (r - 1) * 5.0 + elapsed
    except Exception:
        return None


def settle_bet(bet: dict, result: dict | None) -> tuple[str, float]:
    """Return (outcome, pnl). outcome: Win | Loss | Push | Pending | N/A."""
    if result is None:
        return "Pending", 0.0

    stake        = float((bet.get("stake") or {}).get("eur") or 0)
    odds_decimal = float(bet.get("decimal_odds") or 0)
    market       = bet.get("market", "")
    selection    = bet.get("selection", "")
    winner       = result.get("winner", "")
    method       = result.get("method", "")
    round_       = result.get("round", "")
    time_        = result.get("time", "")

    won: bool | None = None

    # ── Moneyline ──────────────────────────────────────────────────────────────
    if market == "Moneyline":
        if not winner:
            return "Pending", 0.0
        won = _name_in(winner, selection)

    # ── Rounds total  e.g. "Total Rounds 2.5" ─────────────────────────────────
    elif "Total Rounds" in market:
        total_min = _fight_minutes(round_, time_)
        if total_min is None:
            return "Pending", 0.0
        m = re.search(r"(\d+\.?\d*)", market)
        if not m:
            return "Pending", 0.0
        line_min = float(m.group(1)) * 5.0
        is_over  = "over" in selection.lower()
        won = (total_min > line_min) if is_over else (total_min < line_min)

    # ── Method props  e.g. "Jack Della Maddalena by KO/TKO" ───────────────────
    elif " by " in market:
        if not winner or not method:
            return "Pending", 0.0
        parts = market.split(" by ", 1)
        fighter_part, method_part = parts[0].strip(), parts[1].strip()
        fighter_won  = _name_in(winner, fighter_part)
        method_match = method_part.lower() in method.lower()
        won = fighter_won and method_match

    # ── Fight Goes Distance ────────────────────────────────────────────────────
    elif "Fight Goes Distance" in market or "Goes Distance" in market:
        if not method:
            return "Pending", 0.0
        went_distance = method == "Decision"
        is_yes = "yes" in selection.lower()
        won = went_distance == is_yes

    else:
        return "N/A", 0.0

    if won is None:
        return "Pending", 0.0

    if won:
        return "Win", round(stake * (odds_decimal - 1), 2)
    return "Loss", round(-stake, 2)


def _find_result(bet: dict, results: list[dict]) -> dict | None:
    """Match a bet's fight string to a scraped result."""
    fight_l = bet.get("fight", "").lower()
    for r in results:
        a = r["fighter_a"].lower()
        b = r["fighter_b"].lower()
        # Match if both fighter names appear in the fight string
        if (a.split()[-1] in fight_l or a in fight_l) and \
           (b.split()[-1] in fight_l or b in fight_l):
            return r
        # Or if one fighter's name from the result is in the selection
        sel = bet.get("selection", "").lower()
        if (a.split()[-1] in sel or b.split()[-1] in sel):
            if a.split()[-1] in fight_l or b.split()[-1] in fight_l:
                return r
    return None


def settle_event(plan: dict, event_results: list[dict]) -> list[dict]:
    event_name = plan.get("event_name", "Unknown Event")
    event_date = plan.get("event_date", "")
    bankroll   = current_bankroll()
    rows       = []

    for bet in plan.get("singles", []):
        result  = _find_result(bet, event_results)
        outcome, pnl = settle_bet(bet, result)

        bankroll_before = bankroll
        bankroll_after  = round(bankroll + pnl, 2) if outcome in {"Win", "Loss", "Push"} else bankroll
        if outcome in {"Win", "Loss", "Push"}:
            bankroll = bankroll_after

        rows.append({
            "date":            event_date,
            "event":           event_name,
            "fight":           bet.get("fight", ""),
            "market":          bet.get("market", ""),
            "selection":       bet.get("selection", ""),
            "sportsbook":      bet.get("sportsbook", ""),
            "odds":            bet.get("odds", ""),
            "stake_eur":       (bet.get("stake") or {}).get("eur", ""),
            "result":          outcome,
            "pnl":             pnl if outcome in {"Win", "Loss", "Push"} else "",
            "bankroll_before": bankroll_before,
            "bankroll_after":  bankroll_after,
            "winner":          (result or {}).get("winner", ""),
            "method":          (result or {}).get("method", ""),
            "round":           (result or {}).get("round", ""),
            "time":            (result or {}).get("time", ""),
            "notes":           "",
        })

    return rows


# ── display ───────────────────────────────────────────────────────────────────

def print_settlement(rows: list[dict], event_name: str) -> None:
    wins    = [r for r in rows if r["result"] == "Win"]
    losses  = [r for r in rows if r["result"] == "Loss"]
    pending = [r for r in rows if r["result"] == "Pending"]
    staked  = sum(float(r["stake_eur"]) for r in rows if r.get("stake_eur"))
    pnl     = sum(float(r["pnl"]) for r in rows if r.get("pnl") != "" and r.get("pnl") is not None)

    print()
    print("=" * 68)
    print(f"  SETTLEMENT: {event_name}")
    print("=" * 68)
    print(f"  {len(wins)}W - {len(losses)}L"
          + (f" - {len(pending)} Pending" if pending else "")
          + f"  |  Staked: EUR {staked:.2f}  |  P&L: EUR {pnl:+.2f}")
    if rows:
        last_br = rows[-1].get("bankroll_after") or rows[-1].get("bankroll_before")
        print(f"  Bankroll now: EUR {float(last_br):.2f}")
    print()
    print(f"  {'FIGHT':<36} {'SELECTION':<26} {'RESULT':<9} {'P&L':>8}  RESULT DETAIL")
    print("-" * 100)
    for r in rows:
        fight  = r["fight"][:35]
        sel    = r["selection"][:25]
        res    = r["result"]
        pnl_s  = f"EUR {float(r['pnl']):+.2f}" if r.get("pnl") not in ("", None) else "--"
        detail = f"{r['winner']} | {r['method']} R{r['round']} {r['time']}" if r.get("winner") else ""
        print(f"  {fight:<36} {sel:<26} {res:<9} {pnl_s:>8}  {detail}")
    print()


def print_summary() -> None:
    rows    = load_history()
    settled = [r for r in rows if r.get("result") in {"Win", "Loss", "Push"}]
    if not settled:
        print("No settled bets yet.")
        return

    wins   = [r for r in settled if r["result"] == "Win"]
    losses = [r for r in settled if r["result"] == "Loss"]
    staked = sum(float(r.get("stake_eur") or 0) for r in settled)
    pnl    = sum(float(r.get("pnl") or 0) for r in settled)
    br     = float(settled[-1].get("bankroll_after") or BASE_BANKROLL)
    rate   = len(wins) / len(settled) * 100 if settled else 0
    roi    = pnl / staked * 100 if staked else 0

    print()
    print("=" * 50)
    print("  MMA BETTING — RUNNING SUMMARY")
    print("=" * 50)
    print(f"  Bankroll   : EUR {br:.2f}  (base EUR {BASE_BANKROLL:.0f})")
    print(f"  Record     : {len(wins)}W - {len(losses)}L")
    print(f"  Win rate   : {rate:.1f}%")
    print(f"  Staked     : EUR {staked:.2f}")
    print(f"  P&L        : EUR {pnl:+.2f}")
    print(f"  ROI        : {roi:+.1f}%")
    print()

    events: dict[str, list] = {}
    for r in settled:
        events.setdefault(r.get("event", "Unknown"), []).append(r)

    if len(events) > 1:
        print(f"  {'EVENT':<38} {'W':>3} {'L':>3} {'P&L':>9}")
        print("-" * 50)
        for ev, ev_rows in events.items():
            w   = sum(1 for r in ev_rows if r["result"] == "Win")
            l   = sum(1 for r in ev_rows if r["result"] == "Loss")
            ep  = sum(float(r.get("pnl") or 0) for r in ev_rows)
            print(f"  {ev[:37]:<38} {w:>3} {l:>3} {ep:>+9.2f}")
        print()


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Settle MMA bets from UFCStats results.")
    parser.add_argument("--event",   default=None,
                        help="Date prefix or slug to settle, e.g. '2026-04-25'")
    parser.add_argument("--summary", action="store_true",
                        help="Print running P&L summary and exit")
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return

    archive = find_unsettled_archive(args.event)
    if archive is None:
        print("No unsettled event archives found.")
        print_summary()
        return

    plan       = load_json(archive)
    event_name = plan.get("event_name", archive.stem)
    event_url  = plan.get("event_url", "")
    event_date = plan.get("event_date", "")

    print(f"Settling : {event_name} ({event_date})")
    print(f"Archive  : {archive.name}")

    if not event_url:
        print("ERROR: No event_url in archive. Re-run the pipeline to regenerate.")
        return

    event_results = fetch_event_results(event_url)
    if not event_results:
        print("No completed fight results found — event may not have happened yet.")
        return

    rows = settle_event(plan, event_results)
    append_history(rows)
    print_settlement(rows, event_name)
    print_summary()


if __name__ == "__main__":
    main()
