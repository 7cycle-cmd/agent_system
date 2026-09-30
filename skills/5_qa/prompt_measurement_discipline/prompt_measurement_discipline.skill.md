---
task_id: SKILL.PROMPT.MEASURE.DISCIPLINE
display_task_id: SKILL.PROMPT.MEASURE.DISCIPLINE
name: prompt_measurement_discipline
catalog_id: 1
subcatalog_id: 5
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 量測紀律 — 唔可以用一個靚數字掩蓋真相。類別平衡、平衡準確度、判別性、重複認證、分層切分
reason: 同一個 prompt 得到 70% accuracy 但 0% YES recall；單一 seed 100% 被當成 certified。兩個都係假信心
artifacts:
  - prompt_measurement_discipline.skill.md
schema: "result_yes_no"
---

# Skill: prompt_measurement_discipline

**目標：** 當你量度任何 prompt／模型／分類器嘅表現時，**唔可以**用一個靚數字
掩蓋真相。呢個 skill 將五次真實踩坑變成五條硬規則。

Package path: `skills/5_qa/prompt_measurement_discipline/`
Agent entry point: `.github/skills/prompt-measurement-discipline/SKILL.md`（只係指針）
相關：`skills/1_core/env_task_proof/`（同一個家族：先證明，唔好假設）、
`skills/5_qa/evidence_classify/`（唔確定一律唔算 PASS）

## 為什麼要有呢個 skill

`env_task_proof` 講「行動前先證明環境」。
呢個 skill 講「**落結論前先證明量度**」。

兩者係同一個病：**用一個唔可靠嘅訊號當成事實**。
`env_task_proof` 擋嘅係「憑假設去 click」；
呢個擋嘅係「**憑一個數字去改標準**」。

以下五條規則全部由實測失敗而來，唔係理論。

## Rule 1 — 類別平衡（class balance）

gold set 嘅**最少數類別**必須 ≥ 20%。否則一個永遠答多數類嘅 prompt 已經拿到
>80% 分，任何比較都無意義。

**實測：** `mouse_spot_verify` 嘅 gold set 曾經係 14 NO : 2 YES
（最少數類 12.5%）。一個永遠答 NO 嘅 prompt 得 87.5%。兩個唔同嘅 prompt
必然同分——詳見 Rule 3。

**檢查：** `_gold_set_gate()` 會報 `minority_share`，低於 0.20 就出 note。
**方向：** 補最少數類嘅 case，唔係補多數類。

## Rule 2 — 用平衡準確度，唔好只報 accuracy

資料不平衡時，**accuracy 係一個會誤導人嘅指標**。
一律報 `balanced_accuracy` = 每類召回率嘅平均。

**實測：** 現行 `mouse_spot_verify` prompt 報 70% accuracy——
但係 YES recall **0.00%**。即係佢**從來冇偵測到一次命中**。
balanced accuracy 誠實地報 **50%**（一個永遠掉同一面嘅硬幣）。

| 指標 | 值 | 講緊咩 |
|---|---|---|
| accuracy | 70.00% | 喺 14:6 樣本上永遠答 NO 嘅回報 |
| balanced accuracy | **50.00%** | 一類 100%、一類 0% 嘅真相 |
| YES recall | **0.00%** | 佢完全偵測唔到命中 |

**做法：** 任何 run 都要記錄 `per_class` recall。報表以 balanced accuracy 做頭條，
accuracy 可以報，但**唔可以單獨用嚟排名**。

## Rule 3 — 判別性：一個 run 只答一類 = 冇量度過

如果一個 run 嘅答案**全部同一類**，咁佢嘅分數係 gold set 類別比例嘅**算術結果**，
同 prompt 好唔好無關。呢種 run 唔可以當證據。

**實測：** 兩個唔同 prompt 嘅 A/B 得到**完全相同**嘅數字
（60/60 答 NO、兩個都 81.67%、`z=0.000`、`p=1.000`），被報成
「無顯著差異」。但嗰個結論係**由數據保證**嘅，唔係量出嚟嘅——
兩個 prompt 都冇睇過張圖，當然同分。`p=1.000` 係一句同義反覆。

**最危險嘅地方：** 「兩個爛 prompt 打和」睇落好似
「安全，可以留住現行嗰個」。實際上兩者都冇作用。

**檢查：** `discriminating = len(answer_classes) >= 2`。
`test_candidate(require_discriminating=True)` 係**預設開啟**。

## Rule 4 — 重複認證：唔可以由單一 seed 發認證

一次 100% 可以係**抽樣運氣**。要認證，一定要**跨多個 seed 重複**。

**實測（本專案最尷尬嘅一次）：** certification 函式第一版喺**單一** seed
run 兩邊都 100% 時就發「certified」。之後 5-seed 壓力測試顯示，**同一個 prompt**
訓練集只有 **81–94%**，100% 只係嗰一個 seed 啱好。即係話：呢個工具**認證咗抽樣運氣**
——正正係本專案要消滅嘅假信心，竟然喺**為咗防佢而寫嘅閘門裡面**重現。

**修正：** 單一 run 最多只可以叫 `provisional`（暫定）。
只有 `certify_repeated(runs)` 可以叫 `certified`，而且要求：
測試集**每一個 seed** 都 100%，且**每一個 run 都答多過一類**。
訓練集以**範圍**報告，**唔要求完美**（要求佢完美就係靠抽樣運氣認證）。

**另一個實測：** 8 圈時某組合訓練集 100%；12 圈時同一個組合只有 93.75%。
**圈數少會誇大贏家。** 唔可以由細樣本認證。

## Rule 5 — 分層切分（stratified train / holdout）

用嚟揀變體嘅 case，唔可以用嚟證明佢。切分要**按類別分層**。

**為什麼要分層：** 喺 14 NO : 6 YES 上面隨機切，好容易將所有 YES 切埋一邊，
噉 holdout 就變成單一類別——**偵測唔到「一個失敗於另一類」嘅 prompt**，
即係偵測唔到我哋正在追捕嘅嗰個失敗。

**檢查：** 切分後要**印出兩邊嘅類別分佈**；holdout 得返一類就**中止**。

**實測：** 20 個 case → 訓練 14 `{NO:10, YES:4}`、測試 6 `{NO:4, YES:2}`。
兩邊都有齊兩類。

## Rule 6 — 單一數字 = 假精確度

重複量度之後，**一定要報範圍，唔好報一個數**。

**實測：** 帳簿原本只存一個訓練分數，令冠軍顯示 **100.0%**，
而佢真正跨 seed 範圍係 **71–92%**。一個數字暗示咗數據冇嘅精確度。

**修正：** 存 `train_ba_min/max`、`hold_ba_min/max`、`seed_runs`，
報表同 UI 一律顯示區間：`71.4–91.7% | 100.0% (5/5 seeds) | certified`。

## Rule 7 — 斷言「性質」，唔好斷言「數字」（實測 2026-09-21）

**硬編碼數字會訓練人改數字，而唔係問個改動啱唔啱。**

**實測：** `_proof_register_approval.py` 寫住 `check("the alphabet grew to 16",
letters == 16)`。當我合法咁加咗第 17 個 entity type（layer B 嘅
`namespace`）—— **一個正確嘅改動令一個綠色 proof 轉紅。**

```
# 錯：斷言一個字面值
check("the alphabet grew to 16", letters == 16)

# 啱：斷言必須成立嘅關係
check("the letter count matches the register rows",
      letters == len(er.list_entity_types(conn)))
check("...and it does not exceed the 26 available letters", letters <= 26)
```

**為何重要：** 一個硬編碼數字令**正確**嘅改動睇起上嚟係**錯誤**。
跟住嘅反應係改個數字 —— 於是個斷言**永遠唔會再檢查任何嘢**。

**規則：** 數字可以做**邊界**（`<= 26`），唔可以做**等號**。

## 快速檢查清單

落任何 prompt／模型結論之前，逐項問：

1. 最少數類別 ≥ 20% 未？
2. 有冇報 per-class recall（唔係只有 accuracy）？
3. 每個 run 都答多過一類未？（唔係就係無效）
4. 認證係跨 seed 重複，定係一個 seed？
5. train / holdout 兩邊都有齊全部類別未？
6. 報緊係範圍，定係一個數？

任何一項答「未」，**都唔可以落結論**。

## 相關實作

| 檔案 | 角色 |
|---|---|
| `prompt_dimension.py` | 由維度組合 prompt（唔係複製） |
| `prompt_sweep.py` | 全因子掃描 + 分層切分 + `certify_repeated()` |
| `prompt_improvement_report.py` | 邊際效應（邊個維度值造成漏判） |
| `skill_learning._gold_set_gate` | 類別平衡 + skew note |
| `skill_prompt.streak_proof` | `discriminating` + `balanced_accuracy_pct` |
| `test_prompt_certification.py` | 斷言 `verdict()` **永遠唔會**回 "certified" |
