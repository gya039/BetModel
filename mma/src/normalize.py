"""
normalize.py — Canonicalization for fighter names, markets, selections,
and sportsbooks.

Prevents duplicate logical bets caused by casing/spacing inconsistencies
and aliases between data sources (e.g. "Moneyline" vs "H2H" vs "Head to Head").

All functions are pure and side-effect-free — safe to use anywhere.
"""
from __future__ import annotations

import re
import unicodedata


# ── fighter / person names ────────────────────────────────────────────────────

def norm_name(raw: str) -> str:
    """
    Canonical fighter name for deduplication and bet-ID stability.

    'Carlos Prates' / 'CARLOS PRATES' / 'carlos  prates' / 'Càrlos Prátes'
    all become → 'carlos prates'
    """
    if not raw:
        return ""
    # Strip Unicode combining marks (accents) so é→e, ñ→n, etc.
    nfkd = unicodedata.normalize("NFKD", raw)
    ascii_approx = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", ascii_approx.lower()).strip()


# ── markets ───────────────────────────────────────────────────────────────────

# Canonical display form for well-known markets
_MARKET_CANONICAL: dict[str, str] = {
    "moneyline":                "Moneyline",
    "h2h":                      "Moneyline",
    "head to head":             "Moneyline",
    "head-to-head":             "Moneyline",
    "fight goes distance":      "Fight Goes Distance",
    "fight goes the distance":  "Fight Goes Distance",
    "will the fight go the distance": "Fight Goes Distance",
    "method of victory":        "Method of Victory",
    "winning method":           "Method of Victory",
    "accumulator":              "Accumulator",
    "acca":                     "Accumulator",
    "parlay":                   "Accumulator",
}

_METHOD_CANONICAL: dict[str, str] = {
    "ko":          "KO/TKO",
    "tko":         "KO/TKO",
    "ko/tko":      "KO/TKO",
    "sub":         "Submission",
    "submission":  "Submission",
    "dec":         "Decision",
    "decision":    "Decision",
    "dq":          "DQ",
    "disqualification": "DQ",
}


def norm_market(raw: str) -> str:
    """
    Canonical market string (lowercase, for storage and indexing).
    E.g. 'Moneyline' / 'H2H' / 'Head to Head' → 'moneyline'
         'Total Rounds 2.5' → 'total rounds 2.5'
         'Jack Della Maddalena by KO/TKO' → 'jack della maddalena by ko/tko'
    """
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.lower().strip())

    # Direct canonical match
    if key in _MARKET_CANONICAL:
        return _MARKET_CANONICAL[key].lower()

    # Method markets: '<fighter> by <method>'
    if " by " in key:
        left, method_raw = key.split(" by ", 1)
        method = _METHOD_CANONICAL.get(method_raw.strip(), method_raw.strip())
        return f"{left.strip()} by {method.lower()}"

    # Total rounds: normalise spacing
    m = re.match(r"total\s+rounds?\s+(\d+(?:\.\d+)?)", key)
    if m:
        return f"total rounds {m.group(1)}"

    return key


def norm_market_display(raw: str) -> str:
    """Human-readable market label (title-cased for display)."""
    normalised = norm_market(raw)
    if normalised in _MARKET_CANONICAL.values():
        return _MARKET_CANONICAL.get(normalised, raw)
    # Title-case words that aren't abbreviations
    parts = []
    for word in normalised.split():
        if word.upper() in {"KO/TKO", "KO", "TKO", "DQ", "NC"}:
            parts.append(word.upper())
        else:
            parts.append(word.capitalize())
    return " ".join(parts)


# ── selections ────────────────────────────────────────────────────────────────

def norm_selection(raw: str) -> str:
    """Canonical selection string (trimmed, collapsed whitespace, lowercase)."""
    if not raw:
        return ""
    return re.sub(r"\s+", " ", raw.lower().strip())


# ── sportsbooks ───────────────────────────────────────────────────────────────

_SPORTSBOOK_CANONICAL: dict[str, str] = {
    "draftkings":     "DraftKings",
    "draft kings":    "DraftKings",
    "fanduel":        "FanDuel",
    "fan duel":       "FanDuel",
    "betmgm":         "BetMGM",
    "bet mgm":        "BetMGM",
    "betway":         "Betway",
    "unibet":         "Unibet",
    "bet365":         "Bet365",
    "1xbet":          "1xBet",
    "1x bet":         "1xBet",
    "pinnacle":       "Pinnacle",
    "pinnaclesports": "Pinnacle",
    "betfair":        "Betfair",
    "williamhill":    "William Hill",
    "william hill":   "William Hill",
    "bovada":         "Bovada",
    "mybookie":       "MyBookie",
    "betonline":      "BetOnline",
    "bet online":     "BetOnline",
    "pointsbet":      "PointsBet",
    "points bet":     "PointsBet",
    "caesars":        "Caesars",
    "barstool":       "Barstool",
}


def norm_sportsbook(raw: str) -> str:
    """Canonical sportsbook name for consistent grouping and analytics."""
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.lower().strip())
    return _SPORTSBOOK_CANONICAL.get(key, raw.strip())
