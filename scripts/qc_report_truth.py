#!/usr/bin/env python
"""qc_report_truth.py — is the report TRUE? Re-run every row and print the count.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "you need to have table to measure it! not talking on air"

A report that says "ontology PASS" is a CLAIM. The only way to judge it is to
RE-RUN the measurement that produced it and compare the two numbers — the
`two_part_verify` pattern this repo already uses. This file does exactly that and
prints, per row:

    gate | stored | re-run | MEASURE (the command a reader can run) | AGREE?

WHY THE MEASURE COLUMN IS THE POINT
-----------------------------------
    "the report must be MEASURABLE, not prose"

A number with no reproducing command cannot be audited. Every row therefore
carries the command that RE-PRODUCES it, so the reader is not asked to believe
the agent — they are given the instrument.

WHAT "AGREE" MEANS, AND WHAT IT DOES NOT
----------------------------------------
AGREE  : re-running the gate on the same subject yields the stored verdict.
DISAGREE: it does not. The reason is PRINTED (a moved subject, a changed row, a
          different reader) — a disagreement is NEVER rounded to an agreement.
`--tamper <row>` proves the DISAGREE path is reachable (a negative control): it
flips ONE stored verdict IN A TEMP COPY of the DB, so the check cannot silently
always print AGREE.

USAGE
  python scripts/qc_report_truth.py                 # last 20 qc_gate rows
  python scripts/qc_report_truth.py --limit 50
  python scripts/qc_report_truth.py --json
  python scripts/qc_report_truth.py --tamper 3      # negative control (temp copy)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"
# The gates a stored row can be RE-RUN on. `verdict` is derived from the others,
# so it is compared against a fresh arbitration rather than re-run in isolation.
REPLAYABLE = ("ontology", "5w1h", "middleware", "tdd", "boundary",
              "role_environment", "trace", "safety")


def measure_command(gate_key: str, subject: str) -> str:
    """The command that re-produces this row's number, as a string."""
    return "python scripts/qc_gate_verify.py --gate %s %s" % (
        gate_key,
        ("--file " + subject) if subject.endswith(".py") else
        ("--contract " + subject))


def _subject_of(row: sqlite3.Row) -> str:
    """The subject a stored row was measured on. Never invented.

    `qc_gate_runner.run_all` writes `gate_run_ref = 'GATE-<subject>'`, so the
    subject is CARRIED by the row — it is read, not guessed from prose.
    """
    ref = str(row["gate_run_ref"] or "")
    if ref.startswith("GATE-"):
        return ref[len("GATE-"):]
    return ""


def load_rows(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    """The stored `qc_gate` rows, newest first.

    MEASURED schema (2026-09-28): `qc_run` has `target` (= the gate key for a
    gate row) and `gate_run_ref` (= 'GATE-<subject>'), NOT `check_name`. The
    columns are read from PRAGMA, so an older DB without the gate columns is
    handled instead of crashing.
    """
    cols = {d[1] for d in conn.execute("PRAGMA table_info(qc_run)")}
    has_gate = {"gate_key", "gate_run_ref"} <= cols
    gk = "gate_key" if has_gate else "NULL"
    gr = "gate_run_ref" if has_gate else "NULL"
    sql = ("SELECT id AS rowid, target, verdict, reason, created_at, "
           "%s AS gate_key, %s AS gate_run_ref "
           "FROM qc_run WHERE tool='qc_gate' ORDER BY id DESC LIMIT ?" % (gk, gr))
    return list(conn.execute(sql, (int(limit),)))


def replay(conn: sqlite3.Connection, gate_key: str, subject: str) -> tuple[str, str]:
    """Re-run ONE gate on a subject. Returns `(verdict, why)`. Never raises."""
    import qc_gate_runner as R
    try:
        kind = "file" if subject.endswith(".py") else "contract"
        r = R.run_gate(conn, gate_key, subject_kind=kind, subject_ref=subject)
        return str(r.get("verdict") or "UNKNOWN"), str(r.get("detail") or "")
    except Exception as exc:
        return "UNKNOWN", "%s: %s" % (type(exc).__name__, exc)


def build_table(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = load_rows(conn, limit)
    out: list[dict] = []
    for r in rows:
        gk = str(r["gate_key"] or r["target"] or "")
        subj = _subject_of(r)
        stored = str(r["verdict"])
        if not subj or gk not in REPLAYABLE:
            out.append({"rowid": r["rowid"], "gate": gk, "subject": subj,
                        "stored": stored, "rerun": None,
                        "measure": "(subject not re-runnable from this row)",
                        "agree": None,
                        "note": "no subject captured -> cannot be re-measured"})
            continue
        fresh, why = replay(conn, gk, subj)
        out.append({"rowid": r["rowid"], "gate": gk, "subject": subj,
                    "stored": stored, "rerun": fresh,
                    "measure": measure_command(gk, subj),
                    "agree": (stored == fresh),
                    "note": "" if stored == fresh else str(why)[:120]})
    return out


def render(rows: list[dict]) -> str:
    agree = sum(1 for r in rows if r["agree"] is True)
    dis = sum(1 for r in rows if r["agree"] is False)
    skip = sum(1 for r in rows if r["agree"] is None)
    lines = ["qc_report_truth: %d rows | AGREE %d | DISAGREE %d | not re-runnable %d"
             % (len(rows), agree, dis, skip),
             "",
             "rowid gate      stored   re-run   AGREE?   MEASURE (re-produce it)"]
    for r in rows:
        ag = {True: "AGREE", False: "DISAGREE", None: "n/a"}[r["agree"]]
        lines.append("%5s %-9s %-8s %-8s %-8s %s"
                     % (r["rowid"], r["gate"], r["stored"],
                        r["rerun"] if r["rerun"] is not None else "-", ag,
                        r["measure"]))
    bad = [r for r in rows if r["agree"] is False]
    if bad:
        lines.append("")
        lines.append("DISAGREEMENTS (the report is NOT reproducible for these):")
        for r in bad:
            lines.append("  rowid %s %s stored=%s re-run=%s :: %s"
                         % (r["rowid"], r["gate"], r["stored"], r["rerun"],
                            r["note"]))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="re-run stored gate rows")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tamper", type=int, default=0,
                    help="NEGATIVE CONTROL: flip rowid N's stored verdict in a "
                         "TEMP COPY, proving DISAGREE is reachable")
    args = ap.parse_args()

    db = Path(args.db)
    tmp = None
    if args.tamper:
        tmpd = tempfile.mkdtemp(prefix="qc_truth_")
        tmp = Path(tmpd) / "agent.db"
        shutil.copy2(db, tmp)
        conn = sqlite3.connect(str(tmp), timeout=15)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id AS rowid, verdict FROM qc_run WHERE tool='qc_gate' "
            "ORDER BY id DESC LIMIT ?",
            (max(args.tamper, 1),))
        picked = list(cur)
        if not picked:
            print("nothing to tamper with")
            return 1
        target = picked[min(args.tamper, len(picked)) - 1]
        new = "FAIL" if str(target["verdict"]).upper() == "PASS" else "PASS"
        conn.execute("UPDATE qc_run SET verdict=? WHERE id=?",
                     (new, target["rowid"]))
        conn.commit()
        if not args.json:
            print("[NEGATIVE CONTROL] tampered rowid %s: %s -> %s (TEMP COPY)\n"
                  % (target["rowid"], target["verdict"], new))
    else:
        conn = sqlite3.connect(str(db), timeout=15)
        conn.row_factory = sqlite3.Row

    try:
        rows = build_table(conn, args.limit)
        if args.json:
            print(json.dumps({"rows": rows, "tampered": bool(args.tamper)},
                             indent=1, ensure_ascii=False))
        else:
            print(render(rows))
        # A DISAGREE is a REAL finding, so the exit code says so (unless we
        # asked for it on purpose with --tamper, which is a positive control).
        dis = sum(1 for r in rows if r["agree"] is False)
        if args.tamper:
            return 0 if dis else 1
        return 1 if dis else 0
    finally:
        conn.close()
        if tmp is not None:
            shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())