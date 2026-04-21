"""
MLB €10,000 demo backtest — 2025 regular season.

Method:
  - Train on the first 40% of the 2025 season (warm-up period).
  - Walk-forward predict the remaining 60%.
  - Simulate betting against a standard MLB moneyline market:
      * Favourite (model prob > 0.55) -> decimal odds 1.85  (implied 54.1%)
      * Underdog  (model prob < 0.45) -> decimal odds 2.05  (implied 48.8%)
      * Near-coin-flip (0.45–0.55)   -> skip (no edge at -110/-110 for either side)
  - This approximates typical MLB vig: favourites ~-115 to -130, underdogs +105 to +120.
  - Bet is placed on the MODEL'S preferred side if model_prob > implied_prob + min_edge.
  - Quarter Kelly staking from a rolling bankroll (starts at €10,000).

NOTE: These are SIMULATED market odds, not real bookmaker lines.
      Real-world odds would vary game-by-game. This models
      a consistent -115-ish market to test model signal quality.

Usage:
    python mlb/scripts/backtest.py
    python mlb/scripts/backtest.py --min-edge 0.03
    python mlb/scripts/backtest.py --bankroll 10000 --min-edge 0.02
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

sys.path.append(str(Path(__file__).parent.parent.parent))
from betfair.kelly import kelly_stake, implied_probability
from mlb.scripts.model import FEATURES, load as load_data, date_split, train as train_model

PROC_DIR = Path(__file__).parent.parent / "data" / "processed"

# ─── SIMULATED MARKET ODDS ────────────────────────────────────────────────────
# Standard MLB moneyline market with typical vig:
#   Favourite  (-115 American) -> decimal 1.870  (implied prob 53.5%)
#   Underdog   (+105 American) -> decimal 2.050  (implied prob 48.8%)
# If model prob > implied_prob + min_edge, we bet.
FAV_ODDS  = 1.870   # ~-115 ML
DOG_ODDS  = 2.050   # ~+105 ML
COIN_BAND = 0.05    # skip games within 50% ± 5% (too close to call)

KELLY_FRAC = 0.25   # quarter Kelly

# ─── CORE SIMULATION ──────────────────────────────────────────────────────────

def simulate(test_df: pd.DataFrame, model, scaler, min_edge: float,
             bankroll: float) -> list[dict]:
    """
    Stakes are sized using quarter Kelly against the FIXED starting bankroll
    (not rolling). This matches the football/NBA models in this repo and avoids
    unrealistic exponential compounding over hundreds of bets.
    Cumulative P&L is tracked separately to show net result.
    """
    bets = []
    cumulative_pnl = 0.0

    for _, row in test_df.iterrows():
        if any(pd.isna(row.get(f)) for f in FEATURES):
            continue

        X         = np.array([[row[f] for f in FEATURES]])
        home_prob = float(model.predict_proba(scaler.transform(X))[0][1])
        away_prob = 1.0 - home_prob

        # Determine which side (if any) to bet
        candidates = []
        if home_prob > 0.5 + COIN_BAND:
            candidates.append(("home", home_prob, FAV_ODDS))
        if away_prob > 0.5 + COIN_BAND:
            candidates.append(("away", away_prob, DOG_ODDS))

        for side, model_prob, odds in candidates:
            implied = implied_probability(odds)
            edge    = model_prob - implied
            if edge < min_edge:
                continue

            # Stake as fraction of STARTING bankroll (fixed, not rolling)
            k     = kelly_stake(model_prob, odds, KELLY_FRAC)
            stake = round(bankroll * k, 2)
            if stake <= 0.01:
                continue

            won = (side == "home") == bool(row["home_win"])
            pnl = round(stake * (odds - 1) if won else -stake, 2)
            cumulative_pnl += pnl

            bets.append({
                "date":        row["game_date"],
                "home_team":   row["home_team"],
                "away_team":   row["away_team"],
                "side":        side,
                "model_prob":  round(model_prob, 4),
                "implied_prob": round(implied, 4),
                "edge":        round(edge, 4),
                "odds":        odds,
                "stake":       stake,
                "won":         won,
                "pnl":         pnl,
                "bankroll":    round(bankroll + cumulative_pnl, 2),
                "home_era":    row.get("HOME_SP_ERA"),
                "away_era":    row.get("AWAY_SP_ERA"),
                "home_l10_wp": row.get("HOME_L10_WIN_PCT"),
                "away_l10_wp": row.get("AWAY_L10_WIN_PCT"),
            })

    return bets


# ─── REPORTING ────────────────────────────────────────────────────────────────

def summarise(bets: pd.DataFrame, bankroll: float, min_edge: float):
    if bets.empty:
        print("  No bets placed. Try lowering --min-edge.")
        return

    n         = len(bets)
    staked    = bets["stake"].sum()
    pnl_total = bets["pnl"].sum()
    roi       = pnl_total / staked * 100
    win_rate  = bets["won"].mean() * 100
    final     = bankroll + pnl_total

    cum = bets.sort_values("date")["pnl"].cumsum()
    peak  = (bankroll + cum).max()
    dd    = (cum - cum.cummax()).min()   # largest drawdown in P&L terms

    print(f"\n{'='*62}")
    print(f"  MLB 2025 BACKTEST  (min edge {min_edge:.0%}, quarter Kelly)")
    print(f"{'='*62}")
    print(f"  Starting bankroll : €{bankroll:,.2f}")
    print(f"  Final bankroll    : €{final:,.2f}  ({(final-bankroll)/bankroll:+.1%})")
    print(f"  Total bets placed : {n}")
    print(f"  Total staked      : €{staked:,.2f}")
    print(f"  Total P&L         : €{pnl_total:+,.2f}")
    print(f"  ROI on staked     : {roi:+.2f}%")
    print(f"  Win rate          : {win_rate:.1f}%")
    print(f"  Avg model edge    : {bets['edge'].mean():+.2%}")
    print(f"  Peak bankroll     : €{peak:,.2f}")
    print(f"  Max drawdown      : €{dd:,.2f}")

    # By side
    print(f"\n  --- By bet side ---")
    for side, g in bets.groupby("side"):
        s = g["pnl"].sum()
        r = s / g["stake"].sum() * 100
        print(f"  {side:4s}: {len(g):3d} bets | "
              f"staked €{g['stake'].sum():7,.2f} | "
              f"P&L €{s:+7,.2f} | ROI {r:+.1f}%")

    # By month
    bets["month"] = pd.to_datetime(bets["date"]).dt.to_period("M")
    print(f"\n  --- By month ---")
    for month, g in bets.groupby("month"):
        s = g["pnl"].sum()
        r = s / g["stake"].sum() * 100
        print(f"  {month}: {len(g):3d} bets | "
              f"P&L €{s:+7,.2f} | ROI {r:+.1f}%")

    # P&L curve milestones
    print(f"\n  --- Bankroll curve (every 50 bets) ---")
    snapshots = bets.iloc[::50][["date", "bankroll"]]
    for _, r in snapshots.iterrows():
        bar_val = r["bankroll"] - bankroll
        bar = "+" * min(int(abs(bar_val) / 100), 20) if bar_val > 0 else "-" * min(int(abs(bar_val) / 100), 20)
        sign = "^" if bar_val > 0 else "v"
        print(f"  {r['date']}  EUR {r['bankroll']:8,.2f}  {sign} {bar}")

    # Top wins / worst losses
    print(f"\n  --- Best 5 bets ---")
    for _, r in bets.nlargest(5, "pnl").iterrows():
        print(f"  {r['date']}  {r['away_team']} @ {r['home_team']}  "
              f"[{r['side'].upper()}]  model={r['model_prob']:.1%}  "
              f"P&L €{r['pnl']:+.2f}")

    print(f"\n  --- Worst 5 bets ---")
    for _, r in bets.nsmallest(5, "pnl").iterrows():
        print(f"  {r['date']}  {r['away_team']} @ {r['home_team']}  "
              f"[{r['side'].upper()}]  model={r['model_prob']:.1%}  "
              f"P&L €{r['pnl']:+.2f}")

    # Which lines are worth targeting?
    print(f"\n  --- Edge buckets (what confidence level beats the market?) ---")
    bets["edge_bucket"] = pd.cut(bets["model_prob"],
                                  bins=[0.55, 0.58, 0.61, 0.64, 0.68, 1.0],
                                  labels=["55-58%","58-61%","61-64%","64-68%","68%+"])
    for bucket, g in bets.groupby("edge_bucket", observed=True):
        if g.empty: continue
        r = g["pnl"].sum() / g["stake"].sum() * 100
        print(f"  Model prob {bucket}: {len(g):3d} bets | "
              f"win {g['won'].mean():.1%} | ROI {r:+.1f}%")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bankroll", type=float, default=10000.0)
    parser.add_argument("--min-edge", type=float, default=0.02)
    parser.add_argument("--train-frac", type=float, default=0.40)
    args = parser.parse_args()

    print("\n=== MLB 2025  €10,000 Demo Backtest ===\n")

    df = load_data()
    print(f"  {len(df)} games loaded")

    train_df, test_df, cutoff = date_split(df, args.train_frac)
    print(f"  Train : {len(train_df)} games  (before {cutoff.date()})")
    print(f"  Test  : {len(test_df)} games  (from   {cutoff.date()})")

    print("\n  Training model on first 40% of season ...")
    model, scaler = train_model(train_df)

    print(f"\n  NOTE: Pitcher ERA/WHIP/K9 use full-season 2025 totals (slight")
    print(f"  look-ahead bias for mid-season games). Rolling team form is")
    print(f"  strictly no look-ahead. Stakes vs fixed starting bankroll.\n")
    print(f"\n  Simulating {len(test_df)} games "
          f"at min-edge {args.min_edge:.0%}, quarter Kelly ...\n")
    bets_list = simulate(test_df, model, scaler, args.min_edge, args.bankroll)

    if not bets_list:
        print("  No bets met the edge threshold.")
    else:
        bets_df = pd.DataFrame(bets_list)
        bets_df.to_csv(
            Path(__file__).parent.parent / "data" / "processed" / "backtest_bets.csv",
            index=False,
        )
        summarise(bets_df, args.bankroll, args.min_edge)

    print("\nDone.")
