# -*- coding: utf-8 -*-
"""binding_proof_run.py — run the proof run a 5W1H binding needs, then activate.

THE HUMAN (2026-09-27):
    "**未結項（honest）：** ... 114 個 binding 仍 inactive（要跑 proof run 先可以
     activate）。do it now"

`CHAT.TITLE.CONTENT.ENTITY` measured that `activate_binding` is THE ONE writer of
`dimension_binding_registry.is_active = 1`, and it DELEGATES to
`activation_gate.assert_may_activate`, which requires a PROOF RUN (a streak AND a
2-part verdict). MEASURED: only **1 of 114** bindings has a run, and it is
refused. So the bindings cannot be activated until a run exists.

THIS MODULE RUNS THE RUN, THROUGH THE EXISTING HARNESS
------------------------------------------------------
It does NOT re-implement the harness. It calls
`llm_100_run_harness.run_harness(...)` with the binding's OWN resolved oracle,
options and target, then calls `dimension_binding_registry.activate_binding(...)`.
A second implementation would let this module and the gate disagree about the
same evidence.

WHAT IT REFUSES
---------------
  * running a binding whose oracle/options do NOT resolve (a run that would
    measure the PHONE and record it as evidence about the binding);
  * writing `is_active=1` itself (the gate is the only writer);
  * activating a binding the gate refuses.

    .\\.venv\\Scripts\\python.exe binding_proof_run.py --measure
    .\\.venv\\Scripts\\python.exe binding_proof_run.py --apply
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

import dimension_binding_registry as dbr  # noqa: E402
import llm_100_run_harness as h  # noqa: E402

DEFAULT_DB = BASE_DIR / "agent.db"


def runnable(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which inactive bindings CAN run a proof run, and WHY. READ-ONLY.

    A binding is RUNNABLE iff its oracle, options and target all resolve from
    its OWN declaration (not the phone fallback). MEASURED: the harness REFUSES
    a run whose sources fell back to `default_phone`, because it would measure
    the phone and record it as evidence about the binding.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT subject_kind, dimension_key, is_active, cite_ref FROM "
        "dimension_binding_registry WHERE is_active=0 "
        "ORDER BY subject_kind, dimension_key")]
    out: list[dict[str, Any]] = []
    for r in rows:
        kind = str(r["subject_kind"])
        dim = str(r["dimension_key"])
        ref_tag = dbr.binding_ref_tag(kind, dim)
        info: dict[str, Any] = {"subject_kind": kind, "dimension_key": dim,
                                "ref_tag": ref_tag}
        try:
            o = h.oracle_for(conn, ref_tag)
            info["oracle_source"] = o.get("source")
        except Exception as e:  # noqa: BLE001
            info["oracle_source"] = "ERR:%s" % type(e).__name__
        try:
            v = h.options_for_ref_tag(conn, ref_tag)
            info["options_source"] = v.get("source")
            info["option_count"] = len(v.get("options") or [])
        except Exception as e:  # noqa: BLE001
            info["options_source"] = "ERR:%s" % type(e).__name__
            info["option_count"] = 0
        try:
            t = h.streak_target_for(conn, ref_tag)
            info["target"] = int(t.get("target") or 0)
            info["target_source"] = t.get("source")
        except Exception as e:  # noqa: BLE001
            info["target"] = 0
            info["target_source"] = "ERR:%s" % type(e).__name__
        # RUNNABLE iff no source fell back to the phone
        fell_back = [k for k in ("oracle_source", "options_source")
                     if str(info.get(k) or "") == "default_phone"]
        info["fell_back"] = fell_back
        info["runnable"] = not fell_back and info["option_count"] > 0
        out.append(info)
    runnable_rows = [r for r in out if r["runnable"]]
    return {"ok": True, "inactive_total": len(out),
            "runnable": len(runnable_rows),
            "not_runnable": len(out) - len(runnable_rows),
            "rows": out,
            "cite": ("measured: llm_100_run_harness.oracle_for / "
                     "options_for_ref_tag / streak_target_for per binding")}


def run_and_activate(conn: sqlite3.Connection, *, apply: bool = False,
                     limit: int | None = None) -> dict[str, Any]:
    """Run the proof run for each RUNNABLE binding, then activate via the gate.

    `apply=False` reports what WOULD run. `apply=True` runs the harness (which
    calls the LLM) and then the gate. Every refusal is REPORTED with the gate's
    own reason.
    """
    r = runnable(conn)
    todo = [x for x in r["rows"] if x["runnable"]]
    if limit is not None:
        todo = todo[:int(limit)]
    ran: list[dict[str, Any]] = []
    activated: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for info in todo:
        kind, dim = info["subject_kind"], info["dimension_key"]
        ref_tag = info["ref_tag"]
        if not apply:
            ran.append({"ref_tag": ref_tag, "state": "REPORT_ONLY",
                        "target": info["target"]})
            continue
        # THE RUN, through the EXISTING harness.
        #
        # `ignore_streak=True` IS REQUIRED, and it is a MEASURED finding, not a
        # preference. MEASURED: when the streak already meets the target the
        # round loop breaks on its FIRST iteration, so `rounds == []` and the
        # harness SKIPS the 2-part verify (`NO_ROUNDS_TO_VERIFY`) — a run that
        # measured nothing must not re-record a verdict. So a binding whose
        # streak is already met would produce NO 2-part evidence and the gate
        # would refuse with "no register_approve row". `ignore_streak=True`
        # forces the rounds, so the verify RUNS and its verdict is real.
        try:
            out = h.run_harness(seed=7, ref_tag=ref_tag, write=True,
                                ignore_streak=True)
        except Exception as e:  # noqa: BLE001
            ran.append({"ref_tag": ref_tag, "state": "RUN_ERROR",
                        "error": "%s: %s" % (type(e).__name__, e)})
            continue
        verdict = str(out.get("verdict") or "")
        tp = out.get("two_part_verify") or {}
        stages = tp.get("stages") or {}
        ran.append({"ref_tag": ref_tag, "state": "RAN", "verdict": verdict,
                    "rounds_run": out.get("rounds_run"),
                    "wrote_evidence": out.get("wrote_evidence"),
                    "two_part_verdict": tp.get("verdict"),
                    "failed_stages": stages.get("failed_stages")})
        if verdict == "REFUSED":
            refused.append({"ref_tag": ref_tag, "code": "RUN_REFUSED",
                            "reason": str((out.get("refusal") or {}).get("why")
                                          or "")[:140]})
            continue
        # THE 2-PART VERDICT IS THE GATE'S FIRST REQUIREMENT. If it did not
        # APPROVE, the gate will refuse — REPORT the stage that failed, so the
        # reason is the ROOT CAUSE rather than "not proven".
        if tp.get("verdict") != "APPROVED":
            failed = stages.get("failed_stages") or []
            refused.append({
                "ref_tag": ref_tag, "code": "TWO_PART_NOT_APPROVED",
                "reason": ("the 2-part verify returned %r; failed stage(s)=%s"
                           % (tp.get("verdict"), failed)),
                "failed_stages": failed,
                "stages": {k: stages.get(k) for k in
                           ("tdd", "tdd_verify", "ontology_verify")},
            })
            continue
        # THE ACTIVATION, through the ONE gate.
        row = conn.execute(
            "SELECT cite_ref FROM dimension_binding_registry WHERE "
            "subject_kind=? AND dimension_key=?", (kind, dim)).fetchone()
        cite = str(row["cite_ref"] or "") if row else ""
        res = dbr.activate_binding(conn, kind, dim, cite_ref=cite)
        if res.get("ok"):
            activated.append({"ref_tag": ref_tag, "code": res.get("code")})
        else:
            refused.append({"ref_tag": ref_tag, "code": res.get("code"),
                            "reason": str(res.get("message") or "")[:140]})
    return {"ok": True, "applied": bool(apply),
            "runnable": r["runnable"], "attempted": len(todo),
            "ran": ran, "activated": len(activated),
            "activated_rows": activated, "refused": len(refused),
            "refused_rows": refused,
            "not_runnable": [x for x in r["rows"] if not x["runnable"]][:5],
            "cite": "measured: run_harness + activate_binding"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true",
                    help="report which bindings CAN run, write nothing")
    ap.add_argument("--apply", action="store_true",
                    help="run the harness and activate through the gate")
    ap.add_argument("--limit", type=int, default=None,
                    help="only the first N runnable bindings")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if args.measure:
            r = runnable(conn)
            print("BINDING PROOF RUN  inactive=%d runnable=%d not_runnable=%d"
                  % (r["inactive_total"], r["runnable"], r["not_runnable"]))
            for x in r["rows"]:
                if x["runnable"]:
                    print("  RUNNABLE  %-40s target=%-4d opts=%d"
                          % (x["ref_tag"], x["target"], x["option_count"]))
            for x in r["rows"]:
                if not x["runnable"]:
                    print("  NO        %-40s fell_back=%s"
                          % (x["ref_tag"], x["fell_back"]))
            return 0
        if args.apply:
            res = run_and_activate(conn, apply=True, limit=args.limit)
            print("BINDING PROOF RUN APPLY")
            print("  runnable  : %d" % res["runnable"])
            print("  attempted : %d" % res["attempted"])
            print("  activated : %d" % res["activated"])
            print("  refused   : %d" % res["refused"])
            for x in res["ran"]:
                print("    RAN %-40s %s" % (x["ref_tag"], x.get("verdict")))
            for x in res["refused_rows"]:
                print("    REF %-40s %s %s"
                      % (x["ref_tag"], x["code"], x.get("reason", "")[:70]))
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
