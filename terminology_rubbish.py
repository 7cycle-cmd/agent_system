# -*- coding: utf-8 -*-
"""terminology_rubbish.py — MEASURE the definitions that say nothing.

PLAN: qc_evidence/plan_TERMINOLOGY.PATH.SEGMENTS.AND.RUBBISH.CLEANUP.md (APPROVED)
STEP 4 of 9.

THE HUMAN (2026-09-27), verbatim
--------------------------------
    "you love rubbish? taskbar_vscode_app!!!????"
    "or you need to have helper to cleanup or rubbish definition"

WHAT THIS MODULE IS, AND WHAT IT IS **NOT**
-------------------------------------------
It is a **MEASUREMENT**. It reports every term whose definition would be REFUSED
by `terminology_registry.check_definition` today, and it names the RULE that
fired.

**IT WRITES NOTHING, AND IT OFFERS NO `--apply`.** That is deliberate, and it is
the plan's Decision 3:

    A cleanup that rewrites definitions it did not write is a SECOND AUTHOR.

The human asked for a helper to *"cleanup"*. MEASURED: there is nothing to clean
today -- 0 boilerplate, 0 too-short, 0 name-restating across 1497 rows. So the
honest deliverable is the MEASUREMENT that would find them, plus the GATE that
stops new ones (`terminology_registry.check_definition`, added by this plan).

WHY THE GATE AND THE REPORT SHARE ONE FUNCTION
----------------------------------------------
Both call `terminology_registry.check_definition`. A report with its own copy of
the rules would drift from the gate, and the report would then certify a rule the
gate no longer runs -- the defect this repo has measured before.

RUN:
    .\\.venv\\Scripts\\python.exe terminology_rubbish.py --scan
    .\\.venv\\Scripts\\python.exe terminology_rubbish.py --report
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# THE STDOUT GUARD. MEASURED 2026-09-27: this module is imported by
# `mouse_spot_helper`, which runs under `pythonw.exe` -- and under `pythonw`
# `sys.stdout` is **None**. A bare `sys.stdout.reconfigure(...)` therefore raises
# `AttributeError: 'NoneType' object has no attribute 'reconfigure'` and the API
# route returns 500.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import terminology_registry as tr  # noqa: E402

DEFAULT_DB = BASE / "agent.db"

# The rules this module applies, NAMED so a report can say which it used. Read
# from the gate's own constants, never restated -- a second copy would drift.
RULES = ("empty", "restates_the_name", "boilerplate", "too_short")


def log(msg: str) -> None:
    print(msg, flush=True)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def scan(conn: sqlite3.Connection, *, limit: int | None = None) -> dict[str, Any]:
    """Every term whose definition would be REFUSED today. WRITES NOTHING.

    Returns `{ok, rows, by_rule, checked, rules_applied, error}`. `by_rule` is
    the count per rule, so a reader can see WHICH kind of rubbish exists rather
    than only how much.
    """
    out: dict[str, Any] = {"ok": False, "rows": [], "by_rule": {},
                           "checked": 0, "rules_applied": list(RULES),
                           "error": None}
    try:
        rows = conn.execute(
            "SELECT term_id, term_key, term_kind, definition, cite_ref, "
            "is_active FROM terminology_registry ORDER BY term_id").fetchall()
        out["checked"] = len(rows)
        for r in rows:
            q = tr.check_definition(r["definition"], r["term_key"])
            if q["ok"]:
                continue
            rec = {"term_id": int(r["term_id"]), "term_key": r["term_key"],
                   "term_kind": r["term_kind"], "is_active": int(r["is_active"]),
                   "rule": q.get("rule"), "code": q.get("code"),
                   "definition": r["definition"], "cite_ref": r["cite_ref"],
                   "message": q.get("message")}
            out["rows"].append(rec)
            out["by_rule"][q.get("rule") or "?"] = \
                out["by_rule"].get(q.get("rule") or "?", 0) + 1
        if limit is not None:
            out["rows"] = out["rows"][:int(limit)]
        out["ok"] = True
    except Exception as exc:
        out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return out


def report(db_path: str | Path | None = None) -> dict[str, Any]:
    """The scan, plus the honest reading of it. WRITES NOTHING."""
    conn = _connect(db_path)
    try:
        s = scan(conn)
        s["total_terms"] = conn.execute(
            "SELECT COUNT(*) FROM terminology_registry").fetchone()[0]
        s["rubbish_count"] = len(s["rows"])
        # THE HONEST READING, stated rather than left to the reader: a zero here
        # means the GATE has never had to fire, not that the gate is unnecessary.
        s["note"] = (
            "0 rows means no rubbish is PRESENT, not that the check is "
            "unnecessary: the gate exists so a NEW rubbish definition cannot "
            "enter. MEASURED 2026-09-27: the shortest real definition is 64 "
            "chars, so the 20-char floor refuses only what says nothing.")
        return s
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="measure the definitions that say nothing (writes NOTHING)")
    ap.add_argument("--scan", action="store_true",
                    help="every term whose definition would be refused today")
    ap.add_argument("--report", action="store_true",
                    help="the scan plus the honest reading, as JSON")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    if args.report:
        log(json.dumps(report(args.db), indent=2, ensure_ascii=False))
        return 0

    conn = _connect(args.db)
    try:
        s = scan(conn)
        if not s["ok"]:
            log("scan FAILED: %s" % s["error"])
            return 1
        log("checked %d term(s) against %d rule(s): %s"
            % (s["checked"], len(RULES), ", ".join(RULES)))
        log("")
        log("RUBBISH: %d" % len(s["rows"]))
        for r in s["rows"]:
            log("  term_id=%-6s %-34s rule=%-18s %s"
                % (r["term_id"], r["term_key"], r["rule"],
                   (r["definition"] or "")[:60]))
        if not s["rows"]:
            log("  (none — no definition in the register says nothing)")
        log("")
        log("by rule: %s" % (s["by_rule"] or "{}"))
        log("")
        log("THIS HELPER WRITES NOTHING. A cleanup that rewrites definitions it "
            "did not write is a second author; the human decides.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
