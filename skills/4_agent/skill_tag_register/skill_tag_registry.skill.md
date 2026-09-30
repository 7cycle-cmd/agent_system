---
task_id: "SKILL.TAG.REGISTER"
name: "skill_tag_register"
catalog_id: 4
subcatalog_id: 0
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: "Register a capability tag with a cited definition"
reason: "Adding a tag used to require editing CAPABILITY_TAGS in Python. A tag is DATA, so it is registered as data."
artifacts: ["skill_tag_register.skill.md"]
schema: "capability_tag_registry (tag_key, definition, is_active)"
---
# Skill: skill_tag_register

Register a capability tag with a cited definition. This is the WRITE PATH for
`capability_tag_registry`, so adding a tag is DATA, not a Python edit.

## Why this skill exists

The user's words (2026-09-21): *"先登記 tag (this is the problem for your job, or
instuction is not good at all, skill can help too)"*.

Before this, `CAPABILITY_TAGS` in `skill_factor.py` was the ONLY way to add a
tag — adding one meant editing Python. That contradicts the repo's own rule
("DB-driven, never hand-listed"): `registered_tags()` READS the table, but the
WRITE path was hardcoded, so the table could never hold a tag the tuple did not
already name.

## What it does

1. Derive the tag's MEANING from evidence — never invent it.
2. Call `skill_factor.register_tag(conn, tag_key, definition, cite_ref=...)`.
3. The gate refuses an empty definition, a missing citation, and a silent
   duplicate.

## Not to do

- DO NOT invent a definition. Every definition needs a `cite_ref`; an invented
  definition is a guess with a heading.
- DO NOT overwrite an existing tag silently. Pass `update=True` only when the
  meaning really changed, so the change is an explicit act.
- DO NOT remove the `assert_known_tag()` gate. A typo must fail loudly, because
  an undefined tag matches zero skills SILENTLY.
