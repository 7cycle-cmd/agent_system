---
task_id: SKILL.UI.STANDARD
display_task_id: SKILL.UI.STANDARD
name: ui_standard
catalog_id: 3
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: "The UI standard: 5 rules with measured units, so 'user friendly' is checkable"
reason: "The human: 'user friendly is not a word is standardize'"
artifacts: ["ui_standard.skill.md"]
schema: "UI element register + 5 rules + a proof per rule"
---

# Skill: ui_standard

## 為什麼有這個 skill

**用戶（2026-09-27）原話：**

> "be user fiendly as we have ui helper team , why i never have good experience ?
> i just have question and question for all your ui job?"
> "how does it can be better, github can help you by skill how to have ssot for
> all ui element to proof user friendly is not a word is standardize"
> "3) **`user_friendly for UI -> 5W1H to get factor + element(wording?) -> this is
> formula, you can proof my word, by evidence , i am not boss, evidenc is boss"

**實測到嘅缺陷：** `user_friendly` 喺 `terminology_register` **冇註冊**（1483 個詞，0 命中），
喺 `docs/` **0 個檔案**提到。佢係一個冇定義、冇單位、冇 proof 嘅形容詞 ——
所以每次 agent 話「做好咗」，用戶都冇辦法檢查。

**「User friendly」唔係一個詞。佢係一個標準，而標準係一張表。**

## 第二個實測缺陷

repo 有 **10** 個 `contract.yaml`，**0** 個係 UI。UI 係唯一一個「有 skill 但冇 contract」
嘅領域 —— 所以冇 LOCKED QC checklist、冇 proof、冇嘢可以 fail。

## 三個 UI skill 收合成一個

`ui_skill`（dev_task 329）、`skill_ui_ux_audit`（252）、`ui_field_builder`（251）
各自有一個 `contract.yaml` **指向** `SKILL.UI.STANDARD`。佢哋唔會各自有 contract ——
呢個就係用戶講嘅 `3 > UI template > 1`。

## 五條規則，每條有量度單位

| # | rule | metric_kind | metric_unit | target |
|---|------|-------------|-------------|--------|
| 1 | `label_registered` | `pct` | 有 `terminology_register` 行嘅可見 label 百分比 | 100 |
| 2 | `why_clickable` | `count` | hover-only `title=` 屬性嘅數量 | 0 |
| 3 | `unknown_names_source` | `pct` | 講明 table + value 嘅「唔知」badge 百分比 | 100 |
| 4 | `number_names_population` | `pct` | 講明數咩嘅數字百分比 | 100 |
| 5 | `empty_state_names_action` | `pct` | 有下一步嘅空狀態百分比 | 100 |

## 核心執行流程

1. 喺 `ui_element_register` 註冊元素，**必須帶 `unit_key`**（要喺 `unit_register` 解析到）
2. 跑**全部五條**規則，**即使五條都過**
3. 唔合格嘅元素喺**寫入點丟棄**，**唔准降級**
4. 五個 baseline 用**數字**報告，唔係用一句 claim
5. 對照**渲染出嚟嘅頁面**，因為一個自己同意自己嘅 register 證明唔到任何嘢

## 唔准做

- 唔准將「唔適用」嘅規則計入分母
- 唔准將 `count` 規則嘅 metric 報成**通過數**（應該係**失敗數**）
- 唔准喺第一條失敗規則就停
- 唔准用 `low_confidence` key 保留一個被丟棄嘅元素
- 唔准重新打五條規則嘅名；要 `import ui_standard.RULES`

## 實測 baseline（pinned 頁，2026-09-27）

| 規則 | register | 渲染頁面 |
|---|---|---|
| `label_registered` | 18/18 | **2/10** 表頭 |
| `why_clickable` | 0 hover-only | **3** hover-only，**0** clickable |
| `unknown_names_source` | 2/2 | **0/2** |
| `number_names_population` | 2/2 | **0/11** |
| `empty_state_names_action` | 1/1 | **0/1** |

**register 全部通過，但渲染頁面 4/5 唔達標。** 呢個差距就係 `check_divergence` 要量度嘅嘢 ——
一個自己同意自己嘅 register 證明唔到任何嘢。

## 驗證

```text
.\.venv\Scripts\python.exe ui_element_register.py --seed
.\.venv\Scripts\python.exe ui_element_register.py --check-divergence
.\.venv\Scripts\python.exe ui_standard.py --audit --page user_environment.sessions
.\.venv\Scripts\python.exe _proof_ui_standard.py
```

## 架構對齊

- **Skill** = 固定五條規則、固定輸出格式、固定丟棄語義
- **Prompt/入參** = 頁面、元素、渲染器（可變，唔使改 skill 代碼）
- **知識庫來源** = `ui_element_register`（元素）+ `terminology_register`（詞）+ `unit_register`（單位）
