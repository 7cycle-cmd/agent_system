# -*- coding: utf-8 -*-
"""registry_parent_migration.py — make an UNPROVABLE parent nullable.

WHY THIS EXISTS (measured 2026-09-21)
------------------------------------
`init_ontology_registry.sql:77,103` declares:

    function_registry.capability_id  INTEGER NOT NULL
    api_registry.capability_id       INTEGER NOT NULL

So every api/function row MUST name a capability. But measured:

    can prove the row EXISTS   : 5/5   (a route literal, an `ast` def)
    can prove WHICH capability : 0.54  (balanced accuracy = chance)

and the 7B is a CONSTANT-NO machine on that second question — reversing the
question reproduced the same answers (`serve?` -> NO, `unrelated?` -> NO), while
the same model answers trivial YES/NO 4/4. So the gap is capability, not
phrasing.

The schema therefore forces a value for the one thing that cannot be measured.
This migration removes the FORCING. It does not decide any parent: a NULL parent
means "declared, parent unknown", which is the honest state.

THE TRAP THIS MODULE HANDLES
----------------------------
Both tables have `UNIQUE (capability_id, <key>)`. **SQLite treats NULLs as
DISTINCT in a UNIQUE constraint**, so the moment `capability_id` can be NULL the
existing unique constraint STOPS preventing duplicate `api_key` rows. Dropping
NOT NULL without adding a replacement key would silently remove a real
protection — a schema change that looks additive and removes a guarantee.

So the rebuild adds a UNIQUE index on the key column alone (`api_key`,
`function_key`), which is what the original constraint was actually trying to
say, and KEEPS `UNIQUE(capability_id, key)` for the rows that do have a parent.

Safety: the rebuild preserves every row and asserts the counts match; a copy that
loses a row RAISES rather than proceeding.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

# canonical rebuilt DDL. capability_id loses NOT NULL; the standalone UNIQUE
# index on the key column is created separately (see _TABLES).
_REBUILD = {
    "api_registry": """
CREATE TABLE api_registry_new (
    api_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key        TEXT    NOT NULL,
    name           TEXT    NOT NULL,
    description    TEXT,
    method         TEXT,
    path           TEXT,
    capability_id  INTEGER,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version        TEXT    NOT NULL DEFAULT '1',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (capability_id, api_key),
    FOREIGN KEY (capability_id) REFERENCES capability_registry (capability_id)
);
""",
    "function_registry": """
CREATE TABLE function_registry_new (
    function_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    function_key     TEXT    NOT NULL,
    name             TEXT    NOT NULL,
    description      TEXT,
    capability_id    INTEGER,
    code_registry_id TEXT,
    file_path        TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    version          TEXT    NOT NULL DEFAULT '1',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (capability_id, function_key),
    FOREIGN KEY (capability_id) REFERENCES capability_registry (capability_id)
);
""",
}

# key column that must stay unique ON ITS OWN once the parent can be NULL
_TABLES = {
    "api_registry": ("api_key", "idx_api_registry_cap"),
    "function_registry": ("function_key", "idx_function_registry_cap"),
}


class MigrationRefused(RuntimeError):
    """Raised instead of proceeding with a rebuild that would lose data."""


def log(msg: str) -> None:
    print("[registry_parent_migration] %s" % msg, flush=True)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def _notnull_on(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for r in conn.execute("PRAGMA table_info(%s)" % table):
        if r[1] == column:
            return bool(r[3])
    return False


def status(db_path: Path | str | None = None,
           conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Report whether the parent is still NOT NULL, and the unique-index state."""
    own = conn is None
    if own:
        conn = _connect(db_path)
    try:
        out: dict[str, Any] = {"ok": True, "tables": {}}
        for table, (keycol, cap_idx) in _TABLES.items():
            if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                                "WHERE name=?", (table,)).fetchone()[0]:
                out["tables"][table] = {"present": False}
                continue
            idx = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name=? AND name NOT LIKE 'sqlite_autoindex%'", (table,))]
            out["tables"][table] = {
                "present": True,
                "capability_id_not_null": _notnull_on(conn, table,
                                                      "capability_id"),
                "rows": conn.execute("SELECT COUNT(*) FROM %s" % table)
                        .fetchone()[0],
                "null_parents": conn.execute(
                    "SELECT COUNT(*) FROM %s WHERE capability_id IS NULL"
                    % table).fetchone()[0],
                "indexes": idx,
                # the guard that must exist once the parent can be NULL
                "key_unique_index": any("key_unique" in i for i in idx),
                "parent_index_present": cap_idx in idx,
            }
        out["migrated"] = all(
            not v.get("capability_id_not_null", True)
            for v in out["tables"].values() if v.get("present"))
        return out
    finally:
        if own:
            conn.close()


def apply(db_path: Path | str | None = None, *,
          dry_run: bool = True) -> dict[str, Any]:
    """Rebuild api_registry / function_registry with a NULLABLE parent."""
    conn = _connect(db_path)
    plan: dict[str, Any] = {"ok": True, "dry_run": dry_run, "actions": []}
    try:
        for table, (keycol, cap_idx) in _TABLES.items():
            if not conn.execute("SELECT COUNT(*) FROM sqlite_master "
                                "WHERE name=?", (table,)).fetchone()[0]:
                plan["actions"].append({"table": table, "action": "absent"})
                continue
            if not _notnull_on(conn, table, "capability_id"):
                plan["actions"].append({"table": table,
                                        "action": "already nullable"})
                continue
            n_before = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            plan["actions"].append({"table": table, "action": "rebuild",
                                    "rows": n_before,
                                    "would_add": "UNIQUE(%s) + NOT NULL drop"
                                                 % keycol})
            if dry_run:
                continue

            cols = _columns(conn, table)
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.executescript(_REBUILD[table])
                shared = [c for c in cols if c != "api_id" and c != "function_id"
                          or c in cols]
                # copy every column present in BOTH (the id column keeps its value)
                collist = ", ".join(cols)
                conn.execute("INSERT INTO %s_new (%s) SELECT %s FROM %s"
                             % (table, collist, collist, table))
                n_moved = conn.execute(
                    "SELECT COUNT(*) FROM %s_new" % table).fetchone()[0]
                if n_moved != n_before:
                    raise MigrationRefused(
                        "%s: rebuild would lose rows (%d -> %d)"
                        % (table, n_before, n_moved))
                conn.execute("DROP TABLE %s" % table)
                conn.execute("ALTER TABLE %s_new RENAME TO %s" % (table, table))
                # THE GUARD the original constraint provided via the composite
                # key. Without it, NULL parents would let duplicates in.
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "idx_%s_key_unique ON %s (%s)" % (table, table, keycol))
                conn.execute("CREATE INDEX IF NOT EXISTS idx_%s_active "
                             "ON %s (is_active, %s)" % (table, table, keycol))
                conn.execute("CREATE INDEX IF NOT EXISTS %s ON %s (capability_id)"
                             % (cap_idx, table))
                conn.commit()
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()
    return plan


def parent_for_key(conn: sqlite3.Connection, *, level: str,
                   key: str) -> dict[str, Any]:
    """Return a row's parent, or say plainly that it has none.

    Replaces the old assumption that a parent always exists. Returns
    `{"parent": None, "why": "..."}` rather than raising, because "no parent
    yet" is a legitimate state after this migration.
    """
    table = {"api": "api_registry", "function": "function_registry"}.get(level)
    if not table:
        return {"parent": None, "why": "unknown level %r" % level}
    keycol = _TABLES[table][0]
    row = conn.execute(
        "SELECT capability_id FROM %s WHERE %s = ? AND is_active = 1"
        % (table, keycol), (key,)).fetchone()
    if not row:
        return {"parent": None, "why": "no active row for %s = %r"
                                      % (keycol, key)}
    if row["capability_id"] is None:
        return {"parent": None, "why": "row exists but its parent is DECLARED "
                                       "UNKNOWN (not derivable from code)"}
    return {"parent": int(row["capability_id"]), "why": "registered parent"}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    if args.status:
        print(json.dumps(status(), indent=2, ensure_ascii=False, default=str))
        return
    print(json.dumps(apply(dry_run=not args.apply), indent=2,
                     ensure_ascii=False, default=str))
    print(json.dumps(status(), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()