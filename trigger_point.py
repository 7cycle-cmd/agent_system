"""trigger_point.py — TURN "a worker decides" INTO A DEFINED TRIGGER POINT.

THE USER'S RULING (2026-09-24)
------------------------------
Three checklist items were left as REPORT — "a worker decides each one". The user
rejected that shape:

    "your question is need to have definition for which to let this definition with
     this value need to have what -> trigger point"

That is correct, and it is the stronger design. A judgement never converges: it
gives a different answer depending on who looks, and it cannot be automated. A
DEFINITION states what VALUE the thing must have, which makes the action FOLLOW
from measured state — the same input always gives the same verdict.

So each of the three items now has a definition, and NOTHING is chosen by hand:

C1b  the QUALIFIER of a colliding term is `parent_term.term_key`
     MEASURED: term 39 `route` (parent 37 = `connection`) and term 40 `route`
     (parent 38 = `http_endpoint`) -> `connection.route` / `http_endpoint.route`.
     The register ALREADY STORES the structure, so the qualifier is DERIVED.

C6   a `type='table' AND name=?` check is correct IFF the file also contains a DDL
     verb. MEASURED: 21 files have DDL (KEEP table-only — a VIEW must not pass a
     DDL gate) and 32 do not (MUST accept any readable kind).

C7   a compatibility view may be dropped IFF no file reads it. MEASURED readers:
     `llm_100_run` 20, `v_skill_contract` 2. Order: migrate readers, THEN drop.

WHY C6/C1b ARE NOW `auto` AND C7 STAYS `report`
----------------------------------------------
C1b and C6 became drivable the moment their trigger was defined. C7 is DIFFERENT in
kind: its definition FORBIDS the drop while readers exist, so the honest status is
"blocked by a measured count", not "somebody's opinion". Reporting a person's
preference as a gate would hide a number.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "agent.db"

# The defect pattern, ONE compiled regex so the audit and the codemod agree.
# KEPT ON A SINGLE LINE DELIBERATELY: when the pattern was split across lines, its
# own CONTINUATION line (`r"type\s*=\s*['\"]table['\"]\s+AND\s+name\s*=",
# re.IGNORECASE)`) contained the literal text and was counted as a defect site, so
# this module reported ITSELF. One line keeps the definition self-contained.
TABLE_ONLY_GUARD = re.compile(r"type\s*=\s*['\"]table['\"]\s+AND\s+name\s*=", re.IGNORECASE)

# A DDL verb means the guard protects DDL, so a VIEW must NOT pass it.
DDL_VERB = re.compile(r"\b(ALTER\s+TABLE|CREATE\s+TABLE|DROP\s+TABLE|RENAME\s+TO|ADD\s+COLUMN)\b", re.IGNORECASE)

# A READ of an old name. Only a real SQL read counts — a mention in prose does not.
READ_OF = ("FROM", "INTO", "JOIN", "UPDATE")

MARKER = "# object_door: kind-agnostic by definition (no DDL in this file)"


def _is_a_site(line: str) -> bool:
    """Is this line a REAL defect site, or a DEFINITION of the pattern?

    MEASURED FALSE POSITIVES, both of them mine:
      * `trigger_point.py` was reported as a defect because it DEFINES the regex.
        A pattern's own definition is not a use of it.
      * Its MODULE DOCSTRING describes the pattern in prose, and that prose was
        counted too. Prose ABOUT a defect is not a defect.

    So a line is a site only when it is CODE (see `_code_lines`, which drops both
    comments and docstrings via AST) and does not define the pattern.
    """
    if line.strip().startswith("#"):
        return False
    if "TABLE_ONLY_GUARD" in line or "re.compile" in line:
        return False
    return bool(TABLE_ONLY_GUARD.search(line))


def _docstring_lines(text: str) -> set[int]:
    """Line numbers occupied by a MODULE, CLASS or FUNCTION docstring.

    Uses the AST rather than a quote heuristic, because the defect prose here is
    written with BACKTICKS and mixed quotes, which a naive scan mis-parses.
    """
    import ast
    out: set[int] = set()
    try:
        tree = ast.parse(text)
    except Exception:
        return out
    nodes = [tree] + [n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef))]
    for node in nodes:
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            for ln in range(first.lineno, getattr(first, "end_lineno", first.lineno) + 1):
                out.add(ln)
    return out


def _code_lines(text: str) -> set[int]:
    """Line numbers that are CODE: not a comment and not a docstring."""
    doc = _docstring_lines(text)
    return {i for i, l in enumerate(text.splitlines(), 1)
            if i not in doc and not l.strip().startswith("#")}


# ===========================================================================
# C1b — the qualifier is DERIVED from the stored structure
# ===========================================================================

def qualify_collisions(conn: sqlite3.Connection) -> dict[str, Any]:
    """DERIVE the qualifier of each colliding term from its PARENT.

    The qualifier is `parent_term.term_key`, so the answer is a fact the register
    already holds. Nothing is chosen, and the same input always gives the same
    answer.

    A colliding term whose `parent_term_id` is NULL cannot be qualified from the
    structure. That case is REPORTED with the NAMED reason
    `NO_PARENT_TO_QUALIFY_WITH` — it is not quietly given an invented prefix.

    ------------------------------------------------------------------
    WIDENED 2026-09-26 — a collision is now CANONICAL, and there are TWO KINDS.

    MEASURED DEFECT (the human: "terminontoloty register can totaly KO that!!! fuck
    ... fix it now!"). The rule used to be

        would_be_red_if: "a term_key shared by >= 2 ACTIVE terms"

    and the code grouped by the EXACT string, so two SPELLINGS of one concept
      (`5w1h` vs `derive_5w1h`) never grouped and were never a collision.
MEASURED: `'derive_5w1h' in colliding keys` -> False.

    THE FIX widens the group key to `terminology_registry.canonical_term_key`,
    which follows `alias_list`.

    AND THAT WIDENING EXPOSED TWO KINDS, which my first version CONFLATED — a
    category error that turned C1b red with `NO_PARENT_TO_QUALIFY_WITH` four times:

      (a) ONE WORD, TWO MEANINGS  -> `route` twice, parents `connection` and
          `http_endpoint`. The word alone is ambiguous, so it NEEDS a parent to
          qualify: `connection.route` / `http_endpoint.route`.
      (b) ONE CONCEPT, MANY SPELLINGS -> the `5w1h` family. There is no ambiguity
          and no second thing: an ALIAS declaration already resolves it. Demanding
          a parent of (b) is asking for a qualifier a family by definition does not
          have.

    So the DECLARATION decides the kind, not the count:
      * every member shares one canonical key AND the spellings are DECLARED as
        aliases -> `alias_families` (RESOLVED, no parent needed);
      * a canonical key held by >= 2 terms that are NOT aliases -> `collisions`
        (needs a parent qualifier).
    Nothing is merged by guess: an undeclared second spelling stays its own
    canonical key and appears as an ordinary term.
    """
    import terminology_registry as tr
    rows = [dict(r) for r in conn.execute(
        "SELECT t.term_id, t.term_key, t.parent_term_id, t.cite_ref, t.alias_list, "
        "       p.term_key AS parent_key "
        "FROM terminology_registry t "
        "LEFT JOIN terminology_registry p ON p.term_id = t.parent_term_id "
        "WHERE t.is_active = 1")]

    alias_problems = tr.alias_problems(conn)
    idx = tr.alias_index(conn)

    # GROUP BY CANONICAL KEY, not by the raw string. A name that is not aliased is
    # its own canonical key, so an unaliased population behaves exactly as before.
    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        try:
            canon = tr.canonical_term_key(conn, str(r["term_key"]))
        except Exception:  # a cycle: report it rather than guess a key
            canon = str(r["term_key"])
        by_key.setdefault(canon, []).append(r)

    collisions: list[dict[str, Any]] = []
    collisions_unqualifiable: list[dict[str, Any]] = []
    alias_families: list[dict[str, Any]] = []
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        spellings = sorted(str(x["term_key"]) for x in group)
        distinct = sorted(set(spellings))
        # KIND (b) — AN ALIAS FAMILY: >= 2 DISTINCT spellings, and every spelling
        # that is not the canonical key is DECLARED as an alias of it.
        #
        # MEASURED DEFECT in my FIRST version, and it turned `route` into a false
        # alias family: I tested `sp == key or idx.get(sp) == key`, and for two
        # rows both named `route` each `sp == key` is TRUE, so the group looked
        # fully declared. The `distinct` count is what separates the two kinds:
        # a family needs >= 2 DIFFERENT spellings, while a collision is the SAME
        # spelling held by >= 2 terms.
        if len(distinct) >= 2 and all(
                sp == key or idx.get(sp) == key for sp in distinct):
            alias_families.append({
                "canonical": key,
                "spellings": distinct,
                "aliases": [sp for sp in distinct if sp != key],
                "term_ids": sorted(int(x["term_id"]) for x in group),
                "resolution": "ALIAS_DECLARED",
                "cite": [str(x["cite_ref"]) for x in group],
            })
            continue
        # KIND (a) — A GENUINE COLLISION: the SAME spelling held by >= 2 terms that
        # are NOT aliases. It needs a parent qualifier.
        for g in group:
            if g["parent_key"]:
                collisions.append({
                    "term_id": g["term_id"], "term_key": key,
                    "canonical": key,
                    "spellings": spellings,
                    "parent_term_id": g["parent_term_id"],
                    "parent_key": str(g["parent_key"]),
                    "qualified": "%s.%s" % (g["parent_key"], key),
                    "derivation": ("parent_term.term_key of term %s"
                                   % g["parent_term_id"]),
                    "cite": g["cite_ref"],
                })
            else:
                collisions_unqualifiable.append({
                    "term_id": g["term_id"], "term_key": key,
                    "canonical": key,
                    "spellings": spellings,
                    "reason": "NO_PARENT_TO_QUALIFY_WITH",
                    "cite": g["cite_ref"],
                })

    # RED when a genuine collision has no parent to qualify it. An ALIAS FAMILY is
    # NOT red: its resolution is the declaration, and it is reported separately.
    ok = not collisions_unqualifiable
    return {"ok": ok,
            "derivable": collisions,
            "unqualifiable": collisions_unqualifiable,
            "collisions": collisions,
            "conflated_unqualifiable": collisions_unqualifiable,
            "alias_families": alias_families,
            "alias_family_count": len(alias_families),
            "count": len(collisions),
            "alias_problems": alias_problems,
            "alias_problem_count": len(alias_problems),
            "would_be_red_if": ("a CANONICAL term_key held by >= 2 ACTIVE terms "
                                "that are NOT declared as aliases"),
            "definition": ("TWO KINDS: (a) one word with two MEANINGS needs a "
                           "PARENT — the qualifier IS parent_term.term_key, so "
                           "'{parent_key}.{canonical}'; (b) one concept with many "
                           "SPELLINGS needs an ALIAS declaration, and is REPORTED "
                           "as an alias_family, not demanded a parent")}


# ===========================================================================
# C6 — the trigger is whether the file does DDL
# ===========================================================================

def _file_texts(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.glob("*.py")):
        try:
            out[p.name] = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
    return out


def classify_readers(root: Path | None = None) -> dict[str, Any]:
    """Split every file with the defect pattern by WHETHER IT DOES DDL.

    The definition: the table-only check is CORRECT iff the file also contains a
    DDL verb. So the file's own content decides its class — not an opinion about
    the file.
    """
    root = root or BASE
    ddl_files: list[dict[str, Any]] = []
    migrate: list[dict[str, Any]] = []
    for name, text in _file_texts(root).items():
        code = _code_lines(text)
        # ONLY CODE counts. A docstring that DESCRIBES the pattern, or a comment
        # that explains it, is not a use of it (both were measured false positives).
        body = "\n".join(l for i, l in enumerate(text.splitlines(), 1)
                          if i in code)
        sites = [i for i, l in enumerate(text.splitlines(), 1)
                 if i in code and _is_a_site(l)]
        if not sites:
            continue
        if DDL_VERB.search(body):
            ddl_files.append({"file": name, "lines": sites,
                              "class": "KEEP_TABLE_ONLY",
                              "why": "the file performs DDL, so a VIEW must not pass"})
        else:
            migrate.append({"file": name, "lines": sites,
                            "class": "MIGRATE_TO_NAME_ONLY",
                            "why": "no DDL in the file, so the check's only subject "
                                   "is existence"})
    return {"ok": True, "ddl_files": ddl_files, "migrate": migrate,
            "ddl_count": len(ddl_files), "migrate_count": len(migrate),
            "definition": ("table-only is correct IFF the file contains a DDL verb"),
            "would_be_red_if": "a non-DDL file still decides existence by kind"}


def migrate_readers(*, apply: bool = False, root: Path | None = None) -> dict[str, Any]:
    """Rewrite `type='table'` -> `type IN ('table','view')` in the ELIGIBLE files.

    IDEMPOTENT: a file already carrying the marker is skipped, so a second run
    changes nothing. A DDL file is NEVER touched — that is the definition, not a
    preference.
    """
    root = root or BASE
    cls = classify_readers(root)
    changed: list[str] = []
    skipped: list[str] = []
    for item in cls["migrate"]:
        path = root / item["file"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped.append(item["file"])
            continue
        if MARKER in text:
            skipped.append(item["file"])
            continue
        new_lines = []
        touched = 0
        code = _code_lines(text)
        for idx, line in enumerate(text.splitlines(), 1):
            if idx in code and _is_a_site(line):
                new_line = TABLE_ONLY_GUARD.sub(
                    "type IN ('table','view') AND name=", line)
                new_lines.append(new_line)
                touched += 1
            else:
                new_lines.append(line)
        if not touched:
            skipped.append(item["file"])
            continue
        new_text = "\n".join(new_lines)
        if not new_text.endswith("\n"):
            new_text += "\n"
        # The marker goes in a COMMENT so the discipline is visible at the site.
        new_text = new_text.rstrip("\n") + "\n\n" + MARKER + "\n"
        if apply:
            path.write_text(new_text, encoding="utf-8")
        changed.append(item["file"])
    return {"ok": True, "applied": bool(apply), "changed": changed,
            "skipped": skipped, "eligible": cls["migrate_count"],
            "ddl_untouched": [x["file"] for x in cls["ddl_files"]],
            "would_be_red_if": "a non-DDL file still decides existence by kind",
            "idempotent": (not changed) if not apply else None}


# ===========================================================================
# C7 — the drop trigger is readers == 0
# ===========================================================================

def _readers_of(name: str, root: Path | None = None) -> list[str]:
    root = root or BASE
    pat = re.compile(r"\b(%s)\s+%s\b" % ("|".join(READ_OF), re.escape(name)))
    hits: list[str] = []
    for fname, text in _file_texts(root).items():
        for line in text.splitlines():
            if line.strip().startswith("#"):
                continue
            if pat.search(line):
                hits.append(fname)
                break
    return sorted(hits)


def drop_gate(conn: sqlite3.Connection,
              root: Path | None = None) -> dict[str, Any]:
    """Can a compatibility view be dropped? The DEFINITION answers, with a COUNT.

    A view may be dropped IFF no file reads it. While the reader count is > 0 the
    verdict is `blocked` and the COUNT is reported — the gate never says "a worker
    should look at this", because a count is a fact and a preference is not.
    """
    views = [str(r["name"]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view'")]
    out: list[dict[str, Any]] = []
    for v in sorted(views):
        readers = _readers_of(v, root)
        out.append({
            "view": v, "readers": len(readers), "reader_files": readers,
            "verdict": "allowed" if not readers else "blocked",
            "reason": ("no file reads it" if not readers
                       else "READERS_EXIST: migrate them first, then drop"),
        })
    blocked = [x for x in out if x["verdict"] == "blocked"]
    return {"ok": True, "views": out, "blocked": blocked,
            "droppable": [x["view"] for x in out if x["verdict"] == "allowed"],
            "definition": "a view may be dropped IFF no file reads it",
            "trigger": "reader count == 0",
            "would_be_red_if": "an old name is still a live object"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--qualify", action="store_true",
                    help="C1b: derive the qualifier of every colliding term")
    ap.add_argument("--reader-classes", action="store_true",
                    help="C6: split the guard files by whether they do DDL")
    ap.add_argument("--migrate", action="store_true",
                    help="C6: rewrite the eligible files")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--drop-gate", action="store_true",
                    help="C7: can each compatibility view be dropped?")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if a.qualify:
            out = qualify_collisions(conn)
        elif a.migrate:
            out = migrate_readers(apply=bool(a.apply))
        elif a.drop_gate:
            out = drop_gate(conn)
        else:
            out = classify_readers()
    finally:
        conn.close()

    if a.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    if a.qualify:
        print("definition: %s" % out["definition"])
        for d in out["derivable"]:
            print("   term %-3s %-10r -> %-26r  (%s)"
                  % (d["term_id"], d["term_key"], d["qualified"], d["derivation"]))
        for u in out["unqualifiable"]:
            print("   term %-3s %-10r -> REPORT: %s"
                  % (u["term_id"], u["term_key"], u["reason"]))
        print("   RED IF: %s" % out["would_be_red_if"])
    elif a.migrate:
        print("eligible : %d" % out["eligible"])
        print("changed  : %s" % (out["changed"] or "none"))
        print("skipped  : %s" % (out["skipped"] or "none"))
        print("DDL files left alone: %s" % (out["ddl_untouched"] or "none"))
    elif a.drop_gate:
        print("definition: %s   trigger: %s" % (out["definition"], out["trigger"]))
        for v in out["views"]:
            print("   %-18s readers=%-3d %s"
                  % (v["view"], v["readers"], v["verdict"]))
            if v["readers"]:
                print("        %s" % v["reason"])
    else:
        print("DDL files KEEP table-only : %d" % out["ddl_count"])
        for d in out["ddl_files"]:
            print("   %-40s %s" % (d["file"], d["why"]))
        print("non-DDL files MIGRATE     : %d" % out["migrate_count"])
        for m in out["migrate"][:40]:
            print("   %-40s lines=%s" % (m["file"], m["lines"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
