"""playwright_run_registry.py -- WHICH playwright runs are running, and WHY.

THE HUMAN (2026-09-26): "show me which playwright and running and why, with stop
button".

THE QUESTION THIS MODULE ANSWERS
--------------------------------
Not "is playwright installed" and not "did a run pass". It answers:

    which playwright-driving processes are ALIVE right now,
    WHY each one is alive (who spawned it, and what it is declared to be),
    and how to STOP one.

WHY "WHY" IS MEASURED, NOT GUESSED
----------------------------------
A process list alone is a list of PIDs. The human asked WHY, and a guess would be
worse than no answer: "it is probably the Stop hook" is exactly the kind of claim
this repo has recorded as a defect. So `why()` returns the MEASURED parent chain
(walked from `Win32_Process.ParentProcessId` to the root) plus, when the script is
DECLARED, the reason the gate itself declares.

MEASURED 2026-09-26 21:12: the burst's spawner was `_tmp_triage.py`, found by
walking `ParentProcessId` (PID 75528 -> 44480). No table said so; the chain did.

THE DECLARED SET, NOT A NAME PATTERN
------------------------------------
A script is a playwright run because it is ON a declared list, never because its
name looks like it might be. The lists are READ from `scripts/proof_gate.py`
(`SCREEN_DEPENDENT`, `SELF_PROOFS`) so this module and the gate cannot disagree
about what drives the screen. A name pattern would silently swallow a future
script whose name merely looks similar -- the same rule the gate states for its
own lists.

NEVER RAISES
------------
A status read that can raise turns a page into an outage. Every failure is
returned as `ok: False` with a `why`, so a failure is VISIBLE rather than
indistinguishable from "nothing is running".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# THE MASTER SWITCH. THE HUMAN (2026-09-26): "playwright system with function be
# control ON/OFF" / "have the button!! turn off / ON".
#
# A FILE at the repo root, the SAME shape the Stop hook already uses
# (`scripts/proof_gate.py:1329` reads `STOP_HOOK_OFF`). ONE idea, not two.
#
# PRESENT = OFF, ABSENT = ON. The state is read from the FILESYSTEM every time,
# never cached in a process's memory -- the same reason the Stop hook gives: a
# switch that lives in memory is lost when the process restarts, and the human
# then cannot tell whether the system is on or off.
#
# WHAT OFF MEANS, STATED PRECISELY:
#   OFF refuses a NEW run. It does NOT kill a run already in flight.
# A switch that silently kills work in flight is a worse defect than no switch;
# stopping a live run stays the per-row Stop button's job.
# ---------------------------------------------------------------------------
OFF_FILE = os.path.join(BASE_DIR, "PLAYWRIGHT_OFF")

# THE PER-PROCESS OVERRIDE. THE HUMAN (2026-09-26): "制止呢類並行 proof 互相污染
# 全域狀態（呢個就係今次 flip 嘅根源）".
#
# WHY: the switch is ONE global file. A proof that must turn it OFF holds it OFF
# for ~90s, and any OTHER proof (or another AGENT SESSION) that starts inside
# that window reads OFF and SKIPs. MEASURED: the writer was
# `_proof_proof_skip_when_off.py`, and a proof launched inside the window printed
# `SKIP:`.
#
# THE FIX IS A PATH, NOT A FLAG. `PW_SWITCH_FILE` names the file this PROCESS
# uses. Unset -> the global file, so PRODUCTION IS UNCHANGED. Set -> the proof
# reads and writes its OWN file and cannot collide with anyone.
#
# A path (not a boolean) because the state must stay readable from the
# filesystem: a switch that lives in memory is lost when the process restarts.
SWITCH_FILE_ENV = "PW_SWITCH_FILE"


def _off_file(path: str | None = None) -> str:
    """The switch file THIS process uses. Explicit path, else env, else global.

    Resolved on EVERY call, never cached: a cached path would make the override
    invisible to a process that sets the variable after import.

    THE `path` ARGUMENT EXISTS FOR THE HELPER. The helper is a THREADED server,
    so it cannot honour a per-request override by mutating `os.environ` -- two
    concurrent requests would race on one process-global variable. An explicit
    argument is thread-safe; the env var is for a whole subprocess (a proof).
    """
    if path:
        return path
    try:
        override = os.environ.get(SWITCH_FILE_ENV)
    except Exception:  # noqa: BLE001
        override = None
    return override or OFF_FILE


# THE SWITCH AUDIT. APPEND-ONLY, one line per write.
#
# WHY (2026-09-26): a proof run left `PLAYWRIGHT_OFF` present and I could NOT
# reproduce it in isolation (4 attempts). A guess would be worse than no answer,
# so every write records WHO wrote it: pid, parent pid, the script, and the new
# state. The log is EVIDENCE; if it does not name a writer, the answer is
# "not reproduced", not a story.
#
# APPEND-ONLY on purpose: a log that can be rewritten is not a record. A failure
# to write the audit NEVER blocks the switch -- the switch is the human's, and a
# broken log must not take it away.
AUDIT_FILE = os.path.join(BASE_DIR, "playwright_switch_audit.txt")


def _audit(new_state: bool, path: str | None = None) -> None:
    """Append ONE line naming who changed the switch. Never raises."""
    try:
        import datetime
        try:
            import psutil  # type: ignore
            ppid = psutil.Process(os.getpid()).ppid()
        except Exception:  # noqa: BLE001
            ppid = -1
        script = ""
        try:
            import __main__
            script = os.path.basename(getattr(__main__, "__file__", "") or "")
        except Exception:  # noqa: BLE001
            script = ""
        line = "%s\tpid=%d\tppid=%d\tscript=%s\tstate=%s\tfile=%s\n" % (
            datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            os.getpid(), ppid, script or "?", "ON" if new_state else "OFF",
            os.path.basename(_off_file(path)))
        with open(AUDIT_FILE, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:  # noqa: BLE001
        pass


def is_enabled(path: str | None = None) -> bool:
    """Is the Playwright system ON? MEASURED from the file, never cached.

    `True` when the switch file is ABSENT. The file is `_off_file(path)` -- the
    explicit `path`, else the `PW_SWITCH_FILE` override, else the global
    `PLAYWRIGHT_OFF`. A read that raises is treated as ENABLED, because the
    switch's job is to STOP work on request, and a broken read must not silently
    disable a working system.
    """
    try:
        return not os.path.exists(_off_file(path))
    except Exception:  # noqa: BLE001
        return True


def set_enabled(on: bool, path: str | None = None) -> dict[str, Any]:
    """Turn the Playwright system ON or OFF. Returns the NEW state.

    IDEMPOTENT: OFF twice is not an error, and neither is ON twice. The returned
    `enabled` is the state AFTER the call, read back from the filesystem -- not
    the value that was asked for, so a failed write cannot report success.

    Writes `_off_file(path)`, so a proof with `PW_SWITCH_FILE` set touches ONLY
    its own file and cannot collide with another proof or another agent session.
    """
    target = _off_file(path)
    try:
        if on:
            if os.path.exists(target):
                os.remove(target)
        else:
            if not os.path.exists(target):
                with open(target, "w", encoding="utf-8") as fh:
                    fh.write("Playwright is switched OFF.\n"
                             "Delete this file (or use the UI switch) to turn "
                             "it back ON.\n")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "enabled": is_enabled(path), "off_file": target,
                "why": "%s: %s" % (type(exc).__name__, exc)}
    now = is_enabled(path)
    _audit(now, path)
    return {"ok": True, "enabled": now, "off_file": target,
            "why": ("Playwright is ON" if now else
                    "Playwright is OFF: %s exists" % os.path.basename(target))}


def switch_status(path: str | None = None) -> dict[str, Any]:
    """The switch state, for the page. Never raises."""
    enabled = is_enabled(path)
    target = _off_file(path)
    return {"ok": True, "enabled": enabled, "off_file": target,
            "why": ("Playwright is ON" if enabled else
                    "Playwright is OFF: %s exists" % os.path.basename(target))}


def refuse_if_off(path: str | None = None) -> dict[str, Any] | None:
    """`None` when ON; a REFUSAL dict when OFF. The caller returns it as-is.

    A refusal NAMES the switch and the way back, so a reader is never left with
    "it did not run" and no reason.
    """
    if is_enabled(path):
        return None
    return {"ok": False, "refused": True, "why": (
        "Playwright is switched OFF (%s exists at the repo root). "
        "Turn it ON with the Playwright page switch, or delete the file."
        % os.path.basename(_off_file(path)))}


# The PowerShell query. `-NoProfile` keeps it fast and free of a user profile's
# side effects; `ConvertTo-Json -Compress` keeps the payload small.
#
# `Win32_Process` (not the Perf counters) because the question needs
# `ParentProcessId` and `CommandLine`, which the Perf counters do not carry.
# MEASURED: `Win32_PerfFormattedData_PerfProc_Process.Name` carries an instance
# suffix (`Code#12`), so it is the wrong source for a parent chain.
_PS_QUERY = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId, ParentProcessId, Name, CommandLine, "
    "CreationDate | ConvertTo-Json -Compress"
)

_TIMEOUT_SEC = 30

# A script that DRIVES the live screen or SPAWNS the gate. Read from the gate so
# the two cannot disagree. The fallback is the measured list at the time of
# writing, used only when the gate cannot be imported.
_FALLBACK_DECLARED: dict[str, str] = {
    "_proof_f9.py": "drives the live screen with pyautogui",
    "_proof_llm_status_evidence_5x.py": "drives the live screen with pyautogui",
    "_proof_one_space.py": "drives the live screen with pyautogui",
    "_proof_playwright_ui.py": "calls the live /api/playwright/test endpoint",
    "_proof_task_capture.py": "drives the live screen with pyautogui",
    "_proof_vscode_hotkey_coord.py": "drives the live screen with pyautogui",
    "_proof_vscode_layout_pinned.py": "drives the live screen with pyautogui",
    "_proof_copy_button_trigger_point.py":
        "calls the live /api/playwright/test endpoint",
    "_proof_playwright_step_guide.py":
        "calls the live /api/playwright/test endpoint",
    "_proof_playwright_step_kind.py":
        "calls the live /api/playwright/test endpoint",
    "_proof_playwright_test_button.py":
        "calls the live /api/playwright/test endpoint",
    "_proof_vscode_session_identity.py":
        "calls the live /api/playwright/test endpoint",
    "_proof_proof_gate.py": "spawns the gate to test the hook",
    "_proof_proof_gate_silent.py": "spawns the gate to test the hook",
    "_proof_proof_gate_skip_and_timeout.py": "spawns the gate to test the hook",
    "_proof_stop_hook_temp_off.py": "spawns the gate to test the hook",
    "_proof_stop_hook_no_reattach.py": "spawns the gate to test the hook",
    "_proof_report_no_shrink.py": "spawns the gate to test the hook",
}

# A script that SPAWNS the declared ones. MEASURED 2026-09-26: `_tmp_triage.py`
# ran 38 RED proofs in a loop, including every screen-driving one. It is not a
# proof, so it is not in the gate's lists -- but it is the reason the proofs were
# alive, so it must be shown and stoppable.
_SPAWNER_SCRIPTS: dict[str, str] = {
    "_tmp_triage.py": "runs the RED proof list, including the screen-driving ones",
    "scripts/proof_gate.py": "the Stop hook: runs the changed proofs",
    "proof_gate.py": "the Stop hook: runs the changed proofs",
}


def declared_reasons() -> dict[str, str]:
    """The declared script -> reason map, READ from the gate when possible.

    Reading it from `scripts/proof_gate.py` means this module and the gate cannot
    disagree about what drives the screen. A failure falls back to the measured
    list rather than returning an empty map -- an empty map would make every
    running proof look undeclared, which is a WRONG answer, not a missing one.
    """
    out = dict(_FALLBACK_DECLARED)
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, "scripts"))
        import proof_gate as pg  # type: ignore

        for name, reason in getattr(pg, "SCREEN_DEPENDENT", ()):  # noqa: B905
            out[str(name)] = str(reason)
        for name, reason in getattr(pg, "SELF_PROOFS", ()):  # noqa: B905
            out[str(name)] = str(reason)
    except Exception:
        pass
    out.update(_SPAWNER_SCRIPTS)
    return out


def _run_query() -> tuple[list[dict[str, Any]], str]:
    """Run the process query. Returns (rows, error). Never raises."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_QUERY],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=_TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return [], "%s: %s" % (type(exc).__name__, exc)
    raw = (r.stdout or "").strip()
    if not raw:
        return [], "the process query returned nothing (stderr: %s)" % (
            (r.stderr or "").strip()[:200] or "empty")
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return [], "the process query returned unreadable JSON: %s" % exc
    if isinstance(data, dict):
        data = [data]
    return [d for d in data if isinstance(d, dict)], ""


def snapshot() -> dict[str, Any]:
    """ONE process snapshot. Returns `{ok, rows, by_pid, why}`. Never raises."""
    rows, err = _run_query()
    if err:
        return {"ok": False, "rows": [], "by_pid": {}, "why": err}
    by_pid: dict[int, dict[str, Any]] = {}
    for d in rows:
        try:
            by_pid[int(d.get("ProcessId"))] = d
        except (TypeError, ValueError):
            continue
    return {"ok": True, "rows": rows, "by_pid": by_pid, "why": ""}


def _fmt_started(raw: Any) -> str:
    """A READABLE start time from the WMI value.

    MEASURED: `Win32_Process.CreationDate` arrives as `/Date(1790428859084)/`
    when PowerShell serialises it to JSON. That is a .NET epoch in MILLISECONDS,
    and rendering it raw puts a number on the page that no reader can check
    against a clock. It is converted to local `YYYY-MM-DD HH:MM:SS`.

    A value that cannot be parsed is returned AS-IS rather than blanked: an
    unreadable value is still evidence, and a blank would read as "not collected".
    """
    s = str(raw or "").strip()
    m = re.search(r"/Date\((\d+)\)/", s)
    if not m:
        return s
    try:
        import datetime
        ts = int(m.group(1)) / 1000.0
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:  # noqa: BLE001
        return s


def _script_of(cmd: str) -> str:
    """The script a command line runs, or `""`.

    MEASURED: the command line is `"<py>" <script> [args]`, so the script is the
    first token ending in `.py`. Taking the LAST token would name an argument.
    """
    for tok in str(cmd or "").replace('"', " ").split():
        if tok.lower().endswith(".py"):
            return os.path.basename(tok)
    return ""


def _is_playwright_driver(script: str, declared: dict[str, str]) -> bool:
    """True when the script is DECLARED to drive playwright or spawn the gate."""
    if not script:
        return False
    if script in declared:
        return True
    # A `_proof_*` that calls the live endpoint is declared by the gate; anything
    # else is NOT assumed to be one. Fails CLOSED: an undeclared script is not
    # shown as a playwright run, because a wrong row is worse than a missing one.
    return False


def why(pid: int, snap: dict[str, Any], declared: dict[str, str]) -> list[dict]:
    """The MEASURED parent chain for `pid`, root LAST.

    Each entry is `{pid, script, reason}`. `reason` is the declared reason when
    the script is declared, else `""` -- never a guess. The chain is walked from
    `ParentProcessId`, so it is a measurement, not an inference.
    """
    by_pid = snap.get("by_pid") or {}
    chain: list[dict] = []
    seen: set[int] = set()
    cur = int(pid)
    # A bounded walk: a cycle or a very deep tree must not hang the page.
    for _ in range(24):
        if cur in seen or cur not in by_pid:
            break
        seen.add(cur)
        d = by_pid[cur]
        script = _script_of(str(d.get("CommandLine") or ""))
        chain.append({"pid": cur, "script": script or str(d.get("Name") or ""),
                      "reason": declared.get(script, "")})
        try:
            cur = int(d.get("ParentProcessId"))
        except (TypeError, ValueError):
            break
        if cur <= 0:
            break
    return chain


def running_runs(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every LIVE playwright-driving process, with its measured why.

    Returns `{ok, rows, why_empty, why}`. `why_empty` is a SENTENCE, so an empty
    list is never a blank the reader has to interpret.
    """
    snap = snap or snapshot()
    if not snap.get("ok"):
        return {"ok": False, "rows": [], "why_empty": "",
                "why": snap.get("why") or "the process snapshot failed"}
    declared = declared_reasons()
    rows: list[dict] = []
    for d in snap.get("rows") or []:
        script = _script_of(str(d.get("CommandLine") or ""))
        if not _is_playwright_driver(script, declared):
            continue
        try:
            pid = int(d.get("ProcessId"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "pid": pid,
            "script": script,
            "reason": declared.get(script, ""),
            "started_at": _fmt_started(d.get("CreationDate")),
            "why": why(pid, snap, declared),
        })
    rows.sort(key=lambda r: r["pid"])
    if rows:
        why_empty = ""
    else:
        why_empty = ("No playwright-driving process is running. The declared "
                     "set is read from scripts/proof_gate.py "
                     "(SCREEN_DEPENDENT + SELF_PROOFS) plus the known spawners.")
    return {"ok": True, "rows": rows, "why_empty": why_empty, "why": ""}


def _children_of(pid: int, snap: dict[str, Any]) -> list[int]:
    """The direct children of `pid`, from the snapshot."""
    out: list[int] = []
    for d in snap.get("rows") or []:
        try:
            if int(d.get("ParentProcessId")) == int(pid):
                out.append(int(d.get("ProcessId")))
        except (TypeError, ValueError):
            continue
    return out


def _tree(pid: int, snap: dict[str, Any]) -> list[int]:
    """`pid` and every descendant, CHILDREN FIRST so a parent cannot respawn."""
    order: list[int] = []

    def walk(p: int, depth: int) -> None:
        if depth > 12:
            return
        for c in _children_of(p, snap):
            walk(c, depth + 1)
        order.append(p)

    walk(int(pid), 0)
    return order


def stop_run(pid: int, snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Kill `pid` and its descendants. Returns `{ok, killed, why}`.

    CHILDREN FIRST. MEASURED 2026-09-26: killing a parent first leaves the child
    alive and reparented, so the run keeps going while the page says it stopped.
    Killing the tree bottom-up is what makes "stopped" true.

    AN IMPOSSIBLE PID IS REFUSED, never reported as stopped. A stop that claims
    success for a process that never existed is the worst kind of false pass.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return {"ok": False, "killed": [], "why": "pid must be an int"}
    if pid <= 0:
        return {"ok": False, "killed": [], "why": "pid must be positive"}
    snap = snap or snapshot()
    if not snap.get("ok"):
        return {"ok": False, "killed": [],
                "why": snap.get("why") or "the process snapshot failed"}
    by_pid = snap.get("by_pid") or {}
    if pid not in by_pid:
        return {"ok": False, "killed": [],
                "why": "no process with pid %d is running" % pid}
    declared = declared_reasons()
    script = _script_of(str(by_pid[pid].get("CommandLine") or ""))
    if not _is_playwright_driver(script, declared):
        return {"ok": False, "killed": [],
                "why": ("pid %d runs %r, which is NOT a declared "
                        "playwright-driving script" % (pid, script or "?"))}
    killed: list[int] = []
    failed: list[dict] = []
    for p in _tree(pid, snap):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Stop-Process -Id %d -Force -ErrorAction Stop" % p],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if r.returncode == 0:
                killed.append(p)
            else:
                failed.append({"pid": p,
                               "why": (r.stderr or "").strip()[:160]})
        except Exception as exc:  # noqa: BLE001
            failed.append({"pid": p, "why": "%s: %s" % (type(exc).__name__, exc)})
    if not killed:
        return {"ok": False, "killed": [], "failed": failed,
                "why": "nothing was killed"}
    return {"ok": True, "killed": killed, "failed": failed,
            "script": script, "why": ""}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="print the raw JSON")
    ap.add_argument("--stop", type=int, default=0,
                    help="stop the run with this pid")
    args = ap.parse_args()
    if args.stop:
        out = stop_run(args.stop)
    else:
        out = running_runs()
    if args.json:
        print(json.dumps(out, indent=1, ensure_ascii=False))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("FAILED: %s" % out.get("why"))
        return 1
    if args.stop:
        print("stopped: %s" % out.get("killed"))
        return 0
    rows = out.get("rows") or []
    if not rows:
        print(out.get("why_empty"))
        return 0
    print("%-8s %-42s %s" % ("pid", "script", "why"))
    for r in rows:
        chain = " <- ".join(
            "%s(%d)" % (c.get("script") or "?", c.get("pid"))
            for c in (r.get("why") or []))
        print("%-8d %-42s %s" % (r["pid"], r["script"], chain))
        if r.get("reason"):
            print("%-8s %-42s   declared: %s" % ("", "", r["reason"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
