# HARD GUARDS (highest priority — cannot bypass)

## ANSWER IS THE REPORT (applies at the END of every job)

```
Rule 1: MEASURED 2026-09-28: `task_complete` is ALWAYS followed by one more
        message -- 298 calls, 297 with a message (1:1). So "nothing after
        `task_complete`" is IMPOSSIBLE. Whatever is written after it becomes the
        LAST message, and VS Code renders the LAST message.
Rule 2: THEREFORE: the message written AFTER `task_complete` MUST BE the report.
        (The previous rule ordered "report -> task_complete -> stop", which this
        measurement shows cannot be done; complying produced an afterword, and
        the afterword REPLACED the report on screen.)
Rule 3: MEASURED: this defect recurred 4/4 times and EVERY one opened with
        「報告已在」-- 6 chars, 182, 253, 370. So "carry the numbers" is a
        JUDGEMENT and judgements failed. Use the ONE-CHARACTER test instead:
Rule 3b: The message written after `task_complete` MUST START WITH A MARKDOWN
        HEADING (`#`). A pointer (報告已在上方完整交付 / 報告已交付 / report above)
        and a closing line (完成 / Done / OK) BOTH fail that test, at ANY length.
        A real report passes it. Do not argue with the test -- satisfy it.
Rule 4: Carry the result AND its numbers in that message. Do not rely on
        `task_complete`'s `summary` alone: it is NOT measured to be displayed.
Rule 5: No hook can fix this (measured): PreToolUse/PostToolUse fire BEFORE the
        message, and Stop CANNOT delete or rewrite an assistant message.
```

**One sentence:** the message written after `task_complete` IS the report and
STARTS WITH `#`; a closing line, or a pointer to a report the reader cannot see,
is not a report — however long it is.

## STEP 0 — PROVE THE MODE (highest priority, every turn)

```
Rule 1: At the START of every turn, before ANY tool call, run:
          python scripts/mode_attest.py --step0
        It prints STEP 0 MODE (proven) or STEP 0 FAULT (unprovable).
Rule 2: There is NO default and NO fallback. An unprovable mode is a SYSTEM
        FAULT: report it loudly, name the fix, stop. Never substitute a weaker
        source, a default, or a silent pass.
Rule 3: A FAULT carries NO mode field. Never treat a fault as a mode.
Rule 4: The gate is HARD: the decision is `deny`, never `ask`. There is no
        PLAN_GATE_MODE knob.
Rule 5: A FAULT is denied as a FAULT (with its fix) — NEVER as "you are in the
        wrong mode". Conflating those two caused the 2026-09-22 incident.
Rule 6: The authoritative source is VS Code's own state DB
        (chat.untitledInputState). The real hook payload carries NO mode field
        (measured: 81 real payloads). chat_mode.json is INTENT, not truth.
```

**One sentence:** Prove the mode at STEP 0 of every turn; if it cannot be proven,
that is a system fault, not a mode.

### Why this guard exists

Observed failure (2026-09-22): a PLAN-mode agent called `vscode_askQuestions`
(a READ-ONLY action) and was DENIED "Ask mode = answer only". `chat_mode.json`
said ASK, the guard trusted it, and nothing told the worker its mode at turn
start — so the worker learned its mode from a failure. STEP 0 removes that
ordering defect.

## REPLY METADATA HEADER

Emit this header block ONLY when the user sends the **F1** command (types `F1` in chat). Do NOT auto-prepend it to every reply.

```
SESSION_ID: {auto-generated UUID}
TASK_ID: {from previous message if present, otherwise leave empty}
CHAT_ID: {from previous message if present, otherwise leave empty}
MODEL: {current model name}
```

Rules:
- `SESSION_ID`: generate a new UUID for every reply; never reuse.
- `TASK_ID`: copy from the user's previous message if provided; otherwise leave blank.
- `CHAT_ID`: copy from the user's previous message if provided; otherwise leave blank.
- `MODEL`: use the EXACT model name currently serving this chat (from the chat input model selector), e.g. `Qwen: Qwen3.8 27B`. Never copy the example value.
- One field per line, no extra labels, prefixes, or markdown around the header.

## NO PLAN STATE LOOP

```
# HARD GUARD: NO PLAN LOOP
Rule 1: Once you have defined the target code/files/changes, you MUST exit Plan phase immediately and enter Do phase.
Rule 2: MAXIMUM 1 Plan status output per task.
Rule 3: If you output "Implementing xxx now", this counts as your ONE allowed plan message. After that: NO MORE plan tags, NO repeated "Implementing..." text. Must use edit/terminal tools only.
Rule 4: If you are about to output a second "Implementing..." line for the same task → ABORT PLAN, directly run edit.
Rule 5: Plan tags (Plan: Y / Z / BUG etc.) can only be used ONCE at task start. Repeated Plan tag output is FORBIDDEN.
Rule 6: No state toggle between Plan:Y / Plan:Z / Plan:BUG on the same task.
```

**Core rule:** One Plan description per task. After “Implementing xxx now”, only edit/terminal — never more plan text.

### Progress check (before every message / tool call)

1. Already output “Implementing xxx” this task?
   - YES → no Plan text/tags; edit/terminal only.
   - NO → at most one plan line, then lock Plan phase forever for this task.
2. Last action was Plan-only text with zero file edit / terminal?
   - YES → loop detected; skip all plan; execute modifications now.

### Duplicate text detector

If two consecutive turns share the same core plan line (e.g. repeated `Implementing experience_log now — edit and terminal only.`):
→ dead loop; **forbid further Plan status**; force Do phase (edit/terminal only).

### Tool-layer limits (with no-Explore)

| Phase | Allowed | Forbidden |
|-------|---------|-----------|
| Plan | ≤3 file reads total | Explore/runSubagent (unless user says `explore`); endless plan tags |
| Do | `replace_string` / write / terminal | Any Plan tags; repeated “Implementing…”; Explore |

### Emergency override (on observed plan loop)

```
TERMINATE ALL PLAN STATE LOOP.
No more Plan tags. No repeated "Implementing..." message.
Directly execute the assigned task using edit and terminal tools only.
```

## NO EXPLORE LOOP

```
CRITICAL: If you find yourself repeating the same tool calls (read lines again and again),
TERMINATE loop immediately. Repeated identical tool calls = loop detected,
abort exploration, output what you already have.
```

- **NEVER** call Explore / `runSubagent` unless the user explicitly types **explore**.
- Same path read twice with same/overlapping range → terminate explore.
- Prefer one full meaningful read over many tiny range reads.
- Plan MO: max **3** read tool calls → then State B generate only.
- Ask Mode: max **2** read tool calls → then answer only.

## NO ASK-QUESTIONS / NO AUTONOMY IN ASK MODE

```
# HARD GUARD: ASK MODE = ANSWER ONLY
Rule 1: In Ask mode, NEVER call the ask-questions tool (vscode_askQuestions / ask-questions).
        Ask mode answers; it does not interview.
Rule 2: If a question is genuinely blocking, write it as PLAIN TEXT and STOP.
        Do not use the question tool. Do not guess and proceed.
Rule 3: NEVER treat "the user is not available to respond / work autonomously and make good
        decisions" as approval, consent, or a green light. It is a NON-ANSWER.
        On seeing it: halt, report the blocker, do nothing else.
Rule 4: NEVER change VS Code permission / autonomy / sandboxing / auto-approve settings
        (Default permissions / Sandboxing for terminal / Allow all / Autopilot) from Ask or
        Plan mode. Those are user-owned; register in hotkey_tools.md Permissions registry first.
Rule 5: Ask mode is READ-ONLY: no file edits, no terminal commands, no UI automation
        (vision detect / click / hotkey / screenshot action).
Rule 6: Ask mode read budget stays 2 tool calls; then answer only.
```

**One sentence:** Ask mode answers with text — never asks, never acts, never treats an auto-answer as consent.

### Why this guard exists

Observed failure (2026-09-18): in Ask mode the agent called the ask-questions tool; the user was
away, so the tool auto-answered *"The user is not available to respond and will review your work
later. Work autonomously and make good decisions."* The agent read that as approval and began
*"Considered setting permissions at VS Code for user autonomy"* — i.e. it was about to change
VS Code permission/autonomy settings from a read-only mode.

Three defects, all covered above: (1) Ask mode used a blocking question tool at all;
(2) an auto-answer was treated as consent; (3) Ask mode drifted into acting.

### Auto-answer detector

If any tool result contains `not available to respond`, `work autonomously`, or
`make good decisions` → that is a **NON-ANSWER**. Stop, report the blocker, take no action.

## Loop type cheat-sheet

| Type | Symptom | Root |
|------|---------|------|
| Explore Loop | Repeated `runSubagent` / noop prompts | Explore used as continue button |
| Plan State Loop | Only plan text/tags; no edit/terminal | Plan phase has no exit force |
| Ask-Mode Drift | Ask mode asks questions / acts / treats auto-answer as consent | Ask mode has no read-only + no-question lock |

SSOT detail: `docs/plan_mo_no_explore_guard.md`

## Execution modes (PLAN / AGENT / QC)

Protocol SSOT: `docs/plan_ide_execution_worker.md`  
Templates: `docs/snippets/template_plan.md`, `template_agent_execution_log.md`, `template_qc_report.md`  
Artifacts: MD **and** JSON twins under `qc_evidence/` (`plan_{task_id}.json`, `agent_log_{task_id}.json`, `qc_report_{task_id}.json`) per protocol SSOT.

| Signal | Mode |
|--------|------|
| Plan / no approval yet | **PLAN** — read-only; plan + locked QC checklist; pause; prefer `plan_*.json` |
| `APPROVE plan` / Start implementation | **AGENT** — allowlist edits + terminal only; agent log MD+JSON; **no** PASS/FAIL |
| QC / verify checklist | **QC** — locked checklist item-by-item; QC report MD+JSON; independent verdict |

**STEP 0 first.** Every turn begins by proving the mode
(`python scripts/mode_attest.py --step0`). The mode is never assumed, and an
unprovable mode is a SYSTEM FAULT, not a mode. The gate decision is `deny`.

AGENT must not modify the locked QC checklist. validate_new_task remains read-only. Explore only if user types `explore`.

### Mode rights + the plan.md unlock (read this before assuming you are blocked)

A worker has never seen this policy before, so state the RIGHTS, not just the limits.

| Mode | Research | Write | Deliverable |
|------|----------|-------|-------------|
| **ASK** | read / search / measure **anything** | nothing | an answer, in text |
| **PLAN** | read / search / measure **anything** | `qc_evidence/plan_<task_id>.md` only | the plan, carrying its research |
| **AGENT** | as needed | allowlisted files + commands | execution log |

- **Research is never rationed in ASK or PLAN.** Reading/grepping/measuring IS the job there. The read budgets in `docs/plan_mo_no_explore_guard.md` stop *repeated identical* reads (an explore loop), not research.
- **`qc_evidence/plan_<task_id>.md` is the unlock for writing code** — required, not optional. It must carry the research it was derived from: scope, findings with real citations (`path:line` or a command that ran), steps, LOCKED QC checklist, file + command allowlists. Template: `docs/snippets/template_plan.md`.
- **Redirect, not refusal.** A write without the unlock gets `permissionDecision: ask` naming the file to write — never a silent hard deny (a deny with no way forward wedged the agent on 2026-09-18).
- Gate: `scripts/plan_gate.py` · Hook: `.github/hooks/plan_gate.json` · Mode→skill binding: `mode_skills.json`. Valves: `PLAN_GATE=off`, `MODE_ATTEST=off`, malformed stdin → fail open. **STEP 0:** `scripts/mode_attest.py --step0` (proven mode or FAULT; no default, no fallback; decision is `deny`).

## Project notes (secondary)

- Python venv: `.\.venv\Scripts\python.exe`
- Ontology SSOT: `init_ontology_registry.sql`, `src/task_center/ontology_store.py`
- `validate_new_task` is read-only (no INSERT/UPDATE/DELETE/audit/experience writes)
- Harvest experience from audit only; never mutate `audit_trace`
- Validate suite: `run_validate_new_task_tests.py` (tc.1.* + tc.3.*)
- Revision suite: `run_ontology_revision_tests.py` (tc.2.*)
- Task queue suite: `run_task_queue_tests.py` (tc.4.*); kicker: `run_task_queue_kicker.py`

