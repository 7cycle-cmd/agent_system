# -*- coding: utf-8 -*-
"""data_analyze.py — `data -> analyze -> question`. THE ONE ENTRY.

The user (2026-09-22):
    "question_* is the part for data -> question / that is the missing piece
     question = data analyze / we got 5W1H for question system / question is get
     by data analyze module or capability? / need to rename or is under? data
     analyze > question_* / so it can apply to all generator in easy"

THE MEASURED ANSWER TO "module or capability?"
----------------------------------------------
**MODULE.** Measured, not assumed:

  * `logic_generator.generate(spec)` reads a SPEC, and `spec_from_table` gets it
    with `conn.execute("PRAGMA table_info(...)")` — it reads the DB DIRECTLY.
  * `capability_registry` has **0** rows matching logic / question / generator /
    analyze / distill / factor (38 capabilities, none of them this).
  * `CAPABILITY_KINDS = (thinking, eye, hand, voice, code)` — there is no
    `data_analyze` kind.
  * `data_analyze` did not exist at all (grep: 0 matches).

So `question` is produced by a MODULE, and the chain is:

    data      a table row / a skill row
    analyze   logic_generator.spec_from_*  +  factor_first_principle.derive
    question  logic_generator.generate      +  question_flow.run_flow

WHY A FACADE AND NOT A RENAME
-----------------------------
MEASURED: **12 files** import `question_flow` (`_pilot_flow_run.py`,
`_proof_question_flow*.py`, `_seed_worker_identity_flow.py`, ...). A rename would
break all 12, and the breakage would be a NAME change rather than a behaviour
change — the worst kind of churn.

So this module RE-EXPORTS. It moves no logic. `_proof_data_analyze.py` asserts
every re-exported name is the SAME OBJECT as the original (`is`, not `==`), so
the facade cannot silently become a second implementation.

THE 5W1H DIMENSIONS ARE NOT RESTATED
------------------------------------
`DIMENSIONS` is `skill_5w1h.DIMENSION_NAMES`, imported. A second copy of the six
dimensions is the drift this repo keeps hitting (`LAYERS` was first written as a
literal with a CLAIM of reuse, and the proof measured the claim FALSE).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parent / "agent.db"

# THE THREE STAGES. A chain, in order, and the order is the point: a question
# cannot be asked before the data is read, and the data cannot be read before
# something names it.
STAGES: tuple[str, ...] = ("data", "analyze", "question")

# THE 5W1H DIMENSIONS, IMPORTED — never restated.
import skill_5w1h as _fw  # noqa: E402

DIMENSIONS: tuple[str, ...] = tuple(_fw.DIMENSION_NAMES)

# ---------------------------------------------------------------------------
# THE RE-EXPORTS. Each one is the ORIGINAL object, not a wrapper.
# ---------------------------------------------------------------------------

# --- data: read the thing -------------------------------------------------
from logic_generator import (  # noqa: E402
    spec_from_skill,
    spec_from_table,
)

# --- analyze: derive the rules --------------------------------------------
from logic_generator import (  # noqa: E402
    validate_spec,
)
from factor_first_principle import (  # noqa: E402
    derive,
    dimension_of,
    questions_by_dimension,
    uncovered_dimensions,
)
from factor_distill import (  # noqa: E402
    distill,
    distill_cycle,
)

# --- question: emit the list ----------------------------------------------
from logic_generator import (  # noqa: E402
    generate,
    explain_count,
    to_tdd_cases,
)

# --- flow: narrow the question to 1 -> 2 -> 3 -----------------------------
from question_flow import (  # noqa: E402
    LAYERS,
    STEP_KINDS,
    COMPUTED_KINDS,
    run_flow,
    validate_flow,
    steps_of,
    name_matches,
    make_name_verdict,
    record_lesson,
)

# The module each stage is IMPLEMENTED BY. A caller that wants to know "who does
# the analyze step?" reads this rather than guessing from a name.
STAGE_MODULES: dict[str, tuple[str, ...]] = {
    "data": ("logic_generator",),
    "analyze": ("logic_generator", "factor_first_principle", "factor_distill"),
    "question": ("logic_generator", "question_flow"),
}


def chain_of(subject_kind: str) -> dict[str, Any]:
    """The `data -> analyze -> question` chain for a SUBJECT KIND.

    Returns the STAGES with the MODULE that implements each, so "who does which
    step" is READ from `STAGE_MODULES` rather than asserted in prose.

    `subject_kind` is the kind of thing being analysed (`table`, `skill`, ...).
    It is REPORTED, not validated against a closed list — the list of subject
    kinds lives in `dimension_binding_registry.SUBJECT_KINDS`, and a second copy
    here would drift.
    """
    return {
        "subject_kind": str(subject_kind),
        "stages": [
            {"stage": s, "modules": list(STAGE_MODULES[s])} for s in STAGES
        ],
        "dimensions": list(DIMENSIONS),
        "entry": {
            "data": "spec_from_table / spec_from_skill",
            "analyze": "derive / distill",
            "question": "generate / run_flow",
        },
    }


def spec_for(conn: sqlite3.Connection, subject_kind: str, subject: str
             ) -> dict[str, Any]:
    """The SPEC for a subject, dispatched by its KIND.

    This is the ONE entry a generator calls: it does not need to know whether the
    subject is a table or a skill. An unknown kind is REFUSED with the known
    kinds named, so a typo cannot silently produce an empty spec.
    """
    kind = str(subject_kind or "").strip().lower()
    if kind == "table":
        return spec_from_table(conn, subject)
    if kind == "skill":
        return spec_from_skill(conn, subject)
    raise ValueError(
        "subject_kind %r is not one this entry can read (known: table, skill). "
        "Add a branch rather than passing an unknown kind — an unknown kind "
        "would produce a spec about nothing." % subject_kind)


def questions_for(conn: sqlite3.Connection, subject_kind: str, subject: str
                  ) -> dict[str, Any]:
    """The QUESTION LIST for a subject, with its derivation.

    `data -> analyze -> question` in ONE call: read the spec, then generate. The
    count is DERIVED (`2N + 4`), and `explain_count` states the derivation, so a
    caller never has to trust a number.
    """
    spec = spec_for(conn, subject_kind, subject)
    result = generate(spec)
    return {
        "subject_kind": str(subject_kind),
        "subject": str(subject),
        "spec": spec,
        "questions": result["questions"],
        "count": result["count"],
        "case_count": result["case_count"],
        "derivation": result["derivation"],
        "formula": result["formula"],
        "dimensions_used": result["dimensions_used"],
        "explanation": explain_count(result),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    sub = ap.add_subparsers(dest="cmd")
    ch = sub.add_parser("chain", help="show the data -> analyze -> question chain")
    ch.add_argument("subject_kind", nargs="?", default="table")
    q = sub.add_parser("questions", help="the question list for a subject")
    q.add_argument("subject_kind")
    q.add_argument("subject")
    args = ap.parse_args(argv)
    if args.cmd == "chain":
        print(json.dumps(chain_of(args.subject_kind), ensure_ascii=False,
                         indent=2))
        return 0
    if args.cmd == "questions":
        conn = sqlite3.connect(str(args.db or DEFAULT_DB))
        conn.row_factory = sqlite3.Row
        try:
            out = questions_for(conn, args.subject_kind, args.subject)
            print(json.dumps({k: v for k, v in out.items() if k != "spec"},
                             ensure_ascii=False, indent=2))
            return 0
        finally:
            conn.close()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())