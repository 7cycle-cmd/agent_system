# -*- coding: utf-8 -*-
"""skill_factor.py — a skill is a TABLE, and every row carries a MEASURABLE proof.

THE PROPOSAL (from the human)
-----------------------------
"skill format not by .md anymore! be standardize by table, this is the skill
template for skill generator"

THE CONSTRAINT THAT SHAPES IT
----------------------------
"my skill list not replace the skill i have, is suggestion to help to skill can be
more multi dimensional"

So this ADDS a dimension. It does not replace the 46 `.skill.md` files, the 28
ssot rows, the 48 register rows or the 20 contracts. Those stay.

THE REQUIREMENT, TAKEN LITERALLY
--------------------------------
"Audit Proof column cannot just be descriptive text; must contain quantifiable
metric + unique proof artifact ID; each factor can pass/fail independently"

MEASURED, that is a REAL gap, not a restatement:
    `skill_contract_tdd_case` has `assertion` (prose) and `expected_json` (a
    dict). It has NO metric column and NO proof-artifact id. So a case can be
    "asserted" without anything being MEASURED, and two cases can describe the
    same proof with nothing to tell them apart.

THE SHAPE
---------
```
skill_factor_registry   the 19 factors — ONE dimension, 19 ROWS (not 19 columns)
    factor_id INTEGER PK · factor_key TEXT UNIQUE · name · rule_definition
    · action · metric_kind · metric_target · proof_prefix · applies_to
    · sort_order · is_active

skill_factor_proof      the MEASURED result, per skill x factor
    proof_id INTEGER PK · skill_ref INTEGER FK · factor_ref INTEGER FK
    · metric_value · metric_pass INTEGER · proof_artifact_id TEXT UNIQUE
    · evidence_ref TEXT · measured_at
```
`proof_artifact_id` is UNIQUE: one proof cannot point at two things.
`metric_pass` is INTEGER 0/1: each factor passes or fails INDEPENDENTLY, so one
failure does not invalidate the others.

WHY A NEW TABLE AND NOT A CHANGE TO `skill_contract_tdd_case`
------------------------------------------------------------
That table has 114 rows, a probe-token dispatch mechanism and a streak. Changing
it would break 20 existing contracts. A new table can COEXIST, which is exactly
what "not replace the skill i have" requires.

THE DRIFT THIS ALSO EXPOSES (measured)
--------------------------------------
    46 `.skill.md` files, 28 ssot keys
    identical text : 14
    DIFFERENT text : 12   <- the file and the row already disagree
    no ssot row    : 20
A file and a row that disagree is the same defect as a rule stated but not
enforced: whichever one a reader opens, the other is stale. `drift_report()`
measures it rather than assuming either side is right.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

DB = BASE / "agent.db"

# Metric kinds. A metric that is not one of these cannot be scored, so an unknown
# kind is REFUSED rather than stored as free text.
MK_BOOL = "boolean"
MK_COUNT = "count"
MK_PCT = "pct"
MK_SCORE = "score_0_100"
METRIC_KINDS = (MK_BOOL, MK_COUNT, MK_PCT, MK_SCORE)

# Which skill types a factor applies to. A UI skill does not need a SAST gate, so
# "every factor for every skill" would manufacture 19 failures per skill that
# measure nothing. `*` means every skill.
APPLIES_ALL = "*"

# ---------------------------------------------------------------------------
# THE 19 FACTORS — one dimension, 19 rows.
#
# Each carries a metric_kind and a metric_target, because the human's rule is
# that a proof must be QUANTIFIABLE. `proof_prefix` is the stable part of the
# artifact id, so a proof can be found by prefix.
# ---------------------------------------------------------------------------
FACTORS: list[dict[str, Any]] = [
    dict(factor_key="template_source_ontology_binding", sort_order=1,
         name="Template Source & Ontology Binding",
         rule_definition="CRUD generation SSOT = Table Register + Field Register. "
                         "All entities/relationships must match the Ontology "
                         "definition. No hardcoded table structure.",
         action="Load snapshots of Table Register, Field Register and Ontology; "
                "freeze the hash version.",
         metric_kind=MK_PCT, metric_target="100",
         metric_unit="pct of entities matching the Ontology definition",
         proof_prefix="proof_001", applies_to=APPLIES_ALL),
    dict(factor_key="table_meta_scoring_link", sort_order=2,
         name="Table Meta Input + Scoring Link",
         rule_definition="Read table metadata (module, capability, TTL, MCP, "
                         "whitelist) and attach quality scoring weights.",
         action="Inject metadata + scoring weights into the code comment; "
                "generate the MCP / OpenAPI snippet.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of missing required metadata fields",
         proof_prefix="proof_002", applies_to=APPLIES_ALL),
    dict(factor_key="field_meta_ontology_property", sort_order=3,
         name="Field Meta Input + Ontology Property",
         rule_definition="Load field definition (type, nullable, lazy FK flag, "
                         "regex, boundary) and map each field to an Ontology "
                         "property. Separate legal from illegal NULL.",
         action="Generate payload validation logic + boundary unit tests; map "
                "each field to an ontology property.",
         metric_kind=MK_PCT, metric_target="100",
         metric_unit="pct of fields mapped to an Ontology property",
         proof_prefix="proof_003", applies_to=APPLIES_ALL),
    dict(factor_key="crud_create", sort_order=4,
         name="Create (Insert) Function",
         rule_definition="Primary key = business / trace / check ID, no "
                         "auto-increment. Lazy FK allows a business NULL. DTO "
                         "validation + input sanitization.",
         action="Build the Insert function with a payload validator and an input "
                "sanitizer.",
         metric_kind=MK_PCT, metric_target="100",
         metric_unit="pct of insert test cases passing",
         proof_prefix="proof_004", applies_to="crud"),
    dict(factor_key="crud_read", sort_order=5,
         name="Read (Query) Function",
         rule_definition="Single PK lookup + pagination. Filter by trace_id / "
                         "case_id / queue_task_id. Column masking + query rate "
                         "limit.",
         action="Build Get / List functions; add column masking and a rate "
                "limiter.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of column-mask leaks",
         proof_prefix="proof_005", applies_to="crud"),
    dict(factor_key="crud_update", sort_order=6,
         name="Update Function",
         rule_definition="Optimistic lock required (version_id). No full "
                         "overwrite. No PK update. Lazy FK business reference "
                         "check.",
         action="Build the Update function with an optimistic-lock pre-check; "
                "record a before/after diff.",
         metric_kind=MK_PCT, metric_target="100",
         metric_unit="pct of update cases with a matching before/after diff",
         proof_prefix="proof_006", applies_to="crud"),
    dict(factor_key="crud_delete", sort_order=7,
         name="Delete Function",
         rule_definition="Soft delete by default (deleted flag + TTL). Hard "
                         "delete requires separate Check ID approval.",
         action="Build soft-delete logic; block hard delete unless a Check ID is "
                "approved; scan foreign dependencies.",
         metric_kind=MK_BOOL, metric_target="true",
         metric_unit="boolean of hard delete blocked without a Check ID",
         proof_prefix="proof_007", applies_to="crud"),
    dict(factor_key="lazy_fk_ontology_relation", sort_order=8,
         name="Lazy FK Handling & Ontology Relation",
         rule_definition="No native DB FK constraint. Referential integrity is "
                         "handled at the business layer. NULL = optional "
                         "relationship, defined inside the Ontology.",
         action="Embed business-layer FK validation; skip the DB foreign key "
                "constraint.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of illegal NULL references accepted",
         proof_prefix="proof_008", applies_to=APPLIES_ALL),
    dict(factor_key="environment_registry_check", sort_order=9,
         name="Environment Requirement + Register Check",
         rule_definition="Dependencies: SQLite, Table/Field Register, Schema "
                         "Worker, QC. Pre-check Ontology / Scoring module "
                         "registration and MCP whitelist status.",
         action="Check that all required services and registered skills are alive "
                "and enabled.",
         metric_kind=MK_PCT, metric_target="100",
         metric_unit="pct of required dependencies ready",
         proof_prefix="proof_009", applies_to=APPLIES_ALL),
    dict(factor_key="tdd_unit_test_eval", sort_order=10,
         name="TDD Unit Test + Eval Scoring",
         rule_definition="TDD rule: test cases are generated BEFORE business "
                         "code. Positive / negative / boundary cases.",
         action="Auto-generate the unit test suite and eval cases; execute the "
                "eval and compute the test dimension score.",
         metric_kind=MK_SCORE, metric_target="100",
         metric_unit="score_0_100 of test suite stability",
         proof_prefix="proof_010", applies_to=APPLIES_ALL),
    dict(factor_key="dry_run_before_apply", sort_order=11,
         name="Dry Run Before Apply",
         rule_definition="Simulate CRUD without touching production. Calculate "
                         "data impact, conflict count and NULL risk.",
         action="Run the dry-run simulation; output a change preview report.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of conflicts found in the dry run",
         proof_prefix="proof_011", applies_to="crud"),
    dict(factor_key="security_sast_gate", sort_order=12,
         name="Security SAST Scan Gate",
         rule_definition="Check SQL injection, sensitive data leak and privilege "
                         "escalation. Security score is one dimension of Scoring.",
         action="Run a static SAST scan; block code on high/critical "
                "vulnerabilities.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of critical vulnerabilities",
         proof_prefix="proof_012", applies_to="code"),
    dict(factor_key="qc_review_scoring_approval", sort_order=13,
         name="QC Review Gate + Scoring Approval",
         rule_definition="QC worker validates CRUD logic against census data. "
                         "Combine ontology consistency, test score and security "
                         "score into a total. Approve only when total >= "
                         "threshold.",
         action="QC executes the full test suite, compares against census data, "
                "and approves/rejects with a score.",
         metric_kind=MK_SCORE, metric_target="100",
         metric_unit="score_0_100 of total quality",
         proof_prefix="proof_013", applies_to=APPLIES_ALL),
    dict(factor_key="schema_snapshot_rollback", sort_order=14,
         name="Schema Snapshot & Rollback + Ontology Version",
         rule_definition="Save a schema snapshot; bind the CRUD code version to "
                         "the snapshot hash. Freeze the matching Ontology version "
                         "and scoring weight.",
         action="Store the snapshot hash, trace_id, rollback script, ontology "
                "backup and scoring rule backup.",
         metric_kind=MK_PCT, metric_target="100",
         metric_unit="pct of rollback restores succeeding",
         proof_prefix="proof_014", applies_to=APPLIES_ALL),
    dict(factor_key="ntd_no_direct_production_execute", sort_order=15,
         name="Not To Do 1: No direct production execute",
         rule_definition="Never run generated CRUD code against production. "
                         "Execution is delegated to the scheduler. Bypassing the "
                         "ontology check is blocked.",
         action="Code output only; the scheduler holds execution permission; "
                "block any execution that skips the ontology check.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of direct production write attempts",
         proof_prefix="proof_015", applies_to=APPLIES_ALL),
    dict(factor_key="ntd_no_bypass_registry_ontology", sort_order=16,
         name="Not To Do 2: No bypass Register / Ontology",
         rule_definition="Cannot modify a table/field definition bypassing the "
                         "Table/Field Register. A new entity relationship must be "
                         "registered into the Ontology.",
         action="Compare code against the register hash; block unregistered "
                "ontology relations.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of register bypass attempts",
         proof_prefix="proof_016", applies_to=APPLIES_ALL),
    dict(factor_key="ntd_no_auto_purge_legal_null", sort_order=17,
         name="Not To Do 3: No auto purge legal NULL",
         rule_definition="Do not batch delete legal business NULL. Changing "
                         "ontology relationships requires a separate Check ID.",
         action="Protect legal NULL records; block a mass clean operation without "
                "a Check ID.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of legal NULL rows deleted",
         proof_prefix="proof_017", applies_to=APPLIES_ALL),
    dict(factor_key="ntd_no_db_auto_increment_pk", sort_order=18,
         name="Not To Do 4: No DB auto increment PK",
         rule_definition="Primary key must be a registered business ID / trace ID "
                         "/ check ID. A new entity ID must register into the "
                         "Ontology.",
         action="Enforce the custom ID generation skill; run a duplicate ID "
                "check.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of duplicate primary keys",
         proof_prefix="proof_018", applies_to="crud"),
    dict(factor_key="ntd_no_hardcoded_secrets", sort_order=19,
         name="Not To Do 5: No hardcoded secrets",
         rule_definition="No secret / API key hardcoded in source. The secret "
                         "scan result feeds into security scoring.",
         action="Scan the source code; fail if a hardcoded secret is found.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of hardcoded secrets found",
         proof_prefix="proof_019", applies_to="code"),
    # ---- the LLM provider EDGES (user, 2026-09-21) ----
    # "condition is LLM ability * services factor". `local` is INFRASTRUCTURE
    # (where the model runs), not ABILITY. These factors state the EDGE of each
    # provider, so dispatch can be decided by ability rather than by location.
    dict(factor_key="llm_latency_budget", sort_order=20,
         name="LLM Local Latency Budget",
         rule_definition="A local (on-machine) provider must answer within the "
                         "interactive budget. Measured median for "
                         "qwen2.5:7b-instruct is ~108ms after the first call; "
                         "the first call is cold and may take seconds.",
         action="Measure the round-trip per call; fail if the median exceeds "
                "the budget, and warm the model before timing.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of calls exceeding the latency budget",
         proof_prefix="proof_020", applies_to="llm.local"),
    dict(factor_key="llm_token_cost_gate", sort_order=21,
         name="LLM Remote Token Cost Gate",
         rule_definition="A remote (IDE-reached) provider costs tokens, so a "
                         "task must not be routed to it when a local provider "
                         "can serve it. Cost is a routing input, not a "
                         "post-hoc report.",
         action="Compare the task against the local provider's capability "
                "first; route remote only when local cannot serve it, and "
                "record the reason.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of tasks routed remote while a local provider "
                     "could have served them",
         proof_prefix="proof_021", applies_to="llm.remote"),
    dict(factor_key="llm_availability_window", sort_order=22,
         name="LLM Remote Availability Window",
         rule_definition="A remote provider is reachable only while a human / "
                         "IDE is present, so it cannot be assumed available. "
                         "Its unavailability is a normal state, not a fault.",
         action="Check reachability before routing; when unreachable, raise a "
                "service ticket instead of failing the task.",
         metric_kind=MK_BOOL, metric_target="true",
         metric_unit="boolean of unreachable-remote handled by a ticket rather "
                     "than a task failure",
         proof_prefix="proof_022", applies_to="llm.remote"),
    dict(factor_key="tag_definition_has_cite", sort_order=23,
         name="Tag Definition Carries A Citation",
         rule_definition="A capability tag's definition must cite where its "
                         "meaning comes from. An invented definition is a guess "
                         "with a heading, and a tag with no meaning matches "
                         "zero skills silently.",
         action="Require cite_ref on register_tag(); refuse a definition "
                "without one.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of registered tags whose definition has no citation",
         proof_prefix="proof_023", applies_to="code"),
    dict(factor_key="tag_not_duplicate", sort_order=24,
         name="Tag Is Not A Silent Duplicate",
         rule_definition="Registering a tag that already exists must not "
                         "silently overwrite the existing definition. A clobber "
                         "destroys a definition someone else wrote and the "
                         "caller never learns.",
         action="Refuse a duplicate unless update=True is passed explicitly.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of silent tag overwrites",
         proof_prefix="proof_024", applies_to="code"),
    dict(factor_key="probe_side_effect_free", sort_order=25,
         name="Health Probe Is Side-Effect Free",
         rule_definition="A health probe must be SIDE-EFFECT-FREE and CHEAP. "
                         "The default answer for an unproven tool is 'do not "
                         "touch it', NOT 'it is probably fine'. A blacklist "
                         "cannot keep up: `screen.snapshot` was probed as a "
                         "'health check' and produced 597 screen captures in "
                         "bursts of 7 within one second, because the list named "
                         "8 of 59 tools.",
         action="For every tool a probe may call, PROVE it is read-only. A tool "
                "that cannot be proven read-only is marked unsafe. The safe set "
                "is an ALLOWLIST, so a new tool is unsafe until proven "
                "otherwise.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of tools marked probe-safe WITHOUT a read-only "
                     "proof",
         proof_prefix="proof_025", applies_to="code"),
    dict(factor_key="lesson_has_cite", sort_order=26,
         name="A Lesson Carries A Citation",
         rule_definition="A lesson with no citation is 'I think it is', not a "
                         "finding. `skill_learning.add_lesson` already REFUSES "
                         "an uncited lesson at the write site (measured: 3 of 8 "
                         "lessons were refused for a cite_ref of "
                         "`_proof_llm_service.py` with no line, and the fix was "
                         "`_proof_llm_service.py:260`). The rule is that the "
                         "refusal must stay: a lesson is DISCARDED, never "
                         "downgraded to a low-confidence lesson.",
         action="Every row in `skill_lesson` must carry a `source_ref` that "
                "passes `citation_discipline.is_citation` (an anchored "
                "`path:line` or a command). An uncited lesson is refused, not "
                "stored.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of lessons stored WITHOUT a checkable citation",
         proof_prefix="proof_026", applies_to="code"),
    dict(factor_key="lesson_is_filed", sort_order=27,
         name="A Failure Is Filed As A Lesson",
         rule_definition="A failure that stays in the chat is not a lesson. "
                         "Measured 2026-09-21: `skill_lesson` held 32 rows and "
                         "NONE described this session's failures, while the "
                         "table, the write gate and the `self_fail` vocabulary "
                         "all already existed. The infrastructure was built and "
                         "never used, which is the same 'no skill -> no "
                         "multi-dimensional SSOT' gap the user named.",
         action="At the end of a session that produced a defect, file each "
                "defect into `skill_lesson` with its root cause, its fix and a "
                "citation. Filing must be IDEMPOTENT: the same lesson filed "
                "twice is one lesson, not two.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of session defects left unfiled in chat",
         proof_prefix="proof_027", applies_to="code"),
    dict(factor_key="soft_delete_has_cite", sort_order=28,
         name="A Soft Delete Carries A Citation",
         rule_definition="A soft delete with no cite_ref is 'I think it is a "
                         "duplicate', not a finding. Measured 2026-09-21: "
                         "`register_store.soft_delete()` set `is_active = 0` and "
                         "recorded NOTHING, and the one real soft delete in this "
                         "repo (`skill_registry.skill_key = "
                         "'mcp-tool-checklist'`) kept its reason only inside the "
                         "`description` PROSE. The row was disabled and nobody "
                         "could check WHY.",
         action="Require a `cite_ref` on every soft delete, checked by "
                "`citation_discipline.is_citation` at the WRITE SITE. An "
                "uncited soft delete is REFUSED, never downgraded. Append the "
                "event to `soft_delete_log`, which is append-only so a delete, "
                "a revival and a second delete all survive.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of soft deletes recorded WITHOUT a checkable "
                     "citation",
         proof_prefix="proof_028", applies_to="code"),
    dict(factor_key="soft_delete_no_orphan", sort_order=29,
         name="A Soft Delete Leaves No Live Reference",
         rule_definition="A disabled row that is still referenced is a LEAK, "
                         "not a soft delete: the referencing row points at "
                         "something the system now treats as gone. Measured "
                         "2026-09-21: the one real soft delete had 0 orphans, so "
                         "the guard passes on real data — it is a guard, not a "
                         "repair.",
         action="Before disabling a row, count the rows in other tables that "
                "still reference it. Refuse the delete when the count is "
                "non-zero, unless `allow_orphans=True` is passed with a reason.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of soft-deleted rows still referenced by a live row",
         proof_prefix="proof_029", applies_to="code"),
    dict(factor_key="capability_has_kind", sort_order=30,
         name="A Capability Carries A Registered Kind",
         rule_definition="A capability's kind decides WHICH transport serves it "
                         "(`thinking` -> an LLM over HTTP; `eye`/`hand`/`voice` "
                         "-> OpenClaw over MCP). A kind that is not in "
                         "`capability_kind_registry` would match ZERO "
                         "capabilities and dispatch NOTHING, silently — the same "
                         "defect `skill_factor.assert_known_tag` exists to "
                         "prevent.",
         action="Every row in `capability_registry` must carry a "
                "`capability_kind` that is a registered kind. `capability_store."
                "validate_kinds()` audits the TABLE, not just the write path, "
                "because a row inserted by a migration was never checked.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of capabilities whose kind is not registered",
         proof_prefix="proof_030", applies_to="code"),
    dict(factor_key="capability_tool_is_declared", sort_order=31,
         name="A Capability's Tools Are Declared In A Table",
         rule_definition="Measured 2026-09-21: the capability -> tool mapping "
                         "existed in FOUR places and they DISAGREED. "
                         "`openclaw_settings.CAPABILITIES` MERGED screen capture "
                         "and screen record; `capability_registry` and "
                         "`MCP_TOOL_FOR_CAPABILITY` both SPLIT them. A mapping "
                         "that lives in a Python literal cannot gain a tool "
                         "without a code change, so the table can never hold a "
                         "tool the literal does not already name.",
         action="Declare tools in `capability_tool`, joined to its capability, "
                "written in the SAME transaction as the capability. A Python "
                "list is a SEED or a FALLBACK, never the source.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of capabilities whose tools live only in a Python "
                     "literal",
         proof_prefix="proof_031", applies_to="code"),
    dict(factor_key="capability_declaration_not_observation", sort_order=32,
         name="A Declaration Is Stored, An Observation Is Measured",
         rule_definition="A capability's name, tools, gate and reason are "
                         "DECLARATIONS. Its `state` (working/failing/present/"
                         "missing) is an OBSERVATION. Storing an observation "
                         "makes a stale reading look like a fact: the moment the "
                         "world moves on, the row still asserts the old answer.",
         action="Store only declarations. Compute `state` / `in_server` / "
                "`note` at read time from the live probe. A proof must assert "
                "that no `state` column exists on the declaration tables.",
         metric_kind=MK_COUNT, metric_target="0",
         metric_unit="count of stored OBSERVATION columns on a declaration "
                     "table",
         proof_prefix="proof_032", applies_to="code"),
]

FACTOR_registry_DDL = """
CREATE TABLE IF NOT EXISTS skill_factor_registry (
    factor_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    -- THE COMPOSITE KEY (2026-09-22). `factor_key` used to be
    -- `TEXT NOT NULL UNIQUE`, which made the register SINGLE-dimensional: one
    -- factor name could exist under exactly one scope. The human's point was
    -- that the register should be "multi dimensional ssot", and the model for
    -- that already exists in `wording_registry`:
    --     UNIQUE (skill_id, dim_key, wording_key)
    -- -- the same word is legal under a different dimension.
    --
    -- Measured: only 3 of 11 register tables had a multi-column key, and the
    -- factor register was one of the 8 single-key ones, so the multi-dimensional
    -- claim was NOT true of it. This makes it true.
    --
    -- WHY THE UNIQUE INDEX IS CREATED OUTSIDE THIS DDL, AS AN EXPRESSION
    -- A generic factor has `skill_key IS NULL`. SQLite treats NULLs as DISTINCT
    -- in a UNIQUE index, so `UNIQUE(skill_key, factor_key)` would ALLOW two
    -- generic factors with the same key — the constraint would silently stop
    -- constraining. `COALESCE(skill_key,'')` gives the generic case a real value
    -- to compare. Expression indexes cannot be declared inline, so the index is
    -- created by `_ensure_factor_unique_indexes()`.
    factor_key      TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    rule_definition TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    metric_kind     TEXT    NOT NULL
                    CHECK (metric_kind IN ('boolean','count','pct','score_0_100')),
    -- THE MEASURED UNIT. A number without a unit cannot be audited: '0' is a
    -- pass for a vulnerability count and a FAIL for a test pass rate. The unit
    -- must NAME its subject (`count of critical vulnerabilities`), and a unit
    -- that names nothing is REFUSED by `factor_first_principle`.
    metric_unit     TEXT    NOT NULL DEFAULT '',
    metric_target   TEXT    NOT NULL,
    -- Composite too: two scopes of the same factor must not collide on the proof
    -- prefix, or a proof artifact would resolve to the wrong factor.
    proof_prefix    TEXT    NOT NULL,
    applies_to      TEXT    NOT NULL DEFAULT '*',
    -- DOES THIS FACTOR'S NUMBER NEED AN INDEPENDENT CROSS-CHECK? (2026-09-29)
    --
    -- MEASURED: the agent reported 1516 uncovered paths; the number was wrong by
    -- a factor of 22, and what caught it was the agent asking itself "what should
    -- the number be?" — a HUMAN judgement. The human named the defect:
    --   「依賴人（或 agent）自覺，唔係依賴機制」
    --
    -- A factor whose value is a COUNT over a population is exactly the kind that
    -- can be wrong-but-plausible, so it declares `requires_cross_check=1` and
    -- `record_proof` then REFUSES a value with no AGREED cross-check row. The
    -- requirement is DATA (a declared factor), NOT a caller flag, so a caller
    -- cannot opt out of it.
    requires_cross_check INTEGER NOT NULL DEFAULT 0
                    CHECK (requires_cross_check IN (0, 1)),
    -- WHICH SKILL this factor belongs to. NULL = a GENERIC factor that applies
    -- to every skill whose tags match `applies_to`.
    --
    -- WHY THIS COLUMN EXISTS (measured 2026-09-21)
    -- -------------------------------------------
    -- Before it, the register held ONLY generic factors, so the generator
    -- emitted the SAME 11 rows for all 50 skills. Measured: 11 factor names
    -- appeared in all 50 skills, 642 of 652 factor rows were UNMEASURED, and
    -- the generated body for `citation_discipline` mentioned its own subject
    -- ZERO times -- it talked about "Lazy FK Handling" and "Security SAST Scan
    -- Gate", which are CRUD concerns.
    --
    -- The generator was not at fault: it faithfully rendered a register that
    -- had no per-skill content. Automating it would have multiplied the defect.
    --
    -- A per-skill factor is matched by `skill_key` EXACTLY, so it applies to
    -- one skill and no other. It is NOT a tag: a tag is a CAPABILITY shared by
    -- many skills, a skill_key is an IDENTITY.
    skill_key       TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    -- THE EXPERIENCE. The human's correction (2026-09-22): "i am talking about
    -- experience rewrite, if yes, as factor is table already, don't need to have
    -- extra library, just extend the factor table".
    --
    -- They are right, and I had over-built. I created `factor_change_log` and
    -- `lesson_logic_link` as separate tables for the growth loop's history. But
    -- the factor IS already a table, and the experience of a factor is a
    -- PROPERTY of that factor — not a new entity. A separate library means a
    -- join for every read, and a factor whose experience can be missing.
    --
    -- So the experience lives HERE, as a field:
    --   experience_log  the accumulated lessons, newest last, as JSON
    --   experience_n    how many lessons it holds (so "has experience" is a
    --                   cheap read, not a JSON parse)
    --   rewritten_at    when the experience last changed the factor
    --
    -- `experience_log` is JSON rather than a child table because the experience
    -- is READ WITH the factor and never queried on its own. A child table would
    -- be the right shape if the lessons were entities; they are not — they are
    -- the factor's own history.
    experience_log  TEXT    NOT NULL DEFAULT '[]',
    experience_n    INTEGER NOT NULL DEFAULT 0,
    rewritten_at    TEXT    NOT NULL DEFAULT 'NA',
    is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    skill_ref INTEGER,
    cite_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_skill_factor_order
  ON skill_factor_registry (is_active, sort_order);
"""
# NOTE: the `skill_key` index is NOT in the DDL above. `executescript` runs
# `CREATE INDEX` AFTER `CREATE TABLE IF NOT EXISTS`, and on an EXISTING table
# that IF NOT EXISTS does nothing -- so the index would reference a column that
# does not exist yet and the whole script would fail with
# "no such column: skill_key" (measured). The column is added by the additive
# migration in `ensure_schema()`, and the index is created THERE, after it.

FACTOR_PROOF_DDL = """
CREATE TABLE IF NOT EXISTS skill_factor_proof (
    proof_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_ref         INTEGER NOT NULL,
    factor_ref        INTEGER NOT NULL,
    metric_value      TEXT    NOT NULL,
    metric_pass       INTEGER NOT NULL CHECK (metric_pass IN (0, 1)),
    proof_artifact_id TEXT    NOT NULL UNIQUE,
    evidence_ref      TEXT    NOT NULL,
    measured_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    -- INVALIDATION, NOT DELETION. A proof recorded for a factor that does not
    -- apply is a measurement of the wrong object, so it must stop counting —
    -- but deleting it destroys the evidence that the gate was missing. The row
    -- stays, `invalidated_at` is set, and `invalidated_reason` says why. This is
    -- the same rule as soft-delete elsewhere in the repo: never DROP.
    invalidated_at     TEXT    NOT NULL DEFAULT 'NA',
    invalidated_reason TEXT    NOT NULL DEFAULT 'NA',
    UNIQUE (skill_ref, factor_ref),
    FOREIGN KEY (skill_ref)  REFERENCES skill_registry (skill_id),
    FOREIGN KEY (factor_ref) REFERENCES skill_factor_registry (factor_id)
);
CREATE INDEX IF NOT EXISTS idx_skill_factor_proof_skill
  ON skill_factor_proof (skill_ref, metric_pass);
CREATE INDEX IF NOT EXISTS idx_skill_factor_proof_artifact
  ON skill_factor_proof (proof_artifact_id);
"""

# ---- skill_factor_proof_log (APPEND-ONLY measurement history) ----
# `skill_factor_proof` holds ONE row per (skill, factor) and is UPDATEd, so it
# answers "what is the state NOW?". It CANNOT answer "was this ever failing?" —
# a fix overwrites the failure.
#
# DEFECT FOUND BY RUNNING OPTION C (2026-09-21): the first measurement recorded
# metric_value=38 (FAIL) and the second recorded 0 (PASS), and the trace showed
# ONLY the PASS. The record that the defect existed was gone. A trace that can be
# rewritten is not a trace — the same rule `ticket_event` and
# `skill_capability_tag_log` already follow.
#
# Never UPDATEd, never DELETEd.
FACTOR_PROOF_LOG_DDL = """
CREATE TABLE IF NOT EXISTS skill_factor_proof_log (
    log_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_ref         INTEGER NOT NULL,
    factor_ref        INTEGER NOT NULL,
    metric_value      TEXT    NOT NULL,
    metric_pass       INTEGER NOT NULL CHECK (metric_pass IN (0, 1)),
    proof_artifact_id TEXT    NOT NULL,
    evidence_ref      TEXT    NOT NULL,
    measured_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (skill_ref)  REFERENCES skill_registry (skill_id),
    FOREIGN KEY (factor_ref) REFERENCES skill_factor_registry (factor_id)
);
CREATE INDEX IF NOT EXISTS idx_skill_factor_proof_log
  ON skill_factor_proof_log (skill_ref, factor_ref, log_id);
"""

# ---- measurement_cross_check (the INDEPENDENT-READER record) ----
# WHY THIS TABLE EXISTS (2026-09-29)
# ----------------------------------
# MEASURED: the agent reported 1516 uncovered paths; the number was wrong by a
# factor of 22. What caught it was the agent asking itself "what should the
# number be?" — a HUMAN judgement. The human named the defect:
#   「依賴人（或 agent）自覺，唔係依賴機制」
#
# A factor that declares `requires_cross_check=1` cannot have a proof recorded
# until a row HERE says the number was reproduced by a SECOND, INDEPENDENT
# reader. The two readers must differ in NAME, METHOD and DATA SOURCE — two
# differently-named wrappers over ONE parser can agree while both are wrong, and
# that is WORSE than no second reader because it claims "verified".
#
# `status` is the verdict of `measurement_cross_check.cross_check()`; only
# `AGREED` satisfies the gate. The row is APPEND-ONLY (never UPDATEd), so the
# history of a number's verification survives a later re-measurement.
CROSS_CHECK_DDL = """
CREATE TABLE IF NOT EXISTS measurement_cross_check (
    cross_check_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_key      TEXT    NOT NULL,
    skill_key       TEXT    NOT NULL DEFAULT '',
    reader_a_name   TEXT    NOT NULL,
    reader_a_method TEXT    NOT NULL,
    reader_a_source TEXT    NOT NULL,
    reader_b_name   TEXT    NOT NULL,
    reader_b_method TEXT    NOT NULL,
    reader_b_source TEXT    NOT NULL,
    count_a         TEXT    NOT NULL,
    count_b         TEXT    NOT NULL,
    population_size TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    cite_ref        TEXT    NOT NULL,
    measured_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cross_check_factor
  ON measurement_cross_check (factor_key, status, cross_check_id);
"""

# ---------------------------------------------------------------------------
# THE TAG VOCABULARY — an `applies_to` value must be DEFINED, not free text.
#
# MEASURED DEFECT: `applies_to` holds `crud` and `code`, and NOTHING defined
# them. A typo (`applies_to='crd'`) matched ZERO skills and said nothing — the
# same silent-dead-match defect as reading prose as a tag. `failure_axis.py`
# already solves this shape for `failure_class`: the values are registered with
# a definition, and a value with no definition cannot be used.
#
# The registry lives HERE, with the module that owns `applies_to`, so there is
# ONE copy of the vocabulary and no import cycle.
# ---------------------------------------------------------------------------
NA_TAG = "NA"

# THE SEED, NOT THE SOURCE OF TRUTH.
#
# DEFECT FOUND BY THE USER (2026-09-21): this tuple used to be the ONLY way to
# add a tag — adding one meant editing Python. That contradicts the repo's own
# rule ("DB-driven, never hand-listed"): `registered_tags()` READS the table, but
# the WRITE path was hardcoded, so the table could never hold a tag the tuple did
# not already name. The user's words: "先登記 tag (this is the problem for your
# job, or instuction is not good at all, skill can help too)".
#
# So this is now a SEED, the same pattern as `ticket_store.DEFAULT_SERVICES`:
# it populates an empty registry and is then never authoritative again. The
# write path is `register_tag()`, and the read path is `registered_tags()`.
CAPABILITY_TAGS: tuple[tuple[str, str], ...] = (
    # MEASURED DEFECT (2026-09-21): `*` was never seeded, yet `register_factor`'s
    # default parameter IS `applies_to=APPLIES_ALL` == '*'. So on any database
    # where `*` was not already a row, a DEFAULT registration died with
    # "tag '*' is not in capability_tag_registry". A function whose own default
    # is rejected by its own gate cannot be called as documented, and the failure
    # only appeared on a FRESH database — the live one happened to have the row.
    (APPLIES_ALL, "The factor applies to EVERY skill. A UI skill does not need "
                  "a SAST gate, so this is only for rules that are genuinely "
                  "universal; anything narrower takes a scoped tag."),
    ("crud", "The skill generates or checks Create/Read/Update/Delete "
             "behaviour against the Table Register + Field Register."),
    ("code", "The skill produces or gates SOURCE CODE, so a SAST / secret "
             "scan applies to its output."),
    ("llm.local", "The skill is served by an LLM that runs ON THIS MACHINE "
                  "(ollama). Zero token cost, available 24/7, small model."),
    ("llm.remote", "The skill is served by an LLM reached through the IDE. "
                   "Token cost applies and a human/IDE must be present, but "
                   "the model is larger."),
)

CAPABILITY_TAG_DDL = """
CREATE TABLE IF NOT EXISTS capability_tag_registry (
    tag_key     TEXT    PRIMARY KEY,
    definition  TEXT    NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


class FactorError(ValueError):
    """Raised when a factor or a proof would be stored without a measurement."""


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """The column names of a table.

    MEASURED DEFECT (2026-09-22): `_rebuild_factor_registry_composite` called
    this function, which did NOT exist in this module — it lives in
    `db_schema`. The `NameError` was raised INSIDE the rebuild's `try`, and the
    `finally` restored the pragmas and let the exception propagate... except the
    caller was `ensure_schema`, whose own error handling swallowed it. The result
    was a HALF-MIGRATED table: 39 rows stranded in `_skill_factor_registry_old`
    and an empty `skill_factor_registry`, with no error reported.

    A missing helper is a trivial bug; the damage came from the failure being
    invisible. That is the same family as the earlier `_require_known_tag`
    NameError that looked like a working gate.
    """
    return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def seed_tag_registry(conn: sqlite3.Connection) -> dict[str, Any]:
    """Populate an EMPTY tag registry from the seed. Idempotent.

    DEFECT FOUND BY THE USER (2026-09-21): this used to UPDATE an existing tag's
    definition on every run, so a human-edited definition was silently clobbered
    by the seed the next time anything called `ensure_schema()`. A seed that
    overwrites is not a seed — it is a hidden source of truth that wins on every
    restart. An existing tag is now LEFT ALONE; changing a definition is an
    explicit act (`register_tag(..., update=True)`).
    """
    conn.executescript(CAPABILITY_TAG_DDL)
    added, kept = 0, 0
    for tag_key, definition in CAPABILITY_TAGS:
        row = conn.execute("SELECT tag_key FROM capability_tag_registry WHERE "
                           "tag_key=?", (tag_key,)).fetchone()
        if row:
            kept += 1          # NOT overwritten: the table is the truth
        else:
            conn.execute("INSERT INTO capability_tag_registry (tag_key, "
                         "definition) VALUES (?,?)", (tag_key, definition))
            added += 1
    conn.commit()
    return {"added": added, "kept": kept,
            "total": conn.execute("SELECT COUNT(*) FROM "
                                   "capability_tag_registry").fetchone()[0]}


def register_tag(conn: sqlite3.Connection, tag_key: str, definition: str, *,
                 cite_ref: str = "", update: bool = False) -> dict[str, Any]:
    """Register a tag. THE WRITE PATH — the seed is no longer the only way.

    WHY THIS EXISTS (user, 2026-09-21): "先登記 tag (this is the problem for your
    job, or instuction is not good at all, skill can help too)". Adding a tag
    used to require editing `CAPABILITY_TAGS` in Python. A tag is DATA, so it is
    registered as data.

    REFUSES:
      * an empty tag_key or definition — a tag with no meaning matches nothing
      * a duplicate tag unless `update=True` — a silent clobber would destroy a
        definition someone else wrote, and the caller would never know
      * a definition with no `cite_ref` — an invented definition is a guess with
        a heading. The citation is what makes it checkable.
    """
    key = str(tag_key or "").strip()
    defn = str(definition or "").strip()
    if not key:
        raise FactorError("tag_key is required — an unnamed tag cannot be "
                          "matched against anything")
    if not defn:
        raise FactorError(
            "definition is required for tag %r — a tag with no meaning matches "
            "zero skills SILENTLY, which is the defect the registry exists to "
            "prevent" % key)
    if not str(cite_ref or "").strip():
        raise FactorError(
            "cite_ref is required for tag %r — an invented definition is a "
            "guess with a heading. Cite where the meaning comes from." % key)
    conn.executescript(CAPABILITY_TAG_DDL)
    row = conn.execute("SELECT definition FROM capability_tag_registry WHERE "
                       "tag_key=?", (key,)).fetchone()
    if row and not update:
        raise FactorError(
            "tag %r is already registered (definition: %r). Refusing to "
            "overwrite it silently — pass update=True if the definition really "
            "changed, so the change is an explicit act." % (key, row[0]))
    if row:
        conn.execute("UPDATE capability_tag_registry SET definition=?, "
                     "updated_at=datetime('now') WHERE tag_key=?", (defn, key))
        action = "updated"
    else:
        conn.execute("INSERT INTO capability_tag_registry (tag_key, definition) "
                     "VALUES (?,?)", (key, defn))
        action = "added"
    conn.commit()
    return {"ok": True, "action": action, "tag_key": key, "definition": defn,
            "cite_ref": str(cite_ref)}


def registered_tags(conn: sqlite3.Connection) -> list[str]:
    """The ACTIVE tag vocabulary, read from the registry (never hand-listed)."""
    conn.executescript(CAPABILITY_TAG_DDL)
    return sorted(str(r[0]) for r in conn.execute(
        "SELECT tag_key FROM capability_tag_registry WHERE is_active=1"))


def assert_known_tag(conn: sqlite3.Connection, tag: str) -> str:
    """A tag with no definition is REFUSED — and the refusal says HOW to add it.

    `applies_to='crd'` (a typo) would match zero skills and report nothing —
    the exact silent-dead-match defect this workset removes. A tag must be
    DEFINED before it can be used, so the typo fails LOUDLY at seed time.

    DEFECT FOUND BY THE USER (2026-09-21): the refusal used to name the known
    tags and stop there. A worker hitting it had no next step, which is the
    "instuction is not good at all" the user named. A gate that only refuses
    leaves the worker stuck; a gate that refuses AND names the way forward is an
    instruction. So the message now carries the exact call to make.
    """
    known = registered_tags(conn)
    if str(tag) not in known:
        raise FactorError(
            "tag %r is not in capability_tag_registry (known: %s). An undefined "
            "tag matches zero skills SILENTLY — the same defect as reading "
            "prose as a tag.\n"
            "TO ADD IT: skill_factor.register_tag(conn, %r, \"<what the tag "
            "means>\", cite_ref=\"<path:line or a command that ran>\") — a "
            "definition needs a citation, because an invented definition is a "
            "guess with a heading."
            % (tag, ", ".join(known) or "none", str(tag)))
    return str(tag)


def _ensure_factor_unique_indexes(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the COMPOSITE unique indexes on `skill_factor_registry`.

    WHY EXPRESSION INDEXES
    ----------------------
    A generic factor has `skill_key IS NULL`, and SQLite treats NULLs as DISTINCT
    in a UNIQUE index. So `UNIQUE(skill_key, factor_key)` would ALLOW two generic
    factors with the same key — the constraint would silently stop constraining,
    which is worse than no constraint because it looks like one.
    `COALESCE(skill_key,'')` gives the generic case a real value to compare.

    WHY NOT INLINE IN THE DDL
    An expression index cannot be declared inline, and `executescript` runs
    `CREATE INDEX` AFTER `CREATE TABLE IF NOT EXISTS` — which does nothing on an
    existing table, so an inline index would reference columns that are not there
    yet and the whole script would fail (measured twice already in this workset).
    """
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_scope_key "
        "ON skill_factor_registry (COALESCE(skill_key,''), factor_key)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_scope_prefix "
        "ON skill_factor_registry (COALESCE(skill_key,''), proof_prefix)")
    return {"ok": True}


def _rebuild_factor_registry_composite(conn: sqlite3.Connection) -> dict[str, Any]:
    """Drop the legacy single-column UNIQUEs so the key can be COMPOSITE.

    MEASURED DEFECT (2026-09-22)
    ---------------------------
    The live table declares `factor_key TEXT NOT NULL UNIQUE` and
    `proof_prefix TEXT NOT NULL UNIQUE` INLINE. `CREATE TABLE IF NOT EXISTS`
    cannot remove an inline constraint, so the DDL change alone would never take
    effect and the register would stay single-dimensional.

    THE RECIPE, AND WHY EACH PART IS REQUIRED
    -----------------------------------------
    * `legacy_alter_table=ON` — with it OFF (the modern default), SQLite REWRITES
      a child's FK to follow a RENAME. `skill_factor_proof.factor_ref` and
      `skill_factor_proof_log.factor_ref` reference `factor_id`, so a rename
      would repoint them at the temp table name and the drop would be blocked
      with `FOREIGN KEY constraint failed`. This exact failure destroyed 25 rows
      in `wording_registry` earlier in this workset.
    * `foreign_keys=OFF` — SQLite cannot toggle FK enforcement inside a
      transaction, so it must be set outside one.
    * The DATA is settled under one known name BEFORE anything is dropped, and a
      guard refuses to drop a table that still holds rows.

    The proofs reference `factor_id` (a surrogate), NOT `factor_key`, so the
    natural key can change without touching them — verified before writing this.
    """
    if not _table_exists(conn, "skill_factor_registry"):
        return {"rebuilt": False, "reason": "table absent"}
    # A LEFTOVER TEMP TABLE IS ITSELF THE TRIGGER.
    #
    # DEFECT FOUND BY RUNNING IT (2026-09-22): this detection only looked for a
    # legacy index on the NEW table. After a failed attempt the new table is
    # created from the CURRENT DDL, so it has NO legacy index — the broken state
    # was therefore INVISIBLE and the repair never ran. Measured: 39 rows sat in
    # `_skill_factor_registry_old` while the function reported "no legacy
    # single-column UNIQUE" and did nothing.
    #
    # This is the SAME defect I had already fixed in
    # `db_schema._rebuild_wording_registry_unique` and failed to carry over. The
    # lesson is that a repair path must be triggered by the DAMAGE, not only by
    # the condition that caused it.
    stale_exists = _table_exists(conn, "_skill_factor_registry_old")
    legacy = stale_exists
    for idx in conn.execute("PRAGMA index_list(skill_factor_registry)").fetchall():
        if not idx[2]:
            continue
        cols = [r[2] for r in conn.execute("PRAGMA index_info('%s')" % idx[1])]
        if cols in (["factor_key"], ["proof_prefix"]):
            legacy = True
            break
    if not legacy:
        _ensure_factor_unique_indexes(conn)
        conn.commit()
        return {"rebuilt": False, "reason": "no legacy single-column UNIQUE"}

    tmp = "_skill_factor_registry_old"
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    try:
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("PRAGMA legacy_alter_table = ON")
        # Settle the DATA under `tmp`, whichever name it is under. If the temp
        # table already exists it HOLDS the rows, so the canonical name is the
        # empty shell left by the failed attempt.
        if not stale_exists:
            conn.execute("DROP TABLE IF EXISTS %s" % tmp)
            conn.execute("ALTER TABLE skill_factor_registry RENAME TO %s" % tmp)
        if _table_exists(conn, "skill_factor_registry"):
            held = conn.execute("SELECT COUNT(*) FROM skill_factor_registry").fetchone()[0]
            if held:
                raise RuntimeError(
                    "factor register rebuild: refusing to drop a table holding "
                    "%d rows. The data source must be settled first." % held)
            conn.execute("DROP TABLE skill_factor_registry")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % tmp)]
        conn.executescript(FACTOR_registry_DDL)
        shared = [c for c in cols if c in _table_columns(conn, "skill_factor_registry")]
        if not shared:
            raise RuntimeError(
                "factor register rebuild: no shared column between the legacy "
                "table %s and the canonical DDL; refusing to drop the data."
                % sorted(cols))
        conn.execute(
            "INSERT INTO skill_factor_registry (%s) SELECT %s FROM %s"
            % (", ".join(shared), ", ".join(shared), tmp))
        conn.execute("DROP TABLE %s" % tmp)
        _ensure_factor_unique_indexes(conn)
        conn.commit()
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")
    moved = conn.execute("SELECT COUNT(*) FROM skill_factor_registry").fetchone()[0]
    return {"rebuilt": True, "rows": int(moved),
            "note": "factor_key is now unique PER SCOPE, not globally"}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(FACTOR_registry_DDL)
    conn.executescript(FACTOR_PROOF_DDL)
    conn.executescript(FACTOR_PROOF_LOG_DDL)
    conn.executescript(CROSS_CHECK_DDL)
    conn.executescript(CAPABILITY_TAG_DDL)
    # The change log: every create/update carries a REASON, and the reason is
    # filed as a lesson. A register that holds only current state cannot say why
    # a rule exists or what it replaced.
    from db_schema import FACTOR_CHANGE_LOG_DDL
    conn.executescript(FACTOR_CHANGE_LOG_DDL)
    # ADDITIVE: the distillation ROUND columns. `CREATE TABLE IF NOT EXISTS`
    # cannot add them to a table created before they existed, so the round index
    # would be missing exactly on the databases that already have change rows —
    # and every per-round count would be wrong there.
    cl_cols = {r[1] for r in conn.execute("PRAGMA table_info(factor_change_log)")}
    if "round_index" not in cl_cols:
        conn.execute("ALTER TABLE factor_change_log ADD COLUMN "
                     "round_index INTEGER NOT NULL DEFAULT 0")
    if "round_verdict" not in cl_cols:
        conn.execute("ALTER TABLE factor_change_log ADD COLUMN "
                     "round_verdict TEXT NOT NULL DEFAULT 'NA'")
    # THE INDEX IS CREATED HERE, AFTER THE ALTERS, AND UNCONDITIONALLY.
    #
    # DEFECT FOUND BY RUNNING IT (2026-09-21): the index used to live inside
    # `FACTOR_CHANGE_LOG_DDL`, which `executescript` runs BEFORE these ALTERs. On
    # a legacy DB it therefore raised `no such column: round_index` and
    # `ensure_schema` died before reaching the repair — the fix was unreachable
    # from the very path that needed it. It was also created only INSIDE the
    # `if "round_index" not in cl_cols` branch, so a DB that already had the
    # column but not the index would never get one.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_factor_change_round "
                 "ON factor_change_log (factor_key, round_index)")
    # ADDITIVE migration for a DB created before invalidation existed. A column
    # added here is NOT NULL DEFAULT 'NA', so an existing row reads as "not
    # invalidated" without a rewrite — the same shape as the no_null standard.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(skill_factor_proof)")}
    for col in ("invalidated_at", "invalidated_reason"):
        if col not in cols:
            conn.execute("ALTER TABLE skill_factor_proof ADD COLUMN %s TEXT "
                         "NOT NULL DEFAULT 'NA'" % col)
    # ADDITIVE: the REAL tag column. `taxonomy_path` was the WRONG source — it
    # is a canonical ontology path with hard validation, not a tag. This column
    # is added here (the reader's own module) so it exists wherever the reader
    # runs, including a DB created before it was declared in the DDL.
    if _table_exists(conn, "skill_registry"):
        sk_cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(skill_registry)")}
        if "capability_tags" not in sk_cols:
            conn.execute("ALTER TABLE skill_registry ADD COLUMN "
                         "capability_tags TEXT NOT NULL DEFAULT '%s'" % NA_TAG)
    # ADDITIVE: the per-skill factor column. A DB created before it existed has
    # only generic factors, so NULL (the default) is the correct reading for
    # every existing row -- no rewrite needed.
    f_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(skill_factor_registry)")}
    if "skill_key" not in f_cols:
        conn.execute("ALTER TABLE skill_factor_registry ADD COLUMN skill_key TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_skill_factor_skill "
                     "ON skill_factor_registry (skill_key, is_active, sort_order)")
    # NOTE: there is deliberately NO `cite_ref` column. The temptation is to add
    # one so an UPDATE can read a factor's citation back, but the citation is
    # already held by `factor_change_log` (append-only, one row per create and
    # update). A column would DUPLICATE it in a mutable row, and it would also
    # break the invariant `_proof_skill_factor` asserts: "the register has a
    # FIXED column set (a new factor is a ROW)". Measured: adding it took the
    # column count from 16 to 17 and failed that guard — the guard was right.
    #
    # ADDITIVE: the EXPERIENCE columns. A database created before them has no
    # recorded experience, and '[]' / 0 / 'NA' is the honest reading — NOT an
    # invented history.
    for col, decl in (("experience_log", "TEXT NOT NULL DEFAULT '[]'"),
                      ("experience_n", "INTEGER NOT NULL DEFAULT 0"),
                      ("rewritten_at", "TEXT NOT NULL DEFAULT 'NA'")):
        if col not in f_cols:
            conn.execute("ALTER TABLE skill_factor_registry ADD COLUMN %s %s"
                         % (col, decl))
    # ADDITIVE: the cross-check requirement. A DB created before it existed has
    # no factor that requires a cross-check, and 0 (the default) is the honest
    # reading — NOT an invented requirement. `CREATE TABLE IF NOT EXISTS` cannot
    # add a column to an existing table, so without this the gate would read a
    # missing column and every `record_proof` would fail on a legacy DB.
    if "requires_cross_check" not in f_cols:
        conn.execute("ALTER TABLE skill_factor_registry ADD COLUMN "
                     "requires_cross_check INTEGER NOT NULL DEFAULT 0")
    # THE COMPOSITE KEY. Runs AFTER the additive columns, because the rebuild
    # copies the table and must copy every column that exists. On a legacy DB
    # this drops the inline single-column UNIQUEs; on a fresh DB it is a no-op
    # that just creates the expression indexes.
    _rebuild_factor_registry_composite(conn)
    seed_tag_registry(conn)
    seed_factor_template(conn)
    conn.commit()


def seed_factor_template(conn: sqlite3.Connection) -> dict[str, Any]:
    """Populate an EMPTY factor template from the seed. Idempotent.

    The template is DATA, so it is seeded once and then the TABLE is the truth —
    the same pattern as `seed_tag_registry`. An existing row is LEFT ALONE, so a
    human-edited `why` is not clobbered on the next `ensure_schema()`.

    THE `rule` COLUMN IS ADDED ADDITIVELY. MEASURED: `CREATE TABLE IF NOT EXISTS`
    does NOT add a column to an existing table, so the live `factor_template`
    (9 rows, no `rule`) would keep its old shape and every read of the rule would
    fail with "no such column". The same trap already recorded for `llm_model`
    and `llm_service.needs_flag`.

    The `rule` is BACKFILLED for rows that predate it, from the seed — but only
    where the current value is the default `NA`, so a human edit is never
    clobbered.
    """
    from db_schema import (FACTOR_TEMPLATE_ADDITIVE_COLUMNS,
                           FACTOR_TEMPLATE_DDL, FACTOR_TEMPLATE_SEED)
    conn.executescript(FACTOR_TEMPLATE_DDL)

    # ADDITIVE: the column may not exist on a legacy table.
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(factor_template)")}
    added_cols = 0
    if cols and "rule" not in cols:
        for col, decl in FACTOR_TEMPLATE_ADDITIVE_COLUMNS:
            conn.execute("ALTER TABLE factor_template ADD COLUMN %s %s"
                         % (col, decl))
            added_cols += 1

    added = 0
    for i, (field, kind, why, rule) in enumerate(FACTOR_TEMPLATE_SEED, 1):
        row = conn.execute("SELECT field_name, rule FROM factor_template WHERE "
                           "field_name=?", (field,)).fetchone()
        if row:
            # BACKFILL the rule ONLY on the run that ADDED the column.
            #
            # MEASURED DEFECT in the first version: the backfill ran on EVERY
            # seed, so a human who set a rule to `NA` had it silently restored on
            # the next `ensure_schema()`. The proof caught it — "setting the rule
            # to NA makes the SAME unit PASS" FAILED, because the seed put the
            # rule back. A migration must run ONCE; a rule that re-asserts itself
            # on every read is not a rule, it is a hardcode with extra steps.
            if added_cols and str(row["rule"] or "NA").strip() in ("", "NA") \
                    and rule != "NA":
                conn.execute("UPDATE factor_template SET rule=?, "
                             "updated_at=CURRENT_TIMESTAMP WHERE field_name=?",
                             (rule, field))
            continue
        conn.execute("INSERT INTO factor_template (field_name, kind, why, rule, "
                     "sort_order) VALUES (?,?,?,?,?)", (field, kind, why, rule, i))
        added += 1
    conn.commit()
    return {"added": added, "added_columns": added_cols,
            "total": conn.execute("SELECT COUNT(*) FROM factor_template")
            .fetchone()[0]}


def template_rules(conn: sqlite3.Connection) -> dict[str, str]:
    """`{field_name: rule}` — the CHECK each field must satisfy, from the TABLE.

    WHY THIS EXISTS (the user, 2026-09-23):

        "definition for key factor output = can measured unit"
        "if not, key factor is meaningless"

    MEASURED before this: that rule lived in
    `factor_first_principle.assert_measurable()` — CODE. So the rule could not be
    read, extended, or cited from the table, and a second module that needed the
    same rule would have to re-implement it (the drift this repo keeps hitting).

    Now the rule is a ROW. A gate reads it, so adding a rule is an INSERT.
    """
    seed_factor_template(conn)
    return {str(r["field_name"]): str(r["rule"] or "NA")
            for r in conn.execute(
                "SELECT field_name, rule FROM factor_template "
                "WHERE is_active=1 ORDER BY sort_order, field_name")}


def template_fields(conn: sqlite3.Connection) -> list[str]:
    """The REQUIRED factor fields, read from the template TABLE.

    Read from the table, not from a Python list: a template that lives in code
    cannot be extended without a code change, which is the defect this table
    exists to remove.
    """
    seed_factor_template(conn)
    return [str(r[0]) for r in conn.execute(
        "SELECT field_name FROM factor_template WHERE is_required=1 "
        "AND is_active=1 ORDER BY sort_order, field_name")]


def record_experience(conn: sqlite3.Connection, factor_key: str, *,
                      lesson: str, cite_ref: str) -> dict[str, Any]:
    """Append a lesson to a factor's OWN experience, and rewrite the factor.

    THE HUMAN'S MODEL: "experience rewrite". The factor is rewritten by its own
    accumulated experience — so the experience is a FIELD on the factor, and
    appending to it is a rewrite of that factor, not a new row somewhere else.

    REFUSES an uncited lesson: an experience with no citation is a memory, and a
    memory cannot be checked.
    """
    import citation_discipline as cd
    cd.assert_cited({"evidence_ref": cite_ref})
    text = str(lesson or "").strip()
    if not text:
        raise FactorError("record_experience: lesson is required")
    ensure_schema(conn)
    row = conn.execute("SELECT experience_log, experience_n FROM "
                       "skill_factor_registry WHERE factor_key=?",
                       (factor_key,)).fetchone()
    if not row:
        raise FactorError("record_experience: factor %r is not registered"
                          % factor_key)
    try:
        log = json.loads(row["experience_log"] or "[]")
    except (json.JSONDecodeError, TypeError):
        log = []
    if not isinstance(log, list):
        log = []
    log.append({"lesson": text, "cite_ref": cite_ref})
    conn.execute(
        "UPDATE skill_factor_registry SET experience_log=?, experience_n=?, "
        "rewritten_at=datetime('now'), updated_at=datetime('now') "
        "WHERE factor_key=?",
        (json.dumps(log, ensure_ascii=False), len(log), factor_key))
    conn.commit()
    return {"ok": True, "factor_key": factor_key, "experience_n": len(log),
            "cite_ref": cite_ref}


def factor_experience(conn: sqlite3.Connection, factor_key: str) -> list[dict]:
    """A factor's accumulated experience, oldest first."""
    ensure_schema(conn)
    row = conn.execute("SELECT experience_log FROM skill_factor_registry "
                       "WHERE factor_key=?", (factor_key,)).fetchone()
    if not row:
        return []
    try:
        log = json.loads(row["experience_log"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return log if isinstance(log, list) else []


def invalidate_proof(conn: sqlite3.Connection, skill_key: str, factor_key: str,
                     *, reason: str) -> dict[str, Any]:
    """Stop a proof counting, WITHOUT deleting it.

    A proof recorded for a factor that does not apply is a measurement of the
    wrong object. It must stop counting, but deleting it destroys the evidence
    that the gate was missing — so the row stays and is marked. `reason` is
    required: an invalidation with no reason is indistinguishable from a
    deletion.
    """
    if not str(reason or "").strip():
        raise FactorError("invalidate_proof: a reason is required — an "
                          "invalidation with no reason is a deletion")
    row = conn.execute(
        "SELECT p.proof_id FROM skill_factor_proof p "
        "JOIN skill_registry s ON s.skill_id = p.skill_ref "
        "JOIN skill_factor_registry f ON f.factor_id = p.factor_ref "
        "WHERE s.skill_key=? AND f.factor_key=?", (skill_key, factor_key)
    ).fetchone()
    if not row:
        raise FactorError("no proof for %s / %s" % (skill_key, factor_key))
    conn.execute("UPDATE skill_factor_proof SET invalidated_at=datetime('now'), "
                 "invalidated_reason=? WHERE proof_id=?", (str(reason), row[0]))
    conn.commit()
    return {"skill_key": skill_key, "factor_key": factor_key,
            "proof_id": row[0], "invalidated": True, "reason": str(reason)}


def validate_factor_tags(conn: sqlite3.Connection) -> dict[str, Any]:
    """Audit EVERY row in the register against the tag vocabulary.

    DEFECT FOUND BY RUNNING THE PROOF: `seed_factors()` gates only the factors in
    the `FACTORS` tuple. A row inserted into `skill_factor_registry` by any other
    path — a migration, a repair script, a direct INSERT — was NEVER checked, so
    an unregistered `applies_to` could sit in the table and match zero skills
    silently. That is the exact defect the vocabulary gate exists to prevent, and
    it was reachable by simply not going through the seed.

    A gate that only guards its own front door is not a gate. This audits the
    TABLE, so the property holds however the row got there.
    """
    known = set(registered_tags(conn))
    bad = []
    for r in conn.execute("SELECT factor_key, applies_to FROM "
                          "skill_factor_registry WHERE is_active=1"):
        want = str(r["applies_to"])
        if want != APPLIES_ALL and want not in known:
            bad.append({"factor_key": str(r["factor_key"]), "applies_to": want})
    return {"ok": not bad, "bad": bad, "known_tags": sorted(known)}


def seed_factors(conn: sqlite3.Connection) -> dict[str, Any]:
    """Register the 19 factors. Idempotent: an existing key is UPDATED, not
    duplicated, so a corrected rule_definition reaches the register."""
    ensure_schema(conn)
    inserted, updated = 0, 0
    for f in FACTORS:
        if f["metric_kind"] not in METRIC_KINDS:
            raise FactorError("factor %s: unknown metric_kind %r"
                              % (f["factor_key"], f["metric_kind"]))
        if not str(f["metric_target"]).strip():
            raise FactorError("factor %s: metric_target is empty — a factor with "
                              "no target cannot be scored" % f["factor_key"])
        # THE VOCABULARY GATE. `applies_to` is matched against a skill's tags, so
        # an UNDEFINED value matches zero skills and reports nothing. A typo
        # (`crd`) must fail HERE, loudly, rather than silently deactivate a
        # factor. `*` is the one value that is not a tag.
        if str(f["applies_to"]) != APPLIES_ALL:
            assert_known_tag(conn, str(f["applies_to"]))
        # THE LOOKUP IS SCOPED. With a composite key, `factor_key` alone is no
        # longer unique, so an unscoped lookup would silently pick one of several
        # rows and UPDATE the wrong scope. `COALESCE(skill_key,'')` matches the
        # unique index exactly, so the generic case is found too.
        row = conn.execute(
            "SELECT factor_id FROM skill_factor_registry WHERE "
            "COALESCE(skill_key,'')=COALESCE(?,'') AND factor_key=?",
            (f.get("skill_key"), f["factor_key"])).fetchone()
        if row:
            conn.execute(
                "UPDATE skill_factor_registry SET name=?, rule_definition=?, "
                "action=?, metric_kind=?, metric_unit=?, metric_target=?, "
                "proof_prefix=?, applies_to=?, sort_order=?, "
                "updated_at=datetime('now') WHERE factor_id=?",
                (f["name"], f["rule_definition"], f["action"], f["metric_kind"],
                 f.get("metric_unit", ""), f["metric_target"], f["proof_prefix"],
                 f["applies_to"], f["sort_order"], int(row["factor_id"])))
            updated += 1
        else:
            conn.execute(
                "INSERT INTO skill_factor_registry (factor_key, name, "
                "rule_definition, action, metric_kind, metric_unit, "
                "metric_target, proof_prefix, applies_to, sort_order, skill_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f["factor_key"], f["name"], f["rule_definition"], f["action"],
                 f["metric_kind"], f.get("metric_unit", ""), f["metric_target"],
                 f["proof_prefix"], f["applies_to"], f["sort_order"],
                 f.get("skill_key")))
            inserted += 1
    conn.commit()
    # THE TABLE-LEVEL GATE. The loop above only checks the factors in the
    # `FACTORS` tuple; a row that arrived by another path was never checked.
    audit = validate_factor_tags(conn)
    if not audit["ok"]:
        raise FactorError(
            "skill_factor_registry holds an applies_to that is not a registered "
            "tag: %s (known: %s). An undefined tag matches zero skills "
            "SILENTLY. Register it with skill_factor.register_tag(conn, "
            "<tag>, \"<meaning>\", cite_ref=\"<path:line>\")."
            % (", ".join("%s=%s" % (b["factor_key"], b["applies_to"])
                         for b in audit["bad"]),
               ", ".join(audit["known_tags"]) or "none"))
    return {"inserted": inserted, "updated": updated,
            "total": conn.execute("SELECT COUNT(*) FROM "
                                  "skill_factor_registry").fetchone()[0]}


def proof_artifact_id(skill_key: str, factor_key: str) -> str:
    """A DETERMINISTIC, unique artifact id: `proof_<skill>_<factor>_<hash8>`.

    Deterministic so the same proof can be looked up without a table scan, and
    hashed so a long skill_key cannot collide with a factor_key by concatenation
    (`a_b` + `c` vs `a` + `b_c`).
    """
    h = hashlib.sha256(("%s|%s" % (skill_key, factor_key)).encode("utf-8")
                       ).hexdigest()[:8]
    return "proof_%s_%s_%s" % (skill_key, factor_key, h)


def applicable_factors(conn: sqlite3.Connection, skill_key: str) -> list[dict]:
    """The factors that apply to a skill.

    `applies_to` is matched against the skill's own TAGS, so a UI skill is not
    asked for a SAST gate. A factor that applies to nothing is decorative, so the
    match is REPORTED rather than assumed.

    DEFECT FOUND BY MEASURING IT: the tag source was `skill_registry.description`
    — a PROSE field. Measured: 0 of 48 descriptions look like a tag, so
    `applies_to='code'` and `applies_to='crud'` matched NOTHING and 8 of the 19
    factors applied to zero skills. The mechanism was dead, and it was dead
    SILENTLY: `applicable_factors` returned a shorter list and nothing said why.

    THE SECOND DEFECT, ALSO MINE: the tag source was then pointed at
    `taxonomy_path`. That is a CATEGORY ERROR — `taxonomy_path` is a canonical
    ontology path (`module/task_center`, `db_field/code_registry|register_id`)
    that `skill_contract_store.validate_taxonomy_path` HARD-VALIDATES against the
    ontology registry. It is not a tag, and it is empty for every skill.

    The tag source is now `capability_tags`, a column DECLARED to hold tags. It
    is COMMA-SEPARATED, so one skill can be both `code` and `crud`. Prose is not
    a tag; a path is not a tag; a field that says it holds a tag is.
    """
    tags = {APPLIES_ALL}
    row = conn.execute("SELECT capability_tags FROM skill_registry WHERE "
                       "skill_key=?", (skill_key,)).fetchone()
    tag_source_empty = True
    if row and row[0] and str(row[0]).strip() and str(row[0]).strip() != NA_TAG:
        tag_source_empty = False
        for part in str(row[0]).split(","):
            if part.strip() and part.strip() != NA_TAG:
                tags.add(part.strip().lower())
    out = []
    for f in conn.execute("SELECT * FROM skill_factor_registry WHERE is_active=1 "
                          "ORDER BY sort_order"):
        # A factor applies when EITHER:
        #   * it is GENERIC (skill_key IS NULL) and its tag matches, OR
        #   * it names THIS skill exactly.
        #
        # The two are different axes and must not be merged: `applies_to` is a
        # CAPABILITY shared by many skills, `skill_key` is an IDENTITY naming
        # one. A per-skill factor is NOT a tag with one member -- a tag can be
        # added to a second skill by editing that skill's tags, whereas a
        # skill_key factor belongs to exactly one skill by construction.
        own = f["skill_key"] if "skill_key" in f.keys() else None
        if own:
            if str(own) == str(skill_key):
                out.append(dict(f))
            continue
        want = str(f["applies_to"])
        if want == APPLIES_ALL or want in tags:
            out.append(dict(f))
    return out


def register_factor(
    conn: sqlite3.Connection,
    *,
    factor_key: str,
    name: str,
    rule_definition: str,
    action: str,
    metric_kind: str,
    metric_unit: str,
    metric_target: str,
    proof_prefix: str,
    skill_key: str | None = None,
    applies_to: str = APPLIES_ALL,
    sort_order: int = 0,
    cite_ref: str = "",
    requires_cross_check: int = 0,
) -> dict[str, Any]:
    """Register a factor. `skill_key` makes it SPECIFIC to one skill.

    WHY THIS EXISTS (measured 2026-09-21)
    -------------------------------------
    The register could only be populated by editing the `FACTORS` list in this
    file. That is the same defect the user named for tags: "先登記 tag (this is
    the problem for your job, or instruction is not good at all)". A register
    whose write path is a Python edit is not DB-driven, and a per-skill factor
    could not be added at all.

    REFUSES, rather than storing something unusable:
      * an unknown `metric_kind` -- a metric that cannot be scored is decorative
      * an empty `metric_unit` -- a number without a unit cannot be audited
      * an unknown `applies_to` tag -- an undefined tag matches zero skills
        SILENTLY, which is the defect `assert_known_tag` already guards
      * a `skill_key` that is not in `skill_registry` -- a factor for a skill
        that does not exist can never be measured
      * a `cite_ref` that is not checkable -- no citation, no row

    `skill_key` and `applies_to` are MUTUALLY EXCLUSIVE in intent: a per-skill
    factor ignores `applies_to`. Passing both is allowed but the skill_key wins,
    and that is stated here so a reader is not surprised.
    """
    import citation_discipline as cd

    key = str(factor_key or "").strip()
    if not key:
        raise FactorError("register_factor: factor_key is required")
    if str(metric_kind) not in METRIC_KINDS:
        raise FactorError(
            "register_factor: metric_kind %r is not one of %s. A metric that "
            "cannot be scored is decorative."
            % (metric_kind, ", ".join(METRIC_KINDS)))
    if not str(metric_unit or "").strip():
        raise FactorError(
            "register_factor: metric_unit is required. A number without a unit "
            "cannot be audited -- '0' is a pass for a vulnerability count and a "
            "FAIL for a test pass rate.")
    if not str(proof_prefix or "").strip():
        raise FactorError("register_factor: proof_prefix is required")
    if not str(cite_ref or "").strip():
        raise FactorError(
            "register_factor: cite_ref is required. No citation, no row -- an "
            "invented factor is a guess with a heading.")
    # `citation_discipline.assert_cited` takes a FINDING DICT, not a string
    # (measured: passing a string raises "finding is not a dict"). The citation
    # is wrapped so the one gate is reused rather than re-implemented -- a
    # second checker would drift from the first.
    cd.assert_cited({"evidence_ref": cite_ref})

    sk = str(skill_key or "").strip() or None
    if sk:
        exists = conn.execute("SELECT 1 FROM skill_registry WHERE skill_key=?",
                              (sk,)).fetchone()
        if not exists:
            raise FactorError(
                "register_factor: skill_key %r is not in skill_registry. A "
                "factor for a skill that does not exist can never be measured."
                % sk)
    else:
        assert_known_tag(conn, applies_to)

    # ---- THE 5W1H MEASURABILITY GATE (added 2026-09-28) -------------------
    #
    # MEASURED DEFECT, and it was MY OWN WORK. `factor_first_principle.
    # audit_registry(conn)` reported `factors 65 · measurable 54 · NOT
    # measurable 11`, and BOTH factors I had filed earlier the same day were
    # among the 11. Every one shared a single reason:
    #
    #     metric_unit 'claims with an uncheckable citation' names NO SUBJECT.
    #
    # The gate EXISTED (`factor_first_principle.assert_measurable`) and the rows
    # were written WITHOUT it. A gate that a writer may skip is not a gate — the
    # identical defect `record_proof` already fixed for `applies_to` ("a rule
    # stated but not enforced at the write site"). So the check is HERE.
    #
    # WHY IT IS SKIPPABLE, AND DEFAULTS TO ON: an empty `metric_unit` is ALREADY
    # refused above, so this only sees a NON-EMPTY unit — exactly the case that
    # produced the 11. A valve is provided because one existing test
    # (`_proof_skill_factors` or a seeded fixture) may register a factor before
    # this gate existed; it FAILS CLOSED on an unknown value, the same shape as
    # `TERMINOLOGY_LANGUAGE_GATE`.
    import os as _os
    if _os.environ.get("FACTOR_MEASURABLE_GATE", "enforce").strip().lower() != "report":
        try:
            import factor_first_principle as _fp
            verdict = _fp.audit_factor(
                {"factor_key": key, "metric_kind": metric_kind,
                 "metric_unit": metric_unit, "metric_target": metric_target}, conn)
            if not verdict.get("measurable"):
                raise FactorError(
                    "register_factor: factor %r is not MEASURABLE — %s. "
                    "A factor that names no 5W1H `what` SUBJECT is not one "
                    "factor; it is an unmeasurable rule. Write the unit as "
                    "`<kind> of <subject>` (a count of WHAT)."
                    % (key, "; ".join(verdict.get("reasons") or [])))
        except FactorError:
            raise
        except ImportError:  # pragma: no cover - module always ships together
            pass

    ensure_schema(conn)
    # THE LOOKUP IS SCOPED. With a composite key, `factor_key` alone is no longer
    # unique, so an unscoped lookup would silently pick one of several rows and
    # UPDATE the wrong scope. `COALESCE(skill_key,'')` matches the unique index
    # exactly, so the generic case is found too.
    row = conn.execute(
        "SELECT factor_id FROM skill_factor_registry WHERE "
        "COALESCE(skill_key,'')=COALESCE(?,'') AND factor_key=?",
        (sk, key)).fetchone()
    if row:
        conn.execute(
            "UPDATE skill_factor_registry SET name=?, rule_definition=?, "
            "action=?, metric_kind=?, metric_unit=?, metric_target=?, "
            "proof_prefix=?, applies_to=?, skill_key=?, sort_order=?, "
            "cite_ref=?, requires_cross_check=?, updated_at=datetime('now') "
            "WHERE factor_id=?",
            (name, rule_definition, action, metric_kind, metric_unit,
             metric_target, proof_prefix, applies_to, sk, int(sort_order),
             str(cite_ref).strip(), 1 if int(requires_cross_check or 0) else 0,
             int(row["factor_id"])))
        conn.commit()
        return {"ok": True, "created": False, "factor_id": row["factor_id"],
                "factor_key": key, "skill_key": sk}
    cur = conn.execute(
        "INSERT INTO skill_factor_registry (factor_key, name, rule_definition, "
        "action, metric_kind, metric_unit, metric_target, proof_prefix, "
        "applies_to, skill_key, sort_order, cite_ref, requires_cross_check) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key, name, rule_definition, action, metric_kind, metric_unit,
         metric_target, proof_prefix, applies_to, sk, int(sort_order),
         str(cite_ref).strip(), 1 if int(requires_cross_check or 0) else 0))
    conn.commit()
    return {"ok": True, "created": True, "factor_id": cur.lastrowid,
            "factor_key": key, "skill_key": sk}


def record_cross_check(conn: sqlite3.Connection, factor_key: str, *,
                       reader_a: dict, reader_b: dict,
                       count_a: Any, count_b: Any, population_size: Any,
                       status: str, cite_ref: str,
                       skill_key: str = "") -> dict[str, Any]:
    """Record ONE independent cross-check of a factor's number. APPEND-ONLY.

    WHY THIS IS A SEPARATE WRITE FROM `record_proof` (2026-09-29)
    ------------------------------------------------------------
    The cross-check is EVIDENCE that a number was reproduced by a second,
    independent reader. It is written BEFORE the proof, and `record_proof`
    REFUSES a `requires_cross_check=1` factor until an `AGREED` row exists here.
    Keeping them separate means the verification survives a later re-measurement
    (the proof row is UPDATEd; this log is not), so "was this number ever
    verified?" is answerable.

    `status` is the verdict of `measurement_cross_check.cross_check()`. Only
    `AGREED` satisfies the gate, but a non-AGREED row is still RECORDED — a
    refusal that leaves no trace is the defect this whole workset removes.
    """
    ensure_schema(conn)
    a = reader_a or {}
    b = reader_b or {}
    cur = conn.execute(
        "INSERT INTO measurement_cross_check (factor_key, skill_key, "
        "reader_a_name, reader_a_method, reader_a_source, "
        "reader_b_name, reader_b_method, reader_b_source, "
        "count_a, count_b, population_size, status, cite_ref) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(factor_key), str(skill_key or ""),
         str(a.get("name") or ""), str(a.get("method") or ""),
         str(a.get("data_source") or ""),
         str(b.get("name") or ""), str(b.get("method") or ""),
         str(b.get("data_source") or ""),
         str(count_a), str(count_b), str(population_size),
         str(status), str(cite_ref)))
    conn.commit()
    return {"ok": True, "cross_check_id": cur.lastrowid,
            "factor_key": str(factor_key), "status": str(status)}


def tag_source_empty(conn: sqlite3.Connection) -> dict[str, Any]:
    """Report whether ANY skill declares a tag, so a dead match is visible.

    A scoped factor that matches nothing is decorative. This measures that
    rather than leaving it to be discovered by a shorter list.
    """
    n = conn.execute("SELECT COUNT(*) FROM skill_registry WHERE "
                     "capability_tags IS NOT NULL AND TRIM(capability_tags) <> '' "
                     "AND TRIM(capability_tags) <> ?", (NA_TAG,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM skill_registry").fetchone()[0]
    scoped = conn.execute("SELECT COUNT(*) FROM skill_factor_registry WHERE "
                          "is_active=1 AND applies_to <> ?",
                          (APPLIES_ALL,)).fetchone()[0]
    return {"skills_with_a_tag": n, "skills_total": total,
            "scoped_factors": scoped,
            "empty": n == 0,
            "note": ("no skill declares a capability_tags value, so every scoped "
                     "factor applies to zero skills" if n == 0 else "")}


def proof_scope(conn: sqlite3.Connection, factor_key: str, *,
                reader: str, reader_cite: str, count_command: str,
                population_count: Any = 1,
                population: str = "", population_cite: str = "",
                table_count: Any = None,
                population_stated: bool = True) -> dict[str, Any]:
    """Build the SCOPE a proof must carry, from data the caller already holds.

    The READER and the COMMAND are the CALLER's — the thing that produced the
    number. The POPULATION defaults to the factor's own row, because a proof
    measures a factor; a caller that measured a different set passes it.

    This is a BUILDER, not a bypass: it calls `measurement_scope.scope_finding`,
    so a caller that supplies an empty reader, an uncited population, or no
    command gets an UNSCOPED result and `record_proof` REFUSES it. It exists so
    every caller does not re-derive the same four steps, not so a caller can
    skip them.
    """
    import measurement_scope as _ms
    if not str(population or "").strip():
        population = "skill_factor_registry WHERE factor_key=%s" % factor_key
        population_cite = population_cite or "skill_factor.py:proof_scope"
    if table_count is None:
        table_count = conn.execute(
            "SELECT COUNT(*) FROM skill_factor_registry").fetchone()[0]
    return _ms.scope_finding(
        reader=reader, reader_cite=reader_cite,
        population=population, population_cite=population_cite,
        population_count=population_count, table_count=table_count,
        count_command=count_command, population_stated=population_stated)


def record_proof(conn: sqlite3.Connection, skill_key: str, factor_key: str, *,
                 metric_value: Any, evidence_ref: str,
                 scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Store ONE measured proof. Refuses a proof with no measurement.

    `metric_pass` is DERIVED from the factor's metric_kind and target, so a
    caller cannot assert a pass — it must supply a value that IS a pass.

    `scope` is the measurement's POPULATION, checked by
    `measurement_scope.assert_scoped` BEFORE the row is written. A count of the
    wrong population is the 2026-09-27 defect (75 counted where the reader
    returns 4), and a gate that is never called at the write site is not a gate.

    REQUIRED (2026-09-29). The first version made it OPTIONAL, and MEASURED:
    both production callers and every proof caller passed no scope, so the gate
    never ran on any real path — the finding was CLOSED while the defect stayed.
    A conditional gate is not a gate. `scope=None` is now REFUSED, so the check
    runs on every write or the write does not happen.
    """
    ensure_schema(conn)
    sk = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                      (skill_key,)).fetchone()
    if not sk:
        raise FactorError("skill %r is not in skill_registry" % skill_key)
    f = conn.execute("SELECT * FROM skill_factor_registry WHERE factor_key=?",
                     (factor_key,)).fetchone()
    if not f:
        raise FactorError("factor %r is not registered" % factor_key)
    # THE ACTION-POINT GATE. `applies_to` was stated in the register and
    # REPORTED by `applicable_factors`, but nothing enforced it where a proof is
    # WRITTEN — so a proof could be recorded for a factor that does not apply to
    # the skill. Measured: `no_null_standard` carried a proof for
    # `ntd_no_hardcoded_secrets` (applies_to='code') while `applicable_factors`
    # said it did not apply. A rule stated but not enforced at the write site is
    # the exact defect family this workset removes, so the gate is HERE.
    # It reads `applicable_factors`, so when the tag source is fixed the gate
    # follows automatically — one rule, one place.
    if factor_key not in {x["factor_key"]
                          for x in applicable_factors(conn, skill_key)}:
        raise FactorError(
            "factor %s does not apply to skill %s (applies_to=%r). A proof for "
            "an inapplicable factor is a measurement of the wrong object."
            % (factor_key, skill_key, f["applies_to"]))
    if metric_value is None or str(metric_value).strip() == "":
        raise FactorError(
            "factor %s: metric_value is empty. A proof must be QUANTIFIABLE — "
            "descriptive text is not a measurement." % factor_key)
    if not str(evidence_ref or "").strip():
        raise FactorError("factor %s: evidence_ref is required" % factor_key)

    # ---- THE SCOPE GATE (2026-09-29) --------------------------------------
    # `measurement_scope.assert_scoped` is a write-site gate: a finding is KEPT
    # only when its population is NAMED and COUNTED. MEASURED 2026-09-29: it had
    # 0 production callers — only its own proof called it — so the gate existed
    # and enforced nothing. This is the production write site it belongs at,
    # because `record_proof` is where a measurement becomes a stored finding.
    #
    # REQUIRED, NOT CONDITIONAL. The first version skipped the check when
    # `scope is None`, and MEASURED: every caller passed none, so the gate never
    # fired on a real path. A gate that only runs when the caller opts in is the
    # "stated but not enforced" defect wearing the gate's own name. So a missing
    # scope is REFUSED here, and an unscoped scope is REFUSED by
    # `assert_scoped` — never downgraded.
    if scope is None:
        raise FactorError(
            "factor %s: a proof must carry its SCOPE. `scope` is required — a "
            "count of the wrong population is the 2026-09-27 defect (75 counted "
            "where the reader returns 4).\n"
            "TO SATISFY IT: build the scope with measurement_scope.scope_finding("
            "reader=..., reader_cite=..., population=..., population_cite=..., "
            "population_count=..., table_count=..., count_command=..., "
            "population_stated=...) and pass it here." % factor_key)
    import measurement_scope as _ms
    _ms.assert_scoped(scope, cite_ref=str(evidence_ref))

    # ---- THE CROSS-CHECK GATE (2026-09-29) --------------------------------
    # A factor that declares `requires_cross_check=1` is one whose value is a
    # COUNT over a population — exactly the kind that can be wrong-but-plausible.
    # MEASURED: the agent reported 1516 uncovered paths, wrong by a factor of 22,
    # and only a HUMAN question caught it. This gate makes the requirement a
    # MECHANISM: the number must have been reproduced by a SECOND, INDEPENDENT
    # reader before it can be recorded.
    #
    # THE REQUIREMENT IS DATA, NOT A CALLER FLAG. It is read from the factor's
    # own row, so a caller cannot opt out of it by omitting an argument.
    if int(f["requires_cross_check"] or 0) == 1:
        cc = conn.execute(
            "SELECT cross_check_id, status FROM measurement_cross_check "
            " WHERE factor_key = ? AND status = 'AGREED' "
            " ORDER BY cross_check_id DESC LIMIT 1", (factor_key,)).fetchone()
        if not cc:
            raise FactorError(
                "factor %s requires an INDEPENDENT CROSS-CHECK and none is "
                "AGREED. A count over a population can be wrong-but-plausible "
                "(MEASURED: 1516 was wrong by 22x and only a human question "
                "caught it), so the number must be reproduced by a SECOND "
                "reader that differs in NAME, METHOD and DATA SOURCE.\n"
                "TO SATISFY IT: run measurement_cross_check.cross_check() with "
                "two independent readers and record the AGREED row in "
                "measurement_cross_check before recording this proof."
                % factor_key)

    kind, target = f["metric_kind"], str(f["metric_target"])
    v = str(metric_value).strip()
    if kind == MK_BOOL:
        passed = v.lower() in ("true", "1", "yes")
    elif kind == MK_COUNT:
        passed = int(float(v)) <= int(float(target))
    elif kind == MK_PCT:
        passed = float(v) >= float(target)
    elif kind == MK_SCORE:
        passed = float(v) >= float(target)
    else:
        raise FactorError("unknown metric_kind %r" % kind)

    pid = proof_artifact_id(skill_key, factor_key)
    # THE STABLE NATURAL KEY IS `proof_artifact_id`, NOT `(skill_ref, factor_ref)`.
    #
    # DEFECT FOUND BY RUNNING THE PROOF GATE (2026-09-21). `_proof_probe_safety.py`
    # began crashing with:
    #     sqlite3.IntegrityError: UNIQUE constraint failed:
    #         skill_factor_proof.proof_artifact_id
    #
    # The cause: the row was written with `factor_ref=25`, and by the time the
    # test ran again `probe_side_effect_free` had `factor_id=30` while
    # `factor_id=25` was `llm_latency_budget`. So
    #     ON CONFLICT(skill_ref, factor_ref)  -- (9,30) vs the stored (9,25)
    # did NOT match, the INSERT was attempted, and the PRIMARY UNIQUE
    # `proof_artifact_id` (which IS derived from the stable KEYS) rejected it.
    #
    # WORSE THAN A DANGLING FOREIGN KEY, and this is the part that matters: the
    # stale row's `factor_ref=25` still RESOLVES — to a DIFFERENT factor. So
    # `probe_side_effect_free`'s recorded proof was silently re-attributed to
    # `llm_latency_budget`, and an orphan-check saying "0 orphans" was telling
    # the truth about existence and a lie about meaning. A row that points at
    # the wrong thing is not detectable by an existence check.
    #
    # The fix keys the upsert on the STABLE id and REFRESHES `factor_ref`, so the
    # row tracks the current factor_id instead of drifting away from it. An id
    # that can move must never be the key of a record that outlives it.
    # RECONCILE THE DERIVED ID BEFORE THE UPSERT (2026-09-29).
    #
    # `proof_artifact_id` is DERIVED from (skill_key, factor_key). When a factor
    # is RENAMED, a row already stored under the OLD key keeps the OLD id — and
    # the upsert conflicts on `proof_artifact_id`, which no longer matches the
    # current derivation. So the INSERT is attempted and
    # `UNIQUE(skill_ref, factor_ref)` rejects it: the row can NEVER be
    # re-measured.
    #
    # MEASURED: two rows (`environment_registry_check`,
    # `ntd_no_bypass_registry_ontology`) held an id derived from a factor key
    # that had since changed. The id MOVED, and a record keyed on a moved id
    # must follow it. This renames the row to the CURRENT derived id — it does
    # not delete it, so the evidence survives, and the append-only log below is
    # untouched.
    #
    # The rename runs ONLY when the current id is free. If another row already
    # holds it, that is a real collision and the upsert's ON CONFLICT is the
    # right handler — a rename would raise first and mask it.
    _owner = conn.execute(
        "SELECT 1 FROM skill_factor_proof WHERE proof_artifact_id=?",
        (pid,)).fetchone()
    if _owner is None:
        conn.execute(
            "UPDATE skill_factor_proof SET proof_artifact_id=? "
            "WHERE skill_ref=? AND factor_ref=? AND proof_artifact_id<>?",
            (pid, int(sk[0]), int(f["factor_id"]), pid))
    conn.execute(
        "INSERT INTO skill_factor_proof_log (skill_ref, factor_ref, "
        "metric_value, metric_pass, proof_artifact_id, evidence_ref, "
        "measured_at) VALUES (?,?,?,?,?,?,datetime('now'))",
        (int(sk[0]), int(f["factor_id"]), v, 1 if passed else 0, pid,
         str(evidence_ref)))
    conn.execute(
        "INSERT INTO skill_factor_proof (skill_ref, factor_ref, metric_value, "
        "metric_pass, proof_artifact_id, evidence_ref) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(proof_artifact_id) DO UPDATE SET "
        "skill_ref=excluded.skill_ref, factor_ref=excluded.factor_ref, "
        "metric_value=excluded.metric_value, metric_pass=excluded.metric_pass, "
        "evidence_ref=excluded.evidence_ref, measured_at=datetime('now'), "
        # A fresh measurement REVALIDATES: the row was invalidated because it
        # measured the wrong object, and a new measurement is a new object.
        "invalidated_at='NA', invalidated_reason='NA'",
        (int(sk[0]), int(f["factor_id"]), v, 1 if passed else 0, pid,
         str(evidence_ref)))
    conn.commit()
    return {"skill_key": skill_key, "factor_key": factor_key,
            "metric_kind": kind, "metric_value": v, "metric_target": target,
            "metric_pass": bool(passed), "proof_artifact_id": pid}


def proof_history(conn: sqlite3.Connection, skill_key: str,
                  factor_key: str) -> list[dict[str, Any]]:
    """EVERY measurement of a factor, oldest first. The append-only trace.

    Answers "was this ever failing?" — which the current-state row cannot,
    because a fix overwrites the failure.
    """
    ensure_schema(conn)
    sk = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                      (skill_key,)).fetchone()
    f = conn.execute("SELECT factor_id FROM skill_factor_registry WHERE "
                     "factor_key=?", (factor_key,)).fetchone()
    if not sk or not f:
        return []
    # QUERY BY THE STABLE ID, NOT BY `factor_ref`.
    #
    # DEFECT FOUND BY RUNNING IT (2026-09-21): this filtered on
    # `factor_ref = <current factor_id>`. The log rows were written with the
    # factor_id of the day, and that id MOVED when `seed_factors` re-inserted
    # rows — so the history of `probe_side_effect_free` (written as 25, now 30)
    # became invisible and the trace read as EMPTY. An empty trace is the worst
    # possible failure for an append-only log: it looks like "nothing ever
    # happened" when the truth is "the rows are there and the query missed them".
    #
    # `proof_artifact_id` is derived from the stable KEYS, so it does not move.
    pid = proof_artifact_id(skill_key, factor_key)
    return [dict(r) for r in conn.execute(
        "SELECT log_id, metric_value, metric_pass, evidence_ref, measured_at "
        "FROM skill_factor_proof_log WHERE proof_artifact_id=? "
        "ORDER BY log_id", (pid,))]


def skill_table(conn: sqlite3.Connection, skill_key: str) -> dict[str, Any]:
    """THE TABLE REPRESENTATION of a skill — the format that replaces `.md`.

    Every row is a factor with its rule, its action, and its MEASURED proof. A
    factor with no proof row is reported as `UNMEASURED`, never as a pass: an
    absent measurement is not a passing one.
    """
    ensure_schema(conn)
    factors = applicable_factors(conn, skill_key)
    sk = conn.execute("SELECT skill_id FROM skill_registry WHERE skill_key=?",
                      (skill_key,)).fetchone()
    proofs = {}
    invalidated = []
    if sk:
        for p in conn.execute(
                "SELECT factor_ref, metric_value, metric_pass, "
                "proof_artifact_id, evidence_ref, measured_at, "
                "invalidated_at, invalidated_reason "
                "FROM skill_factor_proof WHERE skill_ref=?", (int(sk[0]),)):
            # An INVALIDATED proof does not count. It is kept (never deleted) so
            # the record that the gate was missing survives, but it is not a
            # measurement of this factor any more.
            if str(p["invalidated_at"] or "NA") != "NA":
                invalidated.append({"factor_ref": int(p["factor_ref"]),
                                    "reason": p["invalidated_reason"]})
                continue
            proofs[int(p["factor_ref"])] = dict(p)
    rows = []
    for f in factors:
        p = proofs.get(int(f["factor_id"]))
        rows.append({
            "factor_key": f["factor_key"], "name": f["name"],
            "rule_definition": f["rule_definition"], "action": f["action"],
            "metric_kind": f["metric_kind"], "metric_target": f["metric_target"],
            "metric_value": p["metric_value"] if p else "",
            "metric_pass": (bool(p["metric_pass"]) if p else None),
            "proof_artifact_id": (p["proof_artifact_id"] if p
                                  else proof_artifact_id(skill_key,
                                                         f["factor_key"])),
            "evidence_ref": p["evidence_ref"] if p else "",
            "state": ("PASS" if p and p["metric_pass"] else
                      ("FAIL" if p else "UNMEASURED")),
        })
    return {"skill_key": skill_key, "rows": rows,
            "applicable": len(rows),
            "measured": sum(1 for r in rows if r["state"] != "UNMEASURED"),
            "passed": sum(1 for r in rows if r["state"] == "PASS"),
            "failed": sum(1 for r in rows if r["state"] == "FAIL"),
            "unmeasured": sum(1 for r in rows if r["state"] == "UNMEASURED"),
            "invalidated": invalidated}


def render_table(conn: sqlite3.Connection, skill_key: str) -> str:
    """Render the skill as a markdown TABLE — the human-readable form of the
    table representation. The TABLE is the SSOT; this is a view of it."""
    t = skill_table(conn, skill_key)
    lines = ["# Skill: %s" % skill_key, "",
             "| # | Factor | Rule Definition | Action | Metric | Value | "
             "Proof | State |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(t["rows"], start=1):
        lines.append("| %d | %s | %s | %s | %s %s | %s | `%s` | %s |"
                     % (i, r["name"], r["rule_definition"].replace("|", "/"),
                        r["action"].replace("|", "/"), r["metric_kind"],
                        r["metric_target"], r["metric_value"] or "-",
                        r["proof_artifact_id"], r["state"]))
    lines += ["", "**%d applicable · %d measured · %d PASS · %d FAIL · "
              "%d UNMEASURED**" % (t["applicable"], t["measured"], t["passed"],
                                   t["failed"], t["unmeasured"])]
    return "\n".join(lines)


def drift_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """The `.skill.md` file vs the `skill_prompt_ssot` row, MEASURED.

    Measured 2026-09-21: 46 files, 28 ssot keys, 14 identical, 12 DIFFERENT, 20
    with no row. A file and a row that disagree is the same defect as a rule
    stated but not enforced — whichever one a reader opens, the other is stale.
    """
    same, diff, no_row = 0, 0, 0
    drifted, orphan_files = [], []
    for p in (BASE / "skills").rglob("*.skill.md"):
        k = p.name[: -len(".skill.md")]
        r = conn.execute("SELECT prompt_text FROM skill_prompt_ssot WHERE "
                         "skill_key=? ORDER BY id DESC LIMIT 1", (k,)).fetchone()
        if not r:
            no_row += 1
            orphan_files.append(k)
            continue
        if r[0] == p.read_text(encoding="utf-8"):
            same += 1
        else:
            diff += 1
            drifted.append(k)
    return {"files": same + diff + no_row, "identical": same,
            "different": diff, "no_ssot_row": no_row,
            "drifted": sorted(drifted), "files_without_a_row":
            sorted(orphan_files)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--drift", action="store_true")
    ap.add_argument("--table", default="")
    ap.add_argument("--tags", action="store_true")
    args = ap.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.seed:
            print(json.dumps(seed_factors(conn), indent=2))
        elif args.tags:
            print(json.dumps({"registered": registered_tags(conn),
                              "tag_source": tag_source_empty(conn)},
                             indent=2, ensure_ascii=False))
        elif args.drift:
            print(json.dumps(drift_report(conn), indent=2, ensure_ascii=False))
        elif args.table:
            print(render_table(conn, args.table))
        else:
            print(json.dumps({"factors": len(FACTORS),
                              "metric_kinds": list(METRIC_KINDS)}, indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main()