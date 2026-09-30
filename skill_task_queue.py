"""Skill Task Queue — model handoff relay.

A DB-backed queue where atomic tasks wait for a model. The Python runtime
polls, dispatches, validates JSON against an output_schema, and drives the
state machine. When a model keeps failing schema validation past max_retry,
the task is marked ``handoff`` and the NEXT model from the runtime escalation
pool is picked by the worker — NOT hard-coded to any specific model.

State machine (driven by Python, not the LLM):
    pending -> running -> success
                        -> retry (retry_count+1; same model next round)
                        -> handoff (retry exhausted; escalation pool next)
    handoff -> running -> success
                        -> failed (escalation pool also failed; human review)

Guardrails:
1. assigned_model is decided by the worker at dispatch time; the task creator
   only sets the initial model. handoff picks the next model from
   HANDOFF_MODELS (escalation pool), never a hard-coded 27B.
2. Every handoff / failed task is auto-logged to skill_mismatch_log so it
   becomes case-study material for the Skill Learning Center.
3. JSON validation is lightweight (no jsonschema dependency): required keys +
   type checks against the output_schema.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

import skill_prompt

DEFAULT_DB = skill_prompt.DEFAULT_DB
DEFAULT_MODEL = "qwen2.5:7b-instruct"
# Escalation pool for handoff. Runtime picks the next model here — NOT a
# hard-coded 27B. Extend this list to add more handoff targets.
HANDOFF_MODELS = ["qwen2.5:7b-instruct"]
MAX_RETRY = 2

# config.yaml path (repo root). Escalation pool is read from here when present.
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
# The DB-driven service route whose provider pool IS the escalation pool.
ESCALATION_LLM_ROUTE = "llm.text"
# Fallback pool when the registry is unreadable AND config.yaml has no pool.
DEFAULT_ESCALATION_POOL = ["qwen2.5:7b-instruct", "qwen2.5vl:7b"]


# Which source answered the last load_escalation_pool() call. A fallback must be
# REPORTED, not inferred: `registry` is the DB-driven truth, `config.yaml` and
# `default` are degraded answers, and a caller cannot tell them apart from the list.
_LAST_POOL_SOURCE: str = "unknown"


def load_escalation_pool(db_path: Path | str | None = None) -> list[str]:
    """The escalation pool, read from the DB-driven service registry.

    DEFECT FOUND BY RUNNING IT (2026-09-21): this used to read a hardcoded model
    array from `config.yaml`, and it had drifted — it named `qwen3.8:27B`, which
    is `local=0` and not on this machine, while the default model
    `qwen2.5:7b-instruct` was ABSENT from it. `get_next_model()` then returned
    None for every handoff and every escalation died with "escalation pool
    exhausted". A hand-written list drifts from the registry; the registry is
    what the dispatcher must read.

    DEFECT FOUND BY THE LIVE-DB SENTINEL (2026-09-24): this took NO db_path and
    called `_connect(None)`, which resolves to the module DEFAULT — the LIVE
    database. Its caller inside `mark_handoff(..., db_path=...)` HAD the path in
    scope and it was silently DISCARDED, so a caller naming a temp DB still had
    the pool resolved against a different database than the task it was deciding
    about. The path is now accepted and propagated.

    Order of preference:
      1. the provider table for `llm.text` (DB-driven, the real source)
      2. `config.yaml` — only as a FALLBACK, and the reason is recorded
      3. `DEFAULT_ESCALATION_POOL` — the last resort
    `pool_source()` reports WHICH of the three answered.
    """
    global _LAST_POOL_SOURCE
    try:
        import llm_service_store as lss

        conn = _connect(db_path)
        try:
            pool = lss.pool_for(conn, ESCALATION_LLM_ROUTE)
        finally:
            conn.close()
        if pool:
            _LAST_POOL_SOURCE = "registry"
            return pool
    except Exception:
        pass
    # Fallback: config.yaml. Kept so a DB that predates the registry still runs,
    # but it is a fallback, not the source of truth.
    try:
        if CONFIG_PATH.is_file():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            pool = (cfg.get("llm_escalation") or {}).get("pool") or []
            if pool:
                _LAST_POOL_SOURCE = "config.yaml"
                return [str(m) for m in pool if str(m).strip()]
    except Exception:
        pass
    _LAST_POOL_SOURCE = "default"
    return list(DEFAULT_ESCALATION_POOL)


def pool_source() -> str:
    """Which source answered the last pool load: `registry` / `config.yaml` / `default`."""
    return _LAST_POOL_SOURCE


def get_next_model(current_model: str, pool: list[str]) -> str | None:
    """The next model to escalate to, or None when there is genuinely none.

    DEFECT FOUND BY RUNNING IT (2026-09-21): this used to return None whenever
    `current_model` was not IN the pool. The default model
    (`qwen2.5:7b-instruct`) was not in the configured pool, so EVERY handoff
    returned None and `mark_handoff()` immediately marked the task failed with
    "escalation pool exhausted". The escalation mechanism was dead from day one.

    "Not in the pool" is not the same as "no upgrade available". A model absent
    from the pool is simply not a pool member, so the pool's FIRST entry is the
    upgrade target. None is returned only when the pool holds no target that
    differs from the current model.
    """
    if not pool:
        return None
    if current_model in pool:
        idx = pool.index(current_model)
        if idx + 1 < len(pool):
            return pool[idx + 1]
        return None
    # Not a pool member: the first entry that is not the current model.
    for m in pool:
        if m != current_model:
            return m
    return None

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_RETRY = "retry"
STATUS_HANDOFF = "handoff"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_RETRY,
    STATUS_HANDOFF,
    STATUS_SUCCESS,
    STATUS_FAILED,
}
TERMINAL_STATUSES = {STATUS_SUCCESS, STATUS_FAILED}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = skill_prompt._connect(db_path)
    skill_prompt.ensure_skill_tables(conn)
    return conn


def _parse_json(raw: Any, default: Any = None) -> Any:
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


def _serialize(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    return {
        "task_id": d.get("task_id"),
        "skill_id": d.get("skill_id"),
        "task_type": d.get("task_type"),
        "payload": _parse_json(d.get("payload"), {}),
        "output_schema": _parse_json(d.get("output_schema"), {}),
        "assigned_model": d.get("assigned_model"),
        "retry_count": int(d.get("retry_count") or 0),
        "max_retry": int(d.get("max_retry") or 0),
        "handoff_count": int(d.get("handoff_count") or 0),
        "status": d.get("status"),
        "result": _parse_json(d.get("result"), None),
        "error_msg": d.get("error_msg"),
        "created_at": d.get("created_at"),
        "finished_at": d.get("finished_at"),
    }


# ---------------------------------------------------------------- schema check

def _type_ok(value: Any, expected: str) -> bool:
    expected = (expected or "").strip().lower()
    if expected in ("string", "str", "text"):
        return isinstance(value, str)
    if expected in ("integer", "int", "number"):
        return isinstance(value, int) and not isinstance(value, bool)
    if expected in ("boolean", "bool"):
        return isinstance(value, bool)
    if expected in ("array", "list"):
        return isinstance(value, list)
    if expected in ("object", "dict"):
        return isinstance(value, dict)
    if expected in ("null", "none"):
        return value is None
    if expected in ("any", ""):
        return True
    return True


def validate_output(raw: str, output_schema: Any) -> tuple[bool, Any, str]:
    """Validate an LLM output against a lightweight JSON schema.

    output_schema may be:
      - a dict with "required" (list) and "properties" ({key: type})
      - a plain dict of {key: type} (all keys required)
      - a list of allowed values (enum)
      - a type name string ("object", "array", ...)
    Returns (ok, parsed, error).
    """
    schema = output_schema
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except Exception:
            schema = schema  # treat as type name

    parsed = _parse_json(raw, None)
    if parsed is None:
        return False, None, "output is not valid JSON"

    # enum list
    if isinstance(schema, list):
        if parsed in schema:
            return True, parsed, ""
        return False, parsed, f"value {parsed!r} not in allowed set"

    # type name string
    if isinstance(schema, str):
        if _type_ok(parsed, schema):
            return True, parsed, ""
        return False, parsed, f"expected type {schema!r}, got {type(parsed).__name__}"

    if not isinstance(schema, dict):
        return True, parsed, ""

    if not isinstance(parsed, dict):
        return False, parsed, f"expected object, got {type(parsed).__name__}"

    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    if isinstance(required, list):
        for key in required:
            if key not in parsed:
                return False, parsed, f"missing required key: {key}"
    for key, expected in properties.items():
        if key in parsed and not _type_ok(parsed[key], expected):
            return False, parsed, (
                f"key {key!r} expected type {expected!r}, "
                f"got {type(parsed[key]).__name__}"
            )
    return True, parsed, ""


# ---------------------------------------------------------------- queue ops

def enqueue(
    skill_id: str,
    task_type: str,
    payload: Any,
    output_schema: Any,
    *,
    model: str | None = None,
    max_retry: int = MAX_RETRY,
    task_id: str | None = None,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Insert one pending skill_task_queue row. assigned_model defaults to 7B."""
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        tid = (task_id or "").strip() or f"stq_{uuid.uuid4().hex[:12]}"
        payload_json = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, default=str)
        )
        schema_json = (
            output_schema
            if isinstance(output_schema, str)
            else json.dumps(output_schema, ensure_ascii=False, default=str)
        )
        model_id = (model or "").strip() or DEFAULT_MODEL
        try:
            mr = max(0, int(max_retry))
        except (TypeError, ValueError):
            mr = MAX_RETRY
        conn.execute(
            """
            INSERT INTO skill_task_queue (
                task_id, skill_id, task_type, payload, output_schema,
                assigned_model, retry_count, max_retry, status,
                result, error_msg, created_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'pending', NULL, NULL,
                      datetime('now'), NULL)
            """,
            (tid, skill_id, task_type, payload_json, schema_json, model_id, mr),
        )
        if own:
            conn.commit()
        row = conn.execute(
            "SELECT * FROM skill_task_queue WHERE task_id=?", (tid,)
        ).fetchone()
        return {"ok": True, "task_id": tid, "row": _serialize(row)}
    except sqlite3.IntegrityError as e:
        return {"ok": False, "error": f"enqueue blocked: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            conn.close()


def poll_next(
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the next pending/retry/handoff task -> running.

    Optimistic-lock: the SELECT (pick oldest) and UPDATE (mark running) are
    merged into ONE atomic UPDATE ... WHERE task_id=(SELECT ...). Only
    rowcount>0 means this worker won the claim, so concurrent workers cannot
    double-process the same task. Returns serialized row or None.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """
            UPDATE skill_task_queue
            SET status = 'running'
            WHERE task_id = (
                SELECT task_id FROM skill_task_queue
                WHERE status IN ('pending','retry','handoff')
                ORDER BY created_at ASC, task_id ASC
                LIMIT 1
            )
            AND status IN ('pending','retry','handoff')
            """
        )
        conn.execute("COMMIT")
        if cur.rowcount == 0:
            return None
        # Read back the claimed row (the one we just set to running).
        claimed = conn.execute(
            """
            SELECT * FROM skill_task_queue
            WHERE status='running'
            ORDER BY created_at ASC, task_id ASC
            LIMIT 1
            """
        ).fetchone()
        if not claimed:
            return None
        return _serialize(claimed)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return None
    finally:
        if own:
            conn.close()


def get_task(
    task_id: str,
    db_path: Path | str | None = None,
    *,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any] | None:
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        row = conn.execute(
            "SELECT * FROM skill_task_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        return _serialize(row)
    finally:
        if own:
            conn.close()


def list_tasks(
    *,
    skill_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
    db_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM skill_task_queue WHERE 1=1"
        args: list[Any] = []
        if skill_id:
            sql += " AND skill_id=?"
            args.append(skill_id)
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, int(limit)))
        rows = conn.execute(sql, args).fetchall()
        return [_serialize(r) for r in rows]
    finally:
        conn.close()


def _update(task_id: str, fields: dict[str, Any], conn: sqlite3.Connection) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(
        f"UPDATE skill_task_queue SET {cols} WHERE task_id=?",
        (*fields.values(), task_id),
    )


def complete(
    task_id: str,
    result: Any,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        result_json = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        _update(
            task_id,
            {
                "status": STATUS_SUCCESS,
                "result": result_json,
                "error_msg": None,
                "finished_at": _utc_now(),
            },
            conn,
        )
        if own:
            conn.commit()
        row = conn.execute(
            "SELECT * FROM skill_task_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        return {"ok": True, "task_id": task_id, "row": _serialize(row)}
    finally:
        if own:
            conn.close()


def mark_retry(
    task_id: str,
    error: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        cur = conn.execute(
            "SELECT retry_count, max_retry FROM skill_task_queue WHERE task_id=?",
            (task_id,),
        ).fetchone()
        rc = int(cur["retry_count"]) + 1 if cur else 1
        mr = int(cur["max_retry"]) if cur else MAX_RETRY
        _update(
            task_id,
            {
                "status": STATUS_RETRY,
                "retry_count": rc,
                "error_msg": str(error)[:2000],
            },
            conn,
        )
        if own:
            conn.commit()
        return {"ok": True, "task_id": task_id, "retry_count": rc, "max_retry": mr}
    finally:
        if own:
            conn.close()


def mark_handoff(
    task_id: str,
    error: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Escalate: pick the next model from the escalation pool (NOT hard-coded).

    If the current model is the last pool entry (or not in the pool), the task
    is marked FAILED instead — no further upgrade is possible.
    """
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        cur = conn.execute(
            "SELECT assigned_model, handoff_count FROM skill_task_queue WHERE task_id=?",
            (task_id,),
        ).fetchone()
        current = str(cur["assigned_model"]) if cur else DEFAULT_MODEL
        hc = int(cur["handoff_count"]) + 1 if cur else 1
        # Propagate the caller's path: without it the pool was read from the LIVE
        # default while this decision was being made against `db_path` (2026-09-24).
        pool = load_escalation_pool(db_path)
        next_model = get_next_model(current, pool)
        if next_model is None:
            # escalation pool exhausted -> final failure (human review)
            mark_failed(task_id, "escalation pool exhausted, no more model available", conn=conn)
            if own:
                conn.commit()
            return {
                "ok": True,
                "task_id": task_id,
                "handoff_to": None,
                "from_model": current,
                "status": STATUS_FAILED,
                "reason": "escalation_pool_exhausted",
            }
        _update(
            task_id,
            {
                "status": STATUS_HANDOFF,
                "assigned_model": next_model,
                "handoff_count": hc,
                "error_msg": str(error)[:2000],
            },
            conn,
        )
        if own:
            conn.commit()
        return {
            "ok": True,
            "task_id": task_id,
            "handoff_to": next_model,
            "from_model": current,
            "status": STATUS_HANDOFF,
        }
    finally:
        if own:
            conn.close()


def mark_failed(
    task_id: str,
    error: str,
    *,
    db_path: Path | str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    own = False
    if conn is None:
        conn = _connect(db_path)
        own = True
    try:
        _update(
            task_id,
            {
                "status": STATUS_FAILED,
                "error_msg": str(error)[:2000],
                "finished_at": _utc_now(),
            },
            conn,
        )
        if own:
            conn.commit()
        return {"ok": True, "task_id": task_id}
    finally:
        if own:
            conn.close()


def _next_handoff_model(current: str, db_path: Path | str | None = None) -> str:
    """Pick the next model in the escalation pool after ``current``.

    If current is not in the pool, return the first pool model. If current is
    the last pool model, stay on it (worker will mark failed next round).

    `db_path` is accepted for the same reason `mark_handoff` takes it: the pool
    lookup must read the database the caller is working against, not the DEFAULT.
    """
    pool = load_escalation_pool(db_path)
    if not pool:
        return current
    if current in pool:
        idx = pool.index(current)
        if idx + 1 < len(pool):
            return pool[idx + 1]
        return pool[idx]
    return pool[0]


# ---------------------------------------------------------------- worker

def _llm_call(prompt: str, *, model: str, timeout: float = 180.0) -> str:
    """Text-only Ollama chat completion (temperature 0.0)."""
    from vision_analyze import complete_text

    result = complete_text(prompt, model=model, format_json=False, timeout=timeout)
    if result.error:
        raise RuntimeError(f"{result.error}: {result.summary}")
    return result.raw_text or ""


def _build_prompt(task: dict[str, Any]) -> str:
    payload = task.get("payload") or {}
    if isinstance(payload, dict):
        return json.dumps(payload, ensure_ascii=False, default=str)
    return str(payload)


def worker_once(
    *,
    db_path: Path | str | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Claim one task and run it through the state machine.

    Returns a summary dict. If no task is available, returns {"ok": True,
    "claimed": False}.
    """
    conn = _connect(db_path)
    try:
        task = poll_next(conn=conn)
        if not task:
            return {"ok": True, "claimed": False}
        tid = str(task["task_id"])
        model = str(task.get("assigned_model") or DEFAULT_MODEL)
        schema = task.get("output_schema") or {}
        prompt = _build_prompt(task)
        raw = ""
        try:
            raw = _llm_call(prompt, model=model, timeout=timeout)
        except Exception as e:
            raw = ""
            err = f"{type(e).__name__}: {e}"
            out = _handle_failure(conn, task, err, raw)
            conn.commit()
            return out

        ok, parsed, verr = validate_output(raw, schema)
        if ok:
            complete(tid, parsed, conn=conn)
            conn.commit()
            return {
                "ok": True,
                "claimed": True,
                "task_id": tid,
                "status": STATUS_SUCCESS,
                "model": model,
                "result": parsed,
            }
        out = _handle_failure(conn, task, verr or "schema validation failed", raw)
        conn.commit()
        return out
    finally:
        conn.close()


def _handle_failure(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    error: str,
    raw: str,
) -> dict[str, Any]:
    tid = str(task["task_id"])
    model = str(task.get("assigned_model") or DEFAULT_MODEL)
    rc = int(task.get("retry_count") or 0)
    mr = int(task.get("max_retry") or MAX_RETRY)
    skill_id = str(task.get("skill_id") or "unknown")
    # Was this task already handed off (handoff_count>0)? If so, the
    # escalation model also failed -> final failure (human review).
    was_handoff = int(task.get("handoff_count") or 0) > 0

    if was_handoff:
        # escalation pool model failed too -> final failure (human review)
        mark_failed(tid, error, conn=conn)
        _log_mismatch(conn, task, raw, error, model, status="failed")
        return {
            "ok": True,
            "claimed": True,
            "task_id": tid,
            "status": STATUS_FAILED,
            "model": model,
            "error": error,
        }

    if rc < mr:
        mark_retry(tid, error, conn=conn)
        return {
            "ok": True,
            "claimed": True,
            "task_id": tid,
            "status": STATUS_RETRY,
            "model": model,
            "retry_count": rc + 1,
            "error": error,
        }

    # retry exhausted -> handoff (escalation pool next model)
    ho = mark_handoff(tid, error, conn=conn)
    if ho.get("status") == STATUS_FAILED:
        # escalation pool exhausted -> final failure (human review)
        _log_mismatch(conn, task, raw, error, model, status="failed")
        return {
            "ok": True,
            "claimed": True,
            "task_id": tid,
            "status": STATUS_FAILED,
            "model": model,
            "error": error,
            "reason": "escalation_pool_exhausted",
        }
    _log_mismatch(conn, task, raw, error, model, status="handoff")
    return {
        "ok": True,
        "claimed": True,
        "task_id": tid,
        "status": STATUS_HANDOFF,
        "model": model,
        "handoff_to": ho.get("handoff_to"),
        "error": error,
    }


def _log_mismatch(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    raw: str,
    error: str,
    model: str,
    *,
    status: str,
) -> None:
    """Auto-log handoff/failed tasks into skill_mismatch_log (case-study fodder).

    error_reason: 'schema_validation_exceed_retry' on handoff,
                  'escalation_model_failed' on final failed.
    """
    reason = (
        "escalation_model_failed"
        if status == "failed"
        else "schema_validation_exceed_retry"
    )
    try:
        skill_prompt.log_mismatch(
            conn,
            skill_key=str(task.get("skill_id") or "unknown"),
            version_label=str(task.get("task_type") or ""),
            image_path=None,
            target_name=None,
            ask_output=(raw or "")[:500] or None,
            expected=json.dumps(task.get("output_schema") or {}, ensure_ascii=False)[:500],
            raw_response=(raw or "")[:2000],
            model=model,
            run_id=str(task.get("task_id") or ""),
            error_reason=reason,
        )
    except Exception:
        pass


# ---------------------------------------------------------------- overview

def overview(*, db_path: Path | str | None = None) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        def _count(sql: str, args: tuple = ()) -> int:
            row = conn.execute(sql, args).fetchone()
            return int(row[0]) if row else 0

        return {
            "ok": True,
            "pending": _count("SELECT COUNT(*) FROM skill_task_queue WHERE status='pending'"),
            "running": _count("SELECT COUNT(*) FROM skill_task_queue WHERE status='running'"),
            "retry": _count("SELECT COUNT(*) FROM skill_task_queue WHERE status='retry'"),
            "handoff": _count("SELECT COUNT(*) FROM skill_task_queue WHERE status='handoff'"),
            "success": _count("SELECT COUNT(*) FROM skill_task_queue WHERE status='success'"),
            "failed": _count("SELECT COUNT(*) FROM skill_task_queue WHERE status='failed'"),
            "total": _count("SELECT COUNT(*) FROM skill_task_queue"),
        }
    finally:
        conn.close()