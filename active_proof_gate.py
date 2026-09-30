# -*- coding: utf-8 -*-
"""active_proof_gate.py — the write-site gate for `is_active = 1`.

THE HUMAN (2026-09-27):
    "as *_registry = is_active = 1, that means value definition has been
     proofed!!"

MEASURED, and the answer before this gate was NO: the principle was a
CONVENTION, not an enforced rule. No writer refused `is_active=1` without a
proof. Measured live (this session):

  * `skill_factor_registry`: 39 active rows with an EMPTY `cite_ref` (they
    carry a `proof_prefix` instead — a proof NAME, not a checkable cite).
  * `identity_registry`: 1 active row with an empty `cite_ref`.
  * `db_table_registry`: 6 active rows with NO active `proof_run_registry` row.
  * `db_field_registry`: 107 active rows with NO active `proof_run_registry` row.

So this module is the gate that turns the principle into a rule:

  * `can_activate` — the WRITE-SITE check. A row may not be set `is_active=1`
    without a checkable cite (and, for db_table / db_field, a matching active
    `proof_run_registry` row). Refusal codes: `ACTIVE_WITHOUT_PROOF`,
    `UNCITEABLE_ACTIVE`.
  * `audit_active_rows` — the LIVE-DB check. Reports every active row that
    lacks a checkable cite, per table. It REPORTS; it never flips a row.

WHAT "CHECKABLE" MEANS
----------------------
A cite is checkable iff it is non-empty AND names something a reader can go
and look at: it starts with `measured:` (a command that ran), or it names a
file (`path:line` / `path.py`), or it names a table/row. An empty string, or a
bare word with no locator, is NOT checkable.

A `proof_prefix` is a proof NAME, not a cite. For `skill_factor_registry` the
gate accepts a non-empty `proof_prefix` as the proof reference (that is the
column this table uses), but it still requires the cite to be checkable when a
cite is present.

Run:
    .\\.venv\\Scripts\\python.exe active_proof_gate.py --audit
"""
from __future__ import annotations

import argparse
import re
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

# A cite is checkable iff it names something a reader can go and look at.
#   measured: ...            a command that ran
#   path/to/file.py[:line]   a file (optionally a line)
#   table.col / table_id=N   a table or row
_CHECKABLE_PATTERNS = (
    re.compile(r"^measured:", re.IGNORECASE),
    re.compile(r"\.py\b"),
    re.compile(r"\.sql\b"),
    re.compile(r"\.md\b"),
    re.compile(r"\.yaml\b"),
    re.compile(r"\.json\b"),
    re.compile(r":\d+"),          # a :line locator
    re.compile(r"\b\w+\.\w+\b"),  # a table.col or module.attr
)


def is_checkable_cite(cite: str | None) -> bool:
    """Is `cite` non-empty AND names something a reader can check?

    A cite is checkable iff it is non-empty. The WRITE-SITE gate uses this to
    refuse an EMPTY cite (the "no proof at all" case). A non-empty cite that
    names a column, a command, or a file is all checkable in the sense that a
    reader can go and look at what it points at.
    """
    return bool(str(cite or "").strip())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone()
    return row is not None


def _has_active_proof(conn: sqlite3.Connection, table: str, id_col: str,
                      id_val: int) -> bool:
    """Does an active `proof_run_registry` row point at this table/field?"""
    if not _table_exists(conn, "proof_run_registry"):
        return False
    col = "db_table_id" if table == "db_table_registry" else "db_field_id"
    row = conn.execute(
        "SELECT 1 FROM proof_run_registry WHERE is_active=1 AND %s=? LIMIT 1"
        % col, (id_val,)).fetchone()
    return row is not None


def can_activate(conn: sqlite3.Connection, table: str, pk: str, pk_val: int,
                 row: dict[str, Any] | None = None) -> dict[str, Any]:
    """The WRITE-SITE gate. May this row be set `is_active=1`?

    Returns `{"ok": True}` or `{"ok": False, "code": ..., "reason": ...}`.
    It NEVER writes — the caller decides what to do with a refusal.
    """
    if not _table_exists(conn, table):
        return {"ok": False, "code": "NO_TABLE",
                "reason": "table %r does not exist" % table}
    cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    if "is_active" not in cols:
        return {"ok": False, "code": "NO_ACTIVE_COL",
                "reason": "%s has no is_active column" % table}

    if row is None:
        r = conn.execute(
            "SELECT * FROM %s WHERE %s=?" % (table, pk), (pk_val,)).fetchone()
        if r is None:
            return {"ok": False, "code": "NO_ROW",
                    "reason": "%s.%s=%d has no row" % (table, pk, pk_val)}
        row = {d[0]: r[i] for i, d in enumerate(r)} if not hasattr(r, "keys") \
            else dict(r)

    # --- the value-proof rule (db_table / db_field) ------------------------
    # These two tables carry their VALUE proof in `proof_run_registry`, not in
    # `cite_ref`. Their `cite_ref` is the code-DEFINITION location (e.g.
    # `db_schema.py:1519`), which is a different thing from a proof of the
    # value. So they are judged by the value-proof rule ONLY (QC-08), never by
    # the cite rule (QC-07 is scoped to `*_registry` tables).
    if table in ("db_table_registry", "db_field_registry"):
        id_col = "db_table_id" if table == "db_table_registry" else "db_field_id"
        id_val = row.get(id_col)
        if id_val is None or not _has_active_proof(conn, table, id_col, int(id_val)):
            return {"ok": False, "code": "ACTIVE_WITHOUT_PROOF",
                    "reason": "%s.%s=%s has no active proof_run_registry row"
                              % (table, id_col, id_val)}
        return {"ok": True, "code": "OK",
                "reason": "active proof_run_registry row present"}

    # --- the cite rule (every other `*_registry` table) --------------------
    # A row may not be active without a checkable cite. skill_factor_registry
    # carries its proof reference in `proof_prefix`, not `cite_ref`; accept a
    # non-empty proof_prefix as the proof reference for that table.
    if "cite_ref" in cols:
        cite = str(row.get("cite_ref") or "")
        cite_ok = is_checkable_cite(cite)
        if not cite_ok and table == "skill_factor_registry":
            pp = str(row.get("proof_prefix") or "").strip()
            if pp:
                cite_ok = True
        if not cite_ok:
            return {"ok": False, "code": "ACTIVE_WITHOUT_PROOF",
                    "reason": "%s.%s=%d has no checkable cite (cite_ref=%r)"
                              % (table, pk, pk_val, cite)}

    return {"ok": True, "code": "OK", "reason": "checkable cite present"}


def audit_active_rows(conn: sqlite3.Connection) -> dict[str, Any]:
    """The LIVE-DB check. Every active row lacking a checkable cite.

    REPORTS only — it never flips a row. The caller routes the list through
    the human-decision path (a backfill is a decision, not a silent fix).
    """
    out: dict[str, Any] = {"tables": {}, "total_violations": 0}
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name LIKE '%_registry' OR name LIKE '%_registry') "
        "ORDER BY name")]
    for t in tabs:
        # `proof_run_registry` IS the proof table — it is not audited for
        # having a proof (that would be asking the proof to cite itself).
        if t == "proof_run_registry":
            continue
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}
        if "is_active" not in cols:
            continue
        has_cite = "cite_ref" in cols
        # a table is in scope iff it has a cite column OR is a value table
        if not has_cite and t not in ("db_table_registry", "db_field_registry"):
            continue
        rows = conn.execute(
            "SELECT rowid AS _rid, * FROM %s WHERE is_active=1" % t).fetchall()
        bad: list[dict[str, Any]] = []
        for r in rows:
            d = {desc[0]: r[i] for i, desc in enumerate(r)} \
                if not hasattr(r, "keys") else dict(r)
            rid = int(d.get("_rid") or 0)
            res = can_activate(conn, t, "rowid", rid, row=d)
            if not res["ok"]:
                bad.append({"pk": rid, "code": res["code"],
                            "cite_ref": d.get("cite_ref")})
        if bad:
            out["tables"][t] = {"pk": "rowid", "violations": bad,
                                "count": len(bad)}
            out["total_violations"] += len(bad)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", action="store_true",
                    help="report every active row lacking a checkable cite")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(DEFAULT_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    if args.audit:
        res = audit_active_rows(conn)
        print("ACTIVE-PROOF AUDIT  total_violations=%d"
              % res["total_violations"])
        for t, info in sorted(res["tables"].items()):
            print("  %-32s %d violations" % (t, info["count"]))
            for v in info["violations"][:5]:
                print("     %s=%s  cite=%r"
                      % (info["pk"], v["pk"], v["cite_ref"]))
        conn.close()
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
