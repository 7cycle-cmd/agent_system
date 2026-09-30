"""hardcode_audit.py — is a value DECLARED in a register, or typed by a human?

THE USER'S CHARGE (2026-09-24)
-----------------------------
    "pls confirm all evidence is from generator not by human hardcode mis-understand
     system ... we trust evidence only, not hardcode maker"

This module is the ENFORCEMENT half of the answer: it finds an uppercase mapping a
human typed and says whether a REGISTER could carry it instead. A number per module,
so "is this generated or hand-typed" is MEASURED rather than promised.

A LESSON FROM BUILDING THIS: my FIRST scan missed every annotated constant
(`X: list[str] = [...]` parses as `AnnAssign`, not `Assign`), so it reported
`none` for modules that were full of typed maps — a detector that could not detect.
BOTH forms are now found, and the proof plants one to keep that honest.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent

# A mapping whose entries are CODE SHAPE rather than a vocabulary a register should
# own. Kept deliberately SHORT and each with a reason, because a broad exemption list
# is how an audit becomes decorative.
CODE_SHAPED: dict[str, str] = {
    "READABLE_KINDS": "sqlite_master types the SQL engine defines, not our vocabulary",
    "CARRIER_PREFIXES": "string PREFIXES of cite forms, a parsing rule not a vocabulary",
    "READ_OF": "SQL verbs this module parses, defined by SQL",
    "CLASSES": "the four class NAMES this classifier emits, a closed code enum",
    "TERM_KINDS": "the term_kind values the schema permits, declared in the DDL",
    "VALUE_MODEL": "the two values a boolean column can hold, declared by the schema",
    "KEY_HINTS": "column-name SPELLINGS used to derive a query, not a vocabulary",
    "CHECKLIST": "the checklist ITEMS ARE the module's function table, not data",
    "CODE_ONLY_RENAMES": "declared rename PAIRS, each citing the line that renamed it",
    "RULES": "rule DEFINITIONS that name their own evidence, not a value list",
    "EVIDENCE_NEEDED": "the REMEDY text for each refusal code",
    "INSTRUMENT": "the instrument per role, which cites what checks it",
    "RIGHTS": "the meaning of each right; the RIGHts THEMSELVES are in the register",
    "NOT_APPLICABLE": "the REASON a register is an exception, not the exception list",
    "NO_NAME_KEY": "the REASON a register has no name column",
}

# A mapping that is a VOCABULARY a register should own. Anything here is a FINDING.
REGISTER_SHAPED = "a vocabulary of names/roles/values that a register should declare"

# A NAME PATTERN that marks a constant as a VOCABULARY rather than a loop constant.
#
# WHY THIS EXISTS, MEASURED: the first version classified EVERY non-exempt mapping as
# `register_shaped` and reported **3,558 findings** across 294 modules — including
# `SKIP_DIRS`, `WANTED`, `LIKE` and `BANDS`, which are plainly code. An audit that
# flags 3,558 things is as useless as one that flags none: nobody can act on it, and
# the real findings drown. A finding now needs a VOCABULARY-LIKE NAME.
VOCABULARY_NAME = re.compile(
    r"(_MAP$|^MAP_|_MAPS$|_TO_|^ROLES?$|^RIGHTS?$|_ROLES$|_RIGHTS$|VOCAB|NAMES$|"
    r"^KINDS?$|_KINDS$|_registry$|^TERMS?$|_ALIAS|_SYNONYM)", re.IGNORECASE)


def _classify(name: str) -> tuple[str, str]:
    """`(class, reason)` for a constant NAME. Three classes, so a finding is real.

      * `code_shaped`     — on the exemption list, with the reason the register cannot
                            carry it (an engine-defined value, a parsing rule).
      * `register_shaped` — a VOCABULARY-LIKE name: it reads as a map of domain
                            names/roles/values that a register should declare.
      * `unknown`         — neither. REPORTED as a number, NOT claimed as a finding,
                            because guessing here is what makes an audit noise.
    """
    if name in CODE_SHAPED:
        return "code_shaped", CODE_SHAPED[name]
    if VOCABULARY_NAME.search(name):
        return "register_shaped", REGISTER_SHAPED
    return "unknown", ("neither a declared exemption nor a vocabulary-like name; "
                       "reported for a human to judge, not auto-claimed")


def _mapping_size(v: ast.AST | None) -> int:
    if v is None:
        return 0
    if isinstance(v, ast.Dict):
        return len(v.keys)
    if isinstance(v, (ast.Tuple, ast.List, ast.Set)):
        return len(v.elts)
    return 0


def scan_file(path: Path) -> dict[str, Any]:
    """Every UPPERCASE mapping constant in a file, with its size and its class.

    Finds BOTH `X = {…}` (`Assign`) and `X: T = {…}` (`AnnAssign`). MEASURED: the
    first version found only `Assign`, so every annotated constant was invisible and
    the audit reported `none` for modules full of typed maps.
    """
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {"file": path.name, "ok": False, "reason": "SYNTAX_ERROR", "why": str(e)}
    found: list[dict[str, Any]] = []
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Assign):
            tgt = nd.targets[0]
            name = tgt.id if isinstance(tgt, ast.Name) else ""
            value = nd.value
        elif isinstance(nd, ast.AnnAssign):
            name = nd.target.id if isinstance(nd.target, ast.Name) else ""
            value = nd.value
        else:
            continue
        if not name or not name.isupper():
            continue
        size = _mapping_size(value)
        if not size:
            continue
        cls, why = _classify(name)
        found.append({"name": name, "entries": size, "class": cls,
                      "line": getattr(nd, "lineno", 0), "reason": why})
    return {"file": path.name, "ok": True, "constants": found,
            "entries": sum(f["entries"] for f in found),
            "register_shaped": sum(f["entries"] for f in found
                                   if f["class"] == "register_shaped"),
            "code_shaped": sum(f["entries"] for f in found
                               if f["class"] == "code_shaped"),
            "unknown": sum(f["entries"] for f in found
                           if f["class"] == "unknown")}


def scan(paths: list[Path] | None = None) -> dict[str, Any]:
    """Scan the given files (or every module at the repo root)."""
    if paths:
        files = paths
    else:
        files = sorted(BASE.glob("*.py"))
    per: list[dict[str, Any]] = []
    for p in files:
        r = scan_file(p)
        if r.get("ok") and r["entries"]:
            per.append(r)
    total = sum(r["entries"] for r in per)
    reg = sum(r["register_shaped"] for r in per)
    code = sum(r.get("code_shaped", 0) for r in per)
    unk = sum(r.get("unknown", 0) for r in per)
    return {"ok": True, "modules": len(per), "files": [r["file"] for r in per],
            "total_entries": total, "register_shaped": reg,
            "code_shaped": code, "unknown": unk,
            "per_module": per,
            "exemptions": sorted(CODE_SHAPED),
            "vocabulary_name_pattern": VOCABULARY_NAME.pattern,
            "would_be_red_if": ("a vocabulary a register could declare is typed into "
                                "a module instead")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scan", nargs="*", default=None,
                    help="files to scan (default: every *.py at the repo root)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    paths = [Path(p) for p in a.scan] if a.scan else None
    out = scan(paths)

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    print("modules with a typed mapping : %d" % out["modules"])
    print("typed mapping ENTRIES        : %d" % out["total_entries"])
    print("  register_shaped (a FINDING): %d" % out["register_shaped"])
    print("  code_shaped (exempt, with a reason): %d" % out["code_shaped"])
    print("  unknown (for a human, NOT claimed as a finding): %d" % out["unknown"])
    print()
    for r in sorted(out["per_module"], key=lambda x: -x["register_shaped"])[:25]:
        if not r["register_shaped"]:
            continue
        print("   %-30s entries=%-4d register_shaped=%d"
              % (r["file"], r["entries"], r["register_shaped"]))
        for c in r["constants"]:
            if c["class"] == "register_shaped":
                print("        %-22s %d entries  <-- could be a REGISTER" % (
                    c["name"], c["entries"]))
    print()
    print("exemptions (each needs a reason, and has one): %d" % len(out["exemptions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
