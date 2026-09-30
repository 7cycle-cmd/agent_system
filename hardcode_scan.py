# -*- coding: utf-8 -*-
"""
hardcode_scan.py — walk the repo, find literal candidates, and SCOPE each one.

Why
---
`hardcode_scope.resolve(file, line)` answers "what taxon does this live in?" for
ONE position. Nothing walked the repo, so there was no candidate list -- and a
candidate list is the input any later step (LLM proposal, three-stage gate,
`register_approve`) needs.

This module does NOT re-implement the resolution. It calls `hardcode_scope` and
adds three things:

  1. a WALK that proposes candidate lines, with a named rule per class
  2. a `cite_ref` on EVERY candidate (`path:line`), because the project's own
     `citation_discipline` refuses anything uncited -- so the output is
     checkable by construction, not by good intentions
  3. a MEASURED classification rate, reported as a number

What counts as a candidate
--------------------------
A literal worth reviewing is one that could NOT be derived from an existing
source. So the scan looks for the shapes that OFTEN mean "a value was typed in
instead of looked up".

Every rule is deliberately OVER-INCLUSIVE: this produces a REVIEW list, not a
verdict. The verdict belongs to the three-stage gate, and the scan says so
instead of pretending to be the judge.

Honesty rules
-------------
- A match inside a comment or a docstring is kept but marked `is_comment=True`
  and is NOT counted in the live total, because a comment cannot hard-code
  behaviour. (Mirrors `migrate_legacy_ids.find_references`.)
- Nothing matched is silently dropped.
- The classification rate is reported together with WHICH kind of unknown
  dominated. A rate that hides that cannot be acted on.

Read-only. It writes no table.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tokenize
from io import StringIO
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB = BASE_DIR / "agent.db"

import hardcode_scope as hs

SKIP_DIRS = frozenset({
    ".git", ".venv", "__pycache__", "node_modules", "dist", "out",
    "chrome_cdp_profile", ".vite", ".pytest_cache", "site-packages",
    "hb_snapshots", "debug_shots", "helper_watchdog_snaps", "evidence",
    "evidence_final", "evidence_steps", "fault_evidence", "qc_evidence",
    "hko_proof", "skills", "docs",
})
# Files whose literals are the SUBJECT of the scan, not a finding in it, plus
# a repo convention: a leading `_` marks a one-shot SESSION script, not product
# code. Measured reason: the first run reported 96 ABS_PATH hits and the sample
# showed nearly all of them were `sys.path.insert(0, r"C:\projects\agent_system")`
# inside `_backfill_*`, `_calib_*`, `_crop_*`, `_record_*` and `_drive_*` --
# throwaway helpers bootstrapping their own import path, which is not a
# hard-coded value that should have been derived from a source.
#
# The convention is applied as a PREFIX rule rather than a long list of observed
# names, because the list would go stale on the next helper. Files skipped by it
# are COUNTED and reported, so nothing is hidden -- and `--include-session-scripts`
# scans them anyway.
SESSION_SCRIPT_PREFIX = "_"
SKIP_FILE_NAMES = frozenset({
    "hardcode_scan.py", "hardcode_scope.py", "taxonomy_backfill.py",
    "entity_id.py", "entity_registry.py", "register_approval.py",
    "migrate_legacy_ids.py", "conftest.py",
})

# A line that only bootstraps an import path is not a finding. Measured: this is
# what most ABS_PATH matches actually were.
_BOOTSTRAP = re.compile(r"sys\.path\.(?:insert|append)\s*\(")
# A literal inside a printed/logged string is a message, not a value the code
# branches on. `print("... y=915 ...")` was matched by MAGIC_PX before this.
_MESSAGE_CALL = re.compile(r"\b(?:print|log|logger\.\w+|vlog|eprint)\s*\(")

# name, regex, why it is suspicious
_RULES: tuple[tuple[str, str, str], ...] = (
    ("SCREEN_RES",
     r"\b(?:screen_w|screen_h|WIDTH|HEIGHT|max_width|maxWidth)\s*[:=]\s*\d{3,4}\b",
     "a screen dimension typed inline"),
    ("SCREEN_PAIR",
     r"\b(?:1600|1920|2560|3840|1440|1200|1080|900)\b",
     "a common screen dimension literal"),
    ("MAGIC_PX",
     r"\b(?:pad|margin|offset|radius|threshold|gap|x|y)\s*[:=]\s*\d{1,4}\b",
     "a pixel-ish constant"),
    ("ABS_PATH",
     r"['\"][A-Za-z]:[\\/]{1,2}[^'\"]{4,}['\"]",
     "an absolute path"),
    ("URL_HOST",
     r"['\"](?:https?://)?(?:127\.0\.0\.1|localhost)(?::\d{2,5})?[^'\"]*['\"]",
     "a local endpoint typed inline"),
    ("ID_LITERAL",
     r"\b(?:register_id|entity_ref_id|capability_id|module_id|db_table_id|"
     r"function_id|api_id|skill_id|table_id|field_id)\s*[:=]\s*\d{1,6}\b",
     "a foreign-key id typed inline"),
    # MEASURED GAP, and it is why this rule exists (2026-09-23). The user:
    #     "so system will nnot have this problem again not hardcode!!!!!"
    # A model name was hard-coded in 8 files and 13 places, and THIS SCANNER DID
    # NOT SEE ONE OF THEM — its six rules covered screen sizes, pixels, paths,
    # URLs and foreign keys, but not a model.
    #
    # The shape is a QUOTED model identifier containing a size tag (`7b`, `27b`,
    # `13b`, `70b`) or a vendor prefix. That is deliberately narrow: a bare word
    # like "qwen" in prose, or a model named in a docstring, is not a value the
    # code branches on -- and `comment_lines()` excludes those anyway.
    ("MODEL_LITERAL",
     r"['\"](?:[a-z0-9_.\-]*/)?(?:qwen|deepseek|gpt|claude|llama|mistral|gemma|"
     r"phi|yi|glm)[\w.\-]*"
     r"(?::|\-)(?:\d+b|\d+\.\d+)[\w.\-]*['\"]",
     "a MODEL NAME typed inline instead of resolved from the service route"),
)

# A PRECISE level can hold a value, so it can answer "was this derivable?".
# An AGGREGATE level (module > capability > channel) can only tell you who would
# have to discuss it, not whether the literal was avoidable. They are reported
# separately so the coverage number cannot be inflated by resolutions that
# decide nothing. Measured: the first filtered run had 71 "classifiable" of 205,
# but 67 of those were module-only -- i.e. the precise figure was 4.
PRECISE_LEVELS = ("db_field", "db_table", "function", "api")
AGGREGATE_LEVELS = ("module", "capability", "channel")


def log(msg: str) -> None:
    print("[hardcode_scan] %s" % msg, flush=True)


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# comments / docstrings, by grammar not by guessing
# ---------------------------------------------------------------------------


def comment_lines(path: Path) -> set[int]:
    """Line numbers that are comment-only, plus docstring spans.

    `tokenize` finds real comments (a `#` inside a string is not one) and `ast`
    identifies docstrings by grammar. Both are used, because a quote-counting
    heuristic would misclassify exactly the lines that matter.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return set()
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.add(tok.start[0])
    except Exception:
        pass
    try:
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef,
                                     ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for stmt in getattr(node, "body", None) or []:
                if isinstance(stmt, ast.Expr) and \
                        isinstance(stmt.value, ast.Constant) and \
                        isinstance(stmt.value.value, str):
                    lo = stmt.value.lineno
                    hi = getattr(stmt.value, "end_lineno", lo) or lo
                    out.update(range(lo, hi + 1))
    except Exception:
        pass
    return out


def _skip_file(p: Path, *, include_session: bool = False) -> bool:
    if p.name in SKIP_FILE_NAMES:
        return True
    if p.name.startswith(SESSION_SCRIPT_PREFIX) and not include_session:
        return True
    return False


def iter_py(root: Path, *, include_session: bool = False) -> tuple[list[Path], list[Path]]:
    """Return (files_to_scan, files_skipped_as_session_scripts).

    The skipped list is RETURNED, not discarded, so the report can say how many
    files the convention removed. A filter whose effect is invisible is how a
    scan quietly becomes "the part of the repo I happened to look at".
    """
    scan: list[Path] = []
    skipped: list[Path] = []
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.name in SKIP_FILE_NAMES:
            continue
        if p.name.startswith(SESSION_SCRIPT_PREFIX) and not include_session:
            skipped.append(p)
            continue
        scan.append(p)
    return scan, skipped


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def scan_file(path: Path, root: Path, *, conn: sqlite3.Connection,
              resolve: bool = True) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    try:
        rel = path.relative_to(root).as_posix()
    except Exception:
        rel = path.as_posix()
    comments = comment_lines(path)
    out: list[dict[str, Any]] = []
    for i, line in enumerate(lines, 1):
        for name, pat, why in _RULES:
            if not re.search(pat, line):
                continue
            is_comment = i in comments
            # A bootstrap guard is not a finding, regardless of the rule that hit.
            if _BOOTSTRAP.search(line):
                out.append({"rule": name, "why": why, "file": rel, "line": i,
                            "cite_ref": "%s:%d" % (rel, i),
                            "text": line.strip()[:120], "is_comment": is_comment,
                            "is_message": False, "excluded": "import-path bootstrap"})
                continue
            # A literal inside a message call is text a human reads, not a value
            # the code branches on. Kept and labelled, never silently dropped.
            if _MESSAGE_CALL.search(line):
                out.append({"rule": name, "why": why, "file": rel, "line": i,
                            "cite_ref": "%s:%d" % (rel, i),
                            "text": line.strip()[:120], "is_comment": is_comment,
                            "is_message": True, "excluded": None})
                continue
            cand: dict[str, Any] = {
                "rule": name, "why": why, "file": rel, "line": i,
                "cite_ref": "%s:%d" % (rel, i),
                "text": line.strip()[:120],
                "is_comment": is_comment, "is_message": False, "excluded": None,
            }
            if resolve and not is_comment:
                r = hs.resolve(rel, i, conn=conn)
                cand["scope_level"] = r.get("scope_level")
                cand["scope_ref"] = r.get("scope_ref")
                cand["source"] = r.get("source")
                cand["confidence"] = r.get("confidence")
                cand["unknown_kind"] = r.get("unknown_kind")
            out.append(cand)
    return out


def scan(root: Path | None = None, *, db_path: Path | str | None = None,
         resolve: bool = True, limit_files: int | None = None,
         include_session: bool = False) -> dict[str, Any]:
    root = Path(root or BASE_DIR)
    conn = _connect(db_path)
    try:
        files, skipped = iter_py(root, include_session=include_session)
        if limit_files:
            files = files[:int(limit_files)]
        all_c: list[dict] = []
        for p in files:
            all_c.extend(scan_file(p, root, conn=conn, resolve=resolve))

        comments = [c for c in all_c if c.get("is_comment")]
        messages = [c for c in all_c if c.get("is_message")]
        boot = [c for c in all_c if c.get("excluded")]
        live = [c for c in all_c
                if not c.get("is_comment") and not c.get("is_message")
                and not c.get("excluded")]

        by_rule: dict[str, int] = {}
        by_level: dict[str, int] = {}
        by_unknown: dict[str, int] = {}
        by_file: dict[str, int] = {}
        for c in live:
            by_rule[c["rule"]] = by_rule.get(c["rule"], 0) + 1
            by_file[c["file"]] = by_file.get(c["file"], 0) + 1
            if resolve:
                lvl = c.get("scope_level") or "?"
                by_level[lvl] = by_level.get(lvl, 0) + 1
                if c.get("unknown_kind"):
                    by_unknown[c["unknown_kind"]] = \
                        by_unknown.get(c["unknown_kind"], 0) + 1

        resolved = sum(v for k, v in by_level.items()
                       if not k.startswith("UNKNOWN")) if resolve else 0
        # "classifiable" alone is a misleading number, and the first measured run
        # proved it: 71 of 205 were classifiable, but 67 of those resolved only to
        # `module` -- an AGGREGATE. At module level the question "was this value
        # derivable?" cannot be answered, because module is not a value source.
        # So the report splits PRECISE (a level that can hold a value) from
        # AGGREGATE (a level that can only hold a forum). Lumping them made a
        # weak result look like a third of the repo was covered.
        precise = sum(v for k, v in by_level.items()
                      if k in PRECISE_LEVELS) if resolve else 0
        aggregate = resolved - precise
        total = len(live)
        return {
            "ok": True,
            "root": str(root),
            "files_scanned": len(files),
            "files_skipped_session_scripts": len(skipped),
            "session_script_prefix": SESSION_SCRIPT_PREFIX,
            "include_session": include_session,
            "candidates_total": len(all_c),
            "candidates_live": total,
            "candidates_comment": len(comments),
            "candidates_message": len(messages),
            "candidates_bootstrap": len(boot),
            "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
            "by_level": dict(sorted(by_level.items(), key=lambda kv: -kv[1])),
            "by_unknown_kind": by_unknown,
            "top_files": dict(sorted(by_file.items(),
                                     key=lambda kv: -kv[1])[:15]),
            "classifiable": resolved,
            "classifiable_rate": (round(resolved / total, 4) if total else None),
            "classifiable_precise": precise,
            "classifiable_precise_rate": (round(precise / total, 4)
                                          if total else None),
            "classifiable_aggregate_only": aggregate,
            "precise_levels": sorted(PRECISE_LEVELS),
            "candidates": all_c,
            "note": "a candidate is for REVIEW, not a verdict; the verdict "
                    "belongs to the three-stage gate",
        }
    finally:
        conn.close()


def explain(res: dict) -> str:
    out = ["hardcode scan: %d live candidate(s) in %d file(s)"
           % (res["candidates_live"], res["files_scanned"])]
    out.append("  files skipped as session scripts (leading %r): %d"
               % (res["session_script_prefix"],
                  res["files_skipped_session_scripts"]))
    out.append("  excluded, but COUNTED (nothing is hidden):")
    out.append("    comment / docstring : %d" % res["candidates_comment"])
    out.append("    message text        : %d" % res["candidates_message"])
    out.append("    import-path bootstrap: %d" % res["candidates_bootstrap"])
    out.append("  by rule:")
    for k, v in res["by_rule"].items():
        out.append("    %-11s %d" % (k, v))
    out.append("  by resolved scope level:")
    for k, v in res["by_level"].items():
        out.append("    %-24s %d" % (k, v))
    if res["by_unknown_kind"]:
        out.append("  unknown kinds (WHICH unknown matters):")
        for k, v in res["by_unknown_kind"].items():
            out.append("    %-24s %d" % (k, v))
    out.append("  classifiable (any level): %d / %d  (rate=%s)"
               % (res["classifiable"], res["candidates_live"],
                  res["classifiable_rate"]))
    out.append("  classifiable at a PRECISE level (can decide necessity): "
               "%d / %d  (rate=%s)"
               % (res["classifiable_precise"], res["candidates_live"],
                  res["classifiable_precise_rate"]))
    out.append("  aggregate-only (a forum, not a verdict): %d"
               % res["classifiable_aggregate_only"])
    out.append("  busiest files:")
    for k, v in res["top_files"].items():
        out.append("    %-52s %d" % (k, v))
    return "\n".join(out)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-resolve", action="store_true",
                    help="skip scope resolution (faster, counts only)")
    ap.add_argument("--limit-files", type=int, default=None)
    ap.add_argument("--show", type=int, default=0,
                    help="print the first N live candidates")
    ap.add_argument("--include-session-scripts", action="store_true",
                    help="do not apply the leading-underscore convention")
    args = ap.parse_args()

    res = scan(resolve=not args.no_resolve, limit_files=args.limit_files,
               include_session=args.include_session_scripts)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
    else:
        print(explain(res))
        if args.show:
            print("\nfirst %d live candidates:" % args.show)
            shown = 0
            for c in res["candidates"]:
                if c.get("is_comment") or c.get("is_message") or c.get("excluded"):
                    continue
                print("  %-11s %-34s level=%-22s %s"
                      % (c["rule"], c["cite_ref"], c.get("scope_level") or "-",
                         c["text"][:56]))
                shown += 1
                if shown >= args.show:
                    break


if __name__ == "__main__":
    main()