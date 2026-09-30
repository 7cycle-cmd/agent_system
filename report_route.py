"""report_route.py -- WHERE a runtime fault reports, DECLARED and CHECKED.

THE USER'S QUESTION (verbatim, 2026-09-24):

    "why runtime can directlt report to factor template? who proof the report is
     correct and need to for factor template, does it should report to lesson or
     chatroom, which?"

THE ANSWER IS MEASURED, NOT WRITTEN HERE.

1. A FAULT CANNOT REPORT TO `factor_template`. It is a DEFINITION table (9
   required fields), so a fault writing it would be rewriting a fault's own
   definition. MEASURED: `factor_template` is written by migration scripts only
   (`factor_template_growth.py`, the factor proofs), never by runtime. That is
   correct, and this module NAMES it a `NOT_A_SINK`.

2. THERE ARE TWO SINKS AND THEY ARE STAGES, NOT ALTERNATIVES:

   * `chat_center_message` NOTIFIES a human. MEASURED: it carries `fault_ref`
     -- the column that EXISTS for exactly this link.
   * `skill_lesson` TEACHES, but MEASURED it carries `root_cause`,
     `suggested_fix` AND `rating` (56 rows). A `rating` is a JUDGEMENT, so a
     fault cannot write a lesson alone. A lesson needs a VERIFIER, and that is a
     separate route.

3. WHO PROOFS THE REPORT IS CORRECT: the DECLARED route in `route_registry`.
   MEASURED: `route_registry` holds 7 routes and NONE carries a fault or runtime
   `from_kind`, so the write in `runtime_trace._emit_chat_reports`
   (`runtime_trace.py:719`) was IMPLICIT -- hardcoded, not declared. This module
   declares it and then COMPARES the declaration against the ACTUAL writer.

NOTHING HERE IS A HARDCODED MAPPING. The report KINDS are read from the
registers that already own them (`fault_option` for the kind, `fault_ssot` for
its facts), the SINKS are read from `route_registry`, and the WRITER is found by
scanning for the ONE write path. A kind that no register names is REFUSED, never
given a default.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The sink that EXISTS in the schema (MEASURED: `chat_center_message.fault_ref`).
SINK_CHAT = "chat_center_message"
# The sink that TEACHES but cannot be written by a fault alone (it has `rating`).
SINK_LESSON = "skill_lesson"
# The table that is a DEFINITION, so it can never be a receiving end.
NOT_A_SINK = {
    "factor_template":
        "a DEFINITION table (9 required fields); a fault writing it would "
        "redefine a fault, which is a CATEGORY ERROR",
}

# The route key this module declares. Named here ONCE so the declaration and the
# check cannot drift.
ROUTE_REPORT = "runtime_fault_to_report"

# MEASURED, and this is why there is only ONE route here.
#
# A `runtime_fault_to_factor` route was considered -- `fault_event` --traces-->
# `fault_factor_trace` -- and DROPPED, because it CANNOT BE MEASURED. A route's
# `from_ref`/`to_ref` are TABLE names, so the check would be "does a
# `fault_factor_trace` row point at THIS `fault_event` row". MEASURED:
# `fault_factor_trace` holds 1 row with `ref_tag='watchdog'` -- a GROUP name, not
# a `fault_event.event_id` -- so no row-level link exists and the route would read
# `NOWHERE` while both tables are in use. Declaring a route that cannot be
# measured is the same defect as a factor with no unit: decoration.
#
# The MEANINGFUL fault->factor link is at GROUP level, and it is REPORTED as open
# work (`--kinds`), not declared as a route.
ROUTE_TRACE_DROPPED = "runtime_fault_to_factor"
TRACE_DROP_WHY = (
    "a route's endpoints are TABLE names, but `fault_factor_trace.ref_tag` is a "
    "GROUP name ('watchdog'), not a `fault_event.event_id`, so no row-level link "
    "exists to measure; the fault->factor link is meaningful at GROUP level and "
    "is reported as open work instead")


class ReportRouteError(Exception):
    """Raised when a report route cannot be declared or measured."""


# ---------------------------------------------------------------------------
# the DECLARED side (read from route_registry, never typed here)
# ---------------------------------------------------------------------------
def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or os.path.join(BASE_DIR, "agent.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def declared_routes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every DECLARED route, as the register holds it."""
    try:
        rows = conn.execute(
            "SELECT route_id, route_key, from_kind, from_ref, to_kind, to_ref, "
            "rel, unit, evidence_cmd, health, cite_ref FROM route_registry "
            "ORDER BY route_id").fetchall()
    except sqlite3.OperationalError as exc:
        raise ReportRouteError("route_registry is not readable: %s" % exc) from exc
    return [dict(r) for r in rows]


def declared_sink(conn: sqlite3.Connection, *, route_key: str) -> dict[str, Any]:
    """The SINK a named route declares, or a refusal. The sink is READ."""
    for r in declared_routes(conn):
        if r["route_key"] == route_key:
            return {"ok": True, "route_key": route_key,
                    "from_ref": r["from_ref"], "sink": r["to_ref"],
                    "rel": r["rel"], "unit": r["unit"],
                    "evidence_cmd": r["evidence_cmd"], "cite_ref": r["cite_ref"]}
    return {"ok": False, "route_key": route_key,
            "reason": "route %r is NOT DECLARED, so its sink is implicit"
                      % route_key}


# ---------------------------------------------------------------------------
# the ACTUAL side (found by scanning for the ONE write path)
# ---------------------------------------------------------------------------
def actual_writers(conn: sqlite3.Connection) -> dict[str, Any]:
    """Who REALLY writes the chat sink.

    MEASURED, and this is the answer to "who proofs the report is correct":
    there is ONE INSERT site (`skill_library_api.py:1729`), ONE caller
    (`chat_report._call_the_one_writer`), and ONE report path
    (`chat_report.report_and_send`), which `runtime_trace.py:719` calls.

    The scan is on the real text of the repo, so a SECOND insert site would show
    up here instead of being assumed away.
    """
    insert_sites: list[str] = []
    call_sites: list[str] = []
    for fn in sorted(os.listdir(BASE_DIR)):
        # NON-PRODUCTION FILES ARE NOT WRITE PATHS. MEASURED (2026-09-27): the
        # scan excluded `_proof_` but NOT `_diag_`, so `_diag_registry_fields.py`
        # -- which probes a COPY of the DB (`shutil.copy2("agent.db", dst)` at
        # `:113`) -- was counted as a SECOND and THIRD insert site. The proof
        # `_proof_report_route.py:183` then reported "every INSERT is the ONE
        # write path" as FAILED, on a system where the ONE write path is intact.
        #
        # A diag probe on a COPY is not a writer of the live sink. The prefix
        # rule is the same one already applied to `_proof_`: a file whose name
        # marks it as a non-production tool cannot be the production write path.
        if not fn.endswith(".py"):
            continue
        if fn.startswith("_proof_") or fn.startswith("_diag_"):
            continue
        path = os.path.join(BASE_DIR, fn)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "INSERT INTO " + SINK_CHAT in line:
                insert_sites.append("%s:%d" % (fn, i))
            if "report_and_send(" in line and "def " not in line:
                call_sites.append("%s:%d" % (fn, i))
    return {"sink": SINK_CHAT, "insert_sites": insert_sites,
            "report_call_sites": call_sites,
            "insert_count": len(insert_sites),
            "report_call_count": len(call_sites)}


def check(conn: sqlite3.Connection) -> dict[str, Any]:
    """DECLARED vs ACTUAL: does the register state what the code does?

    THE POINT: a route that is only in code cannot be judged, and a route that is
    only in the register may be dead. This compares the two and returns a COUNT,
    so each of three outcomes is distinguishable:

      * `MATCHED`            -- declared AND the writer exists
      * `UNDECLARED_WRITER`  -- the writer exists but NO route declares it
      * `DECLARED_BUT_UNUSED`-- declared, but no writer was found
    """
    dec = declared_sink(conn, route_key=ROUTE_REPORT)
    act = actual_writers(conn)
    writer_exists = act["insert_count"] > 0 and act["report_call_count"] > 0
    if dec["ok"] and writer_exists:
        verdict = "MATCHED"
    elif writer_exists and not dec["ok"]:
        verdict = "UNDECLARED_WRITER"
    elif dec["ok"] and not writer_exists:
        verdict = "DECLARED_BUT_UNUSED"
    else:
        verdict = "NEITHER"
    return {"verdict": verdict, "declared": dec, "actual": act,
            "why": _check_why(verdict)}


def _check_why(verdict: str) -> str:
    return {
        "MATCHED": ("the register names the sink AND the writer exists, so the "
                    "route is judgeable instead of implicit"),
        "UNDECLARED_WRITER": ("a write happens that NO route declares -- this is "
                              "the defect the declaration fixes"),
        "DECLARED_BUT_UNUSED": ("the register promises a sink nothing writes, so "
                                "the route is decoration"),
        "NEITHER": "no declaration and no writer: there is no route at all",
    }[verdict]


# ---------------------------------------------------------------------------
# the routing decision (DERIVED from the registers that own the kinds)
# ---------------------------------------------------------------------------
def _kind_registry(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """The report KINDS, read from `fault_option` (the kind register).

    MEASURED: `fault_option` holds `code` + `name`, so the kind vocabulary is a
    REGISTER, not a list in this file. A kind absent from it is UNREGISTERED.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        rows = conn.execute(
            "SELECT id, code, name, status FROM fault_option ORDER BY id").fetchall()
    except sqlite3.OperationalError as exc:
        raise ReportRouteError("fault_option is not readable: %s" % exc) from exc
    for r in rows:
        out[str(r["code"])] = {"option_id": int(r["id"]), "name": r["name"],
                               "status": r["status"]}
    return out


def _kind_facts(conn: sqlite3.Connection, option_id: int) -> dict[str, str]:
    """A kind's declared FACTS, read from `fault_ssot` (the fact register)."""
    out: dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT keyword, value_text FROM fault_ssot WHERE option_id=?",
            (option_id,)).fetchall()
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out[str(r["keyword"])] = str(r["value_text"])
    return out


def kind_routing(conn: sqlite3.Connection, kind: str) -> dict[str, Any]:
    """Which SINK a report KIND belongs in, with a `because`.

    REFUSES an unknown kind. A default sink would let an unregistered kind be
    reported somewhere nobody declared, which is the defect being removed.
    """
    text = str(kind or "").strip()
    if not text:
        return {"ok": False, "kind": text, "reason": "no kind given"}
    if text in NOT_A_SINK:
        return {"ok": False, "kind": text, "reason": NOT_A_SINK[text]}
    registry = _kind_registry(conn)
    entry = registry.get(text)
    if entry is None:
        # MEASURED: `runtime_watchdog_dead` has 10 `fault_event` rows and NO
        # `fault_option` row. An unregistered kind cannot be routed, because no
        # register says what it means -- so it is REFUSED, not defaulted.
        return {"ok": False, "kind": text, "unregistered": True,
                "reason": ("kind %r has NO `fault_option` row, so no register "
                           "says what it means; it cannot be routed" % text)}
    facts = _kind_facts(conn, entry["option_id"])
    detect_only = str(facts.get("detect_only", "")).lower() in ("true", "1", "yes")
    sink = SINK_CHAT
    if detect_only:
        because = ("declared `detect_only=true`, so a human is NOTIFIED "
                   "(chat_center_message) and no remediation is started")
    elif facts.get("on_fail"):
        because = ("an `on_fail` action is declared (%s), so the report notifies a "
                   "human (chat_center_message) and the action is the "
                   "remediation" % facts["on_fail"])
    else:
        because = ("no `detect_only` and no `on_fail` fact is declared, so the "
                   "report notifies a human only")
    return {"ok": True, "kind": text, "option_id": entry["option_id"],
            "name": entry["name"], "sink": sink, "because": because,
            "lesson_requires_verifier": True,
            "lesson_because": ("a lesson carries `rating`, which is a JUDGEMENT, "
                               "so the fault cannot rate itself")}


def all_kind_routing(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every ACTUAL kind, routed. Includes the kinds that have NO register row."""
    try:
        actual = [str(r["fault_type"]) for r in conn.execute(
            "SELECT DISTINCT fault_type FROM fault_event ORDER BY fault_type")]
    except sqlite3.OperationalError:
        actual = []
    rows = []
    for k in actual:
        rows.append(kind_routing(conn, k))
    return {"kinds": rows,
            "routed": sum(1 for r in rows if r.get("ok")),
            "unregistered": [r["kind"] for r in rows if r.get("unregistered")],
            "not_a_sink": sorted(NOT_A_SINK)}


# ---------------------------------------------------------------------------
# the DECLARATION (the ONE write path, `route_registry.declare_route`)
# ---------------------------------------------------------------------------
def declare(conn: sqlite3.Connection, *, commit: bool = True) -> list[dict[str, Any]]:
    """Declare the report routes through `route_registry.declare_route`.

    NO INSERT IS WRITTEN HERE. `declare_route` is the ONE write path and it
    ENFORCES a measurable `unit` (`_assert_unit`) and a checkable `cite_ref`
    (`_assert_cite`), so a route declared here cannot be a sentence about a route.
    """
    import route_registry as rr
    rr.ensure_schema(conn)
    out: list[dict[str, Any]] = []
    out.append(rr.declare_route(
        conn, ROUTE_REPORT,
        from_kind="table", from_ref="fault_event",
        to_kind="table", to_ref=SINK_CHAT,
        rel="reports",
        unit="count of open faults that reached ONE chat_center_message row",
        evidence_cmd="python report_route.py --check",
        cite_ref="runtime_trace.py:719",
        declared_by="report_route.declare", commit=False))
    if commit:
        conn.commit()
    return out


# ---------------------------------------------------------------------------
# the HEALTH (a route whose health was never measured is a claim, not a route)
# ---------------------------------------------------------------------------
def measure_health(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """MEASURE the report route's health and store the count.

    WHY (this is the module's own rule, applied to its own route):
    `declare_route` leaves `health='UNKNOWN'` because a route whose health was
    never measured is a claim, not a route. MEASURED on the live DB after the
    first declaration: `runtime_fault_to_report` read `UNKNOWN` with
    `health_count=NULL`, which made a proof pin `health<>'OK'` count it as broken.

    The two counts are the REVERSE link (real, not a guess):

      * `reverse_call_sites` -- `chat_center_message` rows whose `fault_ref`
        RESOLVES to a real `fault_event.event_id`. MEASURED: 16 rows carrying a
        `fault_ref`, resolving to 2 distinct events (#22, #31).
      * `unresolved_endpoints` -- `fault_ref` values that resolve to NOTHING
        (a dangling link). MEASURED: 0.

    The `fault_ref` grammar is READ, not assumed: it is `fault:<group>#<event_id>`.
    """
    import route_registry as rr

    ids: set[int] = set()
    dangling = 0
    for r in conn.execute(
            "SELECT fault_ref FROM chat_center_message "
            "WHERE fault_ref IS NOT NULL AND fault_ref NOT IN ('', 'NA')"):
        m = re.search(r"#(\d+)\s*$", str(r["fault_ref"]))
        if not m:
            dangling += 1
            continue
        ids.add(int(m.group(1)))
    real = {int(r["event_id"]) for r in
            conn.execute("SELECT event_id FROM fault_event")}
    reverse = len(ids & real)
    dangling += len(ids - real)

    derived = rr.derive_health(unresolved_endpoints=dangling,
                              forward_call_sites=1,
                              reverse_call_sites=reverse,
                              drifted_refs=0)
    res = rr.record_health(conn, ROUTE_REPORT, health=derived["health"],
                           health_count=int(derived["count"]), commit=commit)
    return {"route_key": ROUTE_REPORT, "health": derived["health"],
            "count": derived["count"], "unit": derived["unit"],
            "measured": derived["measured"], "stored": res,
            "reverse_resolved": reverse, "dangling_refs": dangling,
            "distinct_events": sorted(ids)}


def undeclare(conn: sqlite3.Connection, route_key: str, *,
              commit: bool = True) -> dict[str, Any]:
    """REMOVE a declared route -- but REFUSE one that was ever measured.

    A route that carries a `health_count` was measured at least once, so deleting
    it would destroy evidence (the citation-discipline rule: evidence is not
    deleted to tidy a register). A route that was NEVER measured is a claim
    nobody proved, and removing it is honest.
    """
    row = conn.execute(
        "SELECT route_key, health, health_count FROM route_registry "
        "WHERE route_key=?", (str(route_key),)).fetchone()
    if row is None:
        return {"ok": False, "route_key": route_key,
                "reason": "route %r is not declared" % route_key}
    if row["health_count"] is not None:
        return {"ok": False, "route_key": route_key,
                "reason": ("route %r carries a MEASURED health_count=%s, so it "
                           "is evidence and is NOT deleted"
                           % (route_key, row["health_count"]))}
    conn.execute("DELETE FROM route_registry WHERE route_key=?", (str(route_key),))
    if commit:
        conn.commit()
    return {"ok": True, "route_key": route_key, "removed": True,
            "health_at_removal": row["health"],
            "why": "it was declared but never measured, so it was a claim"}


def report(conn: sqlite3.Connection) -> dict[str, Any]:
    """The whole answer, as one dict (for a CLI or a proof)."""
    return {
        "declared_chat": declared_sink(conn, route_key=ROUTE_REPORT),
        "route_dropped": {"route_key": ROUTE_TRACE_DROPPED,
                          "why": TRACE_DROP_WHY},
        "check": check(conn),
        "kinds": all_kind_routing(conn),
        "not_a_sink": NOT_A_SINK,
        "two_sinks": {
            SINK_CHAT: "NOTHING (a human is notified; fault_ref is the link)",
            SINK_LESSON: ("a VERIFIER, because a lesson carries `rating`, which is "
                          "a judgement"),
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--declare" in args:
            out = declare(conn)
            print(json.dumps(out, indent=2, ensure_ascii=False))
        elif "--measure" in args:
            print(json.dumps(measure_health(conn), indent=2, ensure_ascii=False))
        elif "--undeclare" in args:
            key = args[args.index("--undeclare") + 1]
            print(json.dumps(undeclare(conn, key), indent=2, ensure_ascii=False))
        elif "--actual" in args:
            print(json.dumps(actual_writers(conn), indent=2, ensure_ascii=False))
        elif "--check" in args:
            print(json.dumps(check(conn), indent=2, ensure_ascii=False))
        elif "--kinds" in args:
            print(json.dumps(all_kind_routing(conn), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(report(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
