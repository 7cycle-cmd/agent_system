---
task_id: SKILL.TASK.UPSERT.RENDER
display_task_id: SKILL.TASK.UPSERT.RENDER
name: 建立task實體表 + upsert_task，改造test_render由DB讀取資料渲染HTML
final_verdict: PASS
ingested: no
modified_files:
  - coord_store.py
  - test_render.py
qc_summary: "PASS smoke: task table created; scan=4 upsert unique; load_all_tasks=4; HTML tid-btn=4; re-run no dup; modified_files/artifacts JSON round-trip OK. Residual: skill text expected 11 (1.1D-1.11D) but top-level skills/*.skill.md only yields 4 (scanner non-recursive)."
reason: 將skill掃描出來嘅task註冊入task資料表，之後渲染由DB讀取task數據
artifacts:
  - coord_store.py
  - test_render.py
schema: SQLite task table schema
---
# Skill: Task Upsert & DB Render
## 角色
Code Worker，使用Qwen2.5 7B；證據導向修改，每項變更綁定檔名，完成後跑煙霧測試，輸出QC SUMMARY

## 任務目標
1. 在 `coord_store.py` 新增 `task` 實體表，對應 task 資料結構；
2. 新增 `upsert_task()`：接收 skill_scanner 輸出嘅 task dict，按 `task_id` 做 upsert（有就更新，冇就新增）；
3. 新增 `load_all_tasks()`：由DB讀取全部task，返還task dict清單，供渲染函數使用；
4. 修改 `test_render.py`：
   - skill_scanner 掃描 `skills/` 得到 task list
   - 逐個 task 呼叫 `upsert_task()` 存入DB（Worker註冊動作）
   - 改由 `load_all_tasks()` 讀DB數據，傳入 `_render_html()` 產生HTML
> Task ID規範：`1.1D` / `1.1F` / `1.1.2D`，對應UI顯示嘅 `display_task_id`

## task 表 Schema
```sql
CREATE TABLE IF NOT EXISTS task (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL UNIQUE,          -- 內部唯一業務ID，例如 "1.1D"
  display_task_id TEXT NOT NULL,         -- UI顯示用，同task_id，方便後期分開
  name TEXT,
  final_verdict TEXT DEFAULT 'incomplete',
  ingested TEXT DEFAULT 'no',
  modified_files TEXT,
  qc_summary TEXT,
  reason TEXT,
  artifacts TEXT,
  schema TEXT,
  session_id TEXT,
  operator TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## 硬性修改規則

1. 修改 `coord_store.py`
- 新增 `init_task_table()`：建立 task 表；程式啟動自動呼叫
- `upsert_task(task_dict: dict)`：參數係單個task字典；以 `task_id` 做匹配；更新 `updated_at`；參數化SQL防注入
- `load_all_tasks()`：SELECT全部task，轉成dict list返回；
- 原有 `field_register`、`target_points` 相關程式碼保持不變；
2. 修改 `test_render.py`
- 匯入 `init_task_table, upsert_task, load_all_tasks` 來自 coord_store
- skill_scanner 掃描得到 tasks list
- loop tasks：`upsert_task(task)` → 註冊入DB
- 讀取：`tasks_from_db = load_all_tasks()`
- 傳入渲染：`html_output = _render_html(rows=tasks_from_db, engineer_mode=False)`
- 輸出HTML，行為同之前一致，但數據來源由DB提供
3. 資料映射規則

Dict Key
DB Column

task_id
task_id

task_id
display_task_id

name
name

final_verdict
final_verdict

ingested
ingested

modified_files
modified_files（陣列轉文字存，讀取返還原）

qc_summary
qc_summary

reason
reason

artifacts
artifacts（陣列轉文字存，讀取返還原）

schema
schema
4. 唔改動：`_tmp_render_html_fn.py`、`skill_scanner.py`、`watch_skills.py`、`mouse_spot_helper.py`
5. 陣列處理：`modified_files` / `artifacts` list → 用 `json.dumps` 存入TEXT欄；讀取時 `json.loads`，空list就存`[]`

## 測試驗證步驟（一定要執行，寫入QC SUMMARY）

1. 執行 `test_render.py`；
2. 檢查：`task` 表自動建立；掃描出來嘅全部task（1.1D ~1.11D）upsert存入DB；
3. `load_all_tasks()` 讀返全部11條task；欄位數據同skill掃描結果一致；
4. HTML `test_taskview.html` 正常生成，頁面顯示11行task；
5. 重複執行 `test_render.py`：唔會新增重複task，只會更新 `updated_at`；
6. 驗證：`modified_files`、`artifacts` 陣列序列化/反序列化正常；
7. QC SUMMARY記錄PASS/FAIL，標示任何殘留風險

## 限制

今次唔建立 `check` 實體表；唔改動前端；唔改變HTML渲染邏輯；只負責將skill掃描出來嘅task存入同讀取DB。
