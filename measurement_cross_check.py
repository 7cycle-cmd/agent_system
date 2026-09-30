# -*- coding: utf-8 -*-
"""measurement_cross_check.py — a number must be REPRODUCED by an INDEPENDENT reader.

WHY THIS EXISTS
---------------
MEASURED 2026-09-29. The agent reported a total of **1516** uncovered paths. The
number was wrong by a factor of 22 (the real split was 776 structural, 532 not
product code, 69 real code). What caught it was the agent asking itself
*"what should the number be?"* — a HUMAN judgement, not a mechanism.

The human named the defect exactly:

    「份報告靠『問個數應該係幾多』嚟捉量度錯 —— 呢個係人肉判斷，唔係系統化」
    「依賴人（或 agent）自覺，唔係依賴機制」

That is the SAME disease as the plan gate's fail-open: the system depended on the
agent noticing, not on a mechanism. This module is the mechanism.

WHY `measurement_scope` DOES NOT ALREADY CLOSE IT
-------------------------------------------------
`measurement_scope.py` declares `assert_scoped()` — a write-site gate — and it is
correct. But MEASURED (grep, 2026-09-29) it has **ZERO production callers**: only
its own proof and the TDD probe call it. And it checks only that a count NAMES
its population. It does NOT check that the count is REPRODUCIBLE by a second
reader, and it does NOT check that the count is BOUNDED by the population's size.
The 1516 error passed both of those: the population was named, the citation was
real, and the number was simply wrong.

THE TWO MECHANISMS
------------------
A. CROSS-READER. A measurement declares TWO readers. They must be DIFFERENT —
   a reader compared to itself is not a cross-check. This is the 1516 defect: the
   SAME parser was run twice and gave 12, then 1584.

   AND THEY MUST DIFFER IN SOURCE, NOT ONLY IN NAME (the human's approved delta,
   2026-09-29). A reader is `{name, method, data_source}`. Two readers that share
   a `method` (the same parser / the same query) OR a `data_source` (the same
   file, table or endpoint) are NOT independent: they can AGREE WHILE BOTH ARE
   WRONG, and the mechanism would then report `AGREED` on a shared bug. That is
   WORSE than having no second reader, because it claims "verified". So either
   being equal is `SAME_READER` — the pair is not a valid cross-check.

B. BOUND. The count must be <= the INDEPENDENTLY-MEASURED size of the population
   it claims to count. A count of "1584 uncovered paths" when only 329 plans
   exist on disk is IMPOSSIBLE, and this rule refuses it WITHOUT anyone asking
   "should it be that big?". This is the human's question turned into a
   comparison against a measured denominator.

THE STATUSES, EACH OF WHICH CAN FAIL
------------------------------------
  AGREED                 two readers, different name AND method AND data_source,
                         same number, within the bound            -> KEEP
  SAME_READER            the two readers are NOT a valid pair — the same NAME,
                         or a shared `method` or `data_source`   -> DISCARD
  DISAGREED              the two numbers differ                  -> DISCARD
  UNBOUNDED              the count exceeds the population size   -> DISCARD
  SECOND_READER_MISSING  no second reader declared               -> DISCARD

Only `AGREED` keeps the finding. Every other terminal state is DISCARDED, never
downgraded to "low confidence" — `citation-discipline`'s rule, applied here.

WHAT THIS DOES NOT DO
---------------------
It does NOT catch EVERY wrong measurement. It catches the two classes MEASURED
here: a number produced by one reader (SAME_READER) and a number
that cannot fit its population (UNBOUNDED). A wrong number that two genuinely
independent readers agree on is NOT caught, and this module does not claim it is.

Pure functions. No DB, no I/O. The caller supplies the readers and the counts.
"""
from __future__ import annotations

import sys
from typing import Any, Iterable

try:  # cp950 console: a non-cp950 char raises and kills the run before its verdict
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --- the status vocabulary -------------------------------------------------
AGREED = "AGREED"
SAME_READER = "SAME_READER"
DISAGREED = "DISAGREED"
UNBOUNDED = "UNBOUNDED"
SECOND_READER_MISSING = "SECOND_READER_MISSING"

# The ONLY status at which a finding may be kept.
KEEP_STATUS = AGREED

# Every terminal status, and whether it keeps the finding.
TERMINAL: dict[str, bool] = {
    SECOND_READER_MISSING: False,
    SAME_READER: False,
    DISAGREED: False,
    UNBOUNDED: False,
    AGREED: True,
}

STEP_NAMES = (
    "SECOND READER PRESENT",
    "READERS ARE DISTINCT",
    "READERS ARE INDEPENDENT IN SOURCE",
    "THE TWO NUMBERS AGREE",
    "THE NUMBER FITS ITS POPULATION",
)


class UncrossCheckedFinding(ValueError):
    """Raised when a finding is written without an AGREED cross-check."""


# ---------------------------------------------------------------------------
# The reader, and what makes two readers independent
# ---------------------------------------------------------------------------

def normalize_reader(reader: Any) -> dict[str, str]:
    """A reader as `{name, method, data_source}`. A bare string is a NAME only.

    A bare string is accepted so a caller can pass `"parser_a"`, but then its
    `method` and `data_source` are EMPTY — and two empty methods are EQUAL, so
    two bare strings are `SAME_READER`. That is the safe direction: a caller
    that does not declare HOW and FROM WHERE cannot claim independence.
    """
    if isinstance(reader, dict):
        return {
            "name": str(reader.get("name") or "").strip(),
            "method": str(reader.get("method") or "").strip(),
            "data_source": str(reader.get("data_source") or "").strip(),
        }
    return {"name": str(reader or "").strip(), "method": "", "data_source": ""}


def readers_are_distinct(a: dict[str, str], b: dict[str, str]) -> tuple[bool, str]:
    """Step 2 — the two readers must not be the SAME reader.

    A reader compared to itself is not a cross-check. This is the 1516 defect:
    the same parser was run twice.
    """
    if not a["name"] or not b["name"]:
        return False, "a reader has no name"
    if a["name"] == b["name"]:
        return False, "both readers are named %r — a reader compared to itself" % a["name"]
    return True, "%s vs %s" % (a["name"], b["name"])


def readers_are_independent(a: dict[str, str], b: dict[str, str]) -> tuple[bool, str]:
    """Step 3 — the two readers must differ in METHOD and DATA SOURCE (QC-11).

    THE HUMAN'S APPROVED DELTA (2026-09-29): *"plan 冇檢驗「兩個 reader 係咪真係
    唔同」"*. Two differently-NAMED wrappers over ONE parser, or two readers of ONE
    source, can AGREE WHILE BOTH ARE WRONG. Reporting `AGREED` on a shared bug is
    WORSE than having no second reader, because it claims "verified".

    MEASURED, and this is why the rule is needed: the 1516 error was ONE parser
    run twice (12, then 1584). A cross-check that compared only NAMES would have
    caught that; a cross-check that accepted two wrappers over the SAME parser
    would have reported `AGREED` and passed the wrong number.

    THE VERDICT IS `SAME_READER`, PER THE DELTA: a shared method or source means
    the pair is NOT a valid second reader — the same disease as using one reader
    twice. The DETAIL names which of the three caused it, so the diagnosis is not
    lost (and the `measurement_cross_check` row stores both methods and sources).

    AN UNDECLARED METHOD OR SOURCE IS REFUSED, NOT ASSUMED DIFFERENT. The delta
    says the caller MUST declare each reader's method and data source. A reader
    that declares neither cannot be shown to differ from anything, so
    independence is UNPROVABLE and the safe direction is to refuse. MEASURED
    DEFECT IN MY FIRST VERSION: the docstring claimed two bare strings are
    refused, but the code only compared NON-EMPTY values, so two bare strings
    fell through to `AGREED` — a claim the code did not implement, which is the
    exact defect family this repo keeps paying for. The code now refuses.
    """
    if not a["method"] or not b["method"]:
        return False, ("a reader declares no method — independence is "
                       "UNPROVABLE, so it cannot be claimed")
    if not a["data_source"] or not b["data_source"]:
        return False, ("a reader declares no data_source — independence is "
                       "UNPROVABLE, so it cannot be claimed")
    if a["method"] == b["method"]:
        return False, ("both readers use method %r — a shared parser can agree "
                       "while both are wrong" % a["method"])
    if a["data_source"] == b["data_source"]:
        return False, ("both readers read data_source %r — a shared source can "
                       "agree while both are wrong" % a["data_source"])
    return True, "method %r vs %r; source %r vs %r" % (
        a["method"], b["method"], a["data_source"], b["data_source"])


# ---------------------------------------------------------------------------
# The whole cross-check, as one call
# ---------------------------------------------------------------------------

def cross_check(
    *,
    reader_a: Any = "",
    reader_b: Any = "",
    count_a: Any = None,
    count_b: Any = None,
    population_size: Any = None,
    population_cite: str = "",
    count_command: str = "",
) -> dict[str, Any]:
    """Run the five steps in order and return the cross-check.

    Steps SHORT-CIRCUIT: once a step fails, the later steps are not run, because
    a number from a non-independent reader is not a measurement. The status is
    the FIRST failing step's status, so the report names the earliest thing to
    fix.
    """
    steps: list[dict[str, Any]] = []
    a = normalize_reader(reader_a)
    b = normalize_reader(reader_b)

    # Step 1 — a second reader must exist.
    if not b["name"]:
        steps.append({"n": 1, "step": STEP_NAMES[0], "status": SECOND_READER_MISSING,
                      "detail": "no second reader declared — one reader is not a "
                                "cross-check"})
        return _result(steps, SECOND_READER_MISSING)
    steps.append({"n": 1, "step": STEP_NAMES[0], "status": "OK",
                  "detail": "%s + %s" % (a["name"], b["name"])})

    # Step 2 — the two readers must be distinct.
    ok, detail = readers_are_distinct(a, b)
    if not ok:
        steps.append({"n": 2, "step": STEP_NAMES[1], "status": SAME_READER,
                      "detail": detail})
        return _result(steps, SAME_READER)
    steps.append({"n": 2, "step": STEP_NAMES[1], "status": "OK", "detail": detail})

    # Step 3 — they must be independent in SOURCE, not only in name (QC-11).
    # A shared method or source is `SAME_READER`: the pair is not a valid second
    # reader, which is the same disease as using one reader twice.
    ok, detail = readers_are_independent(a, b)
    if not ok:
        steps.append({"n": 3, "step": STEP_NAMES[2], "status": SAME_READER,
                      "detail": detail})
        return _result(steps, SAME_READER)
    steps.append({"n": 3, "step": STEP_NAMES[2], "status": "OK", "detail": detail})

    # Step 4 — the two numbers must AGREE. BOTH are reported, so a reader sees
    # the disagreement rather than a bare refusal.
    if count_a is None or count_b is None:
        steps.append({"n": 4, "step": STEP_NAMES[3], "status": DISAGREED,
                      "detail": "a count is missing: a=%r b=%r" % (count_a, count_b)})
        return _result(steps, DISAGREED)
    if count_a != count_b:
        steps.append({"n": 4, "step": STEP_NAMES[3], "status": DISAGREED,
                      "detail": "reader A says %s, reader B says %s — the number "
                                "is NOT reproducible" % (count_a, count_b)})
        return _result(steps, DISAGREED)
    steps.append({"n": 4, "step": STEP_NAMES[3], "status": "OK",
                  "detail": "both readers say %s" % count_a})

    # Step 5 — the number must FIT its population. ALWAYS runs, even when the
    # number is small, because a check that only runs on the big case never runs
    # on the easy case and a check that never runs cannot fail.
    if population_size is None:
        steps.append({"n": 5, "step": STEP_NAMES[4], "status": UNBOUNDED,
                      "detail": "no population size supplied — the number cannot "
                                "be bounded"})
        return _result(steps, UNBOUNDED)
    if count_a > population_size:
        steps.append({"n": 5, "step": STEP_NAMES[4], "status": UNBOUNDED,
                      "detail": "the count %s EXCEEDS the population size %s — "
                                "impossible, and no one had to ask 'should it be "
                                "that big?'" % (count_a, population_size)})
        return _result(steps, UNBOUNDED)
    steps.append({"n": 5, "step": STEP_NAMES[4], "status": "OK",
                  "detail": "%s <= %s" % (count_a, population_size)})

    return _result(steps, AGREED)


def _result(steps: list[dict[str, Any]], status: str) -> dict[str, Any]:
    return {
        "steps": steps,
        "status": status,
        "keep": TERMINAL.get(status, False),
        "steps_run": len(steps),
        "steps_total": len(STEP_NAMES),
    }


# ---------------------------------------------------------------------------
# The write-site gate
# ---------------------------------------------------------------------------

def assert_cross_checked(scope: dict[str, Any], *, cite_ref: str = "") -> None:
    """Raise unless the cross-check is AGREED. Call this AT THE WRITE SITE.

    A gate that is never called at the write site is not a gate. This is the
    same placement rule `citation-discipline` and `measurement_scope` state: the
    check belongs where the finding is written, not where it is read.
    """
    if not isinstance(scope, dict):
        raise UncrossCheckedFinding(
            "scope must be a dict, got %s" % type(scope).__name__)
    if scope.get("status") != KEEP_STATUS:
        raise UncrossCheckedFinding(
            "finding is %s, not %s — DISCARDED, not downgraded. %s"
            % (scope.get("status"), KEEP_STATUS,
               (scope.get("steps") or [{}])[-1].get("detail", "")))
    if not str(cite_ref or "").strip():
        raise UncrossCheckedFinding(
            "an AGREED finding still needs a citation at the write site")


def partition(findings: Iterable[dict[str, Any]]) -> tuple[list, list]:
    """Split findings into (kept, dropped). A dropped finding is GONE.

    There is no third list and no "low confidence" label: a downgrade would let
    a non-reproducible finding keep existing under a new name.
    """
    kept: list = []
    dropped: list = []
    for f in findings:
        scope = f.get("scope") if isinstance(f, dict) else None
        (kept if (scope or {}).get("status") == KEEP_STATUS else dropped).append(f)
    return kept, dropped


# ---------------------------------------------------------------------------
# The 1516 case, as data — so the proof and the contract share ONE source
# ---------------------------------------------------------------------------

CASE_1516 = {
    "reader_a": {"name": "plan_gate.plan_allowlist",
                 "method": "regex over the plan's allowlist section",
                 "data_source": "qc_evidence/plan_*.md"},
    "reader_b": {"name": "entity_backfill.covered_by",
                 "method": "SQL over code_location_registry",
                 "data_source": "agent.db:code_location_registry"},
    "wrong_count": 1584,
    "right_count": 69,
    "population_size": 329,
    "population": "plans in qc_evidence/",
    "population_cite": "ls qc_evidence/plan_*.md | wc -l",
    "the_wrong_claim": "1516 allowlist-only paths need entities",
    "the_same_reader_defect": {
        "reader_a": {"name": "plan_gate.plan_allowlist",
                     "method": "regex over the plan's allowlist section",
                     "data_source": "qc_evidence/plan_*.md"},
        "reader_b": {"name": "plan_gate.plan_allowlist_v2",
                     "method": "regex over the plan's allowlist section",
                     "data_source": "qc_evidence/plan_*.md"},
    },
}


def case_1516(*, correct: bool) -> dict[str, Any]:
    """The real case. `correct=False` reproduces the wrong number (1584)."""
    c = CASE_1516
    n = c["right_count"] if correct else c["wrong_count"]
    return cross_check(
        reader_a=c["reader_a"], reader_b=c["reader_b"],
        count_a=n, count_b=n,
        population_size=c["population_size"],
        population_cite=c["population_cite"],
        count_command="ls qc_evidence/plan_*.md | wc -l",
    )


def case_1516_same_reader() -> dict[str, Any]:
    """The 1516 defect itself: ONE parser, two names, agreeing numbers.

    The names DIFFER, so it is not the trivial same-name case — it is the
    shared-METHOD case, which is the one a name-only check would miss.
    """
    d = CASE_1516["the_same_reader_defect"]
    return cross_check(
        reader_a=d["reader_a"], reader_b=d["reader_b"],
        count_a=12, count_b=12,
        population_size=CASE_1516["population_size"],
        population_cite=CASE_1516["population_cite"],
        count_command="ls qc_evidence/plan_*.md | wc -l",
    )


if __name__ == "__main__":  # pragma: no cover
    for label, sc in (
        ("the WRONG number (1584 > 329)", case_1516(correct=False)),
        ("the RIGHT number (69 <= 329)", case_1516(correct=True)),
        ("ONE parser, two names (the 1516 defect)", case_1516_same_reader()),
    ):
        print("=== %s ===" % label)
        for s in sc["steps"]:
            print("  %d. %-34s %-22s %s"
                  % (s["n"], s["step"], s["status"], s["detail"]))
        print("  -> status=%s keep=%s" % (sc["status"], sc["keep"]))
        print()
