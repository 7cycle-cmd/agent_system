---
task_id: SKILL.PROPOSAL.DESIGN
display_task_id: SKILL.PROPOSAL.DESIGN
name: skill_proposal_design
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - v1_draft.json
  - skill_proposal_design.skill.md
schema: result_yes_no
---
# Skill: skill_proposal_design

Package path: `skills/4_agent/skill_proposal_design/`

## Prompt

You are skill [skill_proposal_design], version skill-1.0 (draft).
Design a proposal for {{task_id}} based on prior research.
Dimension tags: ["proposal"].
After run, lifecycle state should become proposal_draft.

Input:
{{task_id}}
{{research_summary}}

Output:
Result: [YES / NO]
PROPOSAL_ID: ...
PROPOSAL_SUMMARY: ...
Reason: one short sentence
