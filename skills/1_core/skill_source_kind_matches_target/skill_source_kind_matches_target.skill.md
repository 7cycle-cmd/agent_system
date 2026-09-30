---
task_id: SKILL.SOURCE.KIND.MATCHES.TARGET
display_task_id: SKILL.SOURCE.KIND.MATCHES.TARGET
name: skill_source_kind_matches_target
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 來源 KIND 同目標 register KIND 唔一致就拒收 — 格式即係閘，文字即係解釋
reason: 2026-09-25 實測 discover() 讀 KIND A 寫 KIND B，58 個 key 永遠寫唔入
artifacts:
  - skill_source_kind_matches_target.skill.md
schema: "result_yes_no"
---

# Skill: skill_source_kind_matches_target

**Goal:** 一個 scope 嘅 **SOURCE KIND** 同佢要寫入嘅 **TARGET register KIND** 唔一致，
就喺 **discover() 之前**拒收，並且講清楚 **why / evidence / tutorial**。

Package path: `skills/1_core/skill_source_kind_matches_target/`
Gate: `register_fill.assert_kind_match()`（格式即閘）
Proof: `_proof_discovery_kind_match.py`
相關：`skills/1_core/citation_discipline/`（同一個「寫入點拒收」嘅病）、
`skills/1_core/env_task_proof/`（先證明，唔好假設）

**來源：** 用戶 2026-09-25 嘅修正：

> 「skill 教 become hard gate by writing format, and skill can help to have the
> explain easy to explain to worker why we reject and tutorial them」

## 為什麼要有呢個 skill

`register_fill.discover()` 讀一個 KIND A 嘅表，寫一個 KIND B 嘅 register，
**冇檢查兩者係唔係同一種嘢**。

| scope | 讀 | KIND A | 寫 | KIND B |
|---|---|---|---|---|
| `chat` | `chat_main` | 一個 **CHAT** | `chat_register` | 一個 **PAIRING** |
| `purpose_route` | `identity_register` | 一個 **IDENTITY** | `purpose_route_register` | 一個 **ROUTE** |

實測：呢兩個 scope 產生 56 + 2 = **58 個 key，永遠寫唔入**。
下游嘅拒收係**對嘅**；錯嘅係 **discovery**。

## 呢個 skill 嘅格式就係閘

**唔係**「教」一個 worker 要小心，**係**將 KIND 寫成 **data**，然後由一個
**唔問 LLM** 嘅 validator 執行。同 `skill_contract_store` 一樣：

> "Hard validation against skill_contract_field. **No LLM judgement.**
> Rules (all hard — reject, never warn-and-continue)."

```python
# 格式（data）—— 每個 phase 都要宣告
{"phase_key": "chat", "source_kind": "chat", "target_kind": "pairing", ...}

# 閘（唔問 LLM）—— 讀 data，唔判斷
assert_kind_match(phase)   # 唔一致 → KindMismatch
```

## 拒收要帶 why / evidence / tutorial

一個只講 **WHAT** 失敗嘅拒收，教唔到嘢。所以拒收帶三樣：

```
REFUSED: discover('chat') KIND MISMATCH
  source_kind : chat
  target_kind : pairing
  why         : a CHAT is not a PAIRING. `chat_main` records a conversation;
                `chat_register` records a TICKET paired with a chat. A chat
                carries no ticket, so no pairing can be derived from one.
  evidence    : chat_register_store.py:1
  tutorial    : to fill `chat_register`, supply a REAL `ticket_id` and a REAL
                `chat_id` through `chat_register_store.create_chat_register`.
```

| 欄位 | 係乜 | 邊度嚟 |
|---|---|---|
| `why` | 點解呢對 KIND 唔可以 | `_KIND_MISMATCH_WHY`（實測過） |
| `evidence` | 邊句 code 證明 | `path:line` |
| `tutorial` | 正確做法係點 | 下一步嘅具體動作 |

## 唔一致 ≠ 唔同

**唔可以**用 `source_kind == target_kind` 做 pass 條件。

實測：**11 個 phase 全部**兩邊都唔同 —— 一個 `table` 產生一個 `field`，
一個 `file` 產生一個 `function`，一個 `skill` 產生一個 `component`。
用相等做條件會拒收全部 11 個，即係一個「乜都拒」嘅閘，等於冇閘。

真正嘅問題係：**SOURCE 供唔供得起 TARGET？** 呢個係**實測**出嚟嘅性質，
唔係名嘅比較。所以唔一致嘅組合**明確宣告**喺 `_KIND_MISMATCH_WHY`，
每個都帶住證明佢嘅量度。**冇宣告嘅組合就通過。**

## 拒收喺 SOURCE，唔係喺 WRITE

| 喺邊拒收 | 講到乜 | 學到乜 |
|---|---|---|
| WRITE（下游） | `PARENT_NOT_FOUND` | 症狀 |
| **SOURCE（呢個 skill）** | **KIND MISMATCH + why** | **真正嘅缺陷** |

## 唔准做

1. 改 `chat_main` / `chat_register` / `purpose_route_register`（佢哋係**對嘅**）。
2. 用 `source_kind == target_kind` 做 pass 條件（會拒收全部）。
3. 拒收只講 WHAT，唔講 WHY。
4. 為咗令閘通過而改 KIND 名（改名唔會令 source 供得起 target）。
