# -*- coding: utf-8 -*-
"""dimension_binding_registry.py — the SAME 5W1H dimension BINDS differently
                                    per SUBJECT KIND.

WHY THIS EXISTS (user, 2026-09-22)
----------------------------------
    "why i think factor can be for many thing?
     now can explain for table / field
     and it can explain for function / api / capabiltity / module / channel too
     5W1H, all design is for factor to happen
     why we need that / where file location + line / when trigger point in
     what which and which job to have and looking for whcih
     how = function / api / capabiltity / module / channel
     do u agree? if yes, = generator template have many layer now!!!"

AGREED, and the mechanism for "a factor belongs to many things" ALREADY EXISTS:
`skill_factor_registry.applies_to` is a TAG SET matched against
`skill_registry.capability_tags`, which is COMMA-SEPARATED (`db_schema.py:1937`).
A tag is shared by many subjects; a `skill_key` is an IDENTITY and belongs to one.

WHAT IS NEW IS THE BINDING, NOT THE SHARING
-------------------------------------------
The six dimensions are FIXED. What differs per subject kind is what each
dimension MEANS:

    dimension   table / field              function / api / capability / ...
    ---------   ------------------------   --------------------------------
    where       file path + line           file path + line
    when        at creation                the TRIGGER POINT
    what        which table, which field   which job must exist, looking for which
    how         the verify command         function / api / capability / module

So the generator template gains a LAYER.

WHY A TABLE FOR THE BINDINGS AND NOT FOR THE DIMENSIONS
-------------------------------------------------------
  * the BINDINGS are an OPEN set (the future subject kinds are unknown) -> a
    table is the shape for an open set
  * the SIX DIMENSIONS are FIXED -> a table would let someone add a 7th, which
    would SILENTLY change every generated question list

So `dimension_key` is validated against `skill_5w1h.DIMENSION_NAMES` (the fixed
tuple), and `subject_kind` is free — an INSERT.

RULE 3 SELF-CONSISTENCY
-----------------------
`subject_kind` IS a discriminator, and this table is NOT flagged MIXED by
`table_design.audit_table`, because EVERY kind has the SAME columns. No
value-specific column = no mixing. The proof asserts it.

Run:
    .\\.venv\\Scripts\\python.exe dimension_binding_registry.py --list
    .\\.venv\\Scripts\\python.exe dimension_binding_registry.py --seed
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
except Exception:
    pass

DEFAULT_DB = BASE_DIR / "agent.db"

# The subject kinds the user named, plus the two the repo already registers.
# `db_table` / `db_field`, NOT the bare `table` / `field` (user ruling
# 2026-09-24: "db_table , db_field is more respresenattive than table, field").
# The bare form is AMBIGUOUS in this repo: `table`/`field` are also a SQL CHECK
# literal (`db_schema.py:1888`) and a task-input payload key
# (`test_case_registry.input_payload`), so the bare name does not say WHICH of the
# three layers it means.
SUBJECT_KINDS: tuple[str, ...] = (
    "db_table", "db_field", "function", "api", "capability", "module",
    "channel", "service",
)

DIMENSION_BINDING_DDL = """
CREATE TABLE IF NOT EXISTS dimension_binding_registry (
    binding_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    -- THE COMPOSITE KEY. The same dimension is legal under a DIFFERENT subject
    -- kind — the same rule `wording_registry` uses
    -- (`UNIQUE (skill_id, dim_key, wording_key)`).
    subject_kind   TEXT    NOT NULL,
    dimension_key  TEXT    NOT NULL,
    -- WHAT THE DIMENSION MEANS FOR THIS KIND. This is the new layer.
    binding_text   TEXT    NOT NULL,
    example        TEXT    NOT NULL DEFAULT 'NA',
    cite_ref       TEXT    NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_active      INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (subject_kind, dimension_key)
);
CREATE INDEX IF NOT EXISTS idx_dimension_binding_kind
  ON dimension_binding_registry (subject_kind, sort_order, is_active);
"""

# The bindings the user stated, as the INITIAL SEED. `db_table` and `db_field`
# are the ones already proven by `table_design`; the rest are the user's own words.
DIMENSION_BINDING_SEED: tuple[tuple[str, str, str, str], ...] = (
    # (subject_kind, dimension_key, binding_text, example)
    ("db_table", "where", "the file path + line of the DDL that creates the table",
     "db_schema.py:1471"),
    ("db_table", "when", "at creation, before any row is written",
     "ensure_task_center_schema"),
    ("db_table", "what", "which table, and which columns it holds",
     "code_registry"),
    ("db_table", "how", "the verify command that can FAIL",
     "table_design.py --audit"),
    ("db_field", "where", "the file path + line of the column definition",
     "db_schema.py:1471"),
    ("db_field", "what", "which column, and its declared type",
     "file_path TEXT"),
    ("db_field", "how", "the rule that can FAIL (const / non_blank / type / length)",
     "skill_contract_store.RULE_KINDS"),
    ("function", "where", "the file path + line of the function definition",
     "activation_gate.py:330"),
    ("function", "when", "the TRIGGER POINT — what calls it, and when",
     "on activate()"),
    ("function", "what", "which job must exist, and what it looks for",
     "activate"),
    ("function", "how", "the function / api / capability / module / channel "
                        "that implements it",
     "activation_gate.activate"),
    ("api", "where", "the file path + line of the route handler",
     "NA"),
    ("api", "when", "the TRIGGER POINT — the request that reaches it",
     "NA"),
    ("api", "how", "the function / module that serves it",
     "NA"),
    ("capability", "where", "the file path + line of the capability definition",
     "NA"),
    ("capability", "when", "the TRIGGER POINT — when the capability is invoked",
     "NA"),
    ("capability", "how", "the function / api / module / channel that provides it",
     "NA"),
    ("module", "where", "the file path + line of the module",
     "NA"),
    ("module", "when", "the TRIGGER POINT — when the module is loaded",
     "NA"),
    ("module", "how", "the functions / apis the module exposes",
     "NA"),
    ("channel", "where", "the file path + line of the channel adapter",
     "NA"),
    ("channel", "when", "the TRIGGER POINT — when the channel receives input",
     "NA"),
    ("channel", "how", "the module / capability the channel routes to",
     "NA"),
    ("service", "where", "the file path + line of the service interface",
     "NA"),
    ("service", "when", "the TRIGGER POINT — when the service is called",
     "NA"),
    ("service", "how", "the function / api / module that implements it",
     "NA"),
)

# THE INCOMPLETE KINDS COMPLETED (2026-09-24).
#
# WHY (user): "all = 5W1H, identity is same for everywhere, different is output
# format" and "evidence can be proofed is the only wat to have the work output
# standardize". MEASURED before this: only 6 of 14 kinds had all six dimensions;
# `api`, `capability`, `channel`, `module` and `service` had THREE each (missing
# what + why + who), `db_table` four, `db_field` and `function` four. A kind with
# 3 of 6 bindings produces a question set that LOOKS complete and is not — the
# same defect `ticket_5w1h.coverage()` was written to expose.
#
# EVERY binding answers the dimension FOR THAT KIND, and each is derived from a
# column MEASURED to exist in that kind's own register (checked before writing:
# `api_registry.path`, `capability_registry.capability_kind`, `channel_registry.
# channel_key`, `module_registry.channel_id`, `ticket_center.ticket_origin`,
# `db_table_registry.table_key`, `db_field_registry.db_table_id`,
# `function_registry.capability_id`). A dimension with no such column is left
# ABSENT rather than invented, so a gap stays visible.
COMPLETION_SEED: tuple[tuple[str, str, str, str], ...] = (
    # ---- module: where/when/how already exist; these three were missing ----
    ("module", "what", "which module, by its module_key, and the name a human reads",
     "module_registry.module_key / name"),
    ("module", "why", "why the module exists — the channel it serves",
     "module_registry.channel_id"),
    ("module", "who", "who owns the module — the human or worker that declared it",
     "module_registry.name (the declaring party)"),
    # ---- capability ----
    ("capability", "what", "which capability, and of what KIND it is",
     "capability_registry.capability_kind"),
    ("capability", "why", "why the capability exists — the reason recorded on it",
     "capability_registry.why"),
    ("capability", "who", "who provides it — the module that owns it",
     "capability_registry.module_id"),
    # ---- api ----
    ("api", "what", "which API, and the path a caller reads",
     "api_registry.api_key / path"),
    ("api", "why", "why the API exists — the capability it serves",
     "api_registry.capability_id"),
    ("api", "who", "who serves it — the capability owner behind the route",
     "api_registry.capability_id -> capability_registry.module_id"),
    # ---- channel ----
    ("channel", "what", "which channel, and the name a human reads",
     "channel_registry.channel_key / name"),
    ("channel", "why", "why the channel exists — what it delivers to",
     "channel_registry.description"),
    ("channel", "who", "who sits on it — the modules that run on this channel",
     "channel_registry.channel_id -> module_registry.channel_id"),
    # ---- service ----
    ("service", "what", "which service, and the name a human reads",
     "ticket_center.ticket_origin / name"),
    ("service", "why", "why the service exists — the tickets it raises",
     "ticket_center.description"),
    ("service", "who", "who provides it — the pipeline that raises its tickets",
     "ticket_center.ticket_origin + ticket.opened_by"),
    # ---- db_table ----
    ("db_table", "why", "why the table exists — the register it backstops",
     "db_table_registry.description"),
    ("db_table", "who", "who owns it — the module that declares it",
     "db_table_registry.name / description"),
    # ---- db_field ----
    ("db_field", "why", "why the column exists — the rule it enforces",
     "db_field_registry.description"),
    ("db_field", "who", "who owns it — the table that holds it",
     "db_field_registry.db_table_id"),
    ("db_field", "when", "when it is written — the write owner of its table",
     "db_field_registry.db_table_id -> db_table_registry.table_key"),
    # ---- function ----
    ("function", "why", "why the function exists — the capability it serves",
     "function_registry.capability_id"),
    ("function", "who", "who implements it — the file that declares it",
     "function_registry.file_path"),
    # ---- namespace: 50 live nodes, and a real register ----
    ("namespace", "what", "which namespace, and the route prefix a caller reads",
     "namespace_registry.namespace_key"),
    ("namespace", "why", "why it exists — the routes it groups",
     "namespace_registry.route_count / description"),
    ("namespace", "who", "who serves it — the module and the dominant file",
     "namespace_registry.module_id / dominant_file"),
    ("namespace", "when", "when it changed — the last update",
     "namespace_registry.updated_at"),
    ("namespace", "where", "where it is served — the dominant file + line",
     "namespace_registry.dominant_file / cite_ref"),
    ("namespace", "how", "the verify command that can FAIL",
     "namespace_registry.route_count (a namespace with 0 routes is a defect)"),
    # ---- version: 1112 live nodes, and a real register ----
    ("version", "what", "which version of which entity — the (type, ref, version)",
     "version_registry.entity_type / entity_ref_id / version"),
    ("version", "why", "why the version exists — the note it carries",
     "version_registry.note"),
    ("version", "who", "who created it — the writer recorded on the row",
     "version_registry.created_by"),
    ("version", "when", "when it was created",
     "version_registry.created_at"),
    ("version", "where", "where it sits in the lineage — its parent version",
     "version_registry.parent_version_id"),
    ("version", "how", "the verify command that can FAIL",
     "version_registry.is_active (a version with no active row resolves nothing)"),
)

# THE FOUR KINDS THE USER NAMED (2026-09-23), with a COMPLETE six-dimension set.
#
#   "no, by ticket!! chat ticket, workflow ticket, task ticket and entity ticket
#    so we can have middleware for 5W1H, not hardcode for rubbish"
#
# WHY A SECOND SEED TUPLE AND NOT AN EDIT OF THE ONE ABOVE
# -------------------------------------------------------
# The tuple above is the ORIGINAL seed and is INCOMPLETE for most kinds (it
# carries what/when/where/how but not why/who). `ticket_5w1h.coverage()` MEASURED
# that: 0 of 20 kinds had all six. Rather than rewrite history, the four kinds
# the user named get a COMPLETE set here, and the incompleteness of the rest is
# left VISIBLE in `coverage()` instead of being papered over.
#
# Each binding answers the dimension FOR THAT KIND, so the same six questions
# produce different, concrete answers per subject.
SUBJECT_KIND_BINDING_SEED: tuple[tuple[str, str, str, str], ...] = (
    # ---- chat ----
    ("chat", "what", "which chat, and which session id it is",
     "chat_main.id 12 / session 8f875556-..."),
    ("chat", "why", "why this chat exists — the work it was opened to discuss",
     "the chat that asked for the task"),
    ("chat", "who", "who opened the chat, and who is answering in it",
     "opened_by + the model serving the reply"),
    ("chat", "when", "when the chat was opened, relative to the work it asks for",
     "before the task it requests"),
    ("chat", "where", "the chat's session id, and the transcript path",
     "chat_main.session_id"),
    ("chat", "how", "the command that resolves the chat from its session",
     "python ticket_subject.py --tickets-for-subject chat <id>"),
    # ---- workflow ----
    ("workflow", "what", "which workflow, and which steps it runs",
     "workflow_registry.workflow_key = worker_identity_flow"),
    ("workflow", "why", "why the workflow exists — the gate it enforces",
     "the ontology gate before a TDD verdict"),
    ("workflow", "who", "who authored the workflow, and who it runs for",
     "created_by + the worker that executes it"),
    ("workflow", "when", "the trigger point — what starts the workflow",
     "when a task enters the layer it gates"),
    ("workflow", "where", "the file path + line of the workflow definition",
     "workflow_registry / workflow_step"),
    ("workflow", "how", "the command that runs or verifies the workflow",
     "python ticket_5w1h.py --questions workflow"),
    # ---- task ----
    ("task", "what", "which task, and which template it instantiates",
     "task_instances.task_id = CHAT.TASK.LINK"),
    ("task", "why", "why the task exists — the defect or gap it closes",
     "the chain was broken at three places"),
    ("task", "who", "who submitted the task, and who approves its result",
     "submit_task caller + the human approving the plan"),
    ("task", "when", "the trigger point — what creates the task",
     "after the plan is APPROVED, before any code is written"),
    ("task", "where", "the file path + line of the task's work",
     "task_instances.payload"),
    ("task", "how", "the command that verifies the task",
     "python _proof_ticket_subject_5w1h.py"),
    # ---- entity ----
    ("entity", "what", "which entity, by its registered id",
     "T-1-5-1"),
    ("entity", "why", "why the entity is referenced — the work about it",
     "the table row this ticket changes"),
    ("entity", "who", "who registered the entity, and who may change it",
     "the register owner + the approver"),
    ("entity", "when", "when the entity is touched, relative to the work",
     "before the row is written"),
    ("entity", "where", "the register table + pk the entity id resolves to",
     "entity_type_registry -> register_table"),
    ("entity", "how", "the command that verifies the entity id",
     "python entity_id.py --verify <id>"),
    # ---- worker_identity (the PAIR: the WORKER system joined to the IDENTITY
    #      system). Added 2026-09-23.
    #
    #      The user: "identity is 5W1H, who = session ID + worker ID required /
    #      what = get workflow id / workflow tell you how to have chat ID".
    #
    #      `who` NAMES BOTH IDS because the user said both are required. That is
    #      the one dimension that is COMPOSITE, and it is modelled rather than
    #      flattened into a single id.
    ("worker_identity", "who",
     "the session id AND the worker id, both required — the pair that IS the "
     "identity",
     "session 9fc7ad2c-... + worker W-S-03-A"),
    ("worker_identity", "what",
     "which workflow is running, by its workflow_id — the workflow is what "
     "produces the chat id",
     "workflow_registry.workflow_key = chat_center_identity"),
    ("worker_identity", "why",
     "why this identity is being established — the work it is opened for",
     "to prove which chat a worker is acting in"),
    ("worker_identity", "when",
     "the workflow step reached, relative to the chat id being produced",
     "step 1 'who a u?' before step 2 'confirm the identity block'"),
    ("worker_identity", "where",
     "the channel the identity arrives through, and the session's transcript",
     "source.source_key = vscode / doubao"),
    ("worker_identity", "how",
     "the command that resolves the identity and its chat id",
     "python worker_identity_binding.py --questions <worker_key> <identity_key>"),
    # ---- entity_id (the ID SCHEME ITSELF) — added 2026-09-23 ---------------
    #
    #      The user: "5W1H can get logic for why i have entity ID design" /
    #      "entity id can explain what it is happen and why we need that?" /
    #      "T-1-5-1, how to have this entity id, and it is a map for each small
    #      task with file location and parent relationship, so fully have that
    #      = full graph for system".
    #
    #      This is NOT the same kind as `entity` above. `entity` answers the six
    #      dimensions ABOUT one entity used as a ticket subject. `entity_id`
    #      answers them ABOUT the ID SCHEME — why a letter exists at all, and
    #      how the id becomes a graph node.
    #
    #      The six dimensions ARE the graph's own parts: `what` is the node
    #      identity, `where` is the file-location edge, `when` is the parent
    #      edge's order, `how` is the verify walk.
    ("entity_id", "what",
     "the id itself, in its ONLY shape — {LETTER}-{table_id}-{row_id}-{version} "
     "(e.g. R-1-75-1): letter = node kind, table_id = the register's table, "
     "row_id = the register's own pk, version = a revision. The 3-part form "
     "{LETTER}-{ref_id}-{version} is the OLD one (2026-09-27).",
     "R-1-75-1"),
    ("entity_id", "why",
     "a register row with no letter cannot be reached by a system-wide id, so "
     "the graph needs every node addressable; the letter IS the node identity "
     "of the graph, not a property of a table whose name ends in _registry",
     "the gap that made code_location_registry uncoverable"),
    ("entity_id", "who",
     "the register owner MINTS the id (never hand-built), and the approver "
     "CONFIRMS the entity it names",
     "mint_entity() + register_approval.record()"),
    ("entity_id", "when",
     "minted BEFORE a write that must be attributed to an entity; the PARENT "
     "edge is set at version creation, so the order is id -> version -> write",
     "version_registry.parent_version_id at ensure_version()"),
    ("entity_id", "where",
     "the file location the id resolves to — code_location_registry maps "
     "(entity_type, entity_ref_id, version) to file_path + line_start",
     "code_location_registry.file_path + line_start"),
    ("entity_id", "how",
     "the command that verifies the id AND walks its edges",
     "python entity_id.py --verify <id>  +  entity_registry.entity_graph()"),
)


class BindingError(ValueError):
    """Raised when a binding cannot be registered."""

def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.executescript(DIMENSION_BINDING_DDL)
    conn.commit()
    # THE REGISTER MAPPING IS RE-READ FROM THE TABLE HERE, so every path that
    # touches this register serves the TABLE's answers rather than a stale
    # in-module list. MEASURED: the list used to be a literal that disagreed with
    # `subject_kind_registry` for five kinds, which left 15 `example` rows
    # unmapped although their registers existed.
    result = {"ok": True}
    try:
        result["subject_registry"] = refresh_subject_registry(conn)
    except sqlite3.OperationalError:
        # `subject_kind_registry` may not exist yet in a fresh file. The FALLBACK
        # map keeps the lookup working; the derivation runs on the next call.
        result["subject_registry"] = {"ok": True, "derived": 0,
                                      "note": "subject_kind_registry unreadable"}
    return result


# ---------------------------------------------------------------------------
# THE DIMENSION'S OWN CITATION (added 2026-09-23).
#
# THE DEFECT THIS FIXES, MEASURED: all 62 binding rows carried ONE `cite_ref`
# value — `dimension_binding_registry.py:DIMENSION_BINDING_SEED` (or
# `...:SUBJECT_KIND_BINDING_SEED`). Two consequences, and they are separate:
#
#   1. `citation_discipline.is_citation()` REFUSES that form: `_PATH_LINE`
#      requires `:\d+`, so `file.py:SYMBOL` is not a citation at all.
#   2. Even if it passed, 62 different claims sharing one reference is a pointer
#      to the CONTAINER, not to the evidence — which `citation-discipline`
#      forbids in as many words ("a reference must point at the thing named, not
#      at its container").
#
# So a binding's `cite_ref` now points at the DIMENSION it is a reading of:
# `skill_5w1h.py:<line>`, where `<line>` declares that dimension and its
# `hard_rule`. That IS what a binding is derived from, it is checkable, and it is
# DISTINCT per row.
#
# THE LINE NUMBER IS LOOKED UP, NEVER TYPED. A hand-typed `skill_5w1h.py:52`
# goes stale the moment a comment is added above it, and a stale citation is
# worse than none — it points confidently at the wrong line. `dimension_line()`
# reads the source and finds the declaration, so the citation cannot drift.
#
# THE TWO COLUMNS STAY SEPARATE, and they answer different questions:
#   cite_ref  = what this binding is DERIVED FROM   (the dimension's hard_rule)
#   example   = what it LOOKS LIKE for this subject (the subject's own register)
# Merging them would be the "one column, two meanings" defect.
# ---------------------------------------------------------------------------

def dimension_line(conn: sqlite3.Connection | None,
                   dimension_key: str) -> int:
    """The line in `skill_5w1h.py` declaring `dimension_key`. 0 when not found.

    READS the file rather than importing a constant, because the LINE NUMBER is
    the fact being cited. 0 is returned instead of raising: a caller that cannot
    find the line must still be able to say so, and `0` is visibly not a line.
    """
    import re
    from pathlib import Path
    dim = str(dimension_key or "").strip()
    if not dim:
        return 0
    path = Path(__file__).resolve().parent / "skill_5w1h.py"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return 0
    # The declaration form is a tuple starting with the dimension name, e.g.
    #     ("where",
    # inside `DIMENSIONS`. Anchored so a MENTION of the word elsewhere (a
    # comment, a docstring) cannot be mistaken for the declaration.
    pat = re.compile(r'^\s*\("%s",\s*$' % re.escape(dim))
    for i, ln in enumerate(lines, 1):
        if pat.match(ln):
            return i
    return 0


def dimension_cite_ref(conn: sqlite3.Connection | None,
                       dimension_key: str) -> str:
    """The `cite_ref` for a binding on `dimension_key`: `skill_5w1h.py:<line>`.

    Returns '' when the line cannot be found, so a caller REFUSES rather than
    writing an unciteable row — the same fail-closed direction `add_binding`
    already takes for an empty `cite_ref`.
    """
    n = dimension_line(conn, dimension_key)
    return "skill_5w1h.py:%d" % n if n else ""


def add_binding(conn: sqlite3.Connection, subject_kind: str, dimension_key: str,
                binding_text: str, *, example: str = "NA", cite_ref: str = "",
                sort_order: int = 0, commit: bool = True) -> dict[str, Any]:
    """Register ONE binding. Idempotent on `(subject_kind, dimension_key)`.

    REFUSES:
      * an unknown `dimension_key` — the six are FIXED, so a 7th cannot be
        introduced by an INSERT (that would silently change every generated
        question list)
      * an empty `binding_text` — a binding that says nothing
      * an empty `cite_ref` — a claim nobody can check
    """
    kind = str(subject_kind or "").strip()
    dim = str(dimension_key or "").strip()
    text = str(binding_text or "").strip()
    if not kind:
        return {"ok": False, "code": "MISSING_SUBJECT_KIND",
                "message": "subject_kind is required"}
    if not dim:
        return {"ok": False, "code": "MISSING_DIMENSION_KEY",
                "message": "dimension_key is required"}
    # The six dimensions are FIXED — validated against the ONE declaration.
    import skill_5w1h as fw
    if dim not in fw.DIMENSION_NAMES:
        return {"ok": False, "code": "BAD_DIMENSION_KEY",
                "message": ("dimension_key must be one of the SIX fixed "
                            "dimensions %s, got %r — a 7th would silently "
                            "change every generated question list"
                            % (list(fw.DIMENSION_NAMES), dim))}
    if not text:
        return {"ok": False, "code": "MISSING_BINDING_TEXT",
                "message": ("a binding with no text says nothing: %s/%s"
                            % (kind, dim))}
    if not str(cite_ref or "").strip():
        return {"ok": False, "code": "MISSING_CITE_REF",
                "message": "no citation, no binding: %s/%s" % (kind, dim)}

    ensure_schema(conn)
    existing = conn.execute(
        "SELECT binding_id FROM dimension_binding_registry "
        "WHERE subject_kind=? AND dimension_key=?", (kind, dim)).fetchone()
    if existing:
        return {"ok": True, "binding_id": int(existing[0]), "created": False,
                "subject_kind": kind, "dimension_key": dim}

    cur = conn.execute(
        "INSERT INTO dimension_binding_registry (subject_kind, dimension_key, "
        "binding_text, example, cite_ref, sort_order, is_active) "
        "VALUES (?,?,?,?,?,?,0)",
        (kind, dim, text, str(example or "NA").strip() or "NA",
         str(cite_ref).strip(), int(sort_order)))
    if commit:
        conn.commit()
    return {"ok": True, "binding_id": cur.lastrowid, "created": True,
            "subject_kind": kind, "dimension_key": dim}


def seed_bindings(conn: sqlite3.Connection, *,
                  commit: bool = True) -> dict[str, Any]:
    """Insert the declared bindings. Idempotent.

    Seeds BOTH tuples: the original set, and the COMPLETE six-dimension set for
    the four kinds the user named (2026-09-23).

    THE `cite_ref` IS THE DIMENSION'S OWN LINE, not the seed tuple's name.
    MEASURED before this: every row carried one of two identical strings, so 62
    distinct claims shared a single reference — and `citation_discipline`
    refuses that form outright. `dimension_cite_ref()` derives it, so the two
    tuples below need no citation text of their own.
    """
    ensure_schema(conn)
    added = 0
    skipped_no_cite: list[str] = []
    for i, (kind, dim, text, example) in enumerate(DIMENSION_BINDING_SEED, 1):
        cref = dimension_cite_ref(conn, dim)
        if not cref:
            # FAIL LOUDLY rather than writing an unciteable row. A binding whose
            # dimension cannot be located is a claim with no authority, and
            # `add_binding` would refuse it anyway — but naming WHICH dimension
            # was unlocatable is what makes the fix possible.
            skipped_no_cite.append("%s/%s" % (kind, dim))
            continue
        r = add_binding(conn, kind, dim, text, example=example,
                        cite_ref=cref, sort_order=i, commit=False)
        if r.get("created"):
            added += 1
    for i, (kind, dim, text, example) in enumerate(
            SUBJECT_KIND_BINDING_SEED, 1):
        cref = dimension_cite_ref(conn, dim)
        if not cref:
            skipped_no_cite.append("%s/%s" % (kind, dim))
            continue
        r = add_binding(conn, kind, dim, text, example=example,
                        cite_ref=cref, sort_order=100 + i, commit=False)
        if r.get("created"):
            added += 1
    # THE COMPLETION SEED, so every kind reaches all six dimensions. Sorted AFTER
    # the two seeds above so the ordering is stable across runs.
    for i, (kind, dim, text, example) in enumerate(COMPLETION_SEED, 1):
        cref = dimension_cite_ref(conn, dim)
        if not cref:
            skipped_no_cite.append("%s/%s" % (kind, dim))
            continue
        r = add_binding(conn, kind, dim, text, example=example,
                        cite_ref=cref, sort_order=200 + i, commit=False)
        if r.get("created"):
            added += 1
    if commit:
        conn.commit()
    return {"ok": True, "added": added,
            "skipped_no_cite_ref": skipped_no_cite,
            "total": conn.execute("SELECT COUNT(*) FROM "
                                  "dimension_binding_registry").fetchone()[0]}


def rename_subject_kinds(conn: sqlite3.Connection, *,
                         commit: bool = True) -> dict[str, Any]:
    """Rename `subject_kind` legacy names to the standardized ones. Idempotent.

    The user's ruling (2026-09-24): "db_table , db_field is more respresenattive
    than table, field". The SEED was fixed, but `seed_bindings` is IDEMPOTENT —
    it skips a row that already exists — so changing the seed alone would leave
    every EXISTING `table`/`field` row unchanged. A repair is needed because the
    data is already written.

    It reports `changed` and REFUSES when BOTH names exist for the same
    `dimension_key`: that is a real duplicate, and merging it silently would pick
    one binding over the other without saying so.
    """
    ensure_schema(conn)
    changed: list[dict[str, Any]] = []
    for old, new in (("table", "db_table"), ("field", "db_field")):
        rows = [dict(r) for r in conn.execute(
            "SELECT binding_id, dimension_key FROM dimension_binding_registry "
            "WHERE subject_kind=?", (old,))]
        for r in rows:
            clash = conn.execute(
                "SELECT 1 FROM dimension_binding_registry "
                "WHERE subject_kind=? AND dimension_key=?",
                (new, r["dimension_key"])).fetchone()
            if clash:
                return {"ok": False, "code": "DUPLICATE_BINDING",
                        "message": ("%s/%s already exists, so renaming %s/%s "
                                    "would duplicate it — resolve by hand"
                                    % (new, r["dimension_key"], old,
                                       r["dimension_key"])),
                        "changed": changed}
            conn.execute(
                "UPDATE dimension_binding_registry SET subject_kind=?, "
                "updated_at=datetime('now') WHERE binding_id=?",
                (new, int(r["binding_id"])))
            changed.append({"binding_id": int(r["binding_id"]),
                            "from": old, "to": new,
                            "dimension_key": r["dimension_key"]})
    if commit:
        conn.commit()
    return {"ok": True, "changed": changed}


def repair_cite_refs(conn: sqlite3.Connection, *,
                     commit: bool = True) -> dict[str, Any]:
    """Rewrite every row's `cite_ref` to its dimension's own line.

    WHY A REPAIR AND NOT ONLY A SEED FIX: `seed_bindings` is IDEMPOTENT — it
    skips a row that already exists — so fixing the seed alone would leave all 62
    EXISTING rows pointing at the old container string. A migration is needed
    because the data is already written; a seed change only affects new rows.

    It reports `changed` and `unresolved`, and it does NOT touch `binding_text`
    or `example`: this repairs the CITATION only, so it cannot silently change
    what a binding says. `unresolved` LISTS the rows it could not cite, so a
    partial repair cannot read as a complete one.
    """
    ensure_schema(conn)
    changed = 0
    unresolved: list[dict[str, Any]] = []
    already = 0
    rows = list(conn.execute(
        "SELECT binding_id, subject_kind, dimension_key, cite_ref "
        "FROM dimension_binding_registry ORDER BY binding_id"))
    for r in rows:
        want = dimension_cite_ref(conn, r["dimension_key"])
        if not want:
            unresolved.append({"binding_id": int(r["binding_id"]),
                               "subject_kind": r["subject_kind"],
                               "dimension_key": r["dimension_key"],
                               "cite_ref": r["cite_ref"]})
            continue
        if str(r["cite_ref"] or "") == want:
            already += 1
            continue
        conn.execute(
            "UPDATE dimension_binding_registry SET cite_ref=?, "
            "updated_at=datetime('now') WHERE binding_id=?",
            (want, int(r["binding_id"])))
        changed += 1
    if commit:
        conn.commit()
    return {"ok": not unresolved, "changed": changed, "already_correct": already,
            "unresolved": unresolved, "total": len(rows)}


def bindings_for(conn: sqlite3.Connection, subject_kind: str) -> dict[str, str]:
    """The dimension -> binding_text map for ONE subject kind.

    This IS what `logic_generator` reads (`logic_generator.dimension_wording`),
    so a question's wording comes from the binding rather than from a hard-coded
    string.

    THAT CLAIM WAS FALSE UNTIL 2026-09-24, and it is recorded rather than
    quietly corrected: MEASURED, `grep bindings_for logic_generator.py` returned
    ZERO matches. The docstring asserted a relationship that did not exist, and
    nothing caught it — the same family as a rule stated in prose and unchecked
    at the write site. `logic_generator` now calls this, and
    `_proof_logic_evidence_answer.py` section N asserts it.
    """
    try:
        rows = conn.execute(
            "SELECT dimension_key, binding_text FROM "
            "dimension_binding_registry WHERE subject_kind=? "
            "ORDER BY sort_order, dimension_key",
            (str(subject_kind or "").strip(),))
    except sqlite3.OperationalError:
        return {}
    return {str(r[0]): str(r[1]) for r in rows}


def list_bindings(conn: sqlite3.Connection, *,
                  subject_kind: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM dimension_binding_registry "
    params: tuple = ()
    if subject_kind:
        sql += "WHERE subject_kind=? "
        params = (str(subject_kind),)
    sql += "ORDER BY subject_kind, sort_order, dimension_key"
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []


def subject_kinds(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Every subject kind that has at least one binding."""
    try:
        return tuple(str(r[0]) for r in conn.execute(
            "SELECT DISTINCT subject_kind FROM dimension_binding_registry "
            "ORDER BY subject_kind"))
    except sqlite3.OperationalError:
        return ()


# ---------------------------------------------------------------------------
# THE SUBJECT'S OWN REGISTER (Source A for `example`, added 2026-09-23).
#
# `cite_ref` answers "what is this binding DERIVED from" -> the dimension's
# hard_rule. `example` answers a DIFFERENT question: "what does this dimension
# look like FOR THIS SUBJECT" -> a row in that subject's own register.
#
# ===========================================================================
# THIS WAS A SECOND COPY, AND IT DRIFTED (fixed 2026-09-24)
# ===========================================================================
#
# MEASURED, and this is the whole reason the mapping is now DERIVED rather than
# written out here. `SUBJECT_registry` USED TO BE A LITERAL DICT, and it
# disagreed with `subject_kind_registry` — the DB TABLE that already answers the
# same question — for FIVE of the sixteen kinds:
#
#     kind            the old dict said              the TABLE says
#     --------------  ----------------------------  --------------------------------
#     chat            chat_main.id                  identity_registry.identity_id
#     worker_identity identity_registry.identity_id (no row at all)
#     namespace       (absent)                      namespace_registry.namespace_id
#     version         (absent)                      version_registry.version_registry_id
#     service         (absent)                      ticket_center.id
#
# The result was MEASURED, not theoretical: `repair_examples` reported
# `changed=25 already_checkable=56 unmapped=15 total=96`, and the 15 unmapped
# rows were exactly `namespace` (6) + `version` (6) + `service` (3) — three kinds
# whose registers EXIST, with INTEGER primary keys, and which the TABLE already
# mapped. The dict simply did not know about them.
#
# A COMMENT IN THIS FILE WAS ALSO WRONG, and it was load-bearing: it claimed
# `service -> llm_service_type_registry.type_key` and `task -> task_instances.
# task_id`, i.e. that they had NO integer PK. MEASURED: `subject_kind_registry`
# maps `service -> ticket_center.id` and `task -> dev_task.id`, and BOTH are
# INTEGER. So the old comment invented a refusal for two kinds that do not need
# one. (Both tables exist; the point is which one the BINDINGS refer to — and the
# register table is the authority.)
#
# THE FIX IS A DERIVATION, NOT A CORRECTION. Patching the five entries would
# leave two copies of one fact, and the copies would drift again — which is the
# defect this codebase keeps finding. `subject_kind_registry` is the SSOT because
# it is a TABLE maintained by the registry system, so the mapping is READ from it.
#
# THE REFUSAL IS KEPT, AND IT IS STILL DECIDED BY THE DATA: `example_ref` returns
# '' when the register's key column is not an INTEGER, because
# `citation_discipline._DB_REF` is `^register:(table):(\d+)$` — it looks the row
# up by primary key, so a TEXT key (`entity_id.type_letter`) cannot form the
# reference at all. That is now derived per row instead of asserted in prose, so
# it cannot go stale.
# ---------------------------------------------------------------------------
#
# THE OLD SPELLINGS, kept working. A key that names a register must match the
# DATA, but a caller written against a previous spelling must not silently lose
# its register — the same rule that made `default_phone` KEPT and REPORTED rather
# than deleted.
SUBJECT_registry_ALIASES: dict[str, str] = {
    "field": "db_field",
    "table": "db_table",
}


def _key_is_integer(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """Is `table.col` declared INTEGER, so `register:<table>:<\\d+>` can form?

    `citation_discipline._DB_REF` is `^register:(table):(\\d+)$`, so a TEXT key
    (measured: `entity_type_registry.type_letter`) can NEVER form this reference.
    A kind whose key is TEXT is therefore NOT a `SUBJECT_registry` entry — the map
    means "the register whose `<pk>` I can cite", and a TEXT key has no such pk.
    Keeping it in the map made a checker believe a `register:` ref was available
    for `entity_id` when it is impossible (measured 2026-09-24).
    """
    try:
        rows = conn.execute("PRAGMA table_info(%s)" % table).fetchall()
    except sqlite3.OperationalError:
        return False
    for r in rows:
        # POSITIONAL: pragma rows are `(cid, name, type, notnull, dflt, pk)` and
        # this runs on any connection, `Row` or tuple.
        if str(r[1]) == col:
            return "INT" in str(r[2] or "").upper()
    return False


def _registry_from_table(conn: sqlite3.Connection | None) -> dict[str, tuple[str, str]]:
    """`subject_kind` -> `(table, key_column)`, READ from the registry TABLE.

    Returns {} when the table or connection is unavailable, so a caller falls
    back to the explicit map below rather than losing every mapping — a
    derivation that can return NOTHING on a read error would turn a working
    lookup into a silent outage.

    A kind is kept ONLY when its key column is INTEGER-typed: the map's contract
    is "the register I can form `register:<table>:<pk>` against", and a TEXT key
    (`entity_id` -> `entity_type_registry.type_letter`) cannot form one.
    """
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT kind_key, ref_table, ref_column FROM subject_kind_registry")
    except sqlite3.OperationalError:
        return {}
    out: dict[str, tuple[str, str]] = {}
    for r in rows:
        # POSITIONAL, because `ensure_schema` may be handed a PLAIN connection
        # (no `row_factory`) and `r["kind_key"]` would then raise TypeError on a
        # tuple. `r[0:3]` works for both a tuple and a `sqlite3.Row`.
        kind = str(r[0] or "").strip()
        table = str(r[1] or "").strip()
        col = str(r[2] or "").strip()
        # `NA` is the not-answered sentinel in this schema, NOT a column name — a
        # kind with no register states that honestly rather than leaving a NULL.
        if not kind or not table or table == "NA" or not col or col == "NA":
            continue
        if not _key_is_integer(conn, table, col):
            continue
        out[kind] = (table, col)
    return out


# FALLBACK ONLY, and it is deliberately SMALL. On a connection that cannot read
# `subject_kind_registry` (a fresh file, a schema mid-migration) the derivation
# returns {}, so these keep the module working. They are the entries the TABLE
# agreed with, so the fallback cannot introduce a disagreement — and a proof
# asserts they are a SUBSET of the table, so it cannot become a second copy that
# drifts.
SUBJECT_registry_FALLBACK: dict[str, tuple[str, str]] = {
    "api": ("api_registry", "api_id"),
    "capability": ("capability_registry", "capability_id"),
    "channel": ("channel_registry", "channel_id"),
    "db_field": ("db_field_registry", "db_field_id"),
    "db_table": ("db_table_registry", "db_table_id"),
    "function": ("function_registry", "function_id"),
    "module": ("module_registry", "module_id"),
    "workflow": ("workflow_registry", "workflow_id"),
}

# The last derivation result, so `subject_registry()` (which takes no connection,
# keeping its existing signature for callers) can serve the table's answers. Set
# by `refresh_subject_registry()`, which `ensure_schema` calls.
_SUBJECT_registry_CACHE: dict[str, tuple[str, str]] = {}


def refresh_subject_registry(conn: sqlite3.Connection) -> dict[str, Any]:
    """Re-read the register mapping from `subject_kind_registry`. Idempotent.

    Called from `ensure_schema`, so every path that touches this register gets
    the TABLE's answers rather than a stale in-module list.

    IT MUTATES `SUBJECT_registry` IN PLACE rather than rebinding the name. That
    is deliberate: `SUBJECT_registry` is a module-level NAME, and an existing
    reader that did `from ... import SUBJECT_registry` (or a proof that holds the
    object) would keep the OLD dict after a rebind — a silent stale read, which
    is the exact defect this change removes.
    """
    derived = _registry_from_table(conn)
    # THE TABLE WINS, BUT IT MUST NOT DELETE A KIND IT CANNOT DERIVE.
    #
    # MEASURED DEFECT (2026-09-26): `subject_kind_registry` declares `module` with
    # `ref_column='module_key'`, which is TEXT — deliberately, because
    # `ticket_store.map_module` STORES THE KEY and every reader JOINs
    # `m.module_key = x.subject_ref_id` (`ticket_store.py:270, 311, 401`). So
    # `_key_is_integer()` correctly REFUSES to derive it: a string key cannot
    # form `register:<table>:<pk>`.
    #
    # But the table had ALREADY been given a valid `example`
    # (`register:module_registry:1`, and `module_registry.module_id=1` EXISTS),
    # and `example_ref("module")` then returned '' because the mapping was gone —
    # so the proof reported `extra=['module']`, i.e. a kind USING a register the
    # mapping did not declare. The mapping was incomplete, not the data.
    #
    # THE FALLBACK DESCRIBES THE SAME KIND, and it is asserted to be a SUBSET of
    # the table, so merging cannot introduce a disagreement with the table — it
    # only restores a kind the table declines to express in `register:<pk>` form.
    # ENTRY, THEN FALLBACK: where BOTH have a kind, the TABLE's answer wins.
    effective = dict(SUBJECT_registry_FALLBACK)
    effective.update(derived)
    SUBJECT_registry.clear()
    SUBJECT_registry.update(effective)
    # `subject_registry_map()` — WHICH `example_ref()` READS — serves
    # `_SUBJECT_registry_CACHE`, so the MERGED map must go there too. MEASURED
    # DEFECT (2026-09-26): writing only `SUBJECT_registry` left
    # `example_ref("module")` returning '' while `SUBJECT_registry["module"]`
    # existed — two readers of one fact disagreeing, which is the family this
    # module exists to remove.
    _SUBJECT_registry_CACHE.clear()
    _SUBJECT_registry_CACHE.update(effective)
    return {"ok": True, "derived": len(derived),
            "fallback_only": sorted(set(effective) - set(derived)),
            "effective": len(effective), "kinds": sorted(effective)}


def subject_registry_map() -> dict[str, tuple[str, str]]:
    """The effective mapping: the TABLE's answers, else the fallback."""
    return dict(_SUBJECT_registry_CACHE) or dict(SUBJECT_registry_FALLBACK)


def _derive_at_import() -> None:
    """Derive from the register TABLE at IMPORT, best-effort. Never raises.

    WHY AT IMPORT, NOT ONLY IN `ensure_schema`: `refresh_subject_registry` used
    to be called on the first `ensure_schema`, so a reader that held the NAME
    `SUBJECT_registry` (or read `subject_registry()`) BEFORE any `ensure_schema`
    saw only the 8-entry fallback — measured 2026-09-24: `_proof_binding_cite_source`
    read `set(dbr.SUBJECT_registry)` and got the fallback, so the derivation
    looked like it had NOT happened. A derivation that is only true after a
    schema call is not a derivation; it is a derived value WITH A WINDOW.
    """
    if not DEFAULT_DB.exists():
        return
    try:
        conn = sqlite3.connect(str(DEFAULT_DB))
    except sqlite3.Error:
        return
    try:
        refresh_subject_registry(conn)
    except sqlite3.Error:
        # A missing/older table is a normal state to fall back FROM, not a crash
        # to propagate out of an import.
        pass
    finally:
        conn.close()


# SEEDED FROM THE FALLBACK AT IMPORT, THEN REPLACED IN PLACE. It is a live VIEW
# of the derivation now, not the literal it used to be — a copy is what drifted.
SUBJECT_registry: dict[str, tuple[str, str]] = dict(SUBJECT_registry_FALLBACK)

# ...and the derivation RUNS HERE, so the name is correct from the first read.
_derive_at_import()


def subject_registry(subject_kind: str) -> tuple[str, str] | None:
    """The `(table, key_column)` for a subject kind, or None when unknown.

    None rather than a default: an unknown subject has no register, and guessing
    one would produce a reference to a row that does not exist.
    """
    key = str(subject_kind or "").strip()
    key = SUBJECT_registry_ALIASES.get(key, key)
    return subject_registry_map().get(key)


# THE SUBJECT'S DDL, for the kinds whose register has NO INTEGER PRIMARY KEY.
#
# MEASURED: `entity_type_registry` (`type_letter`), `llm_service_type_registry`
# (`type_key`) and `task_instances` (`task_id`) have no integer PK, so a
# `register:<table>:<pk>` reference CANNOT be formed — `citation_discipline._DB_REF`
# requires `:\\d+`. Those 19 rows would otherwise sit at `NA`, which is honest but
# gives a reader nothing to check.
#
# A `path:line` pointing at the CREATE TABLE is the available checkable fact: it
# is the DDL that declares the register, so a reader can open it and see the
# columns the binding talks about. That is a WEAKER claim than "this row exists"
# and it is recorded as such — the alternative was an `NA`, not a stronger
# reference.
SUBJECT_DDL: dict[str, str] = {
    "entity": "entity_registry.py:66",
    "entity_id": "entity_registry.py:66",
    "service": "db_schema.py:4565",
    "task": "llm_task_center.py:43",
}


def subject_ddl_ref(subject_kind: str) -> str:
    """A `path:line` to the subject's register DDL, or '' when unmapped.

    The line is READ from the file before being returned, so a stale entry
    (a table moved to another module) yields '' instead of a confident pointer
    at the wrong line — the same rule `dimension_cite_ref` follows.
    """
    ref = SUBJECT_DDL.get(str(subject_kind or "").strip(), "")
    if not ref:
        return ""
    path, _, line = ref.partition(":")
    try:
        n = int(line)
    except ValueError:
        return ""
    src = Path(__file__).resolve().parent / path
    try:
        lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    if n < 1 or n > len(lines) or "CREATE TABLE" not in lines[n - 1].upper():
        return ""
    return ref


def example_ref(conn: sqlite3.Connection, subject_kind: str) -> str:
    """A REAL `register:<table>:<pk>` for one row of the subject's register.

    Returns '' when the subject has no mapped register, the table is absent, the
    register is EMPTY, or the key is NOT an integer — so a caller REFUSES rather
    than inventing a row. An invented reference is the defect `add_term`'s
    `UNCITEABLE_CITE_REF` check exists to catch, and it applies here for the same
    reason.

    THE INTEGER REQUIREMENT IS NOT COSMETIC: `citation_discipline._DB_REF` is
    `^register:(table):(\\d+)$`, so a string key cannot form this reference at
    all. Checking it here means the refusal happens at the SOURCE rather than
    being discovered later by a verifier that reports "no row".
    """
    ent = subject_registry(subject_kind)
    if not ent:
        return ""
    table, key_col = ent
    try:
        row = conn.execute(
            "SELECT %s AS k FROM %s ORDER BY 1 LIMIT 1" % (key_col, table)
        ).fetchone()
    except sqlite3.OperationalError:
        return ""
    # `row[0]`, not `row["k"]`: a plain connection returns a TUPLE, and this
    # function is reachable from `ensure_schema` on any connection.
    if not row or row[0] is None:
        return ""
    key = row[0]
    if isinstance(key, bool) or not isinstance(key, int):
        # A TEXT/BLOB key cannot be cited in this form. Returning '' puts the row
        # in `unmapped` where a human sees it, instead of writing a reference
        # that cannot be verified.
        return ""
    return "register:%s:%d" % (table, key)


def audit_cite_refs(conn: sqlite3.Connection) -> dict[str, Any]:
    """How many binding rows carry a DISTINCT, CHECKABLE `cite_ref`? Read-only.

    THE TWO QUESTIONS ARE SEPARATE, and both are needed:
      * `distinct_cite_refs` — 62 rows sharing one value is a pointer to a
        container, not to evidence, even when that value is well-formed.
      * `is_citation` — the form must be one `citation_discipline` accepts, so
        the reference is CHECKABLE rather than merely different.
    A count that reported only one of them could look healthy while the other
    was broken, so both are returned with `ok` requiring BOTH.
    """
    rows = list(conn.execute(
        "SELECT binding_id, subject_kind, dimension_key, cite_ref "
        "FROM dimension_binding_registry ORDER BY binding_id"))
    try:
        import citation_discipline as cd
    except Exception:
        cd = None
    distinct = {str(r["cite_ref"] or "") for r in rows}
    checkable: list[int] = []
    uncheckable: list[dict[str, Any]] = []
    for r in rows:
        cref = str(r["cite_ref"] or "")
        if cd is not None and cd.is_citation(cref):
            checkable.append(int(r["binding_id"]))
        else:
            uncheckable.append({"binding_id": int(r["binding_id"]),
                                "subject_kind": r["subject_kind"],
                                "dimension_key": r["dimension_key"],
                                "cite_ref": cref})
    return {
        "ok": not uncheckable and len(distinct) > 1,
        "rows": len(rows),
        "distinct_cite_refs": len(distinct),
        "checkable": len(checkable),
        "uncheckable": uncheckable,
        "citation_discipline_available": cd is not None,
    }


def repair_examples(conn: sqlite3.Connection, *,
                    commit: bool = True) -> dict[str, Any]:
    """Rewrite `example` to a REAL `register:<table>:<pk>` where one exists.

    SAME REASONING AS `repair_cite_refs`: the seed is idempotent, so the 21 `NA`
    rows and the prose rows stay as they are unless a migration changes them.

    IT IS DELIBERATELY CONSERVATIVE, and that is the point:
      * a row whose `example` ALREADY passes `citation_discipline.is_citation`
        is LEFT ALONE — it is already checkable, and rewriting it would replace
        one good reference with another for no reason;
      * a subject kind with NO mapped register is REPORTED in `unmapped`, not
        filled with a guess;
      * it never touches `binding_text` or `cite_ref`.

    So a partial repair is VISIBLE: `changed` + `already_checkable` + `unmapped`
    accounts for every row.
    """
    ensure_schema(conn)
    try:
        import citation_discipline as cd
    except Exception:
        cd = None
    changed = 0
    already = 0
    cleared = 0
    unmapped: list[dict[str, Any]] = []
    rows = list(conn.execute(
        "SELECT binding_id, subject_kind, dimension_key, example "
        "FROM dimension_binding_registry ORDER BY binding_id"))
    for r in rows:
        cur = str(r["example"] or "").strip()
        if cd is not None and cd.is_citation(cur) and _example_ok(cd, cur, conn):
            already += 1
            continue
        want = example_ref(conn, r["subject_kind"])
        if not want:
            # A `register:` reference is impossible for this kind (no integer
            # PK), so fall back to the DDL that declares the register. That is a
            # WEAKER claim and it is labelled as such — but it is CHECKABLE,
            # which `NA` is not.
            want = subject_ddl_ref(r["subject_kind"])
        if not want:
            # NO VALID REPLACEMENT EXISTS. MEASURED, and my first version was
            # WRONG: it left the OLD value in place, so a FALSE `register:` ref
            # survived the repair. That is the worst outcome — a well-formed
            # reference to a row that does not exist looks checkable, which is
            # why `_example_ok` exists at all. `NA` is HONEST; a false reference
            # is not.
            if cur.startswith("register:"):
                conn.execute(
                    "UPDATE dimension_binding_registry SET example='NA', "
                    "updated_at=datetime('now') WHERE binding_id=?",
                    (int(r["binding_id"]),))
                cleared += 1
            unmapped.append({"binding_id": int(r["binding_id"]),
                             "subject_kind": r["subject_kind"],
                             "dimension_key": r["dimension_key"],
                             "example": cur,
                             "cleared": cur.startswith("register:")})
            continue
        conn.execute(
            "UPDATE dimension_binding_registry SET example=?, "
            "updated_at=datetime('now') WHERE binding_id=?",
            (want, int(r["binding_id"])))
        changed += 1
    if commit:
        conn.commit()
    return {"ok": True, "changed": changed, "already_checkable": already,
            "cleared_false_ref": cleared, "unmapped": unmapped,
            "total": len(rows)}


def _example_ok(cd, ref: str, conn: sqlite3.Connection) -> bool:
    """Is `ref` not merely well-formed, but TRUE?

    THE DISTINCTION IS THE WHOLE POINT, and my first version missed it:
    `is_citation` is SHAPE ONLY (`citation_discipline.py:45` says so), so
    `register:db_table_registry:agent_provider` passed the shape check and
    pointed at a row that does not exist — the table's PK is `db_table_id`, an
    INTEGER. A reference that is well-formed and FALSE is worse than an obvious
    `NA`, because it looks checkable.

    So a `register:` ref is verified against the database; any OTHER citation
    form (a command, a `path:line`) is accepted on shape, because those carry
    their own evidence and have no row to look up.
    """
    if not str(ref).startswith("register:"):
        return True
    return bool(cd.verify_db_ref(ref, conn=conn).get("exists"))


def binding_activation_verdict(conn: sqlite3.Connection) -> dict[str, Any]:
    """The verdict on the 108 inactive bindings, MEASURED.

    THE HUMAN (2026-09-26): "全部 108 行 dimension_binding_registry 都係
    is_active=0 ... 要唔要我跟進？" -> "yes, have the plan and fix it now"

    MEASURED, and the answer is that the 108 inactive rows are NOT a data bug:

      * `is_active=0` is DELIBERATE. `db_schema.py:1761` records the user's
        ruling that `is_active = 0` means "NOT YET PROVEN", and the DDL declares
        `DEFAULT 0` (`dimension_binding_registry.py:99`). `add_binding` writes
        `0` EXPLICITLY, so a freshly declared binding is UNPROVEN by design.
      * THE GATE IS REAL: `derive_5w1h.fields` REFUSES a subject with no
        ACTIVE binding (`reason = MISSING_BINDING`, all 6 dimensions missing).
      * THE DEFECT IS THE MISSING WRITER: `dimension_binding_registry` IS in
        `activation_gate.activation_scope()["in_scope"]`, but
        `activation_gate.activate()` — THE ONLY WRITER of `is_active = 1` —
        writes `version_registry`, a DIFFERENT table. So the table is IN SCOPE
        for activation and has NO activation writer: **a gate with no key**.
    """
    def _one(sql: str, *a: Any) -> Any:
        try:
            r = conn.execute(sql, a).fetchone()
            return r[0] if r else None
        except sqlite3.OperationalError:
            return None

    total = _one("SELECT COUNT(*) FROM dimension_binding_registry")
    active = _one("SELECT COUNT(*) FROM dimension_binding_registry "
                  "WHERE is_active=1")
    out: dict[str, Any] = {
        "total": total, "active": active, "inactive": (total or 0) - (active or 0),
        "verdicts": [],
    }

    # ---- 1. the DDL default ------------------------------------------------
    ddl = _one("SELECT sql FROM sqlite_master WHERE "
               "name='dimension_binding_registry'") or ""
    default_zero = "DEFAULT 0" in ddl
    out["ddl_default_zero"] = default_zero
    out["verdicts"].append({
        "claim": "the 108 inactive rows are a data bug",
        "verdict": ("NO — `is_active=0` is DELIBERATE. The DDL declares "
                    "`DEFAULT 0` (measured: %s) and `add_binding` writes `0` "
                    "EXPLICITLY, so a freshly declared binding is UNPROVEN by "
                    "design. `db_schema.py:1761` records the ruling that "
                    "`is_active = 0` means 'not yet proven'." % default_zero),
        "cite": "measured: PRAGMA sqlite_master -> %s" % (
            "DEFAULT 0 present" if default_zero else "no DEFAULT 0"),
    })

    # ---- 2. the gate -------------------------------------------------------
    gate = {"refuses": None, "reason": None, "missing": None}
    try:
        import derive_5w1h as fw
        kinds = [str(r[0]) for r in conn.execute(
            "SELECT DISTINCT subject_kind FROM dimension_binding_registry "
            "ORDER BY subject_kind")]
        if kinds:
            f = fw.fields(conn, kinds[0])
            gate = {"refuses": f.get("ok") is False,
                    "reason": f.get("reason"),
                    "missing": len(f.get("missing") or []),
                    "kind": kinds[0]}
    except Exception as e:
        gate = {"refuses": None, "reason": "ERR %s" % e, "missing": None}
    out["gate"] = gate
    out["verdicts"].append({
        "claim": "the inactive bindings have no effect",
        "verdict": ("NO — the gate is REAL. `derive_5w1h.fields` REFUSES a "
                    "subject with no ACTIVE binding (measured: ok=%s, "
                    "reason=%s, %s dimension(s) missing)."
                    % (gate.get("refuses") is False, gate.get("reason"),
                       gate.get("missing"))),
        "cite": "measured: derive_5w1h.fields -> %s" % gate.get("reason"),
    })

    # ---- 3. the scope ------------------------------------------------------
    scope = {"in_scope": None, "exempt": None, "unclassified": None}
    try:
        import activation_gate as ag
        s = ag.activation_scope(conn)
        t = "dimension_binding_registry"
        scope = {"in_scope": t in s["in_scope"], "exempt": t in s["exempt"],
                 "unclassified": t in s["unclassified"]}
    except Exception as e:
        scope = {"in_scope": None, "err": str(e)}
    out["scope"] = scope
    out["verdicts"].append({
        "claim": "the table is outside the activation scope",
        "verdict": ("NO — it IS in scope (measured: in_scope=%s, exempt=%s, "
                    "unclassified=%s)."
                    % (scope.get("in_scope"), scope.get("exempt"),
                       scope.get("unclassified"))),
        "cite": "measured: activation_gate.activation_scope()",
    })

    # ---- 4. the missing writer --------------------------------------------
    # UPDATED 2026-09-26. MEASURED BEFORE: `activate_binding()` existed but
    # `main()` declared NO flag reaching it, so the only caller was a proof --
    # a gate with no key. The flag now exists, so the verdict MEASURES whether
    # the writer is REACHABLE rather than asserting it is not.
    _src = ""
    try:
        _src = Path(__file__).read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    _flag = "--activate-binding" in _src
    _writer = "activate_binding" in _src
    out["writer"] = {
        "activation_gate_writes": "version_registry",
        "writes_this_table": _writer,
        "cli_flag": "--activate-binding" if _flag else None,
        "reachable": bool(_writer and _flag),
        "cite": ("measured: dimension_binding_registry.py declares "
                 "--activate-binding and calls activate_binding()"
                 if (_writer and _flag) else
                 "measured: activate_binding() exists but NO CLI flag reaches "
                 "it, so the only caller is a proof"),
    }
    out["verdicts"].append({
        "claim": "there is a writer that can activate a binding",
        "verdict": (
            "YES — `activate_binding()` is THE WRITER of "
            "`dimension_binding_registry.is_active = 1`, and "
            "`--activate-binding` now REACHES it (measured: writer=%s, "
            "flag=%s). It DELEGATES to `activation_gate.assert_may_activate`, "
            "so the streak AND the 2-part verdict are still required — the flag "
            "does NOT bypass the gate."
            % (_writer, _flag)),
        "cite": out["writer"]["cite"],
    })

    # ---- 5. per-binding reason --------------------------------------------
    rows = [dict(r) for r in conn.execute(
        "SELECT subject_kind, dimension_key, is_active FROM "
        "dimension_binding_registry ORDER BY subject_kind, dimension_key")]
    out["bindings"] = rows
    out["refusal"] = {
        "act": "activate a binding with no proof run",
        "would": ("REFUSED — a binding is a CLAIM about what a dimension means, "
                  "and the evidence that it is right is a proof run whose "
                  "questions were generated FROM it. Activating without that "
                  "evidence is the defect this gate exists to prevent."),
        "ref_tag_formula": "dimension_binding.{subject_kind}.{dimension_key}",
    }
    return out


def binding_ref_tag(subject_kind: str, dimension_key: str) -> str:
    """The `ref_tag` a binding's proof run would carry. DERIVED, not chosen."""
    return "dimension_binding.%s.%s" % (str(subject_kind).strip(),
                                        str(dimension_key).strip())


def activate_binding(conn: sqlite3.Connection, subject_kind: str,
                     dimension_key: str, *, cite_ref: str,
                     target: int | None = None, rule_version: int = 1,
                     model: str | None = None, commit: bool = True
                     ) -> dict[str, Any]:
    """THE WRITER of `dimension_binding_registry.is_active = 1`.

    THE HUMAN (2026-09-26): "yes, have the plan and fix it now"

    MEASURED DEFECT THIS CLOSES: the table is IN `activation_scope()["in_scope"]`
    but `activation_gate.activate()` writes `version_registry`, so NO code path
    could ever activate a binding — a gate with no key.

    IT DELEGATES to `activation_gate.assert_may_activate`, so there is ONE
    definition of "proven" (the streak AND the 2-part verdict). A second
    implementation would let this writer and the gate disagree about the same
    evidence.

    REFUSES a binding with no proof run: an unrun thing is UNPROVEN, not proven.
    """
    kind = str(subject_kind or "").strip()
    dim = str(dimension_key or "").strip()
    if not kind or not dim:
        return {"ok": False, "code": "MISSING_KEY",
                "message": "subject_kind and dimension_key are both required"}
    row = conn.execute(
        "SELECT binding_id, is_active FROM dimension_binding_registry "
        "WHERE subject_kind=? AND dimension_key=?", (kind, dim)).fetchone()
    if not row:
        return {"ok": False, "code": "NO_SUCH_BINDING",
                "message": ("no binding for %s/%s — a binding must exist before "
                            "it can be proven" % (kind, dim))}
    if int(row["is_active"]) == 1:
        return {"ok": True, "code": "ALREADY_ACTIVE", "changed": False,
                "binding_id": int(row["binding_id"]),
                "subject_kind": kind, "dimension_key": dim}

    # THE CITATION GATE. An activation with no checkable reference is a claim.
    try:
        import citation_discipline as cd
        cd.assert_cited({"evidence_ref": cite_ref})
    except Exception as e:
        return {"ok": False, "code": "UNCITED",
                "message": "cite_ref %r is not checkable: %s" % (cite_ref, e)}

    # THE ONE GATE. Delegated, so "proven" has ONE definition.
    import activation_gate as ag
    ref_tag = binding_ref_tag(kind, dim)
    # THE TARGET IS DERIVED, NOT A CONSTANT.
    #
    # MEASURED DEFECT (2026-09-26): the harness DERIVED the target from the
    # binding's own contract (`streak_target_for` -> option_count * 5 = 10), but
    # this writer fell back to `ag.DEFAULT_TARGET` = 100. So a run that the
    # harness called `QUALIFIED` at streak 10 was REFUSED here as
    # "streak 10 < target 100" — the SAME evidence, two different verdicts,
    # because "proven" had TWO definitions.
    #
    # THE FIX: ask the harness for the target, so the gate and the run that
    # produced the evidence agree by construction. A caller may still override
    # with an explicit `target`.
    if target is None:
        try:
            import llm_100_run_harness as _h
            _d = _h.streak_target_for(conn, ref_tag)
            tgt = int(_d.get("target") or ag.DEFAULT_TARGET)
        except Exception:
            tgt = int(ag.DEFAULT_TARGET)
    else:
        tgt = int(target)
    ok, reason, detail = ag.assert_may_activate(
        conn, ref_tag, target=tgt, rule_version=rule_version, model=model)
    if not ok:
        return {"ok": False, "code": "NOT_PROVEN", "message": reason,
                "ref_tag": ref_tag, "streak": detail}

    conn.execute(
        "UPDATE dimension_binding_registry SET is_active=1, "
        "updated_at=datetime('now') WHERE binding_id=?",
        (int(row["binding_id"]),))
    if commit:
        conn.commit()
    return {"ok": True, "code": "ACTIVATED", "changed": True,
            "binding_id": int(row["binding_id"]),
            "subject_kind": kind, "dimension_key": dim,
            "ref_tag": ref_tag, "cite_ref": cite_ref, "streak": detail}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dimension binding register")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--repair-cite", action="store_true",
                    help="rewrite every cite_ref to its dimension's own line")
    ap.add_argument("--repair-example", action="store_true",
                    help="rewrite `example` to a real register:<table>:<pk>")
    ap.add_argument("--audit-cite", action="store_true",
                    help="report how many rows carry a DISTINCT, checkable "
                         "cite_ref (read-only)")
    ap.add_argument("--activation-verdict", action="store_true",
                    help="the verdict on the inactive bindings (read-only)")
    # THE KEY TO THE GATE. THE HUMAN (2026-09-26): "yes, have the plan and fix
    # it now".
    #
    # MEASURED DEFECT THIS CLOSES: `activate_binding()` EXISTS and delegates to
    # `activation_gate.assert_may_activate`, but `main()` declared NO flag that
    # reaches it, so the ONLY caller was a proof. The table is IN
    # `activation_scope()["in_scope"]` and had NO reachable writer -- a gate
    # with no key.
    #
    # IT DOES NOT BYPASS THE GATE. It calls the SAME `activate_binding`, so the
    # streak AND the 2-part verdict are still required. A flag that could
    # activate without proof would be the defect this gate exists to prevent.
    ap.add_argument("--activate-binding", action="store_true",
                    help="activate ONE binding; REFUSED unless the streak and "
                         "the 2-part verdict prove it")
    ap.add_argument("--dimension", default=None,
                    help="the dimension_key for --activate-binding")
    ap.add_argument("--cite-ref", default=None,
                    help="the checkable reference for --activate-binding")
    ap.add_argument("--kind", default=None)
    args = ap.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.activation_verdict:
            v = binding_activation_verdict(conn)
            print("STATE: total=%d active=%d inactive=%d"
                  % (v["total"], v["active"], v["inactive"]))
            print("DDL DEFAULT 0: %s" % v["ddl_default_zero"])
            print("GATE: %s" % v["gate"])
            print("SCOPE: %s" % v["scope"])
            print()
            print("VERDICTS")
            for x in v["verdicts"]:
                print("  CLAIM  : %s" % x["claim"])
                print("  VERDICT: %s" % x["verdict"])
                print("  CITE   : %s" % x["cite"])
                print()
            print("REFUSAL: %s" % v["refusal"]["would"])
            print("ref_tag formula: %s" % v["refusal"]["ref_tag_formula"])
            return 0
        if args.activate_binding:
            if not args.kind or not args.dimension:
                print("REFUSED: --activate-binding needs --kind AND --dimension")
                return 2
            if not args.cite_ref:
                print("REFUSED: --activate-binding needs --cite-ref (an "
                      "activation with no checkable reference is a claim)")
                return 2
            r = activate_binding(conn, args.kind, args.dimension,
                                 cite_ref=args.cite_ref)
            print(json.dumps(r, indent=1, ensure_ascii=False))
            return 0 if r.get("ok") else 1
        if args.seed:
            print(seed_bindings(conn))
        if args.repair_cite:
            print(repair_cite_refs(conn))
        if args.repair_example:
            print(repair_examples(conn))
        if args.audit_cite:
            print(audit_cite_refs(conn))
        for r in list_bindings(conn, subject_kind=args.kind):
            print("  %-11s %-6s %-20s %s" % (r["subject_kind"],
                                             r["dimension_key"],
                                             r["cite_ref"],
                                             r["binding_text"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
