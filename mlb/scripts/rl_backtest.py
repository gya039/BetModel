"""
Historical run-line backtest for the spread model.

This report uses the saved SpreadModel and the chronological holdout slice from
games_processed.csv. Historical bookmaker run-line odds are not available, so
the test uses a flat -110 / 1.909 assumption and evaluates the predicted winner
on -1.5 only. Treat it as a selection/gate diagnostic, not a realized market P&L.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.append(str(Path(__file__).parent.parent.parent))
from mlb.scripts.spread_model import MODEL_PATH, PROC_DIR, SpreadModel, _chronological_split

REPORT_DIR = Path(__file__).parent.parent / "reports"
ODDS_ARCHIVE_DIR = Path(__file__).parent.parent / "predictions" / "odds_archive"
ASSUMED_ODDS = 1.909
EDGE_THRESHOLD = 0.03


def _load_holdout(csv_path: Path, model: SpreadModel) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df = df.dropna(subset=[
        "HOME_L10_WIN_PCT", "AWAY_L10_WIN_PCT",
        "HOME_L10_RD", "AWAY_L10_RD",
        "HOME_L20_WIN_PCT", "AWAY_L20_WIN_PCT",
        "HOME_L20_RD", "AWAY_L20_RD",
    ])
    if "point_diff" not in df.columns:
        df["point_diff"] = df["home_score"].astype(float) - df["away_score"].astype(float)

    for feature in model.features or []:
        if feature not in df.columns:
            df[feature] = 0.0
    _, test_df = _chronological_split(df, test_fraction=0.20)
    return test_df.reset_index(drop=True)


def _spread_prob(model: SpreadModel, feat_vec: list[float], side: str) -> float:
    pred = model.predict_margin(feat_vec)
    if side == "home":
        return float(1.0 - norm.cdf(1.5, loc=pred, scale=model.residual_std))
    return float(norm.cdf(-1.5, loc=pred, scale=model.residual_std))


def _covered(actual_margin: float, side: str) -> bool:
    if side == "home":
        return actual_margin > 1.5
    return actual_margin < -1.5


def _load_archived_odds(archive_dir: Path, game_date: str) -> dict:
    for suffix in ("pregame", "morning"):
        path = archive_dir / f"{game_date}_{suffix}_odds.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _historical_rl_odds(row: pd.Series, side: str, archive_dir: Path) -> float | None:
    game_date = str(row.get("game_date", ""))[:10]
    if not game_date:
        return None
    archived = _load_archived_odds(archive_dir, game_date)
    if not archived:
        return None
    key = f"{row.get('home_team')}_{row.get('away_team')}"
    market = archived.get(key)
    if not market:
        return None
    point_key = "home_rl_point" if side == "home" else "away_rl_point"
    odds_key = "home_rl" if side == "home" else "away_rl"
    if market.get(point_key) is None or float(market.get(point_key)) != -1.5:
        return None
    odds = market.get(odds_key)
    return float(odds) if odds and float(odds) > 1.0 else None


def _reason(model: SpreadModel, edge: float) -> str:
    if edge <= 0:
        return "no_positive_ev"
    if edge < EDGE_THRESHOLD:
        return "below_3pct_edge"
    if model.validation_passed is not True:
        checks = model._validation_check_summary()
        if not checks["ece_pass"]:
            return "failed_ece_gate"
        if not checks["roi_pass"]:
            return "failed_roi_gate"
        if not checks["sanity_pass"]:
            return "failed_sanity_gate"
        return "model_validation_failed"
    return "eligible"


def build_backtest(
    model_path: Path = MODEL_PATH,
    csv_path: Path = PROC_DIR / "games_processed.csv",
    odds_archive_dir: Path = ODDS_ARCHIVE_DIR,
) -> dict:
    model = SpreadModel.load(model_path)
    test_df = _load_holdout(csv_path, model)
    features = model.features or []
    implied = 1.0 / ASSUMED_ODDS

    rows = []
    for _, row in test_df.iterrows():
        feat_vec = row[features].fillna(0.0).values.astype(float).tolist()
        pred_margin = model.predict_margin(feat_vec)
        side = "home" if pred_margin >= 0 else "away"
        prob = _spread_prob(model, feat_vec, side)
        market_odds = _historical_rl_odds(row, side, odds_archive_dir)
        odds = market_odds or ASSUMED_ODDS
        implied_used = 1.0 / odds
        edge = round(prob - implied_used, 4)
        reason = _reason(model, edge)
        covered = _covered(float(row["point_diff"]), side)
        rows.append({
            "game_date": str(row.get("game_date", ""))[:10],
            "side": side,
            "line": -1.5,
            "pred_margin": round(pred_margin, 4),
            "cover_prob": round(prob, 4),
            "edge": edge,
            "odds": round(float(odds), 3),
            "odds_source": "archive" if market_odds else "assumed",
            "covered": bool(covered),
            "rejection_reason": reason,
            "eligible": reason == "eligible",
        })

    counts = Counter(r["rejection_reason"] for r in rows)
    eligible = [r for r in rows if r["eligible"]]
    wins = sum(1 for r in eligible if r["covered"])
    losses = len(eligible) - wins
    profit = sum((r["odds"] - 1.0) if r["covered"] else -1.0 for r in eligible)
    roi = profit / len(eligible) if eligible else 0.0
    archived_rows = sum(1 for r in rows if r["odds_source"] == "archive")

    return {
        "model_path": str(model_path),
        "csv_path": str(csv_path),
        "n_games": len(rows),
        "assumed_odds": ASSUMED_ODDS,
        "odds_archive_dir": str(odds_archive_dir),
        "archived_market_rows": archived_rows,
        "historical_market_odds_available": archived_rows > 0,
        "edge_threshold": EDGE_THRESHOLD,
        "validation_passed": model.validation_passed,
        "validation_reasons": model.validation_reasons,
        "reason_counts": dict(counts),
        "eligible_bets": len(eligible),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(eligible), 4) if eligible else 0.0,
        "profit_units": round(float(profit), 4),
        "roi": round(float(roi), 4),
        "rows": rows,
    }


def write_reports(report: dict) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "rl_backtest_report.json"
    md_path = REPORT_DIR / "rl_backtest_report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    reason_lines = "\n".join(
        f"| {reason} | {count} |"
        for reason, count in sorted(report["reason_counts"].items(), key=lambda kv: kv[0])
    )
    md = f"""# Run-Line Backtest Report

This is a historical selection/gate diagnostic for the spread model.

The script uses archived historical run-line odds when matching odds archive files are available. Otherwise it falls back to flat decimal odds of {report['assumed_odds']}. The current holdout set has {report['archived_market_rows']} rows with archived market odds, so treat the fallback portion as a selection diagnostic rather than true bookmaker P&L.

## Summary

| Metric | Value |
|---|---:|
| Holdout games | {report['n_games']} |
| Archived market odds rows | {report['archived_market_rows']} |
| Validation passed | {report['validation_passed']} |
| Eligible bets | {report['eligible_bets']} |
| Wins | {report['wins']} |
| Losses | {report['losses']} |
| Win rate | {report['win_rate']:.1%} |
| Profit | {report['profit_units']:+.2f} units |
| ROI | {report['roi']:.1%} |

## Rejection Reasons

| Reason | Count |
|---|---:|
{reason_lines}

## Validation Reasons

{chr(10).join(f'- {r}' for r in report['validation_reasons'])}
"""
    md_path.write_text(md, encoding="utf-8")
    return json_path, md_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest spread-model RL gates on the holdout set.")
    parser.add_argument("--model", default=str(MODEL_PATH))
    parser.add_argument("--csv", default=str(PROC_DIR / "games_processed.csv"))
    parser.add_argument("--odds-archive", default=str(ODDS_ARCHIVE_DIR))
    args = parser.parse_args()

    result = build_backtest(Path(args.model), Path(args.csv), Path(args.odds_archive))
    json_path, md_path = write_reports(result)
    print(f"RL backtest JSON: {json_path}")
    print(f"RL backtest MD:   {md_path}")
    print(json.dumps({k: result[k] for k in result if k != "rows"}, indent=2))
