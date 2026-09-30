"""value_type.py -- ONE vocabulary for the value format AND the proof method.

THE HUMAN, verbatim:

    "no matter what, for proof run target is value!"
    "and value format is the key for 7B-intract or 7B vl"
    "both side need to have same language"
    "proof run need to understand and know which data format need to how to proof
     = same language"

THE DEFECT (MEASURED 2026-09-26):

    The QUESTION side says what a value IS:

        llm_service_type_registry:   text | visual | NA
        value_type (4 tables):       bool | datetime | json | number | string

    The PROOF side says how to PROVE it:

        playwright_step.method:      cdp | coordinate | hotkey | session | window | NA
        playwright_step.step_kind:   browser | window | session

    MEASURED -- the intersection:

        question side: ['NA', 'text', 'visual']
        proof side   : ['NA', 'cdp', 'coordinate', 'hotkey', 'session', 'window']
        SHARED       : ['NA']

    The two sides share ONE word, and it is the word for "nothing". So a proof run
    cannot answer "which data format needs how to proof" -- the two vocabularies
    have no word in common.

THE FIX: ONE word list. `value_type` selects the route AND the proof method:

    value_type  ->  route  ->  model  ->  proof method

    text/number/bool/json/datetime  ->  llm.text    ->  qwen2.5:7b-instruct  ->  tdd
    image                           ->  llm.vision  ->  qwen2.5vl:7b         ->  visual
    coordinate                      ->  llm.vision  ->  qwen2.5vl:7b         ->  coordinate

`text` / `number` / `bool` / `json` / `datetime` are the EXISTING vocabulary
(MEASURED in 4 tables). `image` and `coordinate` are NEW, and they are new
because the human named them. `coordinate` is ALSO an existing proof method
(MEASURED in `playwright_step.method`, 2 rows) -- so the proof side keeps its own
word and the value side adopts it. THAT is what makes the two sides share a word.

NO MODEL NAME IS HARDCODED HERE. The route is a `llm_service.llm_route` value,
and the model is resolved from `llm_route_provider` by `llm_service_store`.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# THE ONE WORD LIST
# ---------------------------------------------------------------------------
# The 5 EXISTING value types, MEASURED in `fault_event_fact` / `schema_ssot` /
# `task_ssot` / `fault_ssot`. They are reused, not re-invented: a second word for
# "a number" would make every existing reader wrong.
_EXISTING_VALUE_TYPES: tuple[str, ...] = (
    "bool", "datetime", "json", "number", "string",
)

# The 2 NEW value types, named by the human. `image` is a value that must be
# SEEN; `coordinate` is a value that IS a rectangle on a screen.
_NEW_VALUE_TYPES: tuple[str, ...] = ("image", "coordinate")

VALUE_TYPES: tuple[str, ...] = _EXISTING_VALUE_TYPES + _NEW_VALUE_TYPES

# THE ALIASES -- the SAME language, spelled two ways (added 2026-09-26).
#
# MEASURED, and this is a REAL naming collision the human's rule exposes:
#
#     llm_service_type_registry:   text | visual | NA
#     value_type (4 tables):       bool | datetime | json | number | string
#
# `text` and `string` are the SAME thing, spelled two ways. The human: "both side
# need to have same language". So BOTH spellings are accepted and resolve to the
# SAME canonical word -- `string`, because 4 tables already use it and changing
# them would make every existing reader wrong.
#
# `visual` is NOT an alias for `image`: `visual` is a ROUTE's needs_flag (what the
# model can do), while `image` is a VALUE's format (what the value is). They are
# different axes, and conflating them is the defect this module removes.
_ALIASES: dict[str, str] = {
    "text": "string",
}


def canonical(value_type: str) -> str:
    """The canonical spelling of a `value_type`. REFUSES an unknown one.

    `text` -> `string` (the same thing, spelled two ways). Everything else is
    already canonical.
    """
    key = str(value_type or "").strip()
    key = _ALIASES.get(key, key)
    if key not in VALUE_TYPES:
        raise ValueTypeRefused(
            "value_type %r is not in VALUE_TYPES %s (aliases: %s). An unknown "
            "format cannot select a route or a proof method, and defaulting would "
            "judge the value with the wrong model."
            % (value_type, list(VALUE_TYPES), sorted(_ALIASES)))
    return key

# THE PROOF METHODS. `tdd` is the text comparison; `visual` is
# `evidence_classify.classify_with_overlay`; `coordinate` is the rect itself.
# MEASURED: `coordinate` ALREADY exists in `playwright_step.method` (2 rows), so
# the proof side keeps its own word and the value side adopts it.
PROOF_METHODS: tuple[str, ...] = ("tdd", "visual", "coordinate")

# value_type -> proof method. THE JOIN between the two sides.
_PROOF_METHOD_BY_VALUE_TYPE: dict[str, str] = {
    "bool": "tdd",
    "datetime": "tdd",
    "json": "tdd",
    "number": "tdd",
    "string": "tdd",
    "image": "visual",
    "coordinate": "coordinate",
}

# value_type -> llm_service.llm_route. MEASURED: both routes are registered
# (`llm.text` needs_flag='text', `llm.vision` needs_flag='visual').
_ROUTE_BY_VALUE_TYPE: dict[str, str] = {
    "bool": "llm.text",
    "datetime": "llm.text",
    "json": "llm.text",
    "number": "llm.text",
    "string": "llm.text",
    "image": "llm.vision",
    "coordinate": "llm.vision",
}

# The value types that need a MODEL to SEE the value. A `coordinate` is proved by
# the rect itself, but the LABEL inside it is read by a VL model, so it is visual.
_VISUAL_VALUE_TYPES: frozenset[str] = frozenset({"image", "coordinate"})


class ValueTypeRefused(ValueError):
    """A value_type that is not in the ONE word list."""


def route_for(value_type: str) -> str:
    """The `llm_service.llm_route` a `value_type` is judged by.

    REFUSES an unknown value_type rather than defaulting: a silent default would
    make a run report a route it never resolved -- the same rule `resolve_model`
    follows.
    """
    key = canonical(value_type)
    return _ROUTE_BY_VALUE_TYPE[key]


def proof_method_for(value_type: str) -> str:
    """The proof method a `value_type` is proved by. REFUSES an unknown type."""
    key = canonical(value_type)
    return _PROOF_METHOD_BY_VALUE_TYPE[key]


def needs_vision(value_type: str) -> bool:
    """Does proving this value_type require a model that can SEE?"""
    return canonical(value_type) in _VISUAL_VALUE_TYPES


def classify_value(raw: Any) -> str:
    """The `value_type` of a RAW value, by parsing it.

    MEASURED: `proof_run.value` holds YAML text, and the parsed types that
    actually occur are int 193 / str 90 / bool 51 / float 40 / NoneType 26. So
    the format is DERIVED from the value rather than declared -- and a value that
    cannot be parsed is `string`, which is the honest answer (it IS text).

    A `dict` / `list` is `json`. A `bool` is checked BEFORE `int`, because in
    Python `True` is an `int` -- the classic ordering defect.
    """
    if isinstance(raw, bool):
        return "bool"
    if isinstance(raw, int):
        return "number"
    if isinstance(raw, float):
        return "number"
    if isinstance(raw, (dict, list)):
        return "json"
    if raw is None:
        return "string"
    text = str(raw).strip()
    if not text:
        return "string"
    # A YAML/JSON literal in a TEXT column: parse it, and fall back to `string`.
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return "string"
    if isinstance(parsed, bool):
        return "bool"
    if isinstance(parsed, (int, float)):
        return "number"
    if isinstance(parsed, (dict, list)):
        return "json"
    return "string"


# ---------------------------------------------------------------------------
# THE COLUMN -> value_type DERIVATION
# ---------------------------------------------------------------------------
# MEASURED DEFECT (2026-09-26): the proof run declared EVERY column `string`
# (2076 / 2076 registers). That is the exact defect the human named --
# "value format is the key for 7B-intract or 7B vl" -- because a `string`
# declaration routes EVERY column to `llm.text`, so a column that holds a
# rectangle is judged by a model that cannot see it.
#
# The format is DERIVED from the LIVE column, never typed:
#
#   * the SQLite declared type (`PRAGMA table_info`) says INTEGER / REAL / TEXT;
#   * the column NAME says whether the TEXT is really a coordinate, an image, a
#     timestamp, or a JSON blob.
#
# A name-based rule is a HEURISTIC, and a heuristic is only honest when it is
# named as one and its evidence is carried. So `value_type_for_column` returns
# the value_type AND the reason, and the reason is what gets written to
# `proof_run_registry.cite_ref`.
_COORDINATE_NAME_HINTS: tuple[str, ...] = (
    "x1", "y1", "x2", "y2", "rect", "coord", "bbox", "left", "top",
    "width", "height", "click_x", "click_y",
)
_IMAGE_NAME_HINTS: tuple[str, ...] = (
    "image", "img", "screenshot", "png", "jpg", "jpeg", "photo", "frame",
    "overlay", "vl_path", "shot",
)
_DATETIME_NAME_HINTS: tuple[str, ...] = (
    "at", "time", "date", "timestamp", "created", "updated", "measured",
    "started", "finished", "seen", "expires",
)
_JSON_NAME_HINTS: tuple[str, ...] = (
    "json", "payload", "fields", "columns", "steps", "edges", "questions",
    "checklist", "config", "options", "meta", "tags", "list", "items",
)
_BOOL_NAME_HINTS: tuple[str, ...] = (
    "is_", "has_", "can_", "should_", "enabled", "active", "ok", "pass",
    "fail", "done", "valid", "allow",
)


def value_type_for_column(column_name: str, declared_type: str = "") -> dict[str, Any]:
    """The `value_type` of a COLUMN, DERIVED from its name and declared type.

    Returns `{value_type, reason, source}`. The `reason` is the evidence, and it
    is written to `proof_run_registry.cite_ref` so a reader can see WHY a column
    was declared the way it was -- a declaration with no reason is a guess.

    ORDER MATTERS, and it is the order of the evidence's strength:

      1. the SQLite declared type -- INTEGER/REAL is `number`, and that is a FACT
         from `PRAGMA`, not a guess from a name;
      2. the column NAME -- a coordinate, an image, a timestamp, a JSON blob, a
         boolean. These are HEURISTICS, and they are named as such;
      3. the fallback is `string`, which is the honest answer for TEXT.

    A `coordinate` beats an `image` when both match, because a rect is a MORE
    specific claim than "this is a picture" and the more specific claim is the one
    that selects the proof method.
    """
    name = str(column_name or "").strip().lower()
    dtype = str(declared_type or "").strip().upper()

    # 0. THE UNAMBIGUOUS PREFIXES, checked BEFORE the declared type.
    #
    # MEASURED DEFECT (2026-09-26): `is_active INTEGER` was derived as `number`,
    # because the declared type was checked first. But `is_active` is a BOOLEAN
    # stored as an INTEGER -- SQLite has no BOOLEAN type, so the declared type
    # cannot tell a flag from a count. The NAME can: `is_` / `has_` / `can_` /
    # `should_` are unambiguous boolean prefixes, and a flag is a flag whatever
    # SQLite calls it.
    for hint in ("is_", "has_", "can_", "should_"):
        if name.startswith(hint):
            return {"value_type": "bool", "source": "name",
                    "reason": "column name %r starts with the boolean prefix %r, "
                              "and SQLite stores a flag as INTEGER" % (column_name, hint)}

    # 1. the declared type is a FACT.
    if ("INT" in dtype or "REAL" in dtype or "FLOA" in dtype
            or "DOUB" in dtype or "NUM" in dtype):
        # ...but a numeric column whose NAME says it is a coordinate is a
        # coordinate: the number IS a pixel, and the proof is a rect.
        for hint in _COORDINATE_NAME_HINTS:
            if name == hint or name.endswith("_" + hint) or name.startswith(hint + "_"):
                return {"value_type": "coordinate", "source": "name",
                        "reason": "column %r is %s and its name matches the "
                                  "coordinate hint %r" % (column_name, dtype, hint)}
        return {"value_type": "number", "source": "pragma",
                "reason": "PRAGMA table_info declares %r as %s"
                          % (column_name, dtype)}

    # 2. the name is a HEURISTIC, strongest claim first.
    for hint in _COORDINATE_NAME_HINTS:
        if name == hint or name.endswith("_" + hint) or name.startswith(hint + "_"):
            return {"value_type": "coordinate", "source": "name",
                    "reason": "column name %r matches the coordinate hint %r"
                              % (column_name, hint)}
    for hint in _IMAGE_NAME_HINTS:
        if name == hint or name.endswith("_" + hint) or name.startswith(hint + "_"):
            return {"value_type": "image", "source": "name",
                    "reason": "column name %r matches the image hint %r"
                              % (column_name, hint)}
    for hint in _DATETIME_NAME_HINTS:
        if name == hint or name.endswith("_" + hint) or name.startswith(hint + "_"):
            return {"value_type": "datetime", "source": "name",
                    "reason": "column name %r matches the datetime hint %r"
                              % (column_name, hint)}
    for hint in _JSON_NAME_HINTS:
        if name == hint or name.endswith("_" + hint) or name.startswith(hint + "_"):
            return {"value_type": "json", "source": "name",
                    "reason": "column name %r matches the json hint %r"
                              % (column_name, hint)}
    for hint in _BOOL_NAME_HINTS:
        if name == hint or name.startswith(hint):
            return {"value_type": "bool", "source": "name",
                    "reason": "column name %r matches the bool hint %r"
                              % (column_name, hint)}

    # 3. the honest fallback.
    return {"value_type": "string", "source": "fallback",
            "reason": "column %r is %s and no name hint matched; TEXT is `string`"
                      % (column_name, dtype or "untyped")}


def describe(value_type: str) -> dict[str, Any]:
    """The full resolution for a `value_type`: route, proof method, vision need.

    ONE call, so a reader never has to join three tables to answer "which data
    format needs how to proof".
    """
    key = canonical(value_type)
    return {
        "value_type": key,
        "route": route_for(key),
        "proof_method": proof_method_for(key),
        "needs_vision": needs_vision(key),
    }


def shared_words() -> list[str]:
    """The words BOTH sides use. MEASURED before the fix: `['NA']` only.

    This is the check that the two sides speak the same language. `NA` is
    excluded because it is the word for "nothing" -- sharing it proves nothing.
    """
    return sorted((set(VALUE_TYPES) & set(PROOF_METHODS)) - {"NA"})


# THE DECLARATION OF THE TABLE ITSELF, HOISTED TO A MODULE CONSTANT
# (fixed 2026-09-27).
#
# MEASURED DEFECT: the `CREATE TABLE proof_run_registry` text was an INLINE
# `conn.executescript("""...""")` inside `ensure_schema`, so no module-level
# constant declared this table. `logic_generator._iter_ddl_constants` scans for
# `NAME_DDL = """..."""` at module level — an inline string cannot be found, so
# `declared_shape('proof_run_registry')` saw no declaration and
# `question_generator` refused a table with 2123 real rows.
#
# THE POINT OF THE DECLARATION IS NOT DECORATION: `spec_from_table` reads the
# EXPECTATION from the code DDL and the SUBJECT from the live table, so the
# question "does the live table match its declaration" can answer NO. Hoisting the
# text to a constant is what makes both sides readable — the alternative (read the
# expectation from `PRAGMA` too) is the tautology `declared_shape` documents.
proof_run_registry_DDL = """
        CREATE TABLE IF NOT EXISTS proof_run_registry (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            db_table_id   INTEGER NOT NULL,
            db_field_id   INTEGER NOT NULL,
            value_type    TEXT    NOT NULL,
            is_active     INTEGER NOT NULL DEFAULT 1,
            cite_ref      TEXT    NOT NULL DEFAULT 'NA',
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (db_table_id, db_field_id)
        );
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the two tables this vocabulary is recorded in. Idempotent.

    `proof_run_registry` is the PER-COLUMN declaration: WHICH column, WHICH value
    format. It is the join key that does not exist today -- MEASURED: `proof_run`
    has no `db_field_id`, and `field_tdd_rule` has no `db_field_id` either.

    `proof_report` is the VERDICT: how many rounds passed, how many failed, and
    why. `pass` / `fail` are COUNTS (the human wrote two columns).
    """
    conn.executescript(proof_run_registry_DDL)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS proof_report (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            proof_run_registry_id INTEGER NOT NULL,
            pass                  INTEGER NOT NULL DEFAULT 0,
            fail                  INTEGER NOT NULL DEFAULT 0,
            reason                TEXT    NOT NULL DEFAULT 'NA',
            measured_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="agent.db")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    print("=== THE ONE WORD LIST ===")
    print("  VALUE_TYPES  : %s" % list(VALUE_TYPES))
    print("  PROOF_METHODS: %s" % list(PROOF_METHODS))
    print()
    print("=== THE JOIN (value_type -> route -> proof method) ===")
    for vt in VALUE_TYPES:
        d = describe(vt)
        print("  %-10s -> %-11s -> %-10s (vision=%s)"
              % (vt, d["route"], d["proof_method"], d["needs_vision"]))
    print()
    print("=== DO THE TWO SIDES SHARE A WORD? ===")
    print("  shared (excluding NA): %s" % shared_words())
    print("  MEASURED before the fix: ['NA'] only")

    if args.apply:
        conn = sqlite3.connect(args.db)
        try:
            ensure_schema(conn)
            print("\n=== schema ensured ===")
        finally:
            conn.close()
    else:
        print("\nREPORT ONLY. Pass --apply to create the tables.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
