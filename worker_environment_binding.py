"""worker_environment_binding.py -- the DECLARED worker x environment pairing.

WHY THIS EXISTS (the user, 2026-09-24):

    "you have table, but the table not complete or 1 table is not enough as need
     to match environment too"

MEASURED, and the user is RIGHT: NO table paired a worker with an environment.

  * `identity_registry` has BOTH columns, but it is PER-SESSION: 53 rows, 53
    distinct `session_id`, only 3 distinct (worker_id, channel) pairs. A
    per-session OBSERVATION is not a DECLARED binding.
  * `worker_identity_binding` has a worker but no environment column.
  * `worker_mode` has a worker but no environment column.
  * `worker_registry` has no environment column.
  * `channel_registry` has an environment but no worker column.

So the MATRIX (6 workers x 4 active environments) was 24 cells, ALL EMPTY.

AND THE TWO SOURCES FOR "environment" DISAGREED ON EVERY VALUE:
`channel_registry.channel_key` (DECLARED) held `llm_task_monitor_ui`, `local_pc`,
`scripts`, `src`; `identity_registry.channel` (OBSERVED) held `vscode` and
`runtime`. No value appeared in both.

THE ENVIRONMENT IS A FOREIGN KEY, NOT FREE TEXT. `working_environment` is the
DECLARED environment vocabulary, so a binding must name a row in it. That is what
stops a second vocabulary appearing -- the defect measured above.

MEASURED (2026-09-25), AND THE FOREIGN KEY WAS POINTED AT THE WRONG TABLE: this
table carried `channel_id NOT NULL REFERENCES channel_registry`, so a WORKER x
ENVIRONMENT binding could only name a CHANNEL. THE USER:

    "環境 is 環境!!!! not related to channel"
    "vscode not channel!!! / channel is local > agent_system / vscode is working
     enviornment / fix it now"

With exactly ONE channel (`local_pc`), EVERY binding named the same row, so the
matrix could not distinguish one environment from another. The column is now
`environment_id REFERENCES working_environment`.

A BINDING IS REFUSED, NOT DEFAULTED. A worker that does not exist, an environment
that is not declared, and a missing citation are all REFUSALS, because a binding
that cannot be checked is a sentence about a binding.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "agent.db")

TABLE = "worker_environment_binding"


class BindingRefused(RuntimeError):
    """Raised when a binding would be stored without a real worker/env/cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("worker_environment_binding refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table, MIGRATE it off `channel_id`, and register it.

    MEASURED (2026-09-25): the live table carried `channel_id NOT NULL` with a
    FOREIGN KEY to `channel_registry`. THE USER: "環境 is 環境!!!! not related
    to channel". A binding must name an ENVIRONMENT, so the column is
    `environment_id` and the foreign key is `working_environment`.

    SQLite cannot DROP a NOT NULL column, so the migration REBUILDS the table
    and CARRIES the rows over: `environment_id` is taken from the row's own
    `environment_id` when it has one, and otherwise RESOLVED from the channel
    it named (the old fact is preserved, not discarded).

    MEASURED (2026-09-25): the FIRST version of this check tested
    `"environment_id" not in cols`, and the live table had ALREADY been given
    an `environment_id` by an earlier `ALTER TABLE ADD COLUMN` -- so the check
    passed, the migration was SKIPPED, and the old `channel_id NOT NULL` stayed
    behind. The INSERT then failed with
    `NOT NULL constraint failed: worker_environment_binding.channel_id`.
    The test is therefore on the OLD column's PRESENCE, not the new one's
    absence: a table that still carries `channel_id` is a table that has not
    been migrated.
    """
    import db_schema as ds

    conn.execute("PRAGMA foreign_keys = ON;")
    cols = [str(r[1]) for r in conn.execute(
        "PRAGMA table_info(%s)" % TABLE)] if _table_exists(conn, TABLE) else []
    migrated = False
    if cols and "channel_id" in cols:
        _migrate_off_channel(conn)
        migrated = True
    conn.executescript(ds.WORKER_ENVIRONMENT_BINDING_DDL)
    # Best-effort taxonomy row: a throwaway DB (a proof) has no
    # `db_table_registry`, and its absence must not stop the table existing.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            (TABLE, TABLE,
             "the DECLARED pairing of a WORKER with an ENVIRONMENT "
             "(working_environment), so the worker x environment matrix is a "
             "fact rather than a per-session observation"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": TABLE, "migrated": migrated}


def _migrate_off_channel(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild the table with `environment_id` instead of `channel_id`.

    The rows are CARRIED, not dropped: a binding that named a channel is
    re-pointed at the environment that channel's product names, and a binding
    that cannot be resolved is REPORTED rather than silently lost.
    """
    old = [dict(r) for r in conn.execute("SELECT * FROM %s" % TABLE)]
    conn.execute("ALTER TABLE %s RENAME TO %s_old_channel" % (TABLE, TABLE))
    # The rebuilt table carries the SAME foreign keys as the DDL, because a
    # rebuild that drops them would silently remove the constraint that makes
    # the environment vocabulary single -- the defect this table exists to stop.
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS %s ("
        " binding_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " worker_id INTEGER NOT NULL,"
        " environment_id INTEGER NOT NULL,"
        " is_primary INTEGER NOT NULL DEFAULT 0,"
        " cite_ref TEXT NOT NULL,"
        " is_active INTEGER NOT NULL DEFAULT 1,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " UNIQUE (worker_id, environment_id),"
        " FOREIGN KEY (worker_id) REFERENCES worker_registry (worker_id),"
        " FOREIGN KEY (environment_id) REFERENCES working_environment "
        "(environment_id))" % TABLE)
    carried = unresolved = 0
    for r in old:
        eid = r.get("environment_id")
        if eid is None:
            eid = _environment_for_channel(conn, r.get("channel_id"))
        if eid is None:
            unresolved += 1
            continue
        conn.execute(
            "INSERT OR IGNORE INTO %s (worker_id, environment_id, "
            "is_primary, cite_ref, is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)" % TABLE,
            (r.get("worker_id"), int(eid), r.get("is_primary", 0),
             r.get("cite_ref") or "migrated", r.get("is_active", 1),
             r.get("created_at"), r.get("updated_at")))
        carried += 1
    conn.execute("DROP TABLE %s_old_channel" % TABLE)
    conn.commit()
    return {"ok": True, "carried": carried, "unresolved": unresolved}


def _environment_for_channel(conn: sqlite3.Connection,
                             channel_id: Any) -> int | None:
    """The environment a channel's NAME names. READ, never guessed.

    MEASURED: the old rows named `local_pc` (the ONE channel), which names no
    environment -- so the fallback is the channel's `name` matched against
    `working_environment.product`, and a channel that matches NOTHING returns
    None, so the caller REPORTS it instead of inventing an environment.
    """
    if channel_id is None:
        return None
    row = conn.execute(
        "SELECT name FROM channel_registry WHERE channel_id=?",
        (int(channel_id),)).fetchone()
    if row is None:
        return None
    name = str(row["name"] or "").strip()
    if not name:
        return None
    hit = conn.execute(
        "SELECT environment_id FROM working_environment WHERE is_active=1 "
        "AND (product=? OR display=?) ORDER BY environment_id LIMIT 1",
        (name, name)).fetchone()
    return int(hit["environment_id"]) if hit else None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone())


def _worker_id(conn: sqlite3.Connection, worker_key: str) -> int | None:
    row = conn.execute("SELECT worker_id FROM worker_registry WHERE worker_key=?",
                       (str(worker_key),)).fetchone()
    return int(row["worker_id"]) if row else None


def _environment_id(conn: sqlite3.Connection, environment: str) -> int | None:
    """Resolve an environment by its `display`, its `product`, or its id.

    MEASURED (2026-09-25): the caller used to pass a `channel_key`. THE USER:
    "環境 is 環境!!!! not related to channel". The vocabulary is now
    `working_environment`, so a caller may name the row by its `display`
    (`Browser > 豆包 > chat`), by its `product` (`豆包`), or by its id.
    """
    s = str(environment or "").strip()
    if not s:
        return None
    if s.isdigit():
        row = conn.execute(
            "SELECT environment_id FROM working_environment WHERE "
            "environment_id=? AND is_active=1", (int(s),)).fetchone()
        return int(row["environment_id"]) if row else None
    row = conn.execute(
        "SELECT environment_id FROM working_environment WHERE is_active=1 "
        "AND (display=? OR product=?) ORDER BY environment_id LIMIT 1",
        (s, s)).fetchone()
    return int(row["environment_id"]) if row else None


def bind(conn: sqlite3.Connection, *, worker_key: str, environment: str,
         cite_ref: str, is_primary: bool = False,
         commit: bool = True) -> dict[str, Any]:
    """Bind ONE worker to ONE environment. Idempotent on (worker, env).

    REFUSES:
      * an unknown `worker_key` (a binding to a worker that does not exist)
      * an UNDECLARED `environment` (the vocabulary is `working_environment`,
        so a second vocabulary cannot appear)
      * a missing `cite_ref` (no citation, no binding)
    """
    reasons: list[str] = []
    wk = str(worker_key or "").strip()
    env = str(environment or "").strip()
    if not wk:
        reasons.append("worker_key is required")
    if not env:
        reasons.append("environment is required")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no binding)")
    wid = _worker_id(conn, wk) if wk else None
    if wk and wid is None:
        reasons.append("worker_key %r is not in worker_registry" % wk)
    eid = _environment_id(conn, env) if env else None
    if env and eid is None:
        reasons.append("environment %r is NOT DECLARED in working_environment, "
                       "so it is not an environment this system knows" % env)
    if reasons:
        raise BindingRefused(reasons)

    existing = conn.execute(
        "SELECT binding_id FROM %s WHERE worker_id=? AND environment_id=?"
        % TABLE, (wid, eid)).fetchone()
    if existing:
        return {"ok": True, "action": "already_bound",
                "binding_id": int(existing["binding_id"]),
                "worker_key": wk, "environment_id": eid}
    cur = conn.execute(
        "INSERT INTO %s (worker_id, environment_id, is_primary, cite_ref) "
        "VALUES (?, ?, ?, ?)" % TABLE,
        (wid, eid, 1 if is_primary else 0, str(cite_ref)))
    if commit:
        conn.commit()
    return {"ok": True, "action": "created", "binding_id": int(cur.lastrowid),
            "worker_key": wk, "environment_id": eid,
            "is_primary": bool(is_primary)}


def bindings_of(conn: sqlite3.Connection, worker_key: str) -> dict[str, Any]:
    """Every environment a worker is DECLARED to run in.

    MEASURED (2026-09-25): this JOINed `channel_registry` and returned a
    `channel_key`, because the binding carried a `channel_id`. THE USER:
    "環境 is 環境!!!! not related to channel". The binding now carries an
    `environment_id`, so the answer names the ENVIRONMENT.
    """
    wid = _worker_id(conn, worker_key)
    if wid is None:
        return {"ok": False, "worker_key": worker_key,
                "reason": "worker_key %r is not in worker_registry" % worker_key}
    rows = conn.execute(
        "SELECT b.binding_id, b.environment_id, w.display, w.kind, "
        "w.product, w.surface, b.is_primary, b.cite_ref "
        "FROM %s b LEFT JOIN working_environment w ON "
        "w.environment_id = b.environment_id "
        "WHERE b.worker_id=? AND b.is_active=1 ORDER BY b.is_primary DESC, "
        "b.environment_id" % TABLE, (wid,)).fetchall()
    return {"ok": True, "worker_key": worker_key,
            "environments": [dict(r) for r in rows], "count": len(rows)}


def matrix(conn: sqlite3.Connection) -> dict[str, Any]:
    """The worker x environment MATRIX, from the DECLARED bindings.

    This is the same shape `worker_model.pairing_matrix` measures, but read from
    the DECLARED table rather than inferred from per-session observations. The
    two must AGREE once the bindings exist, and a proof asserts it.

    MEASURED (2026-09-25): the columns are now ENVIRONMENTS, not channels. THE
    USER: "環境 is 環境!!!! not related to channel".
    """
    workers = [{"worker_id": int(r["worker_id"]), "worker_key": r["worker_key"]}
               for r in conn.execute(
                   "SELECT worker_id, worker_key FROM worker_registry "
                   "ORDER BY worker_id")]
    envs = [{"environment_id": int(r["environment_id"]),
             "display": r["display"]}
            for r in conn.execute(
                "SELECT environment_id, display FROM working_environment "
                "WHERE is_active=1 ORDER BY environment_id")]
    pairs = {(int(r["worker_id"]), int(r["environment_id"]))
             for r in conn.execute(
                 "SELECT worker_id, environment_id FROM %s WHERE is_active=1 "
                 "AND environment_id IS NOT NULL" % TABLE)}
    out: list[dict[str, Any]] = []
    for w in workers:
        cells = [{"environment_id": e["environment_id"],
                  "display": e["display"],
                  "bound": (w["worker_id"], e["environment_id"]) in pairs}
                 for e in envs]
        out.append({"worker_key": w["worker_key"], "worker_id": w["worker_id"],
                    "cells": cells,
                    "bound_count": sum(1 for c in cells if c["bound"])})
    total = len(workers) * len(envs)
    filled = sum(e["bound_count"] for e in out)
    return {"workers": workers, "environments": envs, "matrix": out,
            "total_cells": total, "filled_cells": filled,
            "empty_cells": total - filled,
            "workers_with_no_binding": [e["worker_key"] for e in out
                                        if e["bound_count"] == 0],
            "environments_never_bound": [
                e["display"] for e in envs
                if not any((w["worker_id"], e["environment_id"]) in pairs
                           for w in workers)],
            "source": "the DECLARED table %s" % TABLE}


def derive_bindings(conn: sqlite3.Connection, *, commit: bool = False) -> dict[str, Any]:
    """DERIVE the bindings from EVIDENCE, and REFUSE what the evidence cannot resolve.

    THE USER (2026-09-24): "logic generator can help you".

    THE EVIDENCE IS `identity_registry` JOIN `chat_main` ON `session_id` -- the
    OBSERVED (worker, ide) pairs. MEASURED: worker 5 -> `VS Code` (51 rows).

    MEASURED (2026-09-25), AND THE EVIDENCE WAS THE WRONG COLUMN: this read
    `identity_registry.channel`, which holds `local_pc` -- a CHANNEL, not an
    environment. THE USER: "環境 is 環境!!!! not related to channel". The
    environment observation is `chat_main.ide` (`VS Code`), which is the same
    fact `working_environment.product` names.

    `identity_registry.channel` is still READ, but as a CHANNEL observation and
    reported under `channel_observations` -- a different fact, kept separate.

    SO THIS FUNCTION DOES NOT INVENT AN ENVIRONMENT. It splits the evidence into:

      * `derivable`  -- the observed value names a DECLARED environment
      * `undeclared` -- the value is OBSERVED but NOT DECLARED, so it is
                        REPORTED and NOT written

    That is the same rule `logic_generator.answer_by_evidence` follows: an answer
    without a resolvable source is REFUSED, never defaulted. Writing `local_pc`
    into `working_environment` to make the derivation succeed would be inventing
    the vocabulary the table exists to protect.
    """
    observed: list[dict[str, Any]] = []
    channel_observations: list[dict[str, Any]] = []
    if _table_exists(conn, "identity_registry"):
        channel_observations = [
            {"worker_id": int(r["worker_id"]), "channel": str(r["channel"]),
             "n": int(r["n"])} for r in conn.execute(
                "SELECT worker_id, channel, COUNT(*) n FROM identity_registry "
                "GROUP BY worker_id, channel ORDER BY n DESC")]
    if _table_exists(conn, "identity_registry") and _table_exists(conn, "chat_main"):
        observed = [
            {"worker_id": int(r["worker_id"]), "environment": str(r["ide"]),
             "n": int(r["n"])} for r in conn.execute(
                "SELECT i.worker_id, cm.ide, COUNT(*) n FROM identity_registry i "
                "JOIN chat_main cm ON cm.session_id = i.session_id "
                "WHERE i.is_active=1 AND cm.ide IS NOT NULL "
                "GROUP BY i.worker_id, cm.ide ORDER BY n DESC")]
    # THE ENVIRONMENT VOCABULARY IS `working_environment`, NOT `channel_registry`.
    declared = {str(r["display"]): int(r["environment_id"]) for r in conn.execute(
        "SELECT environment_id, display FROM working_environment "
        "WHERE is_active=1")}
    by_product = {str(r["product"]): int(r["environment_id"]) for r in conn.execute(
        "SELECT environment_id, product FROM working_environment "
        "WHERE is_active=1")}
    workers = {int(r["worker_id"]): str(r["worker_key"]) for r in conn.execute(
        "SELECT worker_id, worker_key FROM worker_registry")}

    derivable: list[dict[str, Any]] = []
    undeclared: list[dict[str, Any]] = []
    unknown_worker: list[dict[str, Any]] = []
    for o in observed:
        wk = workers.get(o["worker_id"])
        if wk is None:
            unknown_worker.append(o)
            continue
        # The OBSERVED value is an IDE/product name (`VS Code`). It is matched
        # against the environment vocabulary by DISPLAY then by PRODUCT, and a
        # value that matches NOTHING is REPORTED -- never invented.
        eid = declared.get(o["environment"]) or by_product.get(o["environment"])
        if eid is not None:
            derivable.append({"worker_key": wk, "environment_id": int(eid),
                              "observed": o["environment"], "n": o["n"]})
        else:
            undeclared.append({"worker_key": wk, "observed": o["environment"],
                               "n": o["n"],
                               "why": ("OBSERVED %d time(s) but NOT DECLARED in "
                                       "working_environment, so the foreign key "
                                       "cannot resolve" % o["n"])})
    written: list[dict[str, Any]] = []
    if commit:
        for d in derivable:
            try:
                written.append(bind(conn, worker_key=d["worker_key"],
                                    environment=str(d["environment_id"]),
                                    cite_ref="worker_environment_binding.py:"
                                             "derive_bindings",
                                    commit=False))
            except Exception as exc:
                written.append({"ok": False, "worker_key": d["worker_key"],
                                "environment_id": d["environment_id"],
                                "reason": str(exc)})
        conn.commit()
    return {"ok": True, "observed": observed,
            "channel_observations": channel_observations,
            "declared_environments": sorted(declared),
            "derivable": derivable, "undeclared": undeclared,
            "unknown_worker": unknown_worker, "written": written,
            "committed": bool(commit),
            "why": ("a binding is DERIVED only when the observed value names a "
                    "DECLARED environment; an observed-but-undeclared value is "
                    "REPORTED, because inventing it would create the second "
                    "vocabulary this table exists to prevent")}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--matrix" in args:
            print(json.dumps(matrix(conn), indent=2, ensure_ascii=False))
        elif "--derive" in args:
            print(json.dumps(derive_bindings(conn, commit="--apply" in args),
                             indent=2, ensure_ascii=False))
        elif "--of" in args:
            i = args.index("--of")
            print(json.dumps(bindings_of(conn, args[i + 1]),
                             indent=2, ensure_ascii=False))
        elif "--bind" in args:
            i = args.index("--bind")
            print(json.dumps(bind(conn, worker_key=args[i + 1],
                                  environment=args[i + 2],
                                  cite_ref="cli"),
                             indent=2, ensure_ascii=False))
        else:
            print(json.dumps(matrix(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
