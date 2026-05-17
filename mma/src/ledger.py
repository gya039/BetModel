"""
ledger.py — SQLite-backed persistence for the MMA betting lifecycle.

╔══════════════════════════════════════════════════════════════════════╗
║  EVENT STATE MACHINE                                                 ║
║                                                                      ║
║  scheduled → betting_open → locked → completed → settled → archived ║
║                                                                      ║
║  scheduled    Event registered; no bets placed yet.                 ║
║  betting_open Bets placed and pending. Pipeline blocked for others.  ║
║  locked       Event is live/in-progress. Bet window closed.         ║
║  completed    Results are in. Awaiting settlement entry.             ║
║  settled      All bets resolved. Bankroll snapshot written.          ║
║  archived     Historical record. Read-only — no modifications.       ║
╚══════════════════════════════════════════════════════════════════════╝

FINANCIAL ARITHMETIC
  All monetary calculations use Python's decimal.Decimal with 2-decimal-
  place quantization and ROUND_HALF_UP.  Floats are only used at SQLite
  storage boundaries (_f()) and external JSON-serialisable return values.

FINANCIAL LEDGER (append-only + chain-hashed)
  bet_placed  amount = –stake          Stake committed when bet is placed.
  bet_won     amount = +return         Gross return credited on win.
  bet_lost    amount =  0              Nothing returned; stake already debited.
  bet_push    amount = +stake          Stake refunded (no-result).
  bet_void    amount = +stake          Stake refunded (cancelled).
  deposit     amount = +amount         Manual bankroll top-up.
  withdrawal  amount = –amount         Manual bankroll withdrawal.
  adjustment  amount = ±amount         Correction entry (audited).

  Each entry stores the SHA-256 hash of (prev_hash ‖ entry data), forming
  a tamper-evident chain.  Verify integrity with integrity.verify_ledger().

IDEMPOTENCY GUARANTEES
  register_event()   — INSERT OR IGNORE; re-run updates snapshot only.
  place_bets()       — INSERT OR IGNORE on deterministic SHA-256 bet_id.
  settle_event()     — Skips bets not in 'pending' state.
  acquire_lock()     — Atomic INSERT; returns False if lock is held.

PROVENANCE
  Every settled bet records:
    settled_by           — 'system', 'web_ui', 'api', or custom string
    result_source_detail — free-text source description
    result_confidence    — 'confirmed', 'provisional', 'corrected'

ADMIN PROTECTIONS
  • Cannot settle an event in 'settled' or 'archived' state.
  • Cannot place bets if event is past 'betting_open'.
  • Cannot archive a non-settled event.
  • Cannot delete events — only archive.
  • All state changes are written to audit_log.
  • Pipeline lock prevents concurrent generation/settlement.

SCHEMA VERSION: 4
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import socket
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from utils import DATA_PROC

# ── paths ─────────────────────────────────────────────────────────────────────

BETTING_DIR      = DATA_PROC / "betting"
DB_PATH          = BETTING_DIR / "ledger.db"
_BET_HISTORY_CSV = BETTING_DIR / "bet_history.csv"

_BASE_BANKROLL  = Decimal("500.00")
_TWO            = Decimal("0.01")
_SCHEMA_VERSION = 5

# Valid state sequences — used for guard checks
_BLOCKING_STATES   = frozenset({"betting_open", "locked", "completed"})
_MUTABLE_STATES    = frozenset({"scheduled", "betting_open", "locked", "completed"})
_SETTLEABLE_STATES = frozenset({"betting_open", "locked", "completed"})


# ── decimal helpers ───────────────────────────────────────────────────────────

def _d(value: Any) -> Decimal:
    """Convert any numeric value to Decimal quantized to 2 dp (ROUND_HALF_UP)."""
    if value is None:
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(_TWO, rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(value)).quantize(_TWO, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return Decimal("0.00")


def _f(value: Decimal | Any) -> float:
    """Convert Decimal (or numeric) to float for SQLite storage."""
    return float(value)


# ── internal helpers ──────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bet_id(event_id: str, fight_id: str | None, market: str, selection: str,
            leg_index: int = 0) -> str:
    """Deterministic 24-char ID — same inputs always produce the same value."""
    raw = f"{event_id}|{fight_id or 'acca'}|{market}|{selection}|{leg_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def event_id_from_url(url: str) -> str:
    """Extract the last path segment of a UFCStats URL as the stable event ID."""
    if url:
        segment = url.rstrip("/").split("/")[-1].strip()
        if segment:
            return segment
    import uuid
    return str(uuid.uuid4()).replace("-", "")[:16]


def make_canonical_id(event_name: str, event_date: str) -> str:
    """
    Generate a deterministic human-readable slug for an event.

    make_canonical_id("UFC 315: Muhammad vs Della Maddalena", "2026-05-10")
    → "ufc-315-muhammad-vs-della-maddalena-2026-05-10"
    """
    slug = re.sub(r"[^a-z0-9]+", "-", event_name.lower()).strip("-")
    date_part = (event_date or "")[:10]
    return f"{slug}-{date_part}" if date_part else slug


def _process_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# ── audit chain hashing ───────────────────────────────────────────────────────

def _hash_ledger_entry(
    prev_hash: str | None,
    entry_type: str,
    bet_id: str | None,
    event_id: str | None,
    amount: Decimal,
    balance_after: Decimal,
    created_at: str,
) -> tuple[str, str]:
    """
    Compute the chain hash for a ledger entry.

    Returns (prev_hash_used, entry_hash).
    The chain is: SHA-256(prev_hash ‖ entry_type ‖ bet_id ‖ event_id ‖ amount ‖ balance ‖ ts)
    Genesis entries use "GENESIS" as prev_hash.
    """
    prev = prev_hash or "GENESIS"
    data = (
        f"{prev}|{entry_type}|{bet_id or ''}|{event_id or ''}"
        f"|{amount}|{balance_after}|{created_at}"
    )
    entry_hash = hashlib.sha256(data.encode()).hexdigest()
    return prev, entry_hash


def _backfill_ledger_hashes(con) -> int:
    """
    Back-fill chain hashes for existing ledger_entries that have no hash.
    Called once during v3→v4 migration.  Returns number of rows updated.
    """
    rows = con.execute(
        "SELECT id, entry_type, bet_id, event_id, amount, balance_after, created_at, entry_hash "
        "FROM ledger_entries ORDER BY id ASC"
    ).fetchall()

    updated = 0
    prev_hash: str | None = None
    for row in rows:
        r = dict(row)
        # Chain continues from last known good hash even if this one exists
        if r.get("entry_hash"):
            prev_hash = r["entry_hash"]
            continue
        prev_h, entry_h = _hash_ledger_entry(
            prev_hash, r["entry_type"], r.get("bet_id"), r.get("event_id"),
            _d(r["amount"]), _d(r["balance_after"]), r["created_at"],
        )
        con.execute(
            "UPDATE ledger_entries SET prev_hash=?, entry_hash=? WHERE id=?",
            (prev_h, entry_h, r["id"]),
        )
        prev_hash = entry_h
        updated += 1
    return updated


# ── connection ────────────────────────────────────────────────────────────────

@contextmanager
def _conn():
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL_EVENTS = """
    CREATE TABLE IF NOT EXISTS events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id     TEXT    UNIQUE NOT NULL,
        canonical_id TEXT,
        event_name   TEXT    NOT NULL,
        event_date   TEXT    NOT NULL,
        event_url    TEXT,
        status       TEXT    NOT NULL DEFAULT 'scheduled'
                     CHECK(status IN (
                         'scheduled','betting_open','locked',
                         'completed','settled','archived'
                     )),
        card_json    TEXT,
        edges_json   TEXT,
        created_at   TEXT    NOT NULL,
        updated_at   TEXT    NOT NULL,
        settled_at   TEXT
    );
"""

_DDL_BETS = """
    CREATE TABLE IF NOT EXISTS bets (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        bet_id               TEXT    UNIQUE NOT NULL,
        event_id             TEXT    NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
        bet_type             TEXT    NOT NULL
                             CHECK(bet_type IN ('single','double','treble','4fold')),
        fight_id             TEXT,
        fight                TEXT    NOT NULL,
        market               TEXT    NOT NULL,
        market_norm          TEXT,
        selection            TEXT    NOT NULL,
        selection_norm       TEXT,
        sportsbook           TEXT,
        sportsbook_norm      TEXT,
        odds                 REAL    NOT NULL,
        stake_eur            REAL    NOT NULL,
        potential_return     REAL    NOT NULL,
        strategy             TEXT    NOT NULL DEFAULT 'model_v1',
        status               TEXT    NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending','won','lost','push','void')),
        result_source        TEXT    NOT NULL DEFAULT 'manual'
                             CHECK(result_source IN ('manual','api','auto')),
        result_notes         TEXT,
        settled_by           TEXT    NOT NULL DEFAULT 'system',
        result_source_detail TEXT,
        result_confidence    TEXT    NOT NULL DEFAULT 'confirmed'
                             CHECK(result_confidence IN ('confirmed','provisional','corrected')),
        pnl                  REAL,
        bankroll_before      REAL,
        bankroll_after       REAL,
        placed_at            TEXT    NOT NULL,
        updated_at           TEXT    NOT NULL,
        settled_at           TEXT
    );
"""

_DDL_SNAPSHOTS = """
    CREATE TABLE IF NOT EXISTS bankroll_snapshots (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id        TEXT    NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
        snapshot_type   TEXT    NOT NULL
                        CHECK(snapshot_type IN ('pre_event','post_event','manual')),
        bankroll_before REAL,
        bankroll_after  REAL    NOT NULL,
        event_pnl       REAL,
        total_staked    REAL,
        bets_won        INTEGER DEFAULT 0,
        bets_lost       INTEGER DEFAULT 0,
        bets_push       INTEGER DEFAULT 0,
        snapshot_at     TEXT    NOT NULL
    );
"""

_DDL_LOCKS = """
    CREATE TABLE IF NOT EXISTS pipeline_locks (
        lock_name   TEXT    PRIMARY KEY,
        locked_by   TEXT    NOT NULL,
        locked_at   TEXT    NOT NULL,
        expires_at  TEXT    NOT NULL
    );
"""

_DDL_AUDIT = """
    CREATE TABLE IF NOT EXISTS audit_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        action       TEXT    NOT NULL,
        entity_type  TEXT    NOT NULL
                     CHECK(entity_type IN ('event','bet','bankroll','lock','ledger')),
        entity_id    TEXT    NOT NULL,
        old_status   TEXT,
        new_status   TEXT,
        detail       TEXT,
        performed_at TEXT    NOT NULL,
        performed_by TEXT    NOT NULL DEFAULT 'system'
    );
"""

_DDL_LEDGER_ENTRIES = """
    CREATE TABLE IF NOT EXISTS ledger_entries (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_type    TEXT    NOT NULL
                      CHECK(entry_type IN (
                          'bet_placed','bet_won','bet_lost','bet_push','bet_void',
                          'deposit','withdrawal','adjustment'
                      )),
        bet_id        TEXT    REFERENCES bets(bet_id),
        event_id      TEXT    REFERENCES events(event_id),
        amount        REAL    NOT NULL,
        balance_after REAL    NOT NULL,
        prev_hash     TEXT,
        entry_hash    TEXT,
        description   TEXT,
        created_at    TEXT    NOT NULL
    );
"""

_DDL_OPERATION_METRICS = """
    CREATE TABLE IF NOT EXISTS operation_metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        operation    TEXT    NOT NULL,
        event_id     TEXT,
        started_at   TEXT    NOT NULL,
        finished_at  TEXT    NOT NULL,
        duration_ms  INTEGER NOT NULL,
        success      INTEGER NOT NULL DEFAULT 1,
        error_msg    TEXT,
        detail       TEXT
    );
"""

_DDL_SCHEMA_VER = """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER NOT NULL
    );
"""

_DDL_INDEXES = """
    CREATE INDEX IF NOT EXISTS idx_bets_event       ON bets(event_id);
    CREATE INDEX IF NOT EXISTS idx_bets_status      ON bets(status);
    CREATE INDEX IF NOT EXISTS idx_bets_sportsbook  ON bets(sportsbook_norm);
    CREATE INDEX IF NOT EXISTS idx_bets_strategy    ON bets(strategy);
    CREATE INDEX IF NOT EXISTS idx_snapshots_event  ON bankroll_snapshots(event_id);
    CREATE INDEX IF NOT EXISTS idx_audit_entity     ON audit_log(entity_type, entity_id);
    CREATE INDEX IF NOT EXISTS idx_audit_time       ON audit_log(performed_at);
    CREATE INDEX IF NOT EXISTS idx_ledger_event     ON ledger_entries(event_id);
    CREATE INDEX IF NOT EXISTS idx_ledger_bet       ON ledger_entries(bet_id);
    CREATE INDEX IF NOT EXISTS idx_ledger_type      ON ledger_entries(entry_type);
    CREATE INDEX IF NOT EXISTS idx_metrics_op       ON operation_metrics(operation);
    CREATE INDEX IF NOT EXISTS idx_events_canonical ON events(canonical_id);
"""


# ── schema version helpers ────────────────────────────────────────────────────

def _get_schema_version(con) -> int:
    try:
        row = con.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_schema_version(con, version: int) -> None:
    con.execute("DELETE FROM schema_version")
    con.execute("INSERT INTO schema_version VALUES (?)", (version,))


# ── migrations ────────────────────────────────────────────────────────────────

def _migrate_v1_to_v2(con) -> None:
    """Migrate schema v1 → v2 using rename-copy-drop."""
    now = _utcnow()

    events_exists = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    if events_exists:
        cols = {row[1] for row in con.execute("PRAGMA table_info(events)").fetchall()}
        if "updated_at" not in cols:
            _STATUS_MAP = {"pending": "scheduled", "active": "betting_open",
                           "settling": "completed", "settled": "settled"}
            old_rows = con.execute("SELECT * FROM events").fetchall()
            con.execute("ALTER TABLE events RENAME TO events_v1_backup")
            con.executescript(_DDL_EVENTS)
            for row in old_rows:
                r = dict(row)
                con.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, event_name, event_date, event_url, status,
                        card_json, edges_json, created_at, updated_at, settled_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (r["event_id"], r["event_name"], r["event_date"], r.get("event_url"),
                     _STATUS_MAP.get(r.get("status", ""), "scheduled"),
                     r.get("card_json"), r.get("edges_json"),
                     r.get("created_at", now), now, r.get("settled_at")),
                )
        else:
            for old, new in {"pending": "scheduled", "active": "betting_open",
                              "settling": "completed"}.items():
                con.execute("UPDATE events SET status=? WHERE status=?", (new, old))
            con.execute(
                "UPDATE events SET updated_at=created_at WHERE updated_at IS NULL OR updated_at=''"
            )

    bets_exists = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='bets'"
    ).fetchone()
    if bets_exists:
        bet_cols = {row[1] for row in con.execute("PRAGMA table_info(bets)").fetchall()}
        if "updated_at" not in bet_cols or "strategy" not in bet_cols:
            old_bets = con.execute("SELECT * FROM bets").fetchall()
            con.execute("ALTER TABLE bets RENAME TO bets_v1_backup")
            con.executescript(_DDL_BETS)
            for row in old_bets:
                r = dict(row)
                con.execute(
                    """INSERT OR IGNORE INTO bets
                       (bet_id, event_id, bet_type, fight_id, fight, market,
                        selection, sportsbook, odds, stake_eur, potential_return,
                        status, result_notes, pnl, bankroll_before, bankroll_after,
                        placed_at, updated_at, settled_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (r["bet_id"], r["event_id"], r["bet_type"], r.get("fight_id"),
                     r["fight"], r["market"], r["selection"], r.get("sportsbook"),
                     r["odds"], r["stake_eur"], r["potential_return"],
                     r.get("status", "pending"), r.get("result_notes"),
                     r.get("pnl"), r.get("bankroll_before"), r.get("bankroll_after"),
                     r.get("placed_at", now), now, r.get("settled_at")),
                )


def _migrate_v2_to_v3(con) -> None:
    """Add canonical_id, ledger_entries, operation_metrics."""
    event_cols = {row[1] for row in con.execute("PRAGMA table_info(events)").fetchall()}
    if "canonical_id" not in event_cols:
        con.execute("ALTER TABLE events ADD COLUMN canonical_id TEXT")

    rows = con.execute("SELECT event_id, event_name, event_date FROM events").fetchall()
    for row in rows:
        cid = make_canonical_id(row["event_name"], row["event_date"])
        con.execute(
            "UPDATE events SET canonical_id=? WHERE event_id=? AND (canonical_id IS NULL OR canonical_id='')",
            (cid, row["event_id"]),
        )
    con.executescript(_DDL_LEDGER_ENTRIES + _DDL_OPERATION_METRICS)


def _migrate_v3_to_v4(con) -> None:
    """
    Add provenance columns to bets, add chain-hash columns to ledger_entries,
    back-fill chain hashes for existing entries.
    """
    import log as _log
    _log.migration(3, 4, "adding provenance + audit chain hashes")

    # ── bets: provenance columns ──────────────────────────────────────────────
    # bets table may not exist in very old schemas (v2 had no bets table)
    existing_tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "bets" in existing_tables:
        bet_cols = {row[1] for row in con.execute("PRAGMA table_info(bets)").fetchall()}
        if "settled_by" not in bet_cols:
            con.execute("ALTER TABLE bets ADD COLUMN settled_by TEXT NOT NULL DEFAULT 'system'")
        if "result_source_detail" not in bet_cols:
            con.execute("ALTER TABLE bets ADD COLUMN result_source_detail TEXT")
        if "result_confidence" not in bet_cols:
            con.execute("ALTER TABLE bets ADD COLUMN result_confidence TEXT NOT NULL DEFAULT 'confirmed'")

    # ── ledger_entries: chain-hash columns ────────────────────────────────────
    ledger_cols = {row[1] for row in con.execute("PRAGMA table_info(ledger_entries)").fetchall()}
    if "prev_hash" not in ledger_cols:
        con.execute("ALTER TABLE ledger_entries ADD COLUMN prev_hash TEXT")
    if "entry_hash" not in ledger_cols:
        con.execute("ALTER TABLE ledger_entries ADD COLUMN entry_hash TEXT")

    # Back-fill hashes for existing entries
    updated = _backfill_ledger_hashes(con)
    _log.info("ledger_hash_backfill_complete", entries_updated=updated)


def _migrate_v4_to_v5(con) -> None:
    """Add strategy metadata columns to bets (edge, confidence, acca_legs_json)."""
    import log as _log
    _log.migration(4, 5, "adding edge/confidence/acca_legs to bets")
    bet_cols = {row[1] for row in con.execute("PRAGMA table_info(bets)").fetchall()}
    if "edge" not in bet_cols:
        con.execute("ALTER TABLE bets ADD COLUMN edge REAL")
    if "confidence" not in bet_cols:
        con.execute("ALTER TABLE bets ADD COLUMN confidence TEXT")
    if "acca_legs_json" not in bet_cols:
        con.execute("ALTER TABLE bets ADD COLUMN acca_legs_json TEXT")


def _rebuild_indexes(con) -> None:
    """Drop and recreate all indexes, skipping tables that don't exist yet."""
    for idx in [
        "idx_bets_event", "idx_bets_status", "idx_bets_sportsbook",
        "idx_bets_strategy", "idx_snapshots_event", "idx_audit_entity",
        "idx_audit_time", "idx_ledger_event", "idx_ledger_bet",
        "idx_ledger_type", "idx_metrics_op", "idx_events_canonical",
    ]:
        con.execute(f"DROP INDEX IF EXISTS {idx}")

    existing = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    per_table: dict[str, str] = {
        "bets": (
            "CREATE INDEX IF NOT EXISTS idx_bets_event ON bets(event_id);\n"
            "CREATE INDEX IF NOT EXISTS idx_bets_status ON bets(status);\n"
            "CREATE INDEX IF NOT EXISTS idx_bets_sportsbook ON bets(sportsbook_norm);\n"
            "CREATE INDEX IF NOT EXISTS idx_bets_strategy ON bets(strategy);\n"
        ),
        "bankroll_snapshots":
            "CREATE INDEX IF NOT EXISTS idx_snapshots_event ON bankroll_snapshots(event_id);\n",
        "audit_log": (
            "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);\n"
            "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(performed_at);\n"
        ),
        "ledger_entries": (
            "CREATE INDEX IF NOT EXISTS idx_ledger_event ON ledger_entries(event_id);\n"
            "CREATE INDEX IF NOT EXISTS idx_ledger_bet ON ledger_entries(bet_id);\n"
            "CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger_entries(entry_type);\n"
        ),
        "operation_metrics":
            "CREATE INDEX IF NOT EXISTS idx_metrics_op ON operation_metrics(operation);\n",
        "events":
            "CREATE INDEX IF NOT EXISTS idx_events_canonical ON events(canonical_id);\n",
    }
    for table, ddl in per_table.items():
        if table in existing:
            con.executescript(ddl)


def _repair_stale_fks(con) -> None:
    """
    Detect and fix tables whose FK targets are *_backup tables — an artefact of
    SQLite's ALTER TABLE RENAME not updating FK text in dependent tables.
    Runs on every startup; no-op when all FKs are correct.
    """
    _REPAIR = {
        "bankroll_snapshots": (
            "event_id", "events_v1_backup",
            lambda: con.execute("""
                CREATE TABLE bankroll_snapshots_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id        TEXT    NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
                    snapshot_type   TEXT    NOT NULL
                                    CHECK(snapshot_type IN ('pre_event','post_event','manual')),
                    bankroll_before REAL,
                    bankroll_after  REAL    NOT NULL,
                    event_pnl       REAL,
                    total_staked    REAL,
                    bets_won        INTEGER DEFAULT 0,
                    bets_lost       INTEGER DEFAULT 0,
                    bets_push       INTEGER DEFAULT 0,
                    snapshot_at     TEXT    NOT NULL
                )
            """),
        ),
    }
    con.execute("PRAGMA foreign_keys=OFF")
    for table, (col, bad_target, make_new) in _REPAIR.items():
        exists = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        fks = con.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        broken = any(fk["table"] == bad_target for fk in fks)
        if not broken:
            continue
        count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if count > 0:
            rows = con.execute(f"SELECT * FROM {table}").fetchall()
            cols = [d[0] for d in con.execute(f"PRAGMA table_info({table})").fetchall()]
        make_new()
        if count > 0:
            placeholders = ",".join("?" * len(cols))
            col_list = ",".join(cols[1:])  # skip autoincrement id
            ph_list = ",".join("?" * (len(cols) - 1))
            for row in rows:
                con.execute(
                    f"INSERT INTO {table}_new ({col_list}) VALUES ({ph_list})",
                    [dict(row)[c] for c in cols[1:]],
                )
        con.execute(f"DROP TABLE {table}")
        con.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
        con.execute(f"CREATE INDEX IF NOT EXISTS idx_snapshots_event ON {table}(event_id)")
    con.execute("PRAGMA foreign_keys=ON")


def init_db() -> None:
    """
    Initialise the database.  Safe to call on every startup.
    Migration path: v0/v1 → v2 → v3 → v4 → v5 (current).
    """
    import log as _log
    BETTING_DIR.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        _repair_stale_fks(con)
        con.executescript(_DDL_SCHEMA_VER)
        current_ver = _get_schema_version(con)

        events_exists = bool(con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        ).fetchone())

        if not events_exists:
            # Fresh install
            con.executescript(
                _DDL_EVENTS + _DDL_BETS + _DDL_SNAPSHOTS +
                _DDL_LOCKS + _DDL_AUDIT +
                _DDL_LEDGER_ENTRIES + _DDL_OPERATION_METRICS +
                _DDL_INDEXES
            )
            _set_schema_version(con, _SCHEMA_VERSION)

        elif current_ver < 2:
            _log.migration(current_ver, 5, "v0/v1 → v5")
            _migrate_v1_to_v2(con)
            con.executescript(_DDL_SNAPSHOTS + _DDL_LOCKS + _DDL_AUDIT)
            _migrate_v2_to_v3(con)
            _migrate_v3_to_v4(con)
            _migrate_v4_to_v5(con)
            _rebuild_indexes(con)
            _set_schema_version(con, _SCHEMA_VERSION)

        elif current_ver < 3:
            _log.migration(current_ver, 5, "v2 → v5")
            _migrate_v2_to_v3(con)
            con.executescript(_DDL_LOCKS + _DDL_AUDIT)
            _migrate_v3_to_v4(con)
            _migrate_v4_to_v5(con)
            _rebuild_indexes(con)
            _set_schema_version(con, _SCHEMA_VERSION)

        elif current_ver < 4:
            _log.migration(current_ver, 5, "v3 → v5")
            _migrate_v3_to_v4(con)
            _migrate_v4_to_v5(con)
            _rebuild_indexes(con)
            _set_schema_version(con, _SCHEMA_VERSION)

        elif current_ver < 5:
            _migrate_v4_to_v5(con)
            _rebuild_indexes(con)
            _set_schema_version(con, _SCHEMA_VERSION)

        else:
            # Already at v5 — ensure all supplementary tables/indexes exist
            con.executescript(
                _DDL_SNAPSHOTS + _DDL_LOCKS + _DDL_AUDIT +
                _DDL_LEDGER_ENTRIES + _DDL_OPERATION_METRICS +
                _DDL_INDEXES
            )


# ── observability ─────────────────────────────────────────────────────────────

@contextmanager
def _timed_operation(operation: str, event_id: str | None = None,
                     detail: str | None = None):
    """Record wall-clock duration of a named operation to operation_metrics."""
    started  = _utcnow()
    t0       = time.monotonic()
    error_msg: str | None = None
    try:
        yield
    except Exception as exc:
        error_msg = str(exc)
        raise
    finally:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        finished = _utcnow()
        try:
            with _conn() as con:
                con.execute(
                    """INSERT INTO operation_metrics
                       (operation, event_id, started_at, finished_at,
                        duration_ms, success, error_msg, detail)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (operation, event_id, started, finished, elapsed_ms,
                     0 if error_msg else 1, error_msg, detail),
                )
        except Exception:
            pass


def get_operation_metrics(operation: str | None = None, limit: int = 100) -> list[dict]:
    """Return recent operation timing records."""
    init_db()
    with _conn() as con:
        if operation:
            rows = con.execute(
                "SELECT * FROM operation_metrics WHERE operation=? ORDER BY started_at DESC LIMIT ?",
                (operation, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM operation_metrics ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── audit log ─────────────────────────────────────────────────────────────────

def _audit(con, action: str, entity_type: str, entity_id: str,
           old_status: str | None = None, new_status: str | None = None,
           detail: str | None = None) -> None:
    con.execute(
        """INSERT INTO audit_log
           (action, entity_type, entity_id, old_status, new_status, detail, performed_at)
           VALUES (?,?,?,?,?,?,?)""",
        (action, entity_type, entity_id, old_status, new_status, detail, _utcnow()),
    )


def get_audit_log(entity_id: str | None = None, limit: int = 200) -> list[dict]:
    init_db()
    with _conn() as con:
        if entity_id:
            rows = con.execute(
                "SELECT * FROM audit_log WHERE entity_id=? ORDER BY performed_at DESC LIMIT ?",
                (entity_id, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM audit_log ORDER BY performed_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# ── financial ledger (append-only, chain-hashed) ──────────────────────────────

def _ledger_entry(
    con,
    entry_type: str,
    amount: Decimal,
    balance_after: Decimal,
    bet_id: str | None = None,
    event_id: str | None = None,
    description: str | None = None,
) -> None:
    """
    Append one chain-hashed row to ledger_entries.
    Called inside an open connection/transaction.
    """
    now = _utcnow()
    # Fetch previous entry hash for chain
    prev_row = con.execute(
        "SELECT entry_hash FROM ledger_entries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    prev_hash_stored = prev_row["entry_hash"] if prev_row else None
    prev_h, entry_h = _hash_ledger_entry(
        prev_hash_stored, entry_type, bet_id, event_id, amount, balance_after, now
    )
    con.execute(
        """INSERT INTO ledger_entries
           (entry_type, bet_id, event_id, amount, balance_after,
            prev_hash, entry_hash, description, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (entry_type, bet_id, event_id, _f(amount), _f(balance_after),
         prev_h, entry_h, description, now),
    )
    _audit(con, "ledger_entry", "ledger", bet_id or event_id or "manual",
           detail=f"{entry_type} amount={amount:+} balance={balance_after}")


def get_ledger_entries(event_id: str | None = None, limit: int = 500) -> list[dict]:
    """Return ledger entries, optionally filtered by event."""
    init_db()
    with _conn() as con:
        if event_id:
            rows = con.execute(
                "SELECT * FROM ledger_entries WHERE event_id=? ORDER BY created_at ASC LIMIT ?",
                (event_id, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM ledger_entries ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def _ledger_balance(con) -> Decimal:
    """Running balance from ledger_entries. Returns Decimal."""
    row = con.execute(
        "SELECT balance_after FROM ledger_entries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _d(row["balance_after"]) if row else _BASE_BANKROLL


# ── pipeline locking ──────────────────────────────────────────────────────────

def acquire_lock(lock_name: str, ttl_seconds: int = 300) -> bool:
    """Attempt to acquire a named pipeline lock. Returns True if acquired."""
    init_db()
    import log as _log
    now    = _utcnow()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    holder = _process_id()
    with _conn() as con:
        con.execute("DELETE FROM pipeline_locks WHERE expires_at < ?", (now,))
        try:
            con.execute(
                "INSERT INTO pipeline_locks (lock_name, locked_by, locked_at, expires_at) "
                "VALUES (?,?,?,?)",
                (lock_name, holder, now, expires),
            )
            _audit(con, "lock_acquired", "lock", lock_name, detail=holder)
            return True
        except sqlite3.IntegrityError:
            current = con.execute(
                "SELECT locked_by FROM pipeline_locks WHERE lock_name=?", (lock_name,)
            ).fetchone()
            _log.lock_contention(lock_name,
                                 holder=current["locked_by"] if current else "unknown")
            return False


def release_lock(lock_name: str) -> None:
    """Release a named pipeline lock."""
    init_db()
    with _conn() as con:
        con.execute("DELETE FROM pipeline_locks WHERE lock_name=?", (lock_name,))
        _audit(con, "lock_released", "lock", lock_name)


@contextmanager
def pipeline_lock(lock_name: str, ttl_seconds: int = 300):
    """Acquire/release a named lock as a context manager."""
    acquired = acquire_lock(lock_name, ttl_seconds)
    if not acquired:
        raise RuntimeError(
            f"\n  🔒  PIPELINE LOCK HELD: '{lock_name}'\n"
            f"  Another process is running. The lock expires in ≤{ttl_seconds}s.\n"
            f"  If this is stale, call integrity.recover_stale_locks().\n"
        )
    try:
        yield
    finally:
        release_lock(lock_name)


def get_active_locks() -> list[dict]:
    init_db()
    now = _utcnow()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM pipeline_locks WHERE expires_at > ? ORDER BY locked_at", (now,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── event lifecycle ───────────────────────────────────────────────────────────

def register_event(
    event_name: str,
    event_date: str,
    event_url: str,
    card_json: dict | None = None,
    edges_json: list | None = None,
) -> str:
    """
    Idempotently register an event. Returns event_id.
    Sets canonical_id on first registration; preserves it on updates.
    """
    init_db()
    eid   = event_id_from_url(event_url)
    cid   = make_canonical_id(event_name, event_date)
    now   = _utcnow()
    card_s  = json.dumps(card_json)  if card_json  is not None else None
    edges_s = json.dumps(edges_json) if edges_json is not None else None

    with _conn() as con:
        existing = con.execute(
            "SELECT event_id, status FROM events WHERE event_id=?", (eid,)
        ).fetchone()
        if existing:
            con.execute(
                """UPDATE events
                   SET card_json=?, edges_json=?, event_name=?, event_date=?,
                       canonical_id=COALESCE(canonical_id, ?), updated_at=?
                   WHERE event_id=?""",
                (card_s, edges_s, event_name, event_date, cid, now, eid),
            )
        else:
            con.execute(
                """INSERT INTO events
                   (event_id, canonical_id, event_name, event_date, event_url, status,
                    card_json, edges_json, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (eid, cid, event_name, event_date, event_url,
                 "scheduled", card_s, edges_s, now, now),
            )
            _audit(con, "event_registered", "event", eid,
                   new_status="scheduled", detail=f"{event_name} [{cid}]")
    return eid


def advance_event_state(event_id: str, new_status: str) -> dict:
    """
    Advance an event to its next state.  Forward-only state machine.
    Archived events are physically immutable — raises ValueError.
    """
    init_db()
    _FORWARD = {
        "scheduled":    "betting_open",
        "betting_open": "locked",
        "locked":       "completed",
        "completed":    "settled",
        "settled":      "archived",
        "archived":     None,
    }
    now = _utcnow()
    with _conn() as con:
        event = con.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            raise ValueError(f"Event '{event_id}' not found")
        event   = dict(event)
        current = event["status"]

        if current == "archived":
            raise ValueError("Archived events are read-only and cannot be advanced.")
        if current == "settled" and new_status != "archived":
            raise ValueError("Settled events can only move to 'archived'.")

        allowed_next = _FORWARD.get(current)
        if new_status != allowed_next:
            raise ValueError(
                f"Illegal transition: {current} → {new_status}. "
                f"Expected next state: {allowed_next}"
            )
        con.execute(
            "UPDATE events SET status=?, updated_at=? WHERE event_id=?",
            (new_status, now, event_id),
        )
        _audit(con, "state_advanced", "event", event_id,
               old_status=current, new_status=new_status)
    return get_event(event_id)


def archive_event(event_id: str) -> None:
    """Move a settled event to 'archived'."""
    init_db()
    now = _utcnow()
    with _conn() as con:
        event = con.execute("SELECT status FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            raise ValueError(f"Event '{event_id}' not found")
        if event["status"] != "settled":
            raise ValueError(
                f"Only 'settled' events can be archived (current: {event['status']})"
            )
        con.execute(
            "UPDATE events SET status='archived', updated_at=? WHERE event_id=?",
            (now, event_id),
        )
        _audit(con, "event_archived", "event", event_id,
               old_status="settled", new_status="archived")


def get_event(event_id: str) -> dict | None:
    init_db()
    with _conn() as con:
        row = con.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return dict(row) if row else None


def get_all_events() -> list[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def get_active_event() -> dict | None:
    """Return the most recent event that needs attention (blocking states)."""
    init_db()
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM events
               WHERE status IN ('betting_open','locked','completed')
               ORDER BY created_at DESC LIMIT 1"""
        ).fetchone()
        return dict(row) if row else None


def get_event_summary(event_id: str) -> dict:
    """Full event record with all bets, stats, and latest bankroll snapshot."""
    init_db()
    with _conn() as con:
        event = con.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            return {}
        event = dict(event)
        bets = [dict(b) for b in con.execute(
            "SELECT * FROM bets WHERE event_id=? ORDER BY bet_type, placed_at", (event_id,)
        ).fetchall()]
        snapshot = con.execute(
            "SELECT * FROM bankroll_snapshots WHERE event_id=? AND snapshot_type='post_event'",
            (event_id,),
        ).fetchone()

    total_staked = _f(sum(_d(b["stake_eur"]) for b in bets))
    settled      = [b for b in bets if b["status"] != "pending"]
    total_pnl    = _f(sum(_d(b["pnl"] or 0) for b in settled))

    return {
        **event,
        "bets":          bets,
        "total_staked":  total_staked,
        "total_pnl":     total_pnl,
        "won":           sum(1 for b in bets if b["status"] == "won"),
        "lost":          sum(1 for b in bets if b["status"] == "lost"),
        "push":          sum(1 for b in bets if b["status"] == "push"),
        "void":          sum(1 for b in bets if b["status"] == "void"),
        "pending_count": sum(1 for b in bets if b["status"] == "pending"),
        "snapshot":      dict(snapshot) if snapshot else None,
    }


# ── bet lifecycle ─────────────────────────────────────────────────────────────

def place_bets(
    event_id: str,
    singles: list[dict],
    accumulators: list[dict],
    bankroll: float,
) -> list[str]:
    """
    Idempotently record bets as 'pending'.
    INSERT OR IGNORE on deterministic bet_id — re-running is safe.
    Advances event to 'betting_open' if still in 'scheduled'.
    Writes bet_placed entries to ledger_entries for new bets only.
    Deducts stakes from ledger balance using Decimal arithmetic.
    """
    init_db()
    now = _utcnow()
    bet_ids: list[str] = []
    from normalize import norm_market, norm_selection, norm_sportsbook

    with _timed_operation("place_bets", event_id=event_id):
        with _conn() as con:
            event = con.execute(
                "SELECT status FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if not event:
                raise ValueError(f"Event '{event_id}' not found in ledger")
            if event["status"] not in ("scheduled", "betting_open"):
                raise ValueError(
                    f"Cannot place bets on event in '{event['status']}' state."
                )

            if event["status"] == "scheduled":
                con.execute(
                    "UPDATE events SET status='betting_open', updated_at=? WHERE event_id=?",
                    (now, event_id),
                )
                _audit(con, "state_advanced", "event", event_id,
                       old_status="scheduled", new_status="betting_open")

            existing_pre = con.execute(
                "SELECT id FROM bankroll_snapshots WHERE event_id=? AND snapshot_type='pre_event'",
                (event_id,),
            ).fetchone()
            if not existing_pre:
                br = _d(bankroll)
                con.execute(
                    "INSERT INTO bankroll_snapshots "
                    "(event_id, snapshot_type, bankroll_before, bankroll_after, snapshot_at) "
                    "VALUES (?,?,?,?,?)",
                    (event_id, "pre_event", None, _f(br), now),
                )
                _audit(con, "snapshot_pre", "bankroll", event_id,
                       detail=f"pre-event bankroll: {br}")

            running: Decimal = _ledger_balance(con)

            for pick in singles:
                market_raw = pick.get("market", "")
                sel_raw    = pick.get("selection", "")
                sb_raw     = pick.get("sportsbook") or ""
                bid = _bet_id(event_id, pick.get("fight_id"), market_raw, sel_raw)
                stake_d    = _d(pick.get("stake") and pick["stake"].get("eur") or
                                pick.get("stake_eur") or 0)
                odds_d     = _d(pick.get("decimal_odds") or pick.get("odds") or 0)
                ret_d      = _d(pick.get("potential_return") or stake_d * odds_d)

                edge_val = pick.get("edge")
                conf_val = pick.get("confidence")
                inserted = con.execute(
                    """INSERT OR IGNORE INTO bets
                       (bet_id, event_id, bet_type, fight_id, fight,
                        market, market_norm, selection, selection_norm,
                        sportsbook, sportsbook_norm,
                        odds, stake_eur, potential_return,
                        edge, confidence,
                        bankroll_before, placed_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, event_id, "single",
                     pick.get("fight_id"), pick.get("fight", ""),
                     market_raw, norm_market(market_raw),
                     sel_raw, norm_selection(sel_raw),
                     sb_raw or None, norm_sportsbook(sb_raw) or None,
                     _f(odds_d), _f(stake_d), _f(ret_d),
                     _f(_d(edge_val)) if edge_val is not None else None, conf_val,
                     _f(_d(bankroll)), now, now),
                ).rowcount

                if inserted:
                    running = running - stake_d
                    _ledger_entry(
                        con, "bet_placed", -stake_d, running,
                        bet_id=bid, event_id=event_id,
                        description=f"{pick.get('fight', '')} / {sel_raw}",
                    )
                bet_ids.append(bid)

            _ACCA_TYPE = {2: "double", 3: "treble", 4: "4fold"}
            for i, acca in enumerate(accumulators):
                legs       = acca.get("legs", [])
                bet_type   = _ACCA_TYPE.get(len(legs), acca.get("type", "double").lower())
                legs_label = " + ".join(
                    leg.get("label") or leg.get("selection", "") for leg in legs
                )
                fight_ids  = ",".join(str(leg.get("fight_id", "")) for leg in legs)
                bid        = _bet_id(event_id, fight_ids, "accumulator", legs_label, i)
                stake_d    = _d(acca.get("stake") or 0)
                odds_d     = _d(acca.get("combined_odds") or 0)
                ret_d      = _d(acca.get("potential_return") or stake_d * odds_d)

                legs_json = json.dumps([
                    {"label": leg.get("label") or leg.get("selection", ""),
                     "fight": leg.get("fight", ""),
                     "fight_id": leg.get("fight_id"),
                     "odds": leg.get("odds")}
                    for leg in legs
                ])
                inserted = con.execute(
                    """INSERT OR IGNORE INTO bets
                       (bet_id, event_id, bet_type, fight_id, fight,
                        market, market_norm, selection, selection_norm,
                        sportsbook, sportsbook_norm,
                        odds, stake_eur, potential_return,
                        acca_legs_json,
                        bankroll_before, placed_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bid, event_id, bet_type,
                     fight_ids, legs_label,
                     "Accumulator", "accumulator",
                     legs_label, norm_selection(legs_label),
                     None, None,
                     _f(odds_d), _f(stake_d), _f(ret_d),
                     legs_json,
                     _f(_d(bankroll)), now, now),
                ).rowcount

                if inserted:
                    running = running - stake_d
                    _ledger_entry(
                        con, "bet_placed", -stake_d, running,
                        bet_id=bid, event_id=event_id,
                        description=f"acca: {legs_label}",
                    )
                bet_ids.append(bid)

    return bet_ids


def get_bets_for_event(event_id: str) -> list[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM bets WHERE event_id=? ORDER BY bet_type, fight", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_event_bets(event_id: str) -> list[dict]:
    """
    Return all bets for an event, formatted for UI consumption.
    Stakes, odds, and status come from the ledger — never from JSON files.
    Accumulators include a parsed `legs` list and display `type` string.
    """
    init_db()
    _TYPE_LABEL = {"double": "Double", "treble": "Treble", "4fold": "4-Fold", "single": "Single"}
    with _conn() as con:
        rows = con.execute(
            """SELECT bet_id, bet_type, fight, fight_id, market, selection,
                      sportsbook, odds, stake_eur, potential_return,
                      edge, confidence, acca_legs_json,
                      status, pnl, placed_at, settled_at
               FROM bets WHERE event_id=? ORDER BY id ASC""",
            (event_id,),
        ).fetchall()
        result = []
        for r in rows:
            bet = dict(r)
            bet["type"] = _TYPE_LABEL.get(bet.get("bet_type", ""), bet.get("bet_type", ""))
            bet["combined_odds"] = bet.get("odds")
            if bet.get("acca_legs_json"):
                try:
                    bet["legs"] = json.loads(bet["acca_legs_json"])
                except (ValueError, TypeError):
                    bet["legs"] = []
            else:
                bet["legs"] = []
            result.append(bet)
        return result


# ── settlement preview ────────────────────────────────────────────────────────

def preview_settlement(event_id: str, results: dict[str, str]) -> dict:
    """
    Dry-run settlement — computes P&L with Decimal arithmetic, read-only.
    Returns projected bankroll and per-bet breakdown.
    """
    init_db()
    _VALID = {"won", "lost", "push", "void"}
    bad = {r for r in results.values() if r not in _VALID}
    if bad:
        raise ValueError(f"Invalid result values: {bad!r}. Must be one of {_VALID}")

    preview: dict[str, Any] = {
        "event_id":        event_id,
        "valid":           True,
        "errors":          [],
        "warnings":        [],
        "bets":            [],
        "total_staked":    0.0,
        "total_pnl":       0.0,
        "bankroll_before": None,
        "bankroll_after":  None,
        "bets_won":        0,
        "bets_lost":       0,
        "bets_push":       0,
    }

    with _conn() as con:
        event = con.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        if not event:
            preview["valid"] = False
            preview["errors"].append(f"Event '{event_id}' not found in ledger")
            return preview
        event = dict(event)
        if event["status"] in ("settled", "archived"):
            preview["valid"] = False
            preview["errors"].append(
                f"Event is already '{event['status']}' — cannot settle again."
            )
            return preview

        last = con.execute(
            """SELECT bankroll_after FROM bets
               WHERE status != 'pending' AND bankroll_after IS NOT NULL
               ORDER BY settled_at DESC LIMIT 1"""
        ).fetchone()
        running: Decimal = _d(last["bankroll_after"]) if last else _BASE_BANKROLL
        preview["bankroll_before"] = _f(running)

        total_staked = _d("0")
        total_pnl    = _d("0")

        for bet_id, result in results.items():
            row = con.execute("SELECT * FROM bets WHERE bet_id=?", (bet_id,)).fetchone()
            if not row:
                preview["warnings"].append(f"Bet {bet_id[:8]}… not found — skipped")
                continue
            bet = dict(row)
            if bet["status"] != "pending":
                preview["warnings"].append(
                    f"{bet['fight']} / {bet['selection']}: already '{bet['status']}' — will skip"
                )
                continue
            if bet["event_id"] != event_id:
                preview["errors"].append(f"Bet {bet_id[:8]}… belongs to a different event")
                preview["valid"] = False
                continue

            stake  = _d(bet["stake_eur"])
            ret    = _d(bet["potential_return"])
            b_before = running

            if result == "won":
                pnl = ret - stake
                preview["bets_won"] += 1
            elif result == "lost":
                pnl = -stake
                preview["bets_lost"] += 1
            else:
                pnl = _d("0")
                preview["bets_push"] += 1

            b_after  = b_before + stake + pnl
            running  = b_after
            total_staked = total_staked + stake
            total_pnl    = total_pnl    + pnl

            preview["bets"].append({
                "bet_id":           bet_id,
                "fight":            bet["fight"],
                "market":           bet["market"],
                "selection":        bet["selection"],
                "odds":             bet["odds"],
                "stake_eur":        _f(stake),
                "potential_return": _f(ret),
                "result":           result,
                "pnl":              _f(pnl),
                "bankroll_before":  _f(b_before),
                "bankroll_after":   _f(b_after),
            })

    preview["total_staked"]   = _f(total_staked)
    preview["total_pnl"]      = _f(total_pnl)
    preview["bankroll_after"] = _f(running)
    return preview


# ── settlement (commit) ───────────────────────────────────────────────────────

def settle_event(
    event_id: str,
    results: dict[str, str],
    settled_by: str = "system",
    result_source_detail: str | None = None,
    result_confidence: str = "confirmed",
) -> dict:
    """
    Settle bets for an event in a single ACID transaction.
    Uses Decimal arithmetic for all P&L calculations.
    Writes chain-hashed ledger entries for each resolved bet.
    Records provenance: who settled, source, confidence.

    Parameters
    ----------
    event_id : str
    results  : {bet_id: 'won'|'lost'|'push'|'void', ...}
    settled_by : str
        Provenance — who initiated settlement ('system', 'web_ui', 'api', ...).
    result_source_detail : str, optional
        Free-text source, e.g. "ESPN.com results page".
    result_confidence : str
        'confirmed' | 'provisional' | 'corrected'
    """
    import log as _log
    init_db()
    _VALID = {"won", "lost", "push", "void"}
    bad = {r for r in results.values() if r not in _VALID}
    if bad:
        raise ValueError(f"Invalid result values: {bad!r}. Must be one of {_VALID}")

    _CONF_VALID = {"confirmed", "provisional", "corrected"}
    if result_confidence not in _CONF_VALID:
        raise ValueError(f"result_confidence must be one of {_CONF_VALID}")

    with _timed_operation("settle_event", event_id=event_id):
        with pipeline_lock(f"settle_{event_id}", ttl_seconds=120):
            summary: dict[str, Any] = {
                "event_id":        event_id,
                "settled":         [],
                "skipped":         [],
                "total_pnl":       0.0,
                "bankroll_before": None,
                "bankroll_after":  None,
                "settled_by":      settled_by,
            }

            with _conn() as con:
                event = con.execute(
                    "SELECT * FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if not event:
                    raise ValueError(f"Event '{event_id}' not found in ledger")
                event = dict(event)

                if event["status"] in ("settled", "archived"):
                    raise ValueError(
                        f"Event is already '{event['status']}' and cannot be settled again."
                    )

                now = _utcnow()
                # Start running balance from the most recent settled bet's bankroll_after,
                # OR from the post_event snapshot, OR fall back to base.
                # DO NOT use b_before + stake + pnl — that double-counts stakes.
                # Instead use: b_after = b_before + pnl  (stake was already committed).
                last = con.execute(
                    """SELECT bankroll_after FROM bankroll_snapshots
                       WHERE snapshot_type='post_event'
                       ORDER BY snapshot_at DESC LIMIT 1"""
                ).fetchone()
                if not last:
                    last = con.execute(
                        """SELECT bankroll_after FROM bets
                           WHERE status != 'pending' AND bankroll_after IS NOT NULL
                           ORDER BY settled_at DESC LIMIT 1"""
                    ).fetchone()
                running: Decimal = _d(last["bankroll_after"]) if last else _BASE_BANKROLL
                summary["bankroll_before"] = _f(running)
                ledger_running: Decimal    = _ledger_balance(con)
                total_pnl: Decimal         = _d("0")

                for bet_id, result in results.items():
                    row = con.execute(
                        "SELECT * FROM bets WHERE bet_id=?", (bet_id,)
                    ).fetchone()
                    if not row:
                        summary["skipped"].append({"bet_id": bet_id, "reason": "not found"})
                        continue
                    bet = dict(row)
                    if bet["status"] != "pending":
                        summary["skipped"].append(
                            {"bet_id": bet_id, "reason": f"already {bet['status']}"}
                        )
                        continue

                    stake  = _d(bet["stake_eur"])
                    ret    = _d(bet["potential_return"])
                    b_before = running

                    if result == "won":
                        pnl          = ret - stake
                        entry_type   = "bet_won"
                        entry_amount = ret
                    elif result == "lost":
                        pnl          = -stake
                        entry_type   = "bet_lost"
                        entry_amount = _d("0")
                    elif result == "push":
                        pnl          = _d("0")
                        entry_type   = "bet_push"
                        entry_amount = stake
                    else:  # void
                        pnl          = _d("0")
                        entry_type   = "bet_void"
                        entry_amount = stake

                    # b_before is the bankroll with stakes already committed.
                    # Won: get back profit (return - stake). Lost: bankroll decreases by stake.
                    b_after        = b_before + pnl
                    running        = b_after
                    ledger_running = ledger_running + entry_amount
                    total_pnl      = total_pnl + pnl

                    con.execute(
                        """UPDATE bets
                           SET status=?, pnl=?, bankroll_before=?, bankroll_after=?,
                               settled_at=?, updated_at=?,
                               settled_by=?, result_source_detail=?, result_confidence=?
                           WHERE bet_id=?""",
                        (result, _f(pnl), _f(b_before), _f(b_after), now, now,
                         settled_by, result_source_detail, result_confidence, bet_id),
                    )
                    _audit(con, "bet_settled", "bet", bet_id,
                           old_status="pending", new_status=result,
                           detail=(
                               f"pnl={pnl:+} bankroll_after={b_after} "
                               f"settled_by={settled_by} confidence={result_confidence}"
                           ))
                    _ledger_entry(
                        con, entry_type, entry_amount, ledger_running,
                        bet_id=bet_id, event_id=event_id,
                        description=(
                            f"{bet['fight']} / {bet['selection']} @ {bet['odds']}"
                            + (f" [{settled_by}]" if settled_by != "system" else "")
                        ),
                    )
                    summary["settled"].append(bet_id)

                summary["total_pnl"]      = _f(total_pnl)
                summary["bankroll_after"] = _f(running)

                # Counts for snapshot
                bets_settled = con.execute(
                    "SELECT status, COUNT(*) as n FROM bets WHERE event_id=? AND status!='pending' GROUP BY status",
                    (event_id,),
                ).fetchall()
                counts = {r["status"]: r["n"] for r in bets_settled}

                total_staked_d = sum(
                    _d(r["stake_eur"]) for r in con.execute(
                        "SELECT stake_eur FROM bets WHERE event_id=?", (event_id,)
                    ).fetchall()
                )

                con.execute(
                    """INSERT OR REPLACE INTO bankroll_snapshots
                       (event_id, snapshot_type, bankroll_before, bankroll_after,
                        event_pnl, total_staked, bets_won, bets_lost, bets_push, snapshot_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (event_id, "post_event",
                     _f(_d(summary["bankroll_before"])), _f(running),
                     _f(total_pnl), _f(total_staked_d),
                     counts.get("won", 0), counts.get("lost", 0), counts.get("push", 0),
                     now),
                )
                _audit(con, "snapshot_post", "bankroll", event_id,
                       detail=f"pnl={total_pnl:+} bankroll={running}")

                remaining = con.execute(
                    "SELECT COUNT(*) FROM bets WHERE event_id=? AND status='pending'",
                    (event_id,),
                ).fetchone()[0]
                if remaining == 0:
                    con.execute(
                        "UPDATE events SET status='settled', settled_at=?, updated_at=? WHERE event_id=?",
                        (now, now, event_id),
                    )
                    _audit(con, "state_advanced", "event", event_id,
                           old_status=event["status"], new_status="settled")

    _log.settlement(
        event_id, summary["settled"], summary["total_pnl"],
        summary["bankroll_after"], settled_by=settled_by,
    )
    export_bet_history_csv()
    return summary


# ── bankroll ──────────────────────────────────────────────────────────────────

def current_bankroll_from_db() -> float:
    """Live bankroll as float. Falls back to base if no settled bets exist."""
    init_db()
    with _conn() as con:
        snap = con.execute(
            """SELECT bankroll_after FROM bankroll_snapshots
               WHERE snapshot_type='post_event'
               ORDER BY snapshot_at DESC LIMIT 1"""
        ).fetchone()
        if snap:
            return _f(_d(snap["bankroll_after"]))
        row = con.execute(
            """SELECT bankroll_after FROM bets
               WHERE status != 'pending' AND bankroll_after IS NOT NULL
               ORDER BY settled_at DESC LIMIT 1"""
        ).fetchone()
        return _f(_d(row["bankroll_after"])) if row else _f(_BASE_BANKROLL)


def get_bankroll_history() -> list[dict]:
    """Return all post-event snapshots ordered by date — equity curve data."""
    init_db()
    with _conn() as con:
        rows = con.execute(
            """SELECT s.*, e.event_name, e.event_date
               FROM bankroll_snapshots s JOIN events e ON s.event_id = e.event_id
               WHERE s.snapshot_type = 'post_event'
               ORDER BY s.snapshot_at ASC"""
        ).fetchall()
    return [dict(r) for r in rows]


# ── generation guard ──────────────────────────────────────────────────────────

def blocks_new_generation(current_event_url: str) -> tuple[bool, dict | None]:
    """Returns (True, blocking_event) if a different event is in a blocking state."""
    init_db()
    current_eid = event_id_from_url(current_event_url)
    with _conn() as con:
        row = con.execute(
            """SELECT * FROM events
               WHERE status IN ('betting_open','locked','completed')
               AND event_id != ?
               ORDER BY created_at DESC LIMIT 1""",
            (current_eid,),
        ).fetchone()
    if row:
        return True, dict(row)
    return False, None


# backwards-compat alias used in betting_model.py
has_pending_bets_for_different_event = blocks_new_generation


# ── CSV export ────────────────────────────────────────────────────────────────

def export_bet_history_csv() -> None:
    """Rebuild bet_history.csv from all settled bets. Export-only; SQLite is authoritative."""
    init_db()
    _BET_HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        rows = con.execute(
            """SELECT b.*, e.event_name
               FROM bets b JOIN events e ON b.event_id = e.event_id
               WHERE b.status != 'pending'
               ORDER BY b.settled_at ASC"""
        ).fetchall()
    fieldnames = [
        "date", "event", "bet_type", "fight", "market", "selection",
        "sportsbook", "odds", "stake_eur", "result", "pnl",
        "bankroll_before", "bankroll_after", "strategy",
        "settled_by", "result_confidence", "notes",
    ]
    with _BET_HISTORY_CSV.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            b = dict(row)
            writer.writerow({
                "date":              (b.get("settled_at") or b.get("placed_at", ""))[:10],
                "event":             b.get("event_name", ""),
                "bet_type":          b["bet_type"],
                "fight":             b["fight"],
                "market":            b["market"],
                "selection":         b["selection"],
                "sportsbook":        b.get("sportsbook") or "",
                "odds":              b["odds"],
                "stake_eur":         b["stake_eur"],
                "result":            b["status"].capitalize(),
                "pnl":               b.get("pnl") or "",
                "bankroll_before":   b.get("bankroll_before") or "",
                "bankroll_after":    b.get("bankroll_after") or "",
                "strategy":          b.get("strategy", "model_v1"),
                "settled_by":        b.get("settled_by", "system"),
                "result_confidence": b.get("result_confidence", "confirmed"),
                "notes":             b.get("result_notes") or "",
            })
