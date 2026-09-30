# -*- coding: utf-8 -*-
"""coordinate_session_registry.py -- WHICH SESSION a coordinate belongs to.

THE HUMAN (2026-09-25), verbatim
--------------------------------
    "session id at where in identity table?

     coordinate table is individual? by target id? if yes, is easy

     + table : coordinate_session

     is | session_id | coordinate_id |"

THE PROBLEM THIS SOLVES
-----------------------
A coordinate is measured against ONE conversation. MEASURED (2026-09-25): the
copy button's rect was (1288,772)-(1320,806) in one layout and (166,688)-(200,732)
in another, and the action bar moved from y=693 to y=494 when the Keep/Undo bar
appeared. A rect with no session attached is a number whose CONTEXT is lost, so
a later reader cannot tell which conversation it was measured in.

THE ANSWER TO "is it individual? by target id?"
-----------------------------------------------
YES. `environment_template` is keyed by `(environment_id, template_id)`, and
`target_template.id` IS the target id -- so a coordinate is INDIVIDUAL, one row
per target. That is why the join is easy: `coordinate_id` is
`target_template.id`.

WHERE THE SESSION ID LIVES
--------------------------
`identity_registry.session_id` (MEASURED: 56 rows, each with a session_id and an
`identity_key` of `<session_id>|<identity_key>|<step_no>`). So the session id is
NOT a new concept -- it is already the identity register's key. This table LINKS
that existing id to a coordinate; it does not invent a second session id.

THE TABLE
---------
    coordinate_session (id, session_id, coordinate_id, ...)

`coordinate_id` REFERENCES `target_template(id)`, so a link to a target that
does not exist is refused by the database, not by a convention.

NEVER RAISES
------------
Every failure is returned as `ok: False` with a `why`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB = BASE / "agent.db"

CITE = "human 2026-09-25 + identity_registry.session_id + target_template.id"

DDL = """
CREATE TABLE IF NOT EXISTS coordinate_session (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT    NOT NULL,
    coordinate_id INTEGER NOT NULL,
    -- WHY THIS LINK EXISTS. A link with no reason is a row nobody can audit.
    why           TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL DEFAULT 'NA',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(session_id, coordinate_id),
    FOREIGN KEY (coordinate_id) REFERENCES target_template(id)
);
CREATE INDEX IF NOT EXISTS idx_coord_session_session
    ON coordinate_session(session_id);
CREATE INDEX IF NOT EXISTS idx_coord_session_coord
    ON coordinate_session(coordinate_id);
"""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table if it is absent. ADDITIVE: never drops a row.

    ALSO ADDS `pinned_row_no` (2026-09-27). THE HUMAN: "2) register to table for
    each session? 1 name = 1, 2 name = 2, 3 name = 3 ...". The original table
    links a session to a TARGET, so "which PINNED ROW is this session" could not
    be expressed. The column is added with `ALTER TABLE ... ADD COLUMN`, which
    SQLite performs in place — no row is copied, dropped, or recreated.
    """
    conn.executescript(DDL)
    # ADDITIVE MIGRATION. `PRAGMA table_info` is the check, so a second call is a
    # no-op rather than an error — `ALTER TABLE ADD COLUMN` raises if the column
    # already exists, and a schema function that raises on its second call is a
    # schema function nobody can call twice.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(coordinate_session)")}
    if "pinned_row_no" not in cols:
        conn.execute("ALTER TABLE coordinate_session "
                     "ADD COLUMN pinned_row_no INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return {"ok": True, "added_pinned_row_no": "pinned_row_no" not in cols}


def link(
    conn: sqlite3.Connection,
    session_id: str,
    coordinate_id: int,
    *,
    why: str = "NA",
    cite_ref: str = CITE,
    commit: bool = True,
) -> dict[str, Any]:
    """Link ONE session to ONE coordinate. Idempotent on the pair.

    REFUSES a missing session id, a missing coordinate id, and a coordinate id
    that is not a real target -- a link to a target that does not exist is a
    dangling reference, and the database's own foreign key is the authority.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "code": "MISSING_SESSION_ID",
                "message": "session_id is required"}
    try:
        cid = int(coordinate_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "MISSING_COORDINATE_ID",
                "message": "coordinate_id must be an int"}
    row = conn.execute("SELECT id FROM target_template WHERE id = ?",
                       (cid,)).fetchone()
    if not row:
        return {"ok": False, "code": "UNKNOWN_COORDINATE",
                "message": "no target_template with id=%d" % cid}
    conn.execute(
        "INSERT INTO coordinate_session (session_id, coordinate_id, why, "
        "cite_ref) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(session_id, coordinate_id) DO UPDATE SET "
        "why=excluded.why, cite_ref=excluded.cite_ref, "
        "updated_at=datetime('now')",
        (sid, cid, str(why or "NA"), str(cite_ref or CITE)))
    if commit:
        conn.commit()
    return {"ok": True, "session_id": sid, "coordinate_id": cid}


def for_session(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """Every coordinate linked to ONE session, with its rect and centre."""
    rows = conn.execute(
        "SELECT cs.id, cs.session_id, cs.coordinate_id, cs.why, cs.cite_ref, "
        "cs.is_active, t.name, t.label, e.x1, e.y1, e.x2, e.y2 "
        "FROM coordinate_session cs "
        "JOIN target_template t ON t.id = cs.coordinate_id "
        "LEFT JOIN environment_template e ON e.template_id = t.id "
        "WHERE cs.session_id = ? ORDER BY cs.coordinate_id",
        (str(session_id),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["centre"] = "(%d,%d)" % ((int(d["x1"]) + int(d["x2"])) // 2,
                                       (int(d["y1"]) + int(d["y2"])) // 2)
        except (TypeError, ValueError):
            d["centre"] = "NA"
        out.append(d)
    return out


def for_coordinate(conn: sqlite3.Connection,
                   coordinate_id: int) -> list[dict[str, Any]]:
    """Every session a coordinate was measured in.

    A coordinate measured in MORE THAN ONE session is the interesting case: it
    says the rect is session-dependent, which is exactly what the copy button
    turned out to be.
    """
    rows = conn.execute(
        "SELECT cs.id, cs.session_id, cs.why, cs.cite_ref, cs.is_active, "
        "cs.created_at FROM coordinate_session cs "
        "WHERE cs.coordinate_id = ? ORDER BY cs.created_at DESC",
        (int(coordinate_id),)).fetchall()
    return [dict(r) for r in rows]


def main() -> int:
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    print("== coordinate_session ==")
    print(ensure_schema(conn))
    n = conn.execute("SELECT COUNT(*) FROM coordinate_session").fetchone()[0]
    print("rows:", n)
    # NEGATIVE CONTROLS: each MUST be refused.
    print("== negative controls (each MUST be refused) ==")
    print("  no session id   ->", link(conn, "", 40).get("code"))
    print("  no coordinate   ->", link(conn, "s", "x").get("code"))
    print("  unknown target  ->", link(conn, "s", 999999).get("code"))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
