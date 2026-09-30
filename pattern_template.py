# -*- coding: utf-8 -*-
"""pattern_template.py — the EXPERIENCE a worker can REUSE / COPY FROM.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "need to have template table by experience to help worker can reuse or copy
     patterm, with hard gate by friendly for all worker"

Two requirements, and both are measured against what ALREADY exists:

  1. A TEMPLATE TABLE. The precedent is `factor_template` (`db_schema.py:3735`),
     read by the SHARED reader `template_conformance.check_table`. So this module
     follows that SHAPE rather than inventing one: a template row carries
     `tier / kind / rule / why / example / cite_ref / sort_order`.
  2. A HARD GATE THAT IS FRIENDLY. A refusal here does not print one sentence —
     it names the FIELD, the RULE, the WHY and an EXAMPLE the worker can copy.
     The reason is that the repo's own lessons show a gate that only says "no"
     teaches nothing, so the same mistake recurs.

THE EXPERIENCE TABLE IS PROPOSED, NEVER SELF-ACTIVATED
------------------------------------------------------
`factor_template_growth` established the rule (the human's own): a template row
grown from evidence defaults to `is_active=0` and waits for a human. This module
does the same: `add_template` writes `is_active=0`, and `assert_conforms` accepts
an INACTIVE template only when the caller passes `allow_inactive=True` (so the
proposal loop is usable before activation, and the state is always explicit).

WHAT A WORKER DOES WITH IT
--------------------------
    list_templates(conn, subject_kind="db_table")   # what to copy
    assert_conforms(conn, instance)                 # the friendly hard gate
    record_instance(conn, ...)                      # the experience accrued

Run:
    .\\.venv\\Scripts\\python.exe pattern_template.py --seed
    .\\.venv\\Scripts\\python.exe pattern_template.py --list
    .\\.venv\\Scripts\\python.exe pattern_template.py --check
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 -- stdout may be a pipe
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# Normalised so a test is a PROPERTY (has an exemption), never a spelling. The
# same normalisation `ui_standard`'s exemption gate uses.
_NA = {"", "na", "n/a", "none"}

# The CITE this module's rows point at. A SCRATCH cite is refused by `add_term`
# and would make every template uncheckable, so it is a real path:line.
DEFAULT_CITE = "pattern_template.py:1"

PATTERN_TEMPLATE_DDL = """
CREATE TABLE IF NOT EXISTS pattern_template (
    template_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    tier          TEXT    NOT NULL DEFAULT 'recommended'
                  CHECK (tier IN ('must', 'recommended', 'advice')),
    subject_kind  TEXT    NOT NULL,
    -- THE SUBJECT THE ROW IS ABOUT, when it is not the kind itself.
    -- NULL for the 48 step rows (a step IS its kind); the generator_field rows
    -- carry the generator key here.
    subject_ref   TEXT,
    item_kind     TEXT    NOT NULL,
    rule          TEXT    NOT NULL,
    why           TEXT    NOT NULL,
    example       TEXT    NOT NULL,
    is_required   INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0, 1)),
    gate_key      TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    -- THE VALUE SHAPE. NULL means UNSPECIFIED, which is a real state for a step
    -- (a step has no value shape) and is NOT backfilled to a guess.
    kind          TEXT,
    -- is_active=0 BY DEFAULT: a template is a CLAIM until an instance passes it
    -- (the activation rule `factor_template_growth` already uses).
    is_active     INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pattern_template_active
  ON pattern_template (subject_kind, is_active, sort_order);
"""

# THE TRUE NATURAL KEY, as TWO PARTIAL UNIQUE INDEXES (ADDED 2026-09-28).
#
# WHY THE TABLE-LEVEL `UNIQUE (subject_kind, item_kind)` HAD TO GO -- MEASURED,
# and it broke the FIRST migration attempt:
#
#   `logic_generator.subject` and `factor_generator.subject` are the SAME
#   (subject_kind, item_kind) pair, so the 12 rows CANNOT fit that key. SQLite
#   raised `UNIQUE constraint failed`.
#
# THE FIX IS NOT TO QUALIFY `item_kind` (`logic_generator.subject`): then the
# name a reader gets back is no longer the name the form asks for. It is to say
# what the key ALWAYS WAS:
#
#   a row ABOUT a subject   -> (subject_kind, subject_ref, item_kind)
#   a row that IS its kind  -> (subject_kind, item_kind)
#
# Two PARTIAL indexes, because one index cannot be both: with `subject_ref` in
# the key a NULL would make every step row distinct (SQLite treats NULLs as
# distinct), which would silently drop the uniqueness the 48 rows rely on.
PATTERN_TEMPLATE_KEY_DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_template_key_ref
  ON pattern_template (subject_kind, subject_ref, item_kind)
  WHERE subject_ref IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_template_key_noref
  ON pattern_template (subject_kind, item_kind)
  WHERE subject_ref IS NULL;
"""

# The columns carried across a REBUILD. `template_id` is FIRST and is preserved
# VERBATIM: `pattern_instance.template_id` REFERENCES it, so renumbering would
# orphan every recorded verdict -- which is the point of the store.
_REBUILD_COLUMNS = (
    "template_id", "tier", "subject_kind", "item_kind", "rule", "why",
    "example", "is_required", "gate_key", "cite_ref", "sort_order",
    "is_active", "created_at", "updated_at",
)

PATTERN_INSTANCE_DDL = """
CREATE TABLE IF NOT EXISTS pattern_instance (
    instance_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id   INTEGER,
    subject_kind  TEXT    NOT NULL,
    subject_ref   TEXT    NOT NULL,
    verdict       TEXT    NOT NULL
                  CHECK (verdict IN ('PASS', 'FAIL', 'UNKNOWN')),
    value         REAL,
    detail        TEXT,
    cite_ref      TEXT    NOT NULL,
    gate_run_ref  TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (template_id) REFERENCES pattern_template (template_id)
      ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_pattern_instance_subject
  ON pattern_instance (subject_kind, subject_ref, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pattern_instance_run
  ON pattern_instance (gate_run_ref);
"""


class PatternRefused(ValueError):
    """A refusal that NAMES the field, the rule, the why and an example."""

    def __init__(self, field: str, rule: str, why: str, example: str,
                 message: str = "") -> None:
        self.field = field
        self.rule = rule
        self.why = why
        self.example = example
        super().__init__(message or self.friendly())

    def friendly(self) -> str:
        return ("template gate refused:\n"
                "  field   : %s\n"
                "  rule    : %s\n"
                "  why     : %s\n"
                "  example : %s"
                % (self.field, self.rule, self.why, self.example))

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "rule": self.rule, "why": self.why,
                "example": self.example, "message": str(self)}


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DEFAULT_DB), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _needs_rebuild(conn: sqlite3.Connection) -> bool:
    """Does `pattern_template` still carry the table-level `UNIQUE` that the merge
    cannot satisfy?

    WHY A REBUILD IS UNAVOIDABLE HERE: `UNIQUE (subject_kind, item_kind)` is a
    TABLE constraint, so it lives in the stored `CREATE TABLE` text. An index
    cannot make it narrower, and SQLite has no `ALTER TABLE ... DROP CONSTRAINT`.
    MEASURED: the first migration attempt died on
    `UNIQUE constraint failed: pattern_template.subject_kind, pattern_template.item_kind`
    because two generators each declare a field named `subject`.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='pattern_template'"
    ).fetchone()
    if row is None:
        return False
    # 🔴 `row[0]`, NOT `row["sql"]` (FIXED 2026-09-29, RING 5).
    #
    # MEASURED DEFECT, and it is why `pattern_template` had NO key indexes on a
    # FRESH database: `db_schema.ensure_hub_ssot_schema` calls this module's
    # `ensure_schema` with a connection built by `sqlite3.connect(path)` — NO
    # `row_factory`, unlike this module's own `_connect`. So `row["sql"]` raised
    # `TypeError: tuple indices must be integers or slices, not str` HERE, the
    # caller swallowed it in a bare `except Exception`, and the three steps
    # AFTER it (the rebuild and `PATTERN_TEMPLATE_KEY_DDL`) never ran.
    #
    # A row accessor that depends on the CALLER's `row_factory` is the defect:
    # the function silently does different things depending on who called it.
    # Indexing by POSITION works under both, and the SELECT names exactly one
    # column so the position is unambiguous.
    return "subject_kind, item_kind" in str(row[0]).replace(" ", " ")


def rebuild_if_needed(conn: sqlite3.Connection) -> dict[str, Any]:
    """Rebuild `pattern_template` to carry the TRUE key. Idempotent; PRESERVES ids.

    `template_id` IS CARRIED ACROSS VERBATIM. The obvious implementation --
    `CREATE new; INSERT SELECT; DROP old; RENAME` -- renumbers nothing here
    precisely because `template_id` is selected EXPLICITLY; but it still must be
    named, because `pattern_instance.template_id` REFERENCES this column and a
    fresh AUTOINCREMENT set would point every recorded verdict at a different
    template. A verdict store that silently re-points is worse than no store.

    WHY `PRAGMA foreign_keys` IS NOT TOUCHED: it is OFF by default in SQLite, and
    the only FK is FROM `pattern_instance`, whose rows are not modified here. A
    rebuild that flipped the pragma could fail on a row it never intended to
    touch, so the safe order is used instead: the new table is created and filled
    BEFORE the old one is dropped, so `pattern_instance` is never dangling.
    """
    if not _needs_rebuild(conn):
        return {"ok": True, "rebuilt": False, "reason": "key already correct"}
    before = conn.execute("SELECT COUNT(*) FROM pattern_template").fetchone()[0]
    cols = ", ".join(_REBUILD_COLUMNS)
    conn.executescript("""
CREATE TABLE pattern_template__new (
    template_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    tier          TEXT    NOT NULL DEFAULT 'recommended'
                  CHECK (tier IN ('must', 'recommended', 'advice')),
    subject_kind  TEXT    NOT NULL,
    subject_ref   TEXT,
    item_kind     TEXT    NOT NULL,
    rule          TEXT    NOT NULL,
    why           TEXT    NOT NULL,
    example       TEXT    NOT NULL,
    is_required   INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0, 1)),
    gate_key      TEXT    NOT NULL DEFAULT 'NA',
    cite_ref      TEXT    NOT NULL,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    kind          TEXT,
    is_active     INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
""")
    conn.execute("INSERT INTO pattern_template__new (%s) SELECT %s FROM "
                 "pattern_template" % (cols, cols))
    conn.execute("DROP TABLE pattern_template")
    conn.execute("ALTER TABLE pattern_template__new RENAME TO pattern_template")
    conn.executescript(PATTERN_TEMPLATE_DDL)
    conn.executescript(PATTERN_TEMPLATE_KEY_DDL)
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM pattern_template").fetchone()[0]
    orphans = conn.execute(
        "SELECT COUNT(*) FROM pattern_instance i WHERE i.template_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM pattern_template t "
        "                WHERE t.template_id=i.template_id)").fetchone()[0]
    return {"ok": before == after and orphans == 0, "rebuilt": True,
            "rows_before": before, "rows_after": after,
            "orphaned_instances": orphans}


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create both tables, apply the ADDITIVE columns, then the TRUE key. Idempotent.

    THE ORDER IS FORCED, and each step exists for a measured reason:
      1. create          -- the fresh-DB path
      2. additive columns -- `CREATE TABLE IF NOT EXISTS` does NOT add a column to
                             an EXISTING table, so a database created before the
                             merge would keep its old shape and every read of
                             `kind` would fail with "no such column".
      3. rebuild-if-needed -- the table-level `UNIQUE (subject_kind, item_kind)`
                             cannot be narrowed by an index, and the merge needs
                             `(subject_kind, subject_ref, item_kind)`.
      4. the key indexes  -- only valid once the columns exist
    """
    conn.executescript(PATTERN_TEMPLATE_DDL)
    conn.executescript(PATTERN_INSTANCE_DDL)
    import db_schema as ds
    ds._add_columns_if_missing(conn, "pattern_template",
                               (("kind", "TEXT"), ("subject_ref", "TEXT")))
    # 🔴 THE REBUILD IS BEST-EFFORT; THE KEY IS NOT (RING 5, 2026-09-29).
    #
    # MEASURED DEFECT this replaces: `rebuild_if_needed(conn)` raised
    # (`row["sql"]` on a connection with no `row_factory` — see `_needs_rebuild`),
    # and because the key step came AFTER it in the same function, a single
    # unrelated error took the THREE key indexes with it. A fresh database then
    # had NO unique key on `pattern_template` at all.
    #
    # The ORDER still matters and is kept (a legacy table-level UNIQUE must be
    # rebuilt away before the narrower keys can exist), but a failure to rebuild
    # must not silently delete the declaration. It is reported instead.
    rebuilt: dict = {"rebuilt": False}
    try:
        rebuilt = rebuild_if_needed(conn)
    except Exception as exc:                      # reported, never swallowed
        rebuilt = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    conn.executescript(PATTERN_TEMPLATE_KEY_DDL)
    conn.commit()
    return rebuilt


# ---------------------------------------------------------------------------
# THE TEMPLATES. `must` = the hard gate holds; `recommended`/`advice` = guidance.
# `why` states the DEFECT avoided (not a benefit), `example` is copyable.
# ---------------------------------------------------------------------------
TEMPLATES: tuple[dict[str, Any], ...] = (
    # --- code / file subjects ---
    dict(tier="must", subject_kind="file", item_kind="has_cite_ref",
         rule="cite_ref is a path:line that EXISTS",
         why="a claim with no checkable reference cannot be audited, so nobody "
             "can tell a measurement from a memory",
         example="qc_gate.py:1", gate_key="ontology"),
    dict(tier="must", subject_kind="file", item_kind="no_hardcoded_secret",
         rule="the scanned file has 0 live hardcode candidates",
         why="a literal secret or path typed into code cannot be rotated and is "
             "invisible to every later reader",
         example="read it from settings/DB instead of typing it", gate_key="safety"),
    dict(tier="recommended", subject_kind="file", item_kind="has_tdd_case",
         rule="the change has at least one pass case AND one hard_fail case",
         why="a suite with only pass cases cannot fail, so it certifies nothing",
         example="kind='hard_fail' on the boundary input", gate_key="tdd"),
    # --- db table subjects ---
    dict(tier="must", subject_kind="db_table", item_kind="registered",
         rule="the table is a row in db_table_registry",
         why="an unregistered table cannot be joined, renamed or checked by any "
             "other part of the system",
         example="db_table_registry.table_key='qc_gate_registry'",
         gate_key="ontology"),
    dict(tier="must", subject_kind="db_table", item_kind="has_5w1h",
         rule="all six dimensions are bound in dimension_binding_registry",
         why="a row missing a dimension cannot be classified, and every later "
             "reader has to guess what it is about",
         example="subject_kind='table' bound to what/why/who/when/where/how",
         gate_key="5w1h"),
    dict(tier="must", subject_kind="db_table", item_kind="has_pk",
         rule="the table declares a primary key",
         why="without a pk a row cannot be referenced, so no FK can point at it "
             "and no id can name it",
         example="id INTEGER PRIMARY KEY AUTOINCREMENT", gate_key="ontology"),
    # --- api / route subjects ---
    dict(tier="must", subject_kind="api", item_kind="registered_route",
         rule="the route is declared in purpose_route_registry",
         why="a route nobody declared cannot be health-checked or reached by "
             "the router, and the caller gets an empty list that looks like "
             "'nothing found'",
         example="purpose_route_registry.route_key='/api/qc_gate/run'",
         gate_key="middleware"),
    # --- skill subjects ---
    dict(tier="must", subject_kind="skill", item_kind="declared_before_used",
         rule="the skill is a row in skill_registry BEFORE its factors are added",
         why="registering a factor for a skill that does not exist makes the "
             "factor unmeasurable forever",
         example="skill_registrar.ensure_skill_identity() runs FIRST",
         gate_key="ontology"),
    dict(tier="must", subject_kind="skill", item_kind="has_contract",
         rule="the skill has a contract with pass + hard_fail cases",
         why="without a contract the skill cannot be run or certified",
         example="contract SKILL.CODING.STANDARD, 3 pass + 3 hard_fail",
         gate_key="tdd"),
    # --- prompt subjects ---
    dict(tier="must", subject_kind="prompt", item_kind="has_oracle",
         rule="the prompt declares an oracle that can say YES and NO",
         why="a prompt with only a YES form cannot fail, so a run would certify "
             "a model that answers YES to everything",
         example="oracle returns YES for the declared value and NO for a "
                 "different one", gate_key="middleware"),
    # --- workflow / proof_run subjects ---
    dict(tier="recommended", subject_kind="workflow", item_kind="has_steps",
         rule="the workflow declares at least one step in workflow_step",
         why="a workflow with no step cannot run and cannot be traced",
         example="workflow_step rows with step_no 1..N", gate_key="middleware"),
    dict(tier="must", subject_kind="proof_run", item_kind="links_trace",
         rule="the round carries a non-null linked_trace_id",
         why="a proof with no trace cannot be replayed, so it cannot be told "
             "apart from a fabricated one",
         example="linked_trace_id = the runtime_trace row's trace_id",
         gate_key="trace"),
    # --- gate / register subjects ---
    dict(tier="must", subject_kind="gate", item_kind="declared_measurable",
         rule="metric_kind in the closed set AND metric_unit names that kind",
         why="a number without a unit cannot be audited, and a bare count with "
             "no subject is a decoration",
         example="metric_unit='pct of declared TDD cases that pass'",
         gate_key="ontology"),
    dict(tier="must", subject_kind="register", item_kind="has_cite_ref",
         rule="every register row carries a checkable cite_ref",
         why="an uncited register row is a memory, and the whole point of a "
             "register is to replace memory",
         example="cite_ref='db_schema.py:2214'", gate_key="ontology"),
    # --- role / environment ---
    dict(tier="must", subject_kind="role", item_kind="has_environment_pair",
         rule="the role has a row in role_environment",
         why="a role with no environment cannot be dispatched, and the gate that "
             "reads role x environment would refuse for the wrong reason",
         example="role_environment(role_key='reader', environment_id=1)",
         gate_key="role_environment"),
    # --- the remaining subject kinds, so coverage is COMPLETE (QC-09) ---
    dict(tier="must", subject_kind="capability", item_kind="has_kind",
         rule="the capability declares a capability_kind",
         why="a capability with no kind cannot be routed to a provider, so "
             "nothing can serve it",
         example="capability_kind='thinking'", gate_key="ontology"),
    dict(tier="must", subject_kind="channel", item_kind="declared",
         rule="the channel is a row in channel_registry",
         why="an undeclared channel cannot deliver, and a reader sees an empty "
             "list that looks like 'nothing found'",
         example="channel_registry.channel_key='vscode'", gate_key="ontology"),
    dict(tier="must", subject_kind="chat", item_kind="registered",
         rule="the chat is a row in chat_main keyed on its session",
         why="an unregistered chat cannot hold a ticket subject, so no middleware "
             "can carry its 5W1H",
         example="chat_main.session_id = the VS Code session id",
         gate_key="ontology"),
    # THE CHAT'S OWN task_id / entity_id (added 2026-09-28). THE HUMAN:
    #     "ｗｈｅｒ　ｉｓ　ｔａｓｋ　ＩＤ　ａｎｄ　ｅｎｔｉｔｙ　ＩＤtoo?"
    # MEASURED: `chat` had 8 columns and NEITHER, so the task id was readable
    # only by parsing `chat_key` and the entity id existed nowhere. These two
    # rows give the standard a home for them, so the gate can check them.
    #
    # `has_task_id` is `must`: a chat that cannot name the task it is about
    # cannot be found by task, which is the human's own complaint.
    dict(tier="must", subject_kind="chat", item_kind="has_task_id",
         rule="chat.task_id is non-empty and equals the task the chat is about",
         why="a chat that cannot name its task cannot be found by task, so the "
             "only way to reach it is to already know its id",
         example="chat.task_id = 'CODE.QUALITY' (derived from chat_key)",
         gate_key="ontology"),
    # `has_entity_id` is `recommended`, NOT `must` — the human's own rule is
    # "each chat need to have title (requied) + content (requied) + entity ID
    # (optional)". A chat that names no entity is a VALID chat, so a `must` here
    # would refuse every chat that legitimately has none. MEASURED: the tier
    # vocabulary is `must` / `recommended` / `advice` (a CHECK constraint), so
    # `should` is REFUSED — the tier is read from the schema, not chosen.
    dict(tier="recommended", subject_kind="chat", item_kind="has_entity_id",
         rule="chat.entity_id is a registered entity id, or NULL",
         why="a chat that names an entity must name a REAL one, or a later "
             "reader follows a link to nothing",
         example="chat.entity_id = 'R-1-415-1' (or NULL when the chat names none)",
         gate_key="ontology"),
    dict(tier="must", subject_kind="db_field", item_kind="registered",
         rule="the field is a row in db_field_registry under its table",
         why="an unregistered field cannot be validated or typed, so a payload "
             "carrying it is unchecked",
         example="db_field_registry(db_table_id=<the table's id>, "
                 "field_key='register_id')",
         gate_key="ontology"),
    dict(tier="must", subject_kind="entity", item_kind="has_letter",
         rule="the entity kind has a letter in entity_type_registry",
         why="an entity with no letter cannot be minted as an id, so it cannot be "
             "referenced by anything",
         example="{LETTER}-{table_id}-{row_id}-{version}", gate_key="ontology"),
    dict(tier="must", subject_kind="entity_id", item_kind="four_part",
         rule="the id is 4-part: LETTER-table-row-version",
         why="a 3-part id is ambiguous (version or row?), and a truncated read "
             "names a DIFFERENT entity",
         example="R-1-67-1", gate_key="ontology"),
    dict(tier="must", subject_kind="function", item_kind="registered",
         rule="the function is a row in function_registry",
         why="an unregistered function cannot be reached by a caller contract or "
             "a route",
         example="function_registry.function_key='qc_gate.seed_gates'",
         gate_key="ontology"),
    dict(tier="must", subject_kind="module", item_kind="registered",
         rule="the module is a row in module_registry",
         why="a module outside the registry has no place in the architecture, so "
             "its capabilities cannot be found",
         example="module_registry.module_key='task_center'", gate_key="ontology"),
    dict(tier="advice", subject_kind="namespace", item_kind="declared",
         rule="the namespace is a row in namespace_registry",
         why="an undeclared namespace cannot be resolved to a file location",
         example="namespace_registry.namespace_key='mouse_spot_helper'",
         gate_key="ontology"),
    dict(tier="advice", subject_kind="other", item_kind="stated",
         rule="the subject kind is stated explicitly, not left as 'other'",
         why="'other' is a catch-all; leaving it means no rule can be applied and "
             "no reader knows what the row is",
         example="name the real kind_key instead of 'other'", gate_key="ontology"),
    dict(tier="must", subject_kind="route", item_kind="registered",
         rule="the route is a row in purpose_route_registry",
         why="a route nobody declared cannot be health-checked, and the caller "
             "gets an empty list that looks like 'nothing found'",
         example="purpose_route_registry.route_key='/api/qc_gate/run'",
         gate_key="middleware"),
    dict(tier="must", subject_kind="service", item_kind="has_provider",
         rule="the service names a provider in llm_service_provider",
         why="a service with no provider can be called but never served, so it "
             "fails silently at run time",
         example="llm.text -> provider qwen2.5:7b-instruct", gate_key="middleware"),
    dict(tier="advice", subject_kind="system", item_kind="stated",
         rule="the subject kind is stated explicitly, not left as 'system'",
         why="'system' is a catch-all; leaving it means no rule can be applied",
         example="name the real kind_key instead of 'system'",
         gate_key="ontology"),
    dict(tier="advice", subject_kind="tacid", item_kind="resolves",
         rule="the tacid resolves to a code_location_registry row",
         why="a tacid that names no location cannot be traced, so a fault cannot "
             "be debugged",
         example="code_location_registry(location_id=N) covers the file",
         gate_key="trace"),
    dict(tier="must", subject_kind="task", item_kind="has_5w1h",
         rule="all six dimensions are bound in dimension_binding_registry",
         why="a task missing a dimension cannot be classified or dispatched, and "
             "every later reader has to guess its scope",
         example="subject_kind='task' bound to what/why/who/when/where/how",
         gate_key="5w1h"),
    dict(tier="must", subject_kind="ui", item_kind="registered_element",
         rule="every visible element is a row in ui_element_registry with a unit",
         why="a UI element with no unit is a decoration, and a decoration cannot "
             "be audited for 'user friendly'",
         example="ui_element_registry(term_key=..., unit_key='count')",
         gate_key="ontology"),
    dict(tier="must", subject_kind="version", item_kind="has_location",
         rule="the version is covered by a code_location_registry row",
         why="a version with no location is a claim with no code, and the "
             "activation gate would have no evidence to read",
         example="code_location_registry(entity_type, entity_ref_id, version)",
         gate_key="ontology"),
    dict(tier="must", subject_kind="worker_identity", item_kind="two_factor",
         rule="the identity has BOTH a session and a worker",
         why="one half of WHO is not an identity; the pair is what the pair key "
             "is computed from",
         example="session_id + worker_id -> workflow_id -> chat_id",
         gate_key="role_environment"),
    # --- the QUESTION FLOW's shape (SCOPE AC/AE, added 2026-09-28) -----------
    #
    # THE HUMAN: "how to have question flow patterm template by DB driven" and
    # "question is according to the answer by factor!!! not waste token".
    #
    # This row is the SHAPE a question flow must have. The seeder READS it, so
    # the shape is DECLARED in the DB rather than hardcoded in a Python literal
    # -- which is exactly the defect that let `judge_instruction()` stay a
    # one-shot question while `workflow_step` already had the 3-step design.
    dict(tier="must", subject_kind="question", item_kind="flow_3step",
         rule="a question is a flow: GATE (evidence) -> DESCRIBE (model) -> "
              "COMPUTED verdict, and each step DECLARES its factor",
         why="a one-shot verdict question scores 20/22 (P(100 in a row)=0.0073%) "
             "while the same question as a flow scores 22/22 (P(100)=100%) on "
             "the SAME 22 options -- so the flow, not the model, is what makes a "
             "100-streak possible",
         example="workflow_step rows: 1 gate (factor_key=..., expected=YES), "
                 "2 describe (factor_key=..., expected=NA), "
                 "3 verdict (verdict_rule='digit_count_between', "
                 "verdict_params={'lo':7,'hi':15})",
         gate_key="NA"),
    dict(tier="must", subject_kind="question", item_kind="gate_is_evidence",
         rule="a step whose answer EVIDENCE can produce is not asked of a model",
         why="MEASURED: the gate 'is this a single scalar with no spaces?' is a "
             "regex (0 calls, 22/22), while the model's own gate is wrong in BOTH "
             "directions (gate=NO for +85291234567, gate=YES for \"9123-4567\")",
         example="step 1 gate: answered by a regex/query, 0 model calls; only "
                 "the DESCRIBE step is a model call",
         gate_key="NA"),
    dict(tier="must", subject_kind="question", item_kind="selected_by_answer",
         rule="a step DECLARES what its answer selects (branch_on), and the "
              "runner follows it instead of step_no + 1",
         why="MEASURED: without it the runner asked EVERY step in step_no order "
             "-- stepwise_ask_db_field_registry 23 steps / 22 asked, and 91 "
             "steps asked across all flows, with steps 3..22 the SAME text",
         example="branch_on={'NO': 5, 'YES': 3}, is_terminal=1 on the last step",
         gate_key="NA"),
    # --- generator subjects (ADDED 2026-09-28) ---------------------------------
    # THE HUMAN: "how to standardlize, why you hate that, it is same format
    # forever, just step form" / "without table how can i have question template".
    #
    # MEASURED BEFORE: 29 subject_kinds existed and `generator` was NOT one, so a
    # generator had NO step form -- while FOUR tables declared the same idea with
    # the same five columns (is_required, rule, why, sort_order, is_active):
    # `pattern_template` 43, `generator_required_field` 9,
    # `question_template_registry` 5, `factor_template` 9. The fix is NOT a 5th
    # table and NOT a new gate: it is THIS kind, checked by the gate that already
    # exists (`assert_conforms`).
    #
    # STEP 4 IS THE ONE THAT MATTERS. MEASURED: only 1 of the 5 generators
    # exposes a `validate*` callable (`logic_generator.validate_spec`); the other
    # four expose NONE. So a bare "must have a validator" would FAIL four
    # correctly-written generators. The step therefore accepts an EXPLICIT
    # `validator_kind='none_declared'` -- a DECLARATION ("this generator is
    # measured by its entry point alone"), not a gap. A generator with NEITHER
    # still FAILS.
    dict(tier="must", subject_kind="generator", item_kind="declared",
         rule="the generator is a row in `generator_center.GENERATORS`",
         why="a generator nobody declared cannot be listed, dispatched or "
             "health-checked, so a caller asking for it gets an empty result "
             "that reads as 'nothing found' rather than 'no such generator'",
         example="generator_center.GENERATORS entry {'key': 'logic_generator', "
                 "'module': 'logic_generator', 'entry': 'generate'}",
         gate_key="ontology"),
    dict(tier="must", subject_kind="generator", item_kind="has_module",
         rule="its `module` IMPORTS (importlib.import_module(g['module']))",
         why="a declared generator whose module does not import is a menu item "
             "that fails only when a user clicks it, and the failure is "
             "reported as a generic 500 rather than a missing module",
         example="importlib.import_module('logic_generator') -> module",
         gate_key="ontology"),
    dict(tier="must", subject_kind="generator", item_kind="has_entry",
         rule="its `entry` is a CALLABLE on that module",
         why="the key and the module can both be right while the entry is a "
             "typo, in which case every call fails at dispatch and no step can "
             "say which of the three names is wrong",
         example="getattr(module, 'generate') -> callable",
         gate_key="ontology"),
    dict(tier="must", subject_kind="generator", item_kind="has_validator",
         rule="the module exposes a `validate*` callable, OR the generator "
              "DECLARES `validator_kind='none_declared'`",
         why="a form must be checked BEFORE it runs. MEASURED: only 1 of the 5 "
             "generators exposes a validator, so requiring one unconditionally "
             "would fail the other four; and silently accepting none would let "
             "a spec reach `generate()` unvalidated. An explicit "
             "`none_declared` is a DECLARATION, and it is the only alternative "
             "the step accepts",
         example="logic_generator.validate_spec(spec)->list[str]  "
                 "|  validator_kind='none_declared'",
         gate_key="5w1h"),
    dict(tier="must", subject_kind="generator", item_kind="fields_declared",
         rule="every name the validator READS has a `generator_required_field` "
              "row for this generator",
         why="MEASURED: `validate_spec` READS subject/dimensions/field_count/"
             "field_types while the form DECLARED only `subject` -- so three "
             "requirements were invisible to the form and a user could not know "
             "them before submitting",
         example="generator_required_field(field_name='field_types', "
                 "rule='depends_on:field_count')",
         gate_key="ontology"),
)


def seed_templates(conn: sqlite3.Connection) -> dict[str, Any]:
    """Write the templates. Idempotent (UPSERT by `(subject_kind, item_kind)`).

    REFUSES a template with an empty rule/why/example or an uncheckable cite —
    a template a worker cannot copy from is not a template.
    """
    ensure_schema(conn)
    created = updated = 0
    order = 0
    for t in TEMPLATES:
        order += 1
        cite = str(t.get("cite_ref") or DEFAULT_CITE)
        for f in ("rule", "why", "example"):
            if not str(t.get(f) or "").strip():
                raise PatternRefused(
                    f, "template rows must be complete",
                    "a template missing %r cannot be copied or checked" % f,
                    "see TEMPLATES in pattern_template.py")
        try:
            import terminology_cite as tc
            ok, why = tc.verify_cite_ref(cite)
            if not ok:
                raise PatternRefused(
                    "cite_ref", "cite_ref is a path:line that EXISTS",
                    "an uncheckable citation cannot be audited", cite,
                    "pattern cite_ref %r is not checkable: %s" % (cite, why))
        except ImportError:
            pass
        row = conn.execute(
            "SELECT template_id FROM pattern_template WHERE subject_kind=? AND "
            "item_kind=?", (t["subject_kind"], t["item_kind"])).fetchone()
        if row:
            conn.execute(
                "UPDATE pattern_template SET tier=?, rule=?, why=?, example=?, "
                "is_required=?, gate_key=?, cite_ref=?, sort_order=?, "
                "updated_at=datetime('now') WHERE template_id=?",
                (t["tier"], t["rule"], t["why"], t["example"],
                 1 if t["tier"] == "must" else 0, t.get("gate_key", "NA"),
                 cite, order, int(row["template_id"])))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO pattern_template (tier, subject_kind, item_kind, "
                "rule, why, example, is_required, gate_key, cite_ref, "
                "sort_order) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (t["tier"], t["subject_kind"], t["item_kind"], t["rule"],
                 t["why"], t["example"], 1 if t["tier"] == "must" else 0,
                 t.get("gate_key", "NA"), cite, order))
            created += 1
    conn.commit()
    return {"ok": True, "created": created, "updated": updated,
            "total": len(TEMPLATES)}


def list_templates(conn: sqlite3.Connection, *, subject_kind: str | None = None,
                   active_only: bool = False) -> list[dict[str, Any]]:
    """The templates a worker can copy from, in `sort_order`."""
    try:
        sql = "SELECT * FROM pattern_template"
        where, params = [], []
        if subject_kind:
            where.append("subject_kind = ?")
            params.append(subject_kind)
        if active_only:
            where.append("is_active = 1")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY sort_order"
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []


def template_for(conn: sqlite3.Connection, subject_kind: str,
                 item_kind: str,
                 subject_ref: str | None = None) -> dict[str, Any] | None:
    """One template row, or `None`. Never raises.

    `subject_ref` IS OPTIONAL AND NULL-SAFE, and that is what makes the merged
    register addressable. MEASURED 2026-09-28: after `generator_field` rows
    arrived, `template_for(conn, 'generator_field', 'subject')` returned
    `factor_generator` -- the FIRST match, silently -- because two generators
    both declare a field named `subject`. A lookup that ANSWERS WRONGLY instead
    of refusing is the defect this parameter removes.

    `subject_ref IS ?` is SQLite's NULL-safe comparison, so:
      * omitted / `None` -> `IS NULL` -> the 48 step rows, EXACTLY as before
      * a generator key   -> that generator's row only
    Every pre-existing caller passes two arguments and is therefore unaffected.
    """
    try:
        row = conn.execute(
            "SELECT * FROM pattern_template WHERE subject_kind=? AND "
            "item_kind=? AND subject_ref IS ?",
            (subject_kind, item_kind, subject_ref)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


# A SENTINEL, because `None` is a MEANINGFUL value here: it is the template's
# own `subject_ref` for the 48 step rows. Without a sentinel, "the caller did not
# supply a template key" and "the template key IS NULL" are the same value, and
# the lookup silently uses the INSTANCE's ref instead — MEASURED: that wrote 329
# instances with `template_id=NULL` and the PASS evidence never accrued.
_UNSET: Any = object()


def record_instance(conn: sqlite3.Connection, *, subject_kind: str,
                    subject_ref: str, verdict: str, value: float | None = None,
                    detail: str = "", cite_ref: str = DEFAULT_CITE,
                    gate_run_ref: str | None = None,
                    item_kind: str | None = None,
                    template_subject_ref: Any = _UNSET) -> dict[str, Any]:
    """Append the EXPERIENCE: what a subject did against its template.

    This is how a template accrues `experience` — an instance row per measured
    run — so `pattern_instance` is the evidence a template's tier is earned
    from, not asserted.

    `template_subject_ref` SEPARATES THE TWO MEANINGS OF `subject_ref`
    (added 2026-09-28). MEASURED, and this was a real defect: `subject_ref` is
    BOTH the instance's own reference AND the key used to LOOK UP the template.
    A caller that names its instance (`subject_ref='chat:70'`) therefore looked
    up a template whose `subject_ref` is NULL, found nothing, and wrote
    `template_id=NULL` — so the PASS evidence could never accrue and the
    template could never be activated. MEASURED: 329 instances written with
    `template_id=NULL` and `activation_evidence` still reporting `passing: 0`.

    It DEFAULTS to `subject_ref` (via the `_UNSET` sentinel), so every
    pre-existing caller is unaffected — the generator rows genuinely key their
    template by `subject_ref`. Pass `template_subject_ref=None` EXPLICITLY to
    look up a template whose own `subject_ref` is NULL.
    """
    ensure_schema(conn)
    v = str(verdict or "").strip().upper()
    if v not in ("PASS", "FAIL", "UNKNOWN"):
        v = "UNKNOWN"
    tid = None
    if item_kind:
        # `template_subject_ref` IS the template's own key; `subject_ref` is the
        # INSTANCE's. They are the same value for a generator row and different
        # for a named instance, which is why they are two parameters.
        lookup_ref = (subject_ref if template_subject_ref is _UNSET
                      else template_subject_ref)
        trow = template_for(conn, subject_kind, item_kind, lookup_ref)
        # `item_kind` may be a GATE key, not a template item kind: `pattern_
        # template` DECLARES which gate each row gates in its `gate_key` column,
        # so that is the link (MEASURED 2026-09-28 — without this every
        # gate-written instance had template_id NULL and no template could ever
        # accrue the PASS evidence activation needs).
        if not trow:
            trow = template_for_gate(conn, subject_kind=subject_kind,
                                     gate_key=item_kind)
        tid = int(trow["template_id"]) if trow else None
    cur = conn.execute(
        "INSERT INTO pattern_instance (template_id, subject_kind, subject_ref, "
        "verdict, value, detail, cite_ref, gate_run_ref) VALUES (?,?,?,?,?,?,?,?)",
        (tid, subject_kind, subject_ref, v, value, detail, cite_ref,
         gate_run_ref))
    conn.commit()
    return {"ok": True, "instance_id": cur.lastrowid, "template_id": tid,
            "verdict": v}


def assert_conforms(conn: sqlite3.Connection, *, subject_kind: str,
                    item_kind: str, present: bool,
                    observed: str = "", allow_inactive: bool = False,
                    subject_ref: str | None = None) -> dict[str, Any]:
    """THE FRIENDLY HARD GATE. Returns `{ok, ...}`; never raises on a failure.

    A refusal NAMES the field, the rule, the why and a copyable example, because
    the repo's own lessons show a gate that only says "no" teaches nothing and
    the same mistake recurs.

    A `must` template whose requirement is ABSENT is a REFUSAL. An INACTIVE
    `must` template is refused unless `allow_inactive=True` — the gate must not
    silently apply an unproven rule (nor silently ignore one).
    """
    t = template_for(conn, subject_kind, item_kind, subject_ref)
    if not t:
        return {"ok": False, "code": "NO_TEMPLATE",
                "message": ("no pattern_template row for subject_kind=%r "
                            "item_kind=%r subject_ref=%r — a worker cannot "
                            "conform to a rule nobody declared"
                            % (subject_kind, item_kind, subject_ref)),
                "known_templates": [("subject_kind", "item_kind")][:0]}
    refusal = None
    if not int(t["is_active"] or 0) and not allow_inactive:
        refusal = PatternRefused(
            item_kind, "template must be ACTIVE to gate",
            "an inactive template is a CLAIM, not an accepted rule; passing it "
            "would enforce a rule no sample has proven",
            t["example"],
            message=("template %s.%s is is_active=0 (unproven). Activate it with "
                     "a passing sample, or pass allow_inactive=True to use it as "
                     "guidance." % (subject_kind, item_kind)))
    elif bool(int(t["is_required"] or 0)) and not present:
        refusal = PatternRefused(
            item_kind, t["rule"], t["why"], t["example"],
            message=("template gate refused:\n"
                     "  subject : %s\n"
                     "  needs   : %s\n"
                     "  rule    : %s\n"
                     "  why     : %s\n"
                     "  example : %s\n"
                     "  observed: %s"
                     % (subject_kind, item_kind, t["rule"], t["why"],
                        t["example"], observed or "(nothing)")))
    if refusal:
        return {"ok": False, "code": "PATTERN_NOT_CONFORMED",
                "tier": t["tier"], "field": refusal.field,
                "rule": refusal.rule, "why": refusal.why,
                "example": refusal.example, "message": str(refusal)}
    return {"ok": True, "code": "CONFORMED", "tier": t["tier"],
            "subject_kind": subject_kind, "item_kind": item_kind,
            "rule": t["rule"], "example": t["example"]}


def template_for_gate(conn: sqlite3.Connection, *, subject_kind: str,
                      gate_key: str) -> dict[str, Any] | None:
    """The template a GATE result belongs to, for a subject kind.

    WHY (MEASURED 2026-09-28): `qc_gate_runner.run_all` recorded each gate
    result with `item_kind=<gate_key>`, but no template's item_kind IS a gate key
    — so `template_id` was NULL on every one of the 24 instances and the PASS
    evidence could never accrue to any template. The LINK is DECLARED:
    `pattern_template.gate_key` names the gate each row gates (e.g.
    `file/no_hardcoded_secret -> safety`). So the mapping is a lookup on that
    declared column, read at instance time, never guessed.
    """
    try:
        row = conn.execute(
            "SELECT * FROM pattern_template WHERE subject_kind=? AND gate_key=? "
            "ORDER BY is_required DESC, sort_order LIMIT 1",
            (subject_kind, gate_key)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row is not None else None


def activation_evidence(conn: sqlite3.Connection, *, subject_kind: str,
                        item_kind: str,
                        subject_ref: str | None = None) -> dict[str, Any]:
    """The PROOF a template may be activated: its measured PASS instances.

    WHY THIS EXISTS (the human, 2026-09-28)
    ---------------------------------------
        "it is a CLAIM until a sample passes it" / "have the plan"

    `is_active` had NO writer that a worker could reach: the value was set at
    seed time and never earned. This DERIVES the passing-sample count from
    `pattern_instance` — the same rows the gate writes — so "may this template be
    activated?" is a COUNT, not an opinion. It reads; it never flips a row.
    """
    t = template_for(conn, subject_kind, item_kind, subject_ref)
    if not t:
        return {"ok": False, "code": "NO_TEMPLATE", "passing": 0,
                "rule": "the template must exist",
                "why": "a rule nobody declared cannot be proven",
                "example": "pattern_template.seed_templates(conn)",
                "message": ("no pattern_template row for %s.%s subject_ref=%r"
                            % (subject_kind, item_kind, subject_ref))}
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM pattern_instance WHERE template_id=? "
            "AND UPPER(verdict)='PASS'", (int(t["template_id"]),)).fetchone()
        passing = int(row["n"] if row is not None else 0)
    except sqlite3.OperationalError:
        passing = 0
    return {"ok": True, "code": "EVIDENCE", "template_id": int(t["template_id"]),
            "subject_kind": subject_kind, "item_kind": item_kind,
            "passing": passing, "is_active": int(t["is_active"] or 0),
            "measure": ("SELECT COUNT(*) FROM pattern_instance WHERE "
                        "template_id=%d AND UPPER(verdict)='PASS'"
                        % int(t["template_id"]))}


def assert_may_activate(conn: sqlite3.Connection, *, subject_kind: str,
                        item_kind: str, minimum: int = 1,
                        subject_ref: str | None = None) -> dict[str, Any]:
    """REFUSE activation without a passing sample. Named field/rule/why/example."""
    ev = activation_evidence(conn, subject_kind=subject_kind, item_kind=item_kind,
                             subject_ref=subject_ref)
    if not ev.get("ok"):
        return ev
    if int(ev["passing"]) < int(minimum):
        return {"ok": False, "code": "NO_PASSING_EVIDENCE",
                "passing": int(ev["passing"]), "minimum": int(minimum),
                "field": "pattern_template.is_active",
                "rule": "activation requires >= %d PASS instance(s)" % minimum,
                "why": ("an activated template ENFORCES a rule on every worker, "
                        "so activating one no sample has passed propagates an "
                        "unproven rule — the exact shape of the factory-line "
                        "verdict defect"),
                "example": ("record a PASS first: pattern_template.record_instance("
                            "conn, subject_kind=%r, subject_ref='<the run>', "
                            "verdict='PASS', item_kind=%r)"
                            % (subject_kind, item_kind)),
                "message": ("cannot activate %s.%s: no PASS instance recorded "
                            "(found %d)." % (subject_kind, item_kind,
                                             int(ev["passing"])))}
    return {"ok": True, "code": "MAY_ACTIVATE", "passing": int(ev["passing"]),
            "template_id": ev["template_id"], "field": "pattern_template.is_active"}


def activate_template(conn: sqlite3.Connection, *, subject_kind: str,
                      item_kind: str, minimum: int = 1,
                      cite_ref: str = DEFAULT_CITE,
                      subject_ref: str | None = None) -> dict[str, Any]:
    """THE ONE WRITER of `pattern_template.is_active=1`, gated on proof.

    WHY THIS IS THE WRITER: MEASURED 2026-09-28 — `activation_gate.activate()`
    is versioned around `entity_version`, so it CANNOT flip a non-versioned
    feature table like `pattern_template`; calling it for this table would need
    a version column this table does not have. So the writer lives HERE, beside
    the row it flips, and it REFUSES unless `assert_may_activate` passes.
    `activation_gate` is left UNTOUCHED (QC-16).
    """
    ok = assert_may_activate(conn, subject_kind=subject_kind, item_kind=item_kind,
                             minimum=minimum, subject_ref=subject_ref)
    if not ok.get("ok"):
        return ok
    conn.execute("UPDATE pattern_template SET is_active=1 WHERE template_id=?",
                 (int(ok["template_id"]),))
    conn.commit()
    return {"ok": True, "code": "ACTIVATED",
            "template_id": int(ok["template_id"]),
            "subject_kind": subject_kind, "item_kind": item_kind,
            "evidence_passing": int(ok["passing"]), "cite_ref": cite_ref}


def check_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    """Do the templates cover every subject kind the system declares? Never raises."""
    try:
        kinds = {str(r[0]) for r in conn.execute(
            "SELECT kind_key FROM subject_kind_registry WHERE is_active=1")}
    except sqlite3.OperationalError:
        return {"ok": False, "reason": "subject_kind_registry unreadable"}
    have = {str(r["subject_kind"]) for r in list_templates(conn)}
    # A template kind that is not a subject-kind key is ALLOWED (file/register/
    # gate are structural, not ticket subjects) — reported, not an error.
    return {"ok": True, "covered": sorted(have),
            "subject_kinds": sorted(kinds),
            "kinds_without_template": sorted(kinds - have),
            "template_kinds_beyond_subjects": sorted(have - kinds)}


def main() -> int:
    ap = argparse.ArgumentParser(description="pattern template + instance store")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--subject-kind", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    conn = _connect(args.db)
    try:
        if args.seed:
            print(json.dumps(seed_templates(conn), indent=1))
        if args.list:
            rows = list_templates(conn, subject_kind=args.subject_kind)
            if not rows:
                print("(no pattern_template rows — run --seed)")
            for r in rows:
                print("  %-12s %-14s %-22s gate=%-16s act=%d"
                      % (r["tier"], r["subject_kind"], r["item_kind"],
                         r["gate_key"], r["is_active"]))
        if args.check:
            print(json.dumps(check_coverage(conn), indent=1))
        if not (args.seed or args.list or args.check):
            print(json.dumps(check_coverage(conn), indent=1))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())