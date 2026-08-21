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


def _opt_str(value: Any) -> Optional[str]:
    """Coerce an id to TEXT for storage, preserving NULL (0 and "" are not ids here)."""
    if value is None or value == "" or str(value) == "0":
        return None
    return str(value)


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
            # --- Feature access control (see services/access/) -----------------
            # One row per feature key the admin has explicitly toggled. Absence of
            # a row means "use the registry default", so a fresh deployment behaves
            # exactly as the code ships and clearing a row reverts to that default.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS app_feature_flags (
                    feature_key {id_type} PRIMARY KEY,
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                )
            """)
            # One row per user the admin has configured. role is 'admin' or 'member';
            # overrides_json is a {feature_key: bool} map applied on top of the global
            # flags for that user only. Users with no row get the default role.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS app_user_access (
                    email {id_type} PRIMARY KEY,
                    role TEXT NOT NULL,
                    overrides_json TEXT,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT
                )
            """)
            # --- Special-order SLA (see services/so_stage_log.py, so_sla_service.py) ---
            # Append-only stage observations. Deliberately NOT one row per (SO, stage): a
            # special order moves backwards when its PO is deleted or it is re-allocated, and
            # a unique (so_id, stage) row would corrupt on that bounce. entered_at is the
            # AUTHORITATIVE Lightspeed timestamp wherever one exists ('derived'); only the
            # transitions Lightspeed never stamps fall back to 'observed'.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS so_stage_events (
                    event_id {id_type} PRIMARY KEY,
                    special_order_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    entered_at TEXT NOT NULL,
                    entered_source TEXT NOT NULL,
                    left_at TEXT,
                    shop_id TEXT,
                    source TEXT,
                    order_id TEXT,
                    vendor_id TEXT,
                    item_id TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                )
            """)
            # Promise ledger. The headline SLA scores against the ORIGINAL promise, so every
            # quoted date is kept and superseded rather than overwritten -- otherwise the
            # number is gameable by sliding the ETA. promise_key is built in Python so the
            # uniqueness constraint stays portable across SQLite and Postgres.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS so_promises (
                    promise_id {id_type} PRIMARY KEY,
                    promise_key TEXT NOT NULL UNIQUE,
                    special_order_id TEXT,
                    shopify_order_id TEXT,
                    promise_date TEXT NOT NULL,
                    promise_source TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    recorded_by TEXT,
                    superseded_at TEXT,
                    revision_index INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Manual Shopify<->LS links, plus pre-SO placeholders. A row with a NULL
            # special_order_id is an intake placeholder: CS has tagged a Shopify order but no
            # Lightspeed SO exists yet, so there is no id to key a link on. The SLA clock
            # starts on those, and they retire when a real SO links.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS so_shopify_links (
                    link_id {id_type} PRIMARY KEY,
                    special_order_id TEXT,
                    shopify_order_id TEXT NOT NULL,
                    shopify_line_item_id TEXT,
                    action TEXT NOT NULL,
                    intake_status TEXT,
                    owner_email TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    superseded_at TEXT
                )
            """)
            # Reason-coded acknowledgements with a MANDATORY check-back date. Unlike
            # po_watch_acks (which pins only the expected date), an SO ack pins THREE things:
            # stage, promise and PO ETA. Pinning one is not enough -- a snoozed SO that
            # regresses to an earlier stage, or whose customer promise moves, must re-arm or
            # the snooze silently hides a new problem.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS so_acks (
                    special_order_id {id_type} PRIMARY KEY,
                    acked_by TEXT,
                    reason_code TEXT NOT NULL,
                    note TEXT,
                    acked_at TEXT NOT NULL,
                    checkback_date TEXT NOT NULL,
                    pinned_stage TEXT,
                    pinned_promise TEXT,
                    pinned_po_eta TEXT,
                    escalation_level INTEGER NOT NULL DEFAULT 0
                )
            """)
            # Append-only operational activity that has no authoritative timestamp in
            # Lightspeed. Stage milestones and promise revisions remain in their purpose-built
            # tables; this ledger records human actions such as park/unpark, clearing a service
            # promise, and manual match decisions so the row-detail timeline is auditable.
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS so_activity_events (
                    event_id {id_type} PRIMARY KEY,
                    special_order_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    actor TEXT,
                    details_json TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_so_acks_checkback ON so_acks(checkback_date)")
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_so_stage_events_natural
                ON so_stage_events(special_order_id, stage, entered_at)
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_so_stage_events_open ON so_stage_events(left_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_so_promises_so ON so_promises(special_order_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_so_activity_so ON so_activity_events(special_order_id, occurred_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_so_links_so ON so_shopify_links(special_order_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_so_links_shopify ON so_shopify_links(shopify_order_id)")
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

    # ------------------------------------------------------------------
    # Special-order SLA
    # ------------------------------------------------------------------
    def record_so_stage_observations(self, observations: List[Dict[str, Any]]) -> Dict[str, int]:
        """Persist one sweep's worth of stage observations.

        Each observation is ``{special_order_id, stage, entered_at, entered_source, shop_id,
        source, order_id, vendor_id, item_id}``. Insertion is idempotent on the natural key
        (special_order_id, stage, entered_at): re-observing the same stage entry only refreshes
        ``last_seen_at``, so sweeping every five minutes costs nothing and a stage the SO
        bounces back into later records a genuinely new row.
        """
        inserted = touched = 0
        now = self._now()
        with self.transaction(immediate=True) as conn:
            for ob in observations:
                so_id = str(ob["special_order_id"])
                key = (so_id, str(ob["stage"]), str(ob["entered_at"]))
                existing = conn.execute(self._sql(
                    "SELECT event_id FROM so_stage_events "
                    "WHERE special_order_id = ? AND stage = ? AND entered_at = ?"
                ), key).fetchone()
                if existing:
                    conn.execute(self._sql(
                        "UPDATE so_stage_events SET last_seen_at = ? "
                        "WHERE special_order_id = ? AND stage = ? AND entered_at = ?"
                    ), (now,) + key)
                    touched += 1
                    continue
                conn.execute(self._sql("""
                    INSERT INTO so_stage_events (event_id,special_order_id,stage,entered_at,
                        entered_source,left_at,shop_id,source,order_id,vendor_id,item_id,
                        first_seen_at,last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """), (
                    uuid.uuid4().hex, so_id, str(ob["stage"]), str(ob["entered_at"]),
                    ob.get("entered_source") or "observed", None,
                    _opt_str(ob.get("shop_id")), ob.get("source"), _opt_str(ob.get("order_id")),
                    _opt_str(ob.get("vendor_id")), _opt_str(ob.get("item_id")), now, now,
                ))
                inserted += 1
        return {"inserted": inserted, "touched": touched}

    def list_so_stage_events(self, special_order_ids: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            if special_order_ids is None:
                rows = conn.execute("SELECT * FROM so_stage_events").fetchall()
                return [self._dict(r) for r in rows]
            ids = [str(i) for i in special_order_ids]
            if not ids:
                return []
            out: List[Dict[str, Any]] = []
            for start in range(0, len(ids), 500):  # keep the IN list under driver limits
                chunk = ids[start:start + 500]
                marks = ",".join("?" for _ in chunk)
                rows = conn.execute(self._sql(
                    f"SELECT * FROM so_stage_events WHERE special_order_id IN ({marks})"
                ), tuple(chunk)).fetchall()
                out.extend(self._dict(r) for r in rows)
            return out
        finally:
            conn.close()

    def upsert_so_ack(self, special_order_id: str, *, acked_by: Optional[str], reason_code: str,
                      note: Optional[str], checkback_date: str, pinned_stage: Optional[str],
                      pinned_promise: Optional[str], pinned_po_eta: Optional[str],
                      escalation_level: int = 0) -> Dict[str, Any]:
        """Acknowledge a special-order breach until ``checkback_date``.

        The three pinned values are the re-arm triggers: if the SO's stage, customer promise or
        PO ETA changes afterwards, the ack stops applying and the row surfaces again. That is
        what stops a snooze from masking a *new* problem on an SO someone already looked at.
        """
        record = {
            "special_order_id": str(special_order_id),
            "acked_by": acked_by,
            "reason_code": reason_code,
            "note": note,
            "acked_at": self._now(),
            "checkback_date": checkback_date,
            "pinned_stage": pinned_stage,
            "pinned_promise": pinned_promise,
            "pinned_po_eta": pinned_po_eta,
            "escalation_level": int(escalation_level),
        }
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM so_acks WHERE special_order_id = ?"),
                         (record["special_order_id"],))
            conn.execute(self._sql("""
                INSERT INTO so_acks (special_order_id,acked_by,reason_code,note,acked_at,
                    checkback_date,pinned_stage,pinned_promise,pinned_po_eta,escalation_level)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """), tuple(record.values()))
        return record

    def delete_so_ack(self, special_order_id: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM so_acks WHERE special_order_id = ?"),
                         (str(special_order_id),))

    def list_so_acks(self) -> Dict[str, Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM so_acks").fetchall()
            return {str(self._dict(r)["special_order_id"]): self._dict(r) for r in rows}
        finally:
            conn.close()

    def record_so_promise(self, *, special_order_id: Optional[str], shopify_order_id: Optional[str],
                          promise_date: str, promise_source: str,
                          recorded_by: Optional[str] = None) -> bool:
        """Append a quoted date to the promise ledger. Returns True when this is a NEW promise.

        The ledger is keyed on the (order, date, source) tuple, so re-observing an unchanged
        promise on every sweep is a no-op while a genuine re-quote lands as a new revision.
        The earliest surviving row is the ORIGINAL promise the headline SLA scores against.
        """
        key = f"{special_order_id or ''}:{shopify_order_id or ''}:{promise_date}:{promise_source}"
        with self.transaction(immediate=True) as conn:
            if conn.execute(self._sql(
                "SELECT promise_id FROM so_promises WHERE promise_key = ?"
            ), (key,)).fetchone():
                return False
            scope_col = "special_order_id" if special_order_id else "shopify_order_id"
            scope_val = special_order_id or shopify_order_id
            prior = conn.execute(self._sql(
                f"SELECT COUNT(*) AS n FROM so_promises WHERE {scope_col} = ?"
            ), (str(scope_val),)).fetchone() if scope_val else None
            revision = int(self._dict(prior)["n"]) if prior else 0
            if revision:
                conn.execute(self._sql(
                    f"UPDATE so_promises SET superseded_at = ? "
                    f"WHERE {scope_col} = ? AND promise_source = ? AND superseded_at IS NULL"
                ), (self._now(), str(scope_val), promise_source))
            conn.execute(self._sql("""
                INSERT INTO so_promises (promise_id,promise_key,special_order_id,shopify_order_id,
                    promise_date,promise_source,recorded_at,recorded_by,superseded_at,revision_index)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """), (
                uuid.uuid4().hex, key, _opt_str(special_order_id), _opt_str(shopify_order_id),
                promise_date, promise_source, self._now(), recorded_by, None, revision,
            ))
        return True

    def set_service_promise(self, special_order_id: str, promise_date: str,
                            recorded_by: Optional[str] = None) -> Dict[str, Any]:
        """Set an app-owned parts promise for a service special order.

        Service's Workorder ``etaOut`` is a booking/service date, not a parts commitment, so a
        real parts promise needs its own durable source. Revisions are appended and the prior
        active value is superseded; setting the already-active value is idempotent.
        """
        so_id = str(special_order_id)
        now = self._now()
        with self.transaction(immediate=True) as conn:
            active_row = conn.execute(self._sql(
                "SELECT * FROM so_promises WHERE special_order_id = ? "
                "AND promise_source = ? AND superseded_at IS NULL "
                "ORDER BY revision_index DESC LIMIT 1"
            ), (so_id, "service_manual")).fetchone()
            active = self._dict(active_row) if active_row else None
            if active and str(active.get("promise_date"))[:10] == str(promise_date)[:10]:
                return active

            conn.execute(self._sql(
                "UPDATE so_promises SET superseded_at = ? WHERE special_order_id = ? "
                "AND promise_source = ? AND superseded_at IS NULL"
            ), (now, so_id, "service_manual"))
            count_row = conn.execute(self._sql(
                "SELECT COUNT(*) AS n FROM so_promises WHERE special_order_id = ?"
            ), (so_id,)).fetchone()
            revision = int(self._dict(count_row)["n"]) if count_row else 0
            promise_id = uuid.uuid4().hex
            # A manual promise may legitimately return to a previously-used date after being
            # cleared, so its key includes the immutable event id rather than blocking reuse.
            key = f"{so_id}::service_manual:{str(promise_date)[:10]}:{promise_id}"
            record = {
                "promise_id": promise_id,
                "promise_key": key,
                "special_order_id": so_id,
                "shopify_order_id": None,
                "promise_date": str(promise_date)[:10],
                "promise_source": "service_manual",
                "recorded_at": now,
                "recorded_by": recorded_by,
                "superseded_at": None,
                "revision_index": revision,
            }
            conn.execute(self._sql("""
                INSERT INTO so_promises (promise_id,promise_key,special_order_id,shopify_order_id,
                    promise_date,promise_source,recorded_at,recorded_by,superseded_at,revision_index)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """), tuple(record.values()))
        return record

    def clear_service_promise(self, special_order_id: str) -> bool:
        """Supersede the active service promise, preserving every prior revision."""
        with self.transaction(immediate=True) as conn:
            cursor = conn.execute(self._sql(
                "UPDATE so_promises SET superseded_at = ? WHERE special_order_id = ? "
                "AND promise_source = ? AND superseded_at IS NULL"
            ), (self._now(), str(special_order_id), "service_manual"))
            return bool(cursor.rowcount)

    def active_service_promises(self) -> Dict[str, Dict[str, Any]]:
        """Current app-owned service promises keyed by Lightspeed special-order id."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM so_promises WHERE promise_source = 'service_manual' "
                "AND superseded_at IS NULL ORDER BY revision_index"
            ).fetchall()
            return {
                str(self._dict(row)["special_order_id"]): self._dict(row)
                for row in rows if self._dict(row).get("special_order_id")
            }
        finally:
            conn.close()

    def list_so_promises(self, special_order_id: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            if special_order_id:
                rows = conn.execute(self._sql(
                    "SELECT * FROM so_promises WHERE special_order_id = ? ORDER BY revision_index"
                ), (str(special_order_id),)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM so_promises ORDER BY recorded_at").fetchall()
            return [self._dict(r) for r in rows]
        finally:
            conn.close()

    def record_so_activity(self, special_order_id: str, event_type: str, *,
                           actor: Optional[str] = None,
                           details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Append one human/system action to the special-order activity ledger."""
        record = {
            "event_id": uuid.uuid4().hex,
            "special_order_id": str(special_order_id),
            "event_type": str(event_type),
            "occurred_at": self._now(),
            "actor": actor,
            "details_json": json.dumps(details or {}, sort_keys=True),
        }
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("""
                INSERT INTO so_activity_events
                    (event_id,special_order_id,event_type,occurred_at,actor,details_json)
                VALUES (?,?,?,?,?,?)
            """), tuple(record.values()))
        return {**record, "details": details or {}}

    def list_so_activity(self, special_order_id: str) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(self._sql(
                "SELECT * FROM so_activity_events WHERE special_order_id = ? "
                "ORDER BY occurred_at DESC"
            ), (str(special_order_id),)).fetchall()
            out = []
            for row in rows:
                item = self._dict(row)
                try:
                    item["details"] = json.loads(item.pop("details_json") or "{}")
                except (TypeError, ValueError):
                    item["details"] = {}
                    item.pop("details_json", None)
                out.append(item)
            return out
        finally:
            conn.close()

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

    # ------------------------------------------------------------------
    # Feature access control
    # ------------------------------------------------------------------

    def list_feature_flags(self) -> Dict[str, bool]:
        """Explicitly-set feature toggles, {feature_key: enabled}. Keys absent
        here fall back to the registry default."""
        with self.transaction() as conn:
            rows = conn.execute("SELECT feature_key, enabled FROM app_feature_flags").fetchall()
        return {row["feature_key"]: bool(row["enabled"]) for row in rows}

    def set_feature_flag(self, feature_key: str, enabled: bool, updated_by: str = "Dashboard") -> None:
        now = self._now()
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM app_feature_flags WHERE feature_key = ?"), (feature_key,))
            conn.execute(self._sql(
                "INSERT INTO app_feature_flags (feature_key,enabled,updated_at,updated_by) VALUES (?,?,?,?)"
            ), (feature_key, 1 if enabled else 0, now, updated_by))

    def clear_feature_flag(self, feature_key: str) -> None:
        """Drops the override so the feature reverts to its registry default."""
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM app_feature_flags WHERE feature_key=?"), (feature_key,))

    def list_user_access(self) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT email, role, overrides_json, updated_at, updated_by FROM app_user_access ORDER BY email"
            ).fetchall()
        out = []
        for row in rows:
            record = dict(row)
            try:
                record["overrides"] = json.loads(record.pop("overrides_json") or "{}")
            except (TypeError, ValueError):
                record["overrides"] = {}
                record.pop("overrides_json", None)
            out.append(record)
        return out

    def upsert_user_access(
        self,
        email: str,
        role: str,
        overrides: Optional[Dict[str, bool]] = None,
        updated_by: str = "Dashboard",
    ) -> Dict[str, Any]:
        now = self._now()
        payload = json.dumps(overrides or {}, sort_keys=True)
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM app_user_access WHERE email = ?"), (email,))
            conn.execute(self._sql(
                "INSERT INTO app_user_access (email,role,overrides_json,updated_at,updated_by) VALUES (?,?,?,?,?)"
            ), (email, role, payload, now, updated_by))
        return {"email": email, "role": role, "overrides": overrides or {},
                "updated_at": now, "updated_by": updated_by}

    def delete_user_access(self, email: str) -> None:
        with self.transaction(immediate=True) as conn:
            conn.execute(self._sql("DELETE FROM app_user_access WHERE email=?"), (email,))

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
