---
task_id: SKILL.PROBLEM.STATEMENT
display_task_id: SKILL.PROBLEM.STATEMENT
name: problem_statement
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 抱怨同問題唔係問題陳述 — 要有目標、預期、實際、可觀察，先准分析
reason: 2026-09-20 由「can run real case now?」開始分析，即係分析一個冇人講過嘅問題
artifacts:
  - problem_statement.skill.md
schema: "result_yes_no"
---

# Skill: problem_statement

**Goal:** 未有一個**可分析嘅問題陳述**之前，**唔准開始分析**。

Package path: `skills/1_core/problem_statement/`
Executable: `problem_statement.py`（規則 + 閘）
Proof: `_proof_problem_citation.py`
相關：`skills/1_core/systematic_debugging/`（陳述之後才入 Phase 1）、
`skills/1_core/citation_discipline/`（發現要有引用）

**來源：** `obra/superpowers`（MIT，288.8k stars）之 `diagnosing-superpowers`
問題收納部分。**採納規則、唔複製文字。**

## 為什麼要有呢個 skill

`systematic_debugging` 擋「憑症狀改四次方法」。
但佢假設**已經有一個問題**。實測嘅失敗係：**根本冇問題陳述。**

```
「can run real case now?」   ← 呢個係問題，唔係問題陳述
「it's too slow」            ← 呢個係抱怨，唔係問題陳述
```

分析一個抱怨，會得出一個**冇人講過嘅問題**嘅修復。呢個就係 2026-09-20
rect 失敗嘅起點。

## 三類輸入（必須先分類）

| 類別 | 例子 | 可否分析 |
|---|---|---|
| **抱怨** | 「太慢」、「壞咗」、「唔 work」 | ❌ 唔准 |
| **問題** | 「can run real case now?」 | ❌ 唔准 |
| **陳述** | 「perm_pill 存 y 587..717，實際 y 1032..1078，差 315px」 | ✅ 准 |

**抱怨同問題都係「唔係陳述」。** 分別在於：抱怨係評價，問題係求資訊。

## 問題陳述四要素（缺一不可）

| 要素 | 要求 | 反例 |
|---|---|---|
| `target` | 指名**具體物件**，唔係類別 | ❌「個 rect」 ✅「perm_pill」 |
| `expected` | 一個**值** | ❌「應該 work」 ✅「y 1032..1078」 |
| `actual` | **觀察到嘅值**，唔係判斷 | ❌「錯咗」 ✅「y 587..717」 |
| `observable` | 第三者**唔問作者都查得到** | ❌「我睇到」 ✅「315px delta」 |

## 閘（Gate）

```python
assert_problem_stated(statement)   # 唔合格 → raise ProblemNotStated
```

**拒絕時要講明缺乜**，唔係淨係講「唔得」：

```
problem not stated — refusing to analyse:
  missing part(s): expected, actual, observable;
  not observable — no measurable value (px / ms / line / count / exit code)
```

## 收納：一次問一條問題

```python
intake_question(statement)   # → "What is the expected?" 或 None
```

**一次一條**（同 `skill_research_gate` 規則 7 一致）。唔准一次過問四條。

## 紅旗

- 「我大概知你想講乜」→ 唔准估，問
- 「個問題好明顯」→ 明顯嘅問題一樣要陳述
- 「先分析，之後再問清楚」→ 分析完先問 = 白做
- 「佢問嘅其實係…」→ **替對方重構陳述唔係答案**（`diagnosing-superpowers` 原話）

## 測試（Tests）

1. **`_proof_problem_citation.py`** — 30 個檢查，涵蓋規則、閘、變異。
2. **變異測試** — 移除「四要素」檢查，測試**必須失敗**。
3. **實測重播** — 「can run real case now?」必須被判為**問題**而唔係陳述。

## 合約（Contract）

- `contract_id` : `SKILL.PROBLEM.STATEMENT`
- `taxonomy_path`: `module/task_center`（已驗證 active）
- TDD cases : 3 `pass` + 3 `hard_fail`
