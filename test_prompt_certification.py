"""Guard: certification must require REPETITION, not one good sample.

THE BUG THIS PINS DOWN
---------------------
The first version of `verdict()` returned "certified" when a single seeded run
reached 100% on both splits. It did exactly that, and a 5-seed stress test then
showed the same variant scored 81-94% on train and hit 100% only for that one
seed. So the tool certified a favourable sample and called it a property of the
prompt — the precise defect this workset exists to remove, reproduced inside the
guard meant to prevent it.

These checks make that impossible to reintroduce silently.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")
import prompt_sweep as ps  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print("  %s  %s" % ("PASS" if cond else "FAIL", name))
    if not cond and detail:
        print("        %s" % detail)


def run(split, seed, bal, classes=("NO", "YES")):
    return {"split": split, "seed": seed, "balanced": bal, "classes": list(classes)}


def main() -> int:
    print("=== test_prompt_certification ===")

    # 1. a single perfect run must NOT certify
    v = ps.verdict({"balanced_accuracy_pct": 100.0, "discriminating": True},
                   {"balanced_accuracy_pct": 100.0, "discriminating": True})
    check("single 100%/100% run -> 'provisional', never 'certified'",
          v["state"] == "provisional", "got %r (%s)" % (v["state"], v["reason"]))
    check("and it says why a single run is not enough",
          "lucky" in v["reason"] or "not certified" in v["reason"].lower(),
          v["reason"])

    # 2. genuinely repeated holdout perfection DOES certify
    runs = [run("holdout", s, 100.0) for s in (1, 2, 3, 4, 5)]
    runs += [run("train", s, b) for s, b in ((1, 93.75), (2, 88.89), (3, 83.34),
                                             (4, 87.5), (5, 81.25))]
    c = ps.certify_repeated(runs)
    check("holdout 100% on all 5 seeds -> certified", c["state"] == "certified",
          "got %r (%s)" % (c["state"], c.get("reason")))
    check("certified reports the train RANGE (not a fake single train number)",
          c["detail"]["train_min"] == 81.25 and c["detail"]["train_max"] == 93.75,
          str(c["detail"]))

    # 3. imperfect holdout -> provisional, with the count shown
    runs2 = [run("holdout", 1, 100.0), run("holdout", 2, 100.0),
             run("holdout", 3, 87.5), run("holdout", 4, 100.0),
             run("holdout", 5, 100.0)]
    c2 = ps.certify_repeated(runs2)
    check("holdout 100% on 4/5 seeds -> provisional", c2["state"] == "provisional",
          "got %r" % c2["state"])
    check("provisional names the ratio", "4/5" in c2["reason"], c2["reason"])

    # 4. single-class holdout can never certify even at 100%
    runs3 = [run("holdout", s, 100.0, classes=("NO",)) for s in (1, 2, 3)]
    c3 = ps.certify_repeated(runs3)
    check("holdout 100% but single-class -> NOT certified",
          c3["state"] != "certified", "got %r" % c3["state"])

    # 5. no holdout runs supplied -> unverified, not certified
    c4 = ps.certify_repeated([run("train", 1, 100.0)])
    check("train-only runs -> unverified", c4["state"] == "unverified",
          "got %r" % c4["state"])
    check("empty run list -> unverified", ps.certify_repeated([])["state"] == "unverified")

    # 6. verdict() still catches overfit and void
    v_ov = ps.verdict({"balanced_accuracy_pct": 100.0, "discriminating": True},
                      {"balanced_accuracy_pct": 70.0, "discriminating": True})
    check("large train-over-holdout gap -> 'overfit'", v_ov["state"] == "overfit",
          "got %r" % v_ov["state"])
    v_void = ps.verdict({"balanced_accuracy_pct": 90.0, "discriminating": True},
                        {"balanced_accuracy_pct": 50.0, "discriminating": False})
    check("single-class run -> 'void'", v_void["state"] == "void",
          "got %r" % v_void["state"])

    # 7. the source must not reintroduce a one-shot certify path
    src = Path(ps.__file__).read_text(encoding="utf-8")
    check("verdict() does not return 'certified' at all",
          '"certified"' not in src.split("def certify_repeated")[0],
          "a one-shot certify path exists again")
    check("certify_repeated exists and is the only certifier",
          "def certify_repeated" in src)

    print()
    n_pass = sum(1 for _, p, _ in CHECKS if p)
    print("%d/%d checks passed" % (n_pass, len(CHECKS)))
    return 0 if n_pass == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
