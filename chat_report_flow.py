"""chat_report_flow.py -- the STEP definitions for the fault-report pipeline, each
step carrying 5W1H BOUND THROUGH THE EXISTING MIDDLEWARE.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "5W1H for each step -> step with middleware"

MEASURED, and the machinery already exists — this module DECLARES steps, it does
not build a step engine:

  * THE MIDDLEWARE is `identity_middleware.py`, whose own docstring states the
    user's model: "A X B IS: A = the identity 5W1H (who/what/why/when/where/how);
    B = the SUBJECT KIND (chat, task, workflow, ticket, entity, ...); and the
    OUTPUT is the format for that kind."
  * `identity_middleware.open_checked(conn, source, *, cite_ref, ...)` is
    **collect -> check -> write**, and its docstring says "NO SECOND WRITER. A
    private INSERT would be a second place that knows the natural key and the
    guards, and the two would drift." This module CALLS it.
  * its GATE `check()` refuses `NO_SESSION` — "A SESSION IS HALF THE 2-FACTOR" —
    and `WORKER_NOT_REGISTERED`.
  * A STEP WITH MIDDLEWARE is the EXISTING `workflow_step` shape, written by
    `question_flow.add_step(flow_key, step_no, layer_key=, step_kind=,
    question_template=, expected=, parser=)`.
  * the vocabularies are CLOSED and measured:
        LAYERS         = ('tdd', 'ontology')
        STEP_KINDS     = ('gate', 'describe', 'count', 'name', 'verdict')
        DIMENSION_NAMES= ('what', 'why', 'who', 'when', 'where', 'how')
  * a live precedent: `workflow_step id 3` — `layer_key='ontology'`,
    `step_kind='gate'`, `question_template='Is the text below a mapping of field
    names...'`, `expected='YES'`.

THE STEPS, AND THE 5W1H EACH CARRIES
------------------------------------
The six dimensions are NOT stored per step (a step stores its question + layer);
they are BOUND to the step's SUBJECT KIND in `dimension_binding_registry` — which
is exactly the middleware's A X B. So a step is "with middleware" when:
  1. it is a real `workflow_step` row in a real flow;
  2. its `layer_key` / `step_kind` are inside the VOCABULARIES; and
  3. the step's 5W1H is COMPLETE for its subject kind
     (`identity_middleware.kind_coverage`), and the identity opening that step
     goes through `open_checked` rather than a private INSERT.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

FLOW_KEY = "chat_report_flow"
FLOW_NAME = "Chat Fault Report"
FLOW_DESCRIPTION = (
    "The pipeline that turns a runtime fault into a first-class chat message: "
    "gate (is there a trace to report?), describe (the finding + its unit), "
    "verdict (write the report through the ONE writer)."
)

# The subject kind whose 5W1H is bound for this flow. MEASURED: the report is
# about a runtime COMPONENT, and the middleware's coverage is per subject kind.
SUBJECT_KIND = "worker_identity"

# THE STEPS. `layer_key`/`step_kind` are inside the measured vocabularies; the
# template is the question the step answers, and `expected` is the ANSWER that
# makes the step pass — a gate that cannot fail is decorative.
#
# `dimension` names WHICH 5W1H this step is the carrier of, so the "5W1H for each
# step" is machine-readable rather than a claim in prose.
STEPS: tuple[dict[str, Any], ...] = (
    {
        "step_no": 1,
        "dimension": "why",
        "layer_key": "ontology",
        "step_kind": "gate",
        "question_template": ("Is there a fault_factor_trace row for this "
                              "subject, so a report has a MEASUREMENT to report?"),
        "expected": "YES",
        "parser": "result_yes_no",
        "is_final": False,
        "notes": "a report about nothing is a fabricated finding",
    },
    {
        "step_no": 2,
        "dimension": "what",
        "layer_key": "tdd",
        "step_kind": "describe",
        "question_template": ("What exactly is wrong — the subject, its state, "
                              "and the seconds since its newest evidence?"),
        "expected": "YES",
        "parser": "result_yes_no",
        "is_final": False,
        "notes": "the title carries the finding; the content carries the reading",
    },
    {
        "step_no": 3,
        "dimension": "how",
        "layer_key": "tdd",
        "step_kind": "count",
        "question_template": ("How many failures against how many observations, "
                              "and what is the factor's unit and target?"),
        "expected": "YES",
        "parser": "result_yes_no",
        "is_final": False,
        "notes": "failure_count / total_count are MEASURED, never typed",
    },
    {
        "step_no": 4,
        "dimension": "where",
        "layer_key": "tdd",
        "step_kind": "name",
        "question_template": ("Where is the evidence — which checkable "
                              "path:line or command does the report cite?"),
        "expected": "YES",
        "parser": "result_yes_no",
        "is_final": False,
        "notes": "citation-discipline: an uncited finding is DISCARDED",
    },
    {
        "step_no": 5,
        "dimension": "who",
        "layer_key": "ontology",
        "step_kind": "verdict",
        "question_template": ("Who is reporting — which worker and identity is "
                              "the report attributed to?"),
        "expected": "YES",
        "parser": "result_yes_no",
        "is_final": False,
        "notes": "opened THROUGH identity_middleware.open_checked",
    },
    {
        "step_no": 6,
        "dimension": "when",
        "layer_key": "tdd",
        "step_kind": "verdict",
        "question_template": ("When was the fault seen, and is it still open — "
                              "does the report state the time and the state?"),
        "expected": "YES",
        "parser": "result_yes_no",
        "is_final": True,
        "notes": "the message status is 'ask' until a human acts",
    },
)


class FlowErrorRaised(Exception):
    """Refused -- nothing written."""


def declare_flow(conn: sqlite3.Connection, *, commit: bool = True
                 ) -> dict[str, Any]:
    """Create the flow and its steps. IDEMPOTENT: a second run changes nothing.

    Idempotence is the point (QC-16): a declaration that appends on every run
    would make the step count grow, so "the pipeline has six steps" could never
    be asserted.
    """
    import question_flow as qf
    qf.ensure_schema(conn)
    created_flow = False
    if not qf.flow_of(conn, FLOW_KEY):
        qf.add_flow(conn, FLOW_KEY, FLOW_NAME, description=FLOW_DESCRIPTION,
                    commit=False)
        created_flow = True
    existing = {int(s["step_no"]) for s in qf.steps_of(conn, FLOW_KEY)}
    added: list[int] = []
    for s in STEPS:
        if int(s["step_no"]) in existing:
            continue
        qf.add_step(conn, FLOW_KEY, int(s["step_no"]),
                    layer_key=s["layer_key"], step_kind=s["step_kind"],
                    question_template=s["question_template"],
                    expected=s["expected"], parser=s["parser"],
                    is_final=bool(s["is_final"]), notes=s["notes"],
                    commit=False)
        added.append(int(s["step_no"]))
    if commit:
        conn.commit()
    return {"ok": True, "flow_key": FLOW_KEY, "flow_created": created_flow,
            "steps_added": added,
            "steps_total": len(qf.steps_of(conn, FLOW_KEY))}


def step_5w1h(conn: sqlite3.Connection, *, kind: str = SUBJECT_KIND
              ) -> dict[str, Any]:
    """The 5W1H coverage for the flow's subject kind, via the MIDDLEWARE."""
    import identity_middleware as im
    cov = im.kind_coverage(conn, kind)
    return {"kind": kind, "have": cov["have"], "missing": cov["missing"],
            "complete": cov["complete"],
            "source": "identity_middleware.kind_coverage"}


def open_step_identity(conn: sqlite3.Connection, *, source: dict[str, Any],
                       cite_ref: str) -> dict[str, Any]:
    """Open a step's identity THROUGH THE MIDDLEWARE (collect -> check -> write).

    IT CALLS `open_checked` RATHER THAN `open_identity`. A direct call would skip
    the GATES (`check`: NO_SESSION, WORKER_NOT_REGISTERED), which is exactly the
    "second writer that knows the guards" the middleware docstring forbids.
    """
    import identity_middleware as im
    return im.open_checked(conn, source, cite_ref=cite_ref)


def pipeline_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """The flow, its steps, and the 5W1H each step is the carrier of.

    READ-ONLY. Reports the DECLARED steps against the MEASURED coverage, so a step
    whose dimension is unbound is visible rather than assumed.
    """
    import question_flow as qf
    steps = [dict(s) for s in qf.steps_of(conn, FLOW_KEY)] if qf.flow_of(
        conn, FLOW_KEY) else []
    declared_dims = [s["dimension"] for s in STEPS]
    return {"ok": True, "flow_key": FLOW_KEY,
            "flow_exists": qf.flow_of(conn, FLOW_KEY) is not None,
            "steps": [{"step_no": s.get("step_no"), "layer_key": s.get("layer_key"),
                       "step_kind": s.get("step_kind"),
                       "question_template": (s.get("question_template") or "")[:70]}
                      for s in steps],
            "declared_dimensions": declared_dims,
            "coverage": step_5w1h(conn)}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="chat report step flow")
    ap.add_argument("--db", default=str(BASE_DIR / "agent.db"))
    ap.add_argument("--declare", action="store_true")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.declare:
            print(declare_flow(conn))
        if args.report:
            print(_json.dumps(pipeline_report(conn), indent=2,
                              ensure_ascii=False, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
