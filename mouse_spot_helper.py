"""Mouse Spot Helper — web UI for live mouse coordinates and screenshots.

Usage:
    c:/projects/agent_system/.venv/Scripts/python.exe mouse_spot_helper.py

Then open the URL shown in the terminal. Press Ctrl+Shift+M to capture target.
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
    get_skill_task_record,
    improve_prompt_to_draft,
    list_skill_cases,
    list_skill_task_records,
    seed_gold_cases,
    seed_phase5_all,
    seed_task_center_skill_root,
    skill_tc_item_lines,
    test_gold_suite,
)

BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_PATH = BASE_DIR / "mouse_spot_screenshot.png"
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
active_target_id: str | None = None


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
    base = (os.environ.get("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE).rstrip("/")
    model = os.environ.get("OLLAMA_VISION_MODEL") or DEFAULT_VISION_MODEL
    url = f"{base}/api/tags"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        names: list[str] = []
        for m in body.get("models") or []:
            if isinstance(m, dict) and m.get("name"):
                names.append(str(m["name"]))
        model_root = str(model).split(":", 1)[0]
        has_vision = any(model == n or n.startswith(model_root) for n in names)
        return {
            "ok": True,
            "base_url": base,
            "model": model,
            "model_present": bool(has_vision or model in names),
            "models": names[:20],
            "error": None,
        }
    except Exception as e:
        return {
            "ok": False,
            "base_url": base,
            "model": model,
            "model_present": False,
            "models": [],
            "error": f"{type(e).__name__}: {e}",
        }


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except Exception:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    return True


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
    }


def helper_status_payload() -> dict[str, Any]:
    return {
        "ok": True,
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


def ensure_prompt_id_footer(
    text: str,
    *,
    writer: str,
    task_id: str,
    session_id: str,
) -> str:
    """Append canonical ID block if any required marker is missing."""
    check = prompt_has_ids(text, writer=writer, task_id=task_id, session_id=session_id)
    if check["ok"]:
        # Still normalize values if markers exist but differ — keep text, ensure footer values match.
        found = check["found"]
        if (
            (found.get("writer") or writer) == writer
            and str(found.get("task_id") or task_id) == str(task_id)
            and (found.get("session_id") or session_id) == session_id
        ):
            return text.rstrip() + "\n"
    footer = (
        "\n\n---\n"
        f"writer: {writer}\n"
        f"task_id: {task_id}\n"
        f"session_id: {session_id}\n"
    )
    base = (text or "").rstrip()
    # Strip an existing trailing --- id block to avoid duplicates.
    base = re.sub(
        r"(?s)\n---\s*\n(?:writer|task_id|session_id)\s*:.*\Z",
        "",
        base,
    ).rstrip()
    return base + footer


def context_completeness(
    *,
    task_id: str | int | None,
    writer: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    fields = {
        "task_id": bool(str(task_id or "").strip()),
        "writer": bool(str(writer or "").strip()),
        "session_id": bool(str(session_id or "").strip()),
    }
    missing = [k for k, ok in fields.items() if not ok]
    return {"ok": not missing, "fields": fields, "missing": missing}



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
    <div class="hint">Global shortcut: <b>Ctrl+Shift+M</b> to capture target</div>
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
            max-width: 500px;
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
        .target-item input[type="text"] {
            width: 100%;
            padding: 0.5em;
            border-radius: 4px;
            border: 1px solid #444;
            background: #2d2d2d;
            color: #d4d4d4;
        }
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
        .add-row {
            display: flex;
            gap: 0.5em;
            margin-top: 1em;
        }
        .add-row input { flex: 1; }
    </style>
</head>
<body>
    <h1>Mouse Spot Helper Settings</h1>
    <div class="panel" id="panel"></div>

    <script>
        async function load() {
            const res = await fetch('/api/targets');
            const targets = await res.json();
            const panel = document.getElementById('panel');
            panel.innerHTML = '';

            targets.forEach(t => {
                const div = document.createElement('div');
                div.className = 'target-item';
                const safeName = String(t.name || '').replace(/"/g, '&quot;');
                const safeAction = String(t.action || '').replace(/"/g, '&quot;');
                div.innerHTML = `
                    <label>Name <span style="color:#f87171">*</span></label>
                    <input type="text" id="name-${t.id}" value="${safeName}" placeholder="Target name" required>
                    <label>Action <span style="color:#f87171">*</span></label>
                    <input type="text" id="action-${t.id}" value="${safeAction}" placeholder="What should happen (e.g. Open app)" required>
                    <label>Image</label>
                    <input type="file" id="file-${t.id}" accept="image/*,.webp,.png,.jpg,.jpeg,.gif,.bmp">
                    ${t.image ? `<img src="/target-image/${t.id}?t=${Date.now()}" alt="${safeName}">` : ''}
                    <div class="actions">
                        <button onclick="saveTarget('${t.id}')">Save</button>
                        <button class="danger" onclick="deleteTarget('${t.id}')">Delete</button>
                    </div>
                `;
                panel.appendChild(div);
            });

            const addDiv = document.createElement('div');
            addDiv.className = 'target-item';
            addDiv.innerHTML = `
                <label>Name <span style="color:#f87171">*</span></label>
                <input type="text" id="newName" placeholder="New target name" required>
                <label>Action <span style="color:#f87171">*</span></label>
                <input type="text" id="newAction" placeholder="What should happen (e.g. Open app)" required>
                <div class="actions">
                    <button onclick="addTarget()">+ Add</button>
                </div>
            `;
            panel.appendChild(addDiv);
        }

        async function saveTarget(id) {
            const name = document.getElementById(`name-${id}`).value.trim();
            const action = document.getElementById(`action-${id}`).value.trim();
            if (!name) {
                alert('Target name is required.');
                return;
            }
            if (!action) {
                alert('Action is required.');
                return;
            }
            const fileInput = document.getElementById(`file-${id}`);
            const body = { id, name, action };
            if (fileInput.files && fileInput.files[0]) {
                const reader = new FileReader();
                reader.onload = async () => {
                    body.image = reader.result.split(',')[1];
                    const res = await fetch('/api/targets', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });
                    if (!res.ok) {
                        const err = await res.json().catch(() => ({}));
                        alert(err.error || 'Save failed');
                        return;
                    }
                    load();
                };
                reader.readAsDataURL(fileInput.files[0]);
            } else {
                const res = await fetch('/api/targets', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    alert(err.error || 'Save failed');
                    return;
                }
                load();
            }
        }

        async function deleteTarget(id) {
            if (!confirm('Delete this target?')) return;
            await fetch(`/api/targets/${id}`, { method: 'DELETE' });
            load();
        }

        async function addTarget() {
            const name = document.getElementById('newName').value.trim();
            const action = document.getElementById('newAction').value.trim();
            if (!name) {
                alert('Target name is required.');
                return;
            }
            if (!action) {
                alert('Action is required.');
                return;
            }
            const id = 't_' + Date.now();
            const res = await fetch('/api/targets', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, name, action, image: '' })
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                alert(err.error || 'Add failed');
                return;
            }
            load();
        }

        load();
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
        <div class="status" id="status">Press Ctrl+Shift+M to capture target</div>
        <div class="nav">
            <button class="secondary" id="backBtn">← Back</button>
        </div>
    </div>

    <script>
        let target = null;

        async function load() {
            const res = await fetch('/api/state');
            const state = await res.json();
            const targetsRes = await fetch('/api/targets');
            const targets = await targetsRes.json();
            target = targets.find(t => t.id === state.active_id) || targets[0] || { id: '', name: 'Target', image: '' };
            document.getElementById('targetName').textContent = target.name;
            document.getElementById('targetLogo').src = target.image ? `/target-image/${target.id}?t=${Date.now()}` : '/static/target-placeholder.png';
        }

        async function update() {
            const res = await fetch('/api/state');
            const data = await res.json();
            document.getElementById('x').textContent = data.x;
            document.getElementById('y').textContent = data.y;
            if (data.target) {
                document.getElementById('cx').textContent = data.target.x;
                document.getElementById('cy').textContent = data.target.y;
            }
        }

        async function capture() {
            const statusEl = document.getElementById('status');
            statusEl.textContent = 'Capturing...';
            try {
                const res = await fetch('/api/capture', { method: 'POST' });
                const data = await res.json().catch(() => ({}));
                if (!res.ok || !data.ok) {
                    throw new Error(data.error || 'capture failed');
                }
                window.location.href = `/step3?x=${data.x}&y=${data.y}`;
            } catch (e) {
                statusEl.className = 'status';
                statusEl.textContent = 'Capture failed: ' + (e && e.message ? e.message : e);
            }
        }

        async function analyze() {
            const statusEl = document.getElementById('status');
            capture = { x: document.getElementById('cx').textContent, y: document.getElementById('cy').textContent };
            if (capture.x === '-') {
                alert('Capture a target first.');
                return;
            }
            statusEl.textContent = 'Analyzing with LLM...';
            try {
                const res = await fetch('/api/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                                              target_id: target.id,
                        target_name: target.name,
                        x: parseInt(capture.x, 10),
                        y: parseInt(capture.y, 10)
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.reason || 'analyze failed');
                statusEl.className = 'status ' + (data.result === 'SUCCESS' ? 'success' : 'fail');
                statusEl.textContent = `LLM: ${data.result} — ${data.reason} (confidence: ${(data.confidence * 100).toFixed(0)}%)`;
            } catch (e) {
                statusEl.className = 'status';
                statusEl.textContent = `LLM: FAIL — ${e.message}`;
            }
        }

        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'm') {
                e.preventDefault();
                capture();
            }
        });

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
            img.src = `/screenshot.png?t=${Date.now()}`;
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

        <div class="screenshot-wrap" title="Blue circle shows target zone; red crosshair shows captured point">
            <img id="screenshot" src="" alt="annotated screenshot preview">
        </div>
        <div class="caption">Blue circle = target zone &middot; Red crosshair = captured point</div>

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
                const helperPill = `<span class="pill ok">Helper ON</span>`;
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
                setSkillMsg(String(e.meeedAllBtn').addEventListener('click', async () => {
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

        document.getElementById('skillSssage || e), true);
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
    }


def take_screenshot(x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """Capture a 200x200 crop around (x,y) or last_target. Returns status dict."""
    try:
        import pyautogui
        from PIL import ImageDraw

        tx = x if x is not None else (last_target["x"] if last_target else None)
        ty = y if y is not None else (last_target["y"] if last_target else None)
        if tx is None or ty is None:
            img = pyautogui.screenshot()
            img.save(SCREENSHOT_PATH)
            return {
                "ok": True,
                "path": str(SCREENSHOT_PATH),
                "bytes": SCREENSHOT_PATH.stat().st_size,
                "full": True,
            }

        size = 200
        left = max(0, int(tx) - size // 2)
        top = max(0, int(ty) - size // 2)
        img = pyautogui.screenshot(region=(left, top, size, size))
        draw = ImageDraw.Draw(img)
        cx, cy = size // 2, size // 2
        draw.line((cx - 15, cy, cx + 15, cy), fill="red", width=2)
        draw.line((cx, cy - 15, cx, cy + 15), fill="red", width=2)
        img.save(SCREENSHOT_PATH)
        if not SCREENSHOT_PATH.is_file():
            return {"ok": False, "error": f"Screenshot not written to {SCREENSHOT_PATH}"}
        return {
            "ok": True,
            "path": str(SCREENSHOT_PATH),
            "bytes": SCREENSHOT_PATH.stat().st_size,
            "x": int(tx),
            "y": int(ty),
            "full": False,
        }
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"Screenshot failed: {msg}", flush=True)
        return {"ok": False, "error": msg}


def hotkey_listener() -> None:
    global last_target

    def on_activate() -> None:
        global last_target
        x, y = mouse.position
        last_target = {"x": int(x), "y": int(y)}
        print(f"Target captured: X={x}, Y={y}", flush=True)
        result = take_screenshot(int(x), int(y))
        if not result.get("ok"):
            print(f"Hotkey screenshot failed: {result.get('error')}", flush=True)
        else:
            print(f"Screenshot saved: {result.get('bytes')} bytes", flush=True)

    with keyboard.GlobalHotKeys({"<ctrl>+<shift>+m": on_activate}) as h:
        h.join()


@app.route("/")
def index() -> str:
    return render_template_string(HTML)


@app.route("/settings")
def settings() -> str:
    return render_template_string(SETTINGS_HTML)


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
    index = LLM_MONITOR_DIST / "index.html"
    if index.is_file():
        return send_file(index)
    return render_template_string(LLM_TASKS_HTML)


@app.route("/llm-tasks")
@app.route("/llm-tasks/")
def llm_tasks_page():
    return _llm_monitor_spa_index()


@app.route("/llm-tasks/assets/<path:asset_path>")
def llm_tasks_assets(asset_path: str):
    assets = LLM_MONITOR_DIST / "assets"
    if not assets.is_dir():
        return jsonify({"ok": False, "error": "SPA assets not built"}), 404
    return send_from_directory(assets, asset_path)


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

    ctx = context_completeness(task_id=task_id, writer=writer, session_id=session_id)
    if not ctx["ok"]:
        return jsonify({
            "ok": False,
            "error": "missing required context: " + ", ".join(ctx["missing"]),
            "missing": ctx["missing"],
            "context": ctx,
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

    system = (
        "You improve user prompts for coding agents. "
        "Rewrite for clarity and actionability. "
        "You MUST keep or add these exact identity lines in the improved prompt:\n"
        f"writer: {writer}\n"
        f"task_id: {task_id}\n"
        f"session_id: {session_id}\n"
        "Return ONLY the improved prompt text. No markdown fences. No commentary."
    )
    user_msg = (
        "Improve the following prompt. Preserve intent. "
        "Ensure the three identity lines above appear verbatim.\n\n"
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
        }), 502

    improved = (result.raw_text or "").strip()
    # Strip accidental markdown fences.
    if improved.startswith("```"):
        improved = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", improved)
        improved = re.sub(r"\n?```$", "", improved).strip()
    improved = ensure_prompt_id_footer(
        improved, writer=writer, task_id=task_id, session_id=session_id
    )
    ids_check = prompt_has_ids(
        improved, writer=writer, task_id=task_id, session_id=session_id
    )
    update_llm_task(run_id, {
        "status": "done",
        "ended_at": ended,
        "duration_ms": result.duration_ms or 0,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "result": "SUCCESS" if ids_check["ok"] else "FAIL",
        "reason": "improved" if ids_check["ok"] else ("missing ids: " + ",".join(ids_check["missing"])),
        "error": None,
    })
    return jsonify({
        "ok": True,
        "improved_prompt": improved,
        "prompt_ids_ok": ids_check["ok"],
        "prompt_ids": ids_check,
        "writer": writer,
        "task_id": task_id,
        "session_id": session_id,
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

    if not prompt.strip():
        return jsonify({"ok": False, "error": "prompt is required"}), 400

    ctx = context_completeness(task_id=task_id, writer=writer, session_id=session_id)
    prompt_ids = prompt_has_ids(
        prompt,
        writer=writer,
        task_id=task_id,
        session_id=session_id,
    )

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
        "You review prompts for coding agents. Return ONLY JSON with keys: "
        "notes (string), score (number 0..1), suggestions (array of short strings). "
        "Focus on clarity, missing constraints, ambiguity, and whether identity "
        "fields writer/task_id/session_id are present and consistent."
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

    overall_ok = bool(prompt_ids.get("ok")) and bool(ctx.get("ok"))
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

    payload = {
        "ok": True,
        "run_id": run_id,
        "model": model,
        "context_ok": ctx["ok"],
        "context": ctx,
        "prompt_ids_ok": prompt_ids["ok"],
        "prompt_ids": prompt_ids,
        "missing": sorted(set(ctx["missing"] + prompt_ids["missing"])),
        "notes": notes,
        "score": score,
        "suggestions": suggestions,
        "llm_error": result.error,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "duration_ms": result.duration_ms,
        "writer": writer,
        "task_id": task_id,
        "session_id": session_id,
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


@app.route("/api/skills/seed-tasks", methods=["POST"])
def api_skills_seed_tasks() -> Any:
    """Seed Task Center mouse_spot_helper root 10 + 10.1–10.20."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        out = seed_task_center_skill_root(AGENT_DB_PATH)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/task-lines", methods=["GET"])
def api_skills_task_lines() -> Any:
    """Human-readable Task Center lines: 10.1 name … 10.20 name."""
    try:
        lines = skill_tc_item_lines(SKILL_ROOT_TASK_ID)
        return jsonify({
            "ok": True,
            "root": SKILL_ROOT_TASK_ID,
            "lines": lines,
            "count": len(lines),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/task-records", methods=["GET"])
def api_skills_task_records() -> Any:
    """Task ID Coding table rows: Date | Task ID | channel | module | task name | status."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        root = request.args.get("root") or SKILL_ROOT_TASK_ID
        out = list_skill_task_records(AGENT_DB_PATH, root=root)
        code = 200 if out.get("ok") else 500
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/task-records/<task_id>", methods=["GET"])
def api_skills_task_record_one(task_id: str) -> Any:
    """One Task ID Coding row + task_ssot dims (by label 10.3 or db id)."""
    try:
        if not AGENT_DB_PATH.is_file():
            return jsonify({"ok": False, "error": "agent.db missing"}), 500
        root = request.args.get("root") or SKILL_ROOT_TASK_ID
        out = get_skill_task_record(task_id, AGENT_DB_PATH, root=root)
        code = 200 if out.get("ok") else 404
        return jsonify(out), code
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills", methods=["GET"])
def api_skills_list() -> Any:
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
        for r in rows:
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
        return jsonify({"ok": True, "skills": items, "count": len(items)})
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


@app.route("/api/skills/<skill_key>/draft", methods=["POST"])
def api_skills_draft(skill_key: str) -> Any:
    data = request.get_json(silent=True) or {}
    prompt_text = str(data.get("prompt_text") or data.get("prompt") or "")
    version_label = str(data.get("version_label") or data.get("version") or "").strip()
    if not version_label:
        version_label = f"draft_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    if not prompt_text.strip():
        return jsonify({"ok": False, "error": "prompt_text required"}), 400
    activate = bool(data.get("activate"))
    status = "active" if activate else str(data.get("status") or "draft")
    task_id = data.get("task_id")
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
        return jsonify({"ok": True, "skill": row})
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
        out["llm_task_id"] = run_id
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


@app.route("/api/skills/<skill_key>/cases", methods=["GET"])
def api_skills_cases_list(skill_key: str) -> Any:
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
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
wd = watchdog_status_payload()
    return jsonify({
        "ok": True,
        "ready": bool(ollama.get("ok")),
        "identity": identity,
        "ollama": ollama,
        "helper": helper_status_payload(),
        "watchdog": wd,
    })


@app.route("/api/watchdog/status")
def api_watchdog_status() -> Any:
    return jsonify({"ok": True, "watchdog": watchdog_status_payload()})


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
    return send_file(path, mimetype="image/png"ry:
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
        status = 200 if out.get("ok") else 400
        return jsonify(out), status
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/skills/seed", methods=["POST"])
def api_skills_seed() -> Any:
    try:
        out = seed_default_skills(AGENT_DB_PATH)
        return jsonify(out)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/system-status")
def api_system_status() -> Any:
    identity = get_computer_identity()
    ollama = check_ollama_status()
    return jsonify({
        "ok": True,
        "ready": bool(ollama.get("ok")),
        "identity": identity,
        "ollama": ollama,
        "helper": helper_status_payload(),
    })


@app.route("/api/state")
def api_state() -> Any:
    return jsonify(get_state())


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
    global last_target
    x, y = mouse.position
    last_target = {"x": int(x), "y": int(y)}
    result = take_screenshot(int(x), int(y))
    if not result.get("ok"):
        return jsonify({
            "ok": False,
            "x": int(x),
            "y": int(y),
            "error": result.get("error") or "Screenshot failed",
        }), 500
    return jsonify({
        "ok": True,
        "x": int(x),
        "y": int(y),
        "bytes": result.get("bytes"),
        "path": result.get("path"),
    })


@app.route("/api/screenshot", methods=["POST"])
def api_screenshot() -> Any:
    result = take_screenshot()
    if not result.get("ok"):
        return jsonify({"ok": False, "error": result.get("error") or "Screenshot failed"}), 500
    return jsonify({"ok": True, "bytes": result.get("bytes"), "path": result.get("path")})


@app.route("/api/reset-capture", methods=["POST"])
def reset_capture() -> Any:
    global last_target
    last_target = None
    if SCREENSHOT_PATH.is_file():
        SCREENSHOT_PATH.unlink()
    return jsonify({"ok": True})


@app.route("/api/analyze", methods=["POST"])
def api_analyze() -> Any:
    data = request.get_json(silent=True) or {}
    target_id = data.get("target_id") or active_target_id
    target_name = data.get("target_name")
    model = data.get("model") or DEFAULT_VISION_MODEL

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

    if result.error:
        update_llm_task(task_id, {
            "status": "error",
            "ended_at": ended_at,
            "duration_ms": result.duration_ms or wall_ms,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "result": "FAIL",
            "reason": f"LLM error: {result.error}",
            "error": result.error,
            "model": result.model or model,
            "skill_key": skill_key,
            "skill_version": skill_version,
        })
        return jsonify({
            "result": "FAIL",
            "reason": f"LLM error: {result.error}",
            "task_id": task_id,
            "model": result.model or model,
            "skill_key": skill_key,
            "skill_version": skill_version,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "duration_ms": result.duration_ms or wall_ms,
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
                    json.dumps({"parser": parser_name, "x": x, "y": y}, ensure_ascii=False),
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
        "model": result.model or model,
        "task_id": task_id,
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


@app.route("/screenshot-annotated.png")
def screenshot_annotated_png() -> Any:
    """Return the current screenshot with a blue target-zone circle overlay."""
    if not SCREENSHOT_PATH.is_file():
        return "No screenshot yet", 404
    try:
        from PIL import Image, ImageDraw
        img = Image.open(SCREENSHOT_PATH).convert("RGBA")
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        cx, cy = img.size[0] // 2, img.size[1] // 2
        radius = 48
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=(59, 130, 246, 200),
            width=3,
        )
        draw.line((cx - 15, cy, cx + 15, cy), fill=(239, 68, 68, 230), width=2)
        draw.line((cx, cy - 15, cx, cy + 15), fill=(239, 68, 68, 230), width=2)
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    no_browser = (
        "--no-browser" in args
        or os.environ.get("HELPER_NO_BROWSER", "").strip() in ("1", "true", "yes", "on")
    )
    host = "127.0.0.1"
    port = 18765
    url = f"http://{host}:{port}/"
    tasks_url = f"http://{host}:{port}/llm-tasks"

    threading.Thread(target=hotkey_listener, daemon=True).start()

    shortcut = create_desktop_shortcut(url)
    if shortcut:
        print(f"Desktop shortcut: {shortcut}")
    print(f"Open: {url}")
    print(f"LLM tasks: {tasks_url}")
    if no_browser:
        print("Browser open skipped (--no-browser / HELPER_NO_BROWSER).")
    else:
        webbrowser.open(url)

    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
