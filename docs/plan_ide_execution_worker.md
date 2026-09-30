# IDE Execution Worker — PLAN / AGENT / QC

**Status:** ops protocol (agent control) · **not** product/MCS logic  
**IDE target:** VS Code Autopilot (`vscode_autopilot`)  
**Related guards (higher priority):** `docs/plan_mo_no_explore_guard.md`, `.github/copilot-instructions.md` HARD GUARDS

This document is the SSOT for the three fixed modes. Do not invent extra modes or bypass shared ground rules.

---

## Shared Ground Rules (ALWAYS ENFORCED)

1. **Scope lock:** Only modify files and run commands defined in the current approved plan allowlists. No out-of-scope refactor.
2. **Output format:** Capture full factual records: file paths, terminal commands, exit codes, file diff snippets.
3. **No self-judgement (AGENT):** NEVER declare task SUCCESS / FAIL in AGENT MODE. Only output raw execution evidence in the agent execution log.
4. **Read-only protection:** All validate check functions (especially `validate_new_task`) must remain read-only — no write operations inside them.
5. **Boundary:** If the task hits ambiguity or conflict, stop execution and return a warning. Do NOT guess.

Additional hard guards (cannot bypass):

- **NO PLAN STATE LOOP** — max one plan status line per task; after plan/approval, edit/terminal only.
- **NO EXPLORE LOOP** — never call Explore / `runSubagent` unless the user explicitly types `explore`.
- **NO ASK-QUESTIONS IN ASK MODE** — Ask mode never calls the ask-questions tool; a blocking question is plain text + stop. An auto-answer ("user not available / work autonomously") is a NON-ANSWER, never consent. Ask/Plan mode never change VS Code permission / autonomy / sandboxing settings.

---

## Mode triggers (vscode_autopilot)

| User signal | Mode | Behavior |
|-------------|------|----------|
| Plan request / task without approval | **PLAN MODE** | Read-only; produce plan + locked QC checklist; **pause** |
| `APPROVE plan` / `Start implementation` + approved plan | **AGENT MODE** | Edit/terminal per allowlist only; write agent log; **no PASS/FAIL** |
| `QC` / verify checklist + agent log | **QC MODE** | Verify locked checklist; independent PASS/FAIL report |
| `explore` | Explore allowed | Only when user types `explore` |
| Ambiguity | Halt | Warning only |

---

## Mode rights (read this FIRST — a worker has never seen this policy before)

### STEP 0 — PROVE the mode before anything else (MANDATORY, every turn)

The mode is **proven**, never assumed. At the start of EVERY turn, before any
tool call, run:

```text
python scripts/mode_attest.py --step0
```

It prints exactly one of two things:

```text
STEP 0 MODE: AGENT
  source  : vscode_state_db:chat.untitledInputState
  evidence: mode.id='agent' kind='agent'
```

```text
STEP 0 FAULT: the chat mode could NOT be proven.
  reason : ...
  fix    : python scripts/mode_attest.py --step0
  This is a SYSTEM FAULT, not a mode. Do NOT assume a mode.
```

**There is no default and no fallback.** An unprovable mode is a SYSTEM FAULT to
be reported loudly — never papered over by a weaker source, a default, or a
silent pass. A FAULT carries **no mode field at all**, so nothing downstream can
mistake a fault for a mode.

**Why this exists (measured 2026-09-22).** A PLAN-mode agent called
`vscode_askQuestions` (a READ-ONLY action) and was DENIED with "Ask mode = answer
only". The chain: `chat_mode.json` said ASK → the guard trusted it → it denied.
Nothing verified the mode, and nothing told the worker its mode at turn start, so
the worker learned its mode from a FAILURE. STEP 0 removes that ordering defect.

**The authoritative source.** VS Code's own persisted chat input state:
`<workspaceStorage>/<hash>/state.vscdb` → `chat.untitledInputState` →
`{"mode":{"id":"agent","kind":"agent"}}`. Measured facts:
- The real hook payload carries **NO** mode field (81 real payloads inspected;
  keys are `hook_event_name`, `tool_name`, `tool_input`, `cwd`, `session_id`,
  `timestamp`, `transcript_path`, `tool_use_id`, `prompt`).
- `kind` is **not** a discriminator: Agent and Plan both report `kind:"agent"`.
  Only `id` separates them. Plan is a custom agent, so its id is a URI resolved
  through `chat.customModes.local`.
- `chat_mode.json` is the hotkey's INTENT record and was measured WRONG. It is
  kept, but it is no longer a source of truth.

**The gate is HARD.** The decision is `deny`, never `ask`. There is no
`PLAN_GATE_MODE` knob — deny is the one behaviour, not something that can be
tuned into existence. A FAULT is denied as a FAULT (with its fix), never as
"you are in the wrong mode".

A worker arriving in ASK or PLAN does not know what it is allowed to do. A list
of prohibitions reads as a wall, and a worker that does not know its **rights**
either freezes or guesses — and a guess looks identical to a decision.

So state the rights, not just the limits. The card below is emitted
automatically at session start AND on every user prompt by
`scripts/plan_gate.py` (SessionStart + UserPromptSubmit hooks), and the same text
lives in `mode_skills.json` → `_rights`.

| Mode | Research | Write | Deliverable |
|------|----------|-------|-------------|
| **ASK** | Read / search / measure **anything** | nothing | an answer, in text |
| **PLAN** | Read / search / measure **anything** | `qc_evidence/plan_<task_id>.md` only | the plan, carrying its research |
| **AGENT** | as needed | allowlisted files + commands | execution log |

**Research is never rationed in ASK or PLAN.** Reading, grepping and measuring
are the job in those modes, not a detour. The read budgets in
`docs/plan_mo_no_explore_guard.md` exist to stop *repeated identical* reads
(an explore loop), not to stop research.

### The unlock: `plan.md`

`qc_evidence/plan_<task_id>.md` is the artifact that unlocks writing code.
It is **required**, not optional (this changed 2026-09-21; it was previously
marked optional in the artifact table below).

A plan must carry the research data it was derived from. A plan without its
findings is a guess with headings:

1. Scope (in / out)
2. **Findings** — each citing a real `path:line` or a command that ran
3. Step-by-step plan, derived from those findings
4. LOCKED QC checklist (id, criterion, method) — frozen at approval
5. File allowlist + terminal command allowlist
6. Forbidden actions

Template: `docs/snippets/template_plan.md`

### Redirect, not refusal

When a write is attempted without the artifact that unlocks it, the gate does
**not** simply refuse. It names the file to write and what must be in it, then
lets the worker retry. The default decision is `ask` (a prompt the human can
approve through), never a silent hard `deny` — a hard deny with no way forward
is what wedged the agent on 2026-09-18 (see the self-lockout incident in
`docs/plan_mo_no_explore_guard.md`).

- Gate: `scripts/plan_gate.py` · Hook: `.github/hooks/plan_gate.json`
- Mode → skill binding: `mode_skills.json`
- Safety valves: `PLAN_GATE=off` (disable), `PLAN_GATE_MODE=deny` (escalate),
  malformed stdin / unresolvable task id → fail **open** and log.

---

## Agent mode Entry Protocol (MANDATORY)

**Applies to:** every transition into AGENT MODE (triggered by `APPROVE plan` / `Start implementation`).

**Rule:** Before ANY edit or terminal command, the agent MUST run the execution gate skill (`skill_agent_mode_execution_gate`, v1.1) and act on its result. This is a protocol-level enforcement — the gate is prompt-based, so compliance depends on the executor running it; this section makes that a mandatory first step.

**Steps (in order):**

1. **Run the gate.** Classify the planned change via `skill_agent_mode_execution_gate` v1.1:
   - `CLASS: SAFE | MEDIUM | HIGH`
   - `CHANGES:` (specific file + change)
   - `UNANSWERED_RISKS:` (one per line; `(none)` if empty)
   - `ACTION: EXECUTE | CONFIRM | AWAIT_APPROVAL | STOP`
   - `Reason:`

2. **Check UNANSWERED_RISKS first (highest priority).** If not `(none)` → `ACTION: STOP`. Do NOT proceed even if CLASS is SAFE.

3. **Act on CLASS:**
   - `SAFE` → `EXECUTE` (proceed directly)
   - `MEDIUM` → `CONFIRM` (list changes, wait for one-line confirmation)
   - `HIGH` → `AWAIT_APPROVAL` (list changes, wait for explicit PM approval)

4. **Only after the gate result is PROCEED/CONFIRM-approved** may the agent edit files or run terminal commands.

**Hard rules (cannot bypass):**

- `Start implementation` is NOT approval — it only triggers agent mode. Approval comes from the gate CLASS judgment + PM confirmation.
- `Plan complete` is NOT approval — it only triggers agent mode.
- Never expand scope beyond the listed CHANGES.
- HIGH always waits for explicit PM approval — no exceptions.
- Never do first and get approval later.
- Never treat post-execution ratification as pre-approval.
- If any item in one execution is HIGH, the whole is HIGH (no splitting to slip through).

---

## Mode Transition Protocol

### 三個 Mode 嘅邊界

| Mode | 職責 | 可以改 code？ |
|------|------|-------------|
| Ask | 快速問答、研究 | ❌ |
| Plan | 結構化計劃 | ❌ |
| Agent | 執行 | ✅ |

### Ask mode 額外硬規則（2026-09-18）

- ❌ 唔准 call ask-questions tool（`vscode_askQuestions`）—— Ask mode 只答問題，唔做訪問。
- ❌ 唔准將「user 唔喺度 / work autonomously」當成批准或同意 —— 呢個係 NON-ANSWER，要停低報告 blocker。
- ❌ 唔准改 VS Code permission / autonomy / sandboxing 設定（Default permissions / Sandboxing for terminal / Allow all / Autopilot）—— 呢啲係 user-owned，要先喺 `hotkey_tools.md` Permissions 註冊表登記。
- ❌ 唔准做任何 UI automation（vision detect / click / hotkey / screenshot action）。
- ✅ 真係 blocking 嘅問題 → 用純文字寫出嚟然後停，唔好用 question tool。

### 切換規則

1. **Ask → Plan**：當任務需要結構化計劃時
2. **Plan → Agent**：當 Plan 完成 + Gate pass + 明確批准時
3. **Agent → 完成**：執行完，返回 Plan mode 或 Ask mode

### 禁止事項

- ❌ 唔可以喺 Ask / Plan mode 改 code
- ❌ 唔可以喺 Ask / Plan mode 反覆研究同一件事（forloop）
- ❌ 唔可以自行由 Plan 轉 Agent（要明確批准）
- ❌ 唔可以喺 Plan mode 卡住超過 2 次同樣結論

### Forloop 防護

如果發現：
- 重複同樣結論超過 2 次
- 想改 code 但唔准改
- 卡喺同一個問題

→ **停低，報告 blocker，等指示**

---

## PLAN MODE

**Input:** task description, optional code snapshot reference.

**Actions:**

1. Read only relevant source files. Maximum **3** file reads. Do not explore unrelated code.
2. Draft step-by-step implementation plan.
3. Generate a **LOCKED QC CHECKLIST**. This checklist **CANNOT** be modified during later AGENT or informal “fixes.”
4. Define explicit allowlists: files permitted to edit; terminal commands allowed.
5. Define out-of-scope items clearly.
6. Do **not** declare success or failure.
7. **Stop.** Wait for human approval. Do **not** start implementation automatically.

**Human replies:**

- `APPROVE plan`
- `APPROVE plan with changes: …` (re-plan delta; new checklist version if checklist changes)
- `REJECT, re-plan`

**Required plan sections:**

1. Task boundary & scope (in / out)
2. Step-by-step implementation plan
3. LOCKED QC CHECKLIST (ID, criterion, method) — frozen
4. File allowlist
5. Terminal command allowlist
6. Forbidden actions
7. Stop / wait for approval

Template: `docs/snippets/template_plan.md`

---

## AGENT MODE

**Input:** approved plan (including frozen checklist + allowlists).

**Actions:**

1. Execute **strictly** per the approved plan. Do not change task scope. Do **not** alter the locked QC checklist.
2. Record every file edit, every terminal command, all exit codes, and concise diff snippets.
3. Write execution evidence to `qc_evidence/agent_log_{task_id}.md` **and** `qc_evidence/agent_log_{task_id}.json` (same facts; JSON has no `final_verdict`).
4. Return full execution log, modified file path list, command results.
5. **DO NOT** judge PASS / FAIL. **DO NOT** claim task SUCCESS.
6. **The unlock:** `qc_evidence/plan_{task_id}.md` must exist before the first write. If it is missing, write it from the approved plan **first** — a plan written after the code is a description, not a plan. `scripts/plan_gate.py` redirects a write that has no plan artifact and names this file.
7. **SPA rebuild rule:** If the task ran `npm run build` (or any SPA bundle rebuild) and restarted the helper, the user's already-open browser tab is now running the **old** bundle — agent-mode verification uses a separate fresh browser context and cannot see the user's tab. The agent MUST end its turn by telling the user: *"New build deployed — click the amber **New build — Reload** button in the header (or `Ctrl+Shift+R`)."* The SPA's build-detection button (compares loaded bundle vs `/api/system-status` `spa_build` every 5s) is the detection mechanism; the agent's notification is the trigger. Do **not** report a frontend change as "verified" based only on a fresh Playwright page while the user's tab may be stale.

**Forbidden in AGENT:**

- Edits outside file allowlist
- Commands outside terminal allowlist
- Checklist edits (add/remove/renumber/rewrite items)
- Explore/runSubagent unless user said `explore`
- Final verdict language for the overall task

Template: `docs/snippets/template_agent_execution_log.md`

---

## QC MODE

**Input:**

- `locked_qc_checklist` (frozen at plan approval)
- `agent_execution_log`

**Actions:**

1. Verify **every** checklist item one by one.
2. Run static checks as specified: file existence, content search, syntax compile, suite runs when required by an item.
3. Make an **independent** verdict using only factual evidence from the log and inspection.
4. Write `qc_evidence/qc_report_{task_id}.md` **and** `qc_evidence/qc_report_{task_id}.json` (same verdict and per-item results).

**Output:** QC report with per-item PASS / FAIL / SKIP + evidence, and **final_verdict** PASS or FAIL. List every failed item with evidence. JSON is for later backend ingestion into `task_run_logs` (ingest not part of this protocol doc’s runtime).

Template: `docs/snippets/template_qc_report.md`

---

## Relationship to no-plan-loop / no-explore

| Guard | Where enforced |
|-------|----------------|
| One plan description, then Do | PLAN produces the single plan; AGENT does not re-plan |
| No Explore thrash | All modes unless user types `explore` |
| ≤3 reads in plan gather | PLAN MODE rule |
| validate read-only | Ground rule 4 + product code discipline |
| Ask mode = answer only (no ask-questions, no autonomy changes) | Ask mode hard rules + `.github/copilot-instructions.md` |

Detail: `docs/plan_mo_no_explore_guard.md`

---

## Artifact paths (convention)

**Naming:** `task_id` is the stable slug for a run (same as former `task_slug`).  
**Rule:** Keep **MD** for humans. Emit **JSON twins** every run for machine/backend ingestion (e.g. future `task_run_logs`). JSON must not disagree with MD on checklist text, allowlists, command exit codes, or QC verdict.

| Artifact | MD path | JSON path | Who writes |
|----------|---------|-----------|------------|
| Plan + locked checklist | `qc_evidence/plan_{task_id}.md` (**required** — the unlock for writing code) | `qc_evidence/plan_{task_id}.json` (**required**) | PLAN end and/or AGENT start (must exist before AGENT writes anything) |
| Agent execution log | `qc_evidence/agent_log_{task_id}.md` (**required**) | `qc_evidence/agent_log_{task_id}.json` (**required**) | AGENT only |
| QC report | `qc_evidence/qc_report_{task_id}.md` (**required**) | `qc_evidence/qc_report_{task_id}.json` (**required**) | QC only |

### Required JSON shapes

**plan_{task_id}.json**

```json
{
  "task_id": "...",
  "scope": { "in_scope": [], "out_of_scope": [] },
  "step_list": [],
  "locked_qc_checklist": [{ "id": "QC-01", "criterion": "...", "method": "..." }],
  "file_allowlist": [{ "path": "...", "action": "create|edit" }],
  "allowed_commands": [],
  "forbidden_actions": []
}
```

**agent_log_{task_id}.json** (align fields for future `task_run_logs`; **no** task-level `final_verdict`)

```json
{
  "task_id": "...",
  "files_modified": [{ "path": "...", "action": "create|edit|delete", "notes": "..." }],
  "commands": [{ "cmd": "...", "cwd": "...", "exit_code": 0, "output_snippet": "..." }],
  "diff_snippets": [{ "path": "...", "summary": "..." }],
  "notes": []
}
```

**qc_report_{task_id}.json**

```json
{
  "task_id": "...",
  "checklist_items": [{ "id": "QC-01", "result": "PASS|FAIL|SKIP", "evidence": "..." }],
  "final_verdict": "PASS|FAIL"
}
```

Templates: `docs/snippets/template_plan.md`, `template_agent_execution_log.md`, `template_qc_report.md`.

---

## Product work under this protocol

1. Human states feature task description.  
2. PLAN MODE → freeze checklist → pause; prefer writing `plan_{task_id}.json`.  
3. Human approves.  
4. AGENT MODE → allowlisted edits + `agent_log` MD+JSON (+ ensure plan JSON exists).  
5. QC MODE → `qc_report` MD+JSON verdict.  

Ontology/TDD product files are **not** modified by protocol-doc tasks themselves; only by a **separate** approved feature plan.  
**Out of protocol runtime scope:** DB schema changes and ingest CLI into `task_run_logs` (backend follows separately).
