# -*- coding: utf-8 -*-
"""environment_configure.py -- configure is PER environment_id; the CONTENT is a
shared TEMPLATE.

THE HUMAN (2026-09-25), verbatim
--------------------------------
    "table design problem + table for enviornment_configure be the template to
     have unified"
    "no matter enviornment_id is 6,50,51,52 the result is same, it should for
     enviornment_id * configure only, not have all"
    "and you will found that we can have template as no matter enviornment is ,
     content is same and can share"
    "configure = null -> need to collect data at enviornment_id"

THE DEFECT THIS FIXES
---------------------
MEASURED: `target_area` (coords.db) has NO `environment_id`. So the popup for
`environment_id=6` and the popup for `environment_id=52` read the SAME 8 rows.
That is the human's complaint: "no matter enviornment_id is 6,50,51,52 the
result is same".

THE DESIGN (two registers, one fact each)
-----------------------------------------
    environment_configure_template   the SHARED content, keyed by template_key.
                                     Same for every environment.
    environment_configure            ONE environment's configure, keyed by
                                     (environment_id, template_key, target_id).
                                     A MISSING row = NOT COLLECTED (null).

`configure = null` is a REAL outcome: the environment's configure was never
collected. It is NOT "the template applies" and NOT an error.

NEVER RAISES
------------
A configure read that can raise turns a popup into an outage. Every failure is
returned as `ok: False` with a `why`.
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
# The rect content lives in coords.db (coord_store.DB_PATH), NOT agent.db.
COORD_DB = BASE / "coords.db"

SOURCE = "environment_configure:template+per_environment"

# THE TEMPLATE. The shared content, keyed by `template_key`. Same for every
# environment -- that is the human's insight: "content is same and can share".
_CREATE_TEMPLATE_SQL = """
CREATE TABLE IF NOT EXISTS environment_configure_template (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    template_key      TEXT    NOT NULL,
    target_id         TEXT    NOT NULL,
    label             TEXT,
    x1 INTEGER NOT NULL DEFAULT 0,
    y1 INTEGER NOT NULL DEFAULT 0,
    x2 INTEGER NOT NULL DEFAULT 0,
    y2 INTEGER NOT NULL DEFAULT 0,
    cx INTEGER NOT NULL DEFAULT 0,
    cy INTEGER NOT NULL DEFAULT 0,
    checklist_confirm TEXT    NOT NULL DEFAULT 'no',
    cite_ref          TEXT    NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (template_key, target_id)
)
"""

# THE PER-ENVIRONMENT CONFIGURE. ONE environment's OWN values. A MISSING row is
# NOT COLLECTED (null), never a copy of the template.
_CREATE_ENV_SQL = """
CREATE TABLE IF NOT EXISTS environment_configure (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    environment_id INTEGER NOT NULL,
    template_key  TEXT    NOT NULL,
    target_id     TEXT    NOT NULL,
    x1 INTEGER NOT NULL DEFAULT 0,
    y1 INTEGER NOT NULL DEFAULT 0,
    x2 INTEGER NOT NULL DEFAULT 0,
    y2 INTEGER NOT NULL DEFAULT 0,
    cx INTEGER NOT NULL DEFAULT 0,
    cy INTEGER NOT NULL DEFAULT 0,
    collected_at  TIMESTAMP,
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (environment_id, template_key, target_id)
)
"""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create both tables. Idempotent. Never raises."""
    try:
        conn.execute(_CREATE_TEMPLATE_SQL)
        conn.execute(_CREATE_ENV_SQL)
        conn.commit()
        return {"ok": True, "tables": ["environment_configure_template",
                                       "environment_configure"]}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def seed_template_from_target_area(conn: sqlite3.Connection,
                                   coord_db: Path | None = None) -> dict[str, Any]:
    """Seed the TEMPLATE from the current `target_area` rows.

    MEASURED: `target_area` already holds the shared content (8 rects across
    `perm_picker` / `perm_pill_area` / `doubao_app`). Those rows ARE the
    template, so they are COPIED here under `template_key = popup_id`, and
    `target_area` is left untouched (it stays the rect table).
    """
    try:
        ensure_schema(conn)
        cconn = sqlite3.connect(str(coord_db or COORD_DB), timeout=10)
        try:
            cconn.row_factory = sqlite3.Row
            rows = [dict(r) for r in cconn.execute(
                "SELECT target_id, popup_id, label, x1, y1, x2, y2, cx, cy, "
                "checklist_confirm FROM target_area WHERE isactive=1")]
        finally:
            cconn.close()
        n = 0
        for r in rows:
            conn.execute(
                "INSERT INTO environment_configure_template "
                "(template_key, target_id, label, x1, y1, x2, y2, cx, cy, "
                "checklist_confirm, cite_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(template_key, target_id) DO UPDATE SET "
                "label=excluded.label, x1=excluded.x1, y1=excluded.y1, "
                "x2=excluded.x2, y2=excluded.y2, cx=excluded.cx, "
                "cy=excluded.cy, checklist_confirm=excluded.checklist_confirm, "
                "updated_at=CURRENT_TIMESTAMP",
                (str(r["popup_id"]), str(r["target_id"]), r["label"],
                 int(r["x1"]), int(r["y1"]), int(r["x2"]), int(r["y2"]),
                 int(r["cx"]), int(r["cy"]), str(r["checklist_confirm"]),
                 "coords.db:target_area"))
            n += 1
        conn.commit()
        return {"ok": True, "seeded": n}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def template(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The SHARED template content. Never raises."""
    try:
        ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT template_key, target_id, label, x1, y1, x2, y2, cx, cy, "
            "checklist_confirm FROM environment_configure_template "
            "WHERE is_active=1 ORDER BY template_key, target_id")]
    except Exception:
        return []


def merged(conn: sqlite3.Connection, environment_id: int) -> dict[str, Any]:
    """The TEMPLATE LIST with THIS environment's value on each row.

    THE HUMAN (2026-09-25): "as you have template, pop-up will have template
    list and value, null or data".

    So the popup is ONE list: every template row, and beside it the
    environment's OWN value -- `null` when that row was not collected. This is
    a LEFT JOIN, so a template row with no value is STILL SHOWN (with null),
    which is what makes "what is missing" visible instead of invisible.
    """
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "environment_id": environment_id, "rows": [],
                "collected": False, "why": "environment_id is required"}
    try:
        ensure_schema(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT t.template_key, t.target_id, t.label, "
            "       t.x1 AS t_x1, t.y1 AS t_y1, t.x2 AS t_x2, t.y2 AS t_y2, "
            "       e.x1 AS e_x1, e.y1 AS e_y1, e.x2 AS e_x2, e.y2 AS e_y2, "
            "       e.cx AS e_cx, e.cy AS e_cy, e.collected_at "
            "FROM environment_configure_template t "
            "LEFT JOIN environment_configure e ON "
            "  e.environment_id=? AND e.template_key=t.template_key "
            "  AND e.target_id=t.target_id AND e.is_active=1 "
            "WHERE t.is_active=1 ORDER BY t.template_key, t.target_id", (eid,))]
    except Exception as exc:
        return {"ok": False, "environment_id": eid, "rows": [],
                "collected": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    out = []
    for r in rows:
        has = r["e_x1"] is not None
        out.append({
            "template_key": r["template_key"],
            "target_id": r["target_id"],
            "label": r["label"],
            "template": {"x1": r["t_x1"], "y1": r["t_y1"],
                         "x2": r["t_x2"], "y2": r["t_y2"]},
            "value": ({"x1": r["e_x1"], "y1": r["e_y1"], "x2": r["e_x2"],
                       "y2": r["e_y2"], "cx": r["e_cx"], "cy": r["e_cy"],
                       "collected_at": r["collected_at"]} if has else None),
            "collected": has,
        })
    n_have = sum(1 for r in out if r["collected"])
    return {"ok": True, "environment_id": eid, "rows": out,
            "collected": n_have > 0,
            "collected_count": n_have, "template_count": len(out),
            "why": ("%d of %d template row(s) collected for environment_id=%d"
                    % (n_have, len(out), eid)),
            "collect_at": "environment_id=%d" % eid}


def seed_environment_from_template(conn: sqlite3.Connection, environment_id: int,
                                   *, cite_ref: str = "") -> dict[str, Any]:
    """Copy the TEMPLATE into ONE environment's configure.

    THE HUMAN (2026-09-25): "in past version, we know you have some data
    already, by api!! can more easy".

    MEASURED: the past version's API returned `target_area` rows for EVERY
    environment, so that data IS this environment's starting point. This copies
    the template into `environment_configure` for ONE environment_id, so the
    value stops being null and becomes the data we already had.

    It writes ONLY the named environment. It never touches another one, so two
    environments can then diverge.
    """
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "environment_id must be an integer"}
    cite = str(cite_ref or "").strip() or (
        "environment_configure.seed_environment_from_template:"
        "environment_id=%d" % eid)
    try:
        ensure_schema(conn)
        tpl = template(conn)
        if not tpl:
            return {"ok": False, "code": "NO_TEMPLATE",
                    "message": "the template is empty; seed it first"}
        n = 0
        for t in tpl:
            conn.execute(
                "INSERT INTO environment_configure "
                "(environment_id, template_key, target_id, x1, y1, x2, y2, "
                "cx, cy, collected_at, cite_ref) "
                "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?) "
                "ON CONFLICT(environment_id, template_key, target_id) DO "
                "UPDATE SET x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, "
                "y2=excluded.y2, cx=excluded.cx, cy=excluded.cy, "
                "collected_at=excluded.collected_at, "
                "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
                (eid, t["template_key"], t["target_id"], int(t["x1"]),
                 int(t["y1"]), int(t["x2"]), int(t["y2"]), int(t["cx"]),
                 int(t["cy"]), cite))
            n += 1
        conn.commit()
        return {"ok": True, "environment_id": eid, "seeded": n,
                "cite_ref": cite}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def for_environment(conn: sqlite3.Connection, environment_id: int
                    ) -> dict[str, Any]:
    """ONE environment's configure. `values` is `null` when NOT COLLECTED.

    THE HUMAN: "configure = null -> need to collect data at enviornment_id".
    So an environment with no rows returns `values: None` and
    `collected: False` -- NEVER the template's rows, which would claim the
    environment's configure was measured when it was not.
    """
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "environment_id": environment_id,
                "values": None, "collected": False,
                "why": "environment_id is required"}
    try:
        ensure_schema(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT template_key, target_id, x1, y1, x2, y2, cx, cy, "
            "collected_at FROM environment_configure "
            "WHERE environment_id=? AND is_active=1 "
            "ORDER BY template_key, target_id", (eid,))]
    except Exception as exc:
        return {"ok": False, "environment_id": eid, "values": None,
                "collected": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    if not rows:
        return {"ok": True, "environment_id": eid, "values": None,
                "collected": False,
                "why": ("configure is NULL for environment_id=%d: it has not "
                        "been collected yet" % eid),
                "collect_at": "environment_id=%d" % eid}
    return {"ok": True, "environment_id": eid, "values": rows,
            "collected": True,
            "why": ("%d configure row(s) collected for environment_id=%d"
                    % (len(rows), eid))}


def collect(conn: sqlite3.Connection, environment_id: int, template_key: str,
            target_id: str, *, x1: int, y1: int, x2: int, y2: int,
            cite_ref: str) -> dict[str, Any]:
    """Record ONE environment's configure value. cx/cy are DERIVED.

    Fail-closed on a degenerate rect: an inverted rect is a MEASUREMENT ERROR,
    not something to tidy up (the same rule `coord_store.compute_rect_metrics`
    applies).
    """
    try:
        eid = int(environment_id)
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "environment_id and x1,y1,x2,y2 must be integers"}
    if x2 <= x1 or y2 <= y1:
        return {"ok": False, "code": "DEGENERATE_RECT",
                "message": ("degenerate rect (%d,%d)-(%d,%d): need x2>x1 and "
                            "y2>y1" % (x1, y1, x2, y2))}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no configure row"}
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO environment_configure "
            "(environment_id, template_key, target_id, x1, y1, x2, y2, cx, cy, "
            "collected_at, cite_ref) VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?) "
            "ON CONFLICT(environment_id, template_key, target_id) DO UPDATE SET "
            "x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, y2=excluded.y2, "
            "cx=excluded.cx, cy=excluded.cy, collected_at=excluded.collected_at, "
            "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
            (eid, str(template_key), str(target_id), x1, y1, x2, y2, cx, cy,
             str(cite_ref)))
        conn.commit()
        return {"ok": True, "environment_id": eid, "template_key": template_key,
                "target_id": target_id, "cx": cx, "cy": cy}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--seed" in args:
            print(json.dumps(seed_template_from_target_area(conn), indent=2,
                             ensure_ascii=False))
        elif "--for" in args:
            i = args.index("--for")
            print(json.dumps(for_environment(conn, int(args[i + 1])), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps({"template": template(conn)}, indent=2,
                             ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
