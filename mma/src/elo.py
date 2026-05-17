"""
elo.py — Elo rating system for UFC fighters.

Tracks fighter quality over time. Each fight updates both fighters' ratings
so that pre-fight Elo captures historical performance quality without leakage.

Design choices:
  - Default rating: 1500 (arbitrary scale)
  - K-factor: 32 (standard starting point; adjust after tuning)
  - Margin of victory multiplier: finish gets 1.2x update, decision gets 0.9x
  - Only W/L update ratings (draws and NC are skipped)
  - Fights are processed strictly in chronological order

Usage:
    sys = EloSystem()
    sys.replay_history(fighters)          # build ratings from all history
    rating = sys.rating("fighter_id")     # current rating
    pre = sys.pre_fight_ratings(f_id, opp_id)  # ratings before their fight
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import NamedTuple

from features import parse_date

DEFAULT_RATING = 1500.0
K = 32.0

# Multiplier for margin of victory (finish vs decision)
_MoV = {"KO/TKO": 1.2, "Submission": 1.2, "Decision": 0.9, "DQ": 0.9}


class FightResult(NamedTuple):
    fight_url:   str
    event_date:  date
    winner_id:   str
    loser_id:    str
    method:      str


class EloSystem:
    """
    Maintains fighter Elo ratings updated fight-by-fight in chronological order.

    After calling replay_history(), ratings reflect the state after all
    historical fights. For pre-fight ratings, use pre_fight_ratings().
    """

    def __init__(self, k: float = K, default: float = DEFAULT_RATING) -> None:
        self.k = k
        self.default = default
        self._ratings: dict[str, float] = defaultdict(lambda: self.default)
        # Snapshot of rating before each fight: {fight_url: {fighter_id: rating}}
        self._pre_fight: dict[str, dict[str, float]] = {}

    def rating(self, fighter_id: str) -> float:
        return self._ratings[fighter_id]

    def pre_fight_rating(self, fighter_id: str, fight_url: str) -> float:
        """Return the fighter's Elo just BEFORE the given fight."""
        snapshot = self._pre_fight.get(fight_url, {})
        return snapshot.get(fighter_id, self.default)

    def expected(self, ra: float, rb: float) -> float:
        return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))

    def update(self, winner_id: str, loser_id: str, method: str, fight_url: str) -> None:
        """Apply a single fight result and update both fighters' ratings."""
        ra = self._ratings[winner_id]
        rb = self._ratings[loser_id]

        # Snapshot pre-fight ratings (before this update)
        self._pre_fight.setdefault(fight_url, {})[winner_id] = ra
        self._pre_fight.setdefault(fight_url, {})[loser_id] = rb

        ea = self.expected(ra, rb)
        mov = _MoV.get(method, 1.0)
        delta = self.k * mov * (1 - ea)

        self._ratings[winner_id] = round(ra + delta, 1)
        self._ratings[loser_id]  = round(rb - delta, 1)

    def replay_history(self, fighters: list[dict]) -> None:
        """
        Process all historical fights in strict chronological order.

        Arguments:
            fighters: List of preprocessed fighter dicts (fighters_raw.json).
                      Each must have 'fighter_id' and 'fight_history'.
        """
        # Build the global fight list (deduplicated by fight_url)
        seen: set[str] = set()
        all_fights: list[FightResult] = []

        for fighter in fighters:
            fid = fighter.get("fighter_id", "")
            history = fighter.get("fight_history", [])
            for fight in history:
                url = fight.get("fight_url") or fight.get("fight_id", "")
                if not url or url in seen:
                    continue
                result = fight.get("result", "")
                method = fight.get("method", "Decision")
                event_date = parse_date(fight.get("event_date"))
                if event_date is None or result not in ("W", "L"):
                    continue
                opp_url = fight.get("opponent_url", "")
                if result == "W":
                    all_fights.append(FightResult(url, event_date, fid, opp_url, method))
                else:
                    all_fights.append(FightResult(url, event_date, opp_url, fid, method))
                seen.add(url)

        # Sort by date, then process
        all_fights.sort(key=lambda f: f.event_date)
        for fr in all_fights:
            self.update(fr.winner_id, fr.loser_id, fr.method, fr.fight_url)

        print(f"[Elo] Replayed {len(all_fights)} fights across {len(fighters)} fighters.")

    def ratings_snapshot(self) -> dict[str, float]:
        """Return current ratings for all fighters."""
        return dict(self._ratings)


# ── Convenience: build Elo from fighters_raw.json ────────────────────────────

def build_elo_system(fighters: list[dict]) -> EloSystem:
    sys = EloSystem()
    sys.replay_history(fighters)
    return sys


def get_pre_fight_elos(
    sys: EloSystem,
    fighter_a_id: str,
    fighter_b_id: str,
    fight_url: str,
) -> tuple[float, float]:
    """Return (elo_a, elo_b) as they were BEFORE the given fight."""
    ea = sys.pre_fight_rating(fighter_a_id, fight_url)
    eb = sys.pre_fight_rating(fighter_b_id, fight_url)
    return ea, eb
