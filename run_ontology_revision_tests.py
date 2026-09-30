"""
DB-driven ontology revision/rollback TDD runner.

Cases come ONLY from test_case_registry (tc.2.*).
Does NOT call validate_new_task. Mutate/rollback helpers write history.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.task_center.ontology_store import (
    REQUIRED_REVISION_TEST_TAGS,
    ensure_revision_probe_entity,
    get_entity_revisions,
    get_registry_entity,
    get_test_case,
    rollback_entity_to_revision,
    seed_ontology_registry_defaults,
    seed_ontology_revision_test_cases,
    update_registry_entity,
)

BASE_DIR = Path(__file__).resolve().parent
REPORT_PATH = BASE_DIR / "qc_evidence" / "ontology_revision_run.json"

BASELINE_NAME = "Revision Probe Capability"
MUTATED_NAME = "Revision Probe Capability (mutated)"
BASELINE_DESC = "Disposable probe for ontology_revision_history TDD"


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


def _run_tc20(payload: dict) -> dict:
    """update_and_assert_revision"""
    et = str(payload.get("entity_type") or "")
    ek = str(payload.get("entity_key") or "")
    fields = payload.get("fields") or {}
    asserts = payload.get("assert") or {}
    errors: list[str] = []

    ensure_revision_probe_entity(reset_baseline=True)
    before = get_registry_entity(et, ek)
    if not before:
        return {"ok": False, "errors": [f"probe entity missing: {et}/{ek}"]}

    upd = update_registry_entity(
        et,
        ek,
        fields if isinstance(fields, dict) else {},
        created_by=str(payload.get("created_by") or "tdd"),
        note=str(payload.get("note") or "tc.2.0"),
    )
    if not upd.get("ok"):
        return {
            "ok": False,
            "errors": [str(upd.get("error") or "update failed")],
            "update": upd,
        }

    revs = get_entity_revisions(et, ek)
    min_revs = int(asserts.get("min_revisions") or 1)
    if len(revs) < min_revs:
        errors.append(f"min_revisions expected>={min_revs} actual={len(revs)}")

    name_after = asserts.get("name_after")
    row = upd.get("row") or {}
    if name_after and str(row.get("name") or "") != str(name_after):
        errors.append(
            f"name_after expected={name_after!r} actual={row.get('name')!r}"
        )

    snap_name = asserts.get("snapshot_name_before")
    pre = upd.get("pre_image") or {}
    if snap_name and str(pre.get("name") or "") != str(snap_name):
        top = revs[0] if revs else {}
        snap = top.get("snapshot") if isinstance(top, dict) else {}
        if str((snap or {}).get("name") or "") != str(snap_name):
            errors.append(
                f"snapshot_name_before expected={snap_name!r} "
                f"pre={pre.get('name')!r} top_snap={(snap or {}).get('name')!r}"
            )

    if not upd.get("revision_id"):
        errors.append("revision_id missing from update result")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "update": {
            "revision_id": upd.get("revision_id"),
            "name": row.get("name"),
            "version": row.get("version"),
        },
        "pre_image_name": pre.get("name"),
        "revisions_n": len(revs),
        "latest_revision_id": (revs[0].get("revision_id") if revs else None),
    }


def _run_tc21(payload: dict) -> dict:
    """rollback_to_prior_revision — restore snapshot from history."""
    et = str(payload.get("entity_type") or "")
    ek = str(payload.get("entity_key") or "")
    asserts = payload.get("assert") or {}
    errors: list[str] = []

    ensure_revision_probe_entity(reset_baseline=False)
    live = get_registry_entity(et, ek)
    revs = get_entity_revisions(et, ek)

    need_mutate = True
    if live and str(live.get("name") or "") == MUTATED_NAME and len(revs) >= 1:
        need_mutate = False
    if len(revs) < 1:
        need_mutate = True

    if need_mutate:
        if live and str(live.get("name") or "") != BASELINE_NAME:
            update_registry_entity(
                et,
                ek,
                {
                    "name": BASELINE_NAME,
                    "description": BASELINE_DESC,
                    "version": "1",
                    "is_active": 1,
                },
                created_by="tdd_tc.2.1_setup",
                note="tc.2.1 setup restore baseline",
            )
        upd = update_registry_entity(
            et,
            ek,
            {
                "name": MUTATED_NAME,
                "description": "mutated before tc.2.1",
                "version": "2",
            },
            created_by="tdd_tc.2.1_setup",
            note="tc.2.1 setup mutation",
        )
        if not upd.get("ok"):
            return {
                "ok": False,
                "errors": [f"setup update failed: {upd.get('error')}"],
                "update": upd,
            }
        revs = get_entity_revisions(et, ek)

    if not revs:
        return {"ok": False, "errors": ["no revisions available for rollback"]}

    target = None
    for r in revs:
        snap = r.get("snapshot") if isinstance(r.get("snapshot"), dict) else {}
        if str(snap.get("name") or "") == BASELINE_NAME:
            target = r
            break
    if target is None:
        target = revs[-1]

    target_id = str(target.get("revision_id") or "")
    rb = rollback_entity_to_revision(
        et,
        ek,
        target_id,
        created_by=str(payload.get("created_by") or "tdd"),
        note=str(payload.get("note") or "tc.2.1"),
    )
    if not rb.get("ok"):
        return {
            "ok": False,
            "errors": [str(rb.get("error") or "rollback failed")],
            "rollback": rb,
        }

    row = rb.get("row") or {}
    name_after = asserts.get("name_after_rollback")
    if name_after and str(row.get("name") or "") != str(name_after):
        errors.append(
            f"name_after_rollback expected={name_after!r} actual={row.get('name')!r}"
        )

    revs2 = get_entity_revisions(et, ek)
    min_revs = int(asserts.get("min_revisions") or 2)
    if len(revs2) < min_revs:
        errors.append(f"min_revisions expected>={min_revs} actual={len(revs2)}")

    note_contains = str(asserts.get("note_contains") or "")
    top = revs2[0] if revs2 else {}
    top_note = str(top.get("note") or "")
    if note_contains and note_contains not in top_note:
        errors.append(
            f"note_contains missing {note_contains!r} in latest note={top_note!r}"
        )

    if "rollback_to=" not in top_note:
        errors.append(f"latest revision note missing rollback_to= ({top_note!r})")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "rolled_back_to": target_id,
        "new_revision_id": rb.get("revision_id"),
        "name": row.get("name"),
        "revisions_n": len(revs2),
        "latest_note": top_note,
    }


def _execute_case(case: dict) -> dict:
    payload = case.get("input_payload") or {}
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["input_payload must be object"]}
    action = str(payload.get("action") or "").strip()
    if action == "update_and_assert_revision":
        return _run_tc20(payload)
    if action == "rollback_to_prior_revision":
        return _run_tc21(payload)
    return {"ok": False, "errors": [f"unknown action: {action}"]}


def run() -> dict:
    seed_ontology_registry_defaults()
    seed_ontology_revision_test_cases()
    ensure_revision_probe_entity(reset_baseline=True)

    cases: list[dict] = []
    missing: list[str] = []
    for tag in REQUIRED_REVISION_TEST_TAGS:
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
            "reason": "required revision test cases missing",
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
                    "update",
                    "revisions_n",
                    "latest_revision_id",
                    "rolled_back_to",
                    "new_revision_id",
                    "name",
                    "latest_note",
                    "pre_image_name",
                )
                if k in actual
            },
        }
        results.append(row)
        print(f"{row['result']:4} {tag} {title} {reasons if reasons else ''}")
        if not passed:
            halted = tag
            break

    all_pass = (
        all(r["result"] == "PASS" for r in results)
        and len(results) == len(REQUIRED_REVISION_TEST_TAGS)
        and halted is None
    )
    report = {
        "ok": all_pass,
        "result": "PASS" if all_pass else "FAIL",
        "halted_at": halted,
        "executed_count": len(results),
        "pass_count": sum(1 for r in results if r["result"] == "PASS"),
        "fail_count": sum(1 for r in results if r["result"] == "FAIL"),
        "required_tags": list(REQUIRED_REVISION_TEST_TAGS),
        "results": results,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source": "test_case_registry",
        "note": (
            "tc.2.* from test_case_registry; mutate/rollback helpers only; "
            "validate_new_task not invoked"
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
