"""
Seed test_case_registry SSOT (CLI / migrate helper).

Write path only — runners must load cases via get_all_active_test_cases().
Never called from validate_new_task.
"""
from __future__ import annotations

import json
import sys

from src.task_center.ontology_store import (
    ensure_ontology_registry_schema,
    get_all_active_test_cases,
    seed_ontology_registry_defaults,
    seed_validate_new_task_test_cases,
)


def main() -> int:
    ensure_ontology_registry_schema()
    ont = seed_ontology_registry_defaults()
    tc = seed_validate_new_task_test_cases()
    active = get_all_active_test_cases()
    from src.task_center.ontology_store import REQUIRED_VALIDATE_TEST_TAGS

    tags = [c.get("case_ref_tag") for c in active]
    missing = [t for t in REQUIRED_VALIDATE_TEST_TAGS if t not in tags]
    out = {
        "ok": bool(tc.get("ok")) and len(missing) == 0,
        "ontology_seed": {
            "ok": ont.get("ok"),
            "capability_count": ont.get("capability_count"),
        },
        "test_case_seed": tc,
        "active_count": len(active),
        "active_tags": tags,
        "required_validate_tags": list(REQUIRED_VALIDATE_TEST_TAGS),
        "missing_required": missing,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
