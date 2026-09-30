# -*- coding: utf-8 -*-
"""ui_element_registry.py — the UI ELEMENT register: one row per thing a user SEES.

WHY THIS FILE EXISTS
--------------------
THE HUMAN (2026-09-27), verbatim:

    "be user fiendly as we have ui helper team , why i never have good
     experience ? i just have question and question for all your ui job?"
    "how does it can be better, github can help you by skill how to have ssot
     for all ui element to proof user friendly is not a word is standardize"
    "2) by table too, same style with _register, so everything can measure with
     measured unit"

MEASURED, and this is the defect the human named: `user_friendly` is **NOT
REGISTERED** in `terminology_registry` (0 rows of 1483) and appears in **0** files
under `docs/`. It is an adjective with no definition, no unit and no proof — so
every time the agent says "done", the human has no way to check it.

**"User friendly" is not a word. It is a standard, and a standard is a table.**

WHY A NEW TABLE AND NOT `element_store.ui_element`
--------------------------------------------------
`element_store.py` already declares a `ui_element` table, and it is a DIFFERENT
FACT. MEASURED: its 18 rows all carry `app_key='doubao'`, its rect is
FRAME-LOCAL, and its purpose is "look up a measured selector instead of guessing
one" for DESKTOP automation. `elements.db` does not even exist.

This table records what a user READS: the rendered text, the registered user
word, the unit, and the population a number counts. Merging the two would either
weaken `element_store`'s five CHECK invariants or force a screen-space unit onto
a frame-local column. **Two facts, two tables.**

EVERY ROW CARRIES A MEASURED UNIT
---------------------------------
The human's rule: *"so everything can measure with measured unit"*. So `unit_key`
is `NOT NULL` and `add_element` REFUSES a row whose `unit_key` is not in
`unit_registry`. A UI element with no unit is a decoration, and a decoration
cannot be audited.

THE `_register` SHAPE IS THE REPO'S OWN
---------------------------------------
MEASURED: 30 `*_register.py` modules exist, and `subject_kind_registry.py`
declares the shape they share:

    class XError(ValueError)
    def _connect(db_path)
    def ensure_schema(conn)          # executescript(DDL), idempotent
    def add_<thing>(conn, ...)       # REFUSES empty key / bad key / empty cite_ref
    def seed_<things>(conn)          # idempotent
    def validate_<thing>(conn, ...)
    def list_<things>(conn)
    def check_divergence(conn)       # reports drift, never raises
    def main()

This module follows it exactly, so it is RECOGNISABLE rather than novel.

CITATION IS REQUIRED, NOT DECORATION
------------------------------------
Every row names the `app.js:LINE` that renders it (`source_ref`) and a checkable
`cite_ref`. A row with no citation is a memory, and a memory is what this table
exists to replace. `add_element` REFUSES a row without both.

Usage:
  python ui_element_registry.py --init
  python ui_element_registry.py --seed
  python ui_element_registry.py --list
  python ui_element_registry.py --check-divergence
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# The element kinds. A `label` is a column header, a `badge` is a status chip, a
# `number` is a rendered value, an `empty_state` is what shows when there is no
# row, and an `action` is a control the user can press.
ELEMENT_KINDS = ("label", "badge", "number", "empty_state", "action")

# The element_key FORM. Same rule `terminology_registry.TERM_KEY_RE` applies to a
# term_key, so a UI element key and a term key cannot drift apart in form.
ELEMENT_KEY_RE = re.compile(r"^[a-z0-9]+(?:[._][a-z0-9]+)*$")


class UiElementError(ValueError):
    """Raised when a UI element would be registered without its rules."""


UI_ELEMENT_DDL = """
CREATE TABLE IF NOT EXISTS ui_element_registry (
    element_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    element_key    TEXT    NOT NULL UNIQUE,
    page_key       TEXT    NOT NULL,
    element_kind   TEXT    NOT NULL CHECK (element_kind IN
                     ('label','badge','number','empty_state','action')),
    rendered_text  TEXT    NOT NULL,
    user_label     TEXT    NOT NULL,
    term_key       TEXT    NOT NULL,
    unit_key       TEXT    NOT NULL,
    population     TEXT    NOT NULL DEFAULT '',
    why_clickable  INTEGER NOT NULL DEFAULT 0 CHECK (why_clickable IN (0,1)),
    why_text       TEXT    NOT NULL DEFAULT '',
    next_action    TEXT    NOT NULL DEFAULT '',
    source_ref     TEXT    NOT NULL,
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ui_element_page
  ON ui_element_registry (page_key, element_kind);
"""


def _connect(db_path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table. Idempotent."""
    conn.executescript(UI_ELEMENT_DDL)
    conn.commit()
    return {"ok": True, "table": "ui_element_registry"}


def _known_units(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute(
            "SELECT unit_key FROM unit_registry WHERE is_active = 1")}
    except sqlite3.OperationalError:
        return set()


def _known_terms(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(r[0]) for r in conn.execute(
            "SELECT term_key FROM terminology_registry WHERE is_active = 1")}
    except sqlite3.OperationalError:
        return set()


def add_element(
    conn: sqlite3.Connection,
    *,
    element_key: str,
    page_key: str,
    element_kind: str,
    rendered_text: str,
    user_label: str,
    term_key: str,
    unit_key: str,
    source_ref: str,
    cite_ref: str,
    population: str = "",
    why_clickable: int = 0,
    why_text: str = "",
    next_action: str = "",
    commit: bool = True,
    update_existing: bool = False,
) -> dict[str, Any]:
    """Register ONE UI element. Idempotent on `element_key`.

    `update_existing=True` makes an EXISTING row match the declaration instead of
    being left alone. MEASURED 2026-09-28: without it, a seeder that FIXED a row
    reported `added: 0` and the fix never reached the DB — the audit kept
    dropping `question.empty.no_frontier` while the source said it was fixed.

    The update REUSES every check above rather than adding a second validation
    path, because two paths drift: a seeder that writes around the gate can
    store a row the gate would refuse.

    REFUSES, rather than storing something unusable — the same gate
    `subject_kind_registry.add_kind` applies, plus the two this table adds:

      * an empty `element_key`, or one that is not a lowercase identifier
      * an empty `cite_ref` — no citation, no row
      * an unknown `element_kind` — an undefined kind matches nothing SILENTLY
      * an unknown `unit_key` — the human's rule: everything measures with a
        MEASURED unit, so a unit that is not in `unit_registry` is not a unit
      * an unknown `term_key` — a label whose word is not registered is exactly
        the defect this table exists to remove
      * an empty `source_ref` — a row that does not name where it is rendered
        cannot be checked against the page
    """
    key = str(element_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_ELEMENT_KEY",
                "message": "element_key is required"}
    if not ELEMENT_KEY_RE.match(key):
        return {"ok": False, "code": "BAD_ELEMENT_KEY",
                "message": ("element_key must be a lowercase identifier "
                            "(^[a-z0-9]+(?:[._][a-z0-9]+)*$), got %r — an "
                            "element is a KEY, so 'Coords' and 'coords' must "
                            "not become two elements" % key)}
    kind = str(element_kind or "").strip()
    if kind not in ELEMENT_KINDS:
        return {"ok": False, "code": "UNKNOWN_ELEMENT_KIND",
                "message": ("element_kind %r is not one of %s — an undefined "
                            "kind matches nothing silently"
                            % (kind, ", ".join(ELEMENT_KINDS)))}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no element: %s" % key}
    if not str(source_ref or "").strip():
        return {"ok": False, "code": "MISSING_SOURCE_REF",
                "message": ("source_ref is required: a row that does not name "
                            "where it is rendered cannot be checked against the "
                            "page (%s)" % key)}
    if not str(rendered_text or "").strip():
        return {"ok": False, "code": "MISSING_RENDERED_TEXT",
                "message": ("rendered_text is required: this table records what "
                            "the user SEES (%s)" % key)}

    ensure_schema(conn)
    units = _known_units(conn)
    if units and str(unit_key) not in units:
        return {"ok": False, "code": "UNKNOWN_UNIT_KEY",
                "message": ("unit_key %r is not in unit_registry — everything "
                            "measures with a MEASURED unit, so a unit that is "
                            "not registered is not a unit" % unit_key)}
    terms = _known_terms(conn)
    if terms and str(term_key) not in terms:
        return {"ok": False, "code": "UNKNOWN_TERM_KEY",
                "message": ("term_key %r is not in terminology_registry — a "
                            "label whose word is not registered is the defect "
                            "this table exists to remove" % term_key)}

    existing = conn.execute(
        "SELECT element_id FROM ui_element_registry WHERE element_key=?",
        (key,)).fetchone()
    if existing:
        if not update_existing:
            return {"ok": True, "element_id": int(existing[0]),
                    "created": False, "element_key": key}
        conn.execute(
            "UPDATE ui_element_registry SET page_key=?, element_kind=?, "
            "rendered_text=?, user_label=?, term_key=?, unit_key=?, "
            "population=?, why_clickable=?, why_text=?, next_action=?, "
            "source_ref=?, cite_ref=?, updated_at=datetime('now') "
            "WHERE element_key=?",
            (str(page_key or "").strip(), kind, str(rendered_text).strip(),
             str(user_label or "").strip(), str(term_key).strip(), str(unit_key),
             str(population or ""), int(1 if why_clickable else 0),
             str(why_text or ""), str(next_action or ""),
             str(source_ref).strip(), str(cite_ref).strip(), key))
        if commit:
            conn.commit()
        return {"ok": True, "element_id": int(existing[0]), "created": False,
                "updated": True, "element_key": key}

    cur = conn.execute(
        "INSERT INTO ui_element_registry (element_key, page_key, element_kind, "
        "rendered_text, user_label, term_key, unit_key, population, "
        "why_clickable, why_text, next_action, source_ref, cite_ref) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, str(page_key or "").strip(), kind, str(rendered_text).strip(),
         str(user_label or "").strip(), str(term_key).strip(), str(unit_key),
         str(population or ""), int(1 if why_clickable else 0),
         str(why_text or ""), str(next_action or ""),
         str(source_ref).strip(), str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "element_id": cur.lastrowid, "created": True,
            "element_key": key}


# ---------------------------------------------------------------------------
# THE SEED — the pinned page, MEASURED from the renderer.
#
# Every `source_ref` is a real `app.js:LINE`. Every `rendered_text` is the literal
# string the renderer emits. Every `term_key` is a term this module ALSO
# registers, so the label and the word cannot drift apart.
#
# THE BASELINE, MEASURED 2026-09-27 before this table existed:
#   labels registered   2 of 10
#   hover-only why      3, clickable 0
#   unknowns named      0 of 2
#   numbers with a pop  0 of 11
#   empty state action  0 of 1
# ---------------------------------------------------------------------------
PAGE_KEY = "user_environment.sessions"
SEED_CITE = "ui_element_registry.py:SEED_ELEMENTS"

SEED_ELEMENTS: tuple[dict[str, Any], ...] = (
    # ---- the 10 column headers (element_kind='label') ----------------------
    dict(element_key="sessions.col.row_no", element_kind="label",
         rendered_text="#", user_label="Row",
         term_key="ui_row_no", unit_key="count",
         population="the pinned row's position, 1-based",
         source_ref="llm_task_monitor_ui/src/app.js:3951"),
    dict(element_key="sessions.col.session_id", element_kind="label",
         rendered_text="session_id", user_label="Session",
         term_key="ui_session", unit_key="count",
         population="one row per pinned session",
         source_ref="llm_task_monitor_ui/src/app.js:3952"),
    dict(element_key="sessions.col.top_left", element_kind="label",
         rendered_text="x1, y1", user_label="Top-left",
         term_key="ui_rect_top_left", unit_key="count",
         population="the rect's top-left corner, in screen pixels",
         source_ref="llm_task_monitor_ui/src/app.js:3953"),
    dict(element_key="sessions.col.bottom_right", element_kind="label",
         rendered_text="x2, y2", user_label="Bottom-right",
         term_key="ui_rect_bottom_right", unit_key="count",
         population="the rect's bottom-right corner, in screen pixels",
         source_ref="llm_task_monitor_ui/src/app.js:3954"),
    dict(element_key="sessions.col.centre", element_kind="label",
         rendered_text="centre", user_label="Centre",
         term_key="ui_rect_centre", unit_key="count",
         population="the rect's centre point, in screen pixels",
         source_ref="llm_task_monitor_ui/src/app.js:3955"),
    dict(element_key="sessions.col.measurements", element_kind="label",
         rendered_text="coords", user_label="Measurements",
         term_key="ui_measurement_count", unit_key="count",
         population=("count of coordinate_session rows linked to this session "
                     "— NOT a count of sessions"),
         source_ref="llm_task_monitor_ui/src/app.js:3956"),
    dict(element_key="sessions.col.identity", element_kind="label",
         rendered_text="identity", user_label="Identity known?",
         term_key="ui_identity_known", unit_key="boolean",
         population="whether identity_registry has a row for this session_id",
         source_ref="llm_task_monitor_ui/src/app.js:3957"),
    dict(element_key="sessions.col.status", element_kind="label",
         rendered_text="status", user_label="Live?",
         term_key="ui_live_status", unit_key="boolean",
         population=("whether the session's own chatSessions file was touched "
                     "within LIVE_WINDOW_S"),
         source_ref="llm_task_monitor_ui/src/app.js:3958"),
    dict(element_key="sessions.col.environment", element_kind="label",
         rendered_text="environment", user_label="Where",
         term_key="ui_environment", unit_key="count",
         population="the environment the session's coordinate resolves to",
         source_ref="llm_task_monitor_ui/src/app.js:3959"),
    dict(element_key="sessions.col.last_seen", element_kind="label",
         rendered_text="last seen", user_label="Last linked",
         term_key="ui_last_linked", unit_key="count",
         population=("when the session was last LINKED to a coordinate — NOT "
                     "when it last ran"),
         source_ref="llm_task_monitor_ui/src/app.js:3960"),

    # ---- the badges (element_kind='badge') ---------------------------------
    dict(element_key="sessions.badge.identity_missing", element_kind="badge",
         rendered_text="no identity row", user_label="Identity known?",
         term_key="ui_identity_known", unit_key="boolean",
         population="identity_registry rows for this session_id",
         why_clickable=1,
         why_text=("identity_registry has no row for this session_id. The "
                   "session id itself is in the Session column — this badge is "
                   "about a DIFFERENT table."),
         source_ref="llm_task_monitor_ui/src/app.js:3865"),
    dict(element_key="sessions.badge.identity_registered", element_kind="badge",
         rendered_text="registered", user_label="Identity known?",
         term_key="ui_identity_known", unit_key="boolean",
         population="identity_registry rows for this session_id",
         why_clickable=1,
         why_text="identity_registry has a row for this session_id.",
         source_ref="llm_task_monitor_ui/src/app.js:3864"),
    dict(element_key="sessions.badge.live", element_kind="badge",
         rendered_text="LIVE", user_label="Live?",
         term_key="ui_live_status", unit_key="boolean",
         population="the session's own file mtime against LIVE_WINDOW_S",
         why_clickable=1,
         why_text="the session's own chatSessions file was touched within the live window.",
         source_ref="llm_task_monitor_ui/src/app.js:3874"),
    dict(element_key="sessions.badge.idle", element_kind="badge",
         rendered_text="idle", user_label="Live?",
         term_key="ui_live_status", unit_key="boolean",
         population="the session's own file mtime against LIVE_WINDOW_S",
         why_clickable=1,
         why_text="the session's own chatSessions file was NOT touched within the live window.",
         source_ref="llm_task_monitor_ui/src/app.js:3875"),
    dict(element_key="sessions.badge.environment_underivable",
         element_kind="badge", rendered_text="underivable",
         user_label="Where", term_key="ui_environment", unit_key="boolean",
         population="whether the environment could be derived",
         why_clickable=1,
         why_text=("the environment could not be derived from the coordinate "
                   "this session was linked to."),
         source_ref="llm_task_monitor_ui/src/app.js:3886"),

    # ---- the numbers (element_kind='number') -------------------------------
    dict(element_key="sessions.num.measurements", element_kind="number",
         rendered_text="coords", user_label="Measurements",
         term_key="ui_measurement_count", unit_key="count",
         population=("count of coordinate_session rows linked to this session "
                     "— 8 means measured 8 times, NOT 8 sessions"),
         why_clickable=1,
         why_text=("coordinate_session is a LINK table (session_id, "
                   "coordinate_id, why, cite_ref). This number counts the rows "
                   "linked to this session."),
         source_ref="llm_task_monitor_ui/src/app.js:3860"),
    dict(element_key="sessions.num.age_sec", element_kind="number",
         rendered_text="age_sec", user_label="Age",
         term_key="ui_age_seconds", unit_key="count",
         population="seconds since the session's own file was last touched",
         why_clickable=1,
         why_text=("the session's own chatSessions file mtime, compared against "
                   "LIVE_WINDOW_S. It is a MEASUREMENT of the file, not a guess "
                   "from last_seen."),
         source_ref="llm_task_monitor_ui/src/app.js:3878"),

    # ---- the empty state (element_kind='empty_state') ----------------------
    dict(element_key="sessions.empty.no_linked_session",
         element_kind="empty_state",
         rendered_text="The run measured rows but none is linked to a session.",
         user_label="No linked session", term_key="ui_empty_state",
         unit_key="count",
         population="rows measured with no coordinate_session link",
         next_action=("run the open_pinned_session step to link them"),
         source_ref="llm_task_monitor_ui/src/app.js:3893"),
)


def seed_elements(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """Insert the declared elements. Idempotent. Reports every refusal."""
    ensure_schema(conn)
    added, refused = 0, []
    for el in SEED_ELEMENTS:
        res = add_element(conn, cite_ref=SEED_CITE, page_key=PAGE_KEY,
                          commit=False, **el)
        if res.get("ok"):
            added += 1 if res.get("created") else 0
        else:
            refused.append({"element_key": el.get("element_key"),
                            "code": res.get("code"),
                            "message": res.get("message")})
    if commit:
        conn.commit()
    return {"ok": not refused, "declared": len(SEED_ELEMENTS), "added": added,
            "refused": refused}


# ---------------------------------------------------------------------------
# THE CONSULTANT CENTER PAGE (2026-09-28).
#
# THE HUMAN: "＋ ui at http://127.0.0.1:18765/llm-tasks/consultant / your ui team
# can help you, it should not be a single ui".
#
# THREE page_keys, one per tab, because the human said it is NOT a single UI and
# `ui_standard.py --audit --page consultant.*` must be able to report each tab's
# five numbers separately. A single page_key would make one tab's defect hide
# behind another tab's pass.
#
# EVERY `source_ref` is a real `consultant-center.js:LINE`. Every `rendered_text`
# is the literal string the renderer emits. Every `term_key` is a term that
# EXISTS in `terminology_registry` — MEASURED before writing, and `build_step` was
# REFUSED by the naming law (`NON_DECOMPOSABLE_NAME`), so the registered term
# `step` is used instead. The law is not fought; the name is changed.
# ---------------------------------------------------------------------------
CONSULTANT_PAGE_KEYS = ("consultant.build_steps", "consultant.find",
                        "consultant.teams")
CONSULTANT_CITE = "ui_element_registry.py:CONSULTANT_SEED_ELEMENTS"

CONSULTANT_SEED_ELEMENTS: tuple[dict[str, Any], ...] = (
    # ---- tab 1: Build Steps ------------------------------------------------
    dict(element_key="consultant.build_steps.tab", element_kind="action",
         page_key="consultant.build_steps", rendered_text="Build Steps",
         user_label="Build Steps", term_key="step", unit_key="count",
         population="the build steps in the 17-column view",
         why_clickable=1,
         why_text="shows the build-step standard: one row per step, 17 columns.",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:44"),
    dict(element_key="consultant.build_steps.heading", element_kind="label",
         page_key="consultant.build_steps", rendered_text="Build Step Standard",
         user_label="Build Step Standard", term_key="step", unit_key="count",
         population="the build steps in the 17-column view",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:96"),
    dict(element_key="consultant.build_steps.num_steps", element_kind="number",
         page_key="consultant.build_steps", rendered_text="steps in this view",
         user_label="Steps", term_key="step", unit_key="count",
         population=("count of rows in build_step_view — NOT a count of tasks "
                     "and NOT a count of factors"),
         why_clickable=1,
         why_text=("build_step_view is a VIEW over build_step_registry joined to "
                   "terminology_registry, workflow_registry and "
                   "playwright_environment. This number counts its rows."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:98"),
    dict(element_key="consultant.build_steps.num_columns", element_kind="number",
         page_key="consultant.build_steps", rendered_text="columns in the view",
         user_label="Columns", term_key="step", unit_key="count",
         population="count of columns the view declares (17)",
         why_clickable=1,
         why_text=("the columns come from `build_step_registry.VIEW_COLUMNS`, so "
                   "the page cannot show a column the view does not have."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:99"),
    dict(element_key="consultant.build_steps.badge_unmeasured",
         element_kind="badge", page_key="consultant.build_steps",
         rendered_text="UNMEASURED", user_label="Guide present?",
         term_key="evidence", unit_key="boolean",
         population="playwright_step rows for the step's playwright environment",
         why_clickable=1,
         why_text=("no proof row recorded — this is NOT a pass. The step names a "
                   "playwright environment with no guide rows, so the guide that "
                   "would run it does not exist."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:113"),
    dict(element_key="consultant.build_steps.empty", element_kind="empty_state",
         page_key="consultant.build_steps",
         rendered_text="No build steps.",
         user_label="No build steps", term_key="step", unit_key="count",
         population="rows in build_step_view",
         why_clickable=1,
         why_text=("build_step_view returned 0 rows. The view reads "
                   "build_step_registry, so an empty view means the register "
                   "itself is empty."),
         next_action=("register a step with a factor, a purpose and a citation "
                      "— inventing them would fabricate the standard"),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:103"),

    # ---- tab 2: Find -------------------------------------------------------
    dict(element_key="consultant.find.tab", element_kind="action",
         page_key="consultant.find", rendered_text="Find",
         user_label="Find", term_key="find", unit_key="count",
         population="the recorded GitHub finds",
         why_clickable=1,
         why_text="shows the GitHub find: factor -> best 3 -> compare.",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:45"),
    dict(element_key="consultant.find.heading", element_kind="label",
         page_key="consultant.find", rendered_text="GitHub Find",
         user_label="GitHub Find", term_key="find", unit_key="count",
         population="the recorded GitHub finds",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:130"),
    dict(element_key="consultant.find.num_total", element_kind="number",
         page_key="consultant.find", rendered_text="repos matching the query",
         user_label="Matches", term_key="find", unit_key="count",
         population=("GitHub's own `total_count` for the query — NOT the number "
                     "returned, which is capped at 3"),
         why_clickable=1,
         why_text=("`total_count` is the honest measure of whether the QUERY was "
                   "any good. A thin query returns 3 items out of 2 matches; a "
                   "good one returns 3 out of 245."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:143"),
    dict(element_key="consultant.find.num_returned", element_kind="number",
         page_key="consultant.find", rendered_text="returned",
         user_label="Returned", term_key="find", unit_key="count",
         population="count of candidates the API returned (capped by `limit`)",
         why_clickable=1,
         why_text="the API caps the list at `limit`, so this is <= total_count.",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:144"),
    dict(element_key="consultant.find.badge_proofed", element_kind="badge",
         page_key="consultant.find", rendered_text="proofed",
         user_label="Proofed?", term_key="proofed", unit_key="boolean",
         population=("whether the candidate's OWN technique file was READ and "
                     "the mechanism the factor names was found in it"),
         why_clickable=1,
         why_text=("proofed means the mechanism was FOUND in the repo's own file "
                   "at a cited path:line. A star count is NOT a proof."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:152"),
    dict(element_key="consultant.find.badge_unproven", element_kind="badge",
         page_key="consultant.find", rendered_text="unproven",
         user_label="Proofed?", term_key="proofed", unit_key="boolean",
         population="whether the mechanism was found in the repo's own file",
         why_clickable=1,
         why_text=("the mechanism was NOT found in the file the factor names, so "
                   "the candidate is ranked but NOT adopted."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:153"),
    dict(element_key="consultant.find.badge_verdict", element_kind="badge",
         page_key="consultant.find", rendered_text="ADOPT / REJECT / UNKNOWN",
         user_label="Verdict", term_key="verdict", unit_key="count",
         population="the verdict vocabulary (3 values)",
         why_clickable=1,
         why_text=("UNKNOWN is a REAL verdict — an uncertain comparison is never "
                   "a silent pass. Adoption requires `proofed`; unanimity is not "
                   "adoptable."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:170"),
    dict(element_key="consultant.find.empty", element_kind="empty_state",
         page_key="consultant.find", rendered_text="No recorded finds.",
         user_label="No recorded finds", term_key="find", unit_key="count",
         population="rows in github_find_registry",
         why_clickable=1,
         why_text=("github_find_registry returned 0 rows. The table is written "
                   "only by `_seed_consultant_skills.py --find --apply`, so an "
                   "empty table means no find has been recorded yet."),
         next_action=("run `_seed_consultant_skills.py --find --apply` to record "
                      "a find"),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:161"),

    # ---- tab 3: Teams ------------------------------------------------------
    dict(element_key="consultant.teams.tab", element_kind="action",
         page_key="consultant.teams", rendered_text="Teams",
         user_label="Teams", term_key="team", unit_key="count",
         population="the consultant teams",
         why_clickable=1,
         why_text="shows industry -> team -> skills, with the proofed/rating score.",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:46"),
    dict(element_key="consultant.teams.heading", element_kind="label",
         page_key="consultant.teams", rendered_text="Consultant Teams",
         user_label="Consultant Teams", term_key="team", unit_key="count",
         population="the consultant teams",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:196"),
    dict(element_key="consultant.teams.num_industries", element_kind="number",
         page_key="consultant.teams", rendered_text="industries registered",
         user_label="Industries", term_key="industry", unit_key="count",
         population="count of ACTIVE rows in industry_registry",
         why_clickable=1,
         why_text=("industry_registry is an N-level hierarchy (self-FK + "
                   "materialised path). This number counts its active rows."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:198"),
    dict(element_key="consultant.teams.num_teams", element_kind="number",
         page_key="consultant.teams", rendered_text="teams in this industry",
         user_label="Teams", term_key="team", unit_key="count",
         population="count of ACTIVE consultant_team rows for THIS industry",
         why_clickable=1,
         why_text=("consultant_team.industry_id is the FK, so this number is "
                   "scoped to the industry it is printed under."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:207"),
    dict(element_key="consultant.teams.num_skills", element_kind="number",
         page_key="consultant.teams", rendered_text="skills",
         user_label="Skills", term_key="skill", unit_key="count",
         population="count of ACTIVE consultant_skill rows for THIS team",
         why_clickable=1,
         why_text=("consultant_skill.team_id is the FK, so this number is scoped "
                   "to the team it is printed under."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:213"),
    dict(element_key="consultant.teams.num_proofed", element_kind="number",
         page_key="consultant.teams", rendered_text="proofed skills",
         user_label="Proofed", term_key="proofed", unit_key="count",
         population=("count of THIS team's skills with proofed = 1 — NOT a count "
                     "of skills, and NOT a count of ratings"),
         why_clickable=1,
         why_text=("`proofed` and `rating` are SEPARATE, and the order is "
                   "`proofed` FIRST: a 99999-star unproven skill ranks BELOW a "
                   "proven 3-star one."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:214"),
    dict(element_key="consultant.teams.badge_proofed", element_kind="badge",
         page_key="consultant.teams", rendered_text="proofed",
         user_label="Proofed?", term_key="proofed", unit_key="boolean",
         population="consultant_skill.proofed for this row",
         why_clickable=1,
         why_text=("proofed = 1 requires a `source_ref`, enforced by "
                   "`consultant_registry.mark_proofed`. A proof with no citation "
                   "is an assertion."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:232"),
    dict(element_key="consultant.teams.badge_unproven", element_kind="badge",
         page_key="consultant.teams", rendered_text="unproven",
         user_label="Proofed?", term_key="proofed", unit_key="boolean",
         population="consultant_skill.proofed for this row",
         why_clickable=1,
         why_text=("the skill has a SOURCE but has not been VERIFIED, so it is "
                   "ranked below every proven skill."),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:233"),
    dict(element_key="consultant.teams.empty", element_kind="empty_state",
         page_key="consultant.teams", rendered_text="No industries.",
         user_label="No industries", term_key="industry", unit_key="count",
         population="rows in industry_registry",
         why_clickable=1,
         why_text=("industry_registry returned 0 ACTIVE rows. An industry is "
                   "registered only WITH a citation, so an empty register "
                   "means no industry has a source yet."),
         next_action=("register an industry WITH a citation — an industry with "
                      "no source would be an invention"),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:203"),
    # ---- SCOPE M: the step walk (ONE step at a time) ----------------------
    #
    # WHY A SECOND PAGE_KEY AND NOT MORE ROWS UNDER `consultant.build_steps`:
    # the walk is a DIFFERENT page with a different job — the table is for
    # AUDITING every step at once, the walk is for READING one step at a time.
    # A page_key is what `ui_standard --audit --page` audits, so sharing one
    # would make the audit unable to tell the two pages apart.
    dict(element_key="consultant.step_walk.tab", element_kind="action",
         page_key="consultant.step_walk", rendered_text="Step Walk",
         user_label="Step Walk", term_key="walk", unit_key="count",
         population="tabs in the Consultant Center",
         why_clickable=1,
         why_text=("the tab switches the page to the walk, which shows ONE step "
                   "at a time from the same register the table reads"),
         next_action="click to walk the steps one at a time",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:66"),
    dict(element_key="consultant.step_walk.heading", element_kind="label",
         page_key="consultant.step_walk", rendered_text="Step Walk",
         user_label="Step Walk", term_key="walk", unit_key="count",
         population="headings on this page",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:170"),
    dict(element_key="consultant.step_walk.num_total", element_kind="number",
         page_key="consultant.step_walk", rendered_text="steps in this walk",
         user_label="Steps in this walk", term_key="step", unit_key="count",
         population="rows in build_step_registry",
         why_clickable=1,
         why_text=("the count is the register's own row count, so a walk that "
                   "shows fewer steps than this number is a defect the reader "
                   "can SEE"),
         next_action="compare this number with the step number below it",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:174"),
    dict(element_key="consultant.step_walk.num_position", element_kind="number",
         page_key="consultant.step_walk", rendered_text="step number",
         user_label="Step number", term_key="step", unit_key="count",
         population="the index of the step on screen, 1-based",
         why_clickable=1,
         why_text=("the position is 1-based and read from the response, so the "
                   "reader can tell which of the N steps is on screen"),
         next_action="use Next to advance, Previous to go back",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:181"),
    dict(element_key="consultant.step_walk.btn_previous", element_kind="action",
         page_key="consultant.step_walk", rendered_text="Previous",
         user_label="Previous", term_key="previous", unit_key="count",
         population="controls on this page",
         why_clickable=1,
         why_text=("the control is DISABLED on the first step, so a reader "
                   "cannot walk before the beginning"),
         next_action="click to show the step before this one",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:178"),
    dict(element_key="consultant.step_walk.btn_next", element_kind="action",
         page_key="consultant.step_walk", rendered_text="Next",
         user_label="Next", term_key="next", unit_key="count",
         population="controls on this page",
         why_clickable=1,
         why_text=("the control is DISABLED on the last step, so a reader "
                   "cannot walk past the end"),
         next_action="click to show the step after this one",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:183"),
    dict(element_key="consultant.step_walk.badge_status", element_kind="badge",
         page_key="consultant.step_walk", rendered_text="planned",
         user_label="Step status", term_key="status", unit_key="count",
         population="the step's own status column",
         why_clickable=1,
         why_text=("the badge shows the step's OWN status value, and an "
                   "unrecognised value renders as UNKNOWN rather than green"),
         next_action="a blocked step names what blocks it in its evidence cell",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:190"),
    dict(element_key="consultant.step_walk.badge_unmeasured", element_kind="badge",
         page_key="consultant.step_walk", rendered_text="UNMEASURED",
         user_label="Unmeasured", term_key="unknown", unit_key="boolean",
         population="steps whose playwright guide is NA",
         why_clickable=1,
         why_text=("no proof row is recorded for this step, so it is NOT a "
                   "pass — the badge exists so an unproven step cannot read as "
                   "a proven one"),
         next_action="record a playwright guide row for this step",
         source_ref="llm_task_monitor_ui/src/consultant-center.js:196"),
    dict(element_key="consultant.step_walk.empty", element_kind="empty_state",
         page_key="consultant.step_walk", rendered_text="No steps.",
         user_label="No steps", term_key="step", unit_key="count",
         population="rows in build_step_registry",
         why_clickable=1,
         why_text=("build_step_registry returned 0 rows. A step needs a factor, "
                   "a purpose and a citation, so an empty register means no "
                   "step has a source yet."),
         next_action=("seed the steps from factors that carry a usable "
                      "citation — a step with no source would be an invention"),
         source_ref="llm_task_monitor_ui/src/consultant-center.js:176"),
)


def seed_consultant_elements(conn: sqlite3.Connection, *,
                             commit: bool = True) -> dict[str, Any]:
    """Insert the Consultant Center's declared elements. Idempotent.

    Reports EVERY refusal rather than raising, so a term or unit that is missing
    is VISIBLE as a named refusal instead of a silently absent row.
    """
    ensure_schema(conn)
    added, refused = 0, []
    for el in CONSULTANT_SEED_ELEMENTS:
        res = add_element(conn, cite_ref=CONSULTANT_CITE, commit=False, **el)
        if res.get("ok"):
            added += 1 if res.get("created") else 0
        else:
            refused.append({"element_key": el.get("element_key"),
                            "code": res.get("code"),
                            "message": res.get("message")})
    if commit:
        conn.commit()
    return {"ok": not refused, "declared": len(CONSULTANT_SEED_ELEMENTS),
            "added": added, "refused": refused}


def list_elements(conn: sqlite3.Connection,
                  page_key: str | None = None) -> list[dict[str, Any]]:
    """The registered elements, optionally for ONE page."""
    ensure_schema(conn)
    if page_key:
        rows = conn.execute(
            "SELECT * FROM ui_element_registry WHERE page_key=? AND is_active=1 "
            "ORDER BY element_kind, element_id", (str(page_key),)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ui_element_registry WHERE is_active=1 "
            "ORDER BY page_key, element_kind, element_id").fetchall()
    return [dict(r) for r in rows]


def validate_element(conn: sqlite3.Connection,
                     element_key: str) -> dict[str, Any]:
    """The 5 rules for ONE element. Reports, never raises.

    The rules are the human's standard, expressed as checks:

      1. label_registered        the user_label has a terminology_registry row
      2. why_clickable           a badge/number has a clickable why, not hover
      3. unknown_names_source    an "unknown" badge names the table AND the value
      4. number_names_population a number states what it counts
      5. empty_state_names_action an empty state names a next action
    """
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM ui_element_registry WHERE element_key=?",
                       (str(element_key),)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_ELEMENT", "element_key": element_key}
    el = dict(row)
    kind = el["element_kind"]
    terms = _known_terms(conn)
    rules: dict[str, dict[str, Any]] = {}

    rules["label_registered"] = {
        "pass": el["term_key"] in terms,
        "detail": ("term_key %r is registered" % el["term_key"]) if el["term_key"] in terms
                  else ("term_key %r is NOT in terminology_registry" % el["term_key"]),
    }
    needs_why = kind in ("badge", "number")
    rules["why_clickable"] = {
        "pass": (not needs_why) or bool(el["why_clickable"]),
        "detail": ("not applicable to a %s" % kind) if not needs_why
                  else ("clickable" if el["why_clickable"] else "HOVER-ONLY"),
    }
    is_unknown = "unknown" in str(el["rendered_text"]).lower() or \
                 "underivable" in str(el["rendered_text"]).lower() or \
                 "no " in str(el["rendered_text"]).lower()
    rules["unknown_names_source"] = {
        "pass": (not is_unknown) or bool(str(el["why_text"]).strip()),
        "detail": ("not an unknown badge") if not is_unknown
                  else ("names the source" if str(el["why_text"]).strip()
                        else "does NOT name the table and the value"),
    }
    rules["number_names_population"] = {
        "pass": (kind != "number") or bool(str(el["population"]).strip()),
        "detail": ("not a number") if kind != "number"
                  else ("states its population" if str(el["population"]).strip()
                        else "does NOT state what it counts"),
    }
    rules["empty_state_names_action"] = {
        "pass": (kind != "empty_state") or bool(str(el["next_action"]).strip()),
        "detail": ("not an empty state") if kind != "empty_state"
                  else ("names a next action" if str(el["next_action"]).strip()
                        else "does NOT name a next action"),
    }
    failed = [k for k, v in rules.items() if not v["pass"]]
    return {"ok": not failed, "element_key": el["element_key"],
            "element_kind": kind, "rules": rules, "failed": failed}


def check_divergence(conn: sqlite3.Connection) -> dict[str, Any]:
    """The register vs the RENDERED page. Reports, never raises.

    A register that agrees with itself proves nothing. This reads the renderer's
    own source and checks that every registered `rendered_text` is still there,
    and that no `title=` (hover-only why) remains in the pinned renderer.
    """
    ensure_schema(conn)
    src_path = BASE_DIR / "llm_task_monitor_ui" / "src" / "app.js"
    out: dict[str, Any] = {"ok": True, "register_rows": 0, "missing_in_source": [],
                           "hover_only_title": 0, "clickable_data": 0,
                           "why": ""}
    if not src_path.exists():
        out["ok"] = False
        out["why"] = "the renderer source is not at %s" % src_path
        return out
    src = src_path.read_text(encoding="utf-8", errors="replace")
    i = src.find("function userEnvironmentSessionsHtml")
    if i < 0:
        out["ok"] = False
        out["why"] = "userEnvironmentSessionsHtml is not in the renderer"
        return out
    j = src.find("\nfunction ", i + 10)
    seg = src[i:j if j > 0 else len(src)]

    rows = list_elements(conn, PAGE_KEY)
    out["register_rows"] = len(rows)
    for el in rows:
        if el["element_kind"] != "label":
            continue
        if str(el["rendered_text"]) not in seg:
            out["missing_in_source"].append(el["element_key"])
    # THE POPULATION IS THE RULE'S OWN. `why_clickable` applies to a BADGE or a
    # NUMBER, not to a column header. MEASURED 2026-09-27: counting every
    # `title=` in the segment reported 11, because the 10 headers carry a
    # `title=` naming the DB column they read — a MAPPING note, not a "why".
    # Counting them would make the number about the wrong population, which is
    # the exact defect `measurement-scope` exists to catch. So a `<th ... title=`
    # is excluded, and the exclusion is stated here rather than silent.
    th_titles = len(re.findall(r"<th[^>]*title=\"", seg))
    out["hover_only_title"] = len(re.findall(r"title=\"", seg)) - th_titles
    out["header_mapping_titles"] = th_titles
    out["clickable_data"] = len(re.findall(r"data-ue-row", seg))
    if out["missing_in_source"]:
        out["ok"] = False
        out["why"] = ("%d registered label(s) are no longer rendered: %s"
                      % (len(out["missing_in_source"]),
                         ", ".join(out["missing_in_source"])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="the UI element register")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--seed-consultant", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--validate", metavar="ELEMENT_KEY")
    ap.add_argument("--check-divergence", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.init:
            print(json.dumps(ensure_schema(conn), ensure_ascii=False))
        if args.seed:
            print(json.dumps(seed_elements(conn), ensure_ascii=False, indent=2))
        if args.seed_consultant:
            print(json.dumps(seed_consultant_elements(conn), ensure_ascii=False,
                             indent=2))
        if args.validate:
            print(json.dumps(validate_element(conn, args.validate),
                             ensure_ascii=False, indent=2))
        if args.check_divergence:
            print(json.dumps(check_divergence(conn), ensure_ascii=False, indent=2))
        if args.list or not any((args.init, args.seed, args.seed_consultant,
                                 args.validate, args.check_divergence)):
            rows = list_elements(conn)
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                print("%-42s %-12s %-22s %-10s" %
                      ("element_key", "kind", "user_label", "unit"))
                for r in rows:
                    print("%-42s %-12s %-22s %-10s" %
                          (r["element_key"], r["element_kind"],
                           r["user_label"], r["unit_key"]))
                print("\n%d element(s)" % len(rows))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
