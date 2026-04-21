"""
app.py — Octagon IQ Flask dashboard.

Run:  python src/app.py
      → http://localhost:5002
"""
import os
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from flask import Flask, render_template, jsonify, request, send_file, abort
from utils import load_json, DATA_RAW, DATA_PROC, BASE_DIR, get_logger
from betting_model import EDGES_CSV, EDGES_JSON, generate_card_betting, save_outputs
from check_movement import MOVEMENT_CSV, MOVEMENT_JSON, run_check
from bankroll import BET_HISTORY_CSV, STAKING_CSV, STAKING_JSON, load_history

log = get_logger("app")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True


# ── data helpers ──────────────────────────────────────────────────────────────

def _load(path: Path, default):
    """Load a JSON file, returning default if not found."""
    if path.exists():
        try:
            return load_json(path)
        except Exception as exc:
            log.warning("Could not load %s: %s", path, exc)
    return default


def load_all():
    card     = _load(DATA_RAW  / "card.json",                   {})
    fighters = _load(DATA_PROC / "fighter_summary.json",         [])
    matchups = _load(DATA_PROC / "matchup_summary.json",         [])
    profiles = _load(DATA_PROC / "fighter_profiles.json",        {})
    photos   = _load(DATA_PROC / "fighter_photos.json",          {})

    for f in fighters:
        fid = f.get("fighter_id", "")
        if fid and fid in profiles:
            f["profile"] = profiles[fid]
        if fid and fid in photos:
            f["photo"] = photos[fid]

    lookup = {f["fighter_id"]: f for f in fighters if f.get("fighter_id")}
    return card, fighters, matchups, lookup


def load_betting():
    """Load generated betting analysis, creating it from processed stats if needed."""
    if EDGES_JSON.exists():
        try:
            return load_json(EDGES_JSON)
        except Exception as exc:
            log.warning("Could not load betting output: %s", exc)
    analyses = generate_card_betting()
    save_outputs(analyses)
    return analyses


def load_movement():
    if MOVEMENT_JSON.exists():
        try:
            return load_json(MOVEMENT_JSON)
        except Exception as exc:
            log.warning("Could not load movement report: %s", exc)
    return {
        "generated_at": "",
        "markets_compared": 0,
        "noteworthy_count": 0,
        "rows": [],
        "noteworthy": [],
    }


def load_staking():
    if STAKING_JSON.exists():
        try:
            plan = load_json(STAKING_JSON)
        except Exception as exc:
            log.warning("Could not load staking plan: %s", exc)
            plan = {}
    else:
        load_betting()
        plan = load_json(STAKING_JSON) if STAKING_JSON.exists() else {}
    plan.setdefault("bankroll", 500.0)
    plan.setdefault("base_bankroll", 500.0)
    plan.setdefault("singles", [])
    plan.setdefault("accumulators", [])
    plan.setdefault("staking_rules", [])
    plan["history"] = load_history()
    return plan


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    card, fighters, matchups, _ = load_all()
    plan = load_staking()
    betting = load_betting()
    return render_template("index.html",
                           card=card,
                           matchups=matchups,
                           fighters=fighters,
                           plan=plan,
                           betting=betting)


@app.route("/fighters")
def fighter_directory():
    _, fighters, _, _ = load_all()

    q      = request.args.get("q", "").strip().lower()
    stance = request.args.get("stance", "").strip().lower()

    filtered = fighters
    if q:
        filtered = [f for f in filtered if q in f.get("name", "").lower()]
    if stance:
        filtered = [f for f in filtered
                    if f.get("stance", "").lower() == stance]

    all_stances = sorted({f.get("stance", "") for f in fighters
                          if f.get("stance", "").strip()})
    return render_template("fighters.html",
                           fighters=filtered,
                           query=q,
                           stances=all_stances,
                           selected_stance=stance)


@app.route("/fighter/<fighter_id>")
def fighter_detail(fighter_id: str):
    _, _, _, lookup = load_all()
    fighter = lookup.get(fighter_id)
    if not fighter:
        abort(404)
    return render_template("fighter.html", fighter=fighter)


@app.route("/matchup/<fa_id>/<fb_id>")
def matchup(fa_id: str, fb_id: str):
    _, _, _, lookup = load_all()
    fa = lookup.get(fa_id)
    fb = lookup.get(fb_id)
    if not fa or not fb:
        abort(404)
    return render_template("matchup.html", fighter_a=fa, fighter_b=fb)


@app.route("/betting")
def betting_overview():
    card, _, _, _ = load_all()
    fights = load_betting()
    return render_template("betting.html", card=card, fights=fights)


@app.route("/betting/<fight_id>")
def betting_fight(fight_id: str):
    card, _, _, _ = load_all()
    fights = load_betting()
    fight = next((item for item in fights if item.get("fight_id") == fight_id), None)
    if not fight:
        abort(404)
    return render_template("betting_fight.html", card=card, fight=fight)


@app.route("/check-movement")
def check_movement_page():
    card, _, _, _ = load_all()
    report = load_movement()
    return render_template("check_movement.html", card=card, report=report)


@app.route("/bankroll")
def bankroll_page():
    card, _, _, _ = load_all()
    plan = load_staking()
    return render_template("bankroll.html", card=card, plan=plan)


@app.route("/export")
def export_page():
    card, fighters, matchups, _ = load_all()
    return render_template("export.html",
                           card=card,
                           fighters=fighters,
                           matchups=matchups)


# ── JSON API (used by Chart.js) ───────────────────────────────────────────────

@app.route("/api/fighter/<fighter_id>")
def api_fighter(fighter_id: str):
    _, _, _, lookup = load_all()
    f = lookup.get(fighter_id)
    if not f:
        return jsonify({"error": "not found"}), 404
    return jsonify(f)


@app.route("/api/fighters")
def api_fighters():
    _, fighters, _, _ = load_all()
    return jsonify(fighters)


@app.route("/api/card")
def api_card():
    card, _, _, _ = load_all()
    return jsonify(card)


@app.route("/api/matchup/<fa_id>/<fb_id>")
def api_matchup(fa_id: str, fb_id: str):
    _, _, _, lookup = load_all()
    fa = lookup.get(fa_id)
    fb = lookup.get(fb_id)
    if not fa or not fb:
        return jsonify({"error": "not found"}), 404
    return jsonify({"fighter_a": fa, "fighter_b": fb})


@app.route("/api/betting")
def api_betting():
    return jsonify(load_betting())


@app.route("/api/check-movement", methods=["GET", "POST"])
def api_check_movement():
    if request.method == "POST":
        try:
            report = run_check(fetch=True)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify(report)
    return jsonify(load_movement())


@app.route("/api/bankroll")
def api_bankroll():
    return jsonify(load_staking())


# ── file downloads ────────────────────────────────────────────────────────────

@app.route("/download/fighter_summary.csv")
def dl_fighters_csv():
    p = DATA_PROC / "fighter_summary.csv"
    return send_file(str(p), as_attachment=True) if p.exists() else ("Not found", 404)


@app.route("/download/fighter_summary.json")
def dl_fighters_json():
    p = DATA_PROC / "fighter_summary.json"
    return send_file(str(p), as_attachment=True) if p.exists() else ("Not found", 404)


@app.route("/download/matchup_summary.csv")
def dl_matchups_csv():
    p = DATA_PROC / "matchup_summary.csv"
    return send_file(str(p), as_attachment=True) if p.exists() else ("Not found", 404)


@app.route("/download/matchup_summary.json")
def dl_matchups_json():
    p = DATA_PROC / "matchup_summary.json"
    return send_file(str(p), as_attachment=True) if p.exists() else ("Not found", 404)


@app.route("/download/betting_edges.csv")
def dl_betting_csv():
    return send_file(str(EDGES_CSV), as_attachment=True) if EDGES_CSV.exists() else ("Not found", 404)


@app.route("/download/betting_edges.json")
def dl_betting_json():
    return send_file(str(EDGES_JSON), as_attachment=True) if EDGES_JSON.exists() else ("Not found", 404)


@app.route("/download/movement_report.csv")
def dl_movement_csv():
    return send_file(str(MOVEMENT_CSV), as_attachment=True) if MOVEMENT_CSV.exists() else ("Not found", 404)


@app.route("/download/movement_report.json")
def dl_movement_json():
    return send_file(str(MOVEMENT_JSON), as_attachment=True) if MOVEMENT_JSON.exists() else ("Not found", 404)


@app.route("/download/staking_plan.csv")
def dl_staking_csv():
    return send_file(str(STAKING_CSV), as_attachment=True) if STAKING_CSV.exists() else ("Not found", 404)


@app.route("/download/staking_plan.json")
def dl_staking_json():
    return send_file(str(STAKING_JSON), as_attachment=True) if STAKING_JSON.exists() else ("Not found", 404)


@app.route("/download/bet_history.csv")
def dl_bet_history_csv():
    return send_file(str(BET_HISTORY_CSV), as_attachment=True) if BET_HISTORY_CSV.exists() else ("Not found", 404)


# ── error pages ───────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# ── launch ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5002"))
    log.info("Starting Octagon IQ on http://localhost:%s", port)
    app.run(debug=False, port=port, host="0.0.0.0", use_reloader=False)
