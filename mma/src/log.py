"""
log.py — Structured JSON event logging for the MMA betting platform.

Events are emitted as JSON lines to:
  • stdout   (always, at INFO level)
  • <betting_dir>/events.jsonl   (if configure() has been called)

Usage
─────
    import log
    log.configure(BETTING_DIR / "events.jsonl")   # once at startup

    log.info("bet_placed", event_id=eid, stake=10.0)
    log.settlement(event_id, settled, total_pnl, bankroll_after)
    log.integrity_failure(failed_checks)

Domain helpers
──────────────
log.settlement(...)     committed settlement
log.migration(...)      schema migration applied
log.lock_contention(...)  pipeline lock blocked
log.integrity_failure(...)  verify_ledger() found violations
log.replay_op(...)      replay_event() completed
log.backup_op(...)      backup created
log.nightly_job(...)    scheduler tick
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── internal state ────────────────────────────────────────────────────────────

_log_path: Path | None = None
_lock = threading.Lock()
_root = logging.getLogger("octagoniq")
_configured = False


# ── formatter ─────────────────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        d: dict[str, Any] = {
            "ts":     ts,
            "level":  record.levelname.lower(),
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "_extra"):
            d.update(record._extra)  # type: ignore[attr-defined]
        return json.dumps(d, default=str)


# ── configuration ─────────────────────────────────────────────────────────────

def configure(log_path: Path | None = None, level: int = logging.INFO) -> None:
    """
    Configure the structured logger.  Call once at application startup.

    Parameters
    ----------
    log_path : Path, optional
        If provided, events are appended to this file in JSON-lines format.
    level : int
        Logging level (default INFO).
    """
    global _log_path, _configured
    _log_path = log_path
    if _log_path:
        _log_path.parent.mkdir(parents=True, exist_ok=True)

    if not _configured:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JSONFormatter())
        _root.addHandler(handler)
        _root.setLevel(level)
        _root.propagate = False
        _configured = True


def _auto_configure() -> None:
    """Auto-configure with stdout-only output if not already set up."""
    if not _configured:
        configure()


# ── core emitter ──────────────────────────────────────────────────────────────

def _emit(level: str, msg: str, **extra: Any) -> None:
    _auto_configure()
    lvl_int = getattr(logging, level.upper(), logging.INFO)

    record = _root.makeRecord(
        _root.name, lvl_int, "(event)", 0, msg, (), None
    )
    record._extra = extra  # type: ignore[attr-defined]
    _root.handle(record)

    # Append to JSONL file if configured
    if _log_path:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        line = json.dumps(
            {"ts": ts, "level": level, "msg": msg, **extra}, default=str
        )
        with _lock:
            with _log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")


# ── general purpose ───────────────────────────────────────────────────────────

def info(msg: str, **extra: Any) -> None:
    _emit("info", msg, **extra)


def warning(msg: str, **extra: Any) -> None:
    _emit("warning", msg, **extra)


def error(msg: str, **extra: Any) -> None:
    _emit("error", msg, **extra)


def debug(msg: str, **extra: Any) -> None:
    _emit("debug", msg, **extra)


# ── domain helpers ────────────────────────────────────────────────────────────

def settlement(
    event_id: str,
    settled: list,
    total_pnl: float,
    bankroll_after: float,
    settled_by: str = "system",
) -> None:
    info(
        "settlement_committed",
        event_id=event_id,
        settled_count=len(settled),
        total_pnl=total_pnl,
        bankroll_after=bankroll_after,
        settled_by=settled_by,
    )


def migration(from_version: int, to_version: int, detail: str = "") -> None:
    info("schema_migration", from_version=from_version, to_version=to_version,
         detail=detail)


def lock_contention(lock_name: str, holder: str = "") -> None:
    warning("lock_contention", lock_name=lock_name, current_holder=holder)


def integrity_failure(failed_checks: list[dict]) -> None:
    names = [c.get("name") for c in failed_checks]
    error("integrity_failure", failed_checks=names, count=len(failed_checks))


def replay_op(
    event_id: str,
    dry_run: bool,
    corrections: int,
    total_pnl: float | None = None,
) -> None:
    info(
        "replay_operation",
        event_id=event_id,
        dry_run=dry_run,
        corrections=corrections,
        total_pnl=total_pnl,
    )


def backup_op(filename: str, size_bytes: int, reason: str) -> None:
    info("backup_created", filename=filename, size_bytes=size_bytes, reason=reason)


def nightly_job(job: str, success: bool, detail: str | None = None) -> None:
    lvl = "info" if success else "error"
    _emit(lvl, "nightly_job", job=job, success=success, detail=detail)
