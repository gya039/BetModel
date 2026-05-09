# MLB Betting Model — Audit & Upgrade Plan

## Overview

A structured quantitative betting framework built around:
- Logistic Regression (33 features) trained on 2025 season data
- Real market odds via The Odds API (h2h + spreads + alternate spreads)
- 7-tier edge-based staking system
- Two-phase daily execution:
  - `/generate-mlb-predictions` (morning)
  - `/check-movement` (pre-game update)
- Firebase-deployed web app with live reports (MD, Excel, JSON, CSV)

The architecture is solid. The main gap is feedback loops — we don't yet know if the model is well-calibrated or which edge tiers are actually profitable.

---

# Current Strengths

- Rolling team features (L5/L10/L20) built with strict no look-ahead (deque-based)
- Pitcher stats (ERA, WHIP, K/9) with sample-size blending for low-IP starters
- 30-team ballpark factors integrated into features
- Edge calculation against real live odds (not fixed lines)
- 7-tier confidence staking (PASS below 1%, micro at 0.5%, up to 5% at 20%+ edge)
- Run line logic: evaluates spreads + alternate spreads when model confidence >63%
- Accumulator builder (doubles & trebles) with separate EUR 100 bankroll
- Two-phase workflow: morning predictions + afternoon movement/injury checks
- Result settlement with running P&L tracking (game-by-game)
- Model evaluation: AUC-ROC, log loss, Brier score

---

# Critical Gaps

## 1. No Closing Line Value (CLV) Tracking

**Status:** `tracker/database.py` has a `closing_odds` field in schema but is never populated. Start from scratch — ignore that file.

**What to build:**
- After each game goes final, fetch the closing odds from The Odds API (or archive them pre-game)
- Store alongside the bet: `odds_taken`, `closing_odds`
- Calculate: `CLV% = (closing_implied_prob - implied_prob_at_bet) / implied_prob_at_bet`
- Positive CLV = you bet before the market moved against you (long-term edge signal)

**Why it matters:** Win rate is noisy over small samples. Consistent positive CLV is the real proof the model has an edge.

---

## 2. Backtest Uses Simulated Odds

**Status:** `backtest.py` uses generic approximations (favourite -115 → 1.870, underdog +105 → 2.050) not real historical odds.

**Fix:** Start archiving live The Odds API responses now. Future backtests can run against real stored odds. Current backtest ROI figures are "what if" scenarios only.

---

## 3. Lineup / Pitcher Updates Not Re-fed Into Model

**Status:** If a pitcher is TBD at morning run, the game is skipped (`SKIP - SP not yet announced`). No mid-day re-run when TBD → confirmed.

**Fix:**
- `/check-movement` should detect TBD games where SP is now confirmed
- Rebuild features → re-run model → recalc probabilities → recalc edge
- Emit as new BET candidates if edge threshold met

---

## 4. No Probability Calibration Validation

**Status:** `model.py` calculates overall Brier score but no per-bucket analysis.

**Fix:** Split test set into probability bins (50–55%, 55–60%, 60–65%, 65%+). Track actual win rate per bin. If model says 60% but teams win 52%, the edge calculation is systematically wrong.

---

## 5. No Live Edge-Bucket Performance Tracking

**Status:** `backtest.py` shows edge bucket breakdowns offline only. `record_results.py` doesn't classify settled bets by edge tier.

**Fix:** Add `edge_bucket` column to settlement CSV. Generate running report:
```
Edge 1–3%:   22 bets | 51% win | -€8  ROI: -1.6%
Edge 3–6%:   18 bets | 54% win | +€21 ROI: +4.3%
Edge 6–10%:  11 bets | 60% win | +€44 ROI: +8.9%
Edge 10%+:    6 bets | 67% win | +€38 ROI: +12.1%
```

---

## 6. Dynamic Bankroll Staking Not Implemented

**Status:** `stake_tier(edge, bankroll=500.0)` — EUR 500 hardcoded. `record_results.py` tracks running bankroll but never feeds it back into future stake sizing.

**Fix:**

```python
stake = current_bankroll * stake_percentage
```

Example: EUR 647 bankroll × 4% = EUR 25.88

Read current bankroll from latest row in `results_log.csv` at prediction time. Pass it into `stake_tier()`.

---

## 7. Accumulator Results Not Logged

**Status:** Accumulator builder (`build_accumulators()`) generates doubles & trebles but no settlement log exists.

**Fix:**
- Add `accumulators_log.csv` to `record_results.py`
- Track each accumulator: legs, combined odds, stake, outcome, P&L
- Display in web app under a collapsed **More** section (hidden by default, visible on expand) — separate from main singles stats so they don't pollute the headline numbers

---

# Structural Improvements

- Evaluate both sides EV instead of picking side first (currently model picks home/away then checks odds)
- Add uncertainty adjustments for games with TBD pitchers, back-to-back travel, or <5 games rolling window
- Flag when model confidence and line direction contradict each other (high edge but line shortening = market disagrees)

---

# Priority Order

1. **Dynamic bankroll staking** — low effort, immediate impact on stake sizing accuracy
2. **Edge bucket tracking** — add one column to settlement, unlocks ROI by tier
3. **CLV tracking** — build from scratch: archive closing odds, calculate CLV% per bet
4. **Re-run model on SP updates** — integrate into `/check-movement`
5. **Accumulator logging** — add `accumulators_log.csv`, hide stats in web app More section
6. **Calibration validation** — bucket analysis on 2025 test set, then live
7. **Real odds backtesting** — archive now, backtest against real odds when enough data exists

---

# Final Insight

The system needs feedback from reality, not more complexity. Priorities 1–3 directly answer: "Are we staking correctly, on the right tiers, with real edge?" Everything else builds on those answers.
