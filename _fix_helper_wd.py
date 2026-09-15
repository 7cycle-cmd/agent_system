from pathlib import Path
import re
p = Path(r"c:\projects\agent_system\mouse_spot_helper.py")
t = p.read_text(encoding="utf-8")
# Fix broken check_ollama_status tail + insert watchdog helpers cleanly
old_start = t.find('    except Exception as e:\n        return {\n            "ok": False,\n            "base_url": base,\n            "model": model,\n            "model_present": False,')
print("old_start", old_start)
# find helper_status_payload end before _AGENT_DB_MIGRATED
agent = t.find("\n_AGENT_DB_MIGRATED = False")
print("agent", agent)
if old_start < 0 or agent < 0:
    raise SystemExit("markers missing")
block = '''    except Exception as e:
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


'''
new_t = t[:old_start] + block + t[agent+1:]
# fix broken route fragments if still present
new_t = new_t.replace('\n/task-records", methods=["GET"])\n', '\n\n@app.route("/api/skills/task-records", methods=["GET"])\n')
new_t = new_t.replace('@app.route("/api/skills\n\n@app.route("/api/skills", methods=["GET"])', '@app.route("/api/skills", methods=["GET"])')
new_t = new_t.replace('@app.route("/api/skills\n\n\n@app.route("/api/skills", methods=["GET"])', '@app.route("/api/skills", methods=["GET"])')
p.write_text(new_t, encoding="utf-8")
print("wrote", len(new_t))
