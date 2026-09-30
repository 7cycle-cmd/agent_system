# -*- coding: utf-8 -*-
"""migrate_skill_ref.py — PHASE 4: give every `skill_key` table an INTEGER
`skill_ref`, so a join stops depending on a NAME.

WHY ADDITIVE, AND WHY THE NAME STAYS
------------------------------------
`skill_key` is a LABEL. A label is fine to READ and wrong to JOIN on: it can be
misspelled, it can be a slug, and it can be renamed without anything noticing.
`skill_ref INTEGER` is the identity.

The migration is ADDITIVE and REVERSIBLE on purpose:
  * `ADD COLUMN skill_ref INTEGER` — no table rebuild, no data moved
  * `skill_key` is KEPT, so every existing reader keeps working
  * a value that resolves to NO skill is left **NULL**, which is the honest state
    for an unresolved name and is exactly what an INTEGER column can say and a
    slug string cannot

A NULL is NOT a failure here. Measured: `skill_mismatch_log` holds
`SKILL.QUEUE.SMOKE`, which is a QUEUE TASK id, not a skill. Resolving it to a
skill would be WRONG, so it stays NULL and is REPORTED.

Run:  .\\.venv\\Scripts\\python.exe migrate_skill_ref.py [--apply]
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
MIGRATION = "skill_ref_phase4_v1"

# Tables that carry a `skill_key` and are NOT the skill table itself.
# `component_registry` and `skill_contract_template` already have `skill_ref`.
SKIP = {"skill_registry", "component_registry", "skill_contract_template"}


def _tables_with_skill_key(conn) -> list[str]:
    out = []
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                          "ORDER BY name"):
        t = r[0]
        if t.startswith("sqlite_") or t in SKIP:
            continue
        cols = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % t)]
        if "skill_key" in cols:
            out.append(t)
    return out


def _has_col(conn, t: str, col: str) -> bool:
    return col in [c[1] for c in conn.execute("PRAGMA table_info(%s)" % t)]


def plan(conn) -> dict[str, Any]:
    """What WOULD change. Read-only."""
    reg = {str(r[0]): int(r[1]) for r in conn.execute(
        "SELECT skill_key, skill_id FROM skill_registry")}
    rows = []
    for t in _tables_with_skill_key(conn):
        vals = {str(r[0]) for r in conn.execute(
            "SELECT DISTINCT skill_key FROM %s" % t) if r[0]}
        resolved = sorted(v for v in vals if v in reg)
        unresolved = sorted(v for v in vals if v not in reg)
        rows.append({
            "table": t,
            "has_skill_ref": _has_col(conn, t, "skill_ref"),
            "rows": conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0],
            "distinct_keys": len(vals),
            "resolvable": len(resolved),
            "unresolved": unresolved,
        })
    return {"migration": MIGRATION, "skill_registry_rows": len(reg),
            "tables": rows,
            "tables_to_change": sum(1 for r in rows if not r["has_skill_ref"])}


def apply(conn) -> dict[str, Any]:
    """ADD COLUMN + backfill, per table. Idempotent."""
    reg = {str(r[0]): int(r[1]) for r in conn.execute(
        "SELECT skill_key, skill_id FROM skill_registry")}
    out: dict[str, Any] = {"migration": MIGRATION, "tables": []}
    for t in _tables_with_skill_key(conn):
        added = False
        if not _has_col(conn, t, "skill_ref"):
            conn.execute("ALTER TABLE %s ADD COLUMN skill_ref INTEGER" % t)
            added = True
        # Backfill EVERY row, so a row whose key changed is corrected rather than
        # left stale. A row with no resolvable key is set to NULL explicitly.
        cur = conn.execute(
            "UPDATE %s SET skill_ref = (SELECT s.skill_id FROM skill_registry s "
            "WHERE s.skill_key = %s.skill_key)" % (t, t))
        touched = int(cur.rowcount or 0)
        linked = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_ref IS NOT NULL" % t
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        # BOTH directions, per table.
        #
        # DEFECT FOUND BY RUNNING IT (2026-09-22): `bad_a` used
        # `skill_key IN (SELECT ...)` with NO `IS NOT NULL`. When `skill_key` is
        # NULL, `NULL IN (...)` is NULL — NOT false — so the row was EXCLUDED and
        # a table whose rows have NO key at all reported 0 violations. The same
        # three-valued-logic defect `_proof_skill_ref_migration.py` recorded.
        bad_a = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_key IS NOT NULL AND skill_key "
            "IN (SELECT skill_key FROM skill_registry) AND skill_ref IS NULL"
            % t).fetchone()[0]
        bad_b = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_key IS NOT NULL AND skill_key "
            "NOT IN (SELECT skill_key FROM skill_registry) AND skill_ref IS NOT "
            "NULL" % t).fetchone()[0]
        if bad_a or bad_b:
            raise RuntimeError(
                "%s: link inconsistent (%d resolvable-but-NULL, %d "
                "unresolvable-but-set)" % (t, bad_a, bad_b))
        out["tables"].append({"table": t, "column_added": added,
                              "rows": total, "linked": linked,
                              "null": total - linked, "updated": touched})
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration_log (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, migration TEXT NOT NULL, detail_json TEXT NOT NULL "
        "DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute("INSERT INTO schema_migration_log (migration, detail_json) "
                 "VALUES (?,?)", (MIGRATION, json.dumps(out, sort_keys=True)))
    conn.commit()
    out["total_linked"] = sum(t["linked"] for t in out["tables"])
    out["total_null"] = sum(t["null"] for t in out["tables"])
    return out


def relink(conn) -> dict[str, Any]:
    """Re-run the backfill for every table. Idempotent, and SAFE to call often.

    WHY THIS EXISTS (a real gap, measured)
    --------------------------------------
    Registering a NEW skill adds it to `skill_prompt_ssot` and
    `skill_contract_template`, but NOTHING linked it: measured, after registering
    `no_null_standard` its contract's `skill_ref` was NULL and its 7 fields / 8
    TDD cases had NULL `contract_ref`. That is the "half-registered" trap — a
    skill present in one layer and invisible in another — and it is the SAME
    defect the split migration was written to remove.

    So linking is not a one-off migration step: it is a step the REGISTRATION
    path must run. This function is that step, and it is idempotent so calling it
    after every registration is safe.
    """
    out: dict[str, Any] = {"tables": []}
    # `skill_contract_template` is in SKIP for `_tables_with_skill_key` (it was
    # handled by `migrate_contract_ref`), but its `skill_ref` is EXACTLY the link
    # a new registration needs. Measured: skipping it left the new contract's
    # `skill_ref` NULL while its children were linked — a half-linked contract.
    if _has_col(conn, "skill_contract_template", "skill_ref"):
        conn.execute(
            "UPDATE skill_contract_template SET skill_ref = (SELECT s.skill_id "
            "FROM skill_registry s WHERE s.skill_key = "
            "skill_contract_template.skill_key)")
        out["tables"].append({"table": "skill_contract_template", "linked":
                              conn.execute(
                                  "SELECT COUNT(*) FROM skill_contract_template "
                                  "WHERE skill_ref IS NOT NULL").fetchone()[0]})
    for t in _tables_with_skill_key(conn):
        if not _has_col(conn, t, "skill_ref"):
            conn.execute("ALTER TABLE %s ADD COLUMN skill_ref INTEGER" % t)
        conn.execute(
            "UPDATE %s SET skill_ref = (SELECT s.skill_id FROM skill_registry s "
            "WHERE s.skill_key = %s.skill_key)" % (t, t))
        linked = conn.execute(
            "SELECT COUNT(*) FROM %s WHERE skill_ref IS NOT NULL" % t
        ).fetchone()[0]
        out["tables"].append({"table": t, "linked": linked})
    # The contract children, by contract_id.
    if _has_col(conn, "skill_contract_template", "contract_ref"):
        for c in ("skill_contract_field", "skill_contract_tdd_case",
                  "skill_contract_streak", "skill_contract_review_log"):
            if not _has_col(conn, c, "contract_ref"):
                conn.execute("ALTER TABLE %s ADD COLUMN contract_ref INTEGER" % c)
            conn.execute(
                "UPDATE %s SET contract_ref = (SELECT p.contract_ref FROM "
                "skill_contract_template p WHERE p.contract_id = %s.contract_id)"
                % (c, c))
            out["tables"].append({"table": c, "linked": conn.execute(
                "SELECT COUNT(*) FROM %s WHERE contract_ref IS NOT NULL" % c
            ).fetchone()[0]})
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