"""
Retrain the MLB moneyline model using 2025 + available 2026 season data.

Steps:
  1. Fetch all completed 2026 regular season games (2026-03-26 to yesterday)
  2. Fetch 2026 pitcher season stats
  3. Save raw 2026 data to mlb/data/raw/
  4. Preprocess 2026 data (same pipeline as 2025)
  5. Combine 2025 + 2026 processed data chronologically
  6. Retrain logistic regression on combined data
  7. Evaluate on a held-out recent window and save new pkl

Run this monthly (or whenever a meaningful amount of 2026 data has accumulated).

Usage:
    python mlb/scripts/retrain_2026.py
    python mlb/scripts/retrain_2026.py --no-save   # dry run, shows eval only
"""

import sys
import time
import pickle
import argparse
from datetime import date, timedelta
from pathlib import Path

import requests
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.calibration import CalibratedClassifierCV
from sklearn.frozen import FrozenEstimator
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, roc_auc_score

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.append(str(Path(__file__).parent.parent.parent))
from mlb.scripts.model import FEATURES
from mlb.scripts.preprocess import rolling_stats, add_derived, merge_ballpark
from mlb.scripts.fetch_data import fetch_game_pitching_lines
from mlb.scripts.feature_utils import pitcher_row_from_stat

RAW_DIR   = Path(__file__).parent.parent / "data" / "raw"
PROC_DIR  = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR = Path(__file__).parent.parent / "models"
MLB       = "https://statsapi.mlb.com/api/v1"
SEASON    = 2026
START_2026 = "2026-03-26"


# ─── DATA FETCHING ────────────────────────────────────────────────────────────

def get(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_2026_games(end_date: str) -> pd.DataFrame:
    """Fetch all completed 2026 regular season games up to end_date."""
    print(f"  Fetching 2026 games ({START_2026} → {end_date}) ...", flush=True)
    data = get(
        f"{MLB}/schedule",
        params={
            "sportId":   1,
            "startDate": START_2026,
            "endDate":   end_date,
            "gameType":  "R",
            "hydrate":   "probablePitcher,team,linescore",
        },
    )
    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("statusCode") != "F":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            a_score = away.get("score")
            h_score = home.get("score")
            if a_score is None or h_score is None:
                ls = g.get("linescore", {}).get("teams", {})
                a_score = ls.get("away", {}).get("runs")
                h_score = ls.get("home", {}).get("runs")
            if a_score is None or h_score is None:
                continue
            away_sp = away.get("probablePitcher", {})
            home_sp = home.get("probablePitcher", {})
            games.append({
                "game_pk":      g["gamePk"],
                "game_date":    day["date"],
                "away_team_id": away["team"]["id"],
                "away_team":    away["team"]["abbreviation"],
                "home_team_id": home["team"]["id"],
                "home_team":    home["team"]["abbreviation"],
                "away_score":   int(a_score),
                "home_score":   int(h_score),
                "home_win":     int(h_score) > int(a_score),
                "away_sp_id":   away_sp.get("id"),
                "away_sp_name": away_sp.get("fullName", "TBD"),
                "home_sp_id":   home_sp.get("id"),
                "home_sp_name": home_sp.get("fullName", "TBD"),
            })

    df = pd.DataFrame(games).drop_duplicates("game_pk").sort_values("game_date") if games else pd.DataFrame()
    print(f"  {len(df)} completed 2026 games")
    return df


def fetch_2026_pitchers() -> pd.DataFrame:
    """Fetch 2026 season pitcher stats (ERA, WHIP, K/9)."""
    print("  Fetching 2026 pitcher stats ...", flush=True)
    rows = []
    limit, offset = 500, 0
    while True:
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
        time.sleep(0.2)

    df = pd.DataFrame(rows).dropna(subset=["pitcher_id"])
    df["pitcher_id"] = df["pitcher_id"].astype(int)
    df = df.sort_values("ip", ascending=False).drop_duplicates("pitcher_id")
    print(f"  {len(df)} pitchers with 2026 stats")
    return df


def fetch_2026_team_pitching_proxy() -> pd.DataFrame:
    """Team pitching quality proxy for bullpen columns when player rows lack teams."""
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


# ─── PREPROCESSING ────────────────────────────────────────────────────────────

def preprocess(games_df: pd.DataFrame, pitcher_logs_df: pd.DataFrame | None = None) -> pd.DataFrame:
    df = rolling_stats(games_df, pitcher_logs_df)
    df = add_derived(df)
    df = merge_ballpark(df)
    before = len(df)
    df = df.dropna(subset=[
        "HOME_L10_WIN_PCT", "AWAY_L10_WIN_PCT",
        "HOME_L10_RD",      "AWAY_L10_RD",
        "HOME_L20_WIN_PCT", "AWAY_L20_WIN_PCT",
        "HOME_L20_RD",      "AWAY_L20_RD",
    ])
    print(f"  After NaN drop: {len(df)} / {before} games usable")
    return df


# ─── TRAINING & EVALUATION ────────────────────────────────────────────────────

def train_model(df: pd.DataFrame, test_frac: float = 0.15):
    """
    Chronological split: last test_frac of games are held out for evaluation.
    Train on everything before that.
    """
    cutoff_idx = int(len(df) * (1 - test_frac))
    train_df   = df.iloc[:cutoff_idx]
    test_df    = df.iloc[cutoff_idx:]
    print(f"  Train: {len(train_df)} games  |  Test (held-out recent): {len(test_df)} games")

    calib_idx = max(20, int(len(train_df) * 0.80))
    fit_df = train_df.iloc[:calib_idx]
    calib_df = train_df.iloc[calib_idx:]
    X_train = fit_df[FEATURES].values
    y_train = fit_df["home_win"].values
    scaler  = StandardScaler()
    model   = LogisticRegression(C=1.0, max_iter=500, random_state=42)
    model.fit(scaler.fit_transform(X_train), y_train)
    if len(calib_df) >= 20 and calib_df["home_win"].nunique() == 2:
        model = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
        model.fit(scaler.transform(calib_df[FEATURES].values), calib_df["home_win"].values)

    # Evaluate on held-out
    X_test = test_df[FEATURES].values
    y_test = test_df["home_win"].values
    probs  = model.predict_proba(scaler.transform(X_test))[:, 1]
    preds  = (probs > 0.5).astype(int)

    acc   = accuracy_score(y_test, preds)
    ll    = log_loss(y_test, probs)
    brier = brier_score_loss(y_test, probs)
    auc   = roc_auc_score(y_test, probs)

    print(f"\n  --- Held-out evaluation (most recent {test_frac:.0%} of data) ---")
    print(f"  Accuracy  : {acc:.1%}  (baseline home win = {y_test.mean():.1%})")
    print(f"  AUC-ROC   : {auc:.4f}")
    print(f"  Log loss  : {ll:.4f}")
    print(f"  Brier     : {brier:.4f}")

    # Top features
    coef_model = model
    if not hasattr(coef_model, "coef_") and hasattr(model, "calibrated_classifiers_"):
        coef_model = model.calibrated_classifiers_[0].estimator
    if hasattr(coef_model, "coef_"):
        coefs = sorted(
            zip(FEATURES, coef_model.coef_[0]),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        print(f"\n  Top 10 features (by |coef|):")
        for feat, coef in coefs[:10]:
            direction = "HOME+" if coef > 0 else "AWAY+"
            print(f"    {feat:<28} {abs(coef):.4f}  [{direction}]")

    return model, scaler


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-save", action="store_true", help="Dry run — evaluate but don't save pkl")
    parser.add_argument("--test-frac", type=float, default=0.15, help="Fraction of data held out for eval (default 0.15)")
    args = parser.parse_args()

    yesterday = str(date.today() - timedelta(days=1))

    print("\n=== MLB Model Retrain (2025 + 2026) ===\n")

    # --- 1. Fetch + save 2026 raw data ---
    print("[ 1/4 ] Fetching 2026 data ...")
    games_2026    = fetch_2026_games(yesterday)
    pitchers_2026 = fetch_2026_pitchers()

    if games_2026.empty:
        print("  No 2026 games found. Cannot retrain yet.")
        sys.exit(0)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    games_2026.to_csv(RAW_DIR / "games_2026.csv", index=False)
    pitchers_2026.to_csv(RAW_DIR / "pitchers_2026.csv", index=False)
    pitcher_logs_2026 = fetch_game_pitching_lines(games_2026)
    pitcher_logs_2026.to_csv(RAW_DIR / "pitcher_game_logs_2026.csv", index=False)
    print(f"  Saved raw 2026 data to {RAW_DIR}")

    # --- 2. Preprocess 2026 ---
    print("\n[ 2/4 ] Preprocessing 2026 data ...")
    df_2026 = preprocess(games_2026, pitcher_logs_2026)

    # --- 3. Load + combine with 2025 processed ---
    print("\n[ 3/4 ] Combining 2025 + 2026 data ...")
    proc_2025 = PROC_DIR / "games_processed.csv"
    if proc_2025.exists():
        df_2025 = pd.read_csv(proc_2025, parse_dates=["game_date"])
        # Keep only rows that have all new features (older processed files may lack L20/ballpark)
        missing_cols = [c for c in FEATURES if c not in df_2025.columns]
        if missing_cols:
            print(f"  WARNING: 2025 processed CSV is missing {missing_cols}.")
            print(f"  Re-run: python mlb/scripts/preprocess.py  then retry.")
            sys.exit(1)
        df_2025 = df_2025.dropna(subset=FEATURES + ["home_win"])
        combined = pd.concat([df_2025, df_2026], ignore_index=True)
        combined["game_date"] = pd.to_datetime(combined["game_date"])
        combined = combined.sort_values("game_date")
        print(f"  2025: {len(df_2025)} games  +  2026: {len(df_2026)} games  =  {len(combined)} total")
    else:
        print("  No 2025 processed CSV found — training on 2026 only.")
        combined = df_2026

    combined = combined.dropna(subset=FEATURES + ["home_win"]).reset_index(drop=True)
    print(f"  Final training set: {len(combined)} games  ({combined['game_date'].min()} → {combined['game_date'].max()})")

    # --- 4. Train + evaluate ---
    print("\n[ 4/4 ] Training model ...")
    model, scaler = train_model(combined, test_frac=args.test_frac)

    if args.no_save:
        print("\n  --no-save set. Model NOT written to disk.")
    else:
        out_path = MODEL_DIR / "moneyline_model.pkl"
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        with open(out_path, "wb") as f:
            pickle.dump({
                "model":    model,
                "scaler":   scaler,
                "features": FEATURES,
                "trained_through": yesterday,
                "n_games": len(combined),
            }, f)
        print(f"\n  Saved -> {out_path}")
        print(f"  Trained through: {yesterday}")
        print(f"  Features: {len(FEATURES)}")

    print("\nDone.")
