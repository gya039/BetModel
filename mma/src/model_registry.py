"""
model_registry.py — Model versioning, audit logging, and rollback for UFC model.

Tracks every model training run with:
  - A unique version ID (timestamp + short hash)
  - Brier score, log loss, and calibration error at training time
  - The feature columns used
  - The model type and hyperparameters
  - Training / test date ranges

Automatic rollback:
  - After each event settles, recomputes Brier on recent fights
  - If Brier deteriorates by more than ROLLBACK_THRESHOLD from the
    registered baseline, the previous model version is restored

Audit log:
  - Every prediction (fight, fighter, probability, edge, confidence) is
    logged with the model version that produced it
  - Enables post-hoc analysis of which model version was responsible
    for any given bet

Usage:
    from model_registry import ModelRegistry
    reg = ModelRegistry()

    # Register a newly trained model
    reg.register(model, meta, metrics)

    # Load the current production model
    model, meta = reg.load_production()

    # Check if rollback is needed after event settlement
    reg.check_and_rollback(recent_brier=0.242)

    # Log a prediction
    reg.log_prediction(fight_id, fighter_a, model_prob, edge, confidence)

CLI:
    python model_registry.py --list          # show all versions
    python model_registry.py --rollback      # force rollback to previous version
    python model_registry.py --audit fight   # show audit log for a fight
"""
from __future__ import annotations

import hashlib
import json
import pickle
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import DATA_PROC

REGISTRY_DIR    = DATA_PROC.parent / "models" / "registry"
REGISTRY_INDEX  = REGISTRY_DIR / "index.json"
AUDIT_LOG       = DATA_PROC.parent / "models" / "prediction_audit.jsonl"
PRODUCTION_LINK = REGISTRY_DIR / "production.json"

# Rollback sensitivity: if recent Brier exceeds registered Brier by this much → rollback
ROLLBACK_THRESHOLD = 0.03   # 3pp above the trained baseline
# Minimum recent fights before triggering rollback check
MIN_FIGHTS_FOR_ROLLBACK = 10


# ── Version ID ────────────────────────────────────────────────────────────────

def _make_version_id(meta: dict) -> str:
    ts     = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    digest = hashlib.sha1(json.dumps(meta, sort_keys=True, default=str).encode()).hexdigest()[:6]
    return f"{ts}_{digest}"


# ── Registry ──────────────────────────────────────────────────────────────────

class ModelRegistry:
    """
    Manages model versioning, production promotion, rollback, and audit logging.
    """

    def __init__(self) -> None:
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        self._index: list[dict] = self._load_index()

    # ── Index management ─────────────────────────────────────────────────────

    def _load_index(self) -> list[dict]:
        if REGISTRY_INDEX.exists():
            try:
                return json.loads(REGISTRY_INDEX.read_text())
            except Exception:
                pass
        return []

    def _save_index(self) -> None:
        REGISTRY_INDEX.write_text(json.dumps(self._index, indent=2, default=str))

    # ── Register a new model ──────────────────────────────────────────────────

    def register(
        self,
        model:   Any,
        meta:    dict,
        metrics: dict,
        feature_cols: list[str] | None = None,
        promote: bool = True,
    ) -> str:
        """
        Save a trained model and register it.

        Args:
            model:        Fitted sklearn-compatible model
            meta:         Training metadata (model_type, train_from, train_until, ...)
            metrics:      Evaluation metrics (brier_score, log_loss, cal_error, ...)
            feature_cols: Feature column names used in training
            promote:      If True, immediately set as production model

        Returns:
            version_id string
        """
        version_id = _make_version_id({**meta, **metrics})

        version_dir = REGISTRY_DIR / version_id
        version_dir.mkdir(parents=True, exist_ok=True)

        # Save model binary
        model_path = version_dir / "model.pkl"
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

        # Save metadata
        record = {
            "version_id":   version_id,
            "registered_at": datetime.utcnow().isoformat(),
            "meta":          meta,
            "metrics":       metrics,
            "feature_cols":  feature_cols,
            "promoted":      promote,
            "model_path":    str(model_path),
            "status":        "production" if promote else "archived",
        }
        (version_dir / "meta.json").write_text(json.dumps(record, indent=2, default=str))

        # Update index
        if promote:
            for entry in self._index:
                if entry.get("status") == "production":
                    entry["status"] = "archived"

        self._index.append(record)
        self._save_index()

        if promote:
            PRODUCTION_LINK.write_text(json.dumps({"version_id": version_id}, indent=2))
            print(f"[ModelRegistry] Registered & promoted version {version_id}")
            b = metrics.get("brier_score")
            if b:
                print(f"  Brier={b}  LogLoss={metrics.get('log_loss')}  "
                      f"CalErr={metrics.get('cal_error')}")
        else:
            print(f"[ModelRegistry] Registered version {version_id} (not promoted)")

        return version_id

    # ── Load production model ─────────────────────────────────────────────────

    def load_production(self) -> tuple[Any, dict]:
        """
        Load the current production model.
        Returns (model, record_dict) or (None, {}) if none registered.
        """
        if not PRODUCTION_LINK.exists():
            return None, {}

        try:
            production = json.loads(PRODUCTION_LINK.read_text())
            version_id = production["version_id"]
            return self._load_version(version_id)
        except Exception:
            return None, {}

    def _load_version(self, version_id: str) -> tuple[Any, dict]:
        version_dir = REGISTRY_DIR / version_id
        model_path  = version_dir / "model.pkl"
        meta_path   = version_dir / "meta.json"

        if not model_path.exists():
            return None, {}

        try:
            with open(model_path, "rb") as f:
                model = pickle.load(f)
            record = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            return model, record
        except Exception:
            return None, {}

    # ── List versions ─────────────────────────────────────────────────────────

    def list_versions(self) -> list[dict]:
        """Return all registered versions, newest first."""
        return list(reversed(self._index))

    def print_versions(self) -> None:
        versions = self.list_versions()
        if not versions:
            print("[ModelRegistry] No versions registered.")
            return

        print(f"\n{'='*72}")
        print(f"  MODEL REGISTRY — {len(versions)} version(s)")
        print(f"{'='*72}")
        print(f"  {'Version':<22}  {'Type':<8}  {'Brier':>7}  {'LogLoss':>8}  Status")
        print(f"  {'-'*22}  {'-'*8}  {'-'*7}  {'-'*8}  {'-'*12}")

        for v in versions:
            brier = v.get("metrics", {}).get("brier_score", "—")
            ll    = v.get("metrics", {}).get("log_loss", "—")
            mtype = v.get("meta", {}).get("model_type", "—")
            status = v.get("status", "archived")
            marker = " ◄ PRODUCTION" if status == "production" else ""
            print(f"  {v['version_id']:<22}  {mtype:<8}  {str(brier):>7}  {str(ll):>8}  {status}{marker}")

        print(f"{'='*72}")

    # ── Rollback ──────────────────────────────────────────────────────────────

    def rollback(self, reason: str = "manual") -> str | None:
        """
        Roll back to the most recent non-production version.
        Returns the new production version_id, or None if nothing to roll back to.
        """
        production_id = None
        previous_id   = None

        for entry in reversed(self._index):
            if entry.get("status") == "production":
                production_id = entry["version_id"]
            elif production_id is not None:
                previous_id = entry["version_id"]
                break

        if previous_id is None:
            print("[ModelRegistry] No previous version to roll back to.")
            return None

        # Demote current production
        for entry in self._index:
            if entry.get("version_id") == production_id:
                entry["status"] = "rolled_back"
                entry["rollback_reason"] = reason
                entry["rolled_back_at"] = datetime.utcnow().isoformat()
            elif entry.get("version_id") == previous_id:
                entry["status"] = "production"

        self._save_index()
        PRODUCTION_LINK.write_text(json.dumps({"version_id": previous_id}, indent=2))

        print(f"[ModelRegistry] Rolled back from {production_id} → {previous_id}")
        print(f"  Reason: {reason}")
        return previous_id

    def check_and_rollback(
        self,
        recent_brier: float,
        n_fights: int = 0,
    ) -> bool:
        """
        Automatically roll back if recent Brier has deteriorated beyond threshold.
        Returns True if rollback was triggered.
        """
        if n_fights < MIN_FIGHTS_FOR_ROLLBACK:
            return False

        production = self._get_production_record()
        if not production:
            return False

        baseline_brier = production.get("metrics", {}).get("brier_score")
        if baseline_brier is None:
            return False

        deterioration = recent_brier - float(baseline_brier)
        if deterioration >= ROLLBACK_THRESHOLD:
            reason = (
                f"Auto-rollback: recent Brier={recent_brier:.4f} "
                f"exceeds baseline={baseline_brier:.4f} "
                f"by {deterioration:.4f} (threshold={ROLLBACK_THRESHOLD})"
            )
            self.rollback(reason=reason)
            self._log_rollback_event(recent_brier, baseline_brier, deterioration)
            return True

        return False

    def _get_production_record(self) -> dict | None:
        for entry in reversed(self._index):
            if entry.get("status") == "production":
                return entry
        return None

    def _log_rollback_event(
        self,
        recent_brier: float,
        baseline: float,
        delta: float,
    ) -> None:
        event = {
            "event":        "auto_rollback",
            "timestamp":    datetime.utcnow().isoformat(),
            "recent_brier": round(recent_brier, 4),
            "baseline":     round(baseline, 4),
            "delta":        round(delta, 4),
        }
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(event) + "\n")

    # ── Audit logging ─────────────────────────────────────────────────────────

    def log_prediction(
        self,
        fight_id:     str,
        fighter_a:    str,
        fighter_b:    str,
        model_prob:   float,
        edge:         float,
        confidence:   str,
        market_prob:  float | None = None,
        division:     str | None = None,
        components:   dict | None = None,
    ) -> None:
        """
        Append a prediction record to the audit log.
        Called once per matchup that passes the edge filter.
        """
        production = self._get_production_record()
        version_id = production.get("version_id") if production else "unknown"

        record = {
            "event":       "prediction",
            "timestamp":   datetime.utcnow().isoformat(),
            "version_id":  version_id,
            "fight_id":    fight_id,
            "fighter_a":   fighter_a,
            "fighter_b":   fighter_b,
            "model_prob":  round(model_prob, 4),
            "market_prob": round(market_prob, 4) if market_prob is not None else None,
            "edge":        round(edge, 2),
            "confidence":  confidence,
            "division":    division,
            "components":  components,
        }

        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_LOG, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def search_audit(self, query: str, limit: int = 20) -> list[dict]:
        """Search audit log for records matching a query string."""
        if not AUDIT_LOG.exists():
            return []
        results = []
        with open(AUDIT_LOG, "r") as f:
            for line in f:
                if query.lower() in line.lower():
                    try:
                        results.append(json.loads(line))
                    except Exception:
                        pass
        return results[-limit:]


# ── Singleton ─────────────────────────────────────────────────────────────────

_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="UFC model version registry.")
    parser.add_argument("--list",     action="store_true", help="List all registered versions")
    parser.add_argument("--rollback", action="store_true", help="Force rollback to previous version")
    parser.add_argument("--audit",    type=str, default=None,
                        help="Search audit log for a fighter or fight")
    parser.add_argument("--check",    type=float, default=None,
                        help="Check rollback with given recent Brier score")
    args = parser.parse_args()

    reg = get_registry()

    if args.list or (not args.rollback and not args.audit and args.check is None):
        reg.print_versions()

    if args.rollback:
        reg.rollback(reason="manual CLI rollback")

    if args.audit:
        records = reg.search_audit(args.audit)
        if not records:
            print(f"No audit records found for '{args.audit}'.")
        else:
            print(f"\nAudit records for '{args.audit}':")
            for r in records:
                ts = r.get("timestamp", "")[:19]
                print(f"  {ts}  v={r.get('version_id', '?')[:12]}  "
                      f"{r.get('fighter_a', '?')} vs {r.get('fighter_b', '?')}  "
                      f"p={r.get('model_prob', '?')}  edge={r.get('edge', '?')}%  "
                      f"conf={r.get('confidence', '?')}")

    if args.check is not None:
        triggered = reg.check_and_rollback(args.check, n_fights=15)
        print(f"Rollback triggered: {triggered}")


if __name__ == "__main__":
    main()
