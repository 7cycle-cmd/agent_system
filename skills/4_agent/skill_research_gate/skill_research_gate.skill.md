---
task_id: "SKILL.RESEARCH.GATE"
display_task_id: "SKILL.RESEARCH.GATE"
name: "skill_research_gate"
catalog_id: 4
subcatalog_id: 1
final_verdict: "incomplete"
ingested: "no"
modified_files: []
qc_summary: "Research-before-plan HARD GATE — blocks implementation until research is complete + evidenced, with a 3-path classifier (spike/bounded/architectural)"
reason: "Workers repeatedly plan/implement without full research; this gate makes research a blocking prerequisite with evidence, not a suggestion"
artifacts:
  - skill-1.0.json
  - skill_research_gate.skill.md
schema: "research_gate_verdict"
---

# Skill: skill_research_gate

Package path: `skills/4_agent/skill_research_gate/`

**Provenance (github_proofed_lesson):** distilled from `obra/superpowers`
(MIT) — the widely-adopted agent methodology repo (skills `brainstorming` +
`writing-plans`). Its core insight, which this gate enforces:

> **HARD-GATE: Before taking any implementation action — writing product code,
> scaffolding, installing dependencies, or creating an external project —
> complete the selected path's prerequisites.**

And the rule our worker flow violates:

> **A reply approves the stage actually presented.** Approval of an idea or
> feature scope does not approve artifacts that do not exist yet.

Workers currently treat "research done" as permission to implement. It is not.

## The problem this stops

Observed repeat failure mode:

```
worker -> reads 1-2 files -> jumps to plan -> implements -> hits a blocker
       -> plan was wrong -> rework (or silent wrong output)
```

Root cause: **research is advisory, not blocking.** Nothing prevents
implementation, so *partial* research looks identical to *complete* research.

## Core rule (HARD GATE — non-negotiable)

```
BEFORE ANY IMPLEMENTATION ACTION, ALL of these must hold:

  R1. PATH_CLASS declared (spike | bounded | architectural)
  R2. RESEARCH COMPLETE for that path (see Step 2)
  R3. RESEARCH EVIDENCE present — every finding cites a real file/line/query
  R4. GATE_VERDICT = PASS

If ANY of R1-R4 fails -> ACTION: STOP. Do NOT write code.
Do NOT "start while they read". Report the gap.
```

Research approval != plan approval != implementation approval. Never collapse them.

## Step 1 — Classify the path (announce it out loud)

| Path | Trigger | Required output |
|------|---------|-----------------|
| **spike** | feasibility question ("can we...", "is it possible..."); output is an answer, not kept code | question + probe plan (2-3 sentences) -> approval -> investigate -> **recommendation** (label any code throwaway) |
| **bounded** | well-scoped change to code **that already exists in this repo** (a flag, a small endpoint, a one-file fix) | relevant files read + clarifying questions + **short design in chat** -> approval -> implement |
| **architectural** | new project / new subsystem / restructures how components fit / changes an interface others depend on | full research + spec + **written plan** -> approval -> implement |

**Ratchet is one-way.** Hidden complexity discovered mid-task **upgrades** the
path — stop, say so, step up. Nothing downgrades mid-task.
When in doubt, **take the heavier path.**

> WARNING: `bounded` measures **the repo**, not your familiarity.
> "I know this kind of app" is NOT bounded. No existing flow to read ->
> it is **architectural**.

## Step 2 — Required research (all of it, no skipping)

Every item must be answered with **evidence**, never from memory.

**R2.1 EXISTING_IMPLEMENTATION** — does this already exist?
- grep/read for an existing function, route, table, or skill doing this
- Evidence: file path + symbol name (or explicit "none found" + the search run)

**R2.2 DATA_MODEL** — what tables/columns/SSOT are involved?
- read the real DDL / store layer (`PRAGMA table_info`, the schema file)
- Evidence: exact table + column list

**R2.3 CALL_PATH** — who calls what, and what depends on it?
- trace callers/consumers before changing a signature
- Evidence: caller file:line list

**R2.4 CONSTRAINTS** — project rules this touches
- read the applicable instructions / guard docs / registry
- Evidence: quote the rule + its file

**R2.5 RISK_AND_ROLLBACK** — what breaks if wrong, how to revert?
- Evidence: named blast radius + revert step

**R2.6 UNKNOWNS** — what you could not determine
- list them; **do not guess**
- Evidence: each unknown named explicitly

Scope by path:
- `spike`         -> R2.1 + R2.6
- `bounded`       -> R2.1 + R2.2 + R2.3 + R2.4 + R2.6
- `architectural` -> **all six**

## Step 3 — Gate verdict

Output exactly this shape:

```
PATH_CLASS: [spike | bounded | architectural]
RESEARCH:
  EXISTING_IMPLEMENTATION: [finding + file/symbol, or "none found (searched: ...)"]
  DATA_MODEL: [tables/columns, or n/a for spike]
  CALL_PATH: [callers file:line, or n/a for spike]
  CONSTRAINTS: [rule quote + file, or n/a for spike]
  RISK_AND_ROLLBACK: [blast radius + revert, or n/a for spike]
  UNKNOWNS: [one per line, or (none)]
EVIDENCE_COUNT: [number of cited files/queries]
GATE_VERDICT: [PASS | STOP]
STOP_REASON: [why, if STOP; else (none)]
NEXT_ACTION: [report recommendation | present short design | write spec+plan | STOP and report gap]
```

**GATE_VERDICT = PASS requires ALL of:**
- PATH_CLASS declared
- every required research item answered for that path
- EVIDENCE_COUNT >= 1 and every finding cites something real
- UNKNOWNS either empty or explicitly acknowledged (acknowledged unknowns do
  **not** block a `spike`/`bounded`; they **do** block `architectural`)

**If a required item is unanswered -> GATE_VERDICT = STOP.**

## Hard Rules (not negotiable)

1. **Never implement before GATE_VERDICT = PASS.** No exceptions, no "it's
   obvious", no "too simple to need research".
2. **Never rely on memory.** Every finding cites a real file/line/query. An
   uncited finding is not a finding.
3. **Never treat research as approval.** Research != approval. Implementation
   approval must be a separate, explicit act.
4. **Never downgrade the path mid-task.** Hidden complexity upgrades only.
5. **Never guess a blocker away.** Unknowns go in UNKNOWNS, not into a silent
   assumption.
6. **Announce PATH_CLASS before asking questions**, so the human can override.
7. **One question at a time** when clarifying — do not batch questions.
8. **"Too simple to need research" is a red flag, not a justification.** A
   bounded change still requires R2.1-R2.4 on the flow being changed.

## Red Flags (self-check table)

| Thought | Reality |
|---------|---------|
| "This is too simple to need research" | The gate is the gate. Bounded still needs the existing flow read. |
| "I'll call it bounded and skip the spec" | Reaching for a label to skip work IS the doubt -> take the heavier path. |
| "Research is done, so I can start" | Research approval != implementation approval. Present, then STOP. |
| "It's obvious — I'll start while they read" | The gate is the **approval**, not the length. Present -> stop. |
| "I know this kind of app, so it's bounded" | Bounded measures **the repo**, not your familiarity. |
| "It grew but I'm almost done" | Hidden complexity upgrades the path mid-task. Stop and say so. |
| "They approved the spike, so the change is approved" | Each task gets its own classification and its own approval. |

## Input

```
{{task_request}}
{{known_context}}
```

## Examples

### Example 1 — under-researched (must STOP)

```
Request: "Add a quick endpoint to reset the chat log"

PATH_CLASS: bounded
RESEARCH:
  EXISTING_IMPLEMENTATION: found — /api/llm-tasks/clear in mouse_spot_helper.py
                          (same intent; a second endpoint would duplicate it)
  DATA_MODEL: chat_reply_log (+ chat_identity_log, chat_center_message reference chat_id)
  CALL_PATH: app.js bindReportOnly -> /api/llm-tasks/clear (only caller)
  CONSTRAINTS: hotkey_tools.md design rule #8 — register before adding; copilot-instructions ask-guard
  RISK_AND_ROLLBACK: blast radius = chat history only; revert = git checkout of the route
  UNKNOWNS: whether "reset" means clear-all vs clear-one (ambiguous)
EVIDENCE_COUNT: 5
GATE_VERDICT: STOP
STOP_REASON: UNKNOWNS has a blocking ambiguity ("reset" scope) AND an existing
             endpoint may already satisfy the request — clarify before building.
NEXT_ACTION: STOP and report gap
```

### Example 2 — research genuinely complete (may proceed)

```
Request: "Show chat_id column in the Chat Identity recent table"

PATH_CLASS: bounded
RESEARCH:
  EXISTING_IMPLEMENTATION: none found (searched: grep 'chat_id' app.js + recent_chat_identities)
  DATA_MODEL: chat_identity_log(id, session_id, chat_id, sha256, chat_hash, action, ide, llm, source, created_at)
  CALL_PATH: refreshChatIdentity() -> /api/chat_identity/recent -> recent_chat_identities()
  CONSTRAINTS: ui_skill tailwind vocab (rounded-2xl / border-line / bg-panel / text-muted)
  RISK_AND_ROLLBACK: blast radius = one table render; revert = single hunk in app.js
  UNKNOWNS: (none)
EVIDENCE_COUNT: 4
GATE_VERDICT: PASS
STOP_REASON: (none)
NEXT_ACTION: present short design
```

## Backend / integration

- Prompt SSOT row lives in `skill_prompt_ssot` (`skill-1.0.json` twin)
- Registered as a `dev_task` by `sync_skills_to_dev_tasks()` (idempotent scan of
  `skills/**/*.skill.md` frontmatter)
- Sits in the chain with the existing guards:

```
skill_research_gate  ->  skill_plan_mode_research  ->  plan
                     ->  skill_agent_mode_execution_gate  ->  implement
```

- `skill_plan_mode_research` structures Plan-mode research (read-only).
- `skill_agent_mode_execution_gate` classifies risk before executing.
- `skill_research_gate` (this) is the **blocking** prerequisite: it stops the
  plan from being written at all until research is complete and evidenced.
