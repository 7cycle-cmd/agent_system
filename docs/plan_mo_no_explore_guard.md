# Plan MO / Ask Mode — No infinite Explore loop + No Plan state loop

**Status:** ops guard (agent control) · **not** product/MCS logic  
**Why:** Agent thrash = (A) repeated `read_file` / Explore subagent, or (B) endless Plan text/tags with zero edit/terminal. Not a work_order or lifecycle bug.

**Install:** Always-on copy lives in `.github/copilot-instructions.md` and user `no-plan-loop.instructions.md`. Keep this file as SSOT detail.

---

## HARD GUARD: NO PLAN STATE LOOP (highest priority)

```
# HARD GUARD: NO PLAN LOOP
Rule 1: Once you have defined the target code/files/changes, you MUST exit Plan phase immediately and enter Do phase.
Rule 2: MAXIMUM 1 Plan status output per task.
Rule 3: If you output "Implementing xxx now", this counts as your ONE allowed plan message. After that: NO MORE plan tags, NO repeated "Implementing..." text. Must use edit/terminal tools only.
Rule 4: If you are about to output a second "Implementing..." line for the same task → ABORT PLAN, directly run edit.
Rule 5: Plan tags (Plan: Y / Z / BUG etc.) can only be used ONCE at task start. Repeated Plan tag output is FORBIDDEN.
Rule 6: No state toggle between Plan:Y / Plan:Z / Plan:BUG on the same task.
```

**Core (one line):** Each task may have **one** Plan description. After “Implementing xxx now”, **only** edit code and run terminal.

### Progress check (before every message / tool call)

1. Already output “Implementing xxx” this task?
   - ✅ Yes → forbid any Plan text/tags; go straight to edit/terminal.
   - ❌ No → at most **one** plan line, then lock Plan phase for the rest of the task.
2. Previous action was Plan-only text with **no** file edit / terminal?
   - ✅ Yes → classify as loop; skip all plan; execute modifications immediately.

### Duplicate text detector

If two consecutive turns emit the same core plan sentence  
(e.g. twice: `Implementing experience_log now — edit and terminal only.`):

→ dead loop → **ban further Plan status** → force Do phase (edit/terminal only).

### Tool-layer limits (paired with no-Explore)

| Phase | Max reads | Allowed tools | Forbidden |
|-------|-----------|---------------|-----------|
| Plan | 3 file reads total | read only (gather) | Explore/runSubagent unless user says `explore`; endless plan tags |
| Do | 0 explore | `replace_string` / write / terminal | Plan tags; repeated “Implementing…”; Explore |

### Emergency interrupt prompt (worker override)

When a plan loop is observed, paste this and skip all plan logic:

```
TERMINATE ALL PLAN STATE LOOP.
No more Plan tags. No repeated "Implementing..." message.
Directly execute the assigned implementation using edit and terminal tools only.
Follow the user's concrete requirements (DDL / helpers / harvest / TDD / read-only constraints).
Deliverables: code edits + terminal evidence + test report. No more plan status lines.
```

### Two dead-loop types (do not confuse)

| Type | Symptom | Root |
|------|---------|------|
| **Explore Loop** (old) | Repeated `runSubagent`, noop prompts (“Return OK/halt”) | Explore abused as continue button |
| **Plan State Loop** (new) | No Explore; endless Plan text + Plan tag toggles; **zero** edit/terminal | Plan phase has no hard exit into Do |

---

## CRITICAL — NO EXPLORE LOOP

```
CRITICAL: If you find yourself repeating the same tool calls (read lines again and again),
TERMINATE loop immediately. Repeated identical tool calls = loop detected,
abort exploration, output what you already have.
```

- **NEVER** call Explore / `runSubagent` unless the user explicitly says **explore**.
- Same path read **twice** with same/overlapping range = **terminate** explore.
- Prefer **one** full meaningful read over many tiny `lines 1–5` reads.

---

## Plan MO — fixed prompt block

```
# Plan MO — No infinite Explore loop guard rule
Context: Plan MO, Autopilot mode. STRICTLY forbid infinite for-loop / Explore read loops.

Hard Rules (highest priority, cannot bypass):
1. MAX READ ROUNDS = 3. You can invoke read/file tools AT MOST 3 times total for this task.
   After 3 read tool calls, NO MORE read operations. (3 tool calls, not 3 files × many ranges.)
2. Two states ONLY:
   State A: Explore (gather info, read files). Once you have sufficient information to build
   the deliverable → SWITCH TO State B immediately.
   State B: Generate. NO read / explore / extra file scan allowed in State B.
3. Exit trigger: If you already have all required entities:
   channel, module, capability, api, function, db_table, field
   → STOP all exploration immediately.
4. NEVER use while/for explore loop logic. No repeated "read lines" over and over.
5. If you hit MAX READ ROUNDS (3), stop reading; use existing data to generate.
   DO NOT continue reading.
6. After proposal generated, send to skill_proposal_validate QC (when that is the task).
7. If QC PASS: hand validated proposal to Agent MO, stop Plan MO task.
8. If QC FAIL: ONLY 1 retry to fix proposal, NO extra file exploration on retry.
   After 1 retry, halt Plan MO and output error report.
9. NEVER call Explore/runSubagent unless user says "explore".
10. Duplicate detector: consecutive identical read (same path + same range) → abort explore.

Task pattern:
Receive raw requirement → collect reference (≤3 reads) → extract seven entities →
structured proposal → QC → handoff or error report.
```

---

## Ask Mode — fixed prompt block

```
# Ask Mode — No infinite Explore loop
Hard Guard Rules:
1. MAX read operations = 2. Cannot read files more than 2 times (2 tool calls total).
2. No for/while explore loop. Do NOT repeatedly read segments.
3. Logic:
   - Step 1: Read up to 2 times to collect required context.
   - Step 2: Once context enough, STOP reading permanently.
   - Step 3: Generate answer, output final result. No further file operations.
4. Do NOT keep exploring even if you think more details may help.
5. If hit read limit, answer based on existing data, mark "read limit reached".
6. NEVER call Explore/runSubagent unless user says "explore".
7. Same path read twice = terminate explore; answer with what you have.
```

---

## Ask Mode — no questions, no autonomy (HARD GUARD)

**Status:** ops guard (agent control) · **not** product/MCS logic
**Why:** Ask mode is *answer only*. It must not interview the user, must not act, and must not
treat an auto-answer as consent.

### Observed failure (2026-09-18)

In Ask mode the agent called the ask-questions tool. The user was away, so the tool auto-answered:

> *"The user is not available to respond and will review your work later. Work autonomously and
> make good decisions."*

The agent read that as approval and began *"Considered setting permissions at VS Code for user
autonomy"* — i.e. it was about to change VS Code permission / autonomy settings from a read-only
mode.

Three defects:

| # | Defect | Covered by |
|---|--------|-----------|
| 1 | Ask mode used a blocking question tool at all | Rule 1, Rule 2 |
| 2 | An auto-answer was treated as consent | Rule 3 + auto-answer detector |
| 3 | Ask mode drifted into acting (permission settings) | Rule 4, Rule 5 |

### Fixed prompt block

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

**One sentence:** Ask mode answers with text — never asks, never acts, never treats an
auto-answer as consent.

### Auto-answer detector

If any tool result contains `not available to respond`, `work autonomously`, or
`make good decisions` → that is a **NON-ANSWER**. Stop, report the blocker, take no action.

### Permission settings are user-owned

VS Code permission / autonomy settings (Default permissions / Sandboxing for terminal /
Allow all / Autopilot) are registered in `hotkey_tools.md` → **VS Code Permissions 註冊表**.
Rule: any change must be registered there first (owner + purpose + audit). An agent in Ask or
Plan mode must never change them, and must never propose doing so as a "next step".

### Optional deterministic enforcement (hook)

Prompt guards are non-deterministic. For hard enforcement, add a `PreToolUse` hook that reads
the PROVEN mode and denies the ask-questions tool when mode is `ASK`:

- `.github/hooks/ask_mode_guard.json` — hook config
- `scripts/ask_mode_guard.py` — reads stdin JSON, checks `tool_name`, returns
  `hookSpecificOutput.permissionDecision = "deny"` when the PROVEN mode is `ASK`.

**SUPERSEDED 2026-09-22 — the mode source changed.** This section used to say the hook reads
`chat_mode.json`. It no longer does. Measured 2026-09-22: that file said `ASK` while the user
was in AGENT, and the hook then DENIED a PLAN-mode agent's read-only `vscode_askQuestions`
call with "Ask mode = answer only". The mode now comes from `scripts/mode_attest.py`, which
reads VS Code's own persisted chat input state
(`<workspaceStorage>/<hash>/state.vscdb` → `chat.untitledInputState` → `mode.id`). The real
hook payload carries **no** mode field (81 real payloads inspected), so the state DB is the
authoritative source. See `.github/copilot-instructions.md` → **STEP 0**.

**Scope is deliberately NARROW — only the ask-questions tool.** Edit / terminal tools are
**not** denied by the hook: VS Code's own permission system is authoritative for those, and
duplicating it here caused a self-lockout (see below).

#### Self-lockout incident (2026-09-18) — why the scope was narrowed

The first version of the hook also denied edit / terminal tools in Ask mode. It read
`chat_mode.json` as the mode source, but that file is **only** written by `f_mode_switch.py`
(Ctrl+Alt+P). The user switched to Agent mode via the UI, so the file stayed `ASK` → the hook
denied every edit and terminal call → the agent was completely wedged and could not even fix
the hook itself.

Three defects, all now fixed:

| # | Defect | Fix |
|---|--------|-----|
| 1 | Denied edit / terminal (beyond the original purpose) | Scope narrowed to ask-questions only |
| 2 | No staleness check — a stale file was trusted forever | `MODE_MAX_AGE_SEC = 600`; older → fail open |
| 3 | No escape hatch | env `ASK_MODE_GUARD=off` disables the hook |

**Design rule (do not repeat):** a hook may only block something *narrow, unambiguous, and
non-collateral*. Any hook that depends on an external state file MUST have a staleness check
and an escape hatch. Broad tool classes (edit / terminal) belong to VS Code's permission
system — do not build a worse duplicate.

#### Verification

```powershell
cd c:\projects\agent_system
python f_mode_switch.py --set ASK
'{"tool_name":"ask-questions"}'          | python scripts\ask_mode_guard.py   # -> deny
'{"tool_name":"run_in_terminal"}'        | python scripts\ask_mode_guard.py   # -> silent (allow)
'{"tool_name":"replace_string_in_file"}' | python scripts\ask_mode_guard.py   # -> silent (allow)
$env:ASK_MODE_GUARD="off"
'{"tool_name":"ask-questions"}'          | python scripts\ask_mode_guard.py   # -> silent (disabled)
Remove-Item Env:\ASK_MODE_GUARD
python f_mode_switch.py --set AGENT
```

---

## Caller / Agent MO (when validating work_order)

```
Caller TDD / audit tasks:
- State B preferred: work_order path already known
  (e.g. qc_evidence/work_order_t_wo_tskill01_001.json).
- At most 1 read of that JSON (or 1 full read), then execute/report.
- No Explore. No repeated line-range reads.
- On audit FAIL: report findings and halt; do not thrash tools.
```

---

## What this does NOT fix

| Layer | Owner |
|-------|--------|
| Infinite Explore/read in Copilot | **This doc / custom instructions** |
| Infinite Plan-status text (no Do) | **This doc / `.github/copilot-instructions.md`** |
| Ask mode asking questions / acting on auto-answer | **This doc / `.github/copilot-instructions.md`** |
| work_order stub TDD / ignored `broken` | **Domain** — fix builder output or re-emit |
| managed_coding / SSOT apply | **Human caller** after green TDD |

Prompt guards reduce thrash; they do not make invalid TDD cases pass. Runtime cannot fully force Do-phase if the model ignores instructions — human emergency prompt still applies.

---

## How to install

1. **Workspace always-on:** `.github/copilot-instructions.md` (NO PLAN LOOP + NO EXPLORE + NO ASK-QUESTIONS at top).
2. **User always-on:** `%APPDATA%\Code\User\prompts\no-plan-loop.instructions.md` (`applyTo: "**"`).
3. Copy the **NO PLAN LOOP** + **CRITICAL Explore** blocks + the mode block (Plan or Ask) to the top of that mode’s MO system prompt when using external MO runners.
4. Keep this file as SSOT detail for guard text.
5. Tests:
   - Plan MO: ≤3 reads → generate → QC → handoff; no endless `Read lines x to y`.
   - Implement tasks: ≤1 “Implementing…” line → immediate edit/terminal; no second plan line; no Plan tag thrash.
   - Ask mode: no ask-questions tool call; blocking question = plain text + stop; auto-answer text
     (`not available to respond` / `work autonomously`) → halt + report blocker, no action.

## Retry quotas

| Mode | Max reads | Plan status lines | QC / fix retry |
|------|-----------|-------------------|----------------|
| Plan MO | 3 tool calls | 1 | 1 proposal fix after QC FAIL, then halt |
| Ask Mode | 2 tool calls | 0–1 | n/a (answer only; no ask-questions tool, no autonomy changes) |
| Do / implement | 0 explore (reads only if strictly needed before first edit) | 1 max then lock | edit/terminal only |
