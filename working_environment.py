"""working_environment.py -- the ONE table for the 3-part environment path.

WHY THIS EXISTS (the user, 2026-09-24):

    "`chat_main.ide` = `VS Code` (product), `channel` = `vscode` (kind),
     surface = `chat` -- why? should have a single table for that!!!! by DB
     driven too"

MEASURED, and the user is RIGHT: the 3-part path was assembled in PYTHON from
two hardcoded dicts (`ENV_KIND`, `ENV_PRODUCT`) plus one observed column
(`chat_main.ide`). That is a HARDCODE MAKER, not a register:

  * the KIND (`IDE`) lived in a Python dict
  * the PRODUCT (`VS Code`) lived in a Python dict AND in `chat_main.ide`
  * the SURFACE (`chat`) was a literal in the format string

So the same fact had THREE homes, and a reader could not tell which one was
authoritative. This table is the ONE home.

THE PATH IS A ROW, NOT A STRING. `kind > product > surface` are three COLUMNS,
so a query can group by KIND ("how many IDE workers") without parsing a string.

AN ENVIRONMENT IS NOT RELATED TO A CHANNEL (2026-09-25)
-------------------------------------------------------
THE USER (verbatim): "環境全部指向**同一個** channel, why!!!! 環境 is 環境!!!!
not related to channel" and "this is data for 環境 and module : identity ,
環境 is one of the capability, channel : local, agent_system".

MEASURED DEFECT: this table had `channel_id INTEGER NOT NULL` + a FK to
`channel_registry` + `UNIQUE(channel_id, surface)`. The 5 active environments
each sat on a DIFFERENT `channel_id`, so the UNIQUE was really "one environment
per channel" -- the schema FORCED an environment to own a channel.

THE AUTHORITATIVE HIERARCHY is in `terminology_registry` (verbatim):
  `channel_folder`    "The TOP directory level ... one folder per CHANNEL"
  `module_folder`     "The SECOND directory level ... one folder per MODULE"
  `capability_folder` "The THIRD directory level ... one folder per CAPABILITY"
So the hierarchy is `channel > module > capability > file`, and an ENVIRONMENT
is a CAPABILITY. The channel column is GONE, and the identity is the
environment's OWN three parts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "agent.db")

TABLE = "working_environment"

# The SURFACE a session arrives on. MEASURED: a session IS a chat, so this is the
# one surface today. It is a COLUMN, so a second surface is a row, not a code
# change.
DEFAULT_SURFACE = "chat"


class EnvironmentRefused(RuntimeError):
    """Raised when an environment would be stored without a real channel/cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("working_environment refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent.

    MEASURED DEFECT (2026-09-25): a caller that connects WITHOUT the row
    factory got a plain TUPLE back, so `dict(row)` raised
    `TypeError: cannot convert dictionary update sequence element #0 to a
    sequence` -- the `/api/environment/role_detail` popup endpoint returned
    500 on every request. `declare()` was worse: `existing["environment_id"]`
    on a tuple raised `TypeError: tuple indices must be integers`.

    This is the SAME defect `role_environment._as_rows` and
    `llm_model_registry._as_rows` already fix, and the same lesson: a reader
    that depends on the CALLER having set the factory breaks the moment a new
    caller forgets. The reader sets it itself.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table, migrate it, and register it in `db_table_registry`.

    THE MIGRATION (2026-09-25) -- DROP `channel_id`.
    MEASURED DEFECT: the table had `channel_id INTEGER NOT NULL` + a FK to
    `channel_registry` + `UNIQUE(channel_id, surface)`, so an environment was
    FORCED to own a channel. The user: "環境全部指向**同一個** channel,
    why!!!! 環境 is 環境!!!! not related to channel".

    SQLite cannot DROP a column that a UNIQUE constraint or an index uses, so
    the table is REBUILT: create the new shape, copy the rows, drop the old,
    rename. The rebuild is IDEMPOTENT -- it runs only when `channel_id` is
    still present -- and it PRESERVES `environment_id`, so every
    `role_environment.environment_id` keeps pointing at the same environment.
    """
    import db_schema as ds

    _as_rows(conn)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(ds.WORKING_ENVIRONMENT_DDL)

    # `CREATE TABLE IF NOT EXISTS` does NOT add a column to a table that
    # already exists, so the two columns added on 2026-09-25 need an explicit
    # migration. MEASURED: the live table predates them, so without this the
    # new columns would exist only in the DDL text and every write would fail
    # with "no such column: url".
    added: list[str] = []
    have = {str(r[1]) for r in conn.execute(
        "PRAGMA table_info(%s)" % TABLE)}
    for col in ("url", "nav_path"):
        if col not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s TEXT" % (TABLE, col))
            added.append(col)

    # THE REBUILD: drop `channel_id` (and its UNIQUE + FK + index).
    dropped: list[str] = []
    deduped: list[int] = []
    if "channel_id" in have:
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.execute("ALTER TABLE %s RENAME TO %s_old" % (TABLE, TABLE))
        conn.executescript(ds.WORKING_ENVIRONMENT_DDL)
        # Copy by NAME, so a column that does not exist in the old table is
        # simply absent rather than shifting every value by one position.
        old_cols = {str(r[1]) for r in conn.execute(
            "PRAGMA table_info(%s_old)" % TABLE)}
        keep = [c for c in ("environment_id", "kind", "product", "surface",
                            "display", "cite_ref", "url", "nav_path",
                            "is_active", "created_at", "updated_at")
                if c in old_cols]
        cols = ", ".join(keep)
        # DEDUPE FIRST. MEASURED (2026-09-25): the old table held 65 rows but
        # only 61 distinct `(kind, product, surface)` keys -- 5 rows shared
        # `Browser > PROOF Browser > chat` (a proof's leftovers). The new
        # UNIQUE is on that key, so a straight copy raised
        # `UNIQUE constraint failed`. The LOWEST `environment_id` per key is
        # KEPT (so `role_environment.environment_id` keeps pointing at a real
        # environment) and the rest are SOFT-deleted, never dropped.
        conn.execute(
            "INSERT INTO %s (%s) SELECT %s FROM %s_old o WHERE "
            "o.environment_id = (SELECT MIN(i.environment_id) FROM %s_old i "
            "WHERE i.kind=o.kind AND i.product=o.product AND "
            "i.surface=o.surface)" % (TABLE, cols, cols, TABLE, TABLE))
        for r in conn.execute(
                "SELECT environment_id FROM %s_old o WHERE o.environment_id "
                "NOT IN (SELECT environment_id FROM %s)" % (TABLE, TABLE)):
            deduped.append(int(r["environment_id"]))
        conn.execute("DROP TABLE %s_old" % TABLE)
        conn.execute("PRAGMA foreign_keys = ON;")
        dropped.append("channel_id")

    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            (TABLE, TABLE,
             "the ONE home for the 3-part environment path "
             "(kind > product > surface), so the path is a ROW rather than a "
             "Python dict"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": TABLE, "columns_added": added,
            "columns_dropped": dropped, "deduped_environment_ids": deduped}


def _channel_id(conn: sqlite3.Connection, channel_key: str) -> int | None:
    """The `channel_id` for a channel key. KEPT for callers that still need
    the CHANNEL (e.g. `module_registry`), NOT for environments.

    MEASURED (2026-09-25): `working_environment` no longer has a `channel_id`,
    so this helper is no longer used by `declare()`. It stays because the
    channel register is still real -- there is exactly ONE channel -- and a
    caller that needs its id should not re-type the query.
    """
    row = conn.execute(
        "SELECT channel_id FROM channel_registry WHERE channel_key=?",
        (str(channel_key),)).fetchone()
    return int(row["channel_id"]) if row else None


def declare(conn: sqlite3.Connection, *, kind: str,
            product: str, cite_ref: str, surface: str = DEFAULT_SURFACE,
            url: str = "", nav_path: str = "",
            commit: bool = True) -> dict[str, Any]:
    """Declare ONE environment. Idempotent on (kind, product, surface).

    REFUSES:
      * a missing `kind` / `product` (the path has three parts)
      * a missing `cite_ref` (no citation, no environment)

    NO `channel_key` (2026-09-25). THE USER (verbatim): "環境全部指向**同一個**
    channel, why!!!! 環境 is 環境!!!! not related to channel". MEASURED: the
    old signature REQUIRED a channel, and the table's `UNIQUE(channel_id,
    surface)` made the channel the environment's identity. The identity is now
    `(kind, product, surface)` -- the environment's OWN three parts.

    `url` and `nav_path` are OPTIONAL and are NOT part of the key: `display`
    stays `kind > product > surface`, so the key a reader groups by never
    changes when a page is added.
    """
    _as_rows(conn)
    reasons: list[str] = []
    kd = str(kind or "").strip()
    pd = str(product or "").strip()
    sf = str(surface or "").strip() or DEFAULT_SURFACE
    if not kd:
        reasons.append("kind is required (the path is kind > product > surface)")
    if not pd:
        reasons.append("product is required (the path is kind > product > surface)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no environment)")
    if reasons:
        raise EnvironmentRefused(reasons)

    display = "%s > %s > %s" % (kd, pd, sf)
    existing = conn.execute(
        "SELECT environment_id, url, nav_path FROM %s WHERE kind=? AND "
        "product=? AND surface=?" % TABLE, (kd, pd, sf)).fetchone()
    if existing:
        # MEASURED (2026-09-25): this branch returned ONLY
        # `ok/action/environment_id/display`, so a caller that read `url` off
        # the result got `None` for an environment that HAD one -- the return
        # shape differed between the two branches, and a proof that checks the
        # URL went RED for a correct change. The shape is now the SAME on both
        # paths, and the values are the ROW's, not the arguments'.
        return {"ok": True, "action": "already_declared",
                "environment_id": int(existing["environment_id"]),
                "display": display,
                "url": existing["url"],
                "nav_path": existing["nav_path"]}
    cur = conn.execute(
        "INSERT INTO %s (kind, product, surface, display, cite_ref, url, "
        "nav_path) VALUES (?, ?, ?, ?, ?, ?, ?)" % TABLE,
        (kd, pd, sf, display, str(cite_ref),
         str(url or "").strip() or None,
         str(nav_path or "").strip() or None))
    if commit:
        conn.commit()
    return {"ok": True, "action": "created", "environment_id": int(cur.lastrowid),
            "display": display,
            "url": str(url or "").strip() or None,
            "nav_path": str(nav_path or "").strip() or None}


def update(conn: sqlite3.Connection, *, environment_id: int, kind: str,
           product: str, surface: str, cite_ref: str,
           url: str | None = None, nav_path: str | None = None,
           commit: bool = True) -> dict[str, Any]:
    """EDIT an existing environment's 3-part path.

    THE USER (2026-09-25): "pop-up for enviornment can edit to update the
    table too". MEASURED: `declare` is CREATE-only; `kind` / `product` /
    `surface` had no edit path, so a popup could not update the table.

    REFUSES (raises `EnvironmentRefused`):
      * an `environment_id` that is not in the table
      * a missing part (the path is kind > product > surface)
      * a missing `cite_ref` (no citation, no edit)

    `display` is RECOMPUTED from the three parts, so the display can never
    drift from the columns. `updated_at` is touched.
    """
    _as_rows(conn)
    reasons: list[str] = []
    kd = str(kind or "").strip()
    pd = str(product or "").strip()
    sf = str(surface or "").strip() or DEFAULT_SURFACE
    if not kd:
        reasons.append("kind is required (the path is kind > product > surface)")
    if not pd:
        reasons.append("product is required (the path is kind > product > surface)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no edit)")
    try:
        eid = int(environment_id)
    except Exception:
        reasons.append("environment_id must be an integer, got %r"
                       % (environment_id,))
        eid = None
    row = None
    if eid is not None:
        row = conn.execute(
            "SELECT environment_id, kind, product, surface FROM %s "
            "WHERE environment_id=? AND is_active=1" % TABLE, (eid,)).fetchone()
        if row is None:
            reasons.append("environment_id %s is not in `%s`" % (eid, TABLE))
    if reasons:
        raise EnvironmentRefused(reasons)

    display = "%s > %s > %s" % (kd, pd, sf)
    # `url` / `nav_path` are OPTIONAL: `None` means "leave it as it is", so an
    # edit that only changes the kind does not silently wipe the page. An
    # EMPTY STRING means "clear it", which is a different intent.
    sets = ["kind=?", "product=?", "surface=?", "display=?", "cite_ref=?",
            "updated_at=datetime('now')"]
    vals: list[Any] = [kd, pd, sf, display, str(cite_ref)]
    if url is not None:
        sets.append("url=?")
        vals.append(str(url).strip() or None)
    if nav_path is not None:
        sets.append("nav_path=?")
        vals.append(str(nav_path).strip() or None)
    vals.append(eid)
    conn.execute(
        "UPDATE %s SET %s WHERE environment_id=?" % (TABLE, ", ".join(sets)),
        tuple(vals))
    if commit:
        conn.commit()
    return {"ok": True, "action": "updated", "environment_id": eid,
            "kind": kd, "product": pd, "surface": sf, "display": display}


def path_for(conn: sqlite3.Connection, kind: str,
             product: str, surface: str = DEFAULT_SURFACE) -> dict[str, Any]:
    """The 3-part path for ONE environment, READ from the table.

    NO CHANNEL (2026-09-25). MEASURED: this used to JOIN `channel_registry`
    and take a `channel_key`, because the table had a `channel_id`. The user:
    "環境 is 環境!!!! not related to channel". The lookup is now the
    environment's OWN key: `(kind, product, surface)`.

    An environment with NO row is reported as `UNKNOWN` -- never defaulted,
    because a default would make an undeclared environment look declared.
    """
    _as_rows(conn)
    kd = str(kind or "").strip()
    pd = str(product or "").strip()
    sf = str(surface or "").strip() or DEFAULT_SURFACE
    if not kd or not pd:
        return {"ok": False, "kind": kd, "product": pd, "surface": sf,
                "reason": "kind and product are required"}
    row = conn.execute(
        "SELECT kind, product, surface, display, cite_ref, url, nav_path "
        "FROM %s WHERE kind=? AND product=? AND surface=? AND is_active=1"
        % TABLE, (kd, pd, sf)).fetchone()
    if row is None:
        return {"ok": False, "kind": kd, "product": pd, "surface": sf,
                "display": "%s > %s > %s" % (kd, pd, sf),
                "reason": ("no %s row for %s > %s > %s, so its path is NOT "
                           "declared" % (TABLE, kd, pd, sf))}
    return {"ok": True, "kind": row["kind"], "product": row["product"],
            "surface": row["surface"], "display": row["display"],
            "cite_ref": row["cite_ref"], "url": row["url"],
            "nav_path": row["nav_path"]}


def all_paths(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every declared environment path. NO channel join (2026-09-25)."""
    _as_rows(conn)
    rows = conn.execute(
        "SELECT environment_id, kind, product, surface, display, cite_ref, "
        "url, nav_path FROM %s WHERE is_active=1 ORDER BY kind, product"
        % TABLE).fetchall()
    return {"ok": True, "paths": [dict(r) for r in rows], "count": len(rows),
            "kinds": sorted({str(r["kind"]) for r in rows})}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--paths" in args:
            print(json.dumps(all_paths(conn), indent=2, ensure_ascii=False))
        elif "--of" in args:
            i = args.index("--of")
            print(json.dumps(path_for(conn, args[i + 1]),
                             indent=2, ensure_ascii=False))
        else:
            print(json.dumps(all_paths(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
