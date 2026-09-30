"""keepalive_runner.py — the action the keep-alive task runs. SAFE IF RUNNING.

WHY THIS EXISTS
---------------
`install_watchdog_keepalive.py` registers a per-user repeating task pointing at
this file. The task fires every 5 minutes; this is what each firing does.

IT IS A GUARD, NOT A RESTARTER. Its whole job is:

  1. measure whether the watchdog is ALIVE, using the ONE unit from
     `watchdog_health.py` (seconds since the newest event in
     `helper_watchdog_events.json`) — never a second definition of "alive";
  2. if ALIVE -> do NOTHING and say so;
  3. if DEAD  -> START it, and only ever start.

MEASURED WHY (3), AND WHY IT IS NOT A STYLE CHOICE. `helper_watchdog.py:651`:

    Never kill helpers here. Duplicate prevention is helper's own mutex +
    skip-start-if-healthy. Killing from adopt caused false downs.

`start_llm_bg_hidden.vbs` already follows the same rule — it checks the process
table and quits if a watchdog is running. A keep-alive that force-killed a live
watchdog "to be sure" would MANUFACTURE the false downs that lesson is about, and
on a 5-minute tick it would do so repeatedly.

WHY "ALIVE" IS THE FILE AND NOT THE PROCESS TABLE. Measured: two
`mouse_spot_helper.py` processes were running while NO watchdog process existed at
all. A process-table check alone would have said "something is running". The
freshness unit answers the question that matters — *is it still RECORDING?* — and
a watchdog wedged without writing is exactly the case a process check misses.

The run is logged to `watchdog_keepalive_log.jsonl` so "did the tick fire?" is a
fact, not a hope. A missing log would make "the task never ran" and "the task ran
and found everything healthy" indistinguishable — the defect this plan is about.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOG_FILE = BASE_DIR / "watchdog_keepalive_log.jsonl"
WATCHDOG_SCRIPT = BASE_DIR / "helper_watchdog.py"

# THE SECOND SUPERVISED COMPONENT. MEASURED 2026-09-24: `worker_heartbeat_service`
# was NOT started by anything — `Get-ScheduledTask` and the Startup folder both
# showed no entry for it, and `worker_heartbeat`'s newest row was
# `2026-09-14 11:35:11` (9.7 days). It is the SAME fault the watchdog had: a
# component with no supervisor, so its death is invisible until someone reads a
# timestamp.
#
# ONE RUNNER, TWO COMPONENTS, because the DECISION is identical: measure the
# declared liveness unit, start only if not ALIVE, never kill. A second runner
# file would be a second copy of that policy.
SUPERVISED: dict[str, dict[str, Any]] = {
    "watchdog": {
        "script": "helper_watchdog.py",
        "process_marker": "helper_watchdog.py",
        "why": "keeps Mouse Spot Helper alive on :18765",
    },
    "heartbeat": {
        "script": "worker_heartbeat_service.py",
        "process_marker": "worker_heartbeat_service.py",
        "why": "writes the worker liveness row the health unit reads",
    },
}
MAX_LOG_LINES = 500


def _is_running_by_process(marker: str = "helper_watchdog.py") -> tuple[bool, list[int]]:
    """Is a process whose command line contains `marker` running?"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object { "
             "\"$($_.ProcessId)|$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=20)
        out = r.stdout or ""
    except Exception:
        return False, []
    return _parse_processes(out, marker)


def _parse_processes(ps_output: str,
                     marker: str = "helper_watchdog.py") -> tuple[bool, list[int]]:
    """The decision input, parsed from raw process text.

    Split out so a proof can supply a FIXTURE instead of the live process table —
    a check whose result changes when an unrelated program starts is not a test.
    """
    pids: list[int] = []
    for line in str(ps_output or "").splitlines():
        if marker not in line:
            continue
        head = line.split("|", 1)[0].strip()
        if head.isdigit():
            pids.append(int(head))
    return bool(pids), sorted(pids)


def decide(*, alive_by_log: bool, running_by_process: bool,
           subject: str = "watchdog") -> dict[str, Any]:
    """The decision, as a PURE function so a proof can test every branch.

    Kept separate from any side effect on purpose: the interesting question is
    "what would it DO?", and that must be testable without starting anything.
    """
    if alive_by_log:
        return {"action": "none",
                "why": "%s is ALIVE (recent evidence)" % subject}
    if running_by_process:
        # A process exists but has stopped writing. STARTING a second one would
        # duplicate; KILLING it would cause a false down. So it is REPORTED, and
        # left to the operator — a stuck process is a different fault from a dead
        # one and must not be papered over by a kill.
        return {"action": "report_wedged",
                "why": ("a %s process is running but has written no recent "
                        "evidence — this is a WEDGED component, not a missing "
                        "one; starting another would duplicate and killing would "
                        "cause a false down" % subject)}
    return {"action": "start", "why": "no recent evidence AND no process"}


def _start_component(script: str) -> dict[str, Any]:
    """Start a component HIDDEN. Starts only — never kills."""
    pyw = BASE_DIR / ".venv" / "Scripts" / "pythonw.exe"
    runner = pyw if pyw.is_file() else (BASE_DIR / ".venv" / "Scripts"
                                        / "python.exe")
    target = BASE_DIR / script
    if not target.is_file():
        return {"ok": False, "reason": "%s not found" % script}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Start-Process -FilePath '%s' -ArgumentList '%s' "
             "-WorkingDirectory '%s' -WindowStyle Hidden"
             % (runner, target, BASE_DIR)],
            capture_output=True, text=True, timeout=30)
        return {"ok": r.returncode == 0, "runner": str(runner),
                "stderr": (r.stderr or "").strip()[:160]}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)[:160]}


def _append_log(entry: dict[str, Any]) -> None:
    """Append one JSON line, pruning to `MAX_LOG_LINES`."""
    try:
        lines: list[str] = []
        if LOG_FILE.is_file():
            lines = [ln for ln in LOG_FILE.read_text(
                encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        lines.append(json.dumps(entry, ensure_ascii=False))
        if len(lines) > MAX_LOG_LINES:
            lines = lines[-MAX_LOG_LINES:]
        LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        # A LOG FAILURE MUST NOT STOP THE KEEP-ALIVE: the action (starting the
        # watchdog) matters more than the record of it. Unlike `worker_help.py`'s
        # DB-first rule, here the side effect IS the point.
        pass


def run_once(*, apply: bool = True, now: str | None = None,
             ps_output: str | None = None,
             subjects: list[str] | None = None) -> dict[str, Any]:
    """One tick. Measures EVERY supervised component, decides, and (when
    `apply`) acts. Never kills.

    `ps_output` lets a proof supply a fixture process list instead of the live
    table, for the same determinism reason as `now`.
    """
    import watchdog_health as wh

    names = subjects or list(SUPERVISED)
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
    conn.row_factory = sqlite3.Row
    try:
        fresh = {n: wh.freshness(conn, n, 1, now=now) for n in names}
    finally:
        conn.close()
    # ONE process-table read per tick, parsed per marker: two ticks would double
    # the cost for no new information.
    if ps_output is None:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | ForEach-Object { "
                 "\"$($_.ProcessId)|$($_.CommandLine)\" }"],
                capture_output=True, text=True, timeout=20)
            ps_output = r.stdout or ""
        except Exception:
            ps_output = ""
    results: list[dict[str, Any]] = []
    for n in names:
        spec = SUPERVISED[n]
        running, pids = _parse_processes(ps_output, spec["process_marker"])
        d = decide(alive_by_log=fresh[n]["state"] == wh.ALIVE,
                   running_by_process=running, subject=n)
        started = False
        if d["action"] == "start" and apply:
            started = bool(_start_component(spec["script"]).get("ok"))
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "subject": n, "state": fresh[n]["state"],
                 "age_seconds": (None if fresh[n]["seconds"] is None
                                 else round(fresh[n]["seconds"], 1)),
                 "unit": fresh[n]["unit"], "process_running": running,
                 "pids": pids, "action": d["action"], "why": d["why"],
                 "started": started}
        if apply:
            _append_log(entry)
        results.append(entry)
    worst = [e for e in results if e["action"] != "none"]
    traced: list[dict[str, Any]] = []
    if apply and worst:
        # ---- THE TRACE WRITE (plan RUNTIME.TRACE.CYCLE, QC-01/QC-03) --------
        # MEASURED before this: a tick that decided `report_wedged` appended a
        # LINE to a log and returned. Nothing reached the DB, so a WEDGED
        # component left no row a reader could query — the fault was real and
        # the trace was empty. This writes it.
        #
        # `apply=False` (the PROOF's mode) writes NOTHING, so a proof stays
        # read-only and the live DB is untouched (the "a proof runs on a COPY"
        # rule the runner also honours by only tracing when it acts).
        traced = _record_traces(results)
    return {"ok": not worst, "results": results,
            "states": {e["subject"]: e["state"] for e in results},
            "actions": {e["subject"]: e["action"] for e in results},
            "started": [e["subject"] for e in results if e["started"]],
            "traced": traced,
            "applied": bool(apply)}


def _record_traces(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Persist the tick: a fault row per bad subject, a RESOLVE per recovered one.

    IT GOES THROUGH `record_alarm`, NOT `record_fault` DIRECTLY, and that is the
    whole point of this change: `record_alarm` DEDUPES the fault row (one open row
    per ongoing fault) and THROTTLES the chat report (one per window), so a tick
    that still sees the same wedged component no longer grows the tables without
    bound. MEASURED before: 7 `fault_event` rows for one ongoing condition.

    A RECOVERED subject CLOSES its open fault, so the lifecycle is written and a
    later recurrence legitimately opens a new row.

    BEST-EFFORT ON PURPOSE: the keep-alive's PRIMARY job is to keep the component
    alive, and a trace write that raised would abort the tick AFTER the (correct)
    decision was made. The failure is RETURNED, so a broken writer is visible.
    """
    try:
        import runtime_trace as rt
    except Exception as exc:
        return [{"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}]
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"))
    conn.row_factory = sqlite3.Row
    out: list[dict[str, Any]] = []
    try:
        bad = [e for e in results if e["action"] != "none"]
        good = [e for e in results if e["action"] == "none"]
        if bad:
            alarm = {"ok": False, "checked": len(results),
                     "states": {e["subject"]: e["state"] for e in results},
                     "problems": [_problem_for(e) for e in bad]}
            out.append({"kind": "alarm", **rt.record_alarm(conn, alarm)})
        for e in good:
            try:
                r = rt.resolve_fault(conn, "runtime_%s" % e["subject"],
                                     cite_ref="keepalive_runner.py:113")
                out.append({"kind": "resolve", "subject": e["subject"],
                            "event_id": r["event_id"]})
            except rt.RuntimeTraceError:
                # No open fault to close: the normal case for a component that
                # was never faulted. Not an error, so it is NOT reported as one.
                pass
    except Exception as exc:
        return [{"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}]
    finally:
        conn.close()
    return out


def _problem_for(entry: dict[str, Any]) -> dict[str, Any]:
    """The alarm-shaped `problem` for one tick entry.

    The units and the citation come from the ENTRY (the measurement), never from
    a literal here, so the report and the tick cannot disagree.
    """
    unit = str(entry.get("unit") or "")
    secs = entry.get("age_seconds")
    return {"name": entry["subject"], "state": entry["state"], "unit": unit,
            "seconds": secs, "count": len(entry.get("pids") or []),
            "why": ("%s is %s via %s: %s%s"
                    % (entry["subject"], entry["state"], entry["action"],
                       ("%ss " % secs) if secs is not None else "", unit))}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="decide and report, but start nothing")
    ap.add_argument("--subject", action="append", choices=list(SUPERVISED),
                    help="restrict to one component (default: all)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = run_once(apply=not a.dry_run, subjects=a.subject)
    print(json.dumps(out, ensure_ascii=False, indent=2) if a.json
          else "\n".join("%-10s %-6s age=%ss running=%s -> %s"
                         % (e["subject"], e["state"], e["age_seconds"],
                            e["process_running"], e["action"])
                         for e in out["results"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
