"""
Train a match outcome prediction model (Home / Draw / Away).
Uses logistic regression as a baseline — swap in XGBoost later.
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
MODEL_DIR = Path(__file__).parent.parent.parent / "football" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "home_avg_scored",
    "home_avg_conceded",
    "away_avg_scored",
    "away_avg_conceded",
    "impl_home",
    "impl_draw",
    "impl_away",
]


def load_data(league: str) -> tuple[pd.DataFrame, pd.Series]:
    df = pd.read_csv(PROCESSED_DIR / f"{league}_processed.csv")
    df = df.dropna(subset=FEATURES + ["result"])
    X = df[FEATURES]
    y = df["result"].map({1: 0, 0: 1, -1: 2})  # H=0, D=1, A=2
    return X, y


def train(league: str):
    X, y = load_data(league)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    model = LogisticRegression(multi_class="multinomial", max_iter=500)
    model.fit(X_train_scaled, y_train)

    preds = model.predict(X_test_scaled)
    probs = model.predict_proba(X_test_scaled)

    print(f"\n{league.upper()} Model Results:")
    print(f"  Accuracy: {accuracy_score(y_test, preds):.3f}")
    print(f"  Log Loss: {log_loss(y_test, probs):.3f}")

    # Save model and scaler
    with open(MODEL_DIR / f"{league}_model.pkl", "wb") as f:
        pickle.dump({"model": model, "scaler": scaler}, f)
    print(f"  Saved to models/{league}_model.pkl")

    return model, scaler


def predict(league: str, features: dict) -> dict:
    """
    Predict probabilities for a single match.
    features: dict with keys matching FEATURES list
    Returns: {'home': float, 'draw': float, 'away': float}
    """
    with open(MODEL_DIR / f"{league}_model.pkl", "rb") as f:
        saved = pickle.load(f)

    model = saved["model"]
    scaler = saved["scaler"]

    X = pd.DataFrame([features])[FEATURES]
    X_scaled = scaler.transform(X)
    probs = model.predict_proba(X_scaled)[0]

    return {"home": round(probs[0], 4), "draw": round(probs[1], 4), "away": round(probs[2], 4)}


if __name__ == "__main__":
    for league in ["laliga", "bundesliga"]:
        train(league)
