# -*- coding: utf-8 -*-
"""
Tool #7: MODEL = TOOL.MODEL.SELECT — pick which AI model works the task.

Python = state + detection + report SSOT. AHK (^!m) = input orchestration.

The model list is DYNAMIC (models change / get added), so the vision layer
(f_model_vision.py) reads the OPEN dropdown and matches the named row. This
state tool just remembers the TARGET model and cycles through a default list.

Modes:
  --check          : state file readable. exit 0 ok, 1 fail. No side effects.
  --get            : print current model JSON. exit 0.
  --set NAME       : set target model (free text, e.g. "Kimi K2.5"). exit 0.
  --cycle          : next model in MODEL_ORDER. Writes chat_model.json +
                     chat_model_last.txt (bare name for AHK). exit 0.
  --report [--api URL]
                   : doctor (vscode running? state file ok?) + current model
                     -> model_switch_report.json (+ optional POST). exit 0.

Cross-IDE design:
  - The model selector is VS Code chat-specific today.
  - State survives in chat_model.json.
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
STATE = os.path.join(BASE, "chat_model.json")
LAST = os.path.join(BASE, "chat_model_last.txt")
REPORT = os.path.join(BASE, "model_switch_report.json")
# Default model cycle (short match keys — f_model_vision matches by substring).
# Order = the order ^!m cycles through.
MODEL_ORDER = [
    "Qwen: Qwen3.8 27B",
    "Kimi K2.5",
    "DeepSeek V4 Flash",
    "Auto",
]
DEFAULT_MODEL = "Qwen: Qwen3.8 27B"


def log(msg):
    cdp_common.log(msg, tag="MODEL")


def _read():
    try:
        with open(STATE, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        if d.get("model"):
            return d
    except Exception:
        pass
    return {"model": DEFAULT_MODEL, "prev": None, "ts": None}


def _write(model, prev):
    d = {"model": model, "prev": prev,
         "ts": datetime.now().isoformat(timespec="seconds")}
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    with open(LAST, "w", encoding="utf-8") as f:
        f.write(model)
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
    ap.add_argument("--set", dest="set_model", default=None)
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

    if args.set_model:
        m = args.set_model.strip()
        if not m:
            print(json.dumps({"ok": False, "error": "empty model name"},
                             ensure_ascii=False))
            sys.exit(1)
        d = _write(m, _read()["model"])
        print(json.dumps(d, ensure_ascii=False))
        log("set: %s" % m)
        sys.exit(0)

    if args.cycle:
        cur = _read()["model"]
        try:
            idx = MODEL_ORDER.index(cur)
            nxt = MODEL_ORDER[(idx + 1) % len(MODEL_ORDER)]
        except ValueError:
            # current model not in the default list -> go to first
            nxt = MODEL_ORDER[0]
        d = _write(nxt, cur)
        print(json.dumps(d, ensure_ascii=False))
        log("cycle: %s -> %s" % (cur, nxt))
        sys.exit(0)

    if args.report:
        d = _read()
        rep = {"ok": True, "model": d["model"], "prev": d.get("prev"),
               "vscode_running": _vscode_running(),
               "state_file": os.path.exists(STATE),
               "timestamp": datetime.now().isoformat(timespec="seconds")}
        with open(REPORT, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2)
        log("report written: model=%s vscode=%s" % (rep["model"], rep["vscode_running"]))
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
