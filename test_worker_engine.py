# test_worker_engine.py
import tempfile
from pathlib import Path
from unittest.mock import patch

import coord_store
from worker_engine import run_task_worker, parse_llm_result, make_hash_chain


def test_parse_llm_result_pass():
    text = """
VERDICT: PASS
DETAIL: channel test ok
ERROR: none
"""
    verdict, detail, error = parse_llm_result(text)
    assert verdict == "PASS"
    assert "channel test ok" in detail
    assert error is None

def test_parse_llm_result_fail():
    text = """
VERDICT: FAIL
DETAIL: timeout
ERROR: connect failed
"""
    verdict, detail, error = parse_llm_result(text)
    assert verdict == "FAIL"
    assert error == "connect failed"

def test_hash_chain_different():
    h1 = make_hash_chain("", "1.1D", "prompt1")
    h2 = make_hash_chain("", "1.1D", "prompt2")
    assert h1 != h2

@patch("worker_engine.call_local_qwen")
@patch("worker_engine.requests.post")
def test_run_task_worker_success(mock_post, mock_call):
    mock_call.return_value = {
        "message": {"content": "VERDICT: PASS\nDETAIL: ok\nERROR: none"}
    }
    mock_post.return_value.json.return_value = {
        "id": 1,
        "task_id": "1.1D",
        "verdict": "PASS"
    }
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status = lambda: None
    res = run_task_worker("1.1D", "test prompt", "sess_001")
    assert res["task_id"] == "1.1D"
    assert res["verdict"] == "PASS"
    # fallback template still appended when task is None
    called_prompt = mock_call.call_args[0][0]
    assert "VERDICT:" in called_prompt
    post_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert post_json["prompt_setting_key"] == "verdict_3line"


@patch("worker_engine.call_local_qwen")
@patch("worker_engine.requests.post")
def test_run_task_worker_uses_task_template(mock_post, mock_call):
    mock_call.return_value = {
        "message": {"content": "VERDICT: PASS\nDETAIL: ok\nERROR: none"}
    }
    mock_post.return_value.json.return_value = {
        "id": 2,
        "task_id": "1.1D",
        "verdict": "PASS",
        "prompt_setting_key": "custom_fmt",
    }
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status = lambda: None
    task = {
        "prompt_setting_id": 9,
        "prompt_setting_key": "custom_fmt",
        "prompt_setting_instruction": "\nCUSTOM_TEMPLATE_MARKER\n",
        "catalog_id": 1,
        "subcatalog_id": 1,
        "catalog_name": "membership",
        "subcatalog_name": "rest_channel",
    }
    res = run_task_worker("1.1D", "biz prompt", "sess_002", task=task)
    assert res["verdict"] == "PASS"
    assert "CUSTOM_TEMPLATE_MARKER" in mock_call.call_args[0][0]
    post_json = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
    assert post_json["prompt_setting_id"] == 9
    assert post_json["prompt_setting_key"] == "custom_fmt"
    assert post_json["catalog_name"] == "membership"


def test_seed_and_get_prompt_setting():
    tmp = Path(tempfile.mkdtemp()) / "coords.db"
    coord_store.seed_format_template_sample(tmp)
    row = coord_store.get_format_template_by_key("verdict_3line", db_path=tmp)
    assert row is not None
    assert row["prompt_setting_key"] == "verdict_3line"
    assert "VERDICT:" in row["instruction"]
    by_id = coord_store.get_format_template_by_id(int(row["id"]), db_path=tmp)
    assert by_id is not None
    assert by_id["id"] == row["id"]


def test_check_record_template_snapshot():
    tmp = Path(tempfile.mkdtemp()) / "coords.db"
    row = coord_store.create_check_record(
        {
            "task_id": "1.1D",
            "verdict": "PASS",
            "prompt_setting_id": 1,
            "prompt_setting_key": "verdict_3line",
        },
        db_path=tmp,
    )
    assert row["prompt_setting_id"] == 1
    assert row["prompt_setting_key"] == "verdict_3line"
