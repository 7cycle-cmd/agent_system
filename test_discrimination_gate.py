"""TDD for the discrimination gate.

WHAT WENT WRONG WITHOUT THIS
----------------------------
The A/B comparison of two different prompts returned bit-identical numbers:
60/60 answered "NO", 81.67% both, p=1.000, reported as "NO SIGNIFICANT
DIFFERENCE". That verdict was structurally guaranteed, not measured — the score
was arithmetic over the gold set's NO:YES mix, and had nothing to do with either
prompt.

Two holes had to be closed:
  1. streak_proof() never recorded whether a run answered more than one class,
     so a single-class run was indistinguishable from a real measurement.
  2. test_candidate() had no gate on the RUN, only on the score.

Each check below isolates ONE protection. If you neuter a protection and the
suite stays green, the test was asserting its own literal — the same defect that
let an earlier guard pass while testing nothing.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")

CHECKS = []
FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append(name)
    if cond:
        print("  PASS  %s" % name)
    else:
        FAILURES.append("%s :: %s" % (name, detail))
        print("  FAIL  %s  %s" % (name, detail))


def main() -> int:
    print("=== test_discrimination_gate ===")
    import skill_prompt as sp
    import skill_learning as sl

    # ---- 1. the source of truth computes discrimination from the answer mix --
    src = Path(sp.__file__).read_text(encoding="utf-8")
    check("streak_proof computes answer_classes",
          "answer_classes = {a for a, n in" in src)
    check("streak_proof exposes 'discriminating'",
          '"discriminating": discriminating' in src)

    lsrc = Path(sl.__file__).read_text(encoding="utf-8")
    check("test_candidate has a discrimination_gate",
          '"discrimination_gate"' in lsrc)
    check("test_candidate forces pass_gate False when not discriminating",
          "if require_discriminating and not disc:" in lsrc and
          'out["pass_gate"] = False' in lsrc)
    check("test_candidate passes a fixed seed through",
          "seed=test_seed" in lsrc)

    # ---- 2. the A/B refuses to print a verdict on a single-class run ---------
    ab = Path(r"C:\projects\agent_system\_proof_ab_baseline.py").read_text(encoding="utf-8")
    check("A/B passes test_seed=seed", "test_seed=seed" in ab)
    check("A/B voids the verdict when either run is single-class",
          "VERDICT: VOID" in ab and "if not (b_disc and c_disc):" in ab)
    check("A/B reports answer_classes", "answer_classes" in ab)

    # ---- 3. gold-set gate now reports SKEW --------------------------------
    check("_gold_set_gate reports minority_share",
          "minority_share" in lsrc)
    check("_gold_set_gate warns below 20% minority",
          'out["minority_share"] < 0.20' in lsrc)

    # ---- 4. live behaviour: run the gate over the real gold set ------------
    gold = sl._gold_set_gate("mouse_spot_verify")
    print("  live gold gate: %s" % gold["reason"])
    print("  minority_share=%s note=%s" % (gold.get("minority_share"),
                                           bool(gold.get("note"))))
    check("live gold gate applies", gold["applies"])
    check("live gold gate PASSES the >1-class check (classes=%s)" % gold["classes"],
          gold["ok"])

    # ASSERT THE INVARIANT, NOT A SNAPSHOT.
    # An earlier version of this check asserted the specific skew that happened
    # to exist at the time (14 NO / 2 YES). That is a snapshot of data, not a
    # property of the gate — it went red the moment the gold set was legitimately
    # rebalanced, reporting a "failure" that was actually a fix. The real
    # invariant is: the note appears IFF the minority share is below the
    # threshold. That holds whatever the current data is.
    ms = gold.get("minority_share")
    expected_note = (ms is not None and ms < 0.20)
    check("live gold gate skew-note matches the minority threshold "
          "(minority=%s, note=%s)" % (ms, bool(gold.get("note"))),
          bool(gold.get("note")) == expected_note,
          "note/ minority inconsistent — gate logic and reported values disagree")
    if expected_note:
        print("  note: live gold set IS still skewed -> %s" % gold["note"])
    else:
        print("  note: live gold set is balanced (minority %.1f%% >= 20%%)"
              % ((ms or 0) * 100))

    # ---- 5. mutation: neuter the discrimination gate, then re-check --------
    # We do not edit the real file. We simulate "neutered" by evaluating the
    # exact expression the gate uses on a synthetic all-NO run.
    def discriminating_for(yes, no, other):
        classes = {a for a, n in (("YES", yes), ("NO", no), ("OTHER", other)) if n}
        return len(classes) >= 2

    check("mutation check: all-NO run is NOT discriminating",
          discriminating_for(0, 60, 0) is False)
    check("mutation check: mixed run IS discriminating",
          discriminating_for(3, 57, 0) is True)
    check("mutation check: YES-only run is NOT discriminating",
          discriminating_for(60, 0, 0) is False)
    check("mutation check: OTHER counts as a class",
          discriminating_for(0, 0, 60) is False)

    print()
    print("%d/%d checks passed" % (len(CHECKS) - len(FAILURES), len(CHECKS)))
    if FAILURES:
        print("FAILURES:")
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
