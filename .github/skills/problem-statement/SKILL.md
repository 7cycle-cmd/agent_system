---
name: problem-statement
description: "Use when: about to analyse, debug, or fix something that was described as a complaint ('it's too slow', 'it's broken', '唔 work') or as a question ('can run real case now?', 'why is it wrong?'). Enforces that a problem must be STATED before it is analysed: a named target, an expected value, an actual value, and an observable a third party could check. A complaint is an evaluation, a question asks for information — neither is a problem statement, and analysing one produces a fix for a problem nobody stated. Also use when you catch yourself reconstructing what the user 'really meant'."
---

# Problem Statement (state it before you analyse it)

**Goal:** 未有一個**可分析嘅問題陳述**之前，**唔准開始分析**。

Full skill: `skills/1_core/problem_statement/problem_statement.skill.md`
Executable: `problem_statement.py` · Proof: `_proof_problem_citation.py` · Tests: `test_problem_citation.py`

Source: `obra/superpowers` (MIT, 288.8k stars) — `diagnosing-superpowers` intake.
Rules **adopted**, text not copied.

## When to use

- 收到嘅係**抱怨**：「太慢」、「壞咗」、「唔 work」、「有問題」
- 收到嘅係**問題**：「can run real case now?」、「點解會錯？」
- 你準備分析／debug／修復，但**未寫低過問題係乜**
- 你發現自己喺度**替對方重構**佢「其實想講」乜

## 三類輸入

| 類別 | 例子 | 可否分析 |
|---|---|---|
| **抱怨** | 「太慢」 | ❌ |
| **問題** | 「can run real case now?」 | ❌ |
| **陳述** | 「perm_pill 存 y 587..717，實際 y 1032..1078，差 315px」 | ✅ |

## 四要素（缺一不可）

| 要素 | 要求 | 反例 |
|---|---|---|
| `target` | 具體物件 | ❌「個 rect」 ✅「perm_pill」 |
| `expected` | 一個值 | ❌「應該 work」 ✅「y 1032..1078」 |
| `actual` | 觀察值 | ❌「錯咗」 ✅「y 587..717」 |
| `observable` | 第三者查得到 | ❌「我睇到」 ✅「315px delta」 |

## 閘

```python
assert_problem_stated(statement)   # 唔合格 → raise ProblemNotStated
intake_question(statement)         # → 一次一條問題
```

**一次問一條**，唔准一次過問四條。

## 紅旗

- 「我大概知你想講乜」→ 唔准估，問
- 「個問題好明顯」→ 明顯嘅問題一樣要陳述
- 「先分析，之後再問清楚」→ 分析完先問 = 白做
- 「佢問嘅其實係…」→ **替對方重構陳述唔係答案**
