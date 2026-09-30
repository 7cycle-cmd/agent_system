# -*- coding: utf-8 -*-
"""
Tool #9 helper: f_send_chat.py — send the chat via the Ctrl+Shift+Enter
hotkey (fired BY PYTHON, not AHK) and record the send-out time.

On send it INSERTs a chat_reply_log row (agent.db, the existing table
with SHA256 chat_id) with sent_at = now, session_id = 'pending'.
The row id is written to new_session_chat_row.txt so f_copy_reply.py
can UPDATE the same row later with session_id + elapsed_ms.

Usage:
    python f_send_chat.py --send    # hotkey ctrl+shift+enter + record row
    python f_copy_reply.py --copy-reply   # later: fill session_id/elapsed

Output: "SENT: row=<id>" on success, or "FAIL: reason" (exit 1).
Run with pythonw.exe from AHK (no console window flash).
"""
from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
ROW_ID_FILE = os.path.join(ROOT, "new_session_chat_row.txt")
EST_WAIT_FILE = os.path.join(ROOT, "new_session_est_wait.txt")
TEMPLATE_TEXT_FILE = os.path.join(ROOT, "new_session_template_text.txt")
VISION_LOG = os.path.join(ROOT, "send_chat_log.txt")
# Task ID handoff: the caller (AHK / worker) writes the current task id here so
# the chat_reply_log row can be bound to a task. Env TASK_ID wins if set.
TASK_ID_FILE = os.path.join(ROOT, "current_task_id.txt")


def read_task_id() -> str | None:
    """Current task id from env TASK_ID, else current_task_id.txt, else None.

    WHY a file: AHK cannot easily set env vars for a child process, so the
    hotkey writes the file first. Env wins so a caller can override.
    """
    env = (os.environ.get("TASK_ID") or "").strip()
    if env:
        return env
    try:
        if os.path.isfile(TASK_ID_FILE):
            val = open(TASK_ID_FILE, encoding="utf-8").read().strip()
            return val or None
    except Exception:
        pass
    return None

# Response time depends on input tokens (prefill). Rough local-LLM rates:
# prefill ~ 40ms/input-token, generation ~ 60ms/output-token, + 3s base.
MS_PER_INPUT_TOKEN = 40
MS_PER_OUTPUT_TOKEN = 60
BASE_WAIT_MS = 3000
EST_OUTPUT_TOKENS = 60  # the SESSION_ID header reply is short


def estimate_wait_ms(prompt_text: str) -> int:
    """Estimate total wait (ms) from input token count of the prompt."""
    # ~4 chars per token (English/mixed) — good enough for a wait estimate
    input_tokens = max(1, len(prompt_text) // 4)
    wait = BASE_WAIT_MS + input_tokens * MS_PER_INPUT_TOKEN \
        + EST_OUTPUT_TOKENS * MS_PER_OUTPUT_TOKEN
    vlog(f"estimate_wait: chars={len(prompt_text)} "
         f"~input_tokens={input_tokens} est_wait_ms={wait}")
    return wait


def vlog(msg: str) -> None:
    import datetime
    try:
        with open(VISION_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")
    except Exception:
        pass


def send_chat() -> int:
    """Fire Ctrl+Shift+Enter and record the send-out row. Returns row id."""
    import pyautogui
    import sqlite3
    import datetime

    pyautogui.FAILSAFE = False
    # 1) record send-out time FIRST (the moment we fire the hotkey)
    sent_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    task_id = read_task_id()
    agent_db = os.path.join(ROOT, "agent.db")
    conn = sqlite3.connect(agent_db)
    try:
        cur = conn.execute(
            """
            INSERT INTO chat_reply_log
                (session_id, task_id, model, source, sent_at)
            VALUES ('pending', ?, ?, 'f1_new_session', ?)
            """,
            (task_id, "Qwen: Qwen3.8 27B", sent_at),
        )
        conn.commit()
        row_id = int(cur.lastrowid)
    finally:
        conn.close()
    vlog(f"send_chat: row={row_id} sent_at={sent_at} task_id={task_id or '-'}")
    # 2) estimate the wait from input tokens (response time depends on
    #    prefill length) — AHK uses this before starting the 30s poll
    prompt_text = ""
    if os.path.isfile(TEMPLATE_TEXT_FILE):
        try:
            prompt_text = open(TEMPLATE_TEXT_FILE, encoding="utf-8").read()
        except Exception:
            prompt_text = ""
    est = estimate_wait_ms(prompt_text)
    with open(EST_WAIT_FILE, "w", encoding="utf-8") as f:
        f.write(str(est))
    # 3) fire the send hotkey: Ctrl+Shift+Enter
    pyautogui.hotkey("ctrl", "shift", "enter")
    vlog("send_chat: fired ctrl+shift+enter")
    time.sleep(0.5)
    # 4) hand the row id to f_copy_reply.py (it UPDATEs this row later)
    with open(ROW_ID_FILE, "w", encoding="utf-8") as f:
        f.write(str(row_id))
    return row_id


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: f_send_chat.py --send")
        sys.exit(1)
    cmd = sys.argv[1]
    try:
        if cmd == "--send":
            row_id = send_chat()
            print(f"SENT: row={row_id}")
        else:
            print(f"unknown command: {cmd}")
            sys.exit(1)
    except Exception as e:
        vlog(f"FAIL {cmd}: {e}")
        print(f"FAIL: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
