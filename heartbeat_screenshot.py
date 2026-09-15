"""心跳截图模块（正式版）：滚动缓冲，磁盘占用锁死上限。

策略：
1. 常驻一张 heartbeat_latest.png，每次截屏直接覆写（永远只有 1 张）。
2. 另存一张带时间戳的历史快照，最多保留 MAX_SNAPSHOT_COUNT 张。
3. 每写一张就清理最旧的，文件夹大小不会无限膨胀。

故障证据（P1）：
    copy_to_fault_evidence(event_id, target_ts=...) 取图顺序
      1) hb_snapshots/heartbeat_latest.png     ← 正常路径
      2) find_nearest_snapshot(target_ts)      ← 降级：滚动目录里时间最接近的一张
    降级结果会带上 FALLBACK 备注，写进 fault_event.evidence_error，
    避免复盘时把「隔了一段时间的旧画面」误当成出事当下的现场。

用法：
    from heartbeat_screenshot import capture_heartbeat
    res = capture_heartbeat()          # -> {"latest": ..., "snapshot": ...}
"""
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from mss import MSS
    from mss.tools import to_png
except ImportError as e:  # mss 未安装时给出清晰提示
    raise ImportError("需要 mss 库，请先执行：pip install mss") from e

# ---------------- 配置 ----------------
SNAPSHOT_FOLDER = Path("./hb_snapshots")   # 历史快照 + latest 都放这里
MAX_SNAPSHOT_COUNT = 12                    # 最多保留的历史快照数（≈最近 1 小时）
LATEST_NAME = "heartbeat_latest.png"       # 常驻最新图文件名
SNAPSHOT_PREFIX = "hb_"                    # 历史快照文件名前缀
MONITOR_INDEX = 1                          # 1 = 主显示器；0 = 所有屏幕拼接
# 故障证据目录：与滚动缓冲区物理隔离，_prune_old_snapshots() 永远不碰
FAULT_EVIDENCE_FOLDER = Path("./fault_evidence")
# P1 降级阈值：最近快照与故障时刻相差超过呢个数（秒）就放弃降级。
# 宁可诚实标记「证据缺失」，都唔拎一张太旧嘅图误导复盘。
FALLBACK_MAX_DELTA_SECONDS = 30


def _grab():
    """抓一次屏，返回 (rgb_bytes, size)。"""
    with MSS() as sct:
        monitors = sct.monitors
        if MONITOR_INDEX >= len(monitors):
            raise RuntimeError(
                f"MONITOR_INDEX={MONITOR_INDEX} 无效，可用范围 0..{len(monitors) - 1}"
            )
        shot = sct.grab(monitors[MONITOR_INDEX])
        # ScreenShot 没有 .save()，必须用 to_png 编码
        return shot.rgb, shot.size


def _prune_old_snapshots(folder, max_count):
    """滚动清理：只保留最新 max_count 张历史快照，其余删除。

    注意：latest 文件名 heartbeat_latest.png 不匹配 hb_*.png，不会被误删。
    """
    files = sorted(
        folder.glob(f"{SNAPSHOT_PREFIX}*.png"),
        key=lambda f: f.stat().st_mtime,
    )
    excess = len(files) - max(max_count, 0)
    if excess <= 0:
        return
    for old in files[:excess]:
        old.unlink(missing_ok=True)


def capture_heartbeat(snapshot_dir=SNAPSHOT_FOLDER, max_count=MAX_SNAPSHOT_COUNT):
    """做一次心跳截屏，返回 {"latest": str, "snapshot": str}。

    1) 覆写 heartbeat_latest.png（常驻，给 watchdog 实时读）
    2) 写一张带时间戳的历史快照
    3) 滚动清理，历史快照数不超过 max_count

    只抓屏一次，保证 latest 与 snapshot 是同一帧，避免两次抓屏画面不一致。
    """
    folder = Path(snapshot_dir)
    folder.mkdir(parents=True, exist_ok=True)
    latest_path = folder / LATEST_NAME

    rgb, size = _grab()
    to_png(rgb, size, output=str(latest_path))
    snapshot_path = folder / f"{SNAPSHOT_PREFIX}{int(time.time() * 1000)}.png"
    to_png(rgb, size, output=str(snapshot_path))

    _prune_old_snapshots(folder, max_count)

    return {"latest": str(latest_path), "snapshot": str(snapshot_path)}


# ------------------------------------------------------------
# P1：证据降级（latest 缺失 -> 滚动目录里找时间最接近的快照）
# ------------------------------------------------------------

@dataclass
class EvidenceResult:
    """一次证据复制的完整结果，交给 watchdog 写库 / 打日志。"""

    path: Optional[str] = None            # 证据副本路径；None = 取唔到
    source: Optional[str] = None          # "latest" / "nearest"
    ts_ms: Optional[int] = None           # 实际用作证据那张快照的时间戳（毫秒）
    delta_seconds: Optional[float] = None # 降级时：与故障时刻差几多秒
    fallback: bool = False                # 是否行咗降级路径
    error: Optional[str] = None           # None=正常；有值=降级备注 / 失败原因


def _snapshot_ts_ms(path):
    """由 hb_<毫秒>.png 文件名解析时间戳；解析唔到就返 None。"""
    stem = Path(path).stem
    if not stem.startswith(SNAPSHOT_PREFIX):
        return None
    try:
        return int(stem[len(SNAPSHOT_PREFIX):])
    except ValueError:
        return None


def find_nearest_snapshot(target_ts, max_delta=FALLBACK_MAX_DELTA_SECONDS,
                          snapshot_dir=SNAPSHOT_FOLDER):
    """喺滚动目录揾时间最接近 target_ts（秒，float）嘅历史快照。

    返回 (path, delta_seconds)；以下情况返 None：
      - 目录唔存在 / 里面冇合法快照
      - 最近嘅一张都超出 max_delta 阈值

    只读遍历，唔删唔改，天然同 _prune_old_snapshots() 唔冲突。
    """
    folder = Path(snapshot_dir)
    if not folder.is_dir():
        return None

    best_path = None
    best_delta = None
    for p in folder.glob(f"{SNAPSHOT_PREFIX}*.png"):
        ts_ms = _snapshot_ts_ms(p)
        if ts_ms is None:
            continue
        delta = abs(ts_ms / 1000.0 - target_ts)
        if best_delta is None or delta < best_delta:
            best_path, best_delta = p, delta

    if best_path is None or best_delta > max_delta:
        return None
    return best_path, best_delta


def copy_to_fault_evidence(event_id, source=None, target_ts=None,
                           evidence_dir=FAULT_EVIDENCE_FOLDER,
                           max_delta=FALLBACK_MAX_DELTA_SECONDS):
    """把故障当下嘅画面复制一份到独立证据目录。

    取图顺序：
      1) 显式 source（测试 / 特殊场景用）
      2) hb_snapshots/heartbeat_latest.png        —— 正常路径
      3) 降级：find_nearest_snapshot(target_ts)   —— 最近快照，超阈值就放弃

    返回 EvidenceResult：
      - 正常      -> path 有值, fallback=False, error=None
      - 降级成功  -> path 有值, fallback=True,  error="FALLBACK: ..."
      - 取唔到证据 -> path=None,                 error="EVIDENCE_MISSING: ..."

    只有意料之外嘅 IO 异常（权限 / 磁盘满）先会 raise；
    「源文件唔见咗」一律以 error 字段返回 —— 复制证据绝不上抛打断主干。
    """
    dest_dir = Path(evidence_dir)
    latest = Path(SNAPSHOT_FOLDER) / LATEST_NAME
    ts_ms = None
    delta = None
    fallback = False

    if source is not None:
        src = Path(source)
    elif latest.exists():
        src = latest
    else:
        target = target_ts if target_ts is not None else time.time()
        found = find_nearest_snapshot(target, max_delta)
        if found is None:
            return EvidenceResult(
                error="EVIDENCE_MISSING: latest and nearest snapshot unavailable"
            )
        src, delta = found
        ts_ms = _snapshot_ts_ms(src)
        fallback = True

    dest_dir.mkdir(parents=True, exist_ok=True)
    # 目标名带 event_id，并发触发降级都唔会撞名（同一个快照可以安全复制多次）
    dest = dest_dir / f"fault_evt_{event_id}_ts{ts_ms or int(time.time() * 1000)}.png"
    try:
        shutil.copy2(src, dest)
    except FileNotFoundError:
        # 极窄竞态：揾到之后源图被滚动清理 / latest 被并发覆写
        return EvidenceResult(
            error="EVIDENCE_MISSING: latest and nearest snapshot unavailable"
        )

    if fallback:
        note = f"FALLBACK: used nearest snapshot, ts={ts_ms}, delta={delta:.1f}s"
        return EvidenceResult(path=str(dest), source="nearest", ts_ms=ts_ms,
                              delta_seconds=delta, fallback=True, error=note)
    return EvidenceResult(path=str(dest), source="latest")


if __name__ == "__main__":
    res = capture_heartbeat()
    print("latest:  ", res["latest"])
    print("snapshot:", res["snapshot"])
    n = len(list(Path(SNAPSHOT_FOLDER).glob(f"{SNAPSHOT_PREFIX}*.png")))
    print(f"历史快照数：{n} / 上限 {MAX_SNAPSHOT_COUNT}")
