# test_api_coord.py
from fastapi.testclient import TestClient
import tempfile
from pathlib import Path
import sqlite3
import coord_store
from main_api import app

client = TestClient(app)

def setup_module():
    # 臨時DB，隔離測試
    tmp_dir = tempfile.mkdtemp()
    coord_store.DB_PATH = str(Path(tmp_dir) / "coords.db")
    coord_store.init_table()

def test_api_coord_get_all_empty():
    """GET /api/coord，空資料庫回傳空list"""
    resp = client.get("/api/coord")
    assert resp.status_code == 200
    assert resp.json() == []

def test_api_coord_post_new():
    """POST新增坐標記錄"""
    payload = {
        "target_name": "VSCode Copy Button",
        "x": 841,
        "y": 761,
        "action": "click",
        "is_active": True,
        "error": None
    }
    resp = client.post("/api/coord", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_name"] == "VSCode Copy Button"
    assert data["x"] == 841
    assert data["y"] == 761
    assert data["action"] == "click"
    assert data["is_active"] is True

def test_api_coord_get_one():
    """GET單條坐標"""
    # 先新增
    payload = {
        "target_name": "Test Button",
        "x": 100,
        "y": 200,
        "action": "click",
        "is_active": True,
        "error": None
    }
    post_resp = client.post("/api/coord", json=payload)
    item_id = post_resp.json()["id"]

    resp = client.get(f"/api/coord/{item_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == item_id
    assert resp.json()["target_name"] == "Test Button"

def test_api_coord_put_update():
    """PUT更新坐標，包括寫error欄位"""
    payload = {
        "target_name": "Old Name",
        "x": 100,
        "y": 200,
        "action": "click",
        "is_active": True,
        "error": None
    }
    post_resp = client.post("/api/coord", json=payload)
    item_id = post_resp.json()["id"]

    update_payload = {
        "target_name": "Updated Name",
        "x": 120,
        "y": 220,
        "action": "click",
        "is_active": False,
        "error": "cannot find target"
    }
    resp = client.put(f"/api/coord/{item_id}", json=update_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["target_name"] == "Updated Name"
    assert data["x"] == 120
    assert data["error"] == "cannot find target"
    assert data["is_active"] is False

def test_api_coord_delete():
    """DELETE刪除坐標"""
    payload = {
        "target_name": "Delete Me",
        "x": 50,
        "y": 50,
        "action": "click",
        "is_active": True,
        "error": None
    }
    post_resp = client.post("/api/coord", json=payload)
    item_id = post_resp.json()["id"]

    resp = client.delete(f"/api/coord/{item_id}")
    assert resp.status_code == 200

    get_resp = client.get(f"/api/coord/{item_id}")
    assert get_resp.status_code == 404

def test_api_settings_page_html():
    """GET /settings 頁面，確認回傳HTML，包含X,Y輸入框"""
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.text
    assert "<html" in html
    assert "X" in html
    assert "Y" in html
    assert "action" in html
    assert "target_name" in html
    assert "/llm-tasks/prompt_setting" in html
