import sqlite3
import sys
from pathlib import Path

# Windows 控制台可能是 cp950/big5，避免中文输出报错
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ensure hub/SSOT tables exist before inspect (additive, safe)
try:
    from db_schema import ensure_schema

    ensure_schema("agent.db")
except Exception as e:
    print(f"(schema ensure skipped: {e})")

conn = sqlite3.connect("agent.db")

print("== workers（当前快照） ==")
for row in conn.execute(
    "SELECT id, name, group_name, status, last_seen_at FROM workers ORDER BY group_name, id"
):
    print(row)

print("\n== 按节点组汇总 ==")
for row in conn.execute(
    """
    SELECT group_name,
           COUNT(*)                                            AS total,
           SUM(CASE WHEN status = 'idle'     THEN 1 ELSE 0 END) AS idle,
           SUM(CASE WHEN status = 'offline'  THEN 1 ELSE 0 END) AS offline,
           SUM(CASE WHEN status = 'stopping' THEN 1 ELSE 0 END) AS stopping
    FROM workers
    GROUP BY group_name
    ORDER BY group_name
    """
):
    print(f"组 {row[0]}: 共 {row[1]} 个节点，idle={row[2]}，offline={row[3]}，stopping={row[4]}")

print("\n== worker_heartbeat（最近 10 条历史） ==")
for row in conn.execute(
    """
    SELECT h.id, w.name, h.heartbeat_at, h.business_alive, h.pid, h.screenshot_path
    FROM worker_heartbeat h
    LEFT JOIN workers w ON w.id = h.worker_id
    ORDER BY h.heartbeat_at DESC
    LIMIT 10
    """
):
    _id, name, at, alive, pid, shot = row
    flag = {1: "业务正常", 0: "🧊业务卡死", None: "—(旧版)"}.get(alive, f"?{alive}")
    print((_id, name, at, flag, pid, shot))

print("\n== watchdog_log（最近 10 条） ==")
for row in conn.execute(
    "SELECT id, worker_id, level, message, created_at FROM watchdog_log ORDER BY created_at DESC LIMIT 10"
):
    print(row)


def evidence_display(path, err):
    if err and err.startswith("FALLBACK"):
        return f"🟡 FALLBACK快照 {path}  ({err})"
    if err:
        return f"❌ 证据缺失/失败  ({err})"
    if path:
        return f"✅ 正常  {path}"
    return "—"


print("\n== fault_event（故障事件 hub，最近 10 条） ==")
for row in conn.execute(
    """
    SELECT event_id, worker_id, group_id, fault_type, status,
           detect_at, resolved_at, evidence_img_path, evidence_error,
           channel_id, module_id, vision_id, fault_analysis_id,
           task_id, option_id, solution_id
    FROM fault_event
    ORDER BY event_id DESC
    LIMIT 10
    """
):
    (
        event_id, worker_id, group_id, fault_type, status,
        detect_at, resolved_at, path, err,
        channel_id, module_id, vision_id, fault_analysis_id,
        task_id, option_id, solution_id,
    ) = row
    print(f"event_id={event_id}  worker={worker_id}  group={group_id}  type={fault_type}")
    print(f"    status={status}  detect={detect_at}  resolved={resolved_at or '-'}")
    print(
        f"    hub: channel={channel_id} module={module_id} vision={vision_id} "
        f"analysis={fault_analysis_id} task={task_id} option={option_id} solution={solution_id}"
    )
    print(f"    证据: {evidence_display(path, err)}")

print("\n== 故障事件汇总 ==")
for row in conn.execute("SELECT status, COUNT(*) FROM fault_event GROUP BY status"):
    print(f"  status={row[0]}: {row[1]} 条")

TYPE_DESC = {
    "heartbeat_timeout": "从未上报过心跳",
    "crash": "💥 崩溃（进程冇咗）",
    "hang": "🧊 卡死（进程活住，业务停咗）",
}
print("  --- 按故障类型 ---")
for row in conn.execute(
    "SELECT fault_type, COUNT(*) FROM fault_event GROUP BY fault_type ORDER BY fault_type"
):
    print(f"  {row[0]:<18} {row[1]} 条   {TYPE_DESC.get(row[0], '(未知类型)')}")

fallback_n = conn.execute(
    "SELECT COUNT(*) FROM fault_event WHERE evidence_error LIKE 'FALLBACK%'"
).fetchone()[0]
bad_n = conn.execute(
    "SELECT COUNT(*) FROM fault_event "
    "WHERE evidence_error IS NOT NULL AND evidence_error NOT LIKE 'FALLBACK%'"
).fetchone()[0]
ok_n = conn.execute(
    "SELECT COUNT(*) FROM fault_event WHERE evidence_img_path IS NOT NULL AND evidence_error IS NULL"
).fetchone()[0]
print(f"  ✅ 正常证据 : {ok_n} 条")
print(f"  🟡 FALLBACK降级 : {fallback_n} 条   <- 画面有偏差，复盘要留意")
print(f"  ❌ 证据缺失/失败 : {bad_n} 条")

print("\n== fault_analysis（AI 分析，最近 10 条） ==")
try:
    rows = conn.execute(
        """
        SELECT id, event_id, model, evidence_used, notified_at, error, summary,
               evidence_path, created_at, option_id, solution_id, match_status, match_score
        FROM fault_analysis
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()
    if not rows:
        print("  (无)")
    for row in rows:
        (
            _id, event_id, model, used, notified_at, err, summary,
            epath, created, option_id, solution_id, match_status, match_score,
        ) = row
        print(f"id={_id} event_id={event_id} model={model} used={used}")
        print(f"    notified={notified_at or '-'}  created={created}")
        print(
            f"    match={match_status or '-'} score={match_score} "
            f"option={option_id} solution={solution_id}"
        )
        print(f"    evidence={epath or '-'}  error={err or '-'}")
        print(f"    summary={summary or '-'}")
except sqlite3.OperationalError as e:
    print(f"  (fault_analysis 不可用: {e})")

print("\n== channel / module ==")
try:
    for row in conn.execute("SELECT id, code, name FROM channel ORDER BY id"):
        print("  channel", row)
    for row in conn.execute("SELECT id, code, name FROM module ORDER BY id"):
        print("  module ", row)
except sqlite3.OperationalError as e:
    print(f"  (未迁移: {e})")

print("\n== fault_option + SSOT + solution ==")
try:
    for row in conn.execute(
        "SELECT id, code, name, channel_id, module_id, status FROM fault_option ORDER BY id"
    ):
        print("  option", row)
        oid = row[0]
        for s in conn.execute(
            "SELECT keyword, value_text, weight FROM fault_ssot WHERE option_id=? ORDER BY id",
            (oid,),
        ):
            print(f"    ssot  {s[0]} = {s[1]}  (w={s[2]})")
        for sol in conn.execute(
            "SELECT id, title, priority, status FROM fault_solution WHERE option_id=? ORDER BY priority",
            (oid,),
        ):
            print(f"    sol   {sol}")
except sqlite3.OperationalError as e:
    print(f"  (未迁移: {e})")

print("\n== fault_event_fact（最近 15 条） ==")
try:
    rows = conn.execute(
        """
        SELECT id, event_id, keyword, value_text, source, created_at
        FROM fault_event_fact
        ORDER BY id DESC
        LIMIT 15
        """
    ).fetchall()
    if not rows:
        print("  (无 — Phase 2 watchdog 写入后会出现)")
    for row in rows:
        print(row)
except sqlite3.OperationalError as e:
    print(f"  (未迁移: {e})")

print("\n== fault_option_pending / fault_ssot_revision ==")
try:
    n_p = conn.execute("SELECT COUNT(*) FROM fault_option_pending WHERE status='open'").fetchone()[0]
    n_r = conn.execute("SELECT COUNT(*) FROM fault_ssot_revision WHERE status='proposed'").fetchone()[0]
    print(f"  pending options open: {n_p}")
    print(f"  ssot revisions proposed: {n_r}")
except sqlite3.OperationalError as e:
    print(f"  (未迁移: {e})")


def count_png(d):
    p = Path(d)
    return len(list(p.glob("*.png"))) if p.is_dir() else 0


print("\n== 图片文件 ==")
snap_dir = Path("hb_snapshots")
latest = snap_dir / "heartbeat_latest.png"
print("滚动区 hb_snapshots/latest:", latest if latest.exists() else "(无)")
print("滚动区 hb_snapshots/历史快照数:", len(list(snap_dir.glob("hb_*.png"))) if snap_dir.is_dir() else 0)
print("证据区 fault_evidence/文件数:", count_png("fault_evidence"))

conn.close()
