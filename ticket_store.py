# -*- coding: utf-8 -*-
"""ticket_store.py — a TICKET is for a SERVICE.

THE USER'S MODEL (2026-09-21, verbatim)
---------------------------------------
    "ticket is for which services provide to"
    "no related too other, don't mix up"
    "chat / task is module? if yes"
    "id | ticket_id | module_id | is_active | created_at | updated_at"

THREE SEPARATE THINGS, and this module keeps them separate:

    ticket              which SERVICE the work is for      (this module)
    entity id           which THING      (`T-5-1-1`)        (entity_id.py)
    module              WHERE in the system               (module_registry)
    ticket_subject      ticket <-> subject (kind + ref)  (the mapping)

WHAT THIS MODULE USED TO DO, AND WHY IT WAS WRONG
-------------------------------------------------
A ticket used to REQUIRE a verified entity id, store it in four columns, and
make it part of the ticket's UNIQUE key. The user rejected that: a ticket is
about a SERVICE, so letting an entity define the ticket's identity is exactly
the mixing they named. Worse, it was BUGGY: the table never had an `entity_row`
column, so `T-1-5-1` and `T-1-6-1` were indistinguishable and the UNIQUE could
not tell two rows of one table apart. The entity columns are GONE.

`task_entity_link` remains the edge between a task and an entity. A ticket is a
THIRD thing: which service the work is for. It does not replace either.

MEASURED: `module_registry` holds 4 active rows — task_center (1),
mouse_spot_helper (2), openclaw_companion (3), llm_runtime (15115). `task` IS a
module (`task_center`); `chat` is NOT (no row, and "chat" is already the
capability `task_center.chat_identity`).

WHAT IT REFUSES
---------------
  * a `ticket_center_id` that is not an active row in `ticket_center`
  * a `module_id` that is not an active row in `module_registry`
  * a status transition that is not in `TRANSITIONS`
  * a `ticket_event` with no actor — an unattributed state change is not a trace
  * closing a ticket that is already closed

`ticket_event` is never UPDATEd and never DELETEd: the history GROWS, so the
path to the current state survives. A trace that can be rewritten is not a trace.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# The status vocabulary, and the transitions that are ALLOWED. A transition not
# listed here is REFUSED, so a status cannot be set to an arbitrary value and a
# closed ticket cannot silently reopen.
STATUSES = ("open", "in_progress", "blocked", "closed", "cancelled")
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("in_progress", "blocked", "closed", "cancelled"),
    "in_progress": ("blocked", "closed", "cancelled"),
    "blocked": ("in_progress", "closed", "cancelled"),
    "closed": (),          # terminal: a closed ticket does not reopen
    "cancelled": (),       # terminal
}
TERMINAL = ("closed", "cancelled")

# The services seeded on first run. A registry, not free text: a typo is refused
# rather than stored as a new service nobody owns.
DEFAULT_SERVICES: tuple[tuple[str, str, str], ...] = (
    ("chat_center", "Chat Center", "Tickets raised from the chat center flow."),
    ("task_center", "Task Center", "Tickets raised by the task pipeline."),
    ("manual", "Manual", "Tickets raised by a human, outside any pipeline."),
    ("llm_service", "LLM services",
     "Tickets raised when a local=0 LLM provider must serve a task. The ticket "
     "IS the handoff — no task_id/chat_id bridge is involved."),
)


class TicketRefused(RuntimeError):
    """Raised when a ticket would be stored with an unknown service/module."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("ticket refused — not written: %s"
                         % "; ".join(self.reasons))


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the tables and seed the service registry. Idempotent.

    CHANGED 2026-09-23: `ticket_module_map` is no longer created. It was folded
    into `ticket_subject` with `subject_kind='module'` (see
    `_migrate_ticket_module_map.py`), so there is ONE mapping table. Creating the
    old table here would resurrect it and give the repo two conventions again.
    """
    import db_schema
    import ticket_subject as ts

    conn.execute("PRAGMA foreign_keys = ON;")
    for ddl in (db_schema.TICKET_CENTER_DDL, db_schema.TICKET_DDL,
                db_schema.TICKET_EVENT_DDL, db_schema.TICKET_CHAT_LINK_DDL):
        conn.executescript(ddl)
    ts.ensure_schema(conn)
    added = 0
    for service, name, desc in DEFAULT_SERVICES:
        cur = conn.execute(
            "INSERT OR IGNORE INTO ticket_center (ticket_origin, name, description) "
            "VALUES (?,?,?)", (service, name, desc))
        added += cur.rowcount
    conn.commit()
    return {"ok": True, "services_added": added,
            "services": [str(r[0]) for r in conn.execute(
                "SELECT ticket_origin FROM ticket_center WHERE is_active=1 "
                "ORDER BY ticket_origin")]}


def services(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The ACTIVE service registry, read from the table (never hand-listed)."""
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT id, ticket_origin, name, description FROM ticket_center "
        "WHERE is_active=1 ORDER BY ticket_origin")]


def _service_id(conn: sqlite3.Connection, service: str) -> int:
    """Resolve a service NAME to its id, refusing an unknown one.

    A typo must fail LOUDLY: an unknown service stored as a new row would create
    a service nobody owns, and every ticket under it would be orphaned.
    """
    row = conn.execute("SELECT id FROM ticket_center WHERE ticket_origin=? AND "
                       "is_active=1", (str(service).strip(),)).fetchone()
    if not row:
        known = [str(r[0]) for r in conn.execute(
            "SELECT ticket_origin FROM ticket_center WHERE is_active=1")]
        raise TicketRefused(
            ["service %r is not an active row in ticket_center (known: %s). An "
             "unknown service would orphan every ticket under it."
             % (service, ", ".join(known) or "none")])
    return int(row[0])


def create_ticket(conn: sqlite3.Connection, *, service: str,
                  title: str = "", opened_by: str = "",
                  note: str = "", cite_ref: str = "",
                  module: str = "") -> dict[str, Any]:
    """Open a ticket for a SERVICE. Refuses an unknown service.

    NO ENTITY ID. A ticket is for a service (user, 2026-09-21: "ticket is for
    which services provide to / no related too other, don't mix up"). The
    entity is linked elsewhere, through the MAPPING, so this function does not
    know or care which thing the work is about.

    `module` (a `module_registry.module_key`, e.g. `task_center`) is OPTIONAL.
    When given it is written to `ticket_subject` with `subject_kind='module'`,
    which is the ONLY place a ticket and a subject meet (the old
    `ticket_module_map` was folded into it on 2026-09-23).
    """
    ensure_schema(conn)
    if not str(opened_by or "").strip():
        raise TicketRefused(["opened_by is required — an unattributed ticket "
                             "cannot be traced back to who raised it"])
    center_id = _service_id(conn, service)

    # Dedupe on (service, title) — the table's UNIQUE key, and nothing else.
    # The OLD key included the entity, so the same service+title for two
    # different entities created two tickets, and two rows of ONE table
    # collided (there was no entity_row column). Neither can happen now.
    existing = conn.execute(
        "SELECT id, status FROM ticket WHERE ticket_center_id=? AND title=?",
        (center_id, str(title or ""))).fetchone()
    if existing:
        tid = int(existing[0])
        out = {"ok": True, "created": False, "ticket_id": tid,
               "service": str(service), "status": str(existing[1])}
    else:
        cur = conn.execute(
            "INSERT INTO ticket (ticket_center_id, title, status, opened_by) "
            "VALUES (?,?,?,?)",
            (center_id, str(title or ""), "open", str(opened_by)))
        tid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO ticket_event (ticket_id, from_status, to_status, note, "
            "cite_ref, actor) VALUES (?,?,?,?,?,?)",
            (tid, "NA", "open", str(note or "ticket opened"),
             str(cite_ref or "NA"), str(opened_by)))
        conn.commit()
        out = {"ok": True, "created": True, "ticket_id": tid,
               "service": str(service), "status": "open"}

    if module:
        out["module"] = map_module(conn, tid, module,
                                   cite_ref=cite_ref or
                                   "ticket_store.py:create_ticket")
    return out


def _module_id(conn: sqlite3.Connection, module: str) -> int:
    """Resolve a module KEY to its id, refusing an unknown one.

    A typo must fail LOUDLY, exactly like `_service_id`: an unknown module
    stored as a new row would be a mapping pointing at a place that does not
    exist.
    """
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='module_registry'").fetchone():
        raise TicketRefused(
            ["module_registry is absent, so no module can be named"])
    row = conn.execute(
        "SELECT module_id FROM module_registry WHERE module_key=? AND "
        "is_active=1", (str(module).strip(),)).fetchone()
    if not row:
        known = [str(r[0]) for r in conn.execute(
            "SELECT module_key FROM module_registry WHERE is_active=1 "
            "ORDER BY module_key")]
        raise TicketRefused(
            ["module %r is not an active row in module_registry (known: %s). An "
             "unknown module would make the mapping point at nothing."
             % (module, ", ".join(known) or "none")])
    return int(row[0])


def map_module(conn: sqlite3.Connection, ticket_id: int, module: str, *,
               cite_ref: str = "") -> dict[str, Any]:
    """Map a ticket to a MODULE. A THIN WRAPPER over `ticket_subject`.

    CHANGED 2026-09-23 (user: "no, by ticket!! chat ticket, workflow ticket, task
    ticket and entity ticket ... not hardcode for rubbish"). `ticket_module_map`
    was the MODULE special case of "a ticket and its subject", and it was the
    ONLY mapping table, so a second kind had nowhere to go. It is now folded into
    `ticket_subject` with `subject_kind='module'` (see
    `_migrate_ticket_module_map.py`), and this function keeps its signature so
    existing callers are unaffected.

    The module KEY is stored as `subject_ref_id` (a key survives a rebuild; an id
    does not). `_module_id` is still called first, so an unknown module is
    REFUSED loudly rather than stored as a mapping to nothing.
    """
    import ticket_subject as ts

    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM ticket WHERE id=?",
                        (int(ticket_id),)).fetchone():
        raise TicketRefused(["no ticket with id %d — a mapping to a ticket that "
                             "does not exist resolves to nothing"
                             % int(ticket_id)])
    mid = _module_id(conn, module)
    # REVIVE, do not merely ignore. Measured 2026-09-21: after `unmap_module`
    # the row still exists with `is_active=0` (soft delete), so a bare
    # `INSERT OR IGNORE` found the row, did nothing, and the mapping stayed
    # INACTIVE -- re-mapping silently did not work. The row is therefore
    # updated back to active, and only a genuinely new pair is inserted.
    existing = conn.execute(
        "SELECT id, is_active FROM ticket_subject WHERE ticket_id=? AND "
        "subject_kind='module' AND subject_ref_id=?",
        (int(ticket_id), str(module).strip())).fetchone()
    if existing:
        conn.execute(
            "UPDATE ticket_subject SET is_active=1, "
            "updated_at=datetime('now') WHERE id=?", (int(existing[0]),))
        conn.commit()
        created = not bool(existing[1])
    else:
        ts.attach_subject(conn, ticket_id=int(ticket_id),
                          subject_kind="module",
                          subject_ref_id=str(module).strip(),
                          cite_ref=cite_ref or "ticket_store.py:map_module")
        created = True
    row = conn.execute(
        "SELECT m.module_key, m.name FROM module_registry m "
        "WHERE m.module_id=?", (mid,)).fetchone()
    return {"ok": True, "created": created,
            "ticket_id": int(ticket_id), "module": str(module),
            "module_id": mid,
            "module_name": str(row[1]) if row else ""}


def unmap_module(conn: sqlite3.Connection, ticket_id: int,
                 module: str) -> dict[str, Any]:
    """Remove a mapping. SOFT: `is_active=0`, never a DELETE.

    The repo's rule is soft delete only, so a removed mapping can be told from
    one that was never made. Now writes `ticket_subject` (see `map_module`).
    """
    ensure_schema(conn)
    _module_id(conn, module)
    cur = conn.execute(
        "UPDATE ticket_subject SET is_active=0, updated_at=datetime('now') "
        "WHERE ticket_id=? AND subject_kind='module' AND subject_ref_id=? "
        "AND is_active=1",
        (int(ticket_id), str(module).strip()))
    conn.commit()
    return {"ok": True, "removed": bool(cur.rowcount),
            "ticket_id": int(ticket_id), "module": str(module)}


def modules_for_ticket(conn: sqlite3.Connection,
                       ticket_id: int) -> list[dict[str, Any]]:
    """Every ACTIVE module mapped to a ticket. Read-only.

    Reads `ticket_subject` (see `map_module`).
    """
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT m.module_id, m.module_key, m.name, x.created_at "
        "FROM ticket_subject x JOIN module_registry m "
        "ON m.module_key = x.subject_ref_id "
        "WHERE x.ticket_id=? AND x.subject_kind='module' AND x.is_active=1 "
        "ORDER BY m.module_key",
        (int(ticket_id),))]


def transition(conn: sqlite3.Connection, ticket_id: int, to_status: str, *,
               actor: str, note: str = "", cite_ref: str = "") -> dict[str, Any]:
    """Move a ticket to a new status, APPENDING an event. Refuses an illegal move.

    The `ticket` row holds the CURRENT state; `ticket_event` holds the PATH. A
    single boolean cannot answer "how did this progress", which is the point.
    """
    ensure_schema(conn)
    if not str(actor or "").strip():
        raise TicketRefused(["actor is required — an unattributed state change "
                             "is not a trace"])
    row = conn.execute("SELECT status, is_active FROM ticket WHERE id=?",
                       (int(ticket_id),)).fetchone()
    if not row:
        raise TicketRefused(["no ticket with id %d" % int(ticket_id)])
    cur_status = str(row[0])
    if to_status not in STATUSES:
        raise TicketRefused(
            ["status %r is not one of %s" % (to_status, ", ".join(STATUSES))])
    if to_status not in TRANSITIONS.get(cur_status, ()):
        raise TicketRefused(
            ["%s -> %s is not an allowed transition (from %s: %s)"
             % (cur_status, to_status, cur_status,
                ", ".join(TRANSITIONS.get(cur_status, ())) or "terminal")])
    conn.execute(
        "UPDATE ticket SET status=?, is_active=?, updated_at=datetime('now'), "
        "closed_at=CASE WHEN ? IN ('closed','cancelled') "
        "THEN datetime('now') ELSE closed_at END WHERE id=?",
        (to_status, 0 if to_status in TERMINAL else 1, to_status,
         int(ticket_id)))
    conn.execute(
        "INSERT INTO ticket_event (ticket_id, from_status, to_status, note, "
        "cite_ref, actor) VALUES (?,?,?,?,?,?)",
        (int(ticket_id), cur_status, to_status, str(note or "NA"),
         str(cite_ref or "NA"), str(actor)))
    conn.commit()
    return {"ok": True, "ticket_id": int(ticket_id), "from": cur_status,
            "to": to_status, "is_active": to_status not in TERMINAL}


def link_chat(conn: sqlite3.Connection, ticket_id: int, chat_id: int,
              *, role: str = "discussion") -> dict[str, Any]:
    """Attach a chat to a ticket. One ticket, MANY chats.

    A `chat_id` COLUMN on `ticket` would allow only one chat and would silently
    drop the rest, so the link is its own table.
    """
    ensure_schema(conn)
    if not conn.execute("SELECT 1 FROM ticket WHERE id=?",
                        (int(ticket_id),)).fetchone():
        raise TicketRefused(["no ticket with id %d" % int(ticket_id)])
    if not conn.execute("SELECT 1 FROM chat_main WHERE id=?",
                        (int(chat_id),)).fetchone():
        raise TicketRefused(
            ["no chat_main row with id %d — a link to a chat that does not "
             "exist resolves to nothing" % int(chat_id)])
    cur = conn.execute(
        "INSERT OR IGNORE INTO ticket_chat_link (ticket_id, chat_id, role) "
        "VALUES (?,?,?)", (int(ticket_id), int(chat_id), str(role)))
    conn.commit()
    return {"ok": True, "created": bool(cur.rowcount),
            "ticket_id": int(ticket_id), "chat_id": int(chat_id)}


def tickets_for_module(conn: sqlite3.Connection, module: str, *,
                       active_only: bool = True) -> dict[str, Any]:
    """Every ticket mapped to a MODULE, by JOIN — not by string match.

    This REPLACES `tickets_for_entity`. A ticket no longer carries an entity
    (user, 2026-09-21: "ticket is for which services provide to / no related too
    other, don't mix up"), so "which tickets are about this entity" is no longer
    a question the ticket table can answer — and it should not try to. The
    question now answerable here is "which tickets belong to this module".

    The module is resolved first, so a typo returns a REFUSAL rather than an
    empty list that reads as "no tickets".
    """
    ensure_schema(conn)
    mid = _module_id(conn, module)
    sql = ("SELECT t.id, t.title, t.status, t.is_active, t.created_at, "
           "c.ticket_origin, c.name AS ticket_origin_name, m.module_key "
           "FROM ticket_subject x "
           "JOIN ticket t ON t.id = x.ticket_id "
           "JOIN ticket_center c ON c.id = t.ticket_center_id "
           "JOIN module_registry m ON m.module_key = x.subject_ref_id "
           "WHERE x.subject_kind = 'module' AND m.module_id = ?")
    if active_only:
        sql += " AND x.is_active = 1"
    sql += " ORDER BY t.id"
    rows = [dict(r) for r in conn.execute(sql, (mid,))]
    return {"ok": True, "module": str(module), "module_id": mid,
            "tickets": rows, "count": len(rows)}


def progress(conn: sqlite3.Connection, ticket_id: int) -> dict[str, Any]:
    """The FULL history of a ticket: what it is for, and how it moved.

    Also carries the ticket's MODULES, because "which service is this for" and
    "where does it happen" are two facts a reader of a ticket needs together —
    and they live in two tables, so they are joined HERE rather than by every
    caller.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT t.*, c.ticket_origin FROM ticket t JOIN ticket_center c "
        "ON c.id = t.ticket_center_id WHERE t.id=?", (int(ticket_id),)).fetchone()
    if not row:
        raise TicketRefused(["no ticket with id %d" % int(ticket_id)])
    events = [dict(r) for r in conn.execute(
        "SELECT from_status, to_status, note, cite_ref, actor, created_at "
        "FROM ticket_event WHERE ticket_id=? ORDER BY id", (int(ticket_id),))]
    chats = [dict(r) for r in conn.execute(
        "SELECT chat_id, role, created_at FROM ticket_chat_link "
        "WHERE ticket_id=? ORDER BY id", (int(ticket_id),))]
    mods = modules_for_ticket(conn, int(ticket_id))
    return {"ok": True, "ticket": dict(row), "events": events, "chats": chats,
            "modules": mods, "event_count": len(events)}


def list_tickets(conn: sqlite3.Connection, *, service: str = "",
                 status: str = "", active_only: bool = False,
                 limit: int = 200) -> list[dict[str, Any]]:
    """List tickets, optionally filtered. Read-only."""
    ensure_schema(conn)
    sql = ("SELECT t.id, t.title, t.status, t.is_active, "
           "t.created_at, c.ticket_origin FROM ticket t "
           "JOIN ticket_center c ON c.id = t.ticket_center_id WHERE 1=1")
    params: list[Any] = []
    if service:
        sql += " AND c.ticket_origin = ?"
        params.append(str(service))
    if status:
        sql += " AND t.status = ?"
        params.append(str(status))
    if active_only:
        sql += " AND t.is_active = 1"
    sql += " ORDER BY t.id DESC LIMIT ?"
    params.append(int(limit))
    return [dict(r) for r in conn.execute(sql, params)]


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
    ap.add_argument("--services", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--module", default="",
                    help="list tickets mapped to this module_key")
    ap.add_argument("--modules", action="store_true",
                    help="list the module registry")
    ap.add_argument("--progress", type=int, default=0)
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif args.services:
            print(json.dumps(services(conn), indent=2, ensure_ascii=False))
        elif args.modules:
            print(json.dumps([dict(r) for r in conn.execute(
                "SELECT module_id, module_key, name FROM module_registry "
                "WHERE is_active=1 ORDER BY module_key")],
                indent=2, ensure_ascii=False))
        elif args.module:
            print(json.dumps(tickets_for_module(conn, args.module), indent=2,
                             ensure_ascii=False))
        elif args.progress:
            print(json.dumps(progress(conn, args.progress), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps(list_tickets(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()


if __name__ == "__main__":
    main()