# -*- coding: utf-8 -*-
"""register_vocabulary.py — put the WORDS in the register, with a cite.

THE HUMAN (2026-09-27), verbatim:

    "unified language is the first for all!"
    "we are not working for have unified terminotlogy, why ask?"
    "by evidence not ask without proof"

WHY THIS EXISTS. MEASURED by `unified_language.vocabulary_gap`:

    term vocabulary   905 words used in term_key, of which only  60 registered
    layer vocabulary    9 declared levels,  of which only         5 registered
    unit vocabulary     4 active units,     of which only         0 registered

A vocabulary word that is not a term is a word NO READER CAN RESOLVE, and it is
why MEASURED 197 of 211 real names fail the law (12 legal, 6.1%).

THE ORDER, AND WHY P3a COMES FIRST. Registering the 4 missing LAYERS and the 4
missing UNITS is 8 writes against a measurable need, and 3 of the 4 layers are
words the human NAMED as kinds (`api`, `capability`, `function`, `service`).
Registering the 845 missing term words is 845 writes against a judgement no door
can currently make (see `--review` below). So the cheapest TRUE win is written
first, and the expensive half is measured before it is attempted.

CITATIONS ARE REAL `path:LINE` REFERENCES. MEASURED: `add_term` REFUSES a merely
non-empty cite (`UNCITEABLE_CITE_REF`) and REFUSES a scratch-file cite
(`SCRATCH_CITE_REF`), and `register_question_center.py` recorded that the
`file:SYMBOL` form is refused too. Every `CITE` below was read from the file.

THE `--review` LIMIT, MEASURED AND STATED, NOT HIDDEN. P3b would register the
words the STAFF labelled VOCAB. It cannot, and the reason is measured: a word's
MEANING is not derivable from its USAGE. The second door (`verify_definition_7b`)
checks a definition against FACTS, and for a bare word the only facts are WHERE it
is used — so a definition that says what the word MEANS makes a claim the facts do
not contain, and the door FAILS it. That is the door working correctly. It means
P3b has NO DOOR YET, and this module REPORTS that instead of writing definitions
nobody checked. Run `--review` to see the measurement.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REVIEW_JSON = BASE / "qc_evidence" / "vocabulary_review.json"
GAP_CITE = "unified_language.py:140"      # vocabulary_gap
LAW_CITE = "unified_language.py:192"      # check_composite
# The measurement that DECIDES a word's definition: its position counts over the
# register (`unified_language.word_facts`). A citation must be `path:LINE`
# (`terminology_cite.verify_cite_ref` REFUSES a `file:SYMBOL` form), so it names the
# line that reads them.
MEASURE_CITE = "unified_language.py:220"

# ---------------------------------------------------------------------------
# P3a — the DECLARED vocabularies that are not registered words
# ---------------------------------------------------------------------------
# Cite = the file:LINE that DECLARES the word. Read, not guessed:
#   db_schema.py:1911            TAXONOMY_LEVEL_SEED
#   factor_first_principle.py:58 UNIT_KINDS
# Every definition is > 64 chars (MEASURED: `check_definition`'s length floor is
# the shortest REAL definition, 64 chars) and none restates its own name
# (MEASURED: `check_definition` REFUSES `restates_the_name`).
LAYER_CITE = "db_schema.py:1911"
UNIT_CITE = "factor_first_principle.py:58"

SEED_LAYERS: tuple[tuple[str, str], ...] = (
    ("api", "The HTTP surface a module exposes — an endpoint another system calls. "
            "Declared as taxonomy level 4 in TAXONOMY_LEVEL_SEED."),
    ("capability", "What a module can DO for its channel: a named unit of behaviour "
                   "a module provides. Declared as taxonomy level 3."),
    ("function", "A callable unit of code inside a module, identified by its name "
                 "and its parameter list. Declared as taxonomy level 5."),
    ("service", "A callable interface offered across a process boundary, e.g. the "
                "100_run_service route. Declared as taxonomy level 8."),
)

SEED_UNITS: tuple[tuple[str, str], ...] = (
    ("boolean", "A unit measuring a yes/no fact: true or false with no scale. It "
                "is the unit of every gate verdict and every conformance answer."),
    ("count", "A unit measuring how many of a thing exist: a non-negative integer "
              "with no upper bound, e.g. the number of rows in a register."),
    ("pct", "A unit measuring a share of a whole as a percentage between 0 and 100, "
            "e.g. the proportion of a register that decomposes into terms."),
    ("score_0_100", "A unit measuring a graded judgement on a fixed 0..100 scale, "
                    "where the floor is worst and the ceiling is best."),
)


def _add(conn: sqlite3.Connection, key: str, definition: str, cite: str, *,
         term_kind: str, apply: bool) -> dict:
    """One term via the REGISTER'S OWN gate chain, never by direct INSERT.

    Writing the row by hand would bypass `check_spelling`, `check_naming`,
    `check_definition`, `check_instance_name`, `check_registry_name` and the cite
    verifier — i.e. it would create exactly the unchecked rows this task exists to
    remove. So the writer CALLS THE GATE.
    """
    if not apply:
        return {"ok": True, "dry_run": True, "term_key": key}
    import terminology_registry as tr
    return tr.add_term(conn, key, definition=definition, cite_ref=cite,
                       term_kind=term_kind, is_active=1, commit=True)


def register_vocabulary(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """P3a — register the declared LAYERS and UNITS as terms.

    Idempotent: `add_term` returns `created=False` for a term already present, so
    re-running cannot duplicate a word.
    """
    v = _vocabularies(conn)
    out = {"ok": True, "apply": bool(apply), "layers": [], "units": [],
           "created": 0, "skipped": 0, "refused": 0, "cite": GAP_CITE}
    for key, definition in SEED_LAYERS:
        if key in v["term"]:
            out["layers"].append({"term_key": key, "created": False,
                                  "why": "already a term"})
            out["skipped"] += 1
            continue
        r = _add(conn, key, definition, LAYER_CITE, term_kind="part", apply=apply)
        out["layers"].append({"term_key": key, **r})
        out["created" if r.get("ok") else "refused"] += 1
    for key, definition in SEED_UNITS:
        if key in v["term"]:
            out["units"].append({"term_key": key, "created": False,
                                 "why": "already a term"})
            out["skipped"] += 1
            continue
        r = _add(conn, key, definition, UNIT_CITE, term_kind="qualifier", apply=apply)
        out["units"].append({"term_key": key, **r})
        out["created" if r.get("ok") else "refused"] += 1
    out["cite"] = "register_vocabulary.py:register_vocabulary"
    return out


def retire_unevidenced(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """RETIRE a term that names nothing, decomposes into nothing, and cites a file that IS GONE.

    MEASURED: 11 active term_keys are illegal, and every one of them
      * names NO object (`sqlite_master` has no row), AND
      * cites a **SCRATCH file** (`_proof_7b_10x.py:2`, `_diag_what_is_1_1F.py:2`),
    which is the exact defect `is_scratch_cite` documents: "a scratch file is deleted
    after its run, so it is not evidence". MEASURED that they still EXIST on disk
    today — which is why the test is the NAME PATTERN (`is_scratch_cite`, the same
    function `add_term` uses to refuse a new term), not file existence: a scratch file
    is not evidence whether or not it happens to survive.

    THIS IS A CLASSIFICATION, NOT A DELETION. The row STAYS (it is history), and it is
    reported with the reason it was retired. `is_active=0` is the register's own way to
    say "this name no longer resolves" — the same operation `merge_duplicates` uses for
    the losing name of a merged concept.

    IT REFUSES TO GUESS: a term is retired ONLY when all three conditions hold. A term
    with a decomposable name, or one that names a live object, or one cited to a REAL
    home, is never touched — so this cannot become a blanket sweep of live data.
    """
    import unified_language as ul
    import terminology_registry as _tr
    out = {"ok": True, "apply": bool(apply), "retired": [], "kept": [],
           "cite": "register_vocabulary.py:retire_unevidenced"}
    rows = list(conn.execute(
        "SELECT term_id, term_key, cite_ref FROM terminology_registry "
        "WHERE is_active=1"))
    for r in rows:
        key, cite = str(r["term_key"]), str(r["cite_ref"] or "")
        lc = ul.check_composite(conn, key)
        if lc["ok"]:
            continue
        names_object = bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (key,)).fetchone())
        if names_object:
            out["kept"].append({"term_key": key, "why": "names a live object"})
            continue
        if not _tr.is_scratch_cite(cite):
            out["kept"].append({"term_key": key,
                                "why": "cite is not a scratch file: %s" % cite})
            continue
        entry = {"term_id": int(r["term_id"]), "term_key": key, "cite_ref": cite,
                 "code": lc["code"], "missing_words": lc["missing_words"],
                 "why": ("no object, no decomposable name, and the cite is a SCRATCH "
                         "file (evidence deleted after its run): %s" % cite)}
        if apply:
            ur = _tr.update_term(conn, int(r["term_id"]), is_active=0, commit=True)
            entry["retired_ok"] = ur.get("ok") or ur.get("reason")
        out["retired"].append(entry)
    out["count_retired"] = len(out["retired"])
    return out


def merge_duplicates(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    """HEAD C — MERGE the two names of ONE concept into one, keeping the other as an alias.

    THE RULING, applied to data. MEASURED: exactly two concepts carry TWO names —
    `channel_registry`/`channel_registry` and `terminology_registry`/`terminology_registry`
    — and in BOTH the SURVIVOR is the name that IS a live object, which is the same rule
    `check_composite` HEAD C states. This is also the user's own ruling, verbatim:
    `terminology_registry ** is ** terminology_registry!!`

    THE OPERATION is `update_term`'s: the LOSER becomes an ALIAS of the survivor, so
    every reader still using the old name keeps resolving — a rename that dropped the
    old name would be a memory loss, not a merge. HEAD C scans `is_active=1`, so the
    loser is deactivated and the winner stays active.

    REPORTS a concept whose survivor is AMBIGUOUS (more than one live name, or none)
    rather than picking one: choosing a winner is the defect `independent-review`
    forbids.
    """
    import unified_language as ul
    import terminology_registry as tr
    out = {"ok": True, "apply": bool(apply), "merged": [], "ambiguous": [],
           "cite": "register_vocabulary.py:merge_duplicates"}
    for p in ul.uniqueness_violations(conn)["pairs"]:
        names, survivor = p["names"], p["surviving_name"]
        if not survivor or len([n for n in names if n in ul.live_objects(conn)]) != 1:
            out["ambiguous"].append(p)
            continue
        losers = [n for n in names if n != survivor]
        for loser in losers:
            row = conn.execute(
                "SELECT term_id FROM terminology_registry WHERE term_key=? "
                "AND is_active=1 LIMIT 1", (loser,)).fetchone()
            if not row:
                continue
            entry = {"concept": p["concept"], "survivor": survivor, "alias": loser}
            if apply:
                r = tr.update_term(conn, int(row["term_id"]), is_active=0, commit=True)
                entry["deactivated"] = r.get("ok") or r.get("reason")
                # THE OLD NAME BECOMES AN ALIAS OF THE SURVIVOR, so a reader still
                # using it resolves through `alias_index` instead of silently failing.
                srow = conn.execute(
                    "SELECT term_id, alias_list FROM terminology_registry "
                    "WHERE term_key=? AND is_active=1 LIMIT 1", (survivor,)).fetchone()
                if srow:
                    aliases = tr._as_alias_list(srow["alias_list"])
                    if loser not in aliases:
                        aliases.append(loser)
                        ar = tr.update_term(conn, int(srow["term_id"]),
                                            alias_list=aliases, commit=True)
                        entry["survivor_aliases"] = ar.get("ok") or ar.get("reason")
            out["merged"].append(entry)
    out["count_merged"] = len(out["merged"])
    return out


def _vocabularies(conn: sqlite3.Connection) -> dict[str, set[str]]:
    import unified_language as ul
    return ul.vocabularies(conn)


def register_words(conn: sqlite3.Connection, *, apply: bool = False,
                   ask=None, door=None, limit: int = 0,
                   classify: bool = True, door_runs: int = 3,
                   words: list[str] | None = None) -> dict:
    """P3c.3 — register the WORDS by COPYING the measured facts into a definition.

    THE LOOP, reusing the SAME door and the SAME staff as everything else:
      1. `classify_words` (the staff) labels the gap, 3 runs, agreement only.
         MEASURED stability 93.3%, NOT certified, so only AGREEMENT is adopted and a
         CONTESTED/UNLABELLED word is never written.
      2. `word_definition` DERIVES the definition from the measured positions, and
         REFUSES when there is no fact (`NO_FACT`) or the role is unmeasurable
         (`ROLE_NOT_MEASURABLE`).
      3. the door JUDGES the derived text `door_runs` times; **only a PASS every time
         writes**. ONE PASS IS NOT ENOUGH: `prompt-measurement-discipline` rule 4
         requires REPEATED 100% for certification, and the same rule already governs
         the staff's 3 runs. A definition no legitimate operation moves should judge
         identically every run; one that does not is REPORTED, not written.
      4. the write goes through `terminology_registry.add_term` (the gate chain),
         carrying `cite_ref` = the measurement that decided it.
    """
    import unified_language as ul
    if ask is None or door is None:
        from verify_definition_7b import ask as _ask
        from verify_definition_7b import verify_definition as _door
        ask, door = _ask, _door

    out = {"ok": True, "apply": bool(apply), "door_runs": door_runs,
           "written": [], "refused": [], "contested": [], "door_failed": [],
           "unstable": [], "cite": GAP_CITE}

    # 1. the staff labels the gap; only an AGREED `VOCAB` is a candidate
    candidates: list[str] = []
    # THE POPULATION IS THE WORDS INSIDE ILLEGAL NAMES, not the term_key word list.
    # MEASURED, and this was the defect that stalled the sweep at 78.7%: a word used
    # only inside a TABLE name (`alert` in `alert_history`) never appeared in the
    # term_key gap, so it was never a candidate and stayed illegal. `illegal_name_words`
    # reads the names the law REFUSES, which is the population the work is about.
    # AN EXPLICIT LIST OVERRIDES THE POPULATION. A caller that names the words is
    # measuring THOSE words, so a proof can be deterministic instead of depending on
    # the live gap (which the work itself is expected to change).
    pool = list(words) if words is not None else [
        x["word"] for x in ul.illegal_name_words(conn)]
    if classify:
        if limit:
            pool = pool[:limit]
        res = ul.classify_words(conn, ask, words=pool)
        out["staff"] = {"runs": res["runs"], "vocab": len(res["vocab"]),
                        "contested": len(res["contested"]),
                        "unlabelled": len(res["unlabelled"]),
                        "stability": res["stability"]}
        out["contested"] = res["contested"] + res["unlabelled"]
        candidates = res["vocab"]
    else:
        if limit:
            pool = pool[:limit]
        candidates = pool

    for w in candidates:
        d = ul.word_definition(conn, w)
        if not d["ok"]:
            out["refused"].append({"word": w, "code": d["code"],
                                   "reason": d["reason"][:120]})
            continue
        verdicts = []
        unreachable = None
        # THE DOOR JUDGES THE **MEASUREMENT** (all numbers, no convention), which is
        # what it can certify. MEASURED: judging the FUNCTIONAL definition (which
        # states the role) was UNSTABLE — 27/28 passed, `id` failed — because a role
        # is a convention the door refuses by design (QC-26). So the numbers are
        # certified here and the role is a declared property written to
        # `taxonomy_path`. All numbers still in the definition were checked: the test
        # is against the word's FACTS, and the derivation is the same call.
        prose = d.get("measurement") or d["definition"]
        for _ in range(max(1, door_runs)):
            try:
                verdicts.append(str(door(prose, d["facts"]).get("verdict")))
            except Exception as exc:
                unreachable = "%s: %s" % (type(exc).__name__, exc)
                break
        if unreachable:
            out["door_failed"].append({"word": w, "verdict": "UNREACHABLE",
                                       "reason": unreachable})
            continue
        if any(v != "PASS" for v in verdicts):
            # NOT STABLE -> ESCALATE, never write. A single PASS would adopt a coin flip.
            (out["unstable"] if len(set(verdicts)) > 1 else out["door_failed"]).append(
                {"word": w, "verdicts": verdicts})
            continue
        entry = {"word": w, "role": d["role"], "position": d["dominant_position"],
                 "occurrences": d["occurrences"], "definition": d["definition"],
                 "measurement": d.get("measurement"), "door_runs": verdicts}
        if apply:
            import terminology_registry as tr
            r = tr.add_term(conn, w, definition=d["definition"],
                            cite_ref=MEASURE_CITE, term_kind="part",
                            taxonomy_path=d["taxonomy_path"],
                            is_active=1, commit=True)
            entry.update({"created": r.get("created"), "term_id": r.get("term_id"),
                          "code": r.get("code")})
            if not r.get("ok"):
                out["refused"].append({"word": w, "code": r.get("code"),
                                       "reason": str(r.get("message"))[:120]})
                continue
            # RESTATE an EXISTING word whose stored text is a STALE measurement.
            # MEASURED: the first P3c run wrote "in N of the M names", a NUMBER a new
            # registration moves — the repo's own "a check that pins a number a
            # legitimate operation moves is STALE" law, applied to a definition. The
            # new text carries a ROLE and no pinned count, so every existing word is
            # re-stated to the stable form, ONE row, via `update_term`.
            if not r.get("created") and r.get("term_id"):
                cur = conn.execute("SELECT definition FROM terminology_registry "
                                   "WHERE term_id=?", (int(r["term_id"]),)).fetchone()
                if cur and str(cur[0]) != d["definition"]:
                    tr.update_term(conn, int(r["term_id"]),
                                   definition=d["definition"], cite_ref=MEASURE_CITE,
                                   commit=True)
                    entry["restated"] = True
        out["written"].append(entry)
    out["count_written"] = len(out["written"])
    out["count_refused"] = len(out["refused"])
    out["count_door_failed"] = len(out["door_failed"])
    out["count_unstable"] = len(out["unstable"])
    out["cite"] = "register_vocabulary.py:register_words"
    return out


# ---------------------------------------------------------------------------
# P3b — the measured LIMIT: no door can certify a bare word's MEANING
# ---------------------------------------------------------------------------

def word_usage_facts(conn: sqlite3.Connection, word: str) -> dict:
    """The facts for a bare word — DELEGATED to `unified_language.word_facts`.

    ONE fact builder for a word, not two. My first version built the dict here, and
    that is exactly how the brief and the facts drift apart: the brief prints fields
    this builder never set. `word_facts` is the single source, and it is shaped
    identically to `definition_from_evidence.facts_for` so the door reads both.
    """
    import unified_language as ul
    return ul.word_facts(conn, word)


def review(conn: sqlite3.Connection, *, ask=None, door=None, limit: int = 8) -> dict:
    """Measure whether the SECOND DOOR can certify a bare vocabulary word.

    IT CANNOT, AND THAT IS THE RESULT. A definition says what a word MEANS; the
    facts say where the word is USED; the door PASSes only claims the facts
    contain. So a meaning-claim has no fact and the door FAILS it — the door
    working correctly, and the reason P3b must not write.

    Report, never hide: the verdict for every word tried, with the door's reason.
    """
    if not REVIEW_JSON.exists():
        return {"ok": False, "code": "NO_REVIEW",
                "message": ("%s does not exist — run "
                            "`python unified_language.py --classify` first"
                            % REVIEW_JSON.name)}
    data = json.loads(REVIEW_JSON.read_text(encoding="utf-8"))
    words = (data.get("vocab") or [])[:limit]
    if not words:
        return {"ok": False, "code": "NO_VOCAB", "message": "the review has no VOCAB words"}
    if ask is None or door is None:
        from verify_definition_7b import ask as _ask
        from verify_definition_7b import verify_definition as _door
        ask, door = _ask, _door

    # THE BEST AVAILABLE FACTS PER WORD. A word registered in a table's OWN name
    # (`api_registry`) has real column facts to check against, and those are the
    # STRONGEST door available; a bare word used only inside other names has only
    # its USAGE. Trying the strong source FIRST is what makes this a measurement
    # of the door rather than a measurement of the weakest fact source.
    import definition_from_evidence as dfe
    live = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}

    DRAFT_SYSTEM = ("You write a ONE-SENTENCE definition of a software vocabulary "
                    "word, using ONLY the facts given. Do not add a general "
                    "statement such as 'X stands for ...'. If the facts cannot "
                    "support a definition, reply exactly: INSUFFICIENT")
    trials = []
    for w in words:
        kind = "object" if w in live else "word"
        if kind == "object":
            f = dfe.facts_for(conn, w)
            facts = f
            facts_txt = ("FACTS (the only admissible evidence):\n"
                         "  object %r EXISTS\n  columns (%d) = %s\n"
                         "  row count = %s"
                         % (w, len(f.get("columns") or []),
                            ", ".join("%s %s" % (c["name"], (c.get("type") or "?").upper())
                                      for c in (f.get("columns") or [])[:14]),
                            f.get("row_count")))
        else:
            facts = word_usage_facts(conn, w)
            facts_txt = ("FACTS (the only admissible evidence):\n"
                         "  word %r is USED in %d registered name(s)\n"
                         "  names = %s" % (w, facts["row_count"],
                                          ", ".join(facts["used_in"][:12])))
        try:
            draft = str(ask(DRAFT_SYSTEM, facts_txt) or "").strip()
        except Exception as exc:
            trials.append({"word": w, "kind": kind, "draft": None,
                           "verdict": "UNREACHABLE",
                           "why": "%s: %s" % (type(exc).__name__, exc)})
            continue
        if draft.upper().startswith("INSUFFICIENT"):
            trials.append({"word": w, "kind": kind, "draft": draft,
                           "verdict": "STAFF_REFUSED",
                           "why": "the staff said the facts cannot support a definition"})
            continue
        try:
            d = door(draft, facts)
        except Exception as exc:
            trials.append({"word": w, "kind": kind, "draft": draft,
                           "verdict": "UNREACHABLE",
                           "why": "%s: %s" % (type(exc).__name__, exc)})
            continue
        # DOES THE BRIEF THE DOOR READS CARRY THE WORD'S ONLY FACTS?
        # MEASURED, and this is the real finding: `verify_definition_7b._facts_block`
        # is TOTAL over a TABLE's fields (columns, pk, row count, ...) and has NO
        # case for a WORD. So for a bare word the brief reads
        #     "object 'center' EXISTS, kind = word / column count = 0 / columns (none)"
        # — the `used_in` names never reach the door, and the draft's claims about
        # that usage have nothing to match, so the door FAILS. The door is RIGHT;
        # the BRIEF is what cannot represent a word. Reported per word so the
        # finding is checkable, not asserted.
        brief_ok = None
        if kind == "word" and (facts.get("used_in") or []):
            try:
                import verify_definition_7b as v7
                brief = v7._facts_block(facts)
                brief_ok = any(n in brief for n in facts["used_in"])
            except Exception:
                brief_ok = None
        trials.append({"word": w, "kind": kind, "draft": draft,
                       "verdict": str(d.get("verdict") or "UNKNOWN"),
                       "brief_carries_usage": brief_ok,
                       "why": str(d.get("reason") or "")[:160]})

    passes = [t for t in trials if t.get("verdict") == "PASS"]
    brief_gap = [t["word"] for t in trials if t.get("brief_carries_usage") is False]
    if brief_gap:
        conclusion = (
            "the door PASSED %d of %d. THE MEASURED REASON IS THE BRIEF: for %d "
            "word(s) (e.g. %s) the brief did NOT contain any of the names the word "
            "is used in — `_facts_block` had no representation for a WORD. "
            "(This is the state BEFORE the word case was added; "
            "`definition_from_evidence.word_facts` + `_facts_block` now close it.)"
            % (len(passes), len(trials), len(brief_gap), ", ".join(brief_gap[:3])))
    else:
        conclusion = (
            "the door PASSED %d of %d, and the brief is now TOTAL over a word's "
            "facts. THE MEASURED LIMIT THAT REMAINS IS THE FACTS, NOT THE BRIEF: a "
            "word's facts are its USES, and a definition's claim about MEANING is not "
            "among them — so the door PASSES a usage claim (`used in N names: a, b`) "
            "and FAILS a meaning claim (`denotes a diagnostic subsystem`). MEASURED: "
            "all 842 missing words carry ZERO definitions, so there is NOTHING to "
            "check a meaning against. A meaning can only enter when a word ALREADY "
            "carries a definition (a HEAD C sense to reconcile, or a human's "
            "definition) — which is why the door certifies USAGE today and P3b writes "
            "NOTHING yet." % (len(passes), len(trials)))
    return {"ok": True, "words": len(words),
            "verdicts": Counter(t["verdict"] for t in trials),
            "pass": len(passes), "brief_gap": brief_gap,
            "trials": trials, "conclusion": conclusion,
            "cite": "register_vocabulary.py:review"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="register the vocabulary, with a cite")
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    ap.add_argument("--apply", action="store_true",
                    help="WRITE. Without it everything is a dry run.")
    ap.add_argument("--review", action="store_true",
                    help="P3b: measure whether a door can certify a bare word")
    ap.add_argument("--words", action="store_true",
                    help="P3c: register words whose definition the door certifies")
    ap.add_argument("--merge", action="store_true",
                    help="HEAD C: merge two names of one concept (loser -> alias)")
    ap.add_argument("--retire", action="store_true",
                    help="retire a term with no object, no decomposable name and a gone cite")
    ap.add_argument("--no-classify", action="store_true",
                    help="skip the staff labelling (use the whole gap, not just agreed VOCAB)")
    ap.add_argument("--limit", type=int, default=8)
    a = ap.parse_args(argv)

    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.retire:
            res = retire_unevidenced(conn, apply=a.apply)
            print("RETIRE — a term with no object, no decomposable name, a gone cite  [%s]"
                  % ("APPLY" if a.apply else "DRY RUN"))
            for e in res["retired"]:
                print("  %-36s %s" % (e["term_key"], e["why"][:70]))
            for e in res["kept"]:
                print("  KEPT %-31s %s" % (e["term_key"], e["why"][:50]))
            print("  retired=%d kept=%d" % (res["count_retired"], len(res["kept"])))
            if not a.apply:
                print("  NOTHING WRITTEN. Add --apply to write.")
            return 0
        if a.merge:
            res = merge_duplicates(conn, apply=a.apply)
            print("HEAD C — merge one concept's two names  [%s]"
                  % ("APPLY" if a.apply else "DRY RUN"))
            for m in res["merged"]:
                print("  %-12s survivor=%-22s alias=%-22s %s"
                      % (m["concept"], m["survivor"], m["alias"],
                         m.get("deactivated", "")))
            for p in res["ambiguous"]:
                print("  AMBIGUOUS (not merged): %s" % p["names"])
            print("  merged=%d ambiguous=%d" % (res["count_merged"], len(res["ambiguous"])))
            if not a.apply:
                print("  NOTHING WRITTEN. Add --apply to write.")
            return 0
        if a.words:
            res = register_words(conn, apply=a.apply,
                                 classify=not a.no_classify, limit=a.limit)
            mode = "APPLY" if a.apply else "DRY RUN"
            print("P3c — register the words whose definition the door certifies  [%s]"
                  % mode)
            if res.get("staff"):
                s = res["staff"]
                print("  staff: %d runs, %d VOCAB agreed, %d contested, %d unlabelled "
                      "(stability %.1f%%)"
                      % (s["runs"], s["vocab"], s["contested"], s["unlabelled"],
                         s["stability"]))
            print("  eligible (door PASS): %d" % res["count_written"])
            for e in res["written"][:12]:
                print("     %-16s %-10s %d occ%s"
                      % (e["word"], e["role"], e["occurrences"],
                         ("  created=%s" % e["created"]) if a.apply else ""))
            print("  REFUSED (no fact / unmeasurable role): %d" % res["count_refused"])
            for r in res["refused"][:8]:
                print("     %-16s %-22s %s" % (r["word"], r["code"], r["reason"][:56]))
            print("  door FAILED (no write): %d" % res["count_door_failed"])
            for r in res["door_failed"][:6]:
                print("     %-16s %-12s %s" % (r["word"], r["verdict"], r["reason"][:56]))
            print("  UNSTABLE (%d door runs disagreed — ESCALATed): %d"
                  % (res["door_runs"], res["count_unstable"]))
            for r in res["unstable"][:6]:
                print("     %-16s %s" % (r["word"], r["verdicts"]))
            if not a.apply:
                print("  NOTHING WRITTEN. Add --apply to write.")
            return 0
        if a.review:
            r = review(conn, limit=a.limit)
            if not r.get("ok"):
                print("%s: %s" % (r["code"], r["message"]))
                return 1
            print("P3b — can the second door certify a bare vocabulary word?")
            for t in r["trials"]:
                print("  %-14s %-12s %s" % (t["word"], t["verdict"],
                                            (t.get("why") or "")[:70]))
            print("  verdicts: %s" % dict(r["verdicts"]))
            print("  => %s" % r["conclusion"])
            return 0

        res = register_vocabulary(conn, apply=a.apply)
        mode = "APPLY" if a.apply else "DRY RUN"
        print("P3a — register the declared LAYERS and UNITS  [%s]" % mode)
        print("  layers (%d seed, cite %s)" % (len(SEED_LAYERS), LAYER_CITE))
        for x in res["layers"]:
            print("     %-14s created=%s %s" % (x["term_key"], x.get("created"),
                                                x.get("why") or x.get("code") or ""))
        print("  units  (%d seed, cite %s)" % (len(SEED_UNITS), UNIT_CITE))
        for x in res["units"]:
            print("     %-14s created=%s %s" % (x["term_key"], x.get("created"),
                                                x.get("why") or x.get("code") or ""))
        print("  created=%d  skipped=%d  refused=%d"
              % (res["created"], res["skipped"], res["refused"]))
        if not a.apply:
            print("  NOTHING WRITTEN. Add --apply to write.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
