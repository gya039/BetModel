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
from bankroll import BET_HISTORY_CSV, STAKING_CSV, load_history, current_bankroll as _current_bankroll
import ledger
import integrity as _integrity
import backup as _backup
import scheduler as _scheduler
import log as _slog
from auth import require_admin_token, rate_limit, admin_action

log = get_logger("app")

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True

# ── custom JSON encoder for Decimal ──────────────────────────────────────────
from decimal import Decimal as _Decimal
from flask.json.provider import DefaultJSONProvider as _DefaultJSONProvider

class _DecimalJSONProvider(_DefaultJSONProvider):
    def default(self, obj):
        if isinstance(obj, _Decimal):
            return float(obj)
        return super().default(obj)

app.json_provider_class = _DecimalJSONProvider
app.json = _DecimalJSONProvider(app)


# ── fighter enrichment helpers ────────────────────────────────────────────────

_WC_THRESHOLDS = [
    (115, "Strawweight"), (125, "Flyweight"), (135, "Bantamweight"),
    (145, "Featherweight"), (155, "Lightweight"), (170, "Welterweight"),
    (185, "Middleweight"), (205, "Light Heavyweight"), (265, "Heavyweight"),
]
WEIGHT_CLASS_ORDER = [wc for _, wc in _WC_THRESHOLDS] + ["Unknown"]

def _get_weight_class(weight_lbs):
    try:
        w = float(weight_lbs or 0)
    except (ValueError, TypeError):
        return "Unknown"
    if w <= 0:
        return "Unknown"
    for threshold, name in _WC_THRESHOLDS:
        if w <= threshold:
            return name
    return "Heavyweight"

def _get_exp_tier(fighter):
    total = (fighter.get("wins") or 0) + (fighter.get("losses") or 0) + (fighter.get("draws") or 0)
    if total >= 20: return "elite"
    if total >= 10: return "veteran"
    if total >= 5:  return "prospect"
    return "newcomer"

def _enrich_fighters(fighters):
    for f in fighters:
        f["weight_class"] = _get_weight_class(f.get("weight_lbs"))
        f["exp_tier"] = _get_exp_tier(f)
    return fighters


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
    for matchup in matchups:
        for corner_key in ("fighter_a", "fighter_b"):
            fighter = matchup.get(corner_key) or {}
            fid = fighter.get("fighter_id", "")
            if fid and fid in profiles:
                fighter["profile"] = profiles[fid]
            if fid and fid in photos:
                fighter["photo"] = photos[fid]
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


_STAKING_RULES = [
    {"edge": "3-6%",           "stake": "3%"},
    {"edge": "6-10%",          "stake": "5%"},
    {"edge": "10-15%",         "stake": "7%"},
    {"edge": "15%+",           "stake": "10%"},
    {"edge": "Low confidence", "stake": "cap 3%"},
    {"edge": "Singles cap",    "stake": "50% max"},
]


def load_staking() -> dict:
    # RULE: reads ONLY from ledger — never from staking_plan.json or betting_edges.json.
    active = ledger.get_active_event()
    bankroll = float(_current_bankroll())
    if not active:
        return {
            "bankroll":               bankroll,
            "base_bankroll":          500.0,
            "singles":                [],
            "accumulators":           [],
            "total_single_stake":     0.0,
            "total_accumulator_stake": 0.0,
            "staking_rules":          _STAKING_RULES,
            "history":                load_history(),
        }
    event_id = active["event_id"]
    bets = ledger.get_event_bets(event_id)
    singles = [b for b in bets if b["bet_type"] == "single"]
    accas   = [b for b in bets if b["bet_type"] in ("double", "treble", "4fold")]

    # Reshape singles to match template expectations: pick.stake.eur / pick.stake.pct
    for s in singles:
        eur = s.get("stake_eur") or 0.0
        pct = (eur / bankroll * 100) if bankroll else 0.0
        ret = s.get("potential_return") or 0.0
        s["stake"] = {
            "eur":   eur,
            "pct":   f"{pct:.1f}%",
            "label": s.get("status", "pending"),
        }
        s["potential_return"] = ret
        s["profit_if_win"]    = round(ret - eur, 2)
        s["decimal_odds"]     = s.get("odds") or 0.0
        # Derive label from edge when confidence column exists
        edge = s.get("edge")
        if not s.get("label"):
            if edge is None:
                s["label"] = "Placed"
            elif edge >= 10:
                s["label"] = "Best Bet"
            elif edge >= 6:
                s["label"] = "Lean"
            elif edge >= 3:
                s["label"] = "Small Value"
            else:
                s["label"] = "Pass"
        if s.get("confidence") is None:
            s["confidence"] = ""

    # Accumulators: template uses acca.stake as a float directly
    for a in accas:
        a["stake"]         = a.get("stake_eur") or 0.0
        a["combined_edge"] = a.get("combined_edge") or 0.0

    return {
        "event_name":              active.get("event_name", ""),
        "event_id":                event_id,
        "bankroll":                bankroll,
        "base_bankroll":           500.0,
        "singles":                 singles,
        "accumulators":            accas,
        "total_single_stake":      sum(b["stake_eur"] for b in singles),
        "total_accumulator_stake": sum(b.get("stake_eur", 0) for b in bets if b["bet_type"] in ("double", "treble", "4fold")),
        "staking_rules":           _STAKING_RULES,
        "history":                 load_history(),
    }


def _market_rows(fight: dict, predicate):
    return [row for row in fight.get("markets", []) if predicate(row)]


def _top_method_row(fight: dict):
    method_rows = _market_rows(fight, lambda row: " by " in str(row.get("market", "")))
    if not method_rows:
        return None
    return max(method_rows, key=lambda row: row.get("model_probability") or 0)


def _best_total_row(fight: dict):
    totals = _market_rows(
        fight,
        lambda row: str(row.get("market", "")).startswith("Total Rounds ") and row.get("decimal_odds") not in ("", None),
    )
    if not totals:
        return None
    return max(totals, key=lambda row: row.get("edge") if row.get("edge") is not None else -999)


def _method_label(row: dict | None) -> str:
    if not row:
        return "Decision / mixed paths"
    market = str(row.get("market", ""))
    return market.split(" by ", 1)[-1] if " by " in market else market


def _timing_label(fight: dict, method_row: dict | None) -> str:
    finish = fight.get("finish_probability") or 0
    decision = fight.get("decision_probability") or 0
    method = _method_label(method_row)
    if decision >= 0.58:
        return "Most likely reaches the cards"
    if finish >= 0.7 and method == "KO/TKO":
        return "Early danger window"
    if finish >= 0.64 and method == "Submission":
        return "Mid-fight grappling finish live"
    if finish >= 0.58:
        return "Inside-the-distance lean"
    return "Swing rounds after round one"


def _verdict_summary(fight: dict) -> dict:
    method_row = _top_method_row(fight)
    total_row = _best_total_row(fight)
    breakdown = fight.get("betting_breakdown", {})
    side = fight.get("model_side", "Pass")
    side_prob = round((fight.get("model_side_probability") or 0) * 100)
    finish = round((fight.get("finish_probability") or 0) * 100)
    decision = round((fight.get("decision_probability") or 0) * 100)
    why = breakdown.get("fight_script") or breakdown.get("rationale") or "The model sees cleaner winning paths on this side."

    same_fight = []
    if total_row:
        total_label = f"{total_row.get('selection')} {total_row.get('market').replace('Total Rounds ', '')}"
        if (total_row.get("edge") or 0) >= 3:
            same_fight.append(f"{side} + {total_label}")
    if method_row and (method_row.get("model_probability") or 0) >= 0.16:
        same_fight.append(method_row.get("selection"))
    if not same_fight:
        same_fight.append(f"{side} moneyline only")

    return {
        "winner": side,
        "winner_probability_pct": side_prob,
        "method": _method_label(method_row),
        "method_probability_pct": round((method_row.get("model_probability") or 0) * 100) if method_row else None,
        "timing": _timing_label(fight, method_row),
        "finish_probability_pct": finish,
        "decision_probability_pct": decision,
        "why": why,
        "same_fight_builds": same_fight[:2],
    }


def enrich_fight(fight: dict) -> dict:
    # Strip non-playable markets before the template sees them.
    # Avoid/Pass markets and unpriced rows (no odds) must never reach the board.
    fight["markets"] = [
        m for m in fight.get("markets", [])
        if m.get("label") not in ("Pass", "Avoid")
        and m.get("decimal_odds") not in ("", None)
        and m.get("edge") is not None
    ]
    fight["verdict"] = _verdict_summary(fight)
    return fight


def enrich_plan(plan: dict) -> dict:
    combo_notes = []
    for acca in plan.get("accumulators", []):
        if not acca.get("legs"):
            continue
        combo_notes.append(
            {
                "title": f"{acca['type']} lean",
                "summary": " + ".join(leg.get("selection", "") for leg in acca["legs"]),
                "edge": acca.get("combined_edge"),
            }
        )
    plan["combo_notes"] = combo_notes[:3]
    return plan


def _group_movement_rows(rows: list[dict]) -> list[dict]:
    grouped = {}
    for row in rows:
        fight = row.get("fight") or "Unknown Fight"
        group = grouped.setdefault(
            fight,
            {
                "fight": fight,
                "rows": [],
                "move_count": 0,
                "best_edge": None,
                "largest_delta": None,
            },
        )
        group["rows"].append(row)
        if row.get("movement_label") in {"Shortened", "Drifted", "New Market"}:
            group["move_count"] += 1
        edge = row.get("edge")
        if isinstance(edge, (int, float)):
            if group["best_edge"] is None or edge > group["best_edge"]:
                group["best_edge"] = edge
        delta = row.get("decimal_move")
        if isinstance(delta, (int, float)):
            abs_delta = abs(delta)
            if group["largest_delta"] is None or abs_delta > group["largest_delta"]:
                group["largest_delta"] = abs_delta

    groups = list(grouped.values())
    groups.sort(
        key=lambda group: (
            group["move_count"],
            group["largest_delta"] if isinstance(group["largest_delta"], (int, float)) else 0,
            group["best_edge"] if isinstance(group["best_edge"], (int, float)) else -999,
        ),
        reverse=True,
    )
    return groups


def enrich_movement(report: dict) -> dict:
    report.setdefault("rows", [])
    report.setdefault("noteworthy", [])
    report.setdefault("value_holds", [])
    report.setdefault("moved_rows", report["rows"])
    report.setdefault("significant_move_count", report.get("noteworthy_count", len(report["noteworthy"])))
    report.setdefault("value_hold_count", len(report["value_holds"]))
    report["fight_groups"] = _group_movement_rows(report["rows"])
    report["moved_groups"] = _group_movement_rows(report["moved_rows"])
    report["value_hold_groups"] = _group_movement_rows(report["value_holds"])
    return report


# ── routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    card, fighters, matchups, _ = load_all()
    plan = enrich_plan(load_staking())
    betting = [enrich_fight(fight) for fight in load_betting()]
    active_event = ledger.get_active_event()
    return render_template("index.html",
                           card=card,
                           matchups=matchups,
                           fighters=fighters,
                           plan=plan,
                           betting=betting,
                           active_event=active_event)


@app.route("/fighters")
def fighter_directory():
    _, fighters, _, _ = load_all()
    _enrich_fighters(fighters)

    q        = request.args.get("q", "").strip().lower()
    stance   = request.args.get("stance", "").strip().lower()
    sort_by  = request.args.get("sort", "exp")

    filtered = fighters
    if q:
        filtered = [f for f in filtered if q in f.get("name", "").lower()]
    if stance:
        filtered = [f for f in filtered if f.get("stance", "").lower() == stance]

    if sort_by == "exp":
        tier_order = {"elite": 0, "veteran": 1, "prospect": 2, "newcomer": 3}
        filtered = sorted(filtered, key=lambda f: tier_order.get(f.get("exp_tier", "newcomer"), 3))
    else:
        wc_idx = {wc: i for i, wc in enumerate(WEIGHT_CLASS_ORDER)}
        filtered = sorted(filtered, key=lambda f: wc_idx.get(f.get("weight_class", "Unknown"), 99))

    all_stances = sorted({f.get("stance", "") for f in fighters if f.get("stance", "").strip()})
    return render_template("fighters.html",
                           fighters=filtered,
                           query=q,
                           stances=all_stances,
                           selected_stance=stance,
                           sort_by=sort_by)


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
    fights = [enrich_fight(fight) for fight in load_betting()]
    return render_template("betting.html", card=card, fights=fights)


@app.route("/betting/<fight_id>")
def betting_fight(fight_id: str):
    card, _, _, _ = load_all()
    fights = [enrich_fight(fight) for fight in load_betting()]
    fight = next((item for item in fights if item.get("fight_id") == fight_id), None)
    if not fight:
        abort(404)
    return render_template("betting_fight.html", card=card, fight=fight)


@app.route("/check-movement")
def check_movement_page():
    card, _, _, _ = load_all()
    report = enrich_movement(load_movement())
    return render_template("check_movement.html", card=card, report=report)


@app.route("/bankroll")
def bankroll_page():
    card, _, _, _ = load_all()
    plan = enrich_plan(load_staking())
    return render_template("bankroll.html", card=card, plan=plan)


@app.route("/export")
def export_page():
    card, fighters, matchups, _ = load_all()
    return render_template("export.html",
                           card=card,
                           fighters=fighters,
                           matchups=matchups)


@app.route("/history")
def history_page():
    events = ledger.get_all_events()
    summaries = [ledger.get_event_summary(e["event_id"]) for e in events]
    return render_template("history.html", summaries=summaries)


@app.route("/settle/<event_id>", methods=["GET"])
def settle_page(event_id: str):
    summary = ledger.get_event_summary(event_id)
    if not summary:
        abort(404)
    return render_template("settle.html", summary=summary)


@app.route("/api/settle/<event_id>/preview", methods=["POST"])
def api_settle_preview(event_id: str):
    """
    Dry-run settlement — calculates P&L and validates inputs without committing.
    POST JSON body: {"results": {"<bet_id>": "won"|"lost"|"push"|"void", ...}}
    """
    body = request.get_json(silent=True) or {}
    results = body.get("results", {})
    if not results:
        return jsonify({"error": "No results provided"}), 400
    try:
        preview = ledger.preview_settlement(event_id, results)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("Preview failed for event %s", event_id)
        return jsonify({"error": str(exc)}), 500
    return jsonify(preview)


@app.route("/api/settle/<event_id>", methods=["POST"])
@rate_limit(max_calls=10, window_seconds=60)
def api_settle(event_id: str):
    """
    POST JSON body:
    {
        "results":              {"<bet_id>": "won"|"lost"|"push"|"void"},
        "settled_by":           "web_ui",           (optional, default "web_ui")
        "result_source_detail": "ESPN.com",          (optional)
        "result_confidence":    "confirmed"          (optional)
    }
    Settles all provided bets in one ACID transaction.
    Admin protection: raises if event is already settled or archived.
    """
    # Archived events are physically read-only at the API layer
    event = ledger.get_event(event_id)
    if event and event.get("status") == "archived":
        return jsonify({"error": "Event is archived and read-only."}), 403

    body    = request.get_json(silent=True) or {}
    results = body.get("results", {})
    if not results:
        return jsonify({"error": "No results provided"}), 400

    settled_by           = body.get("settled_by", "web_ui")
    result_source_detail = body.get("result_source_detail")
    result_confidence    = body.get("result_confidence", "confirmed")

    try:
        summary = ledger.settle_event(
            event_id, results,
            settled_by=settled_by,
            result_source_detail=result_source_detail,
            result_confidence=result_confidence,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        log.exception("Settlement failed for event %s", event_id)
        return jsonify({"error": str(exc)}), 500
    return jsonify(summary)


@app.route("/api/event/<event_id>/state", methods=["POST"])
def api_event_state(event_id: str):
    """
    Advance an event to its next state in the lifecycle.
    POST JSON body: {"status": "locked"|"completed"|"archived"|...}
    Enforces forward-only transitions.
    """
    body = request.get_json(silent=True) or {}
    new_status = body.get("status", "").strip()
    if not new_status:
        return jsonify({"error": "Missing 'status' in request body"}), 400
    try:
        updated_event = ledger.advance_event_state(event_id, new_status)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        log.exception("State advance failed for event %s", event_id)
        return jsonify({"error": str(exc)}), 500
    return jsonify(updated_event)


@app.route("/api/event/<event_id>/audit")
def api_event_audit(event_id: str):
    """Return the audit log for a specific event."""
    logs = ledger.get_audit_log(entity_id=event_id)
    return jsonify(logs)


@app.route("/api/locks")
def api_locks():
    """Return currently held pipeline locks."""
    return jsonify(ledger.get_active_locks())


@app.route("/api/bankroll/history")
def api_bankroll_history():
    """Return all post-event bankroll snapshots — equity curve data."""
    return jsonify(ledger.get_bankroll_history())


# ── integrity & observability API ─────────────────────────────────────────────

@app.route("/api/integrity", methods=["GET"])
@require_admin_token
def api_integrity():
    """
    Run all integrity checks against the ledger database.
    Returns a structured report with per-check pass/fail and offending rows.
    """
    try:
        report = _integrity.verify_ledger()
    except Exception as exc:
        log.exception("Integrity check failed")
        return jsonify({"error": str(exc)}), 500
    status_code = 200 if report["ok"] else 207
    return jsonify(report), status_code


@app.route("/api/integrity/rebuild-bankroll", methods=["POST"])
@admin_action(max_calls=5, window_seconds=60)
def api_rebuild_bankroll():
    """
    Recompute bankroll_before/after on every settled bet from scratch.
    POST body: {"dry_run": true|false}  (default dry_run=true)
    """
    body    = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", True))
    try:
        result = _integrity.rebuild_bankroll(dry_run=dry_run)
    except Exception as exc:
        log.exception("rebuild_bankroll failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@app.route("/api/integrity/recover-locks", methods=["POST"])
@admin_action(max_calls=10, window_seconds=60)
def api_recover_locks():
    """Delete all expired pipeline_locks. Safe crash-recovery endpoint."""
    try:
        result = _integrity.recover_stale_locks()
    except Exception as exc:
        log.exception("recover_stale_locks failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@app.route("/api/integrity/replay/<event_id>", methods=["POST"])
@admin_action(max_calls=5, window_seconds=60)
def api_replay_event(event_id: str):
    """
    Walk the settlement of one event, recomputing P&L from scratch.
    POST body: {"dry_run": true|false}  (default dry_run=true)
    """
    body    = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", True))
    try:
        result = _integrity.replay_event(event_id, dry_run=dry_run)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        log.exception("replay_event failed for %s", event_id)
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@app.route("/api/ledger/entries", methods=["GET"])
@app.route("/api/ledger/entries/<event_id>", methods=["GET"])
def api_ledger_entries(event_id: str | None = None):
    """
    Return append-only financial ledger entries.
    Optional ?limit=N query param (default 500).
    """
    limit = int(request.args.get("limit", 500))
    return jsonify(ledger.get_ledger_entries(event_id=event_id, limit=limit))


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """
    Return recent operation timing records.
    Optional ?operation=settle_event&limit=100
    """
    operation = request.args.get("operation")
    limit = int(request.args.get("limit", 100))
    from ledger import get_operation_metrics
    return jsonify(get_operation_metrics(operation=operation, limit=limit))


# ── backup API ────────────────────────────────────────────────────────────────

@app.route("/api/backup", methods=["POST"])
@admin_action(max_calls=10, window_seconds=60)
def api_backup():
    """
    Trigger a manual database backup.
    POST body: {"reason": "manual"}  (optional)
    """
    body   = request.get_json(silent=True) or {}
    reason = body.get("reason", "manual")
    try:
        dest = _backup.backup_db(reason)
    except Exception as exc:
        log.exception("Backup failed")
        return jsonify({"error": str(exc)}), 500
    _slog.backup_op(filename=dest.name, size_bytes=dest.stat().st_size, reason=reason)
    return jsonify({"ok": True, "file": dest.name, "path": str(dest)})


@app.route("/api/backup/list", methods=["GET"])
@require_admin_token
def api_backup_list():
    """Return metadata for all backup files."""
    return jsonify(_backup.list_backups())


@app.route("/api/backup/cleanup", methods=["POST"])
@admin_action(max_calls=5, window_seconds=60)
def api_backup_cleanup():
    """
    Prune old backup files.
    POST body: {"keep_days": 30}  (default 30)
    """
    body      = request.get_json(silent=True) or {}
    keep_days = int(body.get("keep_days", 30))
    try:
        result = _backup.cleanup_old_backups(keep_days=keep_days)
    except Exception as exc:
        log.exception("Backup cleanup failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@app.route("/api/scheduler/status", methods=["GET"])
@require_admin_token
def api_scheduler_status():
    """Return background scheduler state and last maintenance run result."""
    return jsonify(_scheduler.status())


@app.route("/api/scheduler/run", methods=["POST"])
@admin_action(max_calls=3, window_seconds=300)
def api_scheduler_run():
    """Trigger maintenance jobs immediately (on-demand)."""
    try:
        result = _scheduler.run_maintenance()
    except Exception as exc:
        log.exception("On-demand maintenance failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@app.route("/wireframe")
def wireframe():
    card, fighters, matchups, _ = load_all()
    try:
        betting = [enrich_fight(f) for f in load_betting()]
    except Exception:
        betting = []
    return render_template("wireframe.html", card=card, matchups=matchups, fighters=fighters, betting=betting)


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
    from utils import DATA_PROC
    from ledger import BETTING_DIR

    # Structured event log → betting/events.jsonl
    _slog.configure(BETTING_DIR / "events.jsonl")
    _slog.info("app_starting", port=int(os.environ.get("PORT", "5002")))

    # Background nightly maintenance (integrity check + backup + cleanup)
    _scheduler.start(interval_hours=24)

    port = int(os.environ.get("PORT", "5002"))
    log.info("Starting Octagon IQ on http://localhost:%s", port)
    app.run(debug=False, port=port, host="0.0.0.0", use_reloader=False)
