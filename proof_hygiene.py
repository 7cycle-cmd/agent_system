"""proof_hygiene.py -- the META-GUARD for the three bugs I kept repeating.

WHY THIS EXISTS. The human: *"do it all now, not let's task be BUG for future"*.

Three defects recurred in MY OWN proofs, and each one produced a GREEN proof that
verified NOTHING — the worst kind, because a green proof is read as evidence:

  1. `or True` inside a `check(...)` — an assertion that CANNOT FAIL.
     MEASURED: 18 sites carry `or True`; most are `add(...) or True` used as a
     STATEMENT to ignore a return value, which is harmless. Only the form INSIDE an
     assertion is the defect.

  2. Scanning SOURCE **TEXT** for a clause — the docstring QUOTES the clause being
     removed, so the check matches its own history.
     MEASURED: I did this TWICE in one proof (whole file, then
     `ast.get_source_segment(fn)`, which INCLUDES the docstring), then had to walk the
     parse tree for a `Compare` node to get it right.

  3. A FIXED PROBE FILENAME — a leftover probe is already in the stamp, so `os.utime`
     changes nothing and a positive control goes RED for a CORRECT subject.
     MEASURED: 6 files write a fixed probe name.

EVERY DETECTOR IS AST-BASED, and that is the point: the bug being detected IS "a text
check fooled by prose". A text-based guard would be fooled by its own docstrings — the
exact defect it exists to catch.

THE GUARD REPORTS. IT DOES NOT FAIL THE CORPUS. The pre-existing population contains
many harmless and many correct uses, so failing on it would be a false positive. The
job is to make the NEXT one impossible to write unnoticed.

Run: `.venv\\Scripts\\python.exe proof_hygiene.py`
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

# Which files the guard inspects. A proof is the thing whose GREEN must mean
# something; a production module is checked by its own tests.
PROOF_PREFIXES = ("_proof_", "_measure_", "_check_")

# Forms that indicate the value is only IGNORED (a statement), not asserted.
_IGNORE_CALLS = {"add", "append", "extend", "update", "insert", "remove", "discard",
                 "execute", "executemany", "commit", "close", "write_text",
                 "mkdir", "unlink", "utime", "setdefault", "pop"}

# The names a proof uses to RECORD a verdict. A `BoolOp` inside one of these is an
# assertion; the same `BoolOp` as a bare statement is not.
ASSERTION_CALLS = {"check", "assert_true", "expect", "verify"}

# A source-text variable name that a code-shaped literal is searched in.
_SOURCE_TEXT_NAMES = ("src", "source", "text", "code", "body", "content", "raw",
                      "s", "txt")

# A fixed fixture/probe name. `_diag_`/`_proof_` PREFIXES are the same shape as the
# scratch-file class the terminology work already found.
_FIXED_FIXTURE_RE = re.compile(
    r"^_?(proof|probe|diag|tmp|tmp_probe|fixture|test)_[A-Za-z0-9_]+\.(py|txt|db|json|sql)$")

# A call that CREATES a file, and the call that STAMPS one. A fixture is only a
# stale-fixture hazard when the SAME fixed name is BOTH -- that is the
# create-then-stamp-then-check pattern whose stamp a leftover already matches.
_WRITE_CALLS = {"write_text", "write_bytes", "mkdir", "touch"}
_STAMP_CALLS = {"utime"}


def proof_files(root: Path | None = None) -> list[Path]:
    root = root or BASE
    return sorted(p for p in root.glob("*.py")
                  if p.name.startswith(PROOF_PREFIXES))


# ---------------------------------------------------------------------------
# DETECTOR 1 -- an assertion that cannot fail
# ---------------------------------------------------------------------------

def always_true_assertions(source: str) -> list[dict[str, Any]]:
    """`or True` (or `True or ...`) INSIDE a call that records a verdict.

    AST, so a `or True` written inside a DOCSTRING is not a node and is not found.
    And a bare `add(...) or True` STATEMENT is counted separately as `ignored`,
    because that form is a deliberate way to discard a return value, not a lie.

    MEASURED BUG IN MY FIRST DRAFT. I wrote `id(n) is wanted`. `id()` returns a NEW
    int object each call, so two calls for the SAME node give equal ints that are
    NOT identical -- `is` is False, and the detector never matched. Every positive
    control failed, which is exactly why the controls exist. The fix is set
    membership, which compares ints by VALUE.
    """
    out: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{"kind": "SYNTAX_ERROR", "line": e.lineno, "detail": str(e)}]
    roots = _asserted_toplevel_ids(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        if not any(isinstance(v, ast.Constant) and v.value is True
                   for v in node.values):
            continue
        seg = ast.unparse(node)
        if id(node) in roots:
            out.append({"kind": "ALWAYS_TRUE_ASSERTION", "line": node.lineno,
                        "code": seg[:80],
                        "why": "an `or True` at the ROOT of a verdict can never fail"})
        else:
            out.append({"kind": "IGNORED_VALUE", "line": node.lineno,
                        "code": seg[:80],
                        "why": "not the root of the verdict — reported, not a defect"})
    # A LITERAL `True` verdict is the SAME defect SHAPE. MEASURED: while fixing the 16
    # above I wrote `check(..., True)` myself.
    #
    # BUT MEASURED FURTHER: 163 live sites use this shape and MOST ARE LEGITIMATE --
    # a "witness" check, where reaching the line IS the evidence, e.g.
    #
    #     except KindMismatch as e:
    #         check("discover() is REFUSED by the KIND gate", True)
    #
    # Here the control flow IS the assertion: if the refusal did not happen, the
    # `except` never runs and the check never executes. Some others (`else:` branches,
    # a bare tally) are NOT witnessed that way and are tally marks.
    #
    # Telling those apart needs control-flow judgement, so this class is REPORTED
    # SEPARATELY and NOT counted as a defect. Claiming 163 defects would be the
    # false-positive failure this guard exists to avoid.
    for node in ast.walk(tree):
        if id(node) not in roots:
            continue
        if isinstance(node, ast.Constant) and node.value is True:
            out.append({"kind": "LITERAL_TRUE_VERDICT", "line": node.lineno,
                        "code": "check(..., True)",
                        "why": "a literal True verdict; LEGITIMATE when reaching the "
                               "line is the evidence (e.g. inside except), a tally "
                               "mark otherwise -- reported for review"})
    return out


def _asserted_toplevel_ids(tree: ast.AST) -> set[int]:
    """Ids of the TOP-LEVEL expression of each assertion argument.

    MEASURED, and this is the difference between a defect and a false positive.
    `x or True` as the whole verdict is an assertion that cannot fail. But
    `(execute(...) or True) and REAL_CHECK` is still `REAL_CHECK` -- there the
    `or True` only discards a call's return value, which is a deliberate idiom
    (`_proof_capability_tag.py:213` is exactly this, and my first rule called it a
    defect). So only the ROOT counts.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            ids.add(id(node.test))
        elif isinstance(node, ast.Call) and _call_name(node) in ASSERTION_CALLS:
            for arg in list(node.args) + [k.value for k in node.keywords]:
                ids.add(id(arg))
    return ids


# ---------------------------------------------------------------------------
# DETECTOR 2 -- a TEXT scan standing in for a structural claim
# ---------------------------------------------------------------------------

def text_scans_for_code(source: str) -> list[dict[str, Any]]:
    """A `Compare` searching a SOURCE-TEXT variable for a CODE-SHAPED literal.

    The hazard: the literal is the name of a thing the proof also DOCUMENTS, so the
    docstring paragraph explaining the fix contains the same text, and the check
    matches the prose instead of the code.

    MEASURED BUG IN MY SECOND DRAFT. I read the source-text name off `node.left`.
    But for `"def check(" in src` the LEFT is the literal and `src` is a COMPARATOR,
    so my detector looked for the variable in the wrong place and found nothing. The
    positive control caught it. Both orientations are handled now, plus the
    `src.find(literal)` method form.
    """
    out: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and any(
                isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
            pairs = [(node.left, c) for c in node.comparators]
            for left, right in pairs:
                lit = _code_shaped_literal(left, right)
                if lit is not None:
                    out.append({"kind": "TEXT_SCAN_FOR_CODE", "line": node.lineno,
                                "code": ast.unparse(node)[:80], "literal": lit[:60],
                                "why": "a code-shaped literal searched in source "
                                       "TEXT; the docstring may quote it"})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr not in ("find", "index", "count", "split", "startswith"):
                continue
            if not _is_source_name(node.func.value) or not node.args:
                continue
            lit = _code_shaped_literal(node.args[0], None)
            if lit is not None:
                out.append({"kind": "TEXT_SCAN_FOR_CODE", "line": node.lineno,
                            "code": ast.unparse(node)[:80], "literal": lit[:60],
                            "why": "a source-text method searched for a code-shaped "
                                   "literal"})
    return out


def _code_shaped_literal(a: ast.AST, b: ast.AST | None) -> str | None:
    """A code-shaped string constant in a comparison with a source-text variable."""
    for one, other in ((a, b), (b, a) if b is not None else (None, None)):
        if one is None:
            continue
        if isinstance(one, ast.Constant) and isinstance(one.value, str) \
                and _looks_code_shaped(one.value):
            if other is None or _is_source_name(other):
                return one.value
    return None


def _is_source_name(node: ast.AST) -> bool:
    name = node.id if isinstance(node, ast.Name) else (
        node.attr if isinstance(node, ast.Attribute) else "")
    return name.lower() in _SOURCE_TEXT_NAMES


def _looks_code_shaped(lit: str) -> bool:
    if len(lit) < 8:
        return False
    markers = ("(", ")", "=", "def ", "class ", "import ", "self.", "->",
               "SELECT ", "CREATE ", " in ", "not ")
    return any(m in lit for m in markers)


# ---------------------------------------------------------------------------
# DETECTOR 3 -- a fixed fixture / probe filename
# ---------------------------------------------------------------------------

def fixed_probe_names(source: str) -> list[dict[str, Any]]:
    """A fixed fixture name used as BOTH a write target AND a `os.utime` target.

    The hazard: the name is fixed, so a leftover from a cancelled run is already
    recorded in the stamp, `os.utime` produces no change, and the POSITIVE CONTROL
    goes RED for a correct subject. A fixture name must be unique per run.

    MEASURED WHY THIS IS NARROW. My first version flagged any string that LOOKED
    like a fixture name and reported 256 hits -- almost all of them a proof
    LEGITIMATELY reading its own source or a sibling proof. A name alone is not a
    fixture. The defect needs BOTH a write and a stamp of the same fixed name in one
    function, with no `uuid`; only then does a leftover defeat the control.
    """
    out: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        if "uuid" in ast.unparse(fn):
            continue  # unique per run -- the correct form
        names: list[tuple[str, int]] = []
        calls: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and _FIXED_FIXTURE_RE.match(node.value):
                names.append((node.value, node.lineno))
            elif isinstance(node, ast.Call):
                calls.add(_call_name(node))
        if not names:
            continue
        # The defect needs BOTH a WRITE and a STAMP of a fixed name; then a leftover
        # already matches the stamp and the positive control goes RED for a correct
        # subject. A name that is only READ is not a fixture.
        if (calls & _WRITE_CALLS) and (calls & _STAMP_CALLS):
            for value, line in names:
                out.append({"kind": "FIXED_FIXTURE_NAME", "line": line,
                            "name": value, "code": value,
                            "why": "the same fixed name is written AND stamped without "
                                   "a uuid, so a leftover defeats the positive control"})
    # A nested function is reached by BOTH the Module walk and its own walk, so the
    # same (name, line) can be appended twice. Dedupe at the END, not per scope.
    seen: set[tuple[str, int]] = set()
    deduped: list[dict[str, Any]] = []
    for h in out:
        key = (h["name"], h["line"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(h)
    return deduped


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


# ---------------------------------------------------------------------------
# THE REPORT
# ---------------------------------------------------------------------------

DETECTORS = (
    ("always_true_assertions", always_true_assertions),
    ("text_scans_for_code", text_scans_for_code),
    ("fixed_probe_names", fixed_probe_names),
)


def scan(root: Path | None = None) -> dict[str, Any]:
    """Run all detectors over every proof file. REPORTED, never asserted."""
    root = root or BASE
    files = proof_files(root)
    per_kind: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in DETECTORS}
    for p in files:
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, fn in DETECTORS:
            for hit in fn(src):
                per_kind[name].append(dict(hit, file=p.name))
    real = [h for h in per_kind["always_true_assertions"]
            if h["kind"] == "ALWAYS_TRUE_ASSERTION"]
    literal = [h for h in per_kind["always_true_assertions"]
               if h["kind"] == "LITERAL_TRUE_VERDICT"]
    return {
        "ok": True,
        "files_scanned": len(files),
        "always_true_assertion_count": len(real),
        "literal_true_verdict_count": len(literal),
        "ignored_value_count": len(per_kind["always_true_assertions"])
        - len(real) - len(literal),
        "text_scan_for_code_count": len(per_kind["text_scans_for_code"]),
        "fixed_fixture_name_count": len(per_kind["fixed_probe_names"]),
        "always_true_assertions": real,
        "literal_true_verdicts": literal[:10],
        "text_scans_for_code": per_kind["text_scans_for_code"][:20],
        "fixed_probe_names": per_kind["fixed_probe_names"][:20],
        "note": ("REPORTED, not asserted. The pre-existing population contains "
                 "harmless `x or True` STATements, legitimate witness checks, and "
                 "correct text uses, so failing on it would be a false positive."),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    r = scan()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print("=== proof_hygiene: the three bugs that made a GREEN proof mean nothing ===")
    print("   files scanned              : %d" % r["files_scanned"])
    print("   ALWAYS-TRUE assertions     : %d   <- the defect" %
          r["always_true_assertion_count"])
    print("   LITERAL-True verdicts      : %d (mostly legitimate witness checks)" %
          r["literal_true_verdict_count"])
    print("   `or True` used as a STATEMENT: %d (harmless, reported)" %
          r["ignored_value_count"])
    print("   TEXT scan for code         : %d" % r["text_scan_for_code_count"])
    print("   fixed fixture names        : %d" % r["fixed_fixture_name_count"])
    for key, title in (("always_true_assertions", "ALWAYS-TRUE ASSERTIONS"),
                       ("text_scans_for_code", "TEXT SCANS FOR CODE"),
                       ("fixed_probe_names", "FIXED FIXTURE NAMES")):
        if not r[key]:
            continue
        print()
        print("=== %s ===" % title)
        for h in r[key]:
            print("   %-46s :%-5s %s" % (h["file"], h["line"], h.get("code", h.get("name", ""))))
    print()
    print("   %s" % r["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
