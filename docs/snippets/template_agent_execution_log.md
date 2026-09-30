# AGENT execution log — {{task_slug}}

**Mode:** AGENT  
**Approved plan / checklist ID:** `{{checklist_id}}`  
**Started at:** {{iso8601}}  
**Finished at:** {{iso8601}}

> Factual record only. **Do not** set `final_verdict` or claim task SUCCESS/FAIL.

## Files modified

| path | action | notes |
|------|--------|-------|
| `…` | create \| edit \| delete | … |

## Commands

| # | command | cwd | exit_code | output_snippet_or_path |
|---|---------|-----|-----------|------------------------|
| 1 | `…` | `…` | 0 | … |

## Diff snippets (concise)

### `path/to/file`

```text
- old
+ new
```

## Notes (factual only)

- …

## Machine-readable twin (JSON)

**Required every AGENT run:** `qc_evidence/agent_log_{{task_id}}.json`  
Same facts as this MD. Fields align for future `task_run_logs` ingestion. **Do not** include task-level `final_verdict` or SUCCESS/FAIL.

**Required keys:**

- `task_id`
- `files_modified` — array of `{path, action, notes}`
- `commands` — array of `{cmd, cwd, exit_code, output_snippet}`
- `diff_snippets` — array of `{path, summary}`
- `notes` — array of strings or a string list

**Skeleton:**

```json
{
  "task_id": "{{task_id}}",
  "files_modified": [{ "path": "...", "action": "edit", "notes": "..." }],
  "commands": [
    {
      "cmd": "Test-Path docs/plan_ide_execution_worker.md",
      "cwd": "C:\\\\projects\\\\agent_system",
      "exit_code": 0,
      "output_snippet": "True"
    }
  ],
  "diff_snippets": [{ "path": "...", "summary": "..." }],
  "notes": ["Factual only; no final_verdict"]
}
```
