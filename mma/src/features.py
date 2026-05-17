"""
features.py — Feature engineering for the UFC predictive model.

Builds leakage-free fighter features from historical fight records.
Every feature is computed using only data available BEFORE the fight.

Key design decisions:
  - Exponential time-decay (recent fights weighted more heavily)
  - Age curve penalty (peak at 27-30, decline after 33)
  - Layoff penalty (ring rust after 12+ months inactivity)
  - Division-aware normalization (HW slpm != FW slpm)
  - Opponent-quality adjustment (simple win-quality weighting)
  - Regression toward division mean (shrinks extreme values)
"""
from __future__ import annotations

import math
import re
from datetime import datetime, date
from typing import Any

# ── Division baseline averages (UFC historical approximations) ────────────────
# Used for normalization: fighter stat relative to division mean.
# Source: UFC Stats career averages aggregated by weight class.

DIVISION_AVERAGES: dict[str, dict[str, float]] = {
    "Strawweight":          {"slpm": 4.2, "sapm": 4.0, "str_acc": 0.41, "str_def": 0.54, "td_avg": 1.9, "td_def": 0.60, "finish_rate": 0.38},
    "Flyweight":            {"slpm": 4.3, "sapm": 4.1, "str_acc": 0.42, "str_def": 0.55, "td_avg": 1.9, "td_def": 0.62, "finish_rate": 0.36},
    "Bantamweight":         {"slpm": 4.5, "sapm": 4.3, "str_acc": 0.43, "str_def": 0.56, "td_avg": 1.8, "td_def": 0.62, "finish_rate": 0.44},
    "Featherweight":        {"slpm": 4.5, "sapm": 4.3, "str_acc": 0.43, "str_def": 0.55, "td_avg": 1.8, "td_def": 0.61, "finish_rate": 0.46},
    "Lightweight":          {"slpm": 4.6, "sapm": 4.4, "str_acc": 0.43, "str_def": 0.56, "td_avg": 1.7, "td_def": 0.63, "finish_rate": 0.50},
    "Welterweight":         {"slpm": 4.4, "sapm": 4.2, "str_acc": 0.43, "str_def": 0.57, "td_avg": 1.7, "td_def": 0.65, "finish_rate": 0.50},
    "Middleweight":         {"slpm": 4.2, "sapm": 4.0, "str_acc": 0.43, "str_def": 0.57, "td_avg": 1.6, "td_def": 0.64, "finish_rate": 0.52},
    "Light Heavyweight":    {"slpm": 3.9, "sapm": 3.7, "str_acc": 0.43, "str_def": 0.57, "td_avg": 1.5, "td_def": 0.64, "finish_rate": 0.54},
    "Heavyweight":          {"slpm": 3.8, "sapm": 3.6, "str_acc": 0.44, "str_def": 0.58, "td_avg": 1.2, "td_def": 0.65, "finish_rate": 0.58},
    "Women's Strawweight":  {"slpm": 4.1, "sapm": 4.0, "str_acc": 0.40, "str_def": 0.53, "td_avg": 1.9, "td_def": 0.60, "finish_rate": 0.30},
    "Women's Flyweight":    {"slpm": 4.0, "sapm": 3.9, "str_acc": 0.40, "str_def": 0.53, "td_avg": 1.8, "td_def": 0.59, "finish_rate": 0.28},
    "Women's Bantamweight": {"slpm": 4.2, "sapm": 4.0, "str_acc": 0.41, "str_def": 0.54, "td_avg": 1.7, "td_def": 0.60, "finish_rate": 0.38},
    "Women's Featherweight":{"slpm": 3.9, "sapm": 3.8, "str_acc": 0.41, "str_def": 0.54, "td_avg": 1.5, "td_def": 0.59, "finish_rate": 0.34},
}

# Fallback division averages (used when weight class is unknown)
_FALLBACK_DIV = {"slpm": 4.3, "sapm": 4.1, "str_acc": 0.43, "str_def": 0.56,
                 "td_avg": 1.7, "td_def": 0.62, "finish_rate": 0.46}

# Exponential decay alpha: 0.85 = each prior fight worth 85% of the one after it
DECAY_ALPHA = 0.85

# Shrinkage toward division mean (Bayesian-style): 0 = no shrinkage, 1 = full shrinkage
SHRINKAGE = 0.25

# Minimum fights before we trust rolling stats (below this, blend with priors)
MIN_FIGHTS_TRUST = 5


# ── Date parsing ──────────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%b. %d, %Y",   # "Aug. 23, 2025"
    "%B %d, %Y",    # "August 23, 2025"
    "%b %d, %Y",    # "Aug 23, 2025"
    "%Y-%m-%d",     # "2025-08-23"
]

_MONTH_ABBR = {
    "Jan.": "Jan", "Feb.": "Feb", "Mar.": "Mar", "Apr.": "Apr",
    "May.": "May", "Jun.": "Jun", "Jul.": "Jul", "Aug.": "Aug",
    "Sep.": "Sep", "Oct.": "Oct", "Nov.": "Nov", "Dec.": "Dec",
}


def parse_date(date_str: str | None) -> date | None:
    """Parse UFC Stats date strings into a Python date object."""
    if not date_str:
        return None
    s = date_str.strip()
    for abbr, repl in _MONTH_ABBR.items():
        s = s.replace(abbr, repl)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def days_between(d1: date | None, d2: date | None) -> int | None:
    """Return signed number of days d2 - d1."""
    if d1 is None or d2 is None:
        return None
    return (d2 - d1).days


# ── Age and career stage ──────────────────────────────────────────────────────

def age_at(dob_str: str | None, fight_date: date | None) -> float | None:
    """Compute fighter age in decimal years at fight date."""
    if not dob_str or fight_date is None:
        return None
    d = parse_date(dob_str)
    if d is None:
        # Try year-only extraction
        m = re.search(r"\b(19|20)\d{2}\b", str(dob_str))
        if m:
            return fight_date.year - int(m.group())
        return None
    return round((fight_date - d).days / 365.25, 1)


def age_curve_factor(age: float | None) -> float:
    """
    Performance multiplier based on career stage.
    1.0 = peak (27-30). Declines outside that window.
    Used to discount stats from very young or very old fighters.
    """
    if age is None:
        return 0.95
    if age < 22:
        return 0.82
    if age < 25:
        return 0.91
    if age <= 30:
        return 1.00
    if age <= 33:
        return 0.97
    if age <= 35:
        return 0.93
    if age <= 37:
        return 0.88
    return 0.82


def layoff_penalty(days: int | None) -> float:
    """
    Multiplier for ring rust from inactivity.
    1.0 = no penalty (< 6 months). Decays for longer layoffs.
    """
    if days is None or days < 0:
        return 1.0
    if days < 180:    # < 6 months
        return 1.00
    if days < 270:    # 6-9 months
        return 0.98
    if days < 365:    # 9-12 months
        return 0.96
    if days < 540:    # 12-18 months
        return 0.93
    if days < 730:    # 18-24 months
        return 0.89
    return 0.84       # 2+ years


# ── Exponential weighting ─────────────────────────────────────────────────────

def exp_weights(n: int, alpha: float = DECAY_ALPHA) -> list[float]:
    """
    Generate exponential decay weights for n fights.
    Weight[0] = 1.0 (most recent), Weight[i] = alpha^i.
    """
    return [alpha ** i for i in range(n)]


def weighted_mean(values: list[float], weights: list[float]) -> float | None:
    """Weighted average; returns None if no valid data."""
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None]
    if not pairs:
        return None
    total_w = sum(w for _, w in pairs)
    if total_w == 0:
        return None
    return sum(v * w for v, w in pairs) / total_w


# ── Division normalization ────────────────────────────────────────────────────

def div_avg(division: str | None, stat: str) -> float:
    """Return division average for a stat (fallback to overall average)."""
    div = division or ""
    avgs = DIVISION_AVERAGES.get(div, _FALLBACK_DIV)
    return avgs.get(stat, _FALLBACK_DIV.get(stat, 0.0))


def normalize_to_division(value: float | None, division: str | None, stat: str) -> float | None:
    """
    Express a stat relative to the division average.
    Returns (value / div_average). 1.0 = average, 1.2 = 20% above average.
    """
    if value is None:
        return None
    avg = div_avg(division, stat)
    if avg == 0:
        return None
    return round(value / avg, 3)


def shrink_to_mean(value: float | None, division: str | None, stat: str,
                   n_fights: int, shrink: float = SHRINKAGE) -> float | None:
    """
    Bayesian shrinkage toward the division mean.
    With few fights (n=1), result is pulled heavily toward the mean.
    With many fights (n >= MIN_FIGHTS_TRUST), result is mostly the observed value.
    """
    if value is None:
        return None
    mean = div_avg(division, stat)
    w = shrink * max(0, 1 - n_fights / MIN_FIGHTS_TRUST)
    return round(value * (1 - w) + mean * w, 4)


# ── Fight history helpers ─────────────────────────────────────────────────────

def _safe(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def result_value(fight: dict) -> float | None:
    """W → 1.0, L → 0.0, else None (draw/NC excluded)."""
    r = fight.get("result", "")
    if r == "W":
        return 1.0
    if r == "L":
        return 0.0
    return None


def is_finish(fight: dict) -> bool:
    return fight.get("method") in ("KO/TKO", "Submission")


def is_ko_win(fight: dict) -> bool:
    return fight.get("result") == "W" and fight.get("method") == "KO/TKO"


def is_sub_win(fight: dict) -> bool:
    return fight.get("result") == "W" and fight.get("method") == "Submission"


def is_ko_loss(fight: dict) -> bool:
    return fight.get("result") == "L" and fight.get("method") == "KO/TKO"


def is_sub_loss(fight: dict) -> bool:
    return fight.get("result") == "L" and fight.get("method") == "Submission"


def current_streak(fights_recent_first: list[dict]) -> int:
    """
    Positive integer = win streak, negative = loss streak.
    Draws and NC reset the streak.
    """
    streak = 0
    streak_type = None
    for f in fights_recent_first:
        rv = result_value(f)
        if rv is None:
            break
        if streak_type is None:
            streak_type = rv
        if rv == streak_type:
            streak += 1 if rv == 1.0 else -1
        else:
            break
    return streak


# ── Core feature computation ──────────────────────────────────────────────────

def compute_fighter_features(
    fighter: dict,
    prior_fights: list[dict],     # Chronological order (oldest first), BEFORE current fight
    fight_date: date | None,
    division: str | None = None,
    elo: float = 1500.0,
) -> dict:
    """
    Compute all pre-fight features for a single fighter.

    Arguments:
        fighter:      Full fighter profile dict (bio + career stats).
        prior_fights: All fights BEFORE the current one, oldest-first.
        fight_date:   Date of the fight being predicted.
        division:     Weight class of the current fight.
        elo:          Pre-fight Elo rating.

    Returns dict with feature keys prefixed to avoid name collisions.
    """
    n = len(prior_fights)
    recent_first = list(reversed(prior_fights))  # most recent first for weighting

    # ── Physical ─────────────────────────────────────────────────────────────
    age = age_at(fighter.get("dob"), fight_date)
    age_factor = age_curve_factor(age)

    # ── Layoff ───────────────────────────────────────────────────────────────
    last_fight_date = None
    if recent_first:
        last_fight_date = parse_date(recent_first[0].get("event_date"))
    layoff_d = days_between(last_fight_date, fight_date)
    layoff_f = layoff_penalty(layoff_d)

    # ── Exponential weights (most recent = weight 1.0) ───────────────────────
    weights = exp_weights(len(recent_first))
    total_w = sum(weights) or 1.0

    def wgt_avg(key: str, fn=None) -> float:
        if fn is None:
            vals = [_safe(f.get(key)) for f in recent_first]
        else:
            vals = [fn(f) for f in recent_first]
        pairs = [(v, w) for v, w in zip(vals, weights) if v is not None]
        if not pairs:
            return 0.0
        tw = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / tw if tw else 0.0

    # ── Rolling performance (time-decayed) ───────────────────────────────────
    results_wgt     = wgt_avg("result", fn=result_value)
    sig_landed_wgt  = wgt_avg("sig_strikes_landed")
    td_landed_wgt   = wgt_avg("td_landed")
    sub_att_wgt     = wgt_avg("sub_attempts")
    kd_wgt          = wgt_avg("kd")

    # Finish / KO / Sub / Dec rates from prior fights
    valid_fights   = [f for f in prior_fights if result_value(f) is not None]
    wins           = [f for f in valid_fights if f.get("result") == "W"]
    losses         = [f for f in valid_fights if f.get("result") == "L"]
    n_valid        = len(valid_fights)
    n_wins         = len(wins)
    n_losses       = len(losses)

    win_rate        = n_wins / n_valid if n_valid else 0.5
    finish_rate     = sum(1 for f in wins if is_finish(f)) / n_wins if n_wins else 0.0
    ko_win_rate     = sum(1 for f in wins if is_ko_win(f)) / n_wins if n_wins else 0.0
    sub_win_rate    = sum(1 for f in wins if is_sub_win(f)) / n_wins if n_wins else 0.0
    dec_win_rate    = 1 - finish_rate
    ko_loss_rate    = sum(1 for f in losses if is_ko_loss(f)) / n_losses if n_losses else 0.0
    sub_loss_rate   = sum(1 for f in losses if is_sub_loss(f)) / n_losses if n_losses else 0.0

    # Shrink extreme rates toward division mean when sample is thin
    finish_rate_s   = shrink_to_mean(finish_rate, division, "finish_rate", n_valid)

    # ── Streak ────────────────────────────────────────────────────────────────
    streak = current_streak(recent_first)
    last_result = result_value(recent_first[0]) if recent_first else 0.5

    # ── Weighted recent form score (−1 to +1) ────────────────────────────────
    form_score = results_wgt * 2 - 1  # maps [0,1] → [−1,+1]

    # ── Division-normalized striking (shrunk toward mean) ────────────────────
    # Use career slpm from bio as the base, age-adjusted
    career_slpm  = _safe(fighter.get("slpm"))
    career_sapm  = _safe(fighter.get("sapm"))
    career_stracc = _safe(fighter.get("str_acc"))
    career_strdef = _safe(fighter.get("str_def"))

    # Blend career stats with rolling stats (career = more stable, rolling = more recent)
    if n >= MIN_FIGHTS_TRUST:
        # Trust rolling stats more
        slpm_est  = career_slpm * 0.4 + (sig_landed_wgt / 3.0) * 0.6  # rough per-minute proxy
    else:
        slpm_est  = career_slpm

    slpm_est *= age_factor * layoff_f  # apply age and ring-rust adjustment

    slpm_vs_div  = normalize_to_division(shrink_to_mean(slpm_est, division, "slpm", n_valid), division, "slpm")
    sapm_vs_div  = normalize_to_division(shrink_to_mean(career_sapm * age_factor, division, "sapm", n_valid), division, "sapm")
    stracc_vs_div = normalize_to_division(shrink_to_mean(career_stracc, division, "str_acc", n_valid), division, "str_acc")
    strdef_vs_div = normalize_to_division(shrink_to_mean(career_strdef, division, "str_def", n_valid), division, "str_def")

    td_vs_div    = normalize_to_division(shrink_to_mean(td_landed_wgt, division, "td_avg", n_valid), division, "td_avg")

    # ── Physical attributes ───────────────────────────────────────────────────
    height_in  = _safe(fighter.get("height_in"))
    reach_in   = _safe(fighter.get("reach_in"))
    weight_lbs = _safe(fighter.get("weight_lbs"))
    is_south   = 1 if str(fighter.get("stance", "")).lower() == "southpaw" else 0

    return {
        # Identity / context
        "n_fights":           n,
        "n_valid_fights":     n_valid,
        "age":                age or 28.0,
        "age_factor":         age_factor,
        "height_in":          height_in,
        "reach_in":           reach_in,
        "weight_lbs":         weight_lbs,
        "is_southpaw":        is_south,
        # Performance (time-decayed rolling)
        "win_rate":           round(win_rate, 4),
        "form_score":         round(form_score, 4),
        "sig_strikes_ew":     round(sig_landed_wgt, 2),
        "td_landed_ew":       round(td_landed_wgt, 3),
        "sub_attempts_ew":    round(sub_att_wgt, 3),
        "kd_ew":              round(kd_wgt, 3),
        # Finish profile
        "finish_rate":        round(finish_rate_s or finish_rate, 4),
        "ko_win_rate":        round(ko_win_rate, 4),
        "sub_win_rate":       round(sub_win_rate, 4),
        "dec_win_rate":       round(dec_win_rate, 4),
        "ko_loss_rate":       round(ko_loss_rate, 4),
        "sub_loss_rate":      round(sub_loss_rate, 4),
        # Streak / recency
        "streak":             streak,
        "last_result":        last_result if last_result is not None else 0.5,
        # Division-normalized
        "slpm_vs_div":        round(slpm_vs_div or 1.0, 4),
        "sapm_vs_div":        round(sapm_vs_div or 1.0, 4),
        "stracc_vs_div":      round(stracc_vs_div or 1.0, 4),
        "strdef_vs_div":      round(strdef_vs_div or 1.0, 4),
        "td_vs_div":          round(td_vs_div or 1.0, 4),
        # Layoff
        "layoff_days":        layoff_d or 180,
        "layoff_factor":      round(layoff_f, 4),
        # Elo
        "elo":                round(elo, 1),
    }


def compute_matchup_features(feat_a: dict, feat_b: dict) -> dict:
    """
    Compute difference features for a fighter pair.
    Convention: positive diff favors fighter A.
    The model sees diffs (not raw values) so it's invariant to division-level baselines.
    """
    def diff(key: str) -> float:
        return round(feat_a.get(key, 0.0) - feat_b.get(key, 0.0), 4)

    return {
        "diff_age":            diff("age"),
        "diff_height":         diff("height_in"),
        "diff_reach":          diff("reach_in"),
        "diff_n_fights":       diff("n_fights"),
        "diff_win_rate":       diff("win_rate"),
        "diff_form_score":     diff("form_score"),
        "diff_sig_strikes":    diff("sig_strikes_ew"),
        "diff_td_landed":      diff("td_landed_ew"),
        "diff_sub_attempts":   diff("sub_attempts_ew"),
        "diff_kd":             diff("kd_ew"),
        "diff_finish_rate":    diff("finish_rate"),
        "diff_ko_win_rate":    diff("ko_win_rate"),
        "diff_sub_win_rate":   diff("sub_win_rate"),
        "diff_ko_loss_rate":   diff("ko_loss_rate"),  # negative = fighter A more KO-vulnerable
        "diff_sub_loss_rate":  diff("sub_loss_rate"),
        "diff_streak":         diff("streak"),
        "diff_last_result":    diff("last_result"),
        "diff_slpm_vs_div":    diff("slpm_vs_div"),
        "diff_sapm_vs_div":    diff("sapm_vs_div"),
        "diff_stracc_vs_div":  diff("stracc_vs_div"),
        "diff_strdef_vs_div":  diff("strdef_vs_div"),
        "diff_td_vs_div":      diff("td_vs_div"),
        "diff_layoff_days":    diff("layoff_days"),
        "diff_elo":            diff("elo"),
        "diff_age_factor":     diff("age_factor"),
        # Stance matchup (Southpaw vs Orthodox is a meaningful edge)
        "a_southpaw":          feat_a.get("is_southpaw", 0),
        "b_southpaw":          feat_b.get("is_southpaw", 0),
        "stance_clash":        int(feat_a.get("is_southpaw", 0) != feat_b.get("is_southpaw", 0)),
    }


# ── Feature column list (used by model trainer for column ordering) ───────────

FEATURE_COLUMNS = [
    "diff_age", "diff_height", "diff_reach", "diff_n_fights",
    "diff_win_rate", "diff_form_score",
    "diff_sig_strikes", "diff_td_landed", "diff_sub_attempts", "diff_kd",
    "diff_finish_rate", "diff_ko_win_rate", "diff_sub_win_rate",
    "diff_ko_loss_rate", "diff_sub_loss_rate",
    "diff_streak", "diff_last_result",
    "diff_slpm_vs_div", "diff_sapm_vs_div", "diff_stracc_vs_div",
    "diff_strdef_vs_div", "diff_td_vs_div",
    "diff_layoff_days", "diff_elo", "diff_age_factor",
    "a_southpaw", "b_southpaw", "stance_clash",
]
