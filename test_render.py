from pathlib import Path
import re
import sqlite3

from db_schema import get_dev_tasks, create_dev_task, ensure_task_center_schema, find_task_by_label, get_db_path
from llm_task_center import list_instances
from _tmp_render_html_fn import _render_merged_html
from coord_store import init_task_table

SKILL_FOLDER = "./skills"


def sync_skills_to_dev_tasks(conn, skill_folder=SKILL_FOLDER):
    """冪等：掃描 skill 資料夾，缺邊個就補邊個 dev_task。

    對每個 skill：
      - 用 find_task_by_label() 檢查 task_label 係咪已存在（exact match）
      - 唔存在 → create_dev_task()（保留 raise 兜底）
      - 已存在 → 跳過（冪等，唔 crash）

    Returns {"created": int, "skipped": int, "total": int}
    """
    from skill_scanner import scan_skill_folder
    from db_schema import create_version

    # 確保 task_center module + work-1.0 version 存在（冪等）
    channel_id = 1
    mod = conn.execute(
        "SELECT id FROM module WHERE code = 'task_center'"
    ).fetchone()
    if not mod:
        cur = conn.execute(
            "INSERT INTO module (code, name) VALUES ('task_center', 'Task Center')"
        )
        module_id = int(cur.lastrowid)
    else:
        module_id = int(mod[0])
    ver = conn.execute(
        "SELECT id FROM version_center WHERE channel_id=? AND module_id=? AND version_label='work-1.0'",
        (channel_id, module_id),
    ).fetchone()
    if not ver:
        create_version(
            conn,
            channel_id=channel_id,
            module_id=module_id,
            version_label="work-1.0",
            title="LLM work items",
            status="active",
        )
        version_id = int(
            conn.execute(
                "SELECT id FROM version_center WHERE channel_id=? AND module_id=? AND version_label='work-1.0'",
                (channel_id, module_id),
            ).fetchone()[0]
        )
    else:
        version_id = int(ver[0])
    # 確保 llm.work action code 存在
    act = conn.execute(
        "SELECT id FROM task_action_name WHERE code='llm.work'"
    ).fetchone()
    if not act:
        cur = conn.execute(
            "INSERT INTO task_action_name (element, action, code, name, requires_tdd, status) "
            "VALUES ('system', 'work', 'llm.work', 'LLM work item', 0, 'active')"
        )
        action_id = int(cur.lastrowid)
    else:
        action_id = int(act[0])

    raw_skill_tasks = scan_skill_folder(skill_folder)
    created = skipped = 0
    # 兩階段：先全量 pre-check（確保所有 label 都唔存在），再插入。
    # create_dev_task 內部會 commit（每個 skill 獨立 transaction），所以用
    # pre-check 避免中途 fail 留低部分記錄 —— 任何 duplicate 喺插入前就 raise。
    for t in raw_skill_tasks:
        label = str(t.get("task_id") or t.get("track_id") or "task")
        if find_task_by_label(conn, label):
            skipped += 1
    for t in raw_skill_tasks:
        label = str(t.get("task_id") or t.get("track_id") or "task")
        if find_task_by_label(conn, label):
            continue
        create_dev_task(
            conn,
            channel_id=channel_id,
            module_id=module_id,
            version_id=version_id,
            action_name_id=action_id,
            task_label=label,
            title=str(t.get("name") or t.get("item_name") or "skill task"),
            payload_json=t,
            status="pending",
        )
        created += 1

    # P2-1 Orphan 檢測：skill 檔唔存在但 dev_task 仍在 → log warning（唔自動刪）
    # 只針對由 skill sync 建立嘅 dev_task（task_label = skill frontmatter task_id）。
    # 跳過純數字/點號 ID（如 10.21）—— 嗰啲係 task ID coding rule 嘅 ID，唔係 skill task_id。
    skill_labels = {
        str(t.get("task_id") or t.get("track_id") or "task")
        for t in raw_skill_tasks
    }
    orphaned = []
    try:
        rows = conn.execute(
            """
            SELECT task_label, title FROM dev_task
            WHERE module_id = ? AND task_label IS NOT NULL AND task_label != ''
            """,
            (module_id,),
        ).fetchall()
        for r in rows:
            label = str(r["task_label"])
            # 跳過 TEST-*（測試）同純數字/點號 ID（task ID coding rule，如 10.21）
            if label.startswith("TEST-"):
                continue
            if re.fullmatch(r"\d+(\.\d+)?([A-Za-z]?)(-T-\d+)?", label):
                continue
            if label not in skill_labels:
                orphaned.append((label, r["title"]))
    except Exception as e:
        print(f"[WARN] orphan 檢測失敗: {e}")
    if orphaned:
        print(f"[WARN] orphan dev_task（skill 檔唔存在，唔自動刪）: {len(orphaned)} 條")
        for label, title in orphaned[:20]:
            print(f"  - {label}: {title}")

    # SCAN = REGISTER (user, 2026-09-22: "the methid is by skill and be auto!!").
    #
    # The scan already walks every `*.skill.md`, so a skill that DECLARES its
    # contract (`contract.yaml`) is registered here rather than by a separate
    # `_register_<name>_skill.py` script. A skill with NO declaration is
    # REPORTED, never invented — inventing a contract is fabricating a rule.
    #
    # This is deliberately NOT fatal: a registration failure must not stop the
    # Skill Library from syncing. The failure is PRINTED, so it is visible
    # rather than swallowed.
    registered = None
    try:
        import skill_registrar as sr
        registered = sr.register_all(conn, skill_folder, apply=True)
        if registered["declared"]:
            print(f"[skill_registrar] declared={registered['declared']} "
                  f"undeclared={registered['undeclared']} "
                  f"ok={registered['ok']}")
        for rep in registered["results"]:
            if not rep.get("ok"):
                print(f"[skill_registrar][REFUSED] "
                      f"{rep.get('skill_key') or rep.get('skill_dir')}: "
                      f"{'; '.join(rep.get('refused') or [])}")
    except Exception as e:
        print(f"[WARN] skill_registrar 失敗（唔影響 sync）: "
              f"{type(e).__name__}: {e}")

    return {"created": created, "skipped": skipped, "total": len(raw_skill_tasks),
            "orphaned": len(orphaned),
            "registered": (registered or {}).get("declared", 0)}


def merge_task_data(dev_list, instance_list):
    """雙數據源合併核心函數.

    dev_list: 靜態 dev_task 列表
    instance_list: 動態 task_instances 列表
    return: 合併後統一任務列表（runtime_instance 覆蓋 dev_static）
    """
    merged_map = {}

    # 1. 先載入所有靜態 dev_task 數據
    for dev in dev_list:
        d = dict(dev)
        d["task_source"] = "dev_static"
        # dev_task 用 task_label 做主鍵；統一為 task_id
        tid = d.get("task_id") or d.get("task_label")
        if not tid:
            continue
        d["task_id"] = tid
        merged_map[tid] = d

    # 2. 載入運行實例，覆蓋同名 task_id 數據（動態優先）
    for inst in instance_list:
        tid = inst.get("task_id")
        if not tid:
            continue
        unified = {
            "task_id": tid,
            "status": inst.get("status", "pending"),
            "current_model": inst.get("current_model", ""),
            "qc_summary": inst.get("qc_summary", ""),
            "error": inst.get("error", ""),
            "template_id": inst.get("template_id", ""),
            "session_id": inst.get("session_id", ""),
            "created_at": inst.get("created_at", ""),
            "updated_at": inst.get("finished_at", ""),
            "task_source": "runtime_instance",
        }
        if tid in merged_map:
            merged_map[tid].update(unified)
        else:
            merged_map[tid] = unified

    return list(merged_map.values())


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Render dev_task HTML (optionally sync skills first)")
    ap.add_argument("--no-sync", action="store_true",
                    help="Skip skill->dev_task sync (caller already synced, e.g. watch_skills)")
    args = ap.parse_args()

    if not args.no_sync:
        # 2. 數據兜底：掃描 skill 並冪等同步到 dev_task（缺邊個補邊個）
        _conn = sqlite3.connect(get_db_path())
        _conn.row_factory = sqlite3.Row
        try:
            ensure_task_center_schema(_conn)
        except Exception as e:
            print(f"[WARN] ensure_task_center_schema: {e}")
        sync_result = sync_skills_to_dev_tasks(_conn, SKILL_FOLDER)
        if sync_result["created"]:
            print(f"[OK] skill sync: 新增 {sync_result['created']} 條 dev_task（跳過 {sync_result['skipped']} 條已存在）")
        else:
            print(f"[OK] skill sync: 無新增（{sync_result['skipped']} 條已存在，冪等）")
        _conn.close()

    # 1. 讀取雙數據源
    dev_tasks = get_dev_tasks()
    runtime_instances = list_instances(limit=500)
    print(f"[OK] 靜態 dev_task: {len(dev_tasks)} 個")
    print(f"[OK] 動態運行實例: {len(runtime_instances)} 個")

    # 3. 合併雙數據源
    final_tasks = merge_task_data(dev_tasks, runtime_instances)
    print(f"[OK] 合併後總任務數: {len(final_tasks)} 個")

    # 4. 渲染普通版 & 工程版 HTML
    html_output = _render_merged_html(rows=final_tasks, engineer_mode=False)
    Path("test_taskview.html").write_text(html_output, encoding="utf-8")
    print("[OK] 普通使用者模式：test_taskview.html 已更新")

    # 工程模式
    html_engineer = _render_merged_html(rows=final_tasks, engineer_mode=True)
    Path("test_taskview_engineer.html").write_text(html_engineer, encoding="utf-8")
    print("[OK] 工程模式：test_taskview_engineer.html 已更新")


if __name__ == "__main__":
    main()
