# BettingModel — What This System Does

## Overview

A sports betting model that finds +EV (positive expected value) bets by comparing model-predicted probabilities against live bookmaker odds. Built in Python, connected live to the Betfair Exchange API.

---

## Architecture

```
BettingModel/
├── betfair/          — Betfair Exchange API wrapper + Kelly staking
├── football/         — La Liga + Bundesliga outcome model
├── nba/              — NBA spread, moneyline + player prop models
├── tracker/          — SQLite bet log, P&L, ROI tracking
└── racing/           — Horse racing lay strategy (Irish markets)
```

---

## How the Pipeline Works

### 1. Data
- **Football:** Downloads historical match CSVs from `football-data.co.uk` (free). Covers La Liga and Bundesliga, 4 seasons (2021/22 – 2024/25).
- **NBA:** Pulls game logs and player box scores from `stats.nba.com` via `nba_api` (official NBA stats API, free). Covers 3 seasons (2022-23 – 2024-25), ~78,000 player-game rows.

### 2. Features

**Football (odds-free — no bookmaker signals in training):**
- Elo ratings (updated after every match, home advantage baked in, K=20)
- Last 5 + last 10 rolling form: PPG, goals for/against, goal difference
- Venue-specific form: last 5 home games (for home team), last 5 away games (for away team)
- H2H last 5 meetings: home win rate, draw rate, avg goals
- Shots on target ratio (xG proxy, last 5 games)

**NBA games:**
- Rolling 10/5-game net rating, win%, points for/against
- Rest days (home & away)
- Back-to-back flag (known performance drop)
- Rest advantage (home rest days minus away rest days)

**NBA player props:**
- Rolling 5/10-game averages: PTS, REB, AST, 3PM, MIN
- Opponent defensive rating (proxy via points allowed)
- Rest days, back-to-back, home/away

### 3. Models

| Model | Algorithm | Target |
|---|---|---|
| Football outcome | Logistic Regression | Home / Draw / Away |
| NBA moneyline | Logistic Regression | Home win probability |
| NBA spread | Ridge Regression | Point differential |
| NBA props (PTS) | Ridge Regression | Expected points (vs line) |
| NBA props (REB) | Ridge Regression | Expected rebounds |
| NBA props (AST) | Ridge Regression | Expected assists |
| NBA props (3PM) | Ridge Regression | Expected 3-pointers |

All models trained on prior seasons only (walk-forward, no look-ahead bias).

### 4. Value Finding

- Model probability is compared against live bookmaker implied probability
- If `model_prob > implied_prob` by at least 2%, it's flagged as a value bet
- Stake is sized using **quarter Kelly criterion** (conservative, reduces variance)
- Football scanner connects live to Betfair Exchange via `betfairlightweight`
- NBA scanner also connects to Betfair (moneyline + spread markets)

### 5. Bet Logging

- Every bet logged to SQLite (`bets.db`) via `tracker/database.py`
- Tracks: selection, side, model prob, odds, implied prob, edge, stake, result, P&L, closing odds
- Summary reports: total staked, total P&L, ROI, win rate, avg edge

---

## Current Model Performance (Backtested)

| Model | Period | ROI | Notes |
|---|---|---|---|
| Football (La Liga) | 2024-25 | -32% | Not ready for live betting |
| Football (Bundesliga) | 2024-25 | -17% | Not ready for live betting |
| NBA moneyline | N/A | Unvalidated | Needs real market odds per game |
| NBA props | N/A | 75.9% O/U acc (PTS) | Needs live prop lines to validate |

**Status: Models are built and connected. Not yet profitable. Needs more work before live staking.**

---

## What's Connected Live

- Betfair Exchange API (authenticated, SSL cert registered)
- Credentials in `.env` (username, password, app key, cert path)
- Can scan live markets, get real-time odds, place bets programmatically

## What's Pending

- **The Odds API** (verification in progress) — needed for SkyBet, PaddyPower, William Hill prop lines
- Once wired in, NBA prop scanner goes live
- Football model needs XGBoost + more leagues to be competitive

---

## Key Files

| File | What it does |
|---|---|
| `football/scripts/find_value.py` | Live Betfair scanner for football value bets |
| `football/scripts/backtest.py` | Walk-forward backtest with P&L reporting |
| `nba/scripts/find_value.py` | Live Betfair scanner for NBA moneyline/spread |
| `nba/scripts/model_props.py` | Player prop model — predict vs book line |
| `betfair/kelly.py` | Kelly criterion stake calculator |
| `tracker/database.py` | Bet log, settle bets, P&L summary |

---

## Running the System

```bash
# Football — scan live Betfair markets (dry run)
python football/scripts/find_value.py

# Football — place bets live
python football/scripts/find_value.py --place

# Football — backtest
python football/scripts/backtest.py

# NBA — scan live Betfair markets
python nba/scripts/find_value.py

# Refresh data + retrain (run before each scan)
python football/scripts/fetch_data.py
python football/scripts/preprocess.py
python football/scripts/model.py
```
