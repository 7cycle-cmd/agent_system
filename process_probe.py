"""process_probe.py -- "how many instances of THIS process name are running?"

WHY THIS MODULE EXISTS (MEASURED 2026-09-24)
--------------------------------------------
`user_asset` reported VS Code and Chrome as `installed` / "process not running"
while BOTH were running. MEASURED at the same instant:

    app        user_asset says   the truth
    vscode     not running       RUNNING (20 instances)
    chrome     not running       RUNNING (17 instances)

ROOT CAUSE, MEASURED: `skill_library_api.sync_user_assets` called
`openclaw_settings.is_process_running()` ONCE, outside its loop, and compared
that ONE result against every app's `process_name`:

    proc = ocs.is_process_running()          # an OPENCLAW-ONLY probe
    running = bool(proc.get("running")
                   and str(proc.get("name") or "").lower()
                   == str(a.get("process_name") or "").lower())

`ocs.is_process_running()` searches ONLY
`OPENCLAW_PROCESS_NAMES = ("OpenClaw.Tray.WinUI", "OpenClawTray",
"OpenClaw Companion")`, so the comparison can be true for `openclaw` and for
NOTHING ELSE. For `vscode` it compared `'openclaw.tray.winui' == 'code'`.

THE PROBE WAS NOT BROKEN -- IT WAS ANSWERING A DIFFERENT QUESTION. It answers
"is OpenClaw running"; the caller read it as "is THIS app running". So this
module answers the question the caller actually had, PER NAME.

THE SOURCE
----------
`Win32_PerfFormattedData_PerfProc_Process` -- the same counters Task Manager
shows, so the answer agrees with what a human sees in Task Manager. It also
carries CPU / memory / IO, which is why it is preferred over `tasklist`.

MEASURED TRAP: its `Name` carries an INSTANCE SUFFIX -- `Code#12`, `Doubao#5`.
The suffix is an instance index, NOT part of the process name, so it must be
stripped before matching. Matching `Code` against `Code#12` without stripping
would report ZERO instances for a running app.

NEVER RAISES
------------
A probe that can raise would turn a status read into an outage. Every failure is
returned as `ok: False` with a `why`, so a failure is VISIBLE rather than
indistinguishable from "not running".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any

# The PowerShell query. `-NoProfile` keeps it fast and free of a user profile's
# side effects; `ConvertTo-Json -Compress` keeps the payload small.
_PS_QUERY = (
    "Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | "
    "Where-Object { $_.Name -notin @('_Total','Idle') } | "
    "Select-Object Name, IDProcess, PercentProcessorTime, WorkingSetPrivate | "
    "ConvertTo-Json -Compress"
)

# A process name is matched case-insensitively; Windows is case-insensitive here.
_TIMEOUT_SEC = 20


def _base_name(name: str) -> str:
    """The process name with its INSTANCE SUFFIX removed.

    MEASURED: `Win32_PerfFormattedData_PerfProc_Process.Name` is `Code#12` for
    the 12th instance of `Code.exe`. The `#12` is an instance index, not part of
    the name, so `Code` must be compared against `Code`, not against `Code#12`.
    """
    return str(name or "").split("#")[0].strip()


def _run_query() -> tuple[list[dict[str, Any]], str]:
    """Run the counter query. Returns (rows, error). Never raises."""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_QUERY],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=_TIMEOUT_SEC,
        )
    except Exception as exc:
        return [], "%s: %s" % (type(exc).__name__, exc)
    out = (res.stdout or "").strip()
    if not out:
        return [], "the counter query returned nothing (stderr: %s)" % (
            (res.stderr or "").strip()[:200] or "-")
    try:
        data = json.loads(out)
    except Exception as exc:
        return [], "the counter query returned non-JSON: %s" % exc
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)], ""


def snapshot() -> dict[str, Any]:
    """Every running process, grouped by BASE name. Never raises.

    Returns `{ok, by_name, total, error}`. `by_name` maps a base name to the
    list of its instances, so a caller can ask for a count AND for the resource
    figures of the same instances.
    """
    rows, err = _run_query()
    if err:
        return {"ok": False, "by_name": {}, "total": 0, "error": err}
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        base = _base_name(str(r.get("Name") or ""))
        if not base:
            continue
        by.setdefault(base.lower(), []).append({
            "name": base,
            "pid": r.get("IDProcess"),
            "cpu_percent": r.get("PercentProcessorTime"),
            "mem_private": r.get("WorkingSetPrivate"),
        })
    return {"ok": True, "by_name": by, "total": len(rows), "error": ""}


def instances(process_name: str, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """How many instances of ONE process name are running. Never raises.

    `process_name` is the name WITHOUT `.exe` (e.g. `Code`, `chrome`), which is
    what `app.process_name` stores. A `.exe` suffix is tolerated and stripped,
    because a caller writing `Code.exe` should not silently get zero.
    """
    want = _base_name(str(process_name or ""))
    if want.lower().endswith(".exe"):
        want = want[:-4]
    if not want:
        return {"ok": False, "process_name": "", "count": 0,
                "running": False, "why": "process_name is required"}
    s = snap if snap is not None else snapshot()
    if not s.get("ok"):
        return {"ok": False, "process_name": want, "count": 0, "running": False,
                "why": s.get("error") or "the snapshot failed"}
    hits = s["by_name"].get(want.lower(), [])
    return {
        "ok": True,
        "process_name": want,
        "count": len(hits),
        "running": len(hits) > 0,
        "instances": hits,
        "why": "%d instance(s) of `%s`" % (len(hits), want),
    }


def probe_all(names: list[str]) -> dict[str, Any]:
    """Probe MANY names from ONE snapshot. Never raises.

    ONE snapshot for many names, because the counter query costs ~1s and asking
    it once per app would make a sync of N apps cost N seconds.
    """
    s = snapshot()
    out: dict[str, Any] = {}
    for n in names:
        out[str(n)] = instances(n, snap=s)
    return {"ok": bool(s.get("ok")), "probes": out,
            "error": s.get("error") or "", "total_processes": s.get("total", 0)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", default="", help="one process name, e.g. Code")
    ap.add_argument("--all", action="store_true", help="probe every app row")
    args = ap.parse_args(argv)

    if args.probe:
        print(json.dumps(instances(args.probe), indent=2, ensure_ascii=False))
        return 0
    if args.all:
        import sqlite3
        from pathlib import Path

        db = Path(__file__).resolve().parent / "agent.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            names = [str(r["process_name"]) for r in conn.execute(
                "SELECT process_name FROM app WHERE is_active=1 "
                "AND process_name IS NOT NULL AND process_name <> ''")]
        finally:
            conn.close()
        print(json.dumps(probe_all(names), indent=2, ensure_ascii=False))
        return 0
    s = snapshot()
    print(json.dumps({"ok": s["ok"], "total_processes": s["total"],
                      "distinct_names": len(s["by_name"]),
                      "error": s["error"]}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
