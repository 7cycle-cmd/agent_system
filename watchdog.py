import sqlite3
import sys
import time
import datetime
import traceback

from heartbeat_screenshot import copy_to_fault_evidence

try:
    from db_schema import ensure_hub_ssot_schema
except Exception:  # pragma: no cover
    ensure_hub_ssot_schema = None

# OpenClaw bridge: analyze + notify after new fault (never blocks trunk)
try:
    from openclaw_bridge import spawn_detached as _spawn_openclaw_bridge
except Exception:  # pragma: no cover - optional if bridge missing
    _spawn_openclaw_bridge = None

# Windows 控制台可能是 cp950/big5，避免中文日志导致 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


DB_PATH = "agent.db"
# 超过10分钟冇心跳就判定失联
STALE_THRESHOLD_MINUTES = 10
SCAN_INTERVAL = 60  # 每60秒扫描一次
# CH7/W12 — open code.cleanup tasks for dead/zombie (mark only; never delete source)
DEAD_FN_CLEANUP_EVERY_N_SCANS = 5
_dead_fn_scan_i = 0

# ---- P2 故障分类 ----
# 心跳过期 + 曾经活过           -> crash（进程冇咗）
# 心跳仲新鲜 + 业务连续唔跳     -> hang （进程活住，但业务卡死）
# 心跳过期 + status=stopping    -> 正常下线，唔开故障单
# 从未上报过心跳                -> heartbeat_timeout
FAULT_HEARTBEAT_TIMEOUT = "heartbeat_timeout"
FAULT_CRASH = "crash"
FAULT_HANG = "hang"

# 连续几条心跳都报「业务唔跳」先判卡死，防单次抖动误报
HANG_CONSECUTIVE_BEATS = 2

# worker 主动收工嘅标记（由 worker_heartbeat_service.mark_stopping 写入）
WORKER_STATUS_STOPPING = "stopping"

# 核心铁律：
#   故障事件 / 告警 = 主干；截图证据 = 辅助 debug 材料。
#   辅助层失败，唔可以打断主干流程。


def get_db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    if ensure_hub_ssot_schema is not None:
        try:
            ensure_hub_ssot_schema(conn)
        except Exception as e:
            print(f"⚠️ hub/SSOT schema ensure failed: {e}")
    return conn


def write_log(cur, worker_id, message, level):
    """写入一条 watchdog 日志。"""
    cur.execute(
        "INSERT INTO watchdog_log (worker_id, message, level, created_at) VALUES (?, ?, ?, ?)",
        (worker_id, message, level, datetime.datetime.now()),
    )


def lookup_code_id(cur, table, code):
    row = cur.execute(f"SELECT id FROM {table} WHERE code = ?", (code,)).fetchone()
    return int(row[0]) if row else None


def resolve_hub_ids(cur, fault_type):
    """Map legacy fault_type -> channel/module/option ids (nullable if seed missing)."""
    channel_id = lookup_code_id(cur, "channel", "local_pc")
    module_id = lookup_code_id(cur, "module", "worker_heartbeat")
    option_id = None
    if fault_type in (FAULT_CRASH, FAULT_HEARTBEAT_TIMEOUT):
        option_id = lookup_code_id(cur, "fault_option", "heartbeat_off")
    return channel_id, module_id, option_id


def insert_vision_asset(cur, path, kind="frozen", source="fault_evidence"):
    if not path:
        return None
    cur.execute(
        "INSERT INTO vision_asset (kind, path_or_url, source) VALUES (?, ?, ?)",
        (kind, path, source),
    )
    return cur.lastrowid


def insert_event_facts(cur, event_id, facts):
    """facts: list of (keyword, value_text, value_type, source)."""
    for keyword, value_text, value_type, source in facts:
        if value_text is None:
            continue
        cur.execute(
            "INSERT INTO fault_event_fact "
            "(event_id, keyword, value_text, value_type, source) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                event_id,
                keyword,
                str(value_text),
                value_type or "string",
                source or "watchdog",
            ),
        )


def build_stale_facts(
    worker_id, name, group_id, fault_type, last_seen_at, delta_min, status_before
):
    facts = [
        ("worker_id", worker_id, "number", "watchdog"),
        ("worker_name", name, "string", "watchdog"),
        ("group_id", group_id, "string", "watchdog"),
        ("fault_type", fault_type, "string", "watchdog"),
        ("status_before", status_before, "string", "watchdog"),
        (
            "watchdog_message",
            "失联" if fault_type != FAULT_HANG else "业务卡死",
            "string",
            "watchdog",
        ),
    ]
    if last_seen_at is not None:
        facts.append(
            ("heartbeat_last_seen_at", last_seen_at, "datetime", "heartbeat")
        )
    if delta_min is not None:
        facts.append(("stale_minutes", f"{delta_min:.4f}", "number", "watchdog"))
    if fault_type in (FAULT_CRASH, FAULT_HEARTBEAT_TIMEOUT):
        facts.append(("option_code_hint", "heartbeat_off", "string", "watchdog"))
        facts.append(
            (
                "likely_infra",
                "host_off_or_worker_dead_or_ollama_down",
                "string",
                "watchdog",
            )
        )
    return facts


# ------------------------------------------------------------
# 故障事件（主干）
# ------------------------------------------------------------

def record_fault_event(
    cur,
    worker_id,
    group_id,
    fault_type,
    channel_id=None,
    module_id=None,
    option_id=None,
):
    """写入一条故障事件（status=open, resolved_at=NULL），返回 event_id。"""
    cur.execute(
        "INSERT INTO fault_event "
        "(worker_id, group_id, fault_type, status, detect_at, "
        " channel_id, module_id, option_id) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?, ?)",
        (
            worker_id,
            group_id,
            fault_type,
            datetime.datetime.now(),
            channel_id,
            module_id,
            option_id,
        ),
    )
    return cur.lastrowid


def link_evidence_to_event(cur, event_id, evidence_path, note=None):
    """把证据图片路径写回 fault_event。

    note 有值时同时写入 evidence_error（用于记录 P1 降级备注），
    正常路径则保持 NULL。
    """
    cur.execute(
        "UPDATE fault_event SET evidence_img_path = ?, evidence_error = ? "
        "WHERE event_id = ?",
        (evidence_path, note, event_id),
    )


def set_evidence_error(cur, event_id, reason):
    """复制证据失败：只记录原因，不阻塞主干。"""
    cur.execute(
        "UPDATE fault_event SET evidence_error = ? WHERE event_id = ?",
        (reason, event_id),
    )


def classify_copy_error(e):
    """把 IO 异常翻译成简短的 evidence_error 代号。"""
    if isinstance(e, FileNotFoundError):
        return "copy_failed:source_missing"
    if isinstance(e, PermissionError):
        return "copy_failed:file_locked"
    if getattr(e, "errno", None) == 28:  # ENOSPC
        return "copy_failed:disk_full"
    return f"copy_failed:{type(e).__name__}"


def freeze_fault_evidence(
    cur,
    worker_id,
    group_id,
    fault_type="heartbeat_timeout",
    target_ts=None,
    facts=None,
):
    """故障隔离：写 fault_event + 复制当下画面到 fault_evidence/。

    铁律：故障事件 / 告警係主干，截图证据係辅助材料。
    复制证据只试一次；失败就写 evidence_error，绝不重试、绝不抛异常、
    绝唔阻塞故障事件同告警。只在故障「发生嘅瞬间」调用一次。

    Phase 2: stamp hub ids, vision_asset, fault_event_fact (instance SSOT).
    """
    channel_id, module_id, option_id = resolve_hub_ids(cur, fault_type)
    event_id = record_fault_event(
        cur,
        worker_id,
        group_id,
        fault_type,
        channel_id=channel_id,
        module_id=module_id,
        option_id=option_id,
    )
    if facts:
        try:
            insert_event_facts(cur, event_id, facts)
        except Exception as e:
            print(f"⚠️ WARN event_id={event_id} fact insert failed: {e}")

    try:
        res = copy_to_fault_evidence(event_id, target_ts=target_ts)  # 只试一次
    except Exception as e:  # 意料之外嘅 IO 异常（权限/磁盘满）
        reason = classify_copy_error(e)
        set_evidence_error(cur, event_id, reason)
        print(f"⚠️ WARN event_id={event_id} evidence copy failed: {reason}")
        return event_id

    if res.path is None:
        set_evidence_error(cur, event_id, res.error)
        print(f"⚠️ WARN event_id={event_id} evidence missing: {res.error}")
        return event_id

    link_evidence_to_event(cur, event_id, res.path, note=res.error)
    try:
        kind = "frozen"
        source = "fault_evidence"
        if res.error and str(res.error).startswith("FALLBACK"):
            source = "hb_snapshot_fallback"
        vid = insert_vision_asset(cur, res.path, kind=kind, source=source)
        if vid is not None:
            cur.execute(
                "UPDATE fault_event SET vision_id = ? WHERE event_id = ?",
                (vid, event_id),
            )
            insert_event_facts(
                cur,
                event_id,
                [
                    ("vision_id", vid, "number", "watchdog"),
                    ("evidence_path", res.path, "string", "watchdog"),
                ],
            )
    except Exception as e:
        print(f"⚠️ WARN event_id={event_id} vision_asset failed: {e}")

    if res.fallback:
        print(f"🟡 降级证据已冻结: event_id={event_id} -> {res.path} ({res.error})")
    else:
        print(f"🧊 故障证据已冻结: event_id={event_id} -> {res.path}")
    return event_id


# ------------------------------------------------------------
# 扫描
# ------------------------------------------------------------

def recent_business_flags(cur, worker_id, n=HANG_CONSECUTIVE_BEATS):
    """取最近 n 条心跳嘅 business_alive（新 -> 旧）。"""
    rows = cur.execute(
        "SELECT business_alive FROM worker_heartbeat "
        "WHERE worker_id = ? ORDER BY heartbeat_at DESC, id DESC LIMIT ?",
        (worker_id, n),
    ).fetchall()
    return [r[0] for r in rows]


def is_hung(cur, worker_id):
    """连续 HANG_CONSECUTIVE_BEATS 条心跳都报 business_alive=0 就当卡死。

    要连续几条先算，系防业务一两次拖慢就误报。
    NULL（旧版 worker 冇上报呢个字段）/ 条数唔够 -> 一律当健康，
    避免架完升级对住旧数据狂出假卡死。
    """
    flags = recent_business_flags(cur, worker_id)
    if len(flags) < HANG_CONSECUTIVE_BEATS:
        return False
    return all(f == 0 for f in flags)


def _queue_bridge(event_ids, pending):
    """Collect new fault event ids for post-commit bridge spawn."""
    if event_ids is None:
        return
    if isinstance(event_ids, (list, tuple, set)):
        pending.extend(x for x in event_ids if x is not None)
    else:
        pending.append(event_ids)


def _process_one_worker(cur, worker_row, now, bridge_events):
    """Handle one worker row. Caller commits after success (per-worker)."""
    worker_id, name, group_id, status, last_seen_at = worker_row

    if last_seen_at is None:
        delta_min = None
        last_ts = None
        fresh = False
    else:
        last_dt = datetime.datetime.fromisoformat(last_seen_at)
        delta_min = (now - last_dt).total_seconds() / 60
        last_ts = last_dt.timestamp()
        fresh = delta_min <= STALE_THRESHOLD_MINUTES

    # ---- 情况一：心跳仍新鲜，但业务循环连续唔跳 -> 卡死 ----
    if fresh and is_hung(cur, worker_id):
        if status != "offline":
            cur.execute("UPDATE workers SET status = 'offline' WHERE id = ?", (worker_id,))
            write_log(
                cur, worker_id,
                f"worker[{name}]业务卡死：心跳仍在，但业务循环连续"
                f"{HANG_CONSECUTIVE_BEATS}次未跳动",
                "ERROR",
            )
            facts = build_stale_facts(
                worker_id, name, group_id, FAULT_HANG, last_seen_at, delta_min, status
            )
            eid = freeze_fault_evidence(
                cur, worker_id, group_id, FAULT_HANG, target_ts=last_ts, facts=facts
            )
            _queue_bridge(eid, bridge_events)
        print(f"🧊 Worker {worker_id}({name}) 业务卡死！心跳仍在（{delta_min:.1f} 分钟前）")
        return

    # ---- 情况二：心跳新鲜 + 业务正常 -> 健康 ----
    if fresh:
        if status == WORKER_STATUS_STOPPING:
            print(f"👋 Worker {worker_id}({name}) 已收工，等心跳过期后正式标记下线")
            return
        # 恢复 != 故障解决；fault_event.status 保持 open
        if status == "offline":
            cur.execute("UPDATE workers SET status = 'idle' WHERE id = ?", (worker_id,))
            write_log(cur, worker_id, f"worker[{name}]心跳恢复", "INFO")
        print(f"✅ Worker {worker_id}({name}) 正常，距上次心跳 {delta_min:.1f} 分钟")
        return

    # ---- 情况三：心跳过期 + 有主动收工标记 -> 正常下线 ----
    if status == WORKER_STATUS_STOPPING:
        cur.execute("UPDATE workers SET status = 'offline' WHERE id = ?", (worker_id,))
        write_log(cur, worker_id, f"worker[{name}]正常下线（主动收工）", "INFO")
        print(f"👋 Worker {worker_id}({name}) 正常下线，唔开故障单")
        return

    # ---- 情况四：心跳过期 + 冇收工标记 -> 真故障 ----
    if status != "offline":
        cur.execute("UPDATE workers SET status = 'offline' WHERE id = ?", (worker_id,))
        if last_seen_at is None:
            fault_type = FAULT_HEARTBEAT_TIMEOUT
            why = "从未上报过心跳"
        else:
            fault_type = FAULT_CRASH
            why = (
                f"心跳中断超过{STALE_THRESHOLD_MINUTES}分钟，"
                f"最后一次心跳仍报业务正常 -> 进程可能已死"
            )
        write_log(cur, worker_id, f"worker[{name}]{why}", "ERROR")
        facts = build_stale_facts(
            worker_id, name, group_id, fault_type, last_seen_at, delta_min, status
        )
        eid = freeze_fault_evidence(
            cur, worker_id, group_id, fault_type, target_ts=last_ts, facts=facts
        )
        _queue_bridge(eid, bridge_events)
    shown = "从未上报" if delta_min is None else f"{delta_min:.1f} 分钟"
    print(f"⚠️ Worker {worker_id}({name}) 失联！距上次心跳 {shown}")


def check_workers():
    conn = get_db_conn()
    bridge_events = []  # event_ids after successful per-worker commits
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, group_name, status, last_seen_at FROM workers ORDER BY id"
        )
        rows = cur.fetchall()
        now = datetime.datetime.now()

        for worker_row in rows:
            worker_id = worker_row[0]
            try:
                _process_one_worker(cur, worker_row, now, bridge_events)
                conn.commit()  # per-worker commit (Phase 2 durability)
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                print(f"❌ worker_id={worker_id} 扫描失败: {e}")
                traceback.print_exc()
                try:
                    write_log(
                        cur,
                        worker_id,
                        f"check_workers exception: {type(e).__name__}: {e}",
                        "ERROR",
                    )
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass

        # 主干已提交后才 spawn bridge；失败不影响下一轮扫描
        if _spawn_openclaw_bridge and bridge_events:
            for eid in bridge_events:
                try:
                    _spawn_openclaw_bridge(eid)
                except Exception as e:
                    print(f"⚠️ openclaw bridge spawn failed event_id={eid}: {e}")
    finally:
        conn.close()


def scan_dead_function_cleanup():
    """W12 — optional side job: open code.cleanup tasks for zombie/dead_candidate.

    Never blocks heartbeat trunk. Never auto-deletes source.
    """
    global _dead_fn_scan_i
    _dead_fn_scan_i += 1
    if DEAD_FN_CLEANUP_EVERY_N_SCANS > 1 and (
        _dead_fn_scan_i % DEAD_FN_CLEANUP_EVERY_N_SCANS
    ) != 0:
        return None
    try:
        from code_health import spawn_dead_function_cleanup_tasks
    except Exception as e:
        print(f"⚠️ dead-fn cleanup import failed: {e}")
        return None
    conn = None
    try:
        conn = get_db_conn()
        result = spawn_dead_function_cleanup_tasks(
            conn, limit=30, commit=True, source="watchdog.dead_fn"
        )
        created_n = int(result.get("created_n") or 0)
        if created_n:
            print(
                f"🧹 dead-fn cleanup tasks created={created_n} "
                f"skipped={int(result.get('skipped_n') or 0)}"
            )
        return result
    except Exception as e:
        print(f"⚠️ dead-fn cleanup scan failed: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def main():
    print(
        f"启动Watchdog看门狗，失联阈值：{STALE_THRESHOLD_MINUTES}分钟，"
        f"每{SCAN_INTERVAL}秒扫描一次 · dead-fn cleanup every "
        f"{DEAD_FN_CLEANUP_EVERY_N_SCANS} scans"
    )
    while True:
        try:
            check_workers()
        except Exception as e:
            print(f"❌ watchdog扫描出错: {e}")
            traceback.print_exc()
        try:
            scan_dead_function_cleanup()
        except Exception as e:
            print(f"⚠️ watchdog dead-fn side job error: {e}")
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
