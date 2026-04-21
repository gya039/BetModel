# Octagon IQ

MMA analytics dashboard for UFC events. Scrapes UFCStats.com, computes fighter metrics, and serves a local dark-themed dashboard.

**Target event:** UFC Fight Night: Sterling vs. Zalal (April 25, 2026)
The scraper auto-detects the latest card at runtime — no hardcoded bout list.

---

## Setup

```bash
cd E:\BettingModel\mma
pip install -r requirements.txt
```

---

## Run the full pipeline

```bash
# 1. Discover the event and scrape the fight card
python src/fetch_card.py

# 2. Scrape each fighter's profile and fight history
python src/fetch_fighters.py

# 3. Scrape per-fight stat detail pages (strikes, TDs, etc.)
python src/fetch_fight_history.py

# 4. Clean and normalise all raw data
python src/preprocess.py

# 5. Compute all fighter metrics
python src/aggregate_stats.py

# 6. Optional: fetch live MMA odds from The Odds API
python src/fetch_odds.py

# 7. Generate betting leans, method probabilities, and value edges
python src/betting_model.py

# 8. Optional fight-day movement check
python src/check_movement.py

# 9. Launch the Octagon IQ dashboard
python src/app.py
```

Then open: **http://localhost:5001**

---

## Data outputs

| File | Contents |
|---|---|
| `data/raw/card.json` | Full fight card — event meta + all bouts |
| `data/raw/fighters/<id>.json` | Per-fighter: record, bio, career stats, fight history |
| `data/raw/fights/<id>.json` | Per-fight: strike/TD totals from fight detail pages |
| `data/processed/fighters_raw.json` | Cleaned, normalised fighter data |
| `data/processed/fighter_summary.csv` | Flat CSV of all computed metrics |
| `data/processed/fighter_summary.json` | Same, JSON with fight history included |
| `data/processed/matchup_summary.csv` | Bout-by-bout comparison table |
| `data/processed/matchup_summary.json` | Same, JSON with full fighter objects |
| `data/raw/odds/latest.json` | Latest The Odds API MMA odds snapshot |
| `data/processed/betting/betting_edges.csv` | Flat betting markets with model probability, implied probability, edge, label, and rationale |
| `data/processed/betting/betting_edges.json` | Fight-by-fight betting breakdowns, suggestions, and value analysis |
| `data/processed/betting/movement_report.csv` | Saturday odds movement report |
| `data/processed/betting/movement_report.json` | Same movement report as JSON |
| `data/processed/betting/staking_plan.csv` | Singles stake plan from the EUR 500 bankroll |
| `data/processed/betting/staking_plan.json` | Singles, accumulator suggestions, staking rules, and bankroll |
| `data/processed/betting/bet_history.csv` | Manual bet-history ledger for settlement and bankroll tracking |

---

## Dashboard pages

| URL | Page |
|---|---|
| `/` | Event card — all bouts with quick stats |
| `/fighters` | Fighter directory with search + stance filter |
| `/fighter/<id>` | Fighter detail — bio, charts, fight history |
| `/matchup/<id_a>/<id_b>` | Head-to-head comparison with radar chart |
| `/betting` | Betting board with side leans, props, confidence, and playable/pass labels |
| `/betting/<fight_id>` | Individual fight betting breakdown, fight script, odds, and value table |
| `/check-movement` | Fight-day movement board comparing earlier odds to the latest prices |
| `/bankroll` | Bankroll, singles staking plan, accumulator suggestions, and bet history |
| `/export` | Download CSV/JSON, data preview table |

---

## Computed stats (per fighter)

- Win/loss/draw/NC record + win rate + loss rate
- Wins & losses broken down by: KO/TKO, Submission, Decision, DQ, Other
- Percentages for each method category
- Career averages: SLpM, Str Acc, SApM, Str Def, TD Avg, TD Acc, TD Def, Sub Avg
- Per-fight averages: avg sig strikes, avg total strikes, avg TDs landed
- Last-3 and last-5 fight rolling averages
- Current win/loss streak
- Finish rate + decision rate
- Physical: height, weight, reach, stance, age

---

## Notes

- All scraping uses polite throttling (0.8s between requests) and retries.
- HTML responses are cached in `data/raw/cache/` — delete to force re-fetch.
- The event cache is always invalidated on `fetch_card.py` runs to pick up late card changes.
- Fighter analytics data comes from UFCStats.com. The betting layer can also use `ODDS_API_KEY` for live fight-day prices from The Odds API.
- The betting engine is intentionally probability-based: it labels no-edge markets as Pass and does not force prop picks.
- Staking uses a EUR 500 starting bankroll with 3%, 5%, 7%, and 10% stake tiers, confidence caps, and a 50% max single-bet exposure cap across a card.
- Accumulators are generated only from real priced moneyline or method-of-victory markets with positive edge.
