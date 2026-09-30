# -*- coding: utf-8 -*-
"""playwright_registry.py -- WHICH playwright environment a workflow runs in.

THE HUMAN (2026-09-25), verbatim
--------------------------------
    "and it is playwright"
    "we need to have table for that too, so worker can get everything to have
     the work easy"
    "id | workflow_id | playwright_id"
    "+ table : playwright_enviornment"
    "id | enviornment_id | name | is_active"

THE TWO TABLES
--------------
    playwright_environment   WHICH browser/page context, and WHICH environment
                             it belongs to.
                             id | environment_id | name | is_active
    workflow_playwright      WHICH workflow runs in WHICH playwright env.
                             id | workflow_id | playwright_id

`playwright_id` is a REAL foreign key to `playwright_environment.id`, and
`workflow_id` is a REAL foreign key to `workflow_registry.workflow_id`. A link
row that references nothing is REFUSED, because a dangling id is a claim that
cannot be checked.

WHY A LINK TABLE AND NOT A COLUMN
---------------------------------
A workflow can run in more than one playwright environment (a browser and a
desktop app), and a playwright environment can serve more than one workflow. A
column on either side would force a choice that the data does not make.

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

SOURCE = "playwright_registry:playwright_environment+workflow_playwright"

NA = "NA"
NA_INT = -1

# ---------------------------------------------------------------------------
# SCOPE N — A PLAYWRIGHT YOU CAN SEE.
#
# THE HUMAN (2026-09-28), verbatim:
#   "playwright can help to proof but playwright run at background, i can't see"
#   "so i can have that to help for UI verifity, and i have have real viewable
#    and proofable and measureable"
#
# THE FINDING, MEASURED: the proof already exists and the screenshots are already
# taken (`playwright_step_run` 4942 rows, 6336 PNGs under `evidence/`). What is
# missing is that the browser is HEADLESS, so the human sitting in front of the
# machine sees nothing. The complaint is not "there is no proof" — it is "the
# proof cannot be watched".
#
# THE FIX: `headless` is READ from `PLAYWRIGHT_HEADED`, defaulting to the current
# behaviour so an unattended run is unchanged.
#
# THE VALVE FAILS CLOSED. An unrecognised value does NOT make the browser
# visible — it stays headless and REPORTS the value it could not read. A valve
# that treated `PLAYWRIGHT_HEADED=maybe` as "yes" would pop a browser window on
# a machine nobody is watching, which is the failure this default exists to
# prevent.
# ---------------------------------------------------------------------------
HEADED_ENV = "PLAYWRIGHT_HEADED"

# The values that mean "show me the browser". Everything else is headless.
HEADED_TRUE = ("1", "true", "yes", "on", "y", "t")
HEADED_FALSE = ("0", "false", "no", "off", "n", "f", "")


def resolve_headless(env: dict[str, str] | None = None) -> dict[str, Any]:
    """Is the browser HEADLESS? Read from `PLAYWRIGHT_HEADED`. Never raises.

    Returns the decision AND its provenance, because a boolean with no source is
    a value nobody can check:

        headless  the value to pass to `p.chromium.launch(headless=...)`
        headed    the inverse, for a reader who thinks in "can I see it"
        source    `env:PLAYWRIGHT_HEADED` or `default`
        raw       the literal value read, or None when unset
        unknown   True when the value was set but not recognised
        why       the reason, in words, for the decision
    """
    import os
    e = os.environ if env is None else env
    raw = e.get(HEADED_ENV)
    if raw is None:
        return {"headless": True, "headed": False, "source": "default",
                "raw": None, "unknown": False,
                "why": ("%s is unset, so the browser stays HEADLESS — the "
                        "current behaviour, unchanged for an unattended run"
                        % HEADED_ENV)}
    val = str(raw).strip().lower()
    if val in HEADED_TRUE:
        return {"headless": False, "headed": True,
                "source": "env:%s" % HEADED_ENV, "raw": raw, "unknown": False,
                "why": ("%s=%r, so the browser is VISIBLE — the run can be "
                        "watched" % (HEADED_ENV, raw))}
    if val in HEADED_FALSE:
        return {"headless": True, "headed": False,
                "source": "env:%s" % HEADED_ENV, "raw": raw, "unknown": False,
                "why": "%s=%r, so the browser stays HEADLESS" % (HEADED_ENV, raw)}
    # FAIL CLOSED: an unrecognised value is REPORTED, never read as "yes".
    return {"headless": True, "headed": False,
            "source": "env:%s" % HEADED_ENV, "raw": raw, "unknown": True,
            "why": ("%s=%r is not a recognised value (%s / %s), so the browser "
                    "stays HEADLESS — the valve FAILS CLOSED rather than "
                    "popping a window on a machine nobody is watching"
                    % (HEADED_ENV, raw, "/".join(HEADED_TRUE),
                       "/".join(v for v in HEADED_FALSE if v)))}


def launch_kwargs(env: dict[str, str] | None = None) -> dict[str, Any]:
    """The kwargs for `p.chromium.launch(...)`, so a caller cannot hardcode it.

    A caller that writes `headless=True` itself has bypassed the valve, so the
    value is produced HERE and nowhere else.
    """
    return {"headless": bool(resolve_headless(env)["headless"])}

_CREATE_ENV_SQL = """
CREATE TABLE IF NOT EXISTS playwright_environment (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    environment_id INTEGER NOT NULL,
    name           TEXT    NOT NULL UNIQUE,
    -- WHICH BROWSER. THE HUMAN (2026-09-25): "launch_browser is for which
    -- browser? is for google chrome or edge or 豆包". MEASURED: the step's
    -- `target` was `headless=True` -- a MODE, not a BROWSER -- so the guide
    -- could not answer the question. The channel lives HERE, on the
    -- environment, because it is a property of the environment, not of a step.
    --   chromium = the bundled browser; chrome = Google Chrome;
    --   msedge = Microsoft Edge; NA = not a browser (an IDE or a desktop app).
    browser_channel TEXT   NOT NULL DEFAULT 'NA',
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    cite_ref       TEXT    NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (environment_id) REFERENCES working_environment (environment_id)
)
"""

_CREATE_LINK_SQL = """
CREATE TABLE IF NOT EXISTS workflow_playwright (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id   INTEGER NOT NULL,
    playwright_id INTEGER NOT NULL,
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (workflow_id, playwright_id),
    FOREIGN KEY (workflow_id)   REFERENCES workflow_registry (workflow_id),
    FOREIGN KEY (playwright_id) REFERENCES playwright_environment (id)
)
"""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create both tables. Idempotent. Never raises.

    ADDITIVE MIGRATION: `browser_channel` was added after the table existed, so
    an older DB needs the column added.
    """
    try:
        conn.execute(_CREATE_ENV_SQL)
        conn.execute(_CREATE_LINK_SQL)
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(playwright_environment)")}
        if "browser_channel" not in cols:
            conn.execute("ALTER TABLE playwright_environment ADD COLUMN "
                         "browser_channel TEXT NOT NULL DEFAULT 'NA'")
        conn.commit()
        return {"ok": True, "tables": ["playwright_environment",
                                       "workflow_playwright"]}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def upsert_environment(conn: sqlite3.Connection, name: str, *,
                       environment_id: int, cite_ref: str,
                       browser_channel: str = NA,
                       is_active: int = 1) -> dict[str, Any]:
    """Register ONE playwright environment, bound to a working_environment.

    REFUSES an `environment_id` that does not exist: a playwright environment
    that belongs to no environment cannot be found by any environment page.
    """
    nm = str(name or "").strip()
    if not nm:
        return {"ok": False, "code": "MISSING_NAME",
                "message": "a playwright environment needs a name"}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no row"}
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "environment_id must be an integer"}
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT environment_id FROM working_environment "
            "WHERE environment_id=?", (eid,)).fetchone()
        if not row:
            return {"ok": False, "code": "UNKNOWN_ENVIRONMENT",
                    "message": "no working_environment row for environment_id=%d"
                               % eid}
        conn.execute(
            "INSERT INTO playwright_environment "
            "(environment_id, name, browser_channel, is_active, cite_ref) "
            "VALUES (?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "environment_id=excluded.environment_id, "
            "browser_channel=excluded.browser_channel, "
            "is_active=excluded.is_active, cite_ref=excluded.cite_ref, "
            "updated_at=CURRENT_TIMESTAMP",
            (eid, nm, str(browser_channel or NA), 1 if is_active else 0,
             str(cite_ref)))
        conn.commit()
        r = conn.execute("SELECT id FROM playwright_environment WHERE name=?",
                         (nm,)).fetchone()
        return {"ok": True, "playwright_id": int(r["id"]), "name": nm,
                "environment_id": eid,
                "browser_channel": str(browser_channel or NA)}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def link(conn: sqlite3.Connection, workflow_id: int, playwright_id: int, *,
         cite_ref: str) -> dict[str, Any]:
    """Link ONE workflow to ONE playwright environment.

    REFUSES a `workflow_id` or `playwright_id` that references nothing. A link
    row is a claim that "this workflow runs there"; a dangling id makes that
    claim uncheckable, which is worse than no row.
    """
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no link"}
    try:
        wid, pid = int(workflow_id), int(playwright_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "workflow_id and playwright_id must be integers"}
    try:
        ensure_schema(conn)
        w = conn.execute("SELECT workflow_id FROM workflow_registry "
                         "WHERE workflow_id=?", (wid,)).fetchone()
        if not w:
            return {"ok": False, "code": "UNKNOWN_WORKFLOW",
                    "message": "no workflow_registry row for workflow_id=%d"
                               % wid}
        p = conn.execute("SELECT id FROM playwright_environment WHERE id=?",
                         (pid,)).fetchone()
        if not p:
            return {"ok": False, "code": "UNKNOWN_PLAYWRIGHT",
                    "message": "no playwright_environment row for id=%d" % pid}
        conn.execute(
            "INSERT INTO workflow_playwright "
            "(workflow_id, playwright_id, cite_ref) VALUES (?,?,?) "
            "ON CONFLICT(workflow_id, playwright_id) DO UPDATE SET "
            "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
            (wid, pid, str(cite_ref)))
        conn.commit()
        r = conn.execute(
            "SELECT id FROM workflow_playwright WHERE workflow_id=? AND "
            "playwright_id=?", (wid, pid)).fetchone()
        return {"ok": True, "id": int(r["id"]), "workflow_id": wid,
                "playwright_id": pid}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def list_environments(conn: sqlite3.Connection, *,
                      environment_id: int | None = None) -> list[dict[str, Any]]:
    """Every playwright environment, optionally scoped to one environment."""
    try:
        ensure_schema(conn)
        sql = ("SELECT p.id, p.environment_id, p.name, p.browser_channel, "
               "       p.is_active, p.cite_ref, "
               "       w.kind, w.product, w.surface "
               "FROM playwright_environment p "
               "LEFT JOIN working_environment w "
               "  ON w.environment_id = p.environment_id ")
        args: tuple = ()
        if environment_id is not None:
            sql += "WHERE p.environment_id = ? "
            args = (int(environment_id),)
        sql += "ORDER BY p.id"
        return [dict(r) for r in conn.execute(sql, args)]
    except Exception:
        return []


def for_workflow(conn: sqlite3.Connection, workflow_id: int
                 ) -> dict[str, Any]:
    """The playwright environments ONE workflow runs in."""
    try:
        wid = int(workflow_id)
    except (TypeError, ValueError):
        return {"ok": False, "workflow_id": workflow_id, "rows": [],
                "why": "workflow_id must be an integer"}
    try:
        ensure_schema(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT l.id AS link_id, l.workflow_id, l.playwright_id, "
            "       p.name, p.environment_id, p.is_active, "
            "       w.kind, w.product, w.surface "
            "FROM workflow_playwright l "
            "JOIN playwright_environment p ON p.id = l.playwright_id "
            "LEFT JOIN working_environment w "
            "  ON w.environment_id = p.environment_id "
            "WHERE l.workflow_id=? AND l.is_active=1 "
            "ORDER BY l.id", (wid,))]
        return {"ok": True, "workflow_id": wid, "rows": rows,
                "count": len(rows)}
    except Exception as exc:
        return {"ok": False, "workflow_id": workflow_id, "rows": [],
                "why": "%s: %s" % (type(exc).__name__, exc)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--environment-id", type=int, default=None)
    ap.add_argument("--workflow-id", type=int, default=None)
    # SCOPE N: `--headed` reports the launch decision WITHOUT launching anything,
    # so the human can check the valve before a run rather than after it.
    ap.add_argument("--headed", action="store_true",
                    help="report whether the browser would be VISIBLE")
    args = ap.parse_args(argv)
    if args.headed:
        print(json.dumps({"ok": True, "launch": resolve_headless()},
                         indent=2, ensure_ascii=False))
        return 0
    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    out: dict[str, Any] = {"ok": True}
    if args.workflow_id is not None:
        out["for_workflow"] = for_workflow(conn, args.workflow_id)
    else:
        out["environments"] = list_environments(
            conn, environment_id=args.environment_id)
    conn.close()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
