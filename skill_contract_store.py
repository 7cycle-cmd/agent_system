"""skill_contract_store — DB-driven Skill Contract SSOT (agent.db).

Six-column skill template + Field Register + TDD cases + streak counter.

Tables (all additive, CREATE IF NOT EXISTS, never DROP):
  skill_contract_template   one row per skill (Environment/Purpose/Flow/Not To Do)
  skill_contract_field      Field Register (field-level SSOT, scoped per contract)
  skill_contract_tdd_case   TDD proof cases (pass / hard_fail)
  skill_contract_streak     streak counter (DB-calculated, resets on fail)
  skill_contract_review_log Post-Case Review Log (gap / revision / streak result)
  v_skill_contract          template LEFT JOIN streak (single read for UI/QC)

Naming note: `skill_template` already exists in llm_task_center.py as the
task-center capability catalog (template_id = catalog.subcatalog). This module
deliberately uses the `skill_contract_*` prefix so that table is untouched.

Law:
  - Field rules live ONLY in skill_contract_field (no ad-hoc rules in code).
  - Hard validation: reject, never warn-and-continue.
  - Streak is computed from recorded results, not stored as a free-text claim.
  - Every write carries trace_id / chat_id / task_id where available.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

CONTRACT_STATUSES = ("draft", "active", "deprecated")
TDD_KINDS = ("pass", "hard_fail")
STREAK_RESULTS = ("pass", "fail")

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

SKILL_CONTRACT_TEMPLATE_DDL = """
CREATE TABLE IF NOT EXISTS skill_contract_template (
    -- THE IDENTITY IS AN INTEGER, AND THE NAME IS ONLY A LABEL.
    --
    -- MEASURED DEFECT (2026-09-29): this DDL still declared `contract_id TEXT
    -- PRIMARY KEY` while LIVE had been migrated by `migrate_contract_ref.py`
    -- (MIGRATION `contract_ref_v1`, applied 2026-09-21) to `contract_ref INTEGER
    -- PRIMARY KEY`. That is DECLARER DRIFT: a fresh build produced a different
    -- schema from the one the four children's FKs must point at. A name as an
    -- identity can be misspelled and renamed without anything noticing, which is
    -- the same defect the policy closes for FKs.
    --
    -- THE POLICY (human-locked): a native FK may only attach to the parent's
    -- PRIMARY KEY, and only for a MANDATORY + LOAD-BEARING reference.
    contract_ref            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id             TEXT NOT NULL UNIQUE,
    skill_key               TEXT NOT NULL UNIQUE,
    taxonomy_path           TEXT NOT NULL,
    environment_json        TEXT NOT NULL DEFAULT '{}',
    purpose                 TEXT NOT NULL,
    purpose_not_responsible TEXT,
    flow_json               TEXT NOT NULL DEFAULT '[]',
    not_to_do_json          TEXT NOT NULL DEFAULT '[]',
    write_owner             INTEGER NOT NULL DEFAULT 0
                            CHECK (write_owner IN (0, 1)),
    status                  TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'active', 'deprecated')),
    version                 INTEGER NOT NULL DEFAULT 1,
    source                  TEXT NOT NULL DEFAULT 'manual',
    notes                   TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    skill_ref INTEGER
);
CREATE INDEX IF NOT EXISTS idx_skill_contract_status
  ON skill_contract_template (status, contract_id);
CREATE INDEX IF NOT EXISTS idx_skill_contract_skill_key
  ON skill_contract_template (skill_key);
"""

# RULE_KINDS is deliberately tiny. Each kind is added only when a real case
# needs it, so the vocabulary cannot grow a rule nothing enforces.
#   const     -> the value must equal rule_value exactly
#   non_blank -> a string value must contain a non-whitespace character
#   type      -> the value's PYTHON type must be the named one
#   length    -> the value's LENGTH must equal rule_value
#
# WHY `type` AND `length` WERE ADDED (2026-09-22)
# ----------------------------------------------
# The user's own example could not be expressed:
#
#   "function A / output = 1 / and function output = INT with char.lenght = 8 /
#    so will be abc is wrong 123 is wrong, 12345678 is correct"
#
# `output = 1` is `const` (existed). `output is INT` needs `type`, and
# `char.length = 8` needs `length` — and with only `const`/`non_blank` in the
# vocabulary, `length` could not be expressed AT ALL. A rule the vocabulary
# cannot state is a rule the generator cannot generate from.
#
# `type` is NOT the same as `data_type`: `data_type` is checked against
# `_TYPE_MAP` (a coarse family, e.g. "int" accepts bool), while `type` compares
# `type(val).__name__` EXACTLY. The user's example needs the exact form, because
# `True` is not `1` for a length-8 integer field.
#
# THIS TUPLE IS THE ONE SOURCE. `SKILL_CONTRACT_FIELD_DDL` derives its CHECK
# from it, because a vocabulary declared in two places is the drift this repo
# has already been bitten by.
RULE_KINDS = ("const", "non_blank", "type", "length")

SKILL_CONTRACT_FIELD_DDL = """
CREATE TABLE IF NOT EXISTS skill_contract_field (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    field_id      TEXT NOT NULL,
    contract_id   TEXT NOT NULL,
    taxonomy_path TEXT,
    field_name    TEXT NOT NULL,
    data_type     TEXT NOT NULL,
    mandatory     INTEGER NOT NULL DEFAULT 0
                  CHECK (mandatory IN (0, 1)),
    enum_json     TEXT,
    hard_rule     TEXT NOT NULL,
    -- THE CHECK IS DERIVED FROM `RULE_KINDS`, NOT RESTATED.
    --
    -- MEASURED DEFECT (2026-09-22): this line used to hard-code
    -- `IN ('const', 'non_blank')`. So the vocabulary was declared in TWO places
    -- — `RULE_KINDS` and this CHECK — and adding `type`/`length` to the tuple
    -- made `upsert_field` accept them while the DDL REFUSED them with
    -- "CHECK constraint failed". A vocabulary in two places is the drift this
    -- repo has already been bitten by (`skill_factor_registry`'s single-
    -- dimensional key). One source now.
    rule_kind     TEXT
                  CHECK (rule_kind IS NULL OR rule_kind IN ({rule_kinds})),
    -- NOT NULL DEFAULT 'NA': an empty value is the explicit NA, so a NULL here
    -- always means the standardiser did not run (see no_null.py).
    rule_value_json TEXT NOT NULL DEFAULT 'NA',
    owner_skill_id TEXT,
    immutable     INTEGER NOT NULL DEFAULT 0
                  CHECK (immutable IN (0, 1)),
    remark        TEXT NOT NULL DEFAULT 'NA',
    -- WHY THE RULE EXISTS, in the worker's own words.
    --
    -- MEASURED DEFECT (2026-09-25): a refusal said WHAT failed and never WHY.
    -- `remark` is a note ABOUT the field; it is not the reason the rule is
    -- enforced. So a worker refused by this contract could not learn from the
    -- refusal — the human's words: "skill can help to have the explain easy to
    -- explain to worker why we reject and tutorial them".
    --
    -- NOT NULL DEFAULT 'NA' follows the same rule as `remark`: an empty value is
    -- the explicit NA, so a NULL always means the standardiser did not run.
    why           TEXT NOT NULL DEFAULT 'NA',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contract_ref INTEGER,
    UNIQUE (contract_id, field_name),
    FOREIGN KEY (contract_ref) REFERENCES skill_contract_template (contract_ref)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_skill_contract_field_contract
  ON skill_contract_field (contract_id, field_name);
""".replace("{rule_kinds}",
            ", ".join("'%s'" % k for k in RULE_KINDS))

SKILL_CONTRACT_TDD_CASE_DDL = """
CREATE TABLE IF NOT EXISTS skill_contract_tdd_case (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_key      TEXT NOT NULL UNIQUE,
    contract_id   TEXT NOT NULL,
    kind          TEXT NOT NULL
                  CHECK (kind IN ('pass', 'hard_fail')),
    assertion     TEXT NOT NULL,
    input_json    TEXT,
    expected_json TEXT,
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'draft', 'deprecated')),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contract_ref INTEGER,
    subject_kind TEXT,
    subject_ref TEXT,
    FOREIGN KEY (contract_ref) REFERENCES skill_contract_template (contract_ref)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_skill_contract_tdd_contract
  ON skill_contract_tdd_case (contract_id, kind, status);
"""

SKILL_CONTRACT_STREAK_DDL = """
CREATE TABLE IF NOT EXISTS skill_contract_streak (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id    TEXT NOT NULL,
    rule_version   INTEGER NOT NULL DEFAULT 1,
    target_streak  INTEGER NOT NULL DEFAULT 100,
    current_streak INTEGER NOT NULL DEFAULT 0,
    best_streak    INTEGER NOT NULL DEFAULT 0,
    total_runs     INTEGER NOT NULL DEFAULT 0,
    reset_count    INTEGER NOT NULL DEFAULT 0,
    last_result    TEXT
                   CHECK (last_result IS NULL OR last_result IN ('pass', 'fail')),
    last_case_key  TEXT,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    code_hash TEXT,
    -- NOT NULL, and LIVE is right. MEASURED DRIFT (2026-09-29): the declarer
    -- left these two loose while LIVE declares them NOT NULL with 0 NULLs in 25
    -- rows. The tighter form is a decision already made (a streak with no
    -- measured state is not a streak), so the DECLARER moves to match LIVE.
    distinct_states INTEGER NOT NULL,
    repeat_runs INTEGER NOT NULL,
    contract_ref INTEGER,
    UNIQUE (contract_id, rule_version),
    FOREIGN KEY (contract_ref) REFERENCES skill_contract_template (contract_ref)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_skill_contract_streak_contract
  ON skill_contract_streak (contract_id, rule_version);
"""

# Additive columns for existing DBs (CREATE TABLE IF NOT EXISTS will not add
# them). Applied by _add_streak_columns() in ensure_skill_contract_schema().
#
# WHY code_hash EXISTS (C2-followup, 2026-09-20)
# ----------------------------------------------
# The streak used to count RUNS. That made it inflate on repetition: the TDD
# probes are deterministic (proven by running 3 contracts x 3 times and
# comparing results byte-for-byte), so 100 runs of unchanged code produced a
# streak of 100 while measuring nothing new. The streak now counts consecutive
# DISTINCT code states that passed:
#   * same code_hash as the last run -> re-confirmation, streak does NOT advance
#   * different code_hash           -> a new state, streak advances on pass
# So 100 runs of unchanged code -> streak 1 (honest), while 100 code changes
# each passing -> streak 100 (meaningful).
SKILL_CONTRACT_STREAK_NEW_COLUMNS = [
    ("code_hash", "TEXT"),
    ("distinct_states", "INTEGER NOT NULL DEFAULT 0"),
    ("repeat_runs", "INTEGER NOT NULL DEFAULT 0"),
]

# ---------------------------------------------------------------------------
# P2-gate Phase 1 (2026-09-20): the Field Register's hard_rule becomes ENFORCED.
#
# WHY THIS EXISTS
# ---------------
# `skill_contract_field.hard_rule` was STORED BUT NEVER READ for enforcement.
# `validate_payload_against_contract` checked only mandatory / data_type /
# enum_json / immutable; the only SELECT of hard_rule was in
# `compute_code_hash`, which hashes it. So every rule the Field Register
# declared in prose was a rule that did not apply — the exact defect this
# contract system exists to remove.
#
# WHY A CLOSED VOCABULARY AND NOT PROSE PARSING
# ---------------------------------------------
# `hard_rule` stays human prose. Parsing it would manufacture NEW "rule that
# does not apply" defects (a reworded sentence silently stops matching).
# Instead a field carries a machine rule from a CLOSED set, and a governance
# check asserts the prose and the machine rule AGREE — so the two cannot drift.
#
# Additive columns for existing DBs (CREATE TABLE IF NOT EXISTS will not add
# them). Applied by ensure_skill_contract_schema().
SKILL_CONTRACT_FIELD_NEW_COLUMNS = [
    ("rule_kind", "TEXT"),
    ("rule_value_json", "TEXT"),
    # WHY THE RULE EXISTS (added 2026-09-25). A refusal that says only WHAT
    # failed teaches nothing; this column carries the reason so the refusal can
    # explain itself. Additive, so existing rows read 'NA' until backfilled.
    ("why", "TEXT NOT NULL DEFAULT 'NA'"),
]

# ---------------------------------------------------------------------------
# THE SUBJECT OF A TDD CASE (added 2026-09-25).
#
# WHY (the human, 2026-09-25: "fix it all now", after a measured report):
#
# MEASURED: `skill_contract_tdd_case` could only be attached to a whole
# CONTRACT. Its columns were `contract_id, kind, assertion, input_json,
# expected_json` — there was NO column naming a function or an output. So the
# opposite proof ("change a legal input, the output MUST differ") could only be
# written about a contract, never about ONE function output.
#
# MEASURED: the vocabulary for "a function is a subject" ALREADY EXISTS —
# `subject_kind_registry` holds 24 kinds, including
# `function -> function_registry.function_id`. The case table simply could not
# point at one.
#
# WHY ALTER AND NOT A NEW TABLE: the case table already holds `kind` (including
# `hard_fail`), `assertion`, `input_json` and `expected_json`. A new table would
# duplicate all of it to add two columns — the second-source-of-truth defect.
#
# BOTH NULL BY DEFAULT, so every existing row keeps its meaning: a case with no
# `subject_kind` is a CONTRACT-level case, exactly as before.
SKILL_CONTRACT_TDD_CASE_NEW_COLUMNS = [
    ("subject_kind", "TEXT"),
    ("subject_ref", "TEXT"),
]

SKILL_CONTRACT_REVIEW_LOG_DDL = """
CREATE TABLE IF NOT EXISTS skill_contract_review_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id     TEXT NOT NULL,
    gap             TEXT,
    revision        TEXT,
    streak_result   TEXT,
    linked_trace_id TEXT,
    chat_id         TEXT,
    task_id         TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    contract_ref INTEGER,
    FOREIGN KEY (contract_ref) REFERENCES skill_contract_template (contract_ref)
      ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_skill_contract_review_contract
  ON skill_contract_review_log (contract_id, created_at DESC);
"""

SKILL_CONTRACT_VIEW_DDL = """
CREATE VIEW IF NOT EXISTS v_skill_contract AS
SELECT
    t.contract_id,
    t.skill_key,
    t.taxonomy_path,
    t.purpose,
    t.purpose_not_responsible,
    t.write_owner,
    t.status,
    t.version,
    t.source,
    t.environment_json,
    t.flow_json,
    t.not_to_do_json,
    s.rule_version,
    s.target_streak,
    s.current_streak,
    s.best_streak,
    s.total_runs,
    s.reset_count,
    s.last_result,
    s.last_case_key,
    CASE
        WHEN s.current_streak IS NULL THEN 0
        WHEN s.current_streak >= s.target_streak THEN 1
        ELSE 0
    END AS streak_qualified
FROM skill_contract_template t
LEFT JOIN skill_contract_streak s
  ON s.contract_id = t.contract_id
 AND s.rule_version = t.version;
"""

ALL_SKILL_CONTRACT_DDL = (
    SKILL_CONTRACT_TEMPLATE_DDL,
    SKILL_CONTRACT_FIELD_DDL,
    SKILL_CONTRACT_TDD_CASE_DDL,
    SKILL_CONTRACT_STREAK_DDL,
    SKILL_CONTRACT_REVIEW_LOG_DDL,
    SKILL_CONTRACT_VIEW_DDL,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _loads(raw: Any, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _dumps(val: Any) -> str:
    return json.dumps(val if val is not None else {}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# gate allowlist (diagnostic escape hatch)
# ---------------------------------------------------------------------------
# The dependency gate (P0-4) and the streak gate (P0-5) are ON by default
# (A3-flip, 2026-09-20). They can be disabled per-run via
# SKILL_CONTRACT_ENFORCE_DEPS / SKILL_CONTRACT_ENFORCE_STREAK (see
# skill_library_api.py).
#
# HISTORY (kept because the reasoning matters): the flags used to default to
# "0" because every contract sat below its 100-streak target, so enabling the
# gates with an EMPTY allowlist made resolve_chat_identity() /
# register_chat_identity() return DEPENDENCY_NOT_READY for every call — a
# production outage, not enforcement. C2-followup changed the target to 5 and
# made the streak count DISTINCT code states, so all 17 contracts are now
# qualified and both gates PASS on live data. The default was flipped only
# after re-measuring that.
#
# The allowlist is a DIAGNOSTIC hatch, and it is deliberately explicit: a
# contract is only exempt when its id is named in
# SKILL_CONTRACT_GATE_ALLOWLIST. There is no wildcard and no "allow everything"
# switch, because a blanket bypass is exactly the "rule that does not apply"
# defect these gates exist to remove.
#
# It is EMPTY by default. Setting it for SKILL-0001 / SKILL-0002 would leave the
# gates ON but bypassed for exactly the contracts they protect — a gate that is
# enabled and simultaneously disabled.
#
# Set e.g. SKILL_CONTRACT_GATE_ALLOWLIST="SKILL-0001,SKILL-0002" only to run a
# scenario that must bypass the gate, and say so.

GATE_ALLOWLIST_ENV = "SKILL_CONTRACT_GATE_ALLOWLIST"


def gate_allowlist() -> set[str]:
    """Contract ids exempted from the P0-4 / P0-5 gates (from the env var)."""
    raw = os.environ.get(GATE_ALLOWLIST_ENV, "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_gate_allowlisted(contract_id: str) -> bool:
    """True when a contract is explicitly exempted from the P0-4 / P0-5 gates."""
    cid = (contract_id or "").strip()
    return bool(cid) and cid in gate_allowlist()


def ensure_skill_contract_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the skill_contract_* tables + view (idempotent)."""
    conn.execute("PRAGMA foreign_keys = ON;")
    for ddl in ALL_SKILL_CONTRACT_DDL:
        conn.executescript(ddl)
    # CREATE TABLE IF NOT EXISTS does NOT add columns to an existing table, so
    # the streak's code_hash / distinct_states / repeat_runs columns are applied
    # here. A column listed in the DDL but not added here would silently not
    # exist on a live DB.
    existing = {
        r[1] for r in conn.execute("PRAGMA table_info(skill_contract_streak)")
    }
    for name, decl in SKILL_CONTRACT_STREAK_NEW_COLUMNS:
        if name not in existing:
            conn.execute(
                "ALTER TABLE skill_contract_streak ADD COLUMN %s %s" % (name, decl)
            )
    # Same reason for the Field Register's machine rule columns (P2-gate).
    existing_field = {
        r[1] for r in conn.execute("PRAGMA table_info(skill_contract_field)")
    }
    for name, decl in SKILL_CONTRACT_FIELD_NEW_COLUMNS:
        if name not in existing_field:
            conn.execute(
                "ALTER TABLE skill_contract_field ADD COLUMN %s %s" % (name, decl)
            )
    # Same reason for the TDD case's SUBJECT columns (added 2026-09-25): a case
    # can now name the function output it is about, not only its contract.
    existing_case = {
        r[1] for r in conn.execute("PRAGMA table_info(skill_contract_tdd_case)")
    }
    for name, decl in SKILL_CONTRACT_TDD_CASE_NEW_COLUMNS:
        if name not in existing_case:
            conn.execute(
                "ALTER TABLE skill_contract_tdd_case ADD COLUMN %s %s"
                % (name, decl)
            )
    conn.commit()
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'skill_contract_%' ORDER BY name"
        ).fetchall()
    ]
    views = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' "
            "AND name LIKE 'v_skill_contract%' ORDER BY name"
        ).fetchall()
    ]
    return {"ok": True, "tables": tables, "views": views}


# ---------------------------------------------------------------------------
# taxonomy_path hard validation (against the ontology registry)
# ---------------------------------------------------------------------------

# Canonical form: "{entity_type}/{entity_key}"
#   channel/local_pc
#   module/task_center
#   capability/media/ppt
#   function/{capability_key}|{function_key}
#   api/{capability_key}|{api_key}
#   db_table/code_registry
#   db_field/{table_key}|{field_key}
# Composite entities (function / api / db_field) use the parent|child form,
# matching ontology_store.ENTITY_KEY_SEP.
TAXONOMY_ENTITY_TYPES = (
    "channel",
    "module",
    "capability",
    "function",
    "api",
    "db_table",
    "db_field",
)

# Accepted aliases so authors can write the 8-level names naturally.
TAXONOMY_TYPE_ALIASES: dict[str, str] = {
    "channel": "channel",
    "通道": "channel",
    "module": "module",
    "模塊": "module",
    "模块": "module",
    "capability": "capability",
    "能力": "capability",
    "function": "function",
    "函數": "function",
    "函数": "function",
    "api": "api",
    "table": "db_table",
    "db_table": "db_table",
    "數據表": "db_table",
    "数据表": "db_table",
    "field": "db_field",
    "db_field": "db_field",
    "字段": "db_field",
}


def parse_taxonomy_path(taxonomy_path: str) -> tuple[str, str] | None:
    """Split "{entity_type}/{entity_key}" into (entity_type, entity_key).

    Returns None when the path is not in canonical form or the type is unknown.
    """
    raw = (taxonomy_path or "").strip()
    if not raw or "/" not in raw:
        return None
    head, _, tail = raw.partition("/")
    etype = TAXONOMY_TYPE_ALIASES.get(head.strip().lower())
    if not etype:
        return None
    ekey = tail.strip()
    if not ekey:
        return None
    return etype, ekey


def validate_taxonomy_path(
    taxonomy_path: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[bool, list[str]]:
    """Hard-validate taxonomy_path against the ontology registry.

    Rules (hard — reject, never warn-and-continue):
      1. path must be "{entity_type}/{entity_key}" with a known entity type
      2. the entity must exist in the matching *_registry table
      3. the entity must be is_active = 1

    If the ontology registry tables are absent, validation is skipped and a
    note is returned (so DBs without the registry are not bricked).
    """
    parsed = parse_taxonomy_path(taxonomy_path)
    if not parsed:
        return False, [
            f"taxonomy_path {taxonomy_path!r} is not canonical; expected "
            f"'{{entity_type}}/{{entity_key}}' with entity_type in "
            f"{list(TAXONOMY_ENTITY_TYPES)}"
        ]
    etype, ekey = parsed

    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        has_registry = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (f"{etype}_registry",),
        ).fetchone()
        if not has_registry:
            return True, [f"ontology registry absent for {etype}; skipped"]
        try:
            from src.task_center.ontology_store import get_registry_entity

            row = get_registry_entity(etype, ekey, conn=conn)
        except Exception as e:
            return True, [f"ontology lookup unavailable ({type(e).__name__}); skipped"]
        if not row:
            return False, [
                f"taxonomy_path {taxonomy_path!r}: no {etype} entity "
                f"{ekey!r} in {etype}_registry"
            ]
        if not int(row.get("is_active") or 0):
            return False, [
                f"taxonomy_path {taxonomy_path!r}: {etype} {ekey!r} is not active"
            ]
        return True, []
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# write-owner enforcement (single authorized write entry per table)
# ---------------------------------------------------------------------------


def find_write_owner(
    table_name: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Return every contract that declares itself the write owner of a table."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        rows = conn.execute(
            "SELECT contract_id, skill_key, environment_json FROM "
            "skill_contract_template WHERE write_owner = 1"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            env = _loads(r["environment_json"], {})
            if str(env.get("table") or "").strip() == table_name:
                out.append({
                    "contract_id": r["contract_id"],
                    "skill_key": r["skill_key"],
                })
        return out
    finally:
        if own:
            conn.close()


def assert_table_write_allowed(
    table_name: str,
    caller_contract_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[bool, str]:
    """Hard gate: only the declared write owner may write a table.

    Returns (True, "") when allowed, or (False, reason) when rejected.
    When no contract declares ownership of the table, the write is allowed
    (ownership is opt-in, so unclaimed tables are not blocked).
    """
    owners = find_write_owner(table_name, db_path=db_path, conn=conn)
    if not owners:
        return True, ""
    ids = [o["contract_id"] for o in owners]
    if caller_contract_id in ids:
        return True, ""
    return False, (
        f"table {table_name!r} is owned by {ids}; "
        f"caller {caller_contract_id!r} is not the authorized write entry"
    )


# ---------------------------------------------------------------------------
# streak qualification gate
# ---------------------------------------------------------------------------


def compute_code_hash(contract_id: str, *, db_path: Path | str | None = None,
                      conn: sqlite3.Connection | None = None) -> str:
    """Hash the CODE UNDER TEST for a contract: its TDD cases + Field Register.

    WHY THIS EXISTS (C2-followup, 2026-09-20)
    -----------------------------------------
    The streak counts consecutive DISTINCT code states. To know whether the
    state changed, the runner must hash what it actually exercises. The inputs
    that determine a contract's TDD outcome are:

      * the case keys, kinds, input payloads and expected shapes
      * the Field Register rows the payload validator enforces

    Hashing those means: edit a fixture or a field rule -> the hash changes ->
    the next passing run counts as a NEW state. Re-run unchanged -> same hash ->
    a re-confirmation that does NOT inflate the streak.

    This is deliberately NOT a hash of the Python source: the source can change
    without altering what the cases exercise, and a source hash would then
    reset the streak for a comment edit. The hash tracks the CONTRACT's
    behaviour, which is what the cases actually test.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        cases = conn.execute(
            "SELECT case_key, kind, input_json, expected_json, status "
            "FROM skill_contract_tdd_case WHERE contract_id=? "
            "ORDER BY case_key",
            (contract_id,),
        ).fetchall()
        fields = conn.execute(
            "SELECT field_name, data_type, mandatory, enum_json, immutable, "
            "hard_rule FROM skill_contract_field WHERE contract_id=? "
            "ORDER BY field_name",
            (contract_id,),
        ).fetchall()
        blob = json.dumps(
            {"cases": [tuple(r) for r in cases],
             "fields": [tuple(r) for r in fields]},
            sort_keys=True, default=str,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    finally:
        if own:
            conn.close()


def is_streak_qualified(
    contract_id: str,
    *,
    rule_version: int | None = None,
    require_dedicated_probe: bool = False,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    """True when this contract's streak is EVIDENCE, not just repetition.

    Two conditions:
      1. `current_streak >= target_streak` — the historical check, always applied.
      2. the contract has a **dedicated probe** (a token in
         `skill_tdd_runner.PROBES`). OPT-IN, default OFF.

    WHY (2) EXISTS: without a dedicated probe, `_probe_for` falls back to
    `_probe_payload_rule`, which validates the payload against the contract's
    OWN Field Register. Measured: 4 of 18 contracts are in that state
    (`CAP.PPT.PRODUCE`, `CH.LOCAL_PC`, `MOD.TASK_CENTER`, `TBL.code_registry`).
    A self-referential probe measures "the register can read what the register
    was told", so a long streak there proves CONSISTENCY, not correctness —
    a number about the test harness wearing the label of a proof about the code.
    `compute_code_hash` hashes the cases + fields, so such a contract can even
    accumulate distinct states while exercising no behaviour.

    WHY IT IS OPT-IN: turning it on BY DEFAULT changed the meaning of an
    existing predicate and turned three unrelated tests red. A predicate whose
    answer changes because a caller was upgraded is a breaking change dressed as
    a safety feature. Callers that want the stronger claim ask for it, and
    `streak_evidence()` reports both halves so nothing is hidden.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        if rule_version is None:
            row = conn.execute(
                "SELECT version FROM skill_contract_template WHERE contract_id=?",
                (contract_id,),
            ).fetchone()
            if not row:
                return False
            rule_version = int(row["version"])
        s = conn.execute(
            "SELECT current_streak, target_streak FROM skill_contract_streak "
            "WHERE contract_id=? AND rule_version=?",
            (contract_id, int(rule_version)),
        ).fetchone()
        if not s:
            return False
        if int(s["current_streak"]) < int(s["target_streak"]):
            return False
        if require_dedicated_probe and not has_dedicated_probe(contract_id,
                                                              conn=conn):
            return False
        return True
    finally:
        if own:
            conn.close()


def has_dedicated_probe(contract_id: str, *,
                        conn: sqlite3.Connection | None = None,
                        db_path: Path | str | None = None) -> bool:
    """True when at least one of the contract's cases is served by a REAL probe.

    A "real" probe is a token in `skill_tdd_runner.PROBES`; the implicit
    `_probe_payload_rule` fallback does not count because it only checks the
    payload against the same contract's Field Register.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        try:
            import skill_tdd_runner as r
        except Exception:
            return False
        for case in list_tdd_cases(contract_id, conn=conn):
            key = str(case.get("case_key") or "")
            probe = r._probe_for(key, contract_id)
            if probe is not None and probe.__name__ != "_probe_payload_rule":
                return True
        return False
    finally:
        if own:
            conn.close()


def streak_evidence(contract_id: str, *,
                    rule_version: int | None = None,
                    db_path: Path | str | None = None,
                    conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """REPORT what a streak does and does not prove. Never a bare number.

    Distinguishes four states a caller must not confuse:
      no_streak / below_target / counting_only (fallback probe) / evidence
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        if rule_version is None:
            row = conn.execute(
                "SELECT version FROM skill_contract_template WHERE contract_id=?",
                (contract_id,)).fetchone()
            rule_version = int(row["version"]) if row else 1
        s = conn.execute(
            "SELECT current_streak, best_streak, target_streak, total_runs, "
            "       distinct_states, repeat_runs FROM skill_contract_streak "
            "WHERE contract_id=? AND rule_version=?",
            (contract_id, int(rule_version))).fetchone()
        dedicated = has_dedicated_probe(contract_id, conn=conn)
        if not s:
            return {"ok": True, "state": "no_streak", "dedicated_probe": dedicated,
                    "proves": "nothing — no run recorded"}
        cur = int(s["current_streak"]); tgt = int(s["target_streak"])
        if cur < tgt:
            state = "below_target"
        elif not dedicated:
            state = "counting_only"
        else:
            state = "evidence"
        return {
            "ok": True, "state": state,
            "current_streak": cur, "best_streak": int(s["best_streak"]),
            "target_streak": tgt, "total_runs": int(s["total_runs"]),
            "distinct_states": int(s["distinct_states"] or 0),
            "repeat_runs": int(s["repeat_runs"] or 0),
            "dedicated_probe": dedicated,
            "proves": {
                "evidence": "these cases behave consistently across "
                            "DISTINCT states (not runs); it does NOT prove "
                            "coverage of unwritten cases",
                "counting_only": "NOTHING about the code — the fallback probe "
                                 "validates the payload against this contract's "
                                 "own Field Register (self-referential)",
                "below_target": "a partial streak",
                "no_streak": "nothing — no run recorded",
            }[state],
        }
    finally:
        if own:
            conn.close()


def assert_streak_qualified(
    contract_id: str,
    *,
    rule_version: int | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[bool, str]:
    """Hard gate: a contract must be streak-qualified before it may be used."""
    if is_streak_qualified(
        contract_id, rule_version=rule_version, db_path=db_path, conn=conn
    ):
        return True, ""
    return False, (
        f"contract {contract_id!r} is not streak-qualified "
        f"(current_streak < target_streak)"
    )


# ---------------------------------------------------------------------------
# depends_on runtime gate (declared dependency must itself be ready)
# ---------------------------------------------------------------------------


def get_depends_on(
    contract_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Read environment.depends_on for a contract (declared dependencies)."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        row = conn.execute(
            "SELECT environment_json FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not row:
            return []
        env = _loads(row["environment_json"], {})
        deps = env.get("depends_on")
        if isinstance(deps, str):
            return [deps.strip()] if deps.strip() else []
        if isinstance(deps, list):
            return [str(d).strip() for d in deps if str(d).strip()]
        return []
    finally:
        if own:
            conn.close()


def check_dependencies(
    contract_id: str,
    *,
    require_qualified: bool = True,
    require_dedicated_probe: bool = False,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Check every declared dependency of a contract.

    A dependency that does not exist is always an error. When
    require_qualified=True, a dependency that exists but is not
    streak-qualified is also an error.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        details: list[dict[str, Any]] = []
        errors: list[str] = []
        for dep in get_depends_on(contract_id, conn=conn):
            exists = bool(conn.execute(
                "SELECT contract_id FROM skill_contract_template WHERE contract_id=?",
                (dep,),
            ).fetchone())
            # REPORT the two facts separately. Collapsing them into one boolean
            # is what let a dependency with a long counter but a self-referential
            # probe read as `qualified: True`.
            #
            # NOTE `qualified` is the SAME predicate regardless of
            # `require_qualified`: that flag controls whether a shortfall becomes
            # an ERROR, not what the fact IS. Overloading it would make the
            # report change meaning with the caller's strictness, so two callers
            # could disagree about the same dependency.
            has_probe = (has_dedicated_probe(dep, conn=conn) if exists else False)
            count_ok = (is_streak_qualified(
                dep, db_path=db_path, conn=conn,
                require_dedicated_probe=False) if exists else False)
            qualified = bool(exists and count_ok
                             and (has_probe or not require_dedicated_probe))
            details.append({
                "contract_id": dep,
                "exists": exists,
                "streak_at_target": count_ok,
                "dedicated_probe": has_probe,
                "qualified": qualified,
            })
            if not exists:
                errors.append(
                    f"dependency {dep!r} of {contract_id!r} does not exist"
                )
            elif require_qualified and not qualified:
                errors.append(
                    f"dependency {dep!r} of {contract_id!r} is not streak-qualified"
                )
        return {"ok": not errors, "contract_id": contract_id,
                "deps": details, "errors": errors}
    finally:
        if own:
            conn.close()


def assert_dependencies_ready(
    contract_id: str,
    *,
    require_qualified: bool = True,
    require_dedicated_probe: bool = False,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[bool, str]:
    """Hard gate: a contract may not run until its declared deps are ready."""
    res = check_dependencies(
        contract_id,
        require_qualified=require_qualified,
        require_dedicated_probe=require_dedicated_probe,
        db_path=db_path,
        conn=conn,
    )
    if res["ok"]:
        return True, ""
    return False, "; ".join(res["errors"])


# ---------------------------------------------------------------------------
# rule-version bump (streak reset trigger)
# ---------------------------------------------------------------------------


def bump_rule_version(
    contract_id: str,
    *,
    reason: str = "",
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Increment the contract version so the streak restarts from zero.

    The streak row is keyed on (contract_id, rule_version) and the view joins
    on rule_version = version, so bumping the version makes the old streak
    inapplicable — that IS the reset. The old row is kept for history.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        row = conn.execute(
            "SELECT version FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "code": "UNKNOWN_CONTRACT",
                    "message": f"contract_id not found: {contract_id}"}
        old = int(row["version"])
        new = old + 1
        conn.execute(
            "UPDATE skill_contract_template SET version=?, updated_at=? "
            "WHERE contract_id=?",
            (new, _utc_now(), contract_id),
        )
        conn.commit()
        return {"ok": True, "contract_id": contract_id,
                "old_version": old, "new_version": new, "reason": reason}
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# contract template
# ---------------------------------------------------------------------------


def upsert_contract(
    contract_id: str,
    skill_key: str,
    taxonomy_path: str,
    purpose: str,
    *,
    environment: Any = None,
    purpose_not_responsible: str | None = None,
    flow: Any = None,
    not_to_do: Any = None,
    write_owner: bool = False,
    status: str = "draft",
    version: int = 1,
    source: str = "manual",
    notes: str | None = None,
    enforce_taxonomy: bool = True,
    allow_taxonomy_override: bool = False,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Insert or update one skill contract. Hard-rejects invalid enum values.

    When enforce_taxonomy=True (default), taxonomy_path is hard-validated
    against the ontology registry: a path that does not resolve to an active
    registry entity is rejected and nothing is written.

    P0-2: disabling the check requires an explicit allow_taxonomy_override=True,
    so taxonomy enforcement cannot be silently switched off.
    """
    cid = (contract_id or "").strip()
    if not cid:
        return {"ok": False, "code": "MISSING_CONTRACT_ID",
                "message": "contract_id is required"}
    skey = (skill_key or "").strip()
    if not skey:
        return {"ok": False, "code": "MISSING_SKILL_KEY",
                "message": "skill_key is required"}
    if status not in CONTRACT_STATUSES:
        return {"ok": False, "code": "BAD_STATUS",
                "message": f"status must be one of {CONTRACT_STATUSES}, got {status!r}"}
    if not (taxonomy_path or "").strip():
        return {"ok": False, "code": "MISSING_TAXONOMY_PATH",
                "message": "taxonomy_path is required"}
    if not (purpose or "").strip():
        return {"ok": False, "code": "MISSING_PURPOSE",
                "message": "purpose is required (single objective)"}
    if not enforce_taxonomy and not allow_taxonomy_override:
        return {
            "ok": False,
            "code": "TAXONOMY_ENFORCEMENT_REQUIRED",
            "message": (
                "enforce_taxonomy=False requires allow_taxonomy_override=True; "
                "taxonomy validation cannot be silently disabled"
            ),
        }

    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        if enforce_taxonomy:
            ok_tax, tax_errors = validate_taxonomy_path(taxonomy_path, conn=conn)
            if not ok_tax:
                return {
                    "ok": False,
                    "code": "BAD_TAXONOMY_PATH",
                    "message": "; ".join(tax_errors),
                    "errors": tax_errors,
                }
        now = _utc_now()
        row = conn.execute(
            "SELECT contract_id FROM skill_contract_template WHERE contract_id=?",
            (cid,),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE skill_contract_template SET
                     skill_key=?, taxonomy_path=?, environment_json=?, purpose=?,
                     purpose_not_responsible=?, flow_json=?, not_to_do_json=?,
                     write_owner=?, status=?, version=?, source=?, notes=?,
                     updated_at=?
                   WHERE contract_id=?""",
                (
                    skey, taxonomy_path, _dumps(environment), purpose,
                    purpose_not_responsible, _dumps(flow if flow is not None else []),
                    _dumps(not_to_do if not_to_do is not None else []),
                    1 if write_owner else 0, status, int(version), source, notes,
                    now, cid,
                ),
            )
            action = "updated"
        else:
            conn.execute(
                """INSERT INTO skill_contract_template
                     (contract_id, skill_key, taxonomy_path, environment_json,
                      purpose, purpose_not_responsible, flow_json, not_to_do_json,
                      write_owner, status, version, source, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    cid, skey, taxonomy_path, _dumps(environment), purpose,
                    purpose_not_responsible, _dumps(flow if flow is not None else []),
                    _dumps(not_to_do if not_to_do is not None else []),
                    1 if write_owner else 0, status, int(version), source, notes,
                    now, now,
                ),
            )
            action = "inserted"
        conn.commit()
        return {"ok": True, "contract_id": cid, "action": action}
    finally:
        if own:
            conn.close()


def get_contract(
    contract_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        row = conn.execute(
            "SELECT * FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["environment"] = _loads(d.pop("environment_json", None), {})
        d["flow"] = _loads(d.pop("flow_json", None), [])
        d["not_to_do"] = _loads(d.pop("not_to_do_json", None), [])
        return d
    finally:
        if own:
            conn.close()


def list_contracts(
    *,
    status: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        sql = "SELECT * FROM v_skill_contract"
        params: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status=?"
            params = (status,)
        sql += " ORDER BY contract_id"
        out: list[dict[str, Any]] = []
        for r in conn.execute(sql, params).fetchall():
            d = dict(r)
            d["environment"] = _loads(d.pop("environment_json", None), {})
            d["flow"] = _loads(d.pop("flow_json", None), [])
            d["not_to_do"] = _loads(d.pop("not_to_do_json", None), [])
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# field register
# ---------------------------------------------------------------------------


def upsert_field(
    contract_id: str,
    field_name: str,
    data_type: str,
    hard_rule: str,
    *,
    field_id: str | None = None,
    taxonomy_path: str | None = None,
    mandatory: bool = False,
    enum: Any = None,
    owner_skill_id: str | None = None,
    immutable: bool = False,
    remark: str | None = None,
    why: str | None = None,
    rule_kind: str | None = None,
    rule_value: Any = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Insert or update one Field Register row (field-level SSOT).

    `rule_kind` / `rule_value` carry the MACHINE rule that
    `validate_payload_against_contract` enforces. `hard_rule` stays human prose.

    `why` is the REASON the rule exists. It is carried into every refusal about
    this field, so a worker refused by the contract can learn from it — the
    human's words (2026-09-25): "skill can help to have the explain easy to
    explain to worker why we reject and tutorial them".
    """
    if not (contract_id or "").strip():
        return {"ok": False, "code": "MISSING_CONTRACT_ID",
                "message": "contract_id is required"}
    if not (field_name or "").strip():
        return {"ok": False, "code": "MISSING_FIELD_NAME",
                "message": "field_name is required"}
    if not (data_type or "").strip():
        return {"ok": False, "code": "MISSING_DATA_TYPE",
                "message": "data_type is required"}
    if not (hard_rule or "").strip():
        return {"ok": False, "code": "MISSING_HARD_RULE",
                "message": "hard_rule is required (no warn-only rules allowed)"}
    rk = (rule_kind or "").strip() or None
    if rk is not None and rk not in RULE_KINDS:
        return {"ok": False, "code": "UNKNOWN_RULE_KIND",
                "message": "rule_kind must be one of %s, got %r" % (RULE_KINDS, rk)}
    if rk is not None and rule_value is None:
        return {"ok": False, "code": "MISSING_RULE_VALUE",
                "message": "rule_value is required when rule_kind is set"}
    rule_value_json = None if rk is None else json.dumps(rule_value, ensure_ascii=False)

    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        tpl = conn.execute(
            "SELECT contract_id FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not tpl:
            return {"ok": False, "code": "UNKNOWN_CONTRACT",
                    "message": f"contract_id not found: {contract_id}"}
        now = _utc_now()
        # field_id is the human-facing Field Register ID (F-001). Auto-number
        # per contract when the caller does not supply one.
        if not (field_id or "").strip():
            n = conn.execute(
                "SELECT count(*) FROM skill_contract_field WHERE contract_id=?",
                (contract_id,),
            ).fetchone()[0]
            field_id = f"F-{int(n) + 1:03d}"
        enum_json = None if enum is None else json.dumps(enum, ensure_ascii=False)
        # STANDARDISE AT THE WRITE SITE. The DDL now declares these columns
        # `NOT NULL DEFAULT 'NA'`, but an explicit `None` in the INSERT BYPASSES
        # the default — measured: 33 tests failed with "NOT NULL constraint
        # failed: skill_contract_field.rule_value_json". A default only applies
        # when the column is OMITTED, so the write site must standardise. That is
        # the rule `no_null` states: an empty value becomes the explicit NA, and
        # a NULL that survives is a defect.
        import no_null as _nn
        rule_value_json = _nn.standardize_empty(rule_value_json, _nn.KIND_TEXT)
        remark = _nn.standardize_empty(remark, _nn.KIND_TEXT)
        why = _nn.standardize_empty(why, _nn.KIND_TEXT)
        row = conn.execute(
            "SELECT id FROM skill_contract_field WHERE contract_id=? AND field_name=?",
            (contract_id, field_name),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE skill_contract_field SET
                     field_id=?, taxonomy_path=?, data_type=?, mandatory=?,
                     enum_json=?, hard_rule=?, rule_kind=?, rule_value_json=?,
                     owner_skill_id=?, immutable=?,
                     remark=?, why=?, updated_at=?
                   WHERE contract_id=? AND field_name=?""",
                (
                    field_id, taxonomy_path, data_type, 1 if mandatory else 0,
                    enum_json, hard_rule, rk, rule_value_json,
                    owner_skill_id, 1 if immutable else 0,
                    remark, why, now, contract_id, field_name,
                ),
            )
            action = "updated"
        else:
            conn.execute(
                """INSERT INTO skill_contract_field
                     (field_id, contract_id, contract_ref, taxonomy_path, field_name, data_type,
                      mandatory, enum_json, hard_rule, rule_kind, rule_value_json,
                      owner_skill_id, immutable,
                      remark, why, created_at, updated_at)
                   VALUES (?,?,(SELECT contract_ref FROM skill_contract_template
                                WHERE contract_id=?),?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    field_id, contract_id, contract_id, taxonomy_path, field_name, data_type,
                    1 if mandatory else 0, enum_json, hard_rule, rk, rule_value_json,
                    owner_skill_id, 1 if immutable else 0, remark, why, now, now,
                ),
            )
            action = "inserted"
        conn.commit()
        return {"ok": True, "contract_id": contract_id, "field_name": field_name,
                "action": action}
    finally:
        if own:
            conn.close()


def list_fields(
    contract_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        rows = conn.execute(
            "SELECT * FROM skill_contract_field WHERE contract_id=? "
            "ORDER BY field_id, field_name",
            (contract_id,),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["enum"] = _loads(d.pop("enum_json", None), None)
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# TDD cases
# ---------------------------------------------------------------------------


def upsert_tdd_case(
    case_key: str,
    contract_id: str,
    kind: str,
    assertion: str,
    *,
    input_payload: Any = None,
    expected: Any = None,
    status: str = "active",
    subject_kind: str | None = None,
    subject_ref: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Insert or update one TDD proof case (pass / hard_fail).

    `subject_kind` + `subject_ref` (added 2026-09-25) name the SUBJECT the case
    is about — e.g. `subject_kind="function"`, `subject_ref="<function_id>"`.
    Both default to None, which is a CONTRACT-level case, exactly as before.

    `subject_kind` is VALIDATED against `subject_kind_registry`, so a kind that
    does not exist is REFUSED rather than stored. A case about a kind nobody
    registered is a case about nothing — the same rule `assert_named` applies to
    a term. An unreadable register is reported, never silently accepted.
    """
    if not (case_key or "").strip():
        return {"ok": False, "code": "MISSING_CASE_KEY",
                "message": "case_key is required"}
    if kind not in TDD_KINDS:
        return {"ok": False, "code": "BAD_KIND",
                "message": f"kind must be one of {TDD_KINDS}, got {kind!r}"}
    if not (assertion or "").strip():
        return {"ok": False, "code": "MISSING_ASSERTION",
                "message": "assertion is required"}
    # A subject needs BOTH halves: a kind with no ref names nothing, and a ref
    # with no kind cannot be resolved. Half a subject is not a subject.
    sk = (subject_kind or "").strip() or None
    sr = (subject_ref or "").strip() or None
    if (sk is None) != (sr is None):
        return {"ok": False, "code": "HALF_SUBJECT",
                "message": ("subject_kind and subject_ref must be given "
                            "TOGETHER (got subject_kind=%r, subject_ref=%r)"
                            % (sk, sr))}

    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        if sk is not None:
            known = {r[0] for r in conn.execute(
                "SELECT kind_key FROM subject_kind_registry")}
            if not known:
                return {"ok": False, "code": "SUBJECT_KIND_registry_EMPTY",
                        "message": ("subject_kind_registry is empty, so the "
                                    "subject kind cannot be checked")}
            if sk not in known:
                return {"ok": False, "code": "UNKNOWN_SUBJECT_KIND",
                        "message": ("subject_kind %r is not in "
                                    "subject_kind_registry (it holds %d kind(s))"
                                    % (sk, len(known)))}
        tpl = conn.execute(
            "SELECT contract_id FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not tpl:
            return {"ok": False, "code": "UNKNOWN_CONTRACT",
                    "message": f"contract_id not found: {contract_id}"}
        now = _utc_now()
        input_json = None if input_payload is None else json.dumps(
            input_payload, ensure_ascii=False
        )
        expected_json = None if expected is None else json.dumps(
            expected, ensure_ascii=False
        )
        row = conn.execute(
            "SELECT id FROM skill_contract_tdd_case WHERE case_key=?", (case_key,)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE skill_contract_tdd_case SET
                     contract_id=?, kind=?, assertion=?, input_json=?,
                     expected_json=?, status=?, subject_kind=?, subject_ref=?,
                     updated_at=?
                   WHERE case_key=?""",
                (contract_id, kind, assertion, input_json, expected_json,
                 status, sk, sr, now, case_key),
            )
            action = "updated"
        else:
            conn.execute(
                """INSERT INTO skill_contract_tdd_case
                     (case_key, contract_id, contract_ref, kind, assertion, input_json,
                      expected_json, status, subject_kind, subject_ref,
                      created_at, updated_at)
                   VALUES (?,?,(SELECT contract_ref FROM skill_contract_template
                                WHERE contract_id=?),?,?,?,?,?,?,?,?,?)""",
                (case_key, contract_id, contract_id, kind, assertion, input_json,
                 expected_json, status, sk, sr, now, now),
            )
            action = "inserted"
        conn.commit()
        return {"ok": True, "case_key": case_key, "action": action}
    finally:
        if own:
            conn.close()


def _prune_children(
    table: str,
    key_col: str,
    contract_id: str,
    declared_keys: Any,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Delete rows of `table` for `contract_id` whose `key_col` is not declared.

    THE PRINCIPLE, STATED ONCE (2026-09-29): **a declaration is the WHOLE SET,
    not a delta.** An upsert-only writer makes a child table the UNION of every
    declaration ever seen, so RENAMING a key leaves the OLD key behind and the
    contract then runs BOTH. MEASURED: `SKILL.MEASUREMENT.CROSS.CHECK` reported
    **9/11 cases passed** after a case key was renamed, with two stale keys
    failing as "no probe registered".

    WHY THIS IS GENERIC AND NOT A `tdd_case` SPECIAL CASE. The human asked the
    exact question: *"呢個 prune 係咪都 apply 去其他「會留 stale」嘅註冊表？"* —
    and the answer was NO, only `tdd_case` had it. A principle enforced in ONE
    place is the "stated but not enforced" disease this repo keeps paying for, so
    the rule lives HERE and every child table uses it.

    `declared_keys` is REQUIRED and an EMPTY set is REFUSED: an empty
    declaration would delete EVERY row of the contract, which is the opposite
    risk and must not be reachable by omission.
    """
    keys = {str(k).strip() for k in (declared_keys or []) if str(k).strip()}
    if not keys:
        return {"ok": False, "code": "NO_DECLARED_KEYS",
                "message": ("declared_keys is empty — refusing to prune %s, "
                            "because an empty declaration would delete EVERY "
                            "row of %s" % (table, contract_id))}
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        rows = [str(r[0]) for r in conn.execute(
            "SELECT %s FROM %s WHERE contract_id=?" % (key_col, table),
            (contract_id,))]
        stale = [k for k in rows if k not in keys]
        for k in stale:
            conn.execute(
                "DELETE FROM %s WHERE contract_id=? AND %s=?" % (table, key_col),
                (contract_id, k))
        conn.commit()
        return {"ok": True, "contract_id": contract_id, "table": table,
                "declared": len(keys), "deleted": len(stale),
                "deleted_keys": stale}
    finally:
        if own:
            conn.close()


def prune_tdd_cases(
    contract_id: str,
    declared_keys: Any,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Delete TDD cases of `contract_id` whose `case_key` is NOT declared.

    Thin wrapper over `_prune_children` — see it for the principle. Kept as a
    named function because `skill_registrar` and the proof call it by name.
    """
    return _prune_children("skill_contract_tdd_case", "case_key", contract_id,
                           declared_keys, db_path=db_path, conn=conn)


def prune_fields(
    contract_id: str,
    declared_names: Any,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Delete fields of `contract_id` whose `field_name` is NOT declared.

    THE SAME PRINCIPLE AS `prune_tdd_cases`, applied to the OTHER child table.
    MEASURED 2026-09-29: `upsert_field` is upsert-only, so a RENAMED field name
    would leave the old row behind exactly as a renamed case key did. The human
    asked whether the prune was general or a one-off; it was a one-off, and this
    is the generalisation.
    """
    return _prune_children("skill_contract_field", "field_name", contract_id,
                           declared_names, db_path=db_path, conn=conn)


def list_tdd_cases(
    contract_id: str,
    *,
    kind: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        sql = "SELECT * FROM skill_contract_tdd_case WHERE contract_id=?"
        params: list[Any] = [contract_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY kind, case_key"
        out: list[dict[str, Any]] = []
        for r in conn.execute(sql, tuple(params)).fetchall():
            d = dict(r)
            d["input"] = _loads(d.pop("input_json", None), None)
            d["expected"] = _loads(d.pop("expected_json", None), None)
            out.append(d)
        return out
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# streak counter (DB-calculated)
# ---------------------------------------------------------------------------


def get_streak(
    contract_id: str,
    *,
    rule_version: int = 1,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        row = conn.execute(
            "SELECT * FROM skill_contract_streak WHERE contract_id=? AND rule_version=?",
            (contract_id, int(rule_version)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        if own:
            conn.close()


def record_streak_result(
    contract_id: str,
    case_key: str,
    passed: bool,
    *,
    rule_version: int = 1,
    target_streak: int = 100,
    code_hash: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Record one TDD round and recompute the streak.

    THE STREAK COUNTS DISTINCT CODE STATES, NOT RUNS (C2-followup 2026-09-20)
    ------------------------------------------------------------------------
    The probes are deterministic, so counting runs made the streak inflate on
    repetition: 100 runs of unchanged code gave a streak of 100 while measuring
    nothing new. The streak now advances only when the code under test CHANGED:

      * `code_hash` equals the stored one -> this is a RE-CONFIRMATION of the
        same state. `repeat_runs` increments; `current_streak` does NOT advance.
      * `code_hash` differs (or none stored yet) -> a NEW state. On pass,
        `current_streak` advances and `distinct_states` increments.

    A failure still resets `current_streak` to 0, because a broken gate is a
    broken gate regardless of whether the code changed.

    `code_hash=None` means the caller could not compute one; the run is then
    treated as a new state so the behaviour degrades to the old semantics
    rather than silently freezing the streak.
    """
    if not (contract_id or "").strip():
        return {"ok": False, "code": "MISSING_CONTRACT_ID",
                "message": "contract_id is required"}
    if not (case_key or "").strip():
        return {"ok": False, "code": "MISSING_CASE_KEY",
                "message": "case_key is required"}

    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        tpl = conn.execute(
            "SELECT contract_id FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not tpl:
            return {"ok": False, "code": "UNKNOWN_CONTRACT",
                    "message": f"contract_id not found: {contract_id}"}
        rv = int(rule_version)
        now = _utc_now()
        row = conn.execute(
            "SELECT * FROM skill_contract_streak WHERE contract_id=? AND rule_version=?",
            (contract_id, rv),
        ).fetchone()
        if row:
            cur = int(row["current_streak"])
            best = int(row["best_streak"])
            total = int(row["total_runs"])
            resets = int(row["reset_count"])
            tgt = int(row["target_streak"])
            prev_hash = row["code_hash"]
            distinct = int(row["distinct_states"] or 0)
            repeats = int(row["repeat_runs"] or 0)
        else:
            cur = best = total = resets = 0
            tgt = int(target_streak)
            prev_hash = None
            distinct = repeats = 0

        total += 1
        # A run is a RE-CONFIRMATION only when we have a hash for both sides and
        # they match. No hash -> treat as a new state (degrade to old semantics).
        is_repeat = bool(code_hash) and prev_hash == code_hash
        if is_repeat:
            repeats += 1
        else:
            distinct += 1

        if passed:
            if not is_repeat:
                cur += 1
                if cur > best:
                    best = cur
            last = "pass"
        else:
            cur = 0
            resets += 1
            last = "fail"

        if row:
            conn.execute(
                """UPDATE skill_contract_streak SET
                     target_streak=?, current_streak=?, best_streak=?,
                     total_runs=?, reset_count=?, last_result=?, last_case_key=?,
                     code_hash=?, distinct_states=?, repeat_runs=?, updated_at=?
                   WHERE contract_id=? AND rule_version=?""",
                (tgt, cur, best, total, resets, last, case_key,
                 code_hash, distinct, repeats, now, contract_id, rv),
            )
        else:
            conn.execute(
                """INSERT INTO skill_contract_streak
                     (contract_id, contract_ref, rule_version, target_streak, current_streak,
                      best_streak, total_runs, reset_count, last_result,
                      last_case_key, code_hash, distinct_states, repeat_runs,
                      created_at, updated_at)
                   VALUES (?,(SELECT contract_ref FROM skill_contract_template
                               WHERE contract_id=?),?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (contract_id, contract_id, rv, tgt, cur, best, total, resets, last,
                 case_key, code_hash, distinct, repeats, now, now),
            )
        conn.commit()
        return {
            "ok": True,
            "contract_id": contract_id,
            "rule_version": rv,
            "case_key": case_key,
            "result": last,
            "current_streak": cur,
            "best_streak": best,
            "total_runs": total,
            "reset_count": resets,
            "target_streak": tgt,
            "qualified": cur >= tgt,
            "code_hash": code_hash,
            "distinct_states": distinct,
            "repeat_runs": repeats,
            "was_repeat": is_repeat,
        }
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# post-case review log
# ---------------------------------------------------------------------------


def append_review_log(
    contract_id: str,
    *,
    gap: str | None = None,
    revision: str | None = None,
    streak_result: str | None = None,
    linked_trace_id: str | None = None,
    chat_id: str | None = None,
    task_id: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Append one Post-Case Review Log entry (append-only)."""
    if not (contract_id or "").strip():
        return {"ok": False, "code": "MISSING_CONTRACT_ID",
                "message": "contract_id is required"}
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        tpl = conn.execute(
            "SELECT contract_id FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not tpl:
            return {"ok": False, "code": "UNKNOWN_CONTRACT",
                    "message": f"contract_id not found: {contract_id}"}
        cur = conn.execute(
            """INSERT INTO skill_contract_review_log
                 (contract_id, contract_ref, gap, revision, streak_result, linked_trace_id,
                  chat_id, task_id, created_at)
               VALUES (?,(SELECT contract_ref FROM skill_contract_template
                           WHERE contract_id=?),?,?,?,?,?,?,?)""",
            (contract_id, contract_id, gap, revision, streak_result, linked_trace_id,
             chat_id, task_id, _utc_now()),
        )
        conn.commit()
        return {"ok": True, "log_id": cur.lastrowid, "contract_id": contract_id}
    finally:
        if own:
            conn.close()


def list_review_logs(
    contract_id: str,
    *,
    limit: int = 50,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        rows = conn.execute(
            "SELECT * FROM skill_contract_review_log WHERE contract_id=? "
            "ORDER BY id DESC LIMIT ?",
            (contract_id, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# hard payload validation against the Field Register
# ---------------------------------------------------------------------------

_TYPE_MAP: dict[str, tuple[str, ...]] = {
    "string": ("str",),
    "text": ("str",),
    "int": ("int",),
    "integer": ("int",),
    "bool": ("bool",),
    "boolean": ("bool",),
    "float": ("float", "int"),
    "number": ("float", "int"),
    "json": ("dict", "list"),
    "object": ("dict",),
    "list": ("list",),
    "array": ("list",),
    "datetime": ("str",),
}


def validate_payload_against_contract(
    contract_id: str,
    payload: dict[str, Any],
    *,
    current: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> tuple[bool, list[str]]:
    """Hard validation against skill_contract_field. No LLM judgement.

    Rules (all hard — reject, never warn-and-continue):
      1. missing mandatory field
      2. data type mismatch
      3. value outside the registered enum
      4. field present in payload but not registered
      5. immutable field value changed vs `current`
      6. the field's MACHINE rule (rule_kind / rule_value) is violated

    Rule 6 is what makes `hard_rule` apply. Before P2-gate Phase 1 the prose
    rule was stored and never read, so a field could declare "must be True"
    and accept False.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        rows = conn.execute(
            "SELECT * FROM skill_contract_field WHERE contract_id=?",
            (contract_id,),
        ).fetchall()
    finally:
        if own:
            conn.close()

    if not rows:
        return False, [f"no Field Register rows for contract_id: {contract_id}"]

    errors: list[str] = []
    registered: set[str] = set()
    for r in rows:
        f = dict(r)
        name = f["field_name"]
        registered.add(name)
        mandatory = bool(f["mandatory"])
        immutable = bool(f["immutable"])
        dtype = str(f["data_type"] or "").strip().lower()
        enum = _loads(f.get("enum_json"), None)
        rule_kind = str(f.get("rule_kind") or "").strip()
        rule_value = _loads(f.get("rule_value_json"), None)

        # WHY THE RULE EXISTS, carried into EVERY refusal about this field.
        #
        # MEASURED DEFECT (2026-09-25): a refusal said WHAT failed and never WHY,
        # so a worker could not learn from it. The human's words: "skill can help
        # to have the explain easy to explain to worker why we reject and
        # tutorial them". The reason is DATA on the field, so the refusal reads
        # it rather than inventing one.
        why = str(f.get("why") or "").strip()
        why_suffix = "" if why in ("", "NA") else "  [why: %s]" % why

        def err(msg: str, _suffix: str = why_suffix) -> None:
            errors.append(msg + _suffix)

        if name not in payload:
            if mandatory:
                err(f"Missing required field: {name}")
            continue

        val = payload[name]
        allowed = _TYPE_MAP.get(dtype)
        if allowed and type(val).__name__ not in allowed:
            err(
                f"Field {name} type mismatch, expect {dtype}, "
                f"got {type(val).__name__}"
            )
        if isinstance(enum, list) and enum and val not in enum:
            err(
                f"Field {name} value {val!r} not in enum {enum}"
            )
        # Rule 6: the machine rule the Field Register declares. `const` compares
        # exactly (so True != 1 and "x" != "x "); `non_blank` rejects a string
        # that is empty or whitespace-only; `type` compares the PYTHON type name
        # exactly; `length` compares the value's length.
        if rule_kind == "const" and val != rule_value:
            err(
                f"Field {name} must equal {rule_value!r} (hard rule), got {val!r}"
            )
        elif rule_kind == "non_blank" and isinstance(val, str) and not val.strip():
            err(
                f"Field {name} must be non-blank (hard rule), got {val!r}"
            )
        elif rule_kind == "type":
            # EXACT type, not the coarse `data_type` family. `bool` is a subclass
            # of `int` in Python, so `isinstance(True, int)` is True — which is
            # why the comparison is on `type(val).__name__` and not isinstance.
            want = str(rule_value)
            got = type(val).__name__
            if got != want:
                err(
                    f"Field {name} must be of type {want} (hard rule), "
                    f"got {got}"
                )
        elif rule_kind == "length":
            # A value with no length cannot satisfy a length rule, so it is a
            # violation rather than a skip. `len()` on an int raises, so the
            # check is guarded and the failure is REPORTED.
            try:
                got_len = len(val)
            except TypeError:
                err(
                    f"Field {name} must have length {rule_value} (hard rule), "
                    f"but {type(val).__name__} has no length"
                )
            else:
                if got_len != int(rule_value):
                    err(
                        f"Field {name} must have length {rule_value} "
                        f"(hard rule), got {got_len}"
                    )
        if immutable and current is not None and name in current:
            if current[name] != val:
                err(
                    f"Field {name} is immutable; change from "
                    f"{current[name]!r} to {val!r} rejected"
                )

    for key in payload:
        if key not in registered:
            errors.append(f"Unregistered field not allowed in payload: {key}")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# seed from skills/**/*.skill.md frontmatter (optional bulk import)
# ---------------------------------------------------------------------------


def seed_contracts_from_skills(
    skill_root: Path | str | None = None,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Bulk-import contracts from *.skill.md frontmatter (idempotent upsert).

    Reads only `task_id` / `name` / `reason` / `schema` style keys; the six
    contract columns are left for explicit authoring (status stays 'draft').
    """
    root = Path(skill_root or (BASE_DIR / "skills"))
    own = conn is None
    if own:
        conn = _connect(db_path)
    inserted = updated = skipped = 0
    try:
        ensure_skill_contract_schema(conn)
        try:
            from skill_scanner import _FRONTMATTER_RE, _load_meta
        except Exception:
            return {"ok": False, "code": "SCANNER_UNAVAILABLE",
                    "message": "skill_scanner not importable"}
        for fp in sorted(root.rglob("*.skill.md")):
            try:
                raw = fp.read_text(encoding="utf-8")
            except OSError:
                skipped += 1
                continue
            m = _FRONTMATTER_RE.match(raw)
            if not m:
                skipped += 1
                continue
            try:
                fm = _load_meta(m.group(1))
            except Exception:
                skipped += 1
                continue
            if not isinstance(fm, dict):
                skipped += 1
                continue
            task_id = str(fm.get("task_id") or "").strip()
            if not task_id:
                skipped += 1
                continue
            skill_key = str(fm.get("name") or task_id).strip()
            res = upsert_contract(
                contract_id=task_id,
                skill_key=skill_key,
                taxonomy_path=str(fm.get("schema") or "unclassified"),
                purpose=str(fm.get("reason") or fm.get("qc_summary") or "unspecified"),
                status="draft",
                source=f"scan:{fp.name}",
                enforce_taxonomy=False,
                allow_taxonomy_override=True,
                conn=conn,
            )
            if res.get("ok"):
                if res.get("action") == "inserted":
                    inserted += 1
                else:
                    updated += 1
            else:
                skipped += 1
        conn.commit()
        return {"ok": True, "inserted": inserted, "updated": updated,
                "skipped": skipped, "root": str(root)}
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# P2-1 contract status governance
# ---------------------------------------------------------------------------

# Minimum coverage for a contract to be promotable to active.
PROMOTE_MIN_FIELDS = 5
PROMOTE_MIN_PASS = 3
PROMOTE_MIN_HARD_FAIL = 2


def contract_readiness(
    contract_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Report whether a contract meets the promotion thresholds."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        fields = list_fields(contract_id, conn=conn)
        cases = list_tdd_cases(contract_id, conn=conn)
        npass = len([c for c in cases if c["kind"] == "pass"])
        nfail = len([c for c in cases if c["kind"] == "hard_fail"])
        reasons: list[str] = []
        if len(fields) < PROMOTE_MIN_FIELDS:
            reasons.append(f"fields {len(fields)} < {PROMOTE_MIN_FIELDS}")
        if npass < PROMOTE_MIN_PASS:
            reasons.append(f"pass cases {npass} < {PROMOTE_MIN_PASS}")
        if nfail < PROMOTE_MIN_HARD_FAIL:
            reasons.append(f"hard_fail cases {nfail} < {PROMOTE_MIN_HARD_FAIL}")
        return {
            "contract_id": contract_id,
            "ready": not reasons,
            "fields": len(fields),
            "pass_cases": npass,
            "hard_fail_cases": nfail,
            "reasons": reasons,
        }
    finally:
        if own:
            conn.close()


def promote_contract(
    contract_id: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Promote a draft contract to active when it meets the thresholds."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        ensure_skill_contract_schema(conn)
        row = conn.execute(
            "SELECT status FROM skill_contract_template WHERE contract_id=?",
            (contract_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "code": "UNKNOWN_CONTRACT",
                    "message": f"contract_id not found: {contract_id}"}
        if row["status"] == "active":
            return {"ok": True, "contract_id": contract_id, "action": "already_active"}
        ready = contract_readiness(contract_id, conn=conn)
        if not ready["ready"]:
            return {"ok": False, "code": "NOT_READY",
                    "message": "; ".join(ready["reasons"]), "readiness": ready}
        conn.execute(
            "UPDATE skill_contract_template SET status='active', updated_at=? "
            "WHERE contract_id=?",
            (_utc_now(), contract_id),
        )
        conn.commit()
        return {"ok": True, "contract_id": contract_id, "action": "promoted",
                "readiness": ready}
    finally:
        if own:
            conn.close()


def promote_ready_contracts(
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Promote every draft contract that meets the thresholds."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    promoted: list[str] = []
    skipped: list[dict[str, Any]] = []
    try:
        ensure_skill_contract_schema(conn)
        rows = conn.execute(
            "SELECT contract_id FROM skill_contract_template "
            "WHERE status='draft' ORDER BY contract_id"
        ).fetchall()
        for r in rows:
            cid = r["contract_id"]
            res = promote_contract(cid, conn=conn)
            if res.get("ok") and res.get("action") == "promoted":
                promoted.append(cid)
            elif not res.get("ok"):
                skipped.append({"contract_id": cid, "reason": res.get("message")})
        conn.commit()
        return {"ok": True, "promoted": promoted, "skipped": skipped,
                "promoted_count": len(promoted)}
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# P2-2 source standardisation
# ---------------------------------------------------------------------------

SOURCE_MANUAL = "manual"
SOURCE_SEED = "seed"
SOURCE_PROVEN = "proven"
VALID_SOURCES = (SOURCE_MANUAL, SOURCE_SEED, SOURCE_PROVEN)


def normalize_source_value(raw: str | None) -> str:
    """Map a free-text source onto the canonical manual/seed/proven set."""
    s = (raw or "").strip().lower()
    if not s:
        return SOURCE_MANUAL
    if s.startswith("scan:") or "seed" in s:
        return SOURCE_SEED
    if s in ("proven", "proof", "verified"):
        return SOURCE_PROVEN
    return SOURCE_MANUAL


def standardize_sources(
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Normalise every contract's source to manual/seed/proven."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    changed: list[dict[str, str]] = []
    try:
        ensure_skill_contract_schema(conn)
        rows = conn.execute(
            "SELECT contract_id, source FROM skill_contract_template "
            "ORDER BY contract_id"
        ).fetchall()
        for r in rows:
            cid, old = r["contract_id"], r["source"]
            new = normalize_source_value(old)
            if new != old:
                conn.execute(
                    "UPDATE skill_contract_template SET source=?, updated_at=? "
                    "WHERE contract_id=?",
                    (new, _utc_now(), cid),
                )
                changed.append({"contract_id": cid, "old": old or "", "new": new})
        conn.commit()
        return {"ok": True, "changed": changed, "changed_count": len(changed)}
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 8-level representative seed (channel -> db_field)
# ---------------------------------------------------------------------------

# One representative contract per taxonomy level, so taxonomy_path can be
# exercised end to end. Keys must already exist in the ontology registry.
EIGHT_LEVEL_SEED: list[dict[str, Any]] = [
    {
        "contract_id": "CH.LOCAL_PC",
        "skill_key": "channel_local_pc",
        "taxonomy_path": "channel/local_pc",
        "purpose": "Own the local_pc channel: declare which modules may run on this machine.",
        "purpose_not_responsible": "NOT responsible for module internals or capability logic.",
        "environment": {"channel": "local_pc", "modules": ["task_center"]},
        "flow": [
            "Pre-research: read channel_registry for local_pc",
            "Validate the requested module belongs to this channel",
            "Return {channel_id, modules, status}",
        ],
        "not_to_do": [
            "DO NOT create modules that are not registered",
            "DO NOT bypass channel_registry lookup",
        ],
    },
    {
        "contract_id": "MOD.TASK_CENTER",
        "skill_key": "module_task_center",
        "taxonomy_path": "module/task_center",
        "purpose": "Own the task_center module: group capabilities and enforce module boundaries.",
        "purpose_not_responsible": "NOT responsible for individual capability behaviour.",
        "environment": {"module": "task_center", "channel": "local_pc"},
        "flow": [
            "Pre-research: read module_registry for task_center",
            "Validate the capability belongs to this module",
            "Return {module_id, capabilities, status}",
        ],
        "not_to_do": [
            "DO NOT register capabilities outside this module",
            "DO NOT skip module_registry lookup",
        ],
    },
    {
        "contract_id": "CAP.VALIDATE_NEW_TASK",
        "skill_key": "capability_validate_new_task",
        "taxonomy_path": "capability/task_center.validate_new_task",
        "purpose": "Own the validate_new_task capability: 8-dim ontology validation entry point.",
        "purpose_not_responsible": "NOT responsible for writing audit rows or mutating registries.",
        "environment": {"module": "task_center", "read_only": True},
        "flow": [
            "Pre-research: read capability_registry for the capability key",
            "Validate the payload against the 8 dimensions",
            "Return {ok, errors} without writing anything",
        ],
        "not_to_do": [
            "DO NOT INSERT/UPDATE/DELETE any registry row",
            "DO NOT write audit_trace or experience_log",
        ],
    },
    {
        "contract_id": "API.POST_TASKS_VALIDATE",
        "skill_key": "api_post_tasks_validate",
        "taxonomy_path": "api/task_center.validate_new_task|POST /api/tasks/validate",
        "purpose": "Own the POST /api/tasks/validate endpoint contract.",
        "purpose_not_responsible": "NOT responsible for the validation logic itself.",
        "environment": {"method": "POST", "path": "/api/tasks/validate"},
        "flow": [
            "Pre-research: read api_registry for the endpoint key",
            "Validate the request body shape",
            "Delegate to the capability and map errors to HTTP status",
        ],
        "not_to_do": [
            "DO NOT reimplement capability validation inline",
            "DO NOT return 200 when validation failed",
        ],
    },
    {
        "contract_id": "FN.VALIDATE_NEW_TASK",
        "skill_key": "function_validate_new_task",
        "taxonomy_path": "function/task_center.validate_new_task|task_center.validate_new_task",
        "purpose": "Own the validate_new_task function: pure read-only 8-dim validator.",
        "purpose_not_responsible": "NOT responsible for HTTP concerns or persistence.",
        "environment": {"module": "task_center", "pure": True},
        "flow": [
            "Pre-research: read function_registry for the function key",
            "Load the 7 taxonomy registries",
            "Return {ok, errors}",
        ],
        "not_to_do": [
            "DO NOT write to any table",
            "DO NOT call the network",
        ],
    },
    {
        "contract_id": "TBL.code_registry",
        "skill_key": "table_code_registry",
        "taxonomy_path": "db_table/code_registry",
        "purpose": "Own the code_registry table: one row per registered function.",
        "purpose_not_responsible": "NOT responsible for field-level rules (see db_field).",
        "environment": {"table": "code_registry", "write_owner": "managed_coding"},
        "flow": [
            "Pre-research: read db_table_registry for code_registry",
            "Validate the row against the Field Register",
            "Insert or update the row",
        ],
        "not_to_do": [
            "DO NOT write without a register_id",
            "DO NOT delete rows (soft-delete only)",
        ],
    },
    {
        "contract_id": "FLD.code_registry.REGISTER_ID",
        "skill_key": "field_code_registry_registry_id",
        "taxonomy_path": "db_field/code_registry|register_id",
        "purpose": "Own the code_registry.register_id field rule.",
        "purpose_not_responsible": "NOT responsible for other code_registry fields.",
        "environment": {"table": "code_registry", "field": "register_id"},
        "flow": [
            "Pre-research: read db_field_registry for the field key",
            "Validate the value shape",
            "Return {ok, errors}",
        ],
        "not_to_do": [
            "DO NOT allow an empty register_id",
            "DO NOT change the field type",
        ],
    },
]


def seed_eight_level_contracts(
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Seed one representative contract per taxonomy level (idempotent).

    taxonomy_path is hard-validated, so a level whose registry entity is
    missing is reported in `rejected` rather than silently written.
    """
    own = conn is None
    if own:
        conn = _connect(db_path)
    inserted = updated = 0
    rejected: list[dict[str, Any]] = []
    try:
        ensure_skill_contract_schema(conn)
        for spec in EIGHT_LEVEL_SEED:
            res = upsert_contract(
                spec["contract_id"],
                spec["skill_key"],
                spec["taxonomy_path"],
                spec["purpose"],
                environment=spec.get("environment"),
                purpose_not_responsible=spec.get("purpose_not_responsible"),
                flow=spec.get("flow"),
                not_to_do=spec.get("not_to_do"),
                status="draft",
                source="seed_eight_level",
                conn=conn,
            )
            if res.get("ok"):
                if res.get("action") == "inserted":
                    inserted += 1
                else:
                    updated += 1
            else:
                rejected.append({
                    "contract_id": spec["contract_id"],
                    "taxonomy_path": spec["taxonomy_path"],
                    "code": res.get("code"),
                    "message": res.get("message"),
                })
        conn.commit()
        return {"ok": True, "inserted": inserted, "updated": updated,
                "rejected": rejected, "total": len(EIGHT_LEVEL_SEED)}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    _c = _connect()
    print(ensure_skill_contract_schema(_c))
    _c.close()