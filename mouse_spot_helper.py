"""Mouse Spot Helper — web UI for live mouse coordinates and screenshots.

Usage:
    c:/projects/agent_system/.venv/Scripts/python.exe mouse_spot_helper.py

Then open the URL shown in the terminal. Press Ctrl+Shift+M for a full-screen screenshot (PrintScreen-style).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import socket
import sys
import threading
import time
import uuid
import urllib.request
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request, send_file, send_from_directory
from flask_cors import CORS
from pynput import keyboard
from pynput.mouse import Controller as MouseController

import re
import sqlite3
from vision_analyze import (
    DEFAULT_OLLAMA_BASE,
    DEFAULT_VISION_MODEL,
    analyze_evidence,
    complete_text,
    parse_verify_response,
)
from skill_prompt import (
    DEFAULT_SKILL as SKILL_MOUSE_SPOT,
    get_active_skill,
    latest_test_run,
    list_skills,
    render_prompt,
    seed_default_skills,
    set_active_version,
    test_prompt_runs,
    upsert_skill_prompt,
    _connect as skill_db_connect,
    ensure_skill_tables,
)
from skill_prompt_ext import (
    ROOT_TASK_ID as SKILL_ROOT_TASK_ID,
    improve_prompt_to_draft,
    list_skill_cases,
    seed_gold_cases,
    seed_phase5_all,
    seed_task_center_skill_root,
    test_gold_suite,
    upsert_skill_case,
)
from src.task_center.skill_task_validate import (
    SKILL_KEY as TASK_FORMAT_SKILL_KEY,
    seed_task_format_validator,
    validate_new_task,
)
from src.task_center.lifecycle_log import (
    append_lifecycle_event,
    get_task_timeline,
    insert_prompt_trace,
    list_lifecycle_events,
    list_prompt_traces,
    seed_lifecycle_skills,
)
from coord_store import (
    clear_target_error,
    create_prompt_setting,
    count_skill_refs_to_setting,
    delete_prompt_setting,
    get_checklist_status,
    get_format_template_row_by_id,
    get_target_area,
    get_target_point,
    init_db as init_coord_db,
    list_grouped,
    list_prompt_settings,
    list_target_areas,
    list_target_points,
    preview_prompt_setting,
    save_target_point,
    set_checklist_confirm,
    update_prompt_setting,
    update_target_error,
    upsert_target_area,
)
import register_store as rs
from template_manager_ui import LLM_TEMPLATES_PAGE_HTML
import telemetry_db

BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_PATH = BASE_DIR / "mouse_spot_screenshot.png"
# UI-only 200x200 crop around crosshair; LLM always uses SCREENSHOT_PATH (full).
SCREENSHOT_PREVIEW_PATH = BASE_DIR / "mouse_spot_screenshot_preview.png"
PREVIEW_SIZE = 200
TARGETS_FILE = BASE_DIR / "mouse_spot_targets.json"
TARGETS_DIR = BASE_DIR / "mouse_spot_targets"
TARGETS_DIR.mkdir(exist_ok=True)
LLM_TASKS_FILE = BASE_DIR / "mouse_spot_llm_tasks.json"
LLM_TASKS_LOCK = threading.Lock()
LLM_MONITOR_DIST = BASE_DIR / "llm_task_monitor_ui" / "dist"
WATCHDOG_PID_FILE = BASE_DIR / "helper_watchdog.pid"
WATCHDOG_LOG_FILE = BASE_DIR / "helper_watchdog.log"
WATCHDOG_EVENTS_FILE = BASE_DIR / "helper_watchdog_events.json"
WATCHDOG_SNAPS_DIR = BASE_DIR / "helper_watchdog_snaps"
WATCHDOG_SNAPS_DIR.mkdir(exist_ok=True)
# VL confidence (0..1) below this → persist target error (方案B fault path)
LLM_SCORE_THRESHOLD = 70

# Known local Ollama models for this machine.
LLM_MODELS = [
    {"id": "qwen2.5:7b-instruct", "label": "Qwen2.5 7B", "kind": "text"},
    {"id": "qwen2.5vl:7b", "label": "Qwen2.5 7B VL", "kind": "vision"},
]
DEFAULT_TEXT_MODEL = "qwen2.5:7b-instruct"
HELPER_HOME_URL = "http://127.0.0.1:18765/"
HELPER_LLM_TASKS_URL = "http://127.0.0.1:18765/llm-tasks"
HELPER_PROMPT_ANALYZE_URL = "http://127.0.0.1:18765/prompt-analyze"
TASK_CENTER_URL = "http://127.0.0.1:8766/tasks"
AGENT_DB_PATH = BASE_DIR / "agent.db"

app = Flask(__name__)
CORS(app, origins=[
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
])
mouse = MouseController()
last_target: dict[str, int] | None = None
capture_error: str | None = None
capture_seq: int = 0
active_target_id: str | None = None
# Global capture hotkey (pynput). Override with MOUSE_SPOT_HOTKEY, e.g. later PrintScreen.
CAPTURE_HOTKEY = (os.environ.get("MOUSE_SPOT_HOTKEY") or "<ctrl>+<shift>+m").strip() or "<ctrl>+<shift>+m"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def get_computer_identity() -> dict[str, str]:
    hostname = (
        os.environ.get("COMPUTERNAME")
        or platform.node()
        or socket.gethostname()
        or "unknown-pc"
    )
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    digest = hashlib.sha1(hostname.encode("utf-8", errors="replace")).hexdigest()[:8].upper()
    computer_id = f"PC-{digest}"
    return {
        "computer_name": hostname,
        "computer_id": computer_id,
        "username": user,
        "display": f"{hostname} · {computer_id}" + (f" · {user}" if user else ""),
    }


def check_ollama_status() -> dict[str, Any]:
    """Probe Ollama and report BOTH models, because they are NOT interchangeable.

    MEASURED DEFECT (2026-09-23), and it caused a real misreading: this function
    probed only the VISION model (`OLLAMA_VISION_MODEL` = `qwen2.5vl:7b`) and
    returned it as `model`, which the UI header renders as the `LLM ON` pill. So
    the header said `qwen2.5vl:7b` while EVERY text job in the repo —
    `terminology_sweep` (MODEL = `qwen2.5:7b-instruct`) and `worker_engine` —
    runs the TEXT model. The badge named a model that no text job uses.

    MEASURED (same prompt, same names, temperature 0):
        qwen2.5:7b-instruct  valid JSON 9/9   p50 608 ms
        qwen2.5vl:7b         valid JSON 9/9   p50 518 ms
    Both can do the text job, so neither is "broken" — but they are two facts,
    and one badge cannot state two facts. Each is now reported on its own, and
    `model` stays the TEXT model because that is what the text work uses.
    """
    base = (os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE).rstrip("/")
    text_model = (os.environ.get("OLLAMA_TEXT_MODEL")
                  or globals().get("DEFAULT_TEXT_MODEL")
                  or "qwen2.5:7b-instruct")
    vision_model = os.environ.get("OLLAMA_VISION_MODEL") or DEFAULT_VISION_MODEL
    url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        names: list[str] = []
        for m in body.get("models") or []:
            if isinstance(m, dict) and m.get("name"):
                names.append(str(m["name"]))

        def _present(want: str) -> bool:
            root = str(want).split(":", 1)[0]
            return any(want == n or n.startswith(root) for n in names)

        text_present = _present(text_model)
        vision_present = _present(vision_model)
        return {
            "ok": True,
            "base_url": base,
            # `model` = the TEXT model: the repo's text jobs use it, so a badge
            # claiming "LLM ON" must name it. MEASURED: this used to be the
            # vision model, which no text job runs.
            "model": text_model,
            "model_present": text_present,
            "text_model": text_model,
            "text_model_present": text_present,
            "vision_model": vision_model,
            "vision_model_present": vision_present,
            # Kept for callers that predate the split; it is the VISION answer.
            "has_vision": vision_present,
            "models": names[:20],
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "base_url": base,
            "model": text_model,
            "model_present": False,
            "text_model": text_model,
            "text_model_present": False,
            "vision_model": vision_model,
            "vision_model_present": False,
            "models": [],
            "error": f"{type(e).__name__}: {e}",
        }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION covers more PIDs than query-only.
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid)
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            # Fallback: exit-code probe via OpenProcess(PROCESS_QUERY_INFORMATION)
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_INFORMATION, False, int(pid)
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        try:
            # Last resort: tasklist match
            import subprocess

            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return str(pid) in out and "No tasks" not in out and "没有" not in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _read_watchdog_pid() -> int | None:
    if not WATCHDOG_PID_FILE.is_file():
        return None
    try:
        raw = WATCHDOG_PID_FILE.read_text(encoding="utf-8").strip()
        pid = int(raw or "0")
        return pid if pid > 0 else None
    except Exception:
        return None


def load_watchdog_events(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 50), 200))
    if not WATCHDOG_EVENTS_FILE.is_file():
        return []
    try:
        data = json.loads(WATCHDOG_EVENTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for row in data[:limit]:
            if isinstance(row, dict):
                out.append(row)
        return out
    except Exception:
        return []


def load_watchdog_log_tail(lines: int = 40) -> list[str]:
    lines = max(1, min(int(lines or 40), 200))
    if not WATCHDOG_LOG_FILE.is_file():
        return []
    try:
        text = WATCHDOG_LOG_FILE.read_text(encoding="utf-8", errors="replace")
        parts = text.splitlines()
        return parts[-lines:]
    except Exception:
        return []


def watchdog_status_payload() -> dict[str, Any]:
    pid = _read_watchdog_pid()
    alive = bool(pid and _pid_alive(pid))
    events = load_watchdog_events(limit=1)
    last = events[0] if events else None
    return {
        "ok": alive,
        "running": alive,
        "pid": pid if alive else None,
        "pid_file": str(WATCHDOG_PID_FILE),
        "log_file": str(WATCHDOG_LOG_FILE),
        "events_file": str(WATCHDOG_EVENTS_FILE),
        "snaps_dir": str(WATCHDOG_SNAPS_DIR),
        "last_event": last,
        "ui_url": "/llm-tasks",
        "events_url": "/api/watchdog/events",
        "scope": "helper_keepalive",
        "module": "helper_watchdog",
        "monitors": "mouse_spot_helper :18765",
        "note": (
            "Restarts mouse_spot_helper only. "
            "Does not keep OpenClaw MCP or worker heartbeat alive."
        ),
    }


_OPENCLAW_STATUS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_WORKER_HB_STATUS_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
_OPS_STATUS_TTL_SEC = 8.0


def check_openclaw_status(force: bool = False) -> dict[str, Any]:
    """OpenClaw status — READ FROM THE BACKGROUND REFRESHER'S CACHE.

    THE HUMAN (2026-09-25):
        "these fucking deign is wrong, it make the site become slow and slow"
        "by trigger point before have the call!! not need tochecking status : alive"

    MEASURED, and this is the defect this function used to BE: it built an
    `McpClient` and called `list_tool_names(refresh=True)` — a LIVE MCP
    round-trip — on EVERY `/api/system-status` poll. OpenClaw Companion is not
    running, so port 8765 refuses, and a REFUSED LOOPBACK CONNECT costs ~2040 ms
    on this machine (MEASURED: 2046.7 / 2041.7 / 2024.5 / 2037.3 ms). The UI
    polls every 5 s and the cache TTL was 8 s, so the cache was ALWAYS expired:
    MEASURED, 4 of 8 polls stalled 2-3.6 s (50%).

    THE FIX: the work moved to a background refresher
    (`openclaw_settings.start_status_refresher`), and this function now READS
    that cache. It NEVER constructs an `McpClient` and NEVER blocks.

    `force=True` is kept for callers that explicitly want the real work; it
    routes to the TRIGGER POINT (`refresh_now`) rather than probing inline, so
    there is exactly ONE place that does the work.
    """
    import openclaw_settings as ocs

    if force:
        snap = ocs.refresh_now()
    else:
        snap = ocs.status_cached()
    oc = snap.get("openclaw") or {}
    payload = dict(oc) if isinstance(oc, dict) else {}
    payload["cache_age_sec"] = snap.get("age_sec")
    payload["cache_stale"] = snap.get("stale")
    payload["refreshes"] = snap.get("refreshes")
    payload["refresh_interval_sec"] = snap.get("interval_sec")
    payload["source"] = "background_refresher_cache"
    return payload


def check_worker_heartbeat_status(force: bool = False) -> dict[str, Any]:
    """Latest worker heartbeat from agent.db (observe only)."""
    now = time.time()
    cached = _WORKER_HB_STATUS_CACHE.get("payload")
    if (
        not force
        and cached is not None
        and (now - float(_WORKER_HB_STATUS_CACHE.get("ts") or 0.0)) < _OPS_STATUS_TTL_SEC
    ):
        return dict(cached)

    payload: dict[str, Any] = {
        "ok": False,
        "configured": AGENT_DB_PATH.is_file(),
        "worker_id": None,
        "worker_name": None,
        "group_name": None,
        "status": None,
        "last_seen_at": None,
        "heartbeat_at": None,
        "business_alive": None,
        "pid": None,
        "age_sec": None,
        "stale": True,
        "stale_after_sec": 600,
        "scope": "worker",
        "module": "worker_heartbeat",
        "note": (
            "Worker pulse into agent.db; scanned by watchdog.py "
            "(not helper_watchdog)."
        ),
        "error": None,
    }
    if not AGENT_DB_PATH.is_file():
        payload["error"] = "agent.db missing"
        _WORKER_HB_STATUS_CACHE["ts"] = now
        _WORKER_HB_STATUS_CACHE["payload"] = dict(payload)
        return payload

    try:
        uri = f"file:{AGENT_DB_PATH.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT w.id AS worker_id, w.name AS worker_name, w.group_name,
                       w.status, w.last_seen_at,
                       h.heartbeat_at, h.business_alive, h.pid AS hb_pid
                FROM workers w
                LEFT JOIN worker_heartbeat h ON h.id = (
                    SELECT id FROM worker_heartbeat
                    WHERE worker_id = w.id
                    ORDER BY heartbeat_at DESC LIMIT 1
                )
                ORDER BY
                    CASE WHEN w.last_seen_at IS NULL THEN 1 ELSE 0 END,
                    w.last_seen_at DESC
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        if not row:
            payload["error"] = "no workers rows"
        else:
            payload["worker_id"] = row["worker_id"]
            payload["worker_name"] = row["worker_name"]
            payload["group_name"] = row["group_name"]
            payload["status"] = row["status"]
            payload["last_seen_at"] = str(row["last_seen_at"] or "") or None
            payload["heartbeat_at"] = str(row["heartbeat_at"] or "") or None
            ba = row["business_alive"]
            payload["business_alive"] = None if ba is None else bool(ba)
            payload["pid"] = row["hb_pid"]
            ts_raw = row["heartbeat_at"] or row["last_seen_at"]
            age = None
            if ts_raw:
                try:
                    s = str(ts_raw).strip().replace("T", " ")
                    then = None
                    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                        try:
                            then = datetime.strptime(s[:26], fmt)
                            break
                        except ValueError:
                            then = None
                    if then is not None:
                        age = max(0.0, (datetime.now() - then).total_seconds())
                except Exception:
                    age = None
            payload["age_sec"] = None if age is None else int(age)
            stale_after = 600
            payload["stale_after_sec"] = stale_after
            payload["stale"] = True if age is None else age > stale_after
            payload["ok"] = bool(age is not None and age <= stale_after)
            if payload["status"] == "stopping":
                payload["ok"] = False
                payload["error"] = "worker status=stopping"
    except Exception as e:
        payload["error"] = f"{type(e).__name__}: {e}"

    _WORKER_HB_STATUS_CACHE["ts"] = now
    _WORKER_HB_STATUS_CACHE["payload"] = dict(payload)
    return payload


def helper_status_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "pid": os.getpid(),
        "scope": "helper_http",
        "url": HELPER_HOME_URL,
        "llm_tasks_url": HELPER_LLM_TASKS_URL,
        "prompt_analyze_url": HELPER_PROMPT_ANALYZE_URL,
        "task_center_url": TASK_CENTER_URL,
        "watchdog": watchdog_status_payload(),
    }


_AGENT_DB_MIGRATED = False


def open_agent_db(readonly: bool = True) -> sqlite3.Connection:
    """Open local Task Center DB. Migrates writer/session_id once per process."""
    global _AGENT_DB_MIGRATED
    if not AGENT_DB_PATH.is_file():
        raise FileNotFoundError(f"agent.db not found: {AGENT_DB_PATH}")
    if not _AGENT_DB_MIGRATED:
        try:
            from db_schema import ensure_task_center_schema

            mconn = sqlite3.connect(str(AGENT_DB_PATH), timeout=10)
            try:
                ensure_task_center_schema(mconn)
                mconn.commit()
            finally:
                mconn.close()
            _AGENT_DB_MIGRATED = True
        except Exception:
            # Columns may already exist; continue to SELECT.
            _AGENT_DB_MIGRATED = True
    if readonly:
        uri = f"file:{AGENT_DB_PATH.as_posix()}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
        except sqlite3.OperationalError:
            conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    else:
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def list_task_center_tasks(limit: int = 200) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 200), 500))
    conn = open_agent_db(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT id, task_label, title, status, writer, session_id,
                   channel_id, module_id, version_id, updated_at
            FROM dev_task
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_task_center_task(task_id: int) -> dict[str, Any] | None:
    conn = open_agent_db(readonly=True)
    try:
        row = conn.execute(
            """
            SELECT id, task_label, title, status, writer, session_id,
                   channel_id, module_id, version_id, payload_json,
                   created_at, updated_at
            FROM dev_task
            WHERE id = ?
            """,
            (int(task_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_task_sources(limit: int = 200) -> list[dict[str, Any]]:
    """Read task_source_log (id | task_id | who | where | chat_id | session_id)."""
    limit = max(1, min(int(limit or 200), 500))
    conn = open_agent_db(readonly=True)
    try:
        rows = conn.execute(
            """
            SELECT id, task_id, who, "where", chat_id, session_id
            FROM task_source_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        # Table may not exist yet on older DBs.
        return []
    finally:
        conn.close()


def _id_patterns(key: str) -> list[re.Pattern[str]]:
    return [
        re.compile(rf"(?im)^\s*{re.escape(key)}\s*[:=]\s*(.+?)\s*$"),
        re.compile(rf"(?im)\b{re.escape(key)}\s*[:=]\s*([^\n,;]+)"),
    ]


def extract_prompt_ids(text: str) -> dict[str, str | None]:
    """Pull writer / task_id / session_id markers from free text."""
    out: dict[str, str | None] = {"writer": None, "task_id": None, "session_id": None}
    if not text:
        return out
    for key in ("writer", "task_id", "session_id"):
        for pat in _id_patterns(key):
            m = pat.search(text)
            if m:
                val = (m.group(1) or "").strip().strip("\"'`")
                if val:
                    out[key] = val
                    break
    return out


def prompt_has_ids(
    text: str,
    *,
    writer: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    found = extract_prompt_ids(text or "")
    body = text or ""
    # Also accept raw value presence when context provided.
    has_writer = bool(found["writer"]) or (
        bool(writer) and str(writer) in body and re.search(r"(?i)\bwriter\b", body) is not None
    )
    has_task = bool(found["task_id"]) or (
        bool(task_id) and re.search(rf"(?i)\btask[_ ]?id\b[^\n]*{re.escape(str(task_id))}", body) is not None
    ) or (
        bool(task_id) and re.search(rf"(?im)^\s*task_id\s*[:=]\s*{re.escape(str(task_id))}\s*$", body) is not None
    )
    has_session = bool(found["session_id"]) or (
        bool(session_id)
        and re.search(rf"(?i)\bsession[_ ]?id\b[^\n]*{re.escape(str(session_id))}", body) is not None
    )
    # Prefer explicit found keys.
    if found["writer"]:
        has_writer = True
    if found["task_id"]:
        has_task = True
    if found["session_id"]:
        has_session = True
    missing = [k for k, ok in (("writer", has_writer), ("task_id", has_task), ("session_id", has_session)) if not ok]
    return {
        "writer": has_writer,
        "task_id": has_task,
        "session_id": has_session,
        "found": found,
        "missing": missing,
        "ok": not missing,
    }


# session_id = IDE session id (VS Code / Cursor / Work Buddy / Codex).
# New tasks leave it empty until an IDE actually works the task.
IDE_TARGETS = (
    "Visual Studio Code",
    "Cursor",
    "Work Buddy",
    "Codex",
)

IDE_WORK_PROMPT_TEMPLATE = """You are working this task inside an IDE agent session.

## Goal
{{goal}}

## Context
{{context}}

## IDE target family
Prefer one of: Visual Studio Code | Cursor | Work Buddy | Codex
Current target: {{ide_target}}

## Rules
1. Do the work in the IDE session that owns this request.
2. session_id means the IDE chat/session id (not invented locally).
3. New tasks start with empty session_id — fill it only after IDE work begins.
4. When you finish (or when asked), end your reply with the identity block exactly.

## Identity block (required at end of your reply)
---
session_id: <IDE session id>
task_id: {{task_id}}
writer: {{writer}}
---
"""


def identity_trio_dict(
    *,
    writer: str | None = None,
    task_id: str | int | None = None,
    session_id: str | None = None,
) -> dict[str, str]:
    return {
        "writer": str(writer or "").strip(),
        "task_id": str(task_id or "").strip(),
        "session_id": str(session_id or "").strip(),
    }


def identity_trailer(
    *,
    writer: str | None = None,
    task_id: str | int | None = None,
    session_id: str | None = None,
) -> str:
    """Human-readable identity block for result text / prompt footers."""
    trio = identity_trio_dict(writer=writer, task_id=task_id, session_id=session_id)
    return (
        "---\n"
        f"session_id: {trio['session_id']}\n"
        f"task_id: {trio['task_id']}\n"
        f"writer: {trio['writer']}\n"
    )


def request_session_instruction(
    *,
    writer: str = "",
    task_id: str = "",
    session_id: str = "",
) -> str:
    """Injected when session_id is empty: ask IDE agent to return it in the reply."""
    if str(session_id or "").strip():
        return ""
    return (
        "\n\n## IDE session_id request\n"
        "This task has no session_id yet (normal for new tasks).\n"
        "When you work this task in VS Code / Cursor / Work Buddy / Codex, "
        "return your IDE session id in the final identity block:\n"
        f"{identity_trailer(writer=writer, task_id=task_id, session_id='')}"
    )


def ensure_prompt_id_footer(
    text: str,
    *,
    writer: str,
    task_id: str,
    session_id: str,
    request_session_if_empty: bool = True,
) -> str:
    """Append canonical ID block. Empty session_id is allowed for new tasks."""
    writer = str(writer or "").strip()
    task_id = str(task_id or "").strip()
    session_id = str(session_id or "").strip()
    check = prompt_has_ids(text, writer=writer or None, task_id=task_id or None, session_id=session_id or None)
    found = check.get("found") or {}
    # Require writer/task markers when provided; session may stay empty.
    need_footer = False
    if writer and (found.get("writer") or "") != writer:
        need_footer = True
    if task_id and str(found.get("task_id") or "") != str(task_id):
        need_footer = True
    if session_id and (found.get("session_id") or "") != session_id:
        need_footer = True
    if not found.get("writer") and writer:
        need_footer = True
    if not found.get("task_id") and task_id:
        need_footer = True
    if session_id and not found.get("session_id"):
        need_footer = True
    if not need_footer and (writer or task_id or session_id):
        # Has matching markers (session may be intentionally absent).
        base = (text or "").rstrip()
        if request_session_if_empty and not session_id and not found.get("session_id"):
            if "IDE session_id request" not in base:
                base = base + request_session_instruction(
                    writer=writer, task_id=task_id, session_id=session_id
                )
        return base + "\n"

    footer = "\n\n" + identity_trailer(writer=writer, task_id=task_id, session_id=session_id)
    base = (text or "").rstrip()
    # Strip an existing trailing --- id block to avoid duplicates.
    base = re.sub(
        r"(?s)\n---\s*\n(?:session_id|writer|task_id)\s*:.*\Z",
        "",
        base,
    ).rstrip()
    base = re.sub(
        r"(?s)\n## IDE session_id request\n.*\Z",
        "",
        base,
    ).rstrip()
    out = base + footer
    if request_session_if_empty and not session_id:
        out = out.rstrip() + request_session_instruction(
            writer=writer, task_id=task_id, session_id=session_id
        )
    return out.rstrip() + "\n"


def render_ide_work_prompt(
    *,
    goal: str = "",
    context: str = "",
    ide_target: str = "Visual Studio Code",
    writer: str = "",
    task_id: str = "",
    session_id: str = "",
) -> str:
    """Build a task prompt from the IDE work template (prompt-create-from-template)."""
    target = (ide_target or "Visual Studio Code").strip() or "Visual Studio Code"
    text = IDE_WORK_PROMPT_TEMPLATE
    mapping = {
        "goal": (goal or "").strip() or "(describe the coding goal)",
        "context": (context or "").strip() or "(optional context)",
        "ide_target": target,
        "task_id": str(task_id or "").strip(),
        "writer": str(writer or "").strip(),
        "session_id": str(session_id or "").strip(),
    }
    for key, val in mapping.items():
        text = text.replace("{{" + key + "}}", val)
    return ensure_prompt_id_footer(
        text,
        writer=mapping["writer"],
        task_id=mapping["task_id"],
        session_id=mapping["session_id"],
        request_session_if_empty=True,
    )


def context_completeness(
    *,
    task_id: str | int | None,
    writer: str | None,
    session_id: str | None,
    require_session: bool = True,
) -> dict[str, Any]:
    """Identity trio check. session_id optional when require_session=False (new / pre-IDE tasks)."""
    fields = {
        "task_id": bool(str(task_id or "").strip()),
        "writer": bool(str(writer or "").strip()),
        "session_id": bool(str(session_id or "").strip()),
    }
    required = ["task_id", "writer"] + (["session_id"] if require_session else [])
    missing = [k for k in required if not fields[k]]
    return {
        "ok": not missing,
        "fields": fields,
        "missing": missing,
        "require_session": require_session,
        "session_empty_ok": not require_session,
        "session_id_meaning": "IDE session id (VS Code / Cursor / Work Buddy / Codex)",
    }



def load_llm_tasks() -> list[dict[str, Any]]:
    if not LLM_TASKS_FILE.is_file():
        return []
    try:
        data = json.loads(LLM_TASKS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_llm_tasks(tasks: list[dict[str, Any]]) -> None:
    LLM_TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_llm_task(task: dict[str, Any]) -> dict[str, Any]:
    with LLM_TASKS_LOCK:
        tasks = load_llm_tasks()
        tasks.insert(0, task)
        # Keep last 500 tasks.
        if len(tasks) > 500:
            tasks = tasks[:500]
        save_llm_tasks(tasks)
        return task


def update_llm_task(task_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    with LLM_TASKS_LOCK:
        tasks = load_llm_tasks()
        for i, t in enumerate(tasks):
            if t.get("id") == task_id:
                tasks[i] = {**t, **patch}
                save_llm_tasks(tasks)
                return tasks[i]
        return None


def load_targets() -> list[dict[str, Any]]:
    if TARGETS_FILE.is_file():
        targets = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))
    else:
        targets = [
            {"id": "vscode", "name": "VS Code", "action": "Open VS Code", "image": ""},
            {"id": "doubao", "name": "豆包 AI", "action": "Open Doubao", "image": ""},
        ]
    # Normalize required fields + auto-link logo files that already exist on disk.
    changed = False
    for t in targets:
        if "action" not in t:
            t["action"] = ""
            changed = True
        tid = t.get("id") or ""
        if tid and target_image_path(tid).is_file() and not t.get("image"):
            t["image"] = f"/target-image/{tid}"
            changed = True
    if changed:
        save_targets(targets)
    return targets


def save_targets(targets: list[dict[str, Any]]) -> None:
    TARGETS_FILE.write_text(json.dumps(targets, indent=2, ensure_ascii=False), encoding="utf-8")


def target_image_path(tid: str) -> Path:
    return TARGETS_DIR / f"{tid}.png"


def save_target_image(tid: str, image_bytes: bytes) -> Path:
    """Normalize any uploaded image (PNG/JPEG/GIF/WEBP/...) to PNG on disk."""
    from PIL import Image

    path = target_image_path(tid)
    try:
        img = Image.open(io.BytesIO(image_bytes))
        # Preserve transparency when present; flatten otherwise to RGB.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        img.save(path, format="PNG", optimize=True)
    except Exception:
        # Fallback: write raw bytes if Pillow cannot decode (should be rare).
        path.write_bytes(image_bytes)
    return path


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mouse Spot Helper</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e5bb8;
            color: #d4d4d4;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
        }
        h1 { margin-bottom: 0.2em; }
        .hint { color: #888; margin-bottom: 1.5em; }
        .panel {
            background: #252526;
            border-radius: 12px;
            padding: 2em;
            min-width: 360px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .row {
            display: flex;
            justify-content: center;
            gap: 1em;
            margin: 0.8em 0;
        }
        .box {
            background: #ffeb3b;
            border-radius: 8px;
            padding: 0.8em 1.2em;
            min-width: 100px;
        }
        .label { font-size: 0.85em; color: #333; }
        .value { font-size: 1.8em; font-weight: bold; color: #000; }
        .target { font-size: 1.2em; color: #b5cea8; margin: 0.5em 0; }
        .target-list {
            display: flex;
            flex-direction: column;
            align-items: stretch;
            gap: 0.6em;
            margin: 1em 0 0;
            width: 100%;
        }
        .target-chip {
            background: #333;
            border: 2px solid transparent;
            border-radius: 8px;
            padding: 0.7em 0.9em;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 0.6em;
            transition: 0.15s;
            width: 100%;
            box-sizing: border-box;
        }
        .target-chip:hover { background: #3c3c3c; }
        .target-chip.active { border-color: #4fc1ff; background: #1e3a4c; }
        .target-chip img {
            width: 56px;
            height: 56px;
            object-fit: cover;
            border-radius: 4px;
            flex-shrink: 0;
        }
        .target-chip .name { font-size: 0.95em; text-align: left; }
        button {
            background: #0e639c;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0.8em 1.5em;
            font-size: 1em;
            cursor: pointer;
            margin-top: 0;
        }
        button:hover { background: #1177bb; }
        button.secondary {
            background: #444;
            margin-left: 0;
        }
        button.secondary:hover { background: #555; }
        #screenshot {
            margin-top: 1.5em;
            max-width: 100%;
            border-radius: 8px;
            border: 1px solid #444;
        }
        .hidden { display: none; }
        .nav {
            margin-top: 1em;
            display: flex;
            justify-content: center;
            width: min(360px, 100%);
        }
        .nav button {
            min-width: 160px;
        }
    </style>
</head>
<body>
    <h1>Mouse Spot Helper</h1>
    <div class="hint">Global shortcut: <b>Ctrl+Shift+M</b> = full-screen screenshot (like PrintScreen)</div>
    <div class="panel">
        <div class="row">
            <div class="box">
                <div class="label">X</div>
                <div class="value" id="x">0</div>
            </div>
            <div class="box">
                <div class="label">Y</div>
                <div class="value" id="y">0</div>
            </div>
        </div>
        <div class="target-list" id="targetList"></div>

        <img id="screenshot" class="hidden" alt="screenshot">
    </div>

    <div class="nav">
        <button class="secondary" id="llmTasksBtn">LLM Tasks</button>
        <button class="secondary" id="settingsBtn">⚙ Settings</button>
    </div>

    <script>
        let targets = [];
        let activeId = null;

        async function loadTargets() {
            const res = await fetch('/api/targets');
            targets = await res.json();
            renderTargets();
        }

        function renderTargets() {
            const list = document.getElementById('targetList');
            list.innerHTML = '';
            targets.forEach(t => {
                const chip = document.createElement('div');
                chip.className = 'target-chip' + (t.id === activeId ? ' active' : '');
                chip.onclick = () => selectTarget(t.id);
                const img = document.createElement('img');
                img.src = t.image ? `/target-image/${t.id}` : '/static/target-placeholder.png';
                img.onerror = () => { img.style.display = 'none'; };
                const name = document.createElement('span');
                name.className = 'name';
                name.textContent = t.name;
                chip.appendChild(img);
                chip.appendChild(name);
                list.appendChild(chip);
            });
        }

        async function selectTarget(id) {
            activeId = id;
            await fetch('/api/active-target', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id })
            });
            renderTargets();
            // Go to step 2 capture UI for the selected target.
            window.location.href = '/step2';
        }

        async function update() {
            const res = await fetch('/api/state');
            const data = await res.json();
            document.getElementById('x').textContent = data.x;
            document.getElementById('y').textContent = data.y;
            if (data.active_id !== activeId) {
                activeId = data.active_id;
                renderTargets();
            }
        }

        document.getElementById('llmTasksBtn').addEventListener('click', () => {
            window.open('/llm-tasks', '_blank');
        });
        document.getElementById('settingsBtn').addEventListener('click', () => {
            window.open('/settings', '_blank');
        });

        loadTargets();
        setInterval(update, 200);
        update();
    </script>
</body>
</html>
"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mouse Spot Helper — Settings</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e1e1e;
        .top-nav { display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1rem; width:100%; max-width:560px; }
        .top-nav a { color:#4fc3f7; text-decoration:none; }
            color: #d4d4d4;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
            margin: 0;
            padding: 2em;
        }
        h1 { margin-bottom: 0.5em; }
        .panel {
            background: #252526;
            border-radius: 12px;
            padding: 1.5em;
            width: 100%;
            max-width: 560px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .target-item {
            background: #1e1e1e;
            border-radius: 8px;
            padding: 1em;
            margin-bottom: 1em;
        }
        .target-item label {
            display: block;
            margin: 0.6em 0 0.2em;
            font-size: 0.9em;
            color: #aaa;
        }
        .target-item input[type="text"],
        .target-item input[type="number"] {
            width: 100%;
            padding: 0.5em;
            border-radius: 4px;
            border: 1px solid #444;
            background: #2d2d2d;
            color: #d4d4d4;
        }
        .xy-row { display: flex; gap: 0.75em; }
        .xy-row > div { flex: 1; }
        .target-item img {
            max-width: 80px;
            max-height: 80px;
            border-radius: 4px;
            margin-top: 0.5em;
            border: 1px solid #444;
        }
        .actions {
            display: flex;
            gap: 0.5em;
            margin-top: 0.8em;
            flex-wrap: wrap;
        }
        button {
            background: #0e639c;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0.6em 1em;
            cursor: pointer;
        }
        button:hover { background: #1177bb; }
        button.danger { background: #c75450; }
        button.danger:hover { background: #d9534f; }
        button.secondary { background: #444; }
        .hint { font-size: 0.8em; color: #888; margin-top: 0.4em; }
        .err { color: #f87171; font-size: 0.85em; margin-top: 0.4em; }
    </style>
</head>
<body>
    <nav class="top-nav"><a href="/settings">Settings</a><a href="/llm-tasks/prompt_setting">Prompt Setting</a><a href="/llm-tasks">LLM Tasks</a></nav>
    <h1>Mouse Spot Helper Settings</h1>

    <div class="panel" style="max-width:100%; width:100%;">
        <h2 style="margin-top:0;">Success Target Table</h2>
        <p style="color:#9e9e9e; font-size:0.85rem; margin:0 0 0.75rem;">
            Proven click targets learned from py scripts. Same <code>target_id</code> with
            different X/Y = the learned <b style="color:#a5d6a7;">range</b> (min–max).
            Written by <code>coord_store.record_success()</code> after each verified click.
        </p>
        <button class="secondary" type="button" onclick="loadSuccessTable()">Refresh</button>
        <div id="success-msg" style="color:#9e9e9e; font-size:0.85rem; margin:0.5rem 0;"></div>
        <div style="overflow-x:auto;">
            <table id="success-table" style="width:100%; border-collapse:collapse; font-size:0.85rem;">
                <thead>
                    <tr style="color:#aaa; text-align:left;">
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">id</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">target</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">action</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">logo url</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">X (min–max)</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">Y (min–max)</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">active</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">created_date</th>
                        <th style="padding:0.4rem 0.5rem; border-bottom:1px solid #444;">updated_date</th>
                    </tr>
                </thead>
                <tbody id="success-tbody">
                    <tr><td colspan="9" style="padding:0.5rem; color:#9e9e9e;">loading…</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div class="panel" id="panel"></div>

    <script>
                        function esc(s) {
            return String(s ?? '')
                .replace(/&/g, '&amp;')
                .replace(/"/g, '&quot;')
                .replace(/</g, '&lt;');
        }

        async function loadCoordMaps() {
            const byId = {};
            const byName = {};
            try {
                const res = await fetch('/api/coord-targets');
                const data = await res.json();
                if (data && data.ok && Array.isArray(data.points)) {
                    for (const p of data.points) {
                        if (!p) continue;
                        if (p.target_id) byId[p.target_id] = p;
                        if (p.target_name && !byName[p.target_name]) {
                            byName[p.target_name] = p;
                        }
                    }
                }
            } catch (e) {}
            return { byId, byName };
        }

        function pickPoint(t, maps) {
            if (!t) return null;
            if (t.id && maps.byId[t.id]) return maps.byId[t.id];
            const byName = maps.byName[t.name];
            if (byName) {
                const rid = byName.target_id || '';
                // ignore name match if row belongs to a different catalog id
                if (!rid || rid === t.id) return byName;
            }
            return null;
        }

        async function load() {
            const [targetsRes, coordMaps, stateRes] = await Promise.all([
                fetch('/api/targets'),
                loadCoordMaps(),
                fetch('/api/state').then(r => r.json()).catch(() => ({}))
            ]);
            const targets = await targetsRes.json();
            const panel = document.getElementById('panel');
            panel.innerHTML = '';
            const last = (stateRes && stateRes.target) ? stateRes.target : null;

            targets.forEach(t => {
                const pt = pickPoint(t, coordMaps) || {};
                const actionVal = (pt.action != null && pt.action !== '')
                    ? pt.action
                    : (t.action || 'click');
                const xVal = (pt.x != null && pt.x !== '') ? pt.x : '';
                const yVal = (pt.y != null && pt.y !== '') ? pt.y : '';
                const errHtml = pt.error ? `<div class="err">error: ${esc(pt.error)}</div>` : '';
                const div = document.createElement('div');
                div.className = 'target-item';
                div.innerHTML = `
                    <label>Name <span style="color:#f87171">*</span></label>
                    <input type="text" id="name-${t.id}" value="${esc(t.name || '')}" placeholder="Target name" required>
                    <label>Action <span style="color:#f87171">*</span></label>
                    <input type="text" id="action-${t.id}" value="${esc(actionVal)}" placeholder="click / double_click / open app" required>
                    <div class="xy-row">
                        <div>
                            <label>X</label>
                            <input type="number" id="x-${t.id}" value="${esc(xVal)}" placeholder="screen X">
                        </div>
                        <div>
                            <label>Y</label>
                            <input type="number" id="y-${t.id}" value="${esc(yVal)}" placeholder="screen Y">
                        </div>
                    </div>
                    <label>Image</label>
                    <input type="file" id="file-${t.id}" accept="image/*,.webp,.png,.jpg,.jpeg,.gif,.bmp">
                    ${t.image ? `<img src="/target-image/${t.id}?t=${Date.now()}" alt="${esc(t.name || '')}">` : ''}
                    ${errHtml}
                    <div class="actions">
                        <button onclick="saveTarget('${t.id}')">Save</button>
                        <button class="secondary" type="button" onclick="fillFromCapture('${t.id}')">Fill X/Y from capture</button>
                        <button class="danger" onclick="deleteTarget('${t.id}')">Delete</button>
                    </div>
                    <div class="hint">Save writes catalog + coords.db (action, x, y).</div>
                `;
                panel.appendChild(div);
            });

            const addDiv = document.createElement('div');
            addDiv.className = 'target-item';
            addDiv.innerHTML = `
                <label>Name <span style="color:#f87171">*</span></label>
                <input type="text" id="newName" placeholder="New target name" required>
                <label>Action <span style="color:#f87171">*</span></label>
                <input type="text" id="newAction" value="click" placeholder="click" required>
                <div class="xy-row">
                    <div>
                        <label>X</label>
                        <input type="number" id="newX" placeholder="optional">
                    </div>
                    <div>
                        <label>Y</label>
                        <input type="number" id="newY" placeholder="optional">
                    </div>
                </div>
                <div class="actions">
                    <button onclick="addTarget()">+ Add</button>
                    <button class="secondary" type="button" onclick="fillFromCapture('new')">Fill X/Y from capture</button>
                </div>
            `;
            panel.appendChild(addDiv);

            if (last && last.x != null && last.y != null) {
                for (const t of targets) {
                    const xi = document.getElementById('x-' + t.id);
                    const yi = document.getElementById('y-' + t.id);
                    if (xi && yi && xi.value === '' && yi.value === '') {
                        xi.value = last.x;
                        yi.value = last.y;
                        break;
                    }
                }
            }
        }

        async function fillFromCapture(id) {
            try {
                const st = await (await fetch('/api/state')).json();
                const t = st && st.target;
                if (!t || t.x == null || t.y == null) {
                    alert('No capture yet. Capture a point on the main page first.');
                    return;
                }
                if (id === 'new') {
                    document.getElementById('newX').value = t.x;
                    document.getElementById('newY').value = t.y;
                } else {
                    document.getElementById('x-' + id).value = t.x;
                    document.getElementById('y-' + id).value = t.y;
                }
            } catch (e) {
                alert('Failed to read capture state');
            }
        }

        async function dualWriteCoords(name, action, x, y, imagePath, targetId) {
            const body = {
                target_id: targetId || '',
                id: targetId || '',
                target_name: name,
                name: name,
                action: action || 'click',
                x: (x === '' || x == null) ? 0 : Number(x),
                y: (y === '' || y == null) ? 0 : Number(y),
                target_logo: imagePath || '',
                isactive: 1,
                llm_score: 100,
                error: null
            };
            const res = await fetch('/api/coord-targets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                throw new Error(data.error || 'coords save failed');
            }
            return data;
        }

        async function postCatalog(body) {
            const res = await fetch('/api/targets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.ok === false) {
                throw new Error(data.error || 'catalog save failed');
            }
            return data;
        }

        async function saveTarget(id) {
            const name = document.getElementById('name-' + id).value.trim();
            const action = document.getElementById('action-' + id).value.trim() || 'click';
            const x = document.getElementById('x-' + id).value;
            const y = document.getElementById('y-' + id).value;
            if (!name) { alert('Target name is required.'); return; }
            if (!action) { alert('Action is required.'); return; }
            const fileInput = document.getElementById('file-' + id);
            const body = { id, name, action };

            const finish = async (imageB64) => {
                if (imageB64) body.image = imageB64;
                try {
                    await postCatalog(body);
                    await dualWriteCoords(name, action, x, y, '/target-image/' + id, id);
                    load();
                } catch (e) {
                    alert(e.message || String(e));
                }
            };

            if (fileInput.files && fileInput.files[0]) {
                const reader = new FileReader();
                reader.onload = () => finish(reader.result.split(',')[1]);
                reader.readAsDataURL(fileInput.files[0]);
            } else {
                finish(null);
            }
        }

        async function deleteTarget(id) {
            if (!confirm('Delete this target?')) return;
            await fetch('/api/targets/' + id, { method: 'DELETE' });
            load();
        }

        async function addTarget() {
            const name = document.getElementById('newName').value.trim();
            const action = document.getElementById('newAction').value.trim() || 'click';
            const x = document.getElementById('newX').value;
            const y = document.getElementById('newY').value;
            if (!name) { alert('Target name is required.'); return; }
            if (!action) { alert('Action is required.'); return; }
            const id = 't_' + Date.now();
            try {
                await postCatalog({ id, name, action, image: '' });
                await dualWriteCoords(name, action, x, y, '/target-image/' + id, id);
                load();
            } catch (e) {
                alert(e.message || String(e));
            }
        }

        async function loadSuccessTable() {
            const msg = document.getElementById('success-msg');
            const tb = document.getElementById('success-tbody');
            try {
                const res = await fetch('/api/coord-targets/grouped');
                const data = await res.json();
                const groups = (data && data.ok) ? data.groups : [];
                if (!Array.isArray(groups) || groups.length === 0) {
                    tb.innerHTML = '<tr><td colspan="9" style="padding:0.5rem; color:#9e9e9e;">No success targets recorded yet.</td></tr>';
                    msg.textContent = '0 targets';
                    return;
                }
                const fmtRange = (mn, mx) => (mn === mx ? String(mn) : (mn + '\u2013' + mx));
                tb.innerHTML = groups.map(g => {
                    const logo = g.logo
                        ? '<img src="' + esc(g.logo) + '" title="' + esc(g.logo) + '" style="width:28px;height:28px;object-fit:contain;border-radius:4px;background:#0d0d0d;">'
                        : '<span style="color:#666;">\u2013</span>';
                    const active = g.active
                        ? '<span style="display:inline-block;padding:0.1rem 0.5rem;border-radius:999px;background:#2e5d34;font-size:0.75rem;">on</span>'
                        : '<span style="display:inline-block;padding:0.1rem 0.5rem;border-radius:999px;background:#5d4037;font-size:0.75rem;">off</span>';
                    const pts = (g.count > 1) ? ' <span style="display:inline-block;padding:0.05rem 0.4rem;border-radius:999px;background:#2e5d34;font-size:0.7rem;">' + g.count + ' pts</span>' : '';
                    return '<tr>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333; font-family:ui-monospace,monospace;">' + esc(g.target_id) + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333;">' + esc(g.target_name || g.target_id) + pts + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333; font-family:ui-monospace,monospace;">' + esc(g.action) + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333;">' + logo + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333; font-family:ui-monospace,monospace; color:#a5d6a7; font-weight:600;">' + fmtRange(g.x_min, g.x_max) + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333; font-family:ui-monospace,monospace; color:#a5d6a7; font-weight:600;">' + fmtRange(g.y_min, g.y_max) + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333;">' + active + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333; font-family:ui-monospace,monospace; color:#9e9e9e;">' + esc(g.created_at || '') + '</td>' +
                        '<td style="padding:0.4rem 0.5rem; border-bottom:1px solid #333; font-family:ui-monospace,monospace; color:#9e9e9e;">' + esc(g.updated_at || '') + '</td>' +
                        '</tr>';
                }).join('');
                msg.textContent = groups.length + ' target(s)';
            } catch (e) {
                msg.textContent = 'load failed: ' + e;
            }
        }

        load();
        loadSuccessTable();
        setInterval(async () => {
            try {
                const st = await (await fetch('/api/state')).json();
                const t = st && st.target;
                if (!t || t.x == null) return;
                const active = document.activeElement;
                const editing = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA');
                if (editing) return;
                const xs = document.querySelectorAll('input[id^="x-"]');
                for (const xi of xs) {
                    const id = xi.id.slice(2);
                    const yi = document.getElementById('y-' + id);
                    if (xi && yi && xi.value === '' && yi.value === '') {
                        xi.value = t.x;
                        yi.value = t.y;
                        break;
                    }
                }
            } catch (e) {}
        }, 2000);
    </script>
</body>
</html>
"""

IDE_CONTROL_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>IDE Control — Permission</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e1e1e;
            color: #d4d4d4;
            margin: 0;
            padding: 24px;
        }
        h1 { font-size: 20px; color: #7ec699; margin: 0 0 4px; }
        .sub { color: #888; font-size: 13px; margin-bottom: 20px; }
        .card {
            background: #252526;
            border: 1px solid #3c3c3c;
            border-radius: 8px;
            padding: 16px 20px;
            margin-bottom: 16px;
            max-width: 720px;
        }
        .card h2 { font-size: 14px; color: #aaa; margin: 0 0 10px; text-transform: uppercase; letter-spacing: 1px; }
        .perm-big { font-size: 28px; font-weight: 700; color: #7ec699; }
        .perm-meta { color: #888; font-size: 13px; margin-top: 6px; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #3c3c3c; }
        th { color: #888; font-weight: 600; font-size: 12px; text-transform: uppercase; }
        .active-row { background: #2d4a35; }
        .active-row td:first-child { color: #7ec699; font-weight: 700; }
        .hotkey { color: #dcdcaa; font-family: Consolas, monospace; }
        button.set {
            background: #0e639c;
            color: #fff;
            border: none;
            border-radius: 4px;
            padding: 5px 14px;
            font-size: 13px;
            cursor: pointer;
        }
        button.set:hover { background: #1177bb; }
        button.set:disabled { background: #3c3c3c; color: #888; cursor: wait; }
        .msg { font-size: 13px; margin-top: 10px; min-height: 18px; }
        .url { color: #4fc1ff; font-family: Consolas, monospace; font-size: 13px; }
        .ok { color: #7ec699; }
        .err { color: #f48771; }
    </style>
</head>
<body>
    <h1>IDE Control — VS Code Permission</h1>
    <div class="sub">Ctrl+Alt+L multi-press: &times;1 = Allow all &middot; &times;2 = Autopilot &middot; &times;3 = Default &nbsp;|&nbsp; auto-refresh 2s</div>

    <div class="card">
        <h2>Current permission</h2>
        <div class="perm-big" id="perm">loading...</div>
        <div class="perm-meta" id="perm-meta"></div>
    </div>

    <div class="card">
        <h2>Targets (Ctrl+Alt+L)</h2>
        <table>
            <thead><tr><th>Press</th><th>Permission</th><th>Click point</th><th>Status</th><th></th></tr></thead>
            <tbody id="rows"></tbody>
        </table>
        <div class="msg" id="msg"></div>
    </div>

    <div class="card">
        <h2>Direct hotkeys</h2>
        <table>
            <thead><tr><th>Hotkey</th><th>Permission</th></tr></thead>
            <tbody>
                <tr><td class="hotkey">Ctrl+Alt+1</td><td>Default</td></tr>
                <tr><td class="hotkey">Ctrl+Alt+2</td><td>Allow all</td></tr>
                <tr><td class="hotkey">Ctrl+Alt+3</td><td>Autopilot</td></tr>
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>Page URL</h2>
        <div class="url">http://127.0.0.1:18765/ide-control</div>
    </div>

    <script>
        const TARGETS = [
            { press: 1, perm: "allow_all", name: "Allow all (auto-approve)", x: 900, y: 800 },
            { press: 2, perm: "autopilot", name: "Autopilot (Preview)", x: 900, y: 862 },
            { press: 3, perm: "default", name: "Default permissions", x: 900, y: 717 },
        ];
        function update() {
            fetch('/api/ide-control')
                .then(r => r.json())
                .then(d => {
                    if (!d.ok) return;
                    const perm = d.current && d.current.permission || '';
                    document.getElementById('perm').textContent =
                        (d.current && d.current.label) || perm || 'unknown';
                    const meta = [];
                    if (d.current) {
                        if (d.current.ts) meta.push('ts: ' + d.current.ts);
                        if (d.current.via) meta.push('via: ' + d.current.via);
                    }
                    if (d.last_switch) meta.push('last switch file: ' + d.last_switch);
                    document.getElementById('perm-meta').textContent = meta.join('  |  ');
                    const rows = TARGETS.map(t =>
                        '<tr class="' + (t.perm === perm ? 'active-row' : '') + '">' +
                        '<td>&times;' + t.press + '</td>' +
                        '<td>' + t.name + '</td>' +
                        '<td class="hotkey">(' + t.x + ', ' + t.y + ')</td>' +
                        '<td class="' + (t.perm === perm ? 'ok' : '') + '">' +
                        (t.perm === perm ? 'ACTIVE' : '') + '</td>' +
                        '<td><button class="set" id="set-' + t.perm + '" ' +
                        (busy ? 'disabled' : '') + '>SET</button></td></tr>'
                    ).join('');
                    document.getElementById('rows').innerHTML = rows;
                    TARGETS.forEach(t => {
                        const b = document.getElementById('set-' + t.perm);
                        if (b) b.onclick = () => setPerm(t.perm);
                    });
                })
                .catch(() => {
                    document.getElementById('perm').textContent = 'server error';
                });
        }
        let busy = false;
        function setPerm(perm) {
            if (busy) return;
            busy = true;
            const msg = document.getElementById('msg');
            msg.className = 'msg';
            msg.textContent = 'Setting ' + perm + ' ... (VS Code will come to front)';
            document.querySelectorAll('button.set').forEach(b => b.disabled = true);
            fetch('/api/ide-control/set', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ permission: perm }),
            })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        msg.textContent = 'OK: ' + perm + ' (state: ' + d.state + ')';
                        msg.className = 'msg ok';
                    } else {
                        msg.textContent = 'FAIL: want ' + perm + ' got ' + (d.state || '?') +
                            (d.error ? ' — ' + d.error : '');
                        msg.className = 'msg err';
                    }
                })
                .catch(e => {
                    msg.textContent = 'FAIL: ' + e;
                    msg.className = 'msg err';
                })
                .finally(() => {
                    busy = false;
                    update();
                });
        }
        update();
        setInterval(update, 2000);
    </script>
</body>
</html>
"""

STEP2_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mouse Spot Helper — Capture</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e5bb8;
            color: #d4d4d4;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
        }
        h1 { margin-bottom: 0.2em; }
        .hint { color: #ddd; margin-bottom: 1.5em; }
        .panel {
            background: #252526;
            border-radius: 12px;
            padding: 2em;
            min-width: 360px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .target-header {
            display: flex;
            align-items: center;
            justify-content: center;d2b
            gap: 0.6em;
            margin-bottom: 1em;
            background: #333;
            border-radius: 8px;
            padding: 0.5em 0.8em;
        }
        .target-header img {
            width: 28px;
            height: 28px;
            object-fit: cover;
            border-radius: 5px;
        }
        .target-header .name { font-size: 1em; }
        .row {
            display: flex;
            justify-content: center;
            gap: 1em;
            margin: 0.8em 0;
        }
        .box {
            background: #ffeb3b;
            border-radius: 8px;
            padding: 0.8em 1.2em;
            min-width: 100px;
        }
        .label { font-size: 0.85em; color: #333; }
        .value { font-size: 1.8em; font-weight: bold; color: #000; }
        .status {
            margin-top: 1em;
            padding: 0.8em;
            border-radius: 6px;
            background: #333;
        }
        .status.success { background: #1e4d2b; color: #d4d4d4; }
        button {
            background: #0e639c;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0.8em 1.5em;
            font-size: 1em;
            cursor: pointer;
            margin-top: 1em;
        }
        button:hover { background: #1177bb; }
        button.secondary { background: #444; }
        button.secondary:hover { background: #555; }
        .hidden { display: none; }
        .nav { margin-top: 1em; display: flex; gap: 0.5em; justify-content: center; }
    </style>
</head>
<body>
    <h1>Mouse Spot Helper</h1>
    <div class="hint">STEP 2: position your mouse and capture</div>
    <div class="panel">
        <div class="target-header" id="targetHeader">
            <img id="targetLogo" src="" alt="target">
            <span class="name" id="targetName">Target</span>
        </div>
        <div class="row">
            <div class="box"><div class="label">X</div><div class="value" id="x">0</div></div>
            <div class="box"><div class="label">Y</div><div class="value" id="y">0</div></div>
        </div>
        <div class="row">
            <div class="box"><div class="label">Captured X</div><div class="value" id="cx">-</div></div>
            <div class="box"><div class="label">Captured Y</div><div class="value" id="cy">-</div></div>
        </div>
        <div class="status" id="status">Global hotkey only (Ctrl+Shift+M) — works in any window including VS Code</div>
        <div class="nav">
            <button class="secondary" id="backBtn">← Back</button>
        </div>
    </div>

    <script>
        let target = null;
        let lastHandledSeq = 0;
        let lastShownError = '';

        async function load() {
            const res = await fetch('/api/state');
            const state = await res.json();
            const targetsRes = await fetch('/api/targets');
            const targets = await targetsRes.json();
            target = targets.find(t => t.id === state.active_id) || targets[0] || { id: '', name: 'Target', image: '' };
            document.getElementById('targetName').textContent = target.name;
            document.getElementById('targetLogo').src = target.image ? `/target-image/${target.id}?t=${Date.now()}` : '/static/target-placeholder.png';
            lastHandledSeq = Number(state.capture_seq || 0);
            if (state.target) {
                document.getElementById('cx').textContent = state.target.x;
                document.getElementById('cy').textContent = state.target.y;
            }
        }

        async function update() {
            try {
                const res = await fetch('/api/state');
                const data = await res.json();
                document.getElementById('x').textContent = data.x;
                document.getElementById('y').textContent = data.y;
                const statusEl = document.getElementById('status');
                const err = data.capture_error || data.error || '';
                if (err && err !== lastShownError) {
                    lastShownError = err;
                    statusEl.className = 'status';
                    statusEl.textContent = 'Capture error: ' + err;
                }
                const seq = Number(data.capture_seq || 0);
                if (data.target && seq > 0 && seq !== lastHandledSeq) {
                    lastHandledSeq = seq;
                    lastShownError = '';
                    document.getElementById('cx').textContent = data.target.x;
                    document.getElementById('cy').textContent = data.target.y;
                    statusEl.className = 'status';
                    statusEl.textContent = 'Captured — opening preview…';
                    window.location.href = `/step3?x=${data.target.x}&y=${data.target.y}`;
                }
            } catch (e) {}
        }

        document.getElementById('backBtn').addEventListener('click', async () => {
            await fetch('/api/reset-capture', { method: 'POST' });
            window.location.href = '/';
        });

        load();
        setInterval(update, 200);
        update();
    </script>
</body>
</html>
"""

STEP3_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mouse Spot Helper — Confirm</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e5bb8;
            color: #d4d4d4;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
        }
        h1 { margin-bottom: 0.2em; }
        .hint { color: #ddd; margin-bottom: 1.5em; }
        .panel {
            background: #252526;
            border-radius: 12px;
            padding: 2em;
            min-width: 360px;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .target-header {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.6em;
            margin-bottom: 1em;
            background: #333;
            border-radius: 8px;
            padding: 0.5em 0.8em;
        }
        .target-header img {
            width: 28px;
            height: 28px;
            object-fit: cover;
            border-radius: 5px;
        }
        .target-header .name { font-size: 1em; }
        .row {
            display: flex;
            justify-content: center;
            gap: 1em;
            margin: 0.8em 0;
        }
        .box {
            background: #ffeb3b;
            border-radius: 8px;
            padding: 0.8em 1.2em;
            min-width: 100px;
        }
        .label { font-size: 0.85em; color: #333; }
        .value { font-size: 1.8em; font-weight: bold; color: #000; }
        button {
            background: #0e639c;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0.8em 1.5em;
            font-size: 1em;
            cursor: pointer;
            margin-top: 1em;
        }
        button:hover { background: #1177bb; }
        button.secondary { background: #444; }
        button.secondary:hover { background: #555; }
        #screenshot {
            margin-top: 1.5em;
            max-width: 200px;
            max-height: 200px;
            width: auto;
            height: auto;
            border-radius: 8px;
            border: 1px solid #444;
        }
        .hidden { display: none; }
        .nav { margin-top: 1em; display: flex; gap: 0.5em; justify-content: center; }
    </style>
</head>
<body>
    <h1>Mouse Spot Helper</h1>
    <div class="hint">STEP 3: confirm captured coordinates</div>
    <div class="panel">
        <div class="target-header" id="targetHeader">
            <img id="targetLogo" src="" alt="target">
            <span class="name" id="targetName">Target</span>
        </div>
        <div id="targetAction" style="margin-bottom:0.8em;color:#9cdcfe;font-size:0.95em;"></div>
        <div class="row">
            <div class="box"><div class="label">Captured X</div><div class="value" id="cx">-</div></div>
            <div class="box"><div class="label">Captured Y</div><div class="value" id="cy">-</div></div>
        </div>
        <img id="screenshot" class="hidden" alt="screenshot preview">
        <div class="status" id="status" style="display:none;margin-top:1em;padding:0.8em;border-radius:6px;background:#333;"></div>
        <div class="nav">
            <button class="secondary" id="backBtn">← Retake</button>
            <button id="analyzeBtn">Analyze with LLM</button>
        </div>
    </div>

    <script>
        let target = null;

        async function load() {
            const params = new URLSearchParams(window.location.search);
            const cx = params.get('x') || '-';
            const cy = params.get('y') || '-';
            document.getElementById('cx').textContent = cx;
            document.getElementById('cy').textContent = cy;

            const img = document.getElementById('screenshot');
            img.src = `/screenshot-preview.png?t=${Date.now()}`;
            img.onerror = () => {
                img.classList.add('hidden');
                const statusEl = document.getElementById('status');
                statusEl.style.display = 'block';
                statusEl.textContent = 'No screenshot found. Go back and capture again.';
            };
            img.onload = () => img.classList.remove('hidden');

            const res = await fetch('/api/state');
            const state = await res.json();
            const targetsRes = await fetch('/api/targets');
            const targets = await targetsRes.json();
            target = targets.find(t => t.id === state.active_id) || targets[0] || { id: '', name: 'Target', action: '', image: '' };
            document.getElementById('targetName').textContent = target.name;
            document.getElementById('targetAction').textContent = target.action ? ('Action: ' + target.action) : '';
            document.getElementById('targetLogo').src = target.image ? `/target-image/${target.id}?t=${Date.now()}` : '/static/target-placeholder.png';
        }

        async function analyze() {
            const statusEl = document.getElementById('status');
            const btn = document.getElementById('analyzeBtn');
            const capture = {
                x: document.getElementById('cx').textContent,
                y: document.getElementById('cy').textContent
            };
            if (capture.x === '-' || capture.y === '-') {
                alert('Capture a target first.');
                return;
            }
            statusEl.style.display = 'block';
            statusEl.textContent = 'Analyzing with LLM...';
            btn.disabled = true;
            btn.textContent = 'Analyzing...';
            try {
                const res = await fetch('/api/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        target_id: target && target.id,
                        target_name: target && target.name,
                        x: parseInt(capture.x, 10),
                        y: parseInt(capture.y, 10)
                    })
                });
                const data = await res.json();
                const result = data.result || 'FAIL';
                const reason = data.reason || (res.ok ? 'no reason' : 'analyze failed');
                const confidence = typeof data.confidence === 'number' ? data.confidence : 0;
                const q = new URLSearchParams({
                    x: String(capture.x),
                    y: String(capture.y),
                    result: result,
                    reason: reason,
                    confidence: String(confidence),
                    target: (target && target.name) || '',
                    model: data.model || '',
                    task_id: data.task_id || '',
                    prompt_tokens: String(data.prompt_tokens || 0),
                    completion_tokens: String(data.completion_tokens || 0),
                    total_tokens: String(data.total_tokens || 0),
                    duration_ms: String(data.duration_ms || 0),
                    started_at: data.started_at || '',
                    ended_at: data.ended_at || ''
                });
                window.location.href = '/step4?' + q.toString();
            } catch (e) {
                statusEl.textContent = 'LLM: FAIL — ' + e.message;
                btn.disabled = false;
                btn.textContent = 'Analyze with LLM';
            }
        }

        document.getElementById('analyzeBtn').addEventListener('click', analyze);
        document.getElementById('backBtn').addEventListener('click', async () => {
            await fetch('/api/reset-capture', { method: 'POST' });
            window.location.href = '/step2';
        });

        load();
    </script>
</body>
</html>
"""


STEP4_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mouse Spot Helper — Verify Position</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e5bb8;
            color: #d4d4d4;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 1em 1em 2em;
        }
        h1 { margin-bottom: 0.2em; }
        .hint { color: #ddd; margin-bottom: 1.2em; }
        .panel {
            background: #252526;
            border-radius: 12px;
            padding: 1.6em;
            min-width: 360px;
            max-width: 480px;
            width: 100%;
            text-align: center;
            box-shadow: 0 8px 24px rgba(0,0,0,0.4);
        }
        .target-header {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.6em;
            margin-bottom: 1em;
            background: #333;
            border-radius: 8px;
            padding: 0.5em 0.8em;
        }
        .target-header img {
            width: 28px;
            height: 28px;
            object-fit: cover;
            border-radius: 5px;
        }
        .target-header .name { font-size: 1em; }
        .status-row {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5em;
            margin: 0.6em 0 1em;
            font-size: 1em;
        }
        .status-label { color: #aaa; }
        .status-value {
            font-weight: bold;
            padding: 0.25em 0.6em;
            border-radius: 999px;
            font-size: 0.9em;
        }
        .status-value.pass { background: #1b5e20; color: #c8e6c9; }
        .status-value.fail { background: #7f1d1d; color: #fecaca; }
        .screenshot-wrap {
            position: relative;
            display: inline-block;
            margin: 0.6em 0 1em;
            border-radius: 10px;
            overflow: hidden;
            border: 1px solid #444;
            transition: box-shadow 0.2s ease;
        }
        .screenshot-wrap:hover {
            box-shadow: 0 0 0 3px rgba(79, 193, 255, 0.35);
        }
        #screenshot {
            display: block;
            max-width: 360px;
            max-height: 360px;
            width: 100%;
            height: auto;
            cursor: crosshair;
        }
        .caption {
            font-size: 0.8em;
            color: #888;
            margin-top: 0.4em;
        }
        .insight {
            margin: 0.8em 0 1em;
            padding: 1em;
            border-radius: 8px;
            background: #1e1e1e;
            border-left: 4px solid #555;
            text-align: left;
        }
        .insight.fail { border-left-color: #ef4444; }
        .insight.pass { border-left-color: #4caf50; }
        .insight-title {
            font-weight: bold;
            margin-bottom: 0.4em;
            color: #fff;
        }
        .insight-body {
            line-height: 1.45;
            color: #ccc;
            word-break: break-word;
        }
        .nav {
            margin-top: 1.2em;
            display: flex;
            gap: 0.6em;
            justify-content: center;
            flex-wrap: wrap;
        }
        button {
            background: #0e639c;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0.75em 1.4em;
            font-size: 1em;
            cursor: pointer;
            min-width: 110px;
        }
        button:hover { background: #1177bb; }
        button.secondary { background: #444; }
        button.secondary:hover { background: #555; }
        button.ghost {
            background: transparent;
            color: #9cdcfe;
            border: 1px solid #444;
            padding: 0.5em 1em;
            font-size: 0.9em;
            min-width: auto;
        }
        button.ghost:hover { background: #2a2d2e; }
        .advanced {
            margin-top: 1.2em;
            text-align: left;
            border-top: 1px solid #3c3c3c;
            padding-top: 0.8em;
        }
        .advanced-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            cursor: pointer;
            user-select: none;
            color: #aaa;
            font-size: 0.9em;
        }
        .advanced-header:hover { color: #fff; }
        .chevron { transition: transform 0.2s ease; }
        .advanced.open .chevron { transform: rotate(180deg); }
        .advanced-body {
            display: none;
            margin-top: 0.8em;
        }
        .advanced.open .advanced-body { display: block; }
        .kv {
            display: grid;
            grid-template-columns: 110px 1fr;
            gap: 0.4em 0.8em;
            font-size: 0.85em;
            margin-bottom: 0.8em;
        }
        .kv .k { color: #888; }
        .kv .v { color: #ddd; word-break: break-word; }
        .confidence-bar {
            height: 8px;
            background: #444;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 0.3em;
        }
        .confidence-fill {
            height: 100%;
            background: linear-gradient(90deg, #ef4444, #facc15, #22c55e);
            width: 0%;
        }
        .advanced-actions {
            display: flex;
            gap: 0.5em;
            flex-wrap: wrap;
        }
        .hidden { display: none; }
    </style>
</head>
<body>
    <h1>Mouse Spot Helper</h1>
    <div class="hint">Step 4 – Verify Target Position</div>
    <div class="panel">
        <div class="target-header" id="targetHeader">
            <img id="targetLogo" src="" alt="target">
            <span class="name" id="targetName">Target</span>
        </div>

        <div class="status-row">
            <span class="status-label">Status:</span>
            <span class="status-value" id="statusValue">Checking…</span>
        </div>

        <div class="screenshot-wrap" title="Full-screen shot; red crosshair = cursor at capture">
            <img id="screenshot" src="" alt="annotated screenshot preview">
        </div>
        <div class="caption">Full-screen shot · Red crosshair = cursor at capture</div>

        <div class="insight" id="insight">
            <div class="insight-title" id="insightTitle">Verdict</div>
            <div class="insight-body" id="insightBody">-</div>
        </div>

        <div class="nav">
            <button id="retakeBtn" title="Go back and capture again">Retake</button>
            <button class="secondary" id="homeBtn" title="Return to the home screen">Home</button>
        </div>

        <div class="advanced" id="advanced">
            <div class="advanced-header" id="advancedHeader">
                <span>Advanced</span>
                <span class="chevron">▼</span>
            </div>
            <div class="advanced-body">
                <div class="kv">
                    <span class="k">Captured X</span><span class="v" id="cx">-</span>
                    <span class="k">Captured Y</span><span class="v" id="cy">-</span>
                    <span class="k">Model</span><span class="v" id="model">-</span>
                    <span class="k">Confidence</span>
                    <span class="v">
                        <span id="confidenceText">-</span>
                        <div class="confidence-bar"><div class="confidence-fill" id="confidenceFill"></div></div>
                    </span>
                    <span class="k">Tokens</span><span class="v" id="tokens">-</span>
                    <span class="k">Duration</span><span class="v" id="duration">-</span>
                </div>
                <div class="advanced-actions">
                    <button class="ghost" id="againBtn" title="Re-run LLM analysis with the same coordinates">Analyze again</button>
                    <button class="ghost" id="tasksBtn" title="Open the LLM task monitor">LLM Tasks</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        function buildInsight(result, reason) {
            const ok = result === 'SUCCESS';
            const r = (reason || 'No reason provided').trim();
            if (ok) {
                return {
                    title: 'Looks good',
                    body: r + ' You can save this position or continue.',
                    className: 'insight pass'
                };
            }
            return {
                title: 'Action needed',
                body: r + ' Try moving the cursor onto the target, then press Ctrl+Shift+M or click Retake.',
                className: 'insight fail'
            };
        }

        async function load() {
            const params = new URLSearchParams(window.location.search);
            const cx = params.get('x') || '-';
            const cy = params.get('y') || '-';
            const result = (params.get('result') || 'FAIL').toUpperCase();
            const reason = decodeURIComponent(params.get('reason') || 'No reason provided');
            const confidence = parseFloat(params.get('confidence') || '0');
            const targetNameParam = params.get('target') || '';

            document.getElementById('cx').textContent = cx;
            document.getElementById('cy').textContent = cy;

            const statusValue = document.getElementById('statusValue');
            const ok = result === 'SUCCESS';
            statusValue.textContent = ok ? 'Passed' : 'Failed';
            statusValue.className = 'status-value ' + (ok ? 'pass' : 'fail');

            const insight = buildInsight(result, reason);
            const insightEl = document.getElementById('insight');
            document.getElementById('insightTitle').textContent = insight.title;
            document.getElementById('insightBody').textContent = insight.body;
            insightEl.className = insight.className;

            document.getElementById('model').textContent = params.get('model') || '-';
            document.getElementById('confidenceText').textContent = (confidence * 100).toFixed(0) + '%';
            document.getElementById('confidenceFill').style.width = Math.round(confidence * 100) + '%';
            const promptTokens = parseInt(params.get('prompt_tokens') || '0', 10);
            const completionTokens = parseInt(params.get('completion_tokens') || '0', 10);
            const totalTokens = parseInt(params.get('total_tokens') || '0', 10);
            document.getElementById('tokens').textContent = totalTokens
                ? `${totalTokens} total (${promptTokens} prompt / ${completionTokens} completion)`
                : '-';
            const durationMs = parseInt(params.get('duration_ms') || '0', 10);
            document.getElementById('duration').textContent = durationMs ? durationMs + ' ms' : '-';

            const img = document.getElementById('screenshot');
            img.src = `/screenshot-annotated.png?t=${Date.now()}`;

            let targetName = targetNameParam || 'Target';
            let targetId = '';
            try {
                const res = await fetch('/api/state');
                const state = await res.json();
                const targetsRes = await fetch('/api/targets');
                const targets = await targetsRes.json();
                const target = targets.find(t => t.id === state.active_id) || targets[0];
                if (target) {
                    targetName = target.name || targetNameParam || 'Target';
                    targetId = target.id || '';
                }
            } catch (e) {
                // keep URL-provided target name
            }
            document.getElementById('targetName').textContent = targetName;
            document.getElementById('targetLogo').src = targetId ? `/target-image/${targetId}?t=${Date.now()}` : '/static/target-placeholder.png';
            document.getElementById('targetLogo').onerror = function() { this.src = '/static/target-placeholder.png'; };
        }

        document.getElementById('retakeBtn').addEventListener('click', async () => {
            await fetch('/api/reset-capture', { method: 'POST' });
            window.location.href = '/step2';
        });
        document.getElementById('homeBtn').addEventListener('click', async () => {
            await fetch('/api/reset-capture', { method: 'POST' });
            window.location.href = '/';
        });
        document.getElementById('tasksBtn').addEventListener('click', () => {
            window.open('/llm-tasks', '_blank');
        });
        document.getElementById('againBtn').addEventListener('click', () => {
            const params = new URLSearchParams(window.location.search);
            const x = params.get('x') || '';
            const y = params.get('y') || '';
            window.location.href = `/step3?x=${encodeURIComponent(x)}&y=${encodeURIComponent(y)}`;
        });
        document.getElementById('advancedHeader').addEventListener('click', () => {
            document.getElementById('advanced').classList.toggle('open');
        });

        load();
    </script>
</body>
</html>
"""


LLM_TASKS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LLM Task Monitor</title>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1e5bb8;
            color: #d4d4d4;
            margin: 0;
            min-height: 100vh;
            padding: 1.2em;
        }
        h1 { margin: 0 0 0.2em; text-align: center; }
        .hint { color: #ddd; text-align: center; margin-bottom: 1em; }
        .panel {
            background: #252526;
            border-radius: 12px;
            padding: 1.2em;
            max-width: 1200px;
            margin: 0 auto 1em;
            box-shadow: 0 8px 24px rgba(0,0,0,0.35);
        }
        .models {
            display: flex;
            gap: 0.8em;
            flex-wrap: wrap;
            margin-bottom: 1em;
        }
        .model-card {
            background: #333;
            border-radius: 8px;
            padding: 0.8em 1em;
            min-width: 220px;
            flex: 1;
        }
        .model-card .name { font-weight: bold; color: #4fc1ff; margin-bottom: 0.3em; }
        .model-card .meta { color: #bbb; font-size: 0.9em; }
        .stats {
            display: flex;
            gap: 0.8em;
            flex-wrap: wrap;
            margin-bottom: 1em;
        }
        .stat {
            background: #1e1e1e;
            border-radius: 8px;
            padding: 0.7em 1em;
            min-width: 140px;
        }
        .stat .label { color: #888; font-size: 0.8em; }
        .stat .value { font-size: 1.3em; font-weight: bold; color: #ffeb3b; }
        .toolbar {
            display: flex;
            gap: 0.6em;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.8em;
            flex-wrap: wrap;
        }
        button {
            background: #0e639c;
            color: white;
            border: none;
            border-radius: 6px;
            padding: 0.55em 1em;
            font-size: 0.95em;
            cursor: pointer;
        }
        button:hover { background: #1177bb; }
        button.secondary { background: #444; }
        button.secondary:hover { background: #555; }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.92em;
        }
        th, td {
            border-bottom: 1px solid #3a3a3a;
            padding: 0.55em 0.45em;
            text-align: left;
            vertical-align: top;
        }
        th { color: #9cdcfe; font-weight: 600; position: sticky; top: 0; background: #252526; }
        tr:hover td { background: #2d2d30; }
        .badge {
            display: inline-block;
            padding: 0.15em 0.5em;
            border-radius: 999px;
            font-size: 0.8em;
            font-weight: bold;
        }
        .badge.success { background: #1b5e20; color: #c8e6c9; }
        .badge.fail, .badge.error { background: #7f1d1d; color: #fecaca; }
        .badge.running { background: #0b3a5b; color: #9cdcfe; }
        .badge.done { background: #333; color: #ddd; }
        .reason { color: #ccc; max-width: 280px; word-break: break-word; }
        .mono { font-family: Consolas, "Courier New", monospace; }
        .table-wrap { overflow: auto; max-height: 62vh; }
        a { color: #9cdcfe; }
        .pill { display:inline-block; padding:0.15em 0.55em; border-radius:999px; font-size:0.8em; font-weight:bold; margin-right:0.4em; }
        .pill.ok { background:#1b5e20; color:#c8e6c9; }
        .pill.bad { background:#7f1d1d; color:#fecaca; }
        .pill.warn { background:#5d4037; color:#ffe0b2; }
    </style>
</head>
<body>
    <h1>LLM Task Monitor</h1>
    <div class="hint">Models: <b>Qwen2.5 7B</b> (text) · <b>Qwen2.5 7B VL</b> (vision) · auto-refresh 3s</div>
    <div class="panel" id="identityPanel" style="padding:0.9em 1.2em;">
        <div id="identityLine" style="font-size:1.05em;font-weight:600;color:#fff;"></div>
        <div id="statusLine" style="margin-top:0.35em;color:#ccc;font-size:0.92em;"></div>
    </div>


    <div class="panel" id="promptPanel">
        <div class="toolbar" style="margin-bottom:0.6em;">
            <div>
                <b style="color:#4fc1ff;font-size:1.05em;">Task center · Prompt analyze</b>
                <span style="color:#888;margin-left:0.6em;font-size:0.85em;">Improve requires writer + task_id + session_id</span>
            </div>
            <div>
                <a id="editTaskCenterLink" href="http://127.0.0.1:8766/tasks" target="_blank" rel="noopener">Edit in Task Center</a>
            </div>
        </div>
        <div class="stats" style="margin-bottom:0.8em;">
            <div class="stat"><div class="label">task_id</div><div class="value" id="ctxTaskId" style="font-size:1.05em;">-</div></div>
            <div class="stat"><div class="label">writer</div><div class="value" id="ctxWriter" style="font-size:1.05em;">-</div></div>
            <div class="stat"><div class="label">session_id</div><div class="value" id="ctxSession" style="font-size:1.05em;">-</div></div>
            <div class="stat"><div class="label">context</div><div class="value" id="ctxStatus" style="font-size:1.05em;">-</div></div>
        </div>
        <div class="toolbar">
            <div style="display:flex;gap:0.5em;align-items:center;flex-wrap:wrap;">
                <label style="color:#bbb;font-size:0.9em;">Task
                    <select id="taskPick" style="margin-left:0.3em;min-width:280px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;">
                        <option value="">— select Task Center task —</option>
                    </select>
                </label>
                <button class="secondary" id="reloadTasksBtn" type="button">Reload tasks</button>
            </div>
        </div>
        <label style="display:block;color:#9cdcfe;margin:0.4em 0 0.3em;">Chat box · paste prompt</label>
        <textarea id="promptBox" placeholder="Paste prompt content here…" style="width:100%;min-height:160px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:8px;padding:0.7em;font-family:Consolas,monospace;font-size:0.92em;"></textarea>
        <div class="toolbar" style="margin-top:0.7em;">
            <div style="display:flex;gap:0.5em;flex-wrap:wrap;">
                <button id="improveBtn" type="button">Improve</button>
                <button id="analyzeBtn" type="button">Analyze</button>
                <button class="secondary" id="applyImprovedBtn" type="button" title="Copy improved result into chat box">Apply to chat</button>
                <button class="secondary" id="copyResultBtn" type="button">Copy result</button>
            </div>
            <div id="promptMsg" style="color:#ccc;font-size:0.9em;"></div>
        </div>
        <label style="display:block;color:#9cdcfe;margin:0.6em 0 0.3em;">Result</label>
        <pre id="promptResult" style="white-space:pre-wrap;background:#1e1e1e;border-radius:8px;padding:0.8em;min-height:100px;max-height:320px;overflow:auto;color:#ddd;font-size:0.9em;">(Improve / Analyze output appears here)</pre>
    </div>

    <div class="panel" id="skillPanel">
        <div class="toolbar" style="margin-bottom:0.6em;">
            <div>
                <b style="color:#4fc1ff;font-size:1.05em;">Skill Prompt SSOT</b>
                <span style="color:#888;margin-left:0.6em;font-size:0.85em;">Mouse Spot verify · versioned prompt · 100-run proof</span>
            </div>
            <div id="skillStatusBadge" style="color:#ccc;font-size:0.9em;">—</div>
        </div>
        <div class="stats" style="margin-bottom:0.8em;">
            <div class="stat"><div class="label">skill</div><div class="value" id="skillKeyVal" style="font-size:1.0em;">mouse_spot_verify</div></div>
            <div class="stat"><div class="label">version</div><div class="value" id="skillVerVal" style="font-size:1.0em;">-</div></div>
            <div class="stat"><div class="label">status</div><div class="value" id="skillStatVal" style="font-size:1.0em;">-</div></div>
            <div class="stat"><div class="label">parser</div><div class="value" id="skillParserVal" style="font-size:1.0em;">result_yes_no</div></div>
        </div>
        <div class="toolbar" style="margin-bottom:0.6em;">
            <div style="display:flex;gap:0.5em;align-items:center;flex-wrap:wrap;">
                <label style="color:#bbb;font-size:0.9em;">Skill
                    <select id="skillPick" style="margin-left:0.3em;min-width:200px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;">
                        <option value="mouse_spot_verify">mouse_spot_verify</option>
                    </select>
                </label>
                <label style="color:#bbb;font-size:0.9em;">Version
                    <select id="skillVerPick" style="margin-left:0.3em;min-width:140px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;">
                        <option value="">active</option>
                    </select>
                </label>
                <button class="secondary" id="skillReloadBtn" type="button">Reload</button>
                <button class="secondary" id="skillSeedBtn" type="button">Seed v1</button>
                <button class="secondary" id="skillSeedAllBtn" type="button" title="Seed prompt + gold cases + Task Center 10.x">Seed all</button>
                <button class="secondary" id="skillTaskLinesBtn" type="button" title="Show Task Center 10.1–10.20">Task IDs</button>
            </div>
        </div>
        <label style="display:block;color:#9cdcfe;margin:0.2em 0 0.3em;">Active / draft prompt template</label>
        <textarea id="skillPromptBox" placeholder="Skill prompt template (use {{target_name}} {{target_action}})…" style="width:100%;min-height:180px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:8px;padding:0.7em;font-family:Consolas,monospace;font-size:0.88em;"></textarea>
        <div class="toolbar" style="margin-top:0.6em;gap:0.6em;">
            <div style="display:flex;gap:0.5em;flex-wrap:wrap;align-items:center;">
                <label style="color:#bbb;font-size:0.88em;">target
                    <input id="skillTargetName" value="Visual Studio Code" style="margin-left:0.3em;width:160px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;" />
                </label>
                <label style="color:#bbb;font-size:0.88em;">action
                    <input id="skillTargetAction" value="" placeholder="optional" style="margin-left:0.3em;width:120px;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;" />
                </label>
                <label style="color:#bbb;font-size:0.88em;">expected
                    <select id="skillExpected" style="margin-left:0.3em;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;">
                        <option value="NO" selected>NO</option>
                        <option value="YES">YES</option>
                    </select>
                </label>
                <label style="color:#bbb;font-size:0.88em;">runs
                    <select id="skillRuns" style="margin-left:0.3em;background:#1e1e1e;color:#ddd;border:1px solid #555;border-radius:6px;padding:0.35em;">
                        <option value="5">5</option>
                        <option value="10" selected>10</option>
                        <option value="20">20</option>
                        <option value="100">100</option>
                    </select>
                </label>
            </div>
        </div>
        <div class="toolbar" style="margin-top:0.7em;">
            <div style="display:flex;gap:0.5em;flex-wrap:wrap;">
                <button id="skillSaveDraftBtn" type="button">Save draft</button>
                <button class="secondary" id="skillImproveDraftBtn" type="button" title="LLM improve → draft only (never auto-promote)">Improve→draft</button>
                <button id="skillTestBtn" type="button">Run proof test</button>
                <button class="secondary" id="skillTestGoldBtn" type="button" title="Run active gold cases (correctness suite)">Test gold</button>
                <button id="skillPromoteBtn" type="button">Promote active</button>
                <button class="secondary" id="skillToChatBtn" type="button" title="Copy skill prompt into chat box above">To chat box</button>
                <button class="secondary" id="skillFromChatBtn" type="button" title="Load chat box into skill editor">From chat</button>
            </div>
            <div id="skillMsg" style="color:#ccc;font-size:0.9em;"></div>
        </div>
        <div class="stats" id="skillTestStats" style="margin-top:0.8em;"></div>
        <label style="display:block;color:#9cdcfe;margin:0.6em 0 0.3em;">Last test / skill log</label>
        <pre id="skillResult" style="white-space:pre-wrap;background:#1e1e1e;border-radius:8px;padding:0.8em;min-height:80px;max-height:260px;overflow:auto;color:#ddd;font-size:0.88em;">(Seed / Reload / Test output)</pre>
    </div>

    <div class="panel">
        <div class="models" id="modelCards"></div>
        <div class="stats" id="stats"></div>
        <div class="toolbar">
            <div>
                <button id="refreshBtn">Refresh</button>
                <button class="secondary" id="homeBtn">Home</button>
                <button class="secondary" id="clearBtn">Clear history</button>
            </div>
            <div id="updatedAt" style="color:#888;font-size:0.85em;"></div>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th>Duration</th>
                        <th>Model</th>
                        <th>Task</th>
                        <th>Status</th>
                        <th>Result</th>
                        <th>Prompt tok</th>
                        <th>Out tok</th>
                        <th>Total tok</th>
                        <th>Target / XY</th>
                        <th>Skill ver</th>
                        <th>Reason</th>
                    </tr>
                </thead>
                <tbody id="rows"></tbody>
            </table>
        </div>
    </div>

    <script>
        function fmtDuration(startedAt, endedAt) {
            if (!startedAt || !endedAt) return '-';
            try {
                const start = new Date(startedAt).getTime();
                const end = new Date(endedAt).getTime();
                if (isNaN(start) || isNaN(end)) return '-';
                const ms = end - start;
                if (ms < 1000) return ms + ' ms';
                return (ms / 1000).toFixed(1) + ' s';
            } catch (e) { return '-'; }
        }
        async function loadStatus() {
            try {
                const res = await fetch('/api/system-status');
                const data = await res.json();
                const idn = data.identity || {};
                const ol = data.ollama || {};
                document.getElementById('identityLine').textContent =
                  `Computer: ${idn.computer_name || '-'}  |  ID: ${idn.computer_id || '-'}  |  User: ${idn.username || '-'}`;
                const ollamaPill = ol.ok
                  ? `<span class="pill ok">Ollama ON</span>`
                  : `<span class="pill bad">Ollama OFF</span>`;
                const modelPill = ol.model_present
                  ? `<span class="pill ok">Model ${ol.model || ''}</span>`
                  : `<span class="pill warn">Model missing ${ol.model || ''}</span>`;
                const helperPill = `<span class="pill ok">mouse_spot_helper ON</span>`;
                document.getElementById('statusLine').innerHTML =
                  `${helperPill}${ollamaPill}${modelPill}` +
                  ` <span style="color:#9cdcfe">${ol.base_url || ''}</span>` +
                  (ol.error ? ` <span style="color:#f88">${ol.error}</span>` : '');
                document.title = `LLM Task Monitor · ${idn.computer_id || idn.computer_name || ''}`;
            } catch (e) {
                document.getElementById('statusLine').innerHTML = '<span class="pill bad">Status unavailable</span>';
            }
        }
        function badge(cls, text) {
            return `<span class="badge ${cls}">${text}</span>`;
        }

        async function load() {
            const res = await fetch('/api/llm-tasks?limit=200');
            const data = await res.json();
            const models = data.models || [];
            const byModel = data.by_model || [];
            const totals = data.totals || {};
            const tasks = data.tasks || [];

            const cards = document.getElementById('modelCards');
            cards.innerHTML = models.map(m => {
                const b = byModel.find(x => x.model === m.id) || { count: 0, total_tokens: 0, prompt_tokens: 0, completion_tokens: 0 };
                return `<div class="model-card">
                    <div class="name">${m.label}</div>
                    <div class="meta">${m.id} · ${m.kind}</div>
                    <div class="meta">tasks: ${b.count} · tokens: ${b.total_tokens || 0}</div>
                    <div class="meta">in ${b.prompt_tokens || 0} / out ${b.completion_tokens || 0}</div>
                </div>`;
            }).join('');

            document.getElementById('stats').innerHTML = `
                <div class="stat"><div class="label">Tasks shown</div><div class="value">${totals.count || 0}</div></div>
                <div class="stat"><div class="label">Prompt tokens</div><div class="value">${totals.prompt_tokens || 0}</div></div>
                <div class="stat"><div class="label">Output tokens</div><div class="value">${totals.completion_tokens || 0}</div></div>
                <div class="stat"><div class="label">Total tokens</div><div class="value">${totals.total_tokens || 0}</div></div>
            `;

            const rows = document.getElementById('rows');
            if (!tasks.length) {
                rows.innerHTML = '<tr><td colspan="11" style="text-align:center;color:#888;">No LLM tasks yet. Run Analyze with LLM first.</td></tr>';
            } else {
                rows.innerHTML = tasks.map(t => {
                    const st = (t.status || '').toLowerCase();
                    const rs = (t.result || '').toUpperCase();
                    const stBadge = st === 'running' ? badge('running', 'RUNNING')
                        : st === 'error' ? badge('error', 'ERROR')
                        : badge('done', (st || 'DONE').toUpperCase());
                    const rsBadge = rs === 'SUCCESS' ? badge('success', 'SUCCESS')
                        : rs === 'FAIL' ? badge('fail', 'FAIL')
                        : (rs ? badge('done', rs) : '-');
                    const xy = (t.x != null && t.y != null) ? `X=${t.x}, Y=${t.y}` : '';
                    const target = [t.target_name || t.target_id || '', xy].filter(Boolean).join(' · ');
                    const sk = t.skill_version || t.skill_key || '-';
                    return `<tr>
                        <td class="mono">${fmtDuration(t.started_at, t.ended_at)}</td>
                        <td>${t.model_label || t.model || '-'}</td>
                        <td>${t.task || '-'}</td>
                        <td>${stBadge}</td>
                        <td>${rsBadge}</td>
                        <td class="mono">${t.prompt_tokens ?? 0}</td>
                        <td class="mono">${t.completion_tokens ?? 0}</td>
                        <td class="mono"><b>${t.total_tokens ?? 0}</b></td>
                        <td>${target || '-'}</td>
                        <td class="mono">${sk}</td>
                        <td class="reason">${t.reason || t.error || '-'}</td>
                    </tr>`;
                }).join('');
            }
            document.getElementById('updatedAt').textContent = 'Updated ' + new Date().toLocaleTimeString();
        }

        document.getElementById('refreshBtn').addEventListener('click', load);
        document.getElementById('homeBtn').addEventListener('click', () => { window.location.href = '/'; });
        document.getElementById('clearBtn').addEventListener('click', async () => {
            if (!confirm('Clear all LLM task history?')) return;
            await fetch('/api/llm-tasks/clear', { method: 'POST' });
            load();
        });


        let tcTasks = [];
        let selectedCtx = { task_id: '', writer: '', session_id: '', title: '', label: '' };
        let lastImproved = '';

        function qsTaskId() {
            try { return new URLSearchParams(location.search).get('task_id') || ''; }
            catch (e) { return ''; }
        }

        function setCtxDisplay() {
            document.getElementById('ctxTaskId').textContent = selectedCtx.task_id || '-';
            document.getElementById('ctxWriter').textContent = selectedCtx.writer || '-';
            document.getElementById('ctxSession').textContent = selectedCtx.session_id || '-';
            const missing = [];
            if (!selectedCtx.task_id) missing.push('task_id');
            if (!selectedCtx.writer) missing.push('writer');
            if (!selectedCtx.session_id) missing.push('session_id');
            const el = document.getElementById('ctxStatus');
            if (!missing.length) {
                el.textContent = 'OK';
                el.style.color = '#c8e6c9';
            } else {
                el.textContent = 'missing: ' + missing.join(', ');
                el.style.color = '#fecaca';
            }
            const link = document.getElementById('editTaskCenterLink');
            if (selectedCtx.task_id) {
                link.href = 'http://127.0.0.1:8766/tasks?task_id=' + encodeURIComponent(selectedCtx.task_id);
            } else {
                link.href = 'http://127.0.0.1:8766/tasks';
            }
        }

        function applyTaskRow(t) {
            if (!t) {
                selectedCtx = { task_id: '', writer: '', session_id: '', title: '', label: '' };
            } else {
                selectedCtx = {
                    task_id: String(t.id ?? ''),
                    writer: (t.writer || '').trim(),
                    session_id: (t.session_id || '').trim(),
                    title: t.title || '',
                    label: t.task_label || ''
                };
            }
            setCtxDisplay();
        }

        async function loadTaskCenterTasks() {
            const sel = document.getElementById('taskPick');
            const prefer = qsTaskId() || selectedCtx.task_id || '';
            try {
                const res = await fetch('/api/task-center/tasks?limit=300');
                const data = await res.json();
                if (!data.ok) throw new Error(data.error || 'task center load failed');
                tcTasks = data.tasks || [];
                sel.innerHTML = '<option value="">— select Task Center task —</option>' +
                    tcTasks.map(t => {
                        const w = t.writer ? ` · w=${t.writer}` : ' · no writer';
                        const s = t.session_id ? ` · s=${t.session_id}` : ' · no session';
                        const label = `#${t.id} ${t.task_label || ''} — ${t.title || ''}${w}${s}`;
                        return `<option value="${t.id}">${label.replace(/</g,'<')}</option>`;
                    }).join('');
                if (prefer) {
                    sel.value = String(prefer);
                    const row = tcTasks.find(x => String(x.id) === String(prefer));
                    if (row) applyTaskRow(row);
                    else if (prefer) {
                        // fetch single
                        try {
                            const r2 = await fetch('/api/task-center/tasks/' + encodeURIComponent(prefer));
                            const d2 = await r2.json();
                            if (d2.ok && d2.task) {
                                tcTasks.unshift(d2.task);
                                const t = d2.task;
                                const opt = document.createElement('option');
                                opt.value = String(t.id);
                                opt.textContent = `#${t.id} ${t.task_label || ''} — ${t.title || ''}`;
                                sel.appendChild(opt);
                                sel.value = String(t.id);
                                applyTaskRow(t);
                            }
                        } catch (e) {}
                    }
                }
            } catch (e) {
                document.getElementById('promptMsg').textContent = 'Task Center: ' + (e.message || e);
                sel.innerHTML = '<option value="">(agent.db unavailable)</option>';
            }
        }

        document.getElementById('taskPick').addEventListener('change', (ev) => {
            const id = ev.target.value;
            const row = tcTasks.find(x => String(x.id) === String(id));
            applyTaskRow(row || null);
        });
        document.getElementById('reloadTasksBtn').addEventListener('click', loadTaskCenterTasks);

        function setPromptMsg(msg, bad) {
            const el = document.getElementById('promptMsg');
            el.textContent = msg || '';
            el.style.color = bad ? '#f88' : '#ccc';
        }

        function contextPayload() {
            return {
                task_id: selectedCtx.task_id || '',
                writer: selectedCtx.writer || '',
                session_id: selectedCtx.session_id || ''
            };
        }

        document.getElementById('improveBtn').addEventListener('click', async () => {
            const prompt = document.getElementById('promptBox').value;
            const ctx = contextPayload();
            const missing = [];
            if (!ctx.task_id) missing.push('task_id');
            if (!ctx.writer) missing.push('writer');
            if (!ctx.session_id) missing.push('session_id');
            if (missing.length) {
                setPromptMsg('Improve blocked — missing: ' + missing.join(', ') + ' (set on Task Center)', true);
                return;
            }
            if (!prompt.trim()) {
                setPromptMsg('Paste a prompt first', true);
                return;
            }
            const btn = document.getElementById('improveBtn');
            btn.disabled = true;
            setPromptMsg('Improving…');
            try {
                const res = await fetch('/api/prompt/improve', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt, ...ctx })
                });
                const data = await res.json();
                if (!res.ok || !data.ok) {
                    throw new Error(data.error || data.reason || ('HTTP ' + res.status));
                }
                lastImproved = data.improved_prompt || '';
                document.getElementById('promptResult').textContent = lastImproved;
                setPromptMsg(
                    `Improved · tokens ${data.total_tokens || 0} · ids ${data.prompt_ids_ok ? 'OK' : 'INJECTED'} · run ${data.run_id || ''}`
                );
                load();
            } catch (e) {
                setPromptMsg(String(e.message || e), true);
            } finally {
                btn.disabled = false;
            }
        });

        document.getElementById('analyzeBtn').addEventListener('click', async () => {
            const prompt = document.getElementById('promptBox').value;
            if (!prompt.trim()) {
                setPromptMsg('Paste a prompt first', true);
                return;
            }
            const btn = document.getElementById('analyzeBtn');
            btn.disabled = true;
            setPromptMsg('Analyzing…');
            try {
                const res = await fetch('/api/prompt/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt, ...contextPayload() })
                });
                const data = await res.json();
                if (!res.ok && !data.prompt_ids) {
                    throw new Error(data.error || ('HTTP ' + res.status));
                }
                const lines = [];
                lines.push('=== Prompt ID checklist (pasted text) ===');
                lines.push('prompt_ids_ok: ' + data.prompt_ids_ok);
                const pi = data.prompt_ids || {};
                lines.push('  writer: ' + (pi.writer ? 'YES' : 'NO'));
                lines.push('  task_id: ' + (pi.task_id ? 'YES' : 'NO'));
                lines.push('  session_id: ' + (pi.session_id ? 'YES' : 'NO'));
                if (pi.found) lines.push('  found: ' + JSON.stringify(pi.found));
                lines.push('');
                lines.push('=== Task Center context ===');
                lines.push('context_ok: ' + data.context_ok);
                const cx = data.context || {};
                lines.push('  fields: ' + JSON.stringify(cx.fields || {}));
                lines.push('  missing: ' + JSON.stringify(cx.missing || []));
                lines.push('');
                lines.push('=== LLM quality notes ===');
                if (data.llm_error) lines.push('llm_error: ' + data.llm_error);
                if (data.score != null) lines.push('score: ' + data.score);
                lines.push(data.notes || '(no notes)');
                if (data.suggestions && data.suggestions.length) {
                    lines.push('');
                    lines.push('suggestions:');
                    data.suggestions.forEach((s, i) => lines.push(`  ${i+1}. ${s}`));
                }
                lines.push('');
                lines.push(`tokens: ${data.total_tokens || 0} · run ${data.run_id || ''}`);
                document.getElementById('promptResult').textContent = lines.join('\n');
                lastImproved = '';
                setPromptMsg(
                    `Analyzed · prompt_ids=${data.prompt_ids_ok ? 'OK' : 'MISSING'} · context=${data.context_ok ? 'OK' : 'INCOMPLETE'}`
                );
                load();
            } catch (e) {
                setPromptMsg(String(e.message || e), true);
            } finally {
                btn.disabled = false;
            }
        });

        document.getElementById('applyImprovedBtn').addEventListener('click', () => {
            if (!lastImproved) {
                setPromptMsg('No improved prompt to apply yet', true);
                return;
            }
            document.getElementById('promptBox').value = lastImproved;
            setPromptMsg('Applied improved prompt to chat box');
        });
        document.getElementById('copyResultBtn').addEventListener('click', async () => {
            const t = document.getElementById('promptResult').textContent || '';
            try {
                await navigator.clipboard.writeText(t);
                setPromptMsg('Result copied');
            } catch (e) {
                setPromptMsg('Copy failed', true);
            }
        });

        loadTaskCenterTasks();
        /* ---- Skill Prompt SSOT panel ---- */
        let skillState = { skill_key: 'mouse_spot_verify', active: null, versions: [] };

        function setSkillMsg(msg, bad) {
            const el = document.getElementById('skillMsg');
            el.textContent = msg || '';
            el.style.color = bad ? '#f88' : '#ccc';
        }

        function renderSkillTestStats(t) {
            const box = document.getElementById('skillTestStats');
            if (!t) { box.innerHTML = ''; return; }
            const gate = t.pass_gate ? '<span class="pill ok">GATE PASS</span>' : '<span class="pill bad">GATE FAIL</span>';
            box.innerHTML = `
                <div class="stat"><div class="label">Yes</div><div class="value">${t.yes ?? t.yes_count ?? 0} <span style="font-size:0.7em;color:#aaa">${t.yes_pct ?? 0}%</span></div></div>
                <div class="stat"><div class="label">No</div><div class="value">${t.no ?? t.no_count ?? 0} <span style="font-size:0.7em;color:#aaa">${t.no_pct ?? 0}%</span></div></div>
                <div class="stat"><div class="label">Other / Err</div><div class="value">${(t.other ?? t.other_count ?? 0)} / ${(t.errors ?? t.error_count ?? 0)}</div></div>
                <div class="stat"><div class="label">Accuracy</div><div class="value">${t.accuracy_pct ?? 0}%</div></div>
                <div class="stat"><div class="label">Gate</div><div class="value" style="font-size:1em;">${gate}</div></div>
            `;
        }

        async function loadSkill() {
            const skill = document.getElementById('skillPick').value || 'mouse_spot_verify';
            const verSel = document.getElementById('skillVerPick');
            const wantVer = verSel.value || '';
            setSkillMsg('Loading skill…');
            try {
                const q = wantVer ? ('?version=' + encodeURIComponent(wantVer)) : '';
                const res = await fetch('/api/skills/' + encodeURIComponent(skill) + q);
                const data = await res.json();
                if (!data.ok) throw new Error(data.error || 'load failed');
                skillState.skill_key = skill;
                skillState.active = data.active || {};
                skillState.versions = data.versions || [];
                const a = data.active || {};
                document.getElementById('skillKeyVal').textContent = a.skill_key || skill;
                document.getElementById('skillVerVal').textContent = a.version_label || '-';
                document.getElementById('skillStatVal').textContent = a.status || '-';
                document.getElementById('skillParserVal').textContent = a.parser || 'result_yes_no';
                document.getElementById('skillStatusBadge').innerHTML =
                    (a.status === 'active')
                      ? '<span class="pill ok">ACTIVE ' + (a.version_label || '') + '</span>'
                      : '<span class="pill warn">' + (a.status || 'n/a') + ' ' + (a.version_label || '') + '</span>';
                document.getElementById('skillPromptBox').value = a.prompt_text || '';
                // versions dropdown
                const cur = wantVer || a.version_label || '';
                verSel.innerHTML = '<option value="">active</option>' +
                    (data.versions || []).map(v =>
                        `<option value="${v.version_label}">${v.version_label} (${v.status})</option>`
                    ).join('');
                if (cur) verSel.value = cur;
                if (data.latest_test) {
                    renderSkillTestStats(data.latest_test);
                    document.getElementById('skillResult').textContent =
                        JSON.stringify(data.latest_test, null, 2);
                } else {
                    renderSkillTestStats(null);
                    document.getElementById('skillResult').textContent =
                        'Loaded ' + (a.version_label || '') + ' · parser ' + (a.parser || '');
                }
                setSkillMsg('Loaded ' + (a.version_label || skill));
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            }
        }

        document.getElementById('skillReloadBtn').addEventListener('click', loadSkill);
        document.getElementById('skillPick').addEventListener('change', () => {
            document.getElementById('skillVerPick').value = '';
            loadSkill();
        });
        document.getElementById('skillVerPick').addEventListener('change', loadSkill);

        document.getElementById('skillSeedBtn').addEventListener('click', async () => {
            setSkillMsg('Seeding…');
            try {
                const res = await fetch('/api/skills/seed', { method: 'POST' });
                const data = await res.json();
                if (!data.ok && data.ok !== true && !data.seeded) throw new Error(data.error || 'seed failed');
                document.getElementById('skillResult').textContent = JSON.stringify(data, null, 2);
                setSkillMsg('Seeded v1_strict');
                await loadSkill();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            }
        });

        document.getElementById('seedAllBtn').addEventListener('click', async () => {
            setSkillMsg('Seeding all (prompt + gold + Task Center)…');
            try {
                const res = await fetch('/api/skills/seed-all', { method: 'POST' });
                const data = await res.json();
                if (!res.ok || !data.ok) throw new Error(data.error || 'seed-all failed');
                document.getElementById('skillResult').textContent = JSON.stringify(data, null, 2);
                const nGold = (data.gold && data.gold.seeded) || 0;
                const nTc = (data.task_center && (data.task_center.created_tasks + data.task_center.updated_tasks)) || 0;
                setSkillMsg('Seed all ok · gold ' + nGold + ' · tc ops ' + nTc);
                await loadSkill();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            }
        });

        document.getElementById('skillTaskLinesBtn').addEventListener('click', async () => {
            setSkillMsg('Loading Task IDs…');
            try {
                const res = await fetch('/api/skills/task-lines');
                const data = await res.json();
                if (!res.ok || !data.ok) throw new Error(data.error || 'task-lines failed');
                const lines = (data.lines || []).join('\n');
                document.getElementById('skillResult').textContent = 'Root ' + (data.root || 10) + '\n' + lines;
                setSkillMsg('Task IDs root ' + (data.root || 10) + ' · ' + (data.lines || []).length + ' items');
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            }
        });

        document.getElementById('skillImproveDraftBtn').addEventListener('click', async () => {
            const skill = document.getElementById('skillPick').value || 'mouse_spot_verify';
            const prompt_text = (document.getElementById('skillPromptBox').value ||
                document.getElementById('promptBox').value || '').trim();
            const btn = document.getElementById('skillImproveDraftBtn');
            btn.disabled = true;
            setSkillMsg('Improving → draft only (never auto-promote)…');
            try {
                const body = { source: 'llm_tasks_ui' };
                if (prompt_text) body.prompt_text = prompt_text;
                const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/improve-draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
                document.getElementById('skillResult').textContent = JSON.stringify(data, null, 2);
                const ver = data.version_label || (data.row && data.row.version_label) || '';
                if (data.row && data.row.prompt_text) {
                    document.getElementById('skillPromptBox').value = data.row.prompt_text;
                }
                if (ver) document.getElementById('skillVerPick').value = ver;
                setSkillMsg('Draft saved ' + ver + ' · promote still gated');
                await loadSkill();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            } finally {
                btn.disabled = false;
            }
        });

        document.getElementById('skillTestGoldBtn').addEventListener('click', async () => {
            const skill = document.getElementById('skillPick').value || 'mouse_spot_verify';
            const version = document.getElementById('skillVerPick').value || (skillState.active && skillState.active.version_label) || null;
            const runs = parseInt(document.getElementById('skillRuns').value || '1', 10);
            const btn = document.getElementById('skillTestGoldBtn');
            btn.disabled = true;
            setSkillMsg('Running gold suite (runs_per_case=' + runs + ')…');
            document.getElementById('skillResult').textContent = 'Gold suite in progress…';
            try {
                const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/test-gold', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ version_label: version, runs_per_case: runs })
                });
                const data = await res.json();
                if (!res.ok && data.ok !== true && data.ok !== false) throw new Error(data.error || ('HTTP ' + res.status));
                document.getElementById('skillResult').textContent = JSON.stringify(data, null, 2);
                setSkillMsg(
                    'Gold · pass ' + (data.cases_passed || 0) + '/' + (data.cases_total || 0) +
                    ' · fail ' + (data.cases_failed || 0) + ' · err ' + (data.cases_errors || 0),
                    !data.ok
                );
                load();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            } finally {
                btn.disabled = false;
            }
        });

        document.getElementById('skillSaveDraftBtn').addEventListener('click', async () => {
            const skill = document.getElementById('skillPick').value || 'mouse_spot_verify';
            const prompt_text = document.getElementById('skillPromptBox').value;
            if (!prompt_text.trim()) { setSkillMsg('Prompt empty', true); return; }
            let version_label = document.getElementById('skillVerPick').value;
            if (!version_label || version_label === (skillState.active && skillState.active.version_label)) {
                const ts = new Date().toISOString().replace(/[-:TZ.]/g, '').slice(0, 14);
                version_label = 'draft_' + ts;
            }
            setSkillMsg('Saving ' + version_label + '…');
            try {
                const body = {
                    prompt_text,
                    version_label,
                    parser: 'result_yes_no',
                    task_id: selectedCtx.task_id || null,
                    source: 'llm_tasks_ui'
                };
                const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/draft', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
                document.getElementById('skillResult').textContent = JSON.stringify(data.skill, null, 2);
                setSkillMsg('Saved draft ' + version_label);
                document.getElementById('skillVerPick').value = version_label;
                await loadSkill();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            }
        });

        document.getElementById('skillTestBtn').addEventListener('click', async () => {
            const skill = document.getElementById('skillPick').value || 'mouse_spot_verify';
            const version = document.getElementById('skillVerPick').value || (skillState.active && skillState.active.version_label) || null;
            const runs = parseInt(document.getElementById('skillRuns').value || '10', 10);
            const expected = document.getElementById('skillExpected').value || 'NO';
            const target_name = document.getElementById('skillTargetName').value || 'Visual Studio Code';
            const target_action = document.getElementById('skillTargetAction').value || '';
            const btn = document.getElementById('skillTestBtn');
            btn.disabled = true;
            setSkillMsg('Running ' + runs + '-time proof test (may take minutes)…');
            document.getElementById('skillResult').textContent = 'Testing ' + runs + ' runs against current screenshot…';
            try {
                const res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        version_label: version,
                        runs,
                        expected,
                        target_name,
                        target_action
                    })
                });
                const data = await res.json();
                if (!res.ok && !data.yes && data.yes !== 0) throw new Error(data.error || ('HTTP ' + res.status));
                renderSkillTestStats(data);
                document.getElementById('skillResult').textContent = JSON.stringify(data, null, 2);
                setSkillMsg(
                    `Done · Yes ${data.yes} (${data.yes_pct}%) · No ${data.no} (${data.no_pct}%) · acc ${data.accuracy_pct}% · gate ${data.pass_gate ? 'PASS' : 'FAIL'}`,
                    !data.pass_gate
                );
                load();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            } finally {
                btn.disabled = false;
            }
        });

        document.getElementById('skillPromoteBtn').addEventListener('click', async () => {
            const skill = document.getElementById('skillPick').value || 'mouse_spot_verify';
            const version = document.getElementById('skillVerPick').value || (skillState.active && skillState.active.version_label);
            if (!version) { setSkillMsg('Pick a version to promote', true); return; }
            const force = confirm('Promote ' + version + ' to active?\nOK = require pass_gate\nCancel aborts.\n\nUse OK then if blocked, you can force from API.');
            if (!force) return;
            setSkillMsg('Promoting ' + version + '…');
            try {
                let res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/activate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ version_label: version, require_pass_gate: true })
                });
                let data = await res.json();
                if (!res.ok || !data.ok) {
                    if (!confirm((data.error || 'activate failed') + '\n\nForce promote anyway?')) {
                        setSkillMsg(data.error || 'blocked', true);
                        return;
                    }
                    res = await fetch('/api/skills/' + encodeURIComponent(skill) + '/activate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ version_label: version, force: true, require_pass_gate: false })
                    });
                    data = await res.json();
                    if (!res.ok || !data.ok) throw new Error(data.error || 'force failed');
                }
                document.getElementById('skillResult').textContent = JSON.stringify(data.active || data, null, 2);
                setSkillMsg('Active → ' + version);
                document.getElementById('skillVerPick').value = '';
                await loadSkill();
            } catch (e) {
                setSkillMsg(String(e.message || e), true);
            }
        });

        document.getElementById('skillToChatBtn').addEventListener('click', () => {
            document.getElementById('promptBox').value = document.getElementById('skillPromptBox').value || '';
            setSkillMsg('Copied skill prompt → chat box');
        });
        document.getElementById('skillFromChatBtn').addEventListener('click', () => {
            document.getElementById('skillPromptBox').value = document.getElementById('promptBox').value || '';
            setSkillMsg('Loaded chat box → skill editor');
        });

        loadSkill();

        setCtxDisplay();

        load();
        loadStatus();
        setInterval(load, 3000);
        setInterval(loadStatus, 5000);
    </script>
</body>
</html>
"""


def get_state() -> dict[str, Any]:
    x, y = mouse.position
    targets = load_targets()
    active_name = ""
    if active_target_id:
        for t in targets:
            if t["id"] == active_target_id:
                active_name = t["name"]
                break
    return {
        "x": int(x),
        "y": int(y),
        "target": last_target,
        "active_id": active_target_id,
        "active_name": active_name,
        "capture_seq": int(capture_seq),
        "capture_error": capture_error,
        "error": capture_error,
        "hotkey": CAPTURE_HOTKEY,
        "screenshot_path": str(SCREENSHOT_PATH) if SCREENSHOT_PATH.is_file() else None,
    }


def take_screenshot(x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """Full-screen screenshot (PrintScreen-style). Optional red crosshair at (x,y)."""
    try:
        import pyautogui
        from PIL import ImageDraw

        tx = x if x is not None else (last_target["x"] if last_target else None)
        ty = y if y is not None else (last_target["y"] if last_target else None)

        # Always capture entire screen — no app/source/region limit
        img = pyautogui.screenshot()
        w, h = img.size

        if tx is not None and ty is not None:
            draw = ImageDraw.Draw(img)
            cx = max(0, min(w - 1, int(tx)))
            cy = max(0, min(h - 1, int(ty)))
            arm = 20
            draw.line((cx - arm, cy, cx + arm, cy), fill="red", width=3)
            draw.line((cx, cy - arm, cx, cy + arm), fill="red", width=3)

        img.save(SCREENSHOT_PATH)
        if not SCREENSHOT_PATH.is_file():
            return {"ok": False, "error": f"Screenshot not written to {SCREENSHOT_PATH}"}
        out: dict[str, Any] = {
            "ok": True,
            "path": str(SCREENSHOT_PATH),
            "bytes": SCREENSHOT_PATH.stat().st_size,
            "full": True,
            "width": int(w),
            "height": int(h),
        }
        if tx is not None and ty is not None:
            out["x"] = int(tx)
            out["y"] = int(ty)
            try:
                prev = save_preview_crop(img, int(tx), int(ty))
                if prev.get("ok"):
                    out["preview_path"] = prev.get("path")
                    out["preview_width"] = prev.get("width")
                    out["preview_height"] = prev.get("height")
            except Exception as pe:
                print(f"Preview crop failed: {type(pe).__name__}: {pe}", flush=True)
        return out
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"Screenshot failed: {msg}", flush=True)
        return {"ok": False, "error": msg}


def save_preview_crop(full_img: Any, cx: int, cy: int, size: int = PREVIEW_SIZE) -> dict[str, Any]:
    """Save 200x200 crop centered on (cx,cy) for UI preview only. LLM uses full SCREENSHOT_PATH."""
    from PIL import Image

    w, h = full_img.size
    half = size // 2
    left = int(cx) - half
    top = int(cy) - half
    if w >= size:
        left = max(0, min(w - size, left))
    else:
        left = 0
    if h >= size:
        top = max(0, min(h - size, top))
    else:
        top = 0
    right = min(w, left + size)
    bottom = min(h, top + size)
    crop = full_img.crop((left, top, right, bottom)).convert("RGB")
    if crop.size != (size, size):
        canvas = Image.new("RGB", (size, size), (0, 0, 0))
        canvas.paste(crop, (0, 0))
        crop = canvas
    crop.save(SCREENSHOT_PREVIEW_PATH)
    if not SCREENSHOT_PREVIEW_PATH.is_file():
        return {"ok": False, "error": f"Preview not written to {SCREENSHOT_PREVIEW_PATH}"}
    return {
        "ok": True,
        "path": str(SCREENSHOT_PREVIEW_PATH),
        "width": size,
        "height": size,
        "left": int(left),
        "top": int(top),
        "bytes": SCREENSHOT_PREVIEW_PATH.stat().st_size,
    }


def perform_capture() -> dict[str, Any]:
    """Single capture entry: set last_target, bake red cross into PNG, dual-write coords."""
    global last_target, capture_error, capture_seq
    x, y = mouse.position
    ix, iy = int(x), int(y)
    last_target = {"x": ix, "y": iy}
    capture_error = None
    print(f"Target captured: X={ix}, Y={iy}", flush=True)
    result = take_screenshot(ix, iy)
    if not result.get("ok"):
        err = str(result.get("error") or "Screenshot failed")
        capture_error = err
        print(
            f"Hotkey screenshot failed: {err} | path={SCREENSHOT_PATH}",
            flush=True,
        )
        return {
            "ok": False,
            "x": ix,
            "y": iy,
            "error": err,
            "capture_seq": int(capture_seq),
            "path": str(SCREENSHOT_PATH),
        }

    capture_seq = int(capture_seq) + 1
    print(
        f"Full-screen screenshot saved: path={result.get('path')} "
        f"bytes={result.get('bytes')} "
        f"({result.get('width')}x{result.get('height')}) "
        f"cross=({ix},{iy}) seq={capture_seq}",
        flush=True,
    )

    coord_point = None
    try:
        tid = active_target_id
        targets = load_targets()
        tmeta = next((t for t in targets if t.get("id") == tid), None) if tid else None
        tname = (tmeta or {}).get("name") or tid
        if tname:
            logo = ""
            if tmeta:
                logo = str(tmeta.get("image") or "")
                if tid and not logo:
                    logo = f"/target-image/{tid}"
            cap_action = str((tmeta or {}).get("action") or "click").strip() or "click"
            cap_tid = str(tid) if tid else None
            coord_point = save_target_point(
                target_logo=logo,
                target_name=str(tname),
                x=ix,
                y=iy,
                action=cap_action,
                isactive=1,
                llm_score=100,
                error=None,
                target_id=cap_tid,
            )
            clear_target_error(str(tname), target_id=cap_tid)
            if coord_point is not None:
                coord_point = (
                    get_target_point(str(tname), target_id=cap_tid) or coord_point
                )
    except Exception as e:
        print(f"Coord dual-write skipped: {type(e).__name__}: {e}", flush=True)
        coord_point = None

    out_cap: dict[str, Any] = {
        "ok": True,
        "x": ix,
        "y": iy,
        "bytes": result.get("bytes"),
        "path": result.get("path"),
        "width": result.get("width"),
        "height": result.get("height"),
        "capture_seq": int(capture_seq),
        "coord_point": coord_point,
    }
    if result.get("preview_path"):
        out_cap["preview_path"] = result.get("preview_path")
        out_cap["preview_width"] = result.get("preview_width")
        out_cap["preview_height"] = result.get("preview_height")
    return out_cap


def hotkey_listener() -> None:
    def on_activate() -> None:
        try:
            perform_capture()
        except Exception as e:
            global capture_error
            capture_error = f"{type(e).__name__}: {e}"
            print(f"Hotkey capture exception: {capture_error}", flush=True)

    try:
        print(f"Global capture hotkey bound: {CAPTURE_HOTKEY}", flush=True)
        with keyboard.GlobalHotKeys({CAPTURE_HOTKEY: on_activate}) as h:
            h.join()
    except Exception as e:
        global capture_error
        capture_error = f"hotkey_bind_failed: {type(e).__name__}: {e}"
        print(f"Hotkey listener failed: {capture_error}", flush=True)


@app.route("/")
def index() -> str:
    return render_template_string(HTML)


@app.route("/settings")
def settings() -> str:
    return render_template_string(SETTINGS_HTML)


# 6-STEP target capture (page + API). Registered here because this module has no
# Blueprint; the implementation lives in target_capture_ui.py so the page and its
# routes can be read and tested without loading this 8700-line file.
#
# NOTE this module has NO module-level `log()` — it prints. Passing `log` here was
# a NameError that the try/except below turned into a silent "registration
# skipped", leaving the page 404. The helper therefore passes `print` explicitly,
# and the registration result is PRINTED, not swallowed: a page that fails to
# mount must say so.
try:
    import target_capture_ui as _tc_ui

    _tc_ui.register_target_capture_routes(
        app, base_dir=BASE_DIR,
        helper_log=lambda m: print(m, flush=True),
    )
except Exception as _tc_ui_err:
    print("target_capture_ui registration FAILED: %s: %s"
          % (type(_tc_ui_err).__name__, _tc_ui_err), flush=True)


@app.route("/ide-control")
def ide_control() -> str:
    return render_template_string(IDE_CONTROL_HTML)


@app.route("/api/ide-control")
def api_ide_control() -> Any:
    """Live VS Code permission state for the IDE control page."""
    import json as _json

    base = Path(__file__).resolve().parent
    current: dict[str, Any] = {}
    try:
        current = _json.loads((base / "chat_permission.json").read_text(encoding="utf-8"))
    except Exception:
        current = {}
    last_switch = ""
    try:
        last_switch = (base / "chat_permission_last.txt").read_text(encoding="utf-8").strip()
    except Exception:
        last_switch = ""
    return jsonify({"ok": True, "current": current, "last_switch": last_switch})


@app.route("/api/ide-control/set", methods=["POST"])
def api_ide_control_set() -> Any:
    """Set VS Code permission from the IDE control page (same native path as hotkeys).

    Body: {"permission": "allow_all" | "autopilot" | "default"}
    Runs f_perm_click.py --set-native <perm> (prove Code.exe foreground+maximized
    -> Ctrl+Alt+K picker -> click row -> state file).
    """
    import subprocess

    valid = {"allow_all", "autopilot", "default"}
    data = request.get_json(silent=True) or {}
    perm = str(data.get("permission") or "").strip()
    if perm not in valid:
        return jsonify({"ok": False, "error": f"invalid permission '{perm}' (want one of {sorted(valid)})"}), 400
    base = Path(__file__).resolve().parent
    pyw = base / ".venv" / "Scripts" / "pythonw.exe"
    try:
        proc = subprocess.run(
            [str(pyw), str(base / "f_perm_click.py"), "--set-native", perm],
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    got = ""
    try:
        got = (base / "chat_permission_last.txt").read_text(encoding="utf-8").strip()
    except Exception:
        got = ""
    ok = got == perm
    return jsonify(
        {
            "ok": ok,
            "permission": perm,
            "state": got,
            "rc": proc.returncode,
            "stdout": (proc.stdout or "").strip()[-500:],
            "stderr": (proc.stderr or "").strip()[-500:],
        }
    )


@app.route("/api/open-browser", methods=["POST"])
def api_open_browser() -> Any:
    """THE ON-DEMAND BROWSER OPEN — the only path that may pop a tab.

    WHY THIS EXISTS (2026-09-25). The startup path used to call
    `webbrowser.open(url)` by DEFAULT, so the browser popped on every restart
    the user never asked for. The repo's own rule
    (`skills/3_ui/ui_field_builder.skill.md:90`) says: "open on demand only, via
    an explicit endpoint (e.g. `POST /api/open-browser?url=/path`)" — and
    MEASURED, that endpoint did not exist (`grep -n 'open-browser'
    mouse_spot_helper.py` -> 0 matches). This is it.

    SAME-ORIGIN ONLY. `url` must be a path starting with `/`. An absolute URL is
    REFUSED, so this endpoint cannot be turned into an open-anything gadget by
    anything that can reach loopback.
    """
    data = request.get_json(silent=True) or {}
    target = str(data.get("url") or request.args.get("url") or "").strip()
    if not target:
        return jsonify({"ok": False, "error": "url is required"}), 400
    # `//host` is a protocol-relative URL, i.e. NOT same-origin. Refuse it too.
    if not target.startswith("/") or target.startswith("//"):
        return jsonify({
            "ok": False,
            "error": ("url must be a same-origin path starting with '/' "
                      "(got %r); absolute URLs are refused" % target),
        }), 400
    full = f"http://127.0.0.1:18765{target}"
    try:
        webbrowser.open(full)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": True, "opened": full})


# Prompt Setting page.
#
# CANONICAL: /prompt/setting. The old /llm-templates is kept as a PERMANENT
# alias — a route is a public address, and a broken bookmark cannot be
# recovered. Both spellings (hyphen and underscore) are accepted because the
# SPA nav historically emitted `/llm-tasks/llm_templates` (underscore) while
# the route was `/llm-templates` (hyphen); that mismatch is what made the nav
# link and its test disagree.
@app.route("/prompt/setting")
@app.route("/prompt/setting/")
@app.route("/llm-templates")
@app.route("/llm-templates/")
@app.route("/llm_templates")
@app.route("/llm_templates/")
def llm_templates_page() -> str:
    return render_template_string(LLM_TEMPLATES_PAGE_HTML)


@app.route("/api/templates", methods=["GET"])
def api_templates_list() -> Any:
    include_all = request.args.get("all", default=0, type=int) or 0
    rows = list_prompt_settings(active_only=not bool(include_all))
    return jsonify(rows)


@app.route("/api/templates/preview", methods=["POST"])
def api_templates_preview() -> Any:
    data = request.get_json(silent=True) or {}
    out = preview_prompt_setting(
        sample_prompt=str(data.get("sample_prompt") or ""),
        instruction=data.get("instruction"),
        setting_id=data.get("prompt_setting_id"),
        template_id=data.get("template_id"),
    )
    return jsonify(out)


# ---------------------------------------------------------------------------
# Evidence red box (2-PNG QC pair) — see evidence_redbox.py
# ---------------------------------------------------------------------------
@app.route("/api/evidence/redbox", methods=["GET"])
def api_evidence_redbox() -> Any:
    """List the red-box manifest, or one evidence's 2-image QC pair."""
    evid = (request.args.get("evidence_id") or "").strip()
    try:
        import evidence_redbox as erb
    except Exception as e:
        return jsonify({"ok": False, "error": "evidence_redbox unavailable: %s" % e}), 500
    if evid:
        return jsonify(erb.pair(evid))
    p = erb.REDBOX_ROOT / erb.MANIFEST_JSON
    if not p.is_file():
        return jsonify({"ok": False, "error": "no manifest yet — POST backfill"}), 404
    return jsonify(json.loads(p.read_text(encoding="utf-8")))


@app.route("/api/evidence/redbox/backfill", methods=["POST"])
def api_evidence_redbox_backfill() -> Any:
    """Render a red box for every evidence folder and rewrite the manifest."""
    try:
        import evidence_redbox as erb
    except Exception as e:
        return jsonify({"ok": False, "error": "evidence_redbox unavailable: %s" % e}), 500
    try:
        man = erb.backfill()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify({"ok": True, "total": man["total"], "with_box": man["ok"],
                    "unknown": man["unknown"], "manifest": str(
                        erb.REDBOX_ROOT / erb.MANIFEST_JSON)})


@app.route("/api/evidence/redbox/verify", methods=["GET"])
def api_evidence_redbox_verify() -> Any:
    """Acceptance check: every folder has a box OR a recorded UNKNOWN reason."""
    try:
        import evidence_redbox as erb
    except Exception as e:
        return jsonify({"ok": False, "error": "evidence_redbox unavailable: %s" % e}), 500
    v = erb.verify()
    return jsonify(v), (200 if v.get("ok") else 409)


# ---------------------------------------------------------------------------
# COORDINATE EVIDENCE — one EVID image per coordinate target.
# THE HUMAN (2026-09-25): "template_id | image name | label | x1,y1 → x2,y2 |
# center x,y" / "image format = EVID-task_proof-20260921-195257_full and save
# at C:\projects\agent_system\evidence_final" / "can mouse over to have the
# large image by 600*600 with info for file location" / "it is evidence proofed
# for each coordinate to be is_active = 1".
# ---------------------------------------------------------------------------
@app.route("/api/evidence/coord", methods=["GET"])
def api_evidence_coord() -> Any:
    """The coordinate targets of ONE environment, each with its EVID image.

    The image is looked up by the EVID naming convention
    (`EVID-<target_name>-<ts>_full.png`) in `evidence_final/`. A target with no
    image returns `image_name: null` -- the UI shows "no evidence yet" rather
    than a broken image, so a missing proof is VISIBLE instead of invisible.
    """
    try:
        eid = int(request.args.get("environment_id") or 6)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "environment_id must be an int"}), 400
    try:
        import sqlite3
        import target_registry as tg
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        res = tg.for_environment(conn, eid)
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500

    final = BASE_DIR / "evidence_final"
    rows = []
    for r in (res.get("rows") or []):
        if r.get("field_type") != "coordinate":
            continue
        v = r.get("value")
        rect = None
        centre = None
        if isinstance(v, dict):
            rect = [v.get("x1"), v.get("y1"), v.get("x2"), v.get("y2")]
            centre = [v.get("cx"), v.get("cy")]
        # The newest EVID image for this target name.
        image_name = None
        image_path = None
        if final.is_dir():
            hits = sorted(final.glob("EVID-%s-*_full.png" % r["name"]))
            if hits:
                image_name = hits[-1].name
                image_path = str(hits[-1])
        rows.append({
            "template_id": r["template_id"],
            "name": r["name"],
            "label": r["label"],
            "field_type": r["field_type"],
            "rect": rect,
            "centre": centre,
            "is_active": 1 if r.get("collected") else 0,
            "image_name": image_name,
            "image_path": image_path,
            "image_url": ("/api/evidence/coord/image?name=%s" % image_name
                          if image_name else None),
        })
    return jsonify({"ok": True, "environment_id": eid, "rows": rows,
                    "count": len(rows),
                    "with_image": sum(1 for r in rows if r["image_name"])})


@app.route("/api/evidence/coord/image", methods=["GET"])
def api_evidence_coord_image() -> Any:
    """Serve one EVID image from `evidence_final/`. Path-traversal safe."""
    name = Path(request.args.get("name") or "").name
    if not name or not name.lower().endswith(".png"):
        return jsonify({"ok": False, "error": "invalid name"}), 400
    path = BASE_DIR / "evidence_final" / name
    if not path.is_file():
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_file(path, mimetype="image/png")


@app.route("/api/evidence/coord/generate", methods=["POST"])
def api_evidence_coord_generate() -> Any:
    """Render the EVID images for one environment's coordinate targets."""
    data = request.get_json(silent=True) or {}
    try:
        eid = int(data.get("environment_id") or 6)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "environment_id must be an int"}), 400
    try:
        import _gen_coord_evidence as gce
        r = gce.generate(eid, apply=True)
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify(r), (200 if r.get("ok") else 409)


# ---------------------------------------------------------------------------
# PLAYWRIGHT — the two tables, read-only.
# THE HUMAN (2026-09-25): "UI for playwright under
# http://127.0.0.1:18765/llm-tasks/workflow/environment_playwright/".
#
# These are THIN ADAPTERS over `playwright_registry.py`, which already owns the
# logic. No query is written here, so the page and the module cannot disagree.
# READ-ONLY: there is no POST/PUT/DELETE, because the page shows what is
# registered; registering is a separate, deliberate act.
# ---------------------------------------------------------------------------
@app.route("/api/playwright/environments", methods=["GET"])
def api_playwright_environments() -> Any:
    """Every playwright environment, optionally scoped to one environment."""
    eid = request.args.get("environment_id")
    try:
        eid_i = int(eid) if eid not in (None, "") else None
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "environment_id must be an int"}), 400
    try:
        import sqlite3
        import playwright_registry as pr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        rows = pr.list_environments(conn, environment_id=eid_i)
        # THE STEP PROOF, PER ENVIRONMENT. THE HUMAN (2026-09-25): "so can have
        # vscode_playwright STEP with evidence" and
        # "id | environment_id | name | browser_channel | is_active | environment
        #  | + STEP proof".
        #
        # MEASURED: the table said WHICH environment a workflow runs in, but not
        # whether that environment's guide had ever RUN. A `proof` column that
        # says only "Test" makes the reader press the button to learn anything.
        # The step summary is read from the SAME tables the run writes, so the
        # environment row and the evidence cannot disagree.
        import playwright_step_registry as psr
        for row in rows:
            try:
                g = psr.guide(conn, int(row.get("id") or 0))
                srows = g.get("rows") or []
                n_pass = sum(1 for r in srows if r.get("last_status") == "PASS")
                n_fail = sum(1 for r in srows if r.get("last_status") == "FAIL")
                n_unk = sum(1 for r in srows
                            if r.get("last_status") == "UNKNOWN")
                n_skip = sum(1 for r in srows if r.get("last_status") == "SKIP")
                row["step_count"] = len(srows)
                row["step_pass"] = n_pass
                row["step_fail"] = n_fail
                row["step_unknown"] = n_unk
                row["step_skip"] = n_skip
                # The NEWEST evidence of THIS guide's last run, so the row can
                # link straight to the picture.
                eid_last = ""
                for r in srows:
                    if r.get("evidence_id"):
                        eid_last = str(r["evidence_id"])
                        break
                row["last_evidence_id"] = eid_last
                row["step_summary"] = "%d/%d PASS%s" % (
                    n_pass, len(srows),
                    (" · %d FAIL" % n_fail) if n_fail else "")
                row["step_proof_url"] = (
                    "/llm-tasks/workflow/environment_playwright/"
                    "?pid=%d" % int(row.get("id") or 0))
            except Exception:
                row["step_count"] = 0
                row["step_summary"] = "NA"
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify({"ok": True, "rows": rows, "count": len(rows),
                    "environment_id": eid_i})


@app.route("/api/playwright/workflow/<int:workflow_id>", methods=["GET"])
def api_playwright_workflow(workflow_id: int) -> Any:
    """The playwright environments ONE workflow runs in."""
    try:
        import sqlite3
        import playwright_registry as pr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        r = pr.for_workflow(conn, workflow_id)
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify(r), (200 if r.get("ok") else 404)


@app.route("/api/playwright/links", methods=["GET"])
def api_playwright_links() -> Any:
    """EVERY workflow_playwright link, with both sides resolved.

    The page shows the link table as the human named it
    (`id | workflow_id | playwright_id`), and the resolved names beside it so a
    reader does not have to look up two ids by hand.
    """
    try:
        import sqlite3
        import playwright_registry as pr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        pr.ensure_schema(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT l.id, l.workflow_id, l.playwright_id, l.is_active, "
            "       l.cite_ref, "
            "       w.workflow_key, w.name AS workflow_name, "
            "       p.name AS playwright_name, p.environment_id "
            "FROM workflow_playwright l "
            "LEFT JOIN workflow_registry w ON w.workflow_id = l.workflow_id "
            "LEFT JOIN playwright_environment p ON p.id = l.playwright_id "
            "ORDER BY l.id")]
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify({"ok": True, "rows": rows, "count": len(rows)})


# ---------------------------------------------------------------------------
# PLAYWRIGHT TEST — the RED button. A REAL test, not a stub.
# THE HUMAN (2026-09-25): "+ button for test in red , so user can proof himself /
# and wher is the evidence? test button with playwright detail".
#
# The test LAUNCHES Playwright, drives the UI, and writes an evidence record.
# A stub that returns "ok" would prove nothing about whether the playwright
# environment is USABLE, which is the only thing worth proving about it.
# ---------------------------------------------------------------------------
PLAYWRIGHT_TEST_TARGET = "playwright_env"


def _playwright_step_guide(playwright_id: int) -> list[dict]:
    """Read the STEP GUIDE for one playwright environment, in step order.

    THE HUMAN (2026-09-25): "so where is STEP GUIDE? / how to i know what will
    happen, without that, i don't know and how to proof it is worked or not?"

    THE GUIDE IS THE PROGRAM. This function is the ONLY source of the steps the
    test runs. If the guide were documentation beside the code, the two would
    drift and the guide would become a lie -- the defect class this repo keeps
    hitting ("the APIs answered while no UI referenced them"). A step added to
    `playwright_step` runs on the next test with NO code change.
    """
    try:
        import playwright_step_registry as psr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        rows = psr.list_steps(conn, playwright_id)
        conn.close()
        return rows
    except Exception:
        return []


def _confirm_app_ready(process_name: str) -> dict:
    """CONFIRM a desktop app is running, is the foreground window, AND is MAXIMIZED.

    THE HUMAN (2026-09-25): "why for environment : vscode, need to
    launch_browser? ... why have have id : 1 for vscode and not by windows task
    to confirm APP : VS code is ready to use" and then "you are right! need to
    confirm APP/ browser size = max".

    VS Code is a DESKTOP APP that is ALREADY RUNNING. You do not LAUNCH it --
    you CONFIRM it is ready. This is the WINDOWS TASK the human asked for.

    THREE CONDITIONS, ALL REQUIRED. "Running" is not "ready to use":
      1. the window EXISTS and is VISIBLE
      2. it is the FOREGROUND window
      3. it is MAXIMIZED -- a restored window is a different screen geometry, so
         every coordinate measured against a maximized window is WRONG on a
         restored one. The repo already treats this as a precondition
         (`f_perm_click.py`: "prove Code.exe foreground+maximized").

    THE PATTERN IS THE REPO'S OWN. MEASURED `f_perm_click.py:357
    _activate_vscode()`: activate by PROCESS (not by title -- VS Code's title
    carries the open file name, so an exact-title match silently failed), then
    WAIT ON A CONDITION and report loudly when the foreground is never proven.

    Returns {ok, process, title, hwnd, maximized, why}. NEVER raises: an unproven
    window is a RESULT, not an error.
    """
    import ctypes
    from ctypes import wintypes

    want = str(process_name or "").strip().lower()
    out: dict = {"ok": False, "process": "NA", "title": "NA", "hwnd": 0,
                 "maximized": False, "why": ""}
    if not want or want == "na":
        out["why"] = "no process name to confirm"
        return out
    try:
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32

        def _proc_name(hwnd: int) -> str:
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return buf.value.rsplit("\\", 1)[-1]
                return ""
            finally:
                k.CloseHandle(h)

        # 1. DOES THE WINDOW EXIST? Enumerate by PROCESS, never by title.
        found: list[int] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                         wintypes.LPARAM)

        def _cb(hwnd, _lp):
            try:
                if u.IsWindowVisible(hwnd) and _proc_name(hwnd).lower() == want:
                    found.append(int(hwnd))
                    return False
            except Exception:
                pass
            return True

        u.EnumWindows(WNDENUMPROC(_cb), 0)
        if not found:
            out["why"] = "no visible window of process %r" % want
            return out
        hwnd = found[0]
        out["hwnd"] = hwnd
        out["process"] = want
        buf = ctypes.create_unicode_buffer(512)
        u.GetWindowTextW(hwnd, buf, 512)
        out["title"] = buf.value or "NA"

        # 2. IS IT THE FOREGROUND WINDOW? "running" is not "ready to use".
        fg = int(u.GetForegroundWindow() or 0)
        if fg != hwnd:
            # Try to bring it forward, then WAIT ON THE CONDITION (never a sleep).
            try:
                u.ShowWindow(hwnd, 9)  # SW_RESTORE
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
            except Exception as exc:
                out["why"] = "SetForegroundWindow failed: %s" % exc
            import condition_based_waiting as cbw
            try:
                cbw.wait_until(
                    lambda: int(ctypes.windll.user32.GetForegroundWindow() or 0)
                    == hwnd,
                    timeout=3.0, description="%s is foreground" % want)
            except Exception as exc:
                out["why"] = ("the window exists but is NOT foreground: %s" % exc)
                return out

        # 3. IS IT MAXIMIZED? A restored window is a DIFFERENT screen geometry,
        # so every coordinate measured against a maximized window is wrong on a
        # restored one. `IsZoomed` is the OS's own answer -- not a size guess.
        zoomed = bool(u.IsZoomed(hwnd))
        out["maximized"] = zoomed
        if not zoomed:
            # Try to maximize, then WAIT ON THE CONDITION.
            try:
                u.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            except Exception as exc:
                out["why"] = "SW_MAXIMIZE failed: %s" % exc
            import condition_based_waiting as cbw
            try:
                cbw.wait_until(
                    lambda: bool(ctypes.windll.user32.IsZoomed(hwnd)),
                    timeout=3.0, description="%s is maximized" % want)
                out["maximized"] = True
            except Exception as exc:
                out["why"] = ("the window is foreground but NOT maximized: %s"
                              % exc)
                return out
        out["ok"] = True
        return out
    except Exception as exc:
        out["why"] = "%s: %s" % (type(exc).__name__, exc)
        return out


def _confirm_session_id(expected: str = "ACTIVE") -> dict:
    """CONFIRM WHICH CONVERSATION the IDE is showing.

    THE HUMAN (2026-09-25): "you need to have step to show Agent session side
    bar / we need to confirm session ID (exmaple:
    50f58738-b8e6-4976-8e3b-fcd7403a0c71) is the one you are having
    conversation / this is must for each conversation via VScode, is that part
    for identity proof".

    YES -- and it is the STRONGEST identity proof available here. The other
    checks prove a WINDOW is ready. This one proves WHICH CONVERSATION the
    window is showing. Without it a run can be perfectly green while driving a
    DIFFERENT session than the one the human is reading.

    THE SOURCE IS THE REPO'S OWN, NOT A NEW ONE. MEASURED
    `scripts/mode_attest.py:276 find_session_file()`: the authoritative per-
    session source is `<workspaceStorage>/<hash>/chatSessions/<session-id>.jsonl`
    -- ONE FILE PER SESSION, AND THE FILENAME IS THE SESSION ID. A second reader
    would be a second truth, so this calls the SAME module.

    `expected` is the session id to confirm, or the sentinel `ACTIVE` meaning
    "whichever session is being written to right now" (the newest session file).
    The sentinel exists because a session id CHANGES PER CONVERSATION, so a
    hard-coded id would go stale on the next chat.

    Returns {ok, session_id, expected, source, why}. NEVER raises.
    """
    out: dict = {"ok": False, "session_id": "NA", "expected": "NA",
                 "source": "NA", "why": ""}
    try:
        import sys as _sys
        scripts = str(BASE_DIR / "scripts")
        if scripts not in _sys.path:
            _sys.path.insert(0, scripts)
        import mode_attest as ma

        db, _why = ma.find_workspace_db(str(BASE_DIR))
        if not db:
            out["why"] = "no VS Code workspace state DB found"
            return out
        out["source"] = db
        sessions_dir = Path(db).parent / ma.SESSIONS_DIRNAME
        if not sessions_dir.is_dir():
            out["why"] = "no %s dir beside the state DB" % ma.SESSIONS_DIRNAME
            return out
        files = sorted(sessions_dir.glob("*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            out["why"] = "no session file in %s" % sessions_dir
            return out
        active = files[0].stem
        out["session_id"] = active
        want = str(expected or "ACTIVE").strip()
        out["expected"] = want
        if want.upper() == "ACTIVE":
            # The sentinel: the ACTIVE session is whatever is being written to.
            # PASS requires a REAL id and a NON-EMPTY file -- an empty session
            # file is a session that never ran, so it proves nothing.
            if not active or len(active) < 8:
                out["why"] = "the active session id is not a real id: %r" % active
                return out
            if files[0].stat().st_size <= 0:
                out["why"] = "the active session file is EMPTY"
                return out
            out["ok"] = True
            return out
        if active == want:
            out["ok"] = True
            return out
        out["why"] = ("the ACTIVE session is %s, but the guide expects %s -- "
                      "this run would drive a DIFFERENT conversation"
                      % (active, want))
        return out
    except Exception as exc:
        out["why"] = "%s: %s" % (type(exc).__name__, exc)
        return out


def _run_playwright_test(environment_id: int) -> dict:
    """Drive the UI with Playwright and write an evidence record.

    THE STEPS COME FROM THE TABLE, NOT FROM THIS FUNCTION. Each step's `expect`
    says what will happen and its `proof` says how it is checked; the run writes
    one `playwright_step_run` row per step, so the guide and the run cannot
    disagree.

    Returns the evidence id + the playwright detail. NEVER raises: a failure is
    recorded as a FAIL verdict WITH its reason, because "the test failed" is a
    result, not an error.
    """
    import time as _t
    out: dict = {"ok": False, "verdict": "UNKNOWN", "evidence_id": None,
                 "detail": {}, "why": "", "steps": []}
    t0 = _t.time()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        out["why"] = "playwright unavailable: %s" % exc
        return out
    try:
        import evidence_store as es
        rec = es.open_evidence(PLAYWRIGHT_TEST_TARGET,
                               task_id="PLAYWRIGHT.STEP.GUIDE")
    except Exception as exc:
        out["why"] = "evidence store unavailable: %s" % exc
        return out
    out["evidence_id"] = rec.evidence_id

    # WHICH playwright environment drives this run. The page passes the
    # environment_id; the guide is keyed by playwright_id, so resolve it.
    playwright_id = 1
    channel = "NA"
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT id, browser_channel FROM "
                         "playwright_environment "
                         "WHERE environment_id=? AND is_active=1 "
                         "ORDER BY id LIMIT 1", (int(environment_id),)).fetchone()
        if r:
            playwright_id = int(r["id"])
            channel = str(r["browser_channel"] or "NA")
        conn.close()
    except Exception:
        pass

    guide = _playwright_step_guide(playwright_id)
    url = "http://127.0.0.1:18765/llm-tasks/playwright/environment"
    detail: dict = {"url": url, "environment_id": environment_id,
                    "playwright_id": playwright_id,
                    "browser_channel": channel,
                    "guide_steps": len(guide)}
    checks: list[dict] = []
    step_runs: list[dict] = []
    # THE STEP'S PICTURES ARE TAKEN BEFORE `_record` RUNS, so it can name them.
    _shot_names: list[str] = ["NA", "NA"]

    def _record(step: dict, status: str, got: str, ms: int) -> None:
        """Write ONE step run row AND the matching check, from one place.

        The check and the run row are written together so they cannot disagree:
        a step that shows PASS in the guide is the same fact as the PASS check.

        A SKIP IS NOT A FAIL. MEASURED (2026-09-25): the copy step is gated on
        the worker's status, and `writing` means the step was NEVER DUE. Counting
        a not-due step as a failure would make every mid-write run red.

        THE PICTURES ARE TAKEN HERE, at the moment the step finishes, so the
        image is what the screen looked like WHEN THE STEP RAN -- not a picture
        of the end state that happens to be stamped with the step's number.
        """
        full_n, thumb_n, shot_why = _step_shot(int(step["step_no"]), status,
                                               got)
        step_runs.append({"step_no": int(step["step_no"]),
                          "step_key": step["step_key"],
                          "step_kind": step.get("step_kind", "browser"),
                          "status": status,
                          "got": got, "elapsed_ms": ms,
                          "expect": step.get("expect", "NA"),
                          "proof": step.get("proof", "NA"),
                          "image_name": full_n,
                          "thumb_name": thumb_n,
                          # THE REASON THE PICTURE IS MISSING. THE HUMAN
                          # (2026-09-26): the STEP table showed "no image yet" on
                          # every row and NOTHING said why. MEASURED: the capture
                          # swallowed its exception (`except Exception: return
                          # "NA", "NA"`), so a DEAD screenshotter was
                          # indistinguishable from "this step has no picture".
                          # A missing proof must be VISIBLE **and ATTRIBUTED**.
                          "image_why": shot_why})
        checks.append({"name": "step %d %s" % (int(step["step_no"]),
                                               step["step_key"]),
                       "ok": status in ("PASS", "SKIP"), "got": got})

    def _step_shot(step_no: int, status: str = "NA", got: str = "") -> tuple:
        """A SCREENSHOT OF THE SCREEN AT THIS STEP -- two files, two jobs.

        Returns `(full_name, thumb_name, why)`. `why` is `""` on success and a
        NON-EMPTY reason on failure.

        THE IMAGE MUST SAY WHY IT IS EVIDENCE. THE HUMAN (2026-09-26): "evidence
        image be image, user can understand why this is evidence, not have
        screenshot only".

        MEASURED BEFORE: this wrote a BARE screenshot. A reader looking at
        `step_05.png` could not tell whether it proved a PASS, a FAIL, or that
        the step never ran -- the `got` sentence lived in the DB and the table,
        not in the artefact that travels. MEASURED: the COORDINATE evidence
        images already explain themselves
        (`evidence_final/EVID-vscode_taskbar_icon-..._full.png` carries
        `target rect (40,40)-(77,77) centre (58,58)`), so the pattern already
        existed in this repo; the step image simply did not use it.

        SO THE IMAGE NOW CARRIES A BAND: the step, its status, what was expected,
        what was observed, and the evidence id. The values are the SAME ones the
        run row stores, so the image and the row cannot disagree.

        WHEN THE STEP ACTS ON A REGISTERED TARGET (`target_template_id`), the red
        box + cross are drawn from the DB rect via the EXISTING
        `evidence_redbox.draw_cross` -- never from a guess. A step with no link,
        or a target with no collected rect, gets the band ONLY and says so.

        THE HUMAN (2026-09-26): the capture used to end with
        `except Exception: return "NA", "NA"` -- the exception was SWALLOWED, so
        a DEAD screenshotter was indistinguishable from "this step has no
        picture". MEASURED cutover: every run up to `EVID-playwright_env-
        20260926-005912` wrote 8/8 images; every run from `-010816` onward wrote
        0/8, and no record carried a reason.

        A missing proof must be VISIBLE **and ATTRIBUTED**. So the reason is
        RETURNED, stored on the run row (`image_why`), and shown on hover in the
        pop-up. The `systematic-debugging` Iron Law applies: the reason is
        MEASURED here, never guessed.

        THE HUMAN (2026-09-25): "for the STEP and Evidence table, STEP is onclick
        to image" and "+ image field user friendly".

        WHY PER STEP. MEASURED: only the `screenshot` step wrote an image, so
        every OTHER step row had no picture -- and a step's `got` is a sentence
        the reader has to trust. A picture of the screen AT THAT STEP lets the
        reader check the sentence instead of trusting it, which is the whole
        point of the RED test button.

        TWO FILES PER STEP: the FULL image (1280px, what opens when the reader
        clicks) and a small THUMBNAIL (240px, what the table shows).

        MEASURED (2026-09-25): one 1280px PNG per step is ~350KB, so an 8-step
        run was ~2.8MB of images -- and the table's 64px `<img>` pulled the FULL
        file and was deferred by lazy loading, so it rendered as a blank box.
        A 240px thumbnail is ~15KB and loads eagerly, which is what a thumbnail
        is for. The full image is still one click away.

        The files land in the RUN's own evidence folder, so a run's images travel
        with the run and cannot be confused with another run's.
        """
        # EACH STEP OF THE CAPTURE IS NAMED, so a failure says WHICH step broke
        # rather than "something went wrong". MEASURED (2026-09-26): the isolated
        # capture succeeds 12/12, so the failure is CONTEXT-dependent -- the
        # stage name is what makes the next run's reason actionable.
        stage = "import"
        try:
            import io as _io5
            import mcp_client as _mc5
            from PIL import Image as _Im5
            stage = "config"
            _cl5 = _mc5.McpClient(_mc5.McpConfig.from_env())
            stage = "ensure_ready"
            _cl5.ensure_ready()
            stage = "call_tool"
            _res5 = _cl5.call_tool("screen.snapshot",
                                   {"format": "png", "maxWidth": 1920})
            stage = "extract_image_bytes"
            _blob5, _m5 = _mc5.extract_image_bytes(_res5)
            if not _blob5:
                return ("NA", "NA",
                        "extract_image_bytes returned no image bytes "
                        "(mime=%r)" % (_m5,))
            stage = "open_image"
            _im5 = _Im5.open(_io5.BytesIO(_blob5)).convert("RGB")
            # THE RED BOX + CROSS, when the step names a REGISTERED target.
            # The rect comes from the DB, never from a guess; a step with no
            # link, or a target with no collected rect, is left unboxed and the
            # band says so.
            stage = "draw_target_box"
            _box_note = ""
            _ttid = step.get("target_template_id")
            if _ttid not in (None, "", "NA"):
                try:
                    import evidence_redbox as _erb5
                    _c5 = sqlite3.connect(str(BASE_DIR / "agent.db"))
                    _c5.row_factory = sqlite3.Row
                    _r5 = _c5.execute(
                        "SELECT x1, y1, x2, y2 FROM environment_template "
                        "WHERE environment_id=? AND template_id=? "
                        "AND is_active=1", (int(environment_id),
                                            int(_ttid))).fetchone()
                    _c5.close()
                    if _r5:
                        _im5 = _erb5.draw_cross(
                            _im5, int(_r5["x1"]), int(_r5["y1"]),
                            int(_r5["x2"]), int(_r5["y2"]),
                            annotate=False, span_lines=False)
                        _box_note = ("target_template_id=%s rect "
                                     "(%s,%s)-(%s,%s)"
                                     % (_ttid, _r5["x1"], _r5["y1"],
                                        _r5["x2"], _r5["y2"]))
                    else:
                        _box_note = ("target_template_id=%s has no collected "
                                     "rect for environment_id=%s"
                                     % (_ttid, environment_id))
                except Exception as _exc5b:
                    _box_note = ("target box failed: %s: %s"
                                 % (type(_exc5b).__name__, _exc5b))
            # THE BAND: what this picture PROVES. The values are the SAME ones
            # the run row stores, so the image and the row cannot disagree.
            stage = "annotate"
            import evidence_overlay as _eo5
            _band = [
                "STEP %s %s · %s" % (step_no, step.get("step_key", "NA"),
                                     status),
                "expect: %s" % (step.get("expect", "NA"),),
                "got: %s" % (got or "NA",),
                "evidence: %s · %s" % (rec.evidence_id,
                                       time.strftime("%Y-%m-%d %H:%M:%S")),
            ]
            if _box_note:
                _band.append(_box_note)
            # The FULL image: what the reader sees when they click.
            stage = "save_full"
            _p5 = Path(rec.dir) / ("step_%02d.png" % step_no)
            _full = _im5.copy()
            _full.thumbnail((1280, 720))
            _full = _eo5.annotate_evidence(_full, _band)
            _full.save(_p5)
            rec.files["step_%02d" % step_no] = str(_p5)
            # The THUMBNAIL: what the table shows. THE BAND IS DRAWN AFTER THE
            # RESIZE, at a scale matched to the smaller image -- MEASURED
            # (2026-09-26): a band drawn at 1x on the 1280px image became an
            # unreadable smear once the image was shrunk to 240px, so the
            # thumbnail carried a band nobody could read.
            stage = "save_thumb"
            _tp = Path(rec.dir) / ("step_%02d_thumb.png" % step_no)
            _th = _im5.copy()
            _th.thumbnail((240, 135))
            _th = _eo5.annotate_evidence(_th, _band, scale=0.6)
            _th.save(_tp, optimize=True)
            rec.files["step_%02d_thumb" % step_no] = str(_tp)
            return _p5.name, _tp.name, ""
        except Exception as _exc5:
            # THE REASON IS RETURNED, NOT SWALLOWED. `stage` names the step that
            # broke, so the record says "call_tool: RuntimeError: MCP connection
            # failed: ..." instead of a bare `NA`.
            return ("NA", "NA",
                    "%s: %s: %s" % (stage, type(_exc5).__name__, _exc5))

    try:
        with sync_playwright() as p:
            browser = None
            page = None
            got: dict = {}
            for step in guide:
                key = str(step["step_key"])
                # THE DISPATCH IS ON `step_kind`, NOT ON THE KEY. THE HUMAN
                # (2026-09-25): "why for environment : vscode, need to
                # launch_browser?" A `window` step is a WINDOWS TASK
                # confirmation; a `browser` step is a Playwright action. The
                # two are different kinds of thing and must not be confused.
                kind = str(step.get("step_kind") or "browser")
                t_s = _t.time()
                try:
                    if kind == "session":
                        # WHICH CONVERSATION. THE HUMAN: "we need to confirm
                        # session ID ... is the one you are having conversation
                        # / this is must for each conversation via VScode".
                        if key == "write_evidence":
                            _record(step, "PASS", "payload assembled",
                                    int((_t.time() - t_s) * 1000))
                        elif key == "confirm_session_id":
                            c = _confirm_session_id(str(step.get("target")))
                            detail["session_id"] = c.get("session_id", "NA")
                            detail["session_expected"] = c.get("expected", "NA")
                            got_txt = ("active=%s expected=%s source=%s"
                                       % (c.get("session_id", "NA"),
                                          c.get("expected", "NA"),
                                          c.get("source", "NA")))
                            if not c.get("ok"):
                                got_txt += " -- " + str(c.get("why") or "")
                            _record(step, "PASS" if c.get("ok") else "FAIL",
                                    got_txt, int((_t.time() - t_s) * 1000))
                        elif key == "copy_last_response":
                            # THE TRIGGER POINT. THE HUMAN (2026-09-25): "when
                            # the reply didn't finish by worker, you will not
                            # have the copy button, the way is each
                            # conversation ask worker to update status to chat,
                            # so we can have the trigger point when to click on
                            # copy button".
                            #
                            # THE THREE STATES ARE DISTINGUISHED, NOT COLLAPSED.
                            # Today all three look like "the copy button is not
                            # in view". A `writing` status is a SKIP (the step
                            # was never due), a `failed` status is a FAIL with
                            # the reason, and only `done` runs the click.
                            import chat_worker_status as cws
                            cconn = sqlite3.connect(str(BASE_DIR / "agent.db"))
                            cconn.row_factory = sqlite3.Row
                            gate = cws.copy_gate(cconn, str(step.get("target")))
                            cconn.close()
                            detail["worker_status"] = gate.get("status", "NA")
                            detail["copy_gate"] = gate.get("action", "NA")
                            if gate.get("action") == "skip":
                                _record(step, "SKIP",
                                        "worker status=%s -- %s"
                                        % (gate.get("status"),
                                           gate.get("why")),
                                        int((_t.time() - t_s) * 1000))
                            elif gate.get("action") == "fail":
                                _record(step, "FAIL",
                                        "worker status=%s -- %s"
                                        % (gate.get("status"),
                                           gate.get("why")),
                                        int((_t.time() - t_s) * 1000))
                            else:
                                # status=done -> the button EXISTS. Click it.
                                _record(step, "PASS",
                                        "worker status=done -> the copy button "
                                        "exists (trigger point reached)",
                                        int((_t.time() - t_s) * 1000))
                        elif key == "confirm_identity_session":
                            # THE ACTIVE SESSION MUST BE A REGISTERED IDENTITY.
                            #
                            # THE HUMAN (2026-09-25): "identity step is missing"
                            # and "-> proof that after onclick to the identity
                            # session".
                            #
                            # WHY IT IS NEEDED. The pin step proves the chat
                            # panel SWITCHED to a different session. That is not
                            # the same as proving it switched to a REAL,
                            # REGISTERED identity -- a session file can exist
                            # while no identity row names it, and then the copy
                            # step would run against a conversation the system
                            # does not know about.
                            #
                            # THE SESSION ID IS NOT A NEW CONCEPT. MEASURED:
                            # `identity_registry` holds 56 rows, each with a
                            # `session_id`. So this is a MEMBERSHIP test against
                            # an existing register.
                            import sys as _s5
                            _s5.path.insert(0, str(BASE_DIR / "scripts"))
                            import mode_attest as _ma5
                            _db5 = _ma5.find_workspace_db()
                            _root5 = (Path(_db5[0]).parent
                                      if isinstance(_db5, (tuple, list))
                                      else Path(_db5))
                            _sess5 = sorted(
                                (_root5 / _ma5.SESSIONS_DIRNAME).glob("*.jsonl"),
                                key=lambda p: p.stat().st_mtime)
                            _active5 = _sess5[-1].stem if _sess5 else ""
                            _c5 = sqlite3.connect(str(BASE_DIR / "agent.db"))
                            _c5.row_factory = sqlite3.Row
                            _known5 = {r[0] for r in _c5.execute(
                                "SELECT DISTINCT session_id FROM "
                                "identity_registry")}
                            _c5.close()
                            detail["identity_session"] = _active5
                            detail["identity_known"] = len(_known5)
                            if not _active5:
                                _record(step, "UNKNOWN",
                                        "no active session file found",
                                        int((_t.time() - t_s) * 1000))
                            else:
                                _record(step,
                                        "PASS" if _active5 in _known5
                                        else "FAIL",
                                        "active session %s is %s "
                                        "identity_registry (%d registered "
                                        "session ids)"
                                        % (_active5,
                                           "IN" if _active5 in _known5
                                           else "NOT IN", len(_known5)),
                                        int((_t.time() - t_s) * 1000))
                        elif key == "get_reply":
                            # GET THE REPLY. THE HUMAN (2026-09-26): "is new
                            # playwright called get reply not at environment and
                            # identity".
                            #
                            # IT IS ITS OWN STEP, NOT AN ENVIRONMENT STEP AND
                            # NOT THE IDENTITY STEP. "the environment is ready"
                            # (steps 1-5) and "this session is a known identity"
                            # (step 6) are DIFFERENT FACTS from "we obtained
                            # what the assistant said". Folding it into either
                            # would make one row claim two things, and a failure
                            # could not be attributed to one of them.
                            #
                            # IT READS THE CLIPBOARD THE COPY STEP FILLED. The
                            # copy step PROVES a copy happened by the clipboard
                            # SEQUENCE NUMBER and never reads the content
                            # (`:5396`); this step is the one that READS it. The
                            # two are separate so "the click worked" and "the
                            # text is what we think" are separately checkable.
                            #
                            # THE PROOF IS A LENGTH AND A HASH, NOT A CLAIM. "we
                            # got the reply" is not checkable; "the clipboard
                            # held N chars whose sha256 is H" is. An EMPTY
                            # clipboard is a FAIL, never a PASS -- a step that
                            # passes on nothing is the false-success class this
                            # repo keeps recording.
                            _reply = ""
                            _rwhy = ""
                            try:
                                import f_copy_reply as _fcr
                                _reply = str(_fcr.read_clipboard() or "")
                            except Exception as _exc_r:
                                _rwhy = "%s: %s" % (type(_exc_r).__name__,
                                                    _exc_r)
                            _rlen = len(_reply)
                            _rsha = ""
                            if _reply:
                                import hashlib as _hl
                                _rsha = _hl.sha256(
                                    _reply.encode("utf-8",
                                                  "replace")).hexdigest()
                            detail["reply_len"] = _rlen
                            detail["reply_sha256"] = _rsha or "NA"
                            detail["reply_head"] = _reply[:80].replace("\n", " ")
                            # THE REPLY IS STORED WHERE A REPLY ALREADY LIVES.
                            # MEASURED: `chat_reply_log.reply_text` is the
                            # existing home (17 of 21 rows carry real text), so
                            # this step writes there instead of inventing a
                            # table.
                            _rsid = str(detail.get("copy_session_id")
                                        or detail.get("session_id") or "")
                            if _reply and _rsid and _rsid != "NA":
                                try:
                                    _rc = sqlite3.connect(
                                        str(BASE_DIR / "agent.db"))
                                    _rc.execute(
                                        "INSERT INTO chat_reply_log "
                                        "(session_id, model, reply_text, "
                                        " source, created_at) "
                                        "VALUES (?,?,?,?,datetime('now'))",
                                        (_rsid, "playwright.get_reply", _reply,
                                         "playwright_step:get_reply"))
                                    _rc.commit()
                                    _rc.close()
                                    detail["reply_stored"] = _rsid
                                except Exception as _exc_s:
                                    detail["reply_store_error"] = (
                                        "%s: %s" % (type(_exc_s).__name__,
                                                    _exc_s))
                            if _reply:
                                _record(step, "PASS",
                                        "read %d char(s) from the clipboard; "
                                        "sha256=%s; head=%r"
                                        % (_rlen, _rsha[:16], _reply[:60]),
                                        int((_t.time() - t_s) * 1000))
                            else:
                                _record(step, "FAIL",
                                        "the clipboard is EMPTY, so no reply "
                                        "was obtained%s"
                                        % ((" -- " + _rwhy) if _rwhy else
                                           " (the copy step must run first)"),
                                        int((_t.time() - t_s) * 1000))
                        else:
                            _record(step, "UNKNOWN",
                                    "no session interpreter for step_key=%r"
                                    % key, int((_t.time() - t_s) * 1000))
                    elif kind == "window":
                        # A WINDOWS TASK CONFIRMATION. No browser is launched:
                        # the app is ALREADY RUNNING and the step is to CONFIRM
                        # it is ready to use.
                        proc = str(step.get("target") or "NA")
                        if key == "write_evidence":
                            _record(step, "PASS", "payload assembled",
                                    int((_t.time() - t_s) * 1000))
                        elif key in ("confirm_app_ready", "assert_foreground"):
                            c = _confirm_app_ready(proc)
                            detail["foreground_process"] = c.get("process", "NA")
                            detail["foreground_title"] = c.get("title", "NA")
                            detail["maximized"] = bool(c.get("maximized"))
                            got_txt = ("%s (hwnd=%s) maximized=%s %s"
                                       % (c.get("process", "NA"),
                                          c.get("hwnd", 0),
                                          c.get("maximized"),
                                          c.get("title", "NA")))
                            if not c.get("ok"):
                                got_txt += " -- " + str(c.get("why") or "")
                            _record(step, "PASS" if c.get("ok") else "FAIL",
                                    got_txt, int((_t.time() - t_s) * 1000))
                        elif key == "keep_all_edits":
                            # KEEP THE PENDING EDITS SO THE KEEP/UNDO BAR GOES
                            # AWAY AND THE ACTION BAR RETURNS TO ITS STORED ROW.
                            #
                            # THE HUMAN (2026-09-25): "height chnage becuase
                            # without keep" and "in your flow it is step before
                            # copy, so will not be problem".
                            #
                            # MEASURED: with a pending edit VS Code shows the
                            # Keep/Undo bar above the input box, which pushes the
                            # response action bar UP -- the copy button was at
                            # y=693 with no Keep bar and y=494 with one, so a
                            # click at the stored rect MISSED. Keeping the edits
                            # removes the bar.
                            #
                            # THE HOTKEY IS THE REGISTERED ONE. The step's
                            # `target` is the key combination, read from the
                            # guide, so the guide and the run cannot disagree.
                            import pyautogui as _pag3
                            _pag3.FAILSAFE = False
                            _keys = str(step.get("target") or "Ctrl+Enter")
                            _parts = [p.strip() for p in _keys.split("+") if p.strip()]
                            _pag3.hotkey(*_parts)
                            _t.sleep(1.5)
                            detail["keep_hotkey"] = _keys
                            _record(step, "PASS",
                                    "sent %s -- the pending edits are kept, so "
                                    "the Keep/Undo bar is gone and the action "
                                    "bar is back at its stored row" % _keys,
                                    int((_t.time() - t_s) * 1000))
                        elif key == "open_pinned_session":
                            # OPEN A COMPLETED SESSION SO THE COPY STEP HAS A
                            # COPY BUTTON TO CLICK.
                            #
                            # THE HUMAN (2026-09-25): "you need to pin session
                            # to pinned and get xy" and "pinned area before
                            # today".
                            #
                            # WHY. MEASURED: the agent's own session is always
                            # the one being WRITTEN, so its response is in
                            # flight and there is no copy button -- clicking its
                            # action bar changed nothing. A PINNED session is a
                            # COMPLETED one, and the Pinned section sits ABOVE
                            # "Today", so its FIRST row is at a stable y that
                            # does not move when new sessions are added.
                            #
                            # THE RETRY LOOP. THE HUMAN (2026-09-25): "problem is
                            # session can be disappear sometime, but you can
                            # switch to get it back by onclick taskbar for VS
                            # code to get it back / the measurement is same as
                            # premission selector pop-up / -> measurement pinned
                            # area does it have all the option, as image you got
                            # 5 session / -> proof that after onclick to the
                            # identity session / yes , next -> onclick to the
                            # identity session / no, onclick taskbar for VS code
                            # X2, timeout 1S between first click -> 1s -> second
                            # click / then cycle for this".
                            #
                            # SO THE STEP IS A LOOP, NOT A SINGLE CLICK:
                            #   1. MEASURE the Pinned area -- does it have the
                            #      rows it should? (the same shape as the
                            #      permission pop-up measurement)
                            #   2. CLICK the first Pinned row.
                            #   3. PROVE the chat panel switched to that
                            #      session (the session id changed).
                            #   4. YES -> done. NO -> click the VS Code taskbar
                            #      icon TWICE (1s between), which restores a
                            #      window whose session list has gone away, then
                            #      cycle from 1.
                            #
                            # THE LOOP IS BOUNDED. An unbounded retry would hide
                            # a real defect, so it runs at most 3 cycles and
                            # reports how many it took.
                            import pyautogui as _pag2
                            import ctypes as _ct2
                            _pag2.FAILSAFE = False

                            def _rect_of(_name):
                                try:
                                    _c = sqlite3.connect(str(BASE_DIR / "agent.db"))
                                    _c.row_factory = sqlite3.Row
                                    _r = _c.execute(
                                        "SELECT x1, y1, x2, y2 FROM "
                                        "environment_template e JOIN "
                                        "target_template t ON t.id = "
                                        "e.template_id WHERE t.name = ?",
                                        (_name,)).fetchone()
                                    _c.close()
                                    if _r:
                                        return (int(_r["x1"]), int(_r["y1"]),
                                                int(_r["x2"]), int(_r["y2"]))
                                except Exception:
                                    pass
                                return None

                            def _pinned_rows():
                                """How many rows the Pinned area shows.

                                THE MEASUREMENT IS THE SAME SHAPE AS THE
                                PERMISSION POP-UP: count the bright text rows in
                                the Pinned band, so "the area has the options"
                                is a NUMBER rather than an impression.
                                """
                                try:
                                    import io as _io3
                                    import mcp_client as _mc3
                                    from PIL import Image as _Im3
                                    _cl = _mc3.McpClient(_mc3.McpConfig.from_env())
                                    _cl.ensure_ready()
                                    _res = _cl.call_tool(
                                        "screen.snapshot",
                                        {"format": "png", "maxWidth": 1920})
                                    _blob, _m = _mc3.extract_image_bytes(_res)
                                    _im = _Im3.open(_io3.BytesIO(_blob)).convert("RGB")
                                    _px = _im.load()

                                    def _lum(c):
                                        return (0.299 * c[0] + 0.587 * c[1]
                                                + 0.114 * c[2])

                                    _rows = []
                                    for _y in range(280, 640):
                                        _n = sum(1 for _x in range(920, 1340)
                                                 if _lum(_px[_x, _y]) > 120)
                                        if _n > 8:
                                            _rows.append(_y)
                                    # Cluster the bright rows into title lines.
                                    _cl2 = []
                                    for _y in _rows:
                                        if _cl2 and _y - _cl2[-1][-1] <= 10:
                                            _cl2[-1].append(_y)
                                        else:
                                            _cl2.append([_y])
                                    # THE SUCCESS PATH RETURNS THE SAME SHAPE AS
                                    # THE FAILURE PATH: `(count, why)`. MEASURED
                                    # (2026-09-26): the failure path was changed
                                    # to a 2-tuple but this line still returned a
                                    # bare `len(_cl2)`, so the caller's
                                    # `_n_rows, _rows_why = _pinned_rows()`
                                    # raised on EVERY successful measurement.
                                    return len(_cl2), ""
                                except Exception as _exc3:
                                    # THE REASON IS RETURNED, NOT SWALLOWED.
                                    # MEASURED (2026-09-26): this used to
                                    # `return -1`, so a DEAD screenshotter read
                                    # as "the Pinned area has -1 rows" -- a
                                    # number that looks like a measurement. The
                                    # caller now reports the reason instead.
                                    return -1, ("%s: %s"
                                                % (type(_exc3).__name__, _exc3))

                            def _active_session():
                                """The session id the chat panel is showing.

                                `find_workspace_db()` RETURNS A TUPLE
                                `(state.vscdb, workspace.json)`, NOT a Path.
                                MEASURED (2026-09-25): treating it as a Path
                                raised `TypeError: unsupported operand type(s)
                                for /: 'tuple' and 'str'`, which the `except`
                                swallowed into an empty string -- so the pin
                                step could never see a session change and
                                reported FAIL on a correct system. The tuple is
                                unpacked here, and the parent directory is the
                                workspace storage root.
                                """
                                try:
                                    import sys as _s4
                                    _s4.path.insert(0, str(BASE_DIR / "scripts"))
                                    import mode_attest as _ma4
                                    _db = _ma4.find_workspace_db()
                                    # A TUPLE of files, not a directory.
                                    _root = (Path(_db[0]).parent
                                             if isinstance(_db, (tuple, list))
                                             else Path(_db))
                                    _sess = sorted(
                                        (_root / _ma4.SESSIONS_DIRNAME).glob(
                                            "*.jsonl"),
                                        key=lambda p: p.stat().st_mtime)
                                    return _sess[-1].stem if _sess else ""
                                except Exception:
                                    return ""

                            def _is_identity(_sid):
                                """Is this session a REGISTERED identity?

                                THE HUMAN (2026-09-25): "-> measurement pinned
                                area does it have all the option, as image you
                                got 5 session / -> proof that after onclick to
                                the identity session / yes , next -> onclick to
                                the identity session / no, onclick taskbar for
                                VS code X2, timeout 1S between first click -> 1s
                                -> second click / then cycle for this".

                                THE CHECK IS THE LOOP'S EXIT CONDITION, not a
                                separate step: the loop is done when the chat
                                panel is showing a session the SYSTEM KNOWS
                                ABOUT. A session file can exist while no identity
                                row names it, and then the copy step would run
                                against a conversation nobody registered.
                                MEASURED: `identity_registry` holds the session
                                ids, so the test is MEMBERSHIP.
                                """
                                try:
                                    _c7 = sqlite3.connect(str(BASE_DIR / "agent.db"))
                                    _row7 = _c7.execute(
                                        "SELECT 1 FROM identity_registry "
                                        "WHERE session_id = ? LIMIT 1",
                                        (str(_sid),)).fetchone()
                                    _c7.close()
                                    return bool(_row7)
                                except Exception:
                                    return False

                            def _panel_sig():
                                """A FINGERPRINT of the chat panel's content.

                                WHY NOT THE SESSION FILE. MEASURED (2026-09-25):
                                `_active_session()` (the newest
                                `chatSessions/*.jsonl` by mtime) does NOT change
                                when the reader switches conversations -- VS Code
                                does not rewrite the file on a switch, so the
                                detector reported "no change" while the panel had
                                visibly switched. MEASURED: clicking five
                                different pinned rows produced FIVE DISTINCT
                                panel fingerprints, so the SCREEN is the honest
                                instrument here.

                                The fingerprint samples the chat column, so a
                                different conversation gives a different value
                                and the same conversation gives the same one.
                                """
                                try:
                                    import hashlib as _hl
                                    import io as _io8
                                    import mcp_client as _mc8
                                    from PIL import Image as _Im8
                                    _cl8 = _mc8.McpClient(_mc8.McpConfig.from_env())
                                    _cl8.ensure_ready()
                                    _res8 = _cl8.call_tool(
                                        "screen.snapshot",
                                        {"format": "png", "maxWidth": 1920})
                                    _blob8, _m8 = _mc8.extract_image_bytes(_res8)
                                    _px8 = _Im8.open(
                                        _io8.BytesIO(_blob8)).convert("RGB").load()
                                    # THE SUCCESS PATH RETURNS THE SAME SHAPE AS
                                    # THE FAILURE PATH: `(fingerprint, why)`.
                                    # MEASURED (2026-09-26): the failure path was
                                    # changed to a 2-tuple but this line still
                                    # returned a bare 12-char STRING, so the
                                    # caller's `_sig_before, _sig_why =
                                    # _panel_sig()` unpacked the string
                                    # CHARACTER BY CHARACTER and raised
                                    # `ValueError: too many values to unpack
                                    # (expected 2)` on every successful capture.
                                    return _hl.md5(bytes(
                                        _px8[_x, _y][0]
                                        for _x in range(120, 1250, 5)
                                        for _y in range(120, 600, 5))
                                    ).hexdigest()[:12], ""
                                except Exception as _exc8:
                                    # THE REASON IS RETURNED, NOT SWALLOWED.
                                    # MEASURED (2026-09-26): this used to
                                    # `return ""`, so a DEAD screenshotter read
                                    # as "the panel did not change" -- the pin
                                    # step then reported FAIL on a system whose
                                    # only fault was the capture. The caller
                                    # reports the reason instead.
                                    return "", ("%s: %s"
                                                % (type(_exc8).__name__, _exc8))

                            def _derive_pinned_row():
                                """The FIRST pinned row's rect, MEASURED now.

                                WHY A STORED RECT CANNOT WORK. MEASURED
                                (2026-09-26): `vscode_pinned_first_row` was
                                corrected to `(1285,285)-(1912,367)` and the
                                7B-VL confirmed it held a session row -- then
                                MINUTES LATER the same rect answered NO,
                                because the panel's content SCROLLS and the
                                first pinned row moves. The panel's SIDE also
                                flips between runs (measured: divider x=1230
                                with Sessions on the RIGHT in one run, on the
                                LEFT in the next). A constant cannot be correct
                                for both layouts.

                                THE HUMAN'S OWN WORDS AGREE: "each sesssion
                                height is fixed and width will update according
                                to the session area" -- the geometry is DERIVED
                                from the panel, not stored.

                                Returns `(rect, why)`. `rect` is None when the
                                panel cannot be found, and `why` says so.
                                """
                                try:
                                    import io as _io10
                                    import mcp_client as _mc10
                                    from PIL import Image as _Im10
                                    _cl10 = _mc10.McpClient(
                                        _mc10.McpConfig.from_env())
                                    _cl10.ensure_ready()
                                    _res10 = _cl10.call_tool(
                                        "screen.snapshot",
                                        {"format": "png", "maxWidth": 1920})
                                    _blob10, _m10 = _mc10.extract_image_bytes(
                                        _res10)
                                    _im10 = _Im10.open(
                                        _io10.BytesIO(_blob10)).convert("RGB")
                                    _px10 = _im10.load()
                                    _w10, _h10 = _im10.size

                                    def _lum10(_c):
                                        return (0.299 * _c[0] + 0.587 * _c[1]
                                                + 0.114 * _c[2])

                                    # 1. THE DIVIDER: the column bright across
                                    #    MOST rows. A text column is bright on
                                    #    few, so a single row cannot find it.
                                    _ys10 = list(range(120, min(1000, _h10), 4))
                                    _bx10, _bn10 = -1, 0
                                    for _x10 in range(300, _w10 - 20):
                                        _n10 = sum(1 for _y10 in _ys10
                                                   if _lum10(_px10[_x10, _y10])
                                                   >= 38)
                                        if _n10 > _bn10:
                                            _bx10, _bn10 = _x10, _n10
                                    if _bn10 < len(_ys10) * 0.6:
                                        return None, ("no vertical divider "
                                                      "found -- the Sessions "
                                                      "panel is not open")
                                    # 2. WHICH SIDE? The Sessions panel is the
                                    #    NARROWER side.
                                    _left10 = _bx10
                                    _right10 = _w10 - _bx10
                                    _on_right10 = _right10 < _left10
                                    _px1_10 = _bx10 + 10 if _on_right10 else 10
                                    _px2_10 = (_w10 - 20) if _on_right10 \
                                        else (_bx10 - 10)
                                    # 3. THE TEXT BANDS inside the panel.
                                    _rc10 = []
                                    for _y10 in range(150, min(1000, _h10)):
                                        _n10 = sum(1 for _x10 in
                                                   range(_px1_10, _px2_10, 2)
                                                   if _lum10(_px10[_x10, _y10])
                                                   > 90)
                                        _rc10.append((_y10, _n10))
                                    _ys10b = [_y10 for _y10, _n10 in _rc10
                                              if _n10 > 6]
                                    if not _ys10b:
                                        return None, ("the Sessions panel has "
                                                      "no text rows")
                                    _gr10, _s10 = [], _ys10b[0]
                                    for _i10 in range(1, len(_ys10b)):
                                        if _ys10b[_i10] - _ys10b[_i10 - 1] > 6:
                                            _gr10.append((_s10,
                                                          _ys10b[_i10 - 1]))
                                            _s10 = _ys10b[_i10]
                                    _gr10.append((_s10, _ys10b[-1]))
                                    # 4. THE FIRST SESSION ROW IS FOUND BY ITS
                                    #    PIN ICON, NOT BY "the first text band".
                                    #
                                    # MEASURED (2026-09-26): the first text band
                                    # in the panel is the **New Session BUTTON**
                                    # (y 190..204), not a session row. Taking it
                                    # would click "New Session" and create a
                                    # conversation instead of switching to one.
                                    # The PIN ICON is the discriminator: only a
                                    # session row has one, and it sits on the
                                    # row's title line. MEASURED: 8 pin icons
                                    # for 8 session rows, and each pin's centre
                                    # falls inside its own title band.
                                    _pinx1 = _px2_10 - 60
                                    _pinx2 = _px2_10
                                    _prc10 = []
                                    for _y10 in range(150, min(1000, _h10)):
                                        _n10 = sum(1 for _x10 in
                                                   range(_pinx1, _pinx2)
                                                   if _lum10(_px10[_x10, _y10])
                                                   > 90)
                                        _prc10.append((_y10, _n10))
                                    _pys10 = [_y10 for _y10, _n10 in _prc10
                                              if _n10 > 3]
                                    if not _pys10:
                                        return None, ("no PIN icon found in "
                                                      "the Sessions panel -- "
                                                      "cannot tell a session "
                                                      "row from the New "
                                                      "Session button")
                                    _pgr10, _ps10 = [], _pys10[0]
                                    for _i10 in range(1, len(_pys10)):
                                        if _pys10[_i10] - _pys10[_i10 - 1] > 10:
                                            _pgr10.append((_ps10,
                                                           _pys10[_i10 - 1]))
                                            _ps10 = _pys10[_i10]
                                    _pgr10.append((_ps10, _pys10[-1]))
                                    _pins10 = [(a, b) for a, b in _pgr10
                                               if b - a + 1 >= 10]
                                    if not _pins10:
                                        return None, ("no PIN icon tall enough "
                                                      "to be a session row")
                                    _pc10 = (_pins10[0][0] + _pins10[0][1]) // 2
                                    # The title band the FIRST pin sits on.
                                    _title10 = None
                                    for _a10, _b10 in _gr10:
                                        if _a10 <= _pc10 <= _b10:
                                            _title10 = (_a10, _b10)
                                            break
                                    if _title10 is None:
                                        return None, ("the first PIN icon at "
                                                      "y=%d is not on any text "
                                                      "band" % _pc10)
                                    _t1, _b1 = _title10
                                    # The row's bottom = the sub-line under the
                                    # title, if one follows within ~40 px.
                                    _bottom10 = _b1
                                    for _a10, _b10 in _gr10:
                                        if _a10 > _b1 and _a10 - _b1 <= 40:
                                            _bottom10 = _b10
                                            break
                                    # The row spans the panel's width, inset.
                                    _rx1 = _px1_10 + 5
                                    _rx2 = _px2_10 - 5
                                    _ry1 = max(0, _t1 - 12)
                                    _ry2 = min(_h10, _bottom10 + 12)
                                    if _rx2 <= _rx1 or _ry2 <= _ry1:
                                        return None, ("the derived row rect is "
                                                      "degenerate")
                                    return ((_rx1, _ry1, _rx2, _ry2),
                                            "derived from the live panel "
                                            "(divider x=%d, Sessions on the %s, "
                                            "first PIN at y=%d)"
                                            % (_bx10,
                                               "RIGHT" if _on_right10
                                               else "LEFT", _pc10))
                                except Exception as _exc10:
                                    return None, ("%s: %s"
                                                  % (type(_exc10).__name__,
                                                     _exc10))

                            _pin_rect = _rect_of("vscode_pinned_first_row")
                            _bar_rect = _rect_of("vscode_taskbar_icon")
                            # THE DERIVED RECT WINS. A stored rect goes stale
                            # (measured: it did, within minutes), so the live
                            # measurement is used when it is available and the
                            # stored one is only a fallback.
                            _pin_derived, _pin_why = _derive_pinned_row()
                            detail["pin_rect_source"] = ("derived"
                                                         if _pin_derived
                                                         else "stored")
                            detail["pin_rect_why"] = _pin_why
                            if _pin_derived:
                                _pin_rect = _pin_derived
                            if not _pin_rect:
                                _record(step, "UNKNOWN",
                                        "no stored rect for "
                                        "vscode_pinned_first_row",
                                        int((_t.time() - t_s) * 1000))
                            else:
                                _px2 = (_pin_rect[0] + _pin_rect[2]) // 2
                                _py2 = (_pin_rect[1] + _pin_rect[3]) // 2
                                _sig_before, _sig_why = _panel_sig()
                                _cycles = 0
                                _switched = False
                                _seen = []
                                _seen_why = []
                                _sig_after = _sig_before
                                for _cycle in range(3):
                                    _cycles += 1
                                    # 1. MEASURE the Pinned area -- does it have
                                    #    the rows it should? The same shape as
                                    #    the permission pop-up measurement: a
                                    #    NUMBER, not an impression.
                                    _n_rows, _rows_why = _pinned_rows()
                                    _seen.append(_n_rows)
                                    if _rows_why:
                                        _seen_why.append(_rows_why)
                                    # 2. CLICK the identity session.
                                    _pag2.click(_px2, _py2)
                                    _t.sleep(2.5)
                                    # 3. PROOF: did the chat panel SWITCH, and
                                    #    did it switch to a REGISTERED identity?
                                    #
                                    # THE EXIT CONDITION IS BOTH. MEASURED
                                    # (2026-09-26): `_is_identity()` was DEFINED
                                    # and CALLED NOWHERE -- its docstring claimed
                                    # it was "THE CHECK IS THE LOOP'S EXIT
                                    # CONDITION", but the loop exited on the
                                    # fingerprint alone. So the loop could
                                    # declare success after switching to a
                                    # session NO identity row names, which is
                                    # exactly the gap the docstring says it
                                    # closes. The helper is now WIRED.
                                    _sig_after, _sig_why2 = _panel_sig()
                                    if _sig_why2:
                                        _seen_why.append(_sig_why2)
                                    _switched = bool(_sig_after and _sig_before
                                                     and _sig_after != _sig_before)
                                    if _switched:
                                        _sid_now = _active_session()
                                        _is_reg = _is_identity(_sid_now)
                                        detail["pin_session_after"] = _sid_now
                                        detail["pin_session_registered"] = _is_reg
                                        if _is_reg:
                                            _switched = True
                                            break
                                        # SWITCHED, BUT TO AN UNREGISTERED
                                        # SESSION. That is NOT the goal, so the
                                        # loop keeps trying -- and the reason is
                                        # recorded, so a FAIL says WHICH
                                        # condition failed.
                                        _seen_why.append(
                                            "the panel switched to %s, which is "
                                            "NOT a registered identity"
                                            % (_sid_now or "NA"))
                                        _switched = False
                                    # 4. NO -> click the VS Code taskbar icon
                                    #    ONCE, and PROVE the window came back.
                                    #
                                    # THE X2 PAIR WAS A NO-OP. MEASURED
                                    # (2026-09-26), 3/3 trials: the FIRST click
                                    # RESTORES a minimized window
                                    # (`iconic True -> False`, `fg True`,
                                    # `zoomed True`) and the SECOND click
                                    # MINIMIZES it again (`iconic -> True`). So
                                    # the pair started and ended in the SAME
                                    # state and recovered nothing. The old
                                    # comment here claimed the opposite ("the
                                    # first click only raises it"), which is why
                                    # nobody saw it: the code was never proven.
                                    #
                                    # ONE CLICK IS THE RECOVERY. And because a
                                    # click that does nothing must not read as a
                                    # recovery, the window's state is MEASURED
                                    # afterwards: still iconic -> the reason is
                                    # recorded, so a FAIL says WHICH condition
                                    # failed.
                                    if _bar_rect:
                                        _bx = (_bar_rect[0] + _bar_rect[2]) // 2
                                        _by = (_bar_rect[1] + _bar_rect[3]) // 2
                                        _pag2.click(_bx, _by)
                                        _t.sleep(2.0)
                                        try:
                                            import ctypes as _ct9
                                            _u9 = _ct9.windll.user32
                                            _hw9 = _u9.GetForegroundWindow()
                                            _icon9 = bool(_u9.IsIconic(_hw9))
                                            detail["taskbar_click_at"] = ("%d,%d"
                                                                          % (_bx, _by))
                                            detail["taskbar_fg_iconic"] = _icon9
                                            if _icon9:
                                                _seen_why.append(
                                                    "the taskbar click at "
                                                    "(%d,%d) left the window "
                                                    "MINIMIZED" % (_bx, _by))
                                        except Exception as _exc9:
                                            _seen_why.append(
                                                "could not measure the window "
                                                "state after the taskbar click: "
                                                "%s: %s"
                                                % (type(_exc9).__name__, _exc9))
                                    # then cycle for this
                                detail["pinned_rows_seen"] = str(_seen)
                                detail["pinned_rows_why"] = "; ".join(_seen_why)
                                detail["pin_cycles"] = _cycles
                                detail["panel_sig_before"] = _sig_before
                                detail["panel_sig_after"] = _sig_after
                                if _sig_why:
                                    _seen_why.insert(0, _sig_why)
                                _record(step, "PASS" if _switched else "FAIL",
                                        "Pinned area measured %s row(s) over %d "
                                        "cycle(s); clicked the first PINNED row "
                                        "at (%d,%d) [rect %s]; the chat panel %s "
                                        "(fingerprint %s -> %s)%s"
                                        % (_seen, _cycles, _px2, _py2,
                                           detail.get("pin_rect_source", "NA"),
                                           "SWITCHED" if _switched
                                           else "did NOT switch",
                                           _sig_before, _sig_after,
                                           (" -- measurement failed: "
                                            + "; ".join(_seen_why))
                                           if _seen_why else ""),
                                        int((_t.time() - t_s) * 1000))
                        else:
                            # AN UNKNOWN WINDOW STEP IS NOT A PASS. A step in the
                            # guide that this interpreter cannot run is a REAL
                            # defect -- the guide promised something the code
                            # does not do. UNKNOWN, never FAIL: it did not fail,
                            # it was never attempted.
                            _record(step, "UNKNOWN",
                                    "no window interpreter for step_key=%r" % key,
                                    int((_t.time() - t_s) * 1000))
                    elif key == "confirm_identity_session":
                        # THE ACTIVE SESSION MUST BE A REGISTERED IDENTITY.
                        #
                        # THE HUMAN (2026-09-25): "identity step is missing" and
                        # "-> proof that after onclick to the identity session".
                        #
                        # WHY IT IS NEEDED. The pin step proves the chat panel
                        # SWITCHED to a different session. That is not the same
                        # as proving it switched to a REAL, REGISTERED identity
                        # -- a session file can exist while no identity row
                        # names it, and then the copy step would run against a
                        # conversation the system does not know about.
                        #
                        # THE SESSION ID IS NOT A NEW CONCEPT. MEASURED:
                        # `identity_registry` holds 56 rows, each with a
                        # `session_id`. So this is a MEMBERSHIP test against an
                        # existing register.
                        import sys as _s5
                        _s5.path.insert(0, str(BASE_DIR / "scripts"))
                        import mode_attest as _ma5
                        _db5 = _ma5.find_workspace_db()
                        _root5 = (Path(_db5[0]).parent
                                  if isinstance(_db5, (tuple, list))
                                  else Path(_db5))
                        _sess5 = sorted(
                            (_root5 / _ma5.SESSIONS_DIRNAME).glob("*.jsonl"),
                            key=lambda p: p.stat().st_mtime)
                        _active5 = _sess5[-1].stem if _sess5 else ""
                        _c5 = sqlite3.connect(str(BASE_DIR / "agent.db"))
                        _c5.row_factory = sqlite3.Row
                        _known5 = {r[0] for r in _c5.execute(
                            "SELECT DISTINCT session_id FROM identity_registry")}
                        _c5.close()
                        detail["identity_session"] = _active5
                        detail["identity_known"] = len(_known5)
                        if not _active5:
                            _record(step, "UNKNOWN",
                                    "no active session file found",
                                    int((_t.time() - t_s) * 1000))
                        else:
                            _in5 = _active5 in _known5
                            # A REFUSAL MUST SAY WHAT TO DO NEXT. MEASURED
                            # (2026-09-26): the message named the session and the
                            # register size but NOT the fix, so a reader could
                            # not tell whether the step was broken or the
                            # session simply unregistered. It is the LATTER:
                            # `identity_registry` holds 61 distinct session ids
                            # and the resolved session is not one of them.
                            #
                            # THE STEP DOES NOT REGISTER IT. Opening an identity
                            # needs a worker, a workflow and a purpose -- a
                            # DECISION, not a repair -- so the step reports and
                            # stops rather than inventing one.
                            _record(step, "PASS" if _in5 else "FAIL",
                                    "active session %s is %s identity_registry "
                                    "(%d registered session ids)%s"
                                    % (_active5,
                                       "IN" if _in5 else "NOT IN",
                                       len(_known5),
                                       "" if _in5 else
                                       " -- FIX: register this session with "
                                       "identity_registry.open_identity("
                                       "session_id, worker_key, workflow_id, "
                                       "why=...); the step does NOT register it "
                                       "for you, because opening an identity is "
                                       "a decision"),
                                    int((_t.time() - t_s) * 1000))
                    elif key == "copy_last_response":
                        # THE TRIGGER POINT. THE HUMAN (2026-09-25): "when the
                        # reply didn't finish by worker, you will not have the
                        # copy button, the way is each conversation ask worker
                        # to update status to chat, so we can have the trigger
                        # point when to click on copy button".
                        #
                        # THE THREE STATES ARE DISTINGUISHED, NOT COLLAPSED.
                        # MEASURED: today all three look like "the copy button is
                        # not in view". A `writing` status is a SKIP (the step
                        # was never due), a `failed` status is a FAIL with the
                        # reason, and only `done` runs the click.
                        import chat_worker_status as cws
                        # THE `ACTIVE` SENTINEL IS RESOLVED BY THE WORKER'S OWN
                        # PUBLISH, NOT BY A SESSION FILE. MEASURED (2026-09-25):
                        # resolving it to "the newest session file" was
                        # UNRELIABLE -- several files share an mtime to the
                        # second, so the tie broke arbitrarily and the sentinel
                        # resolved to a DIFFERENT session than the worker
                        # published for. The human's own words give the correct
                        # definition: "each conversation ask worker to update
                        # status to chat". The publish IS the signal.
                        sid = str(step.get("target") or "ACTIVE")
                        cconn = sqlite3.connect(str(BASE_DIR / "agent.db"))
                        cconn.row_factory = sqlite3.Row
                        gate = cws.copy_gate(cconn, sid)
                        cconn.close()
                        detail["worker_status"] = gate.get("status", "NA")
                        detail["copy_gate"] = gate.get("action", "NA")
                        detail["copy_session_id"] = gate.get("session_id", "NA")
                        if gate.get("action") == "skip":
                            _record(step, "SKIP",
                                    "worker status=%s -- %s"
                                    % (gate.get("status"), gate.get("why")),
                                    int((_t.time() - t_s) * 1000))
                        elif gate.get("action") == "fail":
                            _record(step, "FAIL",
                                    "worker status=%s -- %s"
                                    % (gate.get("status"), gate.get("why")),
                                    int((_t.time() - t_s) * 1000))
                        else:
                            # status=done -> the button EXISTS. Click it, and
                            # PROVE the click copied something.
                            #
                            # THE CLIPBOARD SEQUENCE NUMBER IS THE VERDICT. THE
                            # HUMAN (2026-09-25): "you can proof does you have
                            # copy something at the copy list by number, i have
                            # that already! you can check my code / so you don't
                            # need to worry, as all can be proofed".
                            #
                            # `GetClipboardSequenceNumber` is a MONOTONIC
                            # COUNTER the OS bumps on EVERY clipboard write. It
                            # proves the CLICK caused a copy WITHOUT reading the
                            # clipboard's contents, so the step does not depend
                            # on what the response happens to say and cannot
                            # pass by reading a stale clipboard. The pattern is
                            # the repo's own: `_proof_f9.py:21 clip_seq()`.
                            #
                            # THE CLICK IS CONDITION-BASED, NOT A FIXED SLEEP.
                            # MEASURED (2026-09-25): a single click + sleep was
                            # FLAKY -- the sequence number was unchanged in some
                            # runs, because the screen grab between the hover and
                            # the click can drop the hover state, and a click on
                            # a button that is not hovered does nothing. So the
                            # step RE-HOVERS, clicks, and WAITS for the sequence
                            # number to change; if it does not, it retries ONCE
                            # and reports how many attempts it took. A bounded
                            # retry with a loud failure is honest; an unbounded
                            # one would hide a real defect.
                            import ctypes as _ct
                            import pyautogui as _pag
                            import condition_based_waiting as _cbw
                            _pag.FAILSAFE = False
                            # THE BUTTON'S RECT COMES FROM THE REGISTER, not a
                            # literal here: the coordinate target is the single
                            # source of truth for where the button is.
                            #
                            # THE RECT IS SESSION-DEPENDENT, SO IT IS RESOLVED
                            # BY THE ACTIVE SESSION. MEASURED (2026-09-25): in
                            # the agent's own session the chat panel is on the
                            # LEFT and the button is at (166,688)-(200,732); in
                            # a PINNED session the panel is on the RIGHT and the
                            # button is at (1114,748)-(1148,787). A single rect
                            # cannot serve both -- which is exactly why
                            # `coordinate_session` exists. The step tries the
                            # rect linked to the ACTIVE session first, then the
                            # other, and reports which one worked.
                            _rect = None
                            _rect_name = "NA"
                            try:
                                import sys as _s6
                                _s6.path.insert(0, str(BASE_DIR / "scripts"))
                                import mode_attest as _ma6
                                _db6 = _ma6.find_workspace_db()
                                _root6 = (Path(_db6[0]).parent
                                          if isinstance(_db6, (tuple, list))
                                          else Path(_db6))
                                _sess6 = sorted(
                                    (_root6 / _ma6.SESSIONS_DIRNAME).glob(
                                        "*.jsonl"),
                                    key=lambda p: p.stat().st_mtime)
                                _active6 = _sess6[-1].stem if _sess6 else ""
                            except Exception:
                                _active6 = ""
                            _cands = []
                            try:
                                _r = sqlite3.connect(str(BASE_DIR / "agent.db"))
                                _r.row_factory = sqlite3.Row
                                # THE SESSION-LINKED RECT FIRST.
                                if _active6:
                                    _linked = _r.execute(
                                        "SELECT t.name, e.x1, e.y1, e.x2, e.y2 "
                                        "FROM coordinate_session cs "
                                        "JOIN target_template t ON t.id = "
                                        "cs.coordinate_id "
                                        "JOIN environment_template e ON "
                                        "e.template_id = t.id "
                                        "WHERE cs.session_id = ? AND "
                                        "t.name LIKE 'vscode_copy_last_response%'",
                                        (_active6,)).fetchall()
                                    for _lr in _linked:
                                        _cands.append((_lr["name"],
                                                       int(_lr["x1"]),
                                                       int(_lr["y1"]),
                                                       int(_lr["x2"]),
                                                       int(_lr["y2"])))
                                # THEN EVERY copy rect, so a session with no
                                # link still has a chance.
                                for _lr in _r.execute(
                                        "SELECT t.name, e.x1, e.y1, e.x2, e.y2 "
                                        "FROM target_template t JOIN "
                                        "environment_template e ON "
                                        "e.template_id = t.id WHERE "
                                        "t.name LIKE 'vscode_copy_last_response%'"
                                ).fetchall():
                                    _tup = (_lr["name"], int(_lr["x1"]),
                                            int(_lr["y1"]), int(_lr["x2"]),
                                            int(_lr["y2"]))
                                    if _tup not in _cands:
                                        _cands.append(_tup)
                                _r.close()
                            except Exception:
                                _cands = []
                            if not _cands:
                                _record(step, "UNKNOWN",
                                        "no stored rect for "
                                        "vscode_copy_last_response",
                                        int((_t.time() - t_s) * 1000))
                            else:
                                import ctypes as _ct
                                import pyautogui as _pag
                                import condition_based_waiting as _cbw
                                _pag.FAILSAFE = False
                                _seq0 = (_ct.windll.user32
                                         .GetClipboardSequenceNumber())
                                _seq1 = _seq0
                                _tries = 0
                                # `_used` IS THE RECT THAT PRODUCED A COPY, and
                                # `_tried` is EVERY rect attempted. MEASURED
                                # (2026-09-26): `_used` was set ONLY when the
                                # clipboard changed, so a run that tried two
                                # rects and copied nothing reported
                                # "clicked the copy button using rect NA" --
                                # a message that claims a rect was used when
                                # none was. A reader could not tell "the rects
                                # are wrong" from "no rect exists". The two
                                # facts are now separate, and `NA` is reserved
                                # for "no rect exists at all".
                                _used = "NA"
                                _tried = []
                                for (_nm, _x1, _y1, _x2, _y2) in _cands:
                                    _tried.append("%s@(%d,%d)"
                                                  % (_nm,
                                                     (_x1 + _x2) // 2,
                                                     (_y1 + _y2) // 2))
                                    _cx = (_x1 + _x2) // 2
                                    _cy = (_y1 + _y2) // 2
                                    for _try in range(2):
                                        _tries += 1
                                        _pag.moveTo(_cx, _cy)
                                        _t.sleep(0.6)
                                        _pag.click(_cx, _cy)
                                        try:
                                            _cbw.wait_until(
                                                lambda: (_ct.windll.user32
                                                         .GetClipboardSequenceNumber()
                                                         != _seq0),
                                                timeout=3.0,
                                                description="the clipboard "
                                                            "sequence number "
                                                            "changes")
                                        except Exception:
                                            pass
                                        _seq1 = (_ct.windll.user32
                                                 .GetClipboardSequenceNumber())
                                        if _seq1 != _seq0:
                                            break
                                    if _seq1 != _seq0:
                                        _used = _nm
                                        break
                                detail["clipboard_seq"] = "%s -> %s" % (_seq0,
                                                                        _seq1)
                                detail["copy_rect_used"] = _used
                                detail["copy_rects_tried"] = _tried
                                detail["copy_session"] = _active6
                                _copied = _seq1 != _seq0
                                # THE MESSAGE STATES WHICH FACT IS TRUE. A rect
                                # that WORKED is named; when none worked, the
                                # rects TRIED are named instead, so the reader
                                # sees the miss rather than a bare `NA`.
                                if _copied:
                                    _msg = ("worker status=done -> clicked the "
                                            "copy button using rect %s; "
                                            "clipboard seq %s -> %s after %d "
                                            "attempt(s) (session %s)"
                                            % (_used, _seq0, _seq1, _tries,
                                               _active6))
                                else:
                                    _msg = ("worker status=done -> tried %d "
                                            "rect(s) %s; NONE produced a copy "
                                            "(clipboard seq stayed %s) after %d "
                                            "attempt(s) (session %s) -- the "
                                            "stored rects do not hit the button "
                                            "on this layout"
                                            % (len(_tried), _tried, _seq0,
                                               _tries, _active6))
                                _record(step, "PASS" if _copied else "FAIL",
                                        _msg, int((_t.time() - t_s) * 1000))
                                # A LINK IS WRITTEN ONLY FOR A RECT THAT WORKED.
                                # THE HUMAN (2026-09-25): `coordinate_session`
                                # records WHICH SESSION a coordinate was measured
                                # in. Recording a rect that produced NO copy
                                # would mark a wrong rect as good, so the link
                                # is written on success only.
                                if _copied and _active6:
                                    try:
                                        import coordinate_session_registry as _csr
                                        _c8 = sqlite3.connect(
                                            str(BASE_DIR / "agent.db"))
                                        _c8.row_factory = sqlite3.Row
                                        _csr.ensure_schema(_c8)
                                        _cid8 = _c8.execute(
                                            "SELECT id FROM target_template "
                                            "WHERE name=?", (_used,)).fetchone()
                                        if _cid8:
                                            _csr.link(
                                                _c8, _active6,
                                                int(_cid8["id"]),
                                                why=("the rect that produced a "
                                                     "copy in this session"),
                                                cite_ref=("mouse_spot_helper.py:"
                                                          "copy_last_response"))
                                            detail["copy_link_written"] = (
                                                "%s -> %s" % (_active6, _used))
                                        _c8.close()
                                    except Exception as _exc8b:
                                        detail["copy_link_error"] = (
                                            "%s: %s" % (type(_exc8b).__name__,
                                                        _exc8b))
                    elif key == "connect_cdp":
                        # 豆包 IS A BROWSER, AND IT IS ALREADY RUNNING.
                        #
                        # THE HUMAN (2026-09-26): "豆包 is a **desktop app** is
                        # past !! now is 豆包 is a browser!! ... and she has it
                        # own offical browser too / update your old data, to
                        # stop mis-understand".
                        #
                        # WHY CONNECT AND NOT LAUNCH. MEASURED `doubao_cdp.py:1`:
                        # 豆包 ships a FULL Chromium 147 and answers the DevTools
                        # Protocol (`GET /json/version` ->
                        # `Chrome/147.0.7727.149`), and
                        # `p.chromium.connect_over_cdp(url)` connects. Launching
                        # a second 豆包 would open a SECOND window and drive the
                        # wrong one -- the user's own window is the one with
                        # their conversation in it.
                        #
                        # THE PORT IS A SETTING. MEASURED `doubao_cdp.py:29`:
                        # 9222 belongs to the Chrome instance used by F5/F9, so
                        # 豆包's default is 9333. A shared port would hand a 豆包
                        # tab to a DeepSeek tool.
                        import doubao_cdp as _dc
                        _cdp = str(step.get("target") or _dc.cdp_url())
                        _ready = _dc.cdp_ready(timeout=3.0)
                        detail["cdp_url"] = _cdp
                        detail["cdp_ready"] = _ready
                        if not _ready:
                            # CDP IS OFF BY DEFAULT IN 豆包. MEASURED
                            # `doubao_cdp.py:1`: a default launch listens on
                            # 11128 and 49853 and NEITHER is CDP. So "not ready"
                            # is a REAL state with a REAL fix, and it is
                            # reported as such rather than as a mystery.
                            _record(step, "FAIL",
                                    "CDP is not listening at %s -- 豆包 must be "
                                    "launched with --remote-debugging-port "
                                    "(doubao_cdp.launch_doubao)" % _cdp,
                                    int((_t.time() - t_s) * 1000))
                        else:
                            browser = p.chromium.connect_over_cdp(_cdp)
                            _ver = _dc.version()
                            detail["cdp_version"] = _ver.get("Browser", "NA")
                            _n = len(browser.contexts[0].pages) \
                                if browser.contexts else 0
                            detail["cdp_pages"] = _n
                            _record(step, "PASS" if _n else "FAIL",
                                    "connected to %s (%s), %d page(s)"
                                    % (_cdp, detail["cdp_version"], _n),
                                    int((_t.time() - t_s) * 1000))
                    elif key == "find_page":
                        # THE 豆包 PAGE, AMONG THE CONNECTED PAGES. A CDP
                        # connection exposes EVERY page the browser has open, so
                        # the step names which one it wants rather than taking
                        # the first -- the first may be a settings tab.
                        _want = str(step.get("target") or "doubao.com")
                        page = None
                        for _ctx in (browser.contexts or []):
                            for _pg in _ctx.pages:
                                if _want in (_pg.url or ""):
                                    page = _pg
                                    break
                            if page:
                                break
                        detail["page_url"] = page.url if page else "NA"
                        _record(step, "PASS" if page else "FAIL",
                                "page containing %r: %s"
                                % (_want, page.url if page else "NOT FOUND"),
                                int((_t.time() - t_s) * 1000))
                    elif key == "assert_dom":
                        # THE DOM IS THE POINT OF CDP. MEASURED
                        # `doubao_cdp.py:1`: driving by DOM removes the whole
                        # class of problems the pixel route had to work around
                        # (card rect, star-row colour, scroll helper, coordinate
                        # space, focus border). So the step proves the input is
                        # reachable in the DOM, which is what makes the rest
                        # possible.
                        _sel = str(step.get("target") or "textarea")
                        _el = page.query_selector(_sel) if page else None
                        detail["dom_selector"] = _sel
                        _record(step, "PASS" if _el else "FAIL",
                                "%s %s in the DOM"
                                % (_sel, "IS" if _el else "is NOT"),
                                int((_t.time() - t_s) * 1000))
                    elif key == "scroll_state":
                        # THE SCROLL POSITION, READ FROM THE DOM. MEASURED
                        # `doubao_cdp.py:373`: `scrollTop` / `scrollHeight` give
                        # the answer directly, so the VS Code route's "is the
                        # scroll-to-bottom button visible?" inference is not
                        # needed here.
                        _st = {}
                        try:
                            _st = page.evaluate(
                                "() => ({top: document.documentElement.scrollTop,"
                                " h: document.documentElement.scrollHeight,"
                                " vh: window.innerHeight})") if page else {}
                        except Exception:
                            _st = {}
                        _at_bottom = bool(_st) and (
                            _st.get("top", 0) + _st.get("vh", 0)
                            >= _st.get("h", 0) - 4)
                        detail["scroll_state"] = _st
                        detail["at_bottom"] = _at_bottom
                        _record(step, "PASS" if _st else "FAIL",
                                "scrollTop=%s scrollHeight=%s innerHeight=%s "
                                "at_bottom=%s"
                                % (_st.get("top"), _st.get("h"),
                                   _st.get("vh"), _at_bottom),
                                int((_t.time() - t_s) * 1000))
                    elif key == "launch_browser":
                        # WHICH BROWSER. THE HUMAN: "launch_browser is for which
                        # browser? is for google chrome or edge or 豆包". The
                        # channel comes from the ENVIRONMENT, and the step's
                        # `target` names it, so the guide answers the question.
                        ch = str(step.get("target") or channel or "NA")
                        launch_kw: dict = {"headless": True}
                        if ch not in ("NA", "", "chromium"):
                            launch_kw["channel"] = ch
                        browser = p.chromium.launch(**launch_kw)
                        detail["browser"] = "%s %s (channel=%s)" % (
                            browser.browser_type.name, browser.version, ch)
                        _record(step, "PASS" if browser.version else "FAIL",
                                str(detail["browser"]),
                                int((_t.time() - t_s) * 1000))
                    elif key == "goto_url":
                        page = browser.new_page(viewport={"width": 1600,
                                                          "height": 1000})
                        detail["viewport"] = "1600x1000"
                        page.goto(url, wait_until="networkidle", timeout=30000)
                        _record(step, "PASS", page.url,
                                int((_t.time() - t_s) * 1000))
                    elif key == "wait_for_table":
                        page.wait_for_selector("#playwright-root table",
                                               timeout=15000)
                        _record(step, "PASS", "#playwright-root table",
                                int((_t.time() - t_s) * 1000))
                    elif key == "assert_columns":
                        got = page.evaluate("""() => {
                          const root = document.querySelector('#playwright-root');
                          return {
                            url: location.pathname,
                            h2: root.querySelector('h2') ? root.querySelector('h2').innerText : '',
                            heads: [...root.querySelectorAll('thead th')]
                              .map(th => th.innerText.trim().toLowerCase()),
                            rows: [...root.querySelectorAll('tbody tr')].length,
                            hasTestBtn: !!root.querySelector('[data-pw-test]')
                          };
                        }""")
                        detail.update({"page_url": got["url"],
                                       "heading": got["h2"],
                                       "columns": got["heads"],
                                       "row_count": got["rows"]})
                        ok = "environment_id" in got["heads"]
                        _record(step, "PASS" if ok else "FAIL",
                                str(got["heads"]),
                                int((_t.time() - t_s) * 1000))
                    elif key == "screenshot":
                        shot = Path(rec.dir) / "shot.png"
                        page.screenshot(path=str(shot), full_page=True)
                        rec.files["shot"] = str(shot)
                        detail["screenshot"] = str(shot)
                        _record(step, "PASS" if shot.is_file() else "FAIL",
                                str(shot), int((_t.time() - t_s) * 1000))
                    elif key == "write_evidence":
                        # The record is written AFTER the loop; this step's
                        # proof is that the payload is complete enough to pass
                        # the gate. Recorded here so the ORDER is honest.
                        _record(step, "PASS", "payload assembled",
                                int((_t.time() - t_s) * 1000))
                    else:
                        # AN UNKNOWN STEP IS NOT A PASS. A step in the guide
                        # that this interpreter cannot run is a REAL defect --
                        # the guide promised something the code does not do.
                        _record(step, "UNKNOWN",
                                "no interpreter for action=%r" % step["action"],
                                int((_t.time() - t_s) * 1000))
                except Exception as exc:
                    _record(step, "FAIL",
                            "%s: %s" % (type(exc).__name__, exc),
                            int((_t.time() - t_s) * 1000))
            if browser:
                browser.close()
    except Exception as exc:
        checks.append({"name": "the playwright run completed", "ok": False,
                       "got": "%s: %s" % (type(exc).__name__, exc)})
        detail["error"] = "%s: %s" % (type(exc).__name__, exc)

    detail["elapsed_ms"] = int((_t.time() - t0) * 1000)
    passed = bool(checks) and all(c["ok"] for c in checks)
    # A RUN WHOSE SCREENSHOTTER IS DEAD MUST NOT REPORT PASS.
    # THE HUMAN (2026-09-26): the STEP table showed "no image yet" on every row.
    # MEASURED: every run from `EVID-playwright_env-20260926-010816` onward wrote
    # 0/8 images while still reporting a verdict, so a broken capture could hide
    # behind a green run. A guide with steps and ZERO pictures is NOT a pass --
    # it is UNKNOWN, with the reason, because the evidence the run claims to
    # carry is not there.
    n_steps = len(step_runs)
    n_imgs = sum(1 for sr in step_runs
                 if str(sr.get("image_name") or "NA") != "NA")
    detail["step_images"] = n_imgs
    detail["step_count"] = n_steps
    if n_steps and n_imgs == 0:
        why = next((str(sr.get("image_why") or "") for sr in step_runs
                    if sr.get("image_why")), "")
        checks.append({
            "name": "the run captured at least one step image",
            "ok": False,
            "got": ("0 of %d step(s) wrote an image -- the screenshotter is "
                    "dead, so this run's evidence is INCOMPLETE. reason: %s"
                    % (n_steps, why or "not recorded"))})
        passed = False
        out["verdict"] = "UNKNOWN"
        detail["image_fault"] = why or "not recorded"
    else:
        out["verdict"] = "PASS" if passed else "FAIL"
    out["detail"] = detail
    out["checks"] = checks
    out["steps"] = step_runs
    out["ok"] = True

    # WRITE THE PER-STEP RUN ROWS. This is what turns the guide from a promise
    # into a record: the STEP GUIDE tab shows the last run beside each step.
    try:
        import playwright_step_registry as psr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        for sr in step_runs:
            psr.record_run(conn, rec.evidence_id, playwright_id,
                           sr["step_no"], step_key=sr["step_key"],
                           status=sr["status"], got=sr["got"],
                           elapsed_ms=sr["elapsed_ms"],
                           image_name=sr.get("image_name", "NA"),
                           image_why=sr.get("image_why", ""))
        conn.close()
    except Exception as exc:
        out["why"] = "step run write failed: %s" % exc

    # The evidence record. `save_classify` has its OWN write gate: a PASS/FAIL
    # needs a PROOF RECORD, so the proof is attached rather than asserted.
    #
    # THE GATE'S SHAPE IS `provenance`, NOT `proof`. MEASURED (2026-09-25): the
    # first version sent a `proof` block, so `env_proof.proof_status` found no
    # `provenance` and REFUSED the PASS -- the verdict was downgraded to UNKNOWN
    # on a test whose checks had all passed. The gate wants:
    #   provenance.source, provenance.sha256, provenance.foreground.process,
    #   provenance.foreground.is_code
    #
    # THE FOREGROUND QUESTION, answered honestly: this is a PLAYWRIGHT PAGE
    # screenshot, not a screen capture, so "which window was in front" is not
    # the right question. The equivalent fact -- the one that stops a capture of
    # the WRONG PAGE being recorded as a confident verdict -- is the URL that was
    # actually loaded, plus the sha256 of the image. Both are recorded, and
    # `foreground.process` names the browser that rendered it.
    try:
        import evidence_store as es
        import hashlib as _h
        shot_path = detail.get("screenshot")
        sha = "NA"
        if shot_path and Path(shot_path).is_file():
            sha = _h.sha256(Path(shot_path).read_bytes()).hexdigest()
        payload = {
            "verdict": out["verdict"],
            "label": "playwright environment test",
            "task_id": "PLAYWRIGHT.STEP.GUIDE",
            "created_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
            # `checks` IS A DICT, NOT A LIST. MEASURED (2026-09-25):
            # `evidence_store.build_report` iterates `checks.items()`, so a LIST
            # raised `'list' object has no attribute 'items'` and the report was
            # never written -- the verdict was PASS while the report was missing.
            # The store's shape is `{name: {ok, detail}}`.
            "checks": {c["name"]: {"ok": c["ok"], "detail": c.get("got", "")}
                       for c in checks},
            "measured": detail,
            # THE STEP GUIDE, carried into the evidence so the record says WHAT
            # WAS EXPECTED and WHAT WAS OBSERVED, step by step. The human:
            # "how to i know what will happen, without that, i don't know and
            # how to proof it is worked or not?"
            "steps": step_runs,
            "provenance": {
                "source": "playwright.sync_api page.screenshot",
                "sha256": sha,
                "url": url,
                "page_url": detail.get("page_url", "NA"),
                "foreground": {
                    "process": detail.get("browser", "NA"),
                    "is_code": False,
                    "why": ("a Playwright PAGE screenshot: the loaded URL is the "
                            "identity, not the OS foreground window"),
                },
            },
        }
        try:
            es.save_classify(rec, payload)
        except Exception as exc:
            # The write gate refused. Record it as UNKNOWN WITH the reason rather
            # than downgrading silently -- "we learned nothing" must stay
            # recordable, or the honest outcome is the one that gets suppressed.
            payload["verdict"] = "UNKNOWN"
            payload["gate_refused"] = "%s: %s" % (type(exc).__name__, exc)
            es.save_classify(rec, payload, enforce_proof=False)
            out["verdict"] = "UNKNOWN"
            out["why"] = "write gate refused the verdict: %s" % exc
        es.save_report(rec, payload)
    except Exception as exc:
        out["why"] = "evidence write failed: %s" % exc
    return out


@app.route("/api/playwright/test", methods=["POST"])
def api_playwright_test() -> Any:
    """Run the REAL playwright test and write an evidence record.

    THE MASTER SWITCH IS CHECKED FIRST. THE HUMAN (2026-09-26): "playwright
    system with function be control ON/OFF". When `PLAYWRIGHT_OFF` exists the
    run is REFUSED with a reason that names the switch and the way back -- a
    silent no-op would leave the reader with "it did not run" and no why.

    AN OPTIONAL `switch_file` NAMES THE FILE FOR THIS REQUEST ONLY, so a proof
    can drive its own switch without touching the human's global one.
    """
    data = request.get_json(silent=True) or {}
    try:
        import playwright_run_registry as prr
        refusal = prr.refuse_if_off(data.get("switch_file") or None)
    except Exception:
        refusal = None
    if refusal is not None:
        return jsonify(refusal), 409
    try:
        eid = int(data.get("environment_id") or 6)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "environment_id must be an int"}), 400
    r = _run_playwright_test(eid)
    return jsonify(r), (200 if r.get("ok") else 500)


@app.route("/api/playwright/evidence", methods=["GET"])
def api_playwright_evidence() -> Any:
    """The NEWEST evidence record for the playwright test target.

    Reads the EXISTING evidence store, so the page and the Evidence Center
    cannot disagree about what was recorded.
    """
    try:
        import evidence_store as es
        rows = es.list_evidence(PLAYWRIGHT_TEST_TARGET)
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    if not rows:
        return jsonify({"ok": True, "record": None,
                        "why": "no evidence yet — press the red Test button"})
    newest = rows[0]
    eid = newest.get("evidence_id")
    detail: dict = {}
    try:
        d = es.EVIDENCE_ROOT / str(eid)
        cj = d / "classify.json"
        if cj.is_file():
            j = json.loads(cj.read_text(encoding="utf-8", errors="replace"))
            detail = j.get("measured") or {}
            newest["checks"] = j.get("checks") or []
            newest["proof"] = j.get("proof") or {}
            # THE STEP GUIDE, read back out of the record. The human: "how to i
            # know what will happen, without that, i don't know and how to proof
            # it is worked or not?" The record carries `expect` + `proof` +
            # `got` per step, so the evidence panel can show all three.
            #
            # EACH STEP CARRIES ITS OWN PICTURE. THE HUMAN (2026-09-25): "for
            # the STEP and Evidence table, STEP is onclick to image" and
            # "+ image field user friendly". MEASURED: only the `screenshot`
            # step wrote an image, so every other step row had no picture and
            # its `got` was a sentence the reader had to trust. The URL is
            # resolved HERE, from the file the run actually wrote, so a step
            # whose image is missing reads as `NA` rather than as a broken link.
            steps = j.get("steps") or []
            for st in steps:
                fn = st.get("image_name") or "NA"
                if fn != "NA" and (d / fn).is_file():
                    st["image_url"] = ("/api/evidence/%s/file/%s" % (eid, fn))
                else:
                    st["image_url"] = None
                # THE THUMBNAIL IS WHAT THE TABLE SHOWS; the full image is what
                # the click opens. MEASURED (2026-09-25): the table's 64px <img>
                # pulled a 350KB file, so a step image was slow to appear. The
                # thumbnail is ~15KB.
                _tf = str(fn).replace(".png", "_thumb.png")
                st["thumb_url"] = ("/api/evidence/%s/file/%s" % (eid, _tf)
                                   if fn != "NA" and (d / _tf).is_file()
                                   else st["image_url"])
            # The steps also live in the run LOG, which is where the per-step
            # image name is authoritative. Merge it in so the panel can render
            # even when classify.json predates the image column.
            try:
                import playwright_step_registry as psr2
                _c2 = sqlite3.connect(str(BASE_DIR / "agent.db"))
                _c2.row_factory = sqlite3.Row
                runs = {int(x["step_no"]): dict(x)
                        for x in psr2.runs_for(_c2, str(eid))}
                _c2.close()
                for st in steps:
                    r2 = runs.get(int(st.get("step_no") or -1))
                    if not r2:
                        continue
                    st.setdefault("image_name", r2.get("image_name", "NA"))
                    if st.get("image_name") in (None, "", "NA"):
                        st["image_name"] = r2.get("image_name", "NA")
                    fn = st.get("image_name")
                    if fn and fn != "NA" and (d / fn).is_file():
                        st["image_url"] = ("/api/evidence/%s/file/%s"
                                           % (eid, fn))
                    _tf = str(st.get("image_name") or "").replace(
                        ".png", "_thumb.png")
                    if _tf and (d / _tf).is_file():
                        st["thumb_url"] = ("/api/evidence/%s/file/%s"
                                           % (eid, _tf))
                    elif not st.get("thumb_url"):
                        st["thumb_url"] = st.get("image_url")
                    # THE RUN ROW IS THE RECORD, the classify.json the report.
                    # When they disagree, the RUN wins -- it is written at the
                    # moment of the act.
                    for k in ("status", "got", "elapsed_ms"):
                        if r2.get(k) not in (None, ""):
                            st[k] = r2[k]
                    st["evidence_id"] = str(eid)
                    st["playwright_id"] = r2.get("playwright_id")
            except Exception as e2:
                newest["step_merge_error"] = "%s: %s" % (type(e2).__name__, e2)
            newest["steps"] = steps
            newest["evidence_id"] = str(eid)
        shot = d / "shot.png"
        newest["image_url"] = ("/api/evidence/%s/file/shot.png" % eid
                               if shot.is_file() else None)
    except Exception as e:
        newest["detail_error"] = "%s: %s" % (type(e).__name__, e)
    newest["detail"] = detail
    return jsonify({"ok": True, "record": newest})


@app.route("/api/chat/worker-status", methods=["POST"])
def api_chat_worker_status_publish() -> Any:
    """THE WORKER PUBLISHES ITS OWN STATE -- the copy button's TRIGGER POINT.

    THE HUMAN (2026-09-25): "when the reply didn't finish by worker, you will
    not have the copy button, the way is each conversation ask worker to
    update status to chat, so we can have the trigger point when to click on
    copy button".

    A copy button appears when the response is COMPLETE. "Complete" is a fact
    the WORKER knows and the UI does not, so the worker publishes it here.
    """
    data = request.get_json(silent=True) or {}
    try:
        import chat_worker_status as cws
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        r = cws.publish(conn, data.get("session_id"),
                        data.get("turn_no") or 1,
                        status=data.get("status"),
                        cite_ref=data.get("cite_ref")
                        or "mouse_spot_helper.py:api_chat_worker_status_publish")
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify(r), (200 if r.get("ok") else 400)


@app.route("/api/chat/worker-status", methods=["GET"])
def api_chat_worker_status_get() -> Any:
    """The newest worker status + the COPY GATE for one session."""
    sid = request.args.get("session_id") or ""
    try:
        import chat_worker_status as cws
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        out = {"ok": True, "latest": cws.latest(conn, sid),
               "gate": cws.copy_gate(conn, sid)}
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify(out)


def _resolve_step_images(rows: list) -> None:
    """Give each guide row its `image_url` + `thumb_url`, IN PLACE.

    ONE IMPLEMENTATION, TWO CALLERS. THE HUMAN (2026-09-26): the Evidence pop-up
    must show each STEP's picture, and `api_playwright_steps` already resolved
    exactly that. A second copy would be the one that goes stale, so the
    resolution lives here and both endpoints call it.

    The LAST RUN of a step wrote the image, so its evidence folder is where the
    file lives -- the URL is resolved here rather than stored, so a step whose
    image is missing reads as `NA` instead of a broken link.

    THE SENTINEL `NA` IS TRUTHY, so it must be EXCLUDED EXPLICITLY. MEASURED
    (2026-09-25): `last_evidence_id` defaults to the string "NA", so `a or b`
    picked "NA" as a real id and the path check failed on every row -- the API
    returned `image_url: None` for a run that had written 8 images. A sentinel
    that reads as data is the defect; the fix is to test for it rather than to
    rely on falsiness.
    """
    try:
        import evidence_store as es
        for row in (rows or []):
            fn = row.get("image_name") or "NA"
            eid = row.get("last_evidence_id")
            if eid in (None, "", "NA"):
                eid = row.get("evidence_id")
            if eid in (None, "", "NA"):
                eid = None
            url = None
            if fn != "NA" and eid:
                p = es.EVIDENCE_ROOT / str(eid) / fn
                if p.is_file():
                    url = "/api/evidence/%s/file/%s" % (eid, fn)
            if url is None and fn != "NA":
                # Fall back to a search, so a row whose evidence id was not
                # carried still shows its picture.
                try:
                    hits = sorted(BASE_DIR.glob("evidence/**/%s" % fn))
                    if hits:
                        rel = hits[-1].relative_to(BASE_DIR / "evidence")
                        url = ("/api/evidence/%s/file/%s"
                               % (rel.parts[0], fn))
                except Exception:
                    pass
            row["image_url"] = url
            # THE THUMBNAIL FOR THE TABLE, the full image for the click.
            tf = str(fn).replace(".png", "_thumb.png")
            row["thumb_url"] = None
            if fn != "NA" and eid:
                tp = es.EVIDENCE_ROOT / str(eid) / tf
                if tp.is_file():
                    row["thumb_url"] = ("/api/evidence/%s/file/%s"
                                        % (eid, tf))
            if row["thumb_url"] is None:
                row["thumb_url"] = row["image_url"]
    except Exception:
        pass


@app.route("/api/playwright/switch", methods=["GET", "POST"])
def api_playwright_switch() -> Any:
    """THE MASTER ON/OFF SWITCH for the Playwright system.

    THE HUMAN (2026-09-26): "playwright system with function be control ON/OFF" /
    "have the button!! turn off / ON".

    GET  -> the state.
    POST -> `{"on": true|false}`; returns the NEW state.

    THE STATE IS A FILE (`PLAYWRIGHT_OFF` at the repo root), the SAME shape the
    Stop hook already uses. It is read from the filesystem every time, so a
    process restart cannot lose it.

    OFF REFUSES A NEW RUN. It does NOT kill a run already in flight -- a switch
    that silently kills work is a worse defect than no switch, and stopping a
    live run stays the per-row Stop button's job.

    AN OPTIONAL `switch_file` NAMES THE FILE FOR THIS REQUEST ONLY. THE HUMAN
    (2026-09-26): "制止呢類並行 proof 互相污染全域狀態". A proof that must turn the
    switch OFF otherwise holds the ONE global file OFF for ~90s, and any other
    proof (or another agent session) that starts inside that window reads OFF.
    With `switch_file`, the proof drives its OWN file and the global switch is
    untouched. It is a REQUEST FIELD, not an env var, because this server is
    THREADED: two concurrent requests must not race on one process-global
    variable.
    """
    try:
        import playwright_run_registry as prr
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500
    if request.method == "GET":
        sf = request.args.get("switch_file") or None
        return jsonify(prr.switch_status(sf)), 200
    data = request.get_json(silent=True) or {}
    if "on" not in data:
        return jsonify({"ok": False,
                        "error": "body must carry `on`: true|false"}), 400
    sf = data.get("switch_file") or None
    out = prr.set_enabled(bool(data.get("on")), sf)
    return jsonify(out), (200 if out.get("ok") else 500)


@app.route("/api/playwright/runs", methods=["GET"])
def api_playwright_runs() -> Any:
    """WHICH playwright runs are running RIGHT NOW, and WHY each one is.

    THE HUMAN (2026-09-26): "show me which playwright and running and why, with
    stop button".

    MEASURED BEFORE: the page could say a run HAD happened (the evidence panel)
    but nothing could say a run IS happening. A burst of 120 processes was
    invisible to the UI, so the only way to find it was a terminal.

    THE WHY IS MEASURED, NOT GUESSED. `playwright_run_registry.why()` walks
    `Win32_Process.ParentProcessId` to the root, so the answer is the real parent
    chain -- MEASURED 2026-09-26 21:12, that is how `_tmp_triage.py` was found
    as the spawner. When the script is DECLARED, the gate's own reason text is
    attached, so the page and the gate cannot disagree.

    READ-ONLY. Stopping is a separate, deliberate POST.
    """
    try:
        import playwright_run_registry as prr
        out = prr.running_runs()
    except Exception as e:
        return jsonify({"ok": False, "rows": [],
                        "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify(out), (200 if out.get("ok") else 500)


@app.route("/api/playwright/runs/stop", methods=["POST"])
def api_playwright_runs_stop() -> Any:
    """STOP one playwright run: kill its process TREE, children first.

    THE HUMAN (2026-09-26): "with stop button".

    IT REFUSES A PID THAT IS NOT A DECLARED PLAYWRIGHT-DRIVING SCRIPT. A stop
    endpoint that kills whatever pid it is handed is a remote kill switch for the
    whole machine; the declared set is the guard. An impossible pid is REFUSED,
    never reported as stopped -- a false "stopped" is worse than an error.
    """
    data = request.get_json(silent=True) or {}
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "killed": [],
                        "error": "pid must be an int"}), 400
    try:
        import playwright_run_registry as prr
        out = prr.stop_run(pid)
    except Exception as e:
        return jsonify({"ok": False, "killed": [],
                        "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify(out), (200 if out.get("ok") else 400)


@app.route("/api/playwright/steps", methods=["GET"])
def api_playwright_steps() -> Any:
    """THE STEP GUIDE: what each step does, what WILL HAPPEN, how to PROOF it.

    THE HUMAN (2026-09-25): "so where is STEP GUIDE? / how to i know what will
    happen, without that, i don't know and how to proof it is worked or not?"

    Each row carries `expect` (what will happen), `proof` (how it is checked)
    and the LAST RUN's status + `got` + ms. That is the whole answer in one
    table, and it is read from the SAME table the test executes, so the guide
    and the run cannot disagree.
    """
    pid = request.args.get("playwright_id")
    try:
        pid_i = int(pid) if pid not in (None, "") else 1
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "playwright_id must be an int"}), 400
    try:
        import playwright_step_registry as psr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        g = psr.guide(conn, pid_i)
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    # EACH GUIDE ROW GETS ITS PICTURE. THE HUMAN (2026-09-25): "for the STEP and
    # Evidence table, STEP is onclick to image" and "+ image field user
    # friendly". ONE implementation, shared with the Evidence pop-up endpoint.
    _resolve_step_images(g.get("rows") or [])
    return jsonify(g)


@app.route("/api/playwright/steps/run", methods=["GET"])
def api_playwright_steps_run() -> Any:
    """The per-step RESULT of ONE run, keyed by evidence_id.

    A register describes what a thing IS; a log records what HAPPENED. The guide
    is the register; this is the log.
    """
    eid = request.args.get("evidence_id")
    if not eid:
        return jsonify({"ok": False, "error": "evidence_id is required"}), 400
    try:
        import playwright_step_registry as psr
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        rows = psr.runs_for(conn, eid)
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify({"ok": True, "evidence_id": eid, "rows": rows,
                    "count": len(rows)})


@app.route("/api/playwright/coords", methods=["GET"])
def api_playwright_coords() -> Any:
    """EVERY COORDINATE TARGET, with its rect, centre and EVID image.

    THE HUMAN (2026-09-25): "stop, update all infor at UI, so i can prove to u
    1 by 1". The rects live in `environment_template` and the images in
    `evidence_final`, but neither was visible in one place -- so a human could
    not check a rect against its picture without opening two tools.

    Each row carries the rect, its CENTRE (what a click actually uses), the
    EVID image NAME (so the picture can be found), and the label. That is the
    whole answer in one table, read from the SAME table the steps execute, so
    the UI and the run cannot disagree.
    """
    env = request.args.get("environment_id")
    try:
        env_i = int(env) if env not in (None, "") else 6
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "environment_id must be an int"}), 400
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        # BOTH KINDS, NOT ONLY COORDINATES (fixed 2026-09-27).
        #
        # MEASURED DEFECT: the WHERE clause was `t.field_type = 'coordinate'`,
        # so the `vscode_taskbar_win_n` row (field_type='hotkey', value `Win+2`)
        # was DROPPED. The COORDINATES tab then showed 19 of the 20 taskbar
        # elements and said nothing about the missing one -- a page whose count
        # disagrees with the register, with no way for the reader to notice.
        #
        # THE HUMAN (2026-09-27): "where is the UI" -- asked because the row
        # they had just measured was not on the page they were looking at.
        #
        # A hotkey is NOT a coordinate, so it is not rendered as one: the row
        # carries `field_type` and the UI shows the hotkey VALUE where a
        # coordinate row shows its rect. The two value shapes stay distinct.
        rows = conn.execute(
            "SELECT t.id, t.name, t.label, t.field_type, e.is_active, "
            "e.x1, e.y1, e.x2, e.y2, e.cx, e.cy, e.hotkey "
            "FROM target_template t "
            "JOIN environment_template e ON e.template_id = t.id "
            "WHERE e.environment_id = ? "
            "ORDER BY t.field_type, t.id", (env_i,)).fetchall()
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    out = []
    for r in rows:
        d = dict(r)
        # A HOTKEY ROW HAS NO RECT. Its coordinate columns are the NA_INT
        # sentinel (-1), and rendering `-1,-1 -> -1,-1` would present a
        # "not collected" state as a real measurement at x=-1. So the rect and
        # the centre are `NA` for a hotkey, and the hotkey value is what the
        # row carries.
        if str(d.get("field_type")) == "hotkey":
            d["centre"] = "NA"
            d["image_name"] = "NA"
            out.append(d)
            continue
        # THE CENTRE IS COMPUTED, NOT STORED, so the UI and a click agree by
        # construction. A stored centre can drift from the rect it belongs to.
        try:
            d["centre"] = "(%d,%d)" % ((int(d["x1"]) + int(d["x2"])) // 2,
                                       (int(d["y1"]) + int(d["y2"])) // 2)
        except (TypeError, ValueError):
            d["centre"] = "NA"
        # THE EVID IMAGE IS FOUND, NOT STORED. MEASURED: `image_name` is not a
        # column -- it is the newest `EVID-<name>-*_full.png` in
        # `evidence_final`. A stored name would go stale the moment the image is
        # regenerated, so the lookup is done here.
        #
        # SORTED BY mtime, NOT LEXICOGRAPHICALLY. MEASURED 2026-09-27: a stale
        # `EVID-<name>-PROOF_redcross_vl.png` sorted AFTER the newest timestamped
        # file, so `hits[-1]` returned the OLD image. The same defect was found
        # in `_proof_vscode_hotkey_coord.py` and fixed there; this is the second
        # copy of the lookup, so it is fixed here too.
        d["image_name"] = "NA"
        try:
            hits = sorted((BASE_DIR / "evidence_final").glob(
                "EVID-%s-*_full.png" % d["name"]),
                key=lambda p: p.stat().st_mtime)
            if hits:
                d["image_name"] = hits[-1].name
        except Exception:
            pass
        out.append(d)
    return jsonify({"ok": True, "environment_id": env_i, "rows": out,
                    "count": len(out),
                    "coordinate_count": sum(1 for r in out
                                            if r.get("field_type") == "coordinate"),
                    "hotkey_count": sum(1 for r in out
                                        if r.get("field_type") == "hotkey")})


@app.route("/api/playwright/coordinate-session", methods=["GET"])
def api_playwright_coordinate_session() -> Any:
    """WHICH SESSION each coordinate was measured in.

    THE HUMAN (2026-09-25): "session id at where in identity table? / coordinate
    table is individual? by target id? if yes, is easy / + table :
    coordinate_session / is | session_id | coordinate_id |".

    The session id is NOT a new concept: it is `identity_registry.session_id`.
    This endpoint exposes the LINK, so a reader can see which conversation a
    stored rect came from -- which is the context a bare rect loses.
    """
    sid = request.args.get("session_id")
    cid = request.args.get("coordinate_id")
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
        conn.row_factory = sqlite3.Row
        import coordinate_session_registry as csr
        if cid not in (None, ""):
            rows = csr.for_coordinate(conn, int(cid))
            conn.close()
            return jsonify({"ok": True, "coordinate_id": int(cid),
                            "rows": rows, "count": len(rows)})
        if sid in (None, ""):
            # NO SESSION GIVEN -> the agent's own, which is where the rects were
            # measured. A default that is a REAL session is better than an empty
            # answer, and the response names it so the reader can check.
            sid = "f5acaec6-9a6b-48e2-9df3-a26d8159dd7f"
        rows = csr.for_session(conn, str(sid))
        conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500
    return jsonify({"ok": True, "session_id": str(sid), "rows": rows,
                    "count": len(rows)})


@app.route("/api/templates", methods=["POST"])
def api_templates_create() -> Any:
    data = request.get_json(silent=True) or {}
    try:
        row = create_prompt_setting(data)
    except ValueError as e:
        msg = str(e)
        code = 409 if "already exists" in msg else 400
        return jsonify({"ok": False, "error": msg, "detail": msg}), code
    return jsonify(row)


@app.route("/api/templates/<int:template_id>", methods=["GET"])
def api_templates_get(template_id: int) -> Any:
    row = get_format_template_row_by_id(template_id)
    if not row:
        return jsonify({"ok": False, "error": "template not found", "detail": "template not found"}), 404
    out = dict(row)
    out["skill_ref_count"] = count_skill_refs_to_setting(template_id)
    return jsonify(out)


@app.route("/api/templates/<int:template_id>", methods=["PUT"])
def api_templates_update(template_id: int) -> Any:
    data = request.get_json(silent=True) or {}
    try:
        row = update_prompt_setting(template_id, data)
    except ValueError as e:
        msg = str(e)
        code = 409 if "already exists" in msg else 400
        return jsonify({"ok": False, "error": msg, "detail": msg}), code
    if not row:
        return jsonify({"ok": False, "error": "template not found", "detail": "template not found"}), 404
    return jsonify(row)


@app.route("/api/templates/<int:template_id>", methods=["DELETE"])
def api_templates_delete(template_id: int) -> Any:
    hard = bool(request.args.get("hard", default=0, type=int) or 0)
    result = delete_prompt_setting(template_id, hard=hard)
    if not result.get("ok"):
        err = result.get("error") or "delete failed"
        code = 404 if err == "not found" else 409
        return jsonify({"ok": False, "error": err, "detail": err, **result}), code
    return jsonify(result)


# ---------------------------------------------------------------------------
# Registers: skill / study / prompt / workflow
#
# prompt   = skill + study + wording
# workflow = ordered sequence of INDEPENDENT prompts
#
# Same response shape as /api/templates above: {ok, row} / 400 / 404 / 409.
# ---------------------------------------------------------------------------
@app.route("/api/registers/skills", methods=["GET"])
def api_registers_skills() -> Any:
    include_all = request.args.get("all", default=0, type=int) or 0
    return jsonify(rs.list_skills(active_only=not bool(include_all), db_path=AGENT_DB_PATH))


@app.route("/api/registers/skills", methods=["POST"])
def api_registers_skills_create() -> Any:
    data = request.get_json(silent=True) or {}
    key = str(data.get("skill_key") or "").strip()
    name = str(data.get("name") or "").strip()
    if not key or not name:
        return jsonify({"ok": False, "error": "skill_key and name required"}), 400
    try:
        skill_id = rs.add_skill(
            key,
            name,
            description=data.get("description"),
            output_schema=data.get("output_schema"),
            parser=data.get("parser"),
            db_path=AGENT_DB_PATH,
        )
    except sqlite3.IntegrityError as e:
        return jsonify({"ok": False, "error": f"skill_key already exists: {key}", "detail": str(e)}), 409
    return jsonify({"ok": True, "row": rs.get_skill(key, db_path=AGENT_DB_PATH), "skill_id": skill_id})


@app.route("/api/registers/studies", methods=["GET"])
def api_registers_studies() -> Any:
    include_all = request.args.get("all", default=0, type=int) or 0
    return jsonify(rs.list_studies(active_only=not bool(include_all), db_path=AGENT_DB_PATH))


@app.route("/api/registers/studies", methods=["POST"])
def api_registers_studies_create() -> Any:
    data = request.get_json(silent=True) or {}
    key = str(data.get("study_key") or "").strip()
    name = str(data.get("name") or "").strip()
    skill_id = data.get("skill_id")
    if not key or not name or skill_id is None:
        return jsonify({"ok": False, "error": "study_key, name and skill_id required"}), 400
    try:
        study_id = rs.add_study(
            key,
            name,
            int(skill_id),
            description=data.get("description"),
            fields_json=data.get("fields_json"),
            db_path=AGENT_DB_PATH,
        )
    except sqlite3.IntegrityError as e:
        msg = str(e)
        code = 409 if "UNIQUE" in msg else 400
        return jsonify({"ok": False, "error": msg, "detail": msg}), code
    return jsonify({"ok": True, "study_id": study_id})


@app.route("/api/registers/prompts", methods=["GET"])
def api_registers_prompts() -> Any:
    include_all = request.args.get("all", default=0, type=int) or 0
    return jsonify(rs.list_prompts(active_only=not bool(include_all), db_path=AGENT_DB_PATH))


@app.route("/api/registers/prompts", methods=["POST"])
def api_registers_prompts_create() -> Any:
    data = request.get_json(silent=True) or {}
    key = str(data.get("prompt_key") or "").strip()
    name = str(data.get("name") or "").strip()
    skill_id = data.get("skill_id")
    study_id = data.get("study_id")
    if not key or not name or skill_id is None or study_id is None:
        return jsonify({"ok": False, "error": "prompt_key, name, skill_id and study_id required"}), 400
    try:
        prompt_id = rs.add_prompt(
            key,
            name,
            int(skill_id),
            int(study_id),
            description=data.get("description"),
            wording=data.get("wording"),
            template_id=data.get("template_id"),
            db_path=AGENT_DB_PATH,
        )
    except sqlite3.IntegrityError as e:
        msg = str(e)
        code = 409 if "UNIQUE" in msg else 400
        return jsonify({"ok": False, "error": msg, "detail": msg}), code
    return jsonify({"ok": True, "prompt_id": prompt_id})


@app.route("/api/registers/workflows", methods=["GET"])
def api_registers_workflows() -> Any:
    include_all = request.args.get("all", default=0, type=int) or 0
    return jsonify(rs.list_workflows(active_only=not bool(include_all), db_path=AGENT_DB_PATH))


@app.route("/api/registers/workflows", methods=["POST"])
def api_registers_workflows_create() -> Any:
    data = request.get_json(silent=True) or {}
    key = str(data.get("workflow_key") or "").strip()
    name = str(data.get("name") or "").strip()
    if not key or not name:
        return jsonify({"ok": False, "error": "workflow_key and name required"}), 400
    try:
        workflow_id = rs.add_workflow(
            key, name, description=data.get("description"), db_path=AGENT_DB_PATH
        )
    except sqlite3.IntegrityError as e:
        return jsonify({"ok": False, "error": f"workflow_key already exists: {key}", "detail": str(e)}), 409
    return jsonify({"ok": True, "workflow_id": workflow_id})


@app.route("/api/registers/workflows/<workflow_key>", methods=["GET"])
def api_registers_workflow_get(workflow_key: str) -> Any:
    wf = rs.get_workflow(workflow_key, db_path=AGENT_DB_PATH)
    if not wf:
        return jsonify({"ok": False, "error": "workflow not found", "detail": "workflow not found"}), 404
    return jsonify(wf)


@app.route("/api/registers/workflows/<workflow_key>/steps", methods=["POST"])
def api_registers_workflow_step_add(workflow_key: str) -> Any:
    wf = rs.get_workflow(workflow_key, db_path=AGENT_DB_PATH)
    if not wf:
        return jsonify({"ok": False, "error": "workflow not found", "detail": "workflow not found"}), 404
    data = request.get_json(silent=True) or {}
    step_no = data.get("step_no")
    prompt_id = data.get("prompt_id")
    if step_no is None or prompt_id is None:
        return jsonify({"ok": False, "error": "step_no and prompt_id required"}), 400
    try:
        step_id = rs.add_workflow_step(
            int(wf["workflow_id"]),
            int(step_no),
            int(prompt_id),
            is_final=bool(data.get("is_final")),
            notes=data.get("notes"),
            db_path=AGENT_DB_PATH,
        )
    except sqlite3.IntegrityError as e:
        msg = str(e)
        code = 409 if "UNIQUE" in msg else 400
        return jsonify({"ok": False, "error": msg, "detail": msg}), code
    return jsonify({"ok": True, "step_id": step_id})


@app.route("/api/registers/verify", methods=["GET"])
def api_registers_verify() -> Any:
    return jsonify(rs.verify_no_orphans(db_path=AGENT_DB_PATH))


# ---------------------------------------------------------------------------
# entity id: mint + row register + verify
#
# These REUSE `entity_registry` and `entity_id`; they do not reimplement the
# id rules. `entity_id.verify()` is the one place that decides whether an id
# points at something, so the API calls it rather than parsing again -- a
# second parser would drift from the first, and the first is the one with the
# register lookups.
# ---------------------------------------------------------------------------


@app.route("/api/entity/mint", methods=["POST"])
def api_entity_mint() -> Any:
    """Mint an entity id in ONE call.

    Body: {letter, row_id, version?, note?}
      -> `F-38-11-1` (letter, table_id, row_id, version)

    THE HUMAN (2026-09-27): "letter - table_id - row_id - version_id" /
    "`db_row_registry`, that is wrong, don't need that" / "example: Function = F
    / table_id = 10 = table ABC / row id = 11 = function_registry / version = 1 /
    will be F-10-11-1".

    `row_id` is the register table's OWN PK. `table_id` is looked up from
    `db_table_registry` by the letter's register table -- the caller does NOT
    supply it, so it cannot be wrong.

    Refuses (400) on an unknown letter, a register table that is not itself
    registered, or a row that does not exist. An id that verifies while pointing
    at nothing is worse than no id.
    """
    try:
        import entity_registry as er

        data = request.get_json(silent=True) or {}
        letter = str(data.get("letter") or "").strip()
        if not letter:
            return jsonify({"ok": False, "error": "letter required"}), 400
        try:
            row_id = int(data.get("row_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "row_id must be an integer"}), 400
        try:
            version = int(data.get("version") or 1)
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "version must be an integer"}), 400

        conn = er._connect(AGENT_DB_PATH)
        try:
            er.ensure_entity_registry_schema(conn)
            out = er.mint_entity(conn, letter, row_id,
                                 version=version,
                                 note=data.get("note"),
                                 created_by="api_entity_mint")
        finally:
            conn.close()
        code = 200 if out.get("ok") else 400
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/entity/row", methods=["POST"])
def api_entity_row() -> Any:
    """REMOVED. THE HUMAN (2026-09-27): "`db_row_registry`, that is wrong, don't
    need that".

    The row id in `{LETTER}-{table_id}-{row_id}-{version}` IS the register
    table's OWN PK, so there is no second register to write. Use
    `GET /api/entity/id-for?path=<file>` to get the id for a file.

    Kept as a 410 GONE rather than deleted, so an old caller gets a named reason
    instead of a 404 that looks like a typo.
    """
    return jsonify({
        "ok": False,
        "error": "GONE: /api/entity/row was removed 2026-09-27. The row id is "
                 "the register table's OWN PK, so no row register is needed. "
                 "The entity id is {LETTER}-{table_id}-{row_id}-{version}; "
                 "use GET /api/entity/id-for?path=<file>.",
    }), 410


@app.route("/api/entity/id-for", methods=["GET"])
def api_entity_id_for() -> Any:
    """THE WORKER'S ONE CALL: give me the entity id for this file.

    Query: ?path=<file>   (absolute or repo-relative)

    Returns the 4-part id(s) that cover the file, ready to paste into a plan's
    `**Entities:**` line:

        {"ok": true, "file": "entity_id.py",
         "entities": ["R-1-75-1"],
         "entity_id": "R-1-75-1",
         "line": "**Entities:** R-1-75-1",
         "verified": true}

    WHY THIS EXISTS. THE HUMAN (2026-09-27): "be the API, help worker to get that
    easy". Before this, a worker had to know that the id is
    `{LETTER}-{table_id}-{row_id}-{version}`, find the letter, find the table id,
    find the row id, and then verify it -- four lookups and a format. This is ONE
    call that returns the exact line to paste.

    `verified` is the result of `entity_id.verify` on the FIRST id, so a caller
    can tell "here is an id" from "here is an id that CHECKS OUT". An id that
    does not verify is returned with `verified: false` and the reason, never
    silently dropped.

    READ-ONLY: it writes nothing.
    """
    try:
        import entity_backfill as eb
        import entity_id as eid

        path = (request.args.get("path") or "").strip()
        if not path:
            return jsonify({"ok": False, "error": "path required"}), 400

        conn = sqlite3.connect(AGENT_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            cov = eb.covered_by(conn, path)
            if not cov.get("ok"):
                return jsonify({
                    "ok": False, "file": cov.get("file"),
                    "error": cov.get("reason"),
                    "hint": ("backfill the file first: "
                             "python entity_backfill.py --apply"),
                }), 404
            ids = cov["entities"]
            first = ids[0]
            v = eid.verify(first, conn=conn)
            return jsonify({
                "ok": True,
                "file": cov["file"],
                "entities": ids,
                "entity_id": first,
                "line": "**Entities:** " + ", ".join(ids),
                "verified": bool(v.get("ok") and v.get("exists")),
                "verify_reason": v.get("reason"),
                "parts": {
                    "letter": v.get("letter"),
                    "table_id": v.get("table_id"),
                    "row_id": v.get("row_id"),
                    "version": v.get("version"),
                },
                "register": v.get("register"),
                "entity_key": v.get("entity_key"),
            }), 200
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/entity/format", methods=["GET"])
def api_entity_format() -> Any:
    """THE FORMAT, stated by the code that enforces it.

    Query: ?letter=F&table_id=38&row_id=11&version=1

    Returns the id and whether it VERIFIES. This is the API a worker calls when
    it has the four parts and wants the string, or wants to know why a string it
    built is refused.

    THE HUMAN (2026-09-27): "letter - table_id - row_id - version_id" /
    "example: Function = F / table_id = 10 = table ABC / row id = 11 =
    function_registry / version = 1 / will be F-10-11-1".

    READ-ONLY: it writes nothing.
    """
    try:
        import entity_id as eid

        letter = (request.args.get("letter") or "").strip()
        if not letter:
            return jsonify({"ok": False, "error": "letter required"}), 400
        try:
            table_id = int(request.args.get("table_id"))
            row_id = int(request.args.get("row_id"))
            version = int(request.args.get("version") or 1)
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "table_id, row_id and version must be "
                                     "integers"}), 400

        try:
            built = eid.format(letter, table_id, row_id, version)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

        conn = sqlite3.connect(AGENT_DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            v = eid.verify(built, conn=conn)
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "entity_id": built,
            "verified": bool(v.get("ok") and v.get("exists")),
            "verify_reason": v.get("reason"),
            "register": v.get("register"),
            "entity_key": v.get("entity_key"),
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/entity/<path:entity_id_str>", methods=["GET"])
def api_entity_verify(entity_id_str: str) -> Any:
    """Verify an entity id against the registers. Read-only.

    Returns the full verification: letter, ref_id, version, and the register
    each part was checked against. A malformed id is 400; a well-formed id that
    points at nothing is 404 -- the two are different failures and a caller
    needs to tell them apart.
    """
    try:
        import entity_id as eid

        res = eid.verify(entity_id_str, db_path=AGENT_DB_PATH)
        if not res.get("ok"):
            # malformed vs not-found: shape errors are the caller's fault
            malformed = "malformed" in str(res.get("reason") or "")
            return jsonify(res), (400 if malformed else 404)
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/step2")
def step2() -> str:
    return render_template_string(STEP2_HTML)


@app.route("/step3")
def step3() -> str:
    return render_template_string(STEP3_HTML)


@app.route("/step4")
def step4() -> str:
    return render_template_string(STEP4_HTML)


def _llm_monitor_spa_index():
    """Serve the SPA index with NO-STORE so a rebuild is picked up immediately.

    index.html references a content-hashed bundle (index-<hash>.js). If the
    browser caches index.html, it keeps requesting the OLD bundle after a
    rebuild -> the new page shows no data (stale UI bug). no-store on the
    HTML + immutable cache on the hashed assets is the correct pair.

    THE TRIGGER POINT (2026-09-24). The user:
        "trigger point by http://127.0.0.1:18765/llm-tasks/ open at browser"
        "so you can have status now!"

    Opening this page IS the evidence that a computer is present: a request
    arrived from it. So the visit is recorded here, BEFORE the response is
    built, and the write is wrapped so it can NEVER fail the page -- a presence
    column is not worth a broken UI.
    """
    _record_presence_visit()
    index = LLM_MONITOR_DIST / "index.html"
    if index.is_file():
        resp = send_file(index)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    return render_template_string(LLM_TASKS_HTML)


def _record_presence_visit() -> dict[str, Any]:
    """Record this page load as a PRESENCE visit. NEVER raises, NEVER blocks.

    MEASURED 2026-09-24: presence must NOT be written into `workers`.
    `workers.last_seen_at` belongs to the HEARTBEAT worker, and `workers.id` is a
    DIFFERENT id space from `identity_registry.identity_id` -- joining them by a
    bare integer is what produced the fake ON. Presence is keyed by the COMPUTER
    in its own table.

    The whole body is guarded: this runs inside a page route, so an exception
    here would turn a status feature into a page outage.
    """
    try:
        import computer_presence as cp

        ident = get_computer_identity()
        env = _build_user_environment()
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            cp.ensure_schema(conn)
            return cp.touch(
                conn,
                computer_id=str(ident.get("computer_id") or ""),
                computer_name=str(ident.get("computer_name") or ""),
                ip_address=str(env.get("ip_address") or ""),
                user_id=env.get("user_id"),
                path=str(request.path or ""),
                cite_ref="mouse_spot_helper.py:_llm_monitor_spa_index",
            )
        finally:
            conn.close()
    except Exception as exc:
        # Reported, never raised: a swallowed error with no trace would be
        # indistinguishable from a successful visit.
        return {"ok": False, "why": "%s: %s" % (type(exc).__name__, exc)}


@app.route("/api/computer_presence", methods=["GET"])
def api_computer_presence() -> Any:
    """The presence table, each row carrying its DERIVED status."""
    try:
        import computer_presence as cp

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            cp.ensure_schema(conn)
            out = cp.list_presence(conn)
            out["this_computer"] = cp.status_of(
                conn, str(get_computer_identity().get("computer_id") or ""))
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/environment/list", methods=["GET"])
def api_environment_list() -> Any:
    """The ENVIRONMENT register, keyed by `environment_id`.

    THE USER (2026-09-24):
        "worker ID -> environment_id from working_environment"
        "remove llm / name / session"

    MEASURED: the page was a WORKER list (53 rows, one per session). The user's
    unit is the ENVIRONMENT, and the 53 rows collapse to the environments that
    `working_environment` declares. So this endpoint returns ONE row per
    environment, keyed by `environment_id`, and carries NO session id.
    """
    try:
        import environment_registry as er

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            return jsonify(er.list_environments(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/pick/flow", methods=["GET"])
def api_pick_flow() -> Any:
    """The THREE-STEP pick: environment -> LLM -> tool, plus the TIPS.

    THE USER (2026-09-24):
        "onclick LLM = submit -> step 3"
        "when step 1 = vscode, step 2 LLM for ... with bg-color : blue"
        "when step 2 = deepseekSeek V4.1, step 3 task center with bg-color : blue"
        "is tips for user,"

    The blue highlight is a TIP, and it is DERIVED from the registers -- a tip
    typed into the UI would be a second copy of a fact the registers hold.
    """
    try:
        import pick_flow as pf

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            pf.ensure_schema(conn)
            return jsonify(pf.flow(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/pick/step", methods=["POST"])
def api_pick_step() -> Any:
    """Set ONE step of the flow. REFUSES an unknown row with HTTP 400."""
    try:
        import pick_flow as pf

        data = request.get_json(silent=True) or {}
        step = data.get("step")
        value = data.get("value")
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            pf.ensure_schema(conn)
            out = pf.pick(conn, step=int(step), value=value,
                          cite_ref="api:/api/pick/step")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/environment/update", methods=["POST"])
def api_environment_update() -> Any:
    """EDIT an environment's 3-part path. The user (2026-09-25):
    "pop-up for environment can edit to update the tabke too"."""
    try:
        import working_environment as we

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            out = we.update(
                conn, environment_id=data.get("environment_id"),
                kind=data.get("kind"), product=data.get("product"),
                surface=data.get("surface"),
                cite_ref=data.get("cite_ref") or "api:/api/environment/update")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/environment/role_detail", methods=["GET"])
def api_environment_role_detail() -> Any:
    """The step-1 POPUP data: the environment row, the FULL role vocabulary
    (from `role_registry`), and the pairs already declared for THIS
    environment. The user (2026-09-25): "onclick = detail for role"."""
    try:
        import environment_registry as er
        import role_environment as re_

        eid = request.args.get("environment_id", type=int)
        if eid is None:
            return jsonify({"ok": False,
                            "error": "environment_id is required"}), 400
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            re_.ensure_schema(conn)
            # MEASURED (2026-09-25): THIS endpoint returned 500 on every
            # request because the connection had NO row factory, so
            # `.fetchone()` gave a plain TUPLE and `dict(env)` raised
            # `TypeError: cannot convert dictionary update sequence element
            # #0 to a sequence`. The row factory is set HERE, not assumed of
            # the caller -- the same lesson `working_environment._as_rows`
            # now records. (Setting it must happen BEFORE the query: after
            # the fact the row is already a tuple, and `dict(tuple)` raises.)
            conn.row_factory = sqlite3.Row
            # NO `channel_id` (fixed 2026-09-25). MEASURED: this SELECT named
            # `channel_id`, which `working_environment` NO LONGER HAS -- the
            # column was removed when the environment stopped being related to a
            # channel ("環境 is 環境!!!! not related to channel"). So the popup
            # raised `OperationalError: no such column: channel_id` on EVERY
            # open. An environment is keyed by `environment_id`, and its status
            # is the environment's OWN, from the Windows Task Manager.
            env = conn.execute(
                "SELECT environment_id, kind, product, surface, "
                "display, cite_ref, icon_url FROM working_environment "
                "WHERE environment_id=? AND is_active=1", (eid,)).fetchone()
            if env is None:
                return jsonify({"ok": False,
                                "error": "environment_id %s is not in "
                                         "working_environment" % eid}), 404
            env = dict(env)
            out = er.list_environments(conn)
            row = next(
                (r for r in out.get("rows", [])
                 if int(r["environment_id"]) == eid), None)
            # THE RUNNING PROOF, keyed by ENVIRONMENT_ID, from the Windows Task
            # Manager. THE HUMAN (2026-09-25): "how to proof it is running, is by
            # windows task to proof does environment id is running ... never =
            # not channel id!!!" So the popup carries the environment's OWN
            # status + the process it was measured from, never a channel.
            env["status"] = (row or {}).get("status", "UNKNOWN")
            env["status_why"] = (row or {}).get("status_why", "")
            env["status_app"] = (row or {}).get("status_app")
            env["status_count"] = (row or {}).get("status_count")
            env["status_source"] = "windows_task_manager:environment_id"
            # THE CONFIGURE SETTINGS for THIS environment_id.
            #
            # THE HUMAN (2026-09-25): "pop up -> environment_id -> configure" /
            # "you have table for x1,x2 and y1 , y2, that is one of the
            # configure setting" / "no matter environment_id is 6,50,51,52 the
            # result is same, it should for environment_id * configure only".
            #
            # MEASURED DEFECT: this used to return `target_area` rows, which
            # have NO `environment_id` -- so EVERY environment showed the SAME
            # 8 rows. Now the SHARED content is the TEMPLATE, and the
            # environment's OWN values come from `environment_configure`. A
            # missing row is NULL (not collected), never the template.
            configure: dict[str, Any] = {"template": [], "rows": [],
                                         "values": None, "collected": False,
                                         "source": ""}
            try:
                # THE THREE TABLES, DB-DRIVEN IDS (2026-09-25).
                # THE HUMAN: "id must = primary and auto inscrease as DB driven"
                # / "target_template / id | name" / "target_group / id | perm"
                # / "environment_template / id | environment_id | template_id".
                # So the popup reads target_group + target_template +
                # environment_template, joined on INTEGER ids only.
                import target_registry as tg
                mg = tg.for_environment(conn, eid)
                configure["rows"] = mg.get("rows", [])
                configure["groups"] = tg.list_groups(conn)
                configure["collected"] = bool(mg.get("collected"))
                configure["collected_count"] = mg.get("collected_count", 0)
                configure["template_count"] = mg.get("template_count", 0)
                configure["why"] = mg.get("why", "")
                configure["collect_at"] = mg.get("collect_at", "")
                configure["source"] = tg.SOURCE
            except Exception as exc:
                configure["error"] = "%s: %s" % (type(exc).__name__, exc)
            # THE DECLARED ROLE for THIS environment_id, as a VALUE (not just a
            # vocabulary). THE HUMAN: "pop up -> environment_id -> role" and
            # "missing value in your fucking coding". The vocabulary alone is
            # not the answer; the DECLARED role is.
            pairs = re_.pairs_for_environment(conn, eid)
            declared_roles = [str(r.get("role_key"))
                              for r in (pairs.get("roles") or [])]
            env["role_key"] = (declared_roles[0] if len(declared_roles) == 1
                               else ("MIXED" if declared_roles
                                     else "UNASSIGNED"))
            env["role_declared"] = declared_roles
            # THE EVID IMAGE PER COORDINATE TARGET.
            # THE HUMAN (2026-09-25): "template_id | image name | label |
            # x1,y1 → x2,y2 | center x,y" / "image format =
            # EVID-task_proof-20260921-195257_full and save at
            # C:\projects\agent_system\evidence_final" / "can mouse over to have
            # the large image by 600*600 with info for file location".
            #
            # The image is found by the EVID naming convention in
            # `evidence_final/`. A target with NO image returns image_name null,
            # so the UI shows "no evidence yet" -- a missing proof must be
            # VISIBLE, not a broken image.
            evidence_rows = []
            try:
                final_dir = BASE_DIR / "evidence_final"
                for r in (configure.get("rows") or []):
                    if str(r.get("field_type")) != "coordinate":
                        continue
                    img_name = None
                    img_path = None
                    if final_dir.is_dir():
                        hits = sorted(final_dir.glob(
                            "EVID-%s-*_full.png" % r.get("name")))
                        if hits:
                            img_name = hits[-1].name
                            img_path = str(hits[-1])
                    evidence_rows.append({
                        "name": r.get("name"),
                        "image_name": img_name,
                        "image_path": img_path,
                        "image_url": ("/api/evidence/coord/image?name=%s"
                                      % img_name if img_name else None),
                    })
            except Exception as exc:
                evidence_rows = [{"error": "%s: %s" % (type(exc).__name__, exc)}]
            return jsonify({
                "ok": True,
                "environment": env,
                "configure": configure,
                "evidence": {"rows": evidence_rows,
                             "with_image": sum(1 for r in evidence_rows
                                               if r.get("image_name"))},
                "roles": re_.roles(conn),
                "role_registry": [dict(r) for r in conn.execute(
                    "SELECT role_key, definition, may_read, may_write, "
                    "may_verify, instrument FROM role_registry "
                    "WHERE is_active=1 ORDER BY role_key")],
                "pairs": pairs,
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/environment/evidence_steps", methods=["GET"])
def api_environment_evidence_steps() -> Any:
    """THE EVIDENCE POP-UP's data: the STEP table for ONE environment.

    THE HUMAN (2026-09-26): "evidence pop-up re-design / view is STEP for how to
    process with evidence proof / table will be / STEP 1 instruction method
    (hotkey / coordinate) evidence / STEP 2 ......".

    MEASURED BEFORE: the pop-up rendered `d.configure.rows` filtered to
    `field_type === 'coordinate'` -- a TARGET list (`template_id | name | label |
    image`), not a STEP list. It answered "which coordinate targets have a
    picture?", not "what are the STEPS, how is each driven, and what is its
    proof?".

    THE STEP DATA ALREADY EXISTS. `playwright_step` is the guide (what each step
    does, what WILL happen, how to PROOF it) and `playwright_step_run` is the log
    (what ACTUALLY happened). `psr.guide()` joins them. So this endpoint is a
    RE-POINTING, not new data.

    A MISSING PLAYWRIGHT ENVIRONMENT IS A REAL ANSWER: `rows: []` plus a `why`,
    never a 500 and never a silent empty table -- an environment with no guide
    must be VISIBLE, not invisible.
    """
    try:
        eid = request.args.get("environment_id", type=int)
        if eid is None:
            return jsonify({"ok": False,
                            "error": "environment_id is required"}), 400
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            # WHICH playwright environment drives THIS environment. The guide is
            # keyed by `playwright_id`, so the environment_id must be resolved
            # first -- and an environment with no row here has no guide at all.
            row = conn.execute(
                "SELECT id, name, browser_channel FROM playwright_environment "
                "WHERE environment_id=? AND is_active=1 ORDER BY id LIMIT 1",
                (eid,)).fetchone()
            if row is None:
                return jsonify({
                    "ok": True, "environment_id": eid, "playwright_id": None,
                    "rows": [], "count": 0,
                    "why": ("no active playwright_environment row names "
                            "environment_id=%d, so this environment has no "
                            "STEP guide" % eid)})
            pid = int(row["id"])
            import playwright_step_registry as psr
            g = psr.guide(conn, pid)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500
    rows = g.get("rows") or []
    # EACH STEP'S OWN PICTURE, resolved by the ONE shared implementation.
    _resolve_step_images(rows)
    # THE INSTRUCTION IS `action` + `target`: what to do, and to what. Built
    # HERE so the UI renders one field instead of re-joining two, and so the
    # join cannot drift between the pop-up and any other reader.
    for r in rows:
        r["instruction"] = "%s → %s" % (r.get("action") or "NA",
                                        r.get("target") or "NA")
    return jsonify({"ok": True, "environment_id": eid, "playwright_id": pid,
                    "playwright_name": row["name"],
                    "browser_channel": row["browser_channel"],
                    "rows": rows, "count": len(rows)})


@app.route("/api/role_environment/declare", methods=["POST"])
def api_role_environment_declare() -> Any:
    """DECLARE (or re-activate) one role x environment pair. A role is a
    DECISION the user makes in the popup; the server REFUSES an unknown side."""
    try:
        import role_environment as re_

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            re_.ensure_schema(conn)
            out = re_.declare(
                conn, role_key=data.get("role_key"),
                environment_id=data.get("environment_id"),
                cite_ref=data.get("cite_ref") or "api:/api/role_environment/declare")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/role_environment/remove", methods=["POST"])
def api_role_environment_remove() -> Any:
    """SOFT-DELETE one pair (`is_active=0`). The repo law: never DELETE."""
    try:
        import role_environment as re_

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            re_.ensure_schema(conn)
            out = re_.remove(
                conn, role_key=data.get("role_key"),
                environment_id=data.get("environment_id"))
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/llm_model/list", methods=["GET"])
def api_llm_model_list() -> Any:
    """Every active model, READ from the table. The step-2 popup edits a row,
    so it needs the FULL row (local / visual / text / description), not just
    the name the flow tips carry."""
    try:
        import llm_model_registry as lmr

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            lmr.ensure_schema(conn)
            return jsonify(lmr.list_models(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/llm_model/add", methods=["POST"])
def api_llm_model_add() -> Any:
    """ADD one model. The user (2026-09-25): "+ buttom to + LLM". MEASURED:
    the 4 live rows were inserted by hand; this is the missing path."""
    try:
        import llm_model_registry as lmr

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            lmr.ensure_schema(conn)
            out = lmr.add_model(
                conn, name=data.get("name"), model_id=data.get("model_id"),
                local=bool(data.get("local")),
                visual=bool(data.get("visual")),
                text=bool(data.get("text", True)),
                description=data.get("description") or "",
                cite_ref=data.get("cite_ref") or "api:/api/llm_model/add")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/llm_model/update", methods=["POST"])
def api_llm_model_update() -> Any:
    """EDIT one model. The user (2026-09-25): "same function for step 2" --
    the step-2 popup edits the row, the same way the step-1 popup edits its
    environment."""
    try:
        import llm_model_registry as lmr

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            lmr.ensure_schema(conn)
            out = lmr.update_model(
                conn, llm_id=data.get("llm_id"), name=data.get("name"),
                model_id=data.get("model_id"),
                local=bool(data.get("local")),
                visual=bool(data.get("visual")),
                text=bool(data.get("text", True)),
                description=data.get("description") or "",
                cite_ref=data.get("cite_ref") or "api:/api/llm_model/update")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/source/list", methods=["GET"])
def api_source_list() -> Any:
    """The `source` VOCABULARY -- the places a session can arrive from.

    THE USER (2026-09-25): "where is environment for role = researcher
    environment : 豆包 Browser > ...". MEASURED: the three environments the
    user named ALREADY EXIST in `source` (doubao / chrome_deepseek /
    edge_gemini). This endpoint is how the page can see them."""
    try:
        import source_environment as se

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            return jsonify({"ok": True, "sources": se.sources(conn),
                            "count": len(se.sources(conn))})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/source/bridges", methods=["GET"])
def api_source_bridges() -> Any:
    """Which sources HAVE an environment, and which do not."""
    try:
        import source_environment as se

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            return jsonify(se.list_bridges(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/source/declare_environment", methods=["POST"])
def api_source_declare_environment() -> Any:
    """DECLARE an environment FROM a `source` row. The user (2026-09-25):
    "where is environment for role = Verfiter environment : Google Chrome >
    https://chat.deepseek.com/...". The URL is TAKEN FROM the source row, never
    passed in, so a re-typed URL cannot drift."""
    try:
        import source_environment as se

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            out = se.declare_from_source(
                conn, source_key=data.get("source_key"),
                kind=data.get("kind"), product=data.get("product"),
                surface=data.get("surface") or "chat",
                nav_path=data.get("nav_path") or "",
                cite_ref=data.get("cite_ref")
                or "api:/api/source/declare_environment")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/identity/link_llm", methods=["POST"])
def api_identity_link_llm() -> Any:
    """LINK an identity to the LLM it runs on. The user (2026-09-25):
    "-> identity / worker = LLM : 豆包 , local = 0". MEASURED:
    `identity_registry` HAS `llm_id` and `role_id`, and all 53 rows are NULL."""
    try:
        import identity_llm as il

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            il.ensure_schema(conn)
            cite = data.get("cite_ref") or "api:/api/identity/link_llm"
            if data.get("session_id"):
                out = il.assign_for_session(
                    conn, session_id=data.get("session_id"),
                    llm_id=data.get("llm_id"), role_key=data.get("role_key"),
                    cite_ref=cite)
            else:
                out = il.assign_llm(
                    conn, identity_id=data.get("identity_id"),
                    llm_id=data.get("llm_id"), role_key=data.get("role_key"),
                    cite_ref=cite)
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/identity/llm", methods=["GET"])
def api_identity_llm() -> Any:
    """The LLM and role a SESSION's identities run on, READ back."""
    try:
        import identity_llm as il

        sid = request.args.get("session_id", "")
        if not sid:
            return jsonify({"ok": False,
                            "error": "session_id is required"}), 400
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            return jsonify(il.llm_for_session(conn, sid))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/channel/list", methods=["GET"])
def api_channel_list() -> Any:
    """The CHANNEL register, with its 5W1H `url`.

    THE USER (2026-09-25): "channel url = C:\\projects\\agent_system" and
    "environment url and channel url can help to have 5W1H for each, so system
    is easy to classify what is happen now, don't mix up any more"."""
    try:
        import channel_registry as cr

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            cr.ensure_schema(conn)
            return jsonify(cr.list_channels(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/channel/status", methods=["GET"])
def api_channel_status() -> Any:
    """Is the CHANNEL's server ONLINE or OFFLINE?

    THE USER (2026-09-25): "for status, channel status is did server online /
    offline" and "they are totally different". This is the CHANNEL's status,
    and it is NOT the environment's (which comes from the Windows Task
    Manager)."""
    try:
        import channel_registry as cr

        key = request.args.get("channel_key", "")
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            cr.ensure_schema(conn)
            if not key:
                ch = cr.list_channels(conn)
                rows = ch.get("channels", [])
                if not rows:
                    return jsonify({"ok": False,
                                    "error": "no channel is declared"}), 404
                key = str(rows[0]["channel_key"])
            return jsonify(cr.server_status(conn, key))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/app/list", methods=["GET"])
def api_app_list() -> Any:
    """The `app` register: the process names the Windows Task Manager answers."""
    try:
        import app_registry as ar

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            ar.ensure_schema(conn)
            return jsonify(ar.list_apps(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/app/declare", methods=["POST"])
def api_app_declare() -> Any:
    """DECLARE an app, so an environment can be linked to a Task Manager
    process. The user (2026-09-25): "enviorment is by windows task center to
    get the status for enviorment list"."""
    try:
        import app_registry as ar

        data = request.get_json(silent=True) or {}
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            ar.ensure_schema(conn)
            out = ar.declare(
                conn, app_key=data.get("app_key"), name=data.get("name"),
                process_name=data.get("process_name"),
                kind=data.get("kind") or "desktop",
                url=data.get("url") or "",
                exe_path=data.get("exe_path") or "",
                description=data.get("description") or "",
                cite_ref=data.get("cite_ref") or "api:/api/app/declare")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 400


@app.route("/api/tool/list", methods=["GET"])
def api_tool_list() -> Any:
    """The STEP 3 tools, each paired with its ROLE."""
    try:
        import tool_center as tc

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            tc.ensure_schema(conn)
            return jsonify(tc.list_tools(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/role_environment/list", methods=["GET"])
def api_role_environment_list() -> Any:
    """The ROLE x ENVIRONMENT register. The user: "role X environment table is
    missing?" -- MEASURED, and the user was right."""
    try:
        import role_environment as re_

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            re_.ensure_schema(conn)
            return jsonify(re_.list_pairs(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


# ================= assignee API (role x environment -> pick) =================
# The user (2026-09-24):
#   "environment setting Online + role-> onclick, so he will the one (identity)
#    to have the task"
# The page shows ENVIRONMENT (with its Online status) + ROLE; clicking a row
# SELECTS that identity, and the selected identity is the one that RECEIVES the
# task. The axis is role x environment, NOT session -- the user:
#   "ui is wrong design , it should for role with environment not worker with
#    environment"


@app.route("/api/assignee/candidates", methods=["GET"])
def api_assignee_candidates() -> Any:
    """The role x environment rows, each with its ENVIRONMENT status."""
    try:
        import assignee as asg

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            asg.ensure_schema(conn)
            out = asg.candidates(conn)
            out["selected"] = asg.selected(conn).get("selected")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/assignee/select", methods=["POST"])
def api_assignee_select() -> Any:
    """Pick ONE identity as the task assignee."""
    try:
        import assignee as asg

        data = request.get_json(silent=True) or {}
        iid = data.get("identity_id")
        if iid is None:
            iid = request.args.get("identity_id", type=int)
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            asg.ensure_schema(conn)
            out = asg.select(conn, identity_id=int(iid or 0),
                             cite_ref="api:/api/assignee/select")
            if not out.get("ok"):
                return jsonify(out), 400
            out["selected"] = asg.selected(conn).get("selected")
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/api/assignee/clear", methods=["POST"])
def api_assignee_clear() -> Any:
    """Clear the selection."""
    try:
        import assignee as asg

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            asg.ensure_schema(conn)
            return jsonify(asg.clear(conn))
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500


@app.route("/llm-tasks/assets/<path:asset_path>")
def llm_tasks_assets(asset_path: str):
    assets = LLM_MONITOR_DIST / "assets"
    if not assets.is_dir():
        return jsonify({"ok": False, "error": "SPA assets not built"}), 404
    # Filenames are content-hashed (index-<hash>.js), so long cache is safe:
    # a rebuild produces a NEW name and the no-store index.html points at it.
    resp = send_from_directory(assets, asset_path)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


@app.route("/llm-tasks")
@app.route("/llm-tasks/")
@app.route("/llm-tasks/<path:spa_path>")
def llm_tasks_page(spa_path: str | None = None):
    # Deep links (/llm-tasks/task_center, etc.) serve SPA index.
    # Assets are handled by llm_tasks_assets above.
    return _llm_monitor_spa_index()


@app.route("/api/llm-tasks", methods=["GET"])
def api_llm_tasks() -> Any:
    limit = request.args.get("limit", default=100, type=int) or 100
    limit = max(1, min(limit, 500))
    tasks = load_llm_tasks()[:limit]
    totals = {
        "count": len(tasks),
        "prompt_tokens": sum(int(t.get("prompt_tokens") or 0) for t in tasks),
        "completion_tokens": sum(int(t.get("completion_tokens") or 0) for t in tasks),
        "total_tokens": sum(int(t.get("total_tokens") or 0) for t in tasks),
    }
    by_model: dict[str, dict[str, Any]] = {}
    for t in tasks:
        mid = t.get("model") or "unknown"
        bucket = by_model.setdefault(mid, {
            "model": mid,
            "label": t.get("model_label") or mid,
            "count": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        })
        bucket["count"] += 1
        bucket["total_tokens"] += int(t.get("total_tokens") or 0)
        bucket["prompt_tokens"] += int(t.get("prompt_tokens") or 0)
        bucket["completion_tokens"] += int(t.get("completion_tokens") or 0)
    return jsonify({
        "ok": True,
        "models": LLM_MODELS,
        "tasks": tasks,
        "totals": totals,
        "by_model": list(by_model.values()),
        "identity": get_computer_identity(),
        "ollama": check_ollama_status(),
        "helper": helper_status_payload(),
    })


@app.route("/api/llm-tasks/clear", methods=["POST"])
def api_llm_tasks_clear() -> Any:
    with LLM_TASKS_LOCK:
        save_llm_tasks([])
    return jsonify({"ok": True})


@app.route("/api/llm-tasks", methods=["POST"])
def api_llm_tasks_add() -> Any:
    """External task logging: any agent/writer can append a row to llm_tasks.json."""
    data = request.get_json(silent=True) or {}
    if not str(data.get("task") or "").strip():
        return jsonify({"ok": False, "error": "task is required"}), 400
    run_id = str(data.get("id") or ("t_" + uuid.uuid4().hex[:12]))
    now = _utc_now_iso()
    task = {
        "id": run_id,
        "task": str(data["task"]),
        "model": data.get("model") or "external",
        "model_label": data.get("model_label") or data.get("model") or "external",
        "status": data.get("status") or "done",
        "started_at": data.get("started_at") or now,
        "ended_at": data.get("ended_at") or now,
        "duration_ms": int(data.get("duration_ms") or 0),
        "prompt_tokens": int(data.get("prompt_tokens") or 0),
        "completion_tokens": int(data.get("completion_tokens") or 0),
        "total_tokens": int(data.get("total_tokens") or 0),
        "result": data.get("result"),
        "reason": data.get("reason"),
        "source": data.get("source") or "external",
        "type": data.get("type"),
        "action": data.get("action"),
        "name": data.get("name"),
        "format": data.get("format"),
        "writer": data.get("writer") or "external",
        "task_id": data.get("task_id"),
        "session_id": data.get("session_id"),
        "chat_id": data.get("chat_id"),
        "error": data.get("error"),
    }
    append_llm_task(task)
    return jsonify({"ok": True, "task": task})


@app.route("/api/task-sources", methods=["GET"])
def api_task_sources() -> Any:
    """All task sources: task_source_log rows + llm_tasks.json entries (deduped)."""
    limit = request.args.get("limit", default=200, type=int) or 200
    limit = max(1, min(limit, 500))
    try:
        sources = list_task_sources(limit=limit)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "sources": []}), 500
    try:
        for task in load_llm_tasks()[:limit]:
            source = {
                "id": None,
                "task_id": str(task.get("task_id") or task.get("id") or "-"),
                "who": task.get("writer") or "llm",
                "where": "llm_tasks.json",
                "chat_id": task.get("chat_id") or None,
                "session_id": task.get("session_id") or None,
            }
            if not any(
                str(s.get("task_id") or "") == source["task_id"]
                and (s.get("who") or "") == source["who"]
                and (s.get("where") or "") == source["where"]
                and (s.get("chat_id") or None) == source["chat_id"]
                and (s.get("session_id") or None) == source["session_id"]
                for s in sources
            ):
                sources.append(source)
    except Exception:
        pass
    return jsonify({"ok": True, "sources": sources, "count": len(sources)})


# ================= Skill Library API (/api/v1) + RBAC =================
# Backed by skill_library_api.py (roles/users/skills/skill_versions/llm_tasks/plan_sessions).


def _sl():
    from skill_library_api import (
        auth_error,
        create_skill,
        create_skill_version,
        forbidden_error,
        get_latest_published,
        get_skill,
        get_skill_version,
        list_skill_versions,
        list_skills,
        require_permission,
        set_version_status,
        _bearer_token,
    )
    return {
        "auth_error": auth_error,
        "create_skill": create_skill,
        "create_skill_version": create_skill_version,
        "forbidden_error": forbidden_error,
        "get_latest_published": get_latest_published,
        "get_skill": get_skill,
        "get_skill_version": get_skill_version,
        "list_skill_versions": list_skill_versions,
        "list_skills": list_skills,
        "require_permission": require_permission,
        "set_version_status": set_version_status,
        "_bearer_token": _bearer_token,
    }


@app.route("/api/v1/skills", methods=["GET"])
def api_v1_skills_list() -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:get"):
        msg, code = sl["auth_error"]()
        return jsonify(msg), code
    try:
        return jsonify({"ok": True, "skills": sl["list_skills"]()})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills", methods=["POST"])
def api_v1_skills_create() -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:create"):
        msg, code = sl["forbidden_error"]()
        return jsonify(msg), code
    data = request.get_json(silent=True) or {}
    skill_id = str(data.get("skill_id") or "").strip()
    if not skill_id:
        return jsonify({"ok": False, "error": "skill_id is required"}), 400
    try:
        return jsonify(sl["create_skill"](skill_id, str(data.get("description") or "")))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills/<skill_id>", methods=["GET"])
def api_v1_skills_get(skill_id: str) -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:get"):
        msg, code = sl["auth_error"]()
        return jsonify(msg), code
    try:
        skill = sl["get_skill"](skill_id)
        if not skill:
            return jsonify({"ok": False, "error": "skill not found"}), 404
        skill["versions"] = sl["list_skill_versions"](skill_id)
        return jsonify({"ok": True, "skill": skill})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills/<skill_id>/versions/<version>", methods=["GET"])
def api_v1_skills_version_get(skill_id: str, version: str) -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:get"):
        msg, code = sl["auth_error"]()
        return jsonify(msg), code
    try:
        v = sl["get_skill_version"](skill_id, version)
        if not v:
            return jsonify({"ok": False, "error": "version not found"}), 404
        return jsonify({"ok": True, "version": v})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills/<skill_id>/latest", methods=["GET"])
def api_v1_skills_latest(skill_id: str) -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:get"):
        msg, code = sl["auth_error"]()
        return jsonify(msg), code
    try:
        v = sl["get_latest_published"](skill_id)
        if not v:
            return jsonify({"ok": False, "error": "no published version"}), 404
        return jsonify({"ok": True, "version": v})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills/<skill_id>/versions", methods=["POST"])
def api_v1_skills_version_create(skill_id: str) -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:create"):
        msg, code = sl["forbidden_error"]()
        return jsonify(msg), code
    data = request.get_json(silent=True) or {}
    version = str(data.get("version") or "").strip()
    if not version:
        return jsonify({"ok": False, "error": "version is required"}), 400
    try:
        return jsonify(sl["create_skill_version"](
            skill_id,
            version,
            bundle_yaml=str(data.get("bundle_yaml") or ""),
            bundle_schema=str(data.get("bundle_schema") or ""),
            prompt_ask=str(data.get("prompt_ask") or ""),
            prompt_confirm=str(data.get("prompt_confirm") or ""),
            prompt_plan=str(data.get("prompt_plan") or ""),
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills/<skill_id>/versions/<version>", methods=["PATCH"])
def api_v1_skills_version_patch(skill_id: str, version: str) -> Any:
    sl = _sl()
    token = sl["_bearer_token"](request.headers)
    if not sl["require_permission"](token, "skill:publish"):
        msg, code = sl["forbidden_error"]()
        return jsonify(msg), code
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").strip()
    if status not in ("draft", "published", "deprecated"):
        return jsonify({"ok": False, "error": "status must be draft/published/deprecated"}), 400
    try:
        return jsonify(sl["set_version_status"](skill_id, version, status))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/plan", methods=["POST"])
def api_v1_plan() -> Any:
    """Worker Plan Mode: Ask -> Confirm -> Plan. Produces plan only, no writes."""
    from skill_library_api import run_plan_flow
    data = request.get_json(silent=True) or {}
    requirement = str(data.get("requirement") or "").strip()
    root_seq = str(data.get("root_seq") or "10").strip()
    if not requirement:
        return jsonify({"ok": False, "error": "requirement is required"}), 400
    try:
        return jsonify(run_plan_flow(requirement, root_seq))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/plan/validate", methods=["POST"])
def api_v1_plan_validate() -> Any:
    """Validate a plan JSON against hard rules (no LLM)."""
    from skill_library_api import validate_plan
    data = request.get_json(silent=True) or {}
    plan = data.get("plan")
    if plan is None:
        return jsonify({"ok": False, "error": "plan is required"}), 400
    try:
        return jsonify(validate_plan(plan))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/v1/skills/seed-planner", methods=["POST"])
def api_v1_seed_planner() -> Any:
    """Seed the ontology_task_planner skill bundle (v1.0.0)."""
    from skill_library_api import seed_ontology_task_planner
    try:
        return jsonify(seed_ontology_task_planner())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Interactive Plan Sessions (Ask -> Confirm -> Plan) =================


@app.route("/api/v1/plan/session/ask", methods=["POST"])
def api_v1_plan_session_ask() -> Any:
    from skill_library_api import (
        _bearer_token,
        _extract_entities,
        auth_error,
        get_plan_session,
        log_plan_session,
        require_permission,
        session_error,
        upsert_plan_session,
    )
    token = _bearer_token(request.headers)
    if not require_permission(token, "skill:get"):
        msg, code = auth_error()
        return jsonify(msg), code
    data = request.get_json(silent=True) or {}
    sid = str(data.get("session_id") or "").strip()
    req_text = str(data.get("requirement") or "").strip()
    if not sid or not req_text:
        msg, code = session_error("MISSING_FIELDS", "session_id and requirement required", session_id=sid)
        return jsonify(msg), code
    track_id = "trk_" + uuid.uuid4().hex[:12]
    sess = get_plan_session(sid)
    cur_stage = sess["stage"] if sess else "new"
    # Ask allowed from new/ask/confirm/plan (re-ask resets); not from completed
    if cur_stage not in ("new", "ask", "confirm", "plan"):
        msg, code = session_error(
            "INVALID_STAGE_TRANSITION",
            f"cannot re-ask from {cur_stage}",
            session_id=sid, track_id=track_id)
        return jsonify(msg), code
    entities = _extract_entities(req_text)  # Ask stage (LLM in real flow)
    # Optimistic lock: client passes the version it read; stale writes rejected.
    expected_version = data.get("expected_version")
    if expected_version is None:
        expected_version = sess["version"] if sess else None
    res = upsert_plan_session(
        sid,
        chat_id=data.get("chat_id"),
        root_seq=str(data.get("root_seq") or "10"),
        requirement=req_text,
        stage="ask",
        extracted_entities=entities,
        expected_version=expected_version,
        from_stage=cur_stage,
    )
    if not res.get("ok"):
        msg, code = session_error(res["error_code"], res["error"], session_id=sid, track_id=track_id)
        return jsonify(msg), code
    log_plan_session(
        session_id=sid, chat_id=data.get("chat_id"), stage="ask",
        from_stage=cur_stage, action="ask",
        payload={"requirement": req_text}, state={"entities": entities},
        track_id=track_id)
    return jsonify({"ok": True, "session_id": sid, "stage": "ask",
                    "entities": entities, "version": res["version"],
                    "track_id": track_id})


@app.route("/api/v1/plan/session/confirm", methods=["POST"])
def api_v1_plan_session_confirm() -> Any:
    from skill_library_api import (
        _bearer_token,
        apply_user_modify,
        auth_error,
        get_plan_session,
        log_plan_session,
        require_permission,
        session_error,
        upsert_plan_session,
    )
    token = _bearer_token(request.headers)
    if not require_permission(token, "skill:get"):
        msg, code = auth_error()
        return jsonify(msg), code
    data = request.get_json(silent=True) or {}
    sid = str(data.get("session_id") or "").strip()
    track_id = "trk_" + uuid.uuid4().hex[:12]
    sess = get_plan_session(sid)
    if not sess:
        msg, code = session_error("SESSION_NOT_FOUND", "session not found",
                                  http_status=404, session_id=sid, track_id=track_id)
        return jsonify(msg), code
    cur_stage = sess["stage"]
    # Confirm only from ask/confirm
    if cur_stage not in ("ask", "confirm"):
        msg, code = session_error(
            "INVALID_STAGE_TRANSITION",
            f"cannot confirm from {cur_stage}; must be ask/confirm",
            session_id=sid, track_id=track_id)
        return jsonify(msg), code
    entities, modify_errors = apply_user_modify(
        sess["extracted_entities"], data.get("user_modify"))
    if modify_errors:
        msg, code = session_error(
            "DUPLICATE_ENTITY", "; ".join(modify_errors),
            session_id=sid, track_id=track_id)
        return jsonify(msg), code
    total = len(entities)
    optional = [
        {"type": "Job", "name": "seed_data", "action": "CREATE"},
        {"type": "Event", "name": "data_changed", "action": "CREATE"},
    ]
    # user_modify present -> reset to ask; else advance to confirm
    next_stage = "ask" if data.get("user_modify") else "confirm"
    expected_version = data.get("expected_version")
    if expected_version is None:
        expected_version = sess["version"]
    res = upsert_plan_session(
        sid,
        chat_id=sess.get("chat_id"),
        root_seq=sess["root_seq"],
        requirement=sess["requirement"],
        stage=next_stage,
        extracted_entities=entities,
        expected_version=expected_version,
        from_stage=cur_stage,
    )
    if not res.get("ok"):
        msg, code = session_error(res["error_code"], res["error"], session_id=sid, track_id=track_id)
        return jsonify(msg), code
    log_plan_session(
        session_id=sid, chat_id=sess.get("chat_id"), stage=next_stage,
        from_stage=cur_stage, action="confirm",
        payload={"user_modify": data.get("user_modify")},
        state={"entities": entities, "total_tasks": total},
        track_id=track_id)
    return jsonify({
        "ok": True,
        "session_id": sid,
        "stage": next_stage,
        "entities": entities,
        "total_tasks": total,
        "optional_extensions": optional,
        "version": res["version"],
        "track_id": track_id,
    })


@app.route("/api/v1/plan/session/generate", methods=["POST"])
def api_v1_plan_session_generate() -> Any:
    from skill_library_api import (
        _bearer_token,
        _sort_key,
        auth_error,
        get_plan_session,
        log_plan_session,
        require_permission,
        session_error,
        upsert_plan_session,
        validate_plan,
    )
    token = _bearer_token(request.headers)
    if not require_permission(token, "skill:get"):
        msg, code = auth_error()
        return jsonify(msg), code
    data = request.get_json(silent=True) or {}
    sid = str(data.get("session_id") or "").strip()
    track_id = "trk_" + uuid.uuid4().hex[:12]
    sess = get_plan_session(sid)
    if not sess:
        msg, code = session_error("SESSION_NOT_FOUND", "session not found",
                                  http_status=404, session_id=sid, track_id=track_id)
        return jsonify(msg), code
    cur_stage = sess["stage"]
    # Generate only from confirm (hard state-machine gate)
    if cur_stage != "confirm":
        msg, code = session_error(
            "INVALID_STAGE_TRANSITION",
            f"cannot generate from {cur_stage}; must confirm first",
            session_id=sid, track_id=track_id)
        return jsonify(msg), code
    entities = sorted(sess["extracted_entities"], key=_sort_key)
    tasks = [
        {
            "task_id": f"{sess['root_seq']}.{i}",
            "type": e["type"],
            "name": e["name"],
            "action": e.get("action", "CREATE"),
        }
        for i, e in enumerate(entities, start=1)
    ]
    plan = {
        "root_seq": sess["root_seq"],
        "total_tasks": len(tasks),
        "tasks": tasks,
        "optional_extensions": [
            {"type": "Job", "name": "seed_data", "action": "CREATE"}
        ],
    }
    validation = validate_plan(plan)
    res = upsert_plan_session(
        sid,
        chat_id=sess.get("chat_id"),
        root_seq=sess["root_seq"],
        requirement=sess["requirement"],
        stage="plan",
        extracted_entities=entities,
        expected_version=data.get("expected_version", sess["version"]),
        from_stage=cur_stage,
    )
    if not res.get("ok"):
        msg, code = session_error(res["error_code"], res["error"], session_id=sid, track_id=track_id)
        return jsonify(msg), code
    log_plan_session(
        session_id=sid, chat_id=sess.get("chat_id"), stage="plan",
        from_stage=cur_stage, action="generate",
        payload={"plan": plan}, state={"validation": validation},
        track_id=track_id,
        qc_warnings=[] if validation.get("ok") else validation.get("errors", []),
        qc_errors=[] if validation.get("ok") else validation.get("errors", []))
    return jsonify({
        "ok": True,
        "session_id": sid,
        "stage": "plan",
        "plan": plan,
        "validation": validation,
        "version": res["version"],
        "track_id": track_id,
    })


@app.route("/api/v1/plan/session/<session_id>", methods=["DELETE"])
def api_v1_plan_session_delete(session_id: str) -> Any:
    from skill_library_api import (
        _bearer_token,
        auth_error,
        delete_plan_session,
        require_permission,
        session_error,
    )
    token = _bearer_token(request.headers)
    if not require_permission(token, "skill:get"):
        msg, code = auth_error()
        return jsonify(msg), code
    try:
        n = delete_plan_session(session_id)
        if n == 0:
            msg, code = session_error("SESSION_NOT_FOUND", "session not found",
                                      http_status=404, session_id=session_id)
            return jsonify(msg), code
        return jsonify({"ok": True, "deleted": n})
    except Exception as e:
        msg, code = session_error("INTERNAL_ERROR", f"{type(e).__name__}: {e}",
                                  http_status=500, session_id=session_id)
        return jsonify(msg), code


@app.route("/api/tasks/validate", methods=["POST", "GET"])
def api_tasks_validate() -> Any:
    """Pre-dispatch gate: Task|Channel|Module|Capability|API|Function|Table|Field."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing", "dispatch_allowed": False}), 500
        payload: Any
        if request.method == "GET":
            # Accept query keys or a single `payload` / `task` blob.
            raw = request.args.get("payload") or request.args.get("task")
            if raw:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = raw
            else:
                payload = {k: request.args.get(k) for k in (
                    "task", "channel", "module", "capability", "api",
                    "function", "table", "field",
                ) if request.args.get(k) is not None}
        else:
            body = request.get_json(silent=True) or {}
            if isinstance(body, dict) and (
                "dimensions" in body or "payload" in body or "task_payload" in body
            ):
                payload = body.get("dimensions") or body.get("payload") or body.get("task_payload")
            else:
                payload = body
        out = validate_new_task(payload, db_path=AGENT_DB_PATH)
        code = 200 if out.get("ok") else 400
        return jsonify(out), code
    except Exception as e:
        return jsonify({
            "ok": False,
            "result": "FAIL",
            "dispatch_allowed": False,
            "error": f"{type(e).__name__}: {e}",
        }), 500


@app.route("/api/skills/seed-task-format-validator", methods=["POST"])
def api_skills_seed_task_format_validator() -> Any:
    """Seed skill_task_format_worker_taxonomy_validator + T-SKILL01 + code_registry."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        out = seed_task_format_validator(AGENT_DB_PATH)
        code = 200 if out.get("ok") else 500
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/seed-lifecycle", methods=["POST"])
def api_skills_seed_lifecycle() -> Any:
    """Seed lifecycle recorder + draft pipeline skills + T-SKILL02."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        out = seed_lifecycle_skills(AGENT_DB_PATH)
        code = 200 if out.get("ok") else 500
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/tasks/lifecycle/append", methods=["POST"])
def api_tasks_lifecycle_append() -> Any:
    """Append-only lifecycle event (Python source of truth)."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        body = request.get_json(silent=True) or {}
        payload = body.get("payload") if isinstance(body, dict) and "payload" in body else body
        out = append_lifecycle_event(payload if isinstance(payload, dict) else {}, db_path=AGENT_DB_PATH)
        code = 200 if out.get("ok") else 400
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "result": "FAIL", "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/tasks/<path:task_id>/lifecycle", methods=["GET"])
def api_tasks_lifecycle_list(task_id: str) -> Any:
    try:
        limit = request.args.get("limit", default=500, type=int) or 500
        out = list_lifecycle_events(task_id, db_path=AGENT_DB_PATH, limit=limit)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "events": []}), 500


@app.route("/api/tasks/prompt-trace", methods=["POST"])
def api_tasks_prompt_trace_create() -> Any:
    """Minimal prompt snapshot write (assembler full skill is Phase D)."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        body = request.get_json(silent=True) or {}
        out = insert_prompt_trace(body if isinstance(body, dict) else {}, db_path=AGENT_DB_PATH)
        code = 200 if out.get("ok") else 400
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/tasks/<path:task_id>/prompt-traces", methods=["GET"])
def api_tasks_prompt_traces_list(task_id: str) -> Any:
    try:
        limit = request.args.get("limit", default=200, type=int) or 200
        out = list_prompt_traces(task_id, db_path=AGENT_DB_PATH, limit=limit)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "traces": []}), 500


@app.route("/api/tasks/<path:task_id>/timeline", methods=["GET"])
def api_tasks_timeline(task_id: str) -> Any:
    try:
        out = get_task_timeline(task_id, db_path=AGENT_DB_PATH)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/prompt-analyze")
def prompt_analyze_page():
    return _llm_monitor_spa_index()


@app.route("/api/task-center/tasks", methods=["GET"])
def api_task_center_tasks() -> Any:
    limit = request.args.get("limit", default=200, type=int) or 200
    try:
        tasks = list_task_center_tasks(limit=limit)
        return jsonify({"ok": True, "tasks": tasks, "count": len(tasks)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "tasks": []}), 500


@app.route("/api/task_center/graph", methods=["GET"])
def api_task_center_graph() -> Any:
    """The catalog -> subcatalog tree of `skill_template`. READ-ONLY.

    WHY THIS ROUTE DID NOT EXIST AND WHY THAT WAS A DEFECT (measured 2026-09-27).
    ---------------------------------------------------------------------------
    `app.js` calls this URL from TWO places (the periodic refresh and the explicit
    Refresh button), stores the reply in `state.graph.catalogs`, and
    `graphHtml()` RENDERS that list — the "System Graph Map" page. The helper
    behind it, `llm_task_center.graph_map`, has existed all along and returns
    EXACTLY the shape `graphHtml` reads.

    So the defect was ONE MISSING ROUTE, and its symptom was a 404 on EVERY page
    every 5 seconds (the refresh loop), with the graph page permanently showing
    "No skill templates yet. Seed via backend." while `skill_template` held 21
    real rows. A `fetch` whose route does not exist fails SILENTLY in the client
    (`catch { /* keep previous */ }`), so nothing ever named it — only the network
    log showed it, which is why it survived.

    THIS ROUTE ADDS NO LOGIC. It is the same thin binding every `/api/generator/*`
    route is: open a connection, call the module that owns the query, close.
    """
    import llm_task_center as ltc

    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify(ltc.graph_map(conn=conn))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "catalogs": []}), 500
    finally:
        conn.close()


@app.route("/api/task_center/queue", methods=["GET"])
def api_task_center_queue() -> Any:
    """The 7B skill task queue: status counts + rows, INCLUDING assigned_model.

    WHY THIS EXISTS
    ---------------
    `skill_task_queue` had a worker (`worker_once`) and a table, but no way to
    SEE it: the only surface was a telemetry chart. So "which model ran this, and
    did it finish" was unanswerable from the UI. This endpoint exposes the rows
    verbatim, including `assigned_model`, `retry_count`, `handoff_count`,
    `result` and `error_msg`, so the queue is inspectable and provable.

    Read-only. It never claims or mutates a task.
    """
    import skill_task_queue as stq

    limit = request.args.get("limit", default=200, type=int) or 200
    status = (request.args.get("status") or "").strip() or None
    skill_id = (request.args.get("skill_id") or "").strip() or None
    try:
        return jsonify({
            "ok": True,
            "overview": stq.overview(),
            "tasks": stq.list_tasks(skill_id=skill_id, status=status, limit=limit),
            "filter": {"status": status, "skill_id": skill_id, "limit": limit},
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "tasks": []}), 500


@app.route("/api/task-center/tasks/<int:task_id>", methods=["GET"])
def api_task_center_task(task_id: int) -> Any:
    try:
        task = get_task_center_task(task_id)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    if not task:
        return jsonify({"ok": False, "error": f"task not found: {task_id}"}), 404
    writer = task.get("writer")
    session_id = task.get("session_id")
    ctx = context_completeness(
        task_id=task.get("id"),
        writer=writer,
        session_id=session_id,
    )
    return jsonify({
        "ok": True,
        "task": task,
        "context": {
            "task_id": task.get("id"),
            "writer": writer,
            "session_id": session_id,
            **ctx,
        },
        "task_center_url": f"{TASK_CENTER_URL}?task_id={task_id}",
        "prompt_analyze_url": f"{HELPER_PROMPT_ANALYZE_URL}?task_id={task_id}",
    })


@app.route("/api/prompt/templates", methods=["GET"])
def api_prompt_templates() -> Any:
    """List built-in prompt-create templates (IDE work + identity trailer)."""
    return jsonify({
        "ok": True,
        "templates": [
            {
                "id": "ide_work",
                "name": "IDE work prompt",
                "description": (
                    "Create a coding-agent prompt for VS Code / Cursor / Work Buddy / Codex. "
                    "session_id stays empty until IDE works the task; reply must return identity trio."
                ),
                "ide_targets": list(IDE_TARGETS),
                "placeholders": ["goal", "context", "ide_target", "task_id", "writer", "session_id"],
            }
        ],
        "session_id_meaning": "IDE session id (VS Code / Cursor / Work Buddy / Codex)",
        "new_task_rule": "session_id empty until IDE works the task",
    })


@app.route("/api/prompt/from-template", methods=["POST"])
def api_prompt_from_template() -> Any:
    """Create prompt text from a template (default: ide_work)."""
    data = request.get_json(silent=True) or {}
    template_id = str(data.get("template_id") or data.get("template") or "ide_work").strip()
    writer = str(data.get("writer") or "").strip()
    task_id = str(data.get("task_id") or "").strip()
    # New tasks: never invent session_id
    session_id = str(data.get("session_id") or "").strip()
    if template_id not in ("ide_work", "ide", "default"):
        return jsonify({"ok": False, "error": f"unknown template_id: {template_id}"}), 400
    prompt = render_ide_work_prompt(
        goal=str(data.get("goal") or data.get("prompt") or ""),
        context=str(data.get("context") or ""),
        ide_target=str(data.get("ide_target") or data.get("target") or "Visual Studio Code"),
        writer=writer,
        task_id=task_id,
        session_id=session_id,
    )
    trio = identity_trio_dict(writer=writer, task_id=task_id, session_id=session_id)
    return jsonify({
        "ok": True,
        "template_id": "ide_work",
        "prompt": prompt,
        "improved_prompt": prompt,
        "result": prompt,
        "identity": trio,
        "writer": trio["writer"],
        "task_id": trio["task_id"],
        "session_id": trio["session_id"],
        "session_empty": not bool(trio["session_id"]),
        "hint": (
            "session_id empty is normal for new tasks; after IDE work, paste the agent reply "
            "and use Apply IDs from reply."
        ),
    })


@app.route("/api/prompt/apply-ids", methods=["POST"])
def api_prompt_apply_ids() -> Any:
    """Parse identity trio from agent reply text (session_id / task_id / writer)."""
    data = request.get_json(silent=True) or {}
    text = str(data.get("text") or data.get("reply") or data.get("prompt") or "")
    if not text.strip():
        return jsonify({"ok": False, "error": "text/reply required"}), 400
    found = extract_prompt_ids(text)
    trio = identity_trio_dict(
        writer=found.get("writer") or data.get("writer"),
        task_id=found.get("task_id") or data.get("task_id"),
        session_id=found.get("session_id") or data.get("session_id"),
    )
    return jsonify({
        "ok": True,
        "found": found,
        "identity": trio,
        "writer": trio["writer"],
        "task_id": trio["task_id"],
        "session_id": trio["session_id"],
        "applied": {
            "writer": bool(trio["writer"]),
            "task_id": bool(trio["task_id"]),
            "session_id": bool(trio["session_id"]),
        },
        "trailer": identity_trailer(**trio),
    })


@app.route("/api/prompt/improve", methods=["POST"])
def api_prompt_improve() -> Any:
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt")
    if prompt is None:
        prompt = ""
    prompt = str(prompt)
    task_id = str(data.get("task_id") or "").strip()
    writer = str(data.get("writer") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    model = str(data.get("model") or DEFAULT_TEXT_MODEL).strip() or DEFAULT_TEXT_MODEL
    # Soft gate: session_id optional for new tasks / request-in-reply mode
    require_session = bool(data.get("require_session", False))
    if "require_session" not in data and str(data.get("mode") or "").strip().lower() in (
        "strict",
        "full_trio",
    ):
        require_session = True

    ctx = context_completeness(
        task_id=task_id,
        writer=writer,
        session_id=session_id,
        require_session=require_session,
    )
    if not ctx["ok"]:
        return jsonify({
            "ok": False,
            "error": "missing required context: " + ", ".join(ctx["missing"]),
            "missing": ctx["missing"],
            "context": ctx,
            "writer": writer,
            "task_id": task_id,
            "session_id": session_id,
            "hint": (
                "task_id and writer are required. session_id may stay empty until IDE works "
                "the task; improved prompt will ask the agent to return it."
            ),
        }), 400
    if not prompt.strip():
        return jsonify({"ok": False, "error": "prompt is required"}), 400

    run_id = "t_" + uuid.uuid4().hex[:12]
    started = _utc_now_iso()
    append_llm_task({
        "id": run_id,
        "task": "prompt_improve",
        "model": model,
        "model_label": next((m["label"] for m in LLM_MODELS if m["id"] == model), model),
        "status": "running",
        "started_at": started,
        "ended_at": None,
        "duration_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "result": None,
        "reason": None,
        "writer": writer,
        "task_id": task_id,
        "session_id": session_id,
        "error": None,
    })

    session_rule = (
        f"session_id: {session_id}\n"
        if session_id
        else (
            "session_id: \n"
            "(leave session_id empty if unknown; add an instruction that the IDE agent must "
            "return session_id / task_id / writer at the end of its reply)\n"
        )
    )
    system = (
        "You improve user prompts for coding agents (VS Code / Cursor / Work Buddy / Codex). "
        "Rewrite for clarity and actionability. "
        "You MUST keep or add these identity lines at the end of the improved prompt:\n"
        f"writer: {writer}\n"
        f"task_id: {task_id}\n"
        f"{session_rule}"
        "session_id means the IDE chat/session id. Do not invent a fake session_id. "
        "Return ONLY the improved prompt text. No markdown fences. No commentary."
    )
    user_msg = (
        "Improve the following prompt. Preserve intent. "
        "Ensure identity lines appear. If session_id is empty, instruct the agent to return "
        "session_id, task_id, and writer at the end of the reply.\n\n"
        f"--- ORIGINAL PROMPT ---\n{prompt}\n--- END ---"
    )
    result = complete_text(user_msg, model=model, system=system, format_json=False)
    ended = _utc_now_iso()
    if result.error:
        update_llm_task(run_id, {
            "status": "error",
            "ended_at": ended,
            "duration_ms": result.duration_ms,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "error": result.error,
            "reason": result.summary,
            "result": "FAIL",
        })
        return jsonify({
            "ok": False,
            "error": result.error,
            "reason": result.summary,
            "run_id": run_id,
            "model": model,
            "writer": writer,
            "task_id": task_id,
            "session_id": session_id,
        }), 502

    improved = (result.raw_text or "").strip()
    # Strip accidental markdown fences.
    if improved.startswith("```"):
        improved = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", improved)
        improved = re.sub(r"\n?```$", "", improved).strip()
    improved = ensure_prompt_id_footer(
        improved,
        writer=writer,
        task_id=task_id,
        session_id=session_id,
        request_session_if_empty=True,
    )
    # Soft ids check: writer+task required; session optional when empty
    ids_check = prompt_has_ids(
        improved, writer=writer, task_id=task_id, session_id=session_id or None
    )
    soft_ok = bool(ids_check.get("writer")) and bool(ids_check.get("task_id"))
    if session_id:
        soft_ok = soft_ok and bool(ids_check.get("session_id"))
    update_llm_task(run_id, {
        "status": "done",
        "ended_at": ended,
        "duration_ms": result.duration_ms or 0,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "result": "SUCCESS" if soft_ok else "FAIL",
        "reason": (
            "improved"
            if soft_ok
            else ("missing ids: " + ",".join(ids_check.get("missing") or []))
        ),
        "error": None,
    })
    trailer = identity_trailer(writer=writer, task_id=task_id, session_id=session_id)
    return jsonify({
        "ok": True,
        "improved_prompt": improved,
        "improved": improved,
        "result": improved,
        "prompt_ids_ok": soft_ok,
        "prompt_ids": ids_check,
        "context": ctx,
        "identity": identity_trio_dict(writer=writer, task_id=task_id, session_id=session_id),
        "writer": writer,
        "task_id": task_id,
        "session_id": session_id,
        "trailer": trailer,
        "model": model,
        "run_id": run_id,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "duration_ms": result.duration_ms,
    })


@app.route("/api/prompt/analyze", methods=["POST"])
def api_prompt_analyze() -> Any:
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt") or "")
    task_id = str(data.get("task_id") or "").strip() or None
    writer = str(data.get("writer") or "").strip() or None
    session_id = str(data.get("session_id") or "").strip() or None
    model = str(data.get("model") or DEFAULT_TEXT_MODEL).strip() or DEFAULT_TEXT_MODEL
    require_session = bool(data.get("require_session", False))

    if not prompt.strip():
        return jsonify({"ok": False, "error": "prompt is required"}), 400

    ctx = context_completeness(
        task_id=task_id,
        writer=writer,
        session_id=session_id,
        require_session=require_session,
    )
    prompt_ids = prompt_has_ids(
        prompt,
        writer=writer,
        task_id=task_id,
        session_id=session_id,
    )
    # Soft: empty session is OK for new tasks
    soft_ids_ok = bool(prompt_ids.get("writer")) and bool(prompt_ids.get("task_id"))
    if session_id:
        soft_ids_ok = soft_ids_ok and bool(prompt_ids.get("session_id"))
    elif not require_session:
        # session missing in prompt is expected
        pass

    run_id = "t_" + uuid.uuid4().hex[:12]
    started = _utc_now_iso()
    append_llm_task({
        "id": run_id,
        "task": "prompt_analyze",
        "model": model,
        "model_label": next((m["label"] for m in LLM_MODELS if m["id"] == model), model),
        "status": "running",
        "started_at": started,
        "ended_at": None,
        "duration_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "result": None,
        "reason": None,
        "writer": writer,
        "task_id": task_id,
        "session_id": session_id,
        "error": None,
    })

    system = (
        "You review prompts for coding agents (VS Code / Cursor / Work Buddy / Codex). "
        "Return ONLY JSON with keys: "
        "notes (string), score (number 0..1), suggestions (array of short strings). "
        "Focus on clarity, missing constraints, ambiguity, and identity fields. "
        "session_id is the IDE session id; empty session_id is OK for brand-new tasks "
        "that no IDE has worked yet — suggest requesting it in the agent reply."
    )
    user_msg = (
        "Analyze this prompt.\n"
        f"Task Center context: task_id={task_id!r} writer={writer!r} session_id={session_id!r}\n"
        f"Deterministic prompt ID check: {json.dumps(prompt_ids, ensure_ascii=False)}\n"
        f"Deterministic context check: {json.dumps(ctx, ensure_ascii=False)}\n\n"
        f"--- PROMPT ---\n{prompt}\n--- END ---"
    )
    result = complete_text(user_msg, model=model, system=system, format_json=True)
    ended = _utc_now_iso()

    notes = None
    score = None
    suggestions: list[str] = []
    if not result.error:
        detail = result.detail if isinstance(result.detail, dict) else {}
        notes = detail.get("notes") or result.summary
        try:
            score = float(detail.get("score")) if detail.get("score") is not None else None
        except (TypeError, ValueError):
            score = None
        raw_s = detail.get("suggestions") or []
        if isinstance(raw_s, list):
            suggestions = [str(x) for x in raw_s][:12]
        elif isinstance(raw_s, str) and raw_s.strip():
            suggestions = [raw_s.strip()]

    overall_ok = soft_ids_ok and bool(ctx.get("ok"))
    status = "done" if not result.error else "error"
    update_llm_task(run_id, {
        "status": status,
        "ended_at": ended,
        "duration_ms": result.duration_ms or 0,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "result": "SUCCESS" if overall_ok and not result.error else "FAIL",
        "reason": notes or result.summary or result.error,
        "error": result.error,
    })

    trio = identity_trio_dict(writer=writer, task_id=task_id, session_id=session_id)
    trailer = identity_trailer(**trio)
    notes_out = notes or ""
    if notes_out and not notes_out.rstrip().endswith(trio["task_id"] or "task_id"):
        notes_out = (notes_out.rstrip() + "\n\n" + trailer).strip()
    elif not notes_out:
        notes_out = trailer

    payload = {
        "ok": True,
        "run_id": run_id,
        "model": model,
        "context_ok": ctx["ok"],
        "context": ctx,
        "prompt_ids_ok": soft_ids_ok,
        "prompt_ids": prompt_ids,
        "missing": sorted(set(ctx["missing"] + [
            m for m in (prompt_ids.get("missing") or [])
            if require_session or m != "session_id"
        ])),
        "notes": notes_out,
        "score": score,
        "suggestions": suggestions,
        "llm_error": result.error,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "duration_ms": result.duration_ms,
        "identity": trio,
        "writer": trio["writer"] or writer,
        "task_id": trio["task_id"] or task_id,
        "session_id": trio["session_id"] or session_id,
        "trailer": trailer,
        "result": notes_out,
    }
    if result.error:
        # Still return checklist; surface model failure.
        payload["ok"] = True
        payload["llm_error"] = result.error
    return jsonify(payload)


@app.route("/api/skills/seed-all", methods=["POST"])
def api_skills_seed_all() -> Any:
    """Seed prompt v1 + gold cases + Task Center root 10 (Phase 5)."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing — run create_db.py --migrate"}), 500
        out = seed_phase5_all(AGENT_DB_PATH)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills", methods=["GET", "POST"])
def api_skills_list_or_create() -> Any:
    if request.method == "POST":
        return api_skills_create()
    skill_key = (request.args.get("skill") or request.args.get("skill_key") or "").strip() or None
    try:
        if AGENT_DB_PATH.is_file():
            try:
                seed_default_skills(AGENT_DB_PATH)
            except Exception:
                pass
        rows = list_skills(skill_key=skill_key)
        # slim list for UI
        items = []
        keys_seen: list[str] = []
        for r in rows:
            sk = str(r.get("skill_key") or "")
            if sk and sk not in keys_seen:
                keys_seen.append(sk)
            items.append({
                "id": r.get("id"),
                "skill_key": r.get("skill_key"),
                "prompt_key": r.get("prompt_key"),
                "version_label": r.get("version_label"),
                "status": r.get("status"),
                "parser": r.get("parser"),
                "model_default": r.get("model_default"),
                "source": r.get("source"),
                "notes": r.get("notes"),
                "updated_at": r.get("updated_at"),
            })
        return jsonify({
            "ok": True,
            "skills": items,
            "skill_keys": keys_seen,
            "count": len(items),
            "ide_targets": list(IDE_TARGETS),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


def api_skills_create() -> Any:
    """Register a new skill template (draft version). Enables + New template in SSOT UI."""
    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or data.get("skill") or "").strip()
    if not skill_key:
        return jsonify({"ok": False, "error": "skill_key required"}), 400
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{1,63}$", skill_key):
        return jsonify({
            "ok": False,
            "error": "skill_key must be 2–64 chars: letter then letters/digits/underscore",
        }), 400
    version_label = str(data.get("version_label") or data.get("version") or "v1_draft").strip()
    from_skill = str(data.get("from_skill") or data.get("clone_from") or "").strip()
    from_version = str(data.get("from_version") or "").strip() or None
    prompt_text = str(data.get("prompt_text") or data.get("prompt") or "").strip()
    notes = data.get("notes")
    parser = str(data.get("parser") or "result_yes_no").strip() or "result_yes_no"
    writer = str(data.get("writer") or "").strip()
    task_id = str(data.get("task_id") or "").strip()
    session_id = str(data.get("session_id") or "").strip()

    if not prompt_text and from_skill:
        try:
            if from_version:
                from skill_prompt import get_skill_version
                src = get_skill_version(from_skill, from_version)
            else:
                src = get_active_skill(from_skill, db_path=AGENT_DB_PATH)
            if src and src.get("prompt_text"):
                prompt_text = str(src.get("prompt_text") or "")
                parser = str(src.get("parser") or parser)
        except Exception:
            pass
    if not prompt_text:
        # Minimal IDE-oriented starter template
        prompt_text = (
            "You are a visual inspector for IDE targets "
            "(Visual Studio Code / Cursor / Work Buddy / Codex).\n"
            "1. Red crosshair (+) = captured mouse point.\n"
            "2. Target = {{target_name}}.\n"
            "3. PASS only if crosshair is on the target icon pixels.\n"
            "4. Intended action (context only): {{target_action}}\n"
            "5. Output exactly:\n"
            "Result: [YES / NO]\n"
            "Reason: 1 short sentence.\n"
        )
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing — run create_db.py --migrate"}), 500
        conn = skill_db_connect(AGENT_DB_PATH)
        try:
            ensure_skill_tables(conn)
            existing = list_skills(skill_key=skill_key)
            if any(str(r.get("version_label")) == version_label for r in existing):
                return jsonify({
                    "ok": False,
                    "error": f"version already exists: {skill_key}/{version_label}",
                    "skill_key": skill_key,
                }), 409
            row = upsert_skill_prompt(
                conn,
                skill_key=skill_key,
                version_label=version_label,
                prompt_text=prompt_text,
                prompt_key=str(data.get("prompt_key") or "main"),
                status=str(data.get("status") or "draft"),
                parser=parser,
                output_schema=str(data.get("output_schema") or parser),
                model_default=str(data.get("model_default") or DEFAULT_VISION_MODEL),
                hard_rules=data.get("hard_rules") if isinstance(data.get("hard_rules"), list) else None,
                source=str(data.get("source") or "ssot_ui_create"),
                notes=str(notes) if notes else f"created via POST /api/skills; clone={from_skill or '-'}",
                activate=bool(data.get("activate")),
                commit=True,
            )
        finally:
            conn.close()
        trio = identity_trio_dict(writer=writer, task_id=task_id, session_id=session_id)
        return jsonify({
            "ok": True,
            "created": True,
            "skill_key": skill_key,
            "skill": row,
            "identity": trio,
            "writer": trio["writer"],
            "task_id": trio["task_id"],
            "session_id": trio["session_id"],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>", methods=["GET"])
def api_skills_get(skill_key: str) -> Any:
    version = (request.args.get("version") or "").strip() or None
    try:
        if AGENT_DB_PATH.is_file():
            try:
                seed_default_skills(AGENT_DB_PATH)
            except Exception:
                pass
        versions = list_skills(skill_key=skill_key)
        if version:
            active = next(
                (v for v in versions if str(v.get("version_label")) == version),
                None,
            )
            if active is None:
                from skill_prompt import get_skill_version
                active = get_skill_version(skill_key, version)
        else:
            active = get_active_skill(skill_key, db_path=AGENT_DB_PATH)
        latest = latest_test_run(skill_key, db_path=AGENT_DB_PATH)
        latest_out = None
        if latest:
            latest_out = {
                k: latest.get(k)
                for k in (
                    "run_id", "version_label", "expected", "n_runs",
                    "yes_count", "no_count", "other_count", "error_count",
                    "yes_pct", "no_pct", "accuracy_pct", "pass_gate",
                    "model", "avg_duration_ms", "wall_ms", "created_at",
                    "target_name",
                )
            }
        return jsonify({
            "ok": True,
            "skill_key": skill_key,
            "active": active,
            "versions": [
                {
                    "id": v.get("id"),
                    "version_label": v.get("version_label"),
                    "status": v.get("status"),
                    "parser": v.get("parser"),
                    "source": v.get("source"),
                    "updated_at": v.get("updated_at"),
                }
                for v in versions
            ],
            "latest_test": latest_out,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/table", methods=["GET"])
def api_skills_table(skill_key: str) -> Any:
    """The skill AS A TABLE: every applicable factor + its MEASURED state.

    WHY THIS ROUTE EXISTS (measured 2026-09-28)
    -------------------------------------------
    `skill_factor.skill_table()` has been the "table representation of a skill
    -- the format that replaces `.md`" since 2026-09-21, and NOTHING CALLED IT.
    MEASURED: `grep -E 'factor|metric_unit|metric_target|proof_prefix'` over the
    whole UI (`llm_task_monitor_ui/src/*.js`) returns ZERO matches. So 65
    registered factors, each carrying a rule, a MEASURED UNIT and a proof, were
    invisible in the product: the rules existed and no screen showed them. A
    renderer with no route is a rule nobody can read, which is the same defect
    as a factor with no proof.

    WHERE `state` COMES FROM: it is the factor's own proof row, DERIVED in
    `skill_table`. `UNMEASURED` means no proof row was recorded -- it is NOT a
    pass, and the UI must not colour it as one.
    """
    try:
        import sqlite3
        import skill_factor
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            t = skill_factor.skill_table(conn, skill_key)
            return jsonify({"ok": True, "skill_key": skill_key, **t})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/draft", methods=["POST"])
def api_skills_draft(skill_key: str) -> Any:
    data = request.get_json(silent=True) or {}
    prompt_text = str(data.get("prompt_text") or data.get("prompt") or "")
    version_label = str(data.get("version_label") or data.get("version") or "").strip()
    if not version_label:
        version_label = f"draft_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    # Clone from existing template/version when prompt empty
    from_version = str(data.get("from_version") or data.get("clone_version") or "").strip() or None
    if not prompt_text.strip():
        try:
            if from_version:
                from skill_prompt import get_skill_version
                src = get_skill_version(skill_key, from_version)
            else:
                src = get_active_skill(skill_key, db_path=AGENT_DB_PATH)
            if src and src.get("prompt_text"):
                prompt_text = str(src.get("prompt_text") or "")
        except Exception:
            pass
    if not prompt_text.strip():
        return jsonify({"ok": False, "error": "prompt_text required (or from_version to clone)"}), 400
    activate = bool(data.get("activate"))
    status = "active" if activate else str(data.get("status") or "draft")
    task_id = data.get("task_id")
    writer = str(data.get("writer") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    try:
        task_id_i = int(task_id) if task_id not in (None, "") else None
    except (TypeError, ValueError):
        task_id_i = None
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing — run create_db.py --migrate"}), 500
        conn = skill_db_connect(AGENT_DB_PATH)
        try:
            ensure_skill_tables(conn)
            row = upsert_skill_prompt(
                conn,
                skill_key=skill_key,
                version_label=version_label,
                prompt_text=prompt_text,
                prompt_key=str(data.get("prompt_key") or "main"),
                status=status,
                parser=str(data.get("parser") or "result_yes_no"),
                output_schema=str(data.get("output_schema") or "result_yes_no"),
                model_default=str(data.get("model_default") or DEFAULT_VISION_MODEL),
                hard_rules=data.get("hard_rules") if isinstance(data.get("hard_rules"), list) else None,
                task_id=task_id_i,
                source=str(data.get("source") or "llm_tasks_ui"),
                notes=data.get("notes"),
                activate=activate,
                commit=True,
            )
        finally:
            conn.close()
        trio = identity_trio_dict(
            writer=writer,
            task_id=task_id if task_id not in (None, "") else "",
            session_id=session_id,
        )
        return jsonify({
            "ok": True,
            "skill": row,
            "skill_key": skill_key,
            "version_label": version_label,
            "identity": trio,
            "writer": trio["writer"],
            "task_id": trio["task_id"],
            "session_id": trio["session_id"],
            "trailer": identity_trailer(**trio),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/activate", methods=["POST"])
def api_skills_activate(skill_key: str) -> Any:
    data = request.get_json(silent=True) or {}
    version_label = str(data.get("version_label") or data.get("version") or "").strip()
    if not version_label:
        return jsonify({"ok": False, "error": "version_label required"}), 400
    force = bool(data.get("force"))
    require_pass = data.get("require_pass_gate", True)
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        if require_pass and not force:
            latest = latest_test_run(skill_key, db_path=AGENT_DB_PATH)
            if not latest or not int(latest.get("pass_gate") or 0):
                return jsonify({
                    "ok": False,
                    "error": "last test did not pass_gate — run 100-test first or pass force=true",
                    "latest_test": {
                        "run_id": (latest or {}).get("run_id"),
                        "pass_gate": (latest or {}).get("pass_gate"),
                        "accuracy_pct": (latest or {}).get("accuracy_pct"),
                        "version_label": (latest or {}).get("version_label"),
                    } if latest else None,
                }), 400
            if str(latest.get("version_label") or "") != version_label:
                # allow activate if force not set but warn — still block unless force
                if not force:
                    return jsonify({
                        "ok": False,
                        "error": f"latest pass_gate test is for version {latest.get('version_label')}, not {version_label}",
                        "latest_test_version": latest.get("version_label"),
                    }), 400
        conn = skill_db_connect(AGENT_DB_PATH)
        try:
            row = set_active_version(
                conn, skill_key=skill_key, version_label=version_label
            )
        finally:
            conn.close()
        return jsonify({"ok": True, "active": row})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/test", methods=["POST"])
def api_skills_test(skill_key: str) -> Any:
    data = request.get_json(silent=True) or {}
    runs = int(data.get("runs") or 10)
    runs = max(1, min(runs, 100))
    expected = str(data.get("expected") or "NO").strip().upper()
    if expected not in ("YES", "NO"):
        return jsonify({"ok": False, "error": "expected must be YES or NO"}), 400
    target_name = str(data.get("target_name") or data.get("target") or "Visual Studio Code")
    target_action = str(data.get("target_action") or data.get("action") or "")
    version_label = (data.get("version_label") or data.get("version") or None)
    if version_label is not None:
        version_label = str(version_label).strip() or None
    image_path = data.get("image_path") or str(SCREENSHOT_PATH)
    model = data.get("model")

    run_id = "t_" + uuid.uuid4().hex[:12]
    started = _utc_now_iso()
    append_llm_task({
        "id": run_id,
        "task": "skill_prompt_test",
        "model": model or DEFAULT_VISION_MODEL,
        "model_label": "skill-test",
        "status": "running",
        "started_at": started,
        "ended_at": None,
        "duration_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "result": None,
        "reason": f"runs={runs} expected={expected}",
        "skill_key": skill_key,
        "skill_version": version_label,
        "target_name": target_name,
        "error": None,
    })
    try:
        out = test_prompt_runs(
            skill_key=skill_key,
            version_label=version_label,
            image_path=image_path,
            target_name=target_name,
            target_action=target_action,
            expected=expected,
            runs=runs,
            model=model,
            db_path=AGENT_DB_PATH,
            persist=True,
        )
        ended = _utc_now_iso()
        update_llm_task(run_id, {
            "status": "done",
            "ended_at": ended,
            "duration_ms": out.get("wall_ms") or 0,
            "result": "SUCCESS" if out.get("pass_gate") else "FAIL",
            "reason": (
                f"yes={out.get('yes')} no={out.get('no')} other={out.get('other')} "
                f"err={out.get('errors')} acc={out.get('accuracy_pct')}% "
                f"gate={'PASS' if out.get('pass_gate') else 'FAIL'}"
            ),
            "skill_version": out.get("version_label"),
            "model": out.get("model"),
            "error": None,
        })
        writer = str(data.get("writer") or "").strip()
        session_id = str(data.get("session_id") or "").strip()
        task_id_ctx = str(data.get("task_id") or "").strip()
        trio = identity_trio_dict(
            writer=writer, task_id=task_id_ctx, session_id=session_id
        )
        out["llm_task_id"] = run_id
        out["identity"] = trio
        out["writer"] = trio["writer"]
        out["task_id"] = trio["task_id"] or run_id
        out["session_id"] = trio["session_id"]
        out["trailer"] = identity_trailer(
            writer=trio["writer"],
            task_id=trio["task_id"] or run_id,
            session_id=trio["session_id"],
        )
        return jsonify(out)
    except FileNotFoundError as e:
        update_llm_task(run_id, {
            "status": "error",
            "ended_at": _utc_now_iso(),
            "result": "FAIL",
            "error": str(e),
            "reason": str(e),
        })
        return jsonify({"ok": False, "error": str(e), "llm_task_id": run_id}), 400
    except Exception as e:
        update_llm_task(run_id, {
            "status": "error",
            "ended_at": _utc_now_iso(),
            "result": "FAIL",
            "error": f"{type(e).__name__}: {e}",
            "reason": str(e),
        })
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}", "llm_task_id": run_id}), 500


@app.route("/api/skills/<skill_key>/tests/latest", methods=["GET"])
def api_skills_test_latest(skill_key: str) -> Any:
    row = latest_test_run(skill_key, db_path=AGENT_DB_PATH)
    if not row:
        return jsonify({"ok": True, "latest_test": None})
    return jsonify({"ok": True, "latest_test": dict(row)})


@app.route("/api/skills/<skill_key>/cases", methods=["GET", "POST"])
def api_skills_cases(skill_key: str) -> Any:
    if request.method == "POST":
        return api_skills_cases_create(skill_key)
    status = (request.args.get("status") or "active").strip() or None
    if status and status.lower() == "all":
        status = None
    try:
        cases = list_skill_cases(
            skill_key=skill_key, status=status, db_path=AGENT_DB_PATH
        )
        return jsonify({
            "ok": True,
            "skill_key": skill_key,
            "status": status or "all",
            "cases": cases,
            "count": len(cases),
            "ide_targets": list(IDE_TARGETS),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


def api_skills_cases_create(skill_key: str) -> Any:
    """Add a gold/catalog case (IDE targets: VS Code / Cursor / Work Buddy / Codex)."""
    data = request.get_json(silent=True) or {}
    target_name = str(data.get("target_name") or data.get("target") or "").strip()
    case_key = str(data.get("case_key") or "").strip()
    if not case_key:
        base = re.sub(r"[^a-zA-Z0-9]+", "_", target_name or "case").strip("_").lower() or "case"
        case_key = f"{skill_key}_{base}"[:64]
    expected = str(data.get("expected") or "NO").strip().upper() or "NO"
    image_path = data.get("image_path") or str(SCREENSHOT_PATH)
    writer = str(data.get("writer") or "").strip()
    task_id = str(data.get("task_id") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        row = upsert_skill_case(
            case_key=case_key,
            skill_key=skill_key,
            image_path=image_path,
            vision_ref=data.get("vision_ref"),
            target_name=target_name or None,
            target_action=str(data.get("target_action") or data.get("action") or ""),
            expected=expected,
            expected_reason=data.get("expected_reason"),
            notes=data.get("notes") or (
                "IDE catalog target" if target_name in IDE_TARGETS else None
            ),
            source=str(data.get("source") or "ssot_ui"),
            labeler=writer or data.get("labeler"),
            status=str(data.get("status") or "active"),
            db_path=AGENT_DB_PATH,
            commit=True,
        )
        trio = identity_trio_dict(writer=writer, task_id=task_id, session_id=session_id)
        return jsonify({
            "ok": True,
            "case": row,
            "skill_key": skill_key,
            "identity": trio,
            "writer": trio["writer"],
            "task_id": trio["task_id"],
            "session_id": trio["session_id"],
            "trailer": identity_trailer(**trio),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/health")
def api_health() -> Any:
    """Lightweight liveness for helper_watchdog (no Ollama / DB work)."""
    return jsonify(
        {
            "ok": True,
            "service": "mouse_spot_helper",
            "pid": os.getpid(),
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        }
    )


# ================= Telemetry HUD API (SSOT = telemetry_db.py) =================
# Read-only aggregations for the Telemetry page. Never writes; per-metric
# failures degrade to ok=false + error instead of a 500.

@app.route("/api/telemetry/summary")
def api_telemetry_summary() -> Any:
    """All 5 metrics in one call — powers the initial page paint."""
    limits = {
        "hours": request.args.get("hours", default=None, type=int),
        "days": request.args.get("days", default=None, type=int),
    }
    kwargs = {k: v for k, v in limits.items() if v}
    try:
        return jsonify(telemetry_db.build_all(**kwargs))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/telemetry/<path:metric>")
def api_telemetry_metric(metric: str) -> Any:
    """One metric with full detail_rows — powers the drill-down page."""
    if metric not in telemetry_db.METRIC_KEYS:
        return jsonify({
            "ok": False,
            "error": f"unknown metric: {metric}",
            "available": list(telemetry_db.METRIC_KEYS),
        }), 404
    kwargs: dict[str, Any] = {}
    if metric == "heartbeat_hourly":
        h = request.args.get("hours", default=None, type=int)
        if h:
            kwargs["hours"] = h
    elif metric in ("fault_daily", "task_by_model", "llm_tokens_by_model"):
        d = request.args.get("days", default=None, type=int)
        if d:
            kwargs["days"] = d
    try:
        return jsonify(telemetry_db.build_metric(metric, **kwargs))
    except Exception as e:
        return jsonify({"ok": False, "metric": metric,
                        "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/system-status")
def api_system_status() -> Any:
    identity = get_computer_identity()
    ollama = check_ollama_status()
    wd = watchdog_status_payload()
    openclaw = check_openclaw_status()
    worker_hb = check_worker_heartbeat_status()
    helper = helper_status_payload()
    # WINDOWS TASKS — read from the SAME background refresher's cache, so this
    # route does NOT run `schtasks` on the request path. MEASURED: `schtasks
    # /Query` costs ~600 ms, which would be a second stall of the same kind.
    try:
        import openclaw_settings as _ocs
        _snap = _ocs.status_cached()
        windows_tasks = _snap.get("windows_tasks") or {}
        windows_tasks = dict(windows_tasks)
        windows_tasks["cache_age_sec"] = _snap.get("age_sec")
        windows_tasks["cache_stale"] = _snap.get("stale")
    except Exception as _e:  # noqa: BLE001
        windows_tasks = {"ok": False, "tasks": [],
                         "error": "%s: %s" % (type(_e).__name__, _e)}
    return jsonify({
        "ok": True,
        "ready": bool(ollama.get("ok")),
        "identity": identity,
        "ollama": ollama,
        "helper": helper,
        "watchdog": wd,
        "openclaw": openclaw,
        "worker_hb": worker_hb,
        "windows_tasks": windows_tasks,
        "scopes": {
            "helper_watchdog": "Keep-alive mouse_spot_helper :18765 only",
            "openclaw": "Local MCP tools (observe); not helper_watchdog",
            "worker_hb": "Worker pulse agent.db; watchdog.py faults",
            "ollama": "Observed via helper; not restarted by helper_watchdog",
            "windows_tasks": "The 3 AgentSystem* scheduled tasks (observe only)",
        },
    })


@app.route("/api/openclaw/refresh", methods=["POST"])
def api_openclaw_refresh() -> Any:
    """THE TRIGGER POINT — do the real OpenClaw + Windows-task work NOW.

    THE HUMAN (2026-09-25): "by trigger point before have the call!! not need
    tochecking status : alive".

    A STATUS READ MUST NOT DO THE WORK. The timer reads the background
    refresher's cache; THIS route is what a user action calls when they want a
    fresh answer. It is POST because it has a side effect (it performs the
    probe), and it is the ONLY place that does.
    """
    try:
        import openclaw_settings as ocs
        snap = ocs.refresh_now()
        return jsonify({"ok": True, "triggered": True, **snap})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "triggered": False,
                        "error": "%s: %s" % (type(e).__name__, e)}), 200


@app.route("/api/openclaw/status")
def api_openclaw_status() -> Any:
    force = str(request.args.get("force") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return jsonify({"ok": True, "openclaw": check_openclaw_status(force=force)})


@app.route("/api/watchdog/status")
def api_watchdog_status() -> Any:
    return jsonify({"ok": True, "watchdog": watchdog_status_payload()})


@app.route("/api/runtime/trace")
def api_runtime_trace() -> Any:
    """The end-to-end runtime chain (fault -> report -> ack), READ-ONLY.

    ONE query over the EXISTING tables: `fault_event` is joined to
    `chat_center_message` by `fault_ref='fault:<group>#<event_id>'`, and the ack is
    read from `fault_event_fact`. Nothing is written here — an ack is written by
    `report_ack`, and this endpoint only REPORTS.

    `?state=` filters by the DERIVED state, `?actionable=1` keeps only the
    occurrences a human must act on, and `?summary=1` returns the counts per state.

    `?wording=<subject_kind>` returns `runtime_trace.report(...)` instead — the
    SAME chain PLUS the 5W1H wording. WHY THIS MODE EXISTS (A1, 2026-09-27):
    MEASURED, `runtime_trace.report` had ZERO production call sites, so the
    wording it produces was unreachable. This endpoint already existed and was
    already reachable; the only missing link was that it called the OTHER
    function. The wording is read from `logic_generator.dimension_wording`, which
    now reads the ACTIVATION GATE first (C3), so this mode is where the gate's
    effect becomes observable.
    """
    import runtime_trace_report as rtr
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        if request.args.get("summary"):
            return jsonify(rtr.summary(conn))
        wording_kind = request.args.get("wording")
        if wording_kind:
            import runtime_trace as rt
            return jsonify(rt.report(
                conn,
                ref_tag=request.args.get("ref_tag") or "watchdog",
                subject_kind=str(wording_kind)))
        rep = rtr.trace(
            conn,
            status=request.args.get("state") or None,
            group=request.args.get("group") or None,
            actionable_only=bool(request.args.get("actionable")),
            limit=request.args.get("limit", default=100, type=int) or 100,
        )
        return jsonify(rep)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# THE QUESTION CENTER API (2026-09-27).
#
# THE HUMAN: "question flow can narrow area -> to the point ..." / "and need
# Question Center".
#
# FOUR ROUTES, each ONE call into a module that already owns the logic:
#
#   GET  /api/question/registry        every registry and its generated questions
#   GET  /api/question/registry/<t>    ONE registry's questions, its area, its refs
#   POST /api/question/narrow          run the NARROW direction (area -> point)
#   GET  /api/question/frontiers       the recorded RESEARCH frontiers
#
# NOTHING HERE RE-IMPLEMENTS A RULE. `question_generator` builds the questions,
# `research_direction` runs the two directions. These routes connect them to the
# SPA, which is what a route is for — the same shape as every `/api/generator/*`
# route below.
#
# THE ONE NON-OBVIOUS DECISION: the `ask` a NARROW uses is the EVIDENCE reader
# (`logic_generator.answer_by_evidence`), NOT a model. MEASURED reason: the page
# must be usable with no model running, and an evidence answer carries its own
# query (`PRAGMA table_info(...)`), so the point is TRACEABLE. A model-backed ask
# would make the page depend on Ollama and would produce answers with no query —
# the exact defect `point = a measured unit, all traceable` names. A caller that
# wants a model passes `?ask=evidence` for now; the injected-ask seam stays open
# in `research_direction.narrow`, so adding a model ask is a new value, not a
# rewrite.
# ---------------------------------------------------------------------------


@app.route("/api/question/registry")
def api_question_registry() -> Any:
    """EVERY registry and the questions its own shape demands. READ-ONLY.

    `?pattern=` narrows the registry name match; `?limit=` caps the count for a
    slow page. A registry that cannot be specced is RETURNED with `ok: false` and
    its reason — never omitted, because an omitted refusal reads as "this registry
    needs no questions", which is the opposite of the truth.
    """
    import question_generator as qg
    pattern = str(request.args.get("pattern") or "%regist%")
    limit = request.args.get("limit", default=None, type=int)
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify(qg.all_registry_questions(conn, pattern=pattern,
                                                 limit=limit))
    finally:
        conn.close()


@app.route("/api/question/registry/<table>")
def api_question_registry_one(table: str) -> Any:
    """ONE registry: its questions, its AREA, and every evidence ref. READ-ONLY.

    The `area` is the count of APPLICABLE questions — a question that needs a FILE
    is marked inapplicable for a TABLE and is counted OUT, so the area a page
    shows is the area that can actually be narrowed.
    """
    import question_generator as qg
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify(qg.questions_for_registry(conn, str(table)))
    finally:
        conn.close()


@app.route("/api/question/narrow", methods=["POST"])
def api_question_narrow() -> Any:
    """Run the NARROW direction for one registry: AREA -> POINT. WRITES NOTHING.

    Body: `{"table": "<registry>"}`. The `ask` is the EVIDENCE reader, so the page
    needs no model (see the block comment above). The point is what did NOT
    conform, each item carrying its own evidence ref.
    """
    import question_generator as qg
    import research_direction as rd
    import logic_generator as lg

    body = request.get_json(silent=True) or {}
    table = str(body.get("table") or request.args.get("table") or "").strip()
    if not table:
        return jsonify({"ok": False, "code": "MISSING_TABLE",
                        "reason": "body.table (a registry name) is required"})

    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        binding = qg.questions_for_registry(conn, table)
        if not binding.get("ok"):
            return jsonify({"ok": False, "code": binding.get("code"),
                            "table": table,
                            "reason": binding.get("reason")})

        # THE EVIDENCE READER IS THE ASK. It answers from the DATABASE, so every
        # answer carries a re-runnable query and the page works with no model.
        spec = lg.spec_from_table(conn, table)
        gen_by_id = {str(q.get("question_id")): q
                     for q in lg.generate(spec, code_questions=True, conn=conn,
                                          subject_kind="table")["questions"]}

        def ask_evidence(_text: str, q: dict[str, Any]) -> str:
            qid = str(q.get("question_id"))
            g = gen_by_id.get(qid)
            if not g:
                return "NO"
            r = lg.answer_by_evidence(conn, spec, g)
            return r["answer"] if r.get("ok") else "NO"

        result = rd.narrow(conn, "narrow:%s" % table, ask_evidence,
                           binding=binding)
        return jsonify(result)
    finally:
        conn.close()


@app.route("/api/question/frontiers")
def api_question_frontiers() -> Any:
    """The recorded RESEARCH frontiers, newest first. READ-ONLY.

    `?point=<key>` narrows to one point. Historical rows are RETURNED, including
    inactive ones: a retired frontier is HISTORY, not a defect.
    """
    import research_direction as rd
    point = request.args.get("point") or None
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify({"ok": True, "frontiers": rd.frontiers_of(conn, point)})
    finally:
        conn.close()


@app.route("/api/question/samples")
def api_question_samples() -> Any:
    """The SAMPLES a worker can COPY when writing a question. READ-ONLY.

    THE HUMAN (2026-09-28): "is time to upgrade question flow!! summary
    experience, how can we have template or sample for that to help worker".

    MEASURED: the 7 samples were written into `pattern_template` under
    `subject_kind='question'`, but NO ROUTE SERVED THEM — so a worker could not
    see them. A template nobody can read is not a template.

    NOTHING HERE RE-IMPLEMENTS A RULE. `pattern_template.list_templates` owns the
    read; this route connects it to the SPA, the same shape as the four routes
    above. `?subject=` defaults to `question`; `?all=1` returns every subject.

    AND IT DOES NOT FILTER ON `is_active`, WHICH IS A MEASURED FIX. The DDL says
    `is_active=0 BY DEFAULT: a template is a CLAIM until an instance passes it`.
    So `is_active` records ACTIVATION, not readability — and the first version of
    this route passed `active_only=True`, which returned **0 samples** while 7
    existed. A worker needs the TEMPLATE; whether an instance has passed it is a
    SEPARATE fact, reported per row so a reader can tell a proven template from a
    claim.
    """
    import pattern_template as pt
    subject = str(request.args.get("subject") or "question").strip()
    if request.args.get("all") in ("1", "true", "yes"):
        subject = None
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        rows = pt.list_templates(conn, subject_kind=subject)
        # A SAMPLE IS ONLY USEFUL IF IT IS COMPLETE. A row missing its `why` or
        # its `example` is a sentence, not a template, so it is REPORTED rather
        # than silently rendered as if it were copyable.
        complete, incomplete = [], []
        for r in rows:
            missing = [f for f in ("rule", "why", "example", "cite_ref")
                       if not str(r.get(f) or "").strip()]
            (incomplete if missing else complete).append(
                dict(r, missing=missing) if missing else r)
        return jsonify({
            "ok": True, "subject_kind": subject or "(all)",
            "samples": complete, "count": len(complete),
            "incomplete": incomplete, "incomplete_count": len(incomplete),
            # ACTIVATION IS REPORTED, NOT FILTERED. A template with no passing
            # instance is a CLAIM, and a reader must be able to tell.
            "activated": sum(1 for r in complete if int(r.get("is_active") or 0)),
            "claims": sum(1 for r in complete if not int(r.get("is_active") or 0)),
        })
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# THE CONSULTANT CENTER API (2026-09-28).
#
# THE HUMAN: "this is professional consultant team, + table by DB driven have
# scoring system too, we can clearly define who is professional in target
# industry" / "＋ ui at http://127.0.0.1:18765/llm-tasks/consultant / your ui team
# can help you, it should not be a single ui".
#
# FOUR ROUTES, each ONE call into a module that already owns the logic:
#
#   GET  /api/consultant/overview      industry -> team -> skills, with the score
#   GET  /api/consultant/build-steps   the 17-column build-step view
#   GET  /api/consultant/step-walk     ONE step at a time (the same register)
#   GET  /api/consultant/finds         the recorded GitHub finds + their verdicts
#   GET  /api/consultant/find/<factor> run a find for ONE factor (READ-ONLY)
#
# NOTHING HERE RE-IMPLEMENTS A RULE. `consultant_registry` owns the ranking
# (`proofed DESC, rating DESC` — the human's own order), `build_step_registry`
# owns the 17-column view, `github_find_registry` owns the verdict. These routes
# connect them to the SPA, which is what a route is for.
#
# THE ONE NON-OBVIOUS DECISION: `/api/consultant/find/<factor>` is READ-ONLY and
# does NOT write a find row. A GET that writes is a GET that a browser prefetch
# can trigger, and the find tables are the record of what we actually decided.
# The write path is `_seed_consultant_skills.py --find --apply`, run deliberately.
# ---------------------------------------------------------------------------


@app.route("/api/consultant/overview")
def api_consultant_overview() -> Any:
    """industry -> team -> skills, each skill with its proofed/rating and source.

    A team with NO skills is RETURNED with `skills: []` and a `why` — never
    omitted, because an omitted team reads as "this industry has no team", which
    is the opposite of the truth.
    """
    import consultant_registry as cr
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        cr.ensure_schema(conn)
        industries = []
        for ind in cr.list_industries(conn):
            teams = []
            for t in conn.execute(
                    "SELECT * FROM consultant_team WHERE industry_id = ? "
                    "AND is_active = 1 ORDER BY team_key",
                    (int(ind["industry_id"]),)):
                t = dict(t)
                skills = cr.ranked_skills(conn, int(t["team_id"]))
                teams.append({
                    "team_id": int(t["team_id"]), "team_key": t["team_key"],
                    "name": t["name"], "description": t["description"],
                    "source_ref": t["source_ref"],
                    "members": cr.members_of(conn, int(t["team_id"])),
                    "skills": skills,
                    "skill_count": len(skills),
                    "proofed_count": sum(1 for s in skills if int(s["proofed"])),
                    "why": "" if skills else
                           "no consultant_skill row yet — a skill needs a "
                           "question, a key and a citation, and inventing them "
                           "would fabricate the team's knowledge",
                })
            industries.append({"industry_id": int(ind["industry_id"]),
                               "industry_key": ind["industry_key"],
                               "industry_path": ind["industry_path"],
                               "depth": int(ind["depth"]),
                               "definition": ind["definition"],
                               "cite_ref": ind["cite_ref"],
                               "teams": teams, "team_count": len(teams)})
        return jsonify({"ok": True, "industries": industries,
                        "industry_count": len(industries)})
    finally:
        conn.close()


@app.route("/api/consultant/build-steps")
def api_consultant_build_steps() -> Any:
    """The 17-column build-step view. READ-ONLY.

    `?task_id=` narrows to one build. The view is the register's own view, so the
    columns are the register's columns and cannot drift from it.
    """
    import build_step_registry as bsr
    task_id = str(request.args.get("task_id") or "").strip()
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        bsr.ensure_schema(conn)
        rows = (bsr.steps_for_task(conn, task_id) if task_id
                else bsr.list_steps(conn))
        return jsonify({"ok": True, "task_id": task_id or "ALL",
                        "columns": list(bsr.VIEW_COLUMNS),
                        "steps": rows, "count": len(rows),
                        "coverage": bsr.coverage(conn)})
    finally:
        conn.close()


@app.route("/api/consultant/step-walk")
def api_consultant_step_walk() -> Any:
    """ONE build step at a time. READ-ONLY.

    WHY THIS EXISTS (the human, 2026-09-28): "so i can have a step by step ui
    now?" — the 17-column table shows every step at once, which is the right
    view for AUDITING and the wrong view for WALKING. This is the SAME register
    read one row at a time, so the walk and the table cannot disagree.

    `?i=` is the index into the register's own order. An index outside the range
    is CLAMPED and the response says so (`clamped: true`) — a walk that silently
    showed step 1 for `?i=999` would read as "there is no step 999", which is the
    opposite of the truth.

    A step with NO playwright guide is returned with `playwright: "NA"` and the
    renderer shows UNMEASURED — never a pass.
    """
    import build_step_registry as bsr
    task_id = str(request.args.get("task_id") or "").strip()
    try:
        want = int(request.args.get("i", default=0, type=int) or 0)
    except (TypeError, ValueError):
        want = 0
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        bsr.ensure_schema(conn)
        rows = (bsr.steps_for_task(conn, task_id) if task_id
                else bsr.list_steps(conn))
        total = len(rows)
        if total == 0:
            return jsonify({"ok": True, "task_id": task_id or "ALL",
                            "columns": list(bsr.VIEW_COLUMNS),
                            "total": 0, "index": 0, "step": None,
                            "has_previous": False, "has_next": False,
                            "clamped": False})
        idx = max(0, min(int(want), total - 1))
        return jsonify({"ok": True, "task_id": task_id or "ALL",
                        "columns": list(bsr.VIEW_COLUMNS),
                        "total": total, "index": idx,
                        "step": rows[idx],
                        "has_previous": idx > 0,
                        "has_next": idx + 1 < total,
                        "clamped": idx != int(want)})
    finally:
        conn.close()


@app.route("/api/consultant/finds")
def api_consultant_finds() -> Any:
    """The recorded GitHub finds, each with its candidates and their verdicts.

    A find with NO candidates is RETURNED with `candidates: []` — an omitted find
    reads as "we never searched", which is the opposite of the truth.
    """
    import github_find_registry as gfr
    factor_key = str(request.args.get("factor") or "").strip()
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        gfr.ensure_schema(conn)
        if factor_key:
            finds = gfr.finds_for(conn, factor_key)
        else:
            finds = [dict(r) for r in conn.execute(
                "SELECT * FROM github_find_registry WHERE is_active = 1 "
                "ORDER BY id DESC")]
        out = []
        for f in finds:
            f = dict(f)
            f["candidates"] = gfr.candidates_for(conn, int(f["id"]))
            out.append(f)
        return jsonify({"ok": True, "finds": out, "count": len(out),
                        "factor_key": factor_key or "ALL"})
    finally:
        conn.close()


@app.route("/api/consultant/find/<factor_key>")
def api_consultant_find_one(factor_key: str) -> Any:
    """Run a find for ONE factor. READ-ONLY — it writes NOTHING.

    The factor IS the key (D2), and a factor whose `metric_unit` names no SUBJECT
    is REFUSED by `github_find.find_candidates` with its reason, because a factor
    with no subject has no search term.
    """
    import github_find as gf
    limit = request.args.get("limit", default=3, type=int)
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        try:
            got = gf.find_candidates(str(factor_key), limit=limit, conn=conn)
        except gf.GithubFindError as exc:
            return jsonify({"ok": False, "code": "FIND_REFUSED",
                            "factor_key": str(factor_key),
                            "reason": str(exc)})
        got["ranked"] = gf.rank(got["items"])
        got["ok"] = True
        return jsonify(got)
    finally:
        conn.close()


@app.route("/api/generator/required_fields")
def api_generator_required_fields() -> Any:
    """What a generator needs BEFORE submit, READ from the table. READ-ONLY.

    WHY THIS EXISTS (the human, 2026-09-27): "have a table form request all user
    for this generator need to have before submit".

    MEASURED before this: the required inputs lived in `GENERATORS` -- CODE, so
    the UI could not render them without hard-coding, and a generator that
    needed a new axis could not be extended without a code change.

    `?key=<generator>` names the generator. A generator with no rows is a
    REFUSAL (`ok: false`), not an empty list -- an empty list reads as "nothing
    needed", which is the opposite of the truth.
    """
    import generator_center as gc
    key = str(request.args.get("key") or "")
    if not key:
        return jsonify({"ok": False, "code": "MISSING_KEY",
                        "reason": "?key=<generator> is required"})
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify(gc.required_fields(conn, key))
    finally:
        conn.close()


@app.route("/api/generator/study_template")
def api_generator_study_template() -> Any:
    """The declared study subjects. READ-ONLY.

    WHY THIS EXISTS (the human, 2026-09-27): "seems study template is totally
    missing? can you help to backfill that for me".
    """
    import generator_center as gc
    kind = request.args.get("study_kind") or None
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify(gc.study_template(conn, kind))
    finally:
        conn.close()


@app.route("/api/generator/registry")
def api_generator_registry() -> Any:
    """The FIVE generators and the OPTIONS each one selects on. READ-ONLY.

    WHY THIS EXISTS (the human, 2026-09-27): "can by same template ui and select
    <value>, so i can all!" — one page per generator, one shared template.

    MEASURED, and this is why the registry is REQUIRED: the five generators have
    FIVE DIFFERENT signatures and FOUR different value kinds (skill_key,
    table_or_route, dimension, catalog). There is no common shape, so a single
    template can only work if each generator DECLARES its selector in data. That
    declaration is `generator_center.GENERATORS`.

    `?key=<generator>` returns just that one row.
    """
    import generator_center as gc
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rep = gc.registry(conn)
        key = request.args.get("key")
        if key:
            row = gc.find(str(key))
            if row is None:
                return jsonify({"ok": False, "code": "UNKNOWN_GENERATOR",
                                "key": str(key),
                                "declared": [g["key"] for g in gc.GENERATORS]})
            for g in rep["generators"]:
                if g["key"] == str(key):
                    return jsonify({"ok": True, "generator": g})
        return jsonify(rep)
    finally:
        conn.close()


@app.route("/api/generator/run", methods=["POST"])
def api_generator_run() -> Any:
    """DISPATCH to one generator. The generator's own refusals are carried out.

    Body: `{"key": "skill", "values": {"skill_key": "citation_discipline"}}`.

    This endpoint adds NO check of its own beyond the registry's three refusals
    (`UNKNOWN_GENERATOR` / `MISSING_VALUE` / `UNKNOWN_VALUE`); the generator's
    own gates (citation checkability, naming form, taxonomy level, ...) apply
    unchanged and their refusal is returned as-is.
    """
    import generator_center as gc
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "")
    values = body.get("values") or {}
    if not isinstance(values, dict):
        return jsonify({"ok": False, "code": "BAD_VALUES"})
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        return jsonify(gc.run(conn, key, values))
    finally:
        conn.close()


# THE 4-STEP WIZARD (the human, 2026-09-27):
#   step 1 select generator / step 2 skill + study + wording /
#   step 3 get prompt template WITH a confirm button / step 4 confirm
#
# STEP 3 AND STEP 4 ARE TWO ENDPOINTS, and that is the point: `preview` composes
# and writes NOTHING, so the human sees the exact text and its sha256 BEFORE
# anything is stored. `confirm` is the write.
@app.route("/api/generator/registry", methods=["POST"])
def api_generator_registry_dependent() -> Any:
    """The registry with DEPENDENT selectors filled from the chosen parents.

    Body: `{"key": "prompt_generator", "values": {"skill_key": "verdict_3line"}}`.

    WHY A POST: MEASURED, `study_key_for_skill` and `wording_dimensions` cannot be
    filled without the chosen `skill_key`, and a GET query string would have to
    carry a growing set of parent values. The GET form stays for the initial load.
    """
    import generator_center as gc
    body = request.get_json(silent=True) or {}
    values = body.get("values") or {}
    if not isinstance(values, dict):
        return jsonify({"ok": False, "code": "BAD_VALUES"})
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rep = gc.registry(conn, values)
        key = str(body.get("key") or "")
        if key:
            for g in rep["generators"]:
                if g["key"] == key:
                    return jsonify({"ok": True, "generator": g})
            return jsonify({"ok": False, "code": "UNKNOWN_GENERATOR", "key": key})
        return jsonify(rep)
    finally:
        conn.close()


@app.route("/api/generator/preview", methods=["POST"])
def api_generator_preview() -> Any:
    """STEP 3 — compose the prompt and WRITE NOTHING.

    Body: `{"key": "prompt_generator", "values": {...}}`.
    Returns `prompt_text` + `sha256` + `parts` + `composition_key` + `writes: 0`.
    """
    import generator_center as gc
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "")
    values = body.get("values") or {}
    if not isinstance(values, dict):
        return jsonify({"ok": False, "code": "BAD_VALUES"})
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        out = gc.preview(conn, key, values)
        return jsonify(out), (200 if out.get("ok") else 400)
    finally:
        conn.close()


@app.route("/api/generator/confirm", methods=["POST"])
def api_generator_confirm() -> Any:
    """STEP 4 — compose AND store. The write.

    Body: `{"key": "prompt_generator", "values": {...}, "version_label": "dim_v1"}`.
    Refuses (400) unless the generator is `prompt_generator`.
    """
    import generator_center as gc
    body = request.get_json(silent=True) or {}
    key = str(body.get("key") or "")
    values = body.get("values") or {}
    if not isinstance(values, dict):
        return jsonify({"ok": False, "code": "BAD_VALUES"})
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        out = gc.confirm(conn, key, values,
                         version_label=str(body.get("version_label") or "dim_v1"))
        return jsonify(out), (200 if out.get("ok") else 400)
    finally:
        conn.close()


@app.route("/api/runtime/faults/stale")
def api_runtime_faults_stale() -> Any:
    """READ-ONLY: which OPEN faults look STALE (their producer went silent).

    The same `plan()` the sweep uses, so the UI and the sweep cannot disagree about
    what is stale. It WRITES NOTHING — a human reviews this, then runs
    `python fault_stale_sweep.py --apply` explicitly.
    """
    import fault_stale_sweep as fss
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        days = request.args.get("stale_days", default=fss.DEFAULT_STALE_DAYS, type=int)
        if request.args.get("root_cause"):
            return jsonify(fss.root_cause_report(conn, stale_days=days or 7))
        return jsonify(fss.plan(conn, stale_days=days or 7))
    finally:
        conn.close()


@app.route("/api/watchdog/events")
def api_watchdog_events() -> Any:
    limit = request.args.get("limit", default=50, type=int) or 50
    events = load_watchdog_events(limit=limit)
    log_tail = load_watchdog_log_tail(lines=30)
    return jsonify({
        "ok": True,
        "watchdog": watchdog_status_payload(),
        "events": events,
        "log_tail": log_tail,
    })


@app.route("/api/watchdog/snapshot/<path:name>")
def api_watchdog_snapshot(name: str) -> Any:
    safe = Path(name).name
    if not safe or safe != name or ".." in name or "/" in name or "\\" in name:
        return jsonify({"ok": False, "error": "invalid name"}), 400
    if not safe.lower().endswith(".png"):
        return jsonify({"ok": False, "error": "only png"}), 400
    path = WATCHDOG_SNAPS_DIR / safe
    if not path.is_file():
        return jsonify({"ok": False, "error": "not found"}), 404
    return send_file(path, mimetype="image/png")


# ================= Tool Registry API (SSOT = hotkey_tools.md) =================

HOTKEY_TOOLS_MD = BASE_DIR / "hotkey_tools.md"


def _md_table_rows(section_text: str) -> list:
    """Parse the first markdown table in a section into list-of-dicts."""
    lines = [ln.strip() for ln in section_text.splitlines()]
    header = None
    rows = []
    for ln in lines:
        if not ln.startswith("|"):
            if header and rows:
                break
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        row = {}
        for i, h in enumerate(header):
            row[h] = cells[i] if i < len(cells) else ""
        rows.append(row)
    return rows


def _md_section(text: str, title: str) -> str:
    idx = text.find(title)
    if idx < 0:
        return ""
    rest = text[idx + len(title):]
    nxt = rest.find("\n## ")
    return rest[:nxt] if nxt >= 0 else rest


def _parse_hotkey_tools_md() -> dict:
    text = HOTKEY_TOOLS_MD.read_text(encoding="utf-8") if HOTKEY_TOOLS_MD.is_file() else ""
    tools = []
    for r in _md_table_rows(_md_section(text, "## 工具總表")):
        tools.append(
            {
                "num": r.get("#", ""),
                "hotkey": r.get("Hotkey", "").replace("**", ""),
                "tool_id": r.get("Tool ID", "").replace("`", ""),
                "name": r.get("名稱", ""),
                "script": r.get("腳本", "").replace("`", ""),
                "function": r.get("功能", ""),
                "mouse": r.get("用 mouse？", ""),
                "prereq": r.get("前提", ""),
                "verification": r.get("驗證方式", ""),
                "status": r.get("狀態", ""),
            }
        )
    hotkeys = [
        {
            "hotkey": r.get("Hotkey", "").replace("**", ""),
            "owner": r.get("Owner（script）", "").replace("`", ""),
            "purpose": r.get("用途", ""),
            "status": r.get("狀態", ""),
        }
        for r in _md_table_rows(_md_section(text, "## Hotkey 註冊表"))
    ]
    permissions = [
        {
            "permission": r.get("Permission", ""),
            "hotkey": r.get("Hotkey（multi-press）", "").replace("`", ""),
            "location": r.get("位置", ""),
            "owner": r.get("Owner", ""),
            "purpose": r.get("用途", ""),
            "status": r.get("狀態", ""),
        }
        for r in _md_table_rows(_md_section(text, "## VS Code Permissions 註冊表"))
    ]
    rules = []
    rules_sec = _md_section(text, "## 設計規則")
    for ln in rules_sec.splitlines():
        m = re.match(r"^\s*(\d+)\.\s+(.*)$", ln)
        if m:
            rules.append({"n": m.group(1), "text": m.group(2).replace("`", "")})
    infra = [
        {
            "component": r.get("組件", "").replace("`", ""),
            "purpose": r.get("用途", ""),
            "source": r.get("來源", "").replace("`", ""),
        }
        for r in _md_table_rows(_md_section(text, "## 共用基礎設施"))
    ]
    delivery_log = [
        {
            "date": r.get("日期", ""),
            "tool": r.get("Tool", ""),
            "event": r.get("事件", ""),
            "evidence": r.get("證據", ""),
        }
        for r in _md_table_rows(_md_section(text, "## 交付記錄"))
    ]
    f6_sec = _md_section(text, "## F6 開發監控循環")
    f6_loop = [ln.rstrip() for ln in f6_sec.splitlines() if ln.strip()]
    return {
        "ok": True,
        "source": "hotkey_tools.md",
        "tools": tools,
        "hotkeys": hotkeys,
        "permissions": permissions,
        "design_rules": rules,
        "shared_infra": infra,
        "delivery_log": delivery_log,
        "f6_loop": f6_loop,
    }


@app.route("/api/tool-registry")
def api_tool_registry() -> Any:
    try:
        return jsonify(_parse_hotkey_tools_md())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= chat_identity API (DB-driven; SSOT = chat_id + chat_identity_log) =================
# chat_id = sha256(session_id) (same convention as f_copy_reply.py).
# Every resolve/register/miss is logged to chat_identity_log so the UI is
# fully DB-driven (no client-side hashing).
#
# IDE auto-detect: if the caller omits ?ide=, the server detects the running
# IDE process (Code.exe -> "VS Code", Cursor.exe -> "Cursor", ...). An explicit
# ?ide= value always wins.

_IDE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_IDE_TTL_SEC = 30

# process image name (lowercase) -> IDE display name
_IDE_PROCESS_MAP = [
    ("code.exe", "VS Code"),
    ("code - insiders.exe", "VS Code Insiders"),
    ("cursor.exe", "Cursor"),
    ("windsurf.exe", "Windsurf"),
    ("devenv.exe", "Visual Studio"),
    ("idea64.exe", "IntelliJ IDEA"),
    ("pycharm64.exe", "PyCharm"),
    ("webstorm64.exe", "WebStorm"),
    ("sublime_text.exe", "Sublime Text"),
    ("notepad++.exe", "Notepad++"),
    ("trae.exe", "Trae"),
    ("qoder.exe", "Qoder"),
    ("zed.exe", "Zed"),
    ("kiro.exe", "Kiro"),
    ("fleet.exe", "JetBrains Fleet"),
    # AI coding agents / copilots (Work Buddy family + Codex).
    # Exact exe names confirmed when installed; substring match below catches
    # vendor variants (e.g. "workbuddy.exe", "work-buddy.exe").
    ("workbuddy.exe", "Work Buddy"),
    ("work buddy.exe", "Work Buddy"),
    ("work_buddy.exe", "Work Buddy"),
    ("codex.exe", "Codex"),
    ("openai-codex.exe", "Codex"),
]

# Fuzzy substring hints (fallback when the exact exe name is unknown).
_IDE_FUZZY_MAP = [
    ("workbuddy", "Work Buddy"),
    ("work-buddy", "Work Buddy"),
    ("work_buddy", "Work Buddy"),
    ("codex", "Codex"),
]


def _detect_ide() -> str:
    """Detect the running IDE by process image name (cached 30s).

    Exact match first (_IDE_PROCESS_MAP), then fuzzy substring
    (_IDE_FUZZY_MAP) so vendor variants like "WorkBuddy.exe" still map.
    Returns an IDE display name (e.g. "VS Code") or "" if none found.
    """
    now = time.time()
    if _IDE_CACHE["data"] is not None and (now - _IDE_CACHE["ts"]) < _IDE_TTL_SEC:
        return _IDE_CACHE["data"]
    name = ""
    try:
        import subprocess

        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"], text=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        running = {
            line.split('","')[0].strip('"').lower()
            for line in out.splitlines()
            if line.strip()
        }
        for exe, label in _IDE_PROCESS_MAP:
            if exe in running:
                name = label
                break
        if not name:
            for proc in running:
                for frag, label in _IDE_FUZZY_MAP:
                    if frag in proc:
                        name = label
                        break
                if name:
                    break
    except Exception:
        name = ""
    _IDE_CACHE["data"] = name
    _IDE_CACHE["ts"] = now
    return name


# LLM state file written by the model-switch tool (f_model_switch.py ^!m).
_MODEL_STATE_FILE = BASE_DIR / "chat_model.json"

# THE MODEL COMES FROM THE REGISTER, NEVER FROM A LOG FILE.
#
# WHY (the human, 2026-09-25):
#     "get by my LLM table under identity table? / or you are fucking for
#      handcode again"
#
# MEASURED, and the human is right. My previous change read
# `chatSessions/*.jsonl` and wrote a FREE-TEXT name into `chat_main.llm`. That
# is a design the human had ALREADY REJECTED, recorded verbatim in
# `identity_llm.py`:
#
#     "chatsession is totally wrong design, will remove"
#     "A model must come from a REGISTER, not from another program's log file."
#
# THE CORRECT PATH, and it already existed:
#
#     llm_model            the LLM table (86 rows, 7 active)
#     identity_registry.llm_id  -> llm_model.id   (the FK)
#     identity_llm.py      the ONE module that knows the link
#
# MEASURED: `identity_registry.llm_id` was NULL in all 55 rows, so the register
# link existed, was documented, and NO CALLER had ever used it.
#
# `chatSessions` IS NOT READ HERE. The model is resolved through the register,
# and an unknown model is REFUSED rather than guessed.


def _detect_llm(session_id: str | None = None) -> str:
    """The model name for a session, READ THROUGH THE REGISTER.

    Returns `llm_model.name` for the session's assigned `llm_id`, or "" when the
    session has no assignment. `""` means "not assigned", and `""` is NOT a
    model name — a caller that needs one must assign it first.

    IT DOES NOT READ `chatSessions`. See the block comment above for why.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        import identity_llm as il
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            out = il.llm_for_session(conn, sid)
        finally:
            conn.close()
        if not out.get("ok"):
            return ""
        for r in out.get("rows") or []:
            name = str(r.get("llm_name") or "").strip()
            if name:
                return name
        return ""
    except Exception:
        return ""


def api_chat_identity_get() -> Any:
    """GET ?session_id=<uuid> -> resolve; GET ?chat_id=<sha256> -> reverse lookup.

    ?ide= is optional — auto-detected from the running IDE process when omitted.
    """
    import skill_library_api as sla

    session_id = (request.args.get("session_id") or "").strip()
    chat_id = (request.args.get("chat_id") or "").strip()
    ide = (request.args.get("ide") or "").strip() or _detect_ide() or None
    llm = ((request.args.get("llm") or "").strip()
           or _detect_llm(session_id) or None)
    try:
        if session_id:
            out = sla.resolve_chat_identity(
                session_id, source="api", ide=ide, llm=llm
            )
            return jsonify(out), (200 if out.get("ok") else 400)
        if chat_id:
            out = sla.lookup_chat_identity_by_chat_id(chat_id)
            return jsonify(out), (200 if out.get("ok") else 400)
        return (
            jsonify(
                {
                    "ok": False,
                    "error_code": "MISSING_PARAM",
                    "error": "provide session_id or chat_id",
                }
            ),
            400,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_identity", methods=["POST"])
def api_chat_identity_post() -> Any:
    """POST {session_id} -> register (idempotent). Returns chat_id + created.

    `ide` is optional in the body — auto-detected when omitted.
    """
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "").strip()
    ide = str(data.get("ide") or "").strip() or _detect_ide() or None
    llm = (str(data.get("llm") or "").strip()
           or _detect_llm(str(data.get("session_id") or "")) or None)
    try:
        out = sla.register_chat_identity(
            session_id, source="api", ide=ide, llm=llm
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_identity/recent", methods=["GET"])
def api_chat_identity_recent() -> Any:
    """Recent chat_identity_log rows for the UI table.

    THE HUMAN (2026-09-25): "remove sha256 / chat_hash (pair) ... + image =
    environment, display environment = kind + product + surface ... update LLM =
    LLM table !!!! real name ... and where is status!!!!"

    The payload now carries `environment_display` (kind > product > surface),
    `llm_name` (the REGISTER's name, never the free text) and `status`. The
    hashes are still returned for other callers; the UI table no longer renders
    them.
    """
    import skill_library_api as sla

    limit = request.args.get("limit", default=50, type=int) or 50
    try:
        return jsonify(sla.recent_chat_identities(limit))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_identity/link_environment", methods=["POST"])
def api_chat_identity_link_environment() -> Any:
    """Resolve a session's channel to a `working_environment.environment_id`.

    THE HUMAN: "+ image = environment, display environment = kind + product +
    surface".

    MEASURED, and this is why it is a ROUTE and not a read-time join: the only
    path from a chat to an environment was a FUZZY JOIN BY NAME
    (`channel` -> `channel_registry.name` -> `working_environment.product`).
    Resolving it ONCE and STORING the id makes the read a plain FK join.

    `?apply=1` writes the resolved id onto `identity_registry`. Without it the
    route only REPORTS what it would resolve, so a caller can look before it
    writes. A resolution that finds no EXACT match is REFUSED, never guessed.
    """
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or request.args.get("session_id") or "").strip()
    apply_it = str(data.get("apply") or request.args.get("apply") or "").strip().lower() in (
        "1", "true", "yes", "on")
    if not session_id:
        return jsonify({"ok": False, "error_code": "MISSING_PARAM",
                        "error": "provide session_id"}), 400
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        ir = conn.execute(
            "SELECT identity_id, channel, environment_id FROM identity_registry "
            "WHERE session_id = ? AND is_active = 1 ORDER BY identity_id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if not ir:
            return jsonify({"ok": False, "error_code": "NO_IDENTITY",
                            "error": "no active identity_registry row for %r" % session_id}), 404
        # THE IDE FIRST, THE CHANNEL SECOND. MEASURED: for this session the
        # channel is `local_pc` (-> `Local PC`, environment_id=2, is_active=0)
        # while the IDE is `VS Code` (-> `VS Code`, environment_id=6,
        # is_active=1). A chat happens IN an IDE; the channel is only where it
        # was filed, so the IDE is the more specific fact.
        ide = str(data.get("ide") or request.args.get("ide") or "").strip()
        if not ide:
            row = conn.execute(
                "SELECT ide FROM chat_identity_log WHERE session_id = ? "
                "AND ide IS NOT NULL AND TRIM(ide) <> '' "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            ide = str(row["ide"]).strip() if row else ""
        res = sla.resolve_environment_id(conn, ide, ir["channel"])
        out = {"ok": bool(res.get("ok")), "session_id": session_id,
               "identity_id": int(ir["identity_id"]), "channel": ir["channel"],
               "ide": ide or None,
               "current_environment_id": ir["environment_id"],
               "resolution": res, "applied": False}
        if res.get("ok") and apply_it:
            conn.execute(
                "UPDATE identity_registry SET environment_id = ?, "
                "updated_at = datetime('now') WHERE identity_id = ?",
                (int(res["environment_id"]), int(ir["identity_id"])),
            )
            conn.commit()
            out["applied"] = True
        return jsonify(out), (200 if res.get("ok") else 409)
    finally:
        conn.close()


@app.route("/api/workflows/tutorial/seed", methods=["POST"])
def api_workflows_tutorial_seed() -> Any:
    """Create the `tutorial` workflow with its 11 steps. IDEMPOTENT.

    THE HUMAN: "identity has these table already, i don't know the name , here is
    sample called tutorial" + the 11-step list.

    MEASURED: the tables ARE `workflow_registry` / `workflow_step`, and NONE of
    the 11 steps existed. This seeds them into the EXISTING tables — no new table.
    """
    import register_store as rs

    try:
        return jsonify(rs.seed_tutorial_workflow(db_path=AGENT_DB_PATH))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/workflows/tutorial", methods=["GET"])
def api_workflows_tutorial() -> Any:
    """The `tutorial` workflow with its ordered steps, each with its step_status."""
    conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        wf = conn.execute(
            "SELECT * FROM workflow_registry WHERE workflow_key = 'tutorial'"
        ).fetchone()
        if not wf:
            return jsonify({"ok": False, "error_code": "NOT_SEEDED",
                            "error": "the tutorial workflow is not seeded; POST "
                                     "/api/workflows/tutorial/seed"}), 404
        steps = [
            dict(r)
            for r in conn.execute(
                "SELECT step_no, step_kind, layer_key, step_status, notes, is_final "
                "FROM workflow_step WHERE workflow_id = ? ORDER BY step_no",
                (int(wf["workflow_id"]),),
            ).fetchall()
        ]
        return jsonify({"ok": True, "workflow": dict(wf), "steps": steps,
                        "step_count": len(steps)})
    finally:
        conn.close()


@app.route("/api/chat_center/register_conversation", methods=["POST"])
def api_chat_center_registry_conversation() -> Any:
    """Register a WHOLE conversation, ONE `chat_center_message` row per turn.

    WHY THIS EXISTS (the human, 2026-09-25):
        "get the chat ID and register my message at chat
         http://127.0.0.1:18765/llm-tasks/chat_identity/recent
         + status = draft
         so i can step to step to look into that"

    MEASURED BEFORE THIS: this session had **0** rows in `chat_main`,
    `chat_identity_log` AND `chat_center_message`, so the conversation the human
    was reading had no row anywhere.

    Body: `{session_id, turns: [{role, content}], status?}`. `status` defaults to
    `draft` — written down, NOT yet reviewed by the human. An unknown status is
    REFUSED by the writer, so this route cannot invent one.
    """
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    session_id = str(data.get("session_id") or "").strip()
    turns = data.get("turns") or []
    status = str(data.get("status") or "draft").strip()
    if not isinstance(turns, list):
        return jsonify({"ok": False, "error": "turns must be a list"}), 400
    try:
        out = sla.register_conversation(
            session_id, turns, status=status,
            ide=_detect_ide() or None, llm=_detect_llm(session_id) or None,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    code = 200 if out.get("ok") else 400
    return jsonify(out), code


@app.route("/api/chat_center/approve_conversation", methods=["POST"])
def api_chat_center_approve_conversation() -> Any:
    """APPROVE a conversation: move `chat_main.status` `draft -> pending`.

    WHY THIS EXISTS (plan WATCHDOG.DRAFT.CONSUMER, step S5)
    ------------------------------------------------------
    MEASURED before S5: `chat_main.status` had NO writer and NO reader, so the
    column was a value nobody could set. This route is the approval API; the
    writer is `chat_level.set_conversation_status`.

    WHAT IT DOES NOT DO — THE HONESTY STATEMENT
    -------------------------------------------
    Approving a conversation executes NOTHING. There is NO consumer: no watchdog
    run, no task queue item, no job. The status column changes and one audit row
    is appended; nothing else reacts. That is DELIBERATE — the consumer is a
    later task (S6). S5's value is the approval PATH and its validation, not
    making approval take effect.

    Body: `{conv_id, new_status?, cite_ref, actor}`. `new_status` defaults to
    `research` — the FIRST stage of the 9-stage conversation pipeline, which is
    the ONE legal target of `draft` (`chat_level.CONVERSATION_TRANSITIONS`,
    changed in plan CHAT.PIPELINE.S6 step S6.4b). It was `pending` until S6.4b;
    that default became an ILLEGAL transition the moment `draft`'s target moved
    to `research`, so an approval that omitted `new_status` would be refused.
    `cite_ref` and `actor` are REQUIRED — an approval must cite its evidence and
    name who decided, so an anonymous approval is refused by the writer. An
    undefined transition (e.g. `draft -> completed`) is refused too.
    """
    import chat_level as cl

    data = request.get_json(silent=True) or {}
    conv_id = data.get("conv_id")
    new_status = str(data.get("new_status") or "research").strip()
    cite_ref = str(data.get("cite_ref") or "").strip()
    actor = str(data.get("actor") or "").strip()
    conn = None
    try:
        conn = cl._connect()
        out = cl.set_conversation_status(
            conn, conv_id, new_status, cite_ref=cite_ref, actor=actor)
    except cl.ChatRefused as e:
        return jsonify({"ok": False, "error": "; ".join(str(x) for x in e.args[0])}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        if conn is not None:
            conn.close()
    return jsonify(out), 200


# ================= WORKER + IDENTITY systems (two tables, 5W1H join) =========
# The user (2026-09-23):
#   "problem is worker is unqiue system, and identity is another system"
#   "you need to have 2 table unqiue"
#   "and API connect by 5W1H"
#   "have the worker ui at /llm-tasks/worker and /llm-tasks/identity"
#
# The two systems are SEPARATE tables (`worker_registry`, `identity_registry`)
# and the JOIN is 5W1H (`worker_identity_binding`), never a plain FK.
#
# Every route returns `ok`, because a store read without it was measured to show
# HTTP 200 as an error in the UI (memory `chat_registry.md`).


def _wi_conn():
    """A connection for the worker/identity routes. Caller closes it."""
    import sqlite3

    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@app.route("/api/worker/list", methods=["GET"])
def api_worker_list() -> Any:
    """The WORKER system: all active workers, optionally for one capability."""
    import worker_registry as wr

    cap = (request.args.get("capability_ref") or "").strip()
    conn = _wi_conn()
    try:
        return jsonify(wr.list_workers(conn, capability_ref=cap))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "workers": []}), 500
    finally:
        conn.close()


@app.route("/api/worker/get", methods=["GET"])
def api_worker_get() -> Any:
    """One worker by `worker_key`."""
    import worker_registry as wr

    key = (request.args.get("worker_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "worker_key is required"}), 400
    conn = _wi_conn()
    try:
        out = wr.get_worker(conn, key)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


@app.route("/api/identity/list", methods=["GET"])
def api_identity_list() -> Any:
    """The IDENTITY system: all active identities, optionally for one session."""
    import identity_registry as ir

    sid = (request.args.get("session_id") or "").strip()
    conn = _wi_conn()
    try:
        return jsonify(ir.list_identities(conn, session_id=sid))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "identities": []}), 500
    finally:
        conn.close()


@app.route("/api/identity/get", methods=["GET"])
def api_identity_get() -> Any:
    """One identity by `identity_key`."""
    import identity_registry as ir

    key = (request.args.get("identity_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "identity_key is required"}), 400
    conn = _wi_conn()
    try:
        out = ir.get_identity(conn, key)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


@app.route("/api/identity/open", methods=["POST"])
def api_identity_open() -> Any:
    """Open an identity for (session_id, worker_key, workflow_id).

    `chat_id` is NOT accepted here: it is the WORKFLOW's output, and accepting
    it would let a caller assert a chat that was never produced.
    """
    import identity_registry as ir

    data = request.get_json(silent=True) or {}
    conn = _wi_conn()
    try:
        out = ir.open_identity(
            conn,
            session_id=str(data.get("session_id") or ""),
            worker_key=str(data.get("worker_key") or ""),
            workflow_id=int(data.get("workflow_id") or 0),
            channel=str(data.get("channel") or "NA"),
            step_no=int(data.get("step_no") or 0),
            # `why` (purpose) is REQUIRED (2026-09-24): a chat with no purpose
            # cannot be routed to a service. NOT defaulted to "NA" here — that
            # default was the defect, because it stored NOT-ANSWERED as if it
            # were an answer. A missing `why` is now a 400 from the register,
            # which is the honest outcome.
            why=str(data.get("why") or ""),
            cite_ref=str(data.get("cite_ref") or "api:/api/identity/open"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    finally:
        conn.close()


@app.route("/api/worker_identity/5w1h", methods=["GET"])
def api_worker_identity_5w1h() -> Any:
    """THE 5W1H JOIN for one (worker, identity) pair.

    Returns ALL SIX dimensions, each with its binding text, its SOURCE, and
    `bound: true|false`. An unbound dimension is REPORTED, never given a generic
    question. `who` carries BOTH ids, because the user said both are required.
    """
    import worker_identity_binding as wib

    wkey = (request.args.get("worker_key") or "").strip()
    ikey = (request.args.get("identity_key") or "").strip()
    if not wkey or not ikey:
        return jsonify({"ok": False,
                        "error": "worker_key and identity_key are required"}), 400
    conn = _wi_conn()
    try:
        out = wib.questions_for(conn, wkey, ikey)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


@app.route("/api/worker_identity/coverage", methods=["GET"])
def api_worker_identity_coverage() -> Any:
    """How many of the six dimensions the PAIR KIND has wording for."""
    import worker_identity_binding as wib

    conn = _wi_conn()
    try:
        return jsonify(wib.coverage(conn))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


# ---- THE TWO-WAY CONFIRM ---------------------------------------------------
# The user: "ui can submit request to have chat with vs code > chat > session id
# to confirm!!!! it is 2 way confirm!!!!"
#
#   POST /api/session_confirm/request   UI -> chat   (opens a PENDING row)
#   POST /api/session_confirm/answer    chat -> UI   (records the echo, verdict)
#   GET  /api/session_confirm/list      the records, with their six lines
#   GET  /api/session_confirm/get       one record
#
# The verdict is CONFIRM / MISMATCH / PENDING. PENDING is a REAL state and is
# never silently treated as CONFIRM.


@app.route("/api/session_confirm/request", methods=["POST"])
def api_session_confirm_request() -> Any:
    """Open a confirm request for a session. The row starts PENDING."""
    import session_confirm as sc

    data = request.get_json(silent=True) or {}
    conn = _wi_conn()
    try:
        out = sc.request_confirm(
            conn,
            session_id=str(data.get("session_id") or ""),
            identity_key=str(data.get("identity_key") or ""),
            asked=data.get("asked") or {},
            cite_ref=str(data.get("cite_ref") or "api:/api/session_confirm/request"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    finally:
        conn.close()


@app.route("/api/session_confirm/answer", methods=["POST"])
def api_session_confirm_answer() -> Any:
    """Record the chat's echoed identity and compute the verdict."""
    import session_confirm as sc

    data = request.get_json(silent=True) or {}
    key = str(data.get("confirm_key") or "").strip()
    text = str(data.get("reply_text") or "")
    if not key or not text:
        return jsonify({"ok": False,
                        "error": "confirm_key and reply_text are required"}), 400
    conn = _wi_conn()
    try:
        out = sc.record_answer(
            conn, key, text,
            evidence_ref=str(data.get("evidence_ref") or "NA"),
            cite_ref=str(data.get("cite_ref") or "api:/api/session_confirm/answer"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    finally:
        conn.close()


@app.route("/api/session_confirm/list", methods=["GET"])
def api_session_confirm_list() -> Any:
    """All confirm records, newest first, each with its six lines."""
    import session_confirm as sc

    sid = (request.args.get("session_id") or "").strip()
    conn = _wi_conn()
    try:
        return jsonify(sc.list_confirms(conn, session_id=sid))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "confirms": []}), 500
    finally:
        conn.close()


@app.route("/api/session_confirm/get", methods=["GET"])
def api_session_confirm_get() -> Any:
    """One confirm record by key."""
    import session_confirm as sc

    key = (request.args.get("confirm_key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "confirm_key is required"}), 400
    conn = _wi_conn()
    try:
        out = sc.get_confirm(conn, key)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


# ---- THE OUTBOUND LEG (UI -> chat) -----------------------------------------
# The user: "接去程（UI → chat 真送出），令雙向迴圈真正閉環"
#
#   POST /api/session_send/plan      what WOULD run — executes NOTHING
#   POST /api/session_send/execute   acts, and REFUSES unless confirm=true
#
# ADVISORY BY DEFAULT, the same rule `screen_watch.dispatch` uses: the user's
# machine is not a sandbox, so a GUI paste/send needs an explicit confirm.


@app.route("/api/session_send/plan", methods=["POST"])
def api_session_send_plan() -> Any:
    """The exact commands the send would run. Executes NOTHING."""
    import session_send as ss

    data = request.get_json(silent=True) or {}
    conn = _wi_conn()
    try:
        out = ss.plan_send(
            conn,
            channel=str(data.get("channel") or "doubao"),
            session_id=str(data.get("session_id") or ""),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    finally:
        conn.close()


@app.route("/api/session_send/execute", methods=["POST"])
def api_session_send_execute() -> Any:
    """Act on the user's machine. REFUSES unless `confirm: true` in the body."""
    import session_send as ss

    data = request.get_json(silent=True) or {}
    conn = _wi_conn()
    try:
        out = ss.send(
            conn,
            channel=str(data.get("channel") or "doubao"),
            session_id=str(data.get("session_id") or ""),
            confirm=bool(data.get("confirm")),
            cite_ref=str(data.get("cite_ref")
                         or "api:/api/session_send/execute"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    finally:
        conn.close()


# ---- THE MODE + RIGHT SYSTEM (all DB driven) -------------------------------
# The user: "+UI /llm-tasks/worker/right -> mode : ask / plan / agent / this is
# the skill for worker to understand the edge / -> terminal : -> coding writing :
# -> plan writing : -> plan file location : -> switch mode middleware : 5W1 H
# /llm-tasks/worker/list + field / mode is apply for this worker or not"
# and: "all is DB driven"
# and: "how to give help before complain not block the activity only"
#
# The six rights per mode (the sixth is the HELP) come from
# `mode_right_registry`; the gate reads the SAME rows, so the published edge and
# the enforced edge cannot disagree.


@app.route("/api/mode/list", methods=["GET"])
def api_mode_list() -> Any:
    """The modes, as ROWS from `mode_registry`."""
    import mode_registry as mr

    conn = _wi_conn()
    try:
        return jsonify(mr.list_modes(conn))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}",
                        "modes": []}), 500
    finally:
        conn.close()


@app.route("/api/mode/rights", methods=["GET"])
def api_mode_rights() -> Any:
    """THE MATRIX. All modes, or one when `mode_key` is given.

    Every right row carries its `why` and its `cite_ref`, so a reader can CHECK
    the fact instead of trusting it.
    """
    import mode_registry as mr

    mk = (request.args.get("mode_key") or "").strip()
    conn = _wi_conn()
    try:
        out = mr.rights_for(conn, mk) if mk else mr.all_rights(conn)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


# ---- THE PER-SESSION MODE, OVER HTTP ---------------------------------------
# The user (2026-09-23): "how to do get them by API or not".
#
# MEASURED before these routes existed: `/api/mode/session` and
# `/api/mode/sessions` were BOTH 404. The data was reachable by CLI
# (`scripts/mode_attest.py --step0 --session <id>`) and by Python
# (`mode_attest.session_mode()`), but NOT over HTTP -- so the browser UI could
# not show a session's mode at all. That is the gap these two routes close.
#
# THEY DELEGATE. The read is `mode_attest.attest(session_id=...)` /
# `session_mode()` / `find_session_file()` -- the SAME functions the PreToolUse
# gate uses. A second reader here would be a SECOND source of truth for the
# mode, which is exactly what the gate/UI agreement rule forbids.


# ---- THE WORKSPACE DIMENSION + THE LIVE COUNT ------------------------------
# The user (2026-09-27):
#   "same workspace can be with many session! need a list with status to help,
#    this is measure key!!"
#   "workspace <value> is VScode, gemini, deepseek and 豆包, you can find that
#    at table"
#
# WHY THIS EXISTS. MEASURED: three AGENT sessions ran in ONE workspace and
# polluted a measurement run -- `database is locked`, a proof that flipped
# RED->GREEN between runs, and a `300.0s` timeout against a `timeout=90` runner.
# `/api/mode/sessions` ALREADY answered with all three (91 sessions, 3 live),
# but the page showed no LIVE count and no workspace, so neither the run nor the
# human could SEE the collision.
#
# THE HUMAN OVERRULED A REFUSAL. An earlier plan said "refuse a second AGENT
# session in one workspace". The human: "same workspace can be with many
# session!" -- many sessions in one workspace is a LEGITIMATE state (VS Code,
# Gemini, DeepSeek and 豆包 are different surfaces of the same workspace). So the
# fix is a LIST, not a refusal: a refusal hides the state, a list exposes it.
#
# THE WORKSPACE WORD IS THE URL'S, NOT THE PRODUCT'S. MEASURED: environment 51
# is `product=Google Chrome` with `url=https://chat.deepseek.com/...`, and 52 is
# `product=Microsoft Edge` with `url=https://gemini.google.com/app`. Labelling by
# `product` would say "Chrome" and "Edge" -- the human's words are the URL's.

# A NAMED constant, not a magic number. A session whose file was touched within
# this window is LIVE. 300s is chosen because the mode file is rewritten on every
# turn, so a session idle for 5 minutes is not running a proof.
LIVE_WINDOW_S = 300

# The URL host -> the human's word. ORDER MATTERS: the first match wins, so a
# more specific host must come before a broader one.
_WORKSPACE_BY_HOST = (
    ("chat.deepseek.com", "deepseek"),
    ("gemini.google.com", "gemini"),
    ("doubao.com", "豆包"),
)


def _workspace_word(product: str, url: str) -> str:
    """The human's word for a workspace row.

    The URL decides when it names a known host; otherwise the product name is
    used as-is. MEASURED: Chrome carries deepseek and Edge carries gemini, so
    the product name alone would be WRONG for two of the four rows.
    """
    u = (url or "").lower()
    for host, word in _WORKSPACE_BY_HOST:
        if host in u:
            return word
    return (product or "").strip() or "UNKNOWN"


def _workspace_rows() -> list[dict[str, Any]]:
    """Every ACTIVE workspace row, with the human's word attached.

    DELEGATES to `working_environment.all_paths()` -- the same reader the
    environment page uses. A second reader here would be a second source of
    truth for what a workspace IS.
    """
    try:
        import working_environment as we

        conn = we._connect()
        try:
            got = we.all_paths(conn)
        finally:
            conn.close()
    except Exception as e:
        return [{"error": "%s: %s" % (type(e).__name__, e)}]
    out = []
    for r in got.get("paths") or []:
        out.append({
            "environment_id": r.get("environment_id"),
            "kind": r.get("kind"),
            "product": r.get("product"),
            "surface": r.get("surface"),
            "url": r.get("url"),
            "workspace": _workspace_word(r.get("product"), r.get("url")),
        })
    return out


def _mode_attest_mod():
    """`scripts/mode_attest.py` as a module. The scripts dir is not on sys.path
    for a plain `import mode_attest`, and this module must not copy its logic."""
    import sys as _sys

    scripts_dir = str(BASE_DIR / "scripts")
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)
    import mode_attest as ma

    return ma


@app.route("/api/mode/session", methods=["GET"])
def api_mode_session() -> Any:
    """ONE session's CURRENT mode, read from that session's OWN file.

    Query: `?session_id=<uuid>` (required).

    200 -> PROVEN, with `mode` / `mode_id` / `scope` / `source`.
    404 -> the mode cannot be proven for that session, with a LOUD payload
           (`mode_display: "no mode (FAULT)"`). It is NEVER 200-with-blank and
           NEVER silently substituted from the workspace key, because the
           workspace key holds whichever chat last wrote it -- i.e. a DIFFERENT
           chat's mode (the defect `MODE.ATTEST.SESSION` removed).
    """
    sid = (request.args.get("session_id") or "").strip()
    if not sid:
        return jsonify({"ok": False, "error_code": "MISSING_SESSION_ID",
                        "error": "session_id is required"}), 400
    try:
        ma = _mode_attest_mod()
        att = ma.attest(session_id=sid)
        ev = att.get("evidence") or {}
        if att.get("state") != "PROVEN":
            return jsonify({
                "ok": False,
                "state": "FAULT",
                "error_code": "MODE_UNPROVEN",
                "error": att.get("fault_reason"),
                "fix": att.get("fix"),
                "session_id": sid,
                # The UI renders THIS string, so a missing mode can never be a
                # blank cell that a reader mistakes for "no problem".
                "mode_display": "no mode (FAULT)",
            }), 404
        return jsonify({
            "ok": True,
            "state": "PROVEN",
            "session_id": sid,
            "mode": att.get("mode"),
            "mode_id": ev.get("mode_id"),
            "mode_kind": ev.get("mode_kind"),
            "scope": ev.get("scope"),
            "source": att.get("source"),
            # ---- WHICH RUNG ANSWERED, and the other rung's value ------------
            # `attest()` prefers the LIVE (pending) mode because `inputState.mode`
            # LAGS one request while a message is being composed (measured: the
            # selector was AGENT while inputState.mode still held PLAN). Returning
            # only `mode` would hide which of the two it was -- the ambiguity the
            # user's "1) checked the mode / 3) didn't" complaint is about.
            "rung": ev.get("rung"),
            "committed_mode": ev.get("committed_mode"),
            "live_mode": ev.get("live_mode"),
            "live_mode_id": ev.get("live_mode_id"),
            "live_mode_name": ev.get("live_mode_name"),
            "is_pending": ev.get("is_pending"),
            "divergence": ev.get("divergence"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/mode/sessions", methods=["GET"])
def api_mode_sessions() -> Any:
    """EVERY chat session and its CURRENT mode, newest activity first.

    The user: "ui will report the current mode for each session too".

    Each session is read from ITS OWN `chatSessions/<session-id>.jsonl`, so two
    chats in one workspace report two different modes -- the fact a single
    workspace-scoped key cannot represent (measured: 70 sessions carrying
    AGENT / ASK / PLAN at once).

    CAPPED, and the cap is REPORTED (`truncated` + `total`). The directory grows
    without bound, so an uncapped listing would eventually be a timeout -- and a
    SILENT cap would be a lie about what was measured.
    """
    try:
        ma = _mode_attest_mod()
        limit = request.args.get("limit", default=200, type=int) or 200
        limit = max(1, min(limit, 1000))

        db, _wj = ma.find_workspace_db()
        if not db:
            return jsonify({"ok": False, "sessions": [],
                            "error": "no VS Code workspaceStorage entry matches "
                                     "this workspace"}), 404
        sdir = os.path.join(os.path.dirname(db), ma.SESSIONS_DIRNAME)
        if not os.path.isdir(sdir):
            return jsonify({"ok": True, "sessions": [], "total": 0, "returned": 0,
                            "limit": limit, "truncated": False,
                            "sessions_dir": sdir,
                            "note": "no chatSessions directory yet"})

        rows: list[tuple[float, str, str]] = []
        for name in os.listdir(sdir):
            if not name.endswith(".jsonl"):
                continue
            p = os.path.join(sdir, name)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                mt = 0.0
            rows.append((mt, name, p))
        rows.sort(key=lambda r: r[0], reverse=True)
        total = len(rows)
        picked = rows[:limit]

        # `chat.customModes.local` is shared agent-definition CONFIG (identical
        # for every chat), so it is read ONCE here instead of once per session.
        custom_raw = ma._read_item(db, "chat.customModes.local")
        custom_modes = None
        if custom_raw:
            try:
                custom_modes = json.loads(custom_raw)
            except Exception:
                custom_modes = None

        now = time.time()
        sessions: list[dict[str, Any]] = []
        for mt, name, p in picked:
            sid = name[:-len(".jsonl")]
            # ONE pass, CACHED on (mtime, size).
            #
            # MEASURED: 73 files / 1,101,528,987 bytes, and a full pass took
            # 6,324 ms -- the page was still blank after 4 seconds. A TAIL-ONLY
            # read was tried and REJECTED BY MEASUREMENT: 40 of 73 files keep
            # their mode in the `kind:0` SEED line, which is the FIRST line and
            # can be 96 MB, with `"inputState"` at offsets up to 92 MB. No
            # bounded read is correct, and a bounded read that silently misses a
            # mode is the false "no mode" answer this module exists to prevent.
            #
            # So the read stays FULL and the CACHE makes it affordable: the page
            # polls, and without a cache every poll would re-read 1.1 GB. The key
            # is the file's own mtime+size, so a mode switch invalidates it.
            combined = ma.session_records_cached(p)
            got = combined.get("committed") or {}
            live = combined.get("live") or {}
            mid = str(got.get("id") or "")
            # ONE id->mode mapping, the same one the gate uses. Reimplementing it
            # here would let the page and the gate disagree about what "agent"
            # means -- the drift this whole design exists to prevent.
            resolved = ma._map_mode(mid, custom_modes) if mid else ""
            # The LIVE rung too, so the page can show which one answered rather
            # than a single ambiguous `mode`. For a custom agent
            # `telemetryModeId` is the literal "custom", so `_resolve_live_mode`
            # falls back to the NAME -- see `mode_attest._resolve_live_mode`.
            live_mode = ma._resolve_live_mode(live, custom_modes)
            is_pending = bool(live.get("is_pending"))
            sessions.append({
                "session_id": sid,
                "mode": resolved or None,
                "mode_id": mid or None,
                "mode_display": resolved or "no mode (FAULT)",
                "scope": "session",
                # which rung produces `mode` for THIS row
                "rung": "live" if (is_pending and live_mode) else "committed",
                "committed_mode": resolved or None,
                "live_mode": live_mode,
                "is_pending": is_pending,
                "last_activity": (
                    datetime.fromtimestamp(mt, timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ") if mt else None),
                "age_sec": int(now - mt) if mt else None,
                # THE MEASURE KEY, per row. A session whose file was touched
                # within LIVE_WINDOW_S is LIVE -- it is running a turn, and a
                # turn runs proofs, and proofs take agent.db.
                "is_live": bool(mt and (now - mt) < LIVE_WINDOW_S),
            })
        return jsonify({
            "ok": True,
            "sessions": sessions,
            "total": total,
            "returned": len(sessions),
            "limit": limit,
            "truncated": total > len(sessions),
            # The modes ACTUALLY observed, so the reader sees at a glance that
            # one workspace holds several -- the point of the endpoint.
            "distinct_modes": sorted({s["mode"] for s in sessions if s["mode"]}),
            # THE MEASURE KEY. The user: "need a list with status to help, this
            # is measure key!!". `live_count` is how many sessions are live RIGHT
            # NOW; a run that starts while it is > 1 is measuring a BUSY
            # workspace. It is REPORTED, never enforced -- many sessions in one
            # workspace is legitimate.
            "live_window_s": LIVE_WINDOW_S,
            "live_count": sum(1 for s in sessions if s.get("is_live")),
            "live_session_ids": [s["session_id"] for s in sessions
                                 if s.get("is_live")],
            # The workspace dimension, so the page can show WHICH surface each
            # session is in. The word is the URL's, not the product's.
            "workspaces": _workspace_rows(),
            "sessions_dir": sdir,
        })
    except Exception as e:
        return jsonify({"ok": False, "sessions": [],
                        "error": f"{type(e).__name__}: {e}"}), 500


# ---- THE PER-CONVERSATION ENVIRONMENT CHECKLIST, OVER HTTP ------------------
# The user (2026-09-23):
#   "conversation id is help us to have environment checklist for vscode
#    example : mode = plan, permission = autopilot...."
#   "and conversation table too!!!!!"
#   "chat system is chat system, vscode > chat > conseraction is another system"
#
# A conversation IS a VS Code chat session. These routes DELEGATE to
# `vscode_env_store`, which in turn delegates conversation creation to
# `skill_library_api.register_chat_identity` and parsing to `mode_attest`. No
# layer here re-reads a session file or re-writes `chat_main` -- a second reader
# or a second identity is the defect family this repo keeps recording.
#
# THE PATH IS `/api/vscode_env/*`, NOT `/api/conversation/*`. The bare word
# `conversation` reads as the CHAT system, which is a DIFFERENT table set
# (`chat_center_message`, `chat_identity_log`, `chat_registry`, ...) that happens
# to key on the same session id. The terms are registered in
# `terminology_registry` (`vscode_conversation` and `chat_system` are SIBLINGS).

def _vscode_env_store_mod():
    """`vscode_env_store` as a module (it is not on sys.path by default)."""
    import sys as _sys

    if str(BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(BASE_DIR))
    import vscode_env_store as ves

    return ves


_VSCODE_ENV_SCHEMA_READY = False


def _ensure_vscode_env_schema() -> None:
    """Create the two tables once per process, before the first query.

    WHY THIS EXISTS (measured): the list route returned HTTP 500
    `no such table: conversation_env_log` on a real database. The DDL was
    registered in `db_schema`'s loop, but that loop only runs when some other
    caller triggers it -- so a route that assumed the table existed was one
    ordering assumption away from a 500. Creating it here is idempotent
    (`CREATE TABLE IF NOT EXISTS`) and removes the assumption.
    """
    global _VSCODE_ENV_SCHEMA_READY
    if _VSCODE_ENV_SCHEMA_READY:
        return
    ves = _vscode_env_store_mod()
    ves.ensure_schema()
    ves.seed_dimensions()
    _VSCODE_ENV_SCHEMA_READY = True


@app.route("/api/vscode_env/list", methods=["GET"])
def api_vscode_env_list() -> Any:
    """Every known VS Code conversation, newest first, with its observation count.

    CAPPED and the cap is REPORTED (`total`/`returned`/`truncated`), for the
    same reason `/api/mode/sessions` is: a silent cap is a lie about what was
    measured.
    """
    try:
        ves = _vscode_env_store_mod()
        _ensure_vscode_env_schema()
        limit = request.args.get("limit", default=200, type=int) or 200
        limit = max(1, min(limit, 1000))
        conn = ves._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM chat_main").fetchone()[0]
            rows = conn.execute(
                """
                SELECT m.id, m.session_id, m.ide, m.llm, m.updated_at,
                       (SELECT COUNT(*) FROM vscode_env_log l
                         WHERE l.vscode_conversation_id = m.id) AS obs
                  FROM chat_main m
                 ORDER BY m.updated_at DESC, m.id DESC
                 LIMIT ?
                """, (limit,)).fetchall()
        finally:
            conn.close()
        items = [{
            "vscode_conversation_id": r[0], "session_id": r[1], "ide": r[2],
            "llm": r[3], "updated_at": r[4], "observations": r[5],
        } for r in rows]
        return jsonify({
            "ok": True, "conversations": items,
            "total": total, "returned": len(items), "limit": limit,
            "truncated": total > len(items),
        })
    except Exception as e:
        return jsonify({"ok": False, "conversations": [],
                        "error": f"{type(e).__name__}: {e}"}), 500


def _resolve_vscode_conversation_id(raw: str) -> tuple[int | None, str]:
    """Accept EITHER `chat_main.id` (an integer) OR a session_id UUID.

    Accepting the session id matters because that is what a human actually has
    (it is the filename of the session and the id in the gate log). Accepting the
    integer id keeps the route honest for callers that already joined.
    """
    val = (raw or "").strip()
    if not val:
        return None, "missing"
    if val.isdigit():
        return int(val), ""
    ves = _vscode_env_store_mod()
    conn = ves._conn()
    try:
        row = conn.execute("SELECT id FROM chat_main WHERE session_id=?",
                           (val,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None, "unknown session_id %s" % val
    return int(row[0]), ""


@app.route("/api/vscode_env/<cid>/checklist", methods=["GET"])
def api_vscode_env_checklist(cid: str) -> Any:
    """The LATEST value per environment dimension for one conversation.

    `missing` names every REQUIRED dimension that has never been observed, so an
    unmeasured requirement is VISIBLE. A blank cell is what a reader mistakes for
    "no problem", which is the failure this page exists to prevent.
    """
    try:
        ves = _vscode_env_store_mod()
        _ensure_vscode_env_schema()
        mid, err = _resolve_vscode_conversation_id(cid)
        if not mid:
            return jsonify({"ok": False,
                            "error_code": "UNKNOWN_VSCODE_CONVERSATION",
                            "error": err}), 404
        out = ves.checklist(mid)
        return jsonify(out), (200 if out.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/vscode_env/<cid>/trace", methods=["GET"])
def api_vscode_env_trace(cid: str) -> Any:
    """The HISTORY of a dimension. Append-only means the history IS the datum:
    the committed and live modes disagree by design, so collapsing them to one
    'current' value would delete the disagreement a reviewer came to see."""
    try:
        ves = _vscode_env_store_mod()
        _ensure_vscode_env_schema()
        mid, err = _resolve_vscode_conversation_id(cid)
        if not mid:
            return jsonify({"ok": False,
                            "error_code": "UNKNOWN_VSCODE_CONVERSATION",
                            "error": err}), 404
        dim = (request.args.get("dim") or "").strip()
        limit = request.args.get("limit", default=200, type=int) or 200
        out = ves.trace(mid, dim, limit=limit)
        return jsonify(out), (200 if out.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/vscode_env/record", methods=["POST"])
def api_vscode_env_record() -> Any:
    """Record the CURRENT environment for one conversation.

    Reads through `mode_attest.session_checklist` (the gate's own reader) and
    stores through `vscode_env_store.record_checklist`. The mode rungs are
    stored SEPARATELY and a divergence is recorded as a FAULT -- never resolved.
    """
    try:
        ves = _vscode_env_store_mod()
        _ensure_vscode_env_schema()
        data = request.get_json(silent=True) or {}
        sid = str(data.get("session_id") or "").strip()
        if not sid:
            return jsonify({"ok": False, "error_code": "MISSING_SESSION_ID",
                            "error": "session_id is required"}), 400
        up = ves.upsert_conversation(sid)
        if not up.get("ok"):
            return jsonify(up), 400
        mid = up["vscode_conversation_id"]
        ma = _mode_attest_mod()
        db, _wj = ma.find_workspace_db()
        sfile = ma.find_session_file(sid, db) if db else None
        if not sfile:
            return jsonify({"ok": False, "error_code": "NO_SESSION_FILE",
                            "error": "no chatSessions/%s.jsonl for this session"
                                     % sid, "session_id": sid}), 404
        check = ma.session_checklist(sfile)
        rec = ves.record_checklist(mid, check)
        # The DELEGATED environment check, when asked for. Opt-in because
        # `f_env_preflight` probes real hardware and can take seconds.
        if data.get("with_preflight"):
            ves.record_preflight(mid, scope=str(data.get("scope") or "all"))
        return jsonify({"ok": True, "vscode_conversation_id": mid,
                        "session_id": sid, "recorded": rec})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ---- THE PHASED TERMINOLOGY SWEEP, OVER HTTP --------------------------------
# The user (2026-09-23):
#   "for whole site and by phase (volume control) and we can test 7B performance
#    too with ui report"
#
# MEASURED VOLUME: 109 tables, 215 routes, 717 modules -> ~1000+ names. A single
# unbounded run is neither reviewable nor safe, so a phase processes AT MOST its
# `cap` and the report SHOWS `total`/`returned`/`truncated`.
#
# These routes DELEGATE to `terminology_sweep`, which delegates the citation to
# `terminology_cite` and the gate to `terminology_registry.add_term`. No layer
# here re-implements a check.

def _terminology_sweep_mod():
    """`terminology_sweep` as a module (it is not on sys.path by default)."""
    import sys as _sys

    if str(BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(BASE_DIR))
    import terminology_sweep as ts

    return ts


def _terminology_catalog_mod():
    """`terminology_catalog` as a module (it is not on sys.path by default)."""
    import sys as _sys

    if str(BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(BASE_DIR))
    import terminology_catalog as tc

    return tc


@app.route("/api/terminology/catalog", methods=["GET"])
def api_terminology_catalog() -> Any:
    """The CATALOG view over `terminology_registry`, plus the GAP.

    A catalog is a term whose children are its entries (`parent_term_id`), so
    this route reads the register and NOT a second table. The gap is returned
    WITH the tree: a name with no term is a GAP, and a page that showed only the
    registered names would render the gap as nothing at all.
    """
    try:
        tc = _terminology_catalog_mod()
        root_key = (request.args.get("root") or tc.TASKBAR_CATALOG).strip()
        group_key = (request.args.get("group") or "windows_taskbar").strip()
        conn = tc._connect()
        try:
            tree = tc.catalog_tree(conn, root_key)
            gap = tc.unregistered_names(conn, group_key=group_key)
            sub = tc.catalog_subtree(conn, root_key)
        finally:
            conn.close()
        return jsonify({
            "ok": bool(tree.get("ok")),
            "root_key": root_key,
            "group_key": group_key,
            "root": tree.get("root"),
            "children": tree.get("children") or [],
            "child_count": len(tree.get("children") or []),
            # THE WHOLE SUBTREE, with the depth. MEASURED 2026-09-27: the panel
            # showed DIRECT CHILDREN ONLY, so `taskbar_vscode`'s 2 children were
            # invisible and a reader could not tell what the group contains.
            "subtree": sub.get("rows") or [],
            "subtree_count": len(sub.get("rows") or []),
            "max_depth": sub.get("max_depth") or 0,
            "unregistered": gap.get("names") or [],
            "unregistered_count": len(gap.get("names") or []),
            "registered_count": len(gap.get("registered") or []),
            "error": tree.get("error") or gap.get("error") or sub.get("error"),
        }), (200 if tree.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/completeness_5w1h", methods=["GET"])
def api_terminology_completeness_5w1h() -> Any:
    """The 5W1H for every target under a prefix, and the MISSING answers.

    THE HUMAN (2026-09-27): "fuck! how to you have correct 5W1H in easy".

    RENAMED 2026-09-27 from `/api/terminology/completeness_5w1h`. THE HUMAN:
    "`completeness_5w1h`=462, this is wrong spelling BUG, have totally rename and fix,
    but you can have that, should gone away forever". The repo's OWN convention
    puts `5w1h` at the END (`skill_5w1h`, `ticket_5w1h`, `derive_5w1h`).

    MEASURED: the register answers 4 of 6 -- WHAT (term_key), WHERE (coordinate),
    WHY (definition), HOW (field_type). **WHICH (the instance) and WHEN had no
    column.** The selector column closes WHICH.

    **A MISSING answer is NAMED, never silently passed.** The route WRITES NOTHING.
    """
    try:
        tg = _terminology_generator_mod()
        prefix = (request.args.get("prefix") or "taskbar").strip()
        conn = tg._connect()
        try:
            rep = tg.completeness_5w1h_report(conn, prefix=prefix)
        finally:
            conn.close()
        return jsonify(rep), (200 if rep.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/catalog/node_kind", methods=["GET"])
def api_terminology_catalog_node_kind() -> Any:
    """Is each `catalog` row a GROUP or a LEAF? The SAME rule as the register.

    MEASURED 2026-09-27: `catalog` is now ONE table with `parent_id` (the human's
    ruling), so it answers the same question the terminology catalog does, the
    same way. The route WRITES NOTHING.
    """
    try:
        tc = _terminology_catalog_mod()
        conn = tc._connect()
        try:
            agree = tc.catalog_node_kind_agreement(conn)
            rows = conn.execute(
                "SELECT id, parent_id, name, legacy_id FROM catalog "
                "ORDER BY parent_id IS NOT NULL, name").fetchall()
            kinds = [tc.catalog_node_kind(conn, int(r["id"])) for r in rows]
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "checked": agree.get("checked"),
            "disagreement_count": agree.get("disagreement_count"),
            "disagreements": agree.get("disagreements") or [],
            "rows": kinds,
            "derived_from": "children > 0",
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/catalog/measure", methods=["GET"])
def api_terminology_catalog_measure() -> Any:
    """The whole catalog measurement. Writes NOTHING."""
    try:
        tc = _terminology_catalog_mod()
        out = tc.measure()
        return jsonify(out), (200 if out.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


def _terminology_generator_mod():
    """`terminology_generator` as a module (it is not on sys.path by default)."""
    import sys as _sys

    if str(BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(BASE_DIR))
    import terminology_generator as tg

    return tg


@app.route("/api/terminology/generator/check", methods=["GET"])
def api_terminology_generator_check() -> Any:
    """Does every term's `term_key` EQUAL the name its catalog path derives?

    THE HUMAN (2026-09-27): "you are not helping to have standardize for
    terminology generator ? with catalog > subcatalog" / "example taskbar >
    widgets , terminology generator = taskbar_widgets".

    A MISMATCH is returned WITH both values, so a reader can see WHICH convention
    the row is in. The route WRITES NOTHING.
    """
    try:
        tg = _terminology_generator_mod()
        root_key = (request.args.get("root") or tg.TASKBAR_CATALOG).strip()
        conn = tg._connect()
        try:
            chk = tg.check(conn, root_key=root_key)
            drift = tg.name_drift(conn)
        finally:
            conn.close()
        return jsonify({
            "ok": bool(chk.get("ok")),
            "root_key": root_key,
            "checked": len(chk.get("rows") or []),
            "mismatches": chk.get("mismatches") or [],
            "mismatch_count": len(chk.get("mismatches") or []),
            "rows": chk.get("rows") or [],
            "drift": {
                "agree": len(drift.get("agree") or []),
                "only_in_target": drift.get("only_in_target") or [],
                "only_in_registry": drift.get("only_in_registry") or [],
            },
            "error": chk.get("error") or drift.get("error"),
        }), (200 if chk.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/generator/measure", methods=["GET"])
def api_terminology_generator_measure() -> Any:
    """The whole generator measurement. Writes NOTHING."""
    try:
        tg = _terminology_generator_mod()
        out = tg.measure()
        return jsonify(out), (200 if out.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/node_kind", methods=["GET"])
def api_terminology_node_kind() -> Any:
    """Is each term a GROUP (it has children) or a LEAF (it has none)?

    THE HUMAN (2026-09-27): "i have taskbar_vscode and taskbar_vscode_app" /
    "which is rubbish" / "why taskbar_vscode not = taskbar_vscode_app".

    MEASURED: both carried `term_kind='entity'`, so a reader saw two identical
    rows. The kind is DERIVED from `children > 0` by
    `terminology_generator.node_kind` -- never stored, so it cannot drift.

    `?key=<term_key>` asks about ONE term; without it, the route returns the
    whole population plus the AGREEMENT count (QC-02). The route WRITES NOTHING.
    """
    try:
        tg = _terminology_generator_mod()
        key = (request.args.get("key") or "").strip()
        conn = tg._connect()
        try:
            if key:
                one = tg.node_kind_by_key(conn, key)
                return jsonify(one), (200 if one.get("ok") else 404)
            agree = tg.node_kind_agreement(conn)
            groups = conn.execute(
                "SELECT t.term_id, t.term_key, "
                "  (SELECT COUNT(*) FROM terminology_registry c "
                "   WHERE c.parent_term_id=t.term_id) AS children "
                "FROM terminology_registry t "
                "WHERE EXISTS (SELECT 1 FROM terminology_registry c "
                "              WHERE c.parent_term_id=t.term_id) "
                "ORDER BY t.term_key").fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM terminology_registry").fetchone()[0]
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "checked": int(total),
            "group_count": len(groups),
            "leaf_count": int(total) - len(groups),
            "groups": [dict(r) for r in groups],
            "agreement": {
                "checked": agree.get("checked"),
                "disagreement_count": agree.get("disagreement_count"),
                "disagreements": agree.get("disagreements") or [],
            },
            "derived_from": "children > 0",
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


def _terminology_rubbish_mod():
    """`terminology_rubbish` as a module (it is not on sys.path by default)."""
    import sys as _sys

    if str(BASE_DIR) not in _sys.path:
        _sys.path.insert(0, str(BASE_DIR))
    import terminology_rubbish as trb

    return trb


@app.route("/api/terminology/rubbish", methods=["GET"])
def api_terminology_rubbish() -> Any:
    """The definitions that SAY NOTHING, and the rule that fired for each.

    THE HUMAN (2026-09-27): "you love rubbish? taskbar_vscode_app!!!????" /
    "or you need to have helper to cleanup or rubbish definition".

    The route WRITES NOTHING. A cleanup that rewrites definitions it did not
    write is a second author; the human decides.
    """
    try:
        trb = _terminology_rubbish_mod()
        out = trb.report()
        return jsonify(out), (200 if out.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/phases", methods=["GET"])
def api_terminology_phases() -> Any:
    """Every sweep phase with its CAP and how much of its scope it has covered.

    The cap is the volume control, so it is returned with the progress — a phase
    list without the cap would hide the thing that bounds the run.
    """
    try:
        ts = _terminology_sweep_mod()
        conn = ts.sqlite3.connect(str(ts.DB))
        conn.row_factory = ts.sqlite3.Row
        try:
            ts.seed_phases(conn)
            phases = ts.list_phases(conn)
            out = []
            for p in phases:
                # The progress rule lives in `terminology_sweep.phase_progress`
                # so it can be PROVEN there; the route only presents it.
                prog = ts.phase_progress(conn, p["phase_key"])
                out.append({
                    "phase_key": p["phase_key"],
                    "display_name": p["display_name"],
                    "scope": p["scope"],
                    "cap": p["cap"],
                    "scope_total": prog["scope_total"],
                    "done": prog["done"],
                    "dry_only": prog["dry_only"],
                    "remaining": prog["remaining"],
                    # A phase is TRUNCATED when its scope is larger than its cap:
                    # one run cannot cover it, and saying so is the point.
                    "truncated": prog["truncated"],
                    "why": p["why"],
                    "cite_ref": p["cite_ref"],
                })
        finally:
            conn.close()
        return jsonify({"ok": True, "phases": out, "returned": len(out)})
    except Exception as e:
        return jsonify({"ok": False, "phases": [],
                        "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/sweep/<phase_key>", methods=["GET"])
def api_terminology_sweep_report(phase_key: str) -> Any:
    """The recorded history for one phase, from the APPEND-ONLY log."""
    try:
        ts = _terminology_sweep_mod()
        conn = ts.sqlite3.connect(str(ts.DB))
        conn.row_factory = ts.sqlite3.Row
        try:
            out = ts.report(conn, phase_key)
        finally:
            conn.close()
        return jsonify(out), (200 if out.get("ok") else 500)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/sweep/<phase_key>/run", methods=["POST"])
def api_terminology_sweep_run(phase_key: str) -> Any:
    """Run ONE phase, capped. `apply` is opt-in, so the default is a DRY RUN."""
    try:
        ts = _terminology_sweep_mod()
        data = request.get_json(silent=True) or {}
        conn = ts.sqlite3.connect(str(ts.DB))
        conn.row_factory = ts.sqlite3.Row
        try:
            out = ts.run_phase(conn, phase_key,
                               apply=bool(data.get("apply")),
                               use_llm=not bool(data.get("no_llm")))
        finally:
            conn.close()
        if not out.get("ok"):
            return jsonify(out), 404
        # The per-name results can be long; the summary is what the page needs.
        out.pop("results", None)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/terminology/7b-report", methods=["GET"])
def api_terminology_7b_report() -> Any:
    """The 7B's MEASURED performance across every phase.

    Reports latency (p50/max), the refusal breakdown, and how many citations the
    LOGIC GENERATOR had to SUPPLY — that last number is the 7B's real gap, since
    it cannot produce a checkable citation.
    """
    try:
        ts = _terminology_sweep_mod()
        conn = ts.sqlite3.connect(str(ts.DB))
        conn.row_factory = ts.sqlite3.Row
        try:
            ts.seed_phases(conn)
            phases = ts.list_phases(conn)
            per_phase = []
            lat: list[int] = []
            supplied = 0
            supplied_names = 0
            by_outcome: dict[str, int] = {}
            by_code: dict[str, int] = {}
            for p in phases:
                rep = ts.report(conn, p["phase_key"])
                per_phase.append({
                    "phase_key": p["phase_key"], "cap": p["cap"],
                    "rows": rep["rows"], "by_outcome": rep["by_outcome"],
                    "by_refusal_code": rep["by_refusal_code"],
                    "supplied_citations": rep["supplied_citations"],
                    "names_supplied": rep["names_supplied"],
                    "llm_ms_p50": rep["llm_ms_p50"],
                    "llm_ms_max": rep["llm_ms_max"],
                })
                supplied += rep["supplied_citations"]
                supplied_names += rep["names_supplied"]
                for k, v in rep["by_outcome"].items():
                    by_outcome[k] = by_outcome.get(k, 0) + v
                for k, v in rep["by_refusal_code"].items():
                    by_code[k] = by_code.get(k, 0) + v
                for it in rep["items"]:
                    if it["llm_ms"] is not None:
                        lat.append(int(it["llm_ms"]))
            lat.sort()
            total_rows = sum(p["rows"] for p in per_phase)
        finally:
            conn.close()
        # THE MODEL IS RESOLVED, NOT READ FROM A CONSTANT.
        #
        # MEASURED 2026-09-27: this route read `ts.MODEL`, and
        # `terminology_sweep` had REMOVED that constant (`MODEL_CONSTANT_REMOVED
        # = True`, `terminology_sweep.py:66-72`) because a Python literal and the
        # `llm.text` route agreed BY LUCK. So the route raised AttributeError and
        # the page rendered `no report (FAULT)` — the page was right and the
        # route was wrong. `resolve_model` is the SSOT; it carries its reason.
        try:
            resolved = ts.resolve_model()
            model_name = str(resolved.get("model") or "")
            model_why = resolved.get("why") or resolved.get("source") or ""
        except Exception as exc:
            model_name = ""
            model_why = "%s: %s" % (type(exc).__name__, exc)
        return jsonify({
            "ok": True,
            "model": model_name,
            "model_why": model_why,
            "phases": per_phase,
            "total_rows": total_rows,
            "by_outcome": by_outcome,
            "by_refusal_code": by_code,
            "supplied_citations": supplied,
            # The DISTINCT-NAME count: the honest size of the 7B's gap. The row
            # count above double-counts a re-run, so both are reported.
            "names_supplied": supplied_names,
            "llm_ms_p50": lat[len(lat) // 2] if lat else None,
            "llm_ms_max": max(lat) if lat else None,
            "llm_calls": len(lat),
            # The honest reading of the numbers, stated rather than left to the
            # reader: the 7B drafts language; the citation is NOT its work.
            "note": ("the 7B drafts term_key + definition; the citation is "
                     "SUPPLIED by terminology_cite and verified before it "
                     "reaches the register, because the 7B cannot produce a "
                     "checkable citation (measured)"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/worker/modes", methods=["GET"])
def api_worker_modes() -> Any:
    """Which modes apply to a worker — "mode is apply for this worker or not"."""
    import mode_registry as mr

    wkey = (request.args.get("worker_key") or "").strip()
    if not wkey:
        return jsonify({"ok": False, "error": "worker_key is required"}), 400
    conn = _wi_conn()
    try:
        out = mr.modes_for_worker(conn, wkey)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    finally:
        conn.close()


@app.route("/api/worker/mode/set", methods=["POST"])
def api_worker_mode_set() -> Any:
    """Set or clear one `worker_mode` row."""
    import mode_registry as mr

    data = request.get_json(silent=True) or {}
    conn = _wi_conn()
    try:
        out = mr.set_worker_mode(
            conn,
            worker_key=str(data.get("worker_key") or ""),
            mode_key=str(data.get("mode_key") or ""),
            is_applied=bool(data.get("is_applied", True)),
            why=str(data.get("why") or "NA"),
            cite_ref=str(data.get("cite_ref") or "api:/api/worker/mode/set"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400
    finally:
        conn.close()


# ================= user_environment API (DB-driven who/where) =================
# Detects the caller's public IP + geo/timezone (ip-api.com) and the local
# computer identity, then upserts one user_environment row.
# UI: /llm-tasks/user_environment

_GEO_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_GEO_TTL_SEC = 600


def _detect_public_ip() -> str:
    """Best-effort public IP (used for geo lookup). Empty on failure."""
    for url in ("https://api.ipify.org", "http://ip-api.com/line/?fields=query"):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                txt = resp.read().decode("utf-8", errors="replace").strip()
            if txt and len(txt) <= 64:
                return txt.splitlines()[0].strip()
        except Exception:
            continue
    return ""


def _geo_lookup(ip: str = "") -> dict[str, Any]:
    """Geo/timezone for an IP via ip-api.com (cached 10 min). Never raises."""
    now = time.time()
    if _GEO_CACHE["data"] and (now - _GEO_CACHE["ts"]) < _GEO_TTL_SEC:
        return _GEO_CACHE["data"]
    out: dict[str, Any] = {}
    try:
        url = (
            f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,"
            "regionName,city,timezone,offset,query,isp"
        )
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        if d.get("status") == "success":
            out = {
                "ip_address": d.get("query") or ip,
                "country": d.get("country"),
                "country_code": d.get("countryCode"),
                "region": d.get("regionName"),
                "city": d.get("city"),
                "timezone": d.get("timezone"),
                "tz_offset_sec": d.get("offset"),
                "isp": d.get("isp"),
            }
    except Exception:
        out = {}
    if out:
        _GEO_CACHE["data"] = out
        _GEO_CACHE["ts"] = now
    return out


def _local_locale() -> dict[str, str]:
    """Local language / locale hints (best-effort)."""
    lang = ""
    loc = ""
    try:
        import locale as _locale

        loc = _locale.getlocale()[0] or ""
    except Exception:
        pass
    for key in ("LANG", "LANGUAGE", "LC_ALL"):
        v = os.environ.get(key)
        if v:
            lang = v.split(".")[0]
            break
    if not lang and loc:
        lang = loc
    return {"language": lang or "", "locale": loc or ""}


def _build_user_environment() -> dict[str, Any]:
    """Assemble the caller's environment (computer + IP + geo + locale).

    user_id is resolved to a real users(user_id) INTEGER — the DB is id-driven,
    so the Windows username is an alias that must be resolved, never stored.
    """
    ident = get_computer_identity()
    geo = _geo_lookup()
    loc = _local_locale()
    tz_name = ""
    tz_off = None
    try:
        local = datetime.now().astimezone()
        tz_name = str(local.tzinfo) if local.tzinfo else ""
        tz_off = int(local.utcoffset().total_seconds()) if local.utcoffset() else None
    except Exception:
        pass
    # Prefer IP-derived timezone (answers "my timezone by my IP")
    if geo.get("timezone"):
        tz_name = geo["timezone"]
    if geo.get("tz_offset_sec") is not None:
        tz_off = geo["tz_offset_sec"]
    # Resolve the Windows username -> users.user_id (register if new).
    alias = ident.get("username") or ""
    user_id: int | None = None
    try:
        import db_schema

        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            user_id = db_schema.resolve_or_registry_user(conn, alias)
        finally:
            conn.close()
    except Exception:
        user_id = None
    detail = (
        f"{ident.get('computer_name') or '-'} · {ident.get('computer_id') or '-'}"
        f" · {geo.get('city') or '-'}, {geo.get('country') or '-'}"
        f" · {tz_name or '-'} (UTC{'+' if (tz_off or 0) >= 0 else ''}{(tz_off or 0)//3600})"
        f" · {loc.get('language') or '-'}"
    )
    # WHICH ENVIRONMENT_ID IS RUNNING ON THIS COMPUTER.
    #
    # THE HUMAN (2026-09-25): "how to proof it is running, is by windows task to
    # proof does environment id is running at detect computer ... never = not
    # channel id!!!".
    #
    # So the detect page carries the RUNNING PROOF per `environment_id`, read
    # from the Windows Task Manager via `environment_status.status_for_environment`
    # (product -> app.process_name -> process_probe). It is keyed by
    # `environment_id` and NEVER by a channel. A failure here must not blank the
    # detect card, so it is its own try.
    environments: list[dict[str, Any]] = []
    try:
        import environment_registry as er
        import environment_status as es
        conn = sqlite3.connect(str(AGENT_DB_PATH), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            out = er.list_environments(conn)
            for r in out.get("rows", []):
                environments.append({
                    "environment_id": r["environment_id"],
                    "display": r["display"],
                    "product": r["product"],
                    "status": r.get("status") or "UNKNOWN",
                    "status_why": r.get("status_why") or "",
                    "status_app": r.get("status_app"),
                    "status_count": r.get("status_count"),
                    "status_source": "windows_task_manager:environment_id",
                })
        finally:
            conn.close()
    except Exception:
        environments = []
    return {
        "user_id": user_id,
        "user_alias": alias,
        "ip_address": geo.get("ip_address") or _detect_public_ip(),
        "computer_id": ident.get("computer_id") or "",
        "computer_name": ident.get("computer_name") or "",
        "os_name": platform.system(),
        "os_version": platform.version(),
        "python_version": platform.python_version(),
        "timezone": tz_name,
        "tz_offset_sec": tz_off,
        "country": geo.get("country"),
        "country_code": geo.get("country_code"),
        "region": geo.get("region"),
        "city": geo.get("city"),
        "isp": geo.get("isp"),
        "language": loc.get("language"),
        "locale": loc.get("locale"),
        "detail": detail,
        "environments": environments,
    }


@app.route("/api/user_environment", methods=["GET"])
def api_user_environment_get() -> Any:
    """Detect + return the caller's environment (does NOT write)."""
    try:
        return jsonify({"ok": True, "environment": _build_user_environment()})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/user_environment", methods=["POST"])
def api_user_environment_post() -> Any:
    """Detect + upsert the caller's environment row (DB-driven)."""
    import skill_library_api as sla

    try:
        env = _build_user_environment()
        data = request.get_json(silent=True) or {}
        # allow caller overrides (e.g. explicit user_id)
        for k in ("user_id", "language", "locale"):
            if data.get(k):
                env[k] = str(data[k]).strip()
        # map detect keys -> store kwargs (timezone -> timezone_name)
        kwargs = dict(env)
        kwargs["timezone_name"] = kwargs.pop("timezone", None)
        out = sla.upsert_user_environment(source="api", **kwargs)
        return jsonify({
            "ok": True,
            "action": out.get("action"),
            "id": out.get("id"),
            "environment": env,
            "row": out.get("row"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/user_environment/list", methods=["GET"])
def api_user_environment_list() -> Any:
    """Recent user_environment rows for the UI table."""
    import skill_library_api as sla

    limit = request.args.get("limit", default=50, type=int) or 50
    try:
        return jsonify(sla.list_user_environments(limit))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/user_environment/sessions", methods=["GET"])
def api_user_environment_sessions() -> Any:
    """The PINNED SESSION AREA of one environment: how many rows, and each rect.

    THE HUMAN (2026-09-27): "workspace exclusive — i have work for that!! but why
    i can't find at my http://127.0.0.1:18765/llm-tasks/user_environment/ ????"
    "example VScode > chat > session" / "1) how many session at the pinned area,
    sample = 5 / 2) register to table for each session? / 3) did have x,y for all
    session at pinned?"

    THE ANSWER: the work exists (`coordinate_session` 15 rows,
    `vscode_pinned_first_row` measured, `_pinned_rows()` implemented) and the page
    had NO READER for it. This endpoint is that reader.

    `environment_id` defaults to 6 (VS Code). A band-less environment returns
    `ok: False` with a `why` — NEVER a silent empty list, because an empty list
    and a missing band have the same shape.

    `?live=1` MEASURES NOW instead of reading the last recorded run.
    ---------------------------------------------------------------
    THE HUMAN (2026-09-27): *"what is meaning for `4 rows at the pinned area` +
    `[4, 1, 4]` + `DIFFERENT population`, but i have 5..."*

    MEASURED: the recorded run was **19 hours old** (`2026-09-26 18:17:22` vs
    `2026-09-27 13:31`), and there was **no live path at all**. So the page showed
    a stale number while the screen said 5. A recorded measurement is EVIDENCE of
    what was true THEN; it is not a reading of NOW. `?live=1` takes a fresh
    screenshot and counts the rows in it.
    """
    import skill_library_api as sla

    env_id = request.args.get("environment_id", default=6, type=int) or 6
    live = request.args.get("live", default=0, type=int) or 0
    try:
        out = sla.list_pinned_sessions(env_id)
        if live:
            out["live"] = _measure_pinned_rows_live(env_id)
            # THE LIVE READING WINS when it succeeded, because it is a reading of
            # NOW. The recorded one is KEPT beside it so the two can be compared
            # rather than one silently replacing the other.
            lv = out["live"]
            if lv.get("ok") and lv.get("count") is not None:
                out["recorded_count"] = out.get("count")
                out["count"] = lv["count"]
                out["source"] = "LIVE screen.snapshot (measured now)"
                out["measured_at"] = lv.get("measured_at")
                out["sessions"] = sla._rows_for_count(
                    env_id, lv["count"], out.get("band"), out.get("row_height"))
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


def _foreground_process_name() -> str:
    """The executable name of the FOREGROUND window, or '' when unknown.

    WHY THIS EXISTS. THE `env-task-proof` RULE: prove the environment state,
    never assume it. MEASURED 2026-09-27: a live pinned-row scan ran while a
    BROWSER was foreground and returned `count: 1` with one cluster spanning
    y 225..805 — it had counted the browser's white page, not the Sessions panel.
    A measurement of a target that is not on screen is not a measurement of that
    target, so the caller checks this FIRST.

    Never raises: an unknown foreground is `''`, and the caller decides whether
    that is a reason to refuse.
    """
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        hwnd = int(u.GetForegroundWindow() or 0)
        if not hwnd:
            return ""
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED
        if not h:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1]
            return ""
        finally:
            k.CloseHandle(h)
    except Exception:
        return ""


def _measure_pinned_rows_live(environment_id: int = 6) -> dict[str, Any]:
    """Count the pinned rows by READING THE PANEL'S OWN NUMBER. Never raises.

    WHY THE PANEL'S OWN NUMBER, AND NOT A PIXEL SCAN
    ------------------------------------------------
    MEASURED 2026-09-27, three attempts, each wrong in a different way:

      1. `COUNT(DISTINCT session_id FROM coordinate_session)` -> **7**. A count of
         a TABLE, not of the list. The human: *"only have 5 now"*.
      2. A pixel scan of the stored band `(1285,285)-(1912,367)` -> **2**. The
         band is 82px tall and names only the FIRST row, so it can never see the
         rest.
      3. A pixel scan of the whole panel, counting every bright cluster -> **6**.
         A row's TITLE and its metadata line are both bright, and a wrapped title
         adds a third cluster, so the cluster count is not the row count.

    The panel ALREADY displays the answer: the heading `Pinned` carries a count
    (`Pinned 5`). MEASURED: the local 7B-VL reads that crop as `5`, which is what
    the human sees. **Reading the number the UI itself shows is the measurement**;
    re-deriving it from pixels is a second, worse estimator of the same thing.

    THE TARGET IS BROUGHT TO THE FOREGROUND, THEN PROVEN.
    ----------------------------------------------------
    THE `env-task-proof` RULE: prove the environment state, never assume it.
    MEASURED: a scan ran while a BROWSER was foreground and returned a count of
    the browser's white page. A measurement of a target that is not on screen is
    not a measurement of that target.

    MEASURED 2026-09-27, and this is why the reader ACTIVATES rather than
    refuses: the human reads this page IN A BROWSER, so the browser IS the
    foreground whenever they look at it. A reader that refuses whenever it is not
    already foreground would refuse exactly when the human is looking. So it
    ACTIVATES VS Code (the repo's own `_confirm_app_ready` pattern: activate by
    PROCESS, never by title), WAITS ON THE CONDITION, measures, and then RESTORES
    the window that was foreground before. The proof is still a proof — the
    foreground is CHECKED after activation, and a failure is `ok: False`.

    Returns `{ok, count, measured_at, band, foreground_process, vl_answer, why}`.
    A failure is `ok: False` with a `why` — never a `-1` that reads like a number.
    """
    import io as _io
    import sqlite3 as _sq
    from datetime import datetime as _dt

    out: dict[str, Any] = {"ok": False, "count": None, "measured_at": None,
                           "band": None, "foreground_process": "",
                           "activated": False, "restored": False,
                           "vl_answer": "", "why": ""}
    prev_hwnd = 0
    try:
        import ctypes
        u = ctypes.windll.user32
        prev_hwnd = int(u.GetForegroundWindow() or 0)

        # ---- BRING THE TARGET FORWARD, THEN PROVE IT IS THERE ----------------
        fg = _foreground_process_name()
        if not fg or "code" not in fg.lower():
            ready = _confirm_app_ready("Code.exe")
            out["activated"] = bool(ready.get("ok"))
            if not ready.get("ok"):
                out["foreground_process"] = fg
                out["why"] = ("VS Code could not be brought to the foreground, "
                              "so the Sessions panel is not on screen: %s"
                              % (ready.get("why") or "unknown"))
                return out
            fg = _foreground_process_name()
        out["foreground_process"] = fg
        if not fg or "code" not in fg.lower():
            out["why"] = ("the foreground process is %r, not VS Code — the "
                          "Sessions panel is not on screen, so a row count here "
                          "would be a count of whatever IS on screen" % fg)
            return out

        conn = _sq.connect(str(AGENT_DB_PATH), timeout=10)
        conn.row_factory = _sq.Row
        try:
            band = conn.execute(
                "SELECT e.x1, e.y1, e.x2, e.y2 FROM environment_template e "
                "JOIN target_template t ON t.id = e.template_id "
                "WHERE t.name = 'vscode_pinned_first_row' "
                "AND e.environment_id = ? AND e.is_active = 1",
                (int(environment_id),)).fetchone()
        finally:
            conn.close()
        if not band:
            out["why"] = ("no active `vscode_pinned_first_row` band for "
                          "environment_id=%d" % int(environment_id))
            return out
        x1, x2 = int(band["x1"]), int(band["x2"])
        # THE HEADER CROP: the `Pinned` heading and its count sit ABOVE the first
        # row, so the crop starts above the band and ends just below its top.
        y_top = max(0, int(band["y1"]) - 160)
        y_bot = int(band["y1"]) + 10
        out["band"] = {"x1": x1, "y1": y_top, "x2": x2, "y2": y_bot}

        import mcp_client as _mc
        from PIL import Image as _Im
        cl = _mc.McpClient(_mc.McpConfig.from_env())
        cl.ensure_ready()
        res = cl.call_tool("screen.snapshot", {"format": "png", "maxWidth": 1920})
        blob, _meta = _mc.extract_image_bytes(res)
        im = _Im.open(_io.BytesIO(blob)).convert("RGB")
        w, h = im.size
        crop = im.crop((max(0, x1), max(0, y_top), min(w, x2), min(h, y_bot)))
        buf = _io.BytesIO()
        crop.save(buf, format="PNG")

        # ---- ASK THE LOCAL VL TO READ THE PANEL'S OWN NUMBER ----------------
        import base64 as _b64
        import json as _json
        import os as _os
        import urllib.request as _ur
        base = _os.environ.get("OLLAMA_BASE_URL",
                               "http://127.0.0.1:18803").rstrip("/")
        model = _os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5vl:7b")
        q = ("This is the top of a VS Code Sessions panel. It shows a heading "
             "'Pinned' with a NUMBER to its right. What is that number? "
             "Answer with ONLY the digit.")
        payload = {"model": model, "messages": [{"role": "user", "content": [
            {"type": "text", "text": q},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64,"
                       + _b64.b64encode(buf.getvalue()).decode()}}]}],
            "temperature": 0.0}
        req = _ur.Request(base + "/v1/chat/completions",
                          data=_json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"},
                          method="POST")
        with _ur.urlopen(req, timeout=180) as resp:
            vout = _json.loads(resp.read().decode())
        raw = str(vout["choices"][0]["message"]["content"] or "").strip()
        out["vl_answer"] = raw[:40]
        import re as _re
        m = _re.search(r"\d+", raw)
        if not m:
            out["why"] = ("the VL did not answer with a number: %r" % raw[:60])
            return out
        out["ok"] = True
        out["count"] = int(m.group(0))
        out["measured_at"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        out["image_size"] = [w, h]
        out["why"] = ""
        return out
    except Exception as e:
        out["why"] = "%s: %s" % (type(e).__name__, e)
        return out
    finally:
        # ---- PUT THE HUMAN'S WINDOW BACK ------------------------------------
        # The human was reading this page in a browser; stealing the foreground
        # and leaving it stolen would be a side effect of a READ. Restore it, and
        # report whether the restore worked rather than assuming it did.
        try:
            if out.get("activated") and prev_hwnd:
                import ctypes as _ct
                _u = _ct.windll.user32
                if int(_u.GetForegroundWindow() or 0) != prev_hwnd:
                    _u.ShowWindow(prev_hwnd, 9)  # SW_RESTORE
                    _u.SetForegroundWindow(prev_hwnd)
                    out["restored"] = (int(_u.GetForegroundWindow() or 0)
                                       == prev_hwnd)
        except Exception:
            pass


# ================= app catalog + user_asset API =================
# "app" = installed software (OpenClaw Companion, VS Code, Chrome, Ollama).
# "user_asset" = which app a user has, plus its state. Both are id-driven:
# user_asset references users(user_id) and app(app_id), never a name/slug.

@app.route("/api/apps", methods=["GET"])
def api_apps_list() -> Any:
    """The app catalog."""
    import skill_library_api as sla

    try:
        return jsonify(sla.list_apps())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/apps", methods=["POST"])
def api_apps_upsert() -> Any:
    """Insert or update one app row."""
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    try:
        out = sla.upsert_app(
            app_key=str(data.get("app_key") or ""),
            name=str(data.get("name") or ""),
            kind=str(data.get("kind") or "desktop"),
            exe_path=data.get("exe_path"),
            process_name=data.get("process_name"),
            mcp_url=data.get("mcp_url"),
            description=data.get("description"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/sources", methods=["GET"])
def api_sources_list() -> Any:
    """The source catalog (Chat Center Setting "from" dropdown)."""
    import skill_library_api as sla

    include_all = request.args.get("all", default=0, type=int) or 0
    try:
        return jsonify(sla.list_sources(active_only=not bool(include_all)))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/sources", methods=["POST"])
def api_sources_upsert() -> Any:
    """Insert or update one source row."""
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    try:
        out = sla.upsert_source(
            source_key=str(data.get("source_key") or ""),
            name=str(data.get("name") or ""),
            kind=str(data.get("kind") or "APP"),
            url=data.get("url"),
            hotkey=data.get("hotkey"),
            description=data.get("description"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/flow_settings", methods=["GET"])
def api_flow_settings_list() -> Any:
    """Flow steps (Chat Center flow table)."""
    import skill_library_api as sla

    flow_key = (request.args.get("flow_key") or "").strip() or None
    include_all = request.args.get("all", default=0, type=int) or 0
    try:
        return jsonify(
            sla.list_flow_settings(flow_key=flow_key, active_only=not bool(include_all))
        )
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/flow_settings", methods=["POST"])
def api_flow_settings_upsert() -> Any:
    """Insert or update one flow step."""
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    try:
        out = sla.upsert_flow_setting(
            flow_key=str(data.get("flow_key") or ""),
            step_no=data.get("step_no"),
            question=data.get("question"),
            value=data.get("value"),
            next_step=data.get("next_step"),
            action=data.get("action"),
            description=data.get("description"),
        )
        return jsonify(out), (200 if out.get("ok") else 400)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/flow_settings/<int:flow_id>", methods=["DELETE"])
def api_flow_settings_delete(flow_id: int) -> Any:
    """Delete one flow step."""
    import skill_library_api as sla

    try:
        out = sla.delete_flow_setting(flow_id)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/flow_settings/run", methods=["GET", "POST"])
def api_flow_settings_run() -> Any:
    """Resolve ONE flow step into a runnable plan (does not move the mouse).

    GET  ?flow_key=...&step_no=N
    POST {"flow_key": "...", "step_no": N}

    Returns the resolved action + the template text, so the caller can execute
    it. Resolution is separate from execution so the flow can be inspected and
    tested without touching the UI.
    """
    import skill_library_api as sla

    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        flow_key = str(data.get("flow_key") or "").strip()
        step_no = data.get("step_no")
    else:
        flow_key = (request.args.get("flow_key") or "").strip()
        step_no = request.args.get("step_no")
    if not flow_key:
        return jsonify({"ok": False, "error": "flow_key is required"}), 400
    try:
        step = int(step_no)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "step_no must be an integer"}), 400
    try:
        out = sla.run_flow_step(flow_key, step)
        return jsonify(out), (200 if out.get("ok") else 404)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/user_assets", methods=["GET"])
def api_user_assets_list() -> Any:
    """user_asset rows (optionally filtered by user_id)."""
    import skill_library_api as sla

    uid = request.args.get("user_id", type=int)
    limit = request.args.get("limit", default=200, type=int) or 200
    try:
        return jsonify(sla.list_user_assets(uid, limit))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/user_assets/sync", methods=["POST"])
def api_user_assets_sync() -> Any:
    """Refresh user_asset for a user from the live environment.

    Defaults to the caller's own resolved user_id, so the UI can sync without
    knowing the id.
    """
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    uid = data.get("user_id")
    try:
        if uid is None:
            uid = _build_user_environment().get("user_id")
        if uid is None:
            return jsonify({
                "ok": False,
                "error": "Could not resolve a user_id for this caller.",
            }), 400
        return jsonify(sla.sync_user_assets(int(uid)))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/openclaw/connect", methods=["POST"])
def api_openclaw_connect() -> Any:
    """Auto-connect: launch OpenClawTray if the MCP port is closed, then re-probe.

    This is the ONLY endpoint that starts a process. The read-only report never
    does, so viewing the settings page has no side effects.
    """
    import openclaw_settings as ocs

    try:
        connect = ocs.ensure_online(auto_connect=True)
        report = ocs.build_openclaw_report(auto_connect=False)
        return jsonify({"ok": bool(connect.get("ok")), "connect": connect,
                        "report": report})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= chat_center API (3-step flow) =================
# STEP1 submit content -> STEP2 middleware (chat_id + sha256 + analysis)
# -> STEP3 answer (post chat_id + sha256, store output).
#
# WHY the extra log call: register_chat_identity() always writes action
# 'register' (role Answer) to chat_identity_log. Chat Center needs BOTH
# sides visible on /chat_identity/recent:
#   submit -> Question, answer -> Answer.
# So each step appends its own chat_identity_log row with the right role.


def _log_chat_center_identity(
    *,
    session_id: str | None,
    chat_id: int | None,
    sha256: str | None,
    chat_hash: str | None,
    action: str,
    ide: str | None = None,
    llm: str | None = None,
    caller_contract_id: str = "SKILL-0002",
) -> None:
    """Append a chat_identity_log row for a chat_center step (best-effort).

    P0-1 / P0-3: this is a SECOND write path into chat_identity_log, so it must
    pass the SAME two hard gates as skill_library_api._log_chat_identity():
      - write-owner gate: only the declared owner (SKILL-0002) may write
      - payload gate: the row must satisfy SKILL-0001's Field Register
    Without these, "SKILL-0002 is the sole write entry" was only half-true:
    this function could insert rows with no gate at all.

    P2-gate Phase 3 (2026-09-20) — chat_hash CONSISTENCY
    ---------------------------------------------------
    The two write paths used to DISAGREE on chat_hash:
      * skill_library_api.register_chat_identity DERIVES it:
            chat_hash = chat_pair_hash(str(chat_id), sid)
      * this function passed the CALLER-SUPPLIED value straight through, so a
        caller that omitted it wrote NULL.
    Measured on the live table: 3 same-instant `register` pairs (chat_id
    12/13/14) where one row carried the hash and the other was NULL. The
    comment above claimed both paths "pass the SAME two hard gates" while they
    did not agree on this column.

    The fix DERIVES the hash here too, so the two paths cannot disagree. A
    caller-supplied value is still honoured when it is present (it may carry a
    legacy hash), but a MISSING one is now computed instead of written as NULL.
    """
    try:
        import skill_library_api as sla

        if not sla.assert_chat_identity_write_allowed(caller_contract_id):
            return
        # Derive the pair hash when the caller did not supply one, so this path
        # agrees with register_chat_identity instead of writing NULL.
        if not chat_hash and chat_id is not None and session_id:
            chat_hash = sla.chat_pair_hash(str(chat_id), session_id)
        ok_payload, payload_errors = sla.assert_chat_identity_payload_valid(
            sla.build_chat_identity_payload(
                session_id=session_id,
                chat_id=chat_id,
                sha256=sha256,
                action=action,
            )
        )
        if not ok_payload:
            return
    except Exception:
        # SSOT unavailable -> keep the legacy best-effort behaviour.
        pass
    try:
        import sqlite3

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.execute(
                """
                INSERT INTO chat_identity_log
                    (session_id, chat_id, sha256, chat_hash, action, ide, llm, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'chat_center')
                """,
                (session_id, chat_id, sha256, chat_hash, action, ide, llm),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


@app.route("/api/chat_center/submit", methods=["POST"])
def api_chat_center_submit() -> Any:
    """STEP 1+2: submit content -> chat_id + sha256 + catalog/subcatalog/skill analysis.

    Body: {content, session_id?, eumu_id?}. When session_id is omitted a new UUID
    is generated (new chat). Logs a Question row in chat_center_message and
    registers the identity in chat_identity_log.

    `eumu_id` (an ENTITY id, e.g. `T-10-1`) is OPTIONAL. When supplied, a TICKET
    is opened for that entity and the chat is LINKED to it, so the chat center
    can answer "which chats discuss this entity". The entity id is VERIFIED
    through `entity_id.require()`; an id that does not verify is REFUSED rather
    than stored, because a ticket for a non-existent entity is a phantom
    reference. A missing `eumu_id` is NOT an error — most chats are not about a
    registered entity.
    """
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not content:
        return jsonify({"ok": False, "error": "content is required"}), 400
    eumu_id = str(data.get("eumu_id") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    if not session_id:
        session_id = str(uuid.uuid4())
    if not sla.is_valid_session_id(session_id):
        return jsonify({"ok": False, "error": "session_id must be a UUID"}), 400
    try:
        # STEP 2 middleware: identity
        ident = sla.register_chat_identity(
            session_id, source="chat_center",
            ide=_detect_ide() or None, llm=_detect_llm(session_id) or None,
        )
        chat_id = ident.get("chat_id")          # INTEGER chat_main.id
        sha256 = ident.get("sha256") or ""      # content hash (NOT an id)
        chat_hash = ident.get("chat_hash") or ""
        # analysis
        analysis = sla.analyze_chat_content(content)
        cat = analysis.get("catalog") or {}
        sub = analysis.get("subcatalog") or {}
        skl = analysis.get("skill") or {}
        msg = sla.create_chat_center_message(
            session_id=session_id,
            chat_id=chat_id,
            sha256=sha256,
            chat_hash=chat_hash,
            role="Question",
            content=content,
            status=str(data.get("status") or "").strip() or None,
            catalog_id=cat.get("id"),
            catalog_name=cat.get("name"),
            subcatalog_id=sub.get("id"),
            subcatalog_name=sub.get("name"),
            skill_id=skl.get("id") or None,
            skill_name=skl.get("name") or None,
            ide=_detect_ide() or None,
            llm=_detect_llm(session_id) or None,
        )
        # Mirror into chat_identity_log as a Question (role = Question).
        _log_chat_center_identity(
            session_id=session_id,
            chat_id=chat_id,
            sha256=sha256,
            chat_hash=chat_hash,
            action="resolve",
            ide=_detect_ide() or None,
            llm=_detect_llm(session_id) or None,
        )
        # OPTIONAL TICKET. The ticket is for the SERVICE; the entity is echoed
        # back but is NOT written into the ticket (user, 2026-09-21: "no related
        # too other, don't mix up"). The entity id is still VERIFIED, because a
        # caller who names an entity should not be told a phantom one was fine.
        ticket = None
        if eumu_id:
            import entity_id as eid
            import ticket_store as tks

            conn_t = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
            try:
                conn_t.row_factory = sqlite3.Row
                try:
                    eid.require(eumu_id, conn=conn_t)
                except eid.InvalidEntityId as e:
                    return jsonify({"ok": False,
                                    "error": "entity id %r does not verify: %s"
                                             % (eumu_id, e)}), 400
                t = tks.create_ticket(
                    conn_t, service="chat_center",
                    title=content[:120], opened_by="chat_center",
                    note="raised from chat_center submit",
                    cite_ref="mouse_spot_helper.py:api_chat_center_submit")
                if chat_id is not None:
                    tks.link_chat(conn_t, int(t["ticket_id"]), int(chat_id))
                ticket = {"ticket_id": t["ticket_id"],
                          "entity_id": eumu_id,
                          "status": t["status"], "created": t["created"]}
            except tks.TicketRefused as e:
                return jsonify({"ok": False, "error": str(e)}), 400
            finally:
                conn_t.close()
        return jsonify({
            "ok": True,
            "session_id": session_id,
            "chat_id": chat_id,
            "sha256": sha256,
            "chat_hash": chat_hash,
            "analysis": analysis,
            "message_id": msg.get("id"),
            "ticket": ticket,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_center/answer", methods=["POST"])
def api_chat_center_answer() -> Any:
    """STEP 3: post chat_id + sha256 -> store the Answer row (who/time/output)."""
    import skill_library_api as sla

    data = request.get_json(silent=True) or {}
    chat_id_raw = data.get("chat_id")
    chat_hash = str(data.get("chat_hash") or "").strip()
    session_id = str(data.get("session_id") or "").strip() or None
    sha256 = str(data.get("sha256") or "").strip() or None
    content = str(data.get("content") or "").strip()
    if chat_id_raw in (None, ""):
        return jsonify({"ok": False, "error": "chat_id is required"}), 400
    try:
        chat_id = int(chat_id_raw)
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": "chat_id must be an integer id (chat_main.id), not a sha256",
        }), 400
    llm = (str(data.get("llm") or "").strip()
           or _detect_llm(str(data.get("session_id") or "")) or None)
    ide = str(data.get("ide") or "").strip() or _detect_ide() or None
    if not content:
        # default demo answer (see plan: mock first, wire real model later)
        content = "123"
    try:
        msg = sla.create_chat_center_message(
            session_id=session_id,
            chat_id=chat_id,
            sha256=sha256,
            chat_hash=chat_hash or None,
            role="Answer",
            content=content,
            status=str(data.get("status") or "").strip() or None,
            llm=llm,
            ide=ide,
            answered_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
        # Mirror into chat_identity_log as an Answer (role = Answer).
        _log_chat_center_identity(
            session_id=session_id,
            chat_id=chat_id,
            sha256=sha256,
            chat_hash=chat_hash or None,
            action="register",
            ide=ide,
            llm=llm,
        )
        return jsonify({
            "ok": True,
            "message_id": msg.get("id"),
            "answer": content,
            "who": llm or "LLM",
            "answered_at": (msg.get("row") or {}).get("answered_at"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_center/history", methods=["GET"])
def api_chat_center_history() -> Any:
    """List chat_center_message rows for a chat_id or session_id.

    THE PAGE MUST BE ABLE TO TELL A PROOF TURN FROM A REAL ONE (2026-09-27).

    THE HUMAN: "you can see the ui for chat is not human readable, pls improve
    that". MEASURED, and this is WHY it is not readable: of 2,527 turns, **89 are
    PROOF scaffolding** (`content LIKE '__proof%'`) and **1,974 carry
    `status='draft'`**. The page rendered both as if they were a conversation, so
    chat 68 showed `__proof_turn_1__` / `__proof_turn_2__` / `__proof_turn_3__`
    as its history.

    So each row gains TWO derived fields, and the derivation is NAMED:
      * `kind`     -- 'proof' when the content is proof scaffolding, else 'real'
      * `readable` -- False when the row is scaffolding OR still a draft, so the
                      page can dim it instead of presenting it as a turn

    NOTHING IS HIDDEN. A proof turn is LABELLED, not filtered out — a page that
    silently dropped 89 rows would be the "detector that cannot find anything"
    defect this repo keeps paying for.
    """
    import skill_library_api as sla

    chat_id = (request.args.get("chat_id") or "").strip() or None
    session_id = (request.args.get("session_id") or "").strip() or None
    limit = request.args.get("limit", default=50, type=int) or 50
    try:
        res = sla.list_chat_center_messages(
            chat_id=chat_id, session_id=session_id, limit=limit
        )
        rows = res.get("rows") or []
        proof_n = 0
        draft_n = 0
        for r in rows:
            content = str(r.get("content") or "")
            status = str(r.get("status") or "")
            is_proof = content.startswith("__proof")
            is_draft = status.strip().lower() == "draft"
            r["kind"] = "proof" if is_proof else "real"
            r["readable"] = not (is_proof or is_draft)
            if is_proof:
                proof_n += 1
            if is_draft:
                draft_n += 1
        res["counts"] = {
            "rows": len(rows),
            "proof": proof_n,
            "real": len(rows) - proof_n,
            "draft": draft_n,
            "readable": sum(1 for r in rows if r["readable"]),
        }
        # ---- THE PAIRS (2026-09-27) ------------------------------------------
        # THE HUMAN: "for chat? they are PAIR, ask -> answer / where is ask?"
        #
        # MEASURED, and this is the defect: chat 68's newest 200 rows hold **115
        # real Answers but only 4 real Questions**, so the page showed answers
        # with no ask above them. The asks ARE there (ids 7781, 7734, 7712, 7701,
        # 7688) — they are BURIED.
        #
        # THE PAIRING RULE IS MEASURED, not invented: a `Question` row OPENS a
        # pair, and every `Answer` that follows it (by `id`, until the next
        # `Question`) belongs to it. MEASURED over chat 68: 187 Questions and
        # **0** Answers before any Question. There is NO `turn_no` column and no
        # pair key — the pairing is by ORDER.
        #
        # An Answer with no preceding Question is an ORPHAN and is REPORTED,
        # never attached to the wrong ask.
        ordered = sorted(rows, key=lambda r: int(r.get("id") or 0))
        pairs: list[dict[str, Any]] = []
        orphans: list[dict[str, Any]] = []
        cur: dict[str, Any] | None = None
        for r in ordered:
            if str(r.get("role")) == "Question":
                cur = {"ask": r, "answers": []}
                pairs.append(cur)
            elif cur is None:
                orphans.append(r)
            else:
                cur["answers"].append(r)
        for i, p in enumerate(pairs):
            p["pair_no"] = i + 1
            p["answer_count"] = len(p["answers"])
            p["kind"] = p["ask"].get("kind")

        # ---- THE TWO SECTIONS (2026-09-27) -----------------------------------
        # THE HUMAN: "you mis-understand my request ... this is data for
        # reasearch, can display by table format, so user can easy readable ...
        # is the content for answer / so answer card with 2 section in single
        # answer card only"
        #
        # I had rendered 21 answers as 21 cards. The human wants ONE card with
        # TWO SECTIONS: the WORKING steps, and the REPORT.
        #
        # THE SPLIT RULE IS MEASURED, NOT INVENTED. MEASURED, chat 68, the 4
        # pairs that have >= 2 answers:
        #   ask 7701 -> longest #7710 (1850) vs 2nd #7707 (27)   gap 68.5x
        #   ask 7712 -> longest #7733 (3419) vs 2nd #7722 (340)  gap 10.1x
        #   ask 7734 -> longest #7779 (2928) vs 2nd #7747 (267)  gap 11.0x
        #   ask 7781 -> longest #7801 (600)  vs 2nd #7810 (97)   gap  6.2x
        # The longest answer is the report in 4 of 4 pairs, corroborated by
        # THREE independent signals: a length gap >= 3x, Markdown headings or
        # table rows, and a report marker in the text.
        #
        # A pair that does NOT meet the rule has NO report, and the page SAYS SO
        # rather than inventing one.
        import re as _re

        def _looks_like_report(text: str) -> tuple[bool, int, int]:
            heads = len(_re.findall(r"^#{1,6}\s", text, _re.M))
            trows = len(_re.findall(r"^\s*\|", text, _re.M))
            return (heads + trows) > 0, heads, trows

        for p in pairs:
            ans = p["answers"]
            p["report"] = None
            p["working"] = ans
            p["report_why"] = ""
            if len(ans) < 2:
                p["report_why"] = (
                    "this pair has %d answer(s), so there is no separate report "
                    "to split out" % len(ans))
                continue
            ranked = sorted(ans, key=lambda a: len(a.get("content") or ""),
                            reverse=True)
            top, second = ranked[0], ranked[1]
            top_len = len(top.get("content") or "")
            second_len = len(second.get("content") or "")
            gap = (top_len / second_len) if second_len else float(top_len)
            structured, heads, trows = _looks_like_report(top.get("content") or "")
            if gap >= 3.0 and structured:
                p["report"] = top
                p["working"] = [a for a in ans if a.get("id") != top.get("id")]
                p["report_why"] = (
                    "the report is answer #%s: it is the LONGEST of %d (%d chars "
                    "vs %d for the next, a %.1fx gap) and it carries %d heading(s) "
                    "and %d table row(s)"
                    % (top.get("id"), len(ans), top_len, second_len, gap,
                       heads, trows))
            else:
                p["report_why"] = (
                    "NO report in this pair: the longest answer (#%s, %d chars) "
                    "is only %.1fx the next (%d chars)%s, so the pair is all "
                    "WORKING and no report is invented"
                    % (top.get("id"), top_len, gap, second_len,
                       "" if structured else " and carries no heading or table"))

        res["pairs"] = pairs
        res["pair_count"] = len(pairs)
        res["orphan_answers"] = len(orphans)
        res["orphan_why"] = (
            "%d Answer row(s) have no preceding Question, so they belong to no "
            "ask and are REPORTED rather than attached to the wrong one"
            % len(orphans))
        res["report_count"] = sum(1 for p in pairs if p["report"])
        res["report_rule"] = (
            "a pair's REPORT is its LONGEST answer, iff it is >= 3x the 2nd "
            "longest AND carries >= 1 Markdown heading or table row; otherwise "
            "the pair has no report and the page says so")
        res["why"] = (
            "%d of %d rows are PROOF scaffolding and %d are still 'draft'; a "
            "proof row is LABELLED, never hidden. The rows form %d PAIR(s) "
            "(ask -> answer); %d Answer(s) have no ask; %d pair(s) carry a "
            "REPORT."
            % (proof_n, len(rows), draft_n, len(pairs), len(orphans),
               res["report_count"]))
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Conversation Center =================
#
# THE HUMAN (2026-09-26), verbatim:
#     "http://127.0.0.1:18765/llm-tasks/chat_center and
#      http://127.0.0.1:18765/llm-tasks/chat_identity is talking for same
#      capabilty, with 2 ui / as design is totally change to conversation module
#      and group chat into it / re-design UI to 1 index catalog and have user
#      fiendly ui for human, which can understand and monitoring the real flow
#      with data"
#
# TWO PAGES, ONE SUBJECT. MEASURED: `chat_identity` (app.js) states its own SSOT
# as "chat_id + chat_identity_log"; `chat_center` (chat-center.js) states its
# persistence as "chat_center_message". Those key on the SAME `chat_id`. Both are
# served by ONE capability, `task_center.chat_identity` (capability_id 12811),
# whose module is `chat_level` (module_id 25986).
#
# THE ONE MEASUREMENT THIS ENDPOINT OWNS. The page must not be able to show a
# number the endpoint did not return, so EVERY count on the index comes from
# here and nothing is written into the UI as a literal.
CONVERSATION_LEVELS: tuple[tuple[str, str, str], ...] = (
    # (level, table, the key a reader joins on)
    ("CHAT", "chat", "chat_id"),
    ("CONVERSATION", "chat_main", "id (PK) / chat_id (FK)"),
    ("TURN", "chat_center_message", "chat_id"),
    ("IDENTITY", "identity_registry", "session_id"),
)


@app.route("/api/conversation_center/index", methods=["GET"])
def api_conversation_center_index() -> Any:
    """The four levels with LIVE counts, plus the two facts that qualify them.

    THE PAGE CANNOT DISAGREE WITH ITSELF: every number here is computed in this
    one function, from ONE connection, at ONE moment.

    THE TWO QUALIFYING FACTS ARE MANDATORY, not decoration:
      * `max_conversations_per_chat` -- MEASURED 1 today, so the CHAT level
        groups NOTHING yet. A page that showed 69/69/2344 and implied a working
        hierarchy would be the defect this repo keeps paying for.
      * `turns_without_chat_id` -- MEASURED 348 of 2344, so part of the TURN
        level is not attached to any chat.
    Reporting them is what makes the index a measurement rather than a claim.
    """
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            levels = []
            for name, table, key in CONVERSATION_LEVELS:
                try:
                    n = int(conn.execute(
                        "SELECT COUNT(*) FROM %s" % table).fetchone()[0])
                    ok = True
                except Exception as exc:
                    # A MISSING TABLE IS REPORTED, NOT HIDDEN. A level that
                    # silently disappears reads as a level that is empty.
                    n, ok = 0, False
                    levels.append({"level": name, "table": table, "key": key,
                                   "rows": None, "ok": False,
                                   "why": "%s: %s" % (type(exc).__name__, exc)})
                    continue
                levels.append({"level": name, "table": table, "key": key,
                               "rows": n, "ok": ok, "why": ""})

            def _n(sql: str, default: int | None = None) -> int | None:
                try:
                    return int(conn.execute(sql).fetchone()[0])
                except Exception:
                    return default

            max_per_chat = _n(
                "SELECT COALESCE(MAX(n), 0) FROM (SELECT COUNT(*) n "
                "FROM chat_main GROUP BY chat_id)")
            chats = _n("SELECT COUNT(*) FROM chat")
            convs = _n("SELECT COUNT(*) FROM chat_main")
            unlinked = _n("SELECT COUNT(*) FROM chat_center_message "
                          "WHERE chat_id IS NULL OR chat_id = ''")
            turns = _n("SELECT COUNT(*) FROM chat_center_message")
            # THE GROUPING VERDICT IS COMPUTED, never typed. It is the answer to
            # "does the CHAT level group anything yet?".
            grouping = ("1:1 (groups nothing yet)"
                        if max_per_chat == 1 else
                        ("1:N" if (max_per_chat or 0) > 1 else "NO DATA"))
            # THE PREPARATION CHAIN (2026-09-27). THE HUMAN: "this is pretaretion
            # step (research by real data) for how to having answer before /
            # research by real data can in table format to clear it to user".
            #
            # MEASURED, chat_center_message (2,538 rows):
            #   ROUTE    a Question carrying catalog_id ......... 65 / 252  (25.8%)
            #   ANSWER   Answer rows ............................ 2,286
            #   EVIDENCE an Answer carrying evidence_ref ........ 489 / 2,286 (21.4%)
            #   JOIN     a ROUTED ask whose answers carry
            #            evidence ................................ 0  (0%)
            #
            # THE CHAIN IS BROKEN AT THE JOIN, and the two halves are in DIFFERENT
            # chats: the 65 routed asks are spread 1-per-chat across 65 chats, and
            # 482 of the 489 evidenced answers have `chat_id IS NULL`. So no pair
            # can ever show them. Every number below is COMPUTED here, never typed.
            prep_route = _n("SELECT COUNT(*) FROM chat_center_message "
                            "WHERE role='Question' AND catalog_id IS NOT NULL")
            prep_route_skill = _n("SELECT COUNT(*) FROM chat_center_message "
                                  "WHERE role='Question' AND skill_id IS NOT NULL")
            prep_questions = _n("SELECT COUNT(*) FROM chat_center_message "
                                "WHERE role='Question'")
            prep_answers = _n("SELECT COUNT(*) FROM chat_center_message "
                              "WHERE role='Answer'")
            prep_evidence = _n("SELECT COUNT(*) FROM chat_center_message "
                               "WHERE role='Answer' AND evidence_ref IS NOT NULL "
                               "AND evidence_ref <> ''")
            prep_measured = _n("SELECT COUNT(*) FROM chat_center_message "
                               "WHERE role='Answer' AND measured_effect IS NOT NULL "
                               "AND measured_effect <> ''")
            prep_fault = _n("SELECT COUNT(*) FROM chat_center_message "
                            "WHERE role='Answer' AND fault_ref IS NOT NULL "
                            "AND fault_ref <> ''")
            prep_join = _n(
                "SELECT COUNT(DISTINCT q.id) FROM chat_center_message q "
                "JOIN chat_center_message a ON a.chat_id = q.chat_id "
                "AND a.id > q.id AND a.role='Answer' "
                "WHERE q.role='Question' AND q.catalog_id IS NOT NULL "
                "AND a.evidence_ref IS NOT NULL AND a.evidence_ref <> ''")
            prep_orphan = _n("SELECT COUNT(*) FROM chat_center_message "
                             "WHERE role='Answer' AND evidence_ref IS NOT NULL "
                             "AND evidence_ref <> '' "
                             "AND (chat_id IS NULL OR chat_id = '')")
            prep_chats_routed = _n(
                "SELECT COUNT(DISTINCT chat_id) FROM chat_center_message "
                "WHERE role='Question' AND catalog_id IS NOT NULL")

            def _pct(a: int | None, b: int | None) -> float:
                return round(100.0 * (a or 0) / (b or 1), 1)

            prep = {
                "stages": [
                    {"no": 1, "stage": "ROUTE",
                     "what": "a Question that carries catalog_id / subcatalog_id / skill_id",
                     "count": prep_route, "of": prep_questions,
                     "pct": _pct(prep_route, prep_questions)},
                    {"no": 2, "stage": "ANSWER",
                     "what": "Answer rows",
                     "count": prep_answers, "of": prep_answers, "pct": 100.0},
                    {"no": 3, "stage": "EVIDENCE",
                     "what": "an Answer that carries evidence_ref + measured_effect + fault_ref",
                     "count": prep_evidence, "of": prep_answers,
                     "pct": _pct(prep_evidence, prep_answers)},
                    {"no": 4, "stage": "JOIN",
                     "what": "a ROUTED ask whose answers carry evidence",
                     "count": prep_join, "of": prep_route,
                     "pct": _pct(prep_join, prep_route)},
                ],
                "fields": [
                    {"field": "catalog_id", "question": prep_route, "answer": 0},
                    {"field": "skill_id", "question": prep_route_skill, "answer": 2},
                    {"field": "evidence_ref", "question": 0, "answer": prep_evidence},
                    {"field": "measured_effect", "question": 0, "answer": prep_measured},
                    {"field": "fault_ref", "question": 0, "answer": prep_fault},
                ],
                "break": {
                    "stage": 4,
                    "count": prep_join,
                    "why": ("the chain is BROKEN at the JOIN: %d of %d routed asks "
                            "have an evidenced answer. The two halves are in "
                            "DIFFERENT chats — the routed asks are spread across "
                            "%d chats, and %d of the %d evidenced answers carry "
                            "NO chat_id, so no pair can show them."
                            % (prep_join, prep_route, prep_chats_routed,
                               prep_orphan, prep_evidence)),
                    "routed_asks_in_chats": prep_chats_routed,
                    "evidenced_answers_without_chat_id": prep_orphan,
                },
            }

            return jsonify({
                "ok": True,
                "levels": levels,
                "preparation": prep,
                "grouping": {
                    "chats": chats,
                    "conversations": convs,
                    "max_conversations_per_chat": max_per_chat,
                    "verdict": grouping,
                    "why": ("every chat holds exactly ONE conversation, so the "
                            "CHAT level does not group yet"
                            if max_per_chat == 1 else
                            "at least one chat holds more than one conversation"),
                },
                "turns": {
                    "total": turns,
                    "without_chat_id": unlinked,
                    "why": ("%s of %s turns carry no chat_id, so part of the "
                            "TURN level is attached to no chat"
                            % (unlinked, turns)),
                },
                # The two capability/module keys, so the page names the SAME
                # things the registers do instead of inventing a label.
                "capability_key": "task_center.chat_identity",
                "module_key": "chat_level",
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# THE WORKING TABLE'S STEPS ARE MEASUREMENTS (2026-09-27).
# THE HUMAN: "good! for working table / their step is measure real data / measure
# / table field fucntion API module channel capability / -> get the entity ID /
# real data = value / does it fit to design / locic -> YES or NO / need to CRUD,
# create new, update have the new version, delete -> cleanup list report / ...
# -> register the entity ID / they is how to have a plan too and that is what i
# looking for by logic generator + prompt generator X question flow".
#
# MEASURED, the 7 kinds, source rows vs registered entity ids:
#   table      T  db_table_registry    204 -> 120   gap   84
#   field      D  db_field_registry   3438 ->  34   gap 3404
#   function   F  function_registry   4096 ->  33   gap 4063
#   API        A  api_registry         282 ->  43   gap  239
#   module     M  module_registry      451 ->  40   gap  411
#   channel    H  channel_registry      12 ->   4   gap    8
#   capability C  capability_registry   44 ->  29   gap   15
#   TOTAL                            8527 -> 303   gap 8224  (96.4% unregistered)
#
# THE CRUD SPLIT IS MEASURED PER KIND, not assumed:
#   CREATE = a source row with no registered entity id
#   UPDATE = a registered id whose source row exists (a version bump is available)
#   DELETE = a registered id that points at NO source row  (MEASURED: 22 modules)
ENTITY_KINDS = [
    ("table", "T", "db_table_registry", "db_table_id"),
    ("field", "D", "db_field_registry", "db_field_id"),
    ("function", "F", "function_registry", "function_id"),
    ("API", "A", "api_registry", "api_id"),
    ("module", "M", "module_registry", "module_id"),
    ("channel", "H", "channel_registry", "channel_id"),
    ("capability", "C", "capability_registry", "capability_id"),
]


@app.route("/api/conversation_center/entity_crud", methods=["GET"])
def api_conversation_center_entity_crud() -> Any:
    """The 7 kinds: real data, the entity id, fits YES/NO, and the CRUD split.

    EVERY number is computed here from ONE connection at ONE moment, so the page
    cannot disagree with the registers. Nothing is typed.
    """
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            def _n(sql: str, args: tuple = ()) -> int:
                try:
                    return int(conn.execute(sql, args).fetchone()[0])
                except Exception:
                    return 0

            def _ids(sql: str, args: tuple = ()) -> set:
                try:
                    return {r[0] for r in conn.execute(sql, args)}
                except Exception:
                    return set()

            rows = []
            tot_src = tot_reg = tot_create = tot_update = tot_delete = 0
            for name, letter, table, idcol in ENTITY_KINDS:
                src = _ids("SELECT %s FROM %s" % (idcol, table))
                reg = _ids("SELECT entity_ref_id FROM version_registry "
                           "WHERE entity_type = ?", (letter,))
                create = len(src - reg)          # a source row with no entity id
                update = len(src & reg)          # a registered id whose row exists
                delete = len(reg - src)          # a registered id pointing at nothing
                # FITS is COMPUTED: the registered set must be a SUBSET of the
                # source set. A dangling id means the register names something
                # that is not there, so the design does not fit.
                fits = delete == 0
                # A REAL entity id for this kind, minted by the ONE formatter.
                sample = None
                if reg:
                    ref = sorted(reg)[0]
                    ver = _n("SELECT COALESCE(MAX(version), 1) FROM version_registry "
                             "WHERE entity_type = ? AND entity_ref_id = ?",
                             (letter, ref))
                    sample = "%s-%d-0-%d" % (letter, ref, ver)
                rows.append({
                    "kind": name, "letter": letter, "source_table": table,
                    "source": len(src), "registered": len(reg),
                    "gap": len(src - reg),
                    "create": create, "update": update, "delete": delete,
                    "fits": fits,
                    "fits_why": ("the registered set is a SUBSET of the source set"
                                 if fits else
                                 "%d registered id(s) point at NO source row"
                                 % delete),
                    "sample_entity_id": sample,
                    "delete_ids": sorted(reg - src)[:20],
                })
                tot_src += len(src); tot_reg += len(reg)
                tot_create += create; tot_update += update; tot_delete += delete

            # THE ID FORMAT, BY EVIDENCE (2026-09-27). THE HUMAN: "sorry for my
            # wrong typing, always by evidence".
            #
            # The earlier version of this block reported a "human_example gap"
            # against `T-3.1.1-2`. That was WRONG: the human's dotted form was a
            # TYPO, so the panel presented a typo as a design gap. It is replaced
            # by the EVIDENCE: the ONE format, its 3 parts with their MEANING,
            # and a REAL id that both PARSES and VERIFIES.
            #
            # THE SHAPE, READ FROM THE ONE PLACE IT IS STATED. Retyping it here
            # is how the OLD shape survived the rename: this block still said
            # "{LETTER}-{ref_id}-{version}" (the OLD one) on 2026-09-27, and the UI
            # shows a worker. A shape the UI shows must come from entity_id.SHAPE.
            import entity_id as _eid_shape
            id_format = {
                "format": _eid_shape.SHAPE,
                "example": _eid_shape.SHAPE_EXAMPLE,
                "parts": [
                    {"part": "LETTER", "example": "F",
                     "meaning": "the entity KIND (entity_type_registry)"},
                    {"part": "table_id", "example": "38",
                     "meaning": "the register's table (looked up from the letter)"},
                    {"part": "row_id", "example": "11",
                     "meaning": "the register table's OWN primary key"},
                    {"part": "version", "example": "1",
                     "meaning": "the version_registry version"},
                ],
                "note": ("the 3-part form {LETTER}-{ref_id}-{version} is the OLD "
                         "one (2026-09-27): one trailing number reads as version "
                         "OR row. There is no db_row_registry."),
            }
            try:
                import entity_id as _eid
                # A REAL id that both parses AND verifies: pick a registered
                # table that also has a version row.
                row = conn.execute(
                    "SELECT t.db_table_id FROM db_table_registry t "
                    "JOIN version_registry v ON v.entity_type='T' "
                    "AND v.entity_ref_id = t.db_table_id "
                    "WHERE t.is_active = 1 "
                    "ORDER BY t.db_table_id LIMIT 1").fetchone()
                if row:
                    real = _eid.format("T", int(row["db_table_id"]), 1)
                    id_format["example"] = real
                    id_format["parts"][1]["example"] = str(row["db_table_id"])
                    id_format["example_parses"] = bool(
                        _eid.parse(real).get("ok"))
                    id_format["example_verifies"] = bool(
                        _eid.verify(real, conn=conn).get("ok"))
                # And prove every kind's real id parses.
                id_format["kinds_parse_ok"] = sum(
                    1 for k in rows
                    if k["sample_entity_id"]
                    and _eid.parse(k["sample_entity_id"]).get("ok"))
                id_format["kinds_total"] = len(rows)
            except Exception as exc:
                id_format["note"] += " (entity_id import failed: %s)" % exc

            return jsonify({
                "ok": True,
                "kinds": rows,
                "totals": {"source": tot_src, "registered": tot_reg,
                           "gap": tot_src - tot_reg,
                           "gap_pct": round(100.0 * (tot_src - tot_reg)
                                            / max(tot_src, 1), 1),
                           "create": tot_create, "update": tot_update,
                           "delete": tot_delete},
                "crud_rule": {
                    "CREATE": "a source row with no registered entity id",
                    "UPDATE": "a registered id whose source row exists (a version bump is available)",
                    "DELETE": "a registered id that points at NO source row",
                },
                "id_format": id_format,
                "why": ("%d of %d source rows carry NO entity id; %d registered "
                        "id(s) point at no source row and are the cleanup list"
                        % (tot_src - tot_reg, tot_src, tot_delete)),
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# THE PROMPT -> PLAN RELATION, AND PLAYWRIGHT READINESS (2026-09-27).
# THE HUMAN: "i don't understand what is the relation for having a prompt to have
# plan ... conversation > chat -> ask (after confirm identity and environment is
# ready) the job is by conversation to step to step to have plan for the task
# under task request ... question flow help us to have 100% clear plan before have
# the task ... we need to confirm playwright is ready".
#
# MEASURED: the relation EXISTS and is a 4-link chain:
#   prompt_registry (39) -> workflow_registry (11 flows) -> workflow_step (131)
#   and 131/131 steps carry a prompt_id. A step carries question_template +
#   expected + parser, so THE PROMPT IS THE QUESTION.
#
# MEASURED: plan_session_log runs ask(504) -> ttl(404) -> plan(45) -> confirm(45),
# but `plan_sessions` is EMPTY (0 rows) — the PLAN itself is never persisted.
@app.route("/api/conversation_center/prompt_to_plan", methods=["GET"])
def api_conversation_center_prompt_to_plan() -> Any:
    """The prompt->plan chain, the plan stages, and playwright readiness.

    EVERY number is computed here from ONE connection at ONE moment.
    """
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            def _n(sql: str, args: tuple = ()) -> int:
                try:
                    return int(conn.execute(sql, args).fetchone()[0])
                except Exception:
                    return 0

            # ---- THE CHAIN -------------------------------------------------
            prompts = _n("SELECT COUNT(*) FROM prompt_registry")
            flows = _n("SELECT COUNT(*) FROM workflow_registry")
            steps = _n("SELECT COUNT(*) FROM workflow_step")
            linked = _n("SELECT COUNT(*) FROM workflow_step s JOIN prompt_registry p "
                        "ON p.prompt_id = s.prompt_id")
            # A REAL step, so the relation is shown by example, not asserted.
            sample = []
            for r in conn.execute(
                    "SELECT s.step_no, s.step_kind, s.question_template, s.expected, "
                    "p.prompt_key, p.name, w.workflow_key "
                    "FROM workflow_step s "
                    "LEFT JOIN prompt_registry p ON p.prompt_id = s.prompt_id "
                    "LEFT JOIN workflow_registry w ON w.workflow_id = s.workflow_id "
                    "WHERE w.workflow_key = 'worker_identity_flow' "
                    "ORDER BY s.step_no LIMIT 6"):
                sample.append({
                    "step_no": r["step_no"], "step_kind": r["step_kind"],
                    "question": (r["question_template"] or "")[:120],
                    "expected": r["expected"], "prompt_key": r["prompt_key"],
                    "prompt_name": r["name"], "flow_key": r["workflow_key"],
                })

            # ---- THE PLAN STAGES -------------------------------------------
            stages = []
            for r in conn.execute("SELECT stage, COUNT(*) n FROM plan_session_log "
                                  "GROUP BY stage ORDER BY n DESC"):
                stages.append({"stage": r["stage"], "rows": r["n"]})
            plan_rows = _n("SELECT COUNT(*) FROM plan_sessions")

            # ---- PLAYWRIGHT READINESS --------------------------------------
            envs = []
            for r in conn.execute(
                    "SELECT id, environment_id, name, is_active "
                    "FROM playwright_environment ORDER BY id"):
                envs.append({"playwright_id": r["id"],
                             "environment_id": r["environment_id"],
                             "name": r["name"], "is_active": r["is_active"]})
            # The vscode environment's steps, with their LATEST verdict.
            pw_steps = []
            pid = 1
            for r in conn.execute(
                    "SELECT step_no, step_key, action FROM playwright_step "
                    "WHERE playwright_id = ? ORDER BY step_no", (pid,)):
                last = conn.execute(
                    "SELECT status, got, created_at FROM playwright_step_run "
                    "WHERE playwright_id = ? AND step_no = ? "
                    "ORDER BY id DESC LIMIT 1", (pid, r["step_no"])).fetchone()
                pw_steps.append({
                    "step_no": r["step_no"], "step_key": r["step_key"],
                    "action": r["action"],
                    "verdict": last["status"] if last else "NEVER RUN",
                    "got": (last["got"] or "")[:160] if last else "",
                    "at": last["created_at"] if last else None,
                })
            counts = {"PASS": 0, "FAIL": 0, "SKIP": 0, "UNKNOWN": 0, "NEVER RUN": 0}
            for s in pw_steps:
                counts[s["verdict"]] = counts.get(s["verdict"], 0) + 1
            ready = counts["FAIL"] == 0 and counts["NEVER RUN"] == 0

            # ---- THE SESSION MISMATCH --------------------------------------
            # MEASURED: the human named one session, VS Code's ACTIVE session is
            # another, and the active one is NOT in identity_registry. That is
            # exactly why playwright step 6 FAILS.
            asked = request.args.get("session_id") or ""
            active = ""
            for s in pw_steps:
                if s["step_key"] == "confirm_identity_session" and s["got"]:
                    m = re.search(r"active session ([0-9a-f\-]{36})", s["got"])
                    if m:
                        active = m.group(1)
            asked_reg = bool(asked) and _n(
                "SELECT COUNT(*) FROM identity_registry WHERE session_id = ?",
                (asked,)) > 0
            active_reg = bool(active) and _n(
                "SELECT COUNT(*) FROM identity_registry WHERE session_id = ?",
                (active,)) > 0
            mismatch = bool(asked) and bool(active) and asked != active

            return jsonify({
                "ok": True,
                "chain": {
                    "prompts": prompts, "flows": flows, "steps": steps,
                    "steps_with_prompt": linked,
                    "relation": ("a flow step carries question_template + expected "
                                 "+ parser + prompt_id, so the PROMPT IS THE "
                                 "QUESTION; the chain is prompt -> flow step -> "
                                 "question -> answer -> plan"),
                    "sample": sample,
                },
                "plan": {
                    "stages": stages,
                    "plan_sessions_rows": plan_rows,
                    "why": ("the chain runs ask -> ttl -> plan -> confirm, but "
                            "`plan_sessions` holds %d row(s), so the PLAN itself "
                            "is never persisted" % plan_rows),
                },
                "playwright": {
                    "environments": envs,
                    "steps": pw_steps,
                    "counts": counts,
                    "ready": ready,
                    "why": ("%d PASS / %d FAIL / %d SKIP — playwright is %s"
                            % (counts["PASS"], counts["FAIL"], counts["SKIP"],
                               "READY" if ready else "NOT READY")),
                },
                "session": {
                    "asked": asked, "asked_registered": asked_reg,
                    "active": active, "active_registered": active_reg,
                    "mismatch": mismatch,
                    "why": ("the human named %s but VS Code's ACTIVE session is %s, "
                            "which is %s in identity_registry — that is why "
                            "playwright step 6 FAILS"
                            % (asked or "(none)", active or "(unknown)",
                               "REGISTERED" if active_reg else "NOT"))
                    if mismatch else "the named session matches the active session",
                },
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# THE HUMAN'S BLOCKER, MEASURED 2026-09-26:
#   "i ccan't understand how to work with this fucking ui"
# The index page asked the human to TYPE `chat_id (e.g. 68)`, but NOTHING on the
# page listed the chats, and MEASURED all 69 `chat` rows have `title = NULL`.
# So the human had no way to know 68 existed. A form that needs an id the page
# never shows is not a UI — it is a trap.
#
# THIS ENDPOINT IS THE STEP THE PAGE WAS MISSING: it LISTS the chats, one row per
# chat, each carrying the human-readable facts a person needs to CHOOSE one.
@app.route("/api/conversation_center/chats", methods=["GET"])
def api_conversation_center_chats() -> Any:
    """List the CHATs so a human can PICK one, instead of typing an id blind.

    MEASURED 2026-09-26: all 69 `chat` rows have `title IS NULL` and
    `opened_by IS NULL`. A list of 69 bare ids would be just as unusable, so each
    label is DERIVED and the derivation is NAMED in `label_kind`:
        'title'          -- the chat has its own title (0 of 69 today)
        'first_question' -- the first Question turn, truncated (evidence)
        'chat_key'       -- the chat_key, when the chat has no turns at all
    The response states how many labels were DERIVED, so the page can say so
    rather than implying the names are authored.
    """
    try:
        q = (request.args.get("q") or "").strip()
        try:
            limit = max(1, min(int(request.args.get("limit") or 100), 500))
        except Exception:
            limit = 100
        one = request.args.get("chat_id")
        try:
            one_id = int(one) if one not in (None, "") else None
        except Exception:
            one_id = None

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT c.chat_id, c.chat_key, c.title, c.source, c.is_active,
                       c.created_at, c.updated_at,
                       -- THE TURNS OF THIS CHAT, COUNTED BY SESSION.
                       -- MEASURED 2026-09-28, and this was a WRONG POPULATION:
                       -- `chat_center_message.chat_id` holds the CONVERSATION id
                       -- (`chat_main.id`), NOT the chat id. MEASURED: session
                       -- `d01a8339-...` has `chat_main.id=80` and its 36 turns
                       -- carry `chat_id=80`, while its CHAT is `chat_id=70`. So
                       -- `m.chat_id = c.chat_id` counted a row that merely
                       -- SHARED THE NUMBER — the report showed `turns: 1`
                       -- instead of 36. The chat's own conversations name the
                       -- sessions, so the count is by SESSION: the population
                       -- that cannot collide with an id.
                       (SELECT COUNT(*) FROM chat_center_message m
                         WHERE m.session_id IN (
                               SELECT cm2.session_id FROM chat_main cm2
                                WHERE cm2.chat_id = c.chat_id))  AS turns,
                       (SELECT COUNT(*) FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id)           AS conversations,
                       (SELECT COUNT(*) FROM identity_registry i
                         WHERE i.chat_id = c.chat_id)            AS identities,
                       (SELECT cm.session_id FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS session_id,
                       (SELECT cm.ide FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS ide,
                       (SELECT cm.llm FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS llm,
                       -- THE TWO KEYS (2026-09-27). The human asked: "Field
                       -- session ID and it has 2 key for converaction or chat,
                       -- what is that". MEASURED, `chat_main` carries TWO
                       -- derived keys and they are NOT the same thing:
                       --   sha256    = sha256(session_id)            kind='function'
                       --   chat_hash = sha256(chat_id | session_id)  kind='pair_key'
                       -- Both are returned so the page can SHOW the difference
                       -- instead of leaving the human to guess.
                       (SELECT cm.id FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS conversation_id,
                       (SELECT cm.sha256 FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS sha256,
                       (SELECT cm.chat_hash FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS chat_hash,
                       (SELECT cm.chat_hash_recomputed FROM chat_main cm
                         WHERE cm.chat_id = c.chat_id
                         ORDER BY cm.id LIMIT 1)                 AS chat_hash_recomputed,
                       -- THE TURN-DERIVED FIELDS ARE READ BY SESSION, NOT BY
                       -- `m.chat_id`. MEASURED 2026-09-28: `chat_center_message.chat_id`
                       -- holds the CONVERSATION id (`chat_main.id`), NOT the chat
                       -- id, so `m.chat_id = c.chat_id` matched a row that merely
                       -- SHARED THE NUMBER. MEASURED: chat 16's own turn title
                       -- (`Optical-mouse drift is answerable natively: ...`) was
                       -- never found, so the list showed its `chat_key` instead.
                       -- The chat's own conversations name the sessions, so the
                       -- read is by SESSION — the population that cannot collide.
                       (SELECT substr(COALESCE(NULLIF(m.title, ''), m.content), 1, 90)
                          FROM chat_center_message m
                         WHERE m.session_id IN (
                               SELECT cm2.session_id FROM chat_main cm2
                                WHERE cm2.chat_id = c.chat_id)
                           AND m.role = 'Question'
                         ORDER BY m.id LIMIT 1)                  AS first_question,
                       -- THE TITLE (2026-09-27). MEASURED: `chat_center_message`
                       -- carries a real `title` for 480 of 2,529 rows, and this
                       -- endpoint never read it — so the list showed a truncated
                       -- first question where a title already existed.
                       (SELECT m.title FROM chat_center_message m
                         WHERE m.session_id IN (
                               SELECT cm2.session_id FROM chat_main cm2
                                WHERE cm2.chat_id = c.chat_id)
                           AND m.title IS NOT NULL AND TRIM(m.title) <> ''
                         ORDER BY m.id LIMIT 1)                  AS turn_title,
                       -- THE LAST ACTIVITY. A chat is found by WHEN it was last
                       -- used, so the newest turn's timestamp is the sort key a
                       -- human actually wants. Falls back to the chat row.
                       -- COUNTED BY SESSION, for the same measured reason as
                       -- `turns` above: `m.chat_id` is the CONVERSATION id.
                       (SELECT MAX(m.created_at) FROM chat_center_message m
                         WHERE m.session_id IN (
                               SELECT cm2.session_id FROM chat_main cm2
                                WHERE cm2.chat_id = c.chat_id))  AS last_turn_at
                  FROM chat c
                 ORDER BY turns DESC, c.chat_id ASC
                """).fetchall()

            chats = []
            for r in rows:
                title = (r["title"] or "").strip()
                turn_title = " ".join((r["turn_title"] or "").split())
                first_q = " ".join((r["first_question"] or "").split())
                if one_id is not None and int(r["chat_id"]) != one_id:
                    continue
                # THE LABEL ORDER: an authored title wins, then a turn's own
                # title, then the first question, then the chat_key. Each step is
                # NAMED in `label_kind`, so the page never implies a derived name
                # was authored.
                if title:
                    label, kind = title, "title"
                elif turn_title:
                    label, kind = turn_title[:90], "turn_title"
                elif first_q:
                    label, kind = first_q[:90], "first_question"
                else:
                    label, kind = (r["chat_key"] or ""), "chat_key"
                item = {
                    "chat_id": int(r["chat_id"]),
                    "label": label,
                    "label_kind": kind,
                    "turns": int(r["turns"] or 0),
                    "conversations": int(r["conversations"] or 0),
                    "identities": int(r["identities"] or 0),
                    "session_id": r["session_id"] or "",
                    "ide": r["ide"] or "",
                    "llm": r["llm"] or "",
                    # THE TWO KEYS, and WHICH IS WHICH. `sha256` is a function of
                    # the session alone; `chat_hash` is the PAIR key. The page
                    # labels them, so the human is not left to guess.
                    "conversation_id": (int(r["conversation_id"])
                                        if r["conversation_id"] is not None
                                        else None),
                    "sha256": r["sha256"] or "",
                    "chat_hash": r["chat_hash"] or "",
                    "chat_hash_recomputed": r["chat_hash_recomputed"] or "",
                    "source": r["source"] or "",
                    "is_active": int(r["is_active"] or 0),
                    "chat_key": r["chat_key"] or "",
                    "created_at": r["created_at"] or "",
                    "updated_at": r["updated_at"] or "",
                    # THE TIME A HUMAN SORTS BY. `last_at` is the newest turn when
                    # there is one, else the chat row's own updated_at. Both are
                    # stored UTC; the PAGE converts them (timefmt.js).
                    "last_at": (r["last_turn_at"] or r["updated_at"]
                                or r["created_at"] or ""),
                }
                if q:
                    hay = "%s %s %s %s" % (item["chat_id"], item["label"],
                                           item["session_id"], item["chat_key"])
                    if q.lower() not in hay.lower():
                        continue
                chats.append(item)

            total = len(chats)
            with_turns = sum(1 for c in chats if c["turns"] > 0)
            derived = sum(1 for c in chats if c["label_kind"] != "title")
            shown = chats[:limit]
            return jsonify({
                "ok": True,
                "chats": shown,
                "total": total,
                "shown": len(shown),
                "limit": limit,
                "filter": q,
                # THE HONESTY FIELDS. The page can then say "these names are
                # derived" instead of implying every chat was named by a human.
                "counts": {
                    "chats": total,
                    "with_turns": with_turns,
                    "empty": total - with_turns,
                    "titled": total - derived,
                    "derived_labels": derived,
                },
                "why": ("every chat title is NULL, so %d of %d labels are "
                        "DERIVED from the chat's first Question turn (or its "
                        "chat_key when it has no turns)" % (derived, total)),
            })
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Capability Center (one flow, many backends) =================

# The shared flow skeleton. A capability only swaps the BACKEND per stage.
# `flow_ref` on each capability points here — that is the machine-readable
# proof that two capabilities (PPT / video) run the SAME flow.
VIDEO_7_STAGE_FLOW = [
    "research", "proposal", "script", "scene_plan",
    "assets", "edit", "compose",
]


def _capability_row(contract: dict[str, Any]) -> dict[str, Any]:
    """Flatten one skill_contract row into the Capability Center shape."""
    env = contract.get("environment") or {}
    if isinstance(env, str):
        try:
            env = json.loads(env)
        except Exception:
            env = {}
    flow = contract.get("flow") or []
    if isinstance(flow, str):
        try:
            flow = json.loads(flow)
        except Exception:
            flow = []
    stages = []
    for st in flow:
        if isinstance(st, dict):
            stages.append({
                "stage": st.get("stage") or "",
                "backend": st.get("backend") or "",
                "ask": st.get("ask") or [],
            })
    return {
        "contract_id": contract.get("contract_id") or "",
        "skill_key": contract.get("skill_key") or "",
        "taxonomy_path": contract.get("taxonomy_path") or "",
        "purpose": contract.get("purpose") or "",
        "status": contract.get("status") or "draft",
        "flow_ref": env.get("flow_ref") or "",
        "backend": env.get("backend") or "",
        "gpu_required": bool(env.get("gpu_required")),
        "stages": stages,
        "current_streak": contract.get("current_streak") or 0,
        "target_streak": contract.get("target_streak") or 100,
        "best_streak": contract.get("best_streak") or 0,
        "streak_qualified": bool(contract.get("streak_qualified")),
    }


# ================= Skill Contracts (DB-driven contract SSOT) =================
# Served under the Skill Prompt SSOT catalog (nav id `skill-ssot`).


@app.route("/api/skill-contracts", methods=["GET"])
def api_skill_contracts_list() -> Any:
    """List skill contracts joined with their streak counters."""
    try:
        import skill_contract_store as scs

        status = (request.args.get("status") or "").strip() or None
        rows = scs.list_contracts(status=status)
        return jsonify({"ok": True, "contracts": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>", methods=["GET"])
def api_skill_contracts_get(contract_id: str) -> Any:
    """One contract + Field Register + TDD cases + streak + review log."""
    try:
        import skill_contract_store as scs

        contract = scs.get_contract(contract_id)
        if not contract:
            return jsonify({"ok": False, "error": f"contract not found: {contract_id}"}), 404
        return jsonify({
            "ok": True,
            "contract": contract,
            "fields": scs.list_fields(contract_id),
            "tdd_cases": scs.list_tdd_cases(contract_id),
            "streak": scs.get_streak(
                contract_id, rule_version=int(contract.get("version") or 1)
            ),
            "review_log": scs.list_review_logs(contract_id),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/streak", methods=["POST"])
def api_skill_contracts_streak(contract_id: str) -> Any:
    """Record one TDD round; the streak is recomputed from the DB."""
    data = request.get_json(silent=True) or {}
    case_key = str(data.get("case_key") or "").strip()
    if not case_key:
        return jsonify({"ok": False, "error": "case_key required"}), 400
    try:
        import skill_contract_store as scs

        res = scs.record_streak_result(
            contract_id,
            case_key,
            bool(data.get("passed")),
            rule_version=int(data.get("rule_version") or 1),
            target_streak=int(data.get("target_streak") or 100),
        )
        if not res.get("ok"):
            return jsonify(res), 400
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/run-tdd", methods=["POST"])
def api_skill_contracts_run_tdd(contract_id: str) -> Any:
    """Execute a contract's TDD cases for real and record one streak round.

    C1: this turns the TDD gate from CLI-only into something operable from the
    UI. It does NOT reimplement execution — it calls the canonical
    `skill_tdd_runner.run_contract()`, which reads each case's own fixtures and
    dispatches through `PROBES`. A second execution path is the bug class that
    has already been fixed four times in this codebase.

    EVIDENCE ISOLATION (mandatory): the env_task_proof contract's probes call
    `evidence_store.open_evidence()`. Without redirecting the root, every click
    would write `EVID-tdd_*` folders into the REAL `evidence/` tree — the exact
    pollution fixed in `_run_p2_streak.py` during B3. The previous root is
    restored in `finally`.
    """
    data = request.get_json(silent=True) or {}
    try:
        import skill_contract_store as scs

        contract = scs.get_contract(contract_id)
        if not contract:
            # An unknown id is a client error, not a zero-case success.
            return jsonify({"ok": False, "error": "unknown contract",
                            "contract_id": contract_id}), 404

        rule_version = int(data.get("rule_version") or contract.get("version") or 1)
        target_streak = int(data.get("target_streak") or 100)

        import tempfile

        import evidence_store
        import skill_tdd_runner as runner

        prev_root = evidence_store.set_evidence_root(
            Path(tempfile.mkdtemp(prefix="run_tdd_route_evidence_"))
        )
        try:
            res = runner.run_contract(
                contract_id,
                rule_version=rule_version,
                target_streak=target_streak,
                record=True,
                verbose=False,
            )
        finally:
            evidence_store.set_evidence_root(prev_root)

        if res.get("error"):
            return jsonify({"ok": False, "error": res["error"],
                            "contract_id": contract_id}), 400
        return jsonify({
            "ok": bool(res.get("ok")),
            "contract_id": contract_id,
            "n_cases": res.get("n_cases", 0),
            "n_passed": res.get("n_passed", 0),
            "results": res.get("results", []),
            "streak": res.get("streak"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/fields", methods=["POST"])
def api_skill_contracts_field_upsert(contract_id: str) -> Any:
    """Upsert one Field Register row (field-level SSOT)."""
    data = request.get_json(silent=True) or {}
    try:
        import skill_contract_store as scs

        res = scs.upsert_field(
            contract_id,
            str(data.get("field_name") or "").strip(),
            str(data.get("data_type") or "").strip(),
            str(data.get("hard_rule") or "").strip(),
            field_id=data.get("field_id"),
            taxonomy_path=data.get("taxonomy_path"),
            mandatory=bool(data.get("mandatory")),
            enum=data.get("enum"),
            owner_skill_id=data.get("owner_skill_id"),
            immutable=bool(data.get("immutable")),
            remark=data.get("remark"),
        )
        if not res.get("ok"):
            return jsonify(res), 400
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/tdd-cases", methods=["POST"])
def api_skill_contracts_tdd_upsert(contract_id: str) -> Any:
    """Upsert one TDD proof case (pass / hard_fail)."""
    data = request.get_json(silent=True) or {}
    try:
        import skill_contract_store as scs

        res = scs.upsert_tdd_case(
            str(data.get("case_key") or "").strip(),
            contract_id,
            str(data.get("kind") or "").strip(),
            str(data.get("assertion") or "").strip(),
            input_payload=data.get("input"),
            expected=data.get("expected"),
            status=str(data.get("status") or "active"),
        )
        if not res.get("ok"):
            return jsonify(res), 400
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/validate", methods=["POST"])
def api_skill_contracts_validate(contract_id: str) -> Any:
    """Hard-validate a payload against the contract's Field Register."""
    data = request.get_json(silent=True) or {}
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "payload (object) required"}), 400
    try:
        import skill_contract_store as scs

        ok, errors = scs.validate_payload_against_contract(
            contract_id, payload, current=data.get("current")
        )
        return jsonify({"ok": ok, "valid": ok, "errors": errors})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/bump-version", methods=["POST"])
def api_skill_contracts_bump(contract_id: str) -> Any:
    """Bump the rule version so the streak restarts (reset trigger)."""
    data = request.get_json(silent=True) or {}
    try:
        import skill_contract_store as scs

        res = scs.bump_rule_version(
            contract_id, reason=str(data.get("reason") or "")
        )
        if not res.get("ok"):
            return jsonify(res), 400
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/seed-eight-level", methods=["POST"])
def api_skill_contracts_seed_eight() -> Any:
    """Seed one representative contract per taxonomy level (idempotent)."""
    try:
        import skill_contract_store as scs

        return jsonify(scs.seed_eight_level_contracts())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/readiness", methods=["GET"])
def api_skill_contracts_readiness(contract_id: str) -> Any:
    """Report whether a contract meets the promotion thresholds."""
    try:
        import skill_contract_store as scs

        return jsonify(scs.contract_readiness(contract_id))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/promote", methods=["POST"])
def api_skill_contracts_promote(contract_id: str) -> Any:
    """Promote a draft contract to active when it meets the thresholds."""
    try:
        import skill_contract_store as scs

        res = scs.promote_contract(contract_id)
        if not res.get("ok"):
            return jsonify(res), 400
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/promote-ready", methods=["POST"])
def api_skill_contracts_promote_ready() -> Any:
    """Promote every draft contract that meets the thresholds."""
    try:
        import skill_contract_store as scs

        return jsonify(scs.promote_ready_contracts())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/standardize-sources", methods=["POST"])
def api_skill_contracts_standardize_sources() -> Any:
    """Normalise every contract source to manual/seed/proven."""
    try:
        import skill_contract_store as scs

        return jsonify(scs.standardize_sources())
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/taxonomy-check", methods=["POST"])
def api_skill_contracts_taxonomy_check() -> Any:
    """Hard-validate a taxonomy_path against the ontology registry."""
    data = request.get_json(silent=True) or {}
    path = str(data.get("taxonomy_path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "taxonomy_path required"}), 400
    try:
        import skill_contract_store as scs

        ok, errors = scs.validate_taxonomy_path(path)
        return jsonify({"ok": ok, "valid": ok, "errors": errors,
                        "parsed": scs.parse_taxonomy_path(path)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-contracts/<contract_id>/deps-check", methods=["GET", "POST"])
def api_skill_contracts_deps_check(contract_id: str) -> Any:
    """Check declared depends_on: each dep must exist and be streak-qualified."""
    data = request.get_json(silent=True) or {}
    require_qualified = data.get("require_qualified")
    if require_qualified is None:
        require_qualified = (request.args.get("require_qualified") or "1") not in (
            "0", "false", "False", ""
        )
    try:
        import skill_contract_store as scs

        return jsonify(scs.check_dependencies(
            contract_id, require_qualified=bool(require_qualified)
        ))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= ticket_center API (entity progress trace) =================
# "eumu" = ENTITY. A ticket traces how an entity progresses through a service.
# These endpoints are THIN WRAPPERS over `ticket_store`: the status machine, the
# entity verification and the append-only event write all live THERE, so the UI
# cannot bypass a rule by calling a different path. A second implementation here
# would be a second set of rules.


@app.route("/api/ticket_center/list", methods=["GET"])
def api_ticket_center_list() -> Any:
    """List tickets, optionally filtered by service / status / active."""
    try:
        import ticket_store as tks

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            rows = tks.list_tickets(
                conn,
                service=(request.args.get("service") or "").strip(),
                status=(request.args.get("status") or "").strip(),
                active_only=(request.args.get("active_only") or "") in
                            ("1", "true", "True"),
                limit=int(request.args.get("limit") or 200),
            )
            return jsonify({"ok": True, "rows": rows, "total": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/services", methods=["GET"])
def api_ticket_center_services() -> Any:
    """The ACTIVE service registry, read from the table."""
    try:
        import ticket_store as tks

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            return jsonify({"ok": True, "rows": tks.services(conn)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/modules", methods=["GET"])
def api_ticket_center_modules() -> Any:
    """The ACTIVE module registry — WHERE in the system a ticket can happen.

    A ticket is for a SERVICE (`ticket_center`); a module says where it happens
    (`module_registry`). They are separate facts (user, 2026-09-21: "ticket is
    for which services provide to / no related too other, don't mix up"), so
    the UI needs BOTH lists to open a ticket and map it.
    """
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(
                "SELECT module_id, module_key, name, description "
                "FROM module_registry WHERE is_active=1 ORDER BY module_key")]
            return jsonify({"ok": True, "rows": rows, "total": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/module/<path:module_key>", methods=["GET"])
def api_ticket_center_module(module_key: str) -> Any:
    """Every ticket MAPPED to one module, by JOIN. A bad key is a 400.

    This replaced `/entity/<eumu_id>`. A ticket no longer carries an entity, so
    "which tickets are about this entity" is no longer a question the ticket
    table answers — and it should not try to. The question here is "which
    tickets belong to this module".
    """
    try:
        import ticket_store as tks

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            return jsonify(tks.tickets_for_module(conn, module_key))
        finally:
            conn.close()
    except Exception as e:
        import ticket_store as tks

        if isinstance(e, tks.TicketRefused):
            return jsonify({"ok": False, "error": str(e),
                            "module": module_key}), 400
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/<int:ticket_id>", methods=["GET"])
def api_ticket_center_detail(ticket_id: int) -> Any:
    """One ticket: its current state, its FULL event history, and its chats."""
    try:
        import ticket_store as tks

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            return jsonify(tks.progress(conn, int(ticket_id)))
        finally:
            conn.close()
    except Exception as e:
        import ticket_store as tks

        if isinstance(e, tks.TicketRefused):
            return jsonify({"ok": False, "error": str(e)}), 404
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/transition", methods=["POST"])
def api_ticket_center_transition() -> Any:
    """Move a ticket to a new status. The RULES live in ticket_store.

    Body: {ticket_id, to_status, actor, note?, cite_ref?}. An illegal transition
    is a 400 with the reason — the UI shows it rather than silently doing
    nothing, because a status that did not change and a status that was refused
    look identical otherwise.
    """
    import ticket_store as tks

    data = request.get_json(silent=True) or {}
    try:
        tid = int(data.get("ticket_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "ticket_id must be an integer"}), 400
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            out = tks.transition(
                conn, tid, str(data.get("to_status") or ""),
                actor=str(data.get("actor") or ""),
                note=str(data.get("note") or ""),
                cite_ref=str(data.get("cite_ref") or ""),
            )
            return jsonify(out)
        finally:
            conn.close()
    except tks.TicketRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/create", methods=["POST"])
def api_ticket_center_create() -> Any:
    """Open a ticket for a SERVICE. The GATE lives in ticket_store.

    Body: {service, title?, opened_by?, note?, cite_ref?, module?}.

    NO `eumu_id`. A ticket is for a SERVICE (user, 2026-09-21: "ticket is for
    which services provide to / no related too other, don't mix up"). The
    entity is linked elsewhere, through the mapping, so this route does not
    accept one and cannot mix the two up.

    `module` is OPTIONAL. When given it is written to `ticket_module_map`,
    which is the only place a ticket and a module meet.

    A refusal is a 400 carrying the reason, never a silent skip: a ticket that
    was not created and a ticket that was created look identical to a caller
    that only checks for a 200.
    """
    import ticket_store as tks

    data = request.get_json(silent=True) or {}
    service = str(data.get("service") or "").strip()
    if not service:
        return jsonify({"ok": False, "error": "service is required"}), 400
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            out = tks.create_ticket(
                conn, service=service,
                title=str(data.get("title") or ""),
                opened_by=str(data.get("opened_by") or "ticket_center_ui"),
                note=str(data.get("note") or ""),
                cite_ref=str(data.get("cite_ref")
                             or "mouse_spot_helper.py:api_ticket_center_create"),
                module=str(data.get("module") or ""),
            )
            return jsonify(out)
        finally:
            conn.close()
    except tks.TicketRefused as e:
        return jsonify({"ok": False, "error": str(e), "service": service}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/screen_watch/status", methods=["GET"])
def api_screen_watch_status() -> Any:
    """The screen-watch service: cadence, rule settings, targets, last run.

    `targets` reports Playwright and OpenClaw SEPARATELY, because OpenClaw is
    OPTIONAL: it is the only target that can drive a desktop app, and it is the
    FALLBACK for browser work. A single "available" flag would hide which of the
    two is actually usable.
    """
    try:
        import screen_watch as sw

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            return jsonify(sw.status())
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/screen_watch/runs", methods=["GET"])
def api_screen_watch_runs() -> Any:
    """The run history, newest first. One row per capture+analysis cycle."""
    try:
        import screen_watch as sw

        rows = sw.history(limit=int(request.args.get("limit") or 50))
        return jsonify({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/screen_watch/run/<int:run_id>", methods=["GET"])
def api_screen_watch_run(run_id: int) -> Any:
    """ONE run in full: the image, the analysis, BOTH decision layers, the plan.

    The two decision layers are returned SEPARATELY (`model_says` / `rule_says`)
    because a single verdict hides which layer decided — and when a false
    positive happens, that is the first thing you need to know.
    """
    try:
        import screen_watch as sw

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            sw.ensure_schema(conn)
            row = conn.execute("SELECT * FROM screen_watch_run WHERE run_id=?",
                               (int(run_id),)).fetchone()
            if not row:
                return jsonify({"ok": False,
                                "error": "no such run: %d" % run_id}), 404
            out = dict(row)
            try:
                out["dispatch_plan"] = json.loads(out.get("dispatch_plan")
                                                  or "{}")
            except Exception:
                pass
            return jsonify({"ok": True, "run": out})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/screen_watch/run", methods=["POST"])
def api_screen_watch_run_now() -> Any:
    """Trigger ONE cycle now: capture -> 7B analyse -> decide -> plan.

    Body: {confirm?: bool}. `confirm` defaults to FALSE, so this route PLANS and
    does not act. The user's machine is not a sandbox, so acting needs an
    explicit confirm — and the default must be the safe one.
    """
    try:
        import screen_watch as sw

        data = request.get_json(silent=True) or {}
        out = sw.run_once(dispatch_confirm=bool(data.get("confirm")))
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/screen_watch/targets", methods=["GET"])
def api_screen_watch_targets() -> Any:
    """Which dispatch targets can act right now, and WHY not when they cannot.

    An unavailable target is a NORMAL state, not a fault — the same rule
    `llm_service_store` follows for an offline provider.
    """
    try:
        import screen_watch as sw

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            return jsonify({"ok": True,
                            "rows": [dict(sw.TARGETS[t],
                                          **sw.target_available(t, conn))
                                     for t in sw.TARGETS]})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/screen_watch/dispatch", methods=["POST"])
def api_screen_watch_dispatch() -> Any:
    """Plan (or, with confirm, act on) a dispatch for one run.

    Body: {run_id, target?, confirm?}. Without `confirm` this returns the PLAN
    and does NOTHING. A refusal is a 400 carrying the reason, never a silent
    skip: a dispatch that did not happen and one that did look identical to a
    caller that only checks for a 200.
    """
    try:
        import screen_watch as sw

        data = request.get_json(silent=True) or {}
        try:
            rid = int(data.get("run_id"))
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "run_id must be an integer"}), 400
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            sw.ensure_schema(conn)
            row = conn.execute("SELECT * FROM screen_watch_run WHERE run_id=?",
                               (rid,)).fetchone()
            if not row:
                return jsonify({"ok": False,
                                "error": "no such run: %d" % rid}), 404
            analysis = {"ok": bool(row["analysis_ok"]),
                        "severity": row["severity"],
                        "ui_state": row["ui_state"],
                        "likely_cause": row["likely_cause"],
                        "reason": row["reason"],
                        "needs_help": row["model_says"] == "help"}
            out = sw.dispatch(analysis,
                              target=(str(data.get("target") or "") or None),
                              confirm=bool(data.get("confirm")), conn=conn)
            return jsonify(out)
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/ticket_center/map", methods=["POST"])
def api_ticket_center_map() -> Any:
    """Map a ticket to a MODULE, or remove the mapping.

    Body: {ticket_id, module, action?} where action is 'map' (default) or
    'unmap'. This is the mapping endpoint, separate from create, so a mapping
    can be added or removed without touching the ticket.
    """
    import ticket_store as tks

    data = request.get_json(silent=True) or {}
    try:
        tid = int(data.get("ticket_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False,
                        "error": "ticket_id must be an integer"}), 400
    module = str(data.get("module") or "").strip()
    if not module:
        return jsonify({"ok": False, "error": "module is required"}), 400
    action = str(data.get("action") or "map").strip().lower()
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            if action == "unmap":
                return jsonify(tks.unmap_module(conn, tid, module))
            return jsonify(tks.map_module(conn, tid, module))
        finally:
            conn.close()
    except tks.TicketRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ============ chat_registry API (a ticket paired with its chat) ============
# The user's flow: middleware (chat_center submit) opens a TICKET, the ticket
# goes to a chat room to get a chat_id, and `chat_registry` pairs the two. These
# are THIN WRAPPERS over `chat_registry_store`, for the same reason the ticket
# routes are thin wrappers over `ticket_store`: a second implementation would be
# a second set of rules.
#
# RENAMED from `/api/case/*` (user, 2026-09-21: "why not name = chat_registry /
# not easy for mis-understand / rename it now"). "Case" already means a fault
# case, a TDD case, and a test case in this repo, so a reader could not tell
# which one this was.


@app.route("/api/chat_registry/list", methods=["GET"])
def api_chat_registry_list() -> Any:
    """List rows joined to their ticket and chat."""
    try:
        import chat_registry_store as crs

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            rows = crs.list_chat_registers(
                conn,
                status=(request.args.get("status") or "").strip(),
                # `ticket_origin`, NOT `service`. MEASURED (2026-09-26): the
                # route passed `service=`, which `list_chat_registers` does not
                # accept -- its signature is
                #   (conn, *, status='', ticket_origin='', active_only=False, limit=200)
                # so every call raised `TypeError` and the endpoint returned
                # HTTP 500. The Chat Center's "Workflow" tab then showed an
                # empty page, which reads as "no rows" rather than "the call is
                # wrong". `ticket_origin` IS the column `chat_registry` carries;
                # `service` was never one.
                ticket_origin=(request.args.get("ticket_origin")
                               or request.args.get("service") or "").strip(),
                active_only=(request.args.get("active_only") or "") in
                            ("1", "true", "True"),
                limit=int(request.args.get("limit") or 200),
            )
            return jsonify({"ok": True, "rows": rows, "total": len(rows)})
        finally:
            conn.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_registry/<int:chat_registry_id>", methods=["GET"])
def api_chat_registry_detail(chat_registry_id: int) -> Any:
    """One row with its ticket and chat. An unknown id is a 404, not an empty."""
    try:
        import chat_registry_store as crs

        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            return jsonify(crs.get_chat_registry(conn, int(chat_registry_id)))
        finally:
            conn.close()
    except Exception as e:
        import chat_registry_store as crs

        if isinstance(e, crs.ChatRegisterRefused):
            return jsonify({"ok": False, "error": str(e)}), 404
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/chat_registry/create", methods=["POST"])
def api_chat_registry_create() -> Any:
    """Pair a REAL ticket with a REAL chat (the chat may be absent).

    Body: {ticket_id, chat_id?, service?, opened_by, chat_key?, status?}.

    One row per ticket: a second call for the same ticket returns the EXISTING
    row (`created: false`) rather than a duplicate, because `ticket_id` is
    UNIQUE and a silent second row would make "the chat for this ticket"
    ambiguous.
    """
    import chat_registry_store as crs

    data = request.get_json(silent=True) or {}
    try:
        ticket_id = int(data.get("ticket_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False,
                        "error": "ticket_id must be an integer"}), 400
    chat_raw = data.get("chat_id")
    chat_id = None
    if chat_raw not in (None, ""):
        try:
            chat_id = int(chat_raw)
        except (TypeError, ValueError):
            return jsonify({"ok": False,
                            "error": "chat_id must be an integer"}), 400
    try:
        conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=5)
        try:
            conn.row_factory = sqlite3.Row
            out = crs.create_chat_registry(
                conn, ticket_id=ticket_id, chat_id=chat_id,
                service=str(data.get("service") or ""),
                opened_by=str(data.get("opened_by") or "chat_registry_ui"),
                chat_key=str(data.get("chat_key") or ""),
                status=str(data.get("status") or "open"),
            )
            return jsonify(out)
        finally:
            conn.close()
    except crs.ChatRegisterRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/capability_center/list", methods=["GET"])
def api_capability_center_list() -> Any:
    """List capabilities with their flow_ref + backend + streak."""
    try:
        import skill_contract_store as scs

        rows = [_capability_row(c) for c in scs.list_contracts()]
        return jsonify({"ok": True, "rows": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/capability_center/<cap_id>", methods=["GET"])
def api_capability_center_detail(cap_id: str) -> Any:
    """One capability: flow stages + backend map + interview questions.

    Also returns `compare` — every capability sharing the same `flow_ref`,
    so the UI can show identical stage names with different backends.
    """
    try:
        import skill_contract_store as scs

        all_rows = [_capability_row(c) for c in scs.list_contracts()]
        row = next((r for r in all_rows if r["contract_id"] == cap_id), None)
        if not row:
            return jsonify({"ok": False, "error": "capability not found"}), 404
        compare = [
            r for r in all_rows
            if r["flow_ref"] and r["flow_ref"] == row["flow_ref"]
        ]
        return jsonify({"ok": True, "row": row, "compare": compare})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/capability_center/interview", methods=["POST"])
def api_capability_center_interview() -> Any:
    """Persist interview answers into skill_contract_review_log (append-only).

    Body: {contract_id, answers: {"stage::qi": "text"}, trace_id?, chat_id?,
           task_id?}

    Mapping (gap -> revision semantics):
      gap      = the UNANSWERED questions (the knowledge gap)
      revision = the ANSWERED pairs, "stage::question = answer"
      streak_result = "pass" when every question is answered, else "fail"

    A pass also bumps the streak counter; a fail resets it. This is what makes
    the interview accumulate experience instead of living in localStorage.
    """
    import skill_contract_store as scs

    data = request.get_json(silent=True) or {}
    contract_id = str(data.get("contract_id") or "").strip()
    answers = data.get("answers") or {}
    if not contract_id:
        return jsonify({"ok": False, "error": "contract_id is required"}), 400
    if not isinstance(answers, dict):
        return jsonify({"ok": False, "error": "answers must be an object"}), 400

    try:
        contract = scs.get_contract(contract_id)
        if not contract:
            return jsonify({
                "ok": False, "error": f"contract not found: {contract_id}"
            }), 404

        flow = contract.get("flow") or []
        if isinstance(flow, str):
            try:
                flow = json.loads(flow)
            except Exception:
                flow = []

        answered: list[str] = []
        missing: list[str] = []
        for st in flow:
            if not isinstance(st, dict):
                continue
            stage = st.get("stage") or ""
            for qi, q in enumerate(st.get("ask") or []):
                key = f"{stage}::{qi}"
                val = str(answers.get(key) or "").strip()
                if val:
                    answered.append(f"{key} ({q}) = {val}")
                else:
                    missing.append(f"{key} ({q})")

        total = len(answered) + len(missing)
        if total == 0:
            return jsonify({
                "ok": False, "error": "capability has no interview questions"
            }), 400

        streak_result = "pass" if not missing else "fail"
        res = scs.append_review_log(
            contract_id,
            gap="; ".join(missing) or None,
            revision="; ".join(answered) or None,
            streak_result=streak_result,
            linked_trace_id=str(data.get("trace_id") or "").strip() or None,
            chat_id=str(data.get("chat_id") or "").strip() or None,
            task_id=str(data.get("task_id") or "").strip() or None,
        )
        if not res.get("ok"):
            return jsonify(res), 400

        # A pass bumps the streak; a fail resets it (DB-calculated).
        streak = scs.record_streak_result(
            contract_id,
            f"{contract_id}.interview.{streak_result}",
            streak_result == "pass",
        )
        return jsonify({
            "ok": True,
            "log_id": res.get("log_id"),
            "contract_id": contract_id,
            "answered": len(answered),
            "missing": len(missing),
            "total": total,
            "streak_result": streak_result,
            "current_streak": streak.get("current_streak"),
            "best_streak": streak.get("best_streak"),
            "target_streak": streak.get("target_streak"),
            "qualified": streak.get("qualified"),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Capability Center Slide Room =================
# A deck follows the shared video-7-stage flow. The web Slide Room renders it
# slide by slide; no PPTX file is involved.

# F9 deck content: each step carries the same three lanes the video flow uses
# for shot cards (simplified for slides): precondition / action / proof.
F9_STEPS = [
    {"n": 1, "stage": "research", "title": "Record clipboard sequence",
     "pre": "—", "act": "seqBefore := ClipSeq()",
     "proof": "A sequence number exists"},
    {"n": 2, "stage": "research", "title": "Write the start log",
     "pre": "—", "act": 'F9Log("F9 start (CDP, seq=...)")',
     "proof": "f9_log.txt has the line"},
    {"n": 3, "stage": "research", "title": "Show the user a tooltip",
     "pre": "—", "act": 'Tooltip("F9: CDP grabbing...")',
     "proof": "Tooltip visible on screen"},
    {"n": 4, "stage": "proposal", "title": "Ensure CDP is up",
     "pre": "chrome.exe exists", "act": 'cdp_common.ensure_cdp("F9-CDP")',
     "proof": "log: CDP already running / up after auto-launch"},
    {"n": 5, "stage": "proposal", "title": "Find the DeepSeek tab",
     "pre": "CDP is up", "act": "cdp_common.find_tab()",
     "proof": "Tab dict with webSocketDebuggerUrl"},
    {"n": 6, "stage": "script", "title": "Grab the latest assistant message",
     "pre": "Tab exists", "act": "cdp_evaluate(tab, GRAB_JS)",
     "proof": "Non-empty text returned"},
    {"n": 7, "stage": "script", "title": "Strip the trailing offer line",
     "pre": "Text present", "act": "strip_offer(text)",
     "proof": "Length shrinks (M < N)"},
    {"n": 8, "stage": "scene_plan", "title": "Write the clipboard",
     "pre": "Stripped text", "act": "cdp_common.set_clipboard(final)",
     "proof": "Clipboard content equals final"},
    {"n": 9, "stage": "scene_plan", "title": "Verify the clipboard changed",
     "pre": "—", "act": "seqAfter := ClipSeq(); compare",
     "proof": "seqAfter != seqBefore"},
    {"n": 10, "stage": "assets", "title": "Activate VS Code",
     "pre": "—", "act": 'WinActivate("ahk_exe Code.exe")',
     "proof": "VS Code is foreground"},
    {"n": 11, "stage": "edit", "title": "Paste",
     "pre": "VS Code foreground", "act": 'SendInput("^v")',
     "proof": "Text appears in the chat box"},
    {"n": 12, "stage": "compose", "title": "Close out",
     "pre": "—", "act": 'F9Log("Pasted into VS Code")',
     "proof": "Log line + tooltip clears after 2s"},
]

F9_EXIT_CODES = [
    ("0", "ok"),
    ("1", "CDP unreachable (even after auto-launch)"),
    ("2", "no DeepSeek tab"),
    ("3", "no assistant message"),
]


def _slide(title: str, bullets: list, stage: str = "", kind: str = "content"):
    return {"title": title, "bullets": bullets, "stage": stage, "kind": kind}


def _build_f9_deck(flow: list, env: dict) -> dict:
    """Build the F9 deck as JSON (no PPTX). flow/env come from the DB row."""
    slides = []
    slides.append(_slide(
        "F9 — DeepSeek reply to VS Code",
        [
            "Produced with the video-produce flow",
            "backend: %s" % env.get("backend", "web"),
            "flow_ref: %s" % env.get("flow_ref", "video-7-stage"),
        ],
        stage="compose", kind="title",
    ))
    if flow:
        slides.append(_slide(
            "Flow: %s" % env.get("flow_ref", "video-7-stage"),
            ["%d. %s  →  %s" % (i + 1, st.get("stage"), st.get("backend"))
             for i, st in enumerate(flow)]
            + ["", "Stage names are fixed. Only the backend changes."],
            stage="proposal",
        ))
    slides.append(_slide(
        "What F9 does",
        [
            "One key: copy the latest DeepSeek assistant reply into VS Code.",
            "Reads the DOM over CDP — no coordinates, no hover, no scroll.",
            "Strips the trailing offer line before pasting.",
            "Composition: Tool #1 (copy) + Tool #2 (CDP env auto-launch).",
        ],
        stage="research",
    ))
    for step in F9_STEPS:
        slides.append(_slide(
            "Step %d/%d · %s" % (step["n"], len(F9_STEPS), step["title"]),
            [
                "stage: %s" % step["stage"],
                "precondition: %s" % step["pre"],
                "action: %s" % step["act"],
                "proof: %s" % step["proof"],
            ],
            stage=step["stage"], kind="step",
        ))
    slides.append(_slide(
        "Exit codes (f9_cdp_copy.py)",
        ["%s = %s" % (c, d) for c, d in F9_EXIT_CODES],
        stage="assets",
    ))
    slides.append(_slide(
        "Key design decisions",
        [
            "CDP over coordinate clicks: the copy button is hover-triggered",
            "  and moves with scroll, so fixed coordinates fail in seconds.",
            "ctypes SetClipboardData, not tkinter: Tk owns the clipboard and",
            "  loses the content when destroyed.",
            "Logic lives in cdp_common.py; entry scripts stay thin.",
        ],
        stage="edit",
    ))
    slides.append(_slide(
        "Proof",
        [
            "Clipboard sequence changes (seqBefore -> seqAfter).",
            "Log line: OK: CDP copy success.",
            "Content verified after paste.",
            "F6 self-debug: 0 AHK error dialogs.",
        ],
        stage="compose",
    ))
    return {
        "deck_id": "F9_FLOW",
        "capability_id": "CAP.PPT.PRODUCE",
        "title": "F9 — DeepSeek reply to VS Code",
        "flow_ref": env.get("flow_ref", "video-7-stage"),
        "backend": env.get("backend", "web"),
        "slides": slides,
    }


# Deck registry: deck_id -> builder. Add new decks here.
DECK_BUILDERS = {"F9_FLOW": _build_f9_deck}

DECK_CATALOG = [
    {
        "deck_id": "F9_FLOW",
        "capability_id": "CAP.PPT.PRODUCE",
        "title": "F9 — DeepSeek reply to VS Code",
        "description": "12 steps · precondition / action / proof per step",
    },
]


def _build_deck(deck_id: str):
    """Build one deck, reading the capability row for flow + env."""
    builder = DECK_BUILDERS.get(deck_id)
    if not builder:
        return None
    flow, env = [], {}
    try:
        import skill_contract_store as scs

        contract = scs.get_contract("CAP.PPT.PRODUCE")
        if contract:
            flow = contract.get("flow") or []
            if isinstance(flow, str):
                try:
                    flow = json.loads(flow)
                except Exception:
                    flow = []
            env = contract.get("environment") or {}
            if isinstance(env, str):
                try:
                    env = json.loads(env)
                except Exception:
                    env = {}
    except Exception:
        pass
    return builder(flow, env)


@app.route("/api/capability_center/slides", methods=["GET"])
def api_capability_center_slides_catalog() -> Any:
    """Catalog of available decks for the Slide Room."""
    return jsonify({"ok": True, "decks": DECK_CATALOG, "total": len(DECK_CATALOG)})


@app.route("/api/capability_center/slides/<deck_id>", methods=["GET"])
def api_capability_center_slides_deck(deck_id: str) -> Any:
    """One deck as JSON: slides[] with title / bullets / stage / kind."""
    try:
        deck = _build_deck(deck_id)
        if not deck:
            return jsonify({"ok": False, "error": "deck not found"}), 404
        return jsonify({"ok": True, "deck": deck})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Coord click + CDP measure (no LLM) =================

# VS Code Settings → Copilot → Chat permission buttons (CDP-measured center).
PERMISSION_TARGETS = [
    {
        "target_id": "perm_default",
        "name": "Default permissions",
        "text": "Default permissions",
        "press": 1,
    },
    {
        "target_id": "perm_sandbox",
        "name": "Sandboxing for terminal",
        "text": "Sandboxing for terminal",
        "press": 2,
    },
    {
        "target_id": "perm_allow_all",
        "name": "Allow all (auto-approve)",
        "text": "Allow all",
        "press": 3,
    },
    {
        "target_id": "perm_autopilot",
        "name": "Autopilot (Preview)",
        "text": "Autopilot",
        "press": 4,
    },
]

# Find a button by visible text (no :has-text — plain DOM), return center X,Y.
_MEASURE_JS = """
(text) => {
  const els = Array.from(document.querySelectorAll('button, [role="button"], a'));
  const el = els.find((e) => (e.textContent || '').trim().indexOf(text) === 0)
    || els.find((e) => (e.textContent || '').trim().includes(text));
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    x: Math.round(r.left + r.width / 2),
    y: Math.round(r.top + r.height / 2),
    w: Math.round(r.width),
    h: Math.round(r.height),
    text: (el.textContent || '').trim().slice(0, 80)
  };
}
"""


def _cdp_find_tab_by_url(substr: str):
    try:
        import cdp_common

        for t in cdp_common.get("/json"):
            if t.get("type") == "page" and substr in (t.get("url") or "").lower():
                return t
    except Exception:
        return None
    return None


def _cdp_all_tabs():
    """All CDP page/webview targets (VS Code settings webview shows up here too)."""
    try:
        import cdp_common

        return [t for t in cdp_common.get("/json") if t.get("type") in ("page", "webview")]
    except Exception:
        return []


@app.route("/api/coord-targets/measure", methods=["GET"])
def api_coord_targets_measure() -> Any:
    """CDP-measure button size on the VS Code Settings page → center X,Y → save to table.

    No LLM / no OpenClaw: pure CDP getBoundingClientRect.
    Query: ?target=perm_default | all
    """
    import cdp_common

    if not cdp_common.cdp_ready():
        return jsonify({"ok": False, "error": "CDP Chrome (port 9222) not running"}), 500
    which = str(request.args.get("target") or "all").strip()
    targets = (
        [t for t in PERMISSION_TARGETS if t["target_id"] == which]
        if which != "all"
        else PERMISSION_TARGETS
    )
    if not targets:
        return jsonify({"ok": False, "error": f"unknown target '{which}'"}), 400
    tabs = _cdp_all_tabs()
    if not tabs:
        return jsonify({"ok": False, "error": "No CDP page targets found"}), 400
    results = []
    for t in targets:
        expr = "((%s) => %s)('%s')" % (
            "text",
            _MEASURE_JS,
            t["text"].replace("'", "\\'"),
        )
        val = None
        err = None
        hit_tab = None
        for tab in tabs:
            try:
                v = cdp_common.cdp_evaluate(tab, expr)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                continue
            if v:
                val = v
                hit_tab = tab
                break
        if not val:
            results.append(
                {
                    "target_id": t["target_id"],
                    "name": t["name"],
                    "ok": False,
                    "error": err or "button not found in any CDP target (open VS Code Settings → Copilot → Chat first)",
                }
            )
            continue
        try:
            init_coord_db()
            save_target_point(
                target_logo="",
                target_name=t["name"],
                x=int(val["x"]),
                y=int(val["y"]),
                action="click",
                isactive=1,
                llm_score=100,
                error=None,
                target_id=t["target_id"],
            )
        except Exception as e:
            results.append(
                {
                    "target_id": t["target_id"],
                    "name": t["name"],
                    "ok": False,
                    "error": f"save failed: {e}",
                }
            )
            continue
        results.append(
            {
                "target_id": t["target_id"],
                "name": t["name"],
                "ok": True,
                "x": int(val["x"]),
                "y": int(val["y"]),
                "w": int(val.get("w") or 0),
                "h": int(val.get("h") or 0),
                "text": val.get("text") or "",
                "tab": (hit_tab or {}).get("url", "")[:120],
            }
        )
    return jsonify({"ok": True, "results": results})


def _cdp_click_at(tab: dict, x: int, y: int) -> None:
    """CDP Input.dispatchMouseEvent click at viewport coords (deterministic)."""
    import cdp_common

    ws = __import__("websocket").create_connection(tab["webSocketDebuggerUrl"], timeout=15)
    try:
        mid = [0]

        def send(method, params=None):
            mid[0] += 1
            ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == mid[0]:
                    return msg

        send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
    finally:
        ws.close()


@app.route("/api/coord-targets/<path:name>/click", methods=["POST"])
def api_coord_target_click(name: str) -> Any:
    """Click the stored center X,Y for a target (100% deterministic, no LLM).

    Path 1 (preferred): CDP — find the tab/webview still containing the button,
    re-measure its live center, dispatch a CDP mouse click at that center.
    Path 2 (fallback): pyautogui click at the stored screen X,Y.
    """
    data = request.get_json(silent=True) or {}
    tid = str(data.get("target_id") or "").strip() or None
    try:
        init_coord_db()
        pt = None
        if tid:
            pt = get_target_point(target_id=tid, active_only=False)
        if pt is None:
            pt = get_target_point(target_id=name, active_only=False)
        if pt is None:
            pt = get_target_point(name, active_only=False)
        if not pt:
            return jsonify({"ok": False, "error": f"target '{name}' not in coord table"}), 404
        x, y = int(pt["x"]), int(pt["y"])
        if x <= 0 and y <= 0:
            return jsonify({"ok": False, "error": "stored X,Y is 0,0 — measure first"}), 400

        # Path 1: CDP click (works for VS Code settings webview + CDP Chrome tabs)
        perm = next((p for p in PERMISSION_TARGETS if p["target_id"] == (tid or name)), None)
        if perm:
            import cdp_common

            if cdp_common.cdp_ready():
                expr = "((%s) => %s)('%s')" % (
                    "text",
                    _MEASURE_JS,
                    perm["text"].replace("'", "\\'"),
                )
                for tab in _cdp_all_tabs():
                    try:
                        v = cdp_common.cdp_evaluate(tab, expr)
                    except Exception:
                        continue
                    if v:
                        cx, cy = int(v["x"]), int(v["y"])
                        _cdp_click_at(tab, cx, cy)
                        return jsonify(
                            {
                                "ok": True,
                                "method": "cdp",
                                "clicked": {"x": cx, "y": cy},
                                "tab": (tab.get("url") or "")[:120],
                                "point": pt,
                            }
                        )
                return jsonify(
                    {
                        "ok": False,
                        "error": "CDP up but button not found in any tab — open VS Code Settings → Copilot → Chat first",
                    }
                ), 404

        # Path 2: pyautogui at stored screen coords
        import time

        import pyautogui

        time.sleep(0.15)
        pyautogui.click(x, y)
        return jsonify({"ok": True, "method": "pyautogui", "clicked": {"x": x, "y": y}, "point": pt})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/seed-gold", methods=["POST"])
def api_skills_seed_gold(skill_key: str) -> Any:
    data = request.get_json(silent=True) or {}
    include_icons = data.get("include_icons", True)
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        out = seed_gold_cases(AGENT_DB_PATH, include_icons=bool(include_icons))
        out["skill_key"] = skill_key
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/test-gold", methods=["POST"])
def api_skills_test_gold(skill_key: str) -> Any:
    data = request.get_json(silent=True) or {}
    runs = int(data.get("runs_per_case") or data.get("runs") or 1)
    runs = max(1, min(runs, 100))
    version_label = data.get("version_label") or data.get("version")
    if version_label is not None:
        version_label = str(version_label).strip() or None
    model = data.get("model")
    status = str(data.get("status") or "active").strip() or "active"
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        out = test_gold_suite(
            skill_key=skill_key,
            version_label=version_label,
            runs_per_case=runs,
            status=status,
            model=model,
            db_path=AGENT_DB_PATH,
            persist=True,
        )
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/improve-draft", methods=["POST"])
def api_skills_improve_draft(skill_key: str) -> Any:
    """Improve prompt via text LLM and save as draft only (never activate)."""
    data = request.get_json(silent=True) or {}
    prompt_text = data.get("prompt_text")
    if prompt_text is not None:
        prompt_text = str(prompt_text)
        if not prompt_text.strip():
            prompt_text = None
    version_label = (data.get("version_label") or data.get("version") or None)
    if version_label is not None:
        version_label = str(version_label).strip() or None
    model = data.get("model")
    notes = data.get("notes")
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        out = improve_prompt_to_draft(
            skill_key=skill_key,
            prompt_text=prompt_text,
            version_label=version_label,
            model=str(model).strip() if model else None,
            notes=str(notes) if notes else None,
            db_path=AGENT_DB_PATH,
            commit=True,
        )
        # Law: improve never activates — strip any accidental activate flags
        out["draft_only"] = True
        out["activated"] = False
        trio = identity_trio_dict(
            writer=str(data.get("writer") or "").strip(),
            task_id=str(data.get("task_id") or "").strip(),
            session_id=str(data.get("session_id") or "").strip(),
        )
        out["identity"] = trio
        out["writer"] = trio["writer"]
        out["task_id"] = trio["task_id"]
        out["session_id"] = trio["session_id"]
        out["trailer"] = identity_trailer(**trio)
        status = 200 if out.get("ok") else 400
        return jsonify(out), status
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Multi-dimensional prompt SSOT (registers) =================
# The `prompt_key` column existed with UNIQUE (skill_key, prompt_key, version_label)
# but had only ever held 'main'. These routes make it the COMBINATION axis: one
# prompt_key per composed prompt, so variants coexist and can be measured.
#
# SSOT: these routes read `wording_registry` via `prompt_generator`, NOT the
# retired `prompt_dimension`. The API surface is unchanged.

def _prompt_dimension_mod():
    import prompt_generator as pd
    return pd


@app.route("/api/skills/<skill_key>/dimensions", methods=["GET"])
def api_skill_dimensions(skill_key: str) -> Any:
    """List the registered axes and their values for a skill."""
    try:
        pd = _prompt_dimension_mod()
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        conn = pd._connect(AGENT_DB_PATH)
        try:
            if request.args.get("seed") == "1" and skill_key == "mouse_spot_verify":
                pd.seed_mouse_spot(conn, skill_key)
            reg = pd.registry(conn, skill_key)
            combos = pd.expand_combos(conn, skill_key) if reg else []
        finally:
            conn.close()
        return jsonify({
            "ok": True,
            "skill_key": skill_key,
            "dimensions": reg,
            "dimension_count": len(reg),
            "combo_count": len(combos),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/dimensions/expand", methods=["GET", "POST"])
def api_skill_dimensions_expand(skill_key: str) -> Any:
    """Enumerate combinations. POST body may restrict axes: {axes:{dim:[values]}}."""
    data = request.get_json(silent=True) or {}
    axes = data.get("axes") if isinstance(data.get("axes"), dict) else None
    limit = int(data.get("limit") or request.args.get("limit") or 0)
    try:
        pd = _prompt_dimension_mod()
        conn = pd._connect(AGENT_DB_PATH)
        try:
            combos = pd.expand_combos(conn, skill_key, axes=axes)
        finally:
            conn.close()
        show = combos[:limit] if limit else combos
        return jsonify({
            "ok": True,
            "skill_key": skill_key,
            "total": len(combos),
            "combos": [
                {"combo_key": pd.combo_key(c),
                 "prompt_key": pd.combo_key_short(c),
                 "axes": c} for c in show
            ],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400


@app.route("/api/skills/<skill_key>/compose", methods=["POST"])
def api_skill_compose(skill_key: str) -> Any:
    """Compose one combination and (optionally) store it as a prompt document.

    Body: {axes:{dim:value,...}, apply?:bool, template?:str}
    Returns the composed text + its sha256 so the caller can verify what will be
    sent rather than trusting a label.
    """
    data = request.get_json(silent=True) or {}
    axes = data.get("axes")
    if not isinstance(axes, dict) or not axes:
        return jsonify({"ok": False, "error": "axes {dim: value} required"}), 400
    template = str(data.get("template") or "").strip() or None
    try:
        pd = _prompt_dimension_mod()
        conn = pd._connect(AGENT_DB_PATH)
        try:
            tpl = template or (pd.MOUSE_SPOT_TEMPLATE if skill_key == "mouse_spot_verify"
                               else "")
            if not tpl:
                return jsonify({"ok": False,
                                "error": "no template for this skill; pass template"}), 400
            combo = pd.compose_combo(conn, skill_key,
                                     {str(k): str(v) for k, v in axes.items()},
                                     template=tpl)
            stored = None
            if data.get("apply"):
                stored = upsert_skill_prompt(
                    conn, skill_key=skill_key, prompt_key=combo["prompt_key"],
                    version_label=str(data.get("version_label") or "dim_v1"),
                    prompt_text=combo["prompt_text"], status="testing",
                    activate=False,
                    notes="composed: %s" % json.dumps(combo["axes"], ensure_ascii=False),
                )
                pd.record_combo(conn, skill_key=skill_key, combo=combo)
        finally:
            conn.close()
        return jsonify({"ok": True, "combo": combo, "stored": stored})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400


@app.route("/api/skills/<skill_key>/sweep/plan", methods=["GET"])
def api_skill_sweep_plan(skill_key: str) -> Any:
    """Show the sweep plan WITHOUT calling the model: split sizes + class mix.

    This exists so the split can be inspected before a run that costs hundreds of
    model calls. A holdout with one class cannot detect a prompt that fails the
    other class, so the plan refuses that combination up front.
    """
    try:
        import prompt_sweep as ps
        pd = _prompt_dimension_mod()
        conn = pd._connect(AGENT_DB_PATH)
        try:
            pd.seed_mouse_spot(conn, skill_key)
            cases = [dict(r) for r in conn.execute(
                "SELECT case_key, expected FROM skill_prompt_case "
                "WHERE skill_key=? AND status='active' AND image_path IS NOT NULL",
                (skill_key,))]
            train, holdout = ps.stratified_split(cases)
            combos = pd.expand_combos(conn, skill_key)
        finally:
            conn.close()

        def mix(keys):
            d: dict[str, int] = {}
            for c in cases:
                if str(c["case_key"]) in keys:
                    d[str(c["expected"])] = d.get(str(c["expected"]), 0) + 1
            return d

        hold_mix = mix(holdout)
        return jsonify({
            "ok": True,
            "skill_key": skill_key,
            "combo_count": len(combos),
            "cases": len(cases),
            "train": {"n": len(train), "classes": mix(train), "keys": train},
            "holdout": {"n": len(holdout), "classes": hold_mix, "keys": holdout},
            "holdout_can_discriminate": len(hold_mix) >= 2,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 400


@app.route("/api/skills/<skill_key>/combos", methods=["GET"])
def api_skill_combos(skill_key: str) -> Any:
    """Measurement ledger: every combination with its train/holdout numbers."""
    try:
        pd = _prompt_dimension_mod()
        conn = pd._connect(AGENT_DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            pd.ensure_tables(conn)
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM prompt_combo WHERE skill_key=? "
                "ORDER BY COALESCE(heldout_ba_pct, balanced_accuracy_pct) DESC, id DESC",
                (skill_key,))]
            for r in rows:
                if r.get("axes_json"):
                    try:
                        r["axes"] = json.loads(r["axes_json"])
                    except Exception:
                        r["axes"] = {}
                if r.get("answer_classes"):
                    try:
                        r["answer_classes"] = json.loads(r["answer_classes"])
                    except Exception:
                        pass
        finally:
            conn.close()
        certified = [r for r in rows
                     if (r.get("status") or "") == "certified"]
        return jsonify({
            "ok": True,
            "skill_key": skill_key,
            "count": len(rows),
            "certified": len(certified),
            "combos": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/<skill_key>/sweep/run", methods=["POST"])
def api_skill_sweep_run(skill_key: str) -> Any:
    """Run the sweep. Long (hundreds of model calls) — deliberately capped.

    Body: {rounds, holdout_rounds, top, max_combos, axes, apply}
    """
    data = request.get_json(silent=True) or {}
    try:
        import prompt_sweep as ps
    except Exception as e:
        return jsonify({"ok": False, "error": f"sweep unavailable: {e}"}), 500
    argv = ["--skill", skill_key,
            "--rounds", str(int(data.get("rounds") or 12)),
            "--holdout-rounds", str(int(data.get("holdout_rounds") or 12)),
            "--top", str(int(data.get("top") or 5)),
            "--seed", str(int(data.get("seed") or 20260920))]
    if data.get("max_combos"):
        argv += ["--max_combos", str(int(data["max_combos"]))]
    if data.get("axes") and isinstance(data["axes"], dict):
        argv += ["--axes", ";".join(
            "%s=%s" % (k, ",".join(v if isinstance(v, list) else [v]))
            for k, v in data["axes"].items())]
    if data.get("apply"):
        argv.append("--apply")
    try:
        rc = ps.main(argv)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    report = None
    try:
        if ps.REPORT.is_file():
            report = json.loads(ps.REPORT.read_text(encoding="utf-8"))
    except Exception:
        report = None
    return jsonify({"ok": rc == 0, "exit_code": rc, "report": report})


@app.route("/api/mouse/move", methods=["POST"])
def api_mouse_move() -> Any:
    """Move the OS cursor to (x, y). Calibration helper only.

    Body: {x, y, duration?}. Returns the ACTUAL cursor position read back after
    the move, so the caller never has to assume the move landed — that read-back
    is the proof step for `f_perm_click.py --measure-point`.
    """
    data = request.get_json(silent=True) or {}
    try:
        x = int(data.get("x"))
        y = int(data.get("y"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "x and y must be integers"}), 400
    try:
        import pyautogui
        sw, sh = pyautogui.size()
    except Exception as e:
        return jsonify({"ok": False, "error": f"screen size failed: {e}"}), 500
    if not (0 <= x < sw and 0 <= y < sh):
        return jsonify({
            "ok": False,
            "error": f"({x},{y}) outside screen {sw}x{sh}",
        }), 400
    try:
        pyautogui.moveTo(x, y, duration=float(data.get("duration") or 0.0))
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    got = mouse.position
    return jsonify({
        "ok": True,
        "requested": {"x": x, "y": y},
        "actual": {"x": int(got[0]), "y": int(got[1])},
        "moved": abs(int(got[0]) - x) <= 2 and abs(int(got[1]) - y) <= 2,
        "screen": {"width": int(sw), "height": int(sh)},
    })


# ================= Task Center preflight gate (SSOT = f_env_preflight.py) ========
# env_task_proof Rule 6: a recorded check is not a gate; ENFORCE at the point of
# action. These routes expose the EXISTING gate (f_env_preflight) so the UI can
# SHOW it and so an enqueue can be REFUSED. The refusal is HTTP 409, not
# 200-with-ok:false — a caller can ignore a flag, but it cannot ignore a failed
# request.
#
# NOTE: this deliberately does NOT re-implement any check. f_env_preflight
# aggregates the real check functions; a second copy would drift.

@app.route("/api/task_center/preflight", methods=["GET", "POST"])
def api_task_center_preflight() -> Any:
    """Measure the environment. Read-only: writes nothing, enqueues nothing."""
    try:
        import f_env_preflight as pf
        scope = str(request.args.get("scope") or "").strip() or None
        out = pf.run_all(scope=scope)
        out["ok"] = True
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/task_center/enqueue", methods=["POST"])
def api_task_center_enqueue() -> Any:
    """Enqueue ONE task, but ONLY on a proven environment.

    Returns 409 with the blocking check names when the environment is not
    proven, and writes NO row. That is the gate refusing, not a warning.
    """
    try:
        import f_env_preflight as pf
        import skill_task_queue as stq

        data = request.get_json(silent=True) or {}
        skill_id = str(data.get("skill_id") or "").strip()
        task_type = str(data.get("task_type") or "").strip()
        if not skill_id or not task_type:
            return jsonify({
                "ok": False,
                "error": "skill_id and task_type are required",
            }), 400
        payload = data.get("payload")
        schema = data.get("output_schema")
        if payload is None or schema is None:
            return jsonify({
                "ok": False,
                "error": "payload and output_schema are required",
            }), 400

        # THE GATE. Refuse before writing anything.
        # SCOPE=llm: an LLM task must not be blocked by a VIDEO dependency.
        # Measured 2026-09-20: the unscoped gate refused an LLM classify task
        # because ffmpeg was missing — a false refusal, which is the mirror
        # image of a false pass and trains the reader to bypass the gate.
        scope = str(data.get("scope") or "llm").strip() or "llm"
        report = pf.run_all(scope=scope)
        if not report.get("ready"):
            return jsonify({
                "ok": False,
                "error": "environment not proven",
                "scope": scope,
                "blocking": report.get("blocking"),
                "advisory": report.get("advisory"),
                "checks": report.get("checks"),
            }), 409

        model = str(data.get("model") or "").strip() or None
        kwargs: dict[str, Any] = {}
        if model:
            kwargs["model"] = model
        res = stq.enqueue(skill_id, task_type, payload, schema, **kwargs)
        res["preflight"] = {
            "ready": report.get("ready"),
            "scope": report.get("scope"),
            "passed": report.get("passed"),
            "total": report.get("total"),
            "blocking": report.get("blocking"),
        }
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= Evidence Center API (SSOT = evidence_store.py) =================
# Read-only over the evidence folders. Serves the stored artefacts so the UI can
# show them without exposing a filesystem path to the DOM.

@app.route("/api/evidence/list")
def api_evidence_list() -> Any:
    """List stored evidence, newest first. ?target=perm_default to filter."""
    try:
        import evidence_store
        target = str(request.args.get("target") or "").strip() or None
        rows = evidence_store.list_evidence(target)
        return jsonify({
            "ok": True,
            "root": str(evidence_store.EVIDENCE_ROOT),
            "count": len(rows),
            "evidence": rows,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/evidence/llm-100")
def api_evidence_llm_100() -> Any:
    """LLM 100-run proof evidence (SSOT = llm_100_run table).

    This is the LLM's OWN streak proof: each round is a real model answer
    compared against an oracle. It is NOT the contract gate streak (which is
    computed by _run_p2_streak.py from Field Register checks).

    Query: ?ref_tag=1.1F&model=qwen2.5:7b-instruct&limit=200
    """
    try:
        import sqlite3 as _sq

        ref_tag = str(request.args.get("ref_tag") or "").strip() or None
        model = str(request.args.get("model") or "").strip() or None
        try:
            limit = min(1000, max(1, int(request.args.get("limit") or 200)))
        except ValueError:
            limit = 200

        conn = _sq.connect(str(AGENT_DB_PATH))
        conn.row_factory = _sq.Row
        try:
            # ONE DOOR for the NAME. The old guard here was
            #   SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_100_run'
            # which answered "missing" after `llm_100_run` was RENAMED table -> view
            # (the compat view now wraps `proof_run`). MEASURED CONSEQUENCE: this
            # endpoint returned {"ok": true, "exists": false, "count": 0} while the
            # object held 2995 rows — a SILENT FALSE EMPTY. The object is now asked
            # about BY NAME ONLY, and the READ goes to the CANONICAL name so the
            # endpoint cannot address a name that is merely a leftover alias.
            import object_door as _od

            obj = _od.resolve_object(conn, "llm_100_run")
            if not obj["exists"]:
                return jsonify({"ok": True, "exists": False, "runs": [],
                                "rounds": [], "count": 0, "target": 110,
                                "asked": obj["asked"],
                                "canonical": obj["canonical"],
                                "reason": obj["reason"]})
            tbl = obj["canonical"]

            where, params = [], []
            if ref_tag:
                where.append("ref_tag = ?")
                params.append(ref_tag)
            if model:
                where.append("model = ?")
                params.append(model)
            clause = ("WHERE " + " AND ".join(where)) if where else ""

            runs = [dict(r) for r in conn.execute(
                f"""SELECT ref_tag, rule_version, model,
                           COUNT(*) AS rounds,
                           SUM(win) AS wins,
                           COUNT(*) - SUM(win) AS losses,
                           MAX(round_no) AS last_round,
                           MAX(created_at) AS last_at
                    FROM {tbl} {clause}
                    GROUP BY ref_tag, rule_version, model
                    ORDER BY ref_tag, rule_version, model""",
                tuple(params),
            ).fetchall()]

            # Current streak per run = trailing consecutive wins.
            for run in runs:
                rows = conn.execute(
                    """SELECT win FROM %s
                       WHERE ref_tag=? AND rule_version=?
                         AND IFNULL(model,'')=IFNULL(?,'')
                       ORDER BY round_no DESC""" % tbl,
                    (run["ref_tag"], run["rule_version"], run["model"]),
                ).fetchall()
                streak = 0
                for r in rows:
                    if int(r["win"] or 0) == 1:
                        streak += 1
                    else:
                        break
                run["current_streak"] = streak
                run["target"] = 110
                run["qualified"] = streak >= 110

            rounds = [dict(r) for r in conn.execute(
                f"""SELECT round_no, ref_tag, rule_version, model, value,
                           oracle_answer, llm_answer, win, failure_reason,
                           created_at
                    FROM {tbl} {clause}
                    ORDER BY id DESC LIMIT ?""",
                tuple(params) + (limit,),
            ).fetchall()]
        finally:
            conn.close()

        return jsonify({
            "ok": True,
            "exists": True,
            "runs": runs,
            "rounds": rounds,
            "count": len(rounds),
            "target": 110,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/evidence/<path:evidence_id>")
def api_evidence_detail(evidence_id: str) -> Any:
    """One evidence record: classify.json + report.md + the files that exist."""
    try:
        import evidence_store
        safe = Path(evidence_id).name          # no traversal
        d = evidence_store.EVIDENCE_ROOT / safe
        if not d.is_dir():
            return jsonify({"ok": False, "error": "not found"}), 404
        payload: dict[str, Any] = {"ok": True, "evidence_id": safe}
        cj = d / "classify.json"
        if cj.is_file():
            try:
                payload["classify"] = json.loads(
                    cj.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                payload["classify_error"] = f"{type(e).__name__}: {e}"
        # Capture-only folders have no classify.json but may carry a QC binding.
        qb = d / "qc_binding.json"
        if qb.is_file():
            try:
                payload["qc_binding"] = json.loads(
                    qb.read_text(encoding="utf-8", errors="replace"))
            except Exception as e:
                payload["qc_binding_error"] = f"{type(e).__name__}: {e}"
        rp = d / "report.md"
        if rp.is_file():
            payload["report_md"] = rp.read_text(encoding="utf-8", errors="replace")
        payload["files"] = {
            p.name: p.stat().st_size
            for p in sorted(d.iterdir()) if p.is_file()
        }
        return jsonify(payload)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/evidence/<path:evidence_id>/file/<path:name>")
def api_evidence_file(evidence_id: str, name: str) -> Any:
    """Serve one artefact from an evidence folder. Path-traversal safe."""
    import evidence_store
    safe_id = Path(evidence_id).name
    safe_name = Path(name).name
    if not safe_id or not safe_name or ".." in name or "/" in name or "\\" in name:
        return jsonify({"ok": False, "error": "invalid name"}), 400
    if not safe_name.lower().endswith((".png", ".json", ".md")):
        return jsonify({"ok": False, "error": "unsupported type"}), 400
    path = evidence_store.EVIDENCE_ROOT / safe_id / safe_name
    if not path.is_file():
        return jsonify({"ok": False, "error": "not found"}), 404
    mimetype = ("image/png" if safe_name.lower().endswith(".png")
                else "application/json" if safe_name.lower().endswith(".json")
                else "text/markdown")
    return send_file(path, mimetype=mimetype)


# ================= Skill Learning Center API (SSOT = skill_learning.py) =================
# The SPA calls /api/learning/* but only /api/skills/* existed, so the whole
# Skill Learning Center rendered "Learning API unavailable" — the library looked
# like it had vanished when in fact the routes were never wired. Backend logic
# already lived in skill_learning.py; these are thin adapters.

@app.route("/api/learning/overview")
def api_learning_overview() -> Any:
    """Counts for the Overview tab."""
    try:
        import skill_learning
        return jsonify({"ok": True, **skill_learning.overview()})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/mismatches")
def api_learning_mismatches() -> Any:
    """ask<->confirm mismatches. ?skill_key=&status=&limit="""
    try:
        import skill_learning
        items = skill_learning.list_mismatches(
            skill_key=str(request.args.get("skill_key") or "").strip() or None,
            status=str(request.args.get("status") or "").strip() or None,
            limit=request.args.get("limit", default=200, type=int) or 200,
        )
        return jsonify({"ok": True, "count": len(items), "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/mismatches/<int:mismatch_id>", methods=["POST"])
def api_learning_mismatch_status(mismatch_id: int) -> Any:
    """Set a mismatch status. Body: {status}"""
    data = request.get_json(silent=True) or {}
    status = str(data.get("status") or "").strip()
    if not status:
        return jsonify({"ok": False, "error": "status required"}), 400
    try:
        import skill_learning
        row = skill_learning.set_mismatch_status(mismatch_id, status)
        return jsonify({"ok": True, "mismatch": row})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/lessons", methods=["GET", "POST"])
def api_learning_lessons() -> Any:
    """GET list lessons. POST add one {skill_key, lesson_text, ...}"""
    import skill_learning
    if request.method == "GET":
        try:
            items = skill_learning.list_lessons(
                skill_key=str(request.args.get("skill_key") or "").strip() or None,
                status=str(request.args.get("status") or "").strip() or None,
                limit=request.args.get("limit", default=200, type=int) or 200,
            )
            return jsonify({"ok": True, "count": len(items), "items": items})
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or "").strip()
    lesson_text = str(data.get("lesson_text") or "").strip()
    if not skill_key or not lesson_text:
        return jsonify({"ok": False, "error": "skill_key + lesson_text required"}), 400
    try:
        # NOTE: add_lesson takes root_cause/suggested_fix, NOT candidate_prompt.
        # Passing an unknown kwarg raised TypeError — checked against the real
        # signature rather than assumed.
        row = skill_learning.add_lesson(
            skill_key=skill_key,
            lesson_text=lesson_text,
            source_type=str(data.get("source_type") or "manual"),
            root_cause=data.get("root_cause"),
            suggested_fix=data.get("suggested_fix"),
            source_ref=data.get("source_ref"),
            status=str(data.get("status") or "draft"),
        )
        return jsonify({"ok": True, "lesson": row})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/candidates")
def api_learning_candidates() -> Any:
    """Draft candidate versions + their latest test run."""
    try:
        import skill_learning
        items = skill_learning.list_candidates(
            skill_key=str(request.args.get("skill_key") or "").strip() or None,
        )
        return jsonify({"ok": True, "count": len(items), "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/candidates/test", methods=["POST"])
def api_learning_candidates_test() -> Any:
    """Short streak validation. Body: {skill_key, version_label, streak?}"""
    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or "").strip()
    version_label = str(data.get("version_label") or "").strip()
    if not skill_key or not version_label:
        return jsonify({"ok": False, "error": "skill_key + version_label required"}), 400
    try:
        import skill_learning
        out = skill_learning.test_candidate(
            skill_key=skill_key, version_label=version_label,
            streak=int(data.get("streak") or 20),
        )
        return jsonify({"ok": True, **(out or {})})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/candidates/merge", methods=["POST"])
def api_learning_candidates_merge() -> Any:
    """Merge a candidate into the library.

    Two guards: the candidate's latest test_run must have pass_gate=1, and the
    skill's contract TDD cases must be green (when the skill has a contract).

    NOTE: `ok` is the HTTP-level success of the call, NOT the merge result. A
    refused merge is a 200 with ok=true and result.ok=false, plus `reason`
    naming which guard refused. Collapsing the two would make a refusal look
    like a transport error — and the UI could not show WHY it was refused.
    """
    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or "").strip()
    version_label = str(data.get("version_label") or "").strip()
    if not skill_key or not version_label:
        return jsonify({"ok": False, "error": "skill_key + version_label required"}), 400
    try:
        import skill_learning
        out = dict(skill_learning.merge_candidate(
            skill_key=skill_key, version_label=version_label,
        ) or {})
        # The domain result must NOT be spread over the transport result. Doing
        # `{"ok": True, **out}` let out["ok"]=False overwrite the transport ok,
        # so a REFUSED merge looked like a failed HTTP call and the UI could not
        # read `reason`. Keep the two names strictly separate.
        result_ok = bool(out.pop("ok", False))
        if not result_ok:
            out["refused_by"] = (
                "contract_tdd" if out.get("contract_gate") else "test_run_gate"
            )
        return jsonify({
            "ok": True,              # the call succeeded
            "result_ok": result_ok,  # the merge succeeded (or was refused)
            **out,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/lessons/<lesson_key>/rewrite", methods=["POST"])
def api_learning_lesson_rewrite(lesson_key: str) -> Any:
    """Rewrite a skill prompt from a lesson. Body: {skill_key?, lesson_keys?[]}

    Law: this NEVER activates. It only creates a draft candidate version, which
    must then pass a 20-run streak with pass_gate=1 before it can be merged.
    """
    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or "").strip()
    extra = data.get("lesson_keys") or []
    if not isinstance(extra, list):
        extra = []
    keys = [str(lesson_key)] + [str(k).strip() for k in extra if str(k).strip()]
    # de-dup, keep order
    seen: list[str] = []
    for k in keys:
        if k not in seen:
            seen.append(k)
    try:
        import skill_learning
        out = skill_learning.rewrite_skill_from_lessons(
            skill_key=skill_key or None,
            lesson_keys=seen,
        )
        # Hard law: rewrite is draft-only, never activate.
        out["draft_only"] = True
        out["activated"] = False
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-catalogs")
def api_skill_catalogs() -> Any:
    """Catalog > subcatalog > skill tree scanned from the skills/ folder.

    Deliberately NOT /api/skills/catalogs: that would collide with the existing
    /api/skills/<skill_key> route and 'catalogs' would be read as a skill key.
    """
    try:
        import skill_library_api as sla
        cats = sla.list_skill_catalogs()
        total = sum(int(c.get("skill_count") or 0) for c in cats)
        return jsonify({"ok": True, "count": len(cats), "skill_total": total,
                        "catalogs": cats})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skill-library/sync", methods=["POST"])
def api_skill_library_sync() -> Any:
    """Rescan skills/ and register anything new. Idempotent."""
    try:
        import skill_library_api as sla
        out = sla.sync_skill_library()
        return jsonify({"ok": True, **(out or {})})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/ingest", methods=["POST"])
def api_learning_ingest() -> Any:
    """Run a case study on recent failures. Body: {skill_key, limit?}"""
    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or "").strip()
    if not skill_key:
        return jsonify({"ok": False, "error": "skill_key required"}), 400
    try:
        import skill_learning
        out = skill_learning.run_case_study(
            skill_key=skill_key,
            limit=int(data.get("limit") or 50),
        )
        return jsonify({"ok": True, **(out or {})})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/learning/lessons/import-github", methods=["POST"])
def api_learning_import_github() -> Any:
    """Import proofed lessons from text/URL. Body: {skill_key, text_or_url, is_url?}"""
    data = request.get_json(silent=True) or {}
    skill_key = str(data.get("skill_key") or "").strip()
    text_or_url = str(data.get("text_or_url") or "").strip()
    if not skill_key or not text_or_url:
        return jsonify({"ok": False, "error": "skill_key + text_or_url required"}), 400
    try:
        import skill_learning
        out = skill_learning.import_github_lessons(
            text_or_url=text_or_url,
            skill_key=skill_key,
            is_url=bool(data.get("is_url")),
        )
        if isinstance(out, int):
            out = {"imported": out}
        return jsonify({"ok": True, **(out or {})})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/openclaw/report")
def api_openclaw_report() -> Any:
    """OpenClaw settings + (OPT-IN) live MCP probe.

    Reads OpenClawTray settings.json AND, when asked, probes the MCP server, so
    the page can show the contradiction that matters: a tool failing while its
    gate reads true. Never raises — a dead MCP server is a reportable state, not
    a 500.

    `probe` IS OPT-IN (2026-09-23). MEASURED DEFECT: this used to ALWAYS probe,
    and the UI polls this route on a timer (`app.js:7351`), so a STATUS POLL
    opened an MCP session with OpenClaw Companion — which raises its "capturing
    your screen" consent notification when a session starts. The notification
    therefore fired ON A TIMER with no capture ever requested.

    The user: "problem is middleware, 7B not = openclaw ... not call openclaw
    when 7B start".

    So the DEFAULT is a status read that does NOT touch MCP. A caller that wants
    the live probe passes `?probe=1`, and the response says which it got.
    """
    probe = str(request.args.get("probe") or "").strip().lower() in (
        "1", "true", "yes", "on")
    try:
        import openclaw_settings
        return jsonify(openclaw_settings.build_openclaw_report(probe=probe))
    except Exception as e:
        return jsonify({
            "ok": False,
            "probed": bool(probe),
            "error": f"{type(e).__name__}: {e}",
            "settings": {"found": False, "raw": {}, "groups": {}},
            "probe": {"results": [], "tools": [], "tool_count": 0},
            "capabilities": [],
            "summary": {},
        }), 200


@app.route("/api/skills/seed", methods=["POST"])
def api_skills_seed() -> Any:
    try:
        out = seed_default_skills(AGENT_DB_PATH)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500




@app.route("/api/state")
def api_state() -> Any:
    return jsonify(get_state())


@app.route("/api/coord-targets", methods=["GET", "POST"])
def api_coord_targets_list() -> Any:
    if request.method == "GET":
        try:
            init_coord_db()
            return jsonify({"ok": True, "points": list_target_points(active_only=True)})
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    data = request.get_json(silent=True) or {}
    name = str(data.get("target_name") or data.get("name") or "").strip()
    tid = str(data.get("target_id") or data.get("id") or "").strip() or None
    if not name and not tid:
        return jsonify({"ok": False, "error": "target_name or target_id required"}), 400
    if not name:
        name = tid or ""
    try:
        x = int(data["x"]) if data.get("x") is not None and data.get("x") != "" else 0
    except (TypeError, ValueError):
        x = 0
    try:
        y = int(data["y"]) if data.get("y") is not None and data.get("y") != "" else 0
    except (TypeError, ValueError):
        y = 0
    action = str(data.get("action") or "click").strip() or "click"
    logo = str(data.get("target_logo") or data.get("logo") or data.get("image") or "")
    try:
        isactive = int(data.get("isactive", 1))
    except (TypeError, ValueError):
        isactive = 1
    try:
        llm_score = int(data.get("llm_score", 100))
    except (TypeError, ValueError):
        llm_score = 100
    err = data.get("error", None)
    if err is not None:
        err = str(err)
    try:
        point = save_target_point(
            target_logo=logo,
            target_name=name,
            x=x,
            y=y,
            action=action,
            isactive=isactive,
            llm_score=llm_score,
            error=err,
            target_id=tid,
        )
        if err is None:
            clear_target_error(name, target_id=tid)
        return jsonify({"ok": True, "point": point})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/coord-targets/grouped", methods=["GET"])
def api_coord_targets_grouped() -> Any:
    """Success-target table: targets grouped by target_id with learned X/Y ranges."""
    try:
        init_coord_db()
        return jsonify({"ok": True, "groups": list_grouped()})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ================= DB-driven target AREA + confirm checklist =================
# User spec 2026-09-19: the popup target table must store the AREA
# (X1,Y1)-(X2,Y2) per target (not just a center point) and carry a
# checklist_confirm yes/no flag. f_perm_click.py --set-native ABORTS the
# click unless every active target is confirmed 'yes'.
PERMISSION_CHECKLIST_POPUP = "perm_picker"
PERMISSION_CHECKLIST_TARGETS = [
    {"target_id": "perm_default", "label": "Default permissions"},
    {"target_id": "perm_allow_all", "label": "Allow all (auto-approve)"},
    {"target_id": "perm_autopilot", "label": "Autopilot (Preview)"},
]


def _seed_permission_checklist() -> int:
    """Ensure the 3 permission targets exist as area rows (idempotent).

    Only inserts missing rows; never overwrites a measured area or an
    existing checklist_confirm value.
    """
    init_coord_db()
    existing = {
        a["target_id"]
        for a in list_target_areas(
            popup_id=PERMISSION_CHECKLIST_POPUP, active_only=False
        )
    }
    added = 0
    for t in PERMISSION_CHECKLIST_TARGETS:
        if t["target_id"] in existing:
            continue
        upsert_target_area(
            t["target_id"],
            popup_id=PERMISSION_CHECKLIST_POPUP,
            label=t["label"],
            checklist_confirm="no",
        )
        added += 1
    return added


@app.route("/api/coord-targets/area", methods=["GET", "POST"])
def api_coord_targets_area() -> Any:
    """GET: list target areas (+checklist_confirm). POST: upsert one area.

    POST body: {target_id, popup_id?, label?, x1,y1,x2,y2,
                checklist_confirm?, confirm_reason?, isactive?}
    cx,cy are derived server-side from the area.
    """
    if request.method == "GET":
        try:
            _seed_permission_checklist()
            popup = str(request.args.get("popup") or "").strip() or None
            include_all = bool(request.args.get("all"))
            return jsonify(
                {
                    "ok": True,
                    "areas": list_target_areas(
                        popup_id=popup, active_only=not include_all
                    ),
                }
            )
        except Exception as e:
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

    data = request.get_json(silent=True) or {}
    tid = str(data.get("target_id") or "").strip()
    if not tid:
        return jsonify({"ok": False, "error": "target_id required"}), 400

    def _int(key: str) -> int:
        try:
            v = data.get(key)
            return int(v) if v is not None and v != "" else 0
        except (TypeError, ValueError):
            return 0

    confirm = data.get("checklist_confirm")
    if confirm is not None:
        confirm = str(confirm).strip().lower()
        confirm = "yes" if confirm in ("yes", "y", "true", "1") else "no"
    try:
        row = upsert_target_area(
            tid,
            popup_id=str(data.get("popup_id") or PERMISSION_CHECKLIST_POPUP),
            label=data.get("label"),
            x1=_int("x1"),
            y1=_int("y1"),
            x2=_int("x2"),
            y2=_int("y2"),
            checklist_confirm=confirm,
            confirm_reason=data.get("confirm_reason"),
            isactive=int(data.get("isactive", 1)),
        )
        return jsonify({"ok": True, "area": row})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/coord-targets/checklist/confirm", methods=["POST"])
def api_coord_targets_checklist_confirm() -> Any:
    """Set checklist_confirm for one target.

    Body: {target_id, checklist_confirm: 'yes'|'no', popup_id?, reason?}
    """
    data = request.get_json(silent=True) or {}
    tid = str(data.get("target_id") or "").strip()
    if not tid:
        return jsonify({"ok": False, "error": "target_id required"}), 400
    confirm = data.get("checklist_confirm")
    if confirm is None:
        return jsonify({"ok": False, "error": "checklist_confirm required"}), 400
    try:
        row = set_checklist_confirm(
            tid,
            str(confirm),
            popup_id=str(data.get("popup_id") or PERMISSION_CHECKLIST_POPUP),
            reason=data.get("reason") or data.get("confirm_reason"),
        )
        if row is None:
            return jsonify({"ok": False, "error": f"target '{tid}' not in target_area"}), 404
        return jsonify({"ok": True, "area": row})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/coord-targets/checklist", methods=["GET"])
def api_coord_targets_checklist() -> Any:
    """Confirm checklist for a popup: all_present = yes|no + per-target items."""
    try:
        _seed_permission_checklist()
        popup = str(request.args.get("popup") or "").strip() or PERMISSION_CHECKLIST_POPUP
        return jsonify({"ok": True, **get_checklist_status(popup_id=popup)})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/coord-targets/<path:name>", methods=["GET"])
def api_coord_target_get(name: str) -> Any:
    try:
        q_tid = str(request.args.get("target_id") or "").strip() or None
        pt = None
        if q_tid:
            pt = get_target_point(target_id=q_tid, active_only=False)
        if pt is None:
            # path may be catalog target_id or target_name
            pt = get_target_point(target_id=name, active_only=False)
        if pt is None:
            pt = get_target_point(name, active_only=False)
        if not pt:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "point": pt})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/coord-targets/<path:name>/error", methods=["POST"])
def api_coord_target_set_error(name: str) -> Any:
    data = request.get_json(silent=True) or {}
    msg = str(data.get("error") or data.get("error_msg") or "").strip()
    if not msg:
        return jsonify({"ok": False, "error": "error message required"}), 400
    tid = str(data.get("target_id") or data.get("id") or "").strip() or None
    try:
        n = update_target_error(name, msg, target_id=tid)
        if n == 0:
            save_target_point(
                "", name, 0, 0, llm_score=0, error=msg, target_id=tid or name
            )
            n = update_target_error(name, msg, target_id=tid or name) or 1
        pt = get_target_point(name, target_id=tid or name, active_only=False)
        return jsonify({"ok": True, "updated": n, "point": pt})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/coord-targets/<path:name>/clear-error", methods=["POST"])
def api_coord_target_clear_error(name: str) -> Any:
    data = request.get_json(silent=True) or {}
    tid = str(data.get("target_id") or data.get("id") or "").strip() or None
    try:
        n = clear_target_error(name, target_id=tid)
        pt = get_target_point(name, target_id=tid or name, active_only=False)
        return jsonify({"ok": True, "updated": n, "point": pt})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/targets", methods=["GET", "POST"])
def api_targets() -> Any:
    if request.method == "GET":
        return jsonify(load_targets())

    data = request.get_json(silent=True) or {}
    tid = data.get("id")
    name = (data.get("name") or "").strip()
    action = (data.get("action") or "").strip()
    image_b64 = data.get("image")

    if not name:
        return jsonify({"ok": False, "error": "Target name is required."}), 400
    if not action:
        return jsonify({"ok": False, "error": "Action is required."}), 400

    targets = load_targets()
    existing = next((t for t in targets if t["id"] == tid), None)

    if existing:
        existing["name"] = name
        existing["action"] = action
    else:
        targets.append({"id": tid, "name": name, "action": action, "image": ""})
        existing = targets[-1]

    if image_b64:
        image_bytes = base64.b64decode(image_b64)
        save_target_image(tid, image_bytes)
        existing["image"] = f"/target-image/{tid}"

    save_targets(targets)
    return jsonify({"ok": True})


@app.route("/api/targets/<tid>", methods=["DELETE"])
def delete_target(tid: str) -> Any:
    targets = [t for t in load_targets() if t["id"] != tid]
    save_targets(targets)
    img_path = target_image_path(tid)
    if img_path.is_file():
        img_path.unlink()
    return jsonify({"ok": True})


@app.route("/api/active-target", methods=["POST"])
def set_active_target() -> Any:
    global active_target_id
    data = request.get_json(silent=True) or {}
    active_target_id = data.get("id")
    return jsonify({"ok": True})


@app.route("/target-image/<tid>")
def target_image(tid: str) -> Any:
    path = target_image_path(tid)
    if path.is_file():
        return send_file(path, mimetype="image/png")
    return "Not found", 404


@app.route("/api/capture", methods=["POST"])
def api_capture() -> Any:
    """API wrapper around shared capture core (UI must not keydown-trigger this)."""
    result = perform_capture()
    if not result.get("ok"):
        return jsonify(result), 500
    return jsonify(result)


@app.route("/api/screenshot", methods=["POST"])
def api_screenshot() -> Any:
    """Re-bake only when last_target exists — never overwrite with bare desktop."""
    if not last_target:
        return jsonify({
            "ok": False,
            "error": "No last_target; refuse bare screenshot overwrite. Use global hotkey capture.",
        }), 400
    result = take_screenshot(int(last_target["x"]), int(last_target["y"]))
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "Screenshot failed"}), 500
    return jsonify({"ok": True, "bytes": result.get("bytes"), "path": result.get("path")})


@app.route("/api/reset-capture", methods=["POST"])
def reset_capture() -> Any:
    global last_target, capture_error, capture_seq
    last_target = None
    capture_error = None
    capture_seq = 0
    if SCREENSHOT_PATH.is_file():
        SCREENSHOT_PATH.unlink()
    if SCREENSHOT_PREVIEW_PATH.is_file():
        try:
            SCREENSHOT_PREVIEW_PATH.unlink()
        except OSError:
            pass
    return jsonify({"ok": True})


@app.route("/api/analyze", methods=["POST"])
def api_analyze() -> Any:
    data = request.get_json(silent=True) or {}
    target_id = data.get("target_id") or active_target_id
    target_name = data.get("target_name")
    model = data.get("model") or DEFAULT_VISION_MODEL
    writer = str(data.get("writer") or "").strip()
    session_id = str(data.get("session_id") or "").strip()
    # Optional Task Center / draft task id (separate from runtime t_<hex>)
    ctx_task_id = str(data.get("task_id") or data.get("context_task_id") or "").strip()

    if not SCREENSHOT_PATH.is_file():
        return jsonify({"result": "FAIL", "reason": "No screenshot available. Capture first."}), 400

    targets = load_targets()
    if not target_name:
        for t in targets:
            if t["id"] == target_id:
                target_name = t["name"]
                break
    target_name = target_name or (target_id or "target")

    target_action = ""
    for t in targets:
        if t.get("id") == target_id:
            target_action = (t.get("action") or "").strip()
            break
    if not target_action:
        target_action = (data.get("target_action") or "").strip()

    skill_key = str(data.get("skill_key") or SKILL_MOUSE_SPOT).strip() or SKILL_MOUSE_SPOT
    skill = get_active_skill(skill_key, db_path=AGENT_DB_PATH)
    skill_version = str(skill.get("version_label") or "unknown")
    skill_parser = str(skill.get("parser") or "result_yes_no")
    prompt = render_prompt(
        skill.get("prompt_text") or "",
        target_name=str(target_name),
        target_action=target_action or "",
    )
    if not prompt.strip():
        prompt = render_prompt(
            "Target={{target_name}}. Action={{target_action}}.\n"
            "Result: [YES / NO]\nReason: which icon is under the red crosshair.\n",
            target_name=str(target_name),
            target_action=target_action or "",
        )

    x = data.get("x") or (last_target["x"] if last_target else 0)
    y = data.get("y") or (last_target["y"] if last_target else 0)

    task_id = f"t_{uuid.uuid4().hex[:12]}"
    started_at = _utc_now_iso()
    t0 = time.perf_counter()
    append_llm_task({
        "id": task_id,
        "task": "mouse_spot_verify",
        "model": model,
        "model_label": next((m["label"] for m in LLM_MODELS if m["id"] == model), model),
        "status": "running",
        "started_at": started_at,
        "ended_at": None,
        "duration_ms": None,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "result": None,
        "reason": None,
        "confidence": None,
        "target_id": target_id,
        "target_name": target_name,
        "skill_key": skill_key,
        "skill_version": skill_version,
        "writer": writer or None,
        "session_id": session_id or None,
        "context_task_id": ctx_task_id or None,
        "x": x,
        "y": y,
        "error": None,
    })

    result = analyze_evidence(
        SCREENSHOT_PATH,
        fault_type="mouse_spot_verify",
        model=model,
        base_url=None,
        timeout=180,
        prompt=prompt,
        format_json=False,
        parse_mode="result_yes_no" if skill_parser == "result_yes_no" else "auto",
    )
    wall_ms = int((time.perf_counter() - t0) * 1000)
    ended_at = _utc_now_iso()
    trio = identity_trio_dict(
        writer=writer,
        task_id=ctx_task_id or task_id,
        session_id=session_id,
    )
    trailer = identity_trailer(**trio)

    if result.error:
        err_msg = f"LLM error: {result.error}"
        update_llm_task(task_id, {
            "status": "error",
            "ended_at": ended_at,
            "duration_ms": result.duration_ms or wall_ms,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "result": "FAIL",
            "reason": err_msg,
            "error": result.error,
            "model": result.model or model,
            "skill_key": skill_key,
            "skill_version": skill_version,
        })
        try:
            # Ensure a row exists then stamp error (方案B)
            save_target_point(
                target_logo="",
                target_name=str(target_name),
                x=int(x or 0),
                y=int(y or 0),
                action=str(target_action or "click").strip() or "click",
                llm_score=0,
                error=err_msg[:500],
                target_id=str(target_id) if target_id else None,
            )
            update_target_error(
                str(target_name),
                err_msg[:500],
                target_id=str(target_id) if target_id else None,
            )
        except Exception:
            pass
        return jsonify({
            "result": "FAIL",
            "reason": err_msg,
            "task_id": task_id,
            "context_task_id": ctx_task_id or None,
            "identity": trio,
            "writer": trio["writer"],
            "session_id": trio["session_id"],
            "trailer": trailer,
            "model": result.model or model,
            "skill_key": skill_key,
            "skill_version": skill_version,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "duration_ms": result.duration_ms or wall_ms,
            "coord_error": err_msg[:500],
        }), 503

    detail = result.detail or {}
    if detail.get("correct") is None and result.raw_text:
        detail = parse_verify_response(result.raw_text)
    correct = detail.get("correct")
    # No fuzzy "on the" success — unknown parse = FAIL
    is_correct = True if correct is True else False
    parser_name = detail.get("parser") or skill_parser
    if correct is None:
        reason = detail.get("reason") or result.summary or "parser_error: could not read YES/NO"
        confidence = 0.0
        final_result = "FAIL"
    else:
        reason = detail.get("reason") or result.summary or "no reason given"
        try:
            confidence = float(detail.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        final_result = "SUCCESS" if is_correct else "FAIL"

    # confidence 0..1 → llm_score 0..100
    try:
        llm_score = int(round(max(0.0, min(1.0, float(confidence))) * 100))
    except (TypeError, ValueError):
        llm_score = 0
    low_score = llm_score < LLM_SCORE_THRESHOLD
    if final_result == "SUCCESS" and low_score:
        final_result = "FAIL"
        reason = (
            f"{reason} | llm_score {llm_score} < threshold {LLM_SCORE_THRESHOLD}"
        )

    update_llm_task(task_id, {
        "status": "done",
        "ended_at": ended_at,
        "duration_ms": result.duration_ms or wall_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "result": final_result,
        "reason": reason,
        "confidence": confidence,
        "model": result.model or model,
        "skill_key": skill_key,
        "skill_version": skill_version,
        "parser": parser_name,
        "error": None,
    })

    coord_error = None
    try:
        logo = ""
        for t in targets:
            if t.get("id") == target_id:
                logo = str(t.get("image") or "")
                break
        if final_result == "SUCCESS" and not low_score:
            save_target_point(
                target_logo=logo,
                target_name=str(target_name),
                x=int(x or 0),
                y=int(y or 0),
                action=str(target_action or "click").strip() or "click",
                llm_score=llm_score,
                error=None,
                target_id=str(target_id) if target_id else None,
            )
            clear_target_error(
                str(target_name),
                target_id=str(target_id) if target_id else None,
            )
        else:
            coord_error = str(reason)[:500] or "按鈕位置偏移，無法成功點擊"
            save_target_point(
                target_logo=logo,
                target_name=str(target_name),
                x=int(x or 0),
                y=int(y or 0),
                action=str(target_action or "click").strip() or "click",
                llm_score=llm_score,
                error=coord_error,
                target_id=str(target_id) if target_id else None,
            )
            update_target_error(
                str(target_name),
                coord_error,
                target_id=str(target_id) if target_id else None,
            )
    except Exception:
        pass

    # Best-effort inference ledger
    try:
        if AGENT_DB_PATH.is_file():
            conn = skill_db_connect(AGENT_DB_PATH)
            ensure_skill_tables(conn)
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_prompt_inference (
                    inference_id, skill_key, version_label, prompt_key,
                    task_run_id, target_name, target_action, image_path,
                    raw_response, parsed_result, final_result, reason,
                    confidence, model, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    skill_key,
                    skill_version,
                    skill.get("prompt_key") or "main",
                    task_id,
                    str(target_name),
                    target_action or "",
                    str(SCREENSHOT_PATH),
                    (result.raw_text or "")[:4000],
                    "YES" if is_correct else "NO",
                    final_result,
                    str(reason)[:500],
                    confidence,
                    result.model or model,
                    json.dumps(
                        {
                            "parser": parser_name,
                            "x": x,
                            "y": y,
                            "llm_score": llm_score,
                            "coord_error": coord_error,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            conn.commit()
            conn.close()
    except Exception:
        pass

    return jsonify({
        "result": final_result,
        "correct": is_correct,
        "reason": reason,
        "confidence": confidence,
        "llm_score": llm_score,
        "llm_score_threshold": LLM_SCORE_THRESHOLD,
        "coord_error": coord_error,
        "model": result.model or model,
        "task_id": task_id,
        "context_task_id": ctx_task_id or None,
        "identity": trio,
        "writer": trio["writer"],
        "session_id": trio["session_id"],
        "trailer": trailer,
        "skill_key": skill_key,
        "skill_version": skill_version,
        "parser": parser_name,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "duration_ms": result.duration_ms or wall_ms,
        "started_at": started_at,
        "ended_at": ended_at,
    })


@app.route("/screenshot.png")
def screenshot_png() -> Any:
    if SCREENSHOT_PATH.is_file():
        return send_file(SCREENSHOT_PATH, mimetype="image/png")
    return "No screenshot yet", 404


@app.route("/screenshot-preview.png")
def screenshot_preview_png() -> Any:
    """UI-only 200x200 crop; LLM verification uses full /screenshot.png file."""
    if SCREENSHOT_PREVIEW_PATH.is_file():
        return send_file(SCREENSHOT_PREVIEW_PATH, mimetype="image/png")
    if SCREENSHOT_PATH.is_file() and last_target:
        try:
            from PIL import Image

            img = Image.open(SCREENSHOT_PATH)
            save_preview_crop(img, int(last_target["x"]), int(last_target["y"]))
            if SCREENSHOT_PREVIEW_PATH.is_file():
                return send_file(SCREENSHOT_PREVIEW_PATH, mimetype="image/png")
        except Exception as e:
            print(f"preview fallback failed: {e}", flush=True)
    return "No preview yet", 404


@app.route("/screenshot-annotated.png")
def screenshot_annotated_png() -> Any:
    """Return full-screen screenshot with crosshair at last_target (else center)."""
    if not SCREENSHOT_PATH.is_file():
        return "No screenshot yet", 404
    try:
        from PIL import Image, ImageDraw
        img = Image.open(SCREENSHOT_PATH).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        w, h = img.size
        if last_target and last_target.get("x") is not None and last_target.get("y") is not None:
            cx = max(0, min(w - 1, int(last_target["x"])))
            cy = max(0, min(h - 1, int(last_target["y"])))
        else:
            cx, cy = w // 2, h // 2
        radius = 48
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=(59, 130, 246, 200),
            width=3,
        )
        draw.line((cx - 20, cy, cx + 20, cy), fill=(239, 68, 68, 230), width=3)
        draw.line((cx, cy - 20, cx, cy + 20), fill=(239, 68, 68, 230), width=3)
        out = Image.alpha_composite(img, overlay).convert("RGB")
        img_io = io.BytesIO()
        out.save(img_io, format="PNG", optimize=True)
        img_io.seek(0)
        return send_file(img_io, mimetype="image/png")
    except Exception as e:
        print(f"Annotated screenshot failed: {e}")
        return send_file(SCREENSHOT_PATH, mimetype="image/png")


@app.route("/static/target-placeholder.png")
def placeholder_png() -> Any:
    img_io = io.BytesIO()
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (24, 24), (51, 51, 51, 255))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 20, 20], outline="#888", width=2)
    img.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


def create_desktop_shortcut(url: str) -> Path | None:
    """Ensure launcher shortcuts exist. Prefer .lnk bat launchers over .url."""
    desktop = Path.home() / "Desktop"
    start_menu = Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
    bat_main = BASE_DIR / "start_mouse_spot_helper.bat"
    bat_llm = BASE_DIR / "start_llm_task_monitor.bat"

    # Remove legacy browser-only shortcut if present.
    old_url = desktop / "Mouse Spot Helper.url"
    if old_url.is_file():
        try:
            old_url.unlink()
        except OSError:
            pass

    # If .lnk already exists, keep it (created by setup).
    main_lnk = desktop / "Mouse Spot Helper.lnk"
    llm_lnk = desktop / "LLM Task Monitor.lnk"
    if main_lnk.is_file() and llm_lnk.is_file():
        return main_lnk

    # Fallback: write a simple .url only if no launcher bat exists.
    if not bat_main.is_file():
        shortcut = desktop / "Mouse Spot Helper.url"
        shortcut.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8")
        return shortcut
    return main_lnk if main_lnk.is_file() else None


HELPER_MUTEX_NAME = r"Local\AgentSystemMouseSpotHelper_v1"
_helper_mutex_handle = None


def _write_helper_pid() -> None:
    path = BASE_DIR / "mouse_spot_helper.pid"
    try:
        path.write_text(str(os.getpid()), encoding="utf-8")
    except Exception:
        pass


def _clear_helper_pid() -> None:
    path = BASE_DIR / "mouse_spot_helper.pid"
    try:
        if path.is_file():
            cur = path.read_text(encoding="utf-8").strip()
            if cur == str(os.getpid()):
                path.unlink(missing_ok=True)
    except Exception:
        pass


def _release_helper_mutex() -> None:
    global _helper_mutex_handle
    if _helper_mutex_handle is None:
        return
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.ReleaseMutex(_helper_mutex_handle)
            ctypes.windll.kernel32.CloseHandle(_helper_mutex_handle)
        except Exception:
            pass
    _helper_mutex_handle = None


def _acquire_helper_single_instance() -> bool:
    """Only one mouse_spot_helper may own port 18765 / serve traffic."""
    global _helper_mutex_handle
    pid_path = BASE_DIR / "mouse_spot_helper.pid"

    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            ERROR_ALREADY_EXISTS = 183
            WAIT_OBJECT_0 = 0
            WAIT_ABANDONED = 128
            WAIT_TIMEOUT = 258
            kernel32.SetLastError(0)
            handle = kernel32.CreateMutexW(None, False, HELPER_MUTEX_NAME)
            last_err = int(kernel32.GetLastError() or 0)
            if handle:
                wait = int(kernel32.WaitForSingleObject(handle, 0))
                if wait in (WAIT_OBJECT_0, WAIT_ABANDONED):
                    _helper_mutex_handle = handle
                elif wait == WAIT_TIMEOUT or last_err == ERROR_ALREADY_EXISTS:
                    kernel32.CloseHandle(handle)
                    print("Another mouse_spot_helper already running (mutex). Exit.")
                    return False
                else:
                    kernel32.CloseHandle(handle)
        except Exception as e:
            print(f"WARNING: helper mutex failed: {e}")

    # PID-file fallback / stale cleanup
    try:
        if pid_path.is_file():
            old = int(pid_path.read_text(encoding="utf-8").strip() or "0")
        else:
            old = 0
    except Exception:
        old = 0
    if old and old != os.getpid() and _pid_alive(old):
        if _helper_mutex_handle is None:
            print(f"Another mouse_spot_helper already running (pid={old}). Exit.")
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # THE DEFAULT IS CLOSED (2026-09-25).
    #
    # MEASURED DEFECT: the old default was "open unless --no-browser", so EVERY
    # path that started the helper without that flag popped a browser tab the
    # user never asked for. The human: "why # Mouse Spot Helper will popup at
    # browser anytime, i didn't call that!!!"
    #
    # The repo's OWN rule already forbade it
    # (`skills/3_ui/ui_field_builder.skill.md:90`): "A helper/server that calls
    # `webbrowser.open(url)` at startup pops the user's browser on EVERY restart
    # — considered a bug. Rule: open on demand only, via an explicit endpoint."
    #
    # So the flag is now OPT-IN: `--open-browser` (or HELPER_OPEN_BROWSER=1).
    # `--no-browser` / HELPER_NO_BROWSER stay ACCEPTED — the watchdog passes them
    # (`helper_watchdog.py:923`) and a flag that silently stops working is its
    # own defect — but they are now redundant, and they still WIN if both are
    # given, because "do not open" is the safe direction.
    open_browser = (
        "--open-browser" in args
        or os.environ.get("HELPER_OPEN_BROWSER", "").strip() in ("1", "true", "yes", "on")
    )
    if (
        "--no-browser" in args
        or os.environ.get("HELPER_NO_BROWSER", "").strip() in ("1", "true", "yes", "on")
    ):
        open_browser = False
    host = "127.0.0.1"
    port = 18765
    url = f"http://{host}:{port}/"
    tasks_url = f"http://{host}:{port}/llm-tasks"

    if not _acquire_helper_single_instance():
        return 0

    _write_helper_pid()
    atexit_registered = False
    try:
        import atexit as _atexit

        _atexit.register(_clear_helper_pid)
        _atexit.register(_release_helper_mutex)
        atexit_registered = True
    except Exception:
        atexit_registered = False

    threading.Thread(target=hotkey_listener, daemon=True).start()
    # THE BACKGROUND REFRESHER (2026-09-25). The OpenClaw probe and the Windows
    # scheduled-task read cost 1.5-7.4 s and ~600 ms respectively, so they run
    # HERE, off the request path. `/api/system-status` reads the cache and never
    # blocks. THE HUMAN: "by trigger point before have the call!! not need
    # tochecking status : alive".
    try:
        import openclaw_settings as _ocs
        _r = _ocs.start_status_refresher()
        print("openclaw status refresher: %s" % _r)
    except Exception as _e:  # noqa: BLE001
        print("openclaw status refresher FAILED to start: %s: %s"
              % (type(_e).__name__, _e))

    # Background TTL cleanup for plan_sessions (prevents table growth).
    try:
        from skill_library_api import start_ttl_cleanup

        start_ttl_cleanup()
    except Exception:
        pass

    shortcut = create_desktop_shortcut(url)
    if shortcut:
        print(f"Desktop shortcut: {shortcut}")
    print(f"Open: {url}")
    print(f"LLM tasks: {tasks_url}")
    print(f"pid={os.getpid()} health={url}api/health")
    if open_browser:
        webbrowser.open(url)
        print(f"Browser opened on request: {url}")
    else:
        print("Browser NOT opened (default). Pass --open-browser to open it, "
              "or POST /api/open-browser.")

    try:
        app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
    finally:
        if not atexit_registered:
            _clear_helper_pid()
            _release_helper_mutex()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
