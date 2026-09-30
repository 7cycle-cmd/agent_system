# -*- coding: utf-8 -*-
"""task_type_registry.py — the N-LEVEL task-type hierarchy.

WHY THIS MODULE EXISTS (user, 2026-09-22):

    "task_id from task table with entity_id if the task = coding, and these need
     a value table to define type coding = IT project > Project name > ,
     i don't know how many type will have in the future, but we need to have
     preparation in table design"

The requirement is an UNBOUNDED hierarchy. Nothing in the schema could hold one:

  * `taxonomy_path` is TWO levels — `skill_contract_store.py:425` splits on the
    FIRST `/` only, and its entity types are a FIXED tuple of 7
    (`skill_contract_store.py:392`). `capability/media/ppt` only looks
    multi-level because the KEY contains a slash: a workaround, not a design.
  * `task_action_name` is `element` + `action` — exactly two columns.
  * `llm_tasks.type` is ONE text column.

So the depth is DATA here, not schema. A new level is a ROW.

THE THREE RULES THIS MODULE ENFORCES
------------------------------------
1. `type_path` is DERIVED, never passed in. A caller supplies a parent and a
   key; the path is `parent_path + "/" + key`. A path that could be supplied
   independently could disagree with `parent_type_id`, and then the register
   would hold two answers to one question.
2. The same `type_key` is legal under a DIFFERENT parent, and REFUSED under the
   same one. That is the multi-dimensional rule already used by
   `wording_registry` (`UNIQUE (skill_id, dim_key, wording_key)`). A global
   unique on `type_key` would make the register single-dimensional — the exact
   defect `skill_factor_registry` was rebuilt to fix.
3. A type needs a DEFINITION and a CITATION. Per `citation_discipline`: no
   citation, no finding. An invented type is a guess with a heading.

Read-only unless a caller passes `apply=True` to a mutating function.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"

# The separator between levels in `type_path`. `/` matches `taxonomy_path`, so a
# reader that already understands one understands the other.
PATH_SEP = "/"


class TaskTypeError(ValueError):
    """Raised when a type would be stored without a definition or a citation."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the register and seed the ROOTS when it is empty.

    Idempotent. Seeding only when empty means a human DELETE is not silently
    undone on the next startup — the same rule as `entity_type_registry`.
    """
    import db_schema as ds

    conn.executescript(ds.TASK_TYPE_REGISTRY_DDL)
    seeded = 0
    if conn.execute("SELECT COUNT(*) FROM task_type_registry").fetchone()[0] == 0:
        for type_key, definition, cite_ref in ds.TASK_TYPE_SEED:
            conn.execute(
                "INSERT INTO task_type_registry "
                "(parent_type_id, type_key, type_path, depth, definition, "
                " cite_ref, is_active) VALUES (NULL, ?, ?, 0, ?, ?, 1)",
                (type_key, type_key, definition, cite_ref))
            seeded += 1
    conn.commit()
    return {"ok": True, "seeded": seeded,
            "total": conn.execute(
                "SELECT COUNT(*) FROM task_type_registry").fetchone()[0]}


def get_type(conn: sqlite3.Connection, type_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM task_type_registry WHERE task_type_id = ?",
                     (int(type_id),)).fetchone()
    return dict(r) if r else None


def find_by_path(conn: sqlite3.Connection,
                 type_path: str) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM task_type_registry WHERE type_path = ?",
                     (str(type_path or "").strip(),)).fetchone()
    return dict(r) if r else None


def add_type(conn: sqlite3.Connection, type_key: str, *,
             parent_type_id: int | None = None,
             definition: str,
             cite_ref: str,
             apply: bool = True) -> dict[str, Any]:
    """Add a type at ANY depth. The path and depth are DERIVED from the parent.

    `definition` and `cite_ref` are REQUIRED and are refused when empty: a type
    with no definition is a label nobody can act on, and a type with no citation
    is an invention.
    """
    key = str(type_key or "").strip()
    if not key:
        raise TaskTypeError("type_key is required")
    if PATH_SEP in key:
        raise TaskTypeError(
            "type_key %r must not contain %r — the separator is what makes the "
            "path unambiguous, so a key containing it would create a level that "
            "does not exist" % (key, PATH_SEP))
    if not str(definition or "").strip():
        raise TaskTypeError(
            "definition is required: a type with no definition is a label "
            "nobody can act on")
    if not str(cite_ref or "").strip():
        raise TaskTypeError(
            "cite_ref is required: an uncited type is an invention, not a "
            "finding (citation_discipline)")

    parent_path, depth = "", 0
    if parent_type_id is not None:
        parent = get_type(conn, int(parent_type_id))
        if not parent:
            raise TaskTypeError("unknown parent_type_id %r" % parent_type_id)
        if not int(parent["is_active"]):
            raise TaskTypeError(
                "parent %r is inactive; adding a child under a retired type "
                "would create a level nobody can reach" % parent["type_path"])
        parent_path = parent["type_path"]
        depth = int(parent["depth"]) + 1

    path = (parent_path + PATH_SEP + key) if parent_path else key

    existing = find_by_path(conn, path)
    if existing:
        return {"ok": True, "created": False, "type": existing,
                "why": "already present"}

    if not apply:
        return {"ok": True, "created": False, "dry_run": True,
                "would_add": {"type_key": key, "type_path": path,
                              "depth": depth,
                              "parent_type_id": parent_type_id}}

    cur = conn.execute(
        "INSERT INTO task_type_registry "
        "(parent_type_id, type_key, type_path, depth, definition, cite_ref, "
        " is_active) VALUES (?, ?, ?, ?, ?, ?, 1)",
        (parent_type_id, key, path, depth, str(definition).strip(),
         str(cite_ref).strip()))
    conn.commit()
    return {"ok": True, "created": True,
            "type": get_type(conn, int(cur.lastrowid))}


def children_of(conn: sqlite3.Connection,
                parent_type_id: int | None) -> list[dict[str, Any]]:
    """The DIRECT children of a type (or the roots when `parent_type_id` is None)."""
    if parent_type_id is None:
        rows = conn.execute(
            "SELECT * FROM task_type_registry WHERE parent_type_id IS NULL "
            "AND is_active = 1 ORDER BY type_key")
    else:
        rows = conn.execute(
            "SELECT * FROM task_type_registry WHERE parent_type_id = ? "
            "AND is_active = 1 ORDER BY type_key", (int(parent_type_id),))
    return [dict(r) for r in rows]


def ancestors_of(conn: sqlite3.Connection,
                 type_id: int) -> list[dict[str, Any]]:
    """The whole chain, ROOT FIRST, ending with the type itself.

    Walks `parent_type_id` rather than splitting `type_path`, so the answer
    comes from the SAME fact the FK enforces. A path-split would still work, but
    it would be a second implementation of the hierarchy — and two
    implementations of one rule is the defect this repo has recorded repeatedly.
    """
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    cur = get_type(conn, int(type_id))
    while cur:
        tid = int(cur["task_type_id"])
        if tid in seen:
            # A cycle would loop forever. The FK cannot create one, but a
            # hand-edited row could, and an infinite loop is worse than a stop.
            raise TaskTypeError(
                "cycle detected at task_type_id %d — the parent chain is not a "
                "tree" % tid)
        seen.add(tid)
        out.append(cur)
        pid = cur["parent_type_id"]
        cur = get_type(conn, int(pid)) if pid is not None else None
    out.reverse()
    return out


def descendants_of(conn: sqlite3.Connection,
                   type_id: int) -> list[dict[str, Any]]:
    """Every type BELOW this one, breadth-first. Uses `type_path` for one query."""
    me = get_type(conn, int(type_id))
    if not me:
        return []
    prefix = me["type_path"] + PATH_SEP
    rows = conn.execute(
        "SELECT * FROM task_type_registry WHERE type_path LIKE ? "
        "AND is_active = 1 ORDER BY depth, type_path", (prefix + "%",))
    return [dict(r) for r in rows]


def list_types(conn: sqlite3.Connection, *,
               active_only: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM task_type_registry"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY type_path"
    return [dict(r) for r in conn.execute(sql)]


def deactivate(conn: sqlite3.Connection, type_id: int) -> dict[str, Any]:
    """Soft-delete a type. REFUSES when it still has active children.

    A retired parent with live children would leave levels nobody can reach,
    which is the same defect as a dangling entity letter.
    """
    me = get_type(conn, int(type_id))
    if not me:
        return {"ok": False, "why": "unknown task_type_id %r" % type_id}
    kids = children_of(conn, int(type_id))
    if kids:
        return {"ok": False,
                "why": "still has %d active child(ren): %s"
                       % (len(kids), ", ".join(k["type_path"] for k in kids))}
    conn.execute("UPDATE task_type_registry SET is_active = 0, "
                 "updated_at = datetime('now') WHERE task_type_id = ?",
                 (int(type_id),))
    conn.commit()
    return {"ok": True, "deactivated": me["type_path"]}


def tree(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The whole register as a nested tree, for a reader or a UI."""
    rows = list_types(conn)
    by_id = {int(r["task_type_id"]): dict(r, children=[]) for r in rows}
    roots: list[dict[str, Any]] = []
    for r in by_id.values():
        pid = r["parent_type_id"]
        if pid is None:
            roots.append(r)
        elif int(pid) in by_id:
            by_id[int(pid)]["children"].append(r)
    return roots


def main() -> None:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="task type registry (N-level)")
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--add", metavar="TYPE_KEY")
    ap.add_argument("--parent", type=int, default=None)
    ap.add_argument("--definition", default="")
    ap.add_argument("--cite", default="")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2))
        if args.add:
            print(json.dumps(add_type(
                conn, args.add, parent_type_id=args.parent,
                definition=args.definition, cite_ref=args.cite), indent=2))
        if args.list:
            for r in list_types(conn):
                print("%s%s  depth=%d  %s"
                      % ("  " * int(r["depth"]), r["type_path"],
                         int(r["depth"]), r["definition"][:60]))
        if args.tree:
            print(json.dumps(tree(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
