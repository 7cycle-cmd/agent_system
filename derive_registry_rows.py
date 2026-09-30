# derive_registry_rows.py
"""Publish what the schema already knows (plan_DERIVE.TABLE.FIELD.REGISTRY).

THE RULE
--------
A missing value is a problem only when it must be AUTHORED. When it is DERIVABLE from an
authoritative source, filling it is PUBLICATION. The authoritative source here is the
database itself: `sqlite_master` names every table, and `PRAGMA table_info` reports every
field's name, declared type, NOT NULL, DEFAULT and PRIMARY KEY. Nothing is invented.

MEASURED 2026-09-24 (the root cause, not just the symptom):
    179 tables in the DB   | 122 registered | **58 MISSING**
    1383 fields across the 122 registered tables | 34 in `db_field_registry` | **1377 MISSING**
    All 58 missing tables are created by files that NEVER register any table --
    including `db_schema.py` itself. So this is not one forgotten call; it is a missing
    discipline, and the derivation is the repair that does not depend on anyone remembering.

WHAT IS **NOT** DERIVABLE, AND IS LEFT ALONE (measured):
    `api_registry.method`/`path` from `api_key`          -> 3 of 43 rows
    `function_registry.code_registry_id` by name match    -> 1 of 33 rows
Both are REPORTED by `not_derivable()` and never filled: guessing them would be the
synthesised-citation defect one table over.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

PROVENANCE_PRAGMA = "pragma:table_info"
PROVENANCE_SQL = "sqlite_master:sql"
BACKUP_SUFFIX = ".bak_derive_registry"

# The two columns this plan adds. They answer DIFFERENT questions and are not a second
# copy of anything:
#   derived_from -- HOW this row was obtained (a short, queryable token)
#   cite_ref     -- WHERE it can be CHECKED (a path:line)
# The plan said "ONE column"; QC-04 requires a checkable citation, and a token cannot also
# be a `path:line`. Recorded as a deliberate two-column decision rather than smuggling a
# citation into a provenance token.
ADDED_COLUMNS = (
    ("derived_from", "TEXT"),
    ("cite_ref", "TEXT"),
)

_CREATE_RE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?", re.I)
_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "dist", "out",
              ".pytest_cache", "site-packages", "chrome_cdp_profile"}


def _creator_index(root: Path | None = None) -> dict[str, tuple[str, int]]:
    """table name -> (file, line) of its CREATE TABLE. DERIVED, not hand-listed."""
    base = root or Path(__file__).resolve().parent
    out: dict[str, tuple[str, int]] = {}
    for p in sorted(base.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "CREATE TABLE" not in text.upper():
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for m in _CREATE_RE.finditer(line):
                name = m.group(1)
                out.setdefault(name, (p.relative_to(base).as_posix(), i))
    return out


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _fields(conn: sqlite3.Connection, table: str) -> list[dict]:
    return [{"name": r[1], "type": (r[2] or "NA").strip() or "NA",
             "notnull": int(r[3] or 0), "default": r[4], "pk": int(r[5] or 0)}
            for r in conn.execute('PRAGMA table_info("%s")' % table)]


def derive_tables(conn: sqlite3.Connection, *, root: Path | None = None) -> list[dict]:
    """The tables present in the DB but ABSENT from `db_table_registry`."""
    have = {r[0] for r in conn.execute("SELECT table_key FROM db_table_registry")}
    creators = _creator_index(root)
    out = []
    for t in _tables(conn):
        if t in have:
            continue
        f, ln = creators.get(t, ("NA", 0))
        out.append({
            "table_key": t,
            "name": t,
            # The EXISTING convention is prose or a short tag. Here the value is DERIVED:
            # the DDL location, which is what is actually known about a bare table.
            "description": "derived from sqlite_master; DDL at %s:%d" % (f, ln)
            if ln else "derived from sqlite_master; DDL location not located",
            "derived_from": PROVENANCE_SQL,
            "cite_ref": ("%s:%d" % (f, ln)) if ln else "NA",
        })
    return out


def derive_fields(conn: sqlite3.Connection) -> list[dict]:
    """The fields present in REGISTERED tables but absent from `db_field_registry`."""
    have = {(r[0], r[1]) for r in conn.execute(
        "SELECT db_table_id, field_key FROM db_field_registry")}
    out = []
    for row in conn.execute("SELECT db_table_id, table_key FROM db_table_registry"):
        tid, tkey = int(row[0]), row[1]
        for f in _fields(conn, tkey):
            if (tid, f["name"]) in have:
                continue
            # EVERY token comes from PRAGMA. The `table.field` prefix keeps the register's
            # existing convention; the rest is the declared shape, which is a FACT.
            desc = "%s.%s | type=%s | notnull=%d | default=%s | pk=%d" % (
                tkey, f["name"], f["type"], f["notnull"],
                "NA" if f["default"] is None else f["default"], f["pk"])
            out.append({"db_table_id": tid, "field_key": f["name"], "name": f["name"],
                        "description": desc, "derived_from": PROVENANCE_PRAGMA,
                        "cite_ref": "NA"})
    return out


def not_derivable(conn: sqlite3.Connection) -> list[dict]:
    """Gaps with NO authoritative source. REPORTED, never filled."""
    out = []
    # api_registry.method/path from api_key
    total = conn.execute("SELECT COUNT(*) FROM api_registry").fetchone()[0]
    ok = 0
    for r in conn.execute("SELECT api_key, method, path FROM api_registry"):
        k = (r["api_key"] or "").strip()
        parts = k.split(None, 1)
        der = (parts[0], parts[1]) if len(parts) == 2 else (None, k)
        if (r["method"], r["path"]) == der:
            ok += 1
    out.append({"field": "api_registry.method/path", "derivable": ok, "rows": total,
                "reason": "only a key that literally begins '<METHOD> <path>' yields both"})
    # function_registry.code_registry_id
    tot2 = conn.execute("SELECT COUNT(*) FROM function_registry").fetchone()[0]
    ok2 = conn.execute("SELECT COUNT(*) FROM function_registry f JOIN code_registry c "
                       "ON c.function_name = f.function_key").fetchone()[0]
    out.append({"field": "function_registry.code_registry_id", "derivable": ok2,
                "rows": tot2, "reason": "code_registry.function_name matches few keys"})
    return out


def plan(conn: sqlite3.Connection, *, root: Path | None = None) -> dict:
    """READ-ONLY summary. Writes NOTHING (QC-01 asserts this structurally)."""
    t = derive_tables(conn, root=root)
    f = derive_fields(conn)
    return {
        "tables_present": len(_tables(conn)),
        "tables_registered": conn.execute("SELECT COUNT(*) FROM db_table_registry").fetchone()[0],
        "tables_derivable": len(t),
        "fields_registered": conn.execute("SELECT COUNT(*) FROM db_field_registry").fetchone()[0],
        "fields_derivable": len(f),
        "not_derivable": not_derivable(conn),
        "table_rows": t,
        "field_rows": f,
    }


def apply(conn: sqlite3.Connection, *, propose: bool = False, root: Path | None = None,
          commit: bool = True) -> dict:
    """Publish the derived rows. `propose=True` writes NOTHING.

    Idempotent by natural key -- tables by `table_key`, fields by `(db_table_id, field_key)`
    -- so a second run inserts 0.
    """
    t = derive_tables(conn, root=root)
    f = derive_fields(conn)
    res = {"proposed_tables": len(t), "proposed_fields": len(f),
           "inserted_tables": 0, "inserted_fields": 0}
    if propose:
        return res

    cols_t = {r[1] for r in conn.execute("PRAGMA table_info(db_table_registry)")}
    cols_f = {r[1] for r in conn.execute("PRAGMA table_info(db_field_registry)")}

    for r in t:
        cur = conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version%s) VALUES (?,?,?,1,'1'%s)"
            % (", derived_from, cite_ref" if "derived_from" in cols_t else "",
               ", ?, ?" if "derived_from" in cols_t else ""),
            (r["table_key"], r["name"], r["description"], r["derived_from"], r["cite_ref"])
            if "derived_from" in cols_t else (r["table_key"], r["name"], r["description"]))
        res["inserted_tables"] += cur.rowcount

    for r in f:
        if "derived_from" in cols_f:
            cur = conn.execute(
                "INSERT OR IGNORE INTO db_field_registry "
                "(field_key, name, description, db_table_id, is_active, version, "
                " derived_from, cite_ref) VALUES (?,?,?,?,1,'1',?,?)",
                (r["field_key"], r["name"], r["description"], r["db_table_id"],
                 r["derived_from"], r["cite_ref"]))
        else:
            cur = conn.execute(
                "INSERT OR IGNORE INTO db_field_registry "
                "(field_key, name, description, db_table_id, is_active, version) "
                "VALUES (?,?,?,?,1,'1')",
                (r["field_key"], r["name"], r["description"], r["db_table_id"]))
        res["inserted_fields"] += cur.rowcount

    if commit:
        conn.commit()
    return res


def unattributed(conn: sqlite3.Connection) -> dict:
    """Rows with NO provenance. A derived row indistinguishable from an authored one is a
    second-class fact masquerading as a first-class one, so this is checked, not assumed."""
    out = {}
    for t in ("db_table_registry", "db_field_registry"):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}
        if "derived_from" not in cols:
            out[t] = None
            continue
        out[t] = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE derived_from IS NULL OR derived_from=''" % t
        ).fetchone()[0]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="derive registry rows from the schema")
    ap.add_argument("--plan", action="store_true", help="read-only summary")
    ap.add_argument("--apply", action="store_true", help="publish the derived rows")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    import db_schema
    db = args.db or db_schema.get_db_path()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    print("db: %s" % db)

    if args.plan or not args.apply:
        p = plan(conn)
        print("  tables  present=%-5d registered=%-5d DERIVABLE=%d" % (
            p["tables_present"], p["tables_registered"], p["tables_derivable"]))
        print("  fields  registered=%-5d DERIVABLE=%d" % (
            p["fields_registered"], p["fields_derivable"]))
        print("  NOT derivable (reported, never filled):")
        for r in p["not_derivable"]:
            print("    %-38s %d/%d  (%s)" % (r["field"], r["derivable"], r["rows"], r["reason"]))
        print("  sample table rows:")
        for r in p["table_rows"][:4]:
            print("    %s" % {k: str(v)[:56] for k, v in r.items()})
        print("  sample field rows:")
        for r in p["field_rows"][:3]:
            print("    %s" % {k: str(v)[:56] for k, v in r.items()})
        conn.close()
        return 0

    backup = db + BACKUP_SUFFIX
    if not os.path.exists(backup):
        print("  backing up -> %s" % backup)
        shutil.copy2(db, backup)
    res = apply(conn)
    print("  APPLIED tables=%d fields=%d (proposed %d/%d)" % (
        res["inserted_tables"], res["inserted_fields"],
        res["proposed_tables"], res["proposed_fields"]))
    print("  unattributed rows: %s" % unattributed(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())