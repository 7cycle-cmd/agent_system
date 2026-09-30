# -*- coding: utf-8 -*-
"""proof_report_chain.py — the trace chain, as DATA, each stage MEASURED.

WHY THIS EXISTS (user, 2026-09-23)
----------------------------------
    "proof run -> send report -> worker get report / is middleware design lazy,
     5W1H can help easy / and why the output didn't need to test by TDD, if yes,
     value will not have problem"
    "that why all output must can measured in measured unit"
    "we am build a 100% measured system for trace system, not making toy!!!!"

The chain has FOUR stages, and EVERY one produces an OUTPUT:

    proof run     run_one()        -> {passed, failed, state, exit}
    read verdict  parse_verdict()  -> (passed, failed)
    send report   build_report()   -> markdown + JSON
    worker get    emit()           -> systemMessage

A stage whose output is not measured is a link nobody can audit. The user's
diagnosis is exact: the parser's OUTPUT VALUE was never tested against real
proof output, so a decoy in a check NAME (`2 pass / 1 fail`) was read as the
verdict while the proof's own summary said `54 passed / 1 failed`.

WHAT THIS MODULE IS
-------------------
It declares each stage as a FACTOR in the FIRST-PRINCIPLE shape, so the EXISTING
measurability gate applies and NO second checker is written:
`factor_first_principle.assert_measurable` refuses a factor whose `metric_unit`
names no subject. The five questions are answered IN ORDER:

    failure_mode -> observable -> unit -> threshold -> independence

and each answer is tagged with the 5W1H dimension it comes from
(`factor_first_principle.FIRST_PRINCIPLE_QUESTIONS`), so a reader sees WHICH
dimension is unanswered rather than only which question.

WHAT IT IS NOT
--------------
It does NOT re-implement TDD/ontology verify. The proof-run stage is ALREADY
gated by `register_approval.check_stages(tdd_pass, tdd_verify_pass,
ontology_pass)` (`register_approval.py:184`); this module names that as the
stage's `how` and REUSES it.

READ-ONLY: it declares and audits. It writes nothing.

Run:
    .\\.venv\\Scripts\\python.exe proof_report_chain.py
    .\\.venv\\Scripts\\python.exe proof_report_chain.py --json
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

import factor_first_principle as ffp  # noqa: E402

# ---------------------------------------------------------------------------
# THE CHAIN, as DATA. One row per stage. Each row IS a factor in the
# first-principle shape, so `ffp.assert_measurable` applies unchanged.
#
# `stage`        the function's name in `scripts/proof_gate.py`
# `input`        what the stage consumes (REGISTERED, see `INPUTS`)
# `output`       the value the stage produces
# `metric_kind`  one of ffp.UNIT_KINDS
# `metric_unit`  MUST name its subject, or `assert_measurable` REFUSES it
# `metric_target` the value that separates pass from fail
# `failure_mode`/`observable`/`unit`/`threshold`/`independence` = the five, IN ORDER
# `verify_cmd`   the command that can FAIL (the TDD verify for THIS output)
# ---------------------------------------------------------------------------
CHAIN: tuple[dict[str, Any], ...] = (
    {
        "stage": "proof_gate.run_one",
        "input": "a proof file path (registered as a repo file entity)",
        "output": "{passed, failed, state, exit}",
        "metric_kind": ffp.UNIT_COUNT,
        "metric_unit": "count of checks reported by the proof",
        "metric_target": "0",
        "failure_mode": "the parsed (passed, failed) does NOT equal the number "
                        "the proof ITSELF printed in its summary line",
        "observable": "run the proof, read its last summary line, compare with "
                      "the row the gate recorded",
        "unit": "count of checks reported by the proof",
        "threshold": "0 disagreements between the parsed verdict and the proof's "
                     "own summary, because ONE disagreement means the reported "
                     "value is not the proof's value",
        "independence": "yes — the parser can be right while the report stage is "
                        "wrong, so this is its own factor",
        "verify_cmd": "python _proof_proof_gate.py",
    },
    {
        "stage": "proof_gate.parse_verdict",
        "input": "the proof's stdout+stderr text",
        "output": "(passed, failed)",
        "metric_kind": ffp.UNIT_COUNT,
        "metric_unit": "count of decoy check-names read as the verdict",
        "metric_target": "0",
        "failure_mode": "a decoy inside a CHECK NAME (e.g. `2 pass / 1 fail`) is "
                        "read as the verdict instead of the LAST summary by text "
                        "position",
        "observable": "feed the parser a text whose check name holds a decoy and "
                      "whose summary is the real one; read the tuple it returns",
        "unit": "count of decoy check-names read as the verdict",
        "threshold": "0, because ONE decoy read as the verdict prints a FALSE "
                     "row for a proof that ran to completion",
        "independence": "yes — this is the specific stage that was wrong, and it "
                        "can be wrong while run_one is right (run_one calls it)",
        "verify_cmd": "python _proof_proof_gate.py",
    },
    {
        "stage": "proof_gate.build_report",
        "input": "the rows produced by run_one",
        "output": "markdown + JSON report",
        "metric_kind": ffp.UNIT_PCT,
        "metric_unit": "pct of report rows carrying a readable number",
        "metric_target": "100",
        "failure_mode": "the header total disagrees with the sum of the rows, or "
                        "a row is silently omitted",
        "observable": "sum the per-row numbers and compare with the header; count "
                      "the rows in the table against the rows passed in",
        "unit": "pct of report rows carrying a readable number",
        "threshold": "100, because a report that omits a row reports a total the "
                     "run cannot support",
        "independence": "yes — the rows can be right while the header is wrong",
        "verify_cmd": "python _proof_proof_gate_persist.py",
    },
    {
        "stage": "proof_gate.emit",
        "input": "the report text",
        "output": "the hook's systemMessage (JSON on stdout)",
        "metric_kind": ffp.UNIT_COUNT,
        "metric_unit": "count of proofs named in the worker's message",
        "metric_target": "0",
        "failure_mode": "the message omits a proof that ran, so the worker cannot "
                        "tell the run happened (the '0 passed / 0 failed' defect)",
        "observable": "parse the emitted JSON and count the proof names present "
                      "against the rows that ran",
        "unit": "count of proofs named in the worker's message",
        "threshold": "0 missing names, because a message that hides a proof is a "
                     "report the worker cannot act on",
        "independence": "yes — the report can be complete while the emitted "
                        "message is degenerate",
        "verify_cmd": "python _proof_proof_gate_persist.py",
    },
)

# THE REGISTERED INPUTS. A function's input must be REGISTERED (the user:
# "$A and $B has register at function_registry"). Each input names the register
# row that DECLARES it, so the input is checkable rather than assumed.
INPUTS: tuple[dict[str, str], ...] = (
    {"stage": "proof_gate.run_one", "input": "proof_file",
     "register": "code_location_registry", "key": "file_path"},
    {"stage": "proof_gate.parse_verdict", "input": "proof_stdout",
     "register": "proof_gate.run_one", "key": "output"},
    {"stage": "proof_gate.build_report", "input": "rows",
     "register": "proof_gate.run_one", "key": "row"},
    {"stage": "proof_gate.emit", "input": "report_text",
     "register": "proof_gate.build_report", "key": "text"},
)

# THE PROOF-RUN VERIFY, REUSED — NOT re-implemented. The user: "you have TDD and
# ontology verify at proof run". The three stages live in ONE place; this module
# NAMES them so the chain's `how` points at the existing gate.
PROOF_RUN_VERIFY = {
    "module": "register_approval",
    "function": "check_stages",
    "stages": ("tdd", "tdd_verify", "ontology_verify"),
    "rule": "an APPROVED verdict requires all three stages passed "
            "(register_approval.py:263 record())",
}


def stage_factor(row: dict[str, Any]) -> dict[str, Any]:
    """One CHAIN row as a FACTOR the measurability gate understands."""
    return dict(row, factor_key="chain.%s" % row["stage"])


def audit_chain(conn=None) -> dict[str, Any]:
    """Audit EVERY stage: is its output measured by a unit that names a subject?

    Returns `{ok, stages:[{stage, measurable, unit, subject, reasons,
    derived, missing}], all_measurable, all_derived}`. Reports, never raises, so
    the whole chain is audited in one pass.
    """
    stages: list[dict[str, Any]] = []
    for row in CHAIN:
        f = stage_factor(row)
        m = ffp.audit_factor(f, conn)
        d = ffp.derive(f)
        stages.append({
            "stage": row["stage"],
            "output": row["output"],
            "measurable": m["measurable"],
            "unit": m["unit"],
            "subject": m["subject"],
            "reasons": m["reasons"],
            "derived": d["derived"],
            "missing": d["missing"],
            "verify_cmd": row["verify_cmd"],
        })
    return {
        "ok": True,
        "stages": stages,
        "all_measurable": all(s["measurable"] for s in stages),
        "all_derived": all(s["derived"] for s in stages),
        "inputs": [dict(i) for i in INPUTS],
        "proof_run_verify": dict(PROOF_RUN_VERIFY),
    }


def format_report(rep: dict[str, Any]) -> str:
    lines = [
        "=" * 78,
        "THE PROOF-REPORT CHAIN — every stage's OUTPUT measured by a unit",
        "=" * 78,
    ]
    for s in rep["stages"]:
        lines.append("")
        lines.append("  %s" % s["stage"])
        lines.append("    output    : %s" % s["output"])
        lines.append("    unit      : %s" % (s["unit"] or "(none)"))
        lines.append("    subject   : %s" % (s["subject"] or "(NONE — refused)"))
        lines.append("    measurable: %s   derived: %s"
                     % (s["measurable"], s["derived"]))
        lines.append("    verify    : %s" % s["verify_cmd"])
        if s["reasons"]:
            for r in s["reasons"]:
                lines.append("    REASON    : %s" % r)
    lines += [
        "",
        "  all measurable : %s" % rep["all_measurable"],
        "  all derived    : %s" % rep["all_derived"],
        "  proof-run verify (REUSED, not re-implemented): %s.%s over %s"
        % (rep["proof_run_verify"]["module"],
           rep["proof_run_verify"]["function"],
           ", ".join(rep["proof_run_verify"]["stages"])),
        "=" * 78,
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--db", default="")
    args = ap.parse_args()
    conn = None
    if args.db:
        import sqlite3
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
    try:
        rep = audit_chain(conn)
    finally:
        if conn is not None:
            conn.close()
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print(format_report(rep))
    return 0 if (rep["all_measurable"] and rep["all_derived"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
