"""Fail-Handling: FH0–FH6 (goal, assemble, vision, report, option SSOT match).

Pipeline B only — never a structure gate. Hard gate remains PRAGMA exact-set in schema_qc.
Shared with gate only via agent.db IDs (event_id, task_id, vision_id).
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "agent.db"
DEFAULT_BROWSER_PORT = 8766

try:
    from db_schema import get_setting
except Exception:
    get_setting = None

# ---------------------------------------------------------------------------
# FH0 — goal_type enum + standardized envelope column contracts
# ---------------------------------------------------------------------------

GOAL_TYPES = (
    "fix_schema",
    "fix_expected",
    "fill_ssot",
    "update_ssot",
    "delete_ssot",
    "implement_capability",
    "investigate",
    "waive",
)

STD_ROW_COLUMNS = (
    "case_id",
    "qc_task_id",
    "remediate_task_id",
    "table_name",
    "gate_result",
    "source",
    "dim_key",
    "dim_value",
    "expected_value",
    "match",
    "op",
    "evidence_ref",
    "created_at",
)

SSOT_OPS = ("add", "update", "del", "keep")

STEP_SOURCES = (
    "pragma",
    "vision",
    "ssot",
    "ollama",
    "human",
    "mcp",
    "api",
    "schema_qc",
    "fail_handling",
    "task_ssot",
    "schema_ssot",
    "fault_fact",
)


def validate_goal_type(goal_type: str | None) -> str:
    g = (goal_type or "").strip()
    if g not in GOAL_TYPES:
        raise ValueError(f"goal_type must be one of {GOAL_TYPES}, got {goal_type!r}")
    return g


def make_std_row(
    *,
    case_id: int | None = None,
    qc_task_id: int | None = None,
    remediate_task_id: int | None = None,
    table_name: str | None = None,
    gate_result: str | None = None,
    source: str = "fail_handling",
    dim_key: str,
    dim_value: Any = None,
    expected_value: Any = None,
    match: bool | None = None,
    op: str | None = None,
    evidence_ref: str | None = None,
    created_at: str | None = None,
    target_store: str | None = None,
    note: str | None = None,
) -> dict:
    """Build one STEP 2/4/5 standardized row (JSON-serializable)."""
    if op is not None and op not in SSOT_OPS:
        raise ValueError(f"op must be one of {SSOT_OPS}, got {op!r}")
    if gate_result is not None and gate_result not in ("pass", "fail"):
        raise ValueError("gate_result must be pass|fail when set")

    def _ser(v: Any) -> Any:
        if v is None or isinstance(v, (str, int, float, bool)):
            return v
        return json.loads(json.dumps(v, ensure_ascii=False))

    row = {
        "case_id": case_id,
        "qc_task_id": qc_task_id,
        "remediate_task_id": remediate_task_id,
        "table_name": table_name,
        "gate_result": gate_result,
        "source": source,
        "dim_key": dim_key,
        "dim_value": _ser(dim_value),
        "expected_value": _ser(expected_value),
        "match": match,
        "op": op,
        "evidence_ref": evidence_ref,
        "created_at": created_at,
    }
    if target_store is not None:
        row["target_store"] = target_store
    if note is not None:
        row["note"] = note
    return row


def contracts_doc() -> dict:
    """Machine-readable FH0/FH3 contract summary."""
    return {
        "pipeline": "B_fail_handling",
        "depends_on_write_ssot": False,
        "gate": "never — structure gate is A only (PRAGMA exact set)",
        "goal_types": list(GOAL_TYPES),
        "std_row_columns": list(STD_ROW_COLUMNS) + ["target_store", "note"],
        "ssot_ops": list(SSOT_OPS),
        "steps": {
            "1": "hard fail already produced fault + remediate task",
            "2": "optional Ollama QC PNG → std rows source=ollama|vision",
            "3": "goal on remediate task (goal_type/goal_text/success_criteria)",
            "4": "multi-dim SSOT assemble → op add|update|del + plan_steps",
            "5": "fault/task report rollup std rows",
        },
        "fh3": {
            "entry": "assemble_fail_ssot / run_fail_ssot_assemble",
            "writes": "payload.step4_delta_rows, payload.plan_steps, optional facts",
            "not_gate": True,
        },
        "fh4": {
            "entry": "vision_detail_to_std_rows / run_fail_vision_step2",
            "input": "vision_asset / qc_evidence PNG / vision_id",
            "writes": "payload.step2_std_rows, step2_summary, optional facts",
            "not_gate": True,
            "dims": [
                "vision.severity",
                "vision.ui_state",
                "vision.table_visible",
                "vision.column_headers_json",
                "vision.likely_cause",
                "vision.recommended_human_action",
                "vision.confidence",
                "vision.notes",
                "vision.model",
                "vision.error",
            ],
        },
        "fh5": {
            "entry": "build_fail_report / run_fail_report",
            "writes": "fault_report table + payload.step5_* + optional facts/notify",
            "not_gate": True,
            "rollup": [
                "gate",
                "goal",
                "step1",
                "step2_vision",
                "step4_deltas",
                "plan_steps",
                "links",
            ],
        },
        "fh6": {
            "entry": "match_fault_option_ssot / run_fault_ssot_match",
            "input": "fault_event + fault_event_fact + fault_ssot catalog",
            "writes": "match_score/status on analysis+facts; option_id/solution_id link",
            "not_gate": True,
            "statuses": ["matched", "weak", "unmatched", "ambiguous"],
        },
        "phase5": {
            "entry": "run_fail_handling_supervisor / spawn_detached_fail_handling",
            "order": ["assemble", "vision?", "match", "report"],
            "writes": "payload.supervisor_* + each step side effects",
            "not_gate": True,
            "note": "orchestrates B only; never flips PRAGMA match_ok",
        },
    }


# ---------------------------------------------------------------------------
# FH4 — STEP2 Ollama / vision QC PNG → standardized rows (never a gate)
# ---------------------------------------------------------------------------

VISION_DIM_KEYS = (
    "vision.severity",
    "vision.ui_state",
    "vision.table_visible",
    "vision.column_headers_json",
    "vision.likely_cause",
    "vision.recommended_human_action",
    "vision.confidence",
    "vision.notes",
    "vision.summary",
    "vision.model",
    "vision.error",
)


def vision_detail_to_std_rows(
    *,
    detail: dict | None = None,
    summary: str | None = None,
    model: str | None = None,
    error: str | None = None,
    case_id: int | None = None,
    qc_task_id: int | None = None,
    remediate_task_id: int | None = None,
    table_name: str | None = None,
    gate_result: str | None = "fail",
    vision_id: int | None = None,
    image_path: str | None = None,
    source: str = "ollama",
    created_at: str | None = None,
) -> list[dict]:
    """Normalize vision/Ollama JSON into STEP2 standardized rows. Not a gate."""
    detail = detail if isinstance(detail, dict) else {}
    evidence = None
    if vision_id is not None:
        evidence = f"vision_id:{vision_id}"
    elif image_path:
        evidence = str(image_path)

    base = dict(
        case_id=case_id,
        qc_task_id=qc_task_id,
        remediate_task_id=remediate_task_id,
        table_name=table_name,
        gate_result=gate_result if gate_result in ("pass", "fail", None) else "fail",
        source=source if source in STEP_SOURCES else "ollama",
        evidence_ref=evidence,
        created_at=created_at,
    )

    # Map model fields → dim_key
    headers = detail.get("column_headers")
    if headers is None:
        headers = detail.get("column_headers_json")
    table_vis = detail.get("table_visible")
    if table_vis is None:
        table_vis = detail.get("table_selected")

    pairs: list[tuple[str, Any]] = [
        ("vision.severity", detail.get("severity")),
        ("vision.ui_state", detail.get("ui_state")),
        ("vision.table_visible", table_vis),
        ("vision.column_headers_json", headers),
        ("vision.likely_cause", detail.get("likely_cause")),
        (
            "vision.recommended_human_action",
            detail.get("recommended_human_action"),
        ),
        ("vision.confidence", detail.get("confidence")),
        ("vision.notes", detail.get("notes")),
    ]
    if summary:
        pairs.append(("vision.summary", summary))
    if model:
        pairs.append(("vision.model", model))
    if error:
        pairs.append(("vision.error", error))

    rows: list[dict] = []
    for dim_key, val in pairs:
        if val is None or val == "":
            continue
        rows.append(
            make_std_row(
                **base,
                dim_key=dim_key,
                dim_value=val,
                match=None,
                op="keep",
                note="STEP2 vision assist only — never structure gate",
            )
        )

    # Always emit a marker row so consumers know STEP2 ran
    rows.insert(
        0,
        make_std_row(
            **base,
            dim_key="vision.step2_ran",
            dim_value=True if not error else False,
            expected_value=True,
            match=(error is None),
            op="keep",
            note="FH4 STEP2 marker (non-gate)",
        ),
    )
    return rows


def analyze_qc_png(
    image_path: str | Path | None,
    *,
    table_name: str | None = None,
    fault_type: str = "schema_qc_fail",
    event_id: int | None = None,
    expected_columns: list | None = None,
    observed_columns: list | None = None,
    model: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Call Ollama vision on a QC PNG; return dict compatible with VisionResult fields.

    Never raises into gate path — errors become result['error'].
    """
    try:
        from vision_analyze import (
            DEFAULT_OLLAMA_BASE,
            DEFAULT_TIMEOUT,
            DEFAULT_VISION_MODEL,
            _extract_json,
            _file_to_b64,
        )
        import urllib.error
        import urllib.request
    except Exception as e:
        return {
            "model": model or "unknown",
            "summary": f"vision import failed: {type(e).__name__}",
            "detail": {},
            "raw_text": str(e),
            "error": f"import_{type(e).__name__}",
        }

    model = model or DEFAULT_VISION_MODEL
    base_url = (base_url or DEFAULT_OLLAMA_BASE).rstrip("/")
    timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    meta = {
        "event_id": event_id,
        "fault_type": fault_type,
        "table_name": table_name,
        "expected_columns": list(expected_columns or []),
        "observed_columns": list(observed_columns or []),
        "image_path": str(image_path) if image_path else None,
        "pipeline": "B_fail_handling_STEP2",
        "gate": "never",
    }
    prompt = (
        "You are an ops assistant analyzing a Windows desktop screenshot from "
        "Schema QC / db_browser (local Task Center or table view).\n"
        "Return ONLY a JSON object with keys:\n"
        "  severity: one of low|medium|high|critical\n"
        "  ui_state: short description of what is on screen\n"
        "  table_visible: table name if readable, else null or false\n"
        "  column_headers: array of column header strings visible in UI (or [])\n"
        "  likely_cause: likely cause given schema QC fault metadata\n"
        "  recommended_human_action: what a human should do next\n"
        "  confidence: number 0..1\n"
        "  notes: optional short note\n"
        f"QC metadata: {json.dumps(meta, ensure_ascii=False)}\n"
        "This analysis is ASSIST ONLY and is never a structure pass/fail gate.\n"
        "If there is no image, say so in ui_state and lower confidence.\n"
    )

    if image_path and Path(image_path).is_file():
        try:
            b64 = _file_to_b64(image_path)
        except Exception as e:
            return {
                "model": model,
                "summary": f"read image failed: {type(e).__name__}",
                "detail": {},
                "raw_text": str(e),
                "error": f"image_read_{type(e).__name__}",
            }
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64],
                }
            ],
            "stream": False,
            "format": "json",
        }
    else:
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt + "\nNo image was available.",
                }
            ],
            "stream": False,
            "format": "json",
        }

    url = f"{base_url}/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return {
            "model": model,
            "summary": f"vision HTTP error: {e.code}",
            "detail": {},
            "raw_text": err,
            "error": f"ollama_http_{e.code}",
        }
    except Exception as e:
        return {
            "model": model,
            "summary": f"vision failed: {type(e).__name__}",
            "detail": {},
            "raw_text": str(e),
            "error": f"ollama_{type(e).__name__}",
        }

    message = (body.get("message") or {}) if isinstance(body, dict) else {}
    raw = message.get("content") or body.get("response") or ""
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    detail = _extract_json(raw)
    summary = (
        f"[{detail.get('severity', '?')}] {detail.get('ui_state', '')} | "
        f"cause={detail.get('likely_cause', '')}"
    ).strip()
    if len(summary) > 280:
        summary = summary[:277] + "..."
    return {
        "model": model,
        "summary": summary or "analysis complete",
        "detail": detail,
        "raw_text": raw,
        "error": None,
    }


def resolve_qc_vision_path(
    conn: sqlite3.Connection,
    *,
    vision_id: int | None = None,
    remediate_task_id: int | None = None,
    qc_task_id: int | None = None,
    case_id: int | None = None,
    image_path: str | None = None,
) -> tuple[int | None, str | None, str | None]:
    """Resolve (vision_id, path, error). Prefer explicit path, then ids."""
    if image_path:
        p = str(image_path)
        if os.path.isfile(p):
            return vision_id, p, None
        return vision_id, None, f"image_missing:{p}"

    def _path_from_vid(vid: int) -> tuple[str | None, str | None]:
        row = conn.execute(
            "SELECT path_or_url FROM vision_asset WHERE id = ?", (vid,)
        ).fetchone()
        if not row or not row[0]:
            return None, f"vision_asset_missing:{vid}"
        path = str(row[0])
        # relative paths resolve vs package dir
        if not os.path.isabs(path):
            base = os.path.dirname(os.path.abspath(__file__))
            cand = os.path.join(base, path)
            if os.path.isfile(cand):
                path = cand
        if not os.path.isfile(path):
            return path, f"vision_file_missing:{path}"
        return path, None

    # collect candidate vision ids
    candidates: list[int] = []
    if vision_id is not None:
        candidates.append(int(vision_id))

    def _add_from_task(tid: int | None) -> None:
        if tid is None:
            return
        try:
            r = conn.execute(
                "SELECT qc_vision_id, payload_json FROM dev_task WHERE id = ?",
                (int(tid),),
            ).fetchone()
        except sqlite3.Error:
            return
        if not r:
            return
        if r[0] is not None:
            candidates.append(int(r[0]))
        try:
            pl = json.loads(r[1] or "{}")
        except Exception:
            pl = {}
        if isinstance(pl, dict):
            for k in ("vision_id", "qc_vision_id", "step2_vision_id"):
                v = pl.get(k)
                if v is not None and str(v).isdigit():
                    candidates.append(int(v))

    _add_from_task(remediate_task_id)
    _add_from_task(qc_task_id)

    if case_id is not None:
        try:
            er = conn.execute(
                "SELECT vision_id FROM fault_event WHERE event_id = ?",
                (int(case_id),),
            ).fetchone()
            if er and er[0] is not None:
                candidates.append(int(er[0]))
            fr = conn.execute(
                """
                SELECT value_text FROM fault_event_fact
                WHERE event_id = ? AND keyword = 'vision_id'
                ORDER BY id DESC LIMIT 1
                """,
                (int(case_id),),
            ).fetchone()
            if fr and str(fr[0]).isdigit():
                candidates.append(int(fr[0]))
        except sqlite3.Error:
            pass

    seen: set[int] = set()
    last_err = "vision_id_unresolved"
    for vid in candidates:
        if vid in seen:
            continue
        seen.add(vid)
        path, err = _path_from_vid(vid)
        if path and not err:
            return vid, path, None
        if err:
            last_err = err
        elif path:
            return vid, path, last_err
    return (candidates[0] if candidates else None), None, last_err


def run_fail_vision_step2(
    conn: sqlite3.Connection,
    *,
    remediate_task_id: int | None = None,
    case_id: int | None = None,
    vision_id: int | None = None,
    image_path: str | None = None,
    write_payload: bool = True,
    write_facts: bool = True,
    commit: bool = True,
    call_ollama: bool = True,
    model: str | None = None,
    timeout: float | None = None,
    detail_override: dict | None = None,
) -> dict:
    """STEP2: resolve QC PNG → Ollama (optional) → std rows on remediate payload/facts.

    Never a gate. Ollama/MCP failures become step2 error rows only.
    """
    if remediate_task_id is None and case_id is None and vision_id is None and not image_path:
        raise ValueError("remediate_task_id, case_id, vision_id, or image_path required")

    # Resolve remediate task from case if needed
    if remediate_task_id is None and case_id is not None:
        row = conn.execute(
            """
            SELECT value_text FROM fault_event_fact
            WHERE event_id = ? AND keyword = 'remediation_task_id'
            ORDER BY id DESC LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if row and str(row[0]).isdigit():
            remediate_task_id = int(row[0])

    payload: dict = {}
    qc_task_id = None
    table_name = None
    expected_columns: list = []
    observed_columns: list = []
    if remediate_task_id is not None:
        trow = conn.execute(
            "SELECT id, payload_json, parent_task_id FROM dev_task WHERE id = ?",
            (int(remediate_task_id),),
        ).fetchone()
        if not trow:
            raise ValueError(f"unknown remediate_task_id: {remediate_task_id}")
        try:
            payload = json.loads(trow[1] or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if case_id is None and payload.get("fault_event_id") is not None:
            try:
                case_id = int(payload["fault_event_id"])
            except Exception:
                pass
        qc_task_id = payload.get("qc_task_id")
        if qc_task_id is not None:
            try:
                qc_task_id = int(qc_task_id)
            except Exception:
                qc_task_id = trow[2]
        else:
            qc_task_id = trow[2]
        table_name = payload.get("table") or (payload.get("goal_context") or {}).get(
            "table"
        )
        expected_columns = list(payload.get("expected_columns") or [])
        observed_columns = list(payload.get("observed_columns") or [])
        if vision_id is None:
            for k in ("vision_id", "qc_vision_id", "step2_vision_id"):
                v = payload.get(k)
                if v is not None and str(v).isdigit():
                    vision_id = int(v)
                    break

    vid, path, resolve_err = resolve_qc_vision_path(
        conn,
        vision_id=vision_id,
        remediate_task_id=remediate_task_id,
        qc_task_id=qc_task_id if isinstance(qc_task_id, int) else None,
        case_id=case_id,
        image_path=image_path,
    )

    vision_out: dict
    if detail_override is not None:
        vision_out = {
            "model": model or "override",
            "summary": str(
                (detail_override or {}).get("ui_state")
                or (detail_override or {}).get("summary")
                or "override detail"
            ),
            "detail": dict(detail_override),
            "raw_text": json.dumps(detail_override, ensure_ascii=False),
            "error": None,
        }
    elif not call_ollama:
        vision_out = {
            "model": model or "skipped",
            "summary": "ollama skipped",
            "detail": {
                "severity": "low",
                "ui_state": "ollama_skipped",
                "likely_cause": "call_ollama=false",
                "recommended_human_action": "Re-run STEP2 with Ollama",
                "confidence": 0.0,
            },
            "raw_text": "",
            "error": resolve_err or "ollama_skipped",
        }
    else:
        vision_out = analyze_qc_png(
            path,
            table_name=str(table_name) if table_name else None,
            fault_type="schema_qc_fail",
            event_id=case_id,
            expected_columns=expected_columns,
            observed_columns=observed_columns,
            model=model,
            timeout=timeout,
        )
        if resolve_err and not path:
            # keep ollama text fallback but surface resolve error
            if not vision_out.get("error"):
                vision_out["error"] = resolve_err
            else:
                vision_out["error"] = f"{resolve_err};{vision_out['error']}"

    rows = vision_detail_to_std_rows(
        detail=vision_out.get("detail") or {},
        summary=vision_out.get("summary"),
        model=vision_out.get("model"),
        error=vision_out.get("error"),
        case_id=case_id,
        qc_task_id=qc_task_id if isinstance(qc_task_id, int) else None,
        remediate_task_id=int(remediate_task_id) if remediate_task_id is not None else None,
        table_name=str(table_name) if table_name else None,
        gate_result="fail",
        vision_id=vid,
        image_path=path,
        source="ollama" if call_ollama and detail_override is None else (
            "vision" if detail_override is not None else "ollama"
        ),
    )

    summary = vision_out.get("summary") or ""
    result = {
        "step2_std_rows": rows,
        "step2_summary": summary,
        "step2_detail": vision_out.get("detail") or {},
        "step2_model": vision_out.get("model"),
        "step2_error": vision_out.get("error"),
        "vision_id": vid,
        "image_path": path,
        "resolve_error": resolve_err,
        "case_id": case_id,
        "qc_task_id": qc_task_id if isinstance(qc_task_id, int) else None,
        "remediate_task_id": int(remediate_task_id) if remediate_task_id is not None else None,
        "table_name": table_name,
        "row_count": len(rows),
        "analyzer": "fail_handling.run_fail_vision_step2",
        "gate": "never",
    }

    if write_payload and remediate_task_id is not None:
        payload["step2_std_rows"] = rows
        payload["step2_summary"] = summary
        payload["step2_detail"] = vision_out.get("detail") or {}
        payload["step2_model"] = vision_out.get("model")
        payload["step2_error"] = vision_out.get("error")
        payload["step2_vision_id"] = vid
        payload["step2_image_path"] = path
        payload["step2_analyzer"] = result["analyzer"]
        payload["step2_pending"] = False
        if vid is not None and payload.get("vision_id") is None:
            payload["vision_id"] = vid
        conn.execute(
            """
            UPDATE dev_task
            SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), int(remediate_task_id)),
        )

    if write_facts and case_id is not None:
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
                VALUES (?, ?, ?, ?, 'fail_handling')
                """,
                (int(case_id), keyword, text, value_type),
            )

        _fact("step2_summary", summary)
        _fact("step2_std_rows", rows)
        _fact("step2_model", vision_out.get("model"))
        _fact("step2_error", vision_out.get("error"))
        _fact("step2_vision_id", vid, "number")
        _fact("step2_image_path", path)

    if commit:
        conn.commit()

    result["payload_written"] = bool(write_payload and remediate_task_id is not None)
    result["facts_written"] = bool(write_facts and case_id is not None)
    return result


# ---------------------------------------------------------------------------
# FH5 — STEP5 fault/task report rollup (never a gate)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def build_fail_report(
    *,
    case_id: int | None = None,
    qc_task_id: int | None = None,
    remediate_task_id: int | None = None,
    table_name: str | None = None,
    gate_result: str | None = "fail",
    goal: dict | None = None,
    step1_std_rows: list | None = None,
    step2_std_rows: list | None = None,
    step2_summary: str | None = None,
    step4_delta_rows: list | None = None,
    step4_summary: str | None = None,
    step4_counts: dict | None = None,
    plan_steps: list | None = None,
    vision_id: int | None = None,
    image_path: str | None = None,
    browser_url: str | None = None,
    fault_status: str | None = None,
    fault_type: str | None = None,
    task_label: str | None = None,
    links: dict | None = None,
) -> dict:
    """Pure STEP5 rollup: gate + goal + STEP1/2/4 → report envelope + std rows + markdown.

    Never a structure gate.
    """
    goal = goal if isinstance(goal, dict) else {}
    goal_type = goal.get("goal_type")
    goal_text = goal.get("goal_text")
    success = goal.get("success_criteria")
    ctx = goal.get("goal_context") if isinstance(goal.get("goal_context"), dict) else {}
    table_name = table_name or ctx.get("table") or None
    plan_steps = list(plan_steps or goal.get("plan_steps") or [])
    step1 = list(step1_std_rows or [])
    step2 = list(step2_std_rows or [])
    deltas = list(step4_delta_rows or [])
    counts = dict(step4_counts or {})
    if not counts and deltas:
        counts = {
            "add": sum(1 for d in deltas if isinstance(d, dict) and d.get("op") == "add"),
            "update": sum(1 for d in deltas if isinstance(d, dict) and d.get("op") == "update"),
            "del": sum(1 for d in deltas if isinstance(d, dict) and d.get("op") == "del"),
            "keep": sum(1 for d in deltas if isinstance(d, dict) and d.get("op") == "keep"),
            "total": len(deltas),
        }

    top_deltas = []
    for d in deltas:
        if not isinstance(d, dict):
            continue
        if d.get("op") in ("add", "update", "del"):
            top_deltas.append(
                {
                    "dim_key": d.get("dim_key"),
                    "op": d.get("op"),
                    "dim_value": d.get("dim_value"),
                    "expected_value": d.get("expected_value"),
                    "target_store": d.get("target_store"),
                    "note": d.get("note"),
                }
            )
        if len(top_deltas) >= 12:
            break

    vision_bits = {}
    for r in step2:
        if not isinstance(r, dict):
            continue
        k = r.get("dim_key")
        if k in (
            "vision.severity",
            "vision.ui_state",
            "vision.table_visible",
            "vision.likely_cause",
            "vision.recommended_human_action",
            "vision.confidence",
            "vision.summary",
            "vision.error",
            "vision.model",
        ):
            vision_bits[str(k).split(".", 1)[-1]] = r.get("dim_value")

    port = DEFAULT_BROWSER_PORT
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            port = int(get_setting(conn, "browser.port", DEFAULT_BROWSER_PORT))
        finally:
            conn.close()
    except Exception:
        pass

    link_map = {
        "event_id": case_id,
        "qc_task_id": qc_task_id,
        "remediate_task_id": remediate_task_id,
        "vision_id": vision_id,
        "image_path": image_path,
        "browser_url": browser_url,
        "task_center": (
            f"http://127.0.0.1:{port}/tasks?task_id={remediate_task_id}"
            if remediate_task_id is not None
            else None
        ),
    }
    if isinstance(links, dict):
        link_map.update({k: v for k, v in links.items() if v is not None})

    summary_parts = [
        f"gate={gate_result or '?'}",
        f"goal={goal_type or '-'}",
        f"table={table_name or '-'}",
        f"case={case_id if case_id is not None else '-'}",
        f"task={remediate_task_id if remediate_task_id is not None else '-'}",
    ]
    if counts:
        summary_parts.append(
            f"deltas add={counts.get('add', 0)} update={counts.get('update', 0)} "
            f"del={counts.get('del', 0)}"
        )
    if step2_summary:
        summary_parts.append(f"vision={(step2_summary or '')[:80]}")
    summary = " | ".join(summary_parts)

    base = dict(
        case_id=case_id,
        qc_task_id=qc_task_id,
        remediate_task_id=remediate_task_id,
        table_name=table_name,
        gate_result=gate_result if gate_result in ("pass", "fail", None) else "fail",
        source="fail_handling",
        created_at=_now_iso(),
    )
    report_rows: list[dict] = [
        make_std_row(
            **base,
            dim_key="report.step5_ran",
            dim_value=True,
            match=True,
            op="keep",
            note="FH5 report marker (non-gate)",
        ),
        make_std_row(
            **base,
            dim_key="report.summary",
            dim_value=summary,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.goal_type",
            dim_value=goal_type,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.goal_text",
            dim_value=goal_text,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.success_criteria",
            dim_value=success,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.plan_steps",
            dim_value=plan_steps,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.step4_counts",
            dim_value=counts,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.top_deltas",
            dim_value=top_deltas,
            op="keep",
        ),
        make_std_row(
            **base,
            dim_key="report.links",
            dim_value=link_map,
            op="keep",
        ),
    ]
    if step2_summary or vision_bits:
        vbase = dict(base)
        vbase["source"] = "ollama"
        report_rows.append(
            make_std_row(
                **vbase,
                dim_key="report.vision_summary",
                dim_value=step2_summary or vision_bits.get("summary"),
                evidence_ref=(f"vision_id:{vision_id}" if vision_id is not None else image_path),
                op="keep",
            )
        )

    # markdown artifact
    md_lines = [
        f"# Fail-Handling Report (STEP5)",
        f"",
        f"- **gate**: `{gate_result}` (structure gate from STEP1 only)",
        f"- **goal_type**: `{goal_type}`",
        f"- **goal_text**: {goal_text or ''}",
        f"- **success_criteria**: {success or ''}",
        f"- **table**: `{table_name or '-'}`",
        f"- **case_id (event)**: {case_id if case_id is not None else '-'}",
        f"- **qc_task_id**: {qc_task_id if qc_task_id is not None else '-'}",
        f"- **remediate_task_id**: {remediate_task_id if remediate_task_id is not None else '-'}",
        f"- **task_label**: {task_label or '-'}",
        f"- **fault_type/status**: {fault_type or '-'} / {fault_status or '-'}",
        f"- **vision_id**: {vision_id if vision_id is not None else '-'}",
        f"",
        f"## Summary",
        f"",
        summary,
        f"",
        f"## Plan steps",
        f"",
    ]
    if plan_steps:
        for i, s in enumerate(plan_steps, 1):
            md_lines.append(f"{i}. {s}")
    else:
        md_lines.append("_none_")
    md_lines.extend(["", "## Top STEP4 deltas", ""])
    if top_deltas:
        md_lines.append("| op | dim_key | have | want | store |")
        md_lines.append("|----|---------|------|------|-------|")
        for d in top_deltas:
            md_lines.append(
                f"| {d.get('op')} | `{d.get('dim_key')}` | "
                f"{json.dumps(d.get('dim_value'), ensure_ascii=False) if not isinstance(d.get('dim_value'), str) else d.get('dim_value')} | "
                f"{json.dumps(d.get('expected_value'), ensure_ascii=False) if not isinstance(d.get('expected_value'), str) else d.get('expected_value')} | "
                f"{d.get('target_store') or ''} |"
            )
    else:
        md_lines.append("_none_")
    md_lines.extend(["", "## Vision (STEP2 assist)", ""])
    if step2_summary or vision_bits:
        md_lines.append(f"- summary: {step2_summary or vision_bits.get('summary') or ''}")
        for k in ("severity", "ui_state", "likely_cause", "recommended_human_action", "confidence"):
            if k in vision_bits:
                md_lines.append(f"- {k}: {vision_bits[k]}")
    else:
        md_lines.append("_not run_")
    md_lines.extend(["", "## Links", ""])
    for k, v in link_map.items():
        if v is not None:
            md_lines.append(f"- **{k}**: {v}")
    md_lines.extend(
        [
            "",
            "---",
            "_Pipeline B Fail-Handling · STEP5 never flips structure gate._",
            "",
        ]
    )
    markdown_text = "\n".join(md_lines)

    notify_title = (
        f"FH report case={case_id if case_id is not None else '-'} "
        f"[{goal_type or '?'}] {table_name or ''}"
    ).strip()
    notify_body = summary
    if plan_steps:
        notify_body += "\nplan: " + " → ".join(str(s) for s in plan_steps[:4])
    if link_map.get("task_center"):
        notify_body += f"\n{link_map['task_center']}"

    envelope = {
        "case_id": case_id,
        "qc_task_id": qc_task_id,
        "remediate_task_id": remediate_task_id,
        "table_name": table_name,
        "gate_result": gate_result,
        "goal_type": goal_type,
        "goal_text": goal_text,
        "success_criteria": success,
        "goal_context": ctx,
        "plan_steps": plan_steps,
        "step1_count": len(step1),
        "step2_count": len(step2),
        "step2_summary": step2_summary,
        "vision": vision_bits,
        "step4_counts": counts,
        "step4_summary": step4_summary,
        "top_deltas": top_deltas,
        "links": link_map,
        "task_label": task_label,
        "fault_type": fault_type,
        "fault_status": fault_status,
        "summary": summary,
        "markdown_text": markdown_text,
        "step5_std_rows": report_rows,
        "notify_title": notify_title,
        "notify_body": notify_body,
        "builder": "fail_handling.build_fail_report",
        "gate": "never",
        "created_at": base["created_at"],
    }
    return envelope


def run_fail_report(
    conn: sqlite3.Connection,
    *,
    remediate_task_id: int | None = None,
    case_id: int | None = None,
    write_payload: bool = True,
    write_facts: bool = True,
    write_db: bool = True,
    notify: bool = False,
    commit: bool = True,
) -> dict:
    """Load case/task, build STEP5 report, persist fault_report + payload/facts.

    Optional MCP notify (telemetry only — never a gate).
    """
    if remediate_task_id is None and case_id is None:
        raise ValueError("remediate_task_id or case_id required")

    if remediate_task_id is None and case_id is not None:
        row = conn.execute(
            """
            SELECT value_text FROM fault_event_fact
            WHERE event_id = ? AND keyword = 'remediation_task_id'
            ORDER BY id DESC LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if row and str(row[0]).isdigit():
            remediate_task_id = int(row[0])

    payload: dict = {}
    task_label = None
    qc_task_id = None
    if remediate_task_id is not None:
        trow = conn.execute(
            "SELECT id, payload_json, parent_task_id, task_label FROM dev_task WHERE id = ?",
            (int(remediate_task_id),),
        ).fetchone()
        if not trow:
            raise ValueError(f"unknown remediate_task_id: {remediate_task_id}")
        try:
            payload = json.loads(trow[1] or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        task_label = trow[3]
        if case_id is None and payload.get("fault_event_id") is not None:
            try:
                case_id = int(payload["fault_event_id"])
            except Exception:
                pass
        qc_task_id = payload.get("qc_task_id")
        if qc_task_id is not None:
            try:
                qc_task_id = int(qc_task_id)
            except Exception:
                qc_task_id = trow[2]
        else:
            qc_task_id = trow[2]

    fault_status = None
    fault_type = None
    vision_id = payload.get("step2_vision_id") or payload.get("vision_id")
    if vision_id is not None:
        try:
            vision_id = int(vision_id)
        except Exception:
            vision_id = None
    if case_id is not None:
        try:
            er = conn.execute(
                "SELECT fault_type, status, vision_id FROM fault_event WHERE event_id = ?",
                (int(case_id),),
            ).fetchone()
            if er:
                fault_type, fault_status = er[0], er[1]
                if vision_id is None and er[2] is not None:
                    vision_id = int(er[2])
        except sqlite3.Error:
            pass

    goal = extract_goal(payload) or {
        "goal_type": payload.get("goal_type"),
        "goal_text": payload.get("goal_text"),
        "success_criteria": payload.get("success_criteria"),
        "goal_context": payload.get("goal_context") or {},
        "plan_steps": payload.get("plan_steps") or [],
    }
    table_name = payload.get("table") or (goal.get("goal_context") or {}).get("table")

    report = build_fail_report(
        case_id=case_id,
        qc_task_id=qc_task_id if isinstance(qc_task_id, int) else None,
        remediate_task_id=int(remediate_task_id) if remediate_task_id is not None else None,
        table_name=str(table_name) if table_name else None,
        gate_result=payload.get("gate_result") or "fail",
        goal=goal,
        step1_std_rows=payload.get("step1_std_rows") or [],
        step2_std_rows=payload.get("step2_std_rows") or [],
        step2_summary=payload.get("step2_summary"),
        step4_delta_rows=payload.get("step4_delta_rows") or [],
        step4_summary=payload.get("step4_summary"),
        step4_counts=payload.get("step4_counts") or {},
        plan_steps=payload.get("plan_steps") or goal.get("plan_steps") or [],
        vision_id=vision_id,
        image_path=payload.get("step2_image_path"),
        browser_url=payload.get("browser_url"),
        fault_status=fault_status,
        fault_type=fault_type or payload.get("fault_type"),
        task_label=task_label,
    )

    report_id = None
    notified_at = None
    notify_ok = None
    notify_err = None
    if notify:
        try:
            from schema_qc import try_notify as _try_notify
        except Exception:
            try:
                from openclaw_bridge import try_notify as _try_notify
            except Exception as e:
                _try_notify = None  # type: ignore
                notify_err = f"notify_import:{type(e).__name__}"
        if _try_notify is not None:
            try:
                ok, nerr = _try_notify(report["notify_title"], report["notify_body"])
                notify_ok = bool(ok)
                notify_err = nerr
                if ok:
                    notified_at = _now_iso()
            except Exception as e:
                notify_ok = False
                notify_err = f"notify_{type(e).__name__}:{e}"

    if write_db:
        try:
            from db_schema import ensure_task_center_schema, insert_fault_report

            ensure_task_center_schema(conn)
            report_id = insert_fault_report(
                conn,
                event_id=case_id,
                remediate_task_id=(
                    int(remediate_task_id) if remediate_task_id is not None else None
                ),
                qc_task_id=qc_task_id if isinstance(qc_task_id, int) else None,
                table_name=str(table_name) if table_name else None,
                gate_result=report.get("gate_result"),
                goal_type=report.get("goal_type"),
                summary=report.get("summary"),
                markdown_text=report.get("markdown_text"),
                report=report,
                notified_at=notified_at,
                source="fail_handling",
            )
        except Exception as e:
            # table write failure must not become a gate
            report["db_write_error"] = f"{type(e).__name__}:{e}"

    if write_payload and remediate_task_id is not None:
        payload["step5_report_id"] = report_id
        payload["step5_summary"] = report.get("summary")
        payload["step5_std_rows"] = report.get("step5_std_rows")
        payload["step5_markdown"] = report.get("markdown_text")
        payload["step5_top_deltas"] = report.get("top_deltas")
        payload["step5_links"] = report.get("links")
        payload["step5_builder"] = report.get("builder")
        payload["step5_notified_at"] = notified_at
        conn.execute(
            """
            UPDATE dev_task
            SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), int(remediate_task_id)),
        )

    if write_facts and case_id is not None:
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
                VALUES (?, ?, ?, ?, 'fail_handling')
                """,
                (int(case_id), keyword, text, value_type),
            )

        _fact("step5_report_id", report_id, "number")
        _fact("step5_summary", report.get("summary"))
        _fact("step5_std_rows", report.get("step5_std_rows"))
        _fact("step5_top_deltas", report.get("top_deltas"))
        _fact("step5_links", report.get("links"))
        if notified_at:
            _fact("step5_notified_at", notified_at)

    if commit:
        conn.commit()

    out = dict(report)
    out["report_id"] = report_id
    out["notify_ok"] = notify_ok
    out["notify_error"] = notify_err
    out["notified_at"] = notified_at
    out["payload_written"] = bool(write_payload and remediate_task_id is not None)
    out["facts_written"] = bool(write_facts and case_id is not None)
    out["db_written"] = bool(write_db and report_id is not None)
    return out


# ---------------------------------------------------------------------------
# FH1 — infer goal from hard-gate diff
# ---------------------------------------------------------------------------


def infer_schema_qc_goal(
    *,
    table: str,
    diff: dict | None = None,
    expected: list | None = None,
    observed: dict | None = None,
) -> dict:
    """Default goal payload fields for schema.remediate after hard fail."""
    diff = diff or {}
    observed = observed or {}
    table = (table or "").strip() or "unknown"
    missing = list(diff.get("missing_columns") or [])
    extra = list(diff.get("extra_columns") or [])
    exp = list(expected if expected is not None else [])
    obs_names = list(observed.get("column_names") or [])
    summary = (diff.get("summary") or "").strip()

    if missing and not extra:
        goal_type = "fix_schema"
        goal_text = (
            f"Align real table `{table}` to expected column set "
            f"(add missing: {', '.join(missing)})."
        )
        success = (
            f"Re-run schema QC hard gate on `{table}`: PRAGMA column set must equal "
            f"expected {exp!r} (exact set). match_ok=1."
        )
    elif extra and not missing:
        goal_type = "fix_expected"
        goal_text = (
            f"Align expected column set for `{table}` to PRAGMA "
            f"(remove or justify extras in expected: {', '.join(extra)})."
        )
        success = (
            f"Update QC expected_columns (or drop extras from DDL by policy), then "
            f"hard gate pass on `{table}` exact set."
        )
    elif missing and extra:
        goal_type = "investigate"
        goal_text = (
            f"Resolve bidirectional schema drift on `{table}`: "
            f"missing={missing}, extra={extra}."
        )
        success = (
            f"Decide fix_schema vs fix_expected (or both), update SSOT/expected/DDL, "
            f"then hard gate pass on `{table}`."
        )
    else:
        goal_type = "investigate"
        goal_text = (
            f"Hard gate failed on `{table}` without clear missing/extra list; "
            f"diagnose SSOT vs observation. {summary}".strip()
        )
        success = (
            f"Produce STEP4 SSOT delta + refined goal_type; then hard gate pass "
            f"on `{table}` or explicit waive record."
        )

    return {
        "goal_type": goal_type,
        "goal_text": goal_text,
        "success_criteria": success,
        "goal_source": "fail_handling.infer_schema_qc_goal",
        "plan_steps": [],
        "goal_context": {
            "table": table,
            "missing_columns": missing,
            "extra_columns": extra,
            "expected_columns": exp,
            "observed_columns": obs_names,
            "summary": summary,
            "gate": "pragma_exact_set",
        },
    }


def merge_goal_into_payload(payload: dict | None, goal: dict) -> dict:
    """Merge FH1 goal fields into remediate task payload_json dict."""
    out = dict(payload or {})
    for k in (
        "goal_type",
        "goal_text",
        "success_criteria",
        "goal_source",
        "plan_steps",
        "goal_context",
    ):
        if k in goal:
            out[k] = goal[k]
    validate_goal_type(out.get("goal_type"))
    if not str(out.get("goal_text") or "").strip():
        raise ValueError("goal_text required")
    if not str(out.get("success_criteria") or "").strip():
        raise ValueError("success_criteria required")
    if not isinstance(out.get("plan_steps"), list):
        out["plan_steps"] = list(out.get("plan_steps") or [])
    return out


def extract_goal(payload: dict | None) -> dict | None:
    """Read goal block from task payload; None if incomplete."""
    if not isinstance(payload, dict):
        return None
    gt = payload.get("goal_type")
    text = payload.get("goal_text")
    crit = payload.get("success_criteria")
    if not gt and not text:
        return None
    return {
        "goal_type": gt,
        "goal_text": text,
        "success_criteria": crit,
        "goal_source": payload.get("goal_source"),
        "plan_steps": payload.get("plan_steps") or [],
        "goal_context": payload.get("goal_context") or {},
    }


def pragma_diff_std_rows(
    *,
    case_id: int | None,
    qc_task_id: int | None,
    remediate_task_id: int | None,
    table: str,
    diff: dict | None = None,
    expected: list | None = None,
    observed: dict | None = None,
) -> list[dict]:
    """STEP1 observation as standardized rows (for STEP4/5 consumers). Not a gate."""
    diff = diff or {}
    observed = observed or {}
    rows: list[dict] = []
    base = dict(
        case_id=case_id,
        qc_task_id=qc_task_id,
        remediate_task_id=remediate_task_id,
        table_name=table,
        gate_result="fail",
        source="pragma",
    )
    rows.append(
        make_std_row(
            **base,
            dim_key="gate.match_ok",
            dim_value=False,
            expected_value=True,
            match=False,
            op="keep",
        )
    )
    rows.append(
        make_std_row(
            **base,
            dim_key="schema.expected_columns",
            dim_value=list(expected or []),
            op="keep",
        )
    )
    rows.append(
        make_std_row(
            **base,
            dim_key="schema.observed_columns",
            dim_value=list(observed.get("column_names") or []),
            op="keep",
        )
    )
    for col in diff.get("missing_columns") or []:
        rows.append(
            make_std_row(
                **base,
                dim_key=f"schema.column.{col}",
                dim_value=None,
                expected_value="present",
                match=False,
                op="add",
            )
        )
    for col in diff.get("extra_columns") or []:
        rows.append(
            make_std_row(
                **base,
                dim_key=f"schema.column.{col}",
                dim_value="present",
                expected_value=None,
                match=False,
                op="del",
            )
        )
    return rows


# ---------------------------------------------------------------------------
# FH3 — STEP4 multi-dim SSOT assemble
# ---------------------------------------------------------------------------


def _index_dims(rows: list[dict] | None, key_field: str = "dim_key", val_field: str = "value_text") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        k = r.get(key_field) or r.get("keyword")
        if not k:
            continue
        if val_field in r:
            out[str(k)] = r.get(val_field)
        elif "value_text" in r:
            out[str(k)] = r.get("value_text")
        elif "dim_value" in r:
            out[str(k)] = r.get("dim_value")
        else:
            out[str(k)] = r.get("value")
    return out


def _norm(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return str(v).strip()


def plan_steps_from_deltas(
    *,
    goal_type: str,
    goal_text: str,
    deltas: list[dict],
    table_name: str | None = None,
) -> list[str]:
    """Human-readable ordered plan from STEP4 deltas + goal_type."""
    steps: list[str] = []
    steps.append(f"Goal [{goal_type}]: {goal_text}")
    adds = [d for d in deltas if d.get("op") == "add"]
    ups = [d for d in deltas if d.get("op") == "update"]
    dels = [d for d in deltas if d.get("op") == "del"]

    if goal_type == "fix_schema":
        cols = [
            d["dim_key"].split("schema.column.", 1)[-1]
            for d in adds
            if str(d.get("dim_key", "")).startswith("schema.column.")
        ]
        if cols:
            steps.append(
                f"DDL: ADD columns on `{table_name or '?'}`: {', '.join(cols)}"
            )
        steps.append("Refresh schema_ssot from PRAGMA after DDL (optional --write-ssot)")
        steps.append(f"Re-run hard gate QC on `{table_name or '?'}` until match_ok=1")
    elif goal_type == "fix_expected":
        cols = [
            d["dim_key"].split("schema.column.", 1)[-1]
            for d in dels
            if str(d.get("dim_key", "")).startswith("schema.column.")
        ]
        if cols:
            steps.append(
                f"Expected/SSOT: remove or justify extra columns in expected set: {', '.join(cols)}"
            )
        steps.append("Update QC task payload expected_columns / schema_ssot keywords")
        steps.append(f"Re-run hard gate QC on `{table_name or '?'}` until match_ok=1")
    elif goal_type == "implement_capability":
        for d in deltas:
            if d.get("op") in ("add", "update") and "open_browser" in str(d.get("dim_key")):
                steps.append(
                    f"Capability: {d.get('op')} `{d.get('dim_key')}` "
                    f"(have={d.get('dim_value')!r} want={d.get('expected_value')!r}) "
                    f"— {d.get('note') or d.get('target_store') or ''}"
                )
        steps.append("Prefer documented fallback if MCP tool missing; do not hallucinate tools")
        steps.append("Update task_ssot truth dims after implementation")
    elif goal_type in ("fill_ssot", "update_ssot", "delete_ssot"):
        for d in adds[:12]:
            steps.append(f"SSOT ADD `{d.get('dim_key')}` → {d.get('expected_value')!r} ({d.get('target_store')})")
        for d in ups[:12]:
            steps.append(
                f"SSOT UPDATE `{d.get('dim_key')}` {d.get('dim_value')!r} → {d.get('expected_value')!r}"
            )
        for d in dels[:12]:
            steps.append(f"SSOT DEL `{d.get('dim_key')}` (stale={d.get('dim_value')!r})")
    else:  # investigate / waive / other
        steps.append("Review STEP4 deltas; refine goal_type if classification clear")
        for d in (adds + ups + dels)[:15]:
            steps.append(
                f"{str(d.get('op') or '?').upper()} `{d.get('dim_key')}` "
                f"have={d.get('dim_value')!r} want={d.get('expected_value')!r}"
            )

    if not any("Re-run hard gate" in s for s in steps) and table_name and goal_type in (
        "fix_schema",
        "fix_expected",
        "investigate",
    ):
        steps.append(f"Verify with schema QC hard gate on `{table_name}` (telemetry only aside)")
    steps.append("STEP5: roll up fault/task report when deltas accepted")
    return steps


def assemble_fail_ssot(
    *,
    goal: dict | None = None,
    case_id: int | None = None,
    qc_task_id: int | None = None,
    remediate_task_id: int | None = None,
    table_name: str | None = None,
    step1_std_rows: list | None = None,
    task_ssot: list | None = None,
    schema_ssot: list | None = None,
    event_facts: list | None = None,
    gate_result: str = "fail",
) -> dict:
    """STEP4 pure assemble: goal + observations + SSOT packs → delta rows + plan_steps.

    Never flips structure gate. Bugs modeled as SSOT/DDL/expected gaps vs goal.
    """
    goal = goal or {}
    goal_type = str(goal.get("goal_type") or "investigate")
    goal_text = str(goal.get("goal_text") or "(no goal_text)")
    ctx = goal.get("goal_context") if isinstance(goal.get("goal_context"), dict) else {}
    table_name = table_name or ctx.get("table") or None

    base = dict(
        case_id=case_id,
        qc_task_id=qc_task_id,
        remediate_task_id=remediate_task_id,
        table_name=table_name,
        gate_result=gate_result if gate_result in ("pass", "fail") else "fail",
    )

    deltas: list[dict] = []
    seen: set[tuple] = set()

    def _add(row: dict) -> None:
        key = (row.get("dim_key"), row.get("op"), row.get("target_store"))
        if key in seen:
            return
        seen.add(key)
        deltas.append(row)

    # 1) Promote actionable STEP1 rows
    for r in step1_std_rows or []:
        if not isinstance(r, dict):
            continue
        op = r.get("op")
        if op not in ("add", "update", "del"):
            continue
        dim_key = str(r.get("dim_key") or "")
        target = "ddl" if dim_key.startswith("schema.column.") and op == "add" else None
        if dim_key.startswith("schema.column.") and op == "del":
            target = "expected"
        if dim_key.startswith("schema.column.") and goal_type == "fix_expected" and op == "del":
            target = "expected"
        if dim_key.startswith("schema.column.") and goal_type == "fix_schema" and op == "add":
            target = "ddl"
        _add(
            make_std_row(
                **base,
                source=str(r.get("source") or "pragma"),
                dim_key=dim_key,
                dim_value=r.get("dim_value"),
                expected_value=r.get("expected_value"),
                match=False,
                op=op,
                evidence_ref=r.get("evidence_ref") or "step1",
                target_store=target or "observation",
                note="from STEP1 pragma diff",
            )
        )

    # 2) Schema SSOT pack vs expected columns (fill/update)
    schema_map = _index_dims(schema_ssot, key_field="keyword", val_field="value_text")
    exp_cols = list(ctx.get("expected_columns") or [])
    obs_cols = list(ctx.get("observed_columns") or [])
    if table_name and exp_cols:
        ssot_key = "columns_json"
        # common schema_ssot patterns may use different keys; check a few
        present_val = None
        for cand in ("columns_json", "column_names", "expected_columns", "pragma_columns"):
            if cand in schema_map:
                present_val = schema_map[cand]
                ssot_key = cand
                break
        want = json.dumps(exp_cols, ensure_ascii=False)
        if present_val is None and goal_type in ("fix_schema", "fix_expected", "fill_ssot", "investigate"):
            _add(
                make_std_row(
                    **base,
                    source="schema_ssot",
                    dim_key=f"schema_ssot.{ssot_key}",
                    dim_value=None,
                    expected_value=exp_cols,
                    match=False,
                    op="add",
                    target_store="schema_ssot",
                    note="schema_ssot missing column list for table",
                )
            )
        elif present_val is not None and _norm(present_val) != _norm(want) and _norm(present_val) != _norm(exp_cols):
            # also try parse JSON list compare
            try:
                parsed = json.loads(present_val) if isinstance(present_val, str) else present_val
            except Exception:
                parsed = present_val
            if _norm(parsed) != _norm(exp_cols) and set(obs_cols or []) != set(exp_cols):
                _add(
                    make_std_row(
                        **base,
                        source="schema_ssot",
                        dim_key=f"schema_ssot.{ssot_key}",
                        dim_value=present_val,
                        expected_value=exp_cols,
                        match=False,
                        op="update",
                        target_store="schema_ssot",
                        note="schema_ssot column list drifts from goal expected",
                    )
                )

    # 3) task_ssot capability dims vs goal desires
    task_map = _index_dims(task_ssot)
    # Desired capability markers on goal payload / context
    desired_pairs = []
    if goal_type == "implement_capability":
        desired_pairs.extend(
            [
                ("tool.capability.open_browser", "true"),
                ("tool.capability.open_browser.desired", "true"),
            ]
        )
    # generic: goal_context.expected_dims dict
    exp_dims = ctx.get("expected_dims") if isinstance(ctx.get("expected_dims"), dict) else {}
    for k, v in exp_dims.items():
        desired_pairs.append((str(k), v))

    for dim_key, want in desired_pairs:
        have = task_map.get(dim_key)
        if have is None and dim_key.endswith(".desired"):
            continue  # meta marker
        if dim_key.endswith(".desired"):
            # compare sibling without .desired
            real_key = dim_key[: -len(".desired")]
            have = task_map.get(real_key)
            dim_key = real_key
        if have is None:
            _add(
                make_std_row(
                    **base,
                    source="task_ssot",
                    dim_key=dim_key,
                    dim_value=None,
                    expected_value=want,
                    match=False,
                    op="add",
                    target_store="task_ssot",
                    note="task_ssot missing required capability dim",
                )
            )
        elif _norm(have).lower() != _norm(want).lower():
            note = "capability dim mismatch vs goal"
            target = "task_ssot"
            if dim_key == "tool.capability.open_browser" and _norm(have).lower() == "false":
                note = (
                    "MCP open_browser false — implement MCP/API or use fallback.open_browser; "
                    "do not invent browser.open"
                )
                target = "implementation+task_ssot"
            _add(
                make_std_row(
                    **base,
                    source="task_ssot",
                    dim_key=dim_key,
                    dim_value=have,
                    expected_value=want,
                    match=False,
                    op="update",
                    target_store=target,
                    note=note,
                )
            )

    # If implement_capability and fallback exists while open_browser false — keep note row
    if goal_type == "implement_capability":
        ob = task_map.get("tool.capability.open_browser")
        fb = task_map.get("fallback.open_browser")
        if _norm(ob).lower() == "false" and fb:
            _add(
                make_std_row(
                    **base,
                    source="task_ssot",
                    dim_key="fallback.open_browser",
                    dim_value=fb,
                    expected_value="documented_until_mcp",
                    match=True,
                    op="keep",
                    target_store="task_ssot",
                    note="Use documented fallback until MCP tool exists",
                )
            )

    # 4) event facts: surface missing goal facts only as keep/info if needed — skip noise

    plan_steps = plan_steps_from_deltas(
        goal_type=goal_type,
        goal_text=goal_text,
        deltas=deltas,
        table_name=table_name,
    )

    counts = {
        "add": sum(1 for d in deltas if d.get("op") == "add"),
        "update": sum(1 for d in deltas if d.get("op") == "update"),
        "del": sum(1 for d in deltas if d.get("op") == "del"),
        "keep": sum(1 for d in deltas if d.get("op") == "keep"),
        "total": len(deltas),
    }
    summary = (
        f"STEP4 assemble goal_type={goal_type} table={table_name or '-'} "
        f"deltas add={counts['add']} update={counts['update']} del={counts['del']} keep={counts['keep']}"
    )

    return {
        "step4_delta_rows": deltas,
        "plan_steps": plan_steps,
        "summary": summary,
        "counts": counts,
        "goal_type": goal_type,
        "table_name": table_name,
        "case_id": case_id,
        "qc_task_id": qc_task_id,
        "remediate_task_id": remediate_task_id,
        "assembler": "fail_handling.assemble_fail_ssot",
        "gate": "never",
    }


def run_fail_ssot_assemble(
    conn: sqlite3.Connection,
    *,
    remediate_task_id: int | None = None,
    case_id: int | None = None,
    write_payload: bool = True,
    write_facts: bool = True,
    commit: bool = True,
) -> dict:
    """Load task/case from DB, run assemble_fail_ssot, optionally persist.

    Resolve task by remediate_task_id, or by case_id → remediation_task_id fact / payload.
    """
    if remediate_task_id is None and case_id is None:
        raise ValueError("remediate_task_id or case_id required")

    # Resolve case → task
    if remediate_task_id is None and case_id is not None:
        row = conn.execute(
            """
            SELECT value_text FROM fault_event_fact
            WHERE event_id = ? AND keyword = 'remediation_task_id'
            ORDER BY id DESC LIMIT 1
            """,
            (case_id,),
        ).fetchone()
        if row and str(row[0]).isdigit():
            remediate_task_id = int(row[0])
        else:
            # try fault_event.task_id best-effort
            er = conn.execute(
                "SELECT task_id FROM fault_event WHERE event_id = ?", (case_id,)
            ).fetchone()
            if er and er[0] is not None:
                remediate_task_id = int(er[0])
    if remediate_task_id is None:
        raise ValueError("could not resolve remediate_task_id")

    trow = conn.execute(
        "SELECT id, payload_json, parent_task_id FROM dev_task WHERE id = ?",
        (remediate_task_id,),
    ).fetchone()
    if not trow:
        raise ValueError(f"unknown remediate_task_id: {remediate_task_id}")

    try:
        payload = json.loads(trow[1] or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    goal = extract_goal(payload) or {
        "goal_type": payload.get("goal_type") or "investigate",
        "goal_text": payload.get("goal_text") or payload.get("title") or "assemble",
        "success_criteria": payload.get("success_criteria") or "produce STEP4 deltas",
        "goal_context": payload.get("goal_context") or {},
    }
    if case_id is None:
        case_id = payload.get("fault_event_id")
        if case_id is not None:
            try:
                case_id = int(case_id)
            except Exception:
                case_id = None

    qc_task_id = payload.get("qc_task_id")
    if qc_task_id is not None:
        try:
            qc_task_id = int(qc_task_id)
        except Exception:
            qc_task_id = trow[2]
    else:
        qc_task_id = trow[2]

    table_name = payload.get("table") or (goal.get("goal_context") or {}).get("table")

    step1 = payload.get("step1_std_rows") or []
    if not step1 and table_name:
        # rebuild from payload columns if present
        diff = {
            "missing_columns": payload.get("missing_columns") or [],
            "extra_columns": payload.get("extra_columns") or [],
        }
        step1 = pragma_diff_std_rows(
            case_id=case_id,
            qc_task_id=qc_task_id,
            remediate_task_id=remediate_task_id,
            table=str(table_name),
            diff=diff,
            expected=payload.get("expected_columns") or [],
            observed={"column_names": payload.get("observed_columns") or []},
        )

    # task_ssot: this task + parent + linked capability label optional
    task_ssot_rows: list[dict] = []
    try:
        from db_schema import list_task_ssot as _list_ts

        task_ssot_rows = list(_list_ts(conn, int(remediate_task_id)))
        if not task_ssot_rows and trow[2] is not None:
            task_ssot_rows = list(_list_ts(conn, int(trow[2])))
        # also merge oc.open-browser seed if capability goal and empty
        if not task_ssot_rows and str(goal.get("goal_type")) == "implement_capability":
            crow = conn.execute(
                "SELECT id FROM dev_task WHERE task_label = ? ORDER BY id DESC LIMIT 1",
                ("oc.open-browser",),
            ).fetchone()
            if crow:
                task_ssot_rows = list(_list_ts(conn, int(crow[0])))
    except Exception:
        try:
            for r in conn.execute(
                """
                SELECT dim_key, value_text, value_type, source, notes
                FROM task_ssot WHERE task_id = ? ORDER BY sort_order, id
                """,
                (remediate_task_id,),
            ).fetchall():
                task_ssot_rows.append(
                    {
                        "dim_key": r[0],
                        "value_text": r[1],
                        "value_type": r[2],
                        "source": r[3],
                        "notes": r[4],
                    }
                )
        except sqlite3.Error:
            task_ssot_rows = []

    schema_ssot_rows: list[dict] = []
    if table_name:
        try:
            for r in conn.execute(
                """
                SELECT keyword, value_text, value_type, source
                FROM schema_ssot WHERE table_name = ? ORDER BY keyword
                """,
                (table_name,),
            ).fetchall():
                schema_ssot_rows.append(
                    {
                        "keyword": r[0],
                        "value_text": r[1],
                        "value_type": r[2],
                        "source": r[3],
                    }
                )
        except sqlite3.Error:
            schema_ssot_rows = []

    event_facts: list[dict] = []
    if case_id is not None:
        try:
            for r in conn.execute(
                """
                SELECT keyword, value_text, value_type, source
                FROM fault_event_fact WHERE event_id = ? ORDER BY id
                """,
                (case_id,),
            ).fetchall():
                event_facts.append(
                    {
                        "keyword": r[0],
                        "value_text": r[1],
                        "value_type": r[2],
                        "source": r[3],
                    }
                )
        except sqlite3.Error:
            event_facts = []

    result = assemble_fail_ssot(
        goal=goal,
        case_id=case_id,
        qc_task_id=qc_task_id if isinstance(qc_task_id, int) else None,
        remediate_task_id=int(remediate_task_id),
        table_name=str(table_name) if table_name else None,
        step1_std_rows=step1,
        task_ssot=task_ssot_rows,
        schema_ssot=schema_ssot_rows,
        event_facts=event_facts,
        gate_result="fail",
    )

    if write_payload:
        payload["plan_steps"] = result["plan_steps"]
        payload["step4_delta_rows"] = result["step4_delta_rows"]
        payload["step4_summary"] = result["summary"]
        payload["step4_counts"] = result["counts"]
        payload["step4_assembler"] = result["assembler"]
        conn.execute(
            """
            UPDATE dev_task
            SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), int(remediate_task_id)),
        )

    if write_facts and case_id is not None:
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
                VALUES (?, ?, ?, ?, 'fail_handling')
                """,
                (int(case_id), keyword, text, value_type),
            )

        _fact("step4_summary", result["summary"])
        _fact("step4_counts", result["counts"])
        _fact("plan_steps", result["plan_steps"])
        _fact("step4_delta_rows", result["step4_delta_rows"])

    if commit:
        conn.commit()

    result["payload_written"] = bool(write_payload)
    result["facts_written"] = bool(write_facts and case_id is not None)
    return result


# ---------------------------------------------------------------------------
# FH6 — fault_option SSOT match (Phase 3 bridge alignment; never a gate)
# ---------------------------------------------------------------------------

MATCH_STATUSES = ("matched", "weak", "unmatched", "ambiguous")
MATCH_SCORE_MATCHED = 0.70
MATCH_SCORE_WEAK = 0.40
MATCH_SCORE_AMBIGUOUS_GAP = 0.12


def _norm_token(s: Any) -> str:
    return str(s or "").strip().lower()


def _split_csv(s: Any) -> list[str]:
    text = str(s or "").strip()
    if not text:
        return []
    return [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]


def _facts_map(facts: list[dict] | dict | None) -> dict[str, str]:
    """Latest keyword → value_text (list of {keyword,value_text} or plain dict)."""
    out: dict[str, str] = {}
    if facts is None:
        return out
    if isinstance(facts, dict):
        for k, v in facts.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)):
                out[str(k)] = json.dumps(v, ensure_ascii=False)
            else:
                out[str(k)] = str(v)
        return out
    for f in facts:
        if not isinstance(f, dict):
            continue
        k = f.get("keyword")
        if not k:
            continue
        v = f.get("value_text")
        if v is None:
            v = f.get("value")
        if v is None:
            continue
        if isinstance(v, (list, dict)):
            out[str(k)] = json.dumps(v, ensure_ascii=False)
        else:
            out[str(k)] = str(v)
    return out


def _score_option_ssot(
    *,
    option_code: str,
    option_name: str | None,
    ssot_rows: list[dict],
    facts: dict[str, str],
    fault_type: str | None,
    summary: str | None = None,
) -> dict:
    """Score one fault_option against event facts. Pure — no DB."""
    code = _norm_token(option_code)
    ft = _norm_token(fault_type)
    blob = " ".join(
        [
            _norm_token(summary),
            " ".join(_norm_token(v) for v in facts.values()),
            " ".join(_norm_token(k) for k in facts.keys()),
        ]
    )
    ssot: dict[str, dict] = {}
    for r in ssot_rows or []:
        kw = str(r.get("keyword") or "").strip()
        if not kw:
            continue
        try:
            w = float(r.get("weight") if r.get("weight") is not None else 1.0)
        except Exception:
            w = 1.0
        ssot[kw] = {
            "value_text": str(r.get("value_text") if r.get("value_text") is not None else ""),
            "weight": w,
            "value_type": str(r.get("value_type") or "string"),
        }

    hits: list[dict] = []
    weighted = 0.0
    weight_sum = 0.0

    def _hit(kind: str, keyword: str, detail: str, w: float, points: float) -> None:
        nonlocal weighted, weight_sum
        weight_sum += max(w, 0.0)
        weighted += max(w, 0.0) * max(0.0, min(1.0, points))
        hits.append(
            {
                "kind": kind,
                "keyword": keyword,
                "detail": detail,
                "weight": w,
                "points": round(points, 4),
            }
        )

    # 1) fault_type / legacy_fault_types / option code identity
    legacy = _split_csv((ssot.get("legacy_fault_types") or {}).get("value_text"))
    legacy_n = [_norm_token(x) for x in legacy]
    ft_ssot = _norm_token((ssot.get("fault_type") or {}).get("value_text"))
    w_ft = float((ssot.get("fault_type") or {}).get("weight") or 1.0)
    w_leg = float((ssot.get("legacy_fault_types") or {}).get("weight") or 0.95)

    if ft and (ft == code or ft == ft_ssot):
        _hit("fault_type", "fault_type", f"fault_type={ft} == option/code", w_ft, 1.0)
    elif ft and ft in legacy_n:
        _hit("legacy_fault_types", "legacy_fault_types", f"fault_type={ft} in legacy", w_leg, 1.0)
    elif ft and (ft in code or code in ft):
        _hit("fault_type_fuzzy", "fault_type", f"fuzzy {ft}~{code}", max(w_ft * 0.6, 0.3), 0.7)
    else:
        # still count identity slot so unmatched options don't free-ride on fact_keys alone
        if "fault_type" in ssot or "legacy_fault_types" in ssot or code:
            _hit("fault_type_miss", "fault_type", f"no type match ft={ft or '-'} code={code}", w_ft, 0.0)

    # 2) fact_keys coverage
    fk_row = ssot.get("fact_keys")
    if fk_row:
        keys = _split_csv(fk_row["value_text"])
        if keys:
            present = [k for k in keys if k in facts and str(facts.get(k) or "").strip() != ""]
            ratio = len(present) / max(len(keys), 1)
            _hit(
                "fact_keys",
                "fact_keys",
                f"{len(present)}/{len(keys)} keys present",
                float(fk_row["weight"]),
                ratio,
            )

    # 3) direct keyword value equality / containment for catalog dims present on event
    skip_kw = {
        "fact_keys",
        "legacy_fault_types",
        "fault_type",
        "not_gate",
        "not_resolved_by",
        "typical_cause",
        "signal",
        "gate_desc",
        "on_fail",
        "goal_types",
        "watchdog_label",
    }
    for kw, row in ssot.items():
        if kw in skip_kw:
            continue
        if kw not in facts:
            continue
        exp = _norm_token(row["value_text"])
        got = _norm_token(facts.get(kw))
        if not exp:
            _hit("kw_present", kw, "key present (no expected value)", float(row["weight"]) * 0.5, 0.5)
            continue
        if got == exp:
            _hit("kw_eq", kw, f"{got}=={exp}", float(row["weight"]), 1.0)
        elif exp in got or got in exp:
            _hit("kw_sub", kw, f"{got}~{exp}", float(row["weight"]), 0.75)
        else:
            _hit("kw_mismatch", kw, f"{got}!={exp}", float(row["weight"]), 0.05)

    # 4) soft text cues (signal / typical_cause / option name) against blob
    for soft_kw in ("signal", "typical_cause", "watchdog_label"):
        row = ssot.get(soft_kw)
        if not row:
            continue
        tokens = [
            t
            for t in _norm_token(row["value_text"]).replace("/", " ").replace("|", " ").split()
            if len(t) >= 4
        ]
        if not tokens:
            continue
        hit_n = sum(1 for t in tokens if t in blob)
        ratio = hit_n / max(len(tokens), 1)
        if ratio > 0:
            _hit("soft_text", soft_kw, f"{hit_n}/{len(tokens)} tokens in facts/summary", float(row["weight"]) * 0.45, ratio)

    name_n = _norm_token(option_name)
    if name_n and any(t in blob for t in name_n.split() if len(t) >= 4):
        _hit("option_name", "name", "option name tokens in blob", 0.25, 0.6)

    # 5) schema_qc / heartbeat domain boosts from common facts
    if code == "schema_qc_fail":
        boost = 0.0
        if _norm_token(facts.get("match_ok")) in ("0", "false", "no"):
            boost += 0.35
        if _norm_token(facts.get("gate")) in ("pragma_exact_set", "pragma"):
            boost += 0.35
        if facts.get("missing_columns") or facts.get("extra_columns") or facts.get("table"):
            boost += 0.25
        if facts.get("goal_type") or facts.get("remediation_task_id"):
            boost += 0.15
        if boost:
            _hit("domain_schema_qc", "domain", "schema_qc fact pattern", 0.9, min(1.0, boost))
    if code == "heartbeat_off":
        boost = 0.0
        if any(k in facts for k in ("stale_minutes", "heartbeat_last_seen_at", "watchdog_message")):
            boost += 0.5
        if ft in ("heartbeat_timeout", "crash", "heartbeat_off"):
            boost += 0.4
        if "stale" in blob or "heartbeat" in blob or "offline" in blob:
            boost += 0.2
        if boost:
            _hit("domain_heartbeat", "domain", "heartbeat fact pattern", 0.9, min(1.0, boost))

    score = (weighted / weight_sum) if weight_sum > 0 else 0.0
    score = round(max(0.0, min(1.0, score)), 4)
    return {
        "option_code": option_code,
        "option_name": option_name,
        "score": score,
        "hits": hits,
        "weight_sum": round(weight_sum, 4),
        "weighted": round(weighted, 4),
    }


def match_fault_option_ssot(
    *,
    options: list[dict],
    facts: list[dict] | dict | None = None,
    fault_type: str | None = None,
    summary: str | None = None,
    prefer_option_id: int | None = None,
) -> dict:
    """Pure multi-option SSOT match. Never a gate.

    options: [{id, code, name, ssot:[{keyword,value_text,weight,value_type}], solution_id?}]
    """
    fmap = _facts_map(facts)
    ft = fault_type or fmap.get("fault_type")
    scored: list[dict] = []
    for opt in options or []:
        oid = opt.get("id")
        try:
            oid_i = int(oid) if oid is not None else None
        except Exception:
            oid_i = None
        one = _score_option_ssot(
            option_code=str(opt.get("code") or ""),
            option_name=opt.get("name"),
            ssot_rows=list(opt.get("ssot") or []),
            facts=fmap,
            fault_type=ft,
            summary=summary,
        )
        one["option_id"] = oid_i
        one["solution_id"] = opt.get("solution_id")
        one["solution_title"] = opt.get("solution_title")
        # slight sticky prefer if event already linked
        if prefer_option_id is not None and oid_i == int(prefer_option_id):
            one["score"] = round(min(1.0, float(one["score"]) + 0.03), 4)
            one["hits"] = list(one["hits"]) + [
                {
                    "kind": "prefer_existing",
                    "keyword": "option_id",
                    "detail": f"event.option_id={prefer_option_id}",
                    "weight": 0.03,
                    "points": 1.0,
                }
            ]
        scored.append(one)

    scored.sort(key=lambda x: (-float(x.get("score") or 0.0), str(x.get("option_code") or "")))
    best = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None
    status = "unmatched"
    if best is None:
        status = "unmatched"
    else:
        s0 = float(best["score"])
        s1 = float(second["score"]) if second else -1.0
        if s0 < MATCH_SCORE_WEAK:
            status = "unmatched"
        elif second and (s0 - s1) < MATCH_SCORE_AMBIGUOUS_GAP and s1 >= MATCH_SCORE_WEAK:
            status = "ambiguous"
        elif s0 >= MATCH_SCORE_MATCHED:
            status = "matched"
        else:
            status = "weak"

    winner = best if status in ("matched", "weak") else (best if status == "ambiguous" else None)
    # ambiguous still surfaces top candidate but does not auto-trust
    std_rows: list[dict] = []
    base = {
        "source": "fault_ssot",
        "gate_result": None,
        "op": "keep",
    }
    std_rows.append(
        make_std_row(
            **base,
            dim_key="match.status",
            dim_value=status,
            match=status == "matched",
            note="FH6 option SSOT match",
        )
    )
    std_rows.append(
        make_std_row(
            **base,
            dim_key="match.score",
            dim_value=(best or {}).get("score"),
            match=status == "matched",
        )
    )
    if winner and winner.get("option_id") is not None:
        std_rows.append(
            make_std_row(
                **base,
                dim_key="match.option_id",
                dim_value=winner.get("option_id"),
                expected_value=winner.get("option_code"),
                match=status == "matched",
            )
        )
        if winner.get("solution_id") is not None:
            std_rows.append(
                make_std_row(
                    **base,
                    dim_key="match.solution_id",
                    dim_value=winner.get("solution_id"),
                    expected_value=winner.get("solution_title"),
                    match=status == "matched",
                )
            )
    for i, h in enumerate(((best or {}).get("hits") or [])[:12]):
        std_rows.append(
            make_std_row(
                **base,
                dim_key=f"match.hit.{i}.{h.get('kind')}",
                dim_value=h.get("detail"),
                expected_value=h.get("keyword"),
                match=float(h.get("points") or 0) >= 0.5,
                note=f"w={h.get('weight')} p={h.get('points')}",
            )
        )

    prompt = {
        "fault_type": ft,
        "fact_keys": sorted(fmap.keys()),
        "status": status,
        "best": {
            "option_id": (best or {}).get("option_id"),
            "option_code": (best or {}).get("option_code"),
            "score": (best or {}).get("score"),
            "solution_id": (best or {}).get("solution_id"),
        },
        "candidates": [
            {
                "option_id": c.get("option_id"),
                "option_code": c.get("option_code"),
                "score": c.get("score"),
            }
            for c in scored[:5]
        ],
        "gate": "never",
    }

    return {
        "gate": "never",
        "match_status": status,
        "match_score": (best or {}).get("score"),
        "option_id": (winner or best or {}).get("option_id") if status != "unmatched" else None,
        "option_code": (winner or best or {}).get("option_code") if status != "unmatched" else None,
        "solution_id": (winner or best or {}).get("solution_id") if status in ("matched", "weak") else None,
        "solution_title": (winner or best or {}).get("solution_title") if status in ("matched", "weak") else None,
        "candidates": scored,
        "std_rows": std_rows,
        "ssot_prompt": prompt,
        "summary": (
            f"FH6 match_status={status} score={(best or {}).get('score')} "
            f"option={(winner or best or {}).get('option_code')}"
        ),
    }


def load_fault_option_catalog(conn: sqlite3.Connection) -> list[dict]:
    """Active fault_option rows + ssot + top solution."""
    opts = conn.execute(
        """
        SELECT id, code, name, status
        FROM fault_option
        WHERE COALESCE(status, 'active') = 'active'
        ORDER BY id
        """
    ).fetchall()
    out: list[dict] = []
    for o in opts:
        if hasattr(o, "keys"):
            oid, code, name = int(o["id"]), o["code"], o["name"]
        else:
            oid, code, name = int(o[0]), o[1], o[2]
        ssot_rows = conn.execute(
            """
            SELECT keyword, value_text, value_type, weight
            FROM fault_ssot WHERE option_id = ? ORDER BY id
            """,
            (oid,),
        ).fetchall()
        ssot = []
        for r in ssot_rows:
            if hasattr(r, "keys"):
                ssot.append(
                    {
                        "keyword": r["keyword"],
                        "value_text": r["value_text"],
                        "value_type": r["value_type"],
                        "weight": r["weight"],
                    }
                )
            else:
                ssot.append(
                    {
                        "keyword": r[0],
                        "value_text": r[1],
                        "value_type": r[2],
                        "weight": r[3],
                    }
                )
        sol = conn.execute(
            """
            SELECT id, title FROM fault_solution
            WHERE option_id = ? AND COALESCE(status, 'active') = 'active'
            ORDER BY priority ASC, id ASC LIMIT 1
            """,
            (oid,),
        ).fetchone()
        solution_id = None
        solution_title = None
        if sol:
            solution_id = int(sol[0] if not hasattr(sol, "keys") else sol["id"])
            solution_title = sol[1] if not hasattr(sol, "keys") else sol["title"]
        out.append(
            {
                "id": oid,
                "code": code,
                "name": name,
                "ssot": ssot,
                "solution_id": solution_id,
                "solution_title": solution_title,
            }
        )
    return out


def run_fault_ssot_match(
    conn: sqlite3.Connection,
    *,
    case_id: int | None = None,
    remediate_task_id: int | None = None,
    write_event: bool = True,
    write_analysis: bool = True,
    write_facts: bool = True,
    write_payload: bool = True,
    commit: bool = True,
    summary: str | None = None,
) -> dict:
    """Match fault_event facts to fault_option SSOT; persist score/status links.

    Never flips structure gate / match_ok. Does not auto-resolve fault_event.
    """
    if case_id is None and remediate_task_id is None:
        raise ValueError("case_id or remediate_task_id required")

    payload: dict = {}
    if remediate_task_id is not None:
        trow = conn.execute(
            "SELECT id, payload_json FROM dev_task WHERE id = ?",
            (int(remediate_task_id),),
        ).fetchone()
        if not trow:
            raise ValueError(f"unknown remediate_task_id: {remediate_task_id}")
        try:
            payload = json.loads(trow[1] or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if case_id is None and payload.get("fault_event_id") is not None:
            try:
                case_id = int(payload["fault_event_id"])
            except Exception:
                pass

    if case_id is None:
        raise ValueError("could not resolve case_id / fault_event_id")

    er = conn.execute(
        """
        SELECT event_id, fault_type, option_id, solution_id, status, task_id
        FROM fault_event WHERE event_id = ?
        """,
        (int(case_id),),
    ).fetchone()
    if not er:
        raise ValueError(f"unknown case_id/event_id: {case_id}")
    if hasattr(er, "keys"):
        fault_type = er["fault_type"]
        prefer_oid = er["option_id"]
        existing_sid = er["solution_id"]
        fault_status = er["status"]
    else:
        fault_type, prefer_oid, existing_sid, fault_status = er[1], er[2], er[3], er[4]

    fact_rows = conn.execute(
        """
        SELECT keyword, value_text, value_type, source
        FROM fault_event_fact WHERE event_id = ? ORDER BY id
        """,
        (int(case_id),),
    ).fetchall()
    facts: list[dict] = []
    for r in fact_rows:
        if hasattr(r, "keys"):
            facts.append(
                {
                    "keyword": r["keyword"],
                    "value_text": r["value_text"],
                    "value_type": r["value_type"],
                    "source": r["source"],
                }
            )
        else:
            facts.append(
                {
                    "keyword": r[0],
                    "value_text": r[1],
                    "value_type": r[2],
                    "source": r[3],
                }
            )

    # optional analysis summary for soft text
    if summary is None:
        ar = conn.execute(
            "SELECT summary FROM fault_analysis WHERE event_id = ? LIMIT 1",
            (int(case_id),),
        ).fetchone()
        if ar:
            summary = ar[0] if not hasattr(ar, "keys") else ar["summary"]

    catalog = load_fault_option_catalog(conn)
    matched = match_fault_option_ssot(
        options=catalog,
        facts=facts,
        fault_type=fault_type,
        summary=summary,
        prefer_option_id=int(prefer_oid) if prefer_oid is not None else None,
    )

    # attach ids for std rows
    for row in matched.get("std_rows") or []:
        row["case_id"] = int(case_id)
        if remediate_task_id is not None:
            row["remediate_task_id"] = int(remediate_task_id)
        if payload.get("qc_task_id") is not None:
            try:
                row["qc_task_id"] = int(payload["qc_task_id"])
            except Exception:
                pass
        if payload.get("table"):
            row["table_name"] = str(payload.get("table"))

    option_id = matched.get("option_id")
    solution_id = matched.get("solution_id")
    # keep existing solution if match weak/ambiguous without solution
    if solution_id is None and existing_sid is not None and matched["match_status"] in ("matched", "weak"):
        solution_id = int(existing_sid)
        matched["solution_id"] = solution_id

    wrote_event = False
    wrote_analysis = False
    if write_event and matched["match_status"] in ("matched", "weak") and option_id is not None:
        # set option/solution when missing or reinforcing same option
        sets = ["option_id = ?"]
        params: list[Any] = [int(option_id)]
        if solution_id is not None:
            sets.append("solution_id = ?")
            params.append(int(solution_id))
        params.append(int(case_id))
        conn.execute(
            f"UPDATE fault_event SET {', '.join(sets)} WHERE event_id = ?",
            tuple(params),
        )
        wrote_event = True

    if write_analysis:
        existing = conn.execute(
            "SELECT id FROM fault_analysis WHERE event_id = ? LIMIT 1",
            (int(case_id),),
        ).fetchone()
        prompt_json = json.dumps(matched.get("ssot_prompt") or {}, ensure_ascii=False)
        sol_sum = matched.get("solution_title") or matched.get("summary")
        if existing:
            aid = int(existing[0] if not hasattr(existing, "keys") else existing["id"])
            conn.execute(
                """
                UPDATE fault_analysis
                SET option_id = COALESCE(?, option_id),
                    solution_id = COALESCE(?, solution_id),
                    match_score = ?,
                    match_status = ?,
                    ssot_prompt_json = ?,
                    solution_summary = COALESCE(?, solution_summary)
                WHERE id = ?
                """,
                (
                    int(option_id) if option_id is not None else None,
                    int(solution_id) if solution_id is not None else None,
                    matched.get("match_score"),
                    matched.get("match_status"),
                    prompt_json,
                    sol_sum,
                    aid,
                ),
            )
            wrote_analysis = True
        else:
            # lightweight analysis row so match is durable without requiring vision bridge
            conn.execute(
                """
                INSERT INTO fault_analysis
                    (event_id, model, summary, detail_json, evidence_used,
                     option_id, solution_id, match_score, match_status,
                     ssot_prompt_json, solution_summary, created_at)
                VALUES (?, ?, ?, ?, 'none', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(case_id),
                    "fault_ssot_match",
                    matched.get("summary"),
                    json.dumps(
                        {
                            "fh6": matched.get("ssot_prompt"),
                            "candidates": [
                                {
                                    "option_id": c.get("option_id"),
                                    "option_code": c.get("option_code"),
                                    "score": c.get("score"),
                                }
                                for c in (matched.get("candidates") or [])[:5]
                            ],
                            "gate": "never",
                        },
                        ensure_ascii=False,
                    ),
                    int(option_id) if option_id is not None else None,
                    int(solution_id) if solution_id is not None else None,
                    matched.get("match_score"),
                    matched.get("match_status"),
                    prompt_json,
                    sol_sum,
                    datetime.datetime.now(),
                ),
            )
            wrote_analysis = True
            # best-effort link fault_analysis_id on event
            try:
                aid_row = conn.execute(
                    "SELECT id FROM fault_analysis WHERE event_id = ? LIMIT 1",
                    (int(case_id),),
                ).fetchone()
                if aid_row:
                    aid = int(aid_row[0] if not hasattr(aid_row, "keys") else aid_row["id"])
                    conn.execute(
                        """
                        UPDATE fault_event
                        SET fault_analysis_id = COALESCE(fault_analysis_id, ?)
                        WHERE event_id = ?
                        """,
                        (aid, int(case_id)),
                    )
            except sqlite3.Error:
                pass

    if write_facts:
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
                VALUES (?, ?, ?, ?, 'fail_handling')
                """,
                (int(case_id), keyword, text, value_type),
            )

        _fact("match_status", matched.get("match_status"))
        _fact("match_score", matched.get("match_score"), "number")
        _fact("match_option_id", matched.get("option_id"), "number")
        _fact("match_option_code", matched.get("option_code"))
        _fact("match_solution_id", matched.get("solution_id"), "number")
        _fact("match_summary", matched.get("summary"))

    if write_payload and remediate_task_id is not None:
        payload["step6_match_status"] = matched.get("match_status")
        payload["step6_match_score"] = matched.get("match_score")
        payload["step6_option_id"] = matched.get("option_id")
        payload["step6_option_code"] = matched.get("option_code")
        payload["step6_solution_id"] = matched.get("solution_id")
        payload["step6_std_rows"] = matched.get("std_rows") or []
        payload["step6_summary"] = matched.get("summary")
        payload["pipeline"] = payload.get("pipeline") or "B_fail_handling"
        conn.execute(
            """
            UPDATE dev_task
            SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(payload, ensure_ascii=False), int(remediate_task_id)),
        )

    if commit:
        conn.commit()

    out = dict(matched)
    out["case_id"] = int(case_id)
    out["remediate_task_id"] = int(remediate_task_id) if remediate_task_id is not None else None
    out["fault_type"] = fault_type
    out["fault_status"] = fault_status
    out["event_written"] = wrote_event
    out["analysis_written"] = wrote_analysis
    out["facts_written"] = bool(write_facts)
    out["payload_written"] = bool(write_payload and remediate_task_id is not None)
    # trim candidates hits for API size
    slim = []
    for c in out.get("candidates") or []:
        slim.append(
            {
                "option_id": c.get("option_id"),
                "option_code": c.get("option_code"),
                "option_name": c.get("option_name"),
                "score": c.get("score"),
                "solution_id": c.get("solution_id"),
                "hit_count": len(c.get("hits") or []),
            }
        )
    out["candidates"] = slim
    return out


# ---------------------------------------------------------------------------
# Phase 5 — Fail-Handling supervisor (orchestrate B steps; never a gate)
# ---------------------------------------------------------------------------

SUPERVISOR_STEPS_DEFAULT = ("assemble", "vision", "match", "report")

# Pipeline C enrollment names (must match code_health.V1_ENROLLED_SOURCES)
_SUPERVISE_MODULE = "fail_handling"
_SUPERVISE_FN = {
    "assemble": "supervise.assemble",
    "vision": "supervise.vision",
    "match": "supervise.match",
    "report": "supervise.report",
}


def _safe_step(
    name: str,
    fn,
    *args,
    invoker_conn=None,
    invoker_tacid: str | None = None,
    invoker_task_id: int | None = None,
    **kwargs,
) -> dict:
    """Run one supervisor step; never raise into caller trunk.

    CH5: enrolled steps go through code_health.function_invoker (W8) when available.
    Telemetry never gates; commit=False so outer supervisor transaction owns commit.
    """
    t0 = datetime.datetime.now()
    invoke_meta = None
    try:
        def _call():
            return fn(*args, **kwargs)

        result = None
        used_invoker = False
        if invoker_conn is not None:
            try:
                from code_health import function_invoker

                tacid = (invoker_tacid or "").strip() or "fh.supervise"
                inv = function_invoker(
                    _call,
                    tacid=tacid,
                    module_name=_SUPERVISE_MODULE,
                    function_name=_SUPERVISE_FN.get(name, f"supervise.{name}"),
                    task_id=invoker_task_id,
                    source=f"fail_handling.supervise.{name}",
                    conn=invoker_conn,
                    reraise=True,
                    commit=False,
                )
                result = inv.get("result")
                invoke_meta = {
                    "trace_id": (inv.get("invoke") or {}).get("trace_id"),
                    "duration_ms": (inv.get("invoke") or {}).get("duration_ms"),
                    "scoring_status": ((inv.get("invoke") or {}).get("scoring") or {}).get(
                        "status"
                    ),
                }
                used_invoker = True
            except ImportError:
                used_invoker = False
            except Exception:
                # Invoker path raised after recording; fall through to except below
                raise

        if not used_invoker:
            result = _call()

        ok = True
        err = None
        if isinstance(result, dict) and result.get("error") and not result.get("ok", True):
            ok = False
            err = str(result.get("error"))
        # vision may set step2_error without failing whole pipeline
        if isinstance(result, dict) and name == "vision" and result.get("step2_error"):
            err = str(result.get("step2_error"))
            # still ok for supervisor continuum
            ok = True
        out = {
            "step": name,
            "ok": ok,
            "error": err,
            "ms": int((datetime.datetime.now() - t0).total_seconds() * 1000),
            "result": result if isinstance(result, dict) else {"value": result},
            "invoker": used_invoker,
        }
        if invoke_meta:
            out["invoke"] = invoke_meta
        return out
    except Exception as e:
        return {
            "step": name,
            "ok": False,
            "error": f"{type(e).__name__}:{e}",
            "ms": int((datetime.datetime.now() - t0).total_seconds() * 1000),
            "result": None,
            "invoker": invoke_meta is not None,
            "invoke": invoke_meta,
        }


def resolve_supervisor_target(
    conn: sqlite3.Connection,
    *,
    task_id: int | None = None,
    case_id: int | None = None,
) -> dict:
    """Resolve remediate task + case from either id. Prefer schema.remediate."""
    if task_id is None and case_id is None:
        raise ValueError("task_id or case_id required")

    payload: dict = {}
    action_code = None
    task_label = None
    remediate_task_id = task_id
    resolved_case = case_id

    if remediate_task_id is not None:
        row = conn.execute(
            """
            SELECT t.id, t.task_label, t.payload_json, t.status, t.parent_task_id,
                   a.code AS action_code
            FROM dev_task t
            LEFT JOIN task_action_name a ON a.id = t.action_name_id
            WHERE t.id = ?
            """,
            (int(remediate_task_id),),
        ).fetchone()
        if not row:
            raise ValueError(f"unknown task_id: {remediate_task_id}")
        if hasattr(row, "keys"):
            task_label = row["task_label"]
            action_code = row["action_code"]
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
        else:
            # id, task_label, payload_json, status, parent_task_id, action_code
            task_label = row[1]
            try:
                payload = json.loads(row[2] or "{}")
            except Exception:
                payload = {}
            action_code = row[5] if len(row) > 5 else None
        if not isinstance(payload, dict):
            payload = {}

        # If user pointed at QC parent, prefer open remediate child
        if action_code == "qc.verify_schema":
            child = conn.execute(
                """
                SELECT t.id, t.task_label, t.payload_json, a.code
                FROM dev_task t
                JOIN task_action_name a ON a.id = t.action_name_id
                WHERE t.parent_task_id = ? AND a.code = 'schema.remediate'
                ORDER BY t.id DESC LIMIT 1
                """,
                (int(remediate_task_id),),
            ).fetchone()
            if child:
                remediate_task_id = int(child[0] if not hasattr(child, "keys") else child["id"])
                task_label = child[1] if not hasattr(child, "keys") else child["task_label"]
                action_code = "schema.remediate"
                try:
                    raw = child[2] if not hasattr(child, "keys") else child["payload_json"]
                    payload = json.loads(raw or "{}")
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}

        if resolved_case is None and payload.get("fault_event_id") is not None:
            try:
                resolved_case = int(payload["fault_event_id"])
            except Exception:
                pass

    if resolved_case is not None and remediate_task_id is None:
        fr = conn.execute(
            """
            SELECT value_text FROM fault_event_fact
            WHERE event_id = ? AND keyword = 'remediation_task_id'
            ORDER BY id DESC LIMIT 1
            """,
            (int(resolved_case),),
        ).fetchone()
        if fr and str(fr[0]).isdigit():
            remediate_task_id = int(fr[0])
            return resolve_supervisor_target(
                conn, task_id=remediate_task_id, case_id=int(resolved_case)
            )

    return {
        "remediate_task_id": int(remediate_task_id) if remediate_task_id is not None else None,
        "case_id": int(resolved_case) if resolved_case is not None else None,
        "task_label": task_label,
        "action_code": action_code,
        "payload": payload,
        "qc_task_id": payload.get("qc_task_id") or payload.get("parent_task_id"),
        "table": payload.get("table"),
        "gate": "never",
    }


def list_open_fail_handling_targets(conn: sqlite3.Connection) -> list[dict]:
    """Open schema.remediate tasks (and open schema_qc_fail events without task)."""
    rows = conn.execute(
        """
        SELECT t.id, t.task_label, t.status, t.payload_json, a.code AS action_code
        FROM dev_task t
        JOIN task_action_name a ON a.id = t.action_name_id
        WHERE a.code = 'schema.remediate'
          AND t.status IN ('pending', 'fail', 'running')
        ORDER BY t.id DESC
        """
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        if hasattr(r, "keys"):
            tid, label, status, raw, code = (
                int(r["id"]),
                r["task_label"],
                r["status"],
                r["payload_json"],
                r["action_code"],
            )
        else:
            tid, label, status, raw, code = int(r[0]), r[1], r[2], r[3], r[4]
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        case_id = payload.get("fault_event_id")
        try:
            case_id = int(case_id) if case_id is not None else None
        except Exception:
            case_id = None
        out.append(
            {
                "task_id": tid,
                "task_label": label,
                "status": status,
                "action_code": code,
                "case_id": case_id,
                "table": payload.get("table"),
                "pipeline": payload.get("pipeline"),
            }
        )
    return out


def run_fail_handling_supervisor(
    conn: sqlite3.Connection,
    *,
    task_id: int | None = None,
    case_id: int | None = None,
    steps: list[str] | tuple[str, ...] | None = None,
    call_ollama: bool = False,
    notify: bool = False,
    write: bool = True,
    commit: bool = True,
    vision_id: int | None = None,
    image_path: str | None = None,
) -> dict:
    """Orchestrate Fail-Handling B steps for one remediate case.

    Default order: assemble → vision(optional/offline) → match → report.
    Never flips structure gate / match_ok. Step failures are recorded; pipeline continues.
    """
    target = resolve_supervisor_target(conn, task_id=task_id, case_id=case_id)
    rid = target.get("remediate_task_id")
    cid = target.get("case_id")
    if rid is None and cid is None:
        raise ValueError("could not resolve remediate task or case")

    step_list = list(steps) if steps else list(SUPERVISOR_STEPS_DEFAULT)
    # normalize aliases
    norm: list[str] = []
    for s in step_list:
        k = str(s).strip().lower()
        if k in ("4", "fh3", "ssot", "step4"):
            k = "assemble"
        elif k in ("2", "fh4", "step2", "ollama"):
            k = "vision"
        elif k in ("6", "fh6", "ssot-match"):
            k = "match"
        elif k in ("5", "fh5", "step5"):
            k = "report"
        if k not in norm:
            norm.append(k)

    started = datetime.datetime.now().isoformat(timespec="seconds")
    run_log: list[dict] = []
    summaries: dict[str, Any] = {}

    write_kw = bool(write)
    invoker_tacid = str(target.get("task_label") or "").strip() or (
        f"fh.task.{rid}" if rid is not None else f"fh.case.{cid}"
    )
    invoker_task_id = int(rid) if rid is not None else None
    invoker_steps = 0

    def _log_step(out: dict) -> None:
        nonlocal invoker_steps
        entry = {k: out[k] for k in ("step", "ok", "error", "ms") if k in out}
        if out.get("invoker"):
            entry["invoker"] = True
            invoker_steps += 1
        if out.get("invoke"):
            entry["invoke"] = out["invoke"]
        run_log.append(entry)

    if "assemble" in norm:
        out = _safe_step(
            "assemble",
            run_fail_ssot_assemble,
            conn,
            remediate_task_id=rid,
            case_id=cid,
            write_payload=write_kw,
            write_facts=write_kw,
            commit=False,
            invoker_conn=conn,
            invoker_tacid=invoker_tacid,
            invoker_task_id=invoker_task_id,
        )
        _log_step(out)
        if isinstance(out.get("result"), dict):
            summaries["assemble"] = {
                "summary": out["result"].get("summary"),
                "counts": out["result"].get("counts"),
            }
            if cid is None and out["result"].get("case_id") is not None:
                try:
                    cid = int(out["result"]["case_id"])
                except Exception:
                    pass

    if "vision" in norm:
        out = _safe_step(
            "vision",
            run_fail_vision_step2,
            conn,
            remediate_task_id=rid,
            case_id=cid,
            vision_id=vision_id,
            image_path=image_path,
            write_payload=write_kw,
            write_facts=write_kw,
            commit=False,
            call_ollama=bool(call_ollama),
            invoker_conn=conn,
            invoker_tacid=invoker_tacid,
            invoker_task_id=invoker_task_id,
        )
        _log_step(out)
        if isinstance(out.get("result"), dict):
            summaries["vision"] = {
                "summary": out["result"].get("step2_summary"),
                "error": out["result"].get("step2_error"),
                "row_count": out["result"].get("row_count"),
                "vision_id": out["result"].get("vision_id"),
            }

    if "match" in norm:
        try:
            from db_schema import seed_ssot_defaults

            seed_ssot_defaults(conn)
        except Exception:
            pass
        out = _safe_step(
            "match",
            run_fault_ssot_match,
            conn,
            remediate_task_id=rid,
            case_id=cid,
            write_event=write_kw,
            write_analysis=write_kw,
            write_facts=write_kw,
            write_payload=write_kw,
            commit=False,
            invoker_conn=conn,
            invoker_tacid=invoker_tacid,
            invoker_task_id=invoker_task_id,
        )
        _log_step(out)
        if isinstance(out.get("result"), dict):
            summaries["match"] = {
                "match_status": out["result"].get("match_status"),
                "match_score": out["result"].get("match_score"),
                "option_code": out["result"].get("option_code"),
                "solution_id": out["result"].get("solution_id"),
            }
            if cid is None and out["result"].get("case_id") is not None:
                try:
                    cid = int(out["result"]["case_id"])
                except Exception:
                    pass

    if "report" in norm:
        out = _safe_step(
            "report",
            run_fail_report,
            conn,
            remediate_task_id=rid,
            case_id=cid,
            write_payload=write_kw,
            write_facts=write_kw,
            write_db=write_kw,
            notify=bool(notify) and write_kw,
            commit=False,
            invoker_conn=conn,
            invoker_tacid=invoker_tacid,
            invoker_task_id=invoker_task_id,
        )
        _log_step(out)
        if isinstance(out.get("result"), dict):
            summaries["report"] = {
                "report_id": out["result"].get("report_id"),
                "summary": out["result"].get("summary"),
            }

    ok_n = sum(1 for x in run_log if x.get("ok"))
    fail_n = sum(1 for x in run_log if not x.get("ok"))
    finished = datetime.datetime.now().isoformat(timespec="seconds")
    supervisor_summary = (
        f"Phase5 supervisor steps={len(run_log)} ok={ok_n} fail={fail_n} "
        f"task={rid} case={cid} ollama={bool(call_ollama)}"
    )

    payload_written = False
    if write_kw and rid is not None:
        try:
            trow = conn.execute(
                "SELECT payload_json FROM dev_task WHERE id = ?",
                (int(rid),),
            ).fetchone()
            payload: dict = {}
            if trow:
                try:
                    payload = json.loads(trow[0] or "{}")
                except Exception:
                    payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["supervisor_ran_at"] = finished
            payload["supervisor_started_at"] = started
            payload["supervisor_summary"] = supervisor_summary
            payload["supervisor_steps"] = run_log
            payload["supervisor_results"] = summaries
            payload["supervisor_call_ollama"] = bool(call_ollama)
            payload["pipeline"] = payload.get("pipeline") or "B_fail_handling"
            conn.execute(
                """
                UPDATE dev_task
                SET payload_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (json.dumps(payload, ensure_ascii=False), int(rid)),
            )
            payload_written = True
        except Exception:
            payload_written = False

        if cid is not None:
            try:
                conn.execute(
                    """
                    INSERT INTO fault_event_fact
                        (event_id, keyword, value_text, value_type, source)
                    VALUES (?, 'supervisor_summary', ?, 'string', 'fail_handling')
                    """,
                    (int(cid), supervisor_summary),
                )
                conn.execute(
                    """
                    INSERT INTO fault_event_fact
                        (event_id, keyword, value_text, value_type, source)
                    VALUES (?, 'supervisor_steps', ?, 'json', 'fail_handling')
                    """,
                    (int(cid), json.dumps(run_log, ensure_ascii=False)),
                )
            except Exception:
                pass

    if commit:
        conn.commit()

    return {
        "gate": "never",
        "pipeline": "B_fail_handling",
        "phase": 5,
        "ok": fail_n == 0,
        "summary": supervisor_summary,
        "started_at": started,
        "finished_at": finished,
        "remediate_task_id": rid,
        "case_id": cid,
        "task_label": target.get("task_label"),
        "action_code": target.get("action_code"),
        "table": target.get("table"),
        "steps_requested": norm,
        "steps_run": run_log,
        "results": summaries,
        "call_ollama": bool(call_ollama),
        "notify": bool(notify),
        "payload_written": payload_written,
        "write": write_kw,
        "code_health": {
            "gate": "never",
            "invoker_steps": invoker_steps,
            "tacid": invoker_tacid,
            "why": ["W8", "W2"],
        },
    }


def spawn_detached_fail_handling(
    argv: list[str],
    log_name: str | None = None,
) -> str | None:
    """Fire-and-forget fail_handling child (Windows detached). Returns log path."""
    import subprocess
    import sys

    base = Path(__file__).resolve().parent
    log_dir = base / "fault_evidence"
    log_dir.mkdir(parents=True, exist_ok=True)
    if log_name is None:
        log_name = f"fh_spawn_{int(datetime.datetime.now().timestamp() * 1000)}.log"
    log_path = log_dir / log_name
    env = os.environ.copy()
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    try:
        log_f = open(log_path, "a", encoding="utf-8")
        subprocess.Popen(
            argv,
            cwd=str(base),
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        return str(log_path)
    except Exception as e:
        try:
            log_path.write_text(f"spawn failed: {e}\n", encoding="utf-8")
        except Exception:
            pass
        return None


def spawn_pending_fail_handling(
    *,
    db_path: str | None = None,
    call_ollama: bool = False,
    notify: bool = False,
    steps: list[str] | None = None,
) -> dict:
    """Detached supervisor for each open schema.remediate task. Never raises."""
    import sys

    base = Path(__file__).resolve().parent
    db = db_path or str(base / "agent.db")
    conn = None
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        targets = list_open_fail_handling_targets(conn)
    except Exception as e:
        return {"ok": False, "error": str(e), "attempted": 0, "discovered": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    if not targets:
        return {
            "ok": True,
            "attempted": 0,
            "discovered": 0,
            "message": "no open schema.remediate tasks",
        }

    py = sys.executable
    script = str(base / "fail_handling.py")
    attempted = 0
    logs: list[dict] = []
    for t in targets:
        argv = [py, script, "supervise", "--task-id", str(t["task_id"]), "--db", db]
        if t.get("case_id") is not None:
            argv.extend(["--case-id", str(t["case_id"])])
        if call_ollama:
            argv.append("--ollama")
        if notify:
            argv.append("--notify")
        if steps:
            argv.extend(["--steps", ",".join(steps)])
        log_name = f"fh_spawn_{t.get('task_label') or t['task_id']}.log"
        try:
            log_path = spawn_detached_fail_handling(argv, log_name=log_name)
            attempted += 1
            logs.append({"task_id": t["task_id"], "log": log_path})
        except Exception as e:
            logs.append({"task_id": t["task_id"], "error": str(e)})
    return {
        "ok": True,
        "attempted": attempted,
        "discovered": len(targets),
        "call_ollama": call_ollama,
        "logs": logs,
        "gate": "never",
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: contracts | selftest | assemble | vision | report | match | supervise"""
    import argparse
    import os
    import sys

    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["contracts"]
    cmd = args[0].strip().lower()

    if cmd in ("contracts", "fh0", "doc"):
        print(json.dumps(contracts_doc(), ensure_ascii=False, indent=2))
        return 0

    if cmd in ("selftest", "test"):
        g = infer_schema_qc_goal(
            table="demo_x",
            diff={
                "missing_columns": ["updated_at"],
                "extra_columns": [],
                "summary": "missing",
            },
            expected=["id", "name", "updated_at"],
            observed={"column_names": ["id", "name"]},
        )
        assert g["goal_type"] == "fix_schema", g
        g2 = infer_schema_qc_goal(
            table="demo_x",
            diff={"missing_columns": [], "extra_columns": ["x"], "summary": "extra"},
            expected=["id"],
            observed={"column_names": ["id", "x"]},
        )
        assert g2["goal_type"] == "fix_expected", g2
        g3 = infer_schema_qc_goal(
            table="t",
            diff={
                "missing_columns": ["a"],
                "extra_columns": ["b"],
                "summary": "both",
            },
            expected=["a"],
            observed={"column_names": ["b"]},
        )
        assert g3["goal_type"] == "investigate", g3
        p = merge_goal_into_payload({"table": "demo_x"}, g)
        assert p["goal_type"] == "fix_schema" and p["goal_text"]
        rows = pragma_diff_std_rows(
            case_id=1,
            qc_task_id=2,
            remediate_task_id=3,
            table="demo_x",
            diff={"missing_columns": ["updated_at"], "extra_columns": []},
            expected=["id", "updated_at"],
            observed={"column_names": ["id"]},
        )
        assert any(r["op"] == "add" and "updated_at" in r["dim_key"] for r in rows)

        # FH3 pure assemble
        assembled = assemble_fail_ssot(
            goal=g,
            case_id=1,
            qc_task_id=2,
            remediate_task_id=3,
            table_name="demo_x",
            step1_std_rows=rows,
            task_ssot=[],
            schema_ssot=[],
        )
        assert assembled["counts"]["add"] >= 1, assembled
        assert any("DDL: ADD" in s for s in assembled["plan_steps"]), assembled["plan_steps"]
        assert assembled["gate"] == "never"

        cap = assemble_fail_ssot(
            goal={
                "goal_type": "implement_capability",
                "goal_text": "open browser",
                "goal_context": {},
            },
            remediate_task_id=10,
            task_ssot=[
                {"dim_key": "tool.capability.open_browser", "value_text": "false"},
                {"dim_key": "tool.capability.open_browser.desired", "value_text": "true"},
                {"dim_key": "fallback.open_browser", "value_text": "webbrowser"},
            ],
        )
        assert any(
            d.get("dim_key") == "tool.capability.open_browser" and d.get("op") == "update"
            for d in cap["step4_delta_rows"]
        ), cap

        # FH4 pure vision → std rows (no Ollama required)
        vrows = vision_detail_to_std_rows(
            detail={
                "severity": "medium",
                "ui_state": "db_browser table vision_asset",
                "table_visible": "vision_asset",
                "column_headers": ["id", "kind", "path_or_url"],
                "likely_cause": "schema drift visible in UI",
                "recommended_human_action": "fix DDL or expected",
                "confidence": 0.7,
                "notes": "selftest",
            },
            summary="[medium] selftest",
            model="selftest",
            case_id=1,
            qc_task_id=2,
            remediate_task_id=3,
            table_name="vision_asset",
            vision_id=3,
        )
        assert any(r["dim_key"] == "vision.step2_ran" for r in vrows), vrows
        assert any(r["dim_key"] == "vision.column_headers_json" for r in vrows), vrows
        assert all(r.get("op") == "keep" for r in vrows), vrows

        # FH5 pure report rollup
        rep = build_fail_report(
            case_id=1,
            qc_task_id=2,
            remediate_task_id=3,
            table_name="demo_x",
            gate_result="fail",
            goal=g,
            step1_std_rows=rows,
            step2_std_rows=vrows,
            step2_summary="[medium] selftest",
            step4_delta_rows=assembled["step4_delta_rows"],
            step4_summary=assembled["summary"],
            step4_counts=assembled["counts"],
            plan_steps=assembled["plan_steps"],
            vision_id=3,
        )
        assert rep["gate"] == "never", rep
        assert any(r["dim_key"] == "report.step5_ran" for r in rep["step5_std_rows"]), rep
        assert "Fail-Handling Report" in (rep.get("markdown_text") or "")
        assert rep.get("top_deltas")
        assert rep.get("links", {}).get("remediate_task_id") == 3

        # FH6 pure option SSOT match
        qc_opt = {
            "id": 2,
            "code": "schema_qc_fail",
            "name": "Schema QC hard gate fail",
            "solution_id": 9,
            "solution_title": "Remediate schema QC hard fail",
            "ssot": [
                {"keyword": "fault_type", "value_text": "schema_qc_fail", "weight": 1.0},
                {"keyword": "legacy_fault_types", "value_text": "schema_qc_fail", "weight": 0.95},
                {"keyword": "gate", "value_text": "pragma_exact_set", "weight": 1.0},
                {"keyword": "match_ok", "value_text": "false", "weight": 1.0},
                {
                    "keyword": "fact_keys",
                    "value_text": "table,missing_columns,extra_columns,qc_task_id,remediation_task_id",
                    "weight": 0.9,
                },
            ],
        }
        hb_opt = {
            "id": 1,
            "code": "heartbeat_off",
            "name": "heartbeat OFF",
            "solution_id": 1,
            "solution_title": "Restore host + Ollama + worker heartbeat",
            "ssot": [
                {"keyword": "fault_type", "value_text": "heartbeat_timeout", "weight": 1.0},
                {
                    "keyword": "legacy_fault_types",
                    "value_text": "heartbeat_timeout,crash",
                    "weight": 0.95,
                },
                {
                    "keyword": "fact_keys",
                    "value_text": "heartbeat_last_seen_at,stale_minutes,worker_id",
                    "weight": 0.8,
                },
                {
                    "keyword": "signal",
                    "value_text": "workers.last_seen_at stale beyond threshold",
                    "weight": 1.0,
                },
            ],
        }
        m_qc = match_fault_option_ssot(
            options=[hb_opt, qc_opt],
            facts={
                "fault_type": "schema_qc_fail",
                "gate": "pragma_exact_set",
                "match_ok": "false",
                "table": "vision_asset",
                "missing_columns": '["fh3_probe_col"]',
                "extra_columns": "[]",
                "qc_task_id": "3",
                "remediation_task_id": "11",
                "goal_type": "fix_schema",
            },
            fault_type="schema_qc_fail",
        )
        assert m_qc["gate"] == "never", m_qc
        assert m_qc["match_status"] == "matched", m_qc
        assert m_qc["option_code"] == "schema_qc_fail", m_qc
        assert float(m_qc["match_score"] or 0) >= MATCH_SCORE_MATCHED, m_qc
        assert m_qc.get("solution_id") == 9, m_qc

        m_hb = match_fault_option_ssot(
            options=[hb_opt, qc_opt],
            facts={
                "fault_type": "heartbeat_timeout",
                "stale_minutes": "45",
                "heartbeat_last_seen_at": "2020-01-01T00:00:00",
                "worker_id": "1",
                "watchdog_message": "worker stale / offline heartbeat",
            },
            fault_type="heartbeat_timeout",
            summary="host shutdown worker dead no heartbeat",
        )
        assert m_hb["match_status"] in ("matched", "weak"), m_hb
        assert m_hb["option_code"] == "heartbeat_off", m_hb
        assert float(m_hb["match_score"] or 0) >= MATCH_SCORE_WEAK, m_hb

        m_empty = match_fault_option_ssot(
            options=[hb_opt, qc_opt],
            facts={"note": "unrelated"},
            fault_type="something_else",
        )
        assert m_empty["match_status"] in ("unmatched", "weak", "ambiguous"), m_empty
        assert m_empty["gate"] == "never"

        # Phase5 supervisor pure contracts
        assert "phase5" in contracts_doc()
        assert contracts_doc()["phase5"]["not_gate"] is True
        assert "assemble" in SUPERVISOR_STEPS_DEFAULT

        print(
            "selftest OK",
            {
                "goal_types": list(GOAL_TYPES),
                "rows": len(rows),
                "fh3_add": assembled["counts"]["add"],
                "fh3_cap_update": cap["counts"]["update"],
                "fh4_vision_rows": len(vrows),
                "fh5_report_rows": len(rep["step5_std_rows"]),
                "fh5_top_deltas": len(rep["top_deltas"]),
                "fh6_qc_score": m_qc["match_score"],
                "fh6_hb_score": m_hb["match_score"],
                "fh6_empty": m_empty["match_status"],
                "phase5_steps": list(SUPERVISOR_STEPS_DEFAULT),
            },
        )
        return 0

    if cmd in ("assemble", "fh3"):
        ap = argparse.ArgumentParser(prog="fail_handling.py assemble")
        ap.add_argument("--task-id", type=int, default=None)
        ap.add_argument("--case-id", type=int, default=None)
        ap.add_argument("--db", default=None)
        ap.add_argument("--dry-run", action="store_true")
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            result = run_fail_ssot_assemble(
                conn,
                remediate_task_id=ns.task_id,
                case_id=ns.case_id,
                write_payload=not ns.dry_run,
                write_facts=not ns.dry_run,
                commit=not ns.dry_run,
            )
        finally:
            conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if cmd in ("vision", "fh4", "step2"):
        ap = argparse.ArgumentParser(prog="fail_handling.py vision")
        ap.add_argument("--task-id", type=int, default=None)
        ap.add_argument("--case-id", type=int, default=None)
        ap.add_argument("--vision-id", type=int, default=None)
        ap.add_argument("--image", default=None, help="PNG path override")
        ap.add_argument("--db", default=None)
        ap.add_argument("--dry-run", action="store_true")
        ap.add_argument(
            "--no-ollama",
            action="store_true",
            help="Skip Ollama call (offline / resolve-only)",
        )
        ap.add_argument("--timeout", type=float, default=None)
        ap.add_argument("--model", default=None)
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            result = run_fail_vision_step2(
                conn,
                remediate_task_id=ns.task_id,
                case_id=ns.case_id,
                vision_id=ns.vision_id,
                image_path=ns.image,
                write_payload=not ns.dry_run,
                write_facts=not ns.dry_run,
                commit=not ns.dry_run,
                call_ollama=not ns.no_ollama,
                model=ns.model,
                timeout=ns.timeout,
            )
        finally:
            conn.close()
        out = dict(result)
        if isinstance(out.get("step2_std_rows"), list) and len(out["step2_std_rows"]) > 20:
            out["step2_std_rows"] = out["step2_std_rows"][:20]
            out["step2_std_rows_truncated"] = True
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.get("step2_error") else 1

    if cmd in ("report", "fh5", "step5"):
        ap = argparse.ArgumentParser(prog="fail_handling.py report")
        ap.add_argument("--task-id", type=int, default=None)
        ap.add_argument("--case-id", type=int, default=None)
        ap.add_argument("--db", default=None)
        ap.add_argument("--dry-run", action="store_true")
        ap.add_argument(
            "--notify",
            action="store_true",
            help="Optional MCP system.notify of report summary (non-gate)",
        )
        ap.add_argument(
            "--no-db",
            action="store_true",
            help="Skip fault_report table insert",
        )
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            result = run_fail_report(
                conn,
                remediate_task_id=ns.task_id,
                case_id=ns.case_id,
                write_payload=not ns.dry_run,
                write_facts=not ns.dry_run,
                write_db=not ns.dry_run and not ns.no_db,
                notify=bool(ns.notify) and not ns.dry_run,
                commit=not ns.dry_run,
            )
        finally:
            conn.close()
        out = dict(result)
        if out.get("markdown_text") and len(str(out["markdown_text"])) > 4000:
            out["markdown_text"] = str(out["markdown_text"])[:4000] + "\n...(truncated)"
        if isinstance(out.get("step5_std_rows"), list) and len(out["step5_std_rows"]) > 30:
            out["step5_std_rows"] = out["step5_std_rows"][:30]
            out["step5_std_rows_truncated"] = True
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0

    if cmd in ("match", "fh6", "ssot-match"):
        ap = argparse.ArgumentParser(prog="fail_handling.py match")
        ap.add_argument("--task-id", type=int, default=None)
        ap.add_argument("--case-id", type=int, default=None)
        ap.add_argument("--db", default=None)
        ap.add_argument("--dry-run", action="store_true")
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            try:
                from db_schema import seed_ssot_defaults

                seed_ssot_defaults(conn)
                if not ns.dry_run:
                    conn.commit()
            except Exception:
                pass
            result = run_fault_ssot_match(
                conn,
                remediate_task_id=ns.task_id,
                case_id=ns.case_id,
                write_event=not ns.dry_run,
                write_analysis=not ns.dry_run,
                write_facts=not ns.dry_run,
                write_payload=not ns.dry_run,
                commit=not ns.dry_run,
            )
        finally:
            conn.close()
        out = dict(result)
    if cmd in ("supervise", "supervisor", "phase5", "fh-run", "run"):
        ap = argparse.ArgumentParser(prog="fail_handling.py supervise")
        ap.add_argument("--task-id", type=int, default=None)
        ap.add_argument("--case-id", type=int, default=None)
        ap.add_argument("--db", default=None)
        ap.add_argument("--dry-run", action="store_true")
        ap.add_argument(
            "--ollama",
            action="store_true",
            help="Call Ollama for STEP2 (default: resolve-only / skip heavy vision)",
        )
        ap.add_argument("--no-vision", action="store_true", help="Skip vision step")
        ap.add_argument("--notify", action="store_true")
        ap.add_argument(
            "--steps",
            default=None,
            help="Comma list: assemble,vision,match,report (default all)",
        )
        ap.add_argument("--vision-id", type=int, default=None)
        ap.add_argument("--image", default=None)
        ap.add_argument(
            "--spawn-pending",
            action="store_true",
            help="Detached supervise all open schema.remediate tasks",
        )
        ns = ap.parse_args(args[1:])
        db = ns.db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent.db")

        if ns.spawn_pending:
            step_list = None
            if ns.steps:
                step_list = [s.strip() for s in ns.steps.split(",") if s.strip()]
            if ns.no_vision:
                step_list = step_list or list(SUPERVISOR_STEPS_DEFAULT)
                step_list = [s for s in step_list if s != "vision"]
            result = spawn_pending_fail_handling(
                db_path=db,
                call_ollama=bool(ns.ollama),
                notify=bool(ns.notify),
                steps=step_list,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0 if result.get("ok") else 1

        step_list = None
        if ns.steps:
            step_list = [s.strip() for s in ns.steps.split(",") if s.strip()]
        if ns.no_vision:
            step_list = step_list or list(SUPERVISOR_STEPS_DEFAULT)
            step_list = [s for s in step_list if s != "vision"]

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            result = run_fail_handling_supervisor(
                conn,
                task_id=ns.task_id,
                case_id=ns.case_id,
                steps=step_list,
                call_ollama=bool(ns.ollama),
                notify=bool(ns.notify) and not ns.dry_run,
                write=not ns.dry_run,
                commit=not ns.dry_run,
                vision_id=ns.vision_id,
                image_path=ns.image,
            )
        finally:
            conn.close()
        out = dict(result)
        # keep CLI light
        if isinstance(out.get("steps_run"), list):
            pass
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("ok") else 1

    print(
        "usage: fail_handling.py [contracts|selftest|"
        "assemble --task-id N|--case-id N|"
        "vision --task-id N|--case-id N|--vision-id N|--image PATH|"
        "report --task-id N|--case-id N [--notify]|"
        "match --task-id N|--case-id N|"
        "supervise --task-id N|--case-id N [--ollama] [--spawn-pending]]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
