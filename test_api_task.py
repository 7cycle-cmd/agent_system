# test_api_task.py
from fastapi.testclient import TestClient
from main_api import app
import sqlite3
import tempfile
from pathlib import Path
import skill_field_registry

client = TestClient(app)

def setup_module():
    # 臨時DB，唔會污染真實 coords.db
    tmp_dir = tempfile.mkdtemp()
    skill_field_registry.DB_PATH = str(Path(tmp_dir) / "coords.db")
    skill_field_registry.SKILL_ROOT = Path(tmp_dir)
    skill_field_registry.init_table()
    # 建立測試skill
    skill_file = skill_field_registry.SKILL_ROOT / "test.skill.md"
    skill_file.write_text("""---
task_id: "1.1D"
name: "validate_channel_membership"
final_verdict: "INCOMPLETE"
ingested: "yes"
modified_files: []
qc_summary: "test"
reason: "test"
artifacts: ["skill.md"]
schema: "membership table"
---
""", encoding="utf-8")
    skill_field_registry.scan_all_skill_fields()

def test_api_post_task_valid_payload():
    """正確payload，API校驗通過，回傳200"""
    payload = {
        "task_id": "1.1D",
        "name": "validate_channel_membership",
        "final_verdict": "INCOMPLETE",
        "ingested": "yes",
        "modified_files": [],
        "qc_summary": "test",
        "reason": "test reason",
        "artifacts": ["membership_1.1D.skill.md"],
        "schema": "membership table schema"
    }
    resp = client.post("/api/task", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["task_id"] == "1.1D"

def test_api_post_task_missing_required_field():
    """缺失必填欄位，API攔截，回傳400"""
    payload = {
        "task_id": "1.1D",
        "name": "test"
    }
    resp = client.post("/api/task", json=payload)
    assert resp.status_code == 400
    assert "Missing required field" in resp.json()["detail"]

def test_api_post_task_type_mismatch():
    """型態錯誤：modified_files傳string而唔係list，API回傳400"""
    payload = {
        "task_id": "1.1D",
        "name": "validate_channel_membership",
        "final_verdict": "INCOMPLETE",
        "ingested": "yes",
        "modified_files": "[]",
        "qc_summary": "test",
        "reason": "test reason",
        "artifacts": ["membership_1.1D.skill.md"],
        "schema": "membership table schema"
    }
    resp = client.post("/api/task", json=payload)
    assert resp.status_code == 400
    assert "type mismatch" in resp.json()["detail"]

def test_api_post_task_special_char_task_id():
    """task_id包含特殊符號，API正常校驗"""
    payload = {
        "task_id": "DB.FIELD-REG/ISTER_01",
        "name": "special id test",
        "final_verdict": "INCOMPLETE",
        "ingested": "yes",
        "modified_files": [],
        "qc_summary": "ok",
        "reason": "ok",
        "artifacts": [],
        "schema": ""
    }
    resp = client.post("/api/task", json=payload)
    assert resp.status_code == 200
