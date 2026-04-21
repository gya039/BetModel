# MLB Walk-Forward Diagnostics

Games: 2393
Overall log loss: 0.6958
Overall Brier: 0.2513
Overall accuracy: 50.2%
Expected calibration error: 0.0355

## Folds
| Fold | Train | Test | Log Loss | Brier | Accuracy |
|---|---:|---:|---:|---:|---:|
| 2025-07 | 1230 | 368 | 0.7028 | 0.2548 | 45.1% |
| 2025-08 | 1598 | 421 | 0.6955 | 0.2511 | 51.8% |
| 2025-09 | 2019 | 374 | 0.6892 | 0.248 | 53.5% |

## Calibration
| Bucket | Games | Avg Pred | Actual | Diff |
|---|---:|---:|---:|---:|
| 40%-45% | 46 | 42.9% | 56.5% | +13.6% |
| 45%-50% | 128 | 47.9% | 63.3% | +15.3% |
| 50%-55% | 796 | 52.5% | 52.9% | +0.4% |
| 55%-60% | 165 | 56.4% | 49.1% | -7.3% |
| 60%-65% | 16 | 61.6% | 62.5% | +0.9% |
| 65%-70% | 1 | 68.7% | 100.0% | +31.3% |

## Threshold Comparison
| Min Edge | Selection | Bets | ROI | P&L | Drawdown | Win % |
|---:|---|---:|---:|---:|---:|---:|
| 3% | top_3 | 114 | -5.56% | EUR -55.60 | EUR -129.75 | 48.2% |
| 3% | top_5 | 159 | -4.14% | EUR -53.25 | EUR -130.55 | 48.4% |
| 3% | all | 197 | -10.49% | EUR -160.00 | EUR -227.25 | 44.2% |
| 4% | top_3 | 90 | -7.66% | EUR -67.40 | EUR -113.20 | 45.6% |
| 4% | top_5 | 118 | -3.68% | EUR -39.75 | EUR -102.90 | 48.3% |
| 4% | all | 137 | -9.22% | EUR -113.00 | EUR -153.80 | 44.5% |
| 5% | top_3 | 77 | -4.88% | EUR -39.80 | EUR -87.10 | 48.0% |
| 5% | top_5 | 95 | -1.30% | EUR -12.50 | EUR -91.35 | 50.5% |
| 5% | all | 109 | -5.60% | EUR -60.75 | EUR -116.90 | 47.7% |
| 6% | top_3 | 61 | -3.80% | EUR -27.95 | EUR -96.60 | 49.2% |
| 6% | top_5 | 71 | -1.20% | EUR -10.10 | EUR -95.80 | 50.7% |
| 6% | all | 81 | -5.14% | EUR -48.60 | EUR -111.60 | 48.1% |

## Top Feature Contributions
| Feature | Mean |
|---|---:|
| K_BB_PCT_DIFF | 0.49084 |
| HOME_SP_K_BB_PCT | 0.39791 |
| AWAY_SP_K_BB_PCT | 0.28976 |
| K9_DIFF | 0.26751 |
| HOME_SP_K9 | 0.24501 |
| HOME_L5_RD | 0.22338 |
| HOME_BP_WHIP | 0.20243 |
| AWAY_SP_BB9 | 0.15608 |
| BB9_DIFF | 0.14306 |
| HOME_SP_FIP | 0.14023 |
| FIP_DIFF | 0.13059 |
| AWAY_SP_K9 | 0.13042 |
| HOME_BP_ERA | 0.12123 |
| HOME_L10_RUNS_AGN | 0.12113 |
| AWAY_L10_WIN_PCT | 0.11886 |
| HOME_L5_WIN_PCT | 0.1173 |
| HOME_SP_ERA | 0.11591 |
| BP_WHIP_DIFF | 0.11082 |
| AWAY_SP_IP | 0.10999 |
| HOME_SP_WHIP | 0.0893 |