# -*- coding: utf-8 -*-
"""problem_statement.py — the EXECUTABLE form of the problem-statement rule.

WHY THIS FILE EXISTS
--------------------
`docs/report_skill_set_for_finding_problems_zh.md` §3 (A2) names the gap:

    「『太慢』係抱怨，唔係問題陳述。」

A complaint ("can run real case now?", "it's too slow", "the rect is wrong")
cannot be analysed, because it names no target, no expected value, and no
observable. Analysing a complaint produces a fix for a problem nobody stated —
which is exactly how the 2026-09-20 rect failure began.

The rule is only real if it BLOCKS. A rule in a markdown table is prose; the
same defect class `systematic_debug.py` was written to fix. So the rule lives
here as pure functions, and the gate RAISES at the point of action.

Pure: no I/O, no DB, no network. Callers supply the state.
"""
from __future__ import annotations

# The four parts a statement must carry to be analysable.
REQUIRED_PARTS = ("target", "expected", "actual", "observable")

# Words that signal a complaint rather than a statement. A statement containing
# one of these AND no observable is a complaint.
COMPLAINT_MARKERS = (
    "too slow", "slow", "broken", "doesn't work", "does not work", "not working",
    "wrong", "bad", "weird", "strange", "messy", "ugly", "confusing",
    "太慢", "慢", "壞", "唔得", "唔 work", "錯", "有問題", "奇怪",
)

# A statement is observable when it names something a third party could check
# without asking the author what they meant.
OBSERVABLE_MARKERS = (
    "px", "pixel", "ms", "seconds", "sec", "line", "row", "column", "file",
    "path", "returns", "equals", "==", ">=", "<=", "count", "sha256", "exit code",
    "像素", "毫秒", "秒", "行", "欄", "檔案", "路徑", "回傳", "等於", "數量",
)


def missing_parts(statement: dict | None) -> list[str]:
    """Which of the four required parts are absent or blank.

    Returns a list of part names, so a refusal says WHAT was missing rather
    than just "refused".
    """
    if not isinstance(statement, dict):
        return list(REQUIRED_PARTS)
    out = []
    for part in REQUIRED_PARTS:
        v = statement.get(part)
        if v is None or not str(v).strip():
            out.append(part)
    return out


def is_observable(statement: dict | None) -> bool:
    """True when the statement names something a third party could verify.

    Two ways to qualify:
      1. an explicit `observable` field that is non-blank, or
      2. the `actual`/`expected` text contains a measurable marker.
    """
    if not isinstance(statement, dict):
        return False
    if str(statement.get("observable") or "").strip():
        return True
    blob = " ".join(
        str(statement.get(k) or "") for k in ("expected", "actual")
    ).lower()
    return any(m in blob for m in OBSERVABLE_MARKERS)


def is_question(text: str) -> bool:
    """True when `text` is a question rather than a statement.

    A question is a distinct non-statement category: it asks for information
    instead of asserting a problem. The report's own example — "can run real
    case now?" — is a question, and analysing it would answer a question nobody
    asked. Detected by a trailing '?' or a leading interrogative.
    """
    s = str(text or "").strip()
    if not s:
        return False
    if s.endswith("?") or s.endswith("？"):
        return True
    low = s.lower()
    return any(low.startswith(q) for q in (
        "can ", "could ", "is ", "are ", "does ", "do ", "why ", "how ",
        "what ", "when ", "where ", "should ", "will ", "would ",
        "可唔可以", "係唔係", "點解", "點樣", "乜嘢", "幾時",
    ))


def is_complaint(text: str) -> bool:
    """True when `text` reads as a complaint rather than a problem statement.

    A complaint is a bare evaluative word with no measurable content. The test
    is deliberately conservative: a complaint marker alone is NOT enough — the
    text must also lack any observable marker. "the rect is 315px off" contains
    "off" but is measurable, so it is a statement, not a complaint.

    A bare question is also not a statement, so it counts as a complaint here
    (both are "not a problem statement"). Use `is_question` to tell them apart.
    """
    s = str(text or "").strip().lower()
    if not s:
        return True
    if is_question(s):
        return True
    has_marker = any(m in s for m in COMPLAINT_MARKERS)
    has_observable = any(m in s for m in OBSERVABLE_MARKERS)
    return has_marker and not has_observable


def is_analysable(statement: dict | None) -> bool:
    """True when the statement may be analysed: complete AND observable."""
    return not missing_parts(statement) and is_observable(statement)


# ---------------------------------------------------------------------------
# runtime gate: refuse to analyse an unstated problem
# ---------------------------------------------------------------------------

class ProblemNotStated(RuntimeError):
    """Raised instead of analysing a complaint."""

    def __init__(self, reasons: list[str], statement: dict | None = None):
        self.reasons = list(reasons)
        self.statement = statement
        super().__init__(
            "problem not stated — refusing to analyse: %s"
            % ("; ".join(self.reasons) or "no reason given")
        )


def assert_problem_stated(statement: dict | None) -> dict:
    """Gate at the point of action: refuse to analyse an unstated problem.

    Returns the statement when it is analysable; raises ProblemNotStated when
    it is not. The refusal names every missing part, so the caller knows what
    to supply rather than being told "no".
    """
    reasons: list[str] = []
    missing = missing_parts(statement)
    if missing:
        reasons.append("missing part(s): %s" % ", ".join(missing))
    if not is_observable(statement):
        reasons.append(
            "not observable — no measurable value (px / ms / line / count / "
            "exit code); a third party could not check this"
        )
    if reasons:
        raise ProblemNotStated(reasons, statement)
    return statement


def intake_question(statement: dict | None) -> str | None:
    """The ONE question to ask when a statement is incomplete.

    Returns None when the statement is analysable. Otherwise returns a single
    question naming the first missing part — one question at a time, per the
    research gate's rule 7.
    """
    missing = missing_parts(statement)
    if missing:
        return "What is the %s?" % missing[0]
    if not is_observable(statement):
        return "How would a third party observe this (what number, line, or exit code)?"
    return None
