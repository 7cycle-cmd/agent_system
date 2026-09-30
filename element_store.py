# -*- coding: utf-8 -*-
"""element_store.py — the UI element list + the goals a worker can be given.

WHY THIS FILE EXISTS
--------------------
The user asked for "ui for all the element list, user instruction for how worker
can have the goal". A table of elements is what turns "guess a selector" into
"look up a measured selector" — and a selector written from memory is the DOM
equivalent of a hardcoded coordinate: right until the layout changes, then
silently wrong.

WHY A SEPARATE DB (not coord_store's coords.db)
-----------------------------------------------
`coords.db.target_position` holds SCREEN-space rectangles with five CHECK
invariants. An element record is a DIFFERENT fact: it carries a TAG, a FRAME and
a text MATCHER, and its rect is FRAME-LOCAL, not screen-space. Putting both in
one table would either weaken those CHECKs or force a unit onto a column that
does not have one. Two facts, two tables.

COORDINATE SPACE IS RECORDED, NEVER ASSUMED
-------------------------------------------
A rect measured inside an iframe is FRAME-LOCAL: clicking it at page level hits
the wrong element whenever the iframe is not at (0,0). Measured 2026-09-23: the
工作伙伴 tabs live in FRAME 1 while the task list lives in FRAME 0. So every row
states its `coord_space` and its `frame`, and a row that omits them is refused.

CITATION IS REQUIRED, NOT DECORATION
------------------------------------
Every row must name the command that measured it (`measured_by`) and the artefact
it was written to (`source_artifact`). A rect with no citation is a memory, and a
memory is exactly what this table exists to replace. `upsert_element` REFUSES a
row without both — a finding without a checkable reference is discarded at the
write site, not downgraded.

Usage:
  python element_store.py --init
  python element_store.py --seed
  python element_store.py --list
  python element_store.py --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "elements.db"

# `coord_space` values. Frame-local and screen-space coordinates are NOT
# interchangeable, so a row must say which one it is.
COORD_SPACES = ("frame", "window")

_CREATE_UI_ELEMENT_SQL = """
CREATE TABLE IF NOT EXISTS ui_element (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    element_key     TEXT    NOT NULL UNIQUE,
    app_key         TEXT    NOT NULL,
    step_no         INTEGER,
    name            TEXT    NOT NULL,
    tag             TEXT    NOT NULL,
    frame           INTEGER NOT NULL,
    coord_space     TEXT    NOT NULL CHECK (coord_space IN ('frame', 'window')),
    role            TEXT    NOT NULL DEFAULT '',
    exact           INTEGER NOT NULL DEFAULT 0 CHECK (exact IN (0, 1)),
    alt             TEXT    NOT NULL DEFAULT '[]',
    must            TEXT    NOT NULL DEFAULT '[]',
    x1 INTEGER NOT NULL, y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL, y2 INTEGER NOT NULL,
    width  INTEGER NOT NULL,
    height INTEGER NOT NULL,
    cx     INTEGER NOT NULL,
    cy     INTEGER NOT NULL,
    page_url_glob   TEXT    NOT NULL DEFAULT '',
    measured_at     TEXT    NOT NULL,
    measured_by     TEXT    NOT NULL,
    source_artifact TEXT    NOT NULL,
    note            TEXT    NOT NULL DEFAULT '',
    -- THE MEASURED UNIT (added 2026-09-27).
    --
    -- THE HUMAN: "2) by table too, same style with _register, so everything can
    -- measure with measured unit". MEASURED before this: `element_store.ui_element`
    -- had NO unit column at all, so a desktop element could be registered with a
    -- rect and a selector but nothing said WHAT it measures. The UI standard
    -- (`ui_standard.py`) requires a `unit_key` on every element; this column
    -- brings the SAME rule to the desktop table, so the two registers cannot
    -- disagree about what "a measured element" means.
    --
    -- It is ADDITIVE and DEFAULTED, so the 18 existing rows stay valid: a row
    -- written before this column existed carries `''`, which is REPORTED by
    -- `check_unit_coverage()` rather than silently treated as a unit.
    unit_key        TEXT    NOT NULL DEFAULT '',
    isactive        INTEGER NOT NULL DEFAULT 1 CHECK (isactive IN (0, 1)),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK (x2 > x1 AND y2 > y1),
    CHECK (width  = x2 - x1),
    CHECK (height = y2 - y1),
    CHECK (cx = (x1 + x2) / 2),
    CHECK (cy = (y1 + y2) / 2)
);
CREATE INDEX IF NOT EXISTS idx_ui_element_app_step
  ON ui_element (app_key, step_no)
"""

# A goal is an INSTRUCTION A WORKER CAN BE GIVEN, not a label. `done_when` must
# be mechanically checkable, because an instruction whose completion is a matter
# of opinion cannot be handed to a worker and audited afterwards.
_CREATE_GOAL_SQL = """
CREATE TABLE IF NOT EXISTS goal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_key    TEXT    NOT NULL UNIQUE,
    app_key     TEXT    NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0,
    title       TEXT    NOT NULL,
    instruction TEXT    NOT NULL,
    done_when   TEXT    NOT NULL,
    evidence_kind TEXT  NOT NULL,
    element_keys  TEXT  NOT NULL DEFAULT '[]',
    requires_success_count INTEGER NOT NULL DEFAULT 1
                            CHECK (requires_success_count >= 1),
    isactive    INTEGER NOT NULL DEFAULT 1 CHECK (isactive IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> Path:
    path = Path(db_path) if db_path else DB_PATH
    with _conn(path) as conn:
        conn.executescript(_CREATE_UI_ELEMENT_SQL)
        conn.executescript(_CREATE_GOAL_SQL)
        _migrate_unit_key(conn)
    return path


def _migrate_unit_key(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add `unit_key` to an EXISTING `ui_element` table. Idempotent.

    WHY A MIGRATION AND NOT ONLY A DDL CHANGE
    -----------------------------------------
    `CREATE TABLE IF NOT EXISTS` does NOTHING to a table that already exists, so
    a database created before 2026-09-27 would keep a `ui_element` with no
    `unit_key` and every INSERT naming the column would fail. MEASURED: the
    repo's own `coordinate_session_registry.ensure_schema` uses exactly this
    `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pattern, so it is REUSED
    rather than re-invented.

    The column is added with `DEFAULT ''`, so the existing rows stay valid and
    their missing unit is REPORTED by `check_unit_coverage()` — never silently
    treated as a unit.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ui_element)")}
    if "unit_key" in cols:
        return {"ok": True, "added": False, "column": "unit_key"}
    conn.execute("ALTER TABLE ui_element ADD COLUMN unit_key TEXT NOT NULL "
                 "DEFAULT ''")
    conn.commit()
    return {"ok": True, "added": True, "column": "unit_key"}


def check_unit_coverage(db_path: Path | None = None) -> dict[str, Any]:
    """How many `ui_element` rows carry a MEASURED unit. Reports, never raises.

    THE HUMAN: "everything can measure with measured unit". A row with an empty
    `unit_key` is an element whose unit was never stated, so it is COUNTED and
    NAMED rather than assumed to be fine.
    """
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        return {"ok": False, "why": "no elements.db at %s" % path,
                "total": 0, "with_unit": 0, "without_unit": 0, "keys": []}
    with _conn(path) as conn:
        _migrate_unit_key(conn)
        total = conn.execute("SELECT COUNT(*) FROM ui_element").fetchone()[0]
        with_unit = conn.execute(
            "SELECT COUNT(*) FROM ui_element WHERE TRIM(unit_key) <> ''"
        ).fetchone()[0]
        keys = [r[0] for r in conn.execute(
            "SELECT element_key FROM ui_element WHERE TRIM(unit_key) = '' "
            "ORDER BY element_key")]
    return {"ok": with_unit == total, "total": total, "with_unit": with_unit,
            "without_unit": total - with_unit, "keys": keys,
            "why": ("" if with_unit == total else
                    "%d of %d element(s) state no unit" % (total - with_unit, total))}


def rect_metrics(x1: int, y1: int, x2: int, y2: int) -> dict[str, int]:
    """width = x2-x1, height = y2-y1, cx/cy = midpoints (user-confirmed rule).

    Refuses a degenerate or inverted rect instead of storing one: an inverted
    rect would satisfy nothing downstream, and a zero-area rect is a measurement
    failure wearing the costume of a measurement.
    """
    for v in (x1, y1, x2, y2):
        if not isinstance(v, int):
            raise ValueError("rect coordinates must be ints, got %r" % (v,))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(
            "rect must satisfy x2>x1 and y2>y1, got [%d,%d,%d,%d]" % (x1, y1, x2, y2))
    return {"width": x2 - x1, "height": y2 - y1,
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2}


def upsert_element(
    element_key: str,
    app_key: str,
    name: str,
    tag: str,
    frame: int,
    coord_space: str,
    x1: int, y1: int, x2: int, y2: int,
    *,
    step_no: int | None = None,
    role: str = "",
    exact: bool = False,
    alt: list[str] | None = None,
    must: list[str] | None = None,
    page_url_glob: str = "",
    measured_by: str = "",
    source_artifact: str = "",
    note: str = "",
    unit_key: str = "",
    isactive: bool = True,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert or update ONE element. Refuses an uncited or space-less row.

    REFUSALS (each one is a defect this table exists to prevent):
      * no `measured_by` or no `source_artifact` -> an uncited rect is a memory.
      * `coord_space` not in COORD_SPACES          -> frame-local vs screen-space
        must never be guessed, because the two are not interchangeable.
      * `frame` absent/negative                    -> the frame is part of the
        target; measured that it moves between steps.
      * degenerate rect                            -> a failed measurement.

    `unit_key` (added 2026-09-27) is OPTIONAL here and DEFAULTED to `''`, because
    the 18 existing rows predate the column. It is NOT refused when empty — an
    empty unit is REPORTED by `check_unit_coverage()` rather than blocking a
    write, so the migration cannot break a live caller. A NEW row SHOULD state
    one: the human's rule is "everything can measure with measured unit".
    """
    key = str(element_key or "").strip()
    if not key:
        raise ValueError("element_key required")
    if not str(tag or "").strip():
        raise ValueError("tag required (element_key=%s)" % key)
    if not str(name or "").strip():
        raise ValueError("name required (element_key=%s)" % key)
    space = str(coord_space or "").strip().lower()
    if space not in COORD_SPACES:
        raise ValueError(
            "coord_space must be one of %s (element_key=%s)" % (COORD_SPACES, key))
    try:
        fr = int(frame)
    except (TypeError, ValueError):
        raise ValueError("frame must be an integer (element_key=%s)" % key)
    if fr < 0:
        raise ValueError("frame must be >= 0 (element_key=%s)" % key)
    if not str(measured_by or "").strip():
        raise ValueError(
            "measured_by required (element_key=%s): name the command that "
            "measured this rect" % key)
    if not str(source_artifact or "").strip():
        raise ValueError(
            "source_artifact required (element_key=%s): name the JSON/MD the "
            "measurement went to" % key)

    m = rect_metrics(x1, y1, x2, y2)
    init_db(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        _migrate_unit_key(conn)
        conn.execute(
            """
            INSERT INTO ui_element
                (element_key, app_key, step_no, name, tag, frame, coord_space,
                 role, exact, alt, must,
                 x1, y1, x2, y2, width, height, cx, cy,
                 page_url_glob, measured_at, measured_by, source_artifact,
                 note, unit_key, isactive, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(element_key) DO UPDATE SET
                app_key=excluded.app_key, step_no=excluded.step_no,
                name=excluded.name, tag=excluded.tag, frame=excluded.frame,
                coord_space=excluded.coord_space, role=excluded.role,
                exact=excluded.exact, alt=excluded.alt, must=excluded.must,
                x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, y2=excluded.y2,
                width=excluded.width, height=excluded.height,
                cx=excluded.cx, cy=excluded.cy,
                page_url_glob=excluded.page_url_glob,
                measured_at=excluded.measured_at,
                measured_by=excluded.measured_by,
                source_artifact=excluded.source_artifact,
                note=excluded.note, unit_key=excluded.unit_key,
                isactive=excluded.isactive,
                updated_at=excluded.updated_at
            """,
            (key, str(app_key or "").strip(), step_no, str(name).strip(),
             str(tag).strip().lower(), fr, space,
             str(role or "").strip(), int(bool(exact)),
             json.dumps(list(alt or []), ensure_ascii=False),
             json.dumps(list(must or []), ensure_ascii=False),
             int(x1), int(y1), int(x2), int(y2),
             m["width"], m["height"], m["cx"], m["cy"],
             str(page_url_glob or ""), ts,
             str(measured_by).strip(), str(source_artifact).strip(),
             str(note or ""), str(unit_key or "").strip(),
             int(bool(isactive)), ts),
        )
    return get_element(key, db_path=db_path) or {"element_key": key}


def get_element(element_key: str, *, db_path: Path | None = None) -> dict | None:
    init_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute("SELECT * FROM ui_element WHERE element_key = ?",
                           (str(element_key).strip(),)).fetchone()
    return _element_row(row) if row else None


def list_elements(
    *, app_key: str | None = None, active_only: bool = True,
    db_path: Path | None = None,
) -> list[dict]:
    """Elements in STEP order, with unsequenced rows last (step_no NULL sorts last).

    STEP order is the READ order here: the walk is a path, so a list sorted by
    anything else makes the path unreadable.
    """
    init_db(db_path)
    where, params = [], []
    if app_key:
        where.append("app_key = ?")
        params.append(str(app_key))
    if active_only:
        where.append("isactive = 1")
    sql = "SELECT * FROM ui_element"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY (step_no IS NULL), step_no, id"
    with _conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_element_row(r) for r in rows]


def _element_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["alt"] = json.loads(d.get("alt") or "[]")
    d["must"] = json.loads(d.get("must") or "[]")
    d["exact"] = bool(d.get("exact"))
    d["isactive"] = bool(d.get("isactive"))
    d["rect"] = [d["x1"], d["y1"], d["x2"], d["y2"]]
    return d


def upsert_goal(
    goal_key: str,
    app_key: str,
    title: str,
    instruction: str,
    done_when: str,
    evidence_kind: str,
    *,
    element_keys: list[str] | None = None,
    requires_success_count: int = 1,
    seq: int = 0,
    isactive: bool = True,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Insert or update ONE goal.

    `done_when` MUST be mechanically checkable, so `instruction` cannot describe
    a goal whose completion is a matter of opinion. All four text fields are
    required for that reason — an empty `done_when` would make the goal
    unfalsifiable, which is the one thing a worker instruction cannot be.
    """
    for field, value in (("goal_key", goal_key), ("title", title),
                         ("instruction", instruction), ("done_when", done_when),
                         ("evidence_kind", evidence_kind)):
        if not str(value or "").strip():
            raise ValueError("%s required" % field)
    n = int(requires_success_count)
    if n < 1:
        raise ValueError("requires_success_count must be >= 1, got %r" % n)
    init_db(db_path)
    ts = _now()
    with _conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO goal
                (goal_key, app_key, seq, title, instruction, done_when,
                 evidence_kind, element_keys, requires_success_count,
                 isactive, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(goal_key) DO UPDATE SET
                app_key=excluded.app_key, seq=excluded.seq, title=excluded.title,
                instruction=excluded.instruction, done_when=excluded.done_when,
                evidence_kind=excluded.evidence_kind,
                element_keys=excluded.element_keys,
                requires_success_count=excluded.requires_success_count,
                isactive=excluded.isactive, updated_at=excluded.updated_at
            """,
            (str(goal_key).strip(), str(app_key or "").strip(), int(seq),
             str(title).strip(), str(instruction).strip(),
             str(done_when).strip(), str(evidence_kind).strip(),
             json.dumps(list(element_keys or []), ensure_ascii=False),
             n, int(bool(isactive)), ts),
        )
    return get_goal(goal_key, db_path=db_path) or {"goal_key": goal_key}


def get_goal(goal_key: str, *, db_path: Path | None = None) -> dict | None:
    init_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute("SELECT * FROM goal WHERE goal_key = ?",
                           (str(goal_key).strip(),)).fetchone()
    return _goal_row(row) if row else None


def list_goals(*, app_key: str | None = None, active_only: bool = True,
               db_path: Path | None = None) -> list[dict]:
    init_db(db_path)
    where, params = [], []
    if app_key:
        where.append("app_key = ?")
        params.append(str(app_key))
    if active_only:
        where.append("isactive = 1")
    sql = "SELECT * FROM goal"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY seq, id"
    with _conn(db_path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_goal_row(r) for r in rows]


def _goal_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["element_keys"] = json.loads(d.get("element_keys") or "[]")
    d["isactive"] = bool(d.get("isactive"))
    return d


# ---------------------------------------------------------------------------
# SEED — MEASURED ROWS ONLY
# ---------------------------------------------------------------------------
# Every row below came from a run in this session. The citation is the command
# (reproducible) plus the artefact (readable). A row that could not be cited is
# NOT here; it stays unmeasured rather than becoming a plausible-looking guess.
#
# MEASURED BY : python walk_nav.py --run --steps 2
# ARTEFACT    : qc_evidence/nav_walk.json, qc_evidence/nav_step0.json
# MEASURED BY : python _diag_doubao_team.py
# ARTEFACT    : qc_evidence/diag_doubao_team.json, qc_evidence/doubao_team_page.md
# MEASURED BY : python _diag_doubao_page.py
# ARTEFACT    : qc_evidence/diag_page.json, qc_evidence/diag_page_digest.txt
NAV_BY = "python walk_nav.py --run --steps 4"
TEAM_BY = "python _diag_doubao_team.py"
PAGE_BY = "python _diag_doubao_page.py"

SEED_ELEMENTS: tuple[dict, ...] = (
    # --- nav path, frame 0 (outer shell) ---------------------------------
    dict(element_key="doubao.nav.skills", app_key="doubao", step_no=0,
         unit_key="count",  # step 0 of the nav walk: 1 click from the default page
         name="插件 · 技能 · 伙伴", tag="a", frame=0, coord_space="frame",
         x1=9, y1=153, x2=271, y2=185,
         must=["插件", "伙伴"],
         page_url_glob="chrome://doubao-chat/chat*",
         measured_by=NAV_BY, source_artifact="qc_evidence/nav_walk.json",
         note="STEP 0. Clicking it changes the url to /chat/skills. This step is "
              "NOT in the user's description but is REQUIRED: the default "
              "chrome://doubao-chat/chat page contains ZERO occurrences of "
              "工作伙伴 / 产品研发 / 软件研发小组."),
    dict(element_key="doubao.nav.partner", app_key="doubao", step_no=1,
         unit_key="count",  # step 1 of the nav walk: 2 clicks from the default page
         name="工作伙伴", tag="button", frame=0, coord_space="frame",
         x1=446, y1=8, x2=510, y2=48, exact=True,
         page_url_glob="chrome://doubao-chat/chat/skills*",
         measured_by=NAV_BY, source_artifact="qc_evidence/nav_walk.json",
         note="STEP 1. `exact` is load-bearing: a containment match picked the "
              "OUTERMOST div [0,0,1184,755] first and clicking its centre hit "
              "dead space."),
    dict(element_key="doubao.nav.cat_product_dev", app_key="doubao", step_no=2,
         unit_key="count",  # step 2 of the nav walk: 3 clicks from the default page
         name="产品研发", tag="button", frame=1, coord_space="frame",
         x1=506, y1=76, x2=582, y2=108, alt=["產品研發"], must=["研发"],
         page_url_glob="chrome://doubao-chat/chat/skills/market/buddies*",
         measured_by=NAV_BY, source_artifact="qc_evidence/nav_walk.json",
         note="STEP 2. Lives in FRAME 1 (iframe), NOT the top frame. The tab is "
              "SIMPLIFIED (产品研发); the user wrote TRADITIONAL (產品研發) — "
              "matching only the traditional form returns 0 hits and looks like "
              "'the element is gone'."),
    # --- team/task page, frame 1 (iframe) --------------------------------
    dict(element_key="doubao.team.breadcrumb_team", app_key="doubao", step_no=3,
         unit_key="count",  # 1 click to return to the team page
         name="软件研发小组", tag="a", frame=1, coord_space="frame",
         x1=424, y1=16, x2=520, y2=40, exact=True, must=["软件研发"],
         page_url_glob="chrome://doubao-chat/chat/skills/market/buddies/tasks/*",
         measured_by=TEAM_BY,
         source_artifact="qc_evidence/diag_doubao_team.json",
         note="STEP 3. Clicking navigates to /chat/skills/new?assignTeamId=... "
              "so this breadcrumb IS the team/'switch product' entry the user "
              "described. NOTE: this is NOT the same control as the header "
              "team card on the /skills/new page."),
    dict(element_key="doubao.team.new_task_tab", app_key="doubao", step_no=None,
         unit_key="count",  # 1 click to open the new-task tab
         name="新任务 (breadcrumb current)", tag="li", frame=1,
         coord_space="frame", x1=542, y1=15, x2=616, y2=41, exact=True,
         page_url_glob="chrome://doubao-chat/chat/skills/market/buddies/tasks/*",
         measured_by=TEAM_BY,
         source_artifact="qc_evidence/diag_doubao_team.json",
         note="AMBIGUOUS LABEL — three different things are called 新任务: this "
              "breadcrumb li, a mission card `a` [294,528,1169,584], and the "
              "sidebar row '新工作任务 Ctrl N' [9,55,271,87]. Never match 新任务 "
              "without a tag AND a frame."),
    # --- sidebar task list, frame 0 --------------------------------------
    dict(element_key="doubao.sidebar.task_row_1", app_key="doubao", step_no=5,
         unit_key="count",  # 1 click to open task row 1
         name="task row 1", tag="div", frame=0, coord_space="frame",
         x1=9, y1=617, x2=271, y2=649,
         page_url_glob="chrome://doubao-chat/chat/skills*",
         measured_by=TEAM_BY,
         source_artifact="qc_evidence/diag_doubao_team.json",
         note="STEP 5. Row pitch 64px, row height 32px; row 2 is [9,681,271,713] "
              "so a row is addressed by index, not by a single fixed rect."),
    dict(element_key="doubao.sidebar.new_task", app_key="doubao", step_no=None,
         unit_key="count",  # 1 click to create a task
         name="新工作任务 (Ctrl N)", tag="div", frame=0, coord_space="frame",
         x1=9, y1=55, x2=271, y2=87,
         page_url_glob="chrome://doubao-chat/chat*",
         measured_by=TEAM_BY,
         source_artifact="qc_evidence/diag_doubao_team.json",
         note="The CREATE-new entry in the outer shell. Distinct from the "
              "breadcrumb 新任务 and from the mission card."),
    # --- team dashboard (/skills/new), frame 1 ---------------------------
    dict(element_key="doubao.team.card_software_dev", app_key="doubao",
         unit_key="count",  # 1 click to open the ?????? card
         step_no=None, name="软件研发小组 (team card)", tag="button", frame=1,
         coord_space="frame", x1=388, y1=198, x2=1049, y2=254,
         page_url_glob="chrome://doubao-chat/chat/skills/new*",
         measured_by=PAGE_BY, source_artifact="qc_evidence/diag_page.json",
         note="The TEAM PICKER on the /skills/new dashboard — clickable. This is "
              "the second half of 'can switch product'; the breadcrumb `a` above "
              "is the first."),
    dict(element_key="doubao.team.mission_card_1", app_key="doubao",
         unit_key="count",  # 1 click to open mission card 1
         step_no=5, name="mission card 1 (新任务)", tag="a", frame=1,
         coord_space="frame", x1=294, y1=528, x2=1169, y2=584,
         page_url_glob="chrome://doubao-chat/chat/skills/new*",
         measured_by=PAGE_BY, source_artifact="qc_evidence/diag_page.json",
         note="Text observed: '新任务\\n更新于 1 分钟前'. The dashboard shows a "
              "mission LIST; the chatbox is NOT on this page (sender/attach/"
              "paste/upload terms ALL missed), so a chat belongs to a mission."),
)

SEED_GOALS: tuple[dict, ...] = (
    dict(goal_key="doubao.nav_to_skills", app_key="doubao", seq=1,
         title="由預設頁行到技能市集",
         instruction="喺豆包主窗，點 `<a>`『插件 · 技能 · 伙伴』"
                     "（frame 0, rect [9,153,271,185]）。",
         done_when="page.url 包含 '/chat/skills'",
         evidence_kind="url",
         element_keys=["doubao.nav.skills"]),
    dict(goal_key="doubao.nav_to_partner", app_key="doubao", seq=2,
         title="入『工作伙伴』",
         instruction="點 `<button>`『工作伙伴』（frame 0, rect [446,8,510,48]，"
                     "必須 exact 全等，唔可以包含式匹配）。",
         done_when="page.url 包含 '/chat/skills/market/buddies'",
         evidence_kind="url",
         element_keys=["doubao.nav.partner"]),
    dict(goal_key="doubao.pick_product_dev", app_key="doubao", seq=3,
         title="揀『产品研发』分類",
         instruction="喺 FRAME 1 內點 `<button>`『产品研发』"
                     "（rect [506,76,582,108]）；同時接受繁體『產品研發』。",
         done_when="出現團隊卡，或 url 變成 /buddies 之下的團隊頁",
         evidence_kind="url+dom",
         element_keys=["doubao.nav.cat_product_dev"]),
    dict(goal_key="doubao.open_team_software_dev", app_key="doubao", seq=4,
         title="入『软件研发小组』",
         instruction="喺 FRAME 1 內點 `<a>`『软件研发小组』（rect [424,16,520,40]）。",
         done_when="page.url 包含 'assignTeamId='",
         evidence_kind="url",
         element_keys=["doubao.team.breadcrumb_team"]),
    dict(goal_key="doubao.open_mission", app_key="doubao", seq=5,
         title="開一個任務 (mission)",
         instruction="喺任務列表點一行（側欄 frame 0 由 [9,617,271,649] 起，"
                     "行距 64px），或喺 /skills/new 點任務卡 "
                     "`<a>` [294,528,1169,584]。",
         done_when="page.url 匹配 /buddies/tasks/<digits>，並取得該 id",
         evidence_kind="url-task-id",
         element_keys=["doubao.sidebar.task_row_1", "doubao.team.mission_card_1"]),
    dict(goal_key="doubao.read_reply", app_key="doubao", seq=6,
         title="讀取回覆內文（唔靠座標）",
         instruction="由 FRAME 1 讀 innerText。側欄標題唔算 —— 回覆狀態"
                     "（`正在执行 N 秒` / `正在思考` / 最終文字）全部喺 iframe 內。",
         done_when="由 frame 1 取到非空文字，且唔再包含『正在执行』",
         evidence_kind="dom-text",
         element_keys=[]),
    dict(goal_key="doubao.find_doc_card", app_key="doubao", seq=7,
         title="搵到 doc 卡（回覆完之後）",
         instruction="回覆結束後，喺 FRAME 1 搵 doc 卡。⚠️ 未量度 —— 必須先跑"
                     "一次完成嘅任務再 dump，唔准靠估。",
         done_when="frame 1 內出現 doc 卡元素，且有 rect",
         evidence_kind="dom-rect",
         element_keys=[]),
    dict(goal_key="doubao.copy_doc_url", app_key="doubao", seq=8,
         title="經『复制链接』拎 doc URL",
         instruction="點 doc 卡右側 → 開選單 → 點『复制链接』，再讀剪貼簿。"
                     "⚠️ 已量度：Ctrl+A / Ctrl+C 對 doc 卡無效，"
                     "選單係唯一路徑。⚠️ 選單 rect 未量度。",
         done_when="剪貼簿得到一個 http(s) URL",
         evidence_kind="clipboard",
         element_keys=[]),
    dict(goal_key="doubao.test_mission_x3", app_key="doubao", seq=9,
         title="測試任務：送 → 收回覆 → 再送，直到證據 x3",
         instruction="用 worker_identity 做 prompt，另加測試 prompt。"
                     "送第一次，取回覆，將回覆當第二次輸入再送；"
                     "非停止，直到有 3 份 PASS 證據。",
         done_when="3 份 status='PASS' 嘅證據，逐份可查",
         evidence_kind="db-row+artifact",
         element_keys=[],
         requires_success_count=3),
)


def seed(db_path: Path | None = None) -> dict[str, int]:
    """Write the MEASURED seed rows. Idempotent (upsert by key)."""
    init_db(db_path)
    for e in SEED_ELEMENTS:
        upsert_element(db_path=db_path, **e)
    for g in SEED_GOALS:
        upsert_goal(db_path=db_path, **g)
    return {"elements": len(SEED_ELEMENTS), "goals": len(SEED_GOALS)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--init", action="store_true", help="create the tables")
    ap.add_argument("--seed", action="store_true", help="write the measured rows")
    ap.add_argument("--list", action="store_true", help="print elements + goals")
    ap.add_argument("--json", action="store_true", help="dump everything as JSON")
    ap.add_argument("--db", default=None, help="override the db path")
    args = ap.parse_args()

    db = Path(args.db) if args.db else None
    if args.init:
        print("init -> %s" % init_db(db))
    if args.seed:
        print("seeded -> %s" % json.dumps(seed(db)))

    if args.json:
        out = {"db": str(db or DB_PATH),
               "elements": list_elements(db_path=db),
               "goals": list_goals(db_path=db)}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.list or not any((args.init, args.seed)):
        els = list_elements(db_path=db)
        gls = list_goals(db_path=db)
        print("=== ELEMENTS (%d) ===" % len(els))
        for e in els:
            print("  %-34s step=%-4s f%d %-7s %-22s %s"
                  % (e["element_key"], e["step_no"], e["frame"], e["tag"],
                     str(e["rect"]), e["name"]))
        print()
        print("=== GOALS (%d) ===" % len(gls))
        for g in gls:
            print("  %2d %-32s %s" % (g["seq"], g["goal_key"], g["title"]))
            print("       done_when: %s" % g["done_when"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
