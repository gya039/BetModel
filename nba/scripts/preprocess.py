"""
Feature engineering for NBA models.

Game-level features (for spread/moneyline model):
  - Rolling 10-game offensive rating, defensive rating, pace, net rating
  - Rest days (home & away)
  - Back-to-back flag (home & away)
  - Last 10 win %
  - Home/away record last 10

Player-level features (for prop model):
  - Rolling 5 / 10 game averages: PTS, REB, AST, STL, BLK, 3PM, MIN
  - Rolling usage rate, true shooting %
  - Opponent defensive rating (last 10)
  - Rest days, back-to-back flag
  - Home/away flag

Saves:
  - games_processed.csv     (one row per game matchup)
  - players_processed.csv   (one row per player per game, with matchup features)
  - team_state.json         (current rolling stats per team — for live prediction)
  - player_state.json       (current rolling stats per player — for live prediction)
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path

RAW_DIR       = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def rolling_avg(series: list, n: int, default: float) -> float:
    recent = series[-n:] if series else []
    return float(np.mean(recent)) if recent else default


def rest_days(dates: list) -> int:
    """Days since last game. Returns 7 if no prior game (start of season)."""
    if len(dates) < 2:
        return 7
    delta = (dates[-1] - dates[-2]).days
    return min(delta, 7)


def is_b2b(dates: list) -> int:
    """1 if this game is on back-to-back days."""
    if len(dates) < 2:
        return 0
    return 1 if (dates[-1] - dates[-2]).days == 1 else 0


# ---------------------------------------------------------------------------
# Team game log processing
# ---------------------------------------------------------------------------

def process_team_logs(df: pd.DataFrame) -> pd.DataFrame:
    """
    Input: raw LeagueGameLog (one row per team per game).
    Output: game-level DataFrame with rolling features for both teams.
    """
    df = df.copy()
    df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"])
    df = df.sort_values(["GAME_DATE", "GAME_ID"]).reset_index(drop=True)

    # Parse home/away from MATCHUP column: "BOS vs. MIA" = home, "BOS @ MIA" = away
    df["IS_HOME"] = df["MATCHUP"].str.contains(" vs\\.").astype(int)

    # Opponent abbrev
    df["OPP"] = df["MATCHUP"].str.extract(r"(?:vs\.|@)\s+(\w+)$")

    # Win flag
    df["WIN"] = (df["WL"] == "W").astype(int)

    # Rolling state per team
    team_state = {}   # team -> {"dates": [], "pts_f": [], "pts_a": [], "wins": [], ...}

    rows = []
    for _, row in df.iterrows():
        team = row["TEAM_ABBREVIATION"]
        s = team_state.setdefault(team, {
            "dates": [], "pts_f": [], "pts_a": [], "wins": [],
            "home_wins": [], "away_wins": [],
        })

        dates = s["dates"] + [row["GAME_DATE"]]

        feats = {
            "GAME_ID":        row["GAME_ID"],
            "GAME_DATE":      row["GAME_DATE"],
            "SEASON":         row["SEASON"],
            "TEAM":           team,
            "OPP":            row["OPP"],
            "IS_HOME":        row["IS_HOME"],
            "WIN":            row["WIN"],
            "PTS_FOR":        row["PTS"],
            "PTS_AGAINST":    row.get("PLUS_MINUS", 0) and row["PTS"] - row.get("PLUS_MINUS", 0),
            "REST_DAYS":      rest_days(dates),
            "IS_B2B":         is_b2b(dates),
            "L10_WIN_PCT":    rolling_avg(s["wins"],  10, 0.5),
            "L10_PTS_FOR":    rolling_avg(s["pts_f"], 10, 110.0),
            "L10_PTS_AGAINST":rolling_avg(s["pts_a"], 10, 110.0),
            "L10_NET":        rolling_avg(s["pts_f"], 10, 110.0) - rolling_avg(s["pts_a"], 10, 110.0),
            "L5_WIN_PCT":     rolling_avg(s["wins"],  5,  0.5),
            "L5_NET":         rolling_avg(s["pts_f"], 5, 110.0) - rolling_avg(s["pts_a"], 5, 110.0),
        }
        rows.append(feats)

        # Update state
        pts_against = row["PTS"] - row.get("PLUS_MINUS", 0)
        s["dates"].append(row["GAME_DATE"])
        s["pts_f"].append(row["PTS"])
        s["pts_a"].append(pts_against)
        s["wins"].append(row["WIN"])

    return pd.DataFrame(rows)


def build_game_features(team_df: pd.DataFrame) -> pd.DataFrame:
    """
    Join home and away team rows into one row per game.
    """
    home = team_df[team_df["IS_HOME"] == 1].copy()
    away = team_df[team_df["IS_HOME"] == 0].copy()

    home = home.rename(columns={c: f"HOME_{c}" for c in home.columns
                                  if c not in ["GAME_ID", "GAME_DATE", "SEASON"]})
    away = away.rename(columns={c: f"AWAY_{c}" for c in away.columns
                                  if c not in ["GAME_ID", "GAME_DATE", "SEASON"]})

    games = home.merge(away, on=["GAME_ID", "GAME_DATE", "SEASON"])

    # Point differential (home perspective) — target for spread model
    games["POINT_DIFF"] = games["HOME_PTS_FOR"] - games["AWAY_PTS_FOR"]
    games["HOME_WIN"]   = (games["POINT_DIFF"] > 0).astype(int)

    return games.sort_values("GAME_DATE").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Player game log processing
# ---------------------------------------------------------------------------

def process_player_logs(player_df: pd.DataFrame,
                         team_df: pd.DataFrame) -> pd.DataFrame:
    """
    Add rolling player form features and opponent defensive context.
    """
    player_df = player_df.copy()
    player_df["GAME_DATE"] = pd.to_datetime(player_df["GAME_DATE"])
    player_df = player_df.sort_values(["GAME_DATE", "PLAYER_ID"]).reset_index(drop=True)

    # Opponent defensive rating proxy: opponent's pts allowed per game (rolling 10)
    team_def = (
        team_df[["GAME_DATE", "TEAM", "L10_PTS_AGAINST"]]
        .rename(columns={"TEAM": "OPP_TEAM", "L10_PTS_AGAINST": "OPP_DEF_L10"})
    )

    # Parse home/away
    player_df["IS_HOME"] = player_df["MATCHUP"].str.contains(" vs\\.").astype(int)
    player_df["OPP_TEAM"] = player_df["MATCHUP"].str.extract(r"(?:vs\.|@)\s+(\w+)$")

    # Rolling state per player
    player_state = {}

    stat_cols = ["PTS", "REB", "AST", "STL", "BLK", "TOV",
                 "FG3M", "MIN", "FG_PCT", "FG3_PCT"]

    rows = []
    for _, row in player_df.iterrows():
        pid  = row["PLAYER_ID"]
        name = row["PLAYER_NAME"]
        s    = player_state.setdefault(pid, {c: [] for c in stat_cols})
        s.setdefault("dates", [])

        dates = s["dates"] + [row["GAME_DATE"]]

        feats = {
            "GAME_ID":     row["GAME_ID"],
            "GAME_DATE":   row["GAME_DATE"],
            "SEASON":      row["SEASON"],
            "PLAYER_ID":   pid,
            "PLAYER_NAME": name,
            "TEAM":        row["TEAM_ABBREVIATION"],
            "OPP_TEAM":    row["OPP_TEAM"],
            "IS_HOME":     row["IS_HOME"],
            "REST_DAYS":   rest_days(dates),
            "IS_B2B":      is_b2b(dates),
            # Actual stats (target variables)
            "ACT_PTS":     row["PTS"],
            "ACT_REB":     row["REB"],
            "ACT_AST":     row["AST"],
            "ACT_STL":     row["STL"],
            "ACT_BLK":     row["BLK"],
            "ACT_3PM":     row["FG3M"],
            "ACT_MIN":     row["MIN"],
        }

        # Rolling features
        for stat in stat_cols:
            val = row.get(stat, 0) or 0
            feats[f"L5_{stat}"]  = rolling_avg(s[stat], 5,  0.0)
            feats[f"L10_{stat}"] = rolling_avg(s[stat], 10, 0.0)
            s[stat].append(float(val))

        s["dates"].append(row["GAME_DATE"])
        rows.append(feats)

    out = pd.DataFrame(rows)

    # Merge opponent defensive context
    out = out.merge(
        team_def,
        left_on=["GAME_DATE", "OPP_TEAM"],
        right_on=["GAME_DATE", "OPP_TEAM"],
        how="left",
    )

    return out


# ---------------------------------------------------------------------------
# State snapshots for live prediction
# ---------------------------------------------------------------------------

def build_team_state(team_df: pd.DataFrame) -> dict:
    """Latest rolling stats per team."""
    latest = (
        team_df.sort_values("GAME_DATE")
               .groupby("TEAM")
               .last()
               .reset_index()
    )
    state = {}
    for _, row in latest.iterrows():
        state[row["TEAM"]] = {
            "l10_win_pct":    round(row["L10_WIN_PCT"], 4),
            "l10_pts_for":    round(row["L10_PTS_FOR"], 2),
            "l10_pts_against":round(row["L10_PTS_AGAINST"], 2),
            "l10_net":        round(row["L10_NET"], 2),
            "l5_win_pct":     round(row["L5_WIN_PCT"], 4),
            "l5_net":         round(row["L5_NET"], 2),
        }
    return state


def build_player_state(player_df: pd.DataFrame) -> dict:
    """Latest rolling stats per player."""
    stat_cols = ["L5_PTS", "L10_PTS", "L5_REB", "L10_REB",
                 "L5_AST", "L10_AST", "L5_FG3M", "L10_FG3M",
                 "L5_MIN", "L10_MIN", "L5_STL", "L5_BLK"]

    latest = (
        player_df.sort_values("GAME_DATE")
                 .groupby("PLAYER_ID")
                 .last()
                 .reset_index()
    )
    state = {}
    for _, row in latest.iterrows():
        state[str(row["PLAYER_ID"])] = {
            "name":    row["PLAYER_NAME"],
            "team":    row["TEAM"],
            **{c.lower(): round(float(row[c]), 3)
               for c in stat_cols if c in row.index},
        }
    return state


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def process():
    print("Loading raw data...")
    team_raw   = pd.read_csv(RAW_DIR / "team_game_logs.csv")
    player_raw = pd.read_csv(RAW_DIR / "player_game_logs.csv")

    print("Processing team logs...")
    team_df  = process_team_logs(team_raw)
    game_df  = build_game_features(team_df)

    path = PROCESSED_DIR / "games_processed.csv"
    game_df.to_csv(path, index=False)
    print(f"  {len(game_df)} games -> {path}")

    print("Processing player logs...")
    player_df = process_player_logs(player_raw, team_df)

    path = PROCESSED_DIR / "players_processed.csv"
    player_df.to_csv(path, index=False)
    print(f"  {len(player_df)} player-game rows -> {path}")

    # State snapshots
    team_state   = build_team_state(team_df)
    player_state = build_player_state(player_df)

    with open(PROCESSED_DIR / "team_state.json", "w") as f:
        json.dump(team_state, f, indent=2)
    print(f"  Team state: {len(team_state)} teams")

    with open(PROCESSED_DIR / "player_state.json", "w") as f:
        json.dump(player_state, f, indent=2)
    print(f"  Player state: {len(player_state)} players")


if __name__ == "__main__":
    process()
