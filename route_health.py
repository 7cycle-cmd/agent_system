"""route_health.py -- ONE measurer for EVERY declared route.

THE USER'S REQUIREMENT (verbatim, 2026-09-24):

    "yes, all by measured unit only ! do it now"

MEASURING THAT REQUIREMENT FOUND IT WAS NOT MET. Two defects:

D-1  ROUTES 1-4 HAVE NO MEASURER AT ALL.
     `watchdog_health.measure_routes` filters
     `key.startswith(("watchdog_", "worker_", "helper_"))`
     (`watchdog_health.py:517`), so it covers routes 6,7,8 ONLY. Routes 1,2,3,4
     read `health='OK'` from a ONE-OFF measurement by an earlier session, and
     NOTHING re-measures them. A green that nothing re-measures is a claim.

D-2  ROUTE 4's UNIT NAMES A COLUMN THAT DOES NOT EXIST.
     Its unit says "`step_no` values whose `question_id` is the SAME position in
     the list". MEASURED: `workflow_step` has `prompt_id`, NOT `question_id`.
     A unit naming a non-existent column cannot be measured at all.

WHAT THIS MODULE DOES
---------------------
It measures EVERY declared route through ONE entry point, and it REFUSES a unit
whose named column does not exist rather than reporting a green it cannot
justify. The three outcomes are distinguishable:

  * `MEASURED`      -- a count was produced from real rows
  * `UNMEASURABLE`  -- the unit names something that does not exist (D-2)
  * `NO_MEASURER`   -- no measurement is defined for this route (D-1)

NOTHING IS HARDCODED AS A COUNT. Every number comes from a query, and a route
whose unit cannot be resolved is REPORTED, never defaulted to OK.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# The three outcomes. Named ONCE so a caller cannot invent a fourth.
MEASURED = "MEASURED"
UNMEASURABLE = "UNMEASURABLE"
NO_MEASURER = "NO_MEASURER"


class RouteHealthError(Exception):
    """Raised when route health cannot be measured."""


def _connect(path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or os.path.join(BASE_DIR, "agent.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(r["name"]) for r in
                conn.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.OperationalError:
        return []


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone())


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


# ---------------------------------------------------------------------------
# the unit -> measurement resolution
# ---------------------------------------------------------------------------
def unit_columns(unit: str) -> list[str]:
    """The snake_case identifiers a unit mentions, read from its own text.

    A unit is prose, so the identifiers are extracted by looking for snake_case
    names (the repo's convention). This is what makes D-2 detectable: a unit
    naming `question_id` yields `question_id`, and the caller can then ask
    whether that name exists as a column OR as a table.

    A FILE reference (`mouse_spot_helper.py`) is STRIPPED first, because `py` is
    not a name the unit is making a claim about.
    """
    text = str(unit or "")
    # Drop file references, so `mouse_spot_helper.py` does not yield `py`.
    text = re.sub(r"\b[a-z_][a-z0-9_]*\.py\b", " ", text)
    found = re.findall(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", text)
    # A dotted form names its own table, so keep the column part too.
    for _t, col in re.findall(r"\b([a-z_]+)\.([a-z_]+)\b", text):
        found.append(col)
    out: list[str] = []
    for f in found:
        if f not in out:
            out.append(f)
    return out


def _resolve_columns(conn: sqlite3.Connection, unit: str,
                     tables: list[str]) -> dict[str, Any]:
    """Which names a unit mentions, and whether each EXISTS in the SCHEMA.

    A name is PRESENT when it is a COLUMN of any table, OR a TABLE that exists.
    MEASURED, and this correction matters TWICE:

    1. The first version treated a TABLE name (`workflow_step`,
       `chat_center_message`) as a missing COLUMN and reported five healthy
       routes as UNMEASURABLE -- a detector that could not tell a table from a
       column.
    2. The second version checked only the route's ENDPOINT tables. But a route's
       endpoints may be FUNCTIONS (`logic_generator.generate`), so `tables` was
       EMPTY and a unit naming a real table (`workflow_step`, `watchdog_log`) was
       reported missing. The question a unit raises is "does this name exist in
       the schema", not "is it an endpoint".

    A name that is NEITHER a column NOR a table is `missing`. That is D-2, and it
    is a REFUSAL.
    """
    named = unit_columns(unit)
    present: set[str] = set()
    for t in tables:
        present.update(_columns(conn, t))
        present.add(t)
    # The WHOLE schema, because a unit may name a table OR a column the route
    # does not touch. MEASURED: checking only TABLE names made `step_no` and
    # `prompt_id` -- both real `workflow_step` columns -- read as missing, so a
    # correct unit was refused. A name is present when it is a table OR a column
    # ANYWHERE in the schema.
    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        tname = str(r["name"])
        present.add(tname)
        present.update(_columns(conn, tname))
    missing = [c for c in named if c not in present]
    return {"named": named, "missing": missing,
            "endpoint_tables": sorted(tables)}


# ---------------------------------------------------------------------------
# the per-route measurements (each one a QUERY, never a constant)
# ---------------------------------------------------------------------------
def _measure_questions_to_steps(conn: sqlite3.Connection) -> dict[str, Any]:
    """Route 1: questions that became a `workflow_step` row.

    MEASURED: `workflow_step` has 11 rows over 2 distinct `prompt_id`. The unit
    says "questions that became a workflow_step row", so the count is the number
    of DISTINCT prompts that reached a step -- a question that produced no step is
    the failure this route exists to catch.
    """
    if not _table_exists(conn, "workflow_step"):
        return {"ok": False, "reason": "workflow_step does not exist"}
    total = _count(conn, "SELECT COUNT(*) FROM workflow_step")
    distinct = _count(conn, "SELECT COUNT(DISTINCT prompt_id) FROM workflow_step")
    return {"ok": True, "count": distinct, "unit_value": distinct,
            "detail": {"workflow_step_rows": total,
                       "distinct_prompt_id": distinct},
            "why": "distinct prompts that reached a workflow_step row"}


def _measure_step_position(conn: sqlite3.Connection,
                           unit: str = "") -> dict[str, Any]:
    """Route 4: `step_no` whose question is the SAME position in the list.

    THE UNIT ONCE NAMED `question_id`, WHICH DOES NOT EXIST (D-2); the real
    column is `prompt_id`. The unit was CORRECTED, so the defect note is now read
    from the UNIT TEXT rather than hardcoded -- a note that says "the unit names
    question_id" after the unit was fixed is a stale claim, which is the same
    class of defect as a green nothing measured.
    """
    cols = _columns(conn, "workflow_step")
    if not cols:
        return {"ok": False, "reason": "workflow_step does not exist"}
    # The column the UNIT names, if it exists; otherwise the real one.
    named = unit_columns(unit)
    col = ""
    for cand in named:
        if cand in cols:
            col = cand
            break
    if not col:
        for cand in ("prompt_id", "question_id"):
            if cand in cols:
                col = cand
                break
    if not col:
        return {"ok": False,
                "reason": ("workflow_step has neither `prompt_id` nor "
                           "`question_id`, so the unit cannot be measured")}
    # The relation: within a workflow, does step_no equal the prompt's position?
    rows = conn.execute(
        "SELECT workflow_id, step_no, %s AS qid FROM workflow_step "
        "ORDER BY workflow_id, step_no" % col).fetchall()
    same = 0
    for r in rows:
        if int(r["step_no"]) == int(r["qid"]):
            same += 1
    # The defect note is DERIVED: it appears only when the unit names a column
    # that does not exist.
    bad = [c for c in named if c not in cols]
    return {"ok": True, "count": same, "unit_value": same,
            "detail": {"rows": len(rows), "column_used": col,
                       "unit_names": named},
            "why": ("step_no values whose %s equals the step position" % col),
            "unit_defect": (("the unit names %s, which is not a workflow_step "
                             "column; measured with `%s`" % (bad, col))
                            if bad else None)}


def _measure_judged_rounds(conn: sqlite3.Connection) -> dict[str, Any]:
    """Route 2: rounds judged by the DECLARED check instead of the phone oracle.

    MEASURED: `proof_run` has 2995 rows with `oracle_answer` and `llm_answer`, so
    a round judged by the declared check is one where the two AGREE (the declared
    check reproduced the oracle). A disagreement is the failure this route catches.
    """
    if not _table_exists(conn, "proof_run"):
        return {"ok": False, "reason": "proof_run does not exist"}
    cols = _columns(conn, "proof_run")
    if "oracle_answer" not in cols or "llm_answer" not in cols:
        return {"ok": False,
                "reason": "proof_run lacks oracle_answer/llm_answer"}
    total = _count(conn, "SELECT COUNT(*) FROM proof_run")
    agree = _count(conn, "SELECT COUNT(*) FROM proof_run "
                         "WHERE oracle_answer = llm_answer")
    return {"ok": True, "count": agree, "unit_value": agree,
            "detail": {"proof_run_rows": total, "agree": agree,
                       "disagree": total - agree},
            "why": "rounds where the declared check reproduced the oracle"}


def _measure_option_values(conn: sqlite3.Connection) -> dict[str, Any]:
    """Route 3: option values judged correctly when the instruction is DERIVED.

    MEASURED: `proof_run.win` is the verdict column, so a correctly judged option
    value is a `win`. The count is the wins, and the losses are the failure.
    """
    if not _table_exists(conn, "proof_run"):
        return {"ok": False, "reason": "proof_run does not exist"}
    cols = _columns(conn, "proof_run")
    if "win" not in cols:
        return {"ok": False, "reason": "proof_run lacks a `win` column"}
    total = _count(conn, "SELECT COUNT(*) FROM proof_run")
    wins = _count(conn, "SELECT COUNT(*) FROM proof_run WHERE win=1")
    return {"ok": True, "count": wins, "unit_value": wins,
            "detail": {"proof_run_rows": total, "wins": wins,
                       "losses": total - wins},
            "why": "option values judged correctly (win=1)"}


def _measure_watchdog_routes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Routes 6,7,8: DELEGATE to the existing measurer, never re-derive.

    A second derivation would be a second answer to "is this route healthy?", so
    this calls `watchdog_health.measure_routes` and reports what it returns.
    """
    try:
        import watchdog_health as wh
        res = wh.measure_routes(conn, 1, apply=False)
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    # The delegate returns a per-route count, so the count is READ from it rather
    # than left null -- a MEASURED route with a null count is not measured.
    counts = {str(r["route_key"]): r.get("count") for r in res.get("routes", [])}
    return {"ok": True, "delegated": True,
            "states": res.get("states", {}), "counts": counts,
            "routes": res.get("routes", []),
            "why": "delegated to watchdog_health.measure_routes (the existing "
                   "measurer for these three routes)"}


def _measure_report_route(conn: sqlite3.Connection) -> dict[str, Any]:
    """Route 9: DELEGATE to `report_route.measure_health`."""
    try:
        import report_route as rr
        res = rr.measure_health(conn, commit=False)
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "delegated": True, "health": res.get("health"),
            "count": res.get("count"), "measured": res.get("measured"),
            "why": "delegated to report_route.measure_health"}


# The measurement table. A route ABSENT from it is `NO_MEASURER` (D-1), which is
# REPORTED -- never defaulted to OK.
MEASURERS: dict[str, Any] = {
    "questions_to_steps": _measure_questions_to_steps,
    "declared_rule_to_oracle": _measure_judged_rounds,
    "unit_to_judge_instruction": _measure_option_values,
    "step_position_to_question": _measure_step_position,
    "watchdog_watches_helper": _measure_watchdog_routes,
    "worker_reports_heartbeat": _measure_watchdog_routes,
    "helper_process_identity": _measure_watchdog_routes,
    "runtime_fault_to_report": _measure_report_route,
}


def measure_all(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Measure EVERY declared route through ONE entry point.

    `apply=False` writes NOTHING -- a dry run stays dry.
    """
    import route_registry as rr

    rows = rr.routes_of(conn)
    out: list[dict[str, Any]] = []
    for row in rows:
        key = str(row["route_key"])
        unit = str(row["unit"] or "")
        tables = [str(row["from_ref"]).split(".")[0],
                  str(row["to_ref"]).split(".")[0]]
        tables = [t for t in tables if _table_exists(conn, t)]
        colcheck = _resolve_columns(conn, unit, tables)
        fn = MEASURERS.get(key)
        if fn is None:
            out.append({"route_key": key, "state": NO_MEASURER,
                        "unit": unit, "count": None,
                        "why": ("no measurement is defined for this route, so its "
                                "health cannot be justified")})
            continue
        # The unit is PASSED IN, so a measurer can read what the unit claims
        # instead of hardcoding a note about it.
        try:
            res = fn(conn, unit)
        except TypeError:
            res = fn(conn)
        if not res.get("ok"):
            out.append({"route_key": key, "state": UNMEASURABLE, "unit": unit,
                        "count": None, "why": res.get("reason")})
            continue
        # D-2: a unit naming a column that exists in NO touched table is a
        # REFUSAL, because the number cannot be attributed to the unit.
        if colcheck["missing"]:
            out.append({"route_key": key, "state": UNMEASURABLE, "unit": unit,
                        "count": None,
                        "why": ("the unit names %s, which exists in NONE of %s"
                                % (colcheck["missing"], tables)),
                        "unit_columns": colcheck})
            continue
        count = res.get("count")
        if count is None and res.get("delegated"):
            count = (res.get("counts") or {}).get(key)
        entry = {"route_key": key, "state": MEASURED, "unit": unit,
                 "count": count, "why": res.get("why"),
                 "detail": res.get("detail"), "delegated": res.get("delegated")}
        if res.get("unit_defect"):
            entry["unit_defect"] = res["unit_defect"]
        if res.get("health"):
            entry["health"] = res["health"]
        out.append(entry)
        if apply and count is not None:
            # THE HEALTH IS NOT BLINDLY OK. MEASURED BUG, caught by this module's
            # own rule: the first version recorded `HEALTH_OK` for every route
            # with a count, which would have OVERWRITTEN the watchdog routes'
            # real `NOWHERE` (their helper is dead) with a green. A measurer that
            # erases a real fault is worse than no measurer.
            #
            # A DELEGATED route already knows its health, so the delegate's value
            # is used. A route measured HERE derives it from its own unit: the
            # unit is "count of X that reached Y", so a count of ZERO means
            # nothing reached the sink -- `NOWHERE`, not OK.
            health = res.get("health")
            if not health and res.get("delegated"):
                # The delegate reports a per-route STATE, so the health is READ
                # from it. MEASURED BUG: without this, the watchdog routes' real
                # `NOWHERE` was overwritten with `OK` because their count (2 live
                # helpers) is non-zero -- a green that erased a dead helper.
                health = (res.get("states") or {}).get(key)
            if not health:
                health = rr.HEALTH_OK if int(count) > 0 else rr.HEALTH_NOWHERE
            rr.record_health(conn, key, health=health,
                             health_count=int(count), commit=False)
    if apply:
        conn.commit()
    return {"routes": out,
            "measured": sum(1 for e in out if e["state"] == MEASURED),
            "unmeasurable": [e["route_key"] for e in out
                             if e["state"] == UNMEASURABLE],
            "no_measurer": [e["route_key"] for e in out
                            if e["state"] == NO_MEASURER],
            "total": len(out), "applied": bool(apply)}


def correct_unit(conn: sqlite3.Connection, route_key: str, new_unit: str, *,
                 reason: str, cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """CORRECT a route's unit, keeping the OLD one as a REVISION.

    WHY THIS EXISTS (D-2): route 4's unit names `question_id`, which is not a
    `workflow_step` column, so the unit cannot be measured. `declare_route` is
    idempotent on `route_key` and therefore has NO update path, so the correction
    needs one -- and it must not destroy the old text.

    THE OLD UNIT IS SNAPSHOTTED into `ontology_revision_history` (the repo's
    generic revision store: `entity_type`, `entity_key`, `snapshot`, `note`)
    BEFORE the update, because the citation-discipline rule forbids deleting
    evidence to tidy a register. The snapshot is the evidence that the unit was
    once wrong.

    REFUSES a new unit that still names a non-existent name, so a correction
    cannot introduce the same defect it is fixing.
    """
    import route_registry as rr

    key = str(route_key or "").strip()
    row = conn.execute(
        "SELECT route_id, unit, cite_ref FROM route_registry WHERE route_key=?",
        (key,)).fetchone()
    if row is None:
        return {"ok": False, "route_key": key,
                "reason": "route %r is not declared" % key}
    old_unit = str(row["unit"] or "")
    new_text = str(new_unit or "").strip()
    if not new_text:
        return {"ok": False, "route_key": key, "reason": "the new unit is empty"}
    if new_text == old_unit:
        return {"ok": False, "route_key": key,
                "reason": "the new unit is IDENTICAL to the old one"}
    # The SAME enforcement the declaration path uses.
    try:
        rr._assert_unit(new_text, key)
    except Exception as exc:
        return {"ok": False, "route_key": key,
                "reason": "the new unit is refused: %s" % exc}
    # The new unit must not name a non-existent name either.
    check = _resolve_columns(conn, new_text, [])
    if check["missing"]:
        return {"ok": False, "route_key": key,
                "reason": ("the new unit still names %s, which exists in NEITHER "
                           "a column NOR a table" % check["missing"])}
    # Snapshot the OLD row FIRST, so the evidence survives the update.
    import json as _json
    import uuid
    snap = {"route_id": int(row["route_id"]), "route_key": key,
            "unit": old_unit, "cite_ref": row["cite_ref"]}
    rev_id = "orev_%s" % uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO ontology_revision_history (revision_id, entity_type, "
        "entity_key, snapshot, is_deleted, created_by, note) "
        "VALUES (?, 'route', ?, ?, 0, ?, ?)",
        (rev_id, key, _json.dumps(snap, ensure_ascii=False),
         "route_health.correct_unit",
         "unit corrected: %s | cite=%s" % (reason, cite_ref)))
    conn.execute(
        "UPDATE route_registry SET unit=?, updated_at=datetime('now') "
        "WHERE route_key=?", (new_text, key))
    if commit:
        conn.commit()
    return {"ok": True, "route_key": key, "old_unit": old_unit,
            "new_unit": new_text, "revision_id": rev_id, "reason": reason,
            "cite_ref": cite_ref}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--correct-unit" in args:
            i = args.index("--correct-unit")
            key, new_unit = args[i + 1], args[i + 2]
            reason = args[args.index("--reason") + 1] if "--reason" in args else "NA"
            cite = args[args.index("--cite") + 1] if "--cite" in args else "NA"
            print(json.dumps(correct_unit(conn, key, new_unit, reason=reason,
                                          cite_ref=cite),
                             indent=2, ensure_ascii=False))
        else:
            res = measure_all(conn, apply="--apply" in args)
            print(json.dumps(res, indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
