---
name: prompt-measurement-discipline
description: "Use when: about to conclude anything from a prompt/model/classifier measurement — comparing two prompts, certifying a variant, reporting an accuracy number, choosing a prompt by score, or deciding a threshold. Enforces six rules so one flattering number cannot hide the truth: (1) gold set minority class >= 20%; (2) report balanced accuracy + per-class recall, never accuracy alone; (3) a run that answered only ONE class measured the gold set's mix, not the prompt — refuse it; (4) certification requires REPEATED 100% across seeds, never a single run (this tool once certified sampling luck); (5) train/holdout must be STRATIFIED and both sides must contain every class; (6) report a RANGE, not a single number. Also use when a 'no significant difference' result looks like a safe reason to keep the current prompt."
---

# Prompt Measurement Discipline（量測紀律）

**目標：** 當你由一個 prompt／模型／分類器嘅量度**落結論**之前，
先證明個量度本身可靠。同 `env-task-proof` 同一個家族——
但 `env-task-proof` 擋「憑假設去行動」，呢個擋「**憑一個數字去改標準**」。

Canonical skill：`skills/5_qa/prompt_measurement_discipline/prompt_measurement_discipline.skill.md`

## 幾時用

- 比較兩個 prompt／模型，準備講邊個好
- 準備「認證」一個變體，或者按分數揀 prompt
- 準備報一個 accuracy 數字／決定門檻
- 見到「**冇顯著差異（p≥0.05）**」而想「噉就留住現行嗰個」
- 見到一個好靚嘅分數（95%、100%）想信佢

## 六條規則（一行版）

1. **類別平衡** —— gold set 最少數類別 ≥ **20%**。否則永遠答多數類已經 >80%。
2. **平衡準確度** —— 報 balanced accuracy + per-class recall，**唔可以**只報 accuracy。
3. **判別性** —— run 只答**一類** = 分數係類別比例嘅算術，**唔算量度**，拒收。
4. **重複認證** —— 認證要**跨 seed** 重複 100%，**唔可以由單一 run**。
5. **分層切分** —— train/holdout 按類別分層，**兩邊都要有齊全部類別**。
6. **報範圍** —— 重複量度後報**區間**，唔好報一個數。

任何一項答「未」，**都唔可以落結論**。

## 五個真實踩坑（唔係理論）

| 踩過嘅坑 | 規則 | 實測 |
|---|---|---|
| 70% accuracy 但 YES recall **0%** | 2 | 永遠答 NO 喺 14:6 樣本嘅回報；balanced accuracy 誠實報 **50%** |
| 兩個唔同 prompt 得 **完全相同**數字，`p=1.000` | 3 | 兩邊都 60/60 答 NO；同分係**必然**，唔係發現 |
| **單一 seed** 100% 就發「certified」 | 4 | 5-seed 重測，同一 prompt 訓練集只有 **81–94%** |
| 8 圈時 100%、12 圈時 93.75% | 4 | **圈數少會誇大贏家** |
| 帳簿只存一個數，顯示 100.0% | 6 | 真正跨 seed 範圍係 **71–92%** |

**最值得記住嘅一件事：** 本專案嘅 certification 函式第一版，
喺單一 seed 兩邊都 100% 時就發認證。之後壓力測試證明佢認證咗**抽樣運氣**——
即係本專案要消滅嘅嗰種假信心，竟然喺**為咗防佢而寫嘅閘門裡面**重現。
所以 Rule 4 唔係「最好做」，係**必須做**。

## 為什麼「打和」最危險

「兩個爛 prompt 打和」睇落好似「安全，可以留住現行嗰個」。
實際上**兩者都冇睇過張圖**。一個由數據保證嘅 `p=1.000`，
讀落好似「經過驗證嘅無差異」，其實係一句同義反覆。

## 實作位置

| 檔案 | 角色 |
|---|---|
| `prompt_sweep.py` | `certify_repeated()` —— 唯一可以發認證嘅函式 |
| `prompt_dimension.py` | 由維度組合 prompt（唔係複製） |
| `prompt_improvement_report.py` | 邊際效應：邊個維度值造成漏判 |
| `skill_learning._gold_set_gate` | 類別平衡 + skew note |
| `skill_prompt.streak_proof` | `discriminating` + `balanced_accuracy_pct` |
| `test_prompt_certification.py` | 斷言 `verdict()` **永遠唔會**回 "certified" |

## 呢個檔案嘅性質

呢個係**指針**，唔係第二份副本。Canonical skill 喺
`skills/5_qa/prompt_measurement_discipline/`，所以佢對 Skill Library 掃描器可見、
可以有 contract、可以累積 streak。呢個檔案存在只係為咗令 agent 喺任務開始時
自動載入摘要；改呢度**唔會**改到實際執行嘅規則。
