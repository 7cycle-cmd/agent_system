# -*- coding: utf-8 -*-
"""
Tool #4: F7 = TOOL.F7.VSCODE.ENV — VS Code environment preparation.

Python = detection + report SSOT. AHK (F7::) = input/launch orchestration
(AHK has SendInput rights; Python SendInput is blocked by the foreground
lock when run from a background terminal).

Modes:
  --check   : detect vscode + terminal, print JSON, exit 0/1/2. No side effects.
              0 = both ready, 1 = vscode off, 2 = terminal off.
  --report  : detect, write vscode_env_report.json, optional --api URL POST,
              print JSON, exit 0.

Detection (objective, process-tree based, no mouse):
  vscode ready  = a Code.exe process exists
  terminal ready= a shell process (powershell/pwsh/cmd/openconsole/conhost)
                  whose ancestor chain reaches Code.exe (integrated terminal)

AHK F7:: flow:
  1. RunWait python f7_vscode_env.py --check
  2. if exit 1 -> launch `code`, wait, re-check
  3. if exit 2 -> WinActivate VS Code, Send "^`", wait, re-check
  4. RunWait python f7_vscode_env.py --report   (writes JSON + API)
"""
import csv
import io
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

import cdp_common  # shared log()

BASE = r"C:\projects\agent_system"
REPORT = os.path.join(BASE, "vscode_env_report.json")
SHELL_NAMES = {"powershell.exe", "pwsh.exe", "cmd.exe", "openconsole.exe", "conhost.exe"}


def log(msg):
    cdp_common.log(msg, tag="F7")


# ---------------------------------------------------------------- processes
def get_processes():
    """Return {pid: (ppid, name)} for all processes (PowerShell, not toolhelp)."""
    out = subprocess.check_output(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Csv -NoTypeInformation"],
        text=True, timeout=30)
    procs = {}
    for r in csv.DictReader(io.StringIO(out)):
        if r.get("ProcessId"):
            procs[int(r["ProcessId"])] = (int(r["ParentProcessId"] or 0), (r.get("Name") or "").strip())
    return procs


def vscode_running(procs):
    return any(name.lower() == "code.exe" for _, name in procs.values())


def _has_code_ancestor(procs, pid):
    cur, seen = pid, set()
    while cur and cur not in seen:
        seen.add(cur)
        if cur not in procs:
            return False
        ppid, aname = procs[cur]
        if aname.lower() == "code.exe":
            return True
        cur = ppid
    return False


def terminal_open(procs):
    """True if any shell process has Code.exe in its ancestor chain."""
    for pid, (_, name) in procs.items():
        if name.lower() in SHELL_NAMES and _has_code_ancestor(procs, pid):
            return True
    return False


def detect():
    procs = get_processes()
    return {
        "vscode": "ready" if vscode_running(procs) else "not_ready",
        "terminal": "ready" if terminal_open(procs) else "not_ready",
    }


def main():
    args = sys.argv[1:]
    api_url = None
    if "--api" in args:
        api_url = args[args.index("--api") + 1]

    status = detect()
    report = dict(status)
    report["timestamp"] = datetime.now().isoformat(timespec="seconds")

    if "--check" in args:
        print(json.dumps(status))
        if status["vscode"] != "ready":
            sys.exit(1)
        if status["terminal"] != "ready":
            sys.exit(2)
        sys.exit(0)

    # --report (default)
    _finish(report, api_url)
    sys.exit(0)


def _finish(report, api_url):
    with open(REPORT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    log("report written: %s" % json.dumps(report))
    print(json.dumps(report))
    if api_url:
        try:
            req = urllib.request.Request(
                api_url, data=json.dumps(report).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                log("API POST %s -> HTTP %d" % (api_url, resp.status))
        except Exception as e:
            log("WARN: API POST failed: %s" % e)


if __name__ == "__main__":
    main()
