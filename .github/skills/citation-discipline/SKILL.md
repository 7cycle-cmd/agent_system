---
name: citation-discipline
description: "Use when: recording a finding, lesson, or root cause — especially before writing it to a DB row, a lesson store, or a report. Enforces 'no citation, no finding': every finding must carry a checkable reference (path:line or a command), and a finding without one is DISCARDED at the write site, never downgraded to a low-confidence finding. Also use when you catch yourself citing memory ('I remember'), authority ('the docs say'), or writing findings to markdown before the DB row carries the reference."
---

# Citation Discipline (no citation, no finding)

**Goal:** 冇引用嘅發現**被丟棄**，唔係降級。

Full skill: `skills/1_core/citation_discipline/citation_discipline.skill.md`
Executable: `citation_discipline.py` · Proof: `_proof_problem_citation.py` · Tests: `test_problem_citation.py`

Source: `obra/superpowers` (MIT) — `diagnosing-superpowers`:
> 「每個發現都要引用 `path:line`。冇引用，就冇發現。」

## When to use

- 準備寫一條 finding / lesson / root cause 落 DB 或報告
- 你引用嘅係**記憶**（「我記得」）或**權威**（「文件話」）
- 你打算**先寫落 markdown，之後補引用**（之後永遠唔會補）
- 你打算將冇引用嘅發現**降級**做「低信心」

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
assert_cited(finding)   # 冇引用 → raise UncitedFinding
```

冇引用嘅發現**唔會**變成「低信心發現」— 佢**消失**。
降級會令冇引用嘅發現繼續存在，只係換個標籤。

**閘要喺寫入點叫**，唔係讀取點。一個冇人喺寫入點叫嘅判斷式，等於冇。

## 紅旗

- 「我親眼睇到」→ 邊個 file 邊行？
- 「之前傾過」→ 邊個 transcript？
- 「好明顯」→ 明顯唔係引用
- 「先寫落 markdown，之後補引用」→ **之後永遠唔會補**
