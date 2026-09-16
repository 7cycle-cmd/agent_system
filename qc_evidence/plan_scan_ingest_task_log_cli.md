# PLAN — scan_ingest_task_log_cli

**Mode:** PLAN (frozen at approval / Start implementation)  
**task_id:** `scan_ingest_task_log_cli`  
**checklist_id:** `qc.scan_ingest_task_log_cli.v1`

## Scope

Run `ingest_task_log.py --scan`; verify `ingest_task_log_cli` in marker and `task_run_logs`.

## In scope

- Execute venv Python `--scan` (and optional second scan)
- Verify `.ingested_tasks.json` and `agent.db` / `task_run_logs`
- Write plan + agent_log twins for this task_id only

## Out of scope

- Edits to `ingest_task_log.py`, templates, protocol SSOT, parent triple content
- Hand-editing marker or manual SQL insert
- AGENT PASS/FAIL

## Step list

1. Preflight parent JSON triple paths + `--list`
2. `ingest_task_log.py --scan`
3. Verify marker contains `ingest_task_log_cli`
4. Verify DB row count == 1 for that task_id
5. Optional second `--scan` (idempotency)
6. Write agent_log twins; stop for QC

## Locked checklist

See `qc.scan_ingest_task_log_cli.v1` (QC-01 … QC-13) in approved plan — frozen.

## Allowlist

- Indirect: marker, ingest_logs, agent.db via CLI only
- Direct: `qc_evidence/plan_scan_ingest_task_log_cli.*`, `qc_evidence/agent_log_scan_ingest_task_log_cli.*`
- QC only: `qc_evidence/qc_report_scan_ingest_task_log_cli.*`
