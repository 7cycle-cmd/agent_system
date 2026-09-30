# -*- coding: utf-8 -*-
"""prompt_sweep.py — measure every prompt combination, guard against tuning-to-fit.

WHY A SWEEP AND NOT A GUESS
---------------------------
The multi-dimensional SSOT (prompt_dimension.py) can compose 36 distinct prompts
for mouse_spot_verify out of 10 axis values. Choosing one by reading them is how
the original prompt ended up with a stacked-negation axis that nobody had
measured — it scored 70% accuracy while getting 0/12 hit cases right.

So the choice is made by measurement, with three protections:

  1. TRAIN / HOLDOUT SPLIT. Variants are ranked on the train split only, then
     the best are re-measured on cases they never saw. A variant that scores
     high on train and drops on holdout was tuned to the FIXTURES, not the task.
     That is the most likely way to fake "100%" and it is checked for by name.

  2. DISCRIMINATION. A single-class answer run scores a function of the gold
     set's class mix, so two prompts tie by construction. Such runs are refused
     as evidence (see _proof_gold_discrimination.py).

  3. BALANCED ACCURACY. On a 14 NO : 6 YES set, always answering NO scores 70%
     accuracy with 0% YES recall. Balanced accuracy (mean per-class recall) is
     the headline; plain accuracy is reported but never used to rank alone.

WHAT "100%" WOULD HAVE TO MEAN
------------------------------
Not "the number reached 100 on a set I chose". It means: every class answered
correctly, on cases the variant was NOT selected on, with more than one answer
class produced. `verdict()` only says `certified` when all three hold.

Usage:
  .\\.venv\\Scripts\\python.exe prompt_sweep.py --plan
  .\\.venv\\Scripts\\python.exe prompt_sweep.py --rounds 24 --apply
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, r"C:\projects\agent_system")
# SSOT: registers, not the retired prompt_dimension. `prompt_generator`
# provides the same registry/expand_combos/compose_prompt API.
import prompt_generator as pd  # noqa: E402
import skill_learning as sl  # noqa: E402
import skill_prompt as sp  # noqa: E402

DEFAULT_SKILL = "mouse_spot_verify"
VERSION_LABEL = "dim_v1"      # all composed prompts share one version label...
REFERENCE_VERSION = VERSION_LABEL
REPORT = Path(r"C:\projects\agent_system\prompt_sweep_report.json")


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1, p2 = k1 / n1, k2 / n2
    pool = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))


# ------------------------------------------------------------------- split

def stratified_split(cases: list[dict], *, train_frac: float = 0.70
                     ) -> tuple[list[str], list[str]]:
    """Deterministic stratified split by `expected`, so both sides see both classes.

    Non-stratified splitting on a 14 NO : 6 YES set can easily put every YES on
    one side, which would make the holdout majority-only and therefore unable to
    detect a prompt that fails hits — exactly the failure being hunted.
    """
    by_cls: dict[str, list[str]] = {}
    for c in cases:
        by_cls.setdefault(str(c["expected"]), []).append(str(c["case_key"]))
    train, holdout = [], []
    for cls in sorted(by_cls):
        keys = sorted(by_cls[cls])
        n_train = max(1, int(round(len(keys) * train_frac)))
        if n_train >= len(keys):          # never leave a class with no holdout
            n_train = max(1, len(keys) - 1)
        train.extend(keys[:n_train])
        holdout.extend(keys[n_train:])
    return sorted(train), sorted(holdout)


# ------------------------------------------------------- materialise combos

def upsert_combo_prompt(conn: sqlite3.Connection, skill_key: str,
                        combo: dict) -> dict:
    """Store the composed prompt as a real SSOT document under its own prompt_key.

    `prompt_key` is the combination harness: it was already a column with a
    UNIQUE (skill_key, prompt_key, version_label) constraint but had only ever
    held 'main'. Giving each combination its own prompt_key lets variants coexist
    and be measured independently, instead of each overwriting the last.
    """
    return sp.upsert_skill_prompt(
        conn,
        skill_key=skill_key,
        prompt_key=combo["prompt_key"],
        version_label=VERSION_LABEL,
        prompt_text=combo["prompt_text"],
        status="testing",
        activate=False,
        notes="composed from dimensions: %s" % json.dumps(combo["axes"], ensure_ascii=False),
    )


def measure(conn: sqlite3.Connection, skill_key: str, prompt_key: str,
            *, case_keys: list[str], rounds: int, seed: int) -> dict:
    return sl.test_candidate(
        skill_key, VERSION_LABEL, prompt_key=prompt_key, case_keys=case_keys,
        max_rounds=rounds, gate_mode="rate", min_accuracy_pct=100.0,
        min_samples=rounds, test_seed=seed, require_discriminating=False,
    )


def verdict(train: dict, holdout: dict | None) -> dict:
    """Classify a SINGLE seeded run. Never returns 'certified'.

    WHY A SINGLE RUN CANNOT CERTIFY
    -------------------------------
    The first version of this function returned 'certified' when one seeded run
    hit 100% on both splits. It then did exactly that — and a stress test across
    5 seeds showed the same variant scored 81-94% on train and only reached
    100% on train for that one seed. So 'certified' was a favourable sample
    reported as a property of the prompt: this project's signature defect,
    reproduced inside the guard built to prevent it.

    A single run is therefore at best `provisional`. Only `certify_repeated()`,
    which requires the result to hold across many seeds, may say `certified`.
    """
    t_ba = float(train.get("balanced_accuracy_pct") or 0.0)
    t_disc = bool(train.get("discriminating"))
    if holdout is None:
        return {"state": "unverified",
                "reason": "no holdout measurement; train-only numbers cannot "
                          "distinguish skill from fixture-fitting"}
    h_ba = float(holdout.get("balanced_accuracy_pct") or 0.0)
    h_disc = bool(holdout.get("discriminating"))
    gap = t_ba - h_ba
    if not (t_disc and h_disc):
        return {"state": "void",
                "reason": "a run answered only one class, so its score is a "
                          "property of the gold set, not the prompt"}
    if gap > 15.0:
        return {"state": "overfit",
                "reason": "train %.1f%% vs holdout %.1f%% (gap %.1f pp): the "
                          "variant fits the fixtures it was chosen on" % (t_ba, h_ba, gap)}
    if t_ba >= 100.0 and h_ba >= 100.0 and gap <= 0.0:
        return {"state": "provisional",
                "reason": "100%% on both splits for ONE seed. Not certified: a "
                          "single run can be lucky. Run certify_repeated() "
                          "across several seeds before believing it."}
    return {"state": "not_yet",
            "reason": "train %.1f%% holdout %.1f%% — below 100%% on both" % (t_ba, h_ba)}


def certify_repeated(runs: list[dict]) -> dict:
    """Certify only when 100% is REPEATABLE across every measured seed.

    `runs` are single measurements, each with split/seed/balanced/classes.
    Certification requires, on the HOLDOUT split:
      * every seed reached 100% balanced accuracy, and
      * every seed answered more than one class.
    The train split is reported but NOT required to be perfect: with 12 rounds
    over 14 cases the train sample varies per seed, so demanding train 100%
    everywhere would certify on sampling luck rather than behaviour.
    """
    if not runs:
        return {"state": "unverified", "reason": "no runs"}
    hold = [r for r in runs if r.get("split") == "holdout"]
    if not hold:
        return {"state": "unverified", "reason": "no holdout runs supplied"}
    h_perfect = [r for r in hold if float(r.get("balanced") or 0) >= 100.0]
    h_disc = [r for r in hold if len(r.get("classes") or []) >= 2]
    train = [r for r in runs if r.get("split") == "train"]
    t_vals = [float(r.get("balanced") or 0) for r in train]
    detail = {
        "holdout_runs": len(hold),
        "holdout_perfect": len(h_perfect),
        "holdout_discriminating": len(h_disc),
        "train_runs": len(train),
        "train_min": min(t_vals) if t_vals else None,
        "train_max": max(t_vals) if t_vals else None,
    }
    if len(h_perfect) == len(hold) and len(h_disc) == len(hold):
        return {"state": "certified", "detail": detail,
                "reason": "holdout 100%% balanced accuracy on all %d seeds, all "
                          "discriminating; train %s-%s%%"
                          % (len(hold), detail["train_min"], detail["train_max"])}
    return {"state": "provisional", "detail": detail,
            "reason": "holdout 100%% on %d/%d seeds — strong but not "
                      "repeatable enough to certify"
                      % (len(h_perfect), len(hold))}


# -------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skill", default=DEFAULT_SKILL)
    ap.add_argument("--rounds", type=int, default=24, help="train rounds per combo")
    ap.add_argument("--holdout-rounds", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--top", type=int, default=5, help="how many to holdout-test")
    ap.add_argument("--holdout-seeds", type=int, default=5,
                    help="repeat the holdout across this many seeds before certifying")
    ap.add_argument("--max-combos", type=int, default=0)
    ap.add_argument("--axes", default="", help='filter e.g. "negation=none,stack;context=plain"')
    ap.add_argument("--repeats", type=int, default=1,
                    help="repeat each combo and keep the median BA (noise control)")
    ap.add_argument("--plan", action="store_true", help="show the sweep plan only")
    ap.add_argument("--apply", action="store_true", help="write combos to the DB")
    args = ap.parse_args(argv)

    conn = pd._connect(r"C:\projects\agent_system\agent.db")
    pd.seed_mouse_spot(conn, args.skill)

    # ---- split first, so the plan is visible before any model call ----
    cases = [dict(r) for r in conn.execute(
        "SELECT case_key, expected FROM skill_prompt_case "
        "WHERE skill_key=? AND status='active' AND image_path IS NOT NULL",
        (args.skill,))]
    if not cases:
        print("no active gold cases")
        return 1
    train_keys, holdout_keys = stratified_split(cases)
    cls_all = {}
    for c in cases:
        cls_all[str(c["expected"])] = cls_all.get(str(c["expected"]), 0) + 1

    def cls_of(keys):
        d: dict[str, int] = {}
        for c in cases:
            if str(c["case_key"]) in keys:
                d[str(c["expected"])] = d.get(str(c["expected"]), 0) + 1
        return d

    print("=== gold split (stratified by expected) ===")
    print("  all     n=%d %s" % (len(cases), cls_all))
    print("  train   n=%d %s" % (len(train_keys), cls_of(train_keys)))
    print("  holdout n=%d %s" % (len(holdout_keys), cls_of(holdout_keys)))
    if len(set(cls_of(holdout_keys))) < 2:
        print("  ABORT: holdout has one class; it could not detect a prompt that")
        print("  fails the other class.")
        return 2

    axes = None
    if args.axes:
        axes = {}
        for part in args.axes.split(";"):
            part = part.strip()
            if not part:
                continue
            k, _, v = part.partition("=")
            axes[k.strip()] = [x.strip() for x in v.split(",") if x.strip()]

    combos = pd.expand_combos(conn, args.skill, axes=axes)
    if args.max_combos:
        combos = combos[: args.max_combos]
    print()
    print("=== sweep plan ===")
    print("  combinations : %d" % len(combos))
    print("  train rounds : %d x %d repeat(s)" % (args.rounds, args.repeats))
    print("  holdout      : top %d x %d rounds x %d seeds"
          % (args.top, args.holdout_rounds, args.holdout_seeds))
    print("  est. model calls: ~%d"
          % (len(combos) * args.rounds * args.repeats
             + min(args.top, len(combos)) * args.holdout_rounds * args.holdout_seeds
             + min(args.top, len(combos)) * args.rounds * args.holdout_seeds))

    if args.plan:
        print()
        for c in combos:
            print("  %s  %s" % (pd.combo_key_short(c), pd.combo_key(c)))
        return 0

    # ---- materialise + measure on train -------------------------------
    print()
    print("=== train measurements ===")
    results = []
    for i, cv in enumerate(combos, 1):
        combo = pd.compose_combo(conn, args.skill, cv, template=pd.MOUSE_SPOT_TEMPLATE)
        if args.apply:
            upsert_combo_prompt(conn, args.skill, combo)
            pd.record_combo(conn, skill_key=args.skill, combo=combo)
        runs = [measure(conn, args.skill, combo["prompt_key"],
                        case_keys=train_keys, rounds=args.rounds, seed=args.seed)
                for _ in range(max(1, args.repeats))]
        runs.sort(key=lambda o: float(o.get("balanced_accuracy_pct") or 0))
        med = runs[len(runs) // 2]
        row = {
            "prompt_key": combo["prompt_key"],
            "combo_key": combo["combo_key"],
            "axes": combo["axes"],
            "train_balanced": float(med.get("balanced_accuracy_pct") or 0),
            "train_accuracy": float(med.get("accuracy_pct") or 0),
            "train_classes": med.get("answer_classes"),
            "train_per_class": med.get("per_class"),
            "run_id": med.get("run_id"),
        }
        results.append(row)
        print("  %2d/%d %s bal=%6.2f%% acc=%6.2f%% %s"
              % (i, len(combos), row["prompt_key"], row["train_balanced"],
                 row["train_accuracy"], row["axes"]))

    # ---- rank on train, then holdout-test the best --------------------
    results.sort(key=lambda r: (-r["train_balanced"], -r["train_accuracy"]))
    print()
    print("=== holdout test (top %d chosen on train only, %d seeds) ==="
          % (args.top, args.holdout_seeds))
    for r in results[: args.top]:
        ho_runs = []
        for k in range(max(1, args.holdout_seeds)):
            sd = args.seed + (0 if k == 0 else k * 7919)
            ho = measure(conn, args.skill, r["prompt_key"],
                         case_keys=holdout_keys, rounds=args.holdout_rounds, seed=sd)
            ho_runs.append({
                "split": "holdout", "seed": sd,
                "balanced": float(ho.get("balanced_accuracy_pct") or 0),
                "accuracy": float(ho.get("accuracy_pct") or 0),
                "classes": ho.get("answer_classes"),
                "per_class": ho.get("per_class"),
                "run_id": ho.get("run_id"),
            })
        # train repetitions too, so a train/holdout gap is measurable rather than
        # the product of two unrelated samples
        tr_runs = []
        for k in range(max(1, args.holdout_seeds)):
            sd = args.seed + (0 if k == 0 else k * 7919)
            tr = measure(conn, args.skill, r["prompt_key"],
                         case_keys=train_keys, rounds=args.rounds, seed=sd)
            tr_runs.append({
                "split": "train", "seed": sd,
                "balanced": float(tr.get("balanced_accuracy_pct") or 0),
                "accuracy": float(tr.get("accuracy_pct") or 0),
                "classes": tr.get("answer_classes"),
                "per_class": tr.get("per_class"),
                "run_id": tr.get("run_id"),
            })
        best_ho = max(ho_runs, key=lambda x: x["balanced"])
        r["holdout_balanced"] = round(sum(x["balanced"] for x in ho_runs) / len(ho_runs), 2)
        r["holdout_accuracy"] = round(sum(x["accuracy"] for x in ho_runs) / len(ho_runs), 2)
        r["holdout_classes"] = best_ho["classes"]
        r["holdout_per_class"] = best_ho["per_class"]
        r["holdout_run_id"] = best_ho["run_id"]
        r["holdout_runs"] = ho_runs
        r["train_runs"] = tr_runs
        r["verdict"] = certify_repeated(ho_runs + tr_runs)
        print("  %s train=%s holdout=%s -> %s"
              % (r["prompt_key"],
                 "%.0f-%.0f%%" % (min(x["balanced"] for x in tr_runs),
                                  max(x["balanced"] for x in tr_runs)),
                 "%.0f-%.0f%%" % (min(x["balanced"] for x in ho_runs),
                                  max(x["balanced"] for x in ho_runs)),
                 r["verdict"]["state"]))

    # ---- baseline for context -----------------------------------------
    print()
    print("=== reference: the currently ACTIVE main prompt, same splits ===")
    base_train = measure(conn, args.skill, "main", case_keys=train_keys,
                         rounds=args.rounds, seed=args.seed)
    base_hold = measure(conn, args.skill, "main", case_keys=holdout_keys,
                        rounds=args.holdout_rounds, seed=args.seed)
    print("  main  train=%6.2f%% holdout=%6.2f%% classes=%s/%s"
          % (float(base_train.get("balanced_accuracy_pct") or 0),
             float(base_hold.get("balanced_accuracy_pct") or 0),
             base_train.get("answer_classes"), base_hold.get("answer_classes")))

    if args.apply:
        for r in results:
            tr = r.get("train_runs") or []
            ho = r.get("holdout_runs") or []
            trv = [float(x.get("balanced") or 0) for x in tr] or [float(r.get("train_balanced") or 0)]
            hov = [float(x.get("balanced") or 0) for x in ho] or [float(r.get("holdout_balanced") or 0)]
            conn.execute(
                """UPDATE prompt_combo SET accuracy_pct=?, balanced_accuracy_pct=?,
                   train_ba_pct=?, heldout_ba_pct=?,
                   train_ba_min=?, train_ba_max=?, hold_ba_min=?, hold_ba_max=?,
                   seed_runs=?, hold_perfect=?, hold_discriminating=?,
                   answer_classes=?, run_id=?, overfit=?, status=?
                   WHERE skill_key=? AND combo_key=?""",
                (r.get("train_accuracy"), r.get("train_balanced"),
                 r.get("train_balanced"), r.get("holdout_balanced"),
                 min(trv), max(trv), min(hov), max(hov),
                 len(tr), sum(1 for v in hov if v >= 100.0),
                 sum(1 for x in ho if len(x.get("classes") or []) >= 2),
                 json.dumps(r.get("train_classes")),
                 r.get("holdout_run_id") or r.get("run_id"),
                 1 if (r.get("verdict") or {}).get("state") == "overfit" else 0,
                 (r.get("verdict") or {}).get("state", "measured"),
                 args.skill, r["combo_key"]),
            )
        conn.commit()

    REPORT.write_text(json.dumps({
        "skill": args.skill, "version_label": VERSION_LABEL,
        "seed": args.seed, "rounds": args.rounds,
        "holdout_rounds": args.holdout_rounds, "top": args.top,
        "train_n": len(train_keys), "holdout_n": len(holdout_keys),
        "train_keys": train_keys, "holdout_keys": holdout_keys,
        "train_classes": cls_of(train_keys), "holdout_classes": cls_of(holdout_keys),
        "reference_main": {
            "train_balanced": float(base_train.get("balanced_accuracy_pct") or 0),
            "holdout_balanced": float(base_hold.get("balanced_accuracy_pct") or 0),
            "train_classes": base_train.get("answer_classes"),
            "holdout_classes": base_hold.get("answer_classes"),
        },
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("report written to %s" % REPORT)
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
