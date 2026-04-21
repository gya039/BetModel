# Diamond Edge MLB Model Upgrade Summary

## What Changed

Diamond Edge now keeps the existing morning and afternoon workflow, but the model contract is richer and safer:

- The saved moneyline model now uses 76 features instead of the previous 34-feature set.
- Starting pitcher inputs now include ERA, WHIP, K/9, FIP-style estimate, BB/9, K-BB%, HR/9, IP, and handedness.
- Low-IP pitcher metrics are blended toward league averages through one shared shrinkage framework.
- Full-game moneyline features now include bullpen/team pitching quality proxies plus bullpen rest/workload fields.
- Probabilities are sigmoid-calibrated on a chronological calibration slice before saving.
- Market edge is calculated against no-vig two-sided moneyline probability when both sides are available.
- Automatic run-line selection from moneyline edge is disabled for singles and accumulators.
- Walk-forward diagnostics are generated under `mlb/predictions/diagnostics/`.

## Morning Vs Updated Workflow

`/generate-mlb-predictions` still maps to the heavy morning pipeline:

- fetch completed 2026 games through yesterday
- build rolling team state
- fetch probable pitchers and current pitcher stats
- build pitcher, bullpen, ballpark, and team-form features
- fetch odds and archive morning odds
- calculate moneyline probabilities, no-vig edges, stake tiers, and reports
- write JSON, Markdown, and XLSX files for the website flow

`/check-movement` remains the afternoon/pregame update:

- keeps live and final games frozen
- fetches current odds
- checks movement, transactions, lineups, and starting pitcher changes
- reruns affected TBD/changed-SP games
- recalculates moneyline edge and stake using current no-vig prices
- writes separate `(Updated)` JSON, Markdown, and XLSX files
- archives pregame odds for CLV settlement

## Run-Line Logic

Run lines are no longer selected from moneyline probability.

The app still displays available run-line prices for context, but:

- `useRl` is set to `false` in the morning pipeline
- updated predictions clear any inherited run-line flag
- accumulator legs are moneyline only

A future run-line model should train on historical cover labels before run lines are recommended again.

## Bullpen Features

The feature contract includes:

- `HOME/AWAY_BP_ERA`
- `HOME/AWAY_BP_WHIP`
- `HOME/AWAY_BP_K_BB`
- `HOME/AWAY_BP_IP_LAST_3D`
- `HOME/AWAY_BP_IP_YESTERDAY`
- `HOME/AWAY_BP_RELIEVERS_LAST_3D`
- `HOME/AWAY_BP_RELIEVERS_YESTERDAY`
- `HOME/AWAY_BP_TOP_USED_YESTERDAY`
- all home-away differential versions

Historical training uses team pitching quality as the conservative bullpen quality proxy when player-level bullpen team attribution is unavailable. The live pipeline layers recent boxscore workload on top when available and falls back to neutral workload values if a boxscore cannot be read.

## Pitcher Features

The starter feature set now includes:

- ERA
- WHIP
- K/9
- computed FIP-style estimate
- BB/9
- K-BB%
- HR/9
- capped IP
- left-handed flag
- all core home-away differential versions

The live and training code both use the same blending helper so tiny samples are regressed toward league-average fills.

## Validation And Calibration

`python mlb/scripts/diagnostics.py` writes:

- `mlb/predictions/diagnostics/walk_forward_diagnostics.json`
- `mlb/predictions/diagnostics/walk_forward_diagnostics.md`

The diagnostics include:

- monthly walk-forward folds
- log loss, Brier score, and accuracy
- calibration buckets and expected calibration error
- threshold comparison for 3%, 4%, 5%, and 6%
- top 3, top 5, and all qualifying bets per slate
- edge bucket performance
- odds range performance
- favorite vs underdog performance
- feature contribution summary

CLV remains tracked in settlement from archived morning/pregame odds; historical walk-forward CLV is marked unavailable unless old closing odds are present.

## Staking

The stake schedule was intentionally left unchanged:

- edge < 1%: 0%
- 1-3%: 0.5%
- 3-6%: 1%
- 6-10%: 2%
- 10-15%: 3%
- 15-20%: 4%
- 20%+: 5%

The upgraded model can naturally change stake size by changing probability or edge, but the staking formula itself was not redesigned.

## Smoke Tests Run

- `python mlb/scripts/fetch_data.py`
- `python mlb/scripts/preprocess.py`
- `python mlb/scripts/model.py --eval`
- `python mlb/scripts/diagnostics.py`
- `python mlb/scripts/predict_today.py --date 2026-04-21`
- `python mlb/scripts/check_movement.py --date 2026-04-21`

The smoke run confirmed:

- morning reports still write JSON/Markdown/XLSX
- morning odds archive still writes
- updated reports still write
- pregame odds archive still writes
- `useRl` is false in the daily JSON
- accumulator legs are moneyline only
- the model loads 76 features
- bullpen features are present in prediction rows
