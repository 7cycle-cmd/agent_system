"""runtime_trace_report.py -- ONE read-only view of the whole runtime chain:
fault -> report -> ack, keyed on the FAULT OCCURRENCE.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "為呢條 runtime 鏈寫一份端到端 trace 報表（一條查詢同時顯示 fault -> 報告 -> ack
     狀態），令整條生命週期喺一個畫面睇得清"

THE CHAIN IS ALREADY JOINABLE, so this module adds NO table and NO column:

    fault_event            the occurrence      -> `group_id` + `status`
    chat_center_message    the report(s)       -> `fault_ref='fault:<group>#<event_id>'`
    fault_event_fact       the ack             -> `acked_by` / `acked_cite` / `report_message_id`

`chat_center_message.fault_ref` names the fault row and
`fault_event_fact.report_message_id` names the message, so the two directions
already exist. This module only READS them.

WHY THE KEY IS THE OCCURRENCE, NOT THE MESSAGE (decided at approval)
-------------------------------------------------------------------
MEASURED: a raw LEFT JOIN repeats the occurrence once per message — fault 31
appeared **4 times** (one per report/reply row). So the view groups by occurrence
and nests the messages, because the question a reader asks is "is THIS fault
handled?", which is about the occurrence.

THE STATE IS DERIVED, never stored:
    RESOLVED        the occurrence is closed (`fault_event.status='resolved'`)
    OPEN_ACKED      open, and an ack exists (`acked_by`) but it is still open
                    (the ack closed nothing — a real inconsistency worth SEEING)
    OPEN_ACTIONABLE open with a report whose message is still `ask`
    OPEN_UNREPORTED open with NO report at all (a fault nobody was told about)
    OPEN_QUIET      open, reported, but no `ask` remains (superseded/retired)

READ-ONLY: this module contains no INSERT/UPDATE/DELETE. An ack is written by
`report_ack`; a resolve by `runtime_trace`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# The link format `chat_report.link_for` writes and `report_ack.parse_link` reads.
# REPEATED here as a SQL expression, not as a Python constant, because the join is
# done by the DATABASE (one query, not N): `fault_ref` = 'fault:'||group_id||'#'||event_id
LINK_SQL = "('fault:' || fe.group_id || '#' || fe.event_id)"

# Every state this view can report, with the MEANING the UI shows. A state with no
# meaning stated would be a code a reader has to guess.
STATES: dict[str, str] = {
    "RESOLVED": "the occurrence is closed",
    "OPEN_ACKED": ("open, but an ack exists — the ack did not close it, which is "
                   "an inconsistency worth seeing"),
    "OPEN_ACTIONABLE": "open, with a report still asking for a human (`ask`)",
    "OPEN_UNREPORTED": "open with NO report at all — nobody was told",
    "OPEN_QUIET": "open and reported, but no `ask` remains (superseded/retired)",
}


class TraceReportError(Exception):
    """Refused -- the report cannot be produced."""


def _fact(conn: sqlite3.Connection, event_id: int, keyword: str) -> str | None:
    r = conn.execute(
        "SELECT value_text FROM fault_event_fact WHERE event_id=? AND keyword=? "
        "ORDER BY id LIMIT 1", (int(event_id), keyword)).fetchone()
    if not r:
        return None
    v = str(r[0])
    return v if v.strip() not in ("", "None", "NA") else None


def state_for(*, fault_status: str, acked_by: str | None,
              messages: list[dict[str, Any]]) -> str:
    """The derived state. NEVER stored — it is a reading of the rows."""
    if str(fault_status) == "resolved":
        return "RESOLVED"
    if acked_by:
        return "OPEN_ACKED"
    if not messages:
        return "OPEN_UNREPORTED"
    if any(str(m.get("status")) == "ask" for m in messages):
        return "OPEN_ACTIONABLE"
    return "OPEN_QUIET"


def trace(conn: sqlite3.Connection, *, status: str | None = None,
          group: str | None = None, actionable_only: bool = False,
          limit: int = 100) -> dict[str, Any]:
    """One row per FAULT OCCURRENCE, with its reports and its ack nested.

    `status` filters by the DERIVED state (not a column). `actionable_only` keeps
    only occurrences a human must act on. Newest occurrence first.
    """
    sql = (
        "SELECT fe.event_id, fe.group_id, fe.status AS fault_status, "
        "fe.fault_type, fe.detect_at, fe.resolved_at "
        "FROM fault_event fe "
    )
    where: list[str] = []
    args: list[Any] = []
    if group:
        where.append("fe.group_id = ?")
        args.append(str(group))
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY fe.event_id DESC LIMIT ?"
    # `limit` bounds the OCCURRENCES; messages are fetched per occurrence.
    args.append(max(1, min(int(limit or 100), 1000)))

    occ: list[dict[str, Any]] = []
    for r in conn.execute(sql, tuple(args)):
        eid = int(r["event_id"])
        gid = str(r["group_id"])
        link = "fault:%s#%d" % (gid, eid)
        msgs = [dict(m) for m in conn.execute(
            "SELECT id, status, role, title, event, evidence_ref, fault_ref, "
            "created_at FROM chat_center_message WHERE fault_ref=? ORDER BY id",
            (link,))]
        acked_by = _fact(conn, eid, "acked_by")
        row = {
            "event_id": eid,
            "group_id": gid,
            "fault_type": r["fault_type"],
            "fault_status": str(r["fault_status"]),
            "detect_at": r["detect_at"],
            "resolved_at": r["resolved_at"],
            "fault_ref": link,
            "acked_by": acked_by,
            "acked_cite": _fact(conn, eid, "acked_cite"),
            "report_message_id": _fact(conn, eid, "report_message_id"),
            "sighting_count": _fact(conn, eid, "sighting_count"),
            "latest_sighting_at": _fact(conn, eid, "latest_sighting_at"),
            "messages": msgs,
            "n_messages": len(msgs),
            "ask_message_id": next((int(m["id"]) for m in msgs
                                    if str(m.get("status")) == "ask"), None),
            "state": state_for(fault_status=str(r["fault_status"]),
                               acked_by=acked_by, messages=msgs),
        }
        row["state_meaning"] = STATES.get(row["state"], "")
        occ.append(row)

    if status:
        want = str(status).strip().upper()
        occ = [o for o in occ if o["state"] == want]
    if actionable_only:
        occ = [o for o in occ if o["state"] == "OPEN_ACTIONABLE"]
    return {"ok": True, "occurrences": occ, "count": len(occ),
            "states": STATES, "read_only": True}


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Counts per DERIVED state, over ALL occurrences. Read-only.

    A REPORTED set of numbers: a state with 0 is a fact about the system, and
    hiding it would make the view look like the states that exist are the states
    that can exist.
    """
    t = trace(conn, limit=1000)
    counts: dict[str, int] = {k: 0 for k in STATES}
    for o in t["occurrences"]:
        counts[o["state"]] = counts.get(o["state"], 0) + 1
    return {"ok": True, "total": t["count"], "by_state": counts, "states": STATES,
            "read_only": True}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="runtime trace report (read-only)")
    ap.add_argument("--db", default=str(BASE_DIR / "agent.db"))
    ap.add_argument("--group", default=None)
    ap.add_argument("--state", default=None)
    ap.add_argument("--actionable", action="store_true")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.summary:
            print(_json.dumps(summary(conn), indent=2, ensure_ascii=False,
                              default=str))
            return 0
        rep = trace(conn, status=args.state, group=args.group,
                    actionable_only=args.actionable, limit=args.limit)
        print("occurrences: %d" % rep["count"])
        for o in rep["occurrences"]:
            print("  fault %-4s %-24s %-16s msgs=%d acked_by=%s"
                  % (o["event_id"], o["group_id"], o["state"], o["n_messages"],
                     o["acked_by"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
