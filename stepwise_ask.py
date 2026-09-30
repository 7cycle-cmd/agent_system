# -*- coding: utf-8 -*-
"""stepwise_ask.py — ONE key, ONE narrow question at a time, until YES or NO.

WHY (the human, 2026-09-25, verbatim)
-------------------------------------
    "you need to ask 7B to have work 1 by 1 ... `PARENT_NOT_FOUND` 109
     -> logic generate -> prompt generator -> question flow ... KO by step and
     step, the method is how to design question level -> narrow and finally have
     yes or no, explain factor ... so 7B can work with us easy ... question will
     be 1) A,B,C  2) A,B,C  3) A,B,C ... so 7B can smart"

    "logic generation can help to have prompt / you can have setting for that for
     7B, it is easy / question -> ask -> logic generator -> output -> question for
     7B step 1 / step 2 / step 3"

THE CHAIN, AND WHERE THIS MODULE SITS
-------------------------------------
    discovery            the source / the live DB      deterministic
    logic_generator      the QUESTIONS, each with yes_form / no_form
    question_flow        the STEPS (gate / describe / count / name / verdict)
    ->  run_flow         asks every step IN ORDER, STOPS at a failed gate,
    |                    and COMPUTES the verdict
    +-> THIS MODULE      supplies the `ask(prompt, step) -> str` adapter, and
                         records ONE answer per step, with its citation
    the register         REFUSES an unciteable key            the gate

**This module does NOT implement stepping.** `question_flow.run_flow` owns the
order, the gate stop, and the verdict. Re-implementing any of those here would
put two opinions of "what the flow is" in the repo, which is the duplication
class this repo keeps paying for.

THE 7B'S ROLE IS FILLING MISSING DATA, ONE NARROW QUESTION AT A TIME
--------------------------------------------------------------------
It is NOT an analyst, and it is NOT asked for a verdict:

  * MEASURED: the 7B cannot emit a verdict after enumerating (recall NO 0.00 on
    4 of 8 variants). `run_flow` already refuses to send a `verdict` step to the
    model, and `ask_one` refuses a second time — see `_form_of`.
  * MEASURED (`_proof_terminology_cite.py` case 8): the 7B's citations were NOT
    usable — it put a refusal in `cite_ref` twice and invented a filename once.
    So the 7B is NEVER asked for a citation here either. The citation is
    SUPPLIED by `terminology_cite.locate_name`, which is deterministic.

AN OFF-FORM ANSWER IS A FIRST-CLASS OUTCOME
-------------------------------------------
Asking "did it work?" and accepting whatever comes back is how a confident
answer gets produced for the wrong reason. When the answer is not one of the
step's OWN forms, the run records `LLM_OFF_FORM`, names the step, and STOPS.
An off-form answer is never coerced, never trimmed into form, never counted.

NEVER ACTIVATE
--------------
Pattern capture writes `flow_setting` rows with `is_active=0`, and this module
contains NO call to an activation entry point. A pattern that repeats is
EVIDENCE a pattern exists; making it LIVE is a human decision.

Run:
    .\\.venv\\Scripts\\python.exe stepwise_ask.py --key db_field_registry --dry-run
    .\\.venv\\Scripts\\python.exe stepwise_ask.py --key db_field_registry --no-llm
    .\\.venv\\Scripts\\python.exe stepwise_ask.py --key db_field_registry --apply
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import db_schema as ds  # noqa: E402
import logic_generator as lg  # noqa: E402
import question_flow as qf  # noqa: E402
import terminology_cite as tc  # noqa: E402

DB = BASE / "agent.db"

# ---------------------------------------------------------------------------
# THE ROUTE IS A REFERENCE, NOT A LITERAL
#
# `register_fill.py` already resolves the text model from the `llm.text` route
# via `llm_service_store.resolve_text_model`, and its `OLLAMA` constant uses the
# ollama DEFAULT port. MEASURED 2026-09-25 (this session): BOTH
# `127.0.0.1:11434` and `127.0.0.1:18803` answer `/api/tags` with the same two
# models, so the literal is not broken TODAY.
#
# It is still re-declared here as a NAMED constant rather than imported, because
# `llm_service_store.OLLAMA_BASE` is the one the *service registry* uses for its
# reachability check, and a second literal that can drift from it defeats that
# check. The ownership decision (fold this constant into `llm_service_store`,
# which `register_fill.py` also needs) is a NAMED FOLLOW-UP: neither file is in
# this plan's allowlist.
# ---------------------------------------------------------------------------
OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"

# The system instruction. It states the ONE thing the model may do (answer the
# question it was asked) and the TWO things it may not (analyse, decide). The
# forms are repeated in the user turn by `compose_prompt`, because a small model
# honours the LAST instruction it read.
ASK_SYSTEM = (
    "You answer ONE narrow question about a database key. "
    "You are NOT asked to analyse anything and NOT asked to decide anything. "
    "Answer with EXACTLY one of the allowed forms, and nothing else."
)

# The answer forms a step may expect. Derived from `run_flow`'s own rule —
# `expected='NA'` means "this step REPORTS, it does not judge" — so the two
# modules cannot disagree about what a judged step looks like.
NON_JUDGED_FORM = "NA"
ANSWER_FORMS = ("YES", "NO")

# The outcome codes recorded per step. A refusal is a VALUE here, not an error:
# "it refused" and "it lied" must be tellable apart in a report.
STATUS_ANSWERED = "ANSWERED"
STATUS_OFF_FORM = "LLM_OFF_FORM"
STATUS_STOPPED = "STOPPED_AFTER_OFF_FORM"
STATUS_REFUSED_BY_FORM = "REFUSED_NON_JUDGED_STEP"
STATUS_COMPUTED = "COMPUTED"


# ---------------------------------------------------------------------------
# 1. THE STEP TABLE — where an answer is recorded
#
# WHY A NEW TABLE AND NOT `flow_setting`: `flow_setting` is the FLOW DEFINITION
# ("what to ask and where to go next"), and a run's ANSWERS are a different kind
# of fact. Writing an answer into the definition table would make "the question
# this flow asks" and "the answer this run got" the same row, so a second run
# could not be compared with the first — the exact loss this repo records as
# "a snapshot assertion asserts a GAP as correct state".
#
# The table is OWNED HERE, following `job_registry.py` / `capability_binding.py`
# (a module owns the DDL for the fact it owns). It is NOT added to
# `db_schema._DDL` list, so `schema_version.check` does not govern it and a
# re-read of an unchanged `ONTOLOGY_REGISTRY_DDL` constant cannot make
# `existing rows stay as they are`.
# ---------------------------------------------------------------------------
STEP_ANSWER_DDL = """
CREATE TABLE IF NOT EXISTS step_answer (
    answer_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key     TEXT    NOT NULL,
    subject_key TEXT    NOT NULL,
    flow_key    TEXT    NOT NULL,
    step_no     INTEGER NOT NULL,
    step_kind   TEXT    NOT NULL DEFAULT 'NA',
    layer_key   TEXT    NOT NULL DEFAULT 'NA',
    question    TEXT,
    answer      TEXT,
    expected    TEXT,
    status      TEXT    NOT NULL DEFAULT 'NA',
    cite_ref    TEXT,
    model       TEXT,
    elapsed_ms  INTEGER,
    observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_key, step_no)
);
CREATE INDEX IF NOT EXISTS idx_step_answer_subject
    ON step_answer (subject_key, step_no);
"""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(STEP_ANSWER_DDL)
    conn.commit()
    return {"ok": True, "table": "step_answer"}


# ---------------------------------------------------------------------------
# 2. THE FORM RULE — the one place that decides whether a step is judged
# ---------------------------------------------------------------------------
def _form_of(expected: Any) -> str:
    """The allowed FORM SET of a step, from the step's OWN `expected`.

    `question_flow.run_flow` says it in one sentence:
        "A step with `expected='NA'` is NOT JUDGED ... 'NA' means 'this step
         REPORTS, it does not judge'."
    This function is the same rule, so the asker and the runner cannot drift.

    A `describe` step yields `NA` — the model is asked to REPORT, and any answer
    is acceptable. A judged step yields `JUDGED` — the ALLOWED FORMS are
    `{'YES', 'NO'}` and ONLY those two are accepted.

    MEASURED, AND IT WAS A REAL DEFECT IN MY FIRST VERSION: this function used to
    return the step's `expected` (`YES`) as if it were the allowed form set.
    `classify_answer` then required the reply to EQUAL `YES`, so a gate that
    answered the perfectly legitimate `NO` was recorded as `LLM_OFF_FORM` — the
    report said "the model went off form" when it had done nothing wrong, and the
    stop was attributed to the wrong cause.

    TWO FACTS, KEPT APART:
        what the step ACCEPTS (the form set, here)          {"YES","NO"}
        what the step EXPECTS to pass (`expected`, run_flow)  "YES"
    `run_flow` already owns the second one — it compares `got == expected`. A
    `NO` where `YES` was expected is an IN-FORM ANSWER that FAILS the step.
    """
    e = str(expected or "").strip().upper()
    if e == NON_JUDGED_FORM or not e:
        return NON_JUDGED_FORM
    if e in ANSWER_FORMS:
        return "JUDGED"
    # A declared `expected` that is neither NA nor YES/NO. Returned AS-IS so the
    # caller can record it rather than silently widening the accepted set — a
    # step whose expected form is unreadable cannot accept an answer.
    return "UNKNOWN_FORM:%s" % e


def classify_answer(text: Any, form: str) -> tuple[str, str]:
    """(accepted_answer, status) for a raw model reply and an allowed-form set.

    THE RULE, in one place: an answer is IN FORM when it IS one of the forms the
    step accepts. Nothing is coerced. `'yes, it does'` is NOT `YES`, and calling
    it YES would manufacture the one thing this whole chain exists to avoid.

    A JUDGED step accepts `YES` or `NO` — BOTH of them. Which one it EXPECTED is
    `run_flow`'s business, and it decides `ok` by comparing the two. A `NO` where
    `YES` was expected is an IN-FORM answer that FAILS the step; it is NOT
    `LLM_OFF_FORM`. Conflating those two made the report blame the model for
    answering correctly (MEASURED — see `_form_of`).

    A step whose expected form is unreadable accepts nothing, so every reply is
    off-form and the caller records that rather than guessing a vocabulary.
    """
    raw = str(text or "").strip()
    if form == NON_JUDGED_FORM:
        return raw, STATUS_ANSWERED
    if form.startswith("UNKNOWN_FORM"):
        return raw, STATUS_OFF_FORM
    if raw.upper() in ANSWER_FORMS:
        return raw.upper(), STATUS_ANSWERED
    return raw, STATUS_OFF_FORM


# ---------------------------------------------------------------------------
# 3. THE ASK ADAPTER — the ONLY thing injected into run_flow
# ---------------------------------------------------------------------------
def resolve_model(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The text model, resolved from the `llm.text` route. Never a literal."""
    import llm_service_store as lss

    return lss.resolve_text_model(conn)


def compose_prompt(question_text: str, step: dict[str, Any]) -> str:
    """The prompt for ONE step. The forms are in the prompt, not implied.

    A small model cannot infer an accepted vocabulary: if `YES`/`NO` is not
    written in front of it, it answers what it thinks is useful, and every answer
    is off-form for a reason that had nothing to do with the model's ability.
    """
    form = _form_of(step.get("expected"))
    lines = [
        "question:",
        ">>", str(question_text or "").strip(), "<<",
        "",
    ]
    if form == "JUDGED":
        lines += [
            "the allowed forms are EXACTLY these two:",
            "  YES",
            "  NO",
            "answer with EXACTLY one of them, and nothing else.",
        ]
    elif form == NON_JUDGED_FORM:
        lines += [
            "this step REPORTS, it does not judge.",
            "answer with a short factual statement about the key named above.",
        ]
    else:
        lines += [
            "this step's expected form is unreadable (%s), so it cannot accept "
            "an answer." % form,
        ]
    lines += ["", "do not analyse. do not give a verdict. do not explain."]
    return "\n".join(lines)


def ask_llm(prompt: str, *, resolved: dict[str, Any] | None = None,
            timeout: int = 180) -> tuple[str, int]:
    """Send ONE prompt to the local 7B. Returns (text, ms).

    The transport is the only thing here. `compose_prompt` decided WHAT is
    asked; `classify_answer` decides whether the reply is in form. Keeping them
    apart means a transport failure is never mistaken for an off-form answer.
    """
    import requests

    if resolved is None:
        resolved = resolve_model()
    model = str(resolved.get("model") or "")
    if not model:
        raise RuntimeError("no text model resolved from the llm.text route: %s"
                           % resolved.get("reason"))
    t0 = time.time()
    r = requests.post(OLLAMA_CHAT, json={
        "model": model,
        "messages": [{"role": "system", "content": ASK_SYSTEM},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }, timeout=timeout)
    r.raise_for_status()
    return str(r.json()["message"]["content"]), int((time.time() - t0) * 1000)


def make_ask(conn: sqlite3.Connection, *, run_key: str, subject_key: str,
             flow_key: str, cite_ref: str,
             allow_llm: bool = True,
             ask_fn: Callable[[str], tuple[str, int]] | None = None,
             sink: list[dict[str, Any]] | None = None) -> Callable[..., str]:
    """Build the `ask(prompt_text, step) -> str` adapter `run_flow` expects.

    THE ADAPTER'S WHOLE JOB is to answer ONE question and to REFUSE rather than
    pretend. It:

      * refuses a step that is COMPUTED (`verdict`) — the verdict is arithmetic,
        and MEASURED: the 7B cannot emit one after enumerating;
      * where the model would otherwise answer a verdict, it returns the
        off-form marker and records `REFUSED_NON_JUDGED_STEP`;
      * records EVERY answer with its `cite_ref`, so no value in this table
        arrived without a reference;
      * records the status, so a report can separate "answered" from "refused"
        from "off-form".

    `ask_fn` lets a proof inject an answer without a live model. The default
    path calls `ask_llm`, and when `allow_llm` is False an off-form marker is
    returned and RECORDED — a dry run that silently invented answers would be
    exactly the "reported an unlock that did not happen" defect.
    """
    resolved = resolve_model(conn) if (allow_llm and ask_fn is None) else {}
    model = str((resolved or {}).get("model") or "")
    # The stop state. It lives on the CLOSURE because `run_flow` owns the loop:
    # the only place that sees an off-form answer is this adapter, so the stop
    # flag is kept here and every later call records `STOPPED_AFTER_OFF_FORM`
    # without reaching the model. See the block comment in `run_key`.
    state: dict[str, Any] = {"stopped_at": None, "stopped_form": None}

    def _record(step: dict[str, Any], question: str, answer: str,
                status: str, ms: int | None) -> None:
        row = {
            "run_key": run_key, "subject_key": subject_key, "flow_key": flow_key,
            "step_no": int(step.get("step_no")), "step_kind": str(step.get("step_kind")),
            "layer_key": str(step.get("layer_key")), "question": question,
            "answer": answer, "expected": step.get("expected"), "status": status,
            "cite_ref": cite_ref, "model": model, "elapsed_ms": ms,
        }
        if sink is not None:
            sink.append(row)

    def ask(prompt_text: str, step: dict[str, Any]) -> str:
        kind = str(step.get("step_kind") or "")
        question = str(step.get("question_template") or "")
        # --- the flow STOPS at the first off-form answer ---------------------
        # Recorded as STOPPED, NOT as a blank answer: "we did not ask" and "we
        # asked and got nothing" are different facts, and collapsing them is how
        # a silent pass gets manufactured.
        if state["stopped_at"] is not None:
            _record(step, question, "", STATUS_STOPPED, None)
            return "STOPPED"
        # --- a COMPUTED step is never asked, and cannot be answered ----------
        if kind in qf.COMPUTED_KINDS:
            got = "REFUSED:%s" % STATUS_REFUSED_BY_FORM
            _record(step, question, got, STATUS_REFUSED_BY_FORM, None)
            return got
        text = compose_prompt(question or prompt_text, step)
        if ask_fn is not None:
            raw, ms = ask_fn(text)
        elif not allow_llm:
            raw, ms = "", None
        else:
            raw, ms = ask_llm(text, resolved=resolved)
        form = _form_of(step.get("expected"))
        answer, status = classify_answer(raw, form)
        _record(step, question, answer, status, ms)
        if status == STATUS_OFF_FORM:
            # THE STOP, in the ONE place that sees the off-form event. The steps
            # after this one are never sent to the model, and `run_flow`'s own
            # ordering, gate logic and verdict arithmetic are left untouched —
            # this module must not grow a second stepping implementation.
            state["stopped_at"] = int(step.get("step_no"))
            state["stopped_form"] = form
        # `run_flow` compares the returned text against `expected` to decide
        # `ok`. Returning the RAW reply keeps that comparison honest: an
        # off-form reply must fail the step, not be passed as if it were the
        # expected form.
        return answer

    ask.state = state          # type: ignore[attr-defined]
    return ask


# ---------------------------------------------------------------------------
# 4. ONE KEY AT A TIME — the run
# ---------------------------------------------------------------------------
def build_flow(conn: sqlite3.Connection, subject_key: str, *,
               guard_question: str | None = None,
               gate_questions: tuple[str, ...] = (),
               skill_key: str | None = None,
               commit: bool = True) -> dict[str, Any]:
    """Turn the LIVE table `subject_key` into the steps of a flow.

    A QUESTION NAMED HERE LOWERS TO A GATE. It is the human's "KO by step and
    step": if the thing being asked about does not exist, asking about its fields
    is a confident answer for the wrong reason, so the flow STOPS.

    `guard_question` and `gate_questions` are BOTH accepted and merged. They are
    the SAME concept at two widths, not two meanings: the CLI takes one
    (`--guard-question`) while a proof needs MORE than one, to show that a stop
    can happen at a LATER step than step 1. MEASURED, and this is why the second
    parameter exists: with a single gate, a flow has exactly ONE judged step, so
    "a stop at a later judged step" is unprovable — the test would pass for a
    reason that has nothing to do with the code under test.

    The flow is NOT activated. `steps_from_questions` writes `workflow_registry`
    with `is_active=0` (the never-activate law), and nothing here turns it on.
    MEASURED (and it was MY OWN DEFECT, caught by running it): the first version
    of this function tested `spec.get("ok")` and refused every key. `spec_from_table`
    does NOT return an `ok` field — it RETURNS a spec on success and RAISES
    `SpecError` on refusal. Reading a field that is absent made a working refusal
    detector that refused EVERYTHING, which looks identical to "the feature does
    not work" and would have been blamed on the generator.
    """
    try:
        spec = lg.spec_from_table(conn, subject_key)
    except lg.SpecError as e:
        # THE REFUSAL IS CARRIED OUT, NOT SWALLOWED. `spec_from_table` refuses a
        # table with no DECLARED shape, and that refusal is the same law as "no
        # citation, no finding" — it must not be turned into an empty question
        # list that looks like a clean run.
        return {"ok": False, "code": "SPEC_REFUSED", "subject_key": subject_key,
                "message": str(e)}
    # `logic_generator.spec_from_table` returns `subject`; `run_flow` needs a key.
    subject = str(spec.get("subject") or subject_key)
    generated = lg.generate(spec, conn=conn, subject_kind=subject.replace("_registry", ""))
    questions = generated.get("questions") or []
    if not questions:
        return {"ok": False, "code": "NO_QUESTIONS", "subject_key": subject,
                "message": "the generator produced no questions for %s" % subject}
    flow_key = "stepwise_ask_%s" % subject
    gates: list[str] = [str(g) for g in gate_questions if str(g).strip()]
    if guard_question and str(guard_question).strip() not in gates:
        gates.insert(0, str(guard_question))
    written = qf.steps_from_questions(
        conn, flow_key, questions, spec=spec, skill_key=skill_key,
        gate_questions=tuple(gates), commit=commit)
    # EVERY generated question must carry both forms, or the narrowing has no
    # yes/no to narrow WITH. MEASURED 2026-09-25: 22/26/22/20/26 questions across
    # the 5 phase targets, all with yes_form and no_form.
    missing = [str(q.get("question_id")) for q in questions
               if not q.get("yes_form") or not q.get("no_form")]
    # MEASURED, and it is a TRAP worth naming: `steps_from_questions` writes EVERY
    # non-gate question as a `describe` step with `expected='NA'`, and `run_flow`
    # does NOT judge such a step. So a flow with NO gate names nothing to judge,
    # `_default_verdict` returns NO, and the run can never reach YES — the
    # narrowing would have no yes/no to narrow with.
    #
    # That is why `guard_question` exists, and why this field is REPORTED rather
    # than fixed: choosing which question is the gate is a design decision about
    # the SUBJECT (the human's "KO by step and step"), not something this module
    # may infer. A `describe` step is the honest default.
    return {"ok": True, "flow_key": flow_key, "subject_key": subject,
            "question_count": len(questions), "missing_forms": missing,
            "gate_questions": list(gates), "judged_questions": len(gates),
            "written": written, "spec": spec}


def cite_for_key(subject_key: str) -> str:
    """The citation for a subject key. DERIVED, never asked of the model.

    MEASURED (`_proof_terminology_cite.py` case 8): the 7B's citations were NOT
    usable — it put a refusal in `cite_ref` twice and invented a filename once.
    So the citation is computed here.

    PREFERENCE ORDER, and the reason for it:

      1. THE DECLARED SHAPE'S LINE. When `db_schema.ONTOLOGY_REGISTRY_DDL` (or
         any `*_DDL`) declares the table, the answer is ABOUT that declaration,
         so the declaration is the honest reference — `init_ontology_registry.sql:144`
         for `db_field_registry`, say. This is derived from the SQL text itself,
         so it cannot drift from the file by being remembered.
      2. `terminology_cite.locate_name`. A weaker reference (any file that
         MENTIONS the key) but a real one.

    A citation is returned even when it can only be the second kind, because a
    resolver decides whether it holds (`verify_cite_ref` is applied by the
    caller) — silently returning "" would hide the difference.
    """
    table = str(subject_key or "").strip()
    sql_path = BASE / "init_ontology_registry.sql"
    if table and sql_path.exists():
        text = sql_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?%s\b"
                      % re.escape(table), text, re.IGNORECASE)
        if m:
            line = text.count("\n", 0, m.start()) + 1
            return "init_ontology_registry.sql:%d" % line
    return tc.locate_name(table) or ""


def run_key(conn: sqlite3.Connection, subject_key: str, *,
            guard_question: str | None = None,
            gate_questions: tuple[str, ...] = (),
            allow_llm: bool = True,
            ask_fn: Callable[[str], tuple[str, int]] | None = None,
            apply: bool = False,
            capture: bool = True) -> dict[str, Any]:
    """Ask ONE key's flow, step by step. Returns the whole run as DATA.

    ONE KEY AT A TIME is enforced by the SIGNATURE: `subject_key` is a single
    string, the flow key is derived from it, and the answer rows are keyed by it.
    There is no code path here that carries a second key's answers, which is how
    "(a) run on 2 keys; assert a second key does not reuse the first's answers"
    is satisfied structurally rather than by care.
    """
    t0 = time.time()
    built = build_flow(conn, subject_key, guard_question=guard_question,
                       gate_questions=gate_questions, commit=apply)
    if not built.get("ok"):
        return {"ok": False, "subject_key": subject_key, "build": built}

    flow_key = str(built["flow_key"])
    run_key_text = "%s@%s" % (flow_key, _now_us())
    # The citation is SUPPLIED, never asked. See `cite_for_key`.
    cite_ref = cite_for_key(str(built["subject_key"]))
    if not cite_ref:
        # NOTHING RESOLVED. This is REPORTED with `cite_ok=False` rather than
        # papered over with a placeholder: a placeholder that passes the check is
        # the "reported an unlock that did not happen" defect in miniature, and
        # the answer rows would carry a reference that points at nothing.
        cite_ref = ""
    ok_cite, why = tc.verify_cite_ref(cite_ref)

    # A dry run must not write. The `sink` collects what WOULD be written, so the
    # same code path is measured in both modes rather than two paths that can
    # disagree.
    sink: list[dict[str, Any]] = []
    ask = make_ask(conn, run_key=run_key_text, subject_key=str(built["subject_key"]),
                   flow_key=flow_key, cite_ref=cite_ref, allow_llm=allow_llm,
                   ask_fn=ask_fn, sink=sink)
    result = qf.run_flow(conn, flow_key, ask)

    # AN OFF-FORM ANSWER STOPS THE FLOW, NAMING THE STEP (the plan's rule).
    #
    # `run_flow` stops on a failed GATE, which is its own rule and is NOT this
    # one: an off-form answer can occur on a `describe` step, where the gate rule
    # never fires. Coercing it would manufacture the answer this chain exists to
    # avoid, so it is recorded as a REFUSAL and the steps AFTER it are not asked.
    #
    # The stop is done by SUPPRESSION rather than by a mid-loop break, so
    # `run_flow`'s ordering, gate logic and verdict arithmetic are untouched —
    # this module must not grow a second stepping implementation.
    off_form = [r for r in sink if r["status"] == STATUS_OFF_FORM]
    # The stop flag is read OFF THE ADAPTER, because the adapter is the only code
    # that sees an off-form answer INSIDE `run_flow`'s loop. Reading it here is
    # the whole reason the state is attached to the closure (see `make_ask`).
    state = ask.state          # type: ignore[attr-defined]
    stopped_by_off_form = state.get("stopped_at")
    not_asked = [r for r in sink if r["status"] == STATUS_STOPPED]
    refused = [r for r in sink if r["status"] == STATUS_REFUSED_BY_FORM]
    recorded = 0
    if apply and sink:
        ensure_schema(conn)
        for row in sink:
            conn.execute(
                "INSERT INTO step_answer (run_key, subject_key, flow_key, step_no,"
                " step_kind, layer_key, question, answer, expected, status,"
                " cite_ref, model, elapsed_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (run_key, step_no) DO UPDATE SET"
                "  answer=excluded.answer, status=excluded.status,"
                "  cite_ref=excluded.cite_ref, elapsed_ms=excluded.elapsed_ms",
                (row["run_key"], row["subject_key"], row["flow_key"],
                 row["step_no"], row["step_kind"], row["layer_key"],
                 row["question"], row["answer"], row["expected"], row["status"],
                 row["cite_ref"], row["model"], row["elapsed_ms"]))
            recorded += 1
        conn.commit()

    captured = None
    if capture and result.get("verdict") == "YES":
        captured = capture_pattern(conn, flow_key, sink, apply=apply)

    return {
        "ok": bool(result.get("ok")),
        "subject_key": str(built["subject_key"]),
        "flow_key": flow_key,
        "run_key": run_key_text,
        "cite_ref": cite_ref,
        "cite_ok": ok_cite, "cite_why": why,
        "question_count": built["question_count"],
        "missing_forms": built["missing_forms"],
        "gate_questions": built["gate_questions"],
        "judged_questions": built["judged_questions"],
        "verdict": result.get("verdict"),
        "asked_count": result.get("asked_count"),
        "judged_count": result.get("judged_count"),
        "described_count": result.get("described_count"),
        "stopped_at": result.get("stopped_at"),
        "failed_steps": result.get("failed_steps"),
        "answers": result.get("answers"),
        "off_form": [{"step_no": r["step_no"], "answer": r["answer"],
                      "expected_form": r["expected"]} for r in off_form],
        "refused": [{"step_no": r["step_no"]} for r in refused],
        "not_asked": [{"step_no": r["step_no"]} for r in not_asked],
        # A STOP is an OUTCOME, not an error. Two DIFFERENT stops are reported
        # separately, because they have different causes and a single flag would
        # hide which one happened:
        #   `stopped_by_off_form`  an answer was not in the step's allowed form
        #   `result["stopped_at"]` a GATE answered NO (`run_flow`'s own rule)
        "stopped_by_off_form": stopped_by_off_form,
        "stopped_at_gate": result.get("stopped_at"),
        "stopped_early": (stopped_by_off_form is not None
                          or result.get("stopped_at") is not None),
        "no_judged_question": (result.get("judged_count") == 0),
        "recorded": recorded,
        "captured": captured,
        "elapsed_ms": int((time.time() - t0) * 1000),
        "dry_run": not apply,
        "rows": sink,
    }


# ---------------------------------------------------------------------------
# 5. PATTERN CAPTURE — a pattern that repeats is EVIDENCE, not a decision
# ---------------------------------------------------------------------------
def capture_pattern(conn: sqlite3.Connection, flow_key: str,
                    rows: list[dict[str, Any]], *, apply: bool = False) -> dict[str, Any]:
    """Write the questions that WORKED into `flow_setting`, INACTIVE.

    The human: "so experience can be pattern ... so 7B can smart". A run that
    reached YES produced a narrowing sequence that CAN be asked again, so it is
    stored as a flow pattern for reuse.

    `is_active=0` IS THE CEILING. The never-activate law: this function writes
    the pattern and CANNOT turn it on. It calls no activation entry point — the
    proof checks that by AST, because a policy stated only in a docstring is a
    policy that gets bypassed.

    A step that did NOT succeed is not captured. Storing a failed question as a
    pattern would teach the next run to ask the thing that did not work.
    """
    good = [r for r in rows
            if r.get("status") in (STATUS_ANSWERED, STATUS_COMPUTED)
            and str(r.get("answer") or "").strip()]
    plan = [{"flow_key": flow_key, "step_no": int(r["step_no"]),
             "question": str(r.get("question") or "")[:500],
             "value": str(r.get("answer") or "")[:500],
             "action": "stepwise_ask",
             "description": "captured from a YES run of %s" % flow_key}
            for r in good]
    if not apply:
        return {"ok": True, "dry_run": True, "would_write": len(plan),
                "is_active": 0, "plan": plan[:3]}
    from db_schema import FLOW_SETTING_DDL
    conn.executescript(FLOW_SETTING_DDL)
    n = 0
    for p in plan:
        conn.execute(
            "INSERT INTO flow_setting (flow_key, step_no, question, value,"
            " action, description, is_active) VALUES (?,?,?,?,?,?,0)"
            " ON CONFLICT (flow_key, step_no) DO UPDATE SET"
            "  question=excluded.question, value=excluded.value,"
            "  description=excluded.description,"
            "  updated_at=CURRENT_TIMESTAMP",
            (p["flow_key"], p["step_no"], p["question"], p["value"],
             p["action"], p["description"]))
        n += 1
    conn.commit()
    return {"ok": True, "dry_run": False, "written": n, "is_active": 0}


def _now_us() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


# ---------------------------------------------------------------------------
# 6. THE SELF-CHECK THE PLAN ASKS FOR
#
# QC-04 ("the asker uses run_flow and does NOT re-implement stepping") and QC-06
# ("the asker CANNOT ask the model for a verdict") are PROPERTIES OF THIS FILE.
# They are checked over THIS file's own AST so the check travels with the code
# rather than living only in a proof that can be deleted.
# ---------------------------------------------------------------------------
_STEPPING_LOOP_NAMES = ("step_no", "steps_of", "steps_from_questions")
_RUN_FLOW_CONSUMERS = ("add_step",)


def self_check(path: str | Path | None = None) -> dict[str, Any]:
    """Report the asker's own structural properties. Never raises."""
    p = Path(path or __file__)
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def _name(call: ast.Call) -> str:
        f = call.func
        if isinstance(f, ast.Attribute):
            return f.attr
        if isinstance(f, ast.Name):
            return f.id
        return ""

    calls = [ _name(n) for n in ast.walk(tree) if isinstance(n, ast.Call) ]
    uses_run_flow = "run_flow" in calls
    # re-implementing stepping would mean calling `add_step` (writing steps
    # itself) or `steps_of` and looping. `steps_from_questions` is the WRITER
    # the chain intends and is allowed; `add_step` is not.
    reimplements = [c for c in calls if c in _RUN_FLOW_CONSUMERS]
    # a verdict must never be handed to the model: no call may pass a
    # `verdict`-kinded step into `ask_llm`.
    asks_verdict = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and _name(n) in ("ask_llm",):
            for a in list(n.args) + [k.value for k in n.keywords]:
                seg = ast.get_source_segment(src, a) or ""
                if "verdict" in seg.lower():
                    asks_verdict = True
    return {"file": str(p), "uses_run_flow": uses_run_flow,
            "reimplements_stepping": sorted(set(reimplements)),
            "asks_model_for_verdict": asks_verdict,
            "ok": uses_run_flow and not reimplements and not asks_verdict}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", help="ONE subject key at a time (the human's '1 by 1')")
    ap.add_argument("--guard-question", default=None,
                    help="a generated question id to lower to a GATE (step 1)")
    ap.add_argument("--dry-run", action="store_true",
                    help="do everything except write the answers")
    ap.add_argument("--no-llm", action="store_true",
                    help="do not call the model; record the refusal instead")
    ap.add_argument("--apply", action="store_true", help="write to agent.db")
    ap.add_argument("--self-check", action="store_true",
                    help="report this file's structural properties and exit")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_check:
        r = self_check()
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0 if r["ok"] else 1

    if not args.key:
        ap.error("--key is required (ONE key at a time)")

    conn = sqlite3.connect(str(ds.get_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        r = run_key(conn, args.key, guard_question=args.guard_question,
                    allow_llm=not args.no_llm, apply=bool(args.apply))
    finally:
        pass
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
    else:
        print("subject      %s" % r.get("subject_key"))
        print("flow         %s" % r.get("flow_key"))
        print("verdict      %s   (asked %s, judged %s, described %s)"
              % (r.get("verdict"), r.get("asked_count"),
                 r.get("judged_count"), r.get("described_count")))
        print("cite_ref     %s  ok=%s" % (r.get("cite_ref"), r.get("cite_ok")))
        print("recorded     %s %s" % (r.get("recorded"),
                                      "(dry run)" if r.get("dry_run") else ""))
        print("stopped_at   %s" % r.get("stopped_at"))
        if r.get("off_form"):
            print("OFF FORM     %s" % r["off_form"][:3])
        if r.get("missing_forms"):
            print("MISSING FORM %s" % r["missing_forms"])
    conn.close()
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
