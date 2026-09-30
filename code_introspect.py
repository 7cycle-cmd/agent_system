# -*- coding: utf-8 -*-
"""code_introspect.py — read code the RIGHT way, so a checker measures its object.

WHY THIS MODULE EXISTS, WITH THE MEASUREMENTS THAT FORCED IT
-----------------------------------------------------------
Seven times in one session a proof FAILED while the code under test was correct,
because the check read the source the wrong way:

  1. A text search for `_RX_REGISTRY` matched the module's OWN DOCSTRING, which
     explained that it does not use that name.
  2. The same shape again in `failure_link`.
  3. `assert_no_similarity_scoring` flagged ITS OWN NAME (it contains "scoring"),
     so it was a guard nobody could run.
  4. An AST DOCSTRING reader found nothing, because the explanations live in `#`
     COMMENTS — and the AST does not see comments.
  5. An assertion named a phrase the module never contained.
  6. An assertion named a phrase the source splits across Python string
     CONCATENATION, so no substring search can find it whole.
  7. A grep for "does B import A's patterns" matched a comment, not an import.

THE RULE, ENCODED HERE RATHER THAN REMEMBERED
---------------------------------------------
    STRUCTURE  -> read the AST            (function_names, imported_names)
    INTENT     -> read docstrings AND comments, whitespace-normalised
    BEHAVIOUR  -> RUN it                  (do not read at all)

A plain text search is none of the three. Every reader returns a NORMALISED value
so a caller cannot re-introduce the newline / concatenation defects.

`mentions()` is deliberately NOT provided: "does the source mention X" is
ill-posed, and the seven failures above are what answering it produces. Callers
must say which of the three questions they mean.
"""
from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import Any


class IntrospectError(ValueError):
    """Raised when a caller asks for a reading this module refuses to fake."""


# ---------------------------------------------------------------------------
# STRUCTURE — the AST
# ---------------------------------------------------------------------------
def parse(source: str) -> ast.AST:
    return ast.parse(source)


def read_source(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def function_names(source: str) -> set[str]:
    """Every function and method name. STRUCTURE, not text."""
    return {n.name for n in ast.walk(parse(source))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def class_names(source: str) -> set[str]:
    return {n.name for n in ast.walk(parse(source)) if isinstance(n, ast.ClassDef)}


def call_names(source: str) -> set[str]:
    """Every name CALLED anywhere in the file, INCLUDING inside every function.

    SCOPE WARNING, learned from a real defect: this is a WHOLE-FILE set. Using it
    for "does function F call X" gives a FALSE POSITIVE whenever any OTHER
    function calls X — e.g. a module whose `main()` writes a file and whose
    `submit()` does not will still report `write_...` as called. That is the
    "check measured the wrong object" family: the right object for a per-function
    question is one function's body, which is `calls_in_function()` below.
    """
    out: set[str] = set()
    for n in ast.walk(parse(source)):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def _calls_in(node: ast.AST) -> set[str]:
    """Called names inside ONE subtree."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def calls_in_function(source: str, name: str) -> set[str]:
    """Called names inside ONE function's body. The per-function question.

    Returns an EMPTY set both when the function does not exist and when it calls
    nothing, so a caller MUST distinguish those (compare against
    `function_names(source)`) rather than treating "no such function" as "no
    calls" — a silently absent subject would make every negative check pass.
    """
    for n in ast.walk(parse(source)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return _calls_in(n)
    return set()


def calls_outside_functions(source: str) -> set[str]:
    """Called names NOT inside any function or method (module-level code).

    Needed because a call in a `if __name__` block is real code but belongs to no
    function, so `calls_in_function` cannot see it.
    """
    inside: set[int] = set()
    tree = parse(source)
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(n):
                inside.add(id(sub))
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and id(n) not in inside:
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def imported_names(source: str) -> set[str]:
    """Names this module actually IMPORTS.

    "Does B import A's patterns" is an IMPORT question, so it is answered from the
    import graph. A text search cannot answer it: a comment saying "does not use
    `_RX_REGISTRY`" contains the name without importing it.
    """
    out: set[str] = set()
    for n in ast.walk(parse(source)):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                out.add(a.asname or a.name)
        elif isinstance(n, ast.Import):
            for a in n.names:
                out.add(a.asname or a.name.split(".")[0])
    return out


def declared_routes(source: str) -> list[dict[str, Any]]:
    """Every `@app.route("/x")` decorator, read from the AST.

    THE DEFECT THIS REFUSES (measured, 2026-09-21 — the SAME class as the proof
    bug, a third site). `namespace_map` found routes with a LINE REGEX over raw
    source, so a string LITERAL inside a proof script —
    `not ci.has_code(HELPER, '@app.route("/api/case/')`, i.e. an assertion that
    the route is GONE — was read as a DECLARATION of `/api/case`. A phantom
    namespace appeared in the registry, with no implementing file, and its
    "dominant citation" pointed at the assertion that said the route does not
    exist. Prose about a route is not a route; only a decorator is.

    Each entry: {"path", "func", "line", "methods"}. `line` is the DECORATOR's
    line, because that is where the route is declared and what a citation must
    point at -- pointing at the `def` below it fails a route-citation check.
    """
    out: list[dict[str, Any]] = []
    for n in ast.walk(parse(source)):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in n.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            f = dec.func
            if not (isinstance(f, ast.Attribute) and f.attr == "route"):
                continue
            if not dec.args or not isinstance(dec.args[0], ast.Constant):
                continue
            path = dec.args[0].value
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            methods = ["GET"]
            for kw in dec.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    methods = [str(e.value).upper() for e in kw.value.elts
                               if isinstance(e, ast.Constant)]
            out.append({"path": path, "func": n.name, "line": dec.lineno,
                        "def_line": n.lineno, "methods": methods})
    return out


def string_literals(source: str) -> list[str]:
    """Every string constant, with ADJACENT LITERALS REJOINED.

    Measured defect 6: a sentence written as `"a b "` + `"c d"` cannot be found by
    searching for `"a b c d"`. Returning the joined value is what a reader means by
    "the text of this string", and it is the only form a search can rely on.
    """
    out: list[str] = []
    for n in ast.walk(parse(source)):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.JoinedStr):      # f-string: keep the literal parts
            out.append("".join(v.value for v in n.values
                               if isinstance(v, ast.Constant)
                               and isinstance(v.value, str)))
    return out


# ---------------------------------------------------------------------------
# INTENT — docstrings AND comments, whitespace-normalised
# ---------------------------------------------------------------------------
def docstrings(source: str) -> list[str]:
    out: list[str] = []
    for n in ast.walk(parse(source)):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                          ast.ClassDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) \
                    and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.append(first.value.value)
    return out


def comments(source: str) -> list[str]:
    """Every `#` comment. The AST CANNOT see these — measured defect 4.

    A docstring-only reader returned zero matches for explanations that were
    present all along inside comments. `tokenize` is the only reader that sees
    them, so a checker asking about intent must use this.
    """
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                out.append(tok.string.lstrip("#"))
    except (tokenize.TokenError, IndentationError):
        # A partially-edited file must not raise from a reader; return what was
        # read rather than pretending the file has no comments.
        pass
    return out


def code_only(path: Path | str | None = None, *,
              source: str | None = None) -> str:
    """The file's CODE, with comments AND docstrings replaced by BLANKS.

    THE MISSING READER. Measured 2026-09-21: `code_introspect` could answer
    STRUCTURE (AST) and INTENT (docstrings/comments) but there was no way to ask
    "is this identifier in the CODE" — so callers wrote `"X" not in src`, which
    reads comments and strings too. That is the identical defect class the module
    header lists seven times, and it came back three more times in one session:

      * `"{ ...s }" not in wf`      matched the comment WARNING against it
      * `"/api/case/" not in helper` matched the comment EXPLAINING the rename
      * `"entity_id" not in src`     matched the docstring QUOTING the user

    A PYTHON docstring is a STRING LITERAL, so stripping only `#` comments is not
    enough — measured: `entity_id` survived a comment-stripping pass because the
    mention was inside a docstring. Both are blanked here.

    WHY BLANKED IN PLACE, NOT RE-JOINED FROM TOKENS
    -----------------------------------------------
    DEFECT FOUND BY RUNNING IT (2026-09-21): the first version rebuilt the source
    from tokens joined with a space, so `tks.create_ticket(` came back as
    `tks . create_ticket (` and EVERY multi-token search failed — the reader
    silently changed what the code says. Removing a range of characters and
    leaving the rest BYTE-IDENTICAL is the only version where a search over the
    result means what it says. The length is preserved, so positions still map to
    the real file.

    For JavaScript use `js_introspect.code_only`; this reader uses `tokenize` and
    cannot parse JS.
    """
    if source is None:
        source = read_source(path)  # type: ignore[arg-type]
    spans: list[tuple[int, int]] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        # A partially-edited file must not raise. Fall back to blanking `#`
        # comments line-wise, so the reader degrades rather than lying.
        return re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), source)

    def _blank(tok) -> tuple[int, int]:
        (sl, sc), (el, ec) = tok.start, tok.end
        lines = source.splitlines(keepends=True)
        if sl == el:
            off = sum(len(x) for x in lines[:sl - 1])
            return off + sc, off + ec
        off_s = sum(len(x) for x in lines[:sl - 1]) + sc
        off_e = sum(len(x) for x in lines[:el - 1]) + ec
        return off_s, off_e

    doc_lines: set[int] = set()
    try:
        tree = parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.body:
                first = node.body[0]
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    doc_lines.add(int(first.value.lineno))
    except SyntaxError:
        doc_lines = set()

    for tok in toks:
        if tok.type == tokenize.COMMENT:
            spans.append(_blank(tok))
        elif tok.type == tokenize.STRING and tok.start[0] in doc_lines:
            spans.append(_blank(tok))

    out = list(source)
    for s, e in spans:
        for i in range(s, min(e, len(out))):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def has_code(path: Path | str | None, needle: str, *,
             source: str | None = None) -> bool:
    """True when `needle` appears in the CODE (comments + docstrings excluded).

    The correct replacement for `needle in src`. For a STRUCTURE question use the
    AST readers (`function_names`, `imported_names`, `call_names`) instead — they
    answer the question exactly, while this answers it by text.
    """
    if not str(needle):
        raise IntrospectError("empty needle: it would match every file")
    return str(needle) in code_only(path, source=source)


# ---------------------------------------------------------------------------
# THE GUARD — refuse the anti-pattern itself
# ---------------------------------------------------------------------------
# A variable that holds RAW FILE TEXT. Naming one of these is the tell that a
# substring search is reading the whole file rather than a structure.
_RAW_SOURCE_NAMES = (
    re.compile(r"^(src|source|text|raw)\w*$"),
    re.compile(r"^(helper|app|comp|component|tui|wf|bundle|html|js|css|spec)$"),
    re.compile(r"\w+_(src|source|text|raw)$"),
)
# Tokens that make a literal a CODE question rather than a prose question.
_CODE_TOKENS = ("<", "//", "#", "(", ")", "{", "}", "import ", "def ",
                "api/", "export ", "function ", "SELECT ", "INSERT ", "=>",
                "@app.route", "=", "**", "://")
# The ONLY way to allow a line that must quote the pattern: declare it in the
# file. A commented waiver is visible in review; a hand-mutated copy is not.
_WAIVER = "introspect-ok"


def assert_no_string_in_source_checks(
    path: Path | str | None,
    *,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Find `"literal" (not) in <raw source var>` checks in a proof script.

    THE DEFECT THIS REFUSES. A proof that asks a STRUCTURE question with a
    substring search over raw source reads the file's own COMMENTS AND
    DOCSTRINGS, so prose saying "the code does NOT do X" answers YES to "does it
    do X". Measured: three checks broke that way in one session, and each was
    "fixed" by hand-writing another partial filter — which is the bug, not the fix.

    This returns the offending checks; it does NOT decide whether a match is
    legitimate. The caller REPORTS them, so the claim is checkable rather than
    trusted. Intent-shaped checks belong in `says()`, which normalises wrapping.

    `source=` is accepted so a caller can exclude its own positive control from
    the scan — a guard that flags the fixture proving it works is the same defect
    as `forbidden_functions` flagging its own name (see the module header).

    A line carrying `# introspect-ok` is SKIPPED. That is the only way to allow a
    deliberate control that must quote the pattern -- a guard that is clean
    because the proof hand-mutated its own text to hide a line is not a guard. So
    the waiver is EXPLICIT and VISIBLE in the file, never applied to the scan.
    """
    src = source if source is not None else read_source(path)  # type: ignore[arg-type]
    out: list[dict[str, Any]] = []
    pat = re.compile(
        r"""(["'])(?P<lit>.{2,}?)\1\s+(?P<neg>not\s+)?in\s+(?P<var>[A-Za-z_]\w*)""")
    for i, line in enumerate(src.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        if _WAIVER in line:
            continue
        for m in pat.finditer(line):
            lit, var = m.group("lit"), m.group("var")
            if not any(rx.match(var) for rx in _RAW_SOURCE_NAMES):
                continue
            if not any(tok in lit for tok in _CODE_TOKENS):
                continue
            out.append({"line": i, "literal": lit, "variable": var,
                        "negated": bool(m.group("neg")),
                        "text": line.strip()[:110]})
    return out


def _norm(s: str) -> str:
    """Collapse ALL whitespace runs to single spaces.

    The fix for the newline half of defect 7: a sentence that wraps across lines
    is one sentence to a reader, and must be one to a checker.
    """
    return " ".join(str(s).split())


def intent_text(path: Path | str | None = None, source: str | None = None) -> str:
    """Docstrings + comments + string literals, whitespace-normalised, as ONE string.

    Use this for "does the module SAY X". Wrapping and concatenation cannot defeat
    it. A caller that instead searches raw source will keep being fooled by both,
    and by its own comments.
    """
    src = source if source is not None else read_source(path)  # type: ignore[arg-type]
    parts = [_norm(d) for d in docstrings(src)]
    parts += [_norm(c) for c in comments(src)]
    parts += [_norm(s) for s in string_literals(src)]
    return " ".join(p for p in parts if p)


def says(path: Path | str | None, phrase: str,
         source: str | None = None) -> bool:
    """True when the module SAYS `phrase`, ignoring wrapping and concatenation."""
    if not _norm(phrase):
        raise IntrospectError(
            "empty phrase: it would match every file, so the answer would be "
            "meaningless rather than wrong")
    return _norm(phrase) in intent_text(path, source)


# ---------------------------------------------------------------------------
# FORBIDDEN NAMES — a guard that cannot flag itself
# ---------------------------------------------------------------------------
def forbidden_functions(source: str, *, words: tuple[str, ...],
                        allow: tuple[str, ...] = ()) -> list[str]:
    """Function names matching a forbidden word AS A WHOLE WORD.

    Measured defect 3: substring matching made `assert_no_similarity_scoring`
    flag itself, so the guard refused to run. Whole-word matching plus an explicit
    `allow` list is what lets a checker inspect code that legitimately contains the
    word it forbids.
    """
    rules = tuple(re.compile(r"(^|_)%s($|_)" % re.escape(w.lower()))
                  for w in words)
    allowed = set(allow)
    return sorted(f for f in function_names(source)
                  if f not in allowed
                  and any(rx.search(f.lower()) for rx in rules))


def self_check() -> dict[str, Any]:
    """Run this module's OWN readers against a fixture containing every defect.

    A reader is only trustworthy if it has been shown to FIND the thing, and — for
    the forbidden-name guard — to NOT flag a legitimate lookalike. All four cases
    below are the real defects that broke seven proofs.
    """
    fixture = '''# a comment saying the module does NOT import `_RX_BANNED`
"""Docstring: the module says THESE WORDS."""
import os
from a import b as _RX_ALLOWED


def real_ratio(a, b):
    return 0.5


def assert_no_similarity_scoring():
    note = (
        "a sentence split across "
        "two literals"
    )
    return note
'''
    return {
        "comments_seen": comments(fixture),
        "finds_the_comment": any("does NOT import" in c for c in comments(fixture)),
        "finds_the_docstring": any("THESE WORDS" in d for d in docstrings(fixture)),
        "imports": sorted(imported_names(fixture)),
        "strings_rejoined": [s for s in string_literals(fixture)
                             if "literals" in s],
        "concatenation_rejoined": says(None, "split across two literals",
                                       source=fixture),
        "forbidden_word_boundary": forbidden_functions(
            fixture, words=("ratio", "score", "similar"),
            allow=("assert_no_similarity_scoring",)),
        "self_not_flagged": not forbidden_functions(
            fixture, words=("scoring",), allow=("assert_no_similarity_scoring",)),
        "mentions_is_absent": not hasattr(__import__(__name__), "mentions"),
    }


def main() -> None:
    import json
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(json.dumps(self_check(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()