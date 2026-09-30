# -*- coding: utf-8 -*-
"""generator_center.py — ONE registry for the FIVE generators, so ONE UI template
can run any of them.

WHY THIS EXISTS (the human, 2026-09-27)
---------------------------------------
    "can by same template ui and select <value>, so i can all!"

The human asked for `/llm-tasks/generator/*` — one page per generator, all
sharing ONE template with a `<select>` for the value.

MEASURED, and this is why a registry is REQUIRED rather than optional: the five
generators have FIVE DIFFERENT signatures.

    prompt_generator.compose(skill_key, study_key, dim_values=None, *, db_path=...)
    logic_generator.generate(spec, *, code_questions=False, conn=None, subject_kind=None)
    skill_factor_generator.generate(conn, skill_key)
    factor_generator.build(conn, *, dimension, subject, factor_key='', name='', rule=...)
    terminology_generator.generate(conn, *, catalog_key, entry_key, definition, cite_ref, ...)

There is NO common shape. A single template can therefore only work if each
generator DECLARES, in data, which VALUE it selects on and where the options
come from. That declaration IS this module.

THE VALUE KINDS ARE FOUR, NOT ONE (measured)
--------------------------------------------
    skill_key      73 skills, 63 studies
    table_or_route 207 tables, 8 routes
    dimension      59 skill_factor_registry rows
    catalog        5 catalogs, 1504 terms

So "one universal selector" is IMPOSSIBLE, and "a free-text value" is not a
`<select>`. The only candidate the measurement permits is ONE SELECTOR PER
GENERATOR, and that is what this registry declares.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does NOT run anything. `run()` is a DISPATCHER: it looks up the generator,
validates the value against the declared option source, and calls the module's
own entry point. The generator's own refusals are carried out, never swallowed.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "agent.db"


# ---------------------------------------------------------------------------
# THE REGISTRY — one row per generator.
#
# Each row declares:
#   key        the name in the URL (`/llm-tasks/generator/<key>`)
#   label      what the UI shows
#   module     the python module
#   entry      the function to call
#   selectors  the VALUE(s) this generator selects on, each with:
#                name    the argument name
#                label   what the UI shows
#                source  HOW the options are read (a key into OPTION_SOURCES)
#   cite       where the signature was measured
#
# THE KEY IS THE MODULE NAME, AND THAT IS NOT COSMETIC (fixed 2026-09-27).
# MEASURED, the first version of this file used the SHORT names `prompt`,
# `logic`, `skill`, `factor`, `terminology` — and FOUR of those five were NOT
# registered terms, while the module name was ALREADY registered for four of
# them:
#
#   prompt      NOT REGISTERED   prompt_generator        term_id=1282
#   logic       NOT REGISTERED   logic_generator         term_id=1244
#   skill       NOT REGISTERED   skill_factor_generator  term_id=1337
#   factor      NOT REGISTERED   factor_generator        term_id=1203
#   terminology term_id=1449     terminology_generator   NOT REGISTERED
#
# The human caught it: "template is key factor, but your name is factor?" —
# `key_factor` (term_id=75) IS the definition of `factor_template` (term_id=30),
# and `factor` is not a term at all. So the key is now the REGISTERED module
# name, and `key == module` is asserted by QC-04.
# ---------------------------------------------------------------------------
GENERATORS: tuple[dict[str, Any], ...] = (
    {
        "key": "prompt_generator",
        "label": "Prompt Generator",
        "module": "prompt_generator",
        "entry": "compose",
        # THE 4-STEP WIZARD (the human, 2026-09-27): "step 2) skill + study +
        # wording (selector * 3)".
        #
        # `study_key_for_skill` and `wording_dimensions` are DEPENDENT: their
        # options come from the chosen `skill_key`. MEASURED: `compose` REFUSES a
        # study whose skill_id differs, and "wording" is N dimensions (4 for
        # `mouse_spot_verify`, 1 for `failure_classification`), not one value.
        "selectors": (
            {"name": "skill_key", "label": "Skill",
             "source": "component_skill_key"},
            {"name": "study_key", "label": "Study",
             "source": "study_key_for_skill", "depends_on": "skill_key"},
            {"name": "wording", "label": "Wording",
             "source": "wording_dimensions", "depends_on": "skill_key",
             "expands_to": "one select per dimension"},
        ),
        "cite": ("prompt_generator.py:compose(skill_key, study_key, dim_values)"
                 " -- skill_key is a COMPONENT key: MEASURED 2026-09-28, the "
                 "`skill_key` source offers component_registry keys precisely so "
                 "that the two selectors agree; see _skill_key"),
    },
    {
        "key": "logic_generator",
        "label": "Logic Generator",
        "module": "logic_generator",
        "entry": "generate",
        "selectors": (
            {"name": "subject", "label": "Table or Route", "source": "table_or_route"},
        ),
        "cite": "logic_generator.py:generate(spec, ...) via spec_from_table/spec_from_route",
    },
    {
        "key": "skill_factor_generator",
        "label": "Skill Factor Generator",
        "module": "skill_factor_generator",
        "entry": "generate",
        "selectors": (
            # MEASURED 2026-09-28: this read `skill_registry` (now an EMPTY SHELL),
            # so the select rendered 0 options and every
            # `skill_factor_generator` run was refused with
            # `MISSING_VALUE selector=skill_key`. The source now DISCOVERS the
            # skill register and mirrors the generator's OWN reader,
            # `skill_factor_generator.py:271` (`skill_registry`, 74 active keys).
            #
            # MEASURED: pointing it at the COMPONENT vocabulary instead was WRONG
            # -- it allowed 7 values and then refused the value it had offered
            # (`UNKNOWN_VALUE ... value=citation_discipline`), because that
            # generator does not read `component_registry`.
            {"name": "skill_key", "label": "Skill", "source": "skill_key"},
        ),
        "cite": "skill_factor_generator.py:generate(conn, skill_key)",
    },
    {
        "key": "factor_generator",
        "label": "Factor Generator",
        "module": "factor_generator",
        "entry": "build",
        "selectors": (
            {"name": "dimension", "label": "Dimension", "source": "dimension"},
            {"name": "subject", "label": "Subject", "source": "subject_kind"},
        ),
        "cite": "factor_generator.py:build(conn, *, dimension, subject, ...)",
    },
    {
        "key": "terminology_generator",
        "label": "Terminology Generator",
        "module": "terminology_generator",
        "entry": "generate",
        "selectors": (
            {"name": "catalog_key", "label": "Catalog", "source": "catalog_root"},
            {"name": "entry_key", "label": "Entry", "source": "free_text"},
        ),
        "cite": "terminology_generator.py:generate(conn, *, catalog_key, entry_key, ...)",
    },
)


# ---------------------------------------------------------------------------
# THE OPTION SOURCES — how each selector's `<select>` is filled.
#
# A source is a function `(conn) -> [{"value": ..., "label": ...}, ...]`.
# `free_text` returns [] and the UI renders an input instead of a select.
# ---------------------------------------------------------------------------
def _opts(conn: sqlite3.Connection, sql: str, value_col: str,
          label_col: str | None = None) -> list[dict[str, str]]:
    return _opts_params(conn, sql, (), value_col, label_col)


def _opts_params(conn: sqlite3.Connection, sql: str, params: tuple,
                 value_col: str,
                 label_col: str | None = None) -> list[dict[str, str]]:
    """`_opts` with bound parameters — needed by the DEPENDENT sources.

    MEASURED 2026-09-28, and swallowing the error was a REAL live defect:
    `except sqlite3.Error: return []` made TWO DIFFERENT FACTS INDISTINGUISHABLE
    -- "this source has no rows" and "this source is BROKEN". Both rendered as an
    EMPTY `<select>`.

    What that cost: `study_key` was pointed at a table (`study_registry`) that a
    concurrent task had RENAMED, so its SQL raised, so it returned `[]`, so its
    `<select>` was empty, so EVERY prompt run failed with
    `MISSING_VALUE selector=skill_key` -- and nowhere did the word "no such
    table" appear. An empty select is not a symptom a reader can act on: it looks
    like data, and it is silent.

    THE RULE (this is not a proof, it is the fix): a QUERY that RAISES is a
    DEFECT and is reported as one; a query that SUCCEEDS with zero rows is an
    EMPTY SOURCE and stays a normal, quiet empty select -- because `free_text`
    and the dependent sources legitimately render empty before a parent value is
    chosen. Silence must mean "no rows", never "I could not look".
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        # NOT `return []`. A broken source must NAME itself and its error, or the
        # next reader debugs an empty dropdown instead of a missing table.
        sys.stderr.write(
            "generator_center: OPTION SOURCE FAULT -- %s: %s | sql=%s\n"
            % (type(exc).__name__, exc, " ".join(sql.split())))
        sys.stderr.flush()
        return []
    out = []
    for r in rows:
        v = str(r[value_col] if value_col in r.keys() else r[0])
        lab = str(r[label_col]) if label_col and label_col in r.keys() else v
        out.append({"value": v, "label": lab})
    return out


def _skill_key(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """The SKILL vocabulary — the keys `skill_factor_generator` accepts.

    MEASURED 2026-09-28: this named `skill_registry`, which now exists ONLY AS AN
    EMPTY SHELL (0 rows) because the legacy DDL re-creates it on every start.
    MEASURED: the populated skill register is `skill_registry` (75 rows, 74
    active), and `skill_factor_generator.py:271` reads EXACTLY
    `SELECT skill_key FROM skill_registry WHERE is_active=1`.

    THIS IS A DIFFERENT VOCABULARY FROM THE COMPONENT ONE, and conflating them is
    a real defect this repo has already recorded: `component_registry` has 7 keys
    (yes_no, verdict_3line, ...) while `skill_registry` has 75, and the two have
    near-zero overlap. A selector MUST mirror its own generator's reader, so
    `skill_factor_generator` uses THIS source and `prompt_generator` (whose
    `compose` looks skills up in `component_registry`) uses the component one.
    """
    table = _populated_table(conn, ("skill_registry", "skill_registry"))
    if not table:
        sys.stderr.write("generator_center: no skill register exists under a "
                         "known name\n")
        return []
    return _opts(conn, "SELECT skill_key, name FROM %s "
                       "WHERE is_active=1 ORDER BY skill_key" % table,
                "skill_key", "name")


def _component_skill_key(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """The skill_key `prompt_generator.compose` actually accepts.

    MEASURED 2026-09-27, and this was a REAL defect: `compose` reads
    `component_registry` (`prompt_generator.py:320`), NOT `skill_registry`. The
    two vocabularies have **ZERO overlap** (measured: 0 of the 7 active
    component skill_keys is in `skill_registry`), so offering the 72
    `skill_registry` keys made EVERY prompt run fail with
    `ComposeError: skill not found or inactive`.
    """
    return _opts(conn, "SELECT skill_key, name FROM component_registry "
                       "WHERE is_active=1 ORDER BY skill_key", "skill_key", "name")


def _component_skill_key_options(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """`_component_skill_key` with the register's name discovered.

    MEASURED 2026-09-28: the register was renamed `component_registry` ->
    `component_registry` (68 rows). This wrapper is what the OPTION SOURCES read,
    so the selector renders the same 7 keys before and after the rename instead
    of silently collapsing to 0.
    """
    table = _populated_table(conn, ("component_registry", "component_registry"))
    if not table:
        sys.stderr.write("generator_center: no component register exists under "
                         "a known name\n")
        return []
    return _opts(conn, "SELECT skill_key, name FROM %s "
                       "WHERE is_active=1 ORDER BY skill_key" % table,
                "skill_key", "name")


def _study_key(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """The study_key `prompt_generator.compose` actually accepts.

    MEASURED 2026-09-27: `compose` REFUSES a study whose `skill_id` does not
    match the chosen skill's `skill_id` —
    `ComposeError: study mouse_spot_case belongs to skill_id=5, not yes_no
    (skill_id=1)`. So the two selectors are NOT independent: they are a PAIR
    joined by `study_registry.skill_id = component_registry.skill_id`.

    A `<select>` cannot express that join, so this source offers ONLY the studies
    whose component is ACTIVE — which removes the options that could never work.
    MEASURED: that is 2 -> 1 (only `worker_identity_case`, whose component
    `verdict_3line` is the one active component with a study).

    MEASURED 2026-09-28, and it was a REAL live defect: the table was RENAMED
    `study_registry` -> `study_registry` (63 rows, identical 10 columns) and this
    SQL was left naming the old one, so the source raised and the `study_key`
    select rendered ZERO options -- which made EVERY prompt run fail with
    `MISSING_VALUE selector=skill_key`. `_study_key` is now pointed at the table
    that exists.
    """
    study_t = _populated_table(conn, ("study_registry", "study_registry"))
    comp_t = _populated_table(conn, ("component_registry", "component_registry"))
    if not (study_t and comp_t):
        return []
    return _opts(conn, "SELECT s.study_key, s.name FROM %s s "
                       "JOIN %s c ON c.skill_id = s.skill_id "
                       "WHERE s.is_active=1 AND c.is_active=1 "
                       "ORDER BY s.study_key" % (study_t, comp_t),
                "study_key", "name")


def _table_or_route(conn: sqlite3.Connection) -> list[dict[str, str]]:
    out = []
    try:
        for r in conn.execute("SELECT name FROM sqlite_master "
                              "WHERE type IN ('table','view') "
                              "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            out.append({"value": "table:%s" % r["name"],
                        "label": "table: %s" % r["name"]})
    except sqlite3.Error:
        pass
    try:
        for r in conn.execute("SELECT route_key FROM purpose_route_registry "
                              "WHERE is_active=1 ORDER BY route_key"):
            out.append({"value": "route:%s" % r["route_key"],
                        "label": "route: %s" % r["route_key"]})
    except sqlite3.Error:
        pass
    return out


def _populated_table(conn: sqlite3.Connection,
                     names: tuple[str, ...]) -> str:
    """The register to read from, when a register has TWO names.

    MEASURED 2026-09-28, AND THIS IS THE TRAP: a rename left BOTH names in the
    database. The legacy `db_schema` DDL still says `CREATE TABLE IF NOT EXISTS
    terminology_registry`, so a RESTART RE-CREATES the OLD NAME as an EMPTY
    SHELL beside the real 2416-row `terminology_registry`. Choosing "the first
    name that exists" therefore chose the EMPTY one and every reading came back
    blank -- a discovery bug that looked exactly like missing data.

    So the rule is: prefer a table that EXISTS **AND HAS ROWS**; fall back to an
    existing-but-empty one only when nothing populated exists; return "" when
    neither name exists. A discovery that can silently select an empty shell is
    worse than a hard-coded name, because it fails quietly AND looks correct.
    """
    existing = []
    for t in names:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        (t,)).fetchone():
            existing.append(t)
            if conn.execute("SELECT 1 FROM %s LIMIT 1" % t).fetchone():
                return t
    return existing[0] if existing else ""


def _dimension(conn: sqlite3.Connection) -> list[dict[str, str]]:
    try:
        import skill_5w1h as fw
        return [{"value": n, "label": n} for n in fw.DIMENSION_NAMES]
    except Exception:  # noqa: BLE001 -- a missing optional module returns [] 
        return []


def _subject_kind(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """The registered subject kinds — read from the register that EXISTS.

    MEASURED 2026-09-28: this named `subject_kind_registry`, which no longer
    exists (the register was renamed `subject_kind_registry`, same role), so the
    query RAISED. Before the `_opts_params` fix that raise was swallowed and the
    kind selector silently rendered EMPTY; the fix turned it into a NAMED FAULT
    on stderr -- which is how the missing name was found at all. Pointed at the
    register that exists, with a fallback so a name change is a measurement
    rather than an outage.
    """
    table = _populated_table(conn, ("subject_kind_registry",
                                    "subject_kind_registry"))
    if not table:
        sys.stderr.write("generator_center: no subject kind register exists "
                         "under a known name\n")
        return []
    return _opts(conn, "SELECT kind_key FROM %s "
                       "WHERE is_active=1 ORDER BY kind_key" % table, "kind_key")


def _catalog_root(conn: sqlite3.Connection) -> list[dict[str, str]]:
    table = _populated_table(conn, ("terminology_registry",
                                    "terminology_registry"))
    if not table:
        return []
    return _opts(conn, "SELECT term_key FROM %s "
                       "WHERE parent_term_id IS NULL ORDER BY term_key" % table,
                "term_key")


def _free_text(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return []


# ---------------------------------------------------------------------------
# DEPENDENT SOURCES — a selector whose options depend on ANOTHER selector.
#
# THE HUMAN (2026-09-27): "step 2) skill + study + wording (selector * 3)".
#
# MEASURED, and this is why a dependent source is REQUIRED rather than nice:
#   * `prompt_generator.compose` REFUSES a study whose `skill_id` differs from
#     the skill's, so the study list MUST be filtered by the chosen skill.
#   * "wording" is NOT one selector. `compose` takes `dim_values:
#     {dim_key: wording_key}`, and MEASURED `mouse_spot_verify` declares FOUR
#     dimensions (context / criterion / negation / output) while
#     `failure_classification` declares ONE. So the UI must render ONE SELECT PER
#     DIMENSION, and the count is DATA.
#
# A dependent source is `(conn, parent_value) -> [{"value", "label"}, ...]`.
# ---------------------------------------------------------------------------
def _study_key_for_skill(conn: sqlite3.Connection,
                         skill_key: str) -> list[dict[str, str]]:
    """The studies that BELONG to this skill. The join `compose` enforces.

    JOIN BY COMPONENT ID, AND THAT IS CORRECT. MEASURED 2026-09-27, correcting
    an earlier mis-diagnosis in this file's own plan:

      * `prompt_generator.get_skill` reads `component_registry`, so the
        composition engine's "skill" IS a component.
      * `study_registry.skill_id` is declared
        `REFERENCES component_registry (skill_id)`.
      * MEASURED: all 63 studies' `skill_id` resolves to a `component_registry`
        row (63/63), and the 2 ACTIVE studies resolve to the components they
        should (`worker_identity_case` -> `verdict_3line`, `mouse_spot_case` ->
        `mouse_spot_verify`).

    A key join was tried and REVERTED: `study_registry.study_key` is NOT a
    `component_registry.skill_key` (MEASURED: 2/63 agree), so it returned
    NOTHING and BROKE `verdict_3line`, which had worked.

    WHY `logic_layer_judge` IS EMPTY: its component id is 8, and the study with
    `skill_id=8` is `independent_review`, which is `is_active=0`. The select is
    empty because that study is INACTIVE -- not because the link is wrong.

    MEASURED 2026-09-28: the register was renamed `wording_registry` ->
    `wording_registry` while the legacy DDL kept re-creating the old name as an
    EMPTY SHELL; `study_registry` likewise. Names are DISCOVERED, preferring the
    register that EXISTS AND HAS ROWS.
    """
    if not str(skill_key or "").strip():
        return []
    study_t = _populated_table(conn, ("study_registry", "study_registry"))
    comp_t = _populated_table(conn, ("component_registry", "component_registry"))
    if not (study_t and comp_t):
        sys.stderr.write("generator_center: no study/component register under a "
                         "known name\n")
        return []
    return _opts_params(
        conn,
        "SELECT s.study_key, s.name FROM %s s "
        "JOIN %s c ON c.skill_id = s.skill_id "
        "WHERE s.is_active=1 AND c.is_active=1 AND c.skill_key = ? "
        "ORDER BY s.study_key" % (study_t, comp_t),
        (str(skill_key),), "study_key", "name")


def _wording_dimensions(conn: sqlite3.Connection,
                        skill_key: str) -> list[dict[str, str]]:
    """The WORDING DIMENSIONS this skill declares — one `<select>` each.

    MEASURED: `mouse_spot_verify` -> 4 (context, criterion, negation, output);
    `failure_classification` -> 1. The count is DATA, never a literal.

    MEASURED 2026-09-28: the register was renamed `wording_registry` ->
    `wording_registry` (38 rows) while the legacy DDL kept RE-CREATING the old
    name as an EMPTY SHELL, so these selects rendered ZERO dimensions -- which
    made the wording step impossible to fill. The name is DISCOVERED, preferring
    the register that EXISTS AND HAS ROWS.
    """
    if not str(skill_key or "").strip():
        return []
    w_t = _populated_table(conn, ("wording_registry", "wording_registry"))
    c_t = _populated_table(conn, ("component_registry", "component_registry"))
    if not (w_t and c_t):
        sys.stderr.write("generator_center: no wording/component register under "
                         "a known name\n")
        return []
    return _opts_params(
        conn,
        "SELECT w.dim_key, MIN(w.sort_order) AS o FROM %s w "
        "JOIN %s c ON c.skill_id = w.skill_id "
        "WHERE c.skill_key = ? AND w.is_active = 1 "
        "GROUP BY w.dim_key ORDER BY o, w.dim_key" % (w_t, c_t),
        (str(skill_key),), "dim_key")


def _wording_values(conn: sqlite3.Connection, skill_key: str,
                    dim_key: str) -> list[dict[str, str]]:
    """The wording VALUES of ONE dimension of ONE skill."""
    if not (str(skill_key or "").strip() and str(dim_key or "").strip()):
        return []
    w_t = _populated_table(conn, ("wording_registry", "wording_registry"))
    c_t = _populated_table(conn, ("component_registry", "component_registry"))
    if not (w_t and c_t):
        return []
    return _opts_params(
        conn,
        "SELECT w.wording_key, w.name FROM %s w "
        "JOIN %s c ON c.skill_id = w.skill_id "
        "WHERE c.skill_key = ? AND w.dim_key = ? AND w.is_active = 1 "
        "ORDER BY w.sort_order, w.wording_key" % (w_t, c_t),
        (str(skill_key), str(dim_key)), "wording_key", "name")


OPTION_SOURCES: dict[str, Any] = {
    "skill_key": _skill_key,
    "component_skill_key": _component_skill_key_options,
    "study_key": _study_key,
    "table_or_route": _table_or_route,
    "dimension": _dimension,
    "subject_kind": _subject_kind,
    "catalog_root": _catalog_root,
    "free_text": _free_text,
}

# A source that needs the PARENT selector's value. `depends_on` names the parent.
DEPENDENT_SOURCES: dict[str, Any] = {
    "study_key_for_skill": _study_key_for_skill,
    "wording_dimensions": _wording_dimensions,
}


def required_fields(conn: sqlite3.Connection,
                    generator_key: str) -> dict[str, Any]:
    """What a generator needs BEFORE submit, READ from the ONE step form.

    WHY THIS EXISTS (the human, 2026-09-27):

        "have a table form request all user for this generator need to have
         before submit"

    MEASURED before this: the required inputs lived in `GENERATORS` -- CODE. A
    generator that needs a 4th axis could not be extended without a code change,
    and nothing could tell the UI what to ask for. That is the same defect family
    as the hardcoded `llm_service` type.

    THE STORE MOVED (S8 Option A, the human 2026-09-28): the rows now live in
    `pattern_template` as `subject_kind='generator_field'`, keyed by
    `(subject_kind, subject_ref, item_kind)`. MEASURED: `generator_required_field`
    and `pattern_template` already shared the core VERBATIM (`rule, why,
    sort_order, is_active`, `is_required`), so the merge is a MOVE -- and this
    function is the ONE reader that moved with it, which is why no caller and no
    route had to change.

    THE SHAPE IS UNCHANGED ON PURPOSE. Callers read `field_name`, `kind`,
    `is_required`, `rule`, `why`, `sort_order`; those names are produced here by
    ALIASING the new columns (`item_kind AS field_name`), so the merge is
    invisible to everything downstream. A reader that returned the new column
    names instead would have forced every caller to change in lockstep, which is
    the opposite of a merge.

    The rows are the DECLARATION; this function is the READER. A generator with
    no rows is a REFUSAL, not an empty list -- an empty list reads as "nothing
    needed", which is the opposite of the truth.
    """
    rows = [dict(r) for r in conn.execute(
        "SELECT item_kind AS field_name, kind, is_required, rule, why, sort_order "
        "FROM pattern_template "
        "WHERE subject_kind='generator_field' AND subject_ref=? AND is_active=1 "
        "ORDER BY sort_order, item_kind",
        (str(generator_key),))]
    if not rows:
        return {"ok": False, "generator_key": str(generator_key),
                "fields": [], "count": 0,
                "reason": ("no pattern_template row with "
                           "subject_kind='generator_field' declares what %r "
                           "needs before submit" % generator_key)}
    return {"ok": True, "generator_key": str(generator_key),
            "fields": rows, "count": len(rows),
            "cite": ("pattern_template (subject_kind='generator_field', "
                     "subject_ref=%r) -- a ROW, not a Python literal"
                     % generator_key)}


def study_template(conn: sqlite3.Connection,
                   study_kind: str | None = None) -> dict[str, Any]:
    """The declared study subjects, optionally filtered by `study_kind`.

    WHY THIS EXISTS (the human, 2026-09-27): "seems study template is totally
    missing? can you help to backfill that for me".

    MEASURED before this: `study_registry` had 63 rows and NO declaration of
    what a study must carry, so a study could be created with no fields at all
    and nothing could refuse it.
    """
    sql = ("SELECT id, study_kind, subject, cite_ref FROM study_template "
           "WHERE is_active=1")
    params: tuple = ()
    if study_kind:
        sql += " AND study_kind=?"
        params = (str(study_kind),)
    sql += " ORDER BY study_kind, subject"
    rows = [dict(r) for r in conn.execute(sql, params)]
    return {"ok": True, "study_kind": study_kind, "rows": rows,
            "count": len(rows),
            "cite": "study_template (modelled on factor_template)"}


def _why_empty_study(conn: sqlite3.Connection | None,
                     skill_key: str) -> str | None:
    """WHY `_study_key_for_skill` returned nothing. It NAMES the reason.

    MEASURED 2026-09-27: `logic_layer_judge` (component id 8) HAS a study --
    `independent_review` (study_id 12) -- but that study is `is_active=0`. So
    the select is empty because the study is INACTIVE, not because the link is
    missing. A `why` that says "no study_key_for_skill" names the SOURCE, not
    the reason, and sends the reader looking for a missing row that is there.
    """
    if conn is None:
        return None
    study_t = _populated_table(conn, ("study_registry", "study_registry"))
    comp_t = _populated_table(conn, ("component_registry", "component_registry"))
    if not (study_t and comp_t):
        return None
    rows = conn.execute(
        "SELECT s.study_key, s.is_active FROM %s s "
        "JOIN %s c ON c.skill_id = s.skill_id "
        "WHERE c.skill_key = ? ORDER BY s.is_active DESC, s.study_key"
        % (study_t, comp_t),
        (str(skill_key),)).fetchall()
    if not rows:
        return "no study is attached to %s" % skill_key
    inactive = [r["study_key"] for r in rows if not r["is_active"]]
    if inactive:
        return ("%s has a study (%s) but it is INACTIVE"
                % (skill_key, ", ".join(inactive)))
    return None


def registry(conn: sqlite3.Connection | None = None,
             values: dict[str, str] | None = None) -> dict[str, Any]:
    """The registry, with each selector's OPTIONS filled from its source.

    `conn=None` returns the registry WITHOUT options — the shape is still
    complete, so a caller can see what WOULD be selected.

    `values` (added 2026-09-27) supplies the PARENT selector values a DEPENDENT
    source needs. MEASURED: `study_key_for_skill` and `wording_dimensions` cannot
    be filled without the chosen `skill_key`, so a registry call with no values
    returns them EMPTY and says so (`needs`), rather than silently offering the
    wrong options.
    """
    vals = {str(k): str(v) for k, v in (values or {}).items()}
    out = []
    for g in GENERATORS:
        sels = []
        for s in g["selectors"]:
            src = str(s["source"])
            parent = str(s.get("depends_on") or "")
            parent_val = vals.get(parent, "") if parent else ""
            if parent and not parent_val:
                # A dependent source with no parent value has NO options, and
                # that is REPORTED, not hidden.
                sels.append({"name": s["name"], "label": s["label"],
                             "source": src, "options": [], "option_count": 0,
                             "free_text": False, "depends_on": parent,
                             "needs": parent,
                             "why": "needs %s first" % parent})
                continue
            if parent:
                fn = DEPENDENT_SOURCES.get(src)
                opts = fn(conn, parent_val) if (conn is not None and fn) else []
            else:
                fn = OPTION_SOURCES.get(src)
                opts = fn(conn) if (conn is not None and fn is not None) else []
            row = {"name": s["name"], "label": s["label"],
                   "source": src, "options": opts,
                   "option_count": len(opts),
                   "free_text": src == "free_text"}
            if parent:
                row["depends_on"] = parent
                # AN EMPTY DEPENDENT MUST SAY WHY. MEASURED 2026-09-27: when the
                # parent value IS present but the source returns 0 rows, the row
                # was emitted with `options: []` and NO `why`, so the UI showed a
                # bare `-- select (0) --`. An empty selector is then
                # indistinguishable from a broken one -- which is exactly why the
                # human had to ask "not 3 value? why".
                #
                # The `why` NAMES THE REASON, not the source. MEASURED: the
                # study for `logic_layer_judge` EXISTS and is INACTIVE, so a
                # "no study_key_for_skill" message would send the reader looking
                # for a row that is there.
                if not opts:
                    why = None
                    if src == "study_key_for_skill":
                        why = _why_empty_study(conn, parent_val)
                    row["why"] = why or ("no %s for %s=%s"
                                         % (src, parent, parent_val))
            if s.get("expands_to"):
                row["expands_to"] = s["expands_to"]
                # AN EXPANSION CARRIES ITS CHILDREN'S VALUES. MEASURED: the
                # `wording` selector's options are the DIMENSIONS, and each
                # dimension has its own values. Sending them in ONE response means
                # the UI does not have to make N extra calls, and the values can
                # never be stale relative to the dimensions they belong to.
                if conn is not None and parent_val:
                    dv = {str(o["value"]): _wording_values(conn, parent_val,
                                                           str(o["value"]))
                          for o in opts}
                    # BOTH NAMES, because two readers exist: the wizard reads
                    # `values_by_dim`, and `dimension_values` is the name the
                    # first draft used. Emitting one name only would make the
                    # other reader silently see NO values -- the "detector that
                    # cannot find anything" defect.
                    row["dimension_values"] = dv
                    row["values_by_dim"] = dv
            sels.append(row)
        out.append({"key": g["key"], "label": g["label"],
                    "module": g["module"], "entry": g["entry"],
                    "selectors": sels, "cite": g["cite"]})
    return {"ok": True, "generators": out, "count": len(out),
            "cite": "measured: the five signatures in generator_center.GENERATORS"}


def find(key: str) -> dict[str, Any] | None:
    k = str(key or "").strip()
    for g in GENERATORS:
        if g["key"] == k:
            return g
    return None


def run(conn: sqlite3.Connection, key: str,
        values: dict[str, str]) -> dict[str, Any]:
    """DISPATCH to the named generator. The generator's own refusals are carried out.

    REFUSES:
      * `UNKNOWN_GENERATOR` — the key is not in the registry.
      * `MISSING_VALUE`     — a declared selector has no value.
      * `UNKNOWN_VALUE`     — the value is not in the selector's option source
                              (skipped for a `free_text` source).
    """
    g = find(key)
    if g is None:
        return {"ok": False, "code": "UNKNOWN_GENERATOR", "key": str(key),
                "declared": [x["key"] for x in GENERATORS]}
    vals = {str(k): str(v) for k, v in (values or {}).items()}
    for s in g["selectors"]:
        name = str(s["name"])
        src = str(s["source"])
        # AN EXPANSION IS NOT A VALUE. `wording` declares the DIMENSIONS a skill
        # has; the wizard sends one value per dimension as `wording.<dim_key>`.
        # Demanding a value named `wording` would refuse every correct call.
        if s.get("expands_to"):
            continue
        if not vals.get(name, "").strip():
            return {"ok": False, "code": "MISSING_VALUE", "generator": g["key"],
                    "selector": name}
        if src == "free_text":
            continue
        parent = str(s.get("depends_on") or "")
        if parent:
            fn = DEPENDENT_SOURCES.get(src)
            allowed = {o["value"] for o in
                       (fn(conn, vals.get(parent, "")) if fn else [])}
        else:
            fn = OPTION_SOURCES.get(src)
            allowed = {o["value"] for o in (fn(conn) if fn else [])}
        if allowed and vals[name] not in allowed:
            return {"ok": False, "code": "UNKNOWN_VALUE", "generator": g["key"],
                    "selector": name, "value": vals[name],
                    "allowed_count": len(allowed)}
    try:
        mod = __import__(str(g["module"]))
        fn = getattr(mod, str(g["entry"]))
    except Exception as exc:  # noqa: BLE001 -- reported as GENERATOR_UNAVAILABLE
        return {"ok": False, "code": "GENERATOR_UNAVAILABLE",
                "generator": g["key"],
                "message": "%s: %s" % (type(exc).__name__, exc)}
    try:
        if g["key"] == "logic_generator":
            spec = _logic_spec(conn, vals["subject"])
            result = fn(spec, conn=conn, subject_kind=spec.get("kind"))
        elif g["key"] == "prompt_generator":
            # THE WORDING DIMENSIONS (2026-09-27). `compose` takes
            # `dim_values: {dim_key: wording_key}`. TWO SHAPES ARE ACCEPTED,
            # because two callers exist:
            #   * `dim_values`  -- a nested dict (the wizard sends this)
            #   * `wording.<dim_key>` -- a flat key (the first draft sent this)
            # Accepting only one would make the other caller's dimensions
            # silently VANISH, and `compose` SKIPS an unfilled dimension -- so
            # the prompt would be composed from fewer parts than the human chose,
            # with no error. That is the failure this accepts both to prevent.
            dim_values = {k.split(".", 1)[1]: v
                          for k, v in vals.items()
                          if k.startswith("wording.") and str(v).strip()}
            # READ THE NESTED DICT FROM THE ORIGINAL `values`, NOT FROM `vals`.
            # MEASURED DEFECT: `vals` is built with `str(v)` for every entry, so
            # `vals["dim_values"]` is the STRING `"{'bogus_dim': 'x'}"` and
            # `isinstance(..., dict)` is False -- the nested shape was silently
            # DROPPED and `compose` received NO dimensions. A dropped dimension is
            # SKIPPED by `compose`, so the prompt was composed from fewer parts
            # than the human chose, with no error.
            nested = (values or {}).get("dim_values")
            if isinstance(nested, dict):
                for k, v in nested.items():
                    if str(v).strip():
                        dim_values[str(k)] = str(v)
            result = fn(vals["skill_key"], vals["study_key"], dim_values,
                        db_path=DEFAULT_DB)
        elif g["key"] == "skill_factor_generator":
            result = fn(conn, vals["skill_key"])
        elif g["key"] == "factor_generator":
            result = fn(conn, dimension=vals["dimension"], subject=vals["subject"])
        elif g["key"] == "terminology_generator":
            result = fn(conn, catalog_key=vals["catalog_key"],
                        entry_key=vals["entry_key"],
                        definition=vals.get("definition") or
                        "generated from the Generator Center",
                        cite_ref=vals.get("cite_ref") or
                        "generator_center.py:1")
        else:
            return {"ok": False, "code": "NO_DISPATCH", "generator": g["key"]}
    except Exception as exc:  # noqa: BLE001 -- reported as GENERATOR_RAISED
        return {"ok": False, "code": "GENERATOR_RAISED", "generator": g["key"],
                "message": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "generator": g["key"], "values": vals,
            "result": result, "cite": g["cite"]}


# ---------------------------------------------------------------------------
# THE 4-STEP WIZARD (the human, 2026-09-27)
#
#   step 1  select generator
#   step 2  skill + study + wording (selector * 3)
#   step 3  get prompt template WITH a confirm button
#   step 4  confirm
#
# STEP 3 AND STEP 4 ARE TWO CALLS, and that is the point: `preview` COMPOSES and
# writes NOTHING, so the human sees the exact text and its sha256 BEFORE anything
# is stored. `confirm` is the write. A single "Run" that both composes and stores
# would make step 3 and step 4 the same act, which is what the human is asking to
# separate.
# ---------------------------------------------------------------------------
def preview(conn: sqlite3.Connection, key: str,
            values: dict[str, str]) -> dict[str, Any]:
    """STEP 3 — compose the prompt and WRITE NOTHING.

    Returns the composed text, its `sha256`, its `parts` count and its
    `composition_key`, so the human can verify what WILL be sent rather than
    trusting a label. `prompt_generator.compose` already returns all four; this
    adds the `writes: 0` statement so the caller does not have to infer it.
    """
    out = run(conn, key, values)
    if out.get("ok") is False:
        return out
    res = out.get("result") or {}
    return {"ok": True, "step": 3, "generator": out.get("generator"),
            "values": out.get("values"),
            "prompt_text": res.get("prompt_text"),
            "sha256": res.get("sha256"),
            "parts": res.get("parts"),
            "composition_key": res.get("composition_key"),
            "dim_values": res.get("dim_values"),
            "writes": 0,
            "cite": out.get("cite")}


def confirm(conn: sqlite3.Connection, key: str, values: dict[str, str],
            *, version_label: str = "dim_v1") -> dict[str, Any]:
    """STEP 4 — compose AND store. The write.

    REFUSES unless the generator is `prompt_generator`: the other four produce a
    SPEC or a ROW, not a prompt document, and storing one as a prompt would be a
    category error. Their own write paths are unchanged.

    The write goes through `upsert_skill_prompt`, the SAME function
    `POST /api/skills/<skill_key>/compose {apply:true}` already uses — so there is
    ONE write path, not two.
    """
    if str(key) != "prompt_generator":
        return {"ok": False, "code": "NOT_A_PROMPT_GENERATOR", "generator": str(key),
                "why": ("only `prompt_generator` produces a prompt document; the "
                        "other four produce a spec or a row and have their own "
                        "write paths")}
    out = run(conn, key, values)
    if out.get("ok") is False:
        return out
    res = out.get("result") or {}
    vals = out.get("values") or {}
    try:
        import mouse_spot_helper as msh
        # MEASURED 2026-09-27: `upsert_skill_prompt` has NO `cite_ref` parameter
        # (skill_prompt.py:413-430). Passing one raised
        #   TypeError: upsert_skill_prompt() got an unexpected keyword argument 'cite_ref'
        # and the live Confirm returned STORE_RAISED. The provenance goes in
        # `notes`, which the signature DOES accept.
        stored = msh.upsert_skill_prompt(
            conn, skill_key=str(vals.get("skill_key")),
            prompt_key=str(res.get("composition_key") or ""),
            version_label=str(version_label or "dim_v1"),
            prompt_text=str(res.get("prompt_text") or ""),
            status="testing", activate=False,
            notes="cite: generator_center.py:confirm")
    except Exception as exc:  # noqa: BLE001 -- reported as STORE_RAISED
        return {"ok": False, "code": "STORE_RAISED", "generator": str(key),
                "message": "%s: %s" % (type(exc).__name__, exc)}
    return {"ok": True, "step": 4, "generator": out.get("generator"),
            "values": vals,
            "prompt_key": res.get("composition_key"),
            "sha256": res.get("sha256"),
            "version_label": str(version_label or "dim_v1"),
            "stored": stored,
            "cite": "generator_center.py:confirm -> upsert_skill_prompt"}


def _logic_spec(conn: sqlite3.Connection, subject: str) -> dict[str, Any]:
    """A spec from a `table:<name>` or `route:<key>` value.

    MEASURED: `logic_generator.spec_from_table` builds `PRAGMA table_info(%s)`
    WITHOUT quoting, so the value must be the BARE table name — passing
    `table:chat` raised `near ":chat": syntax error`. The prefix is stripped
    HERE, which is the dispatcher's job.
    """
    import logic_generator as lg
    s = str(subject)
    if s.startswith("route:"):
        return lg.spec_from_route(conn, s[6:])
    return lg.spec_from_table(conn, s[6:] if s.startswith("table:") else s)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", action="store_true")
    ap.add_argument("--run", metavar="KEY", default=None)
    ap.add_argument("--value", action="append", default=[],
                    help="name=value, repeatable")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(str(DEFAULT_DB))
    conn.row_factory = sqlite3.Row
    try:
        if a.run:
            vals = {}
            for v in a.value:
                if "=" in v:
                    k, _, val = v.partition("=")
                    vals[k.strip()] = val.strip()
            out = run(conn, a.run, vals)
        else:
            out = registry(conn)
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
