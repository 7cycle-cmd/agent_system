# -*- coding: utf-8 -*-
"""measurement_scope.py — name the POPULATION before you count it.

WHY THIS EXISTS
---------------
MEASURED 2026-09-27. The agent reported:

    "the workspace list is 89% proof residue"

It had counted `COUNT(*) FROM working_environment` = **75** — the TABLE. The
list the user actually reads returns `WHERE is_active=1` = **4**, of which
`kind LIKE 'PROOF%'` = **0**. The finding was wrong, and it was wrong in a way
that TWO existing skills cannot catch:

  * `citation-discipline` requires a citation — and the count WAS cited. A
    citation can be a TRUE count of the WRONG population.
  * `problem-statement` requires target / expected / actual / observable — and
    all four were present. A fully-stated problem can still be about the wrong
    set.

So a finding can be FULLY CITED and FULLY STATED and still be about the wrong
population. Neither skill has a step that NAMES the population. That is the
missing step, and this module is it.

THE FOUR STEPS, EACH WITH A STATUS
----------------------------------
  1. NAME THE READER       READER_NAMED      / READER_UNNAMED
  2. NAME THE POPULATION   POPULATION_NAMED  / POPULATION_UNNAMED
  3. COUNT THE POPULATION  COUNTED           / NOT_COUNTED
  4. COMPARE TO THE TABLE  SCOPED            / UNSCOPED

A finding is KEPT only at `SCOPED`. Every other terminal state is DISCARD.

WHY STEP 4 RUNS EVEN WHEN THE COUNTS ARE EQUAL
----------------------------------------------
A check that only runs when the two counts DIFFER never runs on the easy case,
and a check that never runs cannot FAIL. That is the same defect class as
`independent-review`'s `len(Counter(x)) >= 1` — a condition that is true for
every input. So step 4 ALWAYS runs and always records which branch it took.

DISCARD, NOT DOWNGRADE
----------------------
An `UNSCOPED` finding is DISCARDED, never relabelled "low confidence". A
downgrade lets a wrong-population finding keep existing under a new label —
exactly the failure `citation-discipline` already forbids for an uncited one.

Pure functions. No DB, no I/O. The caller supplies the counts.
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
READER_NAMED = "READER_NAMED"
READER_UNNAMED = "READER_UNNAMED"
POPULATION_NAMED = "POPULATION_NAMED"
POPULATION_UNNAMED = "POPULATION_UNNAMED"
COUNTED = "COUNTED"
NOT_COUNTED = "NOT_COUNTED"
SCOPED = "SCOPED"
UNSCOPED = "UNSCOPED"

# The ONLY status at which a finding may be kept.
KEEP_STATUS = SCOPED

# Every terminal status, and whether it keeps the finding.
TERMINAL: dict[str, bool] = {
    READER_UNNAMED: False,
    POPULATION_UNNAMED: False,
    NOT_COUNTED: False,
    UNSCOPED: False,
    SCOPED: True,
}

STEP_NAMES = (
    "NAME THE READER",
    "NAME THE POPULATION",
    "COUNT THE POPULATION",
    "COMPARE TO THE TABLE",
)


class UnscopedFinding(ValueError):
    """Raised when a finding is written without a SCOPED status."""


# ---------------------------------------------------------------------------
# The four steps. Each returns (status, detail) and each CAN fail.
# ---------------------------------------------------------------------------

def name_reader(reader: Any, cite: str = "") -> tuple[str, str]:
    """Step 1 — the reader is a function / query / endpoint, cited `path:line`.

    A finding with no reader has no population to be right or wrong about, so
    it cannot be checked at all. That is `READER_UNNAMED`, not a pass.
    """
    if not str(reader or "").strip():
        return READER_UNNAMED, "no reader named — nothing to scope the count to"
    if not str(cite or "").strip():
        return READER_UNNAMED, "reader %r named but not cited" % reader
    return READER_NAMED, "%s (%s)" % (reader, cite)


def name_population(population: Any, cite: str = "") -> tuple[str, str]:
    """Step 2 — the population is the READER'S OWN filter, cited.

    The population is not "the table" and not "everything". It is the set the
    reader returns. If the filter is not named, the count that follows is a
    count of something else.
    """
    if not str(population or "").strip():
        return POPULATION_UNNAMED, (
            "no population named — a count without a filter counts the TABLE, "
            "not the LIST")
    if not str(cite or "").strip():
        return POPULATION_UNNAMED, "population %r named but not cited" % population
    return POPULATION_NAMED, "%s (%s)" % (population, cite)


def count_population(count: Any, command: str = "") -> tuple[str, str]:
    """Step 3 — the count ran against the reader's filter, command cited.

    A count with no command is a remembered number, not a measurement.
    """
    if count is None:
        return NOT_COUNTED, "no count supplied"
    if not str(command or "").strip():
        return NOT_COUNTED, "count %r supplied but no command cited" % (count,)
    return COUNTED, "%s (via %s)" % (count, command)


def compare_to_table(
    population_count: Any,
    table_count: Any,
    *,
    population_stated: bool,
) -> tuple[str, str]:
    """Step 4 — BOTH counts present; the finding must state the population.

    ALWAYS runs, even when the two counts are EQUAL. A check that only runs on
    the differing case never runs on the easy case, and a check that never runs
    cannot fail.

    * both counts present and EQUAL      -> SCOPED (the table IS the list)
    * both counts present and DIFFERENT  -> SCOPED only if the finding STATES
                                            which population it counted
    * either count missing               -> UNSCOPED
    """
    if population_count is None or table_count is None:
        return UNSCOPED, (
            "both counts are required: population=%r table=%r"
            % (population_count, table_count))
    if population_count == table_count:
        return SCOPED, (
            "population == table (%s) — the table IS the list here"
            % (population_count,))
    if not population_stated:
        return UNSCOPED, (
            "population %s != table %s and the finding does NOT state which "
            "population it counted — this is the 2026-09-27 defect"
            % (population_count, table_count))
    return SCOPED, (
        "population %s != table %s and the finding STATES the population"
        % (population_count, table_count))


# ---------------------------------------------------------------------------
# The whole scope, as one call
# ---------------------------------------------------------------------------

def scope_finding(
    *,
    reader: Any = "",
    reader_cite: str = "",
    population: Any = "",
    population_cite: str = "",
    population_count: Any = None,
    table_count: Any = None,
    count_command: str = "",
    population_stated: bool = False,
) -> dict[str, Any]:
    """Run the four steps in order and return the scope.

    Steps SHORT-CIRCUIT: once a step fails, the later steps are not run, because
    a count of an unnamed population is not a measurement. The status is the
    FIRST failing step's status, so the report names the earliest thing to fix.
    """
    steps: list[dict[str, Any]] = []

    status, detail = name_reader(reader, reader_cite)
    steps.append({"n": 1, "step": STEP_NAMES[0], "status": status, "detail": detail})
    if status != READER_NAMED:
        return _result(steps, status)

    status, detail = name_population(population, population_cite)
    steps.append({"n": 2, "step": STEP_NAMES[1], "status": status, "detail": detail})
    if status != POPULATION_NAMED:
        return _result(steps, status)

    status, detail = count_population(population_count, count_command)
    steps.append({"n": 3, "step": STEP_NAMES[2], "status": status, "detail": detail})
    if status != COUNTED:
        return _result(steps, status)

    status, detail = compare_to_table(
        population_count, table_count, population_stated=population_stated)
    steps.append({"n": 4, "step": STEP_NAMES[3], "status": status, "detail": detail})
    return _result(steps, status)


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

def assert_scoped(scope: dict[str, Any], *, cite_ref: str = "") -> None:
    """Raise unless the scope is SCOPED. Call this AT THE WRITE SITE.

    A gate that is never called at the write site is not a gate. This is the
    same placement rule `citation-discipline` states: the check belongs where
    the finding is written, not where it is read.
    """
    if not isinstance(scope, dict):
        raise UnscopedFinding("scope must be a dict, got %s" % type(scope).__name__)
    if scope.get("status") != KEEP_STATUS:
        raise UnscopedFinding(
            "finding is %s, not %s — DISCARDED, not downgraded. %s"
            % (scope.get("status"), KEEP_STATUS,
               (scope.get("steps") or [{}])[-1].get("detail", "")))
    if not str(cite_ref or "").strip():
        raise UnscopedFinding(
            "a SCOPED finding still needs a citation at the write site")


def partition(findings: Iterable[dict[str, Any]]) -> tuple[list, list]:
    """Split findings into (kept, dropped). A dropped finding is GONE.

    There is no third list and no "low confidence" label: a downgrade would let
    a wrong-population finding keep existing under a new name.
    """
    kept: list = []
    dropped: list = []
    for f in findings:
        scope = f.get("scope") if isinstance(f, dict) else None
        (kept if (scope or {}).get("status") == KEEP_STATUS else dropped).append(f)
    return kept, dropped


# ---------------------------------------------------------------------------
# The 2026-09-27 case, as data — so the proof and the contract share ONE source
# ---------------------------------------------------------------------------

CASE_2026_09_27 = {
    "reader": "working_environment.all_paths()",
    "reader_cite": "working_environment.py — all_paths()",
    "population": "WHERE is_active=1",
    "population_cite": "working_environment.py — all_paths() filter",
    "population_count": 4,
    "table_count": 75,
    "count_command": "SELECT COUNT(*) FROM working_environment WHERE is_active=1",
    "residue_in_population": 0,
    "residue_in_table": 71,
    "the_wrong_claim": "the workspace list is 89% proof residue",
}


def case_2026_09_27(*, population_stated: bool) -> dict[str, Any]:
    """The real case. `population_stated=False` reproduces the wrong claim."""
    c = CASE_2026_09_27
    return scope_finding(
        reader=c["reader"], reader_cite=c["reader_cite"],
        population=c["population"], population_cite=c["population_cite"],
        population_count=c["population_count"], table_count=c["table_count"],
        count_command=c["count_command"], population_stated=population_stated,
    )


if __name__ == "__main__":  # pragma: no cover
    print("=== the 2026-09-27 case, population STATED ===")
    a = case_2026_09_27(population_stated=True)
    for s in a["steps"]:
        print("  %d. %-22s %-18s %s" % (s["n"], s["step"], s["status"], s["detail"]))
    print("  -> status=%s keep=%s" % (a["status"], a["keep"]))

    print()
    print("=== the SAME case, population NOT stated (the wrong claim) ===")
    b = case_2026_09_27(population_stated=False)
    for s in b["steps"]:
        print("  %d. %-22s %-18s %s" % (s["n"], s["step"], s["status"], s["detail"]))
    print("  -> status=%s keep=%s" % (b["status"], b["keep"]))
