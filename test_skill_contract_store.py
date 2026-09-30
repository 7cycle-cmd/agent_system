"""Tests for skill_contract_store — DB-driven Skill Contract SSOT.

Covers: schema idempotency, contract round-trip, Field Register hard rules,
TDD case kinds, streak pass/reset arithmetic, review log, and the joined view.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

import skill_contract_store as scs


@pytest.fixture()
def db(tmp_path: Path) -> Path:
    return tmp_path / "test_agent.db"


@pytest.fixture()
def seeded(db: Path) -> Path:
    """One active contract with a Field Register + TDD cases."""
    scs.upsert_contract(
        "SKILL-0001",
        "register_chat_identity",
        "Task/chat-identity-log/capability:identity",
        "Compute and validate the chat sha256 hash.",
        environment={"db": "sqlite", "table": "chat_identity_log",
                     "ssot": "F-001..F-007", "depends_on": ["SKILL-0002"]},
        purpose_not_responsible="NOT responsible for writing the log in Chat Center flow.",
        flow=["pre-research: lookup Field Register", "validate payload",
              "compute sha256", "check skip_log", "return {record_id,status,msg}"],
        not_to_do=["DO NOT bypass Field Register lookup",
                   "DO NOT write chat_identity_log in Chat Center flow"],
        status="active",
        version=1,
        source="manual",
        enforce_taxonomy=False, allow_taxonomy_override=True,
        db_path=db,
    )
    scs.upsert_field(
        "SKILL-0001", "chat_id", "string", "must be UUID v4, non-empty",
        field_id="F-001", mandatory=True, immutable=True, owner_skill_id="SKILL-0001",
        db_path=db,
    )
    scs.upsert_field(
        "SKILL-0001", "action", "string", "only resolve/register allowed",
        field_id="F-003", mandatory=True, enum=["resolve", "register"],
        owner_skill_id="SKILL-0002", db_path=db,
    )
    scs.upsert_field(
        "SKILL-0001", "sha256_hash", "string", "64 hex chars, lowercase",
        field_id="F-005", mandatory=True, immutable=True, db_path=db,
    )
    scs.upsert_field(
        "SKILL-0001", "metadata", "json", "registered meta keys only",
        field_id="F-006", mandatory=False, db_path=db,
    )
    scs.upsert_tdd_case(
        "SKILL-0001.pass.skip_log", "SKILL-0001", "pass",
        "Chat Center call with skip_log=True -> hash generated, no DB write",
        input_payload={"skip_log": True}, expected={"db_write": False}, db_path=db,
    )
    scs.upsert_tdd_case(
        "SKILL-0001.fail.missing_trace", "SKILL-0001", "hard_fail",
        "Missing trace_id -> reject immediately",
        input_payload={}, expected={"ok": False}, db_path=db,
    )
    return db


# --------------------------------------------------------------- schema


def test_schema_is_idempotent(db: Path):
    conn = sqlite3.connect(db)
    first = scs.ensure_skill_contract_schema(conn)
    second = scs.ensure_skill_contract_schema(conn)
    conn.close()
    assert first["ok"] and second["ok"]
    assert first["tables"] == second["tables"]
    assert "skill_contract_template" in first["tables"]
    assert "skill_contract_field" in first["tables"]
    assert "skill_contract_tdd_case" in first["tables"]
    assert "skill_contract_streak" in first["tables"]
    assert "skill_contract_review_log" in first["tables"]
    assert "v_skill_contract" in first["views"]


def test_schema_does_not_touch_task_center_skill_template(db: Path):
    """The pre-existing task-center `skill_template` must not be created/renamed."""
    conn = sqlite3.connect(db)
    scs.ensure_skill_contract_schema(conn)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name='skill_template'"
    ).fetchone()
    conn.close()
    assert row is None


# --------------------------------------------------------------- contract


def test_upsert_contract_insert_then_update(seeded: Path):
    res = scs.upsert_contract(
        "SKILL-0001", "register_chat_identity", "Task/x", "Updated purpose",
        status="active", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=seeded,
    )
    assert res["ok"] and res["action"] == "updated"
    got = scs.get_contract("SKILL-0001", db_path=seeded)
    assert got["purpose"] == "Updated purpose"


def test_contract_json_columns_round_trip(seeded: Path):
    got = scs.get_contract("SKILL-0001", db_path=seeded)
    assert got["environment"]["table"] == "chat_identity_log"
    assert got["flow"][0].startswith("pre-research")
    assert len(got["not_to_do"]) == 2
    assert got["purpose_not_responsible"].startswith("NOT responsible")


def test_upsert_contract_rejects_bad_status(db: Path):
    res = scs.upsert_contract(
        "S1", "k", "t", "p", status="bogus", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db
    )
    assert res["ok"] is False and res["code"] == "BAD_STATUS"


def test_upsert_contract_rejects_missing_purpose(db: Path):
    res = scs.upsert_contract(
        "S1", "k", "t", "   ", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db
    )
    assert res["ok"] is False and res["code"] == "MISSING_PURPOSE"


def test_get_contract_missing_returns_none(db: Path):
    assert scs.get_contract("NOPE", db_path=db) is None


# --------------------------------------------------------------- field register


def test_upsert_field_requires_contract(seeded: Path):
    res = scs.upsert_field("NOPE", "x", "string", "rule", db_path=seeded)
    assert res["ok"] is False and res["code"] == "UNKNOWN_CONTRACT"


def test_upsert_field_rejects_empty_hard_rule(seeded: Path):
    res = scs.upsert_field("SKILL-0001", "x", "string", "  ", db_path=seeded)
    assert res["ok"] is False and res["code"] == "MISSING_HARD_RULE"


def test_list_fields_returns_enum(seeded: Path):
    fields = {f["field_name"]: f for f in scs.list_fields("SKILL-0001", db_path=seeded)}
    assert fields["action"]["enum"] == ["resolve", "register"]
    assert fields["chat_id"]["immutable"] == 1
    assert fields["metadata"]["mandatory"] == 0


# --------------------------------------------------------------- hard validation


def test_validate_payload_accepts_valid(seeded: Path):
    ok, errors = scs.validate_payload_against_contract(
        "SKILL-0001",
        {"chat_id": "abc", "action": "resolve", "sha256_hash": "deadbeef"},
        db_path=seeded,
    )
    assert ok is True, errors
    assert errors == []


def test_validate_payload_rejects_missing_mandatory(seeded: Path):
    ok, errors = scs.validate_payload_against_contract(
        "SKILL-0001", {"action": "resolve"}, db_path=seeded
    )
    assert ok is False
    assert any("Missing required field: chat_id" in e for e in errors)


def test_validate_payload_rejects_type_mismatch(seeded: Path):
    ok, errors = scs.validate_payload_against_contract(
        "SKILL-0001",
        {"chat_id": 123, "action": "resolve", "sha256_hash": "x"},
        db_path=seeded,
    )
    assert ok is False
    assert any("type mismatch" in e for e in errors)


def test_validate_payload_rejects_enum_violation(seeded: Path):
    ok, errors = scs.validate_payload_against_contract(
        "SKILL-0001",
        {"chat_id": "abc", "action": "test", "sha256_hash": "x"},
        db_path=seeded,
    )
    assert ok is False
    assert any("not in enum" in e for e in errors)


def test_validate_payload_rejects_unregistered_field(seeded: Path):
    ok, errors = scs.validate_payload_against_contract(
        "SKILL-0001",
        {"chat_id": "abc", "action": "resolve", "sha256_hash": "x", "remark": "hi"},
        db_path=seeded,
    )
    assert ok is False
    assert any("Unregistered field" in e for e in errors)


def test_validate_payload_rejects_immutable_change(seeded: Path):
    ok, errors = scs.validate_payload_against_contract(
        "SKILL-0001",
        {"chat_id": "NEW", "action": "resolve", "sha256_hash": "x"},
        current={"chat_id": "OLD"},
        db_path=seeded,
    )
    assert ok is False
    assert any("immutable" in e for e in errors)


def test_validate_payload_unknown_contract(seeded: Path):
    ok, errors = scs.validate_payload_against_contract("NOPE", {}, db_path=seeded)
    assert ok is False
    assert any("no Field Register rows" in e for e in errors)


# --------------------------------------------------------------- tdd cases


def test_upsert_tdd_case_rejects_bad_kind(seeded: Path):
    res = scs.upsert_tdd_case("k1", "SKILL-0001", "maybe", "a", db_path=seeded)
    assert res["ok"] is False and res["code"] == "BAD_KIND"


def test_list_tdd_cases_by_kind(seeded: Path):
    fails = scs.list_tdd_cases("SKILL-0001", kind="hard_fail", db_path=seeded)
    assert len(fails) == 1
    assert fails[0]["case_key"] == "SKILL-0001.fail.missing_trace"
    assert fails[0]["expected"] == {"ok": False}


# --------------------------------------------------------------- streak


def test_streak_increments_on_pass(seeded: Path):
    r1 = scs.record_streak_result(
        "SKILL-0001", "SKILL-0001.pass.skip_log", True, db_path=seeded
    )
    r2 = scs.record_streak_result(
        "SKILL-0001", "SKILL-0001.pass.skip_log", True, db_path=seeded
    )
    assert r1["current_streak"] == 1 and r1["total_runs"] == 1
    assert r2["current_streak"] == 2 and r2["best_streak"] == 2
    assert r2["reset_count"] == 0
    assert r2["qualified"] is False


def test_streak_resets_on_fail(seeded: Path):
    for _ in range(3):
        scs.record_streak_result(
            "SKILL-0001", "SKILL-0001.pass.skip_log", True, db_path=seeded
        )
    res = scs.record_streak_result(
        "SKILL-0001", "SKILL-0001.fail.missing_trace", False, db_path=seeded
    )
    assert res["current_streak"] == 0
    assert res["best_streak"] == 3
    assert res["reset_count"] == 1
    assert res["total_runs"] == 4
    assert res["result"] == "fail"


def test_streak_qualified_at_target(seeded: Path):
    res = None
    for _ in range(3):
        res = scs.record_streak_result(
            "SKILL-0001", "SKILL-0001.pass.skip_log", True,
            target_streak=3, db_path=seeded,
        )
    assert res["qualified"] is True
    assert res["current_streak"] == 3


def test_streak_row_is_upserted_not_duplicated(seeded: Path):
    for _ in range(5):
        scs.record_streak_result(
            "SKILL-0001", "SKILL-0001.pass.skip_log", True, db_path=seeded
        )
    conn = sqlite3.connect(seeded)
    n = conn.execute(
        "SELECT count(*) FROM skill_contract_streak WHERE contract_id='SKILL-0001'"
    ).fetchone()[0]
    conn.close()
    assert n == 1


def test_streak_separate_rule_versions(seeded: Path):
    scs.record_streak_result(
        "SKILL-0001", "c1", True, rule_version=1, db_path=seeded
    )
    scs.record_streak_result(
        "SKILL-0001", "c1", False, rule_version=2, db_path=seeded
    )
    v1 = scs.get_streak("SKILL-0001", rule_version=1, db_path=seeded)
    v2 = scs.get_streak("SKILL-0001", rule_version=2, db_path=seeded)
    assert v1["current_streak"] == 1
    assert v2["current_streak"] == 0 and v2["reset_count"] == 1


def test_streak_unknown_contract(seeded: Path):
    res = scs.record_streak_result("NOPE", "c1", True, db_path=seeded)
    assert res["ok"] is False and res["code"] == "UNKNOWN_CONTRACT"


# --------------------------------------------------------------- review log


def test_review_log_append_and_read(seeded: Path):
    res = scs.append_review_log(
        "SKILL-0001",
        gap="Old worker omitted skip_log, caused double write with SKILL-0002.",
        revision="Add hard check to block Chat Center call unless skip_log=True.",
        streak_result="92/100, failed -> reset streak",
        linked_trace_id="trace-abc",
        chat_id="cid-8811",
        task_id="task-20260919-chat-identity-log",
        db_path=seeded,
    )
    assert res["ok"] is True
    logs = scs.list_review_logs("SKILL-0001", db_path=seeded)
    assert len(logs) == 1
    assert logs[0]["gap"].startswith("Old worker omitted")
    assert logs[0]["linked_trace_id"] == "trace-abc"
    assert logs[0]["chat_id"] == "cid-8811"


def test_review_log_unknown_contract(seeded: Path):
    res = scs.append_review_log("NOPE", gap="x", db_path=seeded)
    assert res["ok"] is False and res["code"] == "UNKNOWN_CONTRACT"


# --------------------------------------------------------------- view


def test_view_joins_template_and_streak(seeded: Path):
    scs.record_streak_result(
        "SKILL-0001", "SKILL-0001.pass.skip_log", True, db_path=seeded
    )
    rows = scs.list_contracts(db_path=seeded)
    assert len(rows) == 1
    row = rows[0]
    assert row["contract_id"] == "SKILL-0001"
    assert row["current_streak"] == 1
    assert row["target_streak"] == 100
    assert row["streak_qualified"] == 0
    assert row["environment"]["table"] == "chat_identity_log"


def test_view_without_streak_row(seeded: Path):
    rows = scs.list_contracts(db_path=seeded)
    assert rows[0]["current_streak"] is None
    assert rows[0]["streak_qualified"] == 0


def test_list_contracts_filter_by_status(seeded: Path):
    scs.upsert_contract("SKILL-0002", "log_center", "Task/x", "Persist log",
                        status="draft", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=seeded)
    active = scs.list_contracts(status="active", db_path=seeded)
    draft = scs.list_contracts(status="draft", db_path=seeded)
    assert [c["contract_id"] for c in active] == ["SKILL-0001"]
    assert [c["contract_id"] for c in draft] == ["SKILL-0002"]


# --------------------------------------------------------------- cascade


def test_delete_contract_cascades(db: Path):
    scs.upsert_contract("S1", "k", "t", "p", db_path=db)
    scs.upsert_field("S1", "f", "string", "rule", db_path=db)
    scs.upsert_tdd_case("c1", "S1", "pass", "a", db_path=db)
    scs.record_streak_result("S1", "c1", True, db_path=db)
    scs.append_review_log("S1", gap="g", db_path=db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM skill_contract_template WHERE contract_id='S1'")
    conn.commit()
    for table in ("skill_contract_field", "skill_contract_tdd_case",
                  "skill_contract_streak", "skill_contract_review_log"):
        n = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert n == 0, f"{table} not cascaded"
    conn.close()


# --------------------------------------------------------------- taxonomy


def test_parse_taxonomy_path_canonical():
    assert scs.parse_taxonomy_path("channel/local_pc") == ("channel", "local_pc")
    assert scs.parse_taxonomy_path("db_field/code_registry|register_id") == (
        "db_field", "code_registry|register_id"
    )


def test_parse_taxonomy_path_aliases():
    assert scs.parse_taxonomy_path("table/code_registry") == ("db_table", "code_registry")
    assert scs.parse_taxonomy_path("field/code_registry|register_id") == (
        "db_field", "code_registry|register_id"
    )
    assert scs.parse_taxonomy_path("能力/media/ppt") == ("capability", "media/ppt")


def test_parse_taxonomy_path_rejects_bad():
    assert scs.parse_taxonomy_path("") is None
    assert scs.parse_taxonomy_path("no-slash") is None
    assert scs.parse_taxonomy_path("bogus/thing") is None
    assert scs.parse_taxonomy_path("channel/") is None


def test_validate_taxonomy_path_rejects_non_canonical(db: Path):
    ok, errors = scs.validate_taxonomy_path("Task/chat-identity-log", db_path=db)
    assert ok is False
    assert any("not canonical" in e for e in errors)


def test_validate_taxonomy_path_skips_when_registry_absent(db: Path):
    """A DB without the ontology registry must not be bricked."""
    ok, errors = scs.validate_taxonomy_path("channel/local_pc", db_path=db)
    assert ok is True
    assert any("skipped" in e for e in errors)


def test_upsert_contract_rejects_bad_taxonomy(db: Path):
    res = scs.upsert_contract("S1", "k", "not-a-path", "p", db_path=db)
    assert res["ok"] is False and res["code"] == "BAD_TAXONOMY_PATH"


def test_upsert_contract_can_skip_taxonomy_enforcement(db: Path):
    res = scs.upsert_contract(
        "S1", "k", "not-a-path", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db
    )
    assert res["ok"] is True


# --------------------------------------------------------------- write owner


def test_find_write_owner(db: Path):
    scs.upsert_contract(
        "SKILL-0002", "log_center", "t", "p", write_owner=True,
        environment={"table": "chat_identity_log"}, enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    scs.upsert_contract(
        "SKILL-0001", "hash", "t", "p", write_owner=False,
        environment={"table": "chat_identity_log"}, enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    owners = scs.find_write_owner("chat_identity_log", db_path=db)
    assert [o["contract_id"] for o in owners] == ["SKILL-0002"]


def test_assert_table_write_allowed_owner_passes(db: Path):
    scs.upsert_contract(
        "SKILL-0002", "log_center", "t", "p", write_owner=True,
        environment={"table": "chat_identity_log"}, enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    ok, reason = scs.assert_table_write_allowed(
        "chat_identity_log", "SKILL-0002", db_path=db
    )
    assert ok is True and reason == ""


def test_assert_table_write_allowed_non_owner_rejected(db: Path):
    scs.upsert_contract(
        "SKILL-0002", "log_center", "t", "p", write_owner=True,
        environment={"table": "chat_identity_log"}, enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    ok, reason = scs.assert_table_write_allowed(
        "chat_identity_log", "SKILL-0001", db_path=db
    )
    assert ok is False
    assert "not the authorized write entry" in reason


def test_assert_table_write_allowed_unclaimed_table(db: Path):
    ok, reason = scs.assert_table_write_allowed("nobody_owns_this", "X", db_path=db)
    assert ok is True and reason == ""


# --------------------------------------------------------------- streak gate


def test_is_streak_qualified_false_without_row(db: Path):
    scs.upsert_contract("S1", "k", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    assert scs.is_streak_qualified("S1", db_path=db) is False


def test_is_streak_qualified_true_at_target(db: Path):
    """The COUNT half of the qualification.

    `is_streak_qualified` has TWO conditions since 2026-09-21: the streak must
    reach the target AND the contract must have a dedicated probe. This test
    covers the COUNTER, so it explicitly opts out of the probe condition — a
    contract with no TDD cases has no probe by definition, and asserting
    `is True` without saying so would silently re-define the test to be about
    the probe.
    """
    scs.upsert_contract("S1", "k", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    for _ in range(3):
        scs.record_streak_result("S1", "c1", True, target_streak=3, db_path=db)
    assert scs.is_streak_qualified(
        "S1", db_path=db, require_dedicated_probe=False) is True


def test_is_streak_qualified_requires_a_dedicated_probe(db: Path):
    """The EVIDENCE half: reaching the target is not sufficient.

    Measured 2026-09-21: 4 of 18 real contracts have no dedicated probe, so
    `_probe_for` falls back to `_probe_payload_rule`, which validates the payload
    against the SAME contract's Field Register. A long streak there is a number
    about the test harness, not a proof about the code.
    """
    scs.upsert_contract("S2", "k", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    for _ in range(3):
        scs.record_streak_result("S2", "c1", True, target_streak=3, db_path=db)
    # the counter alone says yes, and that is the DEFAULT (unchanged behaviour)
    assert scs.is_streak_qualified("S2", db_path=db) is True
    # ...but with no TDD cases there is no probe, so it is NOT evidence
    assert scs.has_dedicated_probe("S2", db_path=db) is False
    assert scs.is_streak_qualified(
        "S2", db_path=db, require_dedicated_probe=True) is False
    ev = scs.streak_evidence("S2", db_path=db)
    assert ev["state"] == "counting_only"
    assert "NOTHING about the code" in ev["proves"]


def test_assert_streak_qualified_rejects_unqualified(db: Path):
    scs.upsert_contract("S1", "k", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    ok, reason = scs.assert_streak_qualified("S1", db_path=db)
    assert ok is False
    assert "not streak-qualified" in reason


# --------------------------------------------------------------- version bump


def test_bump_rule_version_resets_streak(db: Path):
    scs.upsert_contract("S1", "k", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    for _ in range(3):
        scs.record_streak_result("S1", "c1", True, db_path=db)
    assert scs.is_streak_qualified("S1", db_path=db) is False  # target 100

    res = scs.bump_rule_version("S1", reason="field rule changed", db_path=db)
    assert res["ok"] is True and res["new_version"] == 2

    # old streak row is preserved for history
    old = scs.get_streak("S1", rule_version=1, db_path=db)
    assert old["current_streak"] == 3
    # new version has no streak yet -> not qualified
    assert scs.get_streak("S1", rule_version=2, db_path=db) is None
    assert scs.is_streak_qualified("S1", db_path=db) is False


def test_bump_rule_version_unknown_contract(db: Path):
    res = scs.bump_rule_version("NOPE", db_path=db)
    assert res["ok"] is False and res["code"] == "UNKNOWN_CONTRACT"


# --------------------------------------------------------------- 8-level seed


def test_eight_level_seed_covers_all_levels(db: Path):
    """A bare DB has no ontology registry, so taxonomy checks are skipped and
    all 7 representative contracts are written (one per taxonomy level)."""
    res = scs.seed_eight_level_contracts(db_path=db)
    assert res["ok"] is True
    assert res["total"] == 7
    assert res["inserted"] == 7
    assert res["rejected"] == []
    rows = scs.list_contracts(db_path=db)
    assert len(rows) == 7
    levels = {scs.parse_taxonomy_path(r["taxonomy_path"])[0] for r in rows}
    assert levels == {
        "channel", "module", "capability", "api",
        "function", "db_table", "db_field",
    }


def test_eight_level_seed_is_idempotent(db: Path):
    scs.seed_eight_level_contracts(db_path=db)
    again = scs.seed_eight_level_contracts(db_path=db)
    assert again["ok"] is True
    assert again["inserted"] == 0


def test_eight_level_seed_specs_are_canonical():
    for spec in scs.EIGHT_LEVEL_SEED:
        parsed = scs.parse_taxonomy_path(spec["taxonomy_path"])
        assert parsed is not None, spec["taxonomy_path"]
        assert parsed[0] in scs.TAXONOMY_ENTITY_TYPES


# --------------------------------------------------------------- depends_on


def test_get_depends_on_reads_list(db: Path):
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": ["B", "C"]},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    assert scs.get_depends_on("A", db_path=db) == ["B", "C"]


def test_get_depends_on_reads_string(db: Path):
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": "B"},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    assert scs.get_depends_on("A", db_path=db) == ["B"]


def test_get_depends_on_empty_when_absent(db: Path):
    scs.upsert_contract("A", "a", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    assert scs.get_depends_on("A", db_path=db) == []


def test_get_depends_on_unknown_contract(db: Path):
    assert scs.get_depends_on("NOPE", db_path=db) == []


def test_check_dependencies_missing_dep_is_error(db: Path):
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": ["GHOST"]},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    res = scs.check_dependencies("A", db_path=db)
    assert res["ok"] is False
    assert any("does not exist" in e for e in res["errors"])
    assert res["deps"][0]["exists"] is False


def test_check_dependencies_unqualified_dep_is_error(db: Path):
    scs.upsert_contract("B", "b", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": ["B"]},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    res = scs.check_dependencies("A", db_path=db)
    assert res["ok"] is False
    assert any("not streak-qualified" in e for e in res["errors"])


def test_check_dependencies_qualified_dep_passes(db: Path):
    """A dependency that IS qualified lets the dependent through.

    The dependency's qualification must satisfy the SAME rule the caller uses,
    so the setup has to produce real evidence, not just a large counter. That is
    why this explicitly opts out of the probe condition and asserts the counter
    separately — the point of the test is the DEPENDENCY logic, not the probe.
    """
    scs.upsert_contract("B", "b", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    for _ in range(3):
        scs.record_streak_result("B", "c1", True, target_streak=3, db_path=db)
    assert scs.is_streak_qualified(
        "B", db_path=db, require_dedicated_probe=False) is True
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": ["B"]},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    res = scs.check_dependencies("A", db_path=db, require_qualified=True)
    # The test's subject is the DEPENDENCY plumbing, so it asserts the two facts
    # separately rather than one opaque boolean, and it OPT IN to the probe
    # condition to prove the opt-in works.
    assert res["ok"] is True, res["errors"]
    assert res["deps"][0]["streak_at_target"] is True
    assert res["deps"][0]["qualified"] is True
    # B has the counter but no TDD cases, so it has no probe
    assert res["deps"][0]["dedicated_probe"] is False
    res2 = scs.check_dependencies("A", db_path=db, require_qualified=True,
                                 require_dedicated_probe=True)
    assert res2["ok"] is False
    assert any("not streak-qualified" in e for e in res2["errors"])
    assert res2["deps"][0]["dedicated_probe"] is False


def test_check_dependencies_can_skip_qualification(db: Path):
    scs.upsert_contract("B", "b", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": ["B"]},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    res = scs.check_dependencies("A", require_qualified=False, db_path=db)
    assert res["ok"] is True


def test_assert_dependencies_ready_rejects(db: Path):
    scs.upsert_contract(
        "A", "a", "t", "p", environment={"depends_on": ["GHOST"]},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    ok, reason = scs.assert_dependencies_ready("A", db_path=db)
    assert ok is False
    assert "does not exist" in reason


def test_assert_dependencies_ready_passes_with_no_deps(db: Path):
    scs.upsert_contract("A", "a", "t", "p", enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db)
    ok, reason = scs.assert_dependencies_ready("A", db_path=db)
    assert ok is True and reason == ""


def test_skill_0001_depends_on_skill_0002(seeded: Path):
    """The real chat-identity case: SKILL-0001 declares SKILL-0002."""
    scs.upsert_contract(
        "SKILL-0002", "_log_chat_center_identity", "Task/x", "Persist log",
        write_owner=True, environment={"table": "chat_identity_log"},
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=seeded,
    )
    assert scs.get_depends_on("SKILL-0001", db_path=seeded) == ["SKILL-0002"]
    res = scs.check_dependencies("SKILL-0001", db_path=seeded)
    assert res["ok"] is False  # SKILL-0002 exists but is not qualified
    assert res["deps"][0]["exists"] is True
    assert res["deps"][0]["qualified"] is False


# --------------------------------------------------------------- P0-2 override


def test_taxonomy_cannot_be_silently_disabled(db: Path):
    """P0-2: enforce_taxonomy=False alone must be rejected."""
    res = scs.upsert_contract(
        "S1", "k", "not-a-path", "p", enforce_taxonomy=False, db_path=db
    )
    assert res["ok"] is False
    assert res["code"] == "TAXONOMY_ENFORCEMENT_REQUIRED"


def test_taxonomy_override_requires_explicit_flag(db: Path):
    res = scs.upsert_contract(
        "S1", "k", "not-a-path", "p",
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    assert res["ok"] is True


# --------------------------------------------------------------- P1 coverage


def test_every_contract_has_field_registry_and_tdd(db: Path):
    """P1 acceptance: >=5 fields, >=3 pass, >=2 hard_fail per contract."""
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    for c in scs.list_contracts(db_path=db):
        cid = c["contract_id"]
        fields = scs.list_fields(cid, db_path=db)
        cases = scs.list_tdd_cases(cid, db_path=db)
        npass = len([x for x in cases if x["kind"] == "pass"])
        nfail = len([x for x in cases if x["kind"] == "hard_fail"])
        assert len(fields) >= 5, f"{cid} has only {len(fields)} fields"
        assert npass >= 3, f"{cid} has only {npass} pass cases"
        assert nfail >= 2, f"{cid} has only {nfail} hard_fail cases"


def test_field_registry_rejects_missing_mandatory_per_contract(db: Path):
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    for c in scs.list_contracts(db_path=db):
        cid = c["contract_id"]
        fields = scs.list_fields(cid, db_path=db)
        mandatory = [f["field_name"] for f in fields if f["mandatory"]]
        assert mandatory, f"{cid} has no mandatory field"
        ok, errors = scs.validate_payload_against_contract(cid, {}, db_path=db)
        assert ok is False, f"{cid} accepted an empty payload"
        assert any("Missing required field" in e for e in errors)


def test_field_registry_rejects_unregistered_per_contract(db: Path):
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    for c in scs.list_contracts(db_path=db):
        cid = c["contract_id"]
        ok, errors = scs.validate_payload_against_contract(
            cid, {"__unregistered__": "x"}, db_path=db
        )
        assert ok is False, f"{cid} accepted an unregistered field"
        assert any("Unregistered field" in e for e in errors)


def test_every_contract_has_an_enum_field(db: Path):
    """Each contract must constrain at least one field to a fixed enum."""
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    for c in scs.list_contracts(db_path=db):
        cid = c["contract_id"]
        fields = scs.list_fields(cid, db_path=db)
        assert any(f.get("enum") for f in fields), f"{cid} has no enum field"


def test_p1_data_covers_every_seeded_contract(db: Path):
    """Every seeded contract must appear in the P1 data module."""
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    seeded = {c["contract_id"] for c in scs.list_contracts(db_path=db)}
    covered = set(p1.FIELDS) | {"SKILL-0001"}
    missing = seeded - covered
    assert not missing, f"P1 data missing contracts: {sorted(missing)}"


# --------------------------------------------------------------- P2 governance


def test_contract_readiness_reports_gaps(db: Path):
    scs.upsert_contract(
        "S1", "k", "t", "p",
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    r = scs.contract_readiness("S1", db_path=db)
    assert r["ready"] is False
    assert any("fields" in x for x in r["reasons"])
    assert any("pass cases" in x for x in r["reasons"])


def test_promote_contract_rejects_unready(db: Path):
    scs.upsert_contract(
        "S1", "k", "t", "p",
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    res = scs.promote_contract("S1", db_path=db)
    assert res["ok"] is False and res["code"] == "NOT_READY"


def test_promote_contract_unknown(db: Path):
    res = scs.promote_contract("NOPE", db_path=db)
    assert res["ok"] is False and res["code"] == "UNKNOWN_CONTRACT"


def test_promote_ready_contracts_promotes_covered(db: Path):
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    res = scs.promote_ready_contracts(db_path=db)
    assert res["ok"] is True
    assert res["promoted_count"] == 7
    for c in scs.list_contracts(db_path=db):
        assert c["status"] == "active", f"{c['contract_id']} not promoted"


def test_promote_is_idempotent(db: Path):
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    scs.promote_ready_contracts(db_path=db)
    again = scs.promote_ready_contracts(db_path=db)
    assert again["promoted_count"] == 0


def test_normalize_source_value():
    assert scs.normalize_source_value("seed_eight_level") == "seed"
    assert scs.normalize_source_value("scan:foo.skill.md") == "seed"
    assert scs.normalize_source_value("manual") == "manual"
    assert scs.normalize_source_value("") == "manual"
    assert scs.normalize_source_value("proven") == "proven"
    assert scs.normalize_source_value("something_else") == "manual"


def test_standardize_sources_normalizes(db: Path):
    scs.upsert_contract(
        "S1", "k", "t", "p", source="seed_eight_level",
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    res = scs.standardize_sources(db_path=db)
    assert res["changed_count"] == 1
    assert scs.get_contract("S1", db_path=db)["source"] == "seed"


def test_standardize_sources_is_idempotent(db: Path):
    scs.upsert_contract(
        "S1", "k", "t", "p", source="seed_eight_level",
        enforce_taxonomy=False, allow_taxonomy_override=True, db_path=db,
    )
    scs.standardize_sources(db_path=db)
    again = scs.standardize_sources(db_path=db)
    assert again["changed_count"] == 0


def test_all_sources_are_canonical(db: Path):
    import skill_contract_p1_data as p1

    scs.seed_eight_level_contracts(db_path=db)
    p1.apply(db_path=db)
    scs.standardize_sources(db_path=db)
    for c in scs.list_contracts(db_path=db):
        assert c["source"] in scs.VALID_SOURCES, (
            f"{c['contract_id']} has non-canonical source {c['source']!r}"
        )

# object_door: kind-agnostic by definition (no DDL in this file)
