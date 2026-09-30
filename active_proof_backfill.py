# -*- coding: utf-8 -*-
"""active_proof_backfill.py — decide the 114 unproven active rows BY EVIDENCE.

THE HUMAN (2026-09-27):
    "唯一未結項係 WP2 嘅 114 行 backfill list（6 db_table + 107 db_field + 1
     identity），已 REPORTED 等 human decision — 唔阻收工。
     have the full plan, human decision = by evidence research, it can help no
     matter who has the decision"

So the decision is a RULE, not 114 individual judgements. Every active row that
lacks a proof is classified by what the EVIDENCE says about it:

    DERIVABLE      the live column EXISTS -> a value_type is derivable from it
                   (`value_type.value_type_for_column`), so a proof is written.
    STALE_COLUMN   the live column is GONE (table renamed/dropped) -> the row
                   cannot be proven, so it is DEACTIVATED (never deleted).
    NO_FIELDS      a `db_table_registry` row whose table has 0 registered fields
                   -> `proof_run_registry.db_field_id` is NOT NULL, so the
                   value-proof rule is STRUCTURALLY unsatisfiable. REPORTED.
    NO_CITE        an `*_registry` row with an empty `cite_ref` -> REPORTED.

MEASURED (this session, live DB): 47 DERIVABLE + 60 STALE_COLUMN + 6 NO_FIELDS
+ 1 NO_CITE = 114.

WHAT IT REFUSES
---------------
  * a `value_type` that was not derived by `value_type_for_column` (no typed
    value ever reaches the register);
  * deleting a row (a stale row is DEACTIVATED, so the evidence survives);
  * a silent fix for `NO_FIELDS` / `NO_CITE` (they are REPORTED, not touched).

    .\\.venv\\Scripts\\python.exe active_proof_backfill.py --classify
    .\\.venv\\Scripts\\python.exe active_proof_backfill.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import active_proof_gate as apg  # noqa: E402
import proof_run_registry as prr  # noqa: E402
import value_type as vt  # noqa: E402

DEFAULT_DB = BASE_DIR / "agent.db"

# the tables judged by the VALUE-PROOF rule (proof_run_registry)
VALUE_TABLES = ("db_table_registry", "db_field_registry")


def _live_columns(conn: sqlite3.Connection, table_key: str) -> dict[str, str]:
    """`{column_name: declared_type}` for a live table OR VIEW, else `{}`.

    MEASURED DEFECT (this session): the first version matched `type='table'`
    only, so `llm_100_run` — which is a VIEW with 21 columns — was reported
    STALE and its 19 derivable fields were lost. A view HAS columns, and a
    column in a view is as real as a column in a table, so both are accepted.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table_key,)).fetchone()
    if not row:
        return {}
    return {r[1]: (r[2] or "") for r in conn.execute(
        "PRAGMA table_info(%s)" % table_key)}


def classify(conn: sqlite3.Connection) -> dict[str, Any]:
    """Classify every active row that lacks a proof. REPORTS, never writes.

    Returns `{rows: [...], counts: {...}, total: N}`. Each row carries the
    evidence that decided its class, so a reader can check the decision.
    """
    rows: list[dict[str, Any]] = []

    # ---- db_table_registry: the value-proof rule ------------------------
    for r in conn.execute(
            "SELECT rowid AS _rid, * FROM db_table_registry WHERE is_active=1"):
        d = dict(r)
        tid = int(d["db_table_id"])
        if apg._has_active_proof(conn, "db_table_registry", "db_table_id", tid):
            continue
        n_fields = conn.execute(
            "SELECT COUNT(*) FROM db_field_registry WHERE db_table_id=?",
            (tid,)).fetchone()[0]
        live = _live_columns(conn, str(d["table_key"]))
        if n_fields == 0:
            rows.append({
                "class": "NO_FIELDS", "table": "db_table_registry",
                "id": tid, "key": str(d["table_key"]),
                "evidence": "table has 0 registered fields; proof_run_registry."
                            "db_field_id is NOT NULL, so no proof row can point "
                            "at it. Live table has %d columns." % len(live),
            })
        else:
            # a table WITH fields but no proof: its fields are the derivable
            # unit, so it is reported as DERIVABLE at the field level below.
            rows.append({
                "class": "DERIVABLE", "table": "db_table_registry",
                "id": tid, "key": str(d["table_key"]),
                "evidence": "table has %d registered fields; the proof is "
                            "written per field" % n_fields,
            })

    # ---- db_field_registry: the value-proof rule ------------------------
    for r in conn.execute(
            "SELECT rowid AS _rid, * FROM db_field_registry WHERE is_active=1"):
        d = dict(r)
        fid = int(d["db_field_id"])
        if apg._has_active_proof(conn, "db_field_registry", "db_field_id", fid):
            continue
        trow = conn.execute(
            "SELECT table_key FROM db_table_registry WHERE db_table_id=?",
            (int(d["db_table_id"]),)).fetchone()
        if not trow:
            rows.append({
                "class": "STALE_COLUMN", "table": "db_field_registry",
                "id": fid, "key": str(d["field_key"]),
                "evidence": "db_table_id=%s is not in db_table_registry"
                            % d["db_table_id"],
            })
            continue
        tkey = str(trow["table_key"])
        col = str(d["field_key"]).split(".")[-1]
        live = _live_columns(conn, tkey)
        if col not in live:
            rows.append({
                "class": "STALE_COLUMN", "table": "db_field_registry",
                "id": fid, "key": str(d["field_key"]),
                "evidence": "column %r is not in live table %r (%d live columns)"
                            % (col, tkey, len(live)),
            })
            continue
        derived = vt.value_type_for_column(col, live[col])
        rows.append({
            "class": "DERIVABLE", "table": "db_field_registry",
            "id": fid, "key": str(d["field_key"]),
            "db_table_id": int(d["db_table_id"]), "table_key": tkey,
            "column": col, "declared_type": live[col],
            "value_type": derived["value_type"], "reason": derived["reason"],
            "evidence": "live column %r is %s -> %s"
                        % (col, live[col], derived["value_type"]),
        })

    # ---- every other `*_registry` table: the cite rule ------------------
    tabs = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name LIKE '%_registry' OR name LIKE '%_registry') ORDER BY name")]
    for t in tabs:
        if t in ("proof_run_registry",) or t in VALUE_TABLES:
            continue
        cols = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)}
        if "is_active" not in cols or "cite_ref" not in cols:
            continue
        for r in conn.execute(
                "SELECT rowid AS _rid, * FROM %s WHERE is_active=1" % t):
            d = dict(r)
            rid = int(d["_rid"])
            res = apg.can_activate(conn, t, "rowid", rid, row=d)
            if res["ok"]:
                continue
            rows.append({
                "class": "NO_CITE", "table": t, "id": rid,
                "key": str(d.get("identity_key") or d.get("unit_key")
                           or d.get("factor_key") or rid),
                "evidence": res["reason"],
            })

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["class"]] = counts.get(row["class"], 0) + 1
    return {"rows": rows, "counts": counts, "total": len(rows)}


def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Apply the classification. DERIVABLE -> proof; STALE -> deactivate.

    `NO_FIELDS` / `NO_CITE` are REPORTED and NOT touched — a silent fix for a
    row whose evidence is missing would hide the gap.
    """
    res = classify(conn)
    written: list[dict[str, Any]] = []
    deactivated: list[dict[str, Any]] = []
    reported: list[dict[str, Any]] = []

    for row in res["rows"]:
        cls = row["class"]
        if cls == "DERIVABLE" and row["table"] == "db_field_registry":
            out = prr.declare_column(
                conn, db_table_id=int(row["db_table_id"]),
                db_field_id=int(row["id"]), value_type=row["value_type"],
                cite_ref=row["reason"])
            written.append({"db_field_id": int(row["id"]),
                            "field_key": row["key"],
                            "value_type": row["value_type"],
                            "proof_run_registry_id": out["id"],
                            "action": out["action"]})
        elif cls == "STALE_COLUMN":
            conn.execute(
                "UPDATE db_field_registry SET is_active=0, "
                "updated_at=datetime('now') WHERE db_field_id=?",
                (int(row["id"]),))
            deactivated.append({"db_field_id": int(row["id"]),
                                "field_key": row["key"],
                                "reason": row["evidence"]})
        else:
            reported.append(row)
    conn.commit()
    return {"written": written, "deactivated": deactivated,
            "reported": reported, "counts": res["counts"],
            "total": res["total"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--classify", action="store_true",
                    help="report the classification, write nothing")
    ap.add_argument("--apply", action="store_true",
                    help="write the proofs and deactivate the stale rows")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        if args.classify:
            res = classify(conn)
            print("ACTIVE-PROOF BACKFILL CLASSIFY  total=%d" % res["total"])
            for cls, n in sorted(res["counts"].items()):
                print("  %-14s %d" % (cls, n))
            for row in res["rows"]:
                print("  %-14s %-20s id=%-6s %-34s %s"
                      % (row["class"], row["table"], row["id"], row["key"],
                         row["evidence"][:60]))
            return 0
        if args.apply:
            res = apply(conn)
            print("ACTIVE-PROOF BACKFILL APPLY")
            print("  written     : %d" % len(res["written"]))
            print("  deactivated : %d" % len(res["deactivated"]))
            print("  reported    : %d" % len(res["reported"]))
            for cls, n in sorted(res["counts"].items()):
                print("  class %-14s %d" % (cls, n))
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
