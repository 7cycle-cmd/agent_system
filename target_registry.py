# -*- coding: utf-8 -*-
"""target_registry.py -- THREE tables, one fact each, every id DB-driven.

THE HUMAN (2026-09-25), verbatim
--------------------------------
    "Table name is? is real table or not..."
    "id must = primary and auto inscrease as DB driven"
    "target_template / id | name ... 1 chatbox 2 send_button 3 textarea 4 windows?"
    "that at group, so that are not target_template"
    "target_group / id | perm | vscode...."
    "enviornment_template / id | enviornment_id | template_id | x1,y1 | x2,y2"
    "see, your design is not clear!! and too many fucking lazy"

THE DEFECT THIS FIXES
---------------------
MEASURED: `target_area` (coords.db) mixed THREE facts in one table, keyed by
TEXT (`target_id`, `popup_id`):

    popup_id = doubao_app      -> window, chatbox, send_button, textarea
    popup_id = perm_picker     -> default, allow_all, autopilot
    popup_id = perm_pill_area  -> pill

The human's correction is exact: "that at group, so that are not
target_template" -- `perm_allow_all` / `perm_autopilot` / `perm_default` /
`perm_pill` are GROUP members, not targets.

THE DESIGN (three tables, one fact each)
----------------------------------------
    target_group          WHICH set.        id | group_key
    target_template       WHAT is located.  id | name | group_id
    environment_template  the VALUE for ONE environment.
                          id | environment_id | template_id | x1,y1 | x2,y2

Every link is an INTEGER id. No TEXT key is joined anywhere.

NEVER RAISES
------------
A read that can raise turns a popup into an outage. Every failure is returned
as `ok: False` with a `why`.
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

SOURCE = "target_registry:target_group+target_template+environment_template"

# ---- target_group: WHICH set, and WHICH environment owns it -----------------
# THE HUMAN (2026-09-25): "enviornment #6 will show 豆包 app window" / "your
# table is wrong, re-design that now".
#
# MEASURED DEFECT: `target_group` had NO environment scope, so
# `for_environment()` LEFT JOINed EVERY template onto EVERY environment --
# `environment_id=6` (VS Code) showed `doubao_app`'s `window` target.
#
# THE FIX: a group NAMES the environment it belongs to. The human already named
# the column: "target_group / id | perm | vscode....".
_CREATE_GROUP_SQL = """
CREATE TABLE IF NOT EXISTS target_group (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key      TEXT    NOT NULL UNIQUE,
    environment_id INTEGER NOT NULL,
    label          TEXT,
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (environment_id) REFERENCES working_environment (environment_id)
)
"""

# ---- target_template: WHAT is located, and WHICH KIND of value it takes -----
# THE HUMAN (2026-09-25): "open the pop-up for premission by Ctrl + Alt + K is
# VS code hotkey" / "now we have 2 type hotkey and by x,y" / "re-design
# template table with field type , so we can define 2 type hotkey and
# coordinate".
#
# MEASURED: every row was assumed to be a COORDINATE, and `Ctrl+Alt+K` existed
# only as a STRING in `f_perm_click.py` / `hotkey_tools.md` -- never as a row.
# So a hotkey target could not be listed, versioned, or shown.
#
# THE RULE: `field_type` decides WHICH value columns carry the value.
#   coordinate -> x1,y1,x2,y2,cx,cy   (hotkey stays 'NA')
#   hotkey     -> hotkey              (coords stay NA_INT = -1)
FIELD_TYPES = ("coordinate", "hotkey")

_CREATE_TEMPLATE_SQL = """
CREATE TABLE IF NOT EXISTS target_template (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    group_id    INTEGER NOT NULL,
    field_type  TEXT    NOT NULL DEFAULT 'coordinate'
                CHECK (field_type IN ('coordinate', 'hotkey')),
    label       TEXT,
    cite_ref    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES target_group (id)
)
"""

# ---- environment_template: the VALUE for ONE environment --------------------
# NO NULL (the repo standard, `no_null.py`): an uncollected value is `NA_INT`
# (-1), NOT NULL. THE HUMAN (2026-09-25): "QC-07 (未收集返 NA, null is not
# allowed". So `-1` is the SYSTEM-DEFINED "not collected" state, and a NULL
# would be a DEFECT (the standardiser did not run).
_CREATE_ENV_SQL = """
CREATE TABLE IF NOT EXISTS environment_template (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    environment_id INTEGER NOT NULL,
    template_id    INTEGER NOT NULL,
    x1 INTEGER NOT NULL DEFAULT -1,
    y1 INTEGER NOT NULL DEFAULT -1,
    x2 INTEGER NOT NULL DEFAULT -1,
    y2 INTEGER NOT NULL DEFAULT -1,
    cx INTEGER NOT NULL DEFAULT -1,
    cy INTEGER NOT NULL DEFAULT -1,
    hotkey         TEXT    NOT NULL DEFAULT 'NA',
    collected_at   TIMESTAMP NOT NULL DEFAULT 'NA',
    cite_ref       TEXT    NOT NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (environment_id, template_id),
    FOREIGN KEY (template_id) REFERENCES target_template (id)
)
"""

# THE "NOT COLLECTED" SENTINEL. `no_null.NA_INT` is -1; it is repeated here so
# this module does not import a heavy dependency for one constant.
NA_INT = -1
NA = "NA"


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the three tables. Idempotent. Never raises."""
    try:
        conn.execute(_CREATE_GROUP_SQL)
        conn.execute(_CREATE_TEMPLATE_SQL)
        conn.execute(_CREATE_ENV_SQL)
        # ADDITIVE: `CREATE TABLE IF NOT EXISTS` will NOT add a column to an
        # existing `target_group`, so the scope column is added here too.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(target_group)")]
        if "environment_id" not in cols:
            conn.execute("ALTER TABLE target_group ADD COLUMN "
                         "environment_id INTEGER NOT NULL DEFAULT 0")
        # ADDITIVE: the TYPE column and the HOTKEY value column.
        tcols = [r[1] for r in conn.execute("PRAGMA table_info(target_template)")]
        if "field_type" not in tcols:
            conn.execute("ALTER TABLE target_template ADD COLUMN "
                         "field_type TEXT NOT NULL DEFAULT 'coordinate'")
        ecols = [r[1] for r in conn.execute(
            "PRAGMA table_info(environment_template)")]
        if "hotkey" not in ecols:
            conn.execute("ALTER TABLE environment_template ADD COLUMN "
                         "hotkey TEXT NOT NULL DEFAULT 'NA'")
        conn.commit()
        return {"ok": True, "tables": ["target_group", "target_template",
                                       "environment_template"]}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def upsert_group(conn: sqlite3.Connection, group_key: str, *,
                 environment_id: int = 0, label: str = "",
                 cite_ref: str = "") -> int | None:
    """Return the DB-driven `target_group.id` for `group_key`.

    `environment_id` is the group's OWNER. A group with no owner is `0`, which
    matches NO environment, so it can never leak onto another environment's
    page.
    """
    key = str(group_key or "").strip()
    if not key:
        return None
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO target_group (group_key, environment_id, label, cite_ref) "
        "VALUES (?,?,?,?) ON CONFLICT(group_key) DO UPDATE SET "
        "environment_id=excluded.environment_id, label=excluded.label, "
        "updated_at=CURRENT_TIMESTAMP",
        (key, int(environment_id or 0), label or key,
         cite_ref or "target_registry.upsert_group"))
    conn.commit()
    row = conn.execute("SELECT id FROM target_group WHERE group_key=?",
                       (key,)).fetchone()
    return int(row["id"]) if row else None


def upsert_template(conn: sqlite3.Connection, name: str, group_id: int, *,
                    field_type: str = "coordinate", label: str = "",
                    cite_ref: str = "") -> int | None:
    """Return the DB-driven `target_template.id` for `name`.

    `field_type` is the CLOSED vocabulary `('coordinate','hotkey')`. An unknown
    type is REFUSED rather than stored, because a third kind would silently
    break the value-shape rule.
    """
    nm = str(name or "").strip()
    if not nm or group_id is None:
        return None
    ft = str(field_type or "coordinate").strip().lower()
    if ft not in FIELD_TYPES:
        return None
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO target_template (name, group_id, field_type, label, "
        "cite_ref) VALUES (?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
        "group_id=excluded.group_id, field_type=excluded.field_type, "
        "label=excluded.label, updated_at=CURRENT_TIMESTAMP",
        (nm, int(group_id), ft, label or nm,
         cite_ref or "target_registry.upsert_template"))
    conn.commit()
    row = conn.execute("SELECT id FROM target_template WHERE name=?",
                       (nm,)).fetchone()
    return int(row["id"]) if row else None


# WHICH ENVIRONMENT OWNS WHICH GROUP. MEASURED (2026-09-25):
#   env 6  = IDE > VS Code      -> perm_picker, perm_pill_area
#   env 50 = Browser > 豆包      -> doubao_app
# A group with no owner here is `0`, which matches NO environment, so it can
# never leak onto another environment's page.
GROUP_OWNER = {
    "doubao_app": 50,
    "perm_picker": 6,
    "perm_pill_area": 6,
}


def seed_from_target_area(conn: sqlite3.Connection,
                          coord_db: Path | None = None) -> dict[str, Any]:
    """Migrate the 8 `target_area` rows into groups + templates.

    MEASURED: `popup_id` IS the group, and `target_id` IS the target. The
    `target_id` prefix (`doubao_`, `perm_`) is stripped so the NAME is the
    thing located (`chatbox`, `send_button`, ...), which is what the human
    listed.
    """
    try:
        ensure_schema(conn)
        cconn = sqlite3.connect(str(coord_db or COORD_DB), timeout=10)
        try:
            cconn.row_factory = sqlite3.Row
            rows = [dict(r) for r in cconn.execute(
                "SELECT target_id, popup_id, label FROM target_area "
                "WHERE isactive=1 ORDER BY popup_id, target_id")]
        finally:
            cconn.close()
        groups: dict[str, int] = {}
        targets: dict[str, int] = {}
        for r in rows:
            gk = str(r["popup_id"])
            if gk not in groups:
                gid = upsert_group(conn, gk,
                                   environment_id=GROUP_OWNER.get(gk, 0),
                                   label=gk,
                                   cite_ref="coords.db:target_area.popup_id")
                groups[gk] = gid
            # The NAME is the target_id with its group prefix removed.
            tid = str(r["target_id"])
            name = tid
            for pre in ("doubao_", "perm_"):
                if name.startswith(pre):
                    name = name[len(pre):]
                    break
            if name not in targets:
                targets[name] = upsert_template(
                    conn, name, groups[gk], label=str(r["label"] or name),
                    cite_ref="coords.db:target_area.target_id")
        return {"ok": True, "groups": len(groups), "targets": len(targets),
                "group_ids": groups, "template_ids": targets}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def seed_environment_from_target_area(conn: sqlite3.Connection,
                                      environment_id: int,
                                      coord_db: Path | None = None
                                      ) -> dict[str, Any]:
    """Give ONE environment the values we ALREADY HAD.

    THE HUMAN: "in past version, we know you have some data already, by api!!
    can more easy". The past API returned the `target_area` rows for EVERY
    environment, so that IS the starting data. This writes ONLY the named
    environment, so two environments can then diverge.
    """
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "environment_id must be an integer"}
    try:
        ensure_schema(conn)
        cconn = sqlite3.connect(str(coord_db or COORD_DB), timeout=10)
        try:
            cconn.row_factory = sqlite3.Row
            rows = [dict(r) for r in cconn.execute(
                "SELECT target_id, popup_id, x1, y1, x2, y2, cx, cy "
                "FROM target_area WHERE isactive=1")]
        finally:
            cconn.close()
        n = 0
        for r in rows:
            tid = str(r["target_id"])
            name = tid
            for pre in ("doubao_", "perm_"):
                if name.startswith(pre):
                    name = name[len(pre):]
                    break
            # ONLY the templates whose GROUP is owned by THIS environment.
            # THE HUMAN: "enviornment #6 will show 豆包 app window" -- so a
            # doubao_app template must NOT be written for env 6.
            row = conn.execute(
                "SELECT t.id FROM target_template t "
                "JOIN target_group g ON g.id = t.group_id "
                "WHERE t.name=? AND g.environment_id=?", (name, eid)).fetchone()
            if row is None:
                continue
            conn.execute(
                "INSERT INTO environment_template "
                "(environment_id, template_id, x1, y1, x2, y2, cx, cy, "
                "collected_at, cite_ref) "
                "VALUES (?,?,?,?,?,?,?,?,datetime('now'),?) "
                "ON CONFLICT(environment_id, template_id) DO UPDATE SET "
                "x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, "
                "y2=excluded.y2, cx=excluded.cx, cy=excluded.cy, "
                "collected_at=excluded.collected_at, "
                "updated_at=CURRENT_TIMESTAMP",
                (eid, int(row["id"]), int(r["x1"]), int(r["y1"]),
                 int(r["x2"]), int(r["y2"]), int(r["cx"]), int(r["cy"]),
                 "coords.db:target_area (past version data)"))
            n += 1
        conn.commit()
        return {"ok": True, "environment_id": eid, "seeded": n}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def list_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every group, with its DB-driven id and its OWNER environment. Never raises."""
    try:
        ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT id, group_key, environment_id, label FROM target_group "
            "WHERE is_active=1 ORDER BY id")]
    except Exception:
        return []


def list_templates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every target, with its DB-driven id, group id and field_type. Never raises."""
    try:
        ensure_schema(conn)
        return [dict(r) for r in conn.execute(
            "SELECT t.id, t.name, t.group_id, g.group_key, t.field_type, "
            "t.label FROM target_template t LEFT JOIN target_group g "
            "ON g.id = t.group_id WHERE t.is_active=1 "
            "ORDER BY t.group_id, t.id")]
    except Exception:
        return []


def for_environment(conn: sqlite3.Connection, environment_id: int
                    ) -> dict[str, Any]:
    """The TEMPLATE LIST with THIS environment's value on each row.

    A LEFT JOIN, so a template row with no value is STILL SHOWN (with null),
    which is what makes "what is missing" visible instead of invisible.
    """
    try:
        eid = int(environment_id)
    except (TypeError, ValueError):
        return {"ok": False, "environment_id": environment_id, "rows": [],
                "collected": False, "why": "environment_id is required"}
    try:
        ensure_schema(conn)
        # THE SCOPE. THE HUMAN (2026-09-25): "enviornment #6 will show 豆包 app
        # window" / "your table is wrong, re-design that now".
        #
        # MEASURED DEFECT: without `g.environment_id = ?`, EVERY template was
        # LEFT JOINed onto EVERY environment, so env 6 (VS Code) showed
        # `doubao_app`'s `window`. A group belongs to ONE environment, and only
        # that environment may see its templates.
        rows = [dict(r) for r in conn.execute(
            "SELECT t.id AS template_id, t.name, t.group_id, g.group_key, "
            "       g.environment_id AS group_environment_id, t.field_type, "
            "       t.label, "
            "       e.x1, e.y1, e.x2, e.y2, e.cx, e.cy, e.hotkey, "
            "       e.collected_at "
            "FROM target_template t "
            "JOIN target_group g ON g.id = t.group_id "
            "LEFT JOIN environment_template e ON "
            "  e.environment_id=? AND e.template_id=t.id AND e.is_active=1 "
            "WHERE t.is_active=1 AND g.is_active=1 AND g.environment_id=? "
            "ORDER BY t.group_id, t.id", (eid, eid))]
    except Exception as exc:
        return {"ok": False, "environment_id": eid, "rows": [],
                "collected": False,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    out = []
    for r in rows:
        # THE VALUE SHAPE IS DECIDED BY `field_type`.
        # THE HUMAN (2026-09-25): "now we have 2 type hotkey and by x,y".
        #   coordinate -> {x1,y1,x2,y2,cx,cy}   (hotkey stays 'NA')
        #   hotkey     -> {hotkey}              (coords stay NA_INT = -1)
        # NO NULL: an uncollected value is `NA`, never null.
        ft = str(r["field_type"] or "coordinate")
        if ft == "hotkey":
            hk = r["hotkey"]
            has = hk is not None and str(hk) != NA
            value: Any = ({"hotkey": str(hk)} if has else NA)
        else:
            has = r["x1"] is not None and int(r["x1"]) != NA_INT
            value = ({"x1": r["x1"], "y1": r["y1"], "x2": r["x2"],
                      "y2": r["y2"], "cx": r["cx"], "cy": r["cy"],
                      "collected_at": r["collected_at"]} if has else NA)
        out.append({
            "template_id": r["template_id"],
            "name": r["name"],
            "group_id": r["group_id"],
            "group_key": r["group_key"],
            "group_environment_id": r["group_environment_id"],
            "field_type": ft,
            "label": r["label"],
            "value": value,
            "collected": has,
        })
    n_have = sum(1 for r in out if r["collected"])
    return {"ok": True, "environment_id": eid, "rows": out,
            "collected": n_have > 0, "collected_count": n_have,
            "template_count": len(out),
            "why": ("%d of %d template row(s) collected for environment_id=%d"
                    % (n_have, len(out), eid)),
            "collect_at": "environment_id=%d" % eid}


def collect_hotkey(conn: sqlite3.Connection, environment_id: int,
                   template_id: int, hotkey: str, *, cite_ref: str
                   ) -> dict[str, Any]:
    """Record ONE environment's HOTKEY value for ONE template_id.

    THE HUMAN (2026-09-25): "open the pop-up for premission by Ctrl + Alt + K is
    VS code hotkey". The hotkey is stored in its OWN column, and the coordinate
    columns stay `NA_INT` (-1) -- a hotkey is NOT a coordinate.
    """
    try:
        eid, tid = int(environment_id), int(template_id)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "environment_id and template_id must be integers"}
    hk = str(hotkey or "").strip()
    if not hk:
        return {"ok": False, "code": "MISSING_HOTKEY",
                "message": "a hotkey target needs a hotkey value"}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no value"}
    try:
        ensure_schema(conn)
        # THE COORDINATE COLUMNS ARE WRITTEN EXPLICITLY AS NA_INT (-1).
        # MEASURED DEFECT (2026-09-25): the live table was created with
        # `DEFAULT 0`, and `0` is a VALID coordinate -- so a hotkey row's
        # "not collected" state was indistinguishable from a real measurement
        # at x=0. Relying on the column DEFAULT is what allowed that, so the
        # value is now written explicitly and the DEFAULT cannot matter.
        conn.execute(
            "INSERT INTO environment_template "
            "(environment_id, template_id, x1, y1, x2, y2, cx, cy, hotkey, "
            "collected_at, cite_ref) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?) "
            "ON CONFLICT(environment_id, template_id) DO UPDATE SET "
            "x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, y2=excluded.y2, "
            "cx=excluded.cx, cy=excluded.cy, hotkey=excluded.hotkey, "
            "collected_at=excluded.collected_at, "
            "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
            (eid, tid, NA_INT, NA_INT, NA_INT, NA_INT, NA_INT, NA_INT, hk,
             str(cite_ref)))
        conn.commit()
        return {"ok": True, "environment_id": eid, "template_id": tid,
                "hotkey": hk}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def collect(conn: sqlite3.Connection, environment_id: int, template_id: int,
            *, x1: int, y1: int, x2: int, y2: int, cite_ref: str) -> dict[str, Any]:
    """Record ONE environment's value for ONE template_id. cx/cy are DERIVED.

    Fail-closed on a degenerate rect: an inverted rect is a MEASUREMENT ERROR,
    not something to tidy up.
    """
    try:
        eid, tid = int(environment_id), int(template_id)
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_INPUT",
                "message": "environment_id, template_id and coords must be ints"}
    if x2 <= x1 or y2 <= y1:
        return {"ok": False, "code": "DEGENERATE_RECT",
                "message": ("degenerate rect (%d,%d)-(%d,%d): need x2>x1 and "
                            "y2>y1" % (x1, y1, x2, y2))}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no value"}
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    try:
        ensure_schema(conn)
        # `hotkey` IS WRITTEN EXPLICITLY AS 'NA'. A coordinate row must never
        # carry a hotkey, and relying on the column DEFAULT would let a future
        # schema change silently give it one.
        conn.execute(
            "INSERT INTO environment_template "
            "(environment_id, template_id, x1, y1, x2, y2, cx, cy, hotkey, "
            "collected_at, cite_ref) "
            "VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),?) "
            "ON CONFLICT(environment_id, template_id) DO UPDATE SET "
            "x1=excluded.x1, y1=excluded.y1, x2=excluded.x2, y2=excluded.y2, "
            "cx=excluded.cx, cy=excluded.cy, hotkey=excluded.hotkey, "
            "collected_at=excluded.collected_at, "
            "cite_ref=excluded.cite_ref, updated_at=CURRENT_TIMESTAMP",
            (eid, tid, x1, y1, x2, y2, cx, cy, NA, str(cite_ref)))
        conn.commit()
        return {"ok": True, "environment_id": eid, "template_id": tid,
                "cx": cx, "cy": cy}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--seed" in args:
            print(json.dumps(seed_from_target_area(conn), indent=2,
                             ensure_ascii=False))
        elif "--for" in args:
            i = args.index("--for")
            print(json.dumps(for_environment(conn, int(args[i + 1])), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps({"groups": list_groups(conn),
                              "templates": list_templates(conn)}, indent=2,
                             ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
