---
task_id: "WATCHDOG.HEARTBEAT.SKILL"
name: "worker_heartbeat_service"
final_verdict: "PASS"
ingested: "yes"
modified_files: ["watchdog_health.py", "skills/4_agent/worker_heartbeat_service/contract.yaml"]
qc_summary: "Worker liveness becomes a fact about the ROW'S AGE, computed at read time. business_alive (measured distinct=1) is retired as the health signal because a dead process cannot write a row saying it is dead."
reason: "measured: business_alive distinct=1 and pid distinct=1 — a health column written by the process whose health is in question can never return 'dead'"
artifacts: ["watchdog_health.py"]
schema: "no new table — reads worker_heartbeat"
---

# WATCHDOG.HEARTBEAT.SKILL — worker_heartbeat_service

## 目的

**令 worker 嘅「在唔在生」變成一個關於「行有幾舊」嘅事實，由讀取者計。**

## 為何要改（量到）

`worker_heartbeat.business_alive` **distinct = 1**、`pid` **distinct = 1**。

呢個欄位係「垂死嘅 process 對自己嘅評價」：

```
worker_heartbeat_service.py:60    business_alive() = monotonic() - last_tick <= 30
worker_heartbeat_service.py:128   INSERT ... 1 if business_alive else 0, os.getpid()
worker_heartbeat_service.py:197   send_heartbeat(..., state.business_alive())
```

**死嘅 process 寫唔到一行話自己死咗。** 所以呢個欄位**結構上**永遠返唔到另一個值。
呢個同 `empty_detector_failure_class` 記錄嘅係同一種失誤：一個分唔到兩個狀態嘅檢測器，
會永遠報「一切正常」。

## 做法

| 舊 | 新 |
|---|---|
| 寫入者（自己）評自己 | 讀取者問 `seconds since this worker's newest heartbeat row` |
| 只能 ALIVE | `ALIVE` / `DEAD` / `NEVER` |
| `business_alive=1` 當健康 | 用**行嘅年齡**做健康 |

`business_alive` 欄位**保留**（additive，唔破壞任何嘢），但**唔再係健康訊號**。

## 唔可以做的事 (Not to do)

- **唔准**信 `business_alive` —— distinct = 1。
- **唔准**用 `0` 代替「冇行」—— 0 秒會讀成「剛剛」。
- **唔准**由一次 liveness 讀取去重啟 / 發訊號俾 worker。
- **唔准**為未宣告嘅問題發明單位。
- **唔准**刪改 `worker_heartbeat` 歷史。

## QC 檢查點

- [x] 5 秒 = ALIVE、100000 秒 = DEAD（兩個方向都可達 = positive control）
- [x] `NEVER` 唔退化成 DEAD；`seconds` 係 `null` 唔係 `0`
- [x] 未宣告嘅問題會 raise（`HealthError`），唔會亂猜單位
- [x] Reason 係 structured，交畀現有 `done_chain` 循環
- [x] 單位有名有主體
