# install_skill_tick.py
"""Register the ONE scheduled task that drives the pipeline (plan_AUTO.FOREVER.SKILL.FLOW).

MODELLED ON THE TWO THAT ALREADY WORK
-------------------------------------
`install_watchdog_keepalive.py` registers `AgentSystemWatchdogKeepAlive` and
`AgentSystemHeartbeatKeepAlive`. Both are installed and firing every 5 minutes -- that is the
repo's ONE proven auto-forever surface, so its shape is copied rather than invented:
`New-ScheduledTaskTrigger -Once -At (now+1min) -RepetitionInterval <span>`, queried with
`schtasks /Query /TN <name>`.

WHY ONE TASK AND NOT FOUR
-------------------------
Four tasks (one per link) would make "which link is behind?" unanswerable -- each would have
its own last-run and its own failure mode, and the REVIEW surface could only ever report four
independent mysteries. ONE tick runs the links in the order declared in the conductor flow,
and its log answers the question in one place.

DRY RUN BY DEFAULT
------------------
`apply=False` runs `schtasks /Query` ONLY, so planning cannot register anything by accident.
That is the same guard the keepalive installer uses.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

TASK_NAME = "AgentSystemSkillTick"
# LONGER THAN LINK 1, AND FOR A MEASURED REASON. Link 1 ticks every 5 minutes because the
# watchdog's own log shows several events per minute around a restart -- a fast unit for a
# fast signal. This pipeline's links are slow: a harvest on a quiet repo has NOTHING to
# record, and a rewrite must not fire twelve times an hour on the same lessons. 15 minutes
# keeps "it fired" frequent enough to notice, and "it had work" rare enough to mean something.
REPEAT_MINUTES = 15
ACTION = "wscript.exe"
ARGUMENT = str(BASE_DIR / "_run_skill_tick_hidden.vbs")


def _plan(task_name: str = TASK_NAME) -> dict:
    return {
        "task": task_name,
        "repeat_minutes": REPEAT_MINUTES,
        "action": ACTION,
        "argument": ARGUMENT,
        "runs": "python skill_tick.py --once --apply",
        "why_one_task": ("ONE tick; four tasks would make 'which link is behind?' "
                         "unanswerable"),
        "never": "this task never kills or restarts anything",
    }


def check_command(*, apply: bool = False, task_name: str = TASK_NAME) -> dict:
    """`apply=False` -> QUERY only. `apply=True` -> register the repeating task."""
    if not apply:
        r = subprocess.run(["schtasks", "/Query", "/TN", task_name],
                           capture_output=True, text=True, errors="replace")
        return {"ok": True, "applied": False, "installed": r.returncode == 0,
                "plan": _plan(task_name),
                "cite": "`schtasks /Query` says installed=%s" % (r.returncode == 0)}

    script = (
        "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
        "-RepetitionInterval (New-TimeSpan -Minutes %MIN%); "
        "$a = New-ScheduledTaskAction -Execute '%ACT%' -Argument '%ARG%'; "
        "Register-ScheduledTask -TaskName '%NAME%' -Trigger $t -Action $a -Force"
    ).replace("%MIN%", str(REPEAT_MINUTES)).replace("%ACT%", ACTION) \
     .replace("%ARG%", ARGUMENT).replace("%NAME%", task_name)
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, text=True, errors="replace")
    ok = r.returncode == 0
    # THE PLAN IS INCLUDED ON BOTH PATHS. MEASURED BUG 2026-09-24: the apply branch omitted
    # `plan`, and `main()` reads `out["plan"]` unconditionally, so `--apply` raised
    # KeyError AFTER the task had already been registered -- the report crashed while the
    # work had succeeded. That is the whole reason the dry path was tested and this one was
    # not: nothing exercised `apply=True`. A branch that has never run is not a branch.
    return {"ok": ok, "applied": ok, "task": task_name, "repeat_minutes": REPEAT_MINUTES,
            "plan": _plan(task_name), "installed": ok,
            "stderr": (r.stderr or "")[-300:]}


def uninstall(task_name: str = TASK_NAME) -> dict:
    r = subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, text=True, errors="replace")
    return {"ok": r.returncode == 0, "task": task_name, "removed": r.returncode == 0}


def status(task_name: str = TASK_NAME) -> dict:
    r = subprocess.run(["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"],
                       capture_output=True, text=True, errors="replace")
    return {"ok": True, "installed": r.returncode == 0, "task": task_name,
            "raw": (r.stdout or "")[:400]}


def main() -> int:
    ap = argparse.ArgumentParser(description="install the ONE skill-tick task")
    ap.add_argument("--apply", action="store_true", help="actually register (default: query)")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    if args.uninstall:
        print("  %s" % uninstall())
        return 0
    out = check_command(apply=args.apply)
    p = out["plan"]
    print("  task    : %s" % p["task"])
    print("  repeat  : every %s minutes" % p["repeat_minutes"])
    print("  runs    : %s" % p["runs"])
    print("  why one : %s" % p["why_one_task"])
    print("  applied : %s | installed: %s" % (out["applied"], out.get("installed")))
    print("  %s" % out.get("cite", ""))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())