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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_po_draft_lines_draft ON po_draft_lines(draft_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_po_drafts_status ON po_drafts(status)")

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
                line.get("recommendation_id"), line.get("sku"), str(line["item_id"]),
                str(line.get("location_id") or ""), int(line.get("quantity") or 0),
                line.get("unit_cost"), line.get("landed_cost"), line.get("currency") or "CAD",
                line.get("source") or "manual", line.get("reconciliation"),
                line.get("target_lightspeed_order_id"), line.get("need_by_week"),
                line.get("case_pack"), line.get("moq"), line.get("constraint_warning"),
            )
            conn.execute(self._sql("""
                INSERT INTO po_draft_lines (
                    line_id,draft_id,recommendation_id,sku,item_id,location_id,quantity,
                    unit_cost,landed_cost,currency,source,reconciliation,target_lightspeed_order_id,
                    need_by_week,case_pack,moq,constraint_warning
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """), values)

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

