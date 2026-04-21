# Run-Line Backtest Report

This is a historical selection/gate diagnostic for the spread model.

The script uses archived historical run-line odds when matching odds archive files are available. Otherwise it falls back to flat decimal odds of 1.909. The current holdout set has 0 rows with archived market odds, so treat the fallback portion as a selection diagnostic rather than true bookmaker P&L.

## Summary

| Metric | Value |
|---|---:|
| Holdout games | 479 |
| Archived market odds rows | 0 |
| Validation passed | True |
| Eligible bets | 4 |
| Wins | 3 |
| Losses | 1 |
| Win rate | 75.0% |
| Profit | +1.73 units |
| ROI | 43.2% |

## Rejection Reasons

| Reason | Count |
|---|---:|
| below_3pct_edge | 10 |
| eligible | 4 |
| no_positive_ev | 465 |

## Validation Reasons

- All validation thresholds passed.
