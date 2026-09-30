#!/usr/bin/env python
"""ruff_reader.py — the ONE ruff reader, shared by the gate and the proof.

WHY THIS MODULE EXISTS (MEASURED, not stylistic)
-----------------------------------------------
MEASURED 2026-09-28: `ruff` had been installed, measured, and registered as the
factor `import_is_used_and_sorted` with a live proof — but

    git grep -l ruff qc_gate_runner.py     ->  (nothing)

so the tool ran ONLY when a proof ran. The human named the gap exactly:

    "真正的 skill 化（寫檔時自動量）還沒做"      (the real skill-isation is not done)

A rule that only a proof checks is a rule a WRITER can forget. This module is the
single reader that BOTH callers use:

    * `qc_gate_runner._check_safety` (reader 3) — so the PostToolUse hook measures
      the file that was just WRITTEN, automatically;
    * `_proof_code_quality_ruff.py` — so the registered unit and the gate's number
      are the SAME number.

ONE IMPLEMENTATION, TWO CALLERS. A second copy of "which ruff rules count" is the
drift this repo keeps paying for (`one_parser_only`).

THE POPULATION IS THE DECLARED FACTOR UNIT, NOT RUFF'S RAW COUNT
---------------------------------------------------------------
MEASURED over the tree: ruff reports **22,360** findings and UP031 (`%-format`)
alone is **14,950** = 67%. So a reader whose number was "count of ruff findings"
could NEVER reach 0 — the same unreachable-target defect bandit's B608 taught this
task one hour earlier (`scripts/_register_code_quality_terms.sql_sites`).

The unit therefore names the classes that are DEFECTS, not preferences:

    F401 unused-import   I001 unsorted-import   F841 unused-variable
    UP009 utf-8-BOM      F821 undefined-name

F821 is the one that is not style at all: MEASURED, `logic_generator.py`
annotated `sqlite3.Connection` in 18 signatures while never importing sqlite3, so
`typing.get_type_hints()` raised `NameError: name 'sqlite3' is not defined`.

UNKNOWN IS A REAL OUTCOME: if ruff cannot run, `measure()` returns
`{"ok": False, "reason": ...}` and the caller MUST report UNKNOWN — never a pass.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The classes that are DEFECTS. This is the ONE place the gate reads the list; the
# factor's `metric_unit` states the same list in words, and
# `_proof_wiring.py` W6 asserts the two still agree on the NUMBER.
#
# MEASURED GAP FOUND 2026-09-28 while building `code_scan`: its `BUG_RULES` listed
# `F811` (a redefinition that SHADOWS a real function) but this tuple did NOT, so
# `import`ing the class list from `code_scan` (the correct `one_parser_only`
# direction) would ask ruff for a rule it never measured — the reader reported
# F811 = 0 because it never SELECTED it. The bug-class rules belong HERE.
DEFECT_CLASSES: tuple[str, ...] = ("F401", "I001", "F841", "UP009", "F821",
                                  "F811", "F402", "F823")

# Reported but EXCLUDED, with the reason, so a reader can see WHY the raw count is
# not the unit and can re-measure the share themselves.
EXCLUDED_STYLE: tuple[str, ...] = ("UP031",)


def _interpreter() -> str:
    """The venv interpreter, because ruff is installed THERE, not globally."""
    exe = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    return str(exe if exe.exists() else Path(sys.executable))


def _run(cmd: list[str], timeout: int) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           cwd=str(BASE_DIR), timeout=timeout)
    except FileNotFoundError:
        return {"ok": False, "reason": "ruff is not installed in this interpreter"}
    except Exception as exc:
        return {"ok": False, "reason": "%s: %s" % (type(exc).__name__, exc)}
    out = (r.stdout or "").strip()
    if not out:
        # ruff writes `[]` when clean, so EMPTY output means it could not run.
        # This is the "could not read is not clean" rule at the reader level.
        return {"ok": False, "reason": "ruff produced no output: %s"
                                       % (r.stderr or "")[:200]}
    try:
        data = json.loads(out)
    except Exception as exc:
        return {"ok": False, "reason": "ruff JSON unparseable: %s" % exc}
    by_rule: dict[str, int] = {}
    for x in data:
        c = str(x.get("code"))
        by_rule[c] = by_rule.get(c, 0) + 1
    return {"ok": True, "raw": data, "by_rule": by_rule}


def measure(paths: list[str], *, timeout: int = 300) -> dict:
    """Measure the DEFECT classes over an EXPLICIT path list.

    Returns
    -------
    {"ok": True, "defect_total": int, "by_rule": {}, "findings": [],
     "paths": [...]}
    {"ok": False, "reason": str}   <- the caller MUST report UNKNOWN, not a pass.

    `paths` is REQUIRED: a count must name its population (`measurement-scope`).
    An empty list is refused rather than silently measuring "everything".
    """
    if not paths:
        return {"ok": False, "reason": "no paths given: a count must name its "
                                       "population"}
    cmd = [_interpreter(), "-m", "ruff", "check", *[str(p) for p in paths],
           "--select", ",".join(DEFECT_CLASSES), "--output-format", "json"]
    r = _run(cmd, timeout)
    if not r.get("ok"):
        return r
    by_rule = r["by_rule"]
    return {"ok": True,
            "defect_total": sum(by_rule.get(c, 0) for c in DEFECT_CLASSES),
            "by_rule": by_rule,
            "findings": r["raw"],
            "paths": [str(p) for p in paths]}


def measure_tree(*, timeout: int = 900) -> dict:
    """The WHOLE-TREE measure, used to prove the unit is REACHABLE.

    Deliberately separate from `measure()`: this one exists to show the EXCLUDED
    style share (so "the raw count can never be 0" is a measurement, not an
    opinion), while `measure()` is what a gate runs on ONE written file. A gate
    that swept the whole tree on every write would be the wrong population AND
    too slow.
    """
    cmd = [_interpreter(), "-m", "ruff", "check", str(BASE_DIR),
           "--exclude", ".venv", "--exclude", "node_modules",
           "--exclude", "qc_evidence", "--output-format", "json"]
    r = _run(cmd, timeout)
    if not r.get("ok"):
        return r
    by_rule = r["by_rule"]
    return {"ok": True,
            "raw_total": len(r["raw"]),
            "defect_total": sum(by_rule.get(c, 0) for c in DEFECT_CLASSES),
            "by_rule": by_rule,
            "excluded": {c: by_rule.get(c, 0) for c in EXCLUDED_STYLE}}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="the ONE ruff reader")
    ap.add_argument("paths", nargs="*",
                    help="files to measure; omit for the whole-tree report")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = measure(args.paths) if args.paths else measure_tree()
    if args.json:
        print(json.dumps(res, indent=1, ensure_ascii=False, default=str))
    elif not res.get("ok"):
        print("UNKNOWN: %s" % res.get("reason"))
    else:
        print("defect classes %s = %d" % (",".join(DEFECT_CLASSES),
                                          res["defect_total"]))
        print("by rule: %s" % res["by_rule"])
        if "raw_total" in res:
            print("raw total = %d; excluded style %s"
                  % (res["raw_total"], res["excluded"]))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())