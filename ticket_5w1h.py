# -*- coding: utf-8 -*-
"""ticket_5w1h.py — the 5W1H MIDDLEWARE for a ticket's subject.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "no, by ticket!! chat ticket, workflow ticket, task ticket and entity ticket
     so we can have middleware for 5W1H, not hardcode for rubbish"

A ticket carries a SUBJECT (`ticket_subject`), and the subject has a KIND. The
six 5W1H questions are asked ABOUT that subject, and what each dimension MEANS
depends on the kind:

    dimension   chat                        task
    ---------   -------------------------   ---------------------------
    where       the chat's session id       the task's file path + line
    when        when the chat was opened    the trigger point
    what        which chat, which session   which job must exist
    how         the verify command          the function that implements it

THE WORDING IS DATA, NOT CODE
-----------------------------
`dimension_binding_registry` already holds "what this dimension MEANS for this
kind" (`dimension_binding_registry.py:1-40`), and `bindings_for()` already
returns it (`:248-262`). This module READS that table. It does NOT contain a
question list.

WHY THAT MATTERS (measured, not preference)
-------------------------------------------
`skill_5w1h.py:44-76` declares the six dimensions ONCE, and its own "Not to do"
forbids a second copy. A hardcoded question list here would be exactly that
second copy — and the repo has already measured what drift costs (46 `.skill.md`
files vs 28 ssot rows: 12 disagreed, 20 had no row at all).

So:
  * the SIX NAMES are imported from `skill_5w1h.DIMENSION_NAMES`
  * the WORDING is read from `dimension_binding_registry`
  * the HARD RULE is read from `skill_5w1h.DIMENSIONS`
  * a kind with NO binding is REPORTED as unbound, never silently given a
    generic question (a generic question is a hardcode wearing a disguise)

CLI
---
    python ticket_5w1h.py --questions chat
    python ticket_5w1h.py --answer-set 5
    python ticket_5w1h.py --coverage
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _dimensions() -> tuple[tuple[str, str, str, int], ...]:
    """The six dimensions, IMPORTED from the one place they are declared.

    A second copy here is the drift `skill_5w1h`'s own "Not to do" forbids.
    """
    import skill_5w1h as fw

    return fw.DIMENSIONS


def questions_for(conn: sqlite3.Connection, subject_kind: str) -> dict[str, Any]:
    """The six questions for ONE subject kind, with wording from the DB.

    Returns `{ok, subject_kind, questions: [{dimension, question, hard_rule,
    binding_text, example, bound}], unbound: [...]}`.

    A dimension with NO binding for this kind is returned with `bound: False`
    and NO question text — it is REPORTED, never filled in with a generic
    question. A generic question would be a hardcode wearing a disguise, and it
    would hide the fact that the kind is not yet described.
    """
    import dimension_binding_registry as dbr
    import subject_kind_registry as skr

    kind = str(subject_kind or "").strip()
    if not kind:
        return {"ok": False, "code": "MISSING_SUBJECT_KIND",
                "message": "subject_kind is required"}

    # The kind must be a REGISTERED kind. An unregistered kind has no bindings
    # and would return six unbound rows that read as "nothing to ask".
    v = skr.validate_kind(conn, kind)
    if not v.get("ok"):
        return {"ok": False, "code": "UNKNOWN_SUBJECT_KIND",
                "message": v.get("message")}

    bindings = dbr.bindings_for(conn, kind)
    examples = _examples_for(conn, kind)

    # THE GATE IS READ FIRST (T1, 2026-09-27). `derive_5w1h.fields` is the ONLY
    # reader that filters `is_active`, so a gate-backed dimension is one the
    # activation gate DECIDED. `bindings_for` is the FALLBACK where the gate
    # REFUSES — MEASURED: the gate REFUSES a kind with ANY unbound dimension
    # (`MISSING_BINDING`), while this function reports that dimension as
    # `bound: False` and keeps going. A gate-only rule would turn a
    # per-dimension report into a whole-kind refusal.
    gate: dict[str, dict[str, Any]] = {}
    try:
        import derive_5w1h as _d5
        g = _d5.fields(conn, kind)
        if g.get("ok"):
            for f in (g.get("fields") or []):
                gate[str(f["field_name"])] = f
    except Exception:
        gate = {}

    questions: list[dict[str, Any]] = []
    unbound: list[str] = []
    for name, question, hard_rule, mandatory in _dimensions():
        gf = gate.get(name)
        if gf is not None:
            # THE GATE DECIDED THIS DIMENSION.
            questions.append({
                "dimension": name,
                "bound": True,
                "question": str(gf.get("question") or question),
                "hard_rule": str(gf.get("hard_rule") or hard_rule),
                "binding_text": str(gf.get("binding_text") or ""),
                "example": str(gf.get("example") or ""),
                "mandatory": bool(gf.get("mandatory", mandatory)),
                "source": "gate",
                "cite_ref": str(gf.get("cite_ref") or ""),
            })
            continue
        text = str(bindings.get(name) or "").strip()
        if not text:
            unbound.append(name)
            questions.append({
                "dimension": name,
                "bound": False,
                "question": "",
                "hard_rule": hard_rule,
                "binding_text": "",
                "example": "",
                "mandatory": bool(mandatory),
                "source": "unbound",
            })
            continue
        questions.append({
            "dimension": name,
            "bound": True,
            "question": question,
            "hard_rule": hard_rule,
            "binding_text": text,
            "example": str(examples.get(name) or ""),
            "mandatory": bool(mandatory),
            "source": "register",
        })

    return {"ok": True, "subject_kind": kind, "display_name": v.get("display_name"),
            "questions": questions, "unbound": unbound,
            "bound_count": len(questions) - len(unbound),
            "by_source": {s: sum(1 for q in questions if q.get("source") == s)
                          for s in ("gate", "register", "unbound")}}


def _examples_for(conn: sqlite3.Connection, kind: str) -> dict[str, str]:
    try:
        rows = conn.execute(
            "SELECT dimension_key, example FROM dimension_binding_registry "
            "WHERE subject_kind=?", (kind,)).fetchall()
        return {str(r["dimension_key"]): str(r["example"] or "") for r in rows}
    except sqlite3.Error:
        return {}


def answer_set(conn: sqlite3.Connection, ticket_id: int) -> dict[str, Any]:
    """The 5W1H question set for a ticket, per SUBJECT it carries.

    A ticket may carry several subjects of different kinds (a chat ticket, a
    task ticket), so the answer set is returned PER SUBJECT — one flat list
    would silently mix two kinds' meanings under one dimension name.
    """
    import ticket_subject as ts

    subjects = ts.subjects_for_ticket(conn, int(ticket_id))
    out: list[dict[str, Any]] = []
    for s in subjects:
        q = questions_for(conn, str(s["subject_kind"]))
        out.append({
            "subject_kind": s["subject_kind"],
            "subject_ref_id": s["subject_ref_id"],
            "role": s["role"],
            "questions": q.get("questions", []),
            "unbound": q.get("unbound", []),
            "ok": bool(q.get("ok")),
        })
    return {"ok": True, "ticket_id": int(ticket_id), "subjects": out,
            "subject_count": len(out)}


def activate_bindings(conn: sqlite3.Connection, *, apply: bool = False
                      ) -> dict[str, Any]:
    """Activate the 5W1H bindings for COMPLETE kinds, THROUGH THE ONE GATE.

    THE HUMAN (2026-09-27): "we need to have perparetor data by table
    stantardize (5W1H, will logic generation can help?)".

    MEASURED before this: `dimension_binding_registry` has 19 subject kinds x 6
    dimensions = 114 rows, and `SUM(is_active)` = 0 for EVERY kind. The bindings
    exist and carry a `cite_ref`, but none is active.

    IT DOES NOT FLIP `is_active` ITSELF. MEASURED: `activate_binding` is THE ONE
    writer of `dimension_binding_registry.is_active = 1`, and it DELEGATES to
    `activation_gate.assert_may_activate`, which requires a PROOF RUN (a streak
    AND a 2-part verdict). A second writer that set `is_active=1` from a cite
    alone would be exactly the defect `dimension_binding_registry.py:1213-1255`
    records: "a gate with no key" replaced by "a key that skips the gate".

    SO THIS FUNCTION DELEGATES, and REPORTS what the gate says. MEASURED: only
    **1 of 114** bindings has a proof run (`dimension_binding.api.what`,
    streak=10), and even that one is REFUSED because its 2-part verdict has no
    `register_approve` row. So the honest result is: 0 activated, 114 refused,
    each with the gate's own reason. That is the finding, not a failure to hide.

    A PARTIAL kind is skipped and REPORTED: a kind with 3 of 6 bindings would
    otherwise look complete.
    """
    import dimension_binding_registry as dbr

    cov = coverage(conn)
    complete = list(cov.get("complete") or [])
    partial = dict(cov.get("partial") or {})
    activated: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for kind in complete:
        for name, _q, _rule, _mand in _dimensions():
            row = conn.execute(
                "SELECT binding_id, cite_ref, is_active FROM "
                "dimension_binding_registry WHERE subject_kind=? AND "
                "dimension_key=?", (kind, name)).fetchone()
            if not row:
                refused.append({"subject_kind": kind, "dimension_key": name,
                                "code": "NO_BINDING"})
                continue
            if int(row["is_active"]) == 1:
                activated.append({"subject_kind": kind, "dimension_key": name,
                                  "code": "ALREADY_ACTIVE"})
                continue
            if not apply:
                refused.append({"subject_kind": kind, "dimension_key": name,
                                "code": "REPORT_ONLY",
                                "cite_ref": str(row["cite_ref"] or "")})
                continue
            # THE ONE GATE. Delegated, so "proven" has ONE definition.
            res = dbr.activate_binding(
                conn, kind, name, cite_ref=str(row["cite_ref"] or ""))
            if res.get("ok"):
                activated.append({"subject_kind": kind, "dimension_key": name,
                                  "code": res.get("code")})
            else:
                refused.append({"subject_kind": kind, "dimension_key": name,
                                "code": res.get("code"),
                                "reason": str(res.get("message") or "")[:120]})
    return {"ok": True, "applied": bool(apply),
            "complete_kinds": len(complete), "partial_kinds": len(partial),
            "activated": len(activated), "refused": len(refused),
            "activated_rows": activated, "refused_rows": refused,
            "partial": partial,
            "cite": ("measured: dimension_binding_registry is_active, "
                     "activation_gate.assert_may_activate")}


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which registered kinds have a COMPLETE six-dimension binding set.

    Reported so an incomplete kind is VISIBLE. A kind with 3 of 6 bindings would
    otherwise produce a question set that looks complete and is not.
    """
    import subject_kind_registry as skr

    kinds = [r["kind_key"] for r in skr.list_kinds(conn)]
    complete: list[str] = []
    partial: dict[str, list[str]] = {}
    for k in kinds:
        q = questions_for(conn, k)
        if not q.get("ok"):
            partial[k] = ["<unknown kind>"]
            continue
        if q.get("unbound"):
            partial[k] = list(q["unbound"])
        else:
            complete.append(k)
    return {"ok": True, "complete": complete, "partial": partial,
            "complete_count": len(complete), "partial_count": len(partial)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", default="")
    ap.add_argument("--answer-set", type=int, default=0)
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--activate", action="store_true",
                    help="activate the COMPLETE kinds' bindings through the "
                         "ONE gate (activation_gate); REPORT the refusals")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.questions:
            r = questions_for(conn, args.questions)
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0 if r.get("ok") else 1
        if args.answer_set:
            print(json.dumps(answer_set(conn, args.answer_set),
                             ensure_ascii=False, indent=2))
            return 0
        if args.coverage:
            print(json.dumps(coverage(conn), ensure_ascii=False, indent=2))
            return 0
        if args.activate:
            r = activate_bindings(conn, apply=True)
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
