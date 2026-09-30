---
task_id: SKILL.INDEPENDENT.REVIEW
display_task_id: SKILL.INDEPENDENT.REVIEW
name: independent_review
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 多個 worker 嘅分歧唔係投票理由，係量度觸發器 — 共識唔係證據、空結果要有正向對照
reason: 三個外部 worker 對同一條問題答 7/16/8，而我連續三個偵測器回傳空值都當成「冇問題」
artifacts:
  - independent_review.skill.md
schema: "result_yes_no"
---

# Skill: independent_review

**Goal:** 用多個獨立 worker 嘅**分歧**做量度觸發器；**永不**用一致同意做結論。

Package path: `skills/1_core/independent_review/`
Agent entry point: `.github/skills/independent-review/SKILL.md`（只係指針）
規則模組：`independent_review.py`（純函數，proof 同 TDD probe 都 import 佢）
相關：`skills/1_core/problem_statement/`（claim 要先講得清）、
`skills/1_core/citation_discipline/`（冇引用即丟）、
`skills/5_qa/prompt_measurement_discipline/`（一個數字唔可以蓋過真相）、
`skills/1_core/systematic_debugging/`（3+ 次失敗即質疑架構）

## 為什麼要有呢個 skill

呢個 session 有三個外部 worker（DeepSeek / 豆包 / Gemini）答**同一條**問題，
得出 **7 / 16 / 8 個 module**。三份都係判斷，冇一份建基於路線清單。

**有價值嘅產出唔係任何一份提案 —— 係分歧本身逼出咗一次量度。**
量度完（`route_inventory.py`：187 routes / 57 namespaces）三份都唔啱。

而同一 session 出現咗反面教材：**我連續三個偵測器都回傳 `{}`，三次都報成成功。**
壞咗嘅偵測器同冇壞嘅，可觀察結果完全一樣。所以 worker 嘅空結果唔可以冇對照就接受。

## 五條規則

| # | 規則 | 追溯嘅實測失敗 |
|---|------|----------------|
| R1 | 共識唔係證據。N 個 worker 同意 = N 份共享假設。 | 三個 worker 讀同一份錯文件，ratio = 1.0，結論錯得一樣自信 |
| R2 | 分歧觸發**量度**，唔係投票。永不平均、永不取多數、永不揀最自信、永不揀贏家。 | 7/16/8 冇共同答案；量度先係答案 |
| R3 | claim 要先化約成第三者查得到嘅 observable，先可以裁決。 | 「it is too slow」冇 target 冇值，只可以「同意」，唔可以「裁決」 |
| R4 | 空結果需要**正向對照**。冇對照，「搵唔到」同「壞咗」係一樣嘅。 | 三個偵測器回傳 `{}`，`len(Counter(x)) >= 1` 永真 |
| R5 | 啟發式規則要**雙向量度**。乜都 match 同乜都唔 match，一樣無用、一樣自信。 | token-overlap 得 25（20 錯）；strict substring 得 5 |

## 硬閘

```
assert_may_adopt(verdicts, measurement=, cite_ref=)
```

喺以下任何一種情況**擲出** `UndiscriminatedDisagreement`：

1. **裁決之間有分歧，而冇辨別性量度** → 不淮投票（R2）
2. **裁決冇引用** → 一致同意都唔採納（R1）

`adjudicate()` **故意冇 `winner` 欄位**。`selected` **永遠係 `None`** ——
因為一條「揀多數派」嘅 code path 就係呢個 skill 存在嘅理由。

## 何時用

- 多過一個 worker（人或模型）答同一條問題
- 正想用「佢哋都同意」做理由
- 想用平均／投票／最自信嘅答案收窄分歧
- 收到一個**空結果**，而想當佢係「冇問題」
- 正想用一條啟發式規則落結論（token overlap、substring、相似度）

## 五個下一個動作（`next_action`）

| 值 | 意思 |
|---|---|
| `ADOPT` | 有已引用嘅量度分開咗各立場 —— 但**仍然冇揀任何 worker 嘅 claim** |
| `MEASURE` | 有分歧／一致都好，**冇辨別性量度** → 去做量度 |
| `CLARIFY` | claim 化約唔到 observable → 先問清楚，唔准裁決 |
| `CONTROL` | 空結果冇正向對照 → 先證明個偵測器搵得到嘢 |
| `REFUSE` | 冇裁決可審 |

## 測試（Tests）

本 skill 必須有可執行嘅測試，唔係只有文件。

1. **`_proof_independent_review.py`** — 78 個斷言，**mutation-sensitive**。
   四個變異：移除 R1 citation gate / R2 measure gate / R3 observability /
   R4 control gate，每個**必須**令對應斷言轉紅，還原後轉綠。
2. **真實案例重播** — `DeepSeek=7 豆包=16 Gemini=8` 必須得出
   `next_action=measure`、`selected=None`。呢個係 fixture 嘅重點：
   唔係合成案例。
3. **一致同意測試** — 三個 worker 同答 7，ratio = 1.0，**仍然**係 `measure`。
4. **空結果測試** — `{"found": []}` 冇 `positive_control` 必須被拒。
5. **雙向規則測試** — `matches_nothing` 同 `matches_everything` 兩個 flag
   都要驗。

## 量度「歧義」唔係量度「重疊」（實測 2026-09-21）

當你要決定「可唔可以採納」，唔好靠**重疊幾多**（品味），要靠**候選有幾個**（可數）。

```
STRONG (>=2 重疊 token): 0      <- 冇任何強建議
WEAK   (1 token)        : 8      <- 全部都係
candidate_count: /api/prompt, /prompt-analyze, /prompt/setting -> 5
                 其餘 5 個 group                                -> 1
```

**判別準則係候選數，唔係重疊數：**

| 規則搵到 | 意思 | 可以做 |
|---|---|---|
| **1 個** | 規則**已決定** | 覆核者可以睇 |
| **>1 個** | 規則**未決定** | 確認等於**隨機揀一個** → 拒 |

```python
binding_proposals.assert_may_confirm(conn, "/api/prompt")
# -> {'ok': False, 'gate': 'ambiguity',
#     'why': "... matched 5 capabilities; the rule did not decide,
#             so confirming would pick one at random"}
```

**注意：** 呢個只係報告**許可**，唔係確認。`_proof_ambiguity_gate.py` 斷言
「叫完個閘唔會產生任何 CONFIRMED 行」。

**為何呢個係本 skill 嘅一部份：** 當 `api_registry.capability_id` 係 NOT NULL
而父層**冇得推導**，壓力會令人**確認一個猜測**去令個圖「睇落完整」。
**歧義閘就係擋呢個壓力嘅。** 佢唔判斷對錯 —— 佢判斷**規則有冇決定**。

## 合約（Contract）

- `contract_id` : `SKILL.INDEPENDENT.REVIEW`
- `taxonomy_path`: `module/task_center`（**已驗證** active）
- `purpose` : single objective — 未經辨別嘅多 worker 分歧不准以投票收窄
- TDD cases : ≥3 `pass` + ≥2 `hard_fail`（硬失敗個案係閘嘅本體）

**注意：`expected` 一定要係 dict。** 純字串會被 `_expected_matches` 靜靜咁當成
「冇記錄期望」，個 case 就永遠綠 —— 呢個係已踩過嘅陷阱。

## 註冊（四層，四層都要做）

1. `skill_prompt.upsert_skill_prompt()` — SSOT dropdown
2. `skill_contract_store.upsert_contract()` — taxonomy_path 硬性必需
3. `skill_contract_store.upsert_field()` — ≥5 行
4. `skill_contract_store.upsert_tdd_case()` — ≥3 pass + ≥2 hard_fail

Skill Library 由 `test_render.sync_skills_to_dev_tasks(conn, './skills')` 掃描。
**另外要加 probe token**：`skill_tdd_runner.PROBES["IR."] = _probe_ir`，
否則每個 case 都回 "no probe registered" 然後失敗。

## 本項目實測（本 skill 為何存在）

| 事件 | 為何錯 |
|------|--------|
| 三個 worker 答 7/16/8 | 三份都係判斷，冇一份建基於量度。我三份都反駁咗。 |
| 我嘅 token-overlap 規則 | 得 25 條跨 module route，**20 條假陽性**（`center` 撞 `task_center`） |
| 我嘅 strict substring 規則 | 得 5 條。兩條規則，兩個答案，兩個都自信。 |
| 偵測器 v1 | 值同 key 同一個字串 → 每 key 最多一個值 → 永遠 `{}` |
| 偵測器 v2 | 用 `segs[1]` 分組，但 `/api/v1/skills` 嘅 `segs[1]` 係 `v1` → 永遠 `{}` |
| 偵測器 v3 | 剝走版本段但仍然 key 喺 `segs[0]`，兩邊都係 `api` → 永遠 `{}` |
| `len(Counter(x)) >= 1` | 對任何非空 list 都成立 —— 一粒唔可能失敗嘅條件 |

**四次都係同一個病：一個規則寫低咗但冇喺行動點拒絶。**