# -*- coding: utf-8 -*-
"""chat_worker_status.py -- the worker publishes its own state, so the copy button has a TRIGGER POINT.

THE HUMAN (2026-09-25), verbatim
--------------------------------
    "he copy button isn't in view (a valid state).
     when the reply didn't finish by worker, you will not have the copy button,
     the way is each conversaction ask worker to update status to chat, so we can
     have the trigger point when to click on copy button
     this is playwright too"

THE DEFECT THIS FIXES
---------------------
MEASURED `_proof_vscode_hotkey_coord.py`: the copy button check reports "the
copy icon is not under the cursor -> the response is not in view (a valid
state)". That is an OBSERVATION, not a diagnosis. It cannot distinguish:

    (a) the worker is STILL WRITING   -> the button does not exist yet
    (b) the response is ABOVE the fold -> the button exists but is scrolled away
    (c) the response finished          -> the button IS there; click it

The human names the missing fact exactly: there is no TRIGGER POINT.

WHY A STATUS AND NOT A GUESS
----------------------------
A copy button appears when the response is COMPLETE. "Complete" is a fact the
WORKER knows and the UI does not. So the worker PUBLISHES it and the playwright
step READS it. No polling, no fixed sleep, no guessing.

THE THREE STATES
----------------
    writing  the worker is still producing the reply. The copy button does NOT
             exist yet, so clicking is a guaranteed no-op. -> the copy step SKIPS
    done     the reply is complete. The copy button EXISTS. This is the ONLY
             state in which the copy step may run. -> the copy step RUNS
    failed   the turn ended without a reply. Nothing to copy. -> the copy step
             FAILS with the reason

Today all three look like "the copy button is not in view". That is the defect.

NEVER RAISES
------------
Every failure is returned as `ok: False` with a `why`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DEFAULT_DB = BASE / "agent.db"

SOURCE = "chat_worker_status:chat_worker_status"

NA = "NA"
NA_INT = -1

# THE TRIGGER POINT. `writing` and `done` are not two flavours of the same
# thing -- one means "the button does not exist", the other means "click it".
STATUSES = ("writing", "done", "failed")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS chat_worker_status (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT      NOT NULL,
    turn_no    INTEGER NOT NULL,
    -- THE TRIGGER POINT. `writing` -> the copy button does not exist yet;
    -- `done` -> it exists and the copy step may run; `failed` -> nothing to
    -- copy. A status outside these three is a typo, and a typo that reads as
    -- `done` would click a button that is not there.
    status     TEXT      NOT NULL
               CHECK (status IN ('writing', 'done', 'failed')),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cite_ref   TEXT      NOT NULL,
    UNIQUE (session_id, turn_no)
);
CREATE INDEX IF NOT EXISTS idx_chat_worker_status_session
    ON chat_worker_status (session_id, turn_no);
"""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table. Idempotent. Never raises."""
    try:
        conn.executescript(_CREATE_SQL)
        conn.commit()
        return {"ok": True, "tables": ["chat_worker_status"]}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def publish(conn: sqlite3.Connection, session_id: str, turn_no: int, *,
            status: str, cite_ref: str) -> dict[str, Any]:
    """Publish the worker's state for ONE turn.

    REFUSES:
      * a missing `session_id` -- a status that belongs to no conversation
        cannot gate any step
      * an unknown `status`     -- a typo that reads as `done` would click a
        button that is not there
      * a missing `cite_ref`    -- no citation, no row
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "code": "MISSING_SESSION_ID",
                "message": "a status must name the session it belongs to"}
    st = str(status or "").strip().lower()
    if st not in STATUSES:
        return {"ok": False, "code": "BAD_STATUS",
                "message": "status must be one of %s, got %r"
                           % (", ".join(STATUSES), status)}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no row"}
    try:
        turn = int(turn_no)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "turn_no must be an integer"}
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO chat_worker_status "
            "(session_id, turn_no, status, cite_ref) VALUES (?,?,?,?) "
            "ON CONFLICT(session_id, turn_no) DO UPDATE SET "
            "status=excluded.status, cite_ref=excluded.cite_ref, "
            "updated_at=CURRENT_TIMESTAMP",
            (sid, turn, st, str(cite_ref)))
        conn.commit()
        return {"ok": True, "session_id": sid, "turn_no": turn, "status": st}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def latest(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """The NEWEST turn's status for ONE session.

    The copy step gates on THIS, not on "any status ever published": an old
    `done` from a previous turn must not authorise a click on a turn that is
    still being written.

    THE `ACTIVE` SENTINEL. MEASURED (2026-09-25): resolving `ACTIVE` to "the
    newest session FILE" is UNRELIABLE -- several session files share an mtime
    to the second, so the tie is broken arbitrarily and the sentinel resolved to
    a DIFFERENT session than the one the worker published for. The human's own
    words give the correct definition: "each conversaction ask worker to update
    status to chat". The WORKER'S PUBLISH IS THE SIGNAL, so `ACTIVE` means "the
    newest published status", which is unambiguous.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "session_id": NA, "status": NA, "turn_no": NA_INT,
                "why": "no session_id given"}
    try:
        ensure_schema(conn)
        if sid.upper() == "ACTIVE":
            r = conn.execute(
                "SELECT session_id, turn_no, status, updated_at, cite_ref "
                "FROM chat_worker_status ORDER BY id DESC LIMIT 1").fetchone()
        else:
            r = conn.execute(
                "SELECT session_id, turn_no, status, updated_at, cite_ref "
                "FROM chat_worker_status WHERE session_id=? "
                "ORDER BY turn_no DESC LIMIT 1", (sid,)).fetchone()
        if not r:
            return {"ok": True, "session_id": sid, "status": NA,
                    "turn_no": NA_INT,
                    "why": "no status published for this session"}
        return {"ok": True, "session_id": str(r["session_id"]),
                "status": str(r["status"]),
                "turn_no": int(r["turn_no"]),
                "updated_at": str(r["updated_at"]),
                "cite_ref": str(r["cite_ref"]), "why": ""}
    except Exception as exc:
        return {"ok": False, "session_id": sid, "status": NA,
                "turn_no": NA_INT,
                "why": "%s: %s" % (type(exc).__name__, exc)}


def copy_gate(conn: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    """THE TRIGGER POINT: may the copy step run for this session?

    Returns {ok, action, status, why} where `action` is one of:
      * `run`  -- status is `done`; the copy button EXISTS
      * `skip` -- status is `writing`; the button does not exist yet, so the
                  step is NOT DUE (a skip, never a fail)
      * `fail` -- status is `failed`, or no status was ever published
    """
    r = latest(conn, session_id)
    st = str(r.get("status") or NA)
    if st == "done":
        return {"ok": True, "action": "run", "status": st,
                "turn_no": r.get("turn_no"), "why": ""}
    if st == "writing":
        return {"ok": True, "action": "skip", "status": st,
                "turn_no": r.get("turn_no"),
                "why": "the worker is still writing, so the copy button does "
                       "not exist yet"}
    if st == "failed":
        return {"ok": True, "action": "fail", "status": st,
                "turn_no": r.get("turn_no"),
                "why": "the turn ended without a reply, so there is nothing "
                       "to copy"}
    return {"ok": True, "action": "fail", "status": NA, "turn_no": NA_INT,
            "why": "no worker status was ever published for this session, so "
                   "the trigger point is UNKNOWN"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session-id", default="")
    ap.add_argument("--publish", default=None, help="writing|done|failed")
    ap.add_argument("--turn", type=int, default=1)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    if args.publish:
        out: Any = publish(conn, args.session_id, args.turn,
                           status=args.publish,
                           cite_ref="chat_worker_status.py:1")
    else:
        out = {"latest": latest(conn, args.session_id),
               "gate": copy_gate(conn, args.session_id)}
    conn.close()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
