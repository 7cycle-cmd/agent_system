# -*- coding: utf-8 -*-
"""problem_intake.py — the CALLER for problem_statement.

WHY THIS FILE EXISTS
--------------------
`problem_statement.py` owns the RULES, but a rule with no caller changes
nothing — the defect `systematic_debug.py` documents ("a rule in a table is not
a gate"). The report's §3 (A2) named the gap and §2 recorded the real failure:

    「can run real case now?」  ← 分析呢句 = 分析一個冇人講過嘅問題

So this module is the intake point: it takes a raw request, classifies it, and
REFUSES to hand an unstated problem to analysis. The rules are IMPORTED from
`problem_statement`, never re-stated here.

Read-only: no DB, no network. It decides and reports.
"""
from __future__ import annotations

from typing import Any

import problem_statement as ps

# The intake verdicts. `statement` is the only one that may proceed.
STATEMENT = "statement"
COMPLAINT = "complaint"
QUESTION = "question"
EMPTY = "empty"

PROCEED = (STATEMENT,)


def classify_request(text: str) -> str:
    """Classify a raw request into one of the four intake verdicts.

    Order matters: empty first, then question, then complaint, then statement.
    A question is checked before a complaint because "why is it broken?" carries
    a complaint marker but is fundamentally a request for information.
    """
    s = str(text or "").strip()
    if not s:
        return EMPTY
    if ps.is_question(s):
        return QUESTION
    if ps.is_complaint(s):
        return COMPLAINT
    return STATEMENT


def intake(text: str, statement: dict | None = None) -> dict:
    """The intake gate. Returns a verdict dict; never raises.

    `text` is the raw request. `statement` is the structured four-part form, if
    the caller already has one. A request may be a STATEMENT in prose yet still
    lack the four parts — both checks must pass before analysis is allowed.
    """
    verdict = classify_request(text)
    out: dict[str, Any] = {
        "verdict": verdict,
        "may_analyse": False,
        "question": None,
        "missing_parts": [],
        "reason": "",
    }

    if verdict == EMPTY:
        out["reason"] = "empty request — nothing to analyse"
        out["question"] = "What is the problem?"
        return out

    if verdict == QUESTION:
        out["reason"] = (
            "a question asks for information; it is not a problem statement. "
            "Answer it, or ask the asker to state the problem."
        )
        out["question"] = "What is the problem you want fixed?"
        return out

    if verdict == COMPLAINT:
        out["reason"] = (
            "a complaint is an evaluation, not a problem statement. Analysing "
            "it produces a fix for a problem nobody stated."
        )
        out["question"] = ps.intake_question(statement or {})
        return out

    # verdict == STATEMENT: prose is a statement, but the four parts must exist.
    missing = ps.missing_parts(statement)
    out["missing_parts"] = missing
    if missing:
        out["reason"] = "statement is incomplete: missing %s" % ", ".join(missing)
        out["question"] = ps.intake_question(statement)
        return out
    if not ps.is_observable(statement):
        out["reason"] = (
            "statement has no observable — a third party could not check it"
        )
        out["question"] = ps.intake_question(statement)
        return out

    out["may_analyse"] = True
    out["reason"] = "problem is stated and observable — analysis may proceed"
    return out


def assert_may_analyse(text: str, statement: dict | None = None) -> dict:
    """Gate: raise ProblemNotStated unless the request may be analysed.

    This is the call site the report said was missing. Call it at the START of
    any analysis/debug/fix flow.
    """
    res = intake(text, statement)
    if not res["may_analyse"]:
        raise ps.ProblemNotStated(
            [res["reason"]] + ([res["question"]] if res["question"] else []),
            statement,
        )
    return res


def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Problem intake gate")
    ap.add_argument("--text", required=True, help="the raw request")
    ap.add_argument("--target")
    ap.add_argument("--expected")
    ap.add_argument("--actual")
    ap.add_argument("--observable")
    args = ap.parse_args(argv)

    statement = None
    if any([args.target, args.expected, args.actual, args.observable]):
        statement = {
            "target": args.target, "expected": args.expected,
            "actual": args.actual, "observable": args.observable,
        }
    res = intake(args.text, statement)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0 if res["may_analyse"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
