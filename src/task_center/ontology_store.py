"""
Ontology registry store — READ-ONLY taxonomy SSOT helpers.

Tables: channel_registry | module_registry | capability_registry |
        function_registry | api_registry | db_table_registry | db_field_registry

Business rules (enforced by FK + app checks on manual write paths):
- capability must bind an existing active module
- function / api must bind an existing active capability
- db_field must bind an existing active db_table
- soft delete only: is_active = 0 (no DELETE for history)

This module does NOT auto-write SSOT from validators.
Seed is migrate/CLI only (seed_ontology_registry_defaults).
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB = BASE_DIR / "agent.db"
SQL_PATH = BASE_DIR / "init_ontology_registry.sql"

REGISTRY_TABLES = (
    "channel_registry",
    "module_registry",
    "capability_registry",
    "function_registry",
    "api_registry",
    "db_table_registry",
    "db_field_registry",
)

# Additive TDD case SSOT (not part of validate_new_task 7-table gate)
TEST_CASE_TABLE = "test_case_registry"
# Runner/caller write-only audit log (validate_new_task never writes)
AUDIT_TRACE_TABLE = "audit_trace"
# Snapshot-before-write history for 7 taxonomy registries (validate never writes)
REVISION_HISTORY_TABLE = "ontology_revision_history"
# Harvested FAIL lessons from audit_trace (validate never writes)
EXPERIENCE_LOG_TABLE = "experience_log"
# Ontology validate kicker queue (NOT legacy init_db.sql task_queue)
TASK_QUEUE_TABLE = "validate_task_queue"
REQUIRED_VALIDATE_TEST_TAGS = (
    "tc.1.0",
    "tc.1.1",
    "tc.1.2",
    "tc.1.3",
    "tc.1.4",
    "tc.1.5",
    "tc.1.6",
    "tc.1.7",
    # invalid edge cases (expected_ok=false)
    "tc.1.8",
    "tc.1.9",
    "tc.1.10",
    "tc.1.11",
    "tc.1.12",
    # independent 7-entity edge suite (expected_ok=false)
    "tc.3.0",
    "tc.3.1",
    "tc.3.2",
    "tc.3.3",
    "tc.3.4",
    "tc.3.5",
    "tc.3.6",
    "tc.3.7",
    "tc.3.8",
    "tc.3.9",
    "tc.3.10",
    "tc.3.11",
    "tc.3.12",
    "tc.3.13",
)
REQUIRED_REVISION_TEST_TAGS = (
    "tc.2.0",
    "tc.2.1",
)
REQUIRED_TASK_QUEUE_TEST_TAGS = (
    "tc.4.0",
    "tc.4.1",
    "tc.4.2",
)
TASK_QUEUE_STATUS_PENDING = "pending"
TASK_QUEUE_STATUS_RUNNING = "running"
TASK_QUEUE_STATUS_DONE = "done"
TASK_QUEUE_STATUS_FAILED = "failed"
TASK_QUEUE_TERMINAL_STATUSES = frozenset(
    {TASK_QUEUE_STATUS_DONE, TASK_QUEUE_STATUS_FAILED}
)
# Disposable probe capability for revision/rollback TDD (not used by validate gate)
REVISION_PROBE_CAPABILITY_KEY = "task_center.__rev_probe__"
REVISION_PROBE_MODULE_KEY = "task_center"

# Mouse Spot capability suffixes, in the order the retired CP-S-00..08 occupied.
# Kept as a tuple so the seed and any reader agree, and so the retired numbering
# is not reintroduced by an off-by-one range.
MOUSE_SPOT_CAPABILITY_SUFFIXES: tuple[str, ...] = (
    "core",                # was CP-S-00
    "prompt_load",         # was CP-S-01
    "prompt_render",       # was CP-S-02
    "mouse_spot_verify",   # was CP-S-03
    "prompt_regression",   # was CP-S-04
    "prompt_promote",      # was CP-S-05
    "skills_api",          # was CP-S-06
    "prompt_ssot",         # was CP-S-07
    "events",              # was CP-S-08
    # was CP-S-09: referenced in skill_prompt_ext.py but never seeded. Seeding
    # it now removes a dangling reference rather than preserving it.
    "task_format_validator",
)
# Composite entity_key delimiter for function/api/db_field
ENTITY_KEY_SEP = "|"

# entity_type → table metadata (7 taxonomy registries only)
ENTITY_TYPE_SPECS: dict[str, dict[str, Any]] = {
    "channel": {
        "table": "channel_registry",
        "pk": "channel_id",
        "key_col": "channel_key",
        "composite": False,
        "updatable": frozenset({"name", "description", "is_active", "version"}),
    },
    "module": {
        "table": "module_registry",
        "pk": "module_id",
        "key_col": "module_key",
        "composite": False,
        "updatable": frozenset(
            {"name", "description", "channel_id", "is_active", "version"}
        ),
    },
    "capability": {
        "table": "capability_registry",
        "pk": "capability_id",
        "key_col": "capability_key",
        "composite": False,
        "updatable": frozenset(
            {"name", "description", "module_id", "is_active", "version"}
        ),
    },
    "function": {
        "table": "function_registry",
        "pk": "function_id",
        "key_col": "function_key",
        "composite": True,
        "parent_type": "capability",
        "parent_table": "capability_registry",
        "parent_key_col": "capability_key",
        "parent_pk": "capability_id",
        "fk_col": "capability_id",
        "updatable": frozenset(
            {
                "name",
                "description",
                "code_registry_id",
                "file_path",
                "is_active",
                "version",
            }
        ),
    },
    "api": {
        "table": "api_registry",
        "pk": "api_id",
        "key_col": "api_key",
        "composite": True,
        "parent_type": "capability",
        "parent_table": "capability_registry",
        "parent_key_col": "capability_key",
        "parent_pk": "capability_id",
        "fk_col": "capability_id",
        "updatable": frozenset(
            {
                "name",
                "description",
                "method",
                "path",
                "is_active",
                "version",
            }
        ),
    },
    "db_table": {
        "table": "db_table_registry",
        "pk": "db_table_id",
        "key_col": "table_key",
        "composite": False,
        "updatable": frozenset({"name", "description", "is_active", "version"}),
    },
    "db_field": {
        "table": "db_field_registry",
        "pk": "db_field_id",
        "key_col": "field_key",
        "composite": True,
        "parent_type": "db_table",
        "parent_table": "db_table_registry",
        "parent_key_col": "table_key",
        "parent_pk": "db_table_id",
        "fk_col": "db_table_id",
        "updatable": frozenset({"name", "description", "is_active", "version"}),
    },
}


def _norm(v: Any) -> str:
    return str(v or "").strip()


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _row(d: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(d) if d is not None else None


def registry_tables_exist(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
            ",".join("?" for _ in REGISTRY_TABLES)
        ),
        REGISTRY_TABLES,
    ).fetchall()
    found = {str(r[0]) for r in rows}
    return all(t in found for t in REGISTRY_TABLES)


def ensure_ontology_registry_schema(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """CREATE IF NOT EXISTS all seven registry tables (idempotent)."""
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        if SQL_PATH.is_file():
            sql = SQL_PATH.read_text(encoding="utf-8")
            # Strip pure comment-only example blocks are fine for executescript
            conn.executescript(sql)
        else:
            # Minimal fallback if SQL file missing.
            #
            # 🔴 THE THIRD COPY OF `channel_registry` WAS REMOVED 2026-09-29
            # (RING 5, D1). MEASURED: this fallback was a THIRD declaration of a
            # table whose SSOT is `db_schema.CHANNEL_REGISTRY_DDL` and whose
            # second copy was `init_ontology_registry.sql` — and NEITHER of those
            # two agreed with it, because this one has no `url`. A fallback is
            # still a DECLARATION: when it is the one that runs, the table is
            # built without `url` and the write path's `INSERT ... url` fails.
            #
            # A missing SQL file is a BROKEN INSTALL, not a state to paper over
            # with a weaker schema. It now raises the import error it already
            # caught above, naming the file, so the failure is loud instead of
            # silently producing a different database.
            raise FileNotFoundError(
                "ontology registry SQL not found: %s. This is a broken install; "
                "a weaker inline fallback schema would silently build a database "
                "that disagrees with the SSOT (measured defect, RING 5 D1)."
                % SQL_PATH
            )
        ok = registry_tables_exist(conn)
        if own:
            conn.commit()
        return {"ok": ok, "tables": list(REGISTRY_TABLES)}
    finally:
        if own:
            conn.close()


def seed_ontology_registry_defaults(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Idempotent T-SKILL01 seed. CALL FROM MIGRATE/CLI ONLY — never from validate_new_task.
    INSERT OR IGNORE / existence checks only; does not deactivate rows.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        conn.execute("PRAGMA foreign_keys = ON;")
        now = conn.execute("SELECT datetime('now')").fetchone()[0]

        def _ins_channel(key: str, name: str) -> int:
            conn.execute(
                """
                INSERT OR IGNORE INTO channel_registry
                    (channel_key, name, description, is_active, version, created_at, updated_at)
                VALUES (?, ?, ?, 1, '1', ?, ?)
                """,
                (key, name, f"seed {key}", now, now),
            )
            row = conn.execute(
                "SELECT channel_id FROM channel_registry WHERE channel_key = ?",
                (key,),
            ).fetchone()
            return int(row["channel_id"])

        def _ins_module(key: str, name: str, channel_id: int) -> int:
            conn.execute(
                """
                INSERT OR IGNORE INTO module_registry
                    (module_key, name, description, channel_id, is_active, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, '1', ?, ?)
                """,
                (key, name, f"seed {key}", channel_id, now, now),
            )
            # If row existed from older seed with different channel, leave as-is
            row = conn.execute(
                "SELECT module_id FROM module_registry WHERE module_key = ?",
                (key,),
            ).fetchone()
            return int(row["module_id"])

        def _ins_cap(key: str, name: str, module_id: int) -> int:
            conn.execute(
                """
                INSERT OR IGNORE INTO capability_registry
                    (capability_key, name, description, module_id, is_active, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, '1', ?, ?)
                """,
                (key, name, f"seed {key}", module_id, now, now),
            )
            row = conn.execute(
                "SELECT capability_id FROM capability_registry WHERE capability_key = ?",
                (key,),
            ).fetchone()
            return int(row["capability_id"])

        ch_id = _ins_channel("local_pc", "Local PC")
        mod_tc = _ins_module("task_center", "Task Center", ch_id)
        mod_ms = _ins_module("mouse_spot_helper", "Mouse Spot Helper", ch_id)
        mod_oc = _ins_module("openclaw_companion", "OpenClaw Companion", ch_id)

        cap_v = _ins_cap(
            "task_center.validate_new_task",
            "Validate New Task",
            mod_tc,
        )
        _ins_cap(
            "task_center.append_lifecycle_event",
            "Append Lifecycle Event",
            mod_tc,
        )
        # Chat Identity capability — taxonomy anchor for the chat_identity_log
        # skill contracts (SKILL-0001 / SKILL-0002).
        _ins_cap(
            "task_center.chat_identity",
            "Chat Identity",
            mod_tc,
        )
        # Mouse Spot capabilities. The legacy `CP-S-00..08` numbering was
        # retired (2026-09-21): it was one fact copied into three files, and it
        # hid which capability a row meant. Keys are now
        # `{module}.{capability}`, matching the convention the rows above use.
        # The 10th key (`...task_format_validator`) replaces the dangling
        # `CP-S-09` reference that existed in skill_prompt_ext.py with no
        # registry row behind it.
        for _suffix in MOUSE_SPOT_CAPABILITY_SUFFIXES:
            _ins_cap(
                "mouse_spot_helper.%s" % _suffix,
                "Mouse Spot %s" % _suffix.replace("_", " "),
                mod_ms,
            )
        _ins_cap("capability.ssot", "Capability SSOT", mod_oc)

        # function + api for validate_new_task
        conn.execute(
            """
            INSERT OR IGNORE INTO function_registry
                (function_key, name, description, capability_id, code_registry_id,
                 file_path, is_active, version, created_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?, 1, '1', ?, ?)
            """,
            (
                "task_center.validate_new_task",
                "validate_new_task",
                "Pre-dispatch taxonomy validator",
                cap_v,
                "src/task_center/skill_task_validate.py",
                now,
                now,
            ),
        )
        for api_key in (
            "POST /api/tasks/validate",
            "GET /tasks/validate",
            "none",
        ):
            conn.execute(
                """
                INSERT OR IGNORE INTO api_registry
                    (api_key, name, description, method, path, capability_id,
                     is_active, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, '1', ?, ?)
                """,
                (
                    api_key,
                    api_key,
                    f"seed api {api_key}",
                    (api_key.split()[0] if " " in api_key else None),
                    (api_key.split()[1] if " " in api_key else api_key),
                    cap_v,
                    now,
                    now,
                ),
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO db_table_registry
                (table_key, name, description, is_active, version, created_at, updated_at)
            VALUES ('code_registry', 'code_registry', 'MCS enrollment SSOT', 1, '1', ?, ?)
            """,
            (now, now),
        )
        tbl = conn.execute(
            "SELECT db_table_id FROM db_table_registry WHERE table_key = 'code_registry'"
        ).fetchone()
        tid = int(tbl["db_table_id"])
        for fk in (
            "register_id",
            "module_name",
            "function_name",
            "file_path",
            "status",
            "task_id",
        ):
            conn.execute(
                """
                INSERT OR IGNORE INTO db_field_registry
                    (field_key, name, description, db_table_id, is_active, version, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, '1', ?, ?)
                """,
                (fk, fk, f"code_registry.{fk}", tid, now, now),
            )

        tc_seed = seed_validate_new_task_test_cases(conn=conn)
        rev_seed = seed_ontology_revision_test_cases(conn=conn)
        tq_seed = seed_task_queue_test_cases(conn=conn)
        if own:
            conn.commit()
        caps = get_all_registered_capabilities(conn=conn)
        return {
            "ok": True,
            "capability_count": len(caps),
            "sample": caps[:5],
            "seed": "ontology_registry_t_skill01",
            "test_cases": tc_seed,
            "revision_test_cases": rev_seed,
            "task_queue_test_cases": tq_seed,
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# test_case_registry (TDD SSOT)
# ---------------------------------------------------------------------------


def _parse_json_field(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    s = str(raw).strip()
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _serialize_test_case_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    return {
        "test_case_id": d.get("test_case_id"),
        "case_ref_tag": d.get("case_ref_tag"),
        "case_title": d.get("case_title"),
        "input_payload": _parse_json_field(d.get("input_payload"), {}),
        "expected_ok": bool(int(d.get("expected_ok") or 0)),
        "expected_errors": _parse_json_field(d.get("expected_errors"), []),
        "is_active": bool(int(d.get("is_active") or 0)),
        "version": d.get("version"),
        "created_at": d.get("created_at"),
        "updated_at": d.get("updated_at"),
    }


def test_case_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TEST_CASE_TABLE,),
    ).fetchone()
    return bool(row)


def get_all_active_test_cases(
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return all active rows from test_case_registry (TDD SSOT)."""
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not test_case_table_exists(conn):
            return []
        rows = conn.execute(
            """
            SELECT test_case_id, case_ref_tag, case_title, input_payload,
                   expected_ok, expected_errors, is_active, version,
                   created_at, updated_at
            FROM test_case_registry
            WHERE is_active = 1
            ORDER BY case_ref_tag
            """
        ).fetchall()
        return [_serialize_test_case_row(r) for r in rows]
    finally:
        if own:
            conn.close()


def get_test_case(
    case_ref_tag: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Fetch one test case by case_ref_tag (active or inactive)."""
    tag = _norm(case_ref_tag)
    if not tag:
        return None
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not test_case_table_exists(conn):
            return None
        row = conn.execute(
            """
            SELECT test_case_id, case_ref_tag, case_title, input_payload,
                   expected_ok, expected_errors, is_active, version,
                   created_at, updated_at
            FROM test_case_registry
            WHERE case_ref_tag = ?
            """,
            (tag,),
        ).fetchone()
        return _serialize_test_case_row(row) if row else None
    finally:
        if own:
            conn.close()


def seed_validate_new_task_test_cases(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Upsert the 8 validate_new_task unit cases into test_case_registry.
    CALL FROM MIGRATE/CLI ONLY — never from validate_new_task.
    Seed is the write path; runners must READ from DB only.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not test_case_table_exists(conn):
            return {"ok": False, "error": "test_case_registry missing after ensure"}
        now = conn.execute("SELECT datetime('now')").fetchone()[0]
        canon = {
            "task": "T-SKILL01 — Register Task Format Validator Skill",
            "channel": "local_pc",
            "module": "task_center",
            "capability": "task_center.validate_new_task",
            "api": "POST /api/tasks/validate",
            "function": "task_center.validate_new_task",
            "table": "code_registry",
            "field": "register_id, module_name, function_name, file_path",
        }

        def _case(
            tag: str,
            title: str,
            payload: dict[str, Any],
            expected_ok: bool,
            expected_errors: list[str],
        ) -> dict[str, Any]:
            return {
                "case_ref_tag": tag,
                "case_title": title,
                "input_payload": payload,
                "expected_ok": expected_ok,
                "expected_errors": expected_errors,
            }

        cases = [
            _case("tc.1.0", "canon — full 8-dim PASS", dict(canon), True, []),
            _case(
                "tc.1.1",
                "bad_cap — unknown capability",
                {**canon, "capability": "task_center.unknown_capability"},
                False,
                ["capability"],
            ),
            _case(
                "tc.1.2",
                "bad_api — unregistered api",
                {**canon, "api": "POST /api/tasks/__invalid__"},
                False,
                ["api"],
            ),
            _case(
                "tc.1.3",
                "bad_fn — unregistered function",
                {**canon, "function": "task_center.__invalid_fn__"},
                False,
                ["function"],
            ),
            _case(
                "tc.1.4",
                "bad_col — unknown field",
                {**canon, "field": "bad_column"},
                False,
                ["field"],
            ),
            _case(
                "tc.1.5",
                "bad_ch — unknown channel",
                {**canon, "channel": "wrong_channel"},
                False,
                ["channel"],
            ),
            _case(
                "tc.1.6",
                "bad_mod — unknown module",
                {**canon, "module": "wrong_module"},
                False,
                ["module"],
            ),
            _case(
                "tc.1.7",
                "null_cap — empty capability",
                {**canon, "capability": None},
                False,
                ["capability"],
            ),
            # --- invalid edge cases (expected_ok=false); originals above untouched ---
            _case(
                "tc.1.8",
                "extra_key — unknown top-level field",
                {**canon, "unexpected_meta": "should_fail"},
                False,
                ["unknown top-level", "unexpected_meta"],
            ),
            _case(
                "tc.1.9",
                "ws_cap — capability only whitespace",
                {**canon, "capability": "   "},
                False,
                ["capability"],
            ),
            _case(
                "tc.1.10",
                "num_mod — module value is numeric",
                {**canon, "module": 12345},
                False,
                ["module"],
            ),
            _case(
                "tc.1.11",
                "empty_table — empty string table name",
                {**canon, "table": ""},
                False,
                ["table"],
            ),
            _case(
                "tc.1.12",
                "sql_field — field name SQL injection-like",
                {
                    **canon,
                    "field": "register_id; DROP TABLE code_registry;--",
                },
                False,
                ["field"],
            ),
            # --- tc.3.* independent 7-entity edge suite (expected_ok=false) ---
            # tc.1.0–tc.1.12 above remain untouched
            _case(
                "tc.3.0",
                "unknown_channel — not in channel_registry",
                {**canon, "channel": "channel.__unknown__"},
                False,
                ["channel"],
            ),
            _case(
                "tc.3.1",
                "ws_channel — channel only whitespace",
                {**canon, "channel": "   "},
                False,
                ["channel"],
            ),
            _case(
                "tc.3.2",
                "unknown_module — not in module_registry",
                {**canon, "module": "module.__unknown__"},
                False,
                ["module"],
            ),
            _case(
                "tc.3.3",
                "ws_module — module only whitespace",
                {**canon, "module": "   "},
                False,
                ["module"],
            ),
            _case(
                "tc.3.4",
                "unknown_api — not in api_registry",
                {**canon, "api": "POST /api/__unknown__/edge"},
                False,
                ["api"],
            ),
            _case(
                "tc.3.5",
                "ws_api — api only whitespace",
                {**canon, "api": "   "},
                False,
                ["api"],
            ),
            _case(
                "tc.3.6",
                "unknown_function — not in function_registry",
                {**canon, "function": "task_center.__unknown_fn_edge__"},
                False,
                ["function"],
            ),
            _case(
                "tc.3.7",
                "ws_function — function only whitespace",
                {**canon, "function": "   "},
                False,
                ["function"],
            ),
            _case(
                "tc.3.8",
                "unknown_table — not in db_table_registry",
                {**canon, "table": "table.__unknown__"},
                False,
                ["table"],
            ),
            _case(
                "tc.3.9",
                "ws_table — table only whitespace",
                {**canon, "table": "   "},
                False,
                ["table"],
            ),
            _case(
                "tc.3.10",
                "unknown_field — not in db_field_registry",
                {**canon, "field": "field.__unknown_col__"},
                False,
                ["field"],
            ),
            _case(
                "tc.3.11",
                "ws_field — field only whitespace",
                {**canon, "field": "   "},
                False,
                ["field"],
            ),
            _case(
                "tc.3.12",
                "num_channel — channel is numeric type",
                {**canon, "channel": 99901},
                False,
                ["channel"],
            ),
            _case(
                "tc.3.13",
                "ctrl_module — module contains special control characters",
                {**canon, "module": "task_center\x00\x1f\n\t"},
                False,
                ["module"],
            ),
        ]

        upserted = 0
        for c in cases:
            conn.execute(
                """
                INSERT INTO test_case_registry (
                    case_ref_tag, case_title, input_payload, expected_ok,
                    expected_errors, is_active, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, '1', ?, ?)
                ON CONFLICT(case_ref_tag) DO UPDATE SET
                    case_title = excluded.case_title,
                    input_payload = excluded.input_payload,
                    expected_ok = excluded.expected_ok,
                    expected_errors = excluded.expected_errors,
                    is_active = 1,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    c["case_ref_tag"],
                    c["case_title"],
                    json.dumps(c["input_payload"], ensure_ascii=False),
                    1 if c["expected_ok"] else 0,
                    json.dumps(c["expected_errors"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            upserted += 1

        if own:
            conn.commit()
        active = get_all_active_test_cases(conn=conn)
        tags = [x["case_ref_tag"] for x in active]
        missing = [t for t in REQUIRED_VALIDATE_TEST_TAGS if t not in tags]
        return {
            "ok": len(missing) == 0,
            "upserted": upserted,
            "active_count": len(active),
            "tags": tags,
            "missing_required": missing,
            "seed": "validate_new_task_unit_27",
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# ontology_revision_history (mutate/rollback only — never from validate)
# ---------------------------------------------------------------------------


def revision_history_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (REVISION_HISTORY_TABLE,),
    ).fetchone()
    return bool(row)


def _split_entity_key(entity_key: str) -> tuple[str, str | None]:
    key = _norm(entity_key)
    if ENTITY_KEY_SEP in key:
        parent, child = key.split(ENTITY_KEY_SEP, 1)
        return _norm(parent), _norm(child) or None
    return key, None


def _compose_entity_key(parent_key: str, child_key: str) -> str:
    return f"{_norm(parent_key)}{ENTITY_KEY_SEP}{_norm(child_key)}"


def _serialize_revision_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    return {
        "revision_id": d.get("revision_id"),
        "entity_type": d.get("entity_type"),
        "entity_key": d.get("entity_key"),
        "snapshot": _parse_json_field(d.get("snapshot"), {}),
        "is_deleted": bool(int(d.get("is_deleted") or 0)),
        "created_at": d.get("created_at"),
        "created_by": d.get("created_by"),
        "note": d.get("note"),
    }


def _fetch_live_registry_row(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_key: str,
) -> dict[str, Any] | None:
    et = _norm(entity_type).lower()
    spec = ENTITY_TYPE_SPECS.get(et)
    if not spec:
        return None
    table = spec["table"]
    if not spec.get("composite"):
        row = conn.execute(
            f"SELECT * FROM {table} WHERE {spec['key_col']} = ?",
            (_norm(entity_key),),
        ).fetchone()
        return dict(row) if row else None

    parent_key, child_key = _split_entity_key(entity_key)
    if not parent_key or not child_key:
        return None
    parent_table = spec["parent_table"]
    parent_key_col = spec["parent_key_col"]
    parent_pk = spec["parent_pk"]
    fk_col = spec["fk_col"]
    key_col = spec["key_col"]
    row = conn.execute(
        f"""
        SELECT c.*
        FROM {table} c
        JOIN {parent_table} p ON p.{parent_pk} = c.{fk_col}
        WHERE p.{parent_key_col} = ? AND c.{key_col} = ?
        """,
        (parent_key, child_key),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["_parent_key"] = parent_key
    d["_entity_key"] = _compose_entity_key(parent_key, child_key)
    return d


def _snapshot_registry_row(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_key: str,
) -> dict[str, Any] | None:
    row = _fetch_live_registry_row(conn, entity_type, entity_key)
    if not row:
        return None
    # JSON-safe copy
    out: dict[str, Any] = {}
    for k, v in row.items():
        if str(k).startswith("_"):
            continue
        out[str(k)] = v
    out["_entity_type"] = _norm(entity_type).lower()
    out["_entity_key"] = _norm(entity_key)
    return out


def _insert_revision(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_key: str,
    snapshot: dict[str, Any] | None,
    is_deleted: bool = False,
    created_by: str | None = None,
    note: str | None = None,
) -> str:
    rid = f"orev_{uuid.uuid4().hex[:12]}"
    snap = snapshot if isinstance(snapshot, dict) else {}
    conn.execute(
        """
        INSERT INTO ontology_revision_history (
            revision_id, entity_type, entity_key, snapshot,
            is_deleted, created_at, created_by, note
        ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?, ?)
        """,
        (
            rid,
            _norm(entity_type).lower(),
            _norm(entity_key),
            json.dumps(snap, ensure_ascii=False, default=str),
            1 if is_deleted else 0,
            _norm(created_by) or None,
            note,
        ),
    )
    return rid


def get_registry_entity(
    entity_type: str,
    entity_key: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Read one taxonomy registry row by entity_type + entity_key (any is_active)."""
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        return _fetch_live_registry_row(conn, entity_type, entity_key)
    finally:
        if own:
            conn.close()


def ensure_revision_probe_entity(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
    reset_baseline: bool = False,
) -> dict[str, Any]:
    """
    Ensure disposable capability probe exists for revision TDD.
    Default: INSERT OR IGNORE only (no history).
    If reset_baseline=True and row drifted, restore via update_registry_entity
    (snapshot-before-write).
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        now = conn.execute("SELECT datetime('now')").fetchone()[0]
        mod = conn.execute(
            "SELECT module_id FROM module_registry WHERE module_key = ?",
            (REVISION_PROBE_MODULE_KEY,),
        ).fetchone()
        if not mod:
            return {
                "ok": False,
                "error": f"module {REVISION_PROBE_MODULE_KEY} missing — seed ontology first",
            }
        module_id = int(mod["module_id"])
        baseline_name = "Revision Probe Capability"
        baseline_desc = "Disposable probe for ontology_revision_history TDD"
        conn.execute(
            """
            INSERT OR IGNORE INTO capability_registry
                (capability_key, name, description, module_id, is_active, version, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, '1', ?, ?)
            """,
            (
                REVISION_PROBE_CAPABILITY_KEY,
                baseline_name,
                baseline_desc,
                module_id,
                now,
                now,
            ),
        )
        row = _fetch_live_registry_row(
            conn, "capability", REVISION_PROBE_CAPABILITY_KEY
        )
        reset_result = None
        if reset_baseline and row:
            drifted = (
                str(row.get("name") or "") != baseline_name
                or str(row.get("description") or "") != baseline_desc
                or str(row.get("version") or "") != "1"
                or int(row.get("is_active") or 0) != 1
            )
            if drifted:
                # Snapshot-before-write restore (do not bypass history)
                reset_result = update_registry_entity(
                    "capability",
                    REVISION_PROBE_CAPABILITY_KEY,
                    {
                        "name": baseline_name,
                        "description": baseline_desc,
                        "version": "1",
                        "is_active": 1,
                    },
                    created_by="ensure_revision_probe_entity",
                    note="reset probe to baseline",
                    conn=conn,
                )
                row = _fetch_live_registry_row(
                    conn, "capability", REVISION_PROBE_CAPABILITY_KEY
                )
        if own:
            conn.commit()
        return {
            "ok": bool(row),
            "entity_type": "capability",
            "entity_key": REVISION_PROBE_CAPABILITY_KEY,
            "row": row,
            "reset": reset_result,
        }
    finally:
        if own:
            conn.close()


def update_registry_entity(
    entity_type: str,
    entity_key: str,
    fields: dict[str, Any] | None = None,
    *,
    created_by: str | None = None,
    note: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Snapshot-before-update for a taxonomy registry row.
    CALL FROM MIGRATE/CLI/TESTS ONLY — never from validate_new_task.
    """
    et = _norm(entity_type).lower()
    ek = _norm(entity_key)
    spec = ENTITY_TYPE_SPECS.get(et)
    if not spec:
        return {"ok": False, "error": f"unknown entity_type: {entity_type}"}
    if not ek:
        return {"ok": False, "error": "entity_key required"}
    patch = dict(fields or {})
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not revision_history_table_exists(conn):
            return {"ok": False, "error": "ontology_revision_history missing after ensure"}
        live = _fetch_live_registry_row(conn, et, ek)
        if not live:
            return {"ok": False, "error": f"entity not found: {et}/{ek}"}

        snap = _snapshot_registry_row(conn, et, ek) or {}
        rid = _insert_revision(
            conn,
            entity_type=et,
            entity_key=ek,
            snapshot=snap,
            is_deleted=False,
            created_by=created_by,
            note=note or "update_registry_entity pre-image",
        )

        allowed = spec["updatable"]
        sets: list[str] = []
        vals: list[Any] = []
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "is_active":
                v = 1 if bool(v) else 0
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            if own:
                conn.commit()
            return {
                "ok": True,
                "revision_id": rid,
                "updated": False,
                "reason": "no allowed fields in patch",
                "row": _fetch_live_registry_row(conn, et, ek),
            }
        sets.append("updated_at = datetime('now')")
        pk = spec["pk"]
        pk_val = live.get(pk)
        vals.append(pk_val)
        conn.execute(
            f"UPDATE {spec['table']} SET {', '.join(sets)} WHERE {pk} = ?",
            tuple(vals),
        )
        if own:
            conn.commit()
        new_row = _fetch_live_registry_row(conn, et, ek)
        return {
            "ok": True,
            "revision_id": rid,
            "updated": True,
            "entity_type": et,
            "entity_key": ek,
            "row": new_row,
            "pre_image": snap,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def soft_delete_registry_entity(
    entity_type: str,
    entity_key: str,
    *,
    created_by: str | None = None,
    note: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Snapshot then set is_active=0. History row is_deleted=1."""
    et = _norm(entity_type).lower()
    ek = _norm(entity_key)
    spec = ENTITY_TYPE_SPECS.get(et)
    if not spec:
        return {"ok": False, "error": f"unknown entity_type: {entity_type}"}
    if not ek:
        return {"ok": False, "error": "entity_key required"}
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not revision_history_table_exists(conn):
            return {"ok": False, "error": "ontology_revision_history missing after ensure"}
        live = _fetch_live_registry_row(conn, et, ek)
        if not live:
            return {"ok": False, "error": f"entity not found: {et}/{ek}"}
        snap = _snapshot_registry_row(conn, et, ek) or {}
        rid = _insert_revision(
            conn,
            entity_type=et,
            entity_key=ek,
            snapshot=snap,
            is_deleted=True,
            created_by=created_by,
            note=note or "soft_delete_registry_entity pre-image",
        )
        pk = spec["pk"]
        conn.execute(
            f"""
            UPDATE {spec['table']}
            SET is_active = 0, updated_at = datetime('now')
            WHERE {pk} = ?
            """,
            (live.get(pk),),
        )
        if own:
            conn.commit()
        return {
            "ok": True,
            "revision_id": rid,
            "entity_type": et,
            "entity_key": ek,
            "row": _fetch_live_registry_row(conn, et, ek),
            "pre_image": snap,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def get_entity_revisions(
    entity_type: str,
    entity_key: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    et = _norm(entity_type).lower()
    ek = _norm(entity_key)
    if not et or not ek:
        return []
    lim = max(1, min(int(limit or 50), 1000))
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not revision_history_table_exists(conn):
            return []
        rows = conn.execute(
            """
            SELECT * FROM ontology_revision_history
            WHERE entity_type = ? AND entity_key = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (et, ek, lim),
        ).fetchall()
        return [_serialize_revision_row(r) for r in rows]
    finally:
        if own:
            conn.close()


def rollback_entity_to_revision(
    entity_type: str,
    entity_key: str,
    revision_id: str,
    *,
    created_by: str | None = None,
    note: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Restore snapshot from revision_id onto live registry row.
    Appends a NEW history entry for the rollback action (pre-rollback live snapshot).
    Does not delete history rows.
    """
    et = _norm(entity_type).lower()
    ek = _norm(entity_key)
    rid = _norm(revision_id)
    spec = ENTITY_TYPE_SPECS.get(et)
    if not spec:
        return {"ok": False, "error": f"unknown entity_type: {entity_type}"}
    if not ek or not rid:
        return {"ok": False, "error": "entity_key and revision_id required"}
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not revision_history_table_exists(conn):
            return {"ok": False, "error": "ontology_revision_history missing after ensure"}

        hist = conn.execute(
            "SELECT * FROM ontology_revision_history WHERE revision_id = ?",
            (rid,),
        ).fetchone()
        if not hist:
            return {"ok": False, "error": f"revision not found: {rid}"}
        hist_d = _serialize_revision_row(hist)
        if hist_d.get("entity_type") != et or hist_d.get("entity_key") != ek:
            return {
                "ok": False,
                "error": (
                    f"revision entity mismatch: "
                    f"{hist_d.get('entity_type')}/{hist_d.get('entity_key')} "
                    f"!= {et}/{ek}"
                ),
            }

        live = _fetch_live_registry_row(conn, et, ek)
        if not live:
            return {"ok": False, "error": f"live entity not found: {et}/{ek}"}

        pre = _snapshot_registry_row(conn, et, ek) or {}
        # Always embed rollback_to= so history is self-describing even if caller note set
        base_note = _norm(note) or "pre-rollback live snapshot"
        rb_note = f"rollback_to={rid}; {base_note}"
        new_rid = _insert_revision(
            conn,
            entity_type=et,
            entity_key=ek,
            snapshot=pre,
            is_deleted=bool(int(live.get("is_active") or 0) == 0),
            created_by=created_by,
            note=rb_note,
        )

        snap = hist_d.get("snapshot") if isinstance(hist_d.get("snapshot"), dict) else {}
        # If target revision marked deleted, force inactive; else restore is_active from snap
        if hist_d.get("is_deleted"):
            target_active = 0
        else:
            target_active = 1 if int(snap.get("is_active") or 0) else 0

        allowed = set(spec["updatable"]) | {"is_active", "version", "name", "description"}
        # Prefer restoring all updatable columns present in snapshot
        sets: list[str] = []
        vals: list[Any] = []
        for col in sorted(spec["updatable"]):
            if col not in snap:
                continue
            val = snap.get(col)
            if col == "is_active":
                val = target_active
            sets.append(f"{col} = ?")
            vals.append(val)
        if "is_active" in spec["updatable"] and "is_active" not in {
            s.split()[0] for s in sets
        }:
            sets.append("is_active = ?")
            vals.append(target_active)
        # Always set is_active explicitly
        if not any(s.startswith("is_active") for s in sets):
            # table always has is_active
            sets.append("is_active = ?")
            vals.append(target_active)
        sets.append("updated_at = datetime('now')")
        pk = spec["pk"]
        vals.append(live.get(pk))
        conn.execute(
            f"UPDATE {spec['table']} SET {', '.join(sets)} WHERE {pk} = ?",
            tuple(vals),
        )
        if own:
            conn.commit()
        restored = _fetch_live_registry_row(conn, et, ek)
        return {
            "ok": True,
            "revision_id": new_rid,
            "rolled_back_to": rid,
            "entity_type": et,
            "entity_key": ek,
            "row": restored,
            "target_snapshot": snap,
            "pre_rollback_snapshot": pre,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def seed_ontology_revision_test_cases(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Upsert tc.2.0 / tc.2.1 revision+rollback cases into test_case_registry.
    CALL FROM MIGRATE/CLI ONLY — never from validate_new_task.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not test_case_table_exists(conn):
            return {"ok": False, "error": "test_case_registry missing after ensure"}
        # Ensure probe entity exists for runners
        probe = ensure_revision_probe_entity(conn=conn)
        now = conn.execute("SELECT datetime('now')").fetchone()[0]
        cases = [
            {
                "case_ref_tag": "tc.2.0",
                "case_title": "revision_save — snapshot before update",
                "input_payload": {
                    "suite": "ontology_revision",
                    "action": "update_and_assert_revision",
                    "entity_type": "capability",
                    "entity_key": REVISION_PROBE_CAPABILITY_KEY,
                    "fields": {
                        "name": "Revision Probe Capability (mutated)",
                        "description": "mutated by tc.2.0",
                        "version": "2",
                    },
                    "created_by": "tdd_tc.2.0",
                    "note": "tc.2.0 pre-update snapshot",
                    "assert": {
                        "min_revisions": 1,
                        "name_after": "Revision Probe Capability (mutated)",
                        "snapshot_name_before": "Revision Probe Capability",
                    },
                },
                "expected_ok": True,
                "expected_errors": [],
            },
            {
                "case_ref_tag": "tc.2.1",
                "case_title": "rollback — restore prior revision snapshot",
                "input_payload": {
                    "suite": "ontology_revision",
                    "action": "rollback_to_prior_revision",
                    "entity_type": "capability",
                    "entity_key": REVISION_PROBE_CAPABILITY_KEY,
                    "created_by": "tdd_tc.2.1",
                    "note": "tc.2.1 rollback",
                    "assert": {
                        "name_after_rollback": "Revision Probe Capability",
                        "note_contains": "rollback_to=",
                        "min_revisions": 2,
                    },
                },
                "expected_ok": True,
                "expected_errors": [],
            },
        ]
        upserted = 0
        for c in cases:
            conn.execute(
                """
                INSERT INTO test_case_registry (
                    case_ref_tag, case_title, input_payload, expected_ok,
                    expected_errors, is_active, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, '1', ?, ?)
                ON CONFLICT(case_ref_tag) DO UPDATE SET
                    case_title = excluded.case_title,
                    input_payload = excluded.input_payload,
                    expected_ok = excluded.expected_ok,
                    expected_errors = excluded.expected_errors,
                    is_active = 1,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    c["case_ref_tag"],
                    c["case_title"],
                    json.dumps(c["input_payload"], ensure_ascii=False),
                    1 if c["expected_ok"] else 0,
                    json.dumps(c["expected_errors"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            upserted += 1
        if own:
            conn.commit()
        active = get_all_active_test_cases(conn=conn)
        tags = [x["case_ref_tag"] for x in active]
        missing = [t for t in REQUIRED_REVISION_TEST_TAGS if t not in tags]
        return {
            "ok": len(missing) == 0 and bool(probe.get("ok")),
            "upserted": upserted,
            "active_count": len(active),
            "tags": tags,
            "missing_required": missing,
            "probe": probe,
            "seed": "ontology_revision_unit_2",
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# audit_trace (runner/caller write-only — never from validate_new_task)
# ---------------------------------------------------------------------------


def audit_trace_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (AUDIT_TRACE_TABLE,),
    ).fetchone()
    return bool(row)


def _serialize_audit_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    return {
        "trace_id": d.get("trace_id"),
        "task_id": d.get("task_id"),
        "run_at": d.get("run_at"),
        "channel": d.get("channel"),
        "module": d.get("module"),
        "capability": d.get("capability"),
        "input_payload": _parse_json_field(d.get("input_payload"), {}),
        "verdict": bool(int(d.get("verdict") or 0)),
        "errors": _parse_json_field(d.get("errors"), []),
        "test_case_ref": d.get("test_case_ref"),
        "notes": d.get("notes"),
    }


def insert_audit_trace(
    record: dict[str, Any] | None = None,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Insert one audit_trace row. CALL FROM RUNNER/CALLER ONLY.
    validate_new_task must never call this.
    """
    data = dict(record or {})
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not audit_trace_table_exists(conn):
            return {"ok": False, "error": "audit_trace table missing after ensure"}

        trace_id = _norm(data.get("trace_id")) or f"atr_{uuid.uuid4().hex[:12]}"
        run_at = _norm(data.get("run_at")) or conn.execute(
            "SELECT datetime('now')"
        ).fetchone()[0]
        payload = data.get("input_payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, (dict, list, str)):
            payload = {"raw": str(payload)}
        payload_json = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, default=str)
        )
        errors = data.get("errors")
        if errors is None:
            errors = []
        if isinstance(errors, str):
            errors_json = errors
        else:
            if not isinstance(errors, list):
                errors = [str(errors)]
            errors_json = json.dumps(errors, ensure_ascii=False, default=str)

        verdict_raw = data.get("verdict", data.get("ok", False))
        if isinstance(verdict_raw, str):
            verdict = 1 if verdict_raw.strip().lower() in ("1", "true", "yes", "pass") else 0
        else:
            verdict = 1 if bool(verdict_raw) else 0

        conn.execute(
            """
            INSERT INTO audit_trace (
                trace_id, task_id, run_at, channel, module, capability,
                input_payload, verdict, errors, test_case_ref, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                _norm(data.get("task_id")) or None,
                run_at,
                _norm(data.get("channel")) or None,
                _norm(data.get("module")) or None,
                _norm(data.get("capability")) or None,
                payload_json,
                verdict,
                errors_json,
                _norm(data.get("test_case_ref")) or None,
                data.get("notes") if data.get("notes") is not None else None,
            ),
        )
        if own:
            conn.commit()
        row = conn.execute(
            "SELECT * FROM audit_trace WHERE trace_id = ?", (trace_id,)
        ).fetchone()
        return {
            "ok": True,
            "trace_id": trace_id,
            "row": _serialize_audit_row(row) if row else None,
        }
    except sqlite3.IntegrityError as e:
        return {"ok": False, "error": f"insert blocked: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def get_audit_by_trace_id(
    trace_id: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    tid = _norm(trace_id)
    if not tid:
        return None
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not audit_trace_table_exists(conn):
            return None
        row = conn.execute(
            "SELECT * FROM audit_trace WHERE trace_id = ?", (tid,)
        ).fetchone()
        return _serialize_audit_row(row) if row else None
    finally:
        if own:
            conn.close()


def list_audit_by_test_case(
    case_ref_tag: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    tag = _norm(case_ref_tag)
    if not tag:
        return []
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not audit_trace_table_exists(conn):
            return []
        rows = conn.execute(
            """
            SELECT * FROM audit_trace
            WHERE test_case_ref = ?
            ORDER BY run_at DESC, trace_id DESC
            """,
            (tag,),
        ).fetchall()
        return [_serialize_audit_row(r) for r in rows]
    finally:
        if own:
            conn.close()


def list_recent_audit(
    limit: int = 20,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 20), 1000))
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not audit_trace_table_exists(conn):
            return []
        rows = conn.execute(
            """
            SELECT * FROM audit_trace
            ORDER BY run_at DESC, trace_id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        return [_serialize_audit_row(r) for r in rows]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# experience_log (harvest from audit_trace — never mutate audit rows)
# ---------------------------------------------------------------------------


def experience_log_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (EXPERIENCE_LOG_TABLE,),
    ).fetchone()
    return bool(row)


# ---------------------------------------------------------------------------
# THE OUTCOME OF AN AUDIT ROW — and the CATEGORY ERROR this fixes
# ---------------------------------------------------------------------------
# MEASURED (2026-09-25, `plan_EXPERIENCE.HARVEST.CATEGORY.ERROR`):
#
#   `harvest_experience_from_audit(only_failures=True)` selected
#       WHERE a.verdict = 0
#   and `verdict = 0` does NOT mean "the system failed". It means the VALIDATOR
#   ANSWERED NO — which is the CORRECT answer for a NEGATIVE test case.
#
#   MEASURED, joining the 158 harvested rows to `audit_trace.notes`:
#       tdd_result=FAIL  (the validator gave the WRONG answer) : 0
#       tdd_result=PASS  (the validator CORRECTLY rejected)    : 158
#   and the 4 rows that ARE real defects were NOT harvested.
#
#   So the library held 158 SUCCESSES labelled as failures, and the only real
#   failures were absent. Any factor "improved" from it would be taught from the
#   wrong rows.
#
# THE FIX IS A READ, NOT A JUDGEMENT. `audit_trace.notes` already carries
# `tdd_result=PASS|FAIL` and `expected_ok=True|False` — the distinction is a
# STORED value. The harvest simply did not read it.
#
# THE THREE OUTCOMES, and why `correct_rejection` is KEPT rather than dropped:
#   real_defect       the validator gave the WRONG answer  -> the lesson source
#   correct_rejection the validator CORRECTLY said NO      -> POSITIVE evidence
#                     that the gate works; deleting it would destroy the record
#                     that the gate is doing its job
#   unknown           no `tdd_result` in notes             -> NOT a pass, NOT a
#                     defect; an admitted gap
OUTCOME_REAL_DEFECT = "real_defect"
OUTCOME_CORRECT_REJECTION = "correct_rejection"
OUTCOME_UNKNOWN = "unknown"

_TDD_RESULT_RE = re.compile(r"tdd_result\s*=\s*(PASS|FAIL)", re.IGNORECASE)


def audit_outcome(notes: Any, verdict: Any) -> str:
    """The OUTCOME of one audit row, from a STORED value. Never a judgement.

    `tdd_result` is the test harness's own verdict on whether the validator
    answered correctly, so it is the authority — NOT `verdict`, which only says
    what the validator answered.

    A row with no `tdd_result` is `unknown`: it is neither a pass nor a defect,
    and calling it either would be inventing the fact this function exists to
    read.
    """
    m = _TDD_RESULT_RE.search(str(notes or ""))
    if not m:
        return OUTCOME_UNKNOWN
    return (OUTCOME_REAL_DEFECT if m.group(1).upper() == "FAIL"
            else OUTCOME_CORRECT_REJECTION)


def ensure_experience_outcome_column(conn: sqlite3.Connection) -> bool:
    """Add `experience_log.outcome` if absent. ADDITIVE, idempotent.

    Returns True when the column was ADDED on this call, so a backfill can run
    exactly once (a migration runs once; re-asserting it every run would silently
    restore a value a human had corrected).
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)"
                                       % EXPERIENCE_LOG_TABLE)}
    if "outcome" in cols:
        return False
    conn.execute("ALTER TABLE %s ADD COLUMN outcome TEXT NOT NULL DEFAULT '%s'"
                 % (EXPERIENCE_LOG_TABLE, OUTCOME_UNKNOWN))
    conn.commit()
    return True


def backfill_experience_outcome(conn: sqlite3.Connection) -> dict[str, Any]:
    """Set `outcome` on rows that still say `unknown`, from the STORED notes.

    WHY THIS IS NEEDED AND WHY IT IS NOT A GUESS
    --------------------------------------------
    MEASURED (2026-09-25): the 158 rows harvested before the `outcome` column
    existed all default to `unknown`. Their `audit_trace.notes` ALREADY carries
    `tdd_result=PASS`, so the correct value is a READ of a stored column, not a
    judgement. Leaving them `unknown` would keep 158 correct rejections looking
    like an admitted gap.

    ONLY rows whose outcome is `unknown` are touched, so a value a human set is
    never overwritten. The update is idempotent: a second run changes 0 rows.
    """
    ensure_experience_outcome_column(conn)
    rows = conn.execute(
        "SELECT e.log_id, a.notes FROM %s e "
        " JOIN audit_trace a ON a.trace_id = e.audit_trace_id "
        " WHERE e.outcome = ?" % EXPERIENCE_LOG_TABLE,
        (OUTCOME_UNKNOWN,)).fetchall()
    by_outcome: dict[str, int] = {}
    for r in rows:
        outcome = audit_outcome(r["notes"], None)
        if outcome == OUTCOME_UNKNOWN:
            continue
        conn.execute("UPDATE %s SET outcome=? WHERE log_id=?"
                     % EXPERIENCE_LOG_TABLE, (outcome, r["log_id"]))
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
    conn.commit()
    return {"ok": True, "examined": len(rows), "updated": sum(by_outcome.values()),
            "by_outcome": by_outcome}


def _classify_error_type(errors: Any, verdict: bool) -> str:
    """Derive a coarse error_type label from audit errors list."""
    if verdict:
        return "pass"
    if not errors:
        return "unknown_fail"
    if isinstance(errors, str):
        text = errors.lower()
    elif isinstance(errors, list):
        text = " ".join(str(x) for x in errors).lower()
    else:
        text = str(errors).lower()
    # Order matters: more specific first
    rules = (
        ("unknown top-level", "payload_unknown_key"),
        ("payload:", "payload"),
        ("channel:", "channel"),
        ("module:", "module"),
        ("capability:", "capability"),
        ("function:", "function"),
        ("api:", "api"),
        ("table:", "table"),
        ("field:", "field"),
        ("ontology:", "ontology"),
        ("task:", "task"),
    )
    for needle, label in rules:
        if needle in text:
            return label
    return "validation_fail"


def _serialize_experience_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    return {
        "log_id": d.get("log_id"),
        "audit_trace_id": d.get("audit_trace_id"),
        "test_case_ref": d.get("test_case_ref"),
        "error_type": d.get("error_type"),
        "error_message": d.get("error_message"),
        "input_snapshot": _parse_json_field(d.get("input_snapshot"), {}),
        "harvested_at": d.get("harvested_at"),
    }


def harvest_experience_from_audit(
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    only_failures: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """
    READ audit_trace and INSERT new experience_log rows for unharvested audits.
    Never UPDATE/DELETE audit_trace. CALL FROM CLI/harvest script only —
    never from validate_new_task.

    `only_failures=True` NOW MEANS "rows where the VALIDATOR WAS WRONG", not
    "rows where the validator answered NO". MEASURED (2026-09-25): the old
    `WHERE a.verdict = 0` harvested 158 rows that were the validator CORRECTLY
    rejecting negative cases, and dropped the 4 rows that were real defects. See
    `audit_outcome()` for the full measurement.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not audit_trace_table_exists(conn):
            return {"ok": False, "error": "audit_trace missing", "inserted": 0}
        if not experience_log_table_exists(conn):
            return {
                "ok": False,
                "error": "experience_log missing after ensure",
                "inserted": 0,
            }
        added_outcome_col = ensure_experience_outcome_column(conn)

        # Only harvest audits not already present in experience_log
        sql = """
            SELECT a.*
            FROM audit_trace a
            WHERE NOT EXISTS (
                SELECT 1 FROM experience_log e
                WHERE e.audit_trace_id = a.trace_id
            )
        """
        params: list[Any] = []
        # ---- THE SELECTION IS ON A STORED VALUE, NOT ON `verdict` ----------
        #
        # MEASURED (2026-09-25): `verdict = 0` means the validator ANSWERED NO,
        # which is CORRECT for a negative case. Selecting on it harvested 158
        # correct rejections as "failures" and dropped the 4 real defects.
        #
        # `notes` carries `tdd_result=PASS|FAIL` — the harness's own verdict on
        # whether the validator answered CORRECTLY. That is the authority, and it
        # is a stored value, so this is a READ rather than a judgement.
        #
        # A row with no `tdd_result` is NOT selected by `only_failures`: it is
        # `unknown`, and harvesting it as a defect would invent the fact.
        if only_failures:
            sql += " AND a.notes LIKE '%tdd_result=FAIL%'"
        sql += " ORDER BY a.run_at ASC, a.trace_id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(1, int(limit)))

        rows = conn.execute(sql, tuple(params)).fetchall()
        inserted = 0
        log_ids: list[str] = []
        by_outcome: dict[str, int] = {}
        for r in rows:
            ad = _serialize_audit_row(r)
            errors = ad.get("errors") or []
            if isinstance(errors, list):
                err_msg = "; ".join(str(x) for x in errors) if errors else ""
            else:
                err_msg = str(errors)
            err_type = _classify_error_type(errors, bool(ad.get("verdict")))
            outcome = audit_outcome(ad.get("notes"), ad.get("verdict"))
            by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
            log_id = f"exp_{uuid.uuid4().hex[:12]}"
            snap = ad.get("input_payload")
            if snap is None:
                snap = {}
            snap_json = (
                snap
                if isinstance(snap, str)
                else json.dumps(snap, ensure_ascii=False, default=str)
            )
            conn.execute(
                """
                INSERT INTO experience_log (
                    log_id, audit_trace_id, test_case_ref, error_type,
                    error_message, input_snapshot, outcome, harvested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    log_id,
                    ad.get("trace_id"),
                    ad.get("test_case_ref"),
                    err_type,
                    err_msg or None,
                    snap_json,
                    outcome,
                ),
            )
            inserted += 1
            log_ids.append(log_id)

        if own:
            conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM experience_log").fetchone()[0]
        return {
            "ok": True,
            "inserted": inserted,
            "log_ids": log_ids,
            "total_experience": int(total),
            "only_failures": only_failures,
            "outcome_column_added": added_outcome_col,
            "by_outcome": by_outcome,
            "note": "audit_trace read-only; no audit rows modified",
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "inserted": 0}
    finally:
        if own:
            conn.close()


def list_latest_experience(
    limit: int = 20,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or 20), 1000))
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not experience_log_table_exists(conn):
            return []
        rows = conn.execute(
            """
            SELECT * FROM experience_log
            ORDER BY harvested_at DESC, log_id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
        return [_serialize_experience_row(r) for r in rows]
    finally:
        if own:
            conn.close()


def list_experience_by_error_type(
    error_type: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    et = _norm(error_type)
    if not et:
        return []
    lim = max(1, min(int(limit or 100), 1000))
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not experience_log_table_exists(conn):
            return []
        rows = conn.execute(
            """
            SELECT * FROM experience_log
            WHERE error_type = ?
            ORDER BY harvested_at DESC, log_id DESC
            LIMIT ?
            """,
            (et, lim),
        ).fetchall()
        return [_serialize_experience_row(r) for r in rows]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# validate_task_queue (kicker queue - never from validate_new_task)
# Distinct from legacy init_db.sql task_queue (fault hub INTEGER id).
# ---------------------------------------------------------------------------


def task_queue_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TASK_QUEUE_TABLE,),
    ).fetchone()
    return bool(row)


def _serialize_task_queue_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    return {
        "task_id": d.get("task_id"),
        "payload": _parse_json_field(d.get("payload"), {}),
        "status": d.get("status"),
        "priority": int(d.get("priority") if d.get("priority") is not None else 100),
        "created_at": d.get("created_at"),
        "started_at": d.get("started_at"),
        "finished_at": d.get("finished_at"),
        "verdict": d.get("verdict"),
        "trace_id": d.get("trace_id"),
    }


def enqueue_task(
    payload: Any,
    priority: int = 100,
    *,
    task_id: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Insert one pending validate_task_queue row.
    CALL FROM KICKER/CLI/TDD ONLY — never from validate_new_task.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not task_queue_table_exists(conn):
            return {"ok": False, "error": f"{TASK_QUEUE_TABLE} missing after ensure"}

        tid = _norm(task_id) or f"tq_{uuid.uuid4().hex[:12]}"
        try:
            pri = int(priority)
        except (TypeError, ValueError):
            pri = 100

        if payload is None:
            payload = {}
        if not isinstance(payload, (dict, list, str)):
            payload = {"raw": str(payload)}
        payload_json = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, default=str)
        )

        conn.execute(
            f"""
            INSERT INTO {TASK_QUEUE_TABLE} (
                task_id, payload, status, priority, created_at,
                started_at, finished_at, verdict, trace_id
            ) VALUES (?, ?, ?, ?, datetime('now'), NULL, NULL, NULL, NULL)
            """,
            (tid, payload_json, TASK_QUEUE_STATUS_PENDING, pri),
        )
        if own:
            conn.commit()
        row = conn.execute(
            f"SELECT * FROM {TASK_QUEUE_TABLE} WHERE task_id = ?", (tid,)
        ).fetchone()
        return {
            "ok": True,
            "task_id": tid,
            "row": _serialize_task_queue_row(row) if row else None,
        }
    except sqlite3.IntegrityError as e:
        return {"ok": False, "error": f"enqueue blocked: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def poll_next_task(
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """
    Atomically claim the next pending task (priority ASC, created_at ASC).
    Sets status=running and started_at. Returns serialized row or None.
    CALL FROM KICKER/CLI/TDD ONLY — never from validate_new_task.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not task_queue_table_exists(conn):
            return None

        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"""
            SELECT * FROM {TASK_QUEUE_TABLE}
            WHERE status = ?
            ORDER BY priority ASC, created_at ASC, task_id ASC
            LIMIT 1
            """,
            (TASK_QUEUE_STATUS_PENDING,),
        ).fetchone()
        if not row:
            conn.execute("COMMIT")
            return None

        tid = str(row["task_id"])
        conn.execute(
            f"""
            UPDATE {TASK_QUEUE_TABLE}
            SET status = ?, started_at = datetime('now')
            WHERE task_id = ? AND status = ?
            """,
            (TASK_QUEUE_STATUS_RUNNING, tid, TASK_QUEUE_STATUS_PENDING),
        )
        claimed = conn.execute(
            f"SELECT * FROM {TASK_QUEUE_TABLE} WHERE task_id = ?", (tid,)
        ).fetchone()
        conn.execute("COMMIT")
        if not claimed or str(claimed["status"]) != TASK_QUEUE_STATUS_RUNNING:
            return None
        return _serialize_task_queue_row(claimed)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return None
    finally:
        if own:
            conn.close()


def update_task_status(
    task_id: str,
    status: str,
    verdict: str | None = None,
    trace_id: str | None = None,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Update queue row status / verdict / trace_id.
    Sets finished_at when status is done or failed.
    CALL FROM KICKER/CLI/TDD ONLY — never from validate_new_task.
    """
    tid = _norm(task_id)
    st = _norm(status).lower()
    if not tid:
        return {"ok": False, "error": "task_id required"}
    allowed = {
        TASK_QUEUE_STATUS_PENDING,
        TASK_QUEUE_STATUS_RUNNING,
        TASK_QUEUE_STATUS_DONE,
        TASK_QUEUE_STATUS_FAILED,
    }
    if st not in allowed:
        return {"ok": False, "error": f"invalid status: {status!r}"}

    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not task_queue_table_exists(conn):
            return {"ok": False, "error": f"{TASK_QUEUE_TABLE} missing after ensure"}

        existing = conn.execute(
            f"SELECT * FROM {TASK_QUEUE_TABLE} WHERE task_id = ?", (tid,)
        ).fetchone()
        if not existing:
            return {"ok": False, "error": f"task not found: {tid}"}

        verd = _norm(verdict) if verdict is not None else None
        if verd == "":
            verd = None
        if verd is not None:
            vu = verd.upper()
            if vu in ("PASS", "FAIL", "TRUE", "FALSE", "1", "0", "YES", "NO"):
                if vu in ("PASS", "TRUE", "1", "YES"):
                    verd = "PASS"
                elif vu in ("FAIL", "FALSE", "0", "NO"):
                    verd = "FAIL"
                else:
                    verd = vu
            else:
                verd = verd  # keep free-form if caller needs it

        tr = _norm(trace_id) if trace_id is not None else None
        if tr == "":
            tr = None

        # Preserve existing verdict/trace when caller passes None explicitly as omit:
        # signature uses None = leave unchanged for verdict/trace_id only when
        # kwargs not provided — here None means clear only if we want update of status only.
        # Convention: None = do not change column; pass "" to clear is not supported for verdict.
        new_verdict = existing["verdict"] if verdict is None else verd
        new_trace = existing["trace_id"] if trace_id is None else tr

        if st in TASK_QUEUE_TERMINAL_STATUSES:
            conn.execute(
                f"""
                UPDATE {TASK_QUEUE_TABLE}
                SET status = ?, verdict = ?, trace_id = ?, finished_at = datetime('now')
                WHERE task_id = ?
                """,
                (st, new_verdict, new_trace, tid),
            )
        else:
            conn.execute(
                f"""
                UPDATE {TASK_QUEUE_TABLE}
                SET status = ?, verdict = ?, trace_id = ?
                WHERE task_id = ?
                """,
                (st, new_verdict, new_trace, tid),
            )
        if own:
            conn.commit()
        row = conn.execute(
            f"SELECT * FROM {TASK_QUEUE_TABLE} WHERE task_id = ?", (tid,)
        ).fetchone()
        return {
            "ok": True,
            "task_id": tid,
            "row": _serialize_task_queue_row(row) if row else None,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def get_task_queue_item(
    task_id: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    tid = _norm(task_id)
    if not tid:
        return None
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not task_queue_table_exists(conn):
            return None
        row = conn.execute(
            f"SELECT * FROM {TASK_QUEUE_TABLE} WHERE task_id = ?", (tid,)
        ).fetchone()
        return _serialize_task_queue_row(row) if row else None
    finally:
        if own:
            conn.close()


def seed_task_queue_test_cases(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Upsert tc.4.0–tc.4.2 task_queue cases into test_case_registry.
    CALL FROM MIGRATE/CLI ONLY — never from validate_new_task.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        ensure_ontology_registry_schema(conn)
        if not test_case_table_exists(conn):
            return {"ok": False, "error": "test_case_registry missing after ensure"}
        now = conn.execute("SELECT datetime('now')").fetchone()[0]
        canon_payload = {
            "task": "T-SKILL01 — Register Task Format Validator Skill",
            "channel": "local_pc",
            "module": "task_center",
            "capability": "task_center.validate_new_task",
            "api": "POST /api/tasks/validate",
            "function": "task_center.validate_new_task",
            "table": "code_registry",
            "field": "register_id, module_name, function_name, file_path",
        }
        cases = [
            {
                "case_ref_tag": "tc.4.0",
                "case_title": "enqueue — pending row with payload and priority",
                "input_payload": {
                    "suite": "task_queue",
                    "action": "enqueue",
                    "payload": canon_payload,
                    "priority": 10,
                    "assert": {
                        "status": "pending",
                        "priority": 10,
                    },
                },
                "expected_ok": True,
                "expected_errors": [],
            },
            {
                "case_ref_tag": "tc.4.1",
                "case_title": "poll — claim pending to running",
                "input_payload": {
                    "suite": "task_queue",
                    "action": "poll_claim",
                    "payload": {
                        **canon_payload,
                        "task": "T-QUEUE-POLL — task_queue poll claim",
                    },
                    "priority": 5,
                    "assert": {
                        "status_after_poll": "running",
                        "second_poll_empty": True,
                    },
                },
                "expected_ok": True,
                "expected_errors": [],
            },
            {
                "case_ref_tag": "tc.4.2",
                "case_title": "update_status — done + verdict + trace_id link",
                "input_payload": {
                    "suite": "task_queue",
                    "action": "update_status",
                    "payload": {
                        **canon_payload,
                        "task": "T-QUEUE-STATUS — task_queue status update",
                    },
                    "priority": 20,
                    "status": "done",
                    "verdict": "PASS",
                    "trace_id": "atr_tq_tdd_link",
                    "assert": {
                        "status": "done",
                        "verdict": "PASS",
                        "trace_id": "atr_tq_tdd_link",
                        "finished_at_set": True,
                    },
                },
                "expected_ok": True,
                "expected_errors": [],
            },
        ]
        upserted = 0
        for c in cases:
            conn.execute(
                """
                INSERT INTO test_case_registry (
                    case_ref_tag, case_title, input_payload, expected_ok,
                    expected_errors, is_active, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, '1', ?, ?)
                ON CONFLICT(case_ref_tag) DO UPDATE SET
                    case_title = excluded.case_title,
                    input_payload = excluded.input_payload,
                    expected_ok = excluded.expected_ok,
                    expected_errors = excluded.expected_errors,
                    is_active = 1,
                    version = excluded.version,
                    updated_at = excluded.updated_at
                """,
                (
                    c["case_ref_tag"],
                    c["case_title"],
                    json.dumps(c["input_payload"], ensure_ascii=False),
                    1 if c["expected_ok"] else 0,
                    json.dumps(c["expected_errors"], ensure_ascii=False),
                    now,
                    now,
                ),
            )
            upserted += 1
        if own:
            conn.commit()
        active = get_all_active_test_cases(conn=conn)
        tags = [x["case_ref_tag"] for x in active]
        missing = [t for t in REQUIRED_TASK_QUEUE_TEST_TAGS if t not in tags]
        return {
            "ok": len(missing) == 0,
            "upserted": upserted,
            "active_count": len(active),
            "tags": tags,
            "missing_required": missing,
            "seed": "task_queue_unit_3",
            "table": TASK_QUEUE_TABLE,
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 
# ---------------------------------------------------------------------------
# Read APIs
# ---------------------------------------------------------------------------


def get_all_registered_capabilities(
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    active_only: bool = True,
) -> list[str]:
    """Active capability_key list for validate_new_task allowlist."""
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not registry_tables_exist(conn):
            return []
        if active_only:
            rows = conn.execute(
                """
                SELECT capability_key FROM capability_registry
                WHERE is_active = 1
                ORDER BY capability_key
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT capability_key FROM capability_registry
                ORDER BY capability_key
                """
            ).fetchall()
        return [str(r["capability_key"]) for r in rows]
    finally:
        if own:
            conn.close()


def get_capability_full_spec(
    capability_key: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """
    Full related registration for one capability_key:
    capability + module + channel + functions[] + apis[] + db_tables[] + db_fields[].
    """
    key = _norm(capability_key)
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        if not registry_tables_exist(conn):
            return {"ok": False, "error": "ontology registry not migrated", "capability_key": key}
        if not key:
            return {"ok": False, "error": "capability_key required"}
        row = conn.execute(
            """
            SELECT
                c.capability_id, c.capability_key, c.name AS capability_name,
                c.description AS capability_description, c.is_active AS capability_active,
                c.version AS capability_version, c.module_id,
                m.module_key, m.name AS module_name, m.is_active AS module_active,
                m.channel_id,
                ch.channel_key, ch.name AS channel_name, ch.is_active AS channel_active
            FROM capability_registry c
            JOIN module_registry m ON m.module_id = c.module_id
            JOIN channel_registry ch ON ch.channel_id = m.channel_id
            WHERE c.capability_key = ?
            """,
            (key,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"capability not found: {key}", "capability_key": key}
        cap_id = int(row["capability_id"])
        functions = [
            dict(r)
            for r in conn.execute(
                """
                SELECT function_id, function_key, name, description, code_registry_id,
                       file_path, is_active, version
                FROM function_registry
                WHERE capability_id = ?
                ORDER BY function_key
                """,
                (cap_id,),
            ).fetchall()
        ]
        apis = [
            dict(r)
            for r in conn.execute(
                """
                SELECT api_id, api_key, name, description, method, path, is_active, version
                FROM api_registry
                WHERE capability_id = ?
                ORDER BY api_key
                """,
                (cap_id,),
            ).fetchall()
        ]
        tables = [
            dict(r)
            for r in conn.execute(
                """
                SELECT db_table_id, table_key, name, description, is_active, version
                FROM db_table_registry
                WHERE is_active = 1
                ORDER BY table_key
                """
            ).fetchall()
        ]
        fields = [
            dict(r)
            for r in conn.execute(
                """
                SELECT f.db_field_id, f.field_key, f.name, f.description, f.is_active,
                       f.version, f.db_table_id, t.table_key
                FROM db_field_registry f
                JOIN db_table_registry t ON t.db_table_id = f.db_table_id
                WHERE f.is_active = 1 AND t.is_active = 1
                ORDER BY t.table_key, f.field_key
                """
            ).fetchall()
        ]
        return {
            "ok": True,
            "capability_key": key,
            "capability": {
                "capability_id": cap_id,
                "capability_key": row["capability_key"],
                "name": row["capability_name"],
                "description": row["capability_description"],
                "is_active": row["capability_active"],
                "version": row["capability_version"],
                "module_id": row["module_id"],
            },
            "module": {
                "module_id": row["module_id"],
                "module_key": row["module_key"],
                "name": row["module_name"],
                "is_active": row["module_active"],
                "channel_id": row["channel_id"],
            },
            "channel": {
                "channel_id": row["channel_id"],
                "channel_key": row["channel_key"],
                "name": row["channel_name"],
                "is_active": row["channel_active"],
            },
            "functions": functions,
            "apis": apis,
            "db_tables": tables,
            "db_fields": fields,
        }
    finally:
        if own:
            conn.close()


def get_active_channel(
    channel_key: str, conn: sqlite3.Connection
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM channel_registry
        WHERE channel_key = ? AND is_active = 1
        """,
        (_norm(channel_key),),
    ).fetchone()
    return _row(row)


def get_active_module(
    module_key: str, conn: sqlite3.Connection
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT m.*, ch.channel_key
        FROM module_registry m
        JOIN channel_registry ch ON ch.channel_id = m.channel_id
        WHERE m.module_key = ? AND m.is_active = 1
        """,
        (_norm(module_key),),
    ).fetchone()
    return _row(row)


def get_active_capability(
    capability_key: str, conn: sqlite3.Connection
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT c.*, m.module_key, m.is_active AS module_is_active
        FROM capability_registry c
        JOIN module_registry m ON m.module_id = c.module_id
        WHERE c.capability_key = ? AND c.is_active = 1
        """,
        (_norm(capability_key),),
    ).fetchone()
    return _row(row)


def function_belongs_to_capability(
    function_key: str,
    capability_key: str,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT f.*
        FROM function_registry f
        JOIN capability_registry c ON c.capability_id = f.capability_id
        WHERE f.function_key = ?
          AND c.capability_key = ?
          AND f.is_active = 1
          AND c.is_active = 1
        """,
        (_norm(function_key), _norm(capability_key)),
    ).fetchone()
    return _row(row)


def _normalize_api_key(api: str) -> str:
    s = _norm(api)
    # collapse internal whitespace
    return re.sub(r"\s+", " ", s)


def api_belongs_to_capability(
    api_key: str,
    capability_key: str,
    conn: sqlite3.Connection,
) -> dict[str, Any] | None:
    want = _normalize_api_key(api_key)
    if want.lower() in ("none", "n/a", "na", "-"):
        # literal none allowed if registered under capability or globally as api_key none
        row = conn.execute(
            """
            SELECT a.*
            FROM api_registry a
            JOIN capability_registry c ON c.capability_id = a.capability_id
            WHERE lower(a.api_key) IN ('none', 'n/a', 'na')
              AND c.capability_key = ?
              AND a.is_active = 1 AND c.is_active = 1
            """,
            (_norm(capability_key),),
        ).fetchone()
        return _row(row)
    row = conn.execute(
        """
        SELECT a.*
        FROM api_registry a
        JOIN capability_registry c ON c.capability_id = a.capability_id
        WHERE c.capability_key = ?
          AND a.is_active = 1 AND c.is_active = 1
          AND (
                a.api_key = ?
             OR lower(a.api_key) = lower(?)
             OR (a.method IS NOT NULL AND a.path IS NOT NULL
                 AND lower(trim(a.method) || ' ' || trim(a.path)) = lower(?))
          )
        """,
        (_norm(capability_key), want, want, want),
    ).fetchone()
    return _row(row)


def get_active_db_table(
    table_key: str, conn: sqlite3.Connection
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM db_table_registry
        WHERE table_key = ? AND is_active = 1
        """,
        (_norm(table_key),),
    ).fetchone()
    return _row(row)


def fields_belong_to_table(
    table_key: str,
    field_keys: list[str],
    conn: sqlite3.Connection,
) -> tuple[bool, list[str]]:
    """Return (ok, missing_field_keys)."""
    wanted = [_norm(f) for f in field_keys if _norm(f)]
    if not wanted:
        return False, []
    tbl = get_active_db_table(table_key, conn)
    if not tbl:
        return False, wanted
    rows = conn.execute(
        """
        SELECT field_key FROM db_field_registry
        WHERE db_table_id = ? AND is_active = 1
        """,
        (tbl["db_table_id"],),
    ).fetchall()
    have = {str(r["field_key"]) for r in rows}
    missing = [f for f in wanted if f not in have]
    return (len(missing) == 0, missing)


if __name__ == "__main__":
    import sys

    cmd = (sys.argv[1] if len(sys.argv) > 1 else "selftest").lower()
    if cmd in ("migrate", "ensure", "seed"):
        r = seed_ontology_registry_defaults()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    elif cmd in ("seed-tests", "seed_tests"):
        seed_ontology_registry_defaults()
        r = seed_validate_new_task_test_cases()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    elif cmd in ("seed-revision-tests", "seed_revision_tests"):
        seed_ontology_registry_defaults()
        r = seed_ontology_revision_test_cases()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    elif cmd in ("list-tests", "list_tests"):
        print(json.dumps(get_all_active_test_cases(), ensure_ascii=False, indent=2, default=str))
    elif cmd == "list":
        print(json.dumps(get_all_registered_capabilities(), ensure_ascii=False, indent=2))
    elif cmd == "spec":
        key = sys.argv[2] if len(sys.argv) > 2 else "task_center.validate_new_task"
        print(json.dumps(get_capability_full_spec(key), ensure_ascii=False, indent=2, default=str))
    else:
        seed_ontology_registry_defaults()
        caps = get_all_registered_capabilities()
        spec = get_capability_full_spec("task_center.validate_new_task")
        tcs = get_all_active_test_cases()
        print(
            json.dumps(
                {
                    "caps_n": len(caps),
                    "has_validate": "task_center.validate_new_task" in caps,
                    "spec_ok": spec.get("ok"),
                    "test_cases_n": len(tcs),
                    "test_tags": [c.get("case_ref_tag") for c in tcs],
                    "revision_tags": [
                        t for t in REQUIRED_REVISION_TEST_TAGS if t in [c.get("case_ref_tag") for c in tcs]
                    ],
                },
                indent=2,
            )
        )
