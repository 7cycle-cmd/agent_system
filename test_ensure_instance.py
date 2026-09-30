# test_ensure_instance.py — idempotent task_instances creation
import shutil
import sqlite3
import tempfile
from pathlib import Path

from llm_task_center import ensure_instance, ensure_tables


def _fresh_db():
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "t.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    ensure_tables(conn)
    return tmp, db, conn


class TestEnsureInstance:
    def test_create_new(self):
        tmp, db, conn = _fresh_db()
        try:
            r = ensure_instance("T-1", "10.1", agent_worker_id="w1", db_path=db)
            assert r["ok"] is True
            assert r["created"] is True
            assert r["status"] == "pending"
            row = conn.execute("SELECT * FROM task_instances WHERE task_id='T-1'").fetchone()
            assert row is not None
            assert row["status"] == "pending"
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_idempotent_existing(self):
        tmp, db, conn = _fresh_db()
        try:
            ensure_instance("T-1", "10.1", db_path=db)
            r2 = ensure_instance("T-1", "10.1", db_path=db)
            assert r2["ok"] is True
            assert r2["created"] is False
            assert r2["status"] == "pending"
            # 只應該有 1 行
            n = conn.execute("SELECT COUNT(*) FROM task_instances WHERE task_id='T-1'").fetchone()[0]
            assert n == 1
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_task_id(self):
        tmp, db, conn = _fresh_db()
        try:
            r = ensure_instance("", "10.1", db_path=db)
            assert r["ok"] is False
            assert r["code"] == "MISSING_TASK_ID"
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_payload_serialized(self):
        tmp, db, conn = _fresh_db()
        try:
            r = ensure_instance("T-2", "10.1", payload={"file": "x.py"}, db_path=db)
            assert r["created"] is True
            row = conn.execute("SELECT payload FROM task_instances WHERE task_id='T-2'").fetchone()
            import json
            assert json.loads(row["payload"]) == {"file": "x.py"}
        finally:
            conn.close()
            shutil.rmtree(tmp, ignore_errors=True)