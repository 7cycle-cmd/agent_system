---
task_id: SKILL.SYSTEMATIC.DEBUGGING
display_task_id: SKILL.SYSTEMATIC.DEBUGGING
name: systematic_debugging
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 先根因後修復 — 鐵律、四階段、3+ 次修復失敗即質疑架構、紅旗與合理化對照
reason: 單一 session 內連犯四次同類錯（顏色信號→格網→band 索引→問人類），冇任何規則叫停
artifacts:
  - systematic_debugging.skill.md
schema: "result_yes_no"
---

# Skill: systematic_debugging

**Goal:** 未有根因調查之前，**唔准提出修復**。症狀修復係失敗。

Package path: `skills/1_core/systematic_debugging/`
Agent entry point: `.github/skills/systematic-debugging/SKILL.md`（只係指針）
相關：`skills/1_core/env_task_proof/`（行動前先證明）、
`skills/5_qa/evidence_classify/`（唔確定一律唔算 PASS）、
`skills/5_qa/prompt_measurement_discipline/`（落結論前先證明量度）

**來源：** `obra/superpowers`（MIT，288.8k stars，v6.4.1）之 `systematic-debugging`。
本 skill **採納其規則、唔複製其文字** — 價值在停止規則同引用要求，唔在散文。
（見 `docs/report_skill_set_for_finding_problems.md` §1、§5）

## 為什麼要有呢個 skill

前三個 skill 擋嘅係「**呢個 claim 係唔係真？**」。
冇一個擋「**我係唔係做緊啱嘅嘢？**」— 而實測四次失敗全部屬第二類。

兩者係同一個病嘅兩面：
- `env_task_proof` 擋「憑假設去 click」
- `prompt_measurement_discipline` 擋「憑一個數字去改標準」
- **本 skill 擋「憑症狀去改四次方法」**

## 鐵律

```
未有根因調查之前，唔准提出修復。
```

## 四個階段（必須逐階完成）

### Phase 1 — 根因調查

1. **讀清錯誤訊息** — 唔好跳過 warning。stack trace 由頭讀到尾，記 line number／file path／error code。
2. **穩定重現** — 觸發得到嗎？步驟係乜？每次都發生嗎？**重現唔到 → 收多啲數據，唔准估**。
3. **查最近改動** — `git diff`、新依賴、config、環境差異。
4. **多組件系統要落診斷 instrumentation** — 喺**每個組件邊界**記錄入乜／出乜，跑一次睇**邊層斷**，再查嗰層。
5. **追數據流** — 壞值由邊度嚟？邊個用壞值叫佢？一路向上追到源頭。**修源頭，唔修症狀。**

### Phase 2 — 模式分析

1. 喺同一 codebase 搵**行得通嘅同類實作**
2. 若果係跟某個 pattern，**完整讀參考實作**（唔准 skim）
3. **列出工作版同壞版嘅所有差異**，無論幾細；唔准假設「嗰個唔緊要」
4. 搞清楚依賴：要乜組件、乜設定、乜假設

### Phase 3 — 假設與測試

1. **寫低單一假設**：「我認為 X 係根因，因為 Y」— 要具體，唔准含糊
2. **最小測試** — 改最少嘅嘢，一次一個變數，唔准同時修幾樣
3. **先驗證再前進** — 唔 work → **開新假設**，唔准喺上面加多幾個修復
4. **唔知就講唔知** — 唔准扮識

### Phase 4 — 實作

1. **先寫會失敗嘅測試個案** — 最簡重現；冇框架就用一次性 script。**修之前必須有。**
2. **單一修復** — 針對根因，一次一個改動，**唔准「順手」改埋其他嘢**
3. **驗證修復** — 測試過咗嗎？冇整壞其他測試嗎？問題真係解決嗎？
4. **修復唔 work** → 停。數：試咗幾次？
   - **< 3 次** → 返 Phase 1，用新資訊重新分析
   - **≥ 3 次** → **停，質疑架構**（見 4.5）

### Phase 4.5 — 3 次以上失敗即質疑架構（本 skill 嘅核心閘）

**架構問題嘅特徵：**
- 每次修復都喺**唔同位置**揭出新嘅共享狀態／耦合／問題
- 修復需要「大規模重構」先做得到
- 每次修復都喺其他地方產生新症狀

**要停低問根本問題：**
- 呢個 pattern 本質上啱唔啱？
- 我哋係唔係「靠慣性」死守佢？
- 應該重構架構，定係繼續修症狀？

**呢個唔係「假設失敗」— 係「架構錯」。**

## Phase 1 補充 — 判斷器唔可以窄過提案者（實測 2026-09-21）

一個**判斷器**如果睇嘅範圍**窄過**提案者，會製造**假陰性** ——
而假陰性睇起上嚟**同「提案者錯」一模一樣**。

實例：我用 regex 掃**所有** `.py` 做候選池，但個 api 判斷器**只讀
`mouse_spot_helper.py`**。結果佢拒咗啲池**合法搵到**嘅 route。

```
候選池  = glob("*.py")                    <- 215 routes
判斷器  = 只讀 mouse_spot_helper.py        <- 拒絕咗池裡面嘅 route
=> 睇落似「模型錯」，其實係「判斷器視野窄」
```

**規則：** 判斷器必須讀**同一個語料庫**，唔可以自己收窄。
（`_probe_llm_register_fill.py:101` `_route_files()`）

**診斷順序：** 見到「提案者錯」之前，先確認**判斷器同提案者睇同一個世界**。

## Phase 1 補充 — 「冇總結行」等於有失敗（實測 2026-09-21）

一個 proof 喺**印總結之前**拋 exception，佢啲失敗係**隱形**嘅：
你去搵 output，搵唔到，就讀成「冇問題」。

實例：`_proof_binding_proposals.py` 第 7 節 `NameError`（空 DB 冇
`capability_registry`）→ script 喺**印 tally 之前**死 → **6 個失敗斷言
一直冇人見到**。

**規則：**
- tally 要喺 exception 路徑都印（`try/finally`）
- **「冇總結行」係一個 FAILURE**，唔係「冇失敗」

## 紅旗 — 見到即停，返 Phase 1

- 「先快速修復，之後再查」
- 「試下改 X 睇下 work 唔 work」
- 「一次改幾樣，跑測試」
- 「跳過測試，我手動驗證」
- 「大概係 X，等我改佢」
- 「我唔完全明白，但呢個可能 work」
- **「再試多一次」**（已試 2 次以上時）
- **每次修復都喺唔同位置揭出新問題**

## 合理化對照表

| 藉口 | 現實 |
|------|------|
| 「問題好簡單，唔使程序」 | 簡單問題都有根因。程序對簡單 bug 一樣快。 |
| 「緊急，冇時間做程序」 | 系統化比亂試更快。 |
| 「先試呢個，之後再查」 | 第一個修復就定咗模式。一開始就要做啱。 |
| 「確認 work 之後再寫測試」 | 未測嘅修復唔會穩。先寫測試先證明。 |
| 「一次改幾樣省時間」 | 分唔清邊樣 work，仲會引入新 bug。 |
| 「參考太長，我參考個 pattern 自己改」 | 部分理解必然出 bug。要完整讀。 |
| 「我睇到問題喇，等我改佢」 | 睇到症狀 ≠ 理解根因。 |
| 「再試多一次」（已失敗 2 次以上） | 3 次以上 = 架構問題。質疑 pattern，唔好再修。 |

## 本項目實測（本 skill 為何存在）

2026-09-20，同一個 session 內連續四次同類失敗：

| # | 做法 | 為何錯 |
|---|------|--------|
| 1 | 顏色搜尋（黃色） | 黃色編碼**選中狀態**，唔係控件位置 → **跟住 state 走嘅信號永遠定位唔到固定控件** |
| 2 | 格網／聚類 | 仍然係位置識別 |
| 3 | 基於索引嘅 band 比對 | 索引會漂移（9→10 band） |
| 4 | 問人類 | 已試 3 次以上，本應停低質疑架構 |

**架構錯在：用位置識別 rect。** 最終有效嘅修復係**用內容識別**。
「3+ 次 → 質疑架構」準確預測咗結果 — 呢個係規則可轉移嘅證據。

## 測試（Tests）

本 skill 必須有可執行嘅測試，唔係只有文件。

1. **`_proof_stop_rule.py`** — 餵入四次失敗修復記錄，斷言第 3 次之後
   輸出「質疑架構」而唔係「第 4 個修復」。
2. **變異測試** — 移除「3+ 計數器」，上述測試**必須失敗**（證明測試有效，唔係永遠綠）。
3. **重播測試** — 用上表四次失敗序列重播，斷言程序喺第 3 次前停低。
4. **引用測試** — 每個 finding 必須帶 `path:line`；冇引用者被**丟棄**，唔係降級。

## 合約（Contract）

- `contract_id` : `SKILL.SYSTEMATIC.DEBUGGING`
- `taxonomy_path`: `module/task_center`（**已驗證** active；`capability_registry`
  冇 systematic_debugging 實體，要獨立 taxonomy 就需先建 active row）
- `purpose` : single objective — 未有根因調查之前唔提出修復（`upsert_contract` 硬性必需）
- TDD cases : ≥3 `pass` + ≥2 `hard_fail`（硬失敗個案係閘嘅本體）

`upsert_contract()` 對 `taxonomy_path` 同 `purpose` **硬性必需**，缺即拒寫。

## 註冊（兩層，兩層都要做）

1. **Skill Library** — 由 `test_render.sync_skills_to_dev_tasks(conn, './skills')` 掃描
   （靠 frontmatter `task_id`；本檔 = `SKILL.SYSTEMATIC.DEBUGGING`）
2. **Skill Prompt SSOT** — `skill_prompt.upsert_skill_prompt(...)`（dropdown）
3. **驗證同步** — `_check_skill_ssot_drift.py` 必須報 `in sync: True`

> 編輯本檔之後**必須**重新 upsert，否則 SSOT 保持舊文字（已踩過）。
