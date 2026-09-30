# -*- coding: utf-8 -*-
"""ticket_subject.py — the GENERAL mapping: a ticket and its SUBJECT.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "no, by ticket!! chat ticket, workflow ticket, task ticket and entity ticket
     so we can have middleware for 5W1H, not hardcode for rubbish"

A ticket is the universal WORK ITEM. What it is ABOUT is its SUBJECT, and the
subject is a `(kind, ref)` pair. The KIND is an OPEN SET held in
`subject_kind_registry` — never a CHECK enum, so adding "chat" or "workflow" is
an INSERT and never a table rebuild.

WHY A MAPPING TABLE AND NOT A COLUMN ON `ticket`
------------------------------------------------
`db_schema.py:3487-3500` states the rule for the module case:

    "the three things are SEPARATE and this table is the only place they meet"

and `ticket_chat_link` (`db_schema.py:3549-3565`) exists for the same reason: a
`chat_id` COLUMN on `ticket` would allow only ONE chat and would silently drop
the rest. A `subject_kind` column would have exactly that defect — one subject
per ticket, the rest silently lost.

`ticket` itself deliberately carries NO entity columns; the removal is documented
as a FIX (`db_schema.py:3445-3460`). This module does not undo that: the subject
lives HERE, and `ticket` stays about the SERVICE.

ONE CONVENTION, NOT TWO
-----------------------
`ticket_module_map` is the module special case of this same idea. It is MIGRATED
into `ticket_subject` with `subject_kind='module'` (see
`_migrate_ticket_module_map.py`), and `ticket_store.map_module()` becomes a thin
wrapper, so existing callers keep working while there is only ONE mapping table.

REFUSALS (a row that resolves to nothing is worse than no row)
-------------------------------------------------------------
  * an unknown `subject_kind`        -> refused (a typo must fail LOUDLY, the
    same rule `ticket_store._module_id` applies)
  * a blank `subject_ref_id`         -> refused
  * a ticket that does not exist     -> refused
  * a `subject_kind` whose `ref_table` is known and whose ref does NOT exist
    -> refused (the FK graph must not lie while looking complete)

CLI
---
    python ticket_subject.py --attach --ticket-id 5 --kind chat --ref 12
    python ticket_subject.py --subjects-for-ticket 5
    python ticket_subject.py --tickets-for-subject chat 12
    python ticket_subject.py --list
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

TICKET_SUBJECT_DDL = """
CREATE TABLE IF NOT EXISTS ticket_subject (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id      INTEGER NOT NULL,
    subject_kind   TEXT    NOT NULL,
    -- THE IDENTITY of the subject kind, not its name. THE POLICY (human-locked):
    -- a native FK may only attach to the parent's PRIMARY KEY, and only for a
    -- MANDATORY + LOAD-BEARING reference. MEASURED 2026-09-29: the FK used
    -- `kind_key` while `subject_kind_registry`'s PK is `kind_id`, and the LOCAL
    -- column here is named `subject_kind` (not `kind_key`) -- two names guessed
    -- instead of read.
    kind_id        INTEGER,
    subject_ref_id TEXT    NOT NULL,
    role           TEXT    NOT NULL DEFAULT 'subject',
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    cite_ref       TEXT    NOT NULL DEFAULT 'NA',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    -- THE COMPOSITE KEY. One ticket may carry MANY subjects of DIFFERENT kinds
    -- (a chat ticket, a task ticket, an entity ticket), and the same subject may
    -- be attached under a different ROLE. This is the same shape
    -- `task_entity_link` uses (`UNIQUE (track_id, entity_type, entity_ref_id,
    -- version, role)`), so the repo has ONE convention for "a thing is linked to
    -- a thing".
    UNIQUE (ticket_id, subject_kind, subject_ref_id, role),
    FOREIGN KEY (ticket_id) REFERENCES ticket (id),
    FOREIGN KEY (kind_id) REFERENCES subject_kind_registry (kind_id)
);
CREATE INDEX IF NOT EXISTS idx_ticket_subject_ticket
  ON ticket_subject (ticket_id, is_active);
CREATE INDEX IF NOT EXISTS idx_ticket_subject_subject
  ON ticket_subject (subject_kind, subject_ref_id, is_active);
"""


class TicketSubjectRefused(RuntimeError):
    """Raised when a link would be stored that resolves to nothing."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("ticket_subject refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `ticket_subject` and register it in `db_table_registry`.

    ORDER matters (the rule `chat_registry_store.ensure_schema` records): the
    table first, then its `db_table_registry` row, so the taxonomy can reach it.
    """
    import db_schema
    import subject_kind_registry as skr

    conn.executescript(db_schema.TICKET_CENTER_DDL)
    conn.executescript(db_schema.TICKET_DDL)
    skr.ensure_schema(conn)
    conn.executescript(TICKET_SUBJECT_DDL)
    conn.execute(
        "INSERT OR IGNORE INTO db_table_registry "
        "(table_key, name, description, is_active, version) "
        "VALUES (?, ?, ?, 1, '1')",
        ("ticket_subject", "ticket_subject",
         "a ticket and its SUBJECT (kind + ref); the general mapping"))
    conn.commit()
    return {"ok": True, "table": "ticket_subject"}


def _ref_exists(conn: sqlite3.Connection, kind: dict[str, Any],
                ref: str) -> bool | None:
    """Does the ref exist in the kind's own table? None when not checkable.

    A kind whose `ref_table` is 'NA' (e.g. `entity`, whose ref is an entity id
    string) is NOT checkable here, so the answer is None — "no opinion", never a
    silent pass dressed as a check.
    """
    table = str(kind.get("ref_table") or "NA")
    column = str(kind.get("ref_column") or "NA")
    if table == "NA" or column == "NA":
        return None
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name=?", (table,)).fetchone():
        return None
    try:
        row = conn.execute(
            "SELECT 1 FROM %s WHERE %s = ?" % (table, column), (ref,)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return None


def attach_subject(conn: sqlite3.Connection, *, ticket_id: int,
                   subject_kind: str, subject_ref_id: str,
                   role: str = "subject", cite_ref: str = "") -> dict[str, Any]:
    """Attach ONE subject to a ticket. Idempotent on the UNIQUE key.

    Raises `TicketSubjectRefused` when the link would resolve to nothing.
    """
    import subject_kind_registry as skr

    ensure_schema(conn)
    reasons: list[str] = []

    kind = skr.validate_kind(conn, subject_kind)
    if not kind.get("ok"):
        reasons.append(kind.get("message") or "unknown subject_kind")

    ref = str(subject_ref_id or "").strip()
    if not ref:
        reasons.append("subject_ref_id is required — a link with no ref "
                       "resolves to nothing")

    if not conn.execute("SELECT 1 FROM ticket WHERE id=?",
                        (int(ticket_id),)).fetchone():
        reasons.append("no ticket with id %d" % int(ticket_id))

    if not reasons and kind.get("ok"):
        exists = _ref_exists(conn, kind, ref)
        if exists is False:
            reasons.append(
                "no %s row with %s=%r — a link to a subject that does not "
                "exist resolves to nothing"
                % (kind.get("ref_table"), kind.get("ref_column"), ref))

    if reasons:
        raise TicketSubjectRefused(reasons)

    existing = conn.execute(
        "SELECT id FROM ticket_subject WHERE ticket_id=? AND subject_kind=? "
        "AND subject_ref_id=? AND role=?",
        (int(ticket_id), str(subject_kind).strip(), ref, str(role))).fetchone()
    if existing:
        return {"ok": True, "created": False, "id": int(existing[0]),
                "ticket_id": int(ticket_id), "subject_kind": str(subject_kind),
                "subject_ref_id": ref, "role": str(role)}

    cur = conn.execute(
        "INSERT INTO ticket_subject (ticket_id, subject_kind, kind_id, subject_ref_id, "
        "role, cite_ref) VALUES (?, ?, (SELECT kind_id FROM subject_kind_registry "
        "WHERE kind_key=?), ?, ?, ?)",
        (int(ticket_id), str(subject_kind).strip(), str(subject_kind).strip(),
         ref, str(role),
         str(cite_ref or "NA")))
    rid = int(cur.lastrowid)
    conn.commit()
    return {"ok": True, "created": True, "id": rid, "ticket_id": int(ticket_id),
            "subject_kind": str(subject_kind), "subject_ref_id": ref,
            "role": str(role)}


def subjects_for_ticket(conn: sqlite3.Connection, ticket_id: int
                        ) -> list[dict[str, Any]]:
    """Every subject attached to a ticket, with its kind's display name."""
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT ts.id, ts.subject_kind, ts.subject_ref_id, ts.role, ts.is_active, "
        "ts.cite_ref, sk.display_name, sk.ref_table, sk.ref_column "
        "FROM ticket_subject ts "
        "LEFT JOIN subject_kind_registry sk ON sk.kind_key = ts.subject_kind "
        "WHERE ts.ticket_id = ? ORDER BY ts.subject_kind, ts.id",
        (int(ticket_id),)).fetchall()
    return [dict(r) for r in rows]


def tickets_for_subject(conn: sqlite3.Connection, subject_kind: str,
                        subject_ref_id: str) -> list[dict[str, Any]]:
    """Every ticket attached to a subject. Refuses an unknown kind.

    A typo returns a REFUSAL rather than an empty list that reads as "no
    tickets" — the same rule `ticket_store.tickets_for_module` applies.
    """
    import subject_kind_registry as skr

    ensure_schema(conn)
    kind = skr.validate_kind(conn, subject_kind)
    if not kind.get("ok"):
        raise TicketSubjectRefused([kind.get("message") or "unknown kind"])
    rows = conn.execute(
        "SELECT ts.id, ts.ticket_id, ts.role, ts.is_active, t.title, t.status, "
        "c.ticket_origin FROM ticket_subject ts "
        "JOIN ticket t ON t.id = ts.ticket_id "
        "JOIN ticket_center c ON c.id = t.ticket_center_id "
        "WHERE ts.subject_kind = ? AND ts.subject_ref_id = ? "
        "ORDER BY ts.ticket_id",
        (str(subject_kind).strip(), str(subject_ref_id).strip())).fetchall()
    return [dict(r) for r in rows]


def list_rows(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT id, ticket_id, subject_kind, subject_ref_id, role, is_active "
        "FROM ticket_subject ORDER BY id DESC LIMIT ?",
        (max(1, int(limit)),)).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--attach", action="store_true")
    ap.add_argument("--ticket-id", type=int, default=0)
    ap.add_argument("--kind", default="")
    ap.add_argument("--ref", default="")
    ap.add_argument("--role", default="subject")
    ap.add_argument("--cite-ref", default="")
    ap.add_argument("--subjects-for-ticket", type=int, default=0)
    ap.add_argument("--tickets-for-subject", nargs=2, default=None,
                    metavar=("KIND", "REF"))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.attach:
            try:
                out = attach_subject(
                    conn, ticket_id=args.ticket_id, subject_kind=args.kind,
                    subject_ref_id=args.ref, role=args.role,
                    cite_ref=args.cite_ref)
            except TicketSubjectRefused as e:
                print(json.dumps({"ok": False, "refused": True,
                                  "reasons": e.reasons}, ensure_ascii=False))
                return 1
            print(json.dumps(out, ensure_ascii=False))
            return 0

        if args.subjects_for_ticket:
            print(json.dumps(
                {"ok": True, "ticket_id": args.subjects_for_ticket,
                 "subjects": subjects_for_ticket(conn,
                                                 args.subjects_for_ticket)},
                ensure_ascii=False, indent=2))
            return 0

        if args.tickets_for_subject:
            kind, ref = args.tickets_for_subject
            try:
                rows = tickets_for_subject(conn, kind, ref)
            except TicketSubjectRefused as e:
                print(json.dumps({"ok": False, "refused": True,
                                  "reasons": e.reasons}, ensure_ascii=False))
                return 1
            print(json.dumps({"ok": True, "subject_kind": kind,
                              "subject_ref_id": ref, "tickets": rows},
                             ensure_ascii=False, indent=2))
            return 0

        if args.list:
            print(json.dumps({"ok": True, "rows": list_rows(conn)},
                             ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
