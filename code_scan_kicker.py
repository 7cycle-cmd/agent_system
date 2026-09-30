#!/usr/bin/env python
"""code_scan_kicker.py — one scan, then EXIT. The forever loop is the OS.

WHY THIS FILE EXISTS (the human, 2026-09-28)
--------------------------------------------
    "how we can keep it at latest version, not finding and checking everyday ->
     key, in auto forever!!"

MEASURED, and it decides the design: there is **NO** scheduler in the DB — no
table anywhere carries `next_run_at`, `interval_minutes`, `cron`, or any other
next-run column (`PRAGMA table_info` over every table, 2026-09-28). So a
DB-driven forever loop is not available, and writing one would be a new
scheduling mechanism this repo does not have.

The honest shape is therefore:

    THIS SCRIPT = one pass, then EXIT.
    FOREVER    = one OS scheduler entry that calls it (Windows Task Scheduler).

A script that loops forever inside itself cannot be tested, cannot be re-run on
demand, and — MEASURED history in this repo — a self-looping process is how a
turn gets wedged. A script that exits is testable today and schedulable by the OS
tomorrow, and the two concerns stay separate.

WHAT ONE PASS DOES
------------------
  1. `code_scan.scan()` — run the EXISTING readers over the whole site (READ-ONLY);
  2. `code_scan.open_fingerprints()` — what is ALREADY waiting;
  3. enqueue ONLY the NEW findings at/above the severity cut (default `BUG`);
  4. print a summary and exit 0.

THE DEDUPE IS THE POINT: run it 100 times and the waiting list grows only when
the CODE changes. That is what makes "auto forever" safe — a daily run that
re-added everything would make the list unusable within a week.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import code_scan  # noqa: E402
import entity_registry as er  # noqa: E402
import notice_bus  # noqa: E402

REPORT_PATH = BASE_DIR / "qc_evidence" / "code_scan_run.json"


def link_entities(*, apply: bool = False) -> dict:
    """P8.2 — link each OPEN scanner TASK to the ENTITY it is about.

    MEASURED: `task_entity_link` already exists
    (`track_id · entity_type · entity_ref_id · version · role`) and
    `entity_registry.link_task_entity` already REFUSES an entity that does not
    resolve. So this adds NO table and NO second rule — it makes the link the
    human named ("object is task with task ID and entity_id") explicit, using
    the repo's own link function.

    A task whose entity is UNRESOLVED is NOT linked: a link to nothing resolves
    to nothing, which is the same refusal `link_task_entity` applies. It is
    COUNTED, never silently skipped.
    """
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    out = {"linked": 0, "already": 0, "unresolved": 0, "refused": 0,
           "applied": False}
    try:
        board = notice_bus.open_tasks(conn)
        for x in board.get("rows") or []:
            ent = str(x.get("entity_id") or "").strip()
            letter = ent.split("-")[0] if ent else ""
            if not ent or not letter or ent.count("-") != 3:
                out["unresolved"] += 1
                continue
            if not apply:
                out["linked"] += 1
                continue
            r = er.link_task_entity(conn, x["task_id"], letter,
                                    int(x["entity_ref_id"]), None,
                                    "code_scan_subject")
            if r.get("ok") and r.get("created"):
                out["linked"] += 1
            elif r.get("ok"):
                out["already"] += 1
            else:
                out["refused"] += 1
        out["applied"] = bool(apply)
    finally:
        conn.close()
    return out
# How the OS should call this, printed by --install-hint so the "forever" step is
# a copy-paste rather than a paragraph nobody finds again.
TASK_HINT = (
    'schtasks /Create /SC DAILY /TN "agent_system_code_scan" /TR '
    '"\\"%s\\" \\"%s\\"" /ST 09:00' % (sys.executable, Path(__file__).resolve()))


def run_once(*, apply: bool = False, min_severity: str = "BUG",
             include_hardcode: bool = True) -> dict:
    started = datetime.now(timezone.utc).isoformat()
    res = code_scan.scan(include_hardcode=include_hardcode)
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        already = code_scan.open_fingerprints(conn)
        todo = code_scan.enqueue_new(res, already, apply=apply,
                                     min_severity=min_severity, conn=conn)
    finally:
        conn.close()
    # P8.6: the SAME pass publishes + links — no second schedule, no second pass.
    link = link_entities(apply=apply)
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        board = notice_bus.open_tasks(conn)
        notice = notice_bus.publish(board.get("rows") or [], conn=conn,
                                    apply=apply)
    except Exception as exc:  # a board failure must NOT lose the scan
        notice = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        conn.close()
    report = {
        "ok": True,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "population": res["population"],
        "counts": res["counts"],
        "by_severity": res["by_severity"],
        "by_rule": res["by_rule"],
        "already_waiting": len(already),
        "new": {k: v for k, v in todo.items() if k != "sample"},
        "new_sample": todo["sample"],
        "task_entity_link": link,
        "notice_board": notice,
        "applied": bool(apply),
        "min_severity": min_severity,
        "reads": "READ-ONLY unless --apply",
        "forever": TASK_HINT,
        "report_path": str(REPORT_PATH),
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    return report


def verify() -> dict:
    """What is ON the waiting list right now, and is the dedupe key present?

    WHY THIS EXISTS: the claim "the findings went on the EXISTING waiting list"
    is a claim about the DB, and a claim about the DB must be READ back from the
    DB — not inferred from the fact that `--apply` printed `True`. It also
    re-checks the ONE thing the dedupe depends on: every queued row carries its
    `code_scan_fingerprint`. A row without one can never be deduped against, so
    the next pass would re-report it forever.
    """
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM validate_task_queue GROUP BY status")}
        rows, with_fp, by_rule, by_sev = [], 0, {}, {}
        for r in conn.execute(
                "SELECT payload FROM validate_task_queue WHERE status='pending'"):
            try:
                d = r["payload"] if isinstance(r["payload"], dict) \
                    else json.loads(r["payload"])
            except Exception:
                continue
            if not isinstance(d, dict) or not d.get("code_scan_fingerprint"):
                continue
            with_fp += 1
            rows.append(d)
            by_rule[d.get("code_scan_rule")] = \
                by_rule.get(d.get("code_scan_rule"), 0) + 1
            by_sev[d.get("code_scan_severity")] = \
                by_sev.get(d.get("code_scan_severity"), 0) + 1
        return {"ok": True, "queue_by_status": by_status,
                "pending_with_fingerprint": with_fp,
                "by_rule": by_rule, "by_severity": by_sev,
                "sample": [{"rule": d.get("code_scan_rule"),
                            "cite_ref": d.get("code_scan_cite_ref"),
                            "severity": d.get("code_scan_severity"),
                            "fingerprint": d.get("code_scan_fingerprint")}
                           for d in rows[:6]]}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="one code-quality scan pass")
    ap.add_argument("--apply", action="store_true",
                    help="enqueue the NEW findings (default: dry run)")
    ap.add_argument("--min-severity", default="BUG",
                    choices=["BUG", "SHAPE", "CLEANUP", "UNCLASSIFIED"])
    ap.add_argument("--no-hardcode", action="store_true")
    ap.add_argument("--install-hint", action="store_true",
                    help="print the OS scheduler command for the FOREVER loop")
    ap.add_argument("--verify", action="store_true",
                    help="read the waiting list back and report it (the claim is "
                         "about the DB, so it is read from the DB)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.install_hint:
        print(TASK_HINT)
        return 0
    if args.verify:
        v = verify()
        if args.json:
            print(json.dumps(v, indent=1, ensure_ascii=False))
        else:
            print("queue by status        : %s" % v["queue_by_status"])
            print("pending WITH fingerprint: %d" % v["pending_with_fingerprint"])
            print("pending by rule         : %s" % v["by_rule"])
            print("pending by severity     : %s" % v["by_severity"])
            for s in v["sample"]:
                print("   %-5s %-6s %-44s %s"
                      % (s["severity"], s["rule"], s["cite_ref"],
                         s["fingerprint"]))
        return 0
    report = run_once(apply=args.apply, min_severity=args.min_severity,
                      include_hardcode=not args.no_hardcode)
    if args.json:
        print(json.dumps({k: v for k, v in report.items()
                          if k not in ("new_sample",)}, indent=1,
                         ensure_ascii=False))
    else:
        print("population     : %s" % report["population"])
        print("findings       : %s" % report["counts"])
        print("by severity    : %s" % report["by_severity"])
        print("already waiting: %d" % report["already_waiting"])
        print("NEW at %s : %s" % (report["min_severity"],
                                   report["new"]["by_severity"]))
        print("WHO FIXES IT: %s" % report["new"].get("by_owner_state"))
        print("ENTITY      : %s" % report["new"].get("by_entity_state"))
        print("applied        : %s" % report["applied"])
        print("forever        : %s" % report["forever"])
    return 0


if __name__ == "__main__":
    sys.exit(main())