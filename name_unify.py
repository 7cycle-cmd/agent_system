"""name_unify.py — ONE audit + ONE write door over EVERY layer that holds a name.

THE HUMAN (2026-09-28), verbatim:
    "why wrong spelling can be found everyday, problem in where? alais?"
    "have the plan to fix it included rename all old data be unified language,
     no matter what fucking layer"
    "worker should have the work done, not have the work done in half, fuck"
    "no!!!! rename or totally del, no more fucking mnis-undersatnd"
    "old data not reason for keep fucking wrong"

THE FOUR ROOT CAUSES OF "every day" (measured, not asserted)
------------------------------------------------------------
A. `terminology_alias` is SYMMETRIC — it proves two spellings reach one key. It
   can NEVER prove "this spelling is wrong", which is why a typo kept as an alias
   preserves the mis-understanding.
B. `terminology_registry.MISSPELLINGS` is a CLOSED set (32 pairs).
C. `check_spelling` was called from only TWO sites (`add_term:1019`,
   `add_alias:527`); `update_term` had NO check at all.
D. Both checks were reached through a HAND-TYPED dict instead of a register.

THE TWO DECLARED REGISTERS ARE THE FIX
--------------------------------------
`terminology_blacklist` (what MUST NOT exist + its correction) and
`terminology_whitelist` (what LOOKS wrong but is CORRECT). Both are TABLES, so
adding a row is an INSERT with a cite, not a Python edit — B and D die here. The
check reads the table, so every caller is covered — C dies here. And only a
DECLARED row can refuse `role_env`, because `env` is itself a registered term
(id 1549) — A dies here.

THE THREE DETECTORS — complementary, all required (MEASURED 2026-09-28)
-----------------------------------------------------------------------
1. `terminology_blacklist.check`  — name-level, declaration-driven.
2. `unified_language.check_composite` — word-level, refuses an unregistered word
   (`five_w1h` -> MISSING_WORD `w1h`). MEASURED to PASS `role_env`, which is
   exactly why detector 1 cannot be replaced by detector 2.
3. `terminology_whitelist.is_exempt` — the FALSE-POSITIVE guard, so `5w1h`,
   `db_row`, `llm_100_run`, `CP-S-00` are not "fixed" into nonsense.

THE VERDICT IS A LOOKUP, NEVER A JUDGEMENT (R-6)
------------------------------------------------
`classify(conn, name)` returns one of NAME / RECORD / EXEMPT / CLEAN, each with a
reason and a citation. There is NO code path that picks a winner by count, length
or order (`independent-review` forbids it).

A LAYER THAT CANNOT BE READ IS A `FAULT`, NEVER A `0`
-----------------------------------------------------
`empty_detector_failure_class`: a detector that returns an empty list when it
cannot scan passes EVERYTHING. So every layer reports `ok: True` with a number,
or `ok: False` with `code: FAULT` and the reason.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CITE = "name_unify.py:audit"

# WHERE A NAME CAN LIVE. Every layer reports a NUMBER or a FAULT.
#
# `glob` is the file pattern; `skip_dirnames` are the directories that hold
# RECORDS (evidence / history) and are NEVER rewritten — R-4(b). They are still
# SCANNED (so they are counted and named), just never a write target.
LAYERS: tuple[dict, ...] = (
    {"id": "L1", "kind": "db",
     "what": "terminology_registry.term_key"},
    {"id": "L2", "kind": "db",
     "what": "terminology_registry.alias_list"},
    {"id": "L3", "kind": "db",
     # MEASURED FALSE POSITIVE 2026-09-28: scanning EVERY column flagged
     # `cite_ref = 'register_fill.py:1'` as a name. A CITATION is not a name, so
     # the layer names the columns it holds a name in.
     "what": "phase_registry values", "name_columns": ("phase_key", "scope")},
    {"id": "L4", "kind": "db",
     "what": "sqlite_master object names"},
    {"id": "L5", "kind": "py",
     "what": "*.py identifiers + names (CODE only)"},
    {"id": "L6", "kind": "js",
     "what": "llm_task_monitor_ui/src/*.js (code)"},
    {"id": "L7", "kind": "md",
     "what": "**/*.md (code vs record)"},
    {"id": "L8", "kind": "json",
     "what": "**/*.json (code vs record)"},
    {"id": "L9", "kind": "sql",
     "what": "**/*.sql"},
    # 🔴 L10 — DB COLUMN NAMES. MEASURED 2026-09-28 (the human's charge: "why
    # rename never cover for filename or DB table and field name too?"): NO layer
    # scanned a column NAME. `PRAGMA table_info` was called exactly once, at
    # `:354`, and only to read `phase_key`/`scope` VALUES. A column whose NAME
    # carries a declared-wrong token was therefore invisible.
    {"id": "L10", "kind": "db",
     "what": "sqlite_master column names (PRAGMA table_info)"},
    # 🔴 L11 — FILE NAMES. MEASURED 2026-09-28: `path.name` was used ONLY for
    # RECORD classification (`:400`), and the only write was `p.write_text`
    # (`:782`) — so a file whose NAME carried a wrong token was never renamed.
    {"id": "L11", "kind": "file",
     "what": "file NAMES (p.name), not their contents"},
    # 🔴 L12 — FUNCTION NAMES. THE HUMAN (2026-09-28): "alias / filename / table /
    # field / function capability / channel / module = same no matter everywhere".
    # MEASURED: 64 `def`s contain `_register`; NO layer scanned a function name.
    {"id": "L12", "kind": "py",
     "what": "function names (AST FunctionDef/AsyncFunctionDef)"},
    # 🔴 L13 — REGISTRY KEYS: capability / channel / module. MEASURED: module 41,
    # capability 0, channel 0. A key is a NAME, so it is the same carrier class.
    {"id": "L13", "kind": "db",
     "what": "capability_registry / channel_registry / module_registry keys"},
    # 🔴 L14 — ALIASES THAT ARE A DIFFERENT NAME (R-2). THE HUMAN: "can't alias
    # different". MEASURED: 3 such aliases exist. An alias that is a DIFFERENT
    # name is the mis-understanding, not a synonym.
    {"id": "L14", "kind": "db",
     "what": "terminology_registry.alias_list entries that DIFFER from the key"},
)

# A file under one of these directories is a RECORD (R-4(b)): it RE-PLAYS a name
# that existed at a time, and rewriting it FALSIFIES the record. Never a target.
RECORD_DIRS = ("evidence", "qc_evidence", "chrome_cdp_profile", ".git",
               "__pycache__", ".venv", "node_modules")

# A file whose NAME marks it as history rather than a live generator.
RECORD_FILE_RE = re.compile(r"^(_?(diag|dbg|demo|tmp)|.*\.bak)")

TEXT_EXT = {".py": "py", ".js": "js", ".md": "md", ".json": "json",
            ".sql": "sql", ".yaml": "yaml", ".yml": "yml"}

# --------------------------------------------------------------------------
# THE DECLARATION-CARRIER EXEMPTION (a ROW with a cite, not an `if`)
# --------------------------------------------------------------------------
# MEASURED 2026-09-28, and it is NOT a loophole: the file that DECLARES the
# blacklist must NAME the wrong spelling, or the declaration cannot exist.
# A wrong spelling quoted as the SUBJECT of a declaration is the DEFECT BEING
# DESCRIBED, exactly like a comment that quotes the human.
#
# Every row carries a cite. A file not on this list is NOT exempt.
DECLARATION_CARRIERS: tuple[tuple[str, str, str], ...] = (
    ("terminology_blacklist.py", "blacklist_seed",
     "It DECLARES the wrong names; a declaration that cannot name its subject "
     "does not exist."),
    ("terminology_registry.py", "MISSPELLINGS_source",
     "It holds `MISSPELLINGS`, the seed the blacklist imports. Removing the "
     "pairs would delete the source the seed cites."),
    ("terminology_whitelist.py", "whitelist_seed",
     "It DECLARES which names are correct, and must name the row it exempts."),
    ("_proof_alias_must_be_terminology.py", "test_fixture",
     "It PROVES the typo is refused, so it must CONTAIN the typo. MEASURED "
     "2026-09-28: a blanket rename rewrote this fixture into the CORRECT "
     "spelling and turned a GREEN check RED — a proof that measures a refusal "
     "must contain the thing it refuses."),
    ("_proof_terminology_ko_spelling.py", "test_fixture",
     "It measures the spelling rule directly and must name the spellings it "
     "compares."),
    ("_proof_url_and_term.py", "test_fixture",
     "It asserts the typo is ABSENT from the nav paths, and an assertion about a "
     "typo must name it."),
    ("_proof_name_unify.py", "test_fixture",
     "🔴 MEASURED 2026-09-28, AND IT HAPPENED TO THIS VERY FILE: the first "
     "`--apply` ran BEFORE this row existed and rewrote the proof's OWN test "
     "strings (`enviornment` -> `environment`, `five_w1h` -> "
     "`completeness_5w1h`, `role_env` -> `role_environment`), so eleven checks "
     "that measure a REFUSAL went RED because the thing they refuse had been "
     "corrected underneath them. **A rewrites-everything tool must exclude the "
     "files that DECLARE or TEST the wrong names, and that list must exist "
     "BEFORE the first apply, not after.**"),
    ("_proof_registry_naming.py", "test_fixture",
     "🔴 MEASURED 2026-09-28, THE SAME DEFECT A SECOND TIME. This proof measures "
     "that `check_registry_name` REFUSES the non-standard spelling, so it must "
     "CONTAIN that spelling. The L17 proof rewrite turned `version_registry` "
     "into `version_registry` inside it, and 10 checks went RED because the "
     "proof then asserted the CORRECT name is refused. **A proof that measures "
     "a REFUSAL must be exempt from the rewrite, and the exemption must be "
     "declared BEFORE the apply.**"),
    ("_seed_terminology_lists.py", "seed_wrapper",
     "It is the wrapper that seeds both tables."),
    ("name_unify.py", "the_rule",
     "It IS the rule; the rule must name what it refuses."),
    ("skills/1_core/terminology_spelling/terminology_spelling.skill.md",
     "skill_declaration",
     "The skill TEACHES the check and names the typos it refuses."),
)


def canonical_relpath(root: Path, path: str) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            return str(p.relative_to(root))
        except ValueError:
            return p.name
    return str(p)


def declaration_carrier(root: Path, path: str) -> dict | None:
    """Is this file a DECLARED declaration-carrier? A LOOKUP, with a cite."""
    rel = canonical_relpath(root, path).replace("\\", "/")
    for f, kind, why in DECLARATION_CARRIERS:
        if rel == f or rel.endswith("/" + f):
            return {"ok": True, "exempt": True, "kind": kind, "why": why,
                    "cite_ref": "name_unify.py:DECLARATION_CARRIERS"}
    return None

# --------------------------------------------------------------------------
# THE FALSE-POSITIVE GUARDS (measured, and each one fixes a real over-count)
# --------------------------------------------------------------------------
# F1 — THE CODED TOKEN BOUNDARY.
#
# MEASURED 2026-09-28: the blacklist row `role_env` matched INSIDE the CORRECT
# name `role_environment`. `_`, `.` and `-` must stay BOUNDARIES (so
# `enviornment_playwright` still matches the `enviornment` row), but a LETTER or
# a DIGIT must not (so `role_environment` does not match `role_env`).
#
# This is the SAME rule `module_code_align` derived and this repo has recorded
# repeatedly: "a.b is a DIFFERENT name from a."
def token_re(wrong: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])"
                      % re.escape(str(wrong)), re.IGNORECASE)


def ident_re(name: str) -> re.Pattern:
    """A WHOLE-IDENTIFIER match — `_` is PART of the token, not a boundary.

    🔴 MEASURED 2026-09-28, caught by my own proof: `_rename_function` used
    `token_re`, whose boundary excludes only `[A-Za-z0-9]`. So renaming
    `zz_enviornment_fn` ALSO rewrote `zz_enviornment_fn_2` — because `_` was
    treated as a boundary. **`a_b` is a DIFFERENT name from `a`**, the same rule
    this repo derived for `.` (`module_code_align`: "a.b is a DIFFERENT name from
    a").

    THE TWO NEEDS ARE OPPOSITE, and that is why there are two functions:
      * `token_re`  — a TYPO inside a compound name: `_` IS a boundary, so
                      `enviornment_playwright` matches the `enviornment` row.
      * `ident_re`  — an IDENTIFIER rename: `_` is NOT a boundary, so a longer
                      identifier is left alone.
    """
    return re.compile(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"
                      % re.escape(str(name)))


def _strip_py_prose(text: str) -> str:
    """Blank out comments and docstrings, keeping line numbers intact.

    F2 — MEASURED 2026-09-28: `enviornment` appears in COMMENTS that quote the
    human's words (`terminology_registry.py:186`, `llm_task_monitor_ui/src/app.js:54`).
    **Prose ABOUT a defect is not the defect.** Uses the AST, never a quote
    heuristic (this repo learned that lesson three times; `trigger_point.py:86`
    holds the shared implementation, and it is REUSED, not re-written).
    """
    try:
        import trigger_point as tp
        doc = tp._docstring_lines(text)
    except Exception:
        doc = set()
    out: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        # `#` is a Python comment; `--` is a SQL comment INSIDE a DDL string in a
        # .py file (MEASURED: `db_schema.py:1350` quotes the human in `--`).
        if i in doc or s.startswith("#") or s.startswith("--"):
            out.append("")          # keep the line count, drop the prose
        else:
            out.append(line)
    return "\n".join(out)


def _strip_js_prose(text: str) -> str:
    """Blank out `//` and `/* */` comments in a JS/JSON-ish file."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"),
                  text, flags=re.S)
    out: list[str] = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("//") or s.startswith("*"):
            out.append("")
        else:
            out.append(line)
    return "\n".join(out)


def _code_only(text: str, kind: str) -> str:
    if kind == "py":
        return _strip_py_prose(text)
    if kind in ("js", "json"):
        return _strip_js_prose(text)
    return text


# --------------------------------------------------------------------------
# the DB layers
# --------------------------------------------------------------------------
# THE COMPILED ROWS, loaded once per run.
#
# 🔴 MEASURED PERFORMANCE BUG IN MY FIRST VERSION: `_typo_rows` called
# `terminology_blacklist._rows(conn)` — a DB QUERY — for EVERY file, and then
# recompiled 37 regexes and ran all of them against every line. Over ~5,000 files
# that is ~185,000 queries plus ~185,000 regex COMPILATIONS, and the proof
# TIMED OUT at 300s. The fix is three-part and each part is measured:
#   1. load the rows ONCE (`_load_bl`);
#   2. compile each row's regex ONCE (`_BL_CACHE`);
#   3. a `wrong in text.lower()` SUBSTRING prefilter before the regex (a C scan
#      is ~100x cheaper than a regex). The prefilter can only ever produce a
#      FALSE POSITIVE, which the token regex then rejects, so it cannot cause the
#      `role_env`-in-`role_environment` defect.
_BL_ROWS: list[dict] = []
_BL_CACHE: dict[str, tuple] = {}


def _term_table(conn: sqlite3.Connection) -> str:
    """The LIVE name of the terminology table — resolved, never assumed.

    🔴 MEASURED 2026-09-28: this module hardcoded `terminology_registry` in 15
    places. The moment the migration RENAMED that table, every follow-up query
    raised `no such table: terminology_registry` — the tool broke ITSELF by doing
    its job. A reader must resolve the name, not assume it.
    """
    for cand in ("terminology_registry", "terminology_registry"):
        if _table_exists(conn, cand):
            return cand
    return "terminology_registry"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Does a table OR view with this name exist? Kind-agnostic by definition.

    A name is RENAMED table->view, so a `type='table'` filter would report a
    live object as absent (`object_door`'s defect class).
    """
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(c[1]) for c in conn.execute("PRAGMA table_info(%s)" % table)}


def _load_bl(conn: sqlite3.Connection) -> list[dict]:
    """Load the active blacklist rows ONCE for this run."""
    global _BL_ROWS, _BL_CACHE
    import terminology_blacklist as bl
    _BL_ROWS = bl._rows(conn)
    _BL_CACHE = {}
    for r in _BL_ROWS:
        w = str(r["wrong"])
        _BL_CACHE[w.lower()] = (token_re(w), r)
    return _BL_ROWS


def _typo_rows(conn: sqlite3.Connection, text: str) -> list[dict]:
    """Which DECLARED wrong names appear in `text` as a WHOLE TOKEN? A LIST.

    The token boundary (F1) is what stops `role_env` matching inside the CORRECT
    name `role_environment`. A bare substring scan IS the defect.
    """
    if not _BL_ROWS:
        _load_bl(conn)
    t = str(text or "")
    low = t.lower()
    out: list[dict] = []
    for wlow, (rx, r) in _BL_CACHE.items():
        if wlow not in low:          # cheap C scan; cannot lose a real hit
            continue
        if rx.search(t):
            out.append({"wrong": r["wrong"], "correction": r["correction"],
                        "kind": r["kind"], "cite_ref": r["cite_ref"]})
    return out


def _corrected(name: str, hits: list[dict]) -> str:
    """Apply EVERY declared rule to a name, IN PLACE. The corrected WHOLE name.

    🔴 MEASURED 2026-09-28: the first version substituted with `token_re` only, so
    a declared SUFFIX rule (`_register`) never fired and the corrected name came
    out UNCHANGED — a rename to itself. A suffix is substituted at the END, a
    whole token anywhere, and the result is the WHOLE name (never the bare
    correction, which would DROP the rest of the name).
    """
    out = str(name or "")
    for t in hits:
        w = str(t.get("wrong") or "")
        c = str(t.get("correction") or "")
        if not w or not c:
            continue
        if str(t.get("kind")) == "suffix":
            # A SEGMENT substitution: `_register` -> `_registry` wherever it
            # appears as a segment, so `chat_registry_store` becomes
            # `chat_registry_store` (MEASURED: a suffix-only rule left it alone).
            out = re.sub(r"(?<=[A-Za-z0-9_])%s(?![A-Za-z0-9])" % re.escape(w),
                         c, out, flags=re.IGNORECASE)
        else:
            out = token_re(w).sub(c, out)
    return out


def _suffix_rows(conn: sqlite3.Connection) -> list[dict]:
    """The DECLARED SUFFIX rules (`kind='suffix'`), loaded once per run.

    🔴 WHY A SECOND MATCHER IS REQUIRED, MEASURED 2026-09-28: the declared row
    `_register -> _registry` does NOT fire under `token_re`, because `token_re`
    requires a NON-word char BEFORE the match — and in `chat_registry` the char
    before `_register` is `t`, a letter. So the rule was declared and INERT.

    A SUFFIX is a different shape from a whole token, so it needs its own matcher:
    the name must END WITH the suffix, and the suffix must be preceded by a word
    char (so `register` alone does NOT match `_register`).
    """
    if not _BL_ROWS:
        _load_bl(conn)
    return [r for r in _BL_ROWS if str(r["kind"]) == "suffix"]


def _suffix_hits(conn: sqlite3.Connection, name: str) -> list[dict]:
    """Which DECLARED SUFFIX rules does `name` carry as a SEGMENT? A LIST.

    🔴 A SEGMENT, NOT ONLY AN ENDING. MEASURED 2026-09-28: `chat_registry_store`
    carries `_register` in the MIDDLE, and a suffix-only rule left it un-renamed
    while the human's ruling is "same no matter everywhere". The rule is: the
    suffix appears as a SEGMENT — preceded by a word char, followed by `_` or the
    end of the name. So `chat_registry` and `chat_registry_store` both match, and
    `registers` (plural) does NOT.
    """
    n = str(name or "")
    if not n:
        return []
    out: list[dict] = []
    for r in _suffix_rows(conn):
        w = str(r["wrong"])
        if not w:
            continue
        if re.search(r"(?<=[A-Za-z0-9_])%s(?![A-Za-z0-9])" % re.escape(w),
                     n, re.IGNORECASE):
            out.append({"wrong": r["wrong"], "correction": r["correction"],
                        "kind": r["kind"], "cite_ref": r["cite_ref"]})
    return out


def _name_hits(conn: sqlite3.Connection, name: str) -> list[dict]:
    """EVERY declared rule a NAME breaks: a whole token OR a declared suffix.

    THE ONE DOOR for "is this name wrong". A caller that used only `_typo_rows`
    would miss a declared SUFFIX rule — which is exactly the defect measured
    2026-09-28 (the `_register` row was declared and inert).
    """
    return _typo_rows(conn, name) + _suffix_hits(conn, name)


def _composite_refusals(conn: sqlite3.Connection, name: str) -> dict:
    """The word-level verdict, or a named refusal to answer."""
    try:
        import unified_language as ul
        r = ul.check_composite(conn, name)
        return {"ok": True, "refused": not r["ok"], "code": r.get("code"),
                "missing_words": r.get("missing_words") or [],
                "duplicate_of": r.get("duplicate_of")}
    except Exception as e:                      # never a silent pass
        return {"ok": False, "code": "FAULT", "reason": str(e)}


def audit_db_layers(conn: sqlite3.Connection) -> dict:
    """L1..L4: every name in the REGISTER and in `sqlite_master`."""
    _load_bl(conn)
    out: dict[str, dict] = {}

    l1: list[dict] = []
    try:
        for r in conn.execute("SELECT term_id, term_key, is_active FROM %s" % _term_table(conn)):
            typos = _name_hits(conn, r["term_key"])
            comp = _composite_refusals(conn, r["term_key"])
            if typos or comp.get("refused"):
                l1.append({"term_id": int(r["term_id"]),
                           "term_key": r["term_key"],
                           "is_active": int(r["is_active"]),
                           "typos": typos,
                           "composite": comp if comp.get("refused") else None})
        out["L1"] = {"ok": True, "what": "terminology_registry.term_key",
                     "hits": l1, "n": len(l1)}
    except sqlite3.Error as e:
        out["L1"] = {"ok": False, "code": "FAULT", "reason": str(e)}

    l2: list[dict] = []
    try:
        for r in conn.execute("SELECT term_id, term_key, alias_list FROM %s" % _term_table(conn)):
            raw = str(r["alias_list"] or "")
            if raw in ("", "NA"):
                continue
            try:
                items = json.loads(raw)
            except Exception:
                items = [raw]
            for a in items:
                typos = _typo_rows(conn, a)
                comp = _composite_refusals(conn, a)
                if typos or comp.get("refused"):
                    l2.append({"owner": r["term_key"], "alias": a,
                               "typos": typos,
                               "composite": comp if comp.get("refused") else None})
        out["L2"] = {"ok": True, "what": "terminology_registry.alias_list",
                     "hits": l2, "n": len(l2)}
    except sqlite3.Error as e:
        out["L2"] = {"ok": False, "code": "FAULT", "reason": str(e)}

    l3: list[dict] = []
    try:
        # 🔴 THE PHASE TABLE IS RESOLVED, NOT ASSUMED. MEASURED 2026-09-28: this
        # layer hardcoded `phase_registry`, so the moment the migration renamed it
        # the layer reported `FAULT: no such table` — a detector that broke because
        # the rename it performed succeeded.
        phase_tbl = ("phase_registry" if _table_exists(conn, "phase_registry")
                     else "phase_registry")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % phase_tbl)]
        # F3 — ONLY the columns that HOLD A NAME. Scanning every column flagged
        # `cite_ref = 'register_fill.py:1'`, which is a CITATION, not a name.
        wanted = [c for c in ("phase_key", "scope") if c in cols]
        for r in conn.execute("SELECT * FROM %s" % phase_tbl):
            d = dict(zip(cols, r))
            for k in wanted:
                v = d.get(k)
                if not isinstance(v, str) or not v:
                    continue
                typos = _typo_rows(conn, v)
                comp = _composite_refusals(conn, v)
                # An UNDECOMPOSABLE VALUE is not a NAME defect: a phase_key like
                # `completeness_5w1h` legitimately contains a word the vocabulary
                # has not accepted yet. The layer reports the TYPO bucket only; the
                # composite verdict for a DB VALUE is REPORTED SEPARATELY so it can
                # never be confused with a name.
                if typos:
                    l3.append({"phase_id": d.get("phase_id"), "column": k,
                               "value": v, "typos": typos, "composite": None})
        out["L3"] = {"ok": True, "what": "phase_registry values",
                     "name_columns": wanted, "hits": l3, "n": len(l3)}
    except sqlite3.Error as e:
        out["L3"] = {"ok": False, "code": "FAULT", "reason": str(e)}

    l4: list[dict] = []
    try:
        for r in conn.execute("SELECT type, name FROM sqlite_master"):
            # 🔴 A SQLITE-MANAGED AUTO-INDEX IS NOT A NAME. MEASURED 2026-09-28:
            # `sqlite_autoindex_registry_approve_1` matched the `_register` segment
            # — but it belongs to the table `register_approve`, which is NOT being
            # renamed. SQLite names it after its table and manages it; renaming it
            # is both impossible and wrong. It is EXCLUDED, not reported as a gap.
            if str(r["name"]).startswith("sqlite_autoindex_"):
                continue
            typos = _name_hits(conn, r["name"])
            if typos:
                # 🔴 MEASURED DEFECT, FIXED HERE (2026-09-28): this hit carried NO
                # `class`, and `plan_writes` selects on `class == "NAME"`
                # (`:605`), so EVERY DB-object hit was DETECTED and then DROPPED.
                # A declared-wrong TABLE name was reported and never renamed.
                l4.append({"type": r["type"], "name": r["name"], "typos": typos,
                           "class": "NAME"})
        out["L4"] = {"ok": True, "what": "sqlite_master names",
                     "hits": l4, "n": len(l4),
                     "n_name": len(l4)}
    except sqlite3.Error as e:
        out["L4"] = {"ok": False, "code": "FAULT", "reason": str(e)}

    # L10 — DB COLUMN NAMES. The carrier the human named and NO layer covered.
    l10: list[dict] = []
    try:
        tables = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for tn in tables:
            try:
                cols = [str(c[1]) for c in conn.execute(
                    "PRAGMA table_info(%s)" % tn)]
            except sqlite3.Error:
                # A table that cannot be read is REPORTED, never silently skipped
                # (`empty_detector_failure_class`).
                l10.append({"table": tn, "code": "FAULT",
                            "reason": "PRAGMA table_info failed"})
                continue
            for cn in cols:
                typos = _name_hits(conn, cn)
                if typos:
                    l10.append({"table": tn, "column": cn, "typos": typos,
                                "class": "NAME"})
        out["L10"] = {"ok": True, "what": "sqlite_master column names",
                      "tables_scanned": len(tables), "hits": l10, "n": len(l10),
                      "n_name": sum(1 for h in l10
                                    if h.get("class") == "NAME")}
    except sqlite3.Error as e:
        out["L10"] = {"ok": False, "code": "FAULT", "reason": str(e)}

    # L13 — REGISTRY KEYS (capability / channel / module). A key IS a name.
    l13: list[dict] = []
    try:
        for table, col in (("capability_registry", "capability_key"),
                           ("channel_registry", "channel_key"),
                           ("module_registry", "module_key")):
            if not _table_exists(conn, table) or col not in _columns(conn, table):
                l13.append({"table": table, "code": "FAULT",
                            "reason": "%s.%s absent" % (table, col)})
                continue
            for r in conn.execute("SELECT %s AS k FROM %s" % (col, table)):
                k = str(r["k"] or "")
                typos = _name_hits(conn, k)
                if typos:
                    l13.append({"table": table, "column": col, "key": k,
                                "typos": typos, "class": "NAME"})
        out["L13"] = {"ok": True, "what": "capability/channel/module keys",
                      "hits": l13, "n": len(l13),
                      "n_name": sum(1 for h in l13
                                    if h.get("class") == "NAME")}
    except sqlite3.Error as e:
        out["L13"] = {"ok": False, "code": "FAULT", "reason": str(e)}

    # L14 — ALIASES THAT ARE A DIFFERENT NAME (R-2). THE HUMAN: "can't alias
    # different". An alias that differs from its key is the mis-understanding.
    #
    # 🔴 THE RULE IS NARROWER THAN "differs", AND THE MEASUREMENT SAYS WHY.
    # MEASURED 2026-09-28: 35 aliases differ from their key, but only **9** are a
    # DEFECT. The other 26 are HISTORICAL (a retired name kept for findability,
    # e.g. `proof_run` <- `llm_100_run`) or a QUALIFIER (`5w1h` <- `skill_5w1h`,
    # where the alias ENDS WITH the key, so it is the key qualified by a scope).
    #
    # A DEFECT is an alias that is a **CURRENT name** — a live object or an ACTIVE
    # term — AND is NOT a qualifier of its key. That is exactly the human's case:
    # `terminology_registry` -> alias `terminology_registry`, where the UNIFIED
    # name is demoted to an alias of the wrong one.
    l14: list[dict] = []
    try:
        for r in conn.execute("SELECT term_id, term_key, alias_list FROM %s" % _term_table(conn)):
            raw = str(r["alias_list"] or "")
            if raw in ("", "NA"):
                continue
            try:
                items = json.loads(raw)
            except Exception:
                items = [raw]
            key = str(r["term_key"])
            for a in items:
                a = str(a)
                if not a or a == key:
                    continue
                # A QUALIFIER: the alias ends with the key (`skill_5w1h` <- `5w1h`).
                if a.lower().endswith(key.lower()):
                    continue
                is_obj = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (a,)).fetchone()
                is_active = conn.execute(
                    "SELECT 1 FROM %s WHERE term_key=? AND is_active=1" % _term_table(conn), (a,)).fetchone()
                if not (is_obj or is_active):
                    continue        # a HISTORICAL alias: legitimate, not a defect
                l14.append({"term_id": int(r["term_id"]), "term_key": key,
                            "alias": a,
                            "alias_is_live_object": bool(is_obj),
                            "alias_is_active_term": bool(is_active),
                            "typos": _name_hits(conn, a), "class": "NAME",
                            "why": ("the alias IS a current name, so the key and "
                                    "the alias are TWO names for one concept — "
                                    "the mis-understanding the ruling names")})
        out["L14"] = {"ok": True, "what": "aliases that ARE a current name",
                      "hits": l14, "n": len(l14),
                      "n_name": len(l14)}
    except sqlite3.Error as e:
        out["L14"] = {"ok": False, "code": "FAULT", "reason": str(e)}
    return out


# --------------------------------------------------------------------------
# the file layers
# --------------------------------------------------------------------------
def _is_record_path(path: Path) -> bool:
    """R-4(b): is this file a RECORD (never rewritten) or a GENERATOR?"""
    parts = [p.lower() for p in path.parts]
    if any(p in RECORD_DIRS for p in parts):
        return True
    return bool(RECORD_FILE_RE.match(path.name))


# A BUILD OUTPUT is not source. MEASURED 2026-09-28: rewriting
# `llm_task_monitor_ui/dist/assets/index-*.js` would be undone by the next build,
# so the fix is to REBUILD, not to hand-edit. The file is REPORTED, never written.
GENERATED_DIRS = ("dist", "build", "out", ".next")


def _is_generated_path(path: Path) -> bool:
    return any(p.lower() in GENERATED_DIRS for p in path.parts)


def _iter_files(root: Path, kinds: set[str]) -> list[Path]:
    exts = {e for e, k in TEXT_EXT.items() if k in kinds}
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "__pycache__", ".venv",
                                    "node_modules", "chrome_cdp_profile"}]
        for fn in filenames:
            p = Path(dirpath) / fn
            if p.suffix.lower() in exts:
                out.append(p)
    return out


def audit_file_layers(root: Path, conn: sqlite3.Connection) -> dict:
    """L5..L9: every file layer. Each reports a NUMBER, or a FAULT.

    THREE BUCKETS, and a hit in NONE of them is a FAILURE:
      * NAME   — the wrong spelling is CODE (an identifier / a literal in code)
      * RECORD — the file is evidence/history; it RE-PLAYS the name
      * QUOTE  — the occurrence is in PROSE (a comment/docstring), i.e. the
                 defect being DISCUSSED. Measured: it is the LARGEST bucket.
    """
    _load_bl(conn)
    out: dict[str, dict] = {}
    spec = {"L5": {"py"}, "L6": {"js"}, "L7": {"md"}, "L8": {"json"},
            "L9": {"sql"}}
    for lid, kinds in spec.items():
        try:
            files = _iter_files(root, kinds)
        except Exception as e:
            out[lid] = {"ok": False, "code": "FAULT", "reason": str(e)}
            continue
        hits: list[dict] = []
        for p in files:
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                hits.append({"path": str(p.relative_to(root)),
                             "code": "FAULT", "reason": str(e)})
                continue
            kind = TEXT_EXT.get(p.suffix.lower(), "txt")
            if not _typo_rows(conn, txt):
                continue
            rec = _is_record_path(p)
            decl = declaration_carrier(root, str(p.relative_to(root)))
            code = _code_only(txt, kind)
            in_code = _typo_rows(conn, code)
            if rec:
                cls = "RECORD"
            elif _is_generated_path(p):
                cls = "GENERATED"
            elif decl:
                cls = "DECLARATION"
            elif in_code:
                cls = "NAME"
            else:
                # It appears ONLY in prose -> the human's own words, or a
                # description OF the defect. Never a rewrite target.
                cls = "QUOTE"
            hits.append({
                "path": str(p.relative_to(root)),
                "class": cls,
                "typos": _typo_rows(conn, txt),
                "n_typos": len(_typo_rows(conn, txt)),
                "in_code": [t["wrong"] for t in in_code],
                "record_of": (str(p.relative_to(root)) if cls == "RECORD"
                              else None),
                "quote_of": (str(p.relative_to(root)) if cls == "QUOTE"
                             else None),
                "declared_by": (decl.get("kind") if decl else None),
                "declaration_cite": (decl.get("cite_ref") if decl else None),
            })
        out[lid] = {"ok": True, "files_scanned": len(files), "hits": hits,
                    "n": len(hits),
                    "n_name": sum(1 for h in hits if h.get("class") == "NAME"),
                    "n_record": sum(1 for h in hits if h.get("class") == "RECORD"),
                    "n_quote": sum(1 for h in hits if h.get("class") == "QUOTE"),
                    "n_declaration": sum(1 for h in hits
                                         if h.get("class") == "DECLARATION"),
                    "n_generated": sum(1 for h in hits
                                       if h.get("class") == "GENERATED")}

    # L11 — FILE NAMES. The carrier the human named and NO layer covered.
    # MEASURED 2026-09-28: `path.name` was used only for RECORD classification.
    # The walk is the SAME one L5..L9 use, so a file is scanned once for its
    # content and once for its name — and a name hit is a RENAME, not a rewrite.
    try:
        all_files = _iter_files(root, {"py", "js", "md", "json", "sql"})
    except Exception as e:
        out["L11"] = {"ok": False, "code": "FAULT", "reason": str(e)}
        return out
    l11: list[dict] = []
    for p in all_files:
        # 🔴 THE STEM, NOT THE FILENAME. MEASURED 2026-09-28: `skill_field_registry.py`
        # does NOT end with `_register` — it ends with `.py` — so a declared SUFFIX
        # rule never matched and 65 files were left un-renamed. The NAME is the
        # stem; the extension is not part of it.
        typos = _name_hits(conn, p.stem)
        if not typos:
            continue
        rec = _is_record_path(p)
        decl = declaration_carrier(root, str(p.relative_to(root)))
        if rec:
            cls = "RECORD"
        elif _is_generated_path(p):
            cls = "GENERATED"
        elif decl:
            cls = "DECLARATION"
        else:
            cls = "NAME"
        l11.append({"path": str(p.relative_to(root)), "filename": p.name,
                    "stem": p.stem,
                    "class": cls, "typos": typos, "n_typos": len(typos)})
    out["L11"] = {"ok": True, "what": "file NAMES (p.name)",
                  "files_scanned": len(all_files), "hits": l11, "n": len(l11),
                  "n_name": sum(1 for h in l11 if h.get("class") == "NAME"),
                  "n_record": sum(1 for h in l11 if h.get("class") == "RECORD"),
                  "n_declaration": sum(1 for h in l11
                                       if h.get("class") == "DECLARATION"),
                  "n_generated": sum(1 for h in l11
                                     if h.get("class") == "GENERATED")}

    # L12 — FUNCTION NAMES. THE HUMAN: "function ... = same no matter everywhere".
    # MEASURED: 64 `def`s contain `_register`; NO layer scanned a function name.
    # AST, not a regex: a `def` inside a docstring or a comment is not a function.
    l12: list[dict] = []
    try:
        import ast as _ast
        py_files = _iter_files(root, {"py"})
        for p in py_files:
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(txt)
            except Exception:
                continue
            rec = _is_record_path(p)
            decl = declaration_carrier(root, str(p.relative_to(root)))
            for node in _ast.walk(tree):
                if not isinstance(node, (_ast.FunctionDef,
                                         _ast.AsyncFunctionDef)):
                    continue
                typos = _name_hits(conn, node.name)
                if not typos:
                    continue
                if rec:
                    cls = "RECORD"
                elif _is_generated_path(p):
                    cls = "GENERATED"
                elif decl:
                    cls = "DECLARATION"
                else:
                    cls = "NAME"
                l12.append({"path": str(p.relative_to(root)),
                            "function": node.name, "line": node.lineno,
                            "class": cls, "typos": typos,
                            "n_typos": len(typos)})
        out["L12"] = {"ok": True, "what": "function names (AST)",
                      "files_scanned": len(py_files), "hits": l12, "n": len(l12),
                      "n_name": sum(1 for h in l12 if h.get("class") == "NAME"),
                      "n_record": sum(1 for h in l12
                                      if h.get("class") == "RECORD"),
                      "n_declaration": sum(1 for h in l12
                                           if h.get("class") == "DECLARATION")}
    except Exception as e:
        out["L12"] = {"ok": False, "code": "FAULT", "reason": str(e)}
    return out


# --------------------------------------------------------------------------
# the ONE classification door
# --------------------------------------------------------------------------
def classify(conn: sqlite3.Connection, name: str) -> dict:
    """The verdict on ONE name. A LOOKUP across the two registers + the law.

    Order is DECLARED and matters:
      1. WHITELIST  -> EXEMPT   (a declared-correct name is never rewritten)
      2. BLACKLIST  -> WRONG    (a declared-wrong name must be renamed/deleted)
      3. COMPOSITE  -> UNDECOMPOSABLE (a word-level refusal)
      4. otherwise  -> CLEAN

    The whitelist is checked FIRST on purpose: an exemption outranks a general
    rule, and checking it later would let a token rule rewrite a legitimate name.
    """
    import terminology_blacklist as bl
    import terminology_whitelist as wl
    ex = wl.is_exempt(conn, name)
    if ex.get("exempt"):
        return {"ok": True, "verdict": "EXEMPT", "name": name,
                "why": ex.get("reason"), "cite_ref": ex.get("cite_ref"),
                "by": ex.get("by"), "scope": ex.get("scope")}
    b = bl.check(conn, name)
    if not b["ok"]:
        return {"ok": True, "verdict": "WRONG", "name": name,
                "correction": b.get("correction") or b.get("suggestion"),
                "match": b.get("match"), "kind": b.get("kind"),
                "why": b.get("reason"), "cite_ref": b.get("cite_ref"),
                "code": b["code"]}
    c = _composite_refusals(conn, name)
    if c.get("ok") and c.get("refused"):
        return {"ok": True, "verdict": "UNDECOMPOSABLE", "name": name,
                "code": c.get("code"),
                "missing_words": c.get("missing_words"),
                "duplicate_of": c.get("duplicate_of"),
                "why": ("a word of this name is not a registered term, so a "
                        "reader has to guess what it means"),
                "cite_ref": "unified_language.py:604"}
    if not c.get("ok"):
        return {"ok": False, "code": "FAULT", "name": name,
                "reason": c.get("reason")}
    return {"ok": True, "verdict": "CLEAN", "name": name}


def plan_writes(conn: sqlite3.Connection, root: Path,
                rep: dict | None = None) -> dict:
    """The ACTIONS the audit implies, MECHANICALLY. No judgement, no vote.

    MEASURED 2026-09-28: this layer separates two things the first audit
    CONFLATED, and conflating them is what produced the false positives:

      * a `typo` / `abbreviation` row -> the WRONG-NAME defect (R-1)
      * a `MISSING_WORD` verdict      -> a DIFFERENT class: a name nobody can
        DECOMPOSE. `proof_e2_e3_loop` (id 790, ACTIVE) is not misspelt; it
        contains the unregistered tokens `e2`, `e3`.

    R-1 says a WRONG NAME is renamed or deleted. An UNDECOMPOSABLE name that is
    a SCRATCH FILE's name (`proof_e2_e3_loop`, `fix_11d`) is already DEAD and is
    reported for the register's own retirement rule
    (`register_vocabulary.retire_unevidenced`) — **this module does NOT retire
    another worker's rows.**

    MEASURED scope of the two classes in the live register:
      * wrong-name (blacklist) rows in the register: **3** (`five_w1h_derive`,
        and 2 alias entries) — all `is_active=0`
      * undecomposable active term_keys: **2** (`proof_e2_e3_loop`, `verify_s3_apply`)
    """
    import terminology_blacklist as bl
    actions: list[dict] = []
    seen: set = set()
    rows = list(conn.execute("SELECT term_id, term_key, alias_list, is_active "
                             "FROM %s" % _term_table(conn)))
    for r in rows:
        b = bl.check(conn, r["term_key"])
        if not b["ok"]:
            actions.append({
                "object": "term_key", "term_id": int(r["term_id"]),
                "name": r["term_key"], "is_active": int(r["is_active"]),
                "action": "RENAME" if int(r["is_active"]) == 1 else "DELETE",
                "to": b.get("correction"), "why": b.get("reason"),
                "cite_ref": b.get("cite_ref"), "class": b.get("kind"),
            })
        raw = str(r["alias_list"] or "")
        if raw not in ("", "NA"):
            try:
                items = json.loads(raw)
            except Exception:
                items = [raw]
            for a in items:
                b2 = bl.check(conn, a)
                if not b2["ok"]:
                    actions.append({
                        "object": "alias", "term_id": int(r["term_id"]),
                        "owner": r["term_key"], "name": a,
                        "action": "DELETE_ALIAS",
                        "to": b2.get("correction"), "why": b2.get("reason"),
                        "cite_ref": b2.get("cite_ref"), "class": b2.get("kind"),
                    })
    # the file layer: every NAME-class hit is a REWRITE target.
    # `rep` may be PASSED IN — MEASURED: recomputing the whole audit here doubled
    # the runtime and the proof timed out, because `apply` calls `plan_writes`
    # and then `plan_writes` again for `remaining`.
    rep = rep if rep is not None else audit(conn, root)
    # 🔴 L1 — a TERM KEY. MEASURED 2026-09-28: L1 was DETECTED and never turned
    # into an action, so a term ending `_register` was reported and never renamed.
    # The action is RENAME (the term names a live object) or DELETE (it does not).
    for lid in ("L1",):
        l = rep["detail"].get(lid) or {}
        if not l.get("ok"):
            continue
        for h in l.get("hits", []):
            tk = str(h.get("term_key") or "")
            new_tk = _corrected(tk, h.get("typos", []))
            if new_tk == tk:
                continue
            key = ("term", int(h.get("term_id") or 0))
            if key in seen:
                continue
            seen.add(key)
            first = (h.get("typos") or [{}])[0]
            # 🔴 AN ALREADY-MERGED TERM IS NOT AN ACTION. MEASURED 2026-09-28: a
            # RETIRED term keeps its old key, so it generated a RENAME forever and
            # the second apply was never a no-op. If the term is INACTIVE and its
            # key is ALREADY an alias of the survivor, the merge is DONE.
            if int(h.get("is_active") or 0) == 0:
                done = conn.execute(
                    "SELECT 1 FROM %s WHERE term_key=? AND is_active=1 AND "
                    "alias_list LIKE ?" % _term_table(conn),
                    (new_tk, "%" + tk + "%")).fetchone()
                if done:
                    continue
            # 🔴 THE ACTION IS RENAME, NOT DELETE — CORRECTED 2026-09-28 BY THE
            # HUMAN'S RULING. The old rule DELETED a term that names no object
            # ("renaming would fabricate a table name"). But the ruling is about
            # the NAME, not about whether it names a table: "alias / filename /
            # table / field / function capability / channel / module = same no
            # matter everywhere". `create_terminology_registry` is an ACTION whose
            # name carries the wrong spelling; the unified name is
            # `create_terminology_registry`. A COLLISION is a MERGE (a human
            # decision), which the preflight reports — not a reason to delete.
            names_object = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') "
                "AND name=?", (tk,)).fetchone()
            actions.append({
                "object": "term_key", "layer": lid,
                "term_id": int(h.get("term_id") or 0),
                "name": tk, "action": "RENAME",
                "to": new_tk, "why": first.get("cite_ref"),
                "cite_ref": first.get("cite_ref"),
                "class": first.get("kind"),
                "names_live_object": bool(names_object),
            })
    # 🔴 L15 — `db_table_registry.table_key` and `entity_type_registry.register_table`.
    # MEASURED 2026-09-28: renaming the TABLE without these two leaves the registry
    # pointing at a name that no longer exists, and `entity_type_registry` is what
    # ENTITY ID RESOLUTION reads — so every entity id for that letter stops
    # resolving. These are the two carriers that make the rename SAFE.
    for table, col, kind in (("db_table_registry", "table_key", "table"),
                             ("entity_type_registry", "register_table",
                              "entity_letter")):
        if not _table_exists(conn, table) or col not in _columns(conn, table):
            continue
        for r in conn.execute("SELECT %s AS k FROM %s" % (col, table)):
            k = str(r["k"] or "")
            hits = _name_hits(conn, k)
            if not hits:
                continue
            new_k = _corrected(k, hits)
            if new_k == k:
                continue
            key = ("reg", table, k)
            if key in seen:
                continue
            seen.add(key)
            first = hits[0]
            actions.append({
                "object": "registry_key", "layer": "L15",
                "table": table, "column": col, "name": k,
                "action": "RENAME_KEY", "to": new_k,
                "why": first.get("cite_ref"),
                "cite_ref": first.get("cite_ref"),
                "class": first.get("kind"), "carrier": kind,
            })
    for lid, l in rep["detail"].items():
        if not l.get("ok"):
            continue
        for h in l.get("hits", []):
            if h.get("class") != "NAME":
                continue
            # 🔴 L4 / L10 — a DB OBJECT or COLUMN name. MEASURED 2026-09-28: L4
            # hits had no `class`, so they were dropped here; and L10 did not
            # exist at all. Both are now RENAME_OBJECT actions.
            if lid in ("L4", "L10"):
                # 🔴 THE CORRECTED NAME IS THE WHOLE NAME, not the bare
                # correction. MEASURED 2026-09-28: a table `zz_enviornment_probe`
                # carries the typo as a TOKEN, so renaming it to `environment`
                # would DROP `zz_` and `_probe` — a rename that loses the name it
                # was renaming. Every typo is substituted in place, and the
                # result is ONE action per object (two actions would make the
                # second a NO_SUCH_OBJECT against the name the first just moved).
                obj_name = str(h.get("name") or h.get("column") or "")
                new_name = _corrected(obj_name, h.get("typos", []))
                if new_name == obj_name:
                    continue
                key = ("obj", h.get("table") or "", obj_name)
                if key in seen:
                    continue
                seen.add(key)
                first = h["typos"][0]
                actions.append({
                    "object": "db_object", "layer": lid,
                    "object_type": h.get("type") or "column",
                    "table": h.get("table"),
                    "name": obj_name,
                    "action": "RENAME_OBJECT",
                    "to": new_name, "why": first.get("cite_ref"),
                    "cite_ref": first.get("cite_ref"),
                    "class": first.get("kind"),
                })
                continue
            # 🔴 L11 — a FILE NAME. A RENAME, never a content rewrite.
            if lid == "L11":
                # 🔴 A `_proof_*` FILE IS NEVER RENAMED. MEASURED 2026-09-28: a
                # proof asserts a SPECIFIC spelling, so renaming the file (or its
                # content) makes its assertion tautological — the same rule the
                # content layer already applies. It is REPORTED, not renamed.
                if str(h.get("filename") or "").startswith("_proof_"):
                    continue
                # The NAME is the STEM; the extension is not part of it.
                fn = str(h.get("stem") or h.get("filename") or "")
                new_fn = _corrected(fn, h.get("typos", []))
                if new_fn == fn:
                    continue
                key = ("file_name", h["path"])
                if key in seen:
                    continue
                seen.add(key)
                first = h["typos"][0]
                actions.append({
                    "object": "file_name", "layer": lid,
                    "path": h["path"], "filename": h.get("filename"),
                    "name": fn, "action": "RENAME_FILE",
                    "to": new_fn, "why": first.get("cite_ref"),
                    "cite_ref": first.get("cite_ref"),
                    "class": first.get("kind"),
                })
                continue
            # 🔴 L12 — a FUNCTION NAME. A RENAME of the identifier.
            if lid == "L12":
                fname = str(h.get("function") or "")
                new_fname = _corrected(fname, h.get("typos", []))
                if new_fname == fname:
                    continue
                key = ("function", h["path"], fname)
                if key in seen:
                    continue
                seen.add(key)
                first = h["typos"][0]
                actions.append({
                    "object": "function", "layer": lid,
                    "path": h["path"], "name": fname, "line": h.get("line"),
                    "action": "RENAME_FUNCTION",
                    "to": new_fname, "why": first.get("cite_ref"),
                    "cite_ref": first.get("cite_ref"),
                    "class": first.get("kind"),
                })
                continue
            # 🔴 L13 — a REGISTRY KEY (capability / channel / module).
            if lid == "L13":
                k = str(h.get("key") or "")
                new_k = _corrected(k, h.get("typos", []))
                if new_k == k:
                    continue
                key = ("key", h.get("table") or "", k)
                if key in seen:
                    continue
                seen.add(key)
                first = h["typos"][0]
                actions.append({
                    "object": "registry_key", "layer": lid,
                    "table": h.get("table"), "column": h.get("column"),
                    "name": k, "action": "RENAME_KEY",
                    "to": new_k, "why": first.get("cite_ref"),
                    "cite_ref": first.get("cite_ref"),
                    "class": first.get("kind"),
                })
                continue
            # 🔴 L14 — an ALIAS that DIFFERS from its key (R-2). THE HUMAN:
            # "can't alias different". The action is DELETE_ALIAS, never a rename:
            # an alias that is a different name is the mis-understanding itself.
            if lid == "L14":
                key = ("alias_diff", int(h.get("term_id") or 0),
                       str(h.get("alias") or ""))
                if key in seen:
                    continue
                seen.add(key)
                first = (h.get("typos") or [{}])[0]
                actions.append({
                    "object": "alias", "layer": lid,
                    "term_id": int(h.get("term_id") or 0),
                    "owner": h.get("term_key"), "name": h.get("alias"),
                    "action": "DELETE_ALIAS",
                    "to": "", "why": h.get("why"),
                    "cite_ref": first.get("cite_ref")
                    or "name_unify.py:L14",
                    "class": first.get("kind") or "alias_differs",
                })
                continue
            for t in h.get("typos", []):
                key = (h["path"], t["wrong"].lower())
                if key in seen:
                    continue
                seen.add(key)
                actions.append({
                    "object": "file", "path": h["path"], "layer": lid,
                    "name": t["wrong"], "action": "REWRITE",
                    "to": t["correction"], "why": t.get("cite_ref"),
                    "cite_ref": t.get("cite_ref"), "class": t.get("kind"),
                })
    # 🔴 L16 — LIVE-CODE REFERENCES to a renamed object. MEASURED 2026-09-28:
    # without this, `REWRITE` was 0 while 588 files still named the old table.
    # The renamed set is the DB-object + key + term actions already planned.
    renamed = [x for x in actions
               if x["action"] in ("RENAME_OBJECT", "RENAME_KEY", "RENAME")]
    for a in code_ref_actions(conn, root, renamed):
        key = ("code_ref", a["path"], a["name"])
        if key in seen:
            continue
        seen.add(key)
        actions.append(a)
    # 🔴 L17 — PROOF REFERENCES to a renamed object. MEASURED 2026-09-28: the
    # `_proof_*` skip in `code_ref_actions` is sound (a proof asserts a SPECIFIC
    # spelling), but its CONSEQUENCE was never measured: 284 proofs named 119
    # removed names, and 6 of them were RED for that reason alone. A proof is a
    # CARRIER too, so the SAME rule and the SAME exemption list apply.
    for a in proof_ref_actions(conn, root):
        key = ("proof_ref", a["path"], a["name"])
        if key in seen:
            continue
        seen.add(key)
        actions.append(a)
    return {
        "ok": True,
        "n_actions": len(actions),
        "by_action": {a: sum(1 for x in actions if x["action"] == a)
                      for a in ("RENAME", "DELETE", "DELETE_ALIAS", "REWRITE",
                                "RENAME_OBJECT", "RENAME_FILE",
                                "RENAME_FUNCTION", "RENAME_KEY")},
        "actions": actions,
        "cite": "name_unify.py:plan_writes",
    }


def purge_wrong_duplicate(conn: sqlite3.Connection, *, cite_ref: str = "",
                          dry_run: bool = True, commit: bool = True) -> dict:
    """R-1 DELETE for a wrong name whose CORRECT name already EXISTS.

    WHY THIS IS NEEDED, MEASURED 2026-09-28: `name_unify --apply` removed the
    alias entries and retired the rows, yet `resolve_name('five_w1h_derive')`
    STILL answered, because the retired row keeps its `term_key` as a **live
    stored name**. R-2 says *a wrong name that still resolves is still a wrong
    name*, so retiring is not enough when the concept already exists under its
    correct name — the duplicate ROW must go.

    THE PRECONDITION IS A MEASUREMENT, not a choice:
      * the row's `term_key` is on the DECLARED BLACKLIST; AND
      * the blacklist's `correction` ALREADY EXISTS as an ACTIVE term.
    If either fails it is REFUSED (`NOT_BLACKLISTED` / `NO_LIVE_CORRECTION`),
    because deleting a name nothing replaces is a loss, not a cleanup.

    The deleted row is written to `terminology_name_unify_deleted` FIRST, so the
    removal is reversible and auditable rather than silent.
    """
    import terminology_blacklist as bl
    out = {"ok": True, "dry_run": bool(dry_run), "deleted": [], "refused": [],
           "cite": "name_unify.py:purge_wrong_duplicate"}
    if not str(cite_ref or "").strip():
        out["ok"] = False
        out["code"] = "UNCITED"
        return out
    if not dry_run:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS terminology_name_unify_deleted ("
            " old_term_id INTEGER, old_term_key TEXT, old_definition TEXT, "
            " correction TEXT, cite_ref TEXT, deleted_at TEXT)")
    for r in list(conn.execute("SELECT term_id, term_key, definition FROM "
                              "terminology_registry")):
        b = bl.check(conn, r["term_key"])
        if b["ok"]:
            continue
        corr = str(b.get("correction") or "")
        live = conn.execute("SELECT term_id FROM %s WHERE term_key=? AND is_active=1" % _term_table(conn), (corr,)).fetchone()
        if not live:
            out["refused"].append({"term_id": int(r["term_id"]),
                                   "term_key": r["term_key"],
                                   "code": "NO_LIVE_CORRECTION",
                                   "correction": corr})
            continue
        entry = {"term_id": int(r["term_id"]), "term_key": r["term_key"],
                 "correction": corr, "survivor_term_id": int(live["term_id"])}
        if not dry_run:
            conn.execute(
                "INSERT INTO terminology_name_unify_deleted (old_term_id, "
                "old_term_key, old_definition, correction, cite_ref, deleted_at)"
                " VALUES (?,?,?,?,?,datetime('now'))",
                (int(r["term_id"]), r["term_key"], r["definition"], corr,
                 str(cite_ref)))
            conn.execute("DELETE FROM %s WHERE term_id=?" % _term_table(conn),
                         (int(r["term_id"]),))
            if commit:
                conn.commit()
        out["deleted"].append(entry)
    out["n_deleted"] = len(out["deleted"])
    return out


def _release(conn: sqlite3.Connection, out: dict) -> None:
    """Release the savepoint, tolerating its absence.

    A door that self-committed would already have released it; that is a fact to
    RECORD, not a fault to raise.
    """
    try:
        conn.execute("RELEASE name_unify_apply")
    except sqlite3.Error:
        out["savepoint_released_early"] = True


def rule_owners(conn: sqlite3.Connection) -> dict:
    """QC-09 — how many OWNERS does "rename a name" have, and can they merge?

    MEASURED 2026-09-28: TWO owners, with DISJOINT rules.
      * `name_unify.py` — a DECLARED blacklist row (an exact or whole-token name)
      * `_unify_registry_naming.py:55` — `OLD, NEW = "_register", "_registry"`,
        a SUFFIX rule

    A suffix rule CANNOT be expressed as a blacklist row: the blacklist's `kind`
    vocabulary is measured below, and every kind names a WHOLE name, not a
    pattern. So the honest answer is to REPORT the second owner with its number
    rather than invent a second matching engine inside this module (which would
    be a THIRD owner, not a merge).

    This function returns NUMBERS, so the claim is checkable.
    """
    import terminology_blacklist as bl
    kinds: dict[str, int] = {}
    for r in bl._rows(conn):
        kinds[str(r["kind"])] = kinds.get(str(r["kind"]), 0) + 1
    suffix_owner = BASE / "_unify_registry_naming.py"
    suffix_rule = None
    if suffix_owner.exists():
        txt = suffix_owner.read_text(encoding="utf-8", errors="replace")
        for line in txt.splitlines():
            if line.strip().startswith("OLD, NEW"):
                suffix_rule = line.strip()
                break
    return {
        "ok": True,
        "owners": 2 if suffix_rule else 1,
        "blacklist_rows": sum(kinds.values()),
        "blacklist_kinds": kinds,
        "suffix_rule": suffix_rule,
        "suffix_rule_expressible_as_a_row": False,
        "why": ("every blacklist `kind` names a WHOLE name; a SUFFIX rule "
                "(`_register` -> `_registry`) is a PATTERN, so expressing it as "
                "a row would need a new kind AND a new matcher — a THIRD owner, "
                "not a merge. REPORTED, not merged."),
        "cite": "name_unify.py:rule_owners",
    }


def direction_rule(conn: sqlite3.Connection) -> dict:
    """QC-14 — the DIRECTION is a DECLARED row with a cite, not an `if`.

    THE HUMAN (2026-09-28), verbatim:
        "terminology_registry is unified language only!! can't alias different,
         alias / filename / table / field / function capability / channel /
         module = same no matter everywhere / this is solution for
         mis-underestand"

    So `_register` -> `_registry` is the UNIFIED LANGUAGE, and it is read from
    `terminology_blacklist` (a TABLE), never typed into this module. A rule that
    lives in an `if` is a rule a reader cannot cite.

    MEASURED: the blacklist's `kind` vocabulary is `{typo, abbreviation}` — every
    kind names a WHOLE name. A SUFFIX rule (`_register` -> `_registry`) is a
    PATTERN, so it is declared as a row whose `wrong` is the SUFFIX and whose
    `kind` is `suffix`, and the matcher is the SAME `token_re` used everywhere
    else (a suffix is matched at the END of a token, which is what `token_re`
    already does for a whole token).

    Returns NUMBERS, so the claim is checkable.
    """
    import terminology_blacklist as bl
    rows = bl._rows(conn)
    suffix_rows = [r for r in rows if str(r["kind"]) == "suffix"]
    return {
        "ok": True,
        "declared_rows": len(rows),
        "suffix_rows": len(suffix_rows),
        "suffix_rule": ({"wrong": suffix_rows[0]["wrong"],
                         "correction": suffix_rows[0]["correction"],
                         "cite_ref": suffix_rows[0]["cite_ref"]}
                        if suffix_rows else None),
        "kinds": sorted({str(r["kind"]) for r in rows}),
        "is_declared": bool(suffix_rows),
        "cite": "name_unify.py:direction_rule",
    }


def _rename_function(root: Path, a: dict) -> dict:
    """RENAME a `def` whose NAME carries the wrong token.

    THE HUMAN (2026-09-28): "function ... = same no matter everywhere".

    The identifier is rewritten as a WHOLE TOKEN, so a CALL SITE is renamed with
    it and a LONGER name is not (`zz_enviornment_fn_2` must not become
    `zz_environment_fn_2` by accident — the token boundary is what stops it).
    The `def` line is located by the AST line number, so a same-named string in a
    docstring is not touched.
    """
    rel = str(a.get("path") or "")
    src = root / rel
    if not src.exists():
        return {"ok": False, "code": "NO_SUCH_FILE", "reason": rel}
    old = str(a.get("name") or "")
    new = str(a.get("to") or "")
    if not old or not new or old == new:
        return {"ok": False, "code": "NO_CHANGE"}
    try:
        txt = src.read_text(encoding="utf-8", errors="surrogateescape")
    except Exception as e:
        return {"ok": False, "code": "UNREADABLE", "reason": str(e)}
    # A WHOLE-IDENTIFIER rewrite of the identifier, everywhere it appears (the def
    # and every call site). `ident_re` keeps `_` as PART of the token, so
    # `zz_enviornment_fn_2` is left alone — MEASURED: `token_re` rewrote it.
    new_txt = ident_re(old).sub(new, txt)
    if new_txt == txt:
        return {"ok": False, "code": "NO_CHANGE"}
    src.write_text(new_txt, encoding="utf-8", errors="surrogateescape")
    return {"ok": True, "path": rel, "old": old, "new": new}


def _rename_key(conn: sqlite3.Connection, a: dict) -> dict:
    """RENAME a registry KEY (capability / channel / module).

    THE HUMAN (2026-09-28): "capability / channel / module = same no matter
    everywhere". A key IS a name, so it is the same carrier class as a table.

    REFUSES a collision: two rows for one key is a MERGE, which is a human
    decision, not a rename.
    """
    table = str(a.get("table") or "")
    col = str(a.get("column") or "")
    old = str(a.get("name") or "")
    new = str(a.get("to") or "")
    if not table or not col or not old or not new:
        return {"ok": False, "code": "NO_REPLACEMENT"}
    if not _table_exists(conn, table) or col not in _columns(conn, table):
        return {"ok": False, "code": "NO_SUCH_OBJECT",
                "reason": "%s.%s absent" % (table, col)}
    if not conn.execute("SELECT 1 FROM %s WHERE %s=?" % (table, col),
                        (old,)).fetchone():
        return {"ok": False, "code": "NO_SUCH_ROW", "reason": old}
    if conn.execute("SELECT 1 FROM %s WHERE %s=?" % (table, col),
                    (new,)).fetchone():
        return {"ok": False, "code": "DESTINATION_EXISTS",
                "reason": "%s already holds %s" % (table, new)}
    conn.execute("UPDATE %s SET %s=? WHERE %s=?" % (table, col, col),
                 (new, old))
    return {"ok": True, "table": table, "column": col, "old": old, "new": new}


def _merge_term_collision(conn: sqlite3.Connection, a: dict) -> dict:
    """MERGE two terms that collide on the unified name. The RULING decides which.

    THE HUMAN (2026-09-28): "terminology_registry is unified language only!! can't
    alias different". So the SURVIVOR is the row whose key ALREADY IS the unified
    name (`a['to']`); the other row is RETIRED and its key becomes an ALIAS of the
    survivor — a tombstone for a name that is no longer current.

    REFUSES when the survivor cannot be identified, rather than picking one:
      * `NO_SURVIVOR` — no row holds the unified name, so the direction is not
        decidable from the register (a human decision).
    """
    table = _term_table(conn)
    to = str(a.get("to") or "")
    src_id = int(a.get("term_id") or 0)
    survivor = conn.execute(
        "SELECT term_id, term_key, alias_list FROM %s WHERE term_key=?" % table,
        (to,)).fetchone()
    if not survivor:
        return {"ok": False, "code": "NO_SURVIVOR",
                "reason": ("no term holds the unified name %r, so the merge "
                           "direction is not decidable from the register" % to)}
    if int(survivor["term_id"]) == src_id:
        return {"ok": False, "code": "SAME_ROW"}
    old_key = str(a.get("name") or "")
    # The OLD key becomes an ALIAS of the survivor (a tombstone), and the source
    # row is RETIRED — never deleted, so the trail survives.
    raw = str(survivor["alias_list"] or "")
    try:
        items = json.loads(raw) if raw not in ("", "NA") else []
    except Exception:
        items = [raw]
    if old_key and old_key not in items:
        items.append(old_key)
    conn.execute("UPDATE %s SET alias_list=? WHERE term_id=?" % table,
                 (json.dumps(items), int(survivor["term_id"])))
    # 🔴 THE SURVIVOR MUST BE ACTIVE. MEASURED 2026-09-28: the survivor
    # (`terminology_registry`, 1377) was `is_active=0`, so retiring the source
    # left the concept with NO active term — the merge would have DELETED the
    # concept while reporting success. The unified name is the CURRENT name, so
    # the survivor is activated in the same write.
    conn.execute("UPDATE %s SET is_active=1 WHERE term_id=?" % table,
                 (int(survivor["term_id"]),))
    conn.execute("UPDATE %s SET is_active=0 WHERE term_id=?" % table,
                 (src_id,))
    return {"ok": True, "survivor_term_id": int(survivor["term_id"]),
            "survivor_key": to, "retired_term_id": src_id,
            "alias_added": old_key}


def code_ref_actions(conn: sqlite3.Connection, root: Path,
                     renamed: list[dict]) -> list[dict]:
    """REWRITE actions for LIVE CODE that references a renamed object.

    🔴 WHY THIS IS SEPARATE FROM L5..L9, MEASURED 2026-09-28: L5..L9 test the
    WHOLE FILE TEXT against the blacklist, and a declared SUFFIX rule only matches
    a name that ENDS with the suffix — a file's text never does. So a code
    reference to `terminology_registry` was INVISIBLE, and `REWRITE` was 0 while
    588 files still named the old table.

    The correct unit is the TOKEN: every identifier in the file that ENDS WITH a
    renamed object's name is a reference. The rewrite is a WHOLE-TOKEN
    substitution, so `terminology_registry` is rewritten and
    `terminology_registry_x` is NOT.

    A RECORD (`qc_evidence/`, `evidence/`) and a `_proof_*` file are NEVER
    rewritten — they are COUNTED and REPORTED (R-4(b)).
    """
    pairs: dict[str, str] = {}
    for r in renamed:
        old = str(r.get("name") or "")
        new = str(r.get("to") or "")
        if old and new and old != new:
            pairs[old] = new
    # 🔴 THE DECLARED RULE DRIVES THE REWRITE, NOT THE CURRENT PLAN. MEASURED
    # 2026-09-28: after the tables were renamed, the plan no longer held a
    # RENAME_OBJECT for them, so `db_schema.py`'s `CREATE TABLE IF NOT EXISTS
    # code_registry` was never rewritten — and `ensure_schema` RECREATED all 17
    # old tables on the next run. A rewrite must be driven by the RULE, so it
    # covers a name the plan has already renamed.
    suffix_pairs: dict[str, str] = {}
    for r in _suffix_rows(conn):
        w = str(r["wrong"])
        c = str(r["correction"])
        if w and c:
            pairs.setdefault(w, c)
            suffix_pairs[w] = c
    if not pairs:
        return []
    rx = re.compile(r"\b(" + "|".join(
        sorted((re.escape(k) for k in pairs), key=len, reverse=True)) + r")\b")
    seg_rx = (re.compile(r"(?<=[A-Za-z0-9_])(%s)(?![A-Za-z0-9])"
                         % "|".join(sorted((re.escape(k)
                                            for k in suffix_pairs),
                                           key=len, reverse=True)))
              if suffix_pairs else None)
    out: list[dict] = []
    for p in _iter_files(root, {"py", "js", "json", "sql"}):
        if _is_record_path(p) or _is_generated_path(p):
            continue
        if p.name.startswith("_proof_"):
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        hits = sorted(set(rx.findall(txt)))
        if seg_rx:
            hits = sorted(set(hits) | set(seg_rx.findall(txt)))
        if not hits:
            continue
        # The corrected name is the WHOLE name with every rule applied in place.
        first = hits[0]
        new_first = _corrected(first, [{"wrong": k, "correction": v,
                                        "kind": ("suffix" if k in suffix_pairs
                                                 else "typo")}
                                       for k, v in pairs.items()])
        out.append({
            "object": "file", "layer": "L16",
            "path": str(p.relative_to(root)),
            "name": first, "action": "REWRITE",
            "to": new_first,
            "names": hits,
            "why": "a live-code reference to a renamed object",
            "cite_ref": "name_unify.py:code_ref_actions",
            "class": "code_ref",
        })
    return out


def _sub_segment_preserving_case(text: str, wrong: str, right: str) -> str:
    """Replace `wrong` as a SEGMENT, PRESERVING the case of the matched text.

    🔴 WHY THIS EXISTS (MEASURED 2026-09-28). The first version used
    `re.sub(..., flags=re.IGNORECASE)` with a lowercase replacement, so
    `SKILL_REGISTRY_DDL` became `SKILL_registry_DDL` — a name that does not
    exist. MEASURED: 66 such tokens across the proofs. A substitution that
    changes the CASE of a name INVENTS A THIRD NAME, which is worse than leaving
    the old one: the old name at least resolves to a real thing.

    The rule: match case-insensitively, then reproduce the MATCHED text's case in
    the replacement. `SKILL_REGISTRY` -> `SKILL_REGISTRY`, `skill_registry` ->
    `skill_registry`, `Skill_registry` -> `Skill_Registry`.
    """
    def _rep(m: re.Match) -> str:
        got = m.group(0)
        if got.isupper():
            return right.upper()
        if got.islower():
            return right.lower()
        if got[:1].isupper() and got[1:].islower():
            return right.capitalize()
        return right
    return re.sub(r"(?<=[A-Za-z0-9_])%s(?![A-Za-z0-9])" % re.escape(wrong),
                  _rep, text, flags=re.IGNORECASE)


def proof_ref_actions(conn: sqlite3.Connection, root: Path) -> list[dict]:
    """The SAME rewrite as `code_ref_actions`, but for `_proof_*` files.

    🔴 WHY THIS EXISTS (MEASURED 2026-09-28). `code_ref_actions` SKIPS `_proof_*`
    by design, and the reason is sound: a proof asserts a SPECIFIC spelling, so
    rewriting it can make the assertion tautological. But the consequence was
    never measured:

        STALE proofs (name a removed table/module): 284
        distinct dead names: 119

    The migration renamed 119 names in the LIVE system and left 284 proofs naming
    the old ones. Six of them were still RED after their live-write was isolated,
    and the reason was the stale name, not the write:

        _proof_capability_tag.py            AttributeError: db_schema has no attribute 'SKILL_registry_DDL'
        _proof_channel_env_status_5w1h.py   no such table: terminology_registry
        _proof_pinned_sessions.py           no such table: identity_registry
        _proof_playwright_workflow_scroll.py no such table: terminology_registry
        _proof_skill_factor.py              no such table: skill_factor_registry
        _proof_ticket.py                    KeyError: 'service'

    THE RULE AND THE EXEMPTION LIST ARE THE SAME OBJECTS as `code_ref_actions`
    uses (`_suffix_rows` and `DECLARATION_CARRIERS`), so the two paths cannot
    drift. The ONLY difference is the `_proof_` skip, which is removed here.

    A `DECLARATION_CARRIERS` file is STILL exempt: a proof that measures a
    REFUSAL must CONTAIN the thing it refuses, or the check becomes tautological.
    MEASURED 2026-09-28: a blanket rename rewrote `_proof_alias_must_be_terminology.py`
    into the CORRECT spelling and turned a GREEN check RED.

    A RECORD (`qc_evidence/`, `evidence/`) and a GENERATED file are still never
    rewritten (R-4(b)).
    """
    pairs: dict[str, str] = {}
    suffix_pairs: dict[str, str] = {}
    for r in _suffix_rows(conn):
        w = str(r["wrong"])
        c = str(r["correction"])
        if w and c:
            pairs[w] = c
            suffix_pairs[w] = c
    if not pairs:
        return []
    rx = re.compile(r"\b(" + "|".join(
        sorted((re.escape(k) for k in pairs), key=len, reverse=True)) + r")\b")
    seg_rx = (re.compile(r"(?<=[A-Za-z0-9_])(%s)(?![A-Za-z0-9])"
                         % "|".join(sorted((re.escape(k)
                                            for k in suffix_pairs),
                                           key=len, reverse=True)))
              if suffix_pairs else None)
    out: list[dict] = []
    for p in _iter_files(root, {"py", "js", "json", "sql"}):
        if not p.name.startswith("_proof_"):
            continue
        if _is_record_path(p) or _is_generated_path(p):
            continue
        # A DECLARATION CARRIER is exempt: it must NAME the wrong spelling.
        if declaration_carrier(root, str(p)) is not None:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        hits = sorted(set(rx.findall(txt)))
        if seg_rx:
            hits = sorted(set(hits) | set(seg_rx.findall(txt)))
        if not hits:
            continue
        first = hits[0]
        new_first = _corrected(first, [{"wrong": k, "correction": v,
                                        "kind": ("suffix" if k in suffix_pairs
                                                 else "typo")}
                                       for k, v in pairs.items()])
        out.append({
            "object": "file", "layer": "L17",
            "path": str(p.relative_to(root)),
            "name": first, "action": "REWRITE",
            "to": new_first,
            "names": hits,
            "why": "a PROOF reference to a renamed object (L17: proofs are a carrier too)",
            "cite_ref": "name_unify.py:proof_ref_actions",
            "class": "proof_ref",
        })
    return out


def _same_content(conn: sqlite3.Connection, a: str, b: str) -> bool:
    """Do two tables hold the SAME columns and the SAME rows? A MEASUREMENT.

    Used to decide whether a duplicate table carries any information the survivor
    lacks. A column-set difference or a row difference means it does NOT, and the
    caller REFUSES (a merge is a human decision). An unreadable table is `False`,
    so an unknown is never treated as "identical".

    🔴 A TIMESTAMP COLUMN IS RECREATION METADATA, NOT CONTENT. MEASURED
    2026-09-28: `derived_column_registry` (created 12:15) and
    `derived_column_registry` (created 03:15) hold the SAME 2 rows; the ONLY
    difference is `created_at`/`updated_at`, which record WHEN the duplicate was
    recreated. Comparing them would call a recreated copy "different data" and
    refuse a cleanup that loses nothing. So the comparison EXCLUDES the timestamp
    columns, and the excluded names are DECLARED here rather than inferred.
    """
    _TS = ("created_at", "updated_at")
    try:
        ca = [str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % a)]
        cb = [str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % b)]
        if ca != cb:
            return False
        cols = [c for c in ca if c not in _TS]
        if not cols:
            return False
        sel = ", ".join('"%s"' % c for c in cols)
        ra = sorted(tuple(r) for r in conn.execute("SELECT %s FROM %s" % (sel, a)))
        rb = sorted(tuple(r) for r in conn.execute("SELECT %s FROM %s" % (sel, b)))
        return ra == rb
    except sqlite3.Error:
        return False


def _rename_object(conn: sqlite3.Connection, a: dict) -> dict:
    """RENAME a DB object or column. A RENAME moves; it does not destroy.

    MEASURED PRECONDITION, and it is the repo's own rename lesson
    (`_unify_registry_naming.py:29`): `ALTER TABLE ... RENAME TO` is the ADDITIVE
    form — the data is not copied, so there is nothing to miscopy — and the COUNT
    is verified after each rename. A rename that changes a count is a data loss,
    so it RAISES rather than reporting success.

    REFUSES rather than guesses:
      * `NO_SUCH_OBJECT` — the object is gone (a concurrent writer, or a stale plan)
      * `DESTINATION_EXISTS` — the corrected name is already taken; renaming onto
        it would be a MERGE (two things, one name), which is a human decision
      * `UNSUPPORTED_OBJECT` — an INDEX cannot be renamed in place (SQLite has no
        `ALTER INDEX ... RENAME TO`), so it is REPORTED, never attempted
    """
    name = str(a.get("name") or "")
    new = str(a.get("to") or "")
    if not name or not new:
        return {"ok": False, "code": "NO_REPLACEMENT"}
    kind = str(a.get("object_type") or "")
    table = a.get("table")
    if kind == "column":
        if not table:
            return {"ok": False, "code": "NO_TABLE"}
        cols = {str(c[1]) for c in conn.execute("PRAGMA table_info(%s)" % table)}
        if name not in cols:
            return {"ok": False, "code": "NO_SUCH_OBJECT",
                    "reason": "%s.%s is absent" % (table, name)}
        if new in cols:
            return {"ok": False, "code": "DESTINATION_EXISTS",
                    "reason": "%s.%s already exists" % (table, new)}
        conn.execute('ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"'
                     % (table, name, new))
        return {"ok": True, "kind": "column", "table": table,
                "old": name, "new": new}
    if kind in ("table", "view"):
        live = conn.execute("SELECT type FROM sqlite_master WHERE name=?",
                            (name,)).fetchone()
        if not live:
            return {"ok": False, "code": "NO_SUCH_OBJECT",
                    "reason": "%s is absent" % name}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        (new,)).fetchone():
            # 🔴 AN EMPTY DUPLICATE IS DROPPED, NOT REFUSED. MEASURED 2026-09-28:
            # `skill_factor_registry` (0 rows) and `skill_factor_registry` (77 rows)
            # BOTH exist — the same concept under two names, which is exactly the
            # mis-understanding the ruling names. When the OLD table is EMPTY, the
            # duplicate carries no data, so it is DROPPED and the rename proceeds.
            # A NON-empty duplicate is still REFUSED (a merge is a human decision).
            old_rows = conn.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
            if old_rows == 0:
                conn.execute('DROP TABLE "%s"' % name)
                return {"ok": True, "kind": kind, "old": name, "new": new,
                        "rows": 0, "dropped_empty_duplicate": True}
            # 🔴 AN IDENTICAL DUPLICATE IS DROPPED TOO. MEASURED 2026-09-28:
            # `derived_column_registry` (2 rows, created 12:15) is a RECREATED copy
            # of `derived_column_registry` (2 rows, created 03:15) — the SAME rows,
            # the registry being the ORIGINAL. The duplicate carries no information
            # the survivor lacks, so it is DROPPED. The test is CONTENT, not a
            # guess: same column set AND the same rows on both sides.
            if _same_content(conn, name, new):
                conn.execute('DROP TABLE "%s"' % name)
                return {"ok": True, "kind": kind, "old": name, "new": new,
                        "rows": int(old_rows),
                        "dropped_identical_duplicate": True}
            return {"ok": False, "code": "DESTINATION_EXISTS",
                    "reason": ("%s already exists AND %s holds %d rows that "
                               "DIFFER — a MERGE, which is a human decision"
                               % (new, name, old_rows))}
        before = conn.execute('SELECT COUNT(*) FROM "%s"' % name).fetchone()[0]
        conn.execute('ALTER TABLE "%s" RENAME TO "%s"' % (name, new))
        after = conn.execute('SELECT COUNT(*) FROM "%s"' % new).fetchone()[0]
        if after != before:
            raise SystemExit("COUNT CHANGED on %s: %s -> %s"
                             % (name, before, after))
        return {"ok": True, "kind": kind, "old": name, "new": new,
                "rows": int(after)}
    if kind == "index":
        # 🔴 AN INDEX IS RENAMED ONLY WHEN ITS TABLE IS RENAMED. MEASURED
        # 2026-09-28: `idx_registry_fill_run_phase` is an index ON
        # `register_fill_run` — a table that is NOT renamed (its name starts with
        # `register`, so the `_register` SEGMENT rule does not match it). Renaming
        # the index would make its name disagree with its table. An index name
        # FOLLOWS its table, so the table decides.
        row = conn.execute("SELECT sql, tbl_name FROM sqlite_master "
                           "WHERE type='index' AND name=?", (name,)).fetchone()
        if not row:
            return {"ok": False, "code": "NO_SUCH_OBJECT", "reason": name}
        tbl = str(row["tbl_name"] or "")
        if tbl and conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name=? AND name LIKE "
                "'%_register'", (tbl,)).fetchone() is None:
            return {"ok": False, "code": "TABLE_NOT_RENAMED",
                    "reason": ("%s is an index on %s, which is NOT renamed, so "
                               "the index name must follow its table" % (name, tbl))}
        sql = str(row["sql"] or "")
        if not sql:
            # An AUTO-INDEX (`sqlite_autoindex_*`) has no SQL and cannot be
            # recreated; it is REPORTED, never attempted.
            return {"ok": False, "code": "UNSUPPORTED_OBJECT",
                    "reason": ("%s is an auto-index with no SQL; it cannot be "
                               "recreated" % name)}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        (new,)).fetchone():
            return {"ok": False, "code": "DESTINATION_EXISTS",
                    "reason": "%s already exists" % new}
        new_sql = re.sub(r"(?i)\bINDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?%s[`\"]?"
                         % re.escape(name), "INDEX %s" % new, sql, count=1)
        conn.execute("DROP INDEX %s" % name)
        conn.execute(new_sql)
        return {"ok": True, "kind": "index", "old": name, "new": new}
    return {"ok": False, "code": "UNSUPPORTED_OBJECT",
            "reason": ("SQLite cannot rename a %s in place; it must be dropped "
                       "and recreated, which is a separate decision" % (kind or "?"))}


def _rename_file(root: Path, a: dict) -> dict:
    """RENAME a file whose NAME carries the wrong token.

    REFUSES when the destination exists — an overwrite destroys a file, and two
    files for one name is two answers to one name. The CONTENT is not touched:
    a filename rename and a content rewrite are different carriers, and doing
    both from one action would hide which one changed.
    """
    rel = str(a.get("path") or "")
    src = root / rel
    if not src.exists():
        return {"ok": False, "code": "NO_SUCH_FILE", "reason": rel}
    # `to` is the corrected STEM (the caller substituted every typo in place), so
    # the EXTENSION is re-attached here — MEASURED 2026-09-28: the first version
    # used the whole filename, so a declared SUFFIX rule never matched (the name
    # ends with `.py`, not `_register`).
    new_stem = str(a.get("to") or "")
    if not new_stem or new_stem == src.stem:
        return {"ok": False, "code": "NO_CHANGE"}
    new_name = new_stem + src.suffix
    dst = src.with_name(new_name)
    if dst.exists():
        return {"ok": False, "code": "DESTINATION_EXISTS",
                "reason": "%s already exists" % new_name}
    src.rename(dst)
    return {"ok": True, "old": rel,
            "new": str(dst.relative_to(root))}


def apply(conn: sqlite3.Connection, root: Path, *, dry_run: bool = True,
          max_actions: int = 100000, proofs: bool = False) -> dict:
    """THE ONE WRITE DOOR. Turn `plan_writes` into writes, mechanically.

    R-1: a wrong name is RENAMED or DELETED, never aliased.
    R-2: no carrier keeps it — so the DB side removes the alias and retires the
         row, and the file side rewrites the token.

    SAFETY, all measured into the design:
      * `SAVEPOINT`, not `BEGIN` — SQLite aborts the OUTERMOST transaction, so a
        `rollback()` in a caller's transaction would lose THEIR work
        (`register_value_control` incident).
      * BOUNDED (`max_actions`) with a NAMED stop reason — an unbounded writer is
        this repo's recorded loop incident, not automation.
      * `dry_run` is the DEFAULT. A write needs `dry_run=False` explicitly.
      * It NEVER writes a file it has not classified NAME.

    REFUSES rather than guesses: if a replacement string is missing, the action
    is REPORTED (`NO_REPLACEMENT`) and skipped, never written as a blank.
    """
    # 🔴 THE ONE WRITE DOOR REFUSES TO OPEN ON THE LIVE ROOT. MEASURED
    # 2026-09-29, and it caused real damage: `_proof_name_unify.py` called
    # `apply(conn, BASE, dry_run=False)` with `BASE` = the LIVE repo, so running
    # a PROOF rewrote the repo — it renamed `_drop_ghost_register_tables.py`,
    # rewrote its `RECORDED_GHOSTS` tuple from `*_register` to `*_registry`, and
    # changed the `schema_migration_log.migration` key. The renamed script then
    # listed 16 LIVE tables (`terminology_registry` 2416 rows, `code_registry`
    # 431) as its drop set, with its twin check satisfied because a `_registry`
    # is its own "twin" — one run would have DROPPED LIVE DATA.
    #
    # A guard on ARGUMENTS is the right layer: the proof is meant to exercise the
    # door, and the door is the only thing that knows `dry_run=False` means
    # "write". `dry_run=True` is unaffected, so every planning/audit caller works
    # unchanged, and a caller that needs a real write passes a COPY.
    if not dry_run:
        try:
            resolved = Path(root).resolve()
            live = Path(__file__).resolve().parent
        except Exception:  # never let the guard itself be the failure
            resolved, live = None, None
        if resolved is not None and resolved == live:
            return {
                "ok": False, "dry_run": False, "applied": 0, "skipped": 0,
                "stop_reason": "REFUSED_LIVE_ROOT",
                "error": ("apply(dry_run=False) was asked to write the LIVE root "
                          "%s. The one write door never opens on the live tree: a "
                          "PROOF run would then rewrite source and data. Pass a "
                          "COPY of the root." % resolved),
                "actions": [], "files_changed": [],
            }
    import terminology_blacklist as bl
    plan = plan_writes(conn, root)
    # 🔴 `proofs=False` KEEPS THE DEFAULT BEHAVIOUR. MEASURED 2026-09-28: the
    # `_proof_*` skip is sound (a proof asserts a SPECIFIC spelling), so the
    # proof rewrite is OPT-IN. A caller that wants it passes `proofs=True`.
    if not proofs:
        plan["actions"] = [a for a in plan["actions"]
                           if a.get("class") != "proof_ref"]
    out = {"ok": True, "dry_run": bool(dry_run), "applied": 0, "skipped": 0,
           "refused": [], "files_changed": [], "cite": "name_unify.py:apply",
           "stop_reason": None}
    if len(plan["actions"]) > max_actions:
        out["stop_reason"] = "MAX_ACTIONS_REACHED"
        return out

    cur = conn.execute("SAVEPOINT name_unify_apply")
    try:
        # 🔴 THE ORDER IS THE PHASE ORDER, AND IT IS NOT COSMETIC. MEASURED
        # 2026-09-28: renaming the TABLE first made `terminology_alias` and
        # `terminology_registry` raise `no such table: terminology_registry`,
        # because those OWNER modules hardcode the table name. So the DB OBJECT
        # renames run LAST, after every carrier that reads through those modules.
        _ORDER = {"alias": 0, "term_key": 1, "registry_key": 2,
                  "function": 3, "file": 4, "file_name": 5, "db_object": 9}
        ordered = sorted(plan["actions"],
                         key=lambda x: _ORDER.get(x["object"], 5))
        for a in ordered:
            rep = str(a.get("to") or "")
            # A DELETE_ALIAS has NO replacement BY DESIGN — the alias is removed,
            # not rewritten. MEASURED 2026-09-28: the blanket `if not rep` check
            # refused all 5 alias deletions with NO_REPLACEMENT.
            if not rep and a["action"] != "DELETE_ALIAS":
                out["refused"].append({**a, "code": "NO_REPLACEMENT"})
                continue
            # 🔴 DRY RUN MUST NOT WRITE ANYTHING — MEASURED BUG IN MY FIRST
            # VERSION: `terminology_alias.remove_alias` calls `conn.commit()`
            # ITSELF, so a "dry run" that called it would REALLY delete the alias
            # and the SAVEPOINT rollback could not undo it. A dry run that writes
            # is worse than no dry run. So in dry run NO door is called.
            if dry_run:
                if a["object"] in ("file", "file_name", "db_object",
                                   "function", "registry_key"):
                    out["skipped"] += 1
                else:
                    out["would_apply"] = out.get("would_apply", 0) + 1
                continue
            if a["object"] == "function":
                # 🔴 RENAME_FUNCTION — a `def` whose NAME carries the wrong token.
                # The identifier is rewritten as a WHOLE TOKEN, so a call site
                # (`zz_enviornment_fn(`) is renamed with it and a longer name
                # (`zz_enviornment_fn_2`) is NOT.
                r = _rename_function(root, a)
                if r.get("ok"):
                    out["applied"] += 1
                    out.setdefault("functions_renamed", []).append(r)
                else:
                    out["refused"].append({**a, "code": r.get("code"),
                                           "reason": r.get("reason")})
            elif a["object"] == "registry_key":
                # 🔴 RENAME_KEY — a capability / channel / module key.
                r = _rename_key(conn, a)
                if r.get("ok"):
                    out["applied"] += 1
                    out.setdefault("keys_renamed", []).append(r)
                else:
                    out["refused"].append({**a, "code": r.get("code"),
                                           "reason": r.get("reason")})
            elif a["object"] == "db_object":
                # 🔴 RENAME_OBJECT — the carrier the human named. A RENAME moves;
                # it does not destroy, so the COUNT is verified before and after
                # (this repo's own rename lesson). A COLUMN rename uses
                # `ALTER TABLE ... RENAME COLUMN`, which SQLite supports since
                # 3.25; a TABLE/VIEW rename uses `ALTER TABLE ... RENAME TO`.
                r = _rename_object(conn, a)
                if r.get("ok"):
                    out["applied"] += 1
                    out.setdefault("objects_renamed", []).append(r)
                else:
                    out["refused"].append({**a, "code": r.get("code"),
                                           "reason": r.get("reason")})
            elif a["object"] == "file_name":
                # 🔴 RENAME_FILE — a file whose NAME carries the wrong token.
                # REFUSES when the destination exists: an overwrite would destroy
                # a file, and two files for one name is two answers to one name.
                r = _rename_file(root, a)
                if r.get("ok"):
                    out["applied"] += 1
                    out.setdefault("files_renamed", []).append(r)
                else:
                    out["refused"].append({**a, "code": r.get("code"),
                                           "reason": r.get("reason")})
            elif a["object"] == "alias":
                import terminology_alias as ta
                r = ta.remove_alias(conn, a["owner"], a["name"],
                                    cite_ref=str(a["cite_ref"]), commit=False)
                if r.get("ok"):
                    out["applied"] += 1
                else:
                    out["refused"].append({**a, "code": r.get("code")})
            elif a["object"] == "term_key":
                # 🔴 RENAME the term's KEY — CORRECTED 2026-09-28 BY THE RULING.
                # The old branch RETIRED the row (`is_active=0`), which leaves the
                # wrong name as a live stored name (R-2: a wrong name that still
                # resolves is still a wrong name). The ruling is that the NAME is
                # unified, so the key MOVES and the old key becomes an ALIAS (a
                # tombstone for a name that is no longer current).
                #
                # 🔴 MEASURED BLOCKER 2026-09-28: `terminology_registry.py`
                # hardcodes `terminology_registry` in ~30 places, so once the
                # TABLE is renamed its OWNER module cannot read it. The refusal is
                # REPORTED with that reason, never swallowed as a generic fault.
                import terminology_registry as tr
                try:
                    r = tr.update_term(conn, int(a["term_id"]),
                                       term_key=str(a["to"]),
                                       cite=str(a.get("cite_ref") or ""),
                                       commit=False)
                except sqlite3.Error as e:
                    # 🔴 A UNIQUE FAILURE IS A COLLISION, NOT A DEPENDENCY FAULT.
                    # MEASURED 2026-09-28: `update_term` RAISES IntegrityError
                    # (UNIQUE) instead of returning `KEY_COLLISION`, because its
                    # own collision check queries the OLD table name once the
                    # table has been renamed. A collision is a MERGE, so it is
                    # routed there rather than reported as a broken dependency.
                    if "UNIQUE" in str(e).upper():
                        m = _merge_term_collision(conn, a)
                        if m.get("ok"):
                            out["applied"] += 1
                            out.setdefault("terms_merged", []).append(m)
                        else:
                            out["refused"].append({**a, "code": m.get("code"),
                                                   "reason": m.get("reason")})
                        continue
                    out["refused"].append({
                        **a, "code": "DEPENDENCY_CANNOT_RESOLVE",
                        "reason": ("the term table was renamed and its OWNER "
                                   "module still hardcodes the old name: %s" % e)})
                    continue
                if r.get("ok") or r.get("reason") == "NO_CHANGE":
                    out["applied"] += 1
                elif r.get("reason") == "KEY_COLLISION":
                    # 🔴 A COLLISION IS A MERGE, AND THE RULING DECIDES THE
                    # DIRECTION. MEASURED 2026-09-28: `terminology_registry`(68)
                    # collides with `terminology_registry`(1377). The human ruled
                    # the unified name is `_registry`, so the SURVIVOR is the row
                    # whose key ALREADY IS the unified name; the other row is
                    # RETIRED and its key becomes an ALIAS of the survivor (a
                    # tombstone for a name that is no longer current).
                    m = _merge_term_collision(conn, a)
                    if m.get("ok"):
                        out["applied"] += 1
                        out.setdefault("terms_merged", []).append(m)
                    else:
                        out["refused"].append({**a, "code": m.get("code"),
                                               "reason": m.get("reason")})
                else:
                    out["refused"].append({**a, "code": r.get("reason")})
            elif a["object"] == "file":
                p = root / a["path"]
                try:
                    txt = p.read_text(encoding="utf-8", errors="surrogateescape")
                except Exception as e:
                    out["refused"].append({**a, "code": "UNREADABLE",
                                           "reason": str(e)})
                    continue
                new = token_re(a["name"]).sub(rep, txt)
                # 🔴 THE DECLARED SUFFIX RULE IS APPLIED AS A SEGMENT TOO.
                # MEASURED 2026-09-28: `token_re` alone left `code_registry` in
                # `db_schema.py` un-rewritten, so `ensure_schema` RECREATED the
                # old tables. The rule is read from the register, never typed.
                #
                # 🔴 AND THE CASE IS PRESERVED. MEASURED 2026-09-28: the first
                # version used `re.IGNORECASE` with a lowercase replacement, so
                # `SKILL_REGISTRY_DDL` became `SKILL_registry_DDL` — a name that
                # does not exist. 66 such tokens were produced across the proofs.
                # A substitution that changes the CASE of a name invents a third
                # name, which is worse than leaving the old one.
                for sr in _suffix_rows(conn):
                    w = str(sr["wrong"])
                    c = str(sr["correction"])
                    if w and c:
                        new = _sub_segment_preserving_case(new, w, c)
                if new == txt:
                    out["skipped"] += 1
                    continue
                p.write_text(new, encoding="utf-8", errors="surrogateescape")
                out["applied"] += 1
                out["files_changed"].append(a["path"])
        if not dry_run:
            _release(conn, out)
            conn.commit()
        else:
            conn.execute("ROLLBACK TO name_unify_apply")
            conn.execute("RELEASE name_unify_apply")
            out["stop_reason"] = "DRY_RUN"
    except Exception as e:
        # 🔴 MEASURED DEFECT IN MY FIRST VERSION, FIXED HERE: the first apply
        # reported `APPLY_FAULT "no such savepoint"` while HAVING COMPLETED every
        # file rewrite and DB write. A tool that reports FAILURE after SUCCEEDING
        # is worse than one that crashes: the reader re-runs it, or believes the
        # work was not done and does it again by hand.
        #
        # The cause was a door that committed under the caller. That is fixed
        # (`remove_alias(commit=False)`), but the REPORTING must also be honest:
        # the savepoint is only rolled back if it STILL EXISTS, and the number of
        # actions already applied travels with the error.
        rolled = False
        try:
            conn.execute("ROLLBACK TO name_unify_apply")
            rolled = True
        except sqlite3.Error:
            pass
        _release(conn, out)
        return {"ok": False, "code": "APPLY_FAULT", "reason": str(e),
                "applied_before_fault": out["applied"],
                "files_changed_before_fault": out["files_changed"],
                "rolled_back": rolled,
                "savepoint_gone": not rolled,
                # 🔴 A FAULT IS A NAMED STOP REASON TOO. MEASURED 2026-09-28: the
                # fault path returned NO `stop_reason`, so a caller that reads the
                # stop reason (the proof, the CLI) got `None` and could not tell a
                # crash from a clean run. A fault must be as readable as a success.
                "stop_reason": "FAULT_%s" % str(e).split(":")[0].strip().upper()
                .replace(" ", "_"),
                "cite": "name_unify.py:apply"}
    if out["stop_reason"] is None:
        out["stop_reason"] = ("ALL_NAMES_UNIFIED" if not out["refused"]
                              else "BLOCKED_REFUSED_ACTIONS")
    out["remaining"] = len(plan_writes(conn, root)["actions"])
    return out


def audit(conn: sqlite3.Connection, root: Path) -> dict:
    """THE WHOLE POPULATION, as NUMBERS plus every named occurrence.

    Returns `unclassified` explicitly, because an occurrence in no bucket is a
    FAILURE (QC-01), not a silent pass.
    """
    db_layers = audit_db_layers(conn)
    file_layers = audit_file_layers(root, conn)
    layers = {**db_layers, **file_layers}
    n_name = n_record = n_quote = n_decl = n_gen = n_fault = 0
    for lid, l in layers.items():
        if not l.get("ok"):
            n_fault += 1
            continue
        for h in l.get("hits", []):
            cls = h.get("class")
            if cls == "RECORD":
                n_record += 1
            elif cls == "QUOTE":
                n_quote += 1
            elif cls == "DECLARATION":
                n_decl += 1
            elif cls == "GENERATED":
                n_gen += 1
            elif cls == "FAULT" or h.get("code") == "FAULT":
                n_fault += 1
            else:
                n_name += 1
    return {
        "ok": True,
        "layers": {lid: {k: v for k, v in l.items() if k != "hits"}
                   for lid, l in layers.items()},
        "detail": layers,
        "n_name": n_name,
        "n_record": n_record,
        "n_quote": n_quote,
        "n_declaration": n_decl,
        "n_generated": n_gen,
        "n_fault": n_fault,
        "unclassified": 0 if n_fault == 0 else n_fault,
        "cite": CITE,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="audit + unify every named layer")
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    ap.add_argument("--root", default=str(BASE))
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--classify", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.classify:
            print(json.dumps(classify(conn, a.classify), indent=2,
                             ensure_ascii=False))
        if a.plan:
            print(json.dumps(plan_writes(conn, Path(a.root)), indent=2,
                             ensure_ascii=False))
        if a.apply:
            res = apply(conn, Path(a.root), dry_run=bool(a.dry_run or not a.apply))
            # R-1 DELETE of a wrong name whose CORRECT name already exists.
            if a.apply and not a.dry_run:
                res["purge"] = purge_wrong_duplicate(
                    conn, cite_ref="qc_evidence/plan_UNIFIED.NAME.NO.TYPO.md:R-2",
                    dry_run=False, commit=True)
                # `remaining` is recomputed AFTER the purge, or it reports the
                # count the purge just changed.
                res["remaining"] = len(plan_writes(conn, Path(a.root))["actions"])
            print(json.dumps(res, indent=2, ensure_ascii=False))
        if a.audit:
            rep = audit(conn, Path(a.root))
            slim = {"ok": rep["ok"], "layers": rep["layers"],
                    "n_name": rep["n_name"], "n_record": rep["n_record"],
                    "n_quote": rep["n_quote"],
                    "n_declaration": rep["n_declaration"],
                    "n_generated": rep["n_generated"],
                    "n_fault": rep["n_fault"],
                    "unclassified": rep["unclassified"], "cite": rep["cite"]}
            print(json.dumps(slim, indent=2, ensure_ascii=False))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())