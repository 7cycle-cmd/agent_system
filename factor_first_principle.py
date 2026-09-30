# -*- coding: utf-8 -*-
"""factor_first_principle.py — a factor is DERIVED from first principles, and its
output must be auditable by a MEASURED UNIT.

THE RULE (from the human)
-------------------------
"First Principle to define factor and output must can audit by measured unit"

Two requirements, and they are different:

  1. FIRST PRINCIPLE — a factor is DERIVED, not copied. "Everyone does it this
     way" is not a reason. The derivation must answer: what must be true for the
     output to be trustworthy AT ALL? A factor that cannot answer that is a
     preference, and a preference cannot be scored.

  2. MEASURED UNIT — the output must be auditable by a unit. A metric with no
     unit is not a measurement: `count` of WHAT, `pct` of WHAT, `score` on WHAT
     SCALE. Measured 2026-09-21: the 19 factors carry a `metric_kind` and a
     `metric_target`, but NO unit — so "count target 0" does not say whether it
     counts vulnerabilities, rows, or attempts.

WHY A UNIT IS THE WHOLE POINT
-----------------------------
A number without a unit cannot be audited. "0" is a pass for a vulnerability
count and a FAIL for a test pass rate. The unit is what makes the number
checkable by a third party — the same requirement `citation_discipline` makes of
a finding: no unit, no measurement.

THE FOUR UNITS, AND WHAT EACH ONE MEANS
---------------------------------------
    boolean   a state that is true or false, with the CONDITION named
    count     a number of THINGS, with the thing named
    pct       a ratio of two counts, with BOTH named
    score     a bounded scale, with the SCALE named

A unit is REFUSED when it does not name its subject. `count` alone is refused;
`count of critical vulnerabilities` is accepted.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The unit kinds. Same four as `skill_factor.METRIC_KINDS`, because a metric kind
# IS a unit kind — the defect was that the SUBJECT was missing, not the kind.
UNIT_BOOLEAN = "boolean"
UNIT_COUNT = "count"
UNIT_PCT = "pct"
UNIT_SCORE = "score_0_100"
UNIT_KINDS = (UNIT_BOOLEAN, UNIT_COUNT, UNIT_PCT, UNIT_SCORE)

# A unit must NAME its subject. These words name nothing.
EMPTY_SUBJECTS = ("", "value", "count", "number", "amount", "total", "n",
                  "result", "score", "pct", "percent", "ratio", "boolean",
                  "true/false", "yes/no", "n/a", "na", "none", "tbd", "it",
                  "this", "that", "thing", "things")

# A subject must be at least this long, so "x" is refused.
MIN_SUBJECT_CHARS = 4

# ---------------------------------------------------------------------------
# THE FIRST-PRINCIPLE QUESTIONS.
#
# A factor is DERIVED by answering these IN ORDER. The first is the one that
# makes the rest meaningful: if the output cannot be wrong in a way that matters,
# there is nothing to measure.
#
# THE THIRD ELEMENT IS THE 5W1H DIMENSION (2026-09-22).
# ----------------------------------------------------
# The user: "how upgrade to 5W1H / or can be data -> 5w1H -> first_principle ->
# factor / or data -> 5w1H -> factor -> first_principle / actual first_principle
# is very similar concpet for distrill".
#
# MEASURED before this change: `grep "skill_5w1h|5W1H" factor_first_principle.py`
# returned ZERO matches. The two modules were DISCONNECTED, so this file declared
# a SECOND question vocabulary — the drift this repo keeps hitting.
#
# The five questions are NOT a second vocabulary: they are a SUBSET of 5W1H.
# Each one now DECLARES which dimension it comes from, so the relationship is
# CHECKABLE rather than asserted:
#
#     failure_mode  -> why    (what breaks without it)
#     observable    -> what   (what a third party can see)
#     unit          -> what   (what is being measured)
#     threshold     -> how    (the checkable rule)
#     independence  -> who    (which factor OWNS this boundary)
#
# The five stay FIVE. `_proof_factor_first_principle.py:145` asserts exactly
# five, and expanding to six would break it — and would also make the name
# "first principle" mean something it does not.
#
# WHAT IS NOT COVERED, and why that matters: `when` and `where` have NO
# first-principle question. `uncovered_dimensions()` reports them, so the gap is
# VISIBLE rather than assumed.
#
# **THE FIVE ARE FIVE, AND THAT IS AN INVARIANT.** MEASURED 2026-09-28: I first
# closed this gap by ADDING a 6th and 7th question to THIS tuple, and
# `_proof_factor_first_principle.py` went 8 RED, including:
#
#     FAIL  the five questions stay FIVE (the existing proof still holds)
#           [expanding to six would break the name 'first principle']
#     FAIL  `uncovered_dimensions()` reports `when` and `where`   [()]
#
# Those assertions are not stale fixtures — they DECLARE the concept. The five
# questions are what makes a factor a FIRST PRINCIPLE; a longer list is a
# different thing wearing the same name, which is the defect the
# `terminology-register` skill refuses. So this tuple is restored UNCHANGED, and
# the `when`/`where` gap stays REPORTED here.
#
# THE GAP IS CLOSED ELSEWHERE, AND THAT IS THE RIGHT HOME: a BUILD STEP needs its
# order (`when`) and its home (`where`), so its questions belong to the step
# register, not to the first-principle set. See `build_step_registry.STEP_QUESTIONS`
# (SCOPE A of plan_GITHUB.CONSULTANT.UPGRADE), which covers all six dimensions
# and leaves THIS set — and its honest `('when','where')` report — untouched.
#
# The `who` gap is the one that already cost us: `valid_phone_number_judgment`
# (factor_id 38) and `phone_validity_judgment` (factor_id 39) are duplicates,
# both with `skill_key = NULL` and `applies_to = '*'` — NEITHER owns anything,
# which is exactly why the duplicate was invisible.
# ---------------------------------------------------------------------------
FIRST_PRINCIPLE_QUESTIONS = (
    ("failure_mode",
     "What must be true for this output to be TRUSTWORTHY at all? Name the "
     "specific way it could be wrong that would matter.",
     "why"),
    ("observable",
     "What can a THIRD PARTY observe, without asking the author, that "
     "distinguishes the trustworthy case from the failure mode?",
     "what"),
    ("unit",
     "In what UNIT is that observation expressed? Name the subject: a count of "
     "WHAT, a ratio of WHICH two counts, a score on WHICH scale.",
     "what"),
    ("threshold",
     "What value of that unit separates pass from fail, and WHY that value "
     "rather than another?",
     "how"),
    ("independence",
     "Can this factor pass while another factor fails? If not, it is not a "
     "separate factor — it is part of the other one.",
     "who"),
)

# The 5W1H dimensions, IMPORTED from the ONE place they are declared. A second
# copy here is the drift this mapping exists to remove.
import skill_5w1h as fw  # noqa: E402


def dimension_of(question_key: str) -> str:
    """Which 5W1H dimension a first-principle question comes FROM.

    Returns "" for an unknown question, so a caller can tell "no such question"
    from "a question with no dimension".
    """
    for k, _q, dim in FIRST_PRINCIPLE_QUESTIONS:
        if k == str(question_key or "").strip():
            return dim
    return ""


def uncovered_dimensions() -> tuple[str, ...]:
    """The 5W1H dimensions NO first-principle question covers.

    MEASURED: `when` and `where`. Reported rather than assumed, because a gap
    that is not visible is a gap nobody fixes.
    """
    covered = {dim for _k, _q, dim in FIRST_PRINCIPLE_QUESTIONS}
    return tuple(d for d in fw.DIMENSION_NAMES if d not in covered)


def assert_dimensions_known() -> None:
    """Every declared dimension must be a REAL 5W1H dimension.

    A typo (`whyy`) would otherwise invent a dimension that no other module
    knows about — the same defect `skill_factor.assert_known_tag` refuses for
    `applies_to`.
    """
    bad = [dim for _k, _q, dim in FIRST_PRINCIPLE_QUESTIONS
           if dim not in fw.DIMENSION_NAMES]
    if bad:
        raise NotMeasurable(
            ["first-principle question(s) declare dimension(s) %s, which are "
             "not 5W1H dimensions; the six are %s"
             % (bad, list(fw.DIMENSION_NAMES))])


def questions_by_dimension() -> dict[str, list[str]]:
    """The questions grouped by the 5W1H dimension they come from."""
    out: dict[str, list[str]] = {}
    for k, _q, dim in FIRST_PRINCIPLE_QUESTIONS:
        out.setdefault(dim, []).append(k)
    return out


class NotMeasurable(RuntimeError):
    """Raised when a factor cannot be audited by a measured unit."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("factor is not measurable — refusing: %s"
                         % "; ".join(reasons))


def unit_subject(unit: str) -> str:
    """The SUBJECT of a unit: the part that names what is being measured.

    `count of critical vulnerabilities` -> `critical vulnerabilities`
    `pct` -> `` (refused: names nothing)
    """
    s = str(unit or "").strip()
    m = re.match(r"^(boolean|count|pct|score_0_100)\s*(?:of|on|for)?\s*(.*)$",
                 s, re.I)
    if not m:
        return ""
    return m.group(2).strip()


def assert_measurable(factor: dict[str, Any],
                      conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The GATE. A factor with no measured unit is REFUSED.

    Refuses, in order:
      * an unknown unit kind
      * a unit that names NO subject (`count` alone)
      * a subject too short or too generic to be a name
      * a threshold that is not a value of the unit
      * a factor that cannot fail independently

    THE RULE IS READ FROM THE TABLE WHEN A CONNECTION IS GIVEN (the user,
    2026-09-23):

        "definition for key factor output = can measured unit"
        "if not, key factor is meaningless"

    MEASURED before this: the rule lived HERE, in code. `factor_template` had a
    `metric_unit` ROW but no RULE row, so the rule could not be read, extended or
    cited from the table — the same defect family as the hardcoded `llm_service`
    type. Now `factor_template.rule` carries it, and this function READS it.

    The fallback is NAMED, not silent: with no connection the code path runs and
    the result says `rule_source='code'`. A caller can therefore tell a verdict
    that came from the table from one that came from the module.
    """
    reasons: list[str] = []
    kind = str(factor.get("metric_kind") or "").strip()
    unit = str(factor.get("metric_unit") or "").strip()
    target = str(factor.get("metric_target") or "").strip()

    # ---- the rule, from the TABLE when possible ---------------------------
    rule_source = "code"
    rules: dict[str, str] = {}
    if conn is not None:
        try:
            import skill_factor as _sf
            rules = _sf.template_rules(conn)
            rule_source = "table"
        except Exception:
            # A missing table or an un-migrated DB must not turn every factor
            # into a refusal. The fallback is the code path, and it is NAMED.
            rules = {}
            rule_source = "code"

    def rule_for(field: str, default: str) -> str:
        return rules.get(field, default) if rules else default

    if kind not in UNIT_KINDS:
        reasons.append("metric_kind %r is not one of %s"
                       % (kind, ", ".join(UNIT_KINDS)))
    subject = unit_subject(unit)
    # `must_name_subject` is the TABLE's rule for `metric_unit`. When the table
    # says `NA` the field has no rule beyond `is_required`, so the check is
    # SKIPPED — a rule that is not declared must not be enforced silently.
    unit_rule = rule_for("metric_unit", "must_name_subject")
    if unit_rule == "must_name_subject":
        if not subject:
            reasons.append(
                "metric_unit %r names NO SUBJECT. A number without a unit cannot "
                "be audited: '0' is a pass for a vulnerability count and a FAIL "
                "for a test pass rate." % unit)
        elif subject.lower() in EMPTY_SUBJECTS:
            reasons.append("metric_unit subject %r names nothing" % subject)
        elif len(subject) < MIN_SUBJECT_CHARS:
            reasons.append("metric_unit subject %r is too short to be a name"
                           % subject)

    if not target:
        reasons.append("metric_target is empty — a factor with no threshold "
                       "cannot separate pass from fail")
    else:
        try:
            if kind == UNIT_COUNT:
                int(float(target))
            elif kind in (UNIT_PCT, UNIT_SCORE):
                float(target)
            elif kind == UNIT_BOOLEAN:
                if target.lower() not in ("true", "false", "1", "0", "yes", "no"):
                    reasons.append("boolean target %r is not a boolean" % target)
        except ValueError:
            reasons.append("metric_target %r is not a value of unit %r"
                           % (target, kind))

    if factor.get("independent") is False:
        reasons.append("the factor cannot fail independently, so it is part of "
                       "another factor rather than a factor of its own")

    if reasons:
        raise NotMeasurable(reasons)
    return dict(factor, rule_source=rule_source)


def audit_factor(factor: dict[str, Any],
                 conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Can this factor be audited by a measured unit? Returns a verdict, never
    raises, so a whole register can be audited in one pass.

    `is_active` IS CARRIED THROUGH (added 2026-09-27). MEASURED: this returned
    only `measurable`, so `audit_registry` could not tell a LIVE defect from a
    SUPERSEDED factor and reported `valid_phone_number_judgment` (factor 38,
    `is_active=0`) as a live defect forever. A verdict that drops the row's own
    state forces every caller to re-query it, and a caller that forgets gets the
    wrong answer.
    """
    active = int(factor.get("is_active", 1) or 0)
    try:
        res = assert_measurable(factor, conn)
        return {"factor_key": factor.get("factor_key"), "measurable": True,
                "is_active": active,
                "unit": factor.get("metric_unit"),
                "subject": unit_subject(str(factor.get("metric_unit") or "")),
                "rule_source": res.get("rule_source", "code"),
                "reasons": []}
    except NotMeasurable as e:
        return {"factor_key": factor.get("factor_key"), "measurable": False,
                "is_active": active,
                "unit": factor.get("metric_unit"), "subject": "",
                "reasons": e.reasons}


def derive(factor: dict[str, Any]) -> dict[str, Any]:
    """The FIRST-PRINCIPLE derivation of a factor.

    Returns the five answers, and reports `derived: False` unless every one is
    answered. A factor with an unanswered question is a preference that has not
    been examined.

    Each answer is also tagged with the 5W1H dimension it comes from, so a
    reader can see WHICH dimension is unanswered rather than only which question.
    """
    answers = {k: str(factor.get(k) or "").strip()
               for k, _q, _d in FIRST_PRINCIPLE_QUESTIONS}
    missing = [k for k, v in answers.items() if not v]
    return {"factor_key": factor.get("factor_key"), "answers": answers,
            "missing": missing, "derived": not missing,
            "questions": {k: q for k, q, _d in FIRST_PRINCIPLE_QUESTIONS},
            "dimensions": {k: d for k, _q, d in FIRST_PRINCIPLE_QUESTIONS},
            "missing_dimensions": sorted({dimension_of(k) for k in missing}),
            "uncovered_dimensions": list(uncovered_dimensions())}


def audit_registry(conn: sqlite3.Connection) -> dict[str, Any]:
    """Audit every registered factor. Reports, never raises.

    The CONNECTION is passed through, so the verdict comes from the TABLE's rule
    (`factor_template.rule`) rather than from this module's code path. The result
    carries `rule_source` so a reader can tell which one answered.

    AN INACTIVE FACTOR IS REPORTED SEPARATELY, NOT HIDDEN.
    -----------------------------------------------------
    MEASURED 2026-09-27: this counted EVERY row, so `valid_phone_number_judgment`
    (factor 38, `is_active=0`, superseded by factor 39) was reported as a LIVE
    defect forever. A superseded factor is not a defect to fix — it is HISTORY,
    and `activation_gate.record_supersession` records it as such.

    The fix does NOT drop the row: dropping it would make a real problem
    invisible. It CLASSIFIES it, so `failures` means "an ACTIVE factor that
    cannot be measured" and `superseded_failures` means "an INACTIVE factor that
    cannot be measured, which is why it is inactive". Both are returned.
    """
    rows = []
    for r in conn.execute("SELECT * FROM skill_factor_registry "
                          "ORDER BY sort_order"):
        rows.append(audit_factor(dict(r), conn))
    bad = [r for r in rows if not r["measurable"]]
    # `is_active` is read from the ROW, not assumed. A row that does not carry
    # the column is treated as ACTIVE, because an unknown state must not be
    # silently excused.
    active_bad = [r for r in bad if int(r.get("is_active", 1) or 0) == 1]
    inactive_bad = [r for r in bad if int(r.get("is_active", 1) or 0) != 1]
    sources = sorted({r.get("rule_source", "code") for r in rows})
    return {"factors": len(rows), "measurable": len(rows) - len(bad),
            "not_measurable": len(bad), "rule_source": sources,
            "failures": active_bad,
            "superseded_failures": inactive_bad,
            "superseded_note": (
                "%d inactive factor(s) cannot be measured. They are REPORTED, "
                "not hidden: an inactive factor is HISTORY, and "
                "`activation_gate.record_supersession` is what records it."
                % len(inactive_bad))}


def repair_units(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Rewrite a unit that names NO SUBJECT so that it DOES — mechanically.

    MEASURED DEFECT (2026-09-28): `audit_registry(conn)` reported
    `factors 65 · measurable 54 · NOT measurable 11`, and every failure shared
    ONE reason — the `metric_unit` named no subject. **Two of the 11 were mine**,
    filed the same day.

    THE REPAIR IS NOT AN INVENTION, and that is why it can be mechanical. The
    existing unit text ALREADY says what is being counted:

        'claims with an uncheckable citation'      (count, target 0)
        -> 'count of claims with an uncheckable citation'

    `unit_subject()` requires the kind to PREFIX the unit, so the fix is exactly
    `<metric_kind> of <the text that was already there>`. **No subject is
    invented; the one the author wrote is made explicit.** A unit whose text is
    itself empty or generic (`''`, `'value'`, `'count'`) CANNOT be repaired this
    way and is REPORTED as `unrepairable` — never force-rewritten to a fake
    subject, because a fabricated subject is worse than a missing one: it looks
    checkable.

    THE WRITE GOES THROUGH `skill_factor.register_factor`, so it passes the SAME
    gate a human's write passes; if the repair were wrong the gate would refuse
    it here.

    DEFAULT IS A DRY RUN. `apply=True` writes, and each write is reported with
    the before/after text.
    """
    out: dict[str, Any] = {"checked": 0, "repaired": [], "unrepairable": [],
                           "blocked": [], "already_ok": 0, "applied": bool(apply)}
    for r in conn.execute("SELECT * FROM skill_factor_registry "
                          "ORDER BY sort_order"):
        f = dict(r)
        out["checked"] += 1
        if audit_factor(f, conn)["measurable"]:
            out["already_ok"] += 1
            continue
        unit = str(f.get("metric_unit") or "").strip()
        kind = str(f.get("metric_kind") or "").strip()
        subject = unit_subject(unit)
        # TWO SHAPES land here: a unit with no kind prefix (the defect), and a
        # unit whose subject is generic. Only the FIRST is mechanical.
        if subject:
            out["unrepairable"].append(
                {"factor_key": f["factor_key"], "unit": unit,
                 "why": "the subject %r names nothing; a subject cannot be "
                        "derived from it" % subject})
            continue
        if not unit:
            out["unrepairable"].append(
                {"factor_key": f["factor_key"], "unit": unit,
                 "why": "the unit is empty; there is nothing to make explicit"})
            continue
        new_unit = "%s of %s" % (kind, unit)
        verdict = audit_factor(
            {"factor_key": f["factor_key"], "metric_kind": kind,
             "metric_unit": new_unit, "metric_target": f.get("metric_target")},
            conn)
        if not verdict["measurable"]:
            out["unrepairable"].append(
                {"factor_key": f["factor_key"], "unit": unit,
                 "would_be": new_unit,
                 "why": "; ".join(verdict.get("reasons") or [])})
            continue
        # ---- THE WRITER'S OWN PREREQUISITES, CHECKED (so this is IDEMPOTENT)
        # MEASURED 2026-09-28, and this is why the bucket exists: the first
        # version put every repairable row in `repaired` even when the WRITE
        # then failed, so `valid_phone_number_judgment` (empty `cite_ref`,
        # refused by `register_factor` with "No citation, no row") was
        # re-proposed on EVERY run — the repair was NOT idempotent, and a
        # second run reported work that could never land.
        #
        # The prerequisite is not guessed: `register_factor` documents
        # `cite_ref is required`, and the check is the SAME test it applies.
        if not str(f.get("cite_ref") or "").strip():
            out["blocked"].append(
                {"factor_key": f["factor_key"], "unit": unit,
                 "would_be": new_unit,
                 "why": "cite_ref is EMPTY, so the citation gate refuses the "
                        "write (`No citation, no row`). The unit CANNOT be "
                        "repaired until the row carries a citation — a separate "
                        "defect on the same row, reported not patched."})
            continue
        entry = {"factor_key": f["factor_key"], "old": unit, "new": new_unit,
                 "subject": unit_subject(new_unit),
                 "is_active": f.get("is_active")}
        if apply:
            import skill_factor as _sf
            try:
                _sf.register_factor(
                    conn, factor_key=f["factor_key"], name=f["name"],
                    rule_definition=f["rule_definition"], action=f["action"],
                    metric_kind=kind, metric_unit=new_unit,
                    metric_target=str(f["metric_target"]),
                    proof_prefix=str(f["proof_prefix"]),
                    skill_key=f.get("skill_key") or None,
                    applies_to=str(f.get("applies_to") or "*"),
                    sort_order=int(f.get("sort_order") or 0),
                    cite_ref=str(f.get("cite_ref") or ""))
            except _sf.FactorError as e:
                # ANY other refusal is reported with the writer's own words.
                out["blocked"].append(
                    {"factor_key": f["factor_key"], "unit": unit,
                     "would_be": new_unit,
                     "why": "the writer REFUSED it: %s" % e})
                continue
        out["repaired"].append(entry)
    return out


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--questions", action="store_true")
    ap.add_argument("--dimensions", action="store_true",
                    help="the 5W1H mapping, and which dimensions are uncovered")
    args = ap.parse_args()
    if args.questions:
        print(json.dumps({k: {"question": q, "dimension": d}
                          for k, q, d in FIRST_PRINCIPLE_QUESTIONS},
                         indent=2, ensure_ascii=False))
        return
    if args.dimensions:
        assert_dimensions_known()
        print(json.dumps({
            "by_dimension": questions_by_dimension(),
            "uncovered": list(uncovered_dimensions()),
            "all_six": list(fw.DIMENSION_NAMES),
        }, indent=2, ensure_ascii=False))
        return
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        print(json.dumps(audit_registry(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()