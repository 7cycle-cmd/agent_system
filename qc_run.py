# -*- coding: utf-8 -*-
"""qc_run.py — ONE entry point for every QC verdict.

Wraps the existing tools (schema_qc / pair_qc / evidence_classify) so they all
write the same `qc_run` row through qc_contract. We trust EVIDENCE, not any
agent's self-report: a PASS without an evidence screenshot is downgraded to
UNKNOWN.

Evidence lives in `evidence/EVID-<target>-<ts>/` (evidence_store.py). A verdict
binds the sha256 of `shot.png` inside that folder — a hash of "some file"
proves nothing.

POLICY: UNKNOWN = FAIL. Only a proven PASS lets work through; FAIL and UNKNOWN
both block dispatch.

Usage:
  python qc_run.py --summary
  python qc_run.py --list --verdict FAIL
  python qc_run.py --list --task-id 109
  python qc_run.py --gate --task-id 109          # exit 1 when blocked
  python qc_run.py --capture --target perm_default [--note "picker open"]
  python qc_run.py --record --tool manual --target X --verdict PASS \
                   --evidence EVID-perm_default-20260919-232416 \
                   --reason "human reviewed"

Exit codes: 0 ok / 1 gate blocked (FAIL or UNKNOWN present) / 2 bad args.
"""
from __future__ import annotations

import json
import sys

import qc_contract as qc


def _arg(name: str, default: str | None = None) -> str | None:
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main() -> None:
    if "--summary" in sys.argv:
        print(json.dumps(qc.summary(), indent=1))
        return

    if "--list" in sys.argv:
        rows = qc.list_runs(
            verdict=_arg("--verdict"),
            tool=_arg("--tool"),
            target=_arg("--target"),
            task_id=_arg("--task-id"),
            limit=int(_arg("--limit", "50") or 50),
        )
        if not rows:
            print("(no qc_run rows)")
            return
        for r in rows:
            sha = (r["evidence_sha256"] or "-")[:12]
            ev = r.get("evidence_id") or "-"
            print("%-4s %-18s %-22s sha=%s ev=%s  %s" % (
                r["verdict"], r["tool"], r["target"], sha, ev,
                r["reason"] or ""))
        return

    if "--gate" in sys.argv:
        tid = _arg("--task-id")
        if not tid:
            print("usage: qc_run.py --gate --task-id ID")
            sys.exit(2)
        blocked = qc.has_failed(tid)
        print("task=%s blocked=%s (policy: UNKNOWN = FAIL)" % (tid, blocked))
        if blocked:
            for r in qc.blocking_runs(tid):
                print("  %-7s %-18s %-22s %s" % (
                    r["verdict"], r["tool"], r["target"], r["reason"] or ""))
        sys.exit(1 if blocked else 0)

    if "--capture" in sys.argv:
        target = _arg("--target")
        if not target:
            print("usage: qc_run.py --capture --target TARGET [--note TEXT]")
            sys.exit(2)
        bind = qc.capture(target, note=_arg("--note", "") or "")
        print(json.dumps(bind, indent=1))
        return

    if "--record" in sys.argv:
        tool = _arg("--tool", "manual") or "manual"
        target = _arg("--target")
        verdict = _arg("--verdict", qc.UNKNOWN) or qc.UNKNOWN
        if not target:
            print("usage: qc_run.py --record --tool T --target X --verdict V "
                  "[--evidence EVID-...|PATH] [--reason TEXT] [--task-id ID]")
            sys.exit(2)
        res = qc.record(
            tool, target, verdict,
            evidence=_arg("--evidence"),
            reason=_arg("--reason"),
            task_id=_arg("--task-id"),
            trace_id=_arg("--trace-id"),
            gate_key=_arg("--gate-key"),
            score_0_100=(float(_arg("--score")) if _arg("--score") else None),
            gate_run_ref=_arg("--gate-run-ref"),
        )
        print(json.dumps(res, indent=1))
        return

    print(__doc__)


if __name__ == "__main__":
    main()
