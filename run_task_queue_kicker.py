"""
Kicker poller for validate_task_queue.

Flow (validate_new_task remains read-only):
  1. poll_next_task() — claim pending → running
  2. validate_new_task(payload)
  3. insert_audit_trace(...) — runner write; link task_id
  4. update_task_status(done|failed, verdict, trace_id)

Never mutates validate_new_task. Never writes from inside validate.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.task_center.ontology_store import (
    enqueue_task,
    ensure_ontology_registry_schema,
    get_task_queue_item,
    insert_audit_trace,
    poll_next_task,
    seed_ontology_registry_defaults,
    update_task_status,
)
from src.task_center.skill_task_validate import validate_new_task

BASE_DIR = Path(__file__).resolve().parent
REPORT_PATH = BASE_DIR / "qc_evidence" / "task_queue_kicker_run.json"

CANON_PAYLOAD = {
    "task": "T-SKILL01 — Register Task Format Validator Skill",
    "channel": "local_pc",
    "module": "task_center",
    "capability": "task_center.validate_new_task",
    "api": "POST /api/tasks/validate",
    "function": "task_center.validate_new_task",
    "table": "code_registry",
    "field": "register_id, module_name, function_name, file_path",
}


def _kick_one(task_row: dict) -> dict:
    tid = str(task_row.get("task_id") or "")
    payload = task_row.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {"raw": payload}

    actual = validate_new_task(payload)
    act_ok = bool(actual.get("ok")) if isinstance(actual, dict) else False
    dims = actual.get("dimensions") if isinstance(actual, dict) else None
    if not isinstance(dims, dict):
        dims = payload

    errors = []
    if isinstance(actual, dict):
        errors = actual.get("errors") or []
        if not isinstance(errors, list):
            errors = [str(errors)]

    audit = insert_audit_trace(
        {
            "task_id": tid,
            "channel": dims.get("channel") or payload.get("channel"),
            "module": dims.get("module") or payload.get("module"),
            "capability": dims.get("capability") or payload.get("capability"),
            "input_payload": payload,
            "verdict": act_ok,
            "errors": errors,
            "test_case_ref": None,
            "notes": "kicker=run_task_queue_kicker; validate_new_task read-only",
        }
    )
    trace_id = audit.get("trace_id") if isinstance(audit, dict) else None
    audit_ok = bool(isinstance(audit, dict) and audit.get("ok") and trace_id)

    status = "done" if act_ok else "failed"
    verdict = "PASS" if act_ok else "FAIL"
    upd = update_task_status(
        tid,
        status,
        verdict=verdict,
        trace_id=str(trace_id) if trace_id else None,
    )
    final = get_task_queue_item(tid)

    return {
        "ok": audit_ok and bool(upd.get("ok")) and final is not None,
        "task_id": tid,
        "validate_ok": act_ok,
        "validate_errors": errors,
        "audit_ok": audit_ok,
        "trace_id": trace_id,
        "update_ok": bool(upd.get("ok")),
        "final_status": (final or {}).get("status"),
        "final_verdict": (final or {}).get("verdict"),
        "final_trace_id": (final or {}).get("trace_id"),
        "link_ok": bool(
            final
            and final.get("trace_id")
            and trace_id
            and str(final.get("trace_id")) == str(trace_id)
        ),
    }


def run(max_tasks: int = 1, *, seed_demo: bool = True) -> dict:
    ensure_ontology_registry_schema()
    seed_ontology_registry_defaults()

    seeded_id = None
    if seed_demo:
        enq = enqueue_task(
            {**CANON_PAYLOAD, "task": "T-KICKER — validate_task_queue demo"},
            priority=1,
        )
        if enq.get("ok"):
            seeded_id = enq.get("task_id")

    started = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    for _ in range(max(1, int(max_tasks))):
        row = poll_next_task()
        if row is None:
            break
        results.append(_kick_one(row))

    all_ok = bool(results) and all(r.get("ok") and r.get("link_ok") for r in results)
    report = {
        "ok": all_ok,
        "result": "PASS" if all_ok else "FAIL",
        "seeded_task_id": seeded_id,
        "processed": len(results),
        "results": results,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "queue_table": "validate_task_queue",
        "note": (
            "poll → validate_new_task (read-only) → audit_trace → "
            "update_task_status; legacy task_queue untouched"
        ),
        "report_path": str(REPORT_PATH),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run(max_tasks=1, seed_demo=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
