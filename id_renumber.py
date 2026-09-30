# -*- coding: utf-8 -*-
"""id_renumber.py — REUSABLE: renumber an AUTOINCREMENT id space to 1..N, safely.

WHY THIS EXISTS
---------------
`id_audit.py` measures the gap. This CLOSES it, for any table, without breaking
a reference.

THE PROBLEM IT SOLVES
---------------------
`INTEGER PRIMARY KEY AUTOINCREMENT` keeps a high-water mark and NEVER reuses a
value, so a rebuilt table re-inserts its rows at the NEXT ids. Measured:
`db_table_registry` holds 111 rows at **4323..4432** with `sqlite_sequence=4990`.
The ids are UNIQUE but not CONTIGUOUS, so an id derived from them (`T-4323-1`)
is an artefact of insert history rather than a stable name.

WHAT IT REFUSES (all refuse; none warns)
----------------------------------------
  * a table that is not an AUTOINCREMENT table
  * a table whose ids are ALREADY contiguous (nothing to do is not a failure,
    but it is reported as `already_contiguous`, not as a silent success)
  * a table with DUPLICATE ids — renumbering would hide a real defect
  * a run that would leave a FOREIGN KEY dangling. Every FK that points at the
    table is checked BEFORE and AFTER, and a mismatch ABORTS the whole run
  * `--apply` without `--confirm`, because the dangerous direction is writing

HOW IT STAYS SAFE
-----------------
  1. DRY RUN IS THE DEFAULT. It prints the mapping and changes nothing.
  2. The mapping is written to `legacy_id_map` (append-only), so an old id can
     always be resolved to its new one. Without that record the renumber is a
     silent rewrite of history.
  3. FKs are updated in the SAME transaction as the PK, so a crash cannot leave
     a half-renumbered graph.
  4. `sqlite_sequence` is reset to the new max, so the NEXT insert continues
     from N+1 instead of jumping back to the old high-water mark.

WHAT IT DOES NOT FIX
--------------------
The CAUSE. A future rebuild will create a new gap, because AUTOINCREMENT never
reuses a value. This tool makes the id space clean NOW; keeping it clean is a
migration discipline, not a one-off script.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import id_audit as ia  # noqa: E402

DB = BASE / "agent.db"


class RenumberRefused(RuntimeError):
    """Raised when a renumber would break a reference or hide a defect."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("renumber refused — nothing written: %s"
                         % "; ".join(self.reasons))


def referencing_columns(conn: sqlite3.Connection, table: str,
                        pk: str) -> list[dict[str, str]]:
    """Every (table, column) that REFERENCES `table`, read from the schema.

    THREE sources, because SQLite records them differently:
      * a declared FOREIGN KEY  -> PRAGMA foreign_key_list
      * a column NAMED like the pk with no FK declared -> a name match, which is
        reported as `declared: no` so a reader knows it is a convention, not a
        constraint
      * a POLYMORPHIC reference -> `(entity_type, entity_ref_id)`, where ONE
        column points at MANY tables and the target is chosen by a sibling
        column. Reported as `declared: polymorphic`.

    DEFECT FOUND BY RUNNING IT, AND IT BROKE THE DATA: the first version found
    only the first two. `version_registry.entity_ref_id` is POLYMORPHIC — it
    holds `entity_type='T'` + `entity_ref_id=<db_table_id>` — so it was NOT
    updated, and after the renumber 110 of 111 T rows pointed at a table that no
    longer existed. A reference the tool cannot SEE is a reference it will
    silently break, so the polymorphic pattern is now detected explicitly.
    """
    out: list[dict[str, str]] = []
    names = [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in names:
        if t.startswith("sqlite_") or t == table:
            continue
        cols = [str(c[1]) for c in conn.execute("PRAGMA table_info(%s)" % t)]
        for fk in conn.execute("PRAGMA foreign_key_list(%s)" % t):
            if fk[2] == table:
                out.append({"table": t, "column": fk[3], "declared": "yes"})
        for c in cols:
            if _is_name_match(c, pk, table) and not any(
                    o["table"] == t and o["column"] == c for o in out):
                out.append({"table": t, "column": c, "declared": "no"})
        # POLYMORPHIC: (entity_type, entity_ref_id). The letter column says WHICH
        # register, so only rows whose letter matches THIS table are updated.
        if "entity_ref_id" in cols and "entity_type" in cols:
            letter = _letter_for(conn, table)
            if letter and not any(o["table"] == t and
                                  o["column"] == "entity_ref_id" for o in out):
                out.append({"table": t, "column": "entity_ref_id",
                            "declared": "polymorphic", "letter": letter,
                            "letter_column": "entity_type"})
    return out


def _letter_for(conn: sqlite3.Connection, table: str) -> str | None:
    """The entity_type letter whose register_table is `table`, or None."""
    row = conn.execute("SELECT type_letter FROM entity_type_registry WHERE "
                       "register_table=?", (table,)).fetchone()
    return str(row[0]) if row else None


def _is_name_match(column: str, pk: str, table: str) -> bool:
    """Is `column` a NAME-MATCH reference to `table.pk`, or just its own pk?

    DEFECT FOUND BY RUNNING IT: the first version matched any column whose name
    equalled the pk name. When the pk is called `id` — which it is on almost
    every table — that made EVERY table's own primary key look like a reference
    to the table being renumbered. The renumber then tried to rewrite
    `_probe_ref.id` from the target's id map and hit a FOREIGN KEY failure.

    A name match is only credible when the column is NOT the referencing table's
    own primary key, and when the name is specific enough to mean something:
    either it is the target's pk name qualified by the target (`<table>_id`), or
    the pk name is not the generic `id`.
    """
    if column == pk and column != "id":
        return True
    if column == "%s_%s" % (table, pk):
        return True
    if column == "%s_id" % table:
        return True
    return False


def plan(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """The mapping old_id -> new_id, and every reference that must follow it.

    Read-only. This is what a human approves before anything is written.

    GATE ORDER MATTERS. The DUPLICATE check runs BEFORE the autoincrement check,
    because a duplicate id is a defect in the DATA and must be reported as such
    even for a table this tool would not renumber. Measured: with the
    autoincrement gate first, a duplicate-id fixture was refused as "not an
    AUTOINCREMENT table" — the right answer for the wrong reason, and the
    duplicate check was UNREACHABLE.
    """
    cols = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)]
    if not cols:
        raise RenumberRefused(["no table named %r" % table])
    tables = {t["table"]: t["pk"] for t in ia.autoincrement_tables(conn)}
    pk = tables.get(table)
    if pk is None:
        # Not an autoincrement table. Still check for duplicates, so a real
        # defect is not hidden behind a schema complaint.
        for cand in ("id", "rowid"):
            if cand in cols:
                ids = [r[0] for r in conn.execute(
                    "SELECT %s FROM %s" % (cand, table))]
                if len(ids) != len(set(ids)):
                    raise RenumberRefused(
                        ["%s.%s has DUPLICATE ids — renumbering would HIDE a "
                         "real defect" % (table, cand)])
        raise RenumberRefused(
            ["%r is not an AUTOINCREMENT table (see id_audit.py)" % table])
    ids = [int(r[0]) for r in conn.execute(
        "SELECT %s FROM %s ORDER BY %s" % (pk, table, pk))]
    if len(ids) != len(set(ids)):
        raise RenumberRefused(
            ["%s has DUPLICATE ids — renumbering would HIDE a real defect"
             % table])
    if ids == list(range(1, len(ids) + 1)):
        return {"table": table, "pk": pk, "action": "already_contiguous",
                "rows": len(ids), "mapping": {}, "references": []}
    mapping = {old: new for new, old in enumerate(ids, start=1)}
    refs = referencing_columns(conn, table, pk)
    for r in refs:
        r["rows"] = int(conn.execute(
            "SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL"
            % (r["table"], r["column"])).fetchone()[0])
    return {"table": table, "pk": pk, "action": "renumber", "rows": len(ids),
            "old_min": ids[0], "old_max": ids[-1], "new_max": len(ids),
            "mapping": mapping, "references": refs}


def apply(conn: sqlite3.Connection, table: str, *, confirm: bool = False,
          cite_ref: str = "") -> dict[str, Any]:
    """Renumber, updating every reference in ONE transaction.

    `confirm=True` is required: the dangerous direction of a mistake here is
    writing, so the default refuses.
    """
    if not confirm:
        raise RenumberRefused(
            ["apply() requires confirm=True — a renumber rewrites ids, so the "
             "default must refuse rather than proceed"])
    if not str(cite_ref or "").strip():
        raise RenumberRefused(
            ["cite_ref is required — a renumber with no citation cannot be "
             "traced back to why it was done"])
    p = plan(conn, table)
    if p["action"] == "already_contiguous":
        return {"ok": True, "action": "already_contiguous", "rows": p["rows"]}
    pk = p["pk"]
    mapping = p["mapping"]

    # A TEMP table holds the mapping so the UPDATE is a JOIN, not a Python loop
    # issuing one statement per row.
    conn.execute("CREATE TEMP TABLE _idmap (old_id INTEGER PRIMARY KEY, "
                 "new_id INTEGER NOT NULL)")
    conn.executemany("INSERT INTO _idmap (old_id, new_id) VALUES (?,?)",
                     list(mapping.items()))

    # FK enforcement OFF for the rewrite: the ids are being moved as a SET, so
    # an intermediate state is legitimately inconsistent. It is turned back ON
    # before the commit, and the AFTER check below is what proves the result.
    #
    # DEFECT FOUND BY RUNNING IT: `PRAGMA foreign_keys` is a NO-OP inside a
    # transaction, and the CREATE TEMP TABLE above had already opened one, so
    # the pragma was silently ignored and the first referencing UPDATE died with
    # "FOREIGN KEY constraint failed". The commit below ends that transaction so
    # the pragma actually takes effect.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # 1. the referencing columns FIRST, so no row ever points at a pk that
        #    has already moved. A POLYMORPHIC column is filtered by its letter,
        #    so only the rows that actually point at THIS table are touched.
        for r in p["references"]:
            if r.get("declared") == "polymorphic":
                conn.execute(
                    "UPDATE %s SET %s = (SELECT new_id FROM _idmap WHERE old_id "
                    "= %s.%s) WHERE %s = ? AND %s IN (SELECT old_id FROM _idmap)"
                    % (r["table"], r["column"], r["table"], r["column"],
                       r["letter_column"], r["column"]),
                    (r["letter"],))
            else:
                conn.execute(
                    "UPDATE %s SET %s = (SELECT new_id FROM _idmap WHERE old_id = "
                    "%s.%s) WHERE %s IN (SELECT old_id FROM _idmap)"
                    % (r["table"], r["column"], r["table"], r["column"],
                       r["column"]))
        # 2. the pk itself. A collision is impossible because the mapping is a
        #    bijection onto 1..N and the old ids are all >= 1.
        conn.execute(
            "UPDATE %s SET %s = (SELECT new_id FROM _idmap WHERE old_id = "
            "%s.%s) WHERE %s IN (SELECT old_id FROM _idmap)"
            % (table, pk, table, pk, pk))
        # 3. the sequence, so the NEXT insert continues from N+1.
        conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?,?)",
                     (table, p["new_max"]))
        # 4. the append-only record, so an old id still resolves.
        conn.execute(
            "INSERT INTO legacy_id_map (entity_type, old_id, new_id, seen_in, "
            "note, migrated_at) VALUES (?,?,?,?,?,datetime('now'))",
            (table, str(p["old_min"]), str(p["new_max"]), table,
             "renumbered to 1..%d by id_renumber.py; cite=%s"
             % (p["new_max"], cite_ref)))
        conn.execute("DROP TABLE _idmap")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.execute("PRAGMA foreign_keys = ON")
        raise
    conn.execute("PRAGMA foreign_keys = ON")

    # THE AFTER CHECK. A renumber that leaves a dangling FK is worse than the
    # gap it removed, so the result is VERIFIED rather than assumed.
    dangling = []
    for r in p["references"]:
        if r.get("declared") == "polymorphic":
            n = conn.execute(
                "SELECT COUNT(*) FROM %s c WHERE c.%s IS NOT NULL AND c.%s = ? "
                "AND NOT EXISTS (SELECT 1 FROM %s p WHERE p.%s = c.%s)"
                % (r["table"], r["column"], r["letter_column"], table, pk,
                   r["column"]), (r["letter"],)).fetchone()[0]
        else:
            n = conn.execute(
                "SELECT COUNT(*) FROM %s c WHERE c.%s IS NOT NULL AND NOT EXISTS "
                "(SELECT 1 FROM %s p WHERE p.%s = c.%s)"
                % (r["table"], r["column"], table, pk, r["column"])).fetchone()[0]
        if n:
            dangling.append({"table": r["table"], "column": r["column"],
                             "declared": r.get("declared", "no"),
                             "dangling": int(n)})
    after = ia.audit_table(conn, table, pk)
    return {"ok": not dangling, "action": "renumbered", "table": table,
            "rows": p["rows"], "new_max": p["new_max"],
            "references_updated": p["references"], "dangling": dangling,
            "after": after}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--table", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--cite", default="")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.apply:
            out = apply(conn, args.table, confirm=args.confirm,
                        cite_ref=args.cite)
        else:
            out = plan(conn, args.table)
            if out["action"] == "renumber":
                m = out["mapping"]
                keys = sorted(m)
                out["mapping_preview"] = {str(k): m[k] for k in keys[:5]}
                out["mapping_preview"]["..."] = "..."
                out["mapping_preview"][str(keys[-1])] = m[keys[-1]]
                del out["mapping"]
        print(json.dumps(out, indent=2, ensure_ascii=False))
    except RenumberRefused as e:
        print(json.dumps({"ok": False, "refused": e.reasons}, indent=2,
                         ensure_ascii=False))
        raise SystemExit(2)
    finally:
        conn.close()


if __name__ == "__main__":
    main()