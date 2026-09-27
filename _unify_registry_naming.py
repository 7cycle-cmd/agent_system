# -*- coding: utf-8 -*-
"""_unify_registry_naming.py — enforce the ruling: `registry`, NOT `register`.

THE RULING (2026-09-27), verbatim:

    "be unified by registry not register"

WHAT THIS IS FOR
----------------
`terminology_registry.check_register_name` now REFUSES a NEW `_register` name, so
the standard is enforced at the write site going forward. But there are EXISTING
non-standard names. This script is the MIGRATION for those, and it PLANS before it
acts: `--plan` prints every rename; `--apply` performs it.

WHY A PLAN STEP AND NOT JUST A RENAME
-------------------------------------
MEASURED scale (2026-09-27): 31 sqlite objects and 39 active terms end in
`_register`; 5984 code references sit in 569 files; and **8 entity letters have
`entity_type_register.register_table` pointing at a `_register` table**, so a
rename changes ENTITY ID RESOLUTION.

That last line is why this is not cosmetic: `T-19-...` resolves through
`table_id_of_letter()` -> `entity_type_register.register_table`. Rename the table
without updating that row and every entity id for that letter stops resolving.

THE ORDER IS THE REPO'S OWN RENAME LESSON
(`/memories/repo/terminology_register_naming.md:33`): "Migration is ADDITIVE:
create new -> copy -> VERIFY counts -> drop old." For a sqlite table,
`ALTER TABLE ... RENAME TO` IS the additive form: the data is not copied, so there
is nothing to miscopy, and the count is verified after each rename.

WHAT IT DOES NOT DO
-------------------
* It does NOT delete data. A RENAME moves; it does not destroy.
* It does NOT touch a name that does not end in `_register`.
* It does NOT rewrite code: the DB and the CODE must be renamed TOGETHER, so
  `--plan` prints the code side and `--apply` reports it as the next step. A
  half-renamed DB plus rewritten code is worse than either alone.

Run:
    .\\.venv\\Scripts\\python.exe _unify_registry_naming.py --plan
    .\\.venv\\Scripts\\python.exe _unify_registry_naming.py --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = BASE / "agent.db"
OLD, NEW = "_register", "_registry"


def plan_objects(conn: sqlite3.Connection) -> list[dict]:
    """Every sqlite object (table/view/index/trigger) ending in `_register`."""
    out = []
    for r in conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name LIKE '%" + OLD + "' ORDER BY type, name"):
        out.append({"type": str(r["type"]), "old": str(r["name"]),
                    "new": str(r["name"])[:-len(OLD)] + NEW})
    return out


def plan_columns(conn: sqlite3.Connection) -> list[dict]:
    """Columns whose NAME ends in `_register` (a column rename too)."""
    out = []
    for t in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        tn = str(t["name"])
        for c in conn.execute("PRAGMA table_info(%s)" % tn):
            n = str(c["name"])
            if n.endswith(OLD):
                out.append({"table": tn, "old": n,
                            "new": n[:-len(OLD)] + NEW})
    return out


def plan_entities(conn: sqlite3.Connection) -> list[dict]:
    """`entity_type_register.register_table` rows pointing at a `_register` table."""
    out = []
    for r in conn.execute(
            "SELECT type_letter, register_table, pk_column FROM entity_type_register "
            "WHERE register_table LIKE '%" + OLD + "' ORDER BY type_letter"):
        out.append({"letter": str(r["type_letter"]),
                    "old": str(r["register_table"]),
                    "new": str(r["register_table"])[:-len(OLD)] + NEW,
                    "pk_column": str(r["pk_column"])})
    return out


def plan_registry_rows(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT db_table_id, table_key FROM db_table_registry "
            "WHERE table_key LIKE '%" + OLD + "' ORDER BY table_key"):
        out.append({"db_table_id": int(r["db_table_id"]),
                    "old": str(r["table_key"]),
                    "new": str(r["table_key"])[:-len(OLD)] + NEW})
    return out


def plan_terms(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute(
            "SELECT term_id, term_key FROM terminology_register "
            "WHERE term_key LIKE '%" + OLD + "' ORDER BY term_key"):
        out.append({"term_id": int(r["term_id"]), "old": str(r["term_key"]),
                    "new": str(r["term_key"])[:-len(OLD)] + NEW})
    return out


def plan_code() -> list[dict]:
    """Files whose TEXT contains `*_register` — the code side of the rename."""
    rx = re.compile(r"\b[a-z0-9_]+" + OLD + r"\b")
    out = []
    for p in sorted(BASE.rglob("*.py")):
        if ".venv" in str(p) or p.name == Path(__file__).name:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        n = len(rx.findall(t))
        if n:
            out.append({"file": str(p.relative_to(BASE)), "count": n})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    apply = bool(args.apply)

    conn = sqlite3.connect(str(DB), timeout=60)
    conn.row_factory = sqlite3.Row
    try:
        objs, cols = plan_objects(conn), plan_columns(conn)
        ents, regs, terms = plan_entities(conn), plan_registry_rows(conn), plan_terms(conn)
        code = plan_code()

        print("=" * 78)
        print("THE RULING: be unified by %r not %r"
              % (NEW.lstrip("_"), OLD.lstrip("_")))
        print("=" * 78)
        print("  objects to RENAME      : %d" % len(objs))
        print("  columns to RENAME      : %d" % len(cols))
        print("  ENTITY LETTERS affected: %d  <-- entity id resolution" % len(ents))
        print("  db_table_registry rows : %d" % len(regs))
        print("  terminology terms      : %d" % len(terms))
        print("  .py FILES with refs    : %d  (%d occurrences)"
              % (len(code), sum(c["count"] for c in code)))
        print()
        print("--- ENTITY ID IMPACT (the part that is not cosmetic) ---")
        for e in ents:
            print("   %-3s %-32s -> %s" % (e["letter"], e["old"], e["new"]))
        print()
        print("--- OBJECTS (first 12 of %d) ---" % len(objs))
        for o in objs[:12]:
            print("   %-8s %-34s -> %s" % (o["type"], o["old"], o["new"]))
        print()
        print("--- COLUMNS (first 12 of %d) ---" % len(cols))
        for c in cols[:12]:
            print("   %-30s.%-26s -> %s" % (c["table"], c["old"], c["new"]))
        print()
        print("--- CODE FILES (first 15 of %d) ---" % len(code))
        for c in code[:15]:
            print("   %-46s %d ref(s)" % (c["file"], c["count"]))

        if not apply:
            print()
            print("PLAN ONLY — nothing written. The DB and the CODE must be")
            print("renamed TOGETHER; this prints the whole blast radius first.")
            return 0

        backup = DB.with_name(DB.name + ".bak_registry_naming_"
                              + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(DB, backup)
        print("\nbackup (before any write): %s" % backup.name)

        # ---- the DB side, in dependency order --------------------------
        # Foreign keys OFF for the rename: SQLite would otherwise rewrite
        # REFERENCES clauses mid-flight. Turned back on before verification.
        conn.execute("PRAGMA foreign_keys=OFF")
        renamed = 0
        for o in objs:
            if o["type"] != "table":
                continue
            before = conn.execute("SELECT COUNT(*) FROM %s" % o["old"]).fetchone()[0]
            conn.execute('ALTER TABLE "%s" RENAME TO "%s"' % (o["old"], o["new"]))
            after = conn.execute("SELECT COUNT(*) FROM %s" % o["new"]).fetchone()[0]
            if after != before:
                raise SystemExit("COUNT CHANGED on %s: %s -> %s"
                                 % (o["old"], before, after))
            renamed += 1
        print("   renamed tables         : %d (every count verified)" % renamed)

        conn.execute("UPDATE db_table_registry SET table_key=REPLACE(table_key,?,?) "
                     "WHERE table_key LIKE ?", (OLD, NEW, "%" + OLD))
        conn.execute("UPDATE entity_type_register SET register_table=REPLACE("
                     "register_table,?,?) WHERE register_table LIKE ?",
                     (OLD, NEW, "%" + OLD))
        conn.execute("UPDATE terminology_register SET term_key=REPLACE(term_key,?,?) "
                     "WHERE term_key LIKE ?", (OLD, NEW, "%" + OLD))
        conn.commit()
        print("   updated registry rows  : db_table_registry + entity_type_register "
              "+ terminology_register")

        # ---- VERIFY, from the DB -------------------------------------
        conn.execute("PRAGMA foreign_keys=ON")
        left = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                            "name LIKE ?", ("%" + OLD,)).fetchone()[0]
        ent_left = conn.execute("SELECT COUNT(*) FROM entity_type_register WHERE "
                                "register_table LIKE ?", ("%" + OLD,)).fetchone()[0]
        print()
        print("   objects still `_register`: %d" % left)
        print("   letters still `_register`: %d" % ent_left)
        print()
        print("   NEXT: the CODE rename (%d files) must now follow, then "
              "`pytest -q`." % len(code))
        print("   A half-renamed DB plus rewritten code is worse than either.")
        return 0 if (left == 0 and ent_left == 0) else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
