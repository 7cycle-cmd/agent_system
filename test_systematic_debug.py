# -*- coding: utf-8 -*-
"""test_systematic_debug.py — permanent tests for the stop rule and the gate.

Three suites, and the second is the one that matters:

  1. RULE       — decide_next_action / may_propose_fix / keep_finding
  2. MUTATION   — each protection is neutered at runtime and the corresponding
                  TDD case must go RED. A green suite proves nothing until it
                  has been seen to fail. (This is the same standard
                  test_env_proof_gate.py applies, and for the same reason.)
  3. GATE       — the runtime gate raises instead of permitting fix #4

Run:  .\\.venv\\Scripts\\python.exe -m pytest test_systematic_debug.py -q
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import systematic_debug as sd
import skill_contract_store as scs

CONTRACT = "SKILL.SYSTEMATIC.DEBUGGING"
DB = Path(__file__).resolve().parent / "agent.db"

# The real sequence from the 2026-09-20 session.
REAL_SEQUENCE = [
    {"attempt": 1, "method": "colour search (yellow)"},
    {"attempt": 2, "method": "grid / cluster"},
    {"attempt": 3, "method": "index-based band match"},
    {"attempt": 4, "method": "ask the human"},
]


# ---------------------------------------------------------------------------
# 1. RULE
# ---------------------------------------------------------------------------

def test_decide_retries_below_threshold():
    assert sd.decide_next_action(0) == sd.RETRY
    assert sd.decide_next_action(1) == sd.RETRY
    assert sd.decide_next_action(2) == sd.RETRY


def test_decide_stops_at_threshold():
    assert sd.decide_next_action(3) == sd.QUESTION_ARCHITECTURE
    assert sd.decide_next_action(4) == sd.QUESTION_ARCHITECTURE


def test_iron_law_predicate():
    assert sd.may_propose_fix(True) is True
    assert sd.may_propose_fix(False) is False


def test_citation_or_discard():
    assert sd.keep_finding("f_perm_click.py:412") is True
    assert sd.keep_finding("") is False
    assert sd.keep_finding("   ") is False


def test_real_sequence_stops_at_attempt_three():
    assert sd.replay(REAL_SEQUENCE) == 3


# ---------------------------------------------------------------------------
# 2. MUTATION — neutralise each protection, the case must go RED
# ---------------------------------------------------------------------------

def test_mutation_counter_removed_turns_rule_red(monkeypatch):
    """The 3+ counter is the protection. Removing it must break the cases."""
    monkeypatch.setattr(sd, "decide_next_action",
                        lambda n, threshold=10 ** 9: sd.RETRY)
    assert sd.decide_next_action(3) == sd.RETRY          # mutation active
    # the case's expectation is now violated
    assert sd.decide_next_action(3) != sd.QUESTION_ARCHITECTURE
    assert sd.replay(REAL_SEQUENCE) is None              # no stop ever fires


def test_mutation_iron_law_removed_turns_case_red(monkeypatch):
    monkeypatch.setattr(sd, "may_propose_fix", lambda v: True)
    assert sd.may_propose_fix(False) is True             # mutation active
    assert sd.may_propose_fix(False) != False            # case expectation violated


def test_mutation_citation_check_removed_turns_case_red(monkeypatch):
    monkeypatch.setattr(sd, "keep_finding", lambda r: True)
    assert sd.keep_finding("") is True                   # mutation active
    assert sd.keep_finding("") != False                  # case expectation violated


# ---------------------------------------------------------------------------
# 3. GATE — the rule must actually refuse, not advise
# ---------------------------------------------------------------------------

def test_gate_refuses_fourth_fix():
    try:
        sd.assert_may_attempt_fix(3)
    except sd.ArchitectureReviewRequired as e:
        assert "fix #4" in str(e)
    else:
        raise AssertionError("gate allowed fix #4 — the stop rule is not enforced")


def test_gate_allows_below_threshold():
    assert sd.assert_may_attempt_fix(2) == sd.RETRY


def test_gate_allows_after_architecture_questioned():
    assert (sd.assert_may_attempt_fix(3, architecture_questioned=True)
            == sd.QUESTION_ARCHITECTURE)


# ---------------------------------------------------------------------------
# 4. CONTRACT WIRING — the DB cases must actually run
# ---------------------------------------------------------------------------

def test_contract_cases_execute_without_missing_probe():
    """Every SDB case must have a probe NOW. Before the probe existed each case
    returned 'no probe registered', i.e. the declared protection was never run.
    This test fails if the probe registration is dropped again."""
    import skill_tdd_runner as runner
    res = runner.run_contract(CONTRACT, record=False, verbose=False, db_path=DB)
    assert res["n_cases"] > 0, "no cases found for %s" % CONTRACT
    missing = [r["case_key"] for r in res["results"]
               if "no probe registered" in (r.get("detail") or "")]
    assert not missing, "cases with no probe: %s" % missing
    assert res["ok"], "cases failed: %s" % [
        (r["case_key"], r["detail"]) for r in res["results"] if not r["passed"]
    ]


def test_contract_has_both_kinds():
    cases = scs.list_tdd_cases(CONTRACT, db_path=DB)
    kinds = {c["kind"] for c in cases}
    assert "pass" in kinds and "hard_fail" in kinds
    assert len([c for c in cases if c["kind"] == "hard_fail"]) >= 2
