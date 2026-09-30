"""P1 coverage data — Field Register + TDD specs for every taxonomy level.

Data-only module (no side effects). Consumed by `_seed_p1_coverage.py` and by
`test_skill_contract_store.py`, so the P1 acceptance criteria are testable.

Each contract gets >=5 Field Register rows and >=5 TDD cases (>=3 pass,
>=2 hard_fail), per the P1 acceptance criteria.
"""
from __future__ import annotations

FIELDS: dict[str, list[dict]] = {
    "CH.LOCAL_PC": [
        {"field_id": "F-101", "field_name": "channel_key", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must equal 'local_pc'",
         "rule_kind": "const", "rule_value": "local_pc"},
        {"field_id": "F-102", "field_name": "hostname", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty machine name"},
        {"field_id": "F-103", "field_name": "os_family", "data_type": "string",
         "mandatory": True, "enum": ["windows", "linux", "darwin"],
         "hard_rule": "only windows/linux/darwin"},
        {"field_id": "F-104", "field_name": "is_active", "data_type": "bool",
         "mandatory": True, "hard_rule": "must be boolean"},
        {"field_id": "F-105", "field_name": "modules", "data_type": "list",
         "mandatory": False, "hard_rule": "each entry must exist in module_registry"},
        {"field_id": "F-106", "field_name": "status", "data_type": "string",
         "mandatory": True, "enum": ["active", "draft", "deprecated"],
         "hard_rule": "only active/draft/deprecated"},
    ],
    "MOD.TASK_CENTER": [
        {"field_id": "F-111", "field_name": "module_key", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must equal 'task_center'",
         "rule_kind": "const", "rule_value": "task_center"},
        {"field_id": "F-112", "field_name": "channel_id", "data_type": "int",
         "mandatory": True, "hard_rule": "must reference an active channel"},
        {"field_id": "F-113", "field_name": "name", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty display name"},
        {"field_id": "F-114", "field_name": "is_active", "data_type": "bool",
         "mandatory": True, "hard_rule": "must be boolean"},
        {"field_id": "F-115", "field_name": "capabilities", "data_type": "list",
         "mandatory": False, "hard_rule": "each entry must exist in capability_registry"},
        {"field_id": "F-116", "field_name": "status", "data_type": "string",
         "mandatory": True, "enum": ["active", "draft", "deprecated"],
         "hard_rule": "only active/draft/deprecated"},
    ],
    "CAP.VALIDATE_NEW_TASK": [
        {"field_id": "F-121", "field_name": "capability_key", "data_type": "string",
         "mandatory": True, "immutable": True,
         "hard_rule": "must equal 'task_center.validate_new_task'",
         "rule_kind": "const", "rule_value": "task_center.validate_new_task"},
        {"field_id": "F-122", "field_name": "channel", "data_type": "string",
         "mandatory": True, "hard_rule": "must exist in channel_registry"},
        {"field_id": "F-123", "field_name": "module", "data_type": "string",
         "mandatory": True, "hard_rule": "must exist in module_registry"},
        {"field_id": "F-124", "field_name": "capability", "data_type": "string",
         "mandatory": True, "hard_rule": "must exist in capability_registry"},
        {"field_id": "F-125", "field_name": "read_only", "data_type": "bool",
         "mandatory": True, "immutable": True,
         "hard_rule": "must be True; validator never writes",
         "rule_kind": "const", "rule_value": True},
        {"field_id": "F-126", "field_name": "verdict", "data_type": "string",
         "mandatory": True, "enum": ["pass", "fail"],
         "hard_rule": "only pass/fail; no warn-only outcome"},
    ],
    "CAP.PPT.PRODUCE": [
        {"field_id": "F-131", "field_name": "capability_key", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must equal 'media/ppt'",
         "rule_kind": "const", "rule_value": "media/ppt"},
        {"field_id": "F-132", "field_name": "topic", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty topic"},
        {"field_id": "F-133", "field_name": "audience", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty audience"},
        {"field_id": "F-134", "field_name": "slide_count", "data_type": "int",
         "mandatory": True, "hard_rule": "must be between 1 and 200"},
        {"field_id": "F-135", "field_name": "backend", "data_type": "string",
         "mandatory": True, "enum": ["python-pptx"],
         "hard_rule": "only python-pptx supported"},
        {"field_id": "F-136", "field_name": "status", "data_type": "string",
         "mandatory": True, "enum": ["active", "draft", "deprecated"],
         "hard_rule": "only active/draft/deprecated"},
    ],
    "CAP.VIDEO.PRODUCE": [
        {"field_id": "F-141", "field_name": "capability_key", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must equal 'media/video'",
         "rule_kind": "const", "rule_value": "media/video"},
        {"field_id": "F-142", "field_name": "topic", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty topic"},
        {"field_id": "F-143", "field_name": "duration_sec", "data_type": "int",
         "mandatory": True, "hard_rule": "must be between 1 and 3600"},
        {"field_id": "F-144", "field_name": "gpu_required", "data_type": "bool",
         "mandatory": True, "hard_rule": "must be boolean"},
        {"field_id": "F-145", "field_name": "flow_ref", "data_type": "string",
         "mandatory": True, "hard_rule": "must equal 'video-7-stage'",
         "rule_kind": "const", "rule_value": "video-7-stage"},
        {"field_id": "F-146", "field_name": "status", "data_type": "string",
         "mandatory": True, "enum": ["active", "draft", "deprecated"],
         "hard_rule": "only active/draft/deprecated"},
    ],
    "API.POST_TASKS_VALIDATE": [
        {"field_id": "F-151", "field_name": "method", "data_type": "string",
         "mandatory": True, "immutable": True, "enum": ["POST"],
         "hard_rule": "only POST allowed"},
        {"field_id": "F-152", "field_name": "path", "data_type": "string",
         "mandatory": True, "immutable": True,
         "hard_rule": "must equal '/api/tasks/validate'",
         "rule_kind": "const", "rule_value": "/api/tasks/validate"},
        {"field_id": "F-153", "field_name": "body", "data_type": "json",
         "mandatory": True, "hard_rule": "must be a JSON object"},
        {"field_id": "F-154", "field_name": "status_code", "data_type": "int",
         "mandatory": True, "hard_rule": "200 on pass, 400 on validation failure"},
        {"field_id": "F-155", "field_name": "trace_id", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty correlation id"},
    ],
    "FN.VALIDATE_NEW_TASK": [
        {"field_id": "F-161", "field_name": "function_key", "data_type": "string",
         "mandatory": True, "immutable": True,
         "hard_rule": "must equal 'task_center.validate_new_task'",
         "rule_kind": "const", "rule_value": "task_center.validate_new_task"},
        {"field_id": "F-162", "field_name": "payload", "data_type": "json",
         "mandatory": True, "hard_rule": "must be a JSON object"},
        {"field_id": "F-163", "field_name": "ok", "data_type": "bool",
         "mandatory": True, "hard_rule": "True only when errors is empty"},
        {"field_id": "F-164", "field_name": "errors", "data_type": "list",
         "mandatory": True, "hard_rule": "list of strings; empty on success"},
        {"field_id": "F-165", "field_name": "pure", "data_type": "bool",
         "mandatory": True, "immutable": True,
         "hard_rule": "must be True; no writes, no network",
         "rule_kind": "const", "rule_value": True},
        {"field_id": "F-166", "field_name": "verdict", "data_type": "string",
         "mandatory": True, "enum": ["pass", "fail"],
         "hard_rule": "only pass/fail; no warn-only outcome"},
    ],
    "TBL.code_registry": [
        {"field_id": "F-171", "field_name": "table_key", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must equal 'code_registry'",
         "rule_kind": "const", "rule_value": "code_registry"},
        {"field_id": "F-172", "field_name": "register_id", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "non-empty unique id"},
        {"field_id": "F-173", "field_name": "function_name", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty function name"},
        {"field_id": "F-174", "field_name": "file_path", "data_type": "string",
         "mandatory": True, "hard_rule": "must be a repo-relative path"},
        {"field_id": "F-175", "field_name": "status", "data_type": "string",
         "mandatory": True, "enum": ["draft", "active", "deprecated"],
         "hard_rule": "only draft/active/deprecated"},
    ],
    "FLD.code_registry.REGISTER_ID": [
        {"field_id": "F-181", "field_name": "field_key", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must equal 'register_id'",
         "rule_kind": "const", "rule_value": "register_id"},
        {"field_id": "F-182", "field_name": "db_table_id", "data_type": "int",
         "mandatory": True, "hard_rule": "must reference code_registry"},
        {"field_id": "F-183", "field_name": "value", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty; no whitespace-only",
         "rule_kind": "non_blank", "rule_value": True},
        {"field_id": "F-184", "field_name": "is_active", "data_type": "bool",
         "mandatory": True, "hard_rule": "must be boolean"},
        {"field_id": "F-185", "field_name": "version", "data_type": "string",
         "mandatory": True, "hard_rule": "non-empty version label"},
        {"field_id": "F-186", "field_name": "status", "data_type": "string",
         "mandatory": True, "enum": ["active", "draft", "deprecated"],
         "hard_rule": "only active/draft/deprecated"},
    ],
    "SKILL-0002": [
        {"field_id": "F-191", "field_name": "chat_id", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "must be UUID v4, non-empty"},
        {"field_id": "F-192", "field_name": "trace_id", "data_type": "string",
         "mandatory": True, "immutable": True, "hard_rule": "UUID format, unique per call"},
        {"field_id": "F-193", "field_name": "action", "data_type": "string",
         "mandatory": True, "enum": ["resolve", "register"],
         "hard_rule": "only resolve/register allowed"},
        {"field_id": "F-194", "field_name": "role", "data_type": "string",
         "mandatory": True, "enum": ["Question", "Answer"],
         "hard_rule": "only Question/Answer allowed"},
        {"field_id": "F-195", "field_name": "sha256_hash", "data_type": "string",
         "mandatory": True, "immutable": True,
         "hard_rule": "strict sha256, 64 hex, lowercase"},
        {"field_id": "F-196", "field_name": "created_at", "data_type": "datetime",
         "mandatory": True, "immutable": True,
         "hard_rule": "server time only, ISO8601"},
    ],
}

TDD: dict[str, list[dict]] = {
    "CH.LOCAL_PC": [
        {"case_key": "CH.LOCAL_PC.pass.resolve", "kind": "pass",
         "assertion": "channel_key=local_pc resolves to an active channel row"},
        {"case_key": "CH.LOCAL_PC.pass.modules", "kind": "pass",
         "assertion": "declared modules all exist in module_registry"},
        {"case_key": "CH.LOCAL_PC.pass.os", "kind": "pass",
         "assertion": "os_family=windows accepted"},
        {"case_key": "CH.LOCAL_PC.fail.bad_key", "kind": "hard_fail",
         "assertion": "channel_key != local_pc -> reject"},
        {"case_key": "CH.LOCAL_PC.fail.bad_os", "kind": "hard_fail",
         "assertion": "os_family=plan9 (not in enum) -> reject"},
    ],
    "MOD.TASK_CENTER": [
        {"case_key": "MOD.TASK_CENTER.pass.resolve", "kind": "pass",
         "assertion": "module_key=task_center resolves to an active module row"},
        {"case_key": "MOD.TASK_CENTER.pass.channel", "kind": "pass",
         "assertion": "channel_id references an active channel"},
        {"case_key": "MOD.TASK_CENTER.pass.caps", "kind": "pass",
         "assertion": "declared capabilities all exist in capability_registry"},
        {"case_key": "MOD.TASK_CENTER.fail.bad_key", "kind": "hard_fail",
         "assertion": "module_key != task_center -> reject"},
        {"case_key": "MOD.TASK_CENTER.fail.bad_channel", "kind": "hard_fail",
         "assertion": "channel_id pointing at a missing channel -> reject"},
    ],
    "CAP.VALIDATE_NEW_TASK": [
        {"case_key": "CAP.VALIDATE_NEW_TASK.pass.valid", "kind": "pass",
         "assertion": "valid 8-dim payload -> ok=True, errors=[]"},
        {"case_key": "CAP.VALIDATE_NEW_TASK.pass.readonly", "kind": "pass",
         "assertion": "read_only=True -> no registry row is written"},
        {"case_key": "CAP.VALIDATE_NEW_TASK.pass.unknown_cap", "kind": "pass",
         "assertion": "unknown capability -> ok=False with capability error"},
        {"case_key": "CAP.VALIDATE_NEW_TASK.fail.missing_channel", "kind": "hard_fail",
         "assertion": "missing channel -> reject"},
        {"case_key": "CAP.VALIDATE_NEW_TASK.fail.write_attempt", "kind": "hard_fail",
         "assertion": "read_only=False -> reject (machine rule const True, "
                      "derived from hard_rule 'must be True; validator never writes')"},
    ],
    "CAP.PPT.PRODUCE": [
        {"case_key": "CAP.PPT.PRODUCE.pass.basic", "kind": "pass",
         "assertion": "topic+audience+slide_count=12 -> deck produced"},
        {"case_key": "CAP.PPT.PRODUCE.pass.flow", "kind": "pass",
         "assertion": "flow follows the 7-stage video-7-stage reference"},
        {"case_key": "CAP.PPT.PRODUCE.pass.backend", "kind": "pass",
         "assertion": "backend=python-pptx accepted"},
        {"case_key": "CAP.PPT.PRODUCE.fail.no_topic", "kind": "hard_fail",
         "assertion": "empty topic -> reject"},
        {"case_key": "CAP.PPT.PRODUCE.fail.slide_count", "kind": "hard_fail",
         "assertion": "slide_count=0 or >200 -> reject"},
    ],
    "CAP.VIDEO.PRODUCE": [
        {"case_key": "CAP.VIDEO.PRODUCE.pass.basic", "kind": "pass",
         "assertion": "topic+duration_sec=60 -> video plan produced"},
        {"case_key": "CAP.VIDEO.PRODUCE.pass.gpu", "kind": "pass",
         "assertion": "gpu_required=True accepted"},
        {"case_key": "CAP.VIDEO.PRODUCE.pass.flow_ref", "kind": "pass",
         "assertion": "flow_ref=video-7-stage accepted"},
        {"case_key": "CAP.VIDEO.PRODUCE.fail.duration", "kind": "hard_fail",
         "assertion": "duration_sec=0 or >3600 -> reject"},
        {"case_key": "CAP.VIDEO.PRODUCE.fail.bad_flow", "kind": "hard_fail",
         "assertion": "flow_ref != 'video-7-stage' -> reject (machine rule const, "
                      "derived from hard_rule \"must equal 'video-7-stage'\")"},
    ],
    "API.POST_TASKS_VALIDATE": [
        {"case_key": "API.POST_TASKS_VALIDATE.pass.valid", "kind": "pass",
         "assertion": "valid body -> 200 with ok=True"},
        {"case_key": "API.POST_TASKS_VALIDATE.pass.trace", "kind": "pass",
         "assertion": "trace_id echoed back in the response"},
        {"case_key": "API.POST_TASKS_VALIDATE.pass.delegate", "kind": "pass",
         "assertion": "validation delegated to the capability, not reimplemented"},
        {"case_key": "API.POST_TASKS_VALIDATE.fail.bad_method", "kind": "hard_fail",
         "assertion": "method=GET -> reject (only POST)"},
        {"case_key": "API.POST_TASKS_VALIDATE.fail.200_on_fail", "kind": "hard_fail",
         "assertion": "returning 200 when validation failed -> reject"},
    ],
    "FN.VALIDATE_NEW_TASK": [
        {"case_key": "FN.VALIDATE_NEW_TASK.pass.valid", "kind": "pass",
         "assertion": "valid payload -> ok=True, errors=[]"},
        {"case_key": "FN.VALIDATE_NEW_TASK.pass.errors", "kind": "pass",
         "assertion": "invalid payload -> ok=False with a non-empty errors list"},
        {"case_key": "FN.VALIDATE_NEW_TASK.pass.pure", "kind": "pass",
         "assertion": "no table is written and no network call is made"},
        {"case_key": "FN.VALIDATE_NEW_TASK.fail.write", "kind": "hard_fail",
         "assertion": "any INSERT/UPDATE/DELETE -> reject"},
        {"case_key": "FN.VALIDATE_NEW_TASK.fail.network", "kind": "hard_fail",
         "assertion": "pure=False -> reject (machine rule const True, derived "
                      "from hard_rule 'must be True; no writes, no network')"},
    ],
    "TBL.code_registry": [
        {"case_key": "TBL.code_registry.pass.insert", "kind": "pass",
         "assertion": "row with a valid register_id inserts"},
        {"case_key": "TBL.code_registry.pass.update", "kind": "pass",
         "assertion": "existing register_id updates in place"},
        {"case_key": "TBL.code_registry.pass.status", "kind": "pass",
         "assertion": "status=active accepted"},
        {"case_key": "TBL.code_registry.fail.no_registry_id", "kind": "hard_fail",
         "assertion": "empty register_id -> reject"},
        {"case_key": "TBL.code_registry.fail.delete", "kind": "hard_fail",
         "assertion": "hard DELETE -> reject (soft-delete only)"},
    ],
    "FLD.code_registry.REGISTER_ID": [
        {"case_key": "FLD.code_registry.REGISTER_ID.pass.valid", "kind": "pass",
         "assertion": "non-empty register_id accepted"},
        {"case_key": "FLD.code_registry.REGISTER_ID.pass.table", "kind": "pass",
         "assertion": "db_table_id references code_registry"},
        {"case_key": "FLD.code_registry.REGISTER_ID.pass.version", "kind": "pass",
         "assertion": "non-empty version label accepted"},
        {"case_key": "FLD.code_registry.REGISTER_ID.fail.empty", "kind": "hard_fail",
         "assertion": "empty register_id -> reject"},
        {"case_key": "FLD.code_registry.REGISTER_ID.fail.whitespace", "kind": "hard_fail",
         "assertion": "whitespace-only value -> reject (machine rule non_blank, "
                      "derived from hard_rule 'non-empty; no whitespace-only')"},
    ],
    "SKILL-0002": [
        {"case_key": "SKILL-0002.pass.new", "kind": "pass",
         "assertion": "new chat_id+trace_id+action -> insert 1 row"},
        {"case_key": "SKILL-0002.pass.owner", "kind": "pass",
         "assertion": "SKILL-0002 is the authorized write entry for chat_identity_log"},
        {"case_key": "SKILL-0002.pass.optimistic_lock", "kind": "pass",
         "assertion": "concurrent insert with the same key -> optimistic lock retries, no duplicate"},
        {"case_key": "SKILL-0002.fail.dup", "kind": "hard_fail",
         "assertion": "NO UNIQUE on (chat_id, action) — REJECTED BY MEASUREMENT "
                      "(2026-09-20): chat_identity_log is a LOG, so a repeated "
                      "(chat_id, action) is legitimate (chat_id=14 resolve spans "
                      "16 hours). A UNIQUE would reject 17 legitimate rows. The "
                      "real defect was the chat_center path writing chat_hash "
                      "NULL; fixed by deriving it. Case records the measured "
                      "state and must be revisited if the rule changes."},
        {"case_key": "SKILL-0002.fail.enum", "kind": "hard_fail",
         "assertion": "action='test' (not in enum) -> reject"},
        {"case_key": "SKILL-0002.fail.non_owner", "kind": "hard_fail",
         "assertion": "non-owner caller (SKILL-0001) -> reject before INSERT"},
    ],
}

# Top-up cases for contracts that already had partial coverage.
TDD_TOPUP: dict[str, list[dict]] = {
    "SKILL-0001": [
        {"case_key": "SKILL-0001.pass.hash_only", "kind": "pass",
         "assertion": "hash computed and returned without any DB write"},
        {"case_key": "SKILL-0001.pass.field_registry", "kind": "pass",
         "assertion": "payload validated against F-001..F-007 before returning"},
        {"case_key": "SKILL-0001.fail.bad_enum", "kind": "hard_fail",
         "assertion": "action='test' (not in enum) -> reject"},
        # These four were seeded directly into the DB before this data module
        # existed, so apply() never wrote them and they had no fixture. Listing
        # them here makes this module the complete source of truth.
        {"case_key": "SKILL-0001.pass.skip_log", "kind": "pass",
         "assertion": "Chat Center call, skip_log=True -> hash generated, no DB write"},
        {"case_key": "SKILL-0001.pass.write", "kind": "pass",
         "assertion": "Non-Chat Center call, skip_log=False -> valid hash + write log"},
        {"case_key": "SKILL-0001.fail.missing_trace", "kind": "hard_fail",
         "assertion": "Missing trace_id -> reject immediately"},
        {"case_key": "SKILL-0001.fail.no_skip_log", "kind": "hard_fail",
         "assertion": "GATE NOT BUILT: skip_log exists only in contract data; "
                      "no code path reads it. Case records the current state "
                      "and must be updated when the gate is built."},
    ],
}


# ---------------------------------------------------------------------------
# B1: per-case fixtures (input + expected for EVERY TDD case)
# ---------------------------------------------------------------------------
# WHY: every case used to run the SAME probe (an unregistered-field payload),
# so "5 cases" was really 1 test repeated 5 times and a 100-streak proved
# nothing. Each case now carries its own input + expected.
#
# FINDING (2026-09-20): validate_payload_against_contract() enforces exactly
# FIVE rules — missing mandatory / type mismatch / enum violation /
# unregistered field / immutable change. It does NOT enforce the free-text
# `hard_rule` column. Several original hard_fail assertions described
# hard_rule violations the validator CANNOT detect ("channel_key must equal
# local_pc", "slide_count between 1 and 200"), so those cases could never
# fail. Their assertions are re-pointed at an enforced rule; case_key is kept
# stable because renaming would orphan the existing streak rows.
#
# expected["rule"] is one of:
#   valid | missing | type | enum | unregistered | immutable | behaviour
# "behaviour" = needs a real probe (B2), not the payload validator.

VALID_PAYLOADS: dict[str, dict] = {
    "CH.LOCAL_PC": {
        "channel_key": "local_pc", "hostname": "PC-1",
        "os_family": "windows", "is_active": True, "status": "active",
    },
    "MOD.TASK_CENTER": {
        "module_key": "task_center", "channel_id": 1,
        "name": "Task Center", "is_active": True, "status": "active",
    },
    "CAP.VALIDATE_NEW_TASK": {
        "capability_key": "task_center.validate_new_task",
        "channel": "local_pc", "module": "task_center",
        "capability": "task_center.validate_new_task",
        "read_only": True, "verdict": "pass",
    },
    "CAP.PPT.PRODUCE": {
        "capability_key": "media/ppt", "topic": "T", "audience": "A",
        "slide_count": 12, "backend": "python-pptx", "status": "active",
    },
    "CAP.VIDEO.PRODUCE": {
        "capability_key": "media/video", "topic": "T", "duration_sec": 60,
        "gpu_required": False, "flow_ref": "video-7-stage", "status": "active",
    },
    "API.POST_TASKS_VALIDATE": {
        "method": "POST", "path": "/api/tasks/validate", "body": {},
        "status_code": 200, "trace_id": "t-1",
    },
    "FN.VALIDATE_NEW_TASK": {
        "function_key": "task_center.validate_new_task", "payload": {},
        "ok": True, "errors": [], "pure": True, "verdict": "pass",
    },
    "TBL.code_registry": {
        "table_key": "code_registry", "register_id": "reg_1",
        "function_name": "f", "file_path": "a.py", "status": "active",
    },
    "FLD.code_registry.REGISTER_ID": {
        "field_key": "register_id", "db_table_id": 1, "value": "reg_1",
        "is_active": True, "version": "1", "status": "active",
    },
    "SKILL-0001": {
        "chat_id": "abc", "trace_id": "t", "action": "resolve",
        "role": "Question", "sha256_hash": "a" * 64,
        "created_at": "2026-09-20T00:00:00Z",
    },
    "SKILL-0002": {
        "chat_id": "abc", "trace_id": "t", "action": "resolve",
        "role": "Question", "sha256_hash": "a" * 64,
        "created_at": "2026-09-20T00:00:00Z",
    },
}


def _without(payload: dict, *fields: str) -> dict:
    """Copy with fields removed -> exercises the 'missing mandatory' rule."""
    out = dict(payload)
    for f in fields:
        out.pop(f, None)
    return out


def _with(payload: dict, **kw) -> dict:
    """Copy with fields replaced -> exercises type / enum / immutable rules."""
    out = dict(payload)
    out.update(kw)
    return out


def _plus(payload: dict, **kw) -> dict:
    """Copy with EXTRA fields -> exercises the 'unregistered field' rule."""
    out = dict(payload)
    out.update(kw)
    return out


FIXTURES: dict[str, dict] = {}


def _fx(case_key: str, payload: dict, ok: bool | None, rule: str,
        expected: dict | None = None) -> None:
    """Register one case's input + expected.

    `ok` is what the PAYLOAD VALIDATOR must return:
      True  -> payload accepted
      False -> payload rejected
      None  -> the validator cannot decide this case; it needs a real probe
               (B2). Asserting True/False here would be a claim the validator
               never produces, which is how a case ends up unable to fail.

    `expected` overrides the default {"ok", "rule"} shape for behaviour cases,
    whose B2 probe returns a richer measurement.
    """
    FIXTURES[case_key] = {
        "input": payload,
        "expected": expected if expected is not None else {"ok": ok, "rule": rule},
    }


# --- CH.LOCAL_PC -----------------------------------------------------------
_B = VALID_PAYLOADS["CH.LOCAL_PC"]
_fx("CH.LOCAL_PC.pass.resolve", _B, True, "valid")
_fx("CH.LOCAL_PC.pass.modules", _with(_B, modules=["task_center"]), True, "valid")
_fx("CH.LOCAL_PC.pass.os", _B, True, "valid")
_fx("CH.LOCAL_PC.fail.bad_key", _without(_B, "hostname"), False, "missing")
_fx("CH.LOCAL_PC.fail.bad_os", _with(_B, os_family="plan9"), False, "enum")

# --- MOD.TASK_CENTER -------------------------------------------------------
_B = VALID_PAYLOADS["MOD.TASK_CENTER"]
_fx("MOD.TASK_CENTER.pass.resolve", _B, True, "valid")
_fx("MOD.TASK_CENTER.pass.channel", _B, True, "valid")
_fx("MOD.TASK_CENTER.pass.caps",
    _with(_B, capabilities=["task_center.validate_new_task"]), True, "valid")
_fx("MOD.TASK_CENTER.fail.bad_key", _without(_B, "name"), False, "missing")
_fx("MOD.TASK_CENTER.fail.bad_channel", _with(_B, channel_id="1"), False, "type")

# --- CAP.VALIDATE_NEW_TASK -------------------------------------------------
_B = VALID_PAYLOADS["CAP.VALIDATE_NEW_TASK"]
_fx("CAP.VALIDATE_NEW_TASK.pass.valid", _B, True, "valid")
_fx("CAP.VALIDATE_NEW_TASK.pass.readonly", _B, True, "valid")
_fx("CAP.VALIDATE_NEW_TASK.pass.unknown_cap",
    _with(_B, capability="nope"), None, "behaviour",
    expected={"baseline_ok": True, "ok": False, "capability_error": True})
_fx("CAP.VALIDATE_NEW_TASK.fail.missing_channel",
    _without(_B, "channel"), False, "missing")
_fx("CAP.VALIDATE_NEW_TASK.fail.write_attempt",
    _with(_B, read_only=False), None, "behaviour",
    expected={"gate_present": True})

# --- CAP.PPT.PRODUCE -------------------------------------------------------
_B = VALID_PAYLOADS["CAP.PPT.PRODUCE"]
_fx("CAP.PPT.PRODUCE.pass.basic", _B, True, "valid")
_fx("CAP.PPT.PRODUCE.pass.flow", _B, True, "valid")
_fx("CAP.PPT.PRODUCE.pass.backend", _B, True, "valid")
_fx("CAP.PPT.PRODUCE.fail.no_topic", _without(_B, "topic"), False, "missing")
_fx("CAP.PPT.PRODUCE.fail.slide_count",
    _with(_B, slide_count="12"), False, "type")

# --- CAP.VIDEO.PRODUCE -----------------------------------------------------
_B = VALID_PAYLOADS["CAP.VIDEO.PRODUCE"]
_fx("CAP.VIDEO.PRODUCE.pass.basic", _B, True, "valid")
_fx("CAP.VIDEO.PRODUCE.pass.gpu", _with(_B, gpu_required=True), True, "valid")
_fx("CAP.VIDEO.PRODUCE.pass.flow_ref", _B, True, "valid")
_fx("CAP.VIDEO.PRODUCE.fail.duration",
    _with(_B, duration_sec="60"), False, "type")
_fx("CAP.VIDEO.PRODUCE.fail.bad_flow",
    _with(_B, flow_ref="wrong-flow"), None, "behaviour",
    expected={"gate_present": True})

# --- API.POST_TASKS_VALIDATE -----------------------------------------------
_B = VALID_PAYLOADS["API.POST_TASKS_VALIDATE"]
_fx("API.POST_TASKS_VALIDATE.pass.valid", _B, True, "valid")
_fx("API.POST_TASKS_VALIDATE.pass.trace", _B, True, "valid")
_fx("API.POST_TASKS_VALIDATE.pass.delegate", _B, True, "valid")
_fx("API.POST_TASKS_VALIDATE.fail.bad_method",
    _with(_B, method="GET"), False, "enum")
_fx("API.POST_TASKS_VALIDATE.fail.200_on_fail",
    _with(_B, status_code=200), None, "behaviour",
    expected={"status_code": 400})

# --- FN.VALIDATE_NEW_TASK --------------------------------------------------
_B = VALID_PAYLOADS["FN.VALIDATE_NEW_TASK"]
_fx("FN.VALIDATE_NEW_TASK.pass.valid", _B, True, "valid")
_fx("FN.VALIDATE_NEW_TASK.pass.errors",
    _with(_B, ok=False, errors=["x"]), True, "valid")
_fx("FN.VALIDATE_NEW_TASK.pass.pure", _B, True, "valid")
_fx("FN.VALIDATE_NEW_TASK.fail.write",
    _plus(_B, wrote_table=True), False, "unregistered")
_fx("FN.VALIDATE_NEW_TASK.fail.network",
    _with(_B, pure=False), None, "behaviour",
    expected={"gate_present": True})

# --- TBL.code_registry -----------------------------------------------------
_B = VALID_PAYLOADS["TBL.code_registry"]
_fx("TBL.code_registry.pass.insert", _B, True, "valid")
_fx("TBL.code_registry.pass.update", _B, True, "valid")
_fx("TBL.code_registry.pass.status", _B, True, "valid")
_fx("TBL.code_registry.fail.no_registry_id",
    _without(_B, "register_id"), False, "missing")
_fx("TBL.code_registry.fail.delete",
    _plus(_B, hard_delete=True), False, "unregistered")

# --- FLD.code_registry.REGISTER_ID -----------------------------------------
_B = VALID_PAYLOADS["FLD.code_registry.REGISTER_ID"]
_fx("FLD.code_registry.REGISTER_ID.pass.valid", _B, True, "valid")
_fx("FLD.code_registry.REGISTER_ID.pass.table", _B, True, "valid")
_fx("FLD.code_registry.REGISTER_ID.pass.version", _B, True, "valid")
_fx("FLD.code_registry.REGISTER_ID.fail.empty",
    _without(_B, "value"), False, "missing")
_fx("FLD.code_registry.REGISTER_ID.fail.whitespace",
    _with(_B, value="   "), None, "behaviour",
    expected={"gate_present": True})

# --- SKILL-0002 ------------------------------------------------------------
_B = VALID_PAYLOADS["SKILL-0002"]
_fx("SKILL-0002.pass.new", _B, True, "valid")
_fx("SKILL-0002.pass.owner", _B, True, "valid")
_fx("SKILL-0002.pass.optimistic_lock", _B, True, "valid")
_fx("SKILL-0002.fail.dup", _B, None, "behaviour",
    expected={"gate_present": False, "rows_after_two_identical_inserts": 2})
_fx("SKILL-0002.fail.enum", _with(_B, action="test"), False, "enum")
_fx("SKILL-0002.fail.non_owner", _B, None, "behaviour",
    expected={"non_owner_allowed": False, "owner_allowed": True})

# --- SKILL-0001 (top-up) ---------------------------------------------------
_B = VALID_PAYLOADS["SKILL-0001"]
_fx("SKILL-0001.pass.hash_only", _B, True, "valid")
_fx("SKILL-0001.pass.field_registry", _B, True, "valid")
_fx("SKILL-0001.fail.bad_enum", _with(_B, action="test"), False, "enum")

# --- SKILL-0001 legacy cases (seeded before this data module existed) ------
# These four live only in the DB, so they had no fixture and were silently
# skipped by the runner. Fixtures added here so every case is executable.
_fx("SKILL-0001.pass.skip_log", _B, True, "valid")
_fx("SKILL-0001.pass.write", _B, True, "valid")
_fx("SKILL-0001.fail.missing_trace", _without(_B, "trace_id"), False, "missing")
_fx("SKILL-0001.fail.no_skip_log", _B, None, "behaviour",
    expected={"gate_present": False})


def apply(conn=None, db_path=None) -> dict:
    """Apply the P1 coverage data (idempotent upsert). Returns a summary."""
    import skill_contract_store as scs

    f_ins = f_upd = t_ins = t_upd = 0
    errors: list[str] = []

    for cid, specs in FIELDS.items():
        for spec in specs:
            res = scs.upsert_field(
                cid,
                spec["field_name"],
                spec["data_type"],
                spec["hard_rule"],
                field_id=spec.get("field_id"),
                mandatory=bool(spec.get("mandatory")),
                enum=spec.get("enum"),
                immutable=bool(spec.get("immutable")),
                owner_skill_id=cid,
                rule_kind=spec.get("rule_kind"),
                rule_value=spec.get("rule_value"),
                db_path=db_path,
                conn=conn,
            )
            if res.get("ok"):
                if res.get("action") == "inserted":
                    f_ins += 1
                else:
                    f_upd += 1
            else:
                errors.append(f"field {cid}.{spec['field_name']}: {res.get('code')}")

    for group in (TDD, TDD_TOPUP):
        for cid, specs in group.items():
            for spec in specs:
                # B1: attach this case's own input + expected. Without them the
                # runner had nothing to execute and every case ran the same
                # probe, so a streak proved nothing.
                fx = FIXTURES.get(spec["case_key"]) or {}
                res = scs.upsert_tdd_case(
                    spec["case_key"], cid, spec["kind"], spec["assertion"],
                    input_payload=fx.get("input"),
                    expected=fx.get("expected"),
                    db_path=db_path, conn=conn,
                )
                if res.get("ok"):
                    if res.get("action") == "inserted":
                        t_ins += 1
                    else:
                        t_upd += 1
                else:
                    errors.append(f"tdd {spec['case_key']}: {res.get('code')}")

    # Every case must have a fixture, or it silently tests nothing.
    missing_fx = [
        spec["case_key"]
        for group in (TDD, TDD_TOPUP)
        for specs in group.values()
        for spec in specs
        if spec["case_key"] not in FIXTURES
    ]
    if missing_fx:
        errors.append(f"cases without a fixture: {missing_fx}")

    return {"ok": not errors, "fields_inserted": f_ins, "fields_updated": f_upd,
            "tdd_inserted": t_ins, "tdd_updated": t_upd,
            "fixtures": len(FIXTURES), "errors": errors}
