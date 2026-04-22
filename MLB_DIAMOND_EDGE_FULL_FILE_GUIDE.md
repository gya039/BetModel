# MLB And Diamond Edge Full File Guide

Generated on 2026-04-22 for `E:\BettingModel`.

This document explains the files used by the MLB prediction pipeline, the `/generate-mlb-predictions` workflow, the `/check-movement` workflow, and the Diamond Edge web app.

Important: no `.env.local` file exists anywhere in this repo at the time this was written. The repo has `.env` and `.env.example`. The live secret values from `.env` are included near the end because this file was requested as a complete local handoff.

## 1. What The System Is

Diamond Edge is a local MLB betting model plus a React dashboard.

The Python side lives under `mlb/`. It fetches MLB schedule, scores, pitchers, bullpen proxies, and betting odds; loads the saved moneyline model; calculates probabilities, market edge, stake size, single picks, and accumulators; writes daily prediction reports; and settles results.

The web app lives under `diamond-edge/`. It reads the generated files from `mlb/predictions/`, displays today's picks, movement, results, accumulators, archive pages, and a local Run Center that can trigger the Python scripts through `serve.js`.

## 2. Daily Commands

### `/generate-mlb-predictions`

Operationally, this maps to:

```bash
python mlb/scripts/record_results.py --date <yesterday>
python mlb/scripts/predict_today.py --date <today>
```

What it does:

1. Settles yesterday's original morning picks into `mlb/predictions/results_log.csv`.
2. Builds current rolling team state from completed 2026 games.
3. Fetches today's MLB schedule and probable starters.
4. Fetches current pitcher and bullpen inputs.
5. Loads `mlb/models/moneyline_model.pkl`.
6. Fetches current bookmaker prices from The Odds API.
7. Archives the morning odds snapshot.
8. Calculates moneyline probability, no-vig market probability, edge, decision, and stake.
9. Builds accumulator suggestions.
10. Writes the daily `.json`, `.md`, `.xlsx`, `.csv`, and `.html` report files where supported by the script.

Main file: `mlb/scripts/predict_today.py`.

### `/check-movement`

Operationally, this maps to:

```bash
python mlb/scripts/check_movement.py --date <today>
```

What it does:

1. Reads the morning prediction JSON for the date.
2. Fetches current odds from The Odds API.
3. Archives the pregame odds snapshot.
4. Checks MLB game state so live and final games stay frozen.
5. Checks recent MLB transactions for injury flags.
6. Checks confirmed lineups and starting pitchers.
7. Re-runs affected games when a TBD or changed starter is found.
8. Recalculates edge and stake using afternoon odds.
9. Writes `(Updated)` prediction files.
10. Keeps original and updated performance separate through `results_log.csv` and `results_log_updated.csv`.

Main file: `mlb/scripts/check_movement.py`.

## 3. High-Level Data Flow

```text
MLB Stats API
  -> historical games, live schedule, scores, probable starters, pitcher stats, transactions

The Odds API
  -> bookmaker moneyline and spread odds

mlb/scripts/fetch_data.py
  -> mlb/data/raw/*.csv

mlb/scripts/preprocess.py
  -> mlb/data/processed/games_processed.csv

mlb/scripts/model.py
  -> mlb/models/moneyline_model.pkl

mlb/scripts/predict_today.py
  -> mlb/predictions/<Month> Predictions/<Day Folder>/<date> Predictions.*
  -> mlb/predictions/odds_archive/<date>_morning_odds.json

mlb/scripts/check_movement.py
  -> mlb/predictions/<Month> Predictions/<Day Folder>/<date> Predictions (Updated).*
  -> mlb/predictions/odds_archive/<date>_pregame_odds.json

mlb/scripts/record_results.py
  -> mlb/predictions/results_log.csv
  -> mlb/predictions/results_log_updated.csv
  -> mlb/predictions/accumulators_log.csv

diamond-edge/
  -> reads generated prediction and result files
  -> displays picks, movement, archive, tracker, and run controls
```

## 4. Root Files Used By MLB Or Diamond Edge

### `AGENTS.md`

Codex operating guide for this repo. It defines the project structure, daily prediction commands, slash command meaning, stake tiers, bankroll, and the rule that betting logic should not be edited casually.

### `CLAUDE.md`

Similar repo guide for Claude. It describes the same MLB pipeline and command usage.

### `HOW_IT_WORKS.md`

Technical explanation of the Diamond Edge MLB model and daily workflow. It explains the model, feature groups, morning pipeline, afternoon movement pipeline, result settlement, bankroll, and data flow.

### `MLB_MODEL_UPGRADE_SUMMARY.md`

Upgrade notes for the current model contract. It documents the richer 76-feature model, bullpen features, pitcher features, calibration, diagnostics, staking, smoke tests, and the current run-line behavior.

### `MLB_Betting_Model_Audit.md`

Audit notes for the MLB betting model. It references the two-phase workflow, model risks, movement checks, and improvements.

### `DAILY_PICKS_LOG.md`

Manual daily log of Diamond Edge MLB picks and observations.

### `requirements.txt`

Python dependency list for the overall betting repo. MLB uses `pandas`, `numpy`, `scikit-learn`, `scipy`, `requests`, `openpyxl` indirectly through scripts, and related packages.

Current content:

```text
betfairlightweight==2.23.2
pandas>=2.2
numpy>=1.26
scikit-learn>=1.6
scipy>=1.14
requests>=2.31
python-dotenv>=1.0
sqlalchemy>=2.0
psycopg2-binary>=2.9
streamlit>=1.31
matplotlib>=3.8
seaborn>=0.13
jupyter>=1.0
```

### `serve.js`

Local Diamond Edge static server.

What it does:

- Serves the built React app from `site/`.
- Serves live MLB prediction data from `/mlb/...`.
- Exposes `GET /api/status` for Run Center status.
- Exposes `POST /api/run/settle`, `POST /api/run/generate`, and `POST /api/run/movement`.
- Streams script logs through `GET /api/run/stream/<runId>`.
- Exposes `GET /api/mlb-status?date=YYYY-MM-DD` to proxy MLB game status.
- Uses `RUN_TOKEN`, defaulting to `de-local-run`.
- Uses `PYTHON`, defaulting to `python`.
- Runs on `PORT`, defaulting to `4002`.

Run it with:

```bash
node serve.js
```

### `start-diamond-edge.bat`

Windows helper that changes directory to `E:\BettingModel` and starts `node serve.js`.

### `deploy.sh`

Firebase deployment helper.

What it does:

1. Runs `npm run build` inside `diamond-edge/`.
2. Copies `mlb/predictions` into `site/mlb/predictions`.
3. Runs `firebase deploy --only hosting`.
4. Prints the live Firebase URL.

### `firebase.json`

Firebase Hosting configuration. It serves the built app and applies cache headers for prediction JSON/CSV files.

### `.firebaserc`

Firebase project alias configuration.

### `.env`

Live local environment variables. `predict_today.py` and `check_movement.py` manually load this file from the repo root.

### `.env.example`

Template env file with placeholder credentials.

### `.env.local`

Not present. A repo-wide search found no `.env.local`.

## 5. MLB Folder Inventory

### `mlb/__init__.py`

Marks `mlb` as a Python package so imports like `from mlb.scripts.model import FEATURES` work.

### `mlb/scripts/__init__.py`

Marks `mlb/scripts` as a Python subpackage.

## 6. MLB Scripts

### `mlb/scripts/predict_today.py`

Main morning prediction entry point.

Key responsibilities:

- Loads `.env` from the repo root.
- Reads `ODDS_API_KEY`.
- Fetches completed 2026 games to build rolling form.
- Fetches today's upcoming games and probable pitchers.
- Fetches current pitcher stats.
- Builds bullpen quality and bullpen fatigue features.
- Builds the exact feature vector expected by the saved model.
- Loads `mlb/models/moneyline_model.pkl`.
- Optionally loads `mlb/models/spread_model.pkl` for run-line diagnostics.
- Fetches moneyline and spread odds from The Odds API.
- Selects best prices with helper functions from `odds_utils.py`.
- Calculates no-vig market probabilities where both sides are available.
- Calculates model edge.
- Applies stake tiers.
- Builds pick reasons, risks, confidence, and summaries.
- Writes markdown, JSON, CSV, Excel, and HTML reports.
- Builds accumulator combinations.
- Archives morning odds.

Important constants:

- `SEASON = 2026`
- `BANKROLL_EUR = 500.0`
- `ACCA_BANKROLL_EUR = 100.0`
- `USE_SPREAD_MODEL = True`
- `SPREAD_DEBUG` can be enabled through the environment.

Main outputs:

- `mlb/predictions/<Month> Predictions/<Month> <ordinal>/<Month> <ordinal> <year> Predictions.json`
- Matching `.md`, `.xlsx`, `.csv`, and `.html` where written.
- `mlb/predictions/odds_archive/<date>_morning_odds.json`

### `mlb/scripts/check_movement.py`

Afternoon/pregame movement entry point.

Key responsibilities:

- Loads `.env` from the repo root.
- Reads the morning prediction JSON.
- Fetches current odds from The Odds API.
- Compares morning odds against current odds.
- Detects whether games are not started, live, or final.
- Freezes live and final games.
- Fetches recent transactions and flags IL/injury movement.
- Fetches confirmed lineups and starters from MLB boxscore/feed endpoints.
- Re-runs games where a starter was TBD or changed.
- Reprices run-line diagnostics where applicable.
- Recalculates moneyline edge, stake, and decision using current odds.
- Saves updated JSON, markdown, and Excel outputs.

Main outputs:

- `mlb/predictions/... Predictions (Updated).json`
- `mlb/predictions/... Predictions (Updated).md`
- `mlb/predictions/... Predictions (Updated).xlsx`
- `mlb/predictions/odds_archive/<date>_pregame_odds.json`

### `mlb/scripts/record_results.py`

Settlement and bankroll tracker.

Key responsibilities:

- Finds original or updated prediction JSON for a date.
- Fetches final scores from MLB Stats API.
- Determines Win, Loss, Push, Pending, or Skip.
- Calculates P&L.
- Updates bankroll before/after each settled bet.
- Computes CLV from archived closing odds when available.
- Settles accumulator logs.
- Writes or rewrites result logs.
- Prints summaries and original vs updated comparisons.

Main outputs:

- `mlb/predictions/results_log.csv`
- `mlb/predictions/results_log_updated.csv`
- `mlb/predictions/accumulators_log.csv`

### `mlb/scripts/build_tracker_xlsx.py`

Excel tracker builder.

Key responsibilities:

- Updates each daily prediction workbook with final results.
- Builds the cumulative `results_log.xlsx`.
- Adds formatting, weekly summaries, formulas, P&L, bankroll, and conditional fills.
- Supports original and updated prediction workbooks.

Main output:

- `mlb/predictions/results_log.xlsx`

### `mlb/scripts/fetch_data.py`

Historical data fetcher.

Key responsibilities:

- Pulls completed 2025 regular-season MLB games.
- Pulls 2025 pitcher stats.
- Pulls pitcher game logs.
- Pulls team pitching proxy data for bullpen features.
- Writes raw CSV files used by preprocessing and training.

Main outputs:

- `mlb/data/raw/games_2025.csv`
- `mlb/data/raw/pitchers_2025.csv`
- `mlb/data/raw/pitcher_game_logs_2025.csv`
- `mlb/data/raw/bullpens_2025.csv`

### `mlb/scripts/preprocess.py`

Training feature engineering.

Key responsibilities:

- Reads raw games, pitchers, pitcher logs, and bullpen proxy CSVs.
- Builds rolling team form without look-ahead.
- Builds pitcher snapshots available before each game.
- Builds bullpen quality and fatigue features.
- Adds derived differentials.
- Adds ballpark factor.
- Writes the processed model training table.

Main output:

- `mlb/data/processed/games_processed.csv`

### `mlb/scripts/model.py`

Moneyline model trainer and predictor helper.

Key responsibilities:

- Loads `games_processed.csv`.
- Splits training and test data chronologically.
- Trains logistic regression with standard scaling.
- Applies calibration through `CalibratedClassifierCV` and `FrozenEstimator`.
- Evaluates accuracy, log loss, Brier score, and betting threshold behavior.
- Saves the model artifact.
- Provides `predict_moneyline(features)`.

Main output:

- `mlb/models/moneyline_model.pkl`

### `mlb/scripts/feature_utils.py`

Shared feature contract and feature helpers.

Key responsibilities:

- Defines fill values for missing pitcher and bullpen metrics.
- Defines `BALLPARK_FACTORS`.
- Defines pitcher, bullpen, rolling-form, and derived feature names.
- Exposes the canonical `FEATURES` list used by training and prediction.
- Parses innings pitched.
- Computes FIP-style values.
- Computes K-BB%.
- Blends low-IP pitcher metrics toward league-average fills.
- Builds pitcher and bullpen feature dictionaries.
- Adds derived differential columns.
- Aggregates bullpen proxy data from pitcher rows.

This file is important because it keeps the live prediction feature vector aligned with the trained model.

### `mlb/scripts/odds_utils.py`

Shared odds cleaning and pricing helpers.

Key responsibilities:

- Defines preferred and allowed bookmakers.
- Filters invalid decimal prices.
- Filters outlier prices.
- Ranks books so preferred UK books are used first.
- Picks best moneyline price.
- Picks best spread price for a point.
- Collects alternate spread options.
- Determines standard run-line points.
- Converts two-sided prices into no-vig probabilities.

Used by:

- `predict_today.py`
- `check_movement.py`

### `mlb/scripts/diagnostics.py`

Walk-forward model diagnostics.

Key responsibilities:

- Runs monthly chronological folds.
- Trains and calibrates fold models.
- Calculates log loss, Brier score, accuracy, calibration buckets, and ECE.
- Simulates thresholds and top-N slate strategies.
- Writes JSON and markdown diagnostics.

Main outputs:

- `mlb/predictions/diagnostics/walk_forward_diagnostics.json`
- `mlb/predictions/diagnostics/walk_forward_diagnostics.md`

### `mlb/scripts/spread_model.py`

Run-line/spread model trainer and diagnostic tool.

Key responsibilities:

- Trains a Ridge model to estimate home margin.
- Converts predicted margin to spread cover probabilities.
- Validates model quality using ECE and ROI-style diagnostics.
- Saves spread model and diagnostics.
- Provides `SpreadModel` methods used by prediction and movement repricing.

Main outputs:

- `mlb/models/spread_model.pkl`
- `mlb/models/spread_model_diagnostics.json`

### `mlb/scripts/rl_backtest.py`

Run-line backtest report builder.

Key responsibilities:

- Loads the saved spread model and holdout data.
- Evaluates run-line betting assumptions against historical or archived odds where available.
- Writes markdown and JSON reports.

Main outputs:

- `mlb/reports/rl_backtest_report.md`
- `mlb/reports/rl_backtest_report.json`

### `mlb/scripts/backtest.py`

Older/simple moneyline backtest harness.

Key responsibilities:

- Uses historical processed data and assumed favorite/underdog odds.
- Simulates edge betting with configurable bankroll and min edge.
- Prints summary statistics.
- Writes `backtest_bets.csv`.

Main output:

- `mlb/data/processed/backtest_bets.csv`

### `mlb/scripts/season_2026.py`

2026 season bootstrap and early-season simulator.

Key responsibilities:

- Fetches early 2026 games and pitcher stats.
- Builds basic rolling features.
- Simulates 2026 picks against the saved moneyline model.
- Used as a bootstrap/analysis script, not the main daily pipeline.

### `mlb/scripts/retrain_2026.py`

Retraining helper using 2026 data through a chosen end date.

Key responsibilities:

- Fetches 2026 completed games.
- Fetches 2026 pitchers and team pitching proxy data.
- Rebuilds processed features.
- Trains a model with a recent 2026 chronological split.
- Saves a refreshed model when used.

Use this carefully because it can change the model artifact used by daily predictions.

## 7. MLB Data Files

### `mlb/data/raw/games_2025.csv`

Historical game-level table.

Header:

```text
game_pk,game_date,away_team_id,away_team,home_team_id,home_team,away_score,home_score,home_win,away_sp_id,away_sp_name,home_sp_id,home_sp_name
```

### `mlb/data/raw/games_2026.csv`

2026 game-level table used for current-season bootstrapping/retraining.

### `mlb/data/raw/pitchers_2025.csv`

Historical pitcher stat table.

Header:

```text
pitcher_id,pitcher_name,team,era,whip,k9,fip,bb9,k_bb_pct,hr9,ip,is_left,wins,losses,k,walks,home_runs,batters_faced,games_started,games_pitched,saves,holds
```

### `mlb/data/raw/pitchers_2026.csv`

Current-season pitcher stat table.

### `mlb/data/raw/pitcher_game_logs_2025.csv`

Pitcher game logs used to create historical starter and bullpen state without look-ahead.

### `mlb/data/raw/bullpens_2025.csv`

Historical team bullpen proxy table.

### `mlb/data/raw/bullpens_2026.csv`

Current-season team bullpen proxy table.

### `mlb/data/processed/games_processed.csv`

Model training table. This is the key processed dataset.

It contains:

- Game metadata and final result.
- Rolling form features.
- Starter features.
- Bullpen features.
- Derived home-away differentials.
- Ballpark factor.

### `mlb/data/processed/backtest_bets.csv`

Output from `backtest.py`. Contains simulated historical bet rows.

## 8. MLB Model Files

### `mlb/models/moneyline_model.pkl`

Saved moneyline model artifact. Loaded by `predict_today.py`.

It contains the trained logistic regression/calibrated model, scaler, feature list, and metadata needed to generate home-win probabilities.

### `mlb/models/spread_model.pkl`

Saved run-line/spread model artifact. Used for diagnostics and potential run-line repricing.

### `mlb/models/spread_model_diagnostics.json`

Diagnostics from training/validating the spread model.

## 9. MLB Prediction And Result Files

### `mlb/predictions/results_log.csv`

Cumulative original morning-pick ledger.

Header:

```text
date,game_pk,home_team,away_team,pick_side,pick_team,pick_odds,stake_eur,decision,result,pnl,bankroll_before,bankroll_after
```

### `mlb/predictions/results_log_updated.csv`

Cumulative afternoon-updated-pick ledger. Same idea as `results_log.csv`, but for `(Updated)` predictions.

### `mlb/predictions/results_log.xlsx`

Formatted Excel tracker built from the result logs.

### `mlb/predictions/accumulators_log.csv`

Cumulative accumulator ledger.

Header:

```text
date,type,legs,combined_odds,stake,result,pnl
```

### `mlb/predictions/diagnostics/walk_forward_diagnostics.json`

Machine-readable walk-forward diagnostics.

### `mlb/predictions/diagnostics/walk_forward_diagnostics.md`

Human-readable walk-forward diagnostics.

### `mlb/predictions/odds_archive/*.json`

Archived odds snapshots for CLV and movement comparison.

Current files:

- `2026-04-19_morning_odds.json`
- `2026-04-19_pregame_odds.json`
- `2026-04-20_morning_odds.json`
- `2026-04-20_pregame_odds.json`
- `2026-04-21_morning_odds.json`
- `2026-04-21_pregame_odds.json`

### Daily Prediction Folders

Daily files live under:

```text
mlb/predictions/April Predictions/<April day folder>/
```

Current dated folders:

- `April 14th`
- `April 15th`
- `April 16th`
- `April 17th`
- `April 18th`
- `April 19th`
- `April 20th`
- `April 21st`

Each day currently has original prediction files and usually updated prediction files:

- `<date> Predictions.json`: machine-readable morning predictions.
- `<date> Predictions.md`: human-readable morning report and primary daily pick report.
- `<date> Predictions.xlsx`: daily Excel tracker.
- `<date> Predictions (Updated).json`: machine-readable afternoon movement output.
- `<date> Predictions (Updated).md`: human-readable movement/update report.
- `<date> Predictions (Updated).xlsx`: updated Excel tracker.

## 10. MLB Reports

### `mlb/reports/rl_backtest_report.md`

Human-readable run-line backtest summary.

### `mlb/reports/rl_backtest_report.json`

Machine-readable run-line backtest details.

## 11. Diamond Edge App Inventory

### `diamond-edge/package.json`

NPM package definition for the React/Vite app.

Scripts:

- `npm run dev`: starts Vite dev server.
- `npm run build`: builds the app into `site/`.
- `npm run preview`: previews the built app.

Runtime dependencies:

- `react`
- `react-dom`
- `chart.js`
- `react-chartjs-2`
- `framer-motion`
- `papaparse`

Dev dependencies:

- `vite`
- `@vitejs/plugin-react`
- `vite-plugin-pwa`

### `diamond-edge/package-lock.json`

Locked NPM dependency tree.

### `diamond-edge/vite.config.js`

Vite and PWA configuration.

What it does:

- Uses React plugin.
- Copies `mlb/predictions` into `site/mlb/predictions` after build.
- Serves `../mlb` as `/mlb` in dev.
- Configures service worker behavior.
- Uses `NetworkOnly` for prediction JSON and CSV files.
- Builds into `../site`.
- Runs dev server on port `5173`.

### `diamond-edge/index.html`

Vite HTML entry file for the React app.

### `diamond-edge/src/main.jsx`

React root entry point. Imports global CSS and renders `App`.

### `diamond-edge/src/App.jsx`

Top-level app controller.

What it does:

- Holds active tab state.
- Renders `AppShell`.
- Switches between Today, Results, Movement, Archive, Run Center, and About views.
- Wraps each panel with `PageErrorBoundary`.

### `diamond-edge/src/styles/global.css`

Global design system and app styling.

It defines:

- Colors and theme tokens.
- Layout.
- App shell.
- Bottom navigation.
- Pick cards.
- Movement cards.
- Result tables.
- Archive view.
- Run Center.
- Responsive behavior.

### Views

#### `diamond-edge/src/views/TodayView.jsx`

Main picks screen.

Reads predictions for a date, updated predictions when available, result state, live scores, and accumulator data. Displays summary stats, pick cards, accumulator cards, and run-line diagnostics.

#### `diamond-edge/src/views/MovementView.jsx`

Line movement screen.

Loads morning and updated prediction JSON for a date, computes movement, splits changed and unchanged picks, and renders `MovementCard` rows.

#### `diamond-edge/src/views/ResultsView.jsx`

Tracker screen.

Loads settled results and accumulator logs, displays bankroll chart, stats, singles table, and accumulator table.

#### `diamond-edge/src/views/ArchiveView.jsx`

Historical archive screen.

Checks which daily prediction files exist and lets the user jump to previous dates.

#### `diamond-edge/src/views/RunCenterView.jsx`

Local pipeline control screen.

It calls:

- `GET /api/status`
- `POST /api/run/settle`
- `POST /api/run/generate`
- `POST /api/run/movement`
- `GET /api/run/stream/<runId>`

This view is the web UI equivalent of running the settle, generate, and movement scripts locally.

#### `diamond-edge/src/views/AboutView.jsx`

Static explanation/about screen for Diamond Edge.

### Hooks

#### `diamond-edge/src/hooks/usePredictions.js`

Loads original and updated prediction JSON for a date. Maintains a cache so switching views does not constantly refetch the same date.

#### `diamond-edge/src/hooks/useMovement.js`

Loads morning and updated prediction JSON and computes movement rows by comparing prices, edge, stake, and decision.

#### `diamond-edge/src/hooks/useResults.js`

Loads result CSV files and today's/yesterday's prediction JSON. Normalizes rows, adds pending rows, computes stats, and exposes result history for tables/charts.

#### `diamond-edge/src/hooks/useAccumulators.js`

Loads `accumulators_log.csv` and recent prediction JSON, then computes accumulator stats.

#### `diamond-edge/src/hooks/useLiveScores.js`

Fetches live game status and score data from MLB Stats API.

### Components

#### `diamond-edge/src/components/AppShell.jsx`

Main app frame. Displays the current bankroll, page body, baseball cursor, and bottom navigation.

#### `diamond-edge/src/components/BottomNav.jsx`

Mobile-style bottom navigation tabs.

Tabs include picks, tracker/results, movement, archive, run center, and about depending on the app configuration.

#### `diamond-edge/src/components/DateNav.jsx`

Previous/today/next date controls.

#### `diamond-edge/src/components/PickCard.jsx`

Detailed card for one game prediction.

Shows:

- Teams and pick.
- Odds.
- Stake.
- Edge.
- Model probability.
- Market implied probability.
- Pitchers.
- Form.
- Confidence.
- Reasoning.
- Risk flags.
- Run-line diagnostic fields.

#### `diamond-edge/src/components/MovementCard.jsx`

Card for one movement comparison row. Shows pick odds movement, edge movement, stake/return change, decision change, and starter information.

#### `diamond-edge/src/components/ResultsTable.jsx`

Sortable/filterable table of settled and pending single bets.

#### `diamond-edge/src/components/BankrollChart.jsx`

Chart.js line chart for bankroll history.

#### `diamond-edge/src/components/AccumulatorCard.jsx`

Card for an accumulator generated for the current slate.

#### `diamond-edge/src/components/AccumulatorStatsCard.jsx`

Summary card for accumulator performance.

#### `diamond-edge/src/components/AccumulatorsTable.jsx`

Table of settled and pending accumulator results.

#### `diamond-edge/src/components/PremiumBaseball.jsx`

Baseball image/artwork component.

#### `diamond-edge/src/components/BaseballCursor.jsx`

Custom pointer/cursor behavior using baseball artwork.

#### `diamond-edge/src/components/PageErrorBoundary.jsx`

React error boundary for view-level crashes.

#### `diamond-edge/src/components/Skeleton.jsx`

Loading placeholder components.

#### `diamond-edge/src/components/StatChip.jsx`

Small stat display chip.

### Utils

#### `diamond-edge/src/utils/paths.js`

Builds prediction JSON paths from dates.

Important path formats:

```text
mlb/predictions/<Month> Predictions/<Month> <ordinal>/<Month> <ordinal> <year> Predictions.json
mlb/predictions/<Month> Predictions/<Month> <ordinal>/<Month> <ordinal> <year> Predictions (Updated).json
```

Also provides date helpers such as today, previous date, next date, display date, and month date generation.

#### `diamond-edge/src/utils/format.js`

Formatting helpers for numbers, percentages, money, signed money, and safe text.

#### `diamond-edge/src/utils/deriveStats.js`

Derives UI-facing stats such as confidence, reasoning, better pitcher side, form segments, ROI color, P&L color, implied probability, and formatted EUR/PCT labels.

#### `diamond-edge/src/utils/decisions.js`

Client-side interpretation helpers for prediction rows:

- Decision.
- Pick label.
- Stake label.
- Skip reason.
- Edge tier.
- Stake percent.

#### `diamond-edge/src/utils/parseCsv.js`

CSV fetcher/parser using Papa Parse.

### Public Files

#### `diamond-edge/public/manifest.json`

PWA manifest. Sets the app name to Diamond Edge.

#### `diamond-edge/public/sw.js`

Service worker. Avoids stale prediction data by treating `/mlb/predictions/` paths specially.

#### `diamond-edge/public/favicon.svg`

Browser favicon.

#### `diamond-edge/public/images/baseball-cutout.png`

Baseball image used by the UI.

#### `diamond-edge/public/images/baseball-premium-source.png`

Source image for the baseball artwork.

### Diamond Edge Image/Verification Assets

These are UI image assets or previous visual verification screenshots:

- `diamond-edge/served-baseball-photo-v2.png`
- `diamond-edge/verify-image-baseball.png`
- `diamond-edge/verify-image-baseball-desktop.png`
- `diamond-edge/verify-image-baseball-2.png`
- `diamond-edge/verify-image-baseball-3.png`
- `diamond-edge/verify-image-baseball-4.png`
- `diamond-edge/verify-image-baseball-5.png`
- `diamond-edge/verify-image-baseball-7.png`
- `diamond-edge/verify-image-baseball-8.png`
- `diamond-edge/verify-image-baseball-9.png`

## 12. Slash Command And Run Center Mapping

There is no separate slash-command source file in the repo. The behavior is documented in `AGENTS.md`, `CLAUDE.md`, and `HOW_IT_WORKS.md`.

The practical mappings are:

```text
/generate-mlb-predictions
  -> record_results.py for yesterday
  -> predict_today.py for today

/check-movement
  -> check_movement.py for today
```

In Diamond Edge Run Center:

```text
Settle Results
  -> POST /api/run/settle
  -> python mlb/scripts/record_results.py --date <yesterday>

Generate Today's Picks
  -> POST /api/run/generate
  -> python mlb/scripts/record_results.py --date <yesterday>
  -> python mlb/scripts/predict_today.py --date <today>

Check Line Movement
  -> POST /api/run/movement
  -> python mlb/scripts/check_movement.py --date <today>
```

## 13. External APIs

### MLB Stats API

Base URL:

```text
https://statsapi.mlb.com/api/v1
```

Used for:

- Schedule.
- Final scores.
- Probable pitchers.
- Pitcher stats.
- Boxscores and lineups.
- Transactions and IL flags.
- Live score/status data in the app.

No API key is required.

### The Odds API

URL used by scripts:

```text
https://api.the-odds-api.com/v4/sports/baseball_mlb/odds
```

Used for:

- Moneyline odds.
- Spread/run-line odds.
- Morning odds snapshots.
- Pregame movement odds snapshots.

Requires:

```text
ODDS_API_KEY
```

## 14. Environment Variables

### `.env.local`

No `.env.local` exists in this repo.

### `.env`

Current local `.env` shape. Live values must stay local and must be stored as GitHub Actions secrets for cloud runs:

```dotenv
# Betfair API credentials
BETFAIR_USERNAME=<redacted>
BETFAIR_PASSWORD=<redacted>
BETFAIR_APP_KEY=<redacted>
BETFAIR_CERT_PATH=./betfair/certs
BETFAIR_KEY_PATH=./betfair/certs/client-2048.key

# The Odds API (the-odds-api.com) - free tier: 500 req/month
ODDS_API_KEY=<redacted>

# Database
DATABASE_URL=<redacted>
```

### `.env.example`

Current template:

```dotenv
# Betfair API credentials
BETFAIR_USERNAME=your_username
BETFAIR_PASSWORD=your_password
BETFAIR_APP_KEY=your_app_key
BETFAIR_CERT_PATH=./betfair/certs/client-2048.crt
BETFAIR_KEY_PATH=./betfair/certs/client-2048.key

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/bettingmodel
```

### Environment Variables Used Directly By MLB/Diamond Edge

- `ODDS_API_KEY`: required by `predict_today.py` and `check_movement.py`.
- `SPREAD_DEBUG`: optional flag for spread/run-line debug logging in `predict_today.py`.
- `RUN_TOKEN`: optional Run Center auth token for `serve.js`; defaults to `de-local-run`.
- `PYTHON`: optional Python executable for `serve.js`; defaults to `python`.
- `PORT`: optional local server port for `serve.js`; defaults to `4002`.

## 15. What To Run

### Generate today's picks manually

```bash
python mlb/scripts/predict_today.py
```

For a specific date:

```bash
python mlb/scripts/predict_today.py --date 2026-04-22
```

### Check movement manually

```bash
python mlb/scripts/check_movement.py --date 2026-04-22
```

### Settle results manually

```bash
python mlb/scripts/record_results.py --date 2026-04-21
```

### Start local Diamond Edge

```bash
node serve.js
```

Then open:

```text
http://localhost:4002
```

### Start Vite dev server

```bash
cd diamond-edge
npm run dev
```

Then open:

```text
http://localhost:5173
```

### Build the app

```bash
cd diamond-edge
npm run build
```

The build output goes to:

```text
site/
```

## 16. Files That Are Generated And Should Usually Not Be Hand-Edited

These files are outputs of scripts and should normally be regenerated instead of edited by hand:

- `mlb/data/raw/*.csv`
- `mlb/data/processed/*.csv`
- `mlb/models/*.pkl`
- `mlb/models/*diagnostics.json`
- `mlb/predictions/**/*.json`
- `mlb/predictions/**/*.xlsx`
- `mlb/predictions/**/*.csv`
- `mlb/predictions/**/*.html`
- `mlb/predictions/diagnostics/*`
- `mlb/predictions/odds_archive/*`
- `mlb/reports/*`
- `site/`

The daily markdown report is the human-readable source for picks after generation, but the values inside it are still generated from the Python pipeline.

## 17. Important Safety Notes

- Betting logic lives in `mlb/scripts/predict_today.py` and related model scripts.
- Odds, probabilities, edges, stake tiers, and pick selection should not be changed unless you intentionally want to change model behavior.
- `/generate-mlb-predictions` and `/check-movement` spend Odds API requests.
- Live and final games are intentionally frozen by movement logic.
- Original and updated predictions are tracked separately so morning picks and afternoon-adjusted picks can be compared.
- The current repo has live API credentials in `.env`; treat this markdown file as private if it remains in the repo.

## 18. Required For Telegram Bot And GitHub Actions

This section is the practical manifest for running the MLB commands from a Telegram bot through GitHub Actions.

The Telegram commands are:

```text
/generate-mlb-predictions
/check-movement
```

The bot or workflow should map them to:

```bash
# /generate-mlb-predictions
python mlb/scripts/record_results.py --date <yesterday>
python mlb/scripts/predict_today.py --date <today>

# /check-movement
python mlb/scripts/check_movement.py --date <today>
```

### Core Rule

A private GitHub repo is fine, but GitHub Actions can only use files that are either:

1. Committed to the repo.
2. Downloaded during the workflow.
3. Created/rebuilt during the workflow.
4. Provided as GitHub Actions secrets or variables.

Local ignored files on `E:\BettingModel` will not exist in the GitHub runner.

### Must Be Committed To GitHub

These files are code/config and should be committed:

```text
AGENTS.md
HOW_IT_WORKS.md
MLB_DIAMOND_EDGE_FULL_FILE_GUIDE.md
MLB_MODEL_UPGRADE_SUMMARY.md
requirements.txt
serve.js
firebase.json
deploy.sh

mlb/__init__.py
mlb/scripts/__init__.py
mlb/scripts/backtest.py
mlb/scripts/build_tracker_xlsx.py
mlb/scripts/check_movement.py
mlb/scripts/diagnostics.py
mlb/scripts/feature_utils.py
mlb/scripts/fetch_data.py
mlb/scripts/model.py
mlb/scripts/odds_utils.py
mlb/scripts/predict_today.py
mlb/scripts/preprocess.py
mlb/scripts/record_results.py
mlb/scripts/retrain_2026.py
mlb/scripts/rl_backtest.py
mlb/scripts/season_2026.py
mlb/scripts/spread_model.py
```

For the Diamond Edge web app or Firebase update flow, these should also be committed:

```text
diamond-edge/index.html
diamond-edge/package.json
diamond-edge/package-lock.json
diamond-edge/vite.config.js
diamond-edge/public/
diamond-edge/src/
```

The current local git status shows many `diamond-edge/` files as untracked. If the Telegram/GitHub flow needs to build or update the site, those files must be added to Git.

### Must Exist In The Runner One Way Or Another

These files are required by the MLB pipeline, but are currently ignored by `.gitignore` because `*/models/`, `*/data/raw/`, and `*/data/processed/` are ignored.

They must either be force-committed, downloaded from external storage, or rebuilt during the workflow.

```text
mlb/models/moneyline_model.pkl
mlb/models/spread_model.pkl
mlb/models/spread_model_diagnostics.json

mlb/data/processed/games_processed.csv

mlb/data/raw/games_2025.csv
mlb/data/raw/games_2026.csv
mlb/data/raw/pitchers_2025.csv
mlb/data/raw/pitchers_2026.csv
mlb/data/raw/pitcher_game_logs_2025.csv
mlb/data/raw/bullpens_2025.csv
mlb/data/raw/bullpens_2026.csv
```

Minimum practical set for a normal prediction run:

```text
mlb/models/moneyline_model.pkl
mlb/models/spread_model.pkl
mlb/models/spread_model_diagnostics.json
mlb/data/processed/games_processed.csv
```

The raw CSVs are needed if the workflow will rebuild/retrain models or rerun preprocessing from scratch.

### Prediction State Needed For Continuity

For the bot to settle results, track bankroll, compare morning vs updated picks, and send the correct reports, the runner also needs prediction history/state.

These are already tracked locally according to `git ls-files`, but they must stay available in the repo or be persisted somewhere:

```text
mlb/predictions/results_log.csv
mlb/predictions/results_log_updated.csv
mlb/predictions/results_log.xlsx
mlb/predictions/accumulators_log.csv
mlb/predictions/odds_archive/
mlb/predictions/diagnostics/
mlb/predictions/April Predictions/
mlb/reports/
```

For a clean cloud setup, prediction outputs need a persistence strategy. GitHub Actions runners are temporary, so new prediction files disappear unless the workflow pushes them back to GitHub, uploads them as artifacts, deploys them to Firebase, or writes them to external storage.

### Directories That Must Exist For Output

The scripts can create many folders, but the workflow should make sure these paths exist before running:

```text
mlb/data/raw
mlb/data/processed
mlb/models
mlb/predictions
mlb/predictions/odds_archive
mlb/predictions/diagnostics
mlb/reports
```

### Must Be GitHub Secrets, Not Committed

These should go in:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions
```

Required:

```text
ODDS_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

Needed only if deploying Diamond Edge/Firebase from Actions:

```text
FIREBASE_TOKEN
```

Optional:

```text
RUN_TOKEN
SPREAD_DEBUG
DATABASE_URL
BETFAIR_USERNAME
BETFAIR_PASSWORD
BETFAIR_APP_KEY
BETFAIR_CERT_PATH
BETFAIR_KEY_PATH
```

The MLB prediction scripts only directly need `ODDS_API_KEY` for odds. The Telegram bot needs the Telegram token and a chat ID or allowlist so it knows where to send reports.

### Must Not Be Committed

Do not commit:

```text
.env
.env.local
.venv/
diamond-edge/node_modules/
site/
*.log
*.err.log
bets.db
betfair/certs/
```

The current `.env` contains live credentials. It should remain local and private.

### Current `.gitignore` Blockers

The current `.gitignore` ignores:

```text
.env
models/
*/models/
data/raw/
data/processed/
*/data/raw/
*/data/processed/
site/
diamond-edge/node_modules/
*.log
*.err.log
*.bat
```

This means these required MLB artifacts will not be available in GitHub Actions unless handled deliberately:

```text
mlb/models/moneyline_model.pkl
mlb/models/spread_model.pkl
mlb/data/processed/games_processed.csv
mlb/data/raw/*.csv
```

Options:

1. Force-add them to the private repo:

```bash
git add -f mlb/models/moneyline_model.pkl
git add -f mlb/models/spread_model.pkl
git add -f mlb/models/spread_model_diagnostics.json
git add -f mlb/data/processed/games_processed.csv
git add -f mlb/data/raw/games_2025.csv
git add -f mlb/data/raw/pitchers_2025.csv
git add -f mlb/data/raw/pitcher_game_logs_2025.csv
git add -f mlb/data/raw/bullpens_2025.csv
```

2. Store them externally and download them in the workflow.

3. Rebuild them during the workflow with:

```bash
python mlb/scripts/fetch_data.py
python mlb/scripts/preprocess.py
python mlb/scripts/model.py
python mlb/scripts/spread_model.py
```

Rebuilding is slower and spends API time against MLB, but does not require committing generated artifacts.

### Recommended Approach For This Repo

Best practical setup:

1. Keep the GitHub repo private.
2. Commit all code, documentation, `requirements.txt`, and Diamond Edge source files.
3. Force-add the small required model artifacts and `games_processed.csv` if file sizes are acceptable.
4. Keep `.env` out of Git.
5. Put `ODDS_API_KEY`, `TELEGRAM_BOT_TOKEN`, and `TELEGRAM_CHAT_ID` in GitHub Actions secrets.
6. Have GitHub Actions run the command.
7. Have the workflow send the generated `.md` report back to Telegram.
8. Persist generated predictions by committing them back, uploading artifacts, deploying to Firebase, or storing them externally.

### Dry-Run Workflow Check

Before letting the Telegram bot run live dates, the first GitHub Actions test should run a known historical date:

```bash
python mlb/scripts/predict_today.py --date 2026-04-21
python mlb/scripts/check_movement.py --date 2026-04-21
```

Success means:

- Python dependencies install.
- Imports work.
- Model files are present.
- Data files are present or rebuilt.
- `ODDS_API_KEY` is available.
- Output folders are writable.
- The expected `.md` report can be found.

### Telegram Bot Response Contract

For `/generate-mlb-predictions`, send back:

```text
mlb/predictions/<Month> Predictions/<Month> <ordinal>/<Month> <ordinal> <year> Predictions.md
```

For `/check-movement`, send back:

```text
mlb/predictions/<Month> Predictions/<Month> <ordinal>/<Month> <ordinal> <year> Predictions (Updated).md
```

If the markdown file is too long for one Telegram message, the bot should either:

1. Send the `.md` as a document attachment.
2. Split the markdown into multiple messages.
3. Send a short summary plus attach the full report.

### Minimal GitHub Actions Job Shape

The workflow needs these phases:

```text
checkout repo
set up Python
install requirements
restore/download model and data artifacts if not committed
export secrets as environment variables
run selected MLB command
find generated markdown
send markdown/document to Telegram
persist generated output
```

The exact workflow file can be added later under:

```text
.github/workflows/mlb-telegram.yml
```
