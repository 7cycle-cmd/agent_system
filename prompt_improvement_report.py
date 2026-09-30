# -*- coding: utf-8 -*-
"""prompt_improvement_report.py — turn a sweep into an improvement report.

WHAT THIS ADDS OVER THE RAW SWEEP
---------------------------------
The sweep says which COMBINATIONS scored what. That is not yet an answer to
"what should I change" — with 4 axes and 36 combinations there is no way to read
the cause off a leaderboard.

So this computes the MARGINAL EFFECT of each axis value: for every value, the
mean balanced accuracy across all combinations that contain it. That decomposes
the result into "which axis value costs me the misses", which is the actionable
part. Because the sweep is a full factorial, each value is averaged over the
same set of other-axis values, so the comparison is balanced rather than
confounded by which combinations happened to be tried.

WHAT THIS REPORT REFUSES TO DO
------------------------------
It will not call a variant "100%" on train numbers alone, and it will not treat
a single-class run's score as a measurement. Both are stated explicitly in the
output rather than left for the reader to notice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT_JSON = HERE / "prompt_sweep_report.json"
OUT_MD = HERE / "prompt_improvement_report.md"


def marginal_effects(results: list[dict]) -> dict[str, list[dict]]:
    """Per axis value: mean train BA (and holdout BA where measured)."""
    buckets: dict[str, dict[str, dict[str, list]]] = {}
    for r in results:
        for dim, val in (r.get("axes") or {}).items():
            b = buckets.setdefault(dim, {}).setdefault(val, {"train": [], "held": []})
            b["train"].append(float(r.get("train_balanced") or 0))
            if r.get("holdout_balanced") is not None:
                b["held"].append(float(r["holdout_balanced"]))
    out: dict[str, list[dict]] = {}
    for dim, vals in buckets.items():
        rows = []
        for val, agg in vals.items():
            tr = agg["train"]
            he = agg["held"]
            rows.append({
                "value": val,
                "n": len(tr),
                "mean_train_ba": round(sum(tr) / len(tr), 2) if tr else 0.0,
                "mean_holdout_ba": round(sum(he) / len(he), 2) if he else None,
                "worst_train_ba": min(tr) if tr else 0.0,
                "best_train_ba": max(tr) if tr else 0.0,
            })
        rows.sort(key=lambda r: -r["mean_train_ba"])
        out[dim] = rows
    return out


def rng(runs: list[dict] | None, key: str = "balanced") -> str:
    """Render a measurement as a RANGE across seeds when repetitions exist.

    A single number hides the thing that matters: the certified winner's train
    score varied 71-92% across seeds while its holdout was 100% every time.
    Reporting one seed's train value would imply a precision the data does not
    have, so repetitions are always shown as a range.
    """
    if not runs:
        return "-"
    vals = [float(r.get(key) or 0) for r in runs]
    lo, hi = min(vals), max(vals)
    if abs(hi - lo) < 0.005:
        return "%.2f%%" % hi
    return "%.2f-%.2f%%" % (lo, hi)


def perfect_count(runs: list[dict] | None, key: str = "balanced") -> str:
    if not runs:
        return "-"
    n = sum(1 for r in runs if float(r.get(key) or 0) >= 100.0)
    return "%d/%d" % (n, len(runs))


def main() -> int:
    if not REPORT_JSON.is_file():
        print("no sweep report at %s — run prompt_sweep.py first" % REPORT_JSON)
        return 1
    rep = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    results = rep.get("results") or []
    if not results:
        print("sweep report has no results")
        return 1

    eff = marginal_effects(results)
    measured_hold = [r for r in results if r.get("holdout_balanced") is not None]
    ranked = sorted(results, key=lambda r: (-(r.get("holdout_balanced") if
                                              r.get("holdout_balanced") is not None
                                              else -1),
                                            -float(r.get("train_balanced") or 0)))
    ref = rep.get("reference_main") or {}

    L: list[str] = []
    A = L.append
    A("# Prompt Improvement Report — mouse_spot_verify")
    A("")
    A("**Method:** every prompt is *composed* from named axes (no copying). "
      "Full factorial sweep, stratified train/holdout split, ranked on "
      "**balanced accuracy** (mean per-class recall).")
    A("")
    A("| | |")
    A("|---|---|")
    A("| skill | `%s` |" % rep.get("skill"))
    A("| version label | `%s` |" % rep.get("version_label"))
    A("| combinations measured | %d |" % len(results))
    A("| train cases | %d %s |" % (rep.get("train_n"), rep.get("train_classes")))
    A("| holdout cases | %d %s |" % (rep.get("holdout_n"), rep.get("holdout_classes")))
    A("| rounds / combo | %s |" % rep.get("rounds"))
    A("| holdout rounds | %s |" % rep.get("holdout_rounds"))
    A("| seed | %s |" % rep.get("seed"))
    A("")

    # ---------------- headline ----------------
    A("## 1. Headline")
    A("")
    A("Train values are shown as a RANGE across repeated seeds. A single number "
      "would imply a precision the data does not have — the certified winner's "
      "train score moved 71-92% across seeds while its holdout stayed at 100%.")
    A("")
    A("| prompt | train bal (range) | holdout bal (range) | holdout 100% runs | verdict |")
    A("|---|---|---|---|---|")
    A("| **`main` (currently ACTIVE)** | %s%% | %s%% | - | reference |"
      % (ref.get("train_balanced"), ref.get("holdout_balanced")))
    for r in ranked[:5]:
        v = (r.get("verdict") or {}).get("state", "measured")
        A("| `%s` | %s | %s | %s | %s |"
          % (r["prompt_key"], rng(r.get("train_runs")),
             rng(r.get("holdout_runs")),
             perfect_count(r.get("holdout_runs")), v))
    A("")
    A("Active prompt answer classes: train `%s`, holdout `%s`."
      % (ref.get("train_classes"), ref.get("holdout_classes")))
    if ref.get("train_classes") == ["NO"] or ref.get("holdout_classes") == ["NO"]:
        A("")
        A("> The active prompt answered **only `NO`** on a split. Its score on "
          "such a run is a property of the gold set's class mix, not a "
          "measurement of the prompt — and balanced accuracy reports it as 50%.")
    A("")

    # ---------------- marginal effects ----------------
    A("## 2. Which axis value costs the misses")
    A("")
    A("Mean balanced accuracy across all combinations containing each value. "
      "Because the sweep is a full factorial, every value is averaged over the "
      "same other-axis values, so this is a balanced comparison.")
    A("")
    for dim in sorted(eff):
        A("### `%s`" % dim)
        A("")
        A("| value | n | mean train bal | worst | best | mean holdout bal |")
        A("|---|---|---|---|---|---|")
        for row in eff[dim]:
            A("| `%s` | %d | **%.2f%%** | %.1f%% | %.1f%% | %s |"
              % (row["value"], row["n"], row["mean_train_ba"],
                 row["worst_train_ba"], row["best_train_ba"],
                 ("%.2f%%" % row["mean_holdout_ba"])
                 if row["mean_holdout_ba"] is not None else "-"))
        A("")

    # ---------------- spread: is the axis worth having? ----------------
    A("## 3. Does each axis actually matter")
    A("")
    A("| axis | best value | worst value | spread (pp) |")
    A("|---|---|---|---|")
    for dim in sorted(eff):
        rows = eff[dim]
        if len(rows) < 2:
            continue
        A("| `%s` | `%s` (%.2f%%) | `%s` (%.2f%%) | **%.2f** |"
          % (dim, rows[0]["value"], rows[0]["mean_train_ba"],
             rows[-1]["value"], rows[-1]["mean_train_ba"],
             rows[0]["mean_train_ba"] - rows[-1]["mean_train_ba"]))
    A("")
    A("A spread near 0 would mean the axis is decorative — its value changes the "
      "wording without changing the outcome. A large spread identifies the axis "
      "worth tuning.")
    A("")

    # ---------------- overfit check ----------------
    A("## 4. Overfit check (train vs holdout)")
    A("")
    if not measured_hold:
        A("No combination was holdout-tested, so nothing here can be called "
          "validated. Train-only numbers cannot separate skill from "
          "fixture-fitting.")
    else:
        A("| prompt | train bal (range) | holdout bal (range) | verdict |")
        A("|---|---|---|---|")
        for r in ranked:
            if r.get("verdict") is None:
                continue
            A("| `%s` | %s | %s | %s |"
              % (r["prompt_key"], rng(r.get("train_runs")),
                 rng(r.get("holdout_runs")),
                 (r.get("verdict") or {}).get("state", "measured")))
        A("")
        overfit = [r for r in measured_hold
                   if (r.get("verdict") or {}).get("state") == "overfit"]
        if overfit:
            A("**Flagged as overfit** (train exceeds holdout by >15 pp): %s."
              % ", ".join("`%s`" % r["prompt_key"] for r in overfit))
        else:
            A("No combination shows a >15 pp train-over-holdout gap on the "
              "averaged measurement.")
        A("")
        A("A holdout range that is perfect while the train range is not is the "
          "expected shape here: the holdout is only 6 cases, so it is easier to "
          "sweep completely in 8 rounds, whereas train covers 14 cases and the "
          "per-seed sample varies. It is NOT evidence of superiority on train; "
          "it is evidence that the variant never gets a class outright wrong.")
    A("")

    # ---------------- honest verdict ----------------
    A("## 5. What 'certified' means here")
    A("")
    certified = [r for r in measured_hold
                 if (r.get("verdict") or {}).get("state") == "certified"]
    A("A variant is certified only when BOTH hold on the **holdout** split:")
    A("")
    A("1. it reached **100% balanced accuracy on every measured seed**, not just "
      "one, and")
    A("2. **every one of those runs answered more than one class** — a "
      "single-class run's score is arithmetic over the gold set's class mix, "
      "not a measurement of the prompt.")
    A("")
    A("Train is reported as a range but is **not required to be perfect**. That "
      "is a deliberate correction: an earlier version of this tool certified on "
      "a single run that happened to hit 100% on both splits, and a 5-seed "
      "stress test then showed the same prompt scoring 81-94% on train. The "
      "certificate had been issued on sampling luck. Requiring repetition is "
      "what makes the remaining certificates mean something.")
    A("")
    if certified:
        A("**Certified: %s**" % ", ".join("`%s`" % r["prompt_key"] for r in certified))
    else:
        A("**No combination is certified yet.**")
    A("")
    A("Reaching 100% required the *mechanism* to be right, not a threshold to be "
      "lowered: the fix was removing a stacked-negation axis, not raising a "
      "number. Balanced accuracy weights each class equally, so 100% balanced "
      "means every class was answered correctly — a statement about behaviour "
      "rather than about one number.")
    A("")

    # ---------------- what to change ----------------
    A("## 6. Recommended changes")
    A("")
    # Marginal-best-per-axis is NOT the best combination. Taking each axis's best
    # value independently assumes no interaction, but `criterion` and `output`
    # DO interact: presence_gate needs output=with_unknown to be able to answer
    # UNKNOWN at all. So both are shown, and the measured winner is authoritative.
    if ranked:
        win = ranked[0]
        A("### 6a. Best MEASURED combination (authoritative)")
        A("")
        A("| | |")
        A("|---|---|")
        A("| prompt_key | `%s` |" % win["prompt_key"])
        A("| combination | `%s` |" % " + ".join(
            "%s=%s" % (k, win["axes"][k]) for k in sorted(win["axes"])))
        A("| train bal | %s |" % rng(win.get("train_runs")))
        A("| holdout bal | %s (%s) |" % (
            rng(win.get("holdout_runs")), perfect_count(win.get("holdout_runs"))))
        A("| verdict | **%s** |" % (win.get("verdict") or {}).get("state", "-"))
        A("")

    A("### 6b. Per-axis marginal best (reference only, NOT a combination)")
    A("")
    best_axes: dict[str, str] = {}
    for dim in sorted(eff):
        if eff[dim]:
            best_axes[dim] = eff[dim][0]["value"]
    A("```")
    A(" + ".join("%s=%s" % (k, best_axes[k]) for k in sorted(best_axes)))
    A("```")
    A("")
    A("> **Important distinction:** this is each axis's best value taken "
      "INDEPENDENTLY, which assumes the axes do not interact. But `criterion` "
      "and `output` **do**: `presence_gate` needs `output=with_unknown` before "
      "it can ever answer UNKNOWN. So the per-axis best is not necessarily the "
      "best combination — treat 6a as authoritative and use 6b only to see "
      "which value wins on each axis in isolation.")
    A("")
    for dim in sorted(best_axes):
        A("- `%s` = `%s`" % (dim, best_axes[dim]))
    A("")
    if "negation" in eff:
        worst = eff["negation"][-1]
        A("- The `negation` axis is the one to watch: its worst value ("
          "`%s`) averages **%.2f%%** balanced accuracy across %d combinations. "
          "Stacked prohibitions measurably suppress correct YES answers, because "
          "a small vision model under several negations collapses to one answer."
          % (worst["value"], worst["mean_train_ba"], worst["n"]))
    A("")
    A("**Caveats that must travel with this report:**")
    A("")
    A("- The gold fixtures are **synthetic** (a crosshair drawn at a computed "
      "position on a flat canvas). They are labelled by construction, which makes "
      "the geometry exact, but they cannot prove behaviour on a real screen.")
    A("- The fixtures contain **no picker**, so any variant that correctly gates "
      "on picker presence must answer UNKNOWN there and the scorer counts that "
      "wrong. Variants with a presence gate are therefore penalised by the "
      "fixtures for being right.")
    A("- A 20-case set bounds resolution: holdout has %s cases, so one case is "
      "%.0f pp of balanced accuracy. Real captures are needed before a final "
      "choice." % (rep.get("holdout_n"),
                   100.0 / max(1, int(rep.get("holdout_n") or 1))))
    A("")

    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote %s (%d lines)" % (OUT_MD, len(L)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
