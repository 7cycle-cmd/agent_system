#!/usr/bin/env python
"""qc_gate_hook.py — run the quality gate on a file just WRITTEN.

WHY THIS EXISTS (the human, 2026-09-28)
---------------------------------------
    "so can active that now" / "is active now, at skill > coding writing and
     coding verify?"
    "you need to have table to measure it! not talking on air"

MEASURED, and it is why a hook is the answer: the nine-dimension gate
(`qc_gate_runner`) had ZERO production callers (`git grep` found only proofs).
A gate nobody calls is a gate that does not exist. This hook calls it at
`PostToolUse` — the event that fires AFTER a write — so the gate runs on the FILE
that was just changed, automatically, with no cooperation from the writer.

WHY `report` IS THE DEFAULT AND `enforce` IS OPT-IN
---------------------------------------------------
The human's own ruling: *"先 `report` 跑一段，睇報告真唔真，再開 `enforce`"*.
MEASURED history: a hook that BLOCKS by default is how a whole turn gets wedged.
So this hook REPORTS first; `enforce` is opt-in through `QC_GATE_HOOK`, and an
UNKNOWN value FAILS CLOSED to `report` (never silently enforcing).

THE VALVES (a broken hook must never wedge the turn)
  * `QC_GATE_HOOK=off`      -> do nothing.
  * `QC_GATE_HOOK=report`   -> default; attach a report, NEVER deny.
  * `QC_GATE_HOOK=enforce`  -> a `must` failure emits `permissionDecision: deny`
                              with the field/rule/why/example (the fix).
  * any exception           -> fail OPEN (no message), logged.
  * a read tool / a non-.py -> do nothing (only a WRITTEN .py file is gated).

Contract (VS Code hooks):
  stdin  : JSON with `hook_event_name`, `tool_name`, `tool_input`, `session_id`.
  stdout : JSON with `systemMessage`, and (enforce only) `hookSpecificOutput`.

Manual dry-run:
  echo '{"hook_event_name":"PostToolUse"}' | python scripts/qc_gate_hook.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

LOG = BASE_DIR / "qc_gate_hook_log.txt"
MODE_REPORT, MODE_ENFORCE, MODE_OFF = "report", "enforce", "off"
# The two gates a WRITTEN FILE can answer. `tdd`/`5w1h` need a contract/kind, not
# a file; `trace` needs proof_run. Gating a file on a gate it cannot answer would
# report a FAIL the file never caused.
FILE_GATES = ("ontology", "safety")
# The tool_input keys that name the file a write targets (mirrors
# `plan_gate.target_path`, which is the ONE place that list lives for the gate).
_PATH_KEYS = ("filePath", "file_path", "path", "target_file", "notebookPath",
              "notebook_path")


def log(msg: str) -> None:
    """One JSON line per call. NEVER changes the decision."""
    try:
        import datetime
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "msg": str(msg)[:400]}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=True) + "\n")
    except Exception:
        pass


def emit(payload: dict) -> int:
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True))
    except Exception:
        try:
            sys.stdout.write(json.dumps(
                {"systemMessage": payload.get("systemMessage", "")[:400]}
                .__str__()))  # last resort, ASCII-only
        except Exception:
            pass
    return 0


def mode() -> str:
    """The valve. An UNKNOWN value FAILS CLOSED to `report` (never enforce)."""
    v = os.environ.get("QC_GATE_HOOK", MODE_REPORT).strip().lower()
    return v if v in (MODE_REPORT, MODE_ENFORCE, MODE_OFF) else MODE_REPORT


def target_file(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for k in _PATH_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _run_file_gates(path: str) -> list[dict]:
    """Run the FILE gates on `path`. Returns the friendly rows. Never raises."""
    import sqlite3
    import qc_gate_runner as R
    conn = sqlite3.connect(str(BASE_DIR / "agent.db"), timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        rows = []
        for g in FILE_GATES:
            rows.append(R.run_gate(conn, g, subject_kind="file",
                                   subject_ref=path))
        return rows
    finally:
        conn.close()


def _friendly(row: dict) -> str:
    """One readable line per gate, and the MUST-fix block when it failed.

    WHY `safety` GETS ITS OWN COMPACT LINE (MEASURED 2026-09-28)
    ----------------------------------------------------------
    The first version truncated the detail to its first 150 chars; MEASURED, that
    cut `READER 3` (the ruff attribution) out of the line. Widening it to
    head+tail STILL cut it, because `READER 3` sits in the MIDDLE of a long
    detail. Truncating a reader list is always the `measurement-scope` defect: a
    number a reader cannot audit. So `safety` prints a COMPACT attribution built
    from the counts themselves, one reader per line, and nothing is truncated.
    """
    g = row["gate_key"]
    v = row["verdict"]
    detail = str(row.get("detail") or "")
    if g == "safety":
        return "  %-9s %-8s value=%s  %s" % (g, v, row.get("value"),
                                             _safety_readers(detail))
    if len(detail) > 200:
        shown = detail[:110] + " … " + detail[-80:]
    else:
        shown = detail
    return "  %-9s %-8s value=%s  %s" % (g, v, row.get("value"), shown)


def _safety_readers(detail: str) -> str:
    """The reader attribution for `safety`, NEVER truncated.

    Reads the counts back out of the detail so the line is DERIVED from the same
    string the gate produced — a hand-written line here would be a second truth.
    """
    import re
    pop1 = re.search(r"POPULATION 1: [^=]*= (\d+) of (\d+)", detail)
    pop2 = re.search(r"POPULATION 2: [^=]*= (\d+)", detail)
    pop3 = re.search(r"POPULATION 3: [^=]*= (\d+)", detail)
    if not (pop1 and pop2 and pop3):
        # A non-standard detail (e.g. UNKNOWN) — show it whole rather than guess.
        return detail[-180:] if len(detail) > 180 else detail
    return ("READER 1 hardcode_scan=%s/%s | READER 2 code_shape=%s | "
            "READER 3 ruff_reader(measure)=%s"
            % (pop1.group(1), pop1.group(2), pop2.group(1), pop3.group(1)))


def build_report(path: str, rows: list[dict]) -> str:
    name = os.path.basename(path)
    failed = [r for r in rows if r["verdict"] == "FAIL"]
    unknown = [r for r in rows if r["verdict"] == "UNKNOWN"]
    head = ("qc_gate (report): %s -> %s"
            % (name, "FAIL" if failed else ("UNKNOWN" if unknown else "PASS")))
    body = [_friendly(r) for r in rows]
    tail = []
    if failed:
        tail.append("  MUST FIX (a hard-gate rule was violated):")
        for r in failed:
            tail.append("    %s: %s" % (r["gate_key"], _why(r)))
    return "\n".join([head] + body + tail)


def _why(row: dict) -> str:
    """The field/rule/why/example a worker can COPY — the friendly shape."""
    g = row["gate_key"]
    if g == "safety":
        return ("remove the hardcoded literal (read it from settings/DB); "
                "rule=0 live hardcode candidates; why=a typed value cannot be "
                "rotated and is invisible to later readers; example=read from "
                "settings instead of typing it")
    if g == "ontology":
        return ("register the name and give the file a cite; rule=the name is a "
                "registered term; why=an unresolvable name makes every later "
                "reader pick the wrong referent; example=terminology_registry."
                "add_term(conn, name, definition=..., cite_ref='file.py:1')")
    return row.get("detail") or ""


def main() -> int:
    m = mode()
    if m == MODE_OFF:
        return 0
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        log("FAIL-OPEN bad stdin: %s: %s" % (type(exc).__name__, exc))
        return 0
    try:
        event = str(payload.get("hook_event_name") or "").strip()
        if event != "PostToolUse":
            return 0
        tool_name = str(payload.get("tool_name") or "")
        # A READ never wrote anything; only a write is gated.
        tl = tool_name.lower()
        if not any(k in tl for k in ("create_file", "replace_string_in_file",
                                     "multi_replace_string_in_file",
                                     "edit_notebook", "apply_patch")):
            return 0
        tool_input = payload.get("tool_input")
        path = target_file(tool_input if isinstance(tool_input, dict) else {})
        if not path or not path.lower().endswith(".py"):
            return 0
        rows = _run_file_gates(path)
        report = build_report(path, rows)
        failed = [r for r in rows if r["verdict"] == "FAIL"]
        log("PostToolUse path=%s mode=%s failed=%s"
            % (os.path.basename(path), m, [r["gate_key"] for r in failed]))
        if m == MODE_ENFORCE and failed:
            fixes = " | ".join("%s: %s" % (r["gate_key"], _why(r))
                               for r in failed)
            return emit({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason":
                        "qc_gate (enforce) refused the change to %s.\n%s\n\n%s"
                        % (os.path.basename(path), report, fixes),
                },
                "systemMessage": report,
            })
        return emit({"systemMessage": report})
    except Exception as exc:
        log("FAIL-OPEN error: %s: %s" % (type(exc).__name__, exc))
        return 0


if __name__ == "__main__":
    sys.exit(main())