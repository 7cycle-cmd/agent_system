# -*- coding: utf-8 -*-
"""graph_edge_derive.py — put the DERIVABLE parent edges into the graph, and
                          REPORT the ones that have no evidence.

WHY THIS EXISTS (user, 2026-09-24)
----------------------------------
The user chose order "1 → 3 → 2 → 4", where #1 is the 85 missing edges.
`entity_graph_report.py` measures BY KIND:

    factor      0/38   component   0/8   test_case   0/32   workflow   0/1

`entity_graph_report.EDGE_FORMS_BY_KIND` ALREADY declares the form for each:

    factor     forms=("fk","parent","task")  "applies to a skill BY ID (fk skill_ref)"
    component  forms=("fk","parent","task")  "links to a skill BY ID (fk skill_ref)"

So the RULE is not in question — what was missing is the DATA. This module fills
the edges whose parent is RECORDED, and it REFUSES the rest, naming the row.

WHAT IS ACTUALLY DERIVABLE (measured)
-------------------------------------
    factor     skill_factor_registry.skill_ref              5 of 39
    component  component_registry.skill_ref                 1 of 8
    test_case  NO column names a parent at all
    workflow   NO column names a parent at all

`skill_factor_proof` LOOKED like a second witness and is NOT (corrected: my first
version used it and over-counted to 16). It is the MEASUREMENT table, and ALL TEN
unrelated CRUD/DDL factors share `skill_ref=48 = no_null_standard` — a sweep
against one skill, not membership. A measurement is not a relation.

THE REMAINING 34 FACTORS ARE **REPORTED, NOT INVENTED**
------------------------------------------------------
The factor_key does NOT encode the skill: measured, 0 of 34 keyed rows has a
`factor_key` that starts with a registered `skill_key` (the 34 are shared CRUD /
TDD / capability factors). So there is no evidence to derive from, and a guess
would write a WRONG edge — the exact defect this repo refuses. They are reported
with the reason so the fix (record the parent) is obvious.

THE `fk` FORM MUST BE FOR REAL, NOT FAKED BY `task_entity_link`
----------------------------------------------------------------
`entity_graph_report` accepts `fk`, `parent`, `task`, `taxonomy`, `taxonomy_path`
per kind. `factor`'s declared form is `fk` = "a FK in the node's OWN register row
that points at ANOTHER registered table". `skill_factor_registry` has NO FK
constraint on `skill_ref` (measured: `PRAGMA foreign_key_list` is empty), so the
`fk` form cannot fire. Writing a `task_entity_link` row instead would resolve the
`task` form and make the node look complete while the register still declares
nothing — a FALSE GREEN. So this module does the HONEST thing: it adds the real
FK constraint, so the edge the register MEANT becomes the edge the graph SEES.

Run:
    .\\.venv\\Scripts\\python.exe graph_edge_derive.py --measure
    .\\.venv\\Scripts\\python.exe graph_edge_derive.py            # dry run
    .\\.venv\\Scripts\\python.exe graph_edge_derive.py --apply
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

DB = BASE / "agent.db"

# (register table, its pk, the parent column, the parent table, the parent pk)
FK_TARGETS: tuple[tuple[str, str, str, str, str], ...] = (
    ("skill_factor_registry", "factor_id", "skill_ref", "skill_registry",
     "skill_id"),
    ("component_registry", "skill_id", "skill_ref", "skill_registry",
     "skill_id"),
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)]


def _fks(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, str]]:
    return [(str(r[2]), str(r[3]), str(r[4]))
            for r in conn.execute("PRAGMA foreign_key_list(%s)" % table)]


def evidence_rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Every node whose parent skill is RECORDED, with the source that says so.

    `skill_factor_proof` IS DELIBERATELY **NOT** A SOURCE (corrected 2026-09-24).
    My first version used it and reported 16 factors; that was WRONG. MEASURED:
    `skill_factor_proof` is the MEASUREMENT table ("the MEASURED result, per skill
    x factor", `skill_factor.py:36`), and its `proof_artifact_id` is
    `proof_<skill_key>_<factor_key>_<hash8>` (`_repair_proof_factor_ref.py:22-24`).
    So its `(skill_ref, factor_ref)` pair says "which SKILL a measurement was
    attributed to", NOT "which skill owns the factor".

    The deciding evidence: ALL TEN unrelated CRUD/DDL factors (1, 3, 8, 9, 14,
    15, 16, 17, 19) carry `skill_ref=48 = no_null_standard`. A sweep measured many
    factors against one skill; reading that as membership would make every one of
    them a child of `no_null_standard`. A measurement is not a relation — using it
    would write a WRONG edge, which is worse than a missing one.
    """
    out: dict[str, list[dict[str, Any]]] = {"factor": [], "component": []}
    if _table_exists(conn, "skill_factor_registry"):
        for r in conn.execute(
                "SELECT factor_id, factor_key, skill_ref, skill_key "
                "FROM skill_factor_registry ORDER BY factor_id"):
            d = dict(r)
            if d["skill_ref"] is not None:
                out["factor"].append(
                    {"ref_id": int(d["factor_id"]), "skill_ref": int(d["skill_ref"]),
                     "source": "skill_factor_registry.skill_ref",
                     "cite": "skill_factor_registry.factor_id=%d.skill_ref=%d"
                             % (int(d["factor_id"]), int(d["skill_ref"]))})
    if _table_exists(conn, "component_registry"):
        for r in conn.execute(
                "SELECT skill_id, skill_key, skill_ref FROM component_registry "
                "ORDER BY skill_id"):
            d = dict(r)
            if d["skill_ref"] is not None:
                out["component"].append(
                    {"ref_id": int(d["skill_id"]),
                     "skill_ref": int(d["skill_ref"]),
                     "source": "component_registry.skill_ref",
                     "cite": "component_registry.skill_id=%d.skill_ref=%d"
                             % (int(d["skill_id"]), int(d["skill_ref"]))})
    return out


def unresolved(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Nodes whose parent is NOT recorded. REPORTED, never invented."""
    ev = evidence_rows(conn)
    have = {k: {e["ref_id"] for e in v} for k, v in ev.items()}
    out: dict[str, list[dict[str, Any]]] = {"factor": [], "component": []}
    if _table_exists(conn, "skill_factor_registry"):
        for r in conn.execute(
                "SELECT factor_id, factor_key FROM skill_factor_registry "
                "ORDER BY factor_id"):
            if int(r["factor_id"]) in have["factor"]:
                continue
            out["factor"].append(
                {"ref_id": int(r["factor_id"]), "key": r["factor_key"],
                 "why": ("no skill_ref on the row; factor_key encodes no skill "
                         "(measured: 0 of 34 keyed factors prefix-match a "
                         "registered skill) and skill_factor_proof is a "
                         "MEASUREMENT, not membership")})
    if _table_exists(conn, "component_registry"):
        for r in conn.execute(
                "SELECT skill_id, skill_key FROM component_registry "
                "ORDER BY skill_id"):
            if int(r["skill_id"]) in have["component"]:
                continue
            out["component"].append(
                {"ref_id": int(r["skill_id"]), "key": r["skill_key"],
                 "why": ("skill_ref is NULL and this skill_key exists ONLY in "
                         "component_registry (measured: not a skill_registry "
                         "row), so there is no skill to point at")})
    return out


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    ev = evidence_rows(conn)
    un = unresolved(conn)
    fk_state: dict[str, Any] = {}
    for table, pk, col, pt, ppk in FK_TARGETS:
        if not _table_exists(conn, table):
            fk_state[table] = "ABSENT"
            continue
        declared = any(f[0] == pt and f[1] == col for f in _fks(conn, table))
        n = conn.execute("SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL"
                         % (table, col)).fetchone()[0]
        bad = conn.execute(
            "SELECT COUNT(*) FROM %s t WHERE t.%s IS NOT NULL AND NOT EXISTS("
            "SELECT 1 FROM %s p WHERE p.%s = t.%s)"
            % (table, col, pt, ppk, col)).fetchone()[0]
        fk_state[table] = {"declared": declared, "filled": n, "dangling": bad}
    return {"evidence": {k: len(v) for k, v in ev.items()},
            "unresolved": {k: len(v) for k, v in un.items()},
            "fk_state": fk_state}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add the real FK constraints. Idempotent. REFUSES a dangling value.

    A FK cannot be added by ALTER TABLE in SQLite, so the table is REBUILT — and
    the rebuild is guarded: a dangling `skill_ref` (a value with no
    `skill_registry` row) makes it REFUSE, because silently dropping or nulling
    such a row would destroy the very evidence this module exists to report.
    """
    results: list[dict[str, Any]] = []
    for table, pk, col, pt, ppk in FK_TARGETS:
        if not _table_exists(conn, table):
            results.append({"table": table, "ok": False, "code": "ABSENT"})
            continue
        if any(f[0] == pt and f[1] == col for f in _fks(conn, table)):
            results.append({"table": table, "ok": True, "code": "ALREADY_DECLARED"})
            continue
        dangling = conn.execute(
            "SELECT COUNT(*) FROM %s t WHERE t.%s IS NOT NULL AND NOT EXISTS("
            "SELECT 1 FROM %s p WHERE p.%s = t.%s)"
            % (table, col, pt, ppk, col)).fetchone()[0]
        if dangling:
            results.append({"table": table, "ok": False, "code": "DANGLING_REF",
                            "message": ("%d %s rows have a %s with no %s row — a "
                                        "FK would not hold; fix the data first"
                                        % (dangling, table, col, pt))})
            continue
        cols = _cols(conn, table)
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not ddl:
            results.append({"table": table, "ok": False, "code": "NO_DDL"})
            continue
        new_sql = str(ddl["sql"]).rstrip().rstrip(";")
        if ")" not in new_sql:
            results.append({"table": table, "ok": False, "code": "UNPARSEABLE"})
            continue
        head, tail = new_sql.rsplit(")", 1)
        tmp = "_%s_fk_rebuild" % table
        conn.execute("ALTER TABLE %s RENAME TO %s" % (table, tmp))
        conn.execute(head + ",\n    FOREIGN KEY (%s) REFERENCES %s (%s)\n)%s"
                     % (col, pt, ppk, tail))
        conn.execute("INSERT INTO %s (%s) SELECT %s FROM %s"
                     % (table, ", ".join(cols), ", ".join(cols), tmp))
        before = conn.execute("SELECT COUNT(*) FROM %s" % tmp).fetchone()[0]
        after = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        if before != after:
            conn.execute("DROP TABLE %s" % table)
            conn.execute("ALTER TABLE %s RENAME TO %s" % (tmp, table))
            results.append({"table": table, "ok": False,
                            "code": "ROW_COUNT_MISMATCH",
                            "message": "before=%d after=%d — rolled back"
                                       % (before, after)})
            continue
        conn.execute("DROP TABLE %s" % tmp)
        conn.commit()
        results.append({"table": table, "ok": True, "code": "FK_ADDED",
                        "rows": after})
    return {"ok": all(r.get("ok") for r in results), "results": results}


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
            res = measure(conn)
        elif args.apply:
            res = apply(conn)
        else:
            ev = evidence_rows(conn)
            un = unresolved(conn)
            res = {"evidence": {k: len(v) for k, v in ev.items()},
                   "unresolved": {k: len(v) for k, v in un.items()},
                   "unresolved_rows": un}
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        elif args.measure:
            print("EVIDENCE (parent recorded):")
            for k, v in res["evidence"].items():
                print("   %-12s %d" % (k, v))
            print("UNRESOLVED (no parent recorded — reported, never invented):")
            for k, v in res["unresolved"].items():
                print("   %-12s %d" % (k, v))
            for t, st in res["fk_state"].items():
                print("   FK %-24s %s" % (t, st))
        elif args.apply:
            for r in res["results"]:
                print("   %-24s %-16s %s"
                      % (r.get("table"), r.get("code"),
                         r.get("message") or r.get("rows") or ""))
        else:
            print("DRY RUN: evidence=%s unresolved=%s"
                  % (res["evidence"], res["unresolved"]))
            for r in res["unresolved_rows"]["factor"][:3]:
                print("   factor %s: %s" % (r["ref_id"], r["why"]))
            for r in res["unresolved_rows"]["component"][:3]:
                print("   component %s: %s" % (r["ref_id"], r["why"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
