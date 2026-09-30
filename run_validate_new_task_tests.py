"""
DB-driven validate_new_task TDD runner.

Hard rule: test cases come ONLY from test_case_registry via
get_all_active_test_cases() — no hardcoded cases=[...] payloads here.

Audit: after every validate_new_task call, runner writes audit_trace
(validate_new_task itself remains read-only).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.task_center.ontology_store import (
    REQUIRED_VALIDATE_TEST_TAGS,
    get_all_active_test_cases,
    insert_audit_trace,
    list_audit_by_test_case,
    list_recent_audit,
    seed_ontology_registry_defaults,
    seed_validate_new_task_test_cases,
)
from src.task_center.skill_task_validate import validate_new_task

BASE_DIR = Path(__file__).resolve().parent
REPORT_PATH = BASE_DIR / "qc_evidence" / "test_case_registry_run.json"


def _errors_text(actual: dict) -> str:
    parts: list[str] = []
    for k in ("errors", "error", "message", "ERROR_LIST"):
        v = actual.get(k)
        if v is None:
            continue
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        else:
            parts.append(str(v))
    parts.append(json.dumps(actual, ensure_ascii=False, default=str))
    return " ".join(parts).lower()


def _write_audit_for_case(
    *,
    tag: str,
    title: str,
    payload: dict,
    actual: dict,
    act_ok: bool,
    tdd_passed: bool,
    exp_ok: bool,
) -> dict:
    dims = actual.get("dimensions") if isinstance(actual, dict) else None
    if not isinstance(dims, dict):
        dims = payload if isinstance(payload, dict) else {}
    task_id = ""
    if isinstance(payload, dict):
        task_id = str(payload.get("task") or payload.get("task_id") or "").strip()
    if not task_id:
        task_id = str(tag or "")
    errors = []
    if isinstance(actual, dict):
        errors = actual.get("errors") or []
        if not isinstance(errors, list):
            errors = [str(errors)]
    rec = {
        "task_id": task_id,
        "channel": (dims.get("channel") if isinstance(dims, dict) else None)
        or (payload.get("channel") if isinstance(payload, dict) else None),
        "module": (dims.get("module") if isinstance(dims, dict) else None)
        or (payload.get("module") if isinstance(payload, dict) else None),
        "capability": (dims.get("capability") if isinstance(dims, dict) else None)
        or (payload.get("capability") if isinstance(payload, dict) else None),
        "input_payload": payload if isinstance(payload, dict) else {"raw": payload},
        "verdict": act_ok,
        "errors": errors,
        "test_case_ref": tag,
        "notes": (
            f"tdd_result={'PASS' if tdd_passed else 'FAIL'}; "
            f"expected_ok={exp_ok}; case_title={title}"
        ),
    }
    return insert_audit_trace(rec)


def run() -> dict:
    # Ensure SSOT present (migrate/seed write path — not validate)
    seed_ontology_registry_defaults()
    seed_validate_new_task_test_cases()

    all_active = get_all_active_test_cases()
    active_tags = [c.get("case_ref_tag") for c in all_active]
    # Only execute validate suite tags — ignore tc.2.* revision cases here
    required_set = set(REQUIRED_VALIDATE_TEST_TAGS)
    cases = [c for c in all_active if c.get("case_ref_tag") in required_set]
    # Stable order matching REQUIRED_VALIDATE_TEST_TAGS
    order = {t: i for i, t in enumerate(REQUIRED_VALIDATE_TEST_TAGS)}
    cases.sort(key=lambda c: order.get(c.get("case_ref_tag"), 999))
    tags = [c.get("case_ref_tag") for c in cases]
    missing_required = [t for t in REQUIRED_VALIDATE_TEST_TAGS if t not in active_tags]

    results: list[dict] = []
    audit_ids: list[str] = []
    halted = None

    if missing_required:
        report = {
            "ok": False,
            "result": "FAIL",
            "reason": "required test cases missing from test_case_registry",
            "missing_required": missing_required,
            "active_tags": active_tags,
            "active_count": len(all_active),
            "results": [],
            "audit_trace_ids": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "source": "test_case_registry",
            "report_path": str(REPORT_PATH),
        }
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    started = datetime.now(timezone.utc).isoformat()
    for case in cases:
        tag = case.get("case_ref_tag")
        title = case.get("case_title")
        payload = case.get("input_payload") or {}
        exp_ok = bool(case.get("expected_ok"))
        exp_errs = case.get("expected_errors") or []
        if not isinstance(exp_errs, list):
            exp_errs = [str(exp_errs)]

        actual = validate_new_task(payload)
        act_ok = bool(actual.get("ok")) if isinstance(actual, dict) else False
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

        # Runner-only audit write (PASS or FAIL) — never inside validate_new_task
        audit = _write_audit_for_case(
            tag=str(tag or ""),
            title=str(title or ""),
            payload=payload if isinstance(payload, dict) else {"raw": payload},
            actual=actual if isinstance(actual, dict) else {"ok": False, "errors": [str(actual)]},
            act_ok=act_ok,
            tdd_passed=passed,
            exp_ok=exp_ok,
        )
        audit_id = audit.get("trace_id") if isinstance(audit, dict) else None
        if not (isinstance(audit, dict) and audit.get("ok") and audit_id):
            passed = False
            reasons.append(f"audit_trace insert failed: {audit}")
        else:
            audit_ids.append(str(audit_id))

        row = {
            "case_ref_tag": tag,
            "case_title": title,
            "result": "PASS" if passed else "FAIL",
            "expected_ok": exp_ok,
            "actual_ok": act_ok,
            "expected_errors": exp_errs,
            "actual_errors": (actual.get("errors") if isinstance(actual, dict) else None),
            "reasons": reasons,
            "audit_trace_id": audit_id,
            "audit_insert_ok": bool(isinstance(audit, dict) and audit.get("ok")),
        }
        results.append(row)
        print(
            f"{row['result']:4} {tag} {title} audit={audit_id} {reasons if reasons else ''}"
        )
        if not passed:
            halted = tag
            break

    all_pass = all(r["result"] == "PASS" for r in results) and not missing_required
    if halted:
        all_pass = False

    # Require every required tag executed and PASS
    req_results = [r for r in results if r["case_ref_tag"] in REQUIRED_VALIDATE_TEST_TAGS]
    if len(req_results) < len(REQUIRED_VALIDATE_TEST_TAGS) or any(
        r["result"] != "PASS" for r in req_results
    ):
        all_pass = False

    # Verify audit rows landed for executed tags
    audit_by_tag = {
        t: len(list_audit_by_test_case(t)) for t in REQUIRED_VALIDATE_TEST_TAGS
    }
    recent = list_recent_audit(limit=max(20, len(results) + 5))
    recent_ids = {r.get("trace_id") for r in recent}
    missing_audit_ids = [i for i in audit_ids if i not in recent_ids]
    executed_tags = [r.get("case_ref_tag") for r in results]
    missing_tag_audits = [t for t in executed_tags if not audit_by_tag.get(t)]
    audit_verify = {
        "ok": len(missing_tag_audits) == 0 and len(audit_ids) == len(results),
        "audit_ids_written": audit_ids,
        "audit_count_by_test_case": audit_by_tag,
        "recent_count": len(recent),
        "missing_tag_audits": missing_tag_audits,
        "missing_ids_in_recent_window": missing_audit_ids,
    }
    if not audit_verify["ok"]:
        all_pass = False

    report = {
        "ok": all_pass,
        "result": "PASS" if all_pass else "FAIL",
        "halted_at": halted,
        "active_count": len(all_active),
        "executed_count": len(results),
        "pass_count": sum(1 for r in results if r["result"] == "PASS"),
        "fail_count": sum(1 for r in results if r["result"] == "FAIL"),
        "required_tags": list(REQUIRED_VALIDATE_TEST_TAGS),
        "filtered_to": list(REQUIRED_VALIDATE_TEST_TAGS),
        "results": results,
        "audit_trace_ids": audit_ids,
        "audit_verify": audit_verify,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_case_registry",
        "note": (
            "cases filtered to REQUIRED_VALIDATE_TEST_TAGS (tc.1.*); "
            "validate_new_task read-only; audit_trace written by runner only"
        ),
        "report_path": str(REPORT_PATH),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
                "audit_verify_ok": (report.get("audit_verify") or {}).get("ok"),
                "audit_ids": report.get("audit_trace_ids"),
                "report_path": report.get("report_path"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
