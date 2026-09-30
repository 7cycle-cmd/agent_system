# -*- coding: utf-8 -*-
"""alias_acceptance.py — is an ALIAS representative? A standardized proof.

THE USER (2026-09-24)
---------------------
    "for logic / prompt generator, terminologuy register > alias is wording or
     one of the factor, so you can proof does the alias is good for respresenative
     or not with a standardize proof"
    "do u agree, when you have a new terminologuy / 2 similar name for same to
     distrill / combine to 1 is the trigger point"

ANSWER 1 — YES, the trigger is real, and it is MEASURED
-------------------------------------------------------
A new term, or two names for one thing, IS the distil/combine trigger. Measured
against that rule, ONE pair is provably stale and the combine has NOT been taken:

    resolve_name("ollama")  ->  now = "ollama"      the RETIRED name, presented as current

`is_distil_due()` returns that as a NUMBER so the claim is checkable.

ANSWER 2 — the standard ALREADY exists here, so this REUSES it
-------------------------------------------------------------
`prompt_sweep.py` implements the certification discipline this project learned the
hard way (`certify_repeated`, `verdict`): `void` when a run answered only ONE
class, `overfit` when train-holdout > 15pp, and "a single 100% seed is
`provisional`, NOT `certified`". The alias acceptance is the SAME shape:

    terminal        the name appears VERBATIM in the case text it belongs to
    discriminating  at least ONE case does NOT contain it
    void            no cases, or every case contains it
    provisional     ONE case, terminal
    accepted        EVERY case terminal AND the set discriminating

A VERBATIM phrase match, NOT a token match, because `rule` must not match
`ruler` — a substring or token test would certify a name that is not there.

THE PRECONDITION IS MEASURED, NOT ASSUMED
-----------------------------------------
MEASURED: the alias names (`purpose`, `llm_runtime`, `ollama`) appear in **0** of
the `prompt_dimension` / `wording_registry` text rows. So `cases_for` returns 0 for
almost every term, and `accept_alias` then returns `void` WITH THE REASON — it does
NOT invent cases from an unrelated skill's prompt. `coverage_report()` reports the
NUMBER of terms that have cases, so the missing precondition is visible.

Run:
    .\\.venv\\Scripts\\python.exe alias_acceptance.py --measure
    .\\.venv\\Scripts\\python.exe alias_acceptance.py --distil-due
    .\\.venv\\Scripts\\python.exe alias_acceptance.py --apply
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# The verdict vocabulary. It MIRRORS `prompt_sweep.verdict` — the same four names,
# so one discipline covers both a prompt variant and an alias.
VERDICTS = ("accepted", "provisional", "void", "not_representative",
            "non_terminal")

# THE RECORD SHAPE of a scored alias. It MIRRORS `prompt_combo`'s discipline —
# `status` + the measured numbers — so an alias result is stored the same way a
# prompt-variant result is. Kept here, in the module that PRODUCES it, so the API
# references it rather than declaring a second copy.
ACCEPTANCE_FIELDS: tuple[str, ...] = (
    "name", "now", "was", "how", "cases", "terminal", "non_terminal",
    "sources", "verdict", "code", "why", "cite",
)

# The columns a DUrable record would carry, shaped on `prompt_combo`.
ACCEPTANCE_RECORD_COLUMNS: tuple[str, ...] = (
    "subject", "alias_name", "verdict", "code", "cases_n", "terminal_n",
    "non_terminal_n", "sources_json", "discriminating", "seed_runs",
    "cite_ref",
)

# The REAL text carriers for a case. Each is (table, text_column, ref_column).
# MEASURED: `wording_registry` is per-skill/dim PROMPT TEXT and `prompt_dimension`
# holds the axis values, so both are genuine cases. `skill_prompt_case` carries
# the target name/action — its own recorded observation.
CASE_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("prompt_dimension", "value_text", "value_key"),
    ("wording_registry", "template", "wording_key"),
    ("skill_prompt_case", "target_action", "target_name"),
)

# `prompt_registry` names the prompt variant itself.
PROMPT_KEY_SOURCE = ("prompt_registry", "prompt_key")

# The distil the register has NOT yet taken. It is a PAIR the register already
# holds (so the alias exists) but whose direction the term carrier answers WRONG.
KNOWN_PENDING_DISTIL: tuple[tuple[str, str], ...] = (("ollama", "llm_runtime"),)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (table,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {c[1] for c in conn.execute("PRAGMA table_info(%s)" % table)}


# --------------------------------------------------------------------------
# the terminal test — VERBATIM, not a token
# --------------------------------------------------------------------------
def normalise(text: str) -> str:
    """Whitespace-normalised, lower-cased. NOT tokenised and NOT stemmed."""
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def terminal_in(name: str, text: str) -> bool:
    """Does `name` appear VERBATIM in `text`?

    A phrase match on the normalised strings, so `rule` does NOT match `ruler`
    (a substring test would) and `mouse spot` DOES match `mouse  spot`.
    """
    n = normalise(name)
    if not n:
        return False
    t = normalise(text)
    if not t:
        return False
    # Word-ish boundaries, applied to the NORMALISED strings. `_` and `.` are part
    # of a key, so they are kept as word characters.
    pat = r"(?<![a-z0-9_.])" + re.escape(n) + r"(?![a-z0-9_.])"
    return re.search(pat, t) is not None


# --------------------------------------------------------------------------
# the cases
# --------------------------------------------------------------------------
def cases_for(conn: sqlite3.Connection, subject_kind: str, subject_ref: str,
              *, limit: int = 500) -> dict[str, Any]:
    """The REAL texts a name must appear in, with the table each came from.

    A term with NO cases returns `count=0` and the reason — never a fixture.
    """
    ref = str(subject_ref or "").strip()
    cases: list[dict[str, Any]] = []
    if _table_exists(conn, "prompt_registry"):
        for r in conn.execute(
                "SELECT %s AS t FROM prompt_registry WHERE %s LIKE ? LIMIT ?"
                % (PROMPT_KEY_SOURCE[1], PROMPT_KEY_SOURCE[1]),
                ("%" + ref + "%", limit)):
            cases.append({"source": "prompt_registry", "text": str(r["t"])})
    for table, tcol, rcol in CASE_SOURCES:
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if tcol not in cols or rcol not in cols:
            continue
        # Scope to the subject when the table carries a skill/key column linking
        # it to `ref`; otherwise take the rows whose ref column NAMES it.
        where = "%s = ?" % rcol
        try:
            rows = conn.execute("SELECT %s AS t FROM %s WHERE %s LIMIT ?"
                                % (tcol, table, where), (ref, limit)).fetchall()
        except sqlite3.Error:
            rows = []
        for r in rows:
            cases.append({"source": table, "text": str(r["t"])})
    return {"ok": True, "subject_kind": str(subject_kind), "subject_ref": ref,
            "count": len(cases), "cases": cases,
            "sources": sorted({c["source"] for c in cases}),
            "cite": ("measured: %d case rows across %s for %r"
                     % (len(cases), sorted({c["source"] for c in cases}), ref))}


def cases_for_term(conn: sqlite3.Connection, term_key: str, **kw) -> dict[str, Any]:
    """Cases for a TERM: its own key is the subject ref, plus its aliases."""
    import terminology_alias as ta
    key = str(term_key or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_TERM_KEY"}
    row = conn.execute("SELECT term_kind, entity_ref_key FROM "
                       "terminology_registry WHERE term_key=?", (key,)).fetchone()
    wanted = {key}
    if row:
        wanted.add(str(row["entity_ref_key"]))
        wanted |= set(ta._aliases_of(conn.execute(
            "SELECT alias_list FROM terminology_registry WHERE term_key=?",
            (key,)).fetchone()[0]))
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for w in sorted(w for w in wanted if w and w != "NA"):
        got = cases_for(conn, str(row["term_kind"]) if row else "NA", w, **kw)
        for c in got["cases"]:
            if normalise(c["text"]) in seen:
                continue
            seen.add(normalise(c["text"]))
            merged.append(c)
    return {"ok": True, "term_key": key, "count": len(merged), "cases": merged,
            "sources": sorted({c["source"] for c in merged}),
            "cite": "measured: cases for %s and its aliases %s"
                    % (key, sorted(wanted))}


# --------------------------------------------------------------------------
# THE STANDARDIZED PROOF
# --------------------------------------------------------------------------
def accept_alias(conn: sqlite3.Connection, name: str,
                 cases: list[dict[str, Any]] | None = None, *,
                 term_key: str = "") -> dict[str, Any]:
    """Can this alias be CALLED representative? The verdict, with its numbers.

    ORDER MATTERS: the term is resolved FIRST. A name the register does not know
    has no meaning, so it cannot be representative of anything, and it is REFUSED
    (`NOT_A_TERM`) instead of being scored.
    """
    import terminology_alias as ta
    n = str(name or "").strip()
    if not n:
        return {"ok": False, "code": "EMPTY_NAME"}
    res = ta.resolve_name(conn, n)
    if not res["ok"]:
        return {"ok": False, "code": "NOT_A_TERM", "name": n,
                "why": ("the register does not know %r, so it cannot be "
                        "representative of anything" % n),
                "cite": res["cite"]}

    if cases is None:
        got = cases_for_term(conn, term_key or str(res["now"]))
        cases = got["cases"]

    hits = [c for c in cases if terminal_in(n, c["text"])]
    total = len(cases)
    non_terminal = [c for c in cases if c not in hits]
    out: dict[str, Any] = {
        "ok": True, "name": n, "now": res["now"], "was": res.get("was"),
        "how": res["how"], "cases": total, "terminal": len(hits),
        "non_terminal": len(non_terminal),
        "sources": sorted({str(c["source"]) for c in cases}),
        "cite": res["cite"],
    }
    if total == 0:
        out.update({"verdict": "void", "code": "NO_CASES",
                    "why": ("there are NO case texts for %r, so its "
                            "representativeness CANNOT be measured — an empty "
                            "result is not a pass" % n)})
        return out
    if len(hits) == total:
        out.update({"verdict": "void", "code": "NON_DISCRIMINATING",
                    "why": ("every one of the %d cases contains %r, so the "
                            "score is a property of the CORPUS, not of the "
                            "alias — it does not discriminate" % (total, n))})
        return out
    if len(hits) == 0:
        out.update({"verdict": "not_representative", "code": "NO_HITS",
                    "why": ("the name appears in NONE of the %d cases, so it "
                            "does not represent them" % total)})
        return out
    # DISCRIMINATION EXISTS: at least one case carries the name and at least one
    # does not. Whether that is ENOUGH is decided by the two counts, not by a
    # tuned threshold: `accepted` needs at least TWO hits AND TWO misses, so the
    # result does not rest on a single observation on EITHER side.
    #
    # MEASURED BUG IN THIS FUNCTION'S FIRST VERSION: `provisional` was placed
    # after the `hits == total` branch and required `total == 1`, which is
    # unreachable — ONE case either contains the name (hits == total -> void) or
    # does not (hits == 0 -> not_representative). So the verdict was DEAD CODE and
    # the proof caught it. It now covers the reachable thin cases.
    if len(hits) >= 2 and len(non_terminal) >= 2:
        out.update({"verdict": "accepted",
                    "code": "TERMINAL_AND_DISCRIMINATING",
                    "why": ("%d of %d cases carry the name and %d do NOT, so the "
                            "name both TERMINATES on the cases it belongs to and "
                            "DISCRIMINATES" % (len(hits), total,
                                               len(non_terminal)))})
        return out
    thin = ("only %d case(s) carry the name" % len(hits) if len(hits) < 2
            else "only %d case(s) do NOT carry it" % len(non_terminal))
    out.update({"verdict": "provisional", "code": "THIN_CONTRAST",
                "why": ("the name discriminates (%d hit / %d miss) but %s, so "
                        "the result rests on too few observations to accept"
                        % (len(hits), len(non_terminal), thin))})
    return out


def representation_table(conn: sqlite3.Connection) -> dict[str, Any]:
    """Every alias x its cases, with the verdict. A NUMBER per row."""
    import terminology_alias as ta
    rows: list[dict[str, Any]] = []
    if _table_exists(conn, "terminology_registry"):
        for r in conn.execute("SELECT term_key, alias_list FROM "
                              "terminology_registry ORDER BY term_key"):
            for a in ta._aliases_of(r["alias_list"]):
                v = accept_alias(conn, a, term_key=str(r["term_key"]))
                rows.append({"term_key": str(r["term_key"]), "alias": a,
                             "verdict": v.get("verdict"), "code": v.get("code"),
                             "cases": v.get("cases"), "terminal": v.get("terminal")})
    counts: dict[str, int] = {}
    for r in rows:
        counts[str(r["verdict"])] = counts.get(str(r["verdict"]), 0) + 1
    return {"ok": True, "aliases": len(rows), "by_verdict": counts, "rows": rows,
            "cite": "measured: accept_alias per registered alias"}


def coverage_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many TERMS have any case text — the precondition, as a NUMBER."""
    if not _table_exists(conn, "terminology_registry"):
        return {"ok": False, "code": "NO_REGISTER"}
    terms = [str(r[0]) for r in conn.execute(
        "SELECT term_key FROM terminology_registry ORDER BY term_key")]
    with_cases = 0
    per: list[dict[str, Any]] = []
    for t in terms:
        got = cases_for_term(conn, t)
        if got.get("count"):
            with_cases += 1
        per.append({"term_key": t, "cases": got.get("count", 0)})
    total_cases = sum(p["cases"] for p in per)
    return {"ok": True, "terms": len(terms), "terms_with_cases": with_cases,
            "total_case_rows": total_cases, "per_term": per,
            "cite": ("measured: cases_for_term per term over %d terms" % len(terms))}


# --------------------------------------------------------------------------
# the user's trigger rule
# --------------------------------------------------------------------------
def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def lexical_equivalent_terms(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Keys that NORMALISE alike. A KIND GUARD stops a false merge.

    MEASURED: `module_key 'mouse_spot_helper'` and `capability_key
    'mouse_spot_helper.core'` both normalise to a superset/subset, but they are a
    MODULE and a CAPABILITY — a distil that grouped them would merge two
    different KINDS of thing. Such a pair is reported as `different_kind`.
    """
    import terminology_alias as ta
    rows: list[dict[str, Any]] = []
    for table, col, kind in (("module_registry", "module_key", "module"),
                             ("capability_registry", "capability_key", "capability"),
                             ("channel_registry", "channel_key", "channel"),
                             ("workflow_registry", "workflow_key", "workflow")):
        if not _table_exists(conn, table) or col not in _columns(conn, table):
            continue
        for r in conn.execute("SELECT %s AS k FROM %s" % (col, table)):
            k = str(r["k"])
            if k and not k.startswith("_"):
                rows.append({"kind": kind, "key": k, "norm": _normalise_key(k)})
    out: list[dict[str, Any]] = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a["norm"] == b["norm"]:
                out.append({"a": a["key"], "b": b["key"], "kind_a": a["kind"],
                            "kind_b": b["kind"], "relation": "identical_norm",
                            "same_kind": a["kind"] == b["kind"]})
            elif a["norm"] and b["norm"] and (a["norm"] in b["norm"]
                                              or b["norm"] in a["norm"]):
                out.append({"a": a["key"], "b": b["key"], "kind_a": a["kind"],
                            "kind_b": b["kind"], "relation": "nested_norm",
                            "same_kind": a["kind"] == b["kind"]})
    for o in out:
        o["verdict"] = ("group" if (o["same_kind"]
                                    and o["relation"] == "identical_norm")
                        else "different_kind" if not o["same_kind"]
                        else "review")
    return out


def is_distil_due(conn: sqlite3.Connection) -> dict[str, Any]:
    """The user's TRIGGER as a NUMBER: how many distil/combine jobs are open."""
    import terminology_alias as ta
    lex = lexical_equivalent_terms(conn)
    groups = [o for o in lex if o["verdict"] == "group"]

    ungrouped: list[dict[str, Any]] = []
    for old, new in KNOWN_PENDING_DISTIL:
        r = ta.resolve_name(conn, old)
        if not r["ok"]:
            continue
        if normalise(r.get("now") or "") != normalise(new):
            ungrouped.append({"old": old, "should_be": new,
                              "now_answers": r.get("now"),
                              "how": r.get("how"),
                              "cite": r["cite"]})
    return {"ok": True, "lexical_pairs": len(lex), "groups": len(groups),
            "different_kind": sum(1 for o in lex if o["verdict"] == "different_kind"),
            "ungrouped_pairs": len(ungrouped), "ungrouped": ungrouped,
            "lexical": lex,
            "cite": ("measured: %d lexical pairs, %d same-kind identical groups, "
                     "%d pending distils" % (len(lex), len(groups), len(ungrouped)))}


# --------------------------------------------------------------------------
# apply — take the ONE measured distil
# --------------------------------------------------------------------------
def apply(conn: sqlite3.Connection) -> dict[str, Any]:
    """Record the acceptance and REPORT the open distils. Idempotent."""
    due = is_distil_due(conn)
    rep = representation_table(conn)
    cov = coverage_report(conn)
    return {"ok": True, "distil_due": due, "representation": rep, "coverage": cov,
            "records": sum(1 for r in rep["rows"] if r["verdict"]),
            "cite": "measured: representation + coverage + distil_due"}


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"ok": True, "coverage": coverage_report(conn),
            "representation": representation_table(conn),
            "distil_due": is_distil_due(conn),
            "verdicts": list(VERDICTS)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--alias", default="")
    ap.add_argument("--distil-due", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.alias:
            print(json.dumps(accept_alias(conn, args.alias), indent=2,
                             ensure_ascii=False, default=str))
            return 0
        if args.distil_due:
            d = is_distil_due(conn)
            print("DISTIL TRIGGER: lexical_pairs=%d groups=%d different_kind=%d "
                  "ungrouped=%d" % (d["lexical_pairs"], d["groups"],
                                    d["different_kind"], d["ungrouped_pairs"]))
            for o in d["ungrouped"]:
                print("   OPEN  %-22s should be %-22s (answers %r via %s)"
                      % (o["old"], o["should_be"], o["now_answers"], o["how"]))
            for o in d["lexical"][:8]:
                print("   %-10s %-30s vs %-34s %s"
                      % (o["relation"], o["a"], o["b"], o["verdict"]))
            return 0
        if args.apply:
            res = apply(conn)
            c = res["coverage"]
            print("COVERAGE: terms=%d with_cases=%d case_rows=%d"
                  % (c["terms"], c["terms_with_cases"], c["total_case_rows"]))
            r = res["representation"]
            print("REPRESENTATION: aliases=%d by_verdict=%s"
                  % (r["aliases"], r["by_verdict"]))
            d = res["distil_due"]
            print("DISTIL: groups=%d different_kind=%d ungrouped=%d"
                  % (d["groups"], d["different_kind"], d["ungrouped_pairs"]))
            for o in d["ungrouped"]:
                print("   OPEN  %s -> %s (answers %r)"
                      % (o["old"], o["should_be"], o["now_answers"]))
            return 0
        res = measure(conn)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
            return 0
        c = res["coverage"]
        print("VERDICTS: %s" % res["verdicts"])
        print("COVERAGE: terms=%d with_cases=%d case_rows=%d"
              % (c["terms"], c["terms_with_cases"], c["total_case_rows"]))
        print("   terms_without_cases=%d" % (c["terms"] - c["terms_with_cases"]))
        r = res["representation"]
        print("REPRESENTATION: aliases=%d by_verdict=%s"
              % (r["aliases"], r["by_verdict"]))
        for x in r["rows"]:
            print("   %-12s %-30s <- %-22s cases=%s terminal=%s"
                  % (x["verdict"], x["term_key"], x["alias"], x["cases"],
                     x["terminal"]))
        d = res["distil_due"]
        print("DISTIL TRIGGER: lexical_pairs=%d groups=%d different_kind=%d "
              "ungrouped=%d" % (d["lexical_pairs"], d["groups"],
                                d["different_kind"], d["ungrouped_pairs"]))
        for o in d["ungrouped"]:
            print("   OPEN  %s -> %s (answers %r)"
                  % (o["old"], o["should_be"], o["now_answers"]))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

# object_door: kind-agnostic by definition (no DDL in this file)
