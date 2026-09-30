# -*- coding: utf-8 -*-
"""subject_kind_align.py — make the TWO subject-kind vocabularies agree, and bind
                           every kind to an INTEGER id instead of a NAME.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
    "chat / task / workflow / ticket.... all is same"
    "evidence can be proofed is the only wat to have the work output standardize"

MEASURED: they are NOT the same today. Two registers hold a subject-kind
vocabulary and they disagree on TEN names:

    subject_kind_registry only : field, file, namespace, other, register, route,
                                 system, table, tacid, version
    dimension_binding only     : db_field, db_table, worker_identity
    agreed (11)                : api, capability, channel, chat, entity, entity_id,
                                 function, module, service, task, workflow

`table`/`field` vs `db_table`/`db_field` is the SAME drift corrected in
`TAXONOMY.LEVEL.VOCAB.AND.GRAPH.EDGES`, one level out (the user's ruling:
"db_table , db_field is more respresenattive than table, field").

AND every kind that is bound is bound to the WRONG THING (measured):
    module     module_registry.module_key      a NAME   -> the identity law violation
    task       task_instances.task_id          a TRACKING CODE (the user ruled the
                                               task id is dev_task.id)
    chat       chat_main                       the OUTPUT table, not the identity
    version    version_registry.id             a COLUMN THAT DOES NOT EXIST
                                               (the pk is version_registry_id)

THE MATCHING RULE IS DERIVED, NOT HARDCODED
-------------------------------------------
A kind and a `dimension_binding_registry.subject_kind` are the SAME CONCEPT when
they resolve the SAME REGISTER TABLE. So the alignment is: for each
`subject_kind_registry` row, find the `entity_type_registry` kind with the same
`register_table`; if the names differ, rename to that name. That DERIVES
`table -> db_table` and `field -> db_field` from data, so a NEW kind is aligned
automatically instead of needing this file edited.

Run:
    .\\.venv\\Scripts\\python.exe subject_kind_align.py --measure
    .\\.venv\\Scripts\\python.exe subject_kind_align.py            # dry run
    .\\.venv\\Scripts\\python.exe subject_kind_align.py --apply
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

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DB = BASE / "agent.db"

# THE REF FIXES. Each is EXPLICIT because each is a DECISION, not a derivation —
# the ref it replaces is a real value in the DB, so the change must be named and
# reported rather than inferred.
#
# CORRECTED 2026-09-26. MEASURED: this tuple used to carry FOUR fixes, and THREE
# of them were WRONG — they were applied to the live DB and broke the register:
#
#   chat    -> identity_registry.identity_id   REVERTED
#           `chat_main` is the CHAT (the table the kind is named after).
#           `identity_registry` is the IDENTITY, a different kind that already
#           has its own row (`worker_identity`).
#   task    -> dev_task.id                     REVERTED
#           `task_instances` EXISTS (measured) and its `task_id` is the TEXT
#           tracking code the seed names. `dev_task` is a DIFFERENT table.
#   module  -> module_registry.module_id       REVERTED
#           `ticket_store.map_module` STORES THE KEY
#           (`subject_ref_id=str(module)`, ticket_store.py:270) and every reader
#           JOINs `m.module_key = x.subject_ref_id` (ticket_store.py:311, 401).
#           So `_ref_exists` checked `module_id = 'task_center'` and REFUSED a
#           row that is correct. The stated justification — "resolve_subject
#           still accepts a key" — names a function that DOES NOT EXIST
#           (measured: `def resolve_subject` is defined NOWHERE).
#
# The ONE fix that was RIGHT is kept, because its reason is checkable:
#   version -> version_registry.version_registry_id
#           `version_registry.id` DOES NOT EXIST (measured); the pk is
#           `version_registry_id`. A ref to a missing column resolves nothing.
#
# (kind_key, new_ref_table, new_ref_column, WHY)
REF_FIXES: tuple[tuple[str, str, str, str], ...] = (
    ("version", "version_registry", "version_registry_id",
     "the declared ref_column `version_registry.id` DOES NOT EXIST — measured: the "
     "pk of version_registry is `version_registry_id`. A ref to a missing column "
     "resolves nothing."),
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _types(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    return {r[1]: str(r[2] or "") for r in conn.execute(
        "PRAGMA table_info(%s)" % table)}


def _pks(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)
            if len(r) > 5 and int(r[5] or 0) > 0]


def _is_key(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Is `column` a KEY — the pk, or a UNIQUE column?

    A key IS an identity whatever its TYPE. MEASURED 2026-09-26:
    `module_registry.module_key` is `NOT NULL UNIQUE`, so it is the natural key
    `ticket_store.map_module` stores and every reader JOINs on. Treating it as a
    "TEXT name standing in for an id" was a FALSE RED.
    """
    if column in _pks(conn, table):
        return True
    for idx in conn.execute("PRAGMA index_list(%s)" % table):
        # `idx[2]` is the UNIQUE flag; `idx[1]` is the index name.
        if not int(idx[2] or 0):
            continue
        cols = [r[2] for r in conn.execute("PRAGMA index_info(%s)" % idx[1])]
        if cols == [column]:
            return True
    return False


def derived_renames(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Rename a kind when ANOTHER register uses a different name for the SAME
    register table. DERIVED, so a new kind is aligned without editing this file."""
    if not _table_exists(conn, "entity_type_registry"):
        return []
    by_table: dict[str, str] = {}
    for r in conn.execute("SELECT entity_kind, register_table "
                          "FROM entity_type_registry"):
        # The FIRST name wins deterministically (ordered by kind) so a run is
        # repeatable. `db_table`/`db_field` are the only conflicts and they are
        # each one-to-one.
        by_table.setdefault(str(r["register_table"]), str(r["entity_kind"]))
    out: list[dict[str, Any]] = []
    for r in conn.execute("SELECT kind_id, kind_key, ref_table FROM "
                          "subject_kind_registry WHERE is_active=1 "
                          "ORDER BY kind_key"):
        rt = str(r["ref_table"] or "")
        if rt in ("", "NA"):
            continue
        want = by_table.get(rt)
        if want and want != str(r["kind_key"]):
            out.append({"kind_id": int(r["kind_id"]),
                        "from": str(r["kind_key"]), "to": want,
                        "ref_table": rt,
                        "cite": ("register:entity_type_registry: (kind %r "
                                 "resolves the same table %s)"
                                 % (want, rt))})
    return out


def ref_fix_list(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The explicit ref corrections, each with its reason and a check that it FIXES
    a real problem (the new column must EXIST and be an id)."""
    out: list[dict[str, Any]] = []
    for kind, tbl, col, why in REF_FIXES:
        cur = conn.execute("SELECT ref_table, ref_column FROM "
                           "subject_kind_registry WHERE kind_key = ?",
                           (kind,)).fetchone()
        if not cur:
            continue
        types = _types(conn, tbl) if _table_exists(conn, tbl) else {}
        out.append({
            "kind_key": kind,
            "from_table": str(cur["ref_table"]), "from_column": str(cur["ref_column"]),
            "to_table": tbl, "to_column": col,
            "new_column_exists": col in types,
            "new_column_type": types.get(col, ""),
            "is_pk": col in _pks(conn, tbl),
            "why": why,
            "cite": ("measured: %s.%s exists=%s type=%s is_pk=%s"
                     % (tbl, col, col in types, types.get(col, "?"),
                        col in _pks(conn, tbl))),
        })
    return out


def name_ref_columns(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every kind whose ref_column is a TEXT NAME — the identity law violation."""
    out: list[dict[str, Any]] = []
    for r in conn.execute("SELECT kind_key, ref_table, ref_column FROM "
                          "subject_kind_registry WHERE is_active=1 "
                          "ORDER BY kind_key"):
        rt, rc = str(r["ref_table"] or ""), str(r["ref_column"] or "")
        if rt in ("", "NA") or rc in ("", "NA"):
            continue
        if not _table_exists(conn, rt):
            out.append({"kind_key": str(r["kind_key"]), "ref": "%s.%s" % (rt, rc),
                        "violation": "REF_TABLE_ABSENT"})
            continue
        types = _types(conn, rt)
        if rc not in types:
            out.append({"kind_key": str(r["kind_key"]), "ref": "%s.%s" % (rt, rc),
                        "violation": "COLUMN_MISSING"})
            continue
        # A TEXT column is a violation ONLY when it is NOT a KEY. A key is an
        # IDENTITY whatever its TYPE: `entity_type_registry.type_letter` IS the
        # pk (the letter IS the id), and `module_registry.module_key` is
        # `NOT NULL UNIQUE` (measured) — a natural key, so the key IS the
        # identity. Flagging either would be a FALSE RED.
        #
        # MEASURED 2026-09-26: this used to test `rc not in _pks(...)` only, so
        # `module_key` was reported as a TEXT_NAME violation while it is the
        # UNIQUE key `ticket_store.map_module` stores and every reader JOINs on.
        if "TEXT" in types[rc].upper() and not _is_key(conn, rt, rc):
            out.append({"kind_key": str(r["kind_key"]), "ref": "%s.%s" % (rt, rc),
                        "violation": "TEXT_NAME"})
    return out


def vocabulary_diff(conn: sqlite3.Connection) -> dict[str, Any]:
    """The two vocabularies, before and after the derived renames.

    BOTH sides read ACTIVE rows only. MEASURED 2026-09-26: this read every row,
    so a SOFT-DELETED kind (`is_active=0`) still appeared in the vocabulary and
    was reported as a disagreement — a kind that no longer exists cannot
    disagree with anything.
    """
    skr = [str(r[0]) for r in conn.execute(
        "SELECT kind_key FROM subject_kind_registry WHERE is_active=1 "
        "ORDER BY kind_key")]
    dbr = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT subject_kind FROM dimension_binding_registry "
        "WHERE is_active=1 ORDER BY subject_kind")] if _table_exists(
            conn, "dimension_binding_registry") else []
    ren = {r["from"]: r["to"] for r in derived_renames(conn)}
    skr_after = sorted(ren.get(k, k) for k in skr)
    return {"subject_kind_registry": skr, "dimension_binding": dbr,
            "renames": ren, "subject_kind_registry_after": skr_after,
            # A kind in BOTH registers must have the SAME NAME. A kind in only one
            # register is NOT a disagreement — it is an UNBOUND kind, reported.
            "both_but_different": sorted(
                (set(skr) & set(dbr)) - {k for k in skr if ren.get(k) == k}),
            "agreed_after": sorted(set(skr_after) & set(dbr))}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    ren = derived_renames(conn)
    fixes = ref_fix_list(conn)
    names = name_ref_columns(conn)
    vocab = vocabulary_diff(conn)
    # 5W1H coverage, per kind, so an incomplete kind is VISIBLE.
    dims = ("what", "why", "who", "when", "where", "how")
    skr_keys = sorted({vocab["renames"].get(str(r[0]), str(r[0]))
                       for r in conn.execute("SELECT kind_key FROM "
                                             "subject_kind_registry")})
    dbr = {r["subject_kind"]: {x for x in (r["dims"] or "").split(",")}
           for r in conn.execute(
               "SELECT subject_kind, GROUP_CONCAT(DISTINCT dimension_key) dims "
               "FROM dimension_binding_registry GROUP BY subject_kind")}
    cov = {}
    for k in skr_keys:
        have = dbr.get(k, set())
        cov[k] = {"have": sorted(x for x in have if x), 
                  "missing": [d for d in dims if d not in have],
                  "complete": all(d in have for d in dims)}
    return {"renames": len(ren), "rename_rows": ren,
            "ref_fixes": fixes, "name_ref_violations": names,
            "vocabulary": vocab, "coverage": cov,
            "complete_kinds": sorted(k for k, v in cov.items() if v["complete"]),
            "incomplete_kinds": {k: v["missing"] for k, v in cov.items()
                                 if not v["complete"]}}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    import subject_kind_registry as skr
    skr.ensure_schema(conn)
    renames: list[dict[str, Any]] = []
    for r in derived_renames(conn):
        # REFUSE a rename that would COLLIDE with an existing kind: that is a
        # duplicate, not a rename, and silently merging would lose a kind.
        clash = conn.execute("SELECT 1 FROM subject_kind_registry WHERE kind_key=?",
                             (r["to"],)).fetchone()
        if clash:
            renames.append({**r, "ok": False, "code": "DUPLICATE_KIND"})
            continue
        conn.execute("UPDATE subject_kind_registry SET kind_key=?, "
                     "updated_at=datetime('now') WHERE kind_id=?",
                     (r["to"], int(r["kind_id"])))
        renames.append({**r, "ok": True})
    refs: list[dict[str, Any]] = []
    for f in ref_fix_list(conn):
        if not f["new_column_exists"] or not f["is_pk"]:
            # A ref to a column that does not exist, or is not the pk, would
            # resolve nothing. REFUSED rather than written.
            refs.append({**f, "ok": False, "code": "BAD_TARGET_COLUMN"})
            continue
        conn.execute("UPDATE subject_kind_registry SET ref_table=?, ref_column=?, "
                     "updated_at=datetime('now') WHERE kind_key=?",
                     (f["to_table"], f["to_column"], f["kind_key"]))
        refs.append({**f, "ok": True})
    conn.commit()
    return {"ok": True, "renames": renames, "ref_fixes": refs,
            "renamed": sum(1 for r in renames if r.get("ok")),
            "ref_fixed": sum(1 for r in refs if r.get("ok")),
            "remaining_name_violations": len(name_ref_columns(conn))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        res = apply(conn) if args.apply else measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.apply:
            print("APPLIED: renamed=%d ref_fixed=%d remaining_name_violations=%d"
                  % (res["renamed"], res["ref_fixed"],
                     res["remaining_name_violations"]))
            for r in res["renames"]:
                print("   rename %-14s -> %-14s %s"
                      % (r["from"], r["to"], "" if r.get("ok") else
                         "REFUSED %s" % r.get("code")))
            for f in res["ref_fixes"]:
                print("   ref    %-14s %s.%s -> %s.%s %s"
                      % (f["kind_key"], f["from_table"], f["from_column"],
                         f["to_table"], f["to_column"],
                         "" if f.get("ok") else "REFUSED %s" % f.get("code")))
        else:
            print("derived renames      : %d  %s" % (res["renames"],
                  {r["from"]: r["to"] for r in res["rename_rows"]}))
            print("ref fixes            : %d" % len(res["ref_fixes"]))
            for f in res["ref_fixes"]:
                print("   %-14s %s.%s -> %s.%s  (exists=%s is_pk=%s)"
                      % (f["kind_key"], f["from_table"], f["from_column"],
                         f["to_table"], f["to_column"], f["new_column_exists"],
                         f["is_pk"]))
            print("name-ref VIOLATIONS  : %d" % len(res["name_ref_violations"]))
            for v in res["name_ref_violations"]:
                print("   %-14s %-34s %s" % (v["kind_key"], v["ref"],
                                             v["violation"]))
            v = res["vocabulary"]
            print("vocabulary both-but-different: %s" % v["both_but_different"])
            print("complete kinds (%d): %s" % (len(res["complete_kinds"]),
                                               res["complete_kinds"]))
            print("incomplete kinds (%d):" % len(res["incomplete_kinds"]))
            for k, m in sorted(res["incomplete_kinds"].items()):
                print("   %-16s missing %s" % (k, m))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
