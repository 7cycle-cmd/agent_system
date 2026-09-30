"""fault_stale_sweep.py -- close OPEN faults whose PRODUCER went silent, with a
recorded reason. NEVER deletes, NEVER fabricates a report.

WHY THIS EXISTS (the trace report made the gap visible)
-------------------------------------------------------
MEASURED: 21 OPEN faults were reported as `OPEN_UNREPORTED`, and they are
**FOUR conditions, none of them the runtime chain**:

    pair_qc            / pair_qc_mismatch   12 rows   last 2026-09-14 20:29
    default            / crash               5 rows   last 2026-09-14 11:46
    schema_qc:vision_asset / schema_qc_fail  3 rows   last 2026-09-14 05:52
    schema_qc:demo_x       / schema_qc_fail  1 row    last 2026-09-14 05:16

All are 10 days old with `resolved_at='NA'`, and their producers are silent.

THE ROOT CAUSE, RECORDED (not fixed here): the dedup was wired for ONE chain.
The previous plan made `runtime_trace.record_fault` dedup against the OPEN row
(`group_id` + `status='open'`); the OTHER producers write their OWN rows and never
got it, so `pair_qc` produced **12 rows for ONE condition**. Each producer is its
own system, so fixing them is NOT this plan.

WHY NOT BACKFILL A REPORT (measured, not a preference): `chat_report` builds a
report from a `fault_factor_trace` row; NONE of these has one and their producers
are retired. Backfilling would (a) fabricate a measurement nobody made and (b) put
21 unactionable messages in the amber list — the pile-up the last two plans removed.

THE RULE THIS MODULE FOLLOWS
----------------------------
A fault is STALE when its NEWEST `detect_at` among its OPEN rows is older than the
window. It is then closed through the EXISTING `runtime_trace.resolve_fault`, with
`resolved_reason` recorded, so a sweep-close and an ack-close stay distinguishable
in the data. The rows are KEPT: they are the evidence of what happened.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# The declared window. A PARAMETER, never a constant baked into the logic: the
# caller states it and the result REPORTS it, so the number is reviewable.
DEFAULT_STALE_DAYS = 7

# The citation a sweep records. A sweep is an OPERATIONAL act, so the cite names
# the tool and the window, not a person.
SWEEP_CITE = "fault_stale_sweep.py:1"

# The conditions this sweep must NEVER touch: the runtime chain is handled by
# `keepalive_runner` (RESOLVE on recovery) and `report_ack` (a human).
PROTECTED_PREFIX = "runtime_"


class SweepError(Exception):
    """Refused -- nothing written."""


def _age_days(conn: sqlite3.Connection, ts: str) -> float | None:
    """Age in days of a timestamp, computed by SQLite so there is ONE clock."""
    try:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(?))", (str(ts),)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or row[0] is None:
        return None
    return float(row[0])


def plan(conn: sqlite3.Connection, *, stale_days: int = DEFAULT_STALE_DAYS,
         include_protected: bool = False) -> dict[str, Any]:
    """READ-ONLY. One entry per CONDITION, with its duplicate rows and its age.

    ONE ENTRY PER CONDITION, not per row, because the defect is that ONE condition
    has MANY rows: a per-row listing would hide the duplication that caused this
    plan. The rows are listed inside the entry as `event_ids`.
    """
    win = int(stale_days)
    rows = list(conn.execute(
        "SELECT event_id, group_id, fault_type, status, detect_at, resolved_at "
        "FROM fault_event WHERE status='open' ORDER BY group_id, event_id"))
    conditions: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        gid = str(r["group_id"])
        key = (gid, str(r["fault_type"]))
        e = conditions.setdefault(key, {
            "group_id": gid, "fault_type": str(r["fault_type"]),
            "event_ids": [], "oldest": None, "newest": None,
            "protected": gid.startswith(PROTECTED_PREFIX),
        })
        e["event_ids"].append(int(r["event_id"]))
        ts = str(r["detect_at"])
        if e["oldest"] is None or ts < e["oldest"]:
            e["oldest"] = ts
        if e["newest"] is None or ts > e["newest"]:
            e["newest"] = ts

    out: list[dict[str, Any]] = []
    for e in conditions.values():
        age = _age_days(conn, e["newest"])
        e["rows"] = len(e["event_ids"])
        e["age_days"] = None if age is None else round(age, 2)
        e["stale"] = (age is not None and age >= win)
        if e["protected"] and not include_protected:
            e["stale"] = False
            e["why_not"] = ("protected: this is the runtime chain, closed by a "
                            "RESOLVE or an ACK, not by a sweep")
        out.append(e)
    out.sort(key=lambda x: (-(x["rows"]), x["group_id"]))
    stale = [e for e in out if e["stale"]]
    return {
        "ok": True,
        "stale_days": win,
        "conditions": out,
        "stale_conditions": len(stale),
        "stale_rows": sum(e["rows"] for e in stale),
        "total_open_rows": len(rows),
        "read_only": True,
    }


def sweep(conn: sqlite3.Connection, *, stale_days: int = DEFAULT_STALE_DAYS,
          cite_ref: str = SWEEP_CITE, apply: bool = False,
          include_protected: bool = False) -> dict[str, Any]:
    """Close every STALE condition's open rows. `apply=False` is a DRY RUN.

    CLOSES THROUGH `runtime_trace.resolve_fault`, so the lifecycle has ONE writer
    (`status='resolved'` + `resolved_at` + `resolved_by_cite` + `resolved_reason`).
    A private UPDATE here would be a second place that knows the lifecycle, and the
    two would drift.
    """
    p = plan(conn, stale_days=stale_days, include_protected=include_protected)
    if not apply:
        # DRY RUN: report exactly what WOULD change and write nothing.
        return {"ok": True, "applied": False, "stale_days": p["stale_days"],
                "would_close_rows": p["stale_rows"],
                "would_close_conditions": p["stale_conditions"],
                "conditions": p["conditions"], "closed": []}
    if not str(cite_ref or "").strip():
        raise SweepError("no citation, no sweep: cite_ref is required")
    import runtime_trace as rt
    closed: list[dict[str, Any]] = []
    for e in p["conditions"]:
        if not e["stale"]:
            continue
        why = ("stale: the producer has been silent for %s days (window %s), and "
               "no report existed for this occurrence"
               % (e["age_days"], p["stale_days"]))
        for eid in e["event_ids"]:
            # ONE CALL PER ROW, keyed by `event_id`. MEASURED, and my first version
            # was WRONG: it called `resolve_fault(group_id)` once per row, but that
            # closes only the group's OPEN row, so `pair_qc` (12 rows) went 12 -> 11
            # and the sweep would have reported success while 11 rows stayed open.
            # The `event_id` parameter is what makes a per-row close possible.
            try:
                r = rt.resolve_fault(conn, e["group_id"], cite_ref=cite_ref,
                                     reason=why, event_id=int(eid))
                closed.append({"event_id": r["event_id"], "group_id": e["group_id"],
                               "reason": why, "ok": True})
            except rt.RuntimeTraceError as exc:
                closed.append({"event_id": int(eid), "group_id": e["group_id"],
                               "ok": False, "why": str(exc)[:140]})
    conn.commit()
    return {"ok": True, "applied": True, "stale_days": p["stale_days"],
            "would_close_rows": p["stale_rows"],
            "closed": closed,
            "closed_count": sum(1 for c in closed if c["ok"]),
            "failed_count": sum(1 for c in closed if not c["ok"])}


def root_cause_report(conn: sqlite3.Connection, *,
                      stale_days: int = DEFAULT_STALE_DAYS) -> dict[str, Any]:
    """The RECORDED root cause: which conditions duplicated, and by how much.

    REPORTED, not fixed. A condition that produced >1 row for ONE occurrence is a
    producer that lacks the dedup `runtime_trace.record_fault` has.
    """
    p = plan(conn, stale_days=stale_days)
    dupes = [{"group_id": e["group_id"], "fault_type": e["fault_type"],
              "rows": e["rows"], "age_days": e["age_days"]}
             for e in p["conditions"] if e["rows"] > 1]
    return {
        "ok": True,
        "duplicated_conditions": dupes,
        "duplicated_condition_count": len(dupes),
        "extra_rows": sum(d["rows"] - 1 for d in dupes),
        "cause": ("the producers listed here write their OWN fault_event rows and "
                  "lack the OPEN-row dedup that runtime_trace.record_fault has, so "
                  "one ongoing condition became many rows. Each producer is its "
                  "own system; fixing them is NOT this module's job."),
        "read_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="close stale faults (never deletes)")
    ap.add_argument("--db", default=str(BASE_DIR / "agent.db"))
    ap.add_argument("--plan", action="store_true",
                    help="READ-ONLY: show what WOULD be closed")
    ap.add_argument("--apply", action="store_true",
                    help="CLOSE the stale conditions (requires --plan to be read "
                         "first by a human; there is no auto mode)")
    ap.add_argument("--root-cause", action="store_true")
    ap.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    ap.add_argument("--cite", default=SWEEP_CITE)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.root_cause:
            print(_json.dumps(root_cause_report(conn, stale_days=args.stale_days),
                              indent=2, ensure_ascii=False, default=str))
            return 0
        if args.plan:
            p = plan(conn, stale_days=args.stale_days)
            print("stale window: %s days | stale conditions: %s | stale rows: %s"
                  % (p["stale_days"], p["stale_conditions"], p["stale_rows"]))
            for e in p["conditions"]:
                print("   %-26s %-20s rows=%-3s age=%-7s %s"
                      % (e["group_id"], e["fault_type"], e["rows"], e["age_days"],
                         "STALE" if e["stale"] else (e.get("why_not") or "fresh")))
            return 0
        if args.apply:
            print(_json.dumps(sweep(conn, stale_days=args.stale_days,
                                    cite_ref=args.cite, apply=True),
                              indent=2, ensure_ascii=False, default=str))
            return 0
        ap.print_help()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
