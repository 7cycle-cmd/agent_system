# worker_engine.py
import hashlib
import os
import time
from typing import Optional, Dict, Any
import requests

from coord_store import (
    DEFAULT_VERDICT_3LINE_INSTRUCTION,
    get_format_template_by_key,
)

# Ollama 設定
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
LLM_MODEL = "qwen2.5:7b-instruct"
API_CHECK_URL = os.environ.get("WORKER_API_CHECK_URL", "http://127.0.0.1:18765/api/check")

def make_hash_chain(prev_hash: str, task_id: str, prompt: str) -> str:
    """生成本次check嘅hash，串入上一輪hash，形成hash鏈"""
    raw = f"{prev_hash}|{task_id}|{prompt}|{time.time()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def call_local_qwen(prompt: str, timeout: int = 120) -> Dict[str, Any]:
    """呼叫 Ollama qwen2.5:7b-instruct"""
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _resolve_prompt_setting_fields(task: dict | None) -> tuple[str, int, str]:
    """Return (instruction, prompt_setting_id, prompt_setting_key) with Stage-1 fallback."""
    instruction = ""
    prompt_setting_id = 0
    prompt_setting_key = ""
    if task:
        instruction = str(task.get("prompt_setting_instruction") or "")
        prompt_setting_id = int(task.get("prompt_setting_id") or 0)
        prompt_setting_key = str(task.get("prompt_setting_key") or "")

    if instruction.strip():
        if not prompt_setting_key:
            prompt_setting_key = "verdict_3line"
        return instruction, prompt_setting_id, prompt_setting_key

    # task missing instruction → DB verdict_3line, then constant
    tmpl = None
    try:
        tmpl = get_format_template_by_key("verdict_3line")
    except Exception:
        tmpl = None
    if tmpl:
        return (
            str(tmpl.get("instruction") or DEFAULT_VERDICT_3LINE_INSTRUCTION),
            int(tmpl.get("id") or 0),
            str(tmpl.get("prompt_setting_key") or "verdict_3line"),
        )
    return DEFAULT_VERDICT_3LINE_INSTRUCTION, 0, "verdict_3line"


def run_task_worker(task_id: str, prompt: str, session_id: str, prev_hash: str = "", task: dict = None) -> Dict[str, Any]:
    """
    執行單一task
    1. 附加 task 傳入嘅 prompt setting instruction（fallback verdict_3line），呼叫本地Qwen
    2. 解析verdict、detail、error
    3. 生成新hash_chain
    4. POST /api/check 寫入DB（含分類 + prompt setting 快照）
    """
    prompt_setting_instruction, prompt_setting_id, prompt_setting_key = _resolve_prompt_setting_fields(task)
    print(f"[worker] task={task_id} prompt_setting_id={prompt_setting_id} prompt_setting_key={prompt_setting_key}")
    final_prompt = prompt + prompt_setting_instruction

    try:
        llm_resp = call_local_qwen(final_prompt)
        content = llm_resp["message"]["content"]
        verdict, detail, error = parse_llm_result(content)
    except Exception as e:
        verdict = "FAIL"
        detail = "worker llm call error"
        error = str(e)
        content = ""

    new_hash = make_hash_chain(prev_hash, task_id, final_prompt)

    # 從 task dict 拎分類元數據，存入快照
    catalog_id = task.get("catalog_id", 0) if task else 0
    subcatalog_id = task.get("subcatalog_id", 0) if task else 0
    catalog_name = task.get("catalog_name", "") if task else ""
    subcatalog_name = task.get("subcatalog_name", "") if task else ""

    check_payload = {
        "task_id": task_id,
        "session_id": session_id,
        "hash_chain": new_hash,
        "detail": detail,
        "verdict": verdict,
        "error": error,
        "catalog_id": catalog_id,
        "subcatalog_id": subcatalog_id,
        "catalog_name": catalog_name,
        "subcatalog_name": subcatalog_name,
        "prompt_setting_id": prompt_setting_id,
        "prompt_setting_key": prompt_setting_key,
    }
    # 寫入check表
    save_resp = requests.post(API_CHECK_URL, json=check_payload, timeout=10)
    save_resp.raise_for_status()
    saved_check = save_resp.json()
    return saved_check

def parse_llm_result(text: str) -> tuple[str, str, str]:
    """
    從Qwen輸出提取 verdict / detail / error
    規範輸出範例：
    VERDICT: PASS
    DETAIL: xxx
    ERROR: none
    """
    verdict = "INCOMPLETE"
    detail = text
    error = None
    lines = text.splitlines()
    for line in lines:
        line = line.strip()
        if line.startswith("VERDICT:"):
            verdict = line.replace("VERDICT:", "").strip()
        elif line.startswith("DETAIL:"):
            detail = line.replace("DETAIL:", "").strip()
        elif line.startswith("ERROR:"):
            val = line.replace("ERROR:", "").strip()
            error = None if val.lower() == "none" else val
    return verdict, detail, error
