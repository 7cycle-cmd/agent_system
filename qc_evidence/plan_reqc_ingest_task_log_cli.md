# PLAN — reqc_ingest_task_log_cli

**Mode:** PLAN (frozen)  
**task_id:** `reqc_ingest_task_log_cli`  
**checklist_id:** `qc.reqc_ingest_task_log_cli.v1`  
**parent_checklist_id:** `qc.ingest_task_log_cli.v1`

## Scope

Re-collect evidence for parent QC-01…QC-20. QC MODE overwrites parent `qc_report_ingest_task_log_cli.*` only. Do not modify parent plan/agent_log, scripts, or hand-edit marker/DB.

## Step list

1. Hash parent plan/agent_log pre
2. Run parent verification commands
3. Hash post (must match)
4. Write meta agent_log with raw observations
5. Stop for human QC (parent report refresh)
