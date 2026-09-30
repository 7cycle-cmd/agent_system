---
task_id: SKILL.REQUIREMENT.RESEARCH
display_task_id: SKILL.REQUIREMENT.RESEARCH
name: skill_requirement_research
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - v1_draft.json
  - skill_requirement_research.skill.md
schema: result_yes_no
---
# Skill: skill_requirement_research

Package path: `skills/4_agent/skill_requirement_research/`

## Prompt

You are skill [skill_requirement_research], version skill-1.0 (draft).
Analyze the raw requirement for {{task_id}}.
Produce a short research summary and gap list.
Dimension tags: ["research"].
After this skill runs, lifecycle state should become researching (via skill_lifecycle_trace_recorder).

Input:
{{task_id}}
{{requirement_text}}

Output:
Result: [YES / NO]
RESEARCH_SUMMARY: ...
GAP_LIST:
- ...
Reason: one short sentence
