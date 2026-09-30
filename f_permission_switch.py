# -*- coding: utf-8 -*-
"""
Tool #9: PERMISSION = TOOL.PERMISSION.SWITCH — VS Code Copilot permission switch.

Python = state + report SSOT. AHK (^!l) = multi-press input orchestration.

Multi-press mapping (user spec 2026-09-18, same 2s-window pattern as MODE tool):
  1 press = Default permissions
  2 presses = Sandboxing for terminal
  3 presses = Allow all (auto-approve)
  4 presses = Autopilot (Preview)
  5+ wrap: Mod(count-1, 4) + 1

Modes:
  --check          : state file readable. exit 0 ok, 1 fail. No side effects.
  --get            : print current permission JSON. exit 0.
  --set NAME       : set permission (default|sandbox|allow_all|autopilot).
                     exit 0 ok, 1 bad name.
  --cycle          : default -> sandbox -> allow_all -> autopilot -> default.
                     exit 0.
  --report [--api URL]
                   : doctor (vscode running? state ok?) + current permission
                     -> permission_switch_report.json (+ optional POST). exit 0.

State SSOT: chat_permission.json (survives restart).
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
STATE = os.path.join(BASE, "chat_permission.json")
LAST = os.path.join(BASE, "chat_permission_last.txt")
REPORT = os.path.join(BASE, "permission_switch_report.json")

# press-count -> permission (1-based)
ORDER = ["default", "sandbox", "allow_all", "autopilot"]
LABELS = {
    "default": "Default permissions",
    "sandbox": "Sandboxing for terminal",
    "allow_all": "Allow all (auto-approve)",
    "autopilot": "Autopilot (Preview)",
}
DEFAULT_PERM = "default"


def log(msg):
    cdp_common.log(msg, tag="PERM")


def _read():
    try:
        with open(STATE, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if d.get("permission") in ORDER:
            return d
    except Exception:
        pass
    return {"permission": DEFAULT_PERM, "prev": None, "ts": None}


def _write(perm, prev):
    d = {"permission": perm, "prev": prev,
         "ts": datetime.now().isoformat(timespec="seconds")}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    with open(LAST, "w", encoding="utf-8") as f:
        f.write(perm)
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
    ap.add_argument("--set", dest="set_perm", default=None)
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
        d = _read()
        d["label"] = LABELS[d["permission"]]
        print(json.dumps(d, ensure_ascii=False))
        sys.exit(0)

    if args.set_perm:
        p = args.set_perm.strip().lower()
        if p not in ORDER:
            print(json.dumps({"ok": False,
                              "error": "permission must be one of %s" % ORDER},
                             ensure_ascii=False))
            log("set FAIL: bad permission %s" % args.set_perm)
            sys.exit(1)
        d = _write(p, _read()["permission"])
        d["label"] = LABELS[p]
        print(json.dumps(d, ensure_ascii=False))
        log("set: %s (%s)" % (p, LABELS[p]))
        sys.exit(0)

    if args.cycle:
        cur = _read()["permission"]
        nxt = ORDER[(ORDER.index(cur) + 1) % len(ORDER)]
        d = _write(nxt, cur)
        d["label"] = LABELS[nxt]
        print(json.dumps(d, ensure_ascii=False))
        log("cycle: %s -> %s" % (cur, nxt))
        sys.exit(0)

    if args.report:
        d = _read()
        report = {
            "ok": True,
            "vscode_running": _vscode_running(),
            "permission": d["permission"],
            "label": LABELS[d["permission"]],
            "prev": d.get("prev"),
            "ts": d.get("ts"),
            "state_file": STATE,
        }
        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        if args.api:
            try:
                req = urllib.request.Request(
                    args.api,
                    data=json.dumps(report).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST")
                urllib.request.urlopen(req, timeout=10)
                log("report: POST %s ok" % args.api)
            except Exception as e:
                log("report: POST FAIL %s" % e)
        print(json.dumps(report, ensure_ascii=False))
        log("report: ok permission=%s" % d["permission"])
        sys.exit(0)

    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
