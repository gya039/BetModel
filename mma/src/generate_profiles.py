"""
generate_profiles.py — generate a short analyst-style fighter profile for
                       every fighter on the card.

Two modes (automatic):
  1. Claude Haiku (API)  — if ANTHROPIC_API_KEY is set in env / .env
  2. Rule-based fallback — always works without any API key

Run:  python src/generate_profiles.py
Out:  data/processed/fighter_profiles.json  (fighter_id → profile text)
Profiles are cached — delete the file or individual entries to regenerate.
"""
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from utils import load_json, save_json, DATA_PROC, BASE_DIR, get_logger

log = get_logger("generate_profiles")

# ── load .env ─────────────────────────────────────────────────────────────────
_env_path = BASE_DIR.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


# ── rule-based profiler ───────────────────────────────────────────────────────

def _rule_based(f: dict) -> str:
    name        = f.get("name", "This fighter")
    slpm        = f.get("slpm") or 0
    sapm        = f.get("sapm") or 0
    str_acc     = (f.get("str_acc") or 0)
    str_def     = (f.get("str_def") or 0)
    td_avg      = f.get("td_avg") or 0
    td_def      = (f.get("td_def") or 0)
    sub_avg     = f.get("sub_avg") or 0
    finish_rate = f.get("finish_rate") or 0
    ko_pct      = f.get("wins_by_ko_tko_pct") or 0
    sub_pct     = f.get("wins_by_sub_pct") or 0
    dec_pct     = f.get("wins_by_dec_pct") or 0
    wins        = f.get("wins", 0)
    losses      = f.get("losses", 0)
    streak      = f.get("current_streak", 0)
    stance      = f.get("stance", "")
    w_total     = wins + losses

    sentences = []

    # ── Fighting style ────────────────────────────────────────────────────────
    is_wrestler = td_avg >= 2.5
    is_sub_threat = sub_avg >= 1.0 or sub_pct >= 30
    is_ko_striker = ko_pct >= 40 or (slpm >= 4.5 and ko_pct >= 20)
    is_grinder = dec_pct >= 60 and finish_rate < 40
    is_high_volume = slpm >= 5.5
    is_accurate = str_acc >= 0.50
    is_defensive = str_def >= 0.58 and sapm < 3.5
    is_pressure = slpm >= 4.5 and str_def < 0.55

    style_parts = []

    if is_wrestler and is_sub_threat:
        style_parts.append("a dominant wrestler with elite submission skills")
    elif is_wrestler:
        style_parts.append("a grappling-heavy fighter who dictates where the fight goes")
    elif is_ko_striker and is_high_volume:
        style_parts.append("a high-volume power striker")
    elif is_ko_striker:
        style_parts.append("a knockout threat with heavy hands")
    elif is_sub_threat and not is_wrestler:
        style_parts.append("a submission specialist on the feet and ground")
    elif is_grinder:
        style_parts.append("a grinding technical fighter who wears opponents down")
    elif is_high_volume and is_accurate:
        style_parts.append("an accurate, high-output striker")
    elif is_high_volume:
        style_parts.append("a pressure fighter who throws in volume")
    elif is_accurate and is_defensive:
        style_parts.append("a technical counter-striker with clean defence")
    else:
        style_parts.append("a well-rounded fighter")

    if stance and stance.lower() == "southpaw":
        style_parts.append("fighting from a southpaw stance")
    elif stance and stance.lower() == "switch":
        style_parts.append("capable of switching stances")

    sentences.append(f"{name} is {' '.join(style_parts)}.")

    # ── How they win ──────────────────────────────────────────────────────────
    win_parts = []
    if ko_pct >= 35 and sub_pct >= 20:
        win_parts.append(f"finishing {finish_rate:.0f}% of wins by either KO/TKO ({ko_pct:.0f}%) or submission ({sub_pct:.0f}%)")
    elif ko_pct >= 35:
        win_parts.append(f"stopping {ko_pct:.0f}% of opponents by KO/TKO")
    elif sub_pct >= 35:
        win_parts.append(f"submitting {sub_pct:.0f}% of opponents")
    elif dec_pct >= 60:
        win_parts.append(f"winning {dec_pct:.0f}% of bouts by decision through consistent output and control")
    elif finish_rate >= 60:
        win_parts.append(f"finishing fights at a {finish_rate:.0f}% rate")

    if win_parts:
        sentences.append(f"They win by {win_parts[0]}.")

    # ── Strengths / notes ─────────────────────────────────────────────────────
    notes = []
    if str_acc >= 0.52:
        notes.append(f"{str_acc*100:.0f}% striking accuracy")
    if td_def >= 0.75 and td_avg < 1.5:
        notes.append("elite takedown defence")
    if sapm >= 5.0:
        notes.append("a tendency to absorb significant punishment")
    if td_avg >= 3.0:
        notes.append(f"averaging {td_avg:.1f} takedowns per 15 minutes")
    if is_defensive:
        notes.append(f"strong striking defence at {str_def*100:.0f}%")

    if streak >= 3:
        notes.append(f"currently riding a {streak}-fight win streak")
    elif streak <= -2:
        notes.append(f"looking to rebound from a {abs(streak)}-fight skid")

    if notes:
        sentences.append("Notable: " + ", ".join(notes[:3]) + ".")

    # Clamp to 3 sentences
    return " ".join(sentences[:3])


# ── Claude API profiler ───────────────────────────────────────────────────────

def _claude_based(f: dict) -> str:
    import anthropic

    def pct(v):
        return f"{round(v * 100)}%" if v is not None else "N/A"

    lines = [
        f"Fighter: {f.get('name')} | Record: {f.get('wins')}-{f.get('losses')}-{f.get('draws',0)}",
        f"Stance: {f.get('stance','N/A')} | Age: ~{int(f['age'])} " if f.get("age") else "",
        f"SLpM: {f.get('slpm','N/A')} | Str Acc: {pct(f.get('str_acc'))} | SApM: {f.get('sapm','N/A')} | Str Def: {pct(f.get('str_def'))}",
        f"TD Avg: {f.get('td_avg','N/A')} | TD Acc: {pct(f.get('td_acc'))} | TD Def: {pct(f.get('td_def'))} | Sub Avg: {f.get('sub_avg','N/A')}",
        f"Wins — KO/TKO: {f.get('wins_by_ko_tko_pct',0):.0f}%  Sub: {f.get('wins_by_sub_pct',0):.0f}%  Dec: {f.get('wins_by_dec_pct',0):.0f}%  Finish rate: {f.get('finish_rate',0):.0f}%",
    ]
    prompt = (
        "You are an MMA scout. Write a 2–3 sentence pre-fight profile for this fighter: "
        "describe their style, what makes them dangerous, and how they typically win. "
        "Be specific. No headers, no bullet points — plain prose only.\n\n"
        + "\n".join(l for l in lines if l)
    )
    client = anthropic.Anthropic(api_key=_API_KEY)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=160,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ── main ──────────────────────────────────────────────────────────────────────

def generate_profile(f: dict) -> str:
    if _API_KEY:
        try:
            return _claude_based(f)
        except Exception as exc:
            log.warning("Claude API failed for %s (%s) — using rule-based", f.get("name"), exc)
    return _rule_based(f)


def main() -> dict:
    summary_path = DATA_PROC / "fighter_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError("fighter_summary.json not found — run aggregate_stats.py first.")

    out_path = DATA_PROC / "fighter_profiles.json"
    existing: dict = load_json(out_path) if out_path.exists() else {}
    fighters = load_json(summary_path)
    profiles = dict(existing)

    mode = "Claude Haiku API" if _API_KEY else "rule-based (no ANTHROPIC_API_KEY set)"
    log.info("Profile mode: %s", mode)

    for f in fighters:
        fid = f.get("fighter_id", "")
        if fid in profiles and profiles[fid]:
            log.info("Skip (cached): %s", f.get("name"))
            continue
        log.info("Profiling: %s", f.get("name"))
        profiles[fid] = generate_profile(f)
        log.info("  → %s", profiles[fid][:90] + "…")

    save_json(profiles, out_path)
    log.info("Saved %d profiles → %s", len(profiles), out_path)
    return profiles


if __name__ == "__main__":
    main()
