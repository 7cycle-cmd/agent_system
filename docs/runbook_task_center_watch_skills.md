# Runbook — Task Center + watch_skills 營運手冊

日期: 2026-09-18
狀態: 定稿 (Step 5)

---

## 1. 系統架構

```
┌─────────────────────────────────────────────────┐
│  mouse_spot_helper.py  (:18765)                 │
│  - /api/task_center/*  (submit/report/coding/    │
│     graph/task/template)                        │
│  - /llm-tasks/devtask-view  (靜態儀表板)         │
│  - 完全獨立 process，唔知 watcher 存在           │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│  watch_skills.py  (獨立 process)                │
│  - 監控 ./skills/*.skill.md 變動               │
│  - 變動 → sync_skills_to_dev_tasks() (冪等)    │
│  - 變動 → 重跑 test_render.py (渲染 HTML)       │
│  - 心跳: watch_skills_heartbeat.txt (每 60s)    │
│  - PID: watch_skills.pid                        │
└─────────────────────────────────────────────────┘
```

**關鍵：** helper 同 watcher 係**完全獨立 process**，冇互相監測、冇連線。Helper 重啟唔會影響 watcher。

---

## 2. 啟動 / 停止 / 重啟程序

### 啟動 watcher（推薦用 supervisor）
```
start_watch_skills_supervisor.bat
```
- 自動重啟 loop（最多 5 次，3s 延遲）
- 內建 `PYTHONIOENCODING=utf-8`（解決 cp950 emoji crash）

### 啟動 watcher（直接，唔用 supervisor）
```
set PYTHONIOENCODING=utf-8
.\.venv\Scripts\python.exe watch_skills.py
```

### 停止 watcher
```
# 用 PID file
$pid = Get-Content watch_skills.pid
Stop-Process -Id $pid -Force
# 或
Get-Process -Name python* | Where-Object { $_.CommandLine -like '*watch_skills*' } | Stop-Process -Force
```

### 重啟 watcher
```
# 停止 → 再啟動（見上）
```

### 啟動 helper
```
start_mouse_spot_helper.bat
```

---

## 3. 健康檢查

### Watcher 健康（3 個指標）
| 指標 | 方法 | 健康標準 |
|------|------|---------|
| 心跳 | `watch_skills_heartbeat.txt` | timestamp < 120s = 生 |
| PID | `watch_skills.pid` | process 存在 |
| 錯誤 | `watch_skills_err.log` | 空 = 無錯誤 |

**健康檢查規則：** `watch_skills_heartbeat.txt` 嘅 timestamp 距今 < 120s（2 個 heartbeat interval）= 生。如果 > 120s = 死咗。

### Helper 健康
```
http://127.0.0.1:18765/api/system-status
```

---

## 4. 已知限制（重要）

| # | 限制 | 緩解 |
|---|------|------|
| 1 | **Sync 唔係 atomic** — `create_dev_task` 硬 commit，非 duplicate 中途 fail 會留部分記錄 | 每次 sync 後 log `{created, skipped, total}`，異常時人手對賬 |
| 2 | **並發撞鎖** — watcher + worker 同時寫 → `database is locked` | `_run_with_retry`（3 次，1s/2s/4s backoff） |
| 3 | **`_run_with_retry` 返回 `None`** — 3 次失敗後 | 外層 log warning「同步失敗（重試 3 次後仍失敗）」，唔會誤判成功 |
| 4 | **cp950 emoji crash** — watcher 用 emoji print，Windows cp950 頂唔住 | 啟動要 `PYTHONIOENCODING=utf-8` |
| 5 | **刪 skill 唔刪 dev_task** — sync 只新增，唔刪除 | 刪 skill 後要人手清 dev_task，否則 ghost |
| 6 | **雙重 sync** — watcher → test_render → sync | 純冗餘（已驗無害，順序執行） |
| 7 | **Helper/watcher 解耦** — watcher 死亡無人知 | 靠心跳（< 120s = 生） |
| 8 | **Supervisor 5 次用盡** — 連續死 6 次 | log「GAVE UP after 5 restarts」+ exit code 1，等人手介入 |
| 9 | **Supervisor 自己死** — `.bat` 被 kill | 接受風險（P2），或用 Windows Task Scheduler 做第二層 |

---

## 5. Rollback 程序

如果 sync 搞爛 DB（例如錯誤建立大量 dev_task）：

### 5.1 停止 watcher（防止再寫）
```
Get-Process -Name python* | Where-Object { $_.CommandLine -like '*watch_skills*' } | Stop-Process -Force
```

### 5.2 備份 DB
```
copy agent.db agent.db.bak_YYYYMMDD
```

### 5.3 刪除錯誤記錄
```python
import sqlite3
from db_schema import get_db_path
c = sqlite3.connect(get_db_path())
# 刪除特定 task_label（例如 TEST- 前綴）
c.execute("DELETE FROM dev_task WHERE task_label LIKE 'TEST-%'")
c.commit()
c.close()
```

### 5.4 重啟 watcher
```
start_watch_skills_supervisor.bat
```

---

## 6. P2 Backlog

| # | 項目 | 描述 | 狀態 |
|---|------|------|------|
| 1 | **Orphan 檢測** | skill 檔唔存在但 dev_task 仍在 → log warning（唔自動刪） | ✅ 已做（`sync_skills_to_dev_tasks` 加 orphan 檢測，跳過純數字 ID 如 10.21） |
| 2 | **移除雙重 sync** | watcher 或 test_render 其中一層 sync 冗餘，簡化架構 | ✅ 已做（`test_render.py --no-sync`；`watch_skills.py` Step2 用 `--no-sync`） |
| 3 | **Supervisor 第二層** | Windows Task Scheduler 定期檢查心跳，supervisor 死咗自動重啟 | ✅ 已做（`watch_skills_guardian.py`） |
| 4 | **移除 emoji** | 用 `sys.stdout.reconfigure(encoding='utf-8')` 或移除 emoji，根治 cp950 | ✅ 已做（`watch_skills.py` 加 reconfigure） |

### Guardian（第二層 supervisor）用法

```bat
:: 檢查（唔重啟）—— exit 0 健康 / 1 過期 / 2 無心跳檔
.venv\Scripts\python.exe watch_skills_guardian.py --check

:: 檢查 + 過期自動重啟 supervisor
.venv\Scripts\python.exe watch_skills_guardian.py
```

Windows Task Scheduler 設定：每 5 分鐘跑 `watch_skills_guardian.py`（無 `--check`），心跳過期（>120s）自動重啟 `start_watch_skills_supervisor.bat`。

---

## 7. 快速參考

| 動作 | 指令 |
|------|------|
| 啟動 watcher | `start_watch_skills_supervisor.bat` |
| 停止 watcher | `Stop-Process -Id (Get-Content watch_skills.pid) -Force` |
| 健康檢查 | 睇 `watch_skills_heartbeat.txt` timestamp |
| 睇 log | `Get-Content watch_skills.log` |
| 睇錯誤 | `Get-Content watch_skills_err.log` |
| 重跑 sync | `.\.venv\Scripts\python.exe test_render.py` |
| 跑測試 | `.\.venv\Scripts\python.exe test_sync_skills_to_dev_tasks.py` |
| 跑生命週期測試 | `.\.venv\Scripts\python.exe test_task_lifecycle.py` |