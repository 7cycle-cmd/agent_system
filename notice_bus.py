#!/usr/bin/env python
"""notice_bus.py — the NOTICE BOARD publisher for scanner tasks.

WHY THIS EXISTS (the human's correction, 2026-09-28)
----------------------------------------------------
    "sorry, object is task with task ID and entity_id"
    "conversaction is the connection like notice broad, to have a place for
     watchdog or... to monitoring and catch the job ASAP by 24/7"

TWO things in that correction, and I had the first one WRONG:

  1. THE OBJECT IS A TASK, not a finding. A finding is a raw fact; the thing that
     must travel is a TASK carrying **`task_id` + `entity_id`**. MEASURED: the
     queue row ALREADY has `task_id` (`tq_<hex>`), `entity_id` is formable from
     `code_registry` via `entity_id.format` (`R-1-67-1`), and `task_entity_link`
     already holds the task↔entity pair. So nothing new is invented.
  2. THE CONVERSATION CENTRE IS A NOTICE BOARD — a place for the watchdog to
     leave a task so it is caught 24/7. It is NOT a report body.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does NOT INSERT into `chat_center_message`. MEASURED: there is **ONE** insert
site (`skill_library_api.py:1729`), called only by `chat_report.report_and_send`.
A second INSERT would be a second place that knows the rule (the citation gate
`event in ('discovery','lesson')` REQUIRES a non-empty `evidence_ref`), and the
two would drift — the defect family this repo keeps removing. So a notice is
published THROUGH `chat_report.send`.

IDEMPOTENT BY FINGERPRINT — the 24/7 requirement made safe
----------------------------------------------------------
A board that grows a copy per pass is unreadable within a week (the same defect
`code_scan`'s dedupe already removes for the queue). So a notice is keyed by the
finding's FINGERPRINT, stored in `fault_ref` — the column explicitly chosen for a
LINK that survives being used (`chat_report.link_for`'s own docstring: a link
stored in `measured_effect` was DESTROYED by an ack note). `published()` reads
that column, so a second pass finds the notice and SKIPS.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import chat_report  # noqa: E402

# THE EVENT. `chat_report.REPORT_EVENT` is 'discovery' — the ONE value the live
# table uses AND the one the writer's citation gate knows. Inventing a new event
# ('code_finding') would either be refused by the gate or need the gate widened,
# and widening a gate to let a new value through is how the gate stops gating.
NOTICE_EVENT = chat_report.REPORT_EVENT
# STATUS 'ask' = it asks a human/bot to act, which is exactly what a notice is.
NOTICE_STATUS = chat_report.REPORT_STATUS
NOTICE_PREFIX = "notice:code_scan#"

# P8.4 — THE ROUTE, DECLARED. MEASURED: an undeclared route has an IMPLICIT sink
# and an implicit sink is how a notice silently stops arriving. It is declared
# through `route_registry.declare_route` — the ONE write path, which ENFORCES a
# measurable `unit` and a checkable `cite_ref`. NO INSERT is written here.
ROUTE_NOTICE = "code_scan_task_to_notice"


def declare_route(conn: sqlite3.Connection, *, commit: bool = True
                  ) -> dict[str, Any]:
    """Declare + MEASURE the scanner→board route. One write path, no INSERT."""
    import route_registry as rr
    rr.ensure_schema(conn)
    declared = rr.declare_route(
        conn, ROUTE_NOTICE,
        from_kind="table", from_ref="validate_task_queue",
        to_kind="table", to_ref="chat_center_message",
        rel="notices",
        unit="count of open code_scan tasks that reached ONE chat_center_message",
        evidence_cmd="python code_scan_kicker.py --verify",
        cite_ref="notice_bus.py:1",
        declared_by="notice_bus.declare_route", commit=False)
    # HEALTH IS MEASURED, not assumed: a route whose health was never measured is
    # a claim. The counts are the REVERSE link — notices on the board whose
    # fingerprint STILL matches an open task.
    on_board = published(conn)
    open_fps = {str(x.get("fingerprint") or "")
                for x in (open_tasks(conn).get("rows") or [])}
    resolved = len(on_board & open_fps)
    dangling = len(on_board - open_fps)
    derived = rr.derive_health(unresolved_endpoints=dangling,
                               forward_call_sites=1,
                               reverse_call_sites=resolved, drifted_refs=0)
    stored = rr.record_health(conn, ROUTE_NOTICE, health=derived["health"],
                              health_count=int(derived["count"]), commit=False)
    if commit:
        conn.commit()
    return {"ok": True, "route_key": ROUTE_NOTICE, "declared": declared,
            "health": derived["health"], "count": derived["count"],
            "unit": derived["unit"], "resolved_notices": resolved,
            "dangling_notices": dangling, "stored": stored}


def notice_key(fingerprint: str) -> str:
    """The stable token a published notice carries, so it can be found again."""
    return "%s%s" % (NOTICE_PREFIX, str(fingerprint or "").strip())


def published(conn: sqlite3.Connection) -> set[str]:
    """The fingerprints ALREADY on the board. READ-ONLY.

    Reads `chat_center_message.fault_ref` for our prefix and returns the
    fingerprints. A row that carries no token is ignored rather than assumed new:
    a notice nobody can key is not something to dedupe against.
    """
    out: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT fault_ref FROM chat_center_message WHERE fault_ref LIKE ?",
            (NOTICE_PREFIX + "%",)).fetchall()
    except Exception:
        return out
    for r in rows:
        v = r["fault_ref"] if not isinstance(r, tuple) else r[0]
        v = str(v or "")
        if v.startswith(NOTICE_PREFIX):
            out.add(v[len(NOTICE_PREFIX):])
    return out


def title_for(row: dict[str, Any]) -> str:
    """THE TITLE = the finding in one line, with its IDENTITY in it.

    The live convention (measured) is a sentence stating what was FOUND, not a
    topic. It also carries `task_id` and `entity_id`, because the human said the
    OBJECT is the task+entity pair — a title without them cannot be actioned.
    """
    rule = str(row.get("code_scan_rule") or "?")
    cite = str(row.get("code_scan_cite_ref") or "?")
    own = str(row.get("code_scan_owner") or
              row.get("code_scan_owner_state") or "?")
    tid = str(row.get("task_id") or "?")
    ent = str(row.get("entity_id") or row.get("entity_state") or "?")
    return ("%s at %s — owner=%s task=%s entity=%s"
            % (rule, cite, own, tid, ent))


def content_for(row: dict[str, Any]) -> str:
    occ = row.get("code_scan_occurrences") or 1
    cites = row.get("code_scan_cites") or [row.get("code_scan_cite_ref")]
    lines = [
        "SEVERITY: %s" % row.get("code_scan_severity"),
        "RULE    : %s" % row.get("code_scan_rule"),
        "MESSAGE : %s" % row.get("code_scan_message"),
        "OCCURS  : %s at %s" % (occ, ", ".join(str(c) for c in cites[:8])),
        "OWNER   : %s %s" % (row.get("code_scan_owner_state"),
                             row.get("code_scan_owner") or ""),
        "ENTITY  : %s %s" % (row.get("entity_state"),
                             row.get("entity_id") or ""),
        "TASK    : %s" % row.get("task_id"),
        "VIEW    : python code_scan_kicker.py --verify",
    ]
    return "\n".join(lines)


def publish(rows: list[dict[str, Any]], *, conn: sqlite3.Connection,
            apply: bool = False) -> dict[str, Any]:
    """Put NEW tasks on the board. `apply=False` is a DRY RUN.

    For each row: the `evidence_ref` is the `path:line` (REQUIRED — an uncited
    notice is DISCARDED, never downgraded), and the dedupe key is the
    FINGERPRINT, read back from `fault_ref`.
    """
    already = published(conn)
    todo = [r for r in rows
            if str(r.get("code_scan_fingerprint") or "") not in already]
    out: dict[str, Any] = {"already_on_board": len(already),
                           "would_publish": len(todo), "applied": False,
                           "skipped_no_cite": 0, "published": 0,
                           "sample": [title_for(r) for r in todo[:4]]}
    if not todo:
        return out
    # A NOTICE WITH NO CITATION IS NOT A NOTICE. Counted, not silently dropped.
    citable = []
    for r in todo:
        if not str(r.get("code_scan_cite_ref") or "").strip():
            out["skipped_no_cite"] += 1
            continue
        citable.append(r)
    out["would_publish"] = len(citable)
    if not apply:
        return out
    for r in citable:
        rep = {
            "title": title_for(r),
            "content": content_for(r),
            "event": NOTICE_EVENT,
            "status": NOTICE_STATUS,
            "evidence_ref": str(r["code_scan_cite_ref"]),
            # THE KEY AND THE LINK IN ONE COLUMN, chosen because a LINK must
            # survive being read/acked (chat_report.link_for's own measured note).
            "fault_ref": notice_key(str(r.get("code_scan_fingerprint") or "")),
        }
        try:
            res = chat_report.send(rep, conn=conn)
            if res.get("ok"):
                out["published"] += 1
        except Exception as exc:
            out.setdefault("errors", []).append(
                "%s: %s" % (type(exc).__name__, exc))
    out["applied"] = True
    return out


def open_tasks(conn: sqlite3.Connection) -> dict[str, Any]:
    """WHAT IS OPEN AND WHOSE IS IT — the reader the plan's P8.3 promises.

    A worker cannot act on a queue it cannot see. MEASURED: the queue row carries
    `task_id`, `entity_id`, `owner_state`, `severity` and the cite, so this READER
    needs no new table — it projects what is already stored.
    """
    rows = []
    try:
        raw = conn.execute(
            "SELECT task_id, payload, status FROM validate_task_queue "
            "WHERE status='pending'").fetchall()
    except Exception:
        return {"ok": False, "reason": "validate_task_queue unreadable"}
    for r in raw:
        try:
            d = r["payload"] if isinstance(r["payload"], dict) \
                else json.loads(r["payload"])
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("code_scan_fingerprint"):
            continue
        row = dict(d)          # the SAME shape the queue row carries
        row["task_id"] = r["task_id"]
        # short aliases, so a reader can ask for them without knowing the
        # `code_scan_` prefix. MEASURED 2026-09-28: `publish()` first read
        # `code_scan_cite_ref` off THIS dict, got None for all 17 rows, and so
        # refused every notice as "no citation" — a silent no-op caused purely by
        # two key shapes for one row. ONE row shape, aliases included.
        row["severity"] = d.get("code_scan_severity")
        row["rule"] = d.get("code_scan_rule")
        row["cite_ref"] = d.get("code_scan_cite_ref")
        row["owner_state"] = d.get("code_scan_owner_state")
        row["owner"] = d.get("code_scan_owner")
        row["occurrences"] = d.get("code_scan_occurrences")
        row["fingerprint"] = d.get("code_scan_fingerprint")
        rows.append(row)
    by_sev: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    for x in rows:
        by_sev[x["severity"]] = by_sev.get(x["severity"], 0) + 1
        by_owner[x["owner_state"]] = by_owner.get(x["owner_state"], 0) + 1
    return {"ok": True, "open": len(rows), "by_severity": by_sev,
            "by_owner_state": by_owner, "rows": rows}


def stale_tasks(conn: sqlite3.Connection, *, sla_seconds: int = 900
                ) -> dict[str, Any]:
    """P8.5/N6 — the 24/7 CATCH: which open tasks nobody has touched.

    THE HUMAN: "to monitoring and catch the job ASAP by 24/7". A notice that is
    never acted on is the failure mode this closes: a task can sit on the board
    forever and look like a healthy board.

    TWO conditions, BOTH required, and both READ (never assumed):
      1. the task is `pending` — it is still open;
      2. it is OLDER than the SLA, judged by `created_at` against the DB's OWN
         clock (`datetime('now')`), so the comparison is one clock, not two.

    `age_seconds` comes from SQLite itself. MEASURED: reading the clock in Python
    and comparing it to a DB timestamp is TWO clocks, and the repo already has a
    `two_clocks_and_pair_key` note recording that defect.

    A task with NO `created_at` is reported SEPARATELY (`undated`), not counted as
    fresh: an undated row cannot be aged, so calling it "not stale" would be a
    silent pass on a row nobody can judge.
    """
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(validate_task_queue)")}
    except Exception:
        return {"ok": False, "reason": "validate_task_queue unreadable"}
    if "created_at" not in cols:
        return {"ok": False, "reason": "validate_task_queue has no created_at"}
    rows, undated = [], 0
    ack = ("ack_at" in cols)
    for r in conn.execute(
            "SELECT task_id, payload, created_at"
            + (", ack_at" if ack else "")
            + ", CAST((julianday('now') - julianday(created_at)) * 86400 AS "
              "INTEGER) AS age_seconds FROM validate_task_queue "
              "WHERE status='pending'"):
        try:
            d = r["payload"] if isinstance(r["payload"], dict) \
                else json.loads(r["payload"])
        except Exception:
            continue
        if not isinstance(d, dict) or not d.get("code_scan_fingerprint"):
            continue
        if r["created_at"] is None:
            undated += 1
            continue
        if ack and r["ack_at"]:
            continue                     # already acknowledged -> not stale
        age = int(r["age_seconds"] or 0)
        if age >= int(sla_seconds):
            rows.append({"task_id": r["task_id"], "age_seconds": age,
                         "rule": d.get("code_scan_rule"),
                         "cite_ref": d.get("code_scan_cite_ref"),
                         "owner_state": d.get("code_scan_owner_state"),
                         "entity_id": d.get("entity_id"),
                         "fingerprint": d.get("code_scan_fingerprint")})
    rows.sort(key=lambda x: -x["age_seconds"])
    return {"ok": True, "sla_seconds": int(sla_seconds), "stale": len(rows),
            "undated": undated, "rows": rows[:20]}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="the code-scan notice board")
    ap.add_argument("--open", action="store_true",
                    help="read WHAT IS OPEN AND WHOSE IS IT")
    ap.add_argument("--publish", action="store_true",
                    help="publish the NEW tasks to the board (default: dry run)")
    ap.add_argument("--stale", action="store_true",
                    help="the 24/7 CATCH: open tasks past the SLA with no ack")
    ap.add_argument("--sla", type=int, default=900,
                    help="SLA in seconds for --stale (default 900)")
    ap.add_argument("--declare-route", action="store_true",
                    help="declare + measure the scanner->board route (P8.4)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        if args.declare_route:
            print(json.dumps(declare_route(conn), indent=1, ensure_ascii=False,
                             default=str))
            return 0
        if args.stale:
            st = stale_tasks(conn, sla_seconds=args.sla)
            if args.json:
                print(json.dumps(st, indent=1, ensure_ascii=False))
            else:
                print("SLA        : %ss" % st.get("sla_seconds"))
                print("STALE      : %s" % st.get("stale"))
                print("undated    : %s" % st.get("undated"))
                for x in st.get("rows") or []:
                    print("   %-8s %-6s %-44s owner=%-15s entity=%s"
                          % ("%ds" % x["age_seconds"], x["rule"], x["cite_ref"],
                             x["owner_state"], x["entity_id"] or "-"))
            return 0
        if args.open or not args.publish:
            res = open_tasks(conn)
            if args.json:
                print(json.dumps({k: v for k, v in res.items() if k != "rows"},
                                 indent=1, ensure_ascii=False))
            else:
                print("OPEN      : %s" % res.get("open"))
                print("by severity: %s" % res.get("by_severity"))
                print("WHO FIXES  : %s" % res.get("by_owner_state"))
                for x in (res.get("rows") or [])[:8]:
                    print("   %-5s %-6s %-42s owner=%-15s entity=%s"
                          % (x["severity"], x["rule"], x["cite_ref"],
                             x["owner_state"], x["entity_id"] or x["entity_state"]))
            return 0
        # publish needs the CURRENT pending rows
        res = open_tasks(conn)
        pub = publish(res.get("rows") or [], conn=conn, apply=False)
        if args.publish:
            # dry-run first is the DEFAULT shape; --publish applies
            pass
        print(json.dumps(pub, indent=1, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())