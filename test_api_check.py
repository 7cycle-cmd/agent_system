# test_api_check.py
from fastapi.testclient import TestClient
import tempfile
from pathlib import Path
import coord_store
from main_api import app

client = TestClient(app)

def setup_module():
    tmp_dir = tempfile.mkdtemp()
    coord_store.DB_PATH = str(Path(tmp_dir) / "coords.db")
    coord_store.init_table()

def test_check_create():
    payload = {
        "task_id": "1.1D",
        "session_id": "sess_001",
        "hash_chain": "hash_abc123",
        "detail": "channel test ok",
        "verdict": "PASS",
        "error": None,
        "prompt_setting_id": 1,
        "prompt_setting_key": "verdict_3line",
    }
    resp = client.post("/api/check", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == "1.1D"
    assert data["verdict"] == "PASS"
    assert data["prompt_setting_id"] == 1
    assert data["prompt_setting_key"] == "verdict_3line"

def test_check_get_by_id():
    payload = {"task_id": "1.1D", "verdict": "FAIL", "error": "connection failed"}
    post_resp = client.post("/api/check", json=payload)
    check_id = post_resp.json()["id"]
    resp = client.get(f"/api/check/{check_id}")
    assert resp.status_code == 200
    assert resp.json()["error"] == "connection failed"

def test_check_list_by_task_id():
    client.post("/api/check", json={"task_id": "1.1D", "verdict": "PASS"})
    client.post("/api/check", json={"task_id": "1.1D", "verdict": "FAIL"})
    resp = client.get("/api/check/task/1.1D")
    assert resp.status_code == 200
    records = resp.json()
    assert len(records) >= 2

def test_check_update():
    post_resp = client.post("/api/check", json={"task_id": "1.1D", "verdict": "PASS"})
    check_id = post_resp.json()["id"]
    update_payload = {"task_id": "1.1D", "verdict": "FAIL", "error": "timeout"}
    resp = client.put(f"/api/check/{check_id}", json=update_payload)
    assert resp.status_code == 200
    assert resp.json()["verdict"] == "FAIL"
    assert resp.json()["error"] == "timeout"

def test_check_delete():
    post_resp = client.post("/api/check", json={"task_id": "1.1D"})
    check_id = post_resp.json()["id"]
    resp = client.delete(f"/api/check/{check_id}")
    assert resp.status_code == 200
    get_resp = client.get(f"/api/check/{check_id}")
    assert get_resp.status_code == 404
