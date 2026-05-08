"""
ledger.py — authoritative SQLite financial core for MMA bankroll management.

All money is Decimal internally. Float is never permitted in accounting operations.
SQLite stores money values as TEXT to preserve exact decimal representation.

Source of truth for:
  - current bankroll (derived from last ledger entry)
  - bet lifecycle: pending → won / lost / push / void
  - all bankroll mutations (append-only ledger_entries)
"""
from __future__ import annotations

import csv as _csv
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Iterator

from utils import DATA_PROC, get_logger

log = get_logger("ledger")

DB_PATH = DATA_PROC / "betting" / "ledger.db"
BASE_BANKROLL = Decimal("500.00")

# Entry type constants
ENTRY_BET_PLACED  = "bet_placed"
ENTRY_BET_WON     = "bet_won"
ENTRY_BET_LOST    = "bet_lost"
ENTRY_BET_PUSH    = "bet_push"
ENTRY_BET_VOID    = "bet_void"
ENTRY_ADJUSTMENT  = "adjustment"
ENTRY_MIGRATION   = "migration_import"

# Bet status constants
BET_PENDING = "pending"
BET_WON     = "won"
BET_LOST    = "lost"
BET_PUSH    = "push"
BET_VOID    = "void"

_SETTLEMENT_ENTRY = {
    BET_WON:  ENTRY_BET_WON,
    BET_LOST: ENTRY_BET_LOST,
    BET_PUSH: ENTRY_BET_PUSH,
    BET_VOID: ENTRY_BET_VOID,
}


# ── Decimal helpers ────────────────────────────────────────────────────────────

def d(value) -> Decimal:
    """Convert any numeric-ish value to Decimal. Raises on garbage input."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def money(value) -> Decimal:
    """Quantise to 2 decimal places using ROUND_HALF_UP."""
    return d(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ── Deterministic ID ───────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Normalise for stable hashing: lowercase, strip punctuation, collapse whitespace."""
    text = str(text or "").lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def make_bet_id(event_date: str, fight: str, market: str, selection: str, odds) -> str:
    """
    SHA-256 bet ID. Stable as long as these five fields are normalised identically.
    Uses canonical Decimal string for odds to avoid 1.85 vs 1.850 drift.
    """
    raw = "|".join([
        str(event_date).strip(),
        _norm(fight),
        _norm(market),
        _norm(selection),
        str(d(odds)),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Schema ─────────────────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    id          TEXT PRIMARY KEY,
    event_date  TEXT NOT NULL,
    fight       TEXT NOT NULL,
    market      TEXT NOT NULL,
    selection   TEXT NOT NULL,
    sportsbook  TEXT,
    odds        TEXT NOT NULL,
    stake_eur   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','won','lost','push','void')),
    placed_at   TEXT NOT NULL,
    settled_at  TEXT,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type      TEXT NOT NULL
                    CHECK (entry_type IN (
                        'bet_placed','bet_won','bet_lost','bet_push',
                        'bet_void','adjustment','migration_import'
                    )),
    bet_id          TEXT REFERENCES bets(id),
    amount          TEXT NOT NULL,
    bankroll_before TEXT NOT NULL,
    bankroll_after  TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_quarantine (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_row  TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_created_at ON ledger_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_bet_id     ON ledger_entries(bet_id);
CREATE INDEX IF NOT EXISTS idx_bets_status       ON bets(status);
CREATE INDEX IF NOT EXISTS idx_bets_event_date   ON bets(event_date);
"""


# ── Connection ─────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Must re-apply on each connection in SQLite
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _tx() -> Iterator[sqlite3.Connection]:
    """Open a connection, run a single explicit transaction, close on exit."""
    conn = _connect()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialise schema and stamp schema_version. Safe to call multiple times."""
    conn = _connect()
    try:
        # executescript() auto-commits; no transaction is open afterwards
        conn.executescript(_SCHEMA)
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if not row:
            conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, _utcnow()),
            )
            conn.execute("COMMIT")
    finally:
        conn.close()


def get_schema_version() -> int:
    """Return the current schema version recorded in the DB, or 0 if absent."""
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row["version"] if row else 0
    finally:
        conn.close()


# ── Bankroll query ─────────────────────────────────────────────────────────────

def current_bankroll() -> Decimal:
    """
    Derive current bankroll from the last ledger entry.
    Returns BASE_BANKROLL if the ledger is empty (fresh system).
    Never reads from CSV or any mutable source.
    """
    init_db()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT bankroll_after FROM ledger_entries ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return money(row["bankroll_after"]) if row else money(BASE_BANKROLL)
    finally:
        conn.close()


# ── Bet placement ──────────────────────────────────────────────────────────────

def place_bet(
    event_date: str,
    fight: str,
    market: str,
    selection: str,
    sportsbook: str,
    odds,
    stake_eur,
    notes: str = "",
) -> str:
    """
    Record a new bet placement. Deducts stake from bankroll atomically.
    Returns the deterministic bet_id.
    Raises ValueError if the bet already exists.
    """
    init_db()
    odds_d   = d(odds)
    stake_d  = money(stake_eur)
    bet_id   = make_bet_id(event_date, fight, market, selection, odds_d)
    now      = _utcnow()

    if stake_d <= Decimal("0"):
        raise ValueError(f"Stake must be positive, got: {stake_d}")

    with _tx() as conn:
        if conn.execute("SELECT id FROM bets WHERE id = ?", (bet_id,)).fetchone():
            raise ValueError(
                f"Bet already exists: {bet_id[:12]}… ({fight} / {market} / {selection})"
            )

        bankroll_before = current_bankroll()
        bankroll_after  = money(bankroll_before - stake_d)

        if bankroll_after < Decimal("0"):
            raise ValueError(
                f"Insufficient bankroll: EUR {bankroll_before} < stake EUR {stake_d}"
            )

        conn.execute(
            """INSERT INTO bets
               (id, event_date, fight, market, selection, sportsbook,
                odds, stake_eur, status, placed_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (bet_id, event_date, fight, market, selection, sportsbook,
             str(odds_d), str(stake_d), BET_PENDING, now, notes),
        )
        conn.execute(
            """INSERT INTO ledger_entries
               (entry_type, bet_id, amount, bankroll_before, bankroll_after, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ENTRY_BET_PLACED, bet_id, str(stake_d),
             str(bankroll_before), str(bankroll_after),
             f"Bet placed: {fight} — {selection} @ {odds_d}", now),
        )

    log.info("Placed [%s…] %s / %s @ %s — EUR %s", bet_id[:8], fight, selection, odds_d, stake_d)
    return bet_id


# ── Settlement ─────────────────────────────────────────────────────────────────

def settle_bet(bet_id: str, result: str, notes: str = "") -> Decimal:
    """
    Settle a pending bet. result must be 'won', 'lost', 'push', or 'void'.

    Accounting:
      won  — return stake + profit (stake * odds)
      lost — no ledger change (stake deducted at placement)
      push — return stake only
      void — return stake only

    Returns new bankroll after settlement.
    """
    init_db()
    result = result.lower().strip()
    if result not in _SETTLEMENT_ENTRY:
        raise ValueError(f"Invalid result {result!r}. Must be: won / lost / push / void")

    with _tx() as conn:
        row = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
        if not row:
            raise ValueError(f"Bet not found: {bet_id}")
        if row["status"] != BET_PENDING:
            raise ValueError(f"Bet {bet_id[:12]}… already settled as {row['status']!r}")

        stake           = money(row["stake_eur"])
        odds            = d(row["odds"])
        bankroll_before = current_bankroll()
        now             = _utcnow()

        if result == BET_WON:
            total_return   = money(stake * odds)
            bankroll_after = money(bankroll_before + total_return)
            amount         = total_return
            profit         = money(total_return - stake)
            description    = (
                f"Won: {row['fight']} — {row['selection']} @ {odds} "
                f"(profit EUR {profit})"
            )
        elif result == BET_LOST:
            bankroll_after = bankroll_before
            amount         = Decimal("0.00")
            description    = f"Lost: {row['fight']} — {row['selection']} @ {odds}"
        else:  # push or void
            bankroll_after = money(bankroll_before + stake)
            amount         = stake
            description    = (
                f"{'Push' if result == BET_PUSH else 'Void'}: "
                f"{row['fight']} — {row['selection']} (stake returned)"
            )

        conn.execute(
            """UPDATE bets
               SET status = ?, settled_at = ?,
                   notes = COALESCE(NULLIF(?, ''), notes)
               WHERE id = ?""",
            (result, now, notes, bet_id),
        )
        conn.execute(
            """INSERT INTO ledger_entries
               (entry_type, bet_id, amount, bankroll_before, bankroll_after, description, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_SETTLEMENT_ENTRY[result], bet_id, str(amount),
             str(bankroll_before), str(bankroll_after), description, now),
        )

    log.info(
        "Settled [%s…] result=%s  EUR %s → EUR %s",
        bet_id[:8], result, bankroll_before, bankroll_after,
    )
    return bankroll_after


# ── Migration ──────────────────────────────────────────────────────────────────

def _snapshot_csv(csv_path: Path) -> str:
    """
    Archive csv_path to <csv_path>.bak and write a <csv_path>.sha256 checksum file.
    Returns the hex digest. Called once before the first real migration.
    Skipped if a .bak already exists (migration already snapshotted).
    """
    import hashlib as _hl
    import shutil

    bak_path = csv_path.with_suffix(csv_path.suffix + ".bak")
    sha_path = csv_path.with_suffix(csv_path.suffix + ".sha256")

    if bak_path.exists():
        digest = sha_path.read_text(encoding="utf-8").strip() if sha_path.exists() else ""
        log.debug("CSV snapshot already exists: %s", bak_path.name)
        return digest

    raw = csv_path.read_bytes()
    digest = _hl.sha256(raw).hexdigest()
    shutil.copy2(csv_path, bak_path)
    sha_path.write_text(digest + "\n", encoding="utf-8")
    log.info("CSV snapshot saved: %s  sha256=%s…", bak_path.name, digest[:16])
    return digest


def migrate_from_csv(csv_path: Path) -> dict:
    """
    Import historical bet_history.csv into the ledger.

    Before processing, archives the CSV as <name>.bak and writes a .sha256
    checksum — giving permanent forensic provenance for the migration boundary.

    Idempotent: rows whose bet_id already exists are skipped, not duplicated.
    Invalid rows go to migration_quarantine with the reason preserved — they are
    never silently dropped. Callers can inspect get_quarantine_log() afterwards.

    Returns {'imported': N, 'skipped': N, 'quarantined': N, 'snapshot_sha256': str}.
    """
    init_db()

    if not csv_path.exists():
        log.info("No CSV to migrate: %s", csv_path)
        return {"imported": 0, "skipped": 0, "quarantined": 0, "snapshot_sha256": ""}

    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(_csv.DictReader(fh))

    if not rows:
        log.info("CSV exists but is empty — nothing to migrate.")
        return {"imported": 0, "skipped": 0, "quarantined": 0, "snapshot_sha256": ""}

    snapshot_digest = _snapshot_csv(csv_path)
    counts: dict = {"imported": 0, "skipped": 0, "quarantined": 0, "snapshot_sha256": snapshot_digest}
    now = _utcnow()

    # Track running bankroll locally so we don't need repeated queries mid-transaction
    running_bankroll = current_bankroll()

    with _tx() as conn:
        for raw in rows:
            reason = _validate_csv_row(raw)
            if reason:
                conn.execute(
                    "INSERT INTO migration_quarantine (source_row, reason, created_at) VALUES (?, ?, ?)",
                    (str(dict(raw)), reason, now),
                )
                log.warning("Quarantined: %s — %s", raw.get("fight", "?"), reason)
                counts["quarantined"] += 1
                continue

            event_date = raw["date"].strip()
            fight      = raw["fight"].strip()
            market     = raw["market"].strip()
            selection  = raw["selection"].strip()
            sportsbook = raw.get("sportsbook", "").strip()
            notes      = raw.get("notes", "").strip()

            try:
                odds_d  = d(raw["odds"])
                stake_d = money(raw["stake_eur"])
            except Exception as exc:
                conn.execute(
                    "INSERT INTO migration_quarantine (source_row, reason, created_at) VALUES (?, ?, ?)",
                    (str(dict(raw)), f"Decimal parse error: {exc}", now),
                )
                counts["quarantined"] += 1
                continue

            bet_id = make_bet_id(event_date, fight, market, selection, odds_d)

            if conn.execute("SELECT id FROM bets WHERE id = ?", (bet_id,)).fetchone():
                counts["skipped"] += 1
                continue

            result_raw  = raw.get("result", "").strip().lower()
            result_map  = {
                "win": BET_WON, "won": BET_WON,
                "loss": BET_LOST, "lost": BET_LOST,
                "push": BET_PUSH, "void": BET_VOID,
            }
            status     = result_map.get(result_raw, BET_PENDING)
            settled_at = now if status != BET_PENDING else None

            # Use CSV bankroll columns if present, otherwise derive from running state
            try:
                br_before = money(raw["bankroll_before"]) if raw.get("bankroll_before", "").strip() else None
                br_after  = money(raw["bankroll_after"])  if raw.get("bankroll_after", "").strip()  else None
            except Exception:
                br_before = br_after = None

            if br_before is None:
                br_before = running_bankroll
            if br_after is None:
                if status == BET_WON:
                    pnl_raw = raw.get("pnl", "").strip()
                    try:
                        pnl = money(pnl_raw) if pnl_raw else money(stake_d * (odds_d - Decimal("1")))
                    except Exception:
                        pnl = money(stake_d * (odds_d - Decimal("1")))
                    br_after = money(br_before + pnl)
                elif status in (BET_PUSH, BET_VOID):
                    br_after = br_before
                elif status == BET_LOST:
                    br_after = money(br_before - stake_d)
                else:  # pending
                    br_after = money(br_before - stake_d)

            conn.execute(
                """INSERT INTO bets
                   (id, event_date, fight, market, selection, sportsbook,
                    odds, stake_eur, status, placed_at, settled_at, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (bet_id, event_date, fight, market, selection, sportsbook,
                 str(odds_d), str(stake_d), status, now, settled_at, notes),
            )
            conn.execute(
                """INSERT INTO ledger_entries
                   (entry_type, bet_id, amount, bankroll_before, bankroll_after, description, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (ENTRY_MIGRATION, bet_id, str(stake_d),
                 str(br_before), str(br_after),
                 f"[migration] {fight} — {selection} @ {odds_d} [{status}]", now),
            )
            running_bankroll = br_after
            counts["imported"] += 1

    log.info(
        "Migration complete: imported=%d  skipped=%d  quarantined=%d",
        counts["imported"], counts["skipped"], counts["quarantined"],
    )
    return counts


def _validate_csv_row(row: dict) -> str | None:
    """Return error reason if row is invalid, else None."""
    for field in ("date", "fight", "market", "selection", "odds", "stake_eur"):
        if not str(row.get(field, "")).strip():
            return f"Missing required field: {field!r}"
    try:
        d(row["odds"])
    except Exception:
        return f"Invalid odds: {row['odds']!r}"
    try:
        money(row["stake_eur"])
    except Exception:
        return f"Invalid stake_eur: {row['stake_eur']!r}"
    return None


# ── Query helpers ──────────────────────────────────────────────────────────────

def get_pending_bets() -> list[dict]:
    """Return all bets in 'pending' status, newest first."""
    init_db()
    conn = _connect()
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM bets WHERE status = ? ORDER BY placed_at DESC",
                (BET_PENDING,),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_ledger_history(limit: int = 50) -> list[dict]:
    """Return the most recent ledger entries, newest first."""
    init_db()
    conn = _connect()
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM ledger_entries ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]
    finally:
        conn.close()


def get_quarantine_log() -> list[dict]:
    """Return all rows that failed migration, for forensic inspection."""
    init_db()
    conn = _connect()
    try:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM migration_quarantine ORDER BY created_at DESC"
            ).fetchall()
        ]
    finally:
        conn.close()


def verify_ledger() -> dict:
    """
    Run integrity checks on the ledger and return a health report.

    Checks performed:
      no_negative_bankroll  — no ledger entry has bankroll_after < 0
      ledger_continuity     — each entry's bankroll_after equals the next entry's
                              bankroll_before (using id-ordered sequence)
      valid_bet_statuses    — all bets have a recognised status value
      no_orphan_entries     — every ledger entry with a bet_id references an existing bet

    Returns:
      ok (bool)         — True only if all checks pass
      checks (dict)     — per-check pass/fail
      errors (list)     — human-readable descriptions of failures
      stats (dict)      — counts and current bankroll
    """
    init_db()
    conn = _connect()
    errors: list[str] = []
    checks: dict[str, bool] = {}

    try:
        # 1. No negative bankroll_after
        neg = conn.execute(
            "SELECT COUNT(*) FROM ledger_entries WHERE CAST(bankroll_after AS REAL) < 0"
        ).fetchone()[0]
        checks["no_negative_bankroll"] = neg == 0
        if neg:
            errors.append(f"{neg} ledger entries with bankroll_after < 0")

        # 2. Continuity: bankroll_after[n] must equal bankroll_before[n+1]
        rows = conn.execute(
            "SELECT id, bankroll_before, bankroll_after "
            "FROM ledger_entries ORDER BY created_at ASC, id ASC"
        ).fetchall()
        breaks = sum(
            1 for i in range(len(rows) - 1)
            if rows[i]["bankroll_after"] != rows[i + 1]["bankroll_before"]
        )
        checks["ledger_continuity"] = breaks == 0
        if breaks:
            errors.append(f"{breaks} bankroll continuity break(s) in ledger sequence")

        # 3. Valid bet statuses
        bad = conn.execute(
            "SELECT COUNT(*) FROM bets "
            "WHERE status NOT IN ('pending','won','lost','push','void')"
        ).fetchone()[0]
        checks["valid_bet_statuses"] = bad == 0
        if bad:
            errors.append(f"{bad} bet(s) with unrecognised status")

        # 4. No orphan ledger entries
        orphans = conn.execute(
            "SELECT COUNT(*) FROM ledger_entries le "
            "LEFT JOIN bets b ON le.bet_id = b.id "
            "WHERE le.bet_id IS NOT NULL AND b.id IS NULL"
        ).fetchone()[0]
        checks["no_orphan_entries"] = orphans == 0
        if orphans:
            errors.append(f"{orphans} ledger entries referencing non-existent bets")

        return {
            "ok":     len(errors) == 0,
            "checks": checks,
            "errors": errors,
            "stats": {
                "schema_version":  get_schema_version(),
                "ledger_entries":  conn.execute("SELECT COUNT(*) FROM ledger_entries").fetchone()[0],
                "total_bets":      conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0],
                "pending_bets":    conn.execute("SELECT COUNT(*) FROM bets WHERE status='pending'").fetchone()[0],
                "quarantined_rows": conn.execute("SELECT COUNT(*) FROM migration_quarantine").fetchone()[0],
                "current_bankroll": str(current_bankroll()),
            },
        }
    finally:
        conn.close()


# ── Internal ───────────────────────────────────────────────────────────────────

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
