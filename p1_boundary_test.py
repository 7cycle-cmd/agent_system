"""P1 边界校验：latest 快照缺失/损坏 -> 降级搵最近快照（唔影响主干流程）。

覆盖清单：
  1) latest 正常存在：直接用，唔触发 fallback
  2) latest 文件消失：自动揾最近快照，并写 FALLBACK 备注
  3) 最近快照时差 > 阈值：放弃降级，标记 EVIDENCE_MISSING
  4) hb_snapshots 全部清空：标记 EVIDENCE_MISSING，但告警/事件照出（主干不受阻）
  5) 并发多个故障事件同时触发降级：同一个快照可安全复制，互不覆盖、无异常

跑法：python -X utf8 p1_boundary_test.py
"""
import datetime
import os
import shutil
import sqlite3
import sys
import threading
from pathlib import Path

import create_db
import worker_heartbeat_service as s
import watchdog
from heartbeat_screenshot import (SNAPSHOT_PREFIX, _snapshot_ts_ms,
                                  copy_to_fault_evidence)

DB = "agent.db"
SNAP = Path("hb_snapshots")
EVID = Path("fault_evidence")
LATEST = SNAP / "heartbeat_latest.png"

_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(("  ✅ PASS " if cond else "  ❌ FAIL ") + name + (f"   [{detail}]" if detail else ""))


def reset_db():
    """重建空库 + 清空两个图片目录。"""
    shutil.rmtree(SNAP, ignore_errors=True)
    shutil.rmtree(EVID, ignore_errors=True)
    create_db.create_database()


def set_stale(minutes=20):
    c = sqlite3.connect(DB)
    c.execute("UPDATE workers SET last_seen_at=? WHERE id=1",
              ((datetime.datetime.now() - datetime.timedelta(minutes=minutes)).isoformat(sep=" "),))
    c.commit()
    c.close()


def last_seen_ts():
    """由 DB 读回最后一次心跳时间戳（秒），保证同 watchdog 用嘅基准一致。"""
    c = sqlite3.connect(DB)
    v = c.execute("SELECT last_seen_at FROM workers WHERE id=1").fetchone()[0]
    c.close()
    return datetime.datetime.fromisoformat(v).timestamp()


def latest_event():
    c = sqlite3.connect(DB)
    r = c.execute(
        "SELECT event_id, evidence_img_path, evidence_error FROM fault_event "
        "ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    c.close()
    return r


def recover():
    """上报心跳 + 扫一次，令 worker 由 offline 回到 idle（每个用例前准备干净状态）。"""
    s.send_heartbeat(1, s.take_screenshot())
    watchdog.check_workers()


def make_fake_snapshot(ts_seconds, content_src):
    """造一张文件名时间戳可控嘅快照（模拟 worker 自己最后一张截图）。"""
    p = SNAP / f"{SNAPSHOT_PREFIX}{int(ts_seconds * 1000)}.png"
    shutil.copy2(content_src, p)
    return p


def png_source():
    return str(next(SNAP.glob(f"{SNAPSHOT_PREFIX}*.png")))


print("=" * 60)
print("P1 边界校验开始")
print("=" * 60)

# 防呆闸：本脚本会重建 agent.db + 清空两个图片目录，绝不可对验收/生产数据乱跑
if "--yes-wipe" not in sys.argv:
    print("⚠️  本测试会重建 agent.db，并清空 hb_snapshots/ 同 fault_evidence/。")
    print("    只可用于开发/一次性验收环境。确认要跑就加参数：")
    print("    python -X utf8 p1_boundary_test.py --yes-wipe")
    sys.exit(1)

reset_db()
wid = s.register_worker()
s.send_heartbeat(wid, s.take_screenshot())

# ---------------- 1) latest 正常存在 ----------------
print("\n[1] latest 正常存在 -> 直接用")
set_stale()
watchdog.check_workers()
ev = latest_event()
check("正常路径：evidence_img_path 有值", ev[1] is not None, f"path={ev[1]}")
check("正常路径：evidence_error 保持 NULL", ev[2] is None, f"err={ev[2]}")
check("正常路径：唔含 FALLBACK 字样", "FALLBACK" not in str(ev[2]))

# ---------------- 2) latest 消失 -> 降级 ----------------
print("\n[2] latest 消失 -> 自动揾最近快照")
recover()
set_stale()
target = last_seen_ts()                      # 故障时刻
LATEST.unlink()                              # 模拟 latest 唔见咗
make_fake_snapshot(target, png_source())     # 造一张「故障时刻」嘅快照
watchdog.check_workers()
ev = latest_event()
check("降级成功：evidence_img_path 有值", ev[1] is not None, f"path={ev[1]}")
check("降级成功：evidence_error 记 FALLBACK 备注",
      str(ev[2]).startswith("FALLBACK: used nearest snapshot"), f"err={ev[2]}")
check("降级备注含 ts= 与 delta=", "ts=" in str(ev[2]) and "delta=" in str(ev[2]), f"err={ev[2]}")

# ---------------- 3) 时差 > 阈值 -> 放弃降级 ----------------
print("\n[3] 最近快照时差 > 30s -> 放弃降级")
recover()
set_stale()
target = last_seen_ts()
for p in list(SNAP.glob(f"{SNAPSHOT_PREFIX}*.png")):
    p.unlink()
LATEST.unlink()
# 用之前已冻结嘅证据图做内容来源，唔使再抓屏
content_cands = list(EVID.glob("*.png"))
if not content_cands:            # 保险：证据目录都无图就即场抓一张
    s.send_heartbeat(1, s.take_screenshot())
    content_cands = list(SNAP.glob(f"{SNAPSHOT_PREFIX}*.png"))
content = str(content_cands[0])
make_fake_snapshot(target - 120, content)    # 离故障时刻 120s，远超 30s
watchdog.check_workers()
ev = latest_event()
check("超阈值：evidence_img_path 为 NULL", ev[1] is None, f"path={ev[1]}")
check("超阈值：evidence_error 记 EVIDENCE_MISSING",
      str(ev[2]).startswith("EVIDENCE_MISSING"), f"err={ev[2]}")
check("超阈值：事件本身照建（主干未受阻）", ev[0] is not None, f"event_id={ev[0]}")

# ---------------- 4) hb_snapshots 全空 ----------------
print("\n[4] hb_snapshots 全部清空 -> 标记证据缺失，但告警照出")
recover()
set_stale()
for p in list(SNAP.glob("*.png")):
    p.unlink()
assert not list(SNAP.glob("*.png")), "目录应已清空"
c = sqlite3.connect(DB)
err_logs_before = c.execute(
    "SELECT COUNT(*) FROM watchdog_log WHERE level='ERROR'").fetchone()[0]
c.close()
watchdog.check_workers()
ev = latest_event()
check("全空：evidence_img_path 为 NULL", ev[1] is None, f"path={ev[1]}")
check("全空：evidence_error 记 EVIDENCE_MISSING",
      str(ev[2]).startswith("EVIDENCE_MISSING"), f"err={ev[2]}")
c = sqlite3.connect(DB)
err_logs_after = c.execute("SELECT COUNT(*) FROM watchdog_log WHERE level='ERROR'").fetchone()[0]
wstatus = c.execute("SELECT status FROM workers WHERE id=1").fetchone()[0]
c.close()
check("全空：主干未受阻 —— 仍写了 ERROR 告警日志", err_logs_after == err_logs_before + 1,
      f"{err_logs_before} -> {err_logs_after}")
check("全空：worker 状态仍被置 offline", wstatus == "offline", f"status={wstatus}")

# ---------------- 5) 并发触发降级 ----------------
print("\n[5] 并发 8 个故障事件同时触发降级")
recover()                                    # 重建 latest + 快照
snap = next(SNAP.glob(f"{SNAPSHOT_PREFIX}*.png"))
target = _snapshot_ts_ms(snap) / 1000.0
LATEST.unlink()                              # 令所有事件都必须走降级
out = []
lock = threading.Lock()


def _run(eid):
    try:
        r = copy_to_fault_evidence(eid, target_ts=target)
    except Exception as e:                   # 并发下绝不该抛异常
        r = e
    with lock:
        out.append((eid, r))


threads = [threading.Thread(target=_run, args=(500 + i,)) for i in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()

paths = [r.path for _, r in out if not isinstance(r, Exception)]
check("并发：8 个都冇抛异常", all(not isinstance(r, Exception) for _, r in out),
      str([r for _, r in out if isinstance(r, Exception)])[:120])
check("并发：8 个都成功复制到证据", len(paths) == 8 and all(os.path.exists(p) for p in paths),
      f"成功 {len(paths)}/8")
check("并发：目标文件名互不覆盖（按 event_id 唯一）", len(set(paths)) == 8,
      f"唯一路径 {len(set(paths))}/8")
check("并发：全部正确标记为 fallback",
      all(getattr(r, "fallback", False) for _, r in out))

# ---------------- 汇总 ----------------
print("\n" + "=" * 60)
print(f"结果：{sum(_results)} / {len(_results)} 通过")
print("=" * 60)
