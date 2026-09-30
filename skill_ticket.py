# -*- coding: utf-8 -*-
"""skill_ticket.py — a SKILL carries a service ticket, and the ticket is the job.

THE FLOW (user, 2026-09-21)
---------------------------
    新 skill 生成
      -> skill register will have the services ticket
      -> submit ticket for 100 run
    100 run        -> services provider = local LLM 7B
    visual analyze -> services provider = local LLM 7B

And on how the ticket reaches a worker:

    "send to chat id with ticket ID, so worker will have the job itself"

So the ticket is not a record of work already done — it IS the work order. It is
linked to a chat, and the worker picks the job up from there. That is why the
ticket carries a chat link rather than a new transport: the handoff mechanism
already exists.

WHY A SKILL CAN CARRY A TICKET
------------------------------
`entity_registry.py:168` registers `("S", "skill", "skill_registry", "skill_id",
"skill", 0)`, so `S-<skill_id>-1` is a VERIFIABLE entity id. `ticket_store`
requires a verified entity, so a skill ticket needs NO new id space and NO new
gate. This module REUSES `entity_id.require()`; it does not reimplement it — a
second parser would drift from the first, and the first is the one with the
register lookups.

WHAT IT REFUSES
---------------
  * a skill that is not in `skill_registry` — an unregistered skill has no
    identity, so a ticket for it would be a PHANTOM REFERENCE
  * a service that is not an active row in `ticket_center`
  * a chat that does not exist (delegated to `ticket_store.link_chat`)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import entity_id as eid  # noqa: E402
import entity_registry as er  # noqa: E402
import ticket_store as ts  # noqa: E402

DB = BASE / "agent.db"

# The ticket service that carries an LLM-service handoff. Registered in
# `ticket_center` by `ticket_store.DEFAULT_SERVICES`.
LLM_SERVICE = "llm_service"


class SkillTicketRefused(RuntimeError):
    """Raised when a ticket would be opened for a skill that does not exist."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("skill ticket refused — not written: %s"
                         % "; ".join(self.reasons))


def skill_entity_id(conn: sqlite3.Connection, skill_key: str) -> str:
    """The VERIFIABLE entity id for a skill: `S-<skill_id>-<version>`.

    FOUR parts, because that is the only entity id shape (2026-09-27):
      S            letter -> `skill_registry`
      <table_id>   the TABLE the letter's register lives in -> `db_table_registry`
      <row_id>     the skill's row in `skill_registry`  (the register's own pk)
      <version>    a row in `version_registry`

    THE HUMAN (2026-09-27), verbatim: "letter - table_id - row_id - version_id"
    / "3 is old, new version for entity is 4 part" / "version is the key to
    create mis-understand". The 3-part form is the OLD one.

    `table_id` IS LOOKED UP from the letter, never supplied, so it cannot be
    wrong. `row_id` IS the register's own pk -- there is NO `db_row_registry`.

    MEASURED 2026-09-21: `entity_type_registry` says letter `S` resolves its row
    against `skill_registry.skill_id`. So `S-79-50-1` was WRONG -- it asked for
    skill_id 79, which does not exist. The "table id" reading only applies to
    the `table_id` PART, which is `db_table_registry.db_table_id` of the
    letter's register table (79 for `skill_registry`).

    Refuses an unregistered skill. The id is built from the register's own
    `skill_id`, not from the key, because the key is a NAME and the id is the
    identity -- the same rule as `setting_ref_key_not_id`.
    """
    row = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                       (str(skill_key),)).fetchone()
    if not row:
        raise SkillTicketRefused(
            ["skill %r is not in skill_registry. An unregistered skill has no "
             "identity, so a ticket for it would be a phantom reference."
             % skill_key])
    skill_id = int(row[0])
    table_id = er.table_id_of_letter(conn, "S")
    return eid.format("S", table_id, skill_id, 1)


def open_for_skill(conn: sqlite3.Connection, skill_key: str, *,
                   service: str = LLM_SERVICE, title: str = "",
                   opened_by: str = "", note: str = "",
                   cite_ref: str = "", chat_id: int | None = None,
                   role: str = "work_order") -> dict[str, Any]:
    """Open a service ticket for a SKILL, optionally linked to a chat.

    The ticket IS the job. When `chat_id` is given the ticket is linked to that
    chat, so the worker picks the job up from the chat rather than from a new
    transport.

    THE ENTITY ID IS NO LONGER PART OF THE TICKET (2026-09-21). The user's rule:
    "ticket is for which services provide to / no related too other, don't mix
    up". So the skill's entity id is computed and RETURNED (it names the thing
    the ticket is about) but it is NOT written into the ticket row, and it is
    NOT what the ticket is deduped on. The skill is what the WORK is about; the
    ticket is which SERVICE does it.
    """
    ts.ensure_schema(conn)
    eumu = skill_entity_id(conn, skill_key)
    res = ts.create_ticket(
        conn, service=service,
        title=title or ("%s: %s" % (skill_key, service)),
        opened_by=opened_by or "skill_ticket",
        note=note or ("service ticket for skill %s" % skill_key),
        cite_ref=cite_ref or "skill_ticket.py:open_for_skill")
    out = {"ok": True, "skill_key": str(skill_key), "eumu_id": eumu,
           "service": service, "ticket_id": res.get("ticket_id"),
           "created": res.get("created"), "status": res.get("status")}
    if chat_id is not None:
        link = ts.link_chat(conn, int(res["ticket_id"]), int(chat_id),
                            role=role)
        out["chat_id"] = int(chat_id)
        out["chat_linked"] = bool(link.get("ok"))
    return out


def submit_100_run_ticket(conn: sqlite3.Connection, skill_key: str, *,
                          ref_tag: str, model: str = "",
                          opened_by: str = "", cite_ref: str = "",
                          chat_id: int | None = None) -> dict[str, Any]:
    """Submit a ticket for a 100 run. The title NAMES the SKILL and the ref_tag.

    A 100-run ticket that does not name what it measures cannot be traced back
    to the run, so the ref_tag is in the title rather than only in a note.

    THE SKILL NAME MUST BE IN THE TITLE TOO. DEFECT FOUND BY THE PROOF
    (2026-09-21): the title used to be just `100 run: <ref_tag>`. Since a ticket
    no longer carries an entity, `tickets_for_skill` finds a skill's tickets by
    TITLE PREFIX -- and a title without the skill name matched NOTHING, so every
    100-run ticket became unfindable the moment the entity was removed. The
    title now leads with the skill key, which is also the only place a reader
    can see which skill a run ticket belongs to.
    """
    title = "%s: 100 run: %s" % (skill_key, str(ref_tag))
    note = ("100-run proof for ref_tag=%s%s"
            % (ref_tag, (" via %s" % model) if model else ""))
    return open_for_skill(conn, skill_key, service=LLM_SERVICE, title=title,
                          opened_by=opened_by or "100_run_submit",
                          note=note,
                          cite_ref=cite_ref or "skill_ticket.py:submit_100_run_ticket",
                          chat_id=chat_id)


def tickets_for_skill(conn: sqlite3.Connection,
                      skill_key: str) -> list[dict[str, Any]]:
    """Every ticket opened for a skill, newest first.

    A ticket no longer carries an entity, so it cannot be queried BY entity.
    The skill's tickets are found by TITLE (`<skill_key>: ...`), which is what
    `open_for_skill` and `submit_100_run_ticket` both write. This is a NAME
    match and is honest about being one: `skill_entity_id` is still returned by
    the openers so a caller can see which thing the tickets are about.

    DEFECT FOUND BY THE PROOF (2026-09-21): `submit_100_run_ticket` used to
    write a title with NO skill name, so this returned nothing for exactly the
    tickets it exists to find. The title now leads with the skill key.
    """
    ts.ensure_schema(conn)
    rows = ts.list_tickets(conn, limit=1000)
    key = str(skill_key)
    return [r for r in rows if str(r.get("title") or "").startswith(key + ":")]


def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Skill service tickets")
    ap.add_argument("--skill", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--ref-tag", default="")
    ap.add_argument("--chat-id", type=int, default=None)
    ap.add_argument("--opened-by", default="cli")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.list and args.skill:
            out = tickets_for_skill(conn, args.skill)
            print(json.dumps(out, indent=2) if args.json else
                  "%d ticket(s) for %s" % (len(out), args.skill))
            return 0
        if args.open and args.skill:
            if args.ref_tag:
                res = submit_100_run_ticket(
                    conn, args.skill, ref_tag=args.ref_tag,
                    opened_by=args.opened_by, chat_id=args.chat_id)
            else:
                res = open_for_skill(conn, args.skill,
                                     opened_by=args.opened_by,
                                     chat_id=args.chat_id)
            print(json.dumps(res, indent=2))
            return 0
        ap.print_help()
        return 0
    except (SkillTicketRefused, ts.TicketRefused) as e:
        print("REFUSED: %s" % e)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
