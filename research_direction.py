# -*- coding: utf-8 -*-
"""research_direction.py — the TWO directions of a question flow, as data.

THE HUMAN'S SPEC (2026-09-27), verbatim
----------------------------------------
    "question flow can narrow area -> to the point, that is the way to have work
     for LLM ( not 7B only)"
    "narrow area -> to the point = proof"
    "point -> collect evidence -> expend area = research"

TWO DIRECTIONS, STATED AS A PAIR
---------------------------------
    narrow    area  -> point      PROOF     shrink the candidate set until ONE
                                            answer decides it
    research  point -> area       RESEARCH  collect evidence and GROW the
                                            candidate set back into an area

MEASURED before this module: only the first existed. `question_flow.py` states it
in its own docstring (*"a question is a STEP, and steps NARROW"*), and `run_flow`
implements it. The reverse — start from a point, collect evidence, expand — had no
writer, no table and no proof anywhere in the repo.

WHY BOTH DIRECTIONS NEED A TABLE (and why only ONE new table exists)
--------------------------------------------------------------------
A `narrow` result IS a flow, and `question_flow` already stores it in
`workflow_registry` / `workflow_step`. Nothing new is needed there.

A `research` result IS NOT a flow: it is a POINT plus the evidence collected plus
the AREA that opened up. Nothing in the repo records that triple, so
`research_frontier` is the ONE new table. It is new because the FACT is new, not
because a table is convenient.

THE RECIPROCAL RULE — THE POINT OF THIS MODULE
-----------------------------------------------
    a `research` result MUST NAME the `narrow` question that would CLOSE it.

MEASURED REASON: a research that cannot name its closing question is an
EXPLORATION WITH NO END. The repo already forbids that shape under a different
name — `qa.memory/no_explore_guard.md`: *"Repeated identical tool calls = loop
detected, abort exploration"*. An unbounded research is the same defect one level
up: it consumes work and can never report done, because nothing states what
"done" is. So `research()` REFUSES a frontier whose `closing_flow_key` is empty,
and it does NOT fall back to a default — a default closing question would be a
DOOR THAT ALWAYS OPENS, which is the same as no door.

The two directions are therefore checked AGAINST EACH OTHER, and
`directions_are_reciprocal(conn, ...)` MEASURES it: a narrow must SHRINK the
candidate count and a research must GROW it. If both shrink, one of them is
mislabelled, and the measurement says so instead of the label.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"

# The two directions. The KEY is the stored value; the MEANING is what makes the
# key checkable rather than a label someone remembers.
DIRECTIONS: tuple[str, ...] = ("narrow", "research")

DIRECTION_MEANING: dict[str, str] = {
    "narrow": "area -> point: shrink the candidate set until ONE answer decides it",
    "research": "point -> area: collect evidence and grow the candidate set",
}

# The direction a stored row is. A row naming a direction outside `DIRECTIONS` is
# a defect, and `_check_direction` refuses it rather than guessing.
def _check_direction(direction: str) -> str:
    d = str(direction or "").strip()
    if d not in DIRECTIONS:
        raise DirectionError(
            "direction %r is not one of %s (meaning: %s). A direction that is "
            "not declared cannot be told from the other one."
            % (direction, DIRECTIONS,
               "; ".join("%s = %s" % (k, v) for k, v in DIRECTION_MEANING.items())))
    return d


class DirectionError(ValueError):
    """Raised when a direction, a frontier or a closing question is invalid."""


class ExplorationWithNoEnd(DirectionError):
    """Raised when a `research` cannot name the `narrow` question that closes it.

    A research with no closing question can never report done. The human's own
    words: "point -> collect evidence -> expend area = research" — the AREA is the
    result, and an area with no boundary is not a result.
    """


FRONTIER_DDL = """
CREATE TABLE IF NOT EXISTS research_frontier (
    frontier_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    point_key         TEXT    NOT NULL,
    point_cite        TEXT    NOT NULL,
    evidence_ref      TEXT    NOT NULL,
    expanded_area     TEXT    NOT NULL,
    closing_flow_key  TEXT    NOT NULL,
    direction         TEXT    NOT NULL DEFAULT 'research',
    cite_ref          TEXT    NOT NULL,
    created_by        TEXT    NOT NULL DEFAULT 'research_direction',
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT,
    updated_at        TEXT
);
"""

FRONTIER_COLUMNS: tuple[tuple[str, str], ...] = (
    ("closing_flow_key", "TEXT NOT NULL DEFAULT ''"),
    ("direction", "TEXT NOT NULL DEFAULT 'research'"),
    ("cite_ref", "TEXT NOT NULL DEFAULT ''"),
    ("created_by", "TEXT NOT NULL DEFAULT 'research_direction'"),
    ("is_active", "INTEGER NOT NULL DEFAULT 1"),
)


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `research_frontier` and add any column an older copy is missing.

    MEASURED, and this is why the migration is not optional: `CREATE TABLE IF NOT
    EXISTS` does NOTHING on an existing table (`question_flow.ensure_schema`
    records the same defect), so a DB created before a column was added keeps the
    OLD shape and every reader of that column silently sees nothing.
    """
    from db_schema import _add_columns_if_missing
    conn.executescript(FRONTIER_DDL)
    added = _add_columns_if_missing(conn, "research_frontier", FRONTIER_COLUMNS)
    conn.commit()
    return {"ok": True, "columns_added": added}


# ---------------------------------------------------------------------------
# NARROW — the direction that SHRINKS. It REUSES `question_flow.run_flow`.
# ---------------------------------------------------------------------------

def narrow(conn: sqlite3.Connection, flow_key: str,
           ask: Callable[[str, dict[str, Any]], str],
           *, area: str = "",
           area_size: int | None = None,
           binding: dict[str, Any] | None = None,
           **run_kwargs: Any) -> dict[str, Any]:
    """Reduce an AREA to the POINT — the set that did NOT conform. PROOF.

    THE MEASURED DEFECT IN MY FIRST VERSION. I reported `point_size =
    asked_count`, i.e. the number of questions ASKED. MEASURED on
    `ui_element_registry`: area_size=17 (columns), point_size=41 (questions) →
    `shrinks=False`, and the reciprocal check correctly failed. The label was
    right and the MEASUREMENT was wrong: **asking 41 questions is not narrowing
    anything.** Narrowing is REDUCING, and the thing a proof reduces an area to is
    the set that FAILED.

        AREA  = every question the registry's own shape demands (size = the area)
        POINT = the questions that answered NO (size = what remains to fix)

    So `shrinks` is True exactly when the point is SMALLER than the area — and a
    registry that fails every question has NOT been narrowed, which is the honest
    answer.

    TWO MODES, ONE RUNNER:

    * `binding=` given (the output of `question_generator.questions_for_registry`)
      — each question is put to `ask`, its answer compared with YES (the declared
      shape), and the NO answers ARE the point. This is the evidence path: the
      registry is compared against its own declaration, and the point is the
      DIFFERENCE. The evidence reader is `logic_generator.answer_by_evidence`, so
      every answer still carries its query.
    * `binding` absent — `question_flow.run_flow` runs the stored flow and the
      point is its `failed_steps`. No second runner: `run_kwargs` pass straight
      through to `run_flow`.

    A CONFORMING REGISTRY HAS AN EMPTY POINT, and that is a SUCCESS: `ok` is True
    because nothing was left to fix. `shrinks` is still True (0 < N), so a clean
    registry reads as a completed narrowing rather than as "nothing happened".
    """
    import question_flow as qf

    flow_key = str(flow_key or "").strip()
    if not flow_key:
        raise DirectionError("flow_key is required for a narrow direction")

    if binding is None:
        result = qf.run_flow(conn, flow_key, ask, **run_kwargs)
        before = int(area_size) if area_size is not None else None
        failed = result.get("failed_steps") or []
        point_size = len(failed)
        shrinks = (point_size < before) if before is not None else None
        return {
            "ok": bool(result.get("ok")),
            "direction": "narrow",
            "meaning": DIRECTION_MEANING["narrow"],
            "mode": "flow",
            "flow_key": flow_key,
            "from_area": str(area or ""),
            "area_size": before,
            "point_size": point_size,
            "point": [{"step_no": n} for n in failed],
            "shrinks": shrinks,
            "code": result.get("code"),
            "verdict": result.get("verdict"),
            "step_count": result.get("step_count"),
            "asked_count": result.get("asked_count"),
            "judged_count": result.get("judged_count"),
            "failed_steps": failed,
            "layers": result.get("layers") or [],
            "steps": result.get("steps") or [],
        }

    # ---- THE EVIDENCE PATH: the area is the QUESTION SET, the point is the NOs ---
    all_questions = list(binding.get("questions") or [])
    if not all_questions:
        raise DirectionError(
            "binding for %r carries no questions, so there is no area to narrow. "
            "A narrow with an EMPTY area is not a narrowing — it is an unmeasured "
            "claim." % flow_key)
    # ONLY THE APPLICABLE QUESTIONS ARE THE AREA. MEASURED, and my first version
    # was WRONG: it took `len(questions)`, which INCLUDED the two CODE-source
    # questions (`literal_derivable`, `param_shadowed`) that a TABLE spec cannot
    # answer. Their evidence ref is `<no path>`, so the point came back
    # `fully_traceable=False` on EVERY registry — and the area was 41 where only
    # 39 questions could actually be asked. An inflated area makes every narrowing
    # look more successful than it was.
    questions = [q for q in all_questions if q.get("applicable", True)]
    skipped = [q for q in all_questions if not q.get("applicable", True)]
    if not questions:
        raise DirectionError(
            "binding for %r has NO applicable question, so there is no area to "
            "narrow. %d question(s) were skipped (see `skipped`); a narrow with no "
            "applicable question is not a narrowing." % (flow_key, len(skipped)))
    # NO SECOND SPEC AND NO SECOND EVIDENCE CALL. Every question in the binding
    # already carries `evidence_ref` / `traceable`, measured ONCE by
    # `question_generator.questions_for_registry`. The question and its evidence
    # therefore live in the SAME row, so there is no window in which they could be
    # out of step — and no reason for this module to own a second reader.
    area_size_measured = (int(area_size) if area_size is not None
                          else int(binding.get("area") or len(questions)))
    # THE UNIT, NAMED — because "narrowed from 17 to 3" is meaningless without it,
    # and MIXING units is a real defect: a registry's COLUMNS and its QUESTIONS are
    # two different populations (`measurement-scope`: "a count of a TABLE is not a
    # count of a LIST"). The area default is the QUESTION count, and when a caller
    # passes an `area_size` in a different unit the mismatch is REPORTED rather
    # than silently compared.
    unit_mismatch = (area_size is not None and int(area_size) != len(questions))

    point: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    for q in questions:
        qid = str(q.get("question_id"))
        raw = ask(str(q.get("question") or ""), q)
        got = str(raw or "").strip().upper()
        # THE EVIDENCE REF COMES FROM THE BINDING (see the note above the
        # hoist). `traceable` is the binding's own measurement, carried through
        # rather than re-derived — ONE implementation of "traceable".
        ref = str(q.get("evidence_ref") or "")
        conforms = bool(q.get("evidenced")) and got == "YES"
        answers.append({"question_id": qid, "column": q.get("column"),
                        "layer": q.get("layer"), "got": got,
                        "conforms": conforms,
                        "evidence_ref": ref,
                        "traceable": bool(q.get("traceable")),
                        "refusal": None if q.get("evidenced") else "NO_EVIDENCE_ANSWER"})
        if not conforms:
            point.append({"question_id": qid, "column": q.get("column"),
                          "layer": q.get("layer"), "got": got,
                          "evidence_ref": ref,
                          "traceable": bool(q.get("traceable")),
                          "refusal": None if q.get("evidenced") else "NO_EVIDENCE_ANSWER"})

    point_size = len(point)
    # EVERY ROW IS MEASURED, AND EVERY ROW IS CHECKED FOR TRACEABILITY. The point
    # is what a worker is asked to FIX, so an item whose evidence is
    # `scan_file(<no path>)` is an item nobody can open. It is REPORTED
    # (`untraceable`), and it is also reported in `code` via `fully_traceable`, so
    # a caller cannot read `NARROWED` as "the point is actionable" when some of it
    # is not.
    untr = untraceable(answers)
    return {
        "ok": point_size == 0,
        "direction": "narrow",
        "meaning": DIRECTION_MEANING["narrow"],
        "mode": "evidence",
        "flow_key": flow_key,
        "from_area": str(area or binding.get("table") or ""),
        "area_size": area_size_measured,
        "point_size": point_size,
        "point": point,
        "answers": answers,
        "shrinks": point_size < area_size_measured,
        "code": "NARROWED" if point_size < area_size_measured else "NOT_NARROWED",
        "unit": "question",
        "unit_mismatch": unit_mismatch,
        "fully_traceable": not untr,
        "traceable_count": len(answers) - len(untr),
        "untraceable": untr,
        "skipped": [{"question_id": q.get("question_id"),
                     "skip_reason": q.get("skip_reason")} for q in skipped],
        "verdict": "YES" if point_size == 0 else "NO",
        "step_count": len(questions),
        "asked_count": len(questions),
        "judged_count": len(questions),
        "failed_steps": [p["question_id"] for p in point],
        "layers": sorted({str(q.get("layer")) for q in questions if q.get("layer")}),
        "cite": str(binding.get("cite") or ""),
    }


# ---------------------------------------------------------------------------
# RESEARCH — the direction that GROWS. It RECORDS a frontier.
# ---------------------------------------------------------------------------

def research(conn: sqlite3.Connection, point: dict[str, Any],
             ask: Callable[[str, dict[str, Any]], str],
             *, closing_flow_key: str = "",
             area_hint: str = "",
             evidence: list[dict[str, Any]] | None = None,
             cite_ref: str = "",
             created_by: str = "research_direction",
             commit: bool = True) -> dict[str, Any]:
    """Collect evidence at a POINT and EXPAND it into an AREA. Records a frontier.

    `point` is `{key, cite}` — WHAT is being researched and where that came from.
    A point with no `cite` is refused: it is a memory, not a measurement, and the
    citation rule refuses it at the source rather than at report time.

    `ask` is INJECTED (`ask(prompt, item) -> str`), exactly as in `run_flow`, so
    this module talks to no model and any LLM serves. QC-11 scans for a model
    literal and must find none.

    `evidence` is the list of evidence items collected at the point, each with a
    `ref` (a query, a `path:line`, or a command). An evidence item with no `ref`
    is REFUSED — the same rule `logic_generator.answer_by_evidence` applies to an
    answer with no query, because an evidence with no reference is an assertion.

    `closing_flow_key` is the `narrow` flow that would CLOSE this research. IT IS
    REQUIRED. When it is empty, `closing_question_for` is used to DERIVE one from
    the point's own registry; if that also finds nothing, the call RAISES
    `ExplorationWithNoEnd` rather than storing a frontier that can never close.

    Returns `{ok, direction, point, evidence_count, expanded_area,
              closing_flow_key, frontier_id, grows}`.
    """
    import question_flow as qf

    key = str((point or {}).get("key") or "").strip()
    pcite = str((point or {}).get("cite") or "").strip()
    if not key:
        raise DirectionError("point.key is required — a research needs a point")
    if not pcite:
        raise DirectionError(
            "point.cite is required for point %r — a point with no cite is a "
            "memory, not a measurement" % key)

    items = list(evidence or [])
    for i, ev in enumerate(items):
        ref = str((ev or {}).get("ref") or "").strip()
        if not ref:
            raise DirectionError(
                "evidence item %d at point %r has no `ref`; an evidence with no "
                "reference (a query, a `path:line`, or a command) is an assertion"
                % (i, key))
        # NON-EMPTY IS NOT ENOUGH. MEASURED: `hardcode_scan.scan_file(<no path>)`
        # is non-empty and NOT traceable. A research frontier is the record of
        # what was found at a point, so a placeholder ref makes the whole
        # frontier unopenable — the "all traceable" half of "point = a measured
        # unit, all traceable" would be false while the field looked filled.
        if not is_traceable(ref):
            raise DirectionError(
                "evidence item %d at point %r has a `<placeholder>` ref %r, which "
                "is not traceable. A frontier whose evidence cannot be opened "
                "cannot be re-measured." % (i, key, ref))

    # ---- THE RECIPROCAL RULE, ENFORCED BEFORE ANY WRITE --------------------
    closer = str(closing_flow_key or "").strip()
    derived = False
    if not closer:
        closer = closing_question_for(conn, key)
        derived = bool(closer)
    if not closer:
        raise ExplorationWithNoEnd(
            "research at point %r names no closing question, and none could be "
            "derived from the point's registry. A research with no closing "
            "question is an exploration with no end: nothing states what 'done' "
            "is. Pass `closing_flow_key`, or make the point's registry speccable "
            "so `question_generator.questions_for_registry` can name one."
            % key)
    # A closing question must BE a narrow flow, or the two directions are not
    # reciprocals — a research closed by another research never converges.
    if not qf.flow_of(conn, closer):
        raise ExplorationWithNoEnd(
            "research at point %r names closing flow %r, but no such flow exists. "
            "The closing question must be a NARROW flow (`question_flow."
            "add_flow`), because a research closed by another research has no "
            "end." % (key, closer))

    expanded = str(area_hint or "").strip() or _derive_area(conn, items)
    if not expanded:
        raise DirectionError(
            "point %r produces no expanded area, and `area_hint` was not given. "
            "The AREA is the result of a research; a research that expands into "
            "nothing has not finished." % key)

    ensure_schema(conn)
    cite = str(cite_ref or "").strip() or ("%s/%s" % (key, pcite))
    cur = conn.execute(
        "INSERT INTO research_frontier (point_key, point_cite, evidence_ref, "
        "expanded_area, closing_flow_key, direction, cite_ref, created_by, "
        "is_active) VALUES (?,?,?,?,?,?,?,?,1)",
        (key, pcite, _join_refs(items), expanded, closer,
         _check_direction("research"), cite, str(created_by)))
    if commit:
        conn.commit()

    return {
        "ok": True,
        "direction": "research",
        "meaning": DIRECTION_MEANING["research"],
        "frontier_id": int(cur.lastrowid) if cur.lastrowid else None,
        "point_key": key,
        "point_cite": pcite,
        "evidence_count": len(items),
        "expanded_area": expanded,
        "closing_flow_key": closer,
        "closing_derived": derived,
        # GROWS: one point expanded into an area. `candidate_count_before` is 1 by
        # definition (a point is one thing); AFTER is the evidence count, and the
        # area it opened. `grows` is False when the research found NO evidence —
        # which is a real outcome and must not read as a success.
        "candidate_count_before": 1,
        "candidate_count_after": int(len(items)),
        "candidate_count_unit": "evidence item",
        "grows": len(items) > 1,
    }


def closing_question_for(conn: sqlite3.Connection, point_key: str) -> str:
    """The `narrow` flow key that would CLOSE a research at `point_key`, or ''.

    THE DERIVATION IS FROM THE POINT'S OWN REGISTRY, and it is DETERMINISTIC: the
    flow key is `narrow:<registry>` for the registry the point names. A registry
    that the point does not name yields '' — and '' is returned rather than a
    guess, because a guessed closing question would make every research look
    closeable while closing none of them.
    """
    key = str(point_key or "").strip()
    # A point is named `table:<name>` / `regist:<name>` / a bare registry name.
    for prefix in ("table:", "regist:", "registry:", "register:"):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    if not key:
        return ""
    row = conn.execute(
        "SELECT table_key FROM db_table_registry WHERE table_key=?",
        (key,)).fetchone()
    if not row:
        return ""
    return "narrow:%s" % str(row["table_key"])


def _join_refs(items: list[dict[str, Any]]) -> str:
    """The evidence refs as one text field, so a reader can re-run each of them."""
    return "; ".join(str((e or {}).get("ref") or "").strip() for e in items)


# ---------------------------------------------------------------------------
# TRACEABILITY — the second half of "point = a measured unit, all traceable".
#
# MEASURED (2026-09-27), and this is why the check exists: on
# `ui_element_registry` two point items came back with the evidence ref
# `hardcode_scan.scan_file(<no path>)`. The ref is NON-EMPTY, so a plain
# "has a ref" check PASSES it — and it is not traceable at all. `<no path>` is a
# PLACEHOLDER that says the reader had no file to point at.
#
# WHY THAT MATTERS MORE THAN IT LOOKS: a point item is the thing a worker is asked
# to FIX. An item whose evidence is `<no path>` cannot be opened, so the worker
# either guesses or skips it — and a skipped item in a "point" silently shrinks the
# work while the report still shows the item. That is the same defect family as an
# empty result taken for "nothing found" (see `independent-review`).
#
# THE RULE: a ref is traceable when it is non-empty AND contains no ANGLE-BRACKET
# placeholder. This is deliberately narrow — it does NOT try to validate that a
# path exists or that a SQL statement runs, because those are the readers' own
# jobs. It rejects exactly the shape the measurement found.
# ---------------------------------------------------------------------------
import re as _re_placeholder

_PLACEHOLDER_RE = _re_placeholder.compile(r"<[^>]*>")


def is_traceable(ref: Any) -> bool:
    """True when `ref` is a usable citation: non-empty, no `<placeholder>`.

    Returns False — never True — when the ref is blank or carries an angle-bracket
    placeholder. An UNKNOWN cannot pass as traceable: a caller that treats "could
    not tell" as "traceable" produces a point full of items nobody can open.
    """
    s = str(ref or "").strip()
    if not s:
        return False
    return _PLACEHOLDER_RE.search(s) is None


def untraceable(rows: list[dict[str, Any]], *, field: str = "evidence_ref"
                ) -> list[dict[str, Any]]:
    """The rows whose `field` is NOT a usable citation. REPORTED, never dropped."""
    out: list[dict[str, Any]] = []
    for r in rows:
        if not is_traceable((r or {}).get(field)):
            out.append({"question_id": (r or {}).get("question_id"),
                        "column": (r or {}).get("column"),
                        field: str((r or {}).get(field) or ""),
                        "why": "empty or a `<placeholder>` ref is not traceable"})
    return out


def _derive_area(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> str:
    """The area the evidence opens, when the caller gives no `area_hint`.

    DERIVED FROM THE ITEMS, not invented: the areas an evidence item can open are
    the distinct `area` values the caller put on them. Empty means the caller's
    evidence declared no area at all, and `research` refuses that rather than
    writing a placeholder.
    """
    seen: list[str] = []
    for ev in items:
        a = str((ev or {}).get("area") or "").strip()
        if a and a not in seen:
            seen.append(a)
    return ", ".join(seen)


def frontiers_of(conn: sqlite3.Connection, point_key: str | None = None) -> list[dict[str, Any]]:
    """The recorded frontiers, newest first, optionally for one point.

    Historical rows are RETURNED, including inactive ones: a retired frontier is
    HISTORY, not a defect, and hiding it would make a research look like it never
    happened.
    """
    ensure_schema(conn)
    if point_key:
        rows = conn.execute(
            "SELECT * FROM research_frontier WHERE point_key=? "
            "ORDER BY frontier_id DESC", (str(point_key),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM research_frontier ORDER BY frontier_id DESC").fetchall()
    return [dict(r) for r in rows]


def directions_are_reciprocal(conn: sqlite3.Connection,
                              narrow_result: dict[str, Any],
                              research_result: dict[str, Any]) -> dict[str, Any]:
    """MEASURE that one direction shrinks and the other grows.

    WHY A MEASUREMENT AND NOT A CHECK ON THE LABELS. "The two are reciprocal" is a
    claim about COUNTS. Reading the `direction` strings proves nothing: a module
    could label both the same way and the labels would agree. So this compares
    the two counts and reports each side, with the same rule the repo's
    measurement-scope skill states — a count must name the population it counted.

    `ok` is True only when `narrow.shrinks` is True AND `research.grows` is True.
    An UNKNOWN side (`shrinks is None`) is not True, so an unmeasured claim cannot
    pass as a measured one.
    """
    shrinks = narrow_result.get("shrinks")
    grows = research_result.get("grows")
    return {
        "ok": bool(shrinks is True and grows is True),
        "code": ("RECIPROCAL"
                 if (shrinks is True and grows is True) else "NOT_RECIPROCAL"),
        "narrow": {
            "unit": narrow_result.get("unit"),
            "area_size": narrow_result.get("area_size"),
            "point_size": narrow_result.get("point_size"),
            "shrinks": shrinks,
        },
        "research": {
            "unit": research_result.get("candidate_count_unit"),
            "candidate_count_before": research_result.get("candidate_count_before"),
            "candidate_count_after": research_result.get("candidate_count_after"),
            "grows": grows,
        },
        "note": ("a narrow must SHRINK and a research must GROW; if both shrink, "
                 "one of them is mislabelled. The two units are DIFFERENT and are "
                 "reported per side — a narrow counts QUESTIONS, a research counts "
                 "EVIDENCE ITEMS, and comparing them directly would be the "
                 "wrong-population defect."),
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: `research_direction.py [--frontiers POINT] [--meaning]`."""
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frontiers", nargs="?", const="", default=None,
                    help="list frontiers, optionally for one point")
    ap.add_argument("--meaning", action="store_true",
                    help="print the two directions and their meaning")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    if args.meaning:
        print(json.dumps({"directions": DIRECTIONS,
                          "meaning": DIRECTION_MEANING}, indent=2))
        return 0

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        out = frontiers_of(conn, args.frontiers or None)
    finally:
        conn.close()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
