"""
fetch_odds.py - fetch current MMA odds from The Odds API.

Run:
    python src/fetch_odds.py
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from utils import DATA_RAW, get_logger, save_json

SPORT_KEY = "mma_mixed_martial_arts"
ODDS_URL = f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds"
DEFAULT_MARKETS = "h2h,totals"

log = get_logger("fetch_odds")


def _load_env_file() -> None:
    """Small .env loader so the app can reuse mma/.env or the repo root .env."""
    app_root = Path(__file__).resolve().parent.parent
    for env_path in [app_root / ".env", app_root.parent / ".env"]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def fetch_odds(
    api_key: str,
    regions: str = "us",
    markets: str = DEFAULT_MARKETS,
    odds_format: str = "american",
) -> list[dict]:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": odds_format,
    }
    log.info("Fetching MMA odds: regions=%s markets=%s", regions, markets)
    try:
        response = requests.get(ODDS_URL, params=params, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f" HTTP {status}." if status else ""
        raise RuntimeError(f"Could not fetch The Odds API MMA odds.{detail} Check network access, API key, and market availability.") from None
    return response.json()


def save_odds(payload: list[dict], markets: str, regions: str) -> Path:
    odds_dir = DATA_RAW / "odds"
    odds_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    wrapped = {
        "sport_key": SPORT_KEY,
        "regions": regions,
        "markets": markets,
        "fetched_at": fetched_at,
        "events": payload,
    }
    snapshot_path = odds_dir / f"mma_odds_{fetched_at}.json"
    latest_path = odds_dir / "latest.json"
    save_json(wrapped, snapshot_path)
    save_json(wrapped, latest_path)
    log.info("Saved %s events to %s", len(payload), latest_path)
    return latest_path


def main() -> None:
    _load_env_file()
    parser = argparse.ArgumentParser(description="Fetch live UFC/MMA odds.")
    parser.add_argument("--regions", default=os.getenv("ODDS_REGIONS", "us"))
    parser.add_argument("--markets", default=os.getenv("ODDS_MARKETS", DEFAULT_MARKETS))
    parser.add_argument("--odds-format", default="american")
    args = parser.parse_args()

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise SystemExit("ODDS_API_KEY is not set. Add it to the environment or mma/.env.")

    payload = fetch_odds(api_key, args.regions, args.markets, args.odds_format)
    save_odds(payload, args.markets, args.regions)


if __name__ == "__main__":
    main()
