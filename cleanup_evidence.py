"""fault_evidence 清理工具（运维动作，人工执行或 Windows 计划任务定时跑）。

铁律：
- 主服务（watchdog / worker_heartbeat_service）永远唔碰证据目录，
  主服务里面完全冇删除证据嘅逻辑。
- 本工具只删「status=resolved 且 resolved_at 超过 keep-days」嘅事件图片；
  open 状态嘅故障证据一律唔郁（未结案 = 未确认，可能仲要复盘）。
- 安全护栏：只允许删除 fault_evidence/ 目录内嘅文件，防止误删。

两件事：
  1) 已结案事件嘅旧证据   —— 按 --keep-days 清理
  2) 孤儿文件            —— 按 --orphan-days 清理（详见解说 sweep_orphans）

用法：
    python cleanup_evidence.py --keep-days 30              # 真删
    python cleanup_evidence.py --keep-days 30 --dry-run    # 只预览会删乜
    python cleanup_evidence.py --orphan-days 7             # 顺便清孤儿（默认就开）
"""
import argparse
import datetime
import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(BASE_DIR, "agent.db")
DEFAULT_EVIDENCE_DIR = os.path.join(BASE_DIR, "fault_evidence")

# 证据文件命名约定，由 copy_to_fault_evidence() 生成
ORPHAN_PREFIX = "fault_evt_"
ORPHAN_SUFFIX = ".png"
# 孤儿嘅宽限期（日）：必须远大于「写文件 -> commit」嘅时间窗，否则会误删
# 一啲啱写落盘、但事务未提交嘅新证据（嗰个瞬间佢就系「冇主」嘅）。
DEFAULT_ORPHAN_DAYS = 7


def is_inside(path, root):
    """路径安全护栏：确认 path 真係 under root（含 Windows 盘符/大小写处理）。"""
    try:
        real = os.path.normcase(os.path.realpath(path))
        root = os.path.normcase(os.path.realpath(root))
        return os.path.commonpath([real, root]) == root
    except ValueError:
        # 唔同盘符之类，commonpath 会抛 ValueError -> 视为唔安全
        return False


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def sweep_orphans(conn, evidence_dir, orphan_days, dry_run=False):
    """孤儿清理：图片喺证据目录，但 DB 冇任何事件指向佢。

    孤儿点嚟：
      check_workers() 一轮扫多个 worker，A 成功冻结证据（文件已落盘）之后
      B 出错 -> 整笔事务回滚 -> A 嘅事件行消失，但图片留喺磁盘。又或者
      DB 被重建、人手改过表。

    呢批文件永远唔会有人去揾，只会白占磁盘，而且唔清就一世都喺度。

    两层护栏：
      1) 只扫 fault_evt_*.png；人手放入去嘅其他文件一律唔碰。
      2) mtime 要超过 orphan_days 先删 —— 保护「图片啱写落盘、事务未 commit」
         嘅毫秒级竞态。呢个宽限期一定要远大于事务窗口。

    返回删除（或预览）嘅文件数。
    """
    if not os.path.isdir(evidence_dir):
        print("证据目录唔存在，跳过孤儿清理。")
        return 0

    # 有主嘅文件：DB 里面仲有行指向佢 -> 一定唔碰，无论几旧
    referenced = {
        _norm(path)
        for (path,) in conn.execute(
            "SELECT evidence_img_path FROM fault_event WHERE evidence_img_path IS NOT NULL"
        )
    }

    now = datetime.datetime.now()
    removed = 0
    young = 0
    for name in sorted(os.listdir(evidence_dir)):
        if not (name.startswith(ORPHAN_PREFIX) and name.lower().endswith(ORPHAN_SUFFIX)):
            continue
        p = os.path.join(evidence_dir, name)
        if not is_inside(p, evidence_dir):
            print(f"⏭️  跳过孤儿检查：路径唔喺证据目录内 -> {p}")
            continue
        if _norm(p) in referenced:
            continue  # 有主，交由上面「已结案」流程按 keep-days 处理

        age_days = (now - datetime.datetime.fromtimestamp(os.path.getmtime(p))).total_seconds() / 86400
        if age_days < orphan_days:
            young += 1
            continue  # 未够期，可能系未 commit 嘅写入

        if dry_run:
            print(f"[dry-run] 会删除孤儿文件（无 DB 事件指向，已 {age_days:.1f} 天）: {name}")
        else:
            os.remove(p)
            print(f"🗑️  已删除孤儿文件（无 DB 事件指向，已 {age_days:.1f} 天）: {name}")
        removed += 1

    if young:
        print(f"⏭️  {young} 个孤儿未够 {orphan_days} 天，留待下次（保护未 commit 嘅写入）。")
    return removed


def cleanup(db_path, evidence_dir, keep_days, dry_run=False,
            orphan_days=DEFAULT_ORPHAN_DAYS):
    if orphan_days < 1:
        print(f"⚠️  orphan-days={orphan_days} 太激进（会误删未 commit 嘅新证据），改用 1 天。")
        orphan_days = 1

    cutoff = datetime.datetime.now() - datetime.timedelta(days=keep_days)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT event_id, evidence_img_path, resolved_at
            FROM fault_event
            WHERE status = 'resolved'
              AND resolved_at IS NOT NULL
              AND resolved_at < ?
              AND evidence_img_path IS NOT NULL
            ORDER BY event_id
            """,
            (cutoff.isoformat(sep=" "),),
        ).fetchall()

        handled = 0
        if not rows:
            print(f"无可清理证据（resolved 且超过 {keep_days} 天嘅故障）。")
        else:
            for event_id, path, resolved_at in rows:
                if not is_inside(path, evidence_dir):
                    print(f"⏭️  跳过 event_id={event_id}：路径唔喺证据目录内 -> {path}")
                    continue

                if not os.path.exists(path):
                    print(f"⏭️  event_id={event_id} 图片已不存在，只清空 DB 路径")
                elif dry_run:
                    print(f"[dry-run] 会删除 event_id={event_id}: {path}")
                    handled += 1
                    continue
                else:
                    os.remove(path)
                    print(f"🗑️  已删除 event_id={event_id}: {path}")

                if not dry_run:
                    # 保留事件行做审计，只清空路径（图片已经冇咗，唔好扮仲有）
                    conn.execute(
                        "UPDATE fault_event SET evidence_img_path = NULL WHERE event_id = ?",
                        (event_id,),
                    )
                handled += 1

        print()
        orphans = sweep_orphans(conn, evidence_dir, orphan_days, dry_run)

        if not dry_run:
            conn.commit()
        print(
            f"\n共处理 {handled} 条已结案事件（keep-days={keep_days}）"
            f"，孤儿文件 {orphans} 个（orphan-days={orphan_days}, dry_run={dry_run}）。"
        )
        return handled + orphans
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description="清理已 resolved 嘅旧故障证据图片 + 孤儿文件")
    ap.add_argument("--keep-days", type=int, default=30, help="已结案证据保留天数（默认 30）")
    ap.add_argument("--orphan-days", type=int, default=DEFAULT_ORPHAN_DAYS,
                    help=f"孤儿文件宽限期，日（默认 {DEFAULT_ORPHAN_DAYS}）")
    ap.add_argument("--db", default=DEFAULT_DB, help="agent.db 路径")
    ap.add_argument("--evidence-dir", default=DEFAULT_EVIDENCE_DIR, help="证据目录")
    ap.add_argument("--dry-run", action="store_true", help="只预览，唔实际删除")
    args = ap.parse_args()
    cleanup(args.db, args.evidence_dir, args.keep_days, args.dry_run, args.orphan_days)


if __name__ == "__main__":
    main()
