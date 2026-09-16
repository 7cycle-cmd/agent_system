# PLAN — resync_ingest_task_log_cli_db

**Mode:** PLAN (frozen at Start implementation)  
**task_id:** `resync_ingest_task_log_cli_db`  
**checklist_id:** `qc.resync_ingest_task_log_cli_db.v1`  
**Delta applied:** **Option B** (marker remove + single-row DELETE + existing `--scan`)  
Reason: plain `--scan` skips `already_in_marker` and cannot UPDATE; script edit (Option A) out of original “do not change ingest_task_log.py” constraint.

## Scope

Re-import `ingest_task_log_cli` into `task_run_logs` from latest qc_report (PASS). No plan/agent_log edits. No `ingest_task_log.py` / `render_task_view.py` edits.

## Steps

1. Confirm qc_report final_verdict PASS  
2. Remove only `ingest_task_log_cli` from marker  
3. DELETE only that PK row  
4. `ingest_task_log.py --scan`  
5. Verify DB final_verdict PASS; other prior ids not lost  
6. agent_log; stop for QC
