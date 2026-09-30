---
task_id: SKILL.LESSON.GITHUB.IMPORTER
display_task_id: SKILL.LESSON.GITHUB.IMPORTER
name: skill_lesson_github_importer
final_verdict: incomplete
ingested: no
modified_files: []
qc_summary: 從GitHub開源Agent/評估框架匯入經驗教訓（Skill Learning Center 執行單元）
reason: Extract proofed design rules / anti-patterns from GitHub docs into skill_lesson
artifacts:
  - skill_lesson_github_importer.skill.md
schema: structured_lesson_list
---
# Skill: skill_lesson_github_importer

Catalog: agent > general

從GitHub開源Agent/評估框架匯入經驗教訓。
提取已驗證(proofed)設計規則、常見陷阱、反模式，轉為結構化lesson存入Skill Learning Center。
只匯入知識，唔匯入可執行代碼，保持同現有SSOT兼容。

入參：github repo url / 文檔片段、目標skill_id。
輸出：結構化lesson列表，存入skill_lesson表。

## Hard Boundary Rules

1. 只提取知識（規則/維度/反模式），唔提取可執行代碼。
2. 每條 lesson 入表後 status='draft'；要應用必須經候選版本 + 短streak驗證（pass_gate=1）先可以 merged。
3. 唔可以直接改 `skill_prompt_ssot` 嘅 `active` 版本。
4. URL fetch 上限 20k 字元、30s timeout；失敗要回報 error，唔好靜默吞。

## Execution (API)

- `POST /api/learning/github-import` body
  `{"text_or_url": "...", "skill_key": "...", "is_url": false}`
  → 本地 7B 提取 JSON array `[{lesson_text, root_cause, suggested_fix}]`
  → 每條寫 `skill_lesson`（source_type='github_proofed_lesson', status='draft', source_ref=url/片段）

## Backend

`skill_learning.import_github_lessons()` — Ollama `qwen2.5:7b-instruct` (temp 0.0)。
