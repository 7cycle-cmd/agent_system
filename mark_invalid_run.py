"""P2: mark the invalid test run (id=1) without altering history.

THE PROBLEM
-----------
`skill_prompt_test_run` id=1 records, for mouse_spot_verify v1_strict:
    pass_gate=1, accuracy_pct=100.0, n_runs=3
That run was scored against a GOLD SET WITH A SINGLE EXPECTED CLASS (11 cases,
all `NO`). A prompt that always answers "NO" scores 100%, so that pass was never
earned — the skill was not shown to discriminate anything.

WHY NOT JUST DELETE IT
----------------------
The table is a record of what happened. Deleting it would erase the fact that a
false pass was recorded, which is exactly the evidence a future reader needs.
The run happened; the PROBLEM is that it was treated as valid.

WHAT THIS DOES
--------------
Appends the invalidity to the row rather than rewriting it:
  - `source` gets a marker suffix: "single_class_goldset_invalid"
  - a review-log entry on the governing contract records why
  - the original values (pass_gate, accuracy_pct, n_runs) are LEFT INTACT

An append-only correction. A reader can still see the original numbers AND the
explanation. Silently zeroing pass_gate would hide the incident.

Usage:
    .\\.venv\\Scripts\\python.exe mark_invalid_run.py            # dry run
    .\\.venv\\Scripts\\python.exe mark_invalid_run.py --apply
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DB = HERE / "agent.db"
MARKER = "single_class_goldset_invalid"
NOTE = ("INVALID: pass_gate=1 was scored against a gold set with a single "
        "expected class (all NO), so a prompt that always answers NO would "
        "score 100%. The pass was never earned. Kept for history; do not cite "
        "as evidence of correctness.")


def _classes_at_run(conn: sqlite3.Connection, skill_key: str, run_at: str) -> dict:
    """Distinct expected classes that existed WHEN THE RUN HAPPENED.

    Using the CURRENT gold set is wrong and produced a false "(none)" result:
    after discriminating cases are added, an old run scored on a single-class set
    suddenly looks fine because the set has since grown. Invalidity is a property
    of the moment of the run, so the query must be time-bounded.

    A case with no created_at is treated as having always existed (conservative:
    it counts toward the classes available at the time).
    """
    rows = conn.execute(
        "SELECT expected, created_at FROM skill_prompt_case WHERE skill_key=?",
        (skill_key,),
    ).fetchall()
    out: dict = {}
    for r in rows:
        exp = str(r[0])
        created = str(r[1] or "")
        if created and run_at and created > run_at:
            continue  # did not exist yet
        out[exp] = out.get(exp, 0) + 1
    return out


def _gold_classes(conn: sqlite3.Connection, skill_key: str) -> dict:
    rows = conn.execute(
        "SELECT expected FROM skill_prompt_case WHERE skill_key=?", (skill_key,)
    ).fetchall()
    out: dict = {}
    for r in rows:
        k = str(r[0])
        out[k] = out.get(k, 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--run-id", type=int, default=None,
                    help="mark one specific run id (explicit, auditable)")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    print("=== runs whose gold set had ONE class AT THE TIME OF THE RUN ===")
    targets = []
    for r in conn.execute("SELECT * FROM skill_prompt_test_run ORDER BY id"):
        d = dict(r)
        if args.run_id is not None and int(d["id"]) != int(args.run_id):
            continue
        classes = _classes_at_run(conn, d["skill_key"], d.get("created_at") or "")
        now_classes = _gold_classes(conn, d["skill_key"])
        if len(classes) <= 1:
            targets.append((d, classes, now_classes))
            already = MARKER in (d.get("source") or "")
            print("  id=%s skill=%-20s pass_gate=%s acc=%-6s at=%s"
                  % (d["id"], d["skill_key"], d["pass_gate"], d["accuracy_pct"],
                     d["created_at"]))
            print("       classes at run time = %s   (now = %s)%s"
                  % (classes, now_classes, "   [already marked]" if already else ""))
        else:
            print("  id=%s skill=%-20s skipped (had %d classes at run time)"
                  % (d["id"], d["skill_key"], len(classes)))

    if not targets:
        print("  (none)")
        conn.close()
        return 0

    print()
    if not args.apply:
        print("DRY RUN — re-run with --apply to mark %d row(s)." % len(targets))
        conn.close()
        return 0

    print("=== marking (append-only: original values preserved) ===")
    for d, classes, now_classes in targets:
        src = str(d.get("source") or "")
        if MARKER in src:
            print("  id=%s already marked, skipped" % d["id"])
            continue
        new_src = (src + "|" + MARKER) if src else MARKER
        conn.execute(
            "UPDATE skill_prompt_test_run SET source=? WHERE id=?",
            (new_src, d["id"]),
        )
        print("  id=%s source -> %s" % (d["id"], new_src))
        # Append an explanation to the review log of the governing contract, so
        # the reason lives with the contract rather than only in this script.
        try:
            import skill_contract_store as scs
            contracts = [c for c in scs.list_contracts(db_path=args.db)
                         if str(c.get("skill_key")) == str(d["skill_key"])]
            if contracts:
                cid = contracts[0]["contract_id"]
                scs.append_review_log(
                    cid,
                    gap="test_run id=%s was scored on a single-class gold set"
                        % d["id"],
                    revision=NOTE,
                    db_path=args.db,
                )
                print("       review log appended to %s" % cid)
        except Exception as e:
            print("       review log skipped (%s: %s)" % (type(e).__name__, e))
    conn.commit()

    print()
    print("=== verification ===")
    for r in conn.execute("SELECT id,skill_key,pass_gate,accuracy_pct,source "
                          "FROM skill_prompt_test_run ORDER BY id"):
        d = dict(r)
        print("  id=%s pass_gate=%s acc=%s source=%s"
              % (d["id"], d["pass_gate"], d["accuracy_pct"], d["source"]))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
