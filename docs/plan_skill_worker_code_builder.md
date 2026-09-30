# Plan: skill_worker_code_builder (v0.2 formal)

Status: **implemented as draft Skill SSOT** (work-order contract only)  
Formal contract: **v0.2_draft**  
Tags: `["build","ssot","tdd"]`  
v0.2.1 micro-opts: **deferred** (opt-in only)

## Core judgment (locked 2026-09-16)

v0.2 work order is **good enough** for the next hop:

```text
manual caller → managed_coding / task_ssot / code_register / field_tdd_rule APIs
```

- No architecture rewrite.
- Optional comfort fields are **not** required to run.
- Skill **never** writes DB, runs SQL, registers functions, or writes files.

## Why next hop is allowed

1. `work_order_status`: `ready` | `rejected` → caller branch.
2. Each `ssot_work_order` row has `entity_type` + `repo_target` → `task_ssot` / `code_register` / `field_tdd_rule`.
3. TDD cases include normal + boundary (null / empty / invalid / timeout).
4. Hard boundary matches MCS + A+B+C: Python APIs own persistence; skill emits orders.
5. `qc_report_ref` + `linked_trace_id` keep QC and lifecycle audit chain.

## Pipeline position

```text
requirement → research → proposal_design
  → api_standard_validator (check-only)
  → skill_proposal_validate → QC PASS + task_state=validated
  → [manual] skill_worker_code_builder → work order JSON
  → [manual] managed_coding apply (upsert_task_ssot, field_tdd_rule, register_managed_function)
  → [manual] TDD + lifecycle / prompt_trace events
  → taxonomy / finalize
  → Phase D auto-chain (out of scope)
```

## Hard gates (input)

Accept only when all hold:

- `qc_result = PASS`
- `task_state = validated`
- `qc_report_ref` present
- proposal body present; **do not invent** missing requirements

Else:

```json
"work_order_status": "rejected",
"reject_reason": "..."
```

## Hard boundary (authority)

| Allowed | Forbidden |
|---------|-----------|
| Emit `ssot_work_order` | Execute SQL / DDL |
| Emit `code_spec_artifacts` as **spec strings** | Write source files |
| Suggest `register_metadata` placeholders | Call `register_managed_function` |
| `schema_change_required` + short justification | Apply schema |
| Pure JSON output | Append lifecycle / prompt_trace itself |

Caller owns:

- `insert_prompt_trace` (snapshot work order)
- `append_lifecycle_event` (`skill_run`, then pass/fail after TDD)
- MCS apply APIs

## Entity → repo_target

| entity_type | repo_target | caller notes |
|-------------|-------------|--------------|
| channel | task_ssot | reuse `channel` ontology row first |
| module | task_ssot | reuse `module` ontology row first |
| capability | task_ssot | capability id dim |
| api | task_ssot + code_register | dual target |
| function | code_register | `register_id` empty until `register_managed_function` |
| db_table | task_ssot | DDL only if `schema_change_required` |
| field | task_ssot + field_tdd_rule | TDD → `field_tdd_rule.rule_json` |

## Output contract (v0.2)

See `skills/skill_worker_code_builder/v0_draft.json` `prompt_text` / schema:

- Top: `task_id`, `qc_report_ref`, `linked_trace_id`, `target_capability`, `work_order_status`, `reject_reason`
- `ssot_work_order[]`: ref_tag, entity_type, entity_key, definition, tdd_test_cases, dependencies, repo_target, schema_change_required, schema_justification
- `code_spec_artifacts[]`: spec_source_code, spec_test_code, register_metadata placeholders
- `assembly_explanation`, `summary`

## Optional backlog (v0.2.1 — not formal)

1. `dependencies: [{ref_tag, entity_key}]`
2. TDD `is_mandatory`
3. `risk_level: low|medium|high`
4. Cap `schema_justification` length
5. Top-level `work_order_version: "v0.2"`

If shipping fast: **keep v0.2**. If reducing human proofreading later: prefer **1 + 5** only.

## Files

| Path | Role |
|------|------|
| `skills/skill_worker_code_builder/v0_draft.json` | Draft skill SSOT |
| `src/task_center/lifecycle_log.py` | DIMENSION_TAGS + seed draft |
| `docs/plan_skill_worker_code_builder.md` | This plan |

## Non-goals

- Second SSOT store
- Auto-apply work order
- Replace managed_coding / MCS A–H
- Taxonomy final gate
- Phase D 24/7 chain
- UI “run builder” button (manual invoke is enough)

## Seed / verify

```text
POST /api/skills/seed-lifecycle
# or
python -m src.task_center.lifecycle_log seed

GET /api/skills  → includes skill_worker_code_builder (draft)
```
