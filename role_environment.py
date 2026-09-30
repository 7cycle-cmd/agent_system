"""role_environment.py -- the ROLE x ENVIRONMENT register.

THE USER'S QUESTION (2026-09-24, verbatim)
------------------------------------------
    "role X enviornment table is missing?"

MEASURED, AND THE USER IS RIGHT: there was NO role x environment table.
`assignee_selection` exists but it is a PICK SLOT (one active row), not a
MATRIX. So the pairing the user asked for earlier
("role = writer X environment = IDE") had no home.

WHY A PAIR IS A ROW, NOT TWO COLUMNS ON ONE TABLE
-------------------------------------------------
A role may work in SEVERAL environments, and an environment may host SEVERAL
roles. A row per pair is the only shape that expresses many-to-many without a
comma-separated list, which cannot be joined or constrained.

THE PAIR IS REFUSED WHEN EITHER SIDE IS UNKNOWN
-----------------------------------------------
`declare()` REFUSES a role that is not in `role_registry` and an environment that
is not in `working_environment`. A pair naming a role the system does not know
would make every later reader pick the wrong one.

NEVER RAISES ON A READ
----------------------
`list_pairs()` and `pairs_for_environment()` return `ok: False` with a `why`
rather than raising, because a status read that can raise turns a page into an
outage. `declare()` DOES raise, because a caller that ignores a soft failure
would leave the register unchanged while believing it succeeded.
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

SOURCE = "role_environment:role_registry x working_environment"


class PairRefused(Exception):
    """Raised when a pair would name a role or environment the system lacks."""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent.

    MEASURED DEFECT (2026-09-24, in `computer_presence`): a reader that depends
    on the CALLER having set the row factory breaks the moment a new caller
    forgets. The reader sets it itself.

    MEASURED (2026-09-25): the live helper server holds the DB, so a write from
    a proof raised `database is locked`. `busy_timeout` makes the caller WAIT
    for the lock instead of failing -- a condition-based wait, not a sleep.
    """
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `role_environment` if absent. Idempotent."""
    import db_schema

    conn.executescript(db_schema.ROLE_ENVIRONMENT_DDL)
    conn.commit()
    return {"ok": True, "table": "role_environment"}


def roles(conn: sqlite3.Connection) -> list[str]:
    """The role VOCABULARY, READ from `role_registry`. Never typed here."""
    _as_rows(conn)
    return [str(r["role_key"]) for r in conn.execute(
        "SELECT role_key FROM role_registry WHERE is_active=1 ORDER BY role_key")]


def environments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The environment VOCABULARY, READ from `working_environment`."""
    _as_rows(conn)
    return [dict(r) for r in conn.execute(
        "SELECT environment_id, kind, product, surface, display "
        "FROM working_environment WHERE is_active=1 ORDER BY environment_id")]


def declare(conn: sqlite3.Connection, *, role_key: str, environment_id: int,
            cite_ref: str, is_primary: bool = False,
            commit: bool = True) -> dict[str, Any]:
    """Declare ONE role x environment pair. REFUSES an unknown side.

    A REFUSAL is raised, not returned as a soft failure: a caller that ignores a
    soft failure would leave the register unchanged while believing it succeeded.
    """
    _as_rows(conn)
    rk = str(role_key or "").strip()
    if not rk:
        raise PairRefused("role_key is required")
    known_roles = roles(conn)
    if rk not in known_roles:
        raise PairRefused("role_key `%s` is not in `role_registry` "
                          "(known: %s)" % (rk, known_roles))
    try:
        eid = int(environment_id)
    except Exception:
        raise PairRefused("environment_id must be an integer, got %r"
                          % (environment_id,))
    env = conn.execute(
        "SELECT environment_id, display FROM working_environment "
        "WHERE environment_id=? AND is_active=1", (eid,)).fetchone()
    if env is None:
        known = [e["environment_id"] for e in environments(conn)]
        raise PairRefused("environment_id %s is not in `working_environment` "
                          "(known: %s)" % (eid, known))
    row = conn.execute(
        "SELECT pair_id FROM role_environment WHERE role_key=? AND "
        "environment_id=?", (rk, eid)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO role_environment (role_key, environment_id, "
            "is_primary, cite_ref) VALUES (?, ?, ?, ?)",
            (rk, eid, 1 if is_primary else 0, str(cite_ref or "")))
        action = "inserted"
    else:
        conn.execute(
            "UPDATE role_environment SET is_primary=?, cite_ref=?, "
            "is_active=1, updated_at=datetime('now') WHERE pair_id=?",
            (1 if is_primary else 0, str(cite_ref or ""), int(row["pair_id"])))
        action = "updated"
    if commit:
        conn.commit()
    return {"ok": True, "action": action, "role_key": rk,
            "environment_id": eid, "display": env["display"]}


def remove(conn: sqlite3.Connection, *, role_key: str, environment_id: int,
           commit: bool = True) -> dict[str, Any]:
    """SOFT-DELETE one pair (`is_active=0`). The repo law: never DELETE.

    THE USER (2026-09-25): the step-1 popup shows the role detail and lets the
    user UNDO a pair. A hard DELETE would destroy the decision's history, so
    the pair is deactivated, and `declare()` can re-activate the SAME row.
    """
    _as_rows(conn)
    rk = str(role_key or "").strip()
    try:
        eid = int(environment_id)
    except Exception:
        raise PairRefused("environment_id must be an integer, got %r"
                          % (environment_id,))
    row = conn.execute(
        "SELECT pair_id, is_active FROM role_environment WHERE role_key=? "
        "AND environment_id=?", (rk, eid)).fetchone()
    if row is None:
        raise PairRefused("no pair for role %r x environment %s" % (rk, eid))
    if int(row["is_active"]) == 0:
        return {"ok": True, "action": "already_removed", "role_key": rk,
                "environment_id": eid}
    conn.execute(
        "UPDATE role_environment SET is_active=0, updated_at=datetime('now') "
        "WHERE pair_id=?", (int(row["pair_id"]),))
    if commit:
        conn.commit()
    return {"ok": True, "action": "removed", "role_key": rk,
            "environment_id": eid}


def prune_orphan_pairs(conn: sqlite3.Connection, *,
                       commit: bool = False) -> dict[str, Any]:
    """SOFT-DELETE every pair whose ENVIRONMENT is no longer active.

    MEASURED LEAK (2026-09-25, MY OWN): `role_environment` had 6 active pairs
    and 5 of them pointed at PROOF environments (`env 26/28/30/32/34`, all
    `is_active=0`), cited `_proof_env_popup_edit_add_llm.py:1`. My proof
    soft-deleted its ENVIRONMENTS but not its PAIRS, so the pairs stayed
    active and pointed at nothing.

    A pair whose environment is inactive is not a decision any more -- the
    environment it names is gone. The repo law is SOFT DELETE ONLY, so this
    sets `is_active=0` and never deletes.

    A pair whose environment IS active is KEPT, whatever its citation: a
    decision the user made must never be pruned by a cleanup.
    """
    _as_rows(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT p.pair_id, p.role_key, p.environment_id, p.cite_ref, "
        "w.is_active AS env_active, w.display "
        "FROM role_environment p LEFT JOIN working_environment w "
        "ON w.environment_id = p.environment_id "
        "WHERE p.is_active=1 ORDER BY p.pair_id")]
    pruned: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for r in rows:
        if r["env_active"] is not None and int(r["env_active"]) == 1:
            kept.append(r)
            continue
        pruned.append(r)
        if commit:
            conn.execute(
                "UPDATE role_environment SET is_active=0, "
                "updated_at=datetime('now') WHERE pair_id=?",
                (int(r["pair_id"]),))
    if commit:
        conn.commit()
    return {"ok": True, "committed": bool(commit), "kept": kept,
            "pruned": pruned, "kept_count": len(kept),
            "pruned_count": len(pruned)}


def list_pairs(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every declared pair, with the role's rights and the environment's path."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT p.pair_id, p.role_key, p.environment_id, p.is_primary, "
            "p.cite_ref, r.may_read, r.may_write, r.may_verify, "
            "r.definition, w.kind, w.product, w.surface, w.display "
            "FROM role_environment p "
            "LEFT JOIN role_registry r ON r.role_key = p.role_key "
            "AND r.is_active=1 "
            "LEFT JOIN working_environment w ON w.environment_id = "
            "p.environment_id AND w.is_active=1 "
            "WHERE p.is_active=1 ORDER BY p.role_key, p.environment_id")]
    except Exception as exc:
        return {"ok": False, "rows": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    by_role: dict[str, int] = {}
    by_env: dict[int, int] = {}
    for r in rows:
        by_role[str(r["role_key"])] = by_role.get(str(r["role_key"]), 0) + 1
        by_env[int(r["environment_id"])] = by_env.get(
            int(r["environment_id"]), 0) + 1
    return {"ok": True, "rows": rows, "count": len(rows),
            "by_role": by_role, "by_environment": by_env,
            "role_vocabulary": roles(conn),
            "environment_vocabulary": environments(conn),
            "source": SOURCE}


def pairs_for_environment(conn: sqlite3.Connection,
                          environment_id: int) -> dict[str, Any]:
    """The roles declared for ONE environment. Never raises."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT p.role_key, p.is_primary, r.may_read, r.may_write, "
            "r.may_verify, r.definition FROM role_environment p "
            "LEFT JOIN role_registry r ON r.role_key = p.role_key "
            "AND r.is_active=1 "
            "WHERE p.environment_id=? AND p.is_active=1 ORDER BY p.role_key",
            (int(environment_id),))]
    except Exception as exc:
        return {"ok": False, "environment_id": int(environment_id), "roles": [],
                "error": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "environment_id": int(environment_id),
            "roles": rows, "count": len(rows),
            "why": ("%d role(s) declared for this environment" % len(rows))
                   if rows else
                   ("no role is declared for this environment, so the pairing "
                    "is NOT measured")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--env", type=int, default=0)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
            return 0
        if args.env:
            print(json.dumps(pairs_for_environment(conn, args.env), indent=2,
                             ensure_ascii=False))
            return 0
        print(json.dumps(list_pairs(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
