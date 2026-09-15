"""P2 边界校验：分辨「崩溃（crash）」同「卡死（hang）」，并令「正常下线」唔开假单。

核心思路：心跳线程同业务循环分开跑。
  心跳过期                -> 进程冇咗            -> crash
  心跳仍在但业务连续唔跳  -> 进程活住、业务卡死 -> hang
  心跳过期 + status=stopping -> 主动收工         -> 正常下线，唔开故障单

覆盖清单：
  1) 健康：心跳新鲜 + 业务正常            -> 无事件
  2) 卡死：心跳新鲜 + 连续 2 条业务唔跳   -> 一条 hang 事件，证据正常
  3) 卡死唔会重复开单                     -> 再扫一次，事件数不变
  4) 卡死恢复：业务返返嚟                 -> 状态回 idle + 恢复日志
  5) 迟滞：只 1 条业务唔跳                -> 唔开单（防抖动误报）
  6) 向后兼容：business_alive=NULL（旧数据）-> 当健康，唔误报卡死
  7) 崩溃：心跳过期 + 最后仍报业务正常    -> crash 事件
  8) 正常下线：心跳过期 + status=stopping -> 冇故障事件，只有 INFO 日志
  9) 从未上报：last_seen_at=NULL          -> heartbeat_timeout（原语义保留）
 10) 崩溃后重启上报                      -> 回 idle，唔会重复开单

跑法：python -X utf8 p2_boundary_test.py --yes-wipe
"""
import datetime
import shutil
import sqlite3
import sys
from pathlib import Path

import create_db
import worker_heartbeat_service as s
import watchdog

DB = "agent.db"
SNAP = Path("hb_snapshots")
EVID = Path("fault_evidence")

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(("  ✅ PASS " if cond else "  ❌ FAIL ") + name + (f"   [{detail}]" if detail else ""))


# ------------------------------------------------------------
# 夹具：直接写 DB 模拟各种心跳形态（唔使真等 5 分钟）
# ------------------------------------------------------------

def reset_db():
    shutil.rmtree(SNAP, ignore_errors=True)
    shutil.rmtree(EVID, ignore_errors=True)
    create_db.create_database()


def clear_state(status="idle", last_seen="keep"):
    """清空心跳史 + 故障事件 + 日志，并把 worker 快照设成指定状态。

    每个用例都要由零开始数事件，所以连 fault_event / watchdog_log 一齐清，
    否则上一个用例开出嘅单会污染下一个用例嘅 count。

    last_seen="keep"  -> 保留（畀后续 beat() 覆写）
    last_seen=None    -> 设成 NULL，模拟「从未上报过」
    """
    c = sqlite3.connect(DB)
    c.execute("DELETE FROM worker_heartbeat")
    c.execute("DELETE FROM fault_event")
    c.execute("DELETE FROM watchdog_log")
    if last_seen == "keep":
        c.execute("UPDATE workers SET status = ? WHERE id = 1", (status,))
    else:
        c.execute("UPDATE workers SET status = ?, last_seen_at = ? WHERE id = 1",
                  (status, last_seen))
    c.commit()
    c.close()


def beat(alive, minutes_ago):
    """写一条心跳，可指定 business_alive（1/0/None）同发生时间。"""
    t = datetime.datetime.now() - datetime.timedelta(minutes=minutes_ago)
    c = sqlite3.connect(DB)
    c.execute(
        "INSERT INTO worker_heartbeat (worker_id, screenshot_path, heartbeat_at, business_alive, pid) "
        "VALUES (1, ?, ?, ?, 4242)",
        (f"sim_{minutes_ago}min.png", t.isoformat(sep=" "), alive),
    )
    c.execute("UPDATE workers SET last_seen_at = ? WHERE id = 1", (t.isoformat(sep=" "),))
    c.commit()
    c.close()


def events():
    c = sqlite3.connect(DB)
    r = c.execute(
        "SELECT event_id, fault_type, status, evidence_img_path, evidence_error "
        "FROM fault_event ORDER BY event_id"
    ).fetchall()
    c.close()
    return r


def worker_row():
    c = sqlite3.connect(DB)
    r = c.execute("SELECT status, last_seen_at FROM workers WHERE id = 1").fetchone()
    c.close()
    return r


def logs(level):
    c = sqlite3.connect(DB)
    r = [x[0] for x in c.execute(
        "SELECT message FROM watchdog_log WHERE level = ? ORDER BY id", (level,))]
    c.close()
    return r


print("=" * 60)
print("P2 边界校验开始（崩溃 vs 卡死）")
print("=" * 60)

if "--yes-wipe" not in sys.argv:
    print("⚠️  本测试会重建 agent.db，并清空 hb_snapshots/ 同 fault_evidence/。")
    print("    只可用于开发/一次性验收环境。确认要跑就加参数：")
    print("    python -X utf8 p2_boundary_test.py --yes-wipe")
    sys.exit(1)

reset_db()
wid = s.register_worker()
s.take_screenshot()          # 造一张 latest 快照，令证据复制有嘢可用

# ---------------- 1) 健康 ----------------
print("\n[1] 健康：心跳新鲜 + 业务正常 -> 无事件")
clear_state()
beat(1, 6)
beat(1, 1)
watchdog.check_workers()
check("健康：冇开故障单", len(events()) == 0, f"事件数={len(events())}")
check("健康：worker 保持 idle", worker_row()[0] == "idle", f"status={worker_row()[0]}")

# ---------------- 2) 卡死 ----------------
print("\n[2] 卡死：心跳新鲜 + 连续 2 条业务唔跳 -> hang")
clear_state()
beat(0, 6)
beat(0, 1)                   # 心跳仲跳，只系报「业务卡死」
watchdog.check_workers()
ev = events()
check("卡死：开出 1 条事件", len(ev) == 1, f"事件数={len(ev)}")
check("卡死：fault_type=hang", ev and ev[0][1] == "hang", f"type={ev[0][1] if ev else None}")
check("卡死：worker 置 offline", worker_row()[0] == "offline", f"status={worker_row()[0]}")
check("卡死：有 ERROR 告警日志", any("业务卡死" in m for m in logs("ERROR")),
      str(logs("ERROR"))[:100])
check("卡死：证据正常冻结（latest 可用）",
      ev and ev[0][3] is not None and ev[0][4] is None,
      f"path={ev[0][3] if ev else None} err={ev[0][4] if ev else None}")

# ---------------- 3) 卡死唔重复开单 ----------------
print("\n[3] 卡死唔会重复开单 -> 再扫两次")
n_before = len(events())
watchdog.check_workers()
watchdog.check_workers()
check("唔重复：事件数不变", len(events()) == n_before, f"{n_before} -> {len(events())}")

# ---------------- 4) 卡死恢复 ----------------
print("\n[4] 卡死恢复：业务返返嚟 -> 回 idle")
n_before = len(events())
beat(1, 1)                   # 最近两条变返 1,0 -> 唔再算卡死；再补一条变 1,1
beat(1, 0)
watchdog.check_workers()
check("恢复：状态回 idle", worker_row()[0] == "idle", f"status={worker_row()[0]}")
check("恢复：写咗 INFO 恢复日志", any("心跳恢复" in m for m in logs("INFO")),
      str(logs("INFO"))[:100])
check("恢复：唔会额外开单", len(events()) == n_before, f"{n_before} -> {len(events())}")

# ---------------- 5) 迟滞：只 1 条业务唔跳 ----------------
print("\n[5] 迟滞：只 1 条业务唔跳 -> 唔开单（防抖）")
clear_state()
beat(1, 6)
beat(0, 1)                   # 只有最近一条报卡死，未够连续 2 条
watchdog.check_workers()
check("迟滞：唔开单", len(events()) == 0, f"事件数={len(events())}")

# ---------------- 6) 向后兼容：NULL ----------------
print("\n[6] 向后兼容：business_alive=NULL（旧版数据）-> 当健康")
clear_state()
beat(None, 6)
beat(None, 1)
watchdog.check_workers()
check("旧数据：唔误报卡死", len(events()) == 0, f"事件数={len(events())}")
check("旧数据：worker 保持 idle", worker_row()[0] == "idle", f"status={worker_row()[0]}")

# ---------------- 7) 崩溃 ----------------
print("\n[7] 崩溃：心跳过期 + 最后一次仍报业务正常 -> crash")
clear_state(status="idle")
beat(1, 25)
beat(1, 20)                  # 20 分钟前停晒 -> 过期
watchdog.check_workers()
ev = events()
check("崩溃：开出 1 条事件", len(ev) == 1, f"事件数={len(ev)}")
check("崩溃：fault_type=crash", ev and ev[0][1] == "crash", f"type={ev[0][1] if ev else None}")
check("崩溃：告警讲明「进程可能已死」",
      any("进程可能已死" in m for m in logs("ERROR")), str(logs("ERROR"))[:120])
check("崩溃：worker 置 offline", worker_row()[0] == "offline", f"status={worker_row()[0]}")

# ---------------- 8) 正常下线 ----------------
print("\n[8] 正常下线：心跳过期 + status=stopping -> 唔开故障单")
clear_state(status="stopping")
beat(1, 25)
beat(1, 20)
n_before = len(events())
err_before = len(logs("ERROR"))
watchdog.check_workers()
check("正常下线：冇开故障单", len(events()) == n_before, f"{n_before} -> {len(events())}")
check("正常下线：写咗 INFO 日志", any("正常下线" in m for m in logs("INFO")),
      str(logs("INFO"))[:120])
check("正常下线：冇新增 ERROR 告警", len(logs("ERROR")) == err_before,
      f"{err_before} -> {len(logs('ERROR'))}")
check("正常下线：状态转为 offline", worker_row()[0] == "offline", f"status={worker_row()[0]}")

# ---------------- 9) 从未上报 ----------------
print("\n[9] 从未上报：last_seen_at=NULL -> heartbeat_timeout")
clear_state(status="idle", last_seen=None)
watchdog.check_workers()
ev = events()
check("从未上报：开出 1 条事件", len(ev) == 1, f"事件数={len(ev)}")
check("从未上报：fault_type=heartbeat_timeout",
      ev and ev[0][1] == "heartbeat_timeout", f"type={ev[0][1] if ev else None}")

# ---------------- 10) 崩溃后重启 ----------------
print("\n[10] 崩溃后重启上报 -> 回 idle，唔重复开单")
n_before = len(events())
beat(1, 0)
beat(1, 0)
watchdog.check_workers()
check("重启：状态回 idle", worker_row()[0] == "idle", f"status={worker_row()[0]}")
check("重启：唔会重复开单", len(events()) == n_before, f"{n_before} -> {len(events())}")

# ---------------- 汇总 ----------------
print("\n" + "=" * 60)
print(f"结果：{sum(_results)} / {len(_results)} 通过")
print("=" * 60)
