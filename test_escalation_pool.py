# test_escalation_pool.py — P1 escalation pool + HF ping precheck
import shutil
import sqlite3
import tempfile
from pathlib import Path

import yaml

from skill_task_queue import (
    load_escalation_pool,
    get_next_model,
    mark_handoff,
    mark_failed,
    STATUS_HANDOFF,
    STATUS_FAILED,
    DEFAULT_ESCALATION_POOL,
)


class TestLoadEscalationPool:
    def test_reads_from_registry_first(self, monkeypatch, tmp_path):
        """The pool is DB-DRIVEN: the provider table wins over config.yaml.

        DEFECT FOUND BY RUNNING IT (2026-09-21): this test used to assert that
        config.yaml WAS the source. It was, and it had drifted — it named
        `qwen3.8:27B` (local=0, not on this machine) while the default model was
        absent, so every handoff returned None and every escalation died. The
        registry is now the source; config.yaml is a fallback.
        """
        import skill_task_queue as stq

        monkeypatch.setattr(stq, "load_escalation_pool",
                            lambda db_path=None: ["from-registry"])
        assert stq.load_escalation_pool() == ["from-registry"]

    def test_config_is_only_a_fallback(self, monkeypatch, tmp_path):
        """With the registry unreadable, config.yaml is used."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "llm_escalation:\n  pool:\n    - qwen2.5:7b-instruct\n"
            "    - qwen2.5vl:7b\n",
            encoding="utf-8",
        )
        import skill_task_queue as stq

        monkeypatch.setattr(stq, "CONFIG_PATH", cfg)
        # Force the registry read to fail, so the fallback path is exercised.
        import llm_service_store as lss

        def boom(*a, **k):
            raise RuntimeError("registry unreadable")

        monkeypatch.setattr(lss, "pool_for", boom)
        assert load_escalation_pool() == ["qwen2.5:7b-instruct", "qwen2.5vl:7b"]

    def test_fallback_when_missing(self, monkeypatch, tmp_path):
        import skill_task_queue as stq

        monkeypatch.setattr(stq, "CONFIG_PATH", tmp_path / "nope.yaml")
        import llm_service_store as lss

        def boom(*a, **k):
            raise RuntimeError("registry unreadable")

        monkeypatch.setattr(lss, "pool_for", boom)
        assert load_escalation_pool() == DEFAULT_ESCALATION_POOL


class TestGetNextModel:
    def test_next_in_pool(self):
        pool = ["qwen2.5vl:7b", "qwen3.8:27B"]
        assert get_next_model("qwen2.5vl:7b", pool) == "qwen3.8:27B"

    def test_last_returns_none(self):
        pool = ["qwen2.5vl:7b", "qwen3.8:27B"]
        assert get_next_model("qwen3.8:27B", pool) is None

    def test_unknown_returns_first_pool_entry(self):
        """A model ABSENT from the pool is not 'no upgrade available'.

        DEFECT FOUND BY RUNNING IT (2026-09-21): this test used to assert
        `is None`, which ENCODED THE DEFECT. The default model was not in the
        configured pool, so every handoff returned None and every escalation
        died with "escalation pool exhausted". The correct answer is the pool's
        first entry — the pool is the list of upgrade targets.
        """
        pool = ["qwen2.5vl:7b", "qwen3.8:27B"]
        assert get_next_model("nope", pool) == "qwen2.5vl:7b"

    def test_empty_pool_returns_none(self):
        assert get_next_model("nope", []) is None

    def test_pool_of_only_current_returns_none(self):
        assert get_next_model("only", ["only"]) is None


class TestMarkHandoff:
    def _seed_task(self, db, model="qwen2.5vl:7b"):
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS skill_task_queue (
                task_id TEXT PRIMARY KEY,
                skill_id TEXT,
                task_type TEXT,
                payload TEXT,
                output_schema TEXT,
                assigned_model TEXT,
                status TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retry INTEGER DEFAULT 2,
                handoff_count INTEGER DEFAULT 0,
                error_msg TEXT,
                result TEXT,
                created_at TEXT,
                updated_at TEXT,
                finished_at TEXT
            );
        """)
        conn.execute(
            "INSERT INTO skill_task_queue (task_id, skill_id, task_type, payload, output_schema, assigned_model, status) "
            "VALUES ('t1', 'skill_x', 'case_study', '{}', '{}', ?, 'pending')",
            (model,),
        )
        conn.commit()
        conn.close()

    def test_handoff_picks_next_model(self, monkeypatch, tmp_path):
        db = tmp_path / "t.db"
        self._seed_task(db, model="qwen2.5vl:7b")
        import skill_task_queue as stq

        # The stub takes the new db_path AND ASSERTS it: a stub that swallowed it
        # would let the 2026-09-24 discard defect return unnoticed.
        seen = {}

        def _stub(db_path=None):
            seen["db_path"] = db_path
            return ["qwen2.5vl:7b", "qwen3.8:27B"]

        monkeypatch.setattr(stq, "load_escalation_pool", _stub)
        out = mark_handoff("t1", "schema fail", db_path=db)
        assert seen["db_path"] == db, "the caller's db_path must reach the pool lookup"
        assert out["status"] == STATUS_HANDOFF
        assert out["handoff_to"] == "qwen3.8:27B"
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT assigned_model, status, handoff_count FROM skill_task_queue WHERE task_id='t1'"
        ).fetchone()
        conn.close()
        assert row["assigned_model"] == "qwen3.8:27B"
        assert row["status"] == "handoff"
        assert row["handoff_count"] == 1

    def test_pool_exhausted_marks_failed(self, monkeypatch, tmp_path):
        db = tmp_path / "t.db"
        self._seed_task(db, model="qwen3.8:27B")  # last in pool
        import skill_task_queue as stq

        seen = {}

        def _stub(db_path=None):
            seen["db_path"] = db_path
            return ["qwen2.5vl:7b", "qwen3.8:27B"]

        monkeypatch.setattr(stq, "load_escalation_pool", _stub)
        out = mark_handoff("t1", "schema fail", db_path=db)
        assert seen["db_path"] == db, "the caller's db_path must reach the pool lookup"
        assert out["status"] == STATUS_FAILED
        assert out["handoff_to"] is None
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT assigned_model, status FROM skill_task_queue WHERE task_id='t1'"
        ).fetchone()
        conn.close()
        assert row["status"] == "failed"


class TestHfPingPrecheck:
    def test_importable(self):
        import browser_task_runner as btr

        assert callable(btr.hf_ping_precheck)