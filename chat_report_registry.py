# -*- coding: utf-8 -*-
"""chat_report_registry.py — a REPORT registers ITSELF, with its session id.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "why coding quality report task, and task still not show at
     http://127.0.0.1:18765/llm-tasks/conversation/list"
    "yes, do it now"
    "corn report need to auto with session ID（ｉｄｅｎｔｉｔｙ）ｔｏｏ"
    "ｉｔ　ｓｈｏｕｌｄ　ｂｅ　ｓｋｉｌｌ　ｆｏｒ　ａｕｔｏ　ｆｏｒｅｖｅｒ"

MEASURED, and this is the defect: the report is written to a FILE
(`qc_evidence/agent_log_<task_id>.md`) and the conversation list reads a TABLE
(`chat`, `mouse_spot_helper.py:13964`). **Nothing connects the two.** MEASURED
for the CODE.QUALITY session `d01a8339-a222-45d5-8c9c-f68f91c241b6`:

    GET /api/chat_center/history?session_id=d01a8339-...   -> total: 0
    GET /api/chat_identity/recent                          -> 0 rows
    GET /api/conversation_center/chats?q=d01a8339          -> 0 chats

So the report existed on disk and the list could not see it.

THE THREE THINGS THIS MODULE REFUSES TO DO
------------------------------------------
1. **It does not TYPE a session id.** The id is READ from the plan's own
   `**Session:**` line, using the SAME regex the gate uses
   (`scripts/plan_gate.py:365` `SESSION_RE`). A typed id is a claim; a read id
   is a fact.
2. **It does not AUTHOR a turn.** The turns are the report's own `## ` sections,
   in order. A summary written here would be a second, drifting copy of the
   report.
3. **It does not write `chat` or `chat_center_message`.** Both have exactly ONE
   writer (`chat_level.ensure_chat` at `chat_level.py:150`,
   `skill_library_api.create_chat_center_message` at `skill_library_api.py:1729`),
   and this module DELEGATES to them. A second writer is a second place that
   knows the rule, and the two drift.

IT REFUSES RATHER THAN INVENTS
------------------------------
A missing report, a missing plan, a plan with no `**Session:**` line, or a
session that is not a canonical UUID is a NAMED refusal. A silent skip would
make "nothing to register" and "the reader is broken" indistinguishable.

AUTO FOREVER IS A STATE TEST, NOT A COUNTER
-------------------------------------------
The rule already exists in this repo
(`skills/4_agent/skill_worker_auto_loop/contract.yaml`): *each pass must produce
a NEW DISTINCT CODE STATE; when no new state is producible, the loop STOPS.* So
`--all` registers every report that has no conversation row and then STOPS,
naming how many remained. A re-run is a no-op because the identity is
`(session_id, task_id)`.

Run:
    .\\.venv\\Scripts\\python.exe chat_report_registry.py --task CODE.QUALITY
    .\\.venv\\Scripts\\python.exe chat_report_registry.py --task CODE.QUALITY --apply
    .\\.venv\\Scripts\\python.exe chat_report_registry.py --all --apply
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import chat_level as cl  # noqa: E402  — the ONE writer of `chat`
import skill_library_api as sla  # noqa: E402  — the ONE writer of the turns

DB = BASE / "agent.db"
EVIDENCE = BASE / "qc_evidence"

# THE SAME REGEX THE GATE USES. A second pattern would let this module read a
# session the gate cannot, which is how two readers of one line drift apart.
SESSION_RE = re.compile(r"^\s*\*\*Session:\*\*\s*(.+?)\s*$", re.MULTILINE)
SESSION_SPLIT_RE = re.compile(r"[,\s]+")

# The canonical UUID the writer itself requires (`skill_library_api.SESSION_ID_RE`).
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# The chat key for a report. DERIVED from the task id, so the same task always
# gives the same key and a re-run is a no-op.
CHAT_KEY_PREFIX = "chat:report:"

# The `source` that RECORDS a purpose. MEASURED: `chat_identity_backfill.py:82`
# `SOURCE_TO_PURPOSE` holds exactly one entry, `chat_center`. A source that
# records no purpose is REFUSED by `register_chat_identity` (`PURPOSE_REQUIRED`),
# so this is not a free choice.
SOURCE = "chat_center"

# The status a registered report carries. `draft` = written down, NOT yet
# reviewed by the human — which is exactly what a report awaiting a read is.
STATUS = "draft"


class ReportRefused(RuntimeError):
    """Raised when a report cannot be registered. Carries a NAMED code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def plan_path(task_id: str) -> Path:
    return EVIDENCE / ("plan_%s.md" % task_id)


def report_path(task_id: str) -> Path:
    return EVIDENCE / ("agent_log_%s.md" % task_id)


def sessions_of_plan(path: Path) -> list[str]:
    """The session ids a plan DECLARES, in order, de-duplicated.

    READS the plan; never invents one. A plan with no `**Session:**` line
    declares NO session, and that is a refusal — the alternative ("no line ->
    accept anything") is the defect the gate's own scoping removes.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ReportRefused("PLAN_UNREADABLE",
                            "cannot read %s (%s: %s)" % (path, type(e).__name__, e))
    out: list[str] = []
    for raw in SESSION_RE.findall(text):
        for tok in SESSION_SPLIT_RE.split(raw.strip()):
            tok = tok.strip()
            if tok and tok not in out:
                out.append(tok)
    return out


def sections_of_report(path: Path) -> list[dict[str, str]]:
    """The report's own `## ` sections, in order, as (heading, body) pairs.

    THE TURNS ARE THE REPORT. A section is one ask (its heading) and one answer
    (its body). Nothing is summarised, so the registered conversation cannot
    disagree with the file a human reads.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise ReportRefused("REPORT_UNREADABLE",
                            "cannot read %s (%s: %s)" % (path, type(e).__name__, e))
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[dict[str, str]] = []
    head: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if head is None:
            return
        body = "\n".join(buf).strip()
        out.append({"heading": head, "body": body})

    for ln in lines:
        m = re.match(r"^##\s+(.*)$", ln)
        if m:
            flush()
            head = m.group(1).strip()
            buf = []
            continue
        if head is not None:
            buf.append(ln)
    flush()
    return out


def turns_from_sections(sections: list[dict[str, str]]) -> list[dict[str, str]]:
    """One ask + one answer per section. A section with no body is still an ask.

    MEASURED reason a body may be empty: a report can carry a heading whose
    content is a table that the splitter kept in the body, or a heading with
    nothing under it. An EMPTY answer is refused by the writer
    (`EMPTY_TURN`), so an empty body is reported as the heading alone rather
    than written as a blank turn.
    """
    turns: list[dict[str, str]] = []
    for s in sections:
        turns.append({"role": "Question", "content": s["heading"],
                      "title": s["heading"]})
        if s["body"]:
            turns.append({"role": "Answer", "content": s["body"]})
    return turns


def _already_registered(conn: sqlite3.Connection, session_id: str,
                        chat_key: str) -> dict[str, Any]:
    """Has THIS TASK's report been registered? The idempotency test.

    THE KEY IS THE TASK, NOT THE SESSION. MEASURED 2026-09-28, and this was a
    real defect in the first version: the test was "does the SESSION have
    turns", so the FIRST report of a session registered and the other 65 were
    reported ALREADY. MEASURED: session `9fc7ad2c-...` holds **66** reports, so
    209 reports produced only 16 chats. The unit of a report is a TASK; the unit
    of a conversation is a SESSION. They are not the same thing.

    THE TURNS ARE STILL PER SESSION, and that is the schema's own limit:
    `chat_main` declares `UNIQUE(session_id)` (`db_schema.py:4317`), so ONE
    session has ONE conversation, and `chat_center_message.chat_id` holds that
    conversation's id. So a session's turns are written ONCE, by the first
    report of that session; a later report of the same session creates its own
    CHAT (so the task is visible in the list) and writes NO turn, because the
    conversation it belongs to already holds them.
    """
    row = conn.execute("SELECT chat_id FROM chat WHERE chat_key = ?",
                       (chat_key,)).fetchone()
    n = conn.execute(
        "SELECT COUNT(*) FROM chat_center_message WHERE session_id = ?",
        (session_id,)).fetchone()[0]
    return {"chat_exists": row is not None,
            "chat_id": int(row["chat_id"]) if row else None,
            "session_turns": int(n)}


def _detect_ide() -> str:
    """The running IDE, from the ONE implementation. Never a guess.

    DELEGATES to `mouse_spot_helper._detect_ide` (`:10525`) — the module that
    already owns this rule. A second detector would let this module and the
    route disagree about which IDE a chat happened in. The import is LAZY
    because `mouse_spot_helper` is a large Flask module.
    """
    try:
        import mouse_spot_helper as msh
        return str(msh._detect_ide() or "")
    except Exception:
        return ""


def register_report(task_id: str, *, apply: bool = False,
                    db_path: str | Path | None = None) -> dict[str, Any]:
    """Register ONE report as a conversation. Returns a dict; never raises.

    Every refusal is a NAMED code in the returned dict, so a caller can tell a
    bad request from a dead reader.
    """
    tid = str(task_id or "").strip()
    if not tid:
        return {"ok": False, "error_code": "NO_TASK_ID",
                "error": "task_id is required"}
    pp, rp = plan_path(tid), report_path(tid)
    if not rp.is_file():
        return {"ok": False, "error_code": "REPORT_MISSING",
                "error": ("no report at %s — a report that does not exist "
                          "cannot be registered" % rp), "task_id": tid}
    if not pp.is_file():
        return {"ok": False, "error_code": "PLAN_MISSING",
                "error": ("no plan at %s — the session id is READ from the "
                          "plan, so a plan is required" % pp), "task_id": tid}
    sessions = sessions_of_plan(pp)
    if not sessions:
        return {"ok": False, "error_code": "NO_SESSION",
                "error": ("%s declares no `**Session:**` line, so there is no "
                          "identity to register under" % pp), "task_id": tid}
    bad = [s for s in sessions if not UUID_RE.match(s)]
    if bad:
        return {"ok": False, "error_code": "BAD_SESSION",
                "error": ("%s declares a session that is not a canonical UUID: "
                          "%s" % (pp, ", ".join(bad))), "task_id": tid}
    sid = sessions[0]
    sections = sections_of_report(rp)
    if not sections:
        return {"ok": False, "error_code": "NO_SECTIONS",
                "error": ("%s has no `## ` section, so it has no turn to "
                          "register" % rp), "task_id": tid}
    turns = turns_from_sections(sections)
    chat_key = CHAT_KEY_PREFIX + tid

    conn = sqlite3.connect(str(db_path or DB), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        cl.ensure_schema(conn)
        state = _already_registered(conn, sid, chat_key)
        # THE TASK IS THE UNIT. A chat that already exists for THIS task means
        # this report is registered — whatever the session's turn count is.
        if state["chat_exists"]:
            return {"ok": True, "already": True, "task_id": tid,
                    "session_id": sid, "chat_key": chat_key,
                    "chat_id": state["chat_id"],
                    "turns": state["session_turns"],
                    "sections": len(sections),
                    "why": ("this report is already registered: chat %s "
                            "(task %s)" % (state["chat_id"], tid))}
        if not apply:
            return {"ok": True, "applied": False, "task_id": tid,
                    "session_id": sid, "chat_key": chat_key,
                    "sections": len(sections), "turns": len(turns),
                    "session_turns": state["session_turns"],
                    "why": "report-only; pass --apply to write"}
        # ---- THE ORDER IS FORCED BY THE DATA, AND IT IS MEASURED ------------
        # `chat_center_message.chat_id` holds the CONVERSATION id
        # (`chat_main.id`), NOT the chat id. MEASURED: session `b1664979-...`
        # has `chat_main.id=71` and its turn carries `chat_id=71`, while its
        # CHAT is `chat_id=68`. So the conversation must EXIST before the turns
        # are written, and the chat is created and linked AFTERWARDS.
        #
        # 1. THE CONVERSATION + THE TURNS, through the ONE writer. This also
        #    mints the `chat_main` row the link needs.
        #
        #    THE TURNS ARE WRITTEN ONCE PER SESSION, NOT ONCE PER REPORT.
        #    MEASURED: `chat_main` declares `UNIQUE(session_id)`, so ONE session
        #    has ONE conversation, and a session holds up to 66 reports. Writing
        #    every report's turns would put 66 reports' text into one
        #    conversation and duplicate it on every re-run. So a session that
        #    ALREADY holds turns gets its chat and its link, and NO new turn.
        wrote_turns = False
        if state["session_turns"] == 0:
            res = sla.register_conversation(sid, turns, status=STATUS,
                                            source=SOURCE,
                                            ide=_detect_ide() or None)
            if not res.get("ok"):
                return {"ok": False, "error_code": "WRITE_FAILED",
                        "error": (res.get("error")
                                  or "register_conversation failed"),
                        "task_id": tid, "session_id": sid, "detail": res}
            wrote_turns = True
        # 2. THE CHAT, through the ONE writer. `ensure_chat` REFUSES a blank
        #    title, and the task id IS the title — a report is found by its task.
        ch = cl.ensure_chat(conn, chat_key, title=tid, source="report")
        chat_id = int(ch["chat"]["chat_id"])
        # 3. THE LINK, through the ONE writer — BUT ONLY IF THE SESSION IS NOT
        #    ALREADY LINKED. MEASURED 2026-09-28, and this was a real defect:
        #    `link_conversation` MOVES `chat_main.chat_id`, so registering 66
        #    reports of ONE session re-pointed the conversation 66 times and the
        #    LAST task won. MEASURED: `CODE.QUALITY` (registered first, 36 turns)
        #    ended up linked to `QC.GATE`, and only 18 of 260 chats held a link.
        #
        #    THE SCHEMA CANNOT EXPRESS "one session's conversation belongs to
        #    many chats": `chat_main` declares `UNIQUE(session_id)` and carries
        #    ONE `chat_id`. So the FIRST task to register a session keeps the
        #    link, and every later task of that session gets its own CHAT (so it
        #    is findable by name) with NO link. That limit is REPORTED, never
        #    hidden — a chat that silently lost its turns would be the defect
        #    this repo keeps paying for.
        existing = conn.execute(
            "SELECT chat_id FROM chat_main WHERE session_id = ?",
            (sid,)).fetchone()
        linked_to = int(existing["chat_id"]) if existing and existing["chat_id"] else None
        if linked_to is not None and linked_to != chat_id:
            return {"ok": True, "applied": True, "task_id": tid,
                    "session_id": sid, "chat_key": chat_key, "chat_id": chat_id,
                    "wrote_turns": wrote_turns,
                    "linked_to": linked_to,
                    "schema_limit": ("this session's conversation is already "
                                     "linked to chat %d; `chat_main` carries "
                                     "ONE chat_id, so this task's chat is "
                                     "created but NOT linked" % linked_to),
                    "why": ("registered chat %s for task %s; the session's "
                            "conversation stays linked to chat %d"
                            % (chat_id, tid, linked_to))}
        try:
            link = cl.link_conversation(conn, session_id=sid, chat_key=chat_key)
        except cl.ChatRefused as e:
            return {"ok": False, "error_code": "LINK_FAILED",
                    "error": str(e), "task_id": tid, "session_id": sid,
                    "chat_id": chat_id}
        return {"ok": True, "applied": True, "task_id": tid,
                "session_id": sid, "chat_key": chat_key, "chat_id": chat_id,
                "conversation_id": link.get("conversation_id"),
                "sections": len(sections), "turns": len(turns),
                "wrote_turns": wrote_turns,
                "why": ("registered chat %s for task %s; %s"
                        % (chat_id, tid,
                           ("wrote %d turn(s) from %d section(s) of %s"
                            % (len(turns), len(sections), rp.name))
                           if wrote_turns else
                           ("the session already holds %d turn(s), so NO turn "
                            "was written (one conversation per session)"
                            % state["session_turns"])))}
    finally:
        conn.close()


def all_reports() -> list[str]:
    """Every task id that has a report on disk, newest first. DERIVED, not listed."""
    out: list[tuple[float, str]] = []
    for p in EVIDENCE.glob("agent_log_*.md"):
        tid = p.name[len("agent_log_"):-len(".md")]
        if tid:
            out.append((p.stat().st_mtime, tid))
    out.sort(reverse=True)
    return [t for _m, t in out]


def register_all(*, apply: bool = False,
                 db_path: str | Path | None = None) -> dict[str, Any]:
    """Register every report that has no conversation row, then STOP.

    THE STOP CONDITION IS A STATE TEST. A pass may continue only while it
    produces a NEW DISTINCT CODE STATE (a report that was not registered
    becomes registered). When no new state is producible the loop STOPS and
    NAMES how many remained — never a counter, never "run it N times".
    """
    results: list[dict[str, Any]] = []
    registered = already = refused = 0
    for tid in all_reports():
        r = register_report(tid, apply=apply, db_path=db_path)
        results.append(r)
        if not r.get("ok"):
            refused += 1
        elif r.get("already"):
            already += 1
        elif r.get("applied"):
            registered += 1
    remaining = [r["task_id"] for r in results
                 if r.get("ok") and not r.get("already") and not r.get("applied")]
    return {
        "ok": True,
        "applied": bool(apply),
        "reports": len(results),
        "registered": registered,
        "already": already,
        "refused": refused,
        "remaining": len(remaining),
        "stop_reason": ("no report remains unregistered" if not remaining
                        else "%d report(s) still have no conversation row"
                             % len(remaining)),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task", help="one task id, e.g. CODE.QUALITY")
    ap.add_argument("--all", action="store_true",
                    help="every report on disk (state test, then STOP)")
    ap.add_argument("--apply", action="store_true",
                    help="write; without it this only REPORTS")
    args = ap.parse_args(argv)

    if args.all:
        out = register_all(apply=args.apply)
        print("reports=%d registered=%d already=%d refused=%d remaining=%d"
              % (out["reports"], out["registered"], out["already"],
                 out["refused"], out["remaining"]))
        print("STOP: %s" % out["stop_reason"])
        for r in out["results"]:
            if not r.get("ok"):
                print("  REFUSED %-40s %s: %s"
                      % (r.get("task_id"), r.get("error_code"), r.get("error")))
        return 0 if out["refused"] == 0 else 1

    if not args.task:
        ap.error("--task <id> or --all is required")
    out = register_report(args.task, apply=args.apply)
    if not out.get("ok"):
        print("REFUSED %s: %s" % (out.get("error_code"), out.get("error")))
        return 1
    if out.get("already"):
        print("ALREADY registered: chat %s holds %d turn(s)"
              % (out.get("chat_id"), out.get("turns")))
        return 0
    if not out.get("applied"):
        print("REPORT-ONLY: %d section(s) -> %d turn(s); pass --apply to write"
              % (out.get("sections"), out.get("turns")))
        return 0
    print("REGISTERED task=%s session=%s chat=%s conversation=%s turns=%d"
          % (out.get("task_id"), out.get("session_id"), out.get("chat_id"),
             out.get("conversation_id"), out.get("turns")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
