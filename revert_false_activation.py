"""REVERT the accidental activation caused by the E3b probe.

WHAT HAPPENED
-------------
`_proof_e2_e3_loop.py` E3b inserted a FAKE `pass_gate=1` test-run row to prove
the guard layer is not a blanket blocker. That fabricated evidence satisfied
guard 1, and `merge_candidate()` then really activated `cand_20260920_041356`
and deprecated `v1_strict`.

The probe row was deleted afterwards, but the ACTIVATION is a separate effect
and remained. So the DB currently claims a candidate is live on the strength of
evidence that no longer exists — precisely the false pass this workset is about.

REVERT
------
  cand_20260920_041356 : active -> draft   (it never earned the gate)
  v1_strict            : deprecated -> active  (restore the last EARNED version)

Idempotent: re-running reports "already reverted" rather than toggling.
Use --apply to write; default is a dry run.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

DB = r"C:\projects\agent_system\agent.db"
SKILL = "mouse_spot_verify"
CAND = "cand_20260920_041356"
RESTORE = "v1_strict"
REASON = "revert: activated by a fabricated pass_gate=1 probe row (E3b testing)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db", default=DB)
    args = ap.parse_args(argv)

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row

    print("=== before ===")
    before = [
        dict(r)
        for r in c.execute(
            "SELECT id,version_label,status FROM skill_prompt_ssot "
            "WHERE skill_key=? ORDER BY id", (SKILL,))
    ]
    for r in before:
        print("  %-26s %s" % (r["version_label"], r["status"]))

    active = [r for r in before if r["status"] == "active"]
    cand = [r for r in before if r["version_label"] == CAND]
    restore = [r for r in before if r["version_label"] == RESTORE]

    if not cand or cand[0]["status"] != "active":
        print()
        print("already reverted: %s is not active" % CAND)
        c.close()
        return 0

    print()
    print("=== planned revert ===")
    print("  %s : %s -> draft" % (CAND, cand[0]["status"]))
    print("  %s : %s -> active" % (RESTORE, restore[0]["status"] if restore else "?"))
    print("  reason: %s" % REASON)

    if not args.apply:
        print()
        print("DRY RUN — re-run with --apply.")
        c.close()
        return 0

    print()
    print("=== applying ===")
    # Deprecate the wrongly-activated candidate rather than deleting it: its
    # history (and the proof it was rewritten from a lesson) stays visible.
    c.execute(
        "UPDATE skill_prompt_ssot SET status='deprecated', updated_at=? "
        "WHERE skill_key=? AND version_label=?",
        ("2026-09-19 20:25:34", SKILL, CAND),
    )
    # Restore the last version that actually earned activation.
    c.execute(
        "UPDATE skill_prompt_ssot SET status='active', updated_at=? "
        "WHERE skill_key=? AND version_label=?",
        ("2026-09-19 20:25:34", SKILL, RESTORE),
    )
    # Record it: a silent revert would hide that a false activation happened.
    try:
        import skill_contract_store as scs
        contracts = [x for x in scs.list_contracts(db_path=args.db)
                     if str(x.get("skill_key")) == SKILL]
        if contracts:
            scs.append_review_log(
                contracts[0]["contract_id"],
                gap="candidate was activated on fabricated evidence",
                revision=REASON,
                db_path=args.db,
            )
            print("  review log appended to", contracts[0]["contract_id"])
    except Exception as e:
        print("  review log skipped (%s: %s)" % (type(e).__name__, e))
    c.commit()

    print()
    print("=== after ===")
    for r in c.execute(
        "SELECT version_label,status FROM skill_prompt_ssot WHERE skill_key=? "
        "ORDER BY id", (SKILL,)
    ):
        d = dict(r)
        print("  %-26s %s" % (d["version_label"], d["status"]))

    act = c.execute(
        "SELECT version_label FROM skill_prompt_ssot WHERE skill_key=? "
        "AND status='active'", (SKILL,)).fetchall()
    ok = [dict(x)["version_label"] for x in act] == [RESTORE]
    print()
    print("  active == [%s] ? %s" % (RESTORE, ok))
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
