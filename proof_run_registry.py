"""proof_run_registry.py -- WHICH column, WHICH value format, and its verdict.

THE HUMAN, verbatim:

    "table design / is_active is for table all the field"
    "proof run_registry / id | table_id | columu_ID | type (visual / text)"
    "report is / proof run_registry_id | pass | fail | reason"
    "column = table + id | 1 | 2"
    "proof run, proof that with TDD"
    "coordinate i have data now, proof run need to proof that too"
    "proof run need to understand and know which data format need to how to proof
     = same language"

THE TWO TABLES:

    proof_run_registry   the PER-COLUMN declaration: WHICH column, WHICH format
    proof_report         the VERDICT: pass/fail counts + reason

WHY THE JOIN KEY DID NOT EXIST (MEASURED 2026-09-26):

    `proof_run` had NO `db_field_id`, and `field_tdd_rule` had NO `db_field_id`
    either. So a round could not say WHICH column it measured. MEASURED: the only
    `ref_tag` values are `1.1F` / `worker_identity` / `worker_identity_flow`, and
    NONE of them names a `db_field_registry.field_key`.

    `db_field_registry` ALREADY IS `table + id` (the human's own rule):
    `db_field_id=1, db_table_id=1 -> field_key='register_id'`. So the column
    identity exists; only the LINK was missing.

THE VALUE FORMAT IS NOT TYPED HERE. It is resolved by `value_type.py`, which is
the ONE vocabulary shared by the question side and the proof side.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

import value_type as vt

BASE = Path(__file__).resolve().parent
DB = BASE / "agent.db"


class RegisterRefused(ValueError):
    """A declaration that cannot be made, with a named reason."""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the two tables. Idempotent (delegates to `value_type`)."""
    vt.ensure_schema(conn)


def declare_column(conn: sqlite3.Connection, *, db_table_id: int,
                   db_field_id: int, value_type: str,
                   cite_ref: str = "NA") -> dict[str, Any]:
    """Declare WHICH value format a column carries. Idempotent.

    REFUSES rather than guessing:
      * an unknown `value_type` (the vocabulary is closed);
      * a `db_field_id` that does not exist in `db_field_registry`;
      * a `db_field_id` that belongs to a DIFFERENT `db_table_id` -- a declaration
        that names the wrong table is worse than no declaration.
    """
    ensure_schema(conn)
    canonical = vt.canonical(value_type)

    row = conn.execute(
        "SELECT db_table_id, field_key FROM db_field_registry WHERE db_field_id=?",
        (int(db_field_id),)).fetchone()
    if not row:
        raise RegisterRefused(
            "db_field_id=%r is not in db_field_registry. A declaration for a "
            "column that does not exist can never be measured." % db_field_id)
    real_table = int(row["db_table_id"])
    if real_table != int(db_table_id):
        raise RegisterRefused(
            "db_field_id=%r belongs to db_table_id=%r, not %r. A declaration that "
            "names the wrong table is worse than no declaration."
            % (db_field_id, real_table, db_table_id))

    existing = conn.execute(
        "SELECT id FROM proof_run_registry WHERE db_table_id=? AND db_field_id=?",
        (int(db_table_id), int(db_field_id))).fetchone()
    if existing:
        conn.execute(
            "UPDATE proof_run_registry SET value_type=?, cite_ref=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (canonical, str(cite_ref), int(existing["id"])))
        conn.commit()
        return {"ok": True, "action": "updated", "id": int(existing["id"]),
                "db_table_id": int(db_table_id), "db_field_id": int(db_field_id),
                "field_key": str(row["field_key"]), "value_type": canonical,
                "resolution": vt.describe(canonical)}

    cur = conn.execute(
        "INSERT INTO proof_run_registry (db_table_id, db_field_id, value_type, "
        "cite_ref) VALUES (?,?,?,?)",
        (int(db_table_id), int(db_field_id), canonical, str(cite_ref)))
    conn.commit()
    return {"ok": True, "action": "created", "id": int(cur.lastrowid),
            "db_table_id": int(db_table_id), "db_field_id": int(db_field_id),
            "field_key": str(row["field_key"]), "value_type": canonical,
            "resolution": vt.describe(canonical)}


def register_for_column(conn: sqlite3.Connection, *, db_table_id: int,
                        db_field_id: int) -> dict[str, Any] | None:
    """The register row for a column, or `None`. Never raises."""
    try:
        row = conn.execute(
            "SELECT * FROM proof_run_registry WHERE db_table_id=? AND "
            "db_field_id=?", (int(db_table_id), int(db_field_id))).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def write_report(conn: sqlite3.Connection, *, proof_run_registry_id: int,
                 pass_count: int, fail_count: int,
                 reason: str = "NA") -> dict[str, Any]:
    """Write the VERDICT for a register row. `pass`/`fail` are COUNTS.

    REFUSES a register id that does not exist: a report for a declaration that
    does not exist can never be read back.
    """
    ensure_schema(conn)
    row = conn.execute("SELECT id FROM proof_run_registry WHERE id=?",
                       (int(proof_run_registry_id),)).fetchone()
    if not row:
        raise RegisterRefused(
            "proof_run_registry_id=%r does not exist. A report for a declaration "
            "that does not exist can never be read back." % proof_run_registry_id)
    cur = conn.execute(
        "INSERT INTO proof_report (proof_run_registry_id, pass, fail, reason) "
        "VALUES (?,?,?,?)",
        (int(proof_run_registry_id), int(pass_count), int(fail_count),
         str(reason)))
    conn.commit()
    return {"ok": True, "id": int(cur.lastrowid),
            "proof_run_registry_id": int(proof_run_registry_id),
            "pass": int(pass_count), "fail": int(fail_count),
            "reason": str(reason)}


def report_from_run(conn: sqlite3.Connection, *, proof_run_registry_id: int,
                    run: dict[str, Any]) -> dict[str, Any]:
    """Write a `proof_report` from a harness run's rounds.

    `pass` is the number of winning rounds, `fail` the number of losing ones. The
    reason names the verdict and the value format, so a reader can tell WHICH
    format was proved and HOW.
    """
    rounds = run.get("rounds") or []
    wins = sum(1 for r in rounds if int(r.get("win") or 0) == 1)
    losses = len(rounds) - wins
    reason = ("verdict=%s value_type=%s proof_method=%s model=%s"
              % (run.get("verdict"), run.get("value_type"),
                 run.get("proof_method"), run.get("model")))
    return write_report(conn, proof_run_registry_id=proof_run_registry_id,
                        pass_count=wins, fail_count=losses, reason=reason)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--table-id", type=int)
    ap.add_argument("--field-id", type=int)
    ap.add_argument("--value-type")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        if args.table_id is None or args.field_id is None:
            print("=== the register rows ===")
            rows = list(conn.execute(
                "SELECT r.id, r.db_table_id, r.db_field_id, r.value_type, "
                "f.field_key FROM proof_run_registry r LEFT JOIN "
                "db_field_registry f ON f.db_field_id=r.db_field_id"))
            for r in rows:
                print("   %s" % dict(r))
            print("   total: %d" % len(rows))
            print("\nPass --table-id / --field-id / --value-type to declare one.")
            return 0

        if not args.value_type:
            print("REFUSED: --value-type is required to declare a column.")
            return 1
        if not args.apply:
            print("REPORT ONLY. Would declare db_table_id=%d db_field_id=%d "
                  "value_type=%r" % (args.table_id, args.field_id,
                                     args.value_type))
            print("   resolution: %s" % vt.describe(args.value_type))
            return 0
        r = declare_column(conn, db_table_id=args.table_id,
                           db_field_id=args.field_id,
                           value_type=args.value_type,
                           cite_ref="proof_run_registry.py:1")
        print("=== declared ===")
        for k, v in r.items():
            print("   %-14s %r" % (k, v))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
