"""Keep mouse_spot_helper (port 18765) alive while the PC is on.

Stability:
- One watchdog (named mutex + pid file)
- Restart helper only after consecutive failed probes (no single-blip restarts)
- Never start a second helper if HTTP already healthy
- Lightweight /api/health probe with home fallback
- No aggressive process killing on the happy path

Usage:
    .venv\\Scripts\\pythonw.exe helper_watchdog.py
    wscript start_llm_bg_hidden.vbs
"""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import time
import urllib.error
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
HELPER_PID_FILE = BASE_DIR / "mouse_spot_helper.pid"
LOG_FILE = BASE_DIR / "helper_watchdog.log"
EVENTS_FILE = BASE_DIR / "helper_watchdog_events.json"
# THE PER-TICK LIVENESS SINK (added 2026-09-29).
#
# WHY A DEDICATED FILE, AND WHY NOT THE TWO OBVIOUS CANDIDATES. MEASURED
# 2026-09-29: the liveness unit read `helper_watchdog_events.json`, which is
# written ONLY on a TRANSITION, so a healthy watchdog looked DEAD for 46 hours
# (166313.7s) while its loop was running every 15s. Both candidate sinks were
# measured and BOTH were rejected:
#
#   * `helper_watchdog.log` — its steady-state cadence is ~302s, NOT the 15s
#     loop. The only steady writer is the mode-reconcile line, and that fires
#     only while `chat_mode.json` is stale (measured: 24 consecutive lines at
#     302s intervals). A sink whose cadence depends on ANOTHER component's file
#     age is not a liveness signal.
#   * a heartbeat EVENT in `helper_watchdog_events.json` — the file is capped at
#     `MAX_EVENTS = 80` and is FULL (80/80, spanning 8 days). A 5-minute
#     heartbeat would evict every transition event within ~6.7 hours, destroying
#     the transition history `telemetry_db` reads.
#
# So the heartbeat gets its OWN file: written EVERY tick, one element, no cap to
# fight, and the transition log is left untouched.
HEARTBEAT_FILE = BASE_DIR / "helper_watchdog_heartbeat.json"
SNAPS_DIR = BASE_DIR / "helper_watchdog_snaps"
SNAPS_DIR.mkdir(exist_ok=True)

HOME_URL = "http://127.0.0.1:18765/"
HEALTH_URL = "http://127.0.0.1:18765/api/health"
STATUS_URL = "http://127.0.0.1:18765/api/system-status"
HELPER_PORT = 18765

POLL_INTERVAL_SEC = 15
PROBE_TIMEOUT_SEC = 3.0
FAIL_CONFIRM_COUNT = 3  # consecutive misses before DOWN
START_WAIT_SEC = 60
BACKOFF_MIN_SEC = 20
BACKOFF_MAX_SEC = 120
MAX_EVENTS = 80
MAX_SNAPS = 40
MUTEX_NAME = "Local\\AgentSystemHelperWatchdog_v4"

# THE SCREENSHOT RATE LIMIT.
#
# DEFECT FOUND BY MEASURING IT (2026-09-21): `record_event(..., take_shot=True)`
# captured a screenshot UNCONDITIONALLY. There are 4 such call sites in the
# health loop, and the loop runs every `POLL_INTERVAL_SEC` (15s). In a crash
# loop that is 4 captures per minute, each one spawning a thread that joins for
# up to 2.0s and then re-sorting all 40 files in `_prune_snaps()`.
#
# MEASURED, AND IT CORRECTS AN EARLIER CLAIM: the DISK is bounded (40 files,
# `MAX_SNAPS` prunes). So this is NOT unbounded disk growth — it is CPU churn
# during exactly the period when the machine is already struggling.
#
# THE RULE IS THE SAME ONE `condition_based_waiting` STATES: a repeated action
# with no condition is wrong in both directions, and worst it repeats SILENTLY.
# A cooldown is the condition here.
#
# PER-KIND, NOT GLOBAL. `helper_down` and `helper_recovered` are DIFFERENT
# events and each needs its own evidence; an event that steals another event's
# screenshot leaves a gap nobody can see. Keying by `kind` gives each event
# family its own budget.
MIN_SHOT_INTERVAL_SEC = 60.0

# kind -> monotonic timestamp of the last CAPTURE (not of the last attempt).
_last_shot: dict[str, float] = {}
# kind -> how many captures were suppressed, so a reader sees the throttling.
_shot_suppressed: dict[str, int] = {}


def _shot_allowed(kind: str, *, now: float | None = None) -> tuple[bool, str]:
    """May this event capture a screenshot now? Returns (allowed, reason).

    A suppressed capture is REPORTED, never skipped silently. A silent skip is
    the exact failure `condition_based_waiting` exists to prevent: the record
    would show an event with no image and no explanation, and a reader could not
    tell "there was nothing to see" from "the rate limit fired".
    """
    import time as _t

    k = str(kind or "event")
    ts = _t.monotonic() if now is None else float(now)
    last = _last_shot.get(k)
    interval = float(MIN_SHOT_INTERVAL_SEC)
    if interval > 0 and last is not None and (ts - last) < interval:
        _shot_suppressed[k] = _shot_suppressed.get(k, 0) + 1
        return (False,
                "rate limited: %s captured %.1fs ago, interval is %.0fs "
                "(suppressed %d time(s) so far)"
                % (k, ts - last, interval, _shot_suppressed[k]))
    _last_shot[k] = ts
    return (True, "")

# ---------------------------------------------------------------------------
# chat_mode.json reconcile (Option C — hybrid)
# ---------------------------------------------------------------------------
# Why: `chat_mode.json` records INTENT, not verified truth. deepseek_strip.ahk
# runs `f_mode_switch.py --set <mode>` (which writes the file) BEFORE clicking
# the UI selector, then calls `--verify`; if verification fails it only logs and
# never corrects the file. A failed switch therefore leaves the file wrong
# indefinitely, and ask_mode_guard (now strict) would enforce the wrong mode.
#
# This closes that loop. Safety rules are in reconcile_mode().
MODE_STATE_FILE = BASE_DIR / "chat_mode.json"
MODE_RECONCILE_ENABLED = os.environ.get("MODE_RECONCILE", "1").strip().lower() not in (
    "0",
    "off",
    "false",
)
MODE_RECONCILE_EVERY = max(1, int(os.environ.get("MODE_RECONCILE_EVERY", "2")))
MODE_STALE_SEC = int(os.environ.get("MODE_STALE_SEC", "300"))
MODE_VALID = ("ASK", "PLAN", "AGENT")


def _read_mode_state() -> dict | None:
    """Read chat_mode.json. Returns None on any problem."""
    try:
        with open(MODE_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_mode_state(mode: str, prev: str) -> None:
    """Atomically write chat_mode.json (same shape as f_mode_switch._write).

    Atomic via os.replace so ask_mode_guard can never read a partial file.
    `via`/`verified` mark a reconciled value as distinct from a hotkey write.
    """
    d = {
        "mode": mode,
        "prev": prev,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "via": "watchdog_reconcile",
        "verified": True,
    }
    tmp = str(MODE_STATE_FILE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MODE_STATE_FILE)


# ---------------------------------------------------------------------------
# UNKNOWN-streak visibility (added 2026-09-22, user ruling R1)
# ---------------------------------------------------------------------------
# WHY: rule 1 ("UNKNOWN never writes") is correct and is NOT changed. But it
# makes the corrector silent for long stretches — measured 20 consecutive
# UNKNOWN reads between 11:45 and 11:59 (`mode_vision_log.txt:1178-1216`). A
# silent corrector is indistinguishable from a working one, so the blindness is
# COUNTED and marked on the state file. This is VISIBILITY ONLY: no mode is ever
# written from an UNKNOWN read.
UNKNOWN_STREAK_MARK = int(os.environ.get("MODE_UNKNOWN_STREAK_MARK", "3"))


def _bump_unknown_streak() -> int:
    """Increment the consecutive-UNKNOWN counter and mark the state file.

    Returns the new streak. Never raises; a failure here must not break the
    health probe.
    """
    try:
        state = _read_mode_state() or {}
        streak = int(state.get("unknown_streak") or 0) + 1
        state["unknown_streak"] = streak
        if streak >= UNKNOWN_STREAK_MARK:
            # The marker the STEP 0 card can surface: the mode in this file is
            # NOT being corrected, because the reader cannot see the pill.
            state["unverified"] = True
        tmp = str(MODE_STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MODE_STATE_FILE)
        return streak
    except Exception:
        return 0


def _clear_unknown_streak() -> None:
    """Reset the counter after a confident read. Never raises."""
    try:
        state = _read_mode_state() or {}
        if not state.get("unknown_streak") and not state.get("unverified"):
            return
        state.pop("unknown_streak", None)
        state.pop("unverified", None)
        tmp = str(MODE_STATE_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, MODE_STATE_FILE)
    except Exception:
        pass


def reconcile_mode() -> str:
    """One Option-C reconcile pass. Returns a short outcome tag.

    Option C = write ONLY when ALL of these hold:
      1. the vision read is CONFIDENT (not UNKNOWN)
      2. the read is NEWER than the state file (never clobber a hotkey press
         that happened after the snapshot was taken)
      3. the state file is STALE (older than MODE_STALE_SEC)
      4. the read DISAGREES with the file (nothing to do when they match)

    Otherwise: log only, never write. A wrong write is worse than a stale file,
    because ask_mode_guard would then confidently enforce the wrong mode.
    """
    if not MODE_RECONCILE_ENABLED:
        return "disabled"

    state = _read_mode_state()
    if state is None:
        return "no_state"

    try:
        file_mtime = os.path.getmtime(MODE_STATE_FILE)
    except OSError:
        return "no_state"

    age = time.time() - file_mtime
    if age < MODE_STALE_SEC:
        return "fresh"          # rule 3: do not fight a recent hotkey write

    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        import f_mode_vision as fm

        t_before = time.time()
        seen = fm.read_pill()               # focus-free; "UNKNOWN" on any doubt
        t_after = time.time()
    except Exception as e:
        log(f"mode reconcile: read failed ({type(e).__name__}: {e})")
        return "read_error"

    if seen not in MODE_VALID:
        # rule 1: UNKNOWN carries no information — NEVER write from it.
        #
        # VISIBILITY (added 2026-09-22, user ruling R1). The rule above is
        # correct and is NOT changed. But it means the corrector is silent for
        # long stretches — measured 20 consecutive UNKNOWN reads between 11:45
        # and 11:59 (`mode_vision_log.txt:1178-1216`). A silent corrector looks
        # identical to a working one, so the blindness is now COUNTED and
        # marked on the state file. This is visibility only: no mode is written.
        streak = _bump_unknown_streak()
        log(f"mode reconcile: UNKNOWN read (age={int(age)}s, streak={streak}) "
            f"-> no write")
        return "unknown"

    # rule 2: the snapshot must be newer than the file it would overwrite.
    if t_after <= file_mtime:
        log("mode reconcile: read not newer than state file -> no write")
        return "stale_read"

    current = str(state.get("mode") or "").strip().upper()
    if seen == current:
        _clear_unknown_streak()
        return "match"          # rule 4: nothing to do

    log(
        f"mode reconcile: MISMATCH file={current or '?'} actual={seen} "
        f"(age={int(age)}s) -> writing"
    )
    try:
        _write_mode_state(seen, current or "")
        _clear_unknown_streak()
    except Exception as e:
        log(f"mode reconcile: write failed ({type(e).__name__}: {e})")
        return "write_error"
    return f"reconciled:{current}->{seen}"


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


def write_heartbeat(tick: int, helper_ok: bool) -> None:
    """One line of liveness evidence, written EVERY tick.

    THIS IS THE SINK THE LIVENESS UNIT READS. It must be written on the STEADY
    path, not only on a transition — that was the whole defect (see
    `HEARTBEAT_FILE` above). It is a ONE-ELEMENT list so the EXISTING file reader
    (`watchdog_health._newest_in_file`) parses it with no second code path.

    A write failure is LOGGED and swallowed: the heartbeat is evidence, and a
    disk problem must not stop the watchdog from doing its actual job (keeping
    the helper alive). The unit will then read a stale heartbeat and report DEAD,
    which is the correct verdict for a watchdog that cannot record.
    """
    try:
        HEARTBEAT_FILE.write_text(
            json.dumps([{"ts": _utc_iso(), "pid": os.getpid(),
                         "tick": int(tick), "helper_ok": bool(helper_ok)}],
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        log(f"WARNING: could not write heartbeat: {e}")


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


def capture_alert_screenshot(tag: str, timeout_sec: float = 2.5) -> dict:
    """Best-effort desktop shot. Must never block the keep-alive loop for long."""
    out: dict = {"ok": False, "path": None, "url": None, "error": None, "bytes": 0}
    try:
        import threading

        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in (tag or "alert")
        )[:40]
        name = f"{ts}_{safe}.png"
        path = SNAPS_DIR / name
        box: dict = {"img": None, "err": None}

        def _grab() -> None:
            try:
                try:
                    from PIL import ImageGrab

                    box["img"] = ImageGrab.grab(all_screens=False)
                except Exception:
                    import pyautogui

                    box["img"] = pyautogui.screenshot()
            except Exception as e:
                box["err"] = f"{type(e).__name__}: {e}"

        th = threading.Thread(target=_grab, name="wd-shot", daemon=True)
        th.start()
        th.join(max(0.5, float(timeout_sec)))
        if th.is_alive():
            out["error"] = f"screenshot timed out after {timeout_sec}s"
            return out
        if box.get("err"):
            out["error"] = str(box["err"])
            return out
        img = box.get("img")
        if img is None:
            out["error"] = "no image"
            return out
        img.save(path, format="PNG")
        out["ok"] = True
        out["path"] = str(path)
        out["url"] = f"/api/watchdog/snapshot/{name}"
        out["bytes"] = path.stat().st_size if path.is_file() else 0
        _prune_snaps()
    except Exception as e:
        out["error"] = str(e)
    return out


def record_event(
    kind: str,
    message: str,
    *,
    detail: dict | None = None,
    take_shot: bool = False,
    level: str = "info",
) -> dict:
    # Capture shot first but never let it prevent event persistence / restart.
    # THE RATE LIMIT is applied here, at the ONE place a shot is taken, so every
    # call site is covered without touching the 4 of them.
    shot = None
    shot_suppressed = False
    shot_reason = ""
    if take_shot:
        allowed, why = _shot_allowed(kind)
        if allowed:
            try:
                shot = capture_alert_screenshot(kind, timeout_sec=2.0)
            except Exception as e:
                shot = {"ok": False, "error": str(e)}
        else:
            shot_suppressed, shot_reason = True, why
            log(f"Screenshot suppressed for {kind}: {why}")
    event = {
        "id": f"w_{int(time.time() * 1000)}_{os.getpid()}",
        "ts": _utc_iso(),
        "local_time": _now(),
        "kind": kind,
        "level": level,
        "message": message,
        "detail": detail or {},
        "watchdog_pid": os.getpid(),
        "screenshot": shot,
        # NEW FIELDS (additive). An event with `take_shot=True` and
        # `screenshot=None` used to mean capture FAILED; now it can also mean the
        # RATE LIMIT fired. Without these two fields those are indistinguishable,
        # which would turn a working throttle into a mystery.
        "shot_suppressed": shot_suppressed,
        "shot_reason": shot_reason,
    }
    try:
        events = _load_events()
        events.insert(0, event)
        _save_events(events)
    except Exception as e:
        log(f"WARNING: record_event save failed: {e}")
    return event


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                if ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return int(code.value) == STILL_ACTIVE
                return True
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _port_listening(port: int = HELPER_PORT) -> bool:
    """True if anything is LISTENING on 127.0.0.1:port (fast netstat parse)."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except Exception:
        return False
    needle = f"127.0.0.1:{port}"
    for line in (out or "").splitlines():
        u = line.upper()
        if "LISTENING" not in u:
            continue
        if needle in line.replace(" ", ""):
            return True
        # netstat columns vary; softer match
        if f":{port}" in line and "LISTENING" in u and "127.0.0.1" in line:
            return True
    return False


def _listener_pids(port: int = HELPER_PORT) -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in (out or "").splitlines():
        if "LISTENING" not in line.upper():
            continue
        if f":{port}" not in line:
            continue
        parts = line.split()
        if not parts:
            continue
        tail = parts[-1]
        if tail.isdigit():
            pid = int(tail)
            if pid > 0 and pid not in pids:
                pids.append(pid)
    return pids


def _kill_pid(pid: int, reason: str = "", tree: bool = False) -> None:
    """Kill one PID. Default is NOT /T so we never wipe the whole stack by accident."""
    if pid <= 0 or pid == os.getpid():
        return
    log(f"Stopping pid={pid}" + (f" ({reason})" if reason else ""))
    if sys.platform == "win32":
        try:
            cmd = ["taskkill", "/PID", str(pid), "/F"]
            if tree:
                cmd.insert(3, "/T")
            subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=15,
                check=False,
            )
            return
        except Exception as e:
            log(f"taskkill failed pid={pid}: {e}")
    try:
        os.kill(pid, 9)
    except Exception as e:
        log(f"kill failed pid={pid}: {e}")


def _script_pids(script_name: str) -> list[int]:
    """PIDs that are actually running the named .py script (not probes/editors)."""
    if sys.platform != "win32":
        return []
    name = (script_name or "").lower().replace("'", "")
    if not name.endswith(".py"):
        return []
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.CommandLine } | "
                "Select-Object ProcessId, CommandLine | "
                "ConvertTo-Json -Compress",
            ],
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
        )
    except Exception:
        return []
    raw = (out or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    pids: list[int] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        cl = str(row.get("CommandLine") or "")
        low = cl.lower()
        if name not in low:
            continue
        # Must look like python/pythonw invoking the script, not an editor or -c probe.
        if " -c " in f" {low} " or low.strip().startswith("-c"):
            continue
        if "python" not in low:
            continue
        # Prefer token boundary: script name as an argument path/token.
        if f"\\{name}" not in low and f"/{name}" not in low and f" {name}" not in low:
            if not low.endswith(name):
                continue
        try:
            pid = int(row.get("ProcessId") or 0)
        except Exception:
            continue
        if pid > 0 and pid not in pids:
            pids.append(pid)
    return pids


def read_helper_pid() -> int | None:
    # Prefer real LISTENING owner of :18765 over pid file / spawn pid.
    listeners = _listener_pids(HELPER_PORT)
    if listeners:
        return listeners[0]
    try:
        if HELPER_PID_FILE.is_file():
            pid = int(HELPER_PID_FILE.read_text(encoding="utf-8").strip() or "0")
            if pid and _pid_alive(pid):
                return pid
    except Exception:
        pass
    return None


def write_helper_pid(pid: int | None) -> None:
    if not pid:
        return
    try:
        HELPER_PID_FILE.write_text(str(int(pid)), encoding="utf-8")
    except Exception:
        pass


def adopt_helper_pid(*, prune_duplicates: bool = False) -> int | None:
    """Refresh helper pid from listener / health.

    Never kill helpers here. Duplicate prevention is helper's own mutex +
    skip-start-if-healthy. Killing from adopt caused false downs.
    """
    del prune_duplicates  # kept for call-site compat; intentionally unused
    listeners = _listener_pids(HELPER_PORT)
    owner = listeners[0] if listeners else None
    if owner:
        write_helper_pid(owner)
        return owner
    return read_helper_pid()


_mutex_handle = None


def acquire_single_instance() -> bool:
    """Return True if this process owns the watchdog lock.

    Ownership is mutex-first. We do NOT kill other processes during acquire —
    killing peers caused self/race deaths and flapping.
    """
    global _mutex_handle

    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183
        WAIT_OBJECT_0 = 0
        WAIT_ABANDONED = 128
        WAIT_TIMEOUT = 258
        kernel32.SetLastError(0)
        # bInitialOwner=False then WaitForSingleObject — reliable ownership check.
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_err = int(kernel32.GetLastError() or 0)
        if not handle:
            log("WARNING: CreateMutex failed; falling back to PID file")
        else:
            wait = int(kernel32.WaitForSingleObject(handle, 0))
            if wait in (WAIT_OBJECT_0, WAIT_ABANDONED):
                _mutex_handle = handle
                if last_err == ERROR_ALREADY_EXISTS and wait == WAIT_ABANDONED:
                    log("Took over abandoned watchdog mutex")
            elif wait == WAIT_TIMEOUT or last_err == ERROR_ALREADY_EXISTS:
                log("Another helper_watchdog already running (mutex). Exit.")
                kernel32.CloseHandle(handle)
                return False
            else:
                log(f"WARNING: mutex wait failed code={wait}; falling back to PID file")
                kernel32.CloseHandle(handle)

    if PID_FILE.is_file():
        try:
            old = int(PID_FILE.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            old = 0
        if old and old != os.getpid() and _pid_alive(old):
            if _mutex_handle is None:
                log(f"Another helper_watchdog already running (pid={old}). Exit.")
                return False
            log(f"Replacing live-but-unlocked watchdog pid file (old={old})")
        if old and old != os.getpid() and not _pid_alive(old):
            log(f"Replacing stale watchdog pid file (old={old})")
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

                ctypes.windll.kernel32.ReleaseMutex(_mutex_handle)
                ctypes.windll.kernel32.CloseHandle(_mutex_handle)
                _mutex_handle = None
        except Exception:
            pass

    atexit.register(_cleanup)
    return True


def _http_ok(url: str, timeout: float = PROBE_TIMEOUT_SEC) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = int(getattr(resp, "status", 200) or 200)
            if 200 <= code < 500:
                return True, f"http {code}"
            return False, f"http {code}"
    except urllib.error.HTTPError as e:
        code = int(getattr(e, "code", 0) or 0)
        if 400 <= code < 500:
            return True, f"http {code}"
        return False, f"http-error {code or '?'}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


# Local socket errors mean the PROBE could not run, not that the helper is down.
#
# WHY THIS EXISTS (P2-socket, 2026-09-20)
# ---------------------------------------
# `WinError 10048` is WSAEADDRINUSE ("only one usage of each socket address is
# normally permitted"). It is a LOCAL socket-binding failure — the probe's own
# ephemeral port collided — and says nothing about whether the helper is alive.
#
# Measured from the existing logs before changing anything:
#   * 125 occurrences of 10048 in helper_watchdog.log
#   * 30 of the 72 "Probe miss 3/3" (DOWN-threshold) events were 10048
#   * of 22 recorded helper_down events, **16 had port_listening=True** —
#     the helper WAS listening while the probe reported it down
#
# So the watchdog was restarting a healthy helper because its own measurement
# failed. That is the same defect class as the rest of this workset: a
# measurement failure read as a system failure.
#
# These errors are therefore treated as UNKNOWN: they do not count toward the
# DOWN threshold. A genuinely dead helper still fails with 10061 (connection
# refused) or a timeout, which DO count.
LOCAL_SOCKET_ERROR_CODES = ("10048", "10049", "10055", "10060")


def _is_local_socket_error(reason: str) -> bool:
    """True when a probe failure is the PROBE's own socket problem."""
    r = str(reason or "")
    return any(code in r for code in LOCAL_SOCKET_ERROR_CODES)


def probe_helper() -> tuple[bool, str]:
    ok, reason = _http_ok(HEALTH_URL, timeout=PROBE_TIMEOUT_SEC)
    if ok:
        return True, f"health {reason}"
    ok2, reason2 = _http_ok(HOME_URL, timeout=PROBE_TIMEOUT_SEC)
    if ok2:
        return True, f"home {reason2}"
    return False, f"health={reason}; home={reason2}"


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
    return f"{cname} | {cid} | mouse_spot_helper ON | {ol_txt} | {model}".strip()


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


def clear_stale_port_holders() -> None:
    """Only when HTTP is down: free LISTENING pids on :18765 so a new helper can bind."""
    ok, _ = probe_helper()
    if ok:
        return
    pids = _listener_pids(HELPER_PORT)
    if not pids:
        return
    log(f"HTTP down but port {HELPER_PORT} still LISTENING pids={pids} — clearing")
    for pid in pids:
        _kill_pid(pid, reason="stale listener on 18765 (HTTP down)")
    time.sleep(1.0)


def start_helper() -> int | None:
    ok, reason = probe_helper()
    if ok:
        pid = adopt_helper_pid(prune_duplicates=False)
        log(f"Skip start — helper already healthy ({reason}) pid={pid}")
        return pid

    # Another helper process already running but not healthy yet — wait, don't double-spawn.
    existing = [
        p for p in _script_pids("mouse_spot_helper.py") if p != os.getpid() and _pid_alive(p)
    ]
    if existing:
        log(f"Helper process(es) already present pids={existing} — waiting instead of spawn")
        if wait_until_healthy(min(30, START_WAIT_SEC)):
            return adopt_helper_pid()
        log("Existing helper did not become healthy — clearing stale port holders only")
        clear_stale_port_holders()
        time.sleep(0.8)
        if wait_until_healthy(8):
            return adopt_helper_pid()

    clear_stale_port_holders()

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
        # NEW process group, no window. Avoid DETACHED_PROCESS so Popen.pid is the helper.
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    try:
        log(f"Starting helper: {py} {HELPER_SCRIPT.name} --no-browser")
        err_log = BASE_DIR / "mouse_spot_helper_spawn.err.log"
        err_f = open(err_log, "ab", buffering=0)
        proc = subprocess.Popen(
            [py, str(HELPER_SCRIPT), "--no-browser"],
            cwd=str(BASE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_f,
            creationflags=creationflags,
            close_fds=True,
        )
        log(f"Helper process started pid={proc.pid}")
        write_helper_pid(proc.pid)
        time.sleep(2.5)
        adopted = adopt_helper_pid(prune_duplicates=False)
        return int(adopted or proc.pid)
    except Exception as e:
        log(f"ERROR starting helper: {e}")
        return None


def wait_until_healthy(timeout_sec: float = START_WAIT_SEC) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        ok, _ = probe_helper()
        if ok:
            return True
        time.sleep(0.5)
    return False


def main() -> int:
    if not acquire_single_instance():
        return 0

    log("helper_watchdog started")
    log(
        f"probe={HEALTH_URL} fallback={HOME_URL} interval={POLL_INTERVAL_SEC}s "
        f"fail_confirm={FAIL_CONFIRM_COUNT}"
    )

    helper_pid = read_helper_pid()
    record_event(
        "watchdog_started",
        f"Watchdog started pid={os.getpid()} probe={HEALTH_URL}",
        detail={
            "interval_sec": POLL_INTERVAL_SEC,
            "pid": os.getpid(),
            "fail_confirm": FAIL_CONFIRM_COUNT,
            "helper_pid": helper_pid,
        },
        take_shot=False,
        level="info",
    )

    was_up: bool | None = None
    announced_first = False
    fail_streak = 0
    fail_confirm = 0
    next_start_allowed = 0.0
    tick = 0

    while True:
        # Health probe FIRST: it is the watchdog's primary job and its latency
        # budget must never be spent on optional work. The reconcile below runs
        # only after the probe has returned.
        ok, probe_reason = probe_helper()

        # Optional work LAST: reconcile is try/except-wrapped so an error can
        # never break the health loop. It intentionally sits AFTER the probe so
        # a slow screenshot + VL inference cannot delay health detection.
        tick += 1
        # THE HEARTBEAT, EVERY TICK. This is the liveness evidence the unit
        # reads, so it must be written on the STEADY path — writing it only on a
        # transition is exactly the defect this fixes (a healthy watchdog looked
        # DEAD for 46 hours). It sits AFTER the probe so a slow disk write can
        # never delay health detection.
        write_heartbeat(tick, ok)
        if MODE_RECONCILE_ENABLED and tick % MODE_RECONCILE_EVERY == 0:
            try:
                tag = reconcile_mode()
                if tag.startswith("reconciled"):
                    log(f"mode reconcile: {tag}")
            except Exception as e:
                log(f"mode reconcile error: {type(e).__name__}: {e}")

        if ok:
            if fail_confirm:
                log(f"Probe recovered after {fail_confirm} soft-fail(s): {probe_reason}")
            fail_confirm = 0
            helper_pid = adopt_helper_pid(prune_duplicates=False) or helper_pid
            if helper_pid:
                adopt_helper_pid(prune_duplicates=True)
                helper_pid = read_helper_pid() or helper_pid
            summary = status_summary(fetch_status())
            if was_up is False:
                log(f"Helper recovered: {summary}")
                record_event(
                    "helper_recovered",
                    f"Helper recovered: {summary}",
                    detail={
                        "summary": summary,
                        "fail_streak": fail_streak,
                        "helper_pid": helper_pid,
                        "probe": probe_reason,
                    },
                    take_shot=True,
                    level="ok",
                )
                show_balloon("mouse_spot_helper recovered", summary)
            elif not announced_first:
                log(f"Helper ready: {summary}")
                record_event(
                    "helper_ready",
                    f"Helper ready: {summary}",
                    detail={
                        "summary": summary,
                        "helper_pid": helper_pid,
                        "probe": probe_reason,
                    },
                    take_shot=False,
                    level="ok",
                )
                show_balloon("mouse_spot_helper ON", summary)
            announced_first = True
            was_up = True
            fail_streak = 0
            next_start_allowed = 0.0
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # Soft-fail path: require consecutive misses before restart.
        # First boot (was_up is None): start after 1 miss so bring-up is fast.
        #
        # P2-socket (2026-09-20): a LOCAL socket error means the PROBE could not
        # run, not that the helper is down. Measured: 16 of 22 recorded
        # helper_down events had port_listening=True — the helper was listening
        # while the probe reported it down. Such a failure is UNKNOWN, so it
        # does not count toward the DOWN threshold. A genuinely dead helper
        # still fails with 10061 (refused) or a timeout, which DO count.
        if _is_local_socket_error(probe_reason):
            log(f"Probe UNKNOWN (local socket error, not counted): {probe_reason}")
            time.sleep(POLL_INTERVAL_SEC)
            continue

        need = 1 if was_up is None else FAIL_CONFIRM_COUNT
        fail_confirm += 1
        log(f"Probe miss {fail_confirm}/{need}: {probe_reason}")
        if fail_confirm < need:
            time.sleep(max(1.0, POLL_INTERVAL_SEC / float(max(need, 1))))
            continue

        # was_up is True  -> real down event (shot ok)
        # was_up is None  -> first boot, helper not up yet (no shot; start ASAP)
        # was_up is False -> already in recovery loop
        if was_up is True:
            log("Helper DOWN — will restart")
            record_event(
                "helper_down",
                "Helper DOWN — watchdog will restart mouse_spot_helper",
                detail={
                    "probe": HEALTH_URL,
                    "reason": probe_reason,
                    "fail_confirm": fail_confirm,
                    "action": "restart_helper",
                    "last_helper_pid": helper_pid,
                    "port_listening": _port_listening(HELPER_PORT),
                },
                take_shot=True,
                level="alert",
            )
        elif was_up is None:
            log("Helper not up yet — starting mouse_spot_helper")
            record_event(
                "helper_start_initial",
                "Initial start of mouse_spot_helper (not up at watchdog boot)",
                detail={
                    "probe": HEALTH_URL,
                    "reason": probe_reason,
                    "fail_confirm": fail_confirm,
                    "action": "start_helper",
                },
                take_shot=False,
                level="warn",
            )
        was_up = False
        fail_confirm = 0

        now = time.time()
        if now < next_start_allowed:
            time.sleep(min(POLL_INTERVAL_SEC, max(1.0, next_start_allowed - now)))
            continue

        ok_now, reason_now = probe_helper()
        if ok_now:
            log(f"Skip restart — helper healthy again ({reason_now})")
            continue

        log("Calling start_helper()…")
        started_pid = start_helper()
        if started_pid:
            helper_pid = started_pid
        record_event(
            "helper_restart_attempt",
            f"Starting helper process pid={started_pid or '?'}",
            detail={"helper_pid": started_pid, "script": str(HELPER_SCRIPT.name)},
            take_shot=False,
            level="warn",
        )
        healthy = wait_until_healthy(START_WAIT_SEC)
        if healthy:
            helper_pid = adopt_helper_pid(prune_duplicates=False) or started_pid or helper_pid
            if helper_pid:
                adopt_helper_pid(prune_duplicates=True)
                helper_pid = read_helper_pid() or helper_pid
            summary = status_summary(fetch_status())
            log(f"Helper up after start: {summary}")
            record_event(
                "helper_up_after_start",
                f"Helper up after start: {summary}",
                detail={"summary": summary, "helper_pid": helper_pid},
                take_shot=True,
                level="ok",
            )
            if not announced_first:
                show_balloon("mouse_spot_helper ON", summary)
                announced_first = True
            else:
                show_balloon("mouse_spot_helper recovered", summary)
            was_up = True
            fail_streak = 0
            next_start_allowed = 0.0
        else:
            fail_streak += 1
            backoff = min(BACKOFF_MAX_SEC, BACKOFF_MIN_SEC * fail_streak)
            next_start_allowed = time.time() + backoff
            log(
                f"Helper still down after start wait; backoff {backoff}s "
                f"(streak={fail_streak})"
            )
            record_event(
                "helper_start_failed",
                f"Helper still down after {START_WAIT_SEC}s wait; "
                f"backoff {backoff}s (streak={fail_streak})",
                detail={
                    "helper_pid": started_pid,
                    "wait_sec": START_WAIT_SEC,
                    "backoff_sec": backoff,
                    "fail_streak": fail_streak,
                    "hint": "Check mouse_spot_helper import/syntax, port 18765, crash loops",
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
