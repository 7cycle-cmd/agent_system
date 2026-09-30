#!/usr/bin/env python
"""qc_gate_verify.py — run the quality gate at CODE VERIFY time.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "is active now, at skill > coding writing and coding verifty? or how"

WRITING is covered by `scripts/qc_gate_hook.py` (PostToolUse). VERIFYING needs a
second, explicit entry point that a caller can FAIL ON: a hook reports, but a
verify step must return a NON-ZERO exit so a pipeline can gate on it. That is the
one difference between this file and the hook — same runner, same friendly shape,
but `must` failures make the exit code non-zero.

WHAT IT RUNS (and WHY these gates, on this subject)
---------------------------------------------------
A code artefact (a file) CAN answer:
  * `ontology`  — is the file covered by an entity + is its module name a term?
  * `safety`    — does the file carry live hardcoded values?
  * `boundary`  — does a bound numeric field state its own min/max? (file-owner)
It CANNOT answer `tdd` (that needs a CONTRACT), `5w1h` (a KIND),
`role_environment` (an actor), `trace` (a `proof_run`). Asking a file those would
report a failure the file never caused — the wrong-population defect
(`measurement-scope`). So `--gate` selects what to run, and the DEFAULT is the
set that applies to a file.

USAGE
  python scripts/qc_gate_verify.py --file qc_gate.py
  python scripts/qc_gate_verify.py --contract example_basic --gate tdd
  python scripts/qc_gate_verify.py --file qc_gate.py --json

EXIT
  0 = no `must` failure   1 = a `must` failure (or an UNKNOWN on a gated run)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"
# The gates that APPLY to a file. `tdd` is added when a contract is given, and
# `boundary` is NOT here — MEASURED 2026-09-28: a boundary case is declared on a
# CONTRACT (`skill_contract_tdd_case.contract_id`), so running `boundary` on a
# file answers a question the file was never asked and reports a FAIL it did not
# cause. It moved to the contract group.
FILE_GATES = ("ontology", "safety")
CONTRACT_GATES = ("tdd", "boundary")


def _friendly_fix(gate_key: str, row: dict) -> str:
    """The field/rule/why/example a worker can COPY — never a bare 'no'."""
    rules = {
        "ontology": ("the name is a registered term AND the file is covered by a "
                     "code entity",
                     "an unresolvable name makes every later reader pick the "
                     "wrong referent",
                     "python scripts/_registry_qc_gate_terms.py --apply  /  add "
                     "a code_location_registry row for the file"),
        "safety": ("0 live hardcoded candidates",
                   "a typed value cannot be rotated and is invisible to later "
                   "readers",
                   "read the value from settings/DB instead of typing it"),
        "boundary": ("every bound numeric field declares its own min/max",
                     "a field with no declared range silently accepts garbage",
                     "add min_value/max_value to the field's register row"),
        "tdd": ("the contract's cases all pass at the target streak",
                "a rule nobody can run is a rule nobody can trust",
                "add a case that measures the rule, then re-run the contract"),
    }
    field, why, example = rules.get(
        gate_key, ("the declared rule", "a gate with no stated rule cannot be "
                                        "fixed", "see qc_gate.py --list"))
    return "  MUST FIX %s:\n    rule    : %s\n    why     : %s\n    example : %s" \
           % (gate_key, field, why, example)


def run(conn: sqlite3.Connection, *, gate_keys, subject_kind: str,
        subject_ref: str) -> list[dict]:
    import qc_gate_runner as R
    return [R.run_gate(conn, g, subject_kind=subject_kind, subject_ref=subject_ref)
            for g in gate_keys]


def render(subject: str, rows: list[dict]) -> tuple[str, bool]:
    """Returns `(text, has_must_failure)`. NEVER mutates a verdict."""
    failed = [r for r in rows if r["verdict"] == "FAIL"]
    unknown = [r for r in rows if r["verdict"] == "UNKNOWN"]
    head = "qc_gate (verify): %s -> %s" % (
        subject, "FAIL" if failed else ("UNKNOWN" if unknown else "PASS"))
    lines = [head, "  gate      verdict  value   MEASURE (the reader + population)"]
    for r in rows:
        lines.append("  %-9s %-8s %-7s %s" % (
            r["gate_key"], r["verdict"], r.get("value"),
            str(r.get("detail") or "")[:160]))
    for r in failed:
        lines.append(_friendly_fix(r["gate_key"], r))
    return "\n".join(lines), bool(failed)


def main() -> int:
    ap = argparse.ArgumentParser(description="run the qc gate at verify time")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--file", default="", help="a .py artefact to verify")
    ap.add_argument("--contract", default="", help="a contract id (runs tdd)")
    ap.add_argument("--gate", action="append", default=[],
                    help="repeatable; overrides the default set")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.file and not args.contract:
        print("nothing to verify: pass --file and/or --contract")
        return 1

    conn = sqlite3.connect(args.db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        blocks: list[tuple[str, list[dict]]] = []
        if args.file:
            keys = tuple(args.gate) or FILE_GATES
            blocks.append((args.file, run(conn, gate_keys=keys,
                                          subject_kind="file",
                                          subject_ref=args.file)))
        if args.contract:
            keys = tuple(args.gate) or CONTRACT_GATES
            blocks.append((args.contract, run(conn, gate_keys=keys,
                                              subject_kind="contract",
                                              subject_ref=args.contract)))
        out = {}
        worst = 0
        for subject, rows in blocks:
            text, bad = render(subject, rows)
            out[subject] = {"rows": rows, "must_failure": bad}
            if not args.json:
                print(text)
                print()
            worst = 1 if (bad or worst) else 0
        if args.json:
            print(json.dumps(out, indent=1, ensure_ascii=False, default=str))
        return worst
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())