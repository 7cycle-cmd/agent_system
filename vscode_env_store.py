# -*- coding: utf-8 -*-
"""vscode_env_store.py — the per-CONVERSATION environment checklist.

WHAT THIS IS
------------
The user (2026-09-23):

    "conversation id is help us to have environment checklist for vscode
     example : mode = plan, permission = autopilot...."

A conversation IS a VS Code chat session. This module stores, per conversation,
what the environment WAS at each observation: the mode (both rungs), the
permission level, the model, and every other dimension worth checking.

WHY THE NAME IS `vscode_env_*` AND NOT `conversation_env_*`
----------------------------------------------------------
The user (2026-09-23):

    "too easy to have name mis-understand problem, you need to register at
     terminology_registry!!!"
    "chat system is chat system, vscode > chat > conseraction is another system"

The bare word `conversation` reads as the CHAT system, which is a DIFFERENT table
set (`chat_center_message`, `chat_identity_log`, `chat_registry`, ...) that
happens to key on the same session id. The terms are registered in
`terminology_registry`: `vscode_conversation` and `chat_system` are SIBLINGS, so
the register itself stores that they are two systems.

WHY THERE IS NO `vscode_conversation` TABLE (the deliberate omission)
--------------------------------------------------------------------
`chat_main` ALREADY keys a conversation on the session id:

    CREATE TABLE chat_main (id INTEGER PK, session_id TEXT NOT NULL, ...,
                            UNIQUE (session_id))        db_schema.py:3612-3626

and `chat_identity_log`, `chat_registry`, `plan_sessions` (PK
`session_id,chat_id`) and `session_confirm_log` all already key on the same id.
The user's own warning about this repo is "they are totally different system" —
so adding a FOURTH identity for one concept would BE that defect. This module
therefore JOINS to `chat_main.id` (`vscode_env_log.vscode_conversation_id`) and
DELEGATES conversation creation to the existing writer:

    skill_library_api.register_chat_identity(session_id) -> chat_id

WHERE THE READING COMES FROM
----------------------------
PARSING lives in `scripts/mode_attest.py` (`session_checklist`) — the SAME reader
the PreToolUse gate uses, so the checklist and the gate can never disagree about
a mode. STORING lives here. This module does not parse a session file itself.

THE ONE RULE THAT MAKES THIS USEFUL
-----------------------------------
`mode` is stored as TWO dimensions plus a rung, NEVER one merged value:

    mode_committed   inputState.mode  — the mode of the last SENT request
    mode_live        the selector     — pendingRequests[].sendOptions
    mode_rung        which of the two answered

They disagree BY DESIGN. MEASURED (`_diag_mode_lag.py`): at one composition in
the real session file the selector was AGENT while `inputState.mode` still held
PLAN. Merging them into "the mode" is exactly the bug that took a whole session
to find. So the CSV keeps them apart and `checklist()` returns them apart.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from typing import Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import db_schema  # noqa: E402

DB_PATH = os.path.join(BASE_DIR, "agent.db")

# The legal `measure_kind` values. A FIXED set -> a Python tuple (repo doctrine);
# an OPEN set would be a table. Validated at the WRITE SITE so a typo cannot
# invent a measurement strategy.
MEASURE_KINDS = ("direct", "delegate")

# The legal `status` values. 'unknown' is REAL and is never a silent 'ok'.
STATUSES = ("ok", "fault", "unknown")


# ---------------------------------------------------------------------------
# The dimensions, seeded from MEASURED fields (each carries its evidence)
# ---------------------------------------------------------------------------
# `why` is not decoration: `citation_discipline` requires a finding to carry a
# checkable reference, and for a dimension the reference IS the field it reads.
#
# `direct` dimensions are read from `pendingRequests[].sendOptions`, whose shape
# was measured in the real session file:
#   modeInfo.telemetryModeId / telemetryModeName / permissionLevel
#   userSelectedModelId
#   userSelectedModelConfiguration.reasoningEffort
#   userSelectedTools.run_in_terminal
#
# `delegate` dimensions are answered by `f_env_preflight.run_all(only, scope)`
# (`f_env_preflight.py:472`). They are DELEGATED, not re-implemented — the same
# doctrine the task-queue preflight panel already follows.
DIMENSIONS: tuple[dict[str, Any], ...] = (
    {
        "dim_key": "mode",
        "display_name": "Mode (in force)",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 1,
        "sort_order": 9,
        "why": "The mode IN FORCE, chosen by the rung — the answer to the user's "
               "'mode = plan'. It is NOT a merge: mode_committed and mode_live "
               "are stored separately beside it, and mode_rung says which of "
               "them this value came from.",
        "cite_ref": "scripts/mode_attest.py attest() return value",
    },
    {
        "dim_key": "mode_committed",
        "display_name": "Mode (committed)",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 1,
        "sort_order": 10,
        "why": "inputState.mode is the mode of the LAST SENT request. It LAGS "
               "the selector by one request while a message is being composed.",
        "cite_ref": "scripts/mode_attest.py session_mode; _diag_mode_lag.py",
    },
    {
        "dim_key": "mode_live",
        "display_name": "Mode (live selector)",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 1,
        "sort_order": 11,
        "why": "pendingRequests[].sendOptions.modeInfo is what the selector shows "
               "NOW. For a custom agent telemetryModeId is the literal 'custom', "
               "so the NAME is the fallback.",
        "cite_ref": "scripts/mode_attest.py _resolve_live_mode",
    },
    {
        "dim_key": "mode_rung",
        "display_name": "Mode (which rung answered)",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 0,
        "sort_order": 12,
        "why": "Stores 'live' or 'committed' so a reader cannot mistake the "
               "committed rung for the mode in force.",
        "cite_ref": "scripts/mode_attest.py attest() evidence.rung",
    },
    {
        "dim_key": "mode_divergence",
        "display_name": "Mode divergence",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 0,
        "sort_order": 13,
        "why": "Present ONLY when the selector and inputState.mode disagree. If "
               "the mismatch were silently resolved, the evidence that the two "
               "rungs are different facts would be destroyed.",
        "cite_ref": "scripts/mode_attest.py live_fault_codes",
    },
    {
        "dim_key": "permission",
        "display_name": "Permission",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 1,
        "sort_order": 20,
        "why": "The user's own example: 'permission = autopilot'. Read from "
               "sendOptions.modeInfo.permissionLevel; MEASURED value 'autopilot'.",
        "cite_ref": "pendingRequests[].sendOptions.modeInfo.permissionLevel",
    },
    {
        "dim_key": "model",
        "display_name": "Model",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 0,
        "sort_order": 21,
        "why": "sendOptions.userSelectedModelId. MEASURED: "
               "openrouter/OpenRouter/deepseek/deepseek-v4.1-flash.",
        "cite_ref": "pendingRequests[].sendOptions.userSelectedModelId",
    },
    {
        "dim_key": "reasoning_effort",
        "display_name": "Reasoning effort",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 0,
        "sort_order": 22,
        "why": "sendOptions.userSelectedModelConfiguration.reasoningEffort.",
        "cite_ref": "pendingRequests[].sendOptions."
                    "userSelectedModelConfiguration.reasoningEffort",
    },
    {
        "dim_key": "terminal_available",
        "display_name": "Terminal available",
        "measure_kind": "direct",
        "delegate_ref": "",
        "is_required": 0,
        "sort_order": 23,
        "why": "sendOptions.userSelectedTools.run_in_terminal. A real BOOL, so "
               "'false' stays distinct from 'field absent'. MEASURED: it read "
               "false while the terminal worked, because the value came from an "
               "older request -- hence evidence_at vs observed_at.",
        "cite_ref": "pendingRequests[].sendOptions.userSelectedTools",
    },
    {
        "dim_key": "env_preflight",
        "display_name": "Environment preflight",
        "measure_kind": "delegate",
        "delegate_ref": "f_env_preflight.run_all",
        "is_required": 1,
        "sort_order": 40,
        "why": "The existing gate. DELEGATED on purpose: re-implementing a check "
               "would give the checklist a second opinion about the same fact.",
        "cite_ref": "f_env_preflight.py:472 run_all(only, scope)",
    },
    {
        "dim_key": "preflight",
        "display_name": "Preflight (delegated)",
        "measure_kind": "delegate",
        "delegate_ref": "f_env_preflight.run_all",
        "is_required": 0,
        "sort_order": 41,
        "why": "The DELEGATED result reader used by record_preflight(); kept "
               "separate from 'env_preflight' so a caller may record a scoped "
               "run without overwriting the all-scope reading.",
        "cite_ref": "f_env_preflight.py:472 run_all(only, scope)",
    },
)


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a connection with FOREIGN KEYS ENFORCED.

    `PRAGMA foreign_keys = ON` IS NOT OPTIONAL. SQLite defaults it OFF per
    connection, so without this line every `REFERENCES` clause in the DDL is
    DECORATION -- MEASURED: `append_env(999999, ...)` was ACCEPTED against a
    non-existent `chat_main` row until this was added. A constraint that is not
    enforced is worse than none, because the schema then CLAIMS a guarantee it
    does not provide.
    """
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _conn() -> sqlite3.Connection:
    """The default connection, with the env var override used by proofs.

    WHY AN OVERRIDE: a proof must exercise a REAL table on a TEMP db, and
    pointing the module at `agent.db` would write test rows into product data.
    """
    return _connect(os.environ.get("VSCODE_ENV_DB_PATH") or DB_PATH)


def ensure_schema(db_path: str | None = None) -> dict[str, Any]:
    """Create the two tables. Idempotent. Delegates to `db_schema`.

    The DDL is NOT re-typed here: `db_schema` owns it and registers it in its
    drift-locked loop. A second copy of a CREATE in this file is how the repo
    previously resurrected a dropped table.
    """
    try:
        conn = _connect(db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            # executescript, not execute: each DDL string holds several
            # statements (CREATE TABLE + its CREATE INDEX lines), and `execute`
            # raises "You can only execute one statement at a time".
            conn.executescript(db_schema.VSCODE_ENV_DIMENSION_DDL)
            conn.executescript(db_schema.VSCODE_ENV_LOG_DDL)
            conn.commit()
        finally:
            conn.close()
        return {"ok": True, "db": db_path or DB_PATH}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}


def seed_dimensions(db_path: str | None = None) -> dict[str, Any]:
    """UPSERT the dimension register. A human edit is never blanked.

    UPSERT (not INSERT OR IGNORE) because a corrected `why`/`cite_ref` must be
    able to land — the same fix `mode_registry.seed_modes()` needed.
    """
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        n = 0
        for d in DIMENSIONS:
            if d["measure_kind"] not in MEASURE_KINDS:
                return {"ok": False,
                        "error": "dim %s has measure_kind %r, not one of %s"
                                 % (d["dim_key"], d["measure_kind"],
                                    list(MEASURE_KINDS))}
            conn.execute(
                """
                INSERT INTO vscode_env_dimension
                    (dim_key, display_name, measure_kind, delegate_ref,
                     is_required, sort_order, why, cite_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (dim_key) DO UPDATE SET
                    display_name = excluded.display_name,
                    measure_kind = excluded.measure_kind,
                    delegate_ref = excluded.delegate_ref,
                    is_required  = excluded.is_required,
                    sort_order   = excluded.sort_order,
                    why          = excluded.why,
                    cite_ref     = excluded.cite_ref,
                    updated_at   = CURRENT_TIMESTAMP
                """,
                (d["dim_key"], d["display_name"], d["measure_kind"],
                 d["delegate_ref"], d["is_required"], d["sort_order"],
                 d["why"], d["cite_ref"]),
            )
            n += 1
        conn.commit()
        return {"ok": True, "seeded": n}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        conn.close()


def upsert_conversation(session_id: str, db_path: str | None = None,
                        ide: str | None = None,
                        llm: str | None = None) -> dict[str, Any]:
    """session_id -> conversation id (`chat_main.id`). DELEGATES.

    This does NOT write `chat_main` itself. `chat_main` has a declared write
    owner (`skill_library_api.register_chat_identity`, with its own Field
    Register + write-owner gate), and a second writer is how the old
    double-write bug happened. So this calls the owner and returns its id.

    The returned key is `vscode_conversation_id` — the REGISTERED term for the
    column that names a VS Code conversation. It was `chat_main_id`, which made
    a reader think the value belonged to the chat system.
    """
    sid = (session_id or "").strip()
    if not sid:
        return {"ok": False, "error_code": "MISSING_SESSION_ID",
                "error": "session_id is required"}
    try:
        import skill_library_api as sla
        got = sla.register_chat_identity(sid, source="vscode_env_store",
                                         ide=ide, llm=llm)
        if not got.get("ok"):
            return {"ok": False, "error_code": got.get("error_code")
                    or "CHAT_IDENTITY_FAILED",
                    "error": got.get("error"), "session_id": sid}
        return {"ok": True, "session_id": sid,
                "vscode_conversation_id": got.get("chat_id"),
                "created": bool(got.get("created"))}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "session_id": sid}


def append_env(vscode_conversation_id: int, dim_key: str, value_text: Any, *,
               status: str = "unknown", source: str = "unknown",
               fault_code: str = "", cite_ref: str = "",
               is_pending: bool = False, evidence_at: str | None = None,
               observed_at: str | None = None,
               db_path: str | None = None) -> dict[str, Any]:
    """Append ONE observation. Never updates an existing row.

    APPEND-ONLY, and the reason is not tidiness: the committed and live modes
    disagree, so the HISTORY is the datum. An UPDATE would overwrite the
    disagreement a reviewer opened the page to see.

    An unregistered `dim_key` is REFUSED (an FK would be the alternative, but a
    clear error names the fix). An unknown `status` is refused too — a status
    that is not one of the three would be an unreadable row.
    """
    if not isinstance(vscode_conversation_id, int) or vscode_conversation_id <= 0:
        return {"ok": False, "error_code": "INVALID_VSCODE_CONVERSATION_ID",
                "error": "vscode_conversation_id must be a positive INTEGER"}
    if status not in STATUSES:
        return {"ok": False, "error_code": "INVALID_STATUS",
                "error": "status %r is not one of %s"
                         % (status, list(STATUSES))}
    conn = _conn() if db_path is None else _connect(db_path)
    try:
        known = conn.execute(
            "SELECT 1 FROM vscode_env_dimension WHERE dim_key=?",
            (dim_key,),
        ).fetchone()
        if not known:
            return {"ok": False, "error_code": "UNKNOWN_DIMENSION",
                    "error": "dim_key %r is not registered; seed_dimensions() "
                             "first" % dim_key}
        # A value that is not a string is stored as JSON, so a bool stays a bool
        # and a number stays a number. `str(False)` would be "False" -- readable
        # by us, but a JSON consumer would then have to guess it back.
        stored = value_text if isinstance(value_text, str) else json.dumps(
            value_text)
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO vscode_env_log
                (vscode_conversation_id, dim_key, value_text, status, source,
                 fault_code, cite_ref, is_pending, evidence_at, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                    COALESCE(?, datetime('now')))
            """,
            (vscode_conversation_id, dim_key, stored, status, source,
             fault_code, cite_ref, 1 if is_pending else 0, evidence_at,
             observed_at),
        )
        conn.commit()
        # A DUPLICATE IS REPORTED, never swallowed. `INSERT OR IGNORE` keeps the
        # UNIQUE(vscode_conversation_id, dim_key, observed_at) guard honest, but a
        # silent drop looks exactly like a successful write -- and a caller that
        # recorded 8 dimensions in one second would lose the second batch
        # without a trace. So the caller is TOLD, and can retry with a new
        # `observed_at`.
        ignored = (cur.rowcount == 0)
        return {"ok": True, "vscode_conversation_id": vscode_conversation_id,
                "dim_key": dim_key, "status": status, "duplicate": ignored,
                "note": ("an observation for this (conversation, dim, "
                         "observed_at) already existed, so nothing was written"
                         if ignored else "")}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        conn.close()


def checklist(vscode_conversation_id: int,
              db_path: str | None = None) -> dict[str, Any]:
    """The LATEST value per dimension, with its status. Always returns `ok`.

    `missing` names every REQUIRED dimension with no observation, so an unmeasured
    requirement is visible rather than absent — a blank cell is what a reader
    mistakes for "no problem".
    """
    conn = _conn() if db_path is None else _connect(db_path)
    try:
        dims = conn.execute(
            "SELECT dim_key, display_name, measure_kind, delegate_ref, "
            "       is_required, sort_order, why, cite_ref "
            "  FROM vscode_env_dimension WHERE is_active=1 "
            " ORDER BY sort_order, dim_key"
        ).fetchall()
        out: list[dict[str, Any]] = []
        for (key, name, kind, ref, req, order, why, cite) in dims:
            row = conn.execute(
                "SELECT value_text, status, source, fault_code, is_pending, "
                "       evidence_at, observed_at "
                "  FROM vscode_env_log "
                " WHERE vscode_conversation_id=? AND dim_key=? "
                " ORDER BY observed_at DESC, log_id DESC LIMIT 1",
                (vscode_conversation_id, key),
            ).fetchone()
            if row:
                out.append({
                    "dim_key": key, "display_name": name,
                    "measure_kind": kind, "delegate_ref": ref,
                    "is_required": bool(req), "why": why, "cite_ref": cite,
                    "value_text": row[0], "status": row[1], "source": row[2],
                    "fault_code": row[3], "is_pending": bool(row[4]),
                    "evidence_at": row[5], "observed_at": row[6],
                    "measured": True,
                })
            else:
                out.append({
                    "dim_key": key, "display_name": name,
                    "measure_kind": kind, "delegate_ref": ref,
                    "is_required": bool(req), "why": why, "cite_ref": cite,
                    "value_text": None, "status": "unknown",
                    "source": "(no observation)", "fault_code": "",
                    "is_pending": False, "evidence_at": None,
                    "observed_at": None, "measured": False,
                })
        missing = [d["dim_key"] for d in out
                   if d["is_required"] and not d["measured"]]
        faults = [d["dim_key"] for d in out if d["status"] == "fault"]
        return {"ok": True, "vscode_conversation_id": vscode_conversation_id,
                "dimensions": out, "missing": missing, "faults": faults,
                "complete": not missing}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        conn.close()


def trace(vscode_conversation_id: int, dim_key: str = "",
          db_path: str | None = None, limit: int = 200) -> dict[str, Any]:
    """The full history for one dimension (or all), newest first."""
    conn = _conn() if db_path is None else _connect(db_path)
    try:
        lim = max(1, min(int(limit or 200), 2000))
        if dim_key:
            rows = conn.execute(
                "SELECT dim_key, value_text, status, source, fault_code, "
                "       cite_ref, is_pending, evidence_at, observed_at "
                "  FROM vscode_env_log "
                " WHERE vscode_conversation_id=? AND dim_key=? "
                " ORDER BY observed_at DESC, log_id DESC LIMIT ?",
                (vscode_conversation_id, dim_key, lim),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT dim_key, value_text, status, source, fault_code, "
                "       cite_ref, is_pending, evidence_at, observed_at "
                "  FROM vscode_env_log "
                " WHERE vscode_conversation_id=? "
                " ORDER BY observed_at DESC, log_id DESC LIMIT ?",
                (vscode_conversation_id, lim),
            ).fetchall()
        items = [{
            "dim_key": r[0], "value_text": r[1], "status": r[2],
            "source": r[3], "fault_code": r[4], "cite_ref": r[5],
            "is_pending": bool(r[6]), "evidence_at": r[7], "observed_at": r[8],
        } for r in rows]
        return {"ok": True, "vscode_conversation_id": vscode_conversation_id,
                "dim_key": dim_key, "items": items, "returned": len(items),
                "limit": lim}
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Recording from a checklist reading (the bridge to the mode SSOT)
# ---------------------------------------------------------------------------

def record_checklist(vscode_conversation_id: int, check: dict, *,
                     db_path: str | None = None,
                     evidence_at: str | None = None,
                     observed_at: str | None = None) -> dict[str, Any]:
    """Store the outcome of `mode_attest.session_checklist()`.

    `check` is the dict that reader returns. It is NOT re-parsed here: this
    function only MAPS it onto rows, so the gate and the checklist share ONE
    reading (a second parser is the defect this repo keeps recording).

    The two mode rungs are written as SEPARATE rows and the divergence is NOT
    resolved — if the selector and `inputState.mode` disagree, BOTH are stored
    and a `mode_divergence` fault is recorded. Picking a winner here would
    destroy the exact evidence the page exists to show.
    """
    written: list[str] = []
    duplicates: list[str] = []
    committed = check.get("committed_mode")
    live = check.get("live_mode")
    rung = "live" if (check.get("is_pending") and live) else "committed"

    def put(dim, value, status, source, fault="", pending=False):
        r = append_env(vscode_conversation_id, dim, value, status=status,
                       source=source, fault_code=fault, is_pending=pending,
                       db_path=db_path, evidence_at=evidence_at,
                       observed_at=observed_at)
        if r.get("ok"):
            written.append(dim)
        if r.get("duplicate"):
            duplicates.append(dim)
        return r

    put("mode_committed", committed,
        "ok" if committed else "unknown",
        "mode_attest.session_mode")
    put("mode_live", live, "ok" if live else "unknown",
        "mode_attest.session_selection", pending=bool(check.get("is_pending")))
    put("mode_rung", rung, "ok" if (committed or live) else "unknown",
        "mode_attest.attest")
    # The mode IN FORCE is the rung's answer. `mode_rung` beside it says WHICH
    # rung answered, so this single value is never mistaken for a merge of the
    # two.
    in_force = live if (check.get("is_pending") and live) else committed
    put("mode", in_force, "ok" if in_force else "unknown",
        "mode_attest.attest(rung=%s)" % rung)

    # The divergence, stored as its own dimension so it is REVIEWABLE.
    diverged = bool(check.get("is_pending") and live and committed
                    and live != committed)
    if diverged:
        put("mode_divergence",
            "live=%s committed=%s" % (live, committed), "fault",
            "mode_attest.live_fault_codes",
            fault="MODE_DIVERGED_UNCOMMITTED", pending=True)

    for dim, field in (("permission", "permission"), ("model", "model"),
                       ("reasoning_effort", "reasoning_effort")):
        v = check.get(field)
        put(dim, v, "ok" if v is not None else "unknown",
            "sendOptions")

    term = check.get("terminal_available")
    # `None` means the field was ABSENT, which is not the same as False. Kept
    # distinct because conflating them would report a missing field as a
    # disabled terminal.
    put("terminal_available",
        "true" if term is True else ("false" if term is False else None),
        "ok" if term is not None else "unknown", "sendOptions")

    return {"ok": True, "vscode_conversation_id": vscode_conversation_id,
            "written": written, "diverged": diverged,
            "duplicates": duplicates}


def record_preflight(vscode_conversation_id: int, *, scope: str = "all",
                     db_path: str | None = None) -> dict[str, Any]:
    """Store the DELEGATED preflight result. Never re-implements a check."""
    try:
        import f_env_preflight as pf
        report = pf.run_all(scope=scope)
    except Exception as exc:
        return append_env(vscode_conversation_id, "env_preflight", None,
                          status="unknown",
                          source="f_env_preflight.run_all",
                          fault_code="DELEGATE_FAILED:%s" % type(exc).__name__,
                          db_path=db_path)
    ready = bool(report.get("ready"))
    blocking = report.get("blocking") or []
    return append_env(
        vscode_conversation_id, "preflight",
        "%s/%s passed" % (report.get("passed"), report.get("total")),
        status="ok" if ready else "fault",
        source="f_env_preflight.run_all",
        fault_code="" if ready else "ENV_NOT_READY:" + ",".join(blocking),
        cite_ref="f_env_preflight.py:472",
        db_path=db_path,
    )
