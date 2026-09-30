# -*- coding: utf-8 -*-
"""qc_arbiter.py — A09: turn NINE dimension results into ONE verdict.

WHY THIS IS THE ONLY NEW "DIMENSION"
------------------------------------
A01..A08 already exist as eight modules (`qc_gate.GATES` names each
`checker_ref`). Measured: there was NO aggregate — no `quality_score`, no
`arbiter`, no `gate_registry`. So the arbiter is the ONE piece that did not
already exist, and it OWNS ONE JOB: combine results it was GIVEN.

WHAT IT NEVER DOES
------------------
It never RUNS a check and never calls a model. It receives verdicts and metrics
and produces a verdict + a score. That keeps the aggregate auditable: every
number it reports is DERIVED from a per-gate result, so a caller cannot ASSERT a
score.

UNKNOWN IS A FAILURE (the repo's own policy)
--------------------------------------------
`qc_contract.UNKNOWN_BLOCKS = True` and `BLOCKING_VERDICTS = (FAIL, UNKNOWN)`.
An unmeasured dimension is not a pass, so the aggregate treats UNKNOWN exactly
like FAIL — a run that could not measure something has NOT proven the subject.

PASS IS DERIVED, NEVER ASSERTED
-------------------------------
`metric_pass` comes from `value` vs `metric_target` under a DECLARED `polarity`
(`at_least` / `at_most` / `equal`) — the same rule `skill_factor_proof.metric_pass`
uses ("a caller cannot ASSERT a pass"). A number alone cannot say which direction
is good, which is why polarity is declared per gate in `qc_gate.GATES`.

Run:
    .\\.venv\\Scripts\\python.exe qc_arbiter.py --demo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import qc_gate  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


class ArbiterError(ValueError):
    """An aggregate that cannot be computed, with a named reason."""


def derive_pass(value: float | None, target: float, polarity: str) -> bool:
    """Is `value` a pass against `target` under `polarity`? DERIVED.

    A missing value is NOT a pass — it is the absence of a measurement.
    """
    if value is None:
        return False
    v = float(value)
    t = float(target)
    if polarity == "at_least":
        return v >= t
    if polarity == "at_most":
        return v <= t
    if polarity == "equal":
        return abs(v - t) < 1e-9
    raise ArbiterError("unknown polarity %r" % polarity)


def arbitrate(
    results: list[dict[str, Any]],
    *,
    expected_keys: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Combine per-gate results into `{verdict, score_0_100, gates, failed_gates}`.

    `results` is a list of dicts shaped:
        {"gate_key": str, "verdict": PASS|FAIL|UNKNOWN, "value": float|None,
         "metric_target": float, "polarity": str, "detail": str,
         "evidence_ref": str, "cite_ref": str}

    RULES:
      * a gate whose `gate_key` is not in `expected_keys` is REFUSED (a
        dimension nobody declared must not silently enter the score);
      * a MISSING gate is a FAILURE, not an omission — the aggregate must not be
        satisfiable by simply not running a dimension;
      * UNKNOWN is treated as FAIL.

    Never raises for a per-gate problem: it REPORTS it, because a report that
    dies on a finding is not a report.
    """
    keys = tuple(expected_keys) if expected_keys is not None \
        else qc_gate.GATE_KEYS
    by_key: dict[str, dict[str, Any]] = {}
    extra: list[str] = []
    for r in results:
        k = str(r.get("gate_key") or "").strip()
        if k not in keys:
            extra.append(k or "(empty)")
            continue
        by_key[k] = r

    gates: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    missing: list[str] = []
    for k in keys:
        if k not in by_key:
            missing.append(k)
            gates.append({"gate_key": k, "verdict": FAIL, "value": None,
                          "detail": "gate did not run — a missing dimension is a "
                                    "failure, not an omission",
                          "metric_pass": False})
            failed.append({"gate_key": k, "reason": "did not run"})
            continue
        r = by_key[k]
        v = str(r.get("verdict") or UNKNOWN).strip().upper()
        if v not in (PASS, FAIL, UNKNOWN):
            v = UNKNOWN
        # DERIVE the pass from the number; a gate may only CLAIM a pass if the
        # number supports it. An UNKNOWN is a failure regardless of the number.
        mp = False
        if v == PASS:
            mp = derive_pass(r.get("value"),
                             r.get("metric_target",
                                   r.get("metric_target", 0.0)),
                             str(r.get("polarity") or "at_least"))
        entry = {
            "gate_key": k,
            "verdict": v,
            "value": r.get("value"),
            "metric_target": r.get("metric_target"),
            "polarity": r.get("polarity"),
            "metric_pass": bool(mp),
            "detail": str(r.get("detail") or ""),
            "evidence_ref": str(r.get("evidence_ref") or ""),
            "cite_ref": str(r.get("cite_ref") or ""),
        }
        gates.append(entry)
        if v != PASS or not mp:
            failed.append({
                "gate_key": k,
                "reason": ("UNKNOWN — not a pass" if v == UNKNOWN else
                           "verdict %s" % v if v != PASS else
                           "value %r does not meet target %r (%s)"
                           % (r.get("value"), r.get("metric_target"),
                              r.get("polarity"))),
            })

    total = len(keys)
    passed = sum(1 for g in gates if g["metric_pass"] and g["verdict"] == PASS)
    score = round(100.0 * passed / total, 2) if total else 0.0
    verdict = PASS if (passed == total and not missing and not extra) else FAIL

    return {
        "verdict": verdict,
        "score_0_100": score,
        "gates_total": total,
        "gates_passed": passed,
        "gates": gates,
        "failed_gates": failed,
        "missing_gates": missing,
        "unknown_keys": extra,
        "policy": "UNKNOWN = FAIL; a missing dimension is a failure",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="qc gate arbiter (A09)")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    if args.demo:
        demo = [
            {"gate_key": "ontology", "verdict": PASS, "value": 100.0,
             "metric_target": 100.0, "polarity": "at_least"},
            {"gate_key": "5w1h", "verdict": PASS, "value": 100.0,
             "metric_target": 100.0, "polarity": "at_least"},
            {"gate_key": "middleware", "verdict": UNKNOWN, "value": None,
             "metric_target": 100.0, "polarity": "at_least"},
        ]
        out = arbitrate(demo, expected_keys=("ontology", "5w1h", "middleware"))
        print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())