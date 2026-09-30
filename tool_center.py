"""tool_center.py -- the STEP 3 tools, each paired with its ROLE.

THE USER'S DESIGN (2026-09-24, verbatim)
----------------------------------------
    "show chat center / Task Center / QC Center (new) for user to select"
    "-> chat center X research"
    "-> task center X writing"
    "-> QC Center X Verfitier"

MEASURED GAP: there was NO `qc_center` table. `qc_run` / `pair_qc_run` /
`schema_qc_run` are RUN logs, not a CENTER register. So the user's third step had
no register to read.

THE ROLE IS THE PAIRING
-----------------------
The user's mapping says which role each tool serves, so the tool register CARRIES
it rather than the UI typing it. That is what makes the step-3 highlight
DERIVABLE: a tool is highlighted when its `role_key` matches the role the picked
model is suited to.

THE THREE TOOLS, AND WHY EACH ROLE FITS
---------------------------------------
    chat center  x researcher  -- a chat READS and REPORTS; it produces no
                                  product artifact, so its output is a CLAIM
    task center  x writer      -- a task PRODUCES a product artifact (code, a
                                  plan, an execution log)
    QC Center    x verifier    -- QC CHECKS another worker's output against a
                                  standard or a proof

Those three definitions are READ from `role_registry`, not typed here.
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

SOURCE = "tool_center:tool_center x role_registry"

# THE USER'S MAPPING, declared ONCE so a seed and a proof cite the same list.
# `tool_key` is the stable key; `name` is what the user sees.
TOOLS: tuple[tuple[str, str, str], ...] = (
    ("chat_center", "Chat Center", "researcher"),
    ("task_center", "Task Center", "writer"),
    ("qc_center", "QC Center", "verifier"),
)


class ToolRefused(Exception):
    """Raised when a tool would name a role the register does not know."""


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _as_rows(conn: sqlite3.Connection) -> None:
    """Force `row_factory = sqlite3.Row`. Idempotent."""
    if conn.row_factory is not sqlite3.Row:
        conn.row_factory = sqlite3.Row


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create `tool_center` if absent. Idempotent."""
    import db_schema

    conn.executescript(db_schema.TOOL_CENTER_DDL)
    conn.commit()
    return {"ok": True, "table": "tool_center"}


def roles(conn: sqlite3.Connection) -> list[str]:
    """The role VOCABULARY, READ from `role_registry`. Never typed here."""
    _as_rows(conn)
    return [str(r["role_key"]) for r in conn.execute(
        "SELECT role_key FROM role_registry WHERE is_active=1 ORDER BY role_key")]


def declare(conn: sqlite3.Connection, *, tool_key: str, name: str,
            role_key: str, cite_ref: str, description: str = "",
            commit: bool = True) -> dict[str, Any]:
    """Declare ONE tool. REFUSES a role the register does not know."""
    _as_rows(conn)
    tk = str(tool_key or "").strip()
    rk = str(role_key or "").strip()
    if not tk:
        raise ToolRefused("tool_key is required")
    known = roles(conn)
    if rk not in known:
        raise ToolRefused("role_key `%s` is not in `role_registry` "
                          "(known: %s)" % (rk, known))
    row = conn.execute("SELECT tool_id FROM tool_center WHERE tool_key=?",
                       (tk,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO tool_center (tool_key, name, role_key, description, "
            "cite_ref) VALUES (?, ?, ?, ?, ?)",
            (tk, str(name or tk), rk, str(description or ""),
             str(cite_ref or "")))
        action = "inserted"
    else:
        conn.execute(
            "UPDATE tool_center SET name=?, role_key=?, description=?, "
            "cite_ref=?, is_active=1, updated_at=datetime('now') "
            "WHERE tool_id=?", (str(name or tk), rk, str(description or ""),
                                str(cite_ref or ""), int(row["tool_id"])))
        action = "updated"
    if commit:
        conn.commit()
    return {"ok": True, "action": action, "tool_key": tk, "role_key": rk}


def seed(conn: sqlite3.Connection, *, cite_ref: str,
         commit: bool = True) -> dict[str, Any]:
    """Declare the user's three tools. Idempotent."""
    out = []
    for tk, name, rk in TOOLS:
        out.append(declare(conn, tool_key=tk, name=name, role_key=rk,
                           cite_ref=cite_ref, commit=False))
    if commit:
        conn.commit()
    return {"ok": True, "seeded": len(out), "results": out}


def list_tools(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every tool, with its role's rights and definition READ from the register."""
    _as_rows(conn)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT t.tool_id, t.tool_key, t.name, t.role_key, t.description, "
            "r.may_read, r.may_write, r.may_verify, r.definition "
            "FROM tool_center t "
            "LEFT JOIN role_registry r ON r.role_key = t.role_key "
            "AND r.is_active=1 "
            "WHERE t.is_active=1 ORDER BY t.tool_id")]
    except Exception as exc:
        return {"ok": False, "rows": [], "count": 0,
                "error": "%s: %s" % (type(exc).__name__, exc)}
    by_role: dict[str, int] = {}
    for r in rows:
        by_role[str(r["role_key"])] = by_role.get(str(r["role_key"]), 0) + 1
    return {"ok": True, "rows": rows, "count": len(rows), "by_role": by_role,
            "role_vocabulary": roles(conn), "source": SOURCE}


def tool_for_role(conn: sqlite3.Connection, role_key: str) -> dict[str, Any]:
    """The tool a role serves. Never raises."""
    _as_rows(conn)
    rk = str(role_key or "").strip()
    if not rk:
        return {"ok": False, "role_key": "", "tool_key": None,
                "why": "role_key is required"}
    row = conn.execute(
        "SELECT tool_key, name FROM tool_center WHERE role_key=? AND is_active=1 "
        "ORDER BY tool_id LIMIT 1", (rk,)).fetchone()
    if row is None:
        return {"ok": True, "role_key": rk, "tool_key": None, "name": None,
                "why": "no tool is declared for role `%s`" % rk}
    return {"ok": True, "role_key": rk, "tool_key": str(row["tool_key"]),
            "name": str(row["name"]),
            "why": "role `%s` is served by `%s`" % (rk, row["name"])}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ensure", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args(argv)

    conn = _connect(args.db)
    try:
        if args.ensure:
            print(json.dumps(ensure_schema(conn), indent=2, ensure_ascii=False))
            return 0
        if args.seed:
            print(json.dumps(seed(conn, cite_ref="tool_center.py:TOOLS"),
                             indent=2, ensure_ascii=False))
            return 0
        print(json.dumps(list_tools(conn), indent=2, ensure_ascii=False))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
