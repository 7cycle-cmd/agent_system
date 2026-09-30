# -*- coding: utf-8 -*-
"""taxonomy_level_registry.py — the TAXONOMY LEVELS, as a TABLE.

WHY THIS EXISTS (user, 2026-09-22)
----------------------------------
    "DB driven can help worker not to have wrong data easy"
    "TAXONOMY_LEVELS = (...) / you may need 1 more table to make it to be"
    "in table design, 1) DB driven 2) id = primary + autoinscrease
     3) field value as much as it can 4) priority by id 5) never null, null = NA"
    "be skill for having a table / and make it auto forever"

The user's own rule 1 is DB driven. `TAXONOMY_LEVELS` was a Python TUPLE, so
adding a level was a CODE CHANGE — which is exactly what rule 1 forbids. This
module makes the levels a TABLE, so adding one is an INSERT.

THE TUPLE IS KEPT, AND ASSERTED AGAINST THE TABLE
-------------------------------------------------
`db_schema.TAXONOMY_LEVELS` is DEPRECATED as the SSOT but kept so an existing
caller does not break. `_proof_table_design.py` asserts the tuple and the seed
agree, so the two cannot drift — the failure mode this repo has measured before
(`source_type` was declared in two places and a lesson was silently lost).

WHY `is_active` DEFAULTS TO 0
-----------------------------
A level is a CLAIM until it is proven. `activation_gate` is the only writer of
`is_active=1`, and it requires a 100-run streak. So a newly inserted level is
inactive, and the gate promotes it. That is the same rule every other register
follows, and it is why this table is in the ACTIVATION scope (`*_registry`).

Run:
    .\\.venv\\Scripts\\python.exe taxonomy_level_registry.py --list
    .\\.venv\\Scripts\\python.exe taxonomy_level_registry.py --seed
    .\\.venv\\Scripts\\python.exe taxonomy_level_registry.py --add workflow 9 "a workflow"
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"


class TaxonomyLevelError(ValueError):
    """Raised when a level cannot be registered."""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table. Idempotent."""
    from db_schema import TAXONOMY_LEVEL_REGISTRY_DDL
    conn.executescript(TAXONOMY_LEVEL_REGISTRY_DDL)
    conn.commit()
    return {"ok": True}


def seed_levels(conn: sqlite3.Connection, *, commit: bool = True) -> dict[str, Any]:
    """Insert the eight declared levels. Idempotent on `level_key`.

    The seed is the INITIAL content only. After this, the TABLE is the SSOT and
    a new level is an INSERT — not a Python edit.

    RENAMES FIRST (root-cause fix, 2026-09-24). MEASURED: running `--seed`
    against a DB seeded before the `db_table`/`db_field` standardization INSERTED
    a SECOND row for each renamed level, because `INSERT OR IGNORE` is idempotent
    on the KEY and the key had changed (`table` -> `db_table`). That created a
    real DUPLICATE (measured: 10 rows, two with `level_order=6`). Applying the
    rename BEFORE the seed makes the seed a no-op on any DB from any history, so
    the same command is safe on a fresh DB and on an old one.
    """
    from db_schema import TAXONOMY_LEVEL_SEED
    ensure_schema(conn)
    # The rename must not create a duplicate, so it runs first and its refusal
    # (a genuine duplicate already present) is REPORTED rather than swallowed.
    pre = rename_levels(conn, commit=False)
    added = 0
    for level_key, level_order, definition in TAXONOMY_LEVEL_SEED:
        cur = conn.execute(
            "INSERT OR IGNORE INTO taxonomy_level_registry "
            "(level_key, level_order, definition, cite_ref, is_active) "
            "VALUES (?,?,?,?,0)",
            (level_key, int(level_order), definition,
             "db_schema.py:TAXONOMY_LEVEL_SEED"))
        added += cur.rowcount
    if commit:
        conn.commit()
    return {"ok": pre.get("ok", True), "added": added,
            "renamed_first": pre.get("renamed", []),
            "rename_refused": (None if pre.get("ok") else pre.get("code")),
            "total": conn.execute("SELECT COUNT(*) FROM "
                                  "taxonomy_level_registry").fetchone()[0]}


def add_level(conn: sqlite3.Connection, level_key: str, level_order: int,
              definition: str, *, cite_ref: str = "",
              commit: bool = True) -> dict[str, Any]:
    """Add ONE level. This is the whole point: an INSERT, not a code change.

    REFUSES an empty definition (a level nobody defined) and an empty citation
    (a claim nobody can check) — the same rule `citation_discipline` enforces.
    """
    key = str(level_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_LEVEL_KEY",
                "message": "level_key is required"}
    if not str(definition or "").strip():
        return {"ok": False, "code": "MISSING_DEFINITION",
                "message": ("a level with no definition is a level nobody "
                            "defined: %r" % key)}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no level: %r" % key}
    try:
        order = int(level_order)
    except (TypeError, ValueError):
        return {"ok": False, "code": "BAD_LEVEL_ORDER",
                "message": "level_order must be an integer, got %r"
                           % (level_order,)}
    if order < 1:
        return {"ok": False, "code": "BAD_LEVEL_ORDER",
                "message": "level_order must be >= 1 (priority by id), got %d"
                           % order}

    ensure_schema(conn)
    existing = conn.execute(
        "SELECT level_id FROM taxonomy_level_registry WHERE level_key=?",
        (key,)).fetchone()
    if existing:
        return {"ok": True, "level_id": int(existing[0]), "created": False,
                "level_key": key}

    cur = conn.execute(
        "INSERT INTO taxonomy_level_registry "
        "(level_key, level_order, definition, cite_ref, is_active) "
        "VALUES (?,?,?,?,0)",
        (key, order, str(definition).strip(), str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "level_id": cur.lastrowid, "created": True,
            "level_key": key}


def list_levels(conn: sqlite3.Connection, *,
                active_only: bool = False) -> list[dict[str, Any]]:
    """Every level, ordered by `level_order` (priority by id, factor 4)."""
    sql = ("SELECT * FROM taxonomy_level_registry "
           + ("WHERE is_active=1 " if active_only else "")
           + "ORDER BY level_order, level_key")
    try:
        return [dict(r) for r in conn.execute(sql)]
    except sqlite3.OperationalError:
        return []


def level_keys(conn: sqlite3.Connection) -> tuple[str, ...]:
    """The level keys, in order. The DB-driven replacement for the tuple."""
    return tuple(str(r["level_key"]) for r in list_levels(conn))


def table_exists(conn: sqlite3.Connection) -> bool:
    """Is the registry table present?

    DISTINCT from `is_valid_level` returning False. A caller must be able to tell
    "the table says no" from "there is no table yet" — my first version conflated
    them, so an un-migrated DB refused EVERY level instead of falling back to the
    deprecated tuple. Caught by RUNNING `_proof_terminology_registry.py`.
    """
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='taxonomy_level_registry'").fetchone() is not None
    except sqlite3.Error:
        return False


def is_valid_level(conn: sqlite3.Connection, level_key: str) -> bool:
    """Is this a declared level? READ FROM THE TABLE, not from a Python tuple.

    This is the function `terminology_registry.add_term` calls, so a level added
    by an INSERT is immediately valid — no code change, no restart.

    Returns False when the table is ABSENT too; a caller that needs to tell the
    two apart must call `table_exists` first.
    """
    key = str(level_key or "").strip()
    if not key or key == "NA":
        return True          # NA is the no_null standard, always legal
    try:
        return conn.execute(
            "SELECT 1 FROM taxonomy_level_registry WHERE level_key=?",
            (key,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False


def level_order(conn: sqlite3.Connection, level_key: str) -> int | None:
    """The declared order of a level, or None if it is not declared."""
    try:
        row = conn.execute(
            "SELECT level_order FROM taxonomy_level_registry WHERE level_key=?",
            (str(level_key or "").strip(),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return int(row[0]) if row else None


# ---------------------------------------------------------------------------
# rename_level — the VOCABULARY correction (user ruling 2026-09-24)
# ---------------------------------------------------------------------------
# WHY A RENAME AND NOT A DELETE+INSERT:
#   `level_id` is the IDENTITY; `level_key` is a LABEL. Deleting the row to
#   re-add it under a new name would mint a NEW id for the SAME level, which is
#   the name-as-identity defect this repo already corrected for skills
#   (`skill_identity_law.md`). So the key is UPDATED in place and the id survives.
#
# The mapping is EXPLICIT and it is the user's ruling, not a rule I invented:
#   "db_table , db_field is more respresenattive than table, field"
# A GENERIC "strip the db_ prefix" rule would be wrong — `db_table` is the target,
# not the source — and a generic "add db_" rule would rename `channel` to
# `db_channel`. So the two pairs are named, one at a time.
LEVEL_RENAMES: tuple[tuple[str, str], ...] = (
    ("table", "db_table"),
    ("field", "db_field"),
)


def rename_levels(conn: sqlite3.Connection, *, pairs=None,
                  commit: bool = True) -> dict[str, Any]:
    """Rename level KEYS in place, keeping `level_id`. Idempotent.

    REFUSES when BOTH names exist: that is not a rename, it is a DUPLICATE level,
    and silently picking one would hide a real collision behind a "success".
    """
    ensure_schema(conn)
    mapping = tuple(pairs or LEVEL_RENAMES)
    renamed: list[dict[str, Any]] = []
    skipped: list[str] = []
    for old, new in mapping:
        has_old = conn.execute(
            "SELECT 1 FROM taxonomy_level_registry WHERE level_key=?",
            (old,)).fetchone() is not None
        has_new = conn.execute(
            "SELECT 1 FROM taxonomy_level_registry WHERE level_key=?",
            (new,)).fetchone() is not None
        if has_old and has_new:
            return {"ok": False, "code": "DUPLICATE_LEVEL",
                    "message": ("both %r and %r exist — that is a DUPLICATE "
                                "level, not a rename; resolve it by hand"
                                % (old, new)),
                    "renamed": renamed, "skipped": skipped}
        if not has_old:
            # Idempotent: nothing to do, and that is reported, not silently ok.
            skipped.append("%s -> %s (no %s row)" % (old, new, old))
            continue
        conn.execute(
            "UPDATE taxonomy_level_registry SET level_key=?, "
            "updated_at=datetime('now') WHERE level_key=?", (new, old))
        renamed.append({"from": old, "to": new})
    if commit:
        conn.commit()
    return {"ok": True, "renamed": renamed, "skipped": skipped,
            "total": conn.execute("SELECT COUNT(*) FROM "
                                  "taxonomy_level_registry").fetchone()[0]}


def resolve_duplicate(conn: sqlite3.Connection, old: str, new: str, *,
                      commit: bool = True) -> dict[str, Any]:
    """Resolve a REAL duplicate created by a historical `--seed`.

    DELIBERATELY SEPARATE from `rename_levels`, whose job is to REFUSE a
    duplicate. Two different acts need two different tools: a rename that
    silently merged would hide a genuine collision behind a "success".

    It KEEPS THE LOWEST `level_id` (the ORIGINAL identity) and deletes the later
    row, and it REFUSES when the loser's id is referenced by anything — deleting
    a referenced row is not a cleanup, it is an orphan factory.
    """
    ensure_schema(conn)
    # A DUPLICATE must be the SAME level twice: the two rows must be ADJACENT by
    # `level_order`. MEASURED BUG in an earlier version of this function: it
    # checked only "one row named old, one row named new" and proceeded to DELETE
    # a row — so calling it with two REAL levels (`channel`, `module`) DELETED the
    # `module` level. A guard that identifies a duplicate by NAME ALONE will
    # delete a healthy level, so the check is the ORDER, read from the rows.
    rows = [dict(r) for r in conn.execute(
        "SELECT level_id, level_key, level_order FROM taxonomy_level_registry "
        "WHERE level_key IN (?,?) ORDER BY level_order, level_id", (old, new))]
    keys = {r["level_key"] for r in rows}
    if keys != {old, new}:
        return {"ok": False, "code": "NOT_A_DUPLICATE",
                "message": ("expected exactly one row named %r and one named %r, "
                            "found %s" % (old, new, sorted(keys))), "rows": rows}
    orders = {int(r["level_order"]) for r in rows}
    if len(orders) != 1:
        return {"ok": False, "code": "NOT_A_DUPLICATE",
                "message": ("%r and %r are DIFFERENT levels (order %s vs %s), so "
                            "they are not a duplicate — refusing to delete "
                            "anything" % (rows[0]["level_key"],
                                          rows[1]["level_key"],
                                          int(rows[0]["level_order"]),
                                          int(rows[1]["level_order"]))),
                "rows": rows}
    keep, drop = rows[0], rows[1]
    # A referenced row must not be deleted.
    for table in (t["name"] for t in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")):
        if table == "taxonomy_level_registry":
            continue
        cols = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)]
        if "level_id" not in cols:
            continue
        n = conn.execute("SELECT COUNT(*) FROM %s WHERE level_id=?" % table,
                         (int(drop["level_id"]),)).fetchone()[0]
        if n:
            return {"ok": False, "code": "DUPLICATE_REFERENCED",
                    "message": ("%d row(s) in %s reference level_id=%d — resolve "
                                "by hand, do not delete"
                                % (n, table, int(drop["level_id"])))}
    conn.execute("DELETE FROM taxonomy_level_registry WHERE level_id=?",
                 (int(drop["level_id"]),))
    # Now the surviving row carries the ORIGINAL id and the OLD name; the rename
    # gives it the standardized name.
    conn.execute(
        "UPDATE taxonomy_level_registry SET level_key=?, "
        "updated_at=datetime('now') WHERE level_id=?",
        (new, int(keep["level_id"])))
    if commit:
        conn.commit()
    return {"ok": True, "kept_id": int(keep["level_id"]),
            "dropped_id": int(drop["level_id"]), "level_key": new}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="taxonomy level registry")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--rename", action="store_true",
                    help="apply LEVEL_RENAMES (table->db_table, field->db_field)")
    ap.add_argument("--resolve-duplicate", nargs=2, metavar=("OLD", "NEW"),
                    help="resolve a REAL duplicate: keep the lowest level_id")
    ap.add_argument("--add", nargs=3, metavar=("KEY", "ORDER", "DEFINITION"))
    ap.add_argument("--cite", default="")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.seed:
            print(seed_levels(conn))
        if args.rename:
            print(rename_levels(conn))
        if args.resolve_duplicate:
            old, new = args.resolve_duplicate
            print(resolve_duplicate(conn, old, new))
        if args.add:
            key, order, definition = args.add
            print(add_level(conn, key, order, definition, cite_ref=args.cite))
        if args.list or not (args.seed or args.add or args.rename or args.resolve_duplicate):
            for r in list_levels(conn):
                print("  %-12s order=%-3s active=%s  %s"
                      % (r["level_key"], r["level_order"], r["is_active"],
                         r["definition"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
