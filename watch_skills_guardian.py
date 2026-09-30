"""watch_skills_guardian — 2nd-layer supervisor for watch_skills.

Windows Task Scheduler runs this periodically (e.g. every 5 min). It checks
the watcher heartbeat; if stale (> HEARTBEAT_STALE_SEC) or the watcher PID is
dead, it restarts the supervisor (start_watch_skills_supervisor.bat) which in
turn restarts the watcher.

This is the 2nd layer: the .bat supervisor handles watcher crashes; this
guardian handles supervisor death (e.g. .bat window closed / killed).

Usage:
    .venv\\Scripts\\python.exe watch_skills_guardian.py [--check]
    --check : report only, no restart (exit 0 healthy, 1 stale, 2 no pid)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HB_FILE = ROOT / "watch_skills_heartbeat.txt"
PID_FILE = ROOT / "watch_skills.pid"
SUPERVISOR_BAT = ROOT / "start_watch_skills_supervisor.bat"
LOG_FILE = ROOT / "watch_skills_guardian.log"

HEARTBEAT_STALE_SEC = 120  # 2x HEARTBEAT_INTERVAL (60s)


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_heartbeat_age() -> float | None:
    """Return heartbeat age in seconds, or None if file missing/unreadable."""
    try:
        mtime = HB_FILE.stat().st_mtime
        return time.time() - mtime
    except OSError:
        return None


def _read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _restart_supervisor() -> bool:
    """Start the supervisor .bat in a new window (detached)."""
    if not SUPERVISOR_BAT.is_file():
        _log(f"ERROR: supervisor missing: {SUPERVISOR_BAT}")
        return False
    try:
        subprocess.Popen(
            [str(SUPERVISOR_BAT)],
            cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _log(f"Supervisor restarted: {SUPERVISOR_BAT.name}")
        return True
    except Exception as e:
        _log(f"ERROR restarting supervisor: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="2nd-layer supervisor for watch_skills")
    ap.add_argument("--check", action="store_true", help="report only, no restart")
    args = ap.parse_args()

    age = _read_heartbeat_age()
    pid = _read_pid()

    if age is None:
        _log("Heartbeat file missing — watcher never started or file deleted")
        if args.check:
            return 2
        _restart_supervisor()
        return 0

    alive = pid is not None and _pid_alive(pid)
    stale = age > HEARTBEAT_STALE_SEC

    if alive and not stale:
        _log(f"Healthy: heartbeat age {age:.0f}s, pid {pid} alive")
        return 0

    if args.check:
        _log(f"Stale: heartbeat age {age:.0f}s, pid {pid} alive={alive}")
        return 1

    _log(f"Watcher down: heartbeat age {age:.0f}s, pid {pid} alive={alive} — restarting supervisor")
    _restart_supervisor()
    return 0


if __name__ == "__main__":
    sys.exit(main())