"""Scan *.skill.md frontmatter -> field_registry table + hard payload validation.

Extracts every top-level YAML frontmatter key from each skill file, infers its
type, and upserts it into the `field_registry` table (SSOT in coord_store).
Provides validate_task_payload() for hard (non-LLM) validation of task payloads.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from coord_store import _CREATE_field_registry_SQL
from skill_scanner import _FRONTMATTER_RE, _load_meta
from field_validators import validate_phone

DB_PATH = "coords.db"
SKILL_ROOT = Path("./skills")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_table() -> None:
    """Create the field_registry table (SQL SSOT lives in coord_store)."""
    conn = _conn()
    conn.execute(_CREATE_field_registry_SQL)
    conn.commit()
    conn.close()


def extract_frontmatter(md_text: str) -> dict[str, Any] | None:
    """Return the parsed frontmatter mapping, or None if absent/invalid."""
    m = _FRONTMATTER_RE.match(md_text)
    if not m:
        return None
    try:
        data = _load_meta(m.group(1))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def scan_all_skill_fields() -> int:
    """Scan all skills and upsert every frontmatter field into field_registry."""
    init_table()
    count = 0
    conn = _conn()
    try:
        for fp in sorted(SKILL_ROOT.rglob("*.skill.md")):
            raw = fp.read_text(encoding="utf-8")
            fm = extract_frontmatter(raw)
            if not fm or "task_id" not in fm:
                continue
            task_id = str(fm["task_id"]).strip()
            for key, val in fm.items():
                ftype = type(val).__name__
                desc = f"Auto extracted from {fp.name}"
                conn.execute(
                    """
                    INSERT INTO field_registry (task_id, field_name, field_type, required, description)
                    VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(task_id, field_name) DO UPDATE SET
                        field_type = excluded.field_type,
                        required = excluded.required,
                        description = excluded.description,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (task_id, str(key), ftype, desc),
                )
                count += 1
        conn.commit()
    finally:
        conn.close()
    print(f"✅ field_registry scan complete, {count} field rows upserted")
    return count


def validate_task_payload(task_id: str, payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Hard validation against field_registry. No LLM judgement."""
    init_table()
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT field_name, field_type, required FROM field_registry WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()
    errors: list[str] = []
    for r in rows:
        fname, ftype, required = r["field_name"], r["field_type"], r["required"]
        if required and fname not in payload:
            errors.append(f"Missing required field: {fname}")
        elif fname in payload and type(payload[fname]).__name__ != ftype:
            errors.append(
                f"Field {fname} type mismatch, expect {ftype}, got {type(payload[fname]).__name__}"
            )
    # Field dispatch (v1: hardcoded to validate_phone; rule-driven dispatch
    # from field_tdd_rule is deferred until >1 field exists).
    if "phone" in payload:
        err = validate_phone(payload["phone"])
        if err:
            errors.append(f"phone: {err}")
    return len(errors) == 0, errors


if __name__ == "__main__":
    scan_all_skill_fields()
