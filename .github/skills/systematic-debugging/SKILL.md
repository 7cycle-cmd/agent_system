---
name: systematic-debugging
description: "Use when: encountering any bug, test failure, or unexpected behaviour BEFORE proposing a fix — or when you have already tried 2+ fixes for the same problem. Enforces the Iron Law (no fixes without root cause investigation first), four phases (root cause → pattern → hypothesis → implementation), and the stop rule: after 3+ failed fixes, question the ARCHITECTURE instead of attempting fix #4. Also use when a fix reveals a new problem in a different place."
---

# Systematic Debugging (root cause before fix)

**Goal:** 未有根因調查之前，**唔准提出修復**。症狀修復係失敗。

Full skill: `skills/1_core/systematic_debugging/systematic_debugging.skill.md`

Source: `obra/superpowers` (MIT, 288.8k stars, v6.4.1) — rules **adopted**, text not copied.
See `docs/report_skill_set_for_finding_problems.md` §1, §5.

## When to use

- 遇到任何 bug、測試失敗、非預期行為 — **提修復之前**
- **已經試過 2 次以上修復同一問題**
- 你覺得「快速修復就得」、「大概係 X」、「我唔完全明白但試下」
- 每次修復都喺**唔同位置**揭出新問題
- 效能問題、build 失敗、整合問題（全部適用）

## 鐵律

```
未有根因調查之前，唔准提出修復。
```

未完成 Phase 1，就唔可以提修復。

## 四階段（逐階完成）

| 階段 | 做乜 | 成功標準 |
|------|------|----------|
| **1. 根因** | 讀清錯誤、穩定重現、查最近改動、多組件落 instrumentation、追數據流到源頭 | 明白 WHAT 同 WHY |
| **2. 模式** | 搵行得通嘅同類實作、完整讀參考、列出所有差異 | 搵出差異 |
| **3. 假設** | 寫低單一具體假設、最小測試、一次一個變數 | 證實或開新假設 |
| **4. 實作** | 先寫失敗測試、單一修復、驗證 | bug 解決、測試過 |

## 4.5 — 3 次以上失敗即質疑架構（核心閘）

**架構問題特徵：**
- 每次修復都喺**唔同位置**揭出新嘅共享狀態／耦合／問題
- 修復需要「大規模重構」
- 每次修復都喺其他地方產生新症狀

**≥3 次失敗 → 停。質疑 pattern，唔好試第 4 個修復。**

## 紅旗 — 見到即停，返 Phase 1

- 「先快速修復，之後再查」
- 「大概係 X，等我改佢」
- 「我唔完全明白但呢個可能 work」
- **「再試多一次」**（已試 2 次以上）
- **每次修復都喺唔同位置揭出新問題** ← 架構錯嘅信號

## 本項目實測

2026-09-20，一個 session 內連犯四次同類錯：
顏色搜尋 → 格網 → band 索引 → 問人類。

**架構錯在：用位置識別 rect。** 最終有效嘅修復係**用內容識別**。
「3+ 次 → 質疑架構」準確預測咗結果。

## Note on this file

This is a **pointer**, not a second copy. The canonical skill lives at
`skills/1_core/systematic_debugging/` so it is visible to the Skill Library
scanner, has a contract and can accumulate a streak. Editing this file does not
change the enforced rule.
