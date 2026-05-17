"""
backtest.py — Historical backtest pipeline for the UFC betting model.

Replays settled events from the ledger using only data that was available
BEFORE each fight. Simulates bankroll growth under the new conservative
staking rules and reports calibration-grade performance metrics.

Usage:
    python backtest.py --from 2022-01 --to 2025-12
    python backtest.py --from 2023-01 --to 2024-12 --bankroll 500 --output results.json
    python backtest.py --from 2022-01 --to 2025-12 --edge-min 6

Arguments:
    --from      Start month inclusive, format YYYY-MM
    --to        End month inclusive, format YYYY-MM
    --bankroll  Starting bankroll in EUR (default: 500)
    --edge-min  Minimum edge % to include a bet (default: 4.0)
    --stake-pct Flat stake as % of bankroll (default: 0.5)
    --output    Optional path to write JSON results
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from utils import DATA_PROC

BETTING_DIR = DATA_PROC / "betting"
DB_PATH = BETTING_DIR / "ledger.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _ym_to_date(ym: str, end: bool = False) -> str:
    """Convert YYYY-MM to a date string safe for SQLite comparisons."""
    try:
        datetime.strptime(ym, "%Y-%m")
    except ValueError:
        raise ValueError(f"Date must be in YYYY-MM format, got: {ym!r}")
    if end:
        # Last day of any month is <= the 1st of the next month for string comparison
        year, month = int(ym[:4]), int(ym[5:])
        if month == 12:
            return f"{year + 1}-01-01"
        return f"{year}-{month + 1:02d}-01"
    return ym + "-01"


def load_settled_events(from_ym: str, to_ym: str) -> list[dict]:
    """
    Pull settled events with their bets from the ledger, within [from_ym, to_ym].
    Only single bets are included (no accumulators).
    """
    from_date = _ym_to_date(from_ym)
    to_date = _ym_to_date(to_ym, end=True)

    with get_db() as conn:
        events = conn.execute(
            """
            SELECT event_id, event_name, event_date
            FROM events
            WHERE status IN ('settled', 'archived')
              AND event_date >= ?
              AND event_date < ?
            ORDER BY event_date
            """,
            (from_date, to_date),
        ).fetchall()

        result = []
        for ev in events:
            bets = conn.execute(
                """
                SELECT bet_id, fight, market, selection,
                       odds, decimal_odds, stake_eur,
                       edge, confidence, model_probability,
                       implied_probability, status, pnl,
                       bankroll_before, bankroll_after, bet_type
                FROM bets
                WHERE event_id = ?
                  AND bet_type = 'single'
                  AND status IN ('won', 'lost', 'push')
                ORDER BY id
                """,
                (ev["event_id"],),
            ).fetchall()
            result.append({
                "event_id": ev["event_id"],
                "event_name": ev["event_name"],
                "event_date": ev["event_date"],
                "bets": [dict(b) for b in bets],
            })

    return result


def simulate_bankroll(
    events: list[dict],
    starting_bankroll: float = 500.0,
    stake_pct: float = 0.005,
    edge_min: float = 4.0,
    max_card_exposure_pct: float = 0.05,
) -> dict:
    """
    Simulate bankroll evolution over the event sequence.

    Uses flat staking (stake_pct of current bankroll per bet), subject to
    the per-card exposure cap. Bets below edge_min are excluded.
    Actual recorded P&L from the ledger is used — not re-simulated.
    """
    bankroll = starting_bankroll
    equity_curve = [
        {"date": "start", "event": "Starting bankroll", "bankroll": bankroll}
    ]

    total_staked = 0.0
    total_pnl = 0.0
    wins = losses = pushes = 0
    peak_bankroll = bankroll
    max_drawdown_eur = 0.0
    bet_returns: list[float] = []

    event_results: list[dict] = []

    for ev in events:
        card_staked = 0.0
        card_pnl = 0.0
        card_exposure_cap = bankroll * max_card_exposure_pct
        ev_bets_included = []

        for bet in ev.get("bets", []):
            edge_val = bet.get("edge")
            if edge_val is None:
                continue
            try:
                edge_f = float(edge_val)
            except (TypeError, ValueError):
                continue
            if edge_f < edge_min:
                continue

            stake = round(bankroll * stake_pct, 2)
            if card_staked + stake > card_exposure_cap:
                continue

            status = bet.get("status", "")
            pnl = float(bet.get("pnl") or 0)

            if status == "won":
                wins += 1
                bankroll += pnl
                card_pnl += pnl
            elif status == "lost":
                losses += 1
                bankroll += pnl  # pnl is negative
                card_pnl += pnl
            elif status == "push":
                pushes += 1
                # bankroll unchanged on push

            card_staked += stake
            total_staked += stake
            total_pnl += pnl

            bet_returns.append(pnl / starting_bankroll)

            if bankroll > peak_bankroll:
                peak_bankroll = bankroll
            drawdown = peak_bankroll - bankroll
            if drawdown > max_drawdown_eur:
                max_drawdown_eur = drawdown

            ev_bets_included.append({
                "fight": bet.get("fight"),
                "selection": bet.get("selection"),
                "edge": edge_f,
                "status": status,
                "pnl": round(pnl, 2),
            })

        if card_staked > 0:
            equity_curve.append({
                "date": ev["event_date"],
                "event": ev["event_name"],
                "bankroll": round(bankroll, 2),
                "card_pnl": round(card_pnl, 2),
                "card_staked": round(card_staked, 2),
                "bets": len(ev_bets_included),
            })
            event_results.append({
                "event_id": ev["event_id"],
                "event_name": ev["event_name"],
                "event_date": ev["event_date"],
                "bets_included": len(ev_bets_included),
                "card_staked": round(card_staked, 2),
                "card_pnl": round(card_pnl, 2),
                "bankroll_after": round(bankroll, 2),
            })

    total_bets = wins + losses + pushes
    roi = round(total_pnl / total_staked * 100, 2) if total_staked > 0 else 0.0
    hit_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else None
    max_drawdown_pct = round(max_drawdown_eur / peak_bankroll * 100, 1) if peak_bankroll > 0 else 0.0

    # Sharpe-like metric: annualised return / volatility of per-bet returns
    sharpe = None
    if len(bet_returns) > 1:
        mean_r = sum(bet_returns) / len(bet_returns)
        variance = sum((r - mean_r) ** 2 for r in bet_returns) / len(bet_returns)
        std_r = math.sqrt(variance)
        if std_r > 0:
            # Scale by sqrt(n) as a simple risk-adjusted metric
            sharpe = round(mean_r / std_r * math.sqrt(len(bet_returns)), 2)

    return {
        "from_bankroll": starting_bankroll,
        "final_bankroll": round(bankroll, 2),
        "bankroll_change_pct": round((bankroll - starting_bankroll) / starting_bankroll * 100, 1),
        "total_bets": total_bets,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": hit_rate,
        "total_staked": round(total_staked, 2),
        "total_pnl": round(total_pnl, 2),
        "roi": roi,
        "max_drawdown_eur": round(max_drawdown_eur, 2),
        "max_drawdown_pct": max_drawdown_pct,
        "peak_bankroll": round(peak_bankroll, 2),
        "sharpe_ratio": sharpe,
        "equity_curve": equity_curve,
        "event_results": event_results,
    }


def run_backtest(
    from_ym: str,
    to_ym: str,
    starting_bankroll: float = 500.0,
    stake_pct: float = 0.005,
    edge_min: float = 4.0,
) -> dict:
    """Main entry point. Loads ledger history and runs simulation."""
    print(f"Loading settled events {from_ym} → {to_ym} from ledger...")
    events = load_settled_events(from_ym, to_ym)
    n_bets = sum(len(ev["bets"]) for ev in events)
    print(f"Found {len(events)} events, {n_bets} total single bets.")

    if not events:
        return {
            "error": f"No settled events found between {from_ym} and {to_ym}.",
            "hint": "Run the settlement pipeline on past events first.",
        }

    print(f"Simulating with {stake_pct*100:.1f}% flat staking, edge >= {edge_min}%...")
    results = simulate_bankroll(
        events,
        starting_bankroll=starting_bankroll,
        stake_pct=stake_pct,
        edge_min=edge_min,
    )

    results.update({
        "from": from_ym,
        "to": to_ym,
        "events_total": len(events),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "parameters": {
            "stake_pct": round(stake_pct * 100, 2),
            "edge_min_pct": edge_min,
            "starting_bankroll": starting_bankroll,
            "max_card_exposure_pct": 5.0,
            "notes": "Moneyline singles only. Flat staking. No accumulators.",
        },
    })

    return results


def _print_results(r: dict) -> None:
    print()
    print("=" * 64)
    print("  UFC BETTING MODEL — BACKTEST RESULTS")
    print("=" * 64)
    print(f"  Period          : {r['from']} → {r['to']}")
    print(f"  Events          : {r['events_total']}")
    print(f"  Starting bank   : EUR {r['from_bankroll']:.2f}")
    print(f"  Final bank      : EUR {r['final_bankroll']:.2f}  ({r['bankroll_change_pct']:+.1f}%)")
    print(f"  Total bets      : {r['total_bets']} ({r['wins']}W / {r['losses']}L / {r['pushes']}P)")
    print(f"  Hit rate        : {r['hit_rate']}%")
    print(f"  Total staked    : EUR {r['total_staked']:.2f}")
    print(f"  Total P&L       : EUR {r['total_pnl']:.2f}")
    print(f"  ROI             : {r['roi']:+.2f}%")
    print(f"  Max drawdown    : EUR {r['max_drawdown_eur']:.2f}  ({r['max_drawdown_pct']}%)")
    print(f"  Peak bankroll   : EUR {r['peak_bankroll']:.2f}")
    if r.get("sharpe_ratio") is not None:
        print(f"  Sharpe ratio    : {r['sharpe_ratio']}")
    print("=" * 64)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="UFC Betting Model — Historical Backtest Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--from", dest="from_ym", required=True, metavar="YYYY-MM")
    parser.add_argument("--to", dest="to_ym", required=True, metavar="YYYY-MM")
    parser.add_argument("--bankroll", type=float, default=500.0, metavar="EUR")
    parser.add_argument("--stake-pct", type=float, default=0.5,
                        help="Flat stake as %% of bankroll (default: 0.5)")
    parser.add_argument("--edge-min", type=float, default=4.0,
                        help="Minimum edge %% to include a bet (default: 4.0)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Write JSON results to this file")
    args = parser.parse_args()

    results = run_backtest(
        from_ym=args.from_ym,
        to_ym=args.to_ym,
        starting_bankroll=args.bankroll,
        stake_pct=args.stake_pct / 100,
        edge_min=args.edge_min,
    )

    if "error" in results:
        print(f"[ERROR] {results['error']}")
        if "hint" in results:
            print(f"[HINT]  {results['hint']}")
        return

    _print_results(results)

    if args.output:
        args.output.write_text(json.dumps(results, indent=2, default=str))
        print(f"\nResults written to: {args.output}")


if __name__ == "__main__":
    main()
