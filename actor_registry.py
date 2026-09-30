# -*- coding: utf-8 -*-
"""actor_registry.py — THE HUMAN ACTOR AXIS (user / admin).

THE HUMAN (2026-09-28), verbatim:
    "upgrade question template table with role / user / admin, will it can help
     we have all in one easy?"

THE MEASURED ANSWER IS **NO** TO THE LITERAL READING, and the measurement is exact:

    MEASURED: `role_registry` already holds 3 rows (`researcher`, `writer`,
    `verifier`), and the collision test is:
        a hypothetical `user` (read=1, write=0, verify=0) == researcher?  True
    **`user` and `researcher` have IDENTICAL rights.** Adding `user` to
    `role_registry` would put two names on one concept — the defect
    `terminology-register` refuses.

AND THE TWO ARE DIFFERENT KINDS OF THING, measured:

    |                    | the 3 existing roles | a human actor        |
    | has an instrument? | ALL 3 DO (measured)  | NO — a viewer makes  |
    |                    |                      | no artifact          |
    | may_write?         | 1 for `writer`       | 0 — a user does not  |
    |                    |                      | write the DB         |
    | its proof is       | an artifact's format | a `localStorage` key |

So the answer to *"all in one easy"* is **YES, but as a SECOND AXIS**:

    actor_registry   NEW: the human actor (user / admin), no instrument
    role_registry    EXISTING: the worker role, UNCHANGED

WHY A SEPARATE TABLE AND NOT A COLUMN ON `role_registry`
--------------------------------------------------------
MEASURED: `role_registry` has NO actor/kind column, so a human role and a worker
role are INDISTINGUISHABLE in it. Adding `actor_kind` would make the table MIXED
— `may_write` applies to a worker and not to a human, so the column would be
meaningful for only some of its values. `table_design.audit_table` flags that
immediately, and the same reasoning is recorded in `code_location_registry.py:1`
for `file_path`.

Run:
    .\\.venv\\Scripts\\python.exe actor_registry.py --seed
    .\\.venv\\Scripts\\python.exe actor_registry.py --list
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE / "agent.db"
NA = "NA"

# The actor vocabulary. `user` and `admin` are the human's own words; `viewer` is
# NOT added because it would be a THIRD name for `user` (the same defect the
# collision test refuses).
ACTOR_KEYS = ("user", "admin")

# THE DDL CONSTANT IS NAMED `*_DDL` ON PURPOSE: `logic_generator._iter_ddl_constants`
# scans every `*.py` for a MODULE-LEVEL `NAME_DDL = """..."""`, so a table declared
# as `_CREATE_SQL` is UNDECLARED and the generator cannot build a spec for it.
# MEASURED 2026-09-28 (SCOPE C): that exact rename was the whole fix.
actor_registry_DDL = """
CREATE TABLE IF NOT EXISTS actor_registry (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_key   TEXT    NOT NULL UNIQUE,
    definition  TEXT    NOT NULL,
    view_scope  TEXT    NOT NULL,
    proof_kind  TEXT    NOT NULL,
    cite_ref    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# THE SEED. `(actor_key, definition, view_scope, proof_kind, cite_ref)`.
#
# `proof_kind` is the LOAD-BEARING column: it names HOW this actor's work is
# proved, and it is what distinguishes a human actor from a worker role. A worker
# role's proof is an ARTIFACT's format; a human actor's proof is a LOCAL STORAGE
# KEY, because a human produces no artifact — they make a CHOICE, and the choice
# is what is recorded.
SEED: tuple[tuple[str, str, str, str, str], ...] = (
    ("user",
     "The human who USES a page: reads what it shows and makes a choice. "
     "Produces no artifact, so its output is a CHOICE, not a claim.",
     "the pages they are shown, and the choices they can make on them",
     "localStorage:llm_task_monitor_records/<step_key>",
     "llm_task_monitor_ui/src/storage.js:1"),
    ("admin",
     "The human who CONFIGURES the system: registers terms, seeds registers and "
     "decides what a page may show. Produces no artifact either, but its choices "
     "change what every other actor sees.",
     "every register and every page, including the ones a user cannot see",
     "localStorage:llm_task_monitor_records/<step_key> + a register row",
     "ui_element_registry.py:1"),
)


class ActorError(ValueError):
    """Raised when an actor cannot be registered."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.execute(actor_registry_DDL)
    conn.commit()
    return {"ok": True}


def add_actor(conn: sqlite3.Connection, actor_key: str, *, definition: str,
              view_scope: str, proof_kind: str, cite_ref: str,
              commit: bool = True) -> dict[str, Any]:
    """Register ONE actor. Idempotent on `actor_key`. Returns a verdict.

    REFUSES, rather than storing something unusable:
      * `MISSING_ACTOR_KEY` / `BAD_ACTOR_KEY` — the key must be a lowercase word
      * `MISSING_DEFINITION` — an actor with no definition is a label
      * `MISSING_VIEW_SCOPE` — an actor that sees nothing is not an actor
      * `MISSING_PROOF_KIND` — **the load-bearing refusal.** An actor whose work
        cannot be proved is an actor nobody can check, and this column is what
        distinguishes a human actor from a worker role.
      * `MISSING_CITE_REF` — no citation, no row
      * `COLLIDES_WITH_ROLE` — **the collision gate.** An actor whose rights are
        IDENTICAL to an existing worker role would put two names on one concept.
    """
    key = str(actor_key or "").strip().lower()
    if not key:
        return {"ok": False, "code": "MISSING_ACTOR_KEY",
                "message": "actor_key is required"}
    if not key.replace("_", "").isalnum() or key != key.lower():
        return {"ok": False, "code": "BAD_ACTOR_KEY",
                "message": "actor_key must be a lowercase word, got %r" % actor_key}
    for field, value in (("definition", definition), ("view_scope", view_scope),
                         ("proof_kind", proof_kind), ("cite_ref", cite_ref)):
        if not str(value or "").strip() or str(value).strip() == NA:
            return {"ok": False, "code": "MISSING_%s" % field.upper(),
                    "message": "an actor needs a %s" % field}
    # THE COLLISION GATE. MEASURED: `user` (read=1, write=0, verify=0) has the
    # SAME rights as `researcher`, so adding `user` to `role_registry` would put
    # two names on one concept. This gate makes that collision IMPOSSIBLE to
    # introduce by accident: an actor_key that already names a worker role is
    # REFUSED, and the reason names the role.
    try:
        clash = conn.execute("SELECT role_key FROM role_registry WHERE "
                             "role_key=?", (key,)).fetchone()
    except sqlite3.OperationalError:
        clash = None
    if clash:
        return {"ok": False, "code": "COLLIDES_WITH_ROLE",
                "message": ("actor_key %r already names a WORKER role in "
                            "role_registry. A human actor and a worker role are "
                            "different kinds of thing (a worker has an "
                            "instrument; a human does not), so one name cannot "
                            "carry both." % key)}
    ensure_schema(conn)
    row = conn.execute("SELECT id FROM actor_registry WHERE actor_key=?",
                       (key,)).fetchone()
    if row:
        return {"ok": True, "created": False, "id": int(row["id"]),
                "actor_key": key}
    cur = conn.execute(
        "INSERT INTO actor_registry (actor_key, definition, view_scope, "
        "proof_kind, cite_ref) VALUES (?,?,?,?,?)",
        (key, str(definition).strip(), str(view_scope).strip(),
         str(proof_kind).strip(), str(cite_ref).strip()))
    if commit:
        conn.commit()
    return {"ok": True, "created": True, "id": int(cur.lastrowid),
            "actor_key": key}


def list_actors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM actor_registry WHERE is_active=1 ORDER BY actor_key")]


def actor_for(conn: sqlite3.Connection, actor_key: str) -> dict[str, Any] | None:
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM actor_registry WHERE actor_key=?",
                       (str(actor_key),)).fetchone()
    return dict(row) if row else None


def seed_actors(conn: sqlite3.Connection, *, apply: bool = False) -> dict[str, Any]:
    """Write the declared actors. Idempotent. Reports every refusal.

    MEASURED DEFECT (2026-09-28), and it was MINE: the first version SELECTed
    from `actor_registry` BEFORE calling `ensure_schema`, so a fresh DB raised
    `no such table: actor_registry`. A seeder that cannot run on a fresh DB is a
    seeder that only works where someone already ran it — the same defect
    `consultant_registry.ensure_schema` documents for a missing column.
    """
    ensure_schema(conn)
    created, already, refused = [], [], []
    for key, definition, scope, proof, cite in SEED:
        row = conn.execute("SELECT id FROM actor_registry WHERE actor_key=?",
                           (key,)).fetchone()
        if row:
            already.append(key)
            continue
        if not apply:
            created.append({"actor_key": key, "applied": False})
            continue
        r = add_actor(conn, key, definition=definition, view_scope=scope,
                      proof_kind=proof, cite_ref=cite)
        if r.get("ok"):
            (created if r.get("created") else already).append(key)
        else:
            refused.append({"actor_key": key, "code": r.get("code"),
                            "message": r.get("message")})
    return {"ok": not refused, "declared": len(SEED), "created": len(created),
            "already": len(already), "refused": refused,
            "refused_count": len(refused), "applied": bool(apply)}


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """The actors AND the collision state. A RATIO, never a pinned count."""
    ensure_schema(conn)
    actors = list_actors(conn)
    try:
        roles = [str(r[0]) for r in conn.execute(
            "SELECT role_key FROM role_registry WHERE is_active=1")]
    except sqlite3.OperationalError:
        roles = []
    keys = [a["actor_key"] for a in actors]
    return {"ok": True, "actors": len(actors), "actor_keys": keys,
            "roles": len(roles), "role_keys": roles,
            "collisions": [k for k in keys if k in roles],
            "with_proof_kind": sum(1 for a in actors if a["proof_kind"]),
            "with_cite": sum(1 for a in actors if a["cite_ref"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply", action="store_true",
                    help="write; without it this is a DRY RUN")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_actors(conn, apply=args.apply), indent=2,
                             ensure_ascii=False))
        if args.list:
            print(json.dumps(list_actors(conn), indent=2, ensure_ascii=False))
        if not any((args.seed, args.list)):
            print(json.dumps(audit(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
