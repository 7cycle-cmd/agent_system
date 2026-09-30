# -*- coding: utf-8 -*-
"""test_merge_candidate_e2e.py — exercise merge_candidate() END TO END.

WHY THIS FILE EXISTS
--------------------
`test_merge_contract_gate.py` proves `_contract_tdd_gate()` works by calling it
directly, and `merge_candidate()`'s docstring promises that a candidate cannot
be merged while a `hard_fail` case of its contract is failing. That promise was
NEVER executed — only read. A guard that has never run is a claim.

It could not run because `_contract_tdd_gate()` took no `db_path`, so an
end-to-end test on a temp DB would have gated the REAL agent.db. Testing against
production data needs explicit approval and would mutate it. `db_path` is now
threaded through (gate -> list_contracts / list_tdd_cases / run_contract and the
call site in merge_candidate), so this file can run on an isolated DB.

Three cases, and #3 is the load-bearing one:
  1. green contract   -> merge is NOT refused by the contract gate
  2. broken protection-> merge IS refused, error names the gate
  3. the refusal NAMES the failing case (not just "something went wrong")

Run:  .\\.venv\\Scripts\\python.exe -m pytest test_merge_candidate_e2e.py -q
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import skill_contract_store as scs
import skill_learning as sl
import skill_prompt as sp
import systematic_debug as sd

SKILL_KEY = "e2e_debug_skill"
VERSION = "v1_e2e"
CONTRACT_ID = "E2E.SYSTEMATIC.DEBUGGING"


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """An isolated DB with ONE contract whose cases are pure (no I/O)."""
    path = tmp_path / "e2e_agent.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        sp.ensure_skill_tables(conn)          # ssot / case / test_run / lesson ...
        scs.ensure_skill_contract_schema(conn)
        conn.commit()
    finally:
        conn.close()

    # Contract whose taxonomy_path must validate; module/task_center is an
    # ACTIVE registry entity but the registry lives in agent.db, not tmp_path.
    # validate_taxonomy_path() SKIPS validation when the registry is absent
    # (returns ok with a note), so the temp DB is not bricked.
    res = scs.upsert_contract(
        CONTRACT_ID, SKILL_KEY, "module/task_center",
        "Refuse a 4th fix attempt until the architecture is questioned.",
        environment={"read_only": True},
        status="active", db_path=path,
    )
    assert res["ok"], res

    scs.upsert_tdd_case(
        "SDB.e2e.pass.stop_at_three", CONTRACT_ID, "pass",
        "at 3 failed fixes the next action is question_architecture",
        input_payload={"failed_fix_attempts": 3},
        expected={"next_action": "question_architecture"}, db_path=path,
    )
    scs.upsert_tdd_case(
        "SDB.e2e.hard_fail.fourth_fix", CONTRACT_ID, "hard_fail",
        "at 4 failed fixes the outcome MUST still be question_architecture",
        input_payload={"failed_fix_attempts": 4},
        expected={"next_action": "question_architecture"}, db_path=path,
    )
    return path


def _seed_candidate(db_path: Path) -> None:
    """A draft SSOT row + a passing test run (merge guard 1)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        sp.ensure_skill_tables(conn)
        sp.upsert_skill_prompt(
            conn, skill_key=SKILL_KEY, version_label=VERSION,
            prompt_text="e2e candidate text", status="draft", activate=False,
        )
        conn.execute(
            """INSERT INTO skill_prompt_test_run
                 (run_id, skill_key, version_label, prompt_key, n_runs,
                  accuracy_pct, pass_gate, raw_json, source)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("e2e-run-1", SKILL_KEY, VERSION, "main", 10, 100.0, 1, "{}",
             "e2e_test"),
        )
        conn.commit()
    finally:
        conn.close()


def _gold_gate_is_empty(db_path: Path) -> bool:
    g = sl._gold_set_gate(SKILL_KEY, db_path=db_path)
    # No gold cases at all -> applies False, so guard 3 cannot mask guard 2.
    return g["applies"] is False


# ---------------------------------------------------------------------------
# 1. green contract -> gate does not refuse
# ---------------------------------------------------------------------------

def test_green_contract_gate_passes(db: Path):
    assert _gold_gate_is_empty(db), "guard 3 must not participate in this test"
    g = sl._contract_tdd_gate(SKILL_KEY, db_path=db)
    assert g["applies"] is True, g
    assert g["contract_id"] == CONTRACT_ID
    assert g["ok"] is True, g["reason"]


def test_green_contract_merge_not_refused_by_contract_gate(db: Path):
    assert _gold_gate_is_empty(db)
    _seed_candidate(db)
    res = sl.merge_candidate(SKILL_KEY, VERSION, db_path=db)
    # Either it merged, or another guard spoke — but NOT the contract gate.
    if not res.get("ok"):
        assert "contract TDD gate failed" not in str(res.get("error")), res
    assert (res.get("contract_gate") or {}).get("ok") is True, res


# ---------------------------------------------------------------------------
# 2. broken protection -> merge REFUSED (this is the promise being tested)
# ---------------------------------------------------------------------------

def test_broken_protection_refuses_merge(db: Path, monkeypatch):
    assert _gold_gate_is_empty(db)
    _seed_candidate(db)

    # Neuter the protection the hard_fail cases exist to guard: the stop rule
    # never fires, so the case's expected outcome is violated.
    monkeypatch.setattr(sd, "decide_next_action", lambda n, threshold=10 ** 9: sd.RETRY)

    res = sl.merge_candidate(SKILL_KEY, VERSION, db_path=db)
    assert res.get("ok") is False, "merge ran despite a RED hard_fail case: %r" % res
    assert "contract TDD gate failed" in str(res.get("error")), res
    assert (res.get("contract_gate") or {}).get("ok") is False, res


# ---------------------------------------------------------------------------
# 3. the refusal must NAME the failing case
# ---------------------------------------------------------------------------

def test_refusal_names_the_failing_case(db: Path, monkeypatch):
    assert _gold_gate_is_empty(db)
    monkeypatch.setattr(sd, "decide_next_action", lambda n, threshold=10 ** 9: sd.RETRY)
    g = sl._contract_tdd_gate(SKILL_KEY, db_path=db)
    assert g["ok"] is False
    failed = (g.get("detail") or {}).get("failed")
    assert failed, "refusal did not name the failing case(s): %r" % g
    assert "SDB.e2e.hard_fail.fourth_fix" in failed, failed


# ---------------------------------------------------------------------------
# 4. the gate reads the DB IT WAS GIVEN (the isolated-DB property)
# ---------------------------------------------------------------------------

def test_gate_reads_the_supplied_db(tmp_path: Path):
    """On an EMPTY temp DB there is no contract, so the gate must report
    uncovered — proving it did NOT fall back to the real agent.db.

    The skill_key used here is `systematic_debugging`, which DOES have an active
    contract in the real agent.db. That is deliberate: with `e2e_debug_skill`
    the assertion would pass even if the gate ignored db_path, because that key
    does not exist in production either — a test that cannot fail. Using a key
    that really exists makes "applies is False" true ONLY if the supplied DB was
    actually consulted.
    """
    real_key = "systematic_debugging"
    empty = tmp_path / "empty.db"
    conn = sqlite3.connect(str(empty))
    try:
        sp.ensure_skill_tables(conn)
        scs.ensure_skill_contract_schema(conn)
        conn.commit()
    finally:
        conn.close()
    g = sl._contract_tdd_gate(real_key, db_path=empty)
    assert g["applies"] is False, (
        "gate found %r's contract on an EMPTY db — it is reading the real "
        "agent.db, so the isolation this whole file relies on does not exist: %r"
        % (real_key, g)
    )
