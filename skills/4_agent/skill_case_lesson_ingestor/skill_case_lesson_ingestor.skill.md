---
task_id: SKILL.CASE.LESSON.INGESTOR
display_task_id: SKILL.CASE.LESSON.INGESTOR
name: skill_case_lesson_ingestor
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: 自動案例學習與教訓提取Skill（Skill Learning Center 執行單元）
reason: Case study on ask<->confirm failures; emits lessons + candidate prompt versions
artifacts:
  - skill_case_lesson_ingestor.skill.md
schema: case_study_report
---
# Skill: skill_case_lesson_ingestor

Catalog: agent > general

自動案例學習與教訓提取Skill。
讀取失敗記錄、ask<>confirm mismatch案例，執行case study分析根因。
查閱GitHub已驗證嘅領域教訓(proofed lessons)。
輸出候選prompt改良方案或新增多維SSOT維度。
候選版本預設只存入Skill Learning Center，唔直接覆蓋正式Skill Library。

入參：skill_id、最近N條失敗記錄。
輸出：case study報告 + 候選改良清單 + 建議測試用gold case。

## Hard Boundary Rules

1. 候選版本只可以係 `skill_prompt_ssot` 嘅 `status='draft'`，永遠唔可以直接覆蓋 `active` 版本。
2. 所有候選改良必須跑短streak驗證（`/api/learning/candidates/test`），`pass_gate=1` 先可以 merge 入正式 Skill Library。
3. 送俾 LLM 嘅內容只係分組 summary + 每組 ≤5 sample，唔好 dump raw case 入 prompt。
4. 只讀 `skill_prompt_inference` / `skill_mismatch_log`，唔可以改 `audit_trace` 或任何生產表。

## Execution (API)

- `POST /api/learning/ingest` body `{"skill_key": "...", "limit": 50}`
  → 讀最近 is_wrong=1 推論 + to_review mismatch → 分組 → 本地 7B case study
  → 寫 `skill_lesson`（source_type='self_fail', status='draft'）
  → 如有 candidate_prompt → 建 draft 版本 `cand_{timestamp}`
- `POST /api/learning/candidates/test` body `{"skill_key","version_label","streak":20}`
- `POST /api/learning/candidates/merge` body `{"skill_key","version_label"}`
  （guard：該版本最新 test_run 必須 pass_gate=1）

## Backend

`skill_learning.run_case_study()` — Ollama `qwen2.5:7b-instruct` (temp 0.0)。
