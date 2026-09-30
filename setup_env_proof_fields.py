"""Add the Field Register for the env_task_proof contract.

An invariant test requires every contract to constrain at least one field to a
fixed enum. This contract's Field Register describes the proof record itself —
the fields a judgement must carry.

B3 (2026-09-20): also registers the two PASS-side TDD cases. The contract had
5 hard_fail cases and only 1 pass case, so `contract_readiness()` reported
"pass cases 1 < 3" and the contract could not be promoted. The two cases added
here are the missing counterparts of two existing rules:

  * `no_proof_record_write` proves a FAIL without proof is REFUSED; nothing
    proved an honest UNKNOWN is still ALLOWED.
  * `absence_as_geometry` proves the container-ABSENT case is corrected;
    nothing proved the container-PRESENT geometry case is ACCEPTED.

The five existing hard_fail cases are NOT re-seeded: their original seeding
script is gone, so their `expected` shapes are known only from the DB. Writing
guessed shapes would break five working cases.
"""
import sys

sys.path.insert(0, r"C:\projects\agent_system")
import skill_contract_store as scs

DB = r"C:\projects\agent_system\agent.db"
CID = "MOD.MOUSE_SPOT_HELPER.ENV_PROOF"

FIELDS = [
    # (field_name, data_type, hard_rule, mandatory, enum, immutable, remark)
    (
        "verdict", "enum",
        "MUST be one of PASS/FAIL/UNKNOWN. PASS and FAIL assert a judgement and "
        "require a proof record; UNKNOWN asserts nothing and must name an absence "
        "category.",
        True, ["PASS", "FAIL", "UNKNOWN"], False,
        "The judgement itself. UNKNOWN is a real outcome, never a silent pass.",
    ),
    (
        "root_cause.category", "enum",
        "MUST be one of picker_not_open/source_suspect/box_not_seen/"
        "geometry_fail/text_mismatch/no_vl_answer/pass/unknown. Absence must be "
        "tested BEFORE geometry.",
        True,
        ["picker_not_open", "source_suspect", "box_not_seen", "geometry_fail",
         "text_mismatch", "no_vl_answer", "pass", "unknown"],
        False,
        "Container-absent must be picker_not_open, never geometry_fail.",
    ),
    (
        "picker_ok", "bool",
        "MUST be established (True or False) before any FAIL blamed on geometry. "
        "Absent (not set) is treated as UNPROVEN and rejects such a FAIL.",
        True, None, False,
        "Container presence. The input whose absence let geometry_fail through.",
    ),
    (
        "provenance.source", "string",
        "MUST NOT be empty or 'unknown'. Records which capture path produced the "
        "image; two paths produce different images at different moments.",
        True, None, False,
        "Without this a FAIL cannot be re-inspected.",
    ),
    (
        "provenance.sha256", "string",
        "MUST be present. Proves the image actually existed and was not mutated.",
        True, None, True,
        "Immutable once written: the same evidence id must describe the same bytes.",
    ),
    (
        "provenance.foreground.process", "string",
        "MUST be present and not 'unknown'. Records the front window's process "
        "name, e.g. Code.exe. A same-size image is NOT a same-content image.",
        True, None, False,
        "The check that actually catches a capture of the wrong window.",
    ),
    (
        "provenance.foreground_is_code", "bool",
        "MUST be False-or-True, never absent. False means the capture was taken "
        "over a non-target app and cannot support a judgement.",
        True, None, False,
        "Recorded even when the image itself is unreadable.",
    ),
    (
        "post_action_read", "string",
        "REQUIRED for verdict=PASS. A PASS without a post-action read-back is "
        "invalid: nothing verified that the action took effect.",
        False, None, False,
        "Enforces 'never report success without a proven post-action read'.",
    ),
]

for (name, dtype, rule, mandatory, enum, immutable, remark) in FIELDS:
    r = scs.upsert_field(
        CID, name, dtype, rule,
        mandatory=mandatory, enum=enum, immutable=immutable, remark=remark,
        db_path=DB,
    )
    print("  %-34s %s" % (name, r.get("action", r)))

print()
fields = scs.list_fields(CID, db_path=DB)
print("fields=%d  with_enum=%d" % (len(fields), sum(1 for f in fields if f.get("enum"))))
for f in fields:
    print("   -", f.get("field_name"), "|", f.get("data_type"),
          "| mandatory=", f.get("mandatory"), "| enum=", bool(f.get("enum")))

# ---------------------------------------------------------------------------
# B3: the two PASS-side TDD cases (idempotent upsert).
# ---------------------------------------------------------------------------
PASS_CASES = [
    dict(
        case_key="MOD.MOUSE_SPOT_HELPER.ENV_PROOF.pass.unknown_absence_writable",
        kind="pass",
        assertion=(
            "UNKNOWN asserts nothing, so it stays WRITABLE even with no proof "
            "record — but it must name an absence category. Counterpart of "
            "fail.no_proof_record_write: a gate that refuses everything would "
            "pass every existing case."
        ),
        input_payload={
            "verdict": "UNKNOWN",
            "picker_ok": False,
            "picker_reason": "no permissions menu visible",
            "root_cause": {"category": "picker_not_open"},
        },
        expected={"written": True, "verdict": "UNKNOWN"},
    ),
    dict(
        case_key="MOD.MOUSE_SPOT_HELPER.ENV_PROOF.pass.geometry_fail_accepted",
        kind="pass",
        assertion=(
            "container present + geometry_fail + FAIL is a CONSISTENT "
            "classification and must NOT be over-rejected. Counterpart of "
            "fail.absence_as_geometry: a rule engine that rejects everything "
            "would pass every existing case."
        ),
        input_payload={
            "verdict": "FAIL",
            "picker_ok": True,
            "root_cause": {"category": "geometry_fail"},
        },
        expected={"violations": 0, "category": "geometry_fail"},
    ),
]

print()
print("=== B3: PASS-side TDD cases ===")
for spec in PASS_CASES:
    r = scs.upsert_tdd_case(
        spec["case_key"], CID, spec["kind"], spec["assertion"],
        input_payload=spec["input_payload"], expected=spec["expected"],
        db_path=DB,
    )
    print("  %-58s %s" % (spec["case_key"].split(".")[-1],
                          r.get("action", r.get("code", r))))

print()
ready = scs.contract_readiness(CID, db_path=DB)
print("readiness: ready=%s fields=%d pass=%d hard_fail=%d reasons=%s" % (
    ready["ready"], ready["fields"], ready["pass_cases"],
    ready["hard_fail_cases"], ready["reasons"] or "none"))
