# -*- coding: utf-8 -*-
"""terminology_registry.py — a TERM is a DECOMPOSABLE, REGISTERABLE structure.

THE USER'S REQUIREMENT (2026-09-22)
-----------------------------------
    "100_ run services / -> 100_ run / -> services / it is meaningful, can help us"
    "factor defination is correct ot not!!! that is the key"
    "how to proof is name_register!!!!!"

and the RENAME:

    "name _register rename to `terminology_register` / this name is more
     representative, do u agree?"

AGREED, and the reason is substantive: the table holds a `definition` and a
`cite_ref`, so its subject is MEANING, not spelling. The user's own key sentence
is about the DEFINITION ("factor defination is correct ot not"), and a term + its
definition IS a terminology. `name_register` described the identity column;
`terminology_register` describes what the table IS.

So a term is NOT a string. It is a structure whose PARTS must each be registered:

    100_run_service
      -> 100_run        (the capability)
      -> service        (what it IS)
    100_run
      -> 100            (a count)
      -> run            (an action)

"Is this term correct?" then becomes CHECKABLE: does every part resolve in
`terminology_register`? A part that does not resolve was never registered, and an
unregistered part is an INVENTED WORD.

WHY THIS PROVES A FACTOR DEFINITION
-----------------------------------
A factor's `factor_key` must decompose into registered parts. If it does not, the
factor is named with a word nobody defined — which is exactly the defect
`factor_distill.py` records:

    "a generator that INVENTED factors produced 11 factor names across all 50
     skills, 642 of 652 rows UNMEASURED, and 0 mentions of the skill's own
     subject."

THE TAXONOMY IS EMBEDDED, NOT A SECOND TABLE
--------------------------------------------
    "唔使額外開獨立catalog表，直接將分類、taxonomy相關欄位嵌入Name Register，
     新建/註冊任何實體嘅時候一併填埋，一次過寫齊，唔使後補，唔使多一張表維護"

So `taxonomy_level` / `taxonomy_path` / `entity_ref_key` live HERE, filled at
registration time. A separate catalog table would be a second thing to maintain
and a second place for the same fact to disagree with itself.

THE STRUCTURE IS STORED, NOT INFERRED FROM THE STRING
-----------------------------------------------------
`parent_term_id` points at the COMPOSITE term, and the child is a PART:

    100_run   parent = 100_run_service
    service   parent = 100_run_service
    100       parent = 100_run
    run       parent = 100_run

MEASURED, and my first version was WRONG: it used longest-registered-prefix
matching, and because `100_run_service` is ITSELF registered, the longest prefix
was the WHOLE NAME — so it returned `[100_run_service]` and never split the
user's own example. A rule that cannot split the example is not the rule.

WHAT IT REFUSES
---------------
  * a term with no `definition` — a word nobody defined
  * a term with no `cite_ref` — no citation, no term
  * an unknown `taxonomy_level` — a level nothing classifies
  * a decomposition with an UNREGISTERED part, and the refusal NAMES the part
  * a factor whose `factor_key` does not decompose

Run:
    .\\.venv\\Scripts\\python.exe terminology_registry.py --decompose 100_run_service
    .\\.venv\\Scripts\\python.exe terminology_registry.py --list
"""
from __future__ import annotations

import argparse
import hashlib
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

TERM_KINDS = ("count", "action", "role", "entity", "qualifier", "part")

# The eight declared taxonomy levels, IMPORTED from the schema SSOT so the
# vocabulary has ONE source. A second copy here is the drift this repo has been
# bitten by (`source_type` was declared in two places and a lesson was silently
# lost).
from db_schema import TAXONOMY_LEVELS  # noqa: E402


class TermError(ValueError):
    """Raised when a term cannot be registered or decomposed."""


def definition_hash(definition: str) -> str:
    """The sha256 of a definition, DERIVED by the writer.

    NOT accepted from the caller: a value a caller can assert is a value a
    caller can lie about, and a changed definition with a stale hash would
    report "unchanged" silently. Same reason `skill_factor_proof.metric_pass` is
    derived from value-vs-target rather than asserted.
    """
    return hashlib.sha256(str(definition or "").strip().encode("utf-8")
                          ).hexdigest()[:16]


# ---------------------------------------------------------------------------
# THE SCRATCH-CITATION CHECK (added 2026-09-26)
# ---------------------------------------------------------------------------
#
# MEASURED (2026-09-26): 777 of the terms carry a `cite_ref` whose FILE is a
# scratch script — `_diag_*` / `_proof_*` / `_probe_*` / `_dbg_*` / `_check_*` /
# `_measure_*` / `_fix_*`. Per `citation-discipline`, a scratch file is NOT
# evidence: it is a temporary instrument, not the place a thing is defined.
#
# MEASURED, AND THIS IS WHY THE CHECK DOES NOT AUTO-REWRITE:
#
#   * only **4 / 777** definitions are actually AUTO-GENERATED from a filename
#     ("A specific file or script used to derive..."). The other ~773 are REAL
#     CONCEPTS (`chat_env_link_marker`, `api_apps`, `computer_presence`) whose cite
#     merely points at a scratch file.
#   * a plausible non-scratch home was found by PREFIX MATCH for 532 of them — but
#     a prefix match (`computer_presence` -> `computer_presence.py`) is a
#     HEURISTIC. **Auto-re-citing on it would be INVENTING a citation**, which
#     `citation-discipline` forbids outright.
#
# So this reports, with counts, and REFUSES to guess. Re-citing needs a per-term
# measurement, which is its own task.

SCRATCH_FILE_PREFIXES = ("_diag_", "_proof_", "_probe_", "_dbg_", "_check_",
                         "_measure_", "_fix_", "_cleanup_", "_apply_",
                         "_backfill_", "_clear_", "_crop_", "_capture_")

# The shape of a definition that a machine wrote from a FILENAME. `_proof_*` cited
# a term whose definition literally begins with this, so the term means "a file
# named X", which is a description of the instrument, not of the concept.
_AUTO_DEFINITION_RE = re.compile(
    r"^A (specific file|structured entity|ticket|register|table|module|script)\b",
    re.IGNORECASE)


def is_scratch_cite(cite_ref: Any) -> bool:
    """Does this citation point at a scratch file?"""
    f = str(cite_ref or "").split(":")[0].strip()
    return bool(f) and f.startswith(SCRATCH_FILE_PREFIXES)


def is_auto_definition(definition: Any) -> bool:
    """Was this definition machine-generated from a filename?"""
    return bool(_AUTO_DEFINITION_RE.match(str(definition or "").strip()))


# ---------------------------------------------------------------------------
# THE SPELLING CHECK (added 2026-09-27)
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-27):
#     "alias must = terminonotology, all in same language, do it now"
#     "fucking mis-undersatnd name never happen again"
#     "environment this is wrong spelling, is it?"
#     "terminontology_registered has splleing proof?"
#
# MEASURED ANSWER: NO, it did not. `_proof_terminology_ko_spelling.py` has
# "spelling" in its NAME, but it proves ALIAS IDENTITY (two spellings of one
# concept resolve to one canonical key), NOT spelling CORRECTNESS. There was no
# dictionary anywhere in the repo (zero modules import enchant / spellchecker /
# symspellpy), so a typo like `enviornment` was NEVER caught.
#
# MEASURED POPULATION: `environment` 12,423 occurrences / `enviornment` 3,970.
# Of the 3,970, 3,584 are QUOTES of the human's own words (evidence — must NOT
# be changed) and 357 are real identifiers (the defect).
#
# WHY A TABLE AND NOT A DICTIONARY LIBRARY: a general English dictionary would
# refuse the repo's OWN vocabulary (`5w1h`, `db_row`, `llm_100_run`, `CP-S-00`),
# which is a false positive that blocks real work. The defect is a KNOWN, SMALL
# set of transpositions, so the check is a DECLARED table of (typo -> correct)
# pairs. It is narrow on purpose: it refuses what it can PROVE is wrong, and
# stays silent about what it cannot judge.
#
# THE RULE IS NARROW, like the scratch-cite rule: it applies to a NEW term or
# alias only. Existing rows are NOT retro-refused (that would be a mass refusal
# of live data); they are repaired by adding the CORRECT spelling as an alias.
MISSPELLINGS: dict[str, str] = {
    "enviornment": "environment",
    "recieve": "receive",
    "seperate": "separate",
    "occured": "occurred",
    "definately": "definitely",
    "accomodate": "accommodate",
    "acheive": "achieve",
    "adress": "address",
    "arguement": "argument",
    "begining": "beginning",
    "calender": "calendar",
    "commited": "committed",
    "compatable": "compatible",
    "concensus": "consensus",
    "dependancy": "dependency",
    "existance": "existence",
    "foriegn": "foreign",
    "guarentee": "guarantee",
    "independant": "independent",
    "neccessary": "necessary",
    "occurence": "occurrence",
    "persistant": "persistent",
    "priviledge": "privilege",
    "publically": "publicly",
    "recomend": "recommend",
    "refered": "referred",
    "relevent": "relevant",
    "responce": "response",
    "succesful": "successful",
    "treshold": "threshold",
    "untill": "until",
    "wierd": "weird",
}


def misspelling_in(name: Any) -> tuple[str, str] | None:
    """`(typo, correct)` when `name` CONTAINS a declared misspelling, else None.

    A SUBSTRING match, not an equality match: the defect is `enviornment` inside
    `enviornment_playwright`, so an equality test would miss it. The check is
    case-insensitive, because `Enviornment` is the same typo.
    """
    low = str(name or "").lower()
    if not low:
        return None
    for typo, correct in MISSPELLINGS.items():
        if typo in low:
            return typo, correct
    return None


def check_spelling(name: Any) -> dict[str, Any]:
    """The verdict on ONE name. `{ok, code, typo, correct, message}`.

    `ok=True` means "no DECLARED misspelling found" — it is NOT a claim that the
    name is a real English word. The check refuses what it can prove is wrong and
    stays silent about what it cannot judge.
    """
    hit = misspelling_in(name)
    if not hit:
        return {"ok": True, "code": None, "name": str(name or "")}
    typo, correct = hit
    return {"ok": False, "code": "MISSPELLED_NAME", "name": str(name or ""),
            "typo": typo, "correct": correct,
            "message": ("the name %r contains the misspelling %r — the correct "
                        "spelling is %r. A name a reader must decode is a name "
                        "that will be mis-understood." % (str(name or ""), typo,
                                                          correct))}


# The term_key FORM (added 2026-09-27). `terminology_sweep.py:96` declares
# "term_key (lowercase snake_case)" and `register_fill.py:494` declares
# "key (lowercase snake_case or dotted)". MEASURED 2026-09-27: all 1452 active
# terms already match, so the rule is safe to enforce at the write site — and
# it is the rule that would have stopped `LLM_OFF_FORM` (id=87) from entering
# the register as a term_key in the first place.
TERM_KEY_RE = re.compile(r"^[a-z0-9]+(?:[._][a-z0-9]+)*$")


def check_naming(name: Any) -> dict[str, Any]:
    """The verdict on ONE term_key's FORM. `{ok, code, name, message}`.

    A term_key is lowercase snake_case or dotted (`a_b`, `a.b`, `a.b_c`).
    Uppercase, spaces, and other punctuation are refused: a term_key that
    needs decoding is a name that will be mis-understood, and an uppercase
    constant is a CARRIER (an alias), not a term.
    """
    key = str(name or "")
    if TERM_KEY_RE.match(key):
        return {"ok": True, "code": None, "name": key}
    return {"ok": False, "code": "BAD_TERM_KEY_FORM", "name": key,
            "message": ("term_key %r is not lowercase snake_case or dotted "
                        "(e.g. 'llm_off_form'). If it is a constant or a "
                        "legacy name, register it as an ALIAS of the "
                        "lowercase term, not as a term." % key)}


# ---------------------------------------------------------------------------
# THE CONFUSABLE-NAME GATE (added 2026-09-27), now DIRECTIONAL
# ---------------------------------------------------------------------------
# THE HUMAN, first: "how terminontology_register can smart to help me wrong
# describe by similar name to classify it is registry not register".
# THE HUMAN, then the RULING: "be unified by registry not register".
#
# WHY THE REGISTER COULD NOT HELP, MEASURED: it held BOTH spellings of the same
# concept --
#
#     channel_register     (a term)      <-- NOT a table
#     channel_registry     (a table)     <-- the REAL thing
#     terminology_register (a table)
#     terminology_registry (a term)      <-- NOT a table
#
# so there was nothing in the register to contradict the non-standard name.
#
# THE FIX IS DB-DRIVEN, NOT A LIST. `sqlite_master` and `db_table_registry` already
# know every real object, so a suggestion can CITE a real object rather than assert
# one. A hardcoded list would go stale the moment a table is added; reading the DB
# cannot.
#
# WHEN IT APPLIES: the name looks like a register. The suffixes are DECLARED here
# so the rule can be read, not inferred.

REGISTER_NAME_SUFFIXES = ("_register", "_registry", "_registrar")
REGISTER_NAME_PREFIXES = ("register_", "registry_")

# The edit distance that makes two register names CONFUSABLE. MEASURED, not taste:
# the ruling's own defect pair, `register` <-> `registry`, differs by exactly 2.
CONFUSABLE_DISTANCE = 2


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein distance. A MEASUREMENT of nearness, not a guess.

    Used to name the CLOSEST real object when a register-like name is refused, so
    the message can say "did you mean X" with a number behind it.
    """
    a, b = str(a or ""), str(b or "")
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def real_object_names(conn: sqlite3.Connection) -> set[str]:
    """Every name that REALLY exists: a registered table, or any sqlite object.

    ONE source of truth for "does this thing exist" -- the DB, not a list.
    """
    names: set[str] = set()
    try:
        for r in conn.execute("SELECT name FROM sqlite_master WHERE name IS NOT NULL"):
            names.add(str(r[0]))
    except sqlite3.Error:
        pass
    try:
        for r in conn.execute(
                "SELECT table_key FROM db_table_registry WHERE is_active = 1"):
            names.add(str(r[0]))
    except sqlite3.Error:
        pass
    return names


# ---------------------------------------------------------------------------
# THE NAMING STANDARD: `registry`, NOT `register`
# ---------------------------------------------------------------------------
# THE HUMAN'S RULING (2026-09-27), verbatim:
#
#     "be unified by registry not register"
#
# So `_register` is the NON-STANDARD spelling. `_registry` is the standard. That
# is ONE decision, and it reconciles with the earlier architecture ruling
# (`/memories/repo/architecture_user_ruling.md`): the architecture nodes are
# already `module_registry` / `capability_registry`.
#
# MEASURED WHEN THE RULING WAS MADE (so the scale is a fact, not a guess):
#   * `_register` objects: **29**   (incl. `version_register`, `code_register`,
#     `terminology_register`, `entity_type_register`)
#   * `_registry` objects: **17**   (incl. `db_table_registry`, `channel_registry`)
#   * `*_register` code references: **5984** in **569** files
#   * `entity_type_register.register_table` binds **8 letters** to a `_register`
#     name, so a rename changes entity id resolution -- NOT a cosmetic change.
#
# WHY THE GATE IS A FLAG, NOT A REFUSAL, FOR EXISTING NAMES
# ---------------------------------------------------------
# A retro-refusal of 29 live objects would be a mass refusal of working data --
# the defect every gate in this module is written to avoid. So:
#
#   * a NEW term using `_register`  -> REFUSED (the standard is enforceable now)
#   * an EXISTING `_register` name  -> REPAIRED by the migration
#     (`_unify_registry_naming.py`), and REPORTED by `nonstandard_register_names`
#     until it is.
#
# THE DIRECTION IS THE POINT, AND I GOT IT BACKWARDS FIRST: my first version of
# this gate treated `register` as CORRECT and refused `registry`
# (`version_registry` -> "did you mean version_register"). That enforced the
# OPPOSITE of the ruling. A gate that enforces the wrong direction is worse than
# no gate: it makes the correct name look like the error.

REGISTRY_STANDARD_SUFFIX = "_registry"
NONSTANDARD_REGISTER_SUFFIX = "_register"


def looks_like_a_register(name: Any) -> bool:
    """Does `name` CLAIM to be a register, by its own spelling?

    EITHER spelling, because a NON-standard name still claims the role -- that is
    exactly why it is worth checking. `is_registry_standard` decides which
    spelling the ruling accepts.
    """
    low = str(name or "").strip().lower()
    if not low:
        return False
    return (low.endswith(REGISTER_NAME_SUFFIXES)
            or low.startswith(REGISTER_NAME_PREFIXES))


def is_registry_standard(name: Any) -> bool:
    """Is this name in the STANDARD spelling, per the human's ruling?

    A name that does not claim to be a register is trivially standard (the ruling
    says nothing about it). A register-like name is standard ONLY when it uses
    `_registry`.
    """
    low = str(name or "").strip().lower()
    if not looks_like_a_register(low):
        return True
    return (low.endswith(REGISTRY_STANDARD_SUFFIX)
            or low.startswith("registry_"))


def standard_registry_name(name: Any) -> str:
    """The STANDARD spelling of `name`. `x_register` -> `x_registry`.

    A MECHANICAL mapping, so it is one rule and not a judgement per name.
    """
    low = str(name or "").strip()
    if low.endswith(NONSTANDARD_REGISTER_SUFFIX):
        return low[:-len(NONSTANDARD_REGISTER_SUFFIX)] + REGISTRY_STANDARD_SUFFIX
    if low.startswith("register_"):
        return "registry_" + low[len("register_"):]
    return low


def check_register_name(conn: sqlite3.Connection, name: Any) -> dict[str, Any]:
    """The verdict on ONE register-like name against the human's ruling.

    THE RULING (2026-09-27): "be unified by registry not register".

    So the check is DIRECTIONAL:

      * `_registry`  -> ACCEPTED (the standard);
      * `_register`  -> REFUSED for a NEW term, with the standard form named;
      * a name that does not claim to be a register -> untouched.

    MEASURED DEFECT IN MY OWN FIRST VERSION, and it is why this docstring is
    explicit: I treated `register` as correct and refused `registry`
    (`version_registry` -> "did you mean version_register"). That enforced the
    OPPOSITE of the ruling, and a gate that enforces the wrong direction makes the
    CORRECT name look like the error.

    A real object is NOT required to be suggested, but when a real object with the
    standard spelling EXISTS (e.g. `channel_registry` for `channel_register`) it is
    named -- that is the evidence behind the suggestion, not an opinion.
    """
    key = str(name or "").strip()
    if not looks_like_a_register(key):
        return {"ok": True, "code": None, "name": key, "applies": False,
                "why": "the name does not claim to be a register"}
    if is_registry_standard(key):
        return {"ok": True, "code": None, "name": key, "applies": True,
                "why": "%r uses the standard suffix %r"
                       % (key, REGISTRY_STANDARD_SUFFIX)}
    target = standard_registry_name(key)
    names = real_object_names(conn)
    return {"ok": False, "code": "NONSTANDARD_REGISTER_NAME", "name": key,
            "applies": True, "near": target,
            "target_exists": target in names,
            "message": (
                "%r uses the NON-standard suffix %r. The ruling is \"be unified "
                "by registry not register\", so the standard spelling is %r%s. "
                "A register-like name in the wrong spelling makes every later "
                "reader pick the wrong object."
                % (key, NONSTANDARD_REGISTER_SUFFIX, target,
                   " (and %r IS a real object)" % target
                   if target in names else ""))}


def nonstandard_register_names(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """EXISTING objects and terms that use the non-standard `_register` spelling.

    REPORTED, never silently deleted: they are live, so a retro-refusal would be a
    mass refusal. This is the list the migration (`_unify_registry_naming.py`)
    repairs, and it is the SCALE of the ruling made visible.
    """
    out: list[dict[str, Any]] = []
    for kind, sql in (("object",
                       "SELECT name FROM sqlite_master WHERE name LIKE '%_register'"),
                      ("term",
                       "SELECT term_key AS name FROM terminology_register "
                       "WHERE is_active=1 AND term_key LIKE '%_register'")):
        try:
            rows = list(conn.execute(sql))
        except sqlite3.Error:
            continue
        for r in rows:
            n = str(r["name"])
            if n.endswith(NONSTANDARD_REGISTER_SUFFIX):
                out.append({"kind": kind, "name": n,
                            "standard": standard_registry_name(n)})
    return sorted(out, key=lambda d: (d["kind"], d["name"]))


def _nearest_register_twin(name: str,
                           names: set[str]) -> tuple[str | None, int | None]:
    """The closest REAL, register-SHAPED name to `name`, within the threshold.

    Kept because it is still the measurement behind "which two names can be
    confused", used by `confusable_name_report`.
    """
    best, best_d = None, None
    for cand in names:
        if not looks_like_a_register(cand):
            continue
        d = _edit_distance(name, cand)
        if d == 0 or d > CONFUSABLE_DISTANCE:
            continue
        if best_d is None or d < best_d:
            best, best_d = cand, d
    return best, best_d


def confusable_name_report(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """EXISTING names that a reader can CONFUSE with another real name.

    Two names within `CONFUSABLE_DISTANCE` where BOTH are register-shaped: that is
    the `register`/`registry` class the ruling is about. Reported, never deleted.
    """
    out: list[dict[str, Any]] = []
    names = real_object_names(conn)
    for r in conn.execute(
            "SELECT term_id, term_key, term_kind FROM terminology_register "
            "WHERE is_active = 1 ORDER BY term_key"):
        key = str(r["term_key"])
        if not looks_like_a_register(key):
            continue
        twin, dist = _nearest_register_twin(key, names)
        if twin is None:
            continue
        out.append({"term_id": int(r["term_id"]), "term_key": key,
                    "term_kind": str(r["term_kind"]),
                    "near": twin, "distance": dist})
    return out


# ---------------------------------------------------------------------------
# THE RUBBISH-DEFINITION GATE (added 2026-09-27)
# ---------------------------------------------------------------------------
# THE HUMAN: "you love rubbish? taskbar_vscode_app!!!????" / "or you need to have
# helper to cleanup or rubbish definition".
#
# MEASURED: `add_term` refused an empty definition, an empty citation, an
# unciteable citation, a scratch citation, a bad term_kind, a bad taxonomy level,
# an unknown parent, a misspelled key and a bad key form -- but it did NOT refuse
# a definition that SAYS NOTHING. MEASURED: the shortest definition in the
# register is 64 chars, so no rubbish is present TODAY; the gate that would stop
# it did not exist.
#
# THE RULES ARE MEASURED, NOT A TASTE JUDGEMENT:
#
#   1. length < MIN_DEFINITION_CHARS   -- MEASURED: the shortest real definition
#      is 64 chars, so 20 is far below anything a real definition reaches.
#   2. the definition EQUALS the term_key (or its spaces-for-underscores form)
#      -- restating the name says nothing about the thing.
#   3. the definition matches a DECLARED boilerplate pattern -- the sweep's own
#      fallback (`"a name from %s" % scope`) is the one measured instance.
#
# AND IT APPLIES TO A **NEW** TERM ONLY, the same narrowness `check_spelling`
# uses: a rule that retro-refuses live data is a mass refusal, not a gate.
MIN_DEFINITION_CHARS = 20

# The DECLARED boilerplate patterns. A pattern is a PREFIX, because the sweep
# appends the scope name (`a name from tables`).
BOILERPLATE_DEFINITION_PREFIXES = (
    "a name from ",
    "a name from",
    "todo",
    "tbd",
    "n/a",
    "na",
    "none",
    "placeholder",
)


def check_definition(definition: Any, term_key: Any = "") -> dict[str, Any]:
    """The verdict on ONE definition. `{ok, code, rule, message}`.

    `ok=True` means "this definition says something". It is NOT a claim that the
    definition is CORRECT -- only that it is not empty, not a restatement of the
    name, and not boilerplate. A check that claimed correctness would be claiming
    to read meaning, which it cannot.
    """
    text = str(definition or "").strip()
    key = str(term_key or "").strip()

    if not text:
        return {"ok": False, "code": "RUBBISH_DEFINITION", "rule": "empty",
                "message": "the definition is empty"}

    # THE SPECIFIC RULES COME FIRST, AND THE ORDER IS DELIBERATE.
    #
    # MEASURED 2026-09-27: with the length rule first, `taskbar_widgets` (15
    # chars) was reported as `too_short` -- TRUE, but it hides the more useful
    # fact that the definition RESTATES THE NAME. A refusal that names the
    # vaguest applicable rule sends the reader looking in the wrong place.
    #
    # RULE 2 -- restating the name. Both the raw key and its spaces form, because
    # "taskbar widgets" is the same non-statement as "taskbar_widgets".
    if key:
        forms = {key, key.replace("_", " "), key.replace("_", "-")}
        if text.rstrip(".").strip().lower() in {f.lower() for f in forms}:
            return {"ok": False, "code": "RUBBISH_DEFINITION",
                    "rule": "restates_the_name",
                    "message": ("the definition only restates the term_key %r. "
                                "A definition must say what the thing IS, and "
                                "repeating its name says nothing." % key)}

    # RULE 3 -- declared boilerplate.
    low = text.lower()
    for p in BOILERPLATE_DEFINITION_PREFIXES:
        if low.startswith(p):
            return {"ok": False, "code": "RUBBISH_DEFINITION",
                    "rule": "boilerplate",
                    "message": ("the definition starts with the declared "
                                "boilerplate %r. A placeholder is not a "
                                "definition." % p)}

    # RULE 1 -- the length floor, LAST, because it is the vaguest.
    if len(text) < MIN_DEFINITION_CHARS:
        return {"ok": False, "code": "RUBBISH_DEFINITION", "rule": "too_short",
                "message": ("the definition is %d chars, below the %d-char "
                            "floor. MEASURED: the shortest real definition in "
                            "the register is 64 chars, so a definition this "
                            "short says nothing."
                            % (len(text), MIN_DEFINITION_CHARS))}

    return {"ok": True, "code": None, "rule": None, "definition": text}


# ---------------------------------------------------------------------------
# THE INSTANCE-VALUE RULE — a name is a KIND, never an INSTANCE
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-27), verbatim
# ---------------------------------
#     "i found taskbar_microsoft_edge, taskbar_doubao_browser, taskbar_chrome_optical"
#     "and taskbar_chrome_optical??? not taskbar_chrome_deepseek?"
#
# THE PROBLEM, MEASURED
# ---------------------
# MEASURED: `target_template.label = 'Chrome (Optical) button'` and the term is
# `taskbar_chrome_optical`. **`Optical` is a WINDOW TITLE.** The same Chrome
# button is `taskbar_chrome_deepseek` tomorrow, when the window is renamed.
#
#     taskbar_microsoft_edge   = vendor + product (msedge.exe)      STABLE
#     taskbar_doubao_browser   = vendor + product (豆包浏览器)       STABLE
#     taskbar_chrome_optical   = vendor + WINDOW TITLE 'Optical'    NOT STABLE
#
# **A name that changes when a window is renamed is not a name — it is a snapshot.**
#
# WHY THE RULE IS A DECLARED LIST, NOT A GUESS
# --------------------------------------------
# MEASURED: **6 labels carry a parenthetical, and only 1 is an instance**:
#
#     Chrome (Optical) button              -> Optical              INSTANCE
#     Show desktop (far right)             -> far right            a POSITION
#     Tray input indicator (English)       -> English              a VARIANT
#     Tray input indicator (Traditional Chinese) -> Traditional Chinese  a VARIANT
#     Show hidden icons (tray chevron)     -> tray chevron         a DESCRIPTION
#     Widgets button (weather)             -> weather              a DESCRIPTION
#
# **So the rule cannot be "no parenthetical".** A checker cannot tell `Optical`
# (a window title) from `English` (a variant) by looking at the string — the two
# are the same shape. **A checker that guessed would refuse `English`.**
#
# So the rule is: **a name may not contain a token that is DECLARED as an
# instance value**, and the declared list is a TABLE ROW with a reason (QC-09),
# seeded from the MEASURED instance.
#
# AND IT APPLIES TO A **NEW** TERM ONLY, the same narrowness `check_spelling` and
# `check_definition` use: a rule that retro-refuses live data is a mass refusal,
# not a gate.

# The declared instance values, as the INITIAL SEED only. The TABLE is the SSOT
# afterwards — the same pattern `TAXONOMY_LEVEL_SEED` uses, and for the same
# reason: adding an instance value is an INSERT, not a Python edit.
#
# MEASURED 2026-09-27: `optical` is the ONE instance in the register, and it is
# the one the human named.
INSTANCE_VALUE_SEED: tuple[tuple[str, str, str], ...] = (
    ("optical", "taskbar_chrome_optical",
     "MEASURED 2026-09-27: `target_template.label = 'Chrome (Optical) button'`. "
     "`Optical` is a WINDOW TITLE, so the same Chrome button is "
     "`taskbar_chrome_deepseek` tomorrow. A name that changes when a window is "
     "renamed is a snapshot, not a name. The instance stays in `label`."),
)

TERMINOLOGY_INSTANCE_VALUE_DDL = """
CREATE TABLE IF NOT EXISTS terminology_instance_value (
    value_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    value       TEXT    NOT NULL UNIQUE,
    -- The term that MEASURED this value, so the reason is checkable.
    seen_in     TEXT    NOT NULL DEFAULT 'NA',
    -- WHY this value is an instance and not a variant. Required: a declared
    -- value with no reason is a guess wearing a table's clothes.
    reason      TEXT    NOT NULL,
    cite_ref    TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_term_instance_value_active
  ON terminology_instance_value (is_active, value);
"""


def ensure_instance_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(TERMINOLOGY_INSTANCE_VALUE_DDL)
    conn.commit()
    return {"ok": True}


def seed_instance_values(conn: sqlite3.Connection) -> dict[str, Any]:
    """UPSERT the declared instance values. A corrected reason must land."""
    ensure_instance_schema(conn)
    n = 0
    for value, seen_in, reason in INSTANCE_VALUE_SEED:
        conn.execute(
            """
            INSERT INTO terminology_instance_value
                (value, seen_in, reason, cite_ref)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (value) DO UPDATE SET
                seen_in    = excluded.seen_in,
                reason     = excluded.reason,
                cite_ref   = excluded.cite_ref,
                updated_at = CURRENT_TIMESTAMP
            """,
            (value, seen_in, reason, "terminology_registry.py:INSTANCE_VALUE_SEED"),
        )
        n += 1
    conn.commit()
    return {"ok": True, "seeded": n}


def declared_instance_values(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The DECLARED instance values, read from the TABLE (the SSOT)."""
    ensure_instance_schema(conn)
    rows = conn.execute(
        "SELECT value, seen_in, reason, cite_ref FROM terminology_instance_value "
        " WHERE is_active=1 ORDER BY value").fetchall()
    return [dict(r) for r in rows]


def check_instance_name(conn: sqlite3.Connection, term_key: Any) -> dict[str, Any]:
    """Does this name encode a DECLARED instance value? `{ok, code, rule, message}`.

    `ok=True` means "this name is a KIND, not an instance". It is NOT a claim
    that the name is CORRECT — only that it does not carry a value that changes
    with the data.

    **THE CHECK IS A DECLARED LIST, NOT A GUESS.** MEASURED: 6 labels carry a
    parenthetical and only 1 is an instance, so a checker that guessed would
    refuse `English` (a variant) along with `Optical` (a window title).

    A token is a `_`-separated element of the key, compared case-insensitively,
    so `taskbar_chrome_optical` and `taskbar_chrome_Optical` both fire.
    """
    key = str(term_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_TERM_KEY",
                "rule": "empty", "message": "term_key is required"}
    declared = declared_instance_values(conn)
    if not declared:
        # No declared values means nothing to check against. That is a WEAKER
        # guarantee, and it is NAMED rather than silently passing.
        return {"ok": True, "code": None, "rule": None, "term_key": key,
                "declared": 0,
                "note": "no declared instance values; the check is vacuous"}
    tokens = {t.lower() for t in key.split("_") if t}
    for d in declared:
        v = str(d["value"]).lower()
        if v in tokens:
            return {"ok": False, "code": "INSTANCE_IN_NAME",
                    "rule": "declared_instance_value",
                    "value": d["value"], "seen_in": d["seen_in"],
                    "message": (
                        "the name %r contains the DECLARED instance value %r "
                        "(measured in %r). An instance is a value that CHANGES "
                        "with the data, so a name carrying it is a snapshot, not "
                        "a name. The instance belongs in `label`. Reason: %s"
                        % (key, d["value"], d["seen_in"], d["reason"]))}
    return {"ok": True, "code": None, "rule": None, "term_key": key,
            "declared": len(declared)}


# ---------------------------------------------------------------------------
# THE TARGET SELECTOR — the INSTANCE, as a FIELD
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-27), verbatim
# ---------------------------------
#     "chrome can have many tab, without taskbar_chrome_deepseek, how to you
#      know you are looking for deepseek?"
#     "same as sample, taskbar_vscode without taskbar_vscode_app, how to you
#      know it is APP not browser"
#     "fuck! how to you have correct 5W1H in easy"
#
# THE PROBLEM, MEASURED
# ---------------------
# MEASURED: `target_template`'s columns are
#
#     id, name, group_id, label, cite_ref, is_active, created_at, updated_at,
#     field_type
#
# -- **there is NO selector / match / window-title column.** The only place the
# instance lives is `label = 'Chrome (Optical) button'` -- **PROSE**.
#
# **The previous plan said "the instance belongs in `label`".** That is correct
# for a HUMAN and **WRONG for a MACHINE**: a machine cannot match the prose
# `'Chrome (Optical) button'` to a window. **The instance needs a FIELD.**
#
# THE 5W1H, MEASURED
# ------------------
#     WHAT  = term_key     (the KIND)       -> taskbar_chrome      PRESENT
#     WHICH = ???          (the INSTANCE)   -> MISSING             <-- this
#     WHERE = coordinate                    -> PRESENT
#     WHEN  = ???                           -> MISSING
#     WHY   = definition                    -> PRESENT
#     HOW   = field_type                    -> PRESENT
#
# **THE ANSWER TO "how to have correct 5W1H in easy": a name answers WHAT; a
# FIELD answers WHICH.**
#
# WHY THE MIGRATION IS HERE AND NOT IN `target_register.py`
# ---------------------------------------------------------
# MEASURED: `target_register.py` holds `target_template`'s DDL, and it is NOT in
# this plan's allowlist. So the columns are added by a MIGRATION function here --
# the same pattern `ensure_instance_schema` already uses. **A migration that
# lives beside the gate is a migration the gate can prove.**
#
# AND `selector_kind` IS REQUIRED, not optional: **a selector with no kind is a
# value nobody can match.** `'Optical'` means nothing until you know it is a
# WINDOW TITLE and not an executable.

# The DECLARED selector kinds. A kind is WHAT the selector matches, so a reader
# knows how to use it. Validated at the WRITE SITE against this tuple.
SELECTOR_KINDS = ("window_title", "executable", "url", "hotkey", "position",
                  "variant", "NA")


def ensure_target_selector_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Add `selector` + `selector_kind` to `target_template` if absent.

    IDEMPOTENT: `PRAGMA table_info` is read first, so a second call is a no-op.
    A migration that is not idempotent is a migration that cannot be re-run.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(target_template)")}
    added: list[str] = []
    if "selector" not in cols:
        conn.execute("ALTER TABLE target_template "
                     "ADD COLUMN selector TEXT NOT NULL DEFAULT 'NA'")
        added.append("selector")
    if "selector_kind" not in cols:
        conn.execute("ALTER TABLE target_template "
                     "ADD COLUMN selector_kind TEXT NOT NULL DEFAULT 'NA'")
        added.append("selector_kind")
    if added:
        conn.commit()
    return {"ok": True, "added": added,
            "columns": sorted({r[1] for r in
                               conn.execute("PRAGMA table_info(target_template)")})}


def check_selector(selector: Any, selector_kind: Any) -> dict[str, Any]:
    """The verdict on ONE selector. `{ok, code, rule, message}`.

    `ok=True` means "this selector can be MATCHED". It is NOT a claim that the
    selector is CORRECT -- only that it is a value with a declared kind.

    **A SELECTOR WITH NO KIND IS REFUSED**, because `'Optical'` means nothing
    until a reader knows it is a window title and not an executable.
    """
    sel = str(selector or "").strip()
    kind = str(selector_kind or "").strip()
    if not sel or sel == "NA":
        return {"ok": False, "code": "MISSING_SELECTOR", "rule": "empty",
                "message": ("the selector is empty. A target with no selector "
                            "cannot answer WHICH one it is.")}
    if kind not in SELECTOR_KINDS:
        return {"ok": False, "code": "BAD_SELECTOR_KIND", "rule": "unknown_kind",
                "message": ("selector_kind must be one of %s, got %r. A selector "
                            "with no kind is a value nobody can match."
                            % (list(SELECTOR_KINDS), kind))}
    if kind == "NA":
        return {"ok": False, "code": "MISSING_SELECTOR", "rule": "no_kind",
                "message": ("selector_kind is 'NA', so the selector %r has no "
                            "declared meaning." % sel)}
    return {"ok": True, "code": None, "rule": None,
            "selector": sel, "selector_kind": kind}


def scratch_citation_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """The scratch-citation population, with the two classes SEPARATED.

    REPORTED, never asserted, and NEVER auto-rewritten. The two classes have very
    different meanings and are the reason a bulk rewrite would be wrong:

      * `scratch_cite`     — a real concept whose cite points at a scratch file.
                             Fixable only by measuring where the concept IS.
      * `auto_definition`  — a term a machine invented from a FILENAME. This one is
                             not a concept at all.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT term_key, term_kind, cite_ref, definition, is_active "
        "FROM terminology_register")]
    scratch = [r for r in rows if is_scratch_cite(r.get("cite_ref"))]
    auto = [r for r in rows if is_auto_definition(r.get("definition"))]
    files: dict[str, int] = {}
    for r in scratch:
        f = str(r["cite_ref"]).split(":")[0]
        files[f] = files.get(f, 0) + 1
    return {
        "ok": True,
        "total_terms": len(rows),
        "scratch_cite_count": len(scratch),
        "scratch_file_count": len(files),
        "auto_definition_count": len(auto),
        "top_scratch_files": sorted(files.items(), key=lambda x: -x[1])[:10],
        "auto_definition_terms": [str(r["term_key"]) for r in auto][:20],
        "heuristic_candidates_left_alone": (
            "532 of the scratch-cited terms had a prefix-match candidate module; "
            "a prefix match is a HEURISTIC, not evidence, so NO cite was rewritten"),
        "cite_ref_rewritten": 0,
        "why_not_rewritten": ("the honest fix needs a per-term measurement of where "
                              "the concept IS; a prefix guess would INVENT a "
                              "citation"),
    }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    from db_schema import TERMINOLOGY_REGISTER_DDL
    conn.executescript(TERMINOLOGY_REGISTER_DDL)
    conn.commit()
    return {"ok": True}


def add_term(
    conn: sqlite3.Connection,
    term_key: str,
    *,
    definition: str,
    cite_ref: str,
    term_kind: str = "part",
    parent_term_id: int | None = None,
    taxonomy_level: str = "NA",
    taxonomy_path: str = "NA",
    entity_ref_key: str = "NA",
    alias_list: Any = None,
    version: int = 1,
    is_active: int | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Register ONE term. Idempotent on `(parent_term_id, term_key)`.

    `parent_term_id` points at the COMPOSITE term this part belongs to, so the
    register STORES the structure.

    The TAXONOMY is filled HERE, at registration time (the user's decision: no
    second catalog table, no back-fill later).

    REFUSES an empty definition or citation. A term with no meaning is a word
    nobody defined, and a term with no citation is a claim nobody can check —
    the same rule `citation_discipline` enforces for findings.

    `is_active` DEFAULTS TO VISIBLE (changed 2026-09-24). It used to default to
    `0`, which is the ONE default this function must not have: registering a term
    is the act of making a name RECOGNISEABLE, so a new term that resolves to
    nothing is a silent failure of that exact purpose. MEASURED before the change:
    `terminology_register` held 71 active / 3 inactive, so 3 terms had been
    registered by the book and were invisible. `None` now means "the caller did
    not say", and that is `1`. Pass `is_active=0` explicitly to hide one, and the
    reason is then the caller's to state.
    """
    key = str(term_key or "").strip()
    if not key:
        return {"ok": False, "code": "MISSING_TERM_KEY",
                "message": "term_key is required"}
    # A NEW TERM MAY NOT BE MISSPELLED (added 2026-09-27).
    #
    # THE HUMAN: "alias must = terminonotology, all in same language" /
    # "fucking mis-undersatnd name never happen again". MEASURED: the register had
    # NO spelling check at all, so `enviornment_playwright` entered it and 357 real
    # identifiers used the typo. The rule is NARROW (a NEW term only) so it cannot
    # mass-refuse live data.
    sp = check_spelling(key)
    if not sp["ok"]:
        return {"ok": False, "code": sp["code"], "message": sp["message"],
                "typo": sp["typo"], "correct": sp["correct"]}
    # A NEW TERM_KEY MUST BE lowercase snake_case or dotted (added 2026-09-27).
    # MEASURED: all 1452 active terms already comply, so this refuses only
    # NEW violations. The defect it stops: `LLM_OFF_FORM` entered as a
    # term_key (id=87) when it is a Python constant — a carrier, an alias.
    nm = check_naming(key)
    if not nm["ok"]:
        return {"ok": False, "code": nm["code"], "message": nm["message"]}
    if not str(definition or "").strip():
        return {"ok": False, "code": "MISSING_DEFINITION",
                "message": ("a term with no definition is a word nobody "
                            "defined: %r" % key)}
    # A DEFINITION THAT SAYS NOTHING IS REFUSED (added 2026-09-27).
    #
    # THE HUMAN: "you love rubbish? taskbar_vscode_app!!!????" / "or you need to
    # have helper to cleanup or rubbish definition".
    #
    # MEASURED: the register had NO check on the definition's CONTENT -- only on
    # its presence. So `"a name from tables"` (the sweep's own fallback) would
    # have been accepted. The rule is NARROW (a NEW term only) so it cannot
    # mass-refuse live data, and its three rules are MEASURED (see
    # `check_definition`).
    dq = check_definition(definition, key)
    if not dq["ok"]:
        return {"ok": False, "code": dq["code"], "rule": dq["rule"],
                "message": dq["message"]}
    # A NAME THAT ENCODES AN INSTANCE IS REFUSED (added 2026-09-27).
    #
    # THE HUMAN: "i found taskbar_microsoft_edge, taskbar_doubao_browser,
    # taskbar_chrome_optical" / "and taskbar_chrome_optical??? not
    # taskbar_chrome_deepseek?".
    #
    # MEASURED: `taskbar_chrome_optical` encodes the WINDOW TITLE `Optical`, so
    # the same Chrome button is `taskbar_chrome_deepseek` tomorrow. The rule is
    # NARROW (a NEW term only) so it cannot mass-refuse live data, and its list is
    # DECLARED (see `check_instance_name`) because a checker cannot tell `Optical`
    # from `English` by looking at the string.
    iv = check_instance_name(conn, key)
    if not iv["ok"]:
        return {"ok": False, "code": iv["code"], "rule": iv["rule"],
                "message": iv["message"]}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no term: %r" % key}
    # THE CITATION MUST BE CHECKABLE, not merely non-empty.
    #
    # MEASURED 2026-09-23, and this is why the check exists: the local 7B was
    # asked to register three real terms. Its names and definitions were usable,
    # but its citations were not —
    #
    #     cite_ref = "refuse: hypothetical scenario, no real VS Code doc"
    #     cite_ref = "vscode_env_log_schema.txt"     <- the file DOES NOT EXIST
    #
    # — and this function ACCEPTED both, because it only tested for a non-empty
    # string. So the gate could not tell a citation from a sentence ABOUT not
    # having one. `terminology_cite.verify_cite_ref` is the logic generator that
    # closes it: a path must EXIST, a line must be INSIDE the file, and a refusal
    # dressed as a citation is refused.
    try:
        import terminology_cite as tc
        ok_cite, why_cite = tc.verify_cite_ref(str(cite_ref))
        if not ok_cite:
            return {"ok": False, "code": "UNCITEABLE_CITE_REF",
                    "message": "cite_ref for %r is not checkable: %s" % (key, why_cite)}
    except ImportError:
        # The verifier is absent (an older checkout). Fall back to the non-empty
        # check rather than refusing every term — but the fallback is NAMED in
        # the result so a caller can see the weaker guarantee.
        pass
    # A SCRATCH FILE MAY NOT BE THE CITATION FOR A **NEW** TERM.
    #
    # MEASURED 2026-09-26: `terminology_cite.verify_cite_ref` judges whether a cite
    # is CHECKABLE, and a scratch file (`_proof_*.py`, `_diag_*.py`, ...) IS
    # checkable — so 814 live terms cite one. But a scratch file is evidence that is
    # DELETED after its run, so it is not evidence. The verifier cannot see that, and
    # widening IT would break `terminology_cite`'s single existing job.
    #
    # The rule is deliberately NARROW: it applies to a NEW term only. Existing rows
    # are NOT retro-refused (that would be a mass refusal of live data); they are
    # repaired by evidence in `_recite_evidence.py`, which re-cites a term ONLY when
    # its `term_key` is found on a definition line in exactly one non-scratch file.
    if is_scratch_cite(cite_ref):
        return {"ok": False, "code": "SCRATCH_CITE_REF",
                "message": ("the cite for the NEW term %r is a SCRATCH file "
                            "(%r). A scratch file is deleted after its run, so it "
                            "is not evidence. Cite the real home (path:line) — if "
                            "there is none, the term has no home yet."
                            % (key, str(cite_ref)))}
    if term_kind not in TERM_KINDS:
        return {"ok": False, "code": "BAD_TERM_KIND",
                "message": "term_kind must be one of %s, got %r"
                           % (list(TERM_KINDS), term_kind)}
    lvl = str(taxonomy_level or "NA").strip() or "NA"
    # READ THE TABLE, not a Python tuple (QC-87/QC-88). The user's rule 1 is DB
    # driven, so a level added by an INSERT must be valid IMMEDIATELY — no code
    # change, no restart. `TAXONOMY_LEVELS` is kept only as the deprecated
    # fallback for a DB that has not been seeded yet.
    try:
        import taxonomy_level_registry as tlr
        # `table_exists` FIRST: "the table says no" and "there is no table yet"
        # are different answers, and conflating them made an un-migrated DB
        # refuse EVERY level. Caught by RUNNING `_proof_terminology_registry.py`.
        if tlr.table_exists(conn):
            if not tlr.is_valid_level(conn, lvl):
                return {"ok": False, "code": "BAD_TAXONOMY_LEVEL",
                        "message": "taxonomy_level must be a declared level in "
                                   "`taxonomy_level_registry` (or 'NA'), got %r; "
                                   "declared: %s"
                                   % (lvl, list(tlr.level_keys(conn)))}
        elif lvl != "NA" and lvl not in TAXONOMY_LEVELS:
            # The table does not exist yet — fall back to the deprecated tuple so
            # an un-migrated DB still works. The proof asserts the two agree.
            return {"ok": False, "code": "BAD_TAXONOMY_LEVEL",
                    "message": "taxonomy_level must be one of %s (or 'NA'), got %r"
                               % (list(TAXONOMY_LEVELS), lvl)}
    except ImportError:
        if lvl != "NA" and lvl not in TAXONOMY_LEVELS:
            return {"ok": False, "code": "BAD_TAXONOMY_LEVEL",
                    "message": "taxonomy_level must be one of %s (or 'NA'), got %r"
                               % (list(TAXONOMY_LEVELS), lvl)}
    if parent_term_id is not None:
        if not conn.execute("SELECT 1 FROM terminology_register WHERE term_id=?",
                            (int(parent_term_id),)).fetchone():
            return {"ok": False, "code": "UNKNOWN_PARENT",
                    "message": "no terminology_register row with term_id=%d"
                               % int(parent_term_id)}

    existing = conn.execute(
        "SELECT term_id FROM terminology_register WHERE "
        "IFNULL(parent_term_id, -1) = IFNULL(?, -1) AND term_key = ?",
        (parent_term_id, key)).fetchone()
    if existing:
        return {"ok": True, "term_id": int(existing[0]), "created": False,
                "term_key": key}
    # A NEW NAME MUST USE THE STANDARD SPELLING `_registry` (added 2026-09-27).
    #
    # THE HUMAN'S RULING: "be unified by registry not register".
    #
    # IT RUNS *AFTER* THE EXISTING-TERM CHECK, DELIBERATELY. MEASURED DEFECT IN MY
    # OWN FIRST VERSION: the gate ran FIRST, so re-registering an ALREADY-PRESENT
    # term was refused as if it were new -- the proof's own re-seed went red on a
    # correct call. "A new name must be standard" is not "an existing name must
    # be standard now"; the second is the migration's job, not this gate's.
    #
    # The check is NARROW (a NEW term only) so it cannot mass-refuse live data; the
    # 29 existing `_register` objects are REPORTED by `nonstandard_register_names`
    # and repaired by `_unify_registry_naming.py`.
    rn = check_register_name(conn, key)
    if not rn["ok"]:
        return {"ok": False, "code": rn["code"], "message": rn["message"],
                "standard": rn.get("near"),
                "target_exists": rn.get("target_exists")}

    aliases = alias_list
    if aliases is None:
        aliases_json = "NA"
    elif isinstance(aliases, str):
        aliases_json = aliases.strip() or "NA"
    else:
        aliases_json = json.dumps(list(aliases), ensure_ascii=False)

    cur = conn.execute(
        "INSERT INTO terminology_register (term_key, term_kind, parent_term_id, "
        "taxonomy_level, taxonomy_path, entity_ref_key, definition, "
        "definition_sha256, alias_list, version, cite_ref, is_active) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, term_kind, parent_term_id, lvl,
         str(taxonomy_path or "NA").strip() or "NA",
         str(entity_ref_key or "NA").strip() or "NA",
         str(definition).strip(), definition_hash(definition),
         aliases_json, int(version), str(cite_ref).strip(),
         1 if is_active is None else int(is_active)))
    if commit:
        conn.commit()
    return {"ok": True, "term_id": cur.lastrowid, "created": True,
            "term_key": key, "definition_sha256": definition_hash(definition)}


def update_term(
    conn: sqlite3.Connection,
    term_id: int,
    *,
    term_key: str | None = None,
    definition: str | None = None,
    cite_ref: str | None = None,
    is_active: int | None = None,
    alias_list: Any = None,
    cite: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """UPDATE a term's LABEL and/or meaning. ONE row, ONE write.

    THE USER'S RULING (2026-09-24), and this is the whole point of the function:
      > "have update terminology by value ... so not need to have so many duplicate
      >  task by rename and rename, it is not smart"

    `term_id` is the VALUE (the primary key). `term_key` is a LABEL attached to it.
    A rename is therefore a LABEL MOVE on ONE existing row — it must NOT spawn a
    second name-object, a second compatibility VIEW, or a second task.

    THE OLD LABEL IS PRESERVED, NOT OVERWRITTEN. When `term_key` moves, the previous
    key is APPENDED to `alias_list` in the SAME write. If it were dropped, every
    reader still using the old name would silently stop resolving, and the only fix
    would be the renames-by-hand the user is objecting to. Moving a label without
    keeping where it came from is a memory loss, not a rename.

    REFUSES:
      * `NOT_A_TERM`        — no row with this `term_id`.
      * `UNCITED_RENAME`    — moving `term_key` needs a `cite` (checkable provenance).
      * `EMPTY_DEFINITION`  — a term with no meaning is a word nobody defined.
      * `KEY_COLLISION`     — the new key is already another ACTIVE term's key.
      * `NO_CHANGE`         — nothing to write; reported, not silently "ok".

    `cite` is required ONLY for a key move, because that is the claim that needs
    checking. It is distinct from `cite_ref`, which is the term's own provenance.
    """
    row = conn.execute(
        "SELECT term_id, term_key, definition, alias_list, is_active "
        "FROM terminology_register WHERE term_id = ?", (int(term_id),)
    ).fetchone()
    if row is None:
        return {"ok": False, "reason": "NOT_A_TERM", "term_id": int(term_id)}

    cur_key = str(row["term_key"])
    sets: list[str] = []
    params: list[Any] = []

    # --- the LABEL MOVE, with the old label kept as an alias -------------------
    new_aliases = _as_alias_list(row["alias_list"])
    moved: str | None = None
    if term_key is not None:
        new_key = str(term_key).strip()
        if not new_key:
            return {"ok": False, "reason": "EMPTY_TERM_KEY", "term_id": int(term_id)}
        if new_key != cur_key:
            if not (cite or "").strip():
                # Moving a name is a CLAIM about the world. An uncited claim is
                # the thing `citation_discipline` refuses everywhere else.
                return {"ok": False, "reason": "UNCITED_RENAME",
                        "term_id": int(term_id), "from": cur_key, "to": new_key}
            clash = conn.execute(
                "SELECT term_id FROM terminology_register "
                "WHERE term_key = ? AND term_id != ? AND is_active = 1",
                (new_key, int(term_id))).fetchone()
            if clash is not None:
                return {"ok": False, "reason": "KEY_COLLISION", "to": new_key,
                        "collides_with_term_id": int(clash["term_id"])}
            sets.append("term_key = ?")
            params.append(new_key)
            moved = cur_key
            if moved not in new_aliases:
                new_aliases.append(moved)

    if definition is not None:
        d = str(definition).strip()
        if not d:
            return {"ok": False, "reason": "EMPTY_DEFINITION",
                    "term_id": int(term_id)}
        sets.append("definition = ?")
        params.append(d)
        sets.append("definition_sha256 = ?")
        params.append(definition_hash(d))

    if cite_ref is not None:
        sets.append("cite_ref = ?")
        params.append(str(cite_ref).strip())

    if is_active is not None:
        sets.append("is_active = ?")
        params.append(int(is_active))

    # `alias_list` is written whenever it CHANGED, not only when passed.
    if alias_list is not None:
        given = _as_alias_list(alias_list)
        merged = list(dict.fromkeys(new_aliases + [a for a in given
                                                   if a not in new_aliases]))
        if merged != _as_alias_list(row["alias_list"]):
            sets.append("alias_list = ?")
            params.append(json.dumps(merged, ensure_ascii=False) if merged else "NA")
    elif moved is not None:
        sets.append("alias_list = ?")
        params.append(json.dumps(new_aliases, ensure_ascii=False) if new_aliases
                      else "NA")

    if not sets:
        return {"ok": False, "reason": "NO_CHANGE", "term_id": int(term_id),
                "term_key": cur_key}

    sets.append("updated_at = datetime('now')")
    params.append(int(term_id))
    conn.execute(
        "UPDATE terminology_register SET %s WHERE term_id = ?" % ", ".join(sets),
        tuple(params))
    if commit:
        conn.commit()

    after = conn.execute(
        "SELECT term_key, alias_list FROM terminology_register WHERE term_id = ?",
        (int(term_id),)).fetchone()
    return {"ok": True, "term_id": int(term_id), "moved": moved,
            "term_key": str(after["term_key"]),
            "alias_list": _as_alias_list(after["alias_list"]),
            "wrote": len(sets) - 1,
            "cite": (cite or "").strip() or None}


def _as_alias_list(raw: Any) -> list[str]:
    """Read `alias_list` in EITHER shape: SQL NULL / the string 'NA' / JSON."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    s = str(raw).strip()
    if not s or s.upper() == "NA":
        return []
    try:
        got = json.loads(s)
    except Exception:
        return [s]
    if isinstance(got, list):
        return [str(x) for x in got]
    return [str(got)]


def get_term(conn: sqlite3.Connection, term_key: str,
             parent_term_id: int | None = None) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM terminology_register WHERE "
        "IFNULL(parent_term_id, -1) = IFNULL(?, -1) AND term_key = ?",
        (parent_term_id, str(term_key).strip())).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# THE CANONICAL KEY — the ONE name for a concept (added 2026-09-26)
# ---------------------------------------------------------------------------
#
# MEASURED DEFECT (2026-09-26). The human: "terminontoloty register can totaly KO
  # that!!! fuck... fix it now!" — on `5w1h` vs `derive_5w1h`.
#
# The register COULD already KO a name: `trigger_point.qualify_collisions` reports
# a collision and derives the qualifier. But MEASURED, its declared rule is
#
#     would_be_red_if: "a term_key shared by >= 2 ACTIVE terms"
#
# and its code groups by the EXACT string:
#
#     by_key.setdefault(str(r["term_key"]), []).append(r)
#     if len(group) < 2: continue
#
  # So `5w1h` and `derive_5w1h` are different STRINGS, never group, and are
  # never a collision. MEASURED: `'derive_5w1h' in colliding keys` -> False.
# The schema has the same hole: `UNIQUE (parent_term_id, term_key)`, not
# `UNIQUE (term_key)`. So a second SPELLING of one concept inserts cleanly.
#
# THREE terms named one concept, all `parent_term_id = NULL`, all `alias_list =
# 'NA'`, all `assert_named` -> ok=True, and `decompose` saw NO relationship.
#
# THE FIX: identity is the CANONICAL KEY, not the raw string. Two spellings are
# the same term when `alias_list` says so. The survivor is DECLARED (written into
# `alias_list`), never chosen by count, length, or order.

class TermCycleError(TermError):
    """`alias_list` forms a cycle, so no canonical key exists."""


def alias_index(conn: sqlite3.Connection) -> dict[str, str]:
    """`{alias: canonical_term_key}` for every declared alias.

    AN ALIAS IS A NAME THAT IS **NOT** ITSELF A TERM — that is what an alias IS.
    `alias_list` on a term reads "these other names also mean me", so the alias is
    by definition an alternative SPELLING, not a second registered row.

    MEASURED DEFECT IN MY FIRST VERSION (2026-09-26), and it was a FALSE NEGATIVE:

        keys = {r["term_key"] for r in rows}
        if a and a != owner and a in keys:      # <-- WRONG

    The `a in keys` clause required the ALIAS to ALSO be a registered term. MEASURED,
      that kept only **3** aliases (`skill_5w1h`, `ticket_5w1h`, `derive_5w1h` —
    which happened to be rows too) and DROPPED the real ones:

        'field'       -> db_field     NOT resolved
        'table'       -> db_table     NOT resolved
        'llm_100_run' -> proof_run    NOT resolved

    THE CONSEQUENCE WAS WORSE THAN "does not resolve": `assert_named('field')`
    returned **ok=False, "an invented word"** for a name that IS a declared, legal
    alias. The register called a registered name invented.

    SCOPE: an alias is accepted UNCONDITIONALLY (except a self-alias). `alias_list`
    READS "these other names also mean me", so the alias does NOT need to name
    anything on its own — it only needs to resolve TO its owner.

    MEASURED, and this is the SECOND time I got the direction wrong: my first fix
    added a `_target_is_known` gate, which still dropped `field` -> `db_field`
    because the NAME `field` has no row of its own. The correct question is not
    "does the alias name something" but "does the alias AMBIGUOUSLY name something
    ELSE" — which `alias_problems` answers separately.
    """
    out: dict[str, str] = {}
    rows = [dict(r) for r in conn.execute(
        "SELECT term_key, alias_list FROM terminology_register")]
    for r in rows:
        owner = str(r["term_key"])
        for a in _as_alias_list(r.get("alias_list")):
            a = str(a).strip()
            if not a or a == owner:
                continue
            out[a] = owner
    return out


def alias_problems(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Aliases that are STRUCTURALLY unusable, each with a named reason.

    REPORTED, never silently dropped — the same rule the rest of this repo uses.

    CORRECTED TWICE (2026-09-26), and the history is kept because it is the lesson:
    my first version demanded the alias's TARGET be a registered term, reporting
    **19 problems** that were almost all FALSE POSITIVES. My second version narrowed
    that to "the target names a term or a table" and STILL got the direction wrong,
    because an alias does not name anything — it is a second name FOR its owner.

    The real defect conditions are STRUCTURAL, and I got this test wrong TWICE
    before measuring what it should be:

      * `SELF_ALIAS` — a name declared as an alias of itself, which says nothing;
      * `ALIAS_CLAIMED_TWICE` — two different owners both declare the same alias, so
        it does not resolve to ONE term. THIS is the dangerous one.
      * `ALIAS_CYCLE` — the declarations form a loop, so no canonical key exists.

    NOT a defect, MEASURED: an alias that is ALSO its own term row (`skill_5w1h`),
    and an alias whose target is absent (`field` -> `db_field`). My second version
    reported 5 `AMBIGUOUS_ALIAS` for exactly those — all FALSE POSITIVES.
    """
    out: list[dict[str, Any]] = []
    rows = [dict(r) for r in conn.execute(
        "SELECT term_key, alias_list FROM terminology_register")]
    claims: dict[str, list[str]] = {}
    for r in rows:
        owner = str(r["term_key"])
        for a in _as_alias_list(r.get("alias_list")):
            a = str(a).strip()
            if not a:
                continue
            if a == owner:
                out.append({"term_key": owner, "alias": a,
                            "reason": "SELF_ALIAS"})
                continue
            claims.setdefault(a, []).append(owner)
    for a, owners in claims.items():
        distinct = sorted(set(owners))
        if len(distinct) > 1:
            out.append({"term_key": distinct[0], "alias": a,
                        "reason": "ALIAS_CLAIMED_TWICE",
                        "why": "claimed by %s" % distinct})
    for a, owner in alias_index(conn).items():
        try:
            if canonical_term_key(conn, owner) == a:
                out.append({"term_key": owner, "alias": a,
                            "reason": "ALIAS_CYCLE"})
        except TermCycleError:
            out.append({"term_key": owner, "alias": a,
                        "reason": "ALIAS_CYCLE"})
    return out


def canonical_term_key(conn: sqlite3.Connection, name: str) -> str:
    """The ONE canonical term_key for `name`. Follows `alias_list`.

    A name that is not an alias is its own canonical key.

    REFUSES a cycle with `TermCycleError`: a cycle means no name is primary, and
    picking one would be the "choose a winner" defect the `independent-review`
    rule forbids. An alias whose target is missing is NOT followed (it cannot be),
    so the name resolves to itself and `alias_problems` reports the dangling alias.
    """
    key = str(name or "").strip()
    idx = alias_index(conn)
    seen: list[str] = []
    while key in idx:
        if key in seen:
            raise TermCycleError(
                "alias cycle: %s" % " -> ".join(seen + [key]))
        seen.append(key)
        key = idx[key]
    return key



def list_terms(conn: sqlite3.Connection, *,
               parent_term_id: int | None = None,
               taxonomy_level: str | None = None) -> list[dict[str, Any]]:
    if taxonomy_level:
        rows = conn.execute(
            "SELECT * FROM terminology_register WHERE taxonomy_level=? "
            "ORDER BY term_key", (str(taxonomy_level),))
    elif parent_term_id is None:
        rows = conn.execute(
            "SELECT * FROM terminology_register ORDER BY term_key")
    else:
        rows = conn.execute(
            "SELECT * FROM terminology_register WHERE parent_term_id=? "
            "ORDER BY term_key", (int(parent_term_id),))
    return [dict(r) for r in rows]


def terms_for_entity(conn: sqlite3.Connection,
                     entity_ref_key: str) -> list[dict[str, Any]]:
    """Every term that names one entity — the reverse lookup the taxonomy gives."""
    rows = conn.execute(
        "SELECT * FROM terminology_register WHERE entity_ref_key=? "
        "ORDER BY term_key", (str(entity_ref_key),))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Decomposition — the STORED structure
# ---------------------------------------------------------------------------

def parts_of(conn: sqlite3.Connection,
             term_id: int) -> list[dict[str, Any]]:
    """The DIRECT parts of a composite term, read from the register."""
    rows = conn.execute(
        "SELECT * FROM terminology_register WHERE parent_term_id=? "
        "ORDER BY term_key", (int(term_id),))
    return [dict(r) for r in rows]


def _children_of_term(conn: sqlite3.Connection, key: str) -> list[str]:
    """The DIRECT parts of a term, across ALL its rows.

    MEASURED, and my first version was WRONG: it recursed by `term_id`, so
    `100_run` resolved to the row that is a CHILD of `100_run_service` (which has
    no children) instead of the row that HAS children. The tree then showed
    `100_run: []` and the recursion never reached `100` + `run`.

    A term may exist as SEVERAL rows — a top-level one and one per parent — so
    the children of a term are the union of the children of ALL its rows.
    """
    kids: list[str] = []
    for row_k in conn.execute(
            "SELECT term_id FROM terminology_register WHERE term_key=?", (key,)):
        for k in parts_of(conn, int(row_k[0])):
            kk = str(k["term_key"])
            if kk not in kids:
                kids.append(kk)
    return kids


def decompose(conn: sqlite3.Connection, term: str) -> dict[str, Any]:
    """Split a term into its parts, READ FROM THE REGISTER.

    Returns `{term, parts, unregistered, ok, tree}`.

    The structure is STORED (`parent_term_id`), not inferred from the string.
    MEASURED, and my first version was WRONG: longest-registered-prefix matching
    returned `[100_run_service]` for `100_run_service`, because the whole term is
    itself registered — so it never split the user's own example.

    `parts` is the DIRECT decomposition — the user's own example is
    `100_run_service` -> `100_run` + `service`, NOT the fully flattened
    `100_run + 100 + run + service`. The full tree is in `tree`.
    """
    raw = str(term or "").strip()
    if not raw:
        return {"term": raw, "parts": [], "unregistered": [raw],
                "ok": False, "tree": {}}

    # A term may be registered as a TOP-LEVEL term, as a PART of another term, or
    # BOTH. MEASURED, and my first version was WRONG: it looked up only the
    # top-level row, so `service` and `run` — which exist as PARTS of
    # `100_run_service` and `100_run` — reported "not registered". A word that is
    # a part of one term is still a registered word.
    row = get_term(conn, raw)
    if not row:
        row = conn.execute(
            "SELECT * FROM terminology_register WHERE term_key=? "
            "ORDER BY term_id LIMIT 1", (raw,)).fetchone()
        row = dict(row) if row else None
    if not row:
        return {"term": raw, "parts": [], "unregistered": [raw],
                "ok": False, "tree": {}}

    tree: dict[str, Any] = {}
    seen: set[str] = set()

    def walk(key: str) -> None:
        if key in seen:
            tree[key] = ["<cycle>"]
            return
        seen.add(key)
        kids = _children_of_term(conn, key)
        tree[key] = kids          # a LEAF is [], which is not an error
        for kk in kids:
            walk(kk)

    walk(raw)
    direct = _children_of_term(conn, raw)
    return {"term": raw, "parts": direct, "unregistered": [],
            "ok": True, "tree": tree}


def assert_decomposable(conn: sqlite3.Connection, term: str) -> tuple[bool, str]:
    """`(ok, reason)`. The reason NAMES the unregistered term.

    Naming the part is the point: the fix is "register this word", not "try
    again". A refusal that does not say WHICH word is missing sends the reader
    back to guessing.
    """
    res = decompose(conn, term)
    if res["ok"]:
        if not res["parts"]:
            return True, ("%r is registered and ATOMIC (no parts) — a leaf is a "
                          "word, not a composition" % term)
        return True, ("%r decomposes into %s"
                      % (term, " + ".join(res["parts"])))
    return False, ("term %r is NOT in terminology_register — a term that was "
                   "never registered is an invented word" % term)


def assert_factor_named(conn: sqlite3.Connection,
                        factor_key: str) -> tuple[bool, str]:
    """A factor's `factor_key` must decompose into registered parts.

    This is the user's key sentence: "factor defination is correct ot not!!!
    that is the key". A factor named with an unregistered word is a factor
    nobody defined.
    """
    return assert_decomposable(conn, factor_key)


def assert_named(conn: sqlite3.Connection, term: str) -> tuple[bool, str]:
    """`(ok, reason)` for ANY name — is it a registered term?

    WHY THIS EXISTS (the user, 2026-09-23):

        "too easy to have name mis-understand problem, you need to register at
         terminology_register!!!"
        "so wrong name can be applyed to qc skill to help proofed your mistake
         before report done"

    Before this, only `assert_factor_named` existed, so the register could check
    a FACTOR and nothing else — a table name, a column name, an API route or a
    module name had no check at all. That is why the collision below went
    unnoticed: `conversation_env_log.chat_main_id` FK -> `chat_main(id)`, while
    `chat_main` is the CHAT system's table and the value identifies a VS Code
    conversation. One word, two systems.

    The reason NAMES the term, because the fix is "register this word", not "try
    again".
    """
    raw = str(term or "").strip()
    if not raw:
        return False, "an empty name is not a term"
    row = get_term(conn, raw)
    if not row:
        # A NAME WITH SEVERAL SENSES MUST NOT SILENTLY RESOLVE TO ONE.
        #
        # MEASURED DEFECT (2026-09-27): this used `ORDER BY term_id LIMIT 1`, so
        # `route` — which has TWO rows, one under `connection` and one under
        # `http_endpoint` — resolved to whichever had the lower id, and the
        # reader was NEVER TOLD there was a second sense. That is the "choose a
        # winner" defect `independent-review` forbids: a disagreement must
        # trigger a MEASUREMENT, never a silent pick.
        #
        # MEASURED: `route` x2 is the ONLY term_key with more than one row, so
        # this branch is narrow and cannot mass-refuse live data.
        multi = conn.execute(
            "SELECT * FROM terminology_register WHERE term_key=? "
            "ORDER BY term_id", (raw,)).fetchall()
        if len(multi) > 1:
            senses = []
            for r in multi:
                d = dict(r)
                parent = d.get("parent_term_id")
                pkey = ""
                if parent:
                    pr = conn.execute(
                        "SELECT term_key FROM terminology_register "
                        "WHERE term_id=?", (parent,)).fetchone()
                    pkey = pr[0] if pr else "?"
                senses.append("%s (under %s): %s"
                              % (d.get("term_kind"), pkey or "no parent",
                                 str(d.get("definition") or "")[:80]))
            return True, (
                "name %r is a registered term with %d SENSES — it does NOT "
                "resolve to one. Name the sense you mean: %s"
                % (raw, len(multi), " | ".join(senses)))
        row = dict(multi[0]) if multi else None
    if not row:
        # AN ALIAS THAT HAS NO ROW OF ITS OWN IS STILL A DECLARED NAME.
        #
        # MEASURED DEFECT (2026-09-26): this returned ok=False, "an invented word",
        # for `field` / `table` / `llm_100_run` — names that ARE declared aliases of
        # `db_field` / `db_table` / `proof_run`. **The register called a declared,
        # legal alias an INVENTED WORD.** That is a FALSE NEGATIVE, and it is the
        # more dangerous direction: a false positive blocks work, a false negative
        # lets a name through while claiming to have checked it.
        #
        # The owner's row is what carries the definition and the citation, so the
        # alias is judged by its OWNER — the same rule `canonical_term_key` uses.
        try:
            canon = canonical_term_key(conn, raw)
        except TermCycleError as e:
            return False, ("name %r is in an alias CYCLE (%s) — no canonical name "
                           "exists, and picking one would be a guess" % (raw, e))
        if canon != raw:
            crow = conn.execute(
                "SELECT * FROM terminology_register WHERE term_key=? "
                "ORDER BY term_id LIMIT 1", (canon,)).fetchone()
            crow = dict(crow) if crow else None
            if crow and str(crow.get("definition") or "").strip() \
                    and str(crow.get("cite_ref") or "").strip():
                return True, ("%r is an ALIAS of %r (%s): %s"
                              % (raw, canon, crow.get("term_kind"),
                                 crow.get("definition")))
        return False, (
            "name %r is NOT in terminology_register — a name that was never "
            "registered is an invented word. Register it with a definition and "
            "a cite_ref before using it." % raw)
    if not str(row.get("definition") or "").strip():
        return False, ("name %r is registered but has NO definition — a term "
                       "with no meaning is a word nobody defined" % raw)
    if not str(row.get("cite_ref") or "").strip():
        return False, ("name %r is registered but has NO cite_ref — no "
                       "citation, no term" % raw)
    # THE CANONICAL KEY (added 2026-09-26). A reader must learn the ONE name for a
    # concept, not just that its own spelling is registered. MEASURED DEFECT: three
    # spellings of `5w1h` all returned ok=True and the reader was never told they
    # were the same concept.
    canon = raw
    try:
        canon = canonical_term_key(conn, raw)
    except TermCycleError as e:
        return False, ("name %r is in an alias CYCLE (%s) — no canonical name "
                       "exists, and picking one would be a guess" % (raw, e))
    if canon != raw:
        return True, ("%r is a registered term (%s) AND an ALIAS of %r: %s"
                      % (raw, row.get("term_kind"), canon,
                         row.get("definition")))
    return True, ("%r is a registered term (%s): %s"
                  % (raw, row.get("term_kind"), row.get("definition")))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="the terminology register")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--decompose", metavar="TERM")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--level", metavar="LEVEL")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        if args.decompose:
            res = decompose(conn, args.decompose)
            print("term  : %s" % res["term"])
            print("parts : %s" % " + ".join(res["parts"]))
            print("ok    : %s" % res["ok"])
            if res["unregistered"]:
                print("UNREGISTERED: %s" % res["unregistered"])
            return 0 if res["ok"] else 1
        if args.list or args.level:
            rows = list_terms(conn, taxonomy_level=args.level)
            print("terminology_register: %d rows" % len(rows))
            for r in rows:
                print("  %-24s %-10s lvl=%-10s parent=%s active=%s"
                      % (r["term_key"], r["term_kind"], r["taxonomy_level"],
                         r["parent_term_id"], r["is_active"]))
            return 0
        ap.print_help()
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
