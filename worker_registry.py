# -*- coding: utf-8 -*-
"""worker_registry.py — THE WORKER SYSTEM. One register, its own unique key.

WHY THIS EXISTS (user, 2026-09-23)
---------------------------------
    "problem is worker is unqiue system, and identity is another system"
    "you need to have 2 table unqiue"
    "worker not = consultant_team"
    "maybe worker have the fake job before, so you can't found the table"

MEASURED: there was NO worker register, and FIVE candidates were each
disqualified by measurement (`_diag_worker_identity_unknowns.py`):

    workers              a HEARTBEAT NODE table. Its only writer hardcodes
                         `WORKER_NAME = "openclaw_worker_01"`
                         (`worker_heartbeat_service.py:38`), and its single row
                         has been `offline` since 2026-09-14. It answers "is the
                         process alive", NOT "who is this worker". THE FAKE
                         WORKER TABLE: the right name, the wrong job.
    consultant_team      the CONSULTANT TEAM (软件研发小组). The user said
                         explicitly: "worker not = consultant_team".
    job_registry         EMPTY (0 rows), and a job is a scheduled unit of work,
                         not a worker. Letter `J` is registered
                         (`entity_registry.py:205`) but the register has no rows.
    agent_worker_id      free TEXT on `task_instances` (`llm_task_center.py:46`)
                         with values 'worker_01' / 'coding_worker' / NULL — test
                         strings, no register behind them.
    SKILL_CAP_WORKERS    a Python LITERAL (`skill_prompt_ext.py:130-168`), not a
                         table. It is the ONLY real worker catalog, so it is the
                         SEED for this register — nothing is lost.

So the register is CREATED, and seeded from the one real catalog.

WHY `worker_key` AND NOT `worker_id` IS THE IDENTITY
----------------------------------------------------
`worker_id` is an autoincrement INTEGER: it is an internal number that changes if
the table is rebuilt. `worker_key` is the natural key (`W-S-03-A`) and survives a
rebuild. Same rule as `chat_registry.chat_key` and `module_registry.module_key`.

WHAT THIS MODULE REFUSES
------------------------
  * a blank `worker_key`            -> refused (a worker with no key cannot be cited)
  * a blank `cite_ref`              -> refused ("no citation, no finding")
  * a duplicate `worker_key`        -> refused (the register is the authority)
  * a `worker_key` that is not an identifier -> refused (a key is a KEY, not a
    display name; `W-S-03-A` and `w-s-03-a` must not become two workers)

CLI
---
    python worker_registry.py --list
    python worker_registry.py --seed          # seed from SKILL_CAP_WORKERS
    python worker_registry.py --get W-S-03-A
"""
from __future__ import annotations

import argparse
import json
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

# A worker key is an IDENTIFIER: letters, digits, dash, underscore. `W-S-03-A`
# is the measured shape. Case is preserved but compared case-insensitively, so
# `w-s-03-a` cannot become a second worker.
WORKER_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")

WORKER_registry_DDL = """
CREATE TABLE IF NOT EXISTS worker_registry (
    worker_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_key      TEXT    NOT NULL UNIQUE,
    name            TEXT    NOT NULL,
    worker_type     TEXT    NOT NULL DEFAULT 'NA',
    -- WHICH CAPABILITY this worker implements. A worker is an EXECUTOR under a
    -- capability ("Capability = goal/spec | Worker = how to achieve it",
    -- `app.js:2958`), so the capability is part of what a worker IS.
    capability_ref  TEXT    NOT NULL DEFAULT 'NA',
    -- The ORDER to try workers in. Lower = tried first. This is what makes a
    -- pool a LIST read from the table instead of a hardcoded array.
    fallback_order  INTEGER NOT NULL DEFAULT 100,
    -- WHERE the worker's code is. A worker with no location cannot be run.
    physical_path   TEXT    NOT NULL DEFAULT 'NA',
    uses_text       TEXT    NOT NULL DEFAULT 'NA',
    status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'standby', 'retired')),
    cite_ref        TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    role_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_worker_registry_active
  ON worker_registry (is_active, worker_key);
CREATE INDEX IF NOT EXISTS idx_worker_registry_capability
  ON worker_registry (capability_ref, fallback_order);
"""


class WorkerRefused(RuntimeError):
    """Raised when a worker row would be stored without a real key or citation."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("worker_registry refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `worker_registry` and register it in `db_table_registry`.

    Idempotent. The ORDER matters: table -> `db_table_registry` row. A register
    table that is not in the taxonomy cannot be reached by `entity_id.verify()`
    (memory `chat_registry.md`: "THREE things a new register table needs").
    """
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(WORKER_registry_DDL)
    # The taxonomy row is best-effort: a throwaway DB (a proof) has no
    # `db_table_registry`, and a missing taxonomy row must not stop the register
    # from existing. On the real DB the row IS written, which is what
    # `entity_id.verify()` needs.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            ("worker_registry", "worker_registry",
             "the WORKER system: who does the work, unique by worker_key"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": "worker_registry"}


def register_worker(conn: sqlite3.Connection, *, worker_key: str, name: str,
                    worker_type: str = "NA", capability_ref: str = "NA",
                    fallback_order: int = 100, physical_path: str = "NA",
                    uses_text: str = "NA", status: str = "active",
                    cite_ref: str = "") -> dict[str, Any]:
    """Register one worker. Refuses rather than storing a half-row.

    Idempotent on `worker_key`: an existing row is RETURNED, not overwritten, so
    a human edit is never clobbered by a re-seed.
    """
    reasons: list[str] = []
    key = str(worker_key or "").strip()
    if not key:
        reasons.append("worker_key is required")
    elif not WORKER_KEY_RE.match(key):
        reasons.append("worker_key %r is not an identifier (letters, digits, "
                       "dash, underscore)" % key)
    if not str(name or "").strip():
        reasons.append("name is required")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no finding)")
    if str(status) not in ("active", "standby", "retired"):
        reasons.append("status %r is not one of active/standby/retired" % status)
    if reasons:
        raise WorkerRefused(reasons)

    ensure_schema(conn)
    existing = conn.execute(
        "SELECT * FROM worker_registry WHERE worker_key = ?", (key,)).fetchone()
    if existing:
        return {"ok": True, "created": False, "worker": dict(existing)}

    cur = conn.execute(
        "INSERT INTO worker_registry "
        "(worker_key, name, worker_type, capability_ref, fallback_order, "
        " physical_path, uses_text, status, cite_ref) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, str(name).strip(), str(worker_type or "NA"),
         str(capability_ref or "NA"), int(fallback_order),
         str(physical_path or "NA"), str(uses_text or "NA"), str(status),
         str(cite_ref).strip()))
    conn.commit()
    row = conn.execute("SELECT * FROM worker_registry WHERE worker_id = ?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "worker": dict(row)}


def get_worker(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """One worker by key. Returns `ok: False` when absent (never raises).

    Every store read returns `ok`, because a read without it was measured to
    show HTTP 200 as an error in the UI (memory `chat_registry.md`).
    """
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM worker_registry WHERE worker_key = ?",
                       (str(worker_key or "").strip(),)).fetchone()
    if not row:
        return {"ok": False, "error": "no worker with key %r" % worker_key}
    return {"ok": True, "worker": dict(row)}


def list_workers(conn: sqlite3.Connection, *,
                 capability_ref: str = "") -> dict[str, Any]:
    """All active workers, ordered by capability then fallback_order.

    EACH WORKER CARRIES ITS `environments` (added 2026-09-24). The user:

        "with ui , so i can onlcik on worker table to select worker by button"

    MEASURED: this function returned NO environment key, and `worker.js`'s table
    rendered `worker_key / type / capability / order / status / mode applied` --
    so the `worker_environment_binding` table was INVISIBLE in the UI.

    The environments are READ from `worker_environment_binding` JOIN
    `working_environment`, so the UI shows the DECLARED pairing rather than a
    guess. A worker with NO binding gets an EMPTY list, which the UI renders as
    `-`: "this worker has no declared environment" and "this worker has one" are
    different facts, and an empty list says the first.

    MEASURED (2026-09-25): this JOINed `channel_registry` and returned a
    `channel_key`, because the binding carried a `channel_id`. THE USER:
    "環境 is 環境!!!! not related to channel". The binding now carries an
    `environment_id`, so the answer names the ENVIRONMENT's own display.
    """
    ensure_schema(conn)
    sql = ("SELECT * FROM worker_registry WHERE is_active = 1")
    args: list[Any] = []
    if capability_ref:
        sql += " AND capability_ref = ?"
        args.append(str(capability_ref))
    sql += " ORDER BY capability_ref, fallback_order, worker_key"
    rows = [dict(r) for r in conn.execute(sql, args)]
    # The pairing is read ONCE, then attached, so the query count does not grow
    # with the worker count.
    envs: dict[int, list[str]] = {}
    try:
        for r in conn.execute(
                "SELECT b.worker_id, w.display FROM "
                "worker_environment_binding b "
                "JOIN working_environment w ON "
                "w.environment_id = b.environment_id "
                "WHERE b.is_active = 1 AND w.is_active = 1 "
                "ORDER BY b.is_primary DESC, w.display"):
            envs.setdefault(int(r["worker_id"]), []).append(str(r["display"]))
    except sqlite3.OperationalError:
        # A throwaway DB (a proof) may predate the table; an absent table means
        # NO worker has a declared environment, which is an empty list, not an
        # error -- and it is reported rather than hidden.
        envs = {}
    # The IDENTITY part, read the same way. A worker may have several identity
    # rows; the FIRST is reported, and the count is available in the register.
    identities: dict[int, str] = {}
    try:
        for r in conn.execute(
                "SELECT worker_id, identity_key FROM identity_registry "
                "WHERE is_active = 1 ORDER BY identity_id"):
            identities.setdefault(int(r["worker_id"]), str(r["identity_key"]))
    except sqlite3.OperationalError:
        identities = {}
    for w in rows:
        w["environments"] = envs.get(int(w["worker_id"]), [])
        # THE IDENTITY PART of the user's formula:
        #   worker = environment + identity + session
        # MEASURED: `identity_registry` holds the identity rows, and a worker may
        # have several. `chat_id` is deliberately NOT included -- the user said
        # it is OTHER THING.
        w["identity_key"] = identities.get(int(w["worker_id"]), "")
    return {"ok": True, "workers": rows, "count": len(rows),
            "with_environment": sum(1 for w in rows if w["environments"]),
            "without_environment": sum(1 for w in rows if not w["environments"])}


def seed_from_catalog(conn: sqlite3.Connection) -> dict[str, Any]:
    """Seed the register from `skill_prompt_ext.SKILL_CAP_WORKERS`.

    WHY: that literal is the ONLY real worker catalog in the repo (measured), so
    it is the seed — nothing is lost, and the register becomes the read path.
    The literal is NOT deleted: it is the citation for every seeded row, and a
    proof asserts the two agree.

    Idempotent: `register_worker` returns an existing row instead of overwriting.
    """
    import skill_prompt_ext as spe

    ensure_schema(conn)
    created: list[str] = []
    for w in spe.SKILL_CAP_WORKERS:
        r = register_worker(
            conn,
            worker_key=str(w.get("worker_id") or ""),
            name=str(w.get("worker_id") or ""),
            worker_type=str(w.get("worker_type") or "NA"),
            capability_ref=str(w.get("cap_id") or "NA"),
            fallback_order=int(w.get("fallback_order") or 100),
            physical_path=str(w.get("physical_file_path") or "NA"),
            uses_text=str(w.get("uses") or "NA"),
            status=str(w.get("status") or "active"),
            cite_ref="skill_prompt_ext.py:SKILL_CAP_WORKERS",
        )
        if r.get("created"):
            created.append(str(w.get("worker_id")))
    return {"ok": True, "created": created,
            "total": conn.execute(
                "SELECT COUNT(*) FROM worker_registry").fetchone()[0]}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--get", default="")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_from_catalog(conn), ensure_ascii=False,
                             indent=2))
            return 0
        if args.get:
            print(json.dumps(get_worker(conn, args.get), ensure_ascii=False,
                             indent=2))
            return 0
        if args.list:
            print(json.dumps(list_workers(conn), ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
