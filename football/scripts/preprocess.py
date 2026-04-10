"""
Clean and engineer features from raw football match data.
Outputs a processed dataset ready for model training.
"""

import pandas as pd
import numpy as np
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_raw(league: str) -> pd.DataFrame:
    path = RAW_DIR / f"{league}_combined.csv"
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.sort_values("Date").reset_index(drop=True)
    return df


def add_result(df: pd.DataFrame) -> pd.DataFrame:
    """Add numeric result column: H=1, D=0, A=-1"""
    mapping = {"H": 1, "D": 0, "A": -1}
    df["result"] = df["FTR"].map(mapping)
    return df


def rolling_form(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Add rolling average goals scored/conceded per team over last N games.
    Calculated separately for home and away perspectives.
    """
    df = df.copy()

    for team_col, goals_for_col, goals_against_col, prefix in [
        ("HomeTeam", "FTHG", "FTAG", "home"),
        ("AwayTeam", "FTAG", "FTHG", "away"),
    ]:
        scored = df.groupby(team_col)[goals_for_col].transform(
            lambda x: x.shift(1).rolling(window, min_periods=1).mean()
        )
        conceded = df.groupby(team_col)[goals_against_col].transform(
            lambda x: x.shift(1).rolling(window, min_periods=1).mean()
        )
        df[f"{prefix}_avg_scored"] = scored
        df[f"{prefix}_avg_conceded"] = conceded

    return df


def add_odds_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert bookmaker odds to implied probabilities.
    Uses B365 odds if available (Bet365 — common in football-data.co.uk).
    """
    for col, new_col in [("B365H", "impl_home"), ("B365D", "impl_draw"), ("B365A", "impl_away")]:
        if col in df.columns:
            df[new_col] = 1 / df[col]
    return df


def process(league: str) -> pd.DataFrame:
    df = load_raw(league)
    df = add_result(df)
    df = rolling_form(df)
    df = add_odds_features(df)
    df = df.dropna(subset=["result", "home_avg_scored", "away_avg_scored"])

    out = PROCESSED_DIR / f"{league}_processed.csv"
    df.to_csv(out, index=False)
    print(f"Processed {len(df)} rows → {out}")
    return df


if __name__ == "__main__":
    for league in ["laliga", "bundesliga"]:
        process(league)
