"""
MLB data fetcher — 2025 regular season.

Pulls from the official MLB Stats API (free, no key required):
  - Full 2025 regular season schedule with final scores
  - Season pitching stats for all pitchers (ERA, WHIP, K/9)

Saves to:
  mlb/data/raw/games_2025.csv     — one row per game
  mlb/data/raw/pitchers_2025.csv  — one row per pitcher

Usage:
    python mlb/scripts/fetch_data.py
"""

import time
import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))
from mlb.scripts.feature_utils import aggregate_bullpen_from_pitchers, parse_ip, pitcher_row_from_stat, safe_float

MLB = "https://statsapi.mlb.com/api/v1"
RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

SEASON = 2025
# 2025 regular season: March 27 – September 28
SEASON_START = "2025-03-27"
SEASON_END   = "2025-09-28"

# Monthly chunks to avoid oversized responses
MONTH_RANGES = [
    ("2025-03-27", "2025-03-31"),
    ("2025-04-01", "2025-04-30"),
    ("2025-05-01", "2025-05-31"),
    ("2025-06-01", "2025-06-30"),
    ("2025-07-01", "2025-07-31"),
    ("2025-08-01", "2025-08-31"),
    ("2025-09-01", "2025-09-28"),
]


def get(url: str, params: dict = None) -> dict:
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


# ─── SCHEDULE ────────────────────────────────────────────────────────────────

def fetch_schedule_chunk(start: str, end: str) -> list[dict]:
    data = get(
        f"{MLB}/schedule",
        params={
            "sportId":   1,
            "startDate": start,
            "endDate":   end,
            "gameType":  "R",
            "hydrate":   "probablePitcher,team,linescore",
        },
    )
    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            status = g.get("status", {}).get("statusCode", "")
            # Only take final games (statusCode "F")
            if status != "F":
                continue

            away = g["teams"]["away"]
            home = g["teams"]["home"]

            # Scores — present in final games
            away_score = away.get("score")
            home_score = home.get("score")
            if away_score is None or home_score is None:
                ls = g.get("linescore", {}).get("teams", {})
                away_score = ls.get("away", {}).get("runs")
                home_score = ls.get("home", {}).get("runs")
            if away_score is None or home_score is None:
                continue

            away_sp = away.get("probablePitcher", {})
            home_sp = home.get("probablePitcher", {})

            games.append({
                "game_pk":       g["gamePk"],
                "game_date":     day["date"],
                "away_team_id":  away["team"]["id"],
                "away_team":     away["team"]["abbreviation"],
                "home_team_id":  home["team"]["id"],
                "home_team":     home["team"]["abbreviation"],
                "away_score":    int(away_score),
                "home_score":    int(home_score),
                "home_win":      int(home_score) > int(away_score),
                "away_sp_id":    away_sp.get("id"),
                "away_sp_name":  away_sp.get("fullName", "TBD"),
                "home_sp_id":    home_sp.get("id"),
                "home_sp_name":  home_sp.get("fullName", "TBD"),
            })
    return games


def fetch_all_games() -> pd.DataFrame:
    all_games = []
    for start, end in MONTH_RANGES:
        print(f"  Fetching schedule {start} to {end} ...", end=" ", flush=True)
        chunk = fetch_schedule_chunk(start, end)
        print(f"{len(chunk)} games")
        all_games.extend(chunk)
        time.sleep(0.3)

    df = pd.DataFrame(all_games).drop_duplicates("game_pk").sort_values("game_date")
    print(f"  Total: {len(df)} completed games")
    return df


# ─── PITCHER STATS ────────────────────────────────────────────────────────────

def fetch_pitcher_stats() -> pd.DataFrame:
    """Season pitching stats for all pitchers (full season totals)."""
    rows = []
    limit  = 500
    offset = 0

    while True:
        print(f"  Fetching pitcher stats offset={offset} ...", end=" ", flush=True)
        data = get(
            f"{MLB}/stats",
            params={
                "stats":      "season",
                "group":      "pitching",
                "season":     SEASON,
                "playerPool": "all",
                "limit":      limit,
                "offset":     offset,
                "hydrate":    "person",
            },
        )
        splits = data.get("stats", [{}])[0].get("splits", [])
        print(f"{len(splits)}")
        if not splits:
            break
        for s in splits:
            st = s.get("stat", {})
            person = s.get("player") or s.get("person") or {}
            team = s.get("team") or {}
            rows.append(pitcher_row_from_stat(st, person, team))
        if len(splits) < limit:
            break
        offset += limit
        time.sleep(0.3)

    df = pd.DataFrame(rows).dropna(subset=["pitcher_id"])
    df["pitcher_id"] = df["pitcher_id"].astype(int)
    df = df.sort_values("ip", ascending=False).drop_duplicates("pitcher_id")
    print(f"  Total: {len(df)} pitchers")
    return df


def _fetch_one_game_pitching_lines(g: dict) -> list[dict]:
    rows = []
    try:
        box = get(f"{MLB}/game/{int(g['game_pk'])}/boxscore")
    except Exception as exc:
        print(f"    boxscore failed for {g['game_pk']}: {exc}")
        return rows
    for side in ("home", "away"):
        team_abbr = g[f"{side}_team"]
        team_box = box.get("teams", {}).get(side, {})
        pitcher_ids = team_box.get("pitchers", [])
        players = team_box.get("players", {})
        for order_idx, pid in enumerate(pitcher_ids):
            pdata = players.get(f"ID{pid}", {})
            stat = pdata.get("stats", {}).get("pitching", {})
            person = pdata.get("person", {})
            rows.append({
                "game_pk": int(g["game_pk"]),
                "game_date": g["game_date"],
                "team": team_abbr,
                "opponent": g["away_team"] if side == "home" else g["home_team"],
                "pitcher_id": int(pid),
                "pitcher_name": person.get("fullName", "?"),
                "is_starter": 1 if order_idx == 0 else 0,
                "ip": parse_ip(stat.get("inningsPitched", 0)),
                "hits": safe_float(stat.get("hits"), 0.0),
                "er": safe_float(stat.get("earnedRuns"), 0.0),
                "k": safe_float(stat.get("strikeOuts"), 0.0),
                "walks": safe_float(stat.get("baseOnBalls"), 0.0),
                "home_runs": safe_float(stat.get("homeRuns"), 0.0),
                "batters_faced": safe_float(stat.get("battersFaced"), 0.0),
                "is_left": 1 if (pdata.get("person", {}).get("pitchHand") or {}).get("code") == "L" else 0,
            })
    return rows


def fetch_game_pitching_lines(games: pd.DataFrame, max_workers: int = 8) -> pd.DataFrame:
    """Fetch per-game pitching lines used to build leakage-safe pregame features."""
    rows = []
    records = games.sort_values("game_date").to_dict("records")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_one_game_pitching_lines, g) for g in records]
        for idx, future in enumerate(as_completed(futures), start=1):
            if idx == 1 or idx % 100 == 0:
                print(f"  Fetching boxscores {idx}/{len(records)} ...", flush=True)
            rows.extend(future.result())
    return pd.DataFrame(rows)


def fetch_team_pitching_proxy() -> pd.DataFrame:
    """Team pitching quality proxy used when player stat rows lack team abbreviations."""
    teams = get(f"{MLB}/teams", params={"sportId": 1, "season": SEASON}).get("teams", [])
    id_to_abbr = {t["id"]: t.get("abbreviation") for t in teams}
    data = get(
        f"{MLB}/teams/stats",
        params={"stats": "season", "group": "pitching", "season": SEASON, "sportIds": 1},
    )
    rows = []
    for split in data.get("stats", [{}])[0].get("splits", []):
        abbr = id_to_abbr.get(split.get("team", {}).get("id"))
        if not abbr:
            continue
        st = split.get("stat", {})
        rows.append({
            "team": abbr,
            "bp_era": float(st.get("era", 4.30)),
            "bp_whip": float(st.get("whip", 1.32)),
            "bp_k_bb": float(st.get("strikeoutWalkRatio", 2.45)),
            "bp_ip_last_3d": 0.0,
            "bp_ip_yesterday": 0.0,
            "bp_relievers_last_3d": 0.0,
            "bp_relievers_yesterday": 0.0,
            "bp_top_used_yesterday": 0.0,
        })
    return pd.DataFrame(rows)


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== MLB 2025 Data Fetch ===\n")

    print("Schedule:")
    games = fetch_all_games()
    out = RAW_DIR / "games_2025.csv"
    games.to_csv(out, index=False)
    print(f"  Saved -> {out}\n")

    print("Pitcher stats:")
    pitchers = fetch_pitcher_stats()
    out = RAW_DIR / "pitchers_2025.csv"
    pitchers.to_csv(out, index=False)
    print(f"  Saved -> {out}\n")

    print("Per-game pitching lines:")
    pitching_lines = fetch_game_pitching_lines(games)
    out = RAW_DIR / "pitcher_game_logs_2025.csv"
    pitching_lines.to_csv(out, index=False)
    print(f"  Saved -> {out}\n")

    print("Bullpen stats:")
    bullpens = aggregate_bullpen_from_pitchers(pitchers)
    if bullpens.empty:
        bullpens = fetch_team_pitching_proxy()
    out = RAW_DIR / "bullpens_2025.csv"
    bullpens.to_csv(out, index=False)
    print(f"  Saved -> {out}\n")

    print("Done.")
