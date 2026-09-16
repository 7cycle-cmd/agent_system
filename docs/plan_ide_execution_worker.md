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
6. If `qc_evidence/plan_{task_id}.json` is missing, write it from the approved plan before finishing AGENT.

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

Detail: `docs/plan_mo_no_explore_guard.md`

---

## Artifact paths (convention)

**Naming:** `task_id` is the stable slug for a run (same as former `task_slug`).  
**Rule:** Keep **MD** for humans. Emit **JSON twins** every run for machine/backend ingestion (e.g. future `task_run_logs`). JSON must not disagree with MD on checklist text, allowlists, command exit codes, or QC verdict.

| Artifact | MD path | JSON path | Who writes |
|----------|---------|-----------|------------|
| Plan + locked checklist | `qc_evidence/plan_{task_id}.md` (optional) | `qc_evidence/plan_{task_id}.json` (**required**) | PLAN end and/or AGENT start (must exist before AGENT finishes) |
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
