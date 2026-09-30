# 搵問題嘅 Skill SET — 完整報告

**日期：** 2026-09-20
**觸發：** 同一個 session 內同類錯誤重複發生（顏色信號錯 → 清單索引錯 → 錯誤地問人類）。一個 skill 攔唔住。
**問題：** 要有點樣嘅一套 skill（源自實證 GitHub 項目）先攔得住？

---

## 第 0 步 — 分類問題，講出個關鍵

呢個請求有四個可分開嘅部分。先命名佢們，避免答錯題。

| 部分 | 真正嘅問題 | 關鍵 |
|---|---|---|
| 1 | 「去 GitHub 研究、比評分」具體係做乜？ | 邊啲 repo，同埋量度到嘅信號係乜 |
| 2 | 一套搵問題嘅 skill 需要邊幾個？ | 係**一套**，唔係一個 |
| 3 | 每個點樣對映**我哋嘅環境**？ | 相對已有嘢嘅缺口 |
| 4 | 每個點樣**被測試、被證明、被自動化**？ | 係閘，唔係文件 |

**個關鍵，一句講完：** 我哋現有三個 skill 各自答「呢個 claim 係唔係真？」— **冇一個**答「我而家係唔係做緊啱嘅嘢？」— 而今日每個錯都屬第二類。我用檢測砌出一個 rect，但從來冇確立「乜嘢識別一個 rect」。

---

## 1. 來源版圖（實測，唔係記憶）

| 項目 | 信號 | 方法內容 | 相關性 |
|---|---|---|---|
| **obra/superpowers** | **288.8k stars、25.8k forks、MIT、51 contributors**，v6.4.1 於本報告前一日發佈 | `systematic-debugging`、`verification-before-completion`、`diagnosing-superpowers`、`test-driven-development`、`writing-skills` | **搵到最高評分嘅來源。** 內含一套 debugging skill，唔係一個。 |
| **Google SRE Book** 第 15 章 | 業界權威文本（O'Reilly，CC BY-NC-ND） | 無責備事後報告；**觸發條件於事故前定義**；「未經覆核嘅事後報告等於從來冇存在過」 | 提供我哋臨時做嘅 *事故 → 教訓 → 預防* 迴路 |
| **Playwright** | Microsoft，官方文件 | 動作前可動作性：Visible、Stable、**Receives Events**、Enabled → `TimeoutError`，動作**唔會執行** | 今日已採納，用於 rect 點擊 |
| **Selenium** | Software Freedom Conservancy | `StaleElementReferenceException`（「每次使用都要重新定位元素」）、`ElementClickInterceptedException` | 獨立證實「先重新定位再動作」 |

**誠實限制：** GitHub 嘅 code-search 同 repo API 工具在本環境回傳 `No valid auth token`，所以 skill 內文係由 `raw.githubusercontent.com` 讀取，star/fork 數字係由 repo 頁讀取。兩者都係第一手來源，但我**無法跨多個 repo 計算排名** — 「最高評分」係基於實際讀到嘅數字，唔係基於一次調查。

### `systematic-debugging` 實際講乜（讀過，唔係轉述）

```
鐵律：未有根因調查之前，唔准提出修復
```

四個階段：**根因 → 模式 → 假設 → 實作**。另加：

- 4.5 — **「3 次以上修復失敗：要質疑架構」**，特徵模式：*「每次修復都喺唔同位置揭出新嘅共享狀態／耦合／問題」*
- 紅旗清單，包括 **「再試多一次」**（已試 2 次以上時）同 **「大概係 X，等我改佢」**
- 合理化對照表 — 例如 *「問題好簡單，唔使程序」* → *「簡單問題都有根因」*
- 三個輔助技巧：`root-cause-tracing`、`defense-in-depth`、`condition-based-waiting`

### `diagnosing-superpowers` 實際講乜

- **「每個發現都要引用 `path:line`。冇引用，就冇發現。每個數字都嚟自 transcript 或你跑過嘅指令，永遠唔可以嚟自記憶。」**
- 六個固定分析維度；冇 `path:line` 嘅發現會**被丟棄**
- 唯讀；有批准閘；而呢一條正正係我哋今日犯嘅：*「如果佢哋唔在場，就寫低問題然後停。你替佢哋重構出嚟嘅陳述唔係答案。」*

---

## 2. 相對我哋環境嘅缺口分析（實測）

現有 `.github/skills/`：`env-task-proof`、`evidence-classify`、`prompt-measurement-discipline`。正式版喺 `skills/{1_core,5_qa}/`。

| Skill | 答乜問題 | 狀態 |
|---|---|---|
| `env-task-proof` | *環境／目標係唔係如我所假設？* | 已有 |
| `evidence-classify` | *呢個標籤／區域實際係唔係含 X？* | 已有 |
| `prompt-measurement-discipline` | *呢個數字係唔係我聲稱嘅意思？* | 已有 |

**缺乜 — 而今日嘅失敗正正行使咗佢：**

| 缺失能力 | 今日出咗乜事 |
|---|---|
| **先根因，後修復** | 我未確立「乜嘢識別目標」之前，就先檢測黃色像素。黃色編碼嘅係*選中狀態*，所以方法係**種類錯**，唔係調參數錯。 |
| **重複修復後嘅停止規則** | 我試咗 顏色 → 格網 → 聚類 → 基於索引嘅 band 比對。即 ≥4 種做法。superpowers 規則講：**停，質疑架構。** 架構真係錯：用位置識別 rect。最終有效嘅修復係**用內容識別**。 |
| 對*發現*嘅引用紀律 | 我引用得好多（好），但先寫落 markdown 才入 DB — 即係發現冇可查詢嘅引用 |
| 事故觸發條件**事前**定義 | 教訓係我想起才寫；冇任何觸發條件話「呢個算係事故」 |
| 條件式等待 | Ctrl+Alt+K 之後 `time.sleep(1.4)` — 固定 sleep，正正係 `condition-based-waiting` 要取代嘅嘢 |

**用「3 次」規則驗證今日：** 次數係 4 種做法，而最終有效嘅修復需要**架構**改變（內容識別，唔係位置識別）。規則預測中咗結果。呢個係規則可轉移嘅證據。

---

## 3. 呢套 Skill SET（六個 skill、三層）

需要一套，因為失敗喺唔同層發生：有啲係 *「我有冇做緊啱嘅問題」*，有啲係 *「呢個量度有冇效」*，有啲係 *「有冇自動化」*。

### 層 A — 做啱嘅問題

**A1. `systematic-debugging`** — *來源：superpowers，可原封採納*
- 鐵律、4 階段、3+ 次 → 質疑架構、紅旗、合理化表
- **我哋嘅測試：** 重播今日嘅 rect bug。斷言程序會在第 3 次嘗試前停低，因為要求寫低假設同一個可推翻嘅測試。
- **證明：** 每個階段一個先失敗後通過嘅測試；一個移除「3+ 計數器」嘅變異必須令重播測試失敗。
- **自動化：** 對同一目標嘅重複修復失敗掛鈎計數器。

**A2. `problem-statement`**（源自 `diagnosing-superpowers` 嘅問題收納）
- *「『太慢』係抱怨，唔係問題陳述。」*
- 要求：指名目標、預期、實際、可觀察。
- **我哋嘅測試：** 今日嘅起始句 *「can run real case now?」* 按此定義係抱怨 → 斷言收納步驟會問一條問題。
- **自動化：** 陳述唔可觀察時，拒絕開始分析。

### 層 B — 令證據可信（大致已有）

**B1. `citation-discipline`** — *來源：superpowers「冇引用，就冇發現」*
- 每個發現帶 `path:line` 或指令；冇引用嘅發現**被丟棄**。
- **我哋嘅測試：** 餵入一個冇引用嘅發現 → 斷言佢被丟，唔係被降級。
- **自動化：** lesson 寫入器拒絕冇 `source_ref` 嘅 lesson。

**B2. 現有 `evidence-classify` + `prompt-measurement-discipline`**
- 已強制「不確定永遠唔算通過」同「單類別跑唔算量度」。**呢套要引用佢們，唔係複製佢們。**

### 層 C — 收尾閉環

**C1. `incident-postmortem`** — *來源：Google SRE 第 15 章*
- 觸發條件**事前宣告**；無責備；行動項目有負責人；同硬規則 *「未經覆核嘅事後報告等於從來冇存在過。」*
- **我哋嘅測試：** 觸發條件一 fire → 存在一行 postmortem；未覆核嘅在合併時被拒。
- **自動化：** 我哋嘅 `skill_lesson` + `skill_contract_*` 表已模擬咗；加一個覆核狀態閘（draft → reviewed）阻止合併。

**C2. `condition-based-waiting`** — *來源：superpowers 輔助技巧*
- 用輪詢條件 + timeout 取代固定 sleep。
- **我哋嘅測試：** `sleep(1.4)` 變成 `wait_until(menu_visible, timeout=3.0)`；斷言條件一真就即刻返回，timeout 時要響亮失敗。

---

## 4. 今日實測：真正已自動化嘅嘢

| 機制 | 自動化 | 證據 |
|---|---|---|
| `auto_rect_audit.audit_rects()` | 可呼叫 | judged=1 mismatched=0 not_applicable=3 |
| `assert_rect_ok(id)` | 動作前閘 | raise 咗 `RectNotProven`；cursor 冇動 |
| `auto_rect_audit.py --watch N` | 可排程 | 已存在 |
| 發現 → `skill_lesson` + `source_ref` | 去重 | 重跑跳過兩者 |
| `_check_skill_ssot_drift.py` | file↔SSOT sha256 | 3 個 skill `in sync` |

**所以層 C 部分已建。** 缺嘅係**層 A 嘅停止規則** — 今日冇任何嘢話「你已經試咗 4 次，停低重新框定」。

---

## 5. 建議次序（成本、價值）

| # | Skill | 為何先做 | 工作量 |
|---|---|---|---|
| 1 | `systematic-debugging`（A1） | 直接防止今日嘅失敗類；來源可原封採納；測試個案已存在 | 低 |
| 2 | `problem-statement`（A2） | 最便宜嘅閘；阻止對未框定嘅抱怨做分析 | 低 |
| 3 | `citation-discipline`（B1） | 一條規則，機械式可執行 | 低 |
| 4 | `incident-postmortem`（C1） | 需要事前協定觸發清單 | 中 |
| 5 | `condition-based-waiting`（C2） | 局部改動，呼叫點少 | 低 |

**採納，唔好複製。** `systematic-debugging` 係 MIT 且文字短；價值在**停止規則**同**引用要求**，我哋可以實作佢、並用自己嘅事故去測試佢。抄文字只會多一份文件而唔多一個閘 — 正正就係我哋三個 skill 本來要修嘅嗰種缺陷。

---

## 6. 本報告嘅誠實限制

1. **冇跨 repo 評分調查。** GitHub code/repo 搜尋冇 auth。讀到嘅 star 數（288.8k）好大且來自 repo 頁，但佢係一個項目，唔係一個排名。
2. **「3+ 次修復」規則係用重播測試，唔係實驗。** 我係事後配對今日嘅序列。有啟示性，但唔係對照試驗。
3. **SRE 事後報告文化係組織實踐**，唔係一個 library。可轉移嘅部分係*觸發清單*同*覆核閘*；其餘係文化，裝唔到。
4. **我自己就係犯錯者。** 我寫嚟阻止自己錯誤類嘅規則，應該先由他人對抗性測試先可信。

---

## 來源（第一手，本次 session 實讀）

- `github.com/obra/superpowers` — repo 頁（288.8k stars、MIT、v6.4.1）
- `raw.githubusercontent.com/obra/superpowers/main/skills/systematic-debugging/SKILL.md`
- `raw.githubusercontent.com/obra/superpowers/main/skills/diagnosing-superpowers/SKILL.md`
- `sre.google/sre-book/postmortem-culture/` — 第 15 章
- `playwright.dev/docs/actionability`
- `selenium.dev/documentation/webdriver/troubleshooting/errors/`

---

**總結一句：** 缺嘅唔係多一個量度 skill — 係**停止規則**（`3 次以上修復失敗 → 質疑架構`，佢準確預測咗今日結果）加上**冇引用即丟棄**。六個 skill、三層；五個新、兩個已有。
