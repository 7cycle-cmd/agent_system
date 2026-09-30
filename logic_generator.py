"""logic_generator.py — turn a SPEC into the QUESTION LIST a proof run asks.

THE MISSING HALF OF 蒸馏 (the user, 2026-09-22)
---------------------------------------------
    "Data = skill > 5W1H -> factor -> 蒸餾"
    "this is the missing part for 蒸餾, can upgrade 蒸餾!!!"
    "it can help template collect multi dismensional, not for skill only , can
     for all logic / skill / prompt generator"

`factor_distill.py` distils a factor FROM data — the data comes first, and the
factor is what the data supports. That is the INWARD direction.

This module is the OUTWARD direction: given a factor, WHAT QUESTIONS must be
asked to measure it. Without it a distilled factor has no way to be measured,
which is why `skill_factor_proof` has **0 rows** — the chain exists as tables and
has no data flowing through it.

    data  --distill-->  factor          (factor_distill.py, EXISTS)
    factor --generate--> question list  (THIS MODULE, was MISSING)
    question list --run--> evidence     (llm_100_run, EXISTS)
    evidence --streak--> is_active      (activation_gate.py, EXISTS)

THE USER'S EXAMPLE, WHICH IS THE SPEC
-------------------------------------
    "example / table A with 3 field / 100 run need to proof
     1) is it DB driven = id = primary + auto inscrease
     2) how many field does it have, is that = 3
     3) field 1 name = x, YES or NO, explain why
     ...
     9) does this table register at db_table_registry, YES or NO, explain why
     10) does this table register at db_table_registry and is_active = 0, YES or
         NO, explain why"

    [QUOTED VERBATIM. The user wrote `db_table_registry`; the LIVE table is
     `db_table_registry` (MEASURED 2026-09-24: `db_table_registry` does not
     exist). The quote is kept as written so the example stays the user's, but
     the CODE uses the real name — see `CLOSING_QUESTIONS` below.]

**10 questions for 3 fields.** The count is a FUNCTION of the field count:

    questions = 2 (structure + cardinality)
              + N (names)
              + N (types)
              + 2 (registration + activation)
              = 2N + 4

For N=3: 10. With the YES/NO pair: 20 cases. **The count is DERIVED and PRINTED,
never chosen.** A hand-chosen 100 is the "1 test x100" defect `_drive_streaks.py`
already names.

EVERY QUESTION HAS A YES FORM AND A NO FORM
-------------------------------------------
    "have the YES question, then have the NO question"

A question with only its YES form CANNOT FAIL: a model can answer YES to
everything and pass. The NO form is the NEGATIVE CONTROL, and it is what makes
the run discriminating. `generate()` REFUSES a question that has no NO form.

IT TAKES A SPEC, NOT A SKILL
----------------------------
    "not for skill only , can for all logic / skill / prompt generator"

So the input is a SPEC (a 5W1H answer set + a factor), and a skill is ONE caller.
`spec_from_skill()` is a convenience, not the interface.

Run:
    .\\.venv\\Scripts\\python.exe logic_generator.py --example
    .\\.venv\\Scripts\\python.exe logic_generator.py --spec spec.json
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
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# The 5W1H dimensions, IMPORTED from the one place they are declared. A second
# copy here is the drift `skill_5w1h`'s own "Not to do" forbids.
import skill_5w1h as fw  # noqa: E402

# The dimension each question family comes FROM. This mapping is the point: the
# questions are not a hand-written list, they are DERIVED from the 5W1H
# dimensions, so a new dimension would produce new questions.
DIM_STRUCTURE = "how"      # how is it built
DIM_CARDINALITY = "what"   # what is it, how many parts
DIM_NAMING = "what"        # what is each part called
DIM_TYPING = "what"        # what type is each part
DIM_REGISTRATION = "where"  # where does it live
DIM_ACTIVATION = "when"    # when is it proven

# The two structural questions, which do not scale with the field count.
STRUCTURE_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("db_driven",
     "Is it DB driven — is `id` a PRIMARY KEY with AUTOINCREMENT?",
     DIM_STRUCTURE),
    ("field_count",
     "How many fields does it have — is that {n}?",
     DIM_CARDINALITY),
)
# The two closing questions.
#
# THE TABLE IS `db_table_registry`, NOT `db_table_registry`. MEASURED
# (2026-09-24): the question text named `db_table_registry`, a table that does
# NOT exist, while the reader queried `db_table_registry`. A question that names
# a non-existent table is unanswerable by the reader it is paired with, and a
# reader asked to satisfy a typo can only be "fixed" by making it wrong. The
# text and the query now name the SAME table, and a proof asserts it.
CLOSING_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("registered",
     "Does this table register at `db_table_registry`?",
     DIM_REGISTRATION),
    ("registered_inactive",
     "Does this table register at `db_table_registry` AND have `is_active = 0`?",
     DIM_ACTIVATION),
)

# THE CODE-SHAPE QUESTIONS (added 2026-09-23). They are OPT-IN, because they ask
# about a FILE and a NAME rather than a table, so a spec must carry `path`/`term`
# for them to be answerable. `generate(..., code_questions=True)` adds them; the
# default keeps the count `2N + 4` that the user's own example states, so adding
# this axis does NOT silently change every existing count.
#
# Their 5W1H dimensions are declared, not chosen: "is the name registered" is
# `who` (which system owns this name — the `who` gap `factor_first_principle`
# records as the one that already cost us a duplicate), and "was the input
# overwritten" is `how` (how the value is produced).
DIM_REGISTRATION_OWNER = "who"
DIM_VALUE_ORIGIN = "how"
# "is this rule WIRED" is `when` — when does it run? A rule that only runs when a
# proof asks is a rule the writer never sees (the human, 2026-09-28:
# "真正的 skill 化（寫檔時自動量）還沒做").
DIM_ACTIVATION_TRIGGER = DIM_ACTIVATION

CODE_QUESTIONS: tuple[tuple[str, str, str], ...] = (
    ("name_registered",
     "Is the name this code uses a REGISTERED term in `terminology_registry`?",
     DIM_REGISTRATION_OWNER),
    # THE SCHEMA-INTEGRITY QUESTION (added 2026-09-29). MEASURED: this generator
    # could ask whether a name is registered and whether a value is derivable, so
    # it could see the PARTS — but it had NO way to ask whether the TABLE's own
    # declarations are legal. A FK attached to a parent key that SQLite cannot
    # use is a schema claim that is FALSE at run time, and 2 tables carried one.
    #
    # A native FK is only legal on the parent's PRIMARY KEY, and only for a
    # MANDATORY + LOAD-BEARING reference (human-locked policy, 2026-09-29).
    ("fk_parent_key_legal",
     "Is every FOREIGN KEY on this table attached to the parent's PRIMARY KEY "
     "or a plain UNIQUE key — never an EXPRESSION index?",
     DIM_REGISTRATION_OWNER),
    ("literal_derivable",
     "Does this file hard-code a literal that a registry could have supplied?",
     DIM_VALUE_ORIGIN),
    ("param_shadowed",
     "Is a parameter OVERWRITTEN in its own function body (so the output is "
     "true by construction)?",
     DIM_VALUE_ORIGIN),
    # THE WIRING QUESTION (added 2026-09-28). WHY IT HAD TO EXIST: the human
    # complained that a step can be fully PREPARED and still NOT WIRED —
    #   "so i don't need to with you to have all perperation and finally not wired"
    # — and MEASURED, `logic_generator` had NO way to ask it. It could ask "is the
    # name registered" and "is the value derivable", so it could see the PARTS,
    # and the one thing it could not see is whether anyone RUNS them. A tool that
    # cannot ask the question the human is asking will always answer a DIFFERENT
    # question well.
    ("reader_wired",
     "Is a reader for this file RAN by an automatic caller (so the rule is "
     "measured when a file is WRITTEN, not only when a proof asks)?",
     DIM_ACTIVATION_TRIGGER),
)


class SpecError(ValueError):
    """Raised when a spec cannot produce a question list."""


# ---------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------

def spec_from_skill(conn, skill_key: str) -> dict[str, Any]:
    """A SPEC derived from a skill's 5W1H answers + its factors.

    A convenience, NOT the interface: the generator takes a spec, and a skill is
    one way to produce one.
    """
    factors = [dict(r) for r in conn.execute(
        "SELECT factor_key, name, rule_definition, metric_kind, metric_unit, "
        "metric_target FROM skill_factor_registry WHERE is_active=1 "
        "ORDER BY sort_order, factor_key")]
    return {
        "subject": skill_key,
        "kind": "skill",
        "dimensions": {n: "" for n in fw.DIMENSION_NAMES},
        "factors": factors,
    }


# ---------------------------------------------------------------------------
# THE WORDING SOURCE (Phase 3, added 2026-09-24).
#
# WHY (the user): "wording should not by hardcode, should by worker?"
# "question for wording is how to trace the source?" "wording can by logic
# generator?"
#
# MEASURED before this: `logic_generator` read **ZERO** registers. It carried its
# question text as module constants (`STRUCTURE_QUESTIONS`, `CLOSING_QUESTIONS`,
# `CODE_QUESTIONS`), and `dimension_binding_registry.py:399-403` claimed
# `bindings_for()` is "what logic_generator reads" — a FALSE docstring.
#
# THE TWO SOURCES ARE DIFFERENT LAYERS, and neither replaces the other:
#
#   `skill_5w1h.DIMENSIONS`        WHAT is asked      ("Where does the artifact
#                                                     live — the exact path?")
#   `dimension_binding_registry`   WHAT COUNTS, for   ("the file path + line of
#                                  THIS subject kind   the DDL that creates the
#                                                     table")
#
# So the wording is COMPOSED, not chosen: the dimension supplies the question and
# the binding supplies the criterion for the subject at hand. A binding ALONE
# would be a sentence with no question; a dimension ALONE would be a question
# with no subject-specific criterion.
#
# IT IS OPT-IN AND IT DEGRADES HONESTLY. A kind with no binding (or a partial
# one) falls back to the dimension's own question, and the RESULT REPORTS which
# source each question used. A caller can therefore tell "the register supplied
# this wording" from "this is the fallback", instead of both looking identical —
# the same rule `oracle_for` follows for its own fallback.
# ---------------------------------------------------------------------------

SOURCE_GATE = "gate"
SOURCE_registry = "register"
SOURCE_DIMENSION = "dimension"


def wording_units(conn: sqlite3.Connection | None,
                  skill_id: int) -> dict[str, Any]:
    """The MEASURED UNITS of a skill's prose, READ from `wording_registry`.

    WHY (the human, 2026-09-25): *"logic generation can help to have factor ->
    output = measured unit too, 散文 can be proofed by measured unit as that are
    combination of measured unit"*.

    MEASURED BEFORE THIS: `logic_generator` read `dimension_binding_registry` and
    `skill_factor_registry`, and read `wording_registry` **ZERO** times. So a
    generated question carried wording whose units nobody had measured.

    The unit IS the `dim_key` (see `prompt_generator.unit_verdict`), and the
    verdict is AND (decision 甲). This function is the READER; the measurement
    lives in `prompt_generator` so there is ONE implementation, not two.

    Never raises: an unreadable register yields `{ok: False, ...}` and the caller
    reports the gap rather than losing the question.
    """
    if conn is None:
        return {"ok": False, "code": "NO_CONNECTION", "units": {},
                "proof_type": "none", "unit_count": 0}
    try:
        import prompt_generator as _pg
        v = _pg.unit_verdict(conn, int(skill_id))
    except Exception as exc:
        return {"ok": False, "code": "UNREADABLE", "message": str(exc),
                "units": {}, "proof_type": "none", "unit_count": 0}
    return {
        "ok": bool(v["ok"]),
        "units": v["units"],
        "unit_count": v["unit_count"],
        "proof_type": v["proof_type"],
        "failed_units": sorted(d for d, u in v["units"].items() if not u["ok"]),
    }


def dimension_wording_with_source(
        conn: sqlite3.Connection | None,
        subject_kind: str) -> dict[str, tuple[str, str]]:
    """The dimension -> (text, SOURCE) map for a subject kind.

    THE GATE IS READ FIRST (C3b, 2026-09-27). `derive_5w1h.fields` is the ONLY
    reader that filters `is_active`, so a gate-backed wording is one the
    activation gate DECIDED. The register is the FALLBACK where the gate REFUSES.

    WHY THIS IS A SEPARATE FUNCTION (C3b-F5): `dimension_wording` returns a plain
    `{dim: str}` and `runtime_trace.report` puts that dict straight into its JSON.
    Widening the return type would break that caller, so the SOURCE is carried
    here and `dimension_wording` stays a thin wrapper over this.

    MEASURED before C3b: the gate read sat in `generate()`, so
    `runtime_trace.report` — which calls `dimension_wording` directly — bypassed
    it, and flipping `is_active` left its wording unchanged.
    """
    import skill_5w1h as _fw
    out: dict[str, tuple[str, str]] = {}
    bindings: dict[str, str] = {}
    gate_text: dict[str, str] = {}
    if conn is not None:
        try:
            import dimension_binding_registry as _dbr
            bindings = _dbr.bindings_for(conn, subject_kind)
        except Exception:
            bindings = {}
        # THE GATE WINS WHERE IT HAS AN ANSWER; the register is the FALLBACK
        # where the gate REFUSES. MEASURED: the gate REFUSES `function` and
        # `module` (MISSING_BINDING), and `runtime_trace` reports on `function`
        # — so a gate-only rule would REFUSE the watchdog report. The fallback
        # is what resolves that, not a special case.
        try:
            import derive_5w1h as _d5
            g = _d5.fields(conn, subject_kind)
            if g.get("ok"):
                for f in (g.get("fields") or []):
                    q = str(f.get("question") or "").strip()
                    bt = str(f.get("binding_text") or "").strip()
                    if q:
                        gate_text[str(f["field_name"])] = (
                            "%s Criterion: %s." % (q, bt) if bt else q)
        except Exception:
            gate_text = {}
    for name, question, _rule, _mand in _fw.DIMENSIONS:
        if name in gate_text:
            out[name] = (gate_text[name], SOURCE_GATE)
            continue
        crit = str(bindings.get(name) or "").strip()
        if crit:
            out[name] = ("%s Criterion: %s." % (question, crit), SOURCE_registry)
        else:
            out[name] = (question, SOURCE_DIMENSION if conn is not None else "")
    return out


def dimension_wording(conn: sqlite3.Connection | None,
                      subject_kind: str) -> dict[str, str]:
    """The dimension -> wording map for a subject kind, READ from the registers.

    Returns `{dimension: text}` plus nothing else — the per-question SOURCE is
    tracked by `dimension_wording_with_source`, which this wraps.

    For each of the six dimensions:
      * the GATE's wording wins where the gate has an answer (`derive_5w1h`);
      * otherwise the register's binding text is APPENDED as the criterion,
        because that is what the binding is FOR ("the SAME 5W1H dimension binds
        differently per subject kind");
      * otherwise the DIMENSION's own question is the base (`skill_5w1h`).

    Never raises: an unreadable register yields the dimension-only wording, and
    the caller reports the fallback rather than losing the question entirely.
    """
    return {k: v[0] for k, v in
            dimension_wording_with_source(conn, subject_kind).items()}


def _additive_columns_name(table: str) -> str:
    """The `<CONSTANT>_NEW_COLUMNS` list that declares `table`'s added columns.

    The declaration of a table is SPLIT IN TWO in `db_schema`: the `CREATE TABLE`
    text, plus a `<X>_NEW_COLUMNS` list that `_add_columns_if_missing` applies
    with `ALTER TABLE ADD COLUMN`. This finds the second half.

    The match is by the list's OWN `_TABLE`/`_TABLE_SUFFIX` constant naming the
    table, so it is DATA (a naming convention that `db_schema` already follows)
    rather than a per-table lookup that would go stale. Returns `''` when there
    is no such list, which is the common case (only 8 exist).
    """
    import db_schema as ds
    for name in dir(ds):
        if not name.endswith("_NEW_COLUMNS"):
            continue
        val = getattr(ds, name)
        if not isinstance(val, list):
            continue
        # `DEV_TASK_NEW_COLUMNS` -> `DEV_TASK` -> `dev_task`
        stem = name[: -len("_NEW_COLUMNS")].lower()
        # EXACT MATCH ONLY. MEASURED, and my first version was WRONG: a
        # `table.startswith(stem + "_")` rule matched `dev_task_field` to
        # `DEV_TASK_NEW_COLUMNS`, so `dev_task_field` was told it must contain
        # `current_model`/`error`/`qc_summary` — three columns that belong to a
        # DIFFERENT table. That is a parser defect manufacturing a defect, the
        # same family as the hard-coded `INT` and the ghost `ON` column. All 8
        # lists name their table exactly (`DEV_TASK_NEW_COLUMNS` -> `dev_task`),
        # so exact match loses nothing and invents nothing.
        if table.lower() == stem:
            return name
    return ""


# ADDITIVE DECLARATIONS THAT LIVE OUTSIDE `db_schema`.
#
# MEASURED (2026-09-24): the declaration of a table can live in FOUR places, and
# `db_schema` holds only two of them. `workflow_step`'s `layer_key`/`step_kind`/
# `question_template`/`expected`/`parser` are added by `question_flow.ensure_schema`
# from `question_flow._STEP_COLUMNS`, so a reader that knows only `db_schema`
# reports those five as "live but undeclared" on a CORRECT table — a FALSE
# FAILURE, the same defect class as the hard-coded `INT`.
#
# This is DATA, not a branch: `{table: [(module, CONSTANT), ...]}`. A new
# cross-module additive migration is a ROW here, and the residual drift after
# this mapping is the TRUE residual rather than a parser blind spot.
ADDITIVE_DECLARATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "workflow_step": (("question_flow", "_STEP_COLUMNS"),),
    "workflow_registry": (("question_flow", "_FLOW_COLUMNS"),),
    "skills": (("skill_library_api", "_SKILL_CATALOG_COLUMNS"),),
}


# RULE-BASED ADDITIVE DECLARATIONS: a migration that adds a column to MANY
# tables by a CONDITION rather than a per-table list.
#
# MEASURED (2026-09-24): `migrate_skill_ref.py` adds `skill_ref INTEGER` to EVERY
# table that has a `skill_key` column EXCEPT `SKIP = {skill_registry,
# component_registry, skill_contract_template}` (which already have `skill_ref`
# by a different route). No per-table list exists, so a reader that knows only the
# constants reports 13 CORRECT tables as drifted.
#
# The rule is reproduced as DATA: `(column, TYPE, requires_column, skip_tables)`.
# The SKIP set is COPIED FROM `migrate_skill_ref.SKIP` and the proof asserts the
# two agree, because a rule that quietly diverges from the migration it mirrors is
# the drift it was written to detect.
#
# TWO migrations add `skill_ref`, and they key on DIFFERENT columns:
#   * `migrate_skill_ref.py`    keys on a TEXT `skill_key` (13 tables)
#   * `migrate_skill_id_int.py` keys on a TEXT `skill_id`  (the Phase 3 tables,
#     e.g. `chat_center_message`/`skill_versions`/`skills`, which have NO
#     `skill_key` at all — so the first rule alone leaves them looking drifted)
# Both are rows here, so a table is matched by the rule that ACTUALLY governs it.
RULE_BASED_ADDITIONS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("skill_ref", "INTEGER", "skill_key",
     ("skill_registry", "component_registry", "skill_contract_template")),
)


def _read_additive_list(module_name: str, constant: str) -> list[tuple[str, str]]:
    """Read one additive list as `[(name, TYPE), ...]`. Never raises.

    `db_schema`'s lists hold `("col", "TEXT")` pairs. `question_flow`'s hold
    `("col", "TEXT NOT NULL DEFAULT 'NA'")`. Both are read here, and the TYPE is
    reduced to its leading word so it compares with `PRAGMA` the same way.
    """
    import importlib
    try:
        mod = importlib.import_module(module_name)
        val = getattr(mod, constant)
    except Exception:
        return []
    if not isinstance(val, (list, tuple)):
        return []
    out: list[tuple[str, str]] = []
    for entry in val:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            nm = str(entry[0])
            ty = str(entry[1]).split()[0].upper() if str(entry[1]).strip() else ""
            out.append((nm, ty))
        elif isinstance(entry, str):
            out.append((entry, ""))
    return out


def _iter_ddl_constants(ds) -> list[tuple[str, str, str]]:
    """Every `*_DDL` string constant, from EVERY module that declares one.

    Returns `[(module_name, const_name, text), ...]`, `db_schema` FIRST so the
    canonical declaration wins when two modules declare the same table.

    MEASURED (2026-09-26): 12 modules declare `*_DDL` constants. A reader that
    looks in ONE module reports a declaration as MISSING when it merely lives
    elsewhere.

    THE MODULES ARE READ AS TEXT, NOT IMPORTED. MEASURED: importing every module
    runs its side effects (migrations, seeds, heartbeat writes), which a READER
    must never do. The constants are module-level string literals, so a text scan
    finds them without executing anything.
    """
    import re
    from pathlib import Path

    out: list[tuple[str, str, str]] = []
    # `db_schema` FIRST: it is the canonical declaration site.
    for name in dir(ds):
        if name.endswith("_DDL"):
            val = getattr(ds, name)
            if isinstance(val, str):
                out.append(("db_schema", name, val))

    base = Path(__file__).resolve().parent
    for path in sorted(base.glob("*.py")):
        mod = path.stem
        if mod == "db_schema" or mod.startswith("_"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # A module-level `NAME_DDL = """..."""` (or `'''` / `"`). The scan is
        # deliberately narrow: a constant assigned inside a function is not a
        # module-level declaration.
        for m in re.finditer(
                r"^([A-Za-z_]\w*_DDL)\s*=\s*(\"\"\"|'''|\"|')", text, re.M):
            const = m.group(1)
            quote = m.group(2)
            start = m.end()
            end = text.find(quote, start)
            if end == -1:
                continue
            out.append((mod, const, text[start:end]))

    # ---- THE `src/` PACKAGES TOO (RING 6, 2026-09-29) --------------------
    #
    # WHY (MEASURED, and it is a "detector cannot find it" defect)
    # ----------------------------------------------------------------
    # `declared_shape('task_lifecycle_log')` returned
    # `{"ok": False, "code": "NO_DECLARATION"}` while the declaration EXISTS as
    # `TASK_LIFECYCLE_LOG_DDL` in `src/task_center/lifecycle_log.py` — because this
    # scan globbed ONLY the repo root. `spec_from_table` then REFUSED the table
    # ("no `*_DDL` constant in ANY module declares table ..."). A reader that
    # cannot see a real declaration reports a defect that is not there, which is
    # the same family as the FK regex that read comments, and the same family as
    # RING 5's `declared_tables()` that stripped the very strings it was counting.
    #
    # THE SCAN IS THE SAME SCAN, applied to one more directory: a text read, never
    # an import, so no module's side effects (migrations, seeds, heartbeats) run.
    # The `db_schema`-first ordering is preserved by appending AFTER the root scan,
    # so a root declaration still wins a tie.
    src = base / "src"
    if src.is_dir():
        for path in sorted(src.rglob("*.py")):
            mod = path.stem
            if mod == "db_schema" or mod.startswith("_"):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(
                    r"^([A-Za-z_]\w*_DDL)\s*=\s*(\"\"\"|'''|\"|')", text, re.M):
                const = m.group(1)
                quote = m.group(2)
                start = m.end()
                end = text.find(quote, start)
                if end == -1:
                    continue
                # The SOURCE is the path RELATIVE to the repo, so a reader can go
                # and look: `src/task_center/lifecycle_log.py` names the file,
                # while a bare `lifecycle_log` would not.
                out.append((str(path.relative_to(base)).replace("\\", "/"),
                            const, text[start:end]))
    return out


def declared_shape(table: str, conn: sqlite3.Connection | None = None
                   ) -> dict[str, Any]:
    """The table's shape AS DECLARED IN CODE — the independent expectation source.

    WHY THIS EXISTS (the user, 2026-09-24)
    --------------------------------------
        "`UNKNOWN`, because prompt answer can't be measured without unit"
        "is old design"

    THE USER IS RIGHT, AND MY `UNKNOWN` WAS THE OLD DESIGN.

    The OLD design asked a MODEL a question. A model's answer has no unit, so it
    could not be measured, so a third value (`UNKNOWN`) had to exist to mean "I
    was not told what to compare against". That third value is a confession that
    the measurement was never defined.

    THE NEW design asks an EVIDENCE READER a query. A query HAS a unit — it
    returns a value from a named source — so its answer is binary: the value
    matched the expectation, or it did not. There is no third answer, because
    "I have no expectation" is not an ANSWER, it is a DEFECT IN THE SPEC. A defect
    is REFUSED loudly (see `generate()`), never returned as a value. This is the
    rule `factor_first_principle` states for a factor ("a number without a unit
    cannot be audited") applied to a question.

    AND THE EXPECTATION MUST COME FROM SOMEWHERE ELSE.
    MEASURED, and my first version was WRONG: `spec_from_table` read the types
    back out of `PRAGMA table_info` and compared them to `PRAGMA table_info`. A
    check against the thing itself is a TAUTOLOGY — it answers YES for any table,
    including a broken one, so it certifies nothing. That is the same defect as
    `measurement_self_pollution` (an instrument reading itself).

    SO THE EXPECTATION IS THE CODE'S OWN DECLARATION: the `CREATE TABLE` text in
    `db_schema.*_DDL`, which is what `ensure_schema` EXECUTES. It is a DIFFERENT
    artefact from the live schema, and the two CAN DRIFT — a migration that added
    a column, a DDL edited without a re-migrate, a table created by an older
    version of the text. THAT drift is the real thing being measured, and it is
    the same comparison `code_health.verify_code_health_schema` already makes
    between a `REQUIRED_*_COLUMNS` list and `PRAGMA`.

    MEASURED coverage: 110 of 175 live tables are declared in code (65 are not).
    A table with NO declaration therefore has NO unit, and its spec is REFUSED
    rather than answered `UNKNOWN` — see `spec_from_table`.

    Returns `{ok, code, table, columns, source}`:
      `OK`               `columns` is `[(name, TYPE), ...]` in declaration order
      `NO_DECLARATION`   no `*_DDL` constant declares this table
      `UNPARSEABLE`      a declaration exists but no column could be read from it
    """
    import re

    import db_schema as ds
    body = ""
    source = ""
    # THE DECLARATION IS NOT ONLY IN `db_schema` (fixed 2026-09-26).
    #
    # MEASURED DEFECT: this read `db_schema` ONLY, so 64 REAL tables were REFUSED
    # with "no `*_DDL` constant in db_schema declares table X" — while 35 of them
    # ARE declared, in ANOTHER module:
    #
    #     skill_contract_template  <- skill_contract_store:SKILL_CONTRACT_TEMPLATE_DDL
    #     register_approve         <- register_approval:REGISTER_APPROVE_DDL
    #     capability_tag_registry  <- skill_factor:CAPABILITY_TAG_DDL
    #     ...
    #
    # (`db_row_registry <- entity_registry:DB_ROW_REGISTRY_DDL` was in this list
    # until 2026-09-27, when the table and its DDL were removed.)
    #
    # MEASURED: 12 modules declare `*_DDL` constants (db_schema 127,
    # skill_contract_store 6, entity_registry 4, skill_factor 4, ...). A reader
    # that looks in ONE module reports a declaration as MISSING when it merely
    # lives elsewhere — and a refusal that names the wrong reason is worse than
    # no refusal.
    #
    # THE MODULES ARE READ AS TEXT, NOT IMPORTED. MEASURED: importing every
    # module runs its side effects (migrations, seeds, heartbeat writes), which a
    # READER must never do. The DDL constants are module-level string literals, so
    # a text scan finds them without executing anything.
    for mod_name, const_name, val in _iter_ddl_constants(ds):
        m = re.search(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?%s[`\"]?\s*\("
            % re.escape(table), val, re.I)
        if not m:
            continue
        rest = val[m.end():]
        # The body ends at the FIRST line that closes the statement. A CHECK or
        # a DEFAULT containing `)` cannot reach column 0, so `^\);` is safe.
        end = re.search(r"^\s*\)\s*;", rest, re.M)
        body = rest[:end.start()] if end else rest
        source = "%s:%s" % (mod_name, const_name)
        break
    if not source:
        return {"ok": False, "code": "NO_DECLARATION", "table": table,
                "columns": [], "source": "",
                "message": ("no `*_DDL` constant in ANY module declares table "
                            "%r, so there is no DECLARED shape to measure the "
                            "live table against" % table)}
    cols: list[tuple[str, str]] = []
    # A CONSTRAINT LINE IS SKIPPED BY ITS FIRST WORD ONLY.
    #
    # MEASURED, and my first version was WRONG twice over:
    #   1. A `FOREIGN KEY` wrapped across lines puts `ON DELETE SET NULL` at the
    #      start of a line, and a naive two-token read made a column named `ON`
    #      of type `DELETE` — which made a CORRECT `consultant_team` look drifted.
    #   2. My fix then added `KEY` to the skip set, which DELETED the column
    #      `settings.key` (declared `key TEXT PRIMARY KEY`). A correct table lost
    #      a real column.
    # A constraint starts with a CLAUSE keyword; a COLUMN may be named `key`.
    # So the skip test is on the FIRST TOKEN being a clause keyword, and the
    # column-name position is otherwise read as a name. `key`/`status`/`source`
    # are legal column names and are kept.
    _CLAUSE_STARTS = {
        "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT", "ON",
        "REFERENCES", "DEFERRABLE", "NOT", "NULL", "DEFAULT", "COLLATE",
        "GENERATED", "MATCH", "INDEX", "USING", "WITHOUT", "STRICT", "AS",
    }
    for raw in body.split("\n"):
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        tok = re.match(r"[`\"]?([A-Za-z_]\w*)[`\"]?\s+([A-Za-z]+)", line)
        if not tok:
            continue
        nm, ty = tok.group(1), tok.group(2).upper()
        # `nm` is the first token. A clause keyword can NEVER be a column name, so
        # a line starting with one is a constraint, not a column.
        if nm.upper() in _CLAUSE_STARTS:
            continue
        cols.append((nm, ty))

    # THE DECLARATION HAS **TWO** SOURCES, AND `ensure_schema` RUNS BOTH.
    #
    # MEASURED (2026-09-24): 18 live tables have a column the `CREATE TABLE` text
    # does not list, and NONE of them is drift. Every one is declared in a
    # `<TABLE>_NEW_COLUMNS` list that `_add_columns_if_missing` applies with
    # `ALTER TABLE ADD COLUMN`. `dev_task`'s `current_model`/`error`/`qc_summary`
    # are `DEV_TASK_NEW_COLUMNS`; `function_scoring`'s `file_path`/`line_end` are
    # `FUNCTION_SCORING_NEW_COLUMNS`.
    #
    # A reader that knows only the `CREATE TABLE` text therefore reports a
    # CORRECT table as drifted — a FALSE FAILURE, the exact defect class the
    # hard-coded `INT` was. The declaration is the UNION of the two sources, and
    # each added column's type is in the list, so the type question stays
    # answerable.
    add_name = _additive_columns_name(table)
    added: list[tuple[str, str]] = []
    if add_name:
        for entry in getattr(ds, add_name):
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                added.append((str(entry[0]), str(entry[1]).upper()))
            elif isinstance(entry, str):
                added.append((entry, ""))
    # ... plus the additive declarations that live OUTSIDE `db_schema` (see
    # `ADDITIVE_DECLARATIONS`). MEASURED: without these, `workflow_step` reports
    # its five `question_flow`-added columns as undeclared on a CORRECT table.
    extra_sources: list[str] = []
    for module_name, constant in ADDITIVE_DECLARATIONS.get(table, ()):
        got = _read_additive_list(module_name, constant)
        if got:
            added.extend(got)
            extra_sources.append("%s:%s" % (module_name, constant))
    # ... and the RULE-BASED additions (see `RULE_BASED_ADDITIONS`), whose
    # declaration is a RULE rather than a per-table list. MEASURED:
    # `migrate_skill_ref.py` adds `skill_ref` to EVERY table carrying `skill_key`
    # except its SKIP set, so a reader that knows only the constants reports 13
    # CORRECT tables as drifted.
    #
    # THE CONDITION IS ASKED OF THE **LIVE** TABLE, because that is the question
    # fires whenever the migration WOULD apply: the DECLARED shape must list
    # `skill_ref` for every table the migration touches, whether or not the live
    # table already has it. My earlier guard ALSO tested the live column and so
    # never fired.
    live_names_rule = ([str(r[1]) for r in conn.execute(
        "PRAGMA table_info(%s)" % table)] if conn is not None else [])
    for col_name, col_type, requires, skip in RULE_BASED_ADDITIONS:
        if requires in live_names_rule and \
                table.lower() not in {s.lower() for s in skip}:
            added.append((col_name, col_type))
            extra_sources.append("rule:%s(requires %s)"
                                 % (col_name, requires))
    have = {n for n, _t in cols}
    for nm, ty in added:
        if nm not in have:
            cols.append((nm, ty))
            have.add(nm)
    if added:
        parts = [source]
        if add_name:
            parts.append("%s:%s" % (getattr(ds, "__name__", "db_schema"),
                                    add_name))
        parts.extend(extra_sources)
        source = " + ".join(parts)
    if not cols:
        return {"ok": False, "code": "UNPARSEABLE", "table": table,
                "columns": [], "source": source,
                "message": ("the declaration in %s yields no column; the "
                            "parser or the DDL is wrong, and either way "
                            "there is no unit to measure against" % source)}
    return {"ok": True, "code": "OK", "table": table, "columns": cols,
            "source": source, "message": ""}


def spec_from_table(conn, table: str) -> dict[str, Any]:
    """A SPEC derived from a TABLE, whose EXPECTATIONS come from the CODE DDL.
    "factor list = table list, can get it too, is good skil design -> logic
    generator" (user, 2026-09-22).

    A factor is a DIMENSION; a table is a DIMENSION. They have the SAME SHAPE, so
    the generator takes either. This is the table side of that identity: the
    table's columns ARE the field list, and the table's own design factors
    (`table_design.FACTORS`) ARE the factor list.

    THE TWO ENTRY POINTS MUST PRODUCE THE SAME SHAPE OF SPEC — the proof asserts
    it, because "the same shape" is the whole claim.

    WHAT IS READ FROM WHERE (this is the whole design, so it is stated here):

        field_names / field_types / field_count  <- the CODE DDL (the CONTRACT)
        the live columns                         <- PRAGMA (the SUBJECT)

    The question is "does the LIVE table match its DECLARATION", and both sides
    are read, so the answer can be NO. MEASURED, and my first version was WRONG:
    it read BOTH sides from `PRAGMA`, so every question was a tautology that
    answered YES for any table. See `declared_shape`.

    A TABLE WITH NO DECLARATION IS REFUSED. Its spec would have no unit, and the
    old design answered `UNKNOWN` for that — which the user named as the OLD
    design. A missing unit is a DEFECT, and a defect is loud, not a value.
    """
    import table_design as td
    cols = [dict(r) for r in conn.execute("PRAGMA table_info(%s)" % table)]
    if not cols:
        raise SpecError("table %r has no columns (does it exist?)" % table)
    decl = declared_shape(table)
    if not decl["ok"]:
        raise SpecError(
            "cannot build a spec for %r: %s. A spec with no declared shape has "
            "no UNIT to measure against, and this generator does not answer "
            "`UNKNOWN` for that — it refuses. Declare the table in a "
            "`db_schema.*_DDL` constant, or pass an explicit spec." %
            (table, decl["message"]))
    names = [n for n, _t in decl["columns"]]
    types = [t for _n, t in decl["columns"]]
    return {
        "subject": table,
        "kind": "table",
        "dimensions": {n: "" for n in fw.DIMENSION_NAMES},
        "factors": td.factors_as_registry_rows(),
        "field_count": len(decl["columns"]),
        "field_names": names,
        "field_types": types,
        # WHERE THE EXPECTATION CAME FROM. A reader can see that the expectation
        # is the CODE declaration and the subject is the LIVE table, so the
        # answer is a comparison rather than a self-check.
        "shape_source": decl["source"],
        "shape_declared_columns": len(decl["columns"]),
        "shape_live_columns": len(cols),
    }


def spec_from_route(conn, route_key: str) -> dict[str, Any]:
    """A SPEC derived from a ROUTE's WORKFLOW (user, 2026-09-24).

    WHY THIS EXISTS
    ---------------
        "chatting is for purpose -> we need to provide services, which services
         can match the user, so i think we need workflow to define"

    A route (`purpose_route_registry`) says WHICH service fulfils a purpose and
    WHICH workflow is the procedure it runs. So the questions for a chat are NOT
    hardcoded per skill — they come from the WORKFLOW the route selects. This is
    the same identity `spec_from_table` uses: a workflow's STEPS are the question
    list, exactly as a table's COLUMNS are the field list.

    REFUSES a route whose workflow has NO steps: a route that asks nothing cannot
    measure anything, and an empty question list would look like a clean run.
    """
    row = conn.execute(
        "SELECT * FROM purpose_route_registry WHERE route_key = ?",
        (str(route_key or "").strip(),)).fetchone()
    if not row:
        raise SpecError("no route %r in purpose_route_registry" % route_key)
    r = dict(row)
    wf_key = str(r["workflow_key"])
    wf = conn.execute(
        "SELECT workflow_id, workflow_key, name, description "
        "FROM workflow_registry WHERE workflow_key = ?", (wf_key,)).fetchone()
    if not wf:
        raise SpecError("route %r names workflow %r, which does not exist"
                        % (route_key, wf_key))
    steps = [dict(s) for s in conn.execute(
        "SELECT step_no, step_kind, layer_key, question_template, expected "
        "FROM workflow_step WHERE workflow_id = ? ORDER BY step_no",
        (int(wf["workflow_id"]),))]
    if not steps:
        raise SpecError(
            "route %r -> workflow %r has NO steps, so it asks nothing and can "
            "measure nothing" % (route_key, wf_key))
    # A STEP HAS NO SQLite TYPE, so its unit is its ASSERTION — but the unit must
    # still be DECLARED, because `validate_spec` REFUSES a spec whose type
    # questions have no unit (a spec with no unit is a defect, not an `UNKNOWN`).
    #
    # MEASURED 2026-09-24: this function shipped WITHOUT `field_names`/
    # `field_types`, so `validate_spec` refused every route spec:
    #     "a spec with 2 field(s) needs `field_types` ... without it the type
    #      questions have no UNIT"
    # So the route's own DECLARATION is used as the unit, and it is the honest
    # one: a step's declared kind IS what the question compares against.
    step_names = ["step_%s" % s["step_no"] for s in steps]
    step_types = [str(s.get("step_kind") or "NA").upper() for s in steps]
    # THE UNIT MUST BE REAL. A step with no declared `step_kind` (or the literal
    # 'NA') has no unit to measure against, and `validate_spec` would refuse it
    # anyway — but refusing HERE names WHICH step, which is actionable.
    undeclared = [s["step_no"] for s, t in zip(steps, step_types)
                  if not t or t == "NA"]
    if undeclared:
        raise SpecError(
            "route %r -> workflow %r has step(s) %s with NO declared "
            "`step_kind`, so their type questions have no UNIT; declare the "
            "kind or the route cannot be measured"
            % (route_key, wf_key, undeclared))
    return {
        "subject": str(wf["workflow_key"]),
        "kind": "route",
        "route_key": str(r["route_key"]),
        "purpose_key": str(r["purpose_key"]),
        "ticket_origin": str(r["ticket_origin"]),
        "workflow_key": wf_key,
        "dimensions": {n: "" for n in fw.DIMENSION_NAMES},
        # THE PROCEDURE IS THE FACTOR LIST. Each step is a measurable thing, so
        # the workflow DEFINES what "serving this purpose" means (the user's
        # "we need workflow to define").
        "steps": steps,
        "field_count": len(steps),
        # THE DECLARED UNIT: one entry per step, named `step_N`, typed by the
        # step's OWN declared `step_kind`. A route whose step kinds are not
        # declared is REFUSED rather than answered — see the check below.
        "field_names": step_names,
        "field_types": step_types,
        "shape_source": "purpose_route_registry:%s -> workflow_step.step_kind"
                        % wf_key,
    }


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Every reason the spec cannot produce a question list. Never raises."""
    errs: list[str] = []
    if not str(spec.get("subject") or "").strip():
        errs.append("spec needs a `subject` (what the questions are about)")
    dims = spec.get("dimensions")
    if not isinstance(dims, dict):
        errs.append("spec needs `dimensions` (a 5W1H answer set)")
    else:
        unknown = [k for k in dims if k not in fw.DIMENSION_NAMES]
        if unknown:
            errs.append("unknown dimension(s) %s; the six are %s"
                        % (unknown, list(fw.DIMENSION_NAMES)))
    n = spec.get("field_count")
    if n is not None:
        try:
            if int(n) < 0:
                errs.append("`field_count` cannot be negative")
        except (TypeError, ValueError):
            errs.append("`field_count` must be an integer, got %r" % (n,))
    # THE UNIT CHECK — a spec with fields but no DECLARED TYPES has no unit for
    # the type questions, and a question with no unit cannot be measured.
    #
    # WHY THIS IS A REFUSAL AND NOT AN `UNKNOWN` ANSWER (the user, 2026-09-24):
    # "`UNKNOWN`, because prompt answer can't be measured without unit — is old
    # design". The old design asked a MODEL, whose answer had no unit, so a third
    # value had to mean "I was not told what to compare against". The new design
    # asks a QUERY, whose answer is inherently binary — and "I have no
    # expectation" is not an answer, it is a DEFECT IN THE SPEC. A defect is
    # loud. `factor_first_principle` states the same rule for a factor: a number
    # without a unit cannot be audited.
    if n is not None and int(n) > 0:
        types = spec.get("field_types")
        if not isinstance(types, list) or len(types) != int(n):
            errs.append(
                "a spec with %s field(s) needs `field_types` (a list of %s "
                "DECLARED types) — without it the type questions have no UNIT, "
                "and a spec with no unit is refused rather than answered "
                "`UNKNOWN`" % (int(n), int(n)))
    return errs


# ---------------------------------------------------------------------------
# The question list
# ---------------------------------------------------------------------------

def _q(qid: str, text: str, dim: str, *, yes: str, no: str) -> dict[str, Any]:
    """One question, WITH its YES form and its NO form.

    Both forms are REQUIRED. A question with only its YES form cannot fail, so
    the run would certify a model that answers YES to everything.
    """
    return {
        "question_id": qid,
        "question": text,
        "dimension": dim,
        "yes_form": yes,
        "no_form": no,
    }


def generate(spec: dict[str, Any], *,
             code_questions: bool = False,
             conn: sqlite3.Connection | None = None,
             subject_kind: str | None = None) -> dict[str, Any]:
    """The QUESTION LIST for a spec, with the derivation of its count.

    Returns `{subject, questions, count, derivation, dimensions_used}`.

    REFUSES a spec that cannot produce a complete list, and REFUSES to emit a
    question without a NO form — a question that cannot fail is not a test.

    `code_questions=True` (added 2026-09-23) ADDS the three CODE-shape questions.
    It is OPT-IN so the default count stays `2N + 4` — the count the user's own
    example states — and a caller that wants the code axis must ask for it. The
    formula line reports which shape was produced, so a reader can never mistake
    one count for the other.

    `conn` + `subject_kind` (added 2026-09-24) make the WORDING come from the
    REGISTERS instead of this module's constants. OPT-IN, because the default
    must not change for a caller that never asked — and because a kind with no
    binding would otherwise silently lose its wording.

    WHEN ENABLED, EACH QUESTION REPORTS ITS OWN `wording_source`:
      `register`  the dimension's question PLUS the binding's criterion
      `dimension` the dimension's question ALONE (no binding for this kind)
    A caller can therefore tell a register-supplied wording from a fallback
    instead of both looking identical — the same rule `oracle_for` follows.
    """
    errs = validate_spec(spec)
    if errs:
        raise SpecError("spec is not usable: %s" % "; ".join(errs))

    subject = str(spec["subject"]).strip()
    # NOTE: `dims = spec.get("dimensions")` was assigned here and never read —
    # the dimensions are already validated in `validate_spec` (above) and read
    # from the spec by `_wording`. Measured by factor
    # `import_is_used_and_sorted` (F841). Removed, not renamed.
    n = spec.get("field_count")
    n = int(n) if n is not None else None

    # THE WORDING MAP, when a register is available. `None` means "do not use
    # the register", which is DIFFERENT from "the register had nothing": an
    # empty dict from a real lookup still counts as an attempt, and the source
    # line below reports which happened.
    wording: dict[str, str] | None = None
    bound_dims: set[str] = set()
    # THE WORDING AND ITS SOURCE, read from ONE place (C3b, 2026-09-27).
    # `dimension_wording_with_source` reads the GATE first and the register as
    # fallback, so this function no longer needs its own gate read — the logic
    # moved DOWN a layer so that EVERY caller of `dimension_wording` (including
    # `runtime_trace.report`) is gate-decided, not just this one.
    wording_src: dict[str, tuple[str, str]] = {}
    if conn is not None:
        kind = str(subject_kind or spec.get("subject_kind")
                   or spec.get("kind") or "").strip()
        wording_src = dimension_wording_with_source(conn, kind)
        wording = {k: v[0] for k, v in wording_src.items()}
        try:
            import dimension_binding_registry as _dbr
            bound_dims = set(_dbr.bindings_for(conn, kind))
        except Exception:
            bound_dims = set()

    def _wording(dim: str, fallback: str) -> tuple[str, str]:
        """The text for `dim`, and WHERE it came from.

        THE SOURCE COMES FROM `dimension_wording_with_source` (C3b), which reads
        the GATE first. `gate` means the activation gate DECIDED this wording;
        `register` means the gate REFUSED and the register supplied it.

        `register` only when the register ACTUALLY supplied something for this
        dimension. Reporting `register` for a fallback would be the
        "reported an unlock that did not happen" defect.
        """
        if dim in wording_src:
            text, src = wording_src[dim]
            if src == SOURCE_GATE:
                return text, SOURCE_GATE
            if src == SOURCE_registry and dim in bound_dims:
                return text, SOURCE_registry
        return fallback, (SOURCE_DIMENSION if wording is not None else "")

    questions: list[dict[str, Any]] = []
    derivation: list[str] = []

    # --- WHICH FAMILIES APPLY TO THIS KIND (added 2026-09-28) ---------------
    #
    # MEASURED DEFECT, found by RUNNING `answer_all(spec_of_a_FILE)`:
    # a spec whose subject is a `.py` FILE still got the six TABLE families
    # (`db_driven`, `field_count`, N names, N types), and every one of them
    # failed with
    #     OperationalError: near ".": syntax error
    # because the family ran `PRAGMA table_info('code_shape.py')` — a TABLE
    # question asked about a FILE. The deep problem is not the crash: it is that
    # the spec was allowed to emit questions its own subject cannot answer, which
    # is the WRONG-SUBJECT defect (`measurement-scope`) one layer up. A question
    # asked about a subject that cannot answer it manufactures a failure.
    #
    # The fix is at the SOURCE, so an ill-formed spec never becomes a question
    # list: a `kind` that is not a TABLE kind gets the CLOSING + CODE families
    # only. `db_driven` / `field_count` / `field_N_*` are the TABLE families.
    _kind = str(subject_kind or spec.get("subject_kind") or spec.get("kind")
                or "").strip().lower()
    table_subject = (_kind in ("", "table", "db_table")) or bool(n)
    if not table_subject:
        # no table families; the closing + code questions are emitted below
        derivation.append("0 structural questions (subject kind %r is not a "
                          "table; the table families cannot apply)" % _kind)
        n = 0

    # --- the two structural questions ---------------------------------------
    if table_subject:
        for qid, text, dim in STRUCTURE_QUESTIONS:
            if "{n}" in text:
                if n is None:
                    # A cardinality question with no declared count would ask
                    # "is that None?" — a question about nothing.
                    raise SpecError(
                        "the `field_count` question needs `field_count` in the "
                        "spec; without it the question asks about nothing")
                text = text.format(n=n)
            wtext, wsrc = _wording(dim, text)
            q = _q(qid, wtext, dim,
                   yes="the structure matches the specification",
                   no="the structure does NOT match the specification")
            q["wording_source"] = wsrc
            questions.append(q)
        derivation.append("2 structural questions (db_driven, field_count)")

    # --- per-field questions ------------------------------------------------
    # `field_*` questions carry their dimension (`DIM_NAMING` / `DIM_TYPING`),
    # so the register can word them too. MEASURED, and my first version left
    # them at an EMPTY source: `wording_by_source` reported `{'register': 4,
    # '': 6}`, and six questions silently bypassed the register while the summary
    # looked healthy. An empty source is a question whose wording nobody checked.
    if n:
        for i in range(1, n + 1):
            fname = (spec.get("field_names") or [None] * n)[i - 1] \
                if spec.get("field_names") else "x"
            wtext, wsrc = _wording(
                DIM_NAMING, "Is field %d's name `%s`?" % (i, fname))
            q = _q("field_%d_name" % i, wtext, DIM_NAMING,
                   yes="the name matches", no="the name does NOT match")
            q["wording_source"] = wsrc
            questions.append(q)
        derivation.append("%d naming questions (one per field)" % n)
        # THE TYPE QUESTION ASKS ABOUT THE DECLARATION, NOT ABOUT `INT`.
        # MEASURED (2026-09-24): the fallback text was "Is field N's value an
        # INT?" — the same hard-coded demand the READER made, so the question
        # and the reader agreed on a constant and the pair could never disagree.
        # The declared type comes from the spec (`field_types`); when the spec
        # does not declare one, the reader answers UNKNOWN and the question is
        # NOT judged, rather than the line manufacturing a failure.
        declared = (spec.get("field_types") or [None] * n)
        for i in range(1, n + 1):
            want = declared[i - 1] if i - 1 < len(declared) else None
            fallback = ("Is field %d's declared type `%s`?" % (i, want)
                        if want else
                        "Is field %d's declared type the one the spec states?" % i)
            wtext, wsrc = _wording(DIM_TYPING, fallback)
            q = _q("field_%d_type" % i, wtext, DIM_TYPING,
                   yes="the declared type matches the specification",
                   no="the declared type does NOT match the specification")
            q["wording_source"] = wsrc
            questions.append(q)
        derivation.append("%d typing questions (one per field)" % n)

    # --- the two closing questions ------------------------------------------
    for qid, text, dim in CLOSING_QUESTIONS:
        wtext, wsrc = _wording(dim, text)
        q = _q(qid, wtext, dim, yes="it does", no="it does NOT")
        q["wording_source"] = wsrc
        questions.append(q)
    derivation.append("2 closing questions (registered, registered_inactive)")

    # --- the OPT-IN code-shape questions ------------------------------------
    if code_questions:
        for qid, text, dim in CODE_QUESTIONS:
            wtext, wsrc = _wording(dim, text)
            q = _q(qid, wtext, dim,
                   yes="it is, and the evidence cites where", no="it is not")
            q["wording_source"] = wsrc
            questions.append(q)
        derivation.append("3 code-shape questions (name_registered, "
                          "literal_derivable, param_shadowed)")

    # --- the NO-form gate ---------------------------------------------------
    # A question with no NO form cannot fail, so it is REFUSED rather than
    # emitted. This is the same defect family as the empty-`expected` trap in
    # `skill_registrar`: a case that cannot fail certifies nothing.
    noformless = [q["question_id"] for q in questions
                  if not str(q.get("no_form") or "").strip()]
    if noformless:
        raise SpecError(
            "question(s) %s have no NO form — a question that cannot fail is "
            "not a test" % noformless)

    # --- the ANSWERABILITY gate (added 2026-09-26) --------------------------
    #
    # THE HUMAN: "how to help 7B zoom focus to the point / is question design and
    # flow problem / and is good experience to help improve question flow X logic
    # generator > prompt generator / this is the key for the system".
    #
    # MEASURED: the 7B's RAW reply was RIGHT, but an ABSENT-shaped instruction
    # failed EXACTLY the single-missing case (3 of 4 wrong). `required - present`
    # is a SET DIFFERENCE; a 7B does not compute it reliably, and `NONE` is the
    # SAFE answer, so on a near-miss it collapses to `NONE`. The question's own
    # escape hatch is what the model reaches for.
    #
    # WHY HERE, BESIDE THE NO-FORM GATE: both ask "is this question well-formed",
    # and two copies of that question is the drift this repo keeps paying for.
    # The shape rule itself lives in `question_flow.question_shape` — this gate
    # DELEGATES to it rather than re-implementing it.
    #
    # MEASURED (2026-09-26): this refuses NOTHING today — a real spec
    # (`spec_from_table("chat_main")`) generates 24 questions and 0 are refused.
    # It is a FUTURE GUARD: the generator cannot START to emit an ABSENT-shaped
    # question without being refused at the source, instead of the defect being
    # discovered later by a 7B scoring 0.70 recall.
    try:
        import question_flow as _qf
    except Exception as exc:
        raise SpecError(
            "the answerability gate is unreachable (%s: %s) — a gate that cannot "
            "be reached is REPORTED, never silently skipped"
            % (type(exc).__name__, exc))
    unanswerable: list[tuple[str, str]] = []
    for q in questions:
        s = _qf.question_shape(str(q.get("question") or ""))
        if not s.get("ok"):
            unanswerable.append((str(q.get("question_id")), str(s.get("why"))))
    if unanswerable:
        raise SpecError(
            "question(s) %s are UNANSWERABLE by the target model: %s. FIX: %s"
            % ([qid for qid, _w in unanswerable], unanswerable[0][1],
               _qf.question_shape("").get("fix") or
               "ask for what IS there, or ask N one-step questions"))

    dims_used = sorted({q["dimension"] for q in questions})
    # THE WORDING SOURCE IS COUNTED, so a caller cannot mistake a register-backed
    # list for a constant-backed one. Only present when a register was consulted.
    by_wording: dict[str, int] = {}
    if wording is not None:
        for q in questions:
            src = str(q.get("wording_source") or "")
            by_wording[src] = by_wording.get(src, 0) + 1
    # THE MEASURED UNITS OF THE WORDING (added 2026-09-25). A generated question
    # now carries the units its prose is made of, so the prose can be PROVEN by
    # them instead of asserted. Read only when a register was consulted, and the
    # result REPORTS the units rather than asserting them.
    wunits: dict[str, Any] = {}
    if conn is not None:
        sid = spec.get("skill_id")
        if sid is None:
            try:
                row = conn.execute(
                    "SELECT skill_id FROM component_registry WHERE skill_key = ?",
                    (subject,)).fetchone()
                sid = int(row["skill_id"]) if row else None
            except Exception:
                sid = None
        if sid is not None:
            wunits = wording_units(conn, int(sid))
    return {
        "subject": subject,
        "questions": questions,
        "count": len(questions),
        "case_count": len(questions) * 2,   # the YES/NO pair
        "derivation": derivation,
        "dimensions_used": dims_used,
        # 🔴 THE COUNT IS DERIVED, NOT HAND-WRITTEN (fixed 2026-09-29).
        #
        # MEASURED DEFECT: this was the literal string `"2 + N + N + 2 + 3 =
        # 2N + 7"`. When `reader_wired` was ADDED to `CODE_QUESTIONS` the count
        # became 4 while the formula still said `+ 3` — the formula and the
        # question list DISAGREED, and the disagreement was invisible because a
        # hand-written count cannot go stale loudly. Same family as
        # `stale_check_pins_a_number`: a proof that PINS a floor is fine, but a
        # DERIVED value must be derived. `len(CODE_QUESTIONS)` is the single
        # source, so adding a code question updates the formula by construction.
        "formula": ("2 + N + N + 2 + %d = 2N + %d" % (len(CODE_QUESTIONS),
                                                     4 + len(CODE_QUESTIONS))
                    if (n and code_questions)
                    else "2 + 2 + %d (no field_count, code questions)"
                         % len(CODE_QUESTIONS)
                    if code_questions
                    else "2 + N + N + 2 = 2N + 4" if n
                    else "2 + 2 (no field_count)"),
        "code_questions": bool(code_questions),
        "wording_by_source": by_wording,
        "wording_units": wunits,
        "wording_proof_type": wunits.get("proof_type", ""),
        "wording_subject_kind": (str(subject_kind or spec.get("subject_kind")
                                     or spec.get("kind") or "").strip()
                                 if wording is not None else ""),
    }


# ---------------------------------------------------------------------------
# THE EVIDENCE ANSWERS — the half that was MISSING.
#
# WHY THIS EXISTS (the user, 2026-09-23):
#
#     "logic generator can help to have the answer by evidence"
#
# MEASURED before this (`_diag_evidence_answer.py`): `generate()` emits 6
# question families, and EVERY one is answerable from the DB — but NO CODE
# connected a `question_id` to a query. So `llm_100_run_harness.oracle_for()`
# fell back to `default_phone`, a HARDCODED oracle that answers the PHONE's
# question. A run judged by it measures the phone, not the table.
#
# THE RULE, and it is the whole design: **an answer must carry the QUERY that
# produced it.** An answer without a query is an assertion, and an assertion is
# exactly what `default_phone` already is. So every function here returns
# `{answer, query, value}` — the query is the evidence, and it is checkable.
#
# THE LINK IS DATA, NOT BRANCHES. `EVIDENCE_ANSWERS` maps a question FAMILY to a
# function, so adding a question family is a row in this mapping rather than a
# new `if question_id == ...` branch. The per-field families (`field_N_name`,
# `field_N_type`) are matched by SUFFIX, because N is derived from the spec.
#
# REFUSE, NEVER GUESS. `answer_by_evidence()` returns `ok=False` when no
# function answers a question. It does NOT fall back to a default — a silent
# fallback is the defect this block removes.
# ---------------------------------------------------------------------------

def _pragma_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    """The table's columns, from `PRAGMA table_info`. The ONE read of the shape."""
    return [dict(r) for r in conn.execute("PRAGMA table_info(%s)" % table)]


def _ev_db_driven(conn: sqlite3.Connection, spec: dict[str, Any],
                  question: dict[str, Any]) -> dict[str, Any]:
    """Is `id` a PRIMARY KEY with AUTOINCREMENT?

    The question names `id` explicitly, so the check is on THAT column. A table
    whose PK is named something else is NOT db-driven by this definition — and
    saying so is the point: the question has a NO form, so it must be able to
    answer NO.
    """
    table = str(spec["subject"])
    cols = _pragma_columns(conn, table)
    pk = [c for c in cols if int(c.get("pk") or 0) == 1]
    named_id = [c for c in cols if str(c["name"]).lower() == "id"]
    # AUTOINCREMENT is not in `PRAGMA table_info`; it is in the CREATE statement.
    ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND "
                       "name=?", (table,)).fetchone()
    autoinc = bool(ddl and "AUTOINCREMENT" in str(ddl[0] or "").upper())
    ok = bool(named_id) and bool(pk) and str(pk[0]["name"]).lower() == "id" \
        and autoinc
    return {
        "answer": "YES" if ok else "NO",
        "query": ("PRAGMA table_info(%s) + sqlite_master.sql" % table),
        "value": {"pk": [c["name"] for c in pk], "has_id": bool(named_id),
                  "autoincrement": autoinc},
    }


def _ev_fk_parent_key_legal(conn: sqlite3.Connection, spec: dict[str, Any],
                            question: dict[str, Any]) -> dict[str, Any]:
    """Is every FK on this table attached to a LEGAL parent key?

    THE POLICY (human-locked 2026-09-29): a native FK may only be attached to the
    parent's PRIMARY KEY, and only for a MANDATORY + LOAD-BEARING reference.

    WHY THIS QUESTION EXISTS, MEASURED: `question_template_registry` and
    `github_find_registry` declared
    `FOREIGN KEY (factor_key) REFERENCES skill_factor_registry(factor_key)`, and
    the parent's only key on `factor_key` is `uq_factor_scope_key`, an EXPRESSION
    index — which SQLite refuses as an FK parent key. With `foreign_keys=ON` (91
    places set it) ANY insert raised `foreign key mismatch`, **even with a VALID
    key**, so the constraint enforced nothing and only broke writers.

    THE POSITIONS COME FROM `PRAGMA`, NEVER FROM DDL TEXT. MEASURED: a regex over
    the DDL read a COMMENTS-borne `REFERENCES` and invented 7 extra "broken" FKs
    in the first measurement of this very defect (`text_scan_reads_prose_as_code`
    family). `PRAGMA index_info` returns NULL for an expression column, and that
    NULL is the whole signal.
    """
    table = str(spec["subject"])
    fks = list(conn.execute("PRAGMA foreign_key_list(%s)" % table))
    illegal = []
    for f in fks:
        parent, child_col, parent_col = f[2], f[3], f[4]
        legal = False
        pk = [c[1] for c in conn.execute("PRAGMA table_info(%s)" % parent)
              if int(c[5] or 0) == 1]
        if tuple(pk) == (parent_col,):
            legal = True
        else:
            for ix in conn.execute("PRAGMA index_list(%s)" % parent):
                if not int(ix[2] or 0):
                    continue
                cols = [r[2] for r in conn.execute("PRAGMA index_info(%s)" % ix[1])]
                if any(c is None for c in cols):
                    continue  # an EXPRESSION index: never a legal parent key
                if tuple(cols) == (parent_col,):
                    legal = True
        if not legal:
            illegal.append("%s -> %s(%s)" % (child_col, parent, parent_col))
    ok = not illegal
    return {
        "answer": "YES" if ok else "NO",
        "query": "PRAGMA foreign_key_list(%s) + index_list/index_info" % table,
        "value": {"fks": len(fks), "illegal": illegal},
    }


def _ev_field_count(conn: sqlite3.Connection, spec: dict[str, Any],
                    question: dict[str, Any]) -> dict[str, Any]:
    """Is the column count equal to the spec's `field_count`?"""
    table = str(spec["subject"])
    cols = _pragma_columns(conn, table)
    want = spec.get("field_count")
    got = len(cols)
    ok = want is not None and int(want) == got
    return {
        "answer": "YES" if ok else "NO",
        "query": "PRAGMA table_info(%s) -> COUNT(*)" % table,
        "value": {"count": got, "expected": want},
    }


def _ev_field_name(conn: sqlite3.Connection, spec: dict[str, Any],
                   question: dict[str, Any]) -> dict[str, Any]:
    """Does the LIVE table contain the column the DECLARATION names at N?

    N is parsed from the `question_id` (`field_3_name` -> 3) and indexes the
    DECLARED column list. The answer is whether the live table HAS that column.

    COMPARED BY NAME, NOT BY POSITION — and that is a MEASURED decision, not a
    convenience. `spec_from_table` derives the expectation from the CODE DDL, and
    the live column ORDER can differ from the declared order even when the table
    is perfectly correct. MEASURED on `code_registry`: the DDL lists
    `... status, file_path, line_start, line_end, code_span, notes, source ...`
    while the live table is
    `... status, notes, source, updated_at, created_at, file_path, line_start ...`.
    The cause is `db_schema.code_registry_NEW_COLUMNS` — the CH7 columns are
    added by `ALTER TABLE ADD COLUMN`, which APPENDS, so they land at the END
    while the DDL text keeps them in the middle.

    A positional comparison therefore reported **8 FAILs on a correct table** —
    the same defect class as the hard-coded `INT` it replaced: a failure that
    measures a MIGRATION ARTEFACT rather than the design. In SQLite, column
    position is not a design property; the column SET and its TYPES are.
    """
    table = str(spec["subject"])
    n = _field_index(question["question_id"])
    cols = _pragma_columns(conn, table)
    live_names = [str(c["name"]) for c in cols]
    declared = spec.get("field_names") or []
    want = declared[n - 1] if n and n <= len(declared) else None
    present = want is not None and want in live_names
    return {
        "answer": "YES" if present else "NO",
        "query": "PRAGMA table_info(%s) -> names" % table,
        "value": {"index": n, "expected": want, "present": present,
                  "live_count": len(live_names)},
    }


def _ev_field_type(conn: sqlite3.Connection, spec: dict[str, Any],
                   question: dict[str, Any]) -> dict[str, Any]:
    """Is column N's LIVE declared type the type the SPEC (the DDL) declares?

    THE EXPECTED TYPE COMES FROM THE SPEC, WHICH COMES FROM THE CODE DDL — NOT
    from this function, and NOT from the live table.

    MEASURED (2026-09-24), and BOTH my earlier versions were wrong:
      1. The first hard-coded `"INT" in got.upper()`, so EVERY column was asked
         "are you an INT?". 13 of `code_registry`'s 19 columns are TEXT, so the
         line reported 13 failures that measured nothing. The question was a
         CONSTANT wearing a question's clothes.
      2. The second returned `UNKNOWN` when the spec declared no type. The USER
         named that as the OLD DESIGN: "prompt answer can't be measured without
         unit". `UNKNOWN` is a confession that no unit was defined, and it has no
         place in an evidence reader whose answer is a query. A missing unit is a
         SPEC DEFECT, and `generate()` REFUSES such a spec up front — so this
         function never sees one. If it ever does, that is a programming error
         and it RAISES rather than inventing a third answer.

    The answer is BINARY: the live declaration matched the declared one, or it
    did not. That is the only shape a query can have.
    """
    table = str(spec["subject"])
    n = _field_index(question["question_id"])
    cols = _pragma_columns(conn, table)
    names = spec.get("field_names") or []
    types = spec.get("field_types") or []
    if not (n and n <= len(types) and str(types[n - 1]).strip()):
        # UNREACHABLE for a spec built by `spec_from_table` (which refuses a
        # table with no declaration). Kept as a LOUD failure rather than an
        # `UNKNOWN` answer, because a reader that invents a third answer is the
        # old design the user rejected.
        raise ValueError(
            "field_%s_type has no declared expectation in the spec; a spec with "
            "no unit must be refused at generate() time, not answered here"
            % (n or "?"))
    want_name = str(names[n - 1]) if n <= len(names) else ""
    want_type = str(types[n - 1]).strip().upper()
    # LOOKED UP BY NAME (see `_ev_field_name` for the measured reason): the
    # declared column N is found in the live table by NAME, and the comparison is
    # against the live DECLARED type of THAT column.
    live = {str(c["name"]): str(c["type"]) for c in cols}
    got = live.get(want_name, "")
    # A SUBSTRING MATCH, because SQLite reports `VARCHAR(20)` / `INTEGER` /
    # `TEXT NOT NULL` and the DDL names the KIND. Both directions are checked so
    # `TEXT` does not match `TEXTUAL` by accident.
    ok = bool(got) and (want_type in got.upper() or got.upper() in want_type)
    return {
        "answer": "YES" if ok else "NO",
        "query": "PRAGMA table_info(%s) -> column %r type" % (table, want_name),
        "value": {"index": n, "name": want_name, "type": got,
                  "expected": want_type},
    }


def _ev_registered(conn: sqlite3.Connection, spec: dict[str, Any],
                   question: dict[str, Any]) -> dict[str, Any]:
    """Does the table register at `db_table_registry`?"""
    table = str(spec["subject"])
    row = conn.execute("SELECT db_table_id, is_active FROM db_table_registry "
                       "WHERE table_key=?", (table,)).fetchone()
    return {
        "answer": "YES" if row else "NO",
        "query": ("SELECT db_table_id, is_active FROM db_table_registry "
                  "WHERE table_key='%s'" % table),
        "value": ({"db_table_id": int(row["db_table_id"]),
                   "is_active": int(row["is_active"])} if row else None),
    }


def _ev_registered_inactive(conn: sqlite3.Connection, spec: dict[str, Any],
                            question: dict[str, Any]) -> dict[str, Any]:
    """Does it register AND have `is_active = 0`?

    A table that is NOT registered cannot satisfy this, so the answer is NO —
    the two conditions are ANDed, and the evidence shows both.
    """
    table = str(spec["subject"])
    row = conn.execute("SELECT db_table_id, is_active FROM db_table_registry "
                       "WHERE table_key=?", (table,)).fetchone()
    registered = bool(row)
    inactive = bool(row) and int(row["is_active"]) == 0
    return {
        "answer": "YES" if (registered and inactive) else "NO",
        "query": ("SELECT is_active FROM db_table_registry WHERE "
                  "table_key='%s'" % table),
        "value": {"registered": registered,
                  "is_active": (int(row["is_active"]) if row else None)},
    }


def _field_index(question_id: str) -> int | None:
    """The N in `field_<N>_name` / `field_<N>_type`. None when there is no N."""
    parts = str(question_id or "").split("_")
    if len(parts) >= 3 and parts[0] == "field":
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# THE EVIDENCE SOURCE CLASS (added 2026-09-23).
#
# WHY: every reader above reads the DATABASE. The user's two new questions are
# about CODE:
#
#     "worker define $X which are not register at function / terminology
#      register / is it the hardcode define method"
#     "output value in function can be hardcode easy by re-define $A = 0"
#
# Neither is answerable from SQL — one is a register lookup, the other is a
# property of the source's SHAPE — so the source is now DECLARED rather than
# assumed. It is not decoration: the QUERY a reader returns has a different FORM
# per source, and a caller must be able to tell which it received.
#
#   db   -> the query is a SQL / PRAGMA statement that can be re-run
#   code -> the query is a `path:line` citation or a command that ran
#
# The rule does NOT relax: an answer without a query is still REFUSED (see
# `answer_by_evidence`). A `code` answer must carry a `path:line`, which is the
# citation form `citation-discipline` accepts. Declaring the source is what makes
# that requirement CHECKABLE instead of merely asserted.
SOURCE_DB = "db"
SOURCE_CODE = "code"
EVIDENCE_SOURCES: tuple[str, ...] = (SOURCE_DB, SOURCE_CODE)


def _ev_name_registered(conn: sqlite3.Connection, spec: dict[str, Any],
                        question: dict[str, Any]) -> dict[str, Any]:
    """Is the name this spec uses a REGISTERED term?

    DELEGATES to `terminology_registry.assert_named` — the ONE implementation of
    "is this name registered". Re-implementing it here would be a second truth
    about the same fact, the drift this repo keeps recording.

    WHY IT MATTERS (the user): "worker define $X which are not register at
    function / terminology register / is it the hardcode define method". A name
    that was never registered is an invented word, and `assert_named` refuses it
    with a reason that NAMES the term — so the fix is "register this word", not
    "try again".

    MEASURED (F10): `assert_named` EXISTS but is called from NO write path. This
    family is one of the call sites that was missing.
    """
    import terminology_registry as tr
    name = str(spec.get("term") or spec.get("subject") or "").strip()
    # ---- ONE NAME CONVENTION, OR TWO READERS DISAGREE (2026-09-28) ---------
    #
    # MEASURED DEFECT, found by RUNNING this family on a real FILE:
    #     terminology_registry.assert_named(conn, 'code_shape.py')  -> False
    # while `qc_gate_runner._check_ontology` asks the SAME question with
    #     terminology_registry.assert_named(conn, 'code_shape')     -> True
    # (it uses `Path(ref).stem`). So the SAME FACT — "is this module's name
    # registered" — was answered YES by the gate and NO here, purely because one
    # reader kept the `.py` suffix. Two readers of one fact must not disagree:
    # one of them is then a false claim, and a worker cannot tell which.
    #
    # The register stores MODULE names (`qc_gate_runner`, `code_shape`,
    # `pattern_template`) with no extension, so the STEM is the registered form
    # and this reader now matches `qc_gate_runner` rather than inventing a second
    # convention. A non-file subject (`chat_main`) is unchanged.
    if name.lower().endswith(".py") or "/" in name or "\\" in name:
        name = Path(name).stem
    ok, reason = tr.assert_named(conn, name)
    return {
        "answer": "YES" if ok else "NO",
        "query": "terminology_registry.assert_named(conn, %r)" % name,
        "value": {"term": name, "ok": bool(ok), "reason": reason,
                  "name_convention": "module stem (same as qc_gate_runner)"},
    }


def _ev_literal_derivable(conn: sqlite3.Connection, spec: dict[str, Any],
                          question: dict[str, Any]) -> dict[str, Any]:
    """Does the file hard-code a literal that a registry could have supplied?

    DELEGATES to `hardcode_scan.scan_file` — the ONE implementation of "is this a
    hard-coded value". `src/task_center/skill_task_validate.py:519-545` states
    the reason: re-implementing the rules would let the GATE and the REPORT
    disagree about the same file.

    THE PRECISE / AGGREGATE DISTINCTION IS RESPECTED. `hardcode_scan` says of
    itself: "this produces a REVIEW list, not a verdict" (`hardcode_scan.py:24`),
    and only a PRECISE scope level (`db_field/db_table/function/api`) can decide
    whether a literal was DERIVABLE. An aggregate-only resolution names a FORUM,
    not a value source, so it is reported as UNKNOWN rather than FAIL — the same
    grading the QC gate applies, read from the SAME constant.

    The answer is YES when a PRECISE live candidate exists, NO when none does,
    and the query is the `path:line` of the first candidate (or the scan itself).
    """
    from pathlib import Path as _P

    import hardcode_scan as hsc
    path = str(spec.get("path") or spec.get("file") or "").strip()
    if not path:
        # NOT a silent NO: without a path there is nothing to scan, and saying
        # so is the difference between "clean" and "not asked".
        return {"answer": "NO", "query": "hardcode_scan.scan_file(<no path>)",
                "value": {"reason": "spec carries no `path`/`file` to scan"}}
    root = _P(BASE_DIR)
    conn_h = hsc._connect(None)
    try:
        cands = hsc.scan_file(_P(path), root, conn=conn_h, resolve=True)
    finally:
        conn_h.close()
    live = [c for c in cands if not (c.get("is_comment")
                                     or c.get("is_message")
                                     or c.get("excluded"))]
    precise = [c for c in live if c.get("scope_level") in hsc.PRECISE_LEVELS]
    aggregate_only = [c for c in live if c.get("scope_level")
                      not in hsc.PRECISE_LEVELS]
    first = (precise or live or [{}])[0]
    return {
        "answer": "YES" if precise else "NO",
        "query": (first.get("cite_ref") or
                  "hardcode_scan.scan_file(%r)" % path),
        "value": {"live": len(live), "precise": len(precise),
                  "aggregate_only": len(aggregate_only),
                  "rules": sorted({c.get("rule") for c in live}),
                  "note": ("aggregate-only candidates are NOT a FAIL: a scope "
                           "level that cannot hold a value cannot decide "
                           "necessity") if aggregate_only else ""},
    }


def _ev_param_shadowed(conn: sqlite3.Connection, spec: dict[str, Any],
                       question: dict[str, Any]) -> dict[str, Any]:
    """Is a parameter OVERWRITTEN in its own function body?

    DELEGATES to `code_shape.redefined_params` — the only new module in this
    change, because this question is genuinely new: `hardcode_scan` is
    LINE-based and `A = 0` matches none of its seven rules.

    WHY IT IS THE WORST KIND (the user): "output value in function can be
    hardcode easy by re-define $A = 0". A literal is a value that could have been
    looked up; a shadowed parameter makes the OUTPUT TRUE BY CONSTRUCTION, so no
    input can make it fail and no test can catch it. The negative control
    `factor_first_principle.py:94-99` demands is destroyed at the source.

    A file that could NOT be read yields NO with the reason carried in `value`
    AND a query that says so — it must not read as "clean". `code_shape` returns
    a DISTINCT code for that case precisely so this reader can pass it on.
    """
    import code_shape as csh
    path = str(spec.get("path") or spec.get("file") or "").strip()
    if not path:
        return {"answer": "NO", "query": "code_shape.redefined_params(<no path>)",
                "value": {"reason": "spec carries no `path`/`file` to read"}}
    r = csh.redefined_params(path)
    if not r.get("ok"):
        return {
            "answer": "NO",
            "query": "code_shape.redefined_params(%r)" % path,
            "value": {"code": r.get("code"), "reason": r.get("reason"),
                      "read_failed": True},
        }
    first = (r["findings"] or [{}])[0]
    return {
        "answer": "YES" if r["findings"] else "NO",
        "query": first.get("cite_ref") or "code_shape.redefined_params(%r)" % path,
        "value": {"findings": len(r["findings"]), "functions": r["functions"],
                  "shadowed": [{"function": f["function"], "param": f["param"],
                                "cite_ref": f["cite_ref"]}
                               for f in r["findings"][:8]]},
    }


# THE WIRING READER (added 2026-09-28).
#
# WHY IT ANSWERS BY GREPPING CALLERS, AND NOTHING ELSE
# ---------------------------------------------------
# The human's complaint was that a step can be PREPARED and NOT WIRED. The only
# non-opinionated test of "wired" is: does a command that runs WITHOUT the writer
# eventually NAME this reader? So the reader searches the AUTOMATIC callers — the
# gate modules and the PostToolUse hook — for this module's name, and it reports
# each caller it found with its cite. It never inspects the reader's own file for
# a claim about itself: a module that asserts "I am wired" is the
# `control_depends_on_live_data` failure in the other direction.
#
# THE AUTOMATIC CALLERS ARE NAMED, NOT GLOBBED. A glob would count a PROOF as a
# caller, and MEASURED that is exactly the state that produced the complaint: the
# proof ran, the tool looked wired, and the writer still never saw it.
WIRED_CALLERS: tuple[str, ...] = (
    "qc_gate_runner.py",            # the dimension dispatcher
    "scripts/qc_gate_hook.py",      # the PostToolUse hook (fires on a WRITE)
)

# 🔴 A SECOND, NAMED POPULATION: the RUNTIME entry points (added 2026-09-29).
#
# MEASURED DEFECT, found while wiring `laya_router`: `reader_wired` answered NO
# for a module that had just been given a real caller, because `WIRED_CALLERS` is
# the GATE population. That was CORRECT for the question `reader_wired` was built
# to answer — the human's complaint was that a GATE step can be prepared and not
# wired — but it is the WRONG POPULATION for "can a RUNNING system reach this?".
#
# The two questions are different and must not be merged
# (`measurement_scope`: a count of the wrong population is a false signal):
#
#   reader_wired          is a GATE wired?      population = the gate modules
#   reader_wired_runtime  is the RUNTIME wired? population = the servers and
#                                               engines a run actually executes
#
# The fix is a SECOND FAMILY, not a looser first one. Weakening `reader_wired` to
# also accept a runtime caller would erase the gate complaint it exists to
# capture.
#
# NAMED, NOT GLOBBED, for the same measured reason: a glob counts a PROOF as a
# caller, and a proof is exactly what let a dead module look wired.
RUNTIME_CALLERS: tuple[str, ...] = (
    "main_api.py",              # the ONE FastAPI app
    "terminology_api.py",       # the server that runs uvicorn on that app
    "llm_100_run_harness.py",   # the run harness (its judge path)
    "worker_engine.py",         # the worker that reaches the LLM
    "auto_improve.py",          # the bounded improvement loop
    "helper_watchdog.py",       # the long-running automation loop
)


def _code_signal_hits(module: str, callers: tuple[str, ...]) -> list[dict[str, str]]:
    """Every caller in `callers` that names `module` as CODE, with a `path:line` cite.

    THE SIGNAL IS CODE ONLY: an `import <module>`, a `from <module> import`, or a
    `<module>.` ATTRIBUTE ACCESS (a call site). A bare word in a sentence matches
    none of them — MEASURED reason: `qc_gate_runner.py` mentions "bandit" twice and
    BOTH are prose, so a substring test said WIRED for a tool with no reader.
    """
    pat = re.compile(r"(?:^|\n)\s*(?:import\s+%s\b|from\s+%s\s+import)|"
                     r"\b%s\." % (re.escape(module), re.escape(module),
                                  re.escape(module)))
    hits: list[dict[str, str]] = []
    for caller in callers:
        c = Path(__file__).resolve().parent / caller
        try:
            text = c.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        m = pat.search(text)
        if m:
            hits.append({"caller": caller,
                         "cite_ref": "%s:%d" % (caller,
                                                text[:m.start()].count("\n") + 1),
                         "signal": m.group(0).strip()})
    return hits


def _ev_reader_wired(conn: sqlite3.Connection, spec: dict[str, Any],
                     question: dict[str, Any]) -> dict[str, Any]:
    """Does an AUTOMATIC caller name the reader for this file?

    `YES` requires a caller in `WIRED_CALLERS` to NAME this file's module. A
    caller that exists but names nothing, or a caller that is itself only a proof,
    is `NO` — because the question is about activation, not about code existing.
    """
    path = str(spec.get("path") or spec.get("file") or "").strip()
    if not path:
        return {"answer": "NO", "query": "grep(<no path>)",
                "value": {"reason": "spec carries no `path`/`file` to look up"}}
    module = Path(path).stem
    hits: list[dict[str, str]] = []
    # A CODE SIGNAL, NOT A WORD IN PROSE.
    #
    # MEASURED DEFECT (2026-09-28), found by asking this reader about `bandit`:
    # the first version used `if module in text`, a bare substring test, and it
    # returned YES for a caller that merely MENTIONED the name — including inside
    # a COMMENT. MEASURED: `qc_gate_runner.py` mentions "bandit" twice and BOTH
    # are in prose (a docstring and a comment), so `"bandit" in text` said WIRED
    # while bandit has no reader at all. That is the repo's own
    # `substring_check_over_comments` / `text_scan_reads_prose_as_code` defect —
    # and it is the WORST place for it, because this reader's whole job is to
    # report wiring, so a prose mention would certify an UNWIRED tool as wired.
    #
    # The signal is therefore CODE ONLY: an `import <module>`, a
    # `from <module> import`, or a `<module>.` ATTRIBUTE ACCESS (a call site).
    # A name in a sentence matches none of them.
    code_signal = re.compile(r"(?:^|\n)\s*(?:import\s+%s\b|from\s+%s\s+import)|"
                             r"\b%s\." % (re.escape(module), re.escape(module),
                                          re.escape(module)))
    for caller in WIRED_CALLERS:
        c = Path(__file__).resolve().parent / caller
        try:
            text = c.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        m = code_signal.search(text)
        if m:
            line_no = text[:m.start()].count("\n") + 1
            hits.append({"caller": caller,
                         "cite_ref": "%s:%d" % (caller, line_no),
                         "signal": m.group(0).strip()})
    return {
        "answer": "YES" if hits else "NO",
        # THE QUERY MUST BE RE-RUNNABLE, and MEASURED it was refused without this:
        # `answer_by_evidence` rejects a `code` answer whose query has neither a
        # `:` nor a `(` — `CODE_ANSWER_WITHOUT_CITATION`. My first query was a bare
        # `grep -l ...`, which is a DESCRIPTION of a search rather than the call
        # that produced the answer. The command below is the ACTUAL call, so a
        # reader can re-run it and get this answer back.
        "query": ("python -c \"import logic_generator as lg; "
                  "print(lg._ev_reader_wired(None, {'path': '%s'}, {}))\"" % module),
        "value": {"module": module, "wired_callers": hits,
                  "searched": list(WIRED_CALLERS),
                  "match": "CODE signal only (import / from-import / attribute "
                           "access), never a bare word in prose",
                  "note": ("an automatic caller CALLS this module"
                           if hits else
                           "no automatic caller IMPORTS or CALLS this module: the "
                           "rule is PREPARED, not WIRED — a mention in a comment "
                           "does not count")},
    }


def _ev_reader_wired_runtime(conn: sqlite3.Connection, spec: dict[str, Any],
                             question: dict[str, Any]) -> dict[str, Any]:
    """Does a RUNNING system reach this module? — a DIFFERENT question from
    `reader_wired`.

    MEASURED 2026-09-29, and the measurement is why this family exists: after
    `laya_router` was given a real caller in `llm_100_run_harness._llm_judge`,
    `reader_wired` STILL answered NO. That answer was CORRECT — `reader_wired`'s
    population is the GATE modules, because the human's complaint was about a
    GATE step being prepared and not wired. But "can a run reach this?" is a
    different question about a different population, and answering it with the
    gate list is a count of the wrong set (`measurement_scope`).

    So this is a SECOND NAMED POPULATION (`RUNTIME_CALLERS`), never a looser
    first one: accepting a runtime caller in `reader_wired` would erase the gate
    complaint that family exists to capture.

    `YES` requires a caller in `RUNTIME_CALLERS` to name this module as CODE. A
    PROOF is not a runtime caller, and a mention in prose is not a call.
    """
    path = str(spec.get("path") or spec.get("file") or "").strip()
    if not path:
        return {"answer": "NO", "query": "grep(<no path>)",
                "value": {"reason": "spec carries no `path`/`file` to look up"}}
    module = Path(path).stem
    hits = _code_signal_hits(module, RUNTIME_CALLERS)
    return {
        "answer": "YES" if hits else "NO",
        # A RE-RUNNABLE query, because `answer_by_evidence` refuses a `code`
        # answer whose query has neither `:` nor `(`.
        "query": ("python -c \"import logic_generator as lg; "
                  "print(lg._ev_reader_wired_runtime(None, {'path': '%s'}, "
                  "{}))\"" % module),
        "value": {"module": module, "runtime_callers": hits,
                  "searched": list(RUNTIME_CALLERS),
                  "match": "CODE signal only (import / from-import / attribute "
                           "access), never a bare word in prose",
                  "note": ("a RUNNING system reaches this module"
                           if hits else
                           "no runtime entry point IMPORTS or CALLS this module: "
                           "it is PREPARED, not WIRED — being named by a PROOF "
                           "does not count, which is exactly how a dead module "
                           "once looked wired")},
    }


# THE MAPPING. A question FAMILY -> the function that answers it from evidence.
# Adding a family is a row here, not a branch in a chain of `if`s.
EVIDENCE_ANSWERS: dict[str, Any] = {
    "db_driven": _ev_db_driven,
    "field_count": _ev_field_count,
    "field_N_name": _ev_field_name,
    "field_N_type": _ev_field_type,
    "registered": _ev_registered,
    "registered_inactive": _ev_registered_inactive,
    # THE CODE-SOURCE FAMILIES (added 2026-09-23). Every reader above reads the
    # DB; these read a NAME and a FILE, so the source is declared per family.
    "name_registered": _ev_name_registered,
    "literal_derivable": _ev_literal_derivable,
    "param_shadowed": _ev_param_shadowed,
    "reader_wired": _ev_reader_wired,
    "reader_wired_runtime": _ev_reader_wired_runtime,
    # THE SCHEMA-INTEGRITY FAMILY (added 2026-09-29), and it is a DB reader: the
    # evidence is the parent key's SHAPE, which only `PRAGMA` can report.
    "fk_parent_key_legal": _ev_fk_parent_key_legal,
}

# WHICH SOURCE answers each family. A SECOND mapping rather than a column on the
# functions, because a function's `__doc__` is not a data structure and a
# convention encoded in a docstring is the "rule stated in prose" defect.
#
# The default is `db`: a family added without a source row is a DB reader, which
# is what every pre-existing family is. An UNKNOWN family still gets no answer
# at all (`NO_EVIDENCE_ANSWER`), so this default cannot invent one.
EVIDENCE_FAMILY_SOURCE: dict[str, str] = {
    "db_driven": SOURCE_DB,
    "field_count": SOURCE_DB,
    "field_N_name": SOURCE_DB,
    "field_N_type": SOURCE_DB,
    "registered": SOURCE_DB,
    "registered_inactive": SOURCE_DB,
    "fk_parent_key_legal": SOURCE_DB,
    "name_registered": SOURCE_CODE,
    "literal_derivable": SOURCE_CODE,
    "param_shadowed": SOURCE_CODE,
    # `reader_wired` reads the CALLERS, so its source is the code tree, not the DB.
    "reader_wired": SOURCE_CODE,
    # Its RUNTIME twin reads a DIFFERENT named caller list, so it is also a code
    # family. MEASURED: adding the family without this row left it sourced as
    # `db`, and the reader was then asked a DB question about a file that has no
    # table — the "two vocabularies in two places" defect this module already
    # records for `RULE_KINDS`.
    "reader_wired_runtime": SOURCE_CODE,
}


def evidence_source(family: str) -> str:
    """The SOURCE a family is answered from: `db`, `code`, or '' when unknown.

    '' for an unknown family, deliberately: a caller can then tell "no such
    family" from "a family whose source is undeclared", and it never guesses
    `db` for something it does not know.
    """
    return EVIDENCE_FAMILY_SOURCE.get(str(family or "").strip(), "")


def evidence_family(question_id: str) -> str:
    """The FAMILY a `question_id` belongs to, or "" when none matches.

    The per-field families carry a NUMBER (`field_3_name`), so they are matched
    by SUFFIX against the mapping's `field_N_*` keys. Returning "" for an unknown
    id is deliberate: a caller can then tell "no such family" from "a family
    with no answer", and `answer_by_evidence` refuses on both.
    """
    qid = str(question_id or "").strip()
    if qid in EVIDENCE_ANSWERS:
        return qid
    if qid.startswith("field_") and qid.endswith("_name"):
        return "field_N_name"
    if qid.startswith("field_") and qid.endswith("_type"):
        return "field_N_type"
    return ""


# WHICH FAMILIES MAY *GATE* A FLOW (stop the line on a mismatch).
#
# A GATE is a step whose answer is JUDGED against an expected value, so a family
# may gate ONLY IF its answer has an INDEPENDENT expectation — a value the
# question is checked AGAINST, not a value read back from the subject.
#
# THE TEST IS "COULD THIS ANSWER *NO* FOR A REAL REASON?", and it is answered by
# MEASUREMENT, not taste:
#   * `db_driven`   the expectation is the DEFINITION of db-driven (id PK +
#                   AUTOINCREMENT). A table with a different PK answers NO.
#   * `field_count` the expectation is the DECLARED column count (from the DDL).
#                   A table missing a declared column answers NO.
#   * `field_N_name`/`field_N_type`  the expectation is the DECLARED column at N.
#                   An undeclared or mistyped column answers NO.
#   * `registered`  the expectation is a row in `db_table_registry`. Absent → NO.
#
# `registered_inactive` is a REPORT, NOT a gate: a table may LEGITIMATELY be
# active, so a NO is not a defect. Gating on it would fail a correct table — the
# "false by construction" defect this session already fixed twice. The
# `code`-source families (`name_registered`/`literal_derivable`/`param_shadowed`)
# are reports too: a name may be unregistered by design, a literal may be
# necessary, and a shadowed param is a REVIEW list, not a verdict
# (`hardcode_scan` self-describes as "a REVIEW list, not a verdict").
GATE_ABLE_FAMILIES: frozenset[str] = frozenset({
    "db_driven", "field_count", "field_N_name", "field_N_type", "registered",
})

# The families that REPOSIT — their answer is information, never a stop.
REPORT_ONLY_FAMILIES: frozenset[str] = frozenset({
    "registered_inactive", "name_registered", "literal_derivable",
    "param_shadowed",
})

# The gate set a flow uses when the caller does NOT choose one: every question
# whose family may gate. A caller that wants a narrower line passes its own.
DEFAULT_GATE_FAMILIES: tuple[str, ...] = tuple(sorted(GATE_ABLE_FAMILIES))


def gate_questions_for(questions: list[dict[str, Any]],
                       families: tuple[str, ...] = DEFAULT_GATE_FAMILIES
                       ) -> tuple[str, ...]:
    """The `question_id`s that should GATE, selected BY FAMILY.

    WHY BY FAMILY AND NOT BY A HAND-LIST. A hand-picked set of ids would go stale
    the moment the field count changes (`field_7_name` would be missing for an
    8-column table), and a stale gate list silently stops judging. Selecting by
    FAMILY means "every question of a gate-able kind is a gate", which is
    DERIVED from `GATE_ABLE_FAMILIES` and cannot drift.

    The per-field families match by SUFFIX via `evidence_family`, so this returns
    `field_1_name` … `field_N_name` for any N without naming them.
    """
    wanted = {str(f) for f in families}
    return tuple(str(q.get("question_id")) for q in questions
                 if evidence_family(str(q.get("question_id"))) in wanted)


def answer_by_evidence(conn: sqlite3.Connection, spec: dict[str, Any],
                       question: dict[str, Any]) -> dict[str, Any]:
    """`{ok, answer, evidence_ref, query, value, source}` — WITH its evidence.

    REFUSES when no function answers the question. It does NOT fall back to a
    default: a silent fallback is the defect this module removes, and an answer
    without a query is an assertion rather than a measurement.

    THE SOURCE IS DECLARED AND RETURNED (added 2026-09-23). A `code` answer's
    query must be a `path:line` citation or a command, so the citation rule
    survives the new source kind instead of being quietly relaxed for it.
    """
    qid = str(question.get("question_id") or "")
    fam = evidence_family(qid)
    if not fam:
        return {"ok": False, "code": "NO_EVIDENCE_ANSWER",
                "message": ("question %r belongs to no evidence family; known "
                            "families: %s" % (qid, sorted(EVIDENCE_ANSWERS))),
                "question_id": qid}
    fn = EVIDENCE_ANSWERS[fam]
    source = evidence_source(fam)
    try:
        res = fn(conn, spec, question)
    except Exception as exc:
        return {"ok": False, "code": "EVIDENCE_FAILED",
                "message": "evidence for %r failed: %s: %s"
                           % (qid, type(exc).__name__, exc),
                "question_id": qid, "family": fam, "source": source}
    query = str(res.get("query") or "").strip()
    if not query:
        # AN ANSWER WITHOUT A QUERY IS AN ASSERTION. Refused at the source, so
        # no caller can ever receive one.
        return {"ok": False, "code": "ANSWER_WITHOUT_EVIDENCE",
                "message": ("the evidence function for %r returned no query, so "
                            "the answer is an assertion rather than a "
                            "measurement" % qid),
                "question_id": qid, "family": fam, "source": source}
    # A `code` ANSWER MUST CITE A POSITION **OR A COMMAND**. The source class
    # exists so the requirement can be CHECKED per kind: a DB answer's query is a
    # statement that can be re-run, a code answer's is either a place in a file
    # (`path:line`) or the call that produced the answer.
    #
    # MEASURED, and my first version was WRONG: it demanded a `:` and therefore
    # REFUSED `terminology_registry.assert_named(conn, 'x')` — a perfectly good
    # command, and one the plan explicitly allows ("a `path:line` or a command").
    # A rule that refuses the delegate call it is built on is the same defect
    # family as a gate that forbids the action its own help names.
    if source == SOURCE_CODE and ":" not in query and "(" not in query:
        return {"ok": False, "code": "CODE_ANSWER_WITHOUT_CITATION",
                "message": ("a `code`-source answer for %r must cite a "
                            "`path:line` or a command; got %r"
                            % (qid, query)),
                "question_id": qid, "family": fam, "source": source}
    return {"ok": True, "question_id": qid, "family": fam, "source": source,
            "answer": str(res.get("answer") or "").strip().upper(),
            "evidence_ref": query, "query": query, "value": res.get("value")}


def answer_all(conn: sqlite3.Connection, spec: dict[str, Any], *,
               code_questions: bool = False,
               register_wording: bool = False) -> dict[str, Any]:
    """Answer EVERY question a spec generates. Reports what it could not answer.

    Returns `{ok, answered, refused, by_source, results}`. A refused question is
    REPORTED with its code — never silently dropped, because a question with no
    answer is a question the run cannot measure.

    `by_source` (added 2026-09-23) counts the answers per SOURCE (`db`/`code`),
    so a run cannot report a mix of measurements and code reads as if it were one
    kind of thing. A caller that expected only DB answers can SEE the others.

    `register_wording=True` (added 2026-09-24) makes the QUESTION TEXT come from
    the registers. It is opt-in and OFF by default, so an existing caller's
    questions do not change underneath it — and the result reports
    `wording_by_source` so a register-backed run is distinguishable from a
    constant-backed one.
    """
    result = generate(spec, code_questions=code_questions,
                      conn=conn if register_wording else None)
    results: list[dict[str, Any]] = []
    for q in result["questions"]:
        r = answer_by_evidence(conn, spec, q)
        r["question"] = q["question"]
        r["dimension"] = q["dimension"]
        r["wording_source"] = q.get("wording_source", "")
        results.append(r)
    refused = [r for r in results if not r["ok"]]
    by_source: dict[str, int] = {}
    for r in results:
        if r["ok"]:
            src = str(r.get("source") or "?")
            by_source[src] = by_source.get(src, 0) + 1
    return {"ok": not refused, "subject": result["subject"],
            "questions": result["count"],
            "answered": len(results) - len(refused),
            "refused": len(refused), "results": results,
            "by_source": by_source, "code_questions": bool(code_questions),
            "wording_by_source": result.get("wording_by_source") or {},
            "register_wording": bool(register_wording)}


def oracle_from_evidence(conn: sqlite3.Connection, spec: dict[str, Any],
                         question: dict[str, Any]):
    """An `oracle_fn` for `run_harness`, judged by EVIDENCE.

    The returned function takes the value under test and returns 'YES'/'NO'. The
    value is IGNORED for the structural questions — the answer comes from the DB,
    which is the point: the oracle is the pass/fail definition, and a definition
    read from the database is checkable while a hardcoded one is not.

    A question with no evidence answer yields an oracle that answers 'NO' — an
    undecidable case is not a pass, the same rule `oracle_from_contract` states.
    """
    def _fn(_raw: str) -> str:
        r = answer_by_evidence(conn, spec, question)
        return r["answer"] if r.get("ok") else "NO"
    return _fn


def explain_count(result: dict[str, Any]) -> str:
    """The derivation, as text. The count must never be a bare number.

    A number with no derivation is a number a reader cannot check, and the
    "100" in "100 run" is exactly that: a figure that asserts a quantity the
    system does not have.
    """
    lines = ["question count for %r: %d" % (result["subject"], result["count"])]
    for d in result["derivation"]:
        lines.append("  + %s" % d)
    lines.append("  = %d questions, %d cases (each with a YES and a NO form)"
                 % (result["count"], result["case_count"]))
    lines.append("  formula: %s" % result["formula"])
    lines.append("  dimensions used: %s" % ", ".join(result["dimensions_used"]))
    return "\n".join(lines)


def to_tdd_cases(result: dict[str, Any], contract_id: str) -> list[dict[str, Any]]:
    """The question list as TDD cases, one PASS and one HARD_FAIL per question.

    The PASS case asserts the YES form is accepted; the HARD_FAIL case asserts
    the NO form is REFUSED. `expected` is ALWAYS a non-empty dict — a plain
    string is silently read as "no expectation recorded" and the case would pass
    unconditionally.
    """
    cases: list[dict[str, Any]] = []
    for q in result["questions"]:
        qid = q["question_id"]
        cases.append({
            "case_key": "%s.pass.%s" % (contract_id, qid),
            "kind": "pass",
            "assertion": "%s — the YES form is accepted" % q["question"],
            "input": {"question_id": qid, "form": "yes"},
            "expected": {"accepted": True, "question_id": qid},
        })
        cases.append({
            "case_key": "%s.hard_fail.%s" % (contract_id, qid),
            "kind": "hard_fail",
            "assertion": "%s — the NO form is REFUSED" % q["question"],
            "input": {"question_id": qid, "form": "no"},
            "expected": {"accepted": False, "question_id": qid},
        })
    return cases


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

EXAMPLE_SPEC: dict[str, Any] = {
    "subject": "table A",
    "kind": "db_table",
    "field_count": 3,
    "field_names": ["x", "y", "z"],
    # THE DECLARED TYPES ARE REQUIRED (2026-09-24). `validate_spec` REFUSES a spec
    # whose type questions have no UNIT, because a spec with no unit is a defect
    # rather than a question that can be answered `UNKNOWN` (the old design the
    # user rejected). MEASURED: without this key `generate(EXAMPLE_SPEC)` RAISED,
    # so the module's own documented example was unusable — a spec that ships
    # broken is worse than no example. The types are the DECLARED ones, so the
    # example demonstrates the intended comparison (live vs declared).
    "field_types": ["INTEGER", "TEXT", "TEXT"],
    "dimensions": {
        "what": "a table with 3 fields",
        "why": "it must be DB driven and registered",
        "who": "the schema worker writes it; the QC gate approves it",
        "when": "before any row is inserted",
        "where": "agent.db",
        "how": "read the DDL and compare it to the register",
    },
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="generate the question list")
    ap.add_argument("--example", action="store_true",
                    help="the user's own example (table A, 3 fields)")
    ap.add_argument("--spec", metavar="PATH", help="a spec JSON file")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    elif args.example:
        spec = EXAMPLE_SPEC
    else:
        ap.print_help()
        return 2

    try:
        result = generate(spec)
    except SpecError as e:
        print("REFUSED: %s" % e)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(explain_count(result))
    print()
    for i, q in enumerate(result["questions"], 1):
        print("%2d. [%s] %s" % (i, q["dimension"], q["question"]))
        print("      YES: %s" % q["yes_form"])
        print("      NO : %s" % q["no_form"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
