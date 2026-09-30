# -*- coding: utf-8 -*-
"""
capability_binding.py — the DECLARATION surface for a parent that code cannot prove.

The measured blocker
--------------------
`api_registry.capability_id` and `function_registry.capability_id` are NOT NULL,
so every api/function row needs a capability parent. Code cannot supply it:

    a route decorator proves the ROUTE exists, and the file proves the MODULE,
    but nothing in the code says WHICH capability the route serves.

Measured, that is not a gap that more scanning closes:
  * `mouse_spot_helper` has 10 capabilities, `task_center` has 4
  * no module has exactly one, so the parent can never be derived uniquely
  * the chain CAN show a capability, but only by following an ALREADY REGISTERED
    api's own `capability_id` back up -- which is circular for a NEW row

An earlier plan was to "build capability <-> route from real route literals".
Measurement refuted it: a route literal proves existence, not membership. So the
link has to be DECLARED, and this module is that declaration surface.

What makes a declaration acceptable
-----------------------------------
Not preference, not naming similarity. A declaration must carry:
  * `cite_ref` -- a path:line, a command, or register:table:pk, validated by
    `citation_discipline` AND, for a register ref, re-queried so the row is
    confirmed to exist
  * optionally `evidence_id` -- a real `evidence/<EVID>` directory
  * a `status`, so a proposal is not automatically a fact

`parent_for()` returns a capability ONLY when a CONFIRMED binding exists. An
absent binding returns None, and the caller must SKIP -- it must not invent a
parent, because a made-up parent makes the FK graph lie while looking complete.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"
EVIDENCE_ROOT = BASE_DIR / "evidence"

# What a binding can be ABOUT. Deliberately small and syntactic: each kind names
# something a citation can point at.
#
# 'namespace' was added 2026-09-21 (layer B). Measured reason: 49 API namespaces
# vs 15 capabilities, and 46 of those namespaces are dominated by ONE file, so a
# `file` binding cannot discriminate them. A namespace is the structural unit
# that CAN be proven (from its route literals) and then bound once, instead of
# binding every route under it.
SUBJECT_KINDS = ("route", "file", "module", "function", "namespace")
STATUSES = ("DECLARED", "CONFIRMED", "REJECTED")

DDL = """
CREATE TABLE IF NOT EXISTS capability_binding (
    binding_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id INTEGER NOT NULL,
    subject_kind TEXT    NOT NULL
                 CHECK (subject_kind IN ('route','file','module','function','namespace')),
    subject_ref  TEXT    NOT NULL,
    cite_ref     TEXT    NOT NULL,
    evidence_id  TEXT,
    declared_by  TEXT,
    status       TEXT    NOT NULL DEFAULT 'DECLARED'
                 CHECK (status IN ('DECLARED','CONFIRMED','REJECTED')),
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (capability_id, subject_kind, subject_ref),
    FOREIGN KEY (capability_id) REFERENCES capability_registry (capability_id)
);
CREATE INDEX IF NOT EXISTS idx_capability_binding_subject
    ON capability_binding (subject_kind, subject_ref, status);
"""

# SQLite cannot ALTER a CHECK constraint, so widening SUBJECT_KINDS requires a
# table rebuild. This DDL preserves binding_id (and therefore every FK that
# points at it) rather than renumbering.
#
# NOTE the index is created here, with its FINAL name, NOT in a later statement:
# the first version of this migration did `CREATE INDEX IF NOT EXISTS
# idx_capability_binding_subject` where `IF NOT EXISTS` matched the OLD index
# that still existed on the old table, so the new table ended up with NO index
# at all and nothing reported it.
_REBUILD_DDL = """
CREATE TABLE capability_binding_new (
    binding_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id INTEGER NOT NULL,
    subject_kind TEXT    NOT NULL
                 CHECK (subject_kind IN ('route','file','module','function','namespace')),
    subject_ref  TEXT    NOT NULL,
    cite_ref     TEXT    NOT NULL,
    evidence_id  TEXT,
    declared_by  TEXT,
    status       TEXT    NOT NULL DEFAULT 'DECLARED'
                 CHECK (status IN ('DECLARED','CONFIRMED','REJECTED')),
    note         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (capability_id, subject_kind, subject_ref),
    FOREIGN KEY (capability_id) REFERENCES capability_registry (capability_id)
);
"""


class UncitedBinding(RuntimeError):
    """A declaration whose cite_ref is not a checkable reference."""


class UnknownEvidence(RuntimeError):
    """A declaration naming an evidence directory that does not exist."""


def log(msg: str) -> None:
    print("[capability_binding] %s" % msg, flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the table, and WIDEN an existing CHECK if it predates `namespace`.

    `CREATE TABLE IF NOT EXISTS` does nothing to a table that already exists, so
    an existing DB would keep the OLD 4-kind CHECK and reject every
    `subject_kind='namespace'` write with an IntegrityError. That failure would
    look like "the binding was refused by a gate" when it is really "the schema
    is a version behind" — so the migration is detected and applied here, and
    what it did is RETURNED rather than done silently.
    """
    conn.executescript(DDL)
    rebuilt = _widen_subject_kinds_if_needed(conn)
    conn.commit()
    return {"ok": True, "table": "capability_binding", **rebuilt}


def _table_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='capability_binding'").fetchone()
    return (row[0] if row and row[0] else "") if row else ""


def _widen_subject_kinds_if_needed(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild the table when its CHECK predates `namespace`.

    Preserves `binding_id` values on purpose: if anything ever points at a
    binding, renumbering would silently re-target it. UNIQUE and the FK are
    re-declared in the new table.
    """
    sql = _table_sql(conn)
    if not sql:
        return {"rebuilt": False, "why": "table absent"}
    if "namespace" in sql:
        return {"rebuilt": False, "why": "already widened"}
    n_before = conn.execute(
        "SELECT COUNT(*) FROM capability_binding").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(_REBUILD_DDL)
        conn.execute(
            "INSERT INTO capability_binding_new "
            "(binding_id, capability_id, subject_kind, subject_ref, cite_ref, "
            " evidence_id, declared_by, status, note, created_at, updated_at) "
            "SELECT binding_id, capability_id, subject_kind, subject_ref, "
            "cite_ref, evidence_id, declared_by, status, note, created_at, "
            "updated_at FROM capability_binding")
        n_moved = conn.execute(
            "SELECT COUNT(*) FROM capability_binding_new").fetchone()[0]
        # a copy that loses a row must FAIL, not proceed
        if n_moved != n_before:
            raise sqlite3.IntegrityError(
                "rebuild would lose rows: %d -> %d" % (n_before, n_moved))
        conn.execute("DROP TABLE capability_binding")
        conn.execute("ALTER TABLE capability_binding_new "
                     "RENAME TO capability_binding")
        # DROP TABLE removed the old index WITH the old table, so this is a true
        # create. Verified after the migration; see `_indexes()`.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_capability_binding_subject "
            "ON capability_binding (subject_kind, subject_ref, status)")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    idx = _indexes(conn)
    return {"rebuilt": True, "rows_preserved": n_before,
            "indexes": idx, "index_restored": bool(idx),
            "why": "CHECK widened to include 'namespace'"}


def _indexes(conn: sqlite3.Connection) -> list[str]:
    """Indexes on capability_binding. Reported so a lost index is VISIBLE."""
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='capability_binding' "
            "AND name NOT LIKE 'sqlite_autoindex%'")]
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------------------
# gates
# ---------------------------------------------------------------------------


def _assert_cited(cite_ref: str, conn: sqlite3.Connection) -> None:
    """The citation must be checkable, and a register ref must RESOLVE."""
    try:
        import citation_discipline as cd
    except Exception:
        if not cite_ref or ":" not in cite_ref:
            raise UncitedBinding("cite_ref %r is not a reference" % cite_ref)
        return
    cd.assert_cited({"evidence_ref": cite_ref})
    if cd.parse_db_ref(cite_ref):
        res = cd.verify_db_ref(cite_ref, conn=conn)
        if not res.get("exists"):
            raise UncitedBinding("cite_ref %r does not resolve: %s"
                                 % (cite_ref, res.get("why")))


def _assert_evidence(evidence_id: str | None) -> None:
    if not evidence_id:
        return
    if not (EVIDENCE_ROOT / str(evidence_id)).is_dir():
        raise UnknownEvidence("evidence_id %r has no directory under %s"
                              % (evidence_id, EVIDENCE_ROOT.name))


# ---------------------------------------------------------------------------
# declare / confirm
# ---------------------------------------------------------------------------


def declare(conn: sqlite3.Connection, *, capability_key: str,
            subject_kind: str, subject_ref: str, cite_ref: str,
            evidence_id: str | None = None, declared_by: str | None = None,
            note: str | None = None) -> dict[str, Any]:
    """Record a DECLARED binding. It is a proposal until confirmed."""
    ensure_schema(conn)
    kind = str(subject_kind or "").strip().lower()
    if kind not in SUBJECT_KINDS:
        return {"ok": False, "why": "subject_kind must be one of %s"
                                    % (SUBJECT_KINDS,)}
    cap = conn.execute(
        "SELECT capability_id FROM capability_registry WHERE capability_key = ? "
        "AND is_active = 1", (str(capability_key).strip(),)).fetchone()
    if not cap:
        return {"ok": False, "why": "no active capability %r" % capability_key}
    ref = str(subject_ref or "").strip()
    if not ref:
        return {"ok": False, "why": "subject_ref is required"}
    try:
        _assert_cited(cite_ref, conn)
        _assert_evidence(evidence_id)
    except (UncitedBinding, UnknownEvidence) as e:
        return {"ok": False, "gate": "citation", "why": str(e)}
    except Exception as e:
        return {"ok": False, "gate": "citation",
                "why": "%s: %s" % (type(e).__name__, e)}

    now = _utc_now()
    row = conn.execute(
        "SELECT binding_id, status FROM capability_binding WHERE capability_id = ? "
        "AND subject_kind = ? AND subject_ref = ?",
        (cap["capability_id"], kind, ref)).fetchone()
    if row:
        # A REJECTED binding is not silently revived by re-declaring it: the
        # status is preserved so a rejection cannot be undone without a decision.
        conn.execute(
            "UPDATE capability_binding SET cite_ref = ?, evidence_id = ?, "
            "declared_by = ?, note = ?, updated_at = ? WHERE binding_id = ?",
            (cite_ref, evidence_id, declared_by, note, now, row["binding_id"]))
        conn.commit()
        return {"ok": True, "created": False, "binding_id": row["binding_id"],
                "status": row["status"],
                "note": "kept the existing status %r" % row["status"]}
    cur = conn.execute(
        "INSERT INTO capability_binding (capability_id, subject_kind, subject_ref, "
        "cite_ref, evidence_id, declared_by, status, note, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'DECLARED', ?, ?)",
        (cap["capability_id"], kind, ref, cite_ref, evidence_id, declared_by,
         note, now))
    conn.commit()
    return {"ok": True, "created": True, "binding_id": cur.lastrowid,
            "status": "DECLARED"}


def decide(conn: sqlite3.Connection, binding_id: int, *,
           status: str, decided_by: str | None = None) -> dict[str, Any]:
    ensure_schema(conn)
    st = str(status or "").strip().upper()
    if st not in STATUSES:
        return {"ok": False, "why": "status must be one of %s" % (STATUSES,)}
    row = conn.execute("SELECT binding_id FROM capability_binding "
                       "WHERE binding_id = ?", (int(binding_id),)).fetchone()
    if not row:
        return {"ok": False, "why": "no binding %s" % binding_id}
    conn.execute("UPDATE capability_binding SET status = ?, updated_at = ? "
                 "WHERE binding_id = ?", (st, _utc_now(), int(binding_id)))
    conn.commit()
    return {"ok": True, "binding_id": int(binding_id), "status": st,
            "decided_by": decided_by}


def parent_for(conn: sqlite3.Connection, *, subject_kind: str,
               subject_ref: str) -> dict | None:
    """The CONFIRMED capability for a subject, or None.

    Only CONFIRMED counts. A DECLARED proposal is not a parent yet, and returning
    it would let an unconfirmed guess propagate into the FK graph.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT b.binding_id, b.capability_id, c.capability_key, b.cite_ref "
        "FROM capability_binding b "
        "JOIN capability_registry c ON c.capability_id = b.capability_id "
        "WHERE b.subject_kind = ? AND b.subject_ref = ? "
        "AND b.status = 'CONFIRMED' AND c.is_active = 1 "
        "ORDER BY b.binding_id LIMIT 1",
        (str(subject_kind).strip().lower(), str(subject_ref).strip())).fetchone()
    return dict(row) if row else None


def resolve_parent(conn: sqlite3.Connection, *, file_path: str | None = None,
                   route: str | None = None) -> dict[str, Any]:
    """Try the narrowest declared subject first: route, then file.

    A route binding is more specific than a file binding, so it wins. If neither
    is confirmed, the answer is None and the caller must SKIP.
    """
    if route:
        p = parent_for(conn, subject_kind="route", subject_ref=route)
        if p:
            return {"ok": True, "capability_id": p["capability_id"],
                    "capability_key": p["capability_key"],
                    "via": "route", "cite_ref": p["cite_ref"]}
    if file_path:
        p = parent_for(conn, subject_kind="file", subject_ref=file_path)
        if p:
            return {"ok": True, "capability_id": p["capability_id"],
                    "capability_key": p["capability_key"],
                    "via": "file", "cite_ref": p["cite_ref"]}
    return {"ok": False, "why": "no CONFIRMED binding for route=%r file=%r"
                                % (route, file_path)}


def report(conn: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT b.status, b.subject_kind, COUNT(*) AS n "
        "FROM capability_binding b GROUP BY b.status, b.subject_kind "
        "ORDER BY b.status, b.subject_kind")]
    by_status: dict[str, int] = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
    return {"ok": True, "rows": rows, "by_status": by_status,
            "total": sum(by_status.values())}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import json
    ap = argparse.ArgumentParser()
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect()
    try:
        if args.ensure:
            ensure_schema(conn)
        r = report(conn)
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        else:
            print("capability_binding: total=%d" % r["total"])
            for k, v in sorted(r["by_status"].items()):
                print("   %-10s %d" % (k, v))
            for row in r["rows"]:
                print("   %-10s %-9s %d"
                      % (row["status"], row["subject_kind"], row["n"]))
    finally:
        conn.close()


if __name__ == "__main__":
    main()