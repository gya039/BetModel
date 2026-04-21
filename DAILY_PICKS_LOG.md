# Diamond Edge â€” Daily MLB Picks Log (April 2026)

**Current model:** Logistic regression trained on the saved moneyline feature contract in `moneyline_model.pkl` (76 features as of the April 21 run).  
**Bankroll:** Started EUR 500. Singles stakes are sized from the current bankroll read from `results_log.csv`.  
**Pick logic:** Edge = model win probability minus no-vig market probability when both moneyline sides are available. BET if backend decision logic qualifies the row.  
**Stake tiers:** 1-3% edge -> 0.5% stake | 3-6% -> 1% | 6-10% -> 2% | 10-15% -> 3% | 15-20% -> 4% | 20%+ -> 5%.  
**Run lines:** Disabled for production singles and accumulators unless a separately trained spread model passes validation. Historical rows below may include older run-line picks.
**Why columns explained:**
- *Better SP matchup* â€” the picked team's starting pitcher had a meaningfully lower ERA
- *Better L10 form* â€” the picked team won a higher % of their last 10 games
- *Better L10 run diff* â€” the picked team outscored opponents by more per game over last 10

---

## April 14th, 2026

**Slate:** 15 games | **Bets placed:** 12 | **Bankroll entering:** EUR 500.00  
**Result:** 5W 7L | **Day P&L: EUR âˆ’10.25**

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | AZ @ BAL | BAL âˆ’1.5 | 1.62 | 10.1% | EUR 15 | âŒ Loss | âˆ’15.00 | Better SP matchup (Rogers ERA 1.89 vs Kelly ERA N/A) |
| 2 | KC @ DET | DET ML | 1.80 | 12.9% | EUR 15 | âœ… Win | +12.00 | Better SP matchup (Valdez 4.76 vs Ragans 5.91) Â· Better L10 run diff |
| 3 | WSH @ PIT | PIT âˆ’1.5 | 1.53 | 5.8% | EUR 5 | âŒ Loss | âˆ’5.00 | Better SP (Keller 1.00) Â· Better L10 form (70% vs 40%) Â· Better run diff |
| 4 | SF @ CIN | SF ML | 1.95 | 29.2% | EUR 25 | âŒ Loss | âˆ’25.00 | Better SP matchup (Ray ERA 2.08 vs Singer ERA 7.71) |
| 5 | CHC @ PHI | CHC ML | 2.20 | 21.3% | EUR 25 | âœ… Win | +30.00 | Better SP matchup (Martin ERA 0.00 vs Nola ERA 3.63) |
| 6 | BOS @ MIN | BOS ML | 1.73 | 16.7% | EUR 20 | âŒ Loss | âˆ’20.00 | Better SP matchup (Gray ERA 2.76 vs Abel ERA 6.08) |
| 7 | TB @ CWS | CWS ML | 2.15 | 14.5% | EUR 15 | âŒ Loss | âˆ’15.00 | Better L10 run diff (+0.1 vs âˆ’0.8) |
| 8 | TOR @ MIL | TOR ML | 2.00 | 8.4% | EUR 10 | âœ… Win | +10.00 | Better SP matchup (Gausman ERA 1.56 vs Misiorowski ERA 3.31) |
| 9 | CLE @ STL | STL ML | 2.05 | 14.0% | EUR 15 | âœ… Win | +15.75 | Better SP matchup (McGreevy ERA 2.16 vs Cantillo ERA 2.45) |
| 10 | COL @ HOU | HOU âˆ’1.5 | 1.53 | 11.7% | EUR 15 | âŒ Loss | âˆ’15.00 | Better SP (Gordon ERA N/A vs Lorenzen ERA 8.36) |
| 11 | SEA @ SD | SEA ML | 1.83 | 3.1% | EUR 5 | âŒ Loss | âˆ’5.00 | Better SP matchup (Woo ERA 1.50 vs King ERA 3.24) |
| 12 | TEX @ ATH | ATH ML | 2.10 | 18.1% | EUR 20 | âœ… Win | +22.00 | Better SP (Springs ERA 1.47) Â· Better L10 form (70% vs 50%) |

**Notes:** Day started April 14 being the season debut for the model. Several high-edge bets (SF, BOS, CWS) lost despite clear statistical advantages â€” normal variance on a large-slate day with 12 bets.

---

## April 15th, 2026

**Slate:** 15 games | **Bets placed:** 10 | **Bankroll entering:** EUR 489.75  
**Result:** 7W 3L | **Day P&L: EUR +48.65**

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | AZ @ BAL | AZ ML | 2.15 | 33.8% | EUR 25 | âœ… Win | +28.75 | Better SP (Rodriguez ERA 0.50 vs Bradish ERA 5.27) Â· Better form & run diff |
| 2 | CLE @ STL | CLE ML | 1.91 | 12.7% | EUR 15 | âŒ Loss | âˆ’15.00 | Better SP (Cecconi ERA 5.74 vs May ERA 9.45) Â· Better run diff |
| 3 | KC @ DET | KC ML | 2.10 | 23.3% | EUR 25 | âŒ Loss | âˆ’25.00 | Better SP matchup (Lugo ERA 1.53 vs Flaherty ERA 5.14) |
| 4 | SF @ CIN | CIN ML | 1.91 | 12.6% | EUR 15 | âœ… Win | +13.65 | Better SP (Lowder ERA 3.31 vs Mahle ERA 4.30) Â· Better form (60% vs 30%) |
| 5 | CHC @ PHI | CHC ML | 2.20 | 19.9% | EUR 20 | âœ… Win | +24.00 | Better SP (Imanaga ERA 2.81 vs Luzardo ERA 6.23) Â· Better form & run diff |
| 6 | LAA @ NYY | LAA ML | 2.55 | 34.0% | EUR 25 | âŒ Loss | âˆ’25.00 | Better SP (Kochanowicz ERA 3.24 vs Gil ERA 6.75) Â· Better form (60% vs 30%) |
| 7 | TB @ CWS | TB ML | 1.90 | 11.3% | EUR 15 | âœ… Win | +13.50 | Better SP (Scholtens ERA 0.00) Â· Better form (70% vs 40%) |
| 8 | TOR @ MIL | MIL ML | 2.05 | 12.8% | EUR 15 | âœ… Win | +15.75 | Better SP (Patrick ERA 0.73 vs Cease ERA 2.45) Â· Better run diff |
| 9 | SEA @ SD | SD ML | 1.90 | 9.3% | EUR 10 | âœ… Win | +9.00 | Better SP (VÃ¡squez ERA 1.02 vs Hancock ERA 2.04) Â· Form 90% vs 40% |
| 10 | TEX @ ATH | ATH ML | 1.90 | 8.5% | EUR 10 | âœ… Win | +9.00 | Better SP (Ginn ERA 3.27 vs Rocker ERA 4.50) Â· Better form (70% vs 50%) |

**Notes:** Best win-rate day of the sample. CHC/AZ/SD all delivered. LAA @ NYY was the notable miss â€” model saw 34% edge but NYY won at home.

---

## April 16th, 2026

**Slate:** 10 games (lighter midweek slate) | **Bets placed:** 7 | **Bankroll entering:** EUR 538.40  
**Result:** 5W 2L | **Day P&L: EUR +64.85**

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | KC @ DET | DET ML | 1.95 | 14.5% | EUR 15 | âœ… Win | +14.25 | Better SP (Montero ERA 1.74 vs Bubic ERA 2.50) Â· Better form & run diff |
| 2 | TOR @ MIL | TOR ML | 2.05 | 12.5% | EUR 15 | âŒ Loss | âˆ’15.00 | Better SP (Corbin ERA 9.00 vs Sproat ERA 10.45) â€” both SPs poor, model saw away value |
| 3 | TB @ CWS | CWS ML | 2.10 | 14.7% | EUR 15 | âŒ Loss | âˆ’15.00 | Model edge vs market price (similar ERAs, CWS home undervalued) |
| 4 | TEX @ ATH | TEX ML | 1.87 | 5.9% | EUR 5 | âœ… Win | +4.35 | Better SP (Leiter ERA 4.91 vs Lopez ERA 7.43) Â· Marginal run diff edge |
| 5 | BAL @ CLE | CLE ML | 1.75 | 17.0% | EUR 20 | âœ… Win | +15.00 | Better SP matchup (Messick ERA 0.51 vs Baz ERA 4.50) |
| 6 | COL @ HOU | COL ML | 2.45 | 26.7% | EUR 25 | âœ… Win | +36.25 | Better SP (Weiss ERA 7.36 vs Mejia ERA 5.40) Â· COL better form + run diff |
| 7 | SEA @ SD | SD ML | 2.00 | 23.4% | EUR 25 | âœ… Win | +25.00 | Better SP (Buehler ERA 4.97 vs Castillo ERA 6.92) Â· SD form 90% vs SEA 40% |

**Notes:** Best single-day P&L of the run. COL ML at 2.45 with 26.7% edge was the standout â€” market was too slow to reprice COL's improvement. SD continued its strong form run.

---

## April 17th, 2026

**Slate:** 15 games | **Bets placed:** 8 | **Bankroll entering:** EUR 603.25  
**Result:** 3W 5L | **Day P&L: EUR âˆ’45.15** *(worst day)*

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | NYM @ CHC | CHC âˆ’1.5 | 1.67 | 27.1% | EUR 25 | âœ… Win | +32.00 | Better SP (Cabrera ERA 1.62 vs Senga ERA 7.07) Â· CHC better form & run diff |
| 2 | BAL @ CLE | CLE ML | 1.73 | 11.8% | EUR 15 | âŒ Loss | âˆ’15.00 | Better SP (Bibee ERA 6.38 vs Bassitt ERA 9.00) â€” both struggling, CLE home edge |
| 3 | SF @ WSH | WSH ML | 2.30 | 24.0% | EUR 25 | âŒ Loss | âˆ’25.00 | Better SP (Littell ERA 4.20 vs Webb ERA 5.25) Â· WSH better form (60% vs 40%) |
| 4 | TB @ PIT | TB ML | 2.10 | 19.7% | EUR 20 | âŒ Loss | âˆ’20.00 | Better SP (Martinez ERA 2.16 vs Chandler ERA 3.86) Â· TB better form (80% vs 50%) |
| 5 | KC @ NYY | KC ML | 2.55 | 21.3% | EUR 25 | âŒ Loss | âˆ’25.00 | Better SP (Wacha ERA 0.43 vs Schlittler ERA 2.49) Â· KC better run diff |
| 6 | LAD @ COL | COL ML | 3.40 | 40.2% | EUR 25 | âŒ Loss | âˆ’25.00 | Better SP (Sugano ERA 2.16 vs Glasnow ERA 4.00) â€” model's biggest edge of the month |
| 7 | SD @ LAA | LAA ML | 1.73 | 19.1% | EUR 20 | âœ… Win | +14.60 | Better SP (Soriano ERA 0.33 vs Waldron ERA N/A) |
| 8 | TOR @ AZ | AZ ML | 1.73 | 26.8% | EUR 25 | âœ… Win | +18.25 | Better SP (Soroka ERA 2.87 vs Lauer ERA 7.82) Â· AZ form 70% vs TOR 30% |

**Notes:** Worst day of the season. COL ML at 3.40 (40.2% edge â€” highest of any bet so far) lost. KC @ NYY and TB @ PIT both lost despite strong model edges. Variance clustering â€” all five losses came in a single day. Model stayed disciplined, no tilt adjustments needed.

---

## April 18th, 2026

**Slate:** 15 games | **Bets placed:** 9 | **Bankroll entering:** EUR 558.10  
**Result:** 6W 3L | **Day P&L: EUR +94.30** *(best day)*

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | TB @ PIT | TB ML | 2.30 | 12.0% | EUR 14.96 | âœ… Win | +19.45 | Better SP (Rasmussen ERA 1.13 vs Skenes ERA 4.00) Â· TB better form (70% vs 60%) |
| 2 | SF @ WSH | WSH ML | 1.95 | 6.8% | EUR 9.97 | âŒ Loss | âˆ’9.97 | Better SP (Cavalli ERA 4.60 vs Houser ERA 5.06) |
| 3 | CWS @ ATH | CWS ML | 2.30 | 10.0% | EUR 9.97 | âŒ Loss | âˆ’9.97 | Better SP (Fedde ERA 3.38 vs Severino ERA 5.59) |
| 4 | MIL @ MIA | MIA ML | 1.87 | 12.4% | EUR 14.96 | âŒ Loss | âˆ’14.96 | Better SP (Alcantara ERA 2.67, 30.1 IP vs Woodruff ERA 4.32) |
| 5 | STL @ HOU | STL ML | 2.20 | 5.0% | EUR 4.99 | âœ… Win | +5.99 | Better SP (Pallante ERA 4.80 vs McCullers ERA 5.87) Â· STL form 60% vs HOU 20% |
| 6 | TEX @ SEA | SEA ML | 1.75 | 7.6% | EUR 9.97 | âœ… Win | +7.48 | Better SP (Kirby ERA 3.25, 27.2 IP vs Eovaldi ERA 5.40) |
| 7 | LAD @ COL | COL ML | 3.30 | 23.6% | EUR 24.93 | âœ… Win | +57.34 | Model edge vs market price (Feltner ERA 7.30 vs Sheehan ERA 6.60 â€” both poor, COL +200 undervalue) |
| 8 | TOR @ AZ | AZ âˆ’1.5 | 2.38 | 34.2% | EUR 24.93 | âœ… Win | +34.40 | Better SP (Gallen ERA 3.60 vs Scherzer ERA 9.58) Â· AZ form 70% vs TOR 30% |
| 9 | SD @ LAA | SD ML | 1.91 | 5.7% | EUR 4.99 | âœ… Win | +4.54 | Better SP (MÃ¡rquez ERA 5.54 vs Kikuchi ERA 7.50) Â· SD form 80% vs LAA 50% |

**Notes:** Day was rescued by COL ML (+57.34) and AZ âˆ’1.5 (+34.40). COL model had the same logic as April 17 but with a different Dodgers SP â€” the market once again underpriced Colorado at home. AZ run line at 2.38 with 34% edge was the best risk-adjusted bet of the month.

---

## April 19th, 2026

**Slate:** 15 games | **Bets placed:** 8 | **Bankroll entering:** EUR 652.40  
**Result:** 3W 5L | **Day P&L: EUR âˆ’4.92**

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | TB @ PIT | PIT ML | 1.87 | 4.4% | EUR 5.93 | âœ… Win | +5.16 | Better SP (Keller ERA 2.86 vs McClanahan ERA 3.95) Â· Better run diff |
| 2 | KC @ NYY | KC ML | 2.25 | 4.6% | EUR 5.93 | âŒ Loss | âˆ’5.93 | Better SP (Ragans ERA 3.78 vs Weathers ERA 4.29) |
| 3 | MIL @ MIA | MIL ML | 1.95 | 5.9% | EUR 5.93 | âŒ Loss | âˆ’5.93 | Better SP (Misiorowski ERA 3.32 vs PÃ©rez ERA 5.40) Â· MIL better form |
| 4 | STL @ HOU | STL ML | 2.20 | 16.0% | EUR 23.72 | âœ… Win | +28.46 | Better SP (Liberatore ERA 4.29 vs Burrows ERA 6.55) Â· STL form 70% vs HOU 20% |
| 5 | NYM @ CHC | NYM ML | 2.15 | 5.4% | EUR 5.93 | âŒ Loss | âˆ’5.93 | Better SP (Myers ERA 3.46 vs Assad ERA 8.10) â€” Assad's ERA was inflated, recovered |
| 6 | CWS @ ATH | ATH âˆ’1.5 | 2.30 | 31.4% | EUR 29.64 | âŒ Loss | âˆ’29.64 | Better SP (Springs ERA 1.46 vs Schultz ERA 6.23, only 4.1 IP) Â· ATH form 70% vs CWS 30% |
| 7 | TOR @ AZ | AZ ML | 1.91 | 5.3% | EUR 5.93 | âŒ Loss | âˆ’5.93 | AZ better form (80% vs 30%) Â· Better run diff |
| 8 | DET @ BOS | DET ML | 2.25 | 10.0% | EUR 11.86 | âœ… Win | +14.82 | Better SP (Valdez ERA 3.75 vs Crochet ERA 7.58) Â· DET better form & run diff |

**Notes:** ATH âˆ’1.5 was the painful loss â€” Springs (ERA 1.46) was dominant all season but couldn't cover. NYM @ CHC shows the model's Achilles heel: high-ERA SPs can have ERA regression artifacts. Day nearly broke even despite 5 losses thanks to STL ML (+28.46) and DET ML (+14.82).

---

## April 20th, 2026

**Slate:** 10 games (Monday) | **Bets placed:** 5 | **Bankroll entering:** EUR 647.48  
**Result:** 3W 2L | **Day P&L: EUR âˆ’23.52**

| # | Game | Pick | Odds | Edge | Stake | Result | P&L | Why |
|---|---|---|---|---|---|---|---|---|
| 1 | DET @ BOS | BOS ML | 1.73 | 5.0% | EUR 6 | âœ… Win | +4.38 | Model edge vs market price (similar SPs, home edge) |
| 2 | HOU @ CLE | HOU ML | 1.90 | 6.8% | EUR 13 | âœ… Win | +11.70 | Better SP (Arrighetti ERA 1.50, only 6 IP â€” blended; Cecconi ERA 5.03) |
| 3 | STL @ MIA | STL ML | 2.05 | 8.9% | EUR 13 | âŒ Loss | âˆ’13.00 | Better SP (McGreevy ERA 2.49 vs Meyer ERA 4.12) Â· STL form 70% vs MIA 30% |
| 4 | BAL @ KC | KC ML | 1.90 | 20.6% | EUR 32 | âŒ Loss | âˆ’32.00 | Better SP (Lugo ERA 1.48 vs Bradish ERA 5.49) â€” KC's biggest stake of the season, lost |
| 5 | PHI @ CHC | CHC ML | 1.90 | 4.3% | EUR 6 | âœ… Win | +5.40 | Better SP (Rea ERA 3.63 vs Nola ERA 4.03) Â· CHC form 70% vs PHI 20% |

**Notes:** KC ML was the biggest single bet of the season (EUR 32, 20.6% edge) â€” Seth Lugo vs a struggling Bradish â€” and it lost. BAL won 7-5 despite the model's clear SP edge. The EUR 32 loss alone accounts for most of the day's deficit.

---

## April 21st, 2026 *(PENDING)*

**Slate:** 15 games | **Bets placed:** 7 | **Bankroll entering:** EUR 618.96  
**Result:** Pending â€” games not yet played

| # | Game | Pick | Odds | Edge | Stake | Why |
|---|---|---|---|---|---|---|
| 1 | HOU @ CLE | CLE âˆ’1.5 | 2.52* | 15.5% | EUR 25 | Messick ERA 1.05 vs Weiss ERA 6.75 Â· CLE better form |
| 2 | STL @ MIA | MIA ML | 1.90 | 6.1% | EUR 12 | Paddack ERA 5.59 vs May ERA 6.98 â€” both poor, home edge |
| 3 | MIL @ DET | DET ML | 1.90 | 5.2% | EUR 6 | DET form 80% vs MIL 40% Â· DET run diff +2.0 |
| 4 | NYY @ BOS | BOS ML | 1.91 | 14.9% | EUR 19 | Early ERA 2.29 vs Gil ERA 7.00 (only 9 IP) |
| 5 | PHI @ CHC | CHC ML | 1.80 | 26.4% | EUR 31 | Imanaga ERA 2.45 vs Luzardo ERA 7.94 Â· CHC form 70% vs PHI 20% |
| 6 | TOR @ LAA | LAA ML | 1.90 | 6.4% | EUR 12 | Kochanowicz ERA 3.47 vs Corbin ERA 4.66 Â· LAA run diff edge |
| 7 | LAD @ SF | SF ML | 2.55 | 9.4% | EUR 12 | Market pricing SF too long â€” Roupp ERA 2.38 vs Yamamoto ERA 2.10 (near equal) |

*CLE âˆ’1.5 selected over ML due to run-line odds providing better value at this edge level.

---

## Season Summary (Apr 14â€“20 settled)

| Date | Games | Bets | Won | Lost | Staked | P&L | Bankroll After |
|---|---|---|---|---|---|---|---|
| Apr 14 | 15 | 12 | 5 | 7 | EUR 145 | âˆ’10.25 | EUR 489.75 |
| Apr 15 | 15 | 10 | 7 | 3 | EUR 175 | +48.65 | EUR 538.40 |
| Apr 16 | 10 | 7 | 5 | 2 | EUR 120 | +64.85 | EUR 603.25 |
| Apr 17 | 15 | 8 | 3 | 5 | EUR 180 | âˆ’45.15 | EUR 558.10 |
| Apr 18 | 15 | 9 | 6 | 3 | EUR 119 | +94.30 | EUR 652.40 |
| Apr 19 | 15 | 8 | 3 | 5 | EUR 95 | âˆ’4.92 | EUR 647.48 |
| Apr 20 | 10 | 5 | 3 | 2 | EUR 70 | âˆ’23.52 | EUR 618.96 |
| **Apr 21** | 15 | 7 | â€” | â€” | EUR 117 | Pending | â€” |
| **TOTAL** | **100** | **59** | **32** | **27** | **EUR 904** | **+EUR 123.91** | **EUR 618.96** |

**Overall win rate:** 54.2% (32/59) Â· **ROI:** +13.7% on staked Â· **Bankroll growth:** +23.8%

---

## How the Model Picks â€” Quick Reference for GPT Context

Current implementation note: this section supersedes older daily rows that mention automatic run-line switching. The pipeline now evaluates run-line candidates because `USE_SPREAD_MODEL=True`. After fixing spread sign handling and ECE calculation, the spread model validates and can select RL when the RL edge is >= 3% and beats the ML edge.

The model never picks based on gut feel or narrative. Every pick follows this exact process:

1. **Collect data:** All 2026 completed games â†’ rolling team form (L5/L10/L20). Today's pitcher stats from MLB API (blended toward league average if < 30 IP to prevent small-sample distortion).

2. **Build saved features:** The active moneyline model uses the 76-feature contract saved in `moneyline_model.pkl`, including rolling team form, starter stats, bullpen quality/rest, ballpark factor, and differential features.

3. **Run logistic regression:** Model trained on 2025 full season, outputs P(home win). Away probability = 1 âˆ’ home.

4. **Compare to market:** Best UK bookmaker odds fetched via The Odds API. Implied probability = 1 / odds. Edge = model probability âˆ’ implied probability.

5. **Stake:** 1% of bankroll per 3â€“6% edge, scaling up to 5% for 20%+ edge. No bet below 3% edge.

6. **Run line check:** A separate spread model evaluates available run-line / alternate-spread prices and writes diagnostic fields. RL can be selected if the spread model validates, the RL edge is >= 3%, and it beats the ML edge. Current April 21 status: spread model loaded, validation passed, two RL bets selected on the regenerated file.

**What the model is good at:** Identifying when the market misprice a team based on SP ERA differentials and recent team form together â€” especially when one SP is clearly dominant and the market hasn't adjusted enough.

**What the model is not good at:** Very early-season ERA (small IP samples, though blending mitigates this). Bullpen quality (not a feature). Weather/injuries (only SP changes caught at afternoon check-movement step). Home field in neutral venue series.


