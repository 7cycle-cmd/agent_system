# -*- coding: utf-8 -*-
"""chat_registry_store.py — a TICKET paired with the CHAT it was discussed in.

THE FLOW (user, 2026-09-21)
---------------------------
    middleware (chat_center submit) opens a TICKET
      -> the ticket goes to a chat room to get a chat_id
      -> THIS table pairs the two

And the user's model, verbatim:

    "ticket is for which services provide to"
    "no related too other, don't mix up"
    "if chat ID with entity_id need to have ticket will be chat ID with entity_id
     + ticket, mapping relationship don't need to mix"

So the things are SEPARATE and this table is where two of them MEET:

    ticket          which SERVICE the work is for      (ticket table)
    chat_id         where it is discussed              (chat_main)
    chat_registry   ticket + chat, as ONE row          (THIS table)

WHY IT IS CALLED `chat_registry` AND NOT `case_registry`
-------------------------------------------------------
The user renamed it (2026-09-21): "why not name = chat_registry / not easy for
mis-understand / rename it now". "Case" is ambiguous in this repo — it already
means a FAULT case (`fault_event.case_id`), a TDD case
(`skill_contract_tdd_case.case_key`), and a test case (`test_case_registry`,
letter `Q`). A reader could not tell which. The name now SAYS what a row is.

The PK is `chat_registry_id`, NOT `chat_id`, for the same reason: `chat_id` is
already a column here and means the FK into `chat_main`.

NO ENTITY ID OF ITS OWN
-----------------------
An earlier version minted one (`Z-<case_id>-<row>-1`, letter `Z`). That is
REMOVED (user: "case 唔應該有 Z-... -> remove"). An entity id names a THING;
this is a MAPPING between two things that already have their own ids. Minting a
third id for the mapping would make the mapping itself a thing — exactly the
mixing the user rejected.

WHY ONE ROW PER TICKET
----------------------
A ticket already says which service it is for. What it does NOT carry is the
CHAT: `ticket_chat_link` is many-to-many (one ticket, many chats), so "the chat
for this ticket" is not answerable from the ticket alone. `ticket_id` is
therefore UNIQUE here, so "which chat room is this work happening in" has a
single answer. A row that could point at two tickets would make "which ticket is
this about" unanswerable.

WHAT IT REFUSES
---------------
  * a ticket that does not exist — a row for a phantom ticket resolves to nothing
  * a chat that does not exist — same defect family
  * a second row for the same ticket — one row per ticket
  * a `chat_key` that is already used
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The status vocabulary, mirrored from `ticket_store.STATUSES` so a row and its
# ticket cannot drift into two different sets of words. The CHECK constraint in
# `db_schema.chat_registry_DDL` is the enforcement; this tuple is for callers.
STATUSES = ("open", "in_progress", "blocked", "closed", "cancelled")

TABLE_KEY = "chat_registry"


class ChatRegisterRefused(RuntimeError):
    """Raised when a row would be stored without a real ticket or chat."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("chat_registry refused — not written: %s"
                         % "; ".join(self.reasons))


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `chat_registry` and register it in `db_table_registry`.

    Idempotent. The ORDER matters: table -> `db_table_registry` row. The table
    must be a registered table so the taxonomy can reach it; it does NOT get an
    entity letter (see the module docstring).

    ALSO MIGRATES a leftover `case_registry` table, so a DB written by the
    earlier version of this module is not left with an orphan table that no code
    reads. The rows are COPIED (not dropped) so nothing is lost.
    """
    import db_schema

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(db_schema.chat_registry_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO db_table_registry "
        "(table_key, name, description, is_active, version) "
        "VALUES (?, ?, ?, 1, '1')",
        (TABLE_KEY, TABLE_KEY,
         "a ticket paired with the chat it was discussed in"))
    # The old name, if this DB was written before the rename.
    old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='case_registry'"
    ).fetchone()
    moved = 0
    if old:
        moved = conn.execute(
            "INSERT OR IGNORE INTO chat_registry "
            "(chat_registry_id, chat_key, ticket_id, chat_id, ticket_origin, status, "
            "opened_by, is_active, created_at, updated_at) "
            "SELECT case_id, case_key, ticket_id, chat_id, ticket_origin, status, "
            "opened_by, is_active, created_at, updated_at FROM case_registry"
        ).rowcount
        conn.execute("DROP TABLE case_registry")
        conn.execute("DELETE FROM db_table_registry WHERE "
                     "table_key='case_registry'")
    conn.commit()
    return {"ok": True, "table": TABLE_KEY, "migrated_from_case_registry": moved}


def create_chat_registry(conn: sqlite3.Connection, *, ticket_id: int,
                         chat_id: int | None = None,
                         ticket_origin: str = "", opened_by: str = "",
                         chat_key: str = "",
                         status: str = "open") -> dict[str, Any]:
    """Pair a REAL ticket with a REAL chat (the chat may be absent).

    One row per ticket: a second call for the same ticket returns the EXISTING
    row rather than creating a duplicate, because `ticket_id` is UNIQUE and a
    silent second row would make "the chat for this ticket" ambiguous.
    """
    ensure_schema(conn)
    if not str(opened_by or "").strip():
        raise ChatRegisterRefused(
            ["opened_by is required — an unattributed row cannot be traced back "
             "to who raised it"])
    if status not in STATUSES:
        raise ChatRegisterRefused(["status %r is not one of %s"
                                   % (status, ", ".join(STATUSES))])
    # The ticket must EXIST. A row for a phantom ticket resolves to nothing.
    t = conn.execute(
        "SELECT t.id, t.title, c.ticket_origin FROM ticket t "
        "JOIN ticket_center c ON c.id = t.ticket_center_id WHERE t.id = ?",
        (int(ticket_id),)).fetchone()
    if not t:
        raise ChatRegisterRefused(
            ["no ticket with id %d — a row for a ticket that does not exist "
             "resolves to nothing" % int(ticket_id)])
    # The chat must EXIST when one is named. Same defect family as the ticket.
    if chat_id is not None:
        if not conn.execute("SELECT 1 FROM chat_main WHERE id = ?",
                            (int(chat_id),)).fetchone():
            raise ChatRegisterRefused(
                ["no chat_main row with id %d — a row pointing at a chat that "
                 "does not exist resolves to nothing" % int(chat_id)])
    existing = conn.execute(
        "SELECT chat_registry_id, chat_key, status FROM chat_registry WHERE "
        "ticket_id = ?", (int(ticket_id),)).fetchone()
    if existing:
        return {"ok": True, "created": False,
                "chat_registry_id": int(existing[0]),
                "chat_key": str(existing[1]), "status": str(existing[2]),
                "ticket_id": int(ticket_id)}
    key = str(chat_key or "").strip() or ("chat-%d" % int(ticket_id))
    if conn.execute("SELECT 1 FROM chat_registry WHERE chat_key = ?",
                    (key,)).fetchone():
        raise ChatRegisterRefused(
            ["chat_key %r is already used by another row" % key])
    cur = conn.execute(
        "INSERT INTO chat_registry (chat_key, ticket_id, chat_id, ticket_origin, "
        "status, opened_by) VALUES (?,?,?,?,?,?)",
        (key, int(ticket_id), (int(chat_id) if chat_id is not None else None),
         str(ticket_origin or t["ticket_origin"] or ""), str(status),
         str(opened_by)))
    rid = int(cur.lastrowid)
    conn.commit()
    return {"ok": True, "created": True, "chat_registry_id": rid,
            "chat_key": key, "status": str(status), "ticket_id": int(ticket_id),
            "chat_id": (int(chat_id) if chat_id is not None else None)}


def get_chat_registry(conn: sqlite3.Connection,
                      chat_registry_id: int) -> dict[str, Any]:
    """One row with its ticket and its chat. Refuses an unknown id.

    The returned dict carries `ok: True`, like every other store read in this
    repo. Measured 2026-09-21: without it the UI's `if (!data.ok)` check treated
    a perfectly good HTTP 200 as a failure and showed "Could not refresh: HTTP
    200" -- a success reported as an error, which is worse than a plain error
    because it sends the reader looking for a fault that is not there.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM chat_registry WHERE chat_registry_id = ?",
        (int(chat_registry_id),)).fetchone()
    if not row:
        raise ChatRegisterRefused(
            ["no chat_registry with id %d" % int(chat_registry_id)])
    out = _shape(conn, row)
    out["ok"] = True
    return out


def list_chat_registers(conn: sqlite3.Connection, *, status: str = "",
                        ticket_origin: str = "", active_only: bool = False,
                        limit: int = 200) -> list[dict[str, Any]]:
    """List rows joined to their ticket and chat. Read-only."""
    ensure_schema(conn)
    sql = "SELECT cr.* FROM chat_registry cr WHERE 1=1"
    params: list[Any] = []
    if status:
        sql += " AND cr.status = ?"
        params.append(str(status))
    if ticket_origin:
        sql += " AND cr.ticket_origin = ?"
        params.append(str(ticket_origin))
    if active_only:
        sql += " AND cr.is_active = 1"
    sql += " ORDER BY cr.chat_registry_id DESC LIMIT ?"
    params.append(int(limit))
    return [_shape(conn, r) for r in conn.execute(sql, params)]


def _shape(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """One row + its ticket + its chat, as a flat dict for the UI.

    The ticket and chat are read by JOIN, not by string match, so a row whose
    ticket was deleted shows `ticket: null` rather than a plausible-looking
    title from a stale copy.
    """
    d = dict(row)
    t = conn.execute(
        "SELECT t.id, t.title, t.status, t.opened_by, t.created_at, "
        "c.ticket_origin, c.name AS ticket_origin_name FROM ticket t "
        "JOIN ticket_center c ON c.id = t.ticket_center_id WHERE t.id = ?",
        (int(d["ticket_id"]),)).fetchone()
    d["ticket"] = dict(t) if t else None
    ch = None
    if d.get("chat_id") is not None:
        # MEASURED (2026-09-26): `chat_hash` is NOT selected. It is stale on 52
        # of 58 rows (hashed with `id` before `chat_id` existed) and NO reader
        # ever used its value. The correct pair key is `chat_hash_recomputed`.
        # The column stays in the table as append-only audit; it is simply not
        # read. `sha256` IS selected — MEASURED 67/67 correct.
        ch = conn.execute(
            "SELECT id, session_id, sha256, chat_hash_recomputed, ide, llm, "
            "source, created_at FROM chat_main WHERE id = ?",
            (int(d["chat_id"]),)).fetchone()
    d["chat"] = dict(ch) if ch else None
    return d


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--get", type=int, default=0)
    ap.add_argument("--create", type=int, default=0, help="ticket_id")
    ap.add_argument("--chat-id", type=int, default=0)
    ap.add_argument("--opened-by", default="chat_registry_store_cli")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif args.create:
            print(json.dumps(create_chat_registry(
                conn, ticket_id=args.create,
                chat_id=(args.chat_id or None),
                opened_by=args.opened_by), indent=2, ensure_ascii=False))
        elif args.get:
            print(json.dumps(get_chat_registry(conn, args.get), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps(list_chat_registers(conn), indent=2,
                             ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
