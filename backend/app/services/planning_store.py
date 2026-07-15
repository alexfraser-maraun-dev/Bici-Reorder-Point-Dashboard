"""Transactional storage for planner overrides and purchase-order workflow state.

Postgres is used when ``DATABASE_URL`` is a postgres URL. SQLite remains a local
development/test fallback only. BigQuery continues to hold analytical history,
forecast outputs, and append-only reporting facts.
"""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


ALLOWED_DRAFT_TRANSITIONS = {
    "draft": {"approved", "cancelled"},
    "approved": {"draft", "previewed", "cancelled"},
    "previewed": {"draft", "approved", "ready_for_push", "cancelled"},
    "ready_for_push": {"previewed", "pushing", "cancelled"},
    "pushing": {"synchronized", "partial_failure"},
    "partial_failure": {"pushing", "cancelled"},
    "synchronized": set(),
    "cancelled": set(),
}


class PlanningConflict(RuntimeError):
    pass


class PlanningStore:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = database_url or os.getenv("DATABASE_URL") or "sqlite:///replen_app.db"
        self.is_postgres = self.database_url.startswith(("postgres://", "postgresql://"))
        self.initialize()

    def _connect(self):
        if self.is_postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "Postgres DATABASE_URL is configured but psycopg is not installed."
                ) from exc
            return psycopg.connect(self.database_url, row_factory=dict_row)
        path = self.database_url.removeprefix("sqlite:///")
        connection = sqlite3.connect(path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.is_postgres else statement

    @contextmanager
    def transaction(self, immediate: bool = False):
        conn = self._connect()
        try:
            if self.is_postgres:
                conn.execute("BEGIN")
            else:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        id_type = "TEXT" if not self.is_postgres else "TEXT"
        auto_id = "INTEGER PRIMARY KEY AUTOINCREMENT" if not self.is_postgres else "BIGSERIAL PRIMARY KEY"
        with self.transaction() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS po_drafts (
                    draft_id {id_type} PRIMARY KEY,
                    vendor_id TEXT NOT NULL,
                    vendor_name TEXT,
                    shop_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lightspeed_order_id TEXT,
                    notes TEXT,
                    run_id TEXT,
                    model_version TEXT,
                    source_snapshot_at TEXT
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS po_draft_lines (
                    line_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL REFERENCES po_drafts(draft_id) ON DELETE CASCADE,
                    recommendation_id TEXT,
                    sku TEXT,
                    description TEXT,
                    brand TEXT,
                    category_top_level TEXT,
                    item_id TEXT NOT NULL,
                    location_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_cost REAL,
                    landed_cost REAL,
                    currency TEXT NOT NULL DEFAULT 'CAD',
                    source TEXT NOT NULL,
                    reconciliation TEXT,
                    target_lightspeed_order_id TEXT,
                    need_by_week TEXT,
                    case_pack INTEGER,
                    moq INTEGER,
                    constraint_warning TEXT
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS planning_runs (
                    run_id {id_type} PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_snapshot_at TEXT,
                    scope_type TEXT NOT NULL,
                    scope_value TEXT,
                    config_json TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS planner_overrides (
                    override_id TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    location_id TEXT,
                    week_start TEXT,
                    measure TEXT NOT NULL,
                    original_value REAL,
                    override_value REAL NOT NULL,
                    reason TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS po_actions (
                    sequence_id {auto_id},
                    action_id TEXT NOT NULL UNIQUE,
                    draft_id TEXT NOT NULL REFERENCES po_drafts(draft_id),
                    draft_version INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lightspeed_order_id TEXT,
                    lightspeed_order_line_id TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS po_watch_acks (
                    order_id {id_type} PRIMARY KEY,
                    acked_by TEXT,
                    note TEXT,
                    acked_at TEXT NOT NULL,
                    snooze_until TEXT,
                    expected_date TEXT
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS po_watch_meta (
                    key {id_type} PRIMARY KEY,
                    value TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_po_draft_lines_draft ON po_draft_lines(draft_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_po_drafts_status ON po_drafts(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_planning_runs_created ON planning_runs(created_at)")
            # Render may already have the first planning schema. Add display
            # metadata without requiring a destructive/manual migration.
            if self.is_postgres:
                for column in ("description TEXT", "brand TEXT", "category_top_level TEXT"):
                    conn.execute(f"ALTER TABLE po_draft_lines ADD COLUMN IF NOT EXISTS {column}")
            else:
                existing = {row["name"] for row in conn.execute("PRAGMA table_info(po_draft_lines)").fetchall()}
                for name in ("description", "brand", "category_top_level"):
                    if name not in existing:
                        conn.execute(f"ALTER TABLE po_draft_lines ADD COLUMN {name} TEXT")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _dict(row) -> Dict[str, Any]:
        return dict(row)

    def create_draft(self, header: Dict[str, Any], lines: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        draft_id = str(header.get("draft_id") or uuid.uuid4())
        now = self._now()
        normalized_header = {
            "draft_id": draft_id,
            "vendor_id": str(header["vendor_id"]),
            "vendor_name": header.get("vendor_name"),
            "shop_id": str(header["shop_id"]),
            "status": "draft",
            "version": 1,
            "created_by": header.get("created_by") or "UI_User",
            "created_at": now,
            "updated_at": now,
            "lightspeed_order_id": header.get("lightspeed_order_id"),
            "notes": header.get("notes"),
            "run_id": header.get("run_id"),
            "model_version": header.get("model_version"),
            "source_snapshot_at": header.get("source_snapshot_at"),
        }
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("""
                INSERT INTO po_drafts (
                    draft_id,vendor_id,vendor_name,shop_id,status,version,created_by,
                    created_at,updated_at,lightspeed_order_id,notes,run_id,model_version,source_snapshot_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """), tuple(normalized_header.values()))
            self._insert_lines(conn, draft_id, lines)
        return self.get_draft(draft_id)

    def _insert_lines(self, conn, draft_id: str, lines: Iterable[Dict[str, Any]]) -> None:
        for line in lines:
            values = (
                str(line.get("line_id") or uuid.uuid4()), draft_id,
                line.get("recommendation_id"), line.get("sku"), line.get("description"),
                line.get("brand"), line.get("category_top_level"), str(line["item_id"]),
                str(line.get("location_id") or ""), int(line.get("quantity") or 0),
                line.get("unit_cost"), line.get("landed_cost"), line.get("currency") or "CAD",
                line.get("source") or "manual", line.get("reconciliation"),
                line.get("target_lightspeed_order_id"), line.get("need_by_week"),
                line.get("case_pack"), line.get("moq"), line.get("constraint_warning"),
            )
            conn.execute(self._sql("""
                INSERT INTO po_draft_lines (
                    line_id,draft_id,recommendation_id,sku,description,brand,category_top_level,item_id,location_id,quantity,
                    unit_cost,landed_cost,currency,source,reconciliation,target_lightspeed_order_id,
                    need_by_week,case_pack,moq,constraint_warning
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """), values)

    def save_planning_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        """Persist a completed interactive run so browser navigation/restarts do not lose it."""
        record = (
            str(run["run_id"]), str(run.get("status") or "complete"),
            str(run.get("created_at") or self._now()), run.get("source_snapshot_at"),
            str(run.get("scope_type") or "auto_replen"), run.get("scope_value"),
            json.dumps(run.get("config") or {}, sort_keys=True),
            json.dumps(run, sort_keys=True, default=str),
        )
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM planning_runs WHERE run_id = ?"), (record[0],))
            conn.execute(self._sql("""
                INSERT INTO planning_runs (
                    run_id,status,created_at,source_snapshot_at,scope_type,scope_value,config_json,result_json
                ) VALUES (?,?,?,?,?,?,?,?)
            """), record)
            # Interactive persistence is intentionally bounded for the 1 GB
            # Render database. Keep recent work plus any run still referenced by
            # a live draft; BigQuery remains the long-term analytical store.
            conn.execute("""
                DELETE FROM planning_runs
                WHERE run_id NOT IN (
                    SELECT run_id FROM planning_runs ORDER BY created_at DESC LIMIT 12
                )
                AND run_id NOT IN (
                    SELECT DISTINCT run_id FROM po_drafts
                    WHERE run_id IS NOT NULL AND status != 'cancelled'
                )
            """)
        return run

    def get_planning_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                self._sql("SELECT result_json FROM planning_runs WHERE run_id = ?"), (str(run_id),)
            ).fetchone()
            return json.loads(self._dict(row)["result_json"]) if row else None
        finally:
            conn.close()

    def get_latest_planning_run(self) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT result_json FROM planning_runs WHERE status = 'complete' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return json.loads(self._dict(row)["result_json"]) if row else None
        finally:
            conn.close()

    def set_target_order(
        self, draft_id: str, expected_version: int, order_id: Optional[str]
    ) -> Dict[str, Any]:
        """Select an explicit unsent PO (or clear it to create a new PO)."""
        with self.transaction(immediate=True) as conn:
            lock = " FOR UPDATE" if self.is_postgres else ""
            row = conn.execute(self._sql(
                f"SELECT status,version FROM po_drafts WHERE draft_id = ?{lock}"
            ), (draft_id,)).fetchone()
            if not row:
                raise KeyError(draft_id)
            current = self._dict(row)
            if int(current["version"]) != int(expected_version):
                raise PlanningConflict("Draft changed since it was loaded.")
            if current["status"] != "draft":
                raise PlanningConflict("PO routing can only be changed while the draft is editable.")
            target = str(order_id) if order_id not in (None, "", "new") else None
            conn.execute(self._sql("""
                UPDATE po_drafts SET lightspeed_order_id = ?, version = version + 1, updated_at = ?
                WHERE draft_id = ?
            """), (target, self._now(), draft_id))
            conn.execute(self._sql("""
                UPDATE po_draft_lines
                SET target_lightspeed_order_id = ?, reconciliation = ?
                WHERE draft_id = ?
            """), (target, "append_to_open_po" if target else "new_po", draft_id))
        return self.get_draft(draft_id)

    def backfill_line_display_metadata(self, items: Iterable[Dict[str, Any]]) -> int:
        """Fill product display fields on drafts created before those columns existed."""
        updated = 0
        with self.transaction(immediate=True) as conn:
            for item in items:
                if not item.get("item_id"):
                    continue
                cursor = conn.execute(self._sql("""
                    UPDATE po_draft_lines
                    SET description = COALESCE(description, ?),
                        brand = COALESCE(brand, ?),
                        category_top_level = COALESCE(category_top_level, ?)
                    WHERE item_id = ?
                      AND (description IS NULL OR brand IS NULL OR category_top_level IS NULL)
                """), (
                    item.get("description"), item.get("brand"), item.get("category_top_level"),
                    str(item["item_id"]),
                ))
                updated += max(0, int(cursor.rowcount or 0))
        return updated

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                self._sql("SELECT * FROM po_drafts WHERE draft_id = ?"), (draft_id,)
            ).fetchone()
            if not row:
                return None
            draft = self._dict(row)
            lines = conn.execute(
                self._sql("SELECT * FROM po_draft_lines WHERE draft_id = ? ORDER BY line_id"),
                (draft_id,),
            ).fetchall()
            draft["lines"] = [self._dict(line) for line in lines]
            return draft
        finally:
            conn.close()

    def list_drafts(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            if status:
                rows = conn.execute(
                    self._sql("SELECT * FROM po_drafts WHERE status = ? ORDER BY created_at DESC"),
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM po_drafts ORDER BY created_at DESC").fetchall()
            return [self._dict(row) for row in rows]
        finally:
            conn.close()

    def replace_lines(
        self, draft_id: str, expected_version: int, lines: Iterable[Dict[str, Any]]
    ) -> Dict[str, Any]:
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                self._sql("SELECT status,version FROM po_drafts WHERE draft_id = ?"), (draft_id,)
            ).fetchone()
            if not row:
                raise KeyError(draft_id)
            current = self._dict(row)
            if int(current["version"]) != int(expected_version):
                raise PlanningConflict("Draft changed since it was loaded.")
            if current["status"] not in {"draft", "approved", "previewed"}:
                raise PlanningConflict(f"Draft lines cannot be edited in state {current['status']}.")
            conn.execute(self._sql("DELETE FROM po_draft_lines WHERE draft_id = ?"), (draft_id,))
            self._insert_lines(conn, draft_id, lines)
            conn.execute(self._sql("""
                UPDATE po_drafts SET version = version + 1, status = 'draft', updated_at = ?
                WHERE draft_id = ?
            """), (self._now(), draft_id))
        return self.get_draft(draft_id)

    def transition(self, draft_id: str, expected_version: int, new_status: str) -> Dict[str, Any]:
        with self.transaction(immediate=True) as conn:
            lock = " FOR UPDATE" if self.is_postgres else ""
            row = conn.execute(self._sql(
                f"SELECT status,version FROM po_drafts WHERE draft_id = ?{lock}"
            ), (draft_id,)).fetchone()
            if not row:
                raise KeyError(draft_id)
            current = self._dict(row)
            if int(current["version"]) != int(expected_version):
                raise PlanningConflict("Draft changed since it was loaded.")
            if new_status not in ALLOWED_DRAFT_TRANSITIONS.get(current["status"], set()):
                raise PlanningConflict(f"Invalid transition {current['status']} -> {new_status}.")
            conn.execute(self._sql("""
                UPDATE po_drafts SET status = ?, version = version + 1, updated_at = ?
                WHERE draft_id = ?
            """), (new_status, self._now(), draft_id))
        return self.get_draft(draft_id)

    def record_actions(self, draft: Dict[str, Any], operations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = self._now()
        records = []
        with self.transaction(immediate=True) as conn:
            for index, operation in enumerate(operations):
                key = f"{draft['draft_id']}:{draft['version']}:{index}"
                action_id = str(uuid.uuid4())
                conn.execute(self._sql("""
                    INSERT INTO po_actions (
                        action_id,draft_id,draft_version,idempotency_key,action,payload_json,
                        status,created_at,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(idempotency_key) DO NOTHING
                """), (
                    action_id, draft["draft_id"], int(draft["version"]), key,
                    operation["action"], json.dumps(operation, sort_keys=True), "previewed", now, now,
                ))
                records.append({"idempotency_key": key, "action": operation["action"]})
        return records

    # ------------------------------------------------------------------
    # PO-tracker alert acknowledgements
    # ------------------------------------------------------------------
    def upsert_po_ack(self, order_id: str, *, acked_by: Optional[str], note: Optional[str],
                      snooze_until: Optional[str], expected_date: Optional[str]) -> Dict[str, Any]:
        """Acknowledge a late-PO alert. ``expected_date`` pins the PO's expected date
        at ack time so the ack auto-invalidates if the date is later changed in
        Lightspeed. ``snooze_until`` (ISO date) re-arms the alert after that day;
        NULL snoozes until the expected date changes or the PO closes."""
        record = {
            "order_id": str(order_id),
            "acked_by": acked_by or "UI_User",
            "note": note,
            "acked_at": self._now(),
            "snooze_until": snooze_until,
            "expected_date": expected_date,
        }
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM po_watch_acks WHERE order_id = ?"), (record["order_id"],))
            conn.execute(self._sql("""
                INSERT INTO po_watch_acks (order_id,acked_by,note,acked_at,snooze_until,expected_date)
                VALUES (?,?,?,?,?,?)
            """), tuple(record.values()))
        return record

    def delete_po_ack(self, order_id: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM po_watch_acks WHERE order_id = ?"), (str(order_id),))

    def list_po_acks(self) -> Dict[str, Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM po_watch_acks").fetchall()
            return {str(self._dict(row)["order_id"]): self._dict(row) for row in rows}
        finally:
            conn.close()

    def get_po_watch_meta(self, key: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                self._sql("SELECT value FROM po_watch_meta WHERE key = ?"), (key,)
            ).fetchone()
            return self._dict(row)["value"] if row else None
        finally:
            conn.close()

    def set_po_watch_meta(self, key: str, value: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM po_watch_meta WHERE key = ?"), (key,))
            conn.execute(self._sql("INSERT INTO po_watch_meta (key,value) VALUES (?,?)"), (key, value))

    def create_override(self, override: Dict[str, Any]) -> Dict[str, Any]:
        required = ("scope_type", "scope_id", "measure", "override_value", "reason", "created_by")
        missing = [field for field in required if override.get(field) in (None, "")]
        if missing:
            raise ValueError(f"Missing override fields: {', '.join(missing)}")
        record = {
            "override_id": str(uuid.uuid4()),
            "scope_type": override["scope_type"],
            "scope_id": str(override["scope_id"]),
            "location_id": override.get("location_id"),
            "week_start": override.get("week_start"),
            "measure": override["measure"],
            "original_value": override.get("original_value"),
            "override_value": float(override["override_value"]),
            "reason": override["reason"],
            "created_by": override["created_by"],
            "created_at": self._now(),
            "expires_at": override.get("expires_at"),
            "active": 1,
        }
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("""
                INSERT INTO planner_overrides (
                    override_id,scope_type,scope_id,location_id,week_start,measure,
                    original_value,override_value,reason,created_by,created_at,expires_at,active
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """), tuple(record.values()))
        return record


_store: Optional[PlanningStore] = None


def get_planning_store() -> PlanningStore:
    global _store
    if _store is None:
        _store = PlanningStore()
    return _store
