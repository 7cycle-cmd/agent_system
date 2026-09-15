"""Worker 心跳上报服务。

两个循环分开行，系分辨「崩溃」同「卡死」嘅关键：

    ┌─ 业务循环（business_loop）── 真正嘅工作，每隔 BUSINESS_INTERVAL 秒
    │                              跳一次，更新 last_business_tick
    └─ 心跳线程（heartbeat_loop）─ 每隔 HEARTBEAT_INTERVAL 秒上报一次，
                                   附带「业务仲跳唔跳」嘅快照

如果业务卡死（进程未死），心跳线程照跳，只系上报 business_alive=0
  -> watchdog 见到「心跳新鲜 + 业务唔跳」= hang
如果进程崩咗，两条循环一齐消失，心跳断晒
  -> watchdog 见到「心跳过期」= crash

另外，正常收工（Ctrl-C / SIGTERM / --stop-after）会行 mark_stopping()，
落一个 status='stopping' 标记俾 watchdog，避免正常下班被误报成故障。
"""
import argparse
import datetime
import os
import signal
import sqlite3
import sys
import threading
import time

from heartbeat_screenshot import capture_heartbeat

# Windows 控制台可能是 cp950/big5，避免中文日志导致 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


DB_PATH = "agent.db"
WORKER_NAME = "openclaw_worker_01"
GROUP_NAME = "default"            # 节点组名，用于按组分配任务
HEARTBEAT_INTERVAL = 5 * 60       # 5分钟，单位秒
HEARTBEAT_RETENTION_DAYS = 7      # 心跳历史（DB 行）保留天数
MAINTENANCE_INTERVAL = 60 * 60    # 每 1 小时清理一次 DB 历史

BUSINESS_INTERVAL = 5             # 业务循环节拍，单位秒
BUSINESS_STUCK_SECONDS = 30       # 几耐冇业务 tick 就当卡死（>节拍的宽裕值）


class WorkerState:
    """跨线程共享嘅工作状态。lock 保护 last_business_tick。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.last_business_tick = time.monotonic()   # 起手就当跳过一次，避免开机即报卡死
        self.exit_event = threading.Event()

    def tick_business(self):
        with self._lock:
            self.last_business_tick = time.monotonic()

    def business_alive(self):
        """业务循环仲跳唔跳（上报俾 watchdog 嘅快照）。"""
        with self._lock:
            last = self.last_business_tick
        return (time.monotonic() - last) <= BUSINESS_STUCK_SECONDS


def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def register_worker(group_name=GROUP_NAME):
    """按 (name, group_name) 幂等登记/复用节点，返回 worker_id。"""
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM workers WHERE name = ? AND group_name = ?",
            (WORKER_NAME, group_name),
        )
        row = cur.fetchone()
        if row:
            # 已有节点：不动 status（交给 watchdog 管理），只复用 id
            worker_id = row[0]
        else:
            cur.execute(
                "INSERT INTO workers (name, status, group_name) VALUES (?, 'idle', ?)",
                (WORKER_NAME, group_name),
            )
            worker_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    print(f"worker 注册成功，worker_id={worker_id} (group={group_name})")
    return worker_id


def take_screenshot():
    """抓一次屏：覆写 latest + 写时间戳快照 + 滚动清理，返回本次快照路径。

    磁盘占用由 heartbeat_screenshot 的滚动缓冲锁死：
    latest 1 张 + 最多 MAX_SNAPSHOT_COUNT 张历史。
    """
    return capture_heartbeat()["snapshot"]


def send_heartbeat(worker_id, screenshot_path, business_alive=True):
    """追加一条心跳历史，并刷新 workers.last_seen_at。

    status 字段由 watchdog 维护（idle/offline/stopping），心跳只负责写
    last_seen_at，这样 watchdog 才能检测到 offline -> idle 的恢复。

    business_alive：呢一刻业务循环仲跳唔跳。卡死嘅 worker 都会照上报
    （只系报 0），呢个正系 hang 嘅侦测依据。
    """
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        now = datetime.datetime.now()
        # 1) 历史表：每次心跳只追加，不覆盖
        cur.execute(
            """
            INSERT INTO worker_heartbeat
                (worker_id, screenshot_path, heartbeat_at, business_alive, pid)
            VALUES (?, ?, ?, ?, ?)
            """,
            (worker_id, screenshot_path, now, 1 if business_alive else 0, os.getpid()),
        )
        # 2) 快照表：只刷新最近心跳时间，不碰 status
        cur.execute(
            "UPDATE workers SET last_seen_at = ? WHERE id = ?",
            (now, worker_id),
        )
        conn.commit()
    finally:
        conn.close()
    flag = "业务正常" if business_alive else "业务卡死"
    print(f"心跳上报 worker_id={worker_id}, screenshot={screenshot_path}, {flag}")


def mark_stopping(worker_id):
    """正常收工标记：watchdog 见到「stopping + 心跳过期」就当正常下线，唔开故障单。

    注意：只有优雅退出先会行到呢度。被 taskkill /F 杀掉、或者 assert 崩咗，
    根本冇机会写呢个标记 -> 于是 watchdog 就会正确咁判成 crash。
    """
    conn = get_db_conn()
    try:
        conn.execute("UPDATE workers SET status = 'stopping' WHERE id = ?", (worker_id,))
        conn.commit()
    finally:
        conn.close()


def purge_old_heartbeats(keep_days=HEARTBEAT_RETENTION_DAYS):
    """清理超过保留期的心跳历史（DB 行），避免表无限增长。"""
    conn = get_db_conn()
    try:
        cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
        cur = conn.cursor()
        cur.execute("DELETE FROM worker_heartbeat WHERE heartbeat_at < ?", (cutoff,))
        deleted = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    if deleted:
        print(f"已清理 {deleted} 条 {keep_days} 天前的心跳历史")
    return deleted


def business_loop(state, hang_after=None, started_at=None):
    """业务主循环。

    MVP 阶段呢度系一个占位节拍：真正嘅任务处理（由 task_queue 攞工单、跑、
    回报 pass/fail）以后喺呢个 while 里面接手，接法系叫 tick_business() 之前
    做完一轮工。

    hang_after 只系 demo/验收用：到时之后停止跳拍，模拟「业务卡死但进程唔死」。
    呢个时候心跳线程照跳，watchdog 就够睇得出系 hang 而唔系 crash。
    """
    while not state.exit_event.is_set():
        if hang_after is not None and time.monotonic() - started_at >= hang_after:
            print(f"🧪 [simulate] 业务循环卡死（心跳线程照跳，进程唔死）")
            while not state.exit_event.is_set():
                time.sleep(1)      # 卡死：唔再 tick，扮住喺度等一个永远唔返嘅调用
            return
        state.tick_business()
        time.sleep(BUSINESS_INTERVAL)


def heartbeat_loop(worker_id, state):
    """心跳线程：同业务循环同步起步，业务卡死都照跳。"""
    while not state.exit_event.is_set():
        try:
            snap_path = take_screenshot()
            send_heartbeat(worker_id, snap_path, state.business_alive())
        except Exception as e:
            # 心跳出错唔可以搞冧个循环（例如 DB 被锁一下），下次再试
            print(f"心跳出错: {e}")
        state.exit_event.wait(HEARTBEAT_INTERVAL)   # 收工信号一到即刻醒，唔使等足 5 分钟


def maintenance_loop(state):
    """定期清 DB 历史。独立线程，唔阻业务同心跳。"""
    while not state.exit_event.wait(MAINTENANCE_INTERVAL):
        try:
            purge_old_heartbeats()
        except Exception as e:
            print(f"清理心跳历史出错: {e}")


def main():
    ap = argparse.ArgumentParser(description="Worker 心跳上报服务")
    ap.add_argument("--simulate-hang-after", type=float, default=None, metavar="秒",
                    help="验收用：呢秒数之后停止业务跳拍，模拟卡死（进程唔死）")
    ap.add_argument("--stop-after", type=float, default=None, metavar="秒",
                    help="验收用：呢秒数之后优雅收工（会落 status=stopping）")
    args = ap.parse_args()

    worker_id = register_worker(GROUP_NAME)
    state = WorkerState()
    started_at = time.monotonic()

    def _on_signal(signum, frame):
        print(f"\n收到信号 {signum}，准备正常收工...")
        state.exit_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    if args.stop_after:
        t = threading.Timer(args.stop_after, state.exit_event.set)
        t.daemon = True
        t.start()

    print(f"开始循环：业务节拍 {BUSINESS_INTERVAL} 秒，心跳间隔 {HEARTBEAT_INTERVAL} 秒")
    hb = threading.Thread(target=heartbeat_loop, args=(worker_id, state), daemon=True)
    mt = threading.Thread(target=maintenance_loop, args=(state,), daemon=True)
    hb.start()
    mt.start()

    try:
        business_loop(state, args.simulate_hang_after, started_at)
    except KeyboardInterrupt:
        state.exit_event.set()
    finally:
        state.exit_event.set()
        mark_stopping(worker_id)
        hb.join(timeout=5)
        mt.join(timeout=5)
        print("已正常下线，status=stopping")


if __name__ == "__main__":
    main()
