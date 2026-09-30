"""
task lifecycle + prompt trace (A+B+C).

- task_lifecycle_log: append-only business events
- task_prompt_trace: technical prompt snapshots (minimal API; no full assembler skill)

Python API is source of truth; skill SSOT is validation/documentation.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB = BASE_DIR / "agent.db"
SKILLS_DIR = BASE_DIR / "skills"

LIFECYCLE_SKILL_KEY = "skill_lifecycle_trace_recorder"
LIFECYCLE_SKILL_VERSION = "v1_strict"
LIFECYCLE_PRODUCT_VERSION = "skill-1.0"
LIFECYCLE_MODULE = "task_center"
LIFECYCLE_FUNCTION = "task_center.append_lifecycle_event"
LIFECYCLE_CAPABILITY = "task_center.append_lifecycle_event"
LIFECYCLE_API = "POST /api/tasks/lifecycle/append"
LIFECYCLE_TASK_LABEL = "T-SKILL02"
CHANNEL_CODE = "local_pc"
CHANNEL_NAME = "Local PC"
MODULE_CODE = "task_center"
MODULE_NAME = "Task Center"
VERSION_LABEL = "tc-skill-1.0"
SOURCE = "lifecycle_log"

EVENT_TYPES = frozenset(
    {
        "requirement_received",
        "skill_assembled",
        "skill_run",
        "validation_pass",
        "validation_fail",
        "state_update",
        "task_finalized",
    }
)

TASK_STATES = (
    "draft",
    "researching",
    "proposal_draft",
    "validating",
    "validated",
    "finalized",
)
STATE_ORDER = {s: i for i, s in enumerate(TASK_STATES)}

# skill_run / validation defaults (Phase D chain uses these; append validates lightly)
SKILL_STATE_AFTER_RUN: dict[str, str] = {
    "skill_requirement_research": "researching",
    "skill_proposal_design": "proposal_draft",
    "skill_api_standard_validator": "proposal_draft",  # stay; warning only
    "skill_proposal_validate": "validated",  # PASS path; FAIL handled separately
    "skill_task_format_worker_taxonomy_validator": "finalized",
}

DIMENSION_TAGS: dict[str, list[str]] = {
    "skill_lifecycle_trace_recorder": ["lifecycle", "audit", "gate"],
    "skill_requirement_research": ["research"],
    "skill_proposal_design": ["proposal"],
    "skill_api_standard_validator": ["validation", "api-gate"],
    "skill_proposal_validate": ["validation", "proposal-gate"],
    "skill_worker_code_builder": ["build", "ssot", "tdd"],
    "skill_task_format_worker_taxonomy_validator": ["validation", "taxonomy-gate"],
    "mouse_spot_verify": ["visual-gate", "openclaw"],
}

TASK_LIFECYCLE_LOG_DDL = """
CREATE TABLE IF NOT EXISTS task_lifecycle_log (
    lifecycle_id      TEXT    PRIMARY KEY,
    task_id           TEXT    NOT NULL,
    event_sequence    INTEGER NOT NULL,
    event_type        TEXT    NOT NULL,
    skill_id          TEXT,
    skill_version     TEXT,
    module            TEXT,
    capability        TEXT,
    task_state        TEXT    NOT NULL,
    linked_trace_id   TEXT,
    event_summary     TEXT,
    event_detail_json TEXT    NOT NULL DEFAULT '{}',
    dimension_tags_json TEXT  NOT NULL DEFAULT '[]',
    event_timestamp   TEXT    NOT NULL,
    actor             TEXT    NOT NULL DEFAULT 'worker',
    comment           TEXT,
    source            TEXT    NOT NULL DEFAULT 'lifecycle_log',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 🔴 `skill_ref` — DECLARED HERE 2026-09-29 (RING 5, D2).
    --
    -- MEASURED: LIVE has this column with 16 of 20 rows populated and 3 distinct
    -- values, and NOTHING declared it — neither this DDL nor any additive list.
    -- It is the `skill_ref INTEGER` the STAGE 2 FK policy added to the sibling
    -- tables (`component_registry`, `skill_prompt_test_run`, `skill_lesson`),
    -- i.e. a HALF-APPLIED migration of one column family: the writer wrote it,
    -- the declarer never learned about it.
    --
    -- WHY IT IS DECLARED AND NOT DELETED: it is POPULATED. Deleting a populated
    -- column to make a comparison green would delete a fact, and the FK policy
    -- already decided the direction for this column (`component_registry`:
    -- an optional INTEGER reference with NO FK). The 4 NULLs are legitimate —
    -- NULL means "no skill recorded" (`null_columns`: KEEP_NULL).
    skill_ref         INTEGER,
    UNIQUE (task_id, event_sequence)
);
CREATE INDEX IF NOT EXISTS idx_lifecycle_task_seq
  ON task_lifecycle_log (task_id, event_sequence);
CREATE INDEX IF NOT EXISTS idx_lifecycle_trace
  ON task_lifecycle_log (linked_trace_id);
"""

TASK_PROMPT_TRACE_DDL = """
CREATE TABLE IF NOT EXISTS task_prompt_trace (
    trace_id          TEXT    PRIMARY KEY,
    task_id           TEXT    NOT NULL,
    skill_id          TEXT,
    skill_version     TEXT,
    prompt_snapshot   TEXT,
    variables_json    TEXT    NOT NULL DEFAULT '{}',
    dimension_tags_json TEXT  NOT NULL DEFAULT '[]',
    module            TEXT,
    capability        TEXT,
    actor             TEXT    NOT NULL DEFAULT 'worker',
    source            TEXT    NOT NULL DEFAULT 'prompt_trace',
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- 🔴 `skill_ref` — DECLARED HERE 2026-09-29 (RING 5, D2). MEASURED: LIVE has
    -- it, 11/11 rows populated, 3 distinct values, and it was declared NOWHERE.
    -- Same half-applied column family as `task_lifecycle_log.skill_ref` above.
    skill_ref         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_prompt_trace_task
  ON task_prompt_trace (task_id, created_at);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _norm(v: Any) -> str:
    return str(v or "").strip()


def _as_json(v: Any, default: Any) -> str:
    if v is None:
        return json.dumps(default, ensure_ascii=False)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return json.dumps(default, ensure_ascii=False)
        try:
            json.loads(s)
            return s
        except json.JSONDecodeError:
            return json.dumps(v, ensure_ascii=False)
    return json.dumps(v, ensure_ascii=False)


def ensure_lifecycle_tables(conn: sqlite3.Connection) -> None:
    """Create both tables, then apply the ADDITIVE columns.

    ORDER IS FORCED (the trap this repo records twice: `skill_lesson.rating`,
    `factor_change_log.round_index`): `CREATE TABLE IF NOT EXISTS` does NOT add a
    column to an existing table, so the DDL alone is not the migration. Adding
    the columns FIRST and declaring them in the DDL SECOND would also be wrong —
    a FRESH table must be born correct. Both happen: the DDL declares them, and
    this ALTER covers a database created before the declaration.
    """
    import db_schema as ds

    conn.executescript(TASK_LIFECYCLE_LOG_DDL)
    conn.executescript(TASK_PROMPT_TRACE_DDL)
    ds._add_columns_if_missing(conn, "task_lifecycle_log",
                               (("skill_ref", "INTEGER"),))
    ds._add_columns_if_missing(conn, "task_prompt_trace",
                               (("skill_ref", "INTEGER"),))


def next_event_sequence(conn: sqlite3.Connection, task_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(event_sequence), 0) AS m FROM task_lifecycle_log WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return int(row["m"] if row else 0) + 1


def _last_state(conn: sqlite3.Connection, task_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT task_state FROM task_lifecycle_log
        WHERE task_id = ?
        ORDER BY event_sequence DESC LIMIT 1
        """,
        (task_id,),
    ).fetchone()
    return str(row["task_state"]) if row else None


def _trace_exists(conn: sqlite3.Connection, trace_id: str) -> bool:
    if not trace_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM task_prompt_trace WHERE trace_id = ?", (trace_id,)
    ).fetchone()
    return row is not None


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for k in ("event_detail_json", "dimension_tags_json", "variables_json"):
        if k in d and isinstance(d[k], str):
            try:
                parsed = json.loads(d[k] or ("{}" if "detail" in k or "variables" in k else "[]"))
            except json.JSONDecodeError:
                parsed = d[k]
            nice = k.replace("_json", "") if k.endswith("_json") else k
            if k == "event_detail_json":
                d["event_detail"] = parsed
            elif k == "dimension_tags_json":
                d["dimension_tags"] = parsed
            elif k == "variables_json":
                d["variables"] = parsed
    return d


def validate_lifecycle_payload(payload: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Skill-aligned validation; does not write."""
    errors: list[str] = []
    recs: list[str] = []
    task_id = _norm(payload.get("task_id"))
    event_type = _norm(payload.get("event_type"))
    task_state = _norm(payload.get("task_state"))
    linked = _norm(payload.get("linked_trace_id"))
    seq_raw = payload.get("event_sequence")

    if not task_id:
        errors.append("task_id: required")
        recs.append("Provide task_id for the lifecycle trail")
    if event_type not in EVENT_TYPES:
        errors.append(f"event_type: invalid '{event_type}'")
        recs.append("Use one of: " + ", ".join(sorted(EVENT_TYPES)))
    if task_state not in STATE_ORDER:
        errors.append(f"task_state: invalid '{task_state}'")
        recs.append("Use: " + " → ".join(TASK_STATES))

    # sequence
    expected_seq = next_event_sequence(conn, task_id) if task_id else 1
    if seq_raw is None or _norm(seq_raw) == "":
        seq = expected_seq
    else:
        try:
            seq = int(seq_raw)
        except (TypeError, ValueError):
            seq = -1
            errors.append("event_sequence: must be integer")
            recs.append(f"Use next sequence {expected_seq}")
        if seq != expected_seq and seq >= 0:
            errors.append(
                f"event_sequence: got {seq}, expected {expected_seq} (must +1, no reuse)"
            )
            recs.append(f"Set event_sequence to {expected_seq}")

    # linked_trace rules
    if event_type == "requirement_received":
        if linked and not _trace_exists(conn, linked):
            errors.append(f"linked_trace_id: unknown '{linked}'")
            recs.append("Omit linked_trace_id on requirement_received or use existing trace")
    else:
        if linked and not _trace_exists(conn, linked):
            errors.append(f"linked_trace_id: must exist in task_prompt_trace (got '{linked}')")
            recs.append("Create prompt trace first or pass a real trace_id")

    # state machine (no skip forward without intermediate; allow FAIL rollback)
    prev = _last_state(conn, task_id) if task_id else None
    if task_state in STATE_ORDER and prev and prev in STATE_ORDER:
        if event_type == "validation_fail":
            # rollback allowed to earlier or same non-finalized
            if STATE_ORDER[task_state] > STATE_ORDER[prev]:
                errors.append(
                    f"validation_fail: task_state '{task_state}' must not advance past '{prev}'"
                )
                recs.append("On FAIL roll back (e.g. proposal_draft)")
        elif event_type != "state_update":
            if STATE_ORDER[task_state] > STATE_ORDER[prev] + 1:
                errors.append(
                    f"task_state: cannot jump from '{prev}' to '{task_state}' without intermediate events"
                )
                recs.append("Record intermediate lifecycle events; no skip")
            # finalized only via task_finalized / validation_pass path — soft check
            if task_state == "finalized" and event_type not in (
                "task_finalized",
                "validation_pass",
                "skill_run",
            ):
                errors.append("task_state finalized requires task_finalized or validation_pass")
                recs.append("Use event_type task_finalized when closing the trail")

    # skill-specific hints (non-blocking warnings collected separately)
    warnings: list[str] = []
    skill_id = _norm(payload.get("skill_id"))
    if skill_id == "skill_api_standard_validator" and task_state not in (
        "proposal_draft",
        "validating",
    ):
        warnings.append(
            "skill_api_standard_validator usually keeps proposal_draft (check-only)"
        )
    if (
        skill_id == "skill_proposal_validate"
        and event_type == "validation_fail"
        and task_state != "proposal_draft"
    ):
        warnings.append("proposal_validate FAIL should roll back to proposal_draft")

    ok = len(errors) == 0
    return {
        "ok": ok,
        "result": "PASS" if ok else "FAIL",
        "result_yes_no": "YES" if ok else "NO",
        "errors": errors,
        "recommendations": recs,
        "warnings": warnings,
        "event_sequence": expected_seq if seq_raw is None or _norm(seq_raw) == "" else seq_raw,
        "expected_sequence": expected_seq,
        "previous_state": prev,
        "skill_key": LIFECYCLE_SKILL_KEY,
        "version": LIFECYCLE_PRODUCT_VERSION,
    }


def append_lifecycle_event(
    payload: dict[str, Any] | None = None,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Append one immutable lifecycle row after validation."""
    data = dict(payload or {})
    own = False
    if conn is None:
        path = Path(db_path or DEFAULT_DB)
        if not path.is_file():
            return {
                "ok": False,
                "result": "FAIL",
                "result_yes_no": "NO",
                "errors": ["agent.db missing"],
                "recommendations": ["Create agent.db first"],
            }
        conn = _connect(path)
        own = True
    try:
        ensure_lifecycle_tables(conn)
        check = validate_lifecycle_payload(data, conn)
        if not check["ok"]:
            check["dispatch_allowed"] = False
            check["report"] = _format_lifecycle_report(check, None)
            return check

        task_id = _norm(data.get("task_id"))
        seq = int(check["expected_sequence"])
        event_type = _norm(data.get("event_type"))
        task_state = _norm(data.get("task_state"))
        skill_id = _norm(data.get("skill_id"))
        tags = data.get("dimension_tags")
        if tags is None and skill_id:
            tags = DIMENSION_TAGS.get(skill_id) or DIMENSION_TAGS.get(LIFECYCLE_SKILL_KEY)
        lifecycle_id = _norm(data.get("lifecycle_id")) or f"lc_{uuid.uuid4().hex[:12]}"
        ts = _norm(data.get("event_timestamp")) or _utc_now()
        linked = _norm(data.get("linked_trace_id")) or None

        row_out = {
            "lifecycle_id": lifecycle_id,
            "task_id": task_id,
            "event_sequence": seq,
            "event_type": event_type,
            "skill_id": skill_id or None,
            "skill_version": _norm(data.get("skill_version")) or None,
            "module": _norm(data.get("module")) or None,
            "capability": _norm(data.get("capability")) or None,
            "task_state": task_state,
            "linked_trace_id": linked,
            "event_summary": _norm(data.get("event_summary")) or None,
            "event_detail": data.get("event_detail")
            if isinstance(data.get("event_detail"), (dict, list))
            else (
                json.loads(data["event_detail"])
                if isinstance(data.get("event_detail"), str) and data.get("event_detail")
                else {}
            ),
            "dimension_tags": tags if isinstance(tags, list) else (
                json.loads(tags) if isinstance(tags, str) and tags.strip().startswith("[") else []
            ),
            "event_timestamp": ts,
            "actor": _norm(data.get("actor")) or "worker",
            "comment": _norm(data.get("comment")) or None,
        }
        try:
            conn.execute(
                """
                INSERT INTO task_lifecycle_log (
                    lifecycle_id, task_id, event_sequence, event_type,
                    skill_id, skill_version, module, capability, task_state,
                    linked_trace_id, event_summary, event_detail_json,
                    dimension_tags_json, event_timestamp, actor, comment, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_out["lifecycle_id"],
                    row_out["task_id"],
                    row_out["event_sequence"],
                    row_out["event_type"],
                    row_out["skill_id"],
                    row_out["skill_version"],
                    row_out["module"],
                    row_out["capability"],
                    row_out["task_state"],
                    row_out["linked_trace_id"],
                    row_out["event_summary"],
                    _as_json(row_out["event_detail"], {}),
                    _as_json(row_out["dimension_tags"], []),
                    row_out["event_timestamp"],
                    row_out["actor"],
                    row_out["comment"],
                    SOURCE,
                ),
            )
        except sqlite3.IntegrityError as e:
            return {
                "ok": False,
                "result": "FAIL",
                "result_yes_no": "NO",
                "errors": [f"insert blocked: {e}"],
                "recommendations": ["event_sequence must be unique per task_id; lifecycle_id unique"],
                "expected_sequence": seq,
            }
        if commit:
            conn.commit()
        out = {
            "ok": True,
            "result": "PASS",
            "result_yes_no": "YES",
            "errors": [],
            "recommendations": [],
            "warnings": check.get("warnings") or [],
            "row": row_out,
            "NEW_LIFECYCLE_LOG_ROW": row_out,
            "skill_key": LIFECYCLE_SKILL_KEY,
            "version": LIFECYCLE_PRODUCT_VERSION,
        }
        out["report"] = _format_lifecycle_report(out, row_out)
        return out
    finally:
        if own and conn is not None:
            conn.close()


def _format_lifecycle_report(check: dict[str, Any], row: dict[str, Any] | None) -> str:
    yes = check.get("result_yes_no") or ("YES" if check.get("ok") else "NO")
    errors = check.get("errors") or []
    recs = check.get("recommendations") or []
    err_lines = "\n".join(f"- {e}" for e in errors) if errors else "- (none)"
    rec_line = "\n".join(recs) if recs else "(none)"
    body = json.dumps(row or {}, ensure_ascii=False, indent=2)
    return (
        f"Result: {yes}\n"
        f"NEW_LIFECYCLE_LOG_ROW:\n{body}\n"
        f"ERROR_LIST:\n{err_lines}\n"
        f"RECOMMENDATION:\n{rec_line}\n"
    )


def list_lifecycle_events(
    task_id: str,
    *,
    db_path: Path | str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    try:
        ensure_lifecycle_tables(conn)
        tid = _norm(task_id)
        rows = conn.execute(
            """
            SELECT * FROM task_lifecycle_log
            WHERE task_id = ?
            ORDER BY event_sequence ASC
            LIMIT ?
            """,
            (tid, max(1, min(int(limit), 2000))),
        ).fetchall()
        events = [_row_to_dict(r) for r in rows]
        return {"ok": True, "task_id": tid, "count": len(events), "events": events}
    finally:
        conn.close()


def insert_prompt_trace(
    payload: dict[str, Any] | None = None,
    *,
    db_path: Path | str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Minimal prompt snapshot write (assembler skill full text is Phase D)."""
    data = dict(payload or {})
    path = Path(db_path or DEFAULT_DB)
    if not path.is_file():
        return {"ok": False, "error": "agent.db missing"}
    task_id = _norm(data.get("task_id"))
    if not task_id:
        return {"ok": False, "error": "task_id required"}
    conn = _connect(path)
    try:
        ensure_lifecycle_tables(conn)
        trace_id = _norm(data.get("trace_id")) or f"tr_{uuid.uuid4().hex[:12]}"
        skill_id = _norm(data.get("skill_id"))
        tags = data.get("dimension_tags")
        if tags is None and skill_id:
            tags = DIMENSION_TAGS.get(skill_id, [])
        conn.execute(
            """
            INSERT INTO task_prompt_trace (
                trace_id, task_id, skill_id, skill_version, prompt_snapshot,
                variables_json, dimension_tags_json, module, capability, actor, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                task_id,
                skill_id or None,
                _norm(data.get("skill_version")) or None,
                data.get("prompt_snapshot") if data.get("prompt_snapshot") is not None else None,
                _as_json(data.get("variables"), {}),
                _as_json(tags, []),
                _norm(data.get("module")) or None,
                _norm(data.get("capability")) or None,
                _norm(data.get("actor")) or "worker",
                _norm(data.get("source")) or "prompt_trace",
            ),
        )
        if commit:
            conn.commit()
        row = _row_to_dict(
            conn.execute(
                "SELECT * FROM task_prompt_trace WHERE trace_id = ?", (trace_id,)
            ).fetchone()
        )
        return {"ok": True, "trace_id": trace_id, "row": row}
    except sqlite3.IntegrityError as e:
        return {"ok": False, "error": f"insert blocked: {e}"}
    finally:
        conn.close()


def list_prompt_traces(
    task_id: str,
    *,
    db_path: Path | str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    path = Path(db_path or DEFAULT_DB)
    conn = _connect(path)
    try:
        ensure_lifecycle_tables(conn)
        tid = _norm(task_id)
        rows = conn.execute(
            """
            SELECT * FROM task_prompt_trace
            WHERE task_id = ?
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (tid, max(1, min(int(limit), 2000))),
        ).fetchall()
        traces = [_row_to_dict(r) for r in rows]
        return {"ok": True, "task_id": tid, "count": len(traces), "traces": traces}
    finally:
        conn.close()


def get_task_timeline(
    task_id: str,
    *,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    life = list_lifecycle_events(task_id, db_path=db_path)
    traces = list_prompt_traces(task_id, db_path=db_path)
    by_trace = {t["trace_id"]: t for t in (traces.get("traces") or []) if t and t.get("trace_id")}
    joined = []
    for ev in life.get("events") or []:
        item = dict(ev or {})
        tid = item.get("linked_trace_id")
        item["prompt_trace"] = by_trace.get(tid) if tid else None
        joined.append(item)
    return {
        "ok": True,
        "task_id": _norm(task_id),
        "lifecycle_count": life.get("count") or 0,
        "trace_count": traces.get("count") or 0,
        "timeline": joined,
        "traces": traces.get("traces") or [],
    }


# ---------------------------------------------------------------------------
# Seed skills + T-SKILL02
# ---------------------------------------------------------------------------

def _skill_json_path(skill_key: str, version: str = "v1_strict") -> Path:
    """Legacy flat path (kept for callers); prefer resolve_skill_json()."""
    return SKILLS_DIR / skill_key / f"{version}.json"


def _resolve_skill_json(skill_key: str, version: str = "v1_strict") -> Path | None:
    """Category-aware lookup: flat OR skills/<catalog>/<key>/<ver>.json."""
    from skill_prompt import resolve_skill_json

    return resolve_skill_json(skill_key, version)


def _upsert_skill_from_file(
    conn: Any,
    skill_key: str,
    version: str,
    *,
    status: str,
    activate: bool,
) -> dict[str, Any]:
    from skill_prompt import upsert_skill_prompt, load_file_skill, file_skill_path

    bundled = _resolve_skill_json(skill_key, version)
    if bundled is None:
        return {"ok": False, "error": f"missing skill file {skill_key}/{version}"}
    data = json.loads(bundled.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"ok": False, "error": f"invalid skill json {bundled}"}
    tags = data.get("dimension_tags") or DIMENSION_TAGS.get(skill_key) or []
    notes = data.get("notes") or ""
    if tags and "dimension_tags" not in notes:
        notes = (notes + f" | dimension_tags={json.dumps(tags, ensure_ascii=False)}").strip(" |")
    row = upsert_skill_prompt(
        conn,
        skill_key=data["skill_key"],
        version_label=data.get("version_label") or version,
        prompt_text=data.get("prompt_text") or "",
        prompt_key=data.get("prompt_key") or "main",
        status=status,
        parser=data.get("parser") or "result_yes_no",
        output_schema=data.get("output_schema") or "result_yes_no",
        model_default=data.get("model_default") or "qwen2.5:7b-instruct",
        hard_rules=data.get("hard_rules") or [],
        source=data.get("source") or SOURCE,
        notes=notes,
        activate=activate,
        commit=False,
    )
    return {"ok": True, "skill_key": skill_key, "seeded": row, "dimension_tags": tags}


def _seed_discovered_skills(
    conn: Any,
    already: dict[str, Any],
) -> dict[str, Any]:
    """Register every skill JSON twin found under skills/ that has no SSOT row.

    Category-aware (flat + skills/<catalog>/<key>/<ver>.json). Never activates
    and never overwrites an existing row — promotion stays a human action in
    the Skill Prompt SSOT UI. This is what makes a newly added skill package
    appear in the dropdown without editing a hardcoded key list.

    Returns {"scanned": n, "inserted": {key: version}, "skipped": n}.
    """
    from skill_prompt import discover_skill_json_files, upsert_skill_prompt

    out: dict[str, Any] = {"scanned": 0, "inserted": {}, "skipped": 0}
    for p in discover_skill_json_files():
        out["scanned"] += 1
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        key = str(data.get("skill_key") or "").strip()
        ver = str(data.get("version_label") or p.stem).strip()
        if not key or not ver:
            continue
        if key in already and (already.get(key) or {}).get("ok"):
            out["skipped"] += 1
            continue
        exists = conn.execute(
            """
            SELECT id FROM skill_prompt_ssot
            WHERE skill_key = ? AND prompt_key = 'main' AND version_label = ?
            """,
            (key, ver),
        ).fetchone()
        if exists:
            out["skipped"] += 1
            continue
        tags = data.get("dimension_tags") or DIMENSION_TAGS.get(key) or []
        notes = data.get("notes") or ""
        if tags and "dimension_tags" not in notes:
            notes = (
                notes + f" | dimension_tags={json.dumps(tags, ensure_ascii=False)}"
            ).strip(" |")
        try:
            upsert_skill_prompt(
                conn,
                skill_key=key,
                version_label=ver,
                prompt_text=data.get("prompt_text") or "",
                prompt_key=data.get("prompt_key") or "main",
                status="draft",
                parser=data.get("parser") or "result_yes_no",
                output_schema=data.get("output_schema") or "result_yes_no",
                model_default=data.get("model_default") or "qwen2.5:7b-instruct",
                hard_rules=data.get("hard_rules") or [],
                source="seed_discovered",
                notes=notes,
                activate=False,
                commit=False,
            )
        except Exception as e:
            out.setdefault("errors", {})[key] = f"{type(e).__name__}: {e}"
            continue
        out["inserted"][key] = ver
    return out


def seed_lifecycle_skills(
    db_path: Path | str | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """Seed lifecycle recorder (active) + draft pipeline skills + tag refresh."""
    from skill_prompt import _connect as skill_connect, ensure_skill_tables
    from db_schema import ensure_task_center_schema, upsert_task_ssot
    from managed_coding import register_managed_function

    path = Path(db_path or DEFAULT_DB)
    out: dict[str, Any] = {"ok": True, "skills": {}}
    if not path.is_file():
        return {"ok": False, "error": "agent.db missing"}

    conn = skill_connect(path)
    try:
        ensure_skill_tables(conn)
        ensure_task_center_schema(conn)
        ensure_lifecycle_tables(conn)

        # active lifecycle
        out["skills"][LIFECYCLE_SKILL_KEY] = _upsert_skill_from_file(
            conn, LIFECYCLE_SKILL_KEY, LIFECYCLE_SKILL_VERSION, status="active", activate=True
        )
        # draft templates (no activate)
        for key in (
            "skill_requirement_research",
            "skill_proposal_design",
            "skill_api_standard_validator",
            "skill_proposal_validate",
        ):
            out["skills"][key] = _upsert_skill_from_file(
                conn, key, "v1_draft", status="draft", activate=False
            )
        # post-QC work order builder (formal v0.2 contract; file label v0_draft)
        out["skills"]["skill_worker_code_builder"] = _upsert_skill_from_file(
            conn,
            "skill_worker_code_builder",
            "v0_draft",
            status="draft",
            activate=False,
        )
        # refresh tags on existing actives via file re-upsert if present
        for key, ver in (
            ("mouse_spot_verify", "v1_strict"),
            ("skill_task_format_worker_taxonomy_validator", "v1_strict"),
        ):
            p = _resolve_skill_json(key, ver)
            if p is not None:
                out["skills"][key] = _upsert_skill_from_file(
                    conn, key, ver, status="active", activate=True
                )

        # auto-discover any other skill package on disk (category-aware) so a
        # new skills/<catalog>/<key>/<ver>.json gets registered without editing
        # a hardcoded key list. Never activates: drafts stay drafts.
        try:
            out["discovered"] = _seed_discovered_skills(conn, out["skills"])
        except Exception as e:
            out["discovered_warn"] = f"{type(e).__name__}: {e}"

        # T-SKILL02
        channel_id = _get_or_create_dim(conn, "channel", CHANNEL_CODE, CHANNEL_NAME)
        module_id = _get_or_create_dim(conn, "module", MODULE_CODE, MODULE_NAME)
        version_id = _resolve_version_id(conn, channel_id, module_id, VERSION_LABEL)
        action_id = _ensure_action(conn)
        payload = {
            "pipeline": "lifecycle_trace",
            "skill_key": LIFECYCLE_SKILL_KEY,
            "skill_version": LIFECYCLE_PRODUCT_VERSION,
            "channel": CHANNEL_CODE,
            "module": MODULE_CODE,
            "capability": LIFECYCLE_CAPABILITY,
            "api": LIFECYCLE_API,
            "function": LIFECYCLE_FUNCTION,
            "table": "task_lifecycle_log",
            "field": "lifecycle_id, task_id, event_sequence, event_type, module, capability, task_state, linked_trace_id",
            "dimension_tags": DIMENSION_TAGS[LIFECYCLE_SKILL_KEY],
        }
        title = "T-SKILL02 — Lifecycle Trace Recorder"
        existing = conn.execute(
            "SELECT id FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, LIFECYCLE_TASK_LABEL),
        ).fetchone()
        if existing:
            task_id = int(existing[0])
            conn.execute(
                """
                UPDATE dev_task
                SET title = ?, payload_json = ?, module_id = ?, channel_id = ?,
                    action_name_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    title,
                    json.dumps(payload, ensure_ascii=False),
                    module_id,
                    channel_id,
                    action_id,
                    task_id,
                ),
            )
            out["task_action"] = "updated"
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
                    action_id,
                    version_id,
                    LIFECYCLE_TASK_LABEL,
                    title,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            task_id = int(cur.lastrowid)
            out["task_action"] = "created"
        out["task_id"] = task_id
        out["version_id"] = version_id

        dims = {
            "task": title,
            "channel": CHANNEL_CODE,
            "module": MODULE_CODE,
            "capability": LIFECYCLE_CAPABILITY,
            "api": LIFECYCLE_API,
            "function": LIFECYCLE_FUNCTION,
            "table": "task_lifecycle_log",
            "field": "lifecycle_id, task_id, event_sequence, event_type, module, capability, task_state, linked_trace_id",
            "skill_key": LIFECYCLE_SKILL_KEY,
            "dimension_tags": json.dumps(DIMENSION_TAGS[LIFECYCLE_SKILL_KEY], ensure_ascii=False),
            "linked_table": "task_prompt_trace",
            "state_machine": " → ".join(TASK_STATES),
        }
        n = 0
        for i, (k, v) in enumerate(dims.items()):
            upsert_task_ssot(
                conn,
                task_id=task_id,
                dim_key=k,
                value_text=str(v),
                value_type="json" if k in ("dimension_tags",) else "string",
                source=SOURCE,
                sort_order=i,
                commit=False,
            )
            n += 1
        out["dims"] = n

        reg = register_managed_function(
            conn,
            task_id=task_id,
            tacid=LIFECYCLE_TASK_LABEL,
            module_name=MODULE_CODE,
            function_name=LIFECYCLE_FUNCTION,
            file_path="src/task_center/lifecycle_log.py",
            status="active",
            notes="Append-only task_lifecycle_log writer",
            source=SOURCE,
            commit=False,
        )
        out["register"] = reg

        # self-check append dry validate only
        chk = validate_lifecycle_payload(
            {
                "task_id": "__seed_probe__",
                "event_type": "requirement_received",
                "task_state": "draft",
                "event_sequence": 1,
            },
            conn,
        )
        out["self_check_validate"] = {"ok": chk.get("ok"), "result": chk.get("result")}

        if commit:
            conn.commit()
        return out
    except Exception as e:
        out["ok"] = False
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    finally:
        conn.close()


def _get_or_create_dim(conn: sqlite3.Connection, table: str, code: str, name: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(f"INSERT INTO {table} (code, name) VALUES (?, ?)", (code, name))
    return int(cur.lastrowid)


def _resolve_version_id(
    conn: sqlite3.Connection,
    channel_id: int,
    module_id: int,
    version_label: str = VERSION_LABEL,
) -> int:
    row = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        """,
        (channel_id, module_id, version_label),
    ).fetchone()
    if row:
        return int(row[0])
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
            "Task Center skill gates",
            "Lifecycle + taxonomy skills",
        ),
    )
    return int(cur.lastrowid)


def _ensure_action(conn: sqlite3.Connection) -> int:
    code = "function.define"
    row = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?", (code,)
    ).fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        """
        INSERT INTO task_action_name
            (element, action, code, name, requires_tdd, status)
        VALUES ('function', 'define', ?, 'Define function', 0, 'active')
        """,
        (code,),
    )
    return int(cur.lastrowid)


if __name__ == "__main__":
    import sys

    cmd = (sys.argv[1] if len(sys.argv) > 1 else "seed").strip()
    if cmd == "seed":
        print(json.dumps(seed_lifecycle_skills(), ensure_ascii=False, indent=2))
    else:
        print("usage: python -m src.task_center.lifecycle_log seed")
