# MLB Predictions — Website Engineering Brief

## What This Is

A **daily MLB betting predictions dashboard** — a web front-end for the Python pipeline that lives in `mlb/`. The pipeline runs every morning, hits the MLB Stats API + The Odds API, trains a logistic regression model on 2026 season data, and outputs a set of daily picks with edges and stakes.

The website should make those picks readable, trackable, and shareable. It is read-only — it consumes data files written by the Python scripts. It does not need a database or server-side logic.

---

## Data Sources (What the Python Pipeline Writes)

All data files live under `mlb/predictions/`:

```
mlb/predictions/
  results_log.csv          — cumulative results: one row per settled bet
  results_log_updated.csv  — same but using afternoon-checked odds (post movement check)
  accumulators_log.csv     — accumulator results log
  <Month> Predictions/
    <Month> <Day>th/
      <stub>.json          — morning predictions (full detail, see schema below)
      <stub> (Updated).json — afternoon re-evaluated predictions
      <stub>.md            — human-readable markdown
      <stub>.xlsx          — Excel tracker with Win/Loss/Push dropdown
```

### JSON Schema — daily predictions file

```json
{
  "date": "2026-04-16",
  "bankroll": 538.40,
  "predictions": [
    {
      "gamePk": 823396,
      "gameStatus": "NOT_STARTED",       // "NOT_STARTED" | "LIVE" | "FINAL"
      "homeTeam": "Pittsburgh Pirates",
      "awayTeam": "Washington Nationals",
      "homeAbbr": "PIT",
      "awayAbbr": "WSH",
      "homeProb": 0.4826,                // model's home win probability
      "awayProb": 0.5174,
      "pickSide": "none",                // "home" | "away" | "none"
      "modelProb": 0.5174,               // model probability for the picked side
      "edge": 0.0,                       // model_prob - market_implied_prob
      "stake": {
        "pct": "0%",
        "pctValue": 0,
        "eur": 0.0,
        "label": "pass",
        "reportLabel": "PASS"
      },
      "hasRolling": true,
      "awayMl": 2.30,
      "homeMl": 1.60,
      "awayRl": 4.00,
      "homeRl": 2.38,
      "useRl": false,                    // true if run-line (-1.5) is the primary bet
      "rlPickOdds": null,
      "pickOdds": 1.85,                  // decimal odds for the picked market
      "marketImplied": 0.5405,
      "bookCount": 25,
      "hasOdds": true,
      "homeBook": "paddypower",
      "awayBook": "boylesports",
      "homeSpName": "Braxton Ashcraft",
      "awaySpName": "Foster Griffin",
      "homeSpEra": 2.12,
      "awaySpEra": 1.76,
      "homeSpWhip": 1.0,
      "awaySpWhip": 1.11,
      "homeSpW": 1,
      "homeSpL": 1,
      "awaySpW": 2,
      "awaySpL": 0,
      "homeL10WP": 0.60,                 // home team last-10-games win %
      "awayL10WP": 0.50,
      "homeL10RD": 1.50,                 // home team last-10-games avg run differential
      "awayL10RD": -0.90,
      "eraDiff": 0.36,
      "seriesGameNumber": 4,
      "gamesInSeries": 4,
      "homeRlOptions": [{"line": -1.5, "odds": 2.4}],
      "awayRlOptions": [{"line": 1.5, "odds": 1.62}]
    }
  ],
  "accumulators": [
    {
      "type": "Double",
      "legs": [
        {"game": "WSH @ PIT", "label": "PIT ML", "odds": 1.60, "edge": 0.074},
        {"game": "NYY @ BOS", "label": "BOS ML", "odds": 1.90, "edge": 0.062}
      ],
      "combined_odds": 3.04,
      "stake": 5.00,
      "potential_return": 15.20
    }
  ]
}
```

### results_log.csv columns

```
date, game, pick, odds, stake_pct, stake_eur, result, profit_loss, bankroll_after
```

`result` is one of: `Win`, `Loss`, `Push`, `Pending`

---

## What the Website Should Do

### Page 1 — Today's Picks (index page)

- Show today's predictions JSON by default (or latest available date)
- Date picker to navigate to any past date
- **Decision sections** (match the Python pipeline's output grouping):
  - `🟢 BET` cards — games with edge ≥ 3% (stake > 0)
  - `SKIP` cards — games evaluated but below threshold
  - `🔴 LIVE` banner for any game already in progress (gameStatus = "LIVE") — show the frozen morning pick, NOT live odds
  - `⚪ FINAL` — collapse and grey out completed games

**Each BET card should show:**
- Game matchup (Away @ Home)
- Series context (Game N of N if available)
- Starting pitchers with ERA / WHIP
- Pick (team + ML or -1.5)
- Odds (decimal)
- Edge % (colour-coded: green ≥ 5%, amber 3–5%)
- Stake amount in EUR
- Model probability vs market implied probability (small bar or pill)
- Last-10 win % for both teams
- Confidence score (1–10)
- Primary reason for the pick

**Each SKIP card should show:**
- Game + pitchers (collapsed by default, expandable)
- Why it's a skip (edge < 3% or no clear model direction)

**Accumulators section** (if any in the JSON):
- Show Double/Treble with legs, combined odds, stake, potential return
- Note: "Acca bankroll is separate from singles"

---

### Page 2 — Results & Bankroll Tracker

Source file: `mlb/predictions/results_log.csv`

Display:
- Running bankroll chart (line chart — bankroll_after over time)
- Summary stats: total bets, win rate, total staked, total P&L, ROI %
- Results table (sortable by date / game / profit):
  - Date | Game | Pick | Odds | Stake | Result | P&L | Bankroll After
  - Colour rows: Win = green, Loss = red, Push = amber, Pending = grey
- Filter by month or result type
- Separate section for `results_log_updated.csv` (afternoon-checked variant) with same layout

---

### Page 3 — Line Movement (optional, nice-to-have)

Source file: `<stub> (Updated).json` for the selected date

- Compare `pickOdds` (morning) vs updated `pickOdds` (afternoon)
- Arrow + delta for each pick
- Colour: green if line moved in our favour, red if against
- Note at top: "Live games are excluded from this comparison — odds changes after game start are not market signals"

---

## UI / Design Requirements

- **Dark theme** — use the same palette as the existing HTML report:
  ```
  --bg: #0b1220
  --panel: #111a2b
  --text: #e7edf7
  --muted: #91a0b8
  --bet: #19c37d   (green — BET decisions)
  --skip: #ff8a65  (orange — SKIP decisions)
  --accent: #6cb8ff
  ```
- Responsive — works on mobile (single-column card layout)
- No dependencies required beyond a bundler + one charting library
- Fast — data is static JSON, no server needed

---

## Tech Stack Recommendation

**Preferred: plain HTML + vanilla JS + one build step**

The simplest production-ready approach:

```
site/
  index.html          — Today's picks
  results.html        — Bankroll tracker
  movement.html       — Line movement (optional)
  js/
    picks.js          — Parse predictions JSON, render cards
    results.js        — Parse results_log.csv, render chart + table
    utils.js          — Shared helpers (date formatting, ordinal, decision logic)
  css/
    main.css          — Global styles (dark theme)
  data/               — Symlink or copy of mlb/predictions/ (or served statically)
```

Chart library: **Chart.js** (CDN, no build needed) for the bankroll line chart.

Alternatively: **Vite + React** if you want component reuse. Keep state local — no Redux needed.

---

## Decision Logic (must match Python exactly)

The website must replicate this logic to label picks correctly:

```js
function decisionForRow(row) {
  if (row.pickSide === "none") return "SKIP";
  if (!row.hasOdds || row.pickOdds == null) return "SKIP";
  if (row.edge < 0.03 || row.stake.eur <= 0) return "SKIP";
  return "BET";
}

function pickLabel(row) {
  if (row.pickSide === "none") return "SKIP";
  const team = row.pickSide === "home" ? row.homeAbbr : row.awayAbbr;
  return row.useRl ? `${team} -1.5` : `${team} ML`;
}

function stakeLabel(row) {
  return `${row.stake.pctValue}% (EUR ${row.stake.eur.toFixed(2)})`;
}
```

BET threshold: edge >= 3%.  
Stake tiers: 1% (3–6%), 2% (6–10%), 3% (10–15%), 4% (15–20%), 5% (20%+).  
Bankroll: EUR 500 base, updated daily from results_log.csv.

---

## Game State Handling (CRITICAL)

The pipeline now writes `gameStatus` to each prediction row:

| gameStatus    | Website behaviour                                                   |
|---------------|---------------------------------------------------------------------|
| NOT_STARTED   | Show full card with pick, odds, edge, stake — normal                |
| LIVE          | Show card with morning pick frozen; add 🔴 LIVE badge; hide edge bar; never show live odds as movement |
| FINAL         | Collapse card; grey out; show final score if available              |

**Never recalculate or display edge using live/in-play odds.** If `gameStatus === "LIVE"`, the pick and stake shown are from the morning, and that is correct.

---

## File Discovery

To find the JSON for a given date `YYYY-MM-DD`:

```js
function predictionsPath(dateStr) {
  const dt = new Date(dateStr + "T12:00:00");
  const month = dt.toLocaleString("en-US", { month: "long" }); // "April"
  const day = dt.getDate();
  const year = dt.getFullYear();
  const ord = ordinal(day); // "16th"
  return `mlb/predictions/${month} Predictions/${month} ${ord}/${month} ${ord} ${year} Predictions.json`;
}

function ordinal(n) {
  const s = ["th","st","nd","rd"];
  const v = n % 100;
  return n + (s[(v-20)%10] || s[v] || s[0]);
}
```

---

## What NOT to Build

- No user accounts, logins, or auth
- No server-side bet placement or bookmaker integrations
- No live odds feed on the website (The Odds API key stays server-side / Python-only)
- No editing of picks from the UI
- No notifications or alerts

---

## Existing Output to Reference

The Python pipeline already generates a styled HTML report at:
```
mlb/predictions/<Month> Predictions/<Month> <Day>th/<stub>.html
```

Use this as a visual reference for the card layout and tone. The website should be a proper multi-page app built on the same data, not just a wrapper around that HTML.

---

## Key Files in the Pipeline

| File | Role |
|------|------|
| `mlb/scripts/predict_today.py` | Generates morning predictions JSON — source of truth |
| `mlb/scripts/check_movement.py` | Afternoon movement check — writes `(Updated).json` |
| `mlb/scripts/record_results.py` | Settles bets, updates results_log.csv |
| `mlb/predictions/results_log.csv` | Cumulative P&L ledger |
