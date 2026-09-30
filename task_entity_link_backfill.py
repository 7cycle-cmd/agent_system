# -*- coding: utf-8 -*-
"""task_entity_link_backfill.py — put the TASK -> ENTITY edges into
                                   `task_entity_link` from RECORDED evidence.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
Open work #2. `task_entity_link` had **0 rows** although the table, its UNIQUE
key and its FK are all correct, and `entity_registry.link_task_entity()` already
exists — it simply had NO CALLER. That edge is the skeleton the user described:
    "task ID is the way to connect to chat ID and workflow ID".

WHAT EVIDENCE IS USED, AND WHAT IS REJECTED
-------------------------------------------
USED — `code_registry`'s task columns (measured):
    task_id   non-null 41, ALL resolve to a `dev_task` row, 0 dangling
              (3 real FKs to `dev_task(id)`)
    tacid     a LEGACY tracking label, intersects `dev_task.task_label` on 11 values
    -> 21 of those rows ALSO have an ACTIVE version node, and only those may be
       linked (`link_task_entity` refuses an entity with no active version).

REJECTED — `track_id_audit` (measured, and it is the obvious-looking source):
    it has only 6 rows and EVERY dimension fails to resolve —
    `module='value_transform'`, `table_name='member_data'`, `module='demo'` are in
    NO registry. They are the DECOMPOSITION-EXAMPLE walkthrough rows, not real
    work. A link from them would point at entities that do not exist.

THE `track_id` IS DERIVED, NEVER INVENTED
-----------------------------------------
`task_entity_link.track_id` is `TEXT NOT NULL`, and the user ruled the task id is
the AUTO-INCREMENT id, "not the tracking ID coding now". An INTEGER id therefore
needs a canonical TEXT form, and a compound form (`23|1.1`) would be a NEW format
invented here. So the rule is the one this codebase already applies when a label
may not be unique (`entity_backfill`, `skill_taxonomy_evidence`):

    the LABEL when it is provably UNIQUE and names this same task,
    else the AUTO-INCREMENT ID as text.

Measured: `dev_task.task_label` has 152 distinct values over 165 rows, so `1.1`
alone covers 4 rows. The form used is REPORTED per row, so the ambiguity is
visible instead of hidden.

WHY THE IDENTITY TABLE'S task_id IS NOT WRITTEN HERE
----------------------------------------------------
The identity table may hold rows from OTHER chats, and "the task currently being
linked" is not necessarily "the task that chat was opened for". Writing it here
would assert an association this module cannot prove. The column is set by a
caller that KNOWS the association. This is a DELIBERATE gap, stated so a reader
does not read the empty column as an oversight.

Run:
    .\\.venv\\Scripts\\python.exe task_entity_link_backfill.py --measure
    .\\.venv\\Scripts\\python.exe task_entity_link_backfill.py            # dry run
    .\\.venv\\Scripts\\python.exe task_entity_link_backfill.py --apply
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

# The ONE node kind this task links. `code` is letter `R` in entity_type_registry.
LETTER = "R"

# The dimension columns of `track_id_audit` and where each would have to resolve.
AUDIT_DIMS: tuple[tuple[str, str, str], ...] = (
    ("channel", "channel_registry", "channel_key"),
    ("module", "module_registry", "module_key"),
    ("capability", "capability_registry", "capability_key"),
    ("api_function", "function_registry", "function_key"),
    ("api", "api_registry", "api_key"),
    ("table_name", "db_table_registry", "table_key"),
    ("field_name", "db_field_registry", "field_key"),
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def track_id_for(conn: sqlite3.Connection, task_id: int,
                 tacid: str | None) -> dict[str, Any]:
    """The canonical TEXT `track_id` for a task. DERIVED, with the form reported.

    `label` ONLY when the label is unique AND it really names THIS task id — a
    label that is shared, or that resolves to a different id, must not be used,
    because then two different tasks would share one `track_id` and
    `UNIQUE (track_id, entity_type, entity_ref_id, version, role)` could no
    longer tell them apart.
    """
    tid = int(task_id)
    label = str(tacid or "").strip()
    form, why = "id", "the auto-increment id is unique by construction"
    value = str(tid)
    if label:
        n = conn.execute("SELECT COUNT(*) FROM dev_task WHERE task_label = ?",
                         (label,)).fetchone()[0]
        same = conn.execute("SELECT COUNT(*) FROM dev_task WHERE task_label = ? "
                            "AND id = ?", (label, tid)).fetchone()[0]
        if n == 1 and same == 1:
            form = "label"
            value = label
            why = ("the label is UNIQUE (%d dev_task row) and names task id %d"
                   % (n, tid))
        else:
            why = ("the label %r is NOT uniquely this task (%d dev_task rows "
                   "share it), so the id is used" % (label, n))
    return {"track_id": value, "form": form, "why": why, "task_id": tid}


def _active_node(conn: sqlite3.Connection, ref_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM version_registry WHERE entity_type = ? AND is_active = 1 "
        "AND entity_ref_id = ?", (LETTER, int(ref_id))).fetchone() is not None


def candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every code row that CAN be linked: a resolving task AND an active node."""
    out: list[dict[str, Any]] = []
    if not _table_exists(conn, "code_registry"):
        return out
    for r in conn.execute(
            "SELECT id, tacid, task_id FROM code_registry "
            "WHERE task_id IS NOT NULL ORDER BY id"):
        ref = int(r["id"])
        if not _active_node(conn, ref):
            continue
        if not conn.execute("SELECT 1 FROM dev_task WHERE id = ?",
                            (int(r["task_id"]),)).fetchone():
            continue
        t = track_id_for(conn, int(r["task_id"]), r["tacid"])
        out.append({"entity_ref_id": ref, "entity_type": LETTER, "version": 1,
                    "role": "implements",
                    "track_id": t["track_id"], "form": t["form"],
                    "why": t["why"], "task_id": t["task_id"],
                    "cite": ("code_registry.id=%d.task_id=%d -> dev_task.id=%d "
                             "-> track_id=%s (%s)"
                             % (ref, int(r["task_id"]), int(r["task_id"]),
                                t["track_id"], t["form"]))})
    return out


def refused(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every code row that carries a task but CANNOT be linked, with the reason."""
    out: list[dict[str, Any]] = []
    if not _table_exists(conn, "code_registry"):
        return out
    for r in conn.execute(
            "SELECT id, tacid, task_id, status FROM code_registry ORDER BY id"):
        ref = int(r["id"])
        tid = r["task_id"]
        if tid is None:
            out.append({"entity_ref_id": ref, "code": "NO_TASK_ON_ROW",
                        "why": ("code_registry.task_id is NULL, so there is no "
                                "task to link (tacid=%r is a label, not an id)"
                                % r["tacid"]),
                        "cite": "code_registry.id=%d" % ref})
            continue
        if not conn.execute("SELECT 1 FROM dev_task WHERE id = ?",
                            (int(tid),)).fetchone():
            out.append({"entity_ref_id": ref, "code": "NO_DEV_TASK_ROW",
                        "why": "no dev_task row has id=%s" % tid,
                        "cite": "code_registry.id=%d.task_id=%s" % (ref, tid)})
            continue
        if not _active_node(conn, ref):
            out.append({"entity_ref_id": ref, "code": "NO_ACTIVE_NODE",
                        "why": ("task_id=%s resolves, but this code row has no "
                                "ACTIVE version_registry node, so "
                                "link_task_entity would refuse it" % tid),
                        "cite": "code_registry.id=%d" % ref})
    return out


def audit_source_rejected(conn: sqlite3.Connection) -> dict[str, Any]:
    """`track_id_audit` as a source: REJECTED, with the failing values named.

    ONE RETURN SHAPE, ALWAYS. MEASURED (2026-09-27): the early path returned
    `{ok, reason, failures}` while the main path returned
    `{ok, rows, resolved, failures, verdict}`. A caller reading
    `audit["resolved"]` therefore CRASHED with `KeyError: 'resolved'` whenever
    the table was absent -- and MEASURED, the live DB has NO `track_id_audit`,
    so the early path is the one actually taken. `_proof_task_entity_link.py:141`
    is that caller.

    A function with two return shapes is a latent crash for every caller. The
    absent-table case is not a special case: it is the SAME answer with zero
    rows, so it returns the SAME keys.
    """
    if not _table_exists(conn, "track_id_audit"):
        return {"ok": True, "rows": 0, "resolved": 0, "failures": [],
                "reason": "track_id_audit is absent",
                "verdict": ("REJECTED as a source: track_id_audit is absent, "
                            "so 0 of 0 dimension values resolve")}
    rows = [dict(r) for r in conn.execute(
        "SELECT track_id, channel, module, capability, api_function, api, "
        "table_name, field_name FROM track_id_audit ORDER BY audit_id")]
    failures: list[dict[str, Any]] = []
    resolved = 0
    for r in rows:
        for dim, tbl, col in AUDIT_DIMS:
            val = r.get(dim)
            if val is None:
                continue
            if not _table_exists(conn, tbl):
                failures.append({"track_id": r["track_id"], "dim": dim,
                                 "value": val, "why": "%s is absent" % tbl})
                continue
            if conn.execute("SELECT 1 FROM %s WHERE %s = ?" % (tbl, col),
                            (val,)).fetchone():
                resolved += 1
            else:
                failures.append(
                    {"track_id": r["track_id"], "dim": dim, "value": val,
                     "why": "no %s row has %s=%r" % (tbl, col, val)})
    return {"ok": resolved == 0, "rows": len(rows), "resolved": resolved,
            "failures": failures,
            "verdict": ("REJECTED as a source: %d of %d dimension values resolve"
                        % (resolved, len(failures) + resolved))}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    cand = candidates(conn)
    ref = refused(conn)
    audit = audit_source_rejected(conn)
    forms: dict[str, int] = {}
    for c in cand:
        forms[c["form"]] = forms.get(c["form"], 0) + 1
    by_code: dict[str, int] = {}
    for r in ref:
        by_code[r["code"]] = by_code.get(r["code"], 0) + 1
    existing = conn.execute("SELECT COUNT(*) FROM task_entity_link").fetchone()[0] \
        if _table_exists(conn, "task_entity_link") else 0
    return {"candidates": len(cand), "forms": forms,
            "refused": len(ref), "refused_by_code": by_code,
            "existing_links": existing, "audit_source": audit}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Link via `entity_registry.link_task_entity` — the EXISTING writer.

    NO SECOND IMPLEMENTATION. A private INSERT here would be a second place that
    knows the table's UNIQUE key and its guards, and the two would drift.
    """
    import entity_registry as er
    created = 0
    existed = 0
    refused_now: list[dict[str, Any]] = []
    for c in candidates(conn):
        res = er.link_task_entity(conn, c["track_id"], c["entity_type"],
                                  int(c["entity_ref_id"]),
                                  version=c.get("version"),
                                  role=c.get("role"))
        if res.get("ok") and res.get("created"):
            created += 1
        elif res.get("ok"):
            existed += 1
        else:
            refused_now.append({"entity_ref_id": c["entity_ref_id"],
                                "why": res.get("why"), "cite": c["cite"]})
    return {"ok": True, "created": created, "existed": existed,
            "refused_now": refused_now,
            "total": conn.execute("SELECT COUNT(*) FROM task_entity_link"
                                  ).fetchone()[0]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.measure:
            res = measure(conn)
        elif args.apply:
            res = apply(conn)
        else:
            cand = candidates(conn)
            res = {"candidates": len(cand),
                   "forms": {f: sum(1 for c in cand if c["form"] == f)
                             for f in {c["form"] for c in cand}},
                   "refused": len(refused(conn)),
                   "sample": cand[:5], "refused_sample": refused(conn)[:5]}
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.measure:
            print("candidates (linkable) : %d" % res["candidates"])
            print("  track_id forms      : %s" % res["forms"])
            print("refused               : %d  %s"
                  % (res["refused"], res["refused_by_code"]))
            print("existing links        : %d" % res["existing_links"])
            a = res["audit_source"]
            print("track_id_audit source : %s" % a.get("verdict"))
            for f in (a.get("failures") or [])[:4]:
                print("   %-8s %-12s %-18s %s"
                      % (f["track_id"], f["dim"], f["value"], f["why"]))
        elif args.apply:
            print("APPLIED: created=%d existed=%d total=%d"
                  % (res["created"], res["existed"], res["total"]))
            for r in res["refused_now"]:
                print("   REFUSED %s: %s" % (r["entity_ref_id"], r["why"]))
        else:
            print("DRY RUN: candidates=%d refused=%d forms=%s"
                  % (res["candidates"], res["refused"], res["forms"]))
            for c in res["sample"]:
                print("   %s-%d-%s track_id=%s (%s)"
                      % (c["entity_type"], c["entity_ref_id"], c["version"],
                         c["track_id"], c["form"]))
            for r in res["refused_sample"]:
                print("   REFUSED %s %s" % (r["entity_ref_id"], r["why"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
