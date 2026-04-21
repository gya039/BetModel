"""
Fetch NBA data from stats.nba.com via nba_api.

Pulls per season:
  - Team game logs  (results, points, home/away, date)
  - Player box scores (pts, reb, ast, stl, blk, to, min, fg%, 3p%, usg%)
  - Team advanced stats (pace, off rating, def rating)

Rate-limits requests to avoid being blocked (0.6s between calls).

Usage:
    python fetch_data.py              # pulls all seasons
    python fetch_data.py --season 2024-25
"""

import argparse
import time
from pathlib import Path

import pandas as pd
from nba_api.stats.endpoints import (
    leaguegamelog,
    playergamelogs,
    leaguedashteamstats,
)

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

SEASONS = ["2022-23", "2023-24", "2024-25"]
DELAY   = 0.8   # seconds between API calls — stay polite


def fetch_team_game_logs(season: str) -> pd.DataFrame:
    """All regular season games — one row per team per game."""
    print(f"  Fetching team game logs: {season}...")
    time.sleep(DELAY)
    gl = leaguegamelog.LeagueGameLog(
        season=season,
        season_type_all_star="Regular Season",
        league_id="00",
    )
    df = gl.get_data_frames()[0]
    df["SEASON"] = season
    print(f"    {len(df)} team-game rows")
    return df


def fetch_player_game_logs(season: str) -> pd.DataFrame:
    """Full player box scores — one row per player per game."""
    print(f"  Fetching player game logs: {season}...")
    time.sleep(DELAY)
    pl = playergamelogs.PlayerGameLogs(
        season_nullable=season,
        season_type_nullable="Regular Season",
    )
    df = pl.get_data_frames()[0]
    df["SEASON"] = season
    print(f"    {len(df)} player-game rows")
    return df


def fetch_team_advanced(season: str) -> pd.DataFrame:
    """Season-level advanced stats: pace, off/def rating, etc."""
    print(f"  Fetching team advanced stats: {season}...")
    time.sleep(DELAY)
    adv = leaguedashteamstats.LeagueDashTeamStats(
        season=season,
        season_type_all_star="Regular Season",
        measure_type_detailed_defense="Advanced",
        per_mode_simple="PerGame",
    )
    df = adv.get_data_frames()[0]
    df["SEASON"] = season
    print(f"    {len(df)} teams")
    return df


def fetch_and_save(seasons: list[str]):
    team_logs, player_logs, team_adv = [], [], []

    for season in seasons:
        print(f"\n--- {season} ---")
        try:
            team_logs.append(fetch_team_game_logs(season))
        except Exception as e:
            print(f"  team game logs failed: {e}")

        try:
            player_logs.append(fetch_player_game_logs(season))
        except Exception as e:
            print(f"  player game logs failed: {e}")

        try:
            team_adv.append(fetch_team_advanced(season))
        except Exception as e:
            print(f"  team advanced failed: {e}")

    if team_logs:
        out = pd.concat(team_logs, ignore_index=True)
        path = RAW_DIR / "team_game_logs.csv"
        out.to_csv(path, index=False)
        print(f"\nSaved {len(out)} team-game rows -> {path}")

    if player_logs:
        out = pd.concat(player_logs, ignore_index=True)
        path = RAW_DIR / "player_game_logs.csv"
        out.to_csv(path, index=False)
        print(f"Saved {len(out)} player-game rows -> {path}")

    if team_adv:
        out = pd.concat(team_adv, ignore_index=True)
        path = RAW_DIR / "team_advanced.csv"
        out.to_csv(path, index=False)
        print(f"Saved {len(out)} team-season rows -> {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--season", help="Single season e.g. 2024-25. Omit for all.")
    args = parser.parse_args()

    seasons = [args.season] if args.season else SEASONS
    print(f"Fetching {len(seasons)} season(s) from stats.nba.com...")
    fetch_and_save(seasons)
    print("\nDone.")
