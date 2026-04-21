# How Diamond Edge MLB Picks Work â€” Full Technical Breakdown

---

## Current Implementation Note

The active moneyline model loaded by `predict_today.py` uses the feature list saved inside `mlb/models/moneyline_model.pkl` (76 features on the April 21 run). Current edge calculation uses no-vig moneyline probability when both sides are available, and stake sizing reads the live bankroll from `mlb/predictions/results_log.csv`.

Run-line output is now evaluated on every eligible game because `USE_SPREAD_MODEL=True`, but production selection still requires the saved spread model to pass validation. The current spread model artifact loads and generates +EV candidates, but validation fails on ECE and favorite-cover sanity checks, so production picks and accumulators remain moneyline-only. The JSON and app expose RL diagnostic fields showing the best line, edge, cover probability, positive line count, validation status, and rejection reason.

## Overview

The system is a **logistic regression model** trained on full 2025 MLB season data, deployed daily against live 2026 games. It predicts the probability that the home team wins, compares that probability against the bookmakers' implied probability (derived from the offered odds), and bets whenever the model sees a meaningful edge.

There are two commands in the daily workflow:
- `/generate-mlb-predictions` â€” Morning pipeline: data â†’ model â†’ picks â†’ reports
- `/check-movement` â€” Afternoon pipeline: odds comparison â†’ injury check â†’ SP confirmation â†’ updated picks

---

## Part 1 â€” The Model (How It Was Built)

### Training Data (`fetch_data.py` â†’ `preprocess.py` â†’ `model.py`)

**Step 1 â€” Fetch historical data**

`fetch_data.py` pulls from the **official MLB Stats API** (free, no key required) in monthly chunks:
- Every completed 2025 regular-season game: date, teams, scores, probable starters
- Full 2026 season pitching stats for all pitchers: ERA, WHIP, K/9, IP

**Step 2 â€” Build features (`preprocess.py`)**

For each historical game, the saved model feature contract is computed strictly from data available *before* that game was played (no look-ahead). The current moneyline model uses 76 features:

| Feature Group | Features |
|---|---|
| Rolling team form (L5) | Win%, run differential, runs for, runs against |
| Rolling team form (L10) | Same four stats |
| Rolling team form (L20) | Same four stats |
| Starting pitcher | ERA, WHIP, K/9 for home and away SP |
| Ballpark factor | Single multiplier per park (Coors = 1.13, Tropicana = 0.95, etc.) |
| Derived differentials | Home minus away: win%, run diff, ERA, WHIP, K/9 |

Rolling stats use a **deque of max 20 games** per team â€” history is updated *after* each game's features are locked in, preventing any data leakage.

**Step 3 â€” Train the model (`model.py`)**

A **logistic regression** is trained on 2025 games using a chronological split (first 40% = train, remaining 60% = test â€” never random split, which would leak future data). Standard scaling is applied before fitting.

Output: `mlb/models/moneyline_model.pkl` â€” saves the model, scaler, feature list, and cutoff date.

The model outputs a single probability: **P(home team wins)**.

---

## Part 2 â€” The Morning Pipeline (`/generate-mlb-predictions`)

This is the main daily command. It runs the full pipeline from scratch each morning.

### Step 1 â€” Load completed 2026 games

`predict_today.py` calls the MLB Stats API for every completed regular-season game from 2026-03-26 through yesterday. This builds a **live rolling team state**: for every team, a running deque of their last 20 game results (score, run diff, win/loss). This is the same rolling logic as training â€” the model always sees current form, not 2025 form.

### Step 2 â€” Fetch today's schedule

Calls the MLB schedule API for today's date, hydrated with `probablePitcher`. Returns: team IDs, team abbreviations, home/away SP names and IDs, game status.

### Step 3 â€” Fetch 2026 pitcher stats

Calls the MLB stats API for all pitchers' 2026 season totals (ERA, WHIP, K/9, IP). These are live, updating as the season progresses.

**Sample-size blending:** To stop a pitcher with 2 starts and a 0.43 ERA from generating a fake 20% edge, every pitcher's stats are blended toward league average based on their IP:

```
weight  = min(1.0, IP / 30.0)
blended = weight Ã— raw_stat + (1 - weight) Ã— league_average
```

A pitcher with 0 IP â†’ 100% league average (ERA 4.50, WHIP 1.30, K/9 8.5).  
A pitcher with 30+ IP â†’ fully trusted raw stats.

### Step 4 â€” Build features and run the model

For each of today's games, `build_features()` assembles the same saved feature vector that the model was trained on - 76 features in the current moneyline artifact - using today's rolling team state and today's blended pitcher stats. The vector is passed through the trained scaler and logistic regression to get **P(home win)** and by subtraction **P(away win)**.

### Step 5 â€” Fetch live odds

Calls **The Odds API** for UK bookmakers (Paddy Power, Sky Bet, Boylesports preferred; all UK books as fallback). Returns H2H moneyline, standard run line odds, and available alternate spread options for every matched game.

Morning odds are **archived** to `mlb/predictions/odds_archive/<date>_morning_odds.json` for later CLV (Closing Line Value) calculation.

### Step 6 â€” Calculate edge and pick the side

For each game:

1. **Market implied probability** = 1 / best available odds for that side
2. **Model probability** = output from the logistic regression
3. **Edge** = model probability âˆ’ market implied probability

The model always picks the side with the higher model probability. If edge < 0, the market is sharper than the model on that side â€” pass.

**Run Line logic:** A separate spread model evaluates the best available run-line / alternate-spread option for the picked side. The pipeline records the best RL line, odds, cover probability, edge, positive EV line count, validation status, and rejection reason. RL can only replace ML when `USE_SPREAD_MODEL=True`, the spread model passes validation, the RL edge is at least 3%, and the RL edge beats the ML edge. At the moment, `USE_SPREAD_MODEL=True` but validation fails, so RL remains diagnostic-only.

### Step 7 â€” Apply stake tiers

Edge determines stake size as a percentage of the current bankroll:

| Edge | Stake | Label |
|---|---|---|
| < 1% | 0% â€” SKIP | pass |
| 1â€“3% | 0.5% | micro |
| 3â€“6% | 1% | low |
| 6â€“10% | 2% | low-mid |
| 10â€“15% | 3% | mid |
| 15â€“20% | 4% | mid-high |
| â‰¥ 20% | 5% | high |

> Note: CLAUDE.md specifies â‰¥ 3% as the practical BET threshold. The code's 1â€“3% "micro" tier technically generates bets at very small stake but these are generally not placed.

### Step 8 â€” Decision logic

A game is marked **BET** only if all of the following are true:
- Model has a directional pick (not a 50/50 split)
- Both starting pitchers are confirmed (not TBD)
- Odds data is available for the picked side
- Edge â‰¥ 1% and stake > EUR 0

Games are **SKIP** if: SP is TBD, no odds available, edge below threshold, or model has no clear direction.

### Step 9 â€” Acca / Bet Builder

The top picks by edge (â‰¥ 3%) are combined into accumulators: Double, Treble, Quad, 5-Fold, 6-Fold. Stakes are from a **separate EUR 100 acca bankroll** and never touch single bet P&L.

### Step 10 â€” Output files

Written to `mlb/predictions/<Month> Predictions/<Month> <Ordinal>/`:

| File | Contents |
|---|---|
| `.md` | Human-readable markdown with all picks, reasoning, risk flags, acca legs |
| `.json` | Machine-readable full data including the saved model feature values, odds, edge, stake |
| `.xlsx` | Excel tracker with Win/Loss/Push dropdown and auto P&L formula |

---

## Part 3 â€” The Afternoon Pipeline (`/check-movement`)

Run after morning picks are generated, typically 1â€“3 hours before first pitch. Purpose: catch line movement, confirm starters, flag injuries.

### Step 1 â€” Settle yesterday's updated picks

Before anything else, runs `record_results.py --variant updated` to settle yesterday's afternoon picks into `results_log_updated.csv` â€” keeping the A/B tracker current.

### Step 2 â€” Detect game states

Calls the MLB schedule API to classify every game as `NOT_STARTED`, `LIVE`, or `FINAL`.

**Critical rule:** Live and final games are completely frozen â€” their morning pick and stake are preserved as-is. Live odds are driven by score, inning, and pitching changes, not market signals about who will win. Recalculating edge against in-play odds would be meaningless and potentially dangerous.

### Step 3 â€” Fetch current (pregame) odds

Same Odds API call as the morning, but now reflects several hours of market movement. Odds are **archived** as `<date>_pregame_odds.json` for CLV calculation.

### Step 4 â€” Print the movement table

For each pregame game, shows:

```
GAME           PICK               AT PRED   NOW    MOVEMENT
HOU @ CLE      CLE -1.5          2.52      2.48   â†“ -0.04  (money in â€” sharp support)  [BET]
PHI @ CHC      CHC ML            1.80      1.75   â†“ -0.05  (money in â€” sharp support)  [BET]
```

Arrow direction is from the **bettor's perspective on the picked side**:
- â†“ (odds shortening) = money came in on your side = confirmation / sharp support
- â†‘ (odds drifting out) = market moving away = edge may have shrunk
- Warning issued if a BET's odds shortened >0.10 against you (sharp money opposing)

### Step 5 â€” Check injuries

Calls `MLB /transactions` API for the past 7 days. Any IL placements for teams playing today are flagged â€” so you know before placing if a key player went down.

### Step 6 â€” Confirm starting pitchers and lineups

For every BET game, every TBD-SP skip, and every near-threshold skip (edge between -3% and +1%), the script fetches the live game feed from the MLB boxscore API to get:
- Confirmed starting pitcher names
- Full batting orders (if posted)

If a confirmed SP differs from what the model used at prediction time, the game is flagged for a **model re-run**.

### Step 7 â€” Re-run model for changed/TBD starters

If any game had a TBD SP or a SP change confirmed, the model is re-run from scratch with the correct pitcher:
1. Fetch current pitcher stats for the new name
2. Rebuild the saved moneyline feature vector with the correct blended ERA/WHIP/K9
3. Re-predict home win probability
4. New probability feeds into Step 8

### Step 8 â€” Recalculate edges with current odds

For every pregame game, edge is recalculated using:
- Model probability (original or re-run)
- Current afternoon odds (not morning odds)

New stake tiers are computed against the **updated bankroll** (latest value from `results_log_updated.csv`).

**Skips that now qualify ("AFTERNOON RE-EVALUATIONS"):** If a morning SKIP now has edge â‰¥ 1% at afternoon odds, it is flagged as a new BET. This is printed separately in the output.

### Step 9 â€” Save updated files

`<date> Predictions (Updated).json`, `.md`, `.xlsx` are written. These are tracked separately by `record_results.py --variant updated` and settle into `results_log_updated.csv` â€” allowing an A/B comparison of morning vs afternoon pick performance over time.

### Step 10 â€” Deploy to Firebase

After both commands, the React app is rebuilt and deployed:
```
npm run build â†’ copies mlb/predictions into site/ â†’ firebase deploy
```
Live at: **https://diamondedge-7c220.web.app**

---

## Part 4 â€” Settling Results (`record_results.py`)

Runs as part of the next day's `/generate-mlb-predictions`. For each settled game:
1. Fetches final scores from the MLB API
2. Matches picks to results (Win/Loss/Push)
3. Calculates P&L (Win: stake Ã— (odds âˆ’ 1), Loss: âˆ’stake, Push: 0)
4. Updates bankroll
5. Calculates **CLV** (Closing Line Value) â€” did the morning odds beat the closing line?
6. Writes to `results_log.csv` and `results_log_updated.csv`

---

## Summary â€” Data Flow

```
MLB Stats API (free)          The Odds API (key required)
       â”‚                               â”‚
       â–¼                               â–¼
  Completed 2026 games          Live UK bookmaker odds
  Today's schedule              (Paddy Power, Sky Bet, etc.)
  Probable pitchers
  2026 pitcher stats (ERA/WHIP/K9)
       â”‚                               â”‚
       â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                      â–¼
              saved feature vector
              (rolling form + SP + park factor)
                      â”‚
                      â–¼
         Logistic Regression (2025-trained)
                      â”‚
                      â–¼
              P(home win), P(away win)
                      â”‚
                      â–¼
         Edge = Model prob âˆ’ Implied prob
                      â”‚
                      â–¼
         Stake tier â†’ BET or SKIP
                      â”‚
                      â–¼
         .md / .json / .xlsx / Firebase
```

---

## Key Numbers (Current Season)

- **Model:** Logistic regression, saved 76-feature moneyline contract, trained on 2025 full season
- **BET threshold:** Edge â‰¥ 3% (practical), â‰¥ 1% (code floor)
- **Max stake:** 5% of bankroll (edge â‰¥ 20%)
- **Bankroll:** EUR 618.96 (started EUR 500, +23.8% as of 2026-04-21)
- **Win rate:** 53.3% on 60 settled bets
- **ROI:** +12.53% on staked amount

