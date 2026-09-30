# -*- coding: utf-8 -*-
"""
Tool #6: MODE = TOOL.MODE.SWITCH — cross-IDE chat mode switch (ASK/PLAN/AGENT).

Python = state + detection + report SSOT. AHK (^!p / ^!o) = input orchestration.

Default mode = PLAN for all preparation (any IDE; VS Code today).

Modes:
  --check          : state file readable. exit 0 ok, 1 fail. No side effects.
  --get            : print current mode JSON. exit 0.
  --set MODE       : set mode (ASK/PLAN/AGENT). exit 0 ok, 1 bad mode.
  --cycle          : ASK -> PLAN -> AGENT -> ASK. Writes chat_mode.json +
                     chat_mode_last.txt (bare mode word for AHK). exit 0.
  --report [--api URL]
                   : doctor (vscode running? state file ok?) + current mode
                     -> mode_switch_report.json (+ optional POST). exit 0.

Cross-IDE design:
  - Prompt-level token (#MODE:ASK/PLAN/AGENT) works in ANY IDE / LLM chat.
  - IDE-native keys (VS Code: ^l=Ask, ^i=Agent) are sent by the AHK layer.
  - If the IDE's mode selector disappears (known flaky bug), ^!o restarts
    the IDE (user-verified fix); mode state survives in chat_mode.json.
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime

import cdp_common  # shared log()

BASE = r"C:\projects\agent_system"
STATE = os.path.join(BASE, "chat_mode.json")
LAST = os.path.join(BASE, "chat_mode_last.txt")
REPORT = os.path.join(BASE, "mode_switch_report.json")
ORDER = ["ASK", "PLAN", "AGENT"]
# Default mode for ALL preparation (any IDE; VS Code today). PLAN first.
DEFAULT_MODE = "PLAN"


def log(msg):
    cdp_common.log(msg, tag="MODE")


def _read():
    try:
        # utf-8-sig: tolerate a BOM (e.g. if the file was ever written by
        # PowerShell Set-Content -Encoding UTF8, which prepends a BOM and
        # would otherwise make json.load() fail -> silent fallback to PLAN).
        with open(STATE, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if d.get("mode") in ORDER:
            return d
    except Exception:
        pass
    return {"mode": DEFAULT_MODE, "prev": None, "ts": None}


def _write(mode, prev):
    d = {"mode": mode, "prev": prev,
         "ts": datetime.now().isoformat(timespec="seconds")}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    with open(LAST, "w", encoding="utf-8") as f:
        f.write(mode)
    return d


def _vscode_running():
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq Code.exe", "/NH"],
            text=True, timeout=10)
        return "Code.exe" in out
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--get", action="store_true")
    ap.add_argument("--set", dest="set_mode", default=None)
    ap.add_argument("--cycle", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--api", default=None)
    args = ap.parse_args()

    if args.check:
        ok = False
        try:
            _read()
            ok = True
        except Exception:
            ok = False
        print(json.dumps({"ok": ok, "state": STATE}, ensure_ascii=False))
        log("check: ok=%s" % ok)
        sys.exit(0 if ok else 1)

    if args.get:
        print(json.dumps(_read(), ensure_ascii=False))
        sys.exit(0)

    if args.set_mode:
        m = args.set_mode.strip().upper()
        if m not in ORDER:
            print(json.dumps({"ok": False, "error": "mode must be ASK/PLAN/AGENT"},
                             ensure_ascii=False))
            log("set FAIL: bad mode %s" % args.set_mode)
            sys.exit(1)
        d = _write(m, _read()["mode"])
        print(json.dumps(d, ensure_ascii=False))
        log("set: %s" % m)
        sys.exit(0)

    if args.cycle:
        cur = _read()["mode"]
        nxt = ORDER[(ORDER.index(cur) + 1) % len(ORDER)]
        d = _write(nxt, cur)
        print(json.dumps(d, ensure_ascii=False))
        log("cycle: %s -> %s" % (cur, nxt))
        sys.exit(0)

    if args.report:
        d = _read()
        rep = {"ok": True, "mode": d["mode"], "prev": d.get("prev"),
               "vscode_running": _vscode_running(),
               "state_file": os.path.exists(STATE),
               "timestamp": datetime.now().isoformat(timespec="seconds")}
        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        log("report written: mode=%s vscode=%s" % (rep["mode"], rep["vscode_running"]))
        if args.api:
            try:
                req = urllib.request.Request(
                    args.api, data=json.dumps(rep).encode("utf-8"),
                    headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=15) as r:
                    log("api POST %s -> %s" % (args.api, r.status))
            except Exception as e:
                log("api POST failed: %s" % e)
        print(json.dumps(rep, ensure_ascii=False))
        sys.exit(0)

    ap.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
