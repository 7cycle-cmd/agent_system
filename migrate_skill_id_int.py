# -*- coding: utf-8 -*-
"""migrate_skill_id_int.py — PHASE 3: the tables whose `skill_id` is DECLARED TEXT.

WHY ADDITIVE, NOT A REBUILD
---------------------------
SQLite cannot change a column's declared affinity. Making `skill_id` INTEGER
means REBUILDING the table (create new, copy, drop, rename) — on `llm_100_run`
that is 2615 rows, and a rebuild is the operation most likely to lose data.

So this migration does the part that makes JOINS correct and is REVERSIBLE:
`ADD COLUMN skill_ref INTEGER` + backfill. The affinity rebuild is a separate,
later step, and it is only worth doing once every reader uses `skill_ref`.

THE MISNOMER THIS MIGRATION REFUSES TO "FIX"
--------------------------------------------
`skill_task_queue.skill_id` holds `SKILL.QUEUE.SMOKE` / `SKILL.ENV.TASK.PROOF` —
those are the QUEUE's own TASK ids. Measured: 0 of 2 resolve to a skill. Adding a
`skill_ref` there would be a column that can NEVER be non-NULL, i.e. a slot filled
with nothing — the exact defect this workset removes. So the table is REPORTED as
a misnomer and LEFT ALONE. Renaming the column is a decision, not a migration.

Run:  .\\.venv\\Scripts\\python.exe migrate_skill_id_int.py [--apply]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"
MIGRATION = "skill_id_int_phase3_v1"

# A table whose TEXT `skill_id` resolves to NO skill at all is NOT a skill
# reference. Adding `skill_ref` there would create a permanently-NULL column.
MIN_RESOLVABLE = 1


def _text_skill_id_tables(conn) -> list[str]:
    out = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "ORDER BY name"):
        t = r[0]
        if t.startswith("sqlite_"):
            continue
        for c in conn.execute("PRAGMA table_info(%s)" % t):
            if c[1] == "skill_id" and "INT" not in (c[2] or "").upper():
                out.append(t)
                break
    return out


def _has_col(conn, t: str, col: str) -> bool:
    return col in [c[1] for c in conn.execute("PRAGMA table_info(%s)" % t)]


def measure(conn) -> list[dict[str, Any]]:
    reg = {str(r[0]) for r in conn.execute("SELECT skill_key FROM skill_registry")}
    rows = []
    for t in _text_skill_id_tables(conn):
        vals = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT skill_id FROM %s" % t) if r[0]}
        resolved = sorted(v for v in vals if v in reg)
        unresolved = sorted(v for v in vals if v not in reg)
        rows.append({
            "table": t,
            "rows": conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0],
            "distinct": len(vals),
            "resolvable": len(resolved),
            "unresolved": unresolved,
            "is_a_skill_reference": len(resolved) >= MIN_RESOLVABLE,
            "has_skill_ref": _has_col(conn, t, "skill_ref"),
        })
    return rows


def plan(conn) -> dict[str, Any]:
    rows = measure(conn)
    return {
        "migration": MIGRATION,
        "tables": rows,
        "will_migrate": [r["table"] for r in rows if r["is_a_skill_reference"]],
        "reported_as_misnomer": [r["table"] for r in rows
                                 if not r["is_a_skill_reference"]],
        "note": ("A table whose skill_id resolves to NO skill is NOT a skill "
                 "reference. It is REPORTED, not migrated: a skill_ref column "
                 "there could never be non-NULL."),
    }


def apply(conn) -> dict[str, Any]:
    out: dict[str, Any] = {"migration": MIGRATION, "migrated": [],
                           "reported_as_misnomer": []}
    for r in measure(conn):
        t = r["table"]
        if not r["is_a_skill_reference"]:
            out["reported_as_misnomer"].append(
                {"table": t, "values": r["unresolved"][:4],
                 "why": "skill_id holds values that are NOT skills; adding "
                        "skill_ref would create a permanently-NULL column"})
            continue
        added = False
        if not _has_col(conn, t, "skill_ref"):
            conn.execute("ALTER TABLE %s ADD COLUMN skill_ref INTEGER" % t)
            added = True
        conn.execute(
            "UPDATE %s SET skill_ref = (SELECT s.skill_id FROM skill_registry s "
            "WHERE s.skill_key = %s.skill_id)" % (t, t))
        total = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        linked = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_ref IS NOT NULL" % t
        ).fetchone()[0]
        bad_a = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_id IN (SELECT skill_key FROM "
            "skill_registry) AND skill_ref IS NULL" % t).fetchone()[0]
        bad_b = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_id NOT IN (SELECT skill_key "
            "FROM skill_registry) AND skill_ref IS NOT NULL" % t).fetchone()[0]
        if bad_a or bad_b:
            raise RuntimeError("%s: inconsistent (%d, %d)" % (t, bad_a, bad_b))
        out["migrated"].append({"table": t, "column_added": added,
                                "rows": total, "linked": linked,
                                "null": total - linked})
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration_log (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, migration TEXT NOT NULL, detail_json TEXT NOT NULL "
        "DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute("INSERT INTO schema_migration_log (migration, detail_json) "
                 "VALUES (?,?)", (MIGRATION, json.dumps(out, sort_keys=True)))
    conn.commit()
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            print(json.dumps(apply(conn), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(plan(conn), indent=2, ensure_ascii=False))
            print("\n(dry run — pass --apply to migrate)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())