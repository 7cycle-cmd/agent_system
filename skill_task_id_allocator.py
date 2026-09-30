"""Versioned Task ID Allocator Skill.

Reusable rule engine that looks up / allocates versioned task IDs for many
agents/workers. ID format: {catalog}.{subcatalog}-T-{version}.

Rules:
- Catalog initial pool 1..10 (fixed). If a task name is NOT found in 1..10,
  allocate a new catalog id starting at 11.
- Same task update -> increment version number (catalog/subcatalog unchanged).
- Hierarchy: catalog > subcatalog > task(version).

Error handling:
- Empty / non-string task_name -> EMPTY_TASK_NAME
- Non-positive-integer catalog/subcatalog/version -> INVALID_ID / INVALID_VERSION
- Duplicate task name inside catalog 1..10 -> DUPLICATE_TASK (human review)
- Invalid input (not a dict) -> INVALID_INPUT

Persistence: SQLite table skill_task_id_registry (idempotent create).
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import skill_prompt

DEFAULT_DB = skill_prompt.DEFAULT_DB
INITIAL_CATALOG_MAX = 10
TASK_MARKER = "T"

REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS skill_task_id_registry (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name      TEXT    NOT NULL,
    task_name_norm TEXT    NOT NULL,
    catalog_id     INTEGER NOT NULL,
    subcatalog_id  INTEGER NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    task_full_id   TEXT    NOT NULL UNIQUE,
    status         TEXT    NOT NULL DEFAULT 'active',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_id_registry_name
  ON skill_task_id_registry (task_name_norm);
CREATE INDEX IF NOT EXISTS idx_task_id_registry_catalog
  ON skill_task_id_registry (catalog_id, subcatalog_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = skill_prompt._connect(db_path)
    skill_prompt.ensure_skill_tables(conn)
    conn.executescript(REGISTRY_DDL)
    return conn


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).lower()


def _err(code: str, message: str) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message}


def _validate_input(data: Any) -> dict[str, Any] | None:
    """Return an error dict if input is invalid, else None."""
    if not isinstance(data, dict):
        return _err("INVALID_INPUT", "input must be a JSON object")
    name = data.get("task_name")
    if name is None or not isinstance(name, str) or not name.strip():
        return _err("EMPTY_TASK_NAME", "task_name is required and must be a non-empty string")
    return None


def _validate_positive_int(value: Any, field: str) -> dict[str, Any] | None:
    if value is None:
        return None  # optional
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return _err("INVALID_ID", f"{field} must be a positive integer")
    return None


def allocate_task_id(
    task_name: str,
    *,
    catalog_id: int | None = None,
    subcatalog_id: int | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Allocate or look up a versioned task ID for ``task_name``.

    Returns JSON per the skill spec:
    {task_name, catalog_id, subcatalog_id, task_full_id, version, status, note}
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        name = (task_name or "").strip()
        if not name:
            return _err("EMPTY_TASK_NAME", "task_name is required")
        norm = _norm_name(name)

        # 1) Search ALL catalogs for an existing task (once allocated, later
        #    calls are updates -> version bump). Catalog 1..10 is the initial
        #    pool; a task found there is reused, otherwise a new catalog is
        #    allocated (11+). Duplicate rows inside catalog 1..10 -> review.
        existing = conn.execute(
            """
            SELECT task_name, catalog_id, subcatalog_id, version, task_full_id
            FROM skill_task_id_registry
            WHERE task_name_norm = ?
            ORDER BY catalog_id, subcatalog_id, version DESC
            """,
            (norm,),
        ).fetchall()

        if existing:
            # Duplicate task name inside catalog 1..10 -> human review.
            in_initial = [r for r in existing if int(r["catalog_id"]) <= INITIAL_CATALOG_MAX]
            if len(in_initial) > 1:
                return _err(
                    "DUPLICATE_TASK",
                    f"task '{name}' exists multiple times in catalog 1..{INITIAL_CATALOG_MAX}: "
                    + ", ".join(r["task_full_id"] for r in in_initial),
                )
            row = existing[0]
            # Same task update -> bump version (catalog/subcatalog unchanged).
            new_version = int(row["version"]) + 1
            full_id = f"{row['catalog_id']}.{row['subcatalog_id']}-T-{new_version}"
            conn.execute(
                """
                UPDATE skill_task_id_registry
                SET version = ?, task_full_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE task_name_norm = ? AND catalog_id = ? AND subcatalog_id = ?
                """,
                (new_version, full_id, norm, row["catalog_id"], row["subcatalog_id"]),
            )
            if own:
                conn.commit()
            return {
                "task_name": name,
                "catalog_id": row["catalog_id"],
                "subcatalog_id": row["subcatalog_id"],
                "task_full_id": full_id,
                "version": new_version,
                "status": "existing_match",
                "note": f"task exists in catalog {row['catalog_id']}; version bumped to {new_version}",
            }

        # 2) Not found -> allocate. If a catalog >10 already exists, reuse the
        #    highest one (new subcatalog); otherwise allocate a new catalog id
        #    starting at 11.
        if catalog_id is not None:
            err = _validate_positive_int(catalog_id, "catalog_id")
            if err:
                return err
            cat = int(catalog_id)
        else:
            row = conn.execute(
                "SELECT MAX(catalog_id) AS m FROM skill_task_id_registry"
            ).fetchone()
            cur_max = int(row["m"]) if row and row["m"] is not None else 0
            if cur_max > INITIAL_CATALOG_MAX:
                cat = cur_max  # reuse highest catalog, new subcatalog
            else:
                cat = INITIAL_CATALOG_MAX + 1  # first new catalog = 11

        if subcatalog_id is not None:
            err = _validate_positive_int(subcatalog_id, "subcatalog_id")
            if err:
                return err
            sub = int(subcatalog_id)
        else:
            row = conn.execute(
                "SELECT MAX(subcatalog_id) AS m FROM skill_task_id_registry WHERE catalog_id = ?",
                (cat,),
            ).fetchone()
            sub = (int(row["m"]) + 1) if row and row["m"] is not None else 1

        full_id = f"{cat}.{sub}-T-1"
        conn.execute(
            """
            INSERT INTO skill_task_id_registry (
                task_name, task_name_norm, catalog_id, subcatalog_id,
                version, task_full_id, status
            ) VALUES (?, ?, ?, ?, 1, ?, 'active')
            """,
            (name, norm, cat, sub, full_id),
        )
        if own:
            conn.commit()
        return {
            "task_name": name,
            "catalog_id": cat,
            "subcatalog_id": sub,
            "task_full_id": full_id,
            "version": 1,
            "status": "new_allocated",
            "note": f"task not found in catalog 1..{INITIAL_CATALOG_MAX}; allocated new catalog {cat}",
        }
    except sqlite3.IntegrityError as e:
        return _err("DUPLICATE_TASK", f"duplicate task_full_id: {e}")
    except Exception as e:
        return _err("INTERNAL_ERROR", f"{type(e).__name__}: {e}")
    finally:
        if own:
            conn.close()


def lookup_task_id(
    task_name: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Look up the latest version of a task without bumping version."""
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        name = (task_name or "").strip()
        if not name:
            return _err("EMPTY_TASK_NAME", "task_name is required")
        norm = _norm_name(name)
        row = conn.execute(
            """
            SELECT task_name, catalog_id, subcatalog_id, version, task_full_id
            FROM skill_task_id_registry
            WHERE task_name_norm = ?
            ORDER BY version DESC LIMIT 1
            """,
            (norm,),
        ).fetchone()
        if not row:
            return {
                "task_name": name,
                "catalog_id": None,
                "subcatalog_id": None,
                "task_full_id": None,
                "version": None,
                "status": "not_found",
                "note": "task not registered yet; call allocate to create",
            }
        return {
            "task_name": row["task_name"],
            "catalog_id": row["catalog_id"],
            "subcatalog_id": row["subcatalog_id"],
            "task_full_id": row["task_full_id"],
            "version": row["version"],
            "status": "existing_match",
            "note": "latest version lookup (no version bump)",
        }
    finally:
        if own:
            conn.close()


def list_registry(
    *,
    catalog_id: int | None = None,
    limit: int = 200,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM skill_task_id_registry WHERE 1=1"
        args: list[Any] = []
        if catalog_id is not None:
            sql += " AND catalog_id=?"
            args.append(int(catalog_id))
        sql += " ORDER BY catalog_id, subcatalog_id, version DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def overview(*, db_path: Path | str | None = None) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        def _count(sql: str) -> int:
            row = conn.execute(sql).fetchone()
            return int(row[0]) if row else 0

        return {
            "ok": True,
            "total": _count("SELECT COUNT(*) FROM skill_task_id_registry"),
            "catalogs": _count("SELECT COUNT(DISTINCT catalog_id) FROM skill_task_id_registry"),
            "max_catalog": _count("SELECT COALESCE(MAX(catalog_id),0) FROM skill_task_id_registry"),
        }
    finally:
        conn.close()