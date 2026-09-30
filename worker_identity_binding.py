# -*- coding: utf-8 -*-
"""worker_identity_binding.py — THE JOIN IS 5W1H, not a column.

WHY THIS EXISTS (user, 2026-09-23)
---------------------------------
    "and API connect by 5W1H"
    "identity is 5W1H, who = session ID + worker ID required
     what = get workflow id"

The WORKER system and the IDENTITY system are SEPARATE (`worker_registry.py`,
`identity_registry.py`). Neither holds a plain FK to the other, because a single
FK would answer ONE question ("which identity") and leave the other five
unanswerable. This table answers all six, PER PAIR.

THE SIX DIMENSIONS ARE FIXED, THE WORDING IS DATA
-------------------------------------------------
  * the six NAMES are imported from `skill_5w1h.DIMENSION_NAMES`
    (`skill_5w1h.py:51-81`) — a 7th cannot be introduced by an INSERT
  * the WORDING is read from `dimension_binding_registry` via `bindings_for()`
    (`dimension_binding_registry.py:329`) — this module contains NO question list

A hardcoded question list here would be a SECOND copy of the six dimensions, and
`skill_5w1h.py`'s own "Not to do" forbids that. The repo has already measured
what drift costs (46 `.skill.md` files vs 28 ssot rows: 12 disagreed).

`who` IS COMPOSITE, AND THAT IS MODELLED
----------------------------------------
The user said "who = session ID + worker ID required". So the `who` binding's
text names BOTH, and the two ids live on `identity_registry`. Flattening `who`
into one id would lose half of what the user stated.

AN UNBOUND DIMENSION IS REPORTED, NEVER INVENTED
------------------------------------------------
A kind with no binding returns `bound: False` with an EMPTY question. A generic
question would be a hardcode wearing a disguise — the same rule `ticket_5w1h.py`
already applies.

CLI
---
    python worker_identity_binding.py --questions <worker_key> <identity_key>
    python worker_identity_binding.py --coverage
"""
from __future__ import annotations

import argparse
import json
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

# The SIX names, IMPORTED. Never restated here.
from skill_5w1h import DIMENSION_NAMES  # noqa: E402

# The subject kind whose bindings describe a WORKER<->IDENTITY pair. It is a
# kind in the OPEN set (`subject_kind_registry`), so it is a row, not a CHECK.
PAIR_KIND = "worker_identity"


class BindingRefused(RuntimeError):
    """Raised when a binding would be stored without a real dimension or cite."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("worker_identity_binding refused — not written: %s"
                         % "; ".join(self.reasons))


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `worker_identity_binding` and register it in `db_table_registry`."""
    import db_schema as ds

    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(ds.WORKER_IDENTITY_BINDING_DDL)
    # Best-effort taxonomy row: a throwaway DB (a proof) has no
    # `db_table_registry`, and its absence must not stop the table existing.
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            ("worker_identity_binding", "worker_identity_binding",
             "the 5W1H join between the WORKER system and the IDENTITY system"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": "worker_identity_binding"}


def bind(conn: sqlite3.Connection, *, worker_key: str, identity_key: str,
         dimension_key: str, binding_text: str = "",
         cite_ref: str = "") -> dict[str, Any]:
    """Bind ONE dimension of a (worker, identity) pair.

    `dimension_key` is validated against `skill_5w1h.DIMENSION_NAMES` — the six
    are FIXED, so a 7th is refused rather than stored.

    `binding_text` defaults to the wording in `dimension_binding_registry` for
    the pair kind, so the caller does not have to restate it (and cannot drift
    from it). An explicit value is honoured, because a specific pair may need a
    more precise wording than the kind's default.
    """
    import worker_registry as wr
    import identity_registry as ir

    reasons: list[str] = []
    dim = str(dimension_key or "").strip()
    if dim not in DIMENSION_NAMES:
        reasons.append("dimension_key %r is not one of the six fixed dimensions "
                       "%s" % (dim, list(DIMENSION_NAMES)))
    if not str(cite_ref or "").strip():
        reasons.append("cite_ref is required (no citation, no finding)")
    if reasons:
        raise BindingRefused(reasons)

    ensure_schema(conn)
    wr.ensure_schema(conn)
    ir.ensure_schema(conn)

    w = conn.execute("SELECT worker_id FROM worker_registry WHERE worker_key = ?",
                     (str(worker_key or "").strip(),)).fetchone()
    if not w:
        raise BindingRefused(["worker_key %r is not registered" % worker_key])
    i = conn.execute("SELECT identity_id FROM identity_registry WHERE "
                     "identity_key = ?",
                     (str(identity_key or "").strip(),)).fetchone()
    if not i:
        raise BindingRefused(["identity_key %r is not registered" % identity_key])

    text = str(binding_text or "").strip()
    if not text:
        # Read the wording from the register instead of restating it.
        import dimension_binding_registry as dbr
        text = str(dbr.bindings_for(conn, PAIR_KIND).get(dim) or "").strip()
    if not text:
        raise BindingRefused(
            ["no binding_text given and %r has no wording for dimension %r in "
             "dimension_binding_registry — an unbound dimension is REPORTED, "
             "never given a generic question" % (PAIR_KIND, dim)])

    wid, iid = int(w["worker_id"]), int(i["identity_id"])
    existing = conn.execute(
        "SELECT * FROM worker_identity_binding WHERE worker_id=? AND "
        "identity_id=? AND dimension_key=?", (wid, iid, dim)).fetchone()
    if existing:
        return {"ok": True, "created": False, "binding": dict(existing)}

    cur = conn.execute(
        "INSERT INTO worker_identity_binding "
        "(worker_id, identity_id, dimension_key, binding_text, cite_ref) "
        "VALUES (?, ?, ?, ?, ?)",
        (wid, iid, dim, text, str(cite_ref).strip()))
    conn.commit()
    row = conn.execute("SELECT * FROM worker_identity_binding WHERE binding_id=?",
                       (int(cur.lastrowid),)).fetchone()
    return {"ok": True, "created": True, "binding": dict(row)}


def questions_for(conn: sqlite3.Connection, worker_key: str,
                  identity_key: str) -> dict[str, Any]:
    """The 5W1H join for ONE pair: all six dimensions, each with its source.

    Returns every dimension, ALWAYS six entries. A dimension with no binding is
    `bound: False` with an EMPTY question — reported, never invented.
    """
    import dimension_binding_registry as dbr

    ensure_schema(conn)
    w = conn.execute("SELECT worker_id, worker_key FROM worker_registry WHERE "
                     "worker_key = ?",
                     (str(worker_key or "").strip(),)).fetchone()
    i = conn.execute("SELECT identity_id, identity_key, session_id, worker_id, "
                     "workflow_id, chat_id FROM identity_registry WHERE "
                     "identity_key = ?",
                     (str(identity_key or "").strip(),)).fetchone()
    if not w or not i:
        return {"ok": False,
                "error": "worker_key %r or identity_key %r is not registered"
                         % (worker_key, identity_key)}

    kind_wording = dbr.bindings_for(conn, PAIR_KIND)
    rows = {str(r["dimension_key"]): dict(r) for r in conn.execute(
        "SELECT * FROM worker_identity_binding WHERE worker_id=? AND "
        "identity_id=? AND is_active=1", (int(w["worker_id"]),
                                          int(i["identity_id"])))}

    dims: list[dict[str, Any]] = []
    for name in DIMENSION_NAMES:
        r = rows.get(name)
        if r:
            dims.append({
                "dimension_key": name,
                "bound": True,
                "binding_text": str(r["binding_text"]),
                "source": "worker_identity_binding",
                "cite_ref": str(r["cite_ref"]),
            })
        else:
            dims.append({
                "dimension_key": name,
                "bound": False,
                "binding_text": "",
                "source": "UNBOUND",
                "kind_wording": str(kind_wording.get(name) or ""),
                "cite_ref": "",
            })

    return {
        "ok": True,
        "worker_key": str(w["worker_key"]),
        "identity_key": str(i["identity_key"]),
        # `who` is COMPOSITE — both ids, because the user said both are required.
        "who": {"session_id": str(i["session_id"]),
                "worker_id": int(i["worker_id"])},
        "what": {"workflow_id": int(i["workflow_id"])},
        "chat_id": (int(i["chat_id"]) if i["chat_id"] is not None else None),
        "dimensions": dims,
        "bound_count": sum(1 for d in dims if d["bound"]),
        "total": len(DIMENSION_NAMES),
    }


def coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many of the six dimensions the PAIR KIND has wording for.

    This is about the KIND's wording in `dimension_binding_registry`, not about
    any one pair. A partial kind is REPORTED so the gap is visible.
    """
    import dimension_binding_registry as dbr

    ensure_schema(conn)
    have = dbr.bindings_for(conn, PAIR_KIND)
    missing = [n for n in DIMENSION_NAMES if n not in have]
    return {"ok": True, "subject_kind": PAIR_KIND,
            "complete": not missing,
            "bound": [n for n in DIMENSION_NAMES if n in have],
            "missing": missing,
            "total": len(DIMENSION_NAMES)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--questions", nargs=2, metavar=("WORKER_KEY",
                                                     "IDENTITY_KEY"))
    ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    conn = _connect(args.db)
    try:
        if args.questions:
            print(json.dumps(questions_for(conn, args.questions[0],
                                           args.questions[1]),
                             ensure_ascii=False, indent=2))
            return 0
        if args.coverage:
            print(json.dumps(coverage(conn), ensure_ascii=False, indent=2))
            return 0
    finally:
        conn.close()
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
