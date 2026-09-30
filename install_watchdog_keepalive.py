"""install_watchdog_keepalive.py — the KEEP-ALIVE that closes the silent-death gap.

WHY THIS EXISTS (measured 2026-09-24, plan `WATCHDOG.HEARTBEAT.SKILL`)
--------------------------------------------------------------------
The user: *"apply skill for watchdog or heartbeat to make it work again and
become stronger"*. The last part of that is this file.

MEASURED — the mechanism was ALREADY INSTALLED AND STILL FAILED:

  * `%APPDATA%\\...\\Startup\\LLM Auto Start.lnk` exists (2026-09-15), targeting
    `wscript.exe "start_llm_bg_hidden.vbs"`, which starts `helper_watchdog.py`
    hidden WITH a duplicate guard.
  * Yet `helper_watchdog_events.json`'s newest event was **2026-09-22T03:36:32Z**
    — and the live check at 2026-09-24 read **2.07 days** of silence.

THE GAP IS THE TRIGGER, NOT THE INSTALL. A Startup shortcut runs **ONCE, at
login**. If the watchdog dies at 03:36 on a Tuesday, nothing runs again until the
next login — days later. Nothing supervises the supervisor:
`helper_watchdog.py` restarts the HELPER (`helper_watchdog.py:1071` etc.) but an
`grep` for a self-restart/supervisor found **none**.

SO THE FIX IS A REPEATING TASK, AND IT MUST BE PER-USER. MEASURED with a probe:
`Register-ScheduledTask` for the current user succeeded **without elevation**
(and the probe task was removed again). A machine-wide SYSTEM task would need
admin rights and would change the security posture — those are user-owned
decisions (see the Ask-mode guard), not something an agent takes.

THE ACTION IS `runner`, WHICH IS SAFE IF ALREADY RUNNING. This module NEVER
kills, stops or force-restarts a running watchdog. `helper_watchdog.py:651`
records why that matters:

    Never kill helpers here. Duplicate prevention is helper's own mutex +
    skip-start-if-healthy. Killing from adopt caused false downs.

A keep-alive that SIGKILLs a live process "to be sure" would manufacture exactly
the false downs that lesson is about. "If it is already running, do nothing" is
not a soft choice — it is the correct one, and the proof asserts it.

`--uninstall` removes the task. Nothing here is destructive: the only side effect
is a scheduled task with a name you can see.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TASK_NAME = "AgentSystemWatchdogKeepAlive"
# THE SECOND SUPERVISED COMPONENT. MEASURED 2026-09-24: `worker_heartbeat_service`
# was started by NOTHING (`Get-ScheduledTask` and the Startup folder both showed
# no entry), and `worker_heartbeat`'s newest row was `2026-09-14 11:35:11` — 9.7
# days. Same fault, same guard, so it gets the same task with a subject argument
# rather than a second installer script.
HEARTBEAT_TASK_NAME = "AgentSystemHeartbeatKeepAlive"
# EVERY 5 MINUTES. Chosen from the MEASURED cadence, not a guess: the watchdog
# itself polls on `POLL_INTERVAL_SEC = 15` (`helper_watchdog.py:49`) and its event
# log shows several events per minute around a restart. A 5-minute tick therefore
# notices a death within one watchdog poll cycle's worth of delay for the cost of
# 288 cheap invocations a day, each of which does ONE file read.
REPEAT_MINUTES = 5
# A DEAD watchdog is reported after `stale_after_sec` = 300s
# (`watchdog_health.LIVENESS_UNITS['watchdog']`), so the tick and the staleness
# threshold agree. If one is changed the other must be — and the proof asserts
# the relationship rather than each number separately.
RUNNER = "runner"
RUNNER_SCRIPT = BASE_DIR / "keepalive_runner.py"


def task_argument(script: Path | str = RUNNER_SCRIPT, subject: str = "") -> str:
    """The `-Argument` string for a scheduled task. PURE, so it is testable.

    MEASURED DEFECT (2026-09-26) — `AgentSystemHeartbeatKeepAlive` had NEVER run
    successfully since it was installed:

        LastTaskResult = 2   (Windows 2 = ERROR_FILE_NOT_FOUND)
        Heartbeat Arguments: "C:\\...\\keepalive_runner.py --subject heartbeat"
        Watchdog  Arguments: "C:\\...\\keepalive_runner.py"

    The QUOTES WRAPPED THE PATH **AND** THE ARGS, so `pythonw` received ONE
    token. PROVEN by exit code, not inferred:

        pythonw "…\\keepalive_runner.py --subject heartbeat"  -> exit 2
        pythonw "…\\keepalive_runner.py" --subject heartbeat  -> exit 0

    THE BUG WAS DORMANT. The template was shared by BOTH tasks, and it is only
    wrong for a task that passes an argument. The watchdog task passes none, so
    it worked — and "the watchdog task works" was read as "the installer works".

    THE FIX: the closing quote follows the PATH, so the args stay OUTSIDE it. A
    path with no spaces still needs no quotes, but the path is ALWAYS quoted
    because a quoted token is unambiguous and quoting cannot break a no-space
    path.

    This is a pure function on purpose: the argument string is the thing that was
    wrong, so it must be assertable without registering a Windows task.
    """
    path = '"%s"' % str(script)
    subj = str(subject or "").strip()
    return path + ((" --subject %s" % subj) if subj else "")


def check_command(*, apply: bool = False,
                  task_name: str = TASK_NAME,
                  subject: str = "") -> dict[str, Any]:
    """Create-or-update a keep-alive task. Idempotent. NEVER kills anything.

    `apply=False` runs `schtasks /Query` ONLY, so a dry run cannot register
    anything — the same "a dry run stays dry" rule the rest of this repo follows.

    `subject` is passed to the runner as `--subject` when set, so ONE installer
    serves both components (`watchdog`, `heartbeat`) instead of two duplicate
    scripts.
    """
    ps = (
        "$ErrorActionPreference='Stop';"
        "$a = New-ScheduledTaskAction -Execute '%PYW%' "
        "-Argument '%ARGS%' -WorkingDirectory '%ROOT%';"
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes %MIN%) "
        # 3650 days (10 years) rather than [TimeSpan]::MaxValue. MEASURED: the
        # Task Scheduler XML schema REJECTS MaxValue with "(8,42):Duration:
        # P99999999DT23H59M59S", so the task never registered. A finite horizon is
        # required by the schema, and 10 years is longer than the machine's
        # useful life — with `--status` able to prove the task is still there.
        "-RepetitionDuration (New-TimeSpan -Days 3650);"
        "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable "
        "-MultipleInstances IgnoreNew;"
        "Register-ScheduledTask -TaskName '%NAME%' -Action $a -Trigger $t "
        "-Settings $s -Force | Out-Null;"
        "Write-Output 'REGISTERED'"
    )
    pyw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    pyw = pyw if pyw.is_file() else (BASE_DIR / ".venv" / "Scripts"
                                     / "python.exe")
    args = task_argument(RUNNER_SCRIPT, subject)
    ps = (ps.replace("%PYW%", str(pyw)).replace("%ARGS%", args)
          .replace("%ROOT%", str(BASE_DIR)).replace("%MIN%", str(REPEAT_MINUTES))
          .replace("%NAME%", task_name))
    if not apply:
        q = subprocess.run(
            ["schtasks", "/Query", "/TN", task_name],
            capture_output=True, text=True)
        return {"ok": True, "applied": False,
                "already_installed": q.returncode == 0,
                "plan": {"task": task_name, "repeat_minutes": REPEAT_MINUTES,
                         "action": str(pyw), "argument": str(RUNNER_SCRIPT),
                         "subject": subject or "(all)",
                         "multiple_instances": "IgnoreNew"},
                "reason": ("would register a per-user repeating task; "
                           "`schtasks /Query` says installed=%s"
                           % (q.returncode == 0))}
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                        "Bypass", "-Command", ps],
                       capture_output=True, text=True)
    ok = r.returncode == 0 and "REGISTERED" in (r.stdout or "")
    return {"ok": bool(ok), "applied": True, "task": task_name,
            "stdout": (r.stdout or "").strip()[:200],
            "stderr": (r.stderr or "").strip()[:200]}


def _uninstall_one(task_name: str) -> dict[str, Any]:
    """Remove one task. Idempotent — a missing task is not an error."""
    r = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, text=True)
    return {"ok": r.returncode == 0, "task": task_name,
            "stdout": (r.stdout or "").strip()[:160],
            "stderr": (r.stderr or "").strip()[:160]}


def uninstall() -> dict[str, Any]:
    """Remove the WATCHDOG task (kept for compatibility)."""
    return _uninstall_one(TASK_NAME)


def status(task_name: str = TASK_NAME) -> dict[str, Any]:
    """Read back a registered task so the claim is CHECKABLE, not asserted."""
    r = subprocess.run(["schtasks", "/Query", "/TN", task_name, "/V", "/FO",
                        "LIST"], capture_output=True, text=True)
    installed = r.returncode == 0
    out = r.stdout or ""
    rep = ""
    for line in out.splitlines():
        if "Repeat" in line or "重複" in line:
            rep = line.strip()
            break
    return {"ok": True, "installed": installed, "task": task_name,
            "repeat": rep, "raw_tail": out.strip().splitlines()[-3:]}


ALL_TASKS = [(TASK_NAME, ""),
             (HEARTBEAT_TASK_NAME, "heartbeat")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="register the task(s) (default is a read-only dry run)")
    ap.add_argument("--uninstall", action="store_true",
                    help="remove the task(s)")
    ap.add_argument("--status", action="store_true", help="report the task(s)")
    ap.add_argument("--all", action="store_true",
                    help="act on BOTH components (watchdog + heartbeat)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    targets = ALL_TASKS if a.all else ALL_TASKS[:1]
    if a.status:
        outs = [status(name) for name, _ in targets]
    elif a.uninstall:
        outs = [_uninstall_one(name) for name, _ in targets]
    else:
        outs = [check_command(apply=a.apply, task_name=name, subject=subj)
                for name, subj in targets]
    if len(outs) == 1:
        out = outs[0]
    else:
        out = {"ok": all(o.get("ok") for o in outs), "tasks": outs}
    print(json.dumps(out, ensure_ascii=False, indent=2)
          if a.json else "\n".join(_human(o) for o in outs))
    return 0 if out.get("ok") else 1


def _human(out: dict[str, Any]) -> str:
    if "plan" in out:
        p = out["plan"]
        return ("DRY RUN — would register (nothing was written)\n"
                "  task    : %s\n  repeat  : every %s minutes\n"
                "  action  : %s\n  argument: %s\n  if running: %s\n"
                "  currently installed: %s"
                % (p["task"], p["repeat_minutes"], p["action"], p["argument"],
                   p["multiple_instances"], out.get("already_installed")))
    if "installed" in out:
        return ("task %s installed=%s\n  repeat: %s"
                % (out["task"], out["installed"], out.get("repeat") or "?"))
    return ("task %s ok=%s\n  %s"
            % (out.get("task"), out.get("ok"),
               out.get("stderr") or out.get("stdout") or ""))


if __name__ == "__main__":
    raise SystemExit(main())
