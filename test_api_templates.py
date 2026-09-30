# test_api_templates.py
import tempfile
from pathlib import Path

import coord_store
from fastapi.testclient import TestClient
from main_api import app

client = TestClient(app)


def setup_module():
    tmp_dir = tempfile.mkdtemp()
    coord_store.DB_PATH = Path(tmp_dir) / "coords.db"
    coord_store.init_format_template_table()


def test_list_includes_verdict_3line():
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    rows = resp.json()
    assert isinstance(rows, list)
    keys = {r["prompt_setting_key"] for r in rows}
    assert "verdict_3line" in keys


def test_create_get_update_template():
    payload = {
        "prompt_setting_key": "custom_fmt_a",
        "name": "Custom A",
        "instruction": "\nLINE_A\n",
        "description": "notes",
        "catalog_id": 1,
    }
    resp = client.post("/api/templates", json=payload)
    assert resp.status_code == 200, resp.text
    created = resp.json()
    tid = created["id"]
    assert created["prompt_setting_key"] == "custom_fmt_a"

    get_resp = client.get(f"/api/templates/{tid}")
    assert get_resp.status_code == 200
    assert get_resp.json()["instruction"] == "\nLINE_A\n"
    assert "skill_ref_count" in get_resp.json()

    upd = client.put(
        f"/api/templates/{tid}",
        json={"instruction": "\nLINE_B\n", "name": "Custom B"},
    )
    assert upd.status_code == 200
    assert upd.json()["instruction"] == "\nLINE_B\n"
    assert upd.json()["name"] == "Custom B"


def test_duplicate_key_conflict():
    payload = {
        "prompt_setting_key": "dup_key_x",
        "name": "Dup",
        "instruction": "x",
    }
    assert client.post("/api/templates", json=payload).status_code == 200
    resp = client.post("/api/templates", json=payload)
    assert resp.status_code == 409


def test_cannot_delete_verdict_3line():
    rows = client.get("/api/templates").json()
    v = next(r for r in rows if r["prompt_setting_key"] == "verdict_3line")
    resp = client.delete(f"/api/templates/{v['id']}")
    assert resp.status_code == 409
    assert "verdict_3line" in resp.json()["detail"]


def test_soft_delete_custom():
    payload = {
        "prompt_setting_key": "to_soft_delete",
        "name": "Temp",
        "instruction": "bye",
    }
    tid = client.post("/api/templates", json=payload).json()["id"]
    resp = client.delete(f"/api/templates/{tid}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["mode"] == "soft"

    active = client.get("/api/templates").json()
    assert all(r["id"] != tid for r in active)

    all_rows = client.get("/api/templates?all=1").json()
    row = next(r for r in all_rows if r["id"] == tid)
    assert int(row["is_active"]) == 0


def test_revive_soft_deleted_key():
    """TDD 1-3: create -> soft delete -> create same key REVIVES, not 409.

    Without revival a soft-deleted key is PERMANENTLY BURNED, because the UNIQUE
    constraint still sees the is_active=0 row. That is a leak, not soft delete.
    """
    key = "revive_me"
    payload = {"prompt_setting_key": key, "name": "First", "instruction": "v1"}

    # 1. normal create
    first = client.post("/api/templates", json=payload)
    assert first.status_code == 200, first.text
    tid = first.json()["id"]

    # 2. soft delete
    assert client.delete(f"/api/templates/{tid}").status_code == 200
    all_rows = client.get("/api/templates?all=1").json()
    assert int(next(r for r in all_rows if r["id"] == tid)["is_active"]) == 0

    # 3. same key again -> REVIVED (same id, is_active=1, fields updated)
    again = client.post(
        "/api/templates",
        json={"prompt_setting_key": key, "name": "Second", "instruction": "v2"},
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == tid, "must REVIVE the same row, not insert a new one"
    assert int(again.json()["is_active"]) == 1
    assert again.json()["name"] == "Second"
    assert again.json()["instruction"] == "v2"

    # and it is visible in the active list again
    active = client.get("/api/templates").json()
    assert any(r["id"] == tid for r in active)


def test_active_key_still_conflicts():
    """TDD 4: an ACTIVE row with the same key must STILL be 409 — never overwritten."""
    key = "active_owner"
    payload = {"prompt_setting_key": key, "name": "Owner", "instruction": "keep me"}
    first = client.post("/api/templates", json=payload)
    assert first.status_code == 200, first.text
    tid = first.json()["id"]

    clash = client.post(
        "/api/templates",
        json={"prompt_setting_key": key, "name": "Intruder", "instruction": "overwrite"},
    )
    assert clash.status_code == 409, "an ACTIVE key must never be overwritten"

    # the original row is untouched
    row = client.get(f"/api/templates/{tid}").json()
    assert row["name"] == "Owner"
    assert row["instruction"] == "keep me"


def test_preview_endpoint():
    resp = client.post(
        "/api/templates/preview",
        json={"sample_prompt": "TASK", "instruction": "\nFMT"},
    )
    assert resp.status_code == 200
    assert resp.json()["final_prompt"] == "TASK\nFMT"


def test_llm_templates_page():
    resp = client.get("/prompt/setting/")
    assert resp.status_code == 200
    assert "Prompt Setting" in resp.text
    assert "/api/templates" in resp.text


def test_legacy_llm_templates_route_still_works():
    """The old route is a PERMANENT alias — a broken bookmark cannot be recovered."""
    for old in ("/llm-templates/", "/llm_templates/", "/llm-templates", "/llm_templates"):
        resp = client.get(old)
        assert resp.status_code == 200, old
        assert "Prompt Setting" in resp.text, old


def test_settings_nav_link():
    resp = client.get("/settings")
    assert resp.status_code == 200
    # The nav link must point at the CANONICAL path. It previously emitted
    # `/llm-tasks/llm_templates` (underscore) while the route was
    # `/llm-templates` (hyphen) — the mismatch that made this test fail.
    assert "/llm-tasks/prompt_setting" in resp.text
    assert "target_name" in resp.text
    assert "X" in resp.text
