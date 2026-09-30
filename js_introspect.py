# -*- coding: utf-8 -*-
"""js_introspect.py — read JS/Vue source the RIGHT way, so a checker measures its object.

WHY THIS MODULE EXISTS
----------------------
`code_introspect.py` fixed this defect class for PYTHON: a proof that asked "does
the source mention X" about a module, and got a YES from the module's OWN COMMENT
that said it does NOT do X. Seven proofs broke that way. Its rule is:

    STRUCTURE -> the AST | INTENT -> docstrings+comments | BEHAVIOUR -> run it

`code_introspect` cannot read JavaScript (it uses `ast`), so the JS checks in this
repo had NO correct reader — and the SAME defect came back three times in one
session, all in `_proof_chat_registry.py`:

  1. `"{ ...s }" not in wf`            matched the comment WARNING against
                                       spreading a reactive proxy.
  2. `"/api/case/" not in helper`      matched the comment EXPLAINING the rename.
  3. `"entity_id" not in src`          matched the docstring QUOTING the user.

Each was a check that FAILED while the code was correct, and each was fixed by
hand-writing another partial filter. `_js_splice_scan.py` had already recorded
that hand-written filters produce false positives ("Counting quotes across the
whole Python file produced 120 false positives"). Re-writing the filter a fourth
time is the bug, so the reader lives here instead.

THE RULE FOR JS, SAME SHAPE AS PYTHON
-------------------------------------
    STRUCTURE  -> read CODE ONLY      (`code_only`, `imports`, `exported_names`)
    INTENT     -> read comments       (`comments`; what a Vue component EXPLAINS)
    BEHAVIOUR  -> RUN it              (a browser measure; do not read at all)

`mentions()` is deliberately NOT provided here either. "Does the file mention X"
is ill-posed — a comment, a template literal and an identifier all "mention" X and
mean different things. Callers must say which of the three they mean.

WHY `strip_comments` IS A SCANNER AND NOT `line.split("//")`
------------------------------------------------------------
A naive split breaks on any `//` inside a string, and this repo has them:
`'https://127.0.0.1:18765'` becomes `'https:` plus a phantom comment, so a check
for a URL silently reads the WRONG text. The scanner tracks string state
(`'`, `"`, backtick) and escape sequences, so a `//` inside a string is content
and a `//` outside one is a comment.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "read_source", "code_only", "comments", "intent_text", "says",
    "has_code", "imports", "exported_names", "declared_functions",
    "assert_no_string_in_source_checks",
]


def read_source(path: Path | str) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# the scanner
# ---------------------------------------------------------------------------
def _scan(source: str) -> tuple[str, list[str]]:
    """Return (code_only, comments). One pass, string-aware.

    A character scanner rather than a regex, because the ONLY correct way to tell
    a real `//` from one inside `'https://…'` is to track whether we are inside a
    string — which a regex cannot do without breaking on the next line.
    """
    out: list[str] = []
    comments: list[str] = []
    i = 0
    n = len(source)
    # The current string delimiter, or None. A backtick (template literal) can
    # span lines, so `state` must survive the newline.
    quote: str | None = None
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""

        if quote is not None:
            # Inside a string: copy verbatim, honour escapes, stop at the closer.
            if ch == "\\":
                out.append(source[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(ch)
            i += 1
            continue

        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            continue

        # A regex literal is ALSO not a comment. `/\//` and `//` differ only by
        # context, and a regex can contain quotes, so it is copied whole when it
        # starts where a value may appear.
        if ch == "/" and nxt not in ("/", "*") and _regex_may_start(source, i):
            j = _copy_regex(source, i)
            if j > i:
                out.append(source[i:j])
                i = j
                continue

        if ch == "/" and nxt == "/":
            j = source.find("\n", i)
            if j == -1:
                j = n
            comments.append(source[i + 2:j])
            i = j
            continue

        if ch == "/" and nxt == "*":
            j = source.find("*/", i + 2)
            if j == -1:
                j = n
            else:
                j += 2
            comments.append(source[i + 2:j - 2 if j >= 2 else j])
            i = j
            continue

        out.append(ch)
        i += 1
    return "".join(out), comments


_VAL_BEFORE_REGEX = set("(,=:[!&|?{};+-*%~^<>\n")


def _regex_may_start(src: str, i: int) -> bool:
    """Heuristic: a `/` starts a regex when the previous non-space char is an
    operator or an opener, never after an identifier/number/`)` (division)."""
    j = i - 1
    while j >= 0 and src[j] in " \t":
        j -= 1
    if j < 0:
        return True
    return src[j] in _VAL_BEFORE_REGEX


def _copy_regex(src: str, i: int) -> int:
    """Copy a regex literal starting at `i`, or return `i` when it is not one."""
    j = i + 1
    n = len(src)
    in_class = False
    while j < n:
        ch = src[j]
        if ch == "\\":
            j += 2
            continue
        if ch == "\n":
            return i                      # unterminated -> not a regex
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            j += 1
            while j < n and src[j].isalpha():   # flags
                j += 1
            return j
        j += 1
    return i


def code_only(path: Path | str | None = None, *,
              source: str | None = None) -> str:
    """The file's CODE, with every comment removed. STRUCTURE questions use this.

    This is the replacement for `"X" not in raw_source`: a raw search reads the
    comments too, so a comment that says "we do NOT do X" answers YES to "does it
    do X".
    """
    src = source if source is not None else read_source(path)  # type: ignore[arg-type]
    return _scan(src)[0]


def comments(path: Path | str | None = None, *,
             source: str | None = None) -> list[str]:
    """Every `//` and `/* */` comment. INTENT questions use this."""
    src = source if source is not None else read_source(path)  # type: ignore[arg-type]
    return _scan(src)[1]


def has_code(path: Path | str | None, needle: str, *,
             source: str | None = None) -> bool:
    """True when `needle` appears in the CODE (comments excluded).

    The correct replacement for `needle in wf`, which cannot tell an
    implementation from a comment about one.
    """
    if not str(needle):
        raise ValueError("empty needle: it would match every file")
    return str(needle) in code_only(path, source=source)


def _norm(s: str) -> str:
    return " ".join(str(s).split())


def intent_text(path: Path | str | None = None, *,
                source: str | None = None) -> str:
    """Comments + string literals, whitespace-normalised, as ONE string."""
    src = source if source is not None else read_source(path)  # type: ignore[arg-type]
    parts = [_norm(c) for c in comments(None, source=src)]
    parts += [_norm(s) for s in string_literals(src)]
    return " ".join(p for p in parts if p)


def says(path: Path | str | None, phrase: str, *,
         source: str | None = None) -> bool:
    """True when the file SAYS `phrase` (in a comment or a string), ignoring
    wrapping. The JS twin of `code_introspect.says`."""
    if not _norm(phrase):
        raise ValueError("empty phrase: it would match every file")
    return _norm(phrase) in intent_text(path, source=source)


def string_literals(source: str) -> list[str]:
    """Every quoted string in the file (code only, so a comment's quotes are out)."""
    code = _scan(source)[0]
    out: list[str] = []
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            buf: list[str] = []
            while j < n:
                if code[j] == "\\":
                    buf.append(code[j:j + 2])
                    j += 2
                    continue
                if code[j] == ch:
                    break
                buf.append(code[j])
                j += 1
            out.append("".join(buf))
            i = j + 1
            continue
        i += 1
    return out


# ---------------------------------------------------------------------------
# STRUCTURE — imports / exports / declared names
# ---------------------------------------------------------------------------
_IMPORT_RE = re.compile(
    r"""\bimport\s+(?:([\w${},*\s]+?)\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)""")
_EXPORT_FN_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")
_FN_RE = re.compile(
    r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")


def imports(path: Path | str | None = None, *,
            source: str | None = None) -> set[str]:
    """Module specifiers this file IMPORTS. STRUCTURE, from code only.

    "Does this file import X" is an IMPORT question. A raw search answers it with
    a comment saying X is NOT imported, which is exactly defect 7 in
    `code_introspect`'s header.
    """
    src = code_only(path, source=source)
    out: set[str] = set()
    for m in _IMPORT_RE.finditer(src):
        spec = m.group(2) or m.group(3)
        if spec:
            out.add(spec)
    return out


def exported_names(path: Path | str | None = None, *,
                   source: str | None = None) -> set[str]:
    """Names declared `export function …`. STRUCTURE."""
    return set(_EXPORT_FN_RE.findall(code_only(path, source=source)))


def declared_functions(path: Path | str | None = None, *,
                       source: str | None = None) -> set[str]:
    """Every `function name(…)`. STRUCTURE."""
    return set(_FN_RE.findall(code_only(path, source=source)))


# ---------------------------------------------------------------------------
# THE GUARD — refuse the anti-pattern itself
# ---------------------------------------------------------------------------
# A variable that holds RAW FILE TEXT. Naming one of these is the tell.
_RAW_SOURCE_NAMES = (
    re.compile(r"^(src|source|text|raw)\w*$"),
    re.compile(r"^(helper|app|comp|component|tui|wf|bundle|html|js|css)$"),
    re.compile(r"\w+_(src|source|text|raw)$"),
)
# Tokens that make a literal a CODE question rather than a prose question.
_CODE_TOKENS = ("<", "//", "#", "(", ")", "{", "}", "import ", "def ",
                "api/", "export ", "function ", "SELECT ", "INSERT ", "=>")
# The ONLY way to allow a line that must quote the pattern: declare it in the
# file. A commented waiver is visible in review; a hand-mutated copy is not.
_WAIVER = "introspect-ok"


def assert_no_string_in_source_checks(
    path: Path | str,
) -> list[dict[str, object]]:
    """Find `"literal" (not) in <raw source var>` checks in a proof.

    THE DEFECT THIS REFUSES. A proof that asks a STRUCTURE question with a
    substring search over raw source reads the file's own COMMENTS, so a comment
    saying "the code does NOT do X" answers YES to "does it do X". Three checks
    broke that way in one session, and each was "fixed" by hand-writing another
    filter instead of using a reader.

    This returns the offending checks — it does NOT decide whether a match is
    legitimate. A caller reports them, and the allowlist below is for the
    INTENT-shaped use (`ci.says(...)`, which normalises wrapping and is fine).
    The returned dicts carry the line number and the literal, so a reader can
    check the claim instead of trusting this rule.

    A line carrying `# introspect-ok` is SKIPPED — the only way to allow a
    deliberate control that must quote the pattern, declared IN the file.
    """
    src = read_source(path)
    out: list[dict[str, object]] = []
    # The pattern: a string literal compared with `in` against a bare identifier.
    pat = re.compile(
        r"""(["'])(?P<lit>.{2,}?)\1\s+(?P<neg>not\s+)?in\s+(?P<var>[A-Za-z_]\w*)""")
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if _WAIVER in line:
            continue
        for m in pat.finditer(line):
            lit = m.group("lit")
            var = m.group("var")
            if not any(rx.match(var) for rx in _RAW_SOURCE_NAMES):
                continue
            if not any(tok in lit for tok in _CODE_TOKENS):
                continue
            out.append({"line": i, "literal": lit, "variable": var,
                        "negated": bool(m.group("neg")), "text": stripped[:110]})
    return out


def self_check() -> dict[str, object]:
    """Prove the readers FIND the thing, on a fixture with every real defect.

    A reader is only trustworthy when it has been shown to find the defect AND to
    NOT be fooled by the lookalike. Both are asserted here.
    """
    fixture = (
        '/**\n'
        ' * chat_widget.js — a comment that says the code does NOT use case_modal\n'
        ' * and mentions /api/case/list as the OLD path.\n'
        ' */\n'
        'import { createApp } from \'vue\';\n'
        'const URL = \'https://127.0.0.1:18765/api/chat_registry/list\';\n'
        '// a trailing comment with { ...s } in it\n'
        'export function mount() {\n'
        '  const a = 1 / 2;          // division, NOT a regex\n'
        '  const re = /a\\/b/g;       // a regex containing an escaped slash\n'
        '  return a;\n'
        '}\n'
    )
    return {
        "comment_seen": any("does NOT use case_modal" in c
                            for c in comments(None, source=fixture)),
        "comment_is_NOT_in_code": not has_code(
            None, "does NOT use case_modal", source=fixture),
        "url_survived_the_scanner": "https://127.0.0.1:18765" in code_only(
            None, source=fixture),
        "regex_slash_not_treated_as_comment": "a\\/b" in code_only(
            None, source=fixture),
        "division_preserved": "1 / 2" in code_only(None, source=fixture),
        "imports_found": sorted(imports(None, source=fixture)),
        "export_found": sorted(exported_names(None, source=fixture)),
        "spread_in_comment_not_code": not has_code(
            None, "{ ...s }", source=fixture),
        "says_finds_the_comment": says(None, "does NOT use case_modal",
                                       source=fixture),
        "old_path_only_in_comment": not has_code(
            None, "/api/case/list", source=fixture),
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
