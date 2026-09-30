"""Soak long-duration stability stress test.

OpenClaw + Playwright, runs in the background, periodically samples
/api/watchdog/events metrics. Outputs a JSONL metric log + final summary report.

Usage:
    python soak_runner.py                 # full run (6h default)
    python soak_runner.py --duration 60   # short smoke run (60s)
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path

# ========== Config (tunable) ==========
TARGET_URL = "http://127.0.0.1:18765"
DURATION_SECONDS = 6 * 3600    # 6h soak
CONCURRENT_WORKERS = 4
SAMPLE_INTERVAL = 30           # sample monitor metrics every 30s
METRIC_LOG_PATH = Path("./soak_metrics.jsonl")
CRITICAL_THRESHOLD = {
    "db_lock_count": 5,
    "slow_query_count": 20,
}
# ======================================

stop_event = threading.Event()


def collect_watchdog_metrics() -> dict:
    """Pull /api/watchdog/events; return db_health, rate_limit, alerts."""
    try:
        req = urllib.request.Request(f"{TARGET_URL}/api/watchdog/events?limit=1")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


def worker_loop(worker_id: int) -> None:
    """Single concurrent worker: loop visiting the page, simulating a user."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[w{worker_id}] playwright unavailable: {e}")
        return
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        while not stop_event.is_set():
            try:
                page.goto(
                    f"{TARGET_URL}/llm-tasks/watchdog",
                    wait_until="networkidle",
                    timeout=15000,
                )
                page.wait_for_timeout(800)
            except Exception:
                pass
            time.sleep(2)
        browser.close()


def metric_collector() -> None:
    """Periodically sample metrics, write JSONL, check alert thresholds."""
    while not stop_event.is_set():
        ts = time.time()
        data = collect_watchdog_metrics()
        record = {"ts": ts, "iso": time.ctime(), "data": data}
        with METRIC_LOG_PATH.open("a", encoding="utf8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        dbh = data.get("db_health", {})
        if dbh.get("db_lock_count", 0) >= CRITICAL_THRESHOLD["db_lock_count"]:
            print(f"[CRITICAL] DB LOCK COUNT HIGH: {dbh['db_lock_count']}")
        if dbh.get("slow_query_count", 0) >= CRITICAL_THRESHOLD["slow_query_count"]:
            print(f"[WARNING] Slow queries accumulating: {dbh['slow_query_count']}")

        time.sleep(SAMPLE_INTERVAL)


def generate_summary() -> None:
    """Simple summary report (pandas if available, else plain python)."""
    if not METRIC_LOG_PATH.exists():
        print("No metric log")
        return
    rows = []
    for line in METRIC_LOG_PATH.open("r", encoding="utf8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    if not rows:
        print("Metric log empty")
        return
    print(f"Total samples: {len(rows)}")
    print("Last record:", rows[-1])

    # Aggregate db_health / rate_limit over the run
    db_locks = [r["data"].get("db_health", {}).get("db_lock_count", 0) for r in rows]
    slow = [r["data"].get("db_health", {}).get("slow_query_count", 0) for r in rows]
    errors = [r for r in rows if "error" in r["data"]]
    print(f"Max db_lock_count: {max(db_locks) if db_locks else 0}")
    print(f"Max slow_query_count: {max(slow) if slow else 0}")
    print(f"Sample errors: {len(errors)}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Soak stress test")
    parser.add_argument("--duration", type=int, default=DURATION_SECONDS,
                        help="duration in seconds")
    parser.add_argument("--workers", type=int, default=CONCURRENT_WORKERS)
    args = parser.parse_args()

    METRIC_LOG_PATH.unlink(missing_ok=True)
    print(f"Start Soak test, duration: {args.duration / 3600:.1f}h, "
          f"workers={args.workers}")

    workers = []
    for wid in range(args.workers):
        t = threading.Thread(target=worker_loop, args=(wid,), daemon=True)
        t.start()
        workers.append(t)

    metric_t = threading.Thread(target=metric_collector, daemon=True)
    metric_t.start()

    end_at = time.time() + args.duration
    while time.time() < end_at and not stop_event.is_set():
        time.sleep(1)

    stop_event.set()
    print("Soak test finished, generating summary report...")
    generate_summary()


if __name__ == "__main__":
    main()