# -*- coding: utf-8 -*-
"""test_problem_citation.py — mutation-sensitive tests for the two new gates.

The load-bearing property is NOT "the cases pass". It is: **if the protection is
removed, the cases go RED.** A test suite that stays green when the rule is
deleted certifies nothing — that is the defect `systematic_debug.py` documents
("a rule in a table is not a gate").

So each protection is neutered in-process and the TDD cases are re-run against
the neutered rule. If they still pass, the test is worthless and this file fails.

Run:  .\\.venv\\Scripts\\python.exe test_problem_citation.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, r"C:\projects\agent_system")

import citation_discipline as cd  # noqa: E402
import problem_statement as ps  # noqa: E402
import skill_tdd_runner as runner  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("  [%s] %s  %s" % ("PASS" if cond else "FAIL", name, detail))


def _run(contract_id):
    r = runner.run_contract(contract_id, record=False, verbose=False)
    return r.get("n_passed", 0), r.get("n_cases", 0)


print("=== baseline: both contracts green ===")
ps_passed, ps_total = _run("SKILL.PROBLEM.STATEMENT")
cd_passed, cd_total = _run("SKILL.CITATION.DISCIPLINE")
check("PS contract all green", ps_passed == ps_total and ps_total == 6,
      "%d/%d" % (ps_passed, ps_total))
check("CD contract all green", cd_passed == cd_total and cd_total == 6,
      "%d/%d" % (cd_passed, cd_total))

print("\n=== MUTATION 1: neuter problem_statement.is_analysable ===")
_orig_analysable = ps.is_analysable
try:
    ps.is_analysable = lambda statement: isinstance(statement, dict)  # neutered
    m_passed, m_total = _run("SKILL.PROBLEM.STATEMENT")
    check("neutered rule makes a case go RED", m_passed < m_total,
          "%d/%d (was %d/%d) — the test catches removal"
          % (m_passed, m_total, ps_passed, ps_total))
finally:
    ps.is_analysable = _orig_analysable

print("\n=== MUTATION 2: neuter problem_statement.is_complaint ===")
_orig_complaint = ps.is_complaint
try:
    ps.is_complaint = lambda text: False  # neutered: nothing is a complaint
    m_passed, m_total = _run("SKILL.PROBLEM.STATEMENT")
    check("neutered complaint check makes a case go RED", m_passed < m_total,
          "%d/%d (was %d/%d)" % (m_passed, m_total, ps_passed, ps_total))
finally:
    ps.is_complaint = _orig_complaint

print("\n=== MUTATION 3: neuter citation_discipline.is_citation ===")
_orig_citation = cd.is_citation
try:
    cd.is_citation = lambda ref: bool(str(ref or "").strip())  # neutered
    m_passed, m_total = _run("SKILL.CITATION.DISCIPLINE")
    check("neutered citation check makes a case go RED", m_passed < m_total,
          "%d/%d (was %d/%d)" % (m_passed, m_total, cd_passed, cd_total))
finally:
    cd.is_citation = _orig_citation

print("\n=== MUTATION 4: neuter citation_discipline.keep_finding ===")
_orig_keep = cd.keep_finding
try:
    cd.keep_finding = lambda finding: True  # neutered: keep everything
    m_passed, m_total = _run("SKILL.CITATION.DISCIPLINE")
    check("neutered keep_finding makes a case go RED", m_passed < m_total,
          "%d/%d (was %d/%d)" % (m_passed, m_total, cd_passed, cd_total))
finally:
    cd.keep_finding = _orig_keep

print("\n=== restored: both contracts green again ===")
ps_passed2, ps_total2 = _run("SKILL.PROBLEM.STATEMENT")
cd_passed2, cd_total2 = _run("SKILL.CITATION.DISCIPLINE")
check("PS restored green", ps_passed2 == ps_total2,
      "%d/%d" % (ps_passed2, ps_total2))
check("CD restored green", cd_passed2 == cd_total2,
      "%d/%d" % (cd_passed2, cd_total2))

print("\n=== the gates RAISE at the point of action ===")
try:
    ps.assert_problem_stated({"target": "x"})
    check("PS gate raises", False, "did NOT raise")
except ps.ProblemNotStated:
    check("PS gate raises", True)
try:
    cd.assert_cited({"evidence_ref": "I remember"})
    check("CD gate raises", False, "did NOT raise")
except cd.UncitedFinding:
    check("CD gate raises", True)

print()
passed = sum(1 for _n, ok, _d in RESULTS if ok)
total = len(RESULTS)
print("test_problem_citation: %d/%d checks passed" % (passed, total))
if passed == total:
    print("ALL GREEN — protections are load-bearing, not decorative")
    sys.exit(0)
print("FAILURES PRESENT")
sys.exit(1)
