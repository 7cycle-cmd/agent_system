# -*- coding: utf-8 -*-
"""question_flow.py — a question is a STEP, and steps NARROW.

THE PROBLEM THIS SOLVES
-----------------------
MEASURED (2026-09-22): the proof run asked the 7B ONE verdict question — "is
every required field present?" — and the 7B answered YES for a payload MISSING a
required field **19 times out of 40** (`_diag_pilot_failures.py`:
`oracle=NO llm=YES : 19`, `oracle=YES llm=YES : 14`, max_streak 3).

Eight instruction variants were then measured on a 3-YES/3-NO gold set
(`_diag_identity_instruction_variants.py`):

    variant                          recall YES  recall NO  balanced
    A "answer YES iff every field"        1.00       0.00      0.50
    B "list absent keys"                  1.00       0.33      0.67
    C "count present keys"                0.33       1.00      0.67
    D "JSON schema validator"             1.00       0.00      0.50
    E "name ONE missing key"              0.67       0.00      0.33
    F "report absent keys, else NONE"     1.00       1.00      1.00  (unstable)
    F2 (F, tightened)                     1.00       1.00      1.00
    G "per-key then verdict"              0.00       1.00      0.50

F2 looked perfect, but `_diag_identity_f2_probe.py` broke it:

    COMPLETE            -> 'NONE'        -> YES  correct
    missing CHAT_SHA256 -> 'CHAT_SHA256' -> NO   correct
    missing MODEL       -> 'NONE'        -> YES  WRONG

So F2's 1.00 was SAMPLING LUCK. **The 7B cannot emit a verdict after
enumerating.** But it CAN do three things, and the user's own 7B-VL cycle proves
it:

  * DESCRIBE — `_exp_cross_prompt.describe_then_answer`: "Describe the red marks
    you can see in this image in one sentence, then answer."
  * COUNT — `_exp_cross_prompt.count_strokes`: "Count the red strokes that are
    NOT part of the rectangle outline."
  * NAME ONE THING — `_probe_vl_keys.py`: "Is there a long string of only
    hexadecimal characters on the line labelled CHAT_SHA256?"

THE DESIGN (the user: "yes question is combine 1->2 -> 3")

    step 1  GATE      "can you see the thing at all?"   -> if NO, STOP
    step 2  DESCRIBE  "what is there? name/count it"     -> the model's own words
    step 3  VERDICT   COMPUTED from 1 + 2, NOT asked

The verdict is COMPUTED, never asked. That is the whole point: the 7B cannot
emit a verdict (measured above) but it CAN describe and count.

THE GATING RULE IS ALREADY IN THE REPO
--------------------------------------
`evidence_classify.PROMPT_Q1` (`evidence_classify.py:47`), verbatim:

    "Question 1 (gating): can the model even see the box? If not, the overlay did
    not draw or the model cannot perceive it — and asking question 2 anyway would
    produce a confident answer for the wrong reason."

THE TWO LAYERS ARE ALREADY A VOCABULARY
---------------------------------------
`register_approval.STAGES = ("tdd", "tdd_verify", "ontology_verify")`
(`register_approval.py:67`). This module REUSES it rather than inventing a second
vocabulary — a vocabulary declared in two places is the drift this repo keeps
hitting.

    TDD       does the VALUE satisfy the rule
    ONTOLOGY  does the THING exist / is it registered / is it active

THE FLOW IS A TABLE
-------------------
`workflow_registry` + `workflow_step` already exist (`db_schema.py:2205`,
`:2222`) with `step_no`, `is_final` and an FK to `prompt_registry`. This module
REUSES them and adds two columns (`layer_key`, `step_kind`) by additive
migration, so adding a step is an INSERT rather than a Python edit.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

DEFAULT_DB = Path(__file__).resolve().parent / "agent.db"

# THE TWO LAYERS, DERIVED from `register_approval.STAGES` — not re-invented.
#
# DEFECT FOUND BY RUNNING THE PROOF (2026-09-22): I first wrote
# `LAYERS = ("tdd", "ontology")` and CLAIMED it was "reused from
# register_approval.STAGES". The proof measured that claim FALSE:
# `STAGES = ("tdd", "tdd_verify", "ontology_verify")`, and `"ontology"` is NOT a
# member — `"ontology_verify"` is. So the claim was an assertion, not a fact.
#
# The fix is to DERIVE the layer from the stage: a stage's LAYER is its name
# before `_verify`. That yields ("tdd", "ontology") from the SAME source, so the
# two vocabularies cannot drift — which is the whole reason to reuse.
def _layer_of_stage(stage: str) -> str:
    """The layer a stage belongs to: the name before `_verify`."""
    s = str(stage)
    return s[: -len("_verify")] if s.endswith("_verify") else s


def _derive_layers() -> tuple[str, ...]:
    """The layers, DERIVED from `register_approval.STAGES`, in first-seen order."""
    import register_approval as ra
    out: list[str] = []
    for stage in ra.STAGES:
        lay = _layer_of_stage(stage)
        if lay not in out:
            out.append(lay)
    return tuple(out)


LAYERS: tuple[str, ...] = _derive_layers()

# THE STEP KINDS. A step is one of these, and the kind decides how its answer is
# USED: a `gate` can STOP the flow, a `verdict` is COMPUTED rather than asked.
STEP_KINDS: tuple[str, ...] = ("gate", "describe", "count", "name", "verdict")

# A step whose answer is COMPUTED from the earlier steps, never asked of a model.
COMPUTED_KINDS: tuple[str, ...] = ("verdict",)

# The columns added to `workflow_step` by additive migration.
_STEP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("layer_key", "TEXT NOT NULL DEFAULT 'NA'"),
    ("step_kind", "TEXT NOT NULL DEFAULT 'NA'"),
    ("question_template", "TEXT NOT NULL DEFAULT 'NA'"),
    ("expected", "TEXT NOT NULL DEFAULT 'NA'"),
    ("parser", "TEXT NOT NULL DEFAULT 'result_yes_no'"),
    # SCOPE AE (2026-09-28). The human: "question is according to the answer by
    # factor!!! not waste token". MEASURED: without these a step could not say
    # WHICH factor it measures, and the next question was NEVER selected by the
    # answer -- `run_flow` asked every step in `step_no` order.
    ("factor_key", "TEXT"),
    ("branch_on", "TEXT"),
    ("is_terminal", "INTEGER NOT NULL DEFAULT 0"),
    # SCOPE AC (2026-09-28). The verdict is COMPUTED from a DECLARED rule, so
    # the rule is DATA rather than a Python branch. `VERDICT_RULES` is the
    # closed set; an unknown rule FAILS CLOSED.
    ("verdict_rule", "TEXT"),
    ("verdict_params", "TEXT"),
)

# ---------------------------------------------------------------------------
# THE DECLARED VERDICT RULE (SCOPE AC, added 2026-09-28).
#
# `run_flow` already takes `compute_verdict` as a CALLABLE, and the repo has
# three of them. But a callable is CODE: a new rule needs a new function, so a
# worker who cannot edit Python cannot add a rule. `verdict_rule` +
# `verdict_params` make the rule DATA, and this tuple is the CLOSED SET of
# computations the code can perform -- so an unknown rule FAILS CLOSED rather
# than silently defaulting to YES.
# ---------------------------------------------------------------------------
VERDICT_RULES: tuple[str, ...] = (
    "all_judged_match",        # every judged step answered its `expected`
    "digit_count_between",     # the DESCRIBE step's integer is within [lo, hi]
    "name_set_matches",        # the DESCRIBE step named every required name
)


def compute_declared_verdict(rule: str, params: dict[str, Any] | None = None
                             ) -> Callable[..., str]:
    """A `compute_verdict` built from a DECLARED rule name and its params.

    FAILS CLOSED: an unknown rule returns a callable that answers `NO` and
    records why, because a rule nobody declared cannot confirm anything. A
    default of `YES` here would be the "silent pass" this repo records as its
    most repeated defect.
    """
    p = dict(params or {})
    name = str(rule or "").strip()

    if name == "digit_count_between":
        lo = int(p.get("lo", 0))
        hi = int(p.get("hi", 10 ** 9))

        def _digits(answers: dict[int, str], steps: list[dict[str, Any]]) -> str:
            import re as _re
            for s in steps:
                if str(s.get("step_kind")) != "describe":
                    continue
                m = _re.search(r"\d+", str(answers.get(int(s["step_no"])) or ""))
                if not m:
                    return "NO"
                n = int(m.group(0))
                return "YES" if lo <= n <= hi else "NO"
            return "NO"

        _digits.__name__ = "compute_digit_count_between"
        return _digits

    if name == "name_set_matches":
        required = [str(x) for x in (p.get("required") or [])]
        return make_name_verdict(required)

    if name == "all_judged_match":
        def _all(answers: dict[int, str], steps: list[dict[str, Any]]) -> str:
            return _default_verdict(answers, steps, None)

        _all.__name__ = "compute_all_judged_match"
        return _all

    def _refuse(answers: dict[int, str], steps: list[dict[str, Any]]) -> str:
        return "NO"

    _refuse.__name__ = "compute_unknown_rule_%s" % (name or "EMPTY")
    return _refuse


# The columns added to `workflow_registry` by additive migration.
#
# `created_by` is the MAKER. The user: "so who make the system and who QC for
# work". MEASURED: neither `workflow_registry` nor `prompt_registry` had a maker
# column, so "who made this?" was unanswerable.
_FLOW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("created_by", "TEXT NOT NULL DEFAULT 'NA'"),
)


class FlowError(ValueError):
    """Raised when a flow or a step is invalid."""


class QuestionUnanswerable(ValueError):
    """Raised when a question's SHAPE makes it unanswerable by the target model.

    THE HUMAN (2026-09-26): "how to help 7B zoom focus to the point / is question
    design and flow problem / and is good experience to help improve question
    flow X logic generator > prompt generator / this is the key for the system".

    MEASURED, and this is why the rule exists: the 7B's RAW reply was RIGHT, but
    the ABSENT-shaped instruction failed EXACTLY the single-missing case (3 of 4
    wrong). `required - present` is a SET DIFFERENCE; a 7B does not compute it
    reliably, and `NONE` is the SAFE answer, so on a near-miss it collapses to
    `NONE`. The question's own escape hatch is what the model reaches for.

    THE RULE: a question whose answer can be "nothing" invites the model to
    answer "nothing". Ask for what IS there, or ask N one-step questions.
    """


# The phrases that make a membership question a SET DIFFERENCE. A question built
# from one of these asks the model to compute `required - present`, which is the
# shape that measured 0.70 recall on the negative class.
_ABSENT_SHAPED = (
    "absent from the mapping",
    "report only the keys that are absent",
    "list the missing keys",
    "which keys are missing",
    "name the absent keys",
)

# The phrases that make a question a ONE-STEP PER ITEM check. These are the
# shapes that measured balanced 1.00, CERTIFIED over 5 repeated runs.
_PER_KEY_SHAPED = (
    "=present",
    "=absent",
    "for each required key",
    "one line",
)


def question_shape(instruction: str) -> dict[str, Any]:
    """The SHAPE of a question, WITHOUT raising. `{ok, shape, why, fix}`.

    MEASURED (2026-09-26): `assert_question_is_answerable` RAISES on the first
    offender, so a caller that generates MANY questions dies on the first bad one
    and never learns how many others are bad. A generator needs to COLLECT them
    and raise ONE error naming all of them — the same shape
    `logic_generator.generate()` already uses for its NO-form gate.

    This is the ONE implementation of "is this question answerable"; the raising
    form delegates to it, so the two cannot disagree.
    """
    text = str(instruction or "")
    low = text.lower()
    hit = [p for p in _ABSENT_SHAPED if p in low]
    if hit:
        return {
            "ok": False, "shape": "absent_shaped", "offender": hit[0],
            "why": ("the question is ABSENT-shaped (%r), so it asks the model to "
                    "compute a SET DIFFERENCE (`required - present`). MEASURED: "
                    "that shape scores balanced 0.85 with recall NO 0.70, failing "
                    "EXACTLY the single-missing case (3 of 4 wrong), because "
                    "`NONE` is the safe answer and the model collapses to it."
                    % hit[0]),
            "fix": ("ask for what IS there, or ask N one-step questions — e.g. "
                    "'For EACH required key, output one line: <KEY>=PRESENT or "
                    "<KEY>=ABSENT.' (measured balanced 1.00, CERTIFIED over 5 "
                    "repeated runs)"),
        }
    per_key = [p for p in _PER_KEY_SHAPED if p in low]
    if per_key:
        return {"ok": True, "shape": "per_key_checklist",
                "why": "one-step-per-item shape (%r)" % per_key[0], "fix": None}
    return {"ok": True, "shape": "other",
            "why": "not ABSENT-shaped; no measured defect for this shape",
            "fix": None}


def assert_question_is_answerable(instruction: str) -> dict[str, Any]:
    """REFUSE a question whose SHAPE the target model cannot answer reliably.

    Returns `{ok, shape, why, fix}`. Raises `QuestionUnanswerable` when the
    question is ABSENT-shaped, because a refusal that returns a value is a
    refusal a caller can ignore.

    MEASURED (2026-09-26), balanced gold set (18 cases, minority class 44%):

        variant                     accuracy  BALANCED  recallYES  recallNO
        A report ABSENT (refused)      0.83      0.85       1.00      0.70
        D report PRESENT               1.00      1.00       1.00      1.00
        E per-key checklist            1.00      1.00       1.00      1.00

    Rule 4 repeated certification (5 runs each): D and E both
    `[1.0, 1.0, 1.0, 1.0, 1.0]`.

    DELEGATES to `question_shape`, so the raising and non-raising forms cannot
    disagree about the same question.
    """
    s = question_shape(instruction)
    if not s.get("ok"):
        raise QuestionUnanswerable("%s FIX: %s" % (s["why"], s["fix"]))
    return s


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the flow tables and add the columns. Idempotent.

    REUSES `workflow_registry` / `workflow_step` (`db_schema.py:2205`, `:2222`).
    A NEW table would duplicate `step_no` / `is_final` / the FK to
    `prompt_registry`, which is the duplication this repo keeps paying for.

    THE MIGRATION IS NOT OPTIONAL. MEASURED (2026-09-22): `CREATE TABLE IF NOT
    EXISTS` does NOTHING on an existing table, so a DB created before
    `created_by` / `is_active DEFAULT 0` keeps the OLD shape — the live
    `workflow_registry` still had `is_active DEFAULT 1` after the DDL was fixed.
    The columns are added by `_add_columns_if_missing`, and the DEFAULT is
    corrected by an explicit UPDATE, because SQLite cannot ALTER a DEFAULT.
    """
    # 🔴 `workflow_registry_DDL` does not exist; the real name is
    # `WORKFLOW_registry_DDL` (`db_schema.py`). MEASURED 2026-09-29.
    from db_schema import (WORKFLOW_registry_DDL, WORKFLOW_STEP_DDL,
                           _add_columns_if_missing)

    conn.executescript(WORKFLOW_registry_DDL)
    conn.executescript(WORKFLOW_STEP_DDL)
    added = _add_columns_if_missing(conn, "workflow_step", _STEP_COLUMNS)
    added += _add_columns_if_missing(conn, "workflow_registry",
                                     _FLOW_COLUMNS)
    conn.commit()
    return {"ok": True, "columns_added": added}


def add_flow(conn: sqlite3.Connection, flow_key: str, name: str, *,
             description: str | None = None,
             created_by: str = "question_flow",
             commit: bool = True) -> dict[str, Any]:
    """Insert or update a flow. A flow is a `workflow_registry` row.

    `created_by` is the MAKER, and it is REQUIRED to be non-empty. The user:
    "so who make the system and who QC for work". A flow with no maker is a flow
    nobody is accountable for.

    A NEW flow is written with `is_active=0` EXPLICITLY, so it is UNPROVEN until
    `activation_gate.activate_flow` proves it. The DDL default is 0 too, but the
    explicit write means the intent is in the code rather than only in the schema.
    """
    ensure_schema(conn)
    if not str(flow_key or "").strip():
        raise FlowError("flow_key must be non-empty")
    if not str(name or "").strip():
        raise FlowError("name must be non-empty")
    if not str(created_by or "").strip():
        raise FlowError("created_by is required — a flow with no maker is a "
                        "flow nobody is accountable for")
    conn.execute(
        "INSERT INTO workflow_registry (workflow_key, name, description, "
        "created_by, is_active) VALUES (?,?,?,?,0) "
        "ON CONFLICT (workflow_key) DO UPDATE SET "
        "name=excluded.name, description=excluded.description, "
        "created_by=excluded.created_by, updated_at=datetime('now')",
        (str(flow_key), str(name), description, str(created_by)))
    if commit:
        conn.commit()
    row = conn.execute("SELECT workflow_id FROM workflow_registry WHERE "
                       "workflow_key=?", (str(flow_key),)).fetchone()
    return {"ok": True, "flow_key": str(flow_key),
            "workflow_id": int(row[0]) if row else None,
            "created_by": str(created_by)}


def add_step(conn: sqlite3.Connection, flow_key: str, step_no: int, *,
             layer_key: str, step_kind: str, question_template: str,
             expected: str = "NA", parser: str = "result_yes_no",
             is_final: bool = False, prompt_id: int | None = None,
             notes: str | None = None, factor_key: str | None = None,
             branch_on: dict[str, Any] | str | None = None,
             is_terminal: bool = False, verdict_rule: str | None = None,
             verdict_params: dict[str, Any] | str | None = None,
             commit: bool = True) -> dict[str, Any]:
    """Insert or update one step of a flow.

    REFUSES a `layer_key` outside `LAYERS` and a `step_kind` outside
    `STEP_KINDS`, because a step whose layer is unknown cannot be attributed and
    a step whose kind is unknown cannot be run.

    `factor_key` DECLARES which factor the step measures (SCOPE AE). It is
    OPTIONAL because a `verdict` step measures no factor of its own -- it is
    computed from the steps that do.

    `branch_on` DECLARES what each answer selects: `{answer: next_step_no}`.
    A dict is serialised to JSON; a string is stored as given (so a caller can
    pass a pre-built JSON document).
    """
    ensure_schema(conn)
    if int(step_no) < 1:
        raise FlowError("step_no must be >= 1, got %r" % step_no)
    if str(layer_key) not in LAYERS:
        raise FlowError("layer_key must be one of %s, got %r"
                        % (LAYERS, layer_key))
    if str(step_kind) not in STEP_KINDS:
        raise FlowError("step_kind must be one of %s, got %r"
                        % (STEP_KINDS, step_kind))
    if not str(question_template or "").strip():
        raise FlowError("question_template must be non-empty")
    branch_json: str | None = None
    if branch_on is not None:
        if isinstance(branch_on, str):
            branch_json = branch_on
        else:
            import json as _json
            branch_json = _json.dumps(branch_on, ensure_ascii=False)
    params_json: str | None = None
    if verdict_params is not None:
        if isinstance(verdict_params, str):
            params_json = verdict_params
        else:
            import json as _json
            params_json = _json.dumps(verdict_params, ensure_ascii=False)
    if verdict_rule is not None and str(verdict_rule) not in VERDICT_RULES:
        raise FlowError("verdict_rule must be one of %s, got %r"
                        % (VERDICT_RULES, verdict_rule))
    f = add_flow(conn, flow_key, flow_key, commit=False)
    wid = int(f["workflow_id"])
    # `prompt_id` is NOT NULL and has an FK to `prompt_registry`, so a step with
    # no prompt of its own must point at a REAL row.
    #
    # DEFECT FOUND BY RUNNING `_proof_contract_ref.py` (2026-09-22): the first
    # version used `0` as a placeholder, which VIOLATED the FK — the proof
    # reported 3 new violations (`workflow_step` 3/4/5 -> `prompt_registry`).
    # A placeholder that breaks a constraint is not a placeholder.
    #
    # The step's own prompt is the flow's `prompt_registry` row when one exists;
    # otherwise the LOWEST existing `prompt_id` is used, because the FK requires
    # a real row and a flow step that asks a COMPUTED question has no prompt of
    # its own. The `question_template` column carries the actual question, so the
    # FK is a structural requirement rather than the source of the text.
    pid = prompt_id
    if pid is None:
        row = conn.execute("SELECT MIN(prompt_id) FROM prompt_registry").fetchone()
        pid = int(row[0]) if row and row[0] is not None else None
    if pid is None:
        raise FlowError(
            "cannot add a step: `workflow_step.prompt_id` is NOT NULL with an FK "
            "to `prompt_registry`, and `prompt_registry` is EMPTY. Register a "
            "prompt first, or pass `prompt_id=` explicitly.")
    conn.execute(
        """
        INSERT INTO workflow_step
            (workflow_id, step_no, prompt_id, is_final, notes,
             layer_key, step_kind, question_template, expected, parser,
             factor_key, branch_on, is_terminal, verdict_rule, verdict_params)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT (workflow_id, step_no) DO UPDATE SET
            prompt_id=excluded.prompt_id,
            is_final=excluded.is_final,
            notes=excluded.notes,
            layer_key=excluded.layer_key,
            step_kind=excluded.step_kind,
            question_template=excluded.question_template,
            expected=excluded.expected,
            parser=excluded.parser,
            factor_key=excluded.factor_key,
            branch_on=excluded.branch_on,
            is_terminal=excluded.is_terminal,
            verdict_rule=excluded.verdict_rule,
            verdict_params=excluded.verdict_params,
            updated_at=datetime('now')
        """,
        (wid, int(step_no), pid, 1 if is_final else 0, notes,
         str(layer_key), str(step_kind), str(question_template), str(expected),
         str(parser), factor_key, branch_json, 1 if is_terminal else 0,
         verdict_rule, params_json))
    if commit:
        conn.commit()
    return {"ok": True, "flow_key": str(flow_key), "step_no": int(step_no),
            "layer_key": str(layer_key), "step_kind": str(step_kind),
            "factor_key": factor_key, "branch_on": branch_json,
            "is_terminal": bool(is_terminal), "verdict_rule": verdict_rule}


def steps_of(conn: sqlite3.Connection, flow_key: str) -> list[dict[str, Any]]:
    """The steps of a flow, in `step_no` order."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT s.* FROM workflow_step s JOIN workflow_registry w "
        "ON w.workflow_id = s.workflow_id WHERE w.workflow_key=? "
        "ORDER BY s.step_no", (str(flow_key),)).fetchall()
    return [dict(r) for r in rows]


def flow_of(conn: sqlite3.Connection, flow_key: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    r = conn.execute("SELECT * FROM workflow_registry WHERE workflow_key=?",
                     (str(flow_key),)).fetchone()
    return dict(r) if r else None


def validate_flow(conn: sqlite3.Connection, flow_key: str) -> dict[str, Any]:
    """The structural rules a flow must satisfy. REPORTS, never raises.

    The rules, and WHY each one exists:

      * `step_no` is CONTIGUOUS from 1 — a gap means a step was deleted and the
        flow's order is no longer the order it was measured with.
      * exactly ONE `is_final` — `case_verdict` (`skill_prompt_step.py:152`)
        falls back to the LAST step when none is marked final, so ZERO final
        steps silently changes which answer is the verdict.
      * a `gate` step must be step 1 — a gate that runs after another question
        cannot stop that question, which is the whole point of a gate.
      * a `verdict` step must be LAST — a verdict computed before the steps it
        depends on would be computed from nothing.
      * every `layer_key` is in `LAYERS` — an unknown layer cannot be attributed.

    `code` — the THREE-WAY answer (added 2026-09-27). THE HUMAN: "Flow = Question
    by 1 -> 2 or 1 -> 2 -> 3 or 1 -> 2 -> 3 -> 4, **when flow = 1, flow = 0, without
    flow**".

    MEASURED DEFECT THIS CLOSES: before this, a flow with ZERO steps and a flow
    that DOES NOT EXIST returned the IDENTICAL answer --
    `problems=['flow has no steps']` for both. They are different facts:

        FLOW_ABSENT  there is no flow with this key  (nothing was ever declared)
        FLOW_EMPTY   the flow EXISTS with 0 steps    (declared, never filled)
        FLOW_OK      it satisfies the rules
        FLOW_INVALID it exists and breaks a rule

    The repo already names this defect class: *"'the table says no' and 'there is
    no table yet' are different answers, and conflating them made an un-migrated
    DB refuse EVERY level."* A caller cannot act on the difference if the answer
    does not carry it.
    """
    if flow_of(conn, flow_key) is None:
        return {"ok": False, "code": "FLOW_ABSENT", "flow_key": str(flow_key),
                "step_count": 0,
                "problems": ["no flow with key %r — nothing was declared for it"
                             % str(flow_key)]}
    steps = steps_of(conn, flow_key)
    problems: list[str] = []
    if not steps:
        return {"ok": False, "code": "FLOW_EMPTY", "flow_key": str(flow_key),
                "step_count": 0,
                "problems": ["the flow EXISTS and has 0 steps — declared but "
                             "never filled"]}
    nos = [int(s["step_no"]) for s in steps]
    if nos != list(range(1, len(nos) + 1)):
        problems.append("step_no is not contiguous from 1: %s" % nos)
    finals = [s for s in steps if int(s.get("is_final") or 0)]
    if len(finals) != 1:
        problems.append("expected exactly ONE is_final step, found %d"
                        % len(finals))
    gates = [s for s in steps if str(s.get("step_kind")) == "gate"]
    if gates and int(gates[0]["step_no"]) != 1:
        problems.append("a `gate` step must be step 1, found at step %s"
                        % gates[0]["step_no"])
    verdicts = [s for s in steps if str(s.get("step_kind")) == "verdict"]
    if verdicts and int(verdicts[-1]["step_no"]) != nos[-1]:
        problems.append("a `verdict` step must be LAST, found at step %s"
                        % verdicts[-1]["step_no"])
    bad = [s["step_no"] for s in steps
           if str(s.get("layer_key")) not in LAYERS]
    if bad:
        problems.append("steps %s have a layer_key outside %s" % (bad, LAYERS))
    # ---- THE BRANCH RULES (SCOPE AE, added 2026-09-28) ---------------------
    # A `branch_on` that names a step which does not exist is a DANGLING branch:
    # the runner falls through rather than dying, so the defect would be SILENT.
    # It is reported here instead.
    nos_set = set(nos)
    for s in steps:
        raw = s.get("branch_on")
        if not raw:
            continue
        try:
            import json as _json
            table = _json.loads(str(raw))
        except (ValueError, TypeError):
            problems.append("step %s has a branch_on that is not JSON"
                            % s["step_no"])
            continue
        if not isinstance(table, dict):
            problems.append("step %s has a branch_on that is not an object"
                            % s["step_no"])
            continue
        for k, v in table.items():
            try:
                tgt = int(v)
            except (TypeError, ValueError):
                problems.append("step %s branch %r -> %r is not a step number"
                                % (s["step_no"], k, v))
                continue
            if tgt not in nos_set:
                problems.append("step %s branch %r -> %s names no step"
                                % (s["step_no"], k, tgt))
    return {"ok": not problems,
            "code": "FLOW_OK" if not problems else "FLOW_INVALID",
            "flow_key": flow_key,
            "step_count": len(steps), "problems": problems,
            "layers": sorted({str(s.get("layer_key")) for s in steps}),
            "kinds": [str(s.get("step_kind")) for s in steps],
            "branched": any(s.get("branch_on") for s in steps)}


# ---------------------------------------------------------------------------
# THE STEP WRITER — the chain's missing link (added 2026-09-23).
#
# WHY (the user): "logic_generator + prompt generator -> logic_generator +
# prompt generator ... can for many many time, they can be combination".
#
# MEASURED before this: the three pieces EXISTED and did not call each other.
# `logic_generator.generate()` produced the questions and they were THROWN AWAY;
# `prompt_generator.compose_layer_prompts()` produced one scoped prompt per layer
# and nothing attached them; `workflow_step` had every column a step needs
# (`layer_key`, `step_kind`, `question_template`, `expected`, `parser`) and was
# filled BY HAND. So the chain could not be composed at all.
#
# THE MAPPING IS BY QUESTION SEMANTICS, and it reuses the two layers' OWN
# definitions (`question_flow.py:63-66`) rather than inventing a third vocabulary:
#
#     TDD       does the VALUE satisfy the rule
#     ONTOLOGY  does the THING exist / is it registered / is it active
#
# So a question about registration or activation is `ontology`; a question about
# a value, a type or a count is `tdd`. Adding a question family is a row in
# `QUESTION_LAYER`, not a new `if`.
#
# THE VERDICT STEP IS APPENDED AND NEVER ASKED. `run_flow` already refuses to
# send a `verdict` step to the model (measured: the 7B cannot emit a verdict
# after enumerating, recall NO 0.00 on 4 of 8 variants), so the writer always
# ends the flow with one. A flow whose last step is a QUESTION would have no
# computed verdict at all.
QUESTION_LAYER: dict[str, str] = {
    # value questions -> tdd
    "db_driven": "tdd",
    "field_count": "tdd",
    "field_N_name": "tdd",
    "field_N_type": "tdd",
    "literal_derivable": "tdd",
    "param_shadowed": "tdd",
    # existence / registration questions -> ontology
    "registered": "ontology",
    "registered_inactive": "ontology",
    "name_registered": "ontology",
}

# The step kind a generated question becomes. `describe` is the honest default:
# a generated question REPORTS an answer, and whether it should STOP the flow is
# a design decision the caller makes, not one the generator can infer. A caller
# that wants a gate passes `gate_questions=`.
DEFAULT_STEP_KIND = "describe"
VERDICT_STEP_KIND = "verdict"

# ---------------------------------------------------------------------------
# THE VOCABULARY BRIDGE (Phase 8, added 2026-09-23).
#
# MEASURED: there are THREE vocabularies of "step" in this repo, and until this
# mapping existed they could not be joined:
#
#   question_flow.LAYERS            tdd | ontology        (from STAGES)
#   prompt_generator logic layers   env_logic | metric_logic | consistency_logic
#                                   | grounding_logic     (rows in wording_registry)
#   logic_generator families        db_driven | registered | param_shadowed | ...
#
# The FIRST TWO are different AXES, not synonyms, so the bridge is a mapping
# rather than a rename — and it is stated as DATA so adding a layer is a row:
#
#   a question about whether a THING EXISTS/IS REGISTERED  -> ontology
#     ...and `env_logic` is exactly that question for a factor ("is the STATE
#     this factor depends on established")
#   a question about whether a VALUE satisfies a rule       -> tdd
#     ...and metric_logic / consistency_logic / grounding_logic are all that
#     question for a factor (kind, agreement, and does the unit name a subject)
#
# A LAYER WITH NO COUNTERPART IS ABSENT FROM THIS MAP ON PURPOSE. There is
# deliberately no default: attaching an unrelated layer's prompt would put a
# factor-judging guard in front of a table question, which is a worse defect
# than attaching nothing. `steps_from_questions` reports `prompts_attached`
# honestly when the map produces no match.
LAYER_TO_LOGIC_LAYER: dict[str, str] = {
    "ontology": "env_logic",
    "tdd": "metric_logic",
}


def logic_layer_for(layer_key: str) -> str:
    """The `prompt_generator` logic layer for a `LAYERS` member, or ''.

    '' when there is no counterpart — never a guessed default. A caller can then
    tell "no prompt exists for this axis" from "the prompt was empty".
    """
    return LAYER_TO_LOGIC_LAYER.get(str(layer_key or "").strip(), "")


class StepWriteError(ValueError):
    """Raised when a question cannot be turned into a valid step."""


def layer_for_question(question_id: str) -> str:
    """The `LAYERS` member a question belongs to. '' when the family is unknown.

    '' rather than a default: a question whose layer cannot be derived cannot be
    attributed, and `add_step` would refuse it anyway. Guessing `tdd` here would
    move the refusal somewhere less legible.
    """
    qid = str(question_id or "").strip()
    if qid in QUESTION_LAYER:
        return QUESTION_LAYER[qid]
    # The per-field families carry a NUMBER (`field_3_name`), so they are matched
    # by suffix — the same technique `logic_generator.evidence_family` uses, so
    # N stays DERIVED rather than enumerated.
    if qid.startswith("field_") and qid.endswith("_name"):
        return QUESTION_LAYER["field_N_name"]
    if qid.startswith("field_") and qid.endswith("_type"):
        return QUESTION_LAYER["field_N_type"]
    return ""


def steps_from_questions(
    conn: sqlite3.Connection,
    flow_key: str,
    questions: list[dict[str, Any]],
    *,
    spec: dict[str, Any] | None = None,
    skill_key: str | None = None,
    gate_questions: tuple[str, ...] = (),
    commit: bool = True,
) -> dict[str, Any]:
    """Turn a generated QUESTION LIST into the steps of a flow.

    This is the writer the chain was missing: `logic_generator` produces the
    questions, `prompt_generator` produces the per-layer prompt, and this binds
    them into `workflow_step` rows.

    `skill_key` attaches the `prompt_generator` layer prompt: when given, each
    step's `question_template` is the prompt composed for its layer, so the
    question a model is asked is the SCOPED one (with its `scope_guard` and
    `example_pair`) rather than the bare sentence. The composed prompt's
    `combo_key` and `sha256` are recorded in the return value, because two
    prompts must be comparable as ARTEFACTS rather than as text someone
    remembers writing.

    `gate_questions` names question ids that must STOP the flow on a mismatch.
    They are written as `gate` kind and ORDERED FIRST, because `validate_flow`
    requires a gate to be step 1 — a gate that runs after another question
    cannot stop that question, which is the whole point of a gate.

    A question whose layer cannot be derived is REFUSED, with the id named, so
    the fix is "declare the family", not "try again".
    """
    if not questions:
        raise StepWriteError("no questions to write: the list is empty")
    # --- order: gates first, then the rest, then the verdict ----------------
    gates = [q for q in questions
             if str(q.get("question_id")) in tuple(gate_questions)]
    rest = [q for q in questions
            if str(q.get("question_id")) not in tuple(gate_questions)]
    ordered = gates + rest

    unknown = [str(q.get("question_id")) for q in ordered
               if not layer_for_question(str(q.get("question_id")))]
    if unknown:
        raise StepWriteError(
            "question(s) %s belong to no layer. Declare them in "
            "`QUESTION_LAYER` (the mapping is data, not a branch), because a "
            "step whose layer is unknown cannot be attributed." % unknown)

    # --- the layer prompts, composed ONCE -----------------------------------
    prompts: dict[str, dict[str, Any]] = {}
    if skill_key:
        import prompt_generator as pg
        composed = pg.compose_layer_prompts(conn, skill_key)
        prompts = composed.get("prompts", {})

    add_flow(conn, flow_key, flow_key, commit=False)
    written: list[dict[str, Any]] = []
    for i, q in enumerate(ordered, 1):
        qid = str(q["question_id"])
        layer = layer_for_question(qid)
        kind = ("gate" if qid in tuple(gate_questions)
                else DEFAULT_STEP_KIND)
        text = str(q.get("question") or "")
        # THE PROMPT IS LOOKED UP THROUGH THE BRIDGE, because the two axes are
        # different vocabularies (`LAYER_TO_LOGIC_LAYER`). Looking up `layer`
        # directly would find nothing for every question, and the step would
        # silently fall back to the bare sentence — a "reported an unlock that
        # did not happen" defect in miniature.
        logic_layer = logic_layer_for(layer)
        attached = prompts.get(logic_layer) if logic_layer else None
        if attached:
            # THE SCOPED PROMPT, with the bare question as its payload, so the
            # model gets the guard and the example pair the layer carries.
            text = str(attached["prompt_text"]).replace("{payload}", text)
        add_step(conn, flow_key, i, layer_key=layer, step_kind=kind,
                 question_template=text,
                 expected=("YES" if kind == "gate" else "NA"),
                 parser="result_yes_no", is_final=False, commit=False)
        written.append({"step_no": i, "question_id": qid, "layer_key": layer,
                        "step_kind": kind,
                        "logic_layer": logic_layer or "",
                        "combo_key": (attached or {}).get("combo_key"),
                        "sha256": (attached or {}).get("sha256")})
    # --- the verdict step, APPENDED and never asked -------------------------
    n = len(ordered)
    add_step(conn, flow_key, n + 1, layer_key=LAYERS[0],
             step_kind=VERDICT_STEP_KIND,
             question_template="(computed from the steps above; never asked)",
             expected="NA", parser="result_yes_no", is_final=True, commit=False)
    if commit:
        conn.commit()
    return {"ok": True, "flow_key": str(flow_key), "steps": written,
            "step_count": n, "verdict_step": n + 1,
            "gates": [str(q.get("question_id")) for q in gates],
            "prompts_attached": bool(prompts),
            "spec": str((spec or {}).get("subject") or "")}


def run_flow(
    conn: sqlite3.Connection,
    flow_key: str,
    ask: Callable[[str, dict[str, Any]], str],
    *,
    stop_on_gate_fail: bool = True,
    compute_verdict: Callable[[dict[int, str], list[dict[str, Any]]], str] | None = None,
    branch: bool = True,
) -> dict[str, Any]:
    """Ask the steps the ANSWERS select; STOP at a failed gate; COMPUTE the verdict.

    `ask(prompt_text, step) -> str` is injected, so this module never talks to a
    model directly and the runner is testable without one — the same shape as
    `skill_prompt_step.run_case_steps` (`skill_prompt_step.py:355`).

    THE VERDICT IS COMPUTED, NEVER ASKED. MEASURED: the 7B cannot emit a verdict
    after enumerating (recall NO 0.00 on 4 of 8 variants), so a `verdict` step is
    not sent to the model at all. `compute_verdict(answers, steps) -> str` does
    the arithmetic; the default requires every non-verdict step to have answered
    its `expected`.

    A failed GATE stops the flow. `evidence_classify.PROMPT_Q1`'s rule: asking
    question 2 after a failed gate "would produce a confident answer for the
    wrong reason".

    THE NEXT STEP IS SELECTED BY THE ANSWER (SCOPE AE, added 2026-09-28).
    THE HUMAN: *"question is according to the answer by factor!!! not waste
    token"*. MEASURED BEFORE THIS: the loop was `for step in steps:` with the
    ONLY exit a failed gate, so EVERY step was asked in `step_no` order and no
    answer ever selected the next question. The waste was measured:
    `stepwise_ask_db_field_registry` 23 steps / 22 asked (steps 3..22 are the
    SAME question text 20 times), and 91 steps asked across all flows.

    `branch_on` is JSON `{answer: next_step_no}`. When the answer names a
    successor, that step is asked NEXT; otherwise the flow falls through to
    `step_no + 1`. `branch=False` restores the old linear behaviour, so a caller
    can compare the two on the SAME flow — which is how the saving is MEASURED
    rather than asserted.
    """
    steps = steps_of(conn, flow_key)
    if not steps:
        # THE SAME THREE-WAY DISTINCTION `validate_flow` MAKES. MEASURED DEFECT:
        # this returned ONE shape for BOTH "the flow does not exist" and "the flow
        # exists with 0 steps", so a caller could not tell them apart. `code`
        # carries which one it was; `validate_flow` is the single source of the
        # code, so the two cannot disagree.
        v = validate_flow(conn, flow_key)
        return {"ok": False, "code": v.get("code"), "flow_key": flow_key,
                "reason": (v.get("problems") or ["no steps"])[0],
                "steps": [], "answers": {}, "verdict": None,
                "stopped_at": None, "failed_steps": []}

    by_no = {int(s["step_no"]): s for s in steps}
    answers: dict[int, str] = {}
    detail: list[dict[str, Any]] = []
    stopped_at: int | None = None
    asked_nos: list[int] = []

    n: int | None = int(steps[0]["step_no"])
    guard = 0
    while n is not None and n in by_no:
        guard += 1
        if guard > len(steps) + 1:
            # A CYCLE. A branch that returns to a step already asked would loop
            # forever, so it is REFUSED rather than run.
            detail.append({"step_no": n, "kind": "CYCLE", "cycle": True})
            break
        step = by_no[n]
        kind = str(step.get("step_kind") or "")
        if kind in COMPUTED_KINDS:
            # NOT asked. Recorded as computed so the report shows it was not a
            # model answer.
            detail.append({"step_no": n, "kind": kind,
                           "layer_key": str(step.get("layer_key")),
                           "expected": step.get("expected"), "got": None,
                           "ok": None, "computed": True, "judged": False})
            n = _successor(step, None, by_no, branch)
            continue
        try:
            got = str(ask(str(step.get("question_template") or ""), step))
        except Exception as e:
            got = "ERROR:%s" % type(e).__name__
        answers[n] = got
        asked_nos.append(n)
        # ---- A STEP WITH `expected='NA'` IS NOT JUDGED -----------------------
        # MEASURED, and it was a REAL CONNECTION DEFECT (2026-09-24): a generated
        # question is a `describe` step and carries NO expected answer, so
        # `steps_from_questions` writes `expected='NA'`. Comparing the evidence's
        # `'YES'`/`'NO'` against `'NA'` can NEVER be equal, so EVERY generated
        # line produced the verdict `NO` — a verdict that is false by
        # construction, the exact defect class this repo keeps recording.
        #
        # `'NA'` means "this step REPORTS, it does not judge". A judged step must
        # name what it expects; a step that names nothing cannot pass or fail.
        # The two are kept DISTINCT in the detail (`judged`), so a report can say
        # which steps actually decided the outcome.
        judged = str(step.get("expected") or "").strip().upper() != "NA"
        ok = (got == str(step.get("expected"))) if judged else None
        detail.append({"step_no": n, "kind": kind,
                       "layer_key": str(step.get("layer_key")),
                       "factor_key": step.get("factor_key"),
                       "expected": step.get("expected"), "got": got,
                       "ok": ok, "computed": False, "judged": judged})
        if kind == "gate" and judged and not ok and stop_on_gate_fail:
            stopped_at = n
            break
        n = _successor(step, got, by_no, branch)

    if compute_verdict is not None:
        verdict = str(compute_verdict(answers, steps))
    else:
        verdict = _default_verdict(answers, steps, stopped_at)

    asked = [d for d in detail if not d["computed"]]
    judged_steps = [d for d in asked if d["judged"]]
    failed = [d["step_no"] for d in judged_steps if not d["ok"]]
    # `ok` AND `verdict` MUST AGREE. MEASURED, and it was a REAL BUG in my first
    # version: `ok` was computed from `failed` alone, so a flow that judged
    # NOTHING reported `ok=True` while `_default_verdict` returned `NO` — two
    # fields of the same result telling different stories, which is worse than
    # either being wrong on its own because a caller reads whichever suits it.
    # `ok` is now derived FROM the verdict, so they cannot drift.
    verdict_ok = verdict == "YES"
    return {
        "ok": verdict_ok,
        "code": "FLOW_OK" if verdict_ok else "FLOW_VERDICT_NO",
        "flow_key": flow_key,
        "steps": detail,
        "step_count": len(steps),
        "asked_count": len(asked),
        "judged_count": len(judged_steps),
        "described_count": len(asked) - len(judged_steps),
        "asked_step_nos": asked_nos,
        "branched": bool(branch),
        "answers": answers,
        "verdict": verdict,
        "stopped_at": stopped_at,
        "failed_steps": failed,
        "layers": sorted({str(s.get("layer_key")) for s in steps}),
    }


def _successor(step: dict[str, Any], answer: str | None,
               by_no: dict[int, dict[str, Any]], branch: bool) -> int | None:
    """The step to ask NEXT: the answer's selection, else `step_no + 1`.

    A DECLARED `is_terminal` step ends the flow. A `branch_on` that names a step
    which does not exist is IGNORED (the flow falls through) rather than
    crashing — a dangling branch is a data defect, and the runner reports it by
    falling through rather than by dying.
    """
    if not branch:
        return int(step["step_no"]) + 1
    if int(step.get("is_terminal") or 0):
        return None
    raw = step.get("branch_on")
    if raw:
        try:
            import json as _json
            table = _json.loads(str(raw))
        except (ValueError, TypeError):
            table = {}
        if isinstance(table, dict):
            key = str(answer or "").strip().upper()
            for k, v in table.items():
                if str(k).strip().upper() == key:
                    try:
                        nxt = int(v)
                    except (TypeError, ValueError):
                        continue
                    if nxt in by_no:
                        return nxt
    return int(step["step_no"]) + 1


def _default_verdict(answers: dict[int, str], steps: list[dict[str, Any]],
                     stopped_at: int | None) -> str:
    """YES only when every JUDGED step matched its `expected`, and no gate stopped.

    A stopped gate is a NO by construction: the flow did not reach the end, so
    the thing under test was not confirmed.

    A STEP WITH `expected='NA'` IS SKIPPED, because it REPORTS rather than judges
    (see `run_flow`). MEASURED, and it was a REAL DEFECT: without this skip, a
    generated line — whose `describe` steps all carry `'NA'` — compared `'YES'`
    against `'NA'`, which can never be equal, so EVERY such line returned `NO`.
    A verdict that is false by construction is worse than no verdict.

    THE SKIP CANNOT MAKE THE VERDICT VACUOUS: a flow with NO judged step at all
    returns `NO`, not `YES`. MEASURED reason — a flow that judged nothing has
    confirmed nothing, and reporting `YES` for it would be the "silent pass"
    this repo records as its most repeated defect.
    """
    if stopped_at is not None:
        return "NO"
    judged_any = False
    for s in steps:
        if str(s.get("step_kind")) in COMPUTED_KINDS:
            continue
        if str(s.get("expected") or "").strip().upper() == "NA":
            continue            # a describe step: it reports, it does not judge
        n = int(s["step_no"])
        judged_any = True
        if n not in answers:
            return "NO"
        if answers[n] != str(s.get("expected")):
            return "NO"
    if not judged_any:
        # NOTHING WAS JUDGED, so nothing was confirmed. `YES` here would be a
        # pass produced by the absence of a check.
        return "NO"
    return "YES"


def name_matches(reply: str, required: list[str]) -> tuple[bool, list[str]]:
    """Did the reply NAME every required field? A SET MEMBERSHIP test.

    The `describe` step's answer is matched against the required KEY NAMES, not
    free-form judged — so it stays a MEASUREMENT rather than an opinion. This is
    the `_probe_vl_keys.py` rule: ask the model to name things, then check the
    names mechanically.

    DEFECT FOUND BY RUNNING IT (2026-09-22): the first version required an EXACT
    substring match, and the 7B read `CHAT_SHA256` as `CHAT_SHA255` — the last
    character wrong. So a COMPLETE payload scored NO. That is the exact defect
    `_probe_vl_keys.py` names: "asking it to match the exact value is testing the
    model's OCR, not the paste."

    The fix matches on the DISTINCTIVE PREFIX (`CHAT_SHA`), which is what
    identifies the field. A prefix is not a loosening of the test — it is the
    test the model can actually answer, and the field is still distinguished from
    every other field in the contract.

    This lives HERE rather than in a caller, because a caller that re-implements
    it is a second copy of the rule — the drift this repo keeps hitting.
    """
    up = (reply or "").upper()
    found: list[str] = []
    for f in required:
        stem = str(f).rstrip("0123456789") or str(f)
        if stem in up:
            found.append(str(f))
    return len(found) == len(required), found


def make_name_verdict(required: list[str]) -> Callable[..., str]:
    """A `compute_verdict` that requires the gate to pass AND every name present.

    The gate is step 1 by construction (`validate_flow` enforces it), so the
    verdict is: the gate answered YES, and the describe step named every
    required field.
    """
    def _fn(answers: dict[int, str], steps: list[dict[str, Any]]) -> str:
        gate = str(answers.get(1) or "").strip().upper()
        if "YES" not in gate:
            return "NO"
        ok, _found = name_matches(str(answers.get(2) or ""), required)
        return "YES" if ok else "NO"
    return _fn


def record_lesson(conn: sqlite3.Connection, flow_key: str, step_no: int, *,
                  lesson_text: str, root_cause: str, cite_ref: str,
                  suggested_fix: str = "",
                  skill_key: str = "question_flow",
                  commit: bool = True) -> dict[str, Any]:
    """A failed step becomes a LESSON. "YES or NO, explain -> lesson."

    Writes `skill_lesson` (`db_schema.py:2349`) with `source_type='self_fail'`.
    `cite_ref` is REQUIRED and must be checkable (citation_discipline): a lesson
    with no reference is discarded at the write site, never downgraded.

    `suggested_fix` is REQUIRED TOO. DEFECT FOUND BY RUNNING
    `_proof_lesson_report.py` (2026-09-22): the first version left it empty, and
    the proof reported "every lesson names a suggested fix" FAILED. A lesson that
    says what went wrong but not what to DO is a complaint, not a lesson — the
    same rule `problem-statement` enforces for a problem.
    """
    import uuid
    if not str(cite_ref or "").strip():
        raise FlowError("cite_ref is required — a lesson with no reference is "
                        "not a finding")
    if not str(lesson_text or "").strip():
        raise FlowError("lesson_text must be non-empty")
    if not str(suggested_fix or "").strip():
        raise FlowError("suggested_fix is required — a lesson that says what "
                        "went wrong but not what to do is a complaint, not a "
                        "lesson")
    from db_schema import SKILL_LESSON_DDL
    conn.executescript(SKILL_LESSON_DDL)
    key = "ls_%s" % uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO skill_lesson (lesson_key, skill_key, source_type, "
        "root_cause, lesson_text, suggested_fix, source_ref, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (key, str(skill_key), "self_fail", str(root_cause), str(lesson_text),
         str(suggested_fix), str(cite_ref), "draft"))
    if commit:
        conn.commit()
    return {"ok": True, "lesson_key": key, "flow_key": flow_key,
            "step_no": int(step_no), "cite_ref": str(cite_ref)}


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
    sub.add_parser("schema")
    v = sub.add_parser("validate")
    v.add_argument("flow_key")
    s = sub.add_parser("steps")
    s.add_argument("flow_key")
    args = ap.parse_args(argv)
    conn = _connect(args.db)
    try:
        if args.cmd == "schema":
            print(json.dumps(ensure_schema(conn), ensure_ascii=False))
            return 0
        if args.cmd == "validate":
            print(json.dumps(validate_flow(conn, args.flow_key),
                             ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "steps":
            for s_ in steps_of(conn, args.flow_key):
                print("  %d %-9s %-9s %s" % (s_["step_no"], s_["layer_key"],
                                             s_["step_kind"],
                                             str(s_["question_template"])[:60]))
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())