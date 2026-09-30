---
task_id: SKILL.INCIDENT.POSTMORTEM
display_task_id: SKILL.INCIDENT.POSTMORTEM
name: incident_postmortem
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 事故觸發條件事前定義 + 未經覆核嘅事後報告唔算存在
reason: 2026-09-20 教訓係想起才寫，冇任何觸發條件話「呢個算係事故」
artifacts:
  - incident_postmortem.skill.md
schema: "result_yes_no"
---

# Skill: incident_postmortem

**Goal:** 觸發條件**事前**定義；**未經覆核嘅事後報告等於從來冇存在過。**

Package path: `skills/1_core/incident_postmortem/`
Executable: `incident_postmortem.py`（觸發清單 + 覆核閘）
Proof: `_proof_incident_waiting.py`

**來源：** Google SRE Book 第 15 章（postmortem culture）。
可轉移嘅部分係**觸發清單**同**覆核閘**；其餘係文化，裝唔到。

## 為什麼要有呢個 skill

實測缺口：**教訓係想起才寫。** 冇任何嘢話「呢個算係事故」，
所以「算唔算事故」變成事後判斷 — 而事後判斷永遠可以選擇排除自己。

## 觸發清單（事前定義，六條）

| key | 觸發條件 |
|---|---|
| `repeat_same_class` | 同一 session 內同類錯誤重複（≥2） |
| `fix_attempts_ge_3` | 同一問題修復失敗 ≥3 次 |
| `architecture_change_required` | 有效修復需要**架構**改變，唔係參數改變 |
| `false_pass_recorded` | 記錄咗 PASS 但後來證明錯 |
| `evidence_destroyed` | 有閘丟棄或覆寫咗真實 provenance |
| `human_asked_after_3` | 失敗 ≥3 次之後問人類 |

**冇「輕微事故」逃生門。** 任何一條 fire → 需要事後報告。
呢個就係事前清單要移除嘅判斷空間。

## 閘（Gate）

```python
assert_reviewed(postmortem, incident)   # → raise PostmortemRequired
may_close(incident, postmortem)         # → bool，唔 raise
```

拒絕條件：
- 冇事後報告
- `state != 'reviewed'`（draft 唔算）
- 冇記錄觸發條件
- 冇行動項目
- 行動項目冇負責人

## 紅旗

- 「呢個唔算事故」→ 睇清單，唔係睇感覺
- 「先寫 draft，之後覆核」→ draft 唔算存在
- 「行動項目之後再分配」→ 冇負責人嘅行動係願望
- 「我記得嗰次…」→ 冇引用即丟棄（見 `citation_discipline`）

## 測試（Tests）

1. **`_proof_incident_waiting.py`** — 27 個檢查。
2. **實測重播** — 2026-09-20 序列必須 fire 四條觸發條件。
3. **變異測試** — 移除保護，測試必須失敗。

## 合約（Contract）

- `contract_id` : `SKILL.INCIDENT.POSTMORTEM`
- `taxonomy_path`: `module/task_center`（已驗證 active）
- TDD cases : 3 `pass` + 3 `hard_fail`
