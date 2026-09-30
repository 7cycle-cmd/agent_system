# -*- coding: utf-8 -*-
"""id_audit.py — REUSABLE: audit every AUTOINCREMENT id in the DB.

WHY THIS EXISTS
---------------
Measured 2026-09-21: `db_table_registry` holds 111 rows at ids **4323..4432**,
with `sqlite_sequence = 4990`. The ids are UNIQUE but not CONTIGUOUS, because
`INTEGER PRIMARY KEY AUTOINCREMENT` keeps a HIGH-WATER MARK in `sqlite_sequence`
and NEVER reuses a value — so a table that is rebuilt (create -> copy -> drop ->
rename) re-inserts every row at the NEXT ids, not at 1.

That is not a uniqueness bug. It is a STABILITY bug: the id is an artefact of
insert history, so a re-seed changes it, and any id derived from it (`T-4323-1`)
silently points somewhere else afterwards.

This module MEASURES that, for every table, so the question "is this id space
healthy?" has a checkable answer instead of an opinion. It is read-only.

WHAT IT REPORTS, PER TABLE
--------------------------
    rows            how many rows exist
    min_id / max_id the live id range
    seq             sqlite_sequence (the high-water mark)
    unique          are the ids unique? (a PK guarantees it; this CONFIRMS it)
    contiguous      are they 1..rows with no gap?
    gap             seq - max_id  (ids consumed and gone)
    verdict         OK | GAPPED | EMPTY | NO_AUTOINCREMENT

A GAPPED table is not broken — it is a table that has been rebuilt or has had
rows deleted. The number is the point: it says HOW MUCH history is missing.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB = BASE / "agent.db"

VERDICT_OK = "OK"
VERDICT_GAPPED = "GAPPED"
VERDICT_EMPTY = "EMPTY"
VERDICT_NO_AUTOINC = "NO_AUTOINCREMENT"


def autoincrement_tables(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every table whose DDL declares `INTEGER PRIMARY KEY AUTOINCREMENT`.

    Read from the SCHEMA, never hand-listed: a hand-written list drifts from the
    DDL and then the audit protects the wrong tables.

    DEFECT FOUND BY RUNNING IT: this used `dict(c)` on a PRAGMA row, which
    requires `conn.row_factory = sqlite3.Row`. A caller that passed a plain
    connection got `TypeError: cannot convert dictionary update sequence`. A
    measurement module must not depend on the CALLER's connection settings, so
    the columns are read POSITIONALLY instead.
    """
    out: list[dict[str, Any]] = []
    for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table' "
                          "ORDER BY name"):
        name, sql = str(r[0]), str(r[1] or "")
        if name.startswith("sqlite_"):
            continue
        if "AUTOINCREMENT" not in sql.upper():
            continue
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt, pk)
        pk_cols = [(str(c[1]), int(c[5] or 0))
                   for c in conn.execute("PRAGMA table_info(%s)" % name)]
        pk = [c for c in pk_cols if c[1]]
        if len(pk) != 1:
            continue
        out.append({"table": name, "pk": pk[0][0]})
    return out


def audit_table(conn: sqlite3.Connection, table: str, pk: str) -> dict[str, Any]:
    """Measure ONE table's id space. Read-only."""
    rows = int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
    seq_row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?",
                           (table,)).fetchone()
    seq = int(seq_row[0]) if seq_row else None
    if rows == 0:
        return {"table": table, "pk": pk, "rows": 0, "min_id": None,
                "max_id": None, "seq": seq, "unique": True, "contiguous": True,
                "gap": None, "verdict": VERDICT_EMPTY}
    ids = [int(r[0]) for r in conn.execute(
        "SELECT %s FROM %s ORDER BY %s" % (pk, table, pk))]
    unique = len(ids) == len(set(ids))
    contiguous = ids == list(range(1, len(ids) + 1))
    gap = (seq - ids[-1]) if seq is not None else None
    return {"table": table, "pk": pk, "rows": rows, "min_id": ids[0],
            "max_id": ids[-1], "seq": seq, "unique": unique,
            "contiguous": contiguous, "gap": gap,
            "verdict": VERDICT_OK if contiguous else VERDICT_GAPPED}


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Audit EVERY autoincrement table. Returns the rows plus a summary."""
    rows = [audit_table(conn, t["table"], t["pk"])
            for t in autoincrement_tables(conn)]
    gapped = [r for r in rows if r["verdict"] == VERDICT_GAPPED]
    dupes = [r for r in rows if not r["unique"]]
    return {
        "tables": rows,
        "total": len(rows),
        "ok": sum(1 for r in rows if r["verdict"] == VERDICT_OK),
        "gapped": len(gapped),
        "empty": sum(1 for r in rows if r["verdict"] == VERDICT_EMPTY),
        "duplicate_ids": len(dupes),
        "gapped_tables": [r["table"] for r in gapped],
        "duplicate_tables": [r["table"] for r in dupes],
        "worst_gap": max((r["gap"] for r in rows if r["gap"]), default=0),
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--gapped-only", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        res = audit(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return
        print("%-34s %-16s %6s %8s %8s %8s %6s %s"
              % ("table", "pk", "rows", "min", "max", "seq", "gap", "verdict"))
        print("-" * 110)
        for r in res["tables"]:
            if args.gapped_only and r["verdict"] != VERDICT_GAPPED:
                continue
            print("%-34s %-16s %6d %8s %8s %8s %6s %s"
                  % (r["table"], r["pk"], r["rows"], r["min_id"], r["max_id"],
                     r["seq"], r["gap"], r["verdict"]))
        print("-" * 110)
        print("tables=%d  OK=%d  GAPPED=%d  EMPTY=%d  duplicate_ids=%d  "
              "worst_gap=%d"
              % (res["total"], res["ok"], res["gapped"], res["empty"],
                 res["duplicate_ids"], res["worst_gap"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()