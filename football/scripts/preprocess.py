"""
Feature engineering pipeline — no bookmaker odds in the output.

Features computed per match (using only data available before that match):
  - Elo ratings (updated after every result, home advantage baked in)
  - Last 5 / last 10 overall form: PPG, goals for/against, goal diff
  - Last 5 home-specific form (for home team)
  - Last 5 away-specific form (for away team)
  - H2H last 5 meetings: home win rate, draw rate, avg goals
  - Shots on target ratio (xG proxy, last 5 games)

After processing, saves:
  - {league}_processed.csv       — match rows with features + result
  - {league}_team_state.json     — current Elo + recent form per team (for live prediction)
  - {league}_h2h_state.json      — H2H history per pair (for live prediction)
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

ELO_K = 20
ELO_HOME_ADV = 100
ELO_DEFAULT = 1500.0


# ---------------------------------------------------------------------------
# Elo helpers
# ---------------------------------------------------------------------------

def elo_expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


def elo_update(rating: float, expected: float, actual: float) -> float:
    return rating + ELO_K * (actual - expected)


# ---------------------------------------------------------------------------
# Rolling stat helpers
# ---------------------------------------------------------------------------

def rolling_stats(games: list, n: int) -> dict:
    """PPG, avg GF, avg GA, avg GD from last N games. Defaults if empty."""
    recent = games[-n:] if games else []
    if not recent:
        return {"ppg": 1.0, "gf": 1.2, "ga": 1.2, "gd": 0.0}
    ng = len(recent)
    pts = sum(g["pts"] for g in recent)
    gf  = sum(g["gf"]  for g in recent)
    ga  = sum(g["ga"]  for g in recent)
    return {
        "ppg": pts / ng,
        "gf":  gf  / ng,
        "ga":  ga  / ng,
        "gd":  (gf - ga) / ng,
    }


def sot_ratio(games: list, n: int = 5) -> float:
    """Shots on target for / (for + against) over last N games."""
    recent = games[-n:] if games else []
    if not recent:
        return 0.5
    for_ = sum(g.get("sot_f", 0) for g in recent)
    aga  = sum(g.get("sot_a", 0) for g in recent)
    total = for_ + aga
    return for_ / total if total > 0 else 0.5


def h2h_stats(h2h_games: list, home_team: str, n: int = 5) -> dict:
    """H2H stats from the perspective of home_team."""
    recent = h2h_games[-n:] if h2h_games else []
    if not recent:
        return {"hw_rate": 0.45, "draw_rate": 0.25, "avg_goals": 2.5}
    ng = len(recent)
    hw    = sum(1 for g in recent if g["winner"] == home_team)
    draws = sum(1 for g in recent if g["winner"] == "draw")
    goals = sum(g["goals"] for g in recent)
    return {
        "hw_rate":   hw / ng,
        "draw_rate": draws / ng,
        "avg_goals": goals / ng,
    }


# ---------------------------------------------------------------------------
# Main feature computation
# ---------------------------------------------------------------------------

def compute_features(df: pd.DataFrame):
    """
    Iterate through matches in date order, building features from state
    accumulated up to (but not including) each match. Returns:
      - feature DataFrame (aligned with df)
      - elo dict (final state per team)
      - games dict (full game history per team)
      - h2h dict (full H2H history per pair)
    """
    df = df.sort_values("Date").reset_index(drop=True)

    elo   = {}   # team -> float
    games = {}   # team -> list of game dicts
    h2h   = {}   # (t1,t2) sorted tuple -> list of h2h dicts

    rows = []

    for _, row in df.iterrows():
        home = row["HomeTeam"]
        away = row["AwayTeam"]

        h_elo = elo.get(home, ELO_DEFAULT)
        a_elo = elo.get(away, ELO_DEFAULT)

        hg = games.get(home, [])
        ag = games.get(away, [])
        hg_home = [g for g in hg if g["home"]]
        ag_away = [g for g in ag if not g["home"]]

        h2h_key  = tuple(sorted([home, away]))
        h2h_list = h2h.get(h2h_key, [])

        h5  = rolling_stats(hg,      5)
        h10 = rolling_stats(hg,      10)
        a5  = rolling_stats(ag,      5)
        a10 = rolling_stats(ag,      10)
        hh5 = rolling_stats(hg_home, 5)
        aa5 = rolling_stats(ag_away, 5)
        h2  = h2h_stats(h2h_list, home, 5)

        rows.append({
            # Elo
            "home_elo":        h_elo,
            "away_elo":        a_elo,
            "elo_diff":        h_elo - a_elo,
            # Last 5 overall
            "home_l5_ppg":     h5["ppg"],
            "home_l5_gf":      h5["gf"],
            "home_l5_ga":      h5["ga"],
            "home_l5_gd":      h5["gd"],
            "away_l5_ppg":     a5["ppg"],
            "away_l5_gf":      a5["gf"],
            "away_l5_ga":      a5["ga"],
            "away_l5_gd":      a5["gd"],
            # Last 10 overall
            "home_l10_ppg":    h10["ppg"],
            "home_l10_gf":     h10["gf"],
            "home_l10_ga":     h10["ga"],
            "away_l10_ppg":    a10["ppg"],
            "away_l10_gf":     a10["gf"],
            "away_l10_ga":     a10["ga"],
            # Venue-specific last 5
            "home_h5_ppg":     hh5["ppg"],
            "home_h5_gf":      hh5["gf"],
            "home_h5_ga":      hh5["ga"],
            "away_a5_ppg":     aa5["ppg"],
            "away_a5_gf":      aa5["gf"],
            "away_a5_ga":      aa5["ga"],
            # H2H
            "h2h_hw_rate":     h2["hw_rate"],
            "h2h_draw_rate":   h2["draw_rate"],
            "h2h_avg_goals":   h2["avg_goals"],
            # Shots on target ratio
            "home_sot":        sot_ratio(hg, 5),
            "away_sot":        sot_ratio(ag, 5),
        })

        # --- update state after this match ---
        fthg = int(row["FTHG"])
        ftag = int(row["FTAG"])
        ftr  = row["FTR"]
        hst  = int(row["HST"]) if pd.notna(row.get("HST")) else 0
        ast_ = int(row["AST"]) if pd.notna(row.get("AST")) else 0

        if ftr == "H":
            h_pts, a_pts = 3, 0
            h_score, a_score = 1.0, 0.0
            winner = home
        elif ftr == "D":
            h_pts, a_pts = 1, 1
            h_score, a_score = 0.5, 0.5
            winner = "draw"
        else:
            h_pts, a_pts = 0, 3
            h_score, a_score = 0.0, 1.0
            winner = away

        # Elo update (home gets +100 adj for expected calc)
        e_home = elo_expected(h_elo + ELO_HOME_ADV, a_elo)
        e_away = 1.0 - e_home
        elo[home] = elo_update(h_elo, e_home, h_score)
        elo[away] = elo_update(a_elo, e_away, a_score)

        # Games history
        games.setdefault(home, []).append(
            {"pts": h_pts, "gf": fthg, "ga": ftag, "home": True,  "sot_f": hst,  "sot_a": ast_}
        )
        games.setdefault(away, []).append(
            {"pts": a_pts, "gf": ftag, "ga": fthg, "home": False, "sot_f": ast_, "sot_a": hst}
        )

        # H2H history
        h2h.setdefault(h2h_key, []).append({"winner": winner, "goals": fthg + ftag})

    return pd.DataFrame(rows, index=df.index), elo, games, h2h


# ---------------------------------------------------------------------------
# State serialisation (for live prediction in find_value.py)
# ---------------------------------------------------------------------------

def build_team_state(elo: dict, games: dict) -> dict:
    """Snapshot current form stats for every team."""
    state = {}
    for team, g in games.items():
        g_home = [x for x in g if x["home"]]
        g_away = [x for x in g if not x["home"]]
        state[team] = {
            "elo":       round(elo.get(team, ELO_DEFAULT), 2),
            "l5":        rolling_stats(g,      5),
            "l10":       rolling_stats(g,      10),
            "home_l5":   rolling_stats(g_home, 5),
            "away_l5":   rolling_stats(g_away, 5),
            "sot":       round(sot_ratio(g, 5), 4),
        }
    return state


def build_h2h_state(h2h: dict) -> dict:
    """Serialise H2H history (last 5 per pair)."""
    return {
        f"{k[0]}|{k[1]}": v[-5:]
        for k, v in h2h.items()
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def process(league: str):
    path = RAW_DIR / f"{league}_combined.csv"
    df = pd.read_csv(path)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Map result
    df["result"] = df["FTR"].map({"H": 1, "D": 0, "A": -1})

    feat_df, elo, games, h2h = compute_features(df)

    # Keep odds columns for backtesting (NOT used as model features)
    odds_cols = [c for c in ["B365H", "B365D", "B365A",
                              "PSH",   "PSD",   "PSA",
                              "WHH",   "WHD",   "WHA",
                              "AvgH",  "AvgD",  "AvgA"] if c in df.columns]

    # Merge features onto original df
    base_cols = ["Date", "season", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR", "result"]
    out = pd.concat([df[base_cols + odds_cols].reset_index(drop=True),
                     feat_df.reset_index(drop=True)], axis=1)
    out = out.dropna(subset=["result"])

    # Save processed CSV
    csv_path = PROCESSED_DIR / f"{league}_processed.csv"
    out.to_csv(csv_path, index=False)
    print(f"{league}: {len(out)} rows -> {csv_path}")

    # Save team state (current form snapshot for live prediction)
    team_state = build_team_state(elo, games)
    ts_path = PROCESSED_DIR / f"{league}_team_state.json"
    with open(ts_path, "w") as f:
        json.dump(team_state, f, indent=2)
    print(f"{league}: team state saved -> {ts_path}  ({len(team_state)} teams)")

    # Save H2H state
    h2h_state = build_h2h_state(h2h)
    h2h_path = PROCESSED_DIR / f"{league}_h2h_state.json"
    with open(h2h_path, "w") as f:
        json.dump(h2h_state, f, indent=2)
    print(f"{league}: H2H state saved  -> {h2h_path}  ({len(h2h_state)} pairs)")

    return out, elo, games, h2h


if __name__ == "__main__":
    for league in ["laliga", "bundesliga"]:
        process(league)
