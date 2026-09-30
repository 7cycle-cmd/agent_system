# -*- coding: utf-8 -*-
"""purpose_route_registry.py — which SERVICE can fulfil a PURPOSE.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
    "chatting is for purpose -> we need to provide services, which services can
     match the user, so i think we need workflow to define"

THE MISSING EDGE, MEASURED
--------------------------
The chain is `chat -> purpose -> service -> workflow`, and it broke at THREE
hops. Measured:
  * `chat` = `(session_id, worker_id)` EXISTS (`identity_registry`)
  * `chat -> purpose` NO (`chat_main` has no purpose col; `identity_registry.why`
    was `'NA'`) — FIXED by STEP 1 (a blank purpose is now REFUSED)
  * `purpose -> service` NO — NO table linked them
  * `service -> workflow` NO — `workflow_registry` has no `service_id`

THIS MODULE IS THE MIDDLE HOP. It is a ROUTE: given a PURPOSE, which SERVICE
fulfils it, and which WORKFLOW is the procedure that service runs.

WHY A ROW AND NOT A PYTHON MAP
------------------------------
A hardcoded `{"purpose": "service"}` dict is the defect this repo has recorded
repeatedly (`CAPABILITY_TAGS`, `openclaw_settings.CAPABILITIES`): a mapping that
cannot be extended without a code change, and that nothing else can read. So a
route is a ROW, and adding one is an INSERT.

BOTH ENDS MUST RESOLVE
----------------------
`ticket_origin` must name a REAL `ticket_center.ticket_origin` value (the 4 services
that exist: chat_center / task_center / manual / llm_service), and `workflow_key`
must name a REAL `workflow_registry` row. A route pointing at a service or a
workflow that does not exist is REFUSED, NAMING which end failed — a dangling
route is worse than no route, because it looks like coverage.

WHAT IT REFUSES
---------------
  * a route with no `cite_ref` — no citation, no route
  * a `ticket_origin` that is not a live `ticket_center.ticket_origin`
  * a `workflow_key` that is not a live `workflow_registry.workflow_key`
  * `select_route` on an UNKNOWN purpose — it REFUSES and names the purpose,
    rather than returning an empty list. An empty list is indistinguishable from
    "no routes exist at all", which is the empty-detector defect: a caller that
    gets [] cannot tell "not routed" from "the register is empty".

Run:
    .\\.venv\\Scripts\\python.exe purpose_route_registry.py --list
    .\\.venv\\Scripts\\python.exe purpose_route_registry.py --select <purpose>
    .\\.venv\\Scripts\\python.exe purpose_route_registry.py --seed
"""
from __future__ import annotations

import argparse
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

DB = BASE / "agent.db"

PURPOSE_ROUTE_DDL = """
CREATE TABLE IF NOT EXISTS purpose_route_registry (
    route_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    route_key     TEXT    NOT NULL UNIQUE,
    -- THE NEED. A free-text purpose key; it must equal a value an
    -- `identity_registry.why` carries, so a chat's purpose can be routed.
    purpose_key   TEXT    NOT NULL,
    -- THE SERVICE. Must resolve to a live `ticket_center.ticket_origin`.
    ticket_origin   TEXT    NOT NULL,
    -- THE PROCEDURE. Must resolve to a live `workflow_registry.workflow_key`.
    workflow_key  TEXT    NOT NULL,
    -- THE EVIDENCE. A route with no citation is a claim, not a route.
    cite_ref      TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    -- THE KEY FACTOR. Filled from a DERIVED match (`chat_level.fill_key_factor`),
    -- never typed. It is a COLUMN, so it must be declared BEFORE the table
    -- constraint below: SQLite requires every column definition to precede a
    -- `UNIQUE (...)` table constraint. MEASURED 2026-09-26: with `key_factor`
    -- AFTER the constraint the whole DDL failed to parse
    -- (`sqlite3.OperationalError: near "key_factor": syntax error`), and because
    -- `ensure_schema` uses `CREATE TABLE IF NOT EXISTS` the fault only fired on
    -- a FRESH database — so it shipped.
    key_factor    TEXT,
    UNIQUE (purpose_key, ticket_origin, workflow_key)
);
CREATE INDEX IF NOT EXISTS idx_purpose_route_purpose
  ON purpose_route_registry (purpose_key, is_active);
CREATE INDEX IF NOT EXISTS idx_purpose_route_service
  ON purpose_route_registry (ticket_origin, is_active);
"""


class RouteRefused(ValueError):
    """Raised when a route cannot be registered or resolved."""


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(PURPOSE_ROUTE_DDL)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO db_table_registry "
            "(table_key, name, description, is_active, version) "
            "VALUES (?, ?, ?, 1, '1')",
            ("purpose_route_registry", "purpose_route_registry",
             "the ROUTE: which SERVICE fulfils a PURPOSE, and the WORKFLOW that "
             "is the procedure it runs"))
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return {"ok": True, "table": "purpose_route_registry"}


def services(conn: sqlite3.Connection) -> list[str]:
    """The live service keys. The ONE source is `ticket_center.ticket_origin`."""
    if not _table_exists(conn, "ticket_center"):
        return []
    return [str(r["ticket_origin"]) for r in conn.execute(
        "SELECT DISTINCT ticket_origin FROM ticket_center "
        "WHERE ticket_origin IS NOT NULL AND ticket_origin <> '' ORDER BY ticket_origin")]


def workflows(conn: sqlite3.Connection) -> list[str]:
    """The live workflow keys. The ONE source is `workflow_registry`."""
    if not _table_exists(conn, "workflow_registry"):
        return []
    return [str(r["workflow_key"]) for r in conn.execute(
        "SELECT workflow_key FROM workflow_registry "
        "WHERE workflow_key IS NOT NULL AND workflow_key <> '' "
        "ORDER BY workflow_key")]


def add_route(conn: sqlite3.Connection, *, route_key: str, purpose_key: str,
              ticket_origin: str, workflow_key: str, cite_ref: str,
              is_active: int = 1) -> dict[str, Any]:
    """Register ONE route. REFUSES a blank citation or a dangling end."""
    ensure_schema(conn)
    rk = str(route_key or "").strip()
    pk = str(purpose_key or "").strip()
    sk = str(ticket_origin or "").strip()
    wk = str(workflow_key or "").strip()
    cite = str(cite_ref or "").strip()
    reasons: list[str] = []
    if not rk:
        reasons.append("route_key is required")
    if not pk:
        reasons.append("purpose_key is required (what need does this route serve?)")
    if not cite:
        reasons.append("cite_ref is required (no citation, no route)")
    if reasons:
        raise RouteRefused(reasons)

    # BOTH ENDS MUST RESOLVE. A dangling route looks like coverage.
    live_services = services(conn)
    if sk not in live_services:
        raise RouteRefused(
            ["ticket_origin %r is not a live `ticket_center.ticket_origin`; live: %s"
             % (sk, live_services or "(none)")])
    live_wf = workflows(conn)
    if wk not in live_wf:
        raise RouteRefused(
            ["workflow_key %r is not a live `workflow_registry.workflow_key`; "
             "live: %s" % (wk, live_wf or "(none)")])

    existing = conn.execute(
        "SELECT route_id FROM purpose_route_registry WHERE route_key = ?",
        (rk,)).fetchone()
    if existing:
        return {"ok": True, "created": False, "route_id": int(existing["route_id"]),
                "route_key": rk}
    cur = conn.execute(
        "INSERT INTO purpose_route_registry "
        "(route_key, purpose_key, ticket_origin, workflow_key, cite_ref, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rk, pk, sk, wk, cite, int(is_active)))
    conn.commit()
    return {"ok": True, "created": True, "route_id": int(cur.lastrowid),
            "route_key": rk}


def select_route(conn: sqlite3.Connection, purpose_key: str) -> dict[str, Any]:
    """Which route(s) serve this PURPOSE.

    REFUSES an unknown purpose rather than returning []. An empty list cannot be
    told apart from "the register is empty", so a caller would read "not routed"
    as "no routes exist" — the empty-detector defect.
    """
    ensure_schema(conn)
    pk = str(purpose_key or "").strip()
    if not pk:
        raise RouteRefused(["purpose_key is required to select a route"])
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM purpose_route_registry WHERE purpose_key = ? "
        "AND is_active = 1 ORDER BY route_id", (pk,))]
    if not rows:
        total = conn.execute(
            "SELECT COUNT(*) FROM purpose_route_registry").fetchone()[0]
        raise RouteRefused(
            ["purpose %r is UNROUTED: no active route serves it. "
             "(The register holds %d route(s) in total, so this is NOT an empty "
             "register — it is a purpose nobody has routed.)" % (pk, total)])
    return {"ok": True, "purpose_key": pk, "routes": rows,
            "count": len(rows)}


def list_routes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM purpose_route_registry ORDER BY purpose_key, route_id")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--select", metavar="PURPOSE")
    ap.add_argument("--services", action="store_true")
    ap.add_argument("--workflows", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.services:
            print("live services :", services(conn))
        elif args.workflows:
            print("live workflows:", workflows(conn))
        elif args.select:
            try:
                res = select_route(conn, args.select)
                for r in res["routes"]:
                    print("  %-24s service=%-14s workflow=%s"
                          % (r["route_key"], r["ticket_origin"], r["workflow_key"]))
            except RouteRefused as e:
                print("REFUSED: %s" % "; ".join(e.args[0]))
                return 2
        else:
            rows = list_routes(conn)
            print("purpose_route_registry: %d route(s)" % len(rows))
            for r in rows:
                print("  %-24s purpose=%-22s service=%-14s workflow=%s"
                      % (r["route_key"], r["purpose_key"], r["ticket_origin"],
                         r["workflow_key"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
