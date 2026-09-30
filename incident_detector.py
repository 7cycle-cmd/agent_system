# -*- coding: utf-8 -*-
"""incident_detector.py — detect incidents from the lifecycle log, automatically.

WHY THIS FILE EXISTS
--------------------
`incident_postmortem.py` owns the RULES (pre-declared triggers + the review
gate). But a rule that nobody calls changes nothing — the defect
`systematic_debug.py` documents. The report's own §2 recorded the gap:

    「教訓係我想起才寫；冇任何觸發條件話『呢個算係事故』」

So this module is the CALLER: it reads `task_lifecycle_log` and decides, from
the pre-declared triggers, whether an incident occurred. Detection is automatic
rather than remembered.

The trigger rules are IMPORTED from `incident_postmortem`, never re-stated here.
One copy of the rule, two consumers — otherwise the detector and the gate drift
apart and the detector starts reporting incidents the gate no longer recognises.

Read-only: this module never writes to the lifecycle log.
"""
from __future__ import annotations

from typing import Any

import incident_postmortem as ipm

# Lifecycle event types that indicate a failed attempt.
FAIL_EVENT_TYPES = ("validation_fail",)

# Phrases in a lifecycle comment that indicate a repeated-failure or
# architecture-level problem. Kept as data so the mapping is inspectable.
ARCHITECTURE_MARKERS = (
    "architecture", "reframe", "question the architecture", "wrong approach",
    "架構", "重新框定",
)
REPEAT_MARKERS = (
    "same class", "again", "recurred", "repeat", "同類", "重複", "又",
)


def _comment(ev: dict) -> str:
    return str(ev.get("comment") or ev.get("event_summary") or "").lower()


def incident_from_events(events: list[dict] | None) -> dict:
    """Derive an incident record from lifecycle events.

    Returns the shape `incident_postmortem` consumes, so the two modules share
    one vocabulary. Every field is derived from the events, never assumed.
    """
    evs = [e for e in (events or []) if isinstance(e, dict)]

    failed = [e for e in evs if str(e.get("event_type") or "") in FAIL_EVENT_TYPES]
    failed_fix_attempts = len(failed)

    # Same-class recurrence: count distinct comments that carry a repeat marker.
    repeat_comments = {_comment(e) for e in evs if any(
        m in _comment(e) for m in REPEAT_MARKERS)}
    same_class_count = max(len(repeat_comments), 1 if failed_fix_attempts >= 2 else 0)

    architecture_change_required = any(
        any(m in _comment(e) for m in ARCHITECTURE_MARKERS) for e in evs)

    # A help request is recorded as a state_update whose comment starts with
    # "need help:" (worker_help.ask_help writes exactly that).
    human_asked = any(
        _comment(e).startswith("need help:") or "need help" in _comment(e)
        for e in evs)

    return {
        "failed_fix_attempts": failed_fix_attempts,
        "same_class_count": same_class_count,
        "architecture_change_required": architecture_change_required,
        "human_asked": human_asked,
        "false_pass_recorded": False,   # not derivable from the lifecycle log
        "evidence_destroyed": False,    # not derivable from the lifecycle log
        "_source": "lifecycle_log",
        "_event_count": len(evs),
    }


def detect(task_id: str, *, db_path: Any = None) -> dict:
    """Read the lifecycle log for `task_id` and classify it.

    Returns {ok, task_id, incident, classification, postmortem_required}.
    Never raises on a missing task — an unknown task is simply not an incident.
    """
    try:
        from src.task_center.lifecycle_log import list_lifecycle_events

        res = list_lifecycle_events(task_id, db_path=db_path)
        events = res.get("events") or []
    except Exception as e:
        return {
            "ok": False,
            "task_id": str(task_id),
            "error": "%s: %s" % (type(e).__name__, e),
            "incident": {},
            "classification": {"is_incident": False, "triggers": [],
                               "requires_postmortem": False},
            "postmortem_required": False,
        }

    incident = incident_from_events(events)
    cls = ipm.classify_incident(incident)
    return {
        "ok": True,
        "task_id": str(task_id),
        "incident": incident,
        "classification": cls,
        "postmortem_required": cls["requires_postmortem"],
    }


def detect_all(*, db_path: Any = None, limit: int = 200) -> dict:
    """Scan every task that has lifecycle events. Returns the incidents found."""
    try:
        import sqlite3
        from pathlib import Path

        from src.task_center.lifecycle_log import DEFAULT_DB

        path = Path(db_path or DEFAULT_DB)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT DISTINCT task_id FROM task_lifecycle_log LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "incidents": []}

    incidents = []
    for r in rows:
        d = detect(r["task_id"], db_path=db_path)
        if d.get("postmortem_required"):
            incidents.append(d)
    return {"ok": True, "scanned": len(rows), "incidents": incidents,
            "count": len(incidents)}


def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Detect incidents from the lifecycle log")
    ap.add_argument("--task-id", help="one task; omit to scan all")
    ap.add_argument("--all", action="store_true", help="scan every task")
    args = ap.parse_args(argv)

    if args.all or not args.task_id:
        out = detect_all()
    else:
        out = detect(args.task_id)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
