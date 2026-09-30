# QC report — {{task_slug}}

**Mode:** QC  
**Checklist ID (frozen):** `{{checklist_id}}`  
**Agent log path:** `qc_evidence/agent_log_{{task_slug}}.md`  
**Started at:** {{iso8601}}  
**Finished at:** {{iso8601}}

## Checklist results

| ID | result | evidence |
|----|--------|----------|
| QC-01 | PASS \| FAIL \| SKIP | … |

## Failed items (detail)

- **QC-xx:** …

## Evidence commands run in QC

| command | exit_code |
|---------|-----------|
| `…` | 0 |

## final_verdict

**PASS** or **FAIL**

(Only QC MODE sets this field.)

## Machine-readable twin (JSON)

**Required every QC run:** `qc_evidence/qc_report_{{task_id}}.json`  
Must match MD per-item results and `final_verdict`. For backend ingestion (e.g. future `task_run_logs`); ingest CLI is out of band.

**Required keys:**

- `task_id`
- `checklist_items` — array of `{id, result, evidence}` (`result`: PASS | FAIL | SKIP)
- `final_verdict` — `PASS` | `FAIL`

**Skeleton:**

```json
{
  "task_id": "{{task_id}}",
  "checklist_items": [
    { "id": "QC-01", "result": "PASS", "evidence": "..." }
  ],
  "final_verdict": "PASS"
}
```
