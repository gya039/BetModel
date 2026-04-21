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
    compute_fip,
    compute_k_bb_pct,
    neutral_bullpen_features,
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

def _empty_pitcher_state() -> dict:
    return {
        "ip": 0.0, "er": 0.0, "k": 0.0, "walks": 0.0,
        "home_runs": 0.0, "hits": 0.0, "batters_faced": 0.0,
        "is_left": 0,
    }


def _pitcher_snapshot(state: dict) -> dict:
    ip = float(state.get("ip", 0.0) or 0.0)
    if ip <= 0:
        raw = {
            "era": None, "whip": None, "k9": None, "fip": None, "bb9": None,
            "k_bb_pct": None, "hr9": None, "ip": 0.0, "is_left": state.get("is_left", 0),
        }
    else:
        walks = float(state.get("walks", 0.0) or 0.0)
        k = float(state.get("k", 0.0) or 0.0)
        hr = float(state.get("home_runs", 0.0) or 0.0)
        hits = float(state.get("hits", 0.0) or 0.0)
        bf = float(state.get("batters_faced", 0.0) or 0.0)
        raw = {
            "era": state.get("er", 0.0) * 9.0 / ip,
            "whip": (hits + walks) / ip,
            "k9": k * 9.0 / ip,
            "fip": compute_fip(hr, walks, k, ip),
            "bb9": walks * 9.0 / ip,
            "k_bb_pct": compute_k_bb_pct(k, walks, bf),
            "hr9": hr * 9.0 / ip,
            "ip": ip,
            "is_left": state.get("is_left", 0),
        }
    return pitcher_features({0: raw}, 0)


def _update_pitcher_state(state: dict, line: dict) -> None:
    for key in ("ip", "er", "k", "walks", "home_runs", "hits", "batters_faced"):
        state[key] = float(state.get(key, 0.0) or 0.0) + float(line.get(key, 0.0) or 0.0)
    if line.get("is_left"):
        state["is_left"] = int(line.get("is_left", 0))


def _bullpen_quality_from_history(lines: list[dict]) -> dict:
    if not lines:
        base = neutral_bullpen_features()
        return {k: base[k] for k in ("BP_ERA", "BP_WHIP", "BP_K_BB")}
    ip = sum(float(x.get("ip", 0.0) or 0.0) for x in lines)
    if ip <= 0:
        base = neutral_bullpen_features()
        return {k: base[k] for k in ("BP_ERA", "BP_WHIP", "BP_K_BB")}
    er = sum(float(x.get("er", 0.0) or 0.0) for x in lines)
    hits = sum(float(x.get("hits", 0.0) or 0.0) for x in lines)
    walks = sum(float(x.get("walks", 0.0) or 0.0) for x in lines)
    k = sum(float(x.get("k", 0.0) or 0.0) for x in lines)
    return {
        "BP_ERA": round(er * 9.0 / ip, 4),
        "BP_WHIP": round((hits + walks) / ip, 4),
        "BP_K_BB": round(k / max(walks, 1.0), 4),
    }


def _bullpen_fatigue(team: str, game_date: pd.Timestamp, bullpen_daily: dict, top_relievers: dict) -> dict:
    rows = list(bullpen_daily.get(team, []))
    last3 = [r for r in rows if 1 <= (game_date - r["date"]).days <= 3]
    yday = [r for r in rows if (game_date - r["date"]).days == 1]
    top_ids = top_relievers.get(team, set())
    quality = _bullpen_quality_from_history([
        line for r in rows for line in r.get("lines", [])
    ])
    return {
        **quality,
        "BP_IP_LAST_3D": round(sum(r["ip"] for r in last3), 3),
        "BP_IP_YESTERDAY": round(sum(r["ip"] for r in yday), 3),
        "BP_RELIEVERS_LAST_3D": sum(r["relievers"] for r in last3),
        "BP_RELIEVERS_YESTERDAY": sum(r["relievers"] for r in yday),
        "BP_TOP_USED_YESTERDAY": 1 if any(top_ids & set(r.get("pitcher_ids", [])) for r in yday) else 0,
    }


def rolling_stats(games: pd.DataFrame, pitcher_game_logs: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    For each game, compute rolling features from PRIOR games for both teams.
    Uses a per-team deque of recent results (run differential, win/loss).
    """

    # Build chronological list of results per team
    # Key: team_id -> deque of (run_diff, runs_for, runs_against, win)
    team_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=20))
    pitcher_history: dict[int, dict] = defaultdict(_empty_pitcher_state)
    bullpen_daily: dict[str, deque] = defaultdict(lambda: deque(maxlen=14))
    reliever_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    top_relievers: dict[str, set[int]] = defaultdict(set)

    if pitcher_game_logs is not None and not pitcher_game_logs.empty:
        plogs = pitcher_game_logs.copy()
        plogs["game_date"] = pd.to_datetime(plogs["game_date"])
        game_pitching = {
            int(pk): g.to_dict("records")
            for pk, g in plogs.groupby("game_pk")
        }
    else:
        game_pitching = {}

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
        game_date = pd.to_datetime(g["game_date"])
        home_sp_features = _pitcher_snapshot(pitcher_history[int(g["home_sp_id"])]) if pd.notna(g.get("home_sp_id")) else _pitcher_snapshot({})
        away_sp_features = _pitcher_snapshot(pitcher_history[int(g["away_sp_id"])]) if pd.notna(g.get("away_sp_id")) else _pitcher_snapshot({})
        home_bp = _bullpen_fatigue(g["home_team"], game_date, bullpen_daily, top_relievers)
        away_bp = _bullpen_fatigue(g["away_team"], game_date, bullpen_daily, top_relievers)

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
        for key, value in home_sp_features.items():
            row[f"HOME_{key}"] = value
        for key, value in away_sp_features.items():
            row[f"AWAY_{key}"] = value
        for key, value in home_bp.items():
            row[f"HOME_{key}"] = value
        for key, value in away_bp.items():
            row[f"AWAY_{key}"] = value
        rows.append(row)

        # Update history AFTER building features (no look-ahead)
        home_rd = g["home_score"] - g["away_score"]
        away_rd = g["away_score"] - g["home_score"]
        team_history[ht].append((home_rd, g["home_score"], g["away_score"], int(g["home_win"])))
        team_history[at].append((away_rd, g["away_score"], g["home_score"], int(not g["home_win"])))

        # Update pitcher and bullpen states only after features are created.
        for line in game_pitching.get(int(g["game_pk"]), []):
            pid = int(line["pitcher_id"])
            _update_pitcher_state(pitcher_history[pid], line)
        for team in (g["home_team"], g["away_team"]):
            reliever_lines = [
                line for line in game_pitching.get(int(g["game_pk"]), [])
                if line.get("team") == team and int(line.get("is_starter", 0)) == 0
            ]
            for line in reliever_lines:
                reliever_counts[team][int(line["pitcher_id"])] += 1
            top_relievers[team] = {
                pid for pid, _ in sorted(reliever_counts[team].items(), key=lambda item: item[1], reverse=True)[:3]
            }
            bullpen_daily[team].append({
                "date": game_date,
                "ip": sum(float(line.get("ip", 0.0) or 0.0) for line in reliever_lines),
                "relievers": len({int(line["pitcher_id"]) for line in reliever_lines}),
                "pitcher_ids": [int(line["pitcher_id"]) for line in reliever_lines],
                "lines": reliever_lines,
            })

    return pd.DataFrame(rows)


def merge_pitchers(df: pd.DataFrame, pitchers: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compatibility hook: historical SP features are built pregame-only in rolling_stats()."""
    return df


def merge_bullpens(df: pd.DataFrame, bullpens: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compatibility hook: historical bullpen features are built pregame-only in rolling_stats()."""
    return df


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    return add_derived_diffs(df)


def merge_ballpark(df: pd.DataFrame) -> pd.DataFrame:
    return add_ballpark(df)


if __name__ == "__main__":
    print("\n=== MLB 2025 Preprocessing ===\n")

    games = pd.read_csv(RAW_DIR / "games_2025.csv", parse_dates=["game_date"])
    pitcher_logs_path = RAW_DIR / "pitcher_game_logs_2025.csv"
    pitcher_logs = None
    if pitcher_logs_path.exists():
        pitcher_logs = pd.read_csv(pitcher_logs_path)
        print(f"  Loaded {len(games)} games, {len(pitcher_logs)} pitcher game lines")
    else:
        print("  Pitcher game logs not found; using neutral pregame SP/bullpen features")

    df = rolling_stats(games, pitcher_logs)
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
