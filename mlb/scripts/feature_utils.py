"""
Shared MLB feature definitions and conservative stat helpers.

The daily prediction, preprocessing, retraining, and diagnostics scripts all use
this module so the live feature vector stays aligned with the saved model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


FILL_ERA = 4.50
FILL_WHIP = 1.30
FILL_K9 = 8.5
FILL_FIP = 4.35
FILL_BB9 = 3.2
FILL_K_BB_PCT = 0.145
FILL_HR9 = 1.15
FILL_IP = 0.0
FILL_BULLPEN_ERA = 4.30
FILL_BULLPEN_WHIP = 1.32
FILL_BULLPEN_K_BB = 2.45
FILL_BULLPEN_RECENT_IP = 0.0

IP_BLEND_THRESHOLD = 30.0

BALLPARK_FACTORS = {
    "COL": 1.13, "BOS": 1.06, "CIN": 1.05, "PHI": 1.04, "TEX": 1.03,
    "CHC": 1.02, "MIL": 1.02, "DET": 1.01, "NYY": 1.01, "AZ": 1.00,
    "BAL": 1.00, "HOU": 1.00, "LAD": 0.99, "ATL": 0.99, "NYM": 0.99,
    "MIN": 0.98, "CLE": 0.98, "STL": 0.97, "PIT": 0.97, "KC": 0.97,
    "MIA": 0.97, "SF": 0.97, "SD": 0.97, "SEA": 0.97, "WSH": 0.96,
    "TOR": 0.96, "LAA": 0.96, "TB": 0.95, "CWS": 0.95, "ATH": 0.95,
}

PITCHER_FEATURES = [
    "SP_ERA",
    "SP_WHIP",
    "SP_K9",
    "SP_FIP",
    "SP_BB9",
    "SP_K_BB_PCT",
    "SP_HR9",
    "SP_IP",
    "SP_IS_LEFT",
]

BULLPEN_FEATURES = [
    "BP_ERA",
    "BP_WHIP",
    "BP_K_BB",
    "BP_IP_LAST_3D",
    "BP_IP_YESTERDAY",
    "BP_RELIEVERS_LAST_3D",
    "BP_RELIEVERS_YESTERDAY",
    "BP_TOP_USED_YESTERDAY",       # legacy: top 3 by appearances used yesterday
    "BP_TOP2_USED_YESTERDAY",      # top 2 by leverage score used yesterday
    "BP_TOP2_BACKTOBACK",          # top 2 used on back-to-back days
    "BP_TOP3_OUTS_LAST_3D",        # outs pitched by top 3 in last 3 days
]

FEATURES = [
    # Team rolling
    "HOME_L10_WIN_PCT",
    "AWAY_L10_WIN_PCT",
    "HOME_L5_WIN_PCT",
    "AWAY_L5_WIN_PCT",
    "HOME_L10_RD",
    "AWAY_L10_RD",
    "HOME_L5_RD",
    "AWAY_L5_RD",
    "HOME_L10_RUNS_FOR",
    "AWAY_L10_RUNS_FOR",
    "HOME_L10_RUNS_AGN",
    "AWAY_L10_RUNS_AGN",
    # Starting pitchers
    "HOME_SP_ERA",
    "AWAY_SP_ERA",
    "HOME_SP_WHIP",
    "AWAY_SP_WHIP",
    "HOME_SP_K9",
    "AWAY_SP_K9",
    "HOME_SP_FIP",
    "AWAY_SP_FIP",
    "HOME_SP_BB9",
    "AWAY_SP_BB9",
    "HOME_SP_K_BB_PCT",
    "AWAY_SP_K_BB_PCT",
    "HOME_SP_HR9",
    "AWAY_SP_HR9",
    "HOME_SP_IP",
    "AWAY_SP_IP",
    "HOME_SP_IS_LEFT",
    "AWAY_SP_IS_LEFT",
    # L20 rolling
    "HOME_L20_WIN_PCT",
    "AWAY_L20_WIN_PCT",
    "HOME_L20_RD",
    "AWAY_L20_RD",
    "HOME_L20_RUNS_FOR",
    "AWAY_L20_RUNS_FOR",
    "HOME_L20_RUNS_AGN",
    "AWAY_L20_RUNS_AGN",
    # Bullpen
    "HOME_BP_ERA",
    "AWAY_BP_ERA",
    "HOME_BP_WHIP",
    "AWAY_BP_WHIP",
    "HOME_BP_K_BB",
    "AWAY_BP_K_BB",
    "HOME_BP_IP_LAST_3D",
    "AWAY_BP_IP_LAST_3D",
    "HOME_BP_IP_YESTERDAY",
    "AWAY_BP_IP_YESTERDAY",
    "HOME_BP_RELIEVERS_LAST_3D",
    "AWAY_BP_RELIEVERS_LAST_3D",
    "HOME_BP_RELIEVERS_YESTERDAY",
    "AWAY_BP_RELIEVERS_YESTERDAY",
    "HOME_BP_TOP_USED_YESTERDAY",
    "AWAY_BP_TOP_USED_YESTERDAY",
    "HOME_BP_TOP2_USED_YESTERDAY",
    "AWAY_BP_TOP2_USED_YESTERDAY",
    "HOME_BP_TOP2_BACKTOBACK",
    "AWAY_BP_TOP2_BACKTOBACK",
    "HOME_BP_TOP3_OUTS_LAST_3D",
    "AWAY_BP_TOP3_OUTS_LAST_3D",
    # Ballpark
    "BALLPARK_FACTOR",
    # Derived differentials
    "WIN_PCT_DIFF",
    "RD_DIFF",
    "ERA_DIFF",
    "WHIP_DIFF",
    "K9_DIFF",
    "FIP_DIFF",
    "BB9_DIFF",
    "K_BB_PCT_DIFF",
    "HR9_DIFF",
    "SP_IP_DIFF",
    "SP_HAND_DIFF",
    "WIN_PCT_DIFF_L20",
    "RD_DIFF_L20",
    "BP_ERA_DIFF",
    "BP_WHIP_DIFF",
    "BP_K_BB_DIFF",
    "BP_IP_LAST_3D_DIFF",
    "BP_IP_YESTERDAY_DIFF",
    "BP_RELIEVERS_LAST_3D_DIFF",
    "BP_RELIEVERS_YESTERDAY_DIFF",
    "BP_TOP_USED_YESTERDAY_DIFF",
    "BP_TOP2_USED_YESTERDAY_DIFF",
    "BP_TOP2_BACKTOBACK_DIFF",
    "BP_TOP3_OUTS_LAST_3D_DIFF",
]


def safe_float(value, default=None):
    try:
        if value in (None, "", "-", "-.--"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_ip(value) -> float:
    """Convert MLB innings notation (12.1 = 12 + 1 out) to decimal innings."""
    raw = safe_float(value, 0.0)
    whole = int(raw)
    outs = round((raw - whole) * 10)
    if outs not in (0, 1, 2):
        return raw
    return whole + outs / 3.0


def compute_fip(hr: float, walks: float, strikeouts: float, ip: float, constant: float = 3.20) -> float:
    if not ip or ip <= 0:
        return FILL_FIP
    return ((13.0 * hr) + (3.0 * walks) - (2.0 * strikeouts)) / ip + constant


def compute_k_bb_pct(strikeouts: float, walks: float, batters_faced: float) -> float:
    if not batters_faced or batters_faced <= 0:
        return FILL_K_BB_PCT
    return (strikeouts - walks) / batters_faced


def blend_metric(value: float | None, ip: float | None, fill: float, threshold: float = IP_BLEND_THRESHOLD) -> float:
    if value is None or pd.isna(value):
        return fill
    ip = 0.0 if ip is None or pd.isna(ip) else float(ip)
    weight = min(1.0, max(0.0, ip / threshold))
    return round(weight * float(value) + (1.0 - weight) * fill, 4)


def pitcher_row_from_stat(stat: dict, person: dict | None = None, team: dict | None = None) -> dict:
    person = person or {}
    team = team or {}
    ip = parse_ip(stat.get("inningsPitched", 0))
    k = safe_float(stat.get("strikeOuts"), 0.0)
    walks = safe_float(stat.get("baseOnBalls"), 0.0)
    hr = safe_float(stat.get("homeRuns"), 0.0)
    bf = safe_float(stat.get("battersFaced"), 0.0)
    fip = compute_fip(hr, walks, k, ip)
    k_bb_pct = compute_k_bb_pct(k, walks, bf)
    hand = (person.get("pitchHand") or {}).get("code", "")
    return {
        "pitcher_id": person.get("id"),
        "pitcher_name": person.get("fullName", "?"),
        "team": team.get("abbreviation"),
        "era": safe_float(stat.get("era")),
        "whip": safe_float(stat.get("whip")),
        "k9": safe_float(stat.get("strikeoutsPer9Inn")),
        "fip": round(fip, 4),
        "bb9": safe_float(stat.get("walksPer9Inn"), FILL_BB9),
        "k_bb_pct": round(k_bb_pct, 4),
        "hr9": safe_float(stat.get("homeRunsPer9"), FILL_HR9),
        "ip": ip,
        "is_left": 1 if hand == "L" else 0,
        "wins": stat.get("wins", 0),
        "losses": stat.get("losses", 0),
        "k": k,
        "walks": walks,
        "home_runs": hr,
        "batters_faced": bf,
        "games_started": int(safe_float(stat.get("gamesStarted"), 0) or 0),
        "games_pitched": int(safe_float(stat.get("gamesPitched"), 0) or 0),
        "saves": int(safe_float(stat.get("saves"), 0) or 0),
        "holds": int(safe_float(stat.get("holds"), 0) or 0),
    }


def pitcher_features(pitchers: dict | pd.DataFrame, pid) -> dict:
    if isinstance(pitchers, pd.DataFrame):
        if "pitcher_id" in pitchers.columns:
            p_idx = pitchers.set_index("pitcher_id")
        else:
            p_idx = pitchers
        try:
            row = p_idx.loc[int(pid)].to_dict()
        except (TypeError, ValueError, KeyError):
            row = {}
    else:
        row = pitchers.get(pid) or pitchers.get(str(pid)) or {}

    ip = safe_float(row.get("ip"), 0.0)
    return {
        "SP_ERA": blend_metric(safe_float(row.get("era")), ip, FILL_ERA),
        "SP_WHIP": blend_metric(safe_float(row.get("whip")), ip, FILL_WHIP),
        "SP_K9": blend_metric(safe_float(row.get("k9")), ip, FILL_K9),
        "SP_FIP": blend_metric(safe_float(row.get("fip")), ip, FILL_FIP),
        "SP_BB9": blend_metric(safe_float(row.get("bb9")), ip, FILL_BB9),
        "SP_K_BB_PCT": blend_metric(safe_float(row.get("k_bb_pct")), ip, FILL_K_BB_PCT),
        "SP_HR9": blend_metric(safe_float(row.get("hr9")), ip, FILL_HR9),
        "SP_IP": min(float(ip or 0.0), IP_BLEND_THRESHOLD),
        "SP_IS_LEFT": int(safe_float(row.get("is_left"), 0) or 0),
    }


def neutral_bullpen_features() -> dict:
    return {
        "BP_ERA": FILL_BULLPEN_ERA,
        "BP_WHIP": FILL_BULLPEN_WHIP,
        "BP_K_BB": FILL_BULLPEN_K_BB,
        "BP_IP_LAST_3D": FILL_BULLPEN_RECENT_IP,
        "BP_IP_YESTERDAY": FILL_BULLPEN_RECENT_IP,
        "BP_RELIEVERS_LAST_3D": 0.0,
        "BP_RELIEVERS_YESTERDAY": 0.0,
        "BP_TOP_USED_YESTERDAY": 0.0,
        "BP_TOP2_USED_YESTERDAY": 0.0,
        "BP_TOP2_BACKTOBACK": 0.0,
        "BP_TOP3_OUTS_LAST_3D": 0.0,
    }


def bullpen_features(bullpens: dict | pd.DataFrame | None, team: str) -> dict:
    base = neutral_bullpen_features()
    if bullpens is None:
        return base
    if isinstance(bullpens, pd.DataFrame):
        if bullpens.empty or "team" not in bullpens.columns:
            return base
        rows = bullpens[bullpens["team"] == team]
        if rows.empty:
            return base
        row = rows.iloc[0].to_dict()
    else:
        row = bullpens.get(team, {})
    if not row:
        return base
    for key in base:
        base[key] = safe_float(row.get(key.lower()) or row.get(key), base[key])
    return base


def add_side_features(out: dict, prefix: str, values: dict) -> None:
    for key, value in values.items():
        out[f"{prefix}_{key}"] = value


def add_derived_diffs(df: pd.DataFrame) -> pd.DataFrame:
    df["WIN_PCT_DIFF"] = df["HOME_L10_WIN_PCT"] - df["AWAY_L10_WIN_PCT"]
    df["RD_DIFF"] = df["HOME_L10_RD"] - df["AWAY_L10_RD"]
    df["ERA_DIFF"] = df["HOME_SP_ERA"] - df["AWAY_SP_ERA"]
    df["WHIP_DIFF"] = df["HOME_SP_WHIP"] - df["AWAY_SP_WHIP"]
    df["K9_DIFF"] = df["HOME_SP_K9"] - df["AWAY_SP_K9"]
    df["FIP_DIFF"] = df["HOME_SP_FIP"] - df["AWAY_SP_FIP"]
    df["BB9_DIFF"] = df["HOME_SP_BB9"] - df["AWAY_SP_BB9"]
    df["K_BB_PCT_DIFF"] = df["HOME_SP_K_BB_PCT"] - df["AWAY_SP_K_BB_PCT"]
    df["HR9_DIFF"] = df["HOME_SP_HR9"] - df["AWAY_SP_HR9"]
    df["SP_IP_DIFF"] = df["HOME_SP_IP"] - df["AWAY_SP_IP"]
    df["SP_HAND_DIFF"] = df["HOME_SP_IS_LEFT"] - df["AWAY_SP_IS_LEFT"]
    df["WIN_PCT_DIFF_L20"] = df["HOME_L20_WIN_PCT"] - df["AWAY_L20_WIN_PCT"]
    df["RD_DIFF_L20"] = df["HOME_L20_RD"] - df["AWAY_L20_RD"]
    df["BP_ERA_DIFF"] = df["HOME_BP_ERA"] - df["AWAY_BP_ERA"]
    df["BP_WHIP_DIFF"] = df["HOME_BP_WHIP"] - df["AWAY_BP_WHIP"]
    df["BP_K_BB_DIFF"] = df["HOME_BP_K_BB"] - df["AWAY_BP_K_BB"]
    df["BP_IP_LAST_3D_DIFF"] = df["HOME_BP_IP_LAST_3D"] - df["AWAY_BP_IP_LAST_3D"]
    df["BP_IP_YESTERDAY_DIFF"] = df["HOME_BP_IP_YESTERDAY"] - df["AWAY_BP_IP_YESTERDAY"]
    df["BP_RELIEVERS_LAST_3D_DIFF"] = df["HOME_BP_RELIEVERS_LAST_3D"] - df["AWAY_BP_RELIEVERS_LAST_3D"]
    df["BP_RELIEVERS_YESTERDAY_DIFF"] = df["HOME_BP_RELIEVERS_YESTERDAY"] - df["AWAY_BP_RELIEVERS_YESTERDAY"]
    df["BP_TOP_USED_YESTERDAY_DIFF"] = df["HOME_BP_TOP_USED_YESTERDAY"] - df["AWAY_BP_TOP_USED_YESTERDAY"]
    for col in ("BP_TOP2_USED_YESTERDAY", "BP_TOP2_BACKTOBACK", "BP_TOP3_OUTS_LAST_3D"):
        home_col, away_col = f"HOME_{col}", f"AWAY_{col}"
        if home_col in df.columns and away_col in df.columns:
            df[f"{col}_DIFF"] = df[home_col] - df[away_col]
        else:
            df[f"{col}_DIFF"] = 0.0
    return df


def add_ballpark(df: pd.DataFrame) -> pd.DataFrame:
    df["BALLPARK_FACTOR"] = df["home_team"].map(BALLPARK_FACTORS).fillna(1.0)
    return df


def load_optional_bullpen_csv(raw_dir: Path, season: int | str) -> pd.DataFrame | None:
    path = raw_dir / f"bullpens_{season}.csv"
    if not path.exists() or path.stat().st_size <= 2:
        return None
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None


def aggregate_bullpen_from_pitchers(pitchers: pd.DataFrame) -> pd.DataFrame:
    if pitchers.empty or "team" not in pitchers.columns:
        return pd.DataFrame()
    p = pitchers.copy()
    for col in ("games_started", "games_pitched", "ip", "k", "walks", "home_runs", "batters_faced"):
        if col not in p.columns:
            p[col] = 0.0
    relievers = p[p["games_started"].fillna(0).astype(float) <= 0].copy()
    if relievers.empty:
        return pd.DataFrame()
    rows = []
    for team, g in relievers.groupby("team"):
        ip = g["ip"].fillna(0).astype(float).sum()
        if ip <= 0:
            continue
        walks = g["walks"].fillna(0).astype(float).sum()
        hits_proxy = np.maximum(0.0, (g["whip"].fillna(FILL_BULLPEN_WHIP).astype(float) * g["ip"].fillna(0).astype(float)).sum() - walks)
        k = g["k"].fillna(0).astype(float).sum()
        era = np.average(g["era"].fillna(FILL_BULLPEN_ERA).astype(float), weights=np.maximum(g["ip"].fillna(0).astype(float), 0.1))
        rows.append({
            "team": team,
            "bp_era": round(float(era), 3),
            "bp_whip": round(float((hits_proxy + walks) / ip), 3),
            "bp_k_bb": round(float(k / max(walks, 1.0)), 3),
            "bp_ip_last_3d": 0.0,
            "bp_ip_yesterday": 0.0,
            "bp_relievers_last_3d": 0.0,
            "bp_relievers_yesterday": 0.0,
            "bp_top_used_yesterday": 0.0,
            "bp_top2_used_yesterday": 0.0,
            "bp_top2_backtoback": 0.0,
            "bp_top3_outs_last_3d": 0.0,
        })
    return pd.DataFrame(rows)
