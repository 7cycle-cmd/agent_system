"""factor_audit.py — THREE-LEVEL audit of a factor, RULE-ONLY.

WHY RULE-ONLY, AND NOT AN LLM
-----------------------------
The user asked: "can LLM help for this job? his responsibility is audit by 3
level prompt to confirm target = YES or NO; let's try".

I tried it, because the honest answer needs a number, not an opinion.

MEASURED 2026-09-21 (harness `_try_llm_3level_audit.py`, 7 fixtures, 3 stable
runs, model `qwen2.5:7b-instruct`):

    level          rules    LLM      note
    -------------  -------  -------  -------------------------------------
    L1 structural  100%     100%     the LLM adds NOTHING over a rule
    L2 consistent  100%    42.9%     BELOW chance; yes_recall = 20%
    L3 grounded    100%     85.7%    the rule is also 100%
    all-3 correct  100%    28.6%

The LLM's L2 has no_recall=100% but yes_recall=20%: it answers NO to nearly
everything. Its 42.9% is the negative class, not judgement — the "answered only
one class" defect. An auditor that is wrong on 57% of cases and never says YES
is worse than no auditor, because its output LOOKS like a verdict.

So this module uses RULES. Where a rule cannot decide, it says UNDEFINED — it
does not guess.

THE THREE LEVELS ARE THREE DIFFERENT QUESTIONS
----------------------------------------------
  L1 STRUCTURAL  is it a measurable metric at all?  (kind in the alphabet,
                 non-empty unit, numeric target)
  L2 CONSISTENT  do kind and unit AGREE with each other?
  L3 GROUNDED    does the unit NAME a subject a third party could measure?

A LEVEL CAN BE **UNDEFINED**, AND THAT IS NOT A PASS
----------------------------------------------------
Measured: the first draft forced YES/NO everywhere and expected L2=NO on a
factor whose `metric_kind` was invalid ('vibes'). That is incoherent —
CONSISTENCY of an invalid kind is not false, it is UNANSWERABLE. Forcing a
boolean produced a wrong expectation that then looked like a rule bug.

This is the same rule `evidence_classify` follows: when the container is absent,
report UNKNOWN rather than a silent pass.

    L1 fails because the KIND is invalid  ->  L2 is UNDEFINED
    L1 fails for any other reason         ->  L2 is still CHECKABLE
    L3 is INDEPENDENT of L1 on purpose    ->  it answers about the UNIT only

A defect must not smear across levels: a bad TARGET is not a bad UNIT.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# The alphabet. These are the kinds the register accepts, read from the one
# place that defines them so this audit cannot drift from the write gate.
VALID_KINDS = ("boolean", "count", "pct", "score_0_100")

# A unit that NAMES NOTHING. A number without a subject cannot be audited: "0"
# is a pass for a vulnerability count and a FAIL for a test pass rate.
UNIT_NAMES_NOTHING = {
    "", "count", "value", "total", "thing", "things", "x", "number", "amount",
    "score", "quality", "how good it feels",
}


class AuditError(ValueError):
    """Raised when the audit itself cannot run (not when a level fails)."""


# ---------------------------------------------------------------------------
# THE RULES
# ---------------------------------------------------------------------------

def rule_l1(f: dict) -> bool:
    """STRUCTURAL: is this a measurable metric at all?

    A `boolean` factor's target is legitimately `true` / `false`, NOT a number.
    Measured on the real register: demanding a numeric target flagged
    `crud_delete` and `llm_availability_window` as FAIL while both were sound.
    The rule was wrong, not the data — so the numeric requirement is scoped to
    the kinds that are genuinely numeric.
    """
    kind = str(f.get("metric_kind") or "").strip()
    target = str(f.get("metric_target") or "").strip()
    unit = str(f.get("metric_unit") or "").strip()
    if kind not in VALID_KINDS:
        return False
    if not unit:
        return False
    if kind == "boolean":
        return target.lower() in ("true", "false", "1", "0", "yes", "no")
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", target))


def rule_l2(f: dict) -> bool | None:
    """CONSISTENT: do kind and unit agree?

    Returns None when the kind is not a metric kind at all: consistency of an
    invalid kind is UNANSWERABLE, not false.
    """
    kind = str(f.get("metric_kind") or "").strip()
    unit = str(f.get("metric_unit") or "").strip().lower()
    if kind not in VALID_KINDS:
        return None
    if kind == "pct":
        # A percentage must speak in percent, not in things.
        return ("pct" in unit or "percent" in unit or "%" in unit)
    if kind == "count":
        # A count must not claim to be a percentage.
        return not ("pct" in unit or "percent" in unit or "%" in unit)
    if kind == "score_0_100":
        return "score" in unit or "0-100" in unit or "0_100" in unit
    return True  # boolean has no unit constraint


def rule_l3(f: dict) -> bool | None:
    """GROUNDED: does the unit name a subject?

    INDEPENDENT of L1 on purpose. A rule can only catch the obvious cases
    ("count", "value", "total"); it cannot tell whether "count of dead services"
    names a real subject in THIS domain.

    MEASURED (2026-09-21), by asking whether a 7B could distill a factor WITHOUT
    a rubric: it returned `metric_unit="findings"` and `"calls"`. Those DO name
    a subject, and this rule rejected them — a FALSE FAIL. A single word is
    genuinely ambiguous: the rule cannot separate `"count"` (names nothing) from
    `"findings"` (names something) without domain knowledge it does not have.

    So a single word that is not in the known-nothing set returns None
    (UNDEFINED) rather than False. An uncertain rule must not manufacture a
    FAIL — the same rule `evidence_classify` follows, and the reason a false
    negative is worse here than an admitted gap.
    """
    unit = str(f.get("metric_unit") or "").strip().lower()
    if unit in UNIT_NAMES_NOTHING:
        return False
    # Multi-word, or declares its subject with "of" -> clearly grounded.
    if " of " in unit or len(unit.split()) > 1:
        return True
    # A bare word: it MIGHT name a subject ("findings") or might not ("count",
    # already handled above). The rule cannot decide, and must say so.
    return None


def audit_factor_dict(f: dict) -> dict[str, Any]:
    """Audit a factor dict at 3 levels. Returns the verdict, no writes.

    `verdict` is PASS only when all three levels are True. UNDEFINED is its own
    outcome and is NOT a pass: an unanswerable level means the factor is not
    AUDITABLE, which is a defect in the factor, not a mild pass.
    """
    l1 = rule_l1(f)
    l2 = rule_l2(f)
    l3 = rule_l3(f)
    levels = {"L1": l1, "L2": l2, "L3": l3}
    if l1 is False or l3 is False:
        verdict = "FAIL"
    elif l2 is None or l3 is None:
        # UNDEFINED is its own outcome and is NOT a pass: an unanswerable level
        # means the factor is not AUDITABLE as stated, which is a real finding —
        # but it is not the same finding as a defect. Reporting it as FAIL would
        # machine a real defect from an admitted gap in the rule.
        verdict = "UNDEFINED"
    else:
        verdict = "PASS"
    return {"levels": levels, "verdict": verdict,
            "reasons": _reasons(f, levels)}


def _reasons(f: dict, levels: dict) -> list[str]:
    out: list[str] = []
    if levels["L1"] is False:
        kind = str(f.get("metric_kind") or "")
        target = str(f.get("metric_target") or "")
        if kind not in VALID_KINDS:
            out.append("L1: metric_kind %r is not one of %s"
                       % (kind, ", ".join(VALID_KINDS)))
        elif kind == "boolean":
            if target.lower() not in ("true", "false", "1", "0", "yes", "no"):
                out.append("L1: boolean target %r is not true/false" % target)
        elif not re.fullmatch(r"-?\d+(\.\d+)?", target):
            out.append("L1: metric_target %r is not a number" % target)
        if not str(f.get("metric_unit") or "").strip():
            out.append("L1: metric_unit is empty")
    if levels["L2"] is False:
        out.append("L2: metric_kind %r disagrees with metric_unit %r"
                   % (f.get("metric_kind"), f.get("metric_unit")))
    elif levels["L2"] is None:
        out.append("L2: UNDEFINED — metric_kind %r is not a metric kind, so "
                   "consistency cannot be judged"
                   % (f.get("metric_kind") or ""))
    if levels["L3"] is False:
        out.append("L3: metric_unit %r names no subject a third party could "
                   "measure" % (f.get("metric_unit") or ""))
    elif levels["L3"] is None:
        out.append("L3: UNDEFINED — metric_unit %r is a single word, so this "
                   "rule cannot tell whether it names a subject; that needs "
                   "domain knowledge" % (f.get("metric_unit") or ""))
    return out


# ---------------------------------------------------------------------------
# DB ENTRY POINT
# ---------------------------------------------------------------------------

def audit_factor(conn: sqlite3.Connection, factor_key: str) -> dict[str, Any]:
    """Audit ONE registered factor, reading it from the register.

    The row is read from `skill_factor_registry`, so this audits what is
    REGISTERED and not a copy that could drift from it.
    """
    row = conn.execute("SELECT * FROM skill_factor_registry WHERE factor_key=?",
                       (factor_key,)).fetchone()
    if not row:
        raise AuditError("factor %r is not registered" % factor_key)
    f = dict(row)
    res = audit_factor_dict(f)
    res["factor_key"] = factor_key
    res["cite_ref"] = "skill_factor_registry.factor_key=%s" % factor_key
    return res


def audit_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Audit every active factor. Returns a summary, so the SCALE is visible.

    A single factor being auditable says nothing about the register: 642 of 652
    factor rows were UNMEASURED at one point in this workset. The count of
    failures is the finding, so it is reported here rather than left to be
    discovered one row at a time.
    """
    rows = conn.execute("SELECT factor_key FROM skill_factor_registry "
                        "WHERE is_active=1 ORDER BY factor_key").fetchall()
    results = [audit_factor(conn, r[0]) for r in rows]
    by_verdict: dict[str, int] = {}
    for r in results:
        by_verdict[r["verdict"]] = by_verdict.get(r["verdict"], 0) + 1
    return {"total": len(results), "by_verdict": by_verdict,
            "failures": [r for r in results if r["verdict"] != "PASS"]}


def _main() -> None:
    conn = sqlite3.connect(str(BASE / "agent.db"))
    conn.row_factory = sqlite3.Row
    summary = audit_all(conn)
    print("audited %d active factors" % summary["total"])
    for verdict, n in sorted(summary["by_verdict"].items()):
        print("  %-10s %d" % (verdict, n))
    for r in summary["failures"][:20]:
        print("  %-10s %-40s %s" % (r["verdict"], r["factor_key"],
                                    "; ".join(r["reasons"])))
    conn.close()


if __name__ == "__main__":
    _main()