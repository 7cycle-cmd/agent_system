# -*- coding: utf-8 -*-
"""terminology_cite.py — the LOGIC GENERATOR that makes a 7B's draft usable.

WHY (the user, 2026-09-23)
--------------------------
    "can LLM 7B help us to register"
    "by logic generator can help 7B strong than the best model agent, as we have
     the goal for the mission"

MEASURED, and it is the evidence for that claim. The 7B was asked to register
three real terms. Its `term_key` and `definition` were USABLE every time. Its
`cite_ref` was NOT:

    T1  cite_ref = "refuse: hypothetical scenario, no real VS Code doc"
    T2  cite_ref = "vscode_env_log_schema.txt"      <- the file DOES NOT EXIST
    T3  cite_ref = "refuse: hypothetical term without a concrete refer"

and the register ACCEPTED all three, because `add_term()` only checks that
`cite_ref` is NON-EMPTY:

    cite_ref='refuse: ...'            -> ok=True
    cite_ref='vscode_env_log_schema.txt' -> ok=True   (file absent)
    cite_ref=''                       -> ok=False

So the gate could not tell a citation from a sentence ABOUT not having one. A
citation that cannot be checked is not a citation — the rule
`citation-discipline` states and nothing enforced.

THE DIVISION OF LABOUR (this module is the second half)
-------------------------------------------------------
    the 7B            drafts the NAME and the DEFINITION   (language, fuzzy)
    this module       VERIFIES or SUPPLIES the CITATION    (deterministic)

That is what makes the 7B's weakness irrelevant: it never has to be trusted about
the one thing it cannot do, because the citation is checked by code. The GOAL
("every term must carry a checkable reference") constrains the search, so a small
model plus a verifier can beat a large model alone on THIS mission.

WHAT COUNTS AS A CITATION (and what does not)
---------------------------------------------
    path                     the file must EXIST
    path:line                the file must exist AND have that line
    path:line-line           both ends must be inside the file
    a command                must start with a known runner (python/npm/...)
    measured: <text>         an explicit measurement, allowed but flagged
    anything else            REFUSED — prose is not a reference
"""
from __future__ import annotations

import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# A command citation must start with one of these, so "I ran it" is not a
# citation but "python _proof_x.py" is.
COMMAND_PREFIXES = (
    "python ", ".\\", "./", "npm ", "node ", "git ", "sqlite3 ",
    ".venv\\", ".venv/", "powershell ", "pwsh ",
)

# A path-like token: has a separator or a known extension.
_PATH_RE = re.compile(r"^[\w./\\-]+\.(py|md|json|sql|js|ts|txt|jsonl|db|yaml|yml)"
                      r"(:\d+(-\d+)?)?$")
_LINE_RE = re.compile(r"^(?P<path>.+?):(?P<a>\d+)(?:-(?P<b>\d+))?$")

# A string that is prose ABOUT a citation rather than a citation. The 7B produced
# exactly this shape, so it is named rather than left to a length heuristic.
_REFUSAL_MARKERS = ("refuse", "cannot", "can't", "no real", "not available",
                    "hypothetical", "no concrete", "unable", "n/a", "none")


def verify_cite_ref(cite_ref: str, base_dir: Path | None = None) -> tuple[bool, str]:
    """`(ok, reason)`. The reason NAMES what is wrong, so the fix is visible.

    This is the LOGIC GENERATOR: it does not judge whether the citation is
    RELEVANT (that needs a reader), it judges whether it is CHECKABLE. A
    checkable-but-irrelevant citation is a weaker defect than an uncheckable one,
    and conflating them would make this refuse everything.
    """
    raw = str(cite_ref or "").strip()
    if not raw:
        return False, "empty cite_ref — no citation, no term"

    low = raw.lower()
    # 1. A refusal dressed as a citation. The 7B did this twice, and the register
    #    accepted it because the string was non-empty.
    for marker in _REFUSAL_MARKERS:
        if marker in low:
            return False, (
                "cite_ref %r reads as a REFUSAL, not a reference — a sentence "
                "about not having a citation is not a citation" % raw[:60])

    base = base_dir or BASE_DIR

    # 2. An explicit measurement. Allowed, but it must SAY it is a measurement,
    #    so a reader knows it is not a file to open.
    if low.startswith("measured:"):
        if len(raw) < len("measured: ") + 8:
            return False, "a 'measured:' citation must say WHAT was measured"
        return True, "an explicit measurement"

    # 3. A command.
    if any(low.startswith(p) for p in COMMAND_PREFIXES):
        return True, "a command that can be re-run"

    # 4. A path, optionally with a line or a line range.
    m = _LINE_RE.match(raw)
    path_part = m.group("path") if m else raw
    line_a = int(m.group("a")) if m else None
    line_b = int(m.group("b")) if (m and m.group("b")) else None

    if not _PATH_RE.match(raw) and not _PATH_RE.match(path_part):
        return False, (
            "cite_ref %r is neither a path, a path:line, a command, nor a "
            "'measured:' note — prose is not a reference" % raw[:60])

    # Resolve relative to the repo root, then to the cwd, so a citation written
    # from either place is accepted (a worker may cite either).
    cand = [base / path_part, Path.cwd() / path_part]
    found = next((p for p in cand if p.is_file()), None)
    if not found:
        return False, (
            "cite_ref %r names a file that DOES NOT EXIST (tried %s) — an "
            "invented path is the defect this check exists to catch"
            % (path_part, ", ".join(str(p) for p in cand)))

    if line_a is not None:
        try:
            n = sum(1 for _ in found.open("r", encoding="utf-8", errors="replace"))
        except OSError as exc:
            return False, "cannot read %s: %s" % (path_part, exc)
        if line_a < 1 or line_a > n:
            return False, (
                "cite_ref %r points at line %d but %s has only %d lines — a "
                "line that does not exist cannot be checked"
                % (raw, line_a, path_part, n))
        if line_b is not None and (line_b < line_a or line_b > n):
            return False, (
                "cite_ref %r has a line range ending at %d, outside %s (%d lines)"
                % (raw, line_b, path_part, n))
        return True, "a real file and a real line (%s has %d lines)" % (path_part, n)

    return True, "a real file (%s)" % path_part


def suggest_cite_ref(term_key: str, base_dir: Path | None = None) -> list[str]:
    """SUPPLY candidate citations for a term, by searching the repo.

    This is the other half of the division of labour: when the 7B cannot give a
    real citation, the LOGIC GENERATOR can FIND one, because the term's own name
    is a search key. A suggestion is not a citation until it is verified, so the
    caller must still run `verify_cite_ref` on it.
    """
    base = base_dir or BASE_DIR
    key = str(term_key or "").strip()
    if not key:
        return []
    # Search the parts of the term, longest first, so `vscode_conversation`
    # matches before `conversation`. A SHORT name is still searched: MEASURED
    # DEFECT — a `len(p) > 3` filter made `app` (3 chars) produce NO candidates,
    # so a real table was reported as unciteable. The filter is now `>= 3`, and a
    # short name falls back to the whole key.
    parts = [p for p in key.split("_") if len(p) >= 3] or [key]
    parts.sort(key=len, reverse=True)
    hits: list[str] = []
    exts = (".py", ".md", ".json", ".sql", ".js")
    for part in parts[:3]:
        for path in base.rglob("*" + part + "*"):
            if path.is_file() and path.suffix in exts:
                rel = path.relative_to(base)
                if str(rel) not in hits:
                    hits.append(str(rel))
            if len(hits) >= 5:
                return hits
    return hits


# Files that are too large or too noisy to grep for a name. A citation must be
# cheap to re-check, so the search is bounded.
_LOCATE_EXTS = (".py", ".sql", ".md", ".json", ".js")
_LOCATE_SKIP = ("node_modules", ".venv", "dist", ".git", "__pycache__")
_LOCATE_MAX_BYTES = 4_000_000

# The file list is CACHED on (base, mtime): `rglob` over this repo is ~0.4s, and
# a sweep locates one name at a time, so without a cache a 1000-name sweep
# spends ~7 minutes re-listing the same tree. The cache is keyed on the base
# directory's mtime so a new file invalidates it.
_LOCATE_CACHE: dict[str, tuple[float, list[Path]]] = {}


def _locate_files(base: Path) -> list[Path]:
    key = str(base)
    try:
        mtime = base.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _LOCATE_CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    files = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in _LOCATE_EXTS:
            continue
        rel = str(path.relative_to(base))
        if any(s in rel for s in _LOCATE_SKIP):
            continue
        files.append(path)
    _LOCATE_CACHE[key] = (mtime, files)
    return files


def locate_name(name: str, base_dir: Path | None = None) -> str:
    """`path:line` where `name` ACTUALLY APPEARS, or "" if it appears nowhere.

    WHY THIS EXISTS — MEASURED DEFECT (2026-09-23)
    ----------------------------------------------
    `suggest_cite_ref` searched for FILES whose NAME resembles the term. For a
    name that does not exist at all (`__no_such_name_in_the_source__`) it still
    found a file containing the word "source", and `verify_cite_ref` accepted it,
    because the file really does exist. So the sweep ACCEPTED a term for a name
    that is nowhere in the repo.

    A file whose name resembles a term is NOT evidence that the term exists. The
    citation must point at the LINE where the name is used, so the check is
    "does this name appear in the source", not "is there a file with a similar
    name". This is the difference between a citation and a coincidence.
    """
    base = base_dir or BASE_DIR
    key = str(name or "").strip()
    if not key:
        return ""
    # A name is matched as a WHOLE TOKEN, so `app` does not match `apple` and
    # `chat` does not match `chat_center_message`.
    pat = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])")
    for path in _locate_files(base):
        rel = str(path.relative_to(base))
        try:
            if path.stat().st_size > _LOCATE_MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                return "%s:%d" % (rel, i)
    return ""
