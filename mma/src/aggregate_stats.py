"""
aggregate_stats.py — compute all required fighter metrics and produce the
                     final output files used by the dashboard.

Run:  python src/aggregate_stats.py
Out:  data/processed/fighter_summary.{json,csv}
      data/processed/matchup_summary.{json,csv}
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from utils import load_json, save_json, DATA_RAW, DATA_PROC, get_logger

log = get_logger("aggregate_stats")


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_avg(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _count(history: list[dict], result: str, method: str | None = None) -> int:
    return sum(
        1 for f in history
        if f.get("result") == result
        and (method is None or f.get("method") == method)
    )


def _pct(n: int, d: int) -> float:
    return round(n / d * 100, 1) if d else 0.0


def _current_streak(history: list[dict]) -> int:
    """
    Positive = win streak length, negative = loss streak length.
    Draws and NCs break a streak.
    Index 0 = most recent fight.
    """
    if not history:
        return 0
    first = history[0].get("result")
    if first not in ("W", "L"):
        return 0
    count = 0
    for f in history:
        if f.get("result") == first:
            count += 1
        else:
            break
    return count if first == "W" else -count


# ── per-fighter stat computation ──────────────────────────────────────────────

def compute_fighter_stats(raw: dict) -> dict:
    history = raw.get("fight_history", [])

    # Only W/L/D count for method breakdowns (exclude NC)
    valid   = [f for f in history if f.get("result") in ("W", "L", "D")]
    wins    = [f for f in valid   if f.get("result") == "W"]
    losses  = [f for f in valid   if f.get("result") == "L"]
    total   = len(valid)
    w_total = len(wins)
    l_total = len(losses)

    # ── wins by method ────────────────────────────────────────────────────────
    wko  = _count(wins, "W", "KO/TKO")
    wsub = _count(wins, "W", "Submission")
    wdec = _count(wins, "W", "Decision")
    wdq  = _count(wins, "W", "DQ")
    woth = _count(wins, "W", "Other")

    # ── losses by method ──────────────────────────────────────────────────────
    lko  = _count(losses, "L", "KO/TKO")
    lsub = _count(losses, "L", "Submission")
    ldec = _count(losses, "L", "Decision")
    ldq  = _count(losses, "L", "DQ")
    loth = _count(losses, "L", "Other")

    # ── per-fight averages ────────────────────────────────────────────────────
    def _avg_stat(key: str, fights: list[dict]) -> float | None:
        return _safe_avg([f.get(key) for f in fights if f.get(key) is not None])

    avg_sig  = _avg_stat("sig_strikes_landed",   history)
    avg_tot  = _avg_stat("total_strikes_landed",  history)
    avg_td   = _avg_stat("td_landed",             history)
    avg_kd   = _avg_stat("kd",                    history)
    avg_sub  = _avg_stat("sub_attempts",          history)

    # ── last-3 / last-5 averages ──────────────────────────────────────────────
    recent3 = history[:3]
    recent5 = history[:5]

    last3_sig = _avg_stat("sig_strikes_landed",  recent3)
    last5_sig = _avg_stat("sig_strikes_landed",  recent5)
    last3_td  = _avg_stat("td_landed",           recent3)
    last5_td  = _avg_stat("td_landed",           recent5)
    last3_tot = _avg_stat("total_strikes_landed", recent3)
    last5_tot = _avg_stat("total_strikes_landed", recent5)

    # ── build output ──────────────────────────────────────────────────────────
    return {
        # Identity
        "fighter_id":   raw.get("fighter_id", ""),
        "name":         raw.get("name", ""),
        "nickname":     raw.get("nickname", ""),
        "url":          raw.get("url", ""),
        # Record
        "wins":         raw.get("wins", 0),
        "losses":       raw.get("losses", 0),
        "draws":        raw.get("draws", 0),
        "nc":           raw.get("nc", 0),
        "total_fights": total,
        "win_rate":     _pct(w_total, total),
        "loss_rate":    _pct(l_total, total),
        # Physical
        "height_raw":   raw.get("height_raw", ""),
        "height_in":    raw.get("height_in"),
        "weight_raw":   raw.get("weight_raw", ""),
        "weight_lbs":   raw.get("weight_lbs"),
        "reach_raw":    raw.get("reach_raw", ""),
        "reach_in":     raw.get("reach_in"),
        "stance":       raw.get("stance", ""),
        "age":          raw.get("age"),
        "dob":          raw.get("dob", ""),
        # Career stats (from ufcstats profile)
        "slpm":         raw.get("slpm"),          # sig strikes landed per min
        "str_acc":      raw.get("str_acc"),        # striking accuracy (0-1)
        "sapm":         raw.get("sapm"),           # sig strikes absorbed per min
        "str_def":      raw.get("str_def"),        # striking defence (0-1)
        "td_avg":       raw.get("td_avg"),         # TDs per 15 min
        "td_acc":       raw.get("td_acc"),         # TD accuracy (0-1)
        "td_def":       raw.get("td_def"),         # TD defence (0-1)
        "sub_avg":      raw.get("sub_avg"),        # sub attempts per 15 min
        # Per-fight averages (career)
        "avg_sig_strikes_landed":    avg_sig,
        "avg_total_strikes_landed":  avg_tot,
        "avg_takedowns_landed":      avg_td,
        "avg_kd_per_fight":          avg_kd,
        "avg_sub_attempts":          avg_sub,
        # Per-fight averages (recent)
        "avg_sig_strikes_last3":     last3_sig,
        "avg_sig_strikes_last5":     last5_sig,
        "avg_td_last3":              last3_td,
        "avg_td_last5":              last5_td,
        "avg_total_strikes_last3":   last3_tot,
        "avg_total_strikes_last5":   last5_tot,
        # Wins by method — raw counts
        "wins_by_ko_tko":   wko,
        "wins_by_sub":      wsub,
        "wins_by_dec":      wdec,
        "wins_by_dq":       wdq,
        "wins_by_other":    woth,
        # Wins by method — percentages
        "wins_by_ko_tko_pct":  _pct(wko,  w_total),
        "wins_by_sub_pct":     _pct(wsub, w_total),
        "wins_by_dec_pct":     _pct(wdec, w_total),
        "wins_by_dq_pct":      _pct(wdq,  w_total),
        # Losses by method — raw counts
        "losses_by_ko_tko":   lko,
        "losses_by_sub":      lsub,
        "losses_by_dec":      ldec,
        "losses_by_dq":       ldq,
        "losses_by_other":    loth,
        # Losses by method — percentages
        "losses_by_ko_tko_pct":  _pct(lko,  l_total),
        "losses_by_sub_pct":     _pct(lsub, l_total),
        "losses_by_dec_pct":     _pct(ldec, l_total),
        # Summary metrics
        "finish_rate":    _pct(wko + wsub, w_total),
        "decision_rate":  _pct(wdec, w_total),
        "current_streak": _current_streak(history),
        # Full fight history (kept for template rendering)
        "fight_history":  history,
    }


# ── matchup summary ───────────────────────────────────────────────────────────

def _build_matchup_summary(card: dict, lookup: dict[str, dict]) -> list[dict]:
    matchups = []
    for bout in card.get("bouts", []):
        fa_url = bout["fighter_a"].get("url", "")
        fb_url = bout["fighter_b"].get("url", "")
        fa_id  = fa_url.rstrip("/").split("/")[-1] if fa_url else ""
        fb_id  = fb_url.rstrip("/").split("/")[-1] if fb_url else ""

        fa = lookup.get(fa_id, {"name": bout["fighter_a"]["name"], "fighter_id": fa_id})
        fb = lookup.get(fb_id, {"name": bout["fighter_b"]["name"], "fighter_id": fb_id})

        matchups.append({
            "bout_number":  bout["bout_number"],
            "weight_class": bout.get("weight_class", ""),
            "fight_url":    bout.get("fight_url", ""),
            "fighter_a":    {k: v for k, v in fa.items() if k != "fight_history"},
            "fighter_b":    {k: v for k, v in fb.items() if k != "fight_history"},
        })
    return matchups


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> list[dict]:
    raw_json = DATA_PROC / "fighters_raw.json"
    if not raw_json.exists():
        raise FileNotFoundError("fighters_raw.json not found — run preprocess.py first.")

    raw_fighters = load_json(raw_json)
    stats_list   = [compute_fighter_stats(f) for f in raw_fighters]
    lookup       = {s["fighter_id"]: s for s in stats_list}

    # ── Fighter summary ───────────────────────────────────────────────────────
    flat_rows = [{k: v for k, v in s.items() if k != "fight_history"}
                 for s in stats_list]
    df = pd.DataFrame(flat_rows)
    df.to_csv(DATA_PROC / "fighter_summary.csv", index=False)
    save_json(stats_list, DATA_PROC / "fighter_summary.json")
    log.info("fighter_summary: %d rows", len(stats_list))

    # ── Matchup summary ───────────────────────────────────────────────────────
    card_path = DATA_RAW / "card.json"
    if card_path.exists():
        card     = load_json(card_path)
        matchups = _build_matchup_summary(card, lookup)

        flat_match = [
            {
                "bout_number":  m["bout_number"],
                "weight_class": m["weight_class"],
                "fighter_a":    m["fighter_a"].get("name", ""),
                "record_a":     f"{m['fighter_a'].get('wins','?')}-{m['fighter_a'].get('losses','?')}",
                "fighter_b":    m["fighter_b"].get("name", ""),
                "record_b":     f"{m['fighter_b'].get('wins','?')}-{m['fighter_b'].get('losses','?')}",
            }
            for m in matchups
        ]
        pd.DataFrame(flat_match).to_csv(DATA_PROC / "matchup_summary.csv", index=False)
        save_json(matchups, DATA_PROC / "matchup_summary.json")
        log.info("matchup_summary: %d bouts", len(matchups))

    log.info("All aggregation complete.")
    return stats_list


if __name__ == "__main__":
    main()
