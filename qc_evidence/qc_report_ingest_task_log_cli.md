# QC report — ingest_task_log_cli

**Mode:** QC (re-score)  
**Checklist ID (frozen):** `qc.ingest_task_log_cli.v1`  
**task_id:** `ingest_task_log_cli`  
**Re-QC via:** `reqc_ingest_task_log_cli` / `qc.reqc_ingest_task_log_cli.v1`  
**Started at:** 2026-09-16T (QC re-score)  
**Finished at:** 2026-09-16T (QC re-score)

## Checklist results

| ID | result | evidence |
|----|--------|----------|
| QC-01 | PASS | `Test-Path ingest_task_log.py` → True |
| QC-02 | PASS | `-h` exit 0; flags `--scan`, `--dry-run`, `--list` present |
| QC-03 | PASS | Script has `FILE_RE` / plan|agent_log|qc_report and `qc_evidence` |
| QC-04 | PASS | Uses `.ingested_tasks.json`; marker file exists; `--list` works |
| QC-05 | PASS | `qc_evidence/ingest_logs/` has `ingest_*.json` and `ingest.jsonl` |
| QC-06 | PASS | `CREATE TABLE IF NOT EXISTS task_run_logs` with required columns in script |
| QC-07 | PASS | No `docs/snippets/template_*.md` edits in this re-QC |
| QC-08 | PASS | `plan_ide_execution_worker.md` not modified this re-QC |
| QC-09 | PASS | init_db / db_schema / validate not in re-QC writes |
| QC-10 | PASS | `py_compile ingest_task_log.py` ok |
| QC-11 | PASS | `--list` exit 0 |
| QC-12 | PASS | `--scan --dry-run` exit 0; marker text unchanged by dry-run |
| QC-13 | PASS | `--scan` exit 0; `ingest_task_log_cli` in marker + `task_run_logs` (count=1); skip `already_in_marker` on rescan |
| QC-14 | PASS | Incomplete `fix_ingest_agent_log_artifacts` not in marker and not in `task_run_logs` |
| QC-15 | PASS | Rescan shows `already_in_marker` skips; no duplicate parent row (count remains 1) |
| QC-16 | PASS | `qc_evidence/plan_ingest_task_log_cli.json` exists; SHA256 unchanged vs AGENT pre-hash |
| QC-17 | PASS | `agent_log_ingest_task_log_cli.json` exists; keys task_id/files_modified/commands present; **`final_verdict` absent**; task_id `ingest_task_log_cli` |
| QC-18 | PASS | `agent_log_ingest_task_log_cli.md` exists (Test-Path True); SHA256 unchanged |
| QC-19 | PASS | This report scores QC-01 through QC-18 |
| QC-20 | PASS | `final_verdict` only on QC report artifacts (agent_log has none) |

## Failed items (detail)

- (none)

## Prior FAIL (superseded)

- Historical QC-17/QC-18 FAIL (missing agent_log) closed after restore + re-score.
- DB `task_run_logs.final_verdict` may still show legacy `FAIL` string from original ingest of the old FAIL report; checklist re-score does not rewrite DB (out of scope). Marker membership and row existence remain valid.

## Evidence commands run in QC

| command | result |
|---------|--------|
| path/hash checks plan+agent_log | exist; hashes match AGENT baseline |
| help / list / dry-run / scan | exit 0 |
| agent_log JSON key check | no final_verdict |
| sqlite COUNT parent | 1 |
| fix_* not in marker/db | True |

## final_verdict

**PASS**

(Only QC MODE sets this field. Re-score replaces prior FAIL report.)
