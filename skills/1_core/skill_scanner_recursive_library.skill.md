---
task_id: SKILL.SCANNER.RECURSIVE.LIBRARY
display_task_id: SKILL.SCANNER.RECURSIVE.LIBRARY
name: skill_scanner + watch_skills recursive Skill Library
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: Pending run test
reason: Skill Library package wrapper for scanner/render
artifacts:
  - skill_scanner.py
  - watch_skills.py
  - test_render.py
schema: Skill Library folder layout
---
# Skill: Recursive Skill Library scan/watch

- skill_scanner default recursive rglob `*.skill.md`
- watch_skills Observer recursive=True, 500ms debounce
- library buckets: 1_core / 2_db_schema / 3_ui / 4_agent / 5_qa
