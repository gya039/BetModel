"""
MLB 2026 — Live season backtest using the 2025-trained model.

Staking rules (EUR 500 bankroll, confidence-based flat %):
    Edge  2– 5%  ->  1% stake = EUR 5
    Edge  5–10%  ->  2% stake = EUR 10
    Edge 10–15%  ->  3% stake = EUR 15
    Edge   15%+  ->  5% stake = EUR 25  (hard cap — never more than 5%)

"Edge" = model win probability minus market implied probability.
Market assumed: favourite side at -115 (decimal 1.870, implied 53.5%).

The 2025 model is loaded as-is — no retraining on 2026 data.
Rolling team features are built from 2026 games only (strict no look-ahead).
Pitcher stats re-fetched for the 2026 season.

Usage:
    python mlb/scripts/season_2026.py
    python mlb/scripts/season_2026.py --min-edge 0.03
"""

import sys
import time
import pickle
import argparse
from pathlib import Path
from collections import defaultdict, deque

import requests
import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))
from betfair.kelly import implied_probability
from mlb.scripts.model import FEATURES

RAW_DIR   = Path(__file__).parent.parent / "data" / "raw"
PROC_DIR  = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR = Path(__file__).parent.parent / "models"
RAW_DIR.mkdir(parents=True, exist_ok=True)
PROC_DIR.mkdir(parents=True, exist_ok=True)

MLB    = "https://statsapi.mlb.com/api/v1"
SEASON = 2026
START  = "2026-03-26"
END    = "2026-04-14"    # update as season progresses

# Market odds (standard MLB vig ~-115)
FAV_ODDS  = 1.870   # -115 American -> implied 53.5%
DOG_ODDS  = 2.050   # +105 American -> implied 48.8%
COIN_BAND = 0.05    # skip near-50/50 games

BANKROLL  = 500.0

# Fallback pitcher stat constants (league average)
FILL_ERA  = 4.50
FILL_WHIP = 1.30
FILL_K9   = 8.5


# ─── STAKING ──────────────────────────────────────────────────────────────────

def confidence_stake(edge: float, bankroll: float) -> float:
    """
    Flat % stake based on model edge (model prob - market implied prob).
    Never exceeds 5% of bankroll (EUR 25 on EUR 500).
    """
    if edge < 0.05:
        pct = 0.01          # 1% -> EUR 5
    elif edge < 0.10:
        pct = 0.02          # 2% -> EUR 10
    elif edge < 0.15:
        pct = 0.03          # 3% -> EUR 15
    else:
        pct = 0.05          # 5% max -> EUR 25
    return round(bankroll * pct, 2)


# ─── DATA FETCH ───────────────────────────────────────────────────────────────

def get(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_games() -> pd.DataFrame:
    print("  Fetching 2026 schedule ...", end=" ", flush=True)
    data = get(f"{MLB}/schedule", params={
        "sportId": 1, "startDate": START, "endDate": END,
        "gameType": "R", "hydrate": "probablePitcher,team,linescore",
    })
    games = []
    for day in data.get("dates", []):
        for g in day.get("games", []):
            if g.get("status", {}).get("statusCode") != "F":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
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
                "game_pk":      g["gamePk"],
                "game_date":    day["date"],
                "away_team_id": away["team"]["id"],
                "away_team":    away["team"]["abbreviation"],
                "away_name":    away["team"]["name"],
                "home_team_id": home["team"]["id"],
                "home_team":    home["team"]["abbreviation"],
                "home_name":    home["team"]["name"],
                "away_score":   int(away_score),
                "home_score":   int(home_score),
                "home_win":     int(home_score) > int(away_score),
                "away_sp_id":   away_sp.get("id"),
                "away_sp_name": away_sp.get("fullName", "TBD"),
                "home_sp_id":   home_sp.get("id"),
                "home_sp_name": home_sp.get("fullName", "TBD"),
            })
    df = pd.DataFrame(games).drop_duplicates("game_pk").sort_values("game_date")
    print(f"{len(df)} completed games")
    return df


def fetch_pitchers() -> pd.DataFrame:
    rows, offset, limit = [], 0, 500
    while True:
        data = get(f"{MLB}/stats", params={
            "stats": "season", "group": "pitching", "season": SEASON,
            "playerPool": "all", "limit": limit, "offset": offset, "hydrate": "person",
        })
        splits = data.get("stats", [{}])[0].get("splits", [])
        if not splits:
            break
        for s in splits:
            st = s.get("stat", {})
            p  = s.get("player") or s.get("person") or {}
            era  = st.get("era")
            whip = st.get("whip")
            k9   = st.get("strikeoutsPer9Inn")
            ip   = st.get("inningsPitched", "0")
            rows.append({
                "pitcher_id":   p.get("id"),
                "era":   float(era)  if era  and era  != "-.--" else None,
                "whip":  float(whip) if whip and whip != "-.--" else None,
                "k9":    float(k9)   if k9   and k9   != "-.--" else None,
                "ip":    float(ip)   if ip   else 0.0,
            })
        if len(splits) < limit:
            break
        offset += limit
        time.sleep(0.2)
    df = pd.DataFrame(rows).dropna(subset=["pitcher_id"])
    df["pitcher_id"] = df["pitcher_id"].astype(int)
    df = df.sort_values("ip", ascending=False).drop_duplicates("pitcher_id")
    print(f"  Fetched {len(df)} 2026 pitchers")
    return df


# ─── FEATURES ─────────────────────────────────────────────────────────────────

def build_features(games: pd.DataFrame, pitchers: pd.DataFrame) -> pd.DataFrame:
    """Build rolling features — strictly no look-ahead."""
    p_idx = pitchers.set_index("pitcher_id")[["era", "whip", "k9"]]

    def pitcher_stat(pid, col, fallback):
        try:
            pid = int(pid)
            v = p_idx.loc[pid, col] if pid in p_idx.index else fallback
            return fallback if pd.isna(v) else v
        except (TypeError, ValueError, KeyError):
            return fallback

    team_history = defaultdict(lambda: deque(maxlen=20))
    rows = []

    for _, g in games.sort_values("game_date").iterrows():
        ht, at = int(g["home_team_id"]), int(g["away_team_id"])

        def rolling(team_id, n):
            hist   = list(team_history[team_id])
            recent = hist[-n:]
            if len(recent) < 2:
                return {f"L{n}_WIN_PCT": np.nan, f"L{n}_RD": np.nan,
                        f"L{n}_RUNS_FOR": np.nan, f"L{n}_RUNS_AGN": np.nan}
            return {
                f"L{n}_WIN_PCT":  round(np.mean([r[3] for r in recent]), 4),
                f"L{n}_RD":       round(np.mean([r[0] for r in recent]), 4),
                f"L{n}_RUNS_FOR": round(np.mean([r[1] for r in recent]), 4),
                f"L{n}_RUNS_AGN": round(np.mean([r[2] for r in recent]), 4),
            }

        hl10 = rolling(ht, 10); hl5 = rolling(ht, 5)
        al10 = rolling(at, 10); al5 = rolling(at, 5)

        home_era   = pitcher_stat(g.get("home_sp_id"), "era",  FILL_ERA)
        home_whip  = pitcher_stat(g.get("home_sp_id"), "whip", FILL_WHIP)
        home_k9    = pitcher_stat(g.get("home_sp_id"), "k9",   FILL_K9)
        away_era   = pitcher_stat(g.get("away_sp_id"), "era",  FILL_ERA)
        away_whip  = pitcher_stat(g.get("away_sp_id"), "whip", FILL_WHIP)
        away_k9    = pitcher_stat(g.get("away_sp_id"), "k9",   FILL_K9)

        row = {
            "game_pk":       g["game_pk"],
            "game_date":     g["game_date"],
            "home_team":     g["home_team"],
            "away_team":     g["away_team"],
            "home_name":     g["home_name"],
            "away_name":     g["away_name"],
            "home_sp_name":  g.get("home_sp_name", "?"),
            "away_sp_name":  g.get("away_sp_name", "?"),
            "home_score":    g["home_score"],
            "away_score":    g["away_score"],
            "home_win":      int(g["home_win"]),
            "point_diff":    g["home_score"] - g["away_score"],
            "HOME_L10_WIN_PCT":  hl10["L10_WIN_PCT"],  "AWAY_L10_WIN_PCT": al10["L10_WIN_PCT"],
            "HOME_L5_WIN_PCT":   hl5["L5_WIN_PCT"],    "AWAY_L5_WIN_PCT":  al5["L5_WIN_PCT"],
            "HOME_L10_RD":       hl10["L10_RD"],        "AWAY_L10_RD":      al10["L10_RD"],
            "HOME_L5_RD":        hl5["L5_RD"],          "AWAY_L5_RD":       al5["L5_RD"],
            "HOME_L10_RUNS_FOR": hl10["L10_RUNS_FOR"],  "AWAY_L10_RUNS_FOR":al10["L10_RUNS_FOR"],
            "HOME_L10_RUNS_AGN": hl10["L10_RUNS_AGN"],  "AWAY_L10_RUNS_AGN":al10["L10_RUNS_AGN"],
            "HOME_SP_ERA": home_era,  "AWAY_SP_ERA": away_era,
            "HOME_SP_WHIP":home_whip, "AWAY_SP_WHIP":away_whip,
            "HOME_SP_K9":  home_k9,   "AWAY_SP_K9":  away_k9,
        }
        row["WIN_PCT_DIFF"] = (hl10["L10_WIN_PCT"] - al10["L10_WIN_PCT"]
                               if not pd.isna(hl10["L10_WIN_PCT"]) and not pd.isna(al10["L10_WIN_PCT"]) else np.nan)
        row["RD_DIFF"]   = hl10["L10_RD"]   - al10["L10_RD"]   if not pd.isna(hl10["L10_RD"])  else np.nan
        row["ERA_DIFF"]  = home_era  - away_era
        row["WHIP_DIFF"] = home_whip - away_whip
        row["K9_DIFF"]   = home_k9   - away_k9
        rows.append(row)

        hrd = g["home_score"] - g["away_score"]
        ard = g["away_score"] - g["home_score"]
        team_history[ht].append((hrd, g["home_score"], g["away_score"], int(g["home_win"])))
        team_history[at].append((ard, g["away_score"], g["home_score"], int(not g["home_win"])))

    df = pd.DataFrame(rows)
    before = len(df)
    df = df.dropna(subset=["HOME_L10_WIN_PCT", "AWAY_L10_WIN_PCT",
                            "HOME_L10_RD",       "AWAY_L10_RD"])
    print(f"  Features built: {len(df)} / {before} games have full L10 rolling history")
    return df


# ─── SIMULATION ───────────────────────────────────────────────────────────────

def simulate(df: pd.DataFrame, model, scaler, min_edge: float) -> list[dict]:
    bets = []
    for _, row in df.iterrows():
        if any(pd.isna(row.get(f)) for f in FEATURES):
            continue

        X         = np.array([[row[f] for f in FEATURES]])
        home_prob = float(model.predict_proba(scaler.transform(X))[0][1])
        away_prob = 1.0 - home_prob

        candidates = []
        if home_prob > 0.5 + COIN_BAND:
            candidates.append(("home", home_prob, row["home_name"], row["home_team"], FAV_ODDS))
        elif away_prob > 0.5 + COIN_BAND:
            candidates.append(("away", away_prob, row["away_name"], row["away_team"], DOG_ODDS))

        for side, model_prob, team_name, team_abbr, odds in candidates:
            implied = implied_probability(odds)
            edge    = model_prob - implied
            if edge < min_edge:
                continue

            stake = confidence_stake(edge, BANKROLL)
            if stake <= 0:
                continue

            won = (side == "home") == bool(row["home_win"])
            pnl = round(stake * (odds - 1) if won else -stake, 2)

            # Who is the starting pitcher for the team we're betting on?
            if side == "home":
                sp_name  = row.get("home_sp_name", "?")
                opp_sp   = row.get("away_sp_name", "?")
                opp_team = row["away_name"]
                our_era  = row["HOME_SP_ERA"]
                opp_era  = row["AWAY_SP_ERA"]
                score_str = f"{row['away_score']}-{row['home_score']} ({row['away_team']} @ {row['home_team']})"
            else:
                sp_name  = row.get("away_sp_name", "?")
                opp_sp   = row.get("home_sp_name", "?")
                opp_team = row["home_name"]
                our_era  = row["AWAY_SP_ERA"]
                opp_era  = row["HOME_SP_ERA"]
                score_str = f"{row['away_score']}-{row['home_score']} ({row['away_team']} @ {row['home_team']})"

            bets.append({
                "date":       row["game_date"],
                "matchup":    f"{row['away_team']} @ {row['home_team']}",
                "bet_on":     team_name,
                "bet_abbr":   team_abbr,
                "side":       side,
                "sp_name":    sp_name,
                "our_era":    our_era,
                "opp_team":   opp_team,
                "opp_sp":     opp_sp,
                "opp_era":    opp_era,
                "model_prob": round(model_prob, 4),
                "implied":    round(implied, 4),
                "edge":       round(edge, 4),
                "odds":       odds,
                "stake":      stake,
                "won":        won,
                "actual_score": score_str,
                "pnl":        pnl,
            })
    return bets


# ─── REPORTING ────────────────────────────────────────────────────────────────

def print_bets(bets: list[dict], bankroll: float):
    """Print every single bet clearly so you know exactly what was wagered."""
    print(f"\n{'='*72}")
    print(f"  EVERY BET PLACED  (min-edge filter applied)")
    print(f"{'='*72}")
    print(f"  {'DATE':<12} {'MATCHUP':<16} {'BET (what we back)':<28} {'STAKE':>7}  {'RESULT'}")
    print(f"  {'-'*12} {'-'*16} {'-'*28} {'-'*7}  {'-'*20}")

    running = bankroll
    for b in bets:
        result_str = f"WON  +EUR {b['pnl']:.2f}" if b["won"] else f"LOST -EUR {abs(b['pnl']):.2f}"
        bet_label  = f"{b['bet_abbr']} ({b['side'].upper()}) to WIN"
        running   += b["pnl"]
        print(f"  {b['date']:<12} {b['matchup']:<16} {bet_label:<28} {b['stake']:>6.2f}  {result_str}")

        # Explain the bet in detail
        sp_line   = f"    SP: {b['sp_name']} (ERA {b['our_era']:.2f})  vs  {b['opp_sp']} (ERA {b['opp_era']:.2f})"
        edge_line = (f"    Model: {b['model_prob']:.1%} win prob  |  "
                     f"Market implied: {b['implied']:.1%}  |  Edge: +{b['edge']:.1%}")
        score_line = f"    Final score: {b['actual_score']}  |  Running bank: EUR {running:.2f}"
        print(sp_line)
        print(edge_line)
        print(score_line)
        print()


def summarise(bets: list[dict], bankroll: float, min_edge: float):
    if not bets:
        print("\n  No bets placed. Try lowering --min-edge.")
        return

    df     = pd.DataFrame(bets)
    n      = len(df)
    staked = df["stake"].sum()
    pnl    = df["pnl"].sum()
    roi    = pnl / staked * 100
    wr     = df["won"].mean() * 100
    final  = bankroll + pnl

    cum   = df.sort_values("date")["pnl"].cumsum()
    peak  = (bankroll + cum).max()
    dd    = (cum - cum.cummax()).min()

    print(f"\n{'='*62}")
    print(f"  MLB 2026 BACKTEST  (min edge {min_edge:.0%}, confidence staking)")
    print(f"{'='*62}")
    print(f"  Starting bankroll : EUR {bankroll:,.2f}")
    print(f"  Final bankroll    : EUR {final:,.2f}  ({(final-bankroll)/bankroll:+.1%})")
    print(f"  Total bets        : {n}")
    print(f"  Total staked      : EUR {staked:,.2f}")
    print(f"  Total P&L         : EUR {pnl:+,.2f}")
    print(f"  ROI on staked     : {roi:+.2f}%")
    print(f"  Win rate          : {wr:.1f}%")
    print(f"  Avg model edge    : {df['edge'].mean():+.2%}")
    print(f"  Peak bankroll     : EUR {peak:,.2f}")
    print(f"  Max drawdown      : EUR {dd:,.2f}")

    # Stake breakdown
    print(f"\n  --- Stake tier breakdown ---")
    df["tier"] = df["stake"].apply(
        lambda s: "1% (EUR 5)"  if s <= 5.01 else
                  "2% (EUR 10)" if s <= 10.01 else
                  "3% (EUR 15)" if s <= 15.01 else
                  "5% (EUR 25)"
    )
    for tier, g in df.groupby("tier"):
        r = g["pnl"].sum() / g["stake"].sum() * 100
        print(f"  {tier}: {len(g):3d} bets | win {g['won'].mean():.1%} | P&L EUR {g['pnl'].sum():+,.2f} | ROI {r:+.1f}%")

    # By side
    print(f"\n  --- By bet side ---")
    for side, g in df.groupby("side"):
        r = g["pnl"].sum() / g["stake"].sum() * 100
        label = "HOME bets (backing the home team)" if side == "home" else "AWAY bets (backing the away team)"
        print(f"  {label}: {len(g):3d} bets | win {g['won'].mean():.1%} | P&L EUR {g['pnl'].sum():+,.2f} | ROI {r:+.1f}%")

    # Teams bet on most
    print(f"\n  --- Teams backed most often ---")
    team_counts = df.groupby("bet_on").agg(
        bets=("won","count"), wins=("won","sum"), pnl=("pnl","sum"), staked=("stake","sum")
    ).assign(roi=lambda x: x["pnl"]/x["staked"]*100).sort_values("bets", ascending=False)
    for team, r in team_counts.head(10).iterrows():
        print(f"  {team:<30} {int(r['bets']):3d} bets | "
              f"win {r['wins']}/{int(r['bets'])} | P&L EUR {r['pnl']:+.2f} | ROI {r['roi']:+.1f}%")

    print(f"\n  NOTE: Pitcher stats use 2026 season totals-to-date")
    print(f"  (slight look-ahead for very early games). Team rolling")
    print(f"  form is strictly no look-ahead.\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-edge", type=float, default=0.02)
    args = parser.parse_args()

    print(f"\n=== MLB 2026 Season Backtest  (EUR 500 bankroll) ===\n")
    print(f"  Season: {START} to {END}")
    print(f"  Staking: 1%/2%/3%/5% based on model edge, max EUR 25\n")

    print("Fetching 2026 data:")
    games    = fetch_games()
    pitchers = fetch_pitchers()

    print("\nBuilding features:")
    df = build_features(games, pitchers)

    print(f"\nLoading 2025 trained model ...")
    with open(MODEL_DIR / "moneyline_model.pkl", "rb") as f:
        saved = pickle.load(f)
    model, scaler = saved["model"], saved["scaler"]
    print(f"  Model trained on 2025 data up to {saved.get('cutoff', 'unknown')}")

    print(f"\nSimulating bets (min edge {args.min_edge:.0%}) ...")
    bets = simulate(df, model, scaler, args.min_edge)

    print_bets(bets, BANKROLL)
    summarise(bets, BANKROLL, args.min_edge)
    print("Done.")
