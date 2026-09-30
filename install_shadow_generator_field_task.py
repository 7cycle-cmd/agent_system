"""INSTALL the 5-minute shadow task. QUERY by default; never acts on a dry run.

WHY THIS EXISTS (the human, 2026-09-28), verbatim
------------------------------------------------
    "加一個背景重複跑嘅影子 task（例如每 5 分鐘一輪），累積到 N 輪全綠才刪。"

THE LOOP IS THE SCHEDULER'S, NOT A `while True`
----------------------------------------------
`_shadow_generator_field_task.py` does ONE round and exits; the repetition is a
Windows scheduled task. MEASURED repo lesson: `watchdog_health.py:614` records
the `while True:` incident and `_proof_code_scan.py:212` asserts a daemon file
has none. A one-shot runner also means a crash costs ONE round instead of the
whole window.

THE PATTERN IS COPIED FROM `install_watchdog_keepalive.py`, INCLUDING ITS
MEASURED FIX: `-RepetitionDuration` is 3650 days, NOT `[TimeSpan]::MaxValue`.
MEASURED there: the Task Scheduler XML schema REJECTS MaxValue with
"(8,42):Duration: P99999999DT23H59M59S" and the task never registers. A finite
horizon is required by the schema.

`MultipleInstances IgnoreNew` IS NOT DECORATION: the shadow round opens the SPA
database, and two rounds overlapping would make the comparison and the ledger
race each other. It is the setting that makes the cadence safe.

A DRY RUN STAYS DRY: `--apply` is required to register anything, and the default
path runs `schtasks /Query` only -- the same rule the rest of this repo follows.

Run:
    .\\.venv\\Scripts\\python.exe install_shadow_generator_field_task.py
    .\\.venv\\Scripts\\python.exe install_shadow_generator_field_task.py --apply
    .\\.venv\\Scripts\\python.exe install_shadow_generator_field_task.py --remove
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
TASK_NAME = "AgentSystemShadowGeneratorField"
RUNNER = BASE_DIR / "_shadow_generator_field_task.py"
REPEAT_MINUTES = 5

PS_TEMPLATE = (
    "$ErrorActionPreference='Stop';"
    "$a = New-ScheduledTaskAction -Execute '%PYW%' -Argument '%ARGS%' "
    "-WorkingDirectory '%ROOT%';"
    "$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) "
    "-RepetitionInterval (New-TimeSpan -Minutes %MIN%) "
    # 3650 days rather than [TimeSpan]::MaxValue -- MEASURED: the schema refuses
    # MaxValue and the task never registers.
    "-RepetitionDuration (New-TimeSpan -Days 3650);"
    "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
    "-DontStopIfGoingOnBatteries -StartWhenAvailable "
    "-MultipleInstances IgnoreNew;"
    "Register-ScheduledTask -TaskName '%NAME%' -Action $a -Trigger $t "
    "-Settings $s -Force | Out-Null;"
    "Write-Output 'REGISTERED'"
)


def _pythonw() -> Path:
    """`pythonw.exe` so a 5-minute tick does not flash a console window.

    MEASURED fallback: `python.exe` when `pythonw.exe` is absent, because a task
    that silently fails to start is worse than a visible window.
    """
    pyw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    return pyw if pyw.is_file() else (BASE_DIR / ".venv" / "Scripts" / "python.exe")


def plan(task_name: str = TASK_NAME) -> dict:
    return {
        "task": task_name,
        "repeat_minutes": REPEAT_MINUTES,
        "action": str(_pythonw()),
        "argument": '"%s" --once' % RUNNER,
        "working_directory": str(BASE_DIR),
        "multiple_instances": "IgnoreNew",
        "runs": ("one shadow round, appended to "
                 "qc_evidence/shadow_rounds.jsonl"),
        "never": ("this task NEVER drops a table and never kills a process; a "
                  "human performs the drop after --status reports ready"),
    }


def check_command(*, apply: bool = False, remove: bool = False,
                  task_name: str = TASK_NAME) -> dict:
    """`apply=False` -> `schtasks /Query` ONLY, so a dry run cannot register."""
    if remove and not apply:
        return {"ok": True, "applied": False, "removed": False, "plan": plan(task_name),
                "reason": "a dry run stays dry; pass --apply --remove to remove"}
    if remove:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Unregister-ScheduledTask -TaskName '%s' -Confirm:$false" % task_name],
            capture_output=True, text=True, errors="replace")
        return {"ok": r.returncode == 0, "applied": True, "removed": True,
                "plan": plan(task_name), "stderr": r.stderr[-300:]}
    if not apply:
        q = subprocess.run(["schtasks", "/Query", "/TN", task_name],
                           capture_output=True, text=True, errors="replace")
        return {"ok": True, "applied": False,
                "already_installed": q.returncode == 0,
                "plan": plan(task_name),
                "cite": "`schtasks /Query` says installed=%s" % (q.returncode == 0)}
    ps = (PS_TEMPLATE.replace("%PYW%", str(_pythonw()))
          .replace("%ARGS%", '"%s" --once' % RUNNER)
          .replace("%ROOT%", str(BASE_DIR))
          .replace("%MIN%", str(REPEAT_MINUTES))
          .replace("%NAME%", task_name))
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, errors="replace")
    ok = r.returncode == 0 and "REGISTERED" in (r.stdout or "")
    # THE PLAN IS INCLUDED ON BOTH PATHS. MEASURED BUG in
    # `install_skill_tick.py`: the apply branch omitted `plan` while the report
    # read it unconditionally, so `--apply` raised KeyError AFTER the task had
    # registered -- the report crashed while the work had succeeded.
    out = {"ok": ok, "applied": True, "plan": plan(task_name),
           "stdout": (r.stdout or "").strip()[-200:],
           "stderr": (r.stderr or "").strip()[-300:]}
    if ok:
        q = subprocess.run(["schtasks", "/Query", "/TN", task_name],
                           capture_output=True, text=True, errors="replace")
        out["installed_verified"] = q.returncode == 0
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually register (default is a QUERY only)")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--task-name", default=TASK_NAME)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    out = check_command(apply=args.apply, remove=args.remove,
                        task_name=args.task_name)
    if args.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0 if out.get("ok") else 1
    print("   task            %s" % out["plan"]["task"])
    print("   every           %d minutes" % out["plan"]["repeat_minutes"])
    print("   action          %s" % out["plan"]["action"])
    print("   argument        %s" % out["plan"]["argument"])
    print("   instances       %s" % out["plan"]["multiple_instances"])
    print("   runs            %s" % out["plan"]["runs"])
    print("   never           %s" % out["plan"]["never"])
    print()
    if out.get("removed"):
        print("   REMOVED: %s" % out["ok"])
    elif out.get("applied"):
        print("   REGISTERED: %s   verified by schtasks: %s"
              % (out["ok"], out.get("installed_verified")))
        if out.get("stderr"):
            print("   stderr: %s" % out["stderr"])
    else:
        print("   dry run (nothing registered). already installed: %s"
              % out.get("already_installed"))
        print("   %s" % out.get("cite", ""))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())