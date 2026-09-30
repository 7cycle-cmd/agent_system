"""identity_role.py -- the ROLE link, so `identity = A + role = worker` holds.

THE USER'S FORMULA (verbatim, 2026-09-24):

    "identity = A + role = worker"
    "is it correct?"

MEASURED — the formula is RIGHT, and the system was missing ONE column:

    A       = `identity_registry.session_id` + `.channel`   -> EXISTS (53 rows)
    role    = `identity_registry.role_id`                   -> WAS MISSING
    = worker= `identity_registry` IS the worker             -> CORRECT (53 rows)

And `role` ALREADY EXISTED as a register — it was simply not connected:

    role_registry: 3 roles
      researcher  read=1 write=0 verify=0   "READS and REPORTS"
      writer      read=1 write=1 verify=0   "PRODUCES a product artifact (code, ...)"
      verifier    read=1 write=0 verify=1   "CHECKS another worker's output"
    role_right_registry: 9 rows = 3 roles x 3 rights

So the ONLY missing link was `identity_registry.role_id`, and this module is the
ONE writer/reader of it.

A ROLE IS A DECISION, NOT A DEFAULT. An identity with no role is REPORTED as
unassigned, because a fabricated role would be the same defect as a fabricated
task id.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "agent.db")

# The THREE parts of the user's formula. Named ONCE.
PARTS = ("A", "role")

# `A` is itself two parts, and they are the two the register already had.
A_PARTS = ("session_id", "channel")


class RoleRefused(RuntimeError):
    """Raised when a role link would be stored without a real role."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("identity_role refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add `role_id` to `identity_registry` if it is absent.

    A MIGRATION, not a re-create: the table already holds 53 rows, so the column
    is ADDED. `ALTER TABLE ... ADD COLUMN` is the only safe form for an existing
    table, and it is idempotent when guarded by a column check.
    """
    cols = [str(r["name"]) for r in conn.execute(
        "PRAGMA table_info(identity_registry)")]
    if "role_id" in cols:
        return {"ok": True, "action": "already_present", "column": "role_id"}
    conn.execute("ALTER TABLE identity_registry ADD COLUMN role_id INTEGER")
    conn.commit()
    return {"ok": True, "action": "added", "column": "role_id"}


def roles(conn: sqlite3.Connection) -> dict[str, Any]:
    """The role VOCABULARY, READ from `role_registry`. Never typed here."""
    rows = conn.execute(
        "SELECT role_key, definition, may_read, may_write, may_verify, "
        "instrument, because FROM role_registry WHERE is_active=1 "
        "ORDER BY role_key").fetchall()
    return {"ok": True, "roles": [dict(r) for r in rows],
            "keys": [str(r["role_key"]) for r in rows], "count": len(rows)}


def _role_id(conn: sqlite3.Connection, role_key: str) -> int | None:
    """The `role_id` for a role key. MEASURED: `role_registry` has NO id column,
    so the ROWID is the id -- it is the table's own auto-increment identity."""
    row = conn.execute(
        "SELECT rowid AS rid FROM role_registry WHERE role_key=? AND is_active=1",
        (str(role_key),)).fetchone()
    return int(row["rid"]) if row else None


def assign_role(conn: sqlite3.Connection, *, identity_id: int, role_key: str,
                cite_ref: str, commit: bool = True) -> dict[str, Any]:
    """Assign ONE role to ONE identity. REFUSES an unknown role or identity.

    THE FORMULA THIS IMPLEMENTS: `identity = A + role`. `A` is already on the
    row; this writes the `role` half.
    """
    reasons: list[str] = []
    rk = str(role_key or "").strip()
    if not rk:
        reasons.append("role_key is required (identity = A + role)")
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no role)")
    rid = _role_id(conn, rk) if rk else None
    if rk and rid is None:
        reasons.append("role_key %r is NOT in role_registry, so it is not a role "
                       "this system knows" % rk)
    row = conn.execute("SELECT identity_id FROM identity_registry WHERE "
                       "identity_id=?", (int(identity_id),)).fetchone()
    if row is None:
        reasons.append("identity_id %r is not in identity_registry" % identity_id)
    if reasons:
        raise RoleRefused(reasons)
    conn.execute("UPDATE identity_registry SET role_id=?, updated_at="
                 "datetime('now') WHERE identity_id=?",
                 (rid, int(identity_id)))
    if commit:
        conn.commit()
    return {"ok": True, "identity_id": int(identity_id), "role_key": rk,
            "role_id": rid, "cite_ref": cite_ref}


def role_of(conn: sqlite3.Connection, identity_id: int) -> dict[str, Any]:
    """The role of ONE identity, or `UNASSIGNED` -- never defaulted."""
    row = conn.execute(
        "SELECT i.identity_id, i.session_id, i.channel, i.role_id, "
        "r.role_key, r.definition, r.may_read, r.may_write, r.may_verify "
        "FROM identity_registry i LEFT JOIN role_registry r "
        "ON r.rowid = i.role_id WHERE i.identity_id=?",
        (int(identity_id),)).fetchone()
    if row is None:
        return {"ok": False, "identity_id": identity_id,
                "reason": "no identity_registry row for this id"}
    d = dict(row)
    if d.get("role_key") is None:
        return {"ok": True, "identity_id": int(identity_id),
                "role_key": "UNASSIGNED", "role_id": d.get("role_id"),
                "why": ("no role is assigned, so the identity is A ONLY -- a role "
                        "is a DECISION and is not defaulted")}
    return {"ok": True, "identity_id": int(identity_id), "role_key": d["role_key"],
            "role_id": d["role_id"], "definition": d["definition"],
            "may_read": d["may_read"], "may_write": d["may_write"],
            "may_verify": d["may_verify"]}


def formula(conn: sqlite3.Connection, identity_id: int) -> dict[str, Any]:
    """The user's formula, EVALUATED for one identity.

        identity = A + role = worker

    EVERY part is REQUIRED. A missing part is REFUSED, because a default would
    make two different workers look the same -- the defect the user named.
    """
    row = conn.execute(
        "SELECT identity_id, session_id, channel, role_id FROM identity_registry "
        "WHERE identity_id=?", (int(identity_id),)).fetchone()
    if row is None:
        return {"ok": False, "identity_id": identity_id,
                "reason": "no identity_registry row for this id"}
    d = dict(row)
    reasons: list[str] = []
    a: dict[str, Any] = {}
    for p in A_PARTS:
        v = str(d.get(p) or "").strip()
        if not v or v.upper() == "NA":
            reasons.append("A.%s is required (A = session + environment)" % p)
        a[p] = v
    role = role_of(conn, identity_id)
    if role.get("role_key") == "UNASSIGNED":
        reasons.append("role is required (identity = A + role)")
    if reasons:
        return {"ok": False, "identity_id": int(identity_id), "A": a,
                "role": role.get("role_key"), "reasons": reasons,
                "why": "the formula is INCOMPLETE, so this is not yet a worker"}
    return {"ok": True, "identity_id": int(identity_id), "A": a,
            "role": role["role_key"], "worker": int(identity_id),
            "display": "A(%s, %s) + role(%s) = worker(%d)"
                       % (a["session_id"][:8], a["channel"], role["role_key"],
                          int(identity_id)),
            "why": "identity = A + role = worker"}


def audit(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many identities have a role, and which do not. The measured gap."""
    rows = conn.execute(
        "SELECT i.identity_id, i.role_id, r.role_key FROM identity_registry i "
        "LEFT JOIN role_registry r ON r.rowid = i.role_id "
        "WHERE i.is_active=1").fetchall()
    if not rows:
        return {"ok": False, "reason": "identity_registry is EMPTY, so the role "
                                       "gap cannot be measured"}
    by_role: dict[str, int] = {}
    for r in rows:
        k = str(r["role_key"] or "UNASSIGNED")
        by_role[k] = by_role.get(k, 0) + 1
    return {"ok": True, "rows": len(rows), "by_role": by_role,
            "assigned": sum(1 for r in rows if r["role_key"]),
            "unassigned": sum(1 for r in rows if not r["role_key"]),
            "vocabulary": roles(conn)["keys"],
            "why": ("an identity with NO role is A ONLY; the role is a DECISION "
                    "and is REPORTED, not defaulted")}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    conn = _connect()
    try:
        if "--ensure" in args:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
        elif "--roles" in args:
            print(json.dumps(roles(conn), indent=2, ensure_ascii=False))
        elif "--audit" in args:
            print(json.dumps(audit(conn), indent=2, ensure_ascii=False))
        elif "--formula" in args:
            i = args.index("--formula")
            print(json.dumps(formula(conn, int(args[i + 1])), indent=2,
                             ensure_ascii=False))
        else:
            print(json.dumps(audit(conn), indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
