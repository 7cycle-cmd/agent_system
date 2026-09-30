# test_task_lifecycle.py
"""
Task Center 生命週期測試套件
同步開發用｜Worker 可直接參考每個 case 嘅預期結果
規則：
- 每個測試分為：描述、輸入、預期(expect)、斷言assert
- 全部測試數據執行完自動刪除，唔影響正式庫
- 分兩大組：HardGate + Task Lifecycle（狀態機）

ISOLATION (2026-09-24): this file was MEASURED leaving 1834 `TEST-%` rows in the
live `task_instances`. It has no `sqlite3.connect` of its own -- it calls
`llm_task_center`, which resolves the DEFAULT db path -- and its
`clean_test_records()` ran only from `main()`, which pytest never calls.
Now every test runs on a COPY of the live DB. See `_iso_db`.
"""
import json
import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest

import llm_task_center as ltc
import db_schema as ds

# 產生唯一測試前綴，避免測試之間ID撞擊
TEST_PREFIX = f"TEST-{uuid.uuid4().hex[:8]}"
TEMPLATE_CODING = "10.1"   # F type coding template
TEMPLATE_SKILL = "10.21"   # S type skill template

_LIVE_DB = ds.get_db_path()


def _is_live_path(path) -> bool:
    try:
        return Path(str(path)).resolve() == Path(str(_LIVE_DB)).resolve()
    except OSError:
        return False


@pytest.fixture(autouse=True)
def _iso_db(tmp_path, monkeypatch):
    """Run this module against a COPY of the live DB.

    The copy keeps the REAL schema and the REAL `skill_template` rows the tests
    read (TEMPLATE_CODING / TEMPLATE_SKILL), while writing nowhere near the live
    store. A hand-built fixture would be a SECOND schema declaration and could
    drift from db_schema.py.

    BOTH default-path bindings are redirected, because this module does not pass
    `db_path=`: `llm_task_center.DEFAULT_DB` and `skill_prompt.DEFAULT_DB` are the
    SAME object today, but rebinding one does not rebind the other. Patching both
    is the honest repair; the live-DB sentinel in conftest.py is what catches a
    future THIRD binding.
    """
    dest = Path(str(tmp_path)) / "agent_copy.db"
    shutil.copy2(str(_LIVE_DB), str(dest))
    if _is_live_path(dest):
        raise RuntimeError("isolation fixture produced the LIVE path")

    import skill_prompt
    monkeypatch.setattr(ltc, "DEFAULT_DB", dest, raising=False)
    monkeypatch.setattr(skill_prompt, "DEFAULT_DB", dest, raising=False)

    conn = sqlite3.connect(str(dest))
    try:
        row = [r for r in conn.execute("PRAGMA database_list").fetchall() if r[1] == "main"]
        got = row[0][2] if row else ""
    finally:
        conn.close()
    if _is_live_path(got):
        raise RuntimeError("isolation guard: the copy resolved to the live DB")
    yield dest


def clean_test_records(db_path=None):
    """清理今次測試產生嘅所有task_instances + experience_log 回寫

    Was the ONLY cleanup, and it ran only from `main()`. The isolation fixture now
    makes the copy the sole store this module touches, so this is tidiness on the
    copy -- NOT the thing correctness depends on.
    """
    all_inst = ltc.list_instances(db_path=db_path)
    for inst in all_inst:
        if inst["task_id"].startswith(TEST_PREFIX):
            ltc.delete_instance(inst["task_id"], db_path=db_path)
    # 清理測試寫入 template experience_log 嘅 entries（task_id 以 TEST_PREFIX 開頭）
    for tid in (TEMPLATE_CODING, TEMPLATE_SKILL):
        tpl = ltc.get_template(tid, db_path=db_path)
        if not tpl:
            continue
        exp = tpl.get("experience_log")
        entries = exp if isinstance(exp, list) else (json.loads(exp) if isinstance(exp, str) else [])
        if not isinstance(entries, list):
            continue
        kept = [e for e in entries if not str(e.get("task_id", "")).startswith(TEST_PREFIX)]
        if len(kept) != len(entries):
            import skill_prompt
            conn = skill_prompt._connect(db_path)
            conn.execute(
                "UPDATE skill_template SET experience_log=?, updated_at=? WHERE template_id=?",
                (json.dumps(kept, ensure_ascii=False), ltc._utc_now(), tid),
            )
            conn.commit()
            conn.close()

def test_hardgate_cases():
    print("\n===== GROUP 1: Hard Gate 提交校驗 =====")
    cases = [
        {
            "name": "CASE1: task_id 空字串 → 拒絕 MISSING_TASK_ID",
            "task_id": "",
            "template_id": TEMPLATE_SKILL,
            "expect_ok": False,
            "expect_code": "MISSING_TASK_ID"
        },
        {
            "name": "CASE2: task_id 全部空白空格 → 拒絕 MISSING_TASK_ID",
            "task_id": "   ",
            "template_id": TEMPLATE_SKILL,
            "expect_ok": False,
            "expect_code": "MISSING_TASK_ID"
        },
        {
            "name": "CASE3: template_id 不存在 → 拒絕 UNKNOWN_TEMPLATE",
            "task_id": f"{TEST_PREFIX}-03",
            "template_id": "99.99",
            "expect_ok": False,
            "expect_code": "UNKNOWN_TEMPLATE"
        },
        {
            "name": "CASE4: 合法task + 合法template → 建立成功，預設status=pending",
            "task_id": f"{TEST_PREFIX}-04",
            "template_id": TEMPLATE_SKILL,
            "agent_worker_id": "worker_01",
            "payload": {"job": "demo"},
            "expect_ok": True,
            "expect_status": "pending"
        },
        {
            "name": "CASE5: 重複提交同一task_id → 拒絕 DUP_TASK_ID",
            "task_id": f"{TEST_PREFIX}-04",
            "template_id": TEMPLATE_SKILL,
            "expect_ok": False,
            "expect_code": "DUP_TASK_ID"
        }
    ]
    for c in cases:
        print(f"\nRun: {c['name']}")
        res = ltc.submit_task(
            task_id=c.get("task_id"),
            template_id=c.get("template_id"),
            agent_worker_id=c.get("agent_worker_id"),
            payload=c.get("payload", {}),
            validate_8dim=False,  # lifecycle tests use arbitrary payloads
        )
        print(f"  Result: {res}")
        if c["expect_ok"]:
            assert res["ok"] is True
            inst = ltc.get_instance(c["task_id"])
            assert inst["status"] == c["expect_status"]
            print(f"  ✅ PASS: status match {c['expect_status']}")
        else:
            assert res["ok"] is False
            assert res["code"] == c["expect_code"]
            print(f"  ✅ PASS: error code match {c['expect_code']}")

def test_task_state_machine():
    print("\n===== GROUP 2: Task 生命週期 / 狀態機 =====")
    task_id = f"{TEST_PREFIX}-lifecycle-01"
    # Step1 提交新任務，pending
    print("\nStep1: submit task → pending")
    sub = ltc.submit_task(task_id, TEMPLATE_CODING, agent_worker_id="coding_worker", payload={"file":"test.py"}, validate_8dim=False)
    assert sub["ok"] is True
    inst = ltc.get_instance(task_id)
    assert inst["status"] == "pending"
    assert inst["finished_at"] is None
    print("  ✅ Step1 PASS: pending, finished_at null")

    # Step2 更新為 running
    print("\nStep2: update status running, fill trace snippet")
    upd = ltc.update_instance(
        task_id=task_id,
        status="running",
        trace_log="start coding, read spec"
    )
    assert upd["ok"] is True
    inst = ltc.get_instance(task_id)
    assert inst["status"] == "running"
    print("  ✅ Step2 PASS: status=running")

    # Step3 更新為 success，寫完整trace + finished_at
    print("\nStep3: update status success, set finished_at + full trace")
    upd = ltc.update_instance(
        task_id=task_id,
        status="success",
        trace_log="code complete, syntax ok, all test passed",
        finished_at="auto"
    )
    assert upd["ok"] is True
    inst = ltc.get_instance(task_id)
    assert inst["status"] == "success"
    assert inst["finished_at"] is not None
    assert "code complete" in inst["trace_log"]
    print("  ✅ Step3 PASS: success, finished_at filled")

    # Step4 驗證report同coding filter可以揀到呢條實例
    print("\nStep4: verify api filter / report")
    all_insts = ltc.list_instances()
    found = any(i["task_id"] == task_id for i in all_insts)
    assert found
    coding_list = [i for i in ltc.list_instances() if i["item_type"] in ("F","D")]
    assert any(i["task_id"] == task_id for i in coding_list)
    print("  ✅ Step4 PASS: coding filter include this instance")

    # Step5 另一分支：pending → failed
    print("\nStep5: new task, flow pending → failed")
    task_fail_id = f"{TEST_PREFIX}-lifecycle-fail"
    ltc.submit_task(task_fail_id, TEMPLATE_CODING, agent_worker_id="coding_worker", validate_8dim=False)
    upd_fail = ltc.update_instance(
        task_id=task_fail_id,
        status="failed",
        trace_log="syntax error, cannot build",
        finished_at="auto"
    )
    assert upd_fail["ok"]
    inst_fail = ltc.get_instance(task_fail_id)
    assert inst_fail["status"] == "failed"
    print("  ✅ Step5 PASS: failed case ok")

def test_boundary_cases():
    print("\n===== GROUP3: 邊界case測試 =====")
    # 修改不存在task
    print("\nCASE: update non-exist task_id")
    res = ltc.update_instance(task_id=f"{TEST_PREFIX}-notexist", status="running")
    assert res["ok"] is False
    assert res["code"] == "TASK_NOT_FOUND"
    print("  ✅ PASS: update non-exist task reject")

def test_experience_log_writeback():
    print("\n===== GROUP4: experience_log 回寫機制 =====")
    task_id = f"{TEST_PREFIX}-exp-01"
    # Step1 提交 coding task
    print("\nStep1: submit coding task")
    sub = ltc.submit_task(task_id, TEMPLATE_CODING, agent_worker_id="coding_worker", payload={"file":"test.py"}, validate_8dim=False)
    assert sub["ok"] is True
    print("  ✅ Step1 PASS: submitted")

    # Step2 更新為 success，寫入 trace
    print("\nStep2: update status success with trace")
    upd = ltc.update_instance(
        task_id=task_id,
        status="success",
        trace_log="code complete, syntax ok, all test passed",
        finished_at="auto"
    )
    assert upd["ok"] is True
    assert upd.get("experience_appended") is True
    print("  ✅ Step2 PASS: experience_appended=True")

    # Step3 讀取 template，確認 trace 已寫入 experience_log
    print("\nStep3: read template experience_log")
    tpl = ltc.get_template(TEMPLATE_CODING)
    assert tpl is not None
    exp = tpl.get("experience_log")
    assert exp is not None, "experience_log should not be None after write-back"
    # get_template parses JSON; exp may be a list or a JSON string
    entries = exp if isinstance(exp, list) else (json.loads(exp) if isinstance(exp, str) else [])
    assert isinstance(entries, list) and len(entries) >= 1
    last = entries[-1]
    assert last["task_id"] == task_id
    assert last["status"] == "success"
    assert "code complete" in last["trace"]
    print(f"  ✅ Step3 PASS: experience_log has {len(entries)} entries, last={last['task_id']}")

def test_8dim_hard_gate():
    """B2: submit_task 8-dim Hard Gate — validate payload before insert."""
    print("\n===== GROUP5: 8-dim Hard Gate (B2) =====")
    # CASE1: 合法 8-dim payload → 提交成功
    print("\nCASE1: valid 8-dim payload → submit ok")
    valid_payload = {
        "task": "Implement validate_new_task",
        "channel": "local_pc",
        "module": "task_center",
        "capability": "task_center.validate_new_task",
        "api": "POST /api/tasks/validate",
        "function": "task_center.validate_new_task",
        "table": "code_registry",
        "field": "register_id,module_name",
    }
    tid = f"{TEST_PREFIX}-8dim-valid"
    res = ltc.submit_task(tid, TEMPLATE_CODING, payload=valid_payload)
    assert res["ok"] is True, f"expected ok, got {res}"
    inst = ltc.get_instance(tid)
    assert inst is not None
    print("  ✅ PASS: valid 8-dim submitted")

    # CASE2: 無效 payload（未知 top-level key）→ 拒絕 VALIDATION_FAILED
    print("\nCASE2: invalid payload (unknown key) → reject VALIDATION_FAILED")
    bad_payload = {"task": "x", "channel": "web", "bogus_key": "nope"}
    tid2 = f"{TEST_PREFIX}-8dim-bad"
    res = ltc.submit_task(tid2, TEMPLATE_CODING, payload=bad_payload)
    assert res["ok"] is False
    assert res["code"] == "VALIDATION_FAILED", f"expected VALIDATION_FAILED, got {res.get('code')}"
    assert "errors" in res and res["errors"]
    # 確認冇插入
    assert ltc.get_instance(tid2) is None, "rejected task must NOT be inserted"
    print("  ✅ PASS: invalid payload rejected, not inserted")

    # CASE3: validate_8dim=False → 任意 payload 照樣提交
    print("\nCASE3: validate_8dim=False → arbitrary payload submits")
    tid3 = f"{TEST_PREFIX}-8dim-skip"
    res = ltc.submit_task(tid3, TEMPLATE_CODING, payload={"file": "test.py"}, validate_8dim=False)
    assert res["ok"] is True
    print("  ✅ PASS: validate_8dim=False bypasses gate")

def main():
    print(f"Test suite start, test prefix = {TEST_PREFIX}")
    try:
        test_hardgate_cases()
        test_task_state_machine()
        test_boundary_cases()
        test_experience_log_writeback()
        test_8dim_hard_gate()
        print("\n🎉 ALL TESTS PASSED")
    except Exception as e:
        print(f"\n❌ TEST FAILED: {repr(e)}")
        raise
    finally:
        clean_test_records()
        print(f"\nCleanup done, all test records with prefix {TEST_PREFIX} removed")

if __name__ == "__main__":
    main()