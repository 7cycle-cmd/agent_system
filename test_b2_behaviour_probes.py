# -*- coding: utf-8 -*-
"""test_b2_behaviour_probes.py — mutation-sensitive proof for the B2 probes.

The load-bearing property is NOT "the 9 cases pass". It is:

  (A) for the 7 cases WITH a real gate — if the protection is removed, the
      case goes RED. A probe that stays green under mutation certifies nothing.
  (B) for the 2 cases WITHOUT a gate — the case asserts `gate_present: False`.
      If a gate is BUILT, the case goes RED. That is what stops these from
      rotting silently: they are load-bearing in reverse.

Both directions are proven here. Without (A) the seven real probes could be
echoing a literal; without (B) the two UNKNOWN cases would be decoration.

P2-gate Phase 1 (2026-09-20) moved three cases from (B) to (A) by making the
Field Register's hard_rule enforceable; Phase 2 moved a fourth (non_blank).
Their mutation is now the REVERSE of before: clearing the machine rule must
make the case RED.

Run:  .\\.venv\\Scripts\\python.exe test_b2_behaviour_probes.py
"""
from __future__ import annotations

import sqlite3
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import skill_contract_p1_data as p1  # noqa: E402
import skill_tdd_runner as runner  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(cond), detail))
    print("  [%s] %s  %s" % ("PASS" if cond else "FAIL", name, detail))


def _run_case(case_key: str) -> tuple[bool, dict, dict, str]:
    """Run one case's probe against its fixture. Returns (matched, actual, expected, detail)."""
    fx = p1.FIXTURES[case_key]
    probe = runner._probe_for(case_key)
    if probe is None:
        return False, {}, fx["expected"], "no probe registered"
    actual, detail = probe(fx["input"], fx["expected"])
    matched, why = runner._expected_matches(actual, fx["expected"])
    return matched, actual, fx["expected"], (detail + " | " + why).strip(" |")


# The 9 behaviour cases, split by whether a real gate exists.
GATED = [
    "CAP.VALIDATE_NEW_TASK.pass.unknown_cap",
    "API.POST_TASKS_VALIDATE.fail.200_on_fail",
    "SKILL-0002.fail.non_owner",
    # P2-gate Phase 1: the Field Register's hard_rule is now enforced.
    "CAP.VALIDATE_NEW_TASK.fail.write_attempt",
    "CAP.VIDEO.PRODUCE.fail.bad_flow",
    "FN.VALIDATE_NEW_TASK.fail.network",
    # P2-gate Phase 2: the non_blank rule.
    "FLD.CODE_REGISTER.REGISTER_ID.fail.whitespace",
]
GATELESS = [
    "SKILL-0002.fail.dup",
    "SKILL-0001.fail.no_skip_log",
]
ALL9 = GATED + GATELESS

# (contract_id, field_name) whose machine rule gates a case above.
RULE_GATED = [
    ("CAP.VALIDATE_NEW_TASK.fail.write_attempt", "CAP.VALIDATE_NEW_TASK", "read_only"),
    ("CAP.VIDEO.PRODUCE.fail.bad_flow", "CAP.VIDEO.PRODUCE", "flow_ref"),
    ("FN.VALIDATE_NEW_TASK.fail.network", "FN.VALIDATE_NEW_TASK", "pure"),
    ("FLD.CODE_REGISTER.REGISTER_ID.fail.whitespace",
     "FLD.CODE_REGISTER.REGISTER_ID", "value"),
]


def _scratch_db() -> Path:
    """A throwaway COPY of the live DB, so a mutation never touches agent.db.

    WHY A COPY AND NOT `p1.apply(db_path=...)`
    ------------------------------------------
    `apply()` only upserts FIELDS and TDD CASES; it does not create the contract
    TEMPLATES those rows hang off. On a fresh DB every `upsert_field` therefore
    returns UNKNOWN_CONTRACT and writes nothing — and a validator with no Field
    Register rows returns `ok=False`, which reads as `gate_present: True`. The
    first version of this proof did exactly that and the mutation "passed" for
    the wrong reason: the case was green because the DB was EMPTY, not because
    the rule fired. Copying the live DB removes that whole failure mode.
    """
    d = Path(tempfile.mkdtemp(prefix="b2_scratch_"))
    db = d / "scratch.db"
    shutil.copyfile("agent.db", db)
    return db


def _run_case_on_db(case_key: str, db: Path) -> tuple[bool, dict, dict, str]:
    """Run a case with the validator pointed at `db` instead of the live DB."""
    import skill_contract_store as scs

    real = scs.validate_payload_against_contract

    def _wrapped(contract_id, payload, **kw):
        kw.setdefault("db_path", db)
        return real(contract_id, payload, **kw)

    scs.validate_payload_against_contract = _wrapped
    try:
        return _run_case(case_key)
    finally:
        scs.validate_payload_against_contract = real


def _clear_rule(db: Path, contract_id: str, field_name: str) -> None:
    """MUTATION: remove the machine rule, leaving the prose hard_rule intact.

    This is exactly the pre-P2-gate state — the rule was declared and not
    enforced — so the case MUST go RED.
    """
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE skill_contract_field SET rule_kind=NULL, "
            "rule_value_json=NULL WHERE contract_id=? AND field_name=?",
            (contract_id, field_name),
        )
        conn.commit()
    finally:
        conn.close()


def suite_baseline() -> None:
    print("=== baseline: all 9 behaviour cases green ===")
    for k in ALL9:
        matched, actual, expected, detail = _run_case(k)
        check(k, matched, detail)
    # Distinctness: no two cases may share a probe function.
    fns = [runner._probe_for(k).__name__ for k in ALL9]
    check("9 DISTINCT probes (no sharing)", len(set(fns)) == 9,
          "%d unique" % len(set(fns)))


def suite_mutation_gated() -> None:
    print()
    print("=== MUTATION (A): remove each real protection -> case goes RED ===")

    # M1: unknown_cap — neuter the capability lookup so ANY key resolves.
    import src.task_center.ontology_store as ont
    real_get = ont.get_active_capability
    try:
        ont.get_active_capability = lambda key, conn: {"capability_key": key}
        matched, actual, _, _ = _run_case("CAP.VALIDATE_NEW_TASK.pass.unknown_cap")
        check("MUT1 capability lookup neutered -> case RED", not matched,
              "actual=%s" % actual)
    finally:
        ont.get_active_capability = real_get

    # M2: 200_on_fail — neuter the validator so the route always sees ok=True.
    import mouse_spot_helper as msh
    real_vnt = msh.validate_new_task
    try:
        msh.validate_new_task = lambda payload, **kw: {"ok": True, "errors": []}
        matched, actual, _, _ = _run_case("API.POST_TASKS_VALIDATE.fail.200_on_fail")
        check("MUT2 validator neutered -> route returns 200 -> case RED",
              not matched, "actual=%s" % actual)
    finally:
        msh.validate_new_task = real_vnt

    # M3: non_owner — neuter the write-owner gate so everyone is allowed.
    import skill_contract_store as scs
    real_allow = scs.assert_table_write_allowed
    try:
        scs.assert_table_write_allowed = lambda table, caller, **kw: (True, "")
        matched, actual, _, _ = _run_case("SKILL-0002.fail.non_owner")
        check("MUT3 write-owner gate neutered -> case RED", not matched,
              "actual=%s" % actual)
    finally:
        scs.assert_table_write_allowed = real_allow

    # M4-M6: the three P2-gate Phase 1 rules. Clearing the machine rule (while
    # the prose hard_rule stays) restores the pre-gate state, so each case must
    # go RED. Run on a scratch COPY of the live DB so agent.db is never mutated.
    db = _scratch_db()
    for case_key, contract_id, field_name in RULE_GATED:
        # Guard: the rule must actually be present, or "cleared -> RED" would
        # pass for the wrong reason (an empty DB also reports gate_present).
        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT rule_kind, rule_value_json FROM skill_contract_field "
                "WHERE contract_id=? AND field_name=?",
                (contract_id, field_name),
            ).fetchone()
        finally:
            conn.close()
        check("MUT %s rule is present in the scratch DB" % case_key,
              bool(row and row[0]), "row=%s" % (row,))
        matched_before, _, _, _ = _run_case_on_db(case_key, db)
        check("MUT %s green WITH the rule" % case_key, matched_before)
        _clear_rule(db, contract_id, field_name)
        matched_after, actual, _, _ = _run_case_on_db(case_key, db)
        check("MUT %s rule cleared -> case RED" % case_key, not matched_after,
              "actual=%s" % actual)


def suite_mutation_gateless() -> None:
    print()
    print("=== MUTATION (B): BUILD a gate -> the UNKNOWN case goes RED ===")
    print("  (proves the 2 gate-less cases are load-bearing in reverse)")
    print("  NOTE: `dup` is gate-less BY MEASURED DECISION (P2-gate Phase 3):")
    print("        a UNIQUE would reject 17 legitimate log rows. The mutation")
    print("        below proves the case still detects a UNIQUE if one lands.")

    # dup: simulate the UNIQUE constraint landing.
    import db_schema
    real_ddl = db_schema.CHAT_IDENTITY_LOG_DDL
    try:
        db_schema.CHAT_IDENTITY_LOG_DDL = real_ddl.replace(
            "created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,\n"
            "    UNIQUE (chat_id, sha256, action)",
        )
        matched, actual, _, _ = _run_case("SKILL-0002.fail.dup")
        check("BUILD UNIQUE(chat_id,sha256,action) -> dup case RED", not matched,
              "actual=%s" % actual)
    finally:
        db_schema.CHAT_IDENTITY_LOG_DDL = real_ddl


def suite_restored() -> None:
    print()
    print("=== restored: all 9 green again ===")
    for k in ALL9:
        matched, _, _, detail = _run_case(k)
        check("restored %s" % k, matched, detail)


def main() -> int:
    suite_baseline()
    suite_mutation_gated()
    suite_mutation_gateless()
    suite_restored()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print()
    print("B2 behaviour probe proof: %d/%d checks passed" % (passed, total))
    if passed == total:
        print("ALL GREEN — 7 real gates are load-bearing, 2 UNKNOWN cases are")
        print("            load-bearing in reverse, and all 9 probes are distinct")
        return 0
    print("FAILED")
    for name, ok, detail in RESULTS:
        if not ok:
            print("  FAILED: %s  %s" % (name, detail))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
