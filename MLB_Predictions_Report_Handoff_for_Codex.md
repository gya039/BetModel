# MLB Predictions Report Export Handoff for Codex

## Goal

Update the existing MLB betting model so that `predict_today.py` continues to output the JSON used by `mlb.html`, **and also writes a daily markdown predictions report file** into a dated folder structure.

This handoff should give Codex everything needed to implement the feature cleanly without breaking the existing frontend workflow.

---

## Project Context

### Repo root
`E:/BettingModel/`

### Frontend dashboard
`E:/New Code/mlb.html`

---

## What This MLB App Does

This MLB extension has two main parts:

### 1) Python model pipeline
- pulls MLB data
- builds rolling features
- trains a moneyline model
- backtests it
- simulates a live 2026 bankroll
- generates per-game prediction JSON

### 2) Frontend dashboard
- standalone `mlb.html`
- pulls live MLB schedule/stats from MLB Stats API
- displays hardcoded model predictions from `predict_today.py`
- shows `Yesterday / Today / Tomorrow`
- refreshes live MLB schedule/scores every 60 seconds using MLB Stats API only

---

## Main MLB Files

### `E:/BettingModel/mlb/scripts/fetch_data.py`
Purpose:
- pulls 2025 MLB game schedule/results from MLB Stats API
- pulls pitcher season stats from MLB Stats API
- saves raw CSV files

Outputs:
- `mlb/data/raw/games_2025.csv`
- `mlb/data/raw/pitchers_2025.csv`

### `E:/BettingModel/mlb/scripts/preprocess.py`
Purpose:
- converts raw game data into model-ready rows
- builds rolling team features with no look-ahead bias

Key feature types:
- last 5 win %
- last 10 win %
- last 5 run differential
- last 10 run differential
- last 10 runs scored
- last 10 runs allowed

Output:
- `mlb/data/processed/games_processed.csv`

Important note:
- designed to avoid look-ahead in team rolling form

### `E:/BettingModel/mlb/scripts/model.py`
Purpose:
- trains the MLB moneyline model

Model:
- `LogisticRegression` from scikit-learn

Saved model:
- `mlb/models/moneyline_model.pkl`

Important export:
- `FEATURES`
- this is the ordered feature list used by training and prediction

Known result:
- test accuracy about `57.1%`
- AUC about `0.597`

### `E:/BettingModel/mlb/scripts/backtest.py`
Purpose:
- backtests the model on the 2025 season
- simulates staking and ROI

Output:
- `mlb/data/processed/backtest_bets.csv`

Known result:
- around `+19.6% ROI` on the described demo backtest

### `E:/BettingModel/mlb/scripts/season_2026.py`
Purpose:
- simulates live 2026 betting performance
- bankroll-style tracking for ongoing season

Known setup:
- bankroll starts at `EUR 500`
- confidence-based staking tiers

Known reported result:
- about `EUR 500 -> EUR 1,563`

### `E:/BettingModel/mlb/scripts/predict_today.py`
Purpose:
- main daily prediction generator
- produces JSON for embedding into `mlb.html`

What it does:
1. loads all completed 2026 games up to the previous day
2. builds current rolling team state
3. fetches target-date upcoming games and probable pitchers
4. loads the trained 2025 model
5. fetches bookmaker odds from The Odds API
6. compares model probability vs market implied probability
7. outputs per-game JSON

Usage:
```bash
python mlb/scripts/predict_today.py
python mlb/scripts/predict_today.py --date 2026-04-15
```

---

## Data / Model Files

### `E:/BettingModel/mlb/data/raw/games_2025.csv`
- raw historical MLB games

### `E:/BettingModel/mlb/data/raw/pitchers_2025.csv`
- raw pitcher season stats

### `E:/BettingModel/mlb/data/processed/games_processed.csv`
- processed feature table for training

### `E:/BettingModel/mlb/data/processed/backtest_bets.csv`
- logged bets from backtest

### `E:/BettingModel/mlb/models/moneyline_model.pkl`
- pickled trained model and scaler

---

## Frontend File

### `E:/New Code/mlb.html`
Purpose:
- standalone MLB dashboard page
- fetches schedule/stats from MLB Stats API
- renders game cards, probable pitchers, score/status, and model panel

Important behavior:
- `MODEL_PREDICTIONS` is hardcoded near the top
- this gets pasted from `predict_today.py` output
- page itself does not call The Odds API
- this protects The Odds API free-tier request count

Current UI behavior:
- tabs are `Yesterday / Today / Tomorrow`
- live scores/status refresh every 60 seconds
- refresh uses MLB Stats API only
- Odds API is not touched by frontend refresh

---

## APIs Used

### 1) MLB Stats API
Base URL:
`https://statsapi.mlb.com/api/v1`

Used for:
- schedule
- game status
- linescore
- probable pitchers
- pitcher stats
- hitting stats
- team records
- series game number / games in series

This is the live data source for:
- `fetch_data.py`
- `predict_today.py`
- `mlb.html`

Typical endpoints used:
- `/schedule`
- `/stats`
- `/people/{id}/stats`

Important note:
- free
- no API key needed

### 2) The Odds API
Base URL:
`https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/`

Used for:
- moneyline odds
- runline/spread odds
- bookmaker count
- implied probabilities
- best available bookmaker price

API key:
- loaded from `.env`
- variable name: `ODDS_API_KEY`

Current logic:
- request regions: `uk,us`
- bookmaker preference:
  1. `paddypower`
  2. `skybet`
  3. `boylesports`
  4. any other UK bookmaker returned
  5. any remaining bookmaker returned

Important note:
- if odds are missing, script now leaves them missing
- no fake fallback odds should be used

---

## Environment / Config Variables

### Repo `.env`
At repo root:
`E:/BettingModel/.env`

Known MLB-related variable:
- `ODDS_API_KEY=...`

Other repo systems may also use their own env vars, but for MLB the important one is:
- `ODDS_API_KEY`

---

## Important Variables In `predict_today.py`

### Core constants
- `MODEL_DIR`
- `MLB`
- `SEASON`
- `FILL_ERA`
- `FILL_WHIP`
- `FILL_K9`

Meaning:
- `MODEL_DIR`: where the trained model is loaded from
- `MLB`: MLB Stats API base URL
- `SEASON`: active season for live simulation/predictions
- `FILL_*`: fallback values when pitcher data is missing

### Odds config
- `ODDS_TEAM_MAP`
- `PREFERRED_BOOKMAKERS`
- `UK_BOOKMAKERS`

Meaning:
- `ODDS_TEAM_MAP`: maps Odds API full team names to MLB abbreviations
- `PREFERRED_BOOKMAKERS`: top priority bookmakers
- `UK_BOOKMAKERS`: full UK bookmaker key set used for preference ordering

### Main functions
- `get(url, params=None)`
- `fetch_completed(today)`
- `fetch_upcoming(target_date)`
- `fetch_pitcher_stats()`
- `build_team_state(completed)`
- `rolling(hist, n)`
- `pitcher_stat(pitchers, pid, col, fallback)`
- `predict(model, scaler, feat_vec)`
- `build_features(game, team_state, pitchers)`
- `stake_tier(edge)`
- `fetch_mlb_odds(target_date)`

What they do:
- `fetch_completed`: gets completed 2026 games through prior day
- `fetch_upcoming`: gets target-date games
- `fetch_pitcher_stats`: gets current season pitcher stats
- `build_team_state`: builds rolling state from finished games
- `rolling`: computes rolling features
- `build_features`: creates model feature vector for one game
- `stake_tier`: converts edge into stake tier
- `fetch_mlb_odds`: gets bookmaker prices from The Odds API

### Output fields per game
`predict_today.py` outputs JSON entries like:
- `gamePk`
- `homeTeam`
- `awayTeam`
- `homeAbbr`
- `awayAbbr`
- `homeProb`
- `awayProb`
- `pickSide`
- `modelProb`
- `edge`
- `stake`
- `hasRolling`
- `awayMl`
- `homeMl`
- `awayRl`
- `homeRl`
- `awayImplied`
- `homeImplied`
- `pickOdds`
- `marketImplied`
- `bookCount`
- `hasOdds`
- `homeSpName`
- `awaySpName`
- `homeSpEra`
- `awaySpEra`
- `homeSpWhip`
- `awaySpWhip`
- `homeSpW`
- `homeSpL`
- `awaySpW`
- `awaySpL`
- `homeL10WP`
- `awayL10WP`
- `homeL10RD`
- `awayL10RD`
- `eraDiff`

---

## Important Variables In `mlb.html`

### Config constants
- `MLB`
- `TODAY`
- `START_DAY`
- `SEASON`
- `DAYS`
- `RECENT`
- `MIN_PA`

### Main frontend state
- `MODEL_PREDICTIONS`
- `schedule`
- `seasonMap`
- `recentMap`
- `homeAwayMap`
- `pitcherCache`
- `recentOK`
- `hasSplitsData`
- `activeDay`
- `detailGame`
- `gamesIndex`
- `teamFilters`

### Important frontend functions
- `apiFetch(url)`
- `fetchSchedule()`
- `fetchSeasonHitting()`
- `fetchRecentHitting()`
- `fetchHomeAwaySplits()`
- `fetchPitcherStats(id)`
- `loadPitchersForDay(dayIdx)`
- `renderGameCard(game)`
- `renderModelCard(gamePk, compact)`
- `renderTabs()`
- `renderGames(dayIdx)`
- `switchDay(i)`
- `refreshScheduleData()`
- `init()`

---

## Feature Logic

The MLB model uses about 23 features.

Main categories:
- home last 10 win %
- away last 10 win %
- home last 5 win %
- away last 5 win %
- home last 10 run differential
- away last 10 run differential
- home last 5 run differential
- away last 5 run differential
- home last 10 runs scored
- away last 10 runs scored
- home last 10 runs allowed
- away last 10 runs allowed
- home starter ERA
- away starter ERA
- home starter WHIP
- away starter WHIP
- home starter K/9
- away starter K/9
- win pct diff
- run diff diff
- ERA diff
- WHIP diff
- K/9 diff

Important bias note:
- pitcher ERA currently uses season-to-date totals
- this has a small look-ahead-ish imperfection depending on use timing
- team rolling form is intended to be strictly no look-ahead

---

## Betting / Staking Logic

### Current logic
- if edge `< 3%`: `PASS`
- `3% to <6%`: `EUR 10`
- `6% to <10%`: `EUR 10`
- `10% to <15%`: `EUR 15`
- `15%+`: `EUR 25`

The JSON stores this as:
- `stake.pct`
- `stake.eur`
- `stake.label`

The important edge formula is:
```text
edge = model win probability - market implied probability
```

---

## Daily Workflow

### To generate predictions
From repo root:
```bash
python mlb/scripts/predict_today.py
```

Or for a specific date:
```bash
python mlb/scripts/predict_today.py --date 2026-04-15
```

### To update frontend
1. run `predict_today.py`
2. copy JSON stdout
3. paste into `MODEL_PREDICTIONS` in `E:/New Code/mlb.html`

Important:
- frontend does not call Odds API
- only the Python script does

---

## Current Known Issues / Gotchas

1. `predict_today.py` may get no odds for some future slates
- this depends on what The Odds API is actually returning at query time
- tomorrow markets may not always be available yet

2. bookmaker coverage is inconsistent
- not every bookmaker key appears for every slate/sport
- `paddypower` often appears
- `skybet` / `boylesports` may not always appear in returned MLB payloads

3. frontend predictions are static until manually refreshed
- live schedule and scores refresh automatically
- model prediction JSON does not auto-regenerate

4. `mlb.html` is a standalone file
- not a React app
- not a backend app
- just a self-contained HTML/CSS/JS dashboard

---

## High-Level App Structure

```text
E:/BettingModel/
├── .env
├── requirements.txt
├── mlb/
│   ├── __init__.py
│   ├── data/
│   │   ├── raw/
│   │   │   ├── games_2025.csv
│   │   │   └── pitchers_2025.csv
│   │   └── processed/
│   │       ├── games_processed.csv
│   │       └── backtest_bets.csv
│   ├── models/
│   │   └── moneyline_model.pkl
│   └── scripts/
│       ├── __init__.py
│       ├── fetch_data.py
│       ├── preprocess.py
│       ├── model.py
│       ├── backtest.py
│       ├── season_2026.py
│       └── predict_today.py
└── other sports modules...

E:/New Code/
└── mlb.html
```

---

## One-Sentence Summary

This app is a Python MLB moneyline betting model that uses MLB Stats API for game/team/pitcher data, The Odds API for bookmaker prices, outputs daily prediction JSON, and displays it in a standalone `mlb.html` dashboard that refreshes live scores from MLB Stats API without consuming Odds API requests.

---

# Requested Change

## Main objective

Modify `E:/BettingModel/mlb/scripts/predict_today.py` so it does **both**:

1. keeps printing the JSON prediction output used by `mlb.html`
2. writes a **daily markdown report file** to disk

The markdown file should contain:
- every game on the slate
- a bet recommendation or skip for every game
- stake sizing based on a fixed €500 bankroll using only 1% to 5%
- concise reasons for the edge
- concise risk notes
- a summary markdown table at the end

---

## New output folder and file naming

Create report files under:

`E:/BettingModel/mlb/predictions/<Month> Predictions/`

Examples:
- `E:/BettingModel/mlb/predictions/April Predictions/April 14th 2026 Predictions.md`
- `E:/BettingModel/mlb/predictions/April Predictions/April 15th 2026 Predictions.md`

Rules:
- create the month folder automatically if missing
- derive the file name from the target date
- use `--date` when provided
- otherwise use today’s target date logic already used by `predict_today.py`

---

## Critical implementation requirements

### 1) Do not break current JSON output
The JSON printed to stdout for `mlb.html` must continue to work.

### 2) Use real odds only
Keep using odds from The Odds API only.
Do not fabricate fallback odds.

If odds are missing, mark the game:
- `SKIP – Missing odds data`

### 3) Every game must get an answer
For every game on the slate:
- either recommend a bet
- or explicitly skip it

### 4) Edge threshold
If edge is below the minimum threshold, mark:
- `SKIP – No clear edge`

### 5) New bankroll staking logic
Use a fixed bankroll of `€500`.

Allowed stake sizes:
- 1% = €5
- 2% = €10
- 3% = €15
- 4% = €20
- 5% = €25

No bet should exceed 5%.

---

## Recommended replacement stake function

Replace the old flat-euro staking logic with a bankroll-based function similar to this:

```python
def stake_tier(edge, bankroll=500):
    if edge < 0.03:
        return {"pct": 0, "eur": 0, "label": "PASS"}
    elif edge < 0.06:
        pct = 0.01
    elif edge < 0.10:
        pct = 0.02
    elif edge < 0.15:
        pct = 0.03
    elif edge < 0.20:
        pct = 0.04
    else:
        pct = 0.05

    eur = round(bankroll * pct, 2)
    return {
        "pct": int(pct * 100),
        "eur": eur,
        "label": f"{int(pct * 100)}% (€{eur:.2f})"
    }
```

This yields:
- 1% = €5
- 2% = €10
- 3% = €15
- 4% = €20
- 5% = €25

---

## Required decision logic

Each game should clearly resolve to:
- `BET`
- `SKIP`

Suggested logic:
- `BET` if edge >= 3% and odds exist
- `SKIP` otherwise

Suggested examples:
- `SKIP – No clear edge`
- `SKIP – Missing odds data`

---

## Required per-game markdown content

For each game, the report should include:

```text
━━━━━━━━━━━━━━━━━━━━━━━
⚾ GAME: [Away Team] vs [Home Team]

📊 PICK: [Team / Bet Type] or SKIP
💰 STAKE: [X]% (€Y)
📈 ODDS: [Decimal odds or N/A]

🔥 CONFIDENCE: [1-10]

🧠 EDGE:
- short reason 1
- short reason 2
- short reason 3

📉 RISKS:
- short risk note

Decision: BET or SKIP
━━━━━━━━━━━━━━━━━━━━━━━
```

Notes:
- keep reasons concise
- use only information already available in the prediction row / model output
- do not invent unsupported narratives

---

## Suggested auto-generated edge reasons

Use existing fields from each prediction row to generate concise reasons, such as:
- model edge over implied probability
- stronger L10 win %
- better L10 run differential
- better starting pitcher ERA
- better WHIP profile

Suggested helper approach:

```python
def build_reasons(row):
    reasons = []

    if row["edge"] >= 0.03:
        reasons.append(
            f'Model edge of {row["edge"]*100:.1f}% versus market implied probability.'
        )

    if row["pickSide"] == row["homeAbbr"]:
        if row["homeL10WP"] > row["awayL10WP"]:
            reasons.append("Home team has stronger recent L10 win rate.")
        if row["homeL10RD"] > row["awayL10RD"]:
            reasons.append("Home team has better recent run differential.")
        if row["homeSpEra"] < row["awaySpEra"]:
            reasons.append("Home starter has the better ERA matchup.")
        if row["homeSpWhip"] < row["awaySpWhip"]:
            reasons.append("Home starter has the better WHIP profile.")
    else:
        if row["awayL10WP"] > row["homeL10WP"]:
            reasons.append("Away team has stronger recent L10 win rate.")
        if row["awayL10RD"] > row["homeL10RD"]:
            reasons.append("Away team has better recent run differential.")
        if row["awaySpEra"] < row["homeSpEra"]:
            reasons.append("Away starter has the better ERA matchup.")
        if row["awaySpWhip"] < row["homeSpWhip"]:
            reasons.append("Away starter has the better WHIP profile.")

    if not reasons:
        reasons.append("No strong statistical edge beyond model price value.")

    return reasons[:3]
```

---

## Suggested risk note helper

Suggested helper approach:

```python
def build_risk(row):
    risks = []

    if abs(row["homeProb"] - row["awayProb"]) < 0.06:
        risks.append("Model sees this as a fairly close game.")

    if row["pickSide"] == row["homeAbbr"]:
        if row["homeSpEra"] > row["awaySpEra"]:
            risks.append("Picked side does not have the better ERA matchup.")
    else:
        if row["awaySpEra"] > row["homeSpEra"]:
            risks.append("Picked side does not have the better ERA matchup.")

    if row["edge"] < 0.05:
        risks.append("Edge is modest, so variance risk is higher.")

    if not risks:
        risks.append("Baseball variance is high even with a real edge.")

    return risks[0]
```

---

## Suggested confidence mapping

Confidence should remain simple and readable.
A rough mapping based on edge is fine.

Example:
```python
confidence = min(10, max(1, int(round(row["edge"] * 100 / 2))))
```

Codex can improve this as needed, but keep it bounded from 1 to 10.

---

## Required summary table at end of file

At the bottom of the markdown file, output a table like:

```md
| Game | Pick | Odds | Stake % | Stake € | Return € | Profit € | Decision |
|---|---|---:|---:|---:|---:|---:|---|
| NYY @ BOS | Yankees ML | 1.91 | 3% | €15.00 | €28.65 | €13.65 | BET |
| LAD @ SD | SKIP | N/A | 0% | €0.00 | €0.00 | €0.00 | SKIP |
```

Rules:
- include **all** games, even skipped ones
- `Return` = gross return = `stake × decimal odds`
- `Profit` = `return - stake`
- for skipped games, return and profit should be `€0.00`

---

## Suggested date formatting helpers

Codex should add helpers for month folder and ordinal day naming.

Suggested implementation:

```python
from datetime import datetime

def ordinal(n):
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

def format_prediction_date(target_date):
    dt = datetime.strptime(target_date, "%Y-%m-%d")
    month_folder = f"{dt.strftime('%B')} Predictions"
    file_name = f"{dt.strftime('%B')} {ordinal(dt.day)} {dt.year} Predictions.md"
    return month_folder, file_name
```

---

## Suggested markdown writer

Codex can implement a helper like this:

```python
from pathlib import Path

def write_markdown_report(predictions, target_date, bankroll=500):
    month_folder, file_name = format_prediction_date(target_date)

    out_dir = Path("E:/BettingModel/mlb/predictions") / month_folder
    out_dir.mkdir(parents=True, exist_ok=True)

    out_file = out_dir / file_name
    lines = []

    pretty_date = file_name.replace(".md", "")
    lines.append(f"# {pretty_date}")
    lines.append("")
    lines.append(f"**Bankroll:** €{bankroll:.2f}")
    lines.append("")

    for row in predictions:
        game = f'{row["awayTeam"]} vs {row["homeTeam"]}'
        has_odds = bool(row.get("hasOdds"))
        edge = row.get("edge", 0.0)
        decision = "BET" if has_odds and edge >= 0.03 and row["stake"]["eur"] > 0 else "SKIP"

        if not has_odds:
            pick_text = "SKIP – Missing odds data"
        elif decision == "SKIP":
            pick_text = "SKIP – No clear edge"
        else:
            pick_text = row["pickSide"]

        odds = row.get("pickOdds")
        odds_text = f"{odds:.2f}" if isinstance(odds, (int, float)) else "N/A"

        reasons = build_reasons(row)
        risk = build_risk(row)
        confidence = min(10, max(1, int(round(edge * 100 / 2))))

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"⚾ GAME: {game}")
        lines.append("")
        lines.append(f"📊 PICK: {pick_text}")
        lines.append(f'💰 STAKE: {row["stake"]["pct"]}% (€{row["stake"]["eur"]:.2f})')
        lines.append(f"📈 ODDS: {odds_text}")
        lines.append("")
        lines.append(f"🔥 CONFIDENCE: {confidence}")
        lines.append("")
        lines.append("🧠 EDGE:")
        for reason in reasons:
            lines.append(f"- {reason}")
        lines.append("")
        lines.append("📉 RISKS:")
        lines.append(f"- {risk}")
        lines.append("")
        lines.append(f"Decision: {decision}")
        lines.append("")

    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Game | Pick | Odds | Stake % | Stake € | Return € | Profit € | Decision |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---|")

    for row in predictions:
        game = f'{row["awayAbbr"]} @ {row["homeAbbr"]}'
        has_odds = bool(row.get("hasOdds"))
        edge = row.get("edge", 0.0)
        decision = "BET" if has_odds and edge >= 0.03 and row["stake"]["eur"] > 0 else "SKIP"

        if not has_odds:
            pick_text = "SKIP – Missing odds data"
        elif decision == "SKIP":
            pick_text = "SKIP – No clear edge"
        else:
            pick_text = row["pickSide"]

        odds = row.get("pickOdds")
        odds_text = f"{odds:.2f}" if isinstance(odds, (int, float)) else "N/A"

        stake_pct = row["stake"]["pct"]
        stake_eur = row["stake"]["eur"]

        if decision == "BET" and isinstance(odds, (int, float)):
            gross_return = round(stake_eur * odds, 2)
            profit = round(gross_return - stake_eur, 2)
        else:
            gross_return = 0.00
            profit = 0.00

        lines.append(
            f"| {game} | {pick_text} | {odds_text} | {stake_pct}% | €{stake_eur:.2f} | €{gross_return:.2f} | €{profit:.2f} | {decision} |"
        )

    out_file.write_text("\n".join(lines), encoding="utf-8")
    return out_file
```

---

## Important recommendation

The actual betting logic should remain in Python, not in Claude/Codex-generated prose.

Reason:
- `predict_today.py` already has the real model probabilities
- it already fetches real odds
- it already computes edge
- it already has the correct data context

So Codex should enhance the Python script to export a report, rather than inventing picks independently.

---

## Acceptance Criteria

Codex implementation is complete only if all of the following are true:

1. Running:
```bash
python mlb/scripts/predict_today.py
```
still prints valid JSON for the frontend.

2. Running:
```bash
python mlb/scripts/predict_today.py --date 2026-04-15
```
creates a markdown file like:
`E:/BettingModel/mlb/predictions/April Predictions/April 15th 2026 Predictions.md`

3. The markdown file includes every game on the slate.

4. Every game is either:
- a bet recommendation
- or a skip with a reason

5. Missing odds never produce fake prices.

6. Stake sizing uses only:
- 0%
- 1%
- 2%
- 3%
- 4%
- 5%

7. Stake euro amounts are correct for a €500 bankroll.

8. The summary table is included and contains:
- Game
- Pick
- Odds
- Stake %
- Stake €
- Return €
- Profit €
- Decision

9. Existing `mlb.html` integration remains unchanged and usable.

---

## Direct instruction for Codex

Open `E:/BettingModel/mlb/scripts/predict_today.py` and modify it so that, in addition to printing the JSON prediction output used by `mlb.html`, it also writes a daily markdown report file.

Requirements:
- Save the report under `E:/BettingModel/mlb/predictions/<Month> Predictions/`
- File names must be like `April 14th 2026 Predictions.md`
- Use the target date passed to `--date`, or today if no date is passed
- Cover every game on the slate
- If edge is below threshold or odds are missing, mark the game:
  `SKIP – No clear edge`
  or
  `SKIP – Missing odds data`
- Keep using real odds from The Odds API only
- Do not fabricate fallback odds
- Change staking logic to use a €500 bankroll with 1% to 5% stake sizing only:
  - 1% = €5
  - 2% = €10
  - 3% = €15
  - 4% = €20
  - 5% = €25
- Include for each game:
  - matchup
  - pick
  - odds
  - stake percent
  - stake euro
  - confidence
  - short edge reasons
  - short risk note
  - decision BET or SKIP
- At the end of the markdown file, add a markdown table with columns:
  `Game | Pick | Odds | Stake % | Stake € | Return € | Profit € | Decision`

Return means gross return = stake × decimal odds.
Profit means gross return - stake.

Keep the JSON structure used by `mlb.html` working.
Do not break the current frontend output.
