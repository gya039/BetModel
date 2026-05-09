"""
integrity.py — Ledger verification, bankroll reconstruction, and crash recovery.

Functions
─────────
verify_ledger()          10 integrity checks; returns structured report.
rebuild_bankroll()       Recompute bankroll_after on every settled bet from scratch.
replay_event()           Walk one event's settlements as if they had just happened.
recover_stale_locks()    Delete expired pipeline_locks and return how many were cleared.

All *dry_run=True* calls are read-only; they never modify the database.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from ledger import (
    DB_PATH,
    BETTING_DIR,
    _BASE_BANKROLL,
    _conn,
    _utcnow,
    _audit,
    _d,
    _f,
    _hash_ledger_entry,
    init_db,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return _utcnow()


# ── main verification report ──────────────────────────────────────────────────

def verify_ledger() -> dict[str, Any]:
    """
    Run a suite of integrity checks against the ledger database.

    Returns a structured report:
    {
        "ok": bool,
        "checks": [
            {"name": str, "passed": bool, "detail": str|None, "rows": list[dict]},
            ...
        ],
        "checked_at": str,
    }
    """
    init_db()
    checks: list[dict] = []

    def _check(name: str, passed: bool, detail: str | None = None,
               rows: list[dict] | None = None) -> None:
        checks.append({"name": name, "passed": passed,
                       "detail": detail, "rows": rows or []})

    with _conn() as con:

        # ── 1. Orphan bets ────────────────────────────────────────────────────
        orphans = con.execute(
            """SELECT b.bet_id, b.event_id FROM bets b
               LEFT JOIN events e ON b.event_id = e.event_id
               WHERE e.event_id IS NULL"""
        ).fetchall()
        _check("no_orphan_bets",
               passed=len(orphans) == 0,
               detail=f"{len(orphans)} orphan bet(s)" if orphans else None,
               rows=[dict(r) for r in orphans])

        # ── 2. Bankroll continuity ────────────────────────────────────────────
        settled = con.execute(
            """SELECT bet_id, fight, bankroll_before, bankroll_after, settled_at
               FROM bets
               WHERE status != 'pending' AND bankroll_after IS NOT NULL
               ORDER BY settled_at ASC"""
        ).fetchall()
        continuity_errors: list[dict] = []
        prev_after = None
        for row in settled:
            r = dict(row)
            if prev_after is not None and r["bankroll_before"] is not None:
                diff = abs(_d(r["bankroll_before"]) - _d(prev_after))
                if diff > _d("0.005"):
                    continuity_errors.append({
                        "bet_id":          r["bet_id"],
                        "fight":           r["fight"],
                        "expected_before": _f(prev_after),
                        "actual_before":   _f(r["bankroll_before"]),
                        "diff":            _f(diff),
                    })
            prev_after = r["bankroll_after"]
        _check("bankroll_continuity",
               passed=len(continuity_errors) == 0,
               detail=f"{len(continuity_errors)} gap(s) in bankroll chain"
                      if continuity_errors else None,
               rows=continuity_errors)

        # ── 3. Missing post-event snapshots ───────────────────────────────────
        missing_snaps = con.execute(
            """SELECT e.event_id, e.event_name, e.status
               FROM events e
               LEFT JOIN bankroll_snapshots s
                 ON e.event_id = s.event_id AND s.snapshot_type = 'post_event'
               WHERE e.status IN ('settled','archived') AND s.id IS NULL"""
        ).fetchall()
        _check("post_event_snapshots_present",
               passed=len(missing_snaps) == 0,
               detail=f"{len(missing_snaps)} event(s) missing post-event snapshot"
                      if missing_snaps else None,
               rows=[dict(r) for r in missing_snaps])

        # ── 4. Duplicate normalised bets ──────────────────────────────────────
        dups = con.execute(
            """SELECT event_id, market_norm, selection_norm, COUNT(*) as n
               FROM bets
               WHERE market_norm IS NOT NULL AND selection_norm IS NOT NULL
               GROUP BY event_id, market_norm, selection_norm
               HAVING n > 1"""
        ).fetchall()
        _check("no_duplicate_normalised_bets",
               passed=len(dups) == 0,
               detail=f"{len(dups)} duplicate (event, market, selection) group(s)"
                      if dups else None,
               rows=[dict(r) for r in dups])

        # ── 5. Stale pipeline locks ───────────────────────────────────────────
        now = _now_utc()
        stale = con.execute(
            "SELECT * FROM pipeline_locks WHERE expires_at < ?", (now,)
        ).fetchall()
        _check("no_stale_locks",
               passed=len(stale) == 0,
               detail=f"{len(stale)} stale lock(s)" if stale else None,
               rows=[dict(r) for r in stale])

        # ── 6. Archived events with pending bets ──────────────────────────────
        arch_pending = con.execute(
            """SELECT b.bet_id, b.fight, b.event_id
               FROM bets b JOIN events e ON b.event_id = e.event_id
               WHERE e.status = 'archived' AND b.status = 'pending'"""
        ).fetchall()
        _check("no_archived_event_with_pending_bets",
               passed=len(arch_pending) == 0,
               detail=f"{len(arch_pending)} pending bet(s) under archived events"
                      if arch_pending else None,
               rows=[dict(r) for r in arch_pending])

        # ── 7. Settled events with pending bets ───────────────────────────────
        settled_pending = con.execute(
            """SELECT b.bet_id, b.fight, b.event_id
               FROM bets b JOIN events e ON b.event_id = e.event_id
               WHERE e.status = 'settled' AND b.status = 'pending'"""
        ).fetchall()
        _check("no_settled_event_with_pending_bets",
               passed=len(settled_pending) == 0,
               detail=f"{len(settled_pending)} pending bet(s) under settled events"
                      if settled_pending else None,
               rows=[dict(r) for r in settled_pending])

        # ── 8. Snapshot consistency ───────────────────────────────────────────
        snap_errors: list[dict] = []
        snaps = con.execute(
            """SELECT s.event_id, s.bankroll_after as snap_after, e.event_name
               FROM bankroll_snapshots s JOIN events e ON s.event_id = e.event_id
               WHERE s.snapshot_type = 'post_event'"""
        ).fetchall()
        for snap in snaps:
            s = dict(snap)
            last_bet = con.execute(
                """SELECT bankroll_after FROM bets
                   WHERE event_id=? AND status!='pending' AND bankroll_after IS NOT NULL
                   ORDER BY settled_at DESC LIMIT 1""",
                (s["event_id"],),
            ).fetchone()
            if last_bet:
                diff = abs(_d(last_bet["bankroll_after"]) - _d(s["snap_after"]))
                if diff > _d("0.005"):
                    snap_errors.append({
                        "event_id":       s["event_id"],
                        "event_name":     s["event_name"],
                        "snap_after":     _f(_d(s["snap_after"])),
                        "last_bet_after": _f(_d(last_bet["bankroll_after"])),
                        "diff":           _f(diff),
                    })
        _check("snapshot_matches_last_bet",
               passed=len(snap_errors) == 0,
               detail=f"{len(snap_errors)} snapshot(s) diverge from last bet row"
                      if snap_errors else None,
               rows=snap_errors)

        # ── 9. Ledger entries internally consistent ───────────────────────────
        ledger_rows = con.execute(
            "SELECT id, amount, balance_after FROM ledger_entries ORDER BY id ASC"
        ).fetchall()
        ledger_errors: list[dict] = []
        if len(ledger_rows) >= 2:
            for i in range(1, len(ledger_rows)):
                prev = dict(ledger_rows[i - 1])
                curr = dict(ledger_rows[i])
                expected = _d(prev["balance_after"]) + _d(curr["amount"])
                actual   = _d(curr["balance_after"])
                if abs(expected - actual) > _d("0.005"):
                    ledger_errors.append({
                        "entry_id":         curr["id"],
                        "expected_balance": _f(expected),
                        "actual_balance":   _f(actual),
                        "diff":             _f(abs(expected - actual)),
                    })
        _check("ledger_entries_internally_consistent",
               passed=len(ledger_errors) == 0,
               detail=f"{len(ledger_errors)} entry(ies) with incorrect balance_after"
                      if ledger_errors else None,
               rows=ledger_errors)

        # ── 10. Audit chain hash integrity ────────────────────────────────────
        hash_errors: list[dict] = []
        chain_rows = con.execute(
            "SELECT id, entry_type, bet_id, event_id, amount, balance_after, "
            "prev_hash, entry_hash, created_at FROM ledger_entries ORDER BY id ASC"
        ).fetchall()
        prev_stored_hash: str | None = None
        for row in chain_rows:
            r = dict(row)
            if not r.get("entry_hash"):
                # Un-hashed entry (pre-v4 data before backfill) — skip
                prev_stored_hash = None
                continue
            prev_h, expected_hash = _hash_ledger_entry(
                prev_stored_hash,
                r["entry_type"], r.get("bet_id"), r.get("event_id"),
                _d(r["amount"]), _d(r["balance_after"]), r["created_at"],
            )
            if expected_hash != r["entry_hash"]:
                hash_errors.append({
                    "entry_id":      r["id"],
                    "entry_type":    r["entry_type"],
                    "stored_hash":   r["entry_hash"][:16] + "…",
                    "expected_hash": expected_hash[:16] + "…",
                })
            prev_stored_hash = r["entry_hash"]

        _check("audit_chain_hash_valid",
               passed=len(hash_errors) == 0,
               detail=f"{len(hash_errors)} entry(ies) with broken hash chain"
                      if hash_errors else None,
               rows=hash_errors)

    all_passed = all(c["passed"] for c in checks)
    return {"ok": all_passed, "checks": checks, "checked_at": _now_utc()}


# ── bankroll reconstruction ───────────────────────────────────────────────────

def rebuild_bankroll(dry_run: bool = True) -> dict[str, Any]:
    """
    Walk every settled bet in chronological order and recompute bankroll_before
    / bankroll_after from scratch using Decimal arithmetic.

    dry_run=True  (default): returns proposed changes, no DB writes.
    dry_run=False           : writes corrected values to bets + snapshots.
    """
    import log as _log
    init_db()
    corrections: list[dict] = []
    running = _BASE_BANKROLL

    with _conn() as con:
        rows = con.execute(
            """SELECT bet_id, fight, stake_eur, pnl, bankroll_before,
                      bankroll_after, status, settled_at
               FROM bets
               WHERE status != 'pending'
               ORDER BY settled_at ASC, id ASC"""
        ).fetchall()

        for row in rows:
            b         = dict(row)
            stake     = _d(b["stake_eur"])
            pnl       = _d(b["pnl"] or 0)
            new_before = running

            if b["status"] in ("push", "void"):
                new_after = running + stake
            else:
                new_after = running + stake + pnl

            old_before = b["bankroll_before"]
            old_after  = b["bankroll_after"]
            changed = (
                old_before is None or abs(_d(old_before) - new_before) > _d("0.005") or
                old_after  is None or abs(_d(old_after)  - new_after)  > _d("0.005")
            )

            if changed:
                corrections.append({
                    "bet_id":     b["bet_id"],
                    "fight":      b["fight"],
                    "status":     b["status"],
                    "old_before": _f(_d(old_before)) if old_before is not None else None,
                    "new_before": _f(new_before),
                    "old_after":  _f(_d(old_after))  if old_after  is not None else None,
                    "new_after":  _f(new_after),
                })
                if not dry_run:
                    now = _utcnow()
                    con.execute(
                        "UPDATE bets SET bankroll_before=?, bankroll_after=?, updated_at=? "
                        "WHERE bet_id=?",
                        (_f(new_before), _f(new_after), now, b["bet_id"]),
                    )
                    _audit(con, "bankroll_rebuilt", "bet", b["bet_id"],
                           detail=(f"before: {old_before}→{_f(new_before)} "
                                   f"after: {old_after}→{_f(new_after)}"))

            running = new_after

        if not dry_run:
            _rebuild_snapshots(con)

    _log.replay_op("ALL", dry_run=dry_run, corrections=len(corrections))
    return {
        "dry_run":        dry_run,
        "corrections":    corrections,
        "final_bankroll": _f(running),
        "rebuilt_at":     _utcnow(),
    }


def _rebuild_snapshots(con) -> None:
    """Recalculate bankroll_after on all post_event snapshots from last settled bet."""
    events = con.execute(
        "SELECT event_id FROM events WHERE status IN ('settled','archived')"
    ).fetchall()
    for ev in events:
        eid  = ev["event_id"]
        last = con.execute(
            """SELECT bankroll_after FROM bets
               WHERE event_id=? AND status!='pending' AND bankroll_after IS NOT NULL
               ORDER BY settled_at DESC LIMIT 1""",
            (eid,),
        ).fetchone()
        if last:
            pnl_row = con.execute(
                "SELECT SUM(pnl) as total_pnl FROM bets WHERE event_id=?", (eid,)
            ).fetchone()
            total_pnl = _f(_d(pnl_row["total_pnl"] or 0))
            con.execute(
                """UPDATE bankroll_snapshots
                   SET bankroll_after=?, event_pnl=?
                   WHERE event_id=? AND snapshot_type='post_event'""",
                (_f(_d(last["bankroll_after"])), total_pnl, eid),
            )


# ── event replay ──────────────────────────────────────────────────────────────

def replay_event(event_id: str, dry_run: bool = True) -> dict[str, Any]:
    """
    Walk the settlement of a single event, recomputing P&L with Decimal
    from its pre-event bankroll snapshot.

    dry_run=True  (default): read-only; returns projected values.
    dry_run=False           : writes corrected pnl / bankroll values.
    """
    import log as _log
    init_db()

    with _conn() as con:
        event = con.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            raise ValueError(f"Event '{event_id}' not found")
        event = dict(event)

        pre_snap = con.execute(
            "SELECT bankroll_after FROM bankroll_snapshots "
            "WHERE event_id=? AND snapshot_type='pre_event'",
            (event_id,),
        ).fetchone()
        start = _d(pre_snap["bankroll_after"]) if pre_snap else _BASE_BANKROLL

        bets = con.execute(
            "SELECT * FROM bets WHERE event_id=? AND status != 'pending' "
            "ORDER BY settled_at ASC, id ASC",
            (event_id,),
        ).fetchall()
        bets = [dict(b) for b in bets]

        running      = start
        total_pnl    = _d("0")
        total_staked = _d("0")
        bet_rows: list[dict] = []

        for bet in bets:
            stake  = _d(bet["stake_eur"])
            status = bet["status"]

            if status == "won":
                pnl = _d(bet["potential_return"]) - stake
            elif status == "lost":
                pnl = -stake
            else:
                pnl = _d("0")

            b_before = running
            b_after  = running + stake + pnl
            running  = b_after
            total_pnl    = total_pnl    + pnl
            total_staked = total_staked + stake

            diff_before = abs(_d(bet["bankroll_before"] or 0) - b_before)
            diff_after  = abs(_d(bet["bankroll_after"]  or 0) - b_after)
            diff_pnl    = abs(_d(bet["pnl"]             or 0) - pnl)
            changed     = (diff_before > _d("0.005") or
                           diff_after  > _d("0.005") or
                           diff_pnl    > _d("0.005"))

            bet_rows.append({
                "bet_id":          bet["bet_id"],
                "fight":           bet["fight"],
                "selection":       bet["selection"],
                "status":          status,
                "stake_eur":       _f(stake),
                "odds":            bet["odds"],
                "pnl_stored":      _f(_d(bet["pnl"] or 0)),
                "pnl_replayed":    _f(pnl),
                "before_stored":   _f(_d(bet["bankroll_before"] or 0)),
                "before_replayed": _f(b_before),
                "after_stored":    _f(_d(bet["bankroll_after"] or 0)),
                "after_replayed":  _f(b_after),
                "changed":         changed,
            })

            if not dry_run and changed:
                now = _utcnow()
                con.execute(
                    "UPDATE bets SET pnl=?, bankroll_before=?, bankroll_after=?, "
                    "updated_at=? WHERE bet_id=?",
                    (_f(pnl), _f(b_before), _f(b_after), now, bet["bet_id"]),
                )
                _audit(con, "bet_replayed", "bet", bet["bet_id"],
                       detail=(f"pnl {bet['pnl']}→{_f(pnl)} "
                               f"after {bet['bankroll_after']}→{_f(b_after)}"))

        if not dry_run:
            _rebuild_snapshots(con)

    corrections = sum(1 for b in bet_rows if b["changed"])
    _log.replay_op(event_id, dry_run=dry_run, corrections=corrections,
                   total_pnl=_f(total_pnl))
    return {
        "event_id":        event_id,
        "event_name":      event["event_name"],
        "dry_run":         dry_run,
        "bets":            bet_rows,
        "total_staked":    _f(total_staked),
        "total_pnl":       _f(total_pnl),
        "bankroll_before": _f(start),
        "bankroll_after":  _f(running),
        "replayed_at":     _utcnow(),
    }


# ── crash recovery ────────────────────────────────────────────────────────────

def recover_stale_locks() -> dict[str, Any]:
    """Delete all expired pipeline_locks. Safe to call on startup for crash recovery."""
    init_db()
    now = _utcnow()
    with _conn() as con:
        stale = con.execute(
            "SELECT * FROM pipeline_locks WHERE expires_at < ?", (now,)
        ).fetchall()
        stale_list = [dict(r) for r in stale]
        if stale_list:
            con.execute("DELETE FROM pipeline_locks WHERE expires_at < ?", (now,))
            for lock in stale_list:
                _audit(con, "lock_recovered", "lock", lock["lock_name"],
                       detail=f"stale lock cleared (was held by {lock['locked_by']})")
    return {"cleared": len(stale_list), "locks": stale_list, "recovered_at": now}
