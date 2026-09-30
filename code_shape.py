"""code_shape.py — read a file's SHAPE by AST, and answer ONE question about it:
is a parameter OVERWRITTEN before it is used?

WHY THIS EXISTS (the user, 2026-09-23)
--------------------------------------
    "output value in function can be hardcode easy by re-define $A = 0 at
     function ABC"
    "function ABC / $A = 0 / $A + $B = 1 / output = "
    "does we have checking to block such PK design"

    MEASURED: no. `hardcode_scan` is LINE-based (`re.search(pat, line)`,
    `hardcode_scan.py:242-252`), and `A = 0` matches none of its seven rules —
    it is not a path, a pixel, a URL, a foreign key or a model name. So the
    pattern the user described is INVISIBLE to the existing scanner.

WHY IT IS THE WORST KIND OF HARDCODE
------------------------------------
A literal such as `"qwen2.5vl:7b"` is a VALUE that could have been looked up.
A shadowed parameter is worse: it makes the OUTPUT TRUE BY CONSTRUCTION.

    def abc(A, B):
        A = 0            # the input is gone
        return A + B == 1

No input can make that fail, so no test can catch it, and a QC that asks "does
the output equal 1?" is asking a question the function answers about itself. The
opposite proof — the negative control `factor_first_principle.py:94-99` demands
("can this factor pass while another fails?") — is destroyed at the source.

WHAT IT REPORTS, AND WHAT IT REFUSES TO SAY
-------------------------------------------
It reports a LIST of `{function, param, line, cite_ref, overwritten_at}`. It does
NOT report a verdict: the same discipline `hardcode_scan` states for itself
("this produces a REVIEW list, not a verdict"). A parameter re-assigned to a
COMPUTED value is a normal, correct thing; the FINDING is the position, and the
reviewer decides.

    def f(A, B):
        A = A + 1        # still reported (the param is rebound)
        return A + B

Reporting it is CORRECT: the original argument no longer reaches the return, so
the caller's value is not what the function used. Whether that is intended is a
human question. The list says WHERE to look, never whether it is wrong.

FAIL LOUDLY, NEVER EMPTY
------------------------
An unreadable or unparseable file returns `ok=False` with a `reason`, NOT an
empty list. An empty list means "I read the file and found nothing" — the
positive-control rule: an empty result is refused unless the detector is proven
able to find something (`independent_review`, memory
`empty_detector_failure_class.md`). Collapsing "could not read" into "clean" is
how a broken detector reads as a pass.

Read-only. Writes no table, touches no DB.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

# The result codes. DISTINCT on purpose: a caller must be able to tell
# "read and found nothing" from "could not read", because they are different
# facts and only the first is evidence about the code.
OK = "OK"
UNREADABLE = "UNREADABLE"
UNPARSEABLE = "UNPARSEABLE"

# An EMPTY result and a FAILED read must never share a code.
READ_FAILURE_CODES = (UNREADABLE, UNPARSEABLE)


def _cite(path: str, line: int) -> str:
    """`path:line`, the citation form `citation_discipline` accepts.

    Repo-relative when possible, so the reference is stable across machines —
    the same normalisation `hardcode_scan.scan_file` uses for `cite_ref`.
    """
    try:
        rel = Path(path).resolve().relative_to(BASE_DIR)
        return "%s:%d" % (str(rel).replace("\\", "/"), int(line))
    except Exception:
        return "%s:%d" % (str(path).replace("\\", "/"), int(line))


def _param_names(fn: ast.AST) -> list[str]:
    """Every parameter name a function binds: positional, kwonly, vararg, kwarg.

    ALL of them, deliberately. `*args` and `**kwargs` are rebindable too, and a
    check that looked only at `args.args` would miss `def f(*a): a = 0`.
    """
    a = getattr(fn, "args", None)
    if a is None:
        return []
    out: list[str] = []
    for group in (getattr(a, "posonlyargs", []) or [],
                  getattr(a, "args", []) or [],
                  getattr(a, "kwonlyargs", []) or []):
        out.extend(str(x.arg) for x in group)
    if getattr(a, "vararg", None) is not None:
        out.append(str(a.vararg.arg))
    if getattr(a, "kwarg", None) is not None:
        out.append(str(a.kwarg.arg))
    return out


def _assigned_names(node: ast.AST) -> list[tuple[str, int]]:
    """Every name ASSIGNED in a statement: `(name, line)`.

    Handles the four ways a name is bound, because missing one is the defect
    this module exists to catch:
      * `A = ...`            -> ast.Assign with a Name target
      * `A: int = ...`       -> ast.AnnAssign
      * `A += ...`           -> ast.AugAssign (still a rebind)
      * `for A in ...` / `with ... as A` / tuple unpacking -> nested Name in
        Store context
    A `Name` in `Store` context is the general answer, so the walk below uses
    that as the catch-all and the explicit classes for clarity.

    IT DOES NOT CROSS A FUNCTION BOUNDARY. MEASURED, and it was a REAL BUG in
    the first version: `ast.walk` descends into a nested `def`, so

        def outer(A, B):
            def inner(A):
                A = 0          # belongs to `inner`
            return inner(B) + A

    reported `A` as rebound in BOTH `outer` and `inner`. Blaming the outer
    function for its inner function's rebind makes the citation WRONG, and a
    wrong citation is worse than no finding (`citation-discipline`: a finding
    must point at the thing it names, not at its container). So the traversal
    stops at any nested scope.
    """
    out: list[tuple[str, int]] = []

    # THE STATEMENT MAY *BE* A SCOPE. MEASURED, and it was the SECOND half of
    # the same bug: skipping a nested `def` as a CHILD is not enough, because
    # `redefined_params` calls this on each statement of the body — and one of
    # those statements IS the nested `FunctionDef`. Starting the traversal
    # inside it re-introduced the cross-boundary finding. So a scope is refused
    # at the ENTRY as well as on the way down.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
                         ast.ClassDef)):
        return out

    def visit(n: ast.AST) -> None:
        for child in ast.iter_child_nodes(n):
            # A NEW SCOPE owns its own bindings. Do not descend.
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                out.append((str(child.id), int(getattr(child, "lineno", 0) or 0)))
            visit(child)

    visit(node)
    return out


def redefined_params(path: str | Path) -> dict[str, Any]:
    """Parameters of every function in `path` that are REBOUND in its own body.

    Returns:
        {
          ok: bool,                 # False ONLY when the file could not be read
          code: str,                # OK | UNREADABLE | UNPARSEABLE
          path: str,
          findings: [ {function, param, line, cite_ref, overwritten_at} ],
          functions: int,           # how many functions were examined
          reason: str,              # present when ok is False
        }

    A method's `self` is EXCLUDED. Re-binding `self` is not the pattern the user
    described, and including it would make every `self.x = ...` a finding — the
    over-inclusive failure that turns a review list into noise.
    """
    p = Path(path)
    # ---- TWO MEASURED DEFECTS THIS DECODER FIXES (2026-09-28) --------------
    # DEFECT 1: reading with `encoding="utf-8"` alone turns a UTF-8 **BOM** into
    #   a leading `\ufeff`, and `ast.parse` then refuses the file with
    #   "invalid non-printable character U+FEFF". MEASURED: `check_db.py`,
    #   `create_db.py` and `run_ontology_revision_tests.py` each begin with
    #   `\xef\xbb\xbf` and COMPILE FINE (`python -m py_compile` exit 0), yet this
    #   function called all three UNPARSEABLE. A false UNREADABLE is the worst
    #   possible output here: the caller sees `ok=False` and reads "no findings".
    # DEFECT 2: `errors="replace"` silently corrupts a bad byte into U+FFFD. A
    #   corrupt file could then PARSE and be reported clean — the exact
    #   "could not read collapsed into clean" failure this module's docstring
    #   forbids. So decoding is now STRICT, and a genuine decode failure is
    #   UNREADABLE (honest), never a silent repair.
    # `utf-8-sig` strips a BOM when present and is byte-identical to `utf-8` when
    # it is absent, so this is a pure widening: no file that used to be readable
    # stops being readable, and UNPARSEABLE stays reachable for real syntax errors.
    raw: bytes | None = None
    src = ""
    decode_error = ""
    try:
        raw = p.read_bytes()
    except Exception as exc:
        return {"ok": False, "code": UNREADABLE, "path": str(p),
                "findings": [], "functions": 0,
                "reason": "could not read %s: %s: %s"
                          % (p, type(exc).__name__, exc)}
    for enc in ("utf-8-sig", "utf-8"):
        try:
            src = raw.decode(enc)
            decode_error = ""
            break
        except UnicodeDecodeError as exc:
            decode_error = "%s: %s" % (enc, exc)
        except Exception as exc:
            decode_error = "%s: %s: %s" % (enc, type(exc).__name__, exc)
    if decode_error:
        # NOT an empty list. "I could not read it" is not "it is clean".
        return {"ok": False, "code": UNREADABLE, "path": str(p),
                "findings": [], "functions": 0,
                "reason": ("could not decode %s as utf-8-sig or utf-8 (%s) — "
                           "a corrupt encoding is UNREADABLE, not clean"
                           % (p, decode_error))}
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return {"ok": False, "code": UNPARSEABLE, "path": str(p),
                "findings": [], "functions": 0,
                "reason": "could not parse %s: %s at line %s"
                          % (p, exc.msg, exc.lineno)}
    except Exception as exc:
        return {"ok": False, "code": UNPARSEABLE, "path": str(p),
                "findings": [], "functions": 0,
                "reason": "could not parse %s: %s: %s"
                          % (p, type(exc).__name__, exc)}

    findings: list[dict[str, Any]] = []
    n_functions = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        n_functions += 1
        params = [n for n in _param_names(fn) if n != "self"]
        if not params:
            continue
        # THE BODY ONLY, and each statement independently, so a rebind in a
        # NESTED function is attributed to THAT function rather than the outer
        # one. `ast.walk` on the body would cross the boundary.
        for stmt in fn.body:
            for name, line in _assigned_names(stmt):
                if name in params:
                    findings.append({
                        "function": str(fn.name),
                        "param": name,
                        "line": line,
                        "overwritten_at": line,
                        "cite_ref": _cite(str(p), line),
                    })
    return {"ok": True, "code": OK, "path": str(p),
            "findings": findings, "functions": n_functions, "reason": ""}


def has_redefined_params(path: str | Path) -> bool:
    """Convenience: True only when the file was READ and a finding exists.

    An UNREADABLE file returns False here, which is why the caller that needs to
    distinguish them must use `redefined_params` and read `ok` — this helper is
    for the simple yes/no case only.
    """
    r = redefined_params(path)
    return bool(r.get("ok") and r.get("findings"))


def scan_files(paths: list[str | Path]) -> dict[str, Any]:
    """Run `redefined_params` over several files. Aggregates; never swallows.

    Returns `{ok, files, findings, unreadable, findings_total}`. `unreadable`
    LISTS the files that could not be read, so a caller cannot mistake a partial
    scan for a complete one.
    """
    findings: list[dict[str, Any]] = []
    unreadable: list[dict[str, Any]] = []
    for path in paths:
        r = redefined_params(path)
        if not r["ok"]:
            unreadable.append({"path": r["path"], "code": r["code"],
                               "reason": r["reason"]})
            continue
        findings.extend(r["findings"])
    return {"ok": not unreadable, "files": len(paths), "findings": findings,
            "findings_total": len(findings), "unreadable": unreadable}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        description="find parameters REBOUND in their own function body")
    ap.add_argument("paths", nargs="+", help="python files to read")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    res = scan_files(args.paths)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    print("files          : %d" % res["files"])
    print("findings total : %d" % res["findings_total"])
    if res["unreadable"]:
        print("COULD NOT READ (%d) — this is NOT a clean result:"
              % len(res["unreadable"]))
        for u in res["unreadable"]:
            print("  %s  [%s]  %s" % (u["path"], u["code"], u["reason"]))
    for f in res["findings"]:
        print("  %s  %s(%s) rebound at %s"
              % (f["cite_ref"], f["function"], f["param"], f["overwritten_at"]))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
