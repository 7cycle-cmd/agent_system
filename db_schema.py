"""DB schema helpers — safe additive migrations for existing agent.db.

Phase 0–1: fault_event ID hub + option/SSOT catalog (no DROP of live data).
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "agent.db")
DEFAULT_BROWSER_PORT = 8766

# ---------------------------------------------------------------------------
# Settings table (key/value store for runtime configuration)
# ---------------------------------------------------------------------------
SETTINGS_DDL = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def get_setting(
    conn: sqlite3.Connection,
    key: str,
    default: Any | None = None,
) -> Any | None:
    """Read a single setting value from the settings table.

    Returns `default` if the key is missing or the table does not exist.
    """
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    except sqlite3.Error:
        return default
    if row is None:
        return default
    return row[0]


# ---------------------------------------------------------------------------
# Core DDL (CREATE IF NOT EXISTS) — greenfield twin lives in init_db.sql
# ---------------------------------------------------------------------------

FAULT_ANALYSIS_DDL = """
CREATE TABLE IF NOT EXISTS fault_analysis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL UNIQUE,
    model           TEXT,
    summary         TEXT,
    detail_json     TEXT,
    evidence_used   TEXT    NOT NULL DEFAULT 'none'
                    CHECK (evidence_used IN ('frozen', 'live', 'none')),
    evidence_path   TEXT,
    error           TEXT,
    notified_at     TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    vision_id       INTEGER,
    option_id       INTEGER,
    solution_id     INTEGER,
    match_score     REAL,
    match_status    TEXT,
    ssot_prompt_json TEXT,
    solution_summary TEXT,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE CASCADE
);
"""

CHANNEL_DDL = """
CREATE TABLE IF NOT EXISTS channel (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

MODULE_DDL = """
CREATE TABLE IF NOT EXISTS module (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

VISION_ASSET_DDL = """
CREATE TABLE IF NOT EXISTS vision_asset (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL DEFAULT 'other'
                 CHECK (kind IN ('frozen', 'live', 'other')),
    path_or_url  TEXT    NOT NULL,
    sha256       TEXT,
    source       TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

FAULT_OPTION_DDL = """
CREATE TABLE IF NOT EXISTS fault_option (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    channel_id  INTEGER,
    module_id   INTEGER,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'draft', 'deprecated')),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (channel_id) REFERENCES channel (id) ON DELETE SET NULL,
    FOREIGN KEY (module_id)  REFERENCES module  (id) ON DELETE SET NULL
);
"""

FAULT_SSOT_DDL = """
CREATE TABLE IF NOT EXISTS fault_ssot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER NOT NULL,
    keyword     TEXT    NOT NULL,
    value_text  TEXT,
    value_type  TEXT    NOT NULL DEFAULT 'string',
    weight      REAL    NOT NULL DEFAULT 1.0,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (option_id, keyword),
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE CASCADE
);
"""

FAULT_SOLUTION_DDL = """
CREATE TABLE IF NOT EXISTS fault_solution (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER NOT NULL,
    title       TEXT    NOT NULL,
    steps_text  TEXT,
    steps_json  TEXT,
    priority    INTEGER NOT NULL DEFAULT 100,
    status      TEXT    NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'draft', 'deprecated')),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE CASCADE
);
"""

FAULT_EVENT_FACT_DDL = """
CREATE TABLE IF NOT EXISTS fault_event_fact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL,
    keyword     TEXT    NOT NULL,
    value_text  TEXT,
    value_type  TEXT    NOT NULL DEFAULT 'string',
    source      TEXT    NOT NULL DEFAULT 'watchdog',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE CASCADE
);
"""

FAULT_OPTION_PENDING_DDL = """
CREATE TABLE IF NOT EXISTS fault_option_pending (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER NOT NULL,
    analysis_id     INTEGER,
    proposed_code   TEXT,
    proposed_name   TEXT,
    reason          TEXT,
    vision_summary  TEXT,
    status          TEXT    NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'accepted', 'rejected')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE CASCADE,
    FOREIGN KEY (analysis_id) REFERENCES fault_analysis (id) ON DELETE SET NULL
);
"""

FAULT_SSOT_REVISION_DDL = """
CREATE TABLE IF NOT EXISTS fault_ssot_revision (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    option_id   INTEGER,
    event_id    INTEGER,
    action      TEXT    NOT NULL
                CHECK (action IN ('add', 'update', 'delete')),
    keyword     TEXT    NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    status      TEXT    NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'applied', 'rejected')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    applied_at  TIMESTAMP,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE SET NULL,
    FOREIGN KEY (event_id)  REFERENCES fault_event  (event_id) ON DELETE SET NULL
);
"""

INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_fault_analysis_created
ON fault_analysis (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fault_event_fact_event
ON fault_event_fact (event_id, keyword);

CREATE INDEX IF NOT EXISTS idx_fault_ssot_option
ON fault_ssot (option_id, keyword);

CREATE INDEX IF NOT EXISTS idx_fault_option_status
ON fault_option (status);

CREATE INDEX IF NOT EXISTS idx_fault_solution_option
ON fault_solution (option_id, priority);

CREATE INDEX IF NOT EXISTS idx_fault_option_pending_status
ON fault_option_pending (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fault_ssot_revision_status
ON fault_ssot_revision (status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_vision_asset_created
ON vision_asset (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fault_event_channel
ON fault_event (channel_id);

CREATE INDEX IF NOT EXISTS idx_fault_event_module
ON fault_event (module_id);

CREATE INDEX IF NOT EXISTS idx_fault_event_option
ON fault_event (option_id);
"""

FAULT_EVENT_NEW_COLUMNS: list[tuple[str, str]] = [
    ("channel_id", "INTEGER"),
    ("module_id", "INTEGER"),
    ("vision_id", "INTEGER"),
    ("fault_analysis_id", "INTEGER"),
    ("task_id", "INTEGER"),
    ("option_id", "INTEGER"),
    ("solution_id", "INTEGER"),
]

FAULT_ANALYSIS_NEW_COLUMNS: list[tuple[str, str]] = [
    ("vision_id", "INTEGER"),
    ("option_id", "INTEGER"),
    ("solution_id", "INTEGER"),
    ("match_score", "REAL"),
    ("match_status", "TEXT"),
    ("ssot_prompt_json", "TEXT"),
    ("solution_summary", "TEXT"),
]


def get_db_path(db_path: str | None = None) -> str:
    return db_path or DB_PATH


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _add_columns_if_missing(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[tuple[str, str]],
) -> list[str]:
    """ALTER TABLE ADD COLUMN for each missing column. Returns added names."""
    if not _table_exists(conn, table):
        return []
    existing = _table_columns(conn, table)
    added: list[str] = []
    for name, decl in columns:
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        added.append(name)
    return added


def ensure_fault_analysis_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: base fault_analysis + hub/SSOT extensions."""
    conn.execute("PRAGMA foreign_keys = ON;")
    ensure_hub_ssot_schema(conn)


TASK_ACTION_NAME_DDL = """
CREATE TABLE IF NOT EXISTS task_action_name (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    element       TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    code          TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    requires_tdd  INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'deprecated')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

TDD_TYPE_DDL = """
CREATE TABLE IF NOT EXISTS tdd_type (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT    NOT NULL UNIQUE,
    name             TEXT    NOT NULL,
    sqlite_affinity  TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

VERSION_CENTER_DDL = """
CREATE TABLE IF NOT EXISTS version_center (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id         INTEGER NOT NULL,
    module_id          INTEGER NOT NULL,
    version_label      TEXT    NOT NULL,
    parent_version_id  INTEGER,
    title              TEXT,
    notes              TEXT,
    status             TEXT    NOT NULL DEFAULT 'active'
                       CHECK (status IN ('draft', 'active', 'released', 'deprecated')),
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (channel_id, module_id, version_label),
    FOREIGN KEY (channel_id) REFERENCES channel (id) ON DELETE CASCADE,
    FOREIGN KEY (module_id)  REFERENCES module  (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_version_id) REFERENCES version_center (id) ON DELETE SET NULL
);
"""

DEV_TASK_DDL = """
CREATE TABLE IF NOT EXISTS dev_task (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_task_id   INTEGER,
    channel_id       INTEGER NOT NULL,
    module_id        INTEGER NOT NULL,
    action_name_id   INTEGER NOT NULL,
    version_id       INTEGER NOT NULL,
    task_label       TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    payload_json     TEXT,
    status           TEXT    NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'pass', 'fail', 'cancelled')),
    queue_task_id    INTEGER,
    qc_vision_id     INTEGER,
    writer           TEXT,
    session_id       TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at     TIMESTAMP,
    UNIQUE (version_id, task_label),
    FOREIGN KEY (parent_task_id) REFERENCES dev_task (id) ON DELETE CASCADE,
    FOREIGN KEY (channel_id) REFERENCES channel (id) ON DELETE CASCADE,
    FOREIGN KEY (module_id) REFERENCES module (id) ON DELETE CASCADE,
    FOREIGN KEY (action_name_id) REFERENCES task_action_name (id) ON DELETE RESTRICT,
    FOREIGN KEY (version_id) REFERENCES version_center (id) ON DELETE CASCADE,
    FOREIGN KEY (queue_task_id) REFERENCES task_queue (id) ON DELETE SET NULL,
    FOREIGN KEY (qc_vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL
);
"""

# Additive columns for existing DBs (CREATE IF NOT EXISTS won't add cols)
DEV_TASK_NEW_COLUMNS = [
    ("writer", "TEXT"),
    ("session_id", "TEXT"),
]

DEV_TASK_FIELD_DDL = """
CREATE TABLE IF NOT EXISTS dev_task_field (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          INTEGER NOT NULL,
    action_name_id   INTEGER,
    tdd_type_id      INTEGER NOT NULL,
    field_name       TEXT    NOT NULL,
    nullable         INTEGER NOT NULL DEFAULT 1,
    is_pk            INTEGER NOT NULL DEFAULT 0,
    default_text     TEXT,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE CASCADE,
    FOREIGN KEY (action_name_id) REFERENCES task_action_name (id) ON DELETE SET NULL,
    FOREIGN KEY (tdd_type_id) REFERENCES tdd_type (id) ON DELETE RESTRICT
);
"""

SCHEMA_SSOT_DDL = """
CREATE TABLE IF NOT EXISTS schema_ssot (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name   TEXT    NOT NULL,
    keyword      TEXT    NOT NULL,
    value_text   TEXT,
    value_type   TEXT    NOT NULL DEFAULT 'string',
    source       TEXT    NOT NULL DEFAULT 'pragma',
    task_id      INTEGER,
    version_id   INTEGER,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (table_name, keyword, source),
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (version_id) REFERENCES version_center (id) ON DELETE SET NULL
);
"""

SCHEMA_QC_RUN_DDL = """
CREATE TABLE IF NOT EXISTS schema_qc_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER,
    table_name      TEXT    NOT NULL,
    browser_url     TEXT,
    api_ok          INTEGER NOT NULL DEFAULT 0,
    vision_id       INTEGER,
    expected_json   TEXT,
    observed_json   TEXT,
    match_ok        INTEGER NOT NULL DEFAULT 0,
    summary         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL
);
"""

# FH2: per-task multi-dim capability / planning SSOT (not schema_ssot, not a gate)
TASK_SSOT_DDL = """
CREATE TABLE IF NOT EXISTS task_ssot (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         INTEGER NOT NULL,
    dim_key         TEXT    NOT NULL,
    value_text      TEXT,
    value_type      TEXT    NOT NULL DEFAULT 'string',
    source          TEXT    NOT NULL DEFAULT 'seed',
    parent_dim_id   INTEGER,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, dim_key),
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE CASCADE,
    FOREIGN KEY (parent_dim_id) REFERENCES task_ssot (id) ON DELETE SET NULL
);
"""

# FH5 — STEP5 fault/task report rollup (never a gate)
FAULT_REPORT_DDL = """
CREATE TABLE IF NOT EXISTS fault_report (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id            INTEGER,
    remediate_task_id   INTEGER,
    qc_task_id          INTEGER,
    table_name          TEXT,
    gate_result         TEXT,
    goal_type           TEXT,
    summary             TEXT,
    markdown_text       TEXT,
    report_json         TEXT    NOT NULL,
    notified_at         TIMESTAMP,
    source              TEXT    NOT NULL DEFAULT 'fail_handling',
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES fault_event (event_id) ON DELETE SET NULL,
    FOREIGN KEY (remediate_task_id) REFERENCES dev_task (id) ON DELETE SET NULL
);
"""

# Pipeline C / CH1 — Code Health function trace (append-only ledger; never a gate)
# WHY: W2, W9 — runtime hits under task_label (TACID); sub_steps on parent only
FUNCTION_INVOKE_TRACE_DDL = """
CREATE TABLE IF NOT EXISTS function_invoke_trace (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               TEXT    NOT NULL,
    tacid            TEXT    NOT NULL,
    task_id          INTEGER,
    module_name      TEXT    NOT NULL,
    function_name    TEXT    NOT NULL,
    ok               INTEGER NOT NULL DEFAULT 0
                     CHECK (ok IN (0, 1)),
    error_text       TEXT,
    duration_ms      INTEGER,
    parent_trace_id  INTEGER,
    sub_steps_json   TEXT    NOT NULL DEFAULT '[]',
    source           TEXT    NOT NULL DEFAULT 'code_health',
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (parent_trace_id) REFERENCES function_invoke_trace (id) ON DELETE SET NULL
);
"""

# Pipeline C / CH1 — function_scoring rollup (UNIQUE module+function; never a gate)
# WHY: W3, W4, W5, W10 — objective score + status cache; hits remain in trace forever
FUNCTION_SCORING_DDL = """
CREATE TABLE IF NOT EXISTS function_scoring (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    module_name          TEXT    NOT NULL,
    function_name        TEXT    NOT NULL,
    total_invocations    INTEGER NOT NULL DEFAULT 0,
    success_count        INTEGER NOT NULL DEFAULT 0,
    fail_count           INTEGER NOT NULL DEFAULT 0,
    functional_score     REAL,
    ref_static_tacids    TEXT    NOT NULL DEFAULT '[]',
    ref_hit_tacids       TEXT    NOT NULL DEFAULT '[]',
    last_hit_tacid       TEXT,
    last_hit_time        TEXT,
    status               TEXT    NOT NULL DEFAULT 'dead_candidate'
                         CHECK (status IN ('active', 'zombie', 'inactive', 'dead_candidate')),
    register_id          TEXT,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (module_name, function_name)
);
"""

# Pipeline D / MCS1 — managed coding register (register_id law; never a gate)
CODE_REGISTER_DDL = """
CREATE TABLE IF NOT EXISTS code_register (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    register_id       TEXT    NOT NULL UNIQUE,
    module_name       TEXT    NOT NULL,
    function_name     TEXT    NOT NULL,
    task_id           INTEGER,
    tacid             TEXT,
    system_task_id    INTEGER,
    slice_task_id     INTEGER,
    system_key        TEXT,
    slice_key         TEXT,
    status            TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'active', 'zombie', 'rubbish', 'deprecated')),
    -- CH7 / MCS9: source location + optional code span (never auto-delete)
    file_path         TEXT,
    line_start        INTEGER,
    line_end          INTEGER,
    code_span         TEXT,
    notes             TEXT,
    source            TEXT    NOT NULL DEFAULT 'managed_coding',
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (module_name, function_name),
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (system_task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (slice_task_id) REFERENCES dev_task (id) ON DELETE SET NULL
);
"""

# MCS8 — Function Builder: human request → research → multi-dim SSOT plan → build
# Flow is DB-driven; never a gate. AI "not have work" = research path.
FN_REQUEST_DDL = """
CREATE TABLE IF NOT EXISTS fn_request (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_key       TEXT    NOT NULL UNIQUE,
    human_text        TEXT    NOT NULL,
    request_kind      TEXT    NOT NULL DEFAULT 'system'
                      CHECK (request_kind IN ('system', 'function', 'field', 'table')),
    channel_code      TEXT    NOT NULL DEFAULT 'local_pc',
    module_code       TEXT    NOT NULL,
    system_key        TEXT,
    desired_table     TEXT,
    desired_output    TEXT,
    status            TEXT    NOT NULL DEFAULT 'received'
                      CHECK (status IN (
                          'received', 'researching', 'researched',
                          'building', 'built', 'failed', 'cancelled'
                      )),
    research_json     TEXT,
    plan_json         TEXT,
    build_json        TEXT,
    root_task_id      INTEGER,
    version_id        INTEGER,
    error_text        TEXT,
    source            TEXT    NOT NULL DEFAULT 'function_builder',
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (root_task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (version_id) REFERENCES version_center (id) ON DELETE SET NULL
);
"""

FN_RESEARCH_DDL = """
CREATE TABLE IF NOT EXISTS fn_research (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        INTEGER NOT NULL,
    path_code         TEXT    NOT NULL
                      CHECK (path_code IN (
                          'reuse_table_fields',
                          'design_table_fields',
                          'reuse_registers',
                          'extend_system',
                          'unknown'
                      )),
    has_table         INTEGER NOT NULL DEFAULT 0,
    table_name        TEXT,
    existing_fields_json TEXT NOT NULL DEFAULT '[]',
    reuse_registers_json TEXT NOT NULL DEFAULT '[]',
    proposed_slices_json TEXT NOT NULL DEFAULT '[]',
    equation          TEXT,
    notes             TEXT,
    multi_dim_ssot_json TEXT NOT NULL DEFAULT '{}',
    source            TEXT    NOT NULL DEFAULT 'function_builder',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES fn_request (id) ON DELETE CASCADE
);
"""

# Pipeline E / Pair QC L1 — dual-path MCP SSOT vs UI (never a gate; detect only)
PAIR_QC_RUN_DDL = """
CREATE TABLE IF NOT EXISTS pair_qc_run (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key           TEXT    NOT NULL UNIQUE,
    register_id       TEXT,
    tdd_rule_id       INTEGER,
    slice_key         TEXT,
    field_name        TEXT    NOT NULL,
    region            TEXT,
    mcp_raw           TEXT,
    mcp_norm          TEXT,
    ui_raw            TEXT,
    ui_norm           TEXT,
    mcp_source        TEXT    NOT NULL DEFAULT 'seed',
    ui_source         TEXT    NOT NULL DEFAULT 'manual',
    mcp_vision_id     INTEGER,
    ui_vision_id      INTEGER,
    match_ok          INTEGER
                      CHECK (match_ok IS NULL OR match_ok IN (0, 1)),
    compare_op        TEXT    NOT NULL DEFAULT 'eq_norm',
    diff_json         TEXT    NOT NULL DEFAULT '{}',
    tdd_check_json    TEXT    NOT NULL DEFAULT '{}',
    status            TEXT    NOT NULL DEFAULT 'pending'
                      CHECK (status IN (
                          'pending', 'pass', 'fail', 'error', 'skipped'
                      )),
    case_id           INTEGER,
    task_id           INTEGER,
    notes             TEXT,
    source            TEXT    NOT NULL DEFAULT 'pair_qc',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tdd_rule_id) REFERENCES field_tdd_rule (id) ON DELETE SET NULL,
    FOREIGN KEY (mcp_vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL,
    FOREIGN KEY (ui_vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL,
    FOREIGN KEY (case_id) REFERENCES fault_event (event_id) ON DELETE SET NULL,
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL
);
"""

# L1 pair_qc_run required columns (legacy table used backend_* / result shape)
PAIR_QC_RUN_REQUIRED_COLUMNS = (
    "run_key",
    "field_name",
    "region",
    "mcp_raw",
    "mcp_norm",
    "ui_raw",
    "ui_norm",
    "mcp_source",
    "ui_source",
    "match_ok",
    "compare_op",
    "diff_json",
    "tdd_check_json",
    "status",
    "case_id",
)


def _migrate_pair_qc_run_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild legacy pair_qc_run into L1 dual-path shape when columns diverge.

    CREATE TABLE IF NOT EXISTS cannot alter an old shape. Detect missing L1
    columns and rebuild; best-effort copy of compatible fields.
    """
    info: dict[str, Any] = {"rebuilt": False, "reason": None}
    if not _table_exists(conn, "pair_qc_run"):
        conn.executescript(PAIR_QC_RUN_DDL)
        info["created"] = True
        return info
    cols = _table_columns(conn, "pair_qc_run")
    missing = [c for c in PAIR_QC_RUN_REQUIRED_COLUMNS if c not in cols]
    if not missing:
        info["ok"] = True
        return info
    info["missing"] = missing
    info["rebuilt"] = True
    info["reason"] = "legacy_pair_qc_run_shape"
    # Drop dependent indexes if present, then rebuild table
    for idx in (
        "idx_pair_qc_run_status",
        "idx_pair_qc_run_register",
        "idx_pair_qc_run_case",
    ):
        try:
            conn.execute(f"DROP INDEX IF EXISTS {idx}")
        except sqlite3.Error:
            pass
    conn.execute("ALTER TABLE pair_qc_run RENAME TO pair_qc_run_legacy")
    conn.executescript(PAIR_QC_RUN_DDL)
    # Best-effort copy of shared columns from legacy
    leg_cols = _table_columns(conn, "pair_qc_run_legacy")
    shared = [
        c
        for c in (
            "id",
            "run_key",
            "register_id",
            "tdd_rule_id",
            "slice_key",
            "field_name",
            "task_id",
            "notes",
            "source",
            "created_at",
        )
        if c in leg_cols
    ]
    # Map old backend_* into mcp_* when present
    select_parts: list[str] = []
    insert_cols: list[str] = list(shared)
    for c in shared:
        select_parts.append(c)
    # synthetic mappings
    if "mcp_raw" not in leg_cols and "backend_raw_json" in leg_cols:
        insert_cols.append("mcp_raw")
        select_parts.append("backend_raw_json")
    if "ui_raw" not in leg_cols and "ui_raw_json" in leg_cols:
        insert_cols.append("ui_raw")
        select_parts.append("ui_raw_json")
    if "mcp_norm" not in leg_cols and "backend_norm_json" in leg_cols:
        insert_cols.append("mcp_norm")
        select_parts.append("backend_norm_json")
    if "ui_norm" not in leg_cols and "ui_norm_json" in leg_cols:
        insert_cols.append("ui_norm")
        select_parts.append("ui_norm_json")
    if "status" not in leg_cols and "result" in leg_cols:
        insert_cols.append("status")
        # map pass/fail/error-ish legacy result into status
        select_parts.append(
            "CASE lower(coalesce(result,'')) "
            "WHEN 'pass' THEN 'pass' WHEN 'fail' THEN 'fail' "
            "WHEN 'error' THEN 'error' ELSE 'pending' END"
        )
    if "match_ok" not in leg_cols and "result" in leg_cols:
        insert_cols.append("match_ok")
        select_parts.append(
            "CASE lower(coalesce(result,'')) "
            "WHEN 'pass' THEN 1 WHEN 'fail' THEN 0 ELSE NULL END"
        )
    if "ui_vision_id" not in leg_cols and "vision_id" in leg_cols:
        insert_cols.append("ui_vision_id")
        select_parts.append("vision_id")
    # ensure run_key / field_name NOT NULL survivors
    if "run_key" not in insert_cols:
        insert_cols.append("run_key")
        select_parts.append(
            "'legacy_' || cast(id AS TEXT) || '_' || "
            "printf('%s', coalesce(field_name,'field'))"
        )
    if "field_name" not in insert_cols:
        insert_cols.append("field_name")
        select_parts.append("coalesce(field_name, 'unknown')")
    try:
        sql = (
            f"INSERT INTO pair_qc_run ({', '.join(insert_cols)}) "
            f"SELECT {', '.join(select_parts)} FROM pair_qc_run_legacy"
        )
        conn.execute(sql)
        info["copied_rows"] = conn.execute(
            "SELECT COUNT(*) FROM pair_qc_run"
        ).fetchone()[0]
    except sqlite3.Error as e:
        info["copy_error"] = f"{type(e).__name__}: {e}"
    conn.execute("DROP TABLE IF EXISTS pair_qc_run_legacy")
    return info


PAIR_VALUE_SSOT_DDL = """
CREATE TABLE IF NOT EXISTS pair_value_ssot (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    register_id       TEXT,
    field_name        TEXT    NOT NULL,
    slice_key         TEXT,
    value_raw         TEXT,
    value_norm        TEXT,
    region            TEXT,
    source            TEXT    NOT NULL DEFAULT 'seed'
                      CHECK (source IN ('seed', 'mcp', 'sql', 'api', 'manual')),
    meta_json         TEXT    NOT NULL DEFAULT '{}',
    observed_at       TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Skill Prompt SSOT — versioned LLM prompts (Mouse Spot Helper etc.; never a gate)
SKILL_PROMPT_SSOT_DDL = """
CREATE TABLE IF NOT EXISTS skill_prompt_ssot (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key         TEXT    NOT NULL,
    prompt_key        TEXT    NOT NULL DEFAULT 'main',
    version_label     TEXT    NOT NULL,
    prompt_text       TEXT    NOT NULL,
    hard_rules_json   TEXT    NOT NULL DEFAULT '[]',
    output_schema     TEXT    NOT NULL DEFAULT 'result_yes_no',
    parser            TEXT    NOT NULL DEFAULT 'result_yes_no',
    model_default     TEXT,
    status            TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'testing', 'active', 'deprecated')),
    task_id           INTEGER,
    parent_id         INTEGER,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    source            TEXT    NOT NULL DEFAULT 'skill_prompt',
    notes             TEXT,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (skill_key, prompt_key, version_label),
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL,
    FOREIGN KEY (parent_id) REFERENCES skill_prompt_ssot (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_skill
  ON skill_prompt_ssot (skill_key, status, sort_order, prompt_key);
"""

SKILL_PROMPT_CASE_DDL = """
CREATE TABLE IF NOT EXISTS skill_prompt_case (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    case_key          TEXT    NOT NULL UNIQUE,
    skill_key         TEXT    NOT NULL,
    image_path        TEXT,
    vision_ref        TEXT,
    target_name       TEXT,
    target_action     TEXT,
    expected          TEXT    NOT NULL
                      CHECK (expected IN ('YES', 'NO')),
    expected_reason   TEXT,
    notes             TEXT,
    source            TEXT    NOT NULL DEFAULT 'manual',
    labeler           TEXT,
    status            TEXT    NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'draft', 'deprecated')),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_case_skill
  ON skill_prompt_case (skill_key, status);
"""

SKILL_PROMPT_TEST_RUN_DDL = """
CREATE TABLE IF NOT EXISTS skill_prompt_test_run (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL UNIQUE,
    skill_key         TEXT    NOT NULL,
    version_label     TEXT    NOT NULL,
    prompt_key        TEXT    NOT NULL DEFAULT 'main',
    case_id           INTEGER,
    image_path        TEXT,
    target_name       TEXT,
    target_action     TEXT,
    expected          TEXT,
    n_runs            INTEGER NOT NULL DEFAULT 0,
    yes_count         INTEGER NOT NULL DEFAULT 0,
    no_count          INTEGER NOT NULL DEFAULT 0,
    other_count       INTEGER NOT NULL DEFAULT 0,
    error_count       INTEGER NOT NULL DEFAULT 0,
    yes_pct           REAL,
    no_pct            REAL,
    accuracy_pct      REAL,
    pass_gate         INTEGER NOT NULL DEFAULT 0,
    model             TEXT,
    avg_duration_ms   INTEGER,
    wall_ms           INTEGER,
    raw_json          TEXT    NOT NULL DEFAULT '{}',
    source            TEXT    NOT NULL DEFAULT 'skill_prompt_test',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (case_id) REFERENCES skill_prompt_case (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_test_skill
  ON skill_prompt_test_run (skill_key, created_at DESC);
"""

SKILL_PROMPT_INFERENCE_DDL = """
CREATE TABLE IF NOT EXISTS skill_prompt_inference (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    inference_id      TEXT    NOT NULL UNIQUE,
    skill_key         TEXT    NOT NULL,
    version_label     TEXT,
    prompt_key        TEXT    NOT NULL DEFAULT 'main',
    task_run_id       TEXT,
    target_name       TEXT,
    target_action     TEXT,
    image_path        TEXT,
    raw_response      TEXT,
    parsed_result     TEXT,
    final_result      TEXT,
    reason            TEXT,
    confidence        REAL,
    model             TEXT,
    human_override    TEXT,
    is_wrong          INTEGER NOT NULL DEFAULT 0,
    meta_json         TEXT    NOT NULL DEFAULT '{}',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_inference_skill
  ON skill_prompt_inference (skill_key, created_at DESC);
"""

# Per-field TDD rules (DB-driven; bound to register_id when built)
FIELD_TDD_RULE_DDL = """
CREATE TABLE IF NOT EXISTS field_tdd_rule (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id        INTEGER,
    system_key        TEXT,
    slice_key         TEXT    NOT NULL,
    field_name        TEXT    NOT NULL,
    tdd_type_code     TEXT    NOT NULL DEFAULT 'text',
    rule_json         TEXT    NOT NULL DEFAULT '{}',
    depends_on_json   TEXT    NOT NULL DEFAULT '[]',
    register_id       TEXT,
    task_id           INTEGER,
    status            TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'active', 'rubbish', 'deprecated')),
    notes             TEXT,
    source            TEXT    NOT NULL DEFAULT 'function_builder',
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (request_id) REFERENCES fn_request (id) ON DELETE SET NULL,
    FOREIGN KEY (task_id) REFERENCES dev_task (id) ON DELETE SET NULL
);
"""

# Ontology map (Task 1 index) — semantic monitor only; never a gate
ONTO_CONCEPT_DDL = """
CREATE TABLE IF NOT EXISTS onto_concept (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT    NOT NULL UNIQUE,
    kind          TEXT    NOT NULL
                  CHECK (kind IN (
                      'system', 'channel', 'module', 'function',
                      'slice', 'profile', 'version', 'other'
                  )),
    title         TEXT    NOT NULL,
    status        TEXT    NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'draft', 'rubbish', 'deprecated')),
    notes         TEXT,
    source        TEXT    NOT NULL DEFAULT 'managed_coding',
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

ONTO_LINK_DDL = """
CREATE TABLE IF NOT EXISTS onto_link (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_concept_id INTEGER NOT NULL,
    to_concept_id   INTEGER NOT NULL,
    rel             TEXT    NOT NULL DEFAULT 'contains',
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (from_concept_id, to_concept_id, rel),
    FOREIGN KEY (from_concept_id) REFERENCES onto_concept (id) ON DELETE CASCADE,
    FOREIGN KEY (to_concept_id) REFERENCES onto_concept (id) ON DELETE CASCADE
);
"""

ONTO_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS onto_binding (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id      INTEGER NOT NULL,
    bind_type       TEXT    NOT NULL
                    CHECK (bind_type IN (
                        'channel', 'module', 'version', 'task',
                        'register', 'tacid', 'table', 'other'
                    )),
    bind_key        TEXT    NOT NULL,
    bind_id         INTEGER,
    notes           TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (concept_id, bind_type, bind_key),
    FOREIGN KEY (concept_id) REFERENCES onto_concept (id) ON DELETE CASCADE
);
"""

ONTO_MONITOR_ROLLUP_DDL = """
CREATE TABLE IF NOT EXISTS onto_monitor_rollup (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    system_key      TEXT    NOT NULL UNIQUE,
    active_n        INTEGER NOT NULL DEFAULT 0,
    draft_n         INTEGER NOT NULL DEFAULT 0,
    incomplete_n    INTEGER NOT NULL DEFAULT 0,
    rubbish_n       INTEGER NOT NULL DEFAULT 0,
    zombie_n        INTEGER NOT NULL DEFAULT 0,
    register_n      INTEGER NOT NULL DEFAULT 0,
    report_json     TEXT    NOT NULL DEFAULT '{}',
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

TASK_CENTER_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_dev_task_parent ON dev_task (parent_task_id);
CREATE INDEX IF NOT EXISTS idx_dev_task_version ON dev_task (version_id, status);
CREATE INDEX IF NOT EXISTS idx_dev_task_channel_module ON dev_task (channel_id, module_id);
CREATE INDEX IF NOT EXISTS idx_schema_ssot_table ON schema_ssot (table_name, keyword);
CREATE INDEX IF NOT EXISTS idx_schema_qc_run_table ON schema_qc_run (table_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_version_center_cm ON version_center (channel_id, module_id);
CREATE INDEX IF NOT EXISTS idx_task_ssot_task ON task_ssot (task_id, sort_order, dim_key);
CREATE INDEX IF NOT EXISTS idx_fault_report_event ON fault_report (event_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fault_report_task ON fault_report (remediate_task_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_fn_invoke_trace_ts ON function_invoke_trace (ts DESC);
CREATE INDEX IF NOT EXISTS idx_fn_invoke_trace_mod_fn ON function_invoke_trace (module_name, function_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fn_invoke_trace_tacid ON function_invoke_trace (tacid, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fn_invoke_trace_task ON function_invoke_trace (task_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fn_scoring_status ON function_scoring (status, functional_score);
CREATE INDEX IF NOT EXISTS idx_code_register_status ON code_register (status, system_key);
CREATE INDEX IF NOT EXISTS idx_code_register_mod_fn ON code_register (module_name, function_name);
CREATE INDEX IF NOT EXISTS idx_code_register_task ON code_register (task_id);
CREATE INDEX IF NOT EXISTS idx_onto_concept_kind ON onto_concept (kind, status);
CREATE INDEX IF NOT EXISTS idx_onto_binding_concept ON onto_binding (concept_id, bind_type);
CREATE INDEX IF NOT EXISTS idx_onto_link_from ON onto_link (from_concept_id);
"""

# Additive columns for existing DBs (CREATE IF NOT EXISTS won't add cols)
FUNCTION_SCORING_NEW_COLUMNS = [
    ("register_id", "TEXT"),
    ("file_path", "TEXT"),
    ("line_start", "INTEGER"),
    ("line_end", "INTEGER"),
    ("code_span", "TEXT"),
]

CODE_REGISTER_NEW_COLUMNS = [
    ("file_path", "TEXT"),
    ("line_start", "INTEGER"),
    ("line_end", "INTEGER"),
    ("code_span", "TEXT"),
]

def ensure_hub_ssot_schema(conn: sqlite3.Connection) -> dict:
    """Create hub/SSOT tables and additive columns. Returns summary dict."""
    conn.execute("PRAGMA foreign_keys = ON;")

    for ddl in (
        CHANNEL_DDL,
        MODULE_DDL,
        VISION_ASSET_DDL,
        FAULT_OPTION_DDL,
        FAULT_SSOT_DDL,
        FAULT_SOLUTION_DDL,
    ):
        conn.executescript(ddl)

    if _table_exists(conn, "fault_event"):
        conn.executescript(FAULT_ANALYSIS_DDL)
        conn.executescript(FAULT_EVENT_FACT_DDL)
        conn.executescript(FAULT_OPTION_PENDING_DDL)
        conn.executescript(FAULT_SSOT_REVISION_DDL)

    fe_added = _add_columns_if_missing(conn, "fault_event", FAULT_EVENT_NEW_COLUMNS)
    fa_added = _add_columns_if_missing(conn, "fault_analysis", FAULT_ANALYSIS_NEW_COLUMNS)

    conn.executescript(INDEX_DDL)
    seed_info = seed_ssot_defaults(conn)

    task_info = ensure_task_center_schema(conn)

    conn.commit()

    return {
        "fault_event_columns_added": fe_added,
        "fault_analysis_columns_added": fa_added,
        "seed": seed_info,
        "task_center": task_info,
    }


def ensure_task_center_schema(conn: sqlite3.Connection) -> dict:
    """QC0/T1: task/version center + schema SSOT/QC tables + seed.

    Also CH1 pipeline C: function_invoke_trace + function_scoring (never a gate).
    CH7: code_register/function_scoring file+line columns (never a gate).
    """
    conn.execute("PRAGMA foreign_keys = ON;")
    for ddl in (
        TASK_ACTION_NAME_DDL,
        TDD_TYPE_DDL,
        VERSION_CENTER_DDL,
        DEV_TASK_DDL,
        DEV_TASK_FIELD_DDL,
        SCHEMA_SSOT_DDL,
        SCHEMA_QC_RUN_DDL,
        TASK_SSOT_DDL,
        FAULT_REPORT_DDL,
        FUNCTION_INVOKE_TRACE_DDL,
        FUNCTION_SCORING_DDL,
        CODE_REGISTER_DDL,
        FN_REQUEST_DDL,
        FN_RESEARCH_DDL,
        FIELD_TDD_RULE_DDL,
        ONTO_CONCEPT_DDL,
        ONTO_LINK_DDL,
        ONTO_BINDING_DDL,
        ONTO_MONITOR_ROLLUP_DDL,
        PAIR_QC_RUN_DDL,
        PAIR_VALUE_SSOT_DDL,
        SKILL_PROMPT_SSOT_DDL,
        SKILL_PROMPT_CASE_DDL,
        SKILL_PROMPT_TEST_RUN_DDL,
        SKILL_PROMPT_INFERENCE_DDL,
        TASK_CENTER_INDEX_DDL,
        SETTINGS_DDL,
    ):
        conn.executescript(ddl)
    pair_qc_run_migrated = _migrate_pair_qc_run_schema(conn)
    pair_value_ssot_cols = _add_columns_if_missing(
        conn,
        "pair_value_ssot",
        [
            ("register_id", "TEXT"),
            ("slice_key", "TEXT"),
            ("value_raw", "TEXT"),
            ("value_norm", "TEXT"),
            ("region", "TEXT"),
            ("meta_json", "TEXT NOT NULL DEFAULT '{}'"),
            ("observed_at", "TEXT"),
        ],
    )
    # pair indexes after tables exist (legacy DBs may run INDEX_DDL before pair tables)
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pair_qc_run'"
        ).fetchone():
            cols = {
                str(r[1])
                for r in conn.execute("PRAGMA table_info(pair_qc_run)").fetchall()
            }
            if "status" in cols and "created_at" in cols:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pair_qc_run_status "
                    "ON pair_qc_run (status, created_at DESC)"
                )
            if "register_id" in cols and "field_name" in cols:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pair_qc_run_register "
                    "ON pair_qc_run (register_id, field_name)"
                )
            if "case_id" in cols:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pair_qc_run_case "
                    "ON pair_qc_run (case_id)"
                )
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pair_value_ssot'"
        ).fetchone():
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pair_value_ssot_field "
                "ON pair_value_ssot (register_id, field_name, source)"
            )
    except sqlite3.Error:
        pass
    scoring_cols = _add_columns_if_missing(
        conn, "function_scoring", FUNCTION_SCORING_NEW_COLUMNS
    )
    register_cols = _add_columns_if_missing(
        conn, "code_register", CODE_REGISTER_NEW_COLUMNS
    )
    dev_task_cols = _add_columns_if_missing(
        conn, "dev_task", DEV_TASK_NEW_COLUMNS
    )
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dev_task'"
        ).fetchone():
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dev_task_session "
                "ON dev_task (session_id)"
            )
    except sqlite3.Error:
        pass
    # file/line index only after additive columns exist on legacy DBs
    try:
        cr_cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(code_register)").fetchall()
        }
        if {"file_path", "line_start", "line_end"}.issubset(cr_cols):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_register_file "
                "ON code_register (file_path, line_start, line_end)"
            )
    except sqlite3.Error:
        pass
    out = seed_task_center_defaults(conn)
    out["openclaw_capability"] = seed_openclaw_capability_ssot(conn)
    out["fault_report"] = True
    out["code_health"] = {
        "function_invoke_trace": True,
        "function_scoring": True,
        "gate": "never",
        "why": ["W2", "W9", "W10", "W11"],
        "source_location": True,
        "function_scoring_columns_added": scoring_cols,
        "code_register_columns_added": register_cols,
        "dev_task_columns_added": dev_task_cols,
    }
    out["managed_coding"] = {
        "code_register": True,
        "fn_request": True,
        "fn_research": True,
        "field_tdd_rule": True,
        "onto_concept": True,
        "onto_link": True,
        "onto_binding": True,
        "onto_monitor_rollup": True,
        "function_scoring_columns_added": scoring_cols,
        "code_register_columns_added": register_cols,
        "gate": "never",
        "pipeline": "D_managed_coding",
        "flow": "human_request->research->multi_dim_ssot->build->register_id+tdd+file:line",
    }
    out["pair_qc"] = {
        "pair_qc_run": True,
        "pair_value_ssot": True,
        "gate": "never",
        "pipeline": "E_pair_qc",
        "flow": "normalize->mcp_ssot_vs_ui->compare->case",
        "detect_only": True,
        "pair_qc_run_migrated": pair_qc_run_migrated,
        "pair_value_ssot_columns_added": pair_value_ssot_cols,
    }
    # Membership Task-1 seed lives in managed_coding (import lazy to avoid cycles)
    try:
        from managed_coding import seed_membership_system

        out["membership_seed"] = seed_membership_system(conn, commit=False)
    except Exception as e:
        out["membership_seed_error"] = f"{type(e).__name__}: {e}"
    # OpenClaw MCP tool register_id spine (lazy import)
    try:
        from openclaw_mcp_trace import seed_openclaw_mcp_registers

        out["openclaw_mcp_registers"] = seed_openclaw_mcp_registers(
            conn, commit=False
        )
    except Exception as e:
        out["openclaw_mcp_registers_error"] = f"{type(e).__name__}: {e}"
    return out


def insert_fault_report(
    conn: sqlite3.Connection,
    *,
    event_id: int | None = None,
    remediate_task_id: int | None = None,
    qc_task_id: int | None = None,
    table_name: str | None = None,
    gate_result: str | None = None,
    goal_type: str | None = None,
    summary: str | None = None,
    markdown_text: str | None = None,
    report: dict | list | str,
    notified_at: str | None = None,
    source: str = "fail_handling",
) -> int:
    """Insert one STEP5 fault_report row. Returns report id."""
    if isinstance(report, (dict, list)):
        report_json = json.dumps(report, ensure_ascii=False, default=str)
    else:
        report_json = str(report)
    cur = conn.execute(
        """
        INSERT INTO fault_report
            (event_id, remediate_task_id, qc_task_id, table_name, gate_result,
             goal_type, summary, markdown_text, report_json, notified_at, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            remediate_task_id,
            qc_task_id,
            table_name,
            gate_result,
            goal_type,
            summary,
            markdown_text,
            report_json,
            notified_at,
            source,
        ),
    )
    return int(cur.lastrowid)


def list_fault_reports(
    conn: sqlite3.Connection,
    *,
    event_id: int | None = None,
    remediate_task_id: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """List recent fault_report rows for event and/or remediate task."""
    clauses: list[str] = []
    args: list = []
    if event_id is not None:
        clauses.append("event_id = ?")
        args.append(int(event_id))
    if remediate_task_id is not None:
        clauses.append("remediate_task_id = ?")
        args.append(int(remediate_task_id))
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT id, event_id, remediate_task_id, qc_task_id, table_name,
               gate_result, goal_type, summary, markdown_text, report_json,
               notified_at, source, created_at
        FROM fault_report
        {where}
        ORDER BY id DESC
        LIMIT ?
    """
    args.append(int(limit))
    rows = []
    try:
        for r in conn.execute(sql, args).fetchall():
            if hasattr(r, "keys"):
                d = {k: r[k] for k in r.keys()}
            else:
                d = {
                    "id": r[0],
                    "event_id": r[1],
                    "remediate_task_id": r[2],
                    "qc_task_id": r[3],
                    "table_name": r[4],
                    "gate_result": r[5],
                    "goal_type": r[6],
                    "summary": r[7],
                    "markdown_text": r[8],
                    "report_json": r[9],
                    "notified_at": r[10],
                    "source": r[11],
                    "created_at": r[12],
                }
            rows.append(d)
    except sqlite3.Error:
        return []
    return rows


def seed_task_center_defaults(conn: sqlite3.Connection) -> dict:
    """Idempotent seed: actions, tdd types, agent_db module, version 1.1, sample tasks."""
    out: dict = {
        "actions": 0,
        "tdd_types": 0,
        "modules": 0,
        "versions": 0,
        "tasks": 0,
        "fields": 0,
    }

    def get_or_create_dim(table: str, code: str, name: str) -> int:
        row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            f"INSERT INTO {table} (code, name) VALUES (?, ?)",
            (code, name),
        )
        out["modules"] += 1 if table == "module" else 0
        return int(cur.lastrowid)

    channel_id = get_or_create_dim("channel", "local_pc", "Local PC")
    mod_db = get_or_create_dim("module", "agent_db", "Agent DB / Schema")
    get_or_create_dim("module", "schema_qc", "Schema QC")

    actions = [
        ("table", "create", "table.create", "Create table", 0),
        ("field", "create", "field.create", "Create field", 1),
        ("field", "rename", "field.rename", "Rename field", 1),
        ("field", "update", "field.update", "Update field", 1),
        ("field", "delete", "field.delete", "Delete field", 1),
        ("qc", "verify_schema", "qc.verify_schema", "QC verify schema", 0),
        (
            "capability",
            "ssot",
            "capability.ssot",
            "Capability multi-dim SSOT (tools/MCP/API)",
            0,
        ),
        ("schema", "remediate", "schema.remediate", "Remediate schema QC fail", 0),
        ("seed", "create", "seed.create", "Seed data", 0),
        ("migrate", "apply", "migrate.apply", "Apply migration", 0),
        (
            "code",
            "cleanup",
            "code.cleanup",
            "Cleanup dead/zombie function (mark only)",
            0,
        ),
        (
            "qc",
            "pair_verify",
            "qc.pair_verify",
            "Pair QC dual-path verify (MCP SSOT vs UI)",
            0,
        ),
    ]
    action_ids: dict[str, int] = {}
    for element, action, code, name, requires_tdd in actions:
        row = conn.execute(
            "SELECT id FROM task_action_name WHERE code = ?", (code,)
        ).fetchone()
        if row:
            action_ids[code] = int(row[0])
            continue
        cur = conn.execute(
            """
            INSERT INTO task_action_name (element, action, code, name, requires_tdd, status)
            VALUES (?, ?, ?, ?, ?, 'active')
            """,
            (element, action, code, name, requires_tdd),
        )
        action_ids[code] = int(cur.lastrowid)
        out["actions"] += 1

    tdd_rows = [
        ("int", "Integer", "INTEGER"),
        ("text", "Text", "TEXT"),
        ("real", "Real", "REAL"),
        ("blob", "Blob", "BLOB"),
        ("json", "JSON text", "TEXT"),
        ("array", "Array (JSON)", "TEXT"),
        ("datetime", "DateTime", "TEXT"),
        ("bool", "Boolean", "INTEGER"),
    ]
    tdd_ids: dict[str, int] = {}
    for code, name, affinity in tdd_rows:
        row = conn.execute("SELECT id FROM tdd_type WHERE code = ?", (code,)).fetchone()
        if row:
            tdd_ids[code] = int(row[0])
            continue
        cur = conn.execute(
            "INSERT INTO tdd_type (code, name, sqlite_affinity) VALUES (?, ?, ?)",
            (code, name, affinity),
        )
        tdd_ids[code] = int(cur.lastrowid)
        out["tdd_types"] += 1

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, mod_db, "1.1"),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, '1.1', ?, ?, 'active')
            """,
            (
                channel_id,
                mod_db,
                "Hub + vision_asset + SSOT",
                "Phase 0-2 schema hub and QC foundation",
            ),
        )
        version_id = int(cur.lastrowid)
        out["versions"] += 1

    # Root task 1.1 = create table vision_asset
    root = conn.execute(
        """
        SELECT id FROM dev_task
        WHERE version_id = ? AND task_label = ?
        """,
        (version_id, "1.1"),
    ).fetchone()
    if root:
        root_id = int(root[0])
    else:
        payload = json.dumps(
            {
                "table": "vision_asset",
                "expected_columns": [
                    "id",
                    "kind",
                    "path_or_url",
                    "sha256",
                    "source",
                    "created_at",
                ],
            },
            ensure_ascii=False,
        )
        cur = conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status, completed_at)
            VALUES (NULL, ?, ?, ?, ?, '1.1', ?, ?, 'pass', CURRENT_TIMESTAMP)
            """,
            (
                channel_id,
                mod_db,
                action_ids["table.create"],
                version_id,
                "create table vision_asset",
                payload,
            ),
        )
        root_id = int(cur.lastrowid)
        out["tasks"] += 1

    # Child 1.1a = define fields
    child_a = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, "1.1a"),
    ).fetchone()
    if child_a:
        child_a_id = int(child_a[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status, completed_at)
            VALUES (?, ?, ?, ?, ?, '1.1a', ?, ?, 'pass', CURRENT_TIMESTAMP)
            """,
            (
                root_id,
                channel_id,
                mod_db,
                action_ids["field.create"],
                version_id,
                "define vision_asset fields",
                json.dumps({"table": "vision_asset"}, ensure_ascii=False),
            ),
        )
        child_a_id = int(cur.lastrowid)
        out["tasks"] += 1

    field_specs = [
        ("id", "int", 0, 1, 0),
        ("kind", "text", 0, 0, 1),
        ("path_or_url", "text", 0, 0, 2),
        ("sha256", "text", 1, 0, 3),
        ("source", "text", 1, 0, 4),
        ("created_at", "datetime", 1, 0, 5),
    ]
    for fname, tcode, nullable, is_pk, sort_order in field_specs:
        exists = conn.execute(
            "SELECT 1 FROM dev_task_field WHERE task_id = ? AND field_name = ?",
            (child_a_id, fname),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """
            INSERT INTO dev_task_field
                (task_id, action_name_id, tdd_type_id, field_name, nullable, is_pk, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                child_a_id,
                action_ids["field.create"],
                tdd_ids[tcode],
                fname,
                nullable,
                is_pk,
                sort_order,
            ),
        )
        out["fields"] += 1

    # Child 1.1b = QC
    child_b = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, "1.1b"),
    ).fetchone()
    if not child_b:
        conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status)
            VALUES (?, ?, ?, ?, ?, '1.1b', ?, ?, 'pending')
            """,
            (
                root_id,
                channel_id,
                mod_db,
                action_ids["qc.verify_schema"],
                version_id,
                "QC browser proof vision_asset",
                json.dumps(
                    {
                        "table": "vision_asset",
                        "browser_url": f"http://127.0.0.1:{DEFAULT_BROWSER_PORT}/?table=vision_asset&limit=200",
                        "expected_columns": [
                            "id",
                            "kind",
                            "path_or_url",
                            "sha256",
                            "source",
                            "created_at",
                        ],
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        out["tasks"] += 1

    out["channel_id"] = channel_id
    out["module_agent_db_id"] = mod_db
    out["version_1_1_id"] = version_id
    out["root_task_1_1_id"] = root_id

    # Seed default browser port setting (idempotent)
    try:
        conn.execute(
            """
            INSERT INTO settings (key, value)
            VALUES ('browser.port', ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (str(DEFAULT_BROWSER_PORT),),
        )
        out["settings"] = {"browser.port": DEFAULT_BROWSER_PORT}
    except sqlite3.Error as e:
        out["settings_error"] = f"{type(e).__name__}: {e}"

    return out


def list_task_ssot(conn: sqlite3.Connection, task_id: int) -> list[dict]:
    """Return task_ssot rows for task_id ordered by sort_order, id."""
    rows = conn.execute(
        """
        SELECT id, task_id, dim_key, value_text, value_type, source,
               parent_dim_id, sort_order, notes, updated_at, created_at
        FROM task_ssot
        WHERE task_id = ?
        ORDER BY sort_order, id
        """,
        (task_id,),
    ).fetchall()
    out = []
    for r in rows:
        if isinstance(r, sqlite3.Row):
            out.append(dict(r))
        else:
            out.append(
                {
                    "id": r[0],
                    "task_id": r[1],
                    "dim_key": r[2],
                    "value_text": r[3],
                    "value_type": r[4],
                    "source": r[5],
                    "parent_dim_id": r[6],
                    "sort_order": r[7],
                    "notes": r[8],
                    "updated_at": r[9],
                    "created_at": r[10],
                }
            )
    return out


def upsert_task_ssot(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    dim_key: str,
    value_text: str | None = None,
    value_type: str = "string",
    source: str = "manual",
    parent_dim_id: int | None = None,
    sort_order: int = 0,
    notes: str | None = None,
    commit: bool = True,
) -> dict:
    """Insert or update one task_ssot dim (UNIQUE task_id+dim_key)."""
    dim_key = (dim_key or "").strip()
    if not dim_key:
        raise ValueError("dim_key required")
    if not conn.execute("SELECT 1 FROM dev_task WHERE id = ?", (task_id,)).fetchone():
        raise ValueError(f"unknown task_id: {task_id}")
    value_type = (value_type or "string").strip() or "string"
    source = (source or "manual").strip() or "manual"
    existing = conn.execute(
        "SELECT id FROM task_ssot WHERE task_id = ? AND dim_key = ?",
        (task_id, dim_key),
    ).fetchone()
    if existing:
        sid = int(existing[0])
        conn.execute(
            """
            UPDATE task_ssot
            SET value_text = ?, value_type = ?, source = ?,
                parent_dim_id = ?, sort_order = ?, notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                value_text,
                value_type,
                source,
                parent_dim_id,
                int(sort_order),
                notes,
                sid,
            ),
        )
        action = "updated"
    else:
        cur = conn.execute(
            """
            INSERT INTO task_ssot
                (task_id, dim_key, value_text, value_type, source,
                 parent_dim_id, sort_order, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                dim_key,
                value_text,
                value_type,
                source,
                parent_dim_id,
                int(sort_order),
                notes,
            ),
        )
        sid = int(cur.lastrowid)
        action = "inserted"
    if commit:
        conn.commit()
    row = conn.execute(
        """
        SELECT id, task_id, dim_key, value_text, value_type, source,
               parent_dim_id, sort_order, notes, updated_at, created_at
        FROM task_ssot WHERE id = ?
        """,
        (sid,),
    ).fetchone()
    result = dict(row) if isinstance(row, sqlite3.Row) else {
        "id": sid,
        "task_id": task_id,
        "dim_key": dim_key,
        "value_text": value_text,
        "value_type": value_type,
        "source": source,
        "parent_dim_id": parent_dim_id,
        "sort_order": int(sort_order),
        "notes": notes,
    }
    result["action"] = action
    return result


def delete_task_ssot(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    dim_key: str | None = None,
    ssot_id: int | None = None,
    commit: bool = True,
) -> dict:
    """Delete one dim by id or by (task_id, dim_key)."""
    if ssot_id is not None:
        cur = conn.execute(
            "DELETE FROM task_ssot WHERE id = ? AND task_id = ?",
            (int(ssot_id), int(task_id)),
        )
    elif dim_key:
        cur = conn.execute(
            "DELETE FROM task_ssot WHERE task_id = ? AND dim_key = ?",
            (int(task_id), str(dim_key).strip()),
        )
    else:
        raise ValueError("ssot_id or dim_key required")
    if commit:
        conn.commit()
    return {"deleted": int(cur.rowcount), "task_id": int(task_id)}


def seed_openclaw_capability_ssot(conn: sqlite3.Connection) -> dict:
    """Idempotent FH2 example: OpenClaw open-browser capability dims (truthful MCP limits).

    Records actual codebase truth: Companion MCP has screen.snapshot + system.notify;
    no first-class browser.open in mcp_client — use os.webbrowser as alternative.
    """
    out: dict = {"created_task": 0, "dims": 0, "version": 0, "actions": 0}

    def get_or_create_dim(table: str, code: str, name: str) -> int:
        row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            f"INSERT INTO {table} (code, name) VALUES (?, ?)",
            (code, name),
        )
        return int(cur.lastrowid)

    channel_id = get_or_create_dim("channel", "local_pc", "Local PC")
    mod_oc = get_or_create_dim(
        "module", "openclaw_companion", "OpenClaw Companion"
    )

    act = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", ("capability.ssot",)
    ).fetchone()
    if act:
        act_id = int(act[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO task_action_name
                (element, action, code, name, requires_tdd, status)
            VALUES ('capability', 'ssot', 'capability.ssot',
                    'Capability multi-dim SSOT (tools/MCP/API)', 0, 'active')
            """
        )
        act_id = int(cur.lastrowid)
        out["actions"] += 1

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, mod_oc, "cap-1.0"),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, 'cap-1.0', ?, ?, 'active')
            """,
            (
                channel_id,
                mod_oc,
                "OpenClaw capability SSOT",
                "FH2 multi-dim capability tree (truthful MCP surface)",
            ),
        )
        version_id = int(cur.lastrowid)
        out["version"] = 1

    label = "oc.open-browser"
    existing = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, label),
    ).fetchone()
    goal_payload = {
        "pipeline": "B_fail_handling",
        "goal_type": "implement_capability",
        "goal_text": "Open a browser to a specified URL via OpenClaw stack (or documented fallback).",
        "success_criteria": (
            "Either MCP/API exposes open_browser with required fields documented in task_ssot, "
            "or task_ssot records fallback os.webbrowser and a smoke path succeeds."
        ),
        "goal_source": "seed_openclaw_capability_ssot",
        "plan_steps": [],
        "capability": "open_browser",
        "tool_vendor": "openclaw",
    }
    if existing:
        task_id = int(existing[0])
        # keep goal fields fresh on seed re-run (dims still idempotent)
        conn.execute(
            """
            UPDATE dev_task
            SET title = ?, payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "Capability SSOT: open browser (OpenClaw)",
                json.dumps(goal_payload, ensure_ascii=False),
                task_id,
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
                mod_oc,
                act_id,
                version_id,
                label,
                "Capability SSOT: open browser (OpenClaw)",
                json.dumps(goal_payload, ensure_ascii=False),
            ),
        )
        task_id = int(cur.lastrowid)
        out["created_task"] = 1

    # dim tree mirrors user example 1.1–1.4A; values = codebase truth
    dims = [
        (10, "goal.capability", "open_browser", "string", "Open browser to URL"),
        (20, "tool.vendor", "openclaw", "string", "1.1 tools = openclaw"),
        (30, "tool.mcp_supported", "yes", "bool", "1.2 Companion Local MCP exists"),
        (
            40,
            "tool.mcp_tools",
            json.dumps(
                ["screen.snapshot", "system.notify", "ping"],
                ensure_ascii=False,
            ),
            "json",
            "Actual tools exposed via mcp_client (not exhaustive Companion catalog)",
        ),
        (
            50,
            "tool.capability.open_browser",
            "false",
            "bool",
            "1.3 NO first-class browser.open in mcp_client today",
        ),
        (
            60,
            "tool.capability.open_browser.desired",
            "true",
            "bool",
            "Goal wants open browser = yes",
        ),
        (
            70,
            "connect.mode",
            "mcp+os_fallback",
            "string",
            "1.3A how to connect: MCP where available; webbrowser fallback",
        ),
        (
            80,
            "connect.required_fields",
            json.dumps(["url"], ensure_ascii=False),
            "json",
            "1.3B fields to provide for open browser",
        ),
        (
            90,
            "capability.url_param",
            "yes",
            "bool",
            "1.4 can enter specify url",
        ),
        (
            100,
            "capability.url_param.via",
            "os.webbrowser (QC path); MCP browser.open missing",
            "string",
            "1.4A how = api/MCP — truth: QC uses webbrowser.open best-effort",
        ),
        (
            110,
            "fallback.open_browser",
            "python webbrowser.open / OS default browser",
            "string",
            "Documented alternative until MCP tool exists",
        ),
        (
            120,
            "ssot.truth_note",
            "Do not plan MCP browser.open until implemented; update this dim when added",
            "string",
            "Prevents AI hallucination of non-existent MCP tool",
        ),
    ]
    for sort_order, dim_key, value_text, value_type, notes in dims:
        before = conn.execute(
            "SELECT id, value_text FROM task_ssot WHERE task_id = ? AND dim_key = ?",
            (task_id, dim_key),
        ).fetchone()
        upsert_task_ssot(
            conn,
            task_id=task_id,
            dim_key=dim_key,
            value_text=value_text,
            value_type=value_type,
            source="seed",
            sort_order=sort_order,
            notes=notes,
            commit=False,
        )
        if before is None:
            out["dims"] += 1
        elif str(before[1] or "") != str(value_text):
            out["dims"] += 1  # count refresh as touch

    out["task_id"] = task_id
    out["task_label"] = label
    out["version_id"] = version_id
    out["module_id"] = mod_oc
    out["channel_id"] = channel_id
    return out


def _action_id_by_code(conn: sqlite3.Connection, code: str) -> int:
    row = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", (code,)
    ).fetchone()
    if not row:
        raise ValueError(f"unknown action code: {code}")
    return int(row[0])


def _tdd_id_by_code(conn: sqlite3.Connection, code: str) -> int:
    row = conn.execute("SELECT id FROM tdd_type WHERE code = ?", (code,)).fetchone()
    if not row:
        raise ValueError(f"unknown tdd_type code: {code}")
    return int(row[0])


def create_version(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    module_id: int,
    version_label: str,
    title: str | None = None,
    notes: str | None = None,
    status: str = "draft",
    parent_version_id: int | None = None,
) -> dict:
    """Insert version_center row. Raises ValueError on bad input / duplicate."""
    version_label = (version_label or "").strip()
    if not version_label:
        raise ValueError("version_label required")
    if status not in ("draft", "active", "released", "deprecated"):
        raise ValueError(f"invalid version status: {status}")
    if not conn.execute("SELECT 1 FROM channel WHERE id = ?", (channel_id,)).fetchone():
        raise ValueError(f"unknown channel_id: {channel_id}")
    if not conn.execute("SELECT 1 FROM module WHERE id = ?", (module_id,)).fetchone():
        raise ValueError(f"unknown module_id: {module_id}")
    exists = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, module_id, version_label),
    ).fetchone()
    if exists:
        raise ValueError(
            f"version already exists id={exists[0]} label={version_label}"
        )
    cur = conn.execute(
        """
        INSERT INTO version_center
            (channel_id, module_id, version_label, parent_version_id, title, notes, status)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_id,
            module_id,
            version_label,
            parent_version_id,
            title,
            notes,
            status,
        ),
    )
    conn.commit()
    return {
        "id": int(cur.lastrowid),
        "channel_id": channel_id,
        "module_id": module_id,
        "version_label": version_label,
        "status": status,
    }


def create_dev_task(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    module_id: int,
    version_id: int,
    action_name_id: int,
    task_label: str,
    title: str,
    parent_task_id: int | None = None,
    payload_json: str | dict | None = None,
    status: str = "pending",
    writer: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Insert single dev_task. parent must share version_id when set."""
    task_label = (task_label or "").strip()
    title = (title or "").strip()
    writer_text = (writer or "").strip() or None
    session_text = (session_id or "").strip() or None
    if not task_label or not title:
        raise ValueError("task_label and title required")
    if status not in ("pending", "running", "pass", "fail", "cancelled"):
        raise ValueError(f"invalid task status: {status}")
    ver = conn.execute(
        "SELECT id, channel_id, module_id FROM version_center WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not ver:
        raise ValueError(f"unknown version_id: {version_id}")
    if int(ver[1]) != int(channel_id) or int(ver[2]) != int(module_id):
        raise ValueError("channel_id/module_id must match version_center row")
    if not conn.execute(
        "SELECT 1 FROM task_action_name WHERE id = ?", (action_name_id,)
    ).fetchone():
        raise ValueError(f"unknown action_name_id: {action_name_id}")
    if parent_task_id is not None:
        parent = conn.execute(
            "SELECT id, version_id FROM dev_task WHERE id = ?",
            (parent_task_id,),
        ).fetchone()
        if not parent:
            raise ValueError(f"unknown parent_task_id: {parent_task_id}")
        if int(parent[1]) != int(version_id):
            raise ValueError("parent_task_id must belong to same version_id")
    dup = conn.execute(
        "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
        (version_id, task_label),
    ).fetchone()
    if dup:
        raise ValueError(f"task_label already exists id={dup[0]} label={task_label}")
    if isinstance(payload_json, dict):
        payload_text = json.dumps(payload_json, ensure_ascii=False)
    elif payload_json is None:
        payload_text = None
    else:
        payload_text = str(payload_json)
    # Ensure additive columns exist on legacy DBs before INSERT.
    _add_columns_if_missing(conn, "dev_task", DEV_TASK_NEW_COLUMNS)
    cur = conn.execute(
        """
        INSERT INTO dev_task
            (parent_task_id, channel_id, module_id, action_name_id, version_id,
             task_label, title, payload_json, status, writer, session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parent_task_id,
            channel_id,
            module_id,
            action_name_id,
            version_id,
            task_label,
            title,
            payload_text,
            status,
            writer_text,
            session_text,
        ),
    )
    conn.commit()
    return {
        "id": int(cur.lastrowid),
        "parent_task_id": parent_task_id,
        "task_label": task_label,
        "title": title,
        "status": status,
        "version_id": version_id,
        "writer": writer_text,
        "session_id": session_text,
    }


def update_dev_task_context(
    conn: sqlite3.Connection,
    task_id: int,
    *,
    writer: str | None = None,
    session_id: str | None = None,
    set_writer: bool = False,
    set_session_id: bool = False,
) -> dict:
    """Set writer / session_id on an existing dev_task. Empty string clears."""
    if not set_writer and not set_session_id:
        raise ValueError("provide writer and/or session_id")
    _add_columns_if_missing(conn, "dev_task", DEV_TASK_NEW_COLUMNS)
    row = conn.execute(
        "SELECT id, writer, session_id FROM dev_task WHERE id = ?",
        (int(task_id),),
    ).fetchone()
    if not row:
        raise ValueError(f"unknown task_id: {task_id}")
    new_writer = ((writer or "").strip() or None) if set_writer else row[1]
    new_session = ((session_id or "").strip() or None) if set_session_id else row[2]
    conn.execute(
        """
        UPDATE dev_task
           SET writer = ?, session_id = ?, updated_at = CURRENT_TIMESTAMP
         WHERE id = ?
        """,
        (new_writer, new_session, int(task_id)),
    )
    conn.commit()
    return {
        "id": int(task_id),
        "writer": new_writer,
        "session_id": new_session,
        "ok": True,
    }


def add_dev_task_field(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    field_name: str,
    tdd_type_id: int | None = None,
    tdd_code: str | None = None,
    nullable: int = 1,
    is_pk: int = 0,
    default_text: str | None = None,
    sort_order: int = 0,
    action_name_id: int | None = None,
) -> dict:
    """Append dev_task_field row on a field.* task."""
    field_name = (field_name or "").strip()
    if not field_name:
        raise ValueError("field_name required")
    task = conn.execute(
        """
        SELECT t.id, t.action_name_id, a.code, a.requires_tdd
        FROM dev_task t
        JOIN task_action_name a ON a.id = t.action_name_id
        WHERE t.id = ?
        """,
        (task_id,),
    ).fetchone()
    if not task:
        raise ValueError(f"unknown task_id: {task_id}")
    if tdd_type_id is None:
        if not tdd_code:
            raise ValueError("tdd_type_id or tdd_code required")
        tdd_type_id = _tdd_id_by_code(conn, tdd_code)
    elif not conn.execute(
        "SELECT 1 FROM tdd_type WHERE id = ?", (tdd_type_id,)
    ).fetchone():
        raise ValueError(f"unknown tdd_type_id: {tdd_type_id}")
    if action_name_id is None:
        action_name_id = int(task[1])
    exists = conn.execute(
        "SELECT id FROM dev_task_field WHERE task_id = ? AND field_name = ?",
        (task_id, field_name),
    ).fetchone()
    if exists:
        raise ValueError(f"field already exists id={exists[0]} name={field_name}")
    cur = conn.execute(
        """
        INSERT INTO dev_task_field
            (task_id, action_name_id, tdd_type_id, field_name, nullable, is_pk,
             default_text, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            action_name_id,
            tdd_type_id,
            field_name,
            int(nullable),
            int(is_pk),
            default_text,
            int(sort_order),
        ),
    )
    conn.commit()
    return {
        "id": int(cur.lastrowid),
        "task_id": task_id,
        "field_name": field_name,
        "tdd_type_id": tdd_type_id,
        "nullable": int(nullable),
        "is_pk": int(is_pk),
        "sort_order": int(sort_order),
    }


def create_task_bundle(
    conn: sqlite3.Connection,
    *,
    channel_id: int,
    module_id: int,
    version_id: int,
    root_label: str,
    title: str,
    table_name: str,
    fields: list | None = None,
    browser_base: str | None = None,
    status: str = "pending",
) -> dict:
    """Create root table.create + {label}a fields + {label}b QC (seed 1.1 shape).

    Does NOT apply SQLite DDL — planning rows only.
    """
    root_label = (root_label or "").strip()
    title = (title or "").strip()
    table_name = (table_name or "").strip()
    if not root_label or not title or not table_name:
        raise ValueError("root_label, title, table_name required")
    if not table_name.replace("_", "").isalnum():
        raise ValueError(f"invalid table_name: {table_name}")
    fields = list(fields or [])
    expected_columns = [str(f.get("name") or f.get("field_name") or "").strip() for f in fields]
    expected_columns = [c for c in expected_columns if c]
    if not expected_columns:
        raise ValueError("fields required (at least one field name)")

    act_table = _action_id_by_code(conn, "table.create")
    act_field = _action_id_by_code(conn, "field.create")
    act_qc = _action_id_by_code(conn, "qc.verify_schema")

    if browser_base is None:
        port = DEFAULT_BROWSER_PORT
        try:
            port = int(get_setting(conn, "browser.port", DEFAULT_BROWSER_PORT))
        except Exception:
            pass
        browser_base = f"http://127.0.0.1:{port}"
    browser_url = f"{browser_base.rstrip('/')}/?table={table_name}&limit=200"
    root_payload = {
        "table": table_name,
        "expected_columns": expected_columns,
    }
    # defer commits inside helpers — use one transaction
    # create_dev_task commits; for atomicity do inline inserts here
    ver = conn.execute(
        "SELECT id, channel_id, module_id FROM version_center WHERE id = ?",
        (version_id,),
    ).fetchone()
    if not ver:
        raise ValueError(f"unknown version_id: {version_id}")
    if int(ver[1]) != int(channel_id) or int(ver[2]) != int(module_id):
        raise ValueError("channel_id/module_id must match version_center row")

    for lab in (root_label, f"{root_label}a", f"{root_label}b"):
        dup = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, lab),
        ).fetchone()
        if dup:
            raise ValueError(f"task_label already exists id={dup[0]} label={lab}")

    cur = conn.execute(
        """
        INSERT INTO dev_task
            (parent_task_id, channel_id, module_id, action_name_id, version_id,
             task_label, title, payload_json, status)
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel_id,
            module_id,
            act_table,
            version_id,
            root_label,
            title,
            json.dumps(root_payload, ensure_ascii=False),
            status,
        ),
    )
    root_id = int(cur.lastrowid)

    cur = conn.execute(
        """
        INSERT INTO dev_task
            (parent_task_id, channel_id, module_id, action_name_id, version_id,
             task_label, title, payload_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            root_id,
            channel_id,
            module_id,
            act_field,
            version_id,
            f"{root_label}a",
            f"define {table_name} fields",
            json.dumps({"table": table_name}, ensure_ascii=False),
            status,
        ),
    )
    child_a_id = int(cur.lastrowid)

    field_rows = []
    for i, f in enumerate(fields):
        fname = str(f.get("name") or f.get("field_name") or "").strip()
        if not fname:
            continue
        tdd_code = str(f.get("tdd_code") or f.get("tdd") or "text").strip()
        tdd_id = _tdd_id_by_code(conn, tdd_code)
        nullable = int(f.get("nullable", 1))
        is_pk = int(f.get("is_pk", 0))
        sort_order = int(f.get("sort_order", i))
        default_text = f.get("default_text")
        c2 = conn.execute(
            """
            INSERT INTO dev_task_field
                (task_id, action_name_id, tdd_type_id, field_name, nullable, is_pk,
                 default_text, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                child_a_id,
                act_field,
                tdd_id,
                fname,
                nullable,
                is_pk,
                default_text,
                sort_order,
            ),
        )
        field_rows.append(
            {
                "id": int(c2.lastrowid),
                "field_name": fname,
                "tdd_code": tdd_code,
                "nullable": nullable,
                "is_pk": is_pk,
                "sort_order": sort_order,
            }
        )

    qc_payload = {
        "table": table_name,
        "browser_url": browser_url,
        "expected_columns": expected_columns,
    }
    cur = conn.execute(
        """
        INSERT INTO dev_task
            (parent_task_id, channel_id, module_id, action_name_id, version_id,
             task_label, title, payload_json, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            root_id,
            channel_id,
            module_id,
            act_qc,
            version_id,
            f"{root_label}b",
            f"QC browser proof {table_name}",
            json.dumps(qc_payload, ensure_ascii=False),
            status,
        ),
    )
    child_b_id = int(cur.lastrowid)
    conn.commit()
    return {
        "root": {
            "id": root_id,
            "task_label": root_label,
            "title": title,
            "action": "table.create",
        },
        "child_a": {
            "id": child_a_id,
            "task_label": f"{root_label}a",
            "action": "field.create",
            "fields": field_rows,
        },
        "child_b": {
            "id": child_b_id,
            "task_label": f"{root_label}b",
            "action": "qc.verify_schema",
            "browser_url": browser_url,
        },
        "table_name": table_name,
        "version_id": version_id,
        "expected_columns": expected_columns,
    }


def _dim_id_by_code(conn: sqlite3.Connection, table: str, code: str) -> int | None:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    return int(row[0]) if row else None


def next_remediation_label(
    conn: sqlite3.Connection, version_id: int, parent_label: str
) -> str:
    """Return unique label like '{parent}-fix-{n}' under version_id."""
    base = f"{(parent_label or 'qc').strip()}-fix"
    n = 1
    while True:
        label = f"{base}-{n}"
        exists = conn.execute(
            "SELECT 1 FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, label),
        ).fetchone()
        if not exists:
            return label
        n += 1
        if n > 10000:
            raise ValueError("could not allocate remediation task_label")


def record_schema_qc_hard_fail(
    conn: sqlite3.Connection,
    *,
    table: str,
    diff: dict,
    observed: dict | None = None,
    expected: list | None = None,
    qc_task_id: int | None = None,
    qc_run_id: int | None = None,
    browser_url: str | None = None,
    vision_id: int | None = None,
    channel_id: int | None = None,
    module_id: int | None = None,
    version_id: int | None = None,
) -> dict:
    """On HARD GATE fail: open fault_event + facts + schema.remediate child task.

    - Each call = new fault + new remediation task (no dedupe).
    - parent_task_id = failed QC task when known.
    - Does not auto-resolve anything. Caller should commit (this function commits).
    """
    observed = observed or {}
    diff = diff or {}
    table = (table or "").strip() or "unknown"

    # Resolve dims from QC task when possible
    parent_label = "qc"
    if qc_task_id is not None:
        row = conn.execute(
            """
            SELECT id, parent_task_id, channel_id, module_id, version_id, task_label
            FROM dev_task WHERE id = ?
            """,
            (qc_task_id,),
        ).fetchone()
        if row:
            channel_id = channel_id if channel_id is not None else int(row[2])
            module_id = module_id if module_id is not None else int(row[3])
            version_id = version_id if version_id is not None else int(row[4])
            parent_label = str(row[5] or "qc")

    if channel_id is None:
        channel_id = _dim_id_by_code(conn, "channel", "local_pc")
    if module_id is None:
        module_id = _dim_id_by_code(conn, "module", "schema_qc") or _dim_id_by_code(
            conn, "module", "agent_db"
        )
    if channel_id is None or module_id is None:
        raise ValueError("channel_id/module_id required for schema QC fault")

    option_id = None
    opt = conn.execute(
        "SELECT id FROM fault_option WHERE code = ?", ("schema_qc_fail",)
    ).fetchone()
    if opt:
        option_id = int(opt[0])

    cur = conn.execute(
        """
        INSERT INTO fault_event
            (worker_id, group_id, fault_type, status, detect_at,
             channel_id, module_id, option_id, vision_id)
        VALUES (NULL, ?, 'schema_qc_fail', 'open', CURRENT_TIMESTAMP,
                ?, ?, ?, ?)
        """,
        (
            f"schema_qc:{table}",
            channel_id,
            module_id,
            option_id,
            vision_id,
        ),
    )
    event_id = int(cur.lastrowid)
    detect_row = conn.execute(
        "SELECT detect_at FROM fault_event WHERE event_id = ?", (event_id,)
    ).fetchone()
    detect_at = detect_row[0] if detect_row else None


    remediation_id = None
    remediation_label = None
    if version_id is not None:
        try:
            act_id = _action_id_by_code(conn, "schema.remediate")
        except ValueError:
            # seed may not have run yet
            cur_a = conn.execute(
                """
                INSERT INTO task_action_name
                    (element, action, code, name, requires_tdd, status)
                VALUES ('schema', 'remediate', 'schema.remediate',
                        'Remediate schema QC fail', 0, 'active')
                """
            )
            act_id = int(cur_a.lastrowid)
        remediation_label = next_remediation_label(conn, version_id, parent_label)
        # FH0/FH1: goal required on remediate task (Fail-Handling; not a gate)
        try:
            from fail_handling import (
                infer_schema_qc_goal,
                merge_goal_into_payload,
                pragma_diff_std_rows,
            )
        except ImportError:
            infer_schema_qc_goal = None  # type: ignore
            merge_goal_into_payload = None  # type: ignore
            pragma_diff_std_rows = None  # type: ignore

        payload = {
            "table": table,
            "fault_event_id": event_id,
            "qc_task_id": qc_task_id,
            "qc_run_id": qc_run_id,
            "missing_columns": diff.get("missing_columns") or [],
            "extra_columns": diff.get("extra_columns") or [],
            "expected_columns": list(expected or []),
            "observed_columns": list(observed.get("column_names") or []),
            "summary": diff.get("summary") or "",
            "browser_url": browser_url,
            "gate": "pragma_exact_set",
            "pipeline": "B_fail_handling",
        }
        goal_info = {}
        if infer_schema_qc_goal and merge_goal_into_payload:
            goal_info = infer_schema_qc_goal(
                table=table, diff=diff, expected=expected, observed=observed
            )
            payload = merge_goal_into_payload(payload, goal_info)
        else:
            payload.update(
                {
                    "goal_type": "investigate",
                    "goal_text": f"Remediate schema QC fail on `{table}`",
                    "success_criteria": f"Hard gate pass on `{table}` exact set",
                    "goal_source": "db_schema.fallback",
                    "plan_steps": [],
                }
            )
            goal_info = {
                "goal_type": payload["goal_type"],
                "goal_text": payload["goal_text"],
                "success_criteria": payload["success_criteria"],
            }

        if pragma_diff_std_rows:
            payload["step1_std_rows"] = pragma_diff_std_rows(
                case_id=event_id,
                qc_task_id=qc_task_id,
                remediate_task_id=None,
                table=table,
                diff=diff,
                expected=expected,
                observed=observed,
            )

        title = f"Remediate schema QC fail: {table}"
        cur_t = conn.execute(
            """
            INSERT INTO dev_task
                (parent_task_id, channel_id, module_id, action_name_id, version_id,
                 task_label, title, payload_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                qc_task_id,
                channel_id,
                module_id,
                act_id,
                version_id,
                remediation_label,
                title,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        remediation_id = int(cur_t.lastrowid)
        if isinstance(payload.get("step1_std_rows"), list):
            for row in payload["step1_std_rows"]:
                if isinstance(row, dict):
                    row["remediate_task_id"] = remediation_id
        # FH3: STEP4 assemble → plan_steps + step4_delta_rows (not a gate)
        try:
            from fail_handling import assemble_fail_ssot as _assemble_fail_ssot
        except ImportError:
            _assemble_fail_ssot = None  # type: ignore
        if _assemble_fail_ssot is not None:
            ts_rows = []
            try:
                ts_rows = list(list_task_ssot(conn, remediation_id))
                if not ts_rows and qc_task_id is not None:
                    ts_rows = list(list_task_ssot(conn, int(qc_task_id)))
            except Exception:
                ts_rows = []
            schema_rows = []
            try:
                for r in conn.execute(
                    """
                    SELECT keyword, value_text, value_type, source
                    FROM schema_ssot WHERE table_name = ? ORDER BY keyword
                    """,
                    (table,),
                ).fetchall():
                    schema_rows.append(
                        {
                            "keyword": r[0],
                            "value_text": r[1],
                            "value_type": r[2],
                            "source": r[3],
                        }
                    )
            except sqlite3.Error:
                schema_rows = []
            try:
                assembled = _assemble_fail_ssot(
                    goal={
                        "goal_type": payload.get("goal_type"),
                        "goal_text": payload.get("goal_text"),
                        "success_criteria": payload.get("success_criteria"),
                        "goal_context": payload.get("goal_context") or {},
                    },
                    case_id=event_id,
                    qc_task_id=qc_task_id,
                    remediate_task_id=remediation_id,
                    table_name=table,
                    step1_std_rows=payload.get("step1_std_rows") or [],
                    task_ssot=ts_rows,
                    schema_ssot=schema_rows,
                    gate_result="fail",
                )
                payload["plan_steps"] = assembled.get("plan_steps") or []
                payload["step4_delta_rows"] = assembled.get("step4_delta_rows") or []
                payload["step4_summary"] = assembled.get("summary")
                payload["step4_counts"] = assembled.get("counts")
                payload["step4_assembler"] = assembled.get("assembler")
                goal_info["plan_steps"] = payload["plan_steps"]
                goal_info["step4_summary"] = payload.get("step4_summary")
                goal_info["step4_counts"] = payload.get("step4_counts")
            except Exception:
                # assemble failure must not block fault+task creation
                pass
        # FH4: optional STEP2 vision dims when vision_id already known (non-blocking, non-gate)
        # Default: do NOT call Ollama inline (slow); only attach resolve metadata.
        # Full Ollama run via fail_handling.run_fail_vision_step2 / CLI / UI.
        if vision_id is not None:
            payload["vision_id"] = vision_id
            payload["step2_vision_id"] = vision_id
            payload["step2_pending"] = True
            payload["step2_note"] = (
                "vision_id attached; run STEP2 Ollama via "
                "fail_handling.py vision --task-id <remediate> (non-gate)"
            )
            goal_info["step2_pending"] = True
            goal_info["vision_id"] = vision_id
        conn.execute(
            "UPDATE dev_task SET payload_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), remediation_id),
        )
        # best-effort hub link (column may historically FK task_queue; still store id)
        try:
            conn.execute(
                "UPDATE fault_event SET task_id = ? WHERE event_id = ?",
                (remediation_id, event_id),
            )
        except sqlite3.Error:
            pass
    else:
        goal_info = {}

    def _fact(keyword: str, value: object, value_type: str = "string") -> None:
        if value is None:
            return
        if isinstance(value, (list, dict)):
            text = json.dumps(value, ensure_ascii=False)
            value_type = "json"
        else:
            text = str(value)
        conn.execute(
            """
            INSERT INTO fault_event_fact
                (event_id, keyword, value_text, value_type, source)
            VALUES (?, ?, ?, ?, 'schema_qc')
            """,
            (event_id, keyword, text, value_type),
        )

    _fact("table", table)
    _fact("fault_type", "schema_qc_fail")
    _fact("match_ok", "false", "bool")
    _fact("summary", diff.get("summary") or "")
    _fact("missing_columns", diff.get("missing_columns") or [])
    _fact("extra_columns", diff.get("extra_columns") or [])
    _fact("expected_columns", list(expected or []))
    _fact("observed_columns", list(observed.get("column_names") or []))
    _fact("qc_task_id", qc_task_id, "number")
    _fact("qc_run_id", qc_run_id, "number")
    _fact("remediation_task_id", remediation_id, "number")
    _fact("remediation_task_label", remediation_label)
    _fact("browser_url", browser_url)
    _fact("vision_id", vision_id, "number")
    _fact("gate", "pragma_exact_set")
    if goal_info:
        _fact("goal_type", goal_info.get("goal_type"))
        _fact("goal_text", goal_info.get("goal_text"))
        _fact("success_criteria", goal_info.get("success_criteria"))
        _fact("goal_source", goal_info.get("goal_source") or "fail_handling")
        if goal_info.get("step4_summary"):
            _fact("step4_summary", goal_info.get("step4_summary"))
        if goal_info.get("step4_counts"):
            _fact("step4_counts", goal_info.get("step4_counts"))
        if goal_info.get("plan_steps"):
            _fact("plan_steps", goal_info.get("plan_steps"))

    conn.commit()
    return {
        "event_id": event_id,
        "fault_type": "schema_qc_fail",
        "option_id": option_id,
        "remediation_task_id": remediation_id,
        "remediation_task_label": remediation_label,
        "qc_task_id": qc_task_id,
        "table": table,
        "group_id": f"schema_qc:{table}",
        "detect_at": detect_at,
        "goal_type": goal_info.get("goal_type") if goal_info else None,
        "goal_text": goal_info.get("goal_text") if goal_info else None,
        "success_criteria": goal_info.get("success_criteria") if goal_info else None,
        "step4_summary": goal_info.get("step4_summary") if goal_info else None,
        "step4_counts": goal_info.get("step4_counts") if goal_info else None,
        "plan_steps": goal_info.get("plan_steps") if goal_info else None,
        "vision_id": vision_id,
        "step2_pending": goal_info.get("step2_pending") if goal_info else None,
    }


def seed_ssot_defaults(conn: sqlite3.Connection) -> dict:
    """Idempotent seed: local_pc channel, modules, heartbeat_off option + SSOT + solution."""
    out: dict = {
        "channels": 0,
        "modules": 0,
        "options": 0,
        "ssot": 0,
        "solutions": 0,
    }

    def get_or_create_dim(table: str, code: str, name: str, counter_key: str) -> int:
        row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            f"INSERT INTO {table} (code, name) VALUES (?, ?)",
            (code, name),
        )
        out[counter_key] += 1
        return int(cur.lastrowid)

    channel_id = get_or_create_dim("channel", "local_pc", "Local PC", "channels")
    mod_hb = get_or_create_dim(
        "module", "worker_heartbeat", "Worker Heartbeat", "modules"
    )
    get_or_create_dim("module", "ollama", "Ollama LLM", "modules")
    get_or_create_dim("module", "openclaw_gateway", "OpenClaw Gateway", "modules")
    get_or_create_dim("module", "openclaw_companion", "OpenClaw Companion", "modules")
    get_or_create_dim("module", "watchdog", "Watchdog", "modules")
    mod_schema_qc = get_or_create_dim(
        "module", "schema_qc", "Schema QC", "modules"
    )

    opt = conn.execute(
        "SELECT id FROM fault_option WHERE code = ?", ("heartbeat_off",)
    ).fetchone()
    if opt:
        option_id = int(opt[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO fault_option (code, name, channel_id, module_id, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            ("heartbeat_off", "heartbeat OFF", channel_id, mod_hb),
        )
        option_id = int(cur.lastrowid)
        out["options"] += 1

    # schema QC hard-fail option (catalog for fault_event.option_id)
    opt_qc = conn.execute(
        "SELECT id FROM fault_option WHERE code = ?", ("schema_qc_fail",)
    ).fetchone()
    if opt_qc:
        option_qc_id = int(opt_qc[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO fault_option (code, name, channel_id, module_id, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            (
                "schema_qc_fail",
                "Schema QC hard gate fail",
                channel_id,
                mod_schema_qc,
            ),
        )
        option_qc_id = int(cur.lastrowid)
        out["options"] += 1
    out["option_schema_qc_fail_id"] = option_qc_id

    qc_ssot = [
        ("fault_type", "schema_qc_fail", "string", 1.0),
        ("legacy_fault_types", "schema_qc_fail", "string", 0.95),
        ("gate", "pragma_exact_set", "string", 1.0),
        ("match_ok", "false", "bool", 1.0),
        ("pipeline", "B_fail_handling", "string", 0.8),
        (
            "gate_desc",
            "PRAGMA column set must equal expected (exact set)",
            "string",
            0.5,
        ),
        ("not_gate", "MCP shot / api_ok / notify never flip match_ok", "string", 0.5),
        (
            "on_fail",
            "open fault_event + create schema.remediate child under failed QC task",
            "string",
            0.7,
        ),
        (
            "fact_keys",
            "table,missing_columns,extra_columns,qc_task_id,remediation_task_id,qc_run_id,goal_type,gate",
            "string",
            0.9,
        ),
        (
            "goal_types",
            "fix_schema,fix_expected,investigate,fill_ssot,update_ssot,waive",
            "string",
            0.6,
        ),
        (
            "remediate_action",
            "schema.remediate",
            "string",
            0.7,
        ),
    ]
    for keyword, value_text, value_type, weight in qc_ssot:
        exists = conn.execute(
            "SELECT 1 FROM fault_ssot WHERE option_id = ? AND keyword = ?",
            (option_qc_id, keyword),
        ).fetchone()
        if exists:
            # refresh value/weight for catalog truth (idempotent upsert)
            conn.execute(
                """
                UPDATE fault_ssot
                SET value_text = ?, value_type = ?, weight = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE option_id = ? AND keyword = ?
                """,
                (value_text, value_type, weight, option_qc_id, keyword),
            )
            continue
        conn.execute(
            """
            INSERT INTO fault_ssot (option_id, keyword, value_text, value_type, weight)
            VALUES (?, ?, ?, ?, ?)
            """,
            (option_qc_id, keyword, value_text, value_type, weight),
        )
        out["ssot"] += 1

    # schema_qc_fail default solution catalog
    sol_qc = conn.execute(
        """
        SELECT id FROM fault_solution
        WHERE option_id = ? AND title = ?
        """,
        (option_qc_id, "Remediate schema QC hard fail"),
    ).fetchone()
    if not sol_qc:
        steps_qc = (
            "1) Open Task Center remediate child (schema.remediate)\n"
            "2) Confirm goal_type (fix_schema | fix_expected | investigate)\n"
            "3) Optional: STEP2 Ollama on QC PNG (non-gate)\n"
            "4) STEP4 assemble SSOT deltas; apply DDL or expected set\n"
            "5) Re-run schema QC hard gate until match_ok=1\n"
            "6) STEP5 report; keep fault_event open until human resolves"
        )
        conn.execute(
            """
            INSERT INTO fault_solution
                (option_id, title, steps_text, priority, status)
            VALUES (?, ?, ?, 10, 'active')
            """,
            (option_qc_id, "Remediate schema QC hard fail", steps_qc),
        )
        out["solutions"] += 1

    # Pair QC mismatch option (detect-only; never schema gate)
    opt_pair = conn.execute(
        "SELECT id FROM fault_option WHERE code = ?", ("pair_qc_mismatch",)
    ).fetchone()
    if opt_pair:
        option_pair_id = int(opt_pair[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO fault_option (code, name, channel_id, module_id, status)
            VALUES (?, ?, ?, ?, 'active')
            """,
            (
                "pair_qc_mismatch",
                "Pair QC MCP SSOT vs UI mismatch",
                channel_id,
                mod_schema_qc,
            ),
        )
        option_pair_id = int(cur.lastrowid)
        out["options"] += 1
    out["option_pair_qc_mismatch_id"] = option_pair_id
    pair_ssot = [
        ("fault_type", "pair_qc_mismatch", "string", 1.0),
        ("legacy_fault_types", "pair_qc_mismatch", "string", 0.95),
        ("pipeline", "E_pair_qc", "string", 1.0),
        ("gate", "never", "string", 1.0),
        ("detect_only", "true", "bool", 1.0),
        (
            "paths",
            "mcp_ssot vs ui_extract after normalize",
            "string",
            0.9,
        ),
        (
            "fact_keys",
            "register_id,tdd_rule_id,field_name,mcp_raw,mcp_norm,ui_raw,ui_norm,pair_qc_run_id",
            "string",
            0.95,
        ),
        (
            "not_gate",
            "pair fail never flips schema_qc match_ok",
            "string",
            1.0,
        ),
        (
            "on_fail",
            "open fault_event + pair_qc_run + optional qc.pair_verify task",
            "string",
            0.8,
        ),
    ]
    for keyword, value_text, value_type, weight in pair_ssot:
        exists = conn.execute(
            "SELECT 1 FROM fault_ssot WHERE option_id = ? AND keyword = ?",
            (option_pair_id, keyword),
        ).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE fault_ssot
                SET value_text = ?, value_type = ?, weight = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE option_id = ? AND keyword = ?
                """,
                (value_text, value_type, weight, option_pair_id, keyword),
            )
            continue
        conn.execute(
            """
            INSERT INTO fault_ssot (option_id, keyword, value_text, value_type, weight)
            VALUES (?, ?, ?, ?, ?)
            """,
            (option_pair_id, keyword, value_text, value_type, weight),
        )
        out["ssot"] += 1
    sol_pair = conn.execute(
        """
        SELECT id FROM fault_solution
        WHERE option_id = ? AND title = ?
        """,
        (option_pair_id, "Investigate pair QC mismatch"),
    ).fetchone()
    if not sol_pair:
        steps_pair = (
            "1) Open pair_qc_run + both evidences (MCP JSON / UI frame)\n"
            "2) Confirm normalize was applied (phone dashes/spaces/+CC)\n"
            "3) Check register_id + field_tdd_rule expected pattern\n"
            "4) Decide: UI bug vs backend bug vs extract error\n"
            "5) Fix root cause manually or via advanced model — pair QC does not auto-fix\n"
            "6) Re-run pair_qc until match_ok=1; resolve fault_event when done"
        )
        conn.execute(
            """
            INSERT INTO fault_solution
                (option_id, title, steps_text, priority, status)
            VALUES (?, ?, ?, 20, 'active')
            """,
            (option_pair_id, "Investigate pair QC mismatch", steps_pair),
        )
        out["solutions"] += 1

    ssot_rows = [
        ("fault_type", "heartbeat_timeout", "string", 1.0),
        (
            "signal",
            "workers.last_seen_at stale beyond STALE_THRESHOLD_MINUTES",
            "string",
            1.0,
        ),
        ("watchdog_label", "失联", "string", 1.0),
        (
            "typical_cause",
            "host shutdown / WSL-Ollama down / worker process dead / no heartbeat loop",
            "string",
            0.9,
        ),
        (
            "fact_keys",
            "heartbeat_last_seen_at,stale_minutes,worker_id,worker_name,watchdog_message",
            "string",
            0.8,
        ),
        (
            "legacy_fault_types",
            "heartbeat_timeout,crash",
            "string",
            0.95,
        ),
        (
            "not_resolved_by",
            "heartbeat return alone does not resolve fault_event",
            "string",
            1.0,
        ),
    ]
    for keyword, value_text, value_type, weight in ssot_rows:
        exists = conn.execute(
            "SELECT 1 FROM fault_ssot WHERE option_id = ? AND keyword = ?",
            (option_id, keyword),
        ).fetchone()
        if exists:
            conn.execute(
                """
                UPDATE fault_ssot
                SET value_text = ?, value_type = ?, weight = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE option_id = ? AND keyword = ?
                """,
                (value_text, value_type, weight, option_id, keyword),
            )
            continue
        conn.execute(
            """
            INSERT INTO fault_ssot (option_id, keyword, value_text, value_type, weight)
            VALUES (?, ?, ?, ?, ?)
            """,
            (option_id, keyword, value_text, value_type, weight),
        )
        out["ssot"] += 1

    sol = conn.execute(
        """
        SELECT id FROM fault_solution
        WHERE option_id = ? AND title = ?
        """,
        (option_id, "Restore host + Ollama + worker heartbeat"),
    ).fetchone()
    if not sol:
        steps = (
            "1) Confirm PC/WSL powered on\n"
            "2) Start Ollama (port 18803) and pull vision/chat models if needed\n"
            "3) Start OpenClaw Gateway + Companion Local MCP\n"
            "4) Restart worker_heartbeat_service for the offline worker\n"
            "5) Verify workers.last_seen_at updates; keep fault_event open until human resolves"
        )
        conn.execute(
            """
            INSERT INTO fault_solution
                (option_id, title, steps_text, priority, status)
            VALUES (?, ?, ?, 10, 'active')
            """,
            (option_id, "Restore host + Ollama + worker heartbeat", steps),
        )
        out["solutions"] += 1

    out["channel_id"] = channel_id
    out["module_heartbeat_id"] = mod_hb
    out["option_heartbeat_off_id"] = option_id
    return out


def ensure_schema(db_path: str | None = None) -> str:
    path = get_db_path(db_path)
    conn = sqlite3.connect(path)
    try:
        summary = ensure_hub_ssot_schema(conn)
        print("migrate summary:", summary)
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    p = ensure_schema()
    print(f"OK schema ensured: {p}")
