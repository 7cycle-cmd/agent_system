# Plan: Skill Lifecycle Trace Recorder (A+B+C)

Status: implemented (Phase D deferred)

## Scope locked

| Phase | What | Status |
|-------|------|--------|
| A | Skill Prompt SSOT nav tabs: Editor / Lifecycle / Prompt Trace / Proof | Done |
| B | `skill_lifecycle_trace_recorder` + draft pipeline skills in SSOT | Done |
| C | `task_lifecycle_log` + `task_prompt_trace` + Python APIs + readonly timeline UI | Done |
| D | Full prompt assembler skill + auto chain Seq1–12 | Deferred |

## Ontology

- `event_type` = lifecycle verb only (not module/capability).
- Each row carries redundant `module` + `capability`.
- `linked_trace_id` optional on `requirement_received`; preferred on later events.
- Append-only: never update/delete lifecycle rows; FAIL = new row + state rollback.
- State machine: `draft → researching → proposal_draft → validating → validated → finalized`.

## Write path

- **Primary:** Python API `append_lifecycle_event` / `insert_prompt_trace`.
- **HTTP:** `POST /api/tasks/lifecycle/append`, `POST /api/tasks/prompt-trace`.
- **Read:** `GET /api/tasks/<id>/lifecycle`, `.../prompt-traces`, `.../timeline`.
- **Seed:** `POST /api/skills/seed-lifecycle` or `python -m src.task_center.lifecycle_log seed`.
- Skill SSOT documents/validates; does not own the write path.

## Dimension tags (locked)

| Skill | Tags |
|-------|------|
| skill_lifecycle_trace_recorder | lifecycle, audit, gate |
| skill_requirement_research | research |
| skill_proposal_design | proposal |
| skill_api_standard_validator | validation, api-gate |
| skill_proposal_validate | validation, proposal-gate |
| skill_worker_code_builder | build, ssot, tdd |
| skill_task_format_worker_taxonomy_validator | validation, taxonomy-gate |
| mouse_spot_verify | visual-gate, openclaw |

Post-QC work order (formal v0.2): see `docs/plan_skill_worker_code_builder.md`.

## Key files

- `src/task_center/lifecycle_log.py` — DDL, validate, append, timeline, seed (T-SKILL02).
- `mouse_spot_helper.py` — HTTP routes.
- `skills/skill_lifecycle_trace_recorder/v1_strict.json` + draft skills.
- `llm_task_monitor_ui/src/app.js` — SKILL_SSOT_TABS + timeline UI.
- `db_schema.ensure_task_center_schema` → `ensure_lifecycle_tables`.
- Capability map: `task_center.append_lifecycle_event`.

## Smoke checklist

1. Skill SSOT page shows Editor / Lifecycle / Prompt Trace / Proof (not Task Center tabs).
2. `POST /api/skills/seed-lifecycle` → ok, T-SKILL02, code_register row.
3. `POST` requirement_received without trace → 200.
4. Reuse same `event_sequence` → 400.
5. `POST` prompt-trace → `GET` timeline joins.
6. `GET /api/skills` lists lifecycle + draft skills.
