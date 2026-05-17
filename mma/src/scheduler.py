"""
scheduler.py — Background nightly maintenance scheduler.

Runs a daemon thread that executes maintenance jobs on a configurable
interval (default: every 24 hours).

Jobs (in order)
───────────────
1. recover_stale_locks()     — crash recovery: clear expired pipeline locks
2. verify_ledger()           — integrity check: emit structured log on failure
3. backup_db("nightly")      — hot backup of ledger.db
4. cleanup_old_backups()     — prune backup files older than keep_days

Usage
─────
    import scheduler
    scheduler.start(interval_hours=24)     # call once at app startup
    ...
    scheduler.stop()                        # clean shutdown (optional)

The scheduler is designed to run inside the Flask process as a daemon
thread — it will not prevent process exit.  All jobs are wrapped in
try/except so a single failure never kills the loop.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import log as _log

# ── state ─────────────────────────────────────────────────────────────────────

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_last_run: datetime | None = None
_last_result: dict[str, Any] = {}


# ── individual job runners ────────────────────────────────────────────────────

def _job_recover_locks() -> dict:
    from integrity import recover_stale_locks
    result = recover_stale_locks()
    _log.nightly_job("recover_stale_locks", success=True,
                     detail=f"cleared {result['cleared']} lock(s)")
    return result


def _job_verify_ledger() -> dict:
    from integrity import verify_ledger
    report = verify_ledger()
    if report["ok"]:
        _log.nightly_job("verify_ledger", success=True,
                         detail=f"{len(report['checks'])} checks passed")
    else:
        failed = [c for c in report["checks"] if not c["passed"]]
        _log.nightly_job("verify_ledger", success=False,
                         detail=f"{len(failed)} check(s) failed")
        _log.integrity_failure(failed)
    return report


def _job_backup(reason: str = "nightly") -> dict:
    from backup import backup_db
    dest = backup_db(reason)
    size = dest.stat().st_size
    _log.backup_op(filename=dest.name, size_bytes=size, reason=reason)
    _log.nightly_job("backup_db", success=True, detail=dest.name)
    return {"file": dest.name, "size_bytes": size}


def _job_cleanup_backups(keep_days: int = 30) -> dict:
    from backup import cleanup_old_backups
    result = cleanup_old_backups(keep_days=keep_days)
    _log.nightly_job("cleanup_old_backups", success=True,
                     detail=f"deleted {result['deleted']} file(s), freed {result['freed_human']}")
    return result


# ── main tick ─────────────────────────────────────────────────────────────────

def run_maintenance(keep_backup_days: int = 30) -> dict[str, Any]:
    """
    Run all maintenance jobs once.  Returns a summary dict.
    Safe to call manually (e.g., from an admin API endpoint).
    """
    global _last_run, _last_result
    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "jobs":       {},
    }
    _log.info("nightly_maintenance_start")

    for name, fn, kwargs in [
        ("recover_stale_locks", _job_recover_locks, {}),
        ("verify_ledger",        _job_verify_ledger, {}),
        ("backup_db",            _job_backup,        {"reason": "nightly"}),
        ("cleanup_old_backups",  _job_cleanup_backups, {"keep_days": keep_backup_days}),
    ]:
        try:
            summary["jobs"][name] = {"ok": True, "result": fn(**kwargs)}
        except Exception as exc:
            _log.error(f"nightly_job_failed", job=name, error=str(exc))
            summary["jobs"][name] = {"ok": False, "error": str(exc)}

    summary["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _last_run    = datetime.now(timezone.utc)
    _last_result = summary
    _log.info("nightly_maintenance_done",
              ok=all(j["ok"] for j in summary["jobs"].values()))
    return summary


# ── background thread ─────────────────────────────────────────────────────────

def _loop(interval_seconds: int, keep_backup_days: int) -> None:
    """Daemon thread target."""
    _log.info("scheduler_started", interval_seconds=interval_seconds)
    while not _stop_event.is_set():
        # Sleep in short chunks so stop_event is checked promptly
        for _ in range(interval_seconds):
            if _stop_event.is_set():
                break
            time.sleep(1)
        if _stop_event.is_set():
            break
        try:
            run_maintenance(keep_backup_days=keep_backup_days)
        except Exception as exc:
            _log.error("scheduler_tick_failed", error=str(exc))
    _log.info("scheduler_stopped")


def start(interval_hours: float = 24, keep_backup_days: int = 30) -> None:
    """
    Start the background maintenance scheduler as a daemon thread.
    Safe to call multiple times — subsequent calls are no-ops.

    Parameters
    ----------
    interval_hours : float
        How often to run maintenance (default 24 h).
    keep_backup_days : int
        Passed to cleanup_old_backups() (default 30).
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return  # already running

    _stop_event.clear()
    interval_s = max(60, int(interval_hours * 3600))
    _thread = threading.Thread(
        target=_loop,
        args=(interval_s, keep_backup_days),
        daemon=True,
        name="octagoniq-scheduler",
    )
    _thread.start()
    _log.info("scheduler_thread_launched",
              interval_hours=interval_hours, thread=_thread.name)


def stop() -> None:
    """Signal the scheduler to stop after its current sleep."""
    global _thread
    _stop_event.set()
    if _thread:
        _thread.join(timeout=5)
        _thread = None


def status() -> dict[str, Any]:
    """Return current scheduler state for the /api/scheduler/status endpoint."""
    return {
        "running":     _thread is not None and _thread.is_alive(),
        "last_run":    _last_run.strftime("%Y-%m-%dT%H:%M:%SZ") if _last_run else None,
        "last_result": _last_result,
    }
