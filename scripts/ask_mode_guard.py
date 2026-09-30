#!/usr/bin/env python
"""PreToolUse hook — Ask mode = answer only.

Deterministic backstop for the prompt guard in `.github/copilot-instructions.md`
("NO ASK-QUESTIONS / NO AUTONOMY IN ASK MODE").

Reads the VS Code hook payload on stdin, and when the current chat mode is ASK
(state SSOT: `chat_mode.json`, written by `f_mode_switch.py`) it denies:

  * the ask-questions tool  -> Ask mode answers, it does not interview

Everything else is allowed. Any error fails OPEN (allow) so a broken hook can
never wedge the agent; the prompt guard remains the primary defence.

Safety valves:
  * env `ASK_MODE_GUARD=off` disables the hook entirely.
  * env `ASK_MODE_STALE_FAIL_OPEN_SEC=<seconds>` opts back IN to the legacy
    fail-open-on-stale behaviour. Default: unset (= strict, see below).
  * edit / terminal tools are NOT denied — VS Code's own permission system is
    authoritative for those, and duplicating it here caused the lockout.

Staleness policy (CHANGED 2026-09-20, CLASS HIGH):
  STRICT BY DEFAULT. `chat_mode.json` is trusted regardless of age; a stale
  file only produces a rate-limited warning, never a silent allow.

  Why this changed: the 600s fail-open valve was added during the 2026-09-18
  self-lockout, whose cause was BROAD SCOPE (denying edit/terminal). Scope was
  later narrowed to ask-questions only, but the valve stayed. Because the state
  file has a single writer (`f_mode_switch.py`) and is not refreshed on every
  mode change, the valve sat open almost permanently: measured 3258 STALE
  fail-opens vs 13 DENYs, with the last real DENY on 2026-09-18T23:02:27.

  The cost asymmetry inverted once scope narrowed:
    old broad scope  -> false-DENY wedged the agent entirely (lockout)
    narrow scope now -> false-DENY just makes the agent write plain text
                     -> false-ALLOW is the original incident (unattended
                        ask-questions treated as consent)
  For a guard whose only job is "Ask mode = answer only", erring toward
  enforcement is now cheap. False-deny risk was explicitly accepted by PM.

Contract (VS Code hooks):
  stdin  : JSON with `tool_name`, `tool_input`, `hook_event_name`, ...
  stdout : JSON with `hookSpecificOutput.permissionDecision` = allow | ask | deny
  exit   : 0 (success, stdout parsed as JSON)

Usage (manual dry-run):
  echo '{"tool_name":"ask-questions"}' | python scripts/ask_mode_guard.py
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mode_attest  # noqa: E402  (the single attestation SSOT)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(BASE_DIR, "ask_mode_guard_log.txt")
# D4 diagnostic capture (separate file so it can be deleted independently).
BAD_STDIN_PATH = os.path.join(BASE_DIR, "ask_mode_guard_bad_stdin.jsonl")

# REMOVED 2026-09-22 (user ruling R1: no default, no fallback). The mode is no
# longer read from `chat_mode.json`, so the staleness window and its opt-in
# fail-open valve are gone with it. A valve that turns a guard into "no opinion"
# IS a fallback, and R1 forbids one. The mode now comes from
# `mode_attest.attest()` — one authoritative source, or a FAULT.

# Tool names that mean "ask the user a blocking question".
ASK_TOOL_MARKERS = ("askquestions", "ask_questions", "ask-questions", "askquestion")


def log(msg: str, *, key: str = "", rate_sec: int = 0) -> None:
    """Append one timestamped line; never raise.

    When `key` and a positive `rate_sec` are given, repeated messages sharing
    that key are suppressed until `rate_sec` elapses. Rate state lives on disk
    because each hook invocation is a separate process.
    """
    try:
        from datetime import datetime

        if not _rate_allow(key, rate_sec):
            return
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


# Minimum seconds between two log lines sharing the same key.
LOG_RATE_SEC = 300

# Rate-limit state must persist ACROSS processes: the hook runs as a fresh
# process per tool call, so an in-memory dict would never suppress anything.
RATE_STATE = os.path.join(BASE_DIR, "ask_mode_guard_rate.json")


def _rate_allow(key: str, rate_sec: int) -> bool:
    """True when `key` may log now; persists the decision to disk.

    Best-effort: any error returns True (never block logging on this).
    """
    if not key or rate_sec <= 0:
        return True
    try:
        now = time.time()
        data = {}
        try:
            with open(RATE_STATE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        last = float(data.get(key) or 0)
        if now - last < rate_sec:
            return False
        data[key] = now
        # keep the file small
        if len(data) > 50:
            data = dict(sorted(data.items(), key=lambda kv: kv[1])[-50:])
        tmp = RATE_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, RATE_STATE)
        return True
    except Exception:
        return True


def _stale_fail_open_sec() -> int:
    """REMOVED 2026-09-22 — kept only so an old caller fails loudly, not silently.

    The staleness valve existed because the guard read `chat_mode.json`. It no
    longer does (see `current_mode()`), so there is nothing to be stale. Calling
    this now raises rather than returning a value that would imply a fallback
    still exists.
    """
    raise RuntimeError(
        "_stale_fail_open_sec was removed 2026-09-22: the guard no longer reads "
        "chat_mode.json, so there is no staleness window and no fail-open valve."
    )


def current_mode(session_id: str = "") -> str:
    """PROVEN chat mode via `mode_attest.attest()`, or '' on a FAULT.

    CHANGED 2026-09-22 (user ruling R1: no default, no fallback). This used to
    read `chat_mode.json` and trust it regardless of age. That file is the
    hotkey's INTENT record and was measured WRONG: it said ASK while the user was
    in AGENT, and this guard then DENIED a PLAN-mode agent's read-only
    `vscode_askQuestions` call with "Ask mode = answer only".

    The mode now comes from ONE authoritative source (VS Code's own persisted
    chat input state). A FAULT returns '' — and `decide()` treats '' as "no
    opinion", so an unprovable mode can never be enforced as if it were ASK.

    `session_id` (added 2026-09-23): WITHOUT it this guard had the SAME
    wrong-chat defect it was built to prevent — it would read the mode of
    whichever chat last wrote the workspace key, and could deny a read because
    ANOTHER chat was in ASK. Passing the payload's id scopes the read to THIS
    chat. An empty id keeps the workspace rung (see `attest`).
    """
    if os.environ.get("ASK_MODE_GUARD", "").strip().lower() in ("off", "0", "false"):
        return ""                      # escape hatch: ASK_MODE_GUARD=off
    try:
        att = mode_attest.attest(session_id=session_id)
        if att.get("state") == "PROVEN":
            return att.get("mode") or ""
        log(
            "FAULT mode unproven -> no opinion: %s" % att.get("fault_reason"),
            key="attest_fault",
            rate_sec=LOG_RATE_SEC,
        )
        return ""
    except Exception:
        return ""


def classify(tool_name: str) -> str:
    """Return 'ask' for the ask-questions tool, else ''.

    Only the ask-questions tool is classified. Edit / terminal are intentionally
    NOT classified — `decide()` gives them no opinion (see its docstring).
    """
    name = (tool_name or "").strip().lower()
    if not name:
        return ""
    if any(m in name for m in ASK_TOOL_MARKERS):
        return "ask"
    return ""


def decide(tool_name: str, mode: str) -> tuple[str, str]:
    """Return (permissionDecision, reason). Empty decision = no opinion.

    Scope is deliberately NARROW: only the ask-questions tool is denied.
    Edit / terminal tools are NOT denied here — VS Code's own permission
    system plus the prompt guard already cover those, and denying them made
    the hook wedge the agent whenever the mode state was stale.

    `mode == ''` means the mode could NOT be proven (a FAULT). It is NOT ASK.
    Returning no opinion there is the fix for the 2026-09-22 incident: an
    unprovable mode must never be enforced as if it were ASK.
    """
    kind = classify(tool_name)
    if not kind:
        return "", ""
    if mode != "ASK":
        return "", ""
    if kind == "ask": 
        return (
            "deny",
            "Ask mode = answer only. Do NOT call the ask-questions tool. "
            "If a question is genuinely blocking, write it as plain text and stop. "
            "Never treat 'user is not available / work autonomously' as approval.",
        )
    # edit / terminal: no opinion — let VS Code permissions decide.
    return "", ""


def _capture_bad_stdin(raw: str, exc: Exception) -> None:
    """D4 diagnostic: record the RAW malformed payload so the cause is visible.

    WHY THIS EXISTS
    ---------------
    `ask_mode_guard` fails OPEN on a malformed payload, so the ASK-mode
    protection silently stops applying. That happened 21 times (latest
    2026-09-20T14:30). The JSON errors vary in position (char 590 -> 7102) and
    kind, which rules out a fixed truncation — something MANGLES the payload.

    Per the systematic-debugging Iron Law, no fix is proposed before the root
    cause is established. This function only RECORDS what arrived:

      * the byte length and a sha256 of the raw payload
      * the first and last 300 characters, with control characters escaped
      * the JSON error position, and a window around it

    It never raises and never changes the decision. The capture file is
    separate from the main log so it can be deleted without losing history.
    """
    try:
        import hashlib

        blob = raw or ""
        digest = hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()
        pos = getattr(exc, "pos", None)
        window = ""
        if isinstance(pos, int) and 0 <= pos <= len(blob):
            lo = max(0, pos - 120)
            hi = min(len(blob), pos + 120)
            window = blob[lo:hi]

        def _esc(s: str) -> str:
            return (s.replace("\\", "\\\\").replace("\r", "\\r")
                     .replace("\n", "\\n").replace("\t", "\\t"))

        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "error": "%s: %s" % (type(exc).__name__, exc),
            "pos": pos,
            "len": len(blob),
            "sha256": digest,
            "head": _esc(blob[:300]),
            "tail": _esc(blob[-300:]),
            "around_pos": _esc(window),
        }
        with open(BAD_STDIN_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> int:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:  # malformed payload -> fail open
        # D4 (2026-09-20): capture WHAT actually arrived before deciding how to
        # fail. The JSON errors vary in position (char 590 -> 7102) and kind
        # ("Expecting ',' delimiter" / "Expecting property name enclosed in
        # double quotes"), which is NOT a fixed truncation — something MANGLES
        # the payload. Without the raw bytes the cause cannot be established,
        # and a fix would be a guess. This capture is diagnostic only: the
        # failure mode is unchanged.
        _capture_bad_stdin(raw, exc)
        log("FAIL-OPEN bad stdin: %s: %s" % (type(exc).__name__, exc))
        return 0

    tool_name = str(payload.get("tool_name") or "")
    # The payload's session id scopes the mode read to THIS chat. MEASURED: both
    # PreToolUse and UserPromptSubmit carry it (`mode_attest_capture.jsonl:2-3`).
    session_id = str(payload.get("session_id") or "").strip()
    mode = current_mode(session_id)
    decision, reason = decide(tool_name, mode)

    if not decision:
        return 0

    log("DENY mode=%s tool=%s session=%s"
        % (mode, tool_name, session_id or "-"))
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                },
                "systemMessage": "Ask-mode guard blocked `%s`." % tool_name,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
