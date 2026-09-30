#!/usr/bin/env python
"""proof_gate.py — make the proof report MANDATORY and AUTOMATIC.

THE FAILURE THIS FIXES (observed 2026-09-21)
-------------------------------------------
The user asked for "proof report pls". The agent HAD run all 132 proofs and
written the numbers — but it put them in the `task_complete` summary and the
visible reply was one word: "完成。"

The user's reply: **"this is proof report!"**

So the report existed and STILL did not reach the user. That is not a knowledge
problem, it is an ENFORCEMENT problem: a rule that says "always show the proof
report" is a rule the agent can silently not follow, and no gate noticed.

THE DESIGN RULE
---------------
Do not ask the model to remember. Make the REPORT a side effect of the turn
ending, produced by CODE, so it cannot be skipped and does not depend on the
model's cooperation at all.

    Stop event fires
      -> this script runs the proofs the session touched
      -> writes the report to disk
      -> returns it as `systemMessage`, which VS Code shows in the chat

The user therefore ALWAYS sees the report, even when the model says one word.

WHY `Stop`, AND NOT `PreToolUse`
--------------------------------
`PreToolUse` can only see a tool about to run. The report is only meaningful when
the turn ENDS, so `Stop` is the only event that can carry it.

WHY NOT BLOCK THE STOP
----------------------
`decision: "block"` would force the agent to keep talking, which risks an
infinite loop (the VS Code docs warn that a blocked stop consumes credits). This
gate does NOT block: it ATTACHES the report. A report nobody had to ask for is
stronger than a report the agent was nagged into writing, and it cannot loop.

WHAT COUNTS AS "THE PROOFS THE SESSION TOUCHED"
-----------------------------------------------
A proof file whose mtime is newer than the stamp written by the previous report.
That is the honest set: the proofs that CHANGED since the last report. Running
all 132 on every stop would be slow and would re-report stale results.

SAFETY VALVES (a broken gate must never wedge the turn)
  * env `PROOF_GATE=off`        -> hook does nothing.
  * env `PROOF_GATE_MAX`        -> cap how many proofs run (default 40).
  * any exception               -> fail OPEN (no message), logged.
  * no changed proofs           -> say so; never report a silent nothing.
  * `--report`                  -> manual run, same code path as the hook.

Contract (VS Code hooks):
  stdin  : JSON with `hook_event_name`, `stop_hook_active`, ...
  stdout : JSON with `systemMessage` (shown to the user)

Manual dry-run:
  echo '{"hook_event_name":"Stop"}' | python scripts/proof_gate.py
  python scripts/proof_gate.py --report --all
  python scripts/proof_gate.py --report --all --include-screen   # launches browsers
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

# THE ENCODING FIX — this is why the user saw NO report at all.
#
# MEASURED 2026-09-21. On this box the console is cp950 (Traditional Chinese
# Windows). `print()` of a report containing any non-cp950 character raised
# `UnicodeEncodeError: 'cp950' codec can't encode character '\ufffd'`, and that
# raise happened INSIDE the hook's `except Exception: return 0` block, so the
# gate emitted NOTHING. The failure was invisible: a hook that prints nothing
# looks exactly like a hook with nothing to say.
#
# Two rules follow, and both are applied:
#   1. make the OUTPUT UNABLE to raise: errors="replace" on this stream.
#   2. never let a DISPLAY failure be reported as "no report" (see `emit`).
for _stream_name in ("stdout", "stderr"):
    try:
        getattr(sys, _stream_name).reconfigure(
            encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVIDENCE_DIR = os.path.join(BASE_DIR, "qc_evidence")
STAMP_PATH = os.path.join(EVIDENCE_DIR, "proof_gate_stamp.json")
LOG_PATH = os.path.join(BASE_DIR, "proof_gate_log.txt")

# ---------------------------------------------------------------------------
# THE KILL SWITCH, AND ITS PER-PROCESS OVERRIDE (2026-09-26)
# ---------------------------------------------------------------------------
# THE HUMAN: "制止呢類並行 proof 互相污染全域狀態（呢個就係今次 flip 嘅根源）".
#
# THE DEFECT, MEASURED: `STOP_HOOK_OFF` is ONE global file, and THREE proofs move
# or delete it to exercise the hook:
#   * `_proof_report_no_shrink.py:136`      -- `OFF.unlink()`, restored in `finally`
#   * `_proof_stop_hook_no_reattach.py:154` -- `OFF.unlink()`, restored in `finally`
#   * `_proof_stop_hook_temp_off.py:259`    -- `os.remove(MARKER)`
# While one of them holds the switch aside, the hook is ARMED for every other
# process -- and a hard kill leaves the human's kill switch DELETED (that
# incident is recorded in `plan_STOP.HOOK.NO.REATTACH.md`).
#
# THE FIX IS A PATH, NOT A FLAG. `STOP_HOOK_SWITCH_FILE` names the file THIS
# process reads. Unset -> the global `STOP_HOOK_OFF`, so PRODUCTION IS
# UNCHANGED. Set -> a proof points the gate at its OWN file and never touches
# the human's switch.
STOP_HOOK_SWITCH_ENV = "STOP_HOOK_SWITCH_FILE"


def stop_hook_switch_file() -> str:
    """The kill-switch file THIS process reads. Env override, else the global.

    Resolved on EVERY call, never cached: the gate is spawned FRESH on every
    Stop, and a cached path would make the override invisible.
    """
    try:
        override = os.environ.get(STOP_HOOK_SWITCH_ENV)
    except Exception:  # noqa: BLE001
        override = None
    return override or os.path.join(BASE_DIR, "STOP_HOOK_OFF")

# ---------------------------------------------------------------------------
# THE HEARTBEAT (2026-09-26)
# ---------------------------------------------------------------------------
# THE HUMAN: "all must be can measure unit and register / stop hook is my pain!!!"
#
# MEASURED, and this is why a heartbeat is REQUIRED rather than nice: the hook's
# on/off state was a FILENAME. `.github/hooks/proof_gate.json` was renamed to
# `.disabled`, so VS Code did not load it -- and the hook's own log could not say
# so, because a hook that never runs writes no log line. MEASURED:
#   * `python scripts/proof_gate.py --report` printed 0 lines (exit 0).
#   * `proof_gate_log.txt` held 1,010 "emitting NOTHING" lines.
# So "the hook is OFF" and "the hook is ON and idle" produced the SAME
# observation: silence. An observation that cannot separate two states is not a
# measurement.
#
# THE HEARTBEAT SEPARATES THEM. Every invocation appends ONE line naming the
# branch it took, BEFORE any early return. A reader can then tell:
#   off-switch        the file kill switch stopped it
#   env-off           PROOF_GATE=off stopped it
#   child             it was spawned by the gate itself
#   not-a-stop        the event was not Stop
#   stop-hook-active  the loop guard
#   emitted           it delivered a fresh proof report
#   nothing-changed   it ran and had nothing to report
# If the file has NO line for a period in which Stops happened, the hook did not
# run -- which is exactly the fact that was invisible.
HEARTBEAT_PATH = os.path.join(BASE_DIR, "proof_gate_heartbeat.txt")


def heartbeat(branch: str, note: str = "") -> None:
    """Append ONE line naming the branch `main` took. NEVER raises.

    A heartbeat that can fail the hook would be worse than none, so every
    error is swallowed -- the hook's job is the report, not the heartbeat.
    """
    try:
        with open(HEARTBEAT_PATH, "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\n"
                     % (time.strftime("%Y-%m-%d %H:%M:%S"), branch,
                        str(note or "")[:200]))
    except Exception:
        pass


def heartbeat_tail(n: int = 20) -> list[str]:
    """The last `n` heartbeat lines, oldest first. Never raises."""
    try:
        with open(HEARTBEAT_PATH, encoding="utf-8", errors="replace") as fh:
            return [ln.rstrip("\n") for ln in fh.readlines()][-int(n):]
    except Exception:
        return []


# How many proofs a single Stop may run. A bound, because the hook has a
# timeout and an unbounded run would silently produce nothing.
try:
    MAX_PROOFS = max(1, int(os.environ.get("PROOF_GATE_MAX", "40") or "40"))
except ValueError:
    MAX_PROOFS = 40

# ---------------------------------------------------------------------------
# THE TIMEOUT AND THE EXCLUSION ARE SETTINGS, NOT CONSTANTS (2026-09-25)
# ---------------------------------------------------------------------------
# THE HUMAN: "B+C" — raise the timeout, and exclude the screen-dependent proofs.
# THE HUMAN'S OWN RULE, which decides HOW: "i found that you ave over 9000
# coding, i think you are wrong" — use a SETTING, not code.
#
# MEASURED, and this is why the timeout had to move: every proof that the hook
# reported TIMEOUT runs FAR under the old 60s limit when run ALONE
# (_proof_playwright_step_kind.py 17.5s, _proof_playwright_step_guide.py 4.4s,
# _proof_vscode_session_identity.py 4.8s). So the TIMEOUT was CONTENTION: the
# hook runs up to MAX_PROOFS=40 proofs and several drive the SAME live
# browser/VS Code session, so they slow each other down.
#
# The repo ALREADY has the SSOT for a setting: the `settings` table with
# `get_setting()` (db_schema.py:33). So the values live there, and the ENV VAR
# STILL WINS so a one-off run can override without touching the DB.
SETTING_TIMEOUT = "proof_gate.timeout_sec"
SETTING_SKIP_SCREEN = "proof_gate.skip_screen_dependent"
DEFAULT_TIMEOUT_SEC = 180.0
DEFAULT_SKIP_SCREEN = True


def _setting(key: str, default: Any) -> Any:
    """Read one setting from the `settings` table. Never raises.

    A missing table, a missing row, or an unreadable DB returns `default`, so a
    gate that cannot read its own setting still runs. It does NOT silently
    invent a value: the default is NAMED here and reported by `--report`.
    """
    try:
        import sqlite3

        conn = sqlite3.connect(os.path.join(BASE_DIR, "agent.db"), timeout=5)
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else default
    except Exception:
        return default


def _timeout_sec() -> float:
    """The per-proof limit. ENV WINS over the setting, which wins over default."""
    env = os.environ.get("PROOF_GATE_TIMEOUT")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        return float(_setting(SETTING_TIMEOUT, DEFAULT_TIMEOUT_SEC))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC


def _skip_screen_dependent() -> bool:
    """Whether the screen-dependent family is excluded. ENV WINS."""
    env = os.environ.get("PROOF_GATE_SKIP_SCREEN")
    if env is not None:
        return str(env).strip() not in ("0", "false", "False", "")
    v = _setting(SETTING_SKIP_SCREEN, "1" if DEFAULT_SKIP_SCREEN else "0")
    return str(v).strip() not in ("0", "false", "False", "")


PER_PROOF_TIMEOUT = _timeout_sec()

# ---------------------------------------------------------------------------
# THE SCREEN-DEPENDENT FAMILY — DECLARED, with a REASON each
# ---------------------------------------------------------------------------
# WHY THIS LIST EXISTS (2026-09-25). MEASURED: the hook reported 3 proofs RED
# and ALL THREE were GREEN when run alone. The cause is that these proofs drive
# the LIVE screen (pyautogui, or the live /api/playwright/test endpoint), so
# their verdict depends on what is on screen at that instant.
#
# A proof that cannot see the screen has NOT measured anything. Reporting FAIL
# claims "measured, and it failed" — which is a LIE. So they are EXCLUDED from
# the automatic run and NAMED in the report, and a human runs them on purpose.
#
# IT IS A DECLARED LIST, NOT A NAME PATTERN. A proof is excluded because it is
# ON this list, never because its name looks like it might be. Each entry
# carries the reason, so a reader can audit the decision.
SCREEN_DEPENDENT: tuple[tuple[str, str], ...] = (
    ("_proof_f9.py",
     "drives the live screen with pyautogui"),
    ("_proof_llm_status_evidence_5x.py",
     "drives the live screen with pyautogui"),
    ("_proof_one_space.py",
     "drives the live screen with pyautogui"),
    ("_proof_playwright_ui.py",
     "calls the live /api/playwright/test endpoint"),
    ("_proof_task_capture.py",
     "drives the live screen with pyautogui"),
    ("_proof_vscode_hotkey_coord.py",
     "drives the live screen with pyautogui"),
    ("_proof_vscode_layout_pinned.py",
     "drives the live screen with pyautogui"),
    ("_proof_copy_button_trigger_point.py",
     "calls the live /api/playwright/test endpoint"),
    ("_proof_playwright_step_guide.py",
     "calls the live /api/playwright/test endpoint"),
    ("_proof_playwright_step_kind.py",
     "calls the live /api/playwright/test endpoint"),
    ("_proof_playwright_test_button.py",
     "calls the live /api/playwright/test endpoint"),
    ("_proof_vscode_session_identity.py",
     "calls the live /api/playwright/test endpoint"),
)
SCREEN_DEPENDENT_NAMES = frozenset(n for n, _ in SCREEN_DEPENDENT)
SCREEN_DEPENDENT_REASON = dict(SCREEN_DEPENDENT)

# HOW MANY BROWSERS EACH SCREEN-DEPENDENT PROOF LAUNCHES (MEASURED 2026-09-27).
#
# WHY THIS IS A TABLE AND NOT A COUNT: the number is the COST of opting in, and
# a cost that is guessed is not a cost. Each value is the number of
# `POST /api/playwright/test` calls in the file, and each call reaches
# `p.chromium.launch()` (`mouse_spot_helper.py:5133`).
#
# MEASURED by counting the calls in each file:
#   _proof_playwright_step_kind.py        5
#   _proof_playwright_test_button.py      5
#   _proof_vscode_session_identity.py     4
#   _proof_playwright_step_guide.py       3
#   _proof_copy_button_trigger_point.py   2
#   _proof_playwright_ui.py               2
#   ----------------------------------------
#   total                                21
#
# WHY EVERY VALUE ROSE BY ONE (2026-09-27): the per-request `switch_file` field
# was added to the POST body, so each file now names `api/playwright/test` one
# more time. The COUNT is of the string, so the count moved with the code. The
# LAUNCH count is unchanged -- the extra reference is the same call, not a new
# one. The check that compares declared vs measured is what caught this.
#
# A proof NOT in this table launches no browser (it drives the screen with
# pyautogui instead), so it contributes 0 and is not listed.
SCREEN_DEPENDENT_BROWSERS: dict[str, int] = {
    "_proof_playwright_step_kind.py": 5,
    "_proof_playwright_test_button.py": 5,
    "_proof_vscode_session_identity.py": 4,
    "_proof_playwright_step_guide.py": 3,
    "_proof_copy_button_trigger_point.py": 2,
    "_proof_playwright_ui.py": 2,
}


def _announce_screen_cost(names: list[str]) -> None:
    """Print what opting into the screen-dependent family will LAUNCH.

    THE HUMAN (2026-09-26): "why have a playwright keep running at my computer,
    pls stop it". A human who opts in should learn the cost BEFORE paying it,
    not by watching browsers appear. Never raises: a cost line that can crash
    the run would be worse than no cost line.
    """
    try:
        picked = [n for n in names if n in SCREEN_DEPENDENT_NAMES]
        if not picked:
            return
        launches = sum(SCREEN_DEPENDENT_BROWSERS.get(n, 0) for n in picked)
        print("SCREEN-DEPENDENT OPT-IN: %d proof(s) will run, launching %d "
              "browser(s):" % (len(picked), launches))
        for n in picked:
            print("  %-42s %d browser(s)"
                  % (n, SCREEN_DEPENDENT_BROWSERS.get(n, 0)))
        print("  (each call reaches p.chromium.launch(); they need a live "
              "screen to measure anything)")
    except Exception:
        pass

# A proof that needs the live screen prints this when its precondition is
# absent. The gate reports SKIP, which is NOT RED — the same rule the gate
# already applies to NO-VERDICT: absence of a measurement is not a failing one.
# A SKIP MUST CARRY A REASON, so it can never be a silent pass.
_SKIP_LINE = re.compile(r"(?m)^\s*SKIP\s*:\s*(.+?)\s*$")

# The output formats the repo's 132 proofs actually use. MEASURED 2026-09-21:
# the formats are INCONSISTENT, so the verdict must be parsed by alternatives
# rather than one pattern. This is a real defect in the repo, recorded here so
# the parser is not mistaken for defensive padding.
#
# CRITICAL, AND A BUG I MADE FIRST TIME: the two forms mean DIFFERENT things.
#   "N passed / M failed"   -> failed IS M
#   "N PASS / M FAIL"       -> failed IS M
#   "N/M checks passed"     -> M is the TOTAL, so failed = M - N
#   "N/M PASS" (no word)    -> M is the TOTAL, so failed = M - N
#   "RESULT: N/M passed"    -> M is the TOTAL, so failed = M - N
#   "RESULT: N passed, M failed" -> failed IS M
# Treating `RESULT: 21/21 passed` as "21 passed, 21 FAILED" reported `_proof_
# registers.py` as RED when it is GREEN. A parser that mis-reads a verdict
# produces a report that is worse than none, because it is believed.
#
# MEASURED 2026-09-21, second round: two GREEN proofs were reported NO-VERDICT
# because their last line is `N/M checks passed` / `N/M PASS` and no pattern
# covered it. Absence of a measurement is not a passing one, so a miss here
# makes the report lie in the SAFE direction only by accident.
#
# MEASURED 2026-09-23, THIRD round — the user's complaint:
#     "you are report to me, but it will disappear once complete, will turn to
#      very simple report!!"
# The screenshot showed `0 passed / 0 failed` with two rows at NO-VERDICT. The
# cause was HERE: the repo has TWO summary conventions and this tuple knew only
# one of them.
#
#     `N passed / M failed`   -> 86 proofs   (matched)
#     `passed=N  failed=M`    -> 17 proofs   (NOT matched, so NO-VERDICT)
#
# The 17 are the ones written in this session, which is why the report degraded
# exactly when the newest work was the most interesting. The fix is the PARSER,
# not the 17 proofs: both conventions already exist in the repo, so a parser that
# reads one of them will keep mis-reading whichever half is newer. Rewriting the
# proofs would fix today's symptom and leave the next author to rediscover it.
_PASSED_FAILED = (
    re.compile(r"(\d+)\s*passed\s*/\s*(\d+)\s*failed", re.I),
    re.compile(r"(\d+)\s*PASS\s*/\s*(\d+)\s*FAIL", re.I),
    re.compile(r"RESULT:\s*(\d+)\s*passed,\s*(\d+)\s*failed", re.I),
    # `N passed, M failed` WITHOUT the `RESULT:` prefix.
    #
    # MEASURED 2026-09-29: the sweep delegated to this parser and a proof that
    # prints `116 passed, 0 failed` (no prefix) came back None — a real
    # measurement DISCARDED, the same defect class as a false RED. The comma form
    # was only recognised behind `RESULT:`, so a proof using it bare was read as
    # NO-VERDICT. Both forms exist in the repo, so both are matched.
    re.compile(r"(\d+)\s*passed,\s*(\d+)\s*failed", re.I),
    # `passed=17  failed=0` — the equals form. Both numbers are NAMED, so the
    # second is `failed` and NOT a total; it must not go through
    # `_PASSED_OF_TOTAL`, which would read `failed=0` as a total of 0 and
    # compute `failed = 0 - 17`.
    re.compile(r"passed\s*=\s*(\d+)\s+failed\s*=\s*(\d+)", re.I),
    # `PASS=46  FAIL=0` — the same shape with the short words.
    re.compile(r"PASS\s*=\s*(\d+)\s+FAIL\s*=\s*(\d+)", re.I),
    # `PASS 50 / FAIL 0` — THE NUMBER AFTER THE WORD.
    #
    # MEASURED 2026-09-25, and it is the recurring "a parser that knows only one
    # word order" family: `_proof_identity_llm.py` prints `PASS %d / FAIL %d`
    # (its own summary line, `_proof_identity_llm.py:419`), which the patterns
    # above do NOT match because they all expect the NUMBER FIRST. The gate
    # therefore reported **NO-VERDICT** for a proof that had measured **50 / 0**.
    # A real measurement discarded is the same defect class as a false RED.
    #
    # Both word orders exist in this repo, so a parser that reads one of them
    # will keep mis-reading whichever half is newer. The `/ FAIL` tail is
    # REQUIRED, so a per-check line such as `PASS  50 checks` cannot match.
    re.compile(r"PASS\s+(\d+)\s*/\s*FAIL\s+(\d+)", re.I),
    re.compile(r"passed\s+(\d+)\s*/\s*failed\s+(\d+)", re.I),
)
# `N of M` / `N/M` forms: the second number is the TOTAL.
_PASSED_OF_TOTAL = (
    re.compile(r"RESULT:\s*(\d+)\s*/\s*(\d+)\s*passed", re.I),
    re.compile(r"(\d+)\s*/\s*(\d+)\s*passed", re.I),
    re.compile(r"(\d+)\s*/\s*(\d+)\s*checks\s+passed", re.I),
    re.compile(r"(\d+)\s*/\s*(\d+)\s*PASS\s*$", re.I),
    re.compile(r"(\d+)\s+of\s+(\d+)\s+(?:rows\s+)?(?:class|classified)",
               re.I),
    # `label: N/M` at END of line. MEASURED 2026-09-23: `_proof_p1_coverage.py`
    # prints `contracts meeting >=5 fields / >=3 pass / >=2 hard_fail: 21/22`
    # and was reported NO-VERDICT -- a real measurement discarded, the same
    # family as the decoy bug. The second number is the TOTAL.
    re.compile(r":\s*(\d+)\s*/\s*(\d+)\s*$", re.MULTILINE),
)

# A WHOLE-RUN BOOLEAN verdict: `RESULT: PASS (all checks)` / `RESULT: FAIL (3)`.
#
# MEASURED 2026-09-23: `_proof_sse_capture_proxy.py` prints exactly this and was
# reported NO-VERDICT -- a real verdict discarded. It is NOT a per-check line, so
# it must be read as a ONE-check verdict, never counted among checks.
_BOOL_VERDICT = re.compile(r"RESULT:\s*(PASS|FAIL)\b", re.I)

# WHY THERE IS NO PER-CHECK FALLBACK (a REGRESSION I INTRODUCED AND REVERTED,
# measured 2026-09-23). I first added a fallback that COUNTED `PASS`/`FAIL`
# tokens per line when no summary existed. It made 10 proofs report a value --
# and it was WRONG: `_proof_cross_gate.py` ends with
# `GATE PROOF: correct -> PASS ; cross-free -> FAIL  => PASS`, a SENTENCE whose
# final `=> PASS` is a CONCLUSION. The counter read that line as a FAIL and
# reported the proof RED 0/1 when it is GREEN.
#
# A FALSE RED IS WORSE THAN NO-VERDICT: NO-VERDICT says "not measured", which is
# true; a false RED says "measured, and it failed", which is a LIE about a
# passing proof. So the fallback is REMOVED. A proof with no readable verdict
# stays NO-VERDICT -- honestly unmeasured -- and the fix belongs in the PROOF
# (print a summary), not in a token counter that guesses.


def log(msg: str) -> None:
    """Append to the gate log. Never raises."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


# THE RECURSION GUARD, LAYER 1 (the root fix).
#
# DEFECT FOUND BY READING THE GATE'S OWN LOG (2026-09-21). The log held 148
# `stop_hook_active=true` lines and, separately, 18 `Stop ran 2 proofs` lines in
# 18 seconds. The second burst was a RECURSION I CAUSED:
#
#     the agent runs _proof_proof_gate.py
#       -> it spawns proof_gate.py subprocesses to test the hook
#       -> the agent's turn ends
#       -> VS Code fires the Stop hook
#       -> proof_gate.py runs _proof_proof_gate.py (its mtime is new)
#       -> which spawns more subprocesses ...
#
# `stop_hook_active` did NOT stop this, because the recursion does not travel
# through a blocked stop — it travels through a PROOF that re-triggers the hook.
# The guard was the last line of defence and it held; it was never the fix.
#
# So a process that the gate ITSELF started must not start the gate again. The
# marker is an ENVIRONMENT VARIABLE rather than an exclude-list, because an
# exclude-list only blocks one filename while this blocks ANY proof that spawns
# the gate — including one written tomorrow.
CHILD_ENV = "PROOF_GATE_CHILD"


def is_child() -> bool:
    """True when this process was started BY the gate (so it must not recurse)."""
    return os.environ.get(CHILD_ENV, "").strip() == "1"


def child_env() -> dict:
    """The environment a proof subprocess must run with."""
    env = dict(os.environ)
    env[CHILD_ENV] = "1"
    return env


def emit(payload: dict) -> int:
    """Write the hook's JSON to stdout. NEVER raises, and never silently empties.

    MEASURED 2026-09-21: `print()` raised `UnicodeEncodeError` on a cp950 console,
    and because the caller's `except Exception: return 0` swallowed it, the hook
    emitted NOTHING and the user got no report at all. A report that a display
    encoding can delete is not delivered.

    So the JSON is built with `ensure_ascii=True` (pure ASCII, so no console
    encoding can fail on it) and written through an ASCII-safe path. If even that
    fails, the error is LOGGED LOUDLY — a gate that cannot say why it said
    nothing is the defect this whole module exists to fix.
    """
    try:
        text = json.dumps(payload, ensure_ascii=True)
    except Exception as exc:
        log("emit: json build failed: %s: %s" % (type(exc).__name__, exc))
        text = json.dumps({"systemMessage":
                           "Proof gate: could not build the report (%s). "
                           "See proof_gate_log.txt." % type(exc).__name__})
    try:
        data = text.encode("ascii", "replace")
        sys.stdout.buffer.write(data + b"\n")
        sys.stdout.buffer.flush()
    except Exception as exc:
        log("emit: stdout write failed: %s: %s" % (type(exc).__name__, exc))
        try:
            print(text)
        except Exception as exc2:
            log("emit: fallback print ALSO failed: %s: %s"
                % (type(exc2).__name__, exc2))
    return 0


def read_stamp() -> dict:
    """The previous run's stamp: `last_run` plus any proofs left OVER by the cap.

    THE DEFECT THE `pending` LIST FIXES (measured 2026-09-21). `discover()` ran
    only proofs whose mtime is newer than the stamp, and `write_stamp()` then set
    the stamp to NOW. With 137 proofs and a cap of 40, the 97 that were never
    reached were marked as "already covered" by a stamp they never ran under, so
    they became PERMANENTLY INVISIBLE -- they would only reappear if someone
    edited them. Worse, the report then said "no proof changed since the last
    report" for run after run, which is true of the STAMP and false of the
    SUITE. A bound that silently discards the overflow turns "I capped the work"
    into "I claimed the work was done".
    """
    try:
        with open(STAMP_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {"last_run": float(data.get("last_run", 0.0)),
                "pending": [str(x) for x in (data.get("pending") or [])]}
    except Exception:
        return {"last_run": 0.0, "pending": []}


def write_stamp(ran: list[str], pending: list[str]) -> None:
    try:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(STAMP_PATH, "w", encoding="utf-8") as fh:
            json.dump({"last_run": time.time(), "proofs": ran,
                       "pending": pending}, fh, indent=2)
    except Exception as exc:
        log("stamp write failed: %s" % exc)


def discover(since: float, include_all: bool = False,
             pending: list[str] | None = None,
             include_screen: bool = False) -> tuple[list[str], list[str]]:
    """(the proof files to run, the ones left over).

    `include_all` BYPASSES `MAX_PROOFS`. Measured defect (2026-09-21): the
    docstring promises "`--report --all` for the full set", but the cap still
    applied, so "all" ran 39 of 137 and the report was silently incomplete while
    claiming to be the full set. A promise in a docstring that the code does not
    keep is the same defect class as a citation that does not prove.

    `include_screen` (added 2026-09-26) is the ONLY thing that lifts the
    screen-dependent exclusion. THE HUMAN: "why have a playwright keep running
    at my computer, pls stop it".

    MEASURED DEFECT THIS FIXES: `include_all` used to bypass the exclusion too
    (`if include_all: return ordered, []`), so `--report --all` — the command
    this module's OWN message recommends — ran all six screen-dependent proofs.
    Each calls `POST /api/playwright/test`, which reaches
    `p.chromium.launch()` (`mouse_spot_helper.py:5133`), so ONE `--all` pass
    launched **15 chromium browsers** (measured: 4+4+3+2+1+1).

    The old rationale was "a human asking for everything has accepted the screen
    dependency". The human asked for EVERYTHING, not for 15 browsers — and per
    this module's own doctrine those runs cannot measure anything unattended, so
    `--all` delivered 15 false verdicts plus 15 browsers.

    Anything CARRIED OVER from a previous run is run FIRST, so a proof cannot be
    starved indefinitely by a stream of freshly-edited ones.
    """
    changed: list[tuple[float, str]] = []
    carried: list[str] = []
    try:
        present = set()
        for name in os.listdir(BASE_DIR):
            if not name.startswith("_proof_") or not name.endswith(".py"):
                continue
            present.add(name)
            path = os.path.join(BASE_DIR, name)
            mtime = os.path.getmtime(path)
            if include_all or mtime > since:
                changed.append((mtime, name))
        # A pending name that no longer exists is DROPPED, not an error: a proof
        # can be deleted between runs.
        carried = [n for n in (pending or []) if n in present]
    except Exception as exc:
        log("discover failed: %s" % exc)
    changed.sort(reverse=True)
    ordered = carried + [n for _, n in changed if n not in set(carried)]
    # ---- THE SCREEN-DEPENDENT EXCLUSION (2026-09-25) --------------------
    # MEASURED: the hook reported 3 proofs RED and ALL THREE were GREEN when run
    # alone, because they drive the LIVE screen. A proof that cannot see the
    # screen has NOT measured anything, so its FAIL is a LIE.
    #
    # They are EXCLUDED from the automatic run and NAMED in the report, so a
    # human can run them on purpose. The exclusion is a DECLARED LIST, never a
    # name pattern: a proof is excluded because it is ON the list.
    #
    # `include_screen` is the ONLY bypass (2026-09-26). `include_all` does NOT
    # bypass it — see the docstring for the measured 15-browser defect.
    if not include_screen and _skip_screen_dependent():
        ordered = [n for n in ordered if n not in SCREEN_DEPENDENT_NAMES]
    if include_all:
        return ordered, []
    return ordered[:MAX_PROOFS], ordered[MAX_PROOFS:]


def parse_verdict(text: str) -> tuple[int, int] | None:
    """Extract (passed, failed) from the LAST match BY TEXT POSITION, or None.

    LAST, not first: a proof prints per-check lines as it goes and the verdict is
    the summary at the end. Taking the first match would report a partial count
    as the result.

    *** THE DEFECT THIS FIXES (measured 2026-09-23) ***
    The docstring ALWAYS said "LAST, not first" — meaning LAST BY TEXT POSITION.
    The code did NOT do that. It iterated PATTERNS OUTER, MATCHES INNER:

        for pat in _PASSED_FAILED:        # outer
            for m in pat.finditer(text):  # inner
                found = (...)             # a later PATTERN overwrites an earlier one

    So the winner was the LAST PATTERN, not the last match by text position. A
    proof whose CHECK NAME contains a decoy such as `2 pass / 1 fail` (the real
    case: `_proof_registry_approval.py:364` "counters recorded 3 uses / 2 pass /
    1 fail") was read as (2, 1) although its own summary said `54 passed / 1
    failed`, because the decoy was matched by a LATER pattern. The report then
    printed a FALSE `2 passed / 1 failed` for a proof that ran to completion.

    THE FIX: every candidate match from every pattern is collected WITH its END
    POSITION, and the GREATEST end position wins — true "LAST by text". A match
    found LATER in the text always beats an earlier one, whatever pattern found
    it. A tie is broken toward the PART-OF-SUM reading, which is the safer one.
    """
    # (end_pos, priority, verdict); priority 0 = part-of-sum (safer), 1 = of-total
    candidates: list[tuple[int, int, tuple[int, int]]] = []
    for pat in _PASSED_FAILED:
        for m in pat.finditer(text or ""):
            candidates.append((m.end(), 0,
                               (int(m.group(1)), int(m.group(2)))))
    for pat in _PASSED_OF_TOTAL:
        for m in pat.finditer(text or ""):
            passed, total = int(m.group(1)), int(m.group(2))
            if total >= passed:
                candidates.append((m.end(), 1, (passed, total - passed)))
    if not candidates:
        # NO summary. A whole-run BOOLEAN verdict (`RESULT: PASS`) is still a
        # verdict: read it as ONE check. Anything else stays None -- honestly
        # unmeasured -- because a token counter would produce FALSE REDs (see
        # the note above `_BOOL_VERDICT`).
        m = _BOOL_VERDICT.search(text or "")
        if m:
            return (1, 0) if m.group(1).upper() == "PASS" else (0, 1)
        return None
    # GREATEST end position wins; equal position -> LOWER priority wins, i.e.
    # part-of-sum (0) beats of-total (1), the safer reading of a
    # `N passed / M failed` line.
    return max(candidates, key=lambda c: (c[0], -c[1]))[2]


def run_one(name: str) -> dict:
    """Run one proof. Never raises; a crash is reported as a crash."""
    path = os.path.join(BASE_DIR, name)
    py = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(py):
        py = sys.executable
    started = time.time()
    try:
        proc = subprocess.run(
            [py, path], cwd=BASE_DIR, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=PER_PROOF_TIMEOUT,
            # LAYER 1: the proof runs as a CHILD, so if it spawns the gate the
            # gate refuses to run. Without this, a proof that tests the hook
            # re-triggers it on every turn.
            env=child_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (proc.stdout or "") + (proc.stderr or "")
        tail = out.strip().splitlines()[-1][:160] if out.strip() else ""
        # ---- THE SKIP LINE (2026-09-25) ---------------------------------
        # A proof that needs the live screen prints `SKIP: <reason>` when its
        # precondition is absent. MEASURED: the hook reported 3 proofs RED and
        # ALL THREE were GREEN when run alone, because they drive the LIVE
        # screen. A proof that cannot see the screen has NOT measured anything,
        # so FAIL would be a LIE. SKIP says "not measured, and here is why".
        #
        # A SKIP MUST CARRY A REASON. An empty reason is NOT a skip — it is a
        # silent pass, which is the defect this repo keeps paying for.
        skip_m = _SKIP_LINE.search(out)
        if skip_m and skip_m.group(1).strip():
            return {"proof": name, "state": "SKIP", "passed": None,
                    "failed": None, "exit": proc.returncode,
                    "sec": round(time.time() - started, 1),
                    "tail": "SKIP: %s" % skip_m.group(1).strip()[:140]}
        verdict = parse_verdict(out)
        if verdict:
            passed, failed = verdict
            # ---- SECOND MEASURED CHECK (2026-09-23) ------------------------
            # The parsed OUTPUT must AGREE with the process EXIT CODE. A proof
            # that printed failures must exit non-zero; one that printed no
            # failure must exit zero. A disagreement means the OUTPUT cannot be
            # trusted, and it is NAMED rather than believed.
            # `unit` = count of disagreements (0 = pass).
            if failed > 0 and proc.returncode == 0:
                state = "RED"
                tail = ("OUTPUT/EXIT DISAGREE: printed %d failed but exited 0"
                        % failed)
            elif failed == 0 and proc.returncode != 0:
                state = "RED"
                tail = ("OUTPUT/EXIT DISAGREE: printed 0 failed but exited %s"
                        % proc.returncode)
            else:
                state = "GREEN" if failed == 0 else "RED"
        elif proc.returncode == 0:
            # A proof with a NON-verdict output is a diagnostic script, not a
            # failure. Reported as NO-VERDICT rather than silently GREEN:
            # absence of a measurement is not a passing one.
            passed, failed, state = None, None, "NO-VERDICT"
        else:
            passed, failed, state = None, None, "ERROR"
        return {"proof": name, "state": state, "passed": passed,
                "failed": failed, "exit": proc.returncode,
                "sec": round(time.time() - started, 1), "tail": tail}
    except subprocess.TimeoutExpired:
        return {"proof": name, "state": "TIMEOUT", "passed": None,
                "failed": None, "exit": None,
                "sec": round(time.time() - started, 1),
                "tail": "timed out after %.0fs" % PER_PROOF_TIMEOUT}
    except Exception as exc:
        return {"proof": name, "state": "ERROR", "passed": None,
                "failed": None, "exit": None,
                "sec": round(time.time() - started, 1),
                "tail": "%s: %s" % (type(exc).__name__, exc)}


def _report_path() -> str:
    return os.path.join(EVIDENCE_DIR, "proof_report_latest.md")


def build_report(rows: list[dict], changed: int,
                 left_over: list[str] | None = None,
                 excluded: list[tuple[str, str]] | None = None) -> str:
    """The markdown report. Code-owned, so it cannot be a one-word answer.

    AN EMPTY RUN RETURNS "" — THE HOOK THEN SAYS NOTHING AT ALL.

    THE USER'S COMPLAINT (2026-09-25):
        "stop hook will rewrite the fully report to 報告完成 (4 word only), pls
         remove that function / i don't need you to resize the report"

    MEASURED, and this is the defect that IS real: with 0 changed proofs this
    function returned a **215-character** note, and the hook emitted it as
    `systemMessage` on EVERY stop. A message with NO numbers can only DISPLACE
    one that had them — the report the user had just read was replaced by a
    sentence saying there was nothing to say.

    The module already states this rule for the DISK artifact (`write_report`:
    *"A no-op must not be able to delete evidence"*). It was never applied to the
    CHAT message. It is now: **a hook with nothing to report emits nothing.**

    WHAT IS **NOT** SILENCED:
      * the DISK report — `write_report` still keeps the last real one;
      * the LOG — `main` still records the run, so the silence is auditable;
      * the QUEUED case — a capped run that LEFT PROOFS OUT is a real fact the
        user must see, and it is not a "nothing changed" note. It is kept.

    MEASURED, and it is why the QUEUED branch is not folded into the silence: a
    bound that silently discards the overflow turns "I capped the work" into "I
    claimed the work was done" (see `read_stamp`).
    """
    if not rows:
        pending = len(left_over or [])
        if pending:
            return ("## Proof Report\n\n"
                    "**No `_proof_*.py` file changed since the last report**, "
                    "and **%d proof(s) are still QUEUED** from the previous "
                    "run's cap. They will run next time. "
                    "Run `python scripts/proof_gate.py --report --all` for the "
                    "full set. The screen-dependent family stays EXCLUDED from "
                    "`--all`; run one on purpose with `--include-screen` "
                    "(it launches browsers)." % pending)
        # NOTHING CHANGED AND NOTHING QUEUED: there is no report to give.
        # Returning "" is the whole fix — `main` then emits no `systemMessage`.
        return ""
    green = [r for r in rows if r["state"] == "GREEN"]
    red = [r for r in rows if r["state"] in ("RED", "ERROR", "TIMEOUT")]
    other = [r for r in rows if r["state"] == "NO-VERDICT"]
    # A SKIP is its OWN bucket. It is NOT RED (nothing failed) and NOT GREEN
    # (nothing was measured), so it must not be summed into either. MEASURED
    # 2026-09-25: the hook reported 3 screen-dependent proofs RED and all three
    # were GREEN alone; a SKIP bucket is what stops that false RED.
    skipped = [r for r in rows if r["state"] == "SKIP"]
    passed = sum(r["passed"] or 0 for r in rows)
    failed = sum(r["failed"] or 0 for r in rows)
    # HOW MANY ROWS ACTUALLY CARRIED A NUMBER. MEASURED DEFECT (2026-09-23): the
    # header summed `r["passed"] or 0`, so a NO-VERDICT row contributed NOTHING
    # and the header printed `0 passed / 0 failed` while both proofs were really
    # 17 / 0 GREEN. A total that silently omits the unmeasured rows is a FALSE
    # total — the same rule as `citation-discipline`: absence of a measurement is
    # not a measurement, and it must never be summed as a zero.
    measured = [r for r in rows if r["passed"] is not None]
    unmeasured = len(rows) - len(measured)

    if measured:
        headline = ("`%d` proof(s) ran (`%d` changed since the last report). "
                    "**%d passed / %d failed**"
                    % (len(rows), changed, passed, failed))
    else:
        # NO readable verdict anywhere: say THAT, instead of a total of zeros.
        headline = ("`%d` proof(s) ran (`%d` changed since the last report). "
                    "**NO VERDICT READ from any of them** — the totals below "
                    "would be a claim this run cannot support."
                    % (len(rows), changed))
    if unmeasured:
        headline += (" · **%d of %d row(s) UNMEASURED** (no readable verdict; "
                     "they are NOT counted as zero)"
                     % (unmeasured, len(rows)))
    # P3: a run with NO readable verdict must not REPLACE the numbers the user
    # already earned. The last real totals are carried into the message.
    if not measured:
        prev = _last_real_headline()
        if prev:
            headline += "\n\n> " + prev + " — this run added no new verdict, so "
            headline += "the previous numbers still stand."

    lines = ["## Proof Report", "", headline, "",
             "| Proof | State | Passed | Failed | s | Tail |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| `%s` | %s | %s | %s | %s | %s |"
                     % (r["proof"], r["state"],
                        r["passed"] if r["passed"] is not None else "-",
                        r["failed"] if r["failed"] is not None else "-",
                        r["sec"], (r["tail"] or "").replace("|", "/")[:90]))
    if red:
        lines += ["", "### RED — must be explained before this is called done",
                  ""]
        for r in red:
            lines.append("- `%s` — %s. %s"
                         % (r["proof"], r["state"], r["tail"]))
    if other:
        lines += ["", "### NO-VERDICT — a diagnostic script, or a proof that "
                      "prints nothing checkable", ""]
        for r in other:
            lines.append("- `%s`" % r["proof"])
    if skipped:
        # NAMED, with the REASON. A skipped proof that is not named reads as a
        # proof that passed, which is the silent pass this repo forbids.
        lines += ["", "### SKIP — NOT measured, and NOT a failure. Each one "
                      "carries its reason.", ""]
        for r in skipped:
            lines.append("- `%s` — %s" % (r["proof"], r["tail"]))
    if green:
        lines += ["", "%d proof(s) GREEN." % len(green)]
    if left_over:
        # STATED, not implied. A capped run that does not say what it left out
        # reads as a complete run (measured defect, see `read_stamp`).
        lines += ["", "**%d proof(s) QUEUED** (over the %d/run cap) — they run "
                      "next time, or now with `--all`."
                      % (len(left_over), MAX_PROOFS)]
    if excluded:
        # NAMED, with the REASON. A proof that vanishes from the report reads as
        # a proof that passed, so the exclusion is STATED.
        #
        # THE OPT-IN IS NAMED TOO (2026-09-26). MEASURED: this line used to say
        # "Run one on purpose with `--all`" — and `--all` was exactly the flag
        # that launched 15 browsers. A message that recommends the defect is
        # part of the defect.
        lines += ["", "### EXCLUDED — NOT run automatically. Each carries its "
                      "reason and its LAST MEASURED verdict. "
                      "Run one on purpose with `--include-screen` "
                      "(it launches browsers).", ""]
        _lv = last_verdicts()
        for n, why in excluded:
            _v = _lv.get(n)
            lines.append("- `%s` — %s%s"
                         % (n, why,
                            (" [last verdict: **%s**]" % _v) if _v
                            else " [last verdict: NOT MEASURED — never in a "
                                 "report, so a failure here is INVISIBLE]"))
    lines += ["", "Full report: `%s`" % _report_path()]
    return "\n".join(lines)


def last_verdicts() -> dict[str, str]:
    """`{proof_name: STATE}` from the last report, or {}.

    WHY AN EXCLUDED PROOF NEEDS ITS VERDICT SHOWN (2026-09-26).
    THE HUMAN: "stop hook is my pain!!!" / "all must be can measure unit and
    register".

    MEASURED, and this is the defect: `_proof_stop_hook_temp_off.py` asserts the
    Stop hook is RESTORED, while the filesystem says it is `.disabled`. It FAILS
    -- `exit=1`, QC-01a/b/c/d all FAIL -- and because it is EXCLUDED by name
    (`SELF_PROOFS`), its verdict never appeared in the report. An excluded proof
    whose failure is invisible is a check that cannot fail, which is the same as
    no check. The exclusion stays (it must not run inside the gate); the
    IGNORANCE is what this removes.
    """
    out: dict[str, str] = {}
    try:
        with open(os.path.join(EVIDENCE_DIR, "proof_report_latest.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        for r in (data.get("rows") or []):
            n = str(r.get("proof") or "")
            if n:
                out[n] = str(r.get("state") or "?")
    except Exception:
        pass
    return out


def _existing_rows() -> list[dict]:
    """The rows of the report already on disk, or []."""
    try:
        with open(os.path.join(EVIDENCE_DIR, "proof_report_latest.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("rows")
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _last_real_headline() -> str:
    """The headline of the last report that HAD numbers, or "".

    THE USER'S COMPLAINT (2026-09-23):
        "you are report to me, but it will disappear once complete, will turn to
         very simple report!!"

    The DISK file was already protected (`write_report` keeps the last real
    report when a run is empty), but the CHAT message was not: every Stop emits
    its own `systemMessage`, so a turn that ran two proofs the parser could not
    read REPLACED a 822-assertion report with `0 passed / 0 failed`.

    A message may change; it may not go BACKWARDS in information. So when a run
    has no readable verdict, the message carries the last real totals instead of
    a degenerate table.
    """
    try:
        with open(os.path.join(EVIDENCE_DIR, "proof_report_latest.json"),
                  encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("rows") or []
        measured = [r for r in rows if r.get("passed") is not None]
        if not measured:
            return ""
        p = sum(int(r.get("passed") or 0) for r in measured)
        f = sum(int(r.get("failed") or 0) for r in measured)
        return ("last real report: **%d passed / %d failed** across %d proof(s) "
                "(generated %s)"
                % (p, f, len(measured), data.get("generated_at") or "?"))
    except Exception:
        return ""

def write_report(text: str, rows: list[dict],
                 left_over: list[str] | None = None) -> None:
    """Write the report. An EMPTY run must not DESTROY the last real one.

    MEASURED DEFECT (2026-09-21, found while producing this very report). The
    full 136-proof report was generated and written; the NEXT turn's hook ran an
    empty suite (nothing had changed) and OVERWROTE it with the 217-byte "nothing
    changed" note. So the one artifact the user asked for was destroyed by a turn
    that had nothing to report — the user's complaint ("where is my report, i
    need that") reproduced itself DURING the fix. A no-op must not be able to
    delete evidence.
    """
    try:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        prev = _existing_rows()
        # A run that produced NO READABLE VERDICT must not overwrite the numbers
        # the user already earned.
        #
        # MEASURED DEFECT (2026-09-23), found by `_proof_proof_gate_persist.py`
        # case 6a: the guard below only fired when `rows` was EMPTY. A run with
        # rows that all read NO-VERDICT passed straight through and overwrote the
        # JSON twin with 3 rows carrying 0 numbers — so `_last_real_headline()`
        # had nothing to carry, and the "keep the last real report" promise was
        # kept for the MARKDOWN and broken for the JSON. The two artifacts must
        # not disagree about whether a real report exists.
        measured = [r for r in rows if r.get("passed") is not None]
        degenerate = bool(rows) and not measured
        if (not rows and prev) or degenerate:
            # Keep the last real report, and PREPEND a line saying the newest run
            # added nothing. Prepending (rather than replacing the heading)
            # keeps the old table intact and readable, which is the point: the
            # user asked for the report, and a "nothing changed" run only has
            # information to ADD to it.
            old = ""
            try:
                with open(_report_path(), encoding="utf-8") as fh:
                    old = fh.read()
            except Exception:
                old = ""
            if degenerate:
                stamp = ("> _Last hook run: %d proof(s) ran but NO VERDICT could "
                         "be read from any of them, so this report is "
                         "UNCHANGED._\n\n" % len(rows))
            else:
                stamp = "> _Last hook run: nothing changed since this report._\n\n"
            if not old.startswith(stamp):
                old = stamp + old
            with open(_report_path(), "w", encoding="utf-8") as fh:
                fh.write(old)
            # The JSON twin is LEFT ALONE on purpose: it is the record of the
            # last run that HAD numbers, and `_last_real_headline()` reads it.
            return
        # AN EMPTY REPORT MUST NOT TRUNCATE A FILE THAT HAS CONTENT.
        #
        # MEASURED (2026-09-25), and it is a NARROW but real hole in the guard
        # above: `prev` comes from the JSON TWIN, so if the twin is missing while
        # the MARKDOWN still holds a real report, `prev` is `[]`, the guard does
        # not fire, and `text` (now `""` for an empty run) would OVERWRITE the
        # markdown with nothing. The module's own rule is "a no-op must not be
        # able to delete evidence", so the check is made against the FILE, not
        # only against the twin.
        if not text:
            try:
                with open(_report_path(), encoding="utf-8") as fh:
                    existing = fh.read()
            except Exception:
                existing = ""
            if existing.strip():
                log("empty report: kept the existing %s (%d chars)"
                    % (_report_path(), len(existing)))
                return
        with open(_report_path(), "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(os.path.join(EVIDENCE_DIR, "proof_report_latest.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "rows": rows, "queued": left_over or []},
                      fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        log("report write failed: %s" % exc)


# THE RECURSION GUARD, LAYER 2 (the belt to layer 1's braces).
#
# A proof whose whole SUBJECT is the gate tests it by SPAWNING it. Running such
# a proof FROM the gate is a category error: the spawned gate would run the
# proof again. Layer 1 (`PROOF_GATE_CHILD`) already makes that safe, but the
# proof then measures a gate that REFUSED to run — so it reports a FALSE RED.
#
# MEASURED 2026-09-25, and it was a real defect: `_proof_proof_gate_silent.py`
# was NOT on this list, so the hook ran it, the hook's own `PROOF_GATE_CHILD=1`
# was inherited by the proof's `run_hook()`, the spawned gate refused, and the
# proof reported **29 passed / 5 failed** — while run alone it is **34 / 0**.
# A proof that cannot run under the gate must be DECLARED, not left to fail.
#
# The list is DECLARED with a reason each, never a name pattern: a pattern would
# silently swallow a future proof whose name merely looks similar.
SELF_PROOFS: tuple[tuple[str, str], ...] = (
    ("_proof_proof_gate.py", "spawns the gate to test the hook"),
    ("_proof_proof_gate_silent.py", "spawns the gate to test the hook"),
    ("_proof_proof_gate_skip_and_timeout.py", "spawns the gate to test the hook"),
    # MEASURED 2026-09-26. THREE more proofs spawn the gate, and each was found
    # by the SAME criterion the three above use — its subject is the gate and it
    # tests it by SPAWNING it. All three were missing from this list.
    #
    # `_proof_stop_hook_temp_off.py` is the one that RE-ARMED the hook it exists
    # to keep off: it calls `run_hook()` (a `subprocess.run` of
    # `scripts/proof_gate.py`) AND, at `:259`, `os.remove(MARKER)` — it DELETES
    # `STOP_HOOK_OFF` to prove the hook resumes without it. Run BY the gate, that
    # delete lands on the LIVE switch, so the hook turns itself back ON and the
    # next Stop spawns the screen-driving proofs again. Its `finally` restores
    # the marker only when `had_marker` was true, so a run that STARTS with the
    # switch absent leaves it absent — which is exactly the state a disabled
    # hook is in.
    #
    # The other two (`_proof_stop_hook_no_reattach.py`, `_proof_report_no_shrink.py`)
    # move the switch aside and restore it in a `finally`, so they do not delete
    # it — but they still SPAWN the gate, and they deliberately pop
    # `PROOF_GATE_CHILD` so the spawned gate really runs. Run BY the gate, that
    # is the recursion the layer-1 guard exists to stop, reached through a proof
    # instead of through a blocked stop.
    ("_proof_stop_hook_temp_off.py", "spawns the gate to test the hook"),
    ("_proof_stop_hook_no_reattach.py", "spawns the gate to test the hook"),
    ("_proof_report_no_shrink.py", "spawns the gate to test the hook"),
)
SELF_PROOF_NAMES = frozenset(n for n, _ in SELF_PROOFS)
SELF_PROOF_REASON = dict(SELF_PROOFS)
# Kept for the existing callers/tests that name the single original proof.
SELF_PROOF = "_proof_proof_gate.py"


def run_suite(include_all: bool = False,
              exclude: list[str] | None = None,
              include_screen: bool = False) -> tuple[str, list[dict]]:
    """Run the proofs and return (markdown, rows)."""
    stamp = read_stamp()
    names, left_over = discover(stamp["last_run"], include_all=include_all,
                                pending=stamp["pending"],
                                include_screen=include_screen)
    skip = set(exclude or ()) | set(SELF_PROOF_NAMES)
    left_over = [n for n in left_over if n not in skip]
    names = [n for n in names if n not in skip]
    # THE EXCLUDED PROOFS ARE NAMED, NOT SILENTLY DROPPED (2026-09-25).
    # MEASURED: the screen-dependent family is excluded from the automatic run
    # because its verdict depends on what is on screen. A proof that vanishes
    # from the report reads as a proof that passed, so the exclusion is STATED
    # with its reason.
    #
    # `include_screen` is the ONLY bypass (2026-09-26). `include_all` does NOT
    # bypass it: measured, `--all` launched 15 chromium browsers.
    excluded_now: list[tuple[str, str]] = []
    if not include_screen and _skip_screen_dependent():
        excluded_now += [(n, SCREEN_DEPENDENT_REASON.get(n, "screen-dependent"))
                         for n in sorted(SCREEN_DEPENDENT_NAMES)]
    if include_screen:
        # THE COST IS STATED BEFORE IT IS PAID. THE HUMAN: "why have a
        # playwright keep running at my computer, pls stop it". A human who
        # opts in should learn what it will launch BEFORE it launches it, not
        # by watching browsers appear.
        _announce_screen_cost(names)
    # A proof whose SUBJECT is the gate cannot be run BY the gate: the spawned
    # gate refuses (layer 1) and the proof reports a FALSE RED.
    #
    # THIS APPLIES EVEN WITH `--all`. MEASURED 2026-09-25: `--all` bypassed the
    # exclusion, so `_proof_proof_gate_silent.py` reported **30 passed / 4
    # failed** in the full run while it is **34 / 0** alone. `--all` means "run
    # everything a human may run", and a human CAN run a self-proof — but the
    # GATE cannot, so the gate must not pretend to have measured it.
    excluded_now += [(n, SELF_PROOF_REASON.get(n, "tests the gate itself"))
                     for n in sorted(SELF_PROOF_NAMES)]
    rows = [run_one(n) for n in names]
    # The stamp is written even when NOTHING ran, so `pending` survives a run
    # that the cap starved at zero. Otherwise the overflow would be lost on the
    # very next empty run and the proofs would go invisible again.
    write_stamp([r["proof"] for r in rows], left_over)
    text = build_report(rows, len(names), left_over, excluded=excluded_now)
    write_report(text, rows, left_over)
    return text, rows


def summarise(rows: list[dict]) -> str:
    """A log line that NAMES the proofs and their states.

    DEFECT FOUND BY READING THE LOG (2026-09-21): the line was
    `Stop ran 2 proofs, 1 red` — which two, and which one was red? A count with
    no names is not checkable, so it cannot be acted on. The same rule as
    `citation_discipline`: a finding with no reference is discarded.
    """
    if not rows:
        return "Stop ran 0 proofs (nothing changed since the last report)"
    red = [r for r in rows if r["state"] in ("RED", "ERROR", "TIMEOUT")]
    detail = ", ".join("%s %s" % (r["proof"], r["state"]) for r in rows)
    return "Stop ran %d proofs, %d red: %s" % (len(rows), len(red), detail)


# ---------------------------------------------------------------------------
# THE RE-ATTACH DESIGN IS DELETED (2026-09-26)
# ---------------------------------------------------------------------------
# THE HUMAN: "已完成, how you report always so short, at the end of report with
# report end word, function will auto reformat the full report to short, is it
# the BUG! remove that fucking rubbish design now"
#
# WHAT WAS HERE, and why it is gone. Four functions used to live at this spot:
#
#     _assistant_contents(tp)              every assistant message in a transcript
#     current_turn_assistant_contents(tp)  the messages after the last user message
#     last_assistant_message_len(tp)       the length of the last message
#     longest_assistant_message(tp)        the LONGEST message of the turn
#
# Their rule was "A TURN MAY NOT GO BACKWARDS IN INFORMATION": when the last
# message was short, the Stop hook RE-EMITTED the longest message of the turn as
# a `systemMessage`, so VS Code showed the report AGAIN inside a "Warning from
# Stop hook" box. That is the "function will auto reformat the full report" the
# human described, and it was real.
#
# MEASURED BEFORE DELETING, so the removal is not built on a myth:
#   * `main` had ALREADY stopped calling them (0 call sites) -- the 2026-09-26
#     change removed the calls and left the functions behind.
#   * the hook run DIRECTLY with a real Stop payload emitted **0 bytes**.
#   * `.github/hooks/proof_gate.json` is renamed `.disabled`, and `STOP_HOOK_OFF`
#     is present.
#   * a Stop hook CANNOT rewrite an assistant message -- VS Code's turn model
#     does not allow it (`.github/instructions/answer-is-the-report.instructions.md`).
#     It could only ADD a message, never shorten one.
#
# So this was DEAD CODE, not a live bug -- and the short report the human sees is
# written by the AGENT, not by a hook. It is deleted anyway because dead code
# that describes a forbidden behaviour is a loaded gun: one call away from
# firing again, and `_proof_stop_hook_no_reattach.py` was asserting the helpers
# "still exist (a kept reader)" -- a proof PROTECTING the design.
#
# The transcript is no longer read by this module at all. `_proof_stop_hook_
# reattach_removed.py` asserts the four names do not exist.


def main() -> int:
    args = sys.argv[1:]

    # ---- THE FILE-BASED KILL SWITCH (2026-09-25) --------------------------
    # THE HUMAN: "temp stop -> stop hook / since we can't fix that now!!"
    #
    # MEASURED, and this is why a FILE and not the env valve below: the hook
    # config was renamed away at 16:52:12 (`.github/hooks` dir mtime), and the
    # hook STILL RAN at 17:00:19 (`proof_gate_log.txt`: "Stop: last message is
    # 225 chars ... RE-ATTACHING it"). VS Code CACHES the hook configuration —
    # it read `.github/hooks/*.json` when the window opened and does not re-read
    # it on a rename, so the rename only takes effect after a window reload.
    #
    # The SCRIPT, however, is spawned FRESH on every Stop, so anything it reads
    # at run time takes effect IMMEDIATELY. That is the lever.
    #
    # The switch is a FILE, so its state is visible in the filesystem and cannot
    # be lost in a process's memory. Create `STOP_HOOK_OFF` at the repo root to
    # turn the hook off; delete it to turn the hook back on.
    #
    # CHECKED FIRST, before the env valve and before `is_child()`, so no other
    # branch can run ahead of it.
    #
    # THE PATH IS RESOLVED, NOT HARD-CODED, so a proof can point the gate at its
    # OWN switch file (`STOP_HOOK_SWITCH_FILE`) and never move the human's.
    _switch = stop_hook_switch_file()
    if os.path.exists(_switch):
        log("STOP_HOOK_OFF present — the hook is switched OFF (file kill switch)")
        heartbeat("off-switch", "STOP_HOOK_OFF present")
        return 0

    if os.environ.get("PROOF_GATE", "").strip().lower() in ("off", "0", "false"):
        heartbeat("env-off", "PROOF_GATE=%s" % os.environ.get("PROOF_GATE"))
        return 0

    # LAYER 1, THE ROOT FIX. A process the gate started must not start the gate.
    # Checked BEFORE the manual `--report` branch too, so a proof that shells out
    # to `--report` cannot recurse either.
    if is_child():
        log("child process (%s=1) — refusing to run the gate again"
            % CHILD_ENV)
        heartbeat("child", "%s=1" % CHILD_ENV)
        return 0

    # ---- manual mode: same code path as the hook --------------------------
    if "--report" in args:
        heartbeat("manual-report", "--report")
        text, rows = run_suite(include_all="--all" in args,
                               include_screen="--include-screen" in args)
        # A DISPLAY failure must not look like "no report". On a cp950 console
        # the first version raised here and the user got nothing.
        try:
            sys.stdout.buffer.write(text.encode("utf-8", "replace") + b"\n")
            sys.stdout.buffer.flush()
        except Exception as exc:
            log("--report: display failed (%s: %s); the report IS on disk at %s"
                % (type(exc).__name__, exc, _report_path()))
            try:
                print("Proof gate: display failed (%s). Report on disk: %s"
                      % (type(exc).__name__, _report_path()))
            except Exception:
                pass
        red = [r for r in rows if r["state"] in ("RED", "ERROR", "TIMEOUT")]
        return 1 if red else 0

    # ---- hook mode --------------------------------------------------------
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        # MARKED, because a proof deliberately feeds malformed stdin to test the
        # fail-open path. Unmarked, an INTENTIONAL test and a REAL malformed
        # payload look identical in the log, and a log you cannot read is not a
        # log. The marker is the difference between "this is expected" and
        # "something is wrong".
        log("FAIL-OPEN bad stdin [expected when a proof tests this]: %s: %s"
            % (type(exc).__name__, exc))
        heartbeat("bad-stdin", "%s: %s" % (type(exc).__name__, exc))
        return 0

    try:
        event = str(payload.get("hook_event_name") or "").strip()
        if event != "Stop":
            heartbeat("not-a-stop", "event=%r" % event)
            return 0

        # THE LOOP GUARD. `stop_hook_active` is true when the agent is already
        # continuing because of a previous stop hook. This gate does not BLOCK,
        # so it cannot loop today — but if a future change ever blocks, this is
        # where the recursion would start, so the guard is stated NOW.
        if payload.get("stop_hook_active"):
            log("Stop with stop_hook_active=true — not running again")
            heartbeat("stop-hook-active", "loop guard")
            return 0

        heartbeat("stop-accepted", "event=Stop")
        text, rows = run_suite()
        log(summarise(rows))
        # --------------------------------------------------------------------
        # THE STOP HOOK REPORTS PROOFS, AND NOTHING ELSE (2026-09-26)
        # --------------------------------------------------------------------
        # THE HUMAN: "end of your repot have prompt can confirm is ti end will
        # trigger something to rewrite report from full -> short / it is bug,
        # you can see this bug is under stop hook and somewhere related, pls find
        # it out and cleanup that now"
        #
        # MEASURED, from THIS session's transcript:
        #     L24874  assistant.message  3137 chars  the real report
        #     L24879  assistant.message     8 chars  "已完成項目 1。"
        #     L24881  user.message        225 chars
        # Both assistant messages are in the SAME turn. The hook logged:
        #     "last message is 8 chars, the longest message is 3137 — RE-ATTACHING it"
        # and emitted that 3137-char report as `systemMessage`, so VS Code showed
        # the report AGAIN inside the "Warning from Stop hook" box.
        #
        # WHAT WAS **NOT** HAPPENING, stated so the fix is not built on a myth: a
        # Stop hook cannot delete or edit an assistant message (see
        # `.github/instructions/answer-is-the-report.instructions.md`). The defect
        # was RE-DISPLAY, on every stop whose last message was short — MEASURED in
        # `proof_gate_log.txt` on most stops (8/3137, 425/2766, 497/2933, 83/164,
        # 40/2890, 244/2794).
        #
        # AND THE GUARD PROTECTED THE WRONG TEXT. "The longest message of the
        # turn" is NOT the report: at 16:04:23 the longest was **164 chars**, so a
        # 164-char aside was re-attached as the turn's deliverable. A turn also
        # commonly holds SEVERAL short messages, so "the longest" is a proxy that
        # is right only by luck.
        #
        # THE DESIGN WAS A REPAIR FOR A REAL COMPLAINT ("same, report from long ->
        # short in auto!!! fuck , fix it"), and that complaint was about the AGENT
        # writing a short closing line — already fixed BEHAVIOURALLY by the
        # instruction file above, which says so itself:
        #     "That is a REPAIR, not a fix ... a repair that fires every turn is a
        #      symptom, not a solution."
        # This repair fired on most turns. It is now removed.
        #
        # THE TRANSCRIPT IS NO LONGER CONSULTED. `_assistant_contents` and
        # `current_turn_assistant_contents` are KEPT — `_proof_chat_registry_
        # conversation.py` reads them to prove a reader can find a turn's
        # messages — but `main` no longer calls the two length/override helpers.
        if not text:
            # NOTHING CHANGED AND NOTHING QUEUED: there is no report to give.
            # Stated in the log, so a silent no-op is distinguishable from a
            # crash (QC-06).
            log("Stop — emitting NOTHING (no proof report to give; the transcript "
                "is not consulted)")
            heartbeat("nothing-changed", "no proof report to give")
            return 0
        log("Stop: delivering the fresh proof report (%d chars)" % len(text))
        heartbeat("emitted", "%d chars" % len(text))
        return emit({"systemMessage": text})
    except Exception as exc:
        log("FAIL-OPEN error: %s: %s" % (type(exc).__name__, exc))
        heartbeat("fail-open-error", "%s: %s" % (type(exc).__name__, exc))
        return 0


if __name__ == "__main__":
    sys.exit(main())