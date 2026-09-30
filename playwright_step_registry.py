# -*- coding: utf-8 -*-
"""playwright_step_registry.py -- the STEP GUIDE: what will happen, and how to proof it.

THE HUMAN (2026-09-25), verbatim
--------------------------------
    "playwright is STEP, is it correct?
     so where is STEP GUIDE?
     how to i know what will happen, without that, i don't know and how to proof
     it is worked or not?
     playwright didn't tell me
     imrpove it now
     user can easy to have all he need
     do it now"

THE ANSWER TO "playwright is STEP, is it correct?"
--------------------------------------------------
PARTLY, and the wrong part is the part that matters.

Playwright is not itself a step. Playwright is the EXECUTOR of a step. Three
facts are separate and each needs its own home:

    workflow_step           WHICH step of the workflow   (step_no, prompt_id)
    playwright_environment  WHICH browser drives it      (environment_id, name)
    playwright_step         HOW that step is driven      <-- THIS MODULE

So the correct statement is: a step is DRIVEN BY playwright, and the missing
table is the one that says HOW. That table is the STEP GUIDE.

THE TWO TABLES
--------------
    playwright_step       the GUIDE (a register): what each step DOES, what WILL
                          HAPPEN (`expect`), and HOW TO PROOF IT (`proof`).
    playwright_step_run   the RESULT (a log): what ACTUALLY happened on one run.

A register describes what a thing IS; a log records what HAPPENED. The same
split this repo already uses for `prompt_combo`.

WHY `expect` AND `proof` ARE NOT NULL
-------------------------------------
A step whose outcome is unstated cannot be checked. That is EXACTLY the state
the page was in before this module: six real steps ran in Python and the user
could not see one of them. `upsert_step` REFUSES a step with no `expect` or no
`proof`, so a blank cell can never be rendered as "fine".

THE GUIDE IS THE PROGRAM
------------------------
`_run_playwright_test()` READS this table and executes it. If the guide were
documentation beside the code, the two would drift and the guide would become a
lie -- the defect class this repo keeps hitting ("the APIs answered while no UI
referenced them"). A step added here runs on the next test with NO code change.

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

SOURCE = "playwright_step_registry:playwright_step+playwright_step_run"

NA = "NA"
NA_INT = -1

# The statuses a step run may carry. UNKNOWN is a REAL outcome: a step that
# could not be observed is NOT a pass, and it is NOT a failure either.
STATUSES = ("PASS", "FAIL", "SKIP", "UNKNOWN")

# THE STEP'S NATURE. THE HUMAN (2026-09-25): "why for enviornment : vscode, need
# to launch_browser? ... why have have id : 1 for vscode and not by windows task
# to confirm APP : VS code is ready to use".
#
# VS Code is a DESKTOP APP that is ALREADY RUNNING. You do not LAUNCH it -- you
# CONFIRM it is ready. `launch_browser` on an IDE is the wrong verb for the
# wrong kind of thing. Without this column the two kinds look identical and the
# interpreter has to guess.
#
# `session` (added 2026-09-25). THE HUMAN: "we need to confirm session ID
# (exmaple: 50f58738-b8e6-4976-8e3b-fcd7403a0c71) is the one you are having
# conversaction / this is must for each conversaction via VScode, is that part
# for identity proof". A `session` step confirms WHICH CONVERSATION the IDE is
# showing. It is neither a browser action nor a window confirmation, so it gets
# its own kind -- the interpreter dispatches on `step_kind`, so the three can
# never be confused.
STEP_KINDS = ("browser", "window", "session")

# HOW A STEP IS DRIVEN -- the human's `method` column.
#
# THE HUMAN (2026-09-26): "table will be / STEP 1 instruction method (hotkey /
# coordinate) evidence / STEP 2 ......".
#
# WHY THIS IS A REGISTERED COLUMN AND NOT A GUESS. `step_kind` is a DIFFERENT
# axis (WHAT KIND of thing the step acts on: a browser, a window, a session),
# and `target` is a free string (`code.exe`, `ACTIVE`, `Ctrl+Enter`,
# `PINNED_FIRST`, `classify.json`). Deriving "hotkey vs coordinate" from the
# SHAPE of `target` (e.g. "contains a `+`") would silently mislabel a step and
# the label would be believed -- the exact defect the `independent-review` skill
# forbids. So the axis is REGISTERED, and `upsert_step` REFUSES a value outside
# this vocabulary.
#
# `window` / `session` are kept because env 6 has 4 window steps and 2 session
# steps; forcing them into hotkey/coordinate would state something false.
# `NA` is the honest value for a step that is neither (e.g. `write_evidence`).
#
# `cdp` IS A SIXTH METHOD, ADDED 2026-09-26. THE HUMAN: "豆包 is a **desktop
# app** is past !! now is 豆包 is a browser!! ... and she has it own offical
# browser too". MEASURED `doubao_cdp.py:1`: 豆包 ships a FULL Chromium 147 and
# answers the DevTools Protocol, so its steps are driven by CONNECTING to that
# browser and reading the DOM -- which is neither a hotkey, a coordinate, a
# window confirmation, nor a session check. Forcing it into one of those would
# state something false, which is exactly what this vocabulary exists to stop.
METHODS = ("hotkey", "coordinate", "window", "session", "cdp", NA)

_CREATE_STEP_SQL = """
CREATE TABLE IF NOT EXISTS playwright_step (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    playwright_id INTEGER NOT NULL,
    step_no       INTEGER NOT NULL,
    step_key      TEXT    NOT NULL,
    -- WHAT KIND OF STEP THIS IS. `browser` = a Playwright browser action;
    -- `window` = a WINDOWS TASK confirmation that an app is running and ready.
    -- The interpreter DISPATCHES on this, so the two can never be confused.
    step_kind     TEXT    NOT NULL DEFAULT 'browser'
                  CHECK (step_kind IN ('browser', 'window', 'session')),
    -- HOW THE STEP IS DRIVEN. THE HUMAN (2026-09-26): "STEP 1 instruction
    -- method (hotkey / coordinate) evidence". A DIFFERENT axis from
    -- `step_kind`: `step_kind` says WHAT KIND of thing the step acts on, this
    -- says HOW it is driven. `NA` is the honest value for a step that is
    -- neither (e.g. `write_evidence`).
    --
    -- `cdp` ADDED 2026-09-26: 豆包 is a BROWSER reached over the DevTools
    -- Protocol, which is neither a hotkey, a coordinate, a window confirmation
    -- nor a session check.
    method        TEXT    NOT NULL DEFAULT 'NA'
                  CHECK (method IN ('hotkey', 'coordinate', 'window',
                                    'session', 'cdp', 'NA')),
    -- WHICH COORDINATE TARGET THIS STEP ACTS ON, so the evidence image can draw
    -- the red box + cross from the DB rect. THE HUMAN (2026-09-26): "evidence
    -- image be image, user can understand why this is evidence, not have
    -- screenshot only".
    --
    -- NULLABLE ON PURPOSE: most steps act on no single target (a session check,
    -- a write), and a NULL is the honest answer. It is a REGISTERED link, never
    -- derived from the SHAPE of `target` -- that would be a heuristic, and a
    -- mislabelled box is worse than no box.
    target_template_id INTEGER,
    action        TEXT    NOT NULL,
    target        TEXT    NOT NULL DEFAULT 'NA',
    -- WHAT WILL HAPPEN. The human: "how to i know what will happen, without
    -- that, i don't know". NOT NULL: an unstated outcome cannot be checked.
    expect        TEXT    NOT NULL DEFAULT 'NA',
    -- HOW TO PROOF IT WORKED. The human: "how to proof it is worked or not".
    proof         TEXT    NOT NULL DEFAULT 'NA',
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    cite_ref      TEXT    NOT NULL,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (playwright_id, step_no),
    FOREIGN KEY (playwright_id) REFERENCES playwright_environment (id)
)
"""

_CREATE_RUN_SQL = """
CREATE TABLE IF NOT EXISTS playwright_step_run (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    evidence_id   TEXT    NOT NULL,
    playwright_id INTEGER NOT NULL,
    step_no       INTEGER NOT NULL,
    step_key      TEXT    NOT NULL,
    status        TEXT    NOT NULL CHECK (status IN ('PASS','FAIL','SKIP','UNKNOWN')),
    got           TEXT    NOT NULL DEFAULT 'NA',
    elapsed_ms    INTEGER NOT NULL DEFAULT -1,
    -- THE STEP'S OWN PICTURE. THE HUMAN (2026-09-25): "for the STEP and
    -- Evidence table, STEP is onclick to image" and "+ image field user
    -- friendly". MEASURED: only the `screenshot` step wrote an image, so every
    -- other step row had no picture -- and a step's `got` is a sentence the
    -- reader has to trust. A picture of the screen AT THAT STEP lets the reader
    -- check the sentence instead. The NAME is stored (not a path), because the
    -- image lives in the run's own evidence folder and the reader resolves it.
    image_name    TEXT    NOT NULL DEFAULT 'NA',
    -- WHY THE PICTURE IS MISSING. THE HUMAN (2026-09-26): the STEP table showed
    -- "no image yet" on EVERY row and nothing said why. MEASURED: the capture
    -- swallowed its exception, so a DEAD screenshotter was indistinguishable
    -- from "this step has no picture". A missing proof must be VISIBLE **and
    -- ATTRIBUTED**, so the reason is stored beside the `NA`.
    image_why     TEXT    NOT NULL DEFAULT '',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (evidence_id, step_no)
)
"""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create both tables. Idempotent. Never raises.

    ADDITIVE MIGRATION: `step_kind` was added after the table existed, so an
    older DB needs the column added. `ALTER TABLE ... ADD COLUMN` cannot add a
    CHECK constraint, so the CHECK lives in the CREATE and the ALTER adds the
    column with the same DEFAULT; `upsert_step` enforces the CHECK in code for
    an older DB.
    """
    try:
        conn.execute(_CREATE_STEP_SQL)
        conn.execute(_CREATE_RUN_SQL)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(playwright_step)")}
        if "step_kind" not in cols:
            conn.execute("ALTER TABLE playwright_step ADD COLUMN step_kind "
                         "TEXT NOT NULL DEFAULT 'browser'")
        # THE METHOD AXIS, added after the table existed. ADDITIVE: an older DB
        # gets the column with the `NA` sentinel, so an existing row reads as
        # "not stated" rather than as a defect. `ALTER TABLE ... ADD COLUMN`
        # cannot add a CHECK, so `upsert_step` enforces the vocabulary in code
        # for an older DB -- the same pattern `step_kind` already uses.
        if "method" not in cols:
            conn.execute("ALTER TABLE playwright_step ADD COLUMN method "
                         "TEXT NOT NULL DEFAULT 'NA'")
        # THE TARGET LINK, added after the table existed. ADDITIVE: an older DB
        # gets the column as NULL, which reads as "this step acts on no single
        # target" rather than as a defect.
        if "target_template_id" not in cols:
            conn.execute("ALTER TABLE playwright_step ADD COLUMN "
                         "target_template_id INTEGER")
        # THE STEP'S PICTURE, added after the run table existed. ADDITIVE: an
        # older DB gets the column with the `NA` sentinel, so an existing row
        # reads as "no image" rather than as a defect.
        rcols = {r[1] for r in conn.execute(
            "PRAGMA table_info(playwright_step_run)")}
        if "image_name" not in rcols:
            conn.execute("ALTER TABLE playwright_step_run ADD COLUMN "
                         "image_name TEXT NOT NULL DEFAULT 'NA'")
        # THE REASON THE PICTURE IS MISSING, added after the run table existed.
        # ADDITIVE: an older DB gets the column with an EMPTY default, so an
        # existing row reads as "no reason recorded" rather than as a defect.
        if "image_why" not in rcols:
            conn.execute("ALTER TABLE playwright_step_run ADD COLUMN "
                         "image_why TEXT NOT NULL DEFAULT ''")
        conn.commit()
        return {"ok": True, "tables": ["playwright_step", "playwright_step_run"]}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def upsert_step(conn: sqlite3.Connection, playwright_id: int, step_no: int, *,
                step_key: str, action: str, target: str = NA,
                expect: str, proof: str, cite_ref: str,
                step_kind: str = "browser", method: str = NA,
                target_template_id: int | None = None,
                is_active: int = 1) -> dict[str, Any]:
    """Register ONE step of the guide.

    REFUSES:
      * a missing `expect`  -- an unstated outcome cannot be checked
      * a missing `proof`   -- a step with no check proves nothing
      * a missing `cite_ref`-- no citation, no row
      * an unknown `playwright_id` -- a step that belongs to no playwright
        environment can never be run by any test
      * an unknown `step_kind` -- a typo that reads as a browser action when it
        is a window confirmation is the worst possible outcome
      * an unknown `method` -- a typo that reads as "hotkey" when the step is a
        coordinate click would make the pop-up state something false
      * an unknown `target_template_id` -- a DANGLING link is worse than no
        link, because the evidence image would draw a box for a target that
        does not exist
    """
    if not str(step_key or "").strip():
        return {"ok": False, "code": "MISSING_STEP_KEY",
                "message": "a step needs a step_key"}
    if not str(action or "").strip():
        return {"ok": False, "code": "MISSING_ACTION",
                "message": "a step needs an action"}
    # THE STEP'S NATURE. A `window` step and a `browser` step are dispatched
    # differently, so an unstated or misspelled kind must not be storable.
    sk = str(step_kind or "").strip().lower()
    if sk not in STEP_KINDS:
        return {"ok": False, "code": "BAD_STEP_KIND",
                "message": "step_kind must be one of %s, got %r"
                           % (", ".join(STEP_KINDS), step_kind)}
    # HOW THE STEP IS DRIVEN. A typo that reads as "hotkey" when the step is a
    # coordinate click would make the pop-up state something false, so an
    # unknown value must not be storable.
    md = str(method or NA).strip()
    if md not in METHODS:
        return {"ok": False, "code": "BAD_METHOD",
                "message": "method must be one of %s, got %r"
                           % (", ".join(METHODS), method)}
    # THE TARGET LINK. A dangling id would make the evidence image draw a box for
    # a target that does not exist, so it is REFUSED rather than stored.
    ttid = None
    if target_template_id not in (None, "", "NA"):
        try:
            ttid = int(target_template_id)
        except (TypeError, ValueError):
            return {"ok": False, "code": "BAD_TARGET_TEMPLATE_ID",
                    "message": "target_template_id must be an int or None, "
                               "got %r" % (target_template_id,)}
    # THE TWO COLUMNS THE HUMAN ASKED FOR. A step that cannot say what will
    # happen, or how to check it, is the defect this module exists to remove.
    if not str(expect or "").strip() or str(expect).strip() == NA:
        return {"ok": False, "code": "MISSING_EXPECT",
                "message": "a step must state WHAT WILL HAPPEN (`expect`)"}
    if not str(proof or "").strip() or str(proof).strip() == NA:
        return {"ok": False, "code": "MISSING_PROOF",
                "message": "a step must state HOW TO PROOF IT (`proof`)"}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no row"}
    try:
        pid, sno = int(playwright_id), int(step_no)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "playwright_id and step_no must be integers"}
    try:
        ensure_schema(conn)
        p = conn.execute("SELECT id FROM playwright_environment WHERE id=?",
                         (pid,)).fetchone()
        if not p:
            return {"ok": False, "code": "UNKNOWN_PLAYWRIGHT",
                    "message": "no playwright_environment row for id=%d" % pid}
        if ttid is not None:
            t = conn.execute("SELECT id FROM target_template WHERE id=?",
                             (ttid,)).fetchone()
            if not t:
                return {"ok": False, "code": "UNKNOWN_TARGET_TEMPLATE",
                        "message": "no target_template row for id=%d" % ttid}
        conn.execute(
            "INSERT INTO playwright_step "
            "(playwright_id, step_no, step_key, step_kind, method, "
            " target_template_id, action, "
            " target, expect, proof, is_active, cite_ref) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(playwright_id, step_no) DO UPDATE SET "
            "step_key=excluded.step_key, step_kind=excluded.step_kind, "
            "method=excluded.method, "
            "target_template_id=excluded.target_template_id, "
            "action=excluded.action, "
            "target=excluded.target, expect=excluded.expect, "
            "proof=excluded.proof, is_active=excluded.is_active, "
            "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
            (pid, sno, str(step_key), sk, md, ttid, str(action),
             str(target or NA), str(expect), str(proof),
             1 if is_active else 0, str(cite_ref)))
        conn.commit()
        r = conn.execute("SELECT id FROM playwright_step WHERE playwright_id=? "
                         "AND step_no=?", (pid, sno)).fetchone()
        return {"ok": True, "id": int(r["id"]), "playwright_id": pid,
                "step_no": sno, "step_key": str(step_key),
                "step_kind": sk, "method": md,
                "target_template_id": ttid}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def list_steps(conn: sqlite3.Connection, playwright_id: int, *,
               active_only: bool = True) -> list[dict[str, Any]]:
    """The guide for ONE playwright environment, in step order."""
    try:
        ensure_schema(conn)
        sql = ("SELECT id, playwright_id, step_no, step_key, step_kind, method, "
               "       target_template_id, action, target, expect, proof, "
               "       is_active, cite_ref "
               "FROM playwright_step WHERE playwright_id=? ")
        if active_only:
            sql += "AND is_active=1 "
        sql += "ORDER BY step_no"
        return [dict(r) for r in conn.execute(sql, (int(playwright_id),))]
    except Exception:
        return []


def record_run(conn: sqlite3.Connection, evidence_id: str, playwright_id: int,
               step_no: int, *, step_key: str, status: str, got: str = NA,
               elapsed_ms: int = NA_INT,
               image_name: str = NA, image_why: str = "") -> dict[str, Any]:
    """Record what ACTUALLY happened for ONE step of ONE run.

    REFUSES an unknown status: a status outside the four is a typo, and a typo
    that reads as a pass is the worst possible outcome.
    """
    st = str(status or "").upper()
    if st not in STATUSES:
        return {"ok": False, "code": "BAD_STATUS",
                "message": "status must be one of %s, got %r"
                           % (", ".join(STATUSES), status)}
    if not str(evidence_id or "").strip():
        return {"ok": False, "code": "MISSING_EVIDENCE_ID",
                "message": "a run row must name the evidence record it belongs to"}
    try:
        pid, sno = int(playwright_id), int(step_no)
        ms = int(elapsed_ms)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "playwright_id, step_no and elapsed_ms must be ints"}
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO playwright_step_run "
            "(evidence_id, playwright_id, step_no, step_key, status, got, "
            " elapsed_ms, image_name, image_why) VALUES (?,?,?,?,?,?,?,?,?)"
            "ON CONFLICT(evidence_id, step_no) DO UPDATE SET "
            "status=excluded.status, got=excluded.got, "
            "elapsed_ms=excluded.elapsed_ms, "
            "image_name=excluded.image_name, "
            "image_why=excluded.image_why",
            (str(evidence_id), pid, sno, str(step_key), st,
             str(got if got not in (None, "") else NA), ms,
             str(image_name or NA), str(image_why or "")))
        conn.commit()
        return {"ok": True, "evidence_id": str(evidence_id), "step_no": sno,
                "status": st}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def runs_for(conn: sqlite3.Connection, evidence_id: str) -> list[dict[str, Any]]:
    """Every step run recorded for ONE evidence record, in step order."""
    try:
        ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT id, evidence_id, playwright_id, step_no, step_key, status, "
            "       got, elapsed_ms, image_name, image_why, created_at "
            "FROM playwright_step_run WHERE evidence_id=? ORDER BY step_no",
            (str(evidence_id),))]
    except Exception:
        return []


def last_run(conn: sqlite3.Connection, playwright_id: int
             ) -> dict[int, dict[str, Any]]:
    """The NEWEST run row per step_no, keyed by step_no.

    This is what the STEP GUIDE tab shows beside each step: the last time it
    ran, what it observed, and how long it took. Without it the guide is a
    promise; with it the guide is a record.
    """
    try:
        ensure_schema(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT r.step_no, r.step_key, r.status, r.got, r.elapsed_ms, "
            "       r.evidence_id, r.image_name, r.image_why, r.created_at "
            "FROM playwright_step_run r "
            "JOIN (SELECT step_no, MAX(id) AS mx FROM playwright_step_run "
            "      WHERE playwright_id=? GROUP BY step_no) m "
            "  ON m.mx = r.id "
            "WHERE r.playwright_id=? ORDER BY r.step_no",
            (int(playwright_id), int(playwright_id)))]
        return {int(r["step_no"]): r for r in rows}
    except Exception:
        return {}


def guide(conn: sqlite3.Connection, playwright_id: int) -> dict[str, Any]:
    """The guide WITH the last run beside each step. One call for the page."""
    steps = list_steps(conn, playwright_id)
    last = last_run(conn, playwright_id)
    for s in steps:
        r = last.get(int(s["step_no"])) or {}
        s["last_status"] = r.get("status", NA)
        s["last_got"] = r.get("got", NA)
        s["last_elapsed_ms"] = r.get("elapsed_ms", NA_INT)
        s["last_evidence_id"] = r.get("evidence_id", NA)
        s["last_created_at"] = r.get("created_at", NA)
        # THE IMAGE NAME MUST BE CARRIED, or the API has nothing to resolve a
        # URL from. MEASURED (2026-09-25): the guide returned `image_name: None`
        # for every row because this line was missing -- the column existed and
        # held a value, but the dict the API reads it from did not.
        s["image_name"] = r.get("image_name", NA)
        # THE REASON THE PICTURE IS MISSING, carried beside the name so the
        # pop-up can ATTRIBUTE a missing image instead of showing a bare "no
        # image yet".
        s["image_why"] = r.get("image_why", "")
        s["evidence_id"] = r.get("evidence_id", NA)
    return {"ok": True, "playwright_id": int(playwright_id),
            "rows": steps, "count": len(steps)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--playwright-id", type=int, default=1)
    ap.add_argument("--runs", default=None, help="evidence_id to list runs for")
    args = ap.parse_args(argv)
    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    if args.runs:
        out: Any = {"ok": True, "runs": runs_for(conn, args.runs)}
    else:
        out = guide(conn, args.playwright_id)
    conn.close()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
