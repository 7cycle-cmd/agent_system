# -*- coding: utf-8 -*-
"""code_location_registry.py — WHERE a version's code IS, as the EVIDENCE that
                               the version is active.

WHY THIS EXISTS (user, 2026-09-22)
----------------------------------
    "`where` / 檔案路徑 + 行號 / 檔案路徑 + 行號
     version register -> will have this action to have record for all active coding
     so it can help to classify rubbish coding for cleanup
     trigger point for tthis table seems can be proofed evidence for version
     is_active / how do u think"

THE IDEA IS RIGHT, AND IT CONNECTS TO THE EXISTING GATE
------------------------------------------------------
`activation_gate.activate()` requires a `cite_ref` that passes
`citation_discipline.assert_cited`. Today the CALLER supplies it, so it can be
anything. The user's idea: the `where` binding IS the evidence. A version that
claims `is_active=1` must have LOCATABLE code (file path + line). If the code
cannot be located, the version is not proven.

That turns `is_active` from "someone said so" into "there is a location to check".

WHY A LAYER AND NOT A COLUMN ON `version_registry`
--------------------------------------------------
MEASURED: `entity_registry.py:81` `version_registry` has NO `file_path`, and
adding one would VIOLATE the user's own rule 3:

    discriminator  = `entity_type`
    value-specific = `file_path` / `line_start` — they apply only to CODE entity
                     types; a table entity has no file path

`entity_type` splits the table and `file_path` is populated for only some of its
values -> MIXED. `table_design.audit_table` flags it immediately. The proof
asserts that.

Also: one version may have MANY locations (a function spanning files) -> 1:N ->
a separate table is required.

WHAT ALREADY EXISTS (measured, so this module does not duplicate it)
-------------------------------------------------------------------
`db_schema.py:1471` `code_registry` ALREADY has `file_path` + `line_start` +
`line_end` + `code_span`, and `status CHECK (status IN ('draft','active',
'zombie','rubbish','deprecated'))`. So the rubbish classification exists. What is
MISSING is the LINK: `version_registry` keys on `entity_type` + `entity_ref_id`;
`code_registry` keys on `module_name` + `function_name`. No FK between them, and
`code_registry.register_id` is TEXT UNIQUE so per F4 (`entity_id.py:90` does
`int(m.group(2))`) it cannot carry an entity letter.

THIS MODULE REPORTS. IT NEVER DELETES AND NEVER DEACTIVATES.
------------------------------------------------------------
The user's word was "cleanup", and cleanup is a DECISION. So the audit states the
three contradictions and a human decides:

    is_active=1  +  no location          -> unproven, should not be active
    is_active=0  +  a location           -> orphan code, cleanup candidate
    code_registry.status='rubbish'
      + is_active=1                      -> contradiction, a human decides

Run:
    .\\.venv\\Scripts\\python.exe code_location_registry.py --audit
    .\\.venv\\Scripts\\python.exe code_location_registry.py --list
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

CODE_LOCATION_DDL = """
CREATE TABLE IF NOT EXISTS code_location_registry (
    location_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    -- THE JOIN KEY. `version_registry` uses exactly this triple
    -- (`entity_registry.py:81`), so this is the edge that was missing.
    entity_type    TEXT    NOT NULL,
    entity_ref_id  INTEGER NOT NULL,
    version        INTEGER NOT NULL CHECK (version >= 1),
    -- THE `where` BINDING: file path + line.
    file_path      TEXT    NOT NULL,
    line_start     INTEGER NOT NULL DEFAULT 0,
    line_end       INTEGER NOT NULL DEFAULT 0,
    code_span      TEXT    NOT NULL DEFAULT 'NA',
    -- THE EVIDENCE. A location with no citation is a claim, not a location.
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, entity_ref_id, version, file_path, line_start),
    FOREIGN KEY (entity_type) REFERENCES entity_type_registry (type_letter)
);
CREATE INDEX IF NOT EXISTS idx_code_location_entity
  ON code_location_registry (entity_type, entity_ref_id, version);
CREATE INDEX IF NOT EXISTS idx_code_location_path
  ON code_location_registry (file_path, line_start);
"""


class CodeLocationError(ValueError):
    """Raised when a location cannot be registered."""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(CODE_LOCATION_DDL)
    conn.commit()
    return {"ok": True}


def add_location(conn: sqlite3.Connection, entity_type: str, entity_ref_id: int,
                 version: int, file_path: str, *, line_start: int = 0,
                 line_end: int = 0, code_span: str = "NA", cite_ref: str = "",
                 commit: bool = True) -> dict[str, Any]:
    """Register ONE location. Idempotent on the composite key.

    REFUSES an empty `file_path` (a location that points nowhere) and an empty
    `cite_ref` (a location nobody can check).
    """
    letter = str(entity_type or "").strip().upper()
    path = str(file_path or "").strip()
    if not letter:
        return {"ok": False, "code": "MISSING_ENTITY_TYPE",
                "message": "entity_type is required"}
    if not path:
        return {"ok": False, "code": "MISSING_FILE_PATH",
                "message": ("a location with no file path points nowhere: "
                            "%s-%s-%s" % (letter, entity_ref_id, version))}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": ("no citation, no location: %s-%s-%s"
                            % (letter, entity_ref_id, version))}
    try:
        ref = int(entity_ref_id)
        ver = int(version)
        ls = int(line_start)
        le = int(line_end)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_NUMBER",
                "message": "entity_ref_id / version / line_start / line_end "
                           "must be integers"}
    if ver < 1:
        return {"ok": False, "code": "BAD_VERSION",
                "message": "version must be >= 1, got %d" % ver}

    ensure_schema(conn)
    existing = conn.execute(
        "SELECT location_id FROM code_location_registry WHERE entity_type=? "
        "AND entity_ref_id=? AND version=? AND file_path=? AND line_start=?",
        (letter, ref, ver, path, ls)).fetchone()
    if existing:
        return {"ok": True, "location_id": int(existing[0]), "created": False}

    cur = conn.execute(
        "INSERT INTO code_location_registry (entity_type, entity_ref_id, "
        "version, file_path, line_start, line_end, code_span, cite_ref, "
        "is_active) VALUES (?,?,?,?,?,?,?,?,0)",
        (letter, ref, ver, path, ls, le,
         str(code_span or "NA").strip() or "NA", str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "location_id": cur.lastrowid, "created": True,
            "entity_type": letter, "entity_ref_id": ref, "version": ver}


def locations_of(conn: sqlite3.Connection, entity_type: str, entity_ref_id: int,
                 version: int | None = None) -> list[dict[str, Any]]:
    """Every location of a version (1:N — a version may span files)."""
    sql = ("SELECT * FROM code_location_registry WHERE entity_type=? "
           "AND entity_ref_id=?")
    params: list[Any] = [str(entity_type or "").strip().upper(),
                         int(entity_ref_id)]
    if version is not None:
        sql += " AND version=?"
        params.append(int(version))
    sql += " ORDER BY file_path, line_start"
    try:
        return [dict(r) for r in conn.execute(sql, tuple(params))]
    except sqlite3.OperationalError:
        return []


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """The three contradictions. REPORTS ONLY — never deletes, never flips.

    Returns `{unproven, orphan, contradiction, counts}`.
    """
    unproven: list[dict[str, Any]] = []
    orphan: list[dict[str, Any]] = []
    contradiction: list[dict[str, Any]] = []

    # 1. is_active=1 with NO location -> unproven.
    try:
        rows = conn.execute(
            "SELECT v.entity_type, v.entity_ref_id, v.version "
            "FROM version_registry v WHERE v.is_active=1 "
            "AND NOT EXISTS (SELECT 1 FROM code_location_registry c "
            "  WHERE c.entity_type=v.entity_type "
            "  AND c.entity_ref_id=v.entity_ref_id AND c.version=v.version) "
            "ORDER BY v.entity_type, v.entity_ref_id, v.version")
        unproven = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    # 2. is_active=0 with a location -> orphan code.
    try:
        rows = conn.execute(
            "SELECT v.entity_type, v.entity_ref_id, v.version, "
            "  COUNT(c.location_id) AS n_locations "
            "FROM version_registry v JOIN code_location_registry c "
            "  ON c.entity_type=v.entity_type "
            "  AND c.entity_ref_id=v.entity_ref_id AND c.version=v.version "
            "WHERE v.is_active=0 GROUP BY v.entity_type, v.entity_ref_id, "
            "v.version ORDER BY v.entity_type, v.entity_ref_id, v.version")
        orphan = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    # 3. code_registry.status='rubbish' AND the version is active -> contradiction.
    try:
        rows = conn.execute(
            "SELECT c.module_name, c.function_name, c.file_path, c.status, "
            "  v.entity_type, v.entity_ref_id, v.version "
            "FROM code_registry c JOIN version_registry v "
            "  ON v.entity_type='F' AND v.entity_ref_id=c.id "
            "WHERE c.status IN ('rubbish','zombie') AND v.is_active=1 "
            "ORDER BY c.module_name, c.function_name")
        contradiction = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        pass

    return {
        "unproven": unproven,
        "orphan": orphan,
        "contradiction": contradiction,
        "counts": {
            "unproven": len(unproven),
            "orphan": len(orphan),
            "contradiction": len(contradiction),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="code location register")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.audit or not args.list:
            out = audit(conn)
            print("=== code location audit (REPORTS ONLY) ===")
            print("  unproven (is_active=1, no location)      : %d"
                  % out["counts"]["unproven"])
            print("  orphan   (is_active=0, has location)     : %d"
                  % out["counts"]["orphan"])
            print("  contradiction (rubbish/zombie + active)  : %d"
                  % out["counts"]["contradiction"])
            for k in ("unproven", "orphan", "contradiction"):
                for r in out[k][:5]:
                    print("    %s: %s" % (k, dict(r)))
        if args.list:
            for r in conn.execute("SELECT * FROM code_location_registry "
                                  "ORDER BY entity_type, entity_ref_id, "
                                  "version, file_path"):
                print("  %s-%s-%s  %s:%s"
                      % (r["entity_type"], r["entity_ref_id"], r["version"],
                         r["file_path"], r["line_start"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
