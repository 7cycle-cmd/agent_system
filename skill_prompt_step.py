# -*- coding: utf-8 -*-
"""skill_prompt_step.py — multi-step cases + prompt composition.

WHY THIS FILE EXISTS
--------------------
Two measured gaps (2026-09-20):

1. **Multi-step cases could not be expressed.** `skill_prompt_case.expected` is
   `CHECK (expected IN ('YES','NO'))` — one binary answer per case. A case
   needing 2-3 steps had to be flattened (losing the steps) or split into N
   unrelated cases (losing that they belong together). So a 100-streak over
   single-step cases proves the model can answer ONE simple question 100 times.

2. **`prompt = case + skill` was not modelled.** The two tables were joined only
   AFTER the fact via `skill_prompt_test_run.case_id` — a RESULT, not a
   composition. A prompt could not be reproduced from its parts, only copied.

This module adds both, and REUSES `prompt_dimension.compose_prompt()` rather than
building a second composer — two composers would drift, and the drift would be
invisible until a measurement disagreed with itself.

BACKWARD COMPATIBILITY
----------------------
A case with no steps is treated as ONE implicit step built from its `expected`.
Nothing existing breaks; `expected` stays authoritative until steps exist.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).resolve().parent / "agent.db"

# `{{case:field}}` slots, alongside prompt_dimension's `{{dim:x}}`.
CASE_SLOT_RE = re.compile(r"\{\{case:([a-zA-Z0-9_]+)\}\}")

# Case fields a template may reference. An allowlist, so a typo cannot silently
# substitute an empty string into a prompt that then reaches the model.
CASE_FIELDS = (
    "case_key", "skill_key", "image_path", "vision_ref",
    "target_name", "target_action", "expected", "expected_reason", "notes",
    # A PAIR, not a single target. `subject`/`object` were added because a
    # relation question ("does A serve B?") has two participants and the
    # existing fields only name one. Measured need: the four
    # `ontology_relation` templates could not be written with the old list.
    "subject", "object",
    # Candidate-set text. Needed to separate "cannot judge a relation" from
    # "has never heard of these names" — without a glossary variant those two
    # produce the identical wrong answer.
    "glossary",
)


class StepError(ValueError):
    """Raised when a step or composition is invalid."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    from db_schema import PROMPT_COMPOSITION_DDL, SKILL_PROMPT_STEP_DDL

    conn.executescript(SKILL_PROMPT_STEP_DDL)
    conn.executescript(PROMPT_COMPOSITION_DDL)


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def add_step(
    conn: sqlite3.Connection,
    case_id: int,
    step_no: int,
    question_template: str,
    expected: str,
    *,
    parser: str = "result_yes_no",
    is_final: bool = False,
    notes: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Insert or update one step of a case."""
    ensure_tables(conn)
    if int(step_no) < 1:
        raise StepError("step_no must be >= 1, got %r" % step_no)
    if not str(question_template or "").strip():
        raise StepError("question_template must be non-empty")
    if not str(expected or "").strip():
        raise StepError("expected must be non-empty")

    conn.execute(
        """
        INSERT INTO skill_prompt_step
            (case_id, step_no, question_template, expected, parser, is_final, notes)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT (case_id, step_no) DO UPDATE SET
            question_template=excluded.question_template,
            expected=excluded.expected,
            parser=excluded.parser,
            is_final=excluded.is_final,
            notes=excluded.notes,
            updated_at=CURRENT_TIMESTAMP
        """,
        (int(case_id), int(step_no), str(question_template), str(expected),
         str(parser), 1 if is_final else 0, notes),
    )
    if commit:
        conn.commit()
    return {"ok": True, "case_id": int(case_id), "step_no": int(step_no)}


def list_steps(conn: sqlite3.Connection, case_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM skill_prompt_step WHERE case_id=? ORDER BY step_no",
        (int(case_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def effective_steps(conn: sqlite3.Connection, case: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps to run for a case.

    A case with NO stored steps yields ONE implicit step built from its
    `expected`. This is what keeps every pre-existing case working unchanged.
    """
    steps = list_steps(conn, int(case["id"]))
    if steps:
        return steps
    return [{
        "id": None,
        "case_id": int(case["id"]),
        "step_no": 1,
        "question_template": "{{case:target_name}}",
        "expected": str(case.get("expected") or ""),
        "parser": "result_yes_no",
        "is_final": 1,
        "notes": "implicit step (case has no stored steps)",
        "_implicit": True,
    }]


def case_verdict(conn: sqlite3.Connection, case: dict[str, Any],
                 step_answers: dict[int, str]) -> str | None:
    """The case verdict = the `is_final` step's answer.

    Falls back to the LAST step when no step is marked final, so a case whose
    steps were added without `is_final` still yields a verdict rather than None.
    """
    steps = effective_steps(conn, case)
    if not steps:
        return None
    final = next((s for s in steps if s.get("is_final")), steps[-1])
    return step_answers.get(int(final["step_no"]))


# ---------------------------------------------------------------------------
# composition: prompt = skill + case + combo + step
# ---------------------------------------------------------------------------

def composition_key(skill_key: str, case_id: int | None, combo_key: str | None,
                    step_no: int | None) -> str:
    """Canonical key over the sorted parts. Same parts -> same key, always."""
    parts = {
        "skill_key": str(skill_key or ""),
        "case_id": "" if case_id is None else str(int(case_id)),
        "combo_key": str(combo_key or ""),
        "step_no": "" if step_no is None else str(int(step_no)),
    }
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def substitute_case_slots(template: str, case: dict[str, Any],
                          *, strict: bool = True,
                          allow_missing_fields: bool = False) -> str:
    """Replace `{{case:field}}` with the case's value.

    `strict=True` REFUSES an unknown field, a field the case does not carry AT
    ALL, and a leftover slot — the same rule `prompt_dimension.compose_prompt()`
    applies to `{{dim:x}}`. A silently unsubstituted slot would reach the model
    as literal text.

    THE SECOND PLACEHOLDER DEFECT (measured 2026-09-21)
    ---------------------------------------------------
    `{{case:object}}` is a WELL-FORMED slot, so the write gate cannot refuse it,
    and the composer has no reason to. If the caller's case dict simply lacks
    the key, `case.get()` returns None and the old code wrote "" — producing
    "Does /api/apps SERVE ?" and sending it to the model. That is worse than a
    bare `{{subject}}`: it LOOKS composed, so the run is scored as a relation
    answer and the empty slot is invisible in the result table. This was found
    by reading back the recorded prompt, not by any assertion.

    So an ABSENT field is now refused. An EXPLICITLY empty value
    (`{"object": None}` or `{"object": ""}`) is still allowed, because "I mean
    this to be blank" and "I forgot to pass it" must not be the same thing.
    """
    slots = set(CASE_SLOT_RE.findall(template))
    unknown = sorted(slots - set(CASE_FIELDS))
    if strict and unknown:
        raise StepError(
            "unknown case field(s) %s; known=%s" % (unknown, list(CASE_FIELDS))
        )
    if strict and not allow_missing_fields:
        absent = sorted(s for s in slots if s not in case)
        if absent:
            raise StepError(
                "case slot(s) %s have NO value in the case at all. Substituting "
                "an empty string would ask the model about nothing and would "
                "LOOK composed (measured: 'Does /api/apps SERVE ?' was sent and "
                "scored as a relation answer, invalidating the run). Pass the "
                "field explicitly as None or '' if a blank value is intended."
                % absent
            )
    out = template
    for field in slots:
        val = case.get(field)
        out = out.replace("{{case:%s}}" % field, "" if val is None else str(val))
    leftover = CASE_SLOT_RE.findall(out)
    if strict and leftover:
        raise StepError(
            "unsubstituted case slot(s) %s would be sent to the model verbatim"
            % leftover
        )
    return out


def compose_case_prompt(
    conn: sqlite3.Connection,
    *,
    skill_key: str,
    case: dict[str, Any] | None = None,
    combo_key: str | None = None,
    step_no: int | None = None,
    template: str | None = None,
    dim_values: dict[str, str] | None = None,
    record: bool = True,
    commit: bool = True,
    require_nonempty: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Compose one prompt from its parts, and (optionally) record it.

    Order matters: `{{dim:x}}` is substituted by `prompt_dimension` FIRST, then
    `{{case:field}}`. Doing it the other way would let a case value containing
    `{{dim:...}}` text be re-substituted — a case value is DATA, never a slot.

    `require_nonempty` — the SEMANTIC half of the slot invariant.
    ------------------------------------------------------------
    `substitute_case_slots` refuses a slot whose field is ABSENT. It cannot
    refuse a field that is present and empty, because "empty" is sometimes
    exactly what the author means (`context=bare` supplies an empty glossary).
    Only the CALLER knows which fields must carry content.

    So the caller names them here, IN THE SAME CALL that composes, instead of
    re-implementing the guard at each call site. Measured need: the S4 relation
    probe sent `Does /api/apps SERVE ?` to the model because the object was
    empty, and every NO that came back was correct-and-uninformative — a whole
    run invalidated. The guard that catches it belongs next to the composition,
    not in one probe.
    """
    ensure_tables(conn)

    if template is None:
        row = conn.execute(
            "SELECT prompt_text FROM skill_prompt_ssot WHERE skill_key=? "
            "ORDER BY id DESC LIMIT 1", (skill_key,),
        ).fetchone()
        if not row:
            raise StepError("no prompt_text for skill_key=%r" % skill_key)
        template = row["prompt_text"]

    text = template

    # 1) dimension slots (reuse the register-backed composer — do not reimplement)
    if "{{dim:" in text:
        # SSOT: registers, not the retired prompt_dimension.
        import prompt_generator as pd

        if not dim_values:
            raise StepError(
                "template has {{dim:...}} slots but no dim_values were supplied"
            )
        text = pd.compose_prompt(conn, skill_key, dim_values, template=text)

    # 2) case slots
    if case is not None and "{{case:" in text:
        text = substitute_case_slots(text, case)

    cid = int(case["id"]) if case and case.get("id") is not None else None
    ck = composition_key(skill_key, cid, combo_key, step_no)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    # 3) the SEMANTIC half of the invariant, at the composition, not at a caller.
    #    The structural half (field absent) is refused inside
    #    substitute_case_slots; this covers the case it cannot judge.
    if require_nonempty:
        blank = [f for f in require_nonempty
                 if not str((case or {}).get(f) or "").strip()]
        if blank:
            # Show the damage, not just the field name: the reader needs to see
            # WHY this is fatal.
            raise StepError(
                "case field(s) %s are present but EMPTY, and this composition "
                "requires content. Composed text (would be sent to the model): "
                "%r" % (blank, text)
            )

    if record:
        conn.execute(
            """
            INSERT INTO prompt_composition
                (composition_key, skill_key, case_id, combo_key, step_no,
                 prompt_text, sha256)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT (composition_key) DO UPDATE SET
                prompt_text=excluded.prompt_text, sha256=excluded.sha256
            """,
            (ck, skill_key, cid, combo_key, step_no, text, sha),
        )
        if commit:
            conn.commit()

    return {
        "composition_key": ck,
        "skill_key": skill_key,
        "case_id": cid,
        "combo_key": combo_key,
        "step_no": step_no,
        "prompt_text": text,
        "sha256": sha,
    }


def get_composition(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    ensure_tables(conn)
    row = conn.execute(
        "SELECT * FROM prompt_composition WHERE composition_key=?", (key,)
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# multi-step runner
# ---------------------------------------------------------------------------

def run_case_steps(
    conn: sqlite3.Connection,
    case: dict[str, Any],
    *,
    ask: Any,
    skill_key: str | None = None,
    combo_key: str | None = None,
    dim_values: dict[str, str] | None = None,
    template: str | None = None,
) -> dict[str, Any]:
    """Ask every step of a case IN ORDER; return per-step answers + the verdict.

    `ask(prompt_text, step) -> str` is injected, so this module never talks to a
    model directly and the runner is testable without one.

    A step whose answer does not match its `expected` fails the CASE — the
    remaining steps are still asked (so the failure is fully characterised), but
    the verdict is already decided.
    """
    steps = effective_steps(conn, case)
    sk = skill_key or str(case.get("skill_key") or "")

    answers: dict[int, str] = {}
    detail: list[dict[str, Any]] = []
    for step in steps:
        n = int(step["step_no"])
        composed = compose_case_prompt(
            conn, skill_key=sk, case=case, combo_key=combo_key, step_no=n,
            template=template or step.get("question_template"),
            dim_values=dim_values, record=True,
        )
        try:
            got = ask(composed["prompt_text"], step)
        except Exception as e:
            got = "ERROR:%s" % type(e).__name__
        answers[n] = str(got)
        detail.append({
            "step_no": n,
            "expected": step.get("expected"),
            "got": str(got),
            "ok": str(got) == str(step.get("expected")),
            "composition_key": composed["composition_key"],
        })

    verdict = case_verdict(conn, case, answers)
    all_ok = all(d["ok"] for d in detail)

    # A multi-step case is a CONJUNCTION: every step must hold.
    #
    # WHY `ok` IS NOT JUST `verdict == expected`: the verdict comes from the
    # `is_final` step, so a case whose MIDDLE step failed but whose final step
    # was right would report ok=True. That is wrong — a failed precondition
    # ("is it enabled?") must fail the case even when the last question is
    # answered correctly. Found by this module's own proof, which asserted a
    # wrong step 2 fails the case and got ok=True.
    #
    # `verdict` is still reported separately, because it is the answer to the
    # final question and is useful on its own.
    return {
        "case_key": case.get("case_key"),
        "case_id": case.get("id"),
        "steps": detail,
        "step_count": len(steps),
        "answers": answers,
        "verdict": verdict,
        "expected": case.get("expected"),
        "ok": all_ok and verdict == case.get("expected"),
        "all_steps_ok": all_ok,
        "failed_steps": [d["step_no"] for d in detail if not d["ok"]],
    }
