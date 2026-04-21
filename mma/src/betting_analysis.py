"""
betting_analysis.py - fight script and matchup reasoning helpers.
"""
from __future__ import annotations


def pct(value, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    return val if val <= 1 else val / 100


def num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def method_strength(fighter: dict, opponent: dict, method: str) -> float:
    if method == "KO/TKO":
        return (
            pct(fighter.get("wins_by_ko_tko_pct")) * 0.45
            + pct(opponent.get("losses_by_ko_tko_pct")) * 0.30
            + min(num(fighter.get("avg_kd_per_fight")) / 1.2, 1) * 0.15
            + max(num(fighter.get("slpm")) - num(opponent.get("sapm")), 0) / 8 * 0.10
        )
    if method == "Submission":
        return (
            pct(fighter.get("wins_by_sub_pct")) * 0.45
            + pct(opponent.get("losses_by_sub_pct")) * 0.30
            + min(num(fighter.get("avg_sub_attempts")) / 2.0, 1) * 0.15
            + min(num(fighter.get("avg_takedowns_landed")) / 4.0, 1) * 0.10
        )
    return (
        pct(fighter.get("wins_by_dec_pct")) * 0.50
        + pct(opponent.get("losses_by_dec_pct")) * 0.25
        + (1 - pct(fighter.get("finish_rate"))) * 0.15
        + min(num(fighter.get("avg_sig_strikes_landed")) / 90, 1) * 0.10
    )


def best_method(fighter: dict, opponent: dict) -> str:
    strengths = {
        "KO/TKO": method_strength(fighter, opponent, "KO/TKO"),
        "Submission": method_strength(fighter, opponent, "Submission"),
        "Decision": method_strength(fighter, opponent, "Decision"),
    }
    return max(strengths, key=strengths.get)


def style_tags(fighter: dict) -> list[str]:
    tags: list[str] = []
    if num(fighter.get("avg_takedowns_landed")) >= 2 or num(fighter.get("td_avg")) >= 2:
        tags.append("wrestling")
    if num(fighter.get("avg_sub_attempts")) >= 0.7 or pct(fighter.get("wins_by_sub_pct")) >= 0.25:
        tags.append("submission threat")
    if num(fighter.get("slpm")) >= 4 or num(fighter.get("avg_sig_strikes_landed")) >= 60:
        tags.append("volume striking")
    if pct(fighter.get("wins_by_ko_tko_pct")) >= 0.35 or num(fighter.get("avg_kd_per_fight")) >= 0.25:
        tags.append("power")
    if pct(fighter.get("wins_by_dec_pct")) >= 0.45:
        tags.append("decision control")
    return tags or ["balanced"]


def vulnerabilities(fighter: dict) -> list[str]:
    notes: list[str] = []
    if pct(fighter.get("losses_by_ko_tko_pct")) >= 0.35:
        notes.append("has been hurt or stopped by strikes")
    if pct(fighter.get("losses_by_sub_pct")) >= 0.30:
        notes.append("has submission-loss risk")
    if num(fighter.get("sapm")) >= 4:
        notes.append("absorbs meaningful striking volume")
    if pct(fighter.get("str_def")) and pct(fighter.get("str_def")) < 0.52:
        notes.append("defensive striking numbers are soft")
    if num(fighter.get("current_streak")) < 0:
        notes.append("enters off negative recent form")
    return notes or ["few clear historical finishing vulnerabilities"]


def explain_fight_script(winner: dict, loser: dict, method: str) -> str:
    tags = ", ".join(style_tags(winner)[:2])
    risks = "; ".join(vulnerabilities(loser)[:2])
    if method == "Submission":
        return (
            f"{winner['name']} projects best when forcing grappling exchanges, banking control time, "
            f"and turning scrambles into back-take or front-headlock chances. The key support is {tags}; "
            f"the opponent profile shows {risks}."
        )
    if method == "KO/TKO":
        return (
            f"{winner['name']} has the cleaner betting script if the fight stays at range long enough "
            f"for striking volume or power to matter. The model is leaning on {tags}, while the main "
            f"opponent concerns are that {risks}."
        )
    return (
        f"{winner['name']} grades out best in a controlled minutes-winning script: safer exchanges, "
        f"pace management, and enough repeatable offense to bank rounds. The support is {tags}; "
        f"the opposing profile shows {risks}."
    )


def underdog_path(underdog: dict, favorite: dict) -> str:
    method = best_method(underdog, favorite)
    if method == "Submission":
        return f"{underdog['name']} needs grappling variance: early clinch entries, top position, or a scramble that creates submission threat."
    if method == "KO/TKO":
        return f"{underdog['name']} needs the fight at striking range and likely needs to make damage count before the favorite settles into rhythm."
    return f"{underdog['name']} needs round-winning discipline, low-risk minutes, and enough defensive answers to keep the fight from tilting late."
