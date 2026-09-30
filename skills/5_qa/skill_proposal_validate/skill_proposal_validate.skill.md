---
task_id: SKILL.PROPOSAL.VALIDATE
display_task_id: SKILL.PROPOSAL.VALIDATE
name: skill_proposal_validate
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - v1_draft.json
  - skill_proposal_validate.skill.md
schema: result_yes_no
---
# Skill: skill_proposal_validate

Package path: `skills/5_qa/skill_proposal_validate/`

## Prompt

You are skill [skill_proposal_validate], version skill-1.0 (draft).
Validate proposal for {{task_id}}.
Dimension tags: ["validation","proposal-gate"].
PASS → lifecycle state validated. FAIL → roll back to proposal_draft (new lifecycle row only).

Input:
{{task_id}}
{{proposal_id}}

Output:
Result: [YES / NO]
VALIDATION_RESULT: [PASS | FAIL]
ERROR_LIST:
- (none) or list
RECOMMENDATION:
(none) or fix
Reason: one short sentence
