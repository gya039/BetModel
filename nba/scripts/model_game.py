"""
NBA spread and moneyline model.

Spread model:  predicts point differential (home - away) — regression
Moneyline model: predicts home win probability — classification

Features: rolling net rating, rest, back-to-back, home court.

Usage:
    python model_game.py              # train both models
    python model_game.py --eval       # show backtest on last season
"""

import argparse
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import (mean_absolute_error, accuracy_score,
                              log_loss, brier_score_loss)

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR     = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "HOME_L10_NET",
    "AWAY_L10_NET",
    "HOME_L5_NET",
    "AWAY_L5_NET",
    "HOME_L10_WIN_PCT",
    "AWAY_L10_WIN_PCT",
    "HOME_L5_WIN_PCT",
    "AWAY_L5_WIN_PCT",
    "HOME_L10_PTS_FOR",
    "AWAY_L10_PTS_FOR",
    "HOME_L10_PTS_AGAINST",
    "AWAY_L10_PTS_AGAINST",
    "HOME_REST_DAYS",
    "AWAY_REST_DAYS",
    "HOME_IS_B2B",
    "AWAY_IS_B2B",
    # Net rest advantage
    "REST_ADVANTAGE",
]


def load(min_games: int = 5) -> pd.DataFrame:
    df = pd.read_csv(PROCESSED_DIR / "games_processed.csv", parse_dates=["GAME_DATE"])
    df["REST_ADVANTAGE"] = df["HOME_REST_DAYS"] - df["AWAY_REST_DAYS"]
    # Drop first few games of season where rolling stats are warm-up noise
    df = df.dropna(subset=FEATURES + ["POINT_DIFF", "HOME_WIN"])
    return df.sort_values("GAME_DATE").reset_index(drop=True)


def train_spread(df: pd.DataFrame):
    """Ridge regression on point differential."""
    X = df[FEATURES].values
    y = df["POINT_DIFF"].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    scaler = StandardScaler()
    model  = Ridge(alpha=1.0)
    model.fit(scaler.fit_transform(X_train), y_train)

    preds = model.predict(scaler.transform(X_test))
    mae   = mean_absolute_error(y_test, preds)
    # Accuracy against ATS (did we predict the right side of 0?)
    ats   = (np.sign(preds) == np.sign(y_test)).mean()

    print(f"\nSpread model:")
    print(f"  MAE       : {mae:.2f} pts")
    print(f"  ATS acc   : {ats:.1%}  (50% = coin flip)")

    with open(MODEL_DIR / "spread_model.pkl", "wb") as f:
        pickle.dump({"model": model, "scaler": scaler, "features": FEATURES}, f)
    print(f"  Saved -> models/spread_model.pkl")
    return model, scaler


def train_moneyline(df: pd.DataFrame):
    """Logistic regression on home win."""
    X = df[FEATURES].values
    y = df["HOME_WIN"].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    scaler = StandardScaler()
    model  = LogisticRegression(max_iter=500)
    model.fit(scaler.fit_transform(X_train), y_train)

    probs = model.predict_proba(scaler.transform(X_test))[:, 1]
    preds = (probs > 0.5).astype(int)

    print(f"\nMoneyline model:")
    print(f"  Accuracy  : {accuracy_score(y_test, preds):.1%}")
    print(f"  Log loss  : {log_loss(y_test, probs):.4f}")
    print(f"  Brier     : {brier_score_loss(y_test, probs):.4f}")

    with open(MODEL_DIR / "moneyline_model.pkl", "wb") as f:
        pickle.dump({"model": model, "scaler": scaler, "features": FEATURES}, f)
    print(f"  Saved -> models/moneyline_model.pkl")
    return model, scaler


def predict_spread(features: dict) -> float:
    """Predict point differential for a single game."""
    with open(MODEL_DIR / "spread_model.pkl", "rb") as f:
        saved = pickle.load(f)
    X = pd.DataFrame([features])[saved["features"]].values
    return float(saved["model"].predict(saved["scaler"].transform(X))[0])


def predict_moneyline(features: dict) -> dict:
    """Predict home/away win probabilities for a single game."""
    with open(MODEL_DIR / "moneyline_model.pkl", "rb") as f:
        saved = pickle.load(f)
    X    = pd.DataFrame([features])[saved["features"]].values
    prob = float(saved["model"].predict_proba(saved["scaler"].transform(X))[0][1])
    return {"home": round(prob, 4), "away": round(1 - prob, 4)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", action="store_true")
    args = parser.parse_args()

    df = load()
    print(f"Loaded {len(df)} games across {df['SEASON'].nunique()} seasons")
    train_spread(df)
    train_moneyline(df)
