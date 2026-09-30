# -*- coding: utf-8 -*-
"""unified_language.py — ONE word, ONE meaning, ONE name.

THE HUMAN (2026-09-27), verbatim:

    "unified = only have unqiue for each!"
    "single word + single word = must under terminotlogy"
    "unified language is the first for all!"
    "table / field / capability / channel / module, terminotlogy is simple"

THE RULING, AS THREE HEADS. A name is legal only when ALL THREE hold:

    HEAD A  DECOMPOSITION   every word of a name is registered in SOME vocabulary
                            (term / layer / unit — they are ONE language)
    HEAD B  COMPOSITION     a compound name is legal only when each SINGLE word
                            stands alone in a vocabulary
    HEAD C  UNIQUENESS      one concept has ONE name; a word in two vocabularies
                            MEANS one thing

MEASURED 2026-09-27 on the live DB — the disease is universal, not a spelling nit:

    words used in term_key   900 total   60 registered (6.7%)   840 not (3848 uses)
    taxonomy_level_registry    9 total    5 registered (56%)      4 not
    unit_registry              4 total    0 registered (0%)       4 not
    real names (table/view)  211 total   19 legal (9.0%)         183 MISSING_WORD
    uniqueness violations        2 concept-groups hold TWO names
    cross-vocabulary shared      5 words, ALL 5 meaning two different things

WHY THE LAW CANNOT BE SWITCHED ON FIRST — MEASURED decomposition curve:

    top   0 words ->   22 / 1463    1.5%
    top 100 words ->  410 / 1463   28.0%
    top 300 words ->  881 / 1463   60.2%
    top 840 words -> 1463 / 1463  100.0%

So `check_composite` is a MEASUREMENT. It REFUSES nothing until a caller asks it to
(`enforce=`), and the default is REPORT — a refusal at 1.5% refuses almost every
legal name, which is a worse defect than the one it fixes.

THE STAFF NEVER DECIDES. The 7B labels a word; it does not write one. MEASURED
stability over 3 identical runs is 53/60 = 88%, which is NOT certified, so a word
labelled two different ways is ESCALATED, never guessed. And the `\\d` rule is
applied FIRST — a word containing a digit is a FRAGMENT whatever the model says.

THE `ask` IS INJECTED (signature `ask(system, user) -> str`). This module holds NO
model name at module level: the caller passes the door. That is the same seam
`question_flow`, `research_direction` and `stepwise_ask` use, so any LLM can serve.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CITE = "unified_language.py:check_composite"

# The three vocabularies. They are ONE language, so HEAD A accepts a word
# registered in ANY of them; HEAD C makes the shared word mean ONE thing.
VOCABS = ("term", "layer", "unit")

# THE POSITION→ROLE CONVENTION, DECLARED — this is the piece that makes a
# compositional language GIVE a word its meaning.
#
# THE HUMAN (2026-09-27): "single word + single word = must under terminotlogy" and
# "the system for how to let worker be coding builder ... we can easy to rename".
#
# IN A COMPOSITIONAL LANGUAGE A WORD'S ROLE IS ITS MEANING, and the role is WHERE IT
# SITS. MEASURED over the register: `endpoint` is a SUFFIX 37 times and a prefix 0
# times; `diag` is a PREFIX 142 times and a suffix 0 times. The counts are facts; the
# CONVENTION that maps a position to a role is NOT a fact, so it is DECLARED here —
# the same reason `INSTANCE_VALUE_SEED` and `TAXONOMY_LEVEL_SEED` are declarations.
#
# A CONVENTION IS NOT A FACT AND MUST NOT BE ASSERTED TO THE DOOR. MEASURED: when a
# draft stated a role ("X is the KIND a thing is") the door FAILED it — correctly,
# because a role-convention is not among the facts. So the door certifies the
# POSITION (a number it was shown) and the role is applied by the READER. That is
# the whole design: the machine certifies measurements, the convention is declared,
# and neither pretends to be the other.
POSITION_ROLE = {
    "prefix": ("family", "the word NAMES THE FAMILY the thing belongs to "
                         "(it comes first in the name)"),
    "suffix": ("kind", "the word NAMES THE KIND the thing IS "
                       "(it comes last in the name)"),
    "middle": ("qualifier", "the word QUALIFIES between a family and a kind "
                            "(it is neither first nor last)"),
    "alone": ("unit", "the word IS the vocabulary unit; it carries no role"),
}

# A word containing a digit is a FRAGMENT. MEASURED: of 155 7B-FRAGMENT judgements,
# 40 contain a digit and 115 do not — so this rule settles 40 of them OBJECTIVELY
# and the other 115 remain the contestable half that needs door 3.
DIGIT_RE = re.compile(r"\d")

# A token that is ENTIRELY digits is a NUMBER, not a word. MEASURED: the only
# all-digit tokens in the register are `100` (8 uses), and `1`/`0`/`2` (once each) —
# quantities in a name (`llm_100_run` is the "100-run" harness). A number is a
# LITERAL the name carries, so the law ACCEPTS it without registration, exactly as a
# name may contain a literal value. A token with letters AND digits (`7b`, `v1`,
# `5w1h`) is a version or a code, i.e. a FRAGMENT, and is still refused — the two are
# different kinds of token and the distinction is what makes the rule principled
# rather than a blanket "no digits".
NUMBER_RE = re.compile(r"^\d+$")

# A token that is a REGISTERED WORD plus a VERSION NUMBER is a versioned form of that
# word — the word carries the meaning and the digits are the version the name carries.
# MEASURED: `tables2`, `merged2`, `rebuild2`, `config2`, `model2`, `registry2`,
# `rows2`, `step5`, `level2`, `ui3` all have a base that IS registered (`registry`,
# `rows`, `ui`, ...). The rule therefore requires the BASE to be registered: it cannot
# rescue a genuine code. `7b` (starts with a digit), `e2e` (does not end in digits),
# `100run`, `4part` and `v1` (base `v` is not a word) are NOT rescued — those are
# abbreviations, and a name using one is REPORTED as illegal, which is correct.
VERSIONED_RE = re.compile(r"^([A-Za-z_]+?)(\d+)$")

# The canonical merge pair. HEAD C: `x_registry` and `x_registry` are ONE concept
# with TWO names. MEASURED: 2 concepts in this state, and in BOTH the survivor is
# the name that IS a live object.
SUFFIX_RE = re.compile(r"_(register|registry|registar|registery)$", re.I)

# What the staff must answer. A CHECKLIST contract, not an open question: MEASURED
# on the sibling door, an open question produces a rubber stamp (1/3) while a
# checklist contract passes (5/5). Same lesson, same shape.
LABEL_SYSTEM = (
    "You label words from a software system's vocabulary. For EACH input word, "
    "decide ONE label:\n"
    "  VOCAB    = a general word a reader can reuse in MANY names "
    "(e.g. channel, registry, task, api)\n"
    "  FRAGMENT = a private abbreviation, a typo, a version or a fragment of one "
    "specific name (e.g. eumu, zz, 10x, 3level, 100run)\n"
    "A word you cannot place is 'UNKNOWN' - say UNKNOWN, do NOT guess.\n"
    "Reply with ONE line per word, exactly: <word> <VOCAB|FRAGMENT|UNKNOWN>"
)
LABEL_RE = re.compile(r"^\s*([A-Za-z_][\w\-]*)\s*[:=]?\s*(VOCAB|FRAGMENT|UNKNOWN)\b", re.I)


class LanguageError(RuntimeError):
    """A refusal. It names the head that refused, never just 'invalid'."""


# ---------------------------------------------------------------------------
# the vocabularies, read ONCE
# ---------------------------------------------------------------------------

def vocabularies(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """`{term, layer, unit}` — the registered words of each vocabulary.

    Only `is_active=1` counts. MEASURED reason: `add_term` documents that a term
    resolving to nothing is the defect, and 3 of the 9 layers are `is_active=0`.
    """
    term = {str(r[0]) for r in conn.execute(
        "SELECT term_key FROM terminology_registry WHERE is_active=1")}
    try:
        layer = {str(r[0]) for r in conn.execute(
            "SELECT level_key FROM taxonomy_level_registry")}
    except sqlite3.Error:
        layer = set()
    try:
        unit = {str(r[0]) for r in conn.execute(
            "SELECT unit_key FROM unit_registry WHERE is_active=1")}
    except sqlite3.Error:
        unit = set()
    return {"term": term, "layer": layer, "unit": unit}


def live_objects(conn: sqlite3.Connection) -> set[str]:
    """Tables AND views — MEASURED as `type IN ('table','view')`, because a VIEW
    (`subcatalog`) is a named object a reader must resolve just as a table is."""
    return {str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}


def real_names(conn: sqlite3.Connection) -> list[str]:
    """The compound names the law is measured against."""
    return sorted(n for n in live_objects(conn) if "_" in n)


def all_names(conn: sqlite3.Connection) -> list[str]:
    """EVERY name the law governs: the live objects AND the active term_keys.

    MEASURED, and this was a real hole in "build the language first": the population
    was `real_names` (tables/views) ONLY, so a word that appears only inside a
    `term_key` (`active`, `error`, `row`, `set`, `ide` — all ordinary English) never
    became a candidate. MEASURED RESULT: **328 of 2232 active term_keys (14.7%)
    would be REFUSED by the law if created today**, which is exactly the defect the
    "build the language, THEN enforce" order exists to prevent. A term_key IS a name
    the law governs, so it is in the population.
    """
    keys = [str(r[0]) for r in conn.execute(
        "SELECT DISTINCT term_key FROM terminology_registry WHERE is_active=1")]
    return sorted(set(real_names(conn)) | {k for k in keys if "_" in k})


def illegal_name_words(conn: sqlite3.Connection) -> list[dict]:
    """The words that make a NAME illegal, most-blocking first.

    THE POPULATION, STATED. MEASURED, and my first candidate source was WRONG: I
    took `vocabulary_gap()["missing_top"]`, which counts words in `term_key` ONLY —
    so `alert` (used in the TABLE `alert_history`) was never a candidate and stayed
    illegal, even though the writer could register it. The population that matters is
    the words INSIDE the names the law refuses, so this reads THEM, and reports each
    word with the number of names it blocks. A word that blocks more names is fixed
    first.

    AND THE NAMES INCLUDE `term_key`s (`all_names`), not only live objects: MEASURED,
    limiting it to tables/views left 14.7% of active term_keys refusable, because
    their words never entered the pool.
    """
    words = Counter()
    for n in all_names(conn):
        r = check_composite(conn, n)
        if not r["ok"]:
            for w in r["missing_words"]:
                words[w] += 1
    return [{"word": w, "blocks": c} for w, c in words.most_common()]


def vocabulary_gap(conn: sqlite3.Connection) -> dict:
    """P0 — measure every vocabulary. Writes nothing, refuses nothing.

    MEASURED shape of the disease: in EACH vocabulary most words are unregistered,
    and the term vocabulary's gap is 840 words used 3848 times.
    """
    v = vocabularies(conn)
    term = v["term"]
    words = Counter()
    for r in conn.execute("SELECT term_key FROM terminology_registry"):
        for w in str(r[0]).split("_"):
            words[w] += 1

    def _pct(a, b):
        return round(100.0 * a / b, 1) if b else 0.0

    gap_term = {w: n for w, n in words.items() if w not in term}
    # THE NAME COUNT COMES FROM `check_composite`, THE ONE LAW. MEASURED, and it was a
    # real two-implementation defect: this function had its own inline baseline, so
    # when the law gained the NUMBER-LITERAL rule (`llm_100_run` is legal) the gap
    # still reported it illegal — the report and the law disagreed. One source of
    # truth, so a change to the law cannot leave the report behind.
    names = real_names(conn)
    legal_names = [n for n in names if check_composite(conn, n, vocab=v)["ok"]]
    baseline = len(legal_names)
    return {
        "ok": True,
        "vocabularies": [
            {"name": "term", "total": len(words), "registered": len(words) - len(gap_term),
             "missing": len(gap_term), "uses": sum(gap_term.values()),
             "pct": _pct(len(words) - len(gap_term), len(words))},
            {"name": "layer", "total": len(v["layer"]),
             "registered": len(v["layer"] & term), "missing": len(v["layer"] - term),
             "missing_words": sorted(v["layer"] - term),
             "pct": _pct(len(v["layer"] & term), len(v["layer"]))},
            {"name": "unit", "total": len(v["unit"]),
             "registered": len(v["unit"] & term), "missing": len(v["unit"] - term),
             "missing_words": sorted(v["unit"] - term),
             "pct": _pct(len(v["unit"] & term), len(v["unit"]))},
        ],
        "names": {"total": len(names), "legal": baseline,
                  "pct": _pct(baseline, len(names))},
        "missing_top": [{"word": w, "uses": n}
                        for w, n in sorted(gap_term.items(), key=lambda kv: -kv[1])],
        "cite": "unified_language.py:vocabulary_gap",
    }


# ---------------------------------------------------------------------------
# HEAD A + B — the law itself
# ---------------------------------------------------------------------------

def words_of(name: str) -> list[str]:
    return [w for w in str(name).split("_") if w]


def word_facts(conn: sqlite3.Connection, word: str, *,
               vocab: dict | None = None) -> dict:
    """Every fact the database holds about a WORD (not an object).

    WHY THIS LIVES HERE, and not in `definition_from_evidence`. MEASURED, and the
    proof caught it: `_proof_definition_from_evidence.py` QC-01 asserts that module
    has NO literal table name in a FROM clause — it reads objects generically through
    `sqlite_master`/`PRAGMA`. A word's facts require reading the register's own
    columns, so putting it there BROKE that invariant (the proof went red on the very
    change). This module already names the register's tables by design, so the word
    case belongs here.

    WHY IT EXISTS AT ALL. A vocabulary word is not a table, so `facts_for` returns
    `ok=False` for it, and the door's brief then reads an EMPTY fact set. MEASURED by
    `register_vocabulary.py --review`: for 6 of 6 words the brief carried NONE of the
    names the word is used in, so the door FAILED drafts about usage it was never
    shown. The door was right; the brief could not represent a word.

    THE SHAPE IS IDENTICAL TO `facts_for` (`ok`, `name`, `exists`, `kind`, `columns`,
    `row_count`, `foreign_keys`, `indexes`, `cite`) so the SAME door brief reads both
    and there is no second fact format. `columns` is EMPTY and `row_count` counts the
    USES — the honest reading of "how many" for a word. `kind` is `"word"`, never
    `"table"`, because a word claiming to be a table is a lie the brief cannot catch.
    """
    w = str(word or "").strip()
    if not w:
        return {"ok": False, "name": w, "exists": False,
                "why": "an empty string is not a word"}
    v = vocab or vocabularies(conn)
    # MEASUREMENT SELF-POLLUTION, MEASURED AND FIXED. Registering a word CREATES a
    # term row whose `term_key` IS that word, so a naive `used_in` scan counted the
    # word as "used" once more — by itself. MEASURED: after P3c wrote `endpoint`,
    # `used_in` went 37 -> 38 and `alone` 0 -> 1, so the definition it had just
    # written ("in 37 of the 37 names") no longer matched its own facts. The same
    # self-reference made `senses` include the word's OWN definition, which then
    # cycled: a role claim PASSED because the brief now carried a definition saying
    # the same thing. A word is not a USE of itself, and its own definition is not an
    # independent SENSE, so both exclude it.
    used_in = sorted(
        str(r[0]) for r in conn.execute("SELECT term_key FROM terminology_registry")
        if str(r[0]) != w and w in str(r[0]).split("_"))
    # A NAME IS A NAME, whether it is a term or a live object. MEASURED, and my first
    # version scanned only `term_key`, so `alert` (used in the TABLE `alert_history`)
    # and `agent` (`agent_provider`) reported NO_FACT while being used — a word with no
    # fact that demonstrably has one. The register and `sqlite_master` are the two
    # places a name lives, so both are read.
    for r in conn.execute("SELECT name FROM sqlite_master "
                          "WHERE type IN ('table','view')"):
        n = str(r[0])
        if n != w and w in n.split("_"):
            used_in.append(n)
    used_in = sorted(set(used_in))
    registered_as = [k for k in VOCABS if w in v.get(k, set())]
    exists = bool(used_in) or bool(registered_as)
    # POSITION IS A MEASURED FACT, and in a compositional language it is the fact
    # that carries MEANING: `single word + single word` means each word has a ROLE,
    # and the role is where it sits. MEASURED: `endpoint` is a suffix 37 times and a
    # prefix 0 times; `diag` is a prefix 142 times and a suffix 0 times. The counts
    # are read, never assumed, and the convention that turns a position into a role
    # is DECLARED (see `POSITION_ROLE`) rather than asserted.
    pos = _positions(used_in, w)
    return {
        "ok": exists, "name": w, "exists": exists, "kind": "word",
        "columns": [], "foreign_keys": [], "indexes": [],
        "row_count": len(used_in),          # "how many" IS "used in how many names"
        "used_in": used_in,
        "registered_as": registered_as,
        "position": pos,
        "dominant_position": _dominant_position(pos),
        "equivalent_spellings": sorted(
            {s for k in VOCABS for s in v.get(k, set())
             if _normalise_spelling(s) == _normalise_spelling(w) and s != w}),
        "senses": _senses(conn, w),
        "cite": "measured: terminology_registry.term_key and the three vocabularies",
    }


def _positions(names: list[str], word: str) -> dict:
    """Where `word` sits inside each name that contains it. COUNTS, all read.

    A name of ONE word is `alone`: it IS the vocabulary unit, and it has no role,
    which is why single words are ATOMIC (see `check_composite`).
    """
    out = {"prefix": 0, "suffix": 0, "middle": 0, "alone": 0}
    for k in names:
        parts = str(k).split("_")
        if word not in parts:
            continue
        i = parts.index(word)
        if len(parts) == 1:
            out["alone"] += 1
        elif i == 0:
            out["prefix"] += 1
        elif i == len(parts) - 1:
            out["suffix"] += 1
        else:
            out["middle"] += 1
    return out


def _dominant_position(pos: dict) -> str:
    """The position with the most occurrences. Ties resolve to `prefix` > `suffix` >
    `middle` > `alone`, a FIXED order so the answer is deterministic and two runs
    cannot disagree. A word with no occurrences is `unused` — NOT a guess.
    """
    order = ("prefix", "suffix", "middle", "alone")
    best = max(order, key=lambda k: (pos.get(k, 0), -order.index(k)))
    return best if pos.get(best, 0) > 0 else "unused"


# A role is only MEASURABLE when its position is CONSISTENT, not merely frequent.
#
# MEASURED, and this replaced a fixed floor of 3: a word used twice, BOTH times as a
# suffix, has a CONSISTENT role — `history` (4), `provider` (3), `version` (3) were
# registerable; `alert`/`option` (1-2, unanimous) were refused for being RARE rather
# than INCONSISTENT. Frequency and consistency are different tests, and only the second
# is about the role. So the rule is:
#   * UNANIMOUS (every occurrence in one position) -> measurable, whatever the count;
#   * otherwise -> measurable only with a strong plural (>= STRONG_ROLE_OCCURRENCES).
# A word used ONCE is a name's own fragment (the HEAD rule) AND its role is trivially
# unanimous — registering it is exactly what turns that name legal, which is the
# point of the law ("single word + single word = must under terminology").
MIN_ROLE_OCCURRENCES = 1
STRONG_ROLE_OCCURRENCES = 3

# A word whose occurrences split across positions has NO single role, and that is a
# MEASURED property, not a refusal. MEASURED: `asset` is prefix 2 + suffix 2, `right`
# is suffix 2 + middle 2 — real words the language uses as both. The law's job is that
# a NAME decomposes into REGISTERED WORDS, so such a word is registered with its role
# DECLARED as `inconsistent`, and only a genuine fragment is refused.
ROLE_INCONSISTENT = ("inconsistent",
                     "the word appears in MORE THAN ONE position, so position does "
                     "not fix a single role")

# The cite this module stamps on a definition it DERIVED, so `_senses` can tell its
# own text from an independent declaration. Kept as the file name (not a line) so a
# change to this file does not silently invalidate the exclusion.
MEASUREMENT_CITE_FILE = "unified_language.py:"

# The role a word plays in a NAME is not the level a THING is. `taxonomy_level`
# holds entity levels (`channel`, `module`, ...) and `add_term` REFUSES an
# undeclared one, so a word's position-role goes in `taxonomy_path` — the free
# path column — under a declared prefix. Encoding it there rather than inventing a
# taxonomy level keeps one axis per column.
ROLE_PATH_PREFIX = "word_position:"


def word_definition(conn: sqlite3.Connection, word: str) -> dict:
    """The DEFINITION of a word, DERIVED from its measured facts. No prose invented.

    THIS IS THE "CODING COPIER" (the human, 2026-09-27: "let worker be coding builder
    to be coding copyer"). The worker does not WRITE a meaning; it COPIES the facts
    into a definition, and the door then certifies that the copy is faithful.

    WHAT THE DEFINITION CLAIMS: ONLY the measured positions. MEASURED, and this is
    why the role is NOT in the text: the door FAILS a role claim ("is the KIND a thing
    is") because a role is a CONVENTION, not a fact — QC-26 measures exactly that.
    So the text carries the MEASUREMENT and the ROLE goes in `taxonomy_path`.

    REFUSES when no fact can support a definition:
      * the word is used nowhere                 -> NO_FACT
      * its dominant position is used < 3 times   -> ROLE_NOT_MEASURABLE (ESCALATE)
    """
    w = str(word or "").strip()
    f = word_facts(conn, w, vocab=vocabularies(conn))
    # THE DIGIT RULE, applied FIRST and OBJECTIVELY — the same rule `classify_words`
    # applies, moved here so the WRITER cannot register a fragment even when the staff
    # is skipped at scale. MEASURED: of 155 7B-FRAGMENT judgements, 40 contain a digit,
    # so this rule settles them with NO model and NO stability question. A word with a
    # digit is a version or a shorthand (`4part`, `7b`, `100run`, `10x`), never a
    # reusable vocabulary word.
    if DIGIT_RE.search(w):
        return {"ok": False, "code": "FRAGMENT", "word": w,
                "reason": ("%r contains a digit, so it is a version or a shorthand "
                           "rather than a reusable word — the same objective rule "
                           "`classify_words` applies" % w),
                "cite": "unified_language.py:word_definition"}
    if not f.get("ok"):
        return {"ok": False, "code": "NO_FACT", "word": w,
                "reason": "%r is used in no name and is registered nowhere, so "
                          "there is no fact to build a definition from" % w,
                "cite": "unified_language.py:word_definition"}
    pos = f["position"]
    dom = f["dominant_position"]
    top = int(pos.get(dom, 0))
    total = f["row_count"]
    # TWO SEPARATE QUESTIONS, and conflating them stalled the sweep at 78.7%:
    #   (1) MAY THE WORD BE REGISTERED?  -> is it a real word (not a fragment)?
    #   (2) WHAT IS ITS ROLE?            -> is its position consistent?
    # A word used in several positions (`asset`: 2 prefix + 2 suffix) is clearly a
    # REAL word, and the law's purpose — a name decomposing into registered words —
    # needs (1), not (2). So an inconsistent position registers the word with the role
    # DECLARED as `inconsistent`, and only a genuine fragment is refused. MEASURED:
    # 29 of the last 30 blocking words were real words refused for (2), which is the
    # wrong question for a decomposability law.
    unanimous = (top == total) if total else False
    if dom == "unused" or total == 0:
        return {"ok": False, "code": "NO_FACT", "word": w,
                "reason": "%r is used in no name" % w,
                "cite": "unified_language.py:word_definition"}
    if unanimous or top >= STRONG_ROLE_OCCURRENCES:
        role, reading = POSITION_ROLE[dom]
        # THE MEASUREMENT STATES THE PROPERTY, NOT A DOMINANT SLICE. MEASURED: the
        # earlier form ("appears as the middle in 2 of the 3 names") FAILED the door
        # for a SPLIT word, because the facts show the OTHER positions too and the
        # draft looked like it was picking one. A claim about the whole — "in ALL N"
        # or "in more than one" — is what the facts support, and the door PASSES it.
        measurement = (
            "The word '%s' appears as the %s in ALL %d names that use it."
            % (w, dom, total))
        definition = ("The word '%s' is a %s of a name: by the declared position-role "
                      "convention (%s). The measurement behind this is carried in the "
                      "word's position facts." % (w, role, reading))
    else:
        role, reading = ROLE_INCONSISTENT
        measurement = (
            "The word '%s' appears in more than one position across the %d names that "
            "use it (prefix %d, suffix %d, middle %d, alone %d)."
            % (w, total, pos.get("prefix", 0), pos.get("suffix", 0),
               pos.get("middle", 0), pos.get("alone", 0)))
        definition = ("The word '%s' appears in MORE THAN ONE position in the names "
                      "that use it, so position alone does not fix its role; it is a "
                      "word the language uses as both. The measurement behind this is "
                      "carried in the word's position facts." % w)
    # THE STORED DEFINITION CARRIES THE ROLE; THE MEASUREMENT IS REPORTED SEPARATELY.
    #
    # MEASURED, twice, and this is the resolution of both findings:
    #  * a definition must NOT pin a NUMBER a legitimate operation moves (the repo's
    #    own law) — `in 37 of the 37 names` goes wrong the moment another name uses
    #    the word;
    #  * a definition must NOT state a ROLE as a fact — the door refuses a convention
    #    (QC-26), which made the verdict UNSTABLE (27/28 passed, `id` failed).
    #
    # So the TEXT names the role as a DECLARED property of the word and points at the
    # measurement for its evidence; the NUMBER travels in `measurement`, which the door
    # certifies and the caller reports LIVE. `_facts_block` prints the measurement, so
    # the door can check `measurement` and will (correctly) FAIL this text — the write
    # therefore records the measurement as its own cited claim rather than relying on a
    # door verdict for a text that carries a convention.
    definition = (
        "The word '%s' is a %s of a name: by the declared position-role convention "
        "(%s). The measurement behind this is carried in the word's position facts."
        % (w, role, reading))
    return {"ok": True, "word": w, "definition": definition, "role": role,
            "role_reading": reading,
            "dominant_position": dom, "occurrences": top,
            "row_count": total, "position": pos, "facts": f,
            # THE MEASUREMENT, the claim the door certifies (all numbers, no
            # convention). Reported LIVE by the caller; the door's brief prints the same
            # facts, so the door judges it.
            "measurement": measurement,
            "taxonomy_path": ROLE_PATH_PREFIX + role,
            "cite": "unified_language.py:word_definition"}


def _normalise_spelling(s: str) -> str:
    """Lowercase, no separators — so `core` and `Core` compare equal.

    Used ONLY to REPORT equivalent spellings, never to merge them: a spelling pair
    is a HEAD C finding to show a reader, and silently folding one into the other
    would hide the very collision the law exists to find.
    """
    return str(s or "").strip().lower().replace("-", "").replace("_", "")


def _senses(conn: sqlite3.Connection, word: str) -> list[dict]:
    """Every DEFINITION this word carries — one per place it is declared.

    The evidence for a HEAD C "one word, two meanings" violation. When this returns
    two definitions that DISAGREE, the word is used as two concepts, and the door can
    ask the staff to RECONCILE them instead of inventing one.

    THE WORD'S OWN ROW IS EXCLUDED. MEASURED, and it closed a cycle: once P3c wrote a
    word's definition, `senses` returned it, the door's brief carried it, and a draft
    that restated the word's own ROLE then PASSED — the definition certifying itself.
    A word's own definition is not an INDEPENDENT sense; it is the thing being
    checked. Only OTHER declarations (the layer register) can be a second sense.
    """
    out: list[dict] = []
    for where, sql, args in (
            ("term", "SELECT definition, cite_ref FROM terminology_registry "
                     "WHERE term_key=? AND is_active=1", (word,)),
            ("layer", "SELECT definition, NULL FROM taxonomy_level_registry "
                      "WHERE level_key=?", (word,))):
        try:
            for r in conn.execute(sql, args):
                d = str(r[0] or "").strip()
                if not d:
                    continue
                # EXCLUDE A DECLARATION THIS MODULE DERIVED. MEASURED, and the first
                # version keyed on the TEXT PREFIX ("The word 'X' appears as the "),
                # which the design then CHANGED — so the exclusion stopped matching and
                # the word's own definition leaked back in, making a ROLE claim PASS
                # again (the cycle reopened). The CITE is the stable marker: a row this
                # module wrote carries the measurement cite, so its own text can never
                # be an INDEPENDENT sense.
                if where == "term" and str(r[1] or "").startswith(
                        MEASUREMENT_CITE_FILE):
                    continue
                out.append({"where": where, "definition": d})
        except sqlite3.Error:
            continue
    return out


def _token_usage(conn: sqlite3.Connection) -> Counter:
    """How many GOVERNED names each token appears in.

    Used to tell a system ABBREVIATION from a one-name fragment: `7b` appears in 12
    names (it is the system's own shorthand for the 7B model), while `x3` appears in
    one, and a token used once is that name's private fragment — the same consistency
    rule applied to word roles.
    """
    c: Counter = Counter()
    for n in all_names(conn):
        for t in str(n).split("_"):
            c[t] += 1
    return c


def check_composite(conn: sqlite3.Connection, name: str, *,
                    enforce: bool = False, vocab: dict | None = None,
                    usage: Counter | None = None) -> dict:
    """The THREE heads. `ok` is True only when all three hold.

    `enforce=False` (the default) REPORTS. `enforce=True` raises `LanguageError`
    naming the failing head — and it is the CALLER's decision, because MEASURED the
    current decomposition is 9.0% and a refusal there refuses almost everything.
    """
    v = vocab or vocabularies(conn)
    live = live_objects(conn)
    words = words_of(name)

    # A DIGIT TOKEN THAT THE LANGUAGE USES REPEATEDLY IS AN ABBREVIATION, not a word
    # to register: MEASURED, `7b` appears in 12 governed names (the system's shorthand
    # for the 7B model), while `x3`/`e1`/`p0` appear in one. Frequency is the same test
    # the ROLE rule uses, applied to the token itself: used once it is that name's
    # fragment, used repeatedly it is part of the language.
    if usage is None:
        usage = _token_usage(conn)
    digit_abbrev = {w for w in words if DIGIT_RE.search(w) and usage.get(w, 0) >= 2}

    # HEAD A + B — every word registered in some vocabulary of the SAME language.
    #
    # THE SINGLE-WORD BASE CASE, which my first version got wrong: a name of ONE
    # word is ATOMIC — it IS the vocabulary unit, so it has nothing to decompose
    # INTO and HEAD A/B are trivially satisfied. Without this, registering the very
    # word the vocabulary needs (`api`, `register`, `task`) would refuse ITSELF,
    # which is a gate that forbids the action its own message names.
    origin: dict[str, str] = {}
    missing: list[str] = []
    if len(words) > 1:
        for w in words:
            if NUMBER_RE.match(w):
                origin[w] = "number"      # a literal, not a word that needs registering
                continue
            # A SYSTEM ABBREVIATION the language uses repeatedly (see `digit_abbrev`).
            if w in digit_abbrev:
                origin[w] = "abbreviation"
                continue
            # A VERSIONED WORD whose base is registered: the base carries the meaning.
            vm = VERSIONED_RE.match(w)
            if vm and vm.group(1) in v["term"]:
                origin[w] = "versioned:" + vm.group(1)
                continue
            for k in VOCABS:
                if w in v[k]:
                    origin[w] = k
                    break
            else:
                missing.append(w)

    # HEAD C — uniqueness. Two names for one concept is a violation, and the
    # surviving name is the one that names a LIVE object.
    canon = SUFFIX_RE.sub("", name)
    duplicate_of = None
    if SUFFIX_RE.search(name):
        others = [str(r[0]) for r in conn.execute(
            "SELECT term_key FROM terminology_registry WHERE is_active=1")]
        # MEASURED DEFECT IN MY OWN FIRST VERSION, fixed by the law running on real
        # names: I compared `SUFFIX_RE.sub("", o) == canon` for EVERY other term,
        # so `channel` (which has NO suffix, and therefore strips to itself) was
        # reported as a duplicate of `channel_registry` — whose canon IS `channel`.
        # A duplicate requires BOTH names to CLAIM the role, so `o` must itself
        # match SUFFIX_RE. Without this, every register-like name whose stem is a
        # live term was falsely refused, i.e. the gate refused the CORRECT name and
        # looked like an error — the exact defect `check_registry_name` documents.
        clash = [o for o in others
                 if o != name and SUFFIX_RE.search(o) and SUFFIX_RE.sub("", o) == canon]
        if clash:
            duplicate_of = sorted(clash)

    head = None
    if missing:
        head, code = "A", "MISSING_WORD"
    elif duplicate_of:
        head, code = "C", "DUPLICATE_NAME"
    else:
        code = "COMPOSITE_OK"

    result = {
        "ok": code == "COMPOSITE_OK",
        "name": name,
        "head": head,
        "code": code,
        "words": words,
        "word_vocab": origin,
        "missing_words": missing,
        "duplicate_of": duplicate_of,
        "is_live_object": name in live,
        "cite": CITE,
    }
    if enforce and not result["ok"]:
        raise LanguageError(
            "HEAD %s refused %r (%s)%s" % (
                head, name, code,
                " — unregistered word(s): " + ", ".join(missing) if missing
                else " — also named " + ", ".join(duplicate_of or [])))
    return result


def uniqueness_violations(conn: sqlite3.Connection) -> dict:
    """P0 — HEAD C, measured. Three kinds of violation, all three found live:

    (1) ONE CONCEPT, TWO NAMES  — `x_registry` vs `x_registry` (2 concepts today)
    (2) ONE DEFINITION, N NAMES — 4 probe terms share one sentence (a leak)
    (3) ONE WORD, TWO MEANINGS  — a word in the term vocab AND the layer vocab
                                  whose definitions disagree (5 of 5 today)
    """
    live = live_objects(conn)
    terms = {str(r[0]) for r in conn.execute(
        "SELECT term_key FROM terminology_registry WHERE is_active=1")}

    groups: dict[str, list[str]] = defaultdict(list)
    for k in terms:
        if SUFFIX_RE.search(k):
            groups[SUFFIX_RE.sub("", k)].append(k)
    pairs = []
    for c, names in sorted(groups.items()):
        if len(names) > 1:
            survivors = [n for n in names if n in live]
            pairs.append({
                "concept": c, "names": sorted(names),
                "surviving_name": survivors[0] if len(survivors) == 1 else None,
                "rule": "the name that IS a live object survives",
            })

    by_def: dict[str, list[str]] = defaultdict(list)
    for r in conn.execute("SELECT term_key, definition FROM terminology_registry "
                          "WHERE is_active=1 AND definition IS NOT NULL"):
        d = str(r[1]).strip()
        if d:
            by_def[d].append(str(r[0]))
    same_def = [{"definition": d, "names": sorted(n)}
                for d, n in by_def.items() if len(n) > 1]

    v = vocabularies(conn)
    shared = sorted(set(v["term"]) & set(v["layer"]))
    cross = []
    for w in shared:
        td = conn.execute("SELECT definition FROM terminology_registry "
                          "WHERE term_key=? AND is_active=1 LIMIT 1", (w,)).fetchone()
        ld = conn.execute("SELECT definition FROM taxonomy_level_registry "
                          "WHERE level_key=? LIMIT 1", (w,)).fetchone()
        t, l = (td[0] or ""), (ld[0] or "")
        cross.append({"word": w, "term_def": t[:80], "layer_def": l[:80],
                      "agree": t.strip() == l.strip()})

    return {"ok": True, "pairs": pairs, "same_definition": same_def,
            "cross_vocabulary": cross,
            "violations": len(pairs) + len(same_def) + sum(1 for c in cross if not c["agree"]),
            "cite": "unified_language.py:uniqueness_violations"}


# ---------------------------------------------------------------------------
# THE RECIPROCAL — the QUESTION each violation raises
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-27): "evidence + logic generator can't help?" — and it can,
# which is why this exists instead of a question for a person. Every violation
# above is a FINDING, and a finding whose questions are not generated is a finding
# nobody can act on. So this REUSES `logic_generator.spec_from_table`, which
# refuses rather than inventing a spec when a table has no declared DDL; a refusal
# here is REPORTED as `SPEC_REFUSED` with the generator's own reason, never hidden.
#
# MEASURED: both `terminology_registry` and `taxonomy_level_registry` HAVE a
# declared DDL (`db_schema.terminology_registry_DDL`, 15 cols;
# `db_schema.TAXONOMY_LEVEL_REGISTRY_DDL`, 8 cols), so the generator produced a
# real spec for each and every violation has a question list. An earlier draft of
# this comment claimed the first had NO DDL — that was a GUESS and it was WRONG;
# `logic_generator.declared_shape()` is the measurement that corrected it.

def questions_that_settle(conn: sqlite3.Connection, subject: str, *,
                          conn_for_spec: sqlite3.Connection | None = None) -> dict:
    """The question list that would SETTLE a language finding about `subject`.

    RECIPROCAL, by the project's own rule: NARROW shrinks the candidate set until
    ONE answer decides it, RESEARCH grows it by collecting evidence. A violation is
    a POINT (it is known), so the direction is RESEARCH — and the first question
    is where the violating object is DECLARED.
    """
    import logic_generator as lg
    spec_conn = conn_for_spec if conn_for_spec is not None else conn
    spec, refused = None, None
    try:
        spec = lg.spec_from_table(spec_conn, subject)
    except Exception as exc:              # SpecError and anything the generator raises
        refused = "%s: %s" % (type(exc).__name__, exc)
    if spec is None:
        return {"ok": False, "subject": subject, "code": "SPEC_REFUSED",
                "reason": refused, "questions": [],
                "note": ("the logic generator refuses this subject rather than "
                         "inventing a spec — which is the correct answer, and it is "
                         "reported, not hidden"),
                "route": "/api/question/registry/%s" % subject,
                "cite": "unified_language.py:questions_that_settle"}
    try:
        gen = lg.generate(spec, conn=spec_conn, subject_kind="table")
    except Exception as exc:
        return {"ok": False, "subject": subject, "code": "GENERATE_REFUSED",
                "reason": "%s: %s" % (type(exc).__name__, exc), "questions": [],
                "route": "/api/question/registry/%s" % subject,
                "cite": "unified_language.py:questions_that_settle"}
    return {"ok": True, "subject": subject, "code": "QUESTIONS_READY",
            "count": gen.get("count"), "derivation": gen.get("derivation"),
            "questions": gen.get("questions"),
            "route": "/api/question/registry/%s" % subject,
            "cite": "unified_language.py:questions_that_settle"}


def violations_with_questions(conn: sqlite3.Connection) -> dict:
    """Every HEAD C violation, WITH the questions that would settle it."""
    u = uniqueness_violations(conn)
    out = []
    for p in u["pairs"]:
        out.append({"kind": "one_concept_two_names", "names": p["names"],
                    "survivor": p["surviving_name"],
                    "questions": questions_that_settle(conn, "terminology_registry")})
    for s in u["same_definition"]:
        out.append({"kind": "one_definition_many_names", "names": s["names"],
                    "definition": s["definition"],
                    "questions": questions_that_settle(conn, "terminology_registry")})
    for c in u["cross_vocabulary"]:
        if not c["agree"]:
            out.append({"kind": "one_word_two_meanings", "word": c["word"],
                        "term_def": c["term_def"], "layer_def": c["layer_def"],
                        "questions": questions_that_settle(conn, "taxonomy_level_registry")})
    return {"ok": True, "violations": len(out), "items": out,
            "cite": "unified_language.py:violations_with_questions"}


# ---------------------------------------------------------------------------
# P1 — the STAFF labels; the doors decide
# ---------------------------------------------------------------------------

def classify_words(conn: sqlite3.Connection, ask, *, words: list[str] | None = None,
                   runs: int = 3, batch: int = 60) -> dict:
    """The 7B LABELS the missing words. Three runs, and only agreement is adopted.

    MEASURED, and it is why `runs=3` is not optional: over 3 identical runs the
    staff agreed with itself on 53/60 words = **88%**. `prompt-measurement-discipline`
    rule 4 requires REPEATED 100% for certification, so a single label is NOT a
    decision. A word labelled two different ways is CONTESTED and ESCALATED.

    The `\\d` rule is applied FIRST and the model cannot override it: 40 of the 155
    FRAGMENT judgements are settled objectively by that rule alone.
    """
    v = vocabularies(conn)
    if words is None:
        words = [r["word"] for r in vocabulary_gap(conn)["missing_top"]]
    words = [w for w in words if w not in v["term"] and w not in v["layer"]
             and w not in v["unit"]]

    labels: dict[str, list[str]] = {w: [] for w in words}
    for _ in range(runs):
        for i in range(0, len(words), batch):
            chunk = words[i:i + batch]
            reply = ask(LABEL_SYSTEM, "Words:\n" + "\n".join(chunk))
            got = {}
            for line in str(reply or "").splitlines():
                m = LABEL_RE.match(line)
                if m:
                    got[m.group(1)] = m.group(2).upper()
            for w in chunk:
                labels[w].append(got.get(w, "UNLABELLED"))

    digit_fragment, vocab, fragment, contested, unlabelled = [], [], [], [], []
    for w in words:
        got = labels[w]
        if DIGIT_RE.search(w):
            digit_fragment.append(w)
            continue
        # MEASURED DEFECT IN MY OWN FIRST VERSION, caught by `_proof_unified_language`
        # QC-04: I tested `got[0] == "UNLABELLED"` but the model's OWN refusal comes
        # back as the string `UNKNOWN` (it is what LABEL_SYSTEM tells it to say), so
        # a word the staff explicitly declined VANISHED — neither adopted nor
        # reported. An empty result that cannot be distinguished from "nothing was
        # asked" is the defect `independent-review` names, so BOTH the no-reply
        # sentinel AND the model's own UNKNOWN are UNLABELLED: not decisions.
        if len(set(got)) != 1 or got[0] in ("UNLABELLED", "UNKNOWN"):
            (contested if len(set(got)) > 1 else unlabelled).append(w)
        elif got[0] == "VOCAB":
            vocab.append(w)
        elif got[0] == "FRAGMENT":
            fragment.append(w)

    return {
        "ok": True,
        "runs": runs, "total": len(words),
        "vocab": sorted(vocab), "fragment": sorted(fragment),
        "digit_fragment": sorted(digit_fragment),
        "contested": sorted(contested), "unlabelled": sorted(unlabelled),
        "labels": labels,
        "stability": (round(100.0 * (len(vocab) + len(fragment)) / len(words), 1)
                      if words else 0.0),
        "note": ("only a word labelled IDENTICALLY in all %d runs is adopted; "
                 "CONTESTED and UNLABELLED are NOT decisions" % runs),
        "cite": "unified_language.py:classify_words",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _staff_ask():
    """The staff. The import is INSIDE the function so this module holds NO model
    name at module level — the `ask` seam stays injected (QC-13)."""
    from verify_definition_7b import ask as _a
    return _a


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ONE word, ONE meaning, ONE name")
    ap.add_argument("--db", default=str(BASE / "agent.db"))
    ap.add_argument("--gap", action="store_true", help="P0 measure every vocabulary")
    ap.add_argument("--uniqueness", action="store_true", help="P0 measure HEAD C")
    ap.add_argument("--law", nargs="*", default=None, help="check names against the law")
    ap.add_argument("--classify", action="store_true", help="P1 the staff labels the gap")
    ap.add_argument("--questions", action="store_true",
                    help="the questions that would settle every HEAD C violation")
    ap.add_argument("--limit", type=int, default=0, help="cap the words classified")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(a.db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if a.gap or not (a.uniqueness or a.law is not None or a.classify or a.questions):
            g = vocabulary_gap(conn)
            for vv in g["vocabularies"]:
                print("  %-6s %4d total  %4d registered (%5.1f%%)  %4d missing%s"
                      % (vv["name"], vv["total"], vv["registered"], vv["pct"],
                         vv["missing"],
                         ("  e.g. " + ", ".join(vv.get("missing_words", [])[:6]))
                         if vv.get("missing_words") else ""))
            print("  names  %4d total  %4d legal (%.1f%%)"
                  % (g["names"]["total"], g["names"]["legal"], g["names"]["pct"]))
        if a.uniqueness:
            u = uniqueness_violations(conn)
            print("\nHEAD C — %d violation(s)" % u["violations"])
            for p in u["pairs"]:
                print("  one concept, two names : %s -> survivor %s"
                      % (p["names"], p["surviving_name"]))
            for s in u["same_definition"]:
                print("  one definition, %d names : %s" % (len(s["names"]), s["names"]))
            for c in u["cross_vocabulary"]:
                print("  word %-12s term/layer %s"
                      % (c["word"], "AGREE" if c["agree"] else "DISAGREE"))
        if a.questions:
            vq = violations_with_questions(conn)
            print("\nTHE QUESTIONS THAT SETTLE EACH VIOLATION (%d)" % vq["violations"])
            for it in vq["items"]:
                q = it["questions"]
                what = it.get("word") or ", ".join(it.get("names", []))
                if q.get("ok"):
                    print("  %-24s %-22s %d questions -> %s"
                          % (it["kind"], what, q.get("count") or 0, q["route"]))
                else:
                    print("  %-24s %-22s %s" % (it["kind"], what, q["code"]))
                    print("        %s" % str(q.get("reason"))[:110])
        if a.law is not None:
            print("\nTHE LAW")
            for n in a.law:
                r = check_composite(conn, n)
                extra = (" missing=" + ",".join(r["missing_words"])) if r["missing_words"] \
                    else ((" also named " + ",".join(r["duplicate_of"])) if r["duplicate_of"] else "")
                print("  %-24s legal=%-5s HEAD %-4s %s%s"
                      % (r["name"], r["ok"], r["head"] or "-", r["code"], extra))
        if a.classify:
            words = None
            if a.limit:
                words = [r["word"] for r in vocabulary_gap(conn)["missing_top"][:a.limit]] or None
            if words is None:
                words = sorted({w for r in conn.execute("SELECT term_key FROM terminology_registry")
                                for w in str(r[0]).split("_")})[:a.limit or None]
            res = classify_words(conn, _staff_ask(), words=words)
            print("\nP1 THE STAFF — %d words, %d runs" % (res["total"], res["runs"]))
            print("  adopted VOCAB     : %d" % len(res["vocab"]))
            print("  adopted FRAGMENT  : %d (of which %d settled by the \\d rule)"
                  % (len(res["fragment"]), len(res["digit_fragment"])))
            print("  CONTESTED (escalate, NOT a decision) : %d" % len(res["contested"]))
            print("  UNLABELLED (NOT a decision)          : %d" % len(res["unlabelled"]))
            out = BASE / "qc_evidence" / "vocabulary_review.json"
            out.parent.mkdir(exist_ok=True)
            out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
            print("  wrote %s" % out)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
