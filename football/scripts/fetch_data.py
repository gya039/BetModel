"""
Fetch historical football match data from football-data.co.uk (free).
Covers La Liga (SP1), Bundesliga (D1), and more.
"""

import requests
import pandas as pd
from pathlib import Path

# Free CSV data from football-data.co.uk
LEAGUE_URLS = {
    "laliga": "https://www.football-data.co.uk/mmz4281/{season}/SP1.csv",
    "bundesliga": "https://www.football-data.co.uk/mmz4281/{season}/D1.csv",
    "premier_league": "https://www.football-data.co.uk/mmz4281/{season}/E0.csv",
    "irish_premier": "https://www.football-data.co.uk/mmz4281/{season}/IRL1.csv",
}

RAW_DIR = Path(__file__).parent.parent / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def fetch_season(league: str, season: str) -> pd.DataFrame:
    """
    league: key from LEAGUE_URLS (e.g. 'laliga')
    season: format '2324' for 2023/24, '2425' for 2024/25
    """
    url = LEAGUE_URLS[league].format(season=season)
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    filepath = RAW_DIR / f"{league}_{season}.csv"
    with open(filepath, "wb") as f:
        f.write(response.content)

    df = pd.read_csv(filepath)
    print(f"Fetched {len(df)} matches: {league} {season}")
    return df


def fetch_multiple(league: str, seasons: list[str]) -> pd.DataFrame:
    """Fetch and combine multiple seasons."""
    frames = []
    for season in seasons:
        try:
            df = fetch_season(league, season)
            df["season"] = season
            frames.append(df)
        except Exception as e:
            print(f"Failed {league} {season}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


if __name__ == "__main__":
    seasons = ["2122", "2223", "2324", "2425"]
    for league in ["laliga", "bundesliga"]:
        df = fetch_multiple(league, seasons)
        out = RAW_DIR / f"{league}_combined.csv"
        df.to_csv(out, index=False)
        print(f"Saved {len(df)} rows to {out}")
