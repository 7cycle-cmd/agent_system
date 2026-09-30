# -*- coding: utf-8 -*-
"""goal_resolver.py — MATCH a goal to service + workflow. A ROUTER, NOT a capability.

THE USER (2026-09-24)
---------------------
    "match the purpose by `goal_inference` + `goal_resolver (workflow modules)"
    "so we can assign workflow to task module or not"

WHY THIS IS NOT A CAPABILITY — the refusal, with its evidence
------------------------------------------------------------
`capability_kind_registry` declares FIVE kinds, each with a definition, and every
one describes a VERB A PROVIDER PERFORMS (reasons / captures / acts / speaks /
produces-or-gates code). Matching a goal against a register is a LOOKUP over
rows this system already holds — no provider performs it, and NO declared kind
describes it. Registering it as a capability would require INVENTING a 6th kind
with no evidence, so this module does NOT, and `register_router()` returns
`NOT_A_CAPABILITY` if asked. It demonstrates itself instead through
`capability_binding`, whose CHECK already allows `subject_kind='route'`.

THE ASSIGNMENT LINK (measured to be MISSING)
-------------------------------------------
    dev_task (15 columns, 165 rows)   -> NO workflow column
    identity_registry.workflow_id     -> EXISTS (NOT NULL)

So a workflow can be attached to an IDENTITY but NOT to a TASK. `ensure_task_
workflow_column()` adds the ONE nullable column that closes it, as a LAZY FK (no
native FK clause) — the DECLARED repo pattern, used by 95 of 178 tables.

NO DEFAULT ROUTE
----------------
`resolve()` returns a route or `NO_ROUTE` naming the goal it could not match. It
never falls back to a default workflow: a default would make every unmatchable
goal look routed.

Run:
    .\\.venv\\Scripts\\python.exe goal_resolver.py --measure
    .\\.venv\\Scripts\\python.exe goal_resolver.py --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

ROUTER_KEY = "goal_resolver"

# The tables a router reads. MEASURED: `purpose_route_registry` is the live one
# (1 row). A `goal_route_registry` does NOT exist, and creating it would be a
# SECOND truth for the same question — so the rename is ADDITIVE (see the plan):
# the term `goal` is registered and the existing register is read.
ROUTE_TABLE_CANDIDATES: tuple[str, ...] = ("goal_route_registry",
                                           "purpose_route_registry")


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


def route_table(conn: sqlite3.Connection) -> str:
    """The register a route is read from — the FIRST that exists, named.

    READ, not assumed: if a `goal_route_registry` is ever created it wins,
    otherwise the live `purpose_route_registry` is used. The caller always knows
    which table answered.
    """
    for t in ROUTE_TABLE_CANDIDATES:
        if _table_exists(conn, t):
            return t
    return ""


# --------------------------------------------------------------------------
# the router
# --------------------------------------------------------------------------
def resolve(conn: sqlite3.Connection, goal: str) -> dict[str, Any]:
    """Match a GOAL against the route register. NO default, NO fuzzy match.

    The match is EXACT on the recorded goal text, because a near miss would
    route a chat to a service that did not answer its goal — and the route is
    what a task is then assigned to.
    """
    g = str(goal or "").strip()
    if not g:
        return {"ok": False, "code": "EMPTY_GOAL", "goal": g,
                "why": "an empty goal cannot be matched",
                "cite": "measured: goal is empty"}
    table = route_table(conn)
    if not table:
        return {"ok": False, "code": "NO_ROUTE_registry", "goal": g,
                "why": ("no route register exists (tried %s), so no goal can be "
                        "matched" % list(ROUTE_TABLE_CANDIDATES)),
                "cite": "measured: %s absent from sqlite_master"
                         % list(ROUTE_TABLE_CANDIDATES)}
    cols = _columns(conn, table)
    goal_col = "purpose_key" if "purpose_key" in cols else ""
    if not goal_col:
        return {"ok": False, "code": "NO_GOAL_COLUMN", "goal": g, "table": table,
                "why": "%s holds no goal column (columns: %s)"
                       % (table, sorted(cols)),
                "cite": "measured: PRAGMA table_info(%s)" % table}
    row = conn.execute(
        "SELECT * FROM %s WHERE %s = ? AND is_active = 1" % (table, goal_col),
        (g,)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_ROUTE", "goal": g, "table": table,
                "why": ("no active route in %s matches the goal %r — the goal "
                        "cannot be served, and NO default route is substituted"
                        % (table, g)),
                "cite": "measured: SELECT * FROM %s WHERE %s = %r"
                        % (table, goal_col, g)}
    return {"ok": True, "goal": g, "table": table,
            "route_key": row["route_key"] if "route_key" in row.keys() else None,
            "ticket_origin": row["ticket_origin"] if "ticket_origin" in row.keys() else None,
            "workflow_key": row["workflow_key"] if "workflow_key" in row.keys() else None,
            "cite": "measured: %s.%s = %r" % (table, goal_col, g)}


def workflow_id_for(conn: sqlite3.Connection, workflow_key: str) -> dict[str, Any]:
    """The `workflow_id` a `workflow_key` names. REFUSES an unknown key."""
    key = str(workflow_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_WORKFLOW_KEY"}
    if not _table_exists(conn, "workflow_registry"):
        return {"ok": False, "code": "NO_WORKFLOW_TABLE"}
    row = conn.execute("SELECT workflow_id, workflow_key, is_active "
                       "FROM workflow_registry WHERE workflow_key = ?",
                       (key,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_WORKFLOW", "workflow_key": key,
                "cite": "measured: workflow_registry.workflow_key %r absent" % key}
    if not int(row["is_active"]):
        return {"ok": False, "code": "WORKFLOW_INACTIVE", "workflow_key": key,
                "workflow_id": int(row["workflow_id"]),
                "cite": "measured: workflow_registry.is_active=0 for %r" % key}
    return {"ok": True, "workflow_id": int(row["workflow_id"]),
            "workflow_key": key,
            "cite": "measured: workflow_registry.workflow_key = %r" % key}


def resolve_goal_to_workflow(conn: sqlite3.Connection, goal: str
                             ) -> dict[str, Any]:
    """goal -> route -> service + workflow_id. THE USER'S CHAIN, end to end."""
    r = resolve(conn, goal)
    if not r["ok"]:
        return r
    w = workflow_id_for(conn, r["workflow_key"])
    if not w["ok"]:
        return {"ok": False, "code": "ROUTE_WORKFLOW_UNRESOLVED",
                "goal": goal, "route_key": r["route_key"],
                "workflow_key": r["workflow_key"], "why": w.get("cite"),
                "cite": "%s; %s" % (r["cite"], w.get("cite"))}
    return {"ok": True, "goal": goal, "route_key": r["route_key"],
            "ticket_origin": r["ticket_origin"], "workflow_key": w["workflow_key"],
            "workflow_id": w["workflow_id"],
            "cite": "%s; %s" % (r["cite"], w["cite"])}


# --------------------------------------------------------------------------
# the MISSING assignment link: a workflow ON A TASK
# --------------------------------------------------------------------------
def ensure_task_workflow_column(conn: sqlite3.Connection) -> dict[str, Any]:
    """`dev_task.workflow_id` — attach a workflow to a TASK.

    MEASURED to be missing: `dev_task` has 15 columns and none is a workflow.
    Nullable + LAZY FK (no native FK clause), the DECLARED pattern.
    """
    if not _table_exists(conn, "dev_task"):
        return {"ok": False, "code": "NO_TASK_TABLE"}
    if "workflow_id" in _columns(conn, "dev_task"):
        return {"ok": True, "created": False, "column": "workflow_id"}
    conn.execute("ALTER TABLE dev_task ADD COLUMN workflow_id INTEGER")
    conn.commit()
    return {"ok": True, "created": True, "column": "workflow_id",
            "lazy_fk": "workflow_registry.workflow_id",
            "why": ("NULL means NOT ASSIGNED — it is the NOT-ANSWERED sentinel, "
                    "not a default workflow")}


def assign_workflow_to_task(conn: sqlite3.Connection, task_id: int,
                            workflow_key: str, *, cite_ref: str) -> dict[str, Any]:
    """Assign a workflow to a task. REFUSES a workflow no route resolves to.

    THE RULE: a task may only be given a workflow that some goal can actually
    reach, because otherwise the assignment names a path no chat can arrive by.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    cols = _columns(conn, "dev_task") if _table_exists(conn, "dev_task") else set()
    if "workflow_id" not in cols:
        return {"ok": False, "code": "NO_WORKFLOW_COLUMN",
                "why": "run `--apply` to add dev_task.workflow_id first"}
    t = conn.execute("SELECT id, task_label FROM dev_task WHERE id = ?",
                     (int(task_id),)).fetchone()
    if not t:
        return {"ok": False, "code": "UNKNOWN_TASK", "task_id": int(task_id)}
    w = workflow_id_for(conn, workflow_key)
    if not w["ok"]:
        return dict(w, task_id=int(task_id))
    # IS THIS WORKFLOW REACHABLE BY ANY GOAL? A route must name it.
    table = route_table(conn)
    if not table:
        return {"ok": False, "code": "NO_ROUTE_registry",
                "why": "no route register exists, so the workflow's "
                       "reachability cannot be established"}
    cols_r = _columns(conn, table)
    if "workflow_key" not in cols_r:
        return {"ok": False, "code": "NO_WORKFLOW_COLUMN_IN_ROUTE",
                "table": table}
    reach = [r[0] for r in conn.execute(
        "SELECT route_key FROM %s WHERE workflow_key = ? AND is_active = 1"
        % table, (w["workflow_key"],))]
    if not reach:
        return {"ok": False, "code": "WORKFLOW_UNREACHABLE",
                "task_id": int(task_id), "workflow_key": w["workflow_key"],
                "why": ("no active route names workflow %r, so no goal can "
                        "arrive at it — assigning it would name a path no chat "
                        "can reach" % w["workflow_key"]),
                "cite": "measured: no %s row with workflow_key=%r"
                        % (table, w["workflow_key"])}
    conn.execute("UPDATE dev_task SET workflow_id=?, updated_at=CURRENT_TIMESTAMP "
                 "WHERE id=?", (int(w["workflow_id"]), int(task_id)))
    conn.commit()
    return {"ok": True, "task_id": int(task_id),
            "workflow_id": int(w["workflow_id"]),
            "workflow_key": w["workflow_key"], "reachable_via": reach,
            "cite": "%s; %s; measured: route(s) %s name it"
                    % (w["cite"], cite_ref, reach)}


def task_workflow_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which tasks have a workflow, and which workflows are reachable."""
    cols = _columns(conn, "dev_task") if _table_exists(conn, "dev_task") else set()
    if "workflow_id" not in cols:
        return {"ok": False, "code": "NO_WORKFLOW_COLUMN"}
    assigned = conn.execute("SELECT COUNT(*) FROM dev_task WHERE workflow_id IS "
                            "NOT NULL").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM dev_task").fetchone()[0]
    table = route_table(conn)
    reachable: list[str] = []
    if table and "workflow_key" in _columns(conn, table):
        reachable = [r[0] for r in conn.execute(
            "SELECT DISTINCT workflow_key FROM %s WHERE is_active=1" % table)]
    return {"ok": True, "tasks_total": int(total), "tasks_assigned": int(assigned),
            "tasks_unassigned": int(total) - int(assigned),
            "route_table": table or None, "reachable_workflows": sorted(reachable),
            "cite": "measured: dev_task.workflow_id NOT NULL count"}


# --------------------------------------------------------------------------
# the ROUTER'S OWN DEMONSTRATION (and its refusal to be a capability)
# --------------------------------------------------------------------------
def register_router(conn: sqlite3.Connection, *, cite_ref: str) -> dict[str, Any]:
    """REFUSE to register the router as a capability; bind the capability to routes.

    The refusal is the deliverable: it states WHY, with the measured kind list.

    The router does NOT put itself in `capability_binding`, and that is a
    CORRECTION of this module's first draft: `capability_binding.capability_id` is
    `NOT NULL` with a FOREIGN KEY to `capability_registry`, so a subject that is
    NOT a capability cannot be bound there — writing `capability_id = 0` would be
    a fake capability. What IS bound is the real capability `llm.goal_inference`
    to each active route: "this capability serves this route". The router is the
    step that READS that binding, which is why it needs no row of its own.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF"}
    kinds = [r[0] for r in conn.execute(
        "SELECT kind_key FROM capability_kind_registry ORDER BY kind_key")] \
        if _table_exists(conn, "capability_kind_registry") else []
    refusal = {"ok": False, "code": "NOT_A_CAPABILITY", "router": ROUTER_KEY,
               "declared_kinds": kinds,
               "why": ("every declared capability_kind names a VERB A PROVIDER "
                       "PERFORMS (%s); matching a goal against a register is a "
                       "lookup this system performs on its own rows, so "
                       "registering it as a capability would need a 6th kind "
                       "invented with no evidence" % kinds),
               "cite": "measured: SELECT kind_key FROM capability_kind_registry "
                       "-> %s" % kinds}
    bind: dict[str, Any] = {"ok": False, "code": "NO_ROUTES_TO_BIND"}
    table = route_table(conn)
    cap = conn.execute("SELECT capability_id, capability_key FROM "
                       "capability_registry WHERE capability_key = ?",
                       ("llm.goal_inference",)).fetchone() \
        if _table_exists(conn, "capability_registry") else None
    if not cap:
        return {"ok": True, "capability_registration": refusal,
                "binding": {"ok": False, "code": "INFERENCE_CAPABILITY_ABSENT",
                            "why": "run `goal_inference.py --register` first"},
                "cite": cite_ref}
    if table and _table_exists(conn, "capability_binding"):
        made: list[dict[str, Any]] = []
        for r in conn.execute("SELECT route_id, route_key FROM %s WHERE "
                              "is_active = 1 ORDER BY route_id" % table):
            cur = conn.execute(
                "INSERT OR IGNORE INTO capability_binding (capability_id, "
                "subject_kind, subject_ref, cite_ref, evidence_id, declared_by, "
                "status, note) VALUES (?,?,?,?,?,?,?,?)",
                (int(cap["capability_id"]), "route", str(r["route_key"]),
                 cite_ref, "goal_resolver:%s" % r["route_key"], ROUTER_KEY,
                 "DECLARED",
                 "the goal_inference capability serves the route the resolver "
                 "reads"))
            made.append({"route_key": str(r["route_key"]),
                         "capability_key": str(cap["capability_key"]),
                         "rowid": int(cur.lastrowid or 0)})
        if made:
            conn.commit()
            bind = {"ok": True, "bound": made, "route_table": table,
                    "capability_key": str(cap["capability_key"])}
    return {"ok": True, "capability_registration": refusal, "binding": bind,
            "cite": cite_ref}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    table = route_table(conn)
    routes: list[dict[str, Any]] = []
    if table:
        routes = [dict(r) for r in conn.execute("SELECT * FROM %s" % table)]
    cols = _columns(conn, "dev_task") if _table_exists(conn, "dev_task") else set()
    return {"ok": True, "route_table": table or None, "routes": routes,
            "declared_kinds": [r[0] for r in conn.execute(
                "SELECT kind_key FROM capability_kind_registry ORDER BY kind_key")]
            if _table_exists(conn, "capability_kind_registry") else [],
            "capability_named_goal_resolver": conn.execute(
                "SELECT COUNT(*) FROM capability_registry WHERE capability_key "
                "LIKE ?", ("%goal_resolver%",)).fetchone()[0]
            if _table_exists(conn, "capability_registry") else 0,
            "dev_task_has_workflow_id": "workflow_id" in cols,
            "task_workflow_audit": task_workflow_audit(conn)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--goal", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.goal:
            print(json.dumps(resolve_goal_to_workflow(conn, args.goal),
                             indent=2, ensure_ascii=False, default=str))
            return 0
        if args.apply:
            col = ensure_task_workflow_column(conn)
            print("dev_task.workflow_id: %s" % col)
            r = register_router(
                conn, cite_ref="measured: purpose_route_registry holds the live "
                               "route; capability_binding.subject_kind CHECK "
                               "allows 'route'")
            print("router as a capability: %s" % r["capability_registration"]["code"])
            print("   %s" % r["capability_registration"]["why"])
            print("router binding: %s" % r["binding"].get("ok"))
            for b in r["binding"].get("bound", []):
                print("   route %s" % b["route_key"])
            return 0
        res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        print("route register   : %s (%d rows)"
              % (res["route_table"], len(res["routes"])))
        for r in res["routes"]:
            print("   %-38s goal=%r -> service=%s workflow=%s"
                  % (r.get("route_key"), str(r.get("purpose_key"))[:46],
                     r.get("ticket_origin"), r.get("workflow_key")))
        print("declared kinds   : %s" % res["declared_kinds"])
        print("capabilities named goal_resolver: %d (MUST be 0 — it is a router)"
              % res["capability_named_goal_resolver"])
        print("dev_task.workflow_id present: %s" % res["dev_task_has_workflow_id"])
        a = res["task_workflow_audit"]
        if a.get("ok"):
            print("tasks: %d total, %d assigned, %d unassigned"
                  % (a["tasks_total"], a["tasks_assigned"], a["tasks_unassigned"]))
            print("reachable workflows: %s" % a["reachable_workflows"])
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
