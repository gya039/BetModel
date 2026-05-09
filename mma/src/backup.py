"""
backup.py — SQLite hot-backup strategy for the MMA betting ledger.

Strategy
────────
• Nightly backup:  caller schedules a daily call to backup_db("nightly").
• Pre-settlement:  backup_pre_settlement(event_id) called by settle_event()
                   wrappers before any mutation.
• Retention:       cleanup_old_backups(keep_days=30) prunes old files.

All backups use SQLite's online backup API (sqlite3.Connection.backup) which
is safe against concurrent WAL writers — no locking required.

Backup directory:  <BETTING_DIR>/backups/
File format:       ledger_<reason>_<YYYYMMDD_HHMMSS>.db
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ledger import DB_PATH, BETTING_DIR, init_db

BACKUP_DIR = BETTING_DIR / "backups"


# ── helpers ───────────────────────────────────────────────────────────────────

def _ts() -> str:
    """Compact UTC timestamp for filenames: 20260510_143022"""
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_reason(reason: str) -> str:
    """Sanitise reason string for use in a filename."""
    import re
    return re.sub(r"[^a-z0-9_-]", "_", reason.lower())[:40]


# ── core backup ───────────────────────────────────────────────────────────────

def backup_db(reason: str = "manual") -> Path:
    """
    Create a hot backup of the ledger database using SQLite's online backup API.

    Parameters
    ----------
    reason : str
        Label embedded in the filename, e.g. 'nightly', 'pre_settlement_ufc315'.

    Returns
    -------
    Path to the backup file.

    Raises
    ------
    FileNotFoundError if the source database has not been initialised.
    """
    init_db()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if not DB_PATH.exists():
        raise FileNotFoundError(f"Source database not found: {DB_PATH}")

    filename = f"ledger_{_safe_reason(reason)}_{_ts()}.db"
    dest = BACKUP_DIR / filename

    # sqlite3 online backup API — safe under WAL concurrent readers/writers
    src_con  = sqlite3.connect(str(DB_PATH))
    dest_con = sqlite3.connect(str(dest))
    try:
        src_con.backup(dest_con, pages=256)
    finally:
        src_con.close()
        dest_con.close()

    return dest


def backup_pre_settlement(event_id: str) -> Path:
    """
    Create a backup labelled 'pre_settlement_<event_id>' before committing
    a settlement.  Call this immediately before settle_event().

    Returns the backup file Path.
    """
    reason = f"pre_settlement_{event_id}"
    return backup_db(reason)


# ── listing / pruning ─────────────────────────────────────────────────────────

def list_backups() -> list[dict[str, Any]]:
    """
    Return metadata for all backup files, newest first.

    Each entry:
    {
        "filename":   str,
        "path":       str,
        "size_bytes": int,
        "created_at": str,   # ISO-8601 UTC (from mtime)
        "reason":     str,   # extracted from filename
    }
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BACKUP_DIR.glob("ledger_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for f in files:
        stat = f.stat()
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        # Extract reason from filename: ledger_<reason>_<YYYYMMDD_HHMMSS>.db
        parts = f.stem.split("_")
        reason = "_".join(parts[1:-2]) if len(parts) >= 4 else "unknown"
        result.append({
            "filename":   f.name,
            "path":       str(f),
            "size_bytes": stat.st_size,
            "size_human": _human_size(stat.st_size),
            "created_at": mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason":     reason,
        })
    return result


def cleanup_old_backups(keep_days: int = 30) -> dict[str, Any]:
    """
    Delete backup files older than *keep_days* days.

    Always keeps at least the 3 most recent backups regardless of age,
    so you're never left with an empty backup directory after extended inactivity.

    Returns:
    {
        "deleted":    int,
        "kept":       int,
        "freed_bytes": int,
        "files":       [str, ...],    # deleted filenames
    }
    """
    from datetime import timedelta

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(BACKUP_DIR.glob("ledger_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    MIN_KEEP = 3

    deleted = []
    freed   = 0

    for i, f in enumerate(files):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if i < MIN_KEEP:
            continue   # always keep the newest MIN_KEEP
        if mtime < cutoff:
            freed += f.stat().st_size
            f.unlink(missing_ok=True)
            deleted.append(f.name)

    return {
        "deleted":     len(deleted),
        "kept":        len(files) - len(deleted),
        "freed_bytes": freed,
        "freed_human": _human_size(freed),
        "files":       deleted,
    }


# ── utility ───────────────────────────────────────────────────────────────────

def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
