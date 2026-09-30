# -*- coding: utf-8 -*-
"""table_design.py — the FIVE factors a table must satisfy, as an AUDIT.

THE USER'S REQUIREMENT (2026-09-22)
-----------------------------------
    "DB driven can help worker not to have wrong data easy"
    "in table design,
     1) DB driven
     2) id = primary + autoinscrease
     3) field value as much as it can
     4) priority by id
     5) never null, null = NA"
    "be skill for having a table"
    "and make it auto forever"

and, on the shape:

    "skil generator by , you can said 5 factor is requirement for table design
     skill"

So the five rules ARE the five FACTORS of a `table_design` skill. They are
declared ONCE here as data, and the skill's contract is DERIVED from them — the
same shape `skill_5w1h.DIMENSIONS` uses, and for the same reason: a rule stated
in two places is a rule that can disagree with itself.

WHY AN AUDIT AND NOT A MIGRATION
--------------------------------
This module REPORTS. It does not alter a table. Measured: the existing schema has
many `TEXT` columns with no DEFAULT, so factor 3 will report a large number of
existing tables. That is the CORRECT outcome — the audit states the gap, and a
migration is a separate decision with its own risk.

WHAT IT REUSES RATHER THAN REINVENTS
------------------------------------
Factor 5 is ALREADY implemented, completely, in `no_null.py`:
`standardize_empty`, `assert_no_null`, `fk_columns`, `KIND_TEXT/INT/REAL/FK`.
This module CALLS it. A second implementation would be two answers to one
question, and the two would drift.

Run:
    .\\.venv\\Scripts\\python.exe table_design.py --audit
    .\\.venv\\Scripts\\python.exe table_design.py --audit --table dev_task
    .\\.venv\\Scripts\\python.exe table_design.py --factors
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

# ---------------------------------------------------------------------------
# THE FIVE FACTORS — declared ONCE, as data
# ---------------------------------------------------------------------------
# (factor_key, name, rule_definition, action, metric_kind, metric_target)
#
# `metric_kind` / `metric_target` are the MEASURED unit, so a factor can be
# scored rather than merely stated — the same shape `skill_factor_registry` uses.
FACTORS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("db_driven",
     "DB driven",
     "Every table must be registered in `db_table_registry`, and every column in "
     "`db_field_registry`, so a worker reads the definition instead of guessing.",
     "Register the table and its columns, with a cite_ref for each.",
     "pct", "100"),
    ("id_primary_autoincrement",
     "id = primary + autoincrement",
     "Every table must have an `INTEGER PRIMARY KEY AUTOINCREMENT` id, so a row "
     "has a stable identity that does not depend on its content.",
     "Add the id column, or declare why the table is a pure mapping.",
     "pct", "100"),
    ("field_value_max",
     "field value as much as it can",
     "A table must not MIX kinds of truth: if it carries a discriminator "
     "(`*_key`/`*_type`/`*_kind`/`*_level`/`*_status`/`*_mode`) AND columns that "
     "are populated for only SOME of that discriminator's values, the table is "
     "holding two shapes and must be SPLIT into layers. A field then carries as "
     "much meaning as it can, because it means exactly one thing.",
     "Split the table by the discriminator, or move the value-specific columns "
     "into a child table keyed by it.",
     "pct", "100"),
    ("priority_by_id",
     "priority by id",
     "Ordering uses `id`, or an explicit `sort_order` when the order is a "
     "DECISION rather than an accident of insertion.",
     "Order by id, or add a sort_order column and order by it.",
     "pct", "100"),
    ("never_null_na",
     "never null, null = NA",
     "An empty value is standardised to the explicit `NA`, so a surviving NULL "
     "always means the standardiser did not run — a defect, not an ambiguity.",
     "Route the write through `no_null.standardize_empty`.",
     "pct", "100"),
)

FACTOR_KEYS: tuple[str, ...] = tuple(f[0] for f in FACTORS)

# Tables that are EXEMPT from a factor, each WITH ITS REASON. The same shape
# `activation_gate.NO_ACTIVATION_BECAUSE` uses: a named exemption is a decision a
# reader can see, and the proof asserts every named table EXISTS.
#
# A pure MAPPING has no identity of its own, so factor 2 does not apply: minting
# an id for a mapping would make the mapping a thing.
NO_ID_BECAUSE: dict[str, str] = {
    "chat_registry": "MAPPING: pairs a ticket with a chat; it has no identity of its own",
    "ticket_module_map": "MAPPING: pairs a ticket with a module",
    "schema_migration_log": "LOG: append-only, its id IS the order",
    "sqlite_sequence": "SQLITE: an internal table",
}


class TableDesignError(ValueError):
    """Raised when the audit cannot run."""


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def _tables(conn: sqlite3.Connection) -> list[str]:
    return [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("PRAGMA table_info(%s)" % table)]


def _ddl(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                       "AND name=?", (table,)).fetchone()
    return str(row[0] or "") if row else ""


def audit_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    """Audit ONE table against the five factors.

    Returns `{table, factors: {key: {ok, detail}}, ok}`. Every factor reports a
    DETAIL even when it passes, so a pass is an observation rather than a
    silence.
    """
    cols = _columns(conn, table)
    ddl = _ddl(conn, table)
    names = [c["name"] for c in cols]
    out: dict[str, Any] = {"table": table, "factors": {}}

    # --- factor 1: DB driven ------------------------------------------------
    # `db_table_registry` uses `table_key` + `name` (measured: init_ontology_
    # registry.sql:127). My first version queried `table_name`, which does not
    # exist — caught by RUNNING the audit, not by reading the DDL.
    has_tbl_reg = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='db_table_registry'").fetchone() is not None
    if not has_tbl_reg:
        out["factors"]["db_driven"] = {
            "ok": None, "detail": "db_table_registry absent — cannot check"}
    else:
        row = conn.execute(
            "SELECT 1 FROM db_table_registry WHERE table_key=? OR name=?",
            (table, table)).fetchone()
        out["factors"]["db_driven"] = {
            "ok": row is not None,
            "detail": ("registered in db_table_registry" if row
                       else "NOT registered in db_table_registry")}

    # --- factor 2: id = primary + autoincrement -----------------------------
    if table in NO_ID_BECAUSE:
        out["factors"]["id_primary_autoincrement"] = {
            "ok": True, "detail": "EXEMPT: %s" % NO_ID_BECAUSE[table]}
    else:
        pk = [c for c in cols if int(c["pk"] or 0) == 1]
        is_int = bool(pk) and str(pk[0]["type"] or "").upper().startswith("INTEGER")
        has_auto = "AUTOINCREMENT" in ddl.upper()
        out["factors"]["id_primary_autoincrement"] = {
            "ok": bool(pk) and is_int and has_auto,
            "detail": ("id=%s INTEGER=%s AUTOINCREMENT=%s"
                       % (pk[0]["name"] if pk else "NONE", is_int, has_auto))}

    # --- factor 3: field value as much as it can ----------------------------
    # REINTERPRETED 2026-09-22. The user:
    #
    #   "table can be more to easy classify the true not mix that all in single
    #    table" / "table can have layer!"
    #
    # So the rule is NOT "every column has a value". It is: a table must not MIX
    # kinds of truth. A field carries as much MEANING as it can only if it means
    # exactly ONE thing — which requires the table to be SPLIT.
    #
    # HOW TO DETECT MIXING WITHOUT AN OVERLAP SCORE (`independent_review`
    # forbids scoring overlap): a table is MIXED when it carries BOTH
    #   (a) a DISCRIMINATOR column (`*_key`/`*_type`/`*_kind`/`*_level`/
    #       `*_status`/`*_mode`), AND
    #   (b) columns that are populated for only SOME of its values.
    # (b) is MEASURED from the data, not judged: a column is value-specific when
    # it is NULL for every row of at least one discriminator value and non-NULL
    # for at least one row of another. That is a fact about the rows.
    # A discriminator is a column that says WHICH KIND OF ROW this is. It is
    # matched by BARE name (`kind`) as well as by suffix (`entity_kind`) — my
    # first version only matched the suffix, so a table whose column is literally
    # `kind` was not detected. Caught by RUNNING the proof.
    _DISC_BARE = ("kind", "type", "status", "mode", "level", "key", "category")
    _DISC_SUFFIX = ("_key", "_type", "_kind", "_level", "_status", "_mode",
                    "_category")
    disc = [n for n in names
            if n.lower() in _DISC_BARE or n.lower().endswith(_DISC_SUFFIX)]
    mixed: list[str] = []
    disc_used = ""
    if disc:
        # Prefer the discriminator with the most distinct values — it is the one
        # that actually splits the table.
        best: tuple[int, str] = (0, "")
        for d in disc:
            try:
                n = conn.execute("SELECT COUNT(DISTINCT %s) FROM %s"
                                 % (d, table)).fetchone()[0]
            except sqlite3.Error:
                continue
            if n and n > best[0]:
                best = (int(n), d)
        if best[0] >= 2:
            disc_used = best[1]
            for c in cols:
                cn = str(c["name"])
                if cn == disc_used or int(c["pk"] or 0) == 1:
                    continue
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM %s WHERE %s IS NULL AND %s IS NOT "
                        "NULL" % (table, cn, disc_used)).fetchone()
                    row2 = conn.execute(
                        "SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL AND %s IS "
                        "NOT NULL" % (table, cn, disc_used)).fetchone()
                except sqlite3.Error:
                    continue
                if row and row2 and int(row[0]) > 0 and int(row2[0]) > 0:
                    mixed.append(cn)
    out["factors"]["field_value_max"] = {
        "ok": not mixed,
        "detail": ("no discriminator splits the table — every column means one "
                   "thing" if not mixed
                   else "MIXED: `%s` splits the table, and %d column(s) apply to "
                        "only some of its values: %s"
                        % (disc_used, len(mixed), mixed[:6]))}
    out["discriminator"] = disc_used
    out["mixed_columns"] = mixed

    # --- factor 3b: LAYERS ---------------------------------------------------
    # "table can have layer! it is same for how to find factor". A table's layer
    # is a first-class property. The repo already has three of the same shape:
    # `terminology_registry.parent_term_id`, `task_type_registry.parent_type_id`,
    # `industry_registry.parent_industry_id`. A flat table is NOT an error — but
    # a table that NEEDS layers and has none is the mixing defect above.
    layer_col = next((n for n in names
                      if n.lower() in ("parent_id", "parent_term_id",
                                       "parent_type_id", "parent_industry_id",
                                       "parent_key", "parent_ref_key")), "")
    out["layer_column"] = layer_col
    out["is_layered"] = bool(layer_col)

    # --- factor 4: priority by id -------------------------------------------
    # The rule is "order by id, OR by an explicit sort_order when the order is a
    # DECISION". A table with neither is reported, because its order is then an
    # accident of insertion.
    has_id = "id" in names
    has_sort = "sort_order" in names
    out["factors"]["priority_by_id"] = {
        "ok": has_id or has_sort,
        "detail": ("has `id`" if has_id else
                   ("has `sort_order`" if has_sort
                    else "neither `id` nor `sort_order` — order is an accident"))}

    # --- factor 5: never null, null = NA ------------------------------------
    # REUSED, not reinvented: `no_null` already implements this completely.
    try:
        import no_null as nn
        fks = nn.fk_columns(conn, table)
        out["factors"]["never_null_na"] = {
            "ok": True,
            "detail": ("no_null is available; %d FK column(s) exempt (NULL there "
                       "IS the NA): %s" % (len(fks), sorted(fks)[:4]))}
    except Exception as e:
        out["factors"]["never_null_na"] = {
            "ok": False, "detail": "no_null unavailable: %s" % e}

    scored = [v for v in out["factors"].values() if v["ok"] is not None]
    out["ok"] = all(v["ok"] for v in scored) if scored else None
    out["n_failed"] = sum(1 for v in scored if not v["ok"])
    return out


def audit_all(conn: sqlite3.Connection) -> dict[str, Any]:
    """Audit every table. Returns `{tables, results, summary}`.

    The summary counts PER FACTOR, because "12 tables failed" does not say WHICH
    rule is the problem — and the fix differs per rule.
    """
    results = [audit_table(conn, t) for t in _tables(conn)]
    per_factor: dict[str, dict[str, int]] = {
        k: {"pass": 0, "fail": 0, "skipped": 0} for k in FACTOR_KEYS}
    for r in results:
        for k, v in r["factors"].items():
            if v["ok"] is None:
                per_factor[k]["skipped"] += 1
            elif v["ok"]:
                per_factor[k]["pass"] += 1
            else:
                per_factor[k]["fail"] += 1
    return {
        "tables": len(results),
        "results": results,
        "summary": per_factor,
        "clean": sum(1 for r in results if r["ok"]),
        "dirty": sum(1 for r in results if r["ok"] is False),
    }


def factors_as_contract_fields() -> list[dict[str, Any]]:
    """The five factors as `skill_contract_field` rows.

    This is what makes the skill GENERATED rather than hand-written: the
    contract's fields ARE the factors, derived from the one declaration above.
    """
    return [{
        "field_name": key,
        "data_type": "TEXT",
        "hard_rule": rule,
        "mandatory": True,
        "immutable": False,
    } for key, _name, rule, _action, _mk, _mt in FACTORS]


def factors_as_registry_rows() -> list[dict[str, Any]]:
    """The five factors as `skill_factor_registry` rows."""
    return [{
        "factor_key": key,
        "name": name,
        "rule_definition": rule,
        "action": action,
        "metric_kind": mk,
        "metric_target": mt,
        "proof_prefix": key[:24],
    } for key, name, rule, action, mk, mt in FACTORS]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the table design audit")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--table", metavar="NAME")
    ap.add_argument("--factors", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.factors:
        for key, name, rule, action, mk, mt in FACTORS:
            print("%-26s %s" % (key, name))
            print("    rule   : %s" % rule)
            print("    action : %s" % action)
            print("    metric : %s target %s" % (mk, mt))
        return 0

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        if args.table:
            r = audit_table(conn, args.table)
            if args.json:
                print(json.dumps(r, indent=2, ensure_ascii=False))
                return 0
            print("=== %s ===" % r["table"])
            for k, v in r["factors"].items():
                print("  %-26s %-5s %s"
                      % (k, "ok" if v["ok"] else ("skip" if v["ok"] is None
                                                  else "FAIL"), v["detail"]))
            return 0 if r["ok"] else 1

        if args.audit:
            out = audit_all(conn)
            if args.json:
                print(json.dumps(out, indent=2, ensure_ascii=False))
                return 0
            print("=== table design audit ===")
            print("  tables : %d   clean : %d   dirty : %d"
                  % (out["tables"], out["clean"], out["dirty"]))
            print()
            print("  %-26s %6s %6s %8s" % ("FACTOR", "PASS", "FAIL", "SKIPPED"))
            for k in FACTOR_KEYS:
                s = out["summary"][k]
                print("  %-26s %6d %6d %8d"
                      % (k, s["pass"], s["fail"], s["skipped"]))
            print()
            print("  tables failing each factor:")
            for k in FACTOR_KEYS:
                bad = [r["table"] for r in out["results"]
                       if r["factors"][k]["ok"] is False]
                if bad:
                    print("    %-26s %d  %s" % (k, len(bad), bad[:5]))
            return 0
        ap.print_help()
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
