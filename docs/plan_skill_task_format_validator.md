# Plan: skill_task_format_worker_taxonomy_validator

## Purpose

Pre-dispatch gate. Before any new task is sent to a worker, validate the fixed 8-dimension format against project SSOT + `code_register`.

```
Task | Channel | Module | Capability | API | Function | Table | Field
```

- **PASS** → allow dispatch  
- **FAIL** → block, return `ERROR_LIST` + remediation  
- Does **not** execute business logic, mutate ontology, or auto-fix

Skill product version: **skill-1.0**  
Skill Prompt SSOT key: `skill_task_format_worker_taxonomy_validator`  
Parser: `result_yes_no` (PASS→YES, FAIL→NO)

## Artifacts

| Kind | Path / id |
|------|-----------|
| Prompt file | `skills/skill_task_format_worker_taxonomy_validator/v1_strict.json` |
| Validator | `src/task_center/skill_task_validate.py` → `validate_new_task` |
| Seed | `seed_task_format_validator()` |
| API | `POST /api/tasks/validate` (also GET query) |
| Seed API | `POST /api/skills/seed-task-format-validator` |
| Task Center | `T-SKILL01` under module `task_center` / channel `local_pc` / version `tc-skill-1.0` |
| code_register | `module_name=task_center`, `function_name=task_center.validate_new_task`, `file_path=src/task_center/skill_task_validate.py` |

## Workflow

1. Draft new task (8 dims)  
2. Call `validate_new_task` / `POST /api/tasks/validate` (and/or Skill Prompt SSOT LLM path)  
3. PASS → worker queue; FAIL → stop  
4. Skill stays in Skill Prompt SSOT + Task Center for reuse  

## Seed

```text
python -m src.task_center.skill_task_validate seed
# or
POST /api/skills/seed-task-format-validator
# also pulled by seed_default_skills / seed_phase5_all (best-effort)
```

## Example PASS payload (T-SKILL01)

```json
{
  "task": "T-SKILL01 — Register Task Format Validator Skill",
  "channel": "local_pc",
  "module": "task_center",
  "capability": "task_center.validate_new_task",
  "api": "POST /api/tasks/validate",
  "function": "task_center.validate_new_task",
  "table": "code_register",
  "field": "register_id, module_name, function_name, file_path"
}
```

## Laws kept

- improve→draft never activates  
- promote still pass_gate-gated for vision skills  
- mouse_spot root **10** unchanged  
- `code_register` writes only via `register_managed_function`
