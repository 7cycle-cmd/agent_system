---
task_id: SKILL.5W1H
display_task_id: SKILL.5W1H
name: skill_5w1h
catalog_id: 4
subcatalog_id: 0
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: The general 5W1H template — six dimensions, as data, for any artifact
reason: A plan or a contract that cannot answer one of the six is INCOMPLETE, and that must be checkable rather than stated
artifacts:
  - skill_5w1h.skill.md
schema: "skill_contract_field (field_name, hard_rule, mandatory)"
---
# Skill: skill_5w1h

The general 5W1H template. Six dimensions, declared ONCE as data, used for BOTH
a document template and a contract's field set.

## Why this skill exists

The user's words (2026-09-22):

> "skill = 5W1H for plan.md"
> "skill = multi dismenional SSOT, fuck...."

and, asked whether 5W1H is a document format or a registration dimension:

> "-> both"

So the six dimensions live in ONE place (`skill_5w1h.DIMENSIONS`) and both
halves are derived from it. A `.md` template and a set of DB fields maintained
separately WILL disagree — this repo measured that exact drift before: 46
`.skill.md` files against 28 ssot rows, of which 12 disagreed and 20 had no row
at all.

## The six dimensions

| # | Dimension | The question | The hard rule |
|---|-----------|--------------|---------------|
| 1 | **what** | What is being changed, named concretely? | names the artifact (a file, a table, a function), not a topic |
| 2 | **why** | Why is it needed — what breaks without it? | states the defect or the gap, not a benefit |
| 3 | **who** | Who writes it, who approves it, who is affected? | names the writer AND the approver; "the agent" alone is not an answer |
| 4 | **when** | When does it happen — before what, after what? | states the ORDER relative to another act, not a date |
| 5 | **where** | Where does the artifact live — the exact path? | a real path, not a directory name |
| 6 | **how** | How is it verified — which command or check? | a command that can be RUN or a check that can FAIL, not "review it" |

All six are `mandatory=1`. A plan missing any one of them is the defect the
template exists to catch.

## What it does

1. `render_template()` — the document template, DERIVED from `DIMENSIONS`.
2. `seed_contract_5w1h(conn, contract_id)` — writes the six
   `skill_contract_field` rows through `skill_contract_store.upsert_field`, so
   the SAME gate applies as for any other field.
3. `missing_for(conn, contract_id)` — which of the six a contract does not
   declare. Reported, not raised: a contract that predates the template is
   INCOMPLETE, not broken.

## Not to do

- DO NOT hand-write a second copy of the six dimensions. Derive from
  `DIMENSIONS`; a copy is the drift this skill exists to prevent.
- DO NOT register a dimension as a bare heading. `upsert_field` HARD-REJECTS an
  empty `hard_rule`, and that refusal is the point: a heading can be left empty,
  a hard rule cannot.
- DO NOT ignore the return value of `upsert_field`. It RETURNS a refusal dict
  rather than raising, so an unchecked call lets a refused field look written.
- DO NOT treat a missing dimension as an error. It is INCOMPLETE, and the
  difference matters to a reader.
