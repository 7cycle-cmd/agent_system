"""
Harvest experience_log from audit_trace (CLI).

READ audit_trace only — never mutate audit rows.
Never called from validate_new_task.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from src.task_center.ontology_store import (
    ensure_ontology_registry_schema,
    harvest_experience_from_audit,
    list_experience_by_error_type,
    list_latest_experience,
)

BASE_DIR = Path(__file__).resolve().parent
REPORT_PATH = BASE_DIR / "qc_evidence" / "experience_log_harvest.json"


def main() -> int:
    ensure_ontology_registry_schema()
    result = harvest_experience_from_audit(only_failures=True)
    latest = list_latest_experience(limit=10)
    by_type: dict[str, int] = {}
    for row in list_latest_experience(limit=500):
        et = str(row.get("error_type") or "unknown")
        by_type[et] = by_type.get(et, 0) + 1

    # sample one type if present
    sample_type = next(iter(by_type), None)
    sample_rows = (
        list_experience_by_error_type(sample_type, limit=3) if sample_type else []
    )

    out = {
        "ok": bool(result.get("ok")),
        "harvest": result,
        "latest_n": len(latest),
        "latest_sample": [
            {
                "log_id": r.get("log_id"),
                "audit_trace_id": r.get("audit_trace_id"),
                "test_case_ref": r.get("test_case_ref"),
                "error_type": r.get("error_type"),
                "error_message": (r.get("error_message") or "")[:120],
            }
            for r in latest[:5]
        ],
        "counts_by_error_type": by_type,
        "sample_by_error_type": {
            "error_type": sample_type,
            "rows": [
                {
                    "log_id": r.get("log_id"),
                    "test_case_ref": r.get("test_case_ref"),
                    "error_type": r.get("error_type"),
                }
                for r in sample_rows
            ],
        },
        "report_path": str(REPORT_PATH),
        "note": "audit_trace unmodified; experience_log append-only harvest",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
