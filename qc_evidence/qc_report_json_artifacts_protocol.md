# QC report — json_artifacts_protocol

**Mode:** QC  
**Checklist ID (frozen):** `qc.json_artifacts_protocol.v1`  
**Agent log path:** `qc_evidence/agent_log_json_artifacts_protocol.md`  
**JSON twin:** `qc_evidence/qc_report_json_artifacts_protocol.json`  
**Started at:** 2026-09-16T (QC session)  
**Finished at:** 2026-09-16T (QC session)

## Checklist results

| ID | result | evidence |
|----|--------|----------|
| QC-01 | PASS | SSOT L137–139 / L143–169: `plan_{task_id}.json`, `agent_log_{task_id}.json`, `qc_report_{task_id}.json` documented |
| QC-02 | PASS | SSOT L133: Keep **MD** for humans; Emit **JSON twins** every run (additional machine-readable) |
| QC-03 | PASS | Who writes table: plan PLAN/AGENT; agent_log AGENT only; qc_report QC only (L137–139); AGENT L80–83; QC L109 |
| QC-04 | PASS | `template_plan.md` L58+ keys: task_id, scope, step_list, locked_qc_checklist, file_allowlist, allowed_commands, forbidden_actions |
| QC-05 | PASS | `template_agent_execution_log.md` L35+: task_id, files_modified, commands, diff_snippets, notes |
| QC-06 | PASS | Agent template L8 and L38: do not set final_verdict / SUCCESS/FAIL in JSON |
| QC-07 | PASS | `template_qc_report.md` L31+: task_id, checklist_items {id,result,evidence}, final_verdict |
| QC-08 | PASS | All three templates have Machine-readable twin section + JSON skeleton |
| QC-09 | PASS | instructions L6 NO PLAN LOOP; L45 NO EXPLORE LOOP |
| QC-10 | PASS | instructions L70 plan_ide_execution_worker.md; L72 JSON twins paths |
| QC-11 | PASS | python allowlist diff: OUTSIDE_ALLOW [] (agent files_modified ⊆ plan file_allowlist) |
| QC-12 | PASS | No DB/ingest files in agent modified list; protocol notes ingest out of band |
| QC-13 | PASS | agent_log notes: no skill_task_validate.py; not in files_modified |
| QC-14 | PASS | plan_json_artifacts_protocol.json exists; PLAN_MISSING []; locked_qc_checklist n=20 |
| QC-15 | PASS | agent_log_json_artifacts_protocol.json exists; ALOG_MISSING [] |
| QC-16 | PASS | ALOG_final_verdict __ABSENT__ |
| QC-17 | PASS | This QC creates qc_report_json_artifacts_protocol.json with checklist_items + final_verdict |
| QC-18 | PASS | agent_log_json_artifacts_protocol.md exists (Test-Path True); JSON twin alongside |
| QC-19 | PASS | agent log commands table has exit_code; JSON commands exit [null, 0, 0] for 3 cmds |
| QC-20 | PASS | This report marks QC-01…QC-19 with PASS/FAIL/SKIP + evidence |

## Failed items (detail)

- (none)

## Evidence commands run in QC

| command | exit_code |
|---------|-----------|
| Test-Path × protocol/template/plan/agent artifacts | 0 (all True) |
| Select-String SSOT JSON paths / Who writes | 0 |
| Select-String templates Machine-readable twin + keys | 0 |
| Select-String copilot-instructions guards + pointer | 0 |
| python json.load plan+agent_log key/allowlist checks | 0 |

## final_verdict

**PASS**

(Only QC MODE sets this field.)
