"""chat_report.py -- THE MAPPER: a runtime fault + its factor becomes a
first-class CHAT message, not a toast.

WHY THIS EXISTS (the user, 2026-09-24)
--------------------------------------
    "report to module :chat to have the help"
    "factor X watchdog report -> chat title and content"
    "and why watchdog can have chat ID, identity, worker"

MEASURED answers, and each one says "the target already exists":

  1. There is NO `chat` module row. The chat system is `chat_main` (62 rows) +
     **`chat_center_message` (76 rows)** + `chat_registry` (0 rows) + the
     capability `task_center.chat_identity` / `openclaw.chat`, and the writer
     `skill_library_api.create_chat_center_message` (`:1729`).
  2. `chat_center_message` ALREADY carries the report's shape — measured real row
     (id 24): `role='Answer'`, `title='Optical-mouse drift is answerable
     natively: MSLLHOOKSTRUCT LLF_INJECTED'`, `event='discovery'`,
     `evidence_ref='mouse_event_probe.py:60-70 ...'`. So the format the user asks
     for is the table's OWN convention:
         title        = the FINDING (one line, never a topic)
         content      = the body
         event        = 'discovery' | 'lesson'   (the two the writer GATES on)
         evidence_ref = path:line OR a command that ran
  3. The WHY a toast is not enough is already in the code: `watchdog_health`
     wrote a fault ROW then did best-effort `try_notify`. A toast has no `title`,
     no `event`, no `evidence_ref` and no row, so it cannot be read back, cited
     or counted. THIS module turns the row into that message.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does NOT write `chat_center_message` itself. It calls the ONE existing writer,
so the citation gate (`event in ('discovery','lesson')` REQUIRES a non-empty
`evidence_ref`; an uncited finding is DISCARDED, never downgraded) stays in ONE
place. A second INSERT would be a second place that knows the rule, and the two
would drift — the defect family this repo keeps removing.

THE WRITER OPENS ITS OWN CONNECTION
-----------------------------------
MEASURED: `skill_library_api._conn()` connects to `AGENT_DB_PATH`. So `send()`
takes NO connection — and, for the same reason, a proof cannot redirect it to a
temp file. That is handled by asserting on the ROW THAT WAS RETURNED rather than
by re-querying a copy (see `_proof_chat_report.py`).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# The role a REPORT is written as. `chat_center_message.role` has exactly two
# values in the live table (measured: 'Question', 'Answer'); a report ANSWERS a
# fault, so it is an Answer.
REPORT_ROLE = "Answer"

# The event the writer GATES on. `discovery` is the live convention (measured: it
# is the ONLY event value in use), and it REQUIRES `evidence_ref`.
REPORT_EVENT = "discovery"

# `create_chat_center_message` refuses a `status` outside this vocabulary
# (measured: WORKFLOW_STATUSES = done | ask | progressive | QC). A report about a
# fault that is still open is 'ask' — it asks a human to act.
REPORT_STATUS = "ask"

TERM_KEY = "chat_fault_report"
TERM_DEFINITION = (
    "A FIRST-CLASS chat message that reports a runtime fault and the factor it "
    "violates: it carries a `title` (the finding, one line), `content` (the "
    "measured detail), `event='discovery'` and an `evidence_ref` that is "
    "checkable (a path:line or a command that ran). It is NOT a notification: a "
    "toast has no row, so it cannot be read back, cited or counted, while a "
    "chat_fault_report can."
)
TERM_CITE = "chat_report.py:1"


class ChatReportError(Exception):
    """Refused -- nothing written. A refusal is louder than a wrong row."""


def ensure_term(conn: sqlite3.Connection, *, commit: bool = True
                ) -> dict[str, Any]:
    """Register the `chat_fault_report` term, idempotently."""
    import terminology_registry as tr
    tr.ensure_schema(conn)
    existing = tr.get_term(conn, TERM_KEY)
    if existing:
        return {"ok": True, "created": False, "term_id": int(existing["term_id"]),
                "term_key": TERM_KEY}
    r = tr.add_term(conn, TERM_KEY, definition=TERM_DEFINITION, cite_ref=TERM_CITE,
                    term_kind="part", commit=commit)
    if not r.get("ok"):
        raise ChatReportError("the term %r could not be registered: %s"
                              % (TERM_KEY, r))
    return {"ok": True, "created": True, "term_id": int(r["term_id"]),
            "term_key": TERM_KEY}


def _count_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM chat_center_message"
                            ).fetchone()[0])


def title_for(ref_tag: str, factor_key: str, state: str, seconds: float | None
              ) -> str:
    """THE TITLE: the FINDING, one line, never a topic.

    The live convention (measured) is a SENTENCE that states what was found —
    `'Queue success means schema-valid, NOT correct (hallucination passes)'` —
    not a noun phrase. So the title says WHAT is wrong and HOW LONG it has been
    wrong, in the unit the measurement uses.
    """
    subject = str(ref_tag or "?").strip()
    st = str(state or "?").strip().upper()
    if seconds is None:
        return ("%s is %s and has written NO measurable evidence — factor %s is "
                "violated" % (subject, st, factor_key))
    return ("%s is %s for %.0fs — factor %s is violated (stale, not missing)"
            % (subject, st, float(seconds), factor_key))


def content_for(trace: dict[str, Any], *, factor: dict[str, Any] | None,
                worker_key: str = "", identity_key: str = "") -> str:
    """THE CONTENT: the measured detail, with the numbers AND their unit.

    A title alone is a claim; the content is where the READING lives, so the
    counts, the unit and the citation are all present.
    """
    lines = [
        "Runtime liveness fault reported by the keep-alive watchdog.",
        "",
        "subject      : %s" % trace.get("ref_tag"),
        "state        : %s" % trace.get("reason", "")[:200],
        "failure_count: %s" % trace.get("failure_count"),
        "total_count  : %s" % trace.get("total_count"),
        "factor       : %s | %s | target %s"
        % (trace.get("factor_key"),
           (factor or {}).get("metric_unit", "NA"),
           (factor or {}).get("metric_target", "NA")),
        "cite_ref     : %s" % trace.get("cite_ref"),
        "traced_by    : %s" % trace.get("traced_by"),
    ]
    if worker_key:
        lines.append("worker       : %s" % worker_key)
    if identity_key:
        lines.append("identity     : %s" % identity_key)
    lines += ["", "The report is the escalated form of the fault: a toast has no",
              "row to read back, this message does."]
    return "\n".join(lines)


def report_for_trace(conn: sqlite3.Connection, *, ref_tag: str,
                     factor_key: str | None = None, session_id: str = "",
                     worker_key: str = "", identity_key: str = ""
                     ) -> dict[str, Any]:
    """Build the `chat_center_message` fields for a `fault_factor_trace` row.

    READ-ONLY: it builds the dict; `send()` writes it. Refuses when there is no
    trace for `ref_tag` (a report about nothing is a fabricated finding).
    """
    sql = ("SELECT trace_id, trace_key, ref_tag, factor_key, failure_count, "
           "total_count, reason, cite_ref, traced_by, created_at "
           "FROM fault_factor_trace WHERE ref_tag=?")
    args: tuple = (ref_tag,)
    if factor_key:
        sql += " AND factor_key=?"
        args = (ref_tag, factor_key)
    traces = [dict(r) for r in conn.execute(sql, args)]
    if not traces:
        raise ChatReportError(
            "no fault_factor_trace row for ref_tag=%r%s: a report needs a "
            "measurement to report" % (ref_tag, " factor_key=%r" % factor_key
                                       if factor_key else ""))
    t = traces[0]
    factor = None
    if t.get("factor_key"):
        row = conn.execute(
            "SELECT factor_key, name, metric_unit, metric_target, rule_definition "
            "FROM skill_factor_registry WHERE factor_key=?",
            (t["factor_key"],)).fetchone()
        factor = dict(row) if row else None
    # The measured seconds live in the fault_event facts (the trace stores the
    # RATE; the fault stores the VALUE), so the title reads them from there.
    seconds = None
    ev = conn.execute(
        "SELECT event_id FROM fault_event WHERE group_id=? ORDER BY event_id DESC "
        "LIMIT 1", ("runtime_%s" % ref_tag,)).fetchone()
    if ev:
        # `seconds` is the standard key. `age_seconds` is accepted too, because
        # the live keep-alive wrote it before the two paths were reconciled
        # (MEASURED: the first version of `keepalive_runner._record_traces` used
        # `age_seconds` while `runtime_trace.record_alarm` used `seconds`, so the
        # SAME measurement had two keys and this lookup missed it).
        f = conn.execute(
            "SELECT value_text FROM fault_event_fact WHERE event_id=? AND "
            "keyword IN ('seconds','age_seconds') ORDER BY id LIMIT 1",
            (int(ev[0]),)).fetchone()
        if f and str(f[0]).strip() not in ("", "None"):
            try:
                seconds = float(f[0])
            except ValueError:
                seconds = None
    # PREFER THE OPEN ROW: the message reports ONE occurrence, and an ack closes
    # THAT occurrence. `ev` (newest) is the fallback for reading a resolved fault;
    # the OPEN id is what the link must name, so a recurrence (#higher id) can
    # never be closed by an ack meant for the earlier one.
    openrow = conn.execute(
        "SELECT event_id FROM fault_event WHERE group_id=? AND status='open' "
        "ORDER BY event_id LIMIT 1", ("runtime_%s" % ref_tag,)).fetchone()
    fault_event_id = int(openrow[0]) if openrow else (
        int(ev[0]) if ev else None)
    state = "DEAD" if "DEAD" in str(t.get("reason") or "").upper() else (
        "WEDGED" if "WEDGED" in str(t.get("reason") or "").upper() else "FAULT")
    return {
        "ok": True,
        "title": title_for(str(t["ref_tag"]), str(t["factor_key"]),
                           state, seconds),
        "content": content_for(t, factor=factor, worker_key=worker_key,
                               identity_key=identity_key),
        "event": REPORT_EVENT,
        "role": REPORT_ROLE,
        "status": REPORT_STATUS,
        "evidence_ref": str(t.get("cite_ref") or ""),
        "factor_key": str(t.get("factor_key") or ""),
        "ref_tag": str(t["ref_tag"]),
        "trace": t,
        "factor": factor,
        "fault_event_id": fault_event_id,
        "session_id": session_id,
        "chat_id": None,   # resolved by the caller from chat_main, or None
    }


# HOW THE CALLER'S CONNECTION REACHES THE ONE WRITER, and this is the honest shape:
# MEASURED — `skill_library_api._conn()` connects to `AGENT_DB_PATH`
# unconditionally, so a proof that called `send()` wrote into the LIVE DB even
# though every read used a COPY (three rows leaked: ids 78-80, `session_id IS
# NULL`).
#
# A LOCAL INSERT HERE WOULD BE A SECOND WRITER — my first fix did that and it
# violated this plan's own QC-08 (measured: TWO INSERT sites for
# `chat_center_message`). So the fix is the other way round: `send()` calls the
# ONE writer, and the CALLER supplies the connection by temporarily pointing the
# writer's connection factory at it. `_use_connection` owns that swap.
_WRITER_CONN: sqlite3.Connection | None = None


class _use_connection:
    """Context manager: make the ONE writer use a connection to `conn`'s DB.

    `None` leaves the writer alone (production: the live DB).

    IT OPENS A NEW CONNECTION TO THE SAME FILE rather than lending `conn`
    itself: MEASURED — `skill_library_api` closes the connection it is given
    (`finally: conn.close()`, e.g. `:200`), so lending the caller's handle left
    the CALLER with a closed database (`sqlite3.ProgrammingError: Cannot operate
    on a closed database`). Same FILE, different handle, so the caller keeps its
    own.
    """

    def __init__(self, conn: sqlite3.Connection | None) -> None:
        self._conn = conn
        self._saved = None
        self._opened: sqlite3.Connection | None = None

    def __enter__(self):
        global _WRITER_CONN
        if self._conn is not None:
            import skill_library_api as sla
            path = self._conn.execute("PRAGMA database_list").fetchone()[2]
            # `timeout` IS REQUIRED, not cosmetic. MEASURED: the caller may hold an
            # open write transaction (the ack resolves the fault, THEN sets the
            # status), and a second connection with the default 0s timeout raises
            # `database is locked` immediately. Same file, different handle — so
            # the lock is real and must be waited for.
            self._opened = sqlite3.connect(str(path), timeout=10)
            self._opened.row_factory = sqlite3.Row
            _WRITER_CONN = self._opened
            self._saved = _WRITER_CONN
            self._saved_factory = sla._conn
            sla._conn = lambda: self._opened   # type: ignore[assignment]
        return self

    def __exit__(self, *exc) -> None:
        global _WRITER_CONN
        if self._conn is not None:
            import skill_library_api as sla
            sla._conn = self._saved_factory   # type: ignore[assignment]
            _WRITER_CONN = None
            if self._opened is not None:
                try:
                    self._opened.close()
                except Exception:
                    pass
        return None


def send(report: dict[str, Any], *, session_id: str | None = None,
         chat_id: int | None = None,
         conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Write the report through the ONE existing writer. Returns {ok, id}.

    REFUSES an empty `evidence_ref` HERE as well as in the writer: a report with
    no checkable reference is an uncited finding, which citation-discipline
    DISCARDES rather than downgrades — so writing one would be the defect.
    """
    ref = str(report.get("evidence_ref") or "").strip()
    if not ref:
        raise ChatReportError(
            "no citation, no report: evidence_ref is empty. An uncited finding "
            "is DISCARDED at the write site, not downgraded.")
    title = str(report.get("title") or "").strip()
    content = str(report.get("content") or "").strip()
    if not title or not content:
        raise ChatReportError(
            "a report needs a NON-EMPTY title AND content (title=%r content "
            "length=%d)" % (title, len(content)))
    with _use_connection(conn):
        res = _call_the_one_writer(
            session_id=session_id or report.get("session_id") or None,
            chat_id=chat_id if chat_id is not None else report.get("chat_id"),
            role=str(report.get("role") or REPORT_ROLE),
            content=content,
            title=title,
            event=str(report.get("event") or REPORT_EVENT),
            evidence_ref=ref,
            # THE FAULT LINK, in ITS OWN COLUMN. MEASURED: the FAULT already
            # stores the reverse (`report_message_id`), but `chat_center_message`
            # had NO pointer to the fault, so a READER of the message could not
            # find — and therefore could not acknowledge — the fault it reported.
            #
            # IT IS `fault_ref`, NOT `measured_effect`: the link was first stored
            # in `measured_effect` and an ack NOTE was written into the same slot,
            # so the ack DESTROYED the link (measured 2026-09-24, msg 83). A note
            # is written over its column; a LINK must survive being used.
            #
            # THE COLUMN IS "THE LINK", NOT "THE FAULT LINK SPECIFICALLY"
            # (MEASURED 2026-09-28). `send()` hard-coded `link_for(report)`, so a
            # non-fault publisher's link was SILENTLY DISCARDED — and worse, the
            # write returned `ok:True`, so the notice bus published 17 rows that
            # were never findable (second pass re-published all 17: `on board = 0`
            # while `published = 17`). `link_for` is the RULE FOR A FAULT-SOURCED
            # report, so it is the FALLBACK, used when the caller supplies no link
            # of its own. Every existing caller is a fault report and sets none,
            # so their behaviour is unchanged.
            fault_ref=(str(report.get("fault_ref") or "").strip()
                       or link_for(report)),
            status=str(report.get("status") or REPORT_STATUS),
        )
    if not res.get("ok"):
        raise ChatReportError("the writer refused the report: %s"
                              % (res.get("error") or res))
    return {"ok": True, "id": res.get("id"), "row": res.get("row"),
            "fault_link": link_for(report)}


def link_for(report: dict[str, Any]) -> str:
    """The message→fault pointer, as a checkable token.

    FORMAT: `fault:runtime_<subject>#<event_id>` — a `group_id` plus the OPEN
    row's `event_id`, so a reader (and `report_ack`) can resolve THE EXACT
    occurrence the message reports, not merely its subject. A recurrence has a
    different `event_id`, so an ack can never close the wrong occurrence.
    """
    trace = report.get("trace") or {}
    gid = str(trace.get("ref_tag") or report.get("ref_tag") or "").strip()
    ev = report.get("fault_event_id")
    if not gid:
        return ""
    if ev is None:
        return "fault:runtime_%s" % gid
    return "fault:runtime_%s#%s" % (gid, ev)


def _call_the_one_writer(**kwargs: Any) -> dict[str, Any]:
    """The ONLY call site of `create_chat_center_message`.

    It exists so the connection swap in `_use_connection` is the ONLY variation:
    there is ONE writer, ONE INSERT (inside `skill_library_api`), and ONE call.
    """
    import skill_library_api as sla
    return sla.create_chat_center_message(**kwargs)


def report_and_send(conn: sqlite3.Connection, *, ref_tag: str,
                    factor_key: str | None = None, session_id: str = "",
                    worker_key: str = "", identity_key: str = "",
                    chat_id: int | None = None) -> dict[str, Any]:
    """Build + write ONE chat report ON THE CALLER'S CONNECTION.

    The `conn` is passed to `send()`, so the write lands where the caller is
    already reading. That is what keeps a proof's COPY honoured.
    """
    rep = report_for_trace(conn, ref_tag=ref_tag, factor_key=factor_key,
                           session_id=session_id, worker_key=worker_key,
                           identity_key=identity_key)
    out = send(rep, session_id=session_id or None, chat_id=chat_id, conn=conn)
    return {**out, "title": rep["title"], "event": rep["event"],
            "evidence_ref": rep["evidence_ref"], "ref_tag": rep["ref_tag"],
            "factor_key": rep["factor_key"],
            "fault_event_id": rep.get("fault_event_id"),
            "fault_link": out.get("fault_link")}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="chat fault report")
    ap.add_argument("--db", default=str(BASE_DIR / "agent.db"))
    ap.add_argument("--ensure", action="store_true",
                    help="register the chat_fault_report term")
    ap.add_argument("--preview", metavar="REF_TAG",
                    help="BUILD the report for a ref_tag and print it (no write)")
    ap.add_argument("--send", metavar="REF_TAG",
                    help="BUILD and WRITE the report for a ref_tag")
    ap.add_argument("--session", default="", help="session id for the message")
    ap.add_argument("--worker", default="", help="worker key to name")
    ap.add_argument("--identity", default="", help="identity key to name")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.ensure:
            print(ensure_term(conn))
        if args.preview:
            rep = report_for_trace(conn, ref_tag=args.preview,
                                   session_id=args.session,
                                   worker_key=args.worker,
                                   identity_key=args.identity)
            print(_json.dumps({k: v for k, v in rep.items()
                               if k in ("title", "content", "event", "role",
                                        "status", "evidence_ref")},
                              indent=2, ensure_ascii=False))
        if args.send:
            print(_json.dumps(report_and_send(
                conn, ref_tag=args.send, session_id=args.session,
                worker_key=args.worker, identity_key=args.identity),
                indent=2, ensure_ascii=False, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
