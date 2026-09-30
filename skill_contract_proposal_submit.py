# -*- coding: utf-8 -*-
"""skill_contract_proposal_submit.py — a HUMAN's answers become contracts.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
`contract_proposal.py` renders `contract_proposal.tsv` with the DERIVABLE parts
pre-filled and the SEMANTIC parts BLANK (`purpose`, `taxonomy_path`,
`contract_id`). This module reads the ANSWERED file and writes the contracts.

WHAT IT REFUSES
---------------
  * a row whose answer is BLANK          -> SKIPPED and REPORTED, never written
  * a `taxonomy_path` that does not name a real ACTIVE ontology entity
                                          -> REFUSED by `upsert_contract`
                                             (`skill_contract_store.validate_taxonomy_path`)
  * a contract with no `purpose`          -> REFUSED by `upsert_contract`
  * a row with no `cite_ref`              -> REFUSED (a finding needs a reference)

It writes through `skill_contract_store.upsert_contract`, so the SAME hard
validation applies as for any other contract. It does NOT re-implement it.

Run:
    .\\.venv\\Scripts\\python.exe skill_contract_proposal_submit.py --dry-run
    .\\.venv\\Scripts\\python.exe skill_contract_proposal_submit.py --apply
"""
from __future__ import annotations

import argparse
import csv
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
PROPOSAL_TSV = BASE / "contract_proposal.tsv"

import contract_proposal as cp  # noqa: E402


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB))
    conn.row_factory = sqlite3.Row
    return conn


def read_answers(path: Path | str | None = None) -> list[dict[str, str]]:
    p = Path(path or PROPOSAL_TSV)
    if not p.is_file():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh, delimiter="\t")]


def submit(conn: sqlite3.Connection, *, apply: bool = False,
           path: Path | str | None = None) -> dict[str, Any]:
    """Write a contract for every ANSWERED row. Writes nothing when apply=False."""
    import skill_contract_store as scs

    rows = read_answers(path)
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for r in rows:
        sk = str(r.get("skill_key") or "").strip()
        tp = str(r.get("answer_taxonomy_path") or "").strip()
        purpose = str(r.get("answer_purpose") or "").strip()
        cid = str(r.get("answer_contract_id") or "").strip()
        cite = str(r.get("cite_ref") or "").strip()
        # BLANK ANSWER -> SKIP and REPORT. Never invent the missing fields.
        missing = [name for name, val in
                   (("answer_taxonomy_path", tp), ("answer_purpose", purpose),
                    ("answer_contract_id", cid), ("cite_ref", cite))
                   if not val]
        if missing:
            skipped.append({"skill_key": sk, "missing": missing})
            continue
        if not apply:
            written.append({"skill_key": sk, "taxonomy_path": tp,
                            "dry_run": True})
            continue
        res = scs.upsert_contract(
            cid, sk, tp, purpose,
            purpose_not_responsible=str(r.get("decision_reason") or "").strip()
            or None,
            status="draft", source="contract_proposal", conn=conn)
        if res.get("ok"):
            written.append({"skill_key": sk, "taxonomy_path": tp,
                            "action": res.get("action")})
        else:
            refused.append({"skill_key": sk, "code": res.get("code"),
                            "message": res.get("message")})
    if apply:
        conn.commit()
    return {"ok": True, "total": len(rows), "applied": apply,
            "written": len(written), "skipped": len(skipped),
            "refused": len(refused), "rows": written,
            "skipped_rows": skipped, "refused_rows": refused}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--proposal", default=str(PROPOSAL_TSV))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        res = submit(conn, apply=args.apply, path=args.proposal)
        mode = "APPLIED" if args.apply else "DRY RUN"
        print("%s: written=%d skipped=%d refused=%d of %d row(s)"
              % (mode, res["written"], res["skipped"], res["refused"],
                 res["total"]))
        for row in res["skipped_rows"][:5]:
            print("  SKIPPED %-38s missing=%s"
                  % (row["skill_key"], ",".join(row["missing"])))
        for row in res["refused_rows"][:5]:
            print("  REFUSED %-38s %s: %s"
                  % (row["skill_key"], row["code"], row["message"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
