# -*- coding: utf-8 -*-
"""purpose_route_seed.py — record a PURPOSE -> SERVICE -> WORKFLOW route ONLY
                           where the association is evidence-backed.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
Open work #3. `purpose_route_registry` had **0 rows**, so
`logic_generator.spec_from_route()` REFUSED every call — the whole
purpose -> service -> code-generation path was DEAD CODE.

THE ASSOCIATION IS NOWHERE RECORDED (measured, every candidate source)
---------------------------------------------------------------------
    identity_registry.why         1 row, value `'NA'` — a NOT-ANSWERED sentinel —
                                   and the table has NO service column at all
    skill_contract_template.purpose  22 real purpose strings, but their ONLY
                                   `flow_ref` is `video-7-stage`, which is NOT a
                                   `workflow_registry` key (the registry has
                                   exactly `worker_identity` and
                                   `worker_identity_flow`)
    capability_registry.why        a capability-level rationale, not a route
    ticket / ticket_event          ONE demo ticket (`opened_by='demo'`)
    ticket_subject                 a SUBJECT, not a route
    worker_identity_binding        the 6 5W1H rows; no service, no workflow choice
So the INGREDIENTS are real (4 active services, and `worker_identity_flow` with 3
declared steps) and the ASSOCIATION is absent. The table is the ONLY one in the DB
with both a service and a workflow column, so there is nothing to copy from.

THE ONE ROUTE THE EVIDENCE SUPPORTS
-----------------------------------
The user's ruling:
    "chatting is for purpose -> we need to provide services, which services can
     match the user, so i think we need workflow to define"
And the ONE live identity (`identity_registry` row 1) records a chat that arrived
on `channel='vscode'` and runs `workflow_id=2`. Its job is to establish the
worker's identity and produce the chat id. So the purpose that IS recorded is:

    "establish the worker identity for a chat" -> chat_center -> worker_identity_flow

This is a HISTORICAL fact about a live row, not an invented policy, and the
`route_key` says so. Everything else is REPORTED, never wired up — which is what
the table's own column comment means: "a route with no citation is a claim, not a
route".

Run:
    .\\.venv\\Scripts\\python.exe purpose_route_seed.py --measure
    .\\.venv\\Scripts\\python.exe purpose_route_seed.py            # dry run
    .\\.venv\\Scripts\\python.exe purpose_route_seed.py --apply
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

# The purpose key. It is a DECLARED phrase, not a token, because the user's
# phrasing was "chatting is for purpose" — the purpose of the chat that produced
# the live identity. It is deliberately DESCRIPTIVE so that a reader can tell it
# came from a recorded history rather than from a policy.
PURPOSE_WORKER_IDENTITY_CHAT = "establish the worker identity for a chat"


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _rows(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    if not _table_exists(conn, sql.split("FROM", 1)[1].split()[0]):
        return []
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    except sqlite3.Error:
        return []


def citable(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The routes the EVIDENCE supports, each with a citation to a real row.

    CORRECTED 2026-09-26. MEASURED: this used to return ONE entry PER IDENTITY
    ROW (62 entries) with a HARDCODED `route_key`, so:
      * 62 entries carried only 3 distinct workflows, and
      * all 62 carried the SAME `route_key`, so `apply()` upserted 62 times and
        the LAST workflow silently won.
    The result was a function that CLAIMED 3 workflows were routable while the
    table held 1 route — a claim the data did not support.

    The defect was MASKED: `uncitable` raised AttributeError on a list-shaped
    `environment_json`, so the proof died before it could compare the two. Fixing
    that crash REVEALED this one.

    The fix is to return ONE entry per DISTINCT workflow, with the `route_key`
    DERIVED from the workflow rather than hardcoded, and to include only a
    workflow that is actually routable — the SAME conditions `uncitable` reports
    as the reason it is not. That makes the two a PARTITION.
    """
    out: list[dict[str, Any]] = []
    if not _table_exists(conn, "purpose_route_registry"):
        return out
    # ONE entry per DISTINCT workflow the identities actually ran.
    idents = _rows(conn, "SELECT identity_id, session_id, workflow_id, channel, "
                         "why FROM identity_registry ORDER BY identity_id")
    seen: dict[int, dict[str, Any]] = {}
    for ident in idents:
        wid = ident.get("workflow_id")
        if wid is None or int(wid) in seen:
            continue
        seen[int(wid)] = ident
    for wid, ident in sorted(seen.items()):
        wf = _rows(conn, "SELECT workflow_id, workflow_key, is_active "
                         "FROM workflow_registry WHERE workflow_id = ?", (wid,))
        if not wf:
            continue
        # A route to an INACTIVE workflow is not routable.
        if not int(wf[0]["is_active"] or 0):
            continue
        steps = _rows(conn, "SELECT step_no, step_kind FROM workflow_step "
                            "WHERE workflow_id = ? ORDER BY step_no", (wid,))
        # A route to a workflow with NO STEPS is refused by `spec_from_route`, so
        # recording it would create a route that cannot be measured.
        if not steps:
            continue
        # A step with NO declared kind has no UNIT, so `spec_from_route` REFUSES
        # the route. Recording it would create a route that cannot be measured —
        # the SAME condition `uncitable` reports.
        if all(str(s.get("step_kind") or "NA") == "NA" for s in steps):
            continue
        svc = _rows(conn, "SELECT id, ticket_origin FROM ticket_center "
                          "WHERE ticket_origin = 'chat_center'")
        if not svc:
            continue
        wf_key = str(wf[0]["workflow_key"])
        # THE ROUTE KEY IS DERIVED, not hardcoded: `{purpose}.{workflow}`.
        route_key = "chat_identity.%s" % wf_key
        cite = ("register:identity_registry:%d + register:workflow_registry:%d + "
                "register:ticket_center:%d"
                % (int(ident["identity_id"]), int(wf[0]["workflow_id"]),
                   int(svc[0]["id"])))
        out.append({
            "route_key": route_key,
            "purpose_key": PURPOSE_WORKER_IDENTITY_CHAT,
            "ticket_origin": str(svc[0]["ticket_origin"]),
            "workflow_key": wf_key,
            "cite_ref": cite,
            "evidence": {
                "identity_id": int(ident["identity_id"]),
                "session_id": ident["session_id"],
                "channel": ident["channel"],
                "workflow_id": int(wf[0]["workflow_id"]),
                "steps": len(steps),
                "step_kinds": [s.get("step_kind") for s in steps],
            },
            "why": ("the live identity (identity_registry row %d) records a "
                    "chat on channel %r running workflow_id=%d, whose %d declared "
                    "steps are what 'serving the purpose' means"
                    % (int(ident["identity_id"]), ident["channel"],
                       int(wf[0]["workflow_id"]), len(steps))),
        })
    return out


def uncitable(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Everything with NO evidence-backed route, each with the measured reason."""
    out: dict[str, list[dict[str, Any]]] = {"services": [], "workflows": [],
                                            "purposes": []}
    routed_svc = {c["ticket_origin"] for c in citable(conn)}
    routed_wf = {c["workflow_key"] for c in citable(conn)}
    for s in _rows(conn, "SELECT id, ticket_origin, description FROM ticket_center "
                         "ORDER BY id"):
        if s["ticket_origin"] in routed_svc:
            continue
        out["services"].append({
            "ticket_origin": s["ticket_origin"], "cite": "register:ticket_center:%d"
                                            % int(s["id"]),
            "why": ("no recorded purpose selects this service; nothing in the DB "
                    "associates it with a need (%s)"
                    % (s["description"] or "no description")[:60])})
    for w in _rows(conn, "SELECT workflow_id, workflow_key, is_active "
                         "FROM workflow_registry ORDER BY workflow_id"):
        if w["workflow_key"] in routed_wf:
            continue
        steps = _rows(conn, "SELECT step_no, step_kind FROM workflow_step "
                            "WHERE workflow_id = ?", (int(w["workflow_id"]),))
        kinds = [s.get("step_kind") for s in steps]
        if not steps:
            why = "the workflow has NO steps, so a route to it could ask nothing"
        elif all((k or "NA") == "NA" for k in kinds):
            why = ("all %d step(s) have step_kind='NA', so their type questions "
                   "have no UNIT and `spec_from_route` would REFUSE the route"
                   % len(steps))
        elif not w["is_active"]:
            why = ("the workflow is NOT active (is_active=%s)" % w["is_active"])
        else:
            why = "no recorded purpose selects this workflow"
        out["workflows"].append({
            "workflow": w["workflow_key"], "steps": len(steps),
            "step_kinds": kinds, "is_active": w["is_active"],
            "cite": "register:workflow_registry:%d" % int(w["workflow_id"]),
            "why": why})
    for p in _rows(conn, "SELECT skill_key, purpose, environment_json "
                         "FROM skill_contract_template "
                         "WHERE purpose IS NOT NULL AND purpose <> 'NA'"):
        env = {}
        try:
            env = json.loads(p.get("environment_json") or "{}")
        except Exception:
            env = {}
        # THE SHAPE IS MEASURED, NOT ASSUMED. MEASURED 2026-09-26: 28 rows hold a
        # JSON OBJECT and 1 (`skill_name_classify`) holds a JSON LIST
        # (`["name_classify.py", "terminology_alias.py", ...]`). Calling
        # `.items()` on the list raised AttributeError and killed the whole
        # proof. A list has no KEYS, so it can name no flow — the honest reading
        # is "no flow refs", REPORTED as such rather than crashing.
        if isinstance(env, dict):
            refs = [str(v) for k, v in env.items()
                    if "flow" in str(k).lower() or "workflow" in str(k).lower()]
        elif isinstance(env, list):
            refs = [str(v) for v in env
                    if "flow" in str(v).lower() or "workflow" in str(v).lower()]
        else:
            refs = []
        registered = [r for r in refs if _rows(
            conn, "SELECT 1 AS x FROM workflow_registry WHERE workflow_key = ?",
            (r,))]
        out["purposes"].append({
            "skill_key": p["skill_key"], "purpose": (p["purpose"] or "")[:70],
            "flow_ref": refs, "registered_workflow": registered,
            "cite": "register:skill_contract_template:%s" % p["skill_key"],
            "why": ("its flow_ref %s is NOT a workflow_registry key, so there is "
                    "no registered workflow to route to"
                    % (refs or "is absent")) if not registered else "routable"})
    return out


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    c = citable(conn)
    un = uncitable(conn)
    rows = _rows(conn, "SELECT route_id, route_key, purpose_key, ticket_origin, "
                       "workflow_key, cite_ref, is_active "
                       "FROM purpose_route_registry ORDER BY route_id")
    return {"citable": len(c), "rows": c, "uncitable": {k: len(v) for k, v in un.items()},
            "uncitable_rows": un, "existing_routes": len(rows),
            "existing": rows,
            "identity_why": [r.get("why") for r in _rows(
                conn, "SELECT why FROM identity_registry ORDER BY identity_id")]}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Write via `purpose_route_registry.add_route` — the EXISTING writer."""
    import purpose_route_registry as prr
    prr.ensure_schema(conn)
    created = 0
    existed = 0
    refused: list[dict[str, Any]] = []
    for c in citable(conn):
        try:
            res = prr.add_route(conn, route_key=c["route_key"],
                                purpose_key=c["purpose_key"],
                                ticket_origin=c["ticket_origin"],
                                workflow_key=c["workflow_key"],
                                cite_ref=c["cite_ref"])
        except prr.RouteRefused as exc:
            refused.append({"route_key": c["route_key"], "why": str(exc),
                            "cite": c["cite_ref"]})
            continue
        if res.get("created"):
            created += 1
        else:
            existed += 1
    total = conn.execute("SELECT COUNT(*) FROM purpose_route_registry").fetchone()[0]
    return {"ok": True, "created": created, "existed": existed,
            "refused": refused, "total": total}


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
            res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.apply:
            print("APPLIED: created=%d existed=%d total=%d"
                  % (res["created"], res["existed"], res["total"]))
            for r in res["refused"]:
                print("   REFUSED %s: %s" % (r["route_key"], r["why"]))
        else:
            print("citable routes   : %d" % res["citable"])
            for c in res["rows"]:
                print("   %-34s %-38s -> %-12s -> %s"
                      % (c["route_key"], c["purpose_key"][:38], c["ticket_origin"],
                         c["workflow_key"]))
                print("      cite: %s" % c["cite_ref"])
            print("existing routes  : %d" % res["existing_routes"])
            print("identity why     : %s" % res["identity_why"])
            print("UNCITABLE (reported, never wired up):")
            for k, v in res["uncitable_rows"].items():
                print("   %s (%d):" % (k, len(v)))
                for r in v[:3]:
                    print("      %-26s %s" % (r.get("service")
                                               or r.get("workflow")
                                               or r.get("skill_key"),
                                               r["why"][:74]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
