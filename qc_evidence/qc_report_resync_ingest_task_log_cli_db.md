# QC report — resync_ingest_task_log_cli_db

**Mode:** QC  
**Checklist ID (frozen):** `qc.resync_ingest_task_log_cli_db.v1`  
**task_id:** `resync_ingest_task_log_cli_db`  
**Delta:** Option B  
**Started at:** 2026-09-16T (QC session)  
**Finished at:** 2026-09-16T (QC session)

## Checklist results

| ID | result | evidence |
|----|--------|----------|
| QC-01 | PASS | `qc_report_ingest_task_log_cli.json` final_verdict **PASS** |
| QC-02 | PASS | Option B executed: marker remove + DELETE one row + `ingest_task_log.py --scan` (agent_log commands) |
| QC-03 | PASS | `--scan` exit 0 (agent_log / prior transcript) |
| QC-04 | PASS | `COUNT(*)` for `ingest_task_log_cli` = **1** |
| QC-05 | PASS | row `final_verdict` = **PASS** (`ide_execution_worker` / `1`) |
| QC-06 | PASS | marker `task_ids` contains `ingest_task_log_cli` |
| QC-07 | PASS | prior other ids retained: json_artifacts_protocol, render_task_view, scan_ingest_task_log_cli present; no LOST set |
| QC-08 | PASS | plan_ingest SHA256 matches baseline |
| QC-09 | PASS | agent_log_ingest MD+JSON SHA256 match baseline |
| QC-10 | PASS | delta ≠ A; `ingest_task_log.py` has no `--rescan`; git shows `??` only, no content edit this task for upsert |
| QC-11 | PASS | `render_task_view.py` not modified this task |
| QC-12 | PASS | no hand-edit of foreign qc bodies beyond allowlisted resync meta artifacts |
| QC-13 | PASS | `agent_log_resync_ingest_task_log_cli_db.*` exist; no `final_verdict` in agent JSON |
| QC-14 | PASS | this QC report scores QC-01…QC-13; final_verdict only here |

## Failed items (detail)

- (none)

## Notes

- Additive side effect of same `--scan`: `reqc_ingest_task_log_cli` also present in marker/DB (not a deletion of other rows).
- Option B used because default scan cannot UPDATE legacy FAIL rows.

## final_verdict

**PASS**

(Only QC MODE sets this field.)
