"""Telemetry aggregators for the LLM Task Monitor "Telemetry HUD" page.

Iron rules (inherited from the watchdog trunk):
- READ-ONLY. Every SQLite connection opens with `mode=ro`. This module must
  never INSERT / UPDATE / DELETE / audit / experience.
- NEVER raise into the Flask handler. A missing table, a locked DB or a
  malformed JSON file degrades to an empty series plus an `error` string,
  so a broken metric cannot blank the whole HUD page.

Sources:
    agent.db                  -> worker_heartbeat, fault_event, skill_task_queue
    helper_watchdog_events.json -> watchdog events (kind / level)
    mouse_spot_llm_tasks.json   -> LLM token usage per model

Each metric returns the same envelope so the frontend can render any of them
with one chart factory and one modal:
    {
      "ok": bool,
      "metric": str,
      "title": str,
      "unit": str,                      # "counts" | "events" | "tokens"
      "bucket": str,                    # "hour" | "day" | "category"
      "categories": [str, ...],         # x-axis labels
      "series": [{"name": str, "data": [num, ...]}, ...],
      "totals": {...},
      "detail_rows": [dict, ...],       # feeds the drill-down page
      "error": str | None,
    }
"""
from __future__ import annotations

import json
import sqlite3
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

BASE_DIR = Path(__file__).resolve().parent
AGENT_DB_PATH = BASE_DIR / "agent.db"
WATCHDOG_EVENTS_FILE = BASE_DIR / "helper_watchdog_events.json"
LLM_TASKS_FILE = BASE_DIR / "mouse_spot_llm_tasks.json"

# Cap on detail rows so a huge table cannot blow up the API response.
MAX_DETAIL_ROWS = 500

METRIC_KEYS = (
    "heartbeat_hourly",
    "fault_daily",
    "task_by_model",
    "watchdog_by_kind",
    "llm_tokens_by_model",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now()


def _readonly_conn(path: Path = AGENT_DB_PATH) -> sqlite3.Connection | None:
    """Open agent.db read-only. Returns None when the file is missing."""
    if not path.is_file():
        return None
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _parse_ts(raw: Any) -> datetime | None:
    """Tolerate the several timestamp shapes that land in this DB.

    ALWAYS returns a naive local datetime. JSON sources (llm_tasks) write
    ISO-8601 with a trailing `Z` or an offset, which yields an AWARE datetime;
    comparing that against a naive `datetime.now()` raises TypeError. We
    normalise aware -> naive local here so every caller can compare freely.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # strip trailing Z (UTC marker) so fromisoformat can parse older pythons
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s.replace("T", " ")[:26], fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    # naive-ise: aware -> convert to local wall clock, then drop tzinfo
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _load_json_list(path: Path) -> tuple[list[dict], str | None]:
    """Load a JSON array of objects. Never raises."""
    if not path.is_file():
        return [], f"{path.name} missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:  # malformed / mid-write
        return [], f"{path.name} unreadable: {type(e).__name__}"
    if not isinstance(data, list):
        return [], f"{path.name} is not a list"
    return [r for r in data if isinstance(r, dict)], None


def _envelope(
    metric: str,
    title: str,
    unit: str,
    bucket: str,
    categories: list[str],
    series: list[dict],
    totals: dict,
    detail_rows: list[dict],
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": error is None,
        "metric": metric,
        "title": title,
        "unit": unit,
        "bucket": bucket,
        "categories": categories,
        "series": series,
        "totals": totals,
        "detail_rows": detail_rows[:MAX_DETAIL_ROWS],
        "detail_truncated": len(detail_rows) > MAX_DETAIL_ROWS,
        "error": error,
    }


def _empty(metric: str, title: str, unit: str, bucket: str, error: str) -> dict[str, Any]:
    return _envelope(metric, title, unit, bucket, [], [], {}, [], error)


# --------------------------------------------------------------------------
# 1. heartbeat counts per hour
# --------------------------------------------------------------------------

def heartbeat_hourly(hours: int = 24) -> dict[str, Any]:
    """Bar count of heartbeats per hour, split healthy vs business-stuck."""
    hours = max(1, min(int(hours or 24), 168))
    title, unit, bucket = "Heartbeats per hour", "counts", "hour"
    conn = _readonly_conn()
    if conn is None:
        return _empty("heartbeat_hourly", title, unit, bucket, "agent.db missing")
    try:
        if not _table_exists(conn, "worker_heartbeat"):
            return _empty("heartbeat_hourly", title, unit, bucket,
                          "worker_heartbeat table missing")
        rows = conn.execute(
            "SELECT heartbeat_at, business_alive, worker_id, pid "
            "FROM worker_heartbeat ORDER BY heartbeat_at DESC LIMIT 5000"
        ).fetchall()
    except Exception as e:
        return _empty("heartbeat_hourly", title, unit, bucket, f"{type(e).__name__}: {e}")
    finally:
        conn.close()

    now = _now()
    start = (now - timedelta(hours=hours - 1)).replace(minute=0, second=0, microsecond=0)
    slots: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    for i in range(hours):
        slot_ts = start + timedelta(hours=i)
        slots[slot_ts.strftime("%m-%d %H:00")] = {"alive": 0, "stuck": 0}

    detail: list[dict] = []
    for r in rows:
        ts = _parse_ts(r["heartbeat_at"])
        if ts is None or ts < start:
            continue
        key = ts.replace(minute=0, second=0, microsecond=0).strftime("%m-%d %H:00")
        if key not in slots:
            continue
        ba = r["business_alive"]
        # NULL = legacy worker that never reported the field -> treat as alive
        if ba is None or int(ba) == 1:
            slots[key]["alive"] += 1
        else:
            slots[key]["stuck"] += 1
        detail.append({
            "heartbeat_at": str(r["heartbeat_at"]),
            "bucket": key,
            "worker_id": r["worker_id"],
            "pid": r["pid"],
            "business_alive": None if ba is None else bool(ba),
        })

    categories = list(slots.keys())
    alive = [slots[k]["alive"] for k in categories]
    stuck = [slots[k]["stuck"] for k in categories]
    total_alive, total_stuck = sum(alive), sum(stuck)
    series = [
        {"name": "healthy", "data": alive},
        {"name": "business stuck", "data": stuck},
    ]
    return _envelope(
        "heartbeat_hourly", title, unit, bucket, categories, series,
        {
            "total": total_alive + total_stuck,
            "healthy": total_alive,
            "stuck": total_stuck,
            "hours": hours,
        },
        detail,
    )


# --------------------------------------------------------------------------
# 2. fault events per day
# --------------------------------------------------------------------------

def fault_daily(days: int = 14) -> dict[str, Any]:
    """Bar count of fault events per day, one series per fault_type."""
    days = max(1, min(int(days or 14), 90))
    title, unit, bucket = "Fault events per day", "events", "day"
    conn = _readonly_conn()
    if conn is None:
        return _empty("fault_daily", title, unit, bucket, "agent.db missing")
    try:
        if not _table_exists(conn, "fault_event"):
            return _empty("fault_daily", title, unit, bucket, "fault_event table missing")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(fault_event)")}
        if "detect_at" not in cols or "fault_type" not in cols:
            return _empty("fault_daily", title, unit, bucket,
                          "fault_event missing detect_at/fault_type")
        rows = conn.execute(
            "SELECT event_id, fault_type, status, detect_at FROM fault_event "
            "ORDER BY detect_at DESC LIMIT 5000"
        ).fetchall()
    except Exception as e:
        return _empty("fault_daily", title, unit, bucket, f"{type(e).__name__}: {e}")
    finally:
        conn.close()

    today = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    categories = [(start + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
    idx = {c: i for i, c in enumerate(categories)}

    by_type: "OrderedDict[str, list[int]]" = OrderedDict()
    detail: list[dict] = []
    for r in rows:
        ts = _parse_ts(r["detect_at"])
        if ts is None or ts < start:
            continue
        key = ts.strftime("%m-%d")
        if key not in idx:
            continue
        ft = str(r["fault_type"] or "unknown")
        series_data = by_type.setdefault(ft, [0] * days)
        series_data[idx[key]] += 1
        detail.append({
            "event_id": r["event_id"],
            "fault_type": ft,
            "status": r["status"],
            "detect_at": str(r["detect_at"]),
            "bucket": key,
        })

    series = [{"name": k, "data": v} for k, v in by_type.items()]
    return _envelope(
        "fault_daily", title, unit, bucket, categories, series,
        {
            "total": sum(sum(v) for v in by_type.values()),
            "by_type": {k: sum(v) for k, v in by_type.items()},
            "days": days,
        },
        detail,
    )


# --------------------------------------------------------------------------
# 3. task pass/fail per model
# --------------------------------------------------------------------------

def task_by_model(days: int = 30) -> dict[str, Any]:
    """Bar count of skill_task_queue rows per status, grouped by assigned model.

    Dimension is `assigned_model`, not worker name: skill_task_queue has no
    worker column -- the escalation pool assigns the model, so that is the
    only per-executor axis the table actually carries.
    """
    days = max(1, min(int(days or 30), 180))
    title, unit, bucket = "Task pass / fail per model", "counts", "category"
    conn = _readonly_conn()
    if conn is None:
        return _empty("task_by_model", title, unit, bucket, "agent.db missing")
    try:
        if not _table_exists(conn, "skill_task_queue"):
            return _empty("task_by_model", title, unit, bucket,
                          "skill_task_queue table missing")
        rows = conn.execute(
            "SELECT task_id, skill_id, assigned_model, status, retry_count, "
            "handoff_count, error_msg, created_at, finished_at "
            "FROM skill_task_queue ORDER BY created_at DESC LIMIT 5000"
        ).fetchall()
    except Exception as e:
        return _empty("task_by_model", title, unit, bucket, f"{type(e).__name__}: {e}")
    finally:
        conn.close()

    cutoff = _now() - timedelta(days=days)
    models: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    detail: list[dict] = []
    for r in rows:
        ts = _parse_ts(r["created_at"])
        if ts is not None and ts < cutoff:
            continue
        model = str(r["assigned_model"] or "unassigned")
        b = models.setdefault(model, {"success": 0, "failed": 0, "in_flight": 0})
        st = str(r["status"] or "pending")
        if st == "success":
            b["success"] += 1
        elif st == "failed":
            b["failed"] += 1
        else:
            b["in_flight"] += 1
        detail.append({
            "task_id": r["task_id"],
            "skill_id": r["skill_id"],
            "assigned_model": model,
            "status": st,
            "retry_count": r["retry_count"],
            "handoff_count": r["handoff_count"],
            "error_msg": r["error_msg"],
            "created_at": str(r["created_at"] or ""),
            "finished_at": str(r["finished_at"] or ""),
        })

    categories = list(models.keys())
    series = [
        {"name": "success", "data": [models[m]["success"] for m in categories]},
        {"name": "failed", "data": [models[m]["failed"] for m in categories]},
        {"name": "in flight", "data": [models[m]["in_flight"] for m in categories]},
    ]
    total_ok = sum(models[m]["success"] for m in categories)
    total_fail = sum(models[m]["failed"] for m in categories)
    return _envelope(
        "task_by_model", title, unit, bucket, categories, series,
        {
            "total": total_ok + total_fail + sum(models[m]["in_flight"] for m in categories),
            "success": total_ok,
            "failed": total_fail,
            "pass_rate": round(total_ok / (total_ok + total_fail), 4) if (total_ok + total_fail) else None,
            "days": days,
        },
        detail,
    )


# --------------------------------------------------------------------------
# 4. watchdog events by kind
# --------------------------------------------------------------------------

def watchdog_by_kind(limit: int = 300) -> dict[str, Any]:
    """Bar count of helper_watchdog events grouped by kind, coloured by level."""
    title, unit, bucket = "Watchdog events by kind", "events", "category"
    raw, err = _load_json_list(WATCHDOG_EVENTS_FILE)
    if err:
        return _empty("watchdog_by_kind", title, unit, bucket, err)
    rows = raw[: max(1, min(int(limit or 300), 5000))]

    order = ["alert", "warn", "info", "ok"]
    kinds: "OrderedDict[str, dict[str, int]]" = OrderedDict()
    detail: list[dict] = []
    for r in rows:
        kind = str(r.get("kind") or "unknown")
        level = str(r.get("level") or "info")
        b = kinds.setdefault(kind, {lv: 0 for lv in order})
        if level not in b:
            b[level] = 0
            order.append(level)
        b[level] += 1
        detail.append({
            "id": r.get("id"),
            "kind": kind,
            "level": level,
            "local_time": r.get("local_time") or r.get("ts"),
            "message": r.get("message"),
            "has_screenshot": bool((r.get("screenshot") or {}).get("ok")),
        })

    categories = list(kinds.keys())
    series = [
        {"name": lv, "data": [kinds[k].get(lv, 0) for k in categories]}
        for lv in order
    ]
    return _envelope(
        "watchdog_by_kind", title, unit, bucket, categories, series,
        {
            "total": len(rows),
            "by_kind": {k: sum(v.values()) for k, v in kinds.items()},
        },
        detail,
    )


# --------------------------------------------------------------------------
# 5. LLM tokens per model
# --------------------------------------------------------------------------

def llm_tokens_by_model(days: int = 30) -> dict[str, Any]:
    """Bar count of total tokens per model, from mouse_spot_llm_tasks.json."""
    days = max(1, min(int(days or 30), 365))
    title, unit, bucket = "LLM tokens per model", "tokens", "category"
    raw, err = _load_json_list(LLM_TASKS_FILE)
    if err:
        return _empty("llm_tokens_by_model", title, unit, bucket, err)

    cutoff = _now() - timedelta(days=days)
    models: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    detail: list[dict] = []
    for r in raw:
        ts = _parse_ts(r.get("started_at") or r.get("ended_at"))
        if ts is not None and ts < cutoff:
            continue
        mid = str(r.get("model") or "unknown")
        b = models.setdefault(mid, {
            "model": mid,
            "label": str(r.get("model_label") or mid),
            "count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        })
        pt = int(r.get("prompt_tokens") or 0)
        ct = int(r.get("completion_tokens") or 0)
        tt = int(r.get("total_tokens") or 0) or (pt + ct)
        b["count"] += 1
        b["prompt_tokens"] += pt
        b["completion_tokens"] += ct
        b["total_tokens"] += tt
        detail.append({
            "id": r.get("id"),
            "task": r.get("task"),
            "model": mid,
            "started_at": r.get("started_at"),
            "prompt_tokens": pt,
            "completion_tokens": ct,
            "total_tokens": tt,
            "status": r.get("status"),
        })

    categories = [models[m]["label"] for m in models]
    series = [
        {"name": "prompt", "data": [models[m]["prompt_tokens"] for m in models]},
        {"name": "completion", "data": [models[m]["completion_tokens"] for m in models]},
    ]
    return _envelope(
        "llm_tokens_by_model", title, unit, bucket, categories, series,
        {
            "total": sum(models[m]["total_tokens"] for m in models),
            "runs": sum(models[m]["count"] for m in models),
            "by_model": list(models.values()),
            "days": days,
        },
        detail,
    )


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

_METRICS: dict[str, Callable[[], dict[str, Any]]] = {
    "heartbeat_hourly": heartbeat_hourly,
    "fault_daily": fault_daily,
    "task_by_model": task_by_model,
    "watchdog_by_kind": watchdog_by_kind,
    "llm_tokens_by_model": llm_tokens_by_model,
}


def build_metric(metric: str, **kwargs: Any) -> dict[str, Any]:
    """Build one metric envelope. Unknown metric -> ok=False (never raises)."""
    fn = _METRICS.get(metric)
    if fn is None:
        return _empty(
            metric or "unknown", metric or "unknown", "counts", "category",
            f"unknown metric: {metric}",
        )
    try:
        return fn(**kwargs)
    except Exception as e:  # belt-and-braces: handler must never see a raise
        return _empty(metric, metric, "counts", "category", f"{type(e).__name__}: {e}")


def build_all(**kwargs: Any) -> dict[str, Any]:
    """All 5 metrics for the initial page paint."""
    metrics = {m: build_metric(m, **kwargs) for m in METRIC_KEYS}
    failed = [k for k, v in metrics.items() if not v.get("ok")]
    return {
        "ok": True,  # page-level: always render; per-metric ok/error is on the card
        "metrics": metrics,
        "failed": failed,
        "generated_at": _now().strftime("%Y-%m-%d %H:%M:%S"),
    }


if __name__ == "__main__":
    print(json.dumps(build_all(), indent=2, ensure_ascii=False, default=str))

# object_door: kind-agnostic by definition (no DDL in this file)
