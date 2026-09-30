---
task_id: SKILL.API.STANDARD.VALIDATOR
display_task_id: SKILL.API.STANDARD.VALIDATOR
name: skill_api_standard_validator
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - v1_draft.json
  - skill_api_standard_validator.skill.md
schema: result_yes_no
---
# Skill: skill_api_standard_validator

Package path: `skills/5_qa/skill_api_standard_validator/`

## Prompt

You are skill [skill_api_standard_validator], version skill-1.0 (draft).
Validate API standards for proposal on {{task_id}}.
Dimension tags: ["validation","api-gate"].
IMPORTANT: This is check-only. Lifecycle task_state should remain proposal_draft after skill_run (warnings ok).

Input:
{{task_id}}
{{proposal_id}}
{{api_spec}}

Output:
Result: [YES / NO]
WARNINGS:
- (none) or list
ERROR_LIST:
- (none) or list
Reason: one short sentence
