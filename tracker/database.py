"""
Bet tracker — logs all bets to SQLite (no setup needed) or PostgreSQL.
Tracks P&L, ROI, and closing line value over time.
"""

import sqlite3
import pandas as pd
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "bets.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            sport TEXT,
            market TEXT,
            selection TEXT,
            side TEXT,
            model_prob REAL,
            odds REAL,
            implied_prob REAL,
            edge REAL,
            stake REAL,
            result TEXT,
            pnl REAL,
            closing_odds REAL,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    print(f"Database ready at {DB_PATH}")


def log_bet(sport, market, selection, side, model_prob, odds, stake, notes=""):
    conn = sqlite3.connect(DB_PATH)
    implied = round(1 / odds, 4)
    edge = round(model_prob - implied, 4)
    conn.execute("""
        INSERT INTO bets (date, sport, market, selection, side, model_prob, odds, implied_prob, edge, stake, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d"), sport, market, selection, side, model_prob, odds, implied, edge, stake, notes))
    conn.commit()
    conn.close()
    print(f"Logged: {selection} {side} @ {odds} | Edge: {edge:+.2%}")


def settle_bet(bet_id: int, result: str, pnl: float, closing_odds: float = None):
    """result: 'WIN' or 'LOSS'"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        UPDATE bets SET result=?, pnl=?, closing_odds=? WHERE id=?
    """, (result, pnl, closing_odds, bet_id))
    conn.commit()
    conn.close()
    print(f"Settled bet {bet_id}: {result} | P&L: €{pnl:+.2f}")


def get_summary() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM bets WHERE result IS NOT NULL", conn)
    conn.close()

    if df.empty:
        print("No settled bets yet.")
        return df

    total_staked = df["stake"].sum()
    total_pnl = df["pnl"].sum()
    roi = (total_pnl / total_staked) * 100 if total_staked > 0 else 0
    win_rate = (df["result"] == "WIN").mean() * 100

    print(f"\n{'='*40}")
    print(f"BETTING SUMMARY ({len(df)} bets)")
    print(f"{'='*40}")
    print(f"Total Staked: €{total_staked:.2f}")
    print(f"Total P&L:    €{total_pnl:+.2f}")
    print(f"ROI:          {roi:+.2f}%")
    print(f"Win Rate:     {win_rate:.1f}%")
    print(f"Avg Edge:     {df['edge'].mean():+.2%}")

    return df


if __name__ == "__main__":
    init_db()
    get_summary()
