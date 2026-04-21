"""
NBA player prop model.

For each prop (PTS, REB, AST, 3PM) this is a regression model that predicts
the expected stat value. Compare against the book's line to find over/under value.

Edge logic:
    book line = 22.5 pts,  model predicts 25.1 pts  ->  bet OVER
    book line = 22.5 pts,  model predicts 19.8 pts  ->  bet UNDER

Key features:
  - Rolling 5/10 game averages
  - Minutes (proxy for opportunity)
  - Opponent defensive strength
  - Rest/back-to-back
  - Home/away

Usage:
    python model_props.py              # train all prop models
    python model_props.py --prop PTS   # single prop
"""

import argparse
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error

PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
MODEL_DIR     = Path(__file__).parent.parent / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# Min games played to include a player row (avoids cold-start noise)
MIN_GAMES_PLAYED = 10

PROP_CONFIG = {
    "PTS": {
        "target":   "ACT_PTS",
        "features": [
            "L5_PTS", "L10_PTS",
            "L5_MIN", "L10_MIN",
            "L5_FG3M",
            "OPP_DEF_L10",
            "IS_HOME", "IS_B2B", "REST_DAYS",
        ],
    },
    "REB": {
        "target":   "ACT_REB",
        "features": [
            "L5_REB", "L10_REB",
            "L5_MIN", "L10_MIN",
            "OPP_DEF_L10",
            "IS_HOME", "IS_B2B", "REST_DAYS",
        ],
    },
    "AST": {
        "target":   "ACT_AST",
        "features": [
            "L5_AST", "L10_AST",
            "L5_MIN", "L10_MIN",
            "OPP_DEF_L10",
            "IS_HOME", "IS_B2B", "REST_DAYS",
        ],
    },
    "3PM": {
        "target":   "ACT_3PM",
        "features": [
            "L5_FG3M", "L10_FG3M",
            "L5_MIN",  "L10_MIN",
            "OPP_DEF_L10",
            "IS_HOME", "IS_B2B", "REST_DAYS",
        ],
    },
}


def load() -> pd.DataFrame:
    df = pd.read_csv(PROCESSED_DIR / "players_processed.csv", parse_dates=["GAME_DATE"])

    # Drop rows where player has fewer than MIN_GAMES_PLAYED in rolling window
    # (L10_MIN close to 0 means they just started playing)
    df = df[df["L10_MIN"] > 5].copy()
    df = df.fillna({"OPP_DEF_L10": df["OPP_DEF_L10"].median()})
    return df.sort_values("GAME_DATE").reset_index(drop=True)


def train_prop(df: pd.DataFrame, prop: str):
    cfg      = PROP_CONFIG[prop]
    target   = cfg["target"]
    features = cfg["features"]

    df_clean = df.dropna(subset=features + [target])
    X = df_clean[features].values
    y = df_clean[target].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )
    scaler = StandardScaler()
    model  = Ridge(alpha=1.0)
    model.fit(scaler.fit_transform(X_train), y_train)

    preds = model.predict(scaler.transform(X_test))
    mae   = mean_absolute_error(y_test, preds)

    # Over/under accuracy: if pred > actual_mean, did actual exceed mean too?
    # More useful: how often does our over/under call match reality?
    median_line = np.median(y_train)
    pred_side   = (preds > median_line)
    act_side    = (y_test > median_line)
    ou_acc      = (pred_side == act_side).mean()

    print(f"\n{prop} model:")
    print(f"  MAE          : {mae:.2f}")
    print(f"  O/U accuracy : {ou_acc:.1%}  (50% = coin flip)")
    print(f"  Train samples: {len(X_train)}")

    with open(MODEL_DIR / f"prop_{prop.lower()}_model.pkl", "wb") as f:
        pickle.dump({
            "model":    model,
            "scaler":   scaler,
            "features": features,
            "target":   target,
            "prop":     prop,
            "mae":      mae,
        }, f)
    print(f"  Saved -> models/prop_{prop.lower()}_model.pkl")
    return model, scaler


def predict_prop(prop: str, features: dict) -> dict:
    """
    Predict expected stat value for a player.

    Returns:
        {
          "expected": 24.3,       # model's point estimate
          "mae": 6.1,             # model error — use as confidence band
          "over_line": <float>,   # call over if book line < expected - threshold
          "under_line": <float>,  # call under if book line > expected + threshold
        }
    """
    path = MODEL_DIR / f"prop_{prop.lower()}_model.pkl"
    with open(path, "rb") as f:
        saved = pickle.load(f)

    X        = pd.DataFrame([features])[saved["features"]].values
    expected = float(saved["model"].predict(saved["scaler"].transform(X))[0])
    mae      = saved["mae"]

    return {
        "expected":   round(expected, 2),
        "mae":        round(mae, 2),
        "over_line":  round(expected - mae * 0.5, 2),   # conservative over threshold
        "under_line": round(expected + mae * 0.5, 2),   # conservative under threshold
    }


def find_prop_value(prop: str, player_features: dict, book_line: float) -> dict | None:
    """
    Given a book line, return a bet recommendation or None.

    Recommends OVER  if book_line < expected - 0.5 * MAE
    Recommends UNDER if book_line > expected + 0.5 * MAE
    """
    pred = predict_prop(prop, player_features)
    exp  = pred["expected"]
    mae  = pred["mae"]

    if book_line < exp - mae * 0.5:
        return {
            "direction":  "OVER",
            "book_line":  book_line,
            "expected":   exp,
            "edge_pts":   round(exp - book_line, 2),
            "confidence": round((exp - book_line) / mae, 2),
        }
    if book_line > exp + mae * 0.5:
        return {
            "direction":  "UNDER",
            "book_line":  book_line,
            "expected":   exp,
            "edge_pts":   round(book_line - exp, 2),
            "confidence": round((book_line - exp) / mae, 2),
        }
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prop", choices=list(PROP_CONFIG.keys()),
                        help="Train single prop. Omit for all.")
    args = parser.parse_args()

    df = load()
    print(f"Loaded {len(df)} player-game rows")

    props = [args.prop] if args.prop else list(PROP_CONFIG.keys())
    for prop in props:
        train_prop(df, prop)
