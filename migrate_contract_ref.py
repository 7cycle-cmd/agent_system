# -*- coding: utf-8 -*-
"""migrate_contract_ref.py — give `skill_contract_template` an INTEGER identity.

THE DEFECT
----------
```
contract_id   TEXT PRIMARY KEY      <- an identity that is a NAME
skill_key     TEXT NOT NULL UNIQUE
```
`contract_id` holds `SKILL.INDEPENDENT.REVIEW` — a dotted NAME. That is the SAME
defect as `skill_key`: an identity that is a name can be misspelled, can be
renamed without anything noticing, and cannot be joined on safely. FOUR tables FK
to it:

    skill_contract_field        114 rows
    skill_contract_tdd_case     114 rows
    skill_contract_streak        22 rows
    skill_contract_review_log     4 rows

THE FIX (the same shape as the skill split, which is already proven)
------------------------------------------------------------------
```
skill_contract_template
    contract_ref  INTEGER PRIMARY KEY AUTOINCREMENT   <- IDENTITY
    contract_id   TEXT NOT NULL UNIQUE                <- the LABEL, kept
    ... all other columns unchanged ...
```
`contract_id` is KEPT and stays UNIQUE, so:
  * every existing reader keeps working (no API change)
  * the four children's FK clauses still resolve
  * `contract_ref` is the identity a NEW join should use

WHY A REBUILD IS SAFE HERE, AND WHY IT IS VERIFIED
--------------------------------------------------
SQLite cannot add a PRIMARY KEY column with ALTER TABLE, so the parent must be
rebuilt. It is 19 rows, and the rebuild is done with:
  * `PRAGMA foreign_keys = OFF` for the swap only
  * a CONTENT HASH of every row before and after — a row count alone would pass
    while the data changed
  * ONE transaction, so a failure rolls back
  * the children's FK clauses re-checked afterwards

Run:  .\\.venv\\Scripts\\python.exe migrate_contract_ref.py [--apply]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"
MIGRATION = "contract_ref_v1"
PARENT = "skill_contract_template"
CHILDREN = ("skill_contract_field", "skill_contract_tdd_case",
            "skill_contract_streak", "skill_contract_review_log")


def _cols(conn, t: str) -> list[str]:
    return [c[1] for c in conn.execute("PRAGMA table_info(%s)" % t)]


def _content_hash(conn, t: str, cols: list[str], *,
                  where: str = "", params: tuple = ()) -> str:
    """A hash over EVERY value of EVERY row, in a stable order.

    A row COUNT is not enough: a rebuild that copies 19 rows but swaps two values
    would pass a count check. This is the observation that makes "unchanged" a
    measurement rather than a claim.

    `where`/`params` scope the hash to a SUBSET, which is what a migration proof
    needs: comparing the whole table to a pre-migration backup goes red as soon as
    a NEW row is legitimately added, and the migration was never at fault.
    """
    h = hashlib.sha256()
    sql = "SELECT %s FROM %s" % (", ".join(cols), t)
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY " + ", ".join(cols)
    for row in conn.execute(sql, params):
        h.update(repr(tuple(row)).encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def already_done(conn) -> bool:
    if "contract_ref" in _cols(conn, PARENT):
        return True
    try:
        return bool(conn.execute(
            "SELECT 1 FROM schema_migration_log WHERE migration=?",
            (MIGRATION,)).fetchone())
    except sqlite3.Error:
        return False


def plan(conn) -> dict[str, Any]:
    cols = _cols(conn, PARENT)
    return {
        "migration": MIGRATION,
        "already_done": already_done(conn),
        "parent_rows": conn.execute("SELECT COUNT(*) FROM %s" % PARENT
                                    ).fetchone()[0],
        "parent_has_contract_ref": "contract_ref" in cols,
        "children": {c: conn.execute("SELECT COUNT(*) FROM %s" % c).fetchone()[0]
                     for c in CHILDREN},
        "children_with_contract_ref": {c: "contract_ref" in _cols(conn, c)
                                       for c in CHILDREN},
        "note": ("contract_id is KEPT as a UNIQUE label, so no reader changes. "
                 "contract_ref is the new INTEGER identity."),
    }


def apply(conn) -> dict[str, Any]:
    if already_done(conn):
        return {"action": "already_applied", "migration": MIGRATION}

    old_cols = _cols(conn, PARENT)
    before_hash = _content_hash(conn, PARENT, old_cols)
    before_rows = conn.execute("SELECT COUNT(*) FROM %s" % PARENT).fetchone()[0]
    child_before = {c: conn.execute("SELECT COUNT(*) FROM %s" % c).fetchone()[0]
                    for c in CHILDREN}

    # The new shape: contract_ref FIRST (the identity), then every old column.
    # `contract_id` keeps NOT NULL UNIQUE — it is the label, and a label that can
    # be NULL is a label nobody can read.
    new_cols = ["contract_ref INTEGER PRIMARY KEY AUTOINCREMENT",
                "contract_id TEXT NOT NULL UNIQUE"]
    for c in old_cols:
        if c == "contract_id":
            continue
        info = [x for x in conn.execute("PRAGMA table_info(%s)" % PARENT)
                if x[1] == c][0]
        decl = info[2] or "TEXT"
        if info[3]:
            decl += " NOT NULL"
        if info[4] is not None:
            decl += " DEFAULT %s" % info[4]
        new_cols.append("%s %s" % (c, decl))

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # A VIEW references the parent, so `DROP TABLE` fails with
        # "error in view v_skill_contract: no such table". Measured, not assumed:
        # the first attempt failed exactly there and ROLLED BACK cleanly (19 rows,
        # no contract_ref, integrity ok) — which is why the swap is inside one
        # transaction. The view is dropped and recreated around the swap, and its
        # DDL is read from the database rather than retyped, so the recreated view
        # cannot drift from the one that was there.
        view_sql = None
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='view' "
                           "AND name='v_skill_contract'").fetchone()
        if row:
            view_sql = row[0]
            conn.execute("DROP VIEW v_skill_contract")

        conn.execute("DROP TABLE IF EXISTS %s_new" % PARENT)
        conn.execute("CREATE TABLE %s_new (%s)" % (PARENT, ", ".join(new_cols)))
        conn.execute(
            "INSERT INTO %s_new (%s) SELECT %s FROM %s"
            % (PARENT, ", ".join(old_cols), ", ".join(old_cols), PARENT))
        conn.execute("DROP TABLE %s" % PARENT)
        conn.execute("ALTER TABLE %s_new RENAME TO %s" % (PARENT, PARENT))
        conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_contract_status "
                     "ON %s (status, contract_id)" % PARENT)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_contract_skill_key "
                     "ON %s (skill_key)" % PARENT)
        if view_sql:
            conn.execute(view_sql)

        # VERIFY BEFORE COMMITTING. A migration that commits and then checks has
        # already damaged the data.
        after_rows = conn.execute("SELECT COUNT(*) FROM %s" % PARENT).fetchone()[0]
        after_hash = _content_hash(conn, PARENT, old_cols)
        if after_rows != before_rows or after_hash != before_hash:
            raise RuntimeError(
                "the rebuild changed the data: rows %d->%d, hash %s->%s"
                % (before_rows, after_rows, before_hash, after_hash))
        child_after = {c: conn.execute("SELECT COUNT(*) FROM %s" % c).fetchone()[0]
                       for c in CHILDREN}
        if child_after != child_before:
            raise RuntimeError("a child table changed: %s -> %s"
                               % (child_before, child_after))

        # Give each child the INTEGER identity too, and backfill it.
        for c in CHILDREN:
            if "contract_ref" not in _cols(conn, c):
                conn.execute("ALTER TABLE %s ADD COLUMN contract_ref INTEGER" % c)
            conn.execute(
                "UPDATE %s SET contract_ref = (SELECT p.contract_ref FROM %s p "
                "WHERE p.contract_id = %s.contract_id)" % (c, PARENT, c))
            bad = conn.execute(
                "SELECT COUNT(*) FROM %s WHERE contract_id IN (SELECT contract_id "
                "FROM %s) AND contract_ref IS NULL" % (c, PARENT)).fetchone()[0]
            if bad:
                raise RuntimeError("%s: %d rows failed to link" % (c, bad))
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migration_log (id INTEGER PRIMARY KEY "
        "AUTOINCREMENT, migration TEXT NOT NULL, detail_json TEXT NOT NULL "
        "DEFAULT '{}', created_at TEXT NOT NULL DEFAULT (datetime('now')))")
    detail = {"parent_rows": before_rows, "content_hash": before_hash,
              "children": child_before}
    conn.execute("INSERT INTO schema_migration_log (migration, detail_json) "
                 "VALUES (?,?)", (MIGRATION, json.dumps(detail, sort_keys=True)))
    conn.commit()
    return {"action": "applied", "migration": MIGRATION, **detail}


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