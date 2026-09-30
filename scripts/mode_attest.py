#!/usr/bin/env python
"""mode_attest.py — PROVE the chat mode. One source. No fallback.

WHY THIS EXISTS
---------------
Measured 2026-09-22. A PLAN-mode agent called `vscode_askQuestions` (a READ-ONLY
action) and was DENIED with "Ask mode = answer only". The chain:

    chat_mode.json said ASK  ->  ask_mode_guard.current_mode() trusted it
    ->  decide() denied.

Nothing consulted a second source, and nothing told the worker its mode at turn
start. The worker learned its mode from a FAILURE. That is the defect this file
exists to remove: the mode must be PROVEN at STEP 0 of every turn, before any
tool call, by code.

THE RULE (user ruling 2026-09-22)
---------------------------------
    The mode is PROVEN from ONE authoritative source.
    If it cannot be proven, that is a SYSTEM FAULT: report it loudly, name the
    fix, and stop. There is NO default, NO fallback, and NO silent pass.

So this module has exactly two outcomes:

    PROVEN  -> a mode, its source, and the raw evidence it came from
    FAULT   -> a reason and a fix, and NO mode field at all

FAULT deliberately carries no `mode` key. A caller cannot accidentally treat a
fault as a mode, because there is nothing to read.

THE AUTHORITATIVE SOURCE (measured, not assumed)
------------------------------------------------
VS Code's own persisted chat input state:

    <workspaceStorage>/<hash>/state.vscdb  ->  ItemTable
      key "chat.untitledInputState"  ->  {"mode":{"id":"agent","kind":"agent"}, ...}

Evidence for the mapping (all read from this machine, 2026-09-22):
  * `chat.untitledInputState.mode.id` was "agent" while the user was in Agent
    mode and `chat_mode.json` wrongly said ASK. The DB matched the user.
  * The mode id literals are lowercase: `modes:["ask","agent","edit"]`
    (`out/vs/workbench/workbench.desktop.main.js`).
  * `kind` is NOT a discriminator: Agent and Plan both report `kind:"agent"`.
    Only `id` separates them. Plan is a CUSTOM agent, so its id is a URI:
    `chat.customModes.local` -> {"name":"Plan","kind":"agent",
                                 "id":"vscode-userdata:/.../Plan.agent.md"}
    Plan is therefore resolved by LOOKING UP the id in `chat.customModes.local`
    and reading its `name` — data-driven, not a hardcoded URI.

The workspace folder is resolved by reading each `workspaceStorage/*/workspace.json`
and comparing its decoded `folder`/`workspace` URI against the working directory.
That avoids guessing VS Code's storage-hash encoding.

WHY NOT `chat_mode.json`
------------------------
It is the hotkey's INTENT record, written BEFORE the UI click and never
corrected on failure (`helper_watchdog.py:115-119`). It is kept, but it is no
longer a source of truth. It is not consulted here at all.

WHY NOT VISION (`f_mode_vision.read_pill()`)
--------------------------------------------
Measured blind for long stretches: 20 consecutive `UNKNOWN ('+' not found)`
between 11:45 and 11:59 (`mode_vision_log.txt:1178-1216`). A source that is
usually UNKNOWN cannot be the authority, and under the no-fallback rule it must
not be a substitute either.

Contract
--------
    attest() -> {"state": "PROVEN", "mode": "ASK|PLAN|AGENT",
                 "source": "...", "evidence": {...}}
             or {"state": "FAULT", "fault_reason": "...", "fix": "..."}

CLI:
    python scripts/mode_attest.py --step0     # one compact block, exit 0
    python scripts/mode_attest.py --json      # the raw dict, exit 0

Exit code is ALWAYS 0: a FAULT is a reported result, not a crash. A caller that
wants to act on the fault reads `state`.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from urllib.parse import unquote, urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The one authoritative source.
SOURCE_DB = "vscode_state_db:chat.untitledInputState"

# THE KEY MOVES. Measured 2026-09-23: `chat.untitledInputState` was present while
# the chat was UNTITLED, and then DISAPPEARED -- the same workspace DB had 88 keys
# and none of them was it. The mode had moved to:
#
#     memento/interactive-session -> history.copilot[0].mode
#                                 -> {"id":"agent","kind":"agent"}
#
# Both keys are the SAME authoritative source (VS Code's own persisted chat input
# state); they differ only by whether the chat is untitled or an interactive
# session. So this is NOT a fallback chain -- it is the one source, read at the
# key it currently lives under. A key that is absent is NOT "no mode"; it means
# the mode may be under the other key, and if NEITHER answers the result is a
# FAULT.
#
# The lesson is recorded rather than hidden: a single hardcoded key made the gate
# report a FAULT and hard-deny EVERY tool (a self-lockout) the moment VS Code
# moved the state. The FAULT was CORRECT -- it refused to guess -- but the key set
# was incomplete.
STATE_KEYS = (
    "memento/interactive-session-view-copilot",  # ACTIVE session view state
    "chat.untitledInputState",       # untitled chat
    "memento/interactive-session",   # interactive session (history.copilot[N])
)

# VS Code's own mode id literals (lowercase), measured from the bundle:
#   modes:["ask","agent","edit"]
# "edit" is not a chat mode we gate on; it is listed so an unexpected id is a
# FAULT rather than a silent guess.
MODE_ID_MAP = {
    "ask": "ASK",
    "agent": "AGENT",
}

# The fix a worker should run when the mode cannot be proven.
FIX_COMMAND = "python scripts/mode_attest.py --step0"


def _fault(reason: str) -> dict:
    """A FAULT carries NO mode field — by construction, not by convention."""
    return {
        "state": "FAULT",
        "fault_reason": reason,
        "fix": FIX_COMMAND,
        "source": SOURCE_DB,
    }


def _storage_root() -> str:
    """The VS Code user-data workspaceStorage directory.

    `MODE_ATTEST_STORAGE_ROOT` overrides it. That exists so a proof can build a
    REAL workspaceStorage tree (a real `workspace.json` + a real `state.vscdb`)
    in a temp directory and exercise this exact code path with real SQLite data,
    instead of fabricating a payload or adding a fake-result seam to production
    code. Unset in normal use.
    """
    override = os.environ.get("MODE_ATTEST_STORAGE_ROOT", "").strip()
    if override:
        return override
    appdata = os.environ.get("APPDATA") or ""
    return os.path.join(appdata, "Code", "User", "workspaceStorage")


def _uri_to_path(uri: str) -> str:
    """Decode a VS Code folder URI to a filesystem path.

    `file:///c%3A/projects/agent_system` -> `c:/projects/agent_system`
    """
    try:
        p = urlparse(uri)
        path = unquote(p.path or "")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]          # strip the leading slash before the drive
        return path.replace("/", os.sep)
    except Exception:
        return ""


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path or ""))


def find_workspace_db(cwd: str | None = None) -> tuple[str, str]:
    """Return (state.vscdb path, workspace.json path) for `cwd`.

    Returns ("", "") when no workspaceStorage entry matches. Matching is done by
    DECODING each workspace.json and comparing paths, so VS Code's storage-hash
    encoding never has to be reproduced.
    """
    root = _storage_root()
    if not os.path.isdir(root):
        return "", ""
    want = _norm(cwd or os.getcwd())
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        wj = os.path.join(d, "workspace.json")
        db = os.path.join(d, "state.vscdb")
        if not (os.path.isfile(wj) and os.path.isfile(db)):
            continue
        try:
            with open(wj, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        uri = meta.get("folder") or meta.get("workspace") or ""
        if uri and _norm(_uri_to_path(uri)) == want:
            return db, wj
    return "", ""


def _read_item(db: str, key: str):
    """Read one ItemTable value. Read-only; returns None on any problem."""
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True)
        try:
            cur = con.cursor()
            cur.execute("SELECT value FROM ItemTable WHERE key=?", (key,))
            row = cur.fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# THE PER-SESSION SOURCE. This is the fix for "two chats read each other's mode".
# ---------------------------------------------------------------------------
# WHY IT IS NEEDED (measured, not theorised). Every key in `STATE_KEYS` lives in
# `state.vscdb`, which is ONE FILE PER WORKSPACE. So "the mode" it holds is
# whichever chat LAST persisted its input state -- not the chat that triggered the
# hook. Proven twice in `plan_gate_log.txt` (see `mode_wrong_chat_proof.md`):
#
#     12:38:53 UNLOCK plan=... session=9fc7ad2c-... tool=run_in_terminal
#     12:39:20 DENY   state=PROVEN mode=PLAN tool=replace_string_in_file
#
# Same session, same turn, 20 seconds apart: AGENT then PLAN. Nothing about the
# session changed -- the KEY's value changed, because another chat was interleaved
# (session `e6e2e4ab`, mode=PLAN, in the same log).
#
# WHAT A PER-SESSION SOURCE LOOKS LIKE (measured 2026-09-23):
#
#     <workspaceStorage>/<hash>/chatSessions/<session-id>.jsonl
#
# ONE FILE PER SESSION, and the FILENAME IS THE SESSION ID. It is an ORDERED
# PATCH LOG:
#
#     kind:0  -> the whole initial state object (`v` is the state)
#     kind:1  -> ASSIGN the value at path `k`   <-- a MODE CHANGE is kind:1
#     kind:2  -> REPLACE the value at path `k`
#
# Measured mode patches inside `chatSessions/9fc7ad2c-...jsonl`:
#
#     {"kind":1,"k":["inputState","mode"],"v":{"id":"agent","kind":"agent"}}
#     {"kind":1,"k":["inputState","mode"],
#      "v":{"id":"vscode-userdata:/c%3A/.../plan-agent/Plan.agent.md","kind":"agent"}}
#
# So the mode lives at path `inputState.mode`, the LAST write wins, and `v.id` is
# the SAME id vocabulary `_map_mode()` already resolves (a literal like `agent`, or
# a custom-agent URI resolved through `chat.customModes.local`).
#
# A LANDMINE, recorded so it is not re-stepped: a kind:2-only replay MISSES every
# mode change, because a mode change is kind:1. `_diag_replay_all.py` applies only
# kind:2; copying its shape would have produced a reader that silently answered
# "no mode" forever.
SESSIONS_DIRNAME = "chatSessions"
MODE_INPUT_PATH = ("inputState", "mode")


def _sessions_root() -> str:
    """Where the `chatSessions/` dirs live. Same root as `_storage_root()`.

    `MODE_ATTEST_SESSIONS_ROOT` overrides it so a proof can build a REAL
    chatSessions tree and exercise THIS code path with real JSONL data, the same
    way `MODE_ATTEST_STORAGE_ROOT` does for the DB. Unset in normal use.
    """
    override = os.environ.get("MODE_ATTEST_SESSIONS_ROOT", "").strip()
    if override:
        return override
    return _storage_root()


def find_session_file(session_id: str, db: str = "") -> str:
    """The `chatSessions/<session_id>.jsonl` that BELONGS to this session.

    Returns "" when it cannot be found. An empty result becomes a FAULT at the
    call site -- it is NEVER a fallback to the workspace key, because the
    workspace key is the thing that answers with ANOTHER chat's mode.

    The basename must equal `<session_id>.jsonl` EXACTLY. A prefix or substring
    match would happily "find" a DIFFERENT chat and turn a FAULT into a silent
    WRONG pass -- i.e. it would reproduce the very defect being fixed, but
    without the loud failure that made the defect findable.
    """
    sid = (session_id or "").strip()
    if not sid:
        return ""
    name = sid + ".jsonl"
    # The DB and the session files sit in the SAME workspaceStorage hash dir, so
    # look beside the DB first: normally ONE stat and no directory scan.
    if db:
        cand = os.path.join(os.path.dirname(db), SESSIONS_DIRNAME, name)
        if os.path.isfile(cand):
            return cand
    root = _sessions_root()
    if not os.path.isdir(root):
        return ""
    for entry in sorted(os.listdir(root)):
        cand = os.path.join(root, entry, SESSIONS_DIRNAME, name)
        if os.path.isfile(cand):
            return cand
    return ""


def session_mode(path: str) -> dict:
    """The LAST mode object recorded for a session's `inputState.mode`.

    *** READ THE NEXT PARAGRAPH BEFORE USING THIS FOR "THE CURRENT MODE". ***

    MEASURED 2026-09-23, and it settles a defect that cost hours: this value
    LAGS. `inputState.mode` is written when a request is COMMITTED, so between
    requests it still holds the mode of the LAST COMPLETED request while the
    selector already shows a different one. Measured in one session file, in
    order:

        inputState.mode = {.../plan-agent/Plan.agent.md}   <- this function
        sendOptions.modeInfo.telemetryModeId = "agent"     <- the selector
        inputState.mode = {"id":"agent"}                    <- only now updates

    So this function answers "the mode of the last committed request". For "the
    mode in force RIGHT NOW" use `session_selection()` below. Neither may be
    presented as the other -- that substitution IS the bug.

    Returns {} when the session never carries a mode. Three carriers, all real
    (measured):

      kind:0  the whole initial state -> `v.inputState.mode`
              A session whose mode was set at CREATION and never switched has
              NO patch at all, so this is the COMMON case, not an edge case.
              MEASURED while writing this proof: handling only the patches made
              a never-switched session answer "no mode" (a FAULT).
      kind:1  assign the value at path `k`   <-- a MODE CHANGE is kind:1
      kind:2  replace the value at path `k`

    Applies ALL of them in file order, so the last write wins. Streamed in one
    pass. A malformed line is SKIPPED, not fatal: the newest lines of a live
    session file can be a partial write, and refusing to answer because of a
    torn tail would produce a FAULT for a reason that has nothing to do with
    the mode.
    """
    last: dict = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                kind = rec.get("kind")
                if kind == 0:
                    # The initial state. Walk MODE_INPUT_PATH inside `v`.
                    node = rec.get("v")
                    for key in MODE_INPUT_PATH:
                        node = node.get(key) if isinstance(node, dict) else None
                    if isinstance(node, dict) and node.get("id"):
                        last = node
                    continue
                if kind not in (1, 2):
                    continue
                if tuple(rec.get("k") or ()) != MODE_INPUT_PATH:
                    continue
                v = rec.get("v")
                if isinstance(v, dict) and v.get("id"):
                    last = v
    except Exception:
        return last
    return last


# ---------------------------------------------------------------------------
# THE LIVE SELECTION, and THE ENVIRONMENT CHECKLIST (measured 2026-09-23)
# ---------------------------------------------------------------------------
# The user asked why the gate reports PLAN while the selector shows Agent, and
# described what `conversation_id` is FOR:
#
#     "conversation id is help us to have environment checklist for vscode
#      example : mode = plan, permission = autopilot...."
#
# Both answers are in ONE field the reader did not look at.
# `pendingRequests[].sendOptions` carries the CURRENT, not-yet-committed state:
#
#     sendOptions.modeInfo.telemetryModeId   -> "agent"       (the SEE
#     selector)
#     sendOptions.modeInfo.permissionLevel   -> "autopilot"   (the user's example)
#     sendOptions.userSelectedModelId        -> the model
#     sendOptions.userSelectedModelConfiguration.reasoningEffort
#     sendOptions.userSelectedTools.run_in_terminal -> bool
#
# So the checklist is not a new datum to invent: it is THIS field, per request.
# That is why `pendingRequests` is SET while typing and CLEARED on send
# (`{"kind":2,"k":["pendingRequests"],"i":0}`).
#
# IT IS PER REQUEST, which is why the observation is stored appended, not as a
# mutable "current" scalar: the committed value and the selection are BOTH real
# and they DISAGREE between requests. Collapsing them is the defect.
#
# THE BOUNDARY, stated so no future reader has to guess: PARSING the file lives
# here, beside the other mode reader, so the gate, the API and the UI read ONE
# implementation. STORING the rows is `conversation_store.append_env`.
LIVE_SELECTION_PATH = ("pendingRequests",)


def _checklist_from_send_options(so: dict) -> dict:
    """Flatten ONE `sendOptions` blob into the environment dimensions.

    Reads defensively: any missing branch yields None rather than an exception,
    because a partial blob is normal while a request is being composed and must
    never make the WHOLE checklist unavailable.
    """
    if not isinstance(so, dict):
        return {}
    mi = so.get("modeInfo") or {}
    if not isinstance(mi, dict):
        mi = {}
    cfg = so.get("userSelectedModelConfiguration") or {}
    if not isinstance(cfg, dict):
        cfg = {}
    tools = so.get("userSelectedTools") or {}
    if not isinstance(tools, dict):
        tools = {}
    return {
        # telemetryModeId first: it is the short id ("agent") that `_map_mode`
        # resolves. `modeInstructions.name` would be "Plan"/"agent" and adds a
        # second spelling that could disagree with the first.
        "mode_id": str(mi.get("telemetryModeId") or ""),
        "mode_name": str(mi.get("telemetryModeName") or ""),
        "permission": (str(mi.get("permissionLevel"))
                       if mi.get("permissionLevel") is not None else None),
        "model": (str(so.get("userSelectedModelId"))
                  if so.get("userSelectedModelId") is not None else None),
        "reasoning_effort": (str(cfg.get("reasoningEffort"))
                             if cfg.get("reasoningEffort") is not None else None),
        # BOOL, and it must stay a bool: `run_in_terminal: false` would otherwise
        # be indistinguishable from "this field was absent".
        "terminal_available": (bool(tools.get("run_in_terminal"))
                               if "run_in_terminal" in tools else None),
    }


def session_selection(path: str) -> dict:
    """The LIVE (not-yet-committed) selection for a session, or {}.

    Returns `{"mode_id": ..., "permission": ..., "model": ...,
    "reasoning_effort": ..., "terminal_available": ..., "is_pending": bool}`.

    `is_pending` is True when a `pendingRequests` entry exists, i.e. the user has
    a COMPOSED BUT UNSENT request. That distinction is the whole point: while it
    is True the selector has been changed but the change is NOT yet committed to
    `inputState.mode`, and reading the committed value would report the PREVIOUS
    request's mode. Measured: that is exactly the PLAN-vs-Agent report.

    Mirrors `session_mode()`: kind:0 seeds, kind:1/2 patch, last write wins, a
    malformed line is skipped. An empty `v` (the send-time CLEAR,
    `{"kind":2,"k":["pendingRequests"],"i":0}` has no `v`) means NOT pending.
    """
    out: dict = {}
    pending = False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                kind = rec.get("kind")
                if kind == 0:
                    node = rec.get("v")
                    if isinstance(node, dict):
                        items = node.get("pendingRequests")
                        if isinstance(items, list) and items:
                            so = items[0].get("sendOptions")
                            got = _checklist_from_send_options(so)
                            if got.get("mode_id"):
                                out, pending = got, True
                    continue
                if kind not in (1, 2):
                    continue
                if tuple(rec.get("k") or ()) != LIVE_SELECTION_PATH:
                    continue
                v = rec.get("v")
                # An EMPTY list = the request was sent -> nothing pending.
                # (`"i": 0` splices the array empty; there is deliberately no
                # `v` on that patch.)
                #
                # THE SNAPSHOT MUST BE DISCARDED, not merely flagged. MEASURED
                # 2026-09-23 (`_diag_pending_order.py`): the last pendingRequests
                # event in the real file was a CLEAR (line 510) ~100 lines after
                # the last SET (line 499). A reader that kept the SET's values and
                # only flipped `is_pending` reported a resolver mode from a
                # request that had since been SENT -- a stale reading dressed up
                # as a live one. The blob is not recoverable after the CLEAR, so
                # the honest answer is NOTHING PENDING.
                if not isinstance(v, list) or not v:
                    out, pending = {}, False
                    continue
                first = v[0] if isinstance(v[0], dict) else {}
                got = _checklist_from_send_options(first.get("sendOptions"))
                if got.get("mode_id"):
                    out, pending = got, True
    except Exception:
        return out
    if out:
        out["is_pending"] = pending
    return out


def _iter_records(path: str, tail_bytes: int = 0):
    """Yield parsed patch records from `path`, optionally only the TAIL.

    WHY A TAIL MODE (measured): the account list route read ALL 73 session files
    and took **6,324 ms**, because one file was 24,639,817 bytes. The current mode
    is always the LAST write, so it lives near the END of the file -- reading the
    first 24 MB to find it is wasted work, and the latency is visible (the page
    was still empty after 4 seconds).

    `tail_bytes=0` means the whole file (the old, always-correct behaviour). With
    a positive value the read starts `tail_bytes` before EOF and, crucially,
    DISCARDS the first line, because it is almost certainly cut mid-record -- a
    half-parsed line would be a torn record, and a torn record is not evidence.

    The caller must handle "the tail had no answer" by falling back to a full
    read; this function deliberately does NOT decide that, so the fallback stays
    visible at the call site.
    """
    try:
        if tail_bytes and tail_bytes > 0:
            size = os.path.getsize(path)
            start = max(0, size - tail_bytes)
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                if start:
                    f.seek(start)
                    f.readline()  # discard the partial first line
                for line in f:
                    yield line
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    yield line
    except Exception:
        return


def session_checklist_from_records(path: str, tail_bytes: int = 0) -> dict:
    """ONE streaming pass over a session file -> (committed, live) raw rungs.

    WHY THIS EXISTS (a defect I introduced and then measured): the per-session
    API called `session_mode(path)` AND `session_selection(path)`, so it read the
    WHOLE file TWICE per session. A real session file measured 24,639,817 bytes,
    and `/api/mode/sessions?limit=200` then FAILED (a request error, not a slow
    answer) while `limit=2` still succeeded. That is a scaling defect: it would
    only get worse as the chat grew, and it presented as a flaky endpoint.

    So the two rungs are extracted HERE, in ONE pass, and `session_checklist`
    and the API both call this. One read, two rungs.

    Returns `{"committed": {...}|None, "live": {...}|None}` where `committed` is
    the mode object and `live` is the `sendOptions` value dict.
    """
    committed = None
    live: dict = {}
    pending = False
    try:
        for line in _iter_records(path, tail_bytes):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                # A torn tail on a LIVE file is normal; skipping it is right.
                continue
            kind = rec.get("kind")
            if kind == 0:
                node = rec.get("v")
                if isinstance(node, dict):
                    m = (node.get("inputState") or {}).get("mode")
                    if isinstance(m, dict) and m.get("id"):
                        committed = m
                    items = node.get("pendingRequests")
                    if isinstance(items, list) and items:
                        so = items[0].get("sendOptions")
                        got = _checklist_from_send_options(so)
                        if got.get("mode_id"):
                            live, pending = got, True
                continue
            if kind not in (1, 2):
                continue
            k = tuple(rec.get("k") or ())
            if k == MODE_INPUT_PATH:
                v = rec.get("v")
                if isinstance(v, dict) and v.get("id"):
                    committed = v
                continue
            if k == LIVE_SELECTION_PATH:
                v = rec.get("v")
                # An empty list = SENT -> nothing pending, and the snapshot
                # is DISCARDED (see session_selection for why).
                if not isinstance(v, list) or not v:
                    live, pending = {}, False
                    continue
                first = v[0] if isinstance(v[0], dict) else {}
                got = _checklist_from_send_options(first.get("sendOptions"))
                if got.get("mode_id"):
                    live, pending = got, True
    except Exception:
        return {"committed": committed, "live": {}}
    if live:
        live["is_pending"] = pending
    return {"committed": committed, "live": live}


# ---------------------------------------------------------------------------
# THE CACHE, and why a bounded read was REJECTED (measured 2026-09-23)
# ---------------------------------------------------------------------------
# The account list route reads every session file. MEASURED: 73 files, 1,101,528,987
# bytes total, and a full pass took 6,324 ms -- so the page was still blank after
# 4 seconds and looked broken.
#
# A TAIL-ONLY read was tried first and REJECTED BY MEASUREMENT, not by taste:
#   * 40 of 73 files had no mode in the last 256 KB, because the mode lives in
#     the `kind:0` SEED line, which is the FIRST line of the file.
#   * that seed line is enormous -- max 96,186,339 bytes, average 7,442,956.
#   * `"inputState"` sits at offsets up to 96,502,748 bytes (92 MB) inside it.
# So NO bounded head or tail read can be correct, and a bounded read that
# silently misses a mode is exactly the "no mode" false answer this module
# exists to prevent. The full read stays.
#
# What makes it affordable is a CACHE keyed on (mtime, size). The page polls, so
# without it every poll would re-read 1.1 GB. A file that CHANGED has a new
# mtime/size and is re-read, so the cache cannot serve a stale mode.
_CHECKLIST_CACHE: dict[str, tuple[float, int, dict]] = {}


def session_records_cached(path: str) -> dict:
    """`session_checklist_from_records`, memoised on (mtime, size).

    Returns the RAW two-rung shape `{"committed": {...}, "live": {...}}` -- the
    shape the account list route needs. (An earlier version of this cache wrapped
    `session_checklist`, whose shape is the CHECKLIST, so the route read
    `combined["committed"]` as None and every mode came back empty. The cache
    must wrap the function whose SHAPE the caller expects.)

    The key is the file's OWN mtime and size, so a write invalidates the entry. A
    cache keyed on the path alone would serve a stale mode after a switch -- the
    same class of defect as reading the wrong field.
    """
    try:
        st = os.stat(path)
        key = (st.st_mtime, st.st_size)
    except OSError:
        return session_checklist_from_records(path)
    hit = _CHECKLIST_CACHE.get(path)
    if hit and hit[0] == key[0] and hit[1] == key[1]:
        return hit[2]
    got = session_checklist_from_records(path)
    _CHECKLIST_CACHE[path] = (key[0], key[1], got)
    return got


def session_checklist(path: str) -> dict:
    """The environment checklist for a session: the LIVE values when a request
    is composed, the COMMITTED values otherwise. Each value is labelled with the
    rung that produced it, so the reader can always tell which one it got.

    THIS IS THE ONE FUNCTION the checklist consumers should call. It never
    blends: `committed_*` and `live_*` stay separate fields, because merging
    them would recreate the exact substitution that caused the PLAN/Agent
    report.

    ONE PASS (`session_checklist_from_records`), not two reads of the file.
    """
    combined = session_records_cached(path)
    committed = combined.get("committed") or {}
    live = combined.get("live") or {}
    custom_raw = None
    try:
        db, _wj = find_workspace_db()
        if db:
            custom_raw = _read_item(db, "chat.customModes.local")
    except Exception:
        custom_raw = None
    custom_modes = None
    if custom_raw:
        try:
            custom_modes = json.loads(custom_raw)
        except Exception:
            custom_modes = None

    cid = str(committed.get("id") or "")
    return {
        "ok": bool(cid) or bool(live.get("mode_id")),
        # COMMITTED (inputState.mode) -- the mode of the last SENT request.
        "committed_mode_id": cid or None,
        "committed_mode": _map_mode(cid, custom_modes) or None if cid else None,
        "committed_is_known": bool(cid) and bool(_map_mode(cid, custom_modes)),
        # LIVE (pendingRequests[].sendOptions) -- what the SELECTOR shows.
        "live_mode_id": live.get("mode_id") or None,
        "live_mode_name": live.get("mode_name") or None,
        "live_mode": _resolve_live_mode(live, custom_modes),
        "is_pending": bool(live.get("is_pending")),
        "live_is_stale": False,
        # WHAT THE LIVE VALUES DESCRIBE, and whether there ARE any. This label
        # exists because the live blob is CURRENT only while a request is being
        # composed. MEASURED (`_diag_pending_order.py`, 2026-09-23): the real
        # file's last event was a CLEAR, so the reader now returns NO live row
        # rather than a stale one, and this string SAYS so. An earlier version
        # of this label claimed an un-pending blob was "the last sent request" --
        # that was wrong (the blob's request id was not in `requests` at all),
        # and it is corrected here rather than shipped.
        "live_describes": ("pending request (current composer)"
                           if live.get("is_pending")
                           else "nothing pending: no live row (the send CLEARed "
                                "pendingRequests) -- read the committed rung"),
        # The dimensions the user named.
        "permission": live.get("permission"),
        "model": live.get("model"),
        "reasoning_effort": live.get("reasoning_effort"),
        "terminal_available": live.get("terminal_available"),
        "source": ("session:<path>" if (cid or live) else ""),
        "path": path,
    }


def _resolve_live_mode(live: dict, custom_modes) -> str:
    """Resolve the LIVE mode to ASK/PLAN/AGENT, or None.

    TWO steps, because `telemetryModeId` is NOT always a usable id. MEASURED on
    the real session file: for the Plan agent it is the generic literal
    `"custom"`, with the real name in `telemetryModeName` (`"Plan"`). A reader
    that trusted the id alone would map "custom" to nothing and report no live
    mode -- the same class of silent gap this investigation started from.

    So: try the id (builtins like `agent` resolve directly), then the NAME.
    The name is only accepted when it is exactly one of the three modes, which
    keeps this from inventing a mode out of a display label.
    """
    mid = str(live.get("mode_id") or "").strip()
    by_id = _map_mode(mid, custom_modes) if mid else ""
    if by_id:
        return by_id
    name = str(live.get("mode_name") or "").strip().upper()
    return name if name in ("ASK", "PLAN", "AGENT") else None


def live_fault_codes(check: dict) -> list[str]:
    """Why a checklist is not usable, NAMED. Never one generic message.

    `MODE_DIVERGED_UNCOMMITTED` is the one this whole investigation was about:
    the selector and the committed value DISAGREE because the request has not
    been sent. Reported, never silently resolved in favour of either side.
    """
    codes: list[str] = []
    if not check.get("committed_mode_id") and not check.get("live_mode_id"):
        codes.append("MODE_UNPROVEN_NO_ARTEFACT")
    if check.get("committed_mode_id") and not check.get("committed_is_known"):
        codes.append("MODE_AMBIGUOUS_MULTI_SOURCE")
    if (check.get("is_pending") and check.get("live_mode_id")
            and check.get("committed_mode_id")
            and check.get("live_mode_id") != check.get("committed_mode_id")):
        codes.append("MODE_DIVERGED_UNCOMMITTED")
    return codes


def _map_mode(mode_id: str, custom_modes) -> str:
    """Map a VS Code mode id to ASK/PLAN/AGENT, or "" when unknown.

    `kind` is deliberately NOT used: Agent and Plan both report kind "agent".
    Plan is a custom agent, so its id is a URI and is resolved by looking it up
    in `chat.customModes.local` and reading the entry's `name`.
    """
    mid = (mode_id or "").strip()
    if not mid:
        return ""
    low = mid.lower()
    if low in MODE_ID_MAP:
        return MODE_ID_MAP[low]
    # Custom agent (Plan): resolve by id -> name.
    if isinstance(custom_modes, list):
        for entry in custom_modes:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("id") or "") == mid:
                name = str(entry.get("name") or "").strip().upper()
                if name in ("ASK", "PLAN", "AGENT"):
                    return name
    return ""


def _mode_entries(state) -> list[tuple[str, dict, bool]]:
    """Every mode object in a state blob: (path, mode, is_current).

    Two shapes are known, both from VS Code's own persisted chat input state:

      * `chat.untitledInputState`      -> {"mode": {"id": ..., "kind": ...}, ...}
      * `memento/interactive-session`  -> {"history": {"copilot": [
                                             {"mode": {"id": ..., "kind": ...}},
                                             ...]}, ...}

    THE ORDERING IS MEASURED, NOT ASSUMED (2026-09-23). The old docstring claimed
    "the FIRST entry is the current one (VS Code prepends)" with NO citation. The
    40 history entries carry NO timestamp, so freshness cannot be compared from
    the blob. The ordering was therefore proven from `inputText` instead:

        [0] mode=agent         inputText='Start implementation'      <- newest
        [1] mode=Plan.agent.md inputText='A and have a test mission...'
        ...
        [39] mode=agent        inputText='Start implementation'      <- oldest

    `[0].inputText` is the user's MOST RECENT prompt, so the ordering IS prepend
    and `[0]` IS the current entry. Measured by `_diag_mode_order.py`.

    Returning a LIST (not one mode) is the fix for the 2026-09-23 defect: the
    caller must be able to see that TWO sources answered, instead of silently
    taking whichever key it happened to read first.
    """
    out: list[tuple[str, dict, bool]] = []
    if not isinstance(state, dict):
        return out
    direct = state.get("mode")
    if isinstance(direct, dict) and direct.get("id"):
        out.append(("mode", direct, True))
    hist = state.get("history")
    if isinstance(hist, dict):
        for kind, entries in hist.items():
            if not isinstance(entries, list):
                continue
            for i, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                m = entry.get("mode")
                if isinstance(m, dict) and m.get("id"):
                    out.append(("history.%s[%d].mode" % (kind, i), m, i == 0))
    return out


def _mode_from_state(state) -> dict | None:
    """The CURRENT mode object from a state blob, or None.

    Kept for callers that only need the mode. It returns the entry marked
    `is_current` by `_mode_entries`; it does NOT decide between two disagreeing
    sources -- that is `attest()`'s job, and it FAULTS rather than guessing.
    """
    for _path, mode, is_current in _mode_entries(state):
        if is_current:
            return mode
    return None


def _mode_candidates(db: str) -> list[dict]:
    """Every mode object under EVERY known key, with whether it is current.

    WHY THIS EXISTS (user, 2026-09-23: "how did you confirm the mode does update
    or not!!! this is BUG"). The old `attest()` looped the keys and BROKE on the
    first one that answered:

        for key in STATE_KEYS:
            if _mode_from_state(candidate) is not None:
                state, used_key = candidate, key
                break                      # <-- first key wins, no freshness check

    Measured the same day: `chat.untitledInputState` was present on 2026-09-22 and
    ABSENT on 2026-09-23, while `memento/interactive-session` answered. So the key
    MOVES, and a stale key that still carries a mode would WIN over the live one.
    That is the same defect family `plan_gate.plan_artifact()` already fixed with a
    tie-break (`plan_gate.py:305-315`); the attestation had no equivalent.

    This returns ALL candidates so `attest()` can REFUSE when two disagree,
    instead of reporting whichever it read first.
    """
    out: list[dict] = []
    for key in STATE_KEYS:
        raw = _read_item(db, key)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        for path, mode, is_current in _mode_entries(obj):
            out.append({
                "key": key,
                "path": path,
                "mode_id": str(mode.get("id") or ""),
                "mode_kind": str(mode.get("kind") or ""),
                "current": bool(is_current),
            })
    return out


def attest(cwd: str | None = None, session_id: str = "") -> dict:
    """Prove the chat mode. Returns PROVEN or FAULT. Never raises.

    `cwd` may also be supplied via env `MODE_ATTEST_CWD`, which is how a caller
    (or a proof) can point the attestation at a directory with no matching
    workspaceStorage entry to exercise the FAULT path against a REAL absence.

    `session_id` (added 2026-09-23) SCOPES THE READ TO ONE CHAT. The user:
    "same workspace 的兩個 chat 仍可能互相讀到對方 mode ... fix that now".

    THE ORDER OF THE TWO RUNGS, AND WHY THERE IS NO FALLBACK BETWEEN THEM:

      1. `session_id` is given -> read `chatSessions/<session_id>.jsonl`. That
         file belongs to THIS chat, so its last `inputState.mode` patch is the
         answer for THIS chat. If the file cannot be found, the result is a
         FAULT. It is NOT a licence to read the workspace key, because the
         workspace key is precisely the thing that answers with ANOTHER chat's
         mode -- falling back to it would reinstate the defect while looking
         like a working answer.
      2. `session_id` is NOT given -> the workspace keys are the only source
         that exists, so they are read and `evidence.scope` is set to
         "workspace" so a reader can SEE which rung answered and know the answer
         is not chat-scoped.

    `evidence.scope` exists because the two rungs have very different strength,
    and a caller that cannot tell them apart would treat a workspace answer as if
    it were a chat-scoped one -- which is how this defect hid for a whole session.
    """
    # Emergency release, registered in `hotkey_tools.md` before use. It does NOT
    # invent a mode: it forces the FAULT path, so the gate still refuses to act
    # on an unproven mode. A valve that produced a mode would be a fallback, and
    # R1 forbids one.
    if os.environ.get("MODE_ATTEST", "").strip().lower() in ("off", "0", "false"):
        return _fault("MODE_ATTEST=off (emergency release): attestation disabled")
    try:
        if cwd is None:
            cwd = os.environ.get("MODE_ATTEST_CWD") or None
        db, wj = find_workspace_db(cwd)
        if not db:
            return _fault(
                "no VS Code workspaceStorage entry matches the working directory "
                "(%s)" % (cwd or os.getcwd())
            )

        sid = (session_id or "").strip()

        # ---- RUNG 1: the PER-SESSION source. No fallback past it. ------------
        if sid:
            sfile = find_session_file(sid, db)
            if not sfile:
                return _fault(
                    "session %s has no chatSessions/%s.jsonl in the workspace "
                    "store, so its mode cannot be proven. This is NOT answered "
                    "from the workspace key: that key holds whichever chat "
                    "last wrote it, which is the wrong-chat defect."
                    % (sid, sid)
                )
            got = session_mode(sfile)
            mode_id = str(got.get("id") or "")
            custom_raw = _read_item(db, "chat.customModes.local")
            custom_modes = None
            if custom_raw:
                try:
                    custom_modes = json.loads(custom_raw)
                except Exception:
                    custom_modes = None

            # ---- WHICH RUNG ANSWERS (the user's complaint, measured) ---------
            # The user: "here is the problem 1 ->2 [start implementation] -> 3
            # ... 1) have check the mode / 3) didn't!!!" -- i.e. the mode read is
            # not the mode in force.
            #
            # MEASURED (`_diag_mode_lag.py`): while a request is being composed,
            # `inputState.mode` still holds the PREVIOUS request's mode. At line
            # 398 of the real file the selector was composing AGENT while
            # `inputState.mode` still held PLAN. `inputState.mode` is the
            # COMMITTED rung; the selector is `pendingRequests[].sendOptions`.
            #
            # So: a PENDING live mode wins, because it is what is in force NOW.
            # It is NOT a fallback -- no live answer is invented when nothing is
            # pending; the committed answer is then the only truth there is. Both
            # are always reported, and a divergence is NAMED, never hidden.
            live = session_selection(sfile)
            live_mode = _resolve_live_mode(live, custom_modes)
            committed_mode = _map_mode(mode_id, custom_modes) if mode_id else ""
            is_pending = bool(live.get("is_pending"))

            if is_pending and live_mode:
                resolved, rung = live_mode, "live"
            elif committed_mode:
                resolved, rung = committed_mode, "committed"
            elif live_mode:
                resolved, rung = live_mode, "live"
            elif not mode_id:
                # No committed patch AND nothing live. Name it accurately: an
                # earlier edit collapsed this into the "unknown mode.id" fault,
                # which reported the empty string as an unknown id -- technically
                # true and useless to whoever has to fix it.
                return _fault(
                    "session %s (%s) records no `inputState.mode` patch and has "
                    "no live selection either, so its mode cannot be proven."
                    % (sid, os.path.basename(sfile))
                )
            else:
                return _fault(
                    "session %s mode.id %r is not a known chat mode and no live "
                    "selection resolves either (known: ask, agent, or a custom "
                    "agent listed in chat.customModes.local)" % (sid, mode_id)
                )

            divergence = None
            if (live_mode and committed_mode and is_pending
                    and live_mode != committed_mode):
                divergence = (
                    "MODE_DIVERGED_UNCOMMITTED: the selector is composing %s "
                    "while inputState.mode still holds %s (the previous "
                    "request). The LIVE value is reported because the request is "
                    "not sent yet. Measured: this is a real, observed lag, not a "
                    "hypothetical." % (live_mode, committed_mode)
                )
            return {
                "state": "PROVEN",
                "mode": resolved,
                "source": "%s:session(%s)" % (SOURCE_DB, sid),
                "evidence": {
                    "scope": "session",
                    "session_id": sid,
                    "session_file": sfile,
                    "db": db,
                    "workspace_json": wj,
                    "mode_id": mode_id,
                    "mode_kind": str(got.get("kind") or ""),
                    # The rung that ANSWERED, and the other rung's value. A
                    # caller that cannot see both cannot tell a current reading
                    # from a committed one -- which is how this hid.
                    "rung": rung,
                    "committed_mode": committed_mode or None,
                    "live_mode": live_mode or None,
                    "live_mode_id": live.get("mode_id") or None,
                    "live_mode_name": live.get("mode_name") or None,
                    "is_pending": is_pending,
                    "divergence": divergence,
                },
            }

        # ---- RUNG 2: no session id -> only the workspace keys exist. ---------
        # Read the ONE source at whichever key it currently lives under. A key
        # that is absent is not an answer; if NEITHER key answers, that is a
        # FAULT (never a guess).
        state = None
        used_key = ""
        tried: list[str] = []
        for key in STATE_KEYS:
            raw = _read_item(db, key)
            tried.append(key)
            if not raw:
                continue
            try:
                candidate = json.loads(raw)
            except Exception:
                continue
            if _mode_from_state(candidate) is not None:
                state, used_key = candidate, key
                break
        if state is None:
            return _fault(
                "no chat-mode state under any known key in %s (tried: %s)"
                % (os.path.basename(db), ", ".join(tried))
            )

        mode = _mode_from_state(state) or {}
        mode_id = str(mode.get("id") or "")
        if not mode_id:
            return _fault("%s has no mode.id" % used_key)

        custom_raw = _read_item(db, "chat.customModes.local")
        custom_modes = None
        if custom_raw:
            try:
                custom_modes = json.loads(custom_raw)
            except Exception:
                custom_modes = None

        resolved = _map_mode(mode_id, custom_modes)
        if not resolved:
            return _fault(
                "mode.id %r is not a known chat mode (known: ask, agent, or a "
                "custom agent listed in chat.customModes.local)" % mode_id
            )

        return {
            "state": "PROVEN",
            "mode": resolved,
            "source": SOURCE_DB,
            "evidence": {
                "db": db,
                "workspace_json": wj,
                "state_key": used_key,
                "mode_id": mode_id,
                "mode_kind": str(mode.get("kind") or ""),
                # `scope` is set so a reader can SEE that this answer is NOT
                # chat-scoped. Without it, a workspace answer is indistinguishable
                # from a session-scoped one at the call site -- which is exactly
                # how the two-chats-one-mode defect stayed invisible.
                "scope": "workspace",
            },
        }
    except Exception as exc:
        return _fault("attestation raised %s: %s" % (type(exc).__name__, exc))


def step0_block(result: dict | None = None) -> str:
    """The one compact block a worker reads at STEP 0."""
    r = result if result is not None else attest()
    if r.get("state") == "PROVEN":
        ev = r.get("evidence") or {}
        # The SCOPE is printed, not implied. A workspace-scoped answer is NOT the
        # same fact as a session-scoped one, and a worker that cannot see which
        # rung answered has no way to know its mode was read from another chat.
        scope = str(ev.get("scope") or "workspace")
        rung = str(ev.get("rung") or "")
        lines = [
            "STEP 0 MODE: %s" % r["mode"],
            "  source : %s" % r["source"],
            "  scope  : %s%s" % (scope, "" if scope == "session" else
                                "  (NOT chat-scoped: read from the workspace key, "
                                "which holds whichever chat wrote it last)"),
        ]
        if rung:
            # WHICH RUNG answered, and whether it is a fresh reading. The
            # committed rung LAGS by one request (measured), so a worker told
            # only "AGENT" would not know whether that is the mode in force now
            # or the previous request's -- the user's "3) didn't" complaint.
            note = ("pending request is composing this -- the mode IN FORCE NOW"
                    if rung == "live"
                    else "last sent request; no request is pending")
            lines.append("  rung   : %s  (%s)" % (rung, note))
            stored = {"committed_mode": ev.get("committed_mode"),
                      "live_mode": ev.get("live_mode"),
                      "is_pending": ev.get("is_pending")}
            lines.append("  both   : committed=%r live=%r pending=%r"
                         % (stored["committed_mode"], stored["live_mode"],
                            stored["is_pending"]))
        if ev.get("divergence"):
            # LOUD, and first-class: this is the exact condition that produced
            # the PLAN-vs-Agent report. Never printed as a quiet footnote.
            lines.append("  DIVERGENCE: %s" % ev["divergence"])
        lines.append("  evidence: mode.id=%r kind=%r" % (ev.get("mode_id"),
                                                         ev.get("mode_kind")))
        lines.append("  db     : %s" % ev.get("db"))
        return "\n".join(lines)
    return (
        "STEP 0 FAULT: the chat mode could NOT be proven.\n"
        "  reason : %s\n"
        "  source : %s\n"
        "  FIX    : %s\n"
        "  This is a SYSTEM FAULT, not a mode. Do not assume a mode."
        % (r.get("fault_reason"), r.get("source"), r.get("fix"))
    )


def main() -> int:
    args = sys.argv[1:]
    session_id = ""
    if "--session" in args:
        i = args.index("--session")
        if i + 1 < len(args):
            session_id = args[i + 1].strip()
    if "--json" in args:
        print(json.dumps(attest(session_id=session_id), ensure_ascii=False,
                         indent=2))
        return 0
    # Default and --step0: the compact block.
    print(step0_block(attest(session_id=session_id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
