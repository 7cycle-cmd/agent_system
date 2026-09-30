"""DB schema helpers — safe additive migrations for existing agent.db.

Phase 0–1: fault_event ID hub + option/SSOT catalog (no DROP of live data).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Iterable

# The write-site standardiser. Imported lazily inside the writers (see
# `create_dev_task`) so this module keeps working if `no_null` is unavailable in
# an odd import path — but the writers DO use it, because a NULL written here is
# what re-created the NULLs a backfill had already removed.
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


def set_setting(conn: sqlite3.Connection, key: str, value: Any,
                *, commit: bool = True) -> dict[str, Any]:
    """Write a setting value. UPSERT, so a re-write is idempotent.

    THE ONE WRITER for the `settings` table, added 2026-09-29 beside
    `get_setting` so a reader and a writer cannot disagree about the shape. The
    value is stored as TEXT (the column's type), so a caller that reads it back
    gets a string and must parse it — the same rule `get_setting` already
    implies.

    WHY AN UPSERT AND NOT AN INSERT: a setting is a CURRENT value, not an
    append-only log. `INSERT OR REPLACE` keeps exactly one row per key, which is
    what `key TEXT PRIMARY KEY` already promises.
    """
    k = str(key or "").strip()
    if not k:
        raise ValueError("a setting needs a non-empty key")
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now'))", (k, str(value)))
    if commit:
        conn.commit()
    return {"ok": True, "key": k, "value": str(value)}


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
    -- NOT NULL DEFAULT 'NA': an empty value is the explicit NA, so a NULL here
    -- always means the standardiser did not run (see no_null.py).
    -- NOT NOT NULL, and LIVE is right. MEASURED DRIFT (2026-09-29): the declarer
    -- says `notified_at TIMESTAMP NOT NULL DEFAULT 'NA'`, but the comment
    -- IMMEDIATELY ABOVE it says the opposite:
    --     "NOT NULL DEFAULT 'NA': an empty value is the explicit NA, so a NULL
    --      here always means the standardiser did not run (see no_null.py)"
    -- A column that must record "the standardiser did not run" cannot be NOT NULL.
    -- LIVE declares it bare `TIMESTAMP`, which is the form that matches the
    -- comment, and no_null decides the column (KEEP_NULL).
    notified_at     TIMESTAMP DEFAULT 'NA',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    vision_id       INTEGER,
    option_id       INTEGER,
    solution_id     INTEGER,
    match_score     REAL,
    match_status    TEXT,
    ssot_prompt_json TEXT,
    -- REPEATS THE COLUMN THAT ALREADY CARRIES IT. MEASURED (2026-09-29): this
    -- column was UNIQUE on its own line, and the declaration restated it, so the
    -- FRESH table carried `UNIQUE(event_id)` AND `UNIQUE(event_id)` while LIVE
    -- carried one. A duplicated constraint is not a stronger one.
    solution_summary TEXT,
    -- ONLY `event_id` KEEPS A NATIVE FK. THE POLICY: a native FK may only attach
    -- to the parent's PRIMARY KEY, and only for a MANDATORY + LOAD-BEARING
    -- reference. These three are LOOSE -- `vision_id` is NULL on 6/6 rows,
    -- `option_id` 5/6, `solution_id` 5/6 -- and LIVE carried
    -- `... ON DELETE SET NULL` on all three, so the FK never constrained an
    -- insert. The repo's own `null_columns` standard classifies all three
    -- `KEEP_NULL`. They are removed, and the reference moves to the business
    -- layer (factor 8).
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

# ---------------------------------------------------------------------------
# ONTOLOGY REGISTRY DECLARATIONS - READ from `init_ontology_registry.sql`.
#
# WHY THIS CONSTANT EXISTS (measured 2026-09-25)
# ---------------------------------------------
# `logic_generator.declared_shape` builds a table's expectation from "the
# `CREATE TABLE` text in a `db_schema.*_DDL` constant". It found NONE for the 7
# ontology registries, so `spec_from_table` REFUSED, and `register_fill` recorded
# **109 `PARENT_NOT_FOUND` refusals** - the filler correctly declining to invent a
# parent that nothing in `db_schema` declared.
#
# The declarations DID exist; they were in `init_ontology_registry.sql`. The
# measurement that decides this constant's shape: those 7 `CREATE TABLE`
# statements ALSO appear, re-typed, in SEVEN `_proof_*.py` files. So the schema
# already had EIGHT copies, and adding a ninth literal here would have been the
# "second copy of one fact" defect this repo keeps removing.
#
# So this constant READS the one authoritative file rather than restating it. The
# `_proof_*` copies are NOT touched here - cleaning those is its own plan.
#
# A MISSING FILE IS NAMED, NOT SWALLOWED: `_ONTOLOGY_SQL_READ_ERROR` records why,
# so "no declaration" and "the file could not be read" stay distinguishable.
_ONTOLOGY_SQL_PATH = os.path.join(BASE_DIR, "init_ontology_registry.sql")
_ONTOLOGY_SQL_READ_ERROR = ""
try:
    with open(_ONTOLOGY_SQL_PATH, "r", encoding="utf-8") as _f:
        ONTOLOGY_REGISTRY_DDL = _f.read()
except OSError as _e:
    ONTOLOGY_REGISTRY_DDL = ""
    _ONTOLOGY_SQL_READ_ERROR = "%s: %s" % (type(_e).__name__, _e)

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

# 🔴 THESE FOUR CONSTANTS WERE MISSING, AND THAT IS THE FRESH-BUILD DEFECT.
# MEASURED 2026-09-28: `ensure_schema()` on a FRESH path failed with
# `no such table: main.task_queue` (and, before that, `main.fault_event` and
# `main.fault_analysis`). The cause is ONE defect with FOUR faces: `db_schema.py`
# REFERENCES these tables but never CREATES them.
#
#   workers          <- FOREIGN KEY in FAULT_EVENT_DDL / WORKER_HEARTBEAT_DDL
#   task_queue       <- FOREIGN KEY in FAULT_EVENT_DDL / TASK_LIFECYCLE_LOG_DDL
#   watchdog_log     <- seeded by seed_task_center_defaults
#   worker_heartbeat <- seeded by seed_task_center_defaults
#
# They were created ONLY by `init_db.sql` (the greenfield twin) and by
# `db_schema_generated_ddl.py` — a GENERATED module that NOTHING imported (grep
# for `db_schema_generated_ddl` found only comments and a proof that read it as
# TEXT). So the two paths disagreed: `init_db.sql` could build a fresh database,
# `ensure_schema()` could not.
#
# 🔴 THAT MODULE IS NOW DELETED (2026-09-29). MEASURED before deleting it: 30 of
# its 31 `CREATE TABLE` constants declare a table ANOTHER module also creates, so
# it was redundant — and because NOTHING imported it, its own docstring claim
# ("the declared shape and the live shape agree BY CONSTRUCTION") was FALSE: a
# declaration that is never executed cannot agree with anything. The one unique
# constant (`WORKER_JOB_BINDING_DDL`) was MOVED into this module, above.
#
# The shapes below are COPIED FROM THE LIVE TABLES (`sqlite_master.sql`), so the
# declared shape and the live shape agree BY CONSTRUCTION — and here that claim is
# TRUE, because `ensure_hub_ssot_schema` EXECUTES them.
WORKERS_DDL = """
CREATE TABLE IF NOT EXISTS workers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'idle',
    group_name   TEXT    NOT NULL DEFAULT 'default',
    last_seen_at TIMESTAMP,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name, group_name)
);
"""

TASK_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS task_queue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','running','pass','fail')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

WATCHDOG_LOG_DDL = """
CREATE TABLE IF NOT EXISTS watchdog_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id  INTEGER NOT NULL,
    message    TEXT    NOT NULL,
    level      TEXT    NOT NULL DEFAULT 'info',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

WORKER_HEARTBEAT_DDL = """
CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id       INTEGER NOT NULL,
    screenshot_path TEXT,
    business_alive  INTEGER,
    pid             INTEGER,
    heartbeat_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (worker_id) REFERENCES workers (id) ON DELETE CASCADE
);
"""

# 🔴 THE ONE CONSTANT THAT WAS UNIQUE TO THE DELETED GENERATED MODULE.
#
# MEASURED 2026-09-29, before deleting `db_schema_generated_ddl.py`: of its 31
# `CREATE TABLE` constants, **30 declare a table that ANOTHER module also
# creates** and only ONE does not — this one. So the module was 30/31 redundant,
# and deleting it would have silently dropped `worker_job_binding` from every
# build path. The constant is therefore MOVED here, not discarded.
#
# WHY THE TABLE IS KEPT AT ALL (it is empty and has no reader):
# `activation_gate.py:207` CLASSIFIES it as a MAPPING. A table the taxonomy has
# decided about is "not yet used", not "abandoned" — the distinction the repo
# already draws for `pair_qc_case` (0 rows, 0 readers, 0 writers, superseded →
# DROPPED) versus this one (0 rows, 0 readers, but CLASSIFIED → KEPT).
#
# It is registered: `db_table_registry.worker_job_binding` is_active=1,
# cite_ref `job_registry.py:51`.
#
# The shape is COPIED FROM THE LIVE TABLE (`sqlite_master.sql`), so the declared
# shape and the live shape agree BY CONSTRUCTION.
WORKER_JOB_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS worker_job_binding (
    binding_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id    INTEGER NOT NULL,
    job_id       INTEGER NOT NULL,
    task_type    TEXT    NOT NULL,
    workflow_key TEXT    NOT NULL,
    gate_ref     TEXT    NOT NULL,
    evidence_cmd TEXT    NOT NULL,
    cite_ref     TEXT    NOT NULL,
    why          TEXT,
    is_active    INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (worker_id, job_id)
);
"""

# 🔴 THESE SEVEN CONSTANTS WERE MISSING, AND THAT IS THE FRESH-BUILD DEFECT.
# MEASURED 2026-09-29: after `ensure_schema()` plus EVERY owner entry point, TEN
# tables the LIVE database has were still absent from a fresh build. SEVEN of
# them are LIVE with real data and their declared shapes match live EXACTLY:
#
#     agent_provider                4 rows   10/10 columns, no drift
#     alert_history                 3 rows    8/8  columns, no drift
#     doubao_send_log             155 rows   13/13 columns, no drift
#     purge_ledger               2159 rows    8/8  columns, no drift
#     schema_migration_log          5 rows    4/4  columns, no drift
#     skill_task_id_registry        2 rows   10/10 columns, no drift
#     terminology_name_unify_deleted 1 row    6/6  columns, no drift
#
# Each was created ONLY by its own module's entry point, and each of those entry
# points is UNREACHABLE from a bootstrap:
#
#   * `db_schema_generated_ddl.py` was a GENERATED module that NOTHING imported
#     (agent_provider). It is DELETED as of 2026-09-29; its one unique constant
#     (`WORKER_JOB_BINDING_DDL`) was moved into this module.
#   * `skill_library_api.ensure_alert_history_table()` and
#     `f_doubao_send.ensure_log_table()` take NO ARGUMENT — they open the LIVE
#     database themselves, so they cannot be pointed at a fresh one.
#   * `_purge_retired_rows.py`, `split_skill_registry.py`,
#     `skill_task_id_allocator.py` and `name_unify.py` expose NO `ensure_*`
#     function at all.
#
# The shapes below are COPIED FROM THE LIVE TABLES (`sqlite_master.sql`), so the
# declared shape and the live shape agree BY CONSTRUCTION — and here that claim is
# TRUE, because `ensure_task_center_schema` EXECUTES them.
AGENT_PROVIDER_DDL = """
-- THE PK / NOT NULL / UNIQUE / FK ARE DECLARED, not inherited by accident.
--
-- MEASURED DRIFT (2026-09-29, found by `scripts/schema_gate.py`): this block
-- declared 10 BARE-COLUMN rows while LIVE carried
--     id INTEGER PRIMARY KEY AUTOINCREMENT
--     provider / llm_id / price_unit / is_active NOT NULL
--     UNIQUE (provider, llm_id)
--     llm_id INTEGER NOT NULL REFERENCES llm_model(id)   <- INLINE, so a
--         `FOREIGN KEY (...)` search cannot see it; read it with PRAGMA
-- A fresh build therefore produced a table with NO PRIMARY KEY while LIVE had
-- one. The FK is MANDATORY + LOAD-BEARING (4 rows, 0 NULL, resolves 100%), so
-- the policy says it KEEPS a native FK -- and the parent column is the parent's
-- PRIMARY KEY, so the policy is satisfied without a rebuild.
CREATE TABLE IF NOT EXISTS agent_provider (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    provider                 TEXT    NOT NULL,
    llm_id                   INTEGER NOT NULL REFERENCES llm_model (id),
    price_per_1k             REAL,
    price_unit               TEXT    NOT NULL DEFAULT 'per_1k_tokens',
    api_base                 TEXT,
    api_key_ref              TEXT,
    is_active                INTEGER NOT NULL DEFAULT 1,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    price_completion_per_1k  REAL,
    UNIQUE (provider, llm_id)
);
"""

ALERT_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS alert_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT,
    level       TEXT,
    message     TEXT,
    fired_at    TIMESTAMP,
    channel     TEXT,
    success     INTEGER,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_history_fired
    ON alert_history (fired_at DESC);
"""

DOUBAO_SEND_LOG_DDL = """
CREATE TABLE IF NOT EXISTS doubao_send_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT,
    step_no     INTEGER NOT NULL,
    step_name   TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'UNKNOWN'
                CHECK (status IN ('PASS', 'FAIL', 'UNKNOWN')),
    detail      TEXT,
    x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
    cx INTEGER, cy INTEGER,
    input_type  TEXT    NOT NULL DEFAULT 'text'
                CHECK (input_type IN ('text', 'icon', 'select', 'ratio')),
    evidence_id TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

PURGE_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS purge_ledger (
    purge_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    src_table    TEXT    NOT NULL,
    src_pk       TEXT    NOT NULL,
    src_key      TEXT,
    retired_when TEXT,
    purged_at    TEXT    NOT NULL,
    reason       TEXT    NOT NULL,
    task_id      TEXT    NOT NULL,
    UNIQUE(src_table, src_pk)
);
"""

SCHEMA_MIGRATION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS schema_migration_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    migration   TEXT    NOT NULL,
    detail_json TEXT    NOT NULL DEFAULT '{}',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

SKILL_TASK_ID_registry_DDL = """
CREATE TABLE IF NOT EXISTS skill_task_id_registry (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name      TEXT    NOT NULL,
    task_name_norm TEXT    NOT NULL,
    catalog_id     INTEGER NOT NULL,
    subcatalog_id  INTEGER NOT NULL,
    version        INTEGER NOT NULL DEFAULT 1,
    task_full_id   TEXT    NOT NULL UNIQUE,
    status         TEXT    NOT NULL DEFAULT 'active',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_task_id_registry_name
  ON skill_task_id_registry (task_name_norm);
CREATE INDEX IF NOT EXISTS idx_task_id_registry_catalog
  ON skill_task_id_registry (catalog_id, subcatalog_id);
"""

TERMINOLOGY_NAME_UNIFY_DELETED_DDL = """
CREATE TABLE IF NOT EXISTS terminology_name_unify_deleted (
    old_term_id   INTEGER,
    old_term_key  TEXT,
    old_definition TEXT,
    correction    TEXT,
    cite_ref      TEXT,
    deleted_at    TEXT
);
"""

# 🔴 `rate_limit` IS A HALF-BUILT FEATURE, AND THE SCHEMA HALF IS REAL.
# MEASURED 2026-09-29: the table is EMPTY (0 rows) and has NO INSERT and NO
# SELECT anywhere — but it HAS a live DELETE path:
#
#     skill_library_api.py:4140  prune_rate_limit()
#     skill_library_api.py:4150  "DELETE FROM rate_limit WHERE hit_ts <= ?"
#     skill_library_api.py:4185  result["rate_limit"] = prune_rate_limit()
#
# and `RATE_LIMIT_WINDOW_SEC = 60` (`skill_library_api.py:4088`) documents the
# intended sliding window. So the design is: INSERT a row per request, prune the
# rows outside the window, count the rows inside it. The PRUNE exists; the
# INSERT does not. That is a FEATURE gap, not a schema gap — the table is part of
# the schema and is wired here. The missing INSERT is recorded as a separate
# finding, not silently fixed by deleting the table.
RATE_LIMIT_DDL = """
CREATE TABLE IF NOT EXISTS rate_limit (
    ip         TEXT NOT NULL,
    policy     TEXT NOT NULL DEFAULT 'default',
    hit_ts     REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rate_limit_ip_ts
  ON rate_limit (ip, hit_ts);
"""

# 🔴 THE (ip, policy, hit_ts) INDEX IS ITS OWN CONSTANT, AND THE ORDERING IS THE
# REASON (2026-09-29).
#
# MEASURED, found by my OWN proof check: putting this `CREATE INDEX` in
# `RATE_LIMIT_DDL` broke the schema build on any EXISTING database with
#
#     sqlite3.OperationalError: no such column: policy
#
# because `CREATE TABLE IF NOT EXISTS` does NOT add a column, so at the moment the
# DDL list runs the table exists WITHOUT `policy`, and the index names a column
# that is not there yet. The additive migration that adds it runs AFTER the DDL
# list.
#
# This is the SAME defect class already fixed in `ensure_hub_ssot_schema`, where
# `INDEX_DDL` ran unconditionally while the tables it indexed did not exist yet.
# An index must be created AFTER the column it names.
RATE_LIMIT_POLICY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_rate_limit_ip_policy_ts
  ON rate_limit (ip, policy, hit_ts);
"""

# 🔴 `policy` IS ADDITIVE, AND THAT IS THE WHOLE POINT (2026-09-29).
#
# The rate limiter is now TWO-LAYERED (a strict layer for the expensive
# LLM/vision routes, a loose layer for everything else), so a hit must say WHICH
# policy it consumed. Without this, one shared counter means a cheap read can
# exhaust the expensive budget — the defect OWASP API4:2023 names by requiring
# "rate limiting should be fine tuned based on the business needs. Some API
# Endpoints might require stricter policies."
#
# IT IS AN ADDITIVE COLUMN, NOT A NEW TABLE, and the DEFAULT makes it safe: an
# existing row was written before policies existed, so `'default'` is the honest
# label for it. `CREATE TABLE IF NOT EXISTS` does NOT add a column to an existing
# table — the trap this module already records twice (`llm_model`,
# `llm_service.needs_flag`) — so the migration below is REQUIRED, not optional.
RATE_LIMIT_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("policy", "TEXT NOT NULL DEFAULT 'default'"),
)

# 🔴 THIS CONSTANT WAS MISSING, AND THAT IS THE FRESH-BUILD DEFECT.
# MEASURED 2026-09-28: `fault_event` was created ONLY by `init_db.sql:157` (the
# greenfield twin) and by `db_schema_generated_ddl.py:437` — a GENERATED module
# that NOTHING imported (DELETED 2026-09-29; see the note above
# `WORKERS_DDL`). `db_schema.py` referenced `fault_event` in EIGHT places
# (FOREIGN KEYs in FAULT_EVENT_FACT_DDL / FAULT_OPTION_PENDING_DDL /
# FAULT_SSOT_REVISION_DDL, and INDEX_DDL:269-276) but never CREATED it. So
# `ensure_schema()` on a FRESH path failed:
#
#     FRESH BUILD FAILS: OperationalError no such table: main.fault_event
#
# The shape below is COPIED FROM THE LIVE TABLE (`PRAGMA table_info(fault_event)`
# + `sqlite_master.sql`), so the declared shape and the live shape agree BY
# CONSTRUCTION — and here that claim is TRUE, because `ensure_hub_ssot_schema`
# EXECUTES it.
FAULT_EVENT_DDL = """
CREATE TABLE IF NOT EXISTS fault_event (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id         INTEGER,
    group_id          TEXT    NOT NULL,
    detect_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fault_type        TEXT    NOT NULL,
    evidence_img_path TEXT,
    evidence_error    TEXT,
    status            TEXT    NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open','resolved')),
    resolved_at       TIMESTAMP,
    channel_id        INTEGER,
    module_id         INTEGER,
    vision_id         INTEGER,
    fault_analysis_id INTEGER,
    task_id           INTEGER,
    option_id         INTEGER,
    solution_id       INTEGER,
    FOREIGN KEY (worker_id) REFERENCES workers (id) ON DELETE SET NULL,
    FOREIGN KEY (channel_id) REFERENCES channel (id) ON DELETE SET NULL,
    FOREIGN KEY (module_id) REFERENCES module (id) ON DELETE SET NULL,
    FOREIGN KEY (vision_id) REFERENCES vision_asset (id) ON DELETE SET NULL,
    FOREIGN KEY (task_id) REFERENCES task_queue (id) ON DELETE SET NULL,
    FOREIGN KEY (option_id) REFERENCES fault_option (id) ON DELETE SET NULL,
    FOREIGN KEY (solution_id) REFERENCES fault_solution (id) ON DELETE SET NULL
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
    -- NOT NULL REMOVED 2026-09-29 (RING 5, D4). MEASURED: a FRESH 
    -- `fault_option_pending` declared `resolved_at TIMESTAMP NOT NULL DEFAULT
    -- 'NA'` while LIVE declares it NULLABLE. The declared DEFAULT is what made
    -- the tightening look harmless, but a DEFAULT does not backfill a column
    -- SQLite never added, and `ALTER TABLE` cannot add NOT NULL at all. The
    -- comparison — not an opinion — is what settles this: the declarer was the
    -- stricter side with no measurement behind it.
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


# WHY THIS EXISTS (2026-09-28). SQLite cannot BIND an identifier — `SELECT ... FROM
# ?` is not valid SQL — so a helper that takes a table name MUST interpolate it,
# and `bandit` B608 flags every such site (24 in this file). MEASURED, and this is
# the distinction that decides the fix: the risk depends on WHERE the name comes
# from.
#
#   * a LITERAL tuple in the same function (`for table in ("a","b")`) cannot be
#     attacker-controlled — the interpolation is safe BY CONSTRUCTION.
#   * a PARAMETER (`table: str` on a public function) CAN be, and 6 of the 24
#     findings are exactly that (`get_or_create_dim`, `_dim_id_by_code`).
#
# A generic guard was NOT written, deliberately. MEASURED: the repo already owns
# `_table_exists` (above, bound-parameterised) and `structure_contract
# .assert_table_write_allowed`. A THIRD validator would be a second truth about
# the same fact — the drift this repo keeps paying for. So this helper DELEGATES,
# and it adds the one thing neither has: it runs at the INTERPOLATION SITE, so a
# future caller passing a parameter cannot inject without the name first being
# proven to be a real table.
#
# The name must ALSO be a plain identifier, because `_table_exists` is a lookup
# and an attacker-chosen name that happens to exist is still not one this code
# meant to touch. Both conditions, stated once.
_IDENT_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeTableName(ValueError):
    """A table name that is not a bare identifier naming a REAL table."""


def _safe_table_name(conn: sqlite3.Connection, table: str) -> str:
    """Return `table` only if it is a bare identifier AND a real table.

    REFUSES loudly rather than sanitising: a table name that fails either test is
    a programming error (or an injection attempt), and silently quoting it would
    hide both.
    """
    name = str(table or "").strip()
    if not _IDENT_OK.match(name):
        raise UnsafeTableName(
            "refusing to interpolate %r: a table name must be a bare SQL "
            "identifier ([A-Za-z_][A-Za-z0-9_]*), so it cannot carry a "
            "statement" % (table,))
    if not _table_exists(conn, name):
        raise UnsafeTableName(
            "refusing to interpolate %r: it names no existing table, so the "
            "caller is not touching a table this code knows (delegate: "
            "_table_exists)" % (table,))
    return name


def _add_columns_if_missing(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[tuple[str, str]],
) -> list[str]:
    """ALTER TABLE ADD COLUMN for each missing column. Returns added names.

    WHY THE COLUMN NAME IS VALIDATED HERE, AND NOT BY `_safe_table_name`
    ----------------------------------------------------------------
    MEASURED 2026-09-28 by factor `sql_construction_not_string_built`: this was
    the ONLY parameter-driven SQL site left inside an allowlisted file, because
    the TABLE name is already proven by the bound-parameter `_table_exists` call
    below. But `ALTER TABLE ... ADD COLUMN {name} {decl}` interpolates TWO more
    things that bound parameters CANNOT carry (SQLite refuses `ADD COLUMN ?`),
    and `name`/`decl` come from the CALLER:

        _add_columns_if_missing(conn, "source", SOME_COLUMN_LIST)

    A `name` such as `x TEXT; DROP TABLE chat_main; --` would be executed, so
    this is a REAL vector — not the syntactic-only red that B608 usually is.
    The guard below is therefore a genuine fix, and it is checked PER COLUMN so
    a bad entry is refused before ANY statement runs.
    """
    # MEASURED 2026-09-28: 13 of the 94 declarations in this file legitimately
    # contain a QUOTE — `TEXT NOT NULL DEFAULT 'NA'`, `DEFAULT '{}'`. So a quote
    # rule would break the migration it is supposed to protect. The FIRST
    # version of this guard refused a quote and was therefore WRONG; the honest
    # rule is the STATEMENT SEPARATOR only, because a `;` is what turns a
    # declaration into a second statement, while a lone quote merely fails to
    # parse (an error, not an injection).
    if not _IDENT_OK.match(str(table).strip()):
        raise UnsafeTableName(
            "refusing to interpolate table %r: a table name must be a bare SQL "
            "identifier ([A-Za-z_][A-Za-z0-9_]*) so it cannot carry a statement"
            % (table,))
    safe = str(table).strip()
    # The GRACEFUL path is preserved: the original helper returned [] for a
    # table that does not exist, and 23 call sites rely on that. Validating the
    # identifier must not change that behaviour, so the order is
    # identifier-check (raise) THEN existence-check (return []).
    if not _table_exists(conn, safe):
        return []
    existing = _table_columns(conn, safe)
    added: list[str] = []
    for name, decl in columns:
        col = str(name or "").strip()
        if not _IDENT_OK.match(col):
            raise UnsafeTableName(
                "refusing to interpolate column %r on %s: a column name must be "
                "a bare SQL identifier ([A-Za-z_][A-Za-z0-9_]*) so it cannot "
                "carry a statement" % (name, safe))
        if col in existing:
            continue
        # `decl` is a TYPE clause (e.g. "TEXT NOT NULL DEFAULT 'NA'"), which is
        # the one part of an ADD COLUMN that cannot be a parameter.
        d = str(decl or "").strip()
        if ";" in d:
            raise UnsafeTableName(
                "refusing to interpolate declaration %r for %s.%s: an ADD COLUMN "
                "type clause may not contain a statement separator" % (decl,
                                                                       safe, col))
        conn.execute(f"ALTER TABLE {safe} ADD COLUMN {col} {d}")
        added.append(col)
    return added


def _rebuild_worker_job_binding_if_needed(conn: sqlite3.Connection) -> dict:
    """Rebuild `worker_job_binding` onto its DECLARED shape, ONCE.

    WHY A REBUILD AND NOT AN ALTER (RING 5, D3/D5, 2026-09-29)
    -------------------------------------------------------
    MEASURED: LIVE carries `binding_id INTEGER PRIMARY KEY AUTOINCREMENT`, eight
    `NOT NULL` columns and `UNIQUE (worker_id, job_id)`. The DECLARATION was
    twelve bare columns with none of them — because the constant was COPIED FROM
    `sqlite_master.sql`, which for this table had itself been created from an
    older, weaker statement.

    SQLite cannot add a PRIMARY KEY or a `NOT NULL` to an existing table with
    `ALTER TABLE`, and `UNIQUE` could only be added as an index (which would not
    be the declared shape either). So the table is rebuilt from the SSOT.

    SAFETY, MEASURED BEFORE ANYTHING RUNS: the table has **0 rows**, so the copy
    cannot lose one. The guard is on the table's OWN shape, not on a version
    number, so it is a no-op on a database that is already correct and cannot run
    twice into a different state.
    """
    if not _table_exists(conn, "worker_job_binding"):
        return {"ok": False, "reason": "table absent"}
    have_pk = any(r[5] for r in conn.execute("PRAGMA table_info(worker_job_binding)"))
    have_uq = any(ix[2] for ix in conn.execute("PRAGMA index_list(worker_job_binding)"))
    if have_pk and have_uq:
        return {"ok": True, "rebuilt": False, "reason": "already on the declared shape"}
    rows = conn.execute("SELECT COUNT(*) FROM worker_job_binding").fetchone()[0]
    if rows:
        # REFUSES rather than copying: a rebuild is only safe when it cannot drop
        # a row, and this table was measured at 0. If it ever grows, the tool
        # stops and a human decides — it never deletes data to fit a declaration.
        return {"ok": False, "rebuilt": False,
                "reason": "REFUSED: %d row(s) present; a rebuild is only proven "
                          "safe at 0 rows" % rows}
    conn.execute("ALTER TABLE worker_job_binding RENAME TO _dd_old_worker_job_binding")
    conn.executescript(WORKER_JOB_BINDING_DDL)
    conn.execute("DROP TABLE _dd_old_worker_job_binding")
    return {"ok": True, "rebuilt": True, "rows": 0}


def _rebuild_llm_route_provider_transport_check(
        conn: sqlite3.Connection) -> dict:
    """Widen `llm_route_provider.transport`'s CHECK to admit `cli`, ONCE.

    WHY A REBUILD AND NOT AN ALTER (2026-09-29)
    -------------------------------------------
    MEASURED: the LIVE table was created by `ALTER TABLE ... ADD COLUMN
    transport TEXT NOT NULL DEFAULT 'http'` (the additive migration), so it
    carries NO CHECK constraint at all — while the DECLARATION
    (`LLM_SERVICE_PROVIDER_DDL`) declares
    `CHECK (transport IN ('http', 'python'))`. A FRESH build therefore produces a
    table that REFUSES `cli`, and the live table ACCEPTS it. That is the
    half-migration this module already records twice (`llm_model`,
    `llm_service.needs_flag`): the DDL and the live table disagree, and the
    disagreement is invisible until someone inserts the value the DDL forbids.

    SQLite cannot add or widen a CHECK with `ALTER TABLE`, so the table is
    rebuilt from the SSOT — the same recipe `_rebuild_wording_registry_unique`
    uses, and for the same reason.

    SAFETY, MEASURED BEFORE ANYTHING RUNS:
      * the table has 7 rows, so the copy is a straight SELECT of shared columns
      * NO table references `llm_route_provider` (measured: zero children), so
        the rename cannot repoint a child FK
      * the guard is on the table's OWN shape (does its SQL mention `cli`?), so
        it is a no-op on a database that is already correct and cannot run twice
        into a different state
    """
    if not _table_exists(conn, "llm_route_provider"):
        return {"ok": False, "reason": "table absent"}
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND "
        "name='llm_route_provider'").fetchone()
    ddl = str(sql[0] or "") if sql else ""
    # Already widened (a fresh build, or a previous run of this function).
    if "'cli'" in ddl or '"cli"' in ddl:
        return {"ok": True, "rebuilt": False,
                "reason": "transport CHECK already admits cli"}
    rows = conn.execute("SELECT COUNT(*) FROM llm_route_provider").fetchone()[0]
    tmp = "_dd_old_llm_route_provider"
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    try:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        conn.execute("DROP TABLE IF EXISTS %s" % tmp)
        conn.execute("ALTER TABLE llm_route_provider RENAME TO %s" % tmp)
        conn.executescript(LLM_SERVICE_PROVIDER_DDL)
        shared = [c for c in _table_columns(conn, tmp)
                  if c in _table_columns(conn, "llm_route_provider")]
        if not shared:
            raise RuntimeError(
                "llm_route_provider rebuild: no shared column between the legacy "
                "table and the canonical DDL; refusing to drop the data.")
        conn.execute(
            "INSERT INTO llm_route_provider (%s) SELECT %s FROM %s"
            % (", ".join(shared), ", ".join(shared), tmp))
        conn.execute("DROP TABLE %s" % tmp)
        conn.commit()
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")
    moved = conn.execute("SELECT COUNT(*) FROM llm_route_provider").fetchone()[0]
    return {"ok": True, "rebuilt": True, "rows_before": int(rows),
            "rows_after": int(moved),
            "note": "transport CHECK now admits 'cli'"}


def _backfill_chat_hash_recomputed(conn: sqlite3.Connection) -> dict:
    """Backfill `chat_hash_recomputed` = sha256("<chat_id>|<session_id>").

    Why: after chat_id became an INTEGER id, `chat_hash` in legacy rows still
    holds the OLD formula sha256(sha256(session_id)|session_id) — i.e. a hash
    built when chat_id WAS the sha256. That stale value is what caused a real
    misread (78760aed... was mistaken for a sha256).

    This adds the correct pair key in a SEPARATE, read-only column and never
    touches `chat_hash`, so audit history stays intact (append-only rule).

    Idempotent: only rewrites rows whose recomputed value is missing/different.
    Never touches chat_hash.
    """
    info: dict = {"log_rows": 0, "center_rows": 0, "main_rows": 0,
                  "skipped_no_key": 0}

    # ---- `chat_main` IS THE TABLE THAT OWNS THE STALE VALUE --------------
    #
    # MEASURED (2026-09-25): this loop covered `chat_identity_log` and
    # `chat_center_message` but NOT `chat_main` — the table whose `chat_hash`
    # actually goes stale. MEASURED: 53 of 58 non-NULL `chat_hash` values were
    # produced by `chat_pair_hash(id, session_id)` instead of
    # `chat_pair_hash(chat_id, session_id)` — written BEFORE `chat_id` existed,
    # and never recomputed after the backfill.
    #
    # The consequence was a FALSE claim: "the pair key went 1:1 -> 1:N" while
    # EVERY `chat_hash` still mapped to exactly ONE `chat_id`. This is the SAME
    # design as the other two tables (a separate, read-only column; `chat_hash`
    # is never touched, so audit history stays intact).
    #
    # `chat_main`'s primary key is `id`, and its pair is `chat_id | session_id`.
    for table, key in (
        ("chat_identity_log", "log_rows"),
        ("chat_center_message", "center_rows"),
        ("chat_main", "main_rows"),
    ):
        if not _table_exists(conn, table):
            continue
        cols = _table_columns(conn, table)
        if "chat_hash_recomputed" not in cols:
            continue
        has_session = "session_id" in cols
        if not has_session:
            continue
        rows = conn.execute(
            f"SELECT id, chat_id, session_id, chat_hash_recomputed FROM {table}"
        ).fetchall()
        for r in rows:
            rid = r[0]
            chat_id = r[1]
            session_id = r[2]
            current = r[3]
            if chat_id is None or session_id is None or str(session_id) == "":
                info["skipped_no_key"] += 1
                continue
            want = hashlib.sha256(f"{chat_id}|{session_id}".encode("utf-8")).hexdigest()
            if current == want:
                continue
            conn.execute(
                f"UPDATE {table} SET chat_hash_recomputed = ? WHERE id = ?",
                (want, rid),
            )
            info[key] += 1
    return info


def _correct_chat_hash_derived_from(conn: sqlite3.Connection) -> dict:
    """Correct the LIVE `derived_column_registry` row for `chat_main.chat_hash`.

    Why: the seed uses `INSERT OR IGNORE`, so a row written with the OLD
    `derived_from` text is never updated by re-running the seed. MEASURED
    (2026-09-26): the live row still said `id + '|' + session_id` while
    `DERIVED_COLUMN_SEED` had already been corrected to
    `chat_id + '|' + session_id`.

    A declaration that names the wrong input is worse than no declaration: it
    makes the wrong value authoritative. This migration makes the LIVE row agree
    with the corrected seed.

    Idempotent: only rewrites a row whose `derived_from` differs from the seed.
    """
    info: dict = {"corrected": 0, "already_correct": 0, "missing": 0}
    if not _table_exists(conn, "derived_column_registry"):
        info["missing"] = 1
        return info
    for tbl, col, kind, src in DERIVED_COLUMN_SEED:
        row = conn.execute(
            "SELECT derived_from FROM derived_column_registry "
            "WHERE table_name = ? AND column_name = ?",
            (tbl, col),
        ).fetchone()
        if row is None:
            info["missing"] += 1
            continue
        if str(row[0]) == src:
            info["already_correct"] += 1
            continue
        conn.execute(
            "UPDATE derived_column_registry SET derived_from = ?, kind = ? "
            "WHERE table_name = ? AND column_name = ?",
            (src, kind, tbl, col),
        )
        info["corrected"] += 1
    return info


def _migrate_chat_identity_to_int(conn: sqlite3.Connection) -> dict:
    """Move chat identity from TEXT sha256 to INTEGER chat_main.id.

    Why: chat_id must be an auto-increment ID (DB-driven), not the content
    hash. sha256(session_id) is a hash, so it belongs in its own column.

    Steps:
    1. Register every legacy chat_id TEXT value in chat_main_hash so the old
       values stay resolvable (FK-valid) and never collide with new int ids.
    2. For log/center rows still holding TEXT chat_id: resolve/create the
       chat_main row for that session_id, move the old text into `sha256`,
       and set chat_id to the new INTEGER id.
    3. Backfill sha256 from chat_main by session_id (repairs rows where the
       migration previously overwrote the hash with an id).

    Idempotent: an existing non-empty sha256 is never overwritten by an id.
    """
    info: dict = {"mapped_hashes": 0, "log_rows": 0, "center_rows": 0, "sha_repaired": 0}
    if not _table_exists(conn, "chat_main_hash"):
        return info

    # 1. Legacy chat_id (text) registry -> hash bridge
    if _table_exists(conn, "chat_id"):
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO chat_main_hash (hash)
            SELECT chat_id FROM chat_id
            WHERE chat_id IS NOT NULL AND chat_id <> ''
            """
        )
        info["mapped_hashes"] = int(cur.rowcount or 0)

    def _looks_like_id(value) -> bool:
        """True if the value is an integer id (NOT a sha256 hex string)."""
        if value is None:
            return True
        s = str(value).strip()
        if not s:
            return True
        return s.isdigit() and len(s) < 30

    def _main_id_for(session_id: str | None, old_hash: str | None) -> int | None:
        """Find/create the chat_main row for a session; bridge old hash."""
        if not session_id:
            return None
        row = conn.execute(
            "SELECT id FROM chat_main WHERE session_id=?", (session_id,)
        ).fetchone()
        if row:
            mid = int(row[0])
            # keep the real hash present on chat_main
            if old_hash and not _looks_like_id(old_hash):
                conn.execute(
                    """
                    UPDATE chat_main SET sha256=?
                     WHERE id=? AND (sha256 IS NULL OR sha256='' OR sha256=?)
                    """,
                    (old_hash, mid, str(mid)),
                )
        else:
            cur = conn.execute(
                """
                INSERT INTO chat_main (session_id, sha256, source)
                VALUES (?, ?, 'legacy_migration')
                """,
                (session_id, old_hash or ""),
            )
            mid = int(cur.lastrowid)
        if old_hash:
            conn.execute(
                """
                INSERT INTO chat_main_hash (hash, chat_main_id) VALUES (?, ?)
                ON CONFLICT(hash) DO UPDATE SET chat_main_id=excluded.chat_main_id
                """,
                (old_hash, mid),
            )
        return mid

    # 2. chat_identity_log / chat_center_message: TEXT chat_id -> INTEGER
    for table, counter in (
        ("chat_identity_log", "log_rows"),
        ("chat_center_message", "center_rows"),
    ):
        if not _table_exists(conn, table):
            continue
        cols = _table_columns(conn, table)
        has_sha = "sha256" in cols
        sel = "id, session_id, chat_id, sha256" if has_sha else "id, session_id, chat_id"
        table = _safe_table_name(conn, table)
        rows = conn.execute(
            f"""
            SELECT {sel} FROM {table}
            WHERE chat_id IS NOT NULL AND typeof(chat_id) <> 'integer'
            """
        ).fetchall()
        for r in rows:
            rid, session_id, old = r[0], r[1], r[2]
            cur_sha = r[3] if has_sha and len(r) > 3 else None
            mid = _main_id_for(session_id, str(old) if old else None)
            if has_sha:
                # only fill sha256 when it is empty or already an id value
                if cur_sha and not _looks_like_id(cur_sha):
                    conn.execute(
                        "UPDATE %s SET chat_id=? WHERE id=?" % table, (mid, rid)
                    )
                else:
                    conn.execute(
                        "UPDATE %s SET chat_id=?, sha256=? WHERE id=?" % table,
                        (mid, str(old) if old else None, rid),
                    )
            else:
                conn.execute(
                    "UPDATE %s SET chat_id=? WHERE id=?" % table, (mid, rid)
                )
            info[counter] += 1

    # 3. Repair: backfill sha256 from chat_main by session_id.
    for table in ("chat_identity_log", "chat_center_message"):
        if not _table_exists(conn, table):
            continue
        table = _safe_table_name(conn, table)
        if "sha256" not in _table_columns(conn, table):
            continue
        cur = conn.execute(
            f"""
            UPDATE {table}
               SET sha256 = (
                     SELECT m.sha256 FROM chat_main m
                      WHERE m.session_id = {table}.session_id
                   )
             WHERE session_id IS NOT NULL AND session_id <> ''
               AND (
                     sha256 IS NULL OR sha256 = ''
                     OR sha256 = CAST(chat_id AS TEXT)
                     OR length(sha256) <> 64
                   )
               AND EXISTS (
                     SELECT 1 FROM chat_main m
                      WHERE m.session_id = {table}.session_id
                        AND m.sha256 IS NOT NULL AND length(m.sha256) = 64
                   )
            """
        )
        info["sha_repaired"] += int(cur.rowcount or 0)

    conn.commit()
    return info


def _rebuild_chat_id_int_affinity(conn: sqlite3.Connection) -> dict:
    """Rebuild log/center tables so chat_id has INTEGER affinity.

    CREATE TABLE IF NOT EXISTS cannot change a column's declared type. The
    legacy tables declared `chat_id TEXT`; after the migration above the
    values are integers but the affinity is still TEXT. Rebuild to INTEGER so
    the schema states the truth: chat_id is an auto-increment id.
    """
    info: dict = {"rebuilt": []}
    specs = (
        ("chat_identity_log", CHAT_IDENTITY_LOG_DDL, "_chat_identity_log_old"),
        ("chat_center_message", CHAT_CENTER_MESSAGE_DDL, "_chat_center_message_old"),
    )
    for table, ddl, tmp in specs:
        if not _table_exists(conn, table):
            continue
        cols = _table_columns(conn, table)
        decl = ""
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall():
            if r[1] == "chat_id":
                decl = str(r[2] or "").strip().upper()
                break
        if decl == "INTEGER":
            continue
        # Build the column list from the canonical DDL
        conn.execute(f"DROP TABLE IF EXISTS {tmp}")
        conn.execute(f"ALTER TABLE {table} RENAME TO {tmp}")
        conn.executescript(ddl)
        base = {
            "chat_identity_log": [
                "id", "session_id", "chat_id", "sha256", "chat_hash", "action",
                "ide", "llm", "source", "created_at",
            ],
            "chat_center_message": [
                "id", "session_id", "chat_id", "sha256", "chat_hash", "role",
                "content", "catalog_id", "catalog_name", "subcatalog_id",
                "subcatalog_name", "skill_id", "skill_name", "llm", "ide",
                "answered_at", "created_at",
            ],
        }[table]
        shared = [c for c in base if c in cols]
        conn.execute(
            f"INSERT INTO {table} ({', '.join(shared)}) "
            f"SELECT {', '.join(shared)} FROM {tmp}"
        )
        conn.execute(f"DROP TABLE {tmp}")
        info["rebuilt"].append(table)
    conn.commit()
    return info


def _rebuild_wording_registry_unique(conn: sqlite3.Connection) -> dict:
    """Drop the legacy `wording_key ... UNIQUE` so a setting can span dimensions.

    MEASURED DEFECT (2026-09-21)
    ---------------------------
    `WORDING_registry_DDL` declares BOTH:

        wording_key TEXT NOT NULL                    (no UNIQUE)
        UNIQUE (skill_id, dim_key, wording_key)      (the real key)

    The LIVE table additionally declares `wording_key TEXT NOT NULL UNIQUE`, from
    an earlier revision, and carries the composite constraint too. So the live
    table has TWO unique indexes where the DDL has one.

    `CREATE TABLE IF NOT EXISTS` cannot remove a constraint, so the live table
    kept the stale one and the DDL change never took effect. Consequence:

        INSERT ... (wording_key='metric_logic', dim_key='logic_layer_ask')
        INSERT ... (wording_key='metric_logic', dim_key='scope_guard')
        -> UNIQUE constraint failed: wording_registry.wording_key

    A setting name could exist under exactly ONE dimension, which makes the point
    of this table unreachable: "one dim_key with many settings" only works if the
    SETTING can be shared across dims. Seeding the layer axis died on exactly
    this. It was invisible because every existing skill uses a distinct
    wording_key per setting, so nothing had ever reused one.

    A SECOND DEFECT, FOUND BY RUNNING IT
    ------------------------------------
    The first version of this function renamed the table and dropped the old one.
    That failed with `FOREIGN KEY constraint failed`, because `prompt_wording`
    has `FOREIGN KEY (wording_id) REFERENCES wording_registry`. With
    `legacy_alter_table` OFF (the modern default), SQLite rewrites a child's FK
    to follow a RENAME, so `prompt_wording` started pointing at
    `_wording_registry_old` and the drop was blocked while rows still referenced
    it. Worse, the failure LEFT that state behind: the new table empty, the old
    table holding 26 rows, and the child pointing at the old name.

    The fix is the documented recipe, and it is the reason this function both
    REPAIRS that state and performs the migration: `legacy_alter_table=ON` makes
    RENAME leave FK references alone, so `prompt_wording` keeps pointing at the
    logical name `wording_registry` throughout. `foreign_keys=OFF` is required
    because SQLite FK enforcement cannot be toggled inside a transaction.

    Rebuild preserves data: both revisions share the same column set, so the copy
    is a straight SELECT, and the input tables are order-independent.
    """
    if not _table_exists(conn, "wording_registry"):
        return {"rebuilt": False, "reason": "table absent"}

    stale_exists = _table_exists(conn, "_wording_registry_old")
    # The half-migrated state must be repaired even when no legacy index is
    # visible on the NEW table (it was created from the current DDL, so it has
    # none) — otherwise the rows stranded in the old table are simply lost.
    legacy = stale_exists
    for idx in conn.execute("PRAGMA index_list(wording_registry)").fetchall():
        if not idx[2]:
            continue
        cols = [r[2] for r in conn.execute("PRAGMA index_info('%s')" % idx[1])]
        if cols == ["wording_key"]:
            legacy = True
            break
    if not legacy:
        return {"rebuilt": False, "reason": "no legacy wording_key UNIQUE"}
    fk_broken = _child_fk_points_at(conn, "prompt_wording",
                                    "_wording_registry_old")

    tmp = "_wording_registry_old"
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    try:
        # FK enforcement cannot be changed inside a transaction.
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")

        # Settle the DATA under `tmp`, whichever name it is under. A first
        # attempt dropped `wording_registry` AFTER moving the rows back into it,
        # destroying all 26 — so the source of truth is fixed here, once, and the
        # guard below refuses to drop anything that still holds rows.
        if not stale_exists:
            conn.execute("DROP TABLE IF EXISTS %s" % tmp)
            conn.execute("ALTER TABLE wording_registry RENAME TO %s" % tmp)
        if _table_exists(conn, "wording_registry"):
            held = conn.execute("SELECT COUNT(*) FROM wording_registry").fetchone()[0]
            if held:
                raise RuntimeError(
                    "wording_registry rebuild: refusing to drop a table holding "
                    "%d rows. The data source must be settled first." % held)
            conn.execute("DROP TABLE wording_registry")

        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % tmp)]
        conn.executescript(WORDING_registry_DDL)
        shared = [c for c in cols if c in _table_columns(conn, "wording_registry")]
        if not shared:
            raise RuntimeError(
                "wording_registry rebuild: no shared column between the legacy "
                "table %s and the canonical DDL; refusing to drop the data."
                % sorted(cols))
        conn.execute(
            "INSERT INTO wording_registry (%s) SELECT %s FROM %s"
            % (", ".join(shared), ", ".join(shared), tmp)
        )
        conn.execute("DROP TABLE %s" % tmp)

        # Repair a child FK that a failed earlier attempt repointed. The child is
        # rebuilt from its own DDL so it references `wording_registry` again, and
        # rebuild+copy is done with FKs off, so no row is rejected.
        if fk_broken:
            pw_cols = [r[1] for r in conn.execute("PRAGMA table_info(prompt_wording)")]
            pw_rows = conn.execute("SELECT * FROM prompt_wording").fetchall()
            conn.execute("DROP TABLE prompt_wording")
            conn.executescript(PROMPT_WORDING_DDL)
            pw_shared = [c for c in pw_cols
                         if c in _table_columns(conn, "prompt_wording")]
            if pw_rows and pw_shared:
                conn.executemany(
                    "INSERT INTO prompt_wording (%s) VALUES (%s)"
                    % (", ".join(pw_shared),
                       ", ".join("?" * len(pw_shared))),
                    [tuple(r[c] for c in pw_shared) for r in pw_rows]
                )
        conn.commit()
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")

    moved = conn.execute("SELECT COUNT(*) FROM wording_registry").fetchone()[0]
    return {"rebuilt": True, "rows": int(moved), "fk_repaired": fk_broken,
            "note": "wording_key can now repeat across dim_key"}


def _child_fk_points_at(conn: sqlite3.Connection, child: str,
                        parent_like: str) -> bool:
    """True when `child`'s DDL references a table whose name contains `parent_like`."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (child,)
    ).fetchone()
    return bool(row and parent_like in (row[0] or ""))


def _migrate_plan_sessions_pair(conn: sqlite3.Connection) -> None:
    """Rebuild plan_sessions with composite PK (session_id, chat_id).

    Legacy DBs have `session_id TEXT PRIMARY KEY` with nullable chat_id.
    SQLite cannot alter a PK, so we rebuild the table preserving data:
    - ensure chat_id registry rows exist for every distinct chat_id
    - backfill NULL/empty chat_id with a deterministic SHA256 placeholder
    - copy rows into the new composite-PK table, then swap.
    """
    if not _table_exists(conn, "plan_sessions"):
        return
    # Detect legacy single-PK schema: PK is exactly [session_id]
    pk = [
        r[1] for r in conn.execute("PRAGMA table_info(plan_sessions)")
        if r[5]  # pk column index (1-based)
    ]
    if pk == ["session_id"]:
        # Ensure chat_id registry exists
        conn.execute(CHAT_ID_DDL)
        # Backfill chat_id for legacy rows (deterministic placeholder)
        conn.execute(
            """
            UPDATE plan_sessions
            SET chat_id = 'legacy-' || substr(chat_hash, 1, 16)
            WHERE chat_id IS NULL OR chat_id = ''
            """
        )
        # Register every distinct chat_id
        for (cid,) in conn.execute(
            "SELECT DISTINCT chat_id FROM plan_sessions WHERE chat_id IS NOT NULL"
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO chat_id (chat_id) VALUES (?)", (cid,)
            )
        # Rebuild with composite PK
        conn.executescript(
            """
            CREATE TABLE plan_sessions_new (
                session_id          TEXT    NOT NULL,
                chat_id             TEXT    NOT NULL,
                chat_hash           TEXT,
                root_seq            TEXT    NOT NULL,
                requirement         TEXT    NOT NULL,
                stage               TEXT    NOT NULL DEFAULT 'new'
                    CHECK(stage IN ('new','ask','confirm','plan','completed')),
                extracted_entities  TEXT,
                version             INTEGER NOT NULL DEFAULT 0,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, chat_id),
                FOREIGN KEY (chat_id) REFERENCES chat_id(chat_id) ON DELETE RESTRICT
            );
            INSERT INTO plan_sessions_new
                (session_id, chat_id, chat_hash, root_seq, requirement, stage,
                 extracted_entities, version, created_at, updated_at)
            SELECT session_id, chat_id, chat_hash, root_seq, requirement, stage,
                   extracted_entities, version, created_at, updated_at
            FROM plan_sessions;
            DROP TABLE plan_sessions;
            ALTER TABLE plan_sessions_new RENAME TO plan_sessions;
            """
        )
        conn.commit()


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

# ---------------------------------------------------------------------------
# task_type_registry — the N-LEVEL task-type hierarchy
# ---------------------------------------------------------------------------
# WHY THIS EXISTS (user, 2026-09-22):
#   "task_id from task table with entity_id if the task = coding, and these need
#    a value table to define type coding = IT project > Project name > ,
#    i don't know how many type will have in the future, but we need to have
#    preparation in table design"
#
# The requirement is an UNBOUNDED hierarchy, and nothing in the schema could
# hold one. Measured before this table:
#   * `taxonomy_path` is TWO levels: `{entity_type}/{entity_key}`
#     (`skill_contract_store.py:425` splits on the FIRST `/` only), and its
#     entity types are a FIXED tuple of 7 (`skill_contract_store.py:392`).
#     `capability/media/ppt` only looks multi-level because the KEY contains a
#     slash — a workaround, not a design.
#   * `task_action_name` is `element` + `action` — exactly two columns.
#   * `llm_tasks.type` is ONE text column.
# So "coding = IT project > Project name > ..." had nowhere to live.
#
# THE SHAPE, and why each part is load-bearing:
#   * SELF-REFERENCE (`parent_type_id`) — a new level is a ROW, never a schema
#     change. That is the "preparation" the user asked for: the depth is DATA.
#   * `type_path` MATERIALISED — one query returns the whole chain, so a reader
#     never needs a recursive CTE. It is DERIVED from the parent's path, so it
#     cannot disagree with `parent_type_id`.
#   * `UNIQUE (parent_type_id, type_key)` — the same key is legal under a
#     DIFFERENT parent. This is the multi-dimensional rule already used by
#     `wording_registry` (`UNIQUE (skill_id, dim_key, wording_key)`): one word,
#     many dimensions. A global `UNIQUE (type_key)` would make the register
#     single-dimensional, which is the defect `skill_factor_registry` was
#     rebuilt to fix.
#   * `cite_ref NOT NULL` — every type must be provable, per
#     `citation_discipline`: no citation, no finding.
#   * `is_active` — soft delete, so a retired type does not orphan its children.
TASK_TYPE_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS task_type_registry (
    task_type_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_type_id  INTEGER,
    type_key        TEXT    NOT NULL,
    type_path       TEXT    NOT NULL UNIQUE,
    depth           INTEGER NOT NULL DEFAULT 0,
    definition      TEXT    NOT NULL,
    cite_ref        TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (parent_type_id, type_key),
    FOREIGN KEY (parent_type_id) REFERENCES task_type_registry (task_type_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_task_type_parent
  ON task_type_registry (parent_type_id, is_active);
CREATE INDEX IF NOT EXISTS idx_task_type_path
  ON task_type_registry (type_path);
"""

# The ROOT types. A root is a row with `parent_type_id IS NULL`, so adding a
# root is DATA too. Seeded only when the table is EMPTY, so a human DELETE is
# not silently undone (the same rule as `entity_type_registry`).
#
# Q2 = (b): `coding` and `it_project` are INDEPENDENT ROOTS, not parent/child.
# The user's notation "coding = IT project > Project name" names the SHAPE of a
# hierarchy, not a nesting of those two words. So both are seeded as roots and
# neither is the other's parent.
#
# The CHILDREN are NOT seeded: the user said the depth is unknown, so inventing
# `project_name` rows would be fabricating a taxonomy. The register ships with
# the roots and the MECHANISM; levels are added as they are decided.
TASK_TYPE_SEED: tuple[tuple[str, str, str], ...] = (
    ("coding", "Work that produces or changes SOURCE CODE.",
     "db_schema.py:TASK_TYPE_REGISTRY_DDL"),
    ("it_project", "A project-shaped unit of IT work (a container for tasks).",
     "db_schema.py:TASK_TYPE_REGISTRY_DDL"),
    ("research", "Work that produces a FINDING, not a code change.",
     "db_schema.py:TASK_TYPE_REGISTRY_DDL"),
    ("operations", "Work that changes RUNTIME STATE (deploy, restart, migrate).",
     "db_schema.py:TASK_TYPE_REGISTRY_DDL"),
)

# ---------------------------------------------------------------------------
# consultant team registry — industry > team > skill/question/key
# ---------------------------------------------------------------------------
# WHY THIS EXISTS (user, 2026-09-22):
#   "5W1H module can improve by github, there is the place for best source for IT
#    in the world. get the proofed and highest rating to weapon our design"
#   "we can expend for more inductry or more type.... IT industry need
#    ## 软件研发小组, other industry need other .... and each industry have their
#    own professional consultant team"
#   "-> same skill -> question -> key -> 1) proofed 2) highest rating"
#   "that can be easy to have patterm by prompt > workflow"
#   "of course, table by DB driven is required"
#
# THE SHAPE REUSES TWO MECHANISMS THAT ALREADY EXIST, rather than inventing a
# second implementation of either:
#   * the N-LEVEL hierarchy is the SAME shape as `task_type_registry` (self-FK +
#     materialised path + composite UNIQUE), because "we can expend for more
#     industy" is the same unbounded-depth problem.
#   * the RATING vocabulary is `logic_training.RATING_SOURCES` and the reader is
#     `logic_training.fetch_github_rating`, which already reads
#     `stargazers_count` from `api.github.com` with NO login.
#
# `proofed` AND `rating` ARE SEPARATE COLUMNS, and that is load-bearing. The
# user's own ordering is "1) proofed 2) highest rating": a repo with 10k stars
# that was never verified is NOT proofed, and a verified technique with no repo
# has no rating. Merging them would make one number answer two questions.
INDUSTRY_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS industry_registry (
    industry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_industry_id INTEGER,
    industry_key       TEXT    NOT NULL,
    industry_path      TEXT    NOT NULL UNIQUE,
    depth              INTEGER NOT NULL DEFAULT 0,
    definition         TEXT    NOT NULL,
    cite_ref           TEXT    NOT NULL,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (parent_industry_id, industry_key),
    FOREIGN KEY (parent_industry_id) REFERENCES industry_registry (industry_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_industry_parent
  ON industry_registry (parent_industry_id, is_active);
"""

CONSULTANT_TEAM_DDL = """
CREATE TABLE IF NOT EXISTS consultant_team (
    team_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    industry_id      INTEGER NOT NULL,
    team_key         TEXT    NOT NULL UNIQUE,
    name             TEXT    NOT NULL,
    description      TEXT    NOT NULL,
    -- The team's declared strengths, e.g. ["架构设计","前后端研发","测试与交付"].
    -- JSON because the COUNT is not known in advance: a team may declare two
    -- strengths or ten, and a fixed number of columns would cap it.
    expertise_json   TEXT    NOT NULL DEFAULT '[]',
    -- The "试试这样问我" prompts. These become a WORKFLOW (see
    -- `consultant_registry.team_workflow`), which is the user's
    -- "prompt > workflow" pattern.
    probe_json       TEXT    NOT NULL DEFAULT '[]',
    -- Where the team's knowledge came from. For IT this is a GitHub repo, which
    -- is also what `rating` is read from.
    source_ref       TEXT    NOT NULL DEFAULT 'NA',
    -- ---- THE WORKER IDENTITY (added 2026-09-23) -------------------------
    -- The user: "worker is VScode > chat or 豆包 > 工作伙伴 > ## 软件研发小组"
    -- and "you have the table already, but you may need to upgrade it".
    --
    -- A TEAM IS THE WORKER. These three columns are what make the PATH
    -- answerable from the table instead of from prose:
    --
    --   worker_key  the WORKER's own stable key. `team_key` is the team's key;
    --               a worker needs its own, and it is seeded FROM team_key so
    --               `software_rd` keeps working. UNIQUE via an index created
    --               after the additive migration (SQLite cannot ALTER a UNIQUE).
    --   source_id   WHICH CHANNEL the worker is reached through. "VScode > chat"
    --               and "豆包 > 工作伙伴" are DIFFERENT channels for the SAME
    --               team; without this the row cannot say which one it is.
    --   entry_path  the measured NAV ROUTE, e.g. '工作伙伴 > 软件研发小组'.
    --               It lives in `element_store` as separate rows with no link
    --               to the team, so the route was not answerable from here.
    worker_key       TEXT,
    source_id        INTEGER,
    -- NOT NULL DATE: `entry_path` is NOT declared NOT NULL, and LIVE is right.
    --
    -- MEASURED DRIFT (2026-09-29): the declarer added `NOT NULL` to an ADDITIVE
    -- column that LIVE carries as bare `TEXT`. The table holds ONE row whose
    -- `entry_path` happens to be set, so "0 NULLs" is a population of 1, not a
    -- decision -- and a tightening nobody decided must not be smuggled into LIVE
    -- by a drift repair. The other additive columns in this same block
    -- (`worker_key`, `source_id`) are NOT NOT NULL, which is the tell that
    -- `entry_path` was an accident.
    entry_path       TEXT    DEFAULT 'NA',
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    -- `worker_key` IS UNIQUE, AND IT IS DECLARED HERE.
    --
    -- MEASURED DRIFT (2026-09-29): the comment above says "UNIQUE via an index
    -- created after the additive migration (SQLite cannot ALTER a UNIQUE)", and
    -- LIVE does carry `UNIQUE (worker_key)`. The DECLARER did not, so a rebuild
    -- that followed the declarer would have DROPPED a live constraint.
    UNIQUE (worker_key),
    FOREIGN KEY (industry_id) REFERENCES industry_registry (industry_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_consultant_team_industry
  ON consultant_team (industry_id, is_active);
"""

CONSULTANT_MEMBER_DDL = """
CREATE TABLE IF NOT EXISTS consultant_member (
    member_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    -- Exactly one lead per team is expressible; the uniqueness is enforced by a
    -- partial index created in `consultant_registry.ensure_schema`, because
    -- SQLite cannot declare a partial UNIQUE inline.
    is_lead     INTEGER NOT NULL DEFAULT 0 CHECK (is_lead IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (team_id, name),
    FOREIGN KEY (team_id) REFERENCES consultant_team (team_id) ON DELETE CASCADE
);
-- THE "ONE LEAD PER TEAM" KEY, DECLARED HERE 2026-09-29 (RING 5, D3).
--
-- MEASURED: LIVE carries `idx_consultant_one_lead ON consultant_member (team_id)
-- WHERE is_lead = 1`, created by `consultant_registry.ensure_schema`, and a
-- FRESH `ensure_schema` did NOT — because `db_schema` declares this table and
-- never ran the OWNER's index step. It is PARTIAL on purpose (a team may have
-- many members, one lead) and it must stay partial; the table-level
-- `UNIQUE (team_id, name)` above says nothing about leads.
--
-- Declared with `IF NOT EXISTS`, so the owner's identical statement stays
-- harmless: an index is not a table, and both copies are the SAME text.
CREATE UNIQUE INDEX IF NOT EXISTS idx_consultant_one_lead
    ON consultant_member (team_id) WHERE is_lead = 1;
CREATE INDEX IF NOT EXISTS idx_consultant_member_team
  ON consultant_member (team_id);
"""

CONSULTANT_SKILL_DDL = """
CREATE TABLE IF NOT EXISTS consultant_skill (
    consultant_skill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id             INTEGER NOT NULL,
    skill_key           TEXT    NOT NULL,
    -- THE THREE PARTS THE USER NAMED: "same skill -> question -> key".
    question            TEXT    NOT NULL,
    answer_key          TEXT    NOT NULL,
    -- 1) PROOFED. A boolean WITH a citation: `proofed = 1` requires
    -- `source_ref`, enforced in `consultant_registry.mark_proofed`.
    proofed             INTEGER NOT NULL DEFAULT 0 CHECK (proofed IN (0, 1)),
    source_ref          TEXT    NOT NULL DEFAULT 'NA',
    -- 2) HIGHEST RATING. A number WITH a source. `rating > 0` requires
    -- `rating_source` to be one of `logic_training.RATING_SOURCES`, so a star
    -- count and a self-measured proof count are never compared as if equal.
    rating              INTEGER NOT NULL DEFAULT 0,
    rating_source       TEXT    NOT NULL DEFAULT 'NA',
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    skill_ref INTEGER,
    UNIQUE (team_id, skill_key),
    FOREIGN KEY (team_id) REFERENCES consultant_team (team_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_consultant_skill_rank
  ON consultant_skill (proofed DESC, rating DESC);
"""

# ---------------------------------------------------------------------------
# identity_registry — WHO IS PRESENT, as a 5W1H identity (2026-09-23)
# ---------------------------------------------------------------------------
# The user's model, verbatim:
#
#   "identity is 5W1H, who = session ID + worker ID required
#    what = get workflow id
#    workflow tell you how to have chat ID"
#
# So an identity is NOT a session id alone. `who` is COMPOSITE — a session AND a
# worker — and `what` is the WORKFLOW being run. The chain is:
#
#     session_id + worker_id  ->  workflow_id  ->  chat_id
#
# `chat_id` IS NULLABLE, AND THAT IS LOAD-BEARING. It does not exist until the
# workflow produces it ("workflow tell you how to have chat ID"). A NOT NULL
# would force a fabricated value at insert time — the "fake" the user warned
# about. NULL here means "not yet produced", which is a real state.
#
# WHY `worker_id` IS NOT A FK TO `workers`: measured, `workers` is a HEARTBEAT
# NODE table (`init_db.sql:30-40`) whose only writer hardcodes
# `WORKER_NAME = "openclaw_worker_01"` (`worker_heartbeat_service.py:38`). It
# answers "is the process alive", not "who is this worker". It is a fake worker
# table: the right name, the wrong job. The FK target is decided by the Phase 0
# measurement (U1-U3), so this DDL deliberately declares `worker_id` as a plain
# INTEGER with NO FK until that is settled — a wrong FK is worse than none.
IDENTITY_registry_DDL = """
CREATE TABLE IF NOT EXISTS identity_registry (
    identity_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    identity_key   TEXT    NOT NULL UNIQUE,
    -- WHO, part 1: the chat session. The ONE stable chat identity.
    session_id     TEXT    NOT NULL,
    -- WHO, part 2: the worker. NOT NULL because the user said "required".
    worker_id      INTEGER NOT NULL,
    -- WHAT: which workflow is running. FK to the existing workflow register.
    workflow_id    INTEGER NOT NULL,
    -- THE TASK ID (added 2026-09-24). The user's ruling:
    --     "task ID is the way to connect to chat ID and workflow ID"
    -- This row ALREADY holds `chat_id` and `workflow_id`; `task_id` is the JOIN
    -- between them, so the three are reachable from one another instead of being
    -- three unrelated columns.
    --
    -- WHAT IT IS: the task table's AUTO-INCREMENT id (`dev_task.id`), NOT a
    -- tracking code. The user: "the task ID design is wrong at the past, so be
    -- id from task table by auto increase, not the tracking ID coding now".
    -- A legacy tracking label still REACHES this id through `dev_task.task_label`.
    --
    -- NULLABLE on purpose: an identity is opened BEFORE its task exists (the
    -- identity is what lets the work start), and a fabricated task id would be
    -- the defect the `chat_id` comment above already names.
    task_id        INTEGER,
    -- THE OUTPUT. NULL until the workflow produces it. Never fabricated.
    chat_id        INTEGER,
    -- WHERE: the channel (vscode / doubao).
    channel        TEXT    NOT NULL DEFAULT 'NA',
    -- THE ROLE (added 2026-09-24). The user's formula:
    --     "identity = A + role = worker"
    -- MEASURED: `A` is `session_id` + `channel` (both present), and `role` was
    -- MISSING -- `role_registry` held the vocabulary (researcher / writer /
    -- verifier) but NOTHING connected an identity to it.
    --
    -- NULLABLE on purpose: a role is a DECISION, and a fabricated role would be
    -- the same defect as a fabricated task id. An identity with no role is
    -- REPORTED as unassigned, never defaulted.
    role_id        INTEGER,
    -- THE LLM MODEL (added 2026-09-24). The user's formula:
    --     "identity = session + LLM model"
    -- MEASURED: `session_id` was present and the MODEL was MISSING -- there was
    -- no column at all (`llm` / `model` / `model_id` / `llm_id` / `provider` all
    -- absent). So the formula could not be expressed.
    --
    -- THE USER'S DECISION: "if by id, you need to have LLM table = 2 table".
    -- So this is a FK to `llm_model.id`, and the model's NAME and its `local`
    -- flag are READ through the join -- never copied here. Storing `local` twice
    -- would let the two copies disagree.
    --
    -- WHY NOT `chatSessions`: the user ruled it "totally wrong design". MEASURED
    -- and the user is right -- `chatSessions/*.jsonl` is VS Code's OWN session
    -- log, and `agentSessions.model.cache` is a key NAMED "model" whose 73
    -- entries carry NO model name (all `providerType='local'`). A model must come
    -- from a REGISTER, not from another program's log file.
    --
    -- NULLABLE on purpose: a model is a DECISION, and a fabricated model would be
    -- the same defect as a fabricated task id. An identity with no model is
    -- REPORTED as unassigned, never defaulted.
    llm_id         INTEGER,
    -- WHEN: the workflow step reached.
    step_no        INTEGER NOT NULL DEFAULT 0,
    -- WHY: the reason this identity is being established.
    why            TEXT    NOT NULL DEFAULT 'NA',
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- One identity per (session, worker, workflow). The same session may run
    -- DIFFERENT workflows with DIFFERENT workers, and each is its own identity.
    environment_id INTEGER,
    UNIQUE (session_id, worker_id, workflow_id),
    FOREIGN KEY (workflow_id) REFERENCES workflow_registry (workflow_id)
);
CREATE INDEX IF NOT EXISTS idx_identity_registry_session
  ON identity_registry (session_id, is_active);
CREATE INDEX IF NOT EXISTS idx_identity_registry_worker
  ON identity_registry (worker_id, is_active);
CREATE INDEX IF NOT EXISTS idx_identity_registry_chat
  ON identity_registry (chat_id);
CREATE INDEX IF NOT EXISTS idx_identity_registry_task
  ON identity_registry (task_id);
"""

# ADDITIVE columns for `identity_registry`. A `CREATE TABLE IF NOT EXISTS` does
# NOT add a column to an existing table (recorded here repeatedly: the live
# `llm_model` lacked `model_id` for exactly this reason), so the new `task_id`
# needs an explicit ALTER applied by `ensure_schema`.
IDENTITY_registry_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("task_id", "INTEGER"),
)

# ---------------------------------------------------------------------------
# worker_identity_binding — THE JOIN IS 5W1H, not a column (2026-09-23)
# ---------------------------------------------------------------------------
# The user: "and API connect by 5W1H".
#
# A single FK from one table to the other would answer ONE question ("which
# identity") and leave the other five unanswerable. This table answers all six,
# PER PAIR, and the wording is DATA.
#
# `dimension_key` is validated against `skill_5w1h.DIMENSION_NAMES` at the WRITE
# SITE, not by a CHECK: the six dimensions are FIXED (`skill_5w1h.py:51-81`), so
# a 7th must be impossible — and a CHECK enum is exactly the "hardcode for
# rubbish" the user rejected (`subject_kind_registry.py:1-40`).
#
# `who` IS COMPOSITE and that is modelled, not flattened: the user said
# "who = session ID + worker ID required". So the `who` row's `binding_text`
# names BOTH, and the two ids live on `identity_registry`.
WORKER_IDENTITY_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS worker_identity_binding (
    binding_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id      INTEGER NOT NULL,
    identity_id    INTEGER NOT NULL,
    dimension_key  TEXT    NOT NULL,
    binding_text   TEXT    NOT NULL,
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (worker_id, identity_id, dimension_key),
    FOREIGN KEY (identity_id) REFERENCES identity_registry (identity_id)
);
CREATE INDEX IF NOT EXISTS idx_worker_identity_binding_worker
  ON worker_identity_binding (worker_id, is_active);
CREATE INDEX IF NOT EXISTS idx_worker_identity_binding_identity
  ON worker_identity_binding (identity_id, dimension_key);
"""

# WHY THIS TABLE EXISTS (the user, 2026-09-24)
# --------------------------------------------
#     "you have table, but the table not complete or 1 table is not enough as
#      need to match environment too"
#
# MEASURED, and the user is RIGHT: NO table paired a worker with an environment.
#
#   * `identity_registry` has BOTH columns, but it is PER-SESSION: 53 rows, 53
#     distinct `session_id`, only 3 distinct (worker_id, channel) pairs. A
#     per-session OBSERVATION is not a DECLARED binding.
#   * `worker_identity_binding` has a worker but no environment column.
#   * `worker_mode` has a worker but no environment column.
#   * `worker_registry` has no environment column.
#   * `channel_registry` has an environment but no worker column.
#
# So the MATRIX (6 workers x 4 active environments) was 24 cells, ALL EMPTY.
#
# AND THE TWO SOURCES FOR "environment" DISAGREED ON EVERY VALUE:
# `channel_registry.channel_key` (DECLARED) held `llm_task_monitor_ui`,
# `local_pc`, `scripts`, `src`; `identity_registry.channel` (OBSERVED) held
# `vscode` and `runtime`. No value appeared in both. So the four environments the
# user names (VS Code, 豆包, Microsoft Edge, Google Chrome) were not declared
# anywhere.
#
# THE CONCEPT ALREADY EXISTED: `worker_identity_binding`'s `where` dimension says
# "the channel the identity arrives through". It was never made a table.
#
# THE ENVIRONMENT IS A FOREIGN KEY, NOT FREE TEXT. `channel_registry` is the
# DECLARED environment vocabulary, so a binding must name a row in it. That is
# what stops a second vocabulary appearing -- the defect measured above.
WORKER_ENVIRONMENT_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS worker_environment_binding (
    binding_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id      INTEGER NOT NULL,
    environment_id INTEGER NOT NULL,
    is_primary     INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- NOTE: `channel_id` IS RETIRED and is NOT declared here.
    --
    -- MEASURED (2026-09-29): it was declared as `channel_id INTEGER` with NO
    -- constraint, which is exactly the defect this table's own comment below
    -- warns about -- "the environment is a FOREIGN KEY, not free text ... that is
    -- what stops a second vocabulary appearing". LIVE kept the OLD shape
    -- (`channel_id INTEGER NOT NULL` + `UNIQUE (worker_id, channel_id)` +
    -- `FK -> channel_registry`), so a WORKER x ENVIRONMENT binding could only
    -- name a CHANNEL, and with ONE channel (`local_pc`) every binding named the
    -- same row. The human ruled on 2026-09-25: "環境 is 環境!!!! not related to
    -- channel". The retired column holds no information this table needs.
    UNIQUE (worker_id, environment_id),
    FOREIGN KEY (worker_id) REFERENCES worker_registry (worker_id),
    FOREIGN KEY (environment_id) REFERENCES working_environment (environment_id)
);
CREATE INDEX IF NOT EXISTS idx_worker_environment_binding_worker
  ON worker_environment_binding (worker_id, is_active);
CREATE INDEX IF NOT EXISTS idx_worker_environment_binding_environment
  ON worker_environment_binding (environment_id, is_active);
"""

# WHY `environment_id` AND NOT `channel_id` (the user, 2026-09-25)
# ---------------------------------------------------------------
#     "環境 is 環境!!!! not related to channel"
#     "vscode not channel!!! / channel is local > agent_system /
#      vscode is working enviornment / fix it now"
#
# MEASURED: this table carried `channel_id NOT NULL` with a FOREIGN KEY to
# `channel_registry`, so a WORKER x ENVIRONMENT binding could only name a
# CHANNEL. With exactly ONE channel (`local_pc`), every binding named the same
# row -- the matrix could not distinguish one environment from another, which
# is the defect the user names.
#
# The environment vocabulary is `working_environment` (kind > product >
# surface). A binding names a row THERE, so the foreign key is what stops a
# second vocabulary appearing.

# WHY THIS TABLE EXISTS (the user, 2026-09-24)
# --------------------------------------------
#     "`chat_main.ide` = `VS Code` (product), `channel` = `vscode` (kind),
#      surface = `chat` -- why? should have a single table for that!!!! by DB
#      driven too"
#
# MEASURED, and the user is RIGHT: the 3-part environment path was assembled in
# PYTHON from two hardcoded dicts (`ENV_KIND`, `ENV_PRODUCT`) plus one observed
# column (`chat_main.ide`). That is a HARDCODE MAKER, not a register:
#
#   * the KIND (`IDE`) lived in a Python dict
#   * the PRODUCT (`VS Code`) lived in a Python dict AND in `chat_main.ide`
#   * the SURFACE (`chat`) was a literal in the format string
#
# So the same fact had THREE homes, and a reader could not tell which one was
# authoritative. This table is the ONE home.
#
# THE PATH IS A ROW, NOT A STRING. `kind > product > surface` are three COLUMNS,
# so a query can group by KIND ("how many IDE workers") without parsing a string.
#
# `channel_id` IS A FOREIGN KEY, so an environment cannot name a channel the
# system does not know -- the same rule `worker_environment_binding` follows.
WORKING_ENVIRONMENT_DDL = """
CREATE TABLE IF NOT EXISTS working_environment (
    environment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT    NOT NULL,
    product        TEXT    NOT NULL,
    surface        TEXT    NOT NULL DEFAULT 'chat',
    display        TEXT    NOT NULL,
    cite_ref       TEXT    NOT NULL,
    -- THE PAGE A BROWSER ENVIRONMENT IS ON (2026-09-25).
    --
    -- THE USER: "where is enviornment for role = Verfiter
    --            enviornment : Google Chrome > https://chat.deepseek.com/..."
    --
    -- MEASURED: the three environments the user named ALREADY EXIST in
    -- `source` (source_key doubao / chrome_deepseek / edge_gemini), and
    -- `source.url` holds the page. But `working_environment` had NO url
    -- column, so a browser page could not be expressed as a row and the
    -- step-1 page could not show it.
    --
    -- NULLABLE on purpose: an IDE or a runtime environment has no URL, and a
    -- NULL here means "this environment is not a page", which is a real fact.
    url            TEXT,
    -- THE NAV PATH INSIDE THE APP (2026-09-25).
    --
    -- THE USER: "enviornment : 豆包 Browser > 工作伙伴 > 软件研发小组 > 任務"
    --
    -- That is FOUR parts, but the register's path is THREE
    -- (`kind > product > surface`). The extra parts are a NAV PATH, so they
    -- get their OWN column rather than a 4th segment: a path that grows a
    -- segment per site cannot be grouped or constrained.
    --
    -- THE USER'S DECISION (verbatim): "a) , `consultant_team` 是「團隊 is
    -- other table , don't mixup". So this is NOT read from
    -- `consultant_team.entry_path` -- that is the TEAM register holding a
    -- DIFFERENT fact (which team a consultant belongs to) that merely looks
    -- similar. Two registers must never share one column.
    nav_path       TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- THE ENVIRONMENT'S OWN IDENTITY, NOT A CHANNEL (2026-09-25).
    --
    -- THE USER (verbatim): "環境全部指向**同一個** channel, why!!!! 環境 is
    -- 環境!!!! not related to channel" and "this is data for 環境 and module :
    -- identity , 環境 is one of the capability, channel : local, agent_system".
    --
    -- MEASURED DEFECT: this table had `channel_id INTEGER NOT NULL` + a FK to
    -- `channel_registry` + `UNIQUE(channel_id, surface)`. The 5 active
    -- environments each sat on a DIFFERENT `channel_id`
    -- (8505/8504/8534/8535/8536), so the UNIQUE was really "one environment
    -- per channel" -- the schema FORCED an environment to own a channel.
    --
    -- THE AUTHORITATIVE HIERARCHY is in `terminology_registry` (verbatim):
    --   `channel_folder`    "The TOP directory level ... one folder per
    --                        CHANNEL (`channel_registry.channel_key`)"
    --   `module_folder`     "The SECOND directory level ... one folder per
    --                        MODULE (`module_registry.module_key`)"
    --   `capability_folder` "The THIRD directory level ... one folder per
    --                        CAPABILITY"
    -- So the hierarchy is `channel > module > capability > file`, and an
    -- ENVIRONMENT is a CAPABILITY. It is NOT related to a channel.
    --
    -- MEASURED: every non-`NA` `taxonomy_path` in `terminology_registry`
    -- (15 rows) shares the SAME first two segments (`module/agent_system`),
    -- so the taxonomy knows exactly ONE channel.
    --
    -- THE ENVIRONMENT'S LOGO (2026-09-25). THE HUMAN: "+ enviornment logo".
    --
    -- MEASURED: `source` has `source_key, name, kind, url, hotkey, description`
    -- and `working_environment` had NO icon column, so an environment logo had
    -- NO home. It is added HERE, on the environment, because the logo
    -- identifies the ENVIRONMENT's product (VS Code / 豆包 / Chrome / Edge) --
    -- it is not a channel fact and not a source fact.
    --
    -- NULLABLE on purpose: an environment with no logo is a REAL fact, and a
    -- default icon would claim a logo nobody registered.
    icon_url       TEXT,
    UNIQUE (kind, product, surface)
);
CREATE INDEX IF NOT EXISTS idx_working_environment_kind
  ON working_environment (kind, is_active);
"""

# ---------------------------------------------------------------------------
# computer_presence — the TRIGGER POINT (2026-09-24)
# ---------------------------------------------------------------------------
# The user: "trigger point by http://127.0.0.1:18765/llm-tasks/ open at browser"
#           "so you can have status now!"
#
# THE DESIGN: opening the LLM Task Monitor page IS the trigger. A page load is a
# FACT (a request arrived), not an inference, so it is the honest evidence that a
# computer is present.
#
# WHY A NEW TABLE, AND NOT `workers`:
# MEASURED — `workers.last_seen_at` is the HEARTBEAT worker's column, and
# `workers.id` is a DIFFERENT id space from `identity_registry.identity_id`.
# Joining those two by a bare integer is exactly what produced the fake ON
# reported on 2026-09-24. Presence therefore gets its OWN table, keyed by the
# COMPUTER, so the two facts can never be confused again.
#
# `computer_id` IS UNIQUE: one row per computer, UPDATED on each visit. A
# presence table that appended a row per visit would grow without bound and
# would make "when was this computer last here" a MAX() over a growing table.
COMPUTER_PRESENCE_DDL = """
CREATE TABLE IF NOT EXISTS computer_presence (
    presence_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    computer_id   TEXT    NOT NULL UNIQUE,
    computer_name TEXT    NOT NULL DEFAULT '',
    ip_address    TEXT    NOT NULL DEFAULT '',
    user_id       INTEGER,
    last_seen_at  TEXT    NOT NULL,
    first_seen_at TEXT    NOT NULL,
    hits          INTEGER NOT NULL DEFAULT 0,
    last_path     TEXT    NOT NULL DEFAULT '',
    cite_ref      TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_computer_presence_seen
  ON computer_presence (last_seen_at);
"""

# ---------------------------------------------------------------------------
# role_environment — the ROLE x ENVIRONMENT register (2026-09-24)
# ---------------------------------------------------------------------------
# The user: "role X enviornment table is missing?"
#
# MEASURED, AND THE USER IS RIGHT: there was NO role x environment table.
# `assignee_selection` exists but it is a PICK SLOT (one active row), not a
# MATRIX. So the pairing the user asked for earlier
# ("role = writer X environment = IDE") had no home.
#
# WHY A PAIR IS A ROW, NOT TWO COLUMNS ON ONE TABLE:
# a role may work in SEVERAL environments, and an environment may host SEVERAL
# roles. Storing the pair as a row is the only shape that expresses many-to-many
# without a comma-separated list (which cannot be joined or constrained).
#
# `role_key` and `environment_id` are BOTH required, so a pair cannot name a role
# or an environment the system does not know.
ROLE_ENVIRONMENT_DDL = """
CREATE TABLE IF NOT EXISTS role_environment (
    pair_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key       TEXT    NOT NULL,
    environment_id INTEGER NOT NULL,
    is_primary     INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (role_key, environment_id)
);
CREATE INDEX IF NOT EXISTS idx_role_environment_env
  ON role_environment (environment_id, is_active);
CREATE INDEX IF NOT EXISTS idx_role_environment_role
  ON role_environment (role_key, is_active);
"""

# ---------------------------------------------------------------------------
# tool_center — the STEP 3 tools (2026-09-24)
# ---------------------------------------------------------------------------
# The user:
#   "show chat center / Task Center / QC Center (new) for user to select"
#   "-> chat center X research"
#   "-> task center X writing"
#   "-> QC Center X Verfitier"
#
# MEASURED: there was NO `qc_center` table. `qc_run` / `pair_qc_run` /
# `schema_qc_run` are RUN logs, not a CENTER register. So the user's third step
# had no register to read.
#
# `role_key` IS THE PAIRING: the user's mapping says which role each tool serves,
# so the tool register carries it rather than the UI typing it.
TOOL_CENTER_DDL = """
CREATE TABLE IF NOT EXISTS tool_center (
    tool_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_key     TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    role_key     TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    cite_ref     TEXT    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tool_center_role
  ON tool_center (role_key, is_active);
"""

# ---------------------------------------------------------------------------
# pick_flow — the THREE-STEP pick (2026-09-24)
# ---------------------------------------------------------------------------
# The user:
#   "onclick LLM = submit -> step 3"
#   "when step 1 = vscode, step 2 LLM for ... with bg-color : blue"
#   "when step 2 = deepseekSeek V4.1, step 3 task center with bg-color : blue"
#
# ONE row per SLOT, so the flow is a STATE rather than a session variable. The
# three steps are three columns on one row, because a flow is ONE decision made
# in three parts -- three rows would let step 2 exist without step 1.
#
# `slot` is UNIQUE among ACTIVE rows (a partial unique index), so there is at
# most ONE live flow, and a re-pick SUPERSEDES rather than overwrites -- the
# history stays readable.
PICK_FLOW_DDL = """
CREATE TABLE IF NOT EXISTS pick_flow (
    flow_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    slot           INTEGER NOT NULL DEFAULT 1,
    environment_id INTEGER,
    llm_id         INTEGER,
    tool_key       TEXT,
    role_key       TEXT,
    cite_ref       TEXT    NOT NULL DEFAULT '',
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pick_flow_one_active
  ON pick_flow (slot) WHERE is_active = 1;
"""

# ---------------------------------------------------------------------------
# assignee_selection — WHO will receive the task (2026-09-24)
# ---------------------------------------------------------------------------
# The user: "environment setting Online + role-> onclick, so he will the one
#            (identity) to have the task"
#
# THE DESIGN: the page shows ENVIRONMENT (with its Online status) + ROLE, and
# clicking a row SELECTS that identity. The selected identity is the one that
# will RECEIVE the task.
#
# WHY ONE ROW, NOT A LOG:
# "who is the assignee NOW" is a CURRENT fact, not a history. A table that
# appended a row per click would make the answer a MAX() over a growing table
# and would leave several rows claiming to be the assignee. So the table holds
# AT MOST ONE ACTIVE ROW, enforced by a UNIQUE index on a constant column.
#
# `identity_id` IS THE KEY, not a name: the user's own rule is that the DB is
# id-driven, and `identity_registry.identity_id` is the real auto-increment id.
ASSIGNEE_SELECTION_DDL = """
CREATE TABLE IF NOT EXISTS assignee_selection (
    selection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot         INTEGER NOT NULL DEFAULT 1,
    identity_id  INTEGER NOT NULL,
    role_key     TEXT    NOT NULL DEFAULT 'UNASSIGNED',
    channel      TEXT    NOT NULL DEFAULT '',
    environment  TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'UNKNOWN',
    cite_ref     TEXT    NOT NULL DEFAULT '',
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    selected_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_assignee_selection_one_active
  ON assignee_selection (slot) WHERE is_active = 1;
"""

# ---------------------------------------------------------------------------
# session_confirm_log — the TWO-WAY confirm, append-only (2026-09-23)
# ---------------------------------------------------------------------------
# The user: "ui can submit request to have chat with vs code > chat > session id
# to confirm!!!! it is 2 way confirm!!!!"
#
# One leg is NOT a confirm: the UI would be asserting an identity the chat never
# claimed. The CONFIRM exists only when the chat's OWN answer is compared against
# what the UI asked.
#
# APPEND-ONLY, for the same reason as `soft_delete_log` and `factor_change_log`:
# a confirmation is a HISTORICAL FACT. A second request must not overwrite the
# first row's evidence, or the trace the user asked for is destroyed.
#
# `verdict` has exactly THREE values and NO default. PENDING is a real outcome
# (no reply yet) and must never be silently treated as CONFIRM.
SESSION_CONFIRM_LOG_DDL = """
CREATE TABLE IF NOT EXISTS session_confirm_log (
    confirm_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    confirm_key        TEXT    NOT NULL UNIQUE,
    session_id         TEXT    NOT NULL,
    identity_id        INTEGER,
    requested_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    answered_at        TEXT,
    echoed_session_id  TEXT,
    verdict            TEXT    NOT NULL
                       CHECK (verdict IN ('CONFIRM', 'MISMATCH', 'PENDING')),
    -- The SIX lines, as JSON: each field with its ASKED and ECHOED value, so a
    -- reader sees the per-field comparison instead of one merged boolean.
    lines_json         TEXT    NOT NULL DEFAULT '[]',
    evidence_ref       TEXT    NOT NULL DEFAULT 'NA',
    evidence_text      TEXT    NOT NULL DEFAULT 'NA',
    cite_ref           TEXT    NOT NULL,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (identity_id) REFERENCES identity_registry (identity_id)
);
CREATE INDEX IF NOT EXISTS idx_session_confirm_session
  ON session_confirm_log (session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_session_confirm_verdict
  ON session_confirm_log (verdict, created_at DESC);
"""

# ---------------------------------------------------------------------------
# mode_registry / mode_right_registry / worker_mode — THE MODE + RIGHT SYSTEM
#                                                            (2026-09-23)
# ---------------------------------------------------------------------------
# The user's requirement, verbatim:
#
#   "+UI  /llm-tasks/worker/right
#    -> mode : ask / plan / agent
#    this is the skill for worker to understand the edge
#    -> terminal : -> coding writing : -> plan writing :
#    -> plan file location : -> switch mode middleware : 5W1 H
#    /llm-tasks/worker/list   + field
#    mode is apply for this worker or not"
#
#   "all is DB driven"
#
#   "the problem is how to give help before complain not block the activity
#    only, how to user friendly for worker is one of the factor in this plan"
#
# MEASURED BEFORE THIS: there was NO mode table anywhere (`mode_registry` -> 0
# hits). The rights lived as PROSE in `scripts/plan_gate.py` `RIGHTS`, and the
# per-mode skill lists lived in a FILE (`mode_skills.json`). Both are the
# "a definition that lives in code cannot be extended without a code change"
# defect the user has corrected repeatedly (`subject_kind_registry.py:1-40`,
# `FACTOR_TEMPLATE_DDL`). A fourth mode must be a ROW, not a Python edit.
#
# THE SIXTH RIGHT IS NOT A PERMISSION — IT IS THE HELP (`how_to_proceed`).
# "how to give help before complain not block the activity only". A deny that
# only refuses is a wall; a deny that names the next act is a helper. So the
# help is a ROW in the same table as the permissions, and `redirect()` appends
# it to every reason. A mode with no help row is a FAULT, exactly like a mode
# with no permissions.
MODE_registry_DDL = """
CREATE TABLE IF NOT EXISTS mode_registry (
    mode_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mode_key     TEXT    NOT NULL UNIQUE,
    display_name TEXT    NOT NULL,
    -- 1 = shipped with the system, 0 = added by a human. A shipped mode is not
    -- privileged; the flag only records PROVENANCE.
    is_system    INTEGER NOT NULL DEFAULT 1 CHECK (is_system IN (0, 1)),
    definition   TEXT    NOT NULL DEFAULT 'NA',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    cite_ref     TEXT    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mode_registry_active
  ON mode_registry (is_active, sort_order, mode_key);
"""

# THE MATRIX: one row per (mode, right). SIX right_keys, not five:
#
#   terminal                  can run a terminal command
#   coding_writing            can edit product code
#   plan_writing              may write the plan artifact
#   plan_file                 the path (a VALUE, not a yes/no)
#   switch_mode_middleware    which tool/authority switches the mode (a TOOL NAME)
#   how_to_proceed            THE HELP — the exact next act (added 2026-09-23)
#
# `value_text` IS NOT A BOOLEAN, AND THAT IS LOAD-BEARING. `plan_file` holds a
# PATH and `switch_mode_middleware` holds a TOOL NAME. A boolean column would
# force those two facts into a false yes/no — the "one column, two meanings"
# defect `is_active` in `VERSION_CLEANUP_DDL` already carries.
#
# `value_text` is checked against `MODE_RIGHT_VALUES` at the WRITE SITE, so a
# typo cannot invent a permission. The CHECK is deliberately NOT in the DDL: the
# set of legal values is a Python tuple that a TEST can assert, and an SQL enum
# is the "hardcode for rubbish" the user rejected.
MODE_RIGHT_registry_DDL = """
CREATE TABLE IF NOT EXISTS mode_right_registry (
    mode_right_id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode_key      TEXT    NOT NULL,
    -- THE IDENTITY OF THE MODE, not its name. THE POLICY (human-locked): a native
    -- FK may only attach to the parent's PRIMARY KEY, and only for a MANDATORY +
    -- LOAD-BEARING reference. MEASURED 2026-09-29: this FK used `mode_key`, a
    -- business key; `mode_registry`'s PK is `mode_id`. A name is a LABEL that can
    -- be respelled, so it must not be what a constraint is attached to.
    mode_id       INTEGER,
    right_key     TEXT    NOT NULL,
    value_text    TEXT    NOT NULL DEFAULT 'NA',
    -- WHY this value. A right with no reason is a mystery, and
    -- `citation_discipline` requires a citation for a finding.
    why           TEXT    NOT NULL,
    cite_ref      TEXT    NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (mode_key, right_key),
    FOREIGN KEY (mode_id) REFERENCES mode_registry (mode_id)
);
CREATE INDEX IF NOT EXISTS idx_mode_right_lookup
  ON mode_right_registry (mode_key, right_key, is_active);
"""

# "mode is apply for this worker or not". A JOIN TABLE, not a JSON column on
# `worker_registry`: "all is DB driven" means the relationship is a ROW, and the
# user's `list` question IS a join question — with a table it is one query.
# A JSON array is a serialised list nothing can JOIN against.
WORKER_MODE_DDL = """
CREATE TABLE IF NOT EXISTS worker_mode (
    worker_mode_id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id      INTEGER NOT NULL,
    mode_key       TEXT    NOT NULL,
    -- The parent's PK, for the same measured reason as `mode_right_registry`.
    mode_id        INTEGER,
    is_applied     INTEGER NOT NULL DEFAULT 1 CHECK (is_applied IN (0, 1)),
    why            TEXT    NOT NULL DEFAULT 'NA',
    cite_ref       TEXT    NOT NULL,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (worker_id, mode_key),
    FOREIGN KEY (mode_id) REFERENCES mode_registry (mode_id)
);
CREATE INDEX IF NOT EXISTS idx_worker_mode_worker
  ON worker_mode (worker_id, is_applied);
CREATE INDEX IF NOT EXISTS idx_worker_mode_mode
  ON worker_mode (mode_key, is_applied);
"""

# ---------------------------------------------------------------------------
# version_cleanup — WHICH VERSION SUPERSEDES WHICH (user Q6, 2026-09-22)
# ---------------------------------------------------------------------------
# The user's rule:
#
#   "Q6 is related to version / as all the task update by new version, you can
#    trace it easy and once version is proofed / can have cleanup table to
#    prevent new / old version by is_active too"
#
# WHY THIS TABLE EXISTS, AND WHY IT DOES NOT DELETE
# -------------------------------------------------
# `is_active` ALREADY means "retired" in this repo — `db_schema.py` states the
# law "SOFT DELETE ONLY (is_active=0, never DELETE)", and `register_store.py:555`
# plus `task_type_registry.deactivate` both write `is_active = 0` to retire a
# row. The user now wants `is_active = 0` to mean "not yet proven". ONE COLUMN,
# TWO MEANINGS — the same defect family as `catalog_id` having two readers.
#
# This table does NOT resolve that by guessing. It RECORDS the supersession, so
# "old version" becomes a FACT with a citation rather than an inference from a
# `0`. A reader can then tell a retired version from an unproven one.
#
# It is APPEND-ONLY: a supersession is a historical fact, and rewriting it would
# destroy the trace the user asked for ("you can trace it easy").
VERSION_CLEANUP_DDL = """
CREATE TABLE IF NOT EXISTS version_cleanup (
    cleanup_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type    TEXT    NOT NULL,
    entity_ref_id  INTEGER NOT NULL,
    old_version    INTEGER NOT NULL,
    new_version    INTEGER NOT NULL,
    reason         TEXT    NOT NULL,
    cite_ref       TEXT    NOT NULL,
    decided_by     TEXT    NOT NULL DEFAULT 'NA',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
    CHECK (old_version <> new_version),
    FOREIGN KEY (entity_type) REFERENCES entity_type_registry (type_letter)
);
CREATE INDEX IF NOT EXISTS idx_version_cleanup_entity
  ON version_cleanup (entity_type, entity_ref_id);
CREATE INDEX IF NOT EXISTS idx_version_cleanup_new
  ON version_cleanup (new_version);
"""

# ---------------------------------------------------------------------------
# terminology_registry — A TERM IS A DECOMPOSABLE, REGISTERABLE STRUCTURE
# ---------------------------------------------------------------------------
# The user's requirement (2026-09-22):
#
#   "100_ run services / -> 100_ run / -> services / it is meaningful, can help us"
#   "factor defination is correct ot not!!! that is the key"
#   "how to proof is name_registry!!!!!"
#
# and the RENAME:
#
#   "name _register rename to `terminology_registry` / this name is more
#    representative, do u agree?"
#
# AGREED, and the reason is substantive: the table holds a `definition` and a
# `cite_ref`, so its subject is MEANING, not spelling. The user's own key
# sentence is about the DEFINITION ("factor defination is correct ot not"), and a
# term + its definition IS a terminology. `name_registry` described the identity
# column; `terminology_registry` describes what the table IS.
#
# So a term is NOT a string. It is a structure whose PARTS must each be
# registered:
#
#   100_run_service
#     -> 100_run        (the capability)
#     -> service        (what it IS)
#   100_run
#     -> 100            (a count)
#     -> run            (an action)
#
# "Is this term correct?" then becomes CHECKABLE: does every part resolve here?
# A part that does not resolve was never registered, and an unregistered part is
# an INVENTED WORD.
#
# WHY THIS PROVES A FACTOR DEFINITION
# -----------------------------------
# A factor's `factor_key` must decompose into registered parts. If it does not,
# the factor is named with a word nobody defined — which is exactly the defect
# `factor_distill.py` records: "a generator that INVENTED factors produced 11
# factor names across all 50 skills, 642 of 652 rows UNMEASURED, and 0 mentions
# of the skill's own subject."
#
# THE TAXONOMY IS EMBEDDED, NOT A SECOND TABLE (user, 2026-09-22)
# ---------------------------------------------------------------
#   "唔使額外開獨立catalog表，直接將分類、taxonomy相關欄位嵌入Name Register，
#    新建/註冊任何實體嘅時候一併填埋，一次過寫齊，唔使後補，唔使多一張表維護"
#
# So `taxonomy_level` / `taxonomy_path` / `entity_ref_key` live HERE. A separate
# catalog table would be a second thing to maintain and a second place for the
# same fact to disagree with itself — the drift this repo has been bitten by
# repeatedly (`catalog_id` has two readers; `source_type` was declared twice).
#
# WHY `parent_term_id` AND NOT A GLOBAL UNIQUE ON `term_key`
# ----------------------------------------------------------
# The same reason `skill_factor_registry` was rebuilt: a global unique makes the
# register SINGLE-dimensional. `run` under `100_run` and `run` under `test_run`
# are DIFFERENT parts, and a global unique would refuse the second.
#
# `is_active` DEFAULTS TO 0, per the activation gate: a term is UNPROVEN until a
# 100-run proves it. This table is a `*_register`, so it is IN the activation
# scope automatically.
TERMINOLOGY_registry_DDL = """
CREATE TABLE IF NOT EXISTS terminology_registry (
    term_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    term_key       TEXT    NOT NULL,
    term_kind      TEXT    NOT NULL DEFAULT 'part'
                   CHECK (term_kind IN ('count', 'action', 'role', 'entity',
                                        'qualifier', 'part')),
    parent_term_id INTEGER,
    -- THE TAXONOMY, EMBEDDED (the user's decision: no second catalog table).
    -- `taxonomy_level` is one of the eight declared levels; `taxonomy_path` is
    -- the full dotted path (e.g. `erp.order.skill_generator`).
    taxonomy_level TEXT    NOT NULL DEFAULT 'NA',
    taxonomy_path  TEXT    NOT NULL DEFAULT 'NA',
    -- The ORIGINAL entity this term names (skill_key / table_key / factor_key).
    -- 'NA' when the term is a pure vocabulary word with no owning entity.
    entity_ref_key TEXT    NOT NULL DEFAULT 'NA',
    -- The definition, and its HASH. The hash is DERIVED by the writer, never
    -- accepted from the caller: a value a caller can assert is a value a caller
    -- can lie about, and a changed definition with a stale hash would report
    -- "unchanged" silently.
    definition     TEXT    NOT NULL,
    definition_sha256 TEXT NOT NULL DEFAULT 'NA',
    alias_list     TEXT    NOT NULL DEFAULT 'NA',
    version        INTEGER NOT NULL DEFAULT 1,
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (parent_term_id, term_key),
    FOREIGN KEY (parent_term_id) REFERENCES terminology_registry (term_id)
);
CREATE INDEX IF NOT EXISTS idx_terminology_registry_key
  ON terminology_registry (term_key);
-- THE ROOT-NAME KEY, MOVED HERE 2026-09-29 (RING 5, D3).
--
-- MEASURED: LIVE carries `CREATE UNIQUE INDEX idx_terminology_root_one_name ON
-- terminology_registry (term_key) WHERE parent_term_id IS NULL`, created by
-- `terminology_autopilot.py`, and a FRESH build carried NO such index for the
-- simple reason that no DDL declared it. The index lived ONLY in a WRITER
-- module, so nothing that builds a database could know about it.
--
-- WHY IT IS PARTIAL AND MUST STAY PARTIAL: MEASURED, `term_key='route'` already
-- appears TWICE on LIVE, both NON-root (both under a parent). A global UNIQUE
-- on `term_key` would be REJECTED by SQLite on the real database — the same
-- reason `(parent_term_id, term_key)` is the table key and the same choice
-- recorded above at the `db_schema.py` note "WHY `parent_term_id` AND NOT A
-- GLOBAL UNIQUE ON `term_key`". So this DECLARES the true rule: ONE root per
-- name, any number of children.
CREATE UNIQUE INDEX IF NOT EXISTS idx_terminology_root_one_name
  ON terminology_registry (term_key) WHERE parent_term_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_terminology_registry_parent
  ON terminology_registry (parent_term_id, is_active);
CREATE INDEX IF NOT EXISTS idx_terminology_registry_taxonomy
  ON terminology_registry (taxonomy_level, taxonomy_path);
CREATE INDEX IF NOT EXISTS idx_terminology_registry_entity
  ON terminology_registry (entity_ref_key);
"""

# The eight declared taxonomy levels, as a SEED — the TABLE is the SSOT.
#
# WHY A TABLE AND NOT THIS TUPLE (user, 2026-09-22):
#
#   "DB driven can help worker not to have wrong data easy"
#   "you may need 1 more table to make it to be"
#
# A definition that lives in code cannot be extended without a code change, and
# cannot be read by anything that does not import this module — the same defect
# the user already corrected for `factor_template` ("factor template table, or
# something like that, name can help to define").
#
# So adding a taxonomy level is an INSERT, not a Python edit. This tuple is the
# INITIAL SEED only; `taxonomy_level_registry` is the SSOT afterwards.
TAXONOMY_LEVEL_SEED: tuple[tuple[str, int, str], ...] = (
    ("channel", 1, "a delivery channel (local_pc, web, ...)"),
    ("module", 2, "a module inside a channel"),
    ("capability", 3, "a capability a module provides"),
    ("api", 4, "an HTTP surface"),
    ("function", 5, "a callable function"),
    # THE NAMES ARE `db_table` / `db_field`, NOT `table` / `field`
    # (user ruling 2026-09-24):
    #   "db_table , db_field is more respresenattive than table, field"
    # `hardcode_scope.SCOPE_ORDER` already used `db_field`/`db_table`, so the two
    # ladders disagreed on exactly these two names (measured: otherwise
    # identical, same 7 levels, same order). `table` and `field` are ALSO words
    # for a SQL CHECK literal and a task-input payload key, so the bare forms are
    # AMBIGUOUS — `db_table` names the entity, the bare `table` names one of the
    # three. A level name that collides with a different layer's word makes every
    # later reader pick the wrong one.
    ("db_table", 6, "a database table"),
    ("db_field", 7, "a column of a table"),
    ("service", 8, "a callable service interface (e.g. 100_run_service)"),
)

TAXONOMY_LEVEL_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS taxonomy_level_registry (
    level_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    level_key    TEXT    NOT NULL UNIQUE,
    level_order  INTEGER NOT NULL,
    definition   TEXT    NOT NULL,
    cite_ref     TEXT    NOT NULL DEFAULT 'NA',
    is_active    INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_taxonomy_level_order
  ON taxonomy_level_registry (level_order, is_active);
"""

# The eight declared taxonomy levels. A term's `taxonomy_level` must be one of
# these, so the vocabulary cannot grow a level nothing classifies.
#
# DEPRECATED as the SSOT: `taxonomy_level_registry` is the source now. This tuple
# is kept ONLY so an existing caller does not break, and it is asserted against
# the table by `_proof_table_design.py` so the two cannot drift.
TAXONOMY_LEVELS = ("channel", "module", "capability", "api", "function",
                   "db_table", "db_field", "service")

# ---------------------------------------------------------------------------
# dimension_binding_registry — the SAME 5W1H dimension BINDS differently per
#                              SUBJECT KIND
# ---------------------------------------------------------------------------
# The user (2026-09-22):
#
#   "why i think factor can be for many thing? now can explain for table / field
#    and it can explain for function / api / capabiltity / module / channel too
#    ... where file location + line / when trigger point in / what which and which
#    job to have and looking for whcih / how = function / api / capabiltity /
#    module / channel / do u agree? if yes, = generator template have many layer
#    now!!!"
#
# The SHARING mechanism already exists (`skill_factor_registry.applies_to` is a
# tag set matched against `skill_registry.capability_tags`, comma-separated —
# db_schema.py:1937). What is NEW is the BINDING: what each dimension MEANS for a
# given kind.
#
# WHY A TABLE FOR THE BINDINGS AND NOT FOR THE DIMENSIONS: the bindings are an
# OPEN set (the future kinds are unknown) so a table is right; the SIX dimensions
# are FIXED, so a table would let someone add a 7th and silently change every
# generated question list. `dimension_key` is therefore validated against
# `skill_5w1h.DIMENSION_NAMES`, and `subject_kind` is free.
DIMENSION_BINDING_registry_DDL = """
CREATE TABLE IF NOT EXISTS dimension_binding_registry (
    binding_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind   TEXT    NOT NULL,
    dimension_key  TEXT    NOT NULL,
    binding_text   TEXT    NOT NULL,
    example        TEXT    NOT NULL DEFAULT 'NA',
    cite_ref       TEXT    NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (subject_kind, dimension_key)
);
CREATE INDEX IF NOT EXISTS idx_dimension_binding_kind
  ON dimension_binding_registry (subject_kind, sort_order, is_active);
"""

# ---------------------------------------------------------------------------
# code_location_registry — WHERE a version's code IS, as the EVIDENCE that the
#                          version is active
# ---------------------------------------------------------------------------
# The user (2026-09-22):
#
#   "version register -> will have this action to have record for all active
#    coding / so it can help to classify rubbish coding for cleanup / trigger
#    point for tthis table seems can be proofed evidence for version is_active"
#
# WHY A LAYER AND NOT A COLUMN ON `version_registry`: MEASURED,
# `entity_registry.py:81` `version_registry` has no `file_path`, and adding one
# would VIOLATE the user's own rule 3 — `entity_type` is the discriminator and
# `file_path` applies only to CODE entity types, so the table would be MIXED.
# Also one version may have MANY locations (1:N).
#
# The join key is exactly `version_registry`'s triple, so this is the edge that
# was missing between the version space and `code_registry`.
code_location_registry_DDL = """
CREATE TABLE IF NOT EXISTS code_location_registry (
    location_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type    TEXT    NOT NULL,
    entity_ref_id  INTEGER NOT NULL,
    version        INTEGER NOT NULL CHECK (version >= 1),
    file_path      TEXT    NOT NULL,
    line_start     INTEGER NOT NULL DEFAULT 0,
    line_end       INTEGER NOT NULL DEFAULT 0,
    code_span      TEXT    NOT NULL DEFAULT 'NA',
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (entity_type, entity_ref_id, version, file_path, line_start),
    FOREIGN KEY (entity_type) REFERENCES entity_type_registry (type_letter)
);
CREATE INDEX IF NOT EXISTS idx_code_location_entity
  ON code_location_registry (entity_type, entity_ref_id, version);
CREATE INDEX IF NOT EXISTS idx_code_location_path
  ON code_location_registry (file_path, line_start);
"""

# ---------------------------------------------------------------------------
# fault_factor_trace — WHICH FACTOR CAUSED A FAILURE
# ---------------------------------------------------------------------------
# The user's factor 1 (2026-09-22):
#
#   "100_run_service 回報測試失敗，並成功溯源得到 fault_factor_id，先可以啟動"
#   "溯源失敗則禁止啟動蒸餾"
#
# So the trigger REQUIRES a trace. Without one, a distillation would be a blind
# edit: it would change a factor nobody showed to be at fault.
#
# `cite_ref` is NOT NULL because a trace is a FINDING, and the repo's rule is
# "no citation, no finding" (`citation_discipline`). An uncited trace is a guess
# with a heading.
FAULT_FACTOR_TRACE_DDL = """
CREATE TABLE IF NOT EXISTS fault_factor_trace (
    trace_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_key      TEXT    NOT NULL UNIQUE,
    ref_tag        TEXT    NOT NULL,
    factor_key     TEXT    NOT NULL,
    failure_count  INTEGER NOT NULL DEFAULT 0,
    total_count    INTEGER NOT NULL DEFAULT 0,
    reason         TEXT    NOT NULL,
    cite_ref       TEXT    NOT NULL,
    traced_by      TEXT    NOT NULL DEFAULT 'NA',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fault_factor_trace_ref
  ON fault_factor_trace (ref_tag, factor_key);
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
    ("current_model", "TEXT"),
    ("qc_summary", "TEXT"),
    # `error` is a DETAIL OF A CONDITION recorded in the same row (`status`).
    #
    # 🔴 `NOT NULL` REMOVED 2026-09-29 (RING 5, D4). MEASURED, and it is what
    # settles this: LIVE `dev_task` has **6 rows whose `error` IS NULL** out of
    # 165. A DEFAULT does not backfill an existing NULL, and `ALTER TABLE` cannot
    # add `NOT NULL` at all — so this declaration was stricter than anything the
    # database COULD become, and every FRESH-vs-LIVE comparison called LIVE the
    # drifted side for a rule LIVE can never satisfy without deleting rows.
    #
    # The two layers this comment used to justify were: this DEFAULT and
    # `no_null.insert_row`. MEASURED: the DEFAULT survives the relaxation, so a
    # writer that omits the column still gets `'NA'` — the layer that was doing
    # the work is UNCHANGED. Only the layer that could never be retrofitted is
    # dropped, and NULL now reads for what it always was: "no error recorded",
    # which `null_columns` classifies KEEP_NULL.
    ("error", "TEXT DEFAULT 'NA'"),
    # The task's TYPE, as a FK into the N-level `task_type_registry`. NULL is
    # allowed and is the honest state for rows that predate the register: this
    # is a REFERENCE column, and NULL there means "no type recorded" — a
    # decision, not an absence (see `no_null.KIND_FK`).
    ("task_type_id", "INTEGER"),
    # The WORKFLOW a task runs under, as a lazy FK into
    # `workflow_registry.workflow_id`. Declared here because `goal_resolver.py:196`
    # (`ensure_task_workflow_column`, defined at `goal_resolver.py:186`) ALTERs it
    # in lazily with `ALTER TABLE dev_task ADD COLUMN workflow_id INTEGER` — until
    # this row existed the column was real in the DB but absent from the
    # declaration, so the two copies of one fact drifted (measured 2026-09-24:
    # `_proof_logic_evidence_answer` read `dev_task: missing=['workflow_id']`).
    # NULL is ALLOWED and is the honest state for a task no goal assigned a
    # workflow to: it is the NOT-ASSIGNED sentinel, not a default workflow (same
    # REFERENCE-column rule as `task_type_id` above; see `no_null.KIND_FK`).
    ("workflow_id", "INTEGER"),
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

# ---- qc_run: unified QC verdict registry (PASS | FAIL | UNKNOWN) ----
# WHY: every QC tool (schema_qc / pair_qc / evidence_classify) had its own
# output shape, so "did this pass?" needed per-tool knowledge. One table +
# one contract means a single query answers it, and UNKNOWN is a first-class
# outcome that is NEVER silently treated as PASS.
#
# EVIDENCE BINDING (user spec 2026-09-20): a verdict is only trustworthy when
# it points at a real evidence FOLDER under `evidence/` (the EVID-* convention
# owned by evidence_store.py) and carries the sha256 of the screenshot inside
# it. `evidence_sha256` alone was not enough — a hash of "some file" proves
# nothing. So the row also records WHICH folder and WHICH files.
QC_RUN_DDL = """
CREATE TABLE IF NOT EXISTS qc_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    qc_id           TEXT    NOT NULL UNIQUE,
    tool            TEXT    NOT NULL,
    target          TEXT    NOT NULL,
    verdict         TEXT    NOT NULL
                    CHECK (verdict IN ('PASS', 'FAIL', 'UNKNOWN')),
    evidence_id     TEXT,
    evidence_dir    TEXT,
    evidence_path   TEXT,
    evidence_sha256 TEXT,
    evidence_files  TEXT,
    reason          TEXT,
    task_id         TEXT,
    trace_id        TEXT,
    -- qc_gate layer (added 2026-09-28, additive). WHY: the 9-dimension quality
    -- gate writes ONE verdict through this table (no second verdict store).
    -- `gate_key` names WHICH of the nine dimensions the row is about;
    -- `score_0_100` carries the arbiter's DERIVED score; `gate_run_ref` groups
    -- the nine rows of one run so an aggregate is checkable. All three are
    -- NULLABLE so every existing qc_run writer keeps working unchanged.
    gate_key        TEXT,
    score_0_100     REAL,
    gate_run_ref    TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_qc_run_verdict
  ON qc_run (verdict, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qc_run_target
  ON qc_run (tool, target, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qc_run_task
  ON qc_run (task_id, created_at DESC);
"""

# Index for the qc_gate layer. Kept SEPARATE from QC_RUN_DDL for the same reason
# as QC_RUN_EVIDENCE_INDEX_DDL above: an EXISTING `qc_run` table does not get the
# new columns from `CREATE TABLE IF NOT EXISTS`, so an index over `gate_run_ref`
# inside QC_RUN_DDL makes the WHOLE script fail with `no such column` and NOTHING
# runs. It is created AFTER the additive migration in
# `qc_contract.ensure_qc_run_table()`.
QC_RUN_GATE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_qc_run_gate_run
  ON qc_run (gate_run_ref, gate_key);
"""

# Indexes that depend on the evidence-binding columns. Kept separate from
# QC_RUN_DDL because a legacy qc_run table (created before evidence binding)
# lacks those columns, so the index must be created AFTER the additive
# migration in qc_contract.ensure_qc_run_table().
QC_RUN_EVIDENCE_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_qc_run_evidence
  ON qc_run (evidence_id);
"""

# ---- qc_gate_registry: the DECLARATION of the nine quality dimensions ----
# The layer (`qc_gate`) is NOT a new system: each gate DELEGATES to an existing
# module (`qc_gate.GATES[].checker_ref`). This table stores the per-gate
# DECLARATION — which checker, which metric, its unit, its target and its
# polarity — so the nine dimensions are DB-driven rather than restated in prose.
# The FIXED set of nine lives in `qc_gate.GATES` (a Python tuple), the same
# two-layer shape `skill_5w1h.DIMENSIONS` uses for its six dimensions: a table
# row cannot introduce a tenth dimension silently.
QC_GATE_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS qc_gate_registry (
    gate_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_key       TEXT    NOT NULL UNIQUE,
    gate_name      TEXT    NOT NULL,
    dimension      TEXT    NOT NULL DEFAULT 'NA',
    checker_ref    TEXT    NOT NULL,
    metric_kind    TEXT    NOT NULL
                   CHECK (metric_kind IN ('boolean','count','pct','score_0_100')),
    metric_unit    TEXT    NOT NULL,
    metric_target  REAL    NOT NULL,
    polarity       TEXT    NOT NULL DEFAULT 'at_least'
                   CHECK (polarity IN ('at_least','at_most','equal')),
    -- `mode` — THE SECOND DECLARER, MERGED HERE 2026-09-29 (RING 5, D1).
    -- MEASURED: `qc_gate.py:194` declared this SAME table with `mode` (9/9 rows
    -- populated, read by `qc_gate.py`), and this DDL declared it WITHOUT.
    -- MEASURED CONSEQUENCE: a FRESH `ensure_schema` loses `mode` silently,
    -- because both statements are `CREATE TABLE IF NOT EXISTS` and
    -- `db_schema`'s ran first (by length tie-break: this is the longer
    -- declaration). ONE declarer per table is the fix; this is that one.
    mode           TEXT    NOT NULL DEFAULT 'declare'
                   CHECK (mode IN ('declare','run')),
    cite_ref       TEXT    NOT NULL,
    sort_order     INTEGER NOT NULL UNIQUE,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_qc_gate_active
  ON qc_gate_registry (is_active, sort_order);
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
CODE_registry_DDL = """
CREATE TABLE IF NOT EXISTS code_registry (
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
CREATE TABLE IF NOT EXISTS analyze (
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

# THE RENAME `fn_research` -> `analyze` (human, 2026-09-26).
#
# The human asked "fn_research?? fn for what? so i have that already?" and then
# decided: "要唔要改名做 `analyze`？ ... -> yes".
#
# MEASURED before the rename: `fn_research` held 2 rows, and the name `fn` was
# NOT a registered term — so the prefix named nothing a reader could look up.
# `analyze` is the name the human uses for this table, and it is the name the
# table's own job already had (`path_code`, `existing_fields_json`,
# `proposed_slices_json`, `equation`, `multi_dim_ssot_json` = an ANALYSIS).
#
# `ANALYZE` IS a SQLite keyword (the ANALYZE statement), so the name is QUOTED
# in the migration. MEASURED on a copy: `CREATE TABLE analyze` and
# `SELECT * FROM analyze` both work unquoted, but quoting is the safe form and
# costs nothing.
#
# IDEMPOTENT: it renames only when `fn_research` exists AND `analyze` does not.
# A second call is a no-op. Rows are PRESERVED — `ALTER TABLE ... RENAME TO`
# moves the table, it does not copy or drop it.
FN_RESEARCH_RENAME_TO = "analyze"


def _migrate_fn_research_to_analyze(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rename `fn_research` -> `analyze`. Idempotent; preserves every row."""
    has_old = _table_exists(conn, "fn_research")
    has_new = _table_exists(conn, "analyze")
    if not has_old:
        return {"ok": True, "renamed": False,
                "why": ("`fn_research` is absent — already renamed, or never "
                        "created"),
                "rows": (conn.execute('SELECT COUNT(*) FROM "analyze"')
                         .fetchone()[0] if has_new else 0)}
    if has_new:
        # BOTH exist: renaming would collide. Report it rather than guess which
        # one is the truth — a silent merge would destroy one of them.
        return {"ok": False, "renamed": False,
                "why": ("BOTH `fn_research` and `analyze` exist, so a rename "
                        "would collide; refusing rather than guessing which is "
                        "the truth"),
                "fn_research_rows": conn.execute(
                    "SELECT COUNT(*) FROM fn_research").fetchone()[0],
                "analyze_rows": conn.execute(
                    'SELECT COUNT(*) FROM "analyze"').fetchone()[0]}
    before = conn.execute("SELECT COUNT(*) FROM fn_research").fetchone()[0]
    conn.execute('ALTER TABLE fn_research RENAME TO "analyze"')
    after = conn.execute('SELECT COUNT(*) FROM "analyze"').fetchone()[0]
    return {"ok": True, "renamed": True, "rows_before": before,
            "rows_after": after, "rows_preserved": before == after,
            "cite": ("measured: ALTER TABLE fn_research RENAME TO analyze -> "
                     "%d row(s) preserved" % after)}


# THE TWO COLUMN RENAMES (human, 2026-09-26). The human's ruling:
#
#   "而家只係開發環境，唔係 production ... 可以直接揀最乾淨嘅設計，一次過修晒個歧義"
#
# MEASURED before the rename: ONE word `service` carried TWO unrelated meanings —
#   `ticket_center.service`    = WHICH DESK OPENS THE TICKET
#                                (chat_center / task_center / manual / llm_service)
#   `llm_service.service_key`  = WHICH MODEL POOL SERVES THE TASK
#                                (llm.text / llm.vision / eye.capture)
# and the two sets do NOT overlap (measured: intersection = []). Two copies
# followed the first meaning (`purpose_route_registry.service_key`,
# `chat_registry.service`), so all four columns are renamed together.
#
# The new names are DERIVED, not invented:
#   `ticket_origin` — the 4 values are all "where it came from"
#   `llm_route`     — the code ALREADY called it that
#                     (llm_service_store.py:11 "the route registry")
#
# IDEMPOTENT: a column is renamed only when the OLD name exists AND the NEW name
# does not. Rows are PRESERVED — `ALTER TABLE ... RENAME COLUMN` moves the
# column, it does not copy or drop it.
COLUMN_RENAMES: tuple[tuple[str, str, str], ...] = (
    ("ticket_center", "service", "ticket_origin"),
    ("llm_service", "service_key", "llm_route"),
    ("purpose_route_registry", "service_key", "ticket_origin"),
    ("chat_registry", "service", "ticket_origin"),
    ("llm_route_provider", "service_id", "llm_route_id"),
)

# THE TABLE RENAME `llm_service_provider` -> `llm_route_provider` (human,
# 2026-09-26, chose option A). The table binds a ROUTE to the models that serve
# it, so its name must align with `llm_service.llm_route`. MEASURED: 6 rows.
#
# ORDER: the TABLE is renamed FIRST, then its column — the column rename above
# names `llm_route_provider`, which does not exist until the table is renamed.
TABLE_RENAMES: tuple[tuple[str, str], ...] = (
    ("llm_service_provider", "llm_route_provider"),
)


def _migrate_table_renames(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rename `llm_service_provider` -> `llm_route_provider`. Idempotent."""
    done: list[dict[str, Any]] = []
    for (old, new) in TABLE_RENAMES:
        has_old = _table_exists(conn, old)
        has_new = _table_exists(conn, new)
        if not has_old:
            done.append({"old": old, "new": new, "renamed": False,
                         "why": ("`%s` is absent — already renamed, or never "
                                 "created" % old)})
            continue
        if has_new:
            done.append({"old": old, "new": new, "renamed": False,
                         "why": ("BOTH `%s` and `%s` exist, so a rename would "
                                 "collide; refusing rather than guessing which "
                                 "is the truth" % (old, new))})
            continue
        before = conn.execute("SELECT COUNT(*) FROM %s" % old).fetchone()[0]
        conn.execute('ALTER TABLE "%s" RENAME TO "%s"' % (old, new))
        after = conn.execute("SELECT COUNT(*) FROM %s" % new).fetchone()[0]
        done.append({"old": old, "new": new, "renamed": True,
                     "rows_before": before, "rows_after": after,
                     "rows_preserved": before == after})
    return {"ok": all(d.get("renamed") or "why" in d for d in done),
            "renames": done,
            "renamed_count": sum(1 for d in done if d.get("renamed")),
            "cite": ("measured: ALTER TABLE ... RENAME TO -> %s"
                     % [(d["old"], d["new"]) for d in done if d.get("renamed")])}


def _migrate_service_columns(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rename the four `service*` columns. Idempotent; preserves every row."""
    done: list[dict[str, Any]] = []
    for (table, old, new) in COLUMN_RENAMES:
        if not _table_exists(conn, table):
            done.append({"table": table, "renamed": False,
                         "why": "the table is absent"})
            continue
        cols = _table_columns(conn, table)
        if old not in cols:
            done.append({"table": table, "column": new, "renamed": False,
                         "why": ("`%s` is absent — already renamed, or never "
                                 "existed" % old)})
            continue
        if new in cols:
            # BOTH present: renaming would collide. Report rather than guess.
            done.append({"table": table, "renamed": False,
                         "why": ("BOTH `%s` and `%s` exist, so a rename would "
                                 "collide; refusing rather than guessing which "
                                 "is the truth" % (old, new))})
            continue
        before = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        conn.execute('ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"'
                     % (table, old, new))
        after = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        done.append({"table": table, "old": old, "column": new, "renamed": True,
                     "rows_before": before, "rows_after": after,
                     "rows_preserved": before == after})
    return {"ok": all(d.get("renamed") or "why" in d for d in done),
            "renames": done,
            "renamed_count": sum(1 for d in done if d.get("renamed")),
            "cite": ("measured: ALTER TABLE ... RENAME COLUMN -> %s"
                     % [(d["table"], d.get("column")) for d in done
                        if d.get("renamed")])}


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
        "idx_pair_qc_run_registry",
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
    skill_ref INTEGER,
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
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_case_skill
  ON skill_prompt_case (skill_key, status);
"""

# ---------------------------------------------------------------------------
# MULTI-STEP CASES (2026-09-20)
#
# WHY: `skill_prompt_case.expected` is CHECK (expected IN ('YES','NO')) — one
# binary answer per case. A case that genuinely needs 2-3 steps had nowhere to
# live, so it had to be flattened into one YES/NO (losing the steps) or split
# into N unrelated cases (losing that they belong together). A 100-streak over
# single-step binary cases therefore proves the model can answer ONE simple
# question 100 times; it says nothing about a 3-step case.
#
# BACKWARD COMPATIBILITY: a case with NO rows here is treated as ONE implicit
# step built from its existing `expected`. Nothing existing breaks, and
# `expected` stays authoritative until steps exist.
#
# `is_final` marks which step yields the CASE verdict. Both are stored so a
# mismatch between the derived verdict and `expected` is DETECTABLE rather than
# silent.
# ---------------------------------------------------------------------------
SKILL_PROMPT_STEP_DDL = """
CREATE TABLE IF NOT EXISTS skill_prompt_step (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id           INTEGER NOT NULL,
    step_no           INTEGER NOT NULL,
    question_template TEXT    NOT NULL,
    expected          TEXT    NOT NULL,
    parser            TEXT    NOT NULL DEFAULT 'result_yes_no',
    is_final          INTEGER NOT NULL DEFAULT 0,
    notes             TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (case_id, step_no),
    FOREIGN KEY (case_id) REFERENCES skill_prompt_case (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_step_case
  ON skill_prompt_step (case_id, step_no);
"""

# ---------------------------------------------------------------------------
# PROMPT COMPOSITION (2026-09-20)
#
# WHY: "prompt = case + skill" was not modelled. `skill_prompt_case` and
# `skill_prompt_ssot` were joined only AFTER the fact, via
# `skill_prompt_test_run.case_id` — which records a RESULT, not a composition.
# So a prompt could not be reproduced from its parts; it could only be copied.
#
# `composition_key` is canonical: sha256 over the sorted parts. Same parts ->
# same key, always. That is what makes a prompt reproducible rather than copied,
# and it is what lets a 100-run record WHICH composition it measured.
# ---------------------------------------------------------------------------
PROMPT_COMPOSITION_DDL = """
CREATE TABLE IF NOT EXISTS prompt_composition (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    composition_key TEXT    NOT NULL UNIQUE,
    skill_key       TEXT    NOT NULL,
    case_id         INTEGER,
    combo_key       TEXT,
    step_no         INTEGER,
    prompt_text     TEXT    NOT NULL,
    sha256          TEXT    NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER,
    FOREIGN KEY (case_id) REFERENCES skill_prompt_case (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_prompt_composition_skill
  ON prompt_composition (skill_key, created_at DESC);
"""

# ---------------------------------------------------------------------------
# REGISTERS: skill / study / wording / prompt / workflow  (2026-09-20)
#
# WHY: the formula is
#     prompt   = skill + study + wording
#     workflow = ordered sequence of INDEPENDENT prompts
# but only `format_templates` (coords.db) existed, and it conflated all three
# axes into one row. A prompt could not be reproduced from its parts, and a
# workflow had nowhere to live.
#
# These registers split the axes apart, following the ontology register pattern
# EXACTLY (init_ontology_registry.sql):
#   {x}_id PK, {x}_key UNIQUE, name, description, {parent}_id FK,
#   is_active CHECK(0,1), version, timestamps.
# Laws: SOFT DELETE ONLY (is_active=0, never DELETE) and validators READ only.
#
# CROSS-DB NOTE: `template_id` points at `format_templates.id`, which lives in
# **coords.db**, not agent.db. SQLite cannot enforce a FK across files, so
# `template_id` is a DOCUMENTED cross-DB reference, NOT a FOREIGN KEY. A fake FK
# would be worse than none.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# THE SPLIT (2026-09-21): two populations were sharing ONE name
#
# MEASURED: `skill_registry` held 7 rows (yes_no, tdd_verify, verdict_3line,
# test_json_verdict, mouse_spot_verify, ontology_relation, failure_classification)
# — verdict PARSERS and DIMENSION OWNERS. `skill_prompt_ssot` held 27 SKILLS.
#   skill_registry INTERSECT skill_prompt_ssot       = 1 of 7
#   skill_registry INTERSECT skill_contract_template = EMPTY
# One shared NAME, two disjoint populations. That overlap failure is what made an
# `skill_key_alias` table look necessary; it was WRONG, because an alias table is
# only needed when IDENTITY IS A NAME.
#
# The old table is RENAMED component_registry (it is the parent of the prompt
# COMPOSITION engine), and a real skill_registry is created in the SAME shape
# every other registry here uses. `skill_id INTEGER` is the IDENTITY;
# `skill_key` is a human-readable LABEL and never an identity.
#
# component_registry additionally carries `skill_ref INTEGER`, so a component
# that IS a skill (measured: mouse_spot_verify) is linked BY ID rather than by a
# shared name. Migration: `split_skill_registry.py` (recorded in
# `schema_migration_log`).
# ---------------------------------------------------------------------------
COMPONENT_registry_DDL = """
CREATE TABLE IF NOT EXISTS component_registry (
    skill_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key     TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    description   TEXT,
    output_schema TEXT,
    parser        TEXT,
    skill_ref     INTEGER,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version       TEXT    NOT NULL DEFAULT '1',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
    -- NO FOREIGN KEY ON `skill_ref`. The comment above says a component that IS
    -- a skill "is linked BY ID rather than by a shared name" -- but 67 of 68 rows
    -- are NULL, the repo's own `null_columns` standard classifies the column
    -- `KEEP_NULL`, and it resolves nothing. THE POLICY: an optional / loose /
    -- legacy reference gets NO FK (factor 8). The reference still works; it is
    -- checked in the business layer.
);
CREATE INDEX IF NOT EXISTS idx_component_registry_active
  ON component_registry (is_active, skill_key);
"""

# The REAL skills. Same shape as channel_registry / module_registry /
# db_table_registry / db_field_registry / capability_registry — COPIED, not
# invented. `skill_key` is UNIQUE but it is a LABEL; joins use `skill_id`.
SKILL_registry_DDL = """
CREATE TABLE IF NOT EXISTS skill_registry (
    skill_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key     TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    description   TEXT,
    output_schema TEXT,
    parser        TEXT,
    taxonomy_path TEXT,
    -- THE CAPABILITY TAGS. `skill_factor.applies_to` is matched against this
    -- column, so it is the field that DECIDES which factors apply to a skill.
    -- It is NOT `taxonomy_path`: that is a canonical ontology path with hard
    -- validation, not a tag. COMMA-SEPARATED, so one skill can be both `code`
    -- and `crud`. `NA` (never NULL) follows the no_null standard, so a
    -- surviving NULL means the standardiser did not run, which is a defect.
    capability_tags TEXT  NOT NULL DEFAULT 'NA',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version       TEXT    NOT NULL DEFAULT '1',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_skill_registry_active
  ON skill_registry (is_active, skill_key);
"""

# ---- soft_delete_log: WHY a row was disabled, and WHO can check it ----
# DEFECT FOUND BY MEASURING IT (2026-09-21): `register_store.soft_delete()` set
# `is_active = 0` and recorded NOTHING. Measured on the one real soft delete in
# this repo (`skill_registry.skill_key = 'mcp-tool-checklist'`): the reason lived
# only inside the `description` PROSE ("Soft-disabled 2026-09-21; history kept"),
# there was no `cite_ref` column anywhere, and no orphan check existed. So the
# row was disabled and NOBODY could check WHY — which is "I think it is a
# duplicate", not a finding.
#
# This is the same rule as `skill_lesson.source_ref`: a soft delete is a
# FINDING, and a finding with no citation is DISCARDED at the write site.
#
# APPEND-ONLY, for the same reason as `skill_factor_proof_log`: a trace that can
# be rewritten is not a trace. A row can be soft-deleted, revived, and
# soft-deleted again, and all three events must survive.
SOFT_DELETE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS soft_delete_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT    NOT NULL,
    id_column   TEXT    NOT NULL,
    row_id      INTEGER NOT NULL,
    row_key     TEXT,
    cite_ref    TEXT    NOT NULL,
    reason      TEXT,
    actor       TEXT,
    orphan_refs INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_soft_delete_log_row
  ON soft_delete_log (table_name, row_id, created_at);
"""

STUDY_registry_DDL = """
CREATE TABLE IF NOT EXISTS study_registry (
    study_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    study_key   TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    description TEXT,
    skill_id    INTEGER NOT NULL,
    fields_json TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version     TEXT    NOT NULL DEFAULT '1',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (skill_id) REFERENCES component_registry (skill_id)
);
CREATE INDEX IF NOT EXISTS idx_study_registry_active
  ON study_registry (is_active, study_key);
CREATE INDEX IF NOT EXISTS idx_study_registry_skill
  ON study_registry (skill_id);
"""

# ---------------------------------------------------------------------------
# study_template  (the REQUIRED-FIELD declaration for a study)
#
# WHY THIS EXISTS (the human, 2026-09-27):
#
#     "why i have a lot of work, but seems study template is totally missing?
#      can you help to backfill that for me"
#
# MEASURED before this: `study_registry` had 63 rows and NO declaration of what
# a study must carry. `fields_json` (the case payload) was populated for 2 of 63
# rows, and nothing said which fields a study of a given KIND must have. So a
# study could be created with no fields at all and nothing could refuse it.
#
# THE PATTERN IS `factor_template`, which is PROVEN in this repo: a declaration
# table with `kind` + `is_required` + `rule` + `why`, read by
# `template_conformance.check_table()`. This is the SAME pattern for studies.
#
# THE HUMAN'S PROPOSED SHAPE, CORRECTED (see the plan, section 6):
#
#     id | Type | key_factor | is_active
#
# `key_factor` is the WRONG column name. MEASURED, `terminology_registry` term
# 75: a key factor is a CONFORMANCE, not a class -- "There is NO `is_key` column
# anywhere". A conformance cannot hold a value. The column the human wants is
# the SUBJECT the study is about, and `subject_kind` is the vocabulary this repo
# already uses for exactly that.
#
# `Type` is not a registered term and `type` collides with
# `entity_type_registry.type_letter`, so the column is `study_kind`, following
# the existing `subject_kind` / `metric_kind` / `term_kind` convention.
STUDY_TEMPLATE_DDL = """
CREATE TABLE IF NOT EXISTS study_template (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    study_kind  TEXT    NOT NULL,
    subject     TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    cite_ref    TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (study_kind, subject)
);
CREATE INDEX IF NOT EXISTS idx_study_template_kind
  ON study_template (study_kind, is_active);
"""

# ---------------------------------------------------------------------------
# generator_required_field  (what a generator needs BEFORE submit)
#
# WHY THIS EXISTS (the human, 2026-09-27):
#
#     "have a table form request all user for this generator need to have
#      before submit"
#
# MEASURED before this: the five generators' required inputs lived in
# `generator_center.GENERATORS` -- CODE. A generator that needs a 4th axis could
# not be extended without a code change, and nothing could tell the UI what to
# ask for. That is the same defect family as the hardcoded `llm_service` type.
#
# Modelled on `factor_template`: `kind` + `is_required` + `rule` + `why` +
# `sort_order`, so the SAME reader (`template_conformance.check_table`) can
# check it.
GENERATOR_REQUIRED_FIELD_DDL = """
-- ===========================================================================
-- TOMBSTONE (2026-09-28). **THIS TABLE IS RETIRED. DO NOT RE-CREATE IT.**
--
-- S8 Option A (the human, 2026-09-28) MERGED this table into `pattern_template`
-- as `subject_kind='generator_field'`, because MEASURED: the two already shared
-- the core VERBATIM -- `rule, why, sort_order, is_active` plus `is_required` --
-- so keeping both was the "two formats" defect the human named, not a feature.
--
-- THE ROWS NOW LIVE AT:
--     pattern_template.subject_kind = 'generator_field'
--     pattern_template.subject_ref  = the generator key
--     pattern_template.item_kind    = the field name
-- and the ONE reader is `generator_center.required_fields`, which ALIASES the
-- columns back (`item_kind AS field_name`) so no caller changed.
--
-- WHY THE CONSTANT IS KEPT AND NOT DELETED: the DDL is still listed in
-- `ALL_DDL` below, so a NEW database would otherwise be handed this table with
-- no data and no reader -- a store that looks authoritative and is empty. That
-- is the `dead_ddl_removed` trap. Instead the statement is `DROP TABLE IF
-- EXISTS` + a deliberately EMPTY body, so:
--   * a FRESH database gets NO `generator_required_field` table at all;
--   * an EXISTING database keeps the name resolvable until the human authorises
--     S8-5, which drops it;
--   * nothing can silently re-create it, because this constant holds no
--     statement that makes a table.
--
-- RETIRING IT FOR REAL (S8-5) IS A HUMAN DECISION, gated on the shadow window
-- (`_shadow_generator_field_task.py --status` -> READY FOR THE DROP: True).
--
-- The retired NAME stays a registered term (`terminology_registry` term 1526),
-- rewritten to say RETIRED, because the name is still read in old citations and
-- a name that resolves to nothing makes every later reader pick the wrong one.
-- ===========================================================================
"""

# ---------------------------------------------------------------------------
# wording_registry  (FK -> skill)
#
# WHY: `wording` is not one blob of text — it is a set of DIMENSIONS
# (context / criterion / negation / output), each with several VALUES. A prompt
# picks ONE value per dimension.
#
# This is the capability ported from the retired `prompt_dimension.py`, which
# held exactly this shape (10 values across 4 dimensions = 36 combinations).
# Keeping it as a register means the values are DATA, not code: they can be
# added or deprecated without touching the composer.
# ---------------------------------------------------------------------------
WORDING_registry_DDL = """
CREATE TABLE IF NOT EXISTS wording_registry (
    wording_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    wording_key TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    description TEXT,
    skill_id    INTEGER NOT NULL,
    dim_key     TEXT    NOT NULL,
    template    TEXT    NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version     TEXT    NOT NULL DEFAULT '1',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (skill_id, dim_key, wording_key),
    FOREIGN KEY (skill_id) REFERENCES component_registry (skill_id)
);
CREATE INDEX IF NOT EXISTS idx_wording_registry_active
  ON wording_registry (is_active, skill_id, dim_key, sort_order);
"""

PROMPT_registry_DDL = """
CREATE TABLE IF NOT EXISTS prompt_registry (
    prompt_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_key  TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    description TEXT,
    skill_id    INTEGER NOT NULL,
    study_id    INTEGER NOT NULL,
    template_id INTEGER,
    -- THE FIVE FIRST-PRINCIPLE ANSWERS (2026-09-22).
    --
    -- The user: "first_principle can be the qc pass / fail for prompt output?
    -- output need to have this test to = is_active at prompt_registry / so prompt
    -- is design for proof for target / is it correct?" and then "三個都做 and why
    -- we know prompt design pass / fail / as prompt is generatot by formula, so
    -- we can have enough detail to help for improve formula design / measured
    -- unit is the key".
    --
    -- A prompt is NOT text. It is a HYPOTHESIS WITH A MEASURED UNIT:
    --   failure_mode  how this prompt could answer wrongly
    --   observable    what a third party can see
    --   unit          the MEASURED UNIT  <- "measured unit is the key"
    --   threshold     how many wins = pass
    --   independence  can this prompt fail alone?
    --
    -- `unit` + `threshold` are what the oracle and the streak target are DERIVED
    -- from, so `STREAK_TARGET` stops being a hard-coded 110.
    --
    -- `NOT NULL DEFAULT 'NA'` follows the no_null standard: a surviving NULL
    -- would mean the standardiser did not run, which is a defect, not an
    -- ambiguity. `register_prompt` REFUSES a prompt whose answers are still 'NA'.
    failure_mode TEXT    NOT NULL DEFAULT 'NA',
    observable   TEXT    NOT NULL DEFAULT 'NA',
    unit         TEXT    NOT NULL DEFAULT 'NA',
    threshold    TEXT    NOT NULL DEFAULT 'NA',
    independence TEXT    NOT NULL DEFAULT 'NA',
    -- THE ORACLE REFERENCE. Which declarative rule decides pass/fail for this
    -- prompt. A Python function would be code; a rule is data, and
    -- `field_tdd_rule` already holds `rule_json`.
    oracle_ref   TEXT    NOT NULL DEFAULT 'NA',
    -- DEFAULT 0, NOT 1 (2026-09-22). MEASURED: this was `DEFAULT 1`, so a prompt
    -- was ACTIVE the moment it was registered — a prompt nobody proved. Every
    -- other register added this session defaults to 0 (`taxonomy_level_registry`,
    -- `dimension_binding_registry`, `code_location_registry`). `activation_gate`
    -- is the ONLY writer of `is_active=1`, and it requires a 100-run streak.
    is_active   INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    version     TEXT    NOT NULL DEFAULT '1',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (skill_id) REFERENCES component_registry (skill_id),
    FOREIGN KEY (study_id) REFERENCES study_registry (study_id)
);
CREATE INDEX IF NOT EXISTS idx_prompt_registry_active
  ON prompt_registry (is_active, prompt_key);
CREATE INDEX IF NOT EXISTS idx_prompt_registry_skill
  ON prompt_registry (skill_id);
CREATE INDEX IF NOT EXISTS idx_prompt_registry_study
  ON prompt_registry (study_id);
"""

# ---------------------------------------------------------------------------
# prompt_wording  (junction: a prompt picks ONE wording value PER dimension)
#
# WHY a junction and not a single `wording_id` column: a prompt is a
# COMBINATION. `context=strict_boundary + negation=stack + output=with_unknown`
# is one prompt. A single FK could only express one dimension, which would make
# the 36-combination capability impossible to store.
#
# UNIQUE(prompt_id, dim_key) enforces "one value per dimension" — two values for
# the same dimension is a contradiction, not a variant.
# ---------------------------------------------------------------------------
PROMPT_WORDING_DDL = """
CREATE TABLE IF NOT EXISTS prompt_wording (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id  INTEGER NOT NULL,
    wording_id INTEGER NOT NULL,
    dim_key    TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (prompt_id, dim_key),
    FOREIGN KEY (prompt_id)  REFERENCES prompt_registry (prompt_id),
    FOREIGN KEY (wording_id) REFERENCES wording_registry (wording_id)
);
CREATE INDEX IF NOT EXISTS idx_prompt_wording_prompt
  ON prompt_wording (prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_wording_wording
  ON prompt_wording (wording_id);
"""

WORKFLOW_registry_DDL = """
CREATE TABLE IF NOT EXISTS workflow_registry (
    workflow_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_key TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    description  TEXT,
    -- DEFAULT 0, NOT 1 (2026-09-22). MEASURED: this was `DEFAULT 1`, so a flow
    -- was ACTIVE the moment it was created — a flow nobody proved. The user:
    -- "flow is you have the system and register at the table > is_active = 0 /
    -- and need to proofed is that work with proof run report".
    --
    -- Every register added this session defaults to 0 (`taxonomy_level_registry`,
    -- `dimension_binding_registry`, `code_location_registry`, `prompt_registry`).
    -- `activation_gate` is the ONLY writer of `is_active=1`, and it requires a
    -- measured streak.
    is_active    INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    -- WHO MADE IT (2026-09-22). The user: "so who make the system and who QC for
    -- work". MEASURED: neither `workflow_registry` nor `prompt_registry` had a
    -- maker column, so "who made this?" was unanswerable.
    --
    -- The value is a MODULE name (`question_flow`), not a person: in this system
    -- the maker is a program. `NOT NULL DEFAULT 'NA'` follows the no_null
    -- standard, so a surviving NULL always means the standardiser did not run.
    created_by   TEXT    NOT NULL DEFAULT 'NA',
    version      TEXT    NOT NULL DEFAULT '1',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_workflow_registry_active
  ON workflow_registry (is_active, workflow_key);
"""

WORKFLOW_STEP_DDL = """
CREATE TABLE IF NOT EXISTS workflow_step (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL,
    step_no     INTEGER NOT NULL,
    prompt_id   INTEGER NOT NULL,
    is_final    INTEGER NOT NULL DEFAULT 0 CHECK (is_final IN (0, 1)),
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    step_status TEXT,
    UNIQUE (workflow_id, step_no),
    FOREIGN KEY (workflow_id) REFERENCES workflow_registry (workflow_id),
    FOREIGN KEY (prompt_id)   REFERENCES prompt_registry (prompt_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_step_workflow
  ON workflow_step (workflow_id, step_no);
CREATE INDEX IF NOT EXISTS idx_workflow_step_prompt
  ON workflow_step (prompt_id);
"""

# ---------------------------------------------------------------------------
# THE FLOW COLUMNS (SCOPE AE, added 2026-09-28).
#
# THE HUMAN: "question is according to the answer by factor!!! not waste token"
#
# MEASURED BEFORE THESE COLUMNS EXISTED:
#   * `workflow_step` had NO `factor_key`, so a step could not say WHICH factor
#     it measures -- even though `question_template_registry` DOES carry one, so
#     the binding was LOST the moment a question became a step.
#   * `workflow_step` had NO branch column, and `run_flow`'s loop was
#     `for step in steps:` with the ONLY exit a failed gate. So the next question
#     was NEVER selected by the answer.
#   * the waste: `stepwise_ask_db_field_registry` 23 steps / 22 asked (steps
#     3..22 are the SAME question text 20 times); `stepwise_ask_function_registry`
#     27/26; `stepwise_ask_channel_registry` 21/20; 91 steps asked across all
#     flows.
#
# `branch_on` is JSON `{answer: next_step_no}`. It is TEXT rather than a child
# table because a step's successor is a PROPERTY of the step, and a child table
# would let a step have two successors with no rule saying which wins.
# ---------------------------------------------------------------------------
WORKFLOW_STEP_FLOW_COLUMNS = (
    ("factor_key", "TEXT"),
    ("branch_on", "TEXT"),
    ("is_terminal", "INTEGER NOT NULL DEFAULT 0"),
    # 🔴 SEVEN MORE, ADDED 2026-09-29 (RING 5, D2). THE LIST WAS INCOMPLETE.
    # MEASURED: LIVE `workflow_step` carries 19 columns and a FRESH build 9. The
    # ten missing split into these three (above) and SEVEN that no declaration
    # ever mentioned — yet all seven are POPULATED (103/103 rows) and READ by
    # real modules (`build_step_registry`, `chat_level`, `chat_report_flow`,
    # `logic_generator`, `question_flow`, `mouse_spot_helper`, `pattern_template`,
    # `llm_100_run_harness`).
    #
    # An undeclared POPULATED column is NOT dead data: deleting it to make a
    # comparison green would delete a fact. The declarer list was the incomplete
    # side, so the list is completed.
    #
    # `is_terminal` was ALREADY here; it is left where it was. `branch_on` and
    # `factor_key` likewise. The ADDITIONS are the seven below, and the two that
    # are NOT NULL on LIVE (`expected`, `layer_key`, `parser`, `step_kind`,
    # `question_template` have 103/103 non-NULL; `is_terminal` 103/103) are
    # declared with the same nullability LIVE has, measured per column.
    ("expected", "TEXT NOT NULL DEFAULT ''"),
    ("step_kind", "TEXT NOT NULL DEFAULT 'NA'"),
    ("layer_key", "TEXT NOT NULL DEFAULT 'NA'"),
    ("parser", "TEXT NOT NULL DEFAULT 'NA'"),
    ("question_template", "TEXT NOT NULL DEFAULT 'NA'"),
    ("verdict_params", "TEXT"),
    ("verdict_rule", "TEXT"),
)

# ---------------------------------------------------------------------------
# prompt_combo  (a RESULT log, not a register)
#
# WHY it lives here now: it was defined inside the retired `prompt_dimension.py`.
# `prompt_generator.record_combo()` still writes it, so the DDL had to move to
# the schema SSOT — otherwise the table would vanish with the retired module.
#
# It records the MEASURED outcome of a combination (accuracy, streaks, overfit),
# which is why it is a log and not a register: a register describes what a thing
# IS, a log records what HAPPENED.
# ---------------------------------------------------------------------------
PROMPT_COMBO_DDL = """
CREATE TABLE IF NOT EXISTS prompt_combo (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_key     TEXT NOT NULL,
    combo_key     TEXT NOT NULL,
    prompt_key    TEXT NOT NULL,
    axes_json     TEXT NOT NULL,
    template_text TEXT,
    status        TEXT NOT NULL DEFAULT 'candidate',
    accuracy_pct        REAL,
    balanced_accuracy_pct REAL,
    train_ba_pct  REAL,
    heldout_ba_pct REAL,
    train_ba_min  REAL,
    train_ba_max  REAL,
    hold_ba_min   REAL,
    hold_ba_max   REAL,
    seed_runs     INTEGER,
    hold_perfect  INTEGER,
    hold_discriminating INTEGER,
    best_streak   INTEGER,
    answer_classes TEXT,
    run_id        TEXT,
    overfit       INTEGER NOT NULL DEFAULT 0,
    promoted      INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER,
    -- `explain` — ADDED 2026-09-29 (RING 5, D2). MEASURED: LIVE has it, 36/36
    -- rows populated, read by `full_cycle_gap.py` and `mouse_spot_helper.py`,
    -- and NO declarer mentioned it. Declared here and in the additive tuple, so
    -- a NEW table is born correct and an EXISTING one is migrated.
    explain       TEXT,
    UNIQUE (skill_key, combo_key)
);
CREATE INDEX IF NOT EXISTS idx_prompt_combo_skill
  ON prompt_combo (skill_key, status);
"""

# ---------------------------------------------------------------------------
# THE QUESTION-TEMPLATE RECOMMENDATION (added 2026-09-25).
#
# WHY (the human): "when we need to find factor X -> system can recommend
# template for us / totally help 7B".
#
# MEASURED BEFORE THIS: THREE of the four tables the human proposed ALREADY
# EXIST under other names, and creating them again would be a SECOND source of
# truth for the same thing:
#
#   the human's "question template"    -> `prompt_combo`        (36 rows)
#   the human's "question usuage"      -> `prompt_composition`  (39 rows)
#   the human's "question performance" -> `prompt_combo`'s own columns
#                                         (balanced_accuracy_pct, train_ba_pct,
#                                          heldout_ba_pct, seed_runs, ...)
#
# MEASURED: `prompt_combo` IS the question-template table — every row carries
# its `axes_json` (the multi measured units), its `template_text` (the prose),
# and its measured performance.
#
# THE ONE REAL GAP, MEASURED: ZERO tables have both a `factor_*` column and a
# prompt/wording/question/template name. `skill_factor_registry` has
# `factor_id`/`factor_key` (40 rows) and no template link; `prompt_combo` has
# `skill_key`/`combo_key` (36 rows) and no factor link. So "given factor X,
# recommend a template" is NOT answerable today. That is what these two tables
# add — and nothing else.
#
# `unit_id` HAD NO REFERENT EITHER. MEASURED: there is no table named `*unit*`.
# The units are TEXT columns (`wording_registry.dim_key` — 10 values;
# `skill_factor_registry.metric_kind` — 4 values). A `unit_id` column needs a
# register to point at, or it is a number about nothing. Hence `unit_registry`.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# THE MODEL ALIAS (added 2026-09-25).
#
# WHY (the human): "get by my LLM table under identity table? / or you are
# fucking for handcode again"
#
# MEASURED, and the human is right: the model must come from the REGISTER
# (`llm_model`), linked by `identity_registry.llm_id`. My previous change wrote a
# free-text name read from `chatSessions/*.jsonl` — a design the human had
# ALREADY REJECTED, recorded verbatim in `identity_llm.py`:
#
#     "chatsession is totally wrong design, will remove"
#     "A model must come from a REGISTER, not from another program's log file."
#
# THE PROBLEM AN ALIAS SOLVES. MEASURED, the provider's own id and the
# registered id DIFFER:
#
#     VS Code id   : deepseek/deepseek-v4.1-flash
#     llm_model id : deepseek/deepseek-v4-flash-0731
#
# So a name cannot be resolved by equality. A SUBSTRING match would join them by
# LUCK, and would join the WRONG model the moment a second DeepSeek variant is
# registered. An alias is a DECLARED fact; a fuzzy match is a guess.
LLM_MODEL_ALIAS_DDL = """
CREATE TABLE IF NOT EXISTS llm_model_alias (
    alias_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    llm_id    INTEGER NOT NULL,
    alias     TEXT NOT NULL UNIQUE,
    source    TEXT,
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_model_alias_llm
  ON llm_model_alias (llm_id, is_active);
"""

UNIT_registry_DDL = """
CREATE TABLE IF NOT EXISTS unit_registry (
    unit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_key      TEXT NOT NULL UNIQUE,
    unit_kind     TEXT NOT NULL,
    source_column TEXT,
    is_active     INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_unit_registry_kind
  ON unit_registry (unit_kind, is_active);
"""

# `scoring` IS DERIVED, NEVER DECLARED. It is computed from `prompt_combo`'s own
# measured performance, so a caller cannot assert a score — the same reason
# `prompt_generator.proof_type_for` derives `proof_type` instead of accepting it.
QUESTION_TEMPLATE_FACTOR_DDL = """
CREATE TABLE IF NOT EXISTS question_template_factor (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    combo_id   INTEGER NOT NULL,
    factor_id  INTEGER NOT NULL,
    scoring    REAL,
    is_active  INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (combo_id, factor_id)
);
CREATE INDEX IF NOT EXISTS idx_qtf_factor
  ON question_template_factor (factor_id, scoring DESC);
"""

# ---------------------------------------------------------------------------
# THE CHAT -> ENVIRONMENT LINK, AND THE STEP STATUS (2026-09-25).
#
# THE HUMAN, on /llm-tasks/chat_identity/recent:
#     "+ image = environment, display environment = kind + product + surface"
#     "id | researching / id | writing / id | verfitiy"
#
# MEASURED, and this is the gap: `working_environment` (92 rows) ALREADY stores
# exactly the human's formula —
#     environment_id=6  kind=IDE  product='VS Code'  surface=chat
#     display='IDE > VS Code > chat'
# — and NO chat table links to it:
#     chat_main / chat_identity_log / chat_center_message / identity_registry /
#     chat_registry  ->  env cols: NONE
#
# The ONLY existing path is a FUZZY JOIN BY NAME:
#     identity_registry.channel='local_pc'
#       -> channel_registry.name='Local PC'
#       -> working_environment.product='Local PC'
# A name join is this repo's recurring defect #4 ("a fuzzy match joins by luck").
# Resolving it ONCE at register time and STORING the id makes the read a plain
# FK join that cannot drift.
#
# `step_status` is the human's step lifecycle. MEASURED: `workflow_step` had NO
# status column, and the nearest existing vocabulary is
# `task_lifecycle_log.task_state` (draft / validating / researching / validated /
# proposal_draft), so `researching` is NOT an invented term.
# ---------------------------------------------------------------------------
CHAT_ENVIRONMENT_LINK_DDL = """
CREATE TABLE IF NOT EXISTS _chat_env_link_marker (id INTEGER);
DROP TABLE IF EXISTS _chat_env_link_marker;
"""

# The alias that bridges VS Code's own model id to the register's row.
# MEASURED: VS Code reports `deepseek/deepseek-v4.1-flash` while `llm_model`
# stores `deepseek/deepseek-v4-flash-0731` — NO exact match, so the alias table
# is the bridge and it was EMPTY (0 rows).
LLM_MODEL_ALIAS_SEED = [
    ("deepseek/deepseek-v4.1-flash", 4, "measured: VS Code sendOptions.userSelectedModelId"),
    ("deepseek/deepseek-v4-flash-0731", 4, "measured: llm_model.model_id (self-alias)"),
]

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
    skill_ref INTEGER,
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
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skill_prompt_inference_skill
  ON skill_prompt_inference (skill_key, created_at DESC);
"""

# ---- Skill Learning Center (R&D zone: lessons + ask<->confirm mismatches) ----

SKILL_LESSON_DDL = """
CREATE TABLE IF NOT EXISTS skill_lesson (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_key        TEXT    NOT NULL UNIQUE,
    skill_key         TEXT    NOT NULL,
    source_type       TEXT    NOT NULL DEFAULT 'self_fail'
                      CHECK (source_type IN ('self_fail', 'github_proofed_lesson',
                                             'factor_change')),
    root_cause        TEXT,
    lesson_text       TEXT    NOT NULL,
    suggested_fix     TEXT,
    source_ref        TEXT,
    status            TEXT    NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'reviewed', 'merged')),
    -- THE RATING. "highest rating" was a requirement with nowhere to live, so
    -- it could not be expressed, let alone sorted by. Measured 2026-09-21: the
    -- earlier GitHub survey recorded "GitHub does NOT show star counts without
    -- login, so 'highest rating' could NOT be reported". That was true of the
    -- HTML page and FALSE of the API — `api.github.com/repos/<owner>/<repo>`
    -- returns `stargazers_count` with no login (verified: hoardable=59,
    -- cleanerversion=134). So the rating is now a stored number.
    --
    -- `rating_source` names WHERE the number came from, because a star count and
    -- a self-measured proof count are not the same kind of number and must not
    -- be compared as if they were.
    rating            INTEGER NOT NULL DEFAULT 0,
    rating_source     TEXT    NOT NULL DEFAULT 'NA',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skill_lesson_skill
  ON skill_lesson (skill_key, status);
"""
# NOTE: the `rating` index is deliberately NOT in the DDL above. `executescript`
# runs `CREATE INDEX` AFTER `CREATE TABLE IF NOT EXISTS`, and on an EXISTING
# table that IF NOT EXISTS does nothing — so the index would reference columns
# that do not exist yet and the whole script would fail with
# "no such column: rating_source" (measured, the same defect the `skill_key`
# index hit in `skill_factor.py`). The columns are added by the additive
# migration in `logic_training.ensure_schema()`, and the index is created THERE,
# after them.

# WHY A FACTOR CHANGE NEEDS ITS OWN LOG
# -------------------------------------
# The human's rule: "Create, Update -> by reason; by reason will be lesson like
# skill lesson". A factor is a RULE the system enforces, so changing one is a
# decision, and a decision with no recorded reason is indistinguishable from an
# accident. `skill_factor_registry` holds the CURRENT state only — it cannot say
# why a rule exists or what it replaced, because an UPDATE overwrites.
#
# So every create/update appends here: the reason, the citation, and the
# before/after. The reason is ALSO filed as a `skill_lesson` (source_type
# 'factor_change'), so the same reasoning is visible where lessons are read.
FACTOR_CHANGE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS factor_change_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_key  TEXT    NOT NULL,
    action      TEXT    NOT NULL CHECK (action IN ('create', 'update')),
    reason      TEXT    NOT NULL,
    cite_ref    TEXT    NOT NULL,
    before_json TEXT,
    after_json  TEXT,
    lesson_key  TEXT,
    -- WHICH DISTILLATION ROUND produced this change. The human's model is
    -- ITERATED, not one-shot:
    --     factor -> distill -> factor -> distill -> ... -> QUESTION
    -- so a change belongs to a ROUND, and "where did this come from?" is
    -- answerable only if the round is recorded. 0 = created outside the
    -- round loop (a manual register).
    round_index INTEGER NOT NULL DEFAULT 0,
    -- 'pass' / 'fail' / 'undefined' -- the audit verdict AT this round. A FAIL
    -- stops the iteration and becomes the lesson, which is why the verdict is
    -- stored next to the reason rather than inferred later.
    round_verdict TEXT NOT NULL DEFAULT 'NA',
    changed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_factor_change_factor
  ON factor_change_log (factor_key, changed_at);
-- NOTE: the `idx_factor_change_round` index is deliberately NOT here. It spans
-- `round_index`, which a LEGACY `factor_change_log` does not have, and
-- `CREATE TABLE IF NOT EXISTS` does NOT add a column. Measured 2026-09-21: on a
-- live DB this script raised `no such column: round_index` and `ensure_schema`
-- died BEFORE the ALTER that would have fixed it — so the repair was
-- unreachable. An index that spans a migrated column must be created AFTER the
-- migration, in `skill_factor.ensure_schema`. This is the trap the comment
-- above `FACTOR_PROOF_DDL` already warns about, met a second time.
"""

# WHY A LESSON MUST NAME THE LOGIC IT TRAINS
# -------------------------------------------
# The human's framing: "logic training, i think is" ... "or factor finding, i
# don't know, as prompt is by combination, your turn then".
#
# Three objects, and they are easy to conflate:
#     LESSON  = training data   -- HOW to find
#     FACTOR  = the finding     -- WHAT was found
#     PROMPT  = the combination -- skill + content + wording
#
# Measured 2026-09-21: NOTHING connected a lesson to the logic it trains. The
# `logic_layer_*` dimensions existed in `wording_registry`, and `skill_lesson`
# existed, and no row joined them. So a lesson could be filed, reviewed and
# merged without ever reaching the logic it was supposed to improve — training
# data that trains nothing.
#
# This table is that join. A lesson names the LAYER it trains, and the layer is
# the same `dim_key`/`wording_key` pair the prompt generator composes from, so
# the path lesson -> logic -> prompt is walkable in one direction.
LESSON_LOGIC_LINK_DDL = """
CREATE TABLE IF NOT EXISTS lesson_logic_link (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    lesson_key  TEXT    NOT NULL,
    dim_key     TEXT    NOT NULL,
    layer_key   TEXT    NOT NULL,
    -- HOW the lesson trains the layer. A lesson that merely MENTIONS a layer is
    -- not training it, so the relation is stated rather than assumed.
    relation    TEXT    NOT NULL DEFAULT 'trains'
                CHECK (relation IN ('trains', 'contradicts', 'exemplifies')),
    cite_ref    TEXT    NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (lesson_key, dim_key, layer_key, relation)
);
CREATE INDEX IF NOT EXISTS idx_lesson_logic_layer
  ON lesson_logic_link (dim_key, layer_key);
CREATE INDEX IF NOT EXISTS idx_lesson_logic_lesson
  ON lesson_logic_link (lesson_key);
"""

# THE FACTOR TEMPLATE AS A TABLE
# ------------------------------
# The human's correction (2026-09-22): "factor template table, or something like
# that, name can help to define".
#
# The template was a Python dict in `factor_distill.py`. That is the same defect
# the tag registry and the wording register were fixed for: a definition that
# lives in code cannot be extended without a code change, and cannot be read by
# anything that does not import that module. A template is DATA — it says which
# fields a factor must carry and WHY — so it belongs in a table.
#
# `field_name` is the column on `skill_factor_registry`; `why` is the reason the
# field exists, which is what makes the template self-explaining rather than a
# bare list of names.
FACTOR_TEMPLATE_DDL = """
CREATE TABLE IF NOT EXISTS factor_template (
    field_name   TEXT    PRIMARY KEY,
    kind         TEXT    NOT NULL DEFAULT 'text'
                 CHECK (kind IN ('slug', 'text', 'enum', 'citation', 'number')),
    is_required  INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0, 1)),
    why          TEXT    NOT NULL,
    -- THE RULE, as TABLE knowledge.
    --
    -- WHY THIS COLUMN EXISTS (the user, 2026-09-23):
    --
    --     "definition for key factor output = can measured unit"
    --     "if not, key factor is meaningless"
    --
    -- MEASURED before this: the rule "a unit must NAME its subject" lived in
    -- `factor_first_principle.assert_measurable()` — CODE. The template had a
    -- `metric_unit` ROW but no RULE row, so the rule could not be read,
    -- extended, or cited from the table. That is the same defect family as the
    -- hardcoded `llm_service` type: a rule that lives in code cannot be changed
    -- without a code change, and cannot be read by anything that does not import
    -- the module.
    --
    -- `rule` names a CHECK the gate can apply. `NA` means "no rule beyond
    -- is_required" — declared EXPLICITLY rather than left to a silent NULL.
    rule         TEXT    NOT NULL DEFAULT 'NA',
    sort_order   INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ADDITIVE columns for a database that predates `rule`. MEASURED, and it is the
# trap already recorded for `llm_model` and `llm_service.needs_flag`:
# `CREATE TABLE IF NOT EXISTS` does NOT add a column to an existing table, so the
# live `factor_template` (measured this turn: 9 rows, no `rule`) would keep its
# old shape and every read of the rule would fail with "no such column".
FACTOR_TEMPLATE_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("rule", "TEXT NOT NULL DEFAULT 'NA'"),
)

# The seed. Populated once, then the TABLE is the truth — the same pattern as
# `CAPABILITY_TAGS` and `ticket_store.DEFAULT_SERVICES`.
#
# The 4th element is the RULE. It is the checkable form of the `why`, so a gate
# can apply it without importing the module that wrote the `why`.
FACTOR_TEMPLATE_SEED: tuple[tuple[str, str, str, str], ...] = (
    ("factor_key", "slug", "identity; a factor with no key cannot be cited",
     "NA"),
    ("name", "text", "a human reads this in the table", "NA"),
    ("rule_definition", "text", "the rule itself, in one sentence", "NA"),
    ("action", "text", "what to DO when it fails; a rule with no action is advice",
     "NA"),
    ("metric_kind", "enum", "a metric that cannot be scored is decorative",
     "must_be_known_kind"),
    ("metric_unit", "text", "a number without a unit cannot be audited",
     "must_name_subject"),
    ("metric_target", "text", "the pass condition", "must_be_value_of_kind"),
    ("proof_prefix", "slug", "so a proof can be found by prefix", "NA"),
    ("cite_ref", "citation", "no citation, no factor", "must_be_checkable"),
)

# THE REGISTER SPINE (B, 2026-09-22)
# --------------------------------
# The human's observation: "prompt generator and skill generator are the same...
# does it say we can have better design to have them group together under
# lightweight table design, not need to have so many table?"
#
# MEASURED, and they were right: `key + scope + content + is_active` is present
# in 10 of 14 register tables. `wording_registry`, `component_registry`,
# `study_registry`, `prompt_registry` and `skill_registry` all carry
# key + scope + content + is_active + version. So these are ONE shape
# instantiated many times, and a future logic generator (chat -> conversation
# management) would reuse the same shape again.
#
# WHY THIS IS NOT ONE MERGED TABLE
# --------------------------------
# The tables are NOT interchangeable. Measured differences that matter:
#   * `wording_registry` has a COMPOSITE key (skill_id, dim_key, wording_key)
#   * `skill_factor_registry` now has an EXPRESSION composite key + experience
#   * `capability_tag_registry` requires a `cite_ref`; `factor_template` requires
#     a `why` per field
#   * `prompt_wording` and `lesson_logic_link` are JOIN tables, not registers
# A single table would have to drop those constraints or express them as
# kind-conditional CHECKs, and a constraint that depends on a discriminator is
# exactly the "looks like a constraint but is not" defect this codebase already
# has two recorded instances of.
#
# So the design is: DECLARE the shared shape as DATA, keep each register's own
# constraints, and provide ONE read-shape over all of them. Fewer KINDS of table
# to reason about, without deleting a constraint to get there.
#
# ---- `REGISTER_KIND_DDL` / `REGISTER_FIELD_DDL` REMOVED 2026-09-25 --------
#
# MEASURED: both constants were DEFINED here and NEVER EXECUTED — neither name
# appeared in any `executescript()` call or in the `ensure_task_center_schema()`
# DDL list — and NO file read or wrote either table (zero `FROM`/`INTO`/`UPDATE`/
# `JOIN` matches across the repo). So the design above was written down and never
# wired: the shape was not data anywhere, because the table was never created.
#
# They are removed rather than created. Creating them would add two EMPTY tables
# and make the register's size a measure of abandoned designs. The design note
# above is KEPT, because it records WHY the registers were not merged — that
# reasoning is still true and still load-bearing.

SKILL_MISMATCH_LOG_DDL = """
CREATE TABLE IF NOT EXISTS skill_mismatch_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    mismatch_key      TEXT    NOT NULL UNIQUE,
    skill_key         TEXT    NOT NULL,
    version_label     TEXT,
    image_path        TEXT,
    target_name       TEXT,
    ask_output        TEXT,
    expected          TEXT,
    raw_response      TEXT,
    model             TEXT,
    run_id            TEXT,
    -- NOT NULL REMOVED 2026-09-29 (RING 5, D4). MEASURED: **1701 of 6328 rows**
    -- have `error_reason` IS NULL. A DEFAULT never backfills an existing NULL
    -- and `ALTER TABLE` cannot add NOT NULL, so this rule was unenforceable on
    -- the very table that motivated it. NULL here MEANS "this row does not
    -- record a mismatch reason" — a decision, not an absence. The DEFAULT is
    -- KEPT, so a writer that omits the column still gets 'NA'.
    error_reason      TEXT    DEFAULT 'NA',
    status            TEXT    NOT NULL DEFAULT 'to_review'
                      CHECK (status IN ('to_review', 'reviewed', 'reject', 'promote_to_gold')),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    failure_class TEXT,
    skill_ref INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skill_mismatch_skill
  ON skill_mismatch_log (skill_key, status);
"""

# ---- Skill Task Queue (model handoff relay; runtime polls/dispatches) ----
# Distinct from validate_task_queue (ontology kicker) and legacy task_queue.
# assigned_model is NOT hard-coded to 27B: handoff picks the next model from
# the runtime escalation pool, decided by the worker, not the task creator.
SKILL_TASK_QUEUE_DDL = """
CREATE TABLE IF NOT EXISTS skill_task_queue (
    task_id         TEXT    PRIMARY KEY,
    skill_id        TEXT    NOT NULL,
    task_type       TEXT    NOT NULL,
    payload         TEXT    NOT NULL,
    output_schema   TEXT    NOT NULL,
    assigned_model  TEXT    NOT NULL DEFAULT 'qwen2.5:7b-instruct',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retry       INTEGER NOT NULL DEFAULT 2,
    handoff_count   INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','retry','handoff','success','failed')),
    result          TEXT,
    error_msg       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_skill_task_queue_poll
  ON skill_task_queue (status, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_skill_task_queue_skill
  ON skill_task_queue (skill_id, status);
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
CREATE INDEX IF NOT EXISTS idx_code_registry_status ON code_registry (status, system_key);
CREATE INDEX IF NOT EXISTS idx_code_registry_mod_fn ON code_registry (module_name, function_name);
CREATE INDEX IF NOT EXISTS idx_code_registry_task ON code_registry (task_id);
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

CODE_registry_NEW_COLUMNS = [
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
        # 🔴 WIRED 2026-09-29 (RING 5, D1). MEASURED: this constant was DECLARED
        # and NEVER EXECUTED anywhere in the repo (only `channel_registry.py`
        # executes it, and that module has no entry point a bootstrap reaches),
        # so a FRESH build had NO `channel_registry` at all — 8 columns missing,
        # including `url`.
        #
        # It is a hub/SSOT register (the TOP directory level), so it belongs in
        # this list. `channel_registry.py:92` executing the same constant is the
        # writer running the SSOT's own statement, not a second declaration.
        CHANNEL_REGISTRY_DDL,
    ):
        conn.executescript(ddl)

    # 🔴 THE TWO REGISTERS THAT `db_schema.py` REFERENCES BUT NEVER CREATED.
    # MEASURED 2026-09-28: `db_schema.py` declares FOREIGN KEYs to
    # `worker_registry` (`WORKER_ENVIRONMENT_BINDING_DDL:1502`) and
    # `entity_type_registry` (`VERSION_CLEANUP_DDL:1996`,
    # `code_location_registry_DDL:2238`), but NEITHER table is created anywhere in
    # this module. They are created by their OWN modules:
    #
    #     worker_registry.py:81   WORKER_registry_DDL
    #     entity_registry.py:66   entity_type_registry
    #
    # On the LIVE database they exist (created by those modules' own entry points
    # long ago), so the defect was INVISIBLE — the same "the migrate path can only
    # migrate a database that is already migrated" family as the fault tables.
    # A FRESH database had the FK targets missing.
    #
    # Both calls are best-effort, matching the pattern used below for
    # `ensure_ontology_registry_schema` / `ensure_skill_contract_schema`: a
    # throwaway DB (a proof) may lack `db_table_registry`, and a missing taxonomy
    # row must not stop the register from existing.
    try:
        from worker_registry import ensure_schema as _ensure_worker_registry

        _ensure_worker_registry(conn)
    except Exception:
        pass
    try:
        from entity_registry import (
            ensure_entity_registry_schema as _ensure_entity_registry,
        )

        _ensure_entity_registry(conn)
    except Exception:
        pass

    # 🔴 UNCONDITIONAL, AND THAT IS THE FIX. MEASURED 2026-09-28: this block ran
    # ONLY when `fault_event` ALREADY existed, while `INDEX_DDL` (below) runs
    # UNCONDITIONALLY and indexes `fault_analysis` (`INDEX_DDL:245-246`). So a
    # FRESH database could not be built at all:
    #
    #     FRESH BUILD FAILS: OperationalError no such table: main.fault_analysis
    #
    # The same family as the migrate-path defect this repo keeps recording: the
    # migrate path could only migrate a database that was ALREADY migrated. Every
    # DDL here is `CREATE TABLE IF NOT EXISTS`, so running it unconditionally is
    # safe on an existing database and REQUIRED on a fresh one.
    #
    # `FAULT_EVENT_DDL` is FIRST because the other three carry FOREIGN KEYs to
    # `fault_event` and `INDEX_DDL` indexes it (`INDEX_DDL:269-276`).
    #
    # `WORKERS_DDL` / `TASK_QUEUE_DDL` / `WATCHDOG_LOG_DDL` /
    # `WORKER_HEARTBEAT_DDL` are FIRST OF ALL because `fault_event` carries
    # FOREIGN KEYs to `workers` and `task_queue`, and
    # `seed_task_center_defaults` (called below) INSERTs into `watchdog_log`
    # and `worker_heartbeat`.
    conn.executescript(WORKERS_DDL)
    conn.executescript(TASK_QUEUE_DDL)
    conn.executescript(WATCHDOG_LOG_DDL)
    conn.executescript(WORKER_HEARTBEAT_DDL)
    # The one constant rescued from the DELETED `db_schema_generated_ddl.py`
    # (30 of its 31 tables are created elsewhere; this one was unique).
    conn.executescript(WORKER_JOB_BINDING_DDL)
    conn.executescript(FAULT_EVENT_DDL)
    conn.executescript(FAULT_ANALYSIS_DDL)
    conn.executescript(FAULT_EVENT_FACT_DDL)
    conn.executescript(FAULT_OPTION_PENDING_DDL)
    conn.executescript(FAULT_SSOT_REVISION_DDL)

    fe_added = _add_columns_if_missing(conn, "fault_event", FAULT_EVENT_NEW_COLUMNS)
    fa_added = _add_columns_if_missing(conn, "fault_analysis", FAULT_ANALYSIS_NEW_COLUMNS)

    # ---- THE CHAT -> ENVIRONMENT LINK + THE STEP STATUS (2026-09-25) --------
    # MEASURED: `working_environment` already stores `kind + product + surface`
    # (and `display` IS that formula), and NO chat table linked to it. The only
    # path was a FUZZY JOIN BY NAME (channel -> channel_registry.name ->
    # working_environment.product). Storing the id makes the read a plain FK.
    env_added = _add_columns_if_missing(
        conn, "identity_registry", [("environment_id", "INTEGER")]
    )
    step_added = _add_columns_if_missing(
        conn, "workflow_step", [("step_status", "TEXT")]
    )
    # The alias that bridges VS Code's own model id to the register's row.
    # MEASURED: VS Code reports `deepseek/deepseek-v4.1-flash` while `llm_model`
    # stores `deepseek/deepseek-v4-flash-0731` — NO exact match, and the alias
    # table was EMPTY (0 rows), so the link could never be made.
    alias_added = 0
    if _table_exists(conn, "llm_model_alias"):
        for alias, llm_id, src in LLM_MODEL_ALIAS_SEED:
            cur = conn.execute(
                "INSERT OR IGNORE INTO llm_model_alias "
                "(llm_id, alias, source, is_active) VALUES (?, ?, ?, 1)",
                (int(llm_id), alias, src),
            )
            alias_added += cur.rowcount or 0

    conn.executescript(INDEX_DDL)
    seed_info = seed_ssot_defaults(conn)

    task_info = ensure_task_center_schema(conn)

    conn.commit()

    return {
        "fault_event_columns_added": fe_added,
        "fault_analysis_columns_added": fa_added,
        "identity_registry_columns_added": env_added,
        "workflow_step_columns_added": step_added,
        "llm_model_alias_rows_added": alias_added,
        "seed": seed_info,
        "task_center": task_info,
    }


def ensure_task_center_schema(conn: sqlite3.Connection) -> dict:
    """QC0/T1: task/version center + schema SSOT/QC tables + seed.

    Also CH1 pipeline C: function_invoke_trace + function_scoring (never a gate).
    CH7: code_registry/function_scoring file+line columns (never a gate).

    SCHEMA DRIFT LOCK (added 2026-09-23). Before ANY DDL is applied, each DDL is
    checked against `schema_version` (see `schema_version.py`). A table that
    EXISTS with a DIFFERENT recorded hash is a STALE PROCESS running old code,
    and the check REFUSES so it fails LOUDLY instead of silently re-applying the
    old DDL.

    WHY, MEASURED: a migrated-away table was resurrected by four `pythonw`
    processes that had been running for 1357 minutes. They held a stale in-memory
    `db_schema`, so this exact loop re-ran the OLD DDL list. The resurrected table
    was EMPTY and had NO `db_table_registry` row — the signature of a bare
    `CREATE TABLE IF NOT EXISTS` from stale code.

    The check runs FIRST, so a refusal executes NO DDL at all.
    """
    import schema_version as sv

    conn.execute("PRAGMA foreign_keys = ON;")
    sv.ensure_schema(conn)
    skip = os.environ.get(sv.SKIP_ENV, "").strip().lower() in ("1", "true", "on")

    # THE RENAME RUNS BEFORE ANY DDL. `FN_RESEARCH_DDL` now creates `analyze`, so
    # if the DDL loop ran first on a legacy DB it would create an EMPTY `analyze`
    # beside the populated `fn_research` — and the migration would then have to
    # refuse (two tables, one truth). MEASURED: that is exactly what happened on
    # the first attempt (analyze rows = 0, fn_research still present). Renaming
    # FIRST means the DDL loop's `CREATE TABLE IF NOT EXISTS analyze` is a no-op
    # and the 2 rows survive.
    try:
        fn_research_renamed = _migrate_fn_research_to_analyze(conn)
    except Exception:
        fn_research_renamed = {"ok": False, "renamed": False,
                               "why": "the migration raised"}
    # THE TWO COLUMN RENAMES. Also BEFORE the DDL loop, for the same reason: the
    # DDL now declares `ticket_origin` / `llm_route`, so a legacy DB must be
    # renamed FIRST or the DDL would add a SECOND, empty column beside the
    # populated old one.
    #
    # THE TABLE RENAME RUNS FIRST: `COLUMN_RENAMES` names `llm_route_provider`,
    # which does not exist until `llm_service_provider` is renamed.
    try:
        provider_table_renamed = _migrate_table_renames(conn)
    except Exception:
        provider_table_renamed = {"ok": False, "renames": [],
                                  "why": "the migration raised"}
    try:
        service_columns_renamed = _migrate_service_columns(conn)
    except Exception:
        service_columns_renamed = {"ok": False, "renames": [],
                                   "why": "the migration raised"}

    # THE CHECK PASS, BEFORE ANY DDL. Deliberately a separate loop so the
    # ordering is visible and assertable: if this were fused with the apply loop,
    # a later edit could move one DDL ahead of its own check.
    drift_refused: list[dict] = []
    for ddl in (
        TASK_ACTION_NAME_DDL,
        TDD_TYPE_DDL,
        TASK_TYPE_REGISTRY_DDL,
        INDUSTRY_REGISTRY_DDL,
        CONSULTANT_TEAM_DDL,
        CONSULTANT_MEMBER_DDL,
        CONSULTANT_SKILL_DDL,
        IDENTITY_registry_DDL,
        WORKER_IDENTITY_BINDING_DDL,
        WORKER_ENVIRONMENT_BINDING_DDL,
        WORKING_ENVIRONMENT_DDL,
        COMPUTER_PRESENCE_DDL,
        ASSIGNEE_SELECTION_DDL,
        ROLE_ENVIRONMENT_DDL,
        TOOL_CENTER_DDL,
        PICK_FLOW_DDL,
        SESSION_CONFIRM_LOG_DDL,
        MODE_registry_DDL,
        MODE_RIGHT_registry_DDL,
        WORKER_MODE_DDL,
        VERSION_CLEANUP_DDL,
        TERMINOLOGY_registry_DDL,
        TAXONOMY_LEVEL_REGISTRY_DDL,
        DIMENSION_BINDING_registry_DDL,
        code_location_registry_DDL,
        FAULT_FACTOR_TRACE_DDL,
        VERSION_CENTER_DDL,
        DEV_TASK_DDL,
        DEV_TASK_FIELD_DDL,
        SCHEMA_SSOT_DDL,
        SCHEMA_QC_RUN_DDL,
        TASK_SSOT_DDL,
        FAULT_REPORT_DDL,
        FUNCTION_INVOKE_TRACE_DDL,
        FUNCTION_SCORING_DDL,
        CODE_registry_DDL,
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
        SKILL_PROMPT_STEP_DDL,
        PROMPT_COMPOSITION_DDL,
        COMPONENT_registry_DDL,
        SKILL_registry_DDL,
        SOFT_DELETE_LOG_DDL,
        STUDY_registry_DDL,
        STUDY_TEMPLATE_DDL,
        GENERATOR_REQUIRED_FIELD_DDL,
        WORDING_registry_DDL,
        PROMPT_registry_DDL,
        PROMPT_WORDING_DDL,
        WORKFLOW_registry_DDL,
        WORKFLOW_STEP_DDL,
        PROMPT_COMBO_DDL,
        LLM_MODEL_ALIAS_DDL,
        UNIT_registry_DDL,
        QUESTION_TEMPLATE_FACTOR_DDL,
        SKILL_PROMPT_TEST_RUN_DDL,
        SKILL_PROMPT_INFERENCE_DDL,
        # 🔴 FIVE DDL CONSTANTS THAT WERE DECLARED AND NEVER EXECUTED.
        # MEASURED 2026-09-28: each of these five was mentioned ONLY at its own
        # declaration line — no `executescript()` call, and not in this list. So
        # `ensure_schema()` never created them, and a FRESH database lacked them
        # while the LIVE database had them (created by an older path).
        #
        # MEASURED, and this is why they are WIRED rather than REMOVED: all five
        # are LIVE with real data —
        #
        #     skill_lesson        162 rows
        #     factor_change_log     3 rows
        #     lesson_logic_link     2 rows
        #     skill_mismatch_log 6328 rows
        #     skill_task_queue    288 rows
        #
        # and each declared shape matches its live shape EXACTLY (14/14, 11/11,
        # 7/7, 16/16, 14/14 columns, no drift either way). This is the OPPOSITE of
        # the `REGISTER_KIND_DDL` / `REGISTER_FIELD_DDL` case recorded above,
        # where the tables were EMPTY and the DDL was correctly REMOVED. An empty
        # table is an abandoned design; a table with 6328 rows is a live one whose
        # DDL was never wired.
        SKILL_LESSON_DDL,
        FACTOR_CHANGE_LOG_DDL,
        LESSON_LOGIC_LINK_DDL,
        SKILL_MISMATCH_LOG_DDL,
        SKILL_TASK_QUEUE_DDL,
        # 🔴 SEVEN MORE TABLES THAT WERE LIVE BUT NOT BUILDABLE FROM SCRATCH.
        # MEASURED 2026-09-29: after `ensure_schema()` plus EVERY owner entry
        # point, these seven were still absent from a fresh build, while the LIVE
        # database had them with real data (4 / 3 / 155 / 2159 / 5 / 2 / 1 rows)
        # and their declared shapes matched live EXACTLY. Each was created ONLY
        # by an entry point that a bootstrap cannot reach — a GENERATED module
        # nothing imports, a no-argument function that opens the LIVE db itself,
        # or a module with no `ensure_*` function at all.
        AGENT_PROVIDER_DDL,
        ALERT_HISTORY_DDL,
        DOUBAO_SEND_LOG_DDL,
        PURGE_LEDGER_DDL,
        SCHEMA_MIGRATION_LOG_DDL,
        SKILL_TASK_ID_registry_DDL,
        TERMINOLOGY_NAME_UNIFY_DELETED_DDL,
        RATE_LIMIT_DDL,
        TASK_CENTER_INDEX_DDL,
        SETTINGS_DDL,
        CHAT_REPLY_LOG_DDL,
        TASK_SOURCE_LOG_DDL,
        CHAT_ID_DDL,
        CHAT_DDL,
        CHAT_MAIN_DDL,
        CHAT_MAIN_HASH_DDL,
        CHAT_IDENTITY_LOG_DDL,
        derived_column_registry_DDL,
        VSCODE_ENV_DIMENSION_DDL,
        VSCODE_ENV_LOG_DDL,
        # MERGED 2026-09-27 (plan REGISTER.NAMING.AND.PHASE.MERGE):
        # TERMINOLOGY_SWEEP_PHASE_DDL and REGISTER_FILL_PHASE_DDL are SUPERSEDED
        # by PHASE_registry_DDL. Their strings are kept (importers name them) but
        # they are NOT executed here — executing them would RESURRECT the two
        # tables the merge dropped, which is the same defect the
        # TICKET_MODULE_MAP_DDL note below records.
        PHASE_registry_DDL,
        TERMINOLOGY_SWEEP_RUN_DDL,
        CHAT_CENTER_MESSAGE_DDL,
        TICKET_CENTER_DDL,
        TICKET_DDL,
        TICKET_EVENT_DDL,
        TICKET_CHAT_LINK_DDL,
        # S5 (plan WATCHDOG.DRAFT.CONSUMER): the append-only audit trail for
        # `chat_main.status`. Wired here so a FRESH build has it — a table that
        # only a proof creates is a table a bootstrap cannot reach.
        CONVERSATION_STATUS_EVENT_DDL,
        # S6 (plan CHAT.PIPELINE.S6): the 9-stage conversation pipeline. Wired
        # here for the same reason — a fresh build must have it.
        PIPELINE_RUN_DDL,
        # S8 (plan CHAT.PIPELINE.S8): the task<->run link. Wired here for the
        # same reason — a fresh build must have it.
        PIPELINE_RUN_TASK_DDL,
        # TICKET_MODULE_MAP_DDL REMOVED 2026-09-23. `ticket_module_map` was the
        # MODULE special case of "a ticket and its subject" and was the ONLY
        # mapping table, so a second kind (chat, task, entity) had nowhere to go.
        # It is folded into `ticket_subject` with `subject_kind='module'` (see
        # `_migrate_ticket_module_map.py`). Leaving the DDL in this list would
        # RESURRECT the table on the next `ensure_task_center_schema()` and give
        # the repo two conventions again — measured: the proof caught exactly
        # that, because the table came back after the migration dropped it.
        chat_registry_DDL,
        USER_ENVIRONMENT_DDL,
        QC_RUN_DDL,
        QC_GATE_REGISTRY_DDL,
        APP_DDL,
        USER_ASSET_DDL,
        SOURCE_DDL,
        FLOW_SETTING_DDL,
        PLAN_SESSIONS_DDL,
        PLAN_SESSION_LOG_DDL,
        ROLES_DDL,
        USERS_DDL,
        SKILLS_DDL,
        SKILL_VERSIONS_DDL,
        LLM_TASKS_DDL,
        BROWSER_TASK_STEPS_DDL,
        LLM_SERVICE_DDL,
        LLM_SERVICE_TYPE_DDL,
        LLM_SERVICE_PROVIDER_DDL,
        LLM_MODEL_DDL,
        CAPABILITY_KIND_DDL,
        CAPABILITY_TOOL_DDL,
        # REGISTER_FILL_PHASE_DDL REMOVED 2026-09-27 — superseded by
        # PHASE_registry_DDL (see the note above).
        REGISTER_FILL_RUN_DDL,
        REGISTER_FILL_CONTROL_DDL,
    ):
        check = sv.check(conn, sv._table_key_of(ddl), ddl, skip=skip)
        if not check.get("ok"):
            # A DDL with NO CREATE TABLE (a pure index/seed block) has no key to
            # version, so it is APPLIED, not refused — refusing it would silently
            # drop its indexes. Only a named table can drift.
            if check.get("reason", "").startswith("table_key is required"):
                conn.executescript(ddl)
                continue
            # A STALE PROCESS. Record it and DO NOT apply this DDL — the refusal
            # must not execute the very statement it refused.
            drift_refused.append(check)
            continue
        if check.get("action") == "record_only":
            # The table predates the lock (a legacy DB). APPLY, exactly as
            # before this lock existed — these DDL blocks are
            # `CREATE ... IF NOT EXISTS`, so applying also creates any index
            # added alongside the table, and skipping would silently drop them.
            # Then RECORD, so the next mismatch is detectable.
            conn.executescript(ddl)
            sv.record_applied(conn, check["table_key"], ddl)
            continue
        conn.executescript(ddl)
        if check.get("action") in ("first_apply", "table_absent"):
            sv.record_applied(conn, check["table_key"], ddl)
    # RBAC seed (idempotent)
    try:
        seed_rbac_defaults(conn)
    except Exception:
        pass
    # llm_route_provider additive migration (the TRANSPORT column).
    #
    # MEASURED 2026-09-28: without it, `llm.text`'s pool is a list of NAMES with
    # no way to tell which ones a caller can actually reach — and
    # `llm_100_run_harness` picked `convaiinnovations/laya` (a Python LIBRARY)
    # and POSTed it to Ollama, which answered `ollama_http_404`.
    try:
        _add_columns_if_missing(
            conn, "llm_route_provider", LLM_ROUTE_PROVIDER_NEW_COLUMNS)
    except Exception:
        pass
    # 🔴 THE `rate_limit.policy` MIGRATION (2026-09-29). REQUIRED, not optional.
    #
    # `CREATE TABLE IF NOT EXISTS` does NOT add a column to an existing table, so
    # the `policy` column added to `RATE_LIMIT_DDL` above would exist ONLY on a
    # FRESH database — the exact half-migration this module already records twice
    # (`llm_model`, `llm_service.needs_flag`). The live `rate_limit` already has
    # rows written before policies existed; `DEFAULT 'default'` labels them
    # honestly instead of leaving them NULL.
    #
    # Best-effort, matching the pattern above: a throwaway DB (a proof) may not
    # have the table yet, and a missing column must not stop the schema build.
    try:
        added = _add_columns_if_missing(
            conn, "rate_limit", RATE_LIMIT_ADDITIVE_COLUMNS)
        # 🔴 AND THE INDEX IS CREATED HERE, AFTER THE COLUMN IT NAMES.
        # MEASURED: with the index inside `RATE_LIMIT_DDL` the build failed on an
        # existing database with `no such column: policy`, because
        # `CREATE TABLE IF NOT EXISTS` does not add a column. The index MUST come
        # after the migration, never with the table.
        conn.executescript(RATE_LIMIT_POLICY_INDEX_DDL)
    except Exception:
        pass
    # plan_sessions additive migration (legacy DBs created before chat_hash/version)
    try:
        _add_columns_if_missing(
            conn,
            "plan_sessions",
            [
                ("chat_hash", "TEXT"),
                ("version", "INTEGER NOT NULL DEFAULT 0"),
            ],
        )
    except Exception:
        pass
    # chat_reply_log additive migration (send-time / elapsed-ms record)
    try:
        _add_columns_if_missing(
            conn,
            "chat_reply_log",
            [
                ("sent_at", "TIMESTAMP"),
                ("elapsed_ms", "INTEGER"),
            ],
        )
    except Exception:
        pass
    # prompt_registry additive migration (2026-09-22): the five first-principle
    # answers + the oracle reference. A prompt is a HYPOTHESIS WITH A MEASURED
    # UNIT, so the unit and the threshold must be STORED, not asserted.
    #
    # NOTE: `is_active`'s DEFAULT cannot be changed by ALTER TABLE in SQLite, so
    # the DDL above carries `DEFAULT 0` for a NEW table and this migration only
    # adds the columns. An existing DB keeps its old default; the proof asserts
    # the DDL, and `register_prompt` writes `is_active=0` EXPLICITLY so the
    # behaviour does not depend on the default.
    try:
        _add_columns_if_missing(
            conn,
            "prompt_registry",
            [
                ("failure_mode", "TEXT NOT NULL DEFAULT 'NA'"),
                ("observable", "TEXT NOT NULL DEFAULT 'NA'"),
                ("unit", "TEXT NOT NULL DEFAULT 'NA'"),
                ("threshold", "TEXT NOT NULL DEFAULT 'NA'"),
                ("independence", "TEXT NOT NULL DEFAULT 'NA'"),
                ("oracle_ref", "TEXT NOT NULL DEFAULT 'NA'"),
            ],
        )
    except Exception:
        pass
    # plan_sessions composite-PK migration: (session_id, chat_id) pair.
    # Rebuilds the table when it still has the legacy single session_id PK.
    try:
        _migrate_plan_sessions_pair(conn)
    except Exception:
        pass
    # user_environment.user_id: TEXT alias -> INTEGER users(user_id) FK.
    # Must run AFTER users exists (USERS_DDL above) and AFTER the table exists.
    try:
        _migrate_user_environment_user_id_int(conn)
    except Exception:
        pass
    # app catalog seed (idempotent) — OpenClaw Companion + common desktop apps.
    try:
        seed_app_defaults(conn)
    except Exception:
        pass
    # source catalog seed (idempotent) — Chat Center Setting "from" dropdown.
    try:
        seed_source_defaults(conn)
    except Exception:
        pass
    # flow table seed (idempotent) — Chat Center flow steps.
    try:
        seed_flow_setting_defaults(conn)
    except Exception:
        pass
    # Lifecycle + prompt trace tables (append-only audit; never a gate)
    try:
        from src.task_center.lifecycle_log import ensure_lifecycle_tables

        ensure_lifecycle_tables(conn)
    except Exception:
        pass
    # Split ontology registry tables (read-side taxonomy SSOT for validate_new_task)
    ontology_registry_info: dict = {}
    try:
        from src.task_center.ontology_store import (
            ensure_ontology_registry_schema,
            seed_ontology_registry_defaults,
        )

        ontology_registry_info = ensure_ontology_registry_schema(conn)
        ontology_registry_info["seed"] = seed_ontology_registry_defaults(conn)
    except Exception as e:
        ontology_registry_info = {"ok": False, "error": str(e)}
    # Skill Contract SSOT (6-column template + Field Register + streak).
    # Additive only; `skill_template` (task-center catalog) is untouched.
    skill_contract_info: dict = {}
    try:
        from skill_contract_store import ensure_skill_contract_schema

        skill_contract_info = ensure_skill_contract_schema(conn)
    except Exception as e:
        skill_contract_info = {"ok": False, "error": str(e)}
    pair_qc_run_migrated = _migrate_pair_qc_run_schema(conn)
    # `wordng_registry` kept a legacy single-column UNIQUE that the DDL no longer
    # declares, so a setting could never span two dimensions. See the function.
    wording_unique = _rebuild_wording_registry_unique(conn)
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
                    "CREATE INDEX IF NOT EXISTS idx_pair_qc_run_registry "
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
        conn, "code_registry", CODE_registry_NEW_COLUMNS
    )
    dev_task_cols = _add_columns_if_missing(
        conn, "dev_task", DEV_TASK_NEW_COLUMNS
    )
    _add_columns_if_missing(
        conn, "identity_registry", IDENTITY_registry_NEW_COLUMNS
    )
    _add_columns_if_missing(
        conn, "chat_main", CHAT_MAIN_NEW_COLUMNS
    )
    # THE CHAT'S OWN task_id / entity_id (2026-09-28). THE HUMAN:
    #     "ｗｈｅｒ　ｉｓ　ｔａｓｋ　ＩＤ　ａｎｄ　ｅｎｔｉｔｙ　ＩＤtoo?"
    # `CREATE TABLE IF NOT EXISTS` will NOT add a column to an existing `chat`,
    # so the additive path is required as well as the DDL above.
    _add_columns_if_missing(
        conn, "chat", CHAT_NEW_COLUMNS
    )
    # 🔴 THE RING 5 ADDITIVE COLUMNS (2026-09-29). MEASURED: LIVE carried these
    # columns and NO declaration mentioned them, so a FRESH build produced a
    # table that was a strict SUBSET of the live one while the shape comparison
    # called LIVE the drifted side. Each is populated and read; see the notes on
    # each constant. WIRED HERE, never in the DDL alone.
    _add_columns_if_missing(conn, "skills", SKILLS_CATALOG_ADDITIVE_COLUMNS)
    _add_columns_if_missing(conn, "prompt_combo", PROMPT_COMBO_ADDITIVE_COLUMNS)
    _add_columns_if_missing(conn, "workflow_step", WORKFLOW_STEP_FLOW_COLUMNS)
    _add_columns_if_missing(conn, "task_lifecycle_log", (("skill_ref", "INTEGER"),))
    _add_columns_if_missing(conn, "task_prompt_trace", (("skill_ref", "INTEGER"),))
    # `qc_gate_registry.mode` — the column whose ABSENCE was the D1 drift. The
    # DDL now declares it, and this repairs a table created before it did. Same
    # reason `worked_environment.icon_url` is done this way, one line up.
    _add_columns_if_missing(
        conn, "qc_gate_registry",
        (("mode", "TEXT NOT NULL DEFAULT 'declare'"),))
    # THE ENVIRONMENT LOGO (2026-09-25). `CREATE TABLE IF NOT EXISTS` will NOT
    # add a column to an existing `working_environment`, so the additive path is
    # required as well as the DDL above.
    _add_columns_if_missing(
        conn, "working_environment", [("icon_url", "TEXT")]
    )
    # 🔴 THE `worker_job_binding` REBUILD (RING 5, D3/D5, 2026-09-29).
    # MEASURED: LIVE carries a PK, 8 NOT NULLs and `UNIQUE (worker_id, job_id)`;
    # the declaration was 12 bare columns. The table has 0 rows, so a REBUILD is
    # safe and is the only way SQLite can add a PK. Guarded on the table's OWN
    # shape, so it runs at most once and never on a correct database.
    try:
        _rebuild_worker_job_binding_if_needed(conn)
    except Exception:
        pass
    # 🔴 THE `llm_route_provider.transport` CHECK WIDENING (2026-09-29).
    # MEASURED: the LIVE table was built by the ADDITIVE migration, so it carries
    # NO CHECK; the DECLARATION declares `CHECK (transport IN ('http','python'))`.
    # A FRESH build would therefore REFUSE `cli` while the live table accepts it.
    # SQLite cannot widen a CHECK with ALTER, so the table is rebuilt from the
    # SSOT. Guarded on the table's OWN SQL, so it runs at most once.
    try:
        _rebuild_llm_route_provider_transport_check(conn)
    except Exception:
        pass
    # The DERIVED-vs-IDENTITY declaration. `CREATE TABLE IF NOT EXISTS` covers the
    # table; the SEED is what names each column, so a fresh DB and an existing one
    # end up identical.
    conn.executescript(derived_column_registry_DDL)
    for tbl, col, kind, src in DERIVED_COLUMN_SEED:
        conn.execute(
            "INSERT OR IGNORE INTO derived_column_registry "
            "(table_name, column_name, kind, derived_from, cite_ref) "
            "VALUES (?,?,?,?,?)",
            (tbl, col, kind, src,
             "db_schema.py:DERIVED_COLUMN_SEED; skill_library_api."
             "sha256_from_session / chat_pair_hash"))
    # `INSERT OR IGNORE` never updates an existing row, so a row written with the
    # OLD `derived_from` text survives every re-run of the seed. MEASURED
    # (2026-09-26): the live `chat_main.chat_hash` row still said
    # `id + '|' + session_id`. This makes the LIVE row agree with the seed.
    _correct_chat_hash_derived_from(conn)
    # THE CITE_REF REPAIR, run at SCHEMA TIME (2026-09-26).
    #
    # MEASURED DEFECT: `dimension_binding_registry` had **12 rows** whose
    # `cite_ref` was `subject_kind_registry.py:SUBJECT_KIND_SEED` — a SYMBOL, not
    # a `path:line`, so `citation_discipline.is_citation()` REFUSES it and the
    # reference is UNCHECKABLE. The 12 rows are exactly the subject kinds
    # `field` and `table`, which are in NONE of the 8 `SUBJECT_KINDS` the seed
    # declares — another path inserted them with the wrong reference.
    #
    # `repair_cite_refs` already derives the right value, but it was only
    # reachable through hidden CLI flags (`--repair-cite`), so the LIVE data was
    # never repaired. A repair that only runs when a human remembers a flag is a
    # repair that does not happen — so it runs here, the way
    # `_correct_chat_hash_derived_from` does above. Idempotent: a second call
    # changes 0 rows.
    #
    # NOTE: `out` is built FURTHER DOWN, so this result goes into `_mig`, which is
    # merged into `out` when `out` exists. MEASURED: writing to `out` here raised
    # `UnboundLocalError` and the repair never ran.
    _mig: dict = locals().get("_mig") or {}
    try:
        import dimension_binding_registry as _dbr
        _mig["binding_cite_ref"] = _dbr.repair_cite_refs(conn)
        # THE `example` REPAIR TOO. MEASURED (2026-09-26): the same 12 rows
        # (`field`, `table`) carried `db_field_registry.db_field_id` /
        # `db_table_registry.db_table_id` as their `example` — a bare
        # `table.column` string, which `citation_discipline.is_citation()`
        # REFUSES (it is not `path:line` and not `register:<table>:<pk>`). So the
        # example was uncheckable for the same reason the cite_ref was.
        # `repair_examples` maps them to `register:db_field_registry:1` (a row
        # that EXISTS). Idempotent: a second call changes 0.
        _mig["binding_example"] = _dbr.repair_examples(conn)
    except Exception as e:
        _mig["binding_cite_ref_error"] = f"{type(e).__name__}: {e}"
    _add_columns_if_missing(
        conn, "chat_identity_log", CHAT_IDENTITY_LOG_NEW_COLUMNS
    )
    _add_columns_if_missing(
        conn,
        "chat_center_message",
        CHAT_CENTER_MESSAGE_NEW_COLUMNS + [("sha256", "TEXT")],
    )
    # chat identity: TEXT sha256 -> INTEGER chat_main.id (DB-driven id)
    try:
        chat_identity_migrated = _migrate_chat_identity_to_int(conn)
        chat_identity_affinity = _rebuild_chat_id_int_affinity(conn)
    except Exception as e:
        chat_identity_migrated = {"error": f"{type(e).__name__}: {e}"}
        chat_identity_affinity = {"error": f"{type(e).__name__}: {e}"}
    # read-only correct pair key (never overwrites chat_hash)
    try:
        chat_hash_backfill = _backfill_chat_hash_recomputed(conn)
    except Exception as e:
        chat_hash_backfill = {"error": f"{type(e).__name__}: {e}"}
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
            for r in conn.execute("PRAGMA table_info(code_registry)").fetchall()
        }
        if {"file_path", "line_start", "line_end"}.issubset(cr_cols):
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_code_registry_file "
                "ON code_registry (file_path, line_start, line_end)"
            )
    except sqlite3.Error:
        pass
    out = seed_task_center_defaults(conn)
    # Carry the migration results collected BEFORE `out` existed (e.g. the
    # `dimension_binding_registry` cite_ref repair above).
    if _mig:
        out.update(_mig)
    out["openclaw_capability"] = seed_openclaw_capability_ssot(conn)
    # The LLM model registry + the capability-kind vocabulary. Both are SEEDS:
    # the read path is the table, and an existing row is never overwritten.
    try:
        out["llm_model"] = seed_llm_model_defaults(conn)
    except Exception as e:
        out["llm_model_error"] = f"{type(e).__name__}: {e}"
    try:
        out["capability_kind"] = seed_capability_kind_defaults(conn)
    except Exception as e:
        out["capability_kind_error"] = f"{type(e).__name__}: {e}"
    out["fault_report"] = True
    out["code_health"] = {
        "function_invoke_trace": True,
        "function_scoring": True,
        "gate": "never",
        "why": ["W2", "W9", "W10", "W11"],
        "source_location": True,
        "function_scoring_columns_added": scoring_cols,
        "code_registry_columns_added": register_cols,
        "dev_task_columns_added": dev_task_cols,
    }
    out["managed_coding"] = {
        "code_registry": True,
        "fn_request": True,
        "analyze": True,
        "field_tdd_rule": True,
        "onto_concept": True,
        "onto_link": True,
        "onto_binding": True,
        "onto_monitor_rollup": True,
        "function_scoring_columns_added": scoring_cols,
        "code_registry_columns_added": register_cols,
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
    # pattern_template / pattern_instance — the EXPERIENCE a worker copies from
    # (lazy import, same shape as the two above). Created here so the tables
    # exist for every process that runs the schema, not only for a CLI call.
    try:
        import pattern_template as _pt

        _pt.ensure_schema(conn)
        out["pattern_template"] = {
            "templates": len(_pt.list_templates(conn)),
            "ddl": "PATTERN_TEMPLATE_DDL + PATTERN_INSTANCE_DDL",
        }
    except Exception as e:
        out["pattern_template_error"] = f"{type(e).__name__}: {e}"
    out["ontology_registry"] = ontology_registry_info
    out["chat_identity"] = chat_identity_migrated
    out["chat_identity_affinity"] = chat_identity_affinity
    out["chat_hash_backfill"] = chat_hash_backfill
    out["skill_contract"] = skill_contract_info
    # THE DRIFT REPORT. A non-empty list means a process holding OLD code tried
    # to apply a DDL that does not match what is recorded — a STALE PROCESS. It
    # is surfaced in the return value, not only logged, so a caller can act.
    out["schema_drift_refused"] = drift_refused
    out["schema_drift_ok"] = not drift_refused
    return out


CHAT_REPLY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS chat_reply_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    task_id         TEXT,
    chat_id         TEXT,
    model           TEXT,
    reply_text      TEXT,
    source          TEXT    NOT NULL DEFAULT 'agent',
    sent_at         TIMESTAMP,
    elapsed_ms      INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_reply_log_session
    ON chat_reply_log (session_id, created_at DESC);
"""

TASK_SOURCE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS task_source_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT,
    who         TEXT,
    "where"     TEXT,
    chat_id     TEXT,
    session_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_source_log_task
    ON task_source_log (task_id);
"""

# ---- RBAC: roles / users ----
ROLES_DDL = """
CREATE TABLE IF NOT EXISTS roles (
    role_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    role_name   TEXT    NOT NULL UNIQUE,
    permissions TEXT    NOT NULL DEFAULT '[]',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    api_token   TEXT    NOT NULL UNIQUE,
    role_id     INTEGER NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES roles(role_id)
);
"""

# ---- Skill Library: skills / skill_versions ----
SKILLS_DDL = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id    TEXT    PRIMARY KEY,
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER,
    -- 🔴 THE CATALOG COLUMNS, ADDED 2026-09-29 (RING 5, D2).
    -- MEASURED: LIVE `skills` carries 9 columns, a FRESH build 5. The four below
    -- are POPULATED 40/40 by an ad-hoc ALTER that was never given a declarer,
    -- and they are READ — `catalog_name` has 6 DISTINCT values and 5 readers
    -- (`coord_store`, `main_api`, `mouse_spot_helper`, `skill_scanner`,
    -- `skill_library_api`). `subcatalog_name` likewise.
    --
    -- WHY A 1-VALUE COLUMN IS STILL DECLARED: `catalog_id` and `subcatalog_id`
    -- hold ONE distinct value each. That is a POPULATION fact, not a schema one:
    -- an undeclared column is invisible to every reader that is not a live query
    -- today, and the writer still names it. Declared with the same nullability
    -- LIVE has (measured).
    catalog_id       INTEGER,
    catalog_name     TEXT,
    subcatalog_id    INTEGER,
    subcatalog_name  TEXT
);
"""

# THE ADDITIVE LISTS FOR RING 5 (D2), 2026-09-29. Each covers LIVE columns that
# no declaration ever mentioned, measured POPULATED and READ. `CREATE TABLE IF
# NOT EXISTS` does NOT add a column to an existing table, so the DDL above is not
# the migration — these are, and they run right after it.
SKILLS_CATALOG_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("catalog_id", "INTEGER"),
    ("catalog_name", "TEXT"),
    ("subcatalog_id", "INTEGER"),
    ("subcatalog_name", "TEXT"),
)
PROMPT_COMBO_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("explain", "TEXT"),
)

SKILL_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS skill_versions (
    sv_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_id        TEXT    NOT NULL,
    version         TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'draft',
    bundle_yaml     TEXT    NOT NULL DEFAULT '',
    bundle_schema   TEXT    NOT NULL DEFAULT '',
    prompt_ask      TEXT    NOT NULL DEFAULT '',
    prompt_confirm  TEXT    NOT NULL DEFAULT '',
    prompt_plan     TEXT    NOT NULL DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER,
    UNIQUE (skill_id, version),
    FOREIGN KEY (skill_id) REFERENCES skills(skill_id)
);
"""

# ---- interactive plan sessions ----
# ---- chat_main (DB-driven chat identity: INTEGER auto-increment id) ----
# id      = auto-increment PK -> the ONLY chat_id referenced by FK elsewhere.
# sha256  = sha256(session_id) -> content hash, NOT an id (display + dedupe only).
# chat_hash = sha256("chat_id|session_id") -> the PAIR key (plan_sessions).
#
# `worker_id` (added 2026-09-24) COMPLETES THE 2-FACTOR. The user:
#     "it is 2 factor / source / provider : Vscode / chat or 豆包 ／ 工作伙伴"
# MEASURED: the four columns above claim to identify one row, and THREE of them
# are FUNCTIONS of one input — `id` is the real id, `session_id` the INPUT, and
# both hashes are computed FROM it (`skill_library_api._chat_main_ensure`
# RECOMPUTES them on every write). So `chat_main` stored HALF of what the user
# described: the session, but not the worker. `identity_registry` already carries
# both plus `workflow_id`; this column makes the chat row able to reach it.
#
# NULLABLE on purpose: a legacy row predates the worker, and a fabricated worker
# would be the defect rather than the fix.
#
# `worker_id` carries NO native FK, and that is a DECLARED pattern in this repo:
# the factor `lazy_fk_ontology_relation` states "No native DB FK constraint.
# Referential integrity is handled at the business layer" (measured: 95 of 178
# tables carry no FK). A `CREATE TABLE IF NOT EXISTS` cannot add an FK to an
# EXISTING table anyway, so the integrity is enforced where the identity is
# CHECKED — `identity_middleware.check()` resolves the worker and refuses a
# phantom, which is the same guarantee the FK would give.
CHAT_MAIN_DDL = """
CREATE TABLE IF NOT EXISTS chat_main (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    chat_hash   TEXT,
    worker_id   INTEGER,
    ide         TEXT,
    llm         TEXT,
    source      TEXT    NOT NULL DEFAULT 'api',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    chat_id INTEGER,
    chat_hash_recomputed TEXT,
    -- THE CONVERSATION-LEVEL WORKFLOW STATUS (added 2026-09-29, plan
    -- WATCHDOG.DRAFT.CONSUMER, step S3). Declared HERE as well as in
    -- `CHAT_MAIN_NEW_COLUMNS`, because the two sites serve two databases: this
    -- DDL creates the column on a NEW database, and the additive list adds it to
    -- an EXISTING one. MEASURED: `logic_generator.declared_shape` reads the DDL
    -- text, so a column present ONLY in the additive list would read as
    -- "live but undeclared" — a FALSE drift on a correct table.
    status TEXT,
    UNIQUE (session_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_main_sha256
    ON chat_main (sha256);
CREATE INDEX IF NOT EXISTS idx_chat_main_session
    ON chat_main (session_id);
CREATE INDEX IF NOT EXISTS idx_chat_main_worker
    ON chat_main (worker_id);
"""

# Additive columns: `CREATE TABLE IF NOT EXISTS` never adds one to an existing
# table (the defect that left `llm_model` without `model_id`).
CHAT_MAIN_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("worker_id", "INTEGER"),
    # THE CHAT PARENT (added 2026-09-25). THE HUMAN:
    #     "for same chat / can be conversaction / 1 -> 2 = chat ID -> PAIR ...
    #      but for 1 to 6 or 1 to N, is another condition, i should have
    #      conversaction table ... id | conversaction ID | chat_id |"
    #
    # MEASURED, and the human's column list IS this table plus one column:
    #     chat_main: id | session_id | sha256 | chat_hash | worker_id | ...
    # and `session_id` IS the conversation id (one `chatSessions/<id>.jsonl`,
    # the registered term `vscode_conversation`). So the human's shape is
    # `chat_main` + `chat_id` — an ADDITIVE change, not a rebuild.
    #
    # NULLABLE on purpose: a conversation whose chat is not yet known is a real
    # state, and a fabricated parent would be the same defect as a fabricated
    # `chat_id` on `identity_registry`. The backfill fills what it can and
    # REPORTS the rest.
    ("chat_id", "INTEGER"),
    # THE CORRECT PAIR KEY, READ-ONLY (added 2026-09-25).
    #
    # MEASURED: 53 of 58 non-NULL `chat_hash` values were produced by
    # `chat_pair_hash(id, session_id)` — the OLD formula, from when `chat_id` did
    # not exist. They were never recomputed after the backfill, so only **5 of 58**
    # match `chat_pair_hash(chat_id, session_id)`.
    #
    # THE DESIGN IS THE SAME AS THE OTHER TWO TABLES: a SEPARATE, read-only column
    # holds the correct value, and `chat_hash` is NEVER touched, so audit history
    # stays intact (append-only rule) — `db_schema.py:_backfill_chat_hash_recomputed`.
    #
    # WHY IT MATTERS: the stale values made a report claim "the pair key went
    # 1:1 -> 1:N" while EVERY `chat_hash` still mapped to exactly ONE `chat_id`.
    # A reader that keys on the stale column gets a wrong row and no warning.
    ("chat_hash_recomputed", "TEXT"),
    # THE CONVERSATION-LEVEL WORKFLOW STATUS (added 2026-09-29,
    # plan WATCHDOG.DRAFT.CONSUMER, step S3).
    #
    # THE HUMAN (2026-09-26): "vscode chat before will be register for chat id,
    # but design is chnaged, now should be conversaction ID / chat ID / sha256 /
    # role = ask / status = draft". MEASURED: `role` and `status` are TURN
    # properties (`chat_center_message`), but the CONVERSATION has no status of
    # its own — so "show me every conversation still in draft" was unanswerable
    # without scanning turns. This column answers it directly.
    #
    # ADDITIVE, NULLABLE, NO DEFAULT, NO CHECK — and each of those is MEASURED,
    # not a style choice:
    #   * ADDITIVE via this tuple, so the repo's ONE migration
    #     (`_add_columns_if_missing`, applied to `chat_main` at `db_schema.py`
    #     init) carries it. A hand-written `ALTER` beside that mechanism would be
    #     a SECOND migration path that can drift from the first.
    #   * NULLABLE because "no status recorded" is a real state and a fabricated
    #     `draft` would be indistinguishable from a reviewed one.
    #   * NO DEFAULT because the backfill SETS the value explicitly
    #     (`UPDATE ... WHERE status IS NULL`), so a DEFAULT would only mask a
    #     writer that forgot to pass one.
    #   * NO CHECK because SQLite cannot add a CHECK to an existing table without
    #     a REBUILD, and a rebuild of the LIVE `chat_main` is a bigger risk than
    #     the value it buys. The vocabulary is validated at the WRITE SITE
    #     (`skill_library_api.WORKFLOW_STATUSES`), the same rule
    #     `chat_center_message.status` already follows.
    ("status", "TEXT"),
)

# ---- chat — THE CHAT LEVEL (the PARENT of a conversation) ----
#
# WHY THIS TABLE EXISTS (the human, 2026-09-25):
#     "same chat can have many conversaction, does i have something wrong in
#      the past"
#
# MEASURED, and the human is right: `chat_main` declares `UNIQUE (session_id)`,
# so ONE session = ONE row, and a chat holding MANY conversations is NOT
# representable. `chat_main` is the CONVERSATION row wearing the CHAT name.
#
# THE FOUR LEVELS, MEASURED:
#     CHAT          the SUBJECT        -> THIS TABLE (was MISSING)
#     CONVERSATION  one session file   -> chat_main (UNIQUE session_id)
#     TURN          one message        -> chat_center_message (many per chat)
#     IDENTITY      who is present     -> identity_registry
#
# WHY THE PAIR KEY NEEDS THIS TABLE. `derived_column_registry` declares
# `chat_main.chat_hash` kind='pair_key' from `chat_id + '|' + session_id`.
# MEASURED (2026-09-26): the LIVE values are `sha256(chat_main.id | session_id)`
# on 58 of 58 rows — the writer used the CONVERSATION id, not the CHAT id. Since
# `chat_main.id` is the PRIMARY KEY, that hash re-encodes the row id and pairs
# NOTHING. It becomes a real pair key only when ONE chat holds MANY
# conversations, which is what this table makes expressible.
#
# `chat_key` IS THE NATURAL KEY (survives a rebuild); `chat_id` is the
# AUTOINCREMENT identity. There is NO entity id: this is a CONTAINER, and the
# entity id scheme names THINGS (`T-5-1-1`), not containers.
CHAT_DDL = """
CREATE TABLE IF NOT EXISTS chat (
    chat_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_key    TEXT    NOT NULL UNIQUE,
    title       TEXT,
    opened_by   TEXT,
    source      TEXT    NOT NULL DEFAULT 'api',
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_chat_active
    ON chat (is_active, chat_key);
"""

# Additive columns for `chat` (added 2026-09-28). THE HUMAN:
#     "ｗｈｅｒ　ｉｓ　ｔａｓｋ　ＩＤ　ａｎｄ　ｅｎｔｉｔｙ　ＩＤtoo?"
#
# MEASURED, and the human is right: `chat` had 8 columns and NEITHER. The task id
# existed only as TEXT inside `chat.title` / `chat_key` (`chat:report:<task_id>`),
# so it was readable only by parsing a string, and the entity id existed nowhere.
#
# `CREATE TABLE IF NOT EXISTS` never adds a column to an existing table, so the
# additive path is required as well as the DDL above — the same defect that left
# `llm_model` without `model_id`.
#
# BOTH ARE NULLABLE, and that is the human's own rule: "each chat need to have
# title (requied) + content (requied) + entity ID (optional)". A chat that names
# no entity is a VALID chat, so a fabricated `entity_id` would be the defect.
CHAT_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("task_id", "TEXT"),
    ("entity_id", "TEXT"),
)

# ---- derived_column_registry — WHAT IS AN IDENTITY AND WHAT IS A FUNCTION ----
#
# WHY A TABLE (user, 2026-09-24):
#     "evidence can be proofed is the only wat to have the work output standardize"
#
# MEASURED DEFECT this exists to stop: `chat_main` has FOUR columns a reader would
# take for identities, and only ONE is one.
#     id        62/62 distinct  INTEGER PK       <- IDENTITY
#     session_id 62/62 UNIQUE   TEXT NOT NULL    <- the INPUT (not derived)
#     sha256    = sha256(session_id)             <- a FUNCTION
#     chat_hash = sha256(id + "|" + session_id)  <- a FUNCTION
# A function is RECOMPUTED (`skill_library_api` does exactly that on every write),
# so treating it as an identity is how TWO ids appear to name ONE row. Recording
# the distinction HERE makes it machine-readable instead of a code comment.
#
# The columns are NOT dropped: `chat_hash` is append-only AUDIT (the user's own
# rule is soft delete only). MEASURED (2026-09-26): it is no longer SELECTed by
# any product reader — the readers were retired in plan_REMOVE.STALE.CHAT.HASH.
derived_column_registry_DDL = """
CREATE TABLE IF NOT EXISTS derived_column_registry (
    derived_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name     TEXT    NOT NULL,
    column_name    TEXT    NOT NULL,
    kind           TEXT    NOT NULL
                   CHECK (kind IN ('function', 'pair_key', 'content_hash')),
    derived_from   TEXT    NOT NULL,
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (table_name, column_name)
);
CREATE INDEX IF NOT EXISTS idx_derived_column_table
  ON derived_column_registry (table_name, is_active);
"""

# (table, column, kind, derived_from) — each row is a MEASURED fact, and the
# `derived_from` NAMES the input, so a reader can verify the function.
DERIVED_COLUMN_SEED: tuple[tuple[str, str, str, str], ...] = (
    ("chat_main", "sha256", "function", "session_id"),
    # ---- CORRECTED 2026-09-25, ROOT CAUSE CORRECTED 2026-09-26 -----------
    #
    # This said `id + '|' + session_id`, which is the OLD formula. MEASURED:
    # `chat_hash == chat_pair_hash(chat_id, session_id)` — verified against
    # `skill_library_api.chat_pair_hash` on every matching row.
    #
    # THE OLD DECLARATION NAMED THE DEFECT. Because the register declared
    # `derived_from="id + '|' + session_id"`, the writer kept producing a hash of
    # `id` — and the register said that was CORRECT. A declaration that names the
    # wrong input is worse than no declaration: it makes the wrong value
    # authoritative.
    #
    # MEASURED (2026-09-26): this is NOT "53 legacy rows". It is **58 of 58**
    # non-NULL rows, produced by the LIVE writer
    # (`skill_library_api.py:401-408`, `chat_pair_hash(str(mid), session_id)`
    # where `mid` is `chat_main.id`). The writer has since stopped writing it;
    # `chat_hash` is append-only audit and `chat_hash_recomputed` carries the key.
    ("chat_main", "chat_hash", "pair_key", "chat_id + '|' + session_id"),
)

# Legacy hash -> chat_main.id bridge. Keeps existing TEXT chat_id values
# (incl. 'legacy-*' placeholders) FK-valid after chat_id becomes INTEGER.
CHAT_MAIN_HASH_DDL = """
CREATE TABLE IF NOT EXISTS chat_main_hash (
    hash          TEXT PRIMARY KEY,
    chat_main_id  INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (chat_main_id) REFERENCES chat_main (id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_main_hash_main
    ON chat_main_hash (chat_main_id);
"""

# ---- chat_id registry (LEGACY: sha256 text PK) ----
# Kept for backward compat. chat_main.id is the identity going forward;
# chat_id text is now a SHA256 content hash, not an id.
CHAT_ID_DDL = """
CREATE TABLE IF NOT EXISTS chat_id (
    chat_id     TEXT    PRIMARY KEY,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ---- chat_identity audit log (DB-driven progress; append-only, never a gate) ----
# Every resolve/register/miss on a chat identity is recorded here so the
# chat_identity API + UI are fully DB-driven (no client-side hashing).
# role is derived from action: GET (resolve/miss) = Question, POST (register) = Answer.
CHAT_IDENTITY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS chat_identity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT,
    chat_id     INTEGER,
    sha256      TEXT,
    chat_hash   TEXT,
    action      TEXT    NOT NULL DEFAULT 'resolve'
                CHECK (action IN ('resolve', 'register', 'miss')),
    ide         TEXT,
    llm         TEXT,
    source      TEXT    NOT NULL DEFAULT 'api',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_identity_log_chat
    ON chat_identity_log (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_identity_log_session
    ON chat_identity_log (session_id, created_at DESC);
"""

# Additive columns for existing DBs (CREATE IF NOT EXISTS won't add cols)
CHAT_IDENTITY_LOG_NEW_COLUMNS = [
    ("ide", "TEXT"),
    ("llm", "TEXT"),
    ("sha256", "TEXT"),
    # read-only, backfilled: correct pair key. chat_hash is left verbatim so
    # legacy history stays auditable; this column is what consumers should read.
    ("chat_hash_recomputed", "TEXT"),
]

# ---- vscode_env_dimension (the OPEN set of environment checks) ----
# NAMED `vscode_env_*`, NOT `conversation_env_*`. The user (2026-09-23):
#
#   "chat system is chat system, vscode > chat > conseraction is another system"
#
# The bare word `conversation` reads as the CHAT system, which is a DIFFERENT
# table set (`chat_center_message`, `chat_identity_log`, `chat_registry`, ...)
# that happens to key on the same session id. The terms are registered in
# `terminology_registry` (`vscode_conversation`, `chat_system` — SIBLINGS, so the
# register itself stores that they are two systems).
#
# WHY A TABLE AND NOT A PYTHON LIST (`subject_kind_registry.py:1-40`, the repo
# rule the user has corrected repeatedly): the set of things worth checking in
# an environment is OPEN — mode, permission, model, reasoning_effort,
# terminal_available, ide, helper, ollama, queue_api, model_present, screen,
# foreground, dpi_pinned, screen_space ... A new dimension must be a ROW, not a
# code edit.
#
# `measure_kind` is the load-bearing column. A dimension is either:
#   'direct'   — read from a field VS Code already holds (e.g. `mode`,
#                `permission` from `pendingRequests[].sendOptions`)
#   'delegate' — answered by ANOTHER component, named in `delegate_ref`
#                (e.g. `f_env_preflight.run_all`)
# Without this column a reader cannot tell "we read it" from "someone else knows",
# and would re-implement the delegated check — the duplication this repo forbids.
VSCODE_ENV_DIMENSION_DDL = """
CREATE TABLE IF NOT EXISTS vscode_env_dimension (
    dim_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    dim_key      TEXT    NOT NULL UNIQUE,
    display_name TEXT    NOT NULL,
    -- 'direct' | 'delegate'. Validated at the WRITE SITE against a Python tuple
    -- (the same pattern `mode_right_registry.value_text` uses), NOT as an SQL
    -- enum: a legal-value set a TEST can assert beats a hardcoded enum.
    measure_kind TEXT    NOT NULL DEFAULT 'direct',
    -- For 'delegate': the exact entry point, e.g.
    -- 'f_env_preflight.run_all'. Empty for 'direct'.
    delegate_ref TEXT    NOT NULL DEFAULT '',
    -- 1 = a fault on this dimension blocks; 0 = advisory only.
    is_required  INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0, 1)),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    why          TEXT    NOT NULL DEFAULT 'NA',
    cite_ref     TEXT    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_vscode_env_dim_active
  ON vscode_env_dimension (is_active, sort_order, dim_key);
"""

# ---- vscode_env_log (APPEND-ONLY observation of a dimension) ----
# FK TARGET IS `chat_main(id)`, and the COLUMN IS NAMED `vscode_conversation_id`.
#
# THE COLUMN NAME IS THE POINT. It was `chat_main_id`, which made a reader think
# the value belonged to the CHAT system — the exact mis-read the user reported:
# "too easy to have name mis-understand problem". The VALUE is still
# `chat_main.id`; only the NAME changed, because the name is what misleads.
#
# There is deliberately NO `vscode_conversation` TABLE: `chat_main` ALREADY keys
# a conversation on the VS Code session id (`chat_main.session_id TEXT NOT NULL
# UNIQUE`). A second identity table for one concept is the "two systems" defect
# the user warned about — so this table JOINS to the existing one instead, and
# `terminology_registry` records that `chat_main` IS the VS Code conversation
# identity despite its name.
#
# APPEND-ONLY: a dimension that is re-measured gets a NEW row, never an UPDATE.
# The committed and live `mode` values DISAGREE BY DESIGN (measured: the selector
# leads `inputState.mode` by one request), so the history IS the datum; an
# UPDATE would destroy the very disagreement worth reviewing.
#
# `status` is a real tri-state. 'unknown' exists so that "we could not measure"
# is storable and CANNOT be read as 'ok' — a silent pass is the failure mode
# this whole design guards against.
VSCODE_ENV_LOG_DDL = """
CREATE TABLE IF NOT EXISTS vscode_env_log (
    log_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vscode_conversation_id INTEGER NOT NULL,
    dim_key               TEXT    NOT NULL,
    value_text            TEXT,
    status                TEXT    NOT NULL DEFAULT 'unknown'
                          CHECK (status IN ('ok', 'fault', 'unknown')),
    -- WHERE the value came from: 'sendOptions' | 'mode_attest' |
    -- 'f_env_preflight' | ... . A value with no source cannot be re-checked.
    source                TEXT    NOT NULL DEFAULT 'unknown',
    -- The rule that produced status != 'ok', when there is one.
    fault_code            TEXT    NOT NULL DEFAULT '',
    cite_ref              TEXT    NOT NULL DEFAULT '',
    -- 1 = this describes the request being COMPOSED (not yet sent). The mode
    -- rung is stored this way rather than merged, because the two rungs are
    -- two different facts.
    is_pending            INTEGER NOT NULL DEFAULT 0 CHECK (is_pending IN (0, 1)),
    -- WHEN the fact was true (the file's mtime / the observation time), vs
    -- observed_at = when WE looked. Keeping both separate is what lets a stale
    -- reading be detected instead of trusted (measured defect: an un-pending
    -- snapshot was reported as live).
    evidence_at           TEXT,
    observed_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (vscode_conversation_id, dim_key, observed_at),
    FOREIGN KEY (vscode_conversation_id) REFERENCES chat_main (id)
);
CREATE INDEX IF NOT EXISTS idx_vscode_env_log_conv
  ON vscode_env_log (vscode_conversation_id, dim_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_vscode_env_log_dim
  ON vscode_env_log (dim_key, observed_at DESC);
"""

# ---- phase_registry (the MERGED phase table) ----
# MERGED 2026-09-27 (plan REGISTER.NAMING.AND.PHASE.MERGE).
#
# WHY: `terminology_sweep_phase` and `register_fill_phase` were the SAME SHAPE.
# MEASURED: 10 of 12 columns identical, and their `phase_key` sets were DISJOINT
# (13 + 7 = 20, 0 overlap). The only real difference was the two columns that
# describe WHAT a fill phase fills (`target_table`, `key_col`), which are NULL
# for a sweep phase. **A difference in the ROW is not a difference in the TABLE.**
#
# `phase_kind` is the discriminator: 'register_fill' | 'terminology_sweep'.
# The UNIQUE key is (phase_kind, phase_key), NOT phase_key alone, because the
# two vocabularies are independent and a future collision must be a legal row.
#
# THE OLD NAMES ARE NOT KEPT AS VIEWS. MEASURED trap: `subcatalog` is a VIEW
# with no `INSTEAD OF INSERT` trigger, and `skill_library_api.py:2298,2317`
# 500s on it. A view that cannot be written is a trap, not a compatibility layer.
#
# `cap` IS THE VOLUME CONTROL (unchanged): a phase never processes more than
# this many names, so a capped run cannot be mistaken for the whole set.
PHASE_registry_DDL = """
CREATE TABLE IF NOT EXISTS phase_registry (
    phase_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_kind   TEXT    NOT NULL,
    phase_key    TEXT    NOT NULL,
    display_name TEXT    NOT NULL,
    scope        TEXT    NOT NULL,
    -- NULL for a terminology_sweep phase: a sweep does not fill a table.
    target_table TEXT,
    key_col      TEXT,
    cap          INTEGER NOT NULL DEFAULT 25 CHECK (cap > 0),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    why          TEXT    NOT NULL DEFAULT 'NA',
    cite_ref     TEXT    NOT NULL,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (phase_kind, phase_key)
);
CREATE INDEX IF NOT EXISTS idx_phase_registry_kind
  ON phase_registry (phase_kind, is_active, sort_order);
"""

# ---- terminology_sweep_phase (SUPERSEDED 2026-09-27 by phase_registry) ----
# KEPT AS A STRING because `terminology_sweep.py` and 6 proofs import the NAME.
# It is NO LONGER executed by `ensure_schema()`; the table is created by
# `phase_registry` above and the rows live there with phase_kind='terminology_sweep'.
TERMINOLOGY_SWEEP_PHASE_DDL = """
CREATE TABLE IF NOT EXISTS terminology_sweep_phase (
    phase_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_key   TEXT    NOT NULL UNIQUE,
    display_name TEXT   NOT NULL,
    -- 'tables' | 'routes' | 'modules' | 'columns' | 'factors' | 'skills'.
    -- Validated at the WRITE SITE against a Python tuple, not an SQL enum.
    scope       TEXT    NOT NULL,
    -- THE VOLUME CONTROL. A phase never processes more than this many names.
    cap         INTEGER NOT NULL DEFAULT 25 CHECK (cap > 0),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    why         TEXT    NOT NULL DEFAULT 'NA',
    cite_ref    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_term_sweep_phase_active
  ON terminology_sweep_phase (is_active, sort_order, phase_key);
"""

# ---- terminology_sweep_run (APPEND-ONLY record of one name's outcome) ----
# APPEND-ONLY: a re-run appends, so the HISTORY is the datum. A phase that was
# run twice with different results is exactly what a reviewer needs to see, and
# an UPDATE would destroy it.
#
# `outcome` is a real tri-state plus the refusals. 'accepted' means `add_term`
# took it; 'refused' means the register's OWN gate rejected it, and
# `refusal_code` names which gate. A refusal is RECORDED, never retried blindly:
# an unregistered name is a real finding, not a transient error.
#
# `cite_supplied` records whether the LOGIC GENERATOR had to supply the citation
# because the 7B could not. MEASURED: the 7B's names and definitions were usable
# every time, but its citations were not (it put a refusal in the field, or
# invented a filename). So this column measures the 7B's REAL gap.
TERMINOLOGY_SWEEP_RUN_DDL = """
CREATE TABLE IF NOT EXISTS terminology_sweep_run (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_key    TEXT    NOT NULL,
    name         TEXT    NOT NULL,
    outcome      TEXT    NOT NULL DEFAULT 'unknown'
                 CHECK (outcome IN ('accepted', 'refused', 'skipped',
                                    'unknown')),
    refusal_code TEXT    NOT NULL DEFAULT '',
    term_id      INTEGER,
    -- The 7B's latency for THIS name, in ms. NULL when the 7B was not called
    -- (discovery is deterministic and never calls it).
    llm_ms       INTEGER,
    -- 1 = the citation came from `terminology_cite`, not from the 7B.
    cite_supplied INTEGER NOT NULL DEFAULT 0 CHECK (cite_supplied IN (0, 1)),
    cite_ref     TEXT    NOT NULL DEFAULT '',
    observed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (phase_key, name, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_term_sweep_run_phase
  ON terminology_sweep_run (phase_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_term_sweep_run_outcome
  ON terminology_sweep_run (outcome, refusal_code);
"""

# ---- register_fill_phase (SUPERSEDED 2026-09-27 by phase_registry) ----
# KEPT AS A STRING because `register_fill.py` imports the NAME. It is NO LONGER
# executed by `ensure_schema()`; the rows live in `phase_registry` with
# phase_kind='register_fill'.
REGISTER_FILL_PHASE_DDL = """
CREATE TABLE IF NOT EXISTS register_fill_phase (
    phase_key    TEXT    PRIMARY KEY,
    display_name TEXT    NOT NULL,
    scope        TEXT    NOT NULL,
    target_table TEXT    NOT NULL,
    key_col      TEXT    NOT NULL,
    cap          INTEGER NOT NULL DEFAULT 40 CHECK (cap > 0),
    sort_order   INTEGER NOT NULL DEFAULT 100,
    why          TEXT    NOT NULL DEFAULT 'NA',
    cite_ref     TEXT    NOT NULL DEFAULT 'NA',
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_registry_fill_phase_order
  ON register_fill_phase (is_active, sort_order);
"""

# APPEND-ONLY. EVERY outcome is recorded, including a refusal.
#
# MEASURED DEFECT this shape avoids (found in `terminology_sweep`): the first
# version INSERTed only on the success path, so a `NO_CHECKABLE_CITE` refusal was
# COUNTED and never WRITTEN — invisible in the very log that exists to make
# refusals reviewable.
#
# `observed_at` is supplied by PYTHON at microsecond resolution, not left to the
# DEFAULT. MEASURED: `datetime('now')` resolves to the SECOND, and with
# `UNIQUE (phase_key, key_text, observed_at)` a dry run followed immediately by
# an APPLY run collided and every APPLIED row was silently dropped (`done=23`
# instead of 25).
REGISTER_FILL_RUN_DDL = """
CREATE TABLE IF NOT EXISTS register_fill_run (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_key    TEXT    NOT NULL,
    key_text     TEXT    NOT NULL,
    outcome      TEXT    NOT NULL DEFAULT 'unknown'
                 CHECK (outcome IN ('accepted', 'refused', 'skipped',
                                    'unknown')),
    refusal_code TEXT    NOT NULL DEFAULT '',
    row_id       INTEGER,
    -- The 7B's latency for THIS key, in ms. NULL when the 7B was not called
    -- (discovery is deterministic and never calls it).
    llm_ms       INTEGER,
    -- 1 = the citation came from `terminology_cite`, not from the 7B.
    cite_supplied INTEGER NOT NULL DEFAULT 0 CHECK (cite_supplied IN (0, 1)),
    cite_ref     TEXT    NOT NULL DEFAULT '',
    observed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (phase_key, key_text, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_registry_fill_run_phase
  ON register_fill_run (phase_key, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_registry_fill_run_outcome
  ON register_fill_run (outcome, refusal_code);
"""

# THE CONTROL, PERSISTED. One row per phase per run.
#
# WHY THIS TABLE EXISTS (measured 2026-09-23, and it is the user's own complaint
# in a new place): `run_phase()` DID run the control and returned it in its
# result dict — and NOTHING WROTE IT DOWN. So the control's verdict existed for
# the duration of one function call and then vanished, exactly like the proof
# report the user complained about ("it will disappear once complete, will turn
# to very simple report").
#
# The damage was not theoretical. `_diag_7b_fill_quality.py` printed
#
#     "KNOWN LIMIT (measured, not a guess): the CONTROL fails"
#
# as a HARDCODED STRING, while the run log held NO control row at all. So the
# diagnostic ASSERTED a measurement it had never taken — and the assertion was
# the OPPOSITE of the truth: the control PASSED (the 7B refused the fake key).
# A verdict that is not persisted gets replaced by a guess, and the guess is
# believed because it is printed in the same font as the measurements.
#
# `pass` is a SQL keyword, so the column is `passed`.
REGISTER_FILL_CONTROL_DDL = """
CREATE TABLE IF NOT EXISTS register_fill_control (
    control_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_key    TEXT    NOT NULL,
    control_key  TEXT    NOT NULL,
    ran          INTEGER NOT NULL DEFAULT 0 CHECK (ran IN (0, 1)),
    passed       INTEGER NOT NULL DEFAULT 0 CHECK (passed IN (0, 1)),
    refused      INTEGER NOT NULL DEFAULT 0 CHECK (refused IN (0, 1)),
    invented     TEXT    NOT NULL DEFAULT '',
    reason       TEXT    NOT NULL DEFAULT '',
    model        TEXT    NOT NULL DEFAULT '',
    observed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (phase_key, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_registry_fill_control_phase
  ON register_fill_control (phase_key, observed_at DESC);
"""

# ---- chat_center_message (Chat Center 3-step flow: submit -> analyze -> answer) ----
# STEP1 user submits content; STEP2 middleware resolves chat_id (int) + sha256;
# STEP3 answer posts chat_id + sha256 + output (Answer row).
CHAT_CENTER_MESSAGE_DDL = """
CREATE TABLE IF NOT EXISTS chat_center_message (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT,
    chat_id           INTEGER,
    sha256            TEXT,
    chat_hash         TEXT,
    role              TEXT    NOT NULL DEFAULT 'Question'
                      CHECK (role IN ('Question', 'Answer')),
    content           TEXT,
    catalog_id        INTEGER,
    catalog_name      TEXT,
    subcatalog_id     INTEGER,
    subcatalog_name   TEXT,
    skill_id          TEXT,
    skill_name        TEXT,
    llm               TEXT,
    ide               TEXT,
    answered_at       TIMESTAMP,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chat_center_chat
    ON chat_center_message (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_center_session
    ON chat_center_message (session_id, created_at DESC);
"""

# Additive columns for existing DBs (mirrors CHAT_IDENTITY_LOG_NEW_COLUMNS).
# NOTE: CREATE TABLE IF NOT EXISTS does NOT add columns to an existing table, so
# these are applied by the _add_columns_if_missing() path in init_db(). A column
# listed here but not added there would silently not exist.
CHAT_CENTER_MESSAGE_NEW_COLUMNS = [
    # read-only, backfilled: correct pair key. chat_hash kept verbatim.
    ("chat_hash_recomputed", "TEXT"),
    # A discovery needs a NAME. The table had `content` but no `title`, so a
    # finding could be stored but never listed or searched.
    ("title", "TEXT"),
    # What kind of record this is, so a discovery is not confused with a Q/A
    # turn: 'question' | 'answer' | 'discovery' | 'lesson'.
    ("event", "TEXT"),
    # Citation discipline: a finding must carry a checkable reference
    # (path:line or a command). Findings without one are DISCARDED at the write
    # site, never downgraded — see .github/skills/citation-discipline.
    ("evidence_ref", "TEXT"),
    # Which rule/skill the finding came from, and its measurable effect, so a
    # later reader can reproduce the claim instead of trusting the prose.
    ("rule_version", "TEXT"),
    ("measured_effect", "TEXT"),
    # The WORKFLOW status of this turn, so a reader can tell a finished reply
    # from one that is waiting on a decision WITHOUT reading the prose.
    #    #   done        finished, and every claim carries evidence
    #   ask         a specific decision is unanswered AND the worker stopped
    #   progressive work in flight, nothing being waited on
    #   QC          finished, awaiting an INDEPENDENT verdict
    #
    # WHY A SEPARATE COLUMN, not `event`: `event` already means what KIND of
    # record this is ('question' | 'answer' | 'discovery' | 'lesson'). Reusing
    # it for workflow state would make one column carry two meanings, and a
    # reader could not tell "this is a question" from "this is waiting".
    #
    # NO CHECK CONSTRAINT, deliberately. SQLite cannot add a CHECK to an
    # existing table without rebuilding it, and a rebuild of a live message log
    # is a bigger risk than the value it buys. The value is validated at the
    # WRITE SITE instead (`create_chat_center_message`), which is where a bad
    # value can still be refused with a reason.
    ("status", "TEXT"),
    # THE FAULT THIS MESSAGE REPORTS, when it reports one: `fault:<group>#<event_id>`.
    #
    # A DEDICATED COLUMN, and MEASURED why: the link was first stored in
    # `measured_effect`, and an acknowledgment wrote its NOTE into the same
    # column — so the ack OVERWROTE the link it existed to preserve (measured
    # 2026-09-24: msg 83 went from `fault:runtime_watchdog#22` to
    # `acked by ...`, and `parse_link` then returned not-a-fault-link). A second
    # copy of one fact in a shared slot is the defect family this repo removes
    # repeatedly; a LINK needs its own slot, because a note is written over the
    # column it lives in and a link must survive being used.
    #
    # `<event_id>` is part of the token ON PURPOSE: a recurrence opens a NEW
    # fault row, so an acknowledgment can never close the WRONG occurrence.
    ("fault_ref", "TEXT"),
]

# ---- ticket_center (the SERVICE registry) ----
# The same shape `channel` / `module` use: a `code` that is UNIQUE plus a `name`.
# A registry, not free text — so "which service owns this ticket" is a row, and
# a typo is refused rather than stored as a new service nobody owns.
TICKET_CENTER_DDL = """
CREATE TABLE IF NOT EXISTS ticket_center (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- THE ORIGIN. Renamed from `service` (human, 2026-09-26): the column names
    -- WHERE a ticket came from (chat_center / task_center / manual /
    -- llm_service), not a capability. `service` was shared with `llm_service`,
    -- which answers a DIFFERENT question (which model pool serves the task).
    ticket_origin TEXT  NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ticket_center_active
    ON ticket_center (is_active, ticket_origin);
"""

# ---- ticket (the PROGRESS TRACE of an ENTITY) ----
# "eumu" = ENTITY. `R-1-75-1` = {LETTER}-{table_id}-{row_id}-{version}
# (`entity_id.SHAPE`) — the id space `entity_id.py` parses and VERIFIES. The
# 3-part form {LETTER}-{ref_id}-{version} is the OLD one (2026-09-27).
#
# ---- ticket (what a SERVICE is for) ----
# A ticket is FOR a SERVICE. That is the whole of it (user, 2026-09-21):
#
#   "ticket is for which services provide to"
#   "no related too other, don't mix up"
#
# The ticket USED to carry entity_type / entity_ref_id / version / eumu_id, and
# a UNIQUE over them. That made the ENTITY define the ticket's identity, which
# is the mixing the user rejected: a ticket is about a SERVICE, not about a
# thing. Those four columns are GONE.
#
# This also removes a real defect: the table never had an `entity_row` column,
# so `T-1-5-1` and `T-1-6-1` were INDISTINGUISHABLE here, and the UNIQUE could
# not tell two rows of one table apart. Dropping the entity columns is a fix,
# not just a simplification.
#
# `is_active` is the CURRENT state; `ticket_event` is the HISTORY. A single
# boolean cannot answer "how did this progress", which is the stated purpose.
TICKET_DDL = """
CREATE TABLE IF NOT EXISTS ticket (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_center_id INTEGER NOT NULL,
    title          TEXT,
    status         TEXT    NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'in_progress', 'blocked',
                                     'closed', 'cancelled')),
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    opened_by      TEXT,
    closed_at      TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (ticket_center_id, title),
    FOREIGN KEY (ticket_center_id) REFERENCES ticket_center (id)
);
CREATE INDEX IF NOT EXISTS idx_ticket_center
    ON ticket (ticket_center_id, is_active);
CREATE INDEX IF NOT EXISTS idx_ticket_status
    ON ticket (status, is_active);
"""

# ---- ticket_module_map (the MAPPING: ticket + module, and nothing else) ----
# The user's wording (2026-09-21):
#
#   "ticket is for which services provide to / no related too other, don't mix up"
#   "chat / task is module? if yes"
#   "id | ticket_id | module_id | is_active | created_at | updated_at"
#
# So the three things are SEPARATE and this table is the only place they meet:
#
#   ticket   which SERVICE the work is for      (ticket table)
#   module   WHERE in the system it happens     (module_registry)
#   map      ticket <-> module                  (THIS table)
#
# MEASURED: `module_registry` holds 4 active rows -- task_center (1),
# mouse_spot_helper (2), openclaw_companion (3), llm_runtime (15115).
# `task` IS a module (`task_center`). `chat` is NOT: there is no chat /
# chat_center row, and "chat" already exists at a different level as the
# capability `task_center.chat_identity` (ontology_store.py:355).
#
# `module_id` is NOT `entity_id`. An entity id names a THING (`T-5-1-1`); a
# module is a place in the taxonomy. Conflating them is the mixing this table
# exists to prevent, so the column is a plain FK to `module_registry`.
#
# ---- `TICKET_MODULE_MAP_DDL` REMOVED 2026-09-25 --------------------------
#
# The RETIREMENT was already decided on 2026-09-23 (see the note in
# `ensure_task_center_schema()`): `ticket_module_map` was the MODULE special case
# of "a ticket and its subject", and it was folded into `ticket_subject` with
# `subject_kind='module'`. The DDL was removed from the ensure LIST then, but the
# CONSTANT was left here — so the retirement was HALF-DONE: the table could not
# come back, but `register_fill.discover("db_field")` still read the constant and
# proposed fields for a table that does not exist.
#
# The reasoning above is KEPT (it records why `module_id` is a plain FK and not an
# entity id); only the dead DDL is gone.

# ---- ticket_event (APPEND-ONLY history) ----
# One row per state change, so "how did the eumu progress" is answerable. The
# `ticket` row holds the CURRENT state; this holds the PATH to it. Never
# UPDATEd, never DELETEd — the same rule as `skill_capability_tag_log`.
TICKET_EVENT_DDL = """
CREATE TABLE IF NOT EXISTS ticket_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    from_status TEXT    NOT NULL DEFAULT 'NA',
    to_status   TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT 'NA',
    cite_ref    TEXT    NOT NULL DEFAULT 'NA',
    actor       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES ticket (id)
);
CREATE INDEX IF NOT EXISTS idx_ticket_event_ticket
    ON ticket_event (ticket_id, created_at DESC);
"""

# ---- conversation_status_event (APPEND-ONLY history of chat_main.status) ----
# WHY THIS EXISTS (plan WATCHDOG.DRAFT.CONSUMER, step S5).
# `chat_main.status` is the CONVERSATION-level status (the parent of a turn).
# S5 adds the WRITER for it (`chat_level.set_conversation_status`) and an
# approval API, but NO consumer — approving a conversation executes NOTHING.
# That is deliberate: the consumer is a later task.
#
# The approval must not be ANONYMOUS, so every transition writes one row here
# carrying WHO decided (`actor`) and WHAT they cited (`cite_ref`). This is the
# same shape as `ticket_event` (from_status / to_status / cite_ref / actor) and
# the same rule as `skill_capability_tag_log`: APPEND-ONLY, never UPDATEd,
# never DELETEd. The `chat_main` row holds the CURRENT status; this holds the
# PATH to it, so "who approved this conversation, and on what evidence" is
# answerable after the fact.
CONVERSATION_STATUS_EVENT_DDL = """
CREATE TABLE IF NOT EXISTS conversation_status_event (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id     INTEGER NOT NULL,
    session_id  TEXT    NOT NULL DEFAULT 'NA',
    from_status TEXT    NOT NULL DEFAULT 'NA',
    to_status   TEXT    NOT NULL,
    cite_ref    TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (conv_id) REFERENCES chat_main (id)
);
CREATE INDEX IF NOT EXISTS idx_conversation_status_event_conv
    ON conversation_status_event (conv_id, created_at DESC);
"""

# ---- pipeline_run (the 9-stage conversation pipeline) ----
# WHY THIS EXISTS (plan CHAT.PIPELINE.S6).
# A CONVERSATION moves through 9 stages, and each stage is a TASK with its own
# task id. This table records ONE row per (conversation, stage) so "which stage
# is this conversation at, and what did each stage produce" is answerable.
#
# IT IS NOT `plan_sessions`. MEASURED: `plan_sessions` carries a 24-hour TTL
# (`PLAN_SESSION_TTL_SECONDS = 86400`) and a daemon thread purges it hourly, so
# it is a TEMPORARY table and cannot hold a persistent pipeline. This table has
# NO TTL.
#
# THE FK TARGET IS `chat.chat_id` (INTEGER), NOT the `chat_id` table. MEASURED:
# `chat_id` is a TEXT table (477 rows) whose values are `'legacy-...'` strings
# synthesised by the `plan_sessions` migration, and it shares ZERO values with
# `chat.chat_id`. `chat_main.chat_id` and `chat_center_message.chat_id` both
# point at `chat`, so this table follows them.
#
# THE 9 STAGES ARE REGISTERED TERMS (plan CHAT.PIPELINE.S6, S6.2): draft,
# research, research_verify, planning, plan_verify, writing, verify,
# verify_confirm, completed. The CHECK is the vocabulary, and the vocabulary is
# the register — a stage that is not a registered term cannot be stored.
#
# S6 IS A SKELETON: this table is created and its CHECK is enforced, but NOTHING
# writes to it yet and NOTHING reads it. The consumer is a later task.
PIPELINE_RUN_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_run (
    run_id             TEXT    PRIMARY KEY,
    conversation_id    INTEGER,
    chat_id            INTEGER,
    session_id         TEXT,
    stage              TEXT    NOT NULL
                       CHECK (stage IN ('draft', 'research', 'research_verify',
                                        'planning', 'plan_verify', 'writing',
                                        'verify', 'verify_confirm', 'completed')),
    version            INTEGER NOT NULL DEFAULT 1,
    extracted_entities TEXT,
    verdict            TEXT,
    cite_ref           TEXT,
    is_active          INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_conversation
    ON pipeline_run (conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_chat
    ON pipeline_run (chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_stage
    ON pipeline_run (stage);
-- THE DEDUP GUARANTEE (added 2026-09-30, plan CHAT.PIPELINE.S7, step S7a).
-- MEASURED before S7a: `pipeline_run` had ONLY `run_id` UNIQUE, so a second
-- enqueue for the same (conversation, stage) would create a DUPLICATE run — the
-- "duplicate run" failure mode the S7 pre-flight named (P6). A SELECT-then-INSERT
-- check in the consumer would have a TOCTOU window, so the guarantee is placed
-- HERE, at the DB level, where a race cannot bypass it. The pair is
-- (conversation_id, stage), NOT conversation_id alone: `pipeline_run` is a
-- PER-STAGE record (the S6 proof inserts 9 rows for one conversation, one per
-- stage), so one conversation legitimately has one run per stage.
CREATE UNIQUE INDEX IF NOT EXISTS idx_pipeline_run_conv_stage
    ON pipeline_run (conversation_id, stage);
"""

# ---- pipeline_run_task (one run, MANY tasks) — the task<->run link ----
#
# WHY THIS EXISTS (plan CHAT.PIPELINE.S8, step S8a)
# -------------------------------------------------
# MEASURED before S8a: there was NO task<->run link. `task_entity_link` links a
# task to an ENTITY (`entity_type='R'` + `entity_ref_id` = `code_registry.id`),
# and `pipeline_run` is NOT an entity — so reusing that table would give it TWO
# meanings ("task implements entity" AND "task advances run"), which the repo's
# "one table, one meaning" rule forbids. So the link is its OWN table, the same
# shape `ticket_chat_link` already uses for "one ticket, many chats".
#
# THE UNIQUE PAIR IS THE IDEMPOTENCY GUARD. `UNIQUE (run_id, task_id)` means a
# task can advance a run AT MOST ONCE — the "double advance" failure mode the S8
# pre-flight named (P6). A poller that reads the same completion event twice
# cannot advance twice: the second INSERT is refused by the DB, not by a check.
PIPELINE_RUN_TASK_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_run_task (
    link_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL,
    task_id     TEXT    NOT NULL,
    stage       TEXT    NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'advances',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_task_run
    ON pipeline_run_task (run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_task_task
    ON pipeline_run_task (task_id);
"""

# ---- ticket_chat_link (one ticket, MANY chats) ----
# The stated purpose is "submit ticket to chat center to have chat id with eumu
# id". A ticket can be discussed across several chats, so the link is its own
# table rather than a `chat_id` column on `ticket` — a column would allow only
# one chat and would silently drop the rest.
TICKET_CHAT_LINK_DDL = """
CREATE TABLE IF NOT EXISTS ticket_chat_link (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER NOT NULL,
    chat_id     INTEGER NOT NULL,
    role        TEXT    NOT NULL DEFAULT 'discussion',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (ticket_id, chat_id),
    FOREIGN KEY (ticket_id) REFERENCES ticket (id),
    FOREIGN KEY (chat_id)   REFERENCES chat_main (id)
);
CREATE INDEX IF NOT EXISTS idx_ticket_chat_link_chat
    ON ticket_chat_link (chat_id);
"""

# ---- chat_registry (a TICKET + the CHAT it was discussed in) ----
# The user's flow: middleware (chat_center submit) opens a TICKET, the ticket
# goes to a chat room to get a chat_id, and THIS table pairs the two. One row
# <-> one ticket (user, 2026-09-21), so `ticket_id` is UNIQUE rather than a
# second many-to-many table: a row that could point at two tickets would make
# "which ticket is this about" unanswerable.
#
# RENAMED from `case_registry` (user, 2026-09-21: "why not name = chat_registry
# / not easy for mis-understand / rename it now"). "Case" was ambiguous — a
# reader could not tell whether it meant a bug case, a test case, or this
# pairing. The name now SAYS what a row is: a ticket and its chat.
#
# THE PK IS `chat_registry_id`, NOT `chat_id`. `chat_id` is ALREADY a column
# here and means the FK into `chat_main` (the chat room). Two different things
# under one name is the mixing this rename exists to remove.
#
# `chat_key` is the NATURAL key (survives a rebuild); `chat_registry_id` is the
# AUTOINCREMENT identity. There is NO entity id: this is a MAPPING, and a
# mapping does not name a thing of its own (user, same day: "case 唔應該有
# Z-... -> remove").
chat_registry_DDL = """
CREATE TABLE IF NOT EXISTS chat_registry (
    chat_registry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_key         TEXT    NOT NULL UNIQUE,
    ticket_id        INTEGER NOT NULL UNIQUE,
    chat_id          INTEGER,
    -- 🔴 `service` WAS RENAMED TO `ticket_origin` (2026-09-26), AND THIS DDL
    -- STILL DECLARED THE OLD NAME. MEASURED 2026-09-29: `COLUMN_RENAMES` above
    -- renames `chat_registry.service` -> `chat_registry.ticket_origin`, so the
    -- LIVE table has `ticket_origin` and NOT `service`. A FRESH database built
    -- from this DDL would therefore have a column the migration then renames —
    -- i.e. the declared shape and the live shape disagreed, and the declaration
    -- was the stale one. `_proof_fresh_build_entry_points.py` QC-17 caught it.
    ticket_origin    TEXT,
    status           TEXT    NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'in_progress', 'blocked',
                                       'closed', 'cancelled')),
    opened_by        TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (ticket_id) REFERENCES ticket (id),
    FOREIGN KEY (chat_id)   REFERENCES chat_main (id)
);
CREATE INDEX IF NOT EXISTS idx_chat_registry_ticket
    ON chat_registry (ticket_id);
CREATE INDEX IF NOT EXISTS idx_chat_registry_active
    ON chat_registry (is_active, status);
"""

# ---- user_environment (DB-driven who/where: user + IP + computer + locale) ----
# One row per (computer_id, ip_address) — upserted on each detect. Lets the UI
# answer "who has activity" with timezone / language / geo detail.
USER_ENVIRONMENT_DDL = """
CREATE TABLE IF NOT EXISTS user_environment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    ip_address      TEXT,
    computer_id     TEXT,
    computer_name   TEXT,
    os_name         TEXT,
    os_version      TEXT,
    python_version  TEXT,
    timezone        TEXT,
    tz_offset_sec   INTEGER,
    country         TEXT,
    country_code    TEXT,
    region          TEXT,
    city            TEXT,
    isp             TEXT,
    language        TEXT,
    locale          TEXT,
    detail          TEXT,
    source          TEXT    NOT NULL DEFAULT 'api',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (computer_id, ip_address),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_user_environment_computer
    ON user_environment (computer_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_environment_ip
    ON user_environment (ip_address, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_environment_user
    ON user_environment (user_id, updated_at DESC);
"""

# ---- app: installed software (DB-driven, id only — never an alias/slug) ----
# "app" = a software application the user has installed (OpenClaw Companion,
# VS Code, Chrome, Ollama...). It is NOT a vision template (that is
# vision_asset) and NOT an MCP-only concept: an app may or may not expose an
# MCP endpoint, hence the nullable mcp_url.
#
# WHY a table instead of a hard-coded list: the UI must answer "which apps does
# this user have, and are they online" from the DB, so adding an app is a row
# insert, not a code change.
APP_DDL = """
CREATE TABLE IF NOT EXISTS app (
    app_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    app_key     TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'desktop',
    exe_path    TEXT,
    process_name TEXT,
    mcp_url     TEXT,
    description TEXT,
    -- THE 5W1H "WHERE" FOR AN APP (2026-09-25).
    --
    -- THE USER: "enviornment url and channel url can help to have 5W1H for
    -- each, so system is easy to classify what is happen now, don't mix up any
    -- more" and "\"C:\\Users\\user\\AppData\\Local\\Programs\\Microsoft VS
    -- Code\\Code.exe\"".
    --
    -- MEASURED: `exe_path` already holds the executable for 3 of the 4 rows,
    -- but it is the LAUNCH path, not the 5W1H location. `url` is the location
    -- a reader can open: an exe path for a desktop app, a page for a web app.
    url         TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_app_active
    ON app (is_active, app_key);
"""

# ---------------------------------------------------------------------------
# channel_registry -- the CHANNEL register (the TOP directory level).
#
# WHY THIS CONSTANT EXISTS (measured 2026-09-25)
# ---------------------------------------------
# MEASURED: `channel_registry` had NO `CREATE TABLE` in any production module.
# The statement existed ONLY in FOUR `_proof_*.py` files
# (`_proof_hardcode_scope.py:48`, `_proof_registry_fill.py:233`,
# `_proof_taxonomy_backfill.py:45`, `_proof_track_scope.py:136`), so the schema
# had FOUR copies and NO authoritative home -- the same "second copy of one
# fact" defect this repo keeps removing. Adding a fifth copy would have made it
# worse, so the declaration lives HERE, where a reader looks for it.
#
# THE HIERARCHY (from `terminology_registry`, verbatim):
#   `channel_folder` "The TOP directory level ... one folder per CHANNEL"
#   `module_folder`  "The SECOND directory level ... one folder per MODULE"
#   `capability_folder` "The THIRD directory level ... one folder per CAPABILITY"
# So a channel is a TOP-LEVEL FOLDER, like a domain (`xxx.com`, `vvv.com`).
#
# `url` IS THE 5W1H "WHERE" (2026-09-25). THE USER: "channel url =
# C:\projects\agent_system". MEASURED: the table had no `url` column, so the
# one fact that says WHERE the channel is had nowhere to live.
CHANNEL_REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS channel_registry (
    channel_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_key  TEXT    NOT NULL UNIQUE,
    name         TEXT    NOT NULL,
    description  TEXT,
    -- THE 5W1H "WHERE": the filesystem path of the channel.
    -- THE USER (2026-09-25): "channel url = C:\\projects\\agent_system".
    url          TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version      TEXT    NOT NULL DEFAULT '1',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_channel_registry_active
  ON channel_registry (is_active, channel_key);
"""

# ---- user_asset: which app a user has, and its state ----
# Pure link table (user_id, app_id) PLUS state, so the table can answer
# "is OpenClaw installed/online for this user" without joining anything else.
# UNIQUE(user_id, app_id) makes the sync an idempotent upsert.
USER_ASSET_DDL = """
CREATE TABLE IF NOT EXISTS user_asset (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    app_id       INTEGER NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'unknown',
    version      TEXT,
    exe_path     TEXT,
    is_enabled   INTEGER NOT NULL DEFAULT 1 CHECK (is_enabled IN (0, 1)),
    last_seen_at TIMESTAMP,
    detail       TEXT,
    source       TEXT    NOT NULL DEFAULT 'sync',
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, app_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (app_id) REFERENCES app(app_id)
);
CREATE INDEX IF NOT EXISTS idx_user_asset_user
    ON user_asset (user_id, status);
CREATE INDEX IF NOT EXISTS idx_user_asset_app
    ON user_asset (app_id, status);
"""

# ---- source: chat communication sources (IDE / APP / BROWSER) ----
# The Chat Center Setting page needs a "from" dropdown. A source is NOT an app
# (app = installed software) and NOT a vision target (mouse_spot_targets.json):
# it is a place a chat can come FROM, with an optional URL and hotkey.
#
# WHY a table instead of a hard-coded list: adding a source must be a row
# insert, not a code change. `url` is nullable because only some sources have
# one (VS Code / 豆包 have none; Chrome -> DeepSeek and Edge -> Gemini do).
SOURCE_DDL = """
CREATE TABLE IF NOT EXISTS source (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key  TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'APP'
                CHECK (kind IN ('IDE', 'APP', 'BROWSER')),
    url         TEXT,
    hotkey      TEXT,
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_source_active
    ON source (is_active, source_key);
"""

# Additive columns for the 6-STEP target capture flow (2026-09-20).
# CREATE TABLE IF NOT EXISTS will NOT add columns to an existing `source`, so
# these MUST also be applied through _add_columns_if_missing().
SOURCE_NEW_COLUMNS = [
    # APP vs URL is the USER's "type" axis for the capture form: an APP source
    # resolves to an executable path, a URL source to a link. `kind`
    # (IDE/APP/BROWSER) is the CATEGORY; source_kind is HOW you reach it, which
    # is what the capture wizard needs in order to render the right text box.
    ("source_kind", "TEXT"),
    # Links to `app`, which already owns exe_path / process_name. Keeping the
    # executable path in ONE place stops the same path being duplicated per
    # source and drifting apart.
    ("app_id", "INTEGER"),
    # docs/paste_outside_worker_skill_short.md:112 states
    # "source.instruction_limit is NULL". The COLUMN DID NOT EXIST, so that
    # statement was false and instruction_fit could never report a verified
    # limit. NULL here means NOT MEASURED — never "unlimited".
    ("instruction_limit", "INTEGER"),
]

# Backfill for the existing 4 seeded rows so the dropdown can render the right
# control immediately (URL -> url box, APP -> exe-path box).
#
# 豆包 IS A BROWSER, NOT A DESKTOP APP. THE HUMAN (2026-09-26): "豆包 is a
# **desktop app** is past !! now is 豆包 is a browser!! ... and she has it own
# offical browser too / update your old data, to stop mis-understand".
#
# MEASURED on this machine: `%LOCALAPPDATA%\Doubao\Application\app\` contains
# `Doubao_browser_proxy.exe`, `libEGL.dll`, `libGLESv2.dll`, `resources.pak`,
# `vk_swiftshader.dll` and `locales/` -- the Chromium runtime. So 豆包 is a
# Chromium-based BROWSER that happens to ship as a desktop application, and its
# `source_kind` is URL (it is reached by a URL), not APP.
#
# The old value was `APP` with the note "reached by launching Doubao.exe". That
# was true of the LAUNCHER and false of the THING: a browser is reached by a
# URL, and calling it an APP made every later reader pick the wrong control.
SOURCE_KIND_BACKFILL = {
    "vscode": "APP",           # reached by launching Code.exe
    "doubao": "URL",           # a Chromium BROWSER, reached by URL
    "chrome_deepseek": "URL",  # reached by URL
    "edge_gemini": "URL",      # reached by URL
}

# ---- flow_setting: the Chat Center FLOW table (user spec 2026-09-20) ----
# A flow is an ordered list of steps. Each step carries the QUESTION to ask and
# the VALUE that step produces, plus where to go next. Clicking a question or a
# value in the UI drives the flow (selects that step); the ACTION column is the
# hook the API will execute later.
#
# WHY a table: the flow must be editable in the UI without a code change, and
# the same flow must be replayable by a script later (API), so the definition
# lives in the DB, not in the component.
FLOW_SETTING_DDL = """
CREATE TABLE IF NOT EXISTS flow_setting (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    flow_key    TEXT    NOT NULL,
    step_no     INTEGER NOT NULL DEFAULT 1,
    question    TEXT,
    value       TEXT,
    next_step   INTEGER,
    action      TEXT,
    description TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (flow_key, step_no)
);
CREATE INDEX IF NOT EXISTS idx_flow_setting_key
    ON flow_setting (flow_key, step_no);
"""

PLAN_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS plan_sessions (
    session_id          TEXT    NOT NULL,
    chat_id             TEXT    NOT NULL,
    chat_hash           TEXT,
    root_seq            TEXT    NOT NULL,
    requirement         TEXT    NOT NULL,
    stage               TEXT    NOT NULL DEFAULT 'new'
        CHECK(stage IN ('new','ask','confirm','plan','completed')),
    extracted_entities  TEXT,
    version             INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, chat_id),
    FOREIGN KEY (chat_id) REFERENCES chat_id(chat_id) ON DELETE RESTRICT
);
"""

# ---- interactive plan session audit log (append-only, never a gate) ----
PLAN_SESSION_LOG_DDL = """
CREATE TABLE IF NOT EXISTS plan_session_log (
    log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL,
    chat_id     TEXT,
    chat_hash   TEXT,
    stage       TEXT,
    from_stage  TEXT,
    action      TEXT,
    payload     TEXT,
    state       TEXT,
    track_id    TEXT,
    qc_warnings TEXT,
    qc_errors   TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_plan_session_log_session
    ON plan_session_log (session_id, created_at DESC);
"""

# ---- llm_tasks (DB mirror of llm_tasks.json) ----
LLM_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS llm_tasks (
    task_id     TEXT    PRIMARY KEY,
    root_seq    TEXT    NOT NULL DEFAULT '',
    type        TEXT    NOT NULL DEFAULT '',
    name        TEXT    NOT NULL DEFAULT '',
    action      TEXT    NOT NULL DEFAULT 'CREATE',
    session_id  TEXT,
    chat_id     TEXT,
    created_by  INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_llm_tasks_root
    ON llm_tasks (root_seq);
"""

# ---- DB-driven browser task orchestration (reusable runner) ----
BROWSER_TASK_STEPS_DDL = """
CREATE TABLE IF NOT EXISTS browser_task_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT    NOT NULL,
    step_order      INTEGER NOT NULL,
    action_type     TEXT    NOT NULL,
    target_ref      TEXT,
    x               INTEGER,
    y               INTEGER,
    input_text      TEXT,
    wait_condition  TEXT,
    remark          TEXT,
    UNIQUE(task_id, step_order)
);
CREATE INDEX IF NOT EXISTS idx_browser_task_steps_task
    ON browser_task_steps (task_id, step_order);
"""

# ---- llm_service (the LLM SERVICE registry) ----
# "services name = LLM services". A service is a CAPABILITY ROUTE, not a model:
# `llm.text` and `llm.vision` are separate services because a text task routed to
# a vision-only model is a wrong route, and a single `llm.services` key could not
# express that. The route is the unit of dispatch.
#
# DB-DRIVEN, same rule as `ticket_center` and the tag registry: the READ path is
# `SELECT ... FROM llm_service`, never a hand-written list. `DEFAULT_LLM_SERVICES`
# in `llm_service_store.py` is a SEED, not the source of truth — a hand-written
# list drifts from the table, and the table is what the dispatcher reads.
#
# `needs_flag` IS THE USER'S REQUIREMENT (2026-09-23):
#     "this service to have a name and define anayle type = text / visual"
#     "so system will nnot have this problem again not hardcode!!!!!"
# MEASURED DEFECT this column removes: the type lived in a PYTHON DICT —
#     llm_service_store.py:529
#     need = {"llm.text": "text", "llm.vision": "visual"}.get(str(service_key))
# so a new route could not carry a type without editing code, and `eye.capture`
# (a real service, measured: 2 providers) silently got `None` — a third
# behaviour nobody declared.
LLM_SERVICE_DDL = """
CREATE TABLE IF NOT EXISTS llm_service (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- THE ROUTE. Renamed from `service_key` (human, 2026-09-26): the column
    -- names WHICH MODEL POOL a request is routed to (`llm.text`, `llm.vision`,
    -- `eye.capture`), and the code already called it "the route registry"
    -- (llm_service_store.py:11). `service` was shared with `ticket_center`,
    -- which answers a DIFFERENT question (which desk opens the ticket).
    llm_route   TEXT    NOT NULL UNIQUE,
    name        TEXT    NOT NULL,
    description TEXT,
    needs_flag  TEXT    NOT NULL DEFAULT 'NA',
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_service_active
    ON llm_service (is_active, llm_route);
"""

# ---- llm_service_type_registry: THE TYPE A ROUTE NEEDS (text | visual) ----
# WHY A REGISTERED VOCABULARY AND NOT FREE TEXT: the same reason
# `capability_kind_registry:4664` exists — a typo would match zero models
# SILENTLY, and a silent zero-match looks exactly like "no provider can serve
# this". The vocabulary and `llm_model`'s columns are deliberately the SAME two
# words, because `can_serve` compares the route's flag against that column.
LLM_SERVICE_TYPE_DDL = """
CREATE TABLE IF NOT EXISTS llm_service_type_registry (
    type_key    TEXT    PRIMARY KEY,
    definition  TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

LLM_SERVICE_TYPES: tuple[tuple[str, str], ...] = (
    ("text", "The route returns TEXT. Served by a model with llm_model.text=1."),
    ("visual", "The route reads IMAGES. Served by a model with "
               "llm_model.visual=1."),
    ("NA", "The route needs NEITHER text nor visual ability, so no llm_model "
           "column is consulted. Declared EXPLICITLY rather than left to a "
           "silent None — measured: `eye.capture` was exactly that case."),
)

# ADDITIVE columns for a database that predates `needs_flag`. MEASURED, and it is
# the trap already recorded for `llm_model`: `CREATE TABLE IF NOT EXISTS` does
# NOT add a column to an existing table, so the live `llm_service` (measured this
# turn: 3 rows, no `needs_flag`) would keep its old shape and every read would
# fail with "no such column".
LLM_SERVICE_ADDITIVE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("needs_flag", "TEXT NOT NULL DEFAULT 'NA'"),
)


def seed_llm_service_type_defaults(conn: sqlite3.Connection) -> dict:
    """Seed the type vocabulary AND backfill `llm_service.needs_flag`.

    Idempotent, and never overwrites a human edit — the same rule as
    `LLM_MODEL_SEED` and `CAPABILITY_TAGS`.

    The BACKFILL is the part that actually removes the hardcode: the two known
    routes are set from the routes' OWN keys (`llm.text` -> text, `llm.vision` ->
    visual), and every other active route is set to `NA` EXPLICITLY rather than
    left to a `None` nobody declared. After this runs, `can_serve` has a column to
    read and the Python dict can be deleted.
    """
    conn.executescript(LLM_SERVICE_TYPE_DDL)
    added = 0
    for type_key, definition in LLM_SERVICE_TYPES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO llm_service_type_registry (type_key, "
            "definition) VALUES (?,?)", (type_key, definition))
        added += int(cur.rowcount or 0)

    # ADDITIVE: the column may not exist on a legacy table.
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(llm_service)")}
    if cols and "needs_flag" not in cols:
        for col, decl in LLM_SERVICE_ADDITIVE_COLUMNS:
            conn.execute("ALTER TABLE llm_service ADD COLUMN %s %s" % (col, decl))

    # BACKFILL from the route key — the ONE place a route's type is inferred, and
    # it is a MIGRATION step, not the read path. `llm.<type>` is the repo's
    # naming convention; the inference happens ONCE here, instead of on every
    # call the way the deleted dict did.
    known = {"llm.text": "text", "llm.vision": "visual"}
    filled = 0
    for r in conn.execute("SELECT id, llm_route, needs_flag FROM llm_service"):
        if str(r["needs_flag"] or "").strip() not in ("", "NA"):
            continue                      # already set (or human-edited)
        want = known.get(str(r["llm_route"]), "NA")
        cur = conn.execute(
            "UPDATE llm_service SET needs_flag=? WHERE id=?", (want, r["id"]))
        filled += int(cur.rowcount or 0)
    conn.commit()
    # An ACTIVE type must always be in the vocabulary: a route whose type is not
    # registered could never be satisfied, and the failure would look like "no
    # provider can serve this" rather than "the type is a typo".
    bad = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT needs_flag FROM llm_service WHERE needs_flag NOT IN "
        "(SELECT type_key FROM llm_service_type_registry)")]
    return {"ok": not bad, "types_added": added, "needs_flag_backfilled": filled,
            "unregistered_types": bad,
            "types": [str(r[0]) for r in conn.execute(
                "SELECT type_key FROM llm_service_type_registry "
                "WHERE is_active=1 ORDER BY type_key")]}

# ---- llm_route_provider (WHO can serve a route, and HOW) ----
# Renamed from `llm_service_provider` (human, 2026-09-26) so the name aligns with
# `llm_service.llm_route`: this table binds a ROUTE to the models that serve it.
# `service_id` became `llm_route_id` for the same reason — it is an FK to
# `llm_service.id`, and the column name must say WHICH table it points at.
#
# `local` decides HOW the service is provided, NOT whether it is available:
#   local=1 -> on this machine (ollama) -> call it directly
#   local=0 -> the IDE's LLM          -> raise a SERVICE TICKET
# A local=0 provider is not unreachable; it is served by a ticket, which is the
# handoff mechanism that already exists. No new bridge is introduced.
#
# `model_id` references `llm_model.model_id`, NOT `llm_model.id`: the id is an
# internal auto-increment number, the model_id is the stable external name. The
# same rule as `setting_ref_key_not_id`.
#
# `priority` is the pool ORDER (lower = tried first). This is what makes the pool
# a LIST read from the table instead of a hardcoded array.
#
# `provider_kind` — THE USER'S MODEL (2026-09-21): "so thet are same is services
# provide / services provide -> thinking = LLM / services provide -> EYE /
# HAND.... = openclaw". LLM and OpenClaw are BOTH service providers; they differ
# only in the KIND of service. So ONE table carries both, and `provider_kind`
# selects the transport: `thinking` -> `_llm_call()`; `eye`/`hand`/`voice` ->
# the OpenClaw MCP `call_tool()`. Two tables would re-create the split the user
# is asking to remove.
LLM_SERVICE_PROVIDER_DDL = """
CREATE TABLE IF NOT EXISTS llm_route_provider (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    llm_route_id INTEGER NOT NULL,
    model_id    TEXT    NOT NULL,
    local       INTEGER NOT NULL DEFAULT 1 CHECK (local IN (0, 1)),
    priority    INTEGER NOT NULL DEFAULT 100,
    provider_kind TEXT  NOT NULL DEFAULT 'thinking',
    -- THE TRANSPORT. HOW a caller reaches this provider, which is a DIFFERENT
    -- question from WHICH model it is.
    --
    -- MEASURED 2026-09-28: `llm.text`'s pool was
    -- ['convaiinnovations/laya', 'qwen2.5:7b-instruct', 'qwen/qwen3.8-27b'] and
    -- `llm_100_run_harness.resolve_model` returned `pool[0]` = laya. The harness
    -- then called `vision_analyze.complete_text`, which POSTs to Ollama — and
    -- Ollama answered `ollama_http_404: model 'convaiinnovations/laya' not
    -- found`. **The registry was RIGHT and the CALLER was wrong**: laya is a
    -- Python LIBRARY (`import laya`), not an HTTP model, so no Ollama call can
    -- ever reach it.
    --
    -- `http`  = reached over an HTTP API (Ollama, DeepSeek, ...)
    -- `python`= reached by IMPORTING a library in-process (laya)
    -- `cli`   = reached by SPAWNING a command-line program (Cline)
    --
    -- A caller that can only speak HTTP must ask for `http`, so the pool it
    -- receives is one it can actually reach. Without this column the pool is a
    -- list of NAMES with no way to tell which ones the caller can call.
    --
    -- `cli` ADDED 2026-09-29. MEASURED: Cline is a coding agent reached by
    -- running `cline --json ...` as a SUBPROCESS. It is neither an HTTP endpoint
    -- nor an importable library, so neither existing value describes it. The
    -- vocabulary is widened rather than the value being smuggled in as `http`,
    -- because a caller that speaks HTTP would then POST to a program that has no
    -- server — the exact class of defect the `python` value was added to fix.
    transport   TEXT    NOT NULL DEFAULT 'http'
                CHECK (transport IN ('http', 'python', 'cli')),
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (llm_route_id, model_id),
    FOREIGN KEY (llm_route_id) REFERENCES llm_service (id)
);
CREATE INDEX IF NOT EXISTS idx_llm_provider_service
    ON llm_route_provider (llm_route_id, is_active, priority);
CREATE INDEX IF NOT EXISTS idx_llm_provider_model
    ON llm_route_provider (model_id);
"""

# THE ADDITIVE MIGRATION for the column above. `DEFAULT 'http'` is the honest
# default: every provider that existed before this column was reached over HTTP,
# so the default states what was already true rather than inventing a value.
LLM_ROUTE_PROVIDER_NEW_COLUMNS: tuple[tuple[str, str], ...] = (
    ("transport", "TEXT NOT NULL DEFAULT 'http'"),
)


def seed_rbac_defaults(conn: sqlite3.Connection) -> dict:
    """Seed roles (worker/developer/readonly) + a default developer user. Idempotent."""
    roles = {
        "worker": '["skill:get","tasks:write"]',
        "developer": '["skill:get","skill:create","skill:publish","tasks:read","tasks:write"]',
        "readonly": '["skill:get","tasks:read"]',
    }
    role_ids: dict[str, int] = {}
    for name, perms in roles.items():
        cur = conn.execute(
            "INSERT OR IGNORE INTO roles (role_name, permissions) VALUES (?, ?)",
            (name, perms),
        )
        if cur.lastrowid:
            role_ids[name] = int(cur.lastrowid)
        else:
            row = conn.execute(
                "SELECT role_id FROM roles WHERE role_name=?", (name,)
            ).fetchone()
            role_ids[name] = int(row[0])
    # default developer user for testing (token: dev-token-0001)
    dev_role = role_ids.get("developer")
    if dev_role:
        conn.execute(
            "INSERT OR IGNORE INTO users (name, api_token, role_id) VALUES (?, ?, ?)",
            ("dev", "dev-token-0001", dev_role),
        )
    # default worker user for testing (token: worker-token-0001)
    worker_role = role_ids.get("worker")
    if worker_role:
        conn.execute(
            "INSERT OR IGNORE INTO users (name, api_token, role_id) VALUES (?, ?, ?)",
            ("worker", "worker-token-0001", worker_role),
        )
    conn.commit()
    return {"ok": True, "roles": role_ids}


# ---- app catalog + user_asset (DB-driven, id only) ----

# Seeded apps. `process_name` is what tasklist reports; `exe_path` is a
# best-effort default that the runtime may override with a discovered path.
APP_SEED: tuple[dict[str, Any], ...] = (
    {
        "app_key": "openclaw",
        "name": "OpenClaw Companion",
        "kind": "desktop",
        "exe_path": r"C:\Users\user\AppData\Local\OpenClawTray\OpenClaw.Tray.WinUI.exe",
        "process_name": "OpenClaw.Tray.WinUI",
        "mcp_url": "http://127.0.0.1:8765/mcp",
        "description": "Local MCP server (screen/notify/canvas/tts). Gates: EnableMcpServer.",
    },
    {
        "app_key": "vscode",
        "name": "Visual Studio Code",
        "kind": "editor",
        "exe_path": r"C:\Users\user\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        "process_name": "Code",
        "mcp_url": None,
        "description": "IDE target for the vision/mouse adapter.",
    },
    {
        "app_key": "chrome",
        "name": "Google Chrome",
        "kind": "browser",
        "exe_path": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "process_name": "chrome",
        "mcp_url": None,
        "description": "CDP browser automation target.",
    },
    {
        "app_key": "ollama",
        "name": "Ollama",
        "kind": "service",
        "exe_path": None,
        "process_name": "ollama",
        "mcp_url": "http://127.0.0.1:18803",
        "description": "Local LLM runtime (qwen2.5vl:7b vision, qwen2.5:7b-instruct text).",
    },
)


def seed_app_defaults(conn: sqlite3.Connection) -> dict:
    """Seed the app catalog. Idempotent (INSERT OR IGNORE on app_key)."""
    added = 0
    for a in APP_SEED:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO app
                (app_key, name, kind, exe_path, process_name, mcp_url, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                a["app_key"], a["name"], a["kind"], a["exe_path"],
                a["process_name"], a["mcp_url"], a["description"],
            ),
        )
        added += int(cur.rowcount or 0)
    conn.commit()
    return {"ok": True, "added": added}


# ---- llm_model: the LLM MODEL registry (DB-driven, id only) ----
# DEFECT FOUND BY MEASURING IT (2026-09-21): this table EXISTED but had NO DDL
# here and NO seed anywhere. Measured: `CREATE TABLE llm_model` -> 0 matches,
# `LLM_MODEL_DDL` -> 0 matches, `INSERT INTO llm_model` -> only 2 proof
# fixtures. The live DDL shows `, model_id TEXT` appended AFTER `created_at`,
# i.e. an ALTER — so the 4 rows were inserted BY HAND. There was no registration
# path at all.
#
# OpenClaw was registered BETTER than the LLM: `app` has APP_DDL + APP_SEED +
# seed_app_defaults, `user_asset` tracks online/offline, and `module_registry`
# has `openclaw_companion`. The LLM had an `app` row (`ollama`) but no module and
# no declared model table. The user's rule: "so thet are same is services
# provide" — both are service providers, so both need the same registration path.
#
# `model_id` is the STABLE EXTERNAL NAME and is what `llm_route_provider`
# references; `id` is an internal auto-increment number. Same rule as
# `setting_ref_key_not_id`.
LLM_MODEL_DDL = """
CREATE TABLE IF NOT EXISTS llm_model (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    model_id    TEXT    NOT NULL UNIQUE,
    visual      INTEGER NOT NULL DEFAULT 0 CHECK (visual IN (0, 1)),
    text        INTEGER NOT NULL DEFAULT 0 CHECK (text IN (0, 1)),
    local       INTEGER NOT NULL DEFAULT 0 CHECK (local IN (0, 1)),
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    description TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# The models seeded on first run. A SEED, not the source of truth: the read path
# is `SELECT ... FROM llm_model`. `local=1` = on this machine (ollama);
# `local=0` = reached through the IDE.
LLM_MODEL_SEED: tuple[dict[str, Any], ...] = (
    {
        "model_id": "qwen2.5:7b-instruct",
        "name": "Qwen2.5 7B",
        "text": 1, "visual": 0, "local": 1,
        "description": "Local text model. Zero token cost, 24/7.",
    },
    {
        "model_id": "qwen2.5vl:7b",
        "name": "Qwen2.5 7B-vl",
        "text": 0, "visual": 1, "local": 1,
        "description": "Local vision model. Zero token cost, 24/7.",
    },
    {
        "model_id": "qwen/qwen3.8-27b",
        "name": "Qwen 3.8 27B",
        "text": 1, "visual": 1, "local": 0,
        "description": "Reached through the IDE. Token cost applies.",
    },
    {
        "model_id": "deepseek/deepseek-v4-flash-0731",
        "name": "DeepSeek V4.1 Flash 0731",
        "text": 1, "visual": 0, "local": 0,
        "description": "Reached through the IDE. Token cost applies.",
    },
)


def seed_llm_model_defaults(conn: sqlite3.Connection) -> dict:
    """Seed the LLM model registry. Idempotent (INSERT OR IGNORE on model_id).

    A SEED, not the source of truth — the same rule as `APP_SEED` and
    `CAPABILITY_TAGS`. An existing row is LEFT ALONE, so a human-edited
    description is never clobbered by a restart.

    ADDITIVE MIGRATION: the live table predates this DDL and lacks `model_id`,
    `is_active` and `description`. `CREATE TABLE IF NOT EXISTS` does NOT add
    columns to an existing table, so they are applied explicitly — the same
    pattern `_add_columns_if_missing` uses elsewhere. Without this the seed
    would fail with "no such column", which is exactly what happened first.
    """
    conn.executescript(LLM_MODEL_DDL)
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(llm_model)")}
    for col, decl in (("model_id", "TEXT"),
                      ("is_active", "INTEGER NOT NULL DEFAULT 1"),
                      ("description", "TEXT"),
                      ("updated_at", "TIMESTAMP")):
        if col not in cols:
            conn.execute("ALTER TABLE llm_model ADD COLUMN %s %s" % (col, decl))
    # Backfill model_id for a legacy row that has none, from its name, so the
    # UNIQUE index below can be created. A row with no model_id cannot be
    # referenced by `llm_route_provider`.
    conn.execute("UPDATE llm_model SET model_id = name WHERE model_id IS NULL "
                 "OR TRIM(model_id) = ''")
    # 🔴 THE EXPLICIT UNIQUE INDEX WAS REMOVED 2026-09-29 (RING 5, D3).
    #
    # MEASURED: `LLM_MODEL_DDL` already declares `model_id TEXT NOT NULL UNIQUE`,
    # which creates an automatic unique index, and THIS line created a SECOND one
    # on the same column. A FRESH build therefore carried TWO unique indexes on
    # `model_id` where LIVE carried one — a shape difference produced by a
    # statement that added nothing. The inline `UNIQUE` is kept (it is part of
    # the declared column) and the redundant index is gone.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_model_active "
                 "ON llm_model (is_active, local)")
    added = 0
    for m in LLM_MODEL_SEED:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO llm_model
                (model_id, name, text, visual, local, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (m["model_id"], m["name"], m["text"], m["visual"], m["local"],
             m["description"]),
        )
        added += int(cur.rowcount or 0)
    conn.commit()
    return {"ok": True, "added": added,
            "total": conn.execute("SELECT COUNT(*) FROM llm_model"
                                  ).fetchone()[0]}


# ---- capability_kind: the KIND of service a capability provides ----
# The user's model (2026-09-21): "so thet are same is services provide /
# services provide -> thinking = LLM / services provide -> EYE / HAND.... =
# openclaw". LLM and OpenClaw are BOTH service providers; they differ only in
# the KIND of service. So the kind is a REGISTERED vocabulary, not free text —
# a typo would match zero capabilities SILENTLY, the same defect the tag
# registry exists to prevent.
CAPABILITY_KIND_DDL = """
CREATE TABLE IF NOT EXISTS capability_kind_registry (
    kind_key    TEXT    PRIMARY KEY,
    definition  TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

CAPABILITY_KINDS: tuple[tuple[str, str], ...] = (
    ("thinking", "The provider REASONS: it reads text or an image and returns a "
                 "judgement. Served by an LLM."),
    ("eye", "The provider CAPTURES: it returns pixels of the screen or camera. "
            "Served by OpenClaw (screen.snapshot / camera.snap)."),
    ("hand", "The provider ACTS: it runs a command or drives another "
             "application. Served by OpenClaw (system.run)."),
    ("voice", "The provider SPEAKS or LISTENS: it shows a notification or uses "
              "the microphone. Served by OpenClaw (system.notify / tts / stt)."),
    ("code", "The provider produces or gates SOURCE CODE."),
)

# ---- capability_tool: WHICH tools provide a capability ----
# DEFECT FOUND BY MEASURING IT (2026-09-21): the capability -> tool mapping
# existed in FOUR places and they DISAGREED.
#
#   1. `openclaw_settings.CAPABILITIES` (line 196)  9 capabilities, merged screen
#   2. `capability_registry` (this DB)              6 capabilities, NO tools
#   3. `llm_service_store.MCP_TOOL_FOR_CAPABILITY`  6 capabilities, 1 tool each
#   4. `openclaw_mcp_trace.TOOL_SPECS`              4 tools (a trace spine)
#
# Copies 2 and 3 BOTH split screen into `screen_capture` + `screen_record`;
# copy 1 MERGED them. So "read the DB instead of the literal" would have
# silently MERGED two capabilities and DROPPED four (Canvas, App control, Chat,
# Location). That is a MIGRATION, not a read-path swap.
#
# THE DECLARATION / OBSERVATION SPLIT is the design rule:
#   DECLARATION (stored here): name, tools, probe, gate, why
#   OBSERVATION (computed, NEVER stored): state, in_server, note
# `state` is working/failing/present/missing — a MEASUREMENT. Storing it would
# make a stale reading look like a fact.
#
# `is_probe_safe` lives HERE, per tool, because the ALLOWLIST is a declaration
# about a tool, not a property of the capability. It is the same rule as
# `openclaw_settings.PROBE_SAFE_TOOLS`, moved to the table so a new tool is a
# ROW rather than a code change.
CAPABILITY_TOOL_DDL = """
CREATE TABLE IF NOT EXISTS capability_tool (
    capability_tool_id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id      INTEGER NOT NULL,
    tool_name          TEXT    NOT NULL,
    is_probe_safe      INTEGER NOT NULL DEFAULT 0 CHECK (is_probe_safe IN (0, 1)),
    sort_order         INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (capability_id, tool_name),
    FOREIGN KEY (capability_id) REFERENCES capability_registry (capability_id)
);
CREATE INDEX IF NOT EXISTS idx_capability_tool_capability
  ON capability_tool (capability_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_capability_tool_name
  ON capability_tool (tool_name);
"""

# The three DECLARATION columns `capability_registry` was missing. All
# `NOT NULL DEFAULT 'NA'` per the no_null standard, so a surviving NULL means
# the standardiser did not run, which is a defect.
CAPABILITY_DECLARATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("gate_ref", "TEXT NOT NULL DEFAULT 'NA'"),
    ("why", "TEXT NOT NULL DEFAULT 'NA'"),
    ("probe_tool", "TEXT NOT NULL DEFAULT 'NA'"),
)


def seed_capability_kind_defaults(conn: sqlite3.Connection) -> dict:
    """Seed the capability-kind vocabulary. Idempotent; never overwrites."""
    conn.executescript(CAPABILITY_KIND_DDL)
    added = 0
    for kind_key, definition in CAPABILITY_KINDS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO capability_kind_registry (kind_key, "
            "definition) VALUES (?,?)", (kind_key, definition))
        added += int(cur.rowcount or 0)
    conn.commit()
    return {"ok": True, "added": added,
            "kinds": [str(r[0]) for r in conn.execute(
                "SELECT kind_key FROM capability_kind_registry "
                "WHERE is_active=1 ORDER BY kind_key")]}


# ---- source catalog (Chat Center Setting "from" dropdown) ----
# Seeded sources. `url` is blank where the source has no page (VS Code, 豆包);
# `hotkey` records the tool that copies from that source (Chrome -> F9).
SOURCE_SEED: tuple[dict[str, Any], ...] = (
    {
        "source_key": "vscode",
        "name": "VS Code",
        "kind": "IDE",
        "url": None,
        "hotkey": None,
        "description": "VS Code chat panel (IDE source).",
    },
    {
        "source_key": "doubao",
        "name": "豆包",
        # A BROWSER, NOT AN APP. THE HUMAN (2026-09-26): "豆包 is a **desktop
        # app** is past !! now is 豆包 is a browser!! ... and she has it own
        # offical browser too". MEASURED: the install carries the Chromium
        # runtime (`Doubao_browser_proxy.exe`, `libEGL.dll`, `libGLESv2.dll`,
        # `resources.pak`, `vk_swiftshader.dll`, `locales/`), so 豆包 is a
        # Chromium-based browser. `kind` is the CATEGORY (Browser), and
        # `source_kind` is HOW you reach it (URL).
        "kind": "BROWSER",
        "url": "https://www.doubao.com/chat/",
        "hotkey": None,
        "description": "豆包 -- a Chromium-based BROWSER (its own official "
                       "browser), reached by URL.",
    },
    {
        "source_key": "chrome_deepseek",
        "name": "Google Chrome",
        "kind": "BROWSER",
        "url": "https://chat.deepseek.com/a/chat/s/7e589851-81bf-4e63-a3b6-205a3ee6a7fd",
        "hotkey": "F9",
        "description": "Chrome -> DeepSeek (CDP copy, F9).",
    },
    {
        "source_key": "edge_gemini",
        "name": "Microsoft Edge",
        "kind": "BROWSER",
        "url": "https://gemini.google.com/app",
        "hotkey": None,
        "description": "Edge -> Gemini (BROWSER source).",
    },
)


def seed_source_defaults(conn: sqlite3.Connection) -> dict:
    """Seed the source catalog. Idempotent (INSERT OR IGNORE on source_key).

    Also applies the additive columns the 6-STEP capture flow needs and backfills
    `source_kind` for rows seeded before the column existed. The backfill only
    touches rows where source_kind is NULL, so a user's own choice is never
    overwritten.
    """
    added = 0
    for s in SOURCE_SEED:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO source
                (source_key, name, kind, url, hotkey, description)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                s["source_key"], s["name"], s["kind"],
                s["url"], s["hotkey"], s["description"],
            ),
        )
        added += int(cur.rowcount or 0)
    # Additive columns (an old DB predates source_kind / app_id / instruction_limit)
    cols_added = _add_columns_if_missing(conn, "source", SOURCE_NEW_COLUMNS)
    # Backfill source_kind only where it is still NULL.
    backfilled = 0
    if _table_exists(conn, "source"):
        for key, kind in SOURCE_KIND_BACKFILL.items():
            cur = conn.execute(
                "UPDATE source SET source_kind = ? "
                "WHERE source_key = ? AND (source_kind IS NULL OR source_kind = '')",
                (kind, key),
            )
            backfilled += int(cur.rowcount or 0)
    conn.commit()
    return {"ok": True, "added": added, "columns_added": cols_added,
            "source_kind_backfilled": backfilled}


# ---- flow_setting seed (Chat Center flow) ----
# The identity flow: paste the identity block, then confirm it was received.
# `action` is the hook the API will run later; `next_step` chains the steps.
FLOW_SETTING_SEED: tuple[dict[str, Any], ...] = (
    {
        "flow_key": "chat_center_identity",
        "step_no": 1,
        "question": "who a u?",
        "value": "worker_identity",
        "next_step": 2,
        "action": "paste_template",
        "description": "Ask the model who it is; paste the worker_identity block.",
    },
    {
        "flow_key": "chat_center_identity",
        "step_no": 2,
        "question": "confirm the identity block you received",
        "value": "worker_identity_confirm",
        "next_step": None,
        "action": "paste_template",
        "description": "Ask the model to echo the identity block back (CONFIRM YES/NO).",
    },
)


def seed_flow_setting_defaults(conn: sqlite3.Connection) -> dict:
    """Seed the flow table. Idempotent (INSERT OR IGNORE on flow_key+step_no)."""
    added = 0
    for f in FLOW_SETTING_SEED:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO flow_setting
                (flow_key, step_no, question, value, next_step, action, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f["flow_key"], f["step_no"], f["question"], f["value"],
                f["next_step"], f["action"], f["description"],
            ),
        )
        added += int(cur.rowcount or 0)
    conn.commit()
    return {"ok": True, "added": added}


def resolve_or_registry_user(
    conn: sqlite3.Connection,
    name: str,
    *,
    role_name: str = "worker",
    auto_registry: bool = True,
) -> int | None:
    """Resolve a username to users.user_id, registering it if needed.

    WHY: user_environment.user_id used to store the Windows username (an
    alias). The DB must be driven by id only, so the alias is resolved to a
    real users row here. Returns the integer user_id, or None when the name is
    empty or auto_registry is off and no row exists.
    """
    nm = (name or "").strip()
    if not nm:
        return None
    row = conn.execute(
        "SELECT user_id FROM users WHERE name = ? LIMIT 1", (nm,)
    ).fetchone()
    if row:
        return int(row[0])
    if not auto_registry:
        return None
    role = conn.execute(
        "SELECT role_id FROM roles WHERE role_name = ? LIMIT 1", (role_name,)
    ).fetchone()
    if not role:
        return None
    # api_token is NOT NULL UNIQUE; derive a deterministic placeholder so a
    # re-run cannot create a duplicate and the value is traceable to the name.
    token = "auto-" + hashlib.sha1(nm.encode("utf-8", "replace")).hexdigest()[:24]
    cur = conn.execute(
        "INSERT OR IGNORE INTO users (name, api_token, role_id) VALUES (?, ?, ?)",
        (nm, token, int(role[0])),
    )
    if cur.rowcount:
        return int(cur.lastrowid)
    row = conn.execute(
        "SELECT user_id FROM users WHERE name = ? LIMIT 1", (nm,)
    ).fetchone()
    return int(row[0]) if row else None


def _migrate_user_environment_user_id_int(conn: sqlite3.Connection) -> dict:
    """Convert user_environment.user_id from a TEXT alias to an INTEGER id.

    Legacy rows stored the Windows username. The DB is now id-driven, so:
      1. add a unique index on users.name (needed to resolve an alias safely)
      2. resolve every distinct legacy user_id value to a users row
      3. rebuild the table with `user_id INTEGER` + FK to users(user_id)

    CREATE TABLE IF NOT EXISTS cannot change a column's declared type, so the
    table is rebuilt (same approach as _rebuild_chat_id_int_affinity).
    """
    info: dict[str, Any] = {"rebuilt": False, "resolved": {}, "unresolved": []}
    if not _table_exists(conn, "user_environment"):
        return info
    # 1. users.name must be unique for alias resolution to be deterministic.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name ON users (name)"
    )
    # Already migrated?
    decl = ""
    for r in conn.execute("PRAGMA table_info(user_environment)").fetchall():
        if r[1] == "user_id":
            decl = str(r[2] or "").strip().upper()
            break
    if decl == "INTEGER":
        conn.commit()
        return info
    # 2. Resolve legacy aliases -> ids.
    # A value that is already all-digits is an id, not an alias: a previous
    # partial run may have written ids into the TEXT column. Treating those as
    # aliases would register a bogus user literally named "1452".
    legacy = [
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT user_id FROM user_environment "
            "WHERE user_id IS NOT NULL AND TRIM(user_id) <> ''"
        ).fetchall()
    ]
    mapping: dict[str, int] = {}
    for alias in legacy:
        if alias.isdigit():
            continue
        uid = resolve_or_registry_user(conn, alias)
        if uid is None:
            info["unresolved"].append(alias)
        else:
            mapping[alias] = uid
    info["resolved"] = mapping
    # 3. Rebuild with INTEGER affinity + FK.
    cols = _table_columns(conn, "user_environment")
    conn.execute("DROP TABLE IF EXISTS _user_environment_old")
    conn.execute("ALTER TABLE user_environment RENAME TO _user_environment_old")
    conn.executescript(USER_ENVIRONMENT_DDL)
    base = [
        "id", "user_id", "ip_address", "computer_id", "computer_name",
        "os_name", "os_version", "python_version", "timezone", "tz_offset_sec",
        "country", "country_code", "region", "city", "isp", "language",
        "locale", "detail", "source", "created_at", "updated_at",
    ]
    shared = [c for c in base if c in cols]
    conn.execute(
        f"INSERT INTO user_environment ({', '.join(shared)}) "
        f"SELECT {', '.join(shared)} FROM _user_environment_old"
    )
    # Rewrite the alias values to ids.
    for alias, uid in mapping.items():
        conn.execute(
            "UPDATE user_environment SET user_id = ? WHERE user_id = ?",
            (uid, alias),
        )
    # Anything unresolvable becomes NULL rather than a dangling alias.
    if info["unresolved"]:
        conn.execute(
            "UPDATE user_environment SET user_id = NULL "
            "WHERE user_id IS NOT NULL AND CAST(user_id AS INTEGER) = 0"
        )
    conn.execute("DROP TABLE _user_environment_old")
    conn.commit()
    info["rebuilt"] = True
    return info


def insert_chat_reply(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    task_id: str | None = None,
    chat_id: str | None = None,
    model: str | None = None,
    reply_text: str | None = None,
    source: str = "agent",
    sent_at: str | None = None,
    elapsed_ms: int | None = None,
) -> int:
    """Insert one chat_reply_log row. Returns row id.

    sent_at / elapsed_ms record the send-out time and how many ms the
    reply took (elapsed_ms = finished_at - sent_at).
    """
    cur = conn.execute(
        """
        INSERT INTO chat_reply_log
            (session_id, task_id, chat_id, model, reply_text, source,
             sent_at, elapsed_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(session_id),
            None if task_id in (None, "null", "") else str(task_id),
            None if chat_id in (None, "null", "") else str(chat_id),
            model,
            reply_text,
            source,
            sent_at,
            elapsed_ms,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


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
        # `table` is a PARAMETER: prove it before it reaches the SQL.
        table = _safe_table_name(conn, table)
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

    # The task-type ROOTS. Seeded only when the table is EMPTY, so a human
    # DELETE is not silently undone — the same rule as `entity_type_registry`.
    # The CHILDREN are deliberately NOT seeded: the user said the depth is
    # unknown, so inventing `project_name` rows would fabricate a taxonomy.
    if conn.execute("SELECT COUNT(*) FROM task_type_registry").fetchone()[0] == 0:
        for type_key, definition, cite_ref in TASK_TYPE_SEED:
            conn.execute(
                "INSERT INTO task_type_registry "
                "(parent_type_id, type_key, type_path, depth, definition, "
                " cite_ref, is_active) "
                "VALUES (NULL, ?, ?, 0, ?, ?, 1)",
                (type_key, type_key, definition, cite_ref))
            out["task_types"] = out.get("task_types", 0) + 1

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

    # Seed the proof-gate settings (idempotent).
    #
    # WHY A SETTING AND NOT A CONSTANT (2026-09-25): the right per-proof timeout
    # depends on the machine and on how many proofs run at once, and whether the
    # screen-dependent family should be excluded depends on whether a screen is
    # available. A constant would need a CODE change to tune; a setting is a row.
    # The gate reads these with `get_setting()` and an env var still overrides.
    try:
        for key, value in (
            ("proof_gate.timeout_sec", "180"),
            ("proof_gate.skip_screen_dependent", "1"),
        ):
            conn.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, value),
            )
        out.setdefault("settings", {})
        out["settings"]["proof_gate.timeout_sec"] = 180
        out["settings"]["proof_gate.skip_screen_dependent"] = 1
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


def get_dev_tasks(
    conn: sqlite3.Connection | None = None,
    *,
    db_path: Path | str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Read dev_task rows for the Task Center dashboard.

    Returns list[dict] with keys: id, task_id (task_label), title, status,
    current_model, qc_summary, error, session_id, created_at, updated_at.
    Optional status filter and limit.
    """
    own = False
    if conn is None:
        conn = sqlite3.connect(get_db_path(db_path))
        conn.row_factory = sqlite3.Row
        own = True
    try:
        _add_columns_if_missing(conn, "dev_task", DEV_TASK_NEW_COLUMNS)
        sql = (
            "SELECT id, task_label, title, status, current_model, qc_summary, "
            "error, session_id, created_at, updated_at "
            "FROM dev_task"
        )
        args: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            args.append(status)
        sql += " ORDER BY id DESC"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            if isinstance(r, sqlite3.Row):
                d = dict(r)
            else:
                d = {
                    "id": r[0], "task_id": r[1], "title": r[2], "status": r[3],
                    "current_model": r[4], "qc_summary": r[5], "error": r[6],
                    "session_id": r[7], "created_at": r[8], "updated_at": r[9],
                }
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


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
        # `table` is a PARAMETER: prove it before it reaches the SQL.
        table = _safe_table_name(conn, table)
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
    # THE WRITE-SITE STANDARDISER (measured defect 2026-09-22). This INSERT used
    # to omit `error` entirely, so every new row carried NULL — and the NULLs a
    # backfill had removed came back (160 -> 0 -> 6). `error` is a DETAIL OF A
    # CONDITION recorded in the same row (`status`), so its empty state is `NA`,
    # not NULL. Going through `insert_row` makes that automatic instead of
    # something a caller has to remember.
    import no_null as _nn
    cur_id = _nn.insert_row(conn, "dev_task", {
        "parent_task_id": parent_task_id,
        "channel_id": channel_id,
        "module_id": module_id,
        "action_name_id": action_name_id,
        "version_id": version_id,
        "task_label": task_label,
        "title": title,
        "payload_json": payload_text,
        "status": status,
        "writer": writer_text,
        "session_id": session_text,
        "error": None,
    })
    conn.commit()
    return {
        "id": int(cur_id),
        "parent_task_id": parent_task_id,
        "task_label": task_label,
        "title": title,
        "status": status,
        "version_id": version_id,
        "writer": writer_text,
        "session_id": session_text,
    }


def find_task_by_label(conn: sqlite3.Connection, task_label: str) -> dict | None:
    """Find a dev_task by task_label (any version/channel). Latest id wins."""
    row = conn.execute(
        """
        SELECT id, task_label, title, status, channel_id, module_id,
               action_name_id, version_id, writer, session_id, updated_at
        FROM dev_task
        WHERE task_label = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        ((task_label or "").strip(),),
    ).fetchone()
    return dict(row) if row else None


def find_or_create_task_by_label(
    conn: sqlite3.Connection,
    *,
    task_label: str,
    title: str,
    channel_id: int = 1,
    module_id: int = 6,
    version_id: int = 1,
    action_code: str = "llm.work",
    payload_json: str | dict | None = None,
    status: str = "pending",
    writer: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Find dev_task by label (any version); else create under (channel, module, version).

    Returns {task_id, task_label, title, status, created: bool}.
    """
    existing = find_task_by_label(conn, task_label)
    if existing:
        return {**existing, "created": False}
    action_id = _action_id_by_code(conn, action_code)
    row = create_dev_task(
        conn,
        channel_id=channel_id,
        module_id=module_id,
        version_id=version_id,
        action_name_id=action_id,
        task_label=task_label,
        title=title,
        payload_json=payload_json,
        status=status,
        writer=writer,
        session_id=session_id,
    )
    return {**row, "created": True}


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
    # `table` is a PARAMETER: prove it before interpolating.
    table = _safe_table_name(conn, table)
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
        # `table` is a PARAMETER: prove it before it reaches the SQL.
        table = _safe_table_name(conn, table)
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
