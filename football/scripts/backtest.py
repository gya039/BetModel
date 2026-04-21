"""
Walk-forward backtest — new odds-free feature set.

Method:
  - Features (Elo, form, H2H, shots) are pre-computed with no look-ahead.
  - For each season, retrain the model on all prior seasons only.
  - Edge is found by comparing model probabilities against B365 opening odds.
  - Bets are settled at average market odds (AvgH/D/A).
  - Pinnacle (PS) is shown as a benchmark — beating Pinnacle = real edge.

Usage:
  python football/scripts/backtest.py
  python football/scripts/backtest.py --league laliga
  python football/scripts/backtest.py --min-edge 0.03
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
from football.scripts.model import FEATURES
from football.scripts.preprocess import process

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
BANKROLL      = 500.0
KELLY_FRAC    = 0.25


def load(league: str) -> pd.DataFrame:
    return pd.read_csv(
        PROCESSED_DIR / f"{league}_processed.csv",
        parse_dates=["Date"]
    ).sort_values("Date").reset_index(drop=True)


def fit_model(train_df: pd.DataFrame):
    df = train_df.dropna(subset=FEATURES + ["result"])
    X  = df[FEATURES].values
    y  = df["result"].map({1: 0, 0: 1, -1: 2}).values

    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=500)
    model.fit(scaler.fit_transform(X), y)
    return model, scaler


def predict_row(model, scaler, row: pd.Series) -> dict:
    X = np.array([[row[f] for f in FEATURES]])
    p = model.predict_proba(scaler.transform(X))[0]
    return {"home": p[0], "draw": p[1], "away": p[2]}


def simulate(test_df: pd.DataFrame, model, scaler, min_edge: float) -> list[dict]:
    records = []

    for _, row in test_df.iterrows():
        if any(pd.isna(row.get(f)) for f in FEATURES):
            continue
        if pd.isna(row.get("B365H")) or pd.isna(row.get("AvgH")):
            continue

        probs = predict_row(model, scaler, row)
        actual = {0: "home", 1: "draw", 2: "away"}[int(
            {"H": 0, "D": 1, "A": 2}[row["FTR"]]
        )]

        for outcome, b365_col, avg_col, ps_col in [
            ("home", "B365H", "AvgH", "PSH"),
            ("draw", "B365D", "AvgD", "PSD"),
            ("away", "B365A", "AvgA", "PSA"),
        ]:
            model_prob = probs[outcome]
            b365_odds  = row[b365_col]
            avg_odds   = row[avg_col]
            ps_odds    = row.get(ps_col, np.nan)

            if pd.isna(b365_odds):
                continue

            edge = model_prob - implied_probability(b365_odds)
            if edge < min_edge:
                continue

            k     = kelly_stake(model_prob, b365_odds, KELLY_FRAC)
            stake = round(BANKROLL * k, 2)
            if stake <= 0:
                continue

            won  = outcome == actual
            pnl  = round(stake * (avg_odds - 1) if won else -stake, 2)

            # Did we beat Pinnacle? (proxy for genuine market edge)
            ps_edge = (model_prob - implied_probability(ps_odds)
                       if not pd.isna(ps_odds) else None)

            records.append({
                "date":       row["Date"],
                "season":     row["season"],
                "home":       row["HomeTeam"],
                "away":       row["AwayTeam"],
                "outcome":    outcome,
                "model_prob": round(model_prob, 4),
                "b365_odds":  b365_odds,
                "avg_odds":   avg_odds,
                "ps_odds":    ps_odds,
                "edge":       round(edge, 4),
                "ps_edge":    round(ps_edge, 4) if ps_edge is not None else None,
                "stake":      stake,
                "won":        won,
                "pnl":        pnl,
                "home_elo":   row.get("home_elo"),
                "away_elo":   row.get("away_elo"),
            })

    return records


def run(league: str, min_edge: float) -> pd.DataFrame:
    df      = load(league)
    seasons = sorted(df["season"].unique())

    if len(seasons) < 2:
        print(f"Need >= 2 seasons. Found: {seasons}")
        return pd.DataFrame()

    all_bets = []
    for i in range(1, len(seasons)):
        train_seasons = seasons[:i]
        test_season   = seasons[i]

        train_df = df[df["season"].isin(train_seasons)]
        test_df  = df[df["season"] == test_season].copy()

        model, scaler = fit_model(train_df)
        bets = simulate(test_df, model, scaler, min_edge)
        all_bets.extend(bets)

        print(f"  {train_seasons} -> {test_season} | "
              f"{len(test_df)} matches | {len(bets)} bets")

    return pd.DataFrame(all_bets)


def summarise(bets: pd.DataFrame, league: str):
    if bets.empty:
        print("No bets placed.")
        return

    staked   = bets["stake"].sum()
    pnl      = bets["pnl"].sum()
    roi      = pnl / staked * 100
    win_rate = bets["won"].mean() * 100
    n        = len(bets)

    # Pinnacle edge (genuine market signal)
    ps_bets = bets.dropna(subset=["ps_edge"])
    ps_pos  = (ps_bets["ps_edge"] > 0).mean() * 100 if len(ps_bets) else 0

    print(f"\n{'='*58}")
    print(f"  {league.upper()} BACKTEST  (min edge {MIN_EDGE:.0%})")
    print(f"{'='*58}")
    print(f"  Bets          : {n}")
    print(f"  Total staked  : {staked:,.2f}")
    print(f"  Total P&L     : {pnl:+,.2f}")
    print(f"  ROI           : {roi:+.2f}%")
    print(f"  Win rate      : {win_rate:.1f}%")
    print(f"  Avg model edge: {bets['edge'].mean():+.2%}")
    print(f"  Beat Pinnacle : {ps_pos:.0f}% of bets  "
          f"({'good signal' if ps_pos > 50 else 'noise'})")

    print(f"\n  --- By season ---")
    for season, g in bets.groupby("season"):
        s = g["pnl"].sum()
        r = s / g["stake"].sum() * 100
        print(f"  {season}: {len(g):3d} bets | "
              f"staked {g['stake'].sum():7.2f} | P&L {s:+7.2f} | ROI {r:+.1f}%")

    print(f"\n  --- By selection type ---")
    for outcome, g in bets.groupby("outcome"):
        s = g["pnl"].sum()
        r = s / g["stake"].sum() * 100
        print(f"  {outcome:5s}: {len(g):3d} bets | P&L {s:+7.2f} | ROI {r:+.1f}%")

    cum = bets.sort_values("date")["pnl"].cumsum()
    print(f"\n  --- P&L curve ---")
    print(f"  Peak          : {cum.max():+.2f}")
    print(f"  Max drawdown  : {(cum - cum.cummax()).min():+.2f}")
    print(f"  Final         : {cum.iloc[-1]:+.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--league",   choices=["laliga", "bundesliga", "both"], default="both")
    parser.add_argument("--min-edge", type=float, default=0.02)
    args = parser.parse_args()

    MIN_EDGE = args.min_edge
    leagues  = ["laliga", "bundesliga"] if args.league == "both" else [args.league]

    for league in leagues:
        print(f"\nWalk-forward backtest: {league.upper()}")
        bets = run(league, MIN_EDGE)
        summarise(bets, league)
