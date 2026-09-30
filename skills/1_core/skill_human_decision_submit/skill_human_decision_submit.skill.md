---
task_id: SKILL.HUMAN.DECISION.SUBMIT
display_task_id: SKILL.HUMAN.DECISION.SUBMIT
name: skill_human_decision_submit
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 人力決定提交 — 接收人工答案、逐項校驗、append-only 記錄 provenance、先寫 DB
reason: 決定清單係證據展示層，唔應該自己做決定；但接收決定呢個動作層一直冇人做，所以答案只能靠口頭傳
artifacts:
  - skill_human_decision_submit.skill.md
schema: "result_yes_no"
---

# Skill: skill_human_decision_submit

**Goal:** 接收**人工決定**，逐項校驗，記錄來源（邊份文件、邊一行、邊個決定），
校驗全過先寫入 DB。**呢個 skill 唔會自己做任何決定。**

Package path: `skills/1_core/skill_human_decision_submit/`
可執行：`skill_human_decision_submit.py`
輸入檔案：`decision_answers.py`（人機介面形狀，雙向）
顯示層：`decision_list.py` → `decision_list.md`
Proof: `_proof_human_decision_submit.py`（57/0）

## 為什麼要拆兩層（而唔係將 MD 變成 skill）

```
decision_list.py             渲染工作表          證據展示層（唔決定、唔寫 DB）
decision_answers.py          擁有輸入檔形狀       人機介面（唔決定、唔寫 DB）
skill_human_decision_submit  校驗 + 提交 + 溯源   動作執行層
```

**唔可以由 MD 反讀答案。** MD 係渲染結果，係俾眼睛睇嘅：欄寬、標題、措辭
隨時可變，冇一樣係合約。由渲染結果反讀答案 = 格式一改就靜靜回傳**零個答案**，
而「搵唔到答案」同「冇嘢要交」讀起嚟一模一樣。呢個就係今日一系列
「檢查錯對象」嘅同一家族 —— 包括文字搜尋搜到自己 docstring 嗰幾次。

**分層係按角色，唔係按格式。** 所以顯示層永不決定，輸入檔永不由機器代填，
只有執行層先會寫入 —— 而且要六道閘全過。

## 六道閘（全部拒絕，冇警告）

| 閘 | 規則 | 為什麼 |
|---|---|---|
| **G1** | 空白答案 = 未決定 → 跳過，永不當空值提交 | 空格係一個狀態，唔係一個答案 |
| **G2** | `answer` 必須係權威 `skill_key` 或字面 `NONE` | 打錯字唔可以以「解析唔到任何東西」嘅映射入 namespace |
| **G3** | `decided_by` 必填，且唔可以係 `pattern` / `guess` / `auto` / `unknown` | 一個描述**過程**嘅字唔係一個決定 |
| **G4** | `cite_ref` 必填，且必須過 `citation_discipline.assert_cited` | 無引用嘅決定係**丟棄**，唔係降級 |
| **G5** | 有候選嘅行，答案必須係**規則提出過嘅其中一個** | 呢個就係「唔可以提交 AMBIGUOUS 多個候選」嘅執行形式：答案永遠係**一個值**，而且要係規則真係提出過嘅。自己另揀一個 = 覆寫 → **拒絕**（見下） |
| **G6** | `NO_CANDIDATE` 行可以填任何權威 key；`NONE` 永遠准許 | 規則冇提出任何嘢，就冇嘢可以對照。而「呢個唔對應任何 skill」係一個真實決定 |

## 點解 G5 拒絕「覆寫」（而唔係靜靜接受）

一個 AMBIGUOUS 行（例如 3 個 contract 都撞去 `skill_proposal_validate`），
人類**確實可以**合理地推翻碰撞規則。但咁樣佢就唔再係一個映射，而係一個**覆寫**，
需要自己嘅證明依據。目前**冇任何模組可以承載「覆寫引用」**，所以本 skill
拒絕，並將拒絕原因講明。准許一個冇地方記錄嘅覆寫 = 製造一個冇 provenance 嘅映射。

## 寫入：兩張表，各有各答嘅問題

| 表 | 性質 | 答咩問題 |
|---|---|---|
| `skill_key_alias` | 現行真值（`INSERT OR REPLACE`） | 「呢個 alias **而家**對應咩？」 |
| `skill_key_decision_log` | **append-only** | 「邊個決定、幾時、憑咩、之前係咩？」 |

一張表答唔到兩條問題：將歷史塞入映射表會令解析曖昧；只存現值就冇審計鏈。

**`apply_proposed` 唔可以做人力路徑。** 佢拒絕所有非 `PROPOSED` 狀態，
所以 AMBIGUOUS / REVIEW / NO_CANDIDATE 永遠冇可能通過佢提交。人力路徑需要
自己嘅入口（本 skill 提供），而呢一點有 proof 證明，唔係假設。

## `NONE` 寫咩

**唔寫入 `skill_key_alias`。** 寫 `skill_key='NONE'` 會將一個「非答案」放入
真 skill key 嘅 namespace，之後任何 join 都會搵到一個**幻影 skill**。
拒絕本身照樣記錄 —— 喺 decision log，`written_alias=0`。

## provenance（trace）

每次提交一個 `trace_id`：

```
DEC-<YYYYmmdd-HHMMSS>-<8 hex>
```

Log 記低：`trace_id`、`alias_key`、`rule_state`（**保留規則原本立場**）、
`answer`、`written_alias`、`candidates`、`decided_by`、`cite_ref`、
`source_md`（人睇邊份工作表）、`md_line`、`source_tsv`、`tsv_line`。

`tsv_line` 係 **parse 時數出嚟**嘅，唔係人手打。自己報嘅行號係聲稱；數出嚟嘅係觀察。

## 拒絕空結果

```
assert_answers_found(parsed, path)   # 0 行 → raise NoAnswersFound
```

一個破壞咗嘅工作表，唔可以報告成「0 accepted / 0 rejected」—— 咁讀起嚟似一次乾淨嘅執行。
「我搵唔到嘢」永遠唔等於「冇嘢要做」。

## 用法

```powershell
# 1. 產生輸入檔（永不覆蓋已有答案嘅檔案）
.\.venv\Scripts\python.exe skill_human_decision_submit.py --render --answers decision_answers.tsv

# 2. 人手填 answer / decided_by / cite_ref 三欄

# 3. dry run（預設，唔寫任何嘢）
.\.venv\Scripts\python.exe skill_human_decision_submit.py --answers decision_answers.tsv --md decision_list.md

# 4. 確認後才 apply
.\.venv\Scripts\python.exe skill_human_decision_submit.py --answers decision_answers.tsv --md decision_list.md --apply
```

`apply=False` 係**預設**，因為呢度出錯嘅危險方向係「寫入」。

## Hard Rules

1. 空白答案 → 跳過，永不當空值提交
2. 非權威 key（打錯字）→ 拒絕
3. `decided_by` 係 pattern 字 → 拒絕
4. 無引用 / 假引用 → 拒絕（丟棄，唔降級）
5. 有候選而行，答案唔係候選之一 → 拒絕（覆寫需要引用，目前冇地方放）
6. `NONE` → 只寫 decision log，**永不**寫入 `skill_key_alias`
7. 空結果 → raise，永不當「冇嘢要做」
8. 唔可以由 MD 反讀答案 —— MD 只讀 contract id 做身份核對
9. `--apply` 未開之前，一個 byte 都唔寫
10. 唔可以呼叫 `apply_proposed` 做人手路徑

## Output format

TRACE_ID: [DEC-...]
ACCEPTED: [n]
REJECTED: [n]  (+ 逐條原因)
SKIPPED_BLANK: [n]
ALIAS_WRITTEN: [n]
NONE_RECORDED: [n]