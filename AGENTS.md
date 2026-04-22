# BettingModel — Codex Guide

## Project structure

```
mlb/
  scripts/
    predict_today.py   # main entry point — run this to generate predictions
    model.py           # feature definitions, model training helpers
    fetch_data.py      # historical data fetching
    preprocess.py      # feature engineering for training
    backtest.py        # backtesting harness
    season_2026.py     # 2026 season bootstrap
  models/
    moneyline_model.pkl  # trained logistic regression (2025 data)
  data/
    raw/               # raw CSVs from MLB Stats API
    processed/         # feature-engineered CSVs
  predictions/
    <Month> Predictions/
      <Month> <Day> <Year> Predictions.md    # daily markdown report
      <Month> <Day> <Year> Predictions.html  # styled HTML
      <Month> <Day> <Year> Predictions.csv   # raw CSV
      <Month> <Day> <Year> Predictions.xlsx  # Excel tracker (with Win/Loss/Push dropdown)
      <Month> <Day> <Year> Predictions.json  # raw JSON
```

## Generating today's predictions

```bash
python mlb/scripts/predict_today.py
# or for a specific date:
python mlb/scripts/predict_today.py --date 2026-04-15
```

Run from the repo root (`E:\BettingModel`).

Requires env vars in `.env` at the repo root:
- `ODDS_API_KEY` — The Odds API key (https://the-odds-api.com)

## Slash command

`/generate-mlb-predictions` — runs the pipeline and surfaces the markdown report inline.

## Key rules

- **Never edit betting logic in Codex.** Odds, probabilities, edges, stakes, and pick selection all live in `predict_today.py`. Codex is orchestration only.
- The markdown report is the source of truth for daily picks. HTML and Excel are for tracking/viewing.
- BET threshold: edge >= 3%. Anything below is SKIP.
- Stake tiers: 1% (edge 3-6%), 2% (6-10%), 3% (10-15%), 4% (15-20%), 5% (20%+).
- Bankroll: EUR 500.
