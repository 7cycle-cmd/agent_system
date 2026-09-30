# test_sync_skills_to_dev_tasks.py
"""
冪等 sync_skills_to_dev_tasks() 單元測試
PM 指定 4 條場景：
1. 空表 + 10 個 skill → 建立 10 條 dev_task
2. 已有 10 條 + 再 sync → 0 條新增，0 crash
3. 已有 10 條 + 加 1 新 skill + sync → 只新增 1 條
4. 同一 skill sync 兩次 → 第二次 0 新增（冪等）

用正式 agent.db 嘅 schema（已存在），但用 TEST- 前綴 task_label，
測試完自動刪除，唔污染正式數據。

ISOLATION (2026-09-24): each test now runs on its OWN COPY of agent.db and its
OWN skill folder, so the suite writes nowhere near the live store and the tests
cannot wipe each other's fixture. See `_assert_isolated` for the guard.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from db_schema import (
    ensure_hub_ssot_schema,
    find_task_by_label,
    get_db_path,
    get_dev_tasks,
)
from test_render import sync_skills_to_dev_tasks

# ISOLATION (2026-09-24): the LIVE agent.db is a REFERENCE only - it exists so a
# COPY of it can seed a test, and it is NEVER written to. Before the fix this
# module did `sqlite3.connect(get_db_path())`, so every test INSERTed dev_task,
# module, version_center and task_action_name rows straight into the live store,
# guarded only by a finally: _cleanup() DELETE. Any crash between the INSERT and
# that DELETE left live rows behind, and a `TEST-` label could collide with a
# real row. A test must run on a COPY - the same rule a proof follows.
_LIVE_DB = get_db_path()


def _is_live_path(path):
    """True when `path` resolves to the live agent.db."""
    try:
        return os.path.realpath(str(path)) == os.path.realpath(str(_LIVE_DB))
    except OSError:
        return False


def _assert_isolated(conn):
    """GUARD: refuse to hand back a handle onto the LIVE agent.db.

    Reads the connection's OWN file via PRAGMA database_list, so it cannot be
    fooled by which variable the caller passed. Raises RuntimeError naming the
    file, because the whole point is that a future edit cannot silently
    reconnect the suite to production.
    """
    rows = conn.execute("PRAGMA database_list").fetchall()
    main_file = ""
    for r in rows:
        if r[1] == "main":
            main_file = r[2]
    if not main_file:
        raise RuntimeError("guard: connection has no on-disk 'main' database")
    if _is_live_path(main_file):
        raise RuntimeError(
            "guard: REFUSING to use the LIVE agent.db (%s); a test must run on a COPY"
            % main_file
        )
    return main_file


def _copy_db(tmp_path):
    """Copy the LIVE agent.db into tmp_path so the test writes to a COPY.

    Copying the REAL file (rather than hand-building a minimal DB) means the test
    sees the schema db_schema.py actually creates; a hand-built fixture would be
    a SECOND declaration of that schema and could drift from it.
    """
    dest = Path(str(tmp_path)) / "agent_copy.db"
    shutil.copy2(str(_LIVE_DB), str(dest))
    return dest


def _make_skill_folder(labels, tmp_path):
    """Build a PER-TEST skill folder - one .skill.md per label.

    Was shared module-level `_TMP_DIR` + shutil.rmtree, so all four tests wrote
    the same folder and each build WIPED the others' fixture (order-dependent).
    """
    folder = Path(str(tmp_path)) / "skills"
    folder.mkdir(parents=True, exist_ok=True)
    for i, label in enumerate(labels):
        f = folder / f"skill_{i}.skill.md"
        f.write_text(
            f"---\ntask_id: {label}\nname: skill_{i}\n---\n\nprompt body\n",
            encoding="utf-8",
        )
    return str(folder)


def _fresh_conn(tmp_path):
    """Open a handle onto THIS TEST'S COPY of agent.db, then run the guard."""
    conn = sqlite3.connect(str(_copy_db(tmp_path)))
    conn.row_factory = sqlite3.Row
    _assert_isolated(conn)
    return conn


def _count_dev_tasks(conn, prefix="TEST-"):
    return conn.execute(
        "SELECT COUNT(*) FROM dev_task WHERE task_label LIKE ?", (prefix + "%",)
    ).fetchone()[0]


def test_empty_table_creates_all(tmp_path):
    """Scenario 1: empty table + 10 skills -> 10 dev_task rows created."""
    conn = _fresh_conn(tmp_path)
    try:
        labels = ["TEST-%d" % i for i in range(1, 11)]
        folder = _make_skill_folder(labels, tmp_path)
        result = sync_skills_to_dev_tasks(conn, folder)
        assert result["created"] == 10, "expected 10 created, got %s" % result["created"]
        assert result["skipped"] == 0, "expected 0 skipped, got %s" % result["skipped"]
        assert _count_dev_tasks(conn) == 10, "expected 10 TEST- rows, got %s" % _count_dev_tasks(conn)
    finally:
        conn.close()


def test_resync_no_new(tmp_path):
    """Scenario 2: 10 rows already there + sync again -> 0 created, 0 crash."""
    conn = _fresh_conn(tmp_path)
    try:
        labels = ["TEST-%d" % i for i in range(1, 11)]
        folder = _make_skill_folder(labels, tmp_path)
        sync_skills_to_dev_tasks(conn, folder)
        result = sync_skills_to_dev_tasks(conn, folder)
        assert result["created"] == 0, "expected 0 created, got %s" % result["created"]
        assert result["skipped"] == 10, "expected 10 skipped, got %s" % result["skipped"]
        assert _count_dev_tasks(conn) == 10, "expected 10 TEST- rows, got %s" % _count_dev_tasks(conn)
    finally:
        conn.close()


def test_add_one_new(tmp_path):
    """Scenario 3: 10 rows + 1 new skill -> exactly 1 created."""
    conn = _fresh_conn(tmp_path)
    try:
        labels = ["TEST-%d" % i for i in range(1, 11)]
        folder = _make_skill_folder(labels, tmp_path)
        sync_skills_to_dev_tasks(conn, folder)
        new_folder = _make_skill_folder(labels + ["TEST-11"], tmp_path)
        result = sync_skills_to_dev_tasks(conn, new_folder)
        assert result["created"] == 1, "expected 1 created, got %s" % result["created"]
        assert result["skipped"] == 10, "expected 10 skipped, got %s" % result["skipped"]
        assert _count_dev_tasks(conn) == 11, "expected 11 TEST- rows, got %s" % _count_dev_tasks(conn)
    finally:
        conn.close()


def test_idempotent_twice(tmp_path):
    """Scenario 4: sync the same skills twice -> second run creates 0."""
    conn = _fresh_conn(tmp_path)
    try:
        labels = ["TEST-%d" % i for i in range(1, 6)]
        folder = _make_skill_folder(labels, tmp_path)
        r1 = sync_skills_to_dev_tasks(conn, folder)
        r2 = sync_skills_to_dev_tasks(conn, folder)
        assert r1["created"] == 5, "first run expected 5 created, got %s" % r1["created"]
        assert r2["created"] == 0, "second run expected 0 created, got %s" % r2["created"]
        assert _count_dev_tasks(conn) == 5, "expected 5 TEST- rows, got %s" % _count_dev_tasks(conn)
    finally:
        conn.close()


def main():
    print("===== sync_skills_to_dev_tasks isolation tests =====")
    print("live DB (read-only reference): %s" % _LIVE_DB)
    print("each test copies it into its own temp dir; the live DB is never written")
    for name, fn in (
        ("empty_table_creates_all", test_empty_table_creates_all),
        ("resync_no_new", test_resync_no_new),
        ("add_one_new", test_add_one_new),
        ("idempotent_twice", test_idempotent_twice),
    ):
        d = Path(tempfile.mkdtemp(prefix="tc_sync_%s_" % name))
        fn(d)
        print("  PASS %s" % name)
    print("ALL 4 TESTS PASSED")


if __name__ == "__main__":
    main()