---
name: condition-based-waiting
description: "Use when: about to wait for something in UI automation or any async step — replacing a fixed sleep/time.sleep, waiting for an element, a menu, a page, or a process to become ready. Enforces waiting on a CONDITION with a timeout that fails LOUDLY, because a fixed sleep is wrong in both directions (too short = flaky, too long = slow) and — worst — expires silently and lets the next step run against a UI that never became ready. Also use when you see sleep() in automation code."
---

# Condition-Based Waiting (wait for a condition, not a clock)

**Goal:** 等**條件**，唔等**時間**。Timeout 要**響亮失敗**，唔准靜靜咁繼續。

Full skill: `skills/1_core/condition_based_waiting/condition_based_waiting.skill.md`
Executable: `condition_based_waiting.py`
Proof: `_proof_incident_waiting.py`

Source: `obra/superpowers` (MIT) — `condition-based-waiting` supporting technique.

## When to use

- 你準備寫 `time.sleep(...)` / 固定等待
- 等 UI 元素、選單、頁面、程序 ready
- 任何 async 步驟（build、signing、network、file）
- 你見到 flaky 失敗，而原因係「有時快有時慢」

## 固定 sleep 兩個方向都錯

| 方向 | 後果 |
|---|---|
| 太短 | 下一步喺 UI 未 ready 就執行 → **flaky 失敗** |
| 太長 | 每次跑都付最壞情況 → 慢，而且**掩蓋真實時間** |

**最重要嘅係第三個性質：** 固定 sleep 過期之後**靜靜咁繼續**，
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

## 為何要有呢個 skill（實測）

實測：`Ctrl+Alt+K` 之後 `time.sleep(1.4)`。

## Note on this file

This is a **pointer**, not a second copy. The canonical skill lives at
`skills/1_core/condition_based_waiting/` so it is visible to the Skill Library
scanner, has a contract and can accumulate a streak. Editing this file does not
change the enforced rule.
