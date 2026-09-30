# -*- coding: utf-8 -*-
"""register_fill.py — fill the THIN `*_registry` tables, in BOUNDED PHASES.

WHY (the user, 2026-09-23)
--------------------------
    "have the plan, problem is *_registry is missing data"
    "and we have experience, LLM 7B can help"
    "so have this plan at 2 line , LLM help register for all other *_registry"
    "and you do the rest"

MEASURED (P0, `_diag_registry_fill_state.py`, this session):

    db_field    active=6      db_table   active=120
    function    active=1      api        active=3
    capability  active=29     module     active=4
    channel     active=1

and the SOURCES that could fill them:

    @app.route in mouse_spot_helper.py : 234
    CREATE TABLE in db_schema.py       : 110
    top-level def in *.py              : 3809 (in 569 files)

So the data EXISTS in the source and is absent from the registers. That gap is
what this module closes.

THE DIVISION OF LABOUR (measured, not assumed)
----------------------------------------------
    discovery         reads the SOURCE (a regex, a file)      deterministic
    the 7B            drafts the NAME + DESCRIPTION           language
    terminology_cite  verifies or SUPPLIES the cite_ref       determinism
    the register      REFUSES an unciteable key               the gate

MEASURED (`_proof_terminology_cite.py` case 8): the 7B's citations were NOT
usable — it put a refusal in `cite_ref` twice and invented a filename once. So
the 7B is NEVER asked for a citation here.

DISCOVERY IS DETERMINISTIC
--------------------------
`discover()` reads the SOURCE. The 7B never invents the list of keys to register
— if it did, the fill would measure the model's imagination instead of the site.

Run:
    .\\.venv\\Scripts\\python.exe register_fill.py --discover api
    .\\.venv\\Scripts\\python.exe register_fill.py --run api --apply
    .\\.venv\\Scripts\\python.exe register_fill.py --report api
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import db_schema as ds  # noqa: E402
import terminology_cite as tc  # noqa: E402

DB = BASE / "agent.db"
OLLAMA = "http://127.0.0.1:11434/api/chat"

# The registers this module fills, NARROWEST FIRST.
#
# WHY THIS ORDER: a narrow level can be DERIVED from the source directly, and a
# wide level can then be derived from a narrow one's FK chain. Filling `channel`
# first would mean inventing the widest thing before knowing what lives in it.
#
# `parent` is the FK column and the table it points at, so a child row can be
# attached to a REAL parent instead of a guessed id.
PHASES: tuple[dict[str, Any], ...] = (
    {"phase_key": "db_field", "scope": "db_field", "table": "db_field_registry",
     "key_col": "field_key", "cap": 40, "sort_order": 10,
     "source_kind": "table", "target_kind": "field",
     "parent": ("db_table_id", "db_table_registry", "table_key"),
     "why": "A field is the narrowest thing that can be read from a schema. "
            "MEASURED: 6 rows against 110 tables' worth of columns."},
    {"phase_key": "function", "scope": "function",
     "table": "function_registry", "key_col": "function_key", "cap": 40,
     "sort_order": 20,
     "source_kind": "file", "target_kind": "function",
     "parent": ("capability_id", "capability_registry", "capability_key"),
     # MEASURED: a capability is NOT derivable from a function name, so the
     # parent is DECLARED rather than guessed. `task_center.validate_new_task`
     # is the one real row, and its capability is the task-center one.
     "default_parent": "task_center.validate_new_task",
     "why": "MEASURED: 1 row against 3826 top-level defs. The thinnest register "
            "in the repo, and `hardcode_scope` names it as the root of UNKNOWN."},
    {"phase_key": "api", "scope": "api", "table": "api_registry",
     "key_col": "api_key", "cap": 40, "sort_order": 30,
     "source_kind": "route", "target_kind": "api",
     "parent": ("capability_id", "capability_registry", "capability_key"),
     "default_parent": "task_center.validate_new_task",
     "why": "MEASURED: 3 rows against 245 routes. A route name is what a caller "
            "reads, so a missing one is a wrong call."},
    {"phase_key": "module", "scope": "module", "table": "module_registry",
     "key_col": "module_key", "cap": 40, "sort_order": 40,
     "source_kind": "file", "target_kind": "module",
     "parent": ("channel_id", "channel_registry", "channel_key"),
     # MEASURED DEFECT (this session): the parent was taken as the key itself,
     # and `channel_registry` has no channel named `_apply_cap_rename`, so all 40
     # keys were refused `PARENT_NOT_FOUND`. A module's parent is a CHANNEL, and
     # a channel is not derivable from a module name. `local_pc` is the one real
     # channel, and it is the machine these modules run on.
     "default_parent": "local_pc",
     "why": "MEASURED: 4 rows. A module is where a capability lives."},
    {"phase_key": "channel", "scope": "channel", "table": "channel_registry",
     "key_col": "channel_key", "cap": 40, "sort_order": 50,
     "source_kind": "directory", "target_kind": "channel",
     "parent": None,
     "why": "MEASURED: 1 row. The widest level, so it is filled LAST — a channel "
            "is a claim about everything under it."},
    # ---- THE 4 PHASES ADDED 2026-09-25 (plan_7B.FILL.ALL.REGISTERS) --------
    #
    # The human: "7M can help to fill *_registry now". Each of these registers is
    # THIN while its SOURCE is large, so the data exists and is absent:
    #
    #     chat_registry              0 rows   vs chat_main                63
    #     purpose_route_registry     1 row    vs @app.route             257
    #     derived_column_registry    2 rows   vs derived_from          1949
    #     component_registry         8 rows   vs skill_registry           61
    #
    # `parent` is None for all four: none of these is a CHILD of another register
    # in the 5-level chain, so there is no FK to resolve and no `PARENT_NOT_FOUND`
    # to hit. Declaring a parent that does not exist would manufacture exactly the
    # refusal the 5 original phases were fixed for.
    {"phase_key": "chat", "scope": "chat", "table": "chat_registry",
     "key_col": "chat_key", "cap": 40, "sort_order": 60,
     # ---- KIND MISMATCH: a CHAT is not a PAIRING -------------------------
     #
     # MEASURED (2026-09-25): `discover("chat")` reads `chat_main` (a CHAT) and
     # writes `chat_registry` (a PAIRING). The two are DIFFERENT KINDS, so the
     # 56 keys it produced could never be written — every one was refused
     # `PARENT_NOT_FOUND` because a chat carries no ticket.
     #
     # THE TABLES ARE CORRECT. `chat_registry_store.py:1` says a row is "a TICKET
     # paired with the CHAT it was discussed in", and `mouse_spot_helper.py:9030`
     # says "A missing eumu_id is NOT an error — most chats are not about a
     # registered entity". So `chat_main` SHOULD NOT have a ticket column, and
     # `chat_registry=0` is CORRECT (65 chats, 1 demo ticket, no pairing).
     #
     # The defect is the DISCOVERY. The kinds are DECLARED here so the gate can
     # refuse it and SAY WHY.
     "source_kind": "chat", "target_kind": "pairing",
     "parent": ("ticket_id", "ticket", "id"),
     "why": "MEASURED: 0 rows against 63 chats in `chat_main`. The ONLY empty "
            "register in the repo. BLOCKED: `chat_registry.ticket_id` is NOT "
            "NULL and `chat_main` records no ticket, so the parent is missing "
            "from the SOURCE — the same class as the 109 PARENT_NOT_FOUND."},
    {"phase_key": "purpose_route", "scope": "purpose_route",
     "table": "purpose_route_registry", "key_col": "route_key", "cap": 40,
     "sort_order": 70,
     # ---- KIND MISMATCH: an IDENTITY is not a ROUTE ----------------------
     #
     # MEASURED (2026-09-25): `discover("purpose_route")` reads
     # `identity_registry` (an IDENTITY) and writes `purpose_route_registry`
     # (a ROUTE). An identity records WHO/WHY/WHICH-WORKFLOW; a route records
     # WHICH SERVICE fulfils a purpose. `identity_registry` has NO service
     # column, so `ticket_origin` can never be derived from it.
     "source_kind": "identity", "target_kind": "route",
     "parent": None,
     # MEASURED (2026-09-25, the SECOND real `purpose_route` run): the FIRST run
     # fed 277 API paths (`POST /api/tasks/validate`) because `discover()` COPIED
     # the `api` branch. The register needs `purpose.workflow`
     # (`chat_identity.worker_identity_flow`), so every key was refused
     # `SOURCE_MISSING_COLUMN` — a CORRECT refusal caused by a WRONG discovery.
     #
     # `discover()` now reads `identity_registry` (the PURPOSE in `why`, the
     # WORKFLOW in `workflow_id`), so `purpose_key` and `workflow_key` ARE
     # derivable. `ticket_origin` is NOT: `identity_registry` has no service column.
     # It is DECLARED, so the refusal names ONLY `ticket_origin` — a finding that
     # says exactly what is missing, instead of a constraint error.
     "required_extra": ("ticket_origin",),
     # MEASURED (2026-09-25): `cite_ref` is NOT NULL on this register, and the
     # citation this module already computed and verified is exactly what it
     # wants. Writing it is the whole point of the gate.
     "derive_extra": {"cite_ref": "cite_ref"},
     "why": "MEASURED: 1 row against 2 derivable routes in `identity_registry`. "
            "BLOCKED on `ticket_origin` only: the PURPOSE and the WORKFLOW are "
            "recorded, the SERVICE is not."},
    {"phase_key": "derived_column", "scope": "derived_column",
     "table": "derived_column_registry", "key_col": "column_name", "cap": 40,
     "sort_order": 80,
     "source_kind": "declaration", "target_kind": "derived_column",
     "parent": None,
     # MEASURED (2026-09-25, the SECOND real `derived_column` run): the FIRST run
     # read `db_field_registry.derived_from`, which is `'pragma:table_info'` on
     # ALL 1949 rows — a PROVENANCE marker meaning the column is REAL, not
     # derived. So it produced 758 bare column names where the register needs
     # `table_name.column_name`, and all 756 were refused `SOURCE_MISSING_COLUMN`.
     #
     # `discover()` now reads `db_schema.DERIVED_COLUMN_SEED`, whose rows ARE the
     # register's own shape `(table_name, column_name, kind, derived_from)`. So
     # `table_name` and `kind` ARE derivable and nothing is declared missing.
     "derive_extra": {"cite_ref": "cite_ref"},
     "why": "MEASURED: 2 rows against 2 DECLARED derivations in "
            "`db_schema.DERIVED_COLUMN_SEED`. The source is the declaration "
            "itself, so every required column is derivable."},
    {"phase_key": "component", "scope": "component",
     "table": "component_registry", "key_col": "skill_key", "cap": 40,
     "sort_order": 90,
     "source_kind": "skill", "target_kind": "component",
     "parent": None,
     "why": "MEASURED: 8 rows against 61 registered skills. A component is a "
            "skill that produces a parsed output."},
    # ---- THE 2 PHASES ADDED 2026-09-25 (plan_FILL.REGISTERS.BY.SETTING) ----
    #
    # MEASURED: every NOT NULL column IS derivable from the source, so these are
    # FILLABLE where `chat`/`purpose_route`/`derived_column` are not.
    #
    #     study_registry    2 rows  vs skill_registry  61
    #     worker_registry   6 rows  vs llm_model       76
    {"phase_key": "study", "scope": "study", "table": "study_registry",
     "key_col": "study_key", "cap": 40, "sort_order": 100,
     "source_kind": "skill", "target_kind": "study",
     "parent": None,
     # MEASURED (2026-09-25, the first real `study` apply): refused
     # `WRITE_FAILED` for `study_registry.skill_id`, which the SOURCE DOES supply
     # — the source IS `skill_registry`. So the value is READ from the source row,
     # not invented.
     "source_table": "skill_registry", "source_key_col": "skill_key",
     "derive_extra": {"skill_id": "skill_id"},
     "why": "MEASURED: 2 rows against 61 registered skills. A study is a CASE a "
            "skill is studied against, and `study_key`/`name`/`skill_id` all "
            "come from `skill_registry`."},
    {"phase_key": "worker", "scope": "worker", "table": "worker_registry",
     "key_col": "worker_key", "cap": 40, "sort_order": 110,
     "source_kind": "model", "target_kind": "worker",
     "parent": None,
     # MEASURED (2026-09-25, the first real `worker` apply): refused
     # `WRITE_FAILED` for `worker_registry.cite_ref`, which this module ALREADY
     # computed and verified. Writing it is the whole point of the gate.
     "derive_extra": {"cite_ref": "cite_ref"},
     "why": "MEASURED: 6 rows against 76 models in `llm_model`. A worker is a "
            "MODEL that does work, and `worker_key`/`name`/`worker_type` all "
            "come from `llm_model`."},
    # ---- THE 2 CLASS C PHASES ADDED 2026-09-25 (plan_ALL.REGISTERS.NOT.SOME)
    #
    # THE HUMAN: "all *_registry not some_registry" and "same method, LLM 7B can
    # help and be strong with your help too".
    #
    # MEASURED: `wording_registry` holds 38 rows covering only 4 distinct
    # `skill_id` against 63 registered skills, and `code_registry` holds 303 rows
    # against 298 `code_location_registry` rows. Both have a REAL source, so both
    # are FILLABLE by the SAME method: deterministic discovery + 7B drafting +
    # deterministic citation + the KIND gate.
    {"phase_key": "wording", "scope": "wording", "table": "wording_registry",
     "key_col": "wording_key", "cap": 40, "sort_order": 120,
     # ---- KIND MISMATCH: a SKILL is not a WORDING VALUE ------------------
     #
     # MEASURED (2026-09-25, the first real `wording` apply): all 63 keys were
     # refused `WRITE_FAILED` for `wording_registry.dim_key` and `.template`,
     # which are NOT NULL. A wording row is a (SKILL x DIMENSION x VALUE) triple:
     # `dim_key` is `context`/`criterion`/`negation`/`output`, and `template` is
     # the sentence. A SKILL supplies none of those.
     #
     # The real source is `prompt_dimension` (10 rows: skill_key, dim_key,
     # value_key, value_text), which IS the triple. So the discovery read KIND A
     # (a skill) and wrote KIND B (a wording value) — the SAME defect class the
     # KIND gate exists to catch, committed again by me.
     "source_kind": "skill", "target_kind": "wording_value",
     "parent": None,
     "why": "MEASURED: 38 rows covering only 4 distinct `skill_id` against 63 "
            "registered skills. A wording row is a (skill x dimension x value) "
            "triple, so a SKILL alone cannot supply `dim_key`/`template`."},
    {"phase_key": "code", "scope": "code", "table": "code_registry",
     "key_col": "register_id", "cap": 40, "sort_order": 130,
     # ---- KIND MISMATCH: a CODE LOCATION is not a CODE SLICE -------------
     #
     # MEASURED (2026-09-25): `code_registry.register_id` is a TEXT slice key
     # (`reg_membership_member_region_2f5d7336f433`), and 0 of 303 rows match a
     # `code_location_registry.location_id` (an INTEGER 1..298). The two are
     # DIFFERENT KINDS:
     #   code_location_registry = WHERE an entity's code lives
     #   code_registry          = a MANAGED CODING SLICE (module/function/task)
     # So the discovery read KIND A and wrote KIND B.
     "source_kind": "code_location", "target_kind": "code_slice",
     "parent": None,
     "why": "MEASURED: 303 rows against 298 `code_location_registry` rows, but "
            "0 of 303 `register_id` values match a `location_id`. A code slice "
            "is a managed-coding unit, not a file location."},
)

SCOPES = tuple(p["scope"] for p in PHASES)

# ---------------------------------------------------------------------------
# THE COVERAGE TABLE — EVERY `*_registry`, NOT ONLY THE ONES THIS MODULE FILLS
# ---------------------------------------------------------------------------
#
# THE HUMAN (2026-09-25), verbatim:
#     "have the plan!! all *_registry not some_registry"
#
# THE DEFECT THIS REMOVES
# -----------------------
# This module reported "all registers 100%" while covering 11 of 26 `*_registry`
# tables. A report that measures its OWN SCOPE and calls it the world is not a
# report — it is a claim about the reporter. MEASURED: 26 `*_registry` tables
# exist; `register_fill` covers 11; 20 are uncovered.
#
# THE THREE CLASSES (measured, not assumed)
# -----------------------------------------
#   A  POPULATED BY ITS OWN MODULE — a writer exists AND rows exist. NOT a gap.
#      It needs a COVERAGE CHECK, not a fill.
#   B  DERIVED — 1 row per entity, computable from the entity registers. No 7B.
#   C  REAL GAP — no writer, or a writer with no rows.
#
# CLASS AND DISCOVERY ARE DIFFERENT AXES (measured 2026-09-25)
# -----------------------------------------------------------
# I conflated them, and the class gate caught it. They answer DIFFERENT questions:
#
#   CLASS      "is this register POPULATED?"        -> measured by the writer scan
#   DISCOVERY  "can this SOURCE supply this TARGET?" -> the KIND gate
#
# A register can be CLASS A (its own module fills it) AND have a KIND-mismatched
# `register_fill` phase. MEASURED: `wording_registry` holds 38 rows written by
# `prompt_generator.py` (CLASS A) while `discover("wording")` is REFUSED because a
# SKILL cannot supply a WORDING VALUE. Both are true; they are not the same claim.
# So `gap` describes the CLASS gap, and `discovery` describes the KIND status.
#
# A register with NO SOURCE is class C with `source=None`, and it is REPORTED as
# NO-SOURCE rather than filled with invented rows. An invented row verifies and
# lies, which is the exact defect `_write_row` refuses.
COVERAGE: tuple[dict[str, Any], ...] = (
    # ---- covered by this module's PHASES --------------------------------
    {"table": "db_field_registry", "class": "A", "writer": "register_fill:db_field"},
    {"table": "function_registry", "class": "A", "writer": "register_fill:function"},
    {"table": "api_registry", "class": "A", "writer": "register_fill:api"},
    {"table": "module_registry", "class": "A", "writer": "register_fill:module"},
    {"table": "channel_registry", "class": "A", "writer": "register_fill:channel"},
    {"table": "chat_registry", "class": "C", "writer": "register_fill:chat",
     "source": None,
     "gap": "MEASURED: 0 rows. A writer exists (`chat_registry_store.py`) but "
            "no pairing has ever been created — 65 chats, 1 demo ticket.",
     "discovery": "REFUSED: a CHAT cannot supply a PAIRING"},
    {"table": "purpose_route_registry", "class": "A",
     "writer": "purpose_route_registry.py",
     "discovery": "REFUSED: an IDENTITY cannot supply a ROUTE"},
    {"table": "derived_column_registry", "class": "A",
     "writer": "register_fill:derived_column"},
    {"table": "component_registry", "class": "A", "writer": "register_fill:component"},
    {"table": "study_registry", "class": "A", "writer": "register_fill:study"},
    {"table": "worker_registry", "class": "A", "writer": "register_fill:worker"},
    # ---- CLASS A: populated by their own module -------------------------
    {"table": "code_location_registry", "class": "B",
     "writer": "code_location_registry.py:add_location",
     "gap": "MEASURED: 298 rows == 298 distinct (entity_type, entity_ref_id) — "
            "1 row per entity, so it is DERIVED"},
    {"table": "dimension_binding_registry", "class": "A",
     "writer": "dimension_binding_registry.py:seed"},
    {"table": "entity_type_registry", "class": "A",
     "writer": "entity_registry.py:ensure_entity_registry_schema"},
    {"table": "identity_registry", "class": "A",
     "writer": "identity_registry.py:open_identity"},
    {"table": "mode_registry", "class": "A", "writer": "mode_registry.py:seed"},
    {"table": "mode_right_registry", "class": "A", "writer": "mode_registry.py"},
    {"table": "prompt_registry", "class": "A", "writer": "prompt_registry.py"},
    {"table": "role_registry", "class": "A", "writer": "role_right_registry.py:seed"},
    {"table": "role_right_registry", "class": "A",
     "writer": "role_right_registry.py:seed"},
    {"table": "route_registry", "class": "A", "writer": "route_registry.py:seed"},
    {"table": "skill_factor_registry", "class": "A",
     "writer": "skill_factor.py:register_factor"},
    {"table": "skill_registry", "class": "A",
     "writer": "skill_registrar.py:ensure_skill_identity"},
    {"table": "subject_kind_registry", "class": "A",
     "writer": "subject_kind_registry.py:seed"},
    {"table": "unit_registry", "class": "A", "writer": "prompt_generator.py"},
    {"table": "workflow_registry", "class": "A", "writer": "consultant_registry.py"},
    {"table": "terminology_registry", "class": "A",
     "writer": "terminology_sweep.py:discover"},
    # ---- CLASS B: derived, 1 row per entity -----------------------------
    {"table": "version_registry", "class": "B",
     "writer": "entity_registry.py:ensure_version",
     "source": "entity_type_registry x each register",
     "gap": "DERIVED: 1118 distinct (entity_type, entity_ref_id) = 1118 rows"},
    # ---- CLASS C: real gaps ---------------------------------------------
    {"table": "wording_registry", "class": "A", "writer": "prompt_generator.py",
     "discovery": "REFUSED: a SKILL cannot supply a WORDING VALUE. The real "
                  "source is `prompt_dimension` (skill x dimension x value)"},
    {"table": "code_registry", "class": "A", "writer": "managed_coding.py",
     "discovery": "REFUSED: a CODE LOCATION cannot supply a CODE SLICE. "
                  "MEASURED: 0 of 303 register_id values match a location_id"},
    {"table": "field_registry", "class": "A", "writer": "skill_field_registry.py",
     "gap": "MEASURED: 13 rows against a 895-line field TDD module"},
    # ---- REMOVED 2026-09-25 (plan_REMOVE.DEAD.EVENT.JOB) -----------------
    #
    # `event_registry` and `job_registry` were REMOVED. The human: "is old design,
    # proof can remove -> remove rubbish". MEASURED: 0 rows each, 0
    # `version_registry` rows for letter E/J, and `job_registry.py` had NO product
    # caller. They are no longer declared here, so if either table reappears the
    # coverage report will print it as `UNDECLARED` — which is the correct
    # behaviour for a table that should not exist.
    # ---- THE 12 THE FIRST COVERAGE TABLE FORGOT -------------------------
    #
    # MEASURED (2026-09-25): the first `COVERAGE` listed 26 tables, but the DB
    # holds 45 matching `%register%` OR `%registry%`. The report caught its own
    # omission and printed them as `UNDECLARED` — which is exactly why the LIST
    # is read from `sqlite_master` and not from `COVERAGE`. A coverage table
    # that can omit a register is the defect it exists to remove.
    {"table": "capability_kind_registry", "class": "A",
     "writer": "capability_store.py"},
    {"table": "capability_registry", "class": "A",
     "writer": "capability_store.py"},
    {"table": "capability_tag_registry", "class": "A",
     "writer": "skill_factor.py"},
    {"table": "db_table_registry", "class": "A",
     "writer": "derive_registry_rows.py",
     "gap": "MEASURED: 202 rows against 201 live tables — 1 stale row "
            "(`llm_100_run` is registered but NOT live)"},
    {"table": "industry_registry", "class": "A",
     "writer": "consultant_registry.py"},
    {"table": "llm_service_type_registry", "class": "A",
     "writer": "db_schema.py"},
    {"table": "namespace_registry", "class": "A",
     "writer": "namespace_registry.py"},
    {"table": "skill_task_id_registry", "class": "A",
     "writer": "skill_task_id_allocator.py"},
    {"table": "task_type_registry", "class": "A",
     "writer": "task_type_registry.py"},
    {"table": "taxonomy_level_registry", "class": "A",
     "writer": "taxonomy_level_registry.py"},
    {"table": "test_case_registry", "class": "A",
     "writer": "src/task_center/ontology_store.py:seed_validate_new_task_test_cases",
     "gap": "MEASURED: 32 rows, written by `ontology_store.py` (a product "
            "module) via `seed_test_case_registry.py`. MY EARLIER CLASS C CLAIM "
            "WAS WRONG — the writer scan found it."},
)

# The phase row for a scope, so `discover()` can read its DECLARED kinds. Built
# from PHASES rather than hand-written, so a new phase cannot be added without
# its kinds being visible to the gate.
_PHASE_BY_SCOPE: dict[str, dict[str, Any]] = {p["scope"]: p for p in PHASES}

# WHERE A KEY WAS DISCOVERED, for the scopes whose source is a TABLE rather than
# a file. A key read from a table has no file named after it, so `cite_for`'s
# file branches cannot fire and the honest citation is the MEASUREMENT that
# produced it (`measured: <table>.<column> = <key>`), which a reader can re-run.
#
# MEASURED (2026-09-25, the first real `chat` run): all 40 keys were refused
# `NO_CHECKABLE_CITE` because a `chat` key is a 64-char hex `chat_hash` and no
# file is named that. The refusal was CORRECT — the citation really was missing —
# so the fix is to SUPPLY the right one, not to weaken the gate.
#
# MEASURED (2026-09-26): the column is `chat_hash_recomputed`, NOT `chat_hash`.
# `chat_hash` is `sha256(chat_main.id | session_id)` on 58 of 58 rows — the
# writer's OLD formula, which re-encodes the primary key and pairs nothing. It is
# kept as append-only audit. Reading it here would hand the register a WRONG key.
_SOURCE_OF_SCOPE: dict[str, tuple[str, str]] = {
    "chat": ("chat_main", "chat_hash_recomputed"),
    "derived_column": ("db_field_registry", "derived_from"),
    "component": ("skill_registry", "skill_key"),
    # MEASURED (2026-09-25, the first real `worker` dry run): only 5 of 40 keys
    # got a citation. A worker key is a MODEL ID (`deepseek/deepseek-v4-flash-0731`),
    # and the file branches found a file that merely MENTIONS the name —
    # `_proof_pick_flow.py:228` for a DeepSeek model. That is a citation that is
    # checkable AND WRONG, which is worse than none because it is believed.
    #
    # The honest citation is the measurement: which table and column the key was
    # read from.
    "worker": ("llm_model", "model_id"),
    "study": ("skill_registry", "skill_key"),
    # MEASURED (2026-09-25): a `purpose_route` key is `purpose_slug.workflow_key`
    # and a `derived_column` key is `table.column`. Neither names a FILE, so the
    # file branches cannot fire and the honest citation is the measurement that
    # produced the key.
    "purpose_route": ("identity_registry", "why"),
    "derived_column": ("db_schema.DERIVED_COLUMN_SEED", "table_name.column_name"),
    # MEASURED (2026-09-25, plan_ALL.REGISTERS.NOT.SOME): a `wording` key is a
    # SKILL KEY and a `code` key is `entity_type:entity_ref_id:file_path`.
    # Neither names a FILE of its own, so the honest citation is the measurement
    # that produced the key.
    "wording": ("skill_registry", "skill_key"),
    "code": ("code_location_registry", "file_path"),
}

# THE 7B IS ASKED FOR LANGUAGE ONLY. There is deliberately NO `cite_ref` key:
# the model cannot produce a checkable citation (measured), and asking produced
# an invented filename.
DRAFT_SYSTEM = (
    "You name things for a software repository. Reply with ONE JSON object and "
    "nothing else. Keys: key (lowercase snake_case or dotted), name (a short "
    "human label), description (one sentence saying what the thing IS and what "
    "it is NOT). Do NOT include a citation."
)

# The CONTROL: a key that is NOT in the source. The prompt asks for a name and a
# description ONLY, so the failure to guard against is "invents a confident
# description for something that does not exist".
CONTROL_KEY = "__control_not_in_source__"
CONTROL_ASK = (
    "Name this thing from a software repository: the internal name of the "
    "feature that will replace the register tables next year. It is not in any "
    "file yet. If you cannot name a real thing, reply with "
    "{\"refuse\": \"reason\"}."
)


def _now_us() -> str:
    """A microsecond timestamp, for an append-only log.

    The DDL DEFAULT is `datetime('now')`, which resolves to the SECOND, and the
    log is `UNIQUE (phase_key, key_text, observed_at)`. A dry run followed
    immediately by an APPLY run therefore collided and every APPLIED row was
    silently dropped — measured in `terminology_sweep` as `done=23` instead of
    25. Supplying the timestamp here keeps the uniqueness that catches a true
    duplicate while letting a legitimate re-run append.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def ensure_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create the tables. Idempotent.

    MERGED 2026-09-27 (plan REGISTER.NAMING.AND.PHASE.MERGE): the phase rows now
    live in `phase_registry` with `phase_kind='register_fill'`. The old
    `register_fill_phase` table is GONE — see `db_schema.PHASE_registry_DDL`.
    """
    conn.executescript(ds.PHASE_registry_DDL)
    conn.executescript(ds.REGISTER_FILL_RUN_DDL)
    conn.executescript(ds.REGISTER_FILL_CONTROL_DDL)
    conn.commit()
    return {"ok": True}


PHASE_KIND = "register_fill"


def seed_phases(conn: sqlite3.Connection) -> dict[str, Any]:
    """UPSERT the phases. A corrected `cap` must be able to land."""
    ensure_schema(conn)
    n = 0
    for p in PHASES:
        if p["scope"] not in SCOPES:
            return {"ok": False, "error": "phase %s has scope %r, not one of %s"
                    % (p["phase_key"], p["scope"], list(SCOPES))}
        conn.execute(
            """
            INSERT INTO phase_registry
                (phase_kind, phase_key, display_name, scope, target_table,
                 key_col, cap, sort_order, why, cite_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (phase_kind, phase_key) DO UPDATE SET
                display_name = excluded.display_name,
                scope        = excluded.scope,
                target_table = excluded.target_table,
                key_col      = excluded.key_col,
                cap          = excluded.cap,
                sort_order   = excluded.sort_order,
                why          = excluded.why,
                cite_ref     = excluded.cite_ref,
                updated_at   = CURRENT_TIMESTAMP
            """,
            (PHASE_KIND, p["phase_key"],
             p["phase_key"].replace("_", " ").title(),
             p["scope"], p["table"], p["key_col"], p["cap"], p["sort_order"],
             p["why"], "register_fill.py:1"),
        )
        n += 1
    conn.commit()
    return {"ok": True, "seeded": n}


def list_phases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(conn)
    return [dict(r) for r in conn.execute(
        "SELECT phase_key, display_name, scope, target_table, key_col, cap, "
        "       sort_order, why, cite_ref FROM phase_registry "
        " WHERE phase_kind=? AND is_active=1 ORDER BY sort_order, phase_key",
        (PHASE_KIND,))]


# ---------------------------------------------------------------------------
# DETERMINISTIC discovery (the 7B is never asked for the list)
# ---------------------------------------------------------------------------

_ROUTE_RE = re.compile(r'@app\.route\(\s*"([^"]+)"')
_TABLE_RE = re.compile(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)\s*\(",
                       re.IGNORECASE)
_DEF_RE = re.compile(r"^def ([a-z_][a-z0-9_]*)\s*\(", re.MULTILINE)

# A `_`-prefixed file is a ONE-SHOT session script, not product code. The repo
# already declares this convention (`hardcode_scan.SESSION_SCRIPT_PREFIX`), and
# its own comment says such files "are COUNTED and reported, so nothing is
# hidden". This module follows the SAME rule: skip them, and REPORT the count.
SESSION_SCRIPT_PREFIX = "_"


class KindMismatch(RuntimeError):
    """A scope's SOURCE KIND differs from its TARGET register KIND.

    THE HUMAN'S CORRECTION (2026-09-25): "skill 教 become hard gate by writing
    format, and skill can help to have the explain easy to explain to worker why
    we reject and tutorial them".

    So this exception is not a bare error. It carries the FORMAT (both kinds) and
    the EXPLANATION (`why`, `evidence`, `tutorial`), because a refusal that says
    only WHAT failed teaches nothing — the worker cannot learn from it.
    """

    def __init__(self, scope: str, source_kind: str, target_kind: str,
                 why: str, evidence: str, tutorial: str):
        self.scope = scope
        self.source_kind = source_kind
        self.target_kind = target_kind
        self.why = why
        self.evidence = evidence
        self.tutorial = tutorial
        super().__init__(self.explain())

    def explain(self) -> str:
        """The refusal, in the shape a worker can act on."""
        return (
            "REFUSED: discover(%r) KIND MISMATCH\n"
            "  source_kind : %s\n"
            "  target_kind : %s\n"
            "  why         : %s\n"
            "  evidence    : %s\n"
            "  tutorial    : %s"
            % (self.scope, self.source_kind, self.target_kind, self.why,
               self.evidence, self.tutorial))


# WHY a kind pair is a mismatch, and what to do instead. The mapping is EXPLICIT
# and reviewable, because a silent fuzzy match would refuse the wrong scopes.
_KIND_MISMATCH_WHY: dict[tuple[str, str], dict[str, str]] = {
    ("chat", "pairing"): {
        "why": ("a CHAT is not a PAIRING. `chat_main` records a conversation; "
                "`chat_registry` records a TICKET paired with a chat. A chat "
                "carries no ticket, so no pairing can be derived from one."),
        "evidence": "chat_registry_store.py:1",
        "tutorial": ("to fill `chat_registry`, supply a REAL `ticket_id` and a "
                     "REAL `chat_id` through "
                     "`chat_registry_store.create_chat_registry`. A chat alone "
                     "cannot produce a pairing."),
    },
    ("identity", "route"): {
        "why": ("an IDENTITY is not a ROUTE. `identity_registry` records "
                "WHO/WHY/WHICH-WORKFLOW; `purpose_route_registry` records WHICH "
                "SERVICE fulfils a purpose. An identity has no service column, "
                "so `ticket_origin` can never be derived from one."),
        "evidence": "purpose_route_registry.py:1",
        "tutorial": ("to fill `purpose_route_registry`, supply a REAL "
                     "`ticket_origin` (one of the live `ticket_center.ticket_origin` "
                     "values). The purpose and the workflow ARE derivable from "
                     "`identity_registry`; the service is not."),
    },
    # ---- THE 2 I COMMITTED MYSELF (2026-09-25, plan_ALL.REGISTERS.NOT.SOME) --
    #
    # I added `wording` and `code` as CLASS C fill targets, then MEASURED that
    # both are the SAME KIND mismatch class the gate exists to catch. Declaring
    # them here is the honest outcome: the gate refuses, and the report says WHY.
    ("skill", "wording_value"): {
        "why": ("a SKILL is not a WORDING VALUE. `skill_registry` records a "
                "skill's identity; `wording_registry` records a "
                "(skill x dimension x value) triple, whose `dim_key` and "
                "`template` are NOT NULL. A skill supplies neither."),
        "evidence": "prompt_dimension:1",
        "tutorial": ("to fill `wording_registry`, read `prompt_dimension` "
                     "(skill_key, dim_key, value_key, value_text) — that table "
                     "IS the triple. A skill alone cannot produce a wording."),
    },
    ("code_location", "code_slice"): {
        "why": ("a CODE LOCATION is not a CODE SLICE. "
                "`code_location_registry` records WHERE an entity's code lives "
                "(entity_type, entity_ref_id, file_path); `code_registry` "
                "records a MANAGED CODING SLICE (module_name, function_name, "
                "task_id, system_key, slice_key). MEASURED: 0 of 303 "
                "`register_id` values match a `location_id`."),
        "evidence": "code_registry.py:1",
        "tutorial": ("to fill `code_registry`, supply a REAL managed-coding "
                     "slice (module/function/task). A file location cannot "
                     "produce a slice."),
    },
}


def assert_kind_match(phase: dict[str, Any]) -> None:
    """REFUSE a scope whose SOURCE KIND cannot produce its TARGET register KIND.

    THE FORMAT IS THE GATE. The kinds are declared as DATA on the phase, so this
    check reads them rather than judging anything — the same design as
    `skill_contract_store.validate_payload_against_contract`, whose docstring
    says "Hard validation ... No LLM judgement. Rules (all hard — reject, never
    warn-and-continue)".

    WHY THE PASS CONDITION IS NOT `source_kind == target_kind`
    ---------------------------------------------------------
    MEASURED (2026-09-25): the two kinds are DIFFERENT BY CONSTRUCTION for every
    phase — a `table` produces a `field`, a `file` produces a `function`, a
    `skill` produces a `component`. So equality would refuse all 11 phases, which
    is a gate that refuses everything and therefore measures nothing.

    The real question is whether the SOURCE can SUPPLY the TARGET. That is a
    MEASURED property, not a name comparison, so the mismatches are DECLARED
    explicitly in `_KIND_MISMATCH_WHY` — each with the measurement that proved it.
    An undeclared pair passes; a declared pair is refused with its reason.

    MEASURED (2026-09-25): 2 scopes mismatched (`chat`, `purpose_route`), and
    between them they produced 56 + 2 = 58 keys that could NEVER be written. The
    refusals were correct; the DISCOVERY was wrong.
    """
    sk = str(phase.get("source_kind") or "")
    tk = str(phase.get("target_kind") or "")
    if not sk or not tk:
        raise KindMismatch(
            str(phase.get("scope") or phase.get("phase_key") or "?"), sk or "?",
            tk or "?", "the phase does not DECLARE its kinds, so the match "
            "cannot be checked", "register_fill.py:PHASES",
            "add `source_kind` and `target_kind` to the phase row")
    info = _KIND_MISMATCH_WHY.get((sk, tk))
    if info is None:
        return
    raise KindMismatch(str(phase.get("scope") or phase.get("phase_key")),
                       sk, tk, info["why"], info["evidence"], info["tutorial"])


def discover_skipped(scope: str, base: Path | None = None) -> list[str]:
    """The keys `discover()` EXCLUDED, so a skip is never silent.

    MEASURED (2026-09-25): 1348 of 5250 functions and 623 of 953 modules live in
    `_`-prefixed session scripts. Excluding them without reporting would make the
    register's size look like a complete measure of the repo when it is a measure
    of the PRODUCT code only. A skip that is not reported is indistinguishable
    from a key that does not exist.
    """
    b = base or BASE
    if scope == "function":
        out = set()
        for p in sorted(b.glob("*.py")):
            if not p.stem.startswith(SESSION_SCRIPT_PREFIX):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in _DEF_RE.finditer(text):
                out.add("%s.%s" % (p.stem, m.group(1)))
        return sorted(out)
    if scope == "module":
        return sorted(p.stem for p in b.glob("*.py")
                      if p.stem.startswith(SESSION_SCRIPT_PREFIX))
    return []


def discover(scope: str, base: Path | None = None) -> list[str]:
    """The keys in one scope, read from the SOURCE. Deterministic, sorted.

    Sorted so two runs produce the SAME list — a fill whose input order changes
    between runs cannot be compared, and `truncated` would mean something
    different each time.

    THE KIND GATE RUNS FIRST. A scope whose SOURCE KIND differs from its TARGET
    register KIND is REFUSED before a single key is produced, because every key
    it would produce is unwritable. MEASURED (2026-09-25): `chat` produced 56 and
    `purpose_route` produced 2 such keys, and all 58 were refused downstream.
    Refusing at the SOURCE names the real defect; refusing at the WRITE only
    reports its symptom.
    """
    phase = _PHASE_BY_SCOPE.get(scope)
    if phase is not None:
        assert_kind_match(phase)
    b = base or BASE
    if scope == "api":
        text = (b / "mouse_spot_helper.py").read_text(encoding="utf-8",
                                                      errors="replace")
        # `api_key` is `METHOD /path`, matching the ONE real row already there
        # (`POST /api/tasks/validate`). A bare path would collide across methods.
        out = set()
        for m in re.finditer(r'@app\.route\(\s*"([^"]+)"(.*?)\)\s*\n',
                             text, re.S):
            path, tail = m.group(1), m.group(2)
            methods = re.findall(r'methods\s*=\s*\[([^\]]*)\]', tail)
            if methods:
                for meth in re.findall(r'"([A-Z]+)"', methods[0]):
                    out.add("%s %s" % (meth, path))
            else:
                out.add("GET %s" % path)
        return sorted(out)
    if scope == "db_field":
        text = (b / "db_schema.py").read_text(encoding="utf-8",
                                              errors="replace")
        out: set[str] = set()
        for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)"
                             r"\s*\((.*?)\n\);", text, re.IGNORECASE | re.S):
            tbl, body = m.group(1), m.group(2)
            # ---- A DROP MARKER IS NOT A TABLE ---------------------------
            #
            # MEASURED (2026-09-25): `db_schema.py` carries the idiom
            #     CREATE TABLE IF NOT EXISTS _chat_env_link_marker (id INTEGER);
            #     DROP TABLE IF EXISTS _chat_env_link_marker;
            # which means "make sure this table does NOT exist". It is a
            # legitimate pattern, but it is NOT a table, so proposing fields for
            # it invents a table that the same DDL removes.
            #
            # The DROP is checked in the BODY *and* just after the match: this
            # DDL is written on ONE line (`(id INTEGER);`), so the non-greedy
            # `(.*?)\n\);` runs PAST it to the next multi-line DDL and the DROP
            # lands inside the captured body.
            if re.search(r"DROP\s+TABLE\s+IF\s+EXISTS\s+%s\b" % re.escape(tbl),
                         body + text[m.end():m.end() + 200], re.IGNORECASE):
                continue
            for line in body.splitlines():
                cm = re.match(r"\s*([a-z_][a-z0-9_]*)\s+[A-Z]", line)
                if cm and cm.group(1).upper() not in (
                        "PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"):
                    out.add("%s.%s" % (tbl, cm.group(1)))
        return sorted(out)
    if scope == "function":
        # ---- SESSION SCRIPTS ARE NOT PRODUCT CODE -------------------------
        #
        # MEASURED (2026-09-25): 1348 of 5250 discovered functions live in
        # `_`-prefixed files. The repo ALREADY has this convention —
        # `hardcode_scan.SESSION_SCRIPT_PREFIX = "_"` — and its own comment says
        # such files "are COUNTED and reported, so nothing is hidden".
        #
        # A session script is a ONE-SHOT diagnostic (`_diag_*`, `_proof_*`,
        # `_clean_*`), not a function the system calls. Registering it would put
        # throwaway code in the register and make the register's size a measure of
        # how many scripts were ever run. So they are EXCLUDED, and the count is
        # REPORTED by `discover_skipped()` — the same "skip but report" rule.
        out = set()
        for p in sorted(b.glob("*.py")):
            if p.stem.startswith(SESSION_SCRIPT_PREFIX):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in _DEF_RE.finditer(text):
                out.add("%s.%s" % (p.stem, m.group(1)))
        return sorted(out)
    if scope == "module":
        # A module is a file the system IMPORTS. A `_`-prefixed session script is
        # not imported by anything — see the `function` branch above.
        return sorted(p.stem for p in b.glob("*.py")
                      if not p.stem.startswith(SESSION_SCRIPT_PREFIX))
    if scope == "channel":
        # A channel is the widest level, so it is NOT discovered from a file
        # name. It is the set of top-level DIRECTORIES that hold CODE.
        #
        # MEASURED DEFECT in the first version: it took EVERY non-underscore
        # directory, so the dry run proposed `debug_shots`, `evidence`,
        # `hb_snapshots`, `helper_watchdog_snaps` — OUTPUT directories, not
        # channels. A channel is a claim about where code RUNS, so a directory
        # that holds no code cannot be one. The test is therefore "does it hold
        # a .py file", which is checkable, rather than a name blocklist, which
        # would go stale on the next output directory.
        out = set()
        for p in sorted(b.iterdir()):
            if not p.is_dir() or p.name.startswith((".", "_")):
                continue
            if any(p.rglob("*.py")):
                out.add(p.name)
        return sorted(out)
    # ---- THE 4 SCOPES ADDED 2026-09-25 (plan_7B.FILL.ALL.REGISTERS) --------
    #
    # The human: "7M can help to fill *_registry now". MEASURED: these registers
    # are THIN while their SOURCE is large, so the data exists and is absent:
    #
    #     chat_registry              0 rows   vs chat_main                63
    #     purpose_route_registry     1 row    vs @app.route             257
    #     derived_column_registry    2 rows   vs derived_from          1949
    #     component_registry         8 rows   vs skill_registry           61
    #
    # EVERY ONE IS READ FROM THE SOURCE, NEVER FROM THE TARGET TABLE. Reading the
    # target would make the fill a tautology: it would "discover" exactly the keys
    # already registered and report a complete fill forever.
    if scope == "chat":
        # A chat is a SESSION the system has already recorded. `chat_main` is the
        # source of truth for "a chat exists", so the key is its PAIR KEY —
        # the stable identity, not the row id.
        #
        # MEASURED (2026-09-26): the pair key column is `chat_hash_recomputed`,
        # NOT `chat_hash`. `chat_hash` is stale on 52 of 58 rows (hashed with
        # `id` before `chat_id` existed) and is kept only as append-only audit.
        # Reading the stale column here would hand the register a WRONG key.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT chat_hash_recomputed FROM chat_main "
                        "WHERE chat_hash_recomputed IS NOT NULL "
                        "AND chat_hash_recomputed <> ''"):
                    out.add(str(r["chat_hash_recomputed"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    if scope == "purpose_route":
        # ---- THE SOURCE IS `identity_registry`, NOT `@app.route` ----------
        #
        # MEASURED DEFECT (2026-09-25): this branch COPIED the `api` branch, so
        # `discover("api")` and `discover("purpose_route")` returned the SAME 276
        # keys byte-for-byte. But the two registers need DIFFERENT key shapes:
        #
        #     api_registry.api_key            = 'POST /api/tasks/validate'  (METHOD + path)
        #     purpose_route_registry.route_key = 'chat_identity.worker_identity_flow'
        #                                        (purpose.workflow)
        #
        # So all 277 keys were refused `SOURCE_MISSING_COLUMN` — a CORRECT refusal
        # caused by a WRONG discovery. The refusal was right; the input was wrong.
        #
        # A route is `chat -> purpose -> service -> workflow`. The PURPOSE and the
        # WORKFLOW are BOTH recorded on `identity_registry` (`why` and
        # `workflow_id`), so the key is derivable from a real row:
        #
        #     why='establish the worker identity for a chat'  workflow_id=2
        #     -> 'establish_the_worker_identity_for_a_chat.worker_identity_flow'
        #
        # `ticket_origin` is NOT derivable — `identity_registry` has no service
        # column — so it is DECLARED in the phase's `required_extra`, and the
        # refusal names ONLY that column.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT DISTINCT i.why, w.workflow_key "
                        "  FROM identity_registry i "
                        "  JOIN workflow_registry w ON w.workflow_id = i.workflow_id "
                        " WHERE i.why IS NOT NULL AND i.why <> '' "
                        "   AND i.why <> 'NA' AND w.workflow_key IS NOT NULL "
                        "   AND w.workflow_key <> ''"):
                    purpose = str(r["why"]).strip()
                    slug = re.sub(r"[^a-z0-9]+", "_", purpose.lower()).strip("_")
                    if slug:
                        out.add("%s.%s" % (slug, r["workflow_key"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    if scope == "derived_column":
        # ---- THE SOURCE IS `DERIVED_COLUMN_SEED`, NOT `derived_from` ------
        #
        # MEASURED DEFECT (2026-09-25): this branch read
        # `db_field_registry.derived_from`, whose value is `'pragma:table_info'`
        # on ALL 1949 rows. That value is a PROVENANCE marker meaning "this
        # column came from PRAGMA" — i.e. it is a REAL column, NOT a derived one.
        #
        # So the branch read a column that says the OPPOSITE of what the phase
        # assumes, and produced 758 bare column names (`accuracy_pct`, `action`)
        # where the register needs `table_name.column_name`. All 756 were refused
        # `SOURCE_MISSING_COLUMN` — a CORRECT refusal caused by a WRONG discovery.
        #
        # The REAL derivations are DECLARED in `db_schema.DERIVED_COLUMN_SEED`,
        # each a MEASURED fact naming its input:
        #     ('chat_main', 'sha256',    'function', "session_id")
        #     ('chat_main', 'chat_hash', 'pair_key', "chat_id + '|' + session_id")
        #
        # MEASURED (2026-09-26): this line used to say `id + '|' + session_id`,
        # which is the OLD formula. 52 of 58 live rows still carry a hash of
        # `id`, so the old text described the STALE data, not the design.
        # The key is `table_name.column_name`, which is the register's own shape.
        out = set()
        try:
            import db_schema as _ds
            for tbl, col, _kind, _src in getattr(_ds, "DERIVED_COLUMN_SEED", ()):
                if tbl and col:
                    out.add("%s.%s" % (tbl, col))
        except Exception:
            return []
        return sorted(out)
    if scope == "component":
        # A component is a SKILL that produces a parsed output. The source is
        # `skill_registry`, and the key is the skill key.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT skill_key FROM skill_registry "
                        "WHERE skill_key IS NOT NULL AND skill_key <> ''"):
                    out.add(str(r["skill_key"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    # ---- THE 2 SCOPES ADDED 2026-09-25 (plan_FILL.REGISTERS.BY.SETTING) ----
    #
    # MEASURED: these registers are THIN while their SOURCE is large, and every
    # NOT NULL column IS derivable from the source:
    #
    #     study_registry    2 rows  vs skill_registry  61
    #     worker_registry   6 rows  vs llm_model       76
    #
    # READ FROM THE SOURCE, NEVER FROM THE TARGET TABLE. Reading the target would
    # make the fill a tautology: it would "discover" exactly the keys already
    # registered and report a complete fill forever.
    if scope == "study":
        # A study is a CASE a skill is studied against. The source is
        # `skill_registry`, and the key is the skill key — a study is named after
        # the skill it studies.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT skill_key FROM skill_registry "
                        "WHERE skill_key IS NOT NULL AND skill_key <> ''"):
                    out.add(str(r["skill_key"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    if scope == "worker":
        # A worker is a MODEL that does work. The source is `llm_model`, and the
        # key is the model's `model_id` — the stable identity, not the row id.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT model_id FROM llm_model "
                        "WHERE model_id IS NOT NULL AND model_id <> ''"):
                    out.add(str(r["model_id"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    if scope == "wording":
        # A wording is HOW A SKILL SAYS A THING. The source is `skill_registry`,
        # and the key is the skill key — a wording is named after the skill whose
        # wording it is.
        #
        # MEASURED (2026-09-25): `wording_registry` holds 38 rows covering only 4
        # distinct `skill_id` against 63 registered skills, so 59 skills have NO
        # wording. The source is the SKILL LIST, not the existing wording rows —
        # reading the target would make the fill a tautology.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT skill_key FROM skill_registry "
                        "WHERE skill_key IS NOT NULL AND skill_key <> ''"):
                    out.add(str(r["skill_key"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    if scope == "code":
        # A code row IS a located piece of code. The source is
        # `code_location_registry`, and the key is the location's own identity.
        #
        # MEASURED (2026-09-25): `code_registry` holds 303 rows against 298
        # `code_location_registry` rows. The source is the LOCATION list, not the
        # existing code rows.
        out = set()
        try:
            conn = sqlite3.connect(str(DB))
            conn.row_factory = sqlite3.Row
            try:
                for r in conn.execute(
                        "SELECT entity_type, entity_ref_id, version, file_path "
                        "FROM code_location_registry "
                        "WHERE file_path IS NOT NULL AND file_path <> ''"):
                    out.add("%s:%s:%s" % (r["entity_type"], r["entity_ref_id"],
                                          r["file_path"]))
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return sorted(out)
    return []


# ---------------------------------------------------------------------------
# the 7B drafts LANGUAGE only
# ---------------------------------------------------------------------------

def resolve_model(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """The text model, resolved from the `llm.text` route. Never a literal."""
    import llm_service_store as lss

    return lss.resolve_text_model(conn)


def _ask_7b(prompt: str, timeout: int = 180,
            resolved: dict[str, Any] | None = None) -> tuple[str, int]:
    import requests
    if resolved is None:
        resolved = resolve_model()
    model = str(resolved.get("model") or "")
    if not model:
        raise RuntimeError("no text model resolved from the llm.text route: %s"
                           % resolved.get("reason"))
    t0 = time.time()
    r = requests.post(OLLAMA, json={
        "model": model,
        "messages": [{"role": "system", "content": DRAFT_SYSTEM},
                     {"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0},
    }, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"], int((time.time() - t0) * 1000)


def _extract_json(text: str) -> dict | None:
    """Parse the 7B's JSON, tolerating the ONE malformation it actually makes.

    MEASURED (2026-09-25, the `function` phase): 26 keys were refused
    `LLM_FAILED` on EVERY round — 60+ attempts each — and the loop could never
    advance past them. The cause is NOT a model failure to answer; the model
    answers every time. It emits a JSON object whose `name` and `description`
    KEYS are missing their OPENING QUOTE:

        {
          "key": "diag-find-thinking-apply-patch",
          name": "diag_find_thinking.apply_patch",
          description": "A function in t...

    `json.loads` rejects that, so a perfectly usable answer was discarded and the
    key was retried forever. A retry loop that can never succeed is a loop, not a
    retry.

    The repair is NARROW and stated: a `"` is inserted before a bare
    `name"`/`description"`/`key"` that follows a `{` or `,`. It does not attempt
    general JSON repair — a broad repair would accept genuinely broken output and
    hide a real model failure.
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    body = m.group(0)
    try:
        return json.loads(body)
    except Exception:
        pass
    # The measured malformation: a TRAILING COMMA before the closing brace.
    # MEASURED (2026-09-25): the 7B emits
    #     {..., "description": "...",\n}
    # and `json.loads` rejects it, so a usable answer was discarded and the key
    # was retried forever (26 keys, 60+ attempts each).
    repaired = re.sub(r",(\s*[}\]])", r"\1", body)
    if repaired != body:
        try:
            return json.loads(repaired)
        except Exception:
            pass
    # A second measured malformation: a bare key with NO quotes at all, or with
    # only the CLOSING quote. MEASURED (2026-09-25): the 7B emits
    #     {..., name: "x", description: "y"}
    # and also
    #     {..., name": "x", description": "y"}
    # Both are rejected by `json.loads`, so a usable answer was discarded and the
    # key was retried forever. The repair inserts the missing opening quote.
    repaired2 = re.sub(r'([{,]\s*)(name|description|key)\s*"?:',
                       r'\1"\2":', body)
    if repaired2 != body:
        try:
            return json.loads(repaired2)
        except Exception:
            pass
    return None


def draft(key: str, scope: str,
          resolved: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the 7B for the NAME and the DESCRIPTION. Never for a citation."""
    ask = ("Name this thing from a software repository: %s\n"
           "It is a %s in this project." % (key, scope)).strip()
    try:
        raw, ms = _ask_7b(ask, resolved=resolved)
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "llm_ms": None}
    obj = _extract_json(raw)
    if not obj:
        return {"ok": False, "error": "not JSON", "raw": raw[:200], "llm_ms": ms}
    if obj.get("refuse"):
        return {"ok": False, "refused": True, "reason": str(obj["refuse"]),
                "llm_ms": ms}
    # THE KEY IS THE DISCOVERED KEY, NOT THE MODEL'S.
    #
    # MEASURED DEFECT (this session, the first real `channel` run): the 7B
    # returned `llm_task_monitor_ui-channel` and `src_directory` for the
    # discovered keys `llm_task_monitor_ui` and `src`. The register then held a
    # key that DISCOVERY CANNOT PRODUCE, so the next run would propose the
    # original key again and the row would be written a second time under a
    # different name — the register would grow duplicates that no run can
    # reconcile.
    #
    # The 7B drafts the LANGUAGE (name, description). The KEY is a fact read from
    # the source, so it is taken from the source. `model_key` is kept for review,
    # so a disagreement is VISIBLE rather than silently applied.
    model_key = str(obj.get("key") or "").strip()
    return {"ok": True, "key": key, "model_key": model_key,
            "key_agrees": (model_key == key),
            "name": str(obj.get("name") or key),
            "description": str(obj.get("description") or ""),
            "llm_ms": ms, "raw": raw[:200]}


def _cite_declaration(key: str, scope: str, base: Path) -> str:
    """`path:line` where the key is DECLARED, for a scope with a known source file.

    WHY THIS EXISTS — MEASURED DEFECT (2026-09-25)
    ----------------------------------------------
    The loose token search cited `app.app_id` to `_diag_cite_for_choice.py:40` —
    a one-off diagnostic that merely MENTIONS the name. That citation is
    checkable AND wrong, which is worse than none because it is believed.

    A key discovered from a KNOWN FILE has an honest citation: the line in that
    file where the key is declared. The pattern is derived from the key's own
    shape, so it cannot drift from the discovery:

        db_field   `table.column`  -> `CREATE TABLE ... table` then the column
        function   `module.func`   -> `def func(` in `module.py`
        module     `module`        -> line 1 of `module.py`

    Returns "" when no declaration line is found, so the caller falls through to
    the other branches rather than inventing a line.
    """
    if scope == "db_field":
        # The key is `table.column`. Find the CREATE TABLE for `table`, then the
        # first line inside its body that declares `column`.
        if "." not in key:
            return ""
        table, column = key.split(".", 1)
        src = base / "db_schema.py"
        if not src.is_file():
            return ""
        try:
            lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        in_table = False
        for i, line in enumerate(lines, 1):
            if re.match(r"\s*CREATE TABLE IF NOT EXISTS\s+%s\s*\(" % re.escape(table),
                        line, re.IGNORECASE):
                in_table = True
                continue
            if in_table:
                if re.match(r"\s*\)\s*;", line):
                    break
                if re.match(r"\s*%s\s+[A-Z]" % re.escape(column), line):
                    return "db_schema.py:%d" % i
        return ""
    if scope == "function":
        # The key is `module.func`. The declaration is `def func(` in `module.py`.
        if "." not in key:
            return ""
        module, func = key.split(".", 1)
        src = base / (module + ".py")
        if not src.is_file():
            return ""
        try:
            lines = src.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        pat = re.compile(r"^\s*(?:async\s+)?def\s+%s\s*\(" % re.escape(func))
        for i, line in enumerate(lines, 1):
            if pat.match(line):
                return "%s.py:%d" % (module, i)
        return ""
    if scope == "module":
        # The key IS the file stem. The declaration is line 1.
        src = base / (key + ".py")
        if src.is_file():
            return "%s.py:1" % key
        return ""
    if scope == "wording":
        # The key IS a skill key, and a skill is DECLARED in its own
        # `*.skill.md`. MEASURED (2026-09-25): the skill directory is
        # `skills/<taxonomy>/<skill_key>/<skill_key>.skill.md`, so the citation
        # is the declaration file's line 1 — a real, checkable path.
        for p in sorted((base / "skills").rglob("%s.skill.md" % key)):
            try:
                rel = p.relative_to(base).as_posix()
            except ValueError:
                continue
            return "%s:1" % rel
        return ""
    if scope == "code":
        # The key is `entity_type:entity_ref_id:file_path`, and the file_path IS
        # the declaration. MEASURED: `code_location_registry.file_path` holds a
        # repo-relative path, so the citation is that path's line 1.
        if key.count(":") < 2:
            return ""
        file_path = key.split(":", 2)[2]
        if (base / file_path).is_file():
            return "%s:1" % file_path.replace("\\", "/")
        return ""
    return ""


def cite_for(key: str, base: Path | None = None,
             scope: str = "") -> tuple[str, bool]:
    """`(cite_ref, supplied)`. The LOGIC GENERATOR supplies and verifies it.

    `scope` names WHERE the key was discovered. It is needed for a key whose
    source is a DATABASE TABLE rather than a file (see branch 4): a `chat` key is
    a `chat_hash`, no file is named that, and the honest citation is the
    measurement that produced it.

    THE RULE, and it is the whole function: **a citation must point at the file
    the key NAMES, when such a file exists.** `locate_name` finds the first file
    containing the token, which is NOT the same thing — MEASURED
    (`_diag_cite_for_choice.py`):

        llm_task_monitor_ui  -> _audit_question_sources.py:42   (WRONG file)
        src                  -> _apply_cap_rename.py:19         (WRONG file)
        scripts              -> _cleanup_registry_fill_miskey.py:5 (WRONG file)

    Each of those keys NAMES a directory or a module, and a file of that name
    exists. Citing a different file that merely mentions the word is a citation
    that is checkable AND wrong — worse than none, because it is believed.

    So the order is:
      1. the file the key NAMES (a module key IS a file; a channel key IS a dir)
      2. the whole key as a token, in any file
      3. the parts that are real tokens, with the SAME-FILE requirement

    MEASURED DEFECT in an earlier version of this function: it tried the loose
    parts FIRST, so `_backfill_task_center` was cited to
    `_proof_chat_registry.py:44` (via the segment `center`). 49 of 141
    descriptions then shared no vocabulary with their own citation.
    """
    b = base or BASE

    # ---- 0. A KEY WHOSE SOURCE IS A DATABASE TABLE, NOT A FILE -----------
    #
    # THIS IS CHECKED FIRST, and the order is the fix. MEASURED (2026-09-25, the
    # first real `worker` dry run): only 5 of 40 keys got a citation, and the
    # ones that did were WRONG — a DeepSeek model id was cited to
    # `_proof_pick_flow.py:228`, a file that merely MENTIONS the name.
    #
    # A citation that is checkable AND wrong is worse than none, because it is
    # believed. When the scope DECLARES its source table, that declaration is the
    # honest answer and no file search can improve on it.
    #
    # `terminology_cite` accepts a `measured:` note for exactly this case, and it
    # is CHECKABLE — a reader can re-run the query.
    src = _SOURCE_OF_SCOPE.get(scope)
    if src:
        note = "measured: %s.%s = %s" % (src[0], src[1], key)
        ok, _why = tc.verify_cite_ref(note, b)
        if ok:
            return note, True

    # ---- 0b. A KEY WHOSE SOURCE IS A NAMED FILE, AT A NAMED LINE ---------
    #
    # MEASURED (2026-09-25, the `db_field`/`function`/`module` re-run): the loose
    # token search (branches 2/3) produced citations that were checkable AND
    # WRONG — `app.app_id` was cited to `_diag_cite_for_choice.py:40`, a one-off
    # diagnostic that merely MENTIONS the name. A citation that is checkable and
    # wrong is worse than none, because it is believed.
    #
    # For a scope whose source is a KNOWN FILE, the honest citation is the LINE
    # in that file where the key is DECLARED. It is checkable (a reader opens the
    # line) and it is about the right thing (it is the declaration itself). The
    # pattern is derived from the key's own shape, so it cannot drift.
    hit = _cite_declaration(key, scope, b)
    if hit:
        ok, _why = tc.verify_cite_ref(hit, b)
        if ok:
            return hit, True

    # ---- 1. THE FILE THE KEY NAMES --------------------------------------
    # A module key is a file stem; a channel key is a directory. Both are
    # checkable, and both are the RIGHT answer when they exist.
    #
    # MEASURED, and it is why this is FIRST: `locate_name` returns the first file
    # CONTAINING the token, which is not the file the key NAMES. Measured
    # (`_diag_cite_for_choice.py`):
    #     llm_task_monitor_ui -> _audit_question_sources.py:42   (WRONG file)
    #     src                 -> _apply_cap_rename.py:19         (WRONG file)
    #     scripts             -> _cleanup_registry_fill_miskey.py:5 (WRONG file)
    # Each of those keys NAMES a directory or a module, and a file of that name
    # exists. Citing a different file that merely mentions the word is a citation
    # that is checkable AND wrong — worse than none, because it is believed.
    #
    # MEASURED DEFECT (2026-09-25, the `db_field`/`function`/`module` re-run):
    # this branch did `key.split(".")[0].lstrip("_")`, which STRIPS the leading
    # underscore. But `discover()` builds a module/function key from `p.stem`,
    # and `Path("_apply_cap_rename.py").stem` is `_apply_cap_rename` — WITH the
    # underscore. So the stripped name `apply_cap_rename` named a file that does
    # not exist, branch 1 missed for EVERY underscore-prefixed module, and the
    # key fell through to branch 3's order-dependent tail search. MEASURED:
    # `_audit_question_sources.main` got NO citation while `_apply_cap_rename.main`
    # got one only by accident (the tail `main` happened to resolve to a file
    # whose path contained the head).
    #
    # The fix is to try the EXACT head FIRST — it is the file stem the key was
    # built from — and only then the lstripped variant, which is the right answer
    # for a key that names a file WITHOUT the underscore (e.g. a hand-written
    # `foo.bar` for `_foo.py`).
    head = key.split(".")[0]
    for stem in (head, head.lstrip("_")):
        named_file = b / (stem + ".py")
        if named_file.is_file():
            break
    else:
        named_file = None
    if named_file is not None:
        # The file EXISTS, so it is the citation. The line is the first line that
        # mentions the stem, or line 1 when the module does not name itself.
        hit = tc.locate_name(stem, b)
        if hit and Path(hit.split(":")[0]).stem == stem:
            ok, _why = tc.verify_cite_ref(hit, b)
            if ok:
                return hit, True
        return "%s.py:1" % stem, True

    # ---- 1b. THE DIRECTORY THE KEY NAMES --------------------------------
    # A `channel` key names a DIRECTORY (see `discover`), so there is no file of
    # that name and branch 1 cannot fire. MEASURED DEFECT this fixes: the key
    # `llm_task_monitor_ui` fell through to branch 2 and was cited to
    # `_audit_question_sources.py:42` — a file that merely MENTIONS the name.
    # The citation for a channel is a real source file INSIDE the channel, which
    # is both checkable and about the right thing. Sorted, so it is stable.
    named_dir = b / stem
    if named_dir.is_dir():
        # Ordering, so the citation is a REPRESENTATIVE file and not a one-off
        # script: a file named after the channel first, then a non-underscore
        # file, then anything. MEASURED: plain sorted() picked
        # `llm_task_monitor_ui/_fix_nav_and_skills.py`, a one-off fix script.
        cands = sorted(named_dir.rglob("*.py"),
                       key=lambda p: (p.stem != stem,
                                      p.name.startswith("_"),
                                      p.as_posix()))
        for p in cands:
            rel = p.relative_to(b).as_posix()
            ok, _why = tc.verify_cite_ref("%s:1" % rel, b)
            if ok:
                return "%s:1" % rel, True

    # ---- 2. the whole key as a token ------------------------------------
    hit = tc.locate_name(key, b)
    if hit:
        ok, _why = tc.verify_cite_ref(hit, b)
        if ok:
            return hit, True

    # ---- 3. the parts that ARE tokens, requiring the SAME FILE ----------
    if "." in key:
        head, tail = key.split(".", 1)
        tail = tail.split(".")[-1]
        cand = tc.locate_name(tail, b)
        if cand:
            ok, _why = tc.verify_cite_ref(cand, b)
            if ok and (head in cand or head == Path(cand.split(":")[0]).stem):
                return cand, True
    if " " in key:
        for part in reversed(key.split()):
            if len(part) < 4:
                continue
            cand = tc.locate_name(part, b)
            if cand:
                ok, _why = tc.verify_cite_ref(cand, b)
                if ok:
                    return cand, True
    # ---- 4. A KEY WHOSE SOURCE IS A DATABASE TABLE, NOT A FILE -----------
    #
    # MEASURED (2026-09-25, the first real `chat` run): all 40 keys were refused
    # `NO_CHECKABLE_CITE`. The reason is structural, not a model failure: a
    # `chat` key is a `chat_hash` — a 64-char hex string — and NO FILE is named
    # that, so branches 1, 1b, 2 and 3 all miss. The same is true of any key
    # discovered from a TABLE rather than from a filename.
    #
    # The honest citation for such a key is the MEASUREMENT that produced it:
    # which table and column the key was read from. `terminology_cite` accepts a
    # `measured:` note for exactly this case ("Allowed, but it must SAY it is a
    # measurement, so a reader knows it is not a file to open"), and it is
    # CHECKABLE — a reader can re-run the query.
    #
    # A citation that is checkable AND about the right thing beats a file that
    # merely mentions the word, which is the defect branch 1's docstring records.
    src = _SOURCE_OF_SCOPE.get(scope)
    if src:
        note = "measured: %s.%s = %s" % (src[0], src[1], key)
        ok, _why = tc.verify_cite_ref(note, b)
        if ok:
            return note, True
    return "", False


# ---------------------------------------------------------------------------
# run ONE phase, capped
# ---------------------------------------------------------------------------

def run_phase(conn: sqlite3.Connection, phase_key: str, *,
              apply: bool = False, base: Path | None = None,
              use_llm: bool = True,
              keys: list[str] | None = None) -> dict[str, Any]:
    """Run one phase. NEVER processes more than the phase's `cap`.

    `apply=False` is a DRY RUN: it drafts and cites but writes nothing.

    `keys` overrides the discovered list, so a PROOF can force a specific input
    (e.g. a key that cannot be cited) and observe the refusal. The cap still
    applies to the override, so the volume control cannot be bypassed.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT phase_key, scope, target_table, key_col, cap FROM "
        "phase_registry WHERE phase_kind=? AND phase_key=? AND is_active=1",
        (PHASE_KIND, phase_key)).fetchone()
    if not row:
        return {"ok": False, "error_code": "UNKNOWN_PHASE",
                "error": "no active phase %r" % phase_key}
    scope, cap = row["scope"], int(row["cap"])
    table, key_col = row["target_table"], row["key_col"]
    phase = next((p for p in PHASES if p["phase_key"] == phase_key), None)

    keys = list(keys) if keys is not None else discover(scope, base)
    total = len(keys)
    # ---- THE CAP MUST SKIP WHAT IS ALREADY DONE --------------------------
    #
    # MEASURED (2026-09-25, the second `component`/`study`/`worker` run): every
    # key was refused `ALREADY_REGISTERED` and NOTHING new was filled.
    #
    # The cause is `keys[:cap]`. `discover()` returns a SORTED list, so the cap
    # always took the SAME first 40 — the ones already registered. The remaining
    # keys could never be reached, however many times the phase ran.
    #
    # This is the same defect class `proof_gate.read_stamp` records: "a bound that
    # silently discards the overflow turns 'I capped the work' into 'I claimed the
    # work was done'." A cap that re-picks finished work is a cap that never
    # advances.
    #
    # So the cap is applied to the keys that are NOT yet registered. The
    # `ALREADY_REGISTERED` refusal stays (it is the write site's own guard), but
    # it is no longer what the cap spends its budget on.
    done: set[str] = set()
    try:
        for r in conn.execute("SELECT %s FROM %s" % (key_col, table)):
            done.add(str(r[0]))
    except sqlite3.Error:
        done = set()
    # ---- A KEY WITH A TERMINAL REFUSAL IS ALSO "DONE" --------------------
    #
    # MEASURED (2026-09-25, the `db_field` re-run): 12 keys were refused
    # `PARENT_NOT_FOUND` and are NEVER written, so they stayed in `remaining`
    # forever and the cap re-picked them EVERY round — 12 of every 40 slots spent
    # re-deciding keys already decided. That is the same defect class as the
    # `ALREADY_REGISTERED` cap bug above: a cap that re-picks finished work never
    # advances.
    #
    # A key with a TERMINAL outcome (accepted OR refused) has been DECIDED. A
    # refusal is a FINDING, not a pending item, so it must not consume the cap
    # again. `skipped` (LLM_FAILED / DRY_RUN) is NOT terminal — a transient LLM
    # failure must be retried, so those keys stay in `remaining`.
    #
    # ---- BUT A REFUSAL ABOUT THE *CODE* IS NOT A REFUSAL ABOUT THE KEY ----
    #
    # MEASURED (2026-09-25, after fixing the two discovery sources): the corrected
    # phases returned 0 keys, because the SAME keys had been refused under the OLD
    # code and were therefore "terminal". But `WRITE_FAILED` and
    # `SOURCE_MISSING_COLUMN` are claims about what the CODE can do — and the code
    # had just changed. Treating them as terminal made the fix UNTESTABLE: the
    # module would never re-attempt a key whose refusal it had just repaired.
    #
    # So the CODE-level refusals are RETRYABLE, and the KEY-level ones are
    # terminal. The test is: is the refusal a claim about what the CODE can do,
    # or a fact about the KEY (or its parent)?
    #
    #   WRITE_FAILED          the code could not write it        -> CODE
    #   SOURCE_MISSING_COLUMN the code could not derive a column -> CODE
    #   NO_CHECKABLE_CITE     the code could not find a citation -> CODE
    #   ALREADY_REGISTERED    the key IS in the table            -> KEY (a fact)
    #   PARENT_NOT_FOUND      the parent is NOT in its table     -> KEY (a fact)
    #
    # A code change cannot make a registered key un-registered, nor make a missing
    # parent appear. It CAN make a write succeed, a column derivable, or a
    # citation findable. Nothing is deleted: a retry APPENDS a new outcome.
    CODE_LEVEL_REFUSALS = ("WRITE_FAILED", "SOURCE_MISSING_COLUMN",
                           "NO_CHECKABLE_CITE")
    try:
        for r in conn.execute(
                "SELECT DISTINCT key_text FROM register_fill_run "
                " WHERE phase_key=? AND outcome IN ('accepted','refused') "
                "   AND IFNULL(refusal_code,'') NOT IN (?,?)",
                (phase_key, *CODE_LEVEL_REFUSALS)):
            done.add(str(r[0]))
    except sqlite3.Error:
        pass
    remaining = [k for k in keys if str(k) not in done]
    picked = remaining[:cap]
    truncated = len(remaining) > len(picked)

    resolved = resolve_model() if use_llm else {"model": "", "source": "not_used",
                                                "pool": [], "reason": ""}
    if use_llm and not resolved.get("model"):
        return {"ok": False, "error_code": "NO_TEXT_MODEL",
                "error": "the llm.text route resolved no model: %s"
                         % resolved.get("reason")}

    results: list[dict[str, Any]] = []
    accepted = refused = 0
    supplied_count = 0
    latencies: list[int] = []
    recorded = dropped = 0

    def record(key_text: str, outcome: str, code: str, row_id: Any,
               cite: str, supplied: bool, llm_ms: Any) -> None:
        """Append ONE outcome. EVERY outcome is recorded.

        MEASURED DEFECT (in `terminology_sweep`, fixed there and not repeated
        here): the first version INSERTed only on the success path, so a
        `NO_CHECKABLE_CITE` refusal was COUNTED and never WRITTEN — invisible in
        the very log that exists to make refusals reviewable.
        """
        nonlocal recorded, dropped
        cur = conn.execute(
            "INSERT OR IGNORE INTO register_fill_run "
            "(phase_key, key_text, outcome, refusal_code, row_id, llm_ms, "
            " cite_supplied, cite_ref, observed_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (phase_key, key_text, outcome, code, row_id, llm_ms,
             1 if supplied else 0, cite, _now_us()))
        if cur.rowcount:
            recorded += 1
        else:
            dropped += 1

    for key in picked:
        # ---- CITE FIRST, THEN ASK THE 7B --------------------------------
        #
        # MEASURED (2026-09-25): the loop asked the 7B for EVERY key and only
        # then computed the citation. A key that cannot be cited is REFUSED
        # regardless of what the 7B said, so the LLM call was WASTED — and at
        # ~0.7s per call over 1297 db_field keys that is ~15 minutes spent
        # drafting descriptions for rows that can never be written.
        #
        # The citation is DETERMINISTIC and cheap; the 7B is the expensive part.
        # Computing the citation first means the 7B is asked ONLY for keys that
        # can actually be written, which is both faster and more honest: the
        # refusal is decided by the gate, not by the model.
        cite, supplied = cite_for(key, base, scope)
        if supplied:
            supplied_count += 1
        if not cite:
            record(key, "refused", "NO_CHECKABLE_CITE", None, "", False, None)
            results.append({"key": key, "outcome": "refused",
                            "refusal_code": "NO_CHECKABLE_CITE", "llm_ms": None})
            refused += 1
            continue
        d = draft(key, scope, resolved=resolved) if use_llm else {
            "ok": True, "key": key, "name": key,
            "description": "a %s from the source" % scope, "llm_ms": None}
        if d.get("llm_ms") is not None:
            latencies.append(int(d["llm_ms"]))
        if not d.get("ok"):
            code = "LLM_REFUSED" if d.get("refused") else "LLM_FAILED"
            record(key, "skipped", code, None, cite, supplied, d.get("llm_ms"))
            results.append({"key": key, "outcome": "skipped",
                            "refusal_code": code, "llm_ms": d.get("llm_ms")})
            continue
        if not apply:
            record(key, "skipped", "DRY_RUN", None, cite, supplied,
                   d.get("llm_ms"))
            results.append({"key": key, "outcome": "skipped",
                            "refusal_code": "DRY_RUN", "cite_ref": cite,
                            "cite_supplied": supplied,
                            "llm_ms": d.get("llm_ms")})
            continue
        res = _write_row(conn, phase, table, key_col, d, cite)
        if res.get("ok"):
            accepted += 1
            outcome, code, row_id = "accepted", "", res.get("row_id")
        else:
            refused += 1
            outcome, code, row_id = "refused", str(res.get("code")), None
        record(key, outcome, code, row_id, cite, supplied, d.get("llm_ms"))
        results.append({"key": key, "outcome": outcome, "refusal_code": code,
                        "row_id": row_id, "cite_ref": cite,
                        "cite_supplied": supplied, "llm_ms": d.get("llm_ms"),
                        # The model's own key, so a disagreement is VISIBLE.
                        "model_key": d.get("model_key"),
                        "key_agrees": d.get("key_agrees")})
    conn.commit()

    # ---- THE CONTROL -----------------------------------------------------
    # PERSISTED, not merely returned. MEASURED DEFECT this fixes: the control ran
    # and its verdict lived only in this dict, so it vanished when the call
    # returned — and `_diag_7b_fill_quality.py` then printed a HARDCODED
    # "the CONTROL fails" that was the OPPOSITE of the truth. A verdict that is
    # not written down gets replaced by a guess.
    control = {"ran": False}
    if use_llm:
        cd = draft(CONTROL_KEY, scope, resolved=resolved)
        control = {"ran": True, "refused": bool(cd.get("refused")),
                   "ok": bool(cd.get("ok")),
                   "reason": str(cd.get("reason") or cd.get("error") or "")[:120]}
        control["pass"] = bool(cd.get("refused"))
        if not control["pass"] and cd.get("ok"):
            control["invented"] = str(cd.get("key") or "")[:60]
            control["note"] = (
                "the 7B produced a confident name for a key that is NOT in the "
                "source, so it cannot tell a real key from a fake one — its "
                "descriptions must be REVIEWED, not trusted")
        conn.execute(
            "INSERT INTO register_fill_control (phase_key, control_key, ran, "
            "passed, refused, invented, reason, model, observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (phase_key, observed_at) DO UPDATE SET "
            "  ran=excluded.ran, passed=excluded.passed, "
            "  refused=excluded.refused, invented=excluded.invented, "
            "  reason=excluded.reason, model=excluded.model",
            (phase_key, CONTROL_KEY, 1, 1 if control["pass"] else 0,
             1 if control.get("refused") else 0,
             str(control.get("invented") or ""), str(control.get("reason") or ""),
             str(resolved.get("model") or ""), _now_us()))
        conn.commit()

    lat_sorted = sorted(latencies)
    # ---- THE LESSON LOOP IS AUTOMATIC, NOT A SEPARATE TASK ----------------
    #
    # THE HUMAN (2026-09-25): "why 3 not auto? need to ask and have task for us?
    # i don't understand!!! it is BUG"
    #
    # THE HUMAN IS RIGHT. MEASURED: every piece already existed —
    #     skill_lesson table, skill_learning.add_lesson() (with a citation write
    #     gate), screen_for_lessons(), the `skill_case_lesson_ingestor` skill, and
    #     an AUTOMATIC caller in `auto_rect_audit.py` (dedup on `source_ref`).
    # And the gap: 2,693 refusals recorded, 0 filed as lessons, because
    # `register_fill.py` never called `add_lesson`.
    #
    # So the loop was UNWIRED, not missing. A refusal that is recorded but never
    # filed teaches nothing, and the next run repeats it — which is exactly what
    # happened: the same 277 keys were refused on every round.
    lessons = file_refusal_lessons(conn, phase_key) if apply else {
        "ok": True, "filed": 0, "skipped": "dry run"}
    return {
        "ok": True, "phase_key": phase_key, "scope": scope, "cap": cap,
        "target_table": table, "total": total, "returned": len(picked),
        "truncated": truncated, "applied": apply,
        "model": str(resolved.get("model") or ""),
        "model_source": str(resolved.get("source") or ""),
        "accepted": accepted, "refused": refused,
        "supplied_citations": supplied_count,
        "llm_calls": len(latencies),
        "llm_ms_p50": lat_sorted[len(lat_sorted) // 2] if lat_sorted else None,
        "llm_ms_max": max(latencies) if latencies else None,
        "control": control,
        "recorded": recorded, "dropped": dropped,
        "lessons": lessons,
        "results": results,
    }


# ---------------------------------------------------------------------------
# THE LESSON LOOP — a refusal becomes a lesson, AUTOMATICALLY
# ---------------------------------------------------------------------------

# The skill each refusal code teaches. A refusal is a FINDING about a RULE, so
# it belongs to the skill that owns that rule. The mapping is explicit and
# reviewable, because a silent fuzzy match would file a lesson against the wrong
# skill and look like learning.
_REFUSAL_SKILL: dict[str, str] = {
    "SOURCE_MISSING_COLUMN": "env_task_proof",
    "PARENT_NOT_FOUND": "env_task_proof",
    "NO_CHECKABLE_CITE": "citation_discipline",
    "WRITE_FAILED": "systematic_debugging",
    "ALREADY_REGISTERED": "systematic_debugging",
    "LLM_FAILED": "prompt_measurement_discipline",
    "LLM_REFUSED": "prompt_measurement_discipline",
}

# What each refusal code MEANS, so the lesson text states the rule rather than
# restating the error string.
_REFUSAL_MEANING: dict[str, str] = {
    "SOURCE_MISSING_COLUMN":
        "a register needs a NOT NULL column the source cannot supply; the "
        "refusal must NAME the column, and the DECLARATION must be re-checked "
        "against the source before it is trusted",
    "PARENT_NOT_FOUND":
        "a child row's parent does not resolve; a defaulted FK is a phantom "
        "reference that verifies and lies, so the refusal is correct",
    "NO_CHECKABLE_CITE":
        "a key has no citation that resolves; a citation that is checkable AND "
        "wrong is worse than none, because it is believed",
    "WRITE_FAILED":
        "the write hit a constraint the phase did not declare; an opaque "
        "constraint error is a CRASH, not a finding",
    "ALREADY_REGISTERED":
        "the key is already in the target table; the cap must skip decided work "
        "or it re-picks the same keys forever",
    "LLM_FAILED":
        "the model answered but the reply could not be parsed; a retry loop that "
        "can never succeed is a loop, not a retry",
    "LLM_REFUSED":
        "the model refused; a refusal is a real outcome and must not be read as "
        "a pass",
}


def file_refusal_lessons(conn: sqlite3.Connection, phase_key: str) -> dict[str, Any]:
    """File ONE lesson per `(phase_key, refusal_code)`. AUTOMATIC, deduped.

    THE HUMAN'S POINT (2026-09-25): this must not need a separate task. The
    infrastructure exists and `auto_rect_audit.py` already uses it; the loop was
    simply never wired here.

    DEDUP IS ON `source_ref`, which is the SAME identity `add_lesson` uses, so a
    re-run files nothing new. The citation is CHECKABLE — it names the command
    that reproduces the refusal — because `add_lesson` REFUSES an uncited lesson
    at the write site, and a lesson with no reference is a memory, not a finding.
    """
    import skill_learning as sl
    rows = [dict(r) for r in conn.execute(
        "SELECT refusal_code, COUNT(DISTINCT key_text) keys, "
        "       MIN(key_text) example "
        "  FROM register_fill_run "
        " WHERE phase_key=? AND outcome='refused' AND refusal_code <> '' "
        " GROUP BY refusal_code ORDER BY keys DESC", (phase_key,))]
    filed, skipped = 0, 0
    for r in rows:
        code = str(r["refusal_code"])
        skill = _REFUSAL_SKILL.get(code)
        if not skill:
            skipped += 1
            continue
        # THE CITATION IS THE COMMAND THAT REPRODUCES IT — checkable by re-running.
        cite = "python register_fill.py --report %s" % phase_key
        lesson = ("register_fill phase %r refused %d key(s) with %s. %s. "
                  "Example key: %s"
                  % (phase_key, r["keys"], code,
                     _REFUSAL_MEANING.get(code, "see the refusal code"),
                     str(r["example"])[:80]))
        try:
            res = sl.add_lesson(skill, lesson, source_type="self_fail",
                                root_cause=code, source_ref=cite,
                                status="draft", conn=conn)
        except Exception as exc:
            skipped += 1
            continue
        if res.get("created") is False:
            skipped += 1
        else:
            filed += 1
    return {"ok": True, "phase_key": phase_key, "groups": len(rows),
            "filed": filed, "skipped": skipped}


def _derive_extra_from_source(key: str, scope: str,
                              conn: sqlite3.Connection) -> dict[str, Any]:
    """The NOT NULL columns a phase needs, READ from the source row.

    WHY THIS EXISTS — MEASURED (2026-09-25)
    ---------------------------------------
    `purpose_route` and `derived_column` each have NOT NULL columns that the KEY
    alone does not carry. The key is a NAME; the row needs the VALUES behind it.
    Both are derivable from the SAME source `discover()` read, so this re-reads
    that source and returns the values — it never invents one.

        purpose_route  key `slug.workflow_key` -> purpose_key (the sentence),
                                                  workflow_key
        derived_column key `table.column`      -> kind, derived_from

    Returns {} when the key is not in the source, so the caller falls through to
    the declared-missing refusal rather than writing a half row.
    """
    if scope == "purpose_route":
        if "." not in key:
            return {}
        slug, workflow_key = key.rsplit(".", 1)
        try:
            for r in conn.execute(
                    "SELECT DISTINCT i.why, w.workflow_key "
                    "  FROM identity_registry i "
                    "  JOIN workflow_registry w ON w.workflow_id = i.workflow_id "
                    " WHERE i.why IS NOT NULL AND i.why <> '' "
                    "   AND i.why <> 'NA'"):
                purpose = str(r["why"]).strip()
                s = re.sub(r"[^a-z0-9]+", "_", purpose.lower()).strip("_")
                if s == slug and str(r["workflow_key"]) == workflow_key:
                    return {"purpose_key": purpose,
                            "workflow_key": str(r["workflow_key"])}
        except sqlite3.Error:
            return {}
        return {}
    if scope == "derived_column":
        if "." not in key:
            return {}
        table, column = key.split(".", 1)
        try:
            import db_schema as _ds
            for tbl, col, kind, src in getattr(_ds, "DERIVED_COLUMN_SEED", ()):
                if tbl == table and col == column:
                    # ---- THE KEY IS A COMPOSITE; THE COLUMN IS ITS TAIL ----
                    #
                    # MEASURED (2026-09-25): the discovered key is
                    # `table.column` (the IDENTITY used for dedup and the log),
                    # but `derived_column_registry.column_name` holds the BARE
                    # column. Writing the key straight into `column_name` produced
                    # `chat_main.chat_hash` where the register's own rows say
                    # `chat_hash`. So the column value is the part AFTER the dot.
                    return {"table_name": tbl, "column_name": col, "kind": kind,
                            "derived_from": src}
        except Exception:
            return {}
        return {}
    if scope == "wording":
        # A wording belongs to a SKILL, so `skill_id` is READ from the source row
        # rather than invented. MEASURED: `wording_registry.skill_id` is NOT NULL.
        try:
            r = conn.execute(
                "SELECT skill_id FROM skill_registry WHERE skill_key=?",
                (key,)).fetchone()
            if r is not None:
                return {"skill_id": int(r["skill_id"])}
        except sqlite3.Error:
            return {}
        return {}
    if scope == "code":
        # A code row IS a located piece of code, so the location's own values are
        # READ from the source row. MEASURED: `code_registry.register_id`,
        # `module_name` and `function_name` are NOT NULL.
        if key.count(":") < 2:
            return {}
        entity_type, entity_ref_id, file_path = key.split(":", 2)
        try:
            r = conn.execute(
                "SELECT location_id FROM code_location_registry "
                " WHERE entity_type=? AND entity_ref_id=? AND file_path=?",
                (entity_type, entity_ref_id, file_path)).fetchone()
            if r is not None:
                return {"register_id": int(r["location_id"]),
                        "module_name": Path(file_path).stem,
                        "function_name": Path(file_path).stem}
        except sqlite3.Error:
            return {}
        return {}
    return {}


def _pk_col_for(table: str) -> str:
    """The PRIMARY KEY column of a register table.

    A FK points at the PK, never at the human-readable key column — the same rule
    `hardcode_scope._PK` records. Derived from the table name so a new register
    does not need a new branch.
    """
    return {
        "channel_registry": "channel_id",
        "module_registry": "module_id",
        "capability_registry": "capability_id",
        "db_table_registry": "db_table_id",
        "db_field_registry": "db_field_id",
        # MEASURED (2026-09-25): `ticket`'s primary key is `id`, NOT `ticket_id`.
        # The derived fallback (`table.replace("_registry","_id")`) produced
        # `ticket`, which is not a column at all, so the parent lookup raised
        # `no such column: ticket` instead of resolving or refusing.
        "ticket": "id",
    }.get(table, table.replace("_registry", "_id"))


def _write_row(conn: sqlite3.Connection, phase: dict[str, Any] | None,
               table: str, key_col: str, d: dict[str, Any],
               cite: str) -> dict[str, Any]:
    """Write ONE register row. THE WRITE SITE — every refusal lives here.

    REFUSES, in order:
      1. an empty key
      2. a key that is ALREADY registered (never a silent overwrite)
      3. a parent that does not resolve (a phantom FK)
    """
    key = str(d.get("key") or "").strip()
    if not key:
        return {"ok": False, "code": "EMPTY_KEY"}
    # ---- THE EXISTENCE CHECK MUST USE THE VALUE THE ROW WOULD CARRY ------
    #
    # MEASURED (2026-09-25): `derived_column`'s `key_col` is `column_name`, but
    # the KEY is the composite `table.column`. The check below looked up
    # `column_name='chat_main.sha256'`, found nothing, and the INSERT then hit
    # `UNIQUE constraint failed: table_name, column_name` — because the row WAS
    # there under `column_name='sha256'`. A duplicate check that looks up the
    # wrong value is not a duplicate check.
    derived = _derive_extra_from_source(key, (phase or {}).get("scope", ""), conn)
    key_value = derived.get(key_col, key)
    exists = conn.execute(
        "SELECT 1 FROM %s WHERE %s=?" % (table, key_col), (key_value,)).fetchone()
    if exists:
        return {"ok": False, "code": "ALREADY_REGISTERED"}

    cols = [key_col, "name", "description"]
    vals: list[Any] = [key, str(d.get("name") or key),
                       str(d.get("description") or "")]
    # ---- A REGISTER THAT HAS NO `name` / `description` COLUMN -------------
    #
    # MEASURED (2026-09-25, the first real `chat` apply): all 40 keys were
    # refused `WRITE_FAILED` with
    #     "table chat_registry has no column named name"
    #
    # `_write_row` assumed EVERY register carries `name` + `description`. That is
    # true of the 5 original phase targets and FALSE of `chat_registry`, whose
    # columns are `chat_key, ticket_id, chat_id, service, status, opened_by`.
    #
    # The fix is to write the columns the TABLE ACTUALLY HAS, read from PRAGMA,
    # rather than a fixed list. A register that has no place for a description
    # simply does not store one — inventing a column would be a schema change
    # this plan does not own, and silently dropping the key would lose the row.
    have = {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
    # ---- A REQUIRED COLUMN THE SOURCE CANNOT SUPPLY ----------------------
    #
    # MEASURED (2026-09-25): `purpose_route_registry` and
    # `derived_column_registry` each have NOT NULL columns that a route path or a
    # bare column name CANNOT supply. The write failed with an opaque
    # `WRITE_FAILED` / `IntegrityError`, which is a CRASH, not a finding.
    #
    # A phase DECLARES those columns in `required_extra`, and the refusal becomes
    # `SOURCE_MISSING_COLUMN` NAMING them. The distinction matters: a named
    # refusal says WHAT is missing and therefore what to do; a constraint error
    # says only that something went wrong.
    #
    # THE COLUMNS EXIST IN THE TABLE — that is not the test. The test is that the
    # SOURCE supplies no VALUE for them, and this module has no way to invent one.
    # A value it cannot derive is a value it must not guess.
    missing = [c for c in (phase or {}).get("required_extra", ())]
    # ---- THE SOURCE'S OWN COLUMNS ARE ALWAYS WRITTEN ---------------------
    #
    # MEASURED (2026-09-25): `_derive_extra_from_source` returned the right values
    # (`table_name`, `kind`, `derived_from`) but they were only written INSIDE the
    # `if missing:` branch. `derived_column` declares NO `required_extra` (every
    # column IS derivable), so the branch was skipped and the row was written
    # WITHOUT them — `NOT NULL constraint failed: derived_column_registry.table_name`.
    #
    # The derivation is therefore done UNCONDITIONALLY, and the declared-missing
    # check is a SEPARATE question: "is there a column the source cannot supply?"
    derived = _derive_extra_from_source(key, (phase or {}).get("scope", ""), conn)
    still_missing = [c for c in missing if c not in derived]
    if still_missing:
        return {"ok": False, "code": "SOURCE_MISSING_COLUMN",
                "detail": "the register requires %s, which the source cannot "
                          "supply" % ", ".join(still_missing)}
    cols = [key_col]
    vals: list[Any] = [key]
    # ---- THE KEY COLUMN MAY HOLD A DIFFERENT VALUE THAN THE KEY ----------
    #
    # MEASURED (2026-09-25): `derived_column`'s `key_col` IS `column_name`, but
    # the discovered KEY is the composite `table.column` (needed for dedup and for
    # the log). Writing the key straight into `column_name` produced
    # `chat_main.chat_hash` where the register's own rows say `chat_hash`.
    #
    # So when the source supplies a value for the KEY COLUMN itself, that value
    # wins: the key is the IDENTITY, the column is the FACT. The log still records
    # the composite key, so the two are not conflated.
    if key_col in derived:
        vals[0] = derived[key_col]
    # ---- THE SOURCE'S OWN COLUMNS ARE WRITTEN HERE, AFTER THE RESET ------
    #
    # MEASURED (2026-09-25): the derivation ran BEFORE `cols`/`vals` were reset
    # below, so the values were appended and then DISCARDED — the row was written
    # without them and hit `NOT NULL constraint failed: ...table_name`. The order
    # is the fix: derive first (to answer the declared-missing question), then
    # reset, then append.
    for c, v in derived.items():
        if c in have and c != key_col:
            cols.append(c)
            vals.append(v)
    # ---- `is_active = 0` IS WRITTEN EXPLICITLY (decision 甲) --------------
    #
    # MEASURED (2026-09-25, the first real `study`/`worker` apply): every new row
    # came out `is_active=1`. The cause is the DDL default —
    #     `is_active INTEGER NOT NULL DEFAULT 1`
    # — and `_write_row` did not write the column at all, so the DEFAULT decided.
    #
    # `activation_gate`'s own docstring records this exact defect:
    #     "`is_active` is declared `INTEGER NOT NULL DEFAULT 1` in 54 places in
    #      db_schema.py. So every row that was ever inserted is ACTIVE, and the
    #      `1` is a DEFAULT, not a MEASUREMENT."
    #
    # The human's decision (甲): the 7B FILLS, a human ACTIVATES. So the writer
    # states `0` explicitly. A row that is active because nobody said otherwise
    # is a row nobody proved.
    if "is_active" in have:
        cols.append("is_active")
        vals.append(0)
    if "name" in have:
        cols.append("name")
        vals.append(str(d.get("name") or key))
    if "description" in have:
        cols.append("description")
        vals.append(str(d.get("description") or ""))
    # ---- A NOT NULL COLUMN THE SOURCE *CAN* SUPPLY -----------------------
    #
    # MEASURED (2026-09-25, the first real `study`/`worker` apply): both were
    # refused `WRITE_FAILED` for a NOT NULL column that the source DOES supply —
    #     study_registry.skill_id   (the source IS `skill_registry`)
    #     worker_registry.cite_ref  (the citation is computed by `cite_for`)
    #
    # A phase DECLARES those in `derive_extra`, and the value is taken from the
    # SOURCE ROW rather than invented. `cite_ref` is special-cased because it is
    # the citation this module already computed and verified — writing it is the
    # whole point of the gate.
    for col, src_col in ((phase or {}).get("derive_extra") or {}).items():
        if col not in have:
            continue
        if col == "cite_ref":
            cols.append(col)
            vals.append(str(cite or ""))
            continue
        # Read the value from the SOURCE table, keyed by the same key.
        src_tbl = (phase or {}).get("source_table")
        if not src_tbl:
            continue
        try:
            row = conn.execute(
                "SELECT %s FROM %s WHERE %s=?" % (src_col, src_tbl,
                                                  (phase or {}).get("source_key_col") or src_col),
                (key,)).fetchone()
        except sqlite3.Error:
            row = None
        if row is not None and row[0] is not None:
            cols.append(col)
            vals.append(row[0])
    # The parent FK, resolved from a REAL row. A guessed id would create a
    # capability under a module that does not exist.
    if phase and phase.get("parent"):
        fk_col, ptable, pkey_col = phase["parent"]
        # MEASURED DEFECT (this session, the first real `function` run): the
        # parent was taken as `key.split(".")[0]` — the FILE STEM — and
        # `capability_registry` has no capability named after a file, so all 39
        # keys were refused `PARENT_NOT_FOUND`. A function's parent is a
        # CAPABILITY, and a capability is not derivable from a function name.
        #
        # So the parent is resolved by trying, in order:
        #   1. the key's own first segment (correct for `module.sub` shapes)
        #   2. the phase's declared DEFAULT parent, when one is given
        #   3. REFUSE — never a guessed id
        want = key.split(".")[0] if "." in key else key
        prow = conn.execute(
            "SELECT %s FROM %s WHERE %s=?" % (
                _pk_col_for(ptable), ptable, pkey_col), (want,)).fetchone()
        if not prow and phase.get("default_parent"):
            prow = conn.execute(
                "SELECT %s FROM %s WHERE %s=?" % (
                    _pk_col_for(ptable), ptable, pkey_col),
                (phase["default_parent"],)).fetchone()
        if not prow:
            # A parent that cannot be resolved is REFUSED, not defaulted: a
            # defaulted FK is a phantom reference that verifies and lies.
            return {"ok": False, "code": "PARENT_NOT_FOUND",
                    "detail": "%s=%r not in %s" % (pkey_col, want, ptable)}
        cols.append(fk_col)
        vals.append(int(prow[0]))
    try:
        cur = conn.execute(
            "INSERT INTO %s (%s) VALUES (%s)"
            % (table, ", ".join(cols), ", ".join("?" * len(cols))), vals)
        conn.commit()
        return {"ok": True, "row_id": int(cur.lastrowid)}
    except Exception as exc:
        return {"ok": False, "code": "WRITE_FAILED",
                "detail": "%s: %s" % (type(exc).__name__, exc)}


def fill_until_done(conn: sqlite3.Connection, phase_key: str, *,
                    apply: bool = True, base: Path | None = None,
                    use_llm: bool = True, max_rounds: int = 500,
                    on_round: Any = None) -> dict[str, Any]:
    """Run ONE phase repeatedly until it stops ADVANCING. The non-stop driver.

    WHY THIS EXISTS — MEASURED (2026-09-25)
    ---------------------------------------
    The human: *"continue *_registry for 7B, as can be measured, non stop until
    100% and report all the fail"*. A single `--run` processes at most `cap` keys,
    so a phase with 1297 keys needs 33 rounds. Driving that from the shell means
    the loop's stop condition lives OUTSIDE the module that knows what "done"
    means, and a shell loop that stops on the wrong string silently under-fills.

    THE STOP CONDITION IS "NO PROGRESS", NOT "NO KEYS RETURNED".
    A round can return keys and accept NONE (every one refused). Stopping on
    `returned == 0` alone would spin forever on a phase whose remaining keys are
    all refused; stopping on `accepted == 0` alone would stop a phase that is
    still recording NEW refusals (which are findings, not progress). So the loop
    stops when a round changes NEITHER the accepted count NOR the set of keys
    with a terminal outcome — i.e. when the round added no new information.

    Returns the per-round trace, so the caller can REPORT every failure.
    """
    rounds: list[dict[str, Any]] = []
    for i in range(1, max_rounds + 1):
        before = _terminal_keys(conn, phase_key)
        out = run_phase(conn, phase_key, apply=apply, base=base, use_llm=use_llm)
        if not out.get("ok"):
            return {"ok": False, "phase_key": phase_key, "rounds": rounds,
                    "error": out.get("error"), "error_code": out.get("error_code")}
        after = _terminal_keys(conn, phase_key)
        new_keys = len(after - before)
        rec = {"round": i, "returned": out["returned"],
               "accepted": out["accepted"], "refused": out["refused"],
               "new_terminal_keys": new_keys,
               "by_refusal": _round_refusals(out)}
        rounds.append(rec)
        if on_round:
            on_round(rec)
        # NO PROGRESS: the round added no new terminal key. Every remaining key
        # has already been decided, so another round would repeat itself.
        if new_keys == 0:
            break
    return {"ok": True, "phase_key": phase_key, "rounds": rounds,
            "rounds_run": len(rounds),
            "accepted_total": sum(r["accepted"] for r in rounds),
            "refused_total": sum(r["refused"] for r in rounds),
            "stopped_because": ("no new terminal key in the last round"
                                if rounds and rounds[-1]["new_terminal_keys"] == 0
                                else "max_rounds reached")}


def _terminal_keys(conn: sqlite3.Connection, phase_key: str) -> set[str]:
    """The keys with a TERMINAL outcome (accepted/refused) in the log."""
    return {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT key_text FROM register_fill_run "
        " WHERE phase_key=? AND outcome IN ('accepted','refused')",
        (phase_key,))}


def _round_refusals(out: dict[str, Any]) -> dict[str, int]:
    """The refusal codes in ONE round's results, so a failure is NAMED."""
    codes: dict[str, int] = {}
    for r in out.get("results", []):
        c = r.get("refusal_code") or ""
        if c and c != "DRY_RUN":
            codes[c] = codes.get(c, 0) + 1
    return codes


def phase_progress(conn: sqlite3.Connection, phase_key: str) -> dict[str, Any]:
    """How far a phase has got. `done` counts ONLY terminal APPLIED outcomes.

    MEASURED DEFECT (in `terminology_sweep`): counting DISTINCT key over EVERY
    row made a DRY RUN raise `done` — a dry run records `skipped` and registers
    NOTHING, so the page showed progress that had not happened.
    """
    ensure_schema(conn)
    row = conn.execute(
        "SELECT phase_key, scope, cap, target_table, key_col FROM "
        "phase_registry WHERE phase_kind=? AND phase_key=? AND is_active=1",
        (PHASE_KIND, phase_key)).fetchone()
    if not row:
        return {"ok": False, "error_code": "UNKNOWN_PHASE",
                "error": "no active phase %r" % phase_key}
    done = conn.execute(
        "SELECT COUNT(DISTINCT key_text) FROM register_fill_run "
        " WHERE phase_key=? AND outcome IN ('accepted','refused')",
        (phase_key,)).fetchone()[0]
    dry = conn.execute(
        "SELECT COUNT(DISTINCT key_text) FROM register_fill_run "
        " WHERE phase_key=? AND outcome NOT IN ('accepted','refused')",
        (phase_key,)).fetchone()[0]
    # ---- A REFUSED SCOPE IS REPORTED, NOT CRASHED ------------------------
    #
    # MEASURED DEFECT (2026-09-25, introduced by the KIND gate): `discover()`
    # now RAISES `KindMismatch` for a scope whose SOURCE KIND cannot supply its
    # TARGET register KIND. `phase_progress` called it unguarded, so
    # `register_fill.py --phases` CRASHED with a traceback instead of reporting.
    #
    # A refusal is a FINDING, and a report that dies on a finding is not a
    # report. The refusal is caught and carried as DATA, so the page shows the
    # scope as REFUSED with its reason — which is exactly what the human asked
    # for: "explain easy to explain to worker why we reject and tutorial them".
    try:
        keys = discover(row["scope"])
    except KindMismatch as e:
        return {"ok": True, "phase_key": phase_key, "cap": int(row["cap"]),
                "scope_total": 0, "done": done, "dry_only": dry,
                "in_table": 0, "remaining": 0, "truncated": False,
                "refused": True, "refusal_code": "KIND_MISMATCH",
                "source_kind": e.source_kind, "target_kind": e.target_kind,
                "why": e.why, "evidence": e.evidence, "tutorial": e.tutorial,
                "refusal": e.explain()}
    total = len(keys)
    # ---- `remaining` MUST COUNT WHAT IS NOT YET IN THE TABLE -------------
    #
    # MEASURED (2026-09-25, after the fill converged): `remaining` was
    # `total - done`, where `done` counts LOG outcomes. A key that was ALREADY in
    # the target table BEFORE the phase ever ran has NO log row, so it was
    # reported as "remaining" forever — `module` showed remaining=1 for
    # `mouse_spot_helper`, which is IN `module_registry`. The page then showed
    # work that did not exist.
    #
    # The honest question is "which discovered keys are NOT in the target table",
    # so the count is derived from the TABLE, not from the log. A key that is
    # registered is DONE whether or not this module wrote it.
    table, key_col = row["target_table"], row["key_col"]
    try:
        have = {str(r[0]) for r in conn.execute(
            "SELECT %s FROM %s" % (key_col, table))}
    except sqlite3.Error:
        have = set()
    # ---- THE KEY COLUMN MAY HOLD A DIFFERENT VALUE THAN THE KEY ----------
    #
    # MEASURED (2026-09-25): `derived_column`'s `key_col` is `column_name`, but
    # the KEY is the composite `table.column`. Looking the composite key up in
    # `column_name` missed the real rows, so `remaining` reported 2 for a phase
    # whose rows were BOTH present. The same value the WRITE would store is the
    # value the CHECK must look up.
    missing = []
    for k in keys:
        derived = _derive_extra_from_source(str(k), row["scope"], conn)
        if str(derived.get(key_col, k)) not in have:
            missing.append(k)
    return {"ok": True, "phase_key": phase_key, "cap": int(row["cap"]),
            "scope_total": total, "done": done, "dry_only": dry,
            "in_table": len(keys) - len(missing),
            "remaining": len(missing),
            "truncated": total > int(row["cap"])}


def classify_registry(conn: sqlite3.Connection, table: str,
                      base: Path | None = None) -> dict[str, Any]:
    """DERIVE a register's CLASS from a MEASUREMENT, never from an assertion.

    WHY THIS EXISTS — MY OWN DEFECT (2026-09-25)
    --------------------------------------------
    `plan_ALL.REGISTERS.NOT.SOME` declared `test_case_registry` as CLASS C with
    "NO PRODUCT WRITER". MEASURED: it HAS one —
    `src/task_center/ontology_store.py:seed_validate_new_task_test_cases` does
    `INSERT INTO test_case_registry`. My CLASS was a JUDGEMENT, and it was wrong.

    That is the SAME defect class as the 19 `UNDECLARED` registers: a hand-written
    table asserting a fact the DB can measure. The first time the table caught
    itself; this time it did not, because CLASS was free text with no check.

    THE MEASUREMENT
    ---------------
      B  DERIVED — 1 row per entity (rows == distinct entity pairs). Checked
         FIRST, because a derived register also has a writer, and "derived" is
         the more specific fact.
      A  POPULATED — a PRODUCT writer exists AND the table has rows. A writer
         with ZERO rows is NOT populated: MEASURED, `chat_registry` has a writer
         (`chat_registry_store.py`) and 0 rows, which IS a real gap.
      C  REAL GAP — no writer, or a writer with no rows.

    A `_`-prefixed file is a ONE-SHOT session script (`SESSION_SCRIPT_PREFIX`), so
    it does NOT count as a product writer. MEASURED: `test_case_registry`'s rows
    came from `seed_test_case_registry.py`, which is a real CLI, and the INSERT
    lives in `ontology_store.py` — a product module.
    """
    b = base or BASE
    try:
        n = int(conn.execute("SELECT count(*) FROM %s" % table).fetchone()[0])
    except sqlite3.Error:
        n = -1
    # ---- 1. DERIVED? (checked first: a derived register also has a writer) --
    # MEASURED 2026-09-27: this tested `rows == distinct(entity_type,
    # entity_ref_id)` ONLY. `version_registry` is keyed by the TRIPLE
    # `(entity_type, entity_ref_id, version)` -- 1225 rows == 1225 distinct
    # triples, but only 1224 distinct PAIRS, because entity G/39 has versions 1
    # and 2. A VERSION register legitimately holds MORE THAN ONE row per entity:
    # that is what a version register IS. The pair-only test therefore called it
    # CLASS A and the proof reported a contradiction that did not exist.
    #
    # THE FIX: the identity is the table's OWN key. If the table carries a
    # `version` column, the entity identity includes it. This is still a real
    # test -- a table with MORE rows than entities x versions is NOT derived.
    if n > 0:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if {"entity_type", "entity_ref_id"} <= cols:
            key_cols = ["entity_type", "entity_ref_id"]
            if "version" in cols:
                key_cols.append("version")
            d = int(conn.execute(
                "SELECT count(*) FROM (SELECT DISTINCT %s FROM %s)"
                % (", ".join(key_cols), table)).fetchone()[0])
            if d == n:
                return {"class": "B", "measured": True, "writers": [],
                        "rows": n,
                        "why": "1 row per entity: %d rows == %d distinct (%s)"
                               % (n, d, ", ".join(key_cols))}
    # ---- 2. a PRODUCT writer? -------------------------------------------
    writers: list[str] = []
    pat = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+%s\b" % re.escape(table),
                     re.IGNORECASE)
    for p in sorted(b.rglob("*.py")):
        if p.name.startswith(SESSION_SCRIPT_PREFIX):
            continue
        if ".venv" in p.parts or "node_modules" in p.parts:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pat.search(txt):
            try:
                rel = p.relative_to(b).as_posix()
            except ValueError:
                rel = p.name
            writers.append(rel)
    if writers and n > 0:
        return {"class": "A", "measured": True, "writers": writers[:5],
                "rows": n,
                "why": "a PRODUCT writer exists (%s) and the table holds %d rows"
                       % (", ".join(writers[:3]), n)}
    # ---- 3. a real gap ---------------------------------------------------
    if writers:
        return {"class": "C", "measured": True, "writers": writers[:5],
                "rows": n,
                "why": "a writer exists (%s) but the table holds %d rows — a "
                       "writer with no rows is NOT populated"
                       % (", ".join(writers[:3]), n)}
    return {"class": "C", "measured": True, "writers": [], "rows": n,
            "why": "no PRODUCT writer found by scanning non-`_` files for "
                   "`INSERT INTO %s`" % table}


def coverage_report(conn: sqlite3.Connection) -> dict[str, Any]:
    """EVERY `*_registry` table, with its class, rows, writer and gap.

    THE HUMAN (2026-09-25): "have the plan!! all *_registry not some_registry".

    WHY THIS EXISTS
    ---------------
    This module reported "all registers 100%" while covering 11 of 26
    `*_registry` tables. A report that measures its OWN SCOPE and calls it the
    world is a claim about the reporter, not about the registers.

    THE TABLE IS THE SOURCE OF TRUTH FOR THE LIST, NOT `COVERAGE`
    ------------------------------------------------------------
    `COVERAGE` declares the CLASS and the WRITER, which are judgements. The LIST
    of registers is read from `sqlite_master`, so a register that exists but is
    NOT declared in `COVERAGE` is REPORTED as `UNDECLARED` rather than silently
    omitted. A coverage report that can omit a register is the defect it exists
    to remove.
    """
    live = [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND (name LIKE '%register%' OR name LIKE '%registry%') "
        "AND name NOT LIKE 'register_fill%' "
        "AND name NOT LIKE 'register_approve' ORDER BY name")]
    declared = {str(c["table"]): c for c in COVERAGE}
    rows: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    for t in live:
        c = declared.get(t)
        try:
            n = int(conn.execute("SELECT count(*) FROM %s" % t).fetchone()[0])
        except sqlite3.Error:
            n = -1
        if c is None:
            rows.append({"table": t, "rows": n, "class": "UNDECLARED",
                         "writer": None, "source": None,
                         "gap": "NOT DECLARED in register_fill.COVERAGE — a "
                                "register the coverage table forgot"})
            continue
        # ---- THE CLASS GATE: the DECLARED class is CHECKED, never trusted --
        #
        # MEASURED DEFECT (2026-09-25): `test_case_registry` was declared CLASS C
        # ("NO PRODUCT WRITER") and it HAS one. A hand-written class is a
        # JUDGEMENT; the writer scan is a MEASUREMENT. When they disagree, the
        # MEASUREMENT wins and the contradiction is REPORTED by name — the same
        # design as the KIND gate, which reads declared kinds and refuses a
        # declared mismatch rather than trusting the declaration.
        measured = classify_registry(conn, t)
        declared_class = str(c["class"])
        if declared_class != measured["class"]:
            contradictions.append({
                "table": t, "declared": declared_class,
                "measured": measured["class"], "why": measured["why"]})
        rows.append({"table": t, "rows": n, "class": measured["class"],
                     "declared_class": declared_class,
                     "class_agrees": declared_class == measured["class"],
                     "writer": c.get("writer"), "source": c.get("source"),
                     "gap": c.get("gap", ""),
                     "discovery": c.get("discovery", ""),
                     "class_why": measured["why"]})
    # a DECLARED register that is NOT live is also a finding
    for t, c in declared.items():
        if t not in live:
            rows.append({"table": t, "rows": -1, "class": c["class"],
                         "writer": c.get("writer"), "source": c.get("source"),
                         "gap": "DECLARED but NOT a live table"})
    by_class: dict[str, int] = {}
    for r in rows:
        by_class[r["class"]] = by_class.get(r["class"], 0) + 1
    return {"ok": True, "total": len(rows), "by_class": by_class,
            "registers": rows,
            "class_contradictions": contradictions,
            "note": ("class A = populated by its own module (NOT a gap); "
                     "B = derived, 1 row per entity; C = real gap; "
                     "UNDECLARED = the coverage table forgot it. The CLASS is "
                     "MEASURED by `classify_registry()`, and a declared class "
                     "that contradicts the measurement is reported in "
                     "`class_contradictions`.")}


def report(conn: sqlite3.Connection, phase_key: str) -> dict[str, Any]:
    """The recorded history for one phase, from the APPEND-ONLY log."""
    ensure_schema(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT key_text, outcome, refusal_code, row_id, llm_ms, cite_supplied, "
        "       cite_ref, observed_at FROM register_fill_run WHERE phase_key=? "
        " ORDER BY observed_at DESC, run_id DESC", (phase_key,))]
    by_outcome: dict[str, int] = {}
    by_code: dict[str, int] = {}
    lat: list[int] = []
    supplied = 0
    for r in rows:
        by_outcome[r["outcome"]] = by_outcome.get(r["outcome"], 0) + 1
        if r["refusal_code"]:
            by_code[r["refusal_code"]] = by_code.get(r["refusal_code"], 0) + 1
        if r["llm_ms"] is not None:
            lat.append(int(r["llm_ms"]))
        supplied += int(r["cite_supplied"] or 0)
    lat.sort()
    distinct = conn.execute(
        "SELECT COUNT(DISTINCT key_text) FROM register_fill_run "
        " WHERE phase_key=? AND cite_supplied=1 AND "
        "       outcome IN ('accepted','refused')", (phase_key,)).fetchone()[0]
    # THE CONTROL, read back from the table. A caller must be able to see whether
    # the phase's "filled" means anything WITHOUT re-running the phase — that is
    # the whole reason the verdict is persisted.
    ctl = conn.execute(
        "SELECT ran, passed, refused, invented, reason, model, observed_at "
        "  FROM register_fill_control WHERE phase_key=? "
        " ORDER BY observed_at DESC, control_id DESC LIMIT 1",
        (phase_key,)).fetchone()
    control = ({"ran": bool(ctl["ran"]), "pass": bool(ctl["passed"]),
                "refused": bool(ctl["refused"]), "invented": ctl["invented"],
                "reason": ctl["reason"], "model": ctl["model"],
                "observed_at": ctl["observed_at"]} if ctl
               else {"ran": False, "pass": None,
                     "note": ("NO CONTROL ROW — the phase has never run with the "
                              "7B, so 'filled' cannot be told from 'always says "
                              "yes'. Do NOT report a verdict for this phase.")})
    return {"ok": True, "phase_key": phase_key, "rows": len(rows),
            "by_outcome": by_outcome, "by_refusal_code": by_code,
            "supplied_citations": supplied, "names_supplied": distinct,
            "control": control,
            "llm_ms_p50": lat[len(lat) // 2] if lat else None,
            "llm_ms_max": max(lat) if lat else None,
            "items": rows[:200]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fill the thin *_registry tables")
    ap.add_argument("--discover", metavar="SCOPE")
    ap.add_argument("--run", metavar="PHASE")
    ap.add_argument("--report", metavar="PHASE")
    ap.add_argument("--fill-all", action="store_true",
                    help="run EVERY phase until it stops advancing, then report")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--phases", action="store_true")
    ap.add_argument("--coverage", action="store_true",
                    help="EVERY *_registry table with its class, rows, writer "
                         "and gap — not only the ones this module fills")
    args = ap.parse_args(argv)

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    try:
        if args.coverage:
            cov = coverage_report(conn)
            print("ALL *_registry tables: %d  (%s)"
                  % (cov["total"], cov["by_class"]))
            contra = cov.get("class_contradictions") or []
            if contra:
                print()
                print("CLASS CONTRADICTIONS (declared vs MEASURED): %d"
                      % len(contra))
                for x in contra:
                    print("  %-28s declared=%s measured=%s"
                          % (x["table"], x["declared"], x["measured"]))
                    print("      %s" % x["why"])
            print()
            print("  %-30s %6s %-4s %-34s %s"
                  % ("register", "rows", "cls", "writer", "gap / discovery"))
            print("  " + "-" * 118)
            for r in cov["registers"]:
                note = r["gap"] or r.get("discovery") or ""
                print("  %-30s %6d %-4s %-34s %s"
                      % (r["table"], r["rows"], r["class"],
                         str(r["writer"] or "-")[:34], note[:60]))
                if r.get("discovery") and r["gap"]:
                    print("  %-30s %6s %-4s %-34s %s"
                          % ("", "", "", "", r["discovery"][:60]))
            return 0
        if args.phases:
            seed_phases(conn)
            for p in list_phases(conn):
                prog = phase_progress(conn, p["phase_key"])
                if prog.get("refused"):
                    # A REFUSED scope is REPORTED with its reason, not hidden
                    # behind a `total=0`. MEASURED (2026-09-25): the KIND gate
                    # refuses `chat`/`purpose_route`, and a report that showed
                    # only `total=0` would make a REFUSAL look like an EMPTY
                    # scope — the two are different findings.
                    print("  %-10s REFUSED  %s -> %s"
                          % (p["phase_key"], prog["source_kind"],
                             prog["target_kind"]))
                    print("             why      : %s" % prog["why"])
                    print("             evidence : %s" % prog["evidence"])
                    print("             tutorial : %s" % prog["tutorial"])
                    continue
                print("  %-10s cap=%-4d total=%-6d done=%-4d remaining=%-6d "
                      "truncated=%s" % (p["phase_key"], p["cap"],
                                        prog["scope_total"], prog["done"],
                                        prog["remaining"], prog["truncated"]))
            return 0
        if args.discover:
            names = discover(args.discover)
            print("scope %s: %d key(s)" % (args.discover, len(names)))
            for n in names[:20]:
                print("   ", n)
            return 0
        if args.report:
            out = report(conn, args.report)
            print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
            return 0
        if args.fill_all:
            seed_phases(conn)
            summary = []
            for p in list_phases(conn):
                pk = p["phase_key"]

                def _on(rec: dict[str, Any], pk: str = pk) -> None:
                    print("  %-14s round %-3d returned=%-3d accepted=%-3d "
                          "refused=%-3d new=%-3d %s"
                          % (pk, rec["round"], rec["returned"], rec["accepted"],
                             rec["refused"], rec["new_terminal_keys"],
                             rec["by_refusal"] or ""))

                res = fill_until_done(conn, pk, apply=args.apply,
                                      use_llm=not args.no_llm, on_round=_on)
                prog = phase_progress(conn, pk)
                summary.append({"phase_key": pk, "ok": res.get("ok"),
                                "rounds": res.get("rounds_run"),
                                "accepted": res.get("accepted_total"),
                                "refused": res.get("refused_total"),
                                "scope_total": prog.get("scope_total"),
                                "done": prog.get("done"),
                                "remaining": prog.get("remaining"),
                                "stopped_because": res.get("stopped_because")})
                print("  %-14s DONE rounds=%s accepted=%s refused=%s "
                      "done=%s/%s remaining=%s"
                      % (pk, res.get("rounds_run"), res.get("accepted_total"),
                         res.get("refused_total"), prog.get("done"),
                         prog.get("scope_total"), prog.get("remaining")))
            print()
            print("== FILL-ALL SUMMARY ==")
            for s in summary:
                print("  %-14s done=%-5s/%-6s remaining=%-6s accepted=%-5s "
                      "refused=%-5s rounds=%s"
                      % (s["phase_key"], s["done"], s["scope_total"],
                         s["remaining"], s["accepted"], s["refused"],
                         s["rounds"]))
            return 0
        if args.run:
            seed_phases(conn)
            out = run_phase(conn, args.run, apply=args.apply,
                            use_llm=not args.no_llm)
            if not out.get("ok"):
                print("FAILED: %s" % out.get("error"))
                return 1
            print("phase %s scope=%s cap=%d" % (out["phase_key"], out["scope"],
                                                out["cap"]))
            print("  total=%d returned=%d truncated=%s applied=%s"
                  % (out["total"], out["returned"], out["truncated"],
                     out["applied"]))
            print("  accepted=%d refused=%d supplied_citations=%d"
                  % (out["accepted"], out["refused"],
                     out["supplied_citations"]))
            print("  model=%s source=%s" % (out["model"], out["model_source"]))
            print("  7B: calls=%d p50=%sms max=%sms"
                  % (out["llm_calls"], out["llm_ms_p50"], out["llm_ms_max"]))
            print("  recorded=%d dropped=%d" % (out["recorded"], out["dropped"]))
            print("  control: %s" % out["control"])
            for r in out["results"][:25]:
                print("    %-46s %-9s %s" % (r["key"][:46], r["outcome"],
                                             r.get("refusal_code") or ""))
            return 0
        ap.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
