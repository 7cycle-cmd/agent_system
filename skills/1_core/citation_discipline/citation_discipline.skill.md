---
task_id: SKILL.CITATION.DISCIPLINE
display_task_id: SKILL.CITATION.DISCIPLINE
name: citation_discipline
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 冇引用即丟棄 — 發現必須帶 path:line 或指令，寫入時就拒收
reason: 2026-09-20 發現先寫落 markdown 才入 DB，即係 DB 冇可查詢嘅引用
artifacts:
  - citation_discipline.skill.md
schema: "result_yes_no"
---

# Skill: citation_discipline

**Goal:** 冇引用嘅發現**被丟棄**，唔係降級。

Package path: `skills/1_core/citation_discipline/`
Executable: `citation_discipline.py`（規則 + 寫入閘）
Proof: `_proof_problem_citation.py`
相關：`skills/1_core/systematic_debugging/`（`keep_finding()` 係同一條規則）、
`skills/5_qa/evidence_classify/`（唔確定唔算 PASS）

**來源：** `obra/superpowers`（MIT）之 `diagnosing-superpowers`：

> 「每個發現都要引用 `path:line`。冇引用，就冇發現。」

## 為什麼要有呢個 skill

`systematic_debug.py:keep_finding()` 已經有**判斷式**。
但 2026-09-20 嘅實測顯示：**判斷式冇喺寫入點被叫。**

```
我引用得好多（好），但先寫落 markdown 才入 DB
→ 即係發現冇可查詢嘅引用
```

**一個冇人喺寫入點叫嘅判斷式，等於冇。**
同 `env_task_proof.assert_rect_ok()` 要喺 click 點 raise 係同一個病。

## 乜嘢算引用

| 形式 | 例子 | 算唔算 |
|---|---|---|
| `path:line` | `f_perm_click.py:412` | ✅ |
| `path:line-line` | `auto_rect_audit.py:181-203` | ✅ |
| 指令 | `python _proof_stop_rule.py` | ✅ |
| 記憶 | 「我記得」 | ❌ |
| 權威 | 「文件話」 | ❌ |
| 空 | `""` | ❌ |

## 丟棄，唔係降級

```python
kept, dropped = partition(findings)
```

冇引用嘅發現**唔會**變成「低信心發現」— 佢**消失**。
降級會令冇引用嘅發現繼續存在，只係換個標籤。

## 閘（Gate）

```python
assert_cited(finding)   # 冇引用 → raise UncitedFinding
```

喺**寫入點**叫，唔係喺讀取點。呢個就係報告 §2 講嘅缺口。

## 紅旗

- 「我親眼睇到」→ 邊個 file 邊行？
- 「之前傾過」→ 邊個 transcript？
- 「好明顯」→ 明顯唔係引用
- 「先寫落 markdown，之後補引用」→ **之後永遠唔會補**

## 引用唔可以嚟自「父層」（實測 2026-09-21）

一個複合 key 嘅**父層名稱**唔算係子層嘅證據。

```python
# 錯：tokenise 完整 key
key_tokens("mouse_spot_helper.core")   -> {'mouse','spot','core'}
# 所以 /api/mouse 用 token 'mouse' match 到 'core'
#   —— 但 'mouse' 嚟自 MODULE 前綴，唔係 capability 名

# 啱：剝走父層再 tokenise
key_tokens("mouse_spot_helper.core")   -> set()
```

**為何係同一個病：** 父層已經用嚟**收窄**候選範圍，再攞父層個名做 match token，
等於**將父層重複計算成證據**。呢個係 v1 generic-token bug（`api` match 全部）
換咗個字——`mouse` 對嗰個 module match 全部。

**規則：** 引用要指向**被指嘅東西本身**，唔可以指向佢嘅容器。
（`binding_proposals.py:139`）

## 測試（Tests）

1. **`_proof_problem_citation.py`** — 涵蓋規則、閘、變異。
2. **變異測試** — 令 `is_citation` 接受任何非空字串，測試**必須失敗**。
3. **實測** — 「I remember」必須被拒。
4. **父層污染測試**（`_proof_binding_proposals.py` §2b）——
   `key_tokens("mouse_spot_helper.core") == set()`，而且 `/api/mouse` 唔可以
   match 到 `core`。

## 合約（Contract）

- `contract_id` : `SKILL.CITATION.DISCIPLINE`
- `taxonomy_path`: `module/task_center`（已驗證 active）
- TDD cases : 3 `pass` + 3 `hard_fail`
