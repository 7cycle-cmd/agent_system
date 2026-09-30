# test_phone_100_proof_runner.py — orchestration layer (Step 4-5)
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from phone_100_proof_runner import (
    run_proof,
    _write_qc_evidence,
    CANDIDATE_POOL,
    DIM_SUGGESTED_MODEL,
    DIM_FROM_MODEL,
    DIM_CAUSE,
    DIM_FAIL_EVIDENCE_REF,
)


def _fresh_db():
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "t.db"
    return tmp, db


class TestQualifiedPath:
    def test_7b_qualified_writes_assignment(self):
        # llm=False -> oracle answers -> 7b reaches QUALIFIED (streak 110)
        tmp, db = _fresh_db()
        try:
            out = run_proof(seed=7, db_path=db, llm=False, cap=1000)
            assert out["verdict"] == "QUALIFIED"
            assert out["qualified"] is not None
            assert out["qualified"]["model"] == "qwen2.5:7b-instruct"
            # field_tdd_rule active:true + proof (located by slice_key/field_name)
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT rule_json, status FROM field_tdd_rule WHERE slice_key = '1.1F' AND field_name = 'phone'"
            ).fetchone()
            conn.close()
            assert row is not None
            rule = json.loads(row["rule_json"])
            assert rule.get("active") is True
            assert rule["proof"]["model"] == "qwen2.5:7b-instruct"
            assert rule["proof"]["streak"] == 110
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_qc_evidence_written(self):
        tmp, db = _fresh_db()
        try:
            out = run_proof(seed=7, db_path=db, llm=False, cap=1000)
            ev = Path(out["results"][0]["evidence_ref"])
            assert ev.is_file()
            data = json.loads(ev.read_text(encoding="utf-8"))
            assert data["verdict"] == "QUALIFIED"
            assert data["model"] == "qwen2.5:7b-instruct"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestHandoffPath:
    def test_all_fail_creates_handoff_and_not_qualified_all(self):
        # Force both models to fail: use a cap so small that streak never reaches 110
        # with llm=False (oracle always correct -> would qualify). Instead, force
        # failure by making oracle mismatch impossible is hard; so we simulate by
        # monkeypatching run_harness to return NOT_QUALIFIED.
        import phone_100_proof_runner as mod

        orig = mod.run_harness
        calls = []

        def fake_run_harness(*, model, **kw):
            calls.append(model)
            return {
                "verdict": "NOT_QUALIFIED",
                "seed": kw.get("seed"),
                "model": model,
                "rounds_run": 1000,
                "final_streak": 0,
                "max_streak": 3,
                "rule_version": 2,
                "failures": [
                    {"round_no": 1, "value": "1_234", "oracle": "YES", "llm": "NO",
                     "win": 0, "failure_reason": "false NO (oracle=YES) value='1_234'"}
                ],
            }

        mod.run_harness = fake_run_harness
        tmp, db = _fresh_db()
        try:
            out = run_proof(seed=7, db_path=db, llm=True, cap=1000)
            assert calls == CANDIDATE_POOL  # both models ran
            assert out["verdict"] == "NOT_QUALIFIED_ALL"
            assert out["qualified"] is None
            # handoff created for 7b -> deepseek
            assert len(out["handoffs"]) == 1
            h = out["handoffs"][0]
            assert h["from_model"] == "qwen2.5:7b-instruct"
            assert h["suggested_model"] == "deepseek-v4-flash"
            # task_ssot dims on handoff task
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT dim_key, value_text FROM task_ssot WHERE task_id = ?",
                (h["task_id"],),
            ).fetchall()
            conn.close()
            dims = {r["dim_key"]: r["value_text"] for r in rows}
            assert dims[DIM_FROM_MODEL] == "qwen2.5:7b-instruct"
            assert dims[DIM_SUGGESTED_MODEL] == "deepseek-v4-flash"
            assert dims[DIM_CAUSE] == "model_capability_gap"
            assert dims[DIM_FAIL_EVIDENCE_REF].endswith("phone_100_run_qwen2.5_7b-instruct.json")
        finally:
            mod.run_harness = orig
            shutil.rmtree(tmp, ignore_errors=True)


class TestWriteQcEvidence:
    def test_filename_sanitized(self):
        tmp, db = _fresh_db()
        try:
            p = _write_qc_evidence("deepseek-v4-flash", {"verdict": "NOT_QUALIFIED", "seed": 1})
            assert p.name == "phone_100_run_deepseek-v4-flash.json"
            assert p.is_file()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)