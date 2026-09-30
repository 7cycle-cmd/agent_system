---
task_id: SKILL.NO.NULL.STANDARD
display_task_id: SKILL.NO.NULL.STANDARD
name: no_null_standard
catalog_id: 1
subcatalog_id: 1
final_verdict: "INCOMPLETE"
ingested: "no"
modified_files: []
qc_summary: 空值唔可以係 NULL — 一律標準化成 NA，令 NULL 永遠係缺陷而唔係歧義
reason: NULL 可以係「唔適用」「未知」「未決定」「bug」四個意思；標準化成 NA 之後，NULL 就只淨返一個意思：標準化器冇跑
artifacts:
  - no_null_standard.skill.md
schema: "result_yes_no"
---

# Skill: no_null_standard

**Goal:** 空值**永遠唔可以係 NULL**。空值一律標準化成 `NA`，
令 **NULL 永遠係一個缺陷**（標準化器冇跑），而唔係一個意思不明嘅狀態。

Package path: `skills/1_core/no_null_standard/`
可執行：`no_null.py`
Proof: `_proof_no_null.py`（35/0）

## 為什麼要有呢個 skill

NULL 係**歧義**。佢可以係：

| 意思 | 係唔係決定 |
|---|---|
| 「呢個欄位唔適用」 | ✅ 一個決定 |
| 「未知」 | ✅ 一個決定 |
| 「未決定」 | ❌ 一個缺席 |
| 「bug」 | ❌ 一個缺陷 |

**四個意思，一個符號。** 標準化成 `NA` 之後，`NA` 係**系統定義狀態**，
而 **NULL 就只淨返一個意思：標準化器冇跑** —— 即係一個**可偵測嘅缺陷**。

## 兩個實測事實，決定咗規則嘅形狀

### 事實 1 — `NA` 係字串，**INTEGER 欄位裝唔落**

實測：**69 個 INTEGER 欄位**有 NULL。一刀切規則只有兩條路：

| 做法 | 代價 |
|---|---|
| 全部改 TEXT | 失去數值比較／排序／算術；**所有指向佢嘅 FK 斷** |
| 靜靜跳過 | **規則喺最需要嘅地方唔適用** —— 正正係要移除嘅缺陷 |

所以規則**按型別分形**：TEXT 用 `'NA'`，INTEGER 用 `NA_INT = -1`。

### 事實 2 — `NA` 同 NULL 係**唔同嘅聲稱**，而普查證明佢有分別

```
NA   = 「呢個欄位唔適用於呢一行」   （一個決定）
NULL = 「冇人填過」                 （一個缺席）
```

**實測：最大嘅 NULL 族群係外鍵。**

```
task_ssot.parent_dim_id              743
chat_main_hash.chat_main_id          468
version_register.parent_version_id   190
dev_task.queue_task_id               160
skill_prompt_ssot.parent_id           75
```

外鍵嘅 NULL 意思係「**呢一行冇父行**」—— 係一個**決定**，唔係缺席。
寫 `NA` 會聲稱父行「不適用」；強制 sentinel FK 會造出**幻影父行**。

**所以外鍵係唯一准許 NULL 嘅地方，而且係明文准許，唔係容忍。**

## 三個狀態

$$\text{真值} \quad|\quad \text{NA（已決定：不適用）} \quad|\quad \text{NULL（缺陷）}$$

**`NA` 必須寫出，永不推斷。** 一個會猜嘅標準化器會將**缺席**變成**決定** ——
同 `member_id`（為咗格唔空而填值）同 11 個 derived slug **同一家族**。

## 閘（Gate）

```python
standardize_empty(value, kind)      # 空 -> 該型別嘅 NA；未知 kind 拒絕
assert_no_null(row, table, conn=)   # 寫入點：非 FK 欄位有 NULL -> raise
assert_na_is_explicit(value, kind)  # 空值到達寫入點 -> raise（要先標準化）
```

**未知 kind 一律拒絕，永不猜。** 猜一個 kind 就係將缺席變成決定。

## 審計

```python
audit(conn)   # 分開 defect 同 allowed，因為合併咗就分唔清決定同 bug
```

**實測基線：**

```
defect  NULLs  19139  喺 200 個欄位   ← 缺陷
allowed NULLs   2466  喺  36 個欄位   ← 外鍵「無引用」，係決定
```

## Hard Rules

1. 空值 → 標準化成 `NA`（TEXT）或 `NA_INT = -1`（INTEGER），**永不留 NULL**
2. 未知 kind → **拒絕**，永不猜
3. 非 FK 欄位有 NULL → **raise**
4. FK 欄位 NULL → **准許**，意思係「無引用」（一個決定）
5. `NA` 必須**寫出**，永不推斷
6. 真值**永不改寫**（`0` 同 `False` **唔係**空值）
7. FK 欄位清單**由 schema 讀**，唔可以手寫
8. 審計**分開** defect 同 allowed，唔可以合併
9. 例外（`allow`）必須喺**呼叫點可見**
10. 唔准為咗「格唔空」而填一個值 —— 咁係 `member_id` 缺陷

## Output format

DEFECT_NULLS: [n]  in [n] columns
ALLOWED_FK_NULLS: [n]  in [n] columns
STANDARDIZED: [n]
REFUSED: [n]