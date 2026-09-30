---
task_id: SKILL.CONDITION.WAITING
display_task_id: SKILL.CONDITION.WAITING
name: condition_based_waiting
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 等條件，唔等時間 — 固定 sleep 兩個方向都錯，timeout 要響亮失敗
reason: 2026-09-20 Ctrl+Alt+K 之後 time.sleep(1.4)，固定 sleep 正正係要取代嘅嘢
artifacts:
  - condition_based_waiting.skill.md
schema: "result_yes_no"
---

# Skill: condition_based_waiting

**Goal:** 等**條件**，唔等**時間**。Timeout 要**響亮失敗**，唔准靜靜咁繼續。

Package path: `skills/1_core/condition_based_waiting/`
Executable: `condition_based_waiting.py`
Proof: `_proof_incident_waiting.py`

**來源：** `obra/superpowers`（MIT）之 `condition-based-waiting` 輔助技巧。

## 為什麼要有呢個 skill

實測：`Ctrl+Alt+K` 之後 `time.sleep(1.4)`。

固定 sleep **兩個方向都錯**：

| 方向 | 後果 |
|---|---|
| 太短 | 下一步喺 UI 未 ready 就執行 → **flaky 失敗** |
| 太長 | 每次跑都付最壞情況 → 慢，而且**掩蓋真實時間** |

**最重要嘅係第二個性質：** 固定 sleep 過期之後**靜靜咁繼續**，
令下一步喺一個永遠冇 ready 嘅 UI 上執行 —
呢個就係「flaky 失敗」變成「無法解釋嘅失敗」嘅方式。

## 用法

```python
from condition_based_waiting import wait_until, ConditionTimeout

wait_until(lambda: picker_is_open(), timeout=3.0,
           description="permission picker open")
# 條件一真就即刻返回；timeout 就 raise ConditionTimeout
```

**唔准**用 `wait_until_or_false` 除非真係有 fallback 路徑 —
boolean 好容易被忽略，而忽略佢就係呢個 module 要防止嘅靜默 timeout。

## 紅旗

- `time.sleep(1.4)` 之後假設 UI ready
- 「加長個 sleep 就唔 flaky」→ 掩蓋問題，唔係解決
- 「用 try/except 包住個 timeout 繼續行」→ 靜默 timeout
- 「我知大概要等幾耐」→ 量度佢，然後用 2×

## 測試（Tests）

1. **`_proof_incident_waiting.py`** — 條件一真即返回、timeout 響亮失敗、
   raising predicate 當「未真」。
2. **變異測試** — 令 `wait_until` 永遠返回 0.0，測試必須失敗。
3. **假時鐘** — 用 injectable clock，唔靠真實時間。

## 合約（Contract）

- `contract_id` : `SKILL.CONDITION.WAITING`
- `taxonomy_path`: `module/task_center`（已驗證 active）
- TDD cases : 3 `pass` + 3 `hard_fail`
