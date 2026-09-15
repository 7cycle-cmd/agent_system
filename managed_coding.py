"""Pipeline D — Managed Coding Spine (MCS0–MCS8).

Law:
  value + value = output
  same pattern, different values
  no register_id → not managed code
  incomplete profile → worker must not free-form invent
  rubbish/zombie = mark only (never auto-delete source; never gate)

Primary flow (MCS8 Function Builder):
  human request (function/system)
    → research (what exists / reuse / need table?)
    → multi-dim SSOT plan (channel+module, slices, TDD per field)
    → build system (tasks + register_id + field_tdd_rule)
    → everything traceable

Uses Pipeline C (code_health register/invoker/score) + task_ssot multi-dim.
Ontology = Task-1 map (channel+module / channel+module+function).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

PIPELINE_ID = "D_managed_coding"
GATE_POLICY = "never"

# Profile A–H (checklist dims — not 8 tasks per field)
PROFILE_STEPS: tuple[dict[str, str], ...] = (
    {"code": "A", "dim_key": "profile.A.field", "title": "field"},
    {"code": "B", "dim_key": "profile.B.table", "title": "table"},
    {"code": "C", "dim_key": "profile.C.api", "title": "api"},
    {"code": "D", "dim_key": "profile.D.function", "title": "function"},
    {"code": "E", "dim_key": "profile.E.tdd", "title": "tdd"},
    {"code": "F", "dim_key": "profile.F.trace", "title": "trace"},
    {"code": "G", "dim_key": "profile.G.module", "title": "module"},
    {"code": "H", "dim_key": "profile.H.register_id", "title": "register_id"},
)

REQUIRED_PROFILE_DIMS = tuple(p["dim_key"] for p in PROFILE_STEPS)
IMPL_DIM_MODULE = "impl.module"
IMPL_DIM_FUNCTION = "impl.function"
IMPL_DIM_REQUIRED = "impl.required"
IMPL_DIM_REGISTER = "impl.register_id"

SLICE_STATUS_ACTIVE = "active"
SLICE_STATUS_DRAFT = "draft"
SLICE_STATUS_RUBBISH = "rubbish"
SLICE_STATUS_DEPRECATED = "deprecated"
SLICE_STATUSES = (
    SLICE_STATUS_ACTIVE,
    SLICE_STATUS_DRAFT,
    SLICE_STATUS_RUBBISH,
    SLICE_STATUS_DEPRECATED,
)

REGISTER_STATUSES = ("draft", "active", "zombie", "rubbish", "deprecated")

# Membership example (Task 1 under version mem-1.0 — no clash with vision 1.1)
MEMBERSHIP_SYSTEM_KEY = "membership"
MEMBERSHIP_MODULE_CODE = "membership"
MEMBERSHIP_VERSION = "mem-1.0"
MEMBERSHIP_ROOT_LABEL = "1"
MEMBERSHIP_TABLE = "member"
MEMBERSHIP_OUTPUT = "member_profile"
MEMBERSHIP_SLICES: tuple[dict[str, Any], ...] = (
    {"label": "1.1", "key": "region", "tdd": "text", "sort": 1},
    {"label": "1.2", "key": "phone", "tdd": "text", "sort": 2},
    {"label": "1.3", "key": "contact_method", "tdd": "text", "sort": 3},
    {"label": "1.4", "key": "name", "tdd": "text", "sort": 4},
    {"label": "1.5", "key": "gender", "tdd": "text", "sort": 5},
)

ACTION_SYSTEM = "system.managed"
ACTION_SLICE = "slice.managed"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_token(name: str, *, default: str = "x") -> str:
    s = re.sub(r"[^A-Za-z0-9_.]+", "_", (name or "").strip())
    s = s.strip("._") or default
    return s[:120]


def layer_architecture() -> dict[str, Any]:
    """MCS7/8 — Ontology/Registry + Function Builder flow (UI + contracts)."""
    return {
        "positioning": (
            "Managed Coding = Ontology & Registry + Function Builder flow. "
            "Human request → research → multi-dim SSOT → build register_id+TDD. "
            "Not full runtime orchestrator (driver/GUI evidence/pair QC execute)."
        ),
        "primary_flow": [
            "1 human → request function/system (fn_request)",
            "A AI research if work missing (fn_research): what exists / reuse",
            "  · with table → slices from fields (1.1 region, 1.2 phone…)",
            "  · without table → design-table tasks (1.1 member_id, 1.2 name…)",
            "  · depend on channel + module",
            "2 multi-dim SSOT plan (task_ssot A–H + tdd.* per field)",
            "3 build system → code_register.register_id + field_tdd_rule each slice",
            "4 everything output traceable via register_id / task_id / request_id",
        ],
        "this_layer": {
            "id": "D_ontology_registry_builder",
            "pipeline": PIPELINE_ID,
            "gate": GATE_POLICY,
            "owns": [
                "human request capture (fn_request)",
                "research path reuse vs design-table (fn_research)",
                "system / module / version / channel ontology",
                "slice + field map (one slice → one function)",
                "register_id + code_register",
                "per-field TDD rules (field_tdd_rule) — region=+CC; phone local 11 for +86",
                "TACID / dev_task tree + work_queue",
                "naming law + goal.equation",
                "active vs rubbish / zombie filter",
                "task_ssot profile A–H + impl.* + tdd.* dims",
            ],
            "ui": "/managed",
            "api": "/api/managed",
            "builder_api": "/api/managed/build",
        },
        "not_this_layer": {
            "id": "orchestrator_downstream",
            "owns": [
                "template matcher at runtime",
                "exec.driver route (mcp | openclaw | sql | api)",
                "MCP / OpenClaw live execution",
                "Screen-Frame GUI evidence capture",
                "causal chain pack at run time",
                "pair dual-path QC execute",
            ],
            "note": (
                "channel.code=local_pc is environment ontology, NOT exec.driver. "
                "TDD rules are declared here; runtime enforcement can use field_tdd_rule."
            ),
        },
        "handoff_keys": [
            "fn_request.request_key",
            "code_register.register_id",
            "field_tdd_rule.id / rule_json",
            "slice_key",
            "plan.table (task_ssot)",
            "impl.function",
            "dev_task.id (slice_task_id)",
            "channel.code (env only)",
        ],
        "orchestrator_optional_dims": [
            "exec.driver",
            "exec.template_id",
            "trace.run_id",
            "qc.pair_ref",
            "qc.business_check",
        ],
        "flow": (
            "human request → research → multi_dim_ssot → build(register_id+tdd) → "
            "work_queue → handoff → orchestrator(run/evidence/QC)"
        ),
        "related_pipelines": {
            "A": "schema PRAGMA hard gate only",
            "B": "fail-handling / remediate",
            "C": "invoke trace + score (used by D, not driver route)",
            "D": "this layer",
        },
    }


# Built-in field TDD templates (DB-driven copies go into field_tdd_rule.rule_json)
# Law L1.2: region=+CC (e.g. +86); phone=local digits (e.g. 13800138000);
# e164 = compose only. See docs/ssot_member_phone_region.md
FIELD_TDD_TEMPLATES: dict[str, dict[str, Any]] = {
    "region": {
        "tdd_type_code": "text",
        "value_type": "string",
        "fail_class": "business_defect",
        "primary_key": True,
        "field_kind": "region",
        "storage_type": "calling_code_plus",
        "note": "region = +CC primary (e.g. +86). NOT the phone local number. ISO2 is display-only.",
        "example": "+86",
        "depends_on": [],
        "op": "match",
        "pattern": r"^\+\d{1,4}$",
        "rules": [
            {
                "op": "match",
                "pattern": r"^\+\d{1,4}$",
                "note": "calling code only: +86 / +852 / +886 / +1",
                "fail_class": "business_defect",
            }
        ],
        "iso2_lookup": {
            "+86": "CN",
            "+852": "HK",
            "+853": "MO",
            "+886": "TW",
            "+1": "US",
            "+81": "JP",
            "+82": "KR",
            "+65": "SG",
        },
    },
    "phone": {
        "tdd_type_code": "text",
        "value_type": "digit_string",
        "fail_class": "business_defect",
        "field_kind": "phone",
        "storage_type": "local_digit_string",
        "note": "phone = LOCAL only (e.g. 13800138000). No +. Not INT. Depends on region for length.",
        "example": "13800138000",
        "depends_on": ["region"],
        "op": "local_digits_by_region",
        "by_region": {
            "+86": {
                "pattern": r"^\d{11}$",
                "local_len": 11,
                "note": "China local 11 digits (not 10, not 12)",
            },
            "+852": {
                "pattern": r"^\d{8}$",
                "local_len": 8,
                "note": "Hong Kong 8 digits",
            },
            "+886": {
                "pattern": r"^\d{8,9}$",
                "local_len_min": 8,
                "local_len_max": 9,
                "note": "Taiwan mobile local",
            },
            "+1": {
                "pattern": r"^\d{10}$",
                "local_len": 10,
                "note": "NANP 10 digits local",
            },
        },
        "default": {"pattern": r"^\d{6,15}$", "note": "generic local digits"},
        "rules": [
            {
                "when": {"region": "+86"},
                "op": "match_local",
                "pattern": r"^\d{11}$",
                "note": "China local 11 digits",
                "fail_class": "business_defect",
            },
            {
                "when": {"region": "+886"},
                "op": "match_local",
                "pattern": r"^\d{8,9}$",
                "note": "Taiwan local",
                "fail_class": "business_defect",
            },
            {
                "when": {"region": "+852"},
                "op": "match_local",
                "pattern": r"^\d{8}$",
                "note": "Hong Kong local 8",
                "fail_class": "business_defect",
            },
            {
                "when": {"default": True},
                "op": "match_local",
                "pattern": r"^\d{6,15}$",
                "note": "generic local digits only",
                "fail_class": "business_defect",
            },
        ],
        "compose_with": ["region"],
        "compose_note": "e164 = region(+CC) + phone(local) for Pair QC pack only; do not store e164 as phone",
    },
    "country": {
        "tdd_type_code": "text",
        "value_type": "string",
        "fail_class": "business_defect",
        "field_kind": "country",
        "depends_on": ["region"],
        "note": "optional display name looked up from region (+CC)",
        "op": "lookup",
        "rules": [
            {
                "op": "lookup",
                "from_field": "region",
                "map": {
                    "+86": "China",
                    "+852": "Hong Kong",
                    "+853": "Macau",
                    "+886": "Taiwan",
                    "+1": "United States",
                    "+81": "Japan",
                    "+82": "Korea",
                    "+65": "Singapore",
                },
                "note": "country text from region",
            }
        ],
    },
    "name": {
        "tdd_type_code": "text",
        "value_type": "string",
        "fail_class": "business_defect",
        "field_kind": "name",
        "op": "range_len",
        "min": 1,
        "max": 80,
        "min_len": 1,
        "max_len": 80,
        "depends_on": [],
        "rules": [
            {"op": "range_len", "min": 1, "max": 80, "fail_class": "business_defect"},
        ],
    },
    "gender": {
        "tdd_type_code": "text",
        "value_type": "string",
        "fail_class": "business_defect",
        "field_kind": "gender",
        "op": "in_set",
        "values": ["M", "F", "X", "U"],
        "enum": ["M", "F", "X", "U"],
        "depends_on": [],
        "rules": [{"op": "in_set", "values": ["M", "F", "X", "U"]}],
    },
    "contact_method": {
        "tdd_type_code": "text",
        "value_type": "string",
        "fail_class": "business_defect",
        "field_kind": "contact_method",
        "op": "min_len",
        "min_len": 2,
        "value": 2,
        "depends_on": [],
        "enum_hint": ["phone", "email", "line", "wechat"],
        "rules": [{"op": "min_len", "value": 2}],
    },
    "member_id": {
        "tdd_type_code": "text",
        "value_type": "string",
        "op": "match",
        "pattern": r"^[A-Za-z0-9_-]{3,32}$",
        "rules": [
            {"op": "match", "pattern": r"^[A-Za-z0-9_-]{3,32}$", "note": "stable id"},
        ],
    },
    "address": {
        "tdd_type_code": "text",
        "value_type": "string",
        "fail_class": "business_defect",
        "field_kind": "address",
        "op": "text_length_clean",
        "min_len": 3,
        "max_len": 500,
        "trim_whitespace": True,
        "allow_newline": False,
        "depends_on": [],
        "note": "soft rule: only clean & length check, no strict address format regex",
        "rules": [
            {
                "op": "text_length_clean",
                "min_len": 3,
                "max_len": 500,
                "trim_whitespace": True,
                "allow_newline": False,
            }
        ],
    },
}

DEFAULT_DESIGN_FIELDS: tuple[str, ...] = (
    "member_id",
    "name",
    "region",
    "phone",
    "contact_method",
    "gender",
)


def contracts_doc() -> dict[str, Any]:
    """MCS0 machine-readable contracts."""
    return {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "law": [
            "value + value = output",
            "same pattern different values",
            "no register_id → not managed code",
            "profile A-H = dims not always child tasks",
            "rubbish/zombie mark only — no auto source delete",
            "channel.code = env ontology — not exec.driver",
        ],
        "layer": layer_architecture(),
        "profile_steps": list(PROFILE_STEPS),
        "required_profile_dims": list(REQUIRED_PROFILE_DIMS),
        "impl_dims": [
            IMPL_DIM_MODULE,
            IMPL_DIM_FUNCTION,
            IMPL_DIM_REQUIRED,
            IMPL_DIM_REGISTER,
        ],
        "label_grammar": {
            "membership_version": MEMBERSHIP_VERSION,
            "root": MEMBERSHIP_ROOT_LABEL,
            "slices": [s["label"] for s in MEMBERSHIP_SLICES],
            "note": "UNIQUE(version_id, task_label); mem-1.0 vs agent_db 1.1 no clash",
        },
        "tables": [
            "code_register",
            "fn_request",
            "fn_research",
            "field_tdd_rule",
            "onto_concept",
            "onto_link",
            "onto_binding",
            "onto_monitor_rollup",
            "task_ssot",
            "function_scoring",
            "function_invoke_trace",
            "dev_task",
            "dev_task_field",
            "tdd_type",
        ],
        "worker_rules": [
            "work active + incomplete only",
            "ignore rubbish unless un-rubbish",
            "coding claims need register_id",
            "plan lives in DB dims not chat only",
            "human request → research → multi-dim SSOT → build",
            "TDD per field is DB-driven (field_tdd_rule)",
            "do not treat /managed as full runtime orchestrator",
        ],
        "reject": [
            "wall-clock unique names",
            "code without register_id",
            "ontology as hard gate",
            "auto-delete source",
            "parallel experiment.db",
            "rebuild TACID/slice tables in orchestrator",
            "confuse channel.code with exec.driver",
            "build without research path (reuse vs design-table)",
        ],
        "related": {
            "A": "pragma_exact_set_gate",
            "B": "fail_handling",
            "C": "code_health",
            "D": PIPELINE_ID,
        },
        "phases": {
            "MCS0": "contracts + plan",
            "MCS1": "DDL",
            "MCS2": "seed + register",
            "MCS3": "managed_invoke require register",
            "MCS4": "worker clean report",
            "MCS5": "UI /managed",
            "MCS6": "selftest smoke",
            "MCS7": "layer map + handoff UI",
            "MCS8": "function builder flow + fn_request/research/tdd tables + UI",
        },
    }


def verify_managed_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """MCS1 — required tables present."""
    need = (
        "code_register",
        "fn_request",
        "fn_research",
        "field_tdd_rule",
        "onto_concept",
        "onto_link",
        "onto_binding",
        "onto_monitor_rollup",
        "task_ssot",
        "function_scoring",
        "function_invoke_trace",
        "dev_task",
        "module",
        "channel",
        "version_center",
    )
    missing = []
    for t in need:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (t,),
        ).fetchone()
        if not row:
            missing.append(t)
    cols = [
        r[1]
        for r in conn.execute("PRAGMA table_info(function_scoring)").fetchall()
    ]
    has_reg_col = "register_id" in cols
    return {
        "ok": not missing and has_reg_col,
        "gate": GATE_POLICY,
        "missing_tables": missing,
        "function_scoring_has_register_id": has_reg_col,
        "pipeline": PIPELINE_ID,
    }


def _get_or_create_dim(conn: sqlite3.Connection, table: str, code: str, name: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        f"INSERT INTO {table} (code, name) VALUES (?, ?)",
        (code, name),
    )
    return int(cur.lastrowid)


def _ensure_action(
    conn: sqlite3.Connection,
    *,
    element: str,
    action: str,
    code: str,
    name: str,
) -> int:
    row = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", (code,)
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO task_action_name (element, action, code, name, requires_tdd, status)
        VALUES (?, ?, ?, ?, 0, 'active')
        """,
        (element, action, code, name),
    )
    return int(cur.lastrowid)


def allocate_register_id(conn: sqlite3.Connection, *, prefix: str = "reg") -> str:
    """DB-unique register_id (not wall-clock as sole uniqueness)."""
    for _ in range(50):
        rid = f"{_sanitize_token(prefix, default='reg')}_{uuid.uuid4().hex[:12]}"
        hit = conn.execute(
            "SELECT 1 FROM code_register WHERE register_id = ?", (rid,)
        ).fetchone()
        if not hit:
            return rid
    raise RuntimeError("cannot allocate register_id")


def _code_register_has_location_cols(conn: sqlite3.Connection) -> bool:
    cols = {
        str(r[1])
        for r in conn.execute("PRAGMA table_info(code_register)").fetchall()
    }
    return "file_path" in cols and "line_start" in cols and "line_end" in cols


def get_code_register(
    conn: sqlite3.Connection,
    *,
    register_id: str | None = None,
    module_name: str | None = None,
    function_name: str | None = None,
) -> dict[str, Any] | None:
    has_loc = _code_register_has_location_cols(conn)
    cols_sql = (
        "id, register_id, module_name, function_name, task_id, tacid, "
        "system_task_id, slice_task_id, system_key, slice_key, status, "
        + (
            "file_path, line_start, line_end, code_span, "
            if has_loc
            else ""
        )
        + "notes, source, updated_at, created_at"
    )
    if register_id:
        row = conn.execute(
            f"SELECT {cols_sql} FROM code_register WHERE register_id = ?",
            (register_id.strip(),),
        ).fetchone()
    elif module_name and function_name:
        row = conn.execute(
            f"""
            SELECT {cols_sql} FROM code_register
            WHERE module_name = ? AND function_name = ?
            """,
            (module_name.strip(), function_name.strip()),
        ).fetchone()
    else:
        return None
    if not row:
        return None
    keys = [
        "id",
        "register_id",
        "module_name",
        "function_name",
        "task_id",
        "tacid",
        "system_task_id",
        "slice_task_id",
        "system_key",
        "slice_key",
        "status",
    ]
    if has_loc:
        keys.extend(["file_path", "line_start", "line_end", "code_span"])
    keys.extend(["notes", "source", "updated_at", "created_at"])
    # Always index by position — do not rely on row_factory / Row keys
    out = {k: row[i] for i, k in enumerate(keys)}
    if not has_loc:
        out.setdefault("file_path", None)
        out.setdefault("line_start", None)
        out.setdefault("line_end", None)
        out.setdefault("code_span", None)
    return out


def bind_register_source_location(
    conn: sqlite3.Connection,
    *,
    register_id: str | None = None,
    module_name: str | None = None,
    function_name: str | None = None,
    file_path: str,
    line_start: int,
    line_end: int | None = None,
    code_span: str | None = None,
    read_file: bool = True,
    max_span_chars: int = 4000,
    commit: bool = True,
) -> dict[str, Any]:
    """CH7/MCS9 — bind register to file + line range (+ optional code body).

    Example: test.py lines 1-3, status stays on register; usage/pass/fail live
    on function_scoring. Never auto-deletes source.
    """
    path = (file_path or "").strip().replace("\\", "/")
    if not path:
        raise ValueError("file_path required")
    try:
        ls = int(line_start)
    except (TypeError, ValueError) as e:
        raise ValueError("line_start must be int") from e
    if ls < 1:
        raise ValueError("line_start must be >= 1")
    le = int(line_end) if line_end is not None else ls
    if le < ls:
        raise ValueError("line_end must be >= line_start")

    reg = get_code_register(
        conn,
        register_id=register_id,
        module_name=module_name,
        function_name=function_name,
    )
    if not reg:
        raise ValueError("code_register row not found")

    span = code_span
    if span is None and read_file:
        try:
            # resolve relative to repo root (this file's dir)
            root = os.path.dirname(os.path.abspath(__file__))
            abs_path = path if os.path.isabs(path) else os.path.join(root, path)
            with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            chunk = "".join(lines[ls - 1 : le])
            span = chunk[:max_span_chars]
        except OSError:
            span = None

    rid = str(reg["register_id"])
    mod = str(reg["module_name"])
    fn = str(reg["function_name"])

    if _code_register_has_location_cols(conn):
        conn.execute(
            """
            UPDATE code_register
            SET file_path = ?, line_start = ?, line_end = ?, code_span = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE register_id = ?
            """,
            (path, ls, le, span, rid),
        )
    else:
        raise RuntimeError("code_register missing file_path columns — run migrate")

    # mirror onto function_scoring when columns exist
    try:
        scols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(function_scoring)").fetchall()
        }
        if "file_path" in scols:
            conn.execute(
                """
                UPDATE function_scoring
                SET file_path = ?, line_start = ?, line_end = ?, code_span = ?,
                    register_id = COALESCE(register_id, ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE module_name = ? AND function_name = ?
                """,
                (path, ls, le, span, rid, mod, fn),
            )
    except sqlite3.Error:
        pass

    # multi-dim SSOT location dims on bound task when present
    tid = reg.get("task_id")
    if tid is not None:
        try:
            from db_schema import upsert_task_ssot

            for dim_key, value_text, value_type, sort_order in (
                ("impl.file_path", path, "string", 90),
                ("impl.line_start", str(ls), "number", 91),
                ("impl.line_end", str(le), "number", 92),
                ("profile.F.file_line", f"{path}:{ls}-{le}", "string", 93),
            ):
                upsert_task_ssot(
                    conn,
                    task_id=int(tid),
                    dim_key=dim_key,
                    value_text=value_text,
                    value_type=value_type,
                    source="source_location",
                    sort_order=sort_order,
                    notes="file+line bind",
                    commit=False,
                )
        except Exception:
            pass

    if commit:
        conn.commit()

    refreshed = get_code_register(conn, register_id=rid)
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "register_id": rid,
        "module_name": mod,
        "function_name": fn,
        "file_path": path,
        "line_start": ls,
        "line_end": le,
        "code_span": span,
        "register": refreshed,
        "law": "usage/pass/fail on function_scoring; location on code_register",
    }


def upsert_onto_concept(
    conn: sqlite3.Connection,
    *,
    code: str,
    kind: str,
    title: str,
    status: str = "active",
    notes: str | None = None,
    source: str = "managed_coding",
    commit: bool = False,
) -> dict[str, Any]:
    code = (code or "").strip()
    if not code:
        raise ValueError("onto concept code required")
    existing = conn.execute(
        "SELECT id FROM onto_concept WHERE code = ?", (code,)
    ).fetchone()
    if existing:
        cid = int(existing[0])
        conn.execute(
            """
            UPDATE onto_concept
            SET kind = ?, title = ?, status = ?, notes = ?, source = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (kind, title, status, notes, source, cid),
        )
        action = "updated"
    else:
        cur = conn.execute(
            """
            INSERT INTO onto_concept (code, kind, title, status, notes, source)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (code, kind, title, status, notes, source),
        )
        cid = int(cur.lastrowid)
        action = "inserted"
    if commit:
        conn.commit()
    return {"id": cid, "code": code, "kind": kind, "action": action}


def link_onto(
    conn: sqlite3.Connection,
    *,
    from_code: str,
    to_code: str,
    rel: str = "contains",
    commit: bool = False,
) -> dict[str, Any]:
    a = conn.execute(
        "SELECT id FROM onto_concept WHERE code = ?", (from_code,)
    ).fetchone()
    b = conn.execute(
        "SELECT id FROM onto_concept WHERE code = ?", (to_code,)
    ).fetchone()
    if not a or not b:
        raise ValueError(f"missing concept for link {from_code} -> {to_code}")
    fid, tid = int(a[0]), int(b[0])
    existing = conn.execute(
        """
        SELECT id FROM onto_link
        WHERE from_concept_id = ? AND to_concept_id = ? AND rel = ?
        """,
        (fid, tid, rel),
    ).fetchone()
    if existing:
        return {"id": int(existing[0]), "action": "exists"}
    cur = conn.execute(
        """
        INSERT INTO onto_link (from_concept_id, to_concept_id, rel)
        VALUES (?, ?, ?)
        """,
        (fid, tid, rel),
    )
    if commit:
        conn.commit()
    return {"id": int(cur.lastrowid), "action": "inserted"}


def bind_onto(
    conn: sqlite3.Connection,
    *,
    concept_code: str,
    bind_type: str,
    bind_key: str,
    bind_id: int | None = None,
    notes: str | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id FROM onto_concept WHERE code = ?", (concept_code,)
    ).fetchone()
    if not row:
        raise ValueError(f"unknown concept {concept_code}")
    cid = int(row[0])
    existing = conn.execute(
        """
        SELECT id FROM onto_binding
        WHERE concept_id = ? AND bind_type = ? AND bind_key = ?
        """,
        (cid, bind_type, bind_key),
    ).fetchone()
    if existing:
        bid = int(existing[0])
        conn.execute(
            """
            UPDATE onto_binding
            SET bind_id = ?, notes = ?
            WHERE id = ?
            """,
            (bind_id, notes, bid),
        )
        action = "updated"
    else:
        cur = conn.execute(
            """
            INSERT INTO onto_binding (concept_id, bind_type, bind_key, bind_id, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (cid, bind_type, bind_key, bind_id, notes),
        )
        bid = int(cur.lastrowid)
        action = "inserted"
    if commit:
        conn.commit()
    return {"id": bid, "action": action, "concept_id": cid}


def register_managed_function(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    tacid: str,
    module_name: str,
    function_name: str | None = None,
    base_name: str | None = None,
    system_task_id: int | None = None,
    slice_task_id: int | None = None,
    system_key: str | None = None,
    slice_key: str | None = None,
    required: bool = True,
    status: str = "active",
    source: str = "managed_coding",
    notes: str | None = None,
    file_path: str | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
    code_span: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Allocate unique fn name + register_id; write task_ssot impl.* + code_register.

    This is the only blessed path for agent-generated coding enrollment.
    Optional file_path + line_start/line_end bind source location (CH7).
    """
    from code_health import (
        allocate_unique_function_name,
        ensure_scoring_placeholder,
        get_function_score,
    )
    from db_schema import upsert_task_ssot

    mod = (module_name or "").strip() or "unknown"
    if function_name and function_name.strip():
        fn = function_name.strip()
    else:
        fn = allocate_unique_function_name(
            conn,
            module_name=mod,
            base_name=base_name or (slice_key or tacid or "fn"),
        )
    label = (tacid or "").strip() or "unknown"
    if status not in REGISTER_STATUSES:
        status = "draft"

    has_loc = _code_register_has_location_cols(conn)
    fp = (file_path or "").strip().replace("\\", "/") or None
    ls = int(line_start) if line_start is not None else None
    le = int(line_end) if line_end is not None else ls

    existing = get_code_register(conn, module_name=mod, function_name=fn)
    if existing and existing.get("register_id"):
        register_id = str(existing.get("register_id"))
        if has_loc:
            conn.execute(
                """
                UPDATE code_register
                SET task_id = ?, tacid = ?, system_task_id = ?, slice_task_id = ?,
                    system_key = ?, slice_key = ?, status = ?, notes = ?, source = ?,
                    file_path = COALESCE(?, file_path),
                    line_start = COALESCE(?, line_start),
                    line_end = COALESCE(?, line_end),
                    code_span = COALESCE(?, code_span),
                    updated_at = CURRENT_TIMESTAMP
                WHERE register_id = ?
                """,
                (
                    int(task_id),
                    label,
                    system_task_id,
                    slice_task_id if slice_task_id is not None else int(task_id),
                    system_key,
                    slice_key,
                    status,
                    notes,
                    source,
                    fp,
                    ls,
                    le,
                    code_span,
                    register_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE code_register
                SET task_id = ?, tacid = ?, system_task_id = ?, slice_task_id = ?,
                    system_key = ?, slice_key = ?, status = ?, notes = ?, source = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE register_id = ?
                """,
                (
                    int(task_id),
                    label,
                    system_task_id,
                    slice_task_id if slice_task_id is not None else int(task_id),
                    system_key,
                    slice_key,
                    status,
                    notes,
                    source,
                    register_id,
                ),
            )
        reg_action = "updated"
    else:
        register_id = allocate_register_id(
            conn, prefix=f"reg_{_sanitize_token(mod)}_{_sanitize_token(fn)}"
        )
        if has_loc:
            conn.execute(
                """
                INSERT INTO code_register (
                    register_id, module_name, function_name, task_id, tacid,
                    system_task_id, slice_task_id, system_key, slice_key,
                    status, file_path, line_start, line_end, code_span, notes, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    register_id,
                    mod,
                    fn,
                    int(task_id),
                    label,
                    system_task_id,
                    slice_task_id if slice_task_id is not None else int(task_id),
                    system_key,
                    slice_key,
                    status,
                    fp,
                    ls,
                    le,
                    code_span,
                    notes,
                    source,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO code_register (
                    register_id, module_name, function_name, task_id, tacid,
                    system_task_id, slice_task_id, system_key, slice_key,
                    status, notes, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    register_id,
                    mod,
                    fn,
                    int(task_id),
                    label,
                    system_task_id,
                    slice_task_id if slice_task_id is not None else int(task_id),
                    system_key,
                    slice_key,
                    status,
                    notes,
                    source,
                ),
            )
        reg_action = "inserted"

    dims_spec = [
        (IMPL_DIM_MODULE, mod, "string", 10),
        (IMPL_DIM_FUNCTION, fn, "string", 20),
        (IMPL_DIM_REQUIRED, "true" if required else "false", "bool", 30),
        (IMPL_DIM_REGISTER, register_id, "string", 40),
        ("profile.D.function", fn, "string", 50),
        ("profile.G.module", mod, "string", 60),
        ("profile.H.register_id", register_id, "string", 70),
        ("profile.F.trace", "required", "string", 80),
    ]
    written = []
    for dim_key, value_text, value_type, sort_order in dims_spec:
        written.append(
            upsert_task_ssot(
                conn,
                task_id=int(task_id),
                dim_key=dim_key,
                value_text=value_text,
                value_type=value_type,
                source=source,
                sort_order=sort_order,
                notes=notes or f"managed register {mod}.{fn}",
                commit=False,
            )
        )

    ensure_scoring_placeholder(
        conn,
        module_name=mod,
        function_name=fn,
        static_tacids=[label],
        commit=False,
    )
    # attach register_id (+ optional file/line) on scoring if columns exist
    try:
        scols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(function_scoring)").fetchall()
        }
        if "file_path" in scols and fp:
            conn.execute(
                """
                UPDATE function_scoring
                SET register_id = ?, file_path = ?, line_start = ?, line_end = ?,
                    code_span = COALESCE(?, code_span),
                    updated_at = CURRENT_TIMESTAMP
                WHERE module_name = ? AND function_name = ?
                """,
                (register_id, fp, ls, le, code_span, mod, fn),
            )
        else:
            conn.execute(
                """
                UPDATE function_scoring
                SET register_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE module_name = ? AND function_name = ?
                """,
                (register_id, mod, fn),
            )
    except sqlite3.Error:
        pass

    if fp and ls is not None:
        try:
            bind_register_source_location(
                conn,
                register_id=register_id,
                file_path=fp,
                line_start=int(ls),
                line_end=le,
                code_span=code_span,
                read_file=code_span is None,
                commit=False,
            )
        except Exception:
            pass

    if commit:
        conn.commit()

    scoring = get_function_score(conn, mod, fn)
    reg_row = get_code_register(conn, register_id=register_id)
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "register_id": register_id,
        "register_action": reg_action,
        "module_name": mod,
        "function_name": fn,
        "task_id": int(task_id),
        "tacid": label,
        "system_key": system_key,
        "slice_key": slice_key,
        "status": status,
        "file_path": (reg_row or {}).get("file_path") or fp,
        "line_start": (reg_row or {}).get("line_start") if reg_row else ls,
        "line_end": (reg_row or {}).get("line_end") if reg_row else le,
        "dims": written,
        "scoring": scoring,
        "law": "no register_id → not managed code; usage/pass/fail on scoring",
    }


def managed_invoke(
    fn: Callable[..., Any],
    *,
    register_id: str | None = None,
    module_name: str | None = None,
    function_name: str | None = None,
    tacid: str | None = None,
    task_id: int | None = None,
    require_register: bool = True,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    reraise: bool = True,
    commit: bool | None = None,
    source: str = "managed_coding",
) -> dict[str, Any]:
    """Invoke only through code_health.function_invoker after register lookup.

    If require_register and no code_register row → refuse (not a structure gate;
    refuse is managed-coding policy so workers never run untracked code paths).
    """
    from code_health import function_invoker

    own = conn is None
    do_commit = bool(own) if commit is None else bool(commit)
    if own:
        from db_schema import get_db_path

        path = get_db_path(db_path)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON;")
    assert conn is not None

    try:
        reg = None
        if register_id:
            reg = get_code_register(conn, register_id=register_id)
        elif module_name and function_name:
            reg = get_code_register(
                conn, module_name=module_name, function_name=function_name
            )
        if require_register and reg is None:
            return {
                "ok": False,
                "error": "unregistered_function",
                "message": (
                    "managed_invoke refused: no code_register row. "
                    "All coding must carry register_id."
                ),
                "gate": GATE_POLICY,
                "pipeline": PIPELINE_ID,
                "register_id": register_id,
                "module_name": module_name,
                "function_name": function_name,
            }
        if reg is not None and str(reg.get("status") or "") == "rubbish":
            return {
                "ok": False,
                "error": "rubbish_register",
                "message": "register marked rubbish — worker should not spend time",
                "gate": GATE_POLICY,
                "register_id": reg.get("register_id"),
                "status": "rubbish",
            }

        mod = (
            module_name
            or (reg or {}).get("module_name")
            or getattr(fn, "__module__", None)
            or "unknown"
        )
        name = (
            function_name
            or (reg or {}).get("function_name")
            or getattr(fn, "__name__", None)
            or "unknown"
        )
        tac = (tacid or (reg or {}).get("tacid") or "unknown")
        tid = task_id if task_id is not None else (reg or {}).get("task_id")
        rid = register_id or (reg or {}).get("register_id")
        if rid is not None:
            rid = str(rid)

        result = function_invoker(
            fn,
            tacid=str(tac),
            module_name=str(mod),
            function_name=str(name),
            task_id=int(tid) if tid is not None else None,
            source=source,
            conn=conn,
            args=args,
            kwargs=kwargs,
            reraise=reraise,
            commit=do_commit,
        )
        if not isinstance(result, dict):
            result = {"ok": bool(result), "result": result}
        result["register_id"] = rid
        result["pipeline"] = PIPELINE_ID
        result["managed"] = True
        result["code_register"] = {
            "register_id": rid,
            "module_name": str(mod),
            "function_name": str(name),
            "status": (reg or {}).get("status"),
        }
        return result
    finally:
        if own:
            conn.close()


def apply_slice_profile_dims(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    system_key: str,
    slice_key: str,
    table_name: str,
    module_code: str,
    tdd_code: str = "text",
    api_path: str | None = None,
    function_name: str | None = None,
    register_id: str | None = None,
    slice_status: str = SLICE_STATUS_ACTIVE,
    source: str = "managed_coding",
    tdd_rule: dict[str, Any] | None = None,
    request_id: int | None = None,
    commit: bool = False,
) -> list[dict[str, Any]]:
    """Write A–H profile dims for one slice (checklist in task_ssot)."""
    from db_schema import upsert_task_ssot

    api_path = api_path or f"/api/{system_key}/{{{slice_key}}}"
    tmpl = tdd_rule or FIELD_TDD_TEMPLATES.get(slice_key) or {
        "tdd_type_code": tdd_code,
        "rules": [],
    }
    tdd_code = str(tmpl.get("tdd_type_code") or tdd_code or "text")
    depends = tmpl.get("depends_on") or tmpl.get("compose_with") or []
    values = {
        "profile.A.field": f"{table_name}.{slice_key}",
        "profile.B.table": table_name,
        "profile.C.api": api_path,
        "profile.D.function": function_name or "",
        "profile.E.tdd": tdd_code,
        "profile.F.trace": "required",
        "profile.G.module": module_code,
        "profile.H.register_id": register_id or "",
        "slice.key": slice_key,
        "slice.status": slice_status if slice_status in SLICE_STATUSES else SLICE_STATUS_DRAFT,
        "system.key": system_key,
        "tdd.type": tdd_code,
        "tdd.rules_json": json.dumps(tmpl.get("rules") or [], ensure_ascii=False),
        "tdd.depends_on": ",".join(str(x) for x in depends),
        "tdd.compose_note": str(tmpl.get("compose_note") or ""),
    }
    if request_id is not None:
        values["builder.request_id"] = str(int(request_id))
    out = []
    sort = 100
    for dim_key, value_text in values.items():
        out.append(
            upsert_task_ssot(
                conn,
                task_id=int(task_id),
                dim_key=dim_key,
                value_text=str(value_text),
                value_type="string",
                source=source,
                sort_order=sort,
                notes=f"slice profile {system_key}.{slice_key}",
                commit=False,
            )
        )
        sort += 10
    if commit:
        conn.commit()
    return out


def slice_completeness(conn: sqlite3.Connection, task_id: int) -> dict[str, Any]:
    """Worker view: which profile dims missing."""
    rows = conn.execute(
        "SELECT dim_key, value_text FROM task_ssot WHERE task_id = ?",
        (int(task_id),),
    ).fetchall()
    dims = {str(k): ("" if v is None else str(v).strip()) for k, v in rows}
    is_system_root = bool(dims.get("system.key")) and not bool(dims.get("slice.key"))
    missing = []
    if not is_system_root:
        for key in REQUIRED_PROFILE_DIMS:
            val = dims.get(key, "")
            if not val:
                missing.append(key)
    # register_id must be non-empty for coding-complete (slices only)
    reg = dims.get("profile.H.register_id") or dims.get(IMPL_DIM_REGISTER) or ""
    if is_system_root:
        coding_ready = True
        complete = True
        status = dims.get("slice.status") or SLICE_STATUS_ACTIVE
        worker_action = "system_map"
    else:
        coding_ready = bool(reg) and not missing
        complete = len(missing) == 0
        status = dims.get("slice.status") or SLICE_STATUS_DRAFT
        worker_action = (
            "skip_rubbish"
            if status in (SLICE_STATUS_RUBBISH, SLICE_STATUS_DEPRECATED)
            else (
                "fill_profile"
                if missing
                else ("ready" if coding_ready else "register_code")
            )
        )
    return {
        "task_id": int(task_id),
        "missing_dims": missing,
        "complete": complete,
        "coding_ready": coding_ready,
        "register_id": reg or None,
        "slice_status": status,
        "dims": dims,
        "is_system_root": is_system_root,
        "worker_action": worker_action,
    }


def mark_register_status(
    conn: sqlite3.Connection,
    *,
    register_id: str,
    status: str,
    commit: bool = True,
) -> dict[str, Any]:
    if status not in REGISTER_STATUSES:
        raise ValueError(f"invalid status {status}")
    cur = conn.execute(
        """
        UPDATE code_register
        SET status = ?, updated_at = CURRENT_TIMESTAMP
        WHERE register_id = ?
        """,
        (status, register_id.strip()),
    )
    if commit:
        conn.commit()
    return {
        "ok": cur.rowcount > 0,
        "register_id": register_id,
        "status": status,
        "gate": GATE_POLICY,
        "note": "mark only — no source delete",
    }


def mark_demo_noise_rubbish(
    conn: sqlite3.Connection,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Mark ch.demo.*_2/_3 style noise + duplicate membership regs so workers skip (no delete)."""
    from db_schema import upsert_task_ssot

    all_demo = conn.execute(
        """
        SELECT id, task_label FROM dev_task
        WHERE task_label LIKE 'ch.demo.%'
        """
    ).fetchall()
    marked_tasks = []
    for tid, label in all_demo:
        lab = str(label or "")
        # keep first clean demo children; mark _2 _3 suffixes rubbish via ssot
        if re.search(r"_\d+$", lab) or re.search(r"\.[a-z]+_\d+$", lab):
            upsert_task_ssot(
                conn,
                task_id=int(tid),
                dim_key="slice.status",
                value_text=SLICE_STATUS_RUBBISH,
                value_type="string",
                source="managed_coding.cleanup",
                sort_order=5,
                notes="demo noise — worker skip",
                commit=False,
            )
            marked_tasks.append({"task_id": int(tid), "task_label": lab})

    marked_regs = []
    # demo tacid noise
    regs = conn.execute(
        """
        SELECT register_id, tacid, function_name FROM code_register
        WHERE tacid LIKE 'ch.demo.%'
        """
    ).fetchall()
    for rid, tacid, fn in regs:
        tac = str(tacid or "")
        if re.search(r"_\d+", tac) or re.search(r"_\d+$", str(fn or "")):
            conn.execute(
                """
                UPDATE code_register
                SET status = 'rubbish', updated_at = CURRENT_TIMESTAMP
                WHERE register_id = ?
                """,
                (rid,),
            )
            marked_regs.append({"register_id": rid, "tacid": tac, "reason": "demo_noise"})

    # duplicate membership functions: keep lowest id per (system_key, slice_key); rest rubbish
    dups = conn.execute(
        """
        SELECT system_key, slice_key, MIN(id) AS keep_id
        FROM code_register
        WHERE system_key IS NOT NULL AND slice_key IS NOT NULL
          AND status != 'rubbish'
        GROUP BY system_key, slice_key
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    for system_key, slice_key, keep_id in dups:
        rows = conn.execute(
            """
            SELECT register_id, id FROM code_register
            WHERE system_key = ? AND slice_key = ? AND status != 'rubbish'
            """,
            (system_key, slice_key),
        ).fetchall()
        for rid, rid_pk in rows:
            if int(rid_pk) == int(keep_id):
                continue
            conn.execute(
                """
                UPDATE code_register
                SET status = 'rubbish', updated_at = CURRENT_TIMESTAMP,
                    notes = COALESCE(notes, '') || ' | dup superseded'
                WHERE register_id = ?
                """,
                (rid,),
            )
            marked_regs.append(
                {
                    "register_id": rid,
                    "tacid": f"{system_key}.{slice_key}",
                    "reason": "duplicate_slice_register",
                }
            )

    if commit:
        conn.commit()
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "marked_tasks": marked_tasks,
        "marked_registers": marked_regs,
        "note": "mark only; no source delete; workers skip rubbish",
    }


def seed_membership_system(
    conn: sqlite3.Connection,
    *,
    commit: bool = True,
    register_functions: bool = True,
) -> dict[str, Any]:
    """Idempotent Task-1 membership system + slices 1.1–1.5 + ontology + registers."""
    out: dict[str, Any] = {
        "system_key": MEMBERSHIP_SYSTEM_KEY,
        "created_tasks": 0,
        "slices": [],
        "registers": [],
        "onto": [],
    }

    channel_id = _get_or_create_dim(conn, "channel", "local_pc", "Local PC")
    module_id = _get_or_create_dim(
        conn, "module", MEMBERSHIP_MODULE_CODE, "Membership System"
    )
    act_sys = _ensure_action(
        conn,
        element="system",
        action="managed",
        code=ACTION_SYSTEM,
        name="Managed system root",
    )
    act_slice = _ensure_action(
        conn,
        element="slice",
        action="managed",
        code=ACTION_SLICE,
        name="Managed field slice",
    )

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, module_id, MEMBERSHIP_VERSION),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (
                channel_id,
                module_id,
                MEMBERSHIP_VERSION,
                "Membership managed coding spine",
                "Task 1 ontology + slices; register_id law; gate=never",
            ),
        )
        version_id = int(cur.lastrowid)

    equation_fields = [s["key"] for s in MEMBERSHIP_SLICES]
    equation = " + ".join(equation_fields) + f" = {MEMBERSHIP_OUTPUT}"
    root_payload = {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "system_key": MEMBERSHIP_SYSTEM_KEY,
        "goal_type": "build_system",
        "goal_text": "I need a membership system",
        "goal_equation": equation,
        "output": MEMBERSHIP_OUTPUT,
        "table_plan": MEMBERSHIP_TABLE,
        "inputs": equation_fields,
        "worker_rule": "active+incomplete only; skip rubbish; coding needs register_id",
    }

    root = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, MEMBERSHIP_ROOT_LABEL),
    ).fetchone()
    if root:
        root_id = int(root[0])
        conn.execute(
            """
            UPDATE dev_task
            SET title = ?, payload_json = ?, module_id = ?, channel_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "Membership system (Task 1)",
                json.dumps(root_payload, ensure_ascii=False),
                module_id,
                channel_id,
                root_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                channel_id,
                module_id,
                act_sys,
                version_id,
                MEMBERSHIP_ROOT_LABEL,
                "Membership system (Task 1)",
                json.dumps(root_payload, ensure_ascii=False),
            ),
        )
        root_id = int(cur.lastrowid)
        out["created_tasks"] += 1

    from db_schema import upsert_task_ssot

    root_dims = [
        (10, "system.key", MEMBERSHIP_SYSTEM_KEY),
        (20, "system.output", MEMBERSHIP_OUTPUT),
        (30, "goal.text", "I need a membership system"),
        (40, "goal.equation", equation),
        (50, "plan.table", MEMBERSHIP_TABLE),
        (60, "plan.pattern", "value+value=output"),
        (70, "worker.scope", "active_incomplete_only"),
        (80, "coding.law", "register_id_required"),
    ]
    for sort_order, dim_key, value_text in root_dims:
        upsert_task_ssot(
            conn,
            task_id=root_id,
            dim_key=dim_key,
            value_text=value_text,
            value_type="string",
            source="managed_coding.seed",
            sort_order=sort_order,
            notes="membership system root",
            commit=False,
        )

    # Ontology Task-1 map
    sys_code = f"sys.{MEMBERSHIP_SYSTEM_KEY}"
    out["onto"].append(
        upsert_onto_concept(
            conn,
            code=sys_code,
            kind="system",
            title="Membership system",
            notes="Task 1 root",
            commit=False,
        )
    )
    ch_code = "ch.local_pc"
    out["onto"].append(
        upsert_onto_concept(
            conn, code=ch_code, kind="channel", title="Local PC", commit=False
        )
    )
    mod_code = f"mod.{MEMBERSHIP_MODULE_CODE}"
    out["onto"].append(
        upsert_onto_concept(
            conn,
            code=mod_code,
            kind="module",
            title="Membership",
            commit=False,
        )
    )
    link_onto(conn, from_code=sys_code, to_code=ch_code, rel="on_channel", commit=False)
    link_onto(conn, from_code=sys_code, to_code=mod_code, rel="uses_module", commit=False)
    bind_onto(
        conn,
        concept_code=sys_code,
        bind_type="task",
        bind_key=MEMBERSHIP_ROOT_LABEL,
        bind_id=root_id,
        notes="Task 1",
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=sys_code,
        bind_type="version",
        bind_key=MEMBERSHIP_VERSION,
        bind_id=version_id,
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=mod_code,
        bind_type="module",
        bind_key=MEMBERSHIP_MODULE_CODE,
        bind_id=module_id,
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=ch_code,
        bind_type="channel",
        bind_key="local_pc",
        bind_id=channel_id,
        commit=False,
    )
    bind_onto(
        conn,
        concept_code=sys_code,
        bind_type="table",
        bind_key=MEMBERSHIP_TABLE,
        commit=False,
    )

    for spec in MEMBERSHIP_SLICES:
        label = spec["label"]
        skey = spec["key"]
        existing = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, label),
        ).fetchone()
        slice_payload = {
            "pipeline": PIPELINE_ID,
            "system_key": MEMBERSHIP_SYSTEM_KEY,
            "slice_key": skey,
            "parent_label": MEMBERSHIP_ROOT_LABEL,
            "table": MEMBERSHIP_TABLE,
            "pattern": "value+value=output",
            "profile": [p["code"] for p in PROFILE_STEPS],
        }
        if existing:
            slice_id = int(existing[0])
            conn.execute(
                """
                UPDATE dev_task
                SET title = ?, payload_json = ?, parent_task_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    f"Membership slice: {skey}",
                    json.dumps(slice_payload, ensure_ascii=False),
                    root_id,
                    slice_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO dev_task
                    (parent_task_id, channel_id, module_id, action_name_id, version_id,
                     task_label, title, payload_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    root_id,
                    channel_id,
                    module_id,
                    act_slice,
                    version_id,
                    label,
                    f"Membership slice: {skey}",
                    json.dumps(slice_payload, ensure_ascii=False),
                ),
            )
            slice_id = int(cur.lastrowid)
            out["created_tasks"] += 1

        apply_slice_profile_dims(
            conn,
            task_id=slice_id,
            system_key=MEMBERSHIP_SYSTEM_KEY,
            slice_key=skey,
            table_name=MEMBERSHIP_TABLE,
            module_code=MEMBERSHIP_MODULE_CODE,
            tdd_code=str(spec.get("tdd") or "text"),
            slice_status=SLICE_STATUS_ACTIVE,
            source="managed_coding.seed",
            commit=False,
        )

        slice_concept = f"slice.{MEMBERSHIP_SYSTEM_KEY}.{skey}"
        upsert_onto_concept(
            conn,
            code=slice_concept,
            kind="slice",
            title=f"{label} {skey}",
            commit=False,
        )
        link_onto(conn, from_code=sys_code, to_code=slice_concept, rel="has_slice", commit=False)
        bind_onto(
            conn,
            concept_code=slice_concept,
            bind_type="task",
            bind_key=label,
            bind_id=slice_id,
            commit=False,
        )
        bind_onto(
            conn,
            concept_code=slice_concept,
            bind_type="tacid",
            bind_key=label,
            bind_id=slice_id,
            commit=False,
        )

        reg_info = None
        if register_functions:
            base_fn = f"member_{_sanitize_token(skey)}"
            # Idempotent: reuse existing register for this slice task if present
            existing_reg = conn.execute(
                """
                SELECT register_id, function_name, module_name
                FROM code_register
                WHERE slice_task_id = ? AND status IN ('active', 'draft', 'zombie')
                ORDER BY id
                LIMIT 1
                """,
                (slice_id,),
            ).fetchone()
            if not existing_reg:
                existing_reg = conn.execute(
                    """
                    SELECT register_id, function_name, module_name
                    FROM code_register
                    WHERE system_key = ? AND slice_key = ?
                      AND status IN ('active', 'draft', 'zombie')
                    ORDER BY id
                    LIMIT 1
                    """,
                    (MEMBERSHIP_SYSTEM_KEY, skey),
                ).fetchone()
            if existing_reg:
                reg_info = register_managed_function(
                    conn,
                    task_id=slice_id,
                    tacid=label,
                    module_name=str(existing_reg[2] or MEMBERSHIP_MODULE_CODE),
                    function_name=str(existing_reg[1]),
                    system_task_id=root_id,
                    slice_task_id=slice_id,
                    system_key=MEMBERSHIP_SYSTEM_KEY,
                    slice_key=skey,
                    required=True,
                    status="active",
                    source="managed_coding.seed",
                    notes=f"membership {label} {skey} (reuse)",
                    commit=False,
                )
            else:
                reg_info = register_managed_function(
                    conn,
                    task_id=slice_id,
                    tacid=label,
                    module_name=MEMBERSHIP_MODULE_CODE,
                    base_name=base_fn,
                    system_task_id=root_id,
                    slice_task_id=slice_id,
                    system_key=MEMBERSHIP_SYSTEM_KEY,
                    slice_key=skey,
                    required=True,
                    status="active",
                    source="managed_coding.seed",
                    notes=f"membership {label} {skey}",
                    commit=False,
                )
            # refresh profile H/D after register
            apply_slice_profile_dims(
                conn,
                task_id=slice_id,
                system_key=MEMBERSHIP_SYSTEM_KEY,
                slice_key=skey,
                table_name=MEMBERSHIP_TABLE,
                module_code=MEMBERSHIP_MODULE_CODE,
                tdd_code=str(spec.get("tdd") or "text"),
                function_name=reg_info.get("function_name"),
                register_id=reg_info.get("register_id"),
                slice_status=SLICE_STATUS_ACTIVE,
                source="managed_coding.seed",
                commit=False,
            )
            fn_concept = (
                f"fn.{MEMBERSHIP_MODULE_CODE}.{reg_info.get('function_name')}"
            )
            upsert_onto_concept(
                conn,
                code=fn_concept,
                kind="function",
                title=str(reg_info.get("function_name")),
                commit=False,
            )
            link_onto(
                conn, from_code=mod_code, to_code=fn_concept, rel="has_function", commit=False
            )
            link_onto(
                conn, from_code=slice_concept, to_code=fn_concept, rel="implements", commit=False
            )
            bind_onto(
                conn,
                concept_code=fn_concept,
                bind_type="register",
                bind_key=str(reg_info.get("register_id")),
                bind_id=slice_id,
                commit=False,
            )
            out["registers"].append(
                {
                    "register_id": reg_info.get("register_id"),
                    "function_name": reg_info.get("function_name"),
                    "tacid": label,
                }
            )

        out["slices"].append(
            {
                "task_id": slice_id,
                "task_label": label,
                "slice_key": skey,
                "register_id": (reg_info or {}).get("register_id"),
                "function_name": (reg_info or {}).get("function_name"),
            }
        )

    # Canonical field_tdd_rule rows (shared loader SSOT on agent.db)
    try:
        from field_tdd import import_membership_field_tdd

        out["field_tdd"] = import_membership_field_tdd(
            conn, ensure_address=True, commit=False
        )
    except Exception as e:
        out["field_tdd"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # rollup
    report = worker_clean_report(conn, system_key=MEMBERSHIP_SYSTEM_KEY)
    conn.execute(
        """
        INSERT INTO onto_monitor_rollup
            (system_key, active_n, draft_n, incomplete_n, rubbish_n, zombie_n,
             register_n, report_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(system_key) DO UPDATE SET
            active_n = excluded.active_n,
            draft_n = excluded.draft_n,
            incomplete_n = excluded.incomplete_n,
            rubbish_n = excluded.rubbish_n,
            zombie_n = excluded.zombie_n,
            register_n = excluded.register_n,
            report_json = excluded.report_json,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            MEMBERSHIP_SYSTEM_KEY,
            int(report.get("active_n") or 0),
            int(report.get("draft_n") or 0),
            int(report.get("incomplete_n") or 0),
            int(report.get("rubbish_n") or 0),
            int(report.get("zombie_n") or 0),
            int(report.get("register_n") or 0),
            json.dumps(report, ensure_ascii=False, default=str),
        ),
    )

    if commit:
        conn.commit()

    out.update(
        {
            "ok": True,
            "gate": GATE_POLICY,
            "pipeline": PIPELINE_ID,
            "root_task_id": root_id,
            "version_id": version_id,
            "channel_id": channel_id,
            "module_id": module_id,
            "equation": equation,
            "worker_report": report,
        }
    )
    return out


def resolve_system_location(
    conn: sqlite3.Connection,
    *,
    system_key: str | None = None,
) -> dict[str, Any]:
    """Where channel/module/version/task live in DB (for UI + workers)."""
    sk = (system_key or MEMBERSHIP_SYSTEM_KEY).strip() or MEMBERSHIP_SYSTEM_KEY
    # Prefer root task that declares system.key dim
    row = conn.execute(
        """
        SELECT t.id, t.task_label, t.title, t.channel_id, t.module_id, t.version_id,
               ch.code, ch.name, m.code, m.name, v.version_label, v.title
        FROM dev_task t
        JOIN task_ssot s ON s.task_id = t.id AND s.dim_key = 'system.key' AND s.value_text = ?
        JOIN channel ch ON ch.id = t.channel_id
        JOIN module m ON m.id = t.module_id
        JOIN version_center v ON v.id = t.version_id
        WHERE t.parent_task_id IS NULL
        ORDER BY t.id
        LIMIT 1
        """,
        (sk,),
    ).fetchone()
    if not row:
        # fallback: any task with system.key
        row = conn.execute(
            """
            SELECT t.id, t.task_label, t.title, t.channel_id, t.module_id, t.version_id,
                   ch.code, ch.name, m.code, m.name, v.version_label, v.title
            FROM dev_task t
            JOIN task_ssot s ON s.task_id = t.id AND s.dim_key = 'system.key' AND s.value_text = ?
            JOIN channel ch ON ch.id = t.channel_id
            JOIN module m ON m.id = t.module_id
            JOIN version_center v ON v.id = t.version_id
            ORDER BY t.id
            LIMIT 1
            """,
            (sk,),
        ).fetchone()

    plan_table = None
    equation = None
    if row:
        root_id = int(row[0])
        for dim_key, value_text in conn.execute(
            """
            SELECT dim_key, value_text FROM task_ssot
            WHERE task_id = ? AND dim_key IN ('plan.table', 'goal.equation', 'system.output')
            """,
            (root_id,),
        ).fetchall():
            if dim_key == "plan.table":
                plan_table = value_text
            elif dim_key == "goal.equation":
                equation = value_text

    # field map from active registers / slices
    fields = []
    for r in conn.execute(
        """
        SELECT DISTINCT slice_key, function_name, register_id, tacid, status
        FROM code_register
        WHERE system_key = ? AND status NOT IN ('rubbish', 'deprecated')
        ORDER BY slice_key, id
        """,
        (sk,),
    ).fetchall():
        fields.append(
            {
                "slice_key": r[0],
                "function_name": r[1],
                "register_id": r[2],
                "tacid": r[3],
                "status": r[4],
                "data_table": plan_table or MEMBERSHIP_TABLE,
                "data_field": r[0],
            }
        )

    if not row:
        return {
            "system_key": sk,
            "found": False,
            "map": {
                "channel": {
                    "table": "channel",
                    "code_field": "code",
                    "name_field": "name",
                    "pk": "id",
                    "value": None,
                },
                "module": {
                    "table": "module",
                    "code_field": "code",
                    "name_field": "name",
                    "pk": "id",
                    "value": None,
                },
                "task_fk": {
                    "table": "dev_task",
                    "channel_field": "channel_id",
                    "module_field": "module_id",
                    "version_field": "version_id",
                    "label_field": "task_label",
                },
                "version": {
                    "table": "version_center",
                    "label_field": "version_label",
                    "channel_field": "channel_id",
                    "module_field": "module_id",
                },
                "register": {
                    "table": "code_register",
                    "module_field": "module_name",
                    "function_field": "function_name",
                    "note": "code_register has module_name text, not channel_id",
                },
            },
            "fields": fields,
        }

    (
        task_id,
        task_label,
        title,
        channel_id,
        module_id,
        version_id,
        ch_code,
        ch_name,
        mod_code,
        mod_name,
        ver_label,
        ver_title,
    ) = row

    return {
        "system_key": sk,
        "found": True,
        "root_task_id": int(task_id),
        "root_task_label": str(task_label),
        "root_title": title,
        "channel": {
            "table": "channel",
            "id": int(channel_id),
            "code": str(ch_code),
            "name": str(ch_name),
            "code_field": "channel.code",
            "id_field": "channel.id",
            "fk_on_task": "dev_task.channel_id",
            "fk_on_version": "version_center.channel_id",
        },
        "module": {
            "table": "module",
            "id": int(module_id),
            "code": str(mod_code),
            "name": str(mod_name),
            "code_field": "module.code",
            "id_field": "module.id",
            "fk_on_task": "dev_task.module_id",
            "fk_on_version": "version_center.module_id",
            "register_field": "code_register.module_name",
        },
        "version": {
            "table": "version_center",
            "id": int(version_id),
            "version_label": str(ver_label),
            "title": ver_title,
            "label_field": "version_center.version_label",
            "fk_on_task": "dev_task.version_id",
        },
        "task": {
            "table": "dev_task",
            "id": int(task_id),
            "task_label": str(task_label),
            "label_field": "dev_task.task_label",
            "channel_id_field": "dev_task.channel_id",
            "module_id_field": "dev_task.module_id",
            "version_id_field": "dev_task.version_id",
        },
        "plan": {
            "table_dim": "task_ssot.dim_key='plan.table'",
            "data_table": plan_table,
            "equation_dim": "task_ssot.dim_key='goal.equation'",
            "equation": equation,
        },
        "ssot": {
            "table": "task_ssot",
            "system_key_dim": "system.key",
            "slice_key_dim": "slice.key",
            "impl_module_dim": "impl.module",
            "impl_function_dim": "impl.function",
            "register_dim": "impl.register_id / profile.H.register_id",
        },
        "register": {
            "table": "code_register",
            "module_field": "module_name",
            "function_field": "function_name",
            "slice_field": "slice_key",
            "system_field": "system_key",
            "note": "no channel column — channel via dev_task.channel_id",
        },
        "fields": fields,
        "join_path": (
            "channel.id ← dev_task.channel_id ; "
            "module.id ← dev_task.module_id ; "
            "version_center.id ← dev_task.version_id ; "
            "task_ssot.task_id ← dev_task.id ; "
            "code_register.slice_task_id ← dev_task.id"
        ),
    }


def worker_clean_report(
    conn: sqlite3.Connection,
    *,
    system_key: str | None = None,
    include_rubbish_registers: bool = False,
) -> dict[str, Any]:
    """MCS4 — what workers should see: active / incomplete (no noise by default).

    Naming law (membership sample):
      function_name = member_<slice>   e.g. member_region
      value          = runtime data    e.g. region='TW'  (NOT member_region_2)
      _2/_3 suffixes = allocate collision noise → status=rubbish (not values)
    """
    location = resolve_system_location(conn, system_key=system_key)
    params: list[Any] = []
    where = ""
    if system_key:
        # tasks that declare system.key dim or code_register.system_key
        where = """
        WHERE t.id IN (
            SELECT task_id FROM task_ssot WHERE dim_key = 'system.key' AND value_text = ?
            UNION
            SELECT slice_task_id FROM code_register WHERE system_key = ? AND slice_task_id IS NOT NULL
            UNION
            SELECT system_task_id FROM code_register WHERE system_key = ? AND system_task_id IS NOT NULL
        )
        """
        params = [system_key, system_key, system_key]

    sql = f"""
        SELECT t.id, t.task_label, t.title, t.status, t.parent_task_id, t.version_id
        FROM dev_task t
        {where}
        ORDER BY t.id
    """
    tasks = []
    for r in conn.execute(sql, params).fetchall():
        tid = int(r[0])
        comp = slice_completeness(conn, tid)
        st = comp.get("slice_status") or "draft"
        # root may not have slice.status
        dims = comp.get("dims") or {}
        is_root = dims.get("system.key") and not dims.get("slice.key")
        if is_root:
            worker_bucket = "system_root"
        elif st == SLICE_STATUS_RUBBISH or st == SLICE_STATUS_DEPRECATED:
            worker_bucket = "rubbish"
        elif not comp.get("complete"):
            worker_bucket = "incomplete"
        elif not comp.get("coding_ready"):
            worker_bucket = "needs_register"
        else:
            worker_bucket = "active"
        tasks.append(
            {
                "task_id": tid,
                "task_label": r[1],
                "title": r[2],
                "dev_status": r[3],
                "parent_task_id": r[4],
                "slice_status": st,
                "worker_bucket": worker_bucket,
                "worker_action": comp.get("worker_action"),
                "missing_dims": comp.get("missing_dims") or [],
                "register_id": comp.get("register_id"),
                "coding_ready": comp.get("coding_ready"),
                "complete": comp.get("complete"),
            }
        )

    cols_sql = (
        "id, register_id, module_name, function_name, task_id, tacid, "
        "system_task_id, slice_task_id, system_key, slice_key, status, "
        "notes, source, updated_at, created_at"
    )
    reg_sql = f"SELECT {cols_sql} FROM code_register"
    reg_params: list[Any] = []
    if system_key:
        reg_sql += " WHERE system_key = ?"
        reg_params.append(system_key)
    reg_sql += " ORDER BY slice_key, id"
    keys = [
        "id",
        "register_id",
        "module_name",
        "function_name",
        "task_id",
        "tacid",
        "system_task_id",
        "slice_task_id",
        "system_key",
        "slice_key",
        "status",
        "notes",
        "source",
        "updated_at",
        "created_at",
    ]
    all_registers: list[dict[str, Any]] = []
    for r in conn.execute(reg_sql, reg_params).fetchall():
        all_registers.append({k: r[i] for i, k in enumerate(keys)})

    active_registers = [
        r for r in all_registers if str(r.get("status") or "") not in ("rubbish", "deprecated")
    ]
    rubbish_registers = [
        r for r in all_registers if str(r.get("status") or "") in ("rubbish", "deprecated")
    ]
    # Worker default: only active/zombie/draft — never dump _2/_3 noise in main list
    registers = (
        all_registers if include_rubbish_registers else active_registers
    )

    def _count(bucket: str) -> int:
        return sum(1 for t in tasks if t["worker_bucket"] == bucket)

    zombie_n = sum(1 for r in all_registers if str(r.get("status")) == "zombie")
    rubbish_reg = len(rubbish_registers)

    work_queue = [
        t
        for t in tasks
        if t["worker_bucket"] in ("incomplete", "needs_register", "active", "system_root")
    ]
    skip = [t for t in tasks if t["worker_bucket"] == "rubbish"]

    return {
        "ok": True,
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "system_key": system_key,
        "report_time": _utc_now_iso(),
        "location": location,
        "active_n": _count("active"),
        "draft_n": _count("needs_register"),
        "incomplete_n": _count("incomplete"),
        "rubbish_n": _count("rubbish") + rubbish_reg,
        "zombie_n": zombie_n,
        "register_n": len(active_registers),
        "register_n_all": len(all_registers),
        "rubbish_register_n": rubbish_reg,
        "system_root_n": _count("system_root"),
        "work_queue": work_queue,
        "skip_rubbish": skip,
        "registers": registers,
        "registers_active": active_registers,
        "registers_rubbish": rubbish_registers if include_rubbish_registers else [],
        "registers_rubbish_n": rubbish_reg,
        "naming_law": {
            "function": "member_<slice_key>  e.g. member_region",
            "value": "runtime field data  e.g. region='TW' — NOT a function suffix",
            "suffix__N": "DB unique collision from re-seed noise → rubbish (ignore)",
        },
        "layer": layer_architecture(),
        "handoff": _orchestrator_handoff_rows(location, active_registers),
        "tasks": tasks,
        "worker_rules": [
            "Do work_queue only",
            "Skip skip_rubbish and registers status=rubbish",
            "One slice → one active function (member_region), value is data not name",
            "Coding requires register_id",
            "Do not invent dims outside profile A-H",
            "Channel lives in channel.code via dev_task.channel_id (not code_register)",
            "MCS = ontology/registry only — orchestrator owns driver/evidence/QC pair",
        ],
    }


def _orchestrator_handoff_rows(
    location: dict[str, Any],
    active_registers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build per-slice handoff stubs for orchestrator (MCS7)."""
    plan = (location or {}).get("plan") or {}
    ch = (location or {}).get("channel") or {}
    mod = (location or {}).get("module") or {}
    data_table = plan.get("data_table") or MEMBERSHIP_TABLE
    rows = []
    for r in active_registers:
        sk = r.get("slice_key") or ""
        rows.append(
            {
                "register_id": r.get("register_id"),
                "slice_key": sk,
                "plan_table": data_table,
                "data_field": sk,
                "function_name": r.get("function_name"),
                "module_name": r.get("module_name") or mod.get("code"),
                "slice_task_id": r.get("slice_task_id") or r.get("task_id"),
                "tacid": r.get("tacid"),
                "channel_code": ch.get("code"),
                "channel_note": "env ontology only — not exec.driver",
                "exec_driver": None,
                "exec_template_id": None,
                "trace_run_id": None,
                "orchestrator_status": "not_bound",
                "handoff_ready": bool(r.get("register_id") and sk),
            }
        )
    return {
        "ready_n": sum(1 for x in rows if x.get("handoff_ready")),
        "bound_n": sum(1 for x in rows if x.get("exec_driver")),
        "rows": rows,
        "keys": (layer_architecture().get("handoff_keys") or []),
        "optional_dims": (layer_architecture().get("orchestrator_optional_dims") or []),
        "flow": layer_architecture().get("flow"),
    }


def _new_request_key() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    name = (table_name or "").strip()
    if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def _table_fields(conn: sqlite3.Connection, table_name: str) -> list[dict[str, Any]]:
    if not _table_exists(conn, table_name):
        return []
    out = []
    for r in conn.execute(f'PRAGMA table_info("{table_name}")').fetchall():
        # cid, name, type, notnull, dflt, pk
        out.append(
            {
                "name": str(r[1]),
                "type": str(r[2] or ""),
                "notnull": int(r[3] or 0),
                "pk": int(r[5] or 0),
            }
        )
    return out


def _parse_fields_csv(text: str | None) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,;\s]+", str(text).strip())
    return [p for p in (_sanitize_token(x, default="") for x in parts) if p]


def _tdd_template_for(field: str) -> dict[str, Any]:
    key = (field or "").strip().lower()
    if key in FIELD_TDD_TEMPLATES:
        return dict(FIELD_TDD_TEMPLATES[key])
    return {
        "tdd_type_code": "text",
        "value_type": "string",
        "rules": [{"op": "present", "note": f"field {key}"}],
    }


def create_fn_request(
    conn: sqlite3.Connection,
    *,
    human_text: str,
    module_code: str,
    channel_code: str = "local_pc",
    system_key: str | None = None,
    desired_table: str | None = None,
    desired_output: str | None = None,
    request_kind: str = "system",
    fields: list[str] | None = None,
    source: str = "function_builder",
    commit: bool = True,
) -> dict[str, Any]:
    """Step 1 — human asks for a function/system (DB row)."""
    text = (human_text or "").strip()
    if not text:
        raise ValueError("human_text required")
    mod = _sanitize_token(module_code or "app", default="app").lower()
    ch = _sanitize_token(channel_code or "local_pc", default="local_pc").lower()
    sk = _sanitize_token(system_key or mod, default=mod).lower()
    table = _sanitize_token(desired_table or "", default="") or None
    output = _sanitize_token(desired_output or f"{sk}_profile", default=f"{sk}_profile")
    kind = request_kind if request_kind in ("system", "function", "field", "table") else "system"
    req_key = _new_request_key()
    plan_stub = {
        "fields_hint": list(fields or []),
        "flow": layer_architecture().get("primary_flow"),
    }
    cur = conn.execute(
        """
        INSERT INTO fn_request
            (request_key, human_text, request_kind, channel_code, module_code,
             system_key, desired_table, desired_output, status, plan_json, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'received', ?, ?)
        """,
        (
            req_key,
            text,
            kind,
            ch,
            mod,
            sk,
            table,
            output,
            json.dumps(plan_stub, ensure_ascii=False),
            source,
        ),
    )
    rid = int(cur.lastrowid)
    if commit:
        conn.commit()
    got = get_fn_request(conn, request_id=rid)
    if got and got.get("id") is not None:
        return got
    return {
        "id": rid,
        "request_key": req_key,
        "human_text": text,
        "request_kind": kind,
        "channel_code": ch,
        "module_code": mod,
        "system_key": sk,
        "desired_table": table,
        "desired_output": output,
        "status": "received",
        "plan_json": plan_stub,
    }


def get_fn_request(
    conn: sqlite3.Connection,
    *,
    request_id: int | None = None,
    request_key: str | None = None,
) -> dict[str, Any] | None:
    cols = [
        "id",
        "request_key",
        "human_text",
        "request_kind",
        "channel_code",
        "module_code",
        "system_key",
        "desired_table",
        "desired_output",
        "status",
        "research_json",
        "plan_json",
        "build_json",
        "root_task_id",
        "version_id",
        "error_text",
        "source",
        "updated_at",
        "created_at",
    ]
    col_sql = ", ".join(cols)
    if request_id is not None:
        row = conn.execute(
            f"SELECT {col_sql} FROM fn_request WHERE id = ?", (int(request_id),)
        ).fetchone()
    elif request_key:
        row = conn.execute(
            f"SELECT {col_sql} FROM fn_request WHERE request_key = ?",
            (str(request_key).strip(),),
        ).fetchone()
    else:
        return None
    if not row:
        return None
    if hasattr(row, "keys"):
        data = {k: row[k] for k in row.keys()}
    else:
        data = {cols[i]: row[i] for i in range(len(cols))}
    for jk in ("research_json", "plan_json", "build_json"):
        raw = data.get(jk)
        if isinstance(raw, str) and raw.strip():
            try:
                data[jk] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return data


def list_fn_requests(
    conn: sqlite3.Connection, *, limit: int = 30
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, request_key, human_text, request_kind, channel_code, module_code,
               system_key, desired_table, desired_output, status, root_task_id,
               version_id, created_at, updated_at
        FROM fn_request
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    ).fetchall()
    keys = [
        "id",
        "request_key",
        "human_text",
        "request_kind",
        "channel_code",
        "module_code",
        "system_key",
        "desired_table",
        "desired_output",
        "status",
        "root_task_id",
        "version_id",
        "created_at",
        "updated_at",
    ]
    return [{keys[i]: r[i] for i in range(len(keys))} for r in rows]


def research_fn_request(
    conn: sqlite3.Connection,
    *,
    request_id: int | None = None,
    request_key: str | None = None,
    fields: list[str] | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Step A — AI research: what exists / reuse / need table design.

    Path:
      with table + fields → reuse_table_fields → tasks 1.1+region…
      without table → design_table_fields → tasks 1.1+member_id…
    Depends on channel + module.
    """
    req = get_fn_request(conn, request_id=request_id, request_key=request_key)
    if not req:
        raise ValueError("fn_request not found")
    rid = int(req["id"])
    conn.execute(
        """
        UPDATE fn_request
        SET status = 'researching', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (rid,),
    )

    sk = str(req.get("system_key") or req.get("module_code") or "app")
    mod = str(req.get("module_code") or sk)
    ch = str(req.get("channel_code") or "local_pc")
    table = (req.get("desired_table") or "").strip() or None
    output = str(req.get("desired_output") or f"{sk}_profile")
    hint_fields = list(fields or [])
    if not hint_fields:
        plan = req.get("plan_json") or {}
        if isinstance(plan, dict):
            hint_fields = list(plan.get("fields_hint") or [])

    existing_fields: list[dict[str, Any]] = []
    has_table = False
    if table and _table_exists(conn, table):
        has_table = True
        existing_fields = _table_fields(conn, table)
    elif not table:
        # try module-named table
        guess = _sanitize_token(mod, default="").lower()
        if guess and _table_exists(conn, guess):
            table = guess
            has_table = True
            existing_fields = _table_fields(conn, table)

    # existing registers for this system/module
    reuse_regs = []
    for r in conn.execute(
        """
        SELECT register_id, function_name, slice_key, status, tacid
        FROM code_register
        WHERE (system_key = ? OR module_name = ?)
          AND status NOT IN ('rubbish', 'deprecated')
        ORDER BY id
        """,
        (sk, mod),
    ).fetchall():
        reuse_regs.append(
            {
                "register_id": r[0],
                "function_name": r[1],
                "slice_key": r[2],
                "status": r[3],
                "tacid": r[4],
            }
        )

    # decide path + proposed slices
    if has_table and existing_fields:
        path_code = "reuse_table_fields"
        field_names = [f["name"] for f in existing_fields if f["name"].lower() != "id"]
        if hint_fields:
            # prefer human hint order, keep only known or all hints
            known = {n.lower() for n in field_names}
            ordered = [f for f in hint_fields if f.lower() in known] or hint_fields
            field_names = ordered
        notes = f"Table `{table}` exists — slice each field; reuse where possible."
    elif hint_fields:
        path_code = "design_table_fields" if not has_table else "reuse_table_fields"
        field_names = list(hint_fields)
        table = table or _sanitize_token(mod, default="entity").lower()
        notes = (
            f"No usable table fields — design table `{table}` with given fields."
            if not has_table
            else f"Use hint fields on table `{table}`."
        )
        has_table = bool(has_table)
    else:
        path_code = "design_table_fields"
        field_names = list(DEFAULT_DESIGN_FIELDS)
        table = table or _sanitize_token(mod, default="entity").lower()
        notes = (
            f"No table yet — propose design-table tasks for `{table}` "
            f"(default membership-like fields). channel={ch} module={mod}."
        )
        has_table = False

    if reuse_regs and path_code == "design_table_fields":
        path_code = "extend_system"

    proposed = []
    for i, fname in enumerate(field_names, start=1):
        label = f"1.{i}"
        tmpl = _tdd_template_for(fname)
        proposed.append(
            {
                "label": label,
                "key": fname,
                "sort": i,
                "tdd_type_code": tmpl.get("tdd_type_code") or "text",
                "tdd": tmpl,
                "reuse_register_id": next(
                    (
                        x["register_id"]
                        for x in reuse_regs
                        if str(x.get("slice_key") or "") == fname
                    ),
                    None,
                ),
            }
        )

    equation = " + ".join(p["key"] for p in proposed) + f" = {output}"
    multi_dim = {
        "channel.code": ch,
        "module.code": mod,
        "system.key": sk,
        "plan.table": table,
        "goal.equation": equation,
        "goal.text": req.get("human_text"),
        "path": path_code,
        "has_table": has_table,
        "slices": [
            {
                "task_label": p["label"],
                "slice.key": p["key"],
                "profile.A.field": f"{table}.{p['key']}",
                "profile.E.tdd": p["tdd_type_code"],
                "tdd.rules": p["tdd"].get("rules"),
                "tdd.depends_on": p["tdd"].get("depends_on") or p["tdd"].get("compose_with"),
            }
            for p in proposed
        ],
        "law": "value+value=output · register_id required · TDD per field",
    }

    research_payload = {
        "path_code": path_code,
        "has_table": has_table,
        "table_name": table,
        "existing_fields": existing_fields,
        "reuse_registers": reuse_regs,
        "proposed_slices": proposed,
        "equation": equation,
        "notes": notes,
        "multi_dim_ssot": multi_dim,
        "channel_code": ch,
        "module_code": mod,
        "system_key": sk,
    }

    cur = conn.execute(
        """
        INSERT INTO fn_research
            (request_id, path_code, has_table, table_name,
             existing_fields_json, reuse_registers_json, proposed_slices_json,
             equation, notes, multi_dim_ssot_json, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'function_builder')
        """,
        (
            rid,
            path_code,
            1 if has_table else 0,
            table,
            json.dumps(existing_fields, ensure_ascii=False),
            json.dumps(reuse_regs, ensure_ascii=False),
            json.dumps(proposed, ensure_ascii=False),
            equation,
            notes,
            json.dumps(multi_dim, ensure_ascii=False),
        ),
    )
    research_id = int(cur.lastrowid)

    plan_json = {
        "request_id": rid,
        "research_id": research_id,
        "path_code": path_code,
        "table": table,
        "output": output,
        "equation": equation,
        "slices": proposed,
        "multi_dim_ssot": multi_dim,
        "next": "build_fn_request",
    }
    conn.execute(
        """
        UPDATE fn_request
        SET status = 'researched',
            research_json = ?,
            plan_json = ?,
            desired_table = COALESCE(desired_table, ?),
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            json.dumps(research_payload, ensure_ascii=False),
            json.dumps(plan_json, ensure_ascii=False),
            table,
            rid,
        ),
    )
    if commit:
        conn.commit()
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "request_id": rid,
        "request_key": req.get("request_key"),
        "research_id": research_id,
        "research": research_payload,
        "plan": plan_json,
        "status": "researched",
    }


def _upsert_field_tdd_rule(
    conn: sqlite3.Connection,
    *,
    request_id: int | None,
    system_key: str,
    slice_key: str,
    field_name: str,
    tdd_type_code: str,
    rule: dict[str, Any],
    register_id: str | None,
    task_id: int | None,
    notes: str | None = None,
) -> int:
    depends = rule.get("depends_on") or rule.get("compose_with") or []
    existing = conn.execute(
        """
        SELECT id FROM field_tdd_rule
        WHERE system_key = ? AND slice_key = ? AND status IN ('draft', 'active')
        ORDER BY id DESC LIMIT 1
        """,
        (system_key, slice_key),
    ).fetchone()
    rule_json = json.dumps(rule, ensure_ascii=False)
    depends_json = json.dumps(list(depends), ensure_ascii=False)
    if existing:
        eid = int(existing[0])
        conn.execute(
            """
            UPDATE field_tdd_rule
            SET request_id = ?, field_name = ?, tdd_type_code = ?, rule_json = ?,
                depends_on_json = ?, register_id = ?, task_id = ?, status = 'active',
                notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                request_id,
                field_name,
                tdd_type_code,
                rule_json,
                depends_json,
                register_id,
                task_id,
                notes,
                eid,
            ),
        )
        return eid
    cur = conn.execute(
        """
        INSERT INTO field_tdd_rule
            (request_id, system_key, slice_key, field_name, tdd_type_code,
             rule_json, depends_on_json, register_id, task_id, status, notes, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 'function_builder')
        """,
        (
            request_id,
            system_key,
            slice_key,
            field_name,
            tdd_type_code,
            rule_json,
            depends_json,
            register_id,
            task_id,
            notes,
        ),
    )
    return int(cur.lastrowid)


def build_fn_request(
    conn: sqlite3.Connection,
    *,
    request_id: int | None = None,
    request_key: str | None = None,
    register_functions: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    """Step 3 — materialize system: root+slices+register_id+field_tdd_rule+SSOT."""
    from db_schema import upsert_task_ssot

    req = get_fn_request(conn, request_id=request_id, request_key=request_key)
    if not req:
        raise ValueError("fn_request not found")
    rid = int(req["id"])
    plan = req.get("plan_json") if isinstance(req.get("plan_json"), dict) else None
    if not plan:
        research_fn_request(conn, request_id=rid, commit=False)
        req = get_fn_request(conn, request_id=rid) or req
        plan = req.get("plan_json") if isinstance(req.get("plan_json"), dict) else {}

    conn.execute(
        """
        UPDATE fn_request SET status = 'building', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (rid,),
    )

    sk = str(req.get("system_key") or req.get("module_code") or "app")
    mod = str(req.get("module_code") or sk)
    ch = str(req.get("channel_code") or "local_pc")
    table = str(plan.get("table") or req.get("desired_table") or mod)
    output = str(plan.get("output") or req.get("desired_output") or f"{sk}_profile")
    slices_spec = list(plan.get("slices") or [])
    if not slices_spec:
        raise ValueError("plan has no slices — run research first")
    equation = str(plan.get("equation") or "")
    version_label = f"{_sanitize_token(sk, default='sys')[:16]}-1.0"

    channel_id = _get_or_create_dim(conn, "channel", ch, ch.replace("_", " ").title())
    module_id = _get_or_create_dim(conn, "module", mod, mod.replace("_", " ").title())
    act_sys = _ensure_action(
        conn,
        element="system",
        action="managed",
        code=ACTION_SYSTEM,
        name="Managed system root",
    )
    act_slice = _ensure_action(
        conn,
        element="slice",
        action="managed",
        code=ACTION_SLICE,
        name="Managed field slice",
    )

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, module_id, version_label),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (
                channel_id,
                module_id,
                version_label,
                f"{sk} function builder",
                f"request_id={rid}; gate=never",
            ),
        )
        version_id = int(cur.lastrowid)

    root_payload = {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "system_key": sk,
        "goal_type": "build_system",
        "goal_text": req.get("human_text"),
        "goal_equation": equation,
        "output": output,
        "table_plan": table,
        "builder_request_id": rid,
        "builder_request_key": req.get("request_key"),
        "path_code": plan.get("path_code"),
        "worker_rule": "active+incomplete only; coding needs register_id; TDD per field",
    }
    root = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, "1"),
    ).fetchone()
    if root:
        root_id = int(root[0])
        conn.execute(
            """
            UPDATE dev_task
            SET title = ?, payload_json = ?, module_id = ?, channel_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                f"{sk} system (builder)",
                json.dumps(root_payload, ensure_ascii=False),
                module_id,
                channel_id,
                root_id,
            ),
        )
    else:
        cur = conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status)
            VALUES (NULL, ?, ?, ?, ?, '1', ?, ?, 'pending')
            """,
            (
                channel_id,
                module_id,
                act_sys,
                version_id,
                f"{sk} system (builder)",
                json.dumps(root_payload, ensure_ascii=False),
            ),
        )
        root_id = int(cur.lastrowid)

    root_dims = [
        (10, "system.key", sk),
        (20, "system.output", output),
        (30, "goal.text", str(req.get("human_text") or "")),
        (40, "goal.equation", equation),
        (50, "plan.table", table),
        (60, "plan.pattern", "value+value=output"),
        (70, "worker.scope", "active_incomplete_only"),
        (80, "coding.law", "register_id_required"),
        (90, "builder.request_id", str(rid)),
        (100, "builder.request_key", str(req.get("request_key") or "")),
        (110, "builder.path", str(plan.get("path_code") or "")),
        (120, "channel.code", ch),
        (130, "module.code", mod),
    ]
    for sort_order, dim_key, value_text in root_dims:
        upsert_task_ssot(
            conn,
            task_id=root_id,
            dim_key=dim_key,
            value_text=value_text,
            value_type="string",
            source="function_builder",
            sort_order=sort_order,
            notes="builder system root",
            commit=False,
        )

    # ontology
    sys_code = f"sys.{sk}"
    upsert_onto_concept(
        conn, code=sys_code, kind="system", title=f"{sk} system", commit=False
    )
    ch_code = f"ch.{ch}"
    upsert_onto_concept(
        conn, code=ch_code, kind="channel", title=ch, commit=False
    )
    mod_code = f"mod.{mod}"
    upsert_onto_concept(
        conn, code=mod_code, kind="module", title=mod, commit=False
    )
    link_onto(conn, from_code=sys_code, to_code=ch_code, rel="on_channel", commit=False)
    link_onto(conn, from_code=sys_code, to_code=mod_code, rel="uses_module", commit=False)
    bind_onto(
        conn,
        concept_code=sys_code,
        bind_type="task",
        bind_key="1",
        bind_id=root_id,
        notes=f"builder req {rid}",
        commit=False,
    )

    built_slices: list[dict[str, Any]] = []
    built_regs: list[dict[str, Any]] = []
    built_tdd: list[dict[str, Any]] = []
    fn_prefix = _sanitize_token(mod, default="fn").lower()

    for spec in slices_spec:
        label = str(spec.get("label") or "")
        skey = str(spec.get("key") or "")
        if not label or not skey:
            continue
        tmpl = spec.get("tdd") if isinstance(spec.get("tdd"), dict) else _tdd_template_for(skey)
        tdd_code = str(spec.get("tdd_type_code") or tmpl.get("tdd_type_code") or "text")
        slice_payload = {
            "pipeline": PIPELINE_ID,
            "system_key": sk,
            "slice_key": skey,
            "parent_label": "1",
            "table": table,
            "builder_request_id": rid,
            "tdd": tmpl,
            "profile": [p["code"] for p in PROFILE_STEPS],
        }
        existing = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, label),
        ).fetchone()
        if existing:
            slice_id = int(existing[0])
            conn.execute(
                """
                UPDATE dev_task
                SET title = ?, payload_json = ?, parent_task_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    f"{sk} slice: {skey}",
                    json.dumps(slice_payload, ensure_ascii=False),
                    root_id,
                    slice_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO dev_task
                    (parent_task_id, channel_id, module_id, action_name_id, version_id,
                     task_label, title, payload_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    root_id,
                    channel_id,
                    module_id,
                    act_slice,
                    version_id,
                    label,
                    f"{sk} slice: {skey}",
                    json.dumps(slice_payload, ensure_ascii=False),
                ),
            )
            slice_id = int(cur.lastrowid)

        # optional dev_task_field row for DB-driven field list
        try:
            tdd_row = conn.execute(
                "SELECT id FROM tdd_type WHERE code = ?", (tdd_code,)
            ).fetchone()
            if not tdd_row:
                tdd_row = conn.execute(
                    "SELECT id FROM tdd_type WHERE code = 'text'"
                ).fetchone()
            if tdd_row:
                tdd_type_id = int(tdd_row[0])
                exists_f = conn.execute(
                    """
                    SELECT id FROM dev_task_field
                    WHERE task_id = ? AND field_name = ?
                    """,
                    (slice_id, skey),
                ).fetchone()
                if not exists_f:
                    conn.execute(
                        """
                        INSERT INTO dev_task_field
                            (task_id, tdd_type_id, field_name, nullable, is_pk, sort_order)
                        VALUES (?, ?, ?, 1, 0, ?)
                        """,
                        (slice_id, tdd_type_id, skey, int(spec.get("sort") or 0)),
                    )
        except sqlite3.Error:
            pass

        apply_slice_profile_dims(
            conn,
            task_id=slice_id,
            system_key=sk,
            slice_key=skey,
            table_name=table,
            module_code=mod,
            tdd_code=tdd_code,
            tdd_rule=tmpl,
            request_id=rid,
            slice_status=SLICE_STATUS_ACTIVE,
            source="function_builder",
            commit=False,
        )

        reg_info = None
        if register_functions:
            reuse_rid = spec.get("reuse_register_id")
            existing_reg = None
            if reuse_rid:
                existing_reg = conn.execute(
                    """
                    SELECT register_id, function_name, module_name
                    FROM code_register WHERE register_id = ?
                    """,
                    (str(reuse_rid),),
                ).fetchone()
            if not existing_reg:
                existing_reg = conn.execute(
                    """
                    SELECT register_id, function_name, module_name
                    FROM code_register
                    WHERE slice_task_id = ? AND status IN ('active', 'draft', 'zombie')
                    ORDER BY id LIMIT 1
                    """,
                    (slice_id,),
                ).fetchone()
            if not existing_reg:
                existing_reg = conn.execute(
                    """
                    SELECT register_id, function_name, module_name
                    FROM code_register
                    WHERE system_key = ? AND slice_key = ?
                      AND status IN ('active', 'draft', 'zombie')
                    ORDER BY id LIMIT 1
                    """,
                    (sk, skey),
                ).fetchone()
            if existing_reg:
                reg_info = register_managed_function(
                    conn,
                    task_id=slice_id,
                    tacid=label,
                    module_name=str(existing_reg[2] or mod),
                    function_name=str(existing_reg[1]),
                    system_task_id=root_id,
                    slice_task_id=slice_id,
                    system_key=sk,
                    slice_key=skey,
                    required=True,
                    status="active",
                    source="function_builder",
                    notes=f"builder {label} {skey} reuse",
                    commit=False,
                )
            else:
                base_fn = f"{fn_prefix}_{_sanitize_token(skey)}"
                reg_info = register_managed_function(
                    conn,
                    task_id=slice_id,
                    tacid=label,
                    module_name=mod,
                    base_name=base_fn,
                    system_task_id=root_id,
                    slice_task_id=slice_id,
                    system_key=sk,
                    slice_key=skey,
                    required=True,
                    status="active",
                    source="function_builder",
                    notes=f"builder {label} {skey}",
                    commit=False,
                )
            apply_slice_profile_dims(
                conn,
                task_id=slice_id,
                system_key=sk,
                slice_key=skey,
                table_name=table,
                module_code=mod,
                tdd_code=tdd_code,
                function_name=reg_info.get("function_name"),
                register_id=reg_info.get("register_id"),
                tdd_rule=tmpl,
                request_id=rid,
                slice_status=SLICE_STATUS_ACTIVE,
                source="function_builder",
                commit=False,
            )
            built_regs.append(reg_info)

        tdd_id = _upsert_field_tdd_rule(
            conn,
            request_id=rid,
            system_key=sk,
            slice_key=skey,
            field_name=skey,
            tdd_type_code=tdd_code,
            rule=tmpl,
            register_id=(reg_info or {}).get("register_id"),
            task_id=slice_id,
            notes=str(tmpl.get("compose_note") or tmpl.get("note") or ""),
        )
        built_tdd.append(
            {
                "id": tdd_id,
                "slice_key": skey,
                "tdd_type_code": tdd_code,
                "register_id": (reg_info or {}).get("register_id"),
                "task_id": slice_id,
                "rules": tmpl.get("rules"),
                "depends_on": tmpl.get("depends_on") or tmpl.get("compose_with"),
            }
        )
        built_slices.append(
            {
                "task_id": slice_id,
                "task_label": label,
                "slice_key": skey,
                "register_id": (reg_info or {}).get("register_id"),
                "function_name": (reg_info or {}).get("function_name"),
                "tdd_rule_id": tdd_id,
            }
        )

    build_out = {
        "ok": True,
        "request_id": rid,
        "request_key": req.get("request_key"),
        "system_key": sk,
        "channel_code": ch,
        "module_code": mod,
        "table": table,
        "equation": equation,
        "version_id": version_id,
        "version_label": version_label,
        "root_task_id": root_id,
        "slices": built_slices,
        "registers": built_regs,
        "tdd_rules": built_tdd,
        "path_code": plan.get("path_code"),
        "trace": {
            "request_table": "fn_request",
            "research_table": "fn_research",
            "tdd_table": "field_tdd_rule",
            "register_table": "code_register",
            "ssot_table": "task_ssot",
            "task_table": "dev_task",
        },
    }
    conn.execute(
        """
        UPDATE fn_request
        SET status = 'built',
            build_json = ?,
            root_task_id = ?,
            version_id = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (json.dumps(build_out, ensure_ascii=False, default=str), root_id, version_id, rid),
    )
    if commit:
        conn.commit()
    return build_out


def run_function_builder(
    conn: sqlite3.Connection,
    *,
    human_text: str,
    module_code: str,
    channel_code: str = "local_pc",
    system_key: str | None = None,
    desired_table: str | None = None,
    desired_output: str | None = None,
    fields: list[str] | str | None = None,
    request_kind: str = "system",
    commit: bool = True,
) -> dict[str, Any]:
    """Full flow: request → research → build (traceable register_id + TDD)."""
    field_list: list[str]
    if isinstance(fields, str):
        field_list = _parse_fields_csv(fields)
    else:
        field_list = list(fields or [])
    req = create_fn_request(
        conn,
        human_text=human_text,
        module_code=module_code,
        channel_code=channel_code,
        system_key=system_key,
        desired_table=desired_table,
        desired_output=desired_output,
        request_kind=request_kind,
        fields=field_list,
        commit=False,
    )
    research = research_fn_request(
        conn,
        request_id=int(req["id"]),
        fields=field_list or None,
        commit=False,
    )
    built = build_fn_request(
        conn, request_id=int(req["id"]), register_functions=True, commit=commit
    )
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "flow": layer_architecture().get("primary_flow"),
        "request": get_fn_request(conn, request_id=int(req["id"])),
        "research": research.get("research"),
        "plan": research.get("plan"),
        "build": built,
        "worker_report": worker_clean_report(
            conn, system_key=str(built.get("system_key") or system_key or module_code)
        ),
    }


def list_field_tdd_rules(
    conn: sqlite3.Connection, *, system_key: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    sql = """
        SELECT id, request_id, system_key, slice_key, field_name, tdd_type_code,
               rule_json, depends_on_json, register_id, task_id, status, notes, created_at
        FROM field_tdd_rule
    """
    params: list[Any] = []
    if system_key:
        sql += " WHERE system_key = ?"
        params.append(system_key)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    keys = [
        "id",
        "request_id",
        "system_key",
        "slice_key",
        "field_name",
        "tdd_type_code",
        "rule_json",
        "depends_on_json",
        "register_id",
        "task_id",
        "status",
        "notes",
        "created_at",
    ]
    out = []
    for r in rows:
        item = {keys[i]: r[i] for i in range(len(keys))}
        for jk in ("rule_json", "depends_on_json"):
            raw = item.get(jk)
            if isinstance(raw, str) and raw.strip():
                try:
                    item[jk] = json.loads(raw)
                except json.JSONDecodeError:
                    pass
        out.append(item)
    return out


def builder_dashboard(conn: sqlite3.Connection, *, system_key: str | None = None) -> dict[str, Any]:
    """UI payload: flow + recent requests + tdd + optional system report."""
    return {
        "ok": True,
        "gate": GATE_POLICY,
        "pipeline": PIPELINE_ID,
        "layer": layer_architecture(),
        "flow": layer_architecture().get("primary_flow"),
        "requests": list_fn_requests(conn, limit=20),
        "tdd_rules": list_field_tdd_rules(conn, system_key=system_key, limit=50),
        "tdd_templates": FIELD_TDD_TEMPLATES,
        "systems": list_managed_systems(conn),
        "report": worker_clean_report(conn, system_key=system_key)
        if system_key
        else None,
    }


def list_managed_systems(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT system_key, active_n, draft_n, incomplete_n, rubbish_n, zombie_n,
               register_n, updated_at
        FROM onto_monitor_rollup
        ORDER BY system_key
        """
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "system_key": r[0],
                "active_n": r[1],
                "draft_n": r[2],
                "incomplete_n": r[3],
                "rubbish_n": r[4],
                "zombie_n": r[5],
                "register_n": r[6],
                "updated_at": r[7],
            }
        )
    # fallback from code_register if rollup empty
    if not out:
        keys = conn.execute(
            """
            SELECT DISTINCT system_key FROM code_register
            WHERE system_key IS NOT NULL AND system_key != ''
            """
        ).fetchall()
        for (k,) in keys:
            out.append(worker_clean_report(conn, system_key=str(k)))
    return out


def run_selftest(db_path: str | None = None, *, migrate: bool = True) -> dict[str, Any]:
    """MCS6 smoke: schema + membership seed + register law + managed_invoke refuse."""
    from db_schema import ensure_schema, get_db_path

    path = get_db_path(db_path)
    if migrate:
        ensure_schema(path)

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        schema = verify_managed_schema(conn)
        assert schema.get("ok"), schema

        seed = seed_membership_system(conn, commit=True, register_functions=True)
        assert seed.get("root_task_id")
        assert len(seed.get("slices") or []) >= 5
        assert len(seed.get("registers") or []) >= 5

        cleanup = mark_demo_noise_rubbish(conn, commit=True)
        report = worker_clean_report(conn, system_key=MEMBERSHIP_SYSTEM_KEY)
        assert report.get("register_n", 0) >= 5
        assert report.get("incomplete_n", 0) == 0

        ready = [
            t
            for t in report.get("tasks") or []
            if t.get("worker_bucket") == "active"
        ]
        assert len(ready) >= 5, ready

        refused = managed_invoke(
            lambda: 1,
            module_name="nope",
            function_name="not_registered_fn_xyz",
            require_register=True,
            conn=conn,
            commit=False,
            reraise=False,
        )
        assert refused.get("ok") is False
        assert refused.get("error") == "unregistered_function"

        reg0 = (seed.get("registers") or [None])[0]
        assert reg0 and reg0.get("register_id"), reg0
        rid0 = str(reg0["register_id"])
        row_chk = get_code_register(conn, register_id=rid0)
        assert row_chk is not None, f"missing code_register {rid0}"

        called = managed_invoke(
            lambda: "ok_member",
            register_id=rid0,
            require_register=True,
            conn=conn,
            commit=True,
            reraise=True,
        )
        if not called.get("ok"):
            raise AssertionError(f"managed_invoke failed: {called}")
        got_rid = str(called.get("register_id") or "")
        if got_rid != rid0:
            raise AssertionError(
                f"register_id mismatch got={got_rid!r} expected={rid0!r} called={called}"
            )

        doc = contracts_doc()
        assert doc["gate"] == "never"
        assert len(doc["profile_steps"]) == 8
        assert "fn_request" in (doc.get("tables") or [])

        # MCS8 builder smoke (separate system key — no clash with membership seed)
        built = run_function_builder(
            conn,
            human_text="I need a demo member card system",
            module_code="member_card",
            channel_code="local_pc",
            system_key="member_card",
            desired_table="member_card",
            fields=["member_id", "name", "region", "phone"],
            commit=True,
        )
        assert built.get("ok"), built
        assert (built.get("build") or {}).get("root_task_id")
        assert len((built.get("build") or {}).get("registers") or []) >= 4
        assert len((built.get("build") or {}).get("tdd_rules") or []) >= 4
        phone_tdd = next(
            (
                t
                for t in (built.get("build") or {}).get("tdd_rules") or []
                if t.get("slice_key") == "phone"
            ),
            None,
        )
        assert phone_tdd and phone_tdd.get("register_id"), phone_tdd

        report2 = worker_clean_report(conn, system_key=MEMBERSHIP_SYSTEM_KEY)
        return {
            "ok": True,
            "gate": GATE_POLICY,
            "pipeline": PIPELINE_ID,
            "db": path,
            "schema": schema,
            "seed_root_task_id": seed.get("root_task_id"),
            "slices_n": len(seed.get("slices") or []),
            "registers_n": len(seed.get("registers") or []),
            "worker_active_n": report2.get("active_n"),
            "cleanup_marked_tasks": len(cleanup.get("marked_tasks") or []),
            "cleanup_marked_registers": len(cleanup.get("marked_registers") or []),
            "refuse_unregistered": True,
            "managed_invoke_ok": True,
            "invoke_register_id": got_rid,
            "equation": seed.get("equation"),
            "builder_system_key": "member_card",
            "builder_registers_n": len((built.get("build") or {}).get("registers") or []),
            "builder_tdd_n": len((built.get("build") or {}).get("tdd_rules") or []),
            "builder_request_key": (built.get("request") or {}).get("request_key"),
            "notes": "flow=request→research→ssot→build; coding tracable via register_id+tdd",
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["contracts"]
    cmd = args[0].strip().lower()

    if cmd in ("contracts", "mcs0", "doc"):
        print(json.dumps(contracts_doc(), ensure_ascii=False, indent=2))
        return 0

    if cmd in ("selftest", "test", "smoke"):
        result = run_selftest(migrate=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if cmd in ("seed", "membership", "seed-membership"):
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default=None)
        ap.add_argument("--no-migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path

        path = get_db_path(ns.db)
        if not ns.no_migrate:
            ensure_schema(path)
        conn = sqlite3.connect(path)
        try:
            out = seed_membership_system(conn, commit=True)
            mark_demo_noise_rubbish(conn, commit=True)
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if cmd in ("report", "worker", "clean"):
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default=None)
        ap.add_argument("--system", default=MEMBERSHIP_SYSTEM_KEY)
        ap.add_argument("--migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path

        path = get_db_path(ns.db)
        if ns.migrate:
            ensure_schema(path)
        conn = sqlite3.connect(path)
        try:
            out = worker_clean_report(conn, system_key=ns.system or None)
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    if cmd in ("verify-schema", "verify"):
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default=None)
        ap.add_argument("--migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path

        path = get_db_path(ns.db)
        if ns.migrate:
            ensure_schema(path)
        conn = sqlite3.connect(path)
        try:
            out = verify_managed_schema(conn)
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1

    if cmd in ("cleanup", "mark-rubbish"):
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default=None)
        ns = ap.parse_args(args[1:])
        from db_schema import get_db_path

        path = get_db_path(ns.db)
        conn = sqlite3.connect(path)
        try:
            out = mark_demo_noise_rubbish(conn, commit=True)
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if cmd in ("build", "builder", "request"):
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default=None)
        ap.add_argument("--text", required=True, help="human request text")
        ap.add_argument("--module", required=True)
        ap.add_argument("--channel", default="local_pc")
        ap.add_argument("--system", default=None)
        ap.add_argument("--table", default=None)
        ap.add_argument("--output", default=None)
        ap.add_argument("--fields", default="", help="comma fields e.g. region,phone,name")
        ap.add_argument("--no-migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path

        path = get_db_path(ns.db)
        if not ns.no_migrate:
            ensure_schema(path)
        conn = sqlite3.connect(path)
        try:
            out = run_function_builder(
                conn,
                human_text=ns.text,
                module_code=ns.module,
                channel_code=ns.channel,
                system_key=ns.system,
                desired_table=ns.table,
                desired_output=ns.output,
                fields=ns.fields,
                commit=True,
            )
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1

    if cmd in ("import-tdd", "tdd-import", "seed-tdd"):
        ap = argparse.ArgumentParser()
        ap.add_argument("--db", default=None)
        ap.add_argument("--no-migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        from db_schema import ensure_schema, get_db_path
        from field_tdd import import_membership_field_tdd

        path = get_db_path(ns.db)
        if not ns.no_migrate:
            ensure_schema(path)
        conn = sqlite3.connect(path)
        try:
            out = import_membership_field_tdd(conn, ensure_address=True, commit=True)
        finally:
            conn.close()
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if out.get("ok") else 1

    print(
        "usage: managed_coding.py "
        "contracts|selftest|seed|report|verify-schema|cleanup|build|import-tdd",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
