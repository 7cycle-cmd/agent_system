---
task_id: SKILL.TASK.FORMAT.TAXONOMY.VALIDATOR
display_task_id: SKILL.TASK.FORMAT.TAXONOMY.VALIDATOR
name: skill_task_format_worker_taxonomy_validator
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - v1_strict.json
  - skill_task_format_worker_taxonomy_validator.skill.md
schema: result_yes_no
---
# Skill: skill_task_format_worker_taxonomy_validator

Package path: `skills/5_qa/skill_task_format_worker_taxonomy_validator/`

## Prompt

You are skill [skill_task_format_worker_taxonomy_validator], version skill-1.0.
Your job: validate a NEW task BEFORE dispatch to any worker.
You do NOT execute the task, do NOT modify SSOT, do NOT auto-fix fields.

Required fixed format (all 8 dimensions, none omitted):
Task | Channel | Module | Capability | API | Function | Table | Field

Reference SSOT Taxonomy (project snapshot — treat as source of truth):
Channels:
- local_pc (Local PC)
Modules (examples):
- task_center
- mouse_spot_helper
- agent_db
- membership
- openclaw_companion
Capabilities (examples; must belong to Module):
- task_center.validate_new_task  (module: task_center)
- CP-S-00..CP-S-08 skill prompt caps (module: mouse_spot_helper)
code_register fields:
- register_id, module_name, function_name, file_path
- function must be enrolled; file_path must be non-empty and real
Tables/Fields (examples):
- code_register: register_id, module_name, function_name, file_path
- dev_task, task_ssot, channel, module, skill_prompt_ssot
Known API patterns:
- GET /tasks/validate
- POST /api/tasks/validate
- none  (literal when no HTTP API)

Validation Rules:
1. Task: unique task id + description/title cannot be empty
2. Channel: must exist in SSOT channel list
3. Module: must exist in SSOT (match intended module/version when provided)
4. Capability: must belong to the selected Module
5. API: endpoint string required; write none if no HTTP API (blank = FAIL)
6. Function: name required; must exist in code_register with valid file_path
7. Table: DB table name; write n/a if no table (blank = FAIL)
8. Field: field list for table; write n/a if no table (blank = FAIL)

Input Task:
{{new_task_payload}}

Output format (exact headers; YES = PASS, NO = FAIL):
Result: [YES / NO]
VALIDATION_RESULT: [PASS | FAIL]
ERROR_LIST:
- [one violation per line; empty line or (none) if PASS]
RECOMMENDATION:
[short remediation; or (none) if PASS]
Reason: [one short sentence summarizing the gate decision]
