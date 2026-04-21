"""
MLB feature builder.

For each game (sorted by date), computes rolling team stats from PRIOR games only
(strict no look-ahead). Merges season pitcher stats.

Features per game:
  Team (rolling, no look-ahead):
    HOME/AWAY_L10_WIN_PCT   — win% in last 10 games
    HOME/AWAY_L5_WIN_PCT    — win% in last 5 games
    HOME/AWAY_L10_RD        — avg run differential last 10 games
    HOME/AWAY_L5_RD         — avg run differential last 5 games
    HOME/AWAY_L10_RUNS_FOR  — avg runs scored last 10
    HOME/AWAY_L10_RUNS_AGN  — avg runs allowed last 10

  Pitcher (full-season stats — slight future leak in early games, noted):
    HOME/AWAY_SP_ERA
    HOME/AWAY_SP_WHIP
    HOME/AWAY_SP_K9

  Derived:
    WIN_PCT_DIFF    — home L10 win% minus away L10 win%
    RD_DIFF         — home L10 RD minus away L10 RD
    ERA_DIFF        — home ERA minus away ERA (lower = home advantage)
    WHIP_DIFF

Output: mlb/data/processed/games_processed.csv

Usage:
    python mlb/scripts/preprocess.py
"""

from collections import defaultdict, deque
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))
from mlb.scripts.feature_utils import (
    add_ballpark,
    add_derived_diffs,
    aggregate_bullpen_from_pitchers,
    bullpen_features,
    load_optional_bullpen_csv,
    pitcher_features,
)

RAW_DIR  = Path(__file__).parent.parent / "data" / "raw"
PROC_DIR = Path(__file__).parent.parent / "data" / "processed"
PROC_DIR.mkdir(parents=True, exist_ok=True)

FILL_ERA  = 4.50   # league-average fallback for missing pitcher stats
FILL_WHIP = 1.30
FILL_K9   = 8.5

# Park factors (runs-based, 1.00 = league average, sourced from 2023-2025 averages)
BALLPARK_FACTORS = {
    "COL": 1.13,  # Coors Field — extreme altitude
    "BOS": 1.06,  # Fenway Park
    "CIN": 1.05,  # Great American Ball Park
    "PHI": 1.04,  # Citizens Bank Park
    "TEX": 1.03,  # Globe Life Field
    "CHC": 1.02,  # Wrigley Field
    "MIL": 1.02,  # American Family Field
    "DET": 1.01,  # Comerica Park
    "NYY": 1.01,  # Yankee Stadium
    "AZ":  1.00,  # Chase Field
    "BAL": 1.00,  # Camden Yards
    "HOU": 1.00,  # Minute Maid Park
    "LAD": 0.99,  # Dodger Stadium
    "ATL": 0.99,  # Truist Park
    "NYM": 0.99,  # Citi Field
    "MIN": 0.98,  # Target Field
    "CLE": 0.98,  # Progressive Field
    "STL": 0.97,  # Busch Stadium
    "PIT": 0.97,  # PNC Park
    "KC":  0.97,  # Kauffman Stadium
    "MIA": 0.97,  # loanDepot Park
    "SF":  0.97,  # Oracle Park
    "SD":  0.97,  # Petco Park
    "SEA": 0.97,  # T-Mobile Park
    "WSH": 0.96,  # Nationals Park
    "TOR": 0.96,  # Rogers Centre
    "LAA": 0.96,  # Angel Stadium
    "TB":  0.95,  # Tropicana Field
    "CWS": 0.95,  # Guaranteed Rate Field
    "ATH": 0.95,  # Sutter Health Park (Sacramento)
}


def rolling_stats(games: pd.DataFrame) -> pd.DataFrame:
    """
    For each game, compute rolling features from PRIOR games for both teams.
    Uses a per-team deque of recent results (run differential, win/loss).
    """

    # Build chronological list of results per team
    # Key: team_id -> deque of (run_diff, runs_for, runs_against, win)
    team_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=20))

    rows = []
    for _, g in games.sort_values("game_date").iterrows():
        ht = int(g["home_team_id"])
        at = int(g["away_team_id"])

        def stats(team_id: int, n: int) -> dict:
            hist = list(team_history[team_id])
            recent = hist[-n:]
            if len(recent) < 2:
                return {
                    f"L{n}_WIN_PCT":  np.nan,
                    f"L{n}_RD":       np.nan,
                    f"L{n}_RUNS_FOR": np.nan,
                    f"L{n}_RUNS_AGN": np.nan,
                }
            wins  = [r[3] for r in recent]
            rds   = [r[0] for r in recent]
            rf    = [r[1] for r in recent]
            ra    = [r[2] for r in recent]
            return {
                f"L{n}_WIN_PCT":  round(np.mean(wins), 4),
                f"L{n}_RD":       round(np.mean(rds),  4),
                f"L{n}_RUNS_FOR": round(np.mean(rf),   4),
                f"L{n}_RUNS_AGN": round(np.mean(ra),   4),
            }

        home_l10 = stats(ht, 10)
        home_l5  = stats(ht, 5)
        home_l20 = stats(ht, 20)
        away_l10 = stats(at, 10)
        away_l5  = stats(at, 5)
        away_l20 = stats(at, 20)

        row = {
            "game_pk":      g["game_pk"],
            "game_date":    g["game_date"],
            "home_team":    g["home_team"],
            "away_team":    g["away_team"],
            "home_team_id": ht,
            "away_team_id": at,
            "home_score":   g["home_score"],
            "away_score":   g["away_score"],
            "home_win":     int(g["home_win"]),
            "point_diff":   g["home_score"] - g["away_score"],
            "home_sp_id":   g.get("home_sp_id"),
            "away_sp_id":   g.get("away_sp_id"),
            # Home rolling
            "HOME_L10_WIN_PCT":  home_l10["L10_WIN_PCT"],
            "HOME_L5_WIN_PCT":   home_l5["L5_WIN_PCT"],
            "HOME_L20_WIN_PCT":  home_l20["L20_WIN_PCT"],
            "HOME_L10_RD":       home_l10["L10_RD"],
            "HOME_L5_RD":        home_l5["L5_RD"],
            "HOME_L20_RD":       home_l20["L20_RD"],
            "HOME_L10_RUNS_FOR": home_l10["L10_RUNS_FOR"],
            "HOME_L10_RUNS_AGN": home_l10["L10_RUNS_AGN"],
            "HOME_L20_RUNS_FOR": home_l20["L20_RUNS_FOR"],
            "HOME_L20_RUNS_AGN": home_l20["L20_RUNS_AGN"],
            # Away rolling
            "AWAY_L10_WIN_PCT":  away_l10["L10_WIN_PCT"],
            "AWAY_L5_WIN_PCT":   away_l5["L5_WIN_PCT"],
            "AWAY_L20_WIN_PCT":  away_l20["L20_WIN_PCT"],
            "AWAY_L10_RD":       away_l10["L10_RD"],
            "AWAY_L5_RD":        away_l5["L5_RD"],
            "AWAY_L20_RD":       away_l20["L20_RD"],
            "AWAY_L10_RUNS_FOR": away_l10["L10_RUNS_FOR"],
            "AWAY_L10_RUNS_AGN": away_l10["L10_RUNS_AGN"],
            "AWAY_L20_RUNS_FOR": away_l20["L20_RUNS_FOR"],
            "AWAY_L20_RUNS_AGN": away_l20["L20_RUNS_AGN"],
        }
        rows.append(row)

        # Update history AFTER building features (no look-ahead)
        home_rd = g["home_score"] - g["away_score"]
        away_rd = g["away_score"] - g["home_score"]
        team_history[ht].append((home_rd, g["home_score"], g["away_score"], int(g["home_win"])))
        team_history[at].append((away_rd, g["away_score"], g["home_score"], int(not g["home_win"])))

    return pd.DataFrame(rows)


def merge_pitchers(df: pd.DataFrame, pitchers: pd.DataFrame) -> pd.DataFrame:
    """
    Join stabilized starter features onto each game by starter ID.
    Low-IP starters are blended toward league average inside pitcher_features().
    """
    for side, col in (("HOME", "home_sp_id"), ("AWAY", "away_sp_id")):
        feature_rows = df[col].apply(lambda pid: pitcher_features(pitchers, pid))
        if feature_rows.empty:
            continue
        for feature_name in feature_rows.iloc[0].keys():
            df[f"{side}_{feature_name}"] = feature_rows.apply(lambda values: values[feature_name])
    return df


def merge_bullpens(df: pd.DataFrame, bullpens: pd.DataFrame | None) -> pd.DataFrame:
    """Join bullpen quality and fatigue features, using neutral fallbacks if absent."""
    for idx, row in df.iterrows():
        home_bp = bullpen_features(bullpens, row["home_team"])
        away_bp = bullpen_features(bullpens, row["away_team"])
        for key, value in home_bp.items():
            df.at[idx, f"HOME_{key}"] = value
        for key, value in away_bp.items():
            df.at[idx, f"AWAY_{key}"] = value
    return df


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    return add_derived_diffs(df)


def merge_ballpark(df: pd.DataFrame) -> pd.DataFrame:
    return add_ballpark(df)


if __name__ == "__main__":
    print("\n=== MLB 2025 Preprocessing ===\n")

    games    = pd.read_csv(RAW_DIR / "games_2025.csv", parse_dates=["game_date"])
    pitchers = pd.read_csv(RAW_DIR / "pitchers_2025.csv")
    bullpens = load_optional_bullpen_csv(RAW_DIR, 2025)
    if bullpens is None:
        bullpens = aggregate_bullpen_from_pitchers(pitchers)
        if bullpens.empty:
            print("  Bullpen file not found; using neutral bullpen features")

    print(f"  Loaded {len(games)} games, {len(pitchers)} pitchers")

    df = rolling_stats(games)
    df = merge_pitchers(df, pitchers)
    df = merge_bullpens(df, bullpens)
    df = add_derived(df)
    df = merge_ballpark(df)

    # Drop warm-up rows (first ~10 games per team means many NaNs early on)
    before = len(df)
    df = df.dropna(subset=[
        "HOME_L10_WIN_PCT", "AWAY_L10_WIN_PCT",
        "HOME_L10_RD",      "AWAY_L10_RD",
        "HOME_L20_WIN_PCT", "AWAY_L20_WIN_PCT",
        "HOME_L20_RD",      "AWAY_L20_RD",
    ])
    print(f"  After dropping NaN warm-up rows: {len(df)} / {before} games")

    out = PROC_DIR / "games_processed.csv"
    df.to_csv(out, index=False)
    print(f"  Saved -> {out}\n")

    print(f"  Date range : {df['game_date'].min()} -> {df['game_date'].max()}")
    print(f"  Home win % : {df['home_win'].mean():.1%}")
    print(f"  Avg home RD: {df['point_diff'].mean():+.2f}")
    print(f"  Teams      : {df['home_team'].nunique()} home teams seen")
    print("\nDone.")
