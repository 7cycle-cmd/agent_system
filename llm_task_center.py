"""llm_task_center — All-in-1 Task Center (SSOT tables + queries).

Two core tables in agent.db:
- skill_template : capability template (template_id = catalog.subcatalog)
- task_instances  : runtime execution instances (Hard Gate on task_id)

Importable library (no side effects on import).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import skill_prompt
from skill_prompt_ext import SKILL_TC_ITEMS

logger = logging.getLogger(__name__)

DEFAULT_DB = skill_prompt.DEFAULT_DB

TEMPLATE_DDL = """
CREATE TABLE IF NOT EXISTS skill_template (
    template_id    TEXT PRIMARY KEY,
    catalog        INTEGER NOT NULL,
    subcatalog     INTEGER NOT NULL,
    item_type      TEXT NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    spec           TEXT,
    experience_log TEXT,
    description    TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skill_template_catalog
  ON skill_template (catalog, subcatalog);
"""

INSTANCE_DDL = """
CREATE TABLE IF NOT EXISTS task_instances (
    task_id         TEXT PRIMARY KEY NOT NULL UNIQUE,
    template_id     TEXT,
    agent_worker_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    payload         TEXT,
    trace_log       TEXT,
    source          TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_instances_template
  ON task_instances (template_id);
CREATE INDEX IF NOT EXISTS idx_task_instances_status
  ON task_instances (status);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = skill_prompt._connect(db_path)
    ensure_tables(conn)
    return conn


def ensure_tables(conn: sqlite3.Connection | None = None) -> None:
    """Create skill_template + task_instances if missing (idempotent)."""
    own = conn is None
    if own:
        conn = skill_prompt._connect(DEFAULT_DB)
    try:
        conn.executescript(TEMPLATE_DDL)
        conn.executescript(INSTANCE_DDL)
        # Migration: add source column to task_instances if missing (idempotent)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(task_instances)").fetchall()}
        if "source" not in cols:
            conn.execute("ALTER TABLE task_instances ADD COLUMN source TEXT")
        conn.commit()
    finally:
        if own:
            conn.close()


def seed_skill_templates(
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Backfill skill_template from SKILL_TC_ITEMS + registry (idempotent upsert)."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        # The version map used to come from `skill_task_id_allocator`'s registry
        # (`{catalog}.{subcatalog}-T-{version}`). That allocator was RETIRED
        # 2026-09-21: it produced a DIFFERENT id format from the entity id
        # (`{LETTER}-{table_id}-{row_id}-{version}`; the 3-part form is the OLD
        # one), and its registry held 2 rows
        # while `SKILL_TC_ITEMS` has 20 entries -- so the lookup was already
        # returning the default for every template. The dependency is removed
        # rather than kept for a map that was effectively empty.
        #
        # `skill_template.version` is a TEMPLATE revision, not an entity
        # version. Entity versions live in `version_registry` and are reached
        # through `entity_id`, which is a different space on purpose.
        inserted = updated = 0
        for it in SKILL_TC_ITEMS:
            tid = f"{it.get('catalog', 10)}.{it['seq']}"
            catalog = int(it.get("catalog", 10))
            sub = int(it["seq"])
            item_type = str(it["item_type"]).upper()
            version = 1
            desc = it.get("title") or it.get("name") or ""
            row = conn.execute(
                "SELECT template_id FROM skill_template WHERE template_id=?", (tid,)
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE skill_template SET catalog=?, subcatalog=?, item_type=?,
                       version=?, description=?, updated_at=? WHERE template_id=?""",
                    (catalog, sub, item_type, version, desc, _utc_now(), tid),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO skill_template
                       (template_id, catalog, subcatalog, item_type, version, spec, experience_log, description)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (tid, catalog, sub, item_type, version, None, None, desc),
                )
                inserted += 1
        conn.commit()
        return {"ok": True, "inserted": inserted, "updated": updated, "total": len(SKILL_TC_ITEMS)}
    finally:
        if own:
            conn.close()


def submit_task(
    task_id: str,
    template_id: str | None,
    *,
    agent_worker_id: str | None = None,
    payload: Any = None,
    status: str = "pending",
    validate_8dim: bool = True,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Hard Gate: task_id must be present/non-blank. Insert into task_instances.

    When validate_8dim=True (default), runs the 8-dim ontology validation
    (Task|Channel|Module|Capability|API|Function|Table|Field) via
    validate_new_task. On FAIL, rejects with code VALIDATION_FAILED and does
    NOT insert. Callers that don't carry 8-dim payloads (e.g. skill-sync tasks)
    should pass validate_8dim=False to avoid being blocked.
    """
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "code": "MISSING_TASK_ID", "error_code": "MISSING_TASK_ID",
                "message": "task_id is required and must be non-empty"}
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        # validate template_id exists in skill_template (if provided)
        if template_id:
            tpl = conn.execute(
                "SELECT template_id FROM skill_template WHERE template_id=?", (template_id,)
            ).fetchone()
            if not tpl:
                return {"ok": False, "code": "UNKNOWN_TEMPLATE", "error_code": "UNKNOWN_TEMPLATE",
                        "message": f"template_id not found: {template_id}"}
        existing = conn.execute(
            "SELECT task_id FROM task_instances WHERE task_id=?", (tid,)
        ).fetchone()
        if existing:
            return {"ok": False, "code": "DUP_TASK_ID", "error_code": "DUP_TASK_ID",
                    "message": f"task_id already exists: {tid}"}
        # 8-dim Hard Gate: validate payload against ontology registry (read-only).
        if validate_8dim:
            try:
                from src.task_center.skill_task_validate import validate_new_task
                vres = validate_new_task(payload, db_path=db_path)
                if not vres.get("ok"):
                    return {
                        "ok": False,
                        "code": "VALIDATION_FAILED",
                        "error_code": "VALIDATION_FAILED",
                        "message": "8-dim validation failed",
                        "errors": vres.get("errors", []),
                        "validation": vres,
                    }
            except Exception as e:
                return {"ok": False, "code": "VALIDATION_ERROR", "error_code": "VALIDATION_ERROR",
                        "message": f"validation error: {e}"}
        conn.execute(
            """INSERT INTO task_instances
               (task_id, template_id, agent_worker_id, status, payload, trace_log, source, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tid, template_id, agent_worker_id, status,
             json.dumps(payload, ensure_ascii=False) if payload is not None else None,
             None, "submit", _utc_now()),
        )
        conn.commit()
        return {"ok": True, "task_id": tid, "template_id": template_id, "status": status}
    finally:
        if own:
            conn.close()


def ensure_instance(
    task_id: str,
    template_id: str | None,
    *,
    agent_worker_id: str | None = None,
    payload: Any = None,
    status: str = "pending",
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Idempotent task instance creation (auto-populate, NO 8-dim validation).

    If task_id already exists, return the existing row (created=False).
    Otherwise insert a new pending row (created=True). Unlike submit_task,
    this does NOT error on duplicates — it is safe to call every time a task
    ID is generated/allocated, so task_instances auto-populates.

    IMPORTANT: this is an auto-populate mechanism and does NOT run the 8-dim
    ontology validation. Bypass reason: allocator auto-ensure should not be
    blocked by the gate. If you need 8-dim validation, use submit_task, NOT
    ensure_instance. A warning is logged on every new insert so audits can
    trace which tasks entered via this bypass path.
    """
    tid = (task_id or "").strip()
    if not tid:
        return {"ok": False, "code": "MISSING_TASK_ID", "error_code": "MISSING_TASK_ID",
                "message": "task_id is required and must be non-empty"}
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        existing = conn.execute(
            "SELECT task_id, status, template_id FROM task_instances WHERE task_id=?", (tid,)
        ).fetchone()
        if existing:
            return {
                "ok": True,
                "task_id": tid,
                "template_id": existing["template_id"],
                "status": existing["status"],
                "created": False,
            }
        # Bypass trace: log every new insert so audits can see which tasks
        # entered task_instances without 8-dim validation.
        logger.warning("ensure_instance bypasses 8-dim validation: task_id=%s", tid)
        conn.execute(
            """INSERT INTO task_instances
               (task_id, template_id, agent_worker_id, status, payload, trace_log, source, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (tid, template_id, agent_worker_id, status,
             json.dumps(payload, ensure_ascii=False) if payload is not None else None,
             None, "ensure", _utc_now()),
        )
        conn.commit()
        return {"ok": True, "task_id": tid, "template_id": template_id,
                "status": status, "created": True}
    finally:
        if own:
            conn.close()


def list_instances(
    *,
    template_filter: str | None = None,
    limit: int = 200,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """All task_instances JOIN skill_template. Hard Gate: drop null/empty task_id."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        sql = (
            "SELECT i.task_id, i.template_id, i.agent_worker_id, i.status, i.payload, "
            "i.trace_log, i.created_at, i.finished_at, "
            "t.catalog, t.subcatalog, t.item_type, t.version, t.description "
            "FROM task_instances i LEFT JOIN skill_template t ON t.template_id = i.template_id "
            "WHERE 1=1"
        )
        args: list[Any] = []
        if template_filter:
            sql += " AND t.item_type = ?"
            args.append(template_filter)
        sql += " ORDER BY i.created_at DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = conn.execute(sql, args).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            # Hard Gate: drop rows without valid task_id
            if not (d.get("task_id") or "").strip():
                continue
            if d.get("payload"):
                try:
                    d["payload"] = json.loads(d["payload"])
                except (json.JSONDecodeError, TypeError):
                    pass
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


def get_instance(
    task_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        row = conn.execute(
            """SELECT i.task_id, i.template_id, i.agent_worker_id, i.status, i.payload,
               i.trace_log, i.created_at, i.finished_at,
               t.catalog, t.subcatalog, t.item_type, t.version, t.description
               FROM task_instances i LEFT JOIN skill_template t ON t.template_id = i.template_id
               WHERE i.task_id=?""",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d
    finally:
        if own:
            conn.close()


# Allowed lifecycle statuses (state machine).
VALID_STATUSES = frozenset({"pending", "running", "success", "failed"})


def update_instance(
    task_id: str,
    *,
    status: str | None = None,
    trace_log: str | None = None,
    finished_at: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Update a task instance's status / trace_log / finished_at.

    finished_at: "auto" -> now; a timestamp string -> as-is; None -> leave unchanged.
    Returns TASK_NOT_FOUND if task_id does not exist.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT task_id, status FROM task_instances WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "code": "TASK_NOT_FOUND", "error_code": "TASK_NOT_FOUND",
                    "message": f"task_id not found: {task_id}"}
        if status is not None and status not in VALID_STATUSES:
            return {"ok": False, "code": "INVALID_STATUS", "error_code": "INVALID_STATUS",
                    "message": f"invalid status: {status}"}
        sets: list[str] = []
        args: list[Any] = []
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if trace_log is not None:
            sets.append("trace_log=?")
            args.append(trace_log)
        if finished_at is not None:
            if finished_at == "auto":
                sets.append("finished_at=?")
                args.append(_utc_now())
            else:
                sets.append("finished_at=?")
                args.append(finished_at)
        if not sets:
            return {"ok": True, "task_id": task_id, "message": "no fields to update"}
        args.append(task_id)
        conn.execute(f"UPDATE task_instances SET {', '.join(sets)} WHERE task_id=?", args)
        # experience_log write-back: when a task reaches a terminal status
        # (success/failed), append its trace_log to the template's experience_log.
        experience_appended = False
        if status in ("success", "failed"):
            experience_appended = _append_experience(
                conn, task_id, status, trace_log, finished_at
            )
        conn.commit()
        return {"ok": True, "task_id": task_id, "status": status,
                "experience_appended": experience_appended}
    finally:
        if own:
            conn.close()


def _append_experience(
    conn: sqlite3.Connection,
    task_id: str,
    status: str,
    trace_log: str | None,
    finished_at: str | None,
) -> bool:
    """Append a task's trace_log to its template's experience_log (best-effort).

    Returns True if an experience entry was appended, False otherwise.
    """
    try:
        row = conn.execute(
            "SELECT template_id FROM task_instances WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row or not row["template_id"]:
            return False
        template_id = row["template_id"]
        tpl = conn.execute(
            "SELECT experience_log FROM skill_template WHERE template_id=?", (template_id,)
        ).fetchone()
        if not tpl:
            return False
        # Build a new experience entry.
        entry = {
            "task_id": task_id,
            "status": status,
            "trace": trace_log or "",
            "finished_at": finished_at if finished_at != "auto" else _utc_now(),
        }
        existing = tpl["experience_log"]
        entries: list[dict[str, Any]] = []
        if existing:
            try:
                parsed = json.loads(existing)
                if isinstance(parsed, list):
                    entries = parsed
            except (json.JSONDecodeError, TypeError):
                entries = []
        entries.append(entry)
        conn.execute(
            "UPDATE skill_template SET experience_log=?, updated_at=? WHERE template_id=?",
            (json.dumps(entries, ensure_ascii=False), _utc_now(), template_id),
        )
        return True
    except Exception:
        return False


def delete_instance(
    task_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Delete a task instance. Returns TASK_NOT_FOUND if it does not exist."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT task_id FROM task_instances WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "code": "TASK_NOT_FOUND", "error_code": "TASK_NOT_FOUND",
                    "message": f"task_id not found: {task_id}"}
        conn.execute("DELETE FROM task_instances WHERE task_id=?", (task_id,))
        conn.commit()
        return {"ok": True, "task_id": task_id, "deleted": True}
    finally:
        if own:
            conn.close()


def get_template(
    template_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM skill_template WHERE template_id=?", (template_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("spec", "experience_log"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
    finally:
        if own:
            conn.close()


def graph_map(
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """The CATALOG TREE, from `catalog.parent_id`, with each node's templates.

    THE HUMAN (2026-09-27), verbatim
    ---------------------------------
        "catalog table has upgrade with parent_id, subcatalog is not need any more"

    WHAT WAS WRONG, MEASURED (2026-09-27)
    --------------------------------------
    The previous version read `SELECT ... FROM skill_template ORDER BY catalog,
    subcatalog` and grouped by the INTEGER `catalog` column. MEASURED, that is not
    a catalog at all:

        skill_template.catalog     = 10 for ALL 21 rows
        skill_template.subcatalog  = 1..21
        skill_template.template_id = "10.2", "10.3", ...

    and `skill_prompt_ext.py:50` states `ROOT_TASK_ID = 10` with 21
    `SKILL_TC_ITEMS`. So `catalog` is the ROOT TASK ID and `subcatalog` is the
    item's SEQ — `template_id` is literally `{root}.{seq}`. Both columns are LEGACY
    NAMES for the task numbering, and the integer 10 is NOT a row of `catalog`
    (whose ids are 1..5).

    THE TWO NAMES COLLIDE, AND THAT IS THE WHOLE DEFECT. `catalog` is a registered
    term (term_id=33): *"A skill LIBRARY shelf: which kind of work a skill belongs
    to for FINDING it"* — ids 1..5 (`UI`, `QA`, `chat_center`, `verification`,
    `ui_panel`), read by `terminology_catalog`. `skill_template.catalog` is a task
    root. Grouping the second by the first's name produced ONE group labelled
    `10` — matching no shelf — so the page showed a single node called "10" and the
    five real shelves never appeared.

    WHAT THIS DOES NOW
    -------------------
        * the TREE comes from `catalog.parent_id` — the human's ruling, and the
          same shape `terminology_catalog` reads, so the two cannot disagree;
        * NO view and NO `subcatalog` column is consulted: `subcatalog` is a VIEW
          over `catalog WHERE parent_id IS NOT NULL`, so it is DERIVED, not
          removed, and reading it would keep the old name alive;
        * each template carries `task_root` / `task_seq`, which is what its two
          legacy columns ACTUALLY mean — never read as `catalog` ids.

    A skill template is NOT yet linked to a shelf. MEASURED: no FK and no join
    column; `template_id` = `{root}.{seq}`, `catalog` = 10. `catalog_id` on a
    template is therefore `None` unless the legacy number happens to BE a real
    `catalog` id, and `unlinked_templates` reports the count. The page states that
    truth instead of inventing a link — a wrong shelf is worse than no shelf,
    because a reader cannot tell it is wrong.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        # --- the TREE, from `parent_id` (the single source) ----------------
        nodes = [dict(r) for r in conn.execute(
            "SELECT id, parent_id, name, description, legacy_id, is_active "
            "FROM catalog ORDER BY id")]
        by_id: dict[int, dict[str, Any]] = {}
        child_of: dict[int | None, list[dict[str, Any]]] = {}
        for n in nodes:
            pid = int(n["parent_id"]) if n["parent_id"] is not None else None
            node = {"catalog_id": int(n["id"]), "name": n["name"],
                    "description": n["description"],
                    "legacy_id": n["legacy_id"],
                    "is_active": int(n["is_active"] or 0),
                    "parent_id": pid,
                    # THE KIND IS DERIVED, never stored — the same rule
                    # `terminology_catalog.catalog_node_kind` uses ("children > 0"),
                    # so a stored kind cannot go stale against the tree.
                    "node_kind": "leaf",
                    "children": [], "templates": []}
            by_id[int(n["id"])] = node
            child_of.setdefault(pid, []).append(node)
        for n in nodes:
            nid = int(n["id"])
            kids = child_of.get(nid, [])
            by_id[nid]["node_kind"] = "group" if kids else "leaf"
            by_id[nid]["children"] = kids
        roots = child_of.get(None, [])

        # --- the templates, and whether they LINK to any node --------------
        tpls = [dict(r) for r in conn.execute(
            "SELECT template_id, catalog, subcatalog, item_type, version, "
            "description FROM skill_template "
            "ORDER BY CAST(catalog AS INTEGER), CAST(subcatalog AS INTEGER)")]
        linked = 0
        for t in tpls:
            raw = t["catalog"]
            # A LINK ONLY WHEN THE NUMBER IS A REAL `catalog` ID. MEASURED: it is
            # `10` on every row and no `catalog` row has id 10, so the lookup
            # MISSES — and the old code did the opposite, treating ANY integer as
            # a new node, which is how it invented the group "10".
            node = by_id.get(int(raw)) if str(raw).strip().isdigit() else None
            row = {
                "template_id": t["template_id"],
                "item_type": t["item_type"],
                "version": t["version"],
                "description": t["description"],
                "catalog_id": int(node["catalog_id"]) if node else None,
                # THE LEGACY COLUMNS, MEANING WHAT THEY MEAN. Carried so a reader
                # can see `{root}.{seq}` and re-derive the task id, and named for
                # the task numbering rather than for a shelf.
                "task_root": raw,
                "task_seq": t["subcatalog"],
            }
            if node:
                node["templates"].append(row)
                linked += 1

        return {
            "ok": True,
            "shape": "catalog.parent_id",
            "catalogs": roots,
            "node_count": len(nodes),
            "root_count": len(roots),
            "template_count": len(tpls),
            "linked_templates": linked,
            "unlinked_templates": len(tpls) - linked,
            "note": ("the tree is `catalog.parent_id`; `subcatalog` is a DERIVED "
                     "VIEW and is not read. `skill_template.catalog` is the ROOT "
                     "TASK ID (10), not a `catalog` row id, so a skill template is "
                     "not yet linked to a shelf — see `unlinked_templates`."),
        }
    finally:
        if own:
            conn.close()