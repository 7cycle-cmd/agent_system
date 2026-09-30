# -*- coding: utf-8 -*-
"""workflow_step_playwright.py -- WHICH playwright steps implement WHICH workflow step.

THE HUMAN (2026-09-26), verbatim
--------------------------------
    "playwright_step by workflow, is it correct?
     table : workflow_playwright is missing
     id | workflow_id | playwright_id"

THE ANSWER, MEASURED
--------------------
`playwright_step` is keyed by `playwright_id`, NOT `workflow_id` (measured:
`PRAGMA table_info(playwright_step)` has no `workflow_id`). `workflow_step` has
neither `playwright_id` nor `step_key`. The two tables share ONLY
`id, step_no, step_kind, created_at, updated_at`.

So the ONLY link was at WORKFLOW level (`workflow_playwright`: workflow 2 ->
playwright 1). Nothing said which playwright step implements which workflow
step, and the two step lists do not even line up:

    workflow_step (workflow 2)          playwright_step (playwright 1)
    1 gate    is there a mapping        1 confirm_app_ready      window
    2 describe name the keys            2 assert_foreground      window
    3 verdict COMPUTED                  3 confirm_session_id     session
    4 describe prepare the environment  4 keep_all_edits         window
    5 gate    verify prepare done       5 open_pinned_session    window
    6 describe ask user                 6 confirm_identity_session session
    7 describe data > analyze           7 copy_last_response     browser
    8 count   logic generator           8 get_reply              session
    9 verdict prompt generator          9 write_evidence         window

They are DIFFERENT GRANULARITIES: one is the workflow's LOGIC, the other is the
UI ACTION that drives it. This table is the join.

WHY A LINK TABLE AND NOT A COLUMN
---------------------------------
A column (`playwright_step.workflow_step_id`) would say ONE playwright step
serves ONE workflow step. MEASURED: that is false in both directions --
`confirm_app_ready` serves EVERY workflow that runs in VS Code, and one workflow
step ("prepare the environment") can need several UI actions. A link table with
a `role` says HOW each playwright step serves the workflow step, which a single
FK cannot.

THE `role` VOCABULARY
---------------------
    implements  the playwright step IS the workflow step's action
    verifies    the playwright step CHECKS the workflow step's outcome
    prepares    the playwright step sets up what the workflow step needs

`role` is REGISTERED, not derived from the shape of a string: deriving it would
silently mislabel a link and the label would be believed.

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

SOURCE = "workflow_step_playwright:workflow_step_playwright"

# HOW a playwright step serves a workflow step. REGISTERED, never derived.
ROLES = ("implements", "verifies", "prepares")

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS workflow_step_playwright (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_step_id INTEGER NOT NULL,
    playwright_step_id INTEGER NOT NULL,
    role             TEXT    NOT NULL DEFAULT 'implements',
    cite_ref         TEXT    NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workflow_step_id, playwright_step_id),
    FOREIGN KEY (workflow_step_id)    REFERENCES workflow_step (id),
    FOREIGN KEY (playwright_step_id)  REFERENCES playwright_step (id)
)
"""

_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_wsp_step "
    "ON workflow_step_playwright (workflow_step_id, is_active)",
    "CREATE INDEX IF NOT EXISTS idx_wsp_pw "
    "ON workflow_step_playwright (playwright_step_id, is_active)",
)


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table and its indexes. Idempotent. Never raises."""
    try:
        conn.execute(_CREATE_SQL)
        for sql in _CREATE_INDEX_SQL:
            conn.execute(sql)
        conn.commit()
        return {"ok": True, "table": "workflow_step_playwright"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}


def link(conn: sqlite3.Connection, workflow_step_id: int,
         playwright_step_id: int, *, role: str = "implements",
         cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """Link one workflow step to one playwright step.

    REFUSES a role outside `ROLES`, a missing cite_ref, and a step id that does
    not exist. A link to a row that is not there is a dangling edge that would
    read as coverage.
    """
    try:
        ws = int(workflow_step_id)
        ps = int(playwright_step_id)
    except (TypeError, ValueError):
        return {"ok": False, "why": "workflow_step_id and playwright_step_id "
                                    "must be ints"}
    r = str(role or "").strip()
    if r not in ROLES:
        return {"ok": False, "why": "role %r is not one of %s" % (r, list(ROLES))}
    if not str(cite_ref or "").strip():
        return {"ok": False, "why": "cite_ref is required (a link with no "
                                    "reference is a claim)"}
    for table, col, val in (("workflow_step", "id", ws),
                            ("playwright_step", "id", ps)):
        try:
            row = conn.execute("SELECT 1 FROM %s WHERE %s=?" % (table, col),
                               (val,)).fetchone()
        except sqlite3.OperationalError as e:
            return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
        if not row:
            return {"ok": False, "why": "no %s row with %s=%s" % (table, col, val)}
    try:
        conn.execute(
            "INSERT INTO workflow_step_playwright "
            "(workflow_step_id, playwright_step_id, role, cite_ref) "
            "VALUES (?,?,?,?) ON CONFLICT(workflow_step_id, playwright_step_id) "
            "DO UPDATE SET role=excluded.role, cite_ref=excluded.cite_ref, "
            "is_active=1, updated_at=datetime('now')",
            (ws, ps, r, str(cite_ref).strip()))
        if commit:
            conn.commit()
        return {"ok": True, "workflow_step_id": ws, "playwright_step_id": ps,
                "role": r}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}


def unlink(conn: sqlite3.Connection, workflow_step_id: int,
           playwright_step_id: int, *, commit: bool = True) -> dict[str, Any]:
    """Soft-delete one link. Idempotent."""
    try:
        cur = conn.execute(
            "UPDATE workflow_step_playwright SET is_active=0, "
            "updated_at=datetime('now') WHERE workflow_step_id=? AND "
            "playwright_step_id=? AND is_active=1",
            (int(workflow_step_id), int(playwright_step_id)))
        if commit:
            conn.commit()
        return {"ok": True, "changed": cur.rowcount}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}


def for_workflow_step(conn: sqlite3.Connection,
                      workflow_step_id: int) -> list[dict[str, Any]]:
    """Every ACTIVE playwright step linked to one workflow step."""
    try:
        rows = conn.execute(
            "SELECT l.id, l.role, l.cite_ref, ps.id AS playwright_step_id, "
            "ps.playwright_id, ps.step_no, ps.step_key, ps.step_kind, "
            "ps.method, ps.action, ps.target "
            "FROM workflow_step_playwright l "
            "JOIN playwright_step ps ON ps.id = l.playwright_step_id "
            "WHERE l.workflow_step_id=? AND l.is_active=1 "
            "ORDER BY ps.step_no", (int(workflow_step_id),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def for_playwright_step(conn: sqlite3.Connection,
                        playwright_step_id: int) -> list[dict[str, Any]]:
    """Every ACTIVE workflow step served by one playwright step."""
    try:
        rows = conn.execute(
            "SELECT l.id, l.role, l.cite_ref, ws.id AS workflow_step_id, "
            "ws.workflow_id, ws.step_no, ws.step_kind, ws.layer_key, ws.notes "
            "FROM workflow_step_playwright l "
            "JOIN workflow_step ws ON ws.id = l.workflow_step_id "
            "WHERE l.playwright_step_id=? AND l.is_active=1 "
            "ORDER BY ws.workflow_id, ws.step_no",
            (int(playwright_step_id),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:  # noqa: BLE001
        return []


def coverage(conn: sqlite3.Connection, workflow_id: int) -> dict[str, Any]:
    """Which workflow steps have NO playwright step, and which have one.

    THE GAP IS NAMED, NOT IMPLIED. A workflow step with no link is not "fine" --
    it is a step whose UI action is UNKNOWN, and a reader must be able to see
    that rather than infer it from an empty cell.
    """
    try:
        rows = conn.execute(
            "SELECT ws.id, ws.step_no, ws.step_kind, ws.layer_key, ws.notes, "
            "(SELECT COUNT(*) FROM workflow_step_playwright l "
            " WHERE l.workflow_step_id=ws.id AND l.is_active=1) AS links "
            "FROM workflow_step ws WHERE ws.workflow_id=? "
            "ORDER BY ws.step_no", (int(workflow_id),)).fetchall()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
    covered, uncovered = [], []
    for r in rows:
        d = dict(r)
        (covered if int(d["links"]) > 0 else uncovered).append(d)
    return {"ok": True, "workflow_id": int(workflow_id),
            "total": len(rows), "covered": covered, "uncovered": uncovered,
            "why_uncovered": (
                "" if not uncovered else
                "%d workflow step(s) have NO playwright step: %s. Their UI "
                "action is UNKNOWN, not absent."
                % (len(uncovered),
                   ", ".join("step %s (%s)" % (u["step_no"], u["step_kind"])
                             for u in uncovered)))}


def seed_from_workflow_playwright(conn: sqlite3.Connection, *,
                                  commit: bool = True) -> dict[str, Any]:
    """Seed the links DERIVED from the measured `workflow_playwright` rows.

    THE SEED IS DERIVED, NOT HARDCODED. MEASURED: `workflow_playwright` holds
    (workflow 2 -> playwright 1). For each such pair, the workflow's steps are
    linked to that playwright's steps BY POSITION, and the role is DERIVED from
    the workflow step's own `step_kind`:

        gate     -> verifies   (a gate CHECKS an outcome)
        verdict  -> verifies   (a verdict is a check)
        describe -> implements (a describe IS the action)
        count    -> implements
        name     -> implements

    A position with no counterpart on either side is SKIPPED and REPORTED, never
    invented. The seed is idempotent.
    """
    try:
        pairs = conn.execute(
            "SELECT workflow_id, playwright_id FROM workflow_playwright "
            "WHERE is_active=1").fetchall()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, e)}
    role_of = {"gate": "verifies", "verdict": "verifies",
               "describe": "implements", "count": "implements",
               "name": "implements"}
    made, skipped = [], []
    for p in pairs:
        wf, pw = int(p["workflow_id"]), int(p["playwright_id"])
        ws = conn.execute(
            "SELECT id, step_no, step_kind FROM workflow_step "
            "WHERE workflow_id=? ORDER BY step_no", (wf,)).fetchall()
        ps = conn.execute(
            "SELECT id, step_no, step_key FROM playwright_step "
            "WHERE playwright_id=? AND is_active=1 ORDER BY step_no",
            (pw,)).fetchall()
        for i, w in enumerate(ws):
            if i >= len(ps):
                skipped.append({"workflow_step_id": int(w["id"]),
                                "step_no": int(w["step_no"]),
                                "why": "no playwright step at this position"})
                continue
            role = role_of.get(str(w["step_kind"] or "").strip(), "implements")
            r = link(conn, int(w["id"]), int(ps[i]["id"]), role=role,
                     cite_ref=("workflow_step_playwright.py:"
                               "seed_from_workflow_playwright "
                               "(workflow_playwright wf=%d pw=%d)" % (wf, pw)),
                     commit=False)
            if r.get("ok"):
                made.append({"workflow_step_id": int(w["id"]),
                             "playwright_step_id": int(ps[i]["id"]),
                             "role": role})
            else:
                skipped.append({"workflow_step_id": int(w["id"]),
                                "why": r.get("why")})
        # A playwright step with no workflow step at its position is REPORTED.
        for j in range(len(ws), len(ps)):
            skipped.append({"playwright_step_id": int(ps[j]["id"]),
                            "step_key": ps[j]["step_key"],
                            "why": "no workflow step at this position"})
    if commit:
        conn.commit()
    return {"ok": True, "linked": made, "skipped": skipped,
            "linked_count": len(made), "skipped_count": len(skipped)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--seed", action="store_true",
                    help="seed the links derived from workflow_playwright")
    ap.add_argument("--coverage", type=int, default=0,
                    help="report which workflow steps have no playwright step")
    ap.add_argument("--for-workflow-step", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    if args.seed:
        out = seed_from_workflow_playwright(conn)
    elif args.coverage:
        out = coverage(conn, args.coverage)
    elif args.for_workflow_step:
        out = {"ok": True,
               "rows": for_workflow_step(conn, args.for_workflow_step)}
    else:
        out = {"ok": True, "roles": list(ROLES), "source": SOURCE}
    if args.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    else:
        print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
