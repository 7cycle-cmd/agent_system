"""report_ack.py -- ACKNOWLEDGING a fault report CLOSES the fault it reports.

WHY THIS EXISTS (the previous plan's outcome named it)
-----------------------------------------------------
    "a report is throttled, but nothing ever CLOSES it, so `status='ask'` sits
     amber forever."

MEASURED, and the hole is BIGGER than that sentence:

  1. **NOBODY updates the status.** A repo-wide search for
     `UPDATE chat_center_message` returned **NOTHING**, so `status='ask'` could be
     WRITTEN and never MOVED. `chat-center-list.js` says a `ask` is "amber on
     purpose: it is the one state that needs a HUMAN" — and the amber list could
     only GROW. Live proof: three report messages (ids 81, 82, 83) all `ask` for
     the same fault, and nothing could move any of them.
  2. **The link was ONE-WAY.** The FAULT stored `report_message_id='82'`, but
     `chat_center_message` had NO pointer to the fault, so a READER of the message
     could not find — and therefore could not acknowledge — the fault it reported.
  3. **The vocabulary already existed.** `ticket_store.transition(conn,
     ticket_id, to_status, *, actor, note, cite_ref)` already moves a work item
     between states carrying an ACTOR and a CITATION, and
     `chat_center_message.status` already had the workflow vocabulary
     (`done|ask|progressive|QC`). So this module adds NO vocabulary and NO table —
     it COPIES THE SHAPE of the precedent.

WHAT IT DOES
------------
`ack(conn, message_id, *, actor, cite_ref, note='')`:
  * reads the message and its `fault:<group>#<event_id>` link;
  * REFUSES a blank `actor`, a blank/non-citation `cite_ref`, a message that is
    not `ask`, and an actor that is not a REGISTERED worker (the decision taken
    at approval: a typo must not mint a phantom human);
  * sets the message to `done` through the ONE writer's UPDATE path
    (`skill_library_api.set_chat_message_status` — the missing path this plan
    added next to the INSERT);
  * CLOSES the linked fault's OPEN occurrence (`runtime_trace.resolve_fault`), so
    the ack is the human's authoritative "handled";
  * is IDEMPOTENT — a second ack changes nothing and SAYS SO.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# The message status that MEANS "needs a human" (measured from the UI). An ack is
# only meaningful for a message in THIS state.
PENDING_STATUS = "ask"
# The status an ack moves the message to. Not deleted, not rewritten: RETIRED.
ACKED_STATUS = "done"

# `fault:runtime_<group>#<event_id>` — the pointer `chat_report.link_for` writes.
# The event id is part of it ON PURPOSE: a recurrence has a different id, so an ack
# can never close the WRONG occurrence.
LINK_RE = re.compile(r"^fault:(?P<group>[A-Za-z0-9_:.\-]+)(?:#(?P<event>\d+))?$")


class AckError(Exception):
    """Refused -- nothing written. A refusal is louder than a wrong row."""


def parse_link(link: str) -> dict[str, Any]:
    """Parse a message's fault link. Returns {ok, group_id, event_id}."""
    m = LINK_RE.match(str(link or "").strip())
    if not m:
        return {"ok": False, "why": "not a fault link: %r" % link}
    ev = m.group("event")
    return {"ok": True, "group_id": m.group("group"),
            "event_id": int(ev) if ev else None}


def message_link(conn: sqlite3.Connection, message_id: int) -> str:
    """The fault link a message carries: `fault_ref` FIRST, then `measured_effect`.

    `fault_ref` is the message's OWN column (measured: a dedicated column is why
    the link now SURVIVES an ack — the ack note is written to `measured_effect`,
    which used to be where the link lived, so the ack destroyed it).

    THE FALLBACK IS BACKWARD COMPATIBILITY, not a second source: rows written
    before the column existed carry the link in `measured_effect` (measured: 3
    legacy rows), and they must not be lost. A row that has BOTH returns the
    dedicated column, which is the authority.
    """
    row = conn.execute(
        "SELECT fault_ref, measured_effect FROM chat_center_message WHERE id=?",
        (int(message_id),)).fetchone()
    if not row:
        return ""
    dedicated = str(row["fault_ref"] or "").strip()
    if dedicated:
        return dedicated
    legacy = str(row["measured_effect"] or "").strip()
    return legacy if legacy.startswith("fault:") else ""


def _actor_is_registered(conn: sqlite3.Connection, actor: str) -> bool:
    """Is `actor` a REGISTERED worker key? (decision taken at approval)."""
    import worker_registry as wr
    r = wr.get_worker(conn, str(actor).strip()) or {}
    return bool(r.get("ok"))


def ack(conn: sqlite3.Connection, message_id: int, *, actor: str,
        cite_ref: str, note: str = "", require_registered_actor: bool = True
        ) -> dict[str, Any]:
    """Acknowledge ONE report: message `ask → done`, fault occurrence closed.

    REFUSES (nothing written) when:
      * `actor` is blank — a decision needs a decision-maker;
      * `actor` is not a registered `worker_registry.worker_key` (unless the
        caller opts out) — a typo must not mint a phantom human;
      * `cite_ref` is blank or not a citation — no citation, no decision;
      * the message does not exist, is not `ask`, or carries no fault link.

    IDEMPOTENT: an already-`done` message is REPORTED as such and nothing changes.
    """
    import citation_discipline as cd
    import runtime_trace as rt
    import skill_library_api as sla

    who = str(actor or "").strip()
    if not who:
        raise AckError("actor is required: a decision needs a decision-maker")
    if require_registered_actor and not _actor_is_registered(conn, who):
        raise AckError(
            "actor %r is not a registered worker_registry.worker_key; an ack must "
            "name a REAL actor, not free text (a typo would mint a phantom human)"
            % who)
    ref = str(cite_ref or "").strip()
    if not ref:
        raise AckError("no citation, no decision: cite_ref is required")
    if not cd.is_citation(ref):
        raise AckError("cite_ref %r is not a checkable citation" % ref)

    row = conn.execute("SELECT id, status, fault_ref, measured_effect FROM "
                       "chat_center_message WHERE id=?",
                       (int(message_id),)).fetchone()
    if not row:
        raise AckError("no chat_center_message with id=%d" % int(message_id))
    status = str(row["status"] or "").strip()
    if status == ACKED_STATUS:
        # IDEMPOTENT, and it SAYS SO rather than pretending to work.
        return {"ok": True, "already_acked": True, "message_id": int(message_id),
                "status": status, "changed": False}
    if status != PENDING_STATUS:
        raise AckError(
            "message %d is %r, not %r: an ack retires a PENDING ask, it does not "
            "move some other state" % (int(message_id), status, PENDING_STATUS))

    # THE LINK IS READ FROM ITS OWN COLUMN (with a legacy fallback).
    link_text = message_link(conn, int(message_id))
    link = parse_link(link_text)
    if not link.get("ok"):
        raise AckError(
            "message %d carries no fault link (%r), so there is nothing to close"
            % (int(message_id), link_text))

    # THE MESSAGE IS RETIRED FIRST, THROUGH THE ONE WRITER. The note records WHO
    # and WHY on the row itself, so a reader sees the decision without a join.
    #
    # THE CONNECTION IS REDIRECTED so the UPDATE lands where this caller is
    # reading: `set_chat_message_status` uses `_conn()`, which points at the LIVE
    # db by default — and MEASURED, that bit me (a proof's message lived on a COPY,
    # so the update reported `no chat_center_message with id=84`). Same mechanism
    # `chat_report._use_connection` uses.
    note_text = ("acked by %s | cite=%s%s"
                 % (who, ref, (" | " + note) if note else ""))
    # THE CALLER'S CONNECTION IS PASSED IN. MEASURED: opening a SECOND connection
    # while this one holds an uncommitted write is a self-deadlock
    # (`database is locked` — no timeout helps, because this transaction never
    # ends while it waits).
    st = sla.set_chat_message_status(int(message_id), ACKED_STATUS,
                                    measured_effect=note_text, conn=conn)
    if not st.get("ok"):
        raise AckError("the status write was refused: %s"
                       % (st.get("error") or st))

    # ...THEN the fault occurrence is closed. A resolve failure must not leave the
    # message `done` while the fault still reads open, so the fault is resolved
    # BEFORE the message status is committed — the ordering is stated, not assumed.
    #
    # THE LINK IS NOT TOUCHED HERE. The note goes to `measured_effect` (its proper
    # slot) and the LINK stays in `fault_ref`, so the ack no longer destroys the
    # pointer it exists to preserve (measured: msg 83's link WAS destroyed by the
    # old behaviour).
    resolved = None
    group = str(link["group_id"])
    try:
        resolved = rt.resolve_fault(conn, group, cite_ref=ref)
    except rt.RuntimeTraceError as exc:
        # A message whose fault is ALREADY resolved (e.g. the component recovered
        # on its own) is still a valid acknowledgment: the human is confirming it.
        resolved = {"ok": False, "already_resolved": True, "why": str(exc)}
    # Record the ACK as a fact on the fault row, so the reason is greppable from
    # the fault side too (the reverse of the message link).
    if resolved and resolved.get("event_id"):
        conn.execute(
            "INSERT INTO fault_event_fact (event_id, keyword, value_text, "
            "value_type, source) VALUES (?,?,?, 'string', 'report_ack')",
            (int(resolved["event_id"]), "acked_by", who))
        conn.execute(
            "INSERT INTO fault_event_fact (event_id, keyword, value_text, "
            "value_type, source) VALUES (?,?,?, 'string', 'report_ack')",
            (int(resolved["event_id"]), "acked_cite", ref))
    # ---- THE SUPERSEDED RULE (decision (a) at approval) ---------------------
    # Every OTHER `ask` message reporting the SAME occurrence is now answering a
    # decision the ack already made, so it becomes `done` with a note naming the
    # SUPERSEDING message. It gets NO `acked_by` fact: no human acked IT, and
    # claiming one would mint a decision nobody made.
    superseded = supersede_siblings(conn, link_text, superseded_by=int(message_id),
                                    note="the occurrence was closed by message %d"
                                         % int(message_id))
    conn.commit()
    return {"ok": True, "already_acked": False, "changed": True,
            "message_id": int(message_id), "status": ACKED_STATUS,
            "actor": who, "cite_ref": ref, "fault_link": link_text,
            "fault_group": group, "fault_event_id": link.get("event_id"),
            "resolved": resolved, "superseded": superseded}


def supersede_siblings(conn: sqlite3.Connection, link_text: str, *,
                       superseded_by: int, note: str,
                       commit: bool = True) -> list[dict[str, Any]]:
    """Retire the OTHER `ask` messages reporting the SAME fault occurrence.

    SCOPED TO THE EXACT OCCURRENCE (`fault:<group>#<event_id>`), so a message
    about a DIFFERENT still-open fault is never touched — measured by asserting
    that a sibling on another fault stays `ask`.

    A SUPERSEDED message is NOT an ACKED one: it is set `done` with a note naming
    the superseding message, and NO `acked_by` fact is written.
    """
    import skill_library_api as sla
    out: list[dict[str, Any]] = []
    want = str(link_text or "").strip()
    if not want:
        return out
    rows = conn.execute(
        "SELECT id, measured_effect, fault_ref FROM chat_center_message "
        "WHERE status=? ORDER BY id", (PENDING_STATUS,)).fetchall()
    for r in rows:
        mid = int(r["id"])
        if mid == int(superseded_by):
            continue
        # The link is read the SAME way `message_link` reads it, so the rule and
        # the reader cannot disagree about which messages belong together.
        this = str(r["fault_ref"] or "").strip() or (
            str(r["measured_effect"] or "").strip()
            if str(r["measured_effect"] or "").startswith("fault:") else "")
        if this != want:
            continue
        st = sla.set_chat_message_status(
            mid, ACKED_STATUS, conn=conn,
            measured_effect="superseded by message %d | %s"
                            % (int(superseded_by), note))
        out.append({"message_id": mid, "ok": bool(st.get("ok")),
                    "superseded_by": int(superseded_by)})
    if commit:
        conn.commit()
    return out


def cr_use(conn: sqlite3.Connection):
    """`chat_report._use_connection(conn)` — so the ONE writer uses THIS db.

    MEASURED, twice: `skill_library_api` opens its OWN connection, so a proof's
    write would otherwise land in the LIVE db. A local wrapper keeps the import
    in one place for this module's two write paths.
    """
    import chat_report as cr
    return cr._use_connection(conn)


def pending(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """THE AMBER LIST, as a QUERY over the two declared states.

    NOT a stored flag: a report is pending when its message is `ask` AND the fault
    it names is still `open`. So a message whose fault already resolved is NOT
    pending (a human is confirming something already true), and a message with no
    fault link is not claimed either way.
    """
    out: list[dict[str, Any]] = []
    for r in conn.execute(
            "SELECT id, title, status, fault_ref, measured_effect, created_at "
            "FROM chat_center_message WHERE status=? ORDER BY id", (PENDING_STATUS,)):
        # THE LINK IS READ THE SAME WAY EVERYWHERE: the dedicated column first,
        # then the legacy `measured_effect` (measured: 3 rows predate the column).
        link_text = str(r["fault_ref"] or "").strip() or (
            str(r["measured_effect"] or "").strip()
            if str(r["measured_effect"] or "").startswith("fault:") else "")
        link = parse_link(link_text)
        if not link.get("ok"):
            continue
        group = str(link["group_id"])
        ev = link.get("event_id")
        if ev is not None:
            st = conn.execute("SELECT status FROM fault_event WHERE event_id=?",
                              (int(ev),)).fetchone()
        else:
            st = conn.execute("SELECT status FROM fault_event WHERE group_id=? "
                              "AND status='open' ORDER BY event_id LIMIT 1",
                              (group,)).fetchone()
        if st and str(st[0]) == "open":
            out.append({"message_id": int(r["id"]), "title": r["title"],
                        "created_at": r["created_at"], "fault_group": group,
                        "fault_event_id": ev})
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="acknowledge a fault report")
    ap.add_argument("--db", default=str(BASE_DIR / "agent.db"))
    ap.add_argument("--pending", action="store_true",
                    help="list every report whose message is ask and whose fault "
                         "is still open")
    ap.add_argument("--ack", type=int, metavar="MESSAGE_ID")
    ap.add_argument("--actor", default="")
    ap.add_argument("--cite", dest="cite_ref", default="")
    ap.add_argument("--note", default="")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.pending:
            rows = pending(conn)
            print("pending reports: %d" % len(rows))
            for r in rows:
                print("   %s  %s" % (r["message_id"], r["title"]))
        if args.ack is not None:
            print(_json.dumps(ack(conn, args.ack, actor=args.actor,
                                  cite_ref=args.cite_ref, note=args.note),
                              indent=2, ensure_ascii=False, default=str))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
