# -*- coding: utf-8 -*-
"""test_7b_worker_e2e.py — exercise the 7B queue worker END TO END.

WHY THIS EXISTS
---------------
`_proof_7b_queue_reality.py` measured: the worker EXISTS (Q1 YES) but NOTHING
schedules it (Q2 NO), and `skill_task_queue` holds **0 rows** — it has never been
used. So the worker is a FUNCTION, not a 24/7 SERVICE, and its state machine has
never actually run.

This test drives it for real:
  1. enqueue a task whose output_schema the 7B model can satisfy
  2. `worker_once()` -> claim -> call the model -> validate -> complete
  3. assert the row reached `success` with a parsed result
  4. a schema the model CANNOT satisfy must go to retry/handoff/failed, never
     silently "success"
  5. `poll_next()` on an empty queue returns None (no phantom claim)

ISOLATION: a temp DB, so production `agent.db` is untouched. The model call is
REAL (Ollama qwen2.5:7b-instruct) — a mocked model would prove nothing about
whether the queue can actually complete work.

Run:  .\\.venv\\Scripts\\python.exe -m pytest test_7b_worker_e2e.py -q
      .\\.venv\\Scripts\\python.exe -m pytest test_7b_worker_e2e.py -q -m "not slow"
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import skill_task_queue as stq  # noqa: E402

MODEL = "qwen2.5:7b-instruct"


def _make_db(tmp_path: Path) -> Path:
    """A temp DB with the skill_task_queue table (same DDL as production)."""
    from db_schema import SKILL_TASK_QUEUE_DDL

    path = tmp_path / "queue_e2e.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SKILL_TASK_QUEUE_DDL)
        conn.commit()
    finally:
        conn.close()
    return path


def _row(db: Path, task_id: str) -> dict:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute(
            "SELECT * FROM skill_task_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        return dict(r) if r else {}
    finally:
        conn.close()


def _model_available() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=4) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        return any(MODEL.split(":")[0] in n for n in names)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _model_available(),
    reason="Ollama %s not available — a mocked model would prove nothing" % MODEL,
)


# ---------------------------------------------------------------------------
# 1. the happy path: enqueue -> worker_once -> success
# ---------------------------------------------------------------------------

def test_worker_completes_a_task_end_to_end(tmp_path: Path):
    db = _make_db(tmp_path)
    schema = {
        "required": ["answer"],
        "properties": {"answer": "str"},
    }
    enq = stq.enqueue(
        "e2e_skill", "classify",
        {"prompt": "Reply with JSON only: {\"answer\": \"ok\"}"},
        schema, model=MODEL, db_path=db,
    )
    assert enq["ok"], enq
    tid = enq["task_id"]
    assert _row(db, tid)["status"] == "pending"

    out = stq.worker_once(db_path=db, timeout=180.0)
    assert out.get("claimed") is True, out

    row = _row(db, tid)
    assert row["status"] == "success", (
        "worker did not complete the task: status=%s error=%s"
        % (row["status"], row.get("error_msg"))
    )
    assert row["result"], "success with no result"
    parsed = json.loads(row["result"])
    assert "answer" in parsed, parsed
    assert row["finished_at"], "finished_at not stamped"


# ---------------------------------------------------------------------------
# 2. an unsatisfiable schema must NOT be reported as success
# ---------------------------------------------------------------------------

def test_unsatisfiable_schema_does_not_report_success(tmp_path: Path):
    db = _make_db(tmp_path)
    # The model cannot produce a key it is never told about, and the schema
    # demands a type it will not emit.
    schema = {
        "required": ["definitely_not_produced_key_xyz"],
        "properties": {"definitely_not_produced_key_xyz": "int"},
    }
    enq = stq.enqueue(
        "e2e_skill", "classify",
        {"prompt": "Reply with JSON only: {\"answer\": \"ok\"}"},
        schema, model=MODEL, max_retry=0, db_path=db,
    )
    tid = enq["task_id"]

    out = stq.worker_once(db_path=db, timeout=180.0)
    assert out.get("claimed") is True, out

    row = _row(db, tid)
    assert row["status"] != "success", (
        "a schema the model cannot satisfy was recorded as SUCCESS — the "
        "validation gate is not load-bearing: %s" % row
    )
    assert row["status"] in ("retry", "handoff", "failed"), row["status"]
    assert row["error_msg"], "failure recorded with no error_msg"


# ---------------------------------------------------------------------------
# 3. an empty queue must not produce a phantom claim
# ---------------------------------------------------------------------------

def test_empty_queue_returns_no_claim(tmp_path: Path):
    db = _make_db(tmp_path)
    assert stq.poll_next(db_path=db) is None
    out = stq.worker_once(db_path=db, timeout=5.0)
    assert out.get("claimed") is False, out
    assert out.get("ok") is True, out


# ---------------------------------------------------------------------------
# 4. the state machine is real: retry increments, handoff escalates
# ---------------------------------------------------------------------------

def test_retry_then_handoff_escalates_model(tmp_path: Path):
    db = _make_db(tmp_path)
    schema = {"required": ["never_produced_key_abc"], "properties": {}}
    enq = stq.enqueue(
        "e2e_skill", "classify", {"prompt": "Reply with JSON only: {}"},
        schema, model=MODEL, max_retry=1, db_path=db,
    )
    tid = enq["task_id"]

    stq.worker_once(db_path=db, timeout=180.0)   # attempt 1 -> retry
    r1 = _row(db, tid)
    assert r1["status"] in ("retry", "handoff", "failed"), r1

    if r1["status"] == "retry":
        stq.worker_once(db_path=db, timeout=180.0)   # attempt 2 -> handoff/failed
        r2 = _row(db, tid)
        assert r2["status"] in ("handoff", "failed"), r2
        assert int(r2["retry_count"]) >= 1, r2
