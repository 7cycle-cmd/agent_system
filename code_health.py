"""Pipeline C — Code Health + Function Trace (CH0–CH4).

WHY (anti rubbish-mountain): every enrolled module.function must be auditable —
registered (W1), optionally run under task_label/TACID (W2), objectively scored (W3).
Never a structure gate. Never auto-delete source. Never invent event_type zoo.

Phases:
  CH0 — contracts_doc / WHY catalog (this module)
  CH1 — DDL live in db_schema; helpers here for verify
  CH2 — function_invoker + rollup + demo (W8,W2,W3,W9,W10)
  CH3 — harvest task_ssot impl.* → static refs / zombie (W1,W4,W5)
  CH4 — generate_code_health_report + branch query (W6,W7)
  CH5 — wire supervise + run_qc_for_task via invoker (W8)
  CH6 — Task Center /health read-only panel

Unique function names are DB-driven via function_scoring UNIQUE(module,function)
reservation + optional task_ssot impl.* register — not wall-clock tags.

Cite WHY-IDs W1–W10 in PRs. No WHY → out of scope.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Callable
# ---------------------------------------------------------------------------
# CH0 — locked contracts (machine-readable)
# ---------------------------------------------------------------------------

PIPELINE_ID = "C_code_health"
GATE_POLICY = "never"  # A=PRAGMA only; B=FH never; C=health never

WHY_CATALOG: dict[str, dict[str, str]] = {
    "W1": {
        "need": "What fn does the system claim to use?",
        "build": "task_ssot impl.module / impl.function / impl.required",
        "not": "AI or import-graph guess",
    },
    "W2": {
        "need": "Did it run under a task_label (TACID)?",
        "build": "function_invoke_trace append-only",
        "not": "Full APM/profiler product",
    },
    "W3": {
        "need": "Objective quality of a function",
        "build": "functional_score = success/total*100 or null",
        "not": "LLM subjective scores",
    },
    "W4": {
        "need": "Declared but never hit",
        "build": "status=zombie",
        "not": "Delete as dead code",
    },
    "W5": {
        "need": "Undeclared and never hit",
        "build": "status=dead_candidate mark only",
        "not": "Auto-delete .py",
    },
    "W6": {
        "need": "Experiment branch audit (e.g. 1.1*)",
        "build": "get_tacid_branch_function_report",
        "not": "Brittle JSON LIKE only",
    },
    "W7": {
        "need": "Human rot dashboard",
        "build": "generate_code_health_report + UI",
        "not": "Score as CI/QC gate",
    },
    "W8": {
        "need": "Stats must not lie",
        "build": "function_invoker choke point",
        "not": "Wrap stdlib day-one",
    },
    "W9": {
        "need": "Replay / provenance",
        "build": "trace rows never erase hits",
        "not": "Overwrite hit history away",
    },
    "W10": {
        "need": "Fast UI rollup",
        "build": "function_scoring counters",
        "not": "Rollup as only store",
    },
    "W11": {
        "need": "Where is the code (file + line range + optional body)?",
        "build": "code_register.file_path/line_start/line_end/code_span",
        "not": "Guess from import graph only",
    },
    "W12": {
        "need": "Watchdog/health opens cleanup task for dead/zombie",
        "build": "spawn_dead_function_cleanup_tasks → dev_task code.cleanup",
        "not": "Auto-delete source files",
    },
}

# Vocabulary aliases (plan term → repo)
VOCAB = {
    "TACID": "dev_task.task_label (+ optional dev_task.id)",
    "action_register": "task_action_name",
    "static_require": "task_ssot dim_key impl.module|impl.function|impl.required",
    "dynamic_hit": "function_invoke_trace.tacid",
    "score_table": "function_scoring",
    "db": "agent.db",
}

# task_ssot dim keys for static enrollment (W1)
IMPL_DIM_MODULE = "impl.module"
IMPL_DIM_FUNCTION = "impl.function"
IMPL_DIM_REQUIRED = "impl.required"
IMPL_DIM_KEYS = (IMPL_DIM_MODULE, IMPL_DIM_FUNCTION, IMPL_DIM_REQUIRED)

FUNCTION_STATUSES = ("active", "zombie", "inactive", "dead_candidate")

# v1 enrollment allowlist intent (CH2 wires these; CH0 documents only)
V1_ENROLLED_SOURCES = (
    "fail_handling.supervise.assemble",
    "fail_handling.supervise.vision",
    "fail_handling.supervise.match",
    "fail_handling.supervise.report",
    "schema_qc.run_qc_for_task",
    "code_health.demo",
    # OpenClaw Companion Local MCP (register_id spine — openclaw_mcp_trace)
    "openclaw_companion.mcp.screen_snapshot",
    "openclaw_companion.mcp.system_notify",
    "openclaw_companion.mcp.ping",
    "openclaw_companion.fallback.webbrowser_open",
)

V1_DO_NOT_WRAP = (
    "db_browser HTML render",
    "tiny sqlite helpers inside one request",
    "stdlib / third-party",
    "one-off boundary tests unless asserting invoker",
)

SCORE_LOW_THRESHOLD = 60.0  # report only; not a gate
CLEANUP_ACTION_CODE = "code.cleanup"
CLEANUP_TASK_PREFIX = "ch.cleanup"

TRACE_TABLE = "function_invoke_trace"
SCORING_TABLE = "function_scoring"


def contracts_doc() -> dict[str, Any]:
    """CH0 machine-readable contract summary (pipeline C)."""
    return {
        "pipeline": PIPELINE_ID,
        "phase": "CH0+CH1+CH2+CH3+CH4",
        "gate": GATE_POLICY,
        "depends_on_write_ssot": False,
        "db": VOCAB["db"],
        "why_catalog": WHY_CATALOG,
        "vocab": VOCAB,
        "impl_dim_keys": list(IMPL_DIM_KEYS),
        "function_statuses": list(FUNCTION_STATUSES),
        "score_formula": "total_invocations>0 ? (success_count/total_invocations)*100 : null",
        "score_alias": "usage=total_invocations; pass=success_count; fail=fail_count; score=pass/usage*100",
        "score_low_threshold_report_only": SCORE_LOW_THRESHOLD,
        "tables": {
            TRACE_TABLE: {
                "why": ["W2", "W9"],
                "mode": "append_only",
                "note": "sub_steps_json on parent; retries not top-level events",
            },
            SCORING_TABLE: {
                "why": ["W3", "W4", "W5", "W10", "W11"],
                "mode": "rollup_unique_module_function",
                "note": "UPDATE counters/status only; hits stay in trace forever; optional file/line mirror",
            },
            "code_register": {
                "why": ["W1", "W11"],
                "mode": "identity_plus_source_location",
                "note": "file_path + line_start/line_end + optional code_span",
            },
        },
        "status_rules": {
            "zombie": "total==0 and static_refs non-empty",
            "dead_candidate": "total==0 and static_refs empty",
            "active": "total>0 and recent hit (policy)",
            "inactive": "total>0 and not recent",
        },
        "cleanup_tasks": {
            "why": ["W4", "W5", "W12"],
            "action": CLEANUP_ACTION_CODE,
            "rule": "usage==0 and status in (zombie, dead_candidate) → pending code.cleanup task",
            "never": "auto-delete source",
        },
        "enrollment_v1": {
            "must_use_invoker": list(V1_ENROLLED_SOURCES),
            "do_not_wrap": list(V1_DO_NOT_WRAP),
        },
        "report_keys": [
            "report_time",
            "gate",
            "why",
            "zombie_functions",
            "dead_candidate_functions",
            "low_score_functions",
            "active_count",
            "inactive_count",
            "notes",
        ],
        "reject": [
            "parallel experiment.db",
            "score as QC gate",
            "AI writes functional_score",
            "DELETE trace hits for cleanup",
            "auto-delete source",
            "unregistered event_type",
            "invoker on every internal one-liner",
        ],
        "phases": {
            "CH0": "contracts (this doc)",
            "CH1": "DDL + migrate ensure_schema",
            "CH2": "function_invoker + demo",
            "CH3": "harvest task_ssot impl.* → status",
            "CH4": "queries + generate_code_health_report",
            "CH5": "wire supervisor + run_qc",
            "CH6": "Task Center health panel",
        },
        "related_pipelines": {
            "A": "hard_gate_pragma_exact_set",
            "B": "fail_handling",
            "C": PIPELINE_ID,
        },
    }


def compute_functional_score(success_count: int, total_invocations: int) -> float | None:
    """W3 — objective only. null if never invoked."""
    if total_invocations <= 0:
        return None
    return round(100.0 * float(success_count) / float(total_invocations), 4)


def derive_status(
    *,
    total_invocations: int,
    has_static_refs: bool,
    is_recent: bool | None = None,
) -> str:
    """W4/W5 status derivation (deterministic). is_recent unused when total==0."""
    if total_invocations <= 0:
        return "zombie" if has_static_refs else "dead_candidate"
    if is_recent is False:
        return "inactive"
    return "active"


# ---------------------------------------------------------------------------
# CH1 — schema verify helpers (DDL owned by db_schema)
# ---------------------------------------------------------------------------

REQUIRED_TRACE_COLUMNS = (
    "id",
    "ts",
    "tacid",
    "task_id",
    "module_name",
    "function_name",
    "ok",
    "error_text",
    "duration_ms",
    "parent_trace_id",
    "sub_steps_json",
    "source",
    "created_at",
)

REQUIRED_SCORING_COLUMNS = (
    "id",
    "module_name",
    "function_name",
    "total_invocations",
    "success_count",
    "fail_count",
    "functional_score",
    "ref_static_tacids",
    "ref_hit_tacids",
    "last_hit_tacid",
    "last_hit_time",
    "status",
    "updated_at",
    "created_at",
)


def verify_code_health_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """CH1: confirm trace + scoring tables and required columns exist."""

    def _cols(table: str) -> list[str]:
        return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]

    def _exists(table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None

    out: dict[str, Any] = {
        "gate": GATE_POLICY,
        "phase": "CH1",
        "why": ["W2", "W9", "W10"],
        "ok": True,
        "tables": {},
    }
    for table, required in (
        (TRACE_TABLE, REQUIRED_TRACE_COLUMNS),
        (SCORING_TABLE, REQUIRED_SCORING_COLUMNS),
    ):
        exists = _exists(table)
        cols = _cols(table) if exists else []
        missing = [c for c in required if c not in cols]
        out["tables"][table] = {
            "exists": exists,
            "columns": cols,
            "missing": missing,
        }
        if not exists or missing:
            out["ok"] = False
    return out


# ---------------------------------------------------------------------------
# CH2 — function_invoker + rollup (W8, W2, W3, W9, W10)
# ---------------------------------------------------------------------------

DEFAULT_SOURCE = "code_health"
DEMO_MODULE = "code_health"
DEMO_FUNCTION = "demo_probe"
DEMO_PARENT_FUNCTION = "demo_parent"
DEMO_ZOMBIE_FUNCTION = "demo_zombie_unused"
DEMO_TACID = "ch.demo"
DEMO_TASK_TITLE = "Code Health demo / register (pipeline C)"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sanitize_fn_token(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.]+", "_", (name or "").strip())
    s = s.strip("._") or "fn"
    return s[:120]


def _json_list(raw: Any) -> list[Any]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, str):
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return list(val) if isinstance(val, list) else []
    return []


def _append_unique_str(items: list[Any], value: str, *, limit: int = 200) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in list(items) + [value]:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _scoring_location_cols(conn: sqlite3.Connection) -> bool:
    cols = {
        str(r[1]) for r in conn.execute(f"PRAGMA table_info({SCORING_TABLE})").fetchall()
    }
    return "file_path" in cols


def get_function_score(
    conn: sqlite3.Connection,
    module_name: str,
    function_name: str,
) -> dict[str, Any] | None:
    """W3/W10 — read one rollup row (None if never seen)."""
    has_loc = _scoring_location_cols(conn)
    loc_sql = ", file_path, line_start, line_end, code_span, register_id" if has_loc else ""
    # register_id may exist without full location set
    if not has_loc:
        scols = {
            str(r[1])
            for r in conn.execute(f"PRAGMA table_info({SCORING_TABLE})").fetchall()
        }
        if "register_id" in scols:
            loc_sql = ", register_id"
    row = conn.execute(
        f"""
        SELECT module_name, function_name, total_invocations, success_count, fail_count,
               functional_score, ref_static_tacids, ref_hit_tacids,
               last_hit_tacid, last_hit_time, status, updated_at, created_at
               {loc_sql}
        FROM {SCORING_TABLE}
        WHERE module_name = ? AND function_name = ?
        """,
        (module_name, function_name),
    ).fetchone()
    if not row:
        return None
    out: dict[str, Any] = {
        "module_name": row[0],
        "function_name": row[1],
        "total_invocations": int(row[2] or 0),
        "usage": int(row[2] or 0),
        "success_count": int(row[3] or 0),
        "pass_count": int(row[3] or 0),
        "fail_count": int(row[4] or 0),
        "functional_score": row[5],
        "score": row[5],
        "ref_static_tacids": _json_list(row[6]),
        "ref_hit_tacids": _json_list(row[7]),
        "last_hit_tacid": row[8],
        "last_hit_time": row[9],
        "status": row[10],
        "updated_at": row[11],
        "created_at": row[12],
        "gate": GATE_POLICY,
    }
    idx = 13
    if has_loc:
        out["file_path"] = row[idx]
        out["line_start"] = row[idx + 1]
        out["line_end"] = row[idx + 2]
        out["code_span"] = row[idx + 3]
        out["register_id"] = row[idx + 4]
    elif loc_sql:
        out["register_id"] = row[idx]
        out.setdefault("file_path", None)
        out.setdefault("line_start", None)
        out.setdefault("line_end", None)
        out.setdefault("code_span", None)
    else:
        out.setdefault("file_path", None)
        out.setdefault("line_start", None)
        out.setdefault("line_end", None)
        out.setdefault("code_span", None)
        out.setdefault("register_id", None)
    return out


def _update_function_scoring(
    conn: sqlite3.Connection,
    *,
    module_name: str,
    function_name: str,
    ok: bool,
    tacid: str,
    hit_time: str,
) -> dict[str, Any]:
    """W3/W10 — upsert counters; never erase prior hit history from trace."""
    existing = conn.execute(
        f"""
        SELECT id, total_invocations, success_count, fail_count,
               ref_static_tacids, ref_hit_tacids
        FROM {SCORING_TABLE}
        WHERE module_name = ? AND function_name = ?
        """,
        (module_name, function_name),
    ).fetchone()

    if existing is None:
        total = 1
        success = 1 if ok else 0
        fail = 0 if ok else 1
        static_refs: list[Any] = []
        hit_refs = _append_unique_str([], tacid)
        score = compute_functional_score(success, total)
        status = derive_status(
            total_invocations=total,
            has_static_refs=False,
            is_recent=True,
        )
        conn.execute(
            f"""
            INSERT INTO {SCORING_TABLE} (
                module_name, function_name,
                total_invocations, success_count, fail_count, functional_score,
                ref_static_tacids, ref_hit_tacids,
                last_hit_tacid, last_hit_time, status,
                updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                module_name,
                function_name,
                total,
                success,
                fail,
                score,
                json.dumps(static_refs, ensure_ascii=False),
                json.dumps(hit_refs, ensure_ascii=False),
                tacid,
                hit_time,
                status,
            ),
        )
    else:
        _id, total0, success0, fail0, static_raw, hit_raw = existing
        total = int(total0 or 0) + 1
        success = int(success0 or 0) + (1 if ok else 0)
        fail = int(fail0 or 0) + (0 if ok else 1)
        static_refs = _json_list(static_raw)
        hit_refs = _append_unique_str(_json_list(hit_raw), tacid)
        score = compute_functional_score(success, total)
        status = derive_status(
            total_invocations=total,
            has_static_refs=bool(static_refs),
            is_recent=True,
        )
        conn.execute(
            f"""
            UPDATE {SCORING_TABLE}
            SET total_invocations = ?,
                success_count = ?,
                fail_count = ?,
                functional_score = ?,
                ref_hit_tacids = ?,
                last_hit_tacid = ?,
                last_hit_time = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                total,
                success,
                fail,
                score,
                json.dumps(hit_refs, ensure_ascii=False),
                tacid,
                hit_time,
                status,
                int(_id),
            ),
        )

    row = get_function_score(conn, module_name, function_name)
    assert row is not None
    return row


def record_invoke_result(
    conn: sqlite3.Connection,
    *,
    tacid: str,
    module_name: str,
    function_name: str,
    ok: bool,
    error_text: str | None = None,
    duration_ms: int | None = None,
    task_id: int | None = None,
    parent_trace_id: int | None = None,
    sub_steps: list[dict[str, Any]] | None = None,
    source: str = DEFAULT_SOURCE,
    ts: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """W2/W9/W10 — append trace + update rollup. Never deletes; never gates."""
    hit_time = ts or _utc_now_iso()
    tacid_s = (tacid or "").strip() or "unknown"
    mod = (module_name or "").strip() or "unknown"
    fn = (function_name or "").strip() or "unknown"
    steps = sub_steps if isinstance(sub_steps, list) else []
    ok_i = 1 if ok else 0
    err = (error_text or None) if not ok else (error_text or None)

    cur = conn.execute(
        f"""
        INSERT INTO {TRACE_TABLE} (
            ts, tacid, task_id, module_name, function_name,
            ok, error_text, duration_ms, parent_trace_id, sub_steps_json, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            hit_time,
            tacid_s,
            task_id,
            mod,
            fn,
            ok_i,
            err,
            duration_ms,
            parent_trace_id,
            json.dumps(steps, ensure_ascii=False),
            (source or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE,
        ),
    )
    trace_id = int(cur.lastrowid)
    scoring = _update_function_scoring(
        conn,
        module_name=mod,
        function_name=fn,
        ok=bool(ok),
        tacid=tacid_s,
        hit_time=hit_time,
    )
    if commit:
        conn.commit()
    return {
        "gate": GATE_POLICY,
        "why": ["W2", "W3", "W8", "W9", "W10"],
        "trace_id": trace_id,
        "ts": hit_time,
        "tacid": tacid_s,
        "task_id": task_id,
        "module_name": mod,
        "function_name": fn,
        "ok": bool(ok),
        "error_text": err,
        "duration_ms": duration_ms,
        "parent_trace_id": parent_trace_id,
        "sub_steps": steps,
        "source": (source or DEFAULT_SOURCE).strip() or DEFAULT_SOURCE,
        "scoring": scoring,
    }


def function_invoker(
    fn: Callable[..., Any],
    *,
    tacid: str,
    module_name: str | None = None,
    function_name: str | None = None,
    task_id: int | None = None,
    parent_trace_id: int | None = None,
    sub_steps: list[dict[str, Any]] | None = None,
    source: str = DEFAULT_SOURCE,
    db_path: str | None = None,
    conn: sqlite3.Connection | None = None,
    args: tuple[Any, ...] | None = None,
    kwargs: dict[str, Any] | None = None,
    reraise: bool = True,
    commit: bool | None = None,
) -> dict[str, Any]:
    """W8 choke point: run callable, append-only trace, update rollup.

    Never a gate. Never deletes source. Retries belong in sub_steps on parent.
    On failure, records first then re-raises original exception if reraise=True.

    commit: default True when opening own connection; False when caller shares
    conn (e.g. supervisor) so outer transaction can commit once.
    """
    call_args = args if args is not None else ()
    call_kwargs = kwargs if kwargs is not None else {}
    mod = (module_name or getattr(fn, "__module__", None) or "unknown").strip()
    name = (function_name or getattr(fn, "__name__", None) or "unknown").strip()

    own_conn = conn is None
    do_commit = bool(own_conn) if commit is None else bool(commit)
    if own_conn:
        from db_schema import get_db_path

        path = get_db_path(db_path)
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON;")

    assert conn is not None
    started = time.perf_counter()
    result: Any = None
    ok = False
    err_text: str | None = None
    caught: BaseException | None = None
    tb_text: str | None = None
    try:
        result = fn(*call_args, **call_kwargs)
        ok = True
    except BaseException as exc:
        # Record KeyboardInterrupt/SystemExit too would be surprising; only Exception.
        if not isinstance(exc, Exception):
            if own_conn:
                conn.close()
            raise
        caught = exc
        ok = False
        err_text = f"{type(exc).__name__}: {exc}"
        tb_text = traceback.format_exc(limit=8)
    duration_ms = int(round((time.perf_counter() - started) * 1000.0))

    try:
        meta = record_invoke_result(
            conn,
            tacid=tacid,
            module_name=mod,
            function_name=name,
            ok=ok,
            error_text=err_text,
            duration_ms=duration_ms,
            task_id=task_id,
            parent_trace_id=parent_trace_id,
            sub_steps=sub_steps,
            source=source,
            commit=do_commit,
        )
    finally:
        if own_conn:
            conn.close()

    if caught is not None and reraise:
        raise caught

    if ok:
        return {
            "ok": True,
            "result": result,
            "gate": GATE_POLICY,
            "why": ["W8", "W2", "W3", "W9", "W10"],
            "invoke": meta,
        }
    return {
        "ok": False,
        "result": None,
        "error": err_text,
        "error_type": type(caught).__name__ if caught else None,
        "traceback": tb_text,
        "gate": GATE_POLICY,
        "why": ["W8", "W2", "W3", "W9", "W10"],
        "invoke": meta,
    }


def _demo_ok(x: int = 1) -> int:
    return int(x) + 1


def _demo_fail() -> None:
    raise ValueError("demo intentional fail")


# ---------------------------------------------------------------------------
# CH2/CH3 — DB-driven unique register (W1) + harvest (W1,W4,W5)
# ---------------------------------------------------------------------------

def function_name_taken(conn: sqlite3.Connection, module_name: str, function_name: str) -> bool:
    """True if module.function already reserved in function_scoring (DB uniqueness)."""
    row = conn.execute(
        f"""
        SELECT 1 FROM {SCORING_TABLE}
        WHERE module_name = ? AND function_name = ?
        LIMIT 1
        """,
        (module_name, function_name),
    ).fetchone()
    return row is not None


def ensure_scoring_placeholder(
    conn: sqlite3.Connection,
    *,
    module_name: str,
    function_name: str,
    static_tacids: list[str] | None = None,
    status: str | None = None,
    commit: bool = False,
) -> dict[str, Any]:
    """Insert zero-hit rollup row if missing (reserves UNIQUE module+function)."""
    mod = (module_name or "").strip() or "unknown"
    fn = (function_name or "").strip() or "unknown"
    existing = get_function_score(conn, mod, fn)
    if existing is not None:
        return existing

    static_refs = [str(x).strip() for x in (static_tacids or []) if str(x).strip()]
    # de-dupe preserve order
    seen: set[str] = set()
    static_clean: list[str] = []
    for s in static_refs:
        if s not in seen:
            seen.add(s)
            static_clean.append(s)
    st = status or derive_status(
        total_invocations=0,
        has_static_refs=bool(static_clean),
        is_recent=None,
    )
    if st not in FUNCTION_STATUSES:
        st = "dead_candidate"
    conn.execute(
        f"""
        INSERT INTO {SCORING_TABLE} (
            module_name, function_name,
            total_invocations, success_count, fail_count, functional_score,
            ref_static_tacids, ref_hit_tacids,
            last_hit_tacid, last_hit_time, status,
            updated_at, created_at
        ) VALUES (?, ?, 0, 0, 0, NULL, ?, '[]', NULL, NULL, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            mod,
            fn,
            json.dumps(static_clean, ensure_ascii=False),
            st,
        ),
    )
    if commit:
        conn.commit()
    row = get_function_score(conn, mod, fn)
    assert row is not None
    return row


def allocate_unique_function_name(
    conn: sqlite3.Connection,
    *,
    module_name: str,
    base_name: str,
    max_tries: int = 10_000,
) -> str:
    """DB-driven unique name: base, base_2, base_3… against function_scoring UNIQUE.

    WHY W1/W8: uniqueness comes from register/rollup table, not wall-clock tags.
    """
    mod = (module_name or "").strip() or "unknown"
    base = _sanitize_fn_token(base_name)
    if not function_name_taken(conn, mod, base):
        return base
    for i in range(2, max_tries + 2):
        candidate = f"{base}_{i}"
        if not function_name_taken(conn, mod, candidate):
            return candidate
    raise RuntimeError(f"cannot allocate unique function_name under {mod}.{base}")


def ensure_demo_register_task(
    conn: sqlite3.Connection,
    *,
    tacid: str = DEMO_TACID,
    commit: bool = False,
) -> dict[str, Any]:
    """Ensure dev_task row for demo TACID (needed for task_ssot FK)."""
    label = (tacid or DEMO_TACID).strip() or DEMO_TACID
    row = conn.execute(
        "SELECT id, task_label, title, status FROM dev_task WHERE task_label = ? ORDER BY id LIMIT 1",
        (label,),
    ).fetchone()
    if row:
        return {
            "task_id": int(row[0]),
            "task_label": str(row[1]),
            "title": row[2],
            "status": row[3],
            "created": False,
        }

    channel_id = conn.execute(
        "SELECT id FROM channel WHERE code = ? LIMIT 1", ("local_pc",)
    ).fetchone()
    if not channel_id:
        cur = conn.execute(
            "INSERT INTO channel (code, name) VALUES ('local_pc', 'Local PC')"
        )
        ch_id = int(cur.lastrowid)
    else:
        ch_id = int(channel_id[0])

    mod = conn.execute(
        "SELECT id FROM module WHERE code = ? LIMIT 1", ("code_health",)
    ).fetchone()
    if not mod:
        cur = conn.execute(
            "INSERT INTO module (code, name) VALUES ('code_health', 'Code Health / Function Trace')"
        )
        mod_id = int(cur.lastrowid)
    else:
        mod_id = int(mod[0])

    act = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ? LIMIT 1", ("capability.ssot",)
    ).fetchone()
    act_id = int(act[0]) if act else None

    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ? AND version_label = ?
        LIMIT 1
        """,
        (ch_id, mod_id, "ch-1.0"),
    ).fetchone()
    if ver:
        version_id = int(ver[0])
    else:
        cur = conn.execute(
            """
            INSERT INTO version_center
                (channel_id, module_id, version_label, title, notes, status)
            VALUES (?, ?, 'ch-1.0', ?, ?, 'active')
            """,
            (
                ch_id,
                mod_id,
                "Code Health pipeline C",
                "Demo/register task for function_invoker + task_ssot impl.*",
            ),
        )
        version_id = int(cur.lastrowid)

    payload = {
        "pipeline": PIPELINE_ID,
        "gate": GATE_POLICY,
        "why": ["W1", "W2", "W8"],
        "goal_type": "code_health_demo_register",
        "goal_text": "DB-driven unique function register + invoker demo",
    }
    cur = conn.execute(
        """
        INSERT INTO dev_task
            (parent_task_id, channel_id, module_id, action_name_id, version_id,
             task_label, title, payload_json, status)
        VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            ch_id,
            mod_id,
            act_id,
            version_id,
            label,
            DEMO_TASK_TITLE,
            json.dumps(payload, ensure_ascii=False),
        ),
    )
    task_id = int(cur.lastrowid)
    if commit:
        conn.commit()
    return {
        "task_id": task_id,
        "task_label": label,
        "title": DEMO_TASK_TITLE,
        "status": "pending",
        "created": True,
        "channel_id": ch_id,
        "module_id": mod_id,
        "version_id": version_id,
    }


def register_impl_function(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    tacid: str,
    module_name: str,
    function_name: str,
    required: bool = True,
    source: str = "code_health.register",
    notes: str | None = None,
    commit: bool = True,
    system_key: str | None = None,
    slice_key: str | None = None,
    ensure_register_id: bool = True,
) -> dict[str, Any]:
    """W1 — write task_ssot impl.* and reserve UNIQUE row in function_scoring.

    Uniqueness is enforced by function_scoring UNIQUE(module_name, function_name)
    via ensure_scoring_placeholder / allocate_unique_function_name — not timestamps.

    When ensure_register_id=True (default), also enroll Pipeline D code_register so
    all coding stays tracable (register_id law).
    """
    from db_schema import upsert_task_ssot

    mod = (module_name or "").strip() or "unknown"
    fn = (function_name or "").strip() or "unknown"
    label = (tacid or "").strip() or "unknown"
    note = notes or f"impl register {mod}.{fn} under {label}"

    register_meta: dict[str, Any] | None = None
    register_id: str | None = None
    if ensure_register_id:
        try:
            from managed_coding import register_managed_function

            register_meta = register_managed_function(
                conn,
                task_id=int(task_id),
                tacid=label,
                module_name=mod,
                function_name=fn,
                system_key=system_key,
                slice_key=slice_key,
                required=bool(required),
                status="active",
                source=source,
                notes=note,
                commit=False,
            )
            register_id = str(register_meta.get("register_id") or "") or None
        except Exception as exc:
            register_meta = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "note": "code_register optional if MCS not migrated yet",
            }

    dims = [
        (IMPL_DIM_MODULE, mod, "string", 10),
        (IMPL_DIM_FUNCTION, fn, "string", 20),
        (IMPL_DIM_REQUIRED, "true" if required else "false", "bool", 30),
    ]
    if register_id:
        dims.append(("impl.register_id", register_id, "string", 40))
        dims.append(("profile.H.register_id", register_id, "string", 41))
    written: list[dict[str, Any]] = []
    for dim_key, value_text, value_type, sort_order in dims:
        written.append(
            upsert_task_ssot(
                conn,
                task_id=int(task_id),
                dim_key=dim_key,
                value_text=value_text,
                value_type=value_type,
                source=source,
                sort_order=sort_order,
                notes=note,
                commit=False,
            )
        )

    scoring = ensure_scoring_placeholder(
        conn,
        module_name=mod,
        function_name=fn,
        static_tacids=[label],
        commit=False,
    )
    # merge static if row already existed without this tacid
    static_refs = _append_unique_str(_json_list(scoring.get("ref_static_tacids")), label)
    total = int(scoring.get("total_invocations") or 0)
    status = derive_status(
        total_invocations=total,
        has_static_refs=bool(static_refs),
        is_recent=True if total > 0 else None,
    )
    if register_id:
        try:
            conn.execute(
                f"""
                UPDATE {SCORING_TABLE}
                SET ref_static_tacids = ?,
                    status = ?,
                    register_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE module_name = ? AND function_name = ?
                """,
                (json.dumps(static_refs, ensure_ascii=False), status, register_id, mod, fn),
            )
        except sqlite3.Error:
            conn.execute(
                f"""
                UPDATE {SCORING_TABLE}
                SET ref_static_tacids = ?,
                    status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE module_name = ? AND function_name = ?
                """,
                (json.dumps(static_refs, ensure_ascii=False), status, mod, fn),
            )
    else:
        conn.execute(
            f"""
            UPDATE {SCORING_TABLE}
            SET ref_static_tacids = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE module_name = ? AND function_name = ?
            """,
            (json.dumps(static_refs, ensure_ascii=False), status, mod, fn),
        )
    if commit:
        conn.commit()
    scoring2 = get_function_score(conn, mod, fn)
    return {
        "gate": GATE_POLICY,
        "why": ["W1", "W4"],
        "task_id": int(task_id),
        "tacid": label,
        "module_name": mod,
        "function_name": fn,
        "required": bool(required),
        "register_id": register_id,
        "code_register": register_meta,
        "dims": written,
        "scoring": scoring2,
    }


def allocate_and_register_function(
    conn: sqlite3.Connection,
    *,
    task_id: int,
    tacid: str,
    module_name: str,
    base_name: str,
    required: bool = True,
    source: str = "code_health.register",
    commit: bool = True,
) -> dict[str, Any]:
    """Allocate DB-unique function_name then register impl.* + scoring placeholder."""
    fn = allocate_unique_function_name(
        conn, module_name=module_name, base_name=base_name
    )
    reg = register_impl_function(
        conn,
        task_id=task_id,
        tacid=tacid,
        module_name=module_name,
        function_name=fn,
        required=required,
        source=source,
        commit=commit,
    )
    reg["allocated_from_base"] = _sanitize_fn_token(base_name)
    reg["unique_via"] = "function_scoring.UNIQUE(module_name,function_name)"
    return reg


def collect_static_impl_refs(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    """W1 — scan task_ssot for tasks that declare both impl.module and impl.function."""
    rows = conn.execute(
        """
        SELECT s.task_id, t.task_label, s.dim_key, s.value_text
        FROM task_ssot s
        JOIN dev_task t ON t.id = s.task_id
        WHERE s.dim_key IN (?, ?, ?)
        ORDER BY s.task_id, s.dim_key
        """,
        (IMPL_DIM_MODULE, IMPL_DIM_FUNCTION, IMPL_DIM_REQUIRED),
    ).fetchall()

    by_task: dict[int, dict[str, Any]] = {}
    for task_id, task_label, dim_key, value_text in rows:
        tid = int(task_id)
        slot = by_task.setdefault(
            tid,
            {"task_id": tid, "tacid": str(task_label or ""), "dims": {}},
        )
        slot["dims"][str(dim_key)] = None if value_text is None else str(value_text)

    out: dict[tuple[str, str], dict[str, Any]] = {}
    for slot in by_task.values():
        dims = slot["dims"]
        mod = (dims.get(IMPL_DIM_MODULE) or "").strip()
        fn = (dims.get(IMPL_DIM_FUNCTION) or "").strip()
        if not mod or not fn:
            continue
        req_raw = (dims.get(IMPL_DIM_REQUIRED) or "true").strip().lower()
        required = req_raw not in ("0", "false", "no", "off")
        key = (mod, fn)
        entry = out.setdefault(
            key,
            {
                "module_name": mod,
                "function_name": fn,
                "tacids": [],
                "task_ids": [],
                "required": False,
            },
        )
        tacid = (slot["tacid"] or "").strip()
        if tacid and tacid not in entry["tacids"]:
            entry["tacids"].append(tacid)
        if slot["task_id"] not in entry["task_ids"]:
            entry["task_ids"].append(slot["task_id"])
        entry["required"] = bool(entry["required"] or required)
    return out


def harvest_static_impl_refs(
    conn: sqlite3.Connection,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    """CH3 W1/W4/W5 — refresh function_scoring.ref_static_tacids + status from task_ssot.

    - Declared pairs → ensure row, set static refs, zombie if total==0
    - Existing rows with empty static + total==0 stay dead_candidate
    - Does not delete rows or source files
    """
    static_map = collect_static_impl_refs(conn)
    touched: list[dict[str, Any]] = []
    zombies: list[dict[str, Any]] = []

    for (mod, fn), meta in sorted(static_map.items()):
        tacids = list(meta.get("tacids") or [])
        ensure_scoring_placeholder(
            conn,
            module_name=mod,
            function_name=fn,
            static_tacids=tacids,
            commit=False,
        )
        row = get_function_score(conn, mod, fn)
        assert row is not None
        # merge any prior static with harvested (harvest is authority for declared set)
        # Prefer harvested list as full replace for this fn's static claim set.
        static_refs = []
        seen: set[str] = set()
        for t in tacids:
            s = str(t).strip()
            if s and s not in seen:
                seen.add(s)
                static_refs.append(s)
        total = int(row["total_invocations"] or 0)
        # recent: any hit counts as recent in v1
        is_recent = True if total > 0 else None
        status = derive_status(
            total_invocations=total,
            has_static_refs=bool(static_refs),
            is_recent=is_recent,
        )
        conn.execute(
            f"""
            UPDATE {SCORING_TABLE}
            SET ref_static_tacids = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE module_name = ? AND function_name = ?
            """,
            (json.dumps(static_refs, ensure_ascii=False), status, mod, fn),
        )
        updated = get_function_score(conn, mod, fn)
        assert updated is not None
        item = {
            "module_name": mod,
            "function_name": fn,
            "ref_static_tacids": static_refs,
            "total_invocations": total,
            "status": updated["status"],
            "task_ids": meta.get("task_ids") or [],
        }
        touched.append(item)
        if updated["status"] == "zombie":
            zombies.append(item)

    # Recompute status for rows not in static_map (preserve hit counters; refresh dead/active)
    all_rows = conn.execute(
        f"""
        SELECT module_name, function_name, total_invocations, ref_static_tacids, status
        FROM {SCORING_TABLE}
        """
    ).fetchall()
    refreshed_other = 0
    for mod, fn, total, static_raw, old_status in all_rows:
        key = (str(mod), str(fn))
        if key in static_map:
            continue
        static_refs = _json_list(static_raw)
        total_i = int(total or 0)
        new_status = derive_status(
            total_invocations=total_i,
            has_static_refs=bool(static_refs),
            is_recent=True if total_i > 0 else None,
        )
        if new_status != str(old_status or ""):
            conn.execute(
                f"""
                UPDATE {SCORING_TABLE}
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE module_name = ? AND function_name = ?
                """,
                (new_status, mod, fn),
            )
            refreshed_other += 1

    if commit:
        conn.commit()

    return {
        "ok": True,
        "gate": GATE_POLICY,
        "why": ["W1", "W4", "W5"],
        "phase": "CH3",
        "static_pairs": len(static_map),
        "touched": len(touched),
        "zombie_count": len(zombies),
        "zombies": zombies,
        "refreshed_other": refreshed_other,
        "items": touched,
        "notes": "static from task_ssot impl.*; no source delete; never gate",
    }


# ---------------------------------------------------------------------------
# CH4 — reports (W6, W7)
# ---------------------------------------------------------------------------

def _tacid_branch_match(tacid: str, prefix: str) -> bool:
    """W6 branch match with boundary note for dotted labels.

    Matches: exact, or next char in .-_ , or startswith(prefix) when prefix
    already encodes branch (e.g. '1.1' → '1.1b', '1.1b-fix-3').
    Documented tradeoff: '1.1' also matches '1.10*' if such labels exist.
    """
    t = (tacid or "").strip()
    p = (prefix or "").strip()
    if not p:
        return True
    if not t:
        return False
    if t == p:
        return True
    if t.startswith(p + ".") or t.startswith(p + "-") or t.startswith(p + "_"):
        return True
    # intentional branch glob for current label style (1.1b, ch.demo.run3)
    if t.startswith(p):
        return True
    return False


def get_tacid_branch_function_report(
    conn: sqlite3.Connection,
    prefix: str,
) -> dict[str, Any]:
    """W6 — functions hit under task_label/tacid branch prefix."""
    p = (prefix or "").strip()
    rows = conn.execute(
        f"""
        SELECT module_name, function_name, tacid, ok, ts, id
        FROM {TRACE_TABLE}
        ORDER BY id
        """
    ).fetchall()
    by_fn: dict[tuple[str, str], dict[str, Any]] = {}
    for mod, fn, tacid, ok, ts, tid in rows:
        tacid_s = str(tacid or "")
        if not _tacid_branch_match(tacid_s, p):
            continue
        key = (str(mod), str(fn))
        slot = by_fn.setdefault(
            key,
            {
                "module_name": str(mod),
                "function_name": str(fn),
                "hits": 0,
                "success": 0,
                "fail": 0,
                "tacids": [],
                "last_ts": None,
                "last_trace_id": None,
            },
        )
        slot["hits"] += 1
        if int(ok or 0) == 1:
            slot["success"] += 1
        else:
            slot["fail"] += 1
        if tacid_s and tacid_s not in slot["tacids"]:
            slot["tacids"].append(tacid_s)
        slot["last_ts"] = ts
        slot["last_trace_id"] = int(tid)

    functions = sorted(by_fn.values(), key=lambda x: (x["module_name"], x["function_name"]))
    return {
        "gate": GATE_POLICY,
        "why": ["W6", "W2"],
        "prefix": p,
        "function_count": len(functions),
        "functions": functions,
        "boundary_note": (
            "match: exact | prefix+./-/_ | startswith(prefix); "
            "1.1 intentionally matches 1.1b; may also match 1.10 if used"
        ),
    }


def _location_lookup(conn: sqlite3.Connection) -> dict[tuple[str, str], dict[str, Any]]:
    """Map (module, function) → file/line/register from code_register + scoring."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='code_register'"
        ).fetchone():
            has_loc = "file_path" in {
                str(r[1])
                for r in conn.execute("PRAGMA table_info(code_register)").fetchall()
            }
            if has_loc:
                for r in conn.execute(
                    """
                    SELECT module_name, function_name, register_id,
                           file_path, line_start, line_end, code_span, status
                    FROM code_register
                    """
                ).fetchall():
                    key = (str(r[0]), str(r[1]))
                    out[key] = {
                        "register_id": r[2],
                        "file_path": r[3],
                        "line_start": r[4],
                        "line_end": r[5],
                        "code_span": r[6],
                        "register_status": r[7],
                    }
    except sqlite3.Error:
        pass
    try:
        if _scoring_location_cols(conn):
            for r in conn.execute(
                f"""
                SELECT module_name, function_name, register_id,
                       file_path, line_start, line_end, code_span
                FROM {SCORING_TABLE}
                WHERE file_path IS NOT NULL OR register_id IS NOT NULL
                """
            ).fetchall():
                key = (str(r[0]), str(r[1]))
                cur = out.get(key) or {}
                if r[2] and not cur.get("register_id"):
                    cur["register_id"] = r[2]
                if r[3] and not cur.get("file_path"):
                    cur["file_path"] = r[3]
                    cur["line_start"] = r[4]
                    cur["line_end"] = r[5]
                    cur["code_span"] = r[6]
                out[key] = cur
    except sqlite3.Error:
        pass
    return out


def generate_code_health_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """CH4/CH7 W7 — human rot dashboard JSON (never a gate)."""
    rows = conn.execute(
        f"""
        SELECT module_name, function_name, total_invocations, success_count, fail_count,
               functional_score, ref_static_tacids, ref_hit_tacids,
               last_hit_tacid, last_hit_time, status
        FROM {SCORING_TABLE}
        ORDER BY module_name, function_name
        """
    ).fetchall()
    loc_map = _location_lookup(conn)

    zombie_functions: list[dict[str, Any]] = []
    dead_candidate_functions: list[dict[str, Any]] = []
    low_score_functions: list[dict[str, Any]] = []
    active_count = 0
    inactive_count = 0

    for r in rows:
        mod, fn, total, success, fail, score, static_raw, hit_raw, last_tacid, last_time, status = r
        loc = loc_map.get((str(mod), str(fn))) or {}
        usage = int(total or 0)
        pass_n = int(success or 0)
        fail_n = int(fail or 0)
        item = {
            "module": str(mod),
            "function": str(fn),
            "module_name": str(mod),
            "function_name": str(fn),
            "total_invocations": usage,
            "usage": usage,
            "success_count": pass_n,
            "pass_count": pass_n,
            "fail_count": fail_n,
            "functional_score": score,
            "score": score,
            "ref_static_tacids": _json_list(static_raw),
            "ref_hit_tacids": _json_list(hit_raw),
            "last_hit_tacid": last_tacid,
            "last_hit_time": last_time,
            "status": str(status or ""),
            "register_id": loc.get("register_id"),
            "file_path": loc.get("file_path"),
            "line_start": loc.get("line_start"),
            "line_end": loc.get("line_end"),
            "code_span": loc.get("code_span"),
            "file_line": (
                f"{loc.get('file_path')}:{loc.get('line_start')}-{loc.get('line_end')}"
                if loc.get("file_path") and loc.get("line_start") is not None
                else None
            ),
        }
        st = item["status"]
        if st == "zombie":
            zombie_functions.append(item)
        elif st == "dead_candidate":
            dead_candidate_functions.append(item)
        elif st == "active":
            active_count += 1
        elif st == "inactive":
            inactive_count += 1

        if (
            item["total_invocations"] > 0
            and item["functional_score"] is not None
            and float(item["functional_score"]) < SCORE_LOW_THRESHOLD
        ):
            low_score_functions.append(item)

    return {
        "report_time": _utc_now_iso(),
        "gate": GATE_POLICY,
        "why": ["W4", "W5", "W7", "W11"],
        "zombie_functions": zombie_functions,
        "dead_candidate_functions": dead_candidate_functions,
        "low_score_functions": low_score_functions,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "scoring_row_count": len(rows),
        "score_low_threshold": SCORE_LOW_THRESHOLD,
        "score_formula": "usage>0 ? pass/usage*100 : null",
        "notes": "objective score; file+line when bound; no source deleted; never a gate",
    }


def list_dead_dirty_functions(
    conn: sqlite3.Connection,
    *,
    statuses: tuple[str, ...] = ("zombie", "dead_candidate"),
    require_zero_usage: bool = True,
) -> list[dict[str, Any]]:
    """W4/W5/W12 — easy dead/dirty list: status + usage for cleanup."""
    report = generate_code_health_report(conn)
    out: list[dict[str, Any]] = []
    for bucket in (
        report.get("zombie_functions") or [],
        report.get("dead_candidate_functions") or [],
    ):
        for it in bucket:
            st = str(it.get("status") or "")
            if st not in statuses:
                continue
            usage = int(it.get("usage") or it.get("total_invocations") or 0)
            if require_zero_usage and usage > 0:
                continue
            out.append(it)
    return out


def spawn_dead_function_cleanup_tasks(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    commit: bool = True,
    source: str = "code_health.cleanup",
) -> dict[str, Any]:
    """W12 — open pending dev_task(code.cleanup) for dead/zombie (mark-only work).

    Watchdog / health can call this each scan. Never deletes source files.
    Dedupes by open task with same module.function in payload or label.
    """
    from db_schema import create_dev_task

    dead = list_dead_dirty_functions(conn)[: max(1, int(limit))]
    action = conn.execute(
        "SELECT id FROM task_action_name WHERE code = ?",
        (CLEANUP_ACTION_CODE,),
    ).fetchone()
    if not action:
        # seed path may not have run yet
        try:
            from db_schema import seed_task_center_defaults

            seed_task_center_defaults(conn)
            action = conn.execute(
                "SELECT id FROM task_action_name WHERE code = ?",
                (CLEANUP_ACTION_CODE,),
            ).fetchone()
        except Exception:
            action = None
    if not action:
        return {
            "ok": False,
            "gate": GATE_POLICY,
            "error": f"missing action {CLEANUP_ACTION_CODE}",
            "created": [],
            "skipped": [],
        }
    action_id = int(action[0])

    # default channel/module/version for cleanup tasks
    ch = conn.execute(
        "SELECT id FROM channel WHERE code = ? ORDER BY id LIMIT 1", ("local_pc",)
    ).fetchone()
    mod = conn.execute(
        "SELECT id FROM module WHERE code = ? ORDER BY id LIMIT 1", ("agent_db",)
    ).fetchone()
    if not ch or not mod:
        return {
            "ok": False,
            "gate": GATE_POLICY,
            "error": "channel/module seed missing",
            "created": [],
            "skipped": [],
        }
    channel_id = int(ch[0])
    module_id = int(mod[0])
    ver = conn.execute(
        """
        SELECT id FROM version_center
        WHERE channel_id = ? AND module_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (channel_id, module_id),
    ).fetchone()
    if not ver:
        return {
            "ok": False,
            "gate": GATE_POLICY,
            "error": "version_center missing for local_pc/agent_db",
            "created": [],
            "skipped": [],
        }
    version_id = int(ver[0])

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for it in dead:
        mod_name = str(it.get("module_name") or it.get("module") or "unknown")
        fn_name = str(it.get("function_name") or it.get("function") or "unknown")
        token = f"{_sanitize_fn_token(mod_name)}.{_sanitize_fn_token(fn_name)}"
        label = f"{CLEANUP_TASK_PREFIX}.{token}"[:180]
        # open task already?
        open_row = conn.execute(
            """
            SELECT id, task_label, status FROM dev_task
            WHERE action_name_id = ?
              AND status IN ('pending', 'running')
              AND (
                    task_label = ?
                 OR payload_json LIKE ?
              )
            ORDER BY id DESC LIMIT 1
            """,
            (
                action_id,
                label,
                f'%"module_name": "{mod_name}", "function_name": "{fn_name}"%',
            ),
        ).fetchone()
        if open_row:
            skipped.append(
                {
                    "module_name": mod_name,
                    "function_name": fn_name,
                    "reason": "open_task_exists",
                    "task_id": int(open_row[0]),
                    "task_label": open_row[1],
                }
            )
            continue

        # unique label if collision with closed task
        final_label = label
        n = 1
        while conn.execute(
            "SELECT 1 FROM dev_task WHERE version_id = ? AND task_label = ?",
            (version_id, final_label),
        ).fetchone():
            n += 1
            final_label = f"{label}.{n}"[:180]

        payload = {
            "pipeline": PIPELINE_ID,
            "gate": GATE_POLICY,
            "why": ["W4", "W5", "W11", "W12"],
            "action": CLEANUP_ACTION_CODE,
            "module_name": mod_name,
            "function_name": fn_name,
            "register_id": it.get("register_id"),
            "status": it.get("status"),
            "usage": it.get("usage") or it.get("total_invocations") or 0,
            "pass_count": it.get("pass_count") or it.get("success_count") or 0,
            "fail_count": it.get("fail_count") or 0,
            "score": it.get("score") if it.get("score") is not None else it.get("functional_score"),
            "file_path": it.get("file_path"),
            "line_start": it.get("line_start"),
            "line_end": it.get("line_end"),
            "file_line": it.get("file_line"),
            "work": "review dead/zombie; mark rubbish or restore; never auto-delete source",
            "source": source,
        }
        title = f"Cleanup {it.get('status')}: {mod_name}.{fn_name}"
        if it.get("file_line"):
            title = f"{title} @ {it.get('file_line')}"
        try:
            # create_dev_task commits internally — keep batch best-effort
            row = create_dev_task(
                conn,
                channel_id=channel_id,
                module_id=module_id,
                version_id=version_id,
                action_name_id=action_id,
                task_label=final_label,
                title=title[:240],
                payload_json=payload,
                status="pending",
            )
            created.append(
                {
                    "task_id": row["id"],
                    "task_label": row["task_label"],
                    "module_name": mod_name,
                    "function_name": fn_name,
                    "status": it.get("status"),
                    "file_line": it.get("file_line"),
                }
            )
        except Exception as e:
            skipped.append(
                {
                    "module_name": mod_name,
                    "function_name": fn_name,
                    "reason": f"{type(e).__name__}: {e}",
                }
            )

    if commit:
        try:
            conn.commit()
        except sqlite3.Error:
            pass

    return {
        "ok": True,
        "gate": GATE_POLICY,
        "why": ["W4", "W5", "W12"],
        "action": CLEANUP_ACTION_CODE,
        "dead_seen": len(dead),
        "created_n": len(created),
        "skipped_n": len(skipped),
        "created": created,
        "skipped": skipped,
        "notes": "mark-only cleanup tasks; no source delete",
    }


def run_demo(
    *,
    db_path: str | None = None,
    tacid: str = DEMO_TACID,
    migrate: bool = True,
) -> dict[str, Any]:
    """CH2–CH4 demo: DB-unique register → invoker → harvest zombie → report.

    Uniqueness: allocate_unique_function_name against function_scoring UNIQUE,
    then task_ssot impl.* register — not wall-clock tags.
    """
    from db_schema import ensure_schema, get_db_path

    path = get_db_path(db_path)
    if migrate:
        ensure_schema(path)

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON;")
    traces: list[dict[str, Any]] = []
    try:
        schema = verify_code_health_schema(conn)
        if not schema.get("ok"):
            return {"ok": False, "gate": GATE_POLICY, "error": "schema_missing", "schema": schema}

        task_info = ensure_demo_register_task(conn, tacid=tacid, commit=True)
        task_id = int(task_info["task_id"])

        # DB-driven unique names via function_scoring UNIQUE (not wall-clock).
        # v1: one impl.function per task_ssot task → child task per enrolled fn.
        probe_fn = allocate_unique_function_name(
            conn, module_name=DEMO_MODULE, base_name=DEMO_FUNCTION
        )
        ensure_scoring_placeholder(
            conn, module_name=DEMO_MODULE, function_name=probe_fn, commit=True
        )
        parent_fn = allocate_unique_function_name(
            conn, module_name=DEMO_MODULE, base_name=DEMO_PARENT_FUNCTION
        )
        ensure_scoring_placeholder(
            conn, module_name=DEMO_MODULE, function_name=parent_fn, commit=True
        )
        zombie_fn = allocate_unique_function_name(
            conn, module_name=DEMO_MODULE, base_name=DEMO_ZOMBIE_FUNCTION
        )
        ensure_scoring_placeholder(
            conn, module_name=DEMO_MODULE, function_name=zombie_fn, commit=True
        )

        def _child_task(suffix: str, fn_name: str) -> tuple[int, str]:
            """Stable child label uses allocated fn so each demo run gets own task+SSOT."""
            child_label = f"{tacid}.{suffix}.{fn_name}"
            existing = conn.execute(
                "SELECT id FROM dev_task WHERE task_label = ? ORDER BY id LIMIT 1",
                (child_label,),
            ).fetchone()
            if existing:
                return int(existing[0]), child_label
            parent_row = conn.execute(
                "SELECT channel_id, module_id, action_name_id, version_id FROM dev_task WHERE id = ?",
                (task_id,),
            ).fetchone()
            assert parent_row is not None
            cur = conn.execute(
                """
                INSERT INTO dev_task
                    (parent_task_id, channel_id, module_id, action_name_id, version_id,
                     task_label, title, payload_json, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    task_id,
                    parent_row[0],
                    parent_row[1],
                    parent_row[2],
                    parent_row[3],
                    child_label,
                    f"Code Health register: {fn_name}",
                    json.dumps(
                        {
                            "pipeline": PIPELINE_ID,
                            "parent_tacid": tacid,
                            "function_name": fn_name,
                            "unique_via": "function_scoring.UNIQUE",
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
            return int(cur.lastrowid), child_label

        probe_task_id, probe_label = _child_task("probe", probe_fn)
        parent_task_id, parent_label = _child_task("parent", parent_fn)
        zombie_task_id, zombie_label = _child_task("zombie", zombie_fn)

        probe_reg = register_impl_function(
            conn,
            task_id=probe_task_id,
            tacid=probe_label,
            module_name=DEMO_MODULE,
            function_name=probe_fn,
            required=True,
            source="code_health.demo",
            commit=True,
        )
        parent_reg = register_impl_function(
            conn,
            task_id=parent_task_id,
            tacid=parent_label,
            module_name=DEMO_MODULE,
            function_name=parent_fn,
            required=True,
            source="code_health.demo",
            commit=True,
        )
        zombie_reg = register_impl_function(
            conn,
            task_id=zombie_task_id,
            tacid=zombie_label,
            module_name=DEMO_MODULE,
            function_name=zombie_fn,
            required=True,
            source="code_health.demo",
            commit=True,
        )

        # Call 1 — success
        r1 = function_invoker(
            _demo_ok,
            tacid=tacid,
            module_name=DEMO_MODULE,
            function_name=probe_fn,
            task_id=probe_task_id,
            source="code_health.demo",
            conn=conn,
            args=(1,),
            reraise=True,
        )
        traces.append(r1["invoke"])
        score_after_1 = r1["invoke"]["scoring"]

        # Call 2 — fail (recorded; do not reraise so demo continues)
        r2 = function_invoker(
            _demo_fail,
            tacid=tacid,
            module_name=DEMO_MODULE,
            function_name=probe_fn,
            task_id=probe_task_id,
            source="code_health.demo",
            conn=conn,
            reraise=False,
            sub_steps=[{"step": "retry_note", "ok": 0, "note": "sub_steps on parent only"}],
        )
        traces.append(r2["invoke"])
        score_after_2 = r2["invoke"]["scoring"]

        # Call 3 — success again
        r3 = function_invoker(
            _demo_ok,
            tacid=f"{tacid}.run3",
            module_name=DEMO_MODULE,
            function_name=probe_fn,
            task_id=probe_task_id,
            source="code_health.demo",
            conn=conn,
            kwargs={"x": 2},
            reraise=True,
        )
        traces.append(r3["invoke"])
        final = get_function_score(conn, DEMO_MODULE, probe_fn)
        assert final is not None

        # Parent with nested sub_steps only
        parent = function_invoker(
            _demo_ok,
            tacid=tacid,
            module_name=DEMO_MODULE,
            function_name=parent_fn,
            task_id=parent_task_id,
            source="code_health.demo",
            conn=conn,
            args=(0,),
            sub_steps=[
                {"step": "attempt_1", "ok": 0, "error": "transient"},
                {"step": "attempt_2", "ok": 1},
            ],
            reraise=True,
        )
        traces.append(parent["invoke"])
        parent_row = parent["invoke"]
        parent_steps = parent_row.get("sub_steps") or []

        harvest = harvest_static_impl_refs(conn, commit=True)
        zombie_score = get_function_score(conn, DEMO_MODULE, zombie_fn)
        report = generate_code_health_report(conn)
        branch = get_tacid_branch_function_report(conn, tacid)

        expected_final = compute_functional_score(2, 3)
        ok = (
            final["total_invocations"] == 3
            and final["success_count"] == 2
            and final["fail_count"] == 1
            and final["functional_score"] == expected_final
            and final["status"] == "active"
            and tacid in final["ref_hit_tacids"]
            and f"{tacid}.run3" in final["ref_hit_tacids"]
            and len(parent_steps) == 2
            and zombie_score is not None
            and zombie_score["status"] == "zombie"
            and int(zombie_score["total_invocations"] or 0) == 0
            and any(
                z.get("function_name") == zombie_fn or z.get("function") == zombie_fn
                for z in report.get("zombie_functions") or []
            )
            and int(score_after_1["total_invocations"]) == 1
            and float(score_after_1["functional_score"]) == 100.0
            and int(score_after_2["total_invocations"]) == 2
            and float(score_after_2["functional_score"]) == 50.0
            and probe_fn != parent_fn
            and probe_fn != zombie_fn
        )
        return {
            "ok": bool(ok),
            "gate": GATE_POLICY,
            "why": ["W1", "W2", "W3", "W4", "W8", "W9", "W10", "W7", "W6"],
            "phase": "CH2+CH3+CH4",
            "db": path,
            "tacid": tacid,
            "register_task": task_info,
            "unique_via": "function_scoring.UNIQUE(module_name,function_name)+task_ssot.impl",
            "probe_function": probe_fn,
            "parent_function": parent_fn,
            "zombie_function": zombie_fn,
            "probe_register": {
                "task_id": probe_task_id,
                "function_name": probe_fn,
                "scoring": probe_reg.get("scoring"),
            },
            "score_after_1": score_after_1,
            "score_after_ok_fail": score_after_2,
            "final_scoring": final,
            "parent_invoke": parent_row,
            "harvest": {
                "static_pairs": harvest.get("static_pairs"),
                "zombie_count": harvest.get("zombie_count"),
                "zombies": harvest.get("zombies"),
            },
            "zombie_scoring": zombie_score,
            "report": {
                "zombie_n": len(report.get("zombie_functions") or []),
                "dead_n": len(report.get("dead_candidate_functions") or []),
                "low_n": len(report.get("low_score_functions") or []),
                "active_count": report.get("active_count"),
            },
            "branch_prefix_hits": branch.get("function_count"),
            "trace_ids": [t.get("trace_id") for t in traces],
            "trace_count_probe": conn.execute(
                f"SELECT COUNT(*) FROM {TRACE_TABLE} WHERE module_name=? AND function_name=?",
                (DEMO_MODULE, probe_fn),
            ).fetchone()[0],
            "notes": (
                "DB-unique register; append-only trace; rollup counters only; "
                "never gate; no source delete"
            ),
        }
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    """CLI: contracts | selftest | verify-schema | demo | harvest | report | branch"""
    import argparse
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["contracts"]
    cmd = args[0].strip().lower()

    if cmd in ("contracts", "ch0", "doc"):
        print(json.dumps(contracts_doc(), ensure_ascii=False, indent=2))
        return 0

    if cmd in ("selftest", "test"):
        doc = contracts_doc()
        assert doc["gate"] == "never"
        assert doc["pipeline"] == PIPELINE_ID
        assert set(WHY_CATALOG) == {f"W{i}" for i in range(1, 13)}
        assert compute_functional_score(0, 0) is None
        assert compute_functional_score(1, 2) == 50.0
        assert derive_status(total_invocations=0, has_static_refs=True) == "zombie"
        assert derive_status(total_invocations=0, has_static_refs=False) == "dead_candidate"
        assert derive_status(total_invocations=3, has_static_refs=True, is_recent=True) == "active"
        assert derive_status(total_invocations=3, has_static_refs=False, is_recent=False) == "inactive"
        assert _append_unique_str(["a", "a"], "b") == ["a", "b"]
        assert _json_list('["x"]') == ["x"]
        assert _tacid_branch_match("1.1b-fix-3", "1.1")
        assert _tacid_branch_match("ch.demo.run3", "ch.demo")
        assert _sanitize_fn_token("demo probe!") == "demo_probe"
        assert doc["cleanup_tasks"]["action"] == CLEANUP_ACTION_CODE
        print(
            "selftest OK",
            {
                "why_n": len(WHY_CATALOG),
                "score_50": compute_functional_score(1, 2),
                "statuses": list(FUNCTION_STATUSES),
                "phase": doc["phase"],
                "w11_w12": True,
            },
        )
        return 0

    if cmd in ("verify-schema", "ch1", "verify"):
        ap = argparse.ArgumentParser(prog="code_health.py verify-schema")
        ap.add_argument("--db", default=None)
        ap.add_argument("--migrate", action="store_true", help="ensure_schema before verify")
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        if ns.migrate:
            from db_schema import ensure_schema

            ensure_schema(db)
        conn = sqlite3.connect(db)
        try:
            result = verify_code_health_schema(conn)
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if cmd in ("demo", "ch2", "invoke-demo"):
        ap = argparse.ArgumentParser(prog="code_health.py demo")
        ap.add_argument("--db", default=None)
        ap.add_argument("--tacid", default=DEMO_TACID)
        ap.add_argument("--no-migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        result = run_demo(db_path=ns.db, tacid=ns.tacid, migrate=not ns.no_migrate)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if cmd in ("harvest", "ch3"):
        ap = argparse.ArgumentParser(prog="code_health.py harvest")
        ap.add_argument("--db", default=None)
        ap.add_argument("--migrate", action="store_true")
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        if ns.migrate:
            from db_schema import ensure_schema

            ensure_schema(db)
        conn = sqlite3.connect(db)
        try:
            result = harvest_static_impl_refs(conn, commit=True)
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if cmd in ("report", "ch4", "health"):
        ap = argparse.ArgumentParser(prog="code_health.py report")
        ap.add_argument("--db", default=None)
        ap.add_argument("--migrate", action="store_true")
        ap.add_argument("--harvest", action="store_true", help="harvest static refs before report")
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        if ns.migrate:
            from db_schema import ensure_schema

            ensure_schema(db)
        conn = sqlite3.connect(db)
        try:
            if ns.harvest:
                harvest_static_impl_refs(conn, commit=True)
            result = generate_code_health_report(conn)
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if cmd in ("branch", "tacid-branch"):
        ap = argparse.ArgumentParser(prog="code_health.py branch")
        ap.add_argument("prefix", nargs="?", default="ch.demo")
        ap.add_argument("--db", default=None)
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        conn = sqlite3.connect(db)
        try:
            result = get_tacid_branch_function_report(conn, ns.prefix)
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if cmd in ("cleanup", "spawn-cleanup", "dead-tasks", "ch7"):
        ap = argparse.ArgumentParser(prog="code_health.py cleanup")
        ap.add_argument("--db", default=None)
        ap.add_argument("--migrate", action="store_true")
        ap.add_argument("--limit", type=int, default=50)
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        if ns.migrate:
            from db_schema import ensure_schema

            ensure_schema(db)
        conn = sqlite3.connect(db)
        try:
            result = spawn_dead_function_cleanup_tasks(
                conn, limit=ns.limit, commit=True
            )
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    if cmd in ("bind", "bind-location"):
        ap = argparse.ArgumentParser(prog="code_health.py bind")
        ap.add_argument("--db", default=None)
        ap.add_argument("--register-id", default=None)
        ap.add_argument("--module", required=False)
        ap.add_argument("--function", required=False)
        ap.add_argument("--file", required=True)
        ap.add_argument("--line-start", type=int, required=True)
        ap.add_argument("--line-end", type=int, default=None)
        ap.add_argument("--code", default=None, help="optional code span text")
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        from managed_coding import bind_register_source_location

        conn = sqlite3.connect(db)
        try:
            result = bind_register_source_location(
                conn,
                register_id=ns.register_id,
                module_name=ns.module,
                function_name=ns.function,
                file_path=ns.file,
                line_start=ns.line_start,
                line_end=ns.line_end,
                code_span=ns.code,
                commit=True,
            )
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("ok") else 1

    print(
        "usage: code_health.py [contracts|selftest|verify-schema|demo|harvest|report|branch|cleanup|bind]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
