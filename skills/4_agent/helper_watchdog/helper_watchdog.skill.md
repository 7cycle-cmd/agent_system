---
task_id: "WATCHDOG.HEARTBEAT.SKILL"
name: "helper_watchdog"
final_verdict: "PASS"
ingested: "yes"
modified_files: ["watchdog_health.py", "skills/4_agent/helper_watchdog/contract.yaml", "skill_tdd_runner.py"]
qc_summary: "Watchdog liveness becomes a MEASURED unit (seconds since the newest watchdog_log row), derived at read time so a dead process cannot author it. Duplicate helpers become an ALARM with a unit, never a kill. business_alive retired as the health signal (measured distinct=1)."
reason: "user: apply skill for watchdog or heartbeat to make it work again and become stronger. measured: 10 days silent, no process, no alarm, liveness signal was a constant"
artifacts: ["watchdog_health.py", "_proof_watchdog_health.py"]
schema: "no new table — reads watchdog_log / worker_heartbeat"
---

# WATCHDOG.HEARTBEAT.SKILL

## 目的

**令 watchdog 自己嘅「在唔在生」變成一個量得到嘅單位。**

用戶原話：

> why i think we can apply skill for watchdog or heartbeat to make it work again
> and become stronger

## 量到嘅問題（2026-09-24）

| 量度 | 結果 |
|------|------|
| `watchdog_log` 最新一行 | **2026-09-14 11:46:17** → 靜咗 10 日 |
| `worker_heartbeat` 最新心跳 | **2026-09-14 11:35:11** |
| 現時有冇 watchdog process | **冇**；只有兩個 `mouse_spot_helper.py`（PID 20812 / 24080） |
| `Get-ScheduledTask` | 只有 `SpaceAgentTask`，**冇任何嘢**啟動 watchdog |
| `worker_heartbeat.business_alive` distinct | **1**（常數，唔係量度） |
| `route_register` 有關 helper/watchdog/worker | **0 條** |
| `skill_factor_register` 提到 watch/health/restart | **0 行** |

## 功能

1. **Liveness 單位** — `seconds since the newest <table> row for this worker`，
   **讀取時用 query 推導**。死嘅 process 寫唔到一行話自己死咗，所以由被量度者
   以外嘅人去計，就係修正。
2. **三個狀態** — `ALIVE` / `DEAD` / `NEVER`。`NEVER`（從來冇寫過）同 `DEAD`
   （停咗）係唔同故障，唔可以合併。
3. **Duplicate helper 警報** — 單位 `count of live mouse_spot_helper.py processes`。
4. **結構化 reason** — `{ring, unit, observed, expected}`，直接餵入今天已建好嘅
   `done_chain` 循環（factor proposal + worked example），唔另寫收集器。

## 流程

1. 問 liveness 問題 → `freshness(conn, name, worker_id)` → `{state, seconds, unit, why}`
2. 全部問題一齊報 → `diagnose()`（唔會 raise，每個都報，包括過關嘅）
3. 數 helper → `duplicate_helper_alarm()` → 超過 1 就 fire
4. 有問題 → `alarm_reason()` → structured → 交 `done_chain`

## 唔可以做的事 (Not to do)

- **唔准**殺、重啟、spawn 任何 process。`helper_watchdog.py:651` 明確記錄：
  喺 `adopt` 殺 helper **造成假警報**。呢個約束用 AST 檢查斷言（`_assert_no_kill_path`），
  唔係靠口頭承諾。
- **唔准**用 `business_alive` 做健康訊號 —— distinct = **1**，係常數。
- **唔准**將 `NEVER` 當 `DEAD` —— 唔同故障。
- **唔准**寫冇主體嘅單位（`seconds` 單獨一個字查唔到嘢）。
- **唔准**斷言 live 人口嘅**確切數字**（要斷言 property）。
- **唔准**加一個冇 positive control 嘅檢查。

## QC 檢查點

- [x] 單位有名有主體（QC-02）
- [x] **Positive control**：同一個函數 fresh→ALIVE、stale→DEAD（QC-03）
- [x] `NEVER` 唔會退化成 DEAD，`seconds` 係 null 唔係 0（QC-10）
- [x] Duplicate helper 會 fire，而且**冇**任何 kill call（QC-08）
- [x] Reason 係 structured，直接餵入現有循環（QC-09）
- [x] 冇斷言 live 人口嘅確切數字（QC-12）
- [x] 只喺 COPY 上跑 proof（QC-11）
