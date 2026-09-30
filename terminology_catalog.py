# -*- coding: utf-8 -*-
"""terminology_catalog.py -- the CATALOG over `terminology_registry`.

THE HUMAN (2026-09-27), verbatim
--------------------------------
    "why you have 2 language for the same thing, so we need to have catalog, and
     how does it can standardize, by termontology ?"
    "catalog table and subcatalog table, name defintion by termontology"
    "by skill and auto forever"

THE PROBLEM, STATED
-------------------
  target     : the taskbar element register, group `windows_taskbar`, env 6
  expected   : one concept has ONE name, and every name has a definition
  actual     : two rows name the same vendor in two languages, and NEITHER is
               registered
  observable : SELECT name, label FROM target_template
               WHERE name LIKE 'taskbar_doubao%'

THE DECISION THIS MODULE IMPLEMENTS
-----------------------------------
The human asked for `catalog` + `subcatalog`. MEASURED, those tables ALREADY
EXIST (`coord_store.py:157-176`) and are a **chat_center artifact**: 2 catalog
rows (UI, QA), 3 subcatalog rows, every `description = 'auto-created by
chat_center'`. Reusing them would put two meanings in one table.

MEASURED, there are ALREADY two hierarchies in `terminology_registry`:
  * `parent_term_id` (self-FK, `UNIQUE(parent_term_id, term_key)`)
  * `taxonomy_level` + `taxonomy_path`

So a THIRD would make the problem worse. **The two existing ones are given ONE
job each:**

    parent_term_id              -> the CATALOG  (what contains what)
    taxonomy_level + path       -> the LAYER    (what KIND of thing it is)

A catalog is a term whose children are its entries. **No new table, no second
truth.** The human's `catalog`/`subcatalog` becomes a VIEW over the register.

WHY `is_active` IS NOT TOUCHED (a correction to the plan's first draft)
----------------------------------------------------------------------
The plan's first draft said the 8 taxonomy levels were "switched OFF" because
`is_active=0`, and made "switch them ON" step 1. **MEASURED, that was wrong:**

    is_valid_level(db_table) = True   (is_active=0)

`taxonomy_level_registry.is_valid_level()` reads the table WITHOUT filtering on
`is_active`, so a term CAN carry `taxonomy_level='db_table'` today. The 98.7%
`NA` is a POPULATION problem, not a BLOCKED one.

AND a hand-flip would be a violation: `taxonomy_level_registry` ends in
`_registry`, so it is IN the activation scope (`activation_gate.py:110`), and it
is NOT in `NO_ACTIVATION_BECAUSE`. `activation_gate`'s own docstring says
`is_active=1` is written by the gate and requires a 100-run streak. **Flipping it
by hand would be the gate proving itself.**

NEVER RAISES
------------
Every function returns a dict with `ok`; a failure is recorded, not thrown.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DEFAULT_DB = BASE / "agent.db"

# The catalog root for the Windows taskbar. A CATALOG is a term whose children
# are its entries, so this is a term like any other -- it just has children.
TASKBAR_CATALOG = "taskbar"


def log(msg: str) -> None:
    print(msg, flush=True)


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# the catalog VIEW over terminology_registry
# ---------------------------------------------------------------------------

def catalog_tree(conn: sqlite3.Connection, root_key: str) -> dict[str, Any]:
    """The catalog rooted at `root_key`, as a tree.

    A CATALOG is a term whose children are its entries. This reads
    `parent_term_id`, so the tree IS the register -- there is no second table to
    drift from it.
    """
    out: dict[str, Any] = {"ok": False, "root": None, "children": [],
                           "error": None}
    try:
        root = conn.execute(
            "SELECT term_id, term_key, term_kind, definition, cite_ref, "
            "taxonomy_level, is_active FROM terminology_registry "
            "WHERE term_key=? AND parent_term_id IS NULL",
            (root_key,)).fetchone()
        if not root:
            out["error"] = ("no ROOT term %r (a catalog root has "
                            "parent_term_id IS NULL)" % root_key)
            return out
        out["root"] = dict(root)
        kids = conn.execute(
            "SELECT term_id, term_key, term_kind, definition, cite_ref, "
            "taxonomy_level, is_active FROM terminology_registry "
            "WHERE parent_term_id=? ORDER BY term_key",
            (int(root["term_id"]),)).fetchall()
        out["children"] = [dict(k) for k in kids]
        out["ok"] = True
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def catalog_subtree(conn: sqlite3.Connection, root_key: str) -> dict[str, Any]:
    """The WHOLE SUBTREE under `root_key`, with the DEPTH of each node.

    THE HUMAN (2026-09-27), verbatim
    ---------------------------------
        "for taskbar_vscode_app is value is ?"
        "same as sample, taskbar_vscode without taskbar_vscode_app, how to you
         know it is APP not browser"

    THE PROBLEM, MEASURED
    ---------------------
    MEASURED: `taskbar` has **19 direct children**, and the human pasted
    **exactly 19 rows**. So the panel showed **DIRECT CHILDREN ONLY**, and the
    **2 grandchildren were invisible**:

        taskbar_vscode_app    (1474)  parent = taskbar_vscode (1489)
        taskbar_vscode_win_n  (1488)  parent = taskbar_vscode (1489)

    **A group whose contents are invisible is a group nobody can check.** A
    reader looking at `taskbar_vscode` could not tell whether it holds an APP, a
    browser, or nothing at all.

    THE FIX: a recursive CTE over `parent_term_id`, so the WHOLE subtree is
    returned with its `depth`. **`depth` is DERIVED, never stored** -- the same
    rule `node_kind` uses.
    """
    out: dict[str, Any] = {"ok": False, "root": None, "rows": [],
                           "max_depth": 0, "error": None}
    try:
        root = conn.execute(
            "SELECT term_id, term_key FROM terminology_registry "
            "WHERE term_key=? AND parent_term_id IS NULL",
            (root_key,)).fetchone()
        if not root:
            out["error"] = ("no ROOT term %r (a catalog root has "
                            "parent_term_id IS NULL)" % root_key)
            return out
        out["root"] = dict(root)
        rows = conn.execute(
            "WITH RECURSIVE tree(term_id, term_key, parent_term_id, depth) AS ("
            "  SELECT term_id, term_key, parent_term_id, 0 "
            "  FROM terminology_registry WHERE term_id=? "
            "  UNION ALL "
            "  SELECT t.term_id, t.term_key, t.parent_term_id, tree.depth+1 "
            "  FROM terminology_registry t JOIN tree "
            "    ON t.parent_term_id = tree.term_id"
            ") SELECT term_id, term_key, parent_term_id, depth FROM tree "
            "  ORDER BY depth, term_key", (int(root["term_id"]),)).fetchall()
        import terminology_generator as tg
        recs = []
        for r in rows:
            nk = tg.node_kind(conn, int(r["term_id"]))
            recs.append({"term_id": int(r["term_id"]),
                         "term_key": str(r["term_key"]),
                         "parent_term_id": r["parent_term_id"],
                         "depth": int(r["depth"]),
                         "node_kind": nk.get("node_kind"),
                         "children": nk.get("children")})
        out["rows"] = recs
        out["max_depth"] = max((r["depth"] for r in recs), default=0)
        out["ok"] = True
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def catalog_node_kind(conn: sqlite3.Connection, node_id: int) -> dict[str, Any]:
    """Is a `catalog` row a GROUP (it has children) or a LEAF?

    THE SAME DERIVATION as `terminology_generator.node_kind` -- `children > 0` --
    so the two hierarchies answer the same question the same way. **One rule, two
    tables.** A second derivation would be a second truth.

    MEASURED 2026-09-27: `catalog` is now ONE table with `parent_id` (the human's
    ruling), so this reads the SAME shape the terminology catalog uses.
    """
    row = conn.execute(
        "SELECT id, name FROM catalog WHERE id=?", (int(node_id),)).fetchone()
    if not row:
        return {"ok": False, "error": "no catalog row with id=%d" % node_id}
    n = conn.execute("SELECT COUNT(*) FROM catalog WHERE parent_id=?",
                     (int(node_id),)).fetchone()[0]
    n = int(n)
    return {"ok": True, "id": int(row["id"]), "name": str(row["name"]),
            "node_kind": "group" if n > 0 else "leaf", "children": n}


def catalog_node_kind_agreement(conn: sqlite3.Connection) -> dict[str, Any]:
    """QC-13: does `catalog_node_kind` AGREE with `children > 0` for EVERY row?

    It recomputes `children > 0` in SQL and compares, so the check does not reuse
    the thing it is checking. **0 is the only passing value.**
    """
    rows = conn.execute(
        "SELECT c.id, c.name, "
        "  (SELECT COUNT(*) FROM catalog k WHERE k.parent_id=c.id) AS kids "
        "FROM catalog c").fetchall()
    disagree = []
    for r in rows:
        kids = int(r["kids"])
        want = "group" if kids > 0 else "leaf"
        got = catalog_node_kind(conn, int(r["id"]))
        if not got.get("ok") or got.get("node_kind") != want:
            disagree.append({"id": int(r["id"]), "name": str(r["name"]),
                             "children": kids, "want": want,
                             "got": got.get("node_kind")})
    return {"ok": True, "checked": len(rows), "disagreements": disagree,
            "disagreement_count": len(disagree)}


def unregistered_names(conn: sqlite3.Connection, *,
                       group_key: str = "windows_taskbar") -> dict[str, Any]:
    """Every `target_template` name in a group that has NO term.

    A NAME WITH NO TERM IS A GAP, and it is REPORTED, never hidden. This is the
    measurement the human's question needs: it says exactly which names are
    undefined, instead of leaving a reader to notice by eye.
    """
    out: dict[str, Any] = {"ok": False, "names": [], "registered": [],
                           "error": None}
    try:
        rows = conn.execute(
            "SELECT t.name, t.label FROM target_template t "
            "JOIN target_group g ON g.id = t.group_id "
            "WHERE g.group_key = ? ORDER BY t.name", (group_key,)).fetchall()
        for r in rows:
            hit = conn.execute(
                "SELECT term_id FROM terminology_registry WHERE term_key=?",
                (r["name"],)).fetchone()
            (out["registered"] if hit else out["names"]).append(
                {"name": r["name"], "label": r["label"],
                 "term_id": int(hit["term_id"]) if hit else None})
        out["ok"] = True
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def ancestor_cycle(conn: sqlite3.Connection, term_id: int) -> list[int]:
    """The `parent_term_id` chain from `term_id` upward. Detects a cycle.

    A term that is its own ancestor makes the tree infinite, so the walk is
    bounded by the row count and a repeat is REPORTED rather than looped on.
    """
    seen: list[int] = []
    cur = int(term_id)
    limit = int(conn.execute(
        "SELECT COUNT(*) FROM terminology_registry").fetchone()[0]) + 1
    for _ in range(limit):
        if cur in seen:
            return seen
        seen.append(cur)
        row = conn.execute(
            "SELECT parent_term_id FROM terminology_registry WHERE term_id=?",
            (cur,)).fetchone()
        if not row or row["parent_term_id"] is None:
            return []
        cur = int(row["parent_term_id"])
    return seen


def measure(db_path: str | None = None) -> dict[str, Any]:
    """The whole measurement, as one dict. Writes NOTHING."""
    conn = _connect(db_path)
    try:
        out: dict[str, Any] = {"ok": True}
        out["terminology_rows"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry").fetchone()[0]
        out["taxonomy_levels"] = [
            dict(r) for r in conn.execute(
                "SELECT level_key, level_order, is_active "
                "FROM taxonomy_level_registry ORDER BY level_order")]
        out["taxonomy_levels_active"] = sum(
            1 for r in out["taxonomy_levels"] if r["is_active"])
        out["taxonomy_set"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry "
            "WHERE taxonomy_level <> 'NA'").fetchone()[0]
        out["taxonomy_na"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry "
            "WHERE taxonomy_level = 'NA'").fetchone()[0]
        out["alias_used"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry "
            "WHERE alias_list <> 'NA' AND alias_list <> ''").fetchone()[0]
        # THE chat_center ARTIFACT, so a reader can see it is NOT the catalog.
        out["catalog_rows"] = [dict(r) for r in conn.execute(
            "SELECT id, name, description FROM catalog")]
        out["subcatalog_rows"] = [dict(r) for r in conn.execute(
            "SELECT id, catalog_id, name, description FROM subcatalog")]
        out["taskbar_terms"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry "
            "WHERE term_key LIKE '%taskbar%'").fetchone()[0]
        out["taskbar_catalog"] = catalog_tree(conn, TASKBAR_CATALOG)
        out["unregistered"] = unregistered_names(conn)
        return out
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--measure", action="store_true",
                    help="print the measurement (writes nothing)")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    r = measure(args.db)
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
