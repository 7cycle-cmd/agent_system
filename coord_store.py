"""SQLite store for click target coordinates + fault error text.

SSOT for x/y/action used by Mouse Spot / OpenClaw click scripts.
Catalog names/logos may still live in mouse_spot_targets.json.
Rows carry catalog target_id (e.g. t_1789... or doubao) for unique cards.

Also hosts the skill-scanned `task` entity table (upsert/load for HTML render).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent / "coords.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS target_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id TEXT,
    target_logo TEXT,
    target_name TEXT NOT NULL,
    x INTEGER NOT NULL DEFAULT 0,
    y INTEGER NOT NULL DEFAULT 0,
    action TEXT DEFAULT 'click',
    isactive INTEGER DEFAULT 1,
    llm_score INTEGER DEFAULT 100,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_TASK_SQL = """
CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    display_task_id TEXT NOT NULL,
    name TEXT,
    final_verdict TEXT DEFAULT 'incomplete',
    ingested TEXT DEFAULT 'no',
    modified_files TEXT,
    qc_summary TEXT,
    reason TEXT,
    artifacts TEXT,
    schema TEXT,
    session_id TEXT,
    operator TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_field_registry_SQL = """
CREATE TABLE IF NOT EXISTS field_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    field_type TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(task_id, field_name)
)
"""

_CREATE_LLM_100_RUN_SQL = """
CREATE TABLE IF NOT EXISTS proof_run (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type     TEXT    NOT NULL,
    ref_tag         TEXT    NOT NULL,
    entity_name     TEXT    NOT NULL,
    round_no        INTEGER NOT NULL,
    value           TEXT    NOT NULL,
    oracle_answer   TEXT    NOT NULL,
    llm_answer      TEXT    NOT NULL,
    win             INTEGER NOT NULL,
    failure_reason  TEXT,
    rule_version    INTEGER NOT NULL DEFAULT 1,
    model           TEXT,
    skill_id        TEXT,
    linked_trace_id TEXT,
    -- WHICH DIMENSION this round tested (2026-09-22).
    --
    -- The user: "as prompt is generatot by formula, so we can have enough detail
    -- to help for improve formula design".
    --
    -- A prompt is built from `prompt_wording` dimensions (context / criterion /
    -- negation / output). Without this column a failure has a `failure_reason`
    -- but NO dimension, so the formula can only be improved from OPINION. With
    -- it, a failure points at the SAME dimension the prompt was built from.
    --
    -- `NOT NULL DEFAULT 'NA'` follows the no_null standard.
    dim_key         TEXT    NOT NULL DEFAULT 'NA',
    -- WHICH LAYER this round tested (2026-09-22).
    --
    -- The user: "proof run report table will have 2 field / and these 2 field can
    -- help us to have clear graph for everything / when debug, it is key!!!!
    -- special ontology!!!!!!"
    --
    -- `dim_key` says WHICH 5W1H dimension a round tested. It does NOT say whether
    -- the round asked a TDD question (does the VALUE satisfy the rule) or an
    -- ONTOLOGY question (does the THING exist / is it registered / is it active).
    -- Without that split a failure cannot be attributed to a LAYER, so "is the
    -- prompt wrong or is the ontology wrong?" is unanswerable.
    --
    -- The vocabulary is DERIVED from `register_approval.STAGES` (see
    -- `question_flow.LAYERS`), so the two cannot drift.
    layer_key       TEXT    NOT NULL DEFAULT 'NA',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ref_tag, round_no, rule_version)
);
CREATE INDEX IF NOT EXISTS idx_proof_run_streak
  ON proof_run(ref_tag, rule_version, id DESC);
"""

# THE RENAME (2026-09-22). The user: "100 run need to rename".
#
# WHY `proof_run` AND NOT `100_run`: the count is NOT fixed. MEASURED:
# `llm_100_run_harness.STREAK_TARGET` was a hard-coded 110 (22 options x 5), and
# `streak_target_for` now DERIVES it from the prompt's own `threshold`. So "100"
# names a number the system does not have — the same defect the user named for
# `name_registry` -> `terminology_registry`.
#
# WHAT IT IS: a run that PROVES a claim. Each round records `oracle_answer` (the
# ground truth), `llm_answer`, and `win`. A streak of wins is the EVIDENCE that
# `is_active=1` is earned rather than asserted.
#
# THE OLD NAME IS KEPT AS A VIEW, so 305 references across 55 files keep working
# while the rename lands. A view is not a second table: it cannot drift, because
# it has no storage of its own.
_LLM_100_RUN_COMPAT_VIEW_SQL = """
CREATE VIEW IF NOT EXISTS llm_100_run AS SELECT * FROM proof_run;
"""

_CREATE_CHECK_SQL = """
CREATE TABLE IF NOT EXISTS check_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    session_id TEXT,
    hash_chain TEXT,
    detail TEXT,
    verdict TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1
)
"""

_CREATE_SUBCATALOG_SQL = """
CREATE TABLE IF NOT EXISTS subcatalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (catalog_id) REFERENCES catalog(id),
    UNIQUE(catalog_id, name)
)
"""

_CREATE_FORMAT_TEMPLATE_SQL = """
CREATE TABLE IF NOT EXISTS format_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_setting_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    instruction TEXT NOT NULL,
    mode TEXT DEFAULT 'ask',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active INTEGER DEFAULT 1
)
"""

# DB-driven popup target AREAS + confirm checklist (user spec 2026-09-19).
# Unlike target_points (x,y = CENTER only), each row stores the full AREA
# (X1,Y1)-(X2,Y2); cx,cy is DERIVED so center and area can never disagree.
# checklist_confirm = 'yes'|'no' per target; f_perm_click.py --set-native
# ABORTS the click unless every active target is 'yes'.
# id is INTEGER PRIMARY KEY AUTOINCREMENT and rows are written with plain
# INSERT/UPDATE keyed by (popup_id, target_id) — never a TEXT primary key.
_CREATE_TARGET_AREA_SQL = """
CREATE TABLE IF NOT EXISTS target_area (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id         TEXT NOT NULL,
    popup_id          TEXT NOT NULL DEFAULT 'perm_picker',
    label             TEXT,
    x1 INTEGER NOT NULL DEFAULT 0,
    y1 INTEGER NOT NULL DEFAULT 0,
    x2 INTEGER NOT NULL DEFAULT 0,
    y2 INTEGER NOT NULL DEFAULT 0,
    cx INTEGER NOT NULL DEFAULT 0,
    cy INTEGER NOT NULL DEFAULT 0,
    checklist_confirm TEXT NOT NULL DEFAULT 'no',
    confirm_reason    TEXT,
    confirmed_at      TIMESTAMP,
    isactive          INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(popup_id, target_id)
)
"""

# Stage-1 three-line verdict format (fallback + seed SSOT text)
DEFAULT_VERDICT_3LINE_INSTRUCTION = """
【強制輸出規則，必須嚴格跟從，不可省略、不可加額外文字】
你嘅回覆只需要包含以下三行，唔好加多餘標題、解釋、markdown。
VERDICT: PASS / FAIL / INCOMPLETE
DETAIL: 簡短一句描述結果
ERROR: none 或者錯誤原因

- VERDICT 只可選 PASS、FAIL、INCOMPLETE
- 如果冇錯誤，ERROR 寫 none
"""

# Chat Center identity CONFIRM template (user spec 2026-09-20). The identity
# block is pasted first; this follow-up asks the model to ECHO the block back,
# so "did the model actually receive the identity?" is provable instead of
# assumed. Kept as a separate row so the paste step and the confirm step can be
# edited independently.
DEFAULT_WORKER_IDENTITY_CONFIRM_INSTRUCTION = """| sort | Field | Value |
|--|-------|-------|
| 1 | SESSION_ID | {echo the SESSION_ID you received} |
| 2 | MODEL | {echo the MODEL you received} |
| 3 | TASK_ID | {echo the TASK_ID you received, else empty} |
| 4 | CHAT_ID | {echo the CHAT_ID you received, else empty} |
| 5 | CHAT_SHA256 | {echo the CHAT_SHA256 you received, else empty} |
| 6 | CONFIRM | YES / NO |

只回覆上面嘅表，唔好加其他文字。CONFIRM = YES 代表你已收到 identity block。
"""

_POINT_COLS = (
    "id, target_id, target_logo, target_name, x, y, action, isactive, llm_score, error, "
    "created_at, updated_at"
)

_TASK_COLS = (
    "id, task_id, display_task_id, name, final_verdict, ingested, modified_files, "
    "qc_summary, reason, artifacts, schema, session_id, operator, created_at, updated_at"
)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(target_points)").fetchall()}
    if "error" not in cols:
        conn.execute("ALTER TABLE target_points ADD COLUMN error TEXT")
    if "action" not in cols:
        conn.execute(
            "ALTER TABLE target_points ADD COLUMN action TEXT DEFAULT 'click'"
        )
    if "target_id" not in cols:
        conn.execute("ALTER TABLE target_points ADD COLUMN target_id TEXT")
    # A target_id may have MULTIPLE rows (each verified success point); the
    # learned range is MIN/MAX over those rows. So the index must be NON-unique.
    # Drop any legacy UNIQUE index that would block multiple rows per target.
    conn.execute(
        "DROP INDEX IF EXISTS idx_target_points_target_id"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_target_points_target_id
        ON target_points(target_id)
        """
    )


def init_db(db_path: Path | None = None) -> Path:
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_SQL)
        _ensure_columns(conn)
        conn.execute(_CREATE_TASK_SQL)
        conn.execute(_CREATE_field_registry_SQL)
        conn.executescript(_CREATE_LLM_100_RUN_SQL)
        conn.executescript(_LLM_100_RUN_COMPAT_VIEW_SQL)
        conn.execute(_CREATE_CATALOG_SQL)
        conn.execute(_CREATE_SUBCATALOG_SQL)
        conn.execute(_CREATE_TARGET_AREA_SQL)
        # 6-STEP target capture tables (target_position replaces target_area).
        conn.execute(_CREATE_TARGET_POSITION_SQL)
        conn.execute(_CREATE_TARGET_AUTOCAL_SQL)
        conn.execute(_CREATE_TARGET_CAPTURE_SESSION_SQL)
        conn.executescript(_CREATE_TARGET_CAPTURE_LOG_SQL)
    return path


def init_task_table(db_path: Path | None = None) -> Path:
    """Create the skill-scanned task entity table if missing."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_TASK_SQL)
    return path


def init_field_registry_table(db_path: Path | None = None) -> Path:
    """Create the field_registry schema table if missing."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_field_registry_SQL)
    return path


def init_llm_100_run_table(db_path: Path | None = None) -> Path:
    """Create the proof_run table (and the compat view) if missing.

    The function NAME is kept so 305 references keep working; the TABLE it
    creates is `proof_run`.

    DEFECT FOUND BY RUNNING THE TESTS (2026-09-22): this created `proof_run` but
    did NOT rename an EXISTING `llm_100_run` table, so a DB that predates the
    rename ended up with BOTH — and `migrate_llm_100_run_model` then failed with
    `no such table: proof_run` because the old table was still the real one. The
    rename must run FIRST, so there is exactly one table.
    """
    path = Path(db_path) if db_path else DB_PATH
    migrate_proof_run_rename(path)
    with _conn(path) as conn:
        conn.executescript(_CREATE_LLM_100_RUN_SQL)
        conn.executescript(_LLM_100_RUN_COMPAT_VIEW_SQL)
    # The additive columns, for a DB whose `proof_run` predates them. A
    # `CREATE TABLE IF NOT EXISTS` does NOTHING on an existing table, so the
    # columns must be added by migration — the same defect the `skill_lesson`
    # rating index hit (`db_schema.py:2385`).
    migrate_llm_100_run_dim_key(path)
    migrate_proof_run_layer_key(path)
    return path


def migrate_proof_run_rename(db_path: Path | None = None) -> dict[str, Any]:
    """Rename `llm_100_run` (a TABLE) to `proof_run`, then add the compat view.

    WHY A RENAME AND NOT A COPY: the 2615 existing rounds are the ONLY evidence
    the system has, so they must survive byte-for-byte. `ALTER TABLE ... RENAME
    TO` moves the storage; a copy would risk a partial write.

    IDEMPOTENT, and it reports WHICH case it found:
      * `already_renamed` — `proof_run` exists and `llm_100_run` is a VIEW
      * `renamed`         — `llm_100_run` was a TABLE and was renamed
      * `created`         — neither existed, so `proof_run` was created
    """
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        def kind(name: str) -> str | None:
            r = conn.execute("SELECT type FROM sqlite_master WHERE name=?",
                             (name,)).fetchone()
            return str(r[0]) if r else None

        k_old, k_new = kind("llm_100_run"), kind("proof_run")
        if k_new == "table" and k_old == "view":
            return {"action": "already_renamed", "rows": _count(conn, "proof_run")}
        if k_old == "table":
            conn.execute("ALTER TABLE llm_100_run RENAME TO proof_run")
            conn.execute("DROP VIEW IF EXISTS llm_100_run")
            conn.executescript(_LLM_100_RUN_COMPAT_VIEW_SQL)
            conn.commit()
            return {"action": "renamed", "rows": _count(conn, "proof_run")}
        if k_new == "table":
            conn.executescript(_LLM_100_RUN_COMPAT_VIEW_SQL)
            conn.commit()
            return {"action": "view_added", "rows": _count(conn, "proof_run")}
        conn.executescript(_CREATE_LLM_100_RUN_SQL)
        conn.executescript(_LLM_100_RUN_COMPAT_VIEW_SQL)
        conn.commit()
        return {"action": "created", "rows": 0}


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0])
    except sqlite3.Error:
        return -1


def migrate_llm_100_run_model(db_path: Path | None = None, backfill_model: str = "qwen2.5:7b-instruct") -> int:
    """Add `model` column if missing and backfill existing rows (keeps audit).

    Reads `proof_run` (the renamed table). The function NAME is kept so existing
    callers keep working.

    DEFECT FOUND BY RUNNING THE TESTS (2026-09-22): a v1 DB has `llm_100_run` as
    a TABLE, so reading `proof_run` failed with `no such table`. The rename runs
    FIRST, so this works on a v1 DB and on a renamed one alike.
    """
    path = Path(db_path) if db_path else DB_PATH
    migrate_proof_run_rename(path)
    with _conn(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(proof_run)").fetchall()]
        if "model" not in cols:
            conn.execute("ALTER TABLE proof_run ADD COLUMN model TEXT")
        n = conn.execute(
            "UPDATE proof_run SET model = ? WHERE model IS NULL",
            (backfill_model,),
        ).rowcount
    return n


def migrate_llm_100_run_dim_key(db_path: Path | None = None) -> int:
    """Add `dim_key` if missing, and backfill existing rows to 'NA'.

    WHY (user, 2026-09-22): "as prompt is generatot by formula, so we can have
    enough detail to help for improve formula design". Without a dimension a
    failure has a `failure_reason` but no ATTRIBUTION, so the formula can only be
    improved from opinion.

    The backfill is 'NA', NOT a guess: the existing 2615 rows were run before the
    column existed, so their dimension is genuinely UNKNOWN. Writing a plausible
    value would be inventing evidence — the same rule `no_null` follows.
    """
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(proof_run)").fetchall()]
        if "dim_key" not in cols:
            conn.execute("ALTER TABLE proof_run ADD COLUMN dim_key TEXT "
                         "NOT NULL DEFAULT 'NA'")
        n = conn.execute(
            "UPDATE proof_run SET dim_key = 'NA' WHERE dim_key IS NULL OR "
            "TRIM(dim_key) = ''").rowcount
    return n


def migrate_proof_run_layer_key(db_path: Path | None = None) -> int:
    """Add `layer_key` if missing, and backfill existing rows to 'NA'.

    WHY (user, 2026-09-22): "proof run report table will have 2 field / and these
    2 field can help us to have clear graph for everything / when debug, it is
    key!!!! special ontology!!!!!!"

    `dim_key` says WHICH 5W1H dimension a round tested. `layer_key` says WHICH
    KIND of truth it tested — TDD (the value) or ontology (the thing). Both are
    needed to attribute a failure, and the second is the one the user called out.

    The backfill is 'NA', NOT a guess: the existing rows were run before the
    column existed, so their layer is genuinely UNKNOWN. Writing a plausible
    value would be inventing evidence — the same rule `no_null` follows.

    DEFECT FOUND BY RUNNING IT (2026-09-22): this failed on the LIVE DB with
    `no such table: proof_run`, because the live DB still holds `llm_100_run` as
    a TABLE and the rename had not run. Every other migration in this module runs
    `migrate_proof_run_rename` FIRST for exactly this reason; this one must too.
    """
    path = Path(db_path) if db_path else DB_PATH
    migrate_proof_run_rename(path)
    with _conn(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(proof_run)").fetchall()]
        if "layer_key" not in cols:
            conn.execute("ALTER TABLE proof_run ADD COLUMN layer_key TEXT "
                         "NOT NULL DEFAULT 'NA'")
        n = conn.execute(
            "UPDATE proof_run SET layer_key = 'NA' WHERE layer_key IS NULL OR "
            "TRIM(layer_key) = ''").rowcount
    return n


def migrate_proof_run_value_type(db_path: Path | None = None) -> int:
    """Add `value_type` and `proof_run_registry_id` if missing. Idempotent.

    WHY (human, 2026-09-26): "no matter what, for proof run target is value!" /
    "and value format is the key for 7B-intract or 7B vl" / "both side need to
    have same language".

    MEASURED DEFECT: `proof_run` had NO `value_type`, so the format of a value was
    re-derived by parsing on every read, and the ROUTE was hardcoded
    (`llm_100_run_harness.py:41  JUDGE_SERVICE_KEY = "llm.text"`). A run whose
    value is an image would still be judged by `qwen2.5:7b-instruct`, which
    cannot see it.

    MEASURED DEFECT: `proof_run` had NO `db_field_id` (and neither did
    `field_tdd_rule`), so a round could not say WHICH column it measured. The
    join key did not exist.

    The backfill is 'NA', NOT a guess: the existing 2995 rows were run before the
    columns existed, so their format and their column are genuinely UNKNOWN.
    Writing a plausible value would be inventing evidence — the same rule
    `no_null` follows. NO EXISTING ROW IS REWRITTEN beyond this 'NA' backfill.
    """
    path = Path(db_path) if db_path else DB_PATH
    migrate_proof_run_rename(path)
    with _conn(path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(proof_run)").fetchall()]
        if "value_type" not in cols:
            conn.execute("ALTER TABLE proof_run ADD COLUMN value_type TEXT "
                         "NOT NULL DEFAULT 'NA'")
        if "proof_run_registry_id" not in cols:
            conn.execute("ALTER TABLE proof_run ADD COLUMN proof_run_registry_id "
                         "INTEGER NOT NULL DEFAULT 0")
        n = conn.execute(
            "UPDATE proof_run SET value_type = 'NA' WHERE value_type IS NULL OR "
            "TRIM(value_type) = ''").rowcount
    return n


def _json_list_dump(value: Any) -> str:
    if value is None or value == "":
        return "[]"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return "[]"
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
        return json.dumps([s], ensure_ascii=False)
    return json.dumps([value], ensure_ascii=False)


def _json_list_load(raw: Any) -> list[Any]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return parsed
            return [parsed]
        except json.JSONDecodeError:
            return [s]
    return [raw]


def _row_to_task_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    files = _json_list_load(d.get("modified_files"))
    arts = _json_list_load(d.get("artifacts"))
    d["modified_files"] = files
    d["artifacts"] = arts
    # Bridges for _render_html without changing the renderer.
    tid = str(d.get("task_id") or "").strip()
    disp = str(d.get("display_task_id") or tid).strip() or tid
    d["display_task_id"] = disp
    d["display_track_id"] = disp
    d["files_modified"] = files
    if d.get("name") is not None:
        d["item_name"] = d.get("name")
    return d


def upsert_task(
    task_dict: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert or update one task row keyed by business task_id."""
    if not isinstance(task_dict, dict):
        raise TypeError("task_dict must be a dict")
    tid = str(task_dict.get("task_id") or "").strip()
    if not tid:
        raise ValueError("task_id required")
    display = str(
        task_dict.get("display_task_id")
        or task_dict.get("display_track_id")
        or tid
    ).strip() or tid
    name = task_dict.get("name")
    if name is not None:
        name = str(name)
    final_verdict = str(task_dict.get("final_verdict") or "incomplete")
    ingested = str(task_dict.get("ingested") or "no")
    modified_files = _json_list_dump(
        task_dict.get("modified_files", task_dict.get("files_modified"))
    )
    qc_summary = task_dict.get("qc_summary")
    if qc_summary is not None:
        qc_summary = str(qc_summary)
    reason = task_dict.get("reason")
    if reason is not None:
        reason = str(reason)
    artifacts = _json_list_dump(task_dict.get("artifacts"))
    schema = task_dict.get("schema")
    if schema is not None:
        schema = str(schema)
    session_id = task_dict.get("session_id")
    if session_id is not None:
        session_id = str(session_id)
    operator = task_dict.get("operator")
    if operator is not None:
        operator = str(operator)
    ts = _now()
    init_task_table(db_path)
    with _conn(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM task WHERE task_id = ? LIMIT 1",
            (tid,),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE task
                SET display_task_id = ?, name = ?, final_verdict = ?, ingested = ?,
                    modified_files = ?, qc_summary = ?, reason = ?, artifacts = ?,
                    schema = ?, session_id = ?, operator = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    display,
                    name,
                    final_verdict,
                    ingested,
                    modified_files,
                    qc_summary,
                    reason,
                    artifacts,
                    schema,
                    session_id,
                    operator,
                    ts,
                    tid,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO task (
                    task_id, display_task_id, name, final_verdict, ingested,
                    modified_files, qc_summary, reason, artifacts, schema,
                    session_id, operator, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tid,
                    display,
                    name,
                    final_verdict,
                    ingested,
                    modified_files,
                    qc_summary,
                    reason,
                    artifacts,
                    schema,
                    session_id,
                    operator,
                    ts,
                ),
            )
        row = conn.execute(
            f"SELECT {_TASK_COLS} FROM task WHERE task_id = ? LIMIT 1",
            (tid,),
        ).fetchone()
    if row is None:
        raise RuntimeError(f"upsert_task failed to read back task_id={tid!r}")
    return _row_to_task_dict(row)


def load_all_tasks(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Load every task row as dicts suitable for _render_html."""
    init_task_table(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            f"SELECT {_TASK_COLS} FROM task ORDER BY id ASC"
        ).fetchall()
    return [_row_to_task_dict(r) for r in rows]


def _point_to_api_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Map a target_points row to an API dict (isactive int -> is_active bool)."""
    d = {k: row[k] for k in row.keys()}
    d["is_active"] = bool(d.pop("isactive", 0))
    return d


def init_table(db_path: Path | None = None) -> Path:
    """Create the target_points table if missing (coord API init)."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_SQL)
        _ensure_columns(conn)
        conn.execute(_CREATE_CHECK_SQL)
        conn.execute(_CREATE_TARGET_AREA_SQL)
    return path


def list_all(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return every coord row as API dicts."""
    init_table(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM target_points ORDER BY id ASC"
        ).fetchall()
    return [_point_to_api_dict(r) for r in rows]


def get_by_id(item_id: int, *, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return one coord row by id, or None."""
    init_table(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_points WHERE id = ?", (int(item_id),)
        ).fetchone()
    if row is None:
        return None
    return _point_to_api_dict(row)


def create(data: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    """Insert a new coord row and return it as an API dict."""
    name = str(data.get("target_name") or "").strip()
    if not name:
        raise ValueError("target_name required")
    x = int(data.get("x", 0))
    y = int(data.get("y", 0))
    action = str(data.get("action") or "click").strip() or "click"
    is_active = 1 if data.get("is_active", True) else 0
    error = data.get("error")
    init_table(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        cur = conn.execute(
            """
            INSERT INTO target_points
                (target_name, x, y, action, isactive, error, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (name, x, y, action, is_active, error, ts),
        )
        row_id = int(cur.lastrowid)
    return get_by_id(row_id, db_path=db_path)


def update(
    item_id: int,
    data: dict[str, Any],
    *,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Update a coord row by id; return the updated API dict or None."""
    name = str(data.get("target_name") or "").strip()
    if not name:
        raise ValueError("target_name required")
    x = int(data.get("x", 0))
    y = int(data.get("y", 0))
    action = str(data.get("action") or "click").strip() or "click"
    is_active = 1 if data.get("is_active", True) else 0
    error = data.get("error")
    init_table(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE target_points
            SET target_name=?, x=?, y=?, action=?, isactive=?, error=?, updated_at=?
            WHERE id=?
            """,
            (name, x, y, action, is_active, error, ts, int(item_id)),
        )
        if cur.rowcount == 0:
            return None
    return get_by_id(item_id, db_path=db_path)


def delete(item_id: int, *, db_path: Path | None = None) -> bool:
    """Delete a coord row by id; return True if a row was removed."""
    init_table(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute("DELETE FROM target_points WHERE id=?", (int(item_id),))
        return cur.rowcount > 0


def record_success(
    target_id: str,
    x: int,
    y: int,
    action: str = "click",
    *,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Record a SUCCESSFUL py click. Same target_id with different X/Y = range.
    Called by any py script after a verified successful click (clipboard
    changed, element appeared, etc.). Returns the inserted row."""
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError("target_id required")
    init_table(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        cur = conn.execute(
            """
            INSERT INTO target_points
                (target_id, target_name, x, y, action, isactive, updated_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (tid, tid, int(x), int(y), action, 1, ts),
        )
        row_id = int(cur.lastrowid)
    return get_by_id(row_id, db_path=db_path)


def get_target_range(
    target_id: str, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Return min/max X,Y for all active rows with the given target_id.
    Same target_id with different X/Y = the range of proven click positions.
    Returns {target_id, x_min, x_max, y_min, y_max, count} or None."""
    tid = str(target_id or "").strip()
    if not tid:
        return None
    init_table(db_path)
    with _conn(db_path) as conn:
        r = conn.execute(
            """
            SELECT MIN(x) x_min, MAX(x) x_max, MIN(y) y_min, MAX(y) y_max, COUNT(*) cnt
            FROM target_points WHERE target_id=? AND isactive=1
            """,
            (tid,),
        ).fetchone()
    if r is None or r["cnt"] == 0:
        return None
    return {
        "target_id": tid,
        "x_min": r["x_min"], "x_max": r["x_max"],
        "y_min": r["y_min"], "y_max": r["y_max"],
        "count": r["cnt"],
    }


def list_grouped(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all targets grouped by target_id with computed ranges.
    Each entry: {target_id, target_name, logo, action, x_min, x_max, y_min,
    y_max, count, active, created_at, updated_at, points: [{id,x,y}...]}"""
    init_table(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT target_id, target_name, target_logo, action, x, y,
                   isactive, created_at, updated_at, id
            FROM target_points
            WHERE target_id IS NOT NULL AND target_id != ''
            ORDER BY target_id, id ASC
            """
        ).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        tid = r["target_id"]
        if tid not in groups:
            groups[tid] = {
                "target_id": tid,
                "target_name": r["target_name"],
                "logo": r["target_logo"] or "",
                "action": r["action"] or "click",
                "x_min": r["x"], "x_max": r["x"],
                "y_min": r["y"], "y_max": r["y"],
                "count": 0,
                "active": bool(r["isactive"]),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "points": [],
            }
        g = groups[tid]
        g["x_min"] = min(g["x_min"], r["x"])
        g["x_max"] = max(g["x_max"], r["x"])
        g["y_min"] = min(g["y_min"], r["y"])
        g["y_max"] = max(g["y_max"], r["y"])
        g["count"] += 1
        g["active"] = g["active"] or bool(r["isactive"])
        g["updated_at"] = max(g["updated_at"] or "", r["updated_at"] or "")
        g["points"].append({"id": r["id"], "x": r["x"], "y": r["y"]})
    return list(groups.values())


# ---- target_position: REPLACES target_area (6-STEP capture: rect + provenance) ----
# WHY THE CHECK CONSTRAINTS
# -------------------------
# The old `target_area` table documented "cx,cy is DERIVED" in a COMMENT and
# nothing enforced it. Measured 2026-09-20: the live `perm_pill` row holds
# rect (585,944)-(800,975), whose centre is (692,959), but stores cy=652 —
# 307px wrong — and it sat there undetected because a comment cannot fail.
# width/height were not stored at all, so nothing could disagree with them.
#
# These CHECKs make that class of defect UNREPRESENTABLE: an inconsistent width,
# height or centre cannot be INSERTed, so it cannot be read back later. A CHECK
# is a measurement that runs on every write instead of a note someone must trust.
#
# CONFIRMED FORMULA (user 2026-09-20): width = X2-X1, height = Y2-Y1.
#
# CROSS-DB NOTE: `source_id` references `source(id)` in **agent.db**, not
# coords.db. SQLite cannot enforce a FK across files, so this is a DOCUMENTED
# cross-DB reference and NOT a FOREIGN KEY. Declaring a fake FK would be worse
# than none: it would claim a guarantee the engine does not provide.
_CREATE_TARGET_POSITION_SQL = """
CREATE TABLE IF NOT EXISTS target_position (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL UNIQUE,
    source_id         INTEGER NOT NULL,
    source_kind       TEXT    NOT NULL CHECK (source_kind IN ('APP', 'URL')),
    source_ref        TEXT    NOT NULL,
    x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL, y2 INTEGER NOT NULL,
    width  INTEGER NOT NULL,
    height INTEGER NOT NULL,
    cx     INTEGER NOT NULL,
    cy     INTEGER NOT NULL,
    screenshot_id     TEXT,
    evidence_id       TEXT,
    cross_evidence_id TEXT,
    isactive   INTEGER NOT NULL DEFAULT 0 CHECK (isactive IN (0, 1)),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (x2 > x1 AND y2 > y1),
    CHECK (width  = x2 - x1),
    CHECK (height = y2 - y1),
    CHECK (cx = (x1 + x2) / 2),
    CHECK (cy = (y1 + y2) / 2)
)
"""

# ---- target_autocal_data: the auto-computed derived record (STEP 5/6 output) ----
_CREATE_TARGET_AUTOCAL_SQL = """
CREATE TABLE IF NOT EXISTS target_autocal_data (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER NOT NULL,
    name        TEXT    NOT NULL,
    width       INTEGER NOT NULL CHECK (width  > 0),
    height      INTEGER NOT NULL CHECK (height > 0),
    center_x    INTEGER NOT NULL,
    center_y    INTEGER NOT NULL,
    cross_evidence_id TEXT,
    isactive    INTEGER NOT NULL DEFAULT 0 CHECK (isactive IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (position_id) REFERENCES target_position (id) ON DELETE CASCADE
)
"""

# ---- target_capture_session: makes the 6-STEP wizard RESUMABLE ----
# WHY: a half-finished capture must never be mistakable for a target. Holding the
# current STEP + the captured coords in a row makes "where am I" DATA rather than
# page state that evaporates on reload — so an unfinished capture is visible and
# resumable instead of silently lost (or worse, registered).
_CREATE_TARGET_CAPTURE_SESSION_SQL = """
CREATE TABLE IF NOT EXISTS target_capture_session (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT    NOT NULL UNIQUE,
    step_no     INTEGER NOT NULL DEFAULT 1 CHECK (step_no BETWEEN 1 AND 6),
    name        TEXT,
    source_id   INTEGER,
    source_kind TEXT CHECK (source_kind IN ('APP', 'URL')),
    source_ref  TEXT,
    x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,
    screenshot_id TEXT,
    step_log    TEXT    NOT NULL DEFAULT '[]',
    status      TEXT    NOT NULL DEFAULT 'in_progress'
                CHECK (status IN ('in_progress', 'registered', 'abandoned')),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# ---- target_capture_log: the STEP 6 green-tick LOG ----
# Append-only by convention: a gate outcome is a fact about a moment, so a
# re-run writes a NEW row rather than rewriting history.
_CREATE_TARGET_CAPTURE_LOG_SQL = """
CREATE TABLE IF NOT EXISTS target_capture_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    register_id TEXT    NOT NULL,
    position_id INTEGER,
    step_no     INTEGER NOT NULL,
    step_name   TEXT    NOT NULL,
    status      TEXT    NOT NULL CHECK (status IN ('PASS', 'FAIL', 'UNKNOWN')),
    detail      TEXT,
    gate_json   TEXT    NOT NULL DEFAULT '{}',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_target_capture_log_reg
  ON target_capture_log (register_id, created_at DESC)
"""

# Step names for the 6-STEP flow. Kept as data so the wizard, the log and the
# middleware all label the same step identically.
TARGET_STEP_NAMES = {
    1: "capture_x1y1",
    2: "confirm_x1y1",
    3: "capture_x2y2",
    4: "confirm_rect",
    5: "gate_evidence",
    6: "gate_redcross_registry",
}

# ---- target_capture_evidence: ONE PROVABLE ARTEFACT PER STEP ----
# WHY A TABLE AND NOT JUST FILES IN A FOLDER
# ------------------------------------------
# A PNG on disk proves a FILE exists, not that STEP N produced it. Binding
# (session_key, step_no) -> evidence_id -> sha256 makes "step N has evidence"
# ANSWERABLE BY QUERY, and the hash makes the artefact tamper-evident. Without
# the binding a step could advance carrying a screenshot from some other step and
# nothing would notice — which is the failure this table exists to prevent.
#
# UNIQUE(session_key, step_no) is deliberate: one step has ONE artefact, so
# "which image is step 4's?" never has two answers.
_CREATE_TARGET_CAPTURE_EVIDENCE_SQL = """
CREATE TABLE IF NOT EXISTS target_capture_evidence (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT    NOT NULL,
    step_no     INTEGER NOT NULL CHECK (step_no BETWEEN 1 AND 6),
    evidence_id TEXT    NOT NULL,
    kind        TEXT    NOT NULL,
    png_path    TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    width       INTEGER,
    height      INTEGER,
    detail      TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (session_key, step_no)
)
"""


def init_target_tables(db_path: Path | None = None) -> Path:
    """Create the 6-STEP target tables if missing (idempotent)."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_TARGET_POSITION_SQL)
        conn.execute(_CREATE_TARGET_AUTOCAL_SQL)
        conn.execute(_CREATE_TARGET_CAPTURE_SESSION_SQL)
        conn.executescript(_CREATE_TARGET_CAPTURE_LOG_SQL)
        conn.executescript(_CREATE_TARGET_CAPTURE_EVIDENCE_SQL)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_target_position_source "
            "ON target_position (source_id, isactive)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_target_position_active "
            "ON target_position (isactive, name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_target_autocal_position "
            "ON target_autocal_data (position_id, isactive)"
        )
    return path


def compute_rect_metrics(x1: int, y1: int, x2: int, y2: int) -> dict[str, int]:
    """width/height/centre from the CONFIRMED formula. RAISES on a bad rect.

    Fail-closed on purpose: an inverted or degenerate rect is a MEASUREMENT
    ERROR, not something to tidy up. Clamping it would store a rect nobody
    measured — the same false-claim failure the table's CHECKs exist to stop.
    """
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(
            "degenerate rect (%d,%d)-(%d,%d): need x2>x1 and y2>y1"
            % (x1, y1, x2, y2)
        )
    return {
        "width": x2 - x1,          # CONFIRMED: width is X2 - X1
        "height": y2 - y1,         # CONFIRMED: height is Y2 - Y1 (not X2-X1)
        "cx": (x1 + x2) // 2,
        "cy": (y1 + y2) // 2,
    }


def register_id_for(position_id: int) -> str:
    """The green-tick register id: 'TGT-<auto-increment id>' (user spec 2026-09-20).

    No date in the string — the date lives in created_at. Keeping the id short
    makes it easy to write into check_trace / task_trace.
    """
    return "TGT-%d" % int(position_id)


def create_target_position(
    name: str,
    source_id: int,
    source_kind: str,
    source_ref: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    screenshot_id: str | None = None,
    evidence_id: str | None = None,
    cross_evidence_id: str | None = None,
    isactive: int = 0,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert ONE target. `isactive` defaults to 0 and only a gate may pass 1.

    The default is deliberate: a caller that forgets to prove the target gets an
    INACTIVE row, so an unproven rect can never be clicked by accident. The
    alternative default (1) makes "unproven but live" the silent case.
    """
    nm = str(name or "").strip()
    if not nm:
        raise ValueError("name required")
    kind = str(source_kind or "").strip().upper()
    if kind not in ("APP", "URL"):
        raise ValueError("source_kind must be APP or URL, got %r" % (source_kind,))
    ref = str(source_ref or "").strip()
    if not ref:
        raise ValueError("source_ref required (exe path for APP, url for URL)")
    try:
        sid = int(source_id)
    except (TypeError, ValueError):
        raise ValueError("source_id must be an integer, got %r" % (source_id,))
    if sid <= 0:
        raise ValueError("source_id must be a positive id, got %r" % (sid,))
    m = compute_rect_metrics(x1, y1, x2, y2)
    init_target_tables(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO target_position
                (name, source_id, source_kind, source_ref,
                 x1, y1, x2, y2, width, height, cx, cy,
                 screenshot_id, evidence_id, cross_evidence_id,
                 isactive, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (nm, sid, kind, ref,
             int(x1), int(y1), int(x2), int(y2),
             m["width"], m["height"], m["cx"], m["cy"],
             screenshot_id, evidence_id, cross_evidence_id,
             int(bool(isactive)), ts),
        )
        pid = int(cur.lastrowid)
    return get_target_position(pid, db_path=db_path) or {"id": pid}


def get_target_position(
    key: int | str, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Look up by integer id, or by the 'TGT-<id>' register id string."""
    init_target_tables(db_path)
    if isinstance(key, str):
        s = key.strip()
        if s.upper().startswith("TGT-"):
            try:
                key = int(s[4:])
            except ValueError:
                return None
        else:
            try:
                key = int(s)
            except ValueError:
                return None
    try:
        pid = int(key)
    except (TypeError, ValueError):
        return None
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_position WHERE id = ?", (pid,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["register_id"] = register_id_for(d["id"])
    return d


def list_target_positions(
    *, active_only: bool = True, source_id: int | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List targets, newest first. `active_only` reads the PROVEN flag."""
    init_target_tables(db_path)
    where: list[str] = []
    params: list[Any] = []
    if active_only:
        where.append("isactive = 1")
    if source_id is not None:
        where.append("source_id = ?")
        params.append(int(source_id))
    sql = "SELECT * FROM target_position"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    with _conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["register_id"] = register_id_for(d["id"])
        out.append(d)
    return out


def set_target_position_active(
    position_id: int, active: bool, *, reason: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Flip the PROVEN flag. Only the STEP 5/6 gates should call this with True.

    `reason` is accepted for symmetry with the old checklist and is recorded into
    the capture log by the caller — this function itself never decides.
    """
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            "UPDATE target_position SET isactive = ?, updated_at = ? WHERE id = ?",
            (int(bool(active)), _now(), int(position_id)),
        )
        if cur.rowcount == 0:
            return None
    return get_target_position(position_id, db_path=db_path)


def delete_target_position(
    position_id: int, *, db_path: Path | None = None, require_inactive: bool = True,
) -> dict[str, Any]:
    """Remove a target row AND the derived rows that reference it.

    WHY THIS EXISTS: a proof that registers a target into the PRODUCTION database
    leaves production state behind, which is not a proof — measured 2026-09-20,
    `_proof_task_capture.py` left TGT-3..TGT-6 active in the live coords.db. A
    cleanup path removes the excuse for not cleaning up.

    `require_inactive` defaults True so a MISTAKE cannot delete a proven target:
    an active row must be deactivated first, which is an explicit, loggable act.
    """
    init_target_tables(db_path)
    row = get_target_position(position_id, db_path=db_path)
    if not row:
        return {"ok": False, "error": "not found"}
    if require_inactive and int(row.get("isactive") or 0):
        return {"ok": False,
                "error": ("target %s is active (isactive=1); deactivate it first "
                          "— deleting a proven target must be deliberate"
                          % row.get("register_id"))}
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM target_autocal_data WHERE position_id = ?",
                     (int(position_id),))
        conn.execute("DELETE FROM target_capture_evidence WHERE session_key IN "
                     "(SELECT session_key FROM target_capture_session "
                     "WHERE name = ?)", (row.get("name"),))
        cur = conn.execute("DELETE FROM target_position WHERE id = ?",
                           (int(position_id),))
    return {"ok": True, "deleted": int(cur.rowcount),
            "register_id": row.get("register_id"), "name": row.get("name")}


def create_autocal_row(
    position_id: int, name: str, width: int, height: int,
    center_x: int, center_y: int, *, cross_evidence_id: str | None = None,
    isactive: int = 0, db_path: Path | None = None,
) -> dict[str, Any]:
    """Record the auto-computed derived data for a target (STEP 5/6 output)."""
    w, h = int(width), int(height)
    if w <= 0 or h <= 0:
        raise ValueError("width/height must be > 0, got %dx%d" % (w, h))
    init_target_tables(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO target_autocal_data
                (position_id, name, width, height, center_x, center_y,
                 cross_evidence_id, isactive, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (int(position_id), str(name), w, h, int(center_x), int(center_y),
             cross_evidence_id, int(bool(isactive)), ts),
        )
        rid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM target_autocal_data WHERE id = ?", (rid,)
        ).fetchone()
    return dict(row) if row else {"id": rid}


def get_autocal_for(position_id: int, *, active_only: bool = False,
                    db_path: Path | None = None) -> dict[str, Any] | None:
    """The newest autocal row for a target (None when never computed)."""
    init_target_tables(db_path)
    sql = "SELECT * FROM target_autocal_data WHERE position_id = ?"
    if active_only:
        sql += " AND isactive = 1"
    sql += " ORDER BY id DESC LIMIT 1"
    with _conn(db_path) as conn:
        row = conn.execute(sql, (int(position_id),)).fetchone()
    return dict(row) if row else None


def write_capture_log(
    register_id: str, step_no: int, status: str, detail: str = "",
    *, position_id: int | None = None, gate_json: str = "{}",
    db_path: Path | None = None,
) -> None:
    """Append one step outcome to the capture log. Never raises.

    Logging must not be able to break a capture run — a failed LOG write is
    reported, not thrown, so the step's own result still reaches the caller.
    """
    try:
        init_target_tables(db_path)
        st = str(status or "UNKNOWN").strip().upper()
        if st not in ("PASS", "FAIL", "UNKNOWN"):
            st = "UNKNOWN"
        name = TARGET_STEP_NAMES.get(int(step_no), "step_%s" % step_no)
        with _conn(db_path) as conn:
            conn.execute(
                """
                INSERT INTO target_capture_log
                    (register_id, position_id, step_no, step_name, status,
                     detail, gate_json)
                VALUES (?,?,?,?,?,?,?)
                """,
                (str(register_id), position_id, int(step_no), name, st,
                 str(detail or ""), str(gate_json or "{}")),
            )
    except Exception as e:
        print("write_capture_log skipped: %s: %s" % (type(e).__name__, e))


def read_capture_log(register_id: str | None = None, *, limit: int = 50,
                     db_path: Path | None = None) -> list[dict[str, Any]]:
    """The green-tick LOG, newest first (all rows when register_id is None)."""
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        if register_id:
            rows = conn.execute(
                "SELECT * FROM target_capture_log WHERE register_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (str(register_id), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM target_capture_log ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
    return [dict(r) for r in rows]


# ---- per-step evidence (the "evidence for each step" requirement) ----
def _sha256_file(p: Path) -> str:
    """sha256 of a file, computed here so a caller cannot pass a wrong hash in.

    Taking the hash as an ARGUMENT would let a caller record a hash for different
    bytes than the ones on disk; the row would then assert a provenance it cannot
    support. Hashing the real file at write time removes that possibility.
    """
    import hashlib

    h = hashlib.sha256()
    with open(str(p), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def record_step_evidence(
    session_key: str, step_no: int, evidence_id: str, kind: str, png_path: str,
    *, width: int | None = None, height: int | None = None, detail: str = "",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Bind ONE step to ONE artefact. Upserts on (session_key, step_no).

    Refuses when the file is missing: recording a path to a non-existent image
    would make `steps_missing_evidence()` claim a step is covered when it is not,
    which is worse than no record at all.
    """
    sk = str(session_key or "").strip()
    if not sk:
        raise ValueError("session_key required")
    sn = int(step_no)
    if sn not in TARGET_STEP_NAMES:
        raise ValueError("step_no must be 1..6, got %r" % step_no)
    p = Path(str(png_path))
    if not p.is_file():
        raise ValueError("evidence PNG does not exist: %s" % p)
    digest = _sha256_file(p)
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO target_capture_evidence
                (session_key, step_no, evidence_id, kind, png_path, sha256,
                 width, height, detail)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(session_key, step_no) DO UPDATE SET
                evidence_id = excluded.evidence_id,
                kind        = excluded.kind,
                png_path    = excluded.png_path,
                sha256      = excluded.sha256,
                width       = excluded.width,
                height      = excluded.height,
                detail      = excluded.detail,
                created_at  = CURRENT_TIMESTAMP
            """,
            (sk, sn, str(evidence_id), str(kind), str(p), digest,
             width, height, str(detail or "")),
        )
    return get_step_evidence(sk, sn, db_path=db_path) or {"step_no": sn}


def get_step_evidence(session_key: str, step_no: int, *,
                      db_path: Path | None = None) -> dict[str, Any] | None:
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_capture_evidence WHERE session_key = ? "
            "AND step_no = ?", (str(session_key), int(step_no)),
        ).fetchone()
    return dict(row) if row else None


def list_step_evidence(session_key: str, *,
                       db_path: Path | None = None) -> list[dict[str, Any]]:
    """Every recorded step artefact for a session, in step order."""
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM target_capture_evidence WHERE session_key = ? "
            "ORDER BY step_no", (str(session_key),),
        ).fetchall()
    return [dict(r) for r in rows]


def steps_missing_evidence(session_key: str, *,
                           db_path: Path | None = None) -> list[int]:
    """Which of steps 1..6 have NO recorded artefact. [] means fully covered."""
    have = {int(r["step_no"]) for r in list_step_evidence(session_key,
                                                          db_path=db_path)}
    return [n for n in sorted(TARGET_STEP_NAMES) if n not in have]


def verify_step_evidence(session_key: str, *,
                         db_path: Path | None = None) -> dict[str, Any]:
    """All 6 steps have an artefact AND each sha256 still matches the file.

    The hash re-check is the part that makes this a verification rather than a
    count: a PNG that was deleted or edited after recording FAILS here. A plain
    existence check would report success for a tampered or truncated image.
    """
    rows = list_step_evidence(session_key, db_path=db_path)
    present: list[dict[str, Any]] = []
    problems: list[str] = []
    for r in rows:
        p = Path(str(r.get("png_path") or ""))
        if not p.is_file():
            problems.append("step %s: file missing (%s)" % (r["step_no"], p))
            continue
        try:
            now = _sha256_file(p)
        except Exception as e:
            problems.append("step %s: unreadable (%s)" % (r["step_no"], e))
            continue
        if now != str(r.get("sha256") or ""):
            # Tampered or replaced image: the row's provenance claim is now false.
            problems.append("step %s: sha256 MISMATCH (recorded %s.., found %s..)"
                            % (r["step_no"], str(r["sha256"])[:10], now[:10]))
            continue
        present.append({"step_no": int(r["step_no"]), "kind": r["kind"],
                        "png_path": str(p), "sha256": now,
                        "width": r.get("width"), "height": r.get("height")})
    missing = [n for n in sorted(TARGET_STEP_NAMES)
               if n not in {p["step_no"] for p in present}]
    return {
        "session_key": str(session_key),
        "ok": not missing and not problems,
        "count": len(present),
        "total": len(TARGET_STEP_NAMES),
        "present": present,
        "missing_steps": missing,
        "problems": problems,
    }


# ---- capture sessions (resumable 6-STEP wizard) ----
def get_capture_session(session_key: str, *, db_path: Path | None = None) -> dict | None:
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_capture_session WHERE session_key = ?",
            (str(session_key),),
        ).fetchone()
    return dict(row) if row else None


def upsert_capture_session(
    session_key: str, *, step_no: int | None = None, name: str | None = None,
    source_id: int | None = None, source_kind: str | None = None,
    source_ref: str | None = None, x1: int | None = None, y1: int | None = None,
    x2: int | None = None, y2: int | None = None,
    screenshot_id: str | None = None, status: str | None = None,
    append_step: dict | None = None, db_path: Path | None = None,
) -> dict[str, Any]:
    """Create or advance a capture session. Only the fields you pass change.

    `append_step` adds one entry to step_log, so the journey through the 6 steps
    is reconstructable (which step ran, and what it produced) rather than only
    the final position.
    """
    import json

    sk = str(session_key or "").strip()
    if not sk:
        raise ValueError("session_key required")
    init_target_tables(db_path)
    cur = get_capture_session(sk, db_path=db_path)
    ts = _now()
    with _conn(db_path) as conn:
        if not cur:
            conn.execute(
                """
                INSERT INTO target_capture_session
                    (session_key, step_no, name, source_id, source_kind,
                     source_ref, x1, y1, x2, y2, screenshot_id, step_log,
                     status, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (sk, int(step_no or 1), name, source_id, source_kind,
                 source_ref, x1, y1, x2, y2, screenshot_id,
                 json.dumps([append_step] if append_step else []),
                 str(status or "in_progress"), ts),
            )
        else:
            sets: list[str] = []
            vals: list[Any] = []
            for col, val in (("step_no", step_no), ("name", name),
                             ("source_id", source_id), ("source_kind", source_kind),
                             ("source_ref", source_ref), ("x1", x1), ("y1", y1),
                             ("x2", x2), ("y2", y2),
                             ("screenshot_id", screenshot_id), ("status", status)):
                if val is not None:
                    sets.append("%s = ?" % col)
                    vals.append(val)
            if append_step:
                log = []
                try:
                    log = json.loads(cur.get("step_log") or "[]")
                except Exception:
                    log = []
                log.append(append_step)
                sets.append("step_log = ?")
                vals.append(json.dumps(log))
            if sets:
                sets.append("updated_at = ?")
                vals.append(ts)
                vals.append(sk)
                conn.execute(
                    "UPDATE target_capture_session SET %s WHERE session_key = ?"
                    % ", ".join(sets),
                    tuple(vals),
                )
    return get_capture_session(sk, db_path=db_path) or {"session_key": sk}


def migrate_target_area_to_position(
    *, source_id: int = 0, dry_run: bool = False, db_path: Path | None = None,
) -> dict[str, Any]:
    """Copy target_area -> target_position, RE-DERIVING width/height/centre.

    The centre is RECOMPUTED from the rect, never copied: the old table's cx/cy
    was unenforced, and the live `perm_pill` row holds cy=652 against its own
    rect midpoint 959. Copying it verbatim would carry the 307px bug into the new
    table — and the new CHECK would reject the insert, which is the correct,
    LOUD outcome rather than a silent repair.

    Migrated rows land with isactive=0: a copied rect is NOT a proven rect, and
    only the STEP 5/6 gates may set that flag. `dry_run` reports what WOULD
    happen without writing.
    """
    path = init_target_area_table(db_path)
    init_target_tables(db_path)
    report: dict[str, Any] = {
        "source_table": "target_area", "dry_run": bool(dry_run),
        "candidates": 0, "migrated": [], "skipped": [], "centres_repaired": [],
    }
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT target_id, popup_id, label, x1, y1, x2, y2, cx, cy "
            "FROM target_area ORDER BY popup_id, target_id"
        ).fetchall()
    report["candidates"] = len(rows)
    for r in rows:
        d = dict(r)
        tid = str(d["target_id"] or "").strip()
        try:
            m = compute_rect_metrics(d["x1"], d["y1"], d["x2"], d["y2"])
        except ValueError as e:
            report["skipped"].append({"target_id": tid, "reason": str(e)})
            continue
        # Record the repair EXPLICITLY instead of quietly fixing it, so the
        # migration output is evidence of the old defect rather than a claim.
        old_cx, old_cy = d.get("cx"), d.get("cy")
        if (old_cx, old_cy) != (m["cx"], m["cy"]):
            report["centres_repaired"].append({
                "target_id": tid, "stored": [old_cx, old_cy],
                "derived": [m["cx"], m["cy"]],
                "delta": [None if old_cx is None else m["cx"] - old_cx,
                          None if old_cy is None else m["cy"] - old_cy],
            })
        kind = "APP" if str(d.get("popup_id") or "") != "web" else "URL"
        if dry_run:
            report["migrated"].append({"target_id": tid, "rect": [d["x1"], d["y1"],
                                                                 d["x2"], d["y2"]],
                                       **m})
            continue
        existing = get_target_position_by_name(tid, db_path=db_path)
        if existing:
            report["skipped"].append({"target_id": tid,
                                      "reason": "name already in target_position"})
            continue
        try:
            row = create_target_position(
                tid, int(source_id or 0), kind, "migrated from target_area",
                d["x1"], d["y1"], d["x2"], d["y2"],
                screenshot_id=None, evidence_id=None, cross_evidence_id=None,
                isactive=0, db_path=db_path,
            )
            report["migrated"].append({"target_id": tid,
                                       "register_id": row.get("register_id"),
                                       "rect": [d["x1"], d["y1"], d["x2"], d["y2"]],
                                       **m})
        except Exception as e:
            report["skipped"].append({"target_id": tid,
                                      "reason": "%s: %s" % (type(e).__name__, e)})
    return report


def get_target_position_by_name(name: str, *, db_path: Path | None = None):
    """Look up by name (the UNIQUE key used by the migration)."""
    init_target_tables(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM target_position WHERE name = ?", (str(name),)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["register_id"] = register_id_for(d["id"])
    return d


# ---- target_area (DB-driven popup target AREAS + confirm checklist) ----
# DEPRECATED 2026-09-20: replaced by target_position. Kept readable so old
# evidence keeps resolving while the readers are repointed in batches. Nothing
# was dropped — dropping the table in the same change that moves its readers is
# how a rename becomes an outage.
_AREA_COLS = (
    "id, target_id, popup_id, label, x1, y1, x2, y2, cx, cy, "
    "checklist_confirm, confirm_reason, confirmed_at, isactive, "
    "created_at, updated_at"
)


def init_target_area_table(db_path: Path | None = None) -> Path:
    """Create the target_area table if missing (area + checklist_confirm)."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_TARGET_AREA_SQL)
    return path


def _area_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = {k: row[k] for k in row.keys()}
    d["checklist_confirm"] = str(d.get("checklist_confirm") or "no")
    d["isactive"] = int(d.get("isactive") or 0)
    return d


def upsert_target_area(
    target_id: str,
    *,
    popup_id: str = "perm_picker",
    label: str | None = None,
    x1: int = 0,
    y1: int = 0,
    x2: int = 0,
    y2: int = 0,
    checklist_confirm: str | None = None,
    confirm_reason: str | None = None,
    isactive: int = 1,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert or update ONE area row keyed by (popup_id, target_id).

    cx,cy are DERIVED from the area (never stored independently), so the
    center always agrees with X1/X2 + Y1/Y2. When checklist_confirm is None
    the existing confirm state is preserved (area re-measure only).
    """
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError("target_id required")
    pid = str(popup_id or "perm_picker").strip() or "perm_picker"
    x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    init_target_area_table(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM target_area WHERE popup_id = ? AND target_id = ?",
            (pid, tid),
        ).fetchone()
        if existing:
            row_id = int(existing["id"])
            if checklist_confirm is None:
                conn.execute(
                    """
                    UPDATE target_area
                    SET label = ?, x1 = ?, y1 = ?, x2 = ?, y2 = ?,
                        cx = ?, cy = ?, isactive = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (label, x1, y1, x2, y2, cx, cy, int(isactive), ts, row_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE target_area
                    SET label = ?, x1 = ?, y1 = ?, x2 = ?, y2 = ?,
                        cx = ?, cy = ?, checklist_confirm = ?,
                        confirm_reason = ?, confirmed_at = ?, isactive = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        label, x1, y1, x2, y2, cx, cy,
                        str(checklist_confirm), confirm_reason, ts,
                        int(isactive), ts, row_id,
                    ),
                )
        else:
            cur = conn.execute(
                """
                INSERT INTO target_area
                    (target_id, popup_id, label, x1, y1, x2, y2, cx, cy,
                     checklist_confirm, confirm_reason, confirmed_at,
                     isactive, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tid, pid, label, x1, y1, x2, y2, cx, cy,
                    str(checklist_confirm or "no"), confirm_reason,
                    ts if checklist_confirm else None, int(isactive), ts,
                ),
            )
            row_id = int(cur.lastrowid)
    return get_target_area(tid, popup_id=pid, db_path=db_path) or {"id": row_id}


def get_target_area(
    target_id: str,
    *,
    popup_id: str = "perm_picker",
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Lookup one area row by (popup_id, target_id)."""
    tid = str(target_id or "").strip()
    if not tid:
        return None
    pid = str(popup_id or "perm_picker").strip() or "perm_picker"
    init_target_area_table(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT {_AREA_COLS} FROM target_area "
            "WHERE popup_id = ? AND target_id = ?",
            (pid, tid),
        ).fetchone()
    return _area_to_dict(row) if row else None


def list_target_areas(
    *,
    popup_id: str | None = None,
    active_only: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List area rows, optionally filtered by popup_id."""
    init_target_area_table(db_path)
    where: list[str] = []
    params: list[Any] = []
    if popup_id:
        where.append("popup_id = ?")
        params.append(str(popup_id))
    if active_only:
        where.append("isactive = 1")
    sql = f"SELECT {_AREA_COLS} FROM target_area"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY popup_id, id ASC"
    with _conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_area_to_dict(r) for r in rows]


def set_checklist_confirm(
    target_id: str,
    confirm: str,
    *,
    popup_id: str = "perm_picker",
    reason: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any] | None:
    """Set checklist_confirm ('yes'|'no') for one target. Returns the row."""
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError("target_id required")
    val = "yes" if str(confirm).strip().lower() in ("yes", "y", "true", "1") else "no"
    pid = str(popup_id or "perm_picker").strip() or "perm_picker"
    init_target_area_table(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE target_area
            SET checklist_confirm = ?, confirm_reason = ?, confirmed_at = ?,
                updated_at = ?
            WHERE popup_id = ? AND target_id = ?
            """,
            (val, reason, ts, ts, pid, tid),
        )
        if cur.rowcount == 0:
            return None
    return get_target_area(tid, popup_id=pid, db_path=db_path)


def get_checklist_status(
    *,
    popup_id: str = "perm_picker",
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Confirm checklist for a popup.

    all_present = 'yes' only when there is >=1 active target AND every one
    has checklist_confirm = 'yes'. Otherwise 'no'.
    """
    pid = str(popup_id or "perm_picker").strip() or "perm_picker"
    items = list_target_areas(popup_id=pid, active_only=True, db_path=db_path)
    missing = [i["target_id"] for i in items if i.get("checklist_confirm") != "yes"]
    all_present = "yes" if items and not missing else "no"
    return {
        "popup_id": pid,
        "all_present": all_present,
        "count": len(items),
        "confirmed": len(items) - len(missing),
        "missing": missing,
        "items": items,
    }


# ---- check_records ----
def init_check_table(db_path: Path | None = None) -> Path:
    """Create the check_records table if missing (incl. catalog + template snapshot cols)."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_CHECK_SQL)
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(check_records)").fetchall()}
        if "catalog_id" not in cols:
            conn.execute("ALTER TABLE check_records ADD COLUMN catalog_id INTEGER DEFAULT 0")
        if "subcatalog_id" not in cols:
            conn.execute("ALTER TABLE check_records ADD COLUMN subcatalog_id INTEGER DEFAULT 0")
        if "catalog_name" not in cols:
            conn.execute("ALTER TABLE check_records ADD COLUMN catalog_name TEXT DEFAULT ''")
        if "subcatalog_name" not in cols:
            conn.execute("ALTER TABLE check_records ADD COLUMN subcatalog_name TEXT DEFAULT ''")
        if "template_id" in cols and "prompt_setting_id" not in cols:
            conn.execute("ALTER TABLE check_records RENAME COLUMN template_id TO prompt_setting_id")
            cols.discard("template_id"); cols.add("prompt_setting_id")
        if "template_key" in cols and "prompt_setting_key" not in cols:
            conn.execute("ALTER TABLE check_records RENAME COLUMN template_key TO prompt_setting_key")
            cols.discard("template_key"); cols.add("prompt_setting_key")
        if "prompt_setting_id" not in cols:
            conn.execute("ALTER TABLE check_records ADD COLUMN prompt_setting_id INTEGER DEFAULT 0")
        if "prompt_setting_key" not in cols:
            conn.execute("ALTER TABLE check_records ADD COLUMN prompt_setting_key TEXT DEFAULT ''")
    return path


def create_check_record(data: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    """Insert a check record and return it."""
    init_check_table(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO check_records
                (task_id, session_id, hash_chain, detail, verdict, error,
                 catalog_id, subcatalog_id, catalog_name, subcatalog_name,
                 prompt_setting_id, prompt_setting_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("task_id"),
                data.get("session_id"),
                data.get("hash_chain"),
                data.get("detail"),
                data.get("verdict"),
                data.get("error"),
                int(data.get("catalog_id") or 0),
                int(data.get("subcatalog_id") or 0),
                data.get("catalog_name") or "",
                data.get("subcatalog_name") or "",
                int(data.get("prompt_setting_id") or 0),
                data.get("prompt_setting_key") or "",
            ),
        )
        row_id = int(cur.lastrowid)
    return get_check_by_id(row_id, db_path=db_path)


def get_check_by_id(check_id: int, *, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return one check record by id, or None."""
    init_check_table(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM check_records WHERE id = ?", (int(check_id),)
        ).fetchone()
    return dict(row) if row else None


def list_check_by_task_id(task_id: str, *, db_path: Path | None = None) -> list[dict[str, Any]]:
    """Return all check records for a task_id, newest first."""
    init_check_table(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM check_records WHERE task_id = ? ORDER BY id DESC", (task_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_check_record(
    check_id: int, data: dict[str, Any], *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Update a check record by id; return the updated row or None."""
    init_check_table(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE check_records
            SET session_id = ?, hash_chain = ?, detail = ?, verdict = ?, error = ?,
                catalog_id = ?, subcatalog_id = ?, catalog_name = ?, subcatalog_name = ?,
                prompt_setting_id = ?, prompt_setting_key = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                data.get("session_id"),
                data.get("hash_chain"),
                data.get("detail"),
                data.get("verdict"),
                data.get("error"),
                int(data.get("catalog_id") or 0),
                int(data.get("subcatalog_id") or 0),
                data.get("catalog_name") or "",
                data.get("subcatalog_name") or "",
                int(data.get("prompt_setting_id") or 0),
                data.get("prompt_setting_key") or "",
                int(check_id),
            ),
        )
        if cur.rowcount == 0:
            return None
    return get_check_by_id(check_id, db_path=db_path)


def delete_check_record(check_id: int, *, db_path: Path | None = None) -> bool:
    """Delete a check record by id; return True if a row was removed."""
    init_check_table(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute("DELETE FROM check_records WHERE id=?", (int(check_id),))
        return cur.rowcount > 0


# ---- catalog / subcatalog ----
def init_catalog_tables(db_path: Path | None = None) -> Path:
    """Create catalog + subcatalog tables if missing."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_CATALOG_SQL)
        conn.execute(_CREATE_SUBCATALOG_SQL)
    return path


def get_catalog_by_id(catalog_id: int, *, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return one active catalog row by id, or None."""
    init_catalog_tables(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, description FROM catalog WHERE id=? AND is_active=1",
            (int(catalog_id),),
        ).fetchone()
    return dict(row) if row else None


def get_subcatalog_by_id(subcatalog_id: int, *, db_path: Path | None = None) -> dict[str, Any] | None:
    """Return one active subcatalog row by id, or None."""
    init_catalog_tables(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT id, catalog_id, name, description FROM subcatalog WHERE id=? AND is_active=1",
            (int(subcatalog_id),),
        ).fetchone()
    return dict(row) if row else None


def seed_catalog_sample(db_path: Path | None = None) -> None:
    """Insert sample catalog/subcatalog once (idempotent)."""
    init_catalog_tables(db_path)
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO catalog(name, description) VALUES (?, ?)",
            ("membership", "會員通道、會員模組測試"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO subcatalog(catalog_id, name, description) VALUES (?, ?, ?)",
            (1, "rest_channel", "REST 會員通道校驗"),
        )


# ---- format_templates (prompt setting) ----
_FORMAT_TEMPLATE_COLS = (
    "id, prompt_setting_key, name, description, instruction, mode, catalog_id, "
    "created_at, updated_at, is_active"
)


def init_format_template_table(db_path: Path | None = None) -> Path:
    """Create format_templates table if missing and seed default verdict_3line."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        tables = {
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "output_template" in tables and "format_templates" not in tables:
            conn.execute("ALTER TABLE format_templates RENAME TO format_templates")
            ot_cols = {r[1] for r in conn.execute("PRAGMA table_info(format_templates)")}
            if "prompt_setting_key" in ot_cols:
                conn.execute(
                    "ALTER TABLE format_templates RENAME COLUMN template_key TO prompt_setting_key"
                )
        conn.execute(_CREATE_FORMAT_TEMPLATE_SQL)
        cols = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(format_templates)").fetchall()
        }
        if "prompt_setting_key" in cols and "prompt_setting_key" not in cols:
            conn.execute(
                "ALTER TABLE format_templates RENAME COLUMN template_key TO prompt_setting_key"
            )
            cols.discard("prompt_setting_key")
            cols.add("prompt_setting_key")
        if "catalog_id" not in cols:
            conn.execute(
                "ALTER TABLE format_templates ADD COLUMN catalog_id INTEGER DEFAULT 0"
            )
        if "mode" not in cols:
            conn.execute(
                "ALTER TABLE format_templates ADD COLUMN mode TEXT DEFAULT 'ask'"
            )
    seed_format_template_sample(path)
    return path


def get_format_template_by_id(
    setting_id: int, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Return one active format_templates row by id, or None."""
    init_format_template_table(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT {_FORMAT_TEMPLATE_COLS} FROM format_templates "
            "WHERE id=? AND is_active=1",
            (int(setting_id),),
        ).fetchone()
    return dict(row) if row else None


def get_format_template_row_by_id(
    setting_id: int, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Return one format_templates row by id (any is_active), or None."""
    init_format_template_table(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT {_FORMAT_TEMPLATE_COLS} FROM format_templates WHERE id=?",
            (int(setting_id),),
        ).fetchone()
    return dict(row) if row else None


def get_format_template_by_key(
    prompt_setting_key: str, *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Return one active format_templates row by prompt_setting_key, or None."""
    init_format_template_table(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            f"SELECT {_FORMAT_TEMPLATE_COLS} FROM format_templates "
            "WHERE prompt_setting_key=? AND is_active=1",
            (str(prompt_setting_key),),
        ).fetchone()
    return dict(row) if row else None


def seed_format_template_sample(db_path: Path | None = None) -> None:
    """Insert default verdict_3line setting once (idempotent)."""
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.execute(_CREATE_FORMAT_TEMPLATE_SQL)
        cols = {
            str(r[1]) for r in conn.execute("PRAGMA table_info(format_templates)").fetchall()
        }
        if "catalog_id" not in cols:
            conn.execute(
                "ALTER TABLE format_templates ADD COLUMN catalog_id INTEGER DEFAULT 0"
            )
        conn.execute(
            "INSERT OR IGNORE INTO format_templates"
            "(prompt_setting_key, name, description, instruction) VALUES (?, ?, ?, ?)",
            (
                "verdict_3line",
                "三行verdict輸出",
                "輸出VERDICT/DETAIL/ERROR三行格式",
                DEFAULT_VERDICT_3LINE_INSTRUCTION,
            ),
        )
        # Chat Center identity pair (user spec 2026-09-20): the identity block
        # the Ctrl+Alt+T tool pastes, plus a CONFIRM follow-up that asks the
        # model to echo the block back so receipt is provable.
        conn.execute(
            "INSERT OR IGNORE INTO format_templates"
            "(prompt_setting_key, name, description, instruction) VALUES (?, ?, ?, ?)",
            (
                "worker_identity_confirm",
                "worker identity - confirm",
                "確認 worker identity block 已收到（echo 返 identity 欄位）",
                DEFAULT_WORKER_IDENTITY_CONFIRM_INSTRUCTION,
            ),
        )


def list_prompt_settings(
    *,
    active_only: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List format_templates (prompt setting) rows ordered by id."""
    init_format_template_table(db_path)
    sql = f"SELECT {_FORMAT_TEMPLATE_COLS} FROM format_templates"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY id ASC"
    with _conn(db_path) as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def create_prompt_setting(
    data: dict[str, Any], *, db_path: Path | None = None
) -> dict[str, Any]:
    """Insert a new prompt setting, or REVIVE a soft-deleted one.

    Raises ValueError on missing fields, or on a duplicate key that is ACTIVE.

    WHY REVIVE: `prompt_setting_key` is UNIQUE, and soft delete keeps the row
    (is_active=0) for history. Without revival, a soft-deleted key is
    PERMANENTLY BURNED — the user deletes a setting, then cannot create one with
    the same name again. That is not what soft delete means; it is a leak.

    The rule is deliberately narrow:
      * an ACTIVE row with the same key  -> ValueError (409). Never overwritten.
      * a SOFT-DELETED row with same key -> revived (is_active=1, fields updated)
      * no row                           -> inserted
    """
    init_format_template_table(db_path)
    key = str(data.get("prompt_setting_key") or "").strip()
    name = str(data.get("name") or "").strip()
    instruction = str(data.get("instruction") or "")
    if not key:
        raise ValueError("prompt_setting_key required")
    if not name:
        raise ValueError("name required")
    if not instruction.strip():
        raise ValueError("instruction required")
    description = str(data.get("description") or "")
    catalog_id = int(data.get("catalog_id") or 0)
    is_active = int(data.get("is_active") if data.get("is_active") is not None else 1)

    with _conn(db_path) as conn:
        existing = conn.execute(
            "SELECT id, is_active FROM format_templates WHERE prompt_setting_key = ?",
            (key,),
        ).fetchone()

        if existing is not None and int(existing["is_active"]) == 1:
            # An ACTIVE row owns this key. Refuse — never overwrite live config.
            raise ValueError(f"prompt_setting_key already exists: {key}")

        if existing is not None:
            # Soft-deleted: REVIVE it rather than inserting a second row, so the
            # UNIQUE key stays satisfied and the history stays in one place.
            row_id = int(existing["id"])
            conn.execute(
                """
                UPDATE format_templates
                SET name = ?, description = ?, instruction = ?, catalog_id = ?,
                    is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (name, description, instruction, catalog_id, is_active, row_id),
            )
        else:
            try:
                cur = conn.execute(
                    """
                    INSERT INTO format_templates
                        (prompt_setting_key, name, description, instruction, catalog_id, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (key, name, description, instruction, catalog_id, is_active),
                )
                row_id = int(cur.lastrowid)
            except sqlite3.IntegrityError as e:
                raise ValueError(f"prompt_setting_key already exists: {key}") from e

    row = get_format_template_row_by_id(row_id, db_path=db_path)
    if not row:
        raise RuntimeError("failed to load created prompt setting")
    return row


def update_prompt_setting(
    setting_id: int, data: dict[str, Any], *, db_path: Path | None = None
) -> dict[str, Any] | None:
    """Update a prompt setting by id. Returns updated row or None if missing."""
    init_format_template_table(db_path)
    existing = get_format_template_row_by_id(setting_id, db_path=db_path)
    if not existing:
        return None

    new_key = existing["prompt_setting_key"]
    if "prompt_setting_key" in data and data["prompt_setting_key"] is not None:
        candidate = str(data["prompt_setting_key"]).strip()
        if candidate and candidate != existing["prompt_setting_key"]:
            if existing["prompt_setting_key"] == "verdict_3line":
                raise ValueError("cannot change prompt_setting_key of verdict_3line")
            new_key = candidate

    name = existing["name"]
    if "name" in data and data["name"] is not None:
        name = str(data["name"]).strip() or name

    description = existing.get("description") or ""
    if "description" in data and data["description"] is not None:
        description = str(data["description"])

    instruction = existing["instruction"]
    if "instruction" in data and data["instruction"] is not None:
        instruction = str(data["instruction"])
        if not instruction.strip():
            raise ValueError("instruction required")

    catalog_id = int(existing.get("catalog_id") or 0)
    if "catalog_id" in data and data["catalog_id"] is not None:
        catalog_id = int(data["catalog_id"] or 0)

    mode = str(existing.get("mode") or "ask")
    if "mode" in data and data["mode"] is not None:
        mode = str(data["mode"]).strip() or "ask"

    is_active = int(existing.get("is_active") if existing.get("is_active") is not None else 1)
    if "is_active" in data and data["is_active"] is not None:
        is_active = int(data["is_active"])

    try:
        with _conn(db_path) as conn:
            cur = conn.execute(
                """
                UPDATE format_templates
                SET prompt_setting_key = ?, name = ?, description = ?, instruction = ?,
                    mode = ?, catalog_id = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    new_key,
                    name,
                    description,
                    instruction,
                    mode,
                    catalog_id,
                    is_active,
                    int(setting_id),
                ),
            )
            if cur.rowcount == 0:
                return None
    except sqlite3.IntegrityError as e:
        raise ValueError(f"prompt_setting_key already exists: {new_key}") from e
    return get_format_template_row_by_id(setting_id, db_path=db_path)


def _legacy_ids_trusted(db_path: Path | None = None) -> bool:
    """Is a bare `prompt_setting_id:` frontmatter integer meaningful here?

    A bare integer id is only meaningful inside the database it was authored
    against. `skills/` frontmatter was authored against the PRODUCTION
    `coords.db`, so those ids may only be compared when we are operating on that
    same database. Against any other database (a test temp DB, a rebuilt DB) the
    numbering is unrelated and a match is a coincidence, not a reference.
    """
    resolved = Path(db_path) if db_path else Path(DB_PATH)
    prod = Path(__file__).resolve().parent / "coords.db"
    try:
        return resolved.resolve() == prod.resolve()
    except OSError:
        return False


def count_skill_refs_to_setting(
    setting_id: int,
    *,
    skills_root: Path | str | None = None,
    prompt_setting_key: str | None = None,
    trust_legacy_ids: bool = True,
) -> int:
    """Count *.skill.md frontmatter references to a prompt setting.

    WHY THIS TAKES A KEY AND NOT ONLY AN ID
    ---------------------------------------
    `format_templates.id` is a PER-DATABASE AUTOINCREMENT. It is not globally
    unique, so comparing a bare integer across databases is meaningless.

    MEASURED BUG 2026-09-20: the check compared `str(setting_id)` against the
    `prompt_setting_id:` frontmatter of every skill under the PRODUCTION `skills/`
    directory. A test that creates a fresh temp DB gets `verdict_3line` as id 1,
    and the production skills directory happens to contain ids 33 and 449 — so a
    brand-new custom setting could collide with a production id and be reported as
    "referenced by N skill(s)", returning 409 for a delete that should succeed.
    The test `test_soft_delete_custom` failed for exactly this reason.

    So the KEY is the reliable reference: `prompt_setting_key` is UNIQUE and
    meaningful, and it survives a DB rebuild. Both forms are counted:

      * `prompt_setting_key: <key>`  -> authoritative, DB-independent
      * `prompt_setting_id: <id>`    -> legacy, counted ONLY when the caller also
                                        passes the key, so a bare id can never
                                        match across databases

    A legacy id is therefore still honoured for the SAME setting (the caller knows
    which key that id belongs to), but it can no longer produce a false positive
    from an unrelated database's numbering.
    """
    import re as _re

    root = Path(skills_root) if skills_root else Path(__file__).resolve().parent / "skills"
    if not root.is_dir():
        return 0
    fm_re = _re.compile(r"^---\r?\n(.*?)\r?\n---", _re.DOTALL)
    sid = str(int(setting_id))
    key = str(prompt_setting_key or "").strip()
    count = 0
    for md in root.rglob("*.skill.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        m = fm_re.match(text)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip()
            # A commented-out reference is NOT a reference. The skills directory
            # contains lines like "# prompt_setting_id: 34  REMOVED ..." which the
            # old parser skipped only because it checked startswith("#") — kept
            # explicit here so the intent is visible.
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, _, val = line.partition(":")
            k = k.strip()
            val = val.strip().strip("'").strip('"')
            if k == "prompt_setting_key":
                if key and val == key:
                    count += 1
                    break
            elif k == "prompt_setting_id":
                # A bare integer is only a reference inside the database it was
                # authored against. MEASURED 2026-09-20: a temp DB gave a brand
                # new setting id=33, and production `skills/` happens to carry
                # `prompt_setting_id: 33`, so the delete was refused with
                # "referenced by 1 skill(s)" for a setting no skill mentions.
                if trust_legacy_ids and key and val == sid:
                    count += 1
                    break
    return count


def delete_prompt_setting(
    setting_id: int,
    *,
    hard: bool = False,
    db_path: Path | None = None,
    skills_root: Path | str | None = None,
) -> dict[str, Any]:
    """Soft-delete (default) or hard-delete a prompt setting with guards.

    Returns {ok, mode?, error?, prompt_setting_key?}.
    """
    init_format_template_table(db_path)
    row = get_format_template_row_by_id(setting_id, db_path=db_path)
    if not row:
        return {"ok": False, "error": "not found"}
    key = str(row.get("prompt_setting_key") or "")
    if key == "verdict_3line":
        return {
            "ok": False,
            "error": "cannot delete default prompt setting verdict_3line (fallback SSOT)",
            "prompt_setting_key": key,
        }
    refs = count_skill_refs_to_setting(
        setting_id,
        skills_root=skills_root,
        prompt_setting_key=key,
        trust_legacy_ids=_legacy_ids_trusted(db_path),
    )
    if refs > 0:
        return {
            "ok": False,
            "error": (
                f"prompt setting id={setting_id} ({key}) is referenced by {refs} "
                "skill(s); rebind skills or rely on verdict_3line fallback before "
                "delete"
            ),
            "prompt_setting_key": key,
            "skill_ref_count": refs,
        }
    if hard:
        with _conn(db_path) as conn:
            cur = conn.execute(
                "DELETE FROM format_templates WHERE id=?", (int(setting_id),)
            )
            if cur.rowcount == 0:
                return {"ok": False, "error": "not found"}
        return {"ok": True, "mode": "hard", "prompt_setting_key": key}
    with _conn(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE format_templates
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(setting_id),),
        )
        if cur.rowcount == 0:
            return {"ok": False, "error": "not found"}
    return {"ok": True, "mode": "soft", "prompt_setting_key": key}


def preview_prompt_setting(
    *,
    sample_prompt: str = "",
    instruction: str | None = None,
    setting_id: int | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Concatenate sample task prompt + prompt setting instruction (no LLM)."""
    instr = instruction
    meta: dict[str, Any] = {}
    if instr is None or instr == "":
        tmpl = None
        if setting_id is not None:
            tmpl = get_format_template_row_by_id(int(setting_id), db_path=db_path)
        if not tmpl:
            tmpl = get_format_template_by_key("verdict_3line", db_path=db_path)
        if tmpl:
            instr = str(tmpl.get("instruction") or "")
            meta = {
                "prompt_setting_id": tmpl.get("id"),
                "prompt_setting_key": tmpl.get("prompt_setting_key"),
            }
        else:
            instr = DEFAULT_VERDICT_3LINE_INSTRUCTION
            meta = {"prompt_setting_id": 0, "prompt_setting_key": "verdict_3line"}
    final_prompt = f"{sample_prompt or ''}{instr or ''}"
    return {"final_prompt": final_prompt, "instruction": instr or "", **meta}


def save_target_point(
    target_logo: str | None,
    target_name: str,
    x: int,
    y: int,
    action: str = "click",
    isactive: int = 1,
    llm_score: int = 100,
    error: str | None = None,
    *,
    target_id: str | None = None,
    db_path: Path | None = None,
    upsert: bool = True,
) -> dict[str, Any]:
    """Insert or update row.

    When target_id is set: match by target_id only (INSERT if missing — never
    steal another card's name row). When empty: legacy active-name upsert.
    """
    name = (target_name or "").strip()
    if not name:
        raise ValueError("target_name required")
    act = (action or "click").strip() or "click"
    tid = (target_id or "").strip() or None
    init_db(db_path)
    ts = _now()
    logo = target_logo or ""
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        row_id: int | None = None
        if upsert:
            existing = None
            if tid:
                existing = conn.execute(
                    """
                    SELECT id FROM target_points
                    WHERE target_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (tid,),
                ).fetchone()
            else:
                existing = conn.execute(
                    """
                    SELECT id FROM target_points
                    WHERE target_name = ? AND isactive = 1
                    ORDER BY id DESC LIMIT 1
                    """,
                    (name,),
                ).fetchone()
            if existing:
                row_id = int(existing["id"])
                if tid:
                    conn.execute(
                        """
                        UPDATE target_points
                        SET target_id = ?, target_logo = ?, target_name = ?,
                            x = ?, y = ?, action = ?, isactive = ?,
                            llm_score = ?, error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            tid,
                            logo,
                            name,
                            int(x),
                            int(y),
                            act,
                            int(isactive),
                            int(llm_score),
                            error,
                            ts,
                            row_id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE target_points
                        SET target_logo = ?, target_name = ?,
                            x = ?, y = ?, action = ?, isactive = ?,
                            llm_score = ?, error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            logo,
                            name,
                            int(x),
                            int(y),
                            act,
                            int(isactive),
                            int(llm_score),
                            error,
                            ts,
                            row_id,
                        ),
                    )
        if row_id is None:
            cur = conn.execute(
                """
                INSERT INTO target_points
                    (target_id, target_logo, target_name, x, y, action,
                     isactive, llm_score, error, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tid,
                    logo,
                    name,
                    int(x),
                    int(y),
                    act,
                    int(isactive),
                    int(llm_score),
                    error,
                    ts,
                ),
            )
            row_id = int(cur.lastrowid)
    if tid:
        point = get_target_point(
            name, target_id=tid, db_path=db_path, active_only=False
        )
    else:
        point = get_target_point(name, db_path=db_path, active_only=False)
    return point or {
        "id": row_id,
        "target_id": tid,
        "target_logo": logo,
        "target_name": name,
        "x": int(x),
        "y": int(y),
        "action": act,
        "isactive": int(isactive),
        "llm_score": int(llm_score),
        "error": error,
    }


def update_target_error(
    target_name: str | None = None,
    error_msg: str = "",
    *,
    target_id: str | None = None,
    db_path: Path | None = None,
) -> int:
    """When execution fails, write error message on matching row(s)."""
    tid = (target_id or "").strip() or None
    name = (target_name or "").strip() if target_name else ""
    if not tid and not name:
        return 0
    init_db(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        if tid:
            cur = conn.execute(
                """
                UPDATE target_points
                SET error = ?, updated_at = ?
                WHERE target_id = ?
                """,
                (error_msg, ts, tid),
            )
            return int(cur.rowcount or 0)
        cur = conn.execute(
            """
            UPDATE target_points
            SET error = ?, updated_at = ?
            WHERE target_name = ? AND isactive = 1
            """,
            (error_msg, ts, name),
        )
        n = int(cur.rowcount or 0)
        if n == 0:
            cur = conn.execute(
                """
                UPDATE target_points
                SET error = ?, updated_at = ?
                WHERE id = (
                    SELECT id FROM target_points
                    WHERE target_name = ?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (error_msg, ts, name),
            )
            n = int(cur.rowcount or 0)
        return n


def clear_target_error(
    target_name: str | None = None,
    *,
    target_id: str | None = None,
    db_path: Path | None = None,
) -> int:
    """After successful manual recalibrate, clear error."""
    tid = (target_id or "").strip() or None
    name = (target_name or "").strip() if target_name else ""
    if not tid and not name:
        return 0
    init_db(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        if tid:
            cur = conn.execute(
                """
                UPDATE target_points
                SET error = NULL, updated_at = ?
                WHERE target_id = ?
                """,
                (ts, tid),
            )
            return int(cur.rowcount or 0)
        cur = conn.execute(
            """
            UPDATE target_points
            SET error = NULL, updated_at = ?
            WHERE target_name = ?
            """,
            (ts, name),
        )
        return int(cur.rowcount or 0)


def get_target_point(
    target_name: str | None = None,
    *,
    target_id: str | None = None,
    db_path: Path | None = None,
    active_only: bool = True,
) -> dict[str, Any] | None:
    """Lookup by target_id first when provided; else by target_name."""
    tid = (target_id or "").strip() or None
    name = (target_name or "").strip() if target_name else ""
    if not tid and not name:
        return None
    init_db(db_path)
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        row = None
        if tid:
            if active_only:
                row = conn.execute(
                    f"""
                    SELECT {_POINT_COLS}
                    FROM target_points
                    WHERE target_id = ? AND isactive = 1
                    ORDER BY id DESC LIMIT 1
                    """,
                    (tid,),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    SELECT {_POINT_COLS}
                    FROM target_points
                    WHERE target_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (tid,),
                ).fetchone()
        if row is None and name:
            if active_only:
                row = conn.execute(
                    f"""
                    SELECT {_POINT_COLS}
                    FROM target_points
                    WHERE target_name = ? AND isactive = 1
                    ORDER BY id DESC LIMIT 1
                    """,
                    (name,),
                ).fetchone()
            else:
                row = conn.execute(
                    f"""
                    SELECT {_POINT_COLS}
                    FROM target_points
                    WHERE target_name = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (name,),
                ).fetchone()
        if not row:
            return None
        d = {k: row[k] for k in row.keys()}
        if d.get("action") is None:
            d["action"] = "click"
        return d


def list_target_points(
    *,
    active_only: bool = True,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    init_db(db_path)
    with _conn(db_path) as conn:
        _ensure_columns(conn)
        if active_only:
            rows = conn.execute(
                f"""
                SELECT {_POINT_COLS}
                FROM target_points WHERE isactive = 1
                ORDER BY target_name, id DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT {_POINT_COLS}
                FROM target_points
                ORDER BY id DESC
                """
            ).fetchall()
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys()}
            if d.get("action") is None:
                d["action"] = "click"
            out.append(d)
        return out


if __name__ == "__main__":
    init_db()
    save_target_point(
        target_logo="snapshots/vs_code_copy_btn.png",
        target_name="vs_code_copy_btn",
        x=841,
        y=761,
        action="click",
        isactive=1,
        llm_score=100,
        target_id="t_smoke_vs_code_copy_btn",
    )
    point = get_target_point(
        "vs_code_copy_btn", target_id="t_smoke_vs_code_copy_btn"
    )
    print(point)
    update_target_error(
        "vs_code_copy_btn",
        "按鈕位置偏移，無法成功點擊",
        target_id="t_smoke_vs_code_copy_btn",
    )
    print(
        "after error:",
        get_target_point(target_id="t_smoke_vs_code_copy_btn"),
    )
    clear_target_error(
        "vs_code_copy_btn", target_id="t_smoke_vs_code_copy_btn"
    )
    print(
        "after clear:",
        get_target_point(target_id="t_smoke_vs_code_copy_btn"),
    )
