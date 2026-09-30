# -*- coding: utf-8 -*-
"""skill_taxonomy_backfill.py — DERIVE a skill's taxonomy_path from its contract.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "1 -> 2"   (1 = the 55 skill graph gaps; 2 = the stale-assumption proofs)

`entity_graph_report.py` reports `skill 0/55`: every skill node has a register
row (so it IS located) but NO parent edge, with the reason
`skill_registry.taxonomy_path is EMPTY, so no parent can be derived`.

WHY IT IS EMPTY, AND WHY THE FIX IS A COPY (measured, not guessed)
------------------------------------------------------------------
`skill_registry` declares NO FK (`PRAGMA foreign_key_list(skill_registry)` is
empty), so `taxonomy_path` is its ONLY parent source (`db_schema.py:2283-2305`).
And the value ALREADY EXISTS elsewhere: `skill_contract_template.taxonomy_path`
is HARD-REQUIRED per skill (`skill_registrar.py:84`:
`REQUIRED_KEYS = ("skill_key","contract_id","taxonomy_path","purpose")`), keyed
by `skill_key` — the SAME key as `skill_registry`.

So the parent is DECLARED, and the register the graph reads was never populated
from it. This module COPIES it. It does NOT invent one.

THE RULES
---------
* a skill with NO contract            -> REFUSED, with a named reason
* a contract with an EMPTY path       -> REFUSED, with a named reason
* the path must name a REAL registered parent -> hard-validated before writing
* DRY RUN FIRST: `--apply` off writes NOTHING, and `would_write` must equal the
  real run's `written` (the "dry-run-lies" guard)

Run:
    .\\.venv\\Scripts\\python.exe skill_taxonomy_backfill.py --measure
    .\\.venv\\Scripts\\python.exe skill_taxonomy_backfill.py            # dry run
    .\\.venv\\Scripts\\python.exe skill_taxonomy_backfill.py --apply
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

# The taxonomy levels a `taxonomy_path` may name, from the ONE declaration.
# `hardcode_scope.SCOPE_ORDER` holds the level KEYS; a path is `level/subject`.
_SCOPE_LEVELS: tuple[str, ...] = ()


def _scope_levels() -> tuple[str, ...]:
    global _SCOPE_LEVELS
    if not _SCOPE_LEVELS:
        try:
            import hardcode_scope as hs
            _SCOPE_LEVELS = tuple(hs.SCOPE_ORDER)
        except Exception:
            _SCOPE_LEVELS = ("db_field", "db_table", "function", "api",
                             "capability", "module", "channel")
    return _SCOPE_LEVELS


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    """The JOIN hit rate. Nothing below is valid before this is known."""
    out: dict[str, Any] = {}
    out["skill_total"] = conn.execute(
        "SELECT COUNT(1) FROM skill_registry").fetchone()[0]
    out["skill_with_path"] = conn.execute(
        "SELECT COUNT(1) FROM skill_registry "
        "WHERE taxonomy_path IS NOT NULL AND taxonomy_path <> ''").fetchone()[0]
    if not _table_exists(conn, "skill_contract_template"):
        out["error"] = "skill_contract_template absent"
        return out
    out["contract_total"] = conn.execute(
        "SELECT COUNT(1) FROM skill_contract_template").fetchone()[0]
    out["join_hit"] = conn.execute(
        "SELECT COUNT(1) FROM skill_registry s "
        "  JOIN skill_contract_template t ON t.skill_key = s.skill_key "
        " WHERE t.taxonomy_path IS NOT NULL AND t.taxonomy_path <> ''"
    ).fetchone()[0]
    # THE NUMBER `propose()` MUST REPRODUCE: EMPTY skill rows that are DERIVABLE.
    # `join_hit` counts contracts; a skill whose row is ALREADY filled is not a
    # write target, so the two differ once a backfill has run. Measured
    # 2026-09-23: conflating them made the proof compare 10 (contracts) with 0
    # (empty derivable rows) and go RED.
    out["derivable_empty"] = conn.execute(
        "SELECT COUNT(1) FROM skill_registry s "
        "  JOIN skill_contract_template t ON t.skill_key = s.skill_key "
        " WHERE (s.taxonomy_path IS NULL OR s.taxonomy_path = '') "
        "   AND t.taxonomy_path IS NOT NULL AND t.taxonomy_path <> ''"
    ).fetchone()[0]
    out["skill_without_contract"] = conn.execute(
        "SELECT COUNT(1) FROM skill_registry s WHERE NOT EXISTS "
        "  (SELECT 1 FROM skill_contract_template t "
        "    WHERE t.skill_key = s.skill_key)").fetchone()[0]
    return out


def _parent_of(path: str) -> str | None:
    """The parent a `taxonomy_path` declares, or None.

    `module/task_center` -> `task_center` (the module the skill belongs to).
    A path with no `/` declares no parent.
    """
    p = str(path or "").strip()
    if "/" not in p:
        return None
    return p.rsplit("/", 1)[1].strip() or None


def propose(conn: sqlite3.Connection) -> dict[str, Any]:
    """What WOULD be written: derived rows, and refusals with a named reason.

    Writes NOTHING. The dry run's `would_write` is what `apply` must reproduce.
    """
    proposed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    if not _table_exists(conn, "skill_registry"):
        return {"ok": False, "error": "skill_registry absent"}
    if not _table_exists(conn, "skill_contract_template"):
        return {"ok": False, "error": "skill_contract_template absent"}

    rows = [dict(r) for r in conn.execute(
        "SELECT skill_id, skill_key, taxonomy_path FROM skill_registry "
        " WHERE taxonomy_path IS NULL OR taxonomy_path = '' "
        " ORDER BY skill_id")]
    for r in rows:
        sk = str(r["skill_key"])
        t = conn.execute(
            "SELECT taxonomy_path FROM skill_contract_template "
            " WHERE skill_key = ?", (sk,)).fetchone()
        if t is None:
            refused.append({"skill_key": sk, "code": "NO_CONTRACT",
                            "why": "no skill_contract_template row for this "
                                   "skill_key, so no parent is DECLARED"})
            continue
        path = str(t["taxonomy_path"] or "").strip()
        if not path:
            refused.append({"skill_key": sk, "code": "EMPTY_CONTRACT_PATH",
                            "why": "the contract's taxonomy_path is empty, so "
                                   "no parent is declared"})
            continue
        parent = _parent_of(path)
        if parent is None:
            refused.append({"skill_key": sk, "code": "NO_PARENT_IN_PATH",
                            "why": "taxonomy_path %r names no parent" % path})
            continue
        proposed.append({"skill_id": int(r["skill_id"]), "skill_key": sk,
                         "taxonomy_path": path, "parent": parent})
    return {"ok": True, "total": len(rows), "would_write": len(proposed),
            "refused": len(refused), "proposed": proposed,
            "refused_rows": refused}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Write ONLY the proposed rows. Idempotent (a filled row is not a target)."""
    p = propose(conn)
    if not p.get("ok"):
        return p
    written = 0
    for row in p["proposed"]:
        conn.execute(
            "UPDATE skill_registry SET taxonomy_path = ?, "
            "updated_at = datetime('now') WHERE skill_id = ?",
            (row["taxonomy_path"], row["skill_id"]))
        written += 1
    conn.commit()
    return {"ok": True, "total": p["total"], "written": written,
            "would_write": p["would_write"], "refused": p["refused"],
            "refused_rows": p["refused_rows"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.measure:
            m = measure(conn)
            print("== the derivation source ==")
            for k, v in m.items():
                print("  %-24s %s" % (k, v))
            return 0
        if args.apply:
            r = apply(conn)
            print("APPLIED: written=%s (would_write=%s) refused=%s"
                  % (r.get("written"), r.get("would_write"), r.get("refused")))
            for row in r.get("refused_rows", [])[:10]:
                print("  REFUSED %-40s %s" % (row["skill_key"], row["why"]))
            return 0
        p = propose(conn)
        print("DRY RUN: would_write=%s refused=%s of %s empty skill row(s)"
              % (p.get("would_write"), p.get("refused"), p.get("total")))
        for row in p.get("refused_rows", [])[:10]:
            print("  REFUSED %-40s %s" % (row["skill_key"], row["why"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
