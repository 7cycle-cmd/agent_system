"""assignee.py -- WHO will receive the task.

THE USER'S DESIGN (2026-09-24, verbatim)
----------------------------------------
    "environment setting Online + role-> onclick, so he will the one (identity)
     to have the task"

THE DESIGN
----------
The page shows **ENVIRONMENT (with its Online status) + ROLE**. Clicking a row
SELECTS that identity, and the selected identity is the one that will RECEIVE
the task.

WHY THE AXIS IS role x environment, NOT session
-----------------------------------------------
The user: "ui is wrong design , it should for role with enviornment not worker
with enviornment". A session list answers "which sessions exist"; the question
that decides whether a task can be given is "which ROLE, in which ENVIRONMENT,
is UP". So a candidate is a (role, environment) pair carrying its status.

WHY ONE ROW, NOT A LOG
----------------------
"who is the assignee NOW" is a CURRENT fact, not a history. A table that
appended a row per click would make the answer a MAX() over a growing table and
would leave several rows claiming to be the assignee. The table holds AT MOST
ONE ACTIVE ROW, enforced by a UNIQUE index on `slot` WHERE `is_active = 1`.

THE STATUS IS THE ENVIRONMENT STATUS
------------------------------------
A candidate's status comes from `environment_status.status_for`, so the page and
the Worker page cannot disagree about whether an environment is up. A channel
with NO app row is `UNKNOWN`, never `STOPPED`.

NEVER RAISES
------------
A selection read that can raise turns a page into an outage. Every failure is
returned as `ok: False` with a `why`.
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

# The single slot. A constant, so the UNIQUE index can enforce "at most one".
SLOT = 1

SOURCE = "assignee:role x environment -> identity_registry"


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent.

    MEASURED DEFECT (2026-09-24, in `computer_presence`): a reader that depends
    on the CALLER having set the row factory breaks the moment a new caller
    forgets. The reader sets it itself.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `assignee_selection` if absent. Idempotent."""
    import db_schema

    conn.executescript(db_schema.ASSIGNEE_SELECTION_DDL)
    conn.commit()
    return {"ok": True, "table": "assignee_selection"}


def candidates(conn: sqlite3.Connection) -> dict[str, Any]:
    """The role x environment rows, each carrying its ENVIRONMENT status.

    A candidate is built from the IDENTITIES that exist, grouped by
    (role, environment), because the identity is what a task is given to. The
    role is `UNASSIGNED` when `identity_registry.role_id` is NULL -- a role is a
    DECISION and is never defaulted.
    """
    _as_rows(conn)
    import environment_status as es

    rows = [dict(r) for r in conn.execute(
        "SELECT identity_id, session_id, channel, role_id "
        "FROM identity_registry WHERE is_active=1 ORDER BY identity_id DESC")]

    # ONE probe snapshot for every channel, so N rows cost ONE counter query.
    channels = sorted({str(r["channel"] or "") for r in rows if r["channel"]})
    st = es.status_all(conn, channels) if channels else {"by_channel": {}}
    by_channel = st.get("by_channel", {})

    # The role vocabulary, READ from `role_registry`. Never typed here.
    roles: dict[int, str] = {}
    try:
        for r in conn.execute(
                "SELECT rowid AS rid, role_key FROM role_registry "
                "WHERE is_active=1"):
            roles[int(r["rid"])] = str(r["role_key"])
    except sqlite3.OperationalError:
        roles = {}

    out: list[dict[str, Any]] = []
    for r in rows:
        ch = str(r["channel"] or "")
        s = by_channel.get(ch, {})
        rid = r["role_id"]
        out.append({
            "identity_id": int(r["identity_id"]),
            "session_id": str(r["session_id"] or ""),
            "channel": ch,
            "role_key": roles.get(int(rid)) if rid is not None else "UNASSIGNED",
            "role_assigned": rid is not None,
            "environment": s.get("app_name") or ch or "UNKNOWN",
            "environment_path": s.get("channel") or ch,
            "status": s.get("status") or "UNKNOWN",
            "status_why": s.get("why", ""),
            "status_app": s.get("app_name"),
            "status_process": s.get("process_name"),
            "status_count": s.get("count"),
        })
    by_status: dict[str, int] = {}
    by_role: dict[str, int] = {}
    for c in out:
        by_status[str(c["status"])] = by_status.get(str(c["status"]), 0) + 1
        by_role[str(c["role_key"])] = by_role.get(str(c["role_key"]), 0) + 1
    return {"ok": True, "candidates": out, "count": len(out),
            "by_status": by_status, "by_role": by_role,
            "role_vocabulary": sorted(set(roles.values())),
            "source": SOURCE}


def selected(conn: sqlite3.Connection) -> dict[str, Any]:
    """The CURRENT assignee, or a refusal when none is selected."""
    _as_rows(conn)
    row = conn.execute(
        "SELECT * FROM assignee_selection WHERE is_active=1 AND slot=?",
        (SLOT,)).fetchone()
    if row is None:
        return {"ok": True, "selected": None,
                "why": "no assignee is selected, so no identity will receive a "
                       "task"}
    return {"ok": True, "selected": dict(row)}


def select(conn: sqlite3.Connection, *, identity_id: int, cite_ref: str = "",
           commit: bool = True) -> dict[str, Any]:
    """Select ONE identity as the task assignee. REFUSES an unknown identity.

    The previous selection is SOFT-DELETED (`is_active=0`), never DELETEd: the
    repo's law is soft delete only, and the history of who was picked is
    evidence.
    """
    _as_rows(conn)
    try:
        iid = int(identity_id)
    except (TypeError, ValueError):
        return {"ok": False, "why": "identity_id must be an integer"}
    row = conn.execute(
        "SELECT identity_id, session_id, channel FROM identity_registry "
        "WHERE identity_id=? AND is_active=1", (iid,)).fetchone()
    if row is None:
        return {"ok": False, "identity_id": iid,
                "why": "identity_id %d is not an active identity, so it cannot "
                       "receive a task" % iid}
    # The role and the environment status are READ, not typed.
    import environment_status as es

    ch = str(row["channel"] or "")
    st = es.status_for(conn, ch)
    role_key = "UNASSIGNED"
    try:
        r = conn.execute(
            "SELECT rr.role_key FROM identity_registry ir "
            "JOIN role_registry rr ON rr.rowid = ir.role_id "
            "WHERE ir.identity_id=? AND rr.is_active=1", (iid,)).fetchone()
        if r is not None:
            role_key = str(r["role_key"])
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute(
            "UPDATE assignee_selection SET is_active=0, "
            "updated_at=datetime('now') WHERE is_active=1 AND slot=?", (SLOT,))
        conn.execute(
            "INSERT INTO assignee_selection "
            "(slot, identity_id, role_key, channel, environment, status, "
            " cite_ref) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (SLOT, iid, role_key, ch, st.get("app_name") or ch,
             st.get("status") or "UNKNOWN", str(cite_ref or "")))
        if commit:
            conn.commit()
    except Exception as exc:
        return {"ok": False, "identity_id": iid,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "identity_id": iid, "role_key": role_key,
            "channel": ch, "status": st.get("status") or "UNKNOWN",
            "why": "identity %d is now the assignee (role %s, environment %s)"
                   % (iid, role_key, st.get("app_name") or ch)}


def clear(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """Clear the selection (soft delete)."""
    _as_rows(conn)
    conn.execute(
        "UPDATE assignee_selection SET is_active=0, updated_at=datetime('now') "
        "WHERE is_active=1 AND slot=?", (SLOT,))
    if commit:
        conn.commit()
    return {"ok": True, "why": "no assignee is selected"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--candidates", action="store_true")
    ap.add_argument("--selected", action="store_true")
    ap.add_argument("--select", type=int, default=0)
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        ensure_schema(conn)
        if args.select:
            print(json.dumps(select(conn, identity_id=args.select,
                                    cite_ref="assignee.py:cli"),
                             indent=2, ensure_ascii=False))
            return 0
        if args.clear:
            print(json.dumps(clear(conn), indent=2, ensure_ascii=False))
            return 0
        if args.selected:
            print(json.dumps(selected(conn), indent=2, ensure_ascii=False))
            return 0
        print(json.dumps(candidates(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
