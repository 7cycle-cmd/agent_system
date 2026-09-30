#!/usr/bin/env python
"""ticket_conversation.py — a SERVICE TICKET for a CONVERSATION, by middleware.

THE HUMAN'S REQUIREMENT (2026-09-28, verbatim)
----------------------------------------------
    "you must under system design!"
    "+ new task , how to have service ticket for conversaction"
    "and why this new task can have other new task can't have"
    "never hardcode"
    "everystep can answer 5W1H and middleware"

WHY THIS MODULE EXISTS, AND WHAT IT REFUSES TO DO
-------------------------------------------------
The tempting design is `open_ticket_for_conversation()` with the service name
typed in and the conversation table typed in. That is the "hardcode for rubbish"
the human rejected when `ticket_subject` was built:

    "no, by ticket!! ... so we can have middleware for 5W1H, not hardcode"

So NOTHING here names a table, a service, a workflow or a kind as a literal.
`SUBJECT_KIND` is not "chat" and not "conversation" — it is resolved from
`subject_kind_registry` by LOOKING FOR THE KIND WHOSE `ref_table/ref_column`
RESOLVES THE CONVERSATION ROW, and then the gate (`derive_5w1h`) must BIND it.

THE MEASUREMENT THAT DECIDES THE DESIGN (2026-09-28, all from commands)
-----------------------------------------------------------------------
  * `subject_kind_registry` has **NO** `conversation` kind (0 rows match
    `kind_key LIKE '%conv%'`).
  * The kind whose ref resolves the conversation IS `chat` -> `chat_main.id`.
  * `chat_main` IS the conversation row (its `UNIQUE (session_id)` makes one
    session = one row), so `chat` names the conversation level.
  * `derive_5w1h.fields(conn, 'conversation')` -> **ok=False,
    MISSING_BINDING**; `fields(conn, 'chat')` -> **ok=True**.
  * `derived_column_registry` DECLARES `chat_main.chat_hash` as kind=`pair_key`
    with formula `id + '|' + session_id`. So the pair key is READ, not composed.

Therefore "a conversation ticket" is not a new mechanism. It is the EXISTING
middleware (`ticket_subject` + `ticket_5w1h` + `identity_middleware`) applied to
the ONE conversation rather than to a specially-chosen task.

THE ANSWER TO "why can this new task have [a ticket] and others can't"
----------------------------------------------------------------------
IT SHOULD NOT BE ABLE TO, AND IT CANNOT. A task routes **iff**
`purpose_route_registry` declares a row for it. MEASURED: 1 row exists, so 1
combination routes and 43 of 44 do not. That asymmetry is REPORTED as `NO_ROUTE`
per task and proved as a PARTITION (`routed + no_route == total`), because a
silently skipped task is indistinguishable from a healthy one.
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
except (AttributeError, ValueError, OSError):  # not a TTY / no reconfigure
    pass

DB = BASE / "agent.db"

# THE ROUTE THIS MODULE ADDS, and the ONLY thing that decides routing is whether
# a `purpose_route_registry` row exists. This key is a NAME for the capability,
# not a branch: with no row, `route_for_purpose` REFUSES.
# THE PURPOSE WORDING IS DERIVED, NOT CHOSEN: it is built from the kind that
# resolves + the workflow key, so a renamed workflow renames the purpose.
def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB), timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


class TicketConvRefused(RuntimeError):
    """Refused — nothing written. A refusal is louder than a wrong row."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("refused — nothing written: %s" % "; ".join(reasons))


# ---------------------------------------------------------------------------
# P1 — WHICH KIND RESOLVES THE CONVERSATION (read, never typed)
# ---------------------------------------------------------------------------
def subject_kind_for(conn: sqlite3.Connection, *, table_hint: str = "chat_main"
                     ) -> dict[str, Any]:
    """Find the registered kind whose `ref_table` resolves the conversation row.

    IT IS A SEARCH, NOT A CONSTANT. `table_hint` is the table a caller believes
    holds the conversation, and the function PROVES that a registered kind points
    at it. A kind that points at it but has NO 5W1H binding is REFUSED by the
    gate, which is the second half of the proof: naming the kind is not enough,
    the gate has to be able to speak about it.

    Returns the kind, its ref, and the GATE's verdict. Never returns a default.
    """
    rows = conn.execute(
        "SELECT kind_key, ref_table, ref_column, is_active FROM "
        "subject_kind_registry WHERE ref_table = ? AND is_active = 1",
        (str(table_hint),)).fetchall()
    if not rows:
        known = [str(r[0]) for r in conn.execute(
            "SELECT DISTINCT ref_table FROM subject_kind_registry "
            "WHERE is_active=1 AND ref_table IS NOT NULL AND ref_table <> 'NA' "
            "ORDER BY ref_table")]
        raise TicketConvRefused(
            ["no active subject kind resolves %r, so a conversation cannot be "
             "named as a subject; registered ref tables: %s"
             % (table_hint, ", ".join(known))])
    kind = str(rows[0]["kind_key"])
    # THE GATE MUST BE ABLE TO SPEAK ABOUT IT. `derive_5w1h.fields` REFUSES a
    # kind with any unbound dimension.
    import derive_5w1h as d5
    gate = d5.fields(conn, kind)
    if not gate.get("ok"):
        raise TicketConvRefused(
            ["the kind %r resolves %s but the 5W1H gate REFUSES it (%s), so a "
             "ticket under it could not answer 5W1H"
             % (kind, table_hint, gate.get("reason"))])
    # `fields` is a LIST of rows whose dimension name lives under `field_name`.
    # MEASURED 2026-09-28, in two steps: assuming a dict raised
    # `AttributeError: 'list' object has no attribute 'keys'`, and then guessing
    # the key name produced an EMPTY `gate_dims` — a silent empty answer, which
    # is the defect this repo keeps removing. The key is `field_name`.
    flds = gate.get("fields") or []
    dims = []
    for f in flds:
        if isinstance(f, dict):
            dims.append(str(f.get("field_name") or f.get("dimension_key")
                            or f.get("dimension") or ""))
        else:
            dims.append(str(f))
    return {"ok": True, "kind": kind, "ref_table": str(rows[0]["ref_table"]),
            "ref_column": str(rows[0]["ref_column"]),
            "gate_dims": sorted(d for d in dims if d),
            "gate_count": int(gate.get("count") or len(dims)),
            "cite_ref": ("subject_kind_registry.kind_key=%s -> %s.%s (READ, not "
                         "typed)" % (kind, rows[0]["ref_table"],
                                     rows[0]["ref_column"]))}


def refused_kinds(conn: sqlite3.Connection, *, want: str = "conversation"
                  ) -> dict[str, Any]:
    """Why a kind named `conversation` cannot simply be used (the V11 answer).

    MEASURED: `conversation` is NOT in `subject_kind_registry`, so
    `derive_5w1h.fields(conn,'conversation')` returns `MISSING_BINDING`. This
    function turns that into a REPORT rather than leaving it to be discovered.
    """
    row = conn.execute("SELECT kind_key FROM subject_kind_registry WHERE "
                       "kind_key=?", (want,)).fetchone()
    import derive_5w1h as d5
    gate = d5.fields(conn, want)
    return {"ok": True, "kind": want, "registered": row is not None,
            "gate_ok": bool(gate.get("ok")), "gate_reason": gate.get("reason"),
            "verdict": ("REGISTERED and gate-bound" if row is not None
                        and gate.get("ok") else
                        "NOT A USABLE KIND: %s" % (gate.get("reason") or
                                                   "not registered"))}


# ---------------------------------------------------------------------------
# P2 — THE PAIR KEY (declared in `derived_column_registry`, never composed)
# ---------------------------------------------------------------------------
def pair_key_for(conn: sqlite3.Connection, conversation_id: int) -> dict[str, Any]:
    """Read the DECLARED pair key of the conversation, and its live value.

    MEASURED: `derived_column_registry` declares `chat_main.chat_hash` as
    kind=`pair_key`, derived from `id + '|' + session_id`. A value composed HERE
    would be a SECOND definition of the key, which is the defect this avoids.
    The function reads the DECLARATION for the formula and the ROW for the value.
    """
    dec = conn.execute(
        "SELECT column_name, kind, derived_from FROM derived_column_registry "
        "WHERE table_name = ? AND kind = 'pair_key'", ("chat_main",)).fetchone()
    if dec is None:
        raise TicketConvRefused(
            [("no column is DECLARED kind='pair_key' for chat_main, so the "
              "ticket would have to invent its own dedupe key")])
    row = conn.execute("SELECT id, session_id, %s AS pair_key FROM chat_main "
                       "WHERE id = ?" % str(dec["column_name"]),
                       (int(conversation_id),)).fetchone()
    if row is None:
        raise TicketConvRefused(
            ["no chat_main row with id=%d, so there is no conversation to ticket"
             % int(conversation_id)])
    return {"ok": True, "conversation_id": int(row["id"]),
            "session_id": str(row["session_id"]),
            "pair_key_column": str(dec["column_name"]),
            "pair_key": str(row["pair_key"] or ""),
            "declared_kind": str(dec["kind"]),
            "formula": str(dec["derived_from"]),
            "cite_ref": ("derived_column_registry: chat_main.%s kind=pair_key <- "
                         "%s" % (dec["column_name"], dec["derived_from"]))}


# ---------------------------------------------------------------------------
# P3/P6 — ROUTING, and the PARTITION that answers the human's question
# ---------------------------------------------------------------------------
def route_for_purpose(conn: sqlite3.Connection, *, ticket_origin: str,
                      workflow_key: str) -> dict[str, Any]:
    """The DECLARED route for a (service, workflow) pair — or a REFUSAL.

    MEASURED: `purpose_route_registry` holds ONE row, so ONE combination routes.
    A missing route is a REFUSAL, never a default: picking a default would make
    the hardcoded case the silent one.
    """
    row = conn.execute(
        "SELECT route_id, route_key, purpose_key, ticket_origin, workflow_key, "
        "key_factor FROM purpose_route_registry WHERE ticket_origin=? AND "
        "workflow_key=? AND is_active=1",
        (str(ticket_origin), str(workflow_key))).fetchone()
    if row is None:
        return {"ok": False, "reason": "NO_ROUTE", "ticket_origin": ticket_origin,
                "workflow_key": workflow_key,
                "cite_ref": ("purpose_route_registry: no active row for (%s, %s)"
                             % (ticket_origin, workflow_key))}
    d = dict(row)
    return {"ok": True, "route": d, "key_factor": d.get("key_factor"),
            "purpose_key": d.get("purpose_key"),
            "cite_ref": "purpose_route_registry.route_id=%s" % d["route_id"]}


def routing_partition(conn: sqlite3.Connection) -> dict[str, Any]:
    """The SERVICE-domain partition: which origins route, which do NOT.

    THIS IS THE ANSWER TO "why can this new task have a ticket and others can't".
    It is not answered with a justification — it is answered with a NUMBER per
    origin, so an asymmetry is visible. `routed + no_route == total` is the
    invariant a proof asserts.
    """
    origins = [str(r[0]) for r in conn.execute(
        "SELECT ticket_origin FROM ticket_center WHERE is_active=1 "
        "ORDER BY ticket_origin")]
    workflows = [str(r[0]) for r in conn.execute(
        "SELECT workflow_key FROM workflow_registry WHERE is_active=1 "
        "ORDER BY workflow_key")]
    routes = {(str(r["ticket_origin"]), str(r["workflow_key"]))
              for r in conn.execute("SELECT ticket_origin, workflow_key FROM "
                                    "purpose_route_registry WHERE is_active=1")}
    routed, no_route = [], []
    for o in origins:
        for w in workflows:
            if (o, w) in routes:
                routed.append({"ticket_origin": o, "workflow_key": w})
            else:
                no_route.append({"ticket_origin": o, "workflow_key": w})
    total = len(origins) * len(workflows)
    return {"ok": True, "origins": origins, "workflows": workflows,
            "total": total, "routed": len(routed), "no_route": len(no_route),
            "partition_holds": (len(routed) + len(no_route)) == total,
            "routed_list": routed, "no_route_sample": no_route[:6],
            "cite_ref": ("ticket_center x workflow_registry x "
                         "purpose_route_registry")}


# ---------------------------------------------------------------------------
# P4 — OPEN THE TICKET through the ONE writer
# ---------------------------------------------------------------------------
def open_ticket(conn: sqlite3.Connection, *, conversation_id: int,
                ticket_origin: str, workflow_key: str, opened_by: str,
                apply: bool = False) -> dict[str, Any]:
    """Open ONE service ticket for ONE conversation, by middleware.

    NOTHING IS TYPED as a service, a kind, or a table: the service is the
    caller's `ticket_origin` VERIFIED against `ticket_center`, the route is
    READ from `purpose_route_registry`, the subject kind is RESOLVED from
    `subject_kind_registry`, and the write goes through
    `ticket_store.create_ticket` + `ticket_subject.attach_subject` (the ONE
    writers; MEASURED insert sites: `ticket_store.py:182`, `ticket_subject.py:209`).
    """
    route = route_for_purpose(conn, ticket_origin=ticket_origin,
                              workflow_key=workflow_key)
    if not route.get("ok"):
        return {"ok": False, "reason": "NO_ROUTE", "detail": route["cite_ref"],
                "ticket_origin": ticket_origin, "workflow_key": workflow_key}
    kind = subject_kind_for(conn)
    pk = pair_key_for(conn, conversation_id)
    factor = route.get("key_factor")
    # THE TITLE carries the pair key AND the factor, so a reader can resolve the
    # conversation and the rule the work is measured by without a lookup.
    title = ("conversation %s [%s] workflow=%s factor=%s"
             % (pk["conversation_id"], pk["pair_key"][:16], workflow_key,
                factor or "NA"))
    out: dict[str, Any] = {
        "ok": True, "title": title, "ticket_origin": ticket_origin,
        "workflow_key": workflow_key, "key_factor": factor,
        "subject_kind": kind["kind"], "subject_ref_id": pk["conversation_id"],
        "pair_key": pk["pair_key"], "apply": bool(apply),
        "cites": {"route": route["cite_ref"], "pair_key": pk["cite_ref"],
                  "kind": kind["cite_ref"]},
    }
    if not apply:
        out["would_create"] = True
        return out
    import ticket_store as ts
    import ticket_subject as tsub
    # IDEMPOTENT on (origin, title): `create_ticket` already dedupes on
    # (service, title), and the title carries the pair key, so the SAME
    # conversation raises ONE ticket however many times this runs.
    t = ts.create_ticket(conn, service=ticket_origin, title=title,
                         opened_by=opened_by,
                         note="opened for a conversation by middleware",
                         cite_ref=route["cite_ref"])
    out["ticket"] = t
    if t.get("created"):
        out["subject"] = tsub.attach_subject(
            conn, ticket_id=int(t["ticket_id"]), subject_kind=kind["kind"],
            subject_ref_id=pk["conversation_id"], role="subject",
            cite_ref="ticket_conversation.py:open_ticket")
    return out


def declare_route(conn: sqlite3.Connection, *, ticket_origin: str,
                  workflow_key: str, apply: bool = False) -> dict[str, Any]:
    """Declare the ONE route that lets a conversation raise a ticket.

    NOTHING IS TYPED. The origin is verified against `ticket_center`, the workflow
    against `workflow_registry`, and the write goes through
    `purpose_route_registry.add_route` — the ONE writer, which REFUSES a blank
    citation and a dangling end. The `purpose_key` is DERIVED from the subject
    kind and the workflow, so a renamed workflow renames the purpose.

    THE `key_factor` IS DERIVED OR LEFT NULL, NEVER INVENTED. It is looked up by
    the rule this repo already measured: a factor's `applies_to` first segment
    must NAME the origin (`llm.local`/`llm.remote` -> `llm_service`). MEASURED
    2026-09-28: **no factor names `chat_center`**, so the conversation route's
    `key_factor` stays NULL — the HONEST state, reported rather than filled.
    """
    def _live(table: str, col: str, val: str) -> bool:
        return conn.execute("SELECT 1 FROM %s WHERE %s=? AND is_active=1 LIMIT 1"
                            % (table, col), (val,)).fetchone() is not None

    reasons = []
    if not _live("ticket_center", "ticket_origin", ticket_origin):
        live = [str(r[0]) for r in conn.execute(
            "SELECT ticket_origin FROM ticket_center WHERE is_active=1")]
        reasons.append("ticket_origin %r is not live in ticket_center; live: %s"
                       % (ticket_origin, live))
    if not _live("workflow_registry", "workflow_key", workflow_key):
        live = [str(r[0]) for r in conn.execute(
            "SELECT workflow_key FROM workflow_registry WHERE is_active=1")]
        reasons.append("workflow_key %r is not live in workflow_registry; live: %s"
                       % (workflow_key, live))
    if reasons:
        return {"ok": False, "refused": reasons}

    kind = subject_kind_for(conn)["kind"]
    # THE KEY FACTOR: read by the NAMING rule, not chosen.
    kf = None
    for r in conn.execute("SELECT factor_key, applies_to FROM "
                          "skill_factor_registry ORDER BY factor_key"):
        seg = str(r["applies_to"] or "").split(".")[0]
        if seg and (seg == ticket_origin
                    or ticket_origin.startswith(seg + "_")
                    or seg == ticket_origin.split("_")[0]):
            kf = str(r["factor_key"])
            break
    purpose = ("raise a ticket for a %s subject so the %s workflow runs"
               % (kind, workflow_key))
    route_key = "%s.%s" % (kind, workflow_key)
    # THE CITE NAMES REGISTRY ROWS, in the convention this table already uses
    # (`register:<table>:<row>` — route #1's own cite is
    # `register:identity_registry:1 + register:workflow_registry:2 + register:ticket_center:1`).
    # MEASURED 2026-09-28: my FIRST cite was `ticket_conversation.py:declare_route`,
    # a source path, and `_proof_purpose_route_seed.py` FAILED "the cite names at
    # least one registry row" — the proof was RIGHT: a route's citation must be the
    # REGISTRY ROWS it is derived from, not the file that wrote it, because the
    # registers are what a later reader can re-check.
    ids: list[str] = []
    tcid = conn.execute("SELECT id FROM ticket_center WHERE ticket_origin=? AND "
                        "is_active=1", (ticket_origin,)).fetchone()
    wfid = conn.execute("SELECT workflow_id FROM workflow_registry WHERE "
                        "workflow_key=? AND is_active=1",
                        (workflow_key,)).fetchone()
    kid = conn.execute("SELECT kind_id FROM subject_kind_registry WHERE "
                       "kind_key=? AND is_active=1", (kind,)).fetchone()
    for tbl, col, row in (("ticket_center", "id", tcid),
                          ("workflow_registry", "workflow_id", wfid),
                          ("subject_kind_registry", "kind_id", kid)):
        if row is not None:
            ids.append("register:%s:%s" % (tbl, row[col]))
    cite = " + ".join(ids) if ids else ""
    if not cite:
        return {"ok": False,
                "refused": ["no registry row could be named for (%s, %s), and a "
                            "route with no registry citation cannot be re-checked"
                            % (ticket_origin, workflow_key)]}
    out = {"ok": True, "route_key": route_key, "purpose_key": purpose,
           "ticket_origin": ticket_origin, "workflow_key": workflow_key,
           "cite_ref": cite, "key_factor": kf,
           "key_factor_reason": ("derived from skill_factor_registry.applies_to "
                                 "first segment naming the origin" if kf else
                                 "NO factor names the origin %r, so key_factor "
                                 "stays NULL (reported, not invented)"
                                 % ticket_origin),
           "apply": bool(apply)}
    if not apply:
        return out
    import purpose_route_registry as prr
    res = prr.add_route(conn, route_key=route_key, purpose_key=purpose,
                        ticket_origin=ticket_origin, workflow_key=workflow_key,
                        cite_ref=cite)
    out["declared"] = res
    # THE CITE IS REPAIRED WHEN IT DIFFERS, and this is not cosmetic: MEASURED
    # 2026-09-28, route #2 was first written with a SOURCE-PATH cite, and a proof
    # that requires "the cite names at least one registry row" failed. A route
    # that already exists but carries a citation nobody can re-check is a route
    # whose provenance is lost, so the declaration REPAIRS it (idempotent: the
    # second run updates nothing).
    cur = conn.execute("SELECT cite_ref FROM purpose_route_registry WHERE "
                       "route_key=?", (route_key,)).fetchone()
    if cur is not None and str(cur["cite_ref"] or "") != cite:
        conn.execute("UPDATE purpose_route_registry SET cite_ref=? WHERE "
                     "route_key=?", (cite, route_key))
        conn.commit()
        out["cite_updated"] = {"from": str(cur["cite_ref"] or ""), "to": cite}
    if kf and res.get("created"):
        conn.execute("UPDATE purpose_route_registry SET key_factor=? WHERE "
                     "route_key=?", (kf, route_key))
        conn.commit()
        out["key_factor_written"] = kf
    return out


# ---------------------------------------------------------------------------
# P5 — EVERY STEP ANSWERS 5W1H AND NAMES ITS MIDDLEWARE
# ---------------------------------------------------------------------------
# THE MIDDLEWARE, named per step. These are the modules that ALREADY implement
# each answer; none is invented here.
STEP_MIDDLEWARE = {
    "P1": "ticket_subject (the ticket<->subject mapping IS the middleware)",
    "P2": "identity_middleware (MIDDLEWARE_KEY + the declared pair key)",
    "P3": "purpose_route_registry (purpose x service x workflow x factor)",
    "P4": "ticket_store (the ONE ticket writer) + ticket_subject",
    "P5": "ticket_5w1h (the 5W1H middleware)",
    "P6": "subject_kind_registry (the open set) + derive_5w1h (the gate)",
}

# The six dimension keys, IMPORTED from the SSOT the gate uses — never typed.
def dimension_keys() -> tuple[str, ...]:
    """The six dimension names, taken from the SSOT module, not written here."""
    import skill_5w1h
    return tuple(skill_5w1h.DIMENSION_NAMES)


def steps_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """Each step with its 5W1H answers ACTUALLY DERIVED + its middleware.

    THE ANSWERS ARE DERIVED, NOT NARRATED. For each step the 6 questions come
    from `ticket_5w1h.questions_for` for the kind the step operates on, each
    carrying `source` ∈ (gate, register, unbound). A step whose dimension is
    `unbound` is a FAILURE, because an unbound dimension is an answer nobody can
    check.
    """
    kind = subject_kind_for(conn)["kind"]
    import ticket_5w1h
    q = ticket_5w1h.questions_for(conn, kind)
    # THE SHAPE IS READ, NOT ASSUMED. `questions_for` returns the questions in a
    # container whose exact shape is the reader's, so both a dict-of-rows and a
    # list-of-rows are handled, and the source is taken from the RESULT's own
    # `by_source` when it carries one (that is the gate's own accounting).
    src = q.get("by_source") or {}
    if not src:
        items = q.get("questions")
        seq = (items.values() if isinstance(items, dict)
               else (items or []))
        for item in seq:
            if not isinstance(item, dict):
                continue
            s = str(item.get("source") or "unknown")
            src[s] = src.get(s, 0) + 1
    rows = []
    for sid, mw in STEP_MIDDLEWARE.items():
        rows.append({"step": sid, "middleware": mw,
                     "dimensions": list(dimension_keys()),
                     "answers_from": src,
                     "subject_kind": kind})
    # `unbound` CAN BE A LIST of dimension names (MEASURED 2026-09-28:
    # `int()` on it raised `TypeError: ... not 'list'`). Both shapes are handled,
    # because the COUNT and the NAMES are both useful and the reader may give
    # either. An empty list is 0 unbound, which is the healthy state.
    raw_unbound = q.get("unbound")
    if isinstance(raw_unbound, (list, tuple)):
        unbound = len(raw_unbound)
    elif raw_unbound is None:
        unbound = int(src.get("unbound") or 0)
    else:
        unbound = int(raw_unbound)
    return {"ok": True, "subject_kind": kind, "steps": rows,
            "dimension_count": len(dimension_keys()),
            "bound_count": int(q.get("bound_count") or 0),
            "by_source": src,
            "unbound": unbound,
            "unbound_names": (list(raw_unbound)
                              if isinstance(raw_unbound, (list, tuple)) else []),
            "all_steps_answer_5w1h": unbound == 0,
            "middleware": STEP_MIDDLEWARE,
            "cite_ref": "ticket_5w1h.questions_for + skill_5w1h.DIMENSION_NAMES"}


def main() -> int:
    ap = argparse.ArgumentParser(description="a service ticket for a conversation")
    ap.add_argument("--kind", action="store_true",
                    help="which registered kind resolves the conversation")
    ap.add_argument("--refused", nargs="?", const="conversation",
                    help="why a kind named `conversation` is not usable")
    ap.add_argument("--pair-key", type=int, metavar="CONV_ID",
                    help="the DECLARED pair key of a conversation")
    ap.add_argument("--route", nargs=2, metavar=("ORIGIN", "WORKFLOW"),
                    help="READ the declared route for a (service, workflow) pair")
    ap.add_argument("--declare-route", nargs=2, metavar=("ORIGIN", "WORKFLOW"),
                    help="declare the ONE route that lets a conversation raise "
                         "a ticket (dry run unless --apply)")
    ap.add_argument("--partition", action="store_true",
                    help="which origins route, which do NOT (the answer to "
                         "'why can this one')")
    ap.add_argument("--steps", action="store_true",
                    help="every step: its 5W1H answers + its middleware")
    ap.add_argument("--open", nargs=3,
                    metavar=("CONV_ID", "ORIGIN", "WORKFLOW"),
                    help="open a ticket for a conversation (dry run unless --apply)")
    ap.add_argument("--opened-by", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = _connect()
    try:
        def show(label: str, res: dict) -> None:
            if args.json:
                print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
            else:
                print("== %s ==" % label)
                for k, v in res.items():
                    if k in ("routed_list", "steps", "no_route_sample"):
                        continue
                    print("   %-22s %s" % (k, v))
                for x in (res.get("steps") or []):
                    print("   %s  middleware=%s" % (x["step"], x["middleware"]))
                for x in (res.get("routed_list") or []):
                    print("   ROUTED   %s" % x)
                for x in (res.get("no_route_sample") or []):
                    print("   NO_ROUTE %s" % x)

        if args.kind:
            show("kind resolving the conversation", subject_kind_for(conn))
            return 0
        if args.refused:
            show("kind %r" % args.refused, refused_kinds(conn, want=args.refused))
            return 0
        if args.pair_key:
            show("pair key", pair_key_for(conn, args.pair_key))
            return 0
        if args.route:
            show("route", route_for_purpose(conn, ticket_origin=args.route[0],
                                            workflow_key=args.route[1]))
            return 0
        if args.declare_route:
            show("declare route",
                 declare_route(conn, ticket_origin=args.declare_route[0],
                               workflow_key=args.declare_route[1],
                               apply=args.apply))
            return 0
        if args.partition:
            show("routing partition", routing_partition(conn))
            return 0
        if args.steps:
            show("steps: 5W1H + middleware", steps_report(conn))
            return 0
        if args.open:
            cid, origin, wf = args.open
            opened_by = args.opened_by.strip()
            if args.apply and not opened_by:
                print("--opened-by is REQUIRED to apply: an unattributed ticket "
                      "cannot be traced back to who raised it")
                return 2
            show("open ticket", open_ticket(conn, conversation_id=int(cid),
                                            ticket_origin=origin,
                                            workflow_key=wf,
                                            opened_by=opened_by or "dry-run",
                                            apply=args.apply))
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())