"""
Train a match outcome model (Home / Draw / Away).
No bookmaker odds in the feature set — model must find its own signal.

Features: Elo, last 5/10 form, H2H, shots on target.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, log_loss
from sklearn.preprocessing import StandardScaler
import pickle

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    # Elo
    "home_elo",
    "away_elo",
    "elo_diff",
    # Last 5 overall
    "home_l5_ppg",
    "home_l5_gf",
    "home_l5_ga",
    "home_l5_gd",
    "away_l5_ppg",
    "away_l5_gf",
    "away_l5_ga",
    "away_l5_gd",
    # Last 10 overall
    "home_l10_ppg",
    "home_l10_gf",
    "home_l10_ga",
    "away_l10_ppg",
    "away_l10_gf",
    "away_l10_ga",
    # Venue-specific last 5
    "home_h5_ppg",
    "home_h5_gf",
    "home_h5_ga",
    "away_a5_ppg",
    "away_a5_gf",
    "away_a5_ga",
    # H2H
    "h2h_hw_rate",
    "h2h_draw_rate",
    "h2h_avg_goals",
    # Shots on target ratio
    "home_sot",
    "away_sot",
]


def load_data(league: str) -> tuple[pd.DataFrame, pd.Series]:
    df = pd.read_csv(PROCESSED_DIR / f"{league}_processed.csv")
    df = df.dropna(subset=FEATURES + ["result"])
    X = df[FEATURES]
    y = df["result"].map({1: 0, 0: 1, -1: 2})  # H=0, D=1, A=2
    return X, y


def train(league: str):
    X, y = load_data(league)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    model = LogisticRegression(max_iter=500)
    model.fit(X_train_s, y_train)

    preds = model.predict(X_test_s)
    probs = model.predict_proba(X_test_s)

    print(f"\n{league.upper()} Model Results:")
    print(f"  Accuracy : {accuracy_score(y_test, preds):.3f}")
    print(f"  Log Loss : {log_loss(y_test, probs):.3f}")
    print(f"  Samples  : {len(X_train)} train / {len(X_test)} test")

    with open(MODEL_DIR / f"{league}_model.pkl", "wb") as f:
        pickle.dump({"model": model, "scaler": scaler, "features": FEATURES}, f)
    print(f"  Saved -> models/{league}_model.pkl")

    return model, scaler


def predict(league: str, features: dict) -> dict:
    """
    Predict outcome probabilities for a single match.
    features: dict with keys matching FEATURES list.
    Returns: {'home': float, 'draw': float, 'away': float}
    """
    with open(MODEL_DIR / f"{league}_model.pkl", "rb") as f:
        saved = pickle.load(f)

    model  = saved["model"]
    scaler = saved["scaler"]

    X = pd.DataFrame([features])[FEATURES]
    X_s = scaler.transform(X)
    probs = model.predict_proba(X_s)[0]

    return {
        "home": round(float(probs[0]), 4),
        "draw": round(float(probs[1]), 4),
        "away": round(float(probs[2]), 4),
    }


if __name__ == "__main__":
    for league in ["laliga", "bundesliga"]:
        train(league)
