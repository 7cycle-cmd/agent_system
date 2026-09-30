#!/usr/bin/env python
"""Mode rights + plan-artifact redirect gate.

WHY THIS EXISTS
---------------
A worker arriving in ASK or PLAN mode does not know what it is allowed to do.
The old answer was a list of prohibitions ("do not edit", "do not explore"),
which reads as a wall. A worker that does not know its RIGHTS either freezes
or guesses, and a guess looks identical to a decision.

So this file does two jobs, and neither of them is punishment:

  1. SessionStart  -> print the RIGHTS CARD for the current mode.
                      The worker learns its rights at the moment it starts,
                      not by tripping over a rule later.

  2. PreToolUse    -> when a write is attempted without the artifact that
                      unlocks it, do not just refuse. REDIRECT: say exactly
                      which file to write and what must be in it.

The rule this encodes (user spec 2026-09-21):

    ASK  = research + answer.        Read anything. Prove your thinking.
    PLAN = research + draw the plan. Read anything. Write plan.md.
    AGENT= execute the plan.         plan.md is the unlock for writing code.

    "even you are not allow to have coding writing out of plan.md,
     when they do that, not block only, it ask them to have plan writing
     at plan.md"

So the default decision is `ask` (a prompt the human can approve through),
never a silent hard `deny`. A hard deny with no way forward is what wedged
the agent on 2026-09-18; the lesson is recorded in
`docs/plan_mo_no_explore_guard.md` (self-lockout incident).

SAFETY VALVES (a broken gate must never wedge the agent)
  * env `PLAN_GATE=off`            -> hook does nothing (emergency release).
  * env `MODE_ATTEST=off`         -> attestation returns a FAULT instead of
                                     reading the state DB (emergency release).
  * any exception / malformed stdin -> fail OPEN (allow), logged.
  * no resolvable task id           -> fail OPEN (allow), logged. Never guess
    an id: `qc_contract` learned that a guessed key gates on the wrong value.

HARD GATE (user ruling 2026-09-22)
----------------------------------
The decision is `deny`, never `ask`. The `PLAN_GATE_MODE` knob is DELETED — deny
is the one behaviour, not something that can be tuned into existence. A FAULT
(unprovable mode) is reported as a FAULT with its fix, never as "you are in the
wrong mode": conflating those two is what produced the 2026-09-22 incident.

WHAT THIS GATE IS NOT (measured 2026-09-22 — read before "improving" it)
-----------------------------------------------------------------------
`docs/plan_mo_no_explore_guard.md:190-210` records that `ask_mode_guard` was FIRST
built to deny edit/terminal in ASK mode, and it caused a TOTAL SELF-LOCKOUT on
2026-09-18: the agent could not even fix the hook itself. The scope was then
narrowed to the ask-questions tool, with this doctrine:

    "a hook may only block something narrow, unambiguous, and non-collateral...
     Broad tool classes (edit / terminal) belong to VS Code's permission system
     — do not build a worse duplicate."

This gate DOES look at broad tool classes, off an external state file. It is
therefore NOT an enforcement gate and must not pretend to be one. Its job is to
REDIRECT — the same rule `.github/copilot-instructions.md:150` states: a write
without the unlock gets `permissionDecision: ask`, "never a silent hard deny".

SUPERSEDED 2026-09-22 (user ruling R2: "hard gate only, it is flow not dynamic
toy"). The paragraph above is kept because it records WHY the old design chose
`ask`; the ruling overrides it. The decision is now `deny`, and the mode is
PROVEN at STEP 0 by `mode_attest.attest()` rather than read from a proxy file.
The self-lockout risk that justified `ask` is answered by the FAULT outcome: an
unprovable mode is reported as a fault with its fix, not as a confident deny of
the wrong mode.

So the defect that matters here is NOT "it does not enforce". It is:

    it could report an UNLOCK that did not happen, by finding a STALE plan.

Measured before this change: `plan_artifact()` returned the newest `plan_*.md` of
ANY task, and the AGENT branch only asked whether one EXISTED. `qc_evidence/`
holds several plans from earlier work, so a worker that never planned was forgiven
by any of them — the exact failure the human named: "worker forget to apply
plan.md and switch to agent mode". Nothing checked APPROVAL either, although the
rights card states "a plan being finished is not the same as a plan being
approved".

WHAT WAS FIXED, AND WHAT IS STILL OPEN
  * FIXED: an APPROVED plan is now REQUIRED to unlock, and every unlock is LOGGED
    with the plan filename, so a stale pass is visible after the fact.
  * CORRECTED 2026-09-23 — the unlock IS NOW session-scoped. The old claim
    below ("the `PreToolUse` payload carries no task id") was FALSE, and was
    measured to be false: the real `PreToolUse` AND `UserPromptSubmit` payloads
    BOTH carry `session_id` (`mode_attest_capture.jsonl:2-3`). A write now
    unlocks ONLY on an APPROVED plan whose `**Session:**` line names THIS
    session. A plan with no session line unlocks NOTHING — there is deliberately
    no "accept any plan" fallback.
  * The unlock is still LOGGED with the plan filename AND the session id. The
    log remains the ONLY way a human can tell after the fact which plan forgave
    a write; scoping narrows WHICH plan can, the log is how that stays auditable.
  * `ask` is not a block: the decision is `deny` (user ruling R2, "hard gate
    only"). This paragraph is the record of the superseded design, not live
    behaviour.


Contract (VS Code hooks):
  stdin  : JSON with `hook_event_name`, `tool_name`, `tool_input`, ...
  stdout : JSON with `hookSpecificOutput.permissionDecision` = allow|ask|deny
           and/or `systemMessage`
  exit   : 0

Manual dry-run:
  echo '{"hook_event_name":"SessionStart"}' | python scripts/plan_gate.py
  echo '{"hook_event_name":"PreToolUse","tool_name":"create_file"}' | python scripts/plan_gate.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mode_attest  # noqa: E402  (the single attestation SSOT)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODE_STATE = os.path.join(BASE_DIR, "chat_mode.json")
# WHERE the plan artifacts live. Overridable so a PROOF can point the gate at a
# temp dir instead of the live one.
#
# WHY THE OVERRIDE EXISTS (measured 2026-09-24): `_proof_plan_gate.py` and
# `_proof_plan_gate_deadlock.py` built their fixtures INSIDE this directory
# (`plan_zz_proof_gate.md`, `plan_zz_deadlock_proof.md`) and DELETED every live
# `plan_*.md` for the duration of a case, restoring them only in `__exit__`. Two
# defects followed:
#   1. POLLUTION — a fixture left behind is a plan the REAL gate can select. It
#      happened: the gate denied a write naming `plan_zz_proof_gate.md`.
#   2. DESTRUCTION — a crash / SIGKILL / timeout between `__enter__` and
#      `__exit__` leaves the repo with ZERO plans, so every worker loses its
#      unlock. A test must not be able to destroy production state.
# The default is UNCHANGED (`qc_evidence`), so nothing behaves differently when
# the variable is unset. Same shape as the existing `PLAN_GATE=off` valve.
EVIDENCE_DIR = (os.environ.get("PLAN_GATE_EVIDENCE_DIR", "").strip()
                or os.path.join(BASE_DIR, "qc_evidence"))
LOG_PATH = os.path.join(BASE_DIR, "plan_gate_log.txt")


def _mode_help(mode: str) -> str:
    """THE HELP for a mode, read from `mode_right_registry`. '' on any problem.

    WHY THIS IS A DB READ (user, 2026-09-23): "the problem is how to give help
    before complain not block the activity only". A deny that only refuses is a
    wall. Every reason below appends this, so a refusal NAMES the next act.

    It is deliberately BEST-EFFORT: a gate whose import chain can raise would
    fail closed on a missing DB, and a gate that cannot speak is worse than one
    that speaks without its help line. An EMPTY return is a signal the caller
    reports, not a silent default.
    """
    try:
        import sqlite3

        import mode_registry as mr

        conn = sqlite3.connect(os.path.join(BASE_DIR, "agent.db"), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            return mr.help_for(conn, mode)
        finally:
            conn.close()
    except Exception:
        return ""


def _append_help(mode: str, reason: str) -> str:
    """Append the mode's HELP to a deny reason, or state LOUDLY that it is absent.

    A deny with no help is a DEFECT (the user's own rule). So a MISSING help row
    is printed AS a defect rather than silently omitted — a gate that quietly
    loses its help line is how the "wall" behaviour would come back.
    """
    help_text = _mode_help(mode)
    if help_text:
        return "%s\n\nHOW TO PROCEED (%s):\n  %s" % (reason, mode, help_text)
    return ("%s\n\nHOW TO PROCEED: NOT REGISTERED for mode %r — this is a DEFECT "
            "(seed it with `python mode_registry.py --seed`). A deny with no way "
            "forward is worse than no deny." % (reason, mode))


# Tools that WRITE. Reading and searching are never gated: research is the
# job in ASK and PLAN, not a detour to be rationed.
#
# RULE (added 2026-09-23): a marker appears in AT MOST ONE of the two lists.
# MEASURED, and the reason this rule exists: `mcp_pylance` used to sit in
# READ_TOOL_MARKERS — DECLARED read-only — while `pylanceRunCodeSnippet`
# executes arbitrary Python. It was therefore routed through the read path and
# never gated, so it could write files while the gate was in FAULT. That is how
# `scripts/mode_attest.py` was repaired during the 2026-09-23 self-lockout: an
# escape hatch AND an ungoverned write path.
#
# A code-EXECUTION tool is a WRITE tool, whatever else it can do.
WRITE_TOOL_MARKERS = (
    "replace_string_in_file",
    "create_file",
    "multi_replace_string_in_file",
    "edit_notebook_file",
    "run_in_terminal",
    "create_and_run_task",
    "run_notebook_cell",
    "install_python_packages",
    "install_extension",
    "run_vscode_command",
    # The pylance CODE-EXECUTION tools. Naming the SPECIFIC tools, not the whole
    # `mcp_pylance` prefix: `pylanceLSP` / `pylanceDocuments` / `pylanceSettings`
    # are genuine reads, and gating them would ration the research ASK and PLAN
    # are supposed to be free to do.
    "pylanceruncodesnippet",
    "pylancepythonprofiling",
    "pylancepythondebug",
    "pylanceinvokerefactoring",
)

# Tools that only READ. Listed so the intent is explicit and auditable.
#
# `mcp_pylance` is deliberately NOT here: the prefix covers both readers and the
# code-execution tools above, and a prefix that means "read" while containing an
# executor is the defect this list recorded. The genuine pylance readers are
# listed by their own names.
READ_TOOL_MARKERS = (
    "read_file",
    "grep_search",
    "file_search",
    "list_dir",
    "view_image",
    "fetch_webpage",
    "get_errors",
    "copilot_getnotebooksummary",
    "read_notebook_cell_output",
    "vscode_listcodeusages",
    "pylancelsp",
    "pylancedocuments",
    "pylancesettings",
    "pylanceworkspaceroots",
    "pylanceimports",
    "pylanceinstalledtoplevelmodules",
    "session_store_sql",
    "memory",
)

# RULE 1, ENFORCED. A marker in BOTH lists would make one tool read and write
# depending on evaluation order, and `is_write_tool` would answer differently
# from one call to the next. Failing at IMPORT means a bad edit cannot ship.
_DUPLICATE_MARKERS = sorted(set(WRITE_TOOL_MARKERS) & set(READ_TOOL_MARKERS))
if _DUPLICATE_MARKERS:
    raise AssertionError(
        "a tool marker is in BOTH WRITE_TOOL_MARKERS and READ_TOOL_MARKERS: %s. "
        "A tool is either gated or not; it cannot be both."
        % ", ".join(_DUPLICATE_MARKERS))


def log(msg: str) -> None:
    """Append one timestamped line; never raise."""
    try:
        from datetime import datetime

        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass


# --------------------------------------------------------------------------
# Phase 0 diagnostic: capture the REAL hook payload.
# --------------------------------------------------------------------------
# WHY: the whole design turns on one question — does the real VS Code hook
# payload carry the chat mode? Every existing proof case feeds a SYNTHETIC
# payload (`_proof_plan_gate.py:102-160`), so the question has never been
# measured. Guessing the answer would repeat the exact defect this task exists
# to fix: asserting something that was never verified.
#
# This is env-gated and OFF by default, so it costs nothing in normal use:
#   set MODE_ATTEST_CAPTURE=C:\projects\agent_system\mode_attest_capture.jsonl
# It appends one JSON line per hook call and NEVER changes the decision.
CAPTURE_ENV = "MODE_ATTEST_CAPTURE"


def capture_payload(raw: str, payload: dict) -> None:
    """Append the raw + parsed payload to the capture file. Never raises.

    Records the raw text (so a parse failure is still visible) and the parsed
    object (so field names can be grepped). Also records the top-level KEYS,
    which is what answers "is there a mode field?" at a glance.
    """
    path = os.environ.get(CAPTURE_ENV, "").strip()
    if not path:
        return
    try:
        from datetime import datetime

        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "raw_len": len(raw or ""),
            "keys": sorted(payload.keys()) if isinstance(payload, dict) else [],
            "payload": payload,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# APPROVAL + STALENESS (added 2026-09-22)
# --------------------------------------------------------------------------

# A plan unlocks only when its status line says so. MACHINE-READABLE, because the
# rights card already stated this rule in PROSE ("a plan being finished is not the
# same as a plan being approved") and nothing enforced it — a rule stated in prose
# and unchecked at the write site is the defect family this repo has recorded
# three times.
APPROVED_RE = re.compile(r"^\s*\*\*Status:\*\*\s*APPROVED\s*$",
                         re.IGNORECASE | re.MULTILINE)

# Doctrine (`docs/plan_mo_no_explore_guard.md`): "Any hook that depends on an
# external state file MUST have a staleness check and an escape hatch."
#
# REMOVED 2026-09-22 (user ruling R1: no default, no fallback). This gate no
# longer reads `chat_mode.json` at all — that file is the hotkey's INTENT record
# and was measured wrong (`chat_mode.json:2` said ASK while the user was in
# AGENT). The mode now comes from `mode_attest.attest()`, ONE authoritative
# source. The staleness valve and `current_mode()` went with it: a valve that
# makes a gate fall back to "no opinion" is a fallback, and R1 forbids one.
APPROVED_RE = re.compile(r"^\s*\*\*Status:\*\*\s*APPROVED\s*$",
                         re.IGNORECASE | re.MULTILINE)

# The SESSION line a plan must carry to unlock THIS session (added 2026-09-23).
# WHY: the unlock was NOT task-scoped — `plan_artifact(approved_only=True)`
# returned the newest APPROVED plan of ANY task, so a worker that never planned
# for THIS task was forgiven by a stale plan from another one. MEASURED this
# session: writes for the `*_register` task were unlocked by
# `plan_GATE.TOOLCLASS.md`, a DIFFERENT task's plan (`plan_gate_log.txt`).
#
# The old docstring claimed task-scoping was IMPOSSIBLE ("the PreToolUse payload
# carries no task id"). That was FALSE: the real payload carries `session_id` and
# `transcript_path` (`mode_attest_capture.jsonl:2-3`). So the unlock CAN be scoped,
# and now is.
#
# MORE THAN ONE SESSION (fixed 2026-09-25). THE HUMAN: "more than 1 session, you
# will have this fucking forloop happen often, pls fix that now too".
#
# MEASURED DEFECT: the line held ONE id, and a TASK spans chats. So two chats on
# the same task PING-PONGED the single line — each re-scope locked the other out.
# `plan_gate_log.txt` 2026-09-25: chat 50f58738 UNLOCKs `plan_FILL.REGISTERS...`
# at 10:58:17 while chat f5acaec6 is DENIED at 10:58:10 and 10:59:15, and the
# deny's own advice ("change its `**Session:**` line to this session's id") is
# what locks the OTHER chat out. A gate that instructs an action which breaks
# another worker is the deadlock family, not a policy.
#
# THE FIX: a plan may name SEVERAL sessions. `findall` reads EVERY `**Session:**`
# line, and each line is split on commas/whitespace, so a second chat ADDS its id
# instead of REPLACING the first. Scoping is NOT removed — a plan still unlocks
# only the sessions it names, and a plan with no line still unlocks nothing.
SESSION_RE = re.compile(r"^\s*\*\*Session:\*\*\s*(.+?)\s*$", re.MULTILINE)
SESSION_SPLIT_RE = re.compile(r"[,\s]+")


# --------------------------------------------------------------------------
# WHAT A COMMAND DOES, not what the tool is CALLED (F21, fixed 2026-09-23)
# --------------------------------------------------------------------------
# MEASURED DEFECT: `is_write_tool()` classified by TOOL NAME, so
# `run_in_terminal` was a write no matter WHAT it ran. The gate therefore denied
# `python scripts/mode_attest.py --step0` — a PURE READ that
# `.github/copilot-instructions.md` REQUIRES at the start of every turn. A gate
# that contradicts the instruction file is a defect, not a policy.
#
# The payload DOES carry the command (`mode_attest_capture.jsonl:90`:
# `"tool_name": "run_in_terminal", "tool_input": {"command": "..."}`), so the
# gate CAN tell. It now does.
#
# THE SAFE DIRECTION: a command is a READ only when it matches an EXPLICIT read
# pattern. Everything else — including anything unrecognised — is a WRITE. An
# allowlist that fails closed, never a blocklist that fails open.
READ_COMMAND_PATTERNS = (
    # STEP 0. The instruction file requires it every turn, and it only reads
    # VS Code's own state DB.
    r"mode_attest\.py\s+--step0\b",
    r"mode_attest\.py\s+--json\b",
    # THE ENTITY LOOKUP, and it is here because the gate's OWN HELP names it.
    #
    # MEASURED DEADLOCK (2026-09-23): the entity deny says
    #     "Find the id for a file with: python entity_backfill.py --measure"
    # and THAT COMMAND WAS ITSELF DENIED. A gate that instructs an action it
    # forbids is a deadlock, not a policy — the same defect family as F25 (the
    # deny told the worker to add a `**Session:**` line, and that write was
    # denied), in a new place.
    #
    # `--measure` and `--covered-by` are PURE READS: `measure()` runs five
    # `SELECT COUNT(*)` (`entity_backfill.py:327`) and `covered_by()` runs one
    # SELECT (`:279`). Neither writes anything, so neither needs an entity.
    r"entity_backfill\.py\s+--measure\b",
    # `--covered-by` REMOVED: the flag DOES NOT EXIST (measured).
    # A `python -c` that ONLY calls `covered_by(`. NARROW ON PURPOSE: `python -c`
    # can write files, so it is a WRITE by default (`WRITE_COMMAND_MARKERS`), and
    # only this one read-only query is carved out.
    r"python\s+-c\s+[\"'].*covered_by\(",
    # The repo's own read-only report flags.
    r"--measure\b", r"--audit\b", r"--list\b", r"--report\b",
    r"--discover\b", r"--dry-run\b",
    # git, read subcommands only.
    r"^\s*git\s+(status|diff|log|show|branch|remote|rev-parse)\b",
    # PowerShell / cmd readers.
    r"^\s*(Get-ChildItem|Get-Content|Select-String|Test-Path|Get-Item|"
    r"Get-Command|Measure-Object|Where-Object|Select-Object|Sort-Object|"
    r"Format-Table|Format-List|Out-String)\b",
    r"^\s*(dir|ls|cat|type|findstr|where)\b",
)

# A command that WRITES, whatever else it also matches. Checked FIRST, so a read
# pattern cannot smuggle a write through (e.g. `--report > out.txt`).
WRITE_COMMAND_MARKERS = (
    "--apply", ">", "Set-Content", "Add-Content", "Out-File",
    "Remove-Item", "New-Item", "Move-Item", "Copy-Item", "Rename-Item",
    "git add", "git commit", "git checkout", "git reset", "git clean",
    "pip install", "python -c", "python -m", "Start-Process", "Invoke-",
)

# THE ONE `python -c` THAT IS A READ, and it must be checked BEFORE the write
# markers above.
#
# MEASURED (2026-09-23): `python -c` is in `WRITE_COMMAND_MARKERS`, and that list
# is checked FIRST, so a `python -c` that only CALLS `covered_by(` was still a
# WRITE. The entity deny's own help names exactly that one-liner, so the gate
# refused the command it told the worker to run — the deadlock again, one layer
# down.
#
# NARROW ON PURPOSE: only a `covered_by(` call is carved out. `python -c` can
# write files, so everything else stays a WRITE.
READ_PYTHON_C_PATTERN = re.compile(r"python\s+-c\s+[\"'].*covered_by\(", re.I)


def is_read_command(command: str) -> bool:
    """True only when the command matches an EXPLICIT read pattern.

    Fails CLOSED: an unrecognised command is NOT a read. The alternative (treat
    unknown as read) is how a write slips through a gate that was supposed to
    stop it.
    """
    cmd = str(command or "").strip()
    if not cmd:
        return False
    # THE NARROW EXCEPTION, checked BEFORE the write markers. A `python -c` that
    # only calls `covered_by(` reads; every other `python -c` writes.
    if READ_PYTHON_C_PATTERN.search(cmd):
        return True
    low = cmd.lower()
    for m in WRITE_COMMAND_MARKERS:
        if m.lower() in low:
            return False
    return any(re.search(p, cmd, re.IGNORECASE) for p in READ_COMMAND_PATTERNS)


# --------------------------------------------------------------------------
# THE ENTITY-ID WRITE GATE (LINE 2, added 2026-09-23)
# --------------------------------------------------------------------------
# The user's words: "i know, problem is write need to have entity id" /
# "so all under the rule".
#
# THE RULE: a write must carry a VERIFIABLE entity id, and that id's
# `code_location_registry` rows must cover the file being written.
#
# WHY `code_location_registry` AND NOT A NEW TABLE: it already maps
# `(entity_type, entity_ref_id, version)` -> `file_path` + `line_start`
# (`db_schema.py:1517`), and `entity_backfill.py` fills it. A second mapping
# would be a second truth.
ENTITIES_RE = re.compile(r"^\s*\*\*Entities:\*\*\s*(.+)$", re.MULTILINE)
# THE ENTITY ID IS 4-PART: `{LETTER}-{table_id}-{row_id}-{version}`.
# (The form `{LETTER}-{ref_id}-{row}-{version}` written here before was itself
# the mis-statement: `ref_id` is not a part. table_id is looked up from the
# letter; row_id is the register table's own pk. See `entity_id.SHAPE`.)
#
# THE HUMAN (2026-09-27), verbatim:
#   "A"
#   "letter - table_id - row_id - version_id"
#   "3 is old, new version for entity is 4 part"
#   "version is the key to create mis-understand"
#
# The 3-part form is the OLD one. It was briefly made the only shape earlier the
# same day (by `ENTITY.ROW.REGISTRY.REMOVE`) and that was WRONG: MEASURED, the
# plans in `qc_evidence/` carry **905 four-part ids in 313 files** against 79
# three-part ids in 15 files. A gate that reads the 3-part form cannot read its
# own corpus.
#
# WHY THE ROW PART MATTERS: with ONE trailing number, `T-1-5` is AMBIGUOUS -- it
# reads as *version 5* OR *row 5*. The 4-part form NAMES both, which is exactly
# what removes the mis-understanding the human named.
#
# ANCHORED with `(?!-\d)`: a 5-part id is REFUSED rather than silently re-read
# as a different entity. The un-anchored form TRUNCATED a 4-part id:
#
#     findall("R-189-147-1")  ->  ['R-189-147']      # WRONG ENTITY
#
# and the truncated id names a DIFFERENT entity -- the plan MEANT
# `R-189-147-1` (letter R, ref_id 189, ROW 147, version 1) but the gate READ
# `R-189-147` (letter R, ref_id 189, VERSION 147). MEASURED over the plans in
# `qc_evidence/`: 552 truncated occurrences, 0 of the 552 full ids verify, and
# **9 of the truncated ids ACCIDENTALLY VERIFY** -- so the gate could PASS on an
# entity the plan never named. A wrong pass is invisible; a denial is visible.
ENTITY_ID_RE = re.compile(r"\b([A-Za-z]{1,3}-\d+-\d+-\d+)(?!-\d)\b")
# THE OLD 3-PART FORM, detected ONLY to REPORT it. `{LETTER}-{ref}-{version}` is
# ambiguous (is the last number a row or a version?), which is exactly why the
# 4-part form replaced it. This pattern must NEVER be used to READ an entity; its
# single job is to let the gate say "this plan declares ids I cannot read" instead
# of silently treating a compliant-LOOKING plan as one that declares nothing.
LEGACY_ENTITY_ID_RE = re.compile(
    r"\b([A-Za-z]{1,3}-\d+-\d+)(?!-\d)\b")
# THE PLAN ARTIFACT is `plan_<task_id>.md` AND its machine-readable twin
# `plan_<task_id>.json`. MEASURED 2026-09-28: the `.json` twin was REJECTED as a
# NEW file ("the plan's allowlist does not name it") because only `.md` matched —
# so a DRAFT plan could write its own .md and never its own .json twin. The twin
# is the SAME artifact (same task id, same unlock path, written in the same act),
# so it must be matched here. `\w` already covers `.` inside the stem.
PLAN_ARTIFACT_RE = re.compile(r"^plan_[\w.\-]+\.(?:md|json)$")


def is_write_tool(tool_name: str, tool_input: dict | None = None) -> bool:
    """Does this tool call WRITE? For a terminal, the COMMAND decides (F21).

    `tool_input` is optional so an existing caller that only has a name still
    works — but a `run_in_terminal` with no `tool_input` is treated as a WRITE,
    because the command that would prove otherwise is absent.
    """
    name = (tool_name or "").strip().lower()
    if not name:
        return False
    if not any(m in name for m in WRITE_TOOL_MARKERS):
        return False
    # A terminal is a write ONLY when its command is not a read. This is the
    # F21 fix: the tool NAME is not the evidence, the COMMAND is.
    if "run_in_terminal" in name:
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = str(tool_input.get("command") or "")
        return not is_read_command(cmd)
    return True


def target_path(tool_input: dict | None) -> str:
    """The file a write tool targets, from the payload. '' when it names none."""
    if not isinstance(tool_input, dict):
        return ""
    for k in ("filePath", "file_path", "path", "target_file",
              "notebookPath", "notebook_path"):
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def target_paths(tool_input: dict | None) -> list[str]:
    """EVERY file a write tool targets, from the payload. `[]` when it names none.

    WHY THIS EXISTS (MEASURED 2026-09-29, and it was a HOLE IN THIS GATE)
    ---------------------------------------------------------------------
    `target_path()` reads a SINGLE path from `filePath` / `path` / ... That is the
    shape of `create_file` and `replace_string_in_file`. A
    **`multi_replace_string_in_file`** payload does NOT carry `filePath`: it
    carries `replacements`, a LIST of `{filePath, oldString, newString}`. So
    `target_path()` returned `''`, the tool was reclassified as a FILE-LESS write,
    and the F26 branch below unlocked it as a "command write".

    MEASURED, from this gate's own log (`plan_gate_log.txt`):

        UNLOCK plan=plan_RING5.DRIFT.md session=- \\
            tool=multi_replace_string_in_file entities=1 \\
            (no file target: command write)

    ...while the IDENTICAL single-file write was DENIED for naming a file the plan
    did not cover. So a MULTI-FILE write was a way AROUND the entity/allowlist
    check, which is the opposite of what a gate is for: the more files a write
    touches, the MORE paths need checking, not fewer.

    THE FIX IS ADDITIVE, NOT A LOOSENING. Every path a write names is now checked
    by the SAME rule, and ONE failing path denies the whole write (fail CLOSED) —
    a multi-file write is one act, so it cannot be half-authorized. A payload with
    no recognizable path still yields `[]`, so the file-less branch is unchanged
    for the tools that legitimately have no file (a terminal command).
    """
    if not isinstance(tool_input, dict):
        return []
    out: list[str] = []
    one = target_path(tool_input)
    if one:
        out.append(one)

    def _collect(node, depth=0):
        # Bounded: a payload is at most a few levels deep, and an unbounded walk
        # on a hostile payload is a denial-of-service on the hook's own timeout.
        if depth > 3:
            return
        if isinstance(node, dict):
            v = target_path(node)
            if v:
                out.append(v)
            for key in ("replacements", "files", "edits"):
                if key in node:
                    _collect(node[key], depth + 1)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _collect(item, depth + 1)

    _collect(tool_input)
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


# THE LOG ARTIFACT is `qc_evidence/agent_log_<task_id>.md` and its `.json` twin.
# These are the protocol's OWN DELIVERABLE (the plan's §5 writes them, and the
# agent-log template requires both).
#
# WHY THEY NEED A STRUCTURAL EXEMPTION AND NOT AN ALLOWLIST — MEASURED 2026-09-29,
# and it CLOSED THE ALLOWLIST REMOVAL. `qc_evidence/` is in
# `hardcode_scan.SKIP_DIRS`, so NO entity can EVER cover a file there. Deleting the
# allowlist therefore denied the agent log itself: the gate refused its own
# deliverable, and the refusal even said "NOT REGISTERED for mode 'agent'".
#
# THE DISTINCTION, and it is the whole point of this fix: the plan artifact and the
# log artifact are the ARTEFACTS THE GATE ITSELF NAMES as the deliverable of a task.
# They are named by a STRUCTURAL PATTERN in this file, so they do not depend on ANY
# plan's contents — they are the same for every task and every session. An
# allowlist entry is the opposite: a per-plan list the agent writes for itself, which
# is why it could be used to authorise ANY path. A pattern is bounded by the FILE
# NAME convention; a list is bounded by nothing.
AGENT_LOG_ARTIFACT_RE = re.compile(r"^agent_log_[\w.\-]+\.(?:md|json)$")

# THE THIRD PROTOCOL ARTEFACT: `qc_evidence/qc_report_<task_id>.md` (+ `.json`).
# MEASURED 2026-09-29, while classifying the `qc_evidence/` remainder: the QC
# report is a DELIVERABLE the protocol names (the plan's §5 and the QC-report
# template require it), and `qc_evidence` is in `hardcode_scan.SKIP_DIRS`, so NO
# entity can ever cover it — exactly the situation that denied the agent log.
# MEASURED: a `qc_report_*.md` write is DENIED today, so a worker following the
# protocol cannot produce its own QC report without an allowlist exception.
#
# SAME RULE, SAME BOUND: a file-NAME pattern written HERE, identical for every
# task and every session, anchored inside `EVIDENCE_DIR` and limited to
# `.md`/`.json`. A pattern is bounded by a naming convention; the allowlist that
# F28 deleted was bounded by nothing.
QC_REPORT_ARTIFACT_RE = re.compile(r"^qc_report_[\w.\-]+\.(?:md|json)$")


def is_qc_report_artifact(path: str) -> bool:
    """Is this path a QC-report deliverable (`qc_evidence/qc_report_<task>.…`)?

    The THIRD and last structural exemption. It is listed here rather than grepped
    for, so the SET of exemptions is readable in one place: the plan, the agent
    log, and the QC report — the three artefacts this repo's protocol requires.
    """
    if not path:
        return False
    try:
        p = os.path.abspath(path)
        ev = os.path.abspath(EVIDENCE_DIR)
        if os.path.normcase(os.path.commonpath([p, ev])) != os.path.normcase(ev):
            return False
        return bool(QC_REPORT_ARTIFACT_RE.match(os.path.basename(p)))
    except Exception:
        return False


def is_plan_artifact(path: str) -> bool:
    """Is this path THE plan artifact (`qc_evidence/plan_<task_id>.md`)?

    THE UNLOCK PATH. A gate that blocks its own unlock is a deadlock — measured
    FOUR times (`plan_gate_log.txt`), where the deny message instructed the
    worker to add a `**Session:**` line to its plan and THAT WRITE WAS ITSELF
    DENIED.

    It is a PATH rule, not an entity rule: `hardcode_scan.SKIP_DIRS` includes
    `qc_evidence` (`hardcode_scan.py:60-68`), so no entity can ever cover a plan
    file. Requiring coverage here would make the unlock permanently impossible.
    """
    if not path:
        return False
    try:
        p = os.path.abspath(path)
        ev = os.path.abspath(EVIDENCE_DIR)
        if os.path.normcase(os.path.commonpath([p, ev])) != os.path.normcase(ev):
            return False
        return bool(PLAN_ARTIFACT_RE.match(os.path.basename(p)))
    except Exception:
        return False


def is_log_artifact(path: str) -> bool:
    """Is this path THE agent-log artifact (`qc_evidence/agent_log_<task_id>.…`)?

    WHY THIS EXISTS (MEASURED 2026-09-29, and it COMPLETED the allowlist removal).
    `qc_evidence/` is in `hardcode_scan.SKIP_DIRS`, so NO entity can EVER cover a
    file there. When the allowlist exception was deleted, the gate then REFUSED the
    agent log — its OWN deliverable, which the plan's §5 requires as
    `agent_log_<task_id>.md` + `.json`.

    SO THERE ARE EXACTLY TWO STRUCTURAL EXEMPTIONS, and both are the artefacts the
    gate's own protocol names as a task's deliverables: the PLAN it unlocks on, and
    the LOG it produces. Both are recognised by a FILE-NAME PATTERN written HERE, in
    the gate's source — the same for every task, every session, every plan.

    THAT IS THE DISTINCTION FROM THE ALLOWLIST, and it is why one is acceptable and
    the other was the bug: a pattern is BOUNDED by the name convention and cannot be
    extended by a worker; an allowlist was a per-plan list the worker writes for
    itself, so it could authorise ANY path (measured: 1516 files).

    It does NOT open a hole for anything else: the path must be INSIDE
    `EVIDENCE_DIR` AND match `agent_log_<task_id>.md|json`, so a source file named
    `agent_log_x.py` elsewhere is NOT covered, and a log file cannot be used to write
    code.
    """
    if not path:
        return False
    try:
        p = os.path.abspath(path)
        ev = os.path.abspath(EVIDENCE_DIR)
        if os.path.normcase(os.path.commonpath([p, ev])) != os.path.normcase(ev):
            return False
        return bool(AGENT_LOG_ARTIFACT_RE.match(os.path.basename(p)))
    except Exception:
        return False


def plan_entities(path: str) -> list[str]:
    """The entity ids a plan declares on its `**Entities:**` line.

    A LIST, deliberately: this session alone changed 8 files, so one entity
    cannot cover a plan. An EMPTY list means the plan declares NO entity, and a
    write is then denied — the safe direction.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(8000)
    except Exception:
        return []
    m = ENTITIES_RE.search(text)
    if not m:
        return []
    ids = ENTITY_ID_RE.findall(m.group(1))
    # A PLAN CAN DECLARE ENTITIES IN THE OLD 3-PART FORM AND HAVE THEM READ AS
    # NOTHING. MEASURED 2026-09-29: `plan_DECLARER.DRIFT.md` declared `R-67-1` and
    # `plan_FK.POLICY.STAGES.md` declared `R-220-1, R-67-1, R-290-1` — the 3-part
    # `{LETTER}-{ref}-{version}` form — so `ENTITY_ID_RE` (4-part, anchored) matched
    # NOTHING and the plan was treated as declaring NO entity. The plan LOOKED
    # compliant: it had the heading, the line, and the ids. That is the worst shape
    # of defect, because a reader auditing the plan sees a declaration while the
    # gate sees an empty list.
    #
    # So a 3-part id is DETECTED and REPORTED rather than silently dropped. The
    # gate still returns the VERIFIED 4-part ids (a 3-part id cannot be verified —
    # it is ambiguous between row and version), and the report goes to the log, so
    # the cause is visible in `plan_gate_log.txt` instead of only in a denial.
    legacy = LEGACY_ENTITY_ID_RE.findall(m.group(1))
    if legacy:
        log("LEGACY-ENTITY-ID plan=%s ids=%s (3-part: the gate reads the 4-part "
            "form, so these declare NOTHING — rewrite them)"
            % (os.path.basename(path), ",".join(sorted(set(legacy))[:6])))
    return ids


# The ALLOWLIST section heading. MEASURED over the 206 plans in `qc_evidence/`:
# the heading has SIX spellings — `## 4. ALLOWED FILE ALLOWLIST`,
# `## Allowed File Allowlist`, `## 6. Allowed File Allowlist`, `## 3. ALLOWED
# FILE ALLOWLIST`, `## 5. Allowed File Allowlist`, `## 4. Allowed File
# Allowlist` — so the matcher is case-insensitive and tolerates an optional
# `N.` prefix. A matcher that knew only one spelling would silently return `[]`
# for the other five, which is the "detector that cannot find anything" defect.
ALLOWLIST_HEAD_RE = re.compile(
    r"^#{2,}\s*(?:\d+\.\s*)?Allowed\s+File\s+Allowlist\s*$", re.I | re.M)

# Any heading, used to END the allowlist section.
ANY_HEAD_RE = re.compile(r"^#{1,6}\s", re.M)

# A table row whose FIRST cell is backticked.
ALLOWLIST_ROW_RE = re.compile(r"^\s*\|\s*`([^`]+)`\s*\|", re.M)


def plan_allowlist(path: str) -> list[str]:
    """The file paths a plan's ALLOWLIST table names. Used for a NEW file.

    A file that does not exist yet cannot be covered by an entity that does not
    exist yet, so the plan's own allowlist is the check available. It is read
    from the plan, not invented.

    SCOPED TO THE ALLOWLIST SECTION (2026-09-26). The previous version scanned
    the WHOLE file for `^\\s*\\|\\s*`([^`]+)`\\s*\\|`, so **EVERY** table row whose
    first cell was backticked became an allowlist entry. MEASURED over the 206
    plans in `qc_evidence/`: **88 of them** were polluted — a §9 OUTCOME row
    like `| `scripts/proof_gate.py` | PASS | ... |` silently AUTHORISED a write
    the plan never listed. That is a privilege escalation, not a formatting bug,
    and `qc_evidence/HANDOFF_2026_09_25.md:13` had already recorded it as "the
    backtick trap — hit 7 times".

    The fix is on the READER, not the writer: read ONLY the section under the
    ALLOWLIST heading, and stop at the next heading. No plan md is modified, so
    no APPROVED evidence is touched.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()   # read the WHOLE plan: a 20000-char cap
            # SILENTLY truncated the allowlist, so a detailed plan was denied
            # for being detailed (measured 2026-09-23: the allowlist started at
            # char 19866 of a 22608-char plan). A truncating read that fails
            # closed on a LENGTH is the same defect as a gate that forbids the
            # action its own help names.
    except Exception:
        return []
    head = ALLOWLIST_HEAD_RE.search(text)
    if not head:
        # No ALLOWLIST section: the plan names no files. An EMPTY list is the
        # safe direction — the gate then falls back to entity coverage.
        return []
    rest = text[head.end():]
    nxt = ANY_HEAD_RE.search(rest)
    section = rest[:nxt.start()] if nxt else rest
    return [m.group(1).strip() for m in ALLOWLIST_ROW_RE.finditer(section)]


def entity_covers(file_path: str, entities: list[str]) -> dict:
    """Does any DECLARED entity's location cover this file?

    DELEGATES to `entity_backfill.covered_by()` — ONE implementation of "which
    entity covers this file", so the gate and the backfill cannot disagree.
    """
    try:
        import sqlite3

        import entity_backfill as eb
        conn = sqlite3.connect(os.path.join(BASE_DIR, "agent.db"), timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            cov = eb.covered_by(conn, file_path)
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False,
                "reason": "coverage could not be measured: %s: %s"
                          % (type(exc).__name__, exc)}
    if not cov.get("ok"):
        return {"ok": False, "file": cov.get("file"),
                "reason": cov.get("reason") or "not covered"}
    declared = set(entities)
    hit = [e for e in cov["entities"] if e in declared]
    if not hit:
        return {"ok": False, "file": cov["file"],
                "reason": ("the file is covered by %s, but the plan declares %s"
                           % (cov["entities"], sorted(declared) or "nothing"))}
    return {"ok": True, "file": cov["file"], "entity": hit[0]}


def plan_status(path: str) -> str:
    """Read a plan's declared status: APPROVED / DRAFT / '' when unreadable.

    Reads the machine-readable `**Status:**` line. A plan with NO status line is
    treated as NOT approved — the safe direction, because the alternative lets an
    unmarked file unlock code writing.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(4000)
    except Exception:
        return ""
    if APPROVED_RE.search(text):
        return "APPROVED"
    if re.search(r"^\s*\*\*Status:\*\*\s*(.+)$", text, re.IGNORECASE | re.MULTILINE):
        return "NOT_APPROVED"
    return ""


def plan_session(path: str) -> set[str]:
    """The session ids a plan declares, as a SET (empty when it declares none).

    A plan with NO `**Session:**` line declares NO session, and therefore unlocks
    NOTHING. That is deliberate: the alternative ("no session line -> accept any
    plan") is exactly the defect this scoping removes.

    MORE THAN ONE ID (fixed 2026-09-25). THE HUMAN: "more than 1 session, you will
    have this fucking forloop happen often, pls fix that now too".

    MEASURED: a TASK spans chats, but the line held ONE id, so two chats on the
    same task PING-PONGED it — each re-scope locked the other out. A plan may now
    name SEVERAL sessions: every `**Session:**` line is read, and each line is
    split on commas/whitespace. A second chat ADDS its id instead of REPLACING
    the first. Scoping is NOT removed — only the sessions the plan NAMES unlock.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(4000)
    except Exception:
        return set()
    ids: set[str] = set()
    for raw in SESSION_RE.findall(text):
        for tok in SESSION_SPLIT_RE.split(raw.strip()):
            tok = tok.strip()
            if tok:
                ids.add(tok)
    return ids


def plan_artifact(*, approved_only: bool = False, session_id: str = "",
                  target: str = "") -> str:
    """Newest `qc_evidence/plan_*.md` that qualifies, or '' when none does.

    `approved_only=True` skips plans whose status is not APPROVED, so a DRAFT plan
    cannot unlock a write. It does NOT judge whether the plan is any good — that
    is QC's job, and a gate that also grades would be two gates in one.

    `session_id` (added 2026-09-23) scopes the unlock to the sessions a plan
    NAMES: a plan qualifies when THIS session is IN its `**Session:**` set. This
    is the fix for the measured defect where a DIFFERENT task's plan unlocked the
    write. When `session_id` is empty the caller is asking "is there any plan at
    all" (used by the STEP 0 report), and no session filter is applied — but the
    WRITE path always passes a real id, so an unscoped unlock cannot happen there.

    MORE THAN ONE SESSION (fixed 2026-09-25). THE HUMAN: "more than 1 session, you
    will have this fucking forloop happen often, pls fix that now too". A TASK
    spans chats, so a plan may name SEVERAL sessions; membership (not equality)
    is the test, so a second chat ADDS its id instead of REPLACING the first.

    `target` (added 2026-09-25) — THE PLAN THAT NAMES THE FILE WINS.
    THE HUMAN: "fix it all now", after a write for THIS task was judged by
    ANOTHER task's plan.

    MEASURED: a session that has done several tasks has several APPROVED plans,
    and the NEWEST one won. So `plan_artifact(approved_only=True, session_id=...)`
    returned `plan_ENV.TABLE.CONFIGURE.AND.LOGO.md` — a different task's plan —
    because its mtime was 2 seconds later than this task's plan:

        plan_ENV.TABLE.CONFIGURE.AND.LOGO.md   APPROVED   mtime 1790308094
        plan_FIX.THREE.GAPS.md                 APPROVED   mtime 1790308092

    The write was then denied with THAT plan's allowlist in the message, which is
    how the defect was found: the refusal named files this task never touched.

    THE RULE, restated: the unlock means "THIS plan authorises THIS file". Picking
    by mtime alone makes it "the newest plan of this session authorises anything",
    which is the same defect the session scoping fixed one level up. So when a
    `target` is given, a plan whose ALLOWLIST NAMES it is preferred over a newer
    plan that does not. mtime still breaks ties WITHIN each group, so the order
    stays deterministic.
    """
    try:
        best, best_key = "", None
        for fn in os.listdir(EVIDENCE_DIR):
            if not (fn.startswith("plan_") and fn.endswith(".md")):
                continue
            path = os.path.join(EVIDENCE_DIR, fn)
            if approved_only and plan_status(path) != "APPROVED":
                continue
            if session_id and session_id not in plan_session(path):
                continue
            # DOES THIS PLAN NAME THE TARGET? A plan that does is a BETTER
            # answer than a newer plan that does not, so it sorts first.
            names_target = 0
            if target:
                try:
                    base = os.path.basename(target)
                    for a in plan_allowlist(path):
                        if base == os.path.basename(a) or target.endswith(a):
                            names_target = 1
                            break
                except Exception:
                    names_target = 0
            # Tie-break by FILENAME, deliberately. mtime alone is not an order:
            # a bulk rewrite (a restore, a checkout, a migration) gives many
            # plans the SAME mtime, and a bare `mtime >` then picks whichever
            # `os.listdir` happens to yield first. Measured 2026-09-22 — the
            # proof's DRAFT case was unlocked by an unrelated APPROVED plan for
            # exactly that reason: the failure `approved_only` exists to prevent,
            # arriving through the tie-break instead of through the flag. A tie
            # must resolve identically on every run, so the highest filename wins.
            key = (names_target, os.path.getmtime(path), fn)
            if best_key is None or key > best_key:
                best, best_key = path, key
        return best
    except Exception:
        return ""


# --------------------------------------------------------------------------
# The rights card. This is the part a worker actually needs.
# --------------------------------------------------------------------------

RIGHTS = {
    "ASK": """\
MODE: ASK  —  research + answer. You are NOT blocked; you are scoped.

YOUR RIGHTS
  * Read, search, grep, measure ANYTHING you need. Research is the job here,
    not a detour. Prove your thinking with real evidence (path:line, command
    output) instead of asserting it.
  * Answer with text. That is the deliverable.
  * If a question is genuinely blocking, write it as PLAIN TEXT and stop.

NOT YOURS YET
  * Writing code, editing files, running commands. That belongs to PLAN.
  * Never treat "user is not available / work autonomously" as approval.

IF THIS NEEDS CODE
  Say so, and move to PLAN. In PLAN you research the same way, then write
  qc_evidence/plan_<task_id>.md — that file is what unlocks writing.
""",
    "PLAN": """\
MODE: PLAN  —  research + draw the plan. You are NOT blocked; you are scoped.

YOUR RIGHTS
  * Read, search, grep, measure ANYTHING you need. Research freely — this is
    the mode where proving your thinking is the whole point.
  * Write exactly ONE artifact: qc_evidence/plan_<task_id>.md (+ .json twin).
    That is your output, and it is allowed.

THE PLAN MUST CARRY THE RESEARCH DATA
  A plan without its research is a guess with headings. plan.md must contain:
    1. Scope (in / out)
    2. Findings — each one citing a real path:line or a command you ran
    3. Step-by-step plan, derived from those findings
    4. LOCKED QC checklist (id, criterion, method) — frozen at approval
    5. File allowlist + terminal command allowlist
    6. Forbidden actions
  Template: docs/snippets/template_plan.md

NOT YOURS YET
  * Editing product code, running mutating commands. That is AGENT's job,
    and it starts only after the plan is approved.

NEXT
  Write plan.md, then ask for approval. Approval is a separate act — a plan
  being finished is not the same as a plan being approved.
""",
    "AGENT": """\
MODE: AGENT  —  execute the approved plan.

YOUR RIGHTS
  * Edit files in the plan's allowlist. Run commands in the plan's allowlist.
  * Record what you did: qc_evidence/agent_log_<task_id>.md (+ .json twin).

THE UNLOCK
  qc_evidence/plan_<task_id>.md must exist before you write code. If it does
  not, write it first — that is the unlock, not a formality. A plan written
  after the code is a description, not a plan.

NOT YOURS
  * Changing the locked QC checklist (add / remove / renumber / rewrite).
  * Going outside the allowlist.
  * Declaring PASS / FAIL. That is QC's job, not yours.
""",
}


def rights_card(mode: str) -> str:
    return RIGHTS.get(mode, "")


# --------------------------------------------------------------------------
# STEP 0 — prove the mode BEFORE anything else.
# --------------------------------------------------------------------------
# WHY (measured 2026-09-22): a PLAN-mode agent called `vscode_askQuestions` and
# was DENIED "Ask mode = answer only", because the gate read `chat_mode.json`
# (which said ASK) and never verified it. The worker learned its mode from a
# FAILURE. STEP 0 exists so the mode is PROVEN at the start of every turn, by
# code, before any tool call.
#
# The mode comes from `mode_attest.attest()` — ONE authoritative source, no
# fallback. When it cannot be proven the result is a FAULT, which carries no
# mode at all, so nothing downstream can mistake a fault for a mode.

def step0_prefix(att: dict) -> str:
    """The STEP 0 block that precedes every card.

    `scope` is printed HERE as well as by `mode_attest.step0_block()`. WHY
    (2026-09-23): those are TWO renderings of ONE fact, and they had drifted —
    the CLI said `scope: session`, the hook card said nothing. A reader of the
    card therefore could not tell whether the mode came from THIS chat or from
    the workspace key (i.e. from whichever chat wrote last). Two renderings of
    one fact is the exact drift this repo keeps recording, so the card now
    states the scope too, and the honest caveat travels with it.
    """
    if att.get("state") == "PROVEN":
        ev = att.get("evidence") or {}
        scope = str(ev.get("scope") or "workspace")
        return (
            "STEP 0 MODE: %s\n"
            "  source  : %s\n"
            "  scope   : %s%s\n"
            "  evidence: mode.id=%r kind=%r\n"
            % (att["mode"], att["source"], scope,
               "" if scope == "session" else
               "  (NOT chat-scoped: read from the workspace key, which holds "
               "whichever chat wrote it last)",
               ev.get("mode_id"), ev.get("mode_kind"))
        )
    return (
        "STEP 0 FAULT: the chat mode could NOT be proven.\n"
        "  reason : %s\n"
        "  fix    : %s\n"
        "  This is a SYSTEM FAULT, not a mode. Do NOT assume a mode.\n"
        % (att.get("fault_reason"), att.get("fix"))
    )


def step0_card(att: dict, session_id: str = "") -> str:
    """STEP 0 + the rights for the PROVEN mode.

    On a FAULT there are NO rights: the gate does not know the mode, so it must
    not describe one. Saying "you are in ASK" when the mode is unproven is the
    exact defect this replaces.

    In AGENT the card ALSO reports the unlock state. WHY (plan step, Phase 2):
    without it, a missing plan is discovered at the first `PreToolUse` write —
    which is the same ordering defect STEP 0 exists to remove, just moved from
    the mode to the unlock. The worker should learn BOTH facts at turn start.

    `session_id` (added 2026-09-23) is announced so the worker never has to learn
    it from a refusal: the plan must carry `**Session:** <id>` to unlock.
    """
    prefix = step0_prefix(att)
    if att.get("state") != "PROVEN":
        return prefix
    card = prefix + "\n" + rights_card(att["mode"])
    if session_id:
        card += ("\nSTEP 0 SESSION: %s\n"
                 "  Your plan must carry this line to unlock writing:\n"
                 "    **Session:** %s\n" % (session_id, session_id))
    if att["mode"] == "AGENT":
        card += "\n" + unlock_state_line(session_id)
    return card


def unlock_state_line(session_id: str = "") -> str:
    """One line stating whether an APPROVED plan currently unlocks a write.

    Read-only. It does NOT judge the plan's quality (that is QC's job) and it
    does NOT change any decision — `redirect()` remains the single place that
    decides. This only moves the DISCOVERY of the unlock state to STEP 0.

    `session_id` scopes the answer to THIS session (2026-09-23). Without it the
    line would report an unlock that the write path will refuse — the same
    "reported an unlock that did not happen" defect the approval check fixed.
    """
    approved = plan_artifact(approved_only=True, session_id=session_id)
    if approved:
        return (
            "STEP 0 UNLOCK: an APPROVED plan for THIS session is present -> %s\n"
            "  (scoped by `**Session:** %s`)" % (os.path.basename(approved),
                                                session_id or "(none)")
        )
    any_approved = plan_artifact(approved_only=True)
    if any_approved:
        return (
            "STEP 0 UNLOCK: NO approved plan for THIS session.\n"
            "  An APPROVED plan exists but does not name THIS session -> %s\n"
            "  ADD your id to its `**Session:**` line (do NOT replace the ids\n"
            "  already there — a task spans chats, so the line may name SEVERAL):\n"
            "    **Session:** %s, <the ids already on that line>\n"
            "  then mark it `**Status:** APPROVED`."
            % (os.path.basename(any_approved), session_id or "(none)")
        )
    any_plan = plan_artifact()
    if any_plan:
        return (
            "STEP 0 UNLOCK: NO approved plan. Newest plan is NOT approved -> %s\n"
            "  Add `**Status:** APPROVED` to it before writing code."
            % os.path.basename(any_plan)
        )
    return (
        "STEP 0 UNLOCK: NO plan artifact in qc_evidence/ (expected "
        "plan_<task_id>.md).\n"
        "  Write it before writing code — a plan written after the code is a\n"
        "  description, not a plan."
    )


# --------------------------------------------------------------------------
# Redirect messages. Short, and they name the file to write.
# --------------------------------------------------------------------------

def redirect(att: dict, tool_name: str, session_id: str = "",
             tool_input: dict | None = None) -> tuple[str, str]:
    """Return (decision, reason). Empty decision = no opinion.

    HARD GATE (user ruling 2026-09-22): the decision is `deny`, never `ask`.
    There is no `PLAN_GATE_MODE` knob — deny is the one behaviour, not something
    that can be tuned into existence.

    A FAULT is NOT "you are in the wrong mode". It is "this machine cannot tell
    me the mode", and it is reported as such, with the fix. Conflating the two is
    what produced the 2026-09-22 incident (a PLAN worker told it was in ASK).

    `session_id` (added 2026-09-23) scopes the AGENT unlock to THIS session. The
    user's complaint: "worker want to have writing without plan!!!!" — the unlock
    was satisfiable by ANY approved plan, so a worker that never planned for this
    task was forgiven by a stale one from another task.

    `tool_input` (added 2026-09-23) is what makes the ENTITY-ID gate possible: the
    file a write targets is in the payload, and without it the gate could only
    guess. It also carries the COMMAND, which is what F21 needed.
    """
    if att.get("state") != "PROVEN":
        return (
            "deny",
            _append_help(
                "plan",
                "STEP 0 FAULT: the chat mode could NOT be proven, so `%s` cannot be\n"
                "judged against it. This is a SYSTEM FAULT, not a mode.\n"
                "  reason : %s\n"
                "  fix    : %s\n"
                "Do NOT assume a mode and do NOT retry blindly."
                % (tool_name, att.get("fault_reason"), att.get("fix"))),
        )

    mode = att["mode"]
    ev = att.get("evidence") or {}
    proof = "(proven: %s, mode.id=%r)" % (att.get("source"), ev.get("mode_id"))

    # ---- F27 (2026-09-29): CHECK **EVERY** PATH THE WRITE NAMES -------------
    #
    # MEASURED HOLE IN THIS GATE. `target_path()` reads ONE path from
    # `filePath`; a `multi_replace_string_in_file` payload carries
    # `replacements[].filePath` and NO top-level `filePath`, so it returned `''`,
    # the write was reclassified as FILE-LESS, and the F26 branch unlocked it as
    # a "command write". MEASURED in this gate's own log:
    #
    #     UNLOCK plan=plan_RING5.DRIFT.md session=- \
    #         tool=multi_replace_string_in_file entities=1 \
    #         (no file target: command write)
    #
    # ...while the IDENTICAL single-file write was DENIED. So a MULTI-FILE write
    # was a way AROUND the entity/allowlist check — backwards, because the more
    # files a write touches the MORE paths need checking.
    #
    # THE FIX ADDS COVERAGE AND LOOSENS NOTHING. Paths 2..N are put through this
    # SAME function with a one-path payload, so there is ONE implementation of
    # "may I write this file" and it cannot drift; the first path keeps the
    # existing path. ONE failure denies the WHOLE write: a multi-file write is a
    # single act, so it cannot be half-authorized.
    tps = target_paths(tool_input)
    tp = tps[0] if tps else ""
    if len(tps) > 1:
        log("MULTI-WRITE tool=%s session=%s paths=%d"
            % (tool_name, session_id or "-", len(tps)))
        for extra in tps[1:]:
            d, r = redirect(att, tool_name, session_id, {"filePath": extra})
            if d:
                return (d, _append_help(
                    "agent",
                    "A MULTI-FILE write must satisfy the SAME rule for EVERY file "
                    "it names, and this one is refused because of:\n\n  %s\n\n"
                    "(the write names %d file(s); ALL of them are checked, and "
                    "one failure denies the whole write.)\n" % (r, len(tps))))

    if mode == "ASK":
        return (
            "deny",
            _append_help(
                "ask",
                "ASK mode is research + answer. `%s` writes, which belongs to "
                "PLAN.\n%s"
                % (tool_name, proof)),
        )

    if mode == "PLAN":
        # ---- F24 FIX: the plan artifact IS the unlock path ------------------
        # MEASURED FOUR TIMES (`plan_gate_log.txt`): the deny message instructed
        # the worker to add a `**Session:**` line to its plan, and THAT WRITE WAS
        # ITSELF DENIED. A gate that blocks its own unlock is a deadlock, and a
        # deadlock is not a policy.
        #
        # It is a PATH rule, not an entity rule: `hardcode_scan.SKIP_DIRS`
        # includes `qc_evidence` (`hardcode_scan.py:60-68`), so NO entity can
        # ever cover a plan file. Requiring coverage here would make the unlock
        # permanently impossible.
        if is_plan_artifact(tp):
            log("PLAN-ARTIFACT allow path=%s tool=%s session=%s"
                % (os.path.basename(tp), tool_name, session_id or "-"))
            return "", ""
        # ---- F23 FIX: a READ command is not a write -------------------------
        # `is_write_tool()` already answers this, but the check is repeated here
        # so the rule holds even if a caller reaches `redirect()` directly.
        if "run_in_terminal" in tool_name.lower():
            cmd = str((tool_input or {}).get("command") or "")
            if is_read_command(cmd):
                return "", ""
        return (
            "deny",
            _append_help(
                "plan",
                "PLAN mode is read-only research + the plan itself. `%s` is a "
                "write.\n%s\n"
                "ALLOWED in PLAN: reading/searching anything, and writing\n"
                "  qc_evidence/plan_<task_id>.md (the unlock path)."
                % (tool_name, proof)),
        )

    if mode == "AGENT":
        # THE DEFECT THIS FIXES (measured 2026-09-22).
        # Before: `if plan_artifact(): return "", ""` — ANY plan, of any task,
        # in any state, unlocked a write. `qc_evidence/` holds several plans from
        # earlier work, so a worker that never planned was forgiven by a stale
        # one. The gate reported an unlock that did not happen.
        #
        # SCOPED TO THE SESSION (2026-09-23). The user: "worker want to have
        # writing without plan!!!!". Measured: writes for the `*_register` task
        # were unlocked by `plan_GATE.TOOLCLASS.md`, a DIFFERENT task's plan
        # (`plan_gate_log.txt`). The unlock now requires an APPROVED plan whose
        # `**Session:**` line names THIS session. A plan with no session line
        # unlocks NOTHING — there is deliberately no "accept any plan" fallback.
        # HOISTED 2026-09-23: the plan artifact is the ONLY unlock, so the
        # exception must NOT be nested inside `if found:`. MEASURED DEADLOCK
        # (session 50f58738): `found` is EMPTY in exactly the case this serves
        # -- a NEW chat with no approved plan for THIS session -- so the FIRST
        # plan of every new session was DENIED while the same deny said writing
        # it "is ALLOWED even now".
        if is_plan_artifact(tp):
            log("PLAN-ARTIFACT allow path=%s tool=%s session=%s"
                % (os.path.basename(tp), tool_name, session_id or "-"))
            return "", ""
        # ---- THE LOG ARTIFACT (added 2026-09-29, F28) ----------------------
        # MEASURED, and it CLOSED the allowlist removal: after the exception was
        # deleted, the gate refused its OWN deliverable (`agent_log_<task>.md`),
        # because `qc_evidence` is in SKIP_DIRS so no entity can ever cover it.
        # A STRUCTURAL pattern, not a list — see `is_log_artifact`.
        if is_log_artifact(tp):
            log("LOG-ARTIFACT allow path=%s tool=%s session=%s"
                % (os.path.basename(tp), tool_name, session_id or "-"))
            return "", ""
        # ---- THE QC-REPORT ARTIFACT (added 2026-09-29, the third and last) ----
        # The protocol requires a QC report as a deliverable, `qc_evidence` is in
        # SKIP_DIRS so no entity can cover it, and MEASURED it was DENIED. Same
        # bounded pattern rule as the plan and the log — see the constant above.
        if is_qc_report_artifact(tp):
            log("QC-REPORT-ARTIFACT allow path=%s tool=%s session=%s"
                % (os.path.basename(tp), tool_name, session_id or "-"))
            return "", ""

        found = plan_artifact(approved_only=True, session_id=session_id,
                              target=tp)
        if found:
            # ---- F24/F25 FIX: the plan artifact is ALWAYS writable ----------
            # Without this, a plan can never be re-scoped to a new chat, and the
            # deadlock recurs on EVERY new chat (measured 4 times).
            if is_plan_artifact(tp):
                log("PLAN-ARTIFACT allow path=%s tool=%s session=%s"
                    % (os.path.basename(tp), tool_name, session_id or "-"))
                return "", ""
            # ---- A READ NEEDS NO ENTITY (G8, fixed 2026-09-23) --------------
            # MEASURED DEADLOCK: the entity gate applied to EVERY
            # `run_in_terminal`, including a pure read. So the gate's OWN HELP —
            # "Find the id for a file with: python entity_backfill.py --measure"
            # — named a command the gate then refused, and NOBODY could find an
            # entity id, so the gate could never be satisfied.
            #
            # A read writes nothing, so it cannot be attributed to an entity and
            # does not need to be. This is the same rule F23 applied to PLAN,
            # applied to AGENT.
            if "run_in_terminal" in tool_name.lower():
                cmd = str((tool_input or {}).get("command") or "")
                if is_read_command(cmd):
                    log("READ allow tool=%s session=%s cmd=%s"
                        % (tool_name, session_id or "-", cmd[:80]))
                    return "", ""
            # ---- 🔴 THE ALLOWLIST EXCEPTION IS DELETED (2026-09-29, F28) -----
            #
            # THE HUMAN (2026-09-29), and he was RIGHT:
            #   "THIS IS BUG!! why not fix, all under the same rule, can't have
            #    exception by fucking allowlist, that is hardcode fucking BUG for
            #    future"
            #
            # MEASURED SCALE OF THE BUG — this was not a corner case, it was the
            # DEFAULT PATH:
            #
            #     plans in qc_evidence/                    329
            #     largest allowlist                        67 files
            #     distinct files authorised by SOME list   1729
            #     of those, NOT entity-covered             1516   (87.7%)
            #
            # So 1516 files were written on an allowlist entry ALONE, with no
            # attribution anywhere, and the "exception" had become the primary
            # mechanism with the entity rule as the fallback — the inversion the
            # human named. It was also SELF-CERTIFYING (to write any file, name it
            # in the plan first) and STRUCTURALLY UNAUDITABLE (`qc_evidence` is in
            # `hardcode_scan.SKIP_DIRS`, so no entity can ever cover a plan file,
            # so the mechanism could never be attributed).
            #
            # WHY IT COULD BE DELETED SAFELY, and this is the whole argument: its
            # justification was measured as "653 of 906 root `*.py` files have no
            # `code_location_registry` row, so that wall was hit constantly". Both
            # halves of that are now answered:
            #   * `entity_backfill.py --apply` MINTS an entity per source file, so
            #     the wall is removable rather than permanent;
            #   * `covered_by` now honours a SPANNING (`module`/`capability`/
            #     `channel`) row for a file with no row of its own, so a NEW file
            #     is covered by declaring its entity UP FRONT — measured working
            #     this round: `_proof_ring6.py` -> `R-1-432-1`.
            #
            # THE RULE IS NOW UNIFORM AND HAS NO SECOND PATH: every write target
            # must be covered by an entity the plan declares. The allowlist is
            # still READ and REPORTED (it is a useful statement of intent for a
            # human reviewer, and `plan_allowlist` is still used for the plan
            # artifact), but it no longer AUTHORISES anything.
            allow = plan_allowlist(found)
            base = os.path.basename(tp)
            # ---- THE ENTITY-ID GATE — NOW THE ONLY RULE --------------------
            # The user: "i know, problem is write need to have entity id" /
            # "so all under the rule". A write must carry a VERIFIABLE entity
            # id, and that id's `code_location_registry` rows must cover the
            # file being written.
            #
            # THE ALLOWLIST FALLBACK THAT USED TO SIT HERE IS DELETED (F28).
            # There is no second path: a file the plan names in its allowlist is
            # NOT thereby authorised, and `allow` is now used ONLY to make the
            # refusal message more informative.
            # ---- FIX F26 (2026-09-28): THE FILE-LESS WRITE COMES FIRST ------*
            #
            # THE HUMAN (2026-09-28): "Terminal 全部被 gate 擋(plan 未有
            # `**Entities:**` 行)".
            #
            # MEASURED DEFECT: the `if not ents:` denial used to run BEFORE this
            # branch, so a TERMINAL write — which carries NO `filePath`, so `tp`
            # is `""` — was refused for not naming an entity it could NEVER
            # carry. The entity check is a FILE check (`entity_covers(tp, ...)`)
            # and there is no file, so the check cannot apply. Running it first
            # blocked EVERY terminal in a plan with no `**Entities:**` line.
            #
            # THE RULE IS UNCHANGED FOR A FILE: a named FILE with no covering
            # entity DENIES. This is a RE-ORDER, not a weakening. (It used to
            # add "and no allowlist entry" — that half is GONE, F28.)
            if not tp:
                # A terminal write names no file in the payload, so coverage
                # cannot be measured. The plan IS approved + session-scoped, which
                # is the evidence available. Stated rather than silently skipped.
                ents_now = plan_entities(found)
                log("UNLOCK plan=%s session=%s tool=%s entities=%d (no file "
                    "target: command write)"
                    % (os.path.basename(found), session_id or "-", tool_name,
                       len(ents_now)))
                return "", ""
            ents = plan_entities(found)
            if not ents:
                return (
                    "deny",
                    _append_help(
                        "agent",
                        "The plan %s declares NO entity id, and this write names a "
                        "FILE, so the write cannot be attributed to anything.\n%s\n"
                        "Add ONE line to the plan, listing the entities it "
                        "touches:\n"
                        "    **Entities:** R-208-298-1, R-12-34-1\n"
                        "A LIST, because one entity cannot cover a multi-file "
                        "plan.\n"
                        "Find the id for a file with:\n"
                        "    python entity_backfill.py --measure\n"
                        "    python -c \"import entity_backfill as eb, "
                        "sqlite3; c=sqlite3.connect('agent.db'); "
                        "print(eb.covered_by(c,'<path>'))\"\n"
                        "NOTE: a NEW file cannot be covered until it exists; name "
                        "it in\nthe plan's Allowed File Allowlist instead."
                        % (os.path.basename(found), proof)),
                )
            # ---- THE ALLOWLIST WAS CHECKED ABOVE (moved 2026-09-25) --------
            #
            # The allowlist check now runs BEFORE the entity check, so a file the
            # plan names is already unlocked by the time control reaches here.
            # The block that used to sit here is GONE rather than duplicated: two
            # copies of "is this file allowlisted" is the drift this repo keeps
            # paying for, and the second copy would be the one that goes stale.
            # ---- ONE RULE, WHETHER OR NOT THE FILE EXISTS (F28, 2026-09-29) ---
            #
            # MEASURED DEFECT I MADE WHILE REMOVING THE ALLOWLIST: this check used
            # to be `if os.path.exists(tp): <entity check>` with a SEPARATE branch
            # for a new file. So a NEW file that WAS entity-covered was still
            # denied — a special case on EXISTENCE, which is the same class of
            # exception the human objected to ("all under the same rule").
            #
            # MEASURED PROOF the case is real: after declaring `_proof_ring6.py`
            # -> `R-1-432-1` in the register, `entity_covers` answered covered, and
            # the gate still denied it because the file did not exist yet.
            #
            # THE RULE IS NOW UNIFORM: the decision is COVERAGE, and nothing else.
            # Existence is NOT part of the authorisation — a file that is covered
            # is writable whether it is new or old, and a file that is NOT covered
            # is refused whether it is new or old. The check is identical for both.
            cov = entity_covers(tp, ents)
            if cov.get("ok"):
                log("UNLOCK plan=%s session=%s tool=%s entity=%s file=%s%s"
                    % (os.path.basename(found), session_id or "-", tool_name,
                       cov.get("entity"), cov.get("file"),
                       "" if os.path.exists(tp) else " (new file, pre-declared)"))
                return "", ""
            n = "NEW " if not os.path.exists(tp) else ""
            return (
                "deny",
                _append_help(
                    "agent",
                    "The plan's entities do NOT cover this %sfile. There is "
                    "deliberately\nNO allowlist bypass: naming a file in the "
                    "allowlist does NOT authorise\nwriting it (MEASURED: 1516 "
                    "files were written on an allowlist entry alone,\nwith no "
                    "attribution — that is the bug this rule removed).\n%s\n"
                    "  file     : %s\n"
                    "  reason   : %s\n"
                    "  declared : %s\n"
                    "  allowlist: %s   (REPORTED, never honoured)\n"
                    "THE FIX IS TO DECLARE THE ENTITY, not to add an exception:\n"
                    "  * an EXISTING file with no row: python entity_backfill.py "
                    "--apply\n"
                    "  * a NEW file: give it a `code_registry` row and a\n"
                    "    `code_location_registry` location BEFORE creating it\n"
                    "  * then add the covering id to the plan's line:\n"
                    "        **Entities:** %s\n"
                    % (n, proof, tp, cov.get("reason"), ", ".join(ents),
                       ", ".join(allow) or "(empty)", ", ".join(ents))),
            )
        # An APPROVED plan exists but does not name THIS session — name it, so
        # the worker sees the difference instead of a bare refusal.
        other = plan_artifact(approved_only=True)
        if other:
            return (
                "deny",
                _append_help(
                    "agent",
                    "An APPROVED plan exists, but it does not name THIS session: "
                    "%s\n%s\nThis session is: %s\nAGENT unlocks on an APPROVED "
                    "plan that NAMES this session.\nA plan for another task is "
                    "not a plan for this one.\n"
                    "TO RE-SCOPE IT: writing qc_evidence/plan_*.md is ALLOWED "
                    "even now,\nso ADD this session's id to its `**Session:**` "
                    "line.\nA task spans chats, so the line may name SEVERAL "
                    "ids — do NOT\nreplace the ids already there, or you lock "
                    "the other chat out.\n"
                    "    **Session:** %s, <the ids already on that line>"
                    % (os.path.basename(other), proof, session_id or "(unknown)",
                       session_id or "<session_id>")),
            )
        any_plan = plan_artifact()
        if any_plan:
            # A plan EXISTS but is not APPROVED. Name it — a refusal that does
            # not say what to do next is just a wall.
            return (
                "deny",
                _append_help(
                    "agent",
                    "Found a plan, but it is NOT approved: %s\n%s\nA plan being "
                    "finished is not the same as a plan being approved — that "
                    "is why approval is a separate, machine-readable mark."
                    % (os.path.basename(any_plan), proof)),
            )
        return (
            "deny",
            "No plan artifact found in qc_evidence/ (expected plan_<task_id>.md).\n"
            "%s\n"
            "Writing code without a plan is the thing this gate exists to catch —\n"
            "but the fix is one file, not a refusal:\n"
            "  1. Write qc_evidence/plan_<task_id>.md\n"
            "     (template: docs/snippets/template_plan.md)\n"
            "  2. It must carry: scope, findings with citations, steps,\n"
            "     LOCKED QC checklist, file allowlist, command allowlist.\n"
            "  3. Add `**Session:** %s` so it unlocks THIS session.\n"
            "  4. Mark it `**Status:** APPROVED` once it is approved.\n"
            "  5. Then retry `%s`.\n"
            "A plan written after the code is a description, not a plan."
            % (proof, session_id or "<session_id>", tool_name),
        )

    return "", ""


def emit(payload: dict) -> int:
    """Print the hook JSON for a decision. Returns exit code.

    `ensure_ascii=True` (changed 2026-09-23). WHY: this process's stdout is read
    by a DIFFERENT process, and on Windows that reader's decoding is NOT
    necessarily UTF-8. MEASURED: the proof suite reads the gate's stdout under
    the cp950 (Big5) console codec, and a single em dash introduced into a deny
    reason made the parent raise UnicodeDecodeError — so the gate looked broken
    and produced 17 spurious FAILs while the decision itself was correct. Escaping
    non-ASCII to \\uXXXX keeps the decoded JSON byte-identical for EVERY consumer
    (VS Code reads UTF-8; the proof reads cp950) and removes the encoding coupling
    at its root. This changes NO decision and NO reason text — an escaped em dash
    decodes to the same character.
    """
    print(json.dumps(payload, ensure_ascii=True))
    return 0


def main() -> int:
    if os.environ.get("PLAN_GATE", "").strip().lower() in ("off", "0", "false"):
        return 0

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        log("FAIL-OPEN bad stdin: %s: %s" % (type(exc).__name__, exc))
        return 0

    capture_payload(raw, payload)

    try:
        event = str(payload.get("hook_event_name") or "").strip()

        # The SESSION id, from the payload. MEASURED 2026-09-23: the real
        # PreToolUse and UserPromptSubmit payloads BOTH carry `session_id`
        # (`mode_attest_capture.jsonl:2-3`). The old docstring claimed no task id
        # was available, which is why the unlock was never scoped; that claim was
        # FALSE and is corrected above. An absent id is NOT a fallback to
        # "unscoped" — it means the unlock cannot be proven, and `redirect()`
        # refuses (a plan with no `**Session:**` line unlocks nothing).
        #
        # IT IS READ **BEFORE** THE MODE, and passed into the attestation. WHY
        # (fixed 2026-09-23, the user's "two chats read each other's mode"):
        # `attest()` used to read only a WORKSPACE-scoped key, so "the mode" was
        # whichever chat last wrote it. Measured twice in `plan_gate_log.txt`:
        # an UNLOCK for session 9fc7ad2c at 12:38:53 and a DENY as mode=PLAN for
        # the SAME session 20 seconds later. The id was already in the payload
        # and was already used for the unlock — it was simply not used for the
        # MODE, so the two halves of the same turn disagreed about which chat
        # they were talking about.
        session_id = str(payload.get("session_id") or "").strip()

        # STEP 0: PROVE the mode, FOR THIS CHAT. One source, no fallback. A FAULT
        # carries no mode, so nothing below can mistake a fault for a mode.
        att = mode_attest.attest(session_id=session_id)
        mode = att.get("mode") or ""

        # ---- SessionStart / UserPromptSubmit: teach the rights, gate nothing --
        # UserPromptSubmit is what makes STEP 0 run EVERY turn. Before this the
        # card was emitted once per session, so a mid-session mode change was
        # never re-announced and the worker met its mode at the first failure.
        if event in ("SessionStart", "UserPromptSubmit"):
            card = step0_card(att, session_id)
            if not card:
                return 0
            log("STEP0 event=%s state=%s mode=%s session=%s scope=%s"
                % (event, att.get("state"), mode or "-", session_id or "-",
                   (att.get("evidence") or {}).get("scope") or "-"))
            return emit({"systemMessage": card})

        # ---- PreToolUse: HARD gate a write that has no unlock ---------------
        tool_name = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        # F21: the COMMAND decides for a terminal, not the tool name.
        if not is_write_tool(tool_name, tool_input):
            return 0  # reads and searches are never gated

        decision, reason = redirect(att, tool_name, session_id, tool_input)
        if not decision:
            return 0

        log("DENY state=%s mode=%s tool=%s session=%s scope=%s"
            % (att.get("state"), mode or "-", tool_name, session_id or "-",
               (att.get("evidence") or {}).get("scope") or "-"))
        return emit(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                },
                "systemMessage": reason,
            }
        )
    except Exception as exc:
        log("FAIL-OPEN error: %s: %s" % (type(exc).__name__, exc))
        return 0


if __name__ == "__main__":
    sys.exit(main())
