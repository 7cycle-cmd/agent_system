"""
DB-driven validate_task_queue TDD runner (tc.4.*).

Cases come ONLY from test_case_registry.
Does NOT call validate_new_task for unit enqueue/poll/status
(kicker integration is separate: run_task_queue_kicker.py).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.task_center.ontology_store import (
    REQUIRED_TASK_QUEUE_TEST_TAGS,
    enqueue_task,
    get_task_queue_item,
    get_test_case,
    poll_next_task,
    seed_ontology_registry_defaults,
    seed_task_queue_test_cases,
    update_task_status,
)

BASE_DIR = Path(__file__).resolve().parent
REPORT_PATH = BASE_DIR / "qc_evidence" / "task_queue_run.json"


def _errors_text(actual: dict) -> str:
    parts: list[str] = []
    for k in ("errors", "error", "message", "reasons"):
        v = actual.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        else:
            parts.append(str(v))
    parts.append(json.dumps(actual, ensure_ascii=False, default=str))
    return " ".join(parts).lower()


def _run_enqueue(payload: dict) -> dict:
    body = payload.get("payload") or {}
    priority = payload.get("priority", 100)
    asserts = payload.get("assert") or {}
    errors: list[str] = []

    res = enqueue_task(body if isinstance(body, dict) else {"raw": body}, priority=priority)
    if not res.get("ok"):
        return {
            "ok": False,
            "errors": [str(res.get("error") or "enqueue failed")],
            "enqueue": res,
        }

    row = res.get("row") or {}
    tid = res.get("task_id")
    exp_status = asserts.get("status")
    if exp_status and str(row.get("status") or "") != str(exp_status):
        errors.append(
            f"status expected={exp_status!r} actual={row.get('status')!r}"
        )
    if "priority" in asserts:
        try:
            exp_pri = int(asserts["priority"])
        except (TypeError, ValueError):
            exp_pri = asserts["priority"]
        if row.get("priority") != exp_pri:
            errors.append(
                f"priority expected={exp_pri!r} actual={row.get('priority')!r}"
            )
    if not tid:
        errors.append("task_id missing")
    if not isinstance(row.get("payload"), dict) and body:
        errors.append("payload not deserialized as object")

    got = get_task_queue_item(str(tid or ""))
    if not got:
        errors.append("get_task_queue_item returned None after enqueue")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "task_id": tid,
        "status": row.get("status"),
        "priority": row.get("priority"),
    }


def _run_poll_claim(payload: dict) -> dict:
    body = payload.get("payload") or {}
    priority = payload.get("priority", 100)
    asserts = payload.get("assert") or {}
    errors: list[str] = []

    # Isolate: enqueue one high-priority unique task then claim it.
    # Drain is not performed; we use unique task_id and match on return.
    enq = enqueue_task(
        body if isinstance(body, dict) else {"raw": body},
        priority=priority,
    )
    if not enq.get("ok"):
        return {
            "ok": False,
            "errors": [f"setup enqueue failed: {enq.get('error')}"],
            "enqueue": enq,
        }
    expected_id = str(enq.get("task_id") or "")

    # Claim until we get our task or exhaust a safety bound of polls
    claimed = None
    extras: list[str] = []
    for _ in range(50):
        row = poll_next_task()
        if row is None:
            break
        rid = str(row.get("task_id") or "")
        if rid == expected_id:
            claimed = row
            break
        extras.append(rid)
        # finish stray claimed tasks so they do not stick running forever
        update_task_status(rid, "failed", verdict="FAIL", trace_id=None)

    if claimed is None:
        return {
            "ok": False,
            "errors": [
                f"did not claim expected task_id={expected_id}; "
                f"extras_claimed={extras[:5]}"
            ],
            "expected_task_id": expected_id,
        }

    exp_st = asserts.get("status_after_poll") or "running"
    if str(claimed.get("status") or "") != str(exp_st):
        errors.append(
            f"status_after_poll expected={exp_st!r} actual={claimed.get('status')!r}"
        )
    if not claimed.get("started_at"):
        errors.append("started_at not set after poll")

    if asserts.get("second_poll_empty"):
        # Ensure our task is no longer pending; may still claim other pending.
        again = get_task_queue_item(expected_id)
        if again and str(again.get("status") or "") == "pending":
            errors.append("task still pending after claim")
        # Re-poll should not return the same id
        nxt = poll_next_task()
        if nxt is not None and str(nxt.get("task_id") or "") == expected_id:
            errors.append("second poll returned same task_id")
        if nxt is not None:
            update_task_status(
                str(nxt.get("task_id")), "failed", verdict="FAIL", trace_id=None
            )

    # Mark our claimed task done for cleanliness
    update_task_status(expected_id, "done", verdict="PASS", trace_id="atr_tq_poll_tdd")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "task_id": expected_id,
        "status": claimed.get("status"),
        "started_at": claimed.get("started_at"),
        "extras_drained": len(extras),
    }


def _run_update_status(payload: dict) -> dict:
    body = payload.get("payload") or {}
    priority = payload.get("priority", 100)
    status = str(payload.get("status") or "done")
    verdict = payload.get("verdict")
    trace_id = payload.get("trace_id")
    asserts = payload.get("assert") or {}
    errors: list[str] = []

    enq = enqueue_task(
        body if isinstance(body, dict) else {"raw": body},
        priority=priority,
    )
    if not enq.get("ok"):
        return {
            "ok": False,
            "errors": [f"setup enqueue failed: {enq.get('error')}"],
            "enqueue": enq,
        }
    tid = str(enq.get("task_id") or "")

    # Move through running then terminal (realistic kicker path)
    poll_next_task()  # may claim another; force status path via update
    upd = update_task_status(
        tid,
        status,
        verdict=str(verdict) if verdict is not None else None,
        trace_id=str(trace_id) if trace_id is not None else None,
    )
    if not upd.get("ok"):
        return {
            "ok": False,
            "errors": [str(upd.get("error") or "update failed")],
            "update": upd,
        }

    row = upd.get("row") or get_task_queue_item(tid) or {}
    if asserts.get("status") and str(row.get("status") or "") != str(asserts["status"]):
        errors.append(
            f"status expected={asserts['status']!r} actual={row.get('status')!r}"
        )
    if asserts.get("verdict") and str(row.get("verdict") or "") != str(asserts["verdict"]):
        errors.append(
            f"verdict expected={asserts['verdict']!r} actual={row.get('verdict')!r}"
        )
    if asserts.get("trace_id") and str(row.get("trace_id") or "") != str(
        asserts["trace_id"]
    ):
        errors.append(
            f"trace_id expected={asserts['trace_id']!r} actual={row.get('trace_id')!r}"
        )
    if asserts.get("finished_at_set") and not row.get("finished_at"):
        errors.append("finished_at not set on terminal status")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "task_id": tid,
        "status": row.get("status"),
        "verdict": row.get("verdict"),
        "trace_id": row.get("trace_id"),
        "finished_at": row.get("finished_at"),
    }


def _execute_case(case: dict) -> dict:
    payload = case.get("input_payload") or {}
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["input_payload must be object"]}
    action = str(payload.get("action") or "").strip()
    if action == "enqueue":
        return _run_enqueue(payload)
    if action == "poll_claim":
        return _run_poll_claim(payload)
    if action == "update_status":
        return _run_update_status(payload)
    return {"ok": False, "errors": [f"unknown action: {action}"]}


def run() -> dict:
    seed_ontology_registry_defaults()
    seed_task_queue_test_cases()

    cases: list[dict] = []
    missing: list[str] = []
    for tag in REQUIRED_TASK_QUEUE_TEST_TAGS:
        c = get_test_case(tag)
        if not c or not c.get("is_active"):
            missing.append(tag)
        else:
            cases.append(c)

    started = datetime.now(timezone.utc).isoformat()
    results: list[dict] = []
    halted = None

    if missing:
        report = {
            "ok": False,
            "result": "FAIL",
            "reason": "required task_queue test cases missing",
            "missing_required": missing,
            "results": [],
            "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "report_path": str(REPORT_PATH),
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    for case in cases:
        tag = case.get("case_ref_tag")
        title = case.get("case_title")
        exp_ok = bool(case.get("expected_ok"))
        exp_errs = case.get("expected_errors") or []
        if not isinstance(exp_errs, list):
            exp_errs = [str(exp_errs)]

        actual = _execute_case(case)
        act_ok = bool(actual.get("ok"))
        err_text = _errors_text(actual if isinstance(actual, dict) else {"raw": actual})

        reasons: list[str] = []
        passed = True
        if act_ok != exp_ok:
            passed = False
            reasons.append(f"ok mismatch expected={exp_ok} actual={act_ok}")
        for needle in exp_errs:
            n = str(needle).lower()
            if n and n not in err_text:
                passed = False
                reasons.append(f"expected_errors missing substring {needle!r}")
        if actual.get("errors") and exp_ok and not act_ok:
            reasons.extend(str(x) for x in (actual.get("errors") or []))

        row = {
            "case_ref_tag": tag,
            "case_title": title,
            "result": "PASS" if passed else "FAIL",
            "expected_ok": exp_ok,
            "actual_ok": act_ok,
            "expected_errors": exp_errs,
            "actual_errors": actual.get("errors"),
            "reasons": reasons,
            "detail": {
                k: actual.get(k)
                for k in (
                    "task_id",
                    "status",
                    "priority",
                    "verdict",
                    "trace_id",
                    "finished_at",
                    "started_at",
                    "extras_drained",
                )
                if k in actual
            },
        }
        results.append(row)
        print(
            f"{row['result']:4} {tag} {title} "
            f"{reasons if reasons else ''}".rstrip()
        )
        if not passed:
            halted = tag
            break

    all_pass = (
        all(r["result"] == "PASS" for r in results)
        and not missing
        and halted is None
        and len(results) == len(REQUIRED_TASK_QUEUE_TEST_TAGS)
    )
    report = {
        "ok": all_pass,
        "result": "PASS" if all_pass else "FAIL",
        "halted_at": halted,
        "executed_count": len(results),
        "pass_count": sum(1 for r in results if r["result"] == "PASS"),
        "fail_count": sum(1 for r in results if r["result"] == "FAIL"),
        "required_tags": list(REQUIRED_TASK_QUEUE_TEST_TAGS),
        "results": results,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_case_registry",
        "queue_table": "validate_task_queue",
        "note": (
            "tc.4.* task_queue unit suite; table=validate_task_queue "
            "(not legacy task_queue); validate_new_task not invoked here"
        ),
        "report_path": str(REPORT_PATH),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run()
    print("=== SUMMARY ===")
    print(
        json.dumps(
            {
                "result": report.get("result"),
                "pass_count": report.get("pass_count"),
                "fail_count": report.get("fail_count"),
                "executed_count": report.get("executed_count"),
                "halted_at": report.get("halted_at"),
                "report_path": report.get("report_path"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
