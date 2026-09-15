"""Keep Mouse Spot Helper (port 18765) alive while the PC is on.

Polls http://127.0.0.1:18765 and restarts mouse_spot_helper.py when down.
Shows a Windows tray balloon on first ready and on down->up recovery.
Records UI events + desktop screenshots for the Watchdog panel.

Usage:
    .venv\\Scripts\\python.exe helper_watchdog.py
    wscript start_llm_bg_hidden.vbs   # hidden, no console
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BASE_DIR / ".venv" / "Scripts" / "python.exe"
VENV_PYTHONW = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
HELPER_SCRIPT = BASE_DIR / "mouse_spot_helper.py"
PID_FILE = BASE_DIR / "helper_watchdog.pid"
LOG_FILE = BASE_DIR / "helper_watchdog.log"
EVENTS_FILE = BASE_DIR / "helper_watchdog_events.json"
SNAPS_DIR = BASE_DIR / "helper_watchdog_snaps"
SNAPS_DIR.mkdir(exist_ok=True)

HOME_URL = "http://127.0.0.1:18765/"
STATUS_URL = "http://127.0.0.1:18765/api/system-status"

POLL_INTERVAL_SEC = 15
PROBE_TIMEOUT_SEC = 2
START_WAIT_SEC = 45
BACKOFF_MIN_SEC = 15
BACKOFF_MAX_SEC = 60
MAX_EVENTS = 80
MAX_SNAPS = 40


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def log(msg: str) -> None:
    line = f"[{_now()}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _load_events() -> list[dict]:
    if not EVENTS_FILE.is_file():
        return []
    try:
        data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_events(events: list[dict]) -> None:
    try:
        EVENTS_FILE.write_text(
            json.dumps(events[:MAX_EVENTS], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log(f"WARNING: could not save events: {e}")


def _prune_snaps() -> None:
    try:
        files = sorted(
            SNAPS_DIR.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in files[MAX_SNAPS:]:
            try:
                old.unlink(missing_ok=True)
            except Exception:
                pass
    except Exception:
        pass


def capture_alert_screenshot(tag: str) -> dict:
    """Full-desktop snapshot so UI can show what was on screen during an alert."""
    out: dict = {"ok": False, "path": None, "url": None, "error": None, "bytes": 0}
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in (tag or "alert"))[:40]
        name = f"{ts}_{safe}.png"
        path = SNAPS_DIR / name
        img = None
        try:
            from PIL import ImageGrab

            img = ImageGrab.grab(all_screens=True)
        except Exception:
            try:
                import pyautogui

                img = pyautogui.screenshot()
            except Exception as e:
                out["error"] = f"screenshot backend failed: {type(e).__name__}: {e}"
                return out
        if img is None:
            out["error"] = "screenshot returned empty"
            return out
        img.save(path, format="PNG", optimize=True)
        out["ok"] = True
        out["path"] = str(path)
        out["url"] = f"/api/watchdog/snapshot/{name}"
        out["bytes"] = path.stat().st_size if path.is_file() else 0
        _prune_snaps()
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        return out


def record_event(
    kind: str,
    message: str,
    *,
    detail: dict | None = None,
    take_shot: bool = False,
    level: str = "info",
) -> dict:
    """Append a UI-visible watchdog event (optional desktop screenshot)."""
    shot = capture_alert_screenshot(kind) if take_shot else None
    event = {
        "id": f"w_{int(time.time() * 1000)}_{os.getpid()}",
        "ts": _utc_iso(),
        "local_time": _now(),
        "kind": kind,
        "level": level,
        "message": message,
        "detail": detail or {},
        "watchdog_pid": os.getpid(),
        "screenshot": None,
    }
    if shot:
        event["screenshot"] = {
            "ok": bool(shot.get("ok")),
            "url": shot.get("url"),
            "path": shot.get("path"),
            "bytes": shot.get("bytes") or 0,
            "error": shot.get("error"),
        }
    events = _load_events()
    events.insert(0, event)
    _save_events(events)
    return event


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
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    return True


_mutex_handle = None


def acquire_single_instance() -> bool:
    """Return True if this process owns the watchdog lock (Windows named mutex)."""
    global _mutex_handle

    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        name = "Local\\AgentSystemHelperWatchdog"
        handle = kernel32.CreateMutexW(None, wintypes.BOOL(False), name)
        if not handle:
            log("WARNING: CreateMutex failed; falling back to PID file")
        else:
            last_err = kernel32.GetLastError()
            if last_err == ERROR_ALREADY_EXISTS:
                log("Another helper_watchdog already running (mutex). Exit.")
                kernel32.CloseHandle(handle)
                return False
            _mutex_handle = handle

    if PID_FILE.is_file():
        try:
            old = int(PID_FILE.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old = 0
        if old and old != os.getpid() and _pid_alive(old) and _mutex_handle is None:
            log(f"Another helper_watchdog already running (pid={old}). Exit.")
            return False
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        log(f"WARNING: could not write pid file: {e}")

    def _cleanup() -> None:
        global _mutex_handle
        try:
            if PID_FILE.is_file():
                cur = PID_FILE.read_text(encoding="utf-8").strip()
                if cur == str(os.getpid()):
                    PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            if _mutex_handle is not None and sys.platform == "win32":
                import ctypes

                ctypes.windll.kernel32.CloseHandle(_mutex_handle)
                _mutex_handle = None
        except Exception:
            pass

    atexit.register(_cleanup)
    return True


def probe_home() -> bool:
    try:
        req = urllib.request.Request(HOME_URL, method="GET")
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SEC) as resp:
            return 200 <= int(getattr(resp, "status", 200) or 200) < 500
    except Exception:
        return False


def fetch_status() -> dict | None:
    try:
        req = urllib.request.Request(STATUS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def status_summary(data: dict | None) -> str:
    if not data:
        return "status unavailable"
    idn = data.get("identity") or {}
    ol = data.get("ollama") or {}
    cid = idn.get("computer_id") or "-"
    cname = idn.get("computer_name") or "-"
    ol_txt = "Ollama ON" if ol.get("ok") else "Ollama OFF"
    model = ol.get("model") or ""
    return f"{cname} | {cid} | Helper ON | {ol_txt} | {model}".strip()


def show_balloon(title: str, text: str, seconds: float = 6.0) -> None:
    if sys.platform != "win32":
        log(f"balloon skipped: {title} — {text}")
        return
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.BalloonTipTitle = {json.dumps(title)}; "
        f"$n.BalloonTipText = {json.dumps(text[:240])}; "
        "$n.ShowBalloonTip(8000); "
        f"Start-Sleep -Seconds {max(1, int(seconds))}; "
        "$n.Dispose()"
    )
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps,
            ],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log(f"balloon failed: {e}")


def start_helper() -> subprocess.Popen | None:
    if VENV_PYTHONW.is_file():
        py = str(VENV_PYTHONW)
    elif VENV_PYTHON.is_file():
        py = str(VENV_PYTHON)
    else:
        cand = Path(sys.executable)
        pyw = cand.with_name("pythonw.exe")
        py = str(pyw if pyw.is_file() else cand)
    if not HELPER_SCRIPT.is_file():
        log(f"ERROR: helper missing: {HELPER_SCRIPT}")
        return None
    env = os.environ.copy()
    env["HELPER_NO_BROWSER"] = "1"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    try:
        log(f"Starting helper: {py} {HELPER_SCRIPT.name} --no-browser")
        proc = subprocess.Popen(
            [py, str(HELPER_SCRIPT), "--no-browser"],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
        log(f"Helper process started pid={proc.pid}")
        return proc
    except Exception as e:
        log(f"ERROR starting helper: {e}")
        return None


def wait_until_healthy(timeout_sec: float = START_WAIT_SEC) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if probe_home():
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    if not acquire_single_instance():
        return 0

    log("helper_watchdog started")
    log(f"probe={HOME_URL} interval={POLL_INTERVAL_SEC}s")
    record_event(
        "watchdog_started",
        f"Watchdog started pid={os.getpid()} probe={HOME_URL}",
        detail={"interval_sec": POLL_INTERVAL_SEC, "pid": os.getpid()},
        take_shot=False,
        level="info",
    )

    was_up: bool | None = None
    announced_first = False
    fail_streak = 0
    next_start_allowed = 0.0

    while True:
        up = probe_home()
        if up:
            summary = status_summary(fetch_status())
            if was_up is False:
                log(f"Helper recovered: {summary}")
                record_event(
                    "helper_recovered",
                    f"Helper recovered: {summary}",
                    detail={"summary": summary, "fail_streak": fail_streak},
                    take_shot=True,
                    level="ok",
                )
                show_balloon("LLM Helper recovered", summary)
            elif not announced_first:
                log(f"Helper ready: {summary}")
                record_event(
                    "helper_ready",
                    f"Helper ready: {summary}",
                    detail={"summary": summary},
                    take_shot=False,
                    level="ok",
                )
                show_balloon("LLM Helper ON", summary)
            announced_first = True
            was_up = True
            fail_streak = 0
            next_start_allowed = 0.0
            time.sleep(POLL_INTERVAL_SEC)
            continue

        if was_up is not False:
            log("Helper DOWN — will restart")
            record_event(
                "helper_down",
                "Helper DOWN — watchdog will restart mouse_spot_helper",
                detail={
                    "probe": HOME_URL,
                    "reason": "HTTP probe failed (connection refused / timeout / non-OK)",
                    "action": "restart_helper",
                },
                take_shot=True,
                level="alert",
            )
        was_up = False

        now = time.time()
        if now < next_start_allowed:
            time.sleep(min(POLL_INTERVAL_SEC, max(1.0, next_start_allowed - now)))
            continue

        if probe_home():
            continue

        proc = start_helper()
        started_pid = getattr(proc, "pid", None) if proc else None
        record_event(
            "helper_restart_attempt",
            f"Starting helper process pid={started_pid or '?'}",
            detail={"helper_pid": started_pid, "script": str(HELPER_SCRIPT.name)},
            take_shot=False,
            level="warn",
        )
        ok = wait_until_healthy(START_WAIT_SEC)
        if ok:
            summary = status_summary(fetch_status())
            log(f"Helper up after start: {summary}")
            record_event(
                "helper_up_after_start",
                f"Helper up after start: {summary}",
                detail={"summary": summary, "helper_pid": started_pid},
                take_shot=True,
                level="ok",
            )
            if not announced_first:
                show_balloon("LLM Helper ON", summary)
                announced_first = True
            else:
                show_balloon("LLM Helper recovered", summary)
            was_up = True
            fail_streak = 0
            next_start_allowed = 0.0
        else:
            fail_streak += 1
            backoff = min(BACKOFF_MAX_SEC, BACKOFF_MIN_SEC * fail_streak)
            next_start_allowed = time.time() + backoff
            log(f"Helper still down after start wait; backoff {backoff}s (streak={fail_streak})")
            record_event(
                "helper_start_failed",
                f"Helper still down after {START_WAIT_SEC}s wait; backoff {backoff}s (streak={fail_streak})",
                detail={
                    "helper_pid": started_pid,
                    "wait_sec": START_WAIT_SEC,
                    "backoff_sec": backoff,
                    "fail_streak": fail_streak,
                    "hint": "Check mouse_spot_helper syntax/import errors, port 18765 bind, or crash loops",
                },
                take_shot=True,
                level="alert",
            )

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log("helper_watchdog stopped (KeyboardInterrupt)")
        raise SystemExit(0)
