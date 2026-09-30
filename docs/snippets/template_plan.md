# PLAN MODE — {{task_slug}}

**Mode:** PLAN (read-only)  
**IDE:** vscode_autopilot  
**Checklist ID:** `{{checklist_id}}`  
**Status:** DRAFT → freeze on human `APPROVE plan`

## 1. Task Boundary & Scope

### In scope
- …

### Out of scope
- …

## 2. Step-by-step implementation plan

1. …
2. …

## 3. LOCKED QC CHECKLIST

> **FROZEN after approval.** AGENT MUST NOT add, remove, renumber, or rewrite items.

| ID | Verification criterion | Verification method |
|----|------------------------|---------------------|
| QC-01 | … | … |

**Freeze token:** `{{checklist_id}}` = items as approved.

## 4. Allowed File Allowlist

> **ALWAYS include the AGENT deliverable.** `scripts/plan_gate.py:832` names
> `qc_evidence/agent_log_<task_id>.md` (+ `.json` twin) as what AGENT records.
> `is_plan_artifact()` (`scripts/plan_gate.py:516`) matches ONLY `plan_*.md`, so an
> `agent_log_*.md` is **NOT** always writable — it MUST be listed here or the gate
> denies it. MEASURED 2026-09-26: **158 of 182** plans carried an `## OUTCOME` and
> had no agent log, because this table did not name one.

| Path | Action |
|------|--------|
| `path/to/file` | create \| edit |
| `qc_evidence/plan_{{task_id}}.md` | create |
| `qc_evidence/plan_{{task_id}}.json` | create |
| `qc_evidence/agent_log_{{task_id}}.md` | create |
| `qc_evidence/agent_log_{{task_id}}.json` | create |

## 5. Allowed Terminal Command List

```text
# list exact commands only
```

## 6. Forbidden actions list

1. …
2. …

## 7. Stop. Wait for human approval.

```text
APPROVE plan
APPROVE plan with changes: <delta>
REJECT, re-plan
```

No code changes in PLAN MODE.

## 8. Machine-readable twin (JSON)

**Required every run:** `qc_evidence/plan_{{task_id}}.json`  
MD (this file / chat plan) stays the human presentation. JSON is an extra copy for backend API ingestion (e.g. future `task_run_logs`). Do not disagree with the locked checklist or allowlists.

**Required keys:**

- `task_id` (string)
- `scope` (object: `in_scope`, `out_of_scope`)
- `step_list` (array of strings or step objects)
- `locked_qc_checklist` (array of `{id, criterion, method}`)
- `file_allowlist` (array of `{path, action}`)
- `allowed_commands` (array of strings)
- `forbidden_actions` (array of strings)

**Skeleton:**

```json
{
  "task_id": "{{task_id}}",
  "scope": { "in_scope": [], "out_of_scope": [] },
  "step_list": [],
  "locked_qc_checklist": [
    { "id": "QC-01", "criterion": "...", "method": "..." }
  ],
  "file_allowlist": [{ "path": "...", "action": "edit" }],
  "allowed_commands": ["Test-Path <path>"],
  "forbidden_actions": ["Edit paths outside allowlist"]
}
```
