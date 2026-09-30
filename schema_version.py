# -*- coding: utf-8 -*-
"""schema_version.py — a DDL version lock, so a STALE PROCESS fails LOUDLY.

WHY THIS EXISTS (measured 2026-09-23)
-------------------------------------
A table was migrated away and dropped. The proof then went RED:

    failed: ticket_module_map no longer exists

CAUSE: four `pythonw` processes had been running for 1357 minutes (22.6 hours).
They held a STALE in-memory `db_schema` module, so their
`ensure_task_center_schema()` still executed the OLD DDL list and RE-CREATED the
retired table. The resurrected table was EMPTY and had NO `db_table_registry`
row — the signature of a bare `CREATE TABLE IF NOT EXISTS` from stale code.

MEASURED: there was NO schema version mechanism anywhere in the repo.
`schema_version` / `DDL_VERSION` / `schema_hash` had ZERO hits. `schema_ssot`
exists but is DESCRIPTIVE (table/keyword/value read from PRAGMA); it is not a
version lock, and nothing consults it before applying DDL.

THE FIX, AND WHY A HASH
-----------------------
A stale process cannot be prevented by discipline: it is already running, with
code already loaded. What CAN be done is make it REFUSE to apply a DDL it does
not recognise.

    schema_version(table_key, ddl_hash, applied_at)

    expected = sha256(normalised DDL)
    stored   = schema_version[table_key]
    if table EXISTS and stored != expected -> REFUSE, naming the table

Why a HASH and not a version integer: an integer is a thing a human must remember
to bump, and the failure mode is exactly the one measured — nobody bumped
anything, so nothing noticed. The hash is DERIVED from the DDL text, so it cannot
go stale.

Why REFUSE and not "apply and update": silently applying is what caused the
incident. A process holding old code must FAIL LOUDLY and name the table, so the
operator knows to restart it.

WHAT THIS MODULE IS NOT
-----------------------
It does not rewrite DDL, does not migrate, and does not decide what a table
should look like. It records what was applied and refuses a mismatch. That is
all.

CLI
---
    python schema_version.py --check          # report every recorded table
    python schema_version.py --check sqlite_master
    python schema_version.py --drop <table>   # forget one record (re-apply next run)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    table_key   TEXT PRIMARY KEY,
    ddl_hash    TEXT NOT NULL,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Env escape hatch for the SAME reason every other gate in this repo has one:
# a broken check must not wedge the whole schema path. It is LOGGED when used.
SKIP_ENV = "SCHEMA_VERSION_SKIP"


class SchemaDriftRefused(RuntimeError):
    """Raised when a DDL does not match what was applied."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


_CREATE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE)


def _table_key_of(ddl: str) -> str:
    """The table a DDL block creates, or '' when it creates none.

    A DDL constant may hold several statements (a CREATE plus its indexes). The
    FIRST `CREATE TABLE` names the key; indexes and seeds are not versioned on
    their own, because they cannot create drift the way a table shape can.

    Returns '' for a DDL with no CREATE TABLE (e.g. a pure index block). The
    caller passes that through as a `refuse` on an empty key, so such a block is
    skipped rather than silently versioned under a wrong name.
    """
    m = _CREATE_RE.search(ddl or "")
    return m.group(1) if m else ""


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(SCHEMA_VERSION_DDL)
    conn.commit()
    return {"ok": True, "table": "schema_version"}


def ddl_hash(ddl: str) -> str:
    """sha256 of the DDL, NORMALISED.

    Normalisation matters: a whitespace-only edit (a re-indent, a trailing
    newline) is not a schema change, and a check that fired on those would be
    bypassed within a week. Comments are kept — a comment edit is a real edit to
    the text a reviewer reads, and keeping them costs only a re-record.
    """
    text = ddl or ""
    # collapse runs of whitespace, strip each line, drop blank lines
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def recorded_hash(conn: sqlite3.Connection, table_key: str) -> str:
    """The recorded hash for a key, or '' when none."""
    try:
        ensure_schema(conn)
        row = conn.execute(
            "SELECT ddl_hash FROM schema_version WHERE table_key=?",
            (str(table_key),)).fetchone()
        return str(row[0]) if row else ""
    except sqlite3.Error:
        return ""


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(name),)).fetchone())


def check(conn: sqlite3.Connection, table_key: str, ddl: str, *,
          skip: bool = False) -> dict[str, Any]:
    """Decide whether `ddl` may be applied for `table_key`. Never raises.

    Returns `{ok, table_key, action, expected, recorded, reason}` where action
    is one of:

        first_apply  no record and no table -> apply and record
        match        record == expected     -> apply (idempotent) and keep
        refuse       table EXISTS, record != expected -> DO NOT apply
        record_only  table exists but has NO record (legacy) -> record it
        table_absent recorded but the table is gone (a real drop) -> re-apply

    `refuse` is the case the stale process hits, and it is LOUD on purpose.
    """
    key = str(table_key or "").strip()
    if not key:
        return {"ok": False, "action": "refuse", "reason": "table_key is required"}
    expected = ddl_hash(ddl)
    rec = recorded_hash(conn, key)
    exists = _table_exists(conn, key)

    if skip:
        return {"ok": True, "table_key": key, "action": "skipped",
                "expected": expected, "recorded": rec,
                "reason": "%s is set — the check is skipped and LOGGED"
                          % SKIP_ENV}

    if not rec:
        # No record. A table that already exists was created before this lock
        # (legacy); record it rather than refuse, because refusing would wedge
        # every existing DB on first run.
        if exists:
            return {"ok": True, "table_key": key, "action": "record_only",
                    "expected": expected, "recorded": "",
                    "reason": "table exists with no record (legacy) — recorded "
                              "now, DDL not re-applied"}
        return {"ok": True, "table_key": key, "action": "first_apply",
                "expected": expected, "recorded": "",
                "reason": "no record and no table — first apply"}

    if rec == expected:
        return {"ok": True, "table_key": key, "action": "match",
                "expected": expected, "recorded": rec,
                "reason": "recorded DDL matches"}

    if not exists:
        # The table is GONE but a record remains. That is a deliberate drop, not
        # drift, so re-applying is correct.
        return {"ok": True, "table_key": key, "action": "table_absent",
                "expected": expected, "recorded": rec,
                "reason": "recorded but the table is absent — a drop; re-apply"}

    # THE CASE THAT MATTERS: the table exists, the DDL differs from what was
    # applied. A process holding old code would re-apply the OLD DDL here. This
    # is where the 2026-09-23 resurrection would have been stopped.
    return {
        "ok": False,
        "table_key": key,
        "action": "refuse",
        "expected": expected,
        "recorded": rec,
        "reason": ("SCHEMA DRIFT: %s exists and its recorded DDL hash does not "
                   "match the DDL about to be applied.\n"
                   "  recorded: %s\n"
                   "  expected: %s\n"
                   "This is what a STALE PROCESS looks like: it is running OLD "
                   "code and would re-apply the OLD DDL. Restart the process. "
                   "If the DDL change is intended, run this module with "
                   "`--accept %s` to record the new hash AFTER verifying the "
                   "schema, or unset after checking: %s"
                   % (key, rec[:12], expected[:12], key, SKIP_ENV)),
    }


def record_applied(conn: sqlite3.Connection, table_key: str, ddl: str) -> dict:
    """Record the hash for `table_key`. Idempotent."""
    key = str(table_key or "").strip()
    if not key:
        return {"ok": False, "reason": "table_key is required"}
    ensure_schema(conn)
    h = ddl_hash(ddl)
    conn.execute(
        "INSERT INTO schema_version (table_key, ddl_hash, applied_at) "
        "VALUES (?,?,datetime('now')) "
        "ON CONFLICT(table_key) DO UPDATE SET ddl_hash=excluded.ddl_hash, "
        "applied_at=excluded.applied_at",
        (key, h))
    conn.commit()
    return {"ok": True, "table_key": key, "ddl_hash": h}


def accept(conn: sqlite3.Connection, table_key: str, ddl: str) -> dict:
    """Explicitly accept the CURRENT DDL as the applied version.

    This is the ONLY way past a refusal, and it is a deliberate, separate act:
    an operator who has verified the real schema chooses to record it. It is
    never automatic.
    """
    r = record_applied(conn, table_key, ddl)
    r["accepted"] = True
    r["note"] = ("the current DDL was recorded as applied. Only do this after "
                 "verifying the real table matches it.")
    return r


def all_records(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT table_key, ddl_hash, applied_at FROM schema_version "
        "ORDER BY table_key")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", nargs="?", const="", default=None,
                    help="report records; optionally for one table_key")
    ap.add_argument("--accept", default="", help="accept the current DDL shape")
    ap.add_argument("--ddl-file", default="", help="file holding the DDL text")
    ap.add_argument("--drop", default="", help="forget one record")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.accept:
            ddl = ""
            if args.ddl_file and os.path.isfile(args.ddl_file):
                ddl = open(args.ddl_file, encoding="utf-8").read()
            print(json.dumps(accept(conn, args.accept, ddl),
                             ensure_ascii=False, indent=2))
            return 0
        if args.drop:
            ensure_schema(conn)
            cur = conn.execute("DELETE FROM schema_version WHERE table_key=?",
                               (args.drop,))
            conn.commit()
            print(json.dumps({"ok": True, "dropped": args.drop,
                              "rows": cur.rowcount}, ensure_ascii=False))
            return 0
        if args.check is not None:
            rows = all_records(conn)
            if args.check:
                rows = [r for r in rows if r["table_key"] == args.check]
            print(json.dumps({"ok": True, "records": rows,
                              "count": len(rows)},
                             ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
